#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-033.py

CAND-033 (auto-reply lifecycle, P2) baseline.
drain.ts:239-264 collect mode inner for loop 가 authGroups snapshot 후 매 iteration
의 await 사이 clearSessionQueues 가 발생해도 본문이 면역 → 사용자 abort 후에도
pre-captured group 이 모델로 전달됨.

원리:
- worktree 의 dist/auto-reply/reply/queue/drain.js 에서 collect-mode drain entrypoint import
- queue.items = [A(auth=X), B(auth=X), C(auth=Y)] → authGroups=[[A,B],[C]]
- effectiveRunFollowup 을 Deferred gate 로 wrap: 첫 호출 (X 그룹) 에서 park
- gate park 중 clearSessionQueues 트리거
- gate release
- 두 번째 호출 (Y 그룹) 이 fire 됐는지 측정 (callList)

REQUIRES_EXTERNAL_DEP=False.

필요 hook (instrumentation, fix 자체에 비포함):
- __test.setEffectiveRunFollowup(fn)
- __test.enqueueItems(key, items)
- __test.triggerCollectDrain(key)        — collect mode 분기 직접 진입
- __test.clearSessionQueues(key)
- __test.peekFollowupQueuesIdentity(key) — fix 측 identity guard 검증용

without-fix: callList = ['X', 'Y'] (inner for 가 면역, Y 가 model 호출됨)
with-fix:    callList = ['X']      (identity guard 추가 → Y iteration 시 break)

caveat: 빠른 inner-loop race 라 timing 민감. probe 안에서 Deferred 패턴 명시.
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
SCENARIO_NAME = "proof-CAND-033"
DEFAULT_TRIALS = 3


def _build_probe_script(*, trials: int) -> str:
    return f"""\
import {{ __test }} from './dist/auto-reply/reply/queue/drain.js';

if (!__test
    || typeof __test.setEffectiveRunFollowup !== 'function'
    || typeof __test.enqueueItems !== 'function'
    || typeof __test.triggerCollectDrain !== 'function'
    || typeof __test.clearSessionQueues !== 'function') {{
  console.log(JSON.stringify({{ skipped: '__test hooks missing (setEffectiveRunFollowup / enqueueItems / triggerCollectDrain / clearSessionQueues)' }}));
  process.exit(0);
}}

const trialResults = [];

for (let i = 0; i < {trials}; i++) {{
  const callList = [];
  let releaseFirst;
  const firstPark = new Promise(r => {{ releaseFirst = r; }});

  __test.setEffectiveRunFollowup(async (_key, items) => {{
    const authKey = items[0]?.authKey ?? '(none)';
    callList.push(authKey);
    if (callList.length === 1) await firstPark;
  }});

  const key = 'proof-key-' + i;
  __test.enqueueItems(key, [
    {{ id: 'A-' + i, authKey: 'X' }},
    {{ id: 'B-' + i, authKey: 'X' }},
    {{ id: 'C-' + i, authKey: 'Y' }},
  ]);

  const drainPromise = __test.triggerCollectDrain(key);
  await new Promise(r => setTimeout(r, 80));  // let first followup enter the await

  __test.clearSessionQueues(key);
  releaseFirst();
  try {{ await drainPromise; }} catch {{}}

  trialResults.push({{ trial: i, callList }});
}}

const yLeakCount = trialResults.filter(r => r.callList.includes('Y')).length;
console.log(JSON.stringify({{
  trials: {trials},
  trialResults,
  yLeakCount,
}}));
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
    dist_target = wt_path / "dist" / "auto-reply" / "reply" / "queue" / "drain.js"
    if not dist_target.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"build artifact missing: {dist_target}.",
        }

    script = _build_probe_script(trials=trials)
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
    leaks = measurements.get("yLeakCount", 0)
    trials = measurements.get("trials", 0)
    if leaks >= max(1, trials // 2):  # 최소 절반 trial 이 leak 보이면 재현
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_leak = without_fix.get("yLeakCount", 0)
    wf_leak = with_fix.get("yLeakCount", 0)
    if wo_leak >= max(1, without_fix.get("trials", 0) // 2) and wf_leak == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    behavior = (
        "Without this patch, drain.ts's collect-mode branch snapshots `authGroups` at iteration start "
        "and the inner for-loop becomes immune to mid-await clearSessionQueues. If `/stop` arrives "
        "between the first group's await and the second group's iteration, the second authGroup is "
        "still forwarded to effectiveRunFollowup — the model executes follow-up tools for an aborted "
        "session. With this patch, the inner loop re-checks `FOLLOWUP_QUEUES.get(key) !== queue` per "
        "iteration (extending CAND-012's outer identity guard inwards)."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — effectiveRunFollowup mocked via __test seam, clearSessionQueues "
        "invoked from probe to simulate /stop arrival between authGroup iterations. "
        f"Trials per build: {trials}."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe enqueues 3 items in 2 authGroups, parks the first followup, clears queues, releases gate)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of mid-await /stop leak count:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trials:      {without_fix.get('trials')}\n"
        f"  yLeakCount:  {without_fix.get('yLeakCount')}     (Y authGroup invoked after clearSessionQueues)\n"
        f"  sample callList: {json.dumps((without_fix.get('trialResults') or [{}])[0].get('callList'))}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trials:      {with_fix.get('trials')}\n"
        f"  yLeakCount:  {with_fix.get('yLeakCount')}        (inner identity guard breaks after clearSessionQueues)\n"
        f"  sample callList: {json.dumps((with_fix.get('trialResults') or [{}])[0].get('callList'))}\n"
        "```"
    )
    observed = (
        f"With patch, 0/{trials} trials leak the second authGroup to effectiveRunFollowup after "
        f"clearSessionQueues (vs {without_fix.get('yLeakCount')}/{trials} without patch). User /stop "
        f"now stops in-flight collect drains within one inner-loop iteration boundary."
    )
    not_tested = (
        "AbortController propagation through clearSessionQueues (option B in the FIND, deferred follow-up). "
        "Cross-channel /stop ordering when N≥3 authGroups (covered by identity check, but not stressed)."
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
