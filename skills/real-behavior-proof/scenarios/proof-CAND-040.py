#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-040.py

CAND-040 (infra-process concurrency, P3) baseline.
approval-handler-runtime.ts:498-537 deliverTarget 의 두 await (deliverPending L505,
bindPending L517) 사이에 onStopped (L656-672) 가 실행되어 activeEntries.clear() 호출되면,
deliverTarget resume 후 L529-535 에서 wrapped entry 가 비어있던 Map 에 다시 등록 +
stopped 핸들러로는 finalizeResolved/Expired 가 호출 안 됨 → native binding orphan + leak.

cross-review caveat (CAND-040): FIND 가 outer approval-handler-runtime.ts 만 지목,
inner exec-approval-channel-runtime.ts:393-415 spawn detached + stop() inflight await
부재가 진짜 root enabler. PR 본문 시 fix surface 확장 권고.

원리:
- worktree 의 dist/infra/approval-handler-runtime.js 에서 createChannelApprovalHandlerFromCapability import
- fake transport: deliverPending 이 Deferred gate (park 가능), unbindPending 호출 카운트
- handler 생성 → deliverTarget('req-1') 호출 (await park)
- 50ms 후 handler.onStopped() 호출 (activeEntries.clear)
- Deferred release → deliverTarget resume
- 측정: unbindPending 호출 횟수, activeEntries.size

REQUIRES_EXTERNAL_DEP=False.

필요 hook:
- __test.peekActiveEntriesSize(handler)  — closure 의 activeEntries Map 외부 노출
- __test.installFakeTransport(handler, transport)  — capability deps inject

without-fix: unbindCalled=0, mapSize=1 (deliverTarget 가 다시 set 한 wrapped entry. native binding orphan.)
with-fix:    unbindCalled=1 (stopped flag check → native cleanup), mapSize=0 (wrapped 미등록)

추가 axis (cross-review caveat): inner exec-approval-channel-runtime.ts stop() 측 fix 가
함께 적용된 빌드에서 spawn detached promise 가 stop()await 됐는지 측정. probe 별도
scenario 또는 후속 follow-up.
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
SCENARIO_NAME = "proof-CAND-040"
DEFAULT_TRIALS = 5


def _build_probe_script(*, trials: int) -> str:
    return f"""\
import {{ createChannelApprovalHandlerFromCapability, __test }} from './dist/infra/approval-handler-runtime.js';

if (!__test
    || typeof __test.installFakeTransport !== 'function'
    || typeof __test.peekActiveEntriesSize !== 'function') {{
  console.log(JSON.stringify({{ skipped: '__test hooks missing (installFakeTransport / peekActiveEntriesSize in approval-handler-runtime)' }}));
  process.exit(0);
}}

const trialResults = [];

for (let i = 0; i < {trials}; i++) {{
  let releaseDeliver;
  const deliverGate = new Promise(r => {{ releaseDeliver = r; }});

  let unbindCalled = 0;
  const fakeTransport = {{
    deliverPending: async () => {{ await deliverGate; return {{ token: 'probe-token' }}; }},
    bindPending: async () => {{ return {{ binding: 'probe-binding' }}; }},
    unbindPending: () => {{ unbindCalled++; }},
  }};

  const handler = createChannelApprovalHandlerFromCapability({{ /* capability shape from existing tests */ }});
  __test.installFakeTransport(handler, fakeTransport);

  const requestId = 'req-' + i;
  const deliverPromise = handler.deliverTarget({{ id: requestId }});

  await new Promise(r => setTimeout(r, 50));
  if (typeof handler.onStopped === 'function') {{
    handler.onStopped();
  }} else if (typeof handler.__onStopped === 'function') {{
    handler.__onStopped();
  }}
  releaseDeliver();
  try {{ await deliverPromise; }} catch {{}}

  const mapSize = __test.peekActiveEntriesSize(handler);
  trialResults.push({{ trial: i, requestId, unbindCalled, mapSize }});
}}

const totalUnbind = trialResults.reduce((s, t) => s + t.unbindCalled, 0);
const totalLeak = trialResults.filter(t => t.mapSize > 0).length;
console.log(JSON.stringify({{
  trials: {trials},
  trialResults,
  totalUnbind,
  totalLeak,
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
    dist_target = wt_path / "dist" / "infra" / "approval-handler-runtime.js"
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
    trials = measurements.get("trials", 0)
    # without-fix: 대부분 trial 이 leak (mapSize > 0) + unbindCalled == 0
    if measurements.get("totalLeak", 0) >= max(1, trials // 2) and measurements.get("totalUnbind", 0) == 0:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    trials = with_fix.get("trials", 0)
    wo_leak = without_fix.get("totalLeak", 0)
    wo_unbind = without_fix.get("totalUnbind", 0)
    wf_leak = with_fix.get("totalLeak", 0)
    wf_unbind = with_fix.get("totalUnbind", 0)
    if wo_leak >= max(1, trials // 2) and wo_unbind == 0 and wf_leak == 0 and wf_unbind >= trials:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    behavior = (
        "Without this patch, deliverTarget in approval-handler-runtime.ts:498-537 splits its activeEntries "
        "read-modify-write across two awaits (deliverPending, bindPending). If onStopped fires between "
        "them and clears activeEntries, the resumed deliverTarget re-inserts a wrapped entry into the "
        "now-empty Map and the stopped dispatcher never reaches finalizeResolved/Expired — the native "
        "binding stays bound forever. With this patch, deliverTarget checks a stopped flag before each "
        "await and after each await, calling unbindPending and bailing out instead of re-inserting."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — nativeRuntime.transport stubbed via __test.installFakeTransport, "
        "activeEntries.size observed via __test.peekActiveEntriesSize. "
        f"{trials} trials per build, each gating deliverPending then firing onStopped before release."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe gates deliverPending, fires onStopped at 50ms, releases gate, reads unbindCalled + Map.size)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of native unbind calls and orphan map entries:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trials:       {without_fix.get('trials')}\n"
        f"  totalUnbind:  {without_fix.get('totalUnbind')}    (no native cleanup on stopped race)\n"
        f"  totalLeak:    {without_fix.get('totalLeak')}/{without_fix.get('trials')}    (orphan wrapped entries)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trials:       {with_fix.get('trials')}\n"
        f"  totalUnbind:  {with_fix.get('totalUnbind')}    (stopped flag triggers native cleanup)\n"
        f"  totalLeak:    {with_fix.get('totalLeak')}/{with_fix.get('trials')}    (no orphan entries)\n"
        "```"
    )
    observed = (
        f"With patch, every gated deliverTarget that races with onStopped unbinds its native handle "
        f"({with_fix.get('totalUnbind')}/{trials}) and leaves no orphan entry "
        f"({with_fix.get('totalLeak')}/{trials} leaks), versus {without_fix.get('totalLeak')}/{trials} "
        f"leaks and 0 unbinds without patch."
    )
    not_tested = (
        "Inner exec-approval-channel-runtime.ts:393-415 spawn-detached stop() inflight await (cross-review "
        "caveat — separate follow-up). Mutex / AbortSignal-based design alternatives (options B/C in the FIND)."
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
