#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-037.py

CAND-037 (context-engine lifecycle, P3) baseline.
registry.ts:561-599 resolveContextEngine 의 contract validation fallback 분기 (factory throw /
validation throw / contractError) 에서 이미 instantiated engine 의 dispose?.() 호출 부재.
factory 가 SQLite/chokidar/HTTP keep-alive 등 native resource 셋업 시 leak.

원리:
- worktree 의 dist/context-engine/registry.js 에서 resolveContextEngine import
- 3rd-party engine factory mock — engine 인스턴스 반환 (dispose vi.fn 포함) but contract 위반
  (예: compact 메서드 missing → describeResolvedContextEngineContractError 가 non-null 반환)
- resolveContextEngine 호출 → legacy engine fallback
- mocked engine.dispose 호출 횟수 측정

REQUIRES_EXTERNAL_DEP=False.

필요 hook (config plugins.slots.contextEngine override):
- __test.installContextEnginePluginSlot({ id, factory })  — 또는 config builder
- 기존 plugin loader 우회 (test seam).
- without-fix 단계도 hook 자체는 fix-orthogonal. fix 는 dispose() 호출 추가뿐.

without-fix: disposeCalledCount === 0 (fallback path 가 engine 인스턴스 그냥 버림 = leak)
with-fix:    disposeCalledCount === 1 (세 fallback 분기 모두 dispose 호출 추가)

three fallback branches 다 측정 가능하면 더 robust — factory-throw / validation-throw / contract-error
세 트라이얼.
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
SCENARIO_NAME = "proof-CAND-037"
DEFAULT_TRIALS = 3  # three fallback branches (factory-throw / validation-throw / contract-error)


def _build_probe_script() -> str:
    return """\
import { resolveContextEngine, __test } from './dist/context-engine/registry.js';

if (!__test || typeof __test.installContextEnginePluginSlot !== 'function') {
  console.log(JSON.stringify({ skipped: '__test.installContextEnginePluginSlot hook missing (plugin slot DI seam needed)' }));
  process.exit(0);
}

const trialResults = [];

// Trial 1: factory throws — engine 인스턴스 생성 안 됨 → dispose 대상 없음 (expected dispose = 0 both)
{
  let disposeCalls = 0;
  __test.installContextEnginePluginSlot({
    id: 'probe-factory-throw',
    factory: async () => { throw new Error('factory-throw'); },
  });
  try { await resolveContextEngine({ slotId: 'probe-factory-throw' }); } catch {}
  trialResults.push({ branch: 'factory-throw', disposeCalls });
}

// Trial 2: factory returns engine, then validation throws
{
  let disposeCalls = 0;
  __test.installContextEnginePluginSlot({
    id: 'probe-validation-throw',
    factory: async () => ({
      id: 'probe-validation-throw',
      compact: async () => { throw new Error('contract-validation-throw'); },
      dispose: async () => { disposeCalls++; },
    }),
    validateOnCreate: true,
  });
  try { await resolveContextEngine({ slotId: 'probe-validation-throw' }); } catch {}
  trialResults.push({ branch: 'validation-throw', disposeCalls });
}

// Trial 3: factory returns engine, contract describer returns non-null error string
{
  let disposeCalls = 0;
  __test.installContextEnginePluginSlot({
    id: 'probe-contract-error',
    factory: async () => ({
      id: 'probe-contract-error',
      // compact missing — describeResolvedContextEngineContractError 가 contract violation 보고
      dispose: async () => { disposeCalls++; },
    }),
  });
  try { await resolveContextEngine({ slotId: 'probe-contract-error' }); } catch {}
  trialResults.push({ branch: 'contract-error', disposeCalls });
}

const totalDispose = trialResults.reduce((s, t) => s + t.disposeCalls, 0);
const branchesWithDispose = trialResults.filter(t => t.branch !== 'factory-throw' && t.disposeCalls > 0).length;

console.log(JSON.stringify({
  trials: 3,
  trialResults,
  totalDispose,
  branchesWithDispose,
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
    dist_target = wt_path / "dist" / "context-engine" / "registry.js"
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
    # without-fix: validation-throw + contract-error 분기 모두 dispose 안 부름 (branchesWithDispose == 0)
    if measurements.get("branchesWithDispose", -1) == 0:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_branches = without_fix.get("branchesWithDispose", 0)
    wf_branches = with_fix.get("branchesWithDispose", 0)
    if wo_branches == 0 and wf_branches >= 2:  # 두 instantiated 분기 모두 dispose
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, resolveContextEngine's three fallback branches (factory throw, validation throw, "
        "contract error) discard the instantiated 3rd-party engine without calling dispose(). Engines that "
        "opened SQLite handles, chokidar watchers, or HTTP keep-alive sockets in their factory leak those "
        "native resources until process exit. Caller-side dispose paths (run.ts:3094, compact.queued.ts) "
        "only see the legacy engine returned after fallback. With this patch, each fallback branch calls "
        "`await engine?.dispose?.().catch(() => {})` before degrading."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — three probe plugin slots installed via __test.installContextEnginePluginSlot "
        "DI seam to exercise each fallback branch in turn."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe installs 3 probe slots: factory-throw, validation-throw, contract-error; counts dispose calls)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of engine.dispose() call count across three fallback branches:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trialResults: {json.dumps(without_fix.get('trialResults'))}\n"
        f"  totalDispose: {without_fix.get('totalDispose')}\n"
        f"  branchesWithDispose: {without_fix.get('branchesWithDispose')}  (instantiated engines leaked)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trialResults: {json.dumps(with_fix.get('trialResults'))}\n"
        f"  totalDispose: {with_fix.get('totalDispose')}\n"
        f"  branchesWithDispose: {with_fix.get('branchesWithDispose')}      (both instantiated branches dispose)\n"
        "```"
    )
    observed = (
        f"With patch, both instantiated fallback branches (validation-throw, contract-error) call dispose() "
        f"(branchesWithDispose={with_fix.get('branchesWithDispose')}/2), versus 0/2 without patch. The "
        f"factory-throw branch correctly skips dispose (no instance to dispose)."
    )
    not_tested = (
        "Strengthened dispose contract in types.ts (separate follow-up). Production-scale fd accumulation "
        "over hours of daemon uptime (out of scope — quantified by per-resolve dispose call alone)."
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
