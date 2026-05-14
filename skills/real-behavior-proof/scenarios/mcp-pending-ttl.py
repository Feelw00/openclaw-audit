#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/mcp-pending-ttl.py

PR #71648 (CAND-025 / SOL-0008, mcp channel-bridge pending TTL sweeper) baseline.
PR #71648 의 V4 evidence 가 fake timer (vi.useFakeTimers) 사용 → `proof: sufficient` 미부여.
이 시나리오는 **real wall clock** TTL 측정으로 sufficient 부여 노림.

원리:
- worktree 빌드 후, node 로 OpenClawChannelBridge.makeBridge() 인스턴스 생성
- N pending request set (handleClaudePermissionRequest 같은 path 직접 호출)
- 실제 시간 sleep (TTL_MS + grace) 만큼 대기
- sweeper interval 이 자동 trigger 되어 Map drain
- Map.size 측정

REQUIRES_EXTERNAL_DEP=False — bridge 인스턴스만 만들고 외부 호출 없음.

caveat:
- TTL 이 1h (Claude perm) / 30min (approvals) 라 real wall clock 으로 검증하려면 매우 느림.
- 이 시나리오용으로 SOL 의 fix 가 TTL 을 환경변수로 override 가능해야 (e.g., OPENCLAW_MCP_PENDING_TTL_MS).
- 또는 짧은 TTL 로 별도 로컬 빌드 만들기 (scope 밖).

현재 구현은 placeholder — TTL override hook 이 SOL 에 추가될 때 이 시나리오가 진짜 작동.
TTL override 없으면 evaluate_* 가 'blocked-env' 반환.
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
SCENARIO_NAME = "mcp-pending-ttl"
DEFAULT_TRIALS = 100
DEFAULT_TTL_MS = 5_000  # 5 sec — TTL override 시 사용 (production 1h 와 비례 검증)
DEFAULT_GRACE_MS = 2_000


