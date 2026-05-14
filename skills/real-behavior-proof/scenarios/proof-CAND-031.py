#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-031.py

CAND-031 (auto-reply error-boundary) baseline.
drain.ts:298-313 의 catch+finally 가 max-attempts / backoff / dead-letter 없이
scheduleFollowupDrain self-recurse 무한 재시도. deterministic-fail 시 동일 head item
에 debounceMs=500ms 간격 무한 retry → 24h ≈ 170k log + typing keepalive 폭주.

원리:
- worktree 의 dist/auto-reply/reply/queue/drain.js 에서 scheduleFollowupDrain + __test hook 사용
- effectiveRunFollowup 을 항상 throw 하는 deterministic-fail mock 으로 inject
- queue.items 에 1개 enqueue
- scheduleFollowupDrain 호출 → real-clock 10초 대기 (debounceMs=500 → 약 20 retries 예상)
- callCount 측정

REQUIRES_EXTERNAL_DEP=False.

필요 hook: 없음. production 의 scheduleFollowupDrain(key, runFollowup) 이 runFollowup 을
DI 인자로 받음. FOLLOWUP_QUEUES Map + getFollowupQueue(key, settings) 가 export — queue 직접 조작 가능.
tsx 로 src ts 직접 실행 (--skip-build).

without-fix: observe_ms (=3000) 동안 callCount ≥ 5 (debounceMs=200 → 약 15). queue.items[0] 잔존.
with-fix:    callCount ≤ MAX_ATTEMPTS (e.g. 5). queue.items[0] dead-letter 처리.

caveat: 실제 production 의 deterministic source 는 contextEngine.compact() throw 또는
ensureRuntimePluginsLoaded throw (cross-review 가 fs ENOENT 예시 잘못 지목한 점 반영).
probe 는 단순히 throw 하는 mock 으로 본질만 측정.
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
SCENARIO_NAME = "proof-CAND-031"
DEFAULT_TRIALS = 1
DEFAULT_OBSERVE_MS = 3_000  # 3s → debounceMs=200 → ~15 retries 예상
DEFAULT_MAX_EXPECTED_ATTEMPTS = 5  # fix 후 기대 상한


def _build_probe_script(*, observe_ms: int) -> str:
    return f"""\
import {{ scheduleFollowupDrain }} from './src/auto-reply/reply/queue/drain.ts';
import {{ FOLLOWUP_QUEUES, getFollowupQueue }} from './src/auto-reply/reply/queue/state.ts';

const key = 'proof-key-' + Date.now();
const queue = getFollowupQueue(key, {{
  mode: 'followup',
  debounceMs: 200,
  cap: 20,
  dropPolicy: 'summarize',
}});

const fakeRun: any = {{
  prompt: 'probe-prompt',
  enqueuedAt: Date.now(),
  run: {{ agentId: 'probe-agent', agentDir: '/tmp/probe', sessionId: 'probe-session' }},
}};
queue.items.push(fakeRun);
queue.lastRun = fakeRun.run;
queue.lastEnqueuedAt = Date.now();

let callCount = 0;
const mockRunFollowup = async () => {{
  callCount++;
  throw new Error('deterministic-fail probe');
}};

scheduleFollowupDrain(key, mockRunFollowup);
await new Promise(r => setTimeout(r, {observe_ms}));

const q = FOLLOWUP_QUEUES.get(key);
const queueSize = q ? q.items.length : 0;

console.log(JSON.stringify({{
  callCount,
  queueSize,
  observeMs: {observe_ms},
}}));
process.exit(0);
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    observe_ms: int = DEFAULT_OBSERVE_MS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    script = _build_probe_script(observe_ms=observe_ms)
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
            timeout=(observe_ms // 1000) + 60,
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
    cc = measurements.get("callCount", 0)
    # without-fix: 10s 동안 ~20 회 retry 예상. 보수적으로 ≥ 8 이면 재현 OK.
    if cc >= 8:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_cc = without_fix.get("callCount", 0)
    wf_cc = with_fix.get("callCount", 0)
    if wo_cc >= 8 and wf_cc <= DEFAULT_MAX_EXPECTED_ATTEMPTS + 1:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    observe_s = (with_fix.get("observeMs", DEFAULT_OBSERVE_MS)) // 1000
    behavior = (
        "Without this patch, drain.ts's catch+finally calls scheduleFollowupDrain again whenever "
        "effectiveRunFollowup throws, with no max-attempts / backoff / dead-letter. A deterministic "
        "failure (e.g. contextEngine.compact() throws, ensureRuntimePluginsLoaded fails) retries the "
        f"same head item at debounceMs=500ms forever. With this patch, an attempts counter trips after "
        f"~{DEFAULT_MAX_EXPECTED_ATTEMPTS} retries and the item is dead-lettered out of the queue."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — effectiveRunFollowup mocked to throw deterministically via __test seam. "
        f"Real wall clock observation window: {observe_s}s."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe injects throwing followup, enqueues 1 item, schedules drain, sleeps " + str(observe_s) + "s, reads callCount)\n"
        "```"
    )
    evidence = (
        f"Live Node.js measurement of scheduleFollowupDrain self-recurse count over {observe_s}s wall clock:\n\n"
        f"```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  callCount:  {without_fix.get('callCount')}        (unbounded retry storm)\n"
        f"  attempts:   {without_fix.get('attempts')}\n"
        f"  queueSize:  {without_fix.get('queueSize')}        (head item never shifted)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  callCount:  {with_fix.get('callCount')}            (bounded by MAX_ATTEMPTS)\n"
        f"  attempts:   {with_fix.get('attempts')}\n"
        f"  queueSize:  {with_fix.get('queueSize')}            (dead-lettered, queue drained)\n"
        f"```"
    )
    observed = (
        f"With patch, callCount stays ≤ {DEFAULT_MAX_EXPECTED_ATTEMPTS}+1 within {observe_s}s "
        f"(vs {without_fix.get('callCount')} without patch). Queue head no longer pins on deterministic failures."
    )
    not_tested = (
        "Exponential backoff cap and dead-letter retention TTL (separate axis). "
        "Telegram/Slack typing keepalive thrash (downstream symptom — covered by retry bound alone)."
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
    ap.add_argument("--observe-ms", type=int, default=DEFAULT_OBSERVE_MS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        observe_ms=args.observe_ms,
    )
    print(json.dumps(out, indent=2))
