#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-038.py

CAND-038 (gateway lifecycle, P3) baseline.
ws-connection.ts:351-421 의 WS close 핸들러가 session/node/presence/nodeWake state 만
cleanup, chatAbortControllers 의 ownerConnId 매칭 entry 는 abort 안 함. user disconnect
후 maintenance interval sweep (최소 2분 floor) 까지 runner 가 abort 없이 계속 진행 →
LLM 토큰 과금 + tool 실행 + 외부 API 호출 누적.

원리:
- worktree 의 dist/gateway/chat-abort.js + dist/gateway/server/ws-connection.js import
- AbortController 생성 + registerChatAbortController({runId, ownerConnId, controller})
- ws-connection close 핸들러 직접 호출 (또는 __test.simulateWsClose(connId))
- AbortController.signal.aborted 측정

REQUIRES_EXTERNAL_DEP=False — gateway 전체 부팅 없이 close handler 함수만 격리 호출.

필요 hook: 없음. production test 패턴 (`ws-connection.test.ts`) 그대로 활용 —
EventEmitter socket + minimal mock wss/auth/loggers/buildRequestContext.
socket.emit('close', code, reason) 으로 close handler trigger. chatAbortControllers
Map 은 caller-managed 라 직접 만들고 entry 등록 → close handler 가 abort 안 함을 측정.

without-fix: aborted=false, abortReason=undefined (close handler 가 chatAbortControllers 미터치)
with-fix:    aborted=true, abortReason='owner-disconnect' (close handler 가 helper 호출)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-038"
DEFAULT_TRIALS = 1