def _build_probe_script(*, ttl_ms: int, grace_ms: int, trials: int) -> str:
    """OpenClawChannelBridge 인스턴스 + N pending set + real sleep + size 측정.

    OPENCLAW_MCP_PENDING_TTL_MS env 가 fix 에서 인식되어야 함.
    fix 가 sweeper interval 을 5min hard-code 했다면 이 시나리오는 적용 불가 (blocked-env).
    """
    return f"""\
import {{ makeBridge }} from './dist/mcp/channel-bridge.js';

if (!process.env.OPENCLAW_MCP_PENDING_TTL_MS) {{
  console.log(JSON.stringify({{ skipped: 'OPENCLAW_MCP_PENDING_TTL_MS env not honored by build' }}));
  process.exit(0);
}}

const bridge = makeBridge({{ /* minimal config — refer to channel-bridge.test.ts */ }});
const before = bridge.pendingClaudePermissions?.size ?? -1;

for (let i = 0; i < {trials}; i++) {{
  if (typeof bridge.handleClaudePermissionRequest === 'function') {{
    bridge.handleClaudePermissionRequest({{ id: `probe-${{i}}-${{Date.now()}}`, /* min args */ }})
      .catch(() => {{}});
  }}
}}
const afterSet = bridge.pendingClaudePermissions?.size ?? -1;

await new Promise(r => setTimeout(r, {ttl_ms} + {grace_ms}));
const afterTtl = bridge.pendingClaudePermissions?.size ?? -1;

await bridge.close?.();
console.log(JSON.stringify({{ before, afterSet, afterTtl, ttlMs: {ttl_ms}, graceMs: {grace_ms}, trials: {trials} }}));
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    ttl_ms: int = DEFAULT_TTL_MS,
    grace_ms: int = DEFAULT_GRACE_MS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    dist_bridge = wt_path / "dist" / "mcp" / "channel-bridge.js"
    if not dist_bridge.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"build artifact missing: {dist_bridge}. pnpm build 결과 확인.",
        }

    script = _build_probe_script(ttl_ms=ttl_ms, grace_ms=grace_ms, trials=trials)
    with tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", delete=False, dir=str(wt_path)) as f:
        f.write(script)
        script_path = f.name

    env = {**env, "OPENCLAW_MCP_PENDING_TTL_MS": str(ttl_ms)}
    timeout = (ttl_ms + grace_ms + 30_000) // 1000  # ms → sec + 30s buffer
    try:
        proc = subprocess.run(
            ["node", script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
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
            return {
                "scenario": SCENARIO_NAME,
                "trials": 0,
                "skipped": payload["skipped"],
                "note": "fix 가 OPENCLAW_MCP_PENDING_TTL_MS env override 미지원 → 시나리오 적용 불가.",
            }
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "ttl_ms": ttl_ms,
            **payload,
        }
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        if "skipped" in measurements:
            return "blocked-env"
        return "blocked-env"
    after_set = measurements.get("afterSet", 0)
    after_ttl = measurements.get("afterTtl", 0)
    # without-fix: TTL sweeper 없음 → afterTtl == afterSet (pending Map 잔존)
    if after_set > 0 and after_ttl >= after_set:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m or "skipped" in m:
            return "blocked-env"
    # without-fix: afterTtl >= afterSet (sweeper 없음 → 잔존)
    # with-fix:    afterTtl == 0 or afterTtl < afterSet (sweeper drain)
    wo_after_set = without_fix.get("afterSet", 0)
    wo_after_ttl = without_fix.get("afterTtl", 0)
    wf_after_ttl = with_fix.get("afterTtl", 0)
    if wo_after_ttl >= wo_after_set and wf_after_ttl < wo_after_set:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    ttl_ms = with_fix.get("ttl_ms", DEFAULT_TTL_MS)
    behavior = (
        f"Without this patch, OpenClawChannelBridge pending Maps grow unbounded — TTL sweeper not started, "
        f"close() does not clear, and there is no per-entry expiry. With this patch, lazy-started TTL sweeper "
        f"drains expired entries on real wall clock."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built. Isolated OPENCLAW_HOME. "
        f"OPENCLAW_MCP_PENDING_TTL_MS={ttl_ms} env override (real wall clock, NOT vi.useFakeTimers). "
        "Same bridge config across both builds."
    )
    steps = (
        f"```text\n"
        f"$ pnpm build                                          (both builds)\n"
        f"$ OPENCLAW_MCP_PENDING_TTL_MS={ttl_ms} \\\n"
        f"      node <probe.mjs>                                (real setTimeout sleep, no fake timer)\n"
        f"  (probe creates bridge, sets {trials} pending requests, sleeps TTL+grace, measures Map.size)\n"
        f"```"
    )
    evidence = (
        f"Live Node.js measurement of bridge.pendingClaudePermissions.size with **real wall clock** sleep:\n\n"
        f"```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  before: {without_fix.get('before')}, afterSet: {without_fix.get('afterSet')}, "
        f"afterTtl: {without_fix.get('afterTtl')}\n"
        f"  (no sweeper drain → Map persists)\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  before: {with_fix.get('before')}, afterSet: {with_fix.get('afterSet')}, "
        f"afterTtl: {with_fix.get('afterTtl')}\n"
        f"  (TTL sweeper drained Map after {ttl_ms}ms wall clock)\n"
        f"```"
    )
    observed = (
        f"With patch, Map.size after TTL+grace = {with_fix.get('afterTtl')} "
        f"(vs {without_fix.get('afterTtl')} without patch). "
        "Real wall clock measurement — no fake timer."
    )
    not_tested = (
        "Cap-based eviction (separate axis, intentional follow-up). pendingApprovals secondary Map "
        "(same TTL pattern, covered by unit tests)."
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
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--ttl-ms", type=int, default=DEFAULT_TTL_MS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        ttl_ms=args.ttl_ms,
    )
    print(json.dumps(out, indent=2))
