#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-030.py

CAND-030 (agents-registry lifecycle, scope-down) baseline.
markSubagentRunTerminated (run-manager.ts:503-528) 의 dispose loop 가 clearPendingLifecycleError
만 호출하고 clearPendingLifecycleTimeout 누락 → pendingLifecycleTimeoutByRunId Map 의
marker 가 잔존 (cleanup==='keep' marker 는 5분간 유지 정책과 맞물려 memory hygiene 결함).
부수효과로 async ordering 따라 hook 두 번째 발사 가능성.

원리:
- worktree 의 dist/agents/subagent-registry.js (또는 동등 빌드 경로) 에서 __test export 사용
- 두 marker 모두 schedule
- markSubagentRunTerminated 호출
- pendingLifecycleTimeoutByRunId 와 pendingLifecycleErrorByRunId 의 Map.size 측정

REQUIRES_EXTERNAL_DEP=False.

필요 hook (instrumentation, fix 아님):
- src/agents/subagent-registry.ts 또는 run-manager.ts 에 __test export 추가:
  - __test.pendingLifecycleTimeoutByRunId (또는 size getter)
  - __test.pendingLifecycleErrorByRunId
  - __test.schedulePendingLifecycleTimeout(runId, ms)
  - __test.schedulePendingLifecycleError(runId, err)
  - __test.markSubagentRunTerminated(runId)  (controller wrapper)
- production 코드 변경 없이 export 만 추가 (test-only, worktree-local).

without-fix: markTerminated 후 pendingLifecycleTimeoutByRunId.size === 1 (marker 잔존).
with-fix:    markTerminated 후 pendingLifecycleTimeoutByRunId.size === 0 (대칭 clear).
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
SCENARIO_NAME = "proof-CAND-030"
DEFAULT_TRIALS = 1


def _build_probe_script() -> str:
    return """\
import { __test } from './dist/agents/subagent-registry.js';

if (!__test || typeof __test.schedulePendingLifecycleTimeout !== 'function'
    || !__test.pendingLifecycleTimeoutByRunId
    || typeof __test.markSubagentRunTerminated !== 'function') {
  console.log(JSON.stringify({ skipped: '__test hooks missing (need pendingLifecycleTimeoutByRunId / schedulePendingLifecycleTimeout / markSubagentRunTerminated)' }));
  process.exit(0);
}

const runId = 'proof-' + Date.now();
__test.schedulePendingLifecycleTimeout(runId, 15_000);
if (typeof __test.schedulePendingLifecycleError === 'function') {
  __test.schedulePendingLifecycleError(runId, new Error('probe-aborted'));
}

const timeoutBefore = __test.pendingLifecycleTimeoutByRunId.size;
const errorBefore = __test.pendingLifecycleErrorByRunId?.size ?? -1;

__test.markSubagentRunTerminated(runId);

const timeoutAfter = __test.pendingLifecycleTimeoutByRunId.size;
const errorAfter = __test.pendingLifecycleErrorByRunId?.size ?? -1;

console.log(JSON.stringify({ runId, timeoutBefore, timeoutAfter, errorBefore, errorAfter }));
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
    dist_target = wt_path / "dist" / "agents" / "subagent-registry.js"
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
            timeout=60,
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
    t_before = measurements.get("timeoutBefore", 0)
    t_after = measurements.get("timeoutAfter", -1)
    if t_before == 0:
        return "blocked-env"  # marker 가 schedule 안 됨 = hook 문제
    if t_after >= t_before:
        return "collected"  # marker 잔존 = 결함 재현
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_after = without_fix.get("timeoutAfter", 0)
    wo_before = without_fix.get("timeoutBefore", 0)
    wf_after = with_fix.get("timeoutAfter", 0)
    if wo_after >= wo_before and wf_after == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, markSubagentRunTerminated clears pendingLifecycleErrorByRunId but skips the "
        "symmetric pendingLifecycleTimeoutByRunId marker. The 15s grace timer therefore stays armed on a "
        "KILLED entry that the registry keeps for the 5-minute cleanup hold (`cleanup==='keep'`), allowing "
        "stale lifecycle hook fires. With this patch, both markers clear together — the contract matches "
        "finalizeInterruptedSubagentRun's own dispose path."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built from this branch. "
        "Isolated OPENCLAW_HOME. No external dependencies (in-process registry instance, no LLM calls). "
        "Same registry construction across both builds."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe schedules pendingLifecycleTimeout, then calls markSubagentRunTerminated, then reads Map.size)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of pendingLifecycleTimeoutByRunId Map after markTerminated:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  timeoutBefore: {without_fix.get('timeoutBefore')}  (marker scheduled)\n"
        f"  timeoutAfter:  {without_fix.get('timeoutAfter')}   (marker still present = leaked)\n"
        f"  errorAfter:    {without_fix.get('errorAfter')}     (error marker already cleared today)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  timeoutBefore: {with_fix.get('timeoutBefore')}\n"
        f"  timeoutAfter:  {with_fix.get('timeoutAfter')}      (symmetric clear)\n"
        "```"
    )
    observed = (
        f"With patch, pendingLifecycleTimeoutByRunId.size drops to {with_fix.get('timeoutAfter')} "
        f"after markTerminated (vs {without_fix.get('timeoutAfter')} without patch). Marker map "
        f"no longer accumulates stale entries during the 5-minute cleanup hold."
    )
    not_tested = (
        "lifecycle.ts:780-791 reset branch is by-design recovery (commit 2f86ae71d5 + steer-restart "
        "regression test) — outside this PR's scope. Real long-lived daemon memory bound at scale not measured."
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