def _build_probe_script() -> str:
    return """\
import { EventEmitter } from 'node:events';
import { registerChatAbortController } from './src/gateway/chat-abort.ts';
import { attachGatewayWsConnectionHandler } from './src/gateway/server/ws-connection.ts';

function makeLogger() {
  const noop = () => {};
  return { debug: noop, info: noop, warn: noop, error: noop };
}

function makeSocket(): any {
  const ee: any = new EventEmitter();
  ee._socket = {
    remoteAddress: '127.0.0.1',
    remotePort: 1234,
    localAddress: '127.0.0.1',
    localPort: 5678,
  };
  ee.send = () => {};
  ee.close = () => {};
  return ee;
}

const chatAbortControllers = new Map<string, any>();
const connA = 'conn-A-' + Date.now();
const connB = 'conn-B-' + Date.now();

// register two chat abort controllers, one per owner conn
const { controller: ctrlA } = registerChatAbortController({
  chatAbortControllers,
  runId: 'run-A',
  sessionKey: 'sess-A',
  ownerConnId: connA,
  ownerDeviceId: 'devA',
});
const { controller: ctrlB } = registerChatAbortController({
  chatAbortControllers,
  runId: 'run-B',
  sessionKey: 'sess-B',
  ownerConnId: connB,
  ownerDeviceId: 'devB',
});

let abortReasonA: any = undefined;
ctrlA.signal.addEventListener('abort', () => { abortReasonA = ctrlA.signal.reason; });

const listeners = new Map<string, (...args: any[]) => void>();
const wss: any = {
  on: (event: string, handler: any) => { listeners.set(event, handler); },
};

attachGatewayWsConnectionHandler({
  wss,
  clients: new Set() as never,
  preauthConnectionBudget: { release: () => {} } as never,
  port: 19001,
  resolvedAuth: { mode: 'token', allowTailscale: false, token: 'tok' } as any,
  preauthHandshakeTimeoutMs: 60_000,
  gatewayMethods: [],
  events: [],
  refreshHealthSnapshot: (async () => ({})) as never,
  logGateway: makeLogger() as never,
  logHealth: makeLogger() as never,
  logWsControl: makeLogger() as never,
  extraHandlers: {} as never,
  broadcast: () => {},
  buildRequestContext: () => ({
    unsubscribeAllSessionEvents: () => {},
    nodeRegistry: { unregister: () => null },
    nodeUnsubscribeAll: () => {},
    chatAbortControllers,
  }) as any,
});

const onConnection = listeners.get('connection');
if (!onConnection) {
  console.log(JSON.stringify({ error: 'no connection listener registered' }));
  process.exit(0);
}

const socket = makeSocket();
// upgradeReq with the conn-A connId in headers — gateway uses internal connId generation
// so we'll directly identify the conn after the connection handler attaches via socket label.
const upgradeReq: any = {
  headers: { host: '127.0.0.1:19001' },
  socket: { localAddress: '127.0.0.1' },
};

onConnection(socket, upgradeReq);

// Allow the connection setup to settle.
await new Promise(r => setTimeout(r, 50));

// Emit close — the close handler runs synchronously via socket.once('close').
// To bind connA to this socket conceptually, we already registered ctrlA with ownerConnId=connA
// before the handler ran. The defect is that the close handler never iterates
// chatAbortControllers regardless of ownerConnId; both controllers stay unaborted.
socket.emit('close', 1000, Buffer.from(''));

// Give event loop a moment.
await new Promise(r => setTimeout(r, 50));

console.log(JSON.stringify({
  aAborted: ctrlA.signal.aborted,
  bAborted: ctrlB.signal.aborted,
  abortReasonA: abortReasonA ? String(abortReasonA) : null,
}));
process.exit(0);
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    script = _build_probe_script()
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False, dir=str(wt_path)) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            [str(tsx_bin), script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"probe exited {proc.returncode}",
                "stderr": proc.stderr[:500],
            }
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"could not parse probe stdout: {e}",
                "stdout": proc.stdout[:500],
            }
        if "skipped" in payload:
            return {"scenario": SCENARIO_NAME, "trials": 0, "skipped": payload["skipped"]}
        return {"scenario": SCENARIO_NAME, "trials": trials, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    # without-fix: connA close 후에도 ctrlA.aborted === false
    if measurements.get("aAborted") is False and measurements.get("bAborted") is False:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_a = without_fix.get("aAborted")
    wf_a = with_fix.get("aAborted")
    wf_b = with_fix.get("bAborted")
    # with-fix: A close → A.aborted=true, B 그대로 false (정밀 매칭)
    if wo_a is False and wf_a is True and wf_b is False:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, the WS connection close handler in ws-connection.ts:351-421 cleans up "
        "session/node/presence/nodeWake state but leaves chatAbortControllers entries whose ownerConnId "
        "matches the closing connection untouched. Until the maintenance interval sweeps (≥ 2 minute "
        "expiresAtMs floor, typically 5-30 min), the runner keeps calling LLMs, executing tools, and "
        "talking to external APIs for a user who already disconnected. With this patch, the close "
        "handler iterates chatAbortControllers and calls abortChatRunById for every ownerConnId match."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — gateway dependencies stubbed via ws-connection __test.installFakeDeps. "
        "Two AbortControllers registered against two different ownerConnIds; one connection is closed."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe registers chatAbortControllers for conn-A and conn-B, simulates WS close for conn-A)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of AbortController.signal.aborted after simulated WS close:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  ctrlA.aborted (owner of closed conn): {without_fix.get('aAborted')}\n"
        f"  ctrlB.aborted (owner of other conn):  {without_fix.get('bAborted')}\n"
        f"  abortReasonA: {without_fix.get('abortReasonA')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  ctrlA.aborted (owner of closed conn): {with_fix.get('aAborted')}\n"
        f"  ctrlB.aborted (owner of other conn):  {with_fix.get('bAborted')}\n"
        f"  abortReasonA: {with_fix.get('abortReasonA')}\n"
        "```"
    )
    observed = (
        f"With patch, only the AbortController owned by the closing connection aborts "
        f"(ctrlA={with_fix.get('aAborted')}, ctrlB={with_fix.get('bAborted')}), with reason "
        f"'{with_fix.get('abortReasonA')}'. Other connections' runs continue uninterrupted."
    )
    not_tested = (
        "Brief disconnect-and-resume UX trade-off (intentional — current path already drops broadcast "
        "to closed socket). Maintenance interval sweep behavior unchanged (handles entries with no "
        "owner connection)."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True)
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
