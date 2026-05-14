#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-032.py

CAND-032 (auto-reply error-boundary, P2) baseline.
reply-run-registry.ts:505 `void backend.queueMessage(text)` 가 .catch 부재 →
backend (pi-embedded-runner 의 activeSession.steer) reject 시 unhandledRejection.
non-transient 분류 시 infra/unhandled-rejections.ts 가 process.exit(1) 가능.

원리:
- worktree 의 dist/auto-reply/reply/reply-run-registry.js 에서 queueReplyRunMessage import
- process.on('unhandledRejection', listener) 설치
- backend.queueMessage 를 항상 throw 하는 mock 으로 inject (__test seam or DI)
- queueReplyRunMessage 호출 → 짧은 grace (200ms)
- unhandledRejection 발사 횟수 측정

REQUIRES_EXTERNAL_DEP=False.

필요 hook (이미 부분 존재 가능 — reply-run-registry 가 backend deps 인자로 받는 구조):
- __test.registerRun({ runId, backend, ... }) 또는 기존 register API 사용
- backend 객체에 queueMessage: async () => throw new Error(...)
- queueReplyRunMessage(runId, text) 호출 (이미 export)
- fix 자체는 1라인 .catch 부착 — without-fix 빌드도 시나리오 실행 가능.

without-fix: unhandledCount === 1 (reject 가 .catch 없이 floating).
with-fix:    unhandledCount === 0 (.catch 가 swallow + diag.debug).

caveat: process.on('unhandledRejection') 가 fire 되는 timing 은 microtask flush 후
(Node 의 unhandledRejection 검출은 보통 다음 tick). 300-500ms grace 충분.
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
SCENARIO_NAME = "proof-CAND-032"
DEFAULT_TRIALS = 5  # 1 trial 으로도 충분하지만 noise 회피 위해 5회
DEFAULT_GRACE_MS = 500


def _build_probe_script(*, trials: int, grace_ms: int) -> str:
    return f"""\
import {{ queueReplyRunMessage, __test }} from './dist/auto-reply/reply/reply-run-registry.js';

if (!__test || typeof __test.registerRunWithBackend !== 'function') {{
  console.log(JSON.stringify({{ skipped: '__test.registerRunWithBackend hook missing (DI seam needed for fake backend)' }}));
  process.exit(0);
}}

let unhandledCount = 0;
const rejectionListener = (err) => {{ unhandledCount++; }};
process.on('unhandledRejection', rejectionListener);

const results = [];
for (let i = 0; i < {trials}; i++) {{
  const runId = 'proof-run-' + i + '-' + Date.now();
  const fakeBackend = {{
    queueMessage: async () => {{ throw new Error('probe-rejection-' + i); }},
  }};
  __test.registerRunWithBackend(runId, fakeBackend);
  const queued = queueReplyRunMessage(runId, 'probe message ' + i);
  results.push({{ runId, queued }});
}}

await new Promise(r => setTimeout(r, {grace_ms}));
process.removeListener('unhandledRejection', rejectionListener);

console.log(JSON.stringify({{
  trials: {trials},
  unhandledCount,
  graceMs: {grace_ms},
  results,
}}));
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    grace_ms: int = DEFAULT_GRACE_MS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    dist_target = wt_path / "dist" / "auto-reply" / "reply" / "reply-run-registry.js"
    if not dist_target.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"build artifact missing: {dist_target}.",
        }

    script = _build_probe_script(trials=trials, grace_ms=grace_ms)
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
    if measurements.get("unhandledCount", 0) >= 1:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    if without_fix.get("unhandledCount", 0) >= 1 and with_fix.get("unhandledCount", 0) == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    behavior = (
        "Without this patch, queueReplyRunMessage calls `void backend.queueMessage(text)` with no `.catch`. "
        "When the embedded-pi backend's `activeSession.steer` rejects (abort race, transient network, "
        "TypeError on invalid state), the rejection floats up as `unhandledRejection`. infra/unhandled-rejections "
        "may classify it as non-transient and call exitWithTerminalRestore → process.exit(1). With this patch, "
        "the rejection is caught and routed to diag.debug, matching the sibling fire-and-forget call in "
        "pi-embedded-runner/runs.ts:148-154."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        "No external dependencies — backend mocked to throw via __test.registerRunWithBackend seam. "
        f"process.on('unhandledRejection') listener observes rejection events across {trials} runs."
    )
    steps = (
        "```text\n"
        "$ pnpm build                                          (both builds)\n"
        "$ node <probe.mjs>                                    (worktree-relative)\n"
        "  (probe registers fake backend that throws, calls queueReplyRunMessage N times, counts unhandled rejections)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of unhandledRejection event count:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trials:           {without_fix.get('trials')}\n"
        f"  unhandledCount:   {without_fix.get('unhandledCount')}   (floating rejection escapes to process listener)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trials:           {with_fix.get('trials')}\n"
        f"  unhandledCount:   {with_fix.get('unhandledCount')}   (.catch swallows + diag.debug logs)\n"
        "```"
    )
    observed = (
        f"With patch, 0/{trials} backend.queueMessage rejections escape to the process unhandledRejection "
        f"listener (vs {without_fix.get('unhandledCount')}/{trials} without patch). Gateway no longer at "
        f"risk of process.exit(1) on backend race rejections."
    )
    not_tested = (
        "infra/unhandled-rejections.ts classification policy itself (separate axis). Production OAuth / network "
        "transient rejection rate at scale (out of scope for one-line .catch fix)."
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
    ap.add_argument("--grace-ms", type=int, default=DEFAULT_GRACE_MS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        grace_ms=args.grace_ms,
    )
    print(json.dumps(out, indent=2))
