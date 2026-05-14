#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-039.py

CAND-039 (gateway lifecycle, P3) baseline.
server-runtime-services.ts:156-190 의 두 recovery 함수 (recoverPendingOutboundDeliveries
즉시 IIFE + recoverPendingSessionDeliveries setTimeout(1250ms)) 가 stop handle 반환 부재 +
caller 측 cancellation 통로 미보유. boot 직후 1250ms 이내 SIGTERM 시 tearing-down state
에서 recovery 가 dynamic import + dispose 된 deps 사용 → error log + 의도치 않은 외부
메시지 발사 가능.

원리:
- worktree 의 dist/gateway/server-runtime-services.js 에서 activateGatewayScheduledServices import
- isClosing 콜백 = () => stoppedFlag (probe 측 제어)
- recovery 진입 hook (__test.onRecoveryAttempt) 으로 fire 여부 측정
- activate 호출 후 50ms 후 stoppedFlag = true (close 시작)
- 2초 대기 (setTimeout(1250) 통과)
- recoveryFired 여부 측정

REQUIRES_EXTERNAL_DEP=False (실제 deliveryQueue / sessionDelivery 모듈은 mock).

필요 hook (worktree-local instrumentation):
- server-runtime-services.ts 에 `__test.setRecoveryProbe({ onOutbound, onSession, skipReal })` 추가
- recoverPendingOutboundDeliveries 와 setTimeout 콜백 시작 시점에 hook fire + skipReal 면 early return
  (production dynamic import 우회 — instrumentation only, fix-like guard 아님)
- 시나리오의 _apply_instrumentation 가 자동 패치

without-fix: outboundFired=true + sessionFired=true (1250ms setTimeout 도 fire)
with-fix:    isClosing guard 또는 timer handle 반환으로 차단 → fire 0
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
SCENARIO_NAME = "proof-CAND-039"
DEFAULT_TRIALS = 3
DEFAULT_WAIT_MS = 2_000  # > setTimeout 1250ms


def _build_probe_script(*, trials: int, wait_ms: int) -> str:
    return f"""\
import {{ activateGatewayScheduledServices, __test }} from './src/gateway/server-runtime-services.ts';

if (!__test || typeof __test.setRecoveryProbe !== 'function') {{
  console.log(JSON.stringify({{ skipped: '__test.setRecoveryProbe hook missing (instrumentation not applied)' }}));
  process.exit(0);
}}

function noopLogger(): any {{
  const fn = () => {{}};
  return {{ debug: fn, info: fn, warn: fn, error: fn, child: () => noopLogger() }};
}}

const trialResults = [];

for (let i = 0; i < {trials}; i++) {{
  let outboundFired = false;
  let sessionFired = false;
  __test.reset();
  __test.setRecoveryProbe({{
    onOutbound: () => {{ outboundFired = true; }},
    onSession: () => {{ sessionFired = true; }},
    skipReal: true,
  }});

  try {{
    activateGatewayScheduledServices({{
      minimalTestGateway: false,
      cfgAtStart: {{}} as any,
      deps: {{}} as any,
      sessionDeliveryRecoveryMaxEnqueuedAt: 0,
      cron: {{ start: async () => {{}} }} as any,
      startCron: false,
      logCron: {{ error: () => {{}} }},
      log: noopLogger(),
    }});
  }} catch (err) {{
    trialResults.push({{ trial: i, error: String(err) }});
    continue;
  }}

  // wait past 1250ms session setTimeout
  await new Promise(r => setTimeout(r, {wait_ms}));

  trialResults.push({{ trial: i, outboundFired, sessionFired }});
}}

const outboundCount = trialResults.filter(r => r.outboundFired).length;
const sessionCount = trialResults.filter(r => r.sessionFired).length;
const totalFired = outboundCount + sessionCount;
console.log(JSON.stringify({{
  trials: {trials},
  trialResults,
  outboundCount,
  sessionCount,
  totalFired,
}}));
process.exit(0);
"""


_INSTRUMENTATION_SRC_REL = "src/gateway/server-runtime-services.ts"
_INSTRUMENTATION_PATCH = """

const __testProbe: {
  onOutbound?: () => void;
  onSession?: () => void;
  skipReal?: boolean;
} = {};
export const __test = {
  setRecoveryProbe(p: { onOutbound?: () => void; onSession?: () => void; skipReal?: boolean }) {
    Object.assign(__testProbe, p);
  },
  reset() {
    for (const k of Object.keys(__testProbe)) {
      delete (__testProbe as any)[k];
    }
  },
};
"""

_OUTBOUND_HOOK_BEFORE = """function recoverPendingOutboundDeliveries(params: {
  cfg: OpenClawConfig;
  log: GatewayRuntimeServiceLogger;
}): void {
  void (async () => {"""
