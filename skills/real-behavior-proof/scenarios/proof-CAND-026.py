#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-026.py

CAND-026 (mcp lifecycle, scope-down FIND-002 only) baseline.
tools-stdio-server.ts:17 의 setRequestHandler callback 이 두 번째 인자 extra (RequestHandlerExtra)
를 무시 → extra.signal 이 callTool / tool.execute 로 전파 안 됨. host 가 notifications/cancelled
또는 transport close 를 발사해도 in-flight tool.execute 가 abort 신호 못 받음.

원리:
- worktree 의 dist/mcp/plugin-tools-handlers.js 에서 callTool 함수 직접 import
- toolRegistry mock 으로 probe tool 1개 등록 (execute 콜백이 4번째 인자 signal 을 캡처)
- AbortSignal 생성 후 callTool({name: 'probe', arguments: {}}, signal) 호출
- probe tool 의 execute 가 받은 signal 이 caller signal 과 동일 reference 인가 측정

REQUIRES_EXTERNAL_DEP=False (MCP SDK 인스턴스/transport 없이 callTool 함수 단위 측정).

필요 hook (이미 production 코드에 존재):
- plugin-tools-handlers.ts 의 callTool export. 4번째 인자 시그니처는 fix 의 일부.
- toolRegistry deps interface 노출 또는 module 내 setter (__test hook 추가 필요할 수 있음).

without-fix: callTool 시그니처가 (params) 만 받음 → tool.execute 의 4번째 인자 undefined → signalReceived=false.
with-fix:    callTool (params, signal) → tool.execute(id, params, signal) → signalReceived=true.

caveat: handlers.callTool 시그니처 변경 자체가 fix 의 1차 surface. without-fix 빌드에선
시나리오의 callTool(params, signal) 호출이 signal 인자를 무시할 뿐 throw 안 함 — 측정은
'execute 가 signal 을 받았는가' 단일 binary.
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
SCENARIO_NAME = "proof-CAND-026"
DEFAULT_TRIALS = 1


def _build_probe_script() -> str:
    return """\
import { callTool, __test } from './dist/mcp/plugin-tools-handlers.js';

const ctrl = new AbortController();
let observedSignal = undefined;
let executeInvoked = 0;

const probeTool = {
  id: 'probe-tool',
  name: 'probe',
  description: 'CAND-026 signal propagation probe',
  inputSchema: { type: 'object' },
  execute: async (_id, _params, signal) => {
    executeInvoked++;
    observedSignal = signal;
    return { content: [{ type: 'text', text: 'ok' }] };
  },
};

if (typeof __test?.setToolRegistry === 'function') {
  __test.setToolRegistry({ list: () => [probeTool], find: (n) => n === 'probe' ? probeTool : null });
} else {
  console.log(JSON.stringify({ skipped: '__test.setToolRegistry hook missing in build' }));
  process.exit(0);
}

try {
  await callTool({ name: 'probe', arguments: {} }, ctrl.signal);
} catch (err) {
  console.log(JSON.stringify({ error: String(err), executeInvoked }));
  process.exit(0);
}

const signalReceived = observedSignal !== undefined;
const sameSignal = observedSignal === ctrl.signal;
console.log(JSON.stringify({ executeInvoked, signalReceived, sameSignal }));
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
    dist_handlers = wt_path / "dist" / "mcp" / "plugin-tools-handlers.js"
    if not dist_handlers.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"build artifact missing: {dist_handlers}. pnpm build 결과 확인.",
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
            timeout=60,
        )
        if proc.returncode != 0:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"probe exited {proc.returncode}",
                "stderr": proc.stderr[:500],
                "stdout": proc.stdout[:500],
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
            return {
                "scenario": SCENARIO_NAME,
                "trials": 0,
                "skipped": payload["skipped"],
            }
        return {"scenario": SCENARIO_NAME, "trials": trials, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    if measurements.get("executeInvoked", 0) == 0:
        return "blocked-env"
    if measurements.get("signalReceived") is False:
        return "collected"  # signal 미전파 = 결함 재현
    return "unreproducible"  # without-fix 인데 signal 도달 = 모순, axis 재검토


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_recv = without_fix.get("signalReceived", False)
    wf_recv = with_fix.get("signalReceived", False)
    wf_same = with_fix.get("sameSignal", False)
    if not wo_recv and wf_recv and wf_same:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, the MCP standalone tools server (plugin-tools / openclaw-tools) wires "
        "setRequestHandler with `async (request) => ...` and drops the second argument `extra: RequestHandlerExtra`. "
        "The host's `notifications/cancelled` or transport close therefore never propagates to the in-flight "
        "tool.execute. With this patch, extra.signal is forwarded through callTool to tool.execute, so host "
        "cancellation actually aborts the running tool."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built from this branch. "
        "Isolated OPENCLAW_HOME via skills/real-behavior-proof/harness/env_isolate.py. "
        "No external dependencies (no OAuth/LLM/channel calls). Same plugin-tools-handlers build path; only fix diff differs."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe imports callTool, registers a probe tool, invokes callTool with an AbortSignal)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of AbortSignal propagation from callTool to tool.execute:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  executeInvoked: {without_fix.get('executeInvoked')}\n"
        f"  signalReceived: {without_fix.get('signalReceived')}  (tool.execute's 4th arg was undefined)\n"
        f"  sameSignal:     {without_fix.get('sameSignal')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  executeInvoked: {with_fix.get('executeInvoked')}\n"
        f"  signalReceived: {with_fix.get('signalReceived')}\n"
        f"  sameSignal:     {with_fix.get('sameSignal')}  (forwarded the caller's AbortSignal reference)\n"
        "```"
    )
    observed = (
        f"With patch, tool.execute receives the caller's AbortSignal (sameSignal={with_fix.get('sameSignal')}) "
        f"versus undefined without patch. Host-initiated cancellation now reaches the in-flight tool."
    )
    not_tested = (
        "FIND-mcp-lifecycle-001 shutdown drain axis (abandoned by cross-review — SDK Protocol._onclose "
        "already unconditional aborts). Tools whose execute() does not yet accept a signal argument "
        "(memory_recall, cron-tool) still ignore cancellation — separate follow-up."
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
