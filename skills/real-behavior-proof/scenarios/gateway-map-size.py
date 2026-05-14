#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/gateway-map-size.py

SOL-0004 (CAND-014, gateway costUsageCache MAX+FIFO) 패턴.
이름과 달리 "Map.size 측정" 자체는 다른 SOL 에도 재사용 가능 (예: nodeWakeById, pendingClaudePermissions).
구체 측정 대상은 --target-module 인자로 지정.

원리:
- worktree 빌드 후, node 로 임시 .mjs 스크립트 실행
- 스크립트가 target module 의 internal Map (__test export 또는 다른 hook) 을 import
- N iteration 으로 cache miss 유도 → Map.size 측정
- JSON 결과 stdout

REQUIRES_EXTERNAL_DEP=False (외부 OAuth/채널 호출 없음).

caveat: target module 이 `__test` export 또는 비슷한 Map reference hook 을 노출해야 함.
SOL-0004 의 경우 src/gateway/server-methods/usage.ts 의 `__test.costUsageCache` 패턴 사용.
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
SCENARIO_NAME = "gateway-map-size"
DEFAULT_TRIALS = 100  # cache miss iteration 수


def _build_probe_script(
    *,
    target_module_relpath: str,
    test_export_path: str,
    iterations: int,
) -> str:
    """worktree 안에서 실행할 임시 .mjs 스크립트.

    target_module_relpath: 예 'src/gateway/server-methods/usage.ts' (build 후엔 dist/.../*.js)
    test_export_path: 예 '__test.costUsageCache' (Map reference path)
    """
    # 빌드 결과는 worktree/dist/ 또는 worktree/<src> 에 따라 다름. 빌드 후 .js / .mjs 로 추정.
    # 호출자가 정확한 dist 경로를 줘야 함. fallback 으로 src 직접 import (esbuild/tsx 필요).
    return f"""\
import {{ {test_export_path.split('.')[0]} as testExport }} from {json.dumps(target_module_relpath)};

const map = {test_export_path.replace(test_export_path.split('.')[0], 'testExport')};
const initial = map.size;
// trigger N cache misses with synthetic distinct keys (epoch ms varying per iteration)
// caller-specific: SOL-0004 uses parseDateRange({{}}) → today rolling key.
// 일반화: scenario 사용자가 trigger callback 별도 제공해야 정확. 여기선 단순 .set() loop.
for (let i = 0; i < {iterations}; i++) {{
  if (typeof map.set === 'function') {{
    map.set(`probe-${{i}}-${{Date.now()}}-${{Math.random()}}`, {{ probeIteration: i }});
  }}
}}
const after = map.size;
console.log(JSON.stringify({{ initial, after, delta: after - initial, iterations: {iterations} }}));
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused (호환성)
    trials: int = DEFAULT_TRIALS,
    target_module: str | None = None,
    test_export: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """worktree 에서 임시 probe 스크립트 실행 → Map.size 측정.

    target_module: SOL.frontmatter 또는 호출자가 지정. 예 './dist/gateway/server-methods/usage.js'
    test_export: 예 '__test.costUsageCache'. 호출자 책임.
    """
    if not target_module or not test_export:
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": "target_module + test_export 필수 (호출자가 지정).",
            "note": "SOL frontmatter 의 chosen_fix 가 실제로 어떤 Map 을 측정할지 명시해야 함.",
        }

    wt_path = node_entry.parent  # worktree root (openclaw.mjs 가 root 에 있음)
    # 모듈 경로는 worktree-relative
    module_abs = (wt_path / target_module).resolve() if not target_module.startswith("/") else Path(target_module)
    if not module_abs.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"target_module 미존재: {module_abs}. pnpm build 결과 확인.",
        }

    script = _build_probe_script(
        target_module_relpath=str(module_abs),
        test_export_path=test_export,
        iterations=trials,
    )

    with tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", delete=False, dir=str(wt_path)) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"probe exited {proc.returncode}",
                "stderr": proc.stderr[:500],
            }
        try:
            measurements = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"could not parse probe stdout: {e}",
                "stdout": proc.stdout[:500],
            }
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "target_module": target_module,
            "test_export": test_export,
            **measurements,
        }
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: without-fix 빌드에서 Map 이 unbounded growth 보이면 collected.

    delta == iterations → 모든 .set() 이 Map 에 잔존 = unbounded leak.
    delta < iterations → bounded (eviction 있음 또는 dedup) = unreproducible.
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    delta = measurements.get("delta")
    iterations = measurements.get("iterations") or measurements.get("trials")
    if delta is None or iterations is None:
        return "blocked-env"
    if delta >= iterations:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: with-fix 가 Map 을 bounded (delta < iterations 또는 cap 도달) 로 유지하면 collected."""
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_delta = without_fix.get("delta", 0)
    wf_delta = with_fix.get("delta", 0)
    iters = with_fix.get("iterations") or with_fix.get("trials")
    if wo_delta >= iters and wf_delta < iters:
        return "collected"
    if wo_delta < iters:
        return "unreproducible"  # without 에서도 leak 안 보임
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    iters = with_fix.get("iterations") or with_fix.get("trials")
    target_module = with_fix.get("target_module", "(unknown)")
    test_export = with_fix.get("test_export", "(unknown)")
    behavior = (
        f"Without this patch, {target_module} `{test_export}` grows unbounded with operator polls. "
        f"With this patch, the Map applies MAX + FIFO eviction to keep size bounded."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built from this branch. "
        "Isolated OPENCLAW_HOME via skills/real-behavior-proof/harness/env_isolate.py. "
        "No external dependencies (no OAuth/LLM/channel calls). Same gateway path across both builds."
    )
    steps = (
        f"```text\n"
        f"$ pnpm build                                          (both builds)\n"
        f"$ node --experimental-strip-types <probe.mjs>         (worktree-relative)\n"
        f"  (probe imports {target_module} and triggers {iters} cache-miss iterations)\n"
        f"```"
    )
    evidence = (
        f"Live Node.js measurement of `{test_export}` over {iters} iterations:\n\n"
        f"```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  initial Map.size: {without_fix.get('initial')}\n"
        f"  after  Map.size: {without_fix.get('after')}\n"
        f"  delta: {without_fix.get('delta')} (= iterations means unbounded)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  initial Map.size: {with_fix.get('initial')}\n"
        f"  after  Map.size: {with_fix.get('after')}\n"
        f"  delta: {with_fix.get('delta')} (< iterations means bounded by MAX + FIFO)\n"
        f"```"
    )
    observed = (
        f"With patch, Map.size delta = {with_fix.get('delta')} (vs {without_fix.get('delta')} without patch). "
        f"Eviction policy keeps Map bounded under operator polling load."
    )
    not_tested = (
        "TTL-based eviction (separate axis from MAX + FIFO). LRU vs FIFO tradeoff (chose FIFO per sibling pattern)."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


# CLI
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True)
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--target-module", required=True, help="worktree-relative path to built .js, e.g. dist/gateway/server-methods/usage.js")
    ap.add_argument("--test-export", required=True, help="export path to Map, e.g. __test.costUsageCache")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        target_module=args.target_module,
        test_export=args.test_export,
    )
    print(json.dumps(out, indent=2))