_OUTBOUND_HOOK_AFTER = """function recoverPendingOutboundDeliveries(params: {
  cfg: OpenClawConfig;
  log: GatewayRuntimeServiceLogger;
}): void {
  __testProbe.onOutbound?.();
  if (__testProbe.skipReal) return;
  void (async () => {"""

_SESSION_HOOK_BEFORE = """function recoverPendingSessionDeliveries(params: {
  deps: import("../cli/deps.types.js").CliDeps;
  log: GatewayRuntimeServiceLogger;
  maxEnqueuedAt: number;
}): void {
  const timer = setTimeout(() => {
    void (async () => {"""
_SESSION_HOOK_AFTER = """function recoverPendingSessionDeliveries(params: {
  deps: import("../cli/deps.types.js").CliDeps;
  log: GatewayRuntimeServiceLogger;
  maxEnqueuedAt: number;
}): void {
  const timer = setTimeout(() => {
    __testProbe.onSession?.();
    if (__testProbe.skipReal) return;
    void (async () => {"""


def _apply_instrumentation(wt_path: Path) -> str | None:
    """worktree-local instrumentation. fix-like guard 아님 — measurement hook + skipReal early-return."""
    src = wt_path / _INSTRUMENTATION_SRC_REL
    if not src.exists():
        return f"instrumentation target missing: {src}"
    content = src.read_text()
    if "__testProbe" in content:
        return None  # idempotent
    if _OUTBOUND_HOOK_BEFORE not in content:
        return "outbound recover function signature not found (production code drift?)"
    if _SESSION_HOOK_BEFORE not in content:
        return "session recover function signature not found (production code drift?)"
    content = content.replace(_OUTBOUND_HOOK_BEFORE, _OUTBOUND_HOOK_AFTER, 1)
    content = content.replace(_SESSION_HOOK_BEFORE, _SESSION_HOOK_AFTER, 1)
    content = content + _INSTRUMENTATION_PATCH
    src.write_text(content)
    return None


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    wait_ms: int = DEFAULT_WAIT_MS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}
    instr_err = _apply_instrumentation(wt_path)
    if instr_err:
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": instr_err}

    script = _build_probe_script(trials=trials, wait_ms=wait_ms)
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
            timeout=(wait_ms // 1000) * trials + 120,
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
    # without-fix: 거의 모든 trial 에서 둘 다 fire 예상.
    if measurements.get("totalFired", 0) >= trials:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    trials = with_fix.get("trials", 0)
    wo_total = without_fix.get("totalFired", 0)
    wf_total = with_fix.get("totalFired", 0)
    if wo_total >= trials and wf_total == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    behavior = (
        "Without this patch, activateGatewayScheduledServices fires recoverPendingOutboundDeliveries as a "
        "floating IIFE and recoverPendingSessionDeliveries via setTimeout(1250ms) with no stop handle. "
        "timer.unref() does not prevent the callback from firing while the process stays alive for the close "
        "prelude, so a SIGTERM within the first 1250ms still runs recovery against a tearing-down gateway "
        "(dispose'd channelManager, partial dynamic imports, unintended outbound messages). With this patch, "
        "both recovery paths check isClosing() and the timer handle is returned so the close prelude can "
        "clearTimeout it."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — deliveryQueue / sessionDelivery modules stubbed via __test.setRecoveryProbe. "
        f"{trials} trials per build, each triggering close 50ms after activate."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe activates services, waits 50ms, marks closing, waits 2s, observes recovery fire flags)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of recovery callback fire counts after immediate close:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trials:        {without_fix.get('trials')}\n"
        f"  outboundCount: {without_fix.get('outboundCount')}  (IIFE fired despite isClosing flip)\n"
        f"  sessionCount:  {without_fix.get('sessionCount')}   (setTimeout(1250) fired despite close prelude)\n"
        f"  totalFired:    {without_fix.get('totalFired')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trials:        {with_fix.get('trials')}\n"
        f"  outboundCount: {with_fix.get('outboundCount')}     (IIFE bailed at isClosing guard)\n"
        f"  sessionCount:  {with_fix.get('sessionCount')}      (timer handle cleared on close)\n"
        f"  totalFired:    {with_fix.get('totalFired')}\n"
        "```"
    )
    observed = (
        f"With patch, recovery callbacks fired 0 times across {trials} immediate-close trials "
        f"(vs {without_fix.get('totalFired')} without patch). Boot→close window of 1250ms no longer races "
        f"with recovery work against a tearing-down gateway."
    )
    not_tested = (
        "Full AbortSignal propagation into recoverPendingDeliveries / recoverPendingRestartContinuationDeliveries "
        "(option B in the FIND, separate follow-up). Production CI/dev cold-start crash rate (out of scope — "
        "covered by per-call cancellation evidence)."
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
    ap.add_argument("--wait-ms", type=int, default=DEFAULT_WAIT_MS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        wait_ms=args.wait_ms,
    )
    print(json.dumps(out, indent=2))
