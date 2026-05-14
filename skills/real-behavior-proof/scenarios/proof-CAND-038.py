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

필요 hook:
- __test.simulateWsClose(connId)  — gateway server.impl 또는 ws-connection 모듈
  → 실제 ws.close event 흉내 (cleanup pipeline 진입)
- 또는 close handler 를 export 해서 직접 호출 가능하게.
- 일부 dependency (sessionEvents/nodeRegistry mock) 필요 — __test.installFakeDeps()
  같은 utility 가 함께 있어야 함.

without-fix: aborted=false, abortReason=undefined (close handler 가 chatAbortControllers 미터치)
with-fix:    aborted=true, abortReason="owner-disconnect"

multi-controller 변종:
- 두 controller 등록 (ownerConnId: A, ownerConnId: B)
- A WS close → ctrl-A only aborted, ctrl-B 그대로 = 'positive matching' 검증.
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
import { registerChatAbortController } from './dist/gateway/chat-abort.js';
import { __test } from './dist/gateway/server/ws-connection.js';

if (!__test || typeof __test.simulateWsClose !== 'function' || typeof __test.installFakeDeps !== 'function') {
  console.log(JSON.stringify({ skipped: '__test hooks missing (simulateWsClose / installFakeDeps in ws-connection)' }));
  process.exit(0);
}

__test.installFakeDeps();

const connA = 'conn-A-' + Date.now();
const connB = 'conn-B-' + Date.now();

const ctrlA = new AbortController();
const ctrlB = new AbortController();
let abortReasonA = undefined;
ctrlA.signal.addEventListener('abort', () => { abortReasonA = ctrlA.signal.reason; });

registerChatAbortController({ runId: 'run-A', ownerConnId: connA, controller: ctrlA });
registerChatAbortController({ runId: 'run-B', ownerConnId: connB, controller: ctrlB });

await __test.simulateWsClose(connA);

console.log(JSON.stringify({
  aAborted: ctrlA.signal.aborted,
  bAborted: ctrlB.signal.aborted,
  abortReasonA: abortReasonA ? String(abortReasonA) : null,
}));
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
    dist_target = wt_path / "dist" / "gateway" / "server" / "ws-connection.js"
    if not dist_target.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"build artifact missing: {dist_target}.",
        }

    script = _build_probe_script()
    with tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", delete=False, dir=str(wt_path)) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            ["node", script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
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
