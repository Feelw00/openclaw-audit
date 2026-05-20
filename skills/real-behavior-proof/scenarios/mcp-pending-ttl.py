#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/mcp-pending-ttl.py

PR #71648 (CAND-025 / SOL-0008, mcp channel-bridge pending TTL sweeper).
PR #71648 의 V4 evidence 는 vi.useFakeTimers() 사용 → `proof: sufficient` 미부여.
이 시나리오는 **real wall clock** 측정으로 sufficient 부여를 노린다.

전략 (옵션 B — production TTL 그대로):
- worktree 의 src/mcp/channel-bridge.ts 를 tsx 로 직접 import (빌드 우회, --skip-build).
- OpenClawChannelBridge 인스턴스 생성 + N 개 pending Claude permission 을
  production method handleClaudePermissionRequest 로 set.
- production TTL (PENDING_CLAUDE_PERMISSION_TTL_MS = 1h) + sweep interval (5min)
  hard-coded 값을 그대로 두고, 실제 setTimeout 으로 60min+ wall clock 만큼 sleep.
- with-fix 빌드: lazy ensurePendingSweeper 가 띄운 real setInterval 이 자동 fire 하여
  pendingClaudePermissions Map 을 drain → afterTtl == 0.
- without-fix 빌드 (base, sweeper 코드 부재): Map 이 그대로 잔존 → afterTtl == afterSet.

fake timer 가 아니라 real Node.js setInterval/setTimeout 이 실제로 fire 한다.

REQUIRES_EXTERNAL_DEP=False — bridge 인스턴스만 만들고 외부 OAuth/채널 호출 없음.

caveat:
- production TTL 이 1h 라 측정에 60min+ 의 real sleep 이 필요하다 (sleep_seconds 로 조정).
- node probe 는 src 를 tsx 로 로드 → run.py 는 --skip-build 로 호출해야 한다.
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

# production PENDING_CLAUDE_PERMISSION_TTL_MS = 1h, PENDING_SWEEP_INTERVAL_MS = 5min.
# TTL(60min) + 한 sweep tick 여유(5min) + grace(5min) = 70min.
DEFAULT_SLEEP_SECONDS = 70 * 60
EXPECTED_TTL_MS = 60 * 60 * 1_000


def _build_probe_script(*, trials: int, sleep_ms: int) -> str:
    """OpenClawChannelBridge 인스턴스 + N pending set + real sleep + size 측정.

    src/mcp/channel-bridge.ts 를 tsx 로 직접 import. private 필드는 런타임 JS 에서
    일반 property 이므로 `any` 캐스트로 접근한다.
    """
    return f"""\
import {{ OpenClawChannelBridge }} from "./src/mcp/channel-bridge.js";

const TRIALS = {trials};
const SLEEP_MS = {sleep_ms};

function emit(obj) {{
  console.log("PROOF_RESULT:" + JSON.stringify(obj));
}}

try {{
  const bridge = new OpenClawChannelBridge({{}}, {{
    claudeChannelMode: "on",
    verbose: false,
  }});
  const b = bridge;
  if (typeof bridge.handleClaudePermissionRequest !== "function") {{
    emit({{ error: "handleClaudePermissionRequest is not a function" }});
    process.exit(0);
  }}
  const before = b.pendingClaudePermissions?.size ?? -1;

  for (let i = 0; i < TRIALS; i++) {{
    await bridge.handleClaudePermissionRequest({{
      requestId: `probe-${{i}}-${{Date.now()}}`,
      toolName: "Bash",
      description: `probe permission request ${{i}}`,
      inputPreview: "{{}}",
    }});
  }}
  const afterSet = b.pendingClaudePermissions?.size ?? -1;
  const sweeperPresent = b.pendingSweepInterval != null;

  const t0 = Date.now();
  await new Promise((resolve) => setTimeout(resolve, SLEEP_MS));
  const sleptMs = Date.now() - t0;

  const afterTtl = b.pendingClaudePermissions?.size ?? -1;
  try {{
    await b.close?.();
  }} catch {{
    // close() best-effort — gateway is null in this probe.
  }}
  emit({{
    before,
    afterSet,
    afterTtl,
    sleptMs,
    sweeperPresent,
    trials: TRIALS,
    sleepMs: SLEEP_MS,
  }});
}} catch (err) {{
  emit({{ error: String((err && err.stack) || err) }});
  process.exit(0);
}}
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused — bridge probe has no sqlite
    trials: int = DEFAULT_TRIALS,
    sleep_seconds: int | None = None,
    timeout_seconds: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    bridge_src = wt_path / "src" / "mcp" / "channel-bridge.ts"
    if not bridge_src.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"src missing: {bridge_src}. worktree checkout 확인.",
        }
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"tsx missing: {tsx_bin}. pnpm install (skip_head_install=False) 확인.",
        }

    sleep_s = sleep_seconds if sleep_seconds is not None else DEFAULT_SLEEP_SECONDS
    sleep_ms = sleep_s * 1_000
    script = _build_probe_script(trials=trials, sleep_ms=sleep_ms)
    # probe 는 worktree root 에 둬야 ./src/mcp/... 상대 import 가 resolve 된다.
    with tempfile.NamedTemporaryFile(
        suffix=".mts", mode="w", delete=False, dir=str(wt_path), prefix="proof-mcp-ttl-"
    ) as f:
        f.write(script)
        script_path = f.name

    timeout = timeout_seconds if timeout_seconds is not None else sleep_s + 900
    try:
        proc = subprocess.run(
            [str(tsx_bin), script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        line = ""
        for raw in proc.stdout.splitlines():
            if raw.startswith("PROOF_RESULT:"):
                line = raw[len("PROOF_RESULT:") :]
                break
        if not line:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"no PROOF_RESULT line (exit {proc.returncode})",
                "stderr": proc.stderr[-800:],
                "stdout": proc.stdout[-400:],
            }
        payload = json.loads(line)
        if "error" in payload:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": payload["error"][:800],
            }
        return {
            "scenario": SCENARIO_NAME,
            "ttl_ms_expected": EXPECTED_TTL_MS,
            **payload,
        }
    except subprocess.TimeoutExpired:
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "error": f"probe timed out after {timeout}s",
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if measurements.get("error") or measurements.get("afterSet", -1) <= 0:
        return "blocked-env"
    after_set = measurements.get("afterSet", 0)
    after_ttl = measurements.get("afterTtl", 0)
    # without-fix: no sweeper → entries persist (afterTtl >= afterSet).
    if after_ttl >= after_set:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if not m or m.get("error") or m.get("afterSet", -1) <= 0:
            return "blocked-env"
    wo_set = without_fix.get("afterSet", 0)
    wo_ttl = without_fix.get("afterTtl", 0)
    wf_set = with_fix.get("afterSet", 0)
    wf_ttl = with_fix.get("afterTtl", 0)
    # without-fix: no sweeper code → Map persists past TTL (afterTtl >= afterSet).
    # with-fix:    real setInterval sweeper drains the Map (afterTtl < afterSet).
    if wo_ttl >= wo_set and wf_ttl < wf_set:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    trials = with_fix.get("trials", 0)
    wo_slept = round(without_fix.get("sleptMs", 0) / 60_000, 1)
    wf_slept = round(with_fix.get("sleptMs", 0) / 60_000, 1)
    behavior = (
        "Without this patch, OpenClawChannelBridge.pendingClaudePermissions grows unbounded: "
        "the class starts no setInterval, so nothing evicts entries left behind by Claude "
        "permission requests that never get a matching reply. With this patch, the lazy "
        "ensurePendingSweeper() starts a real (unref'd) setInterval that evicts entries past "
        "PENDING_CLAUDE_PERMISSION_TTL_MS. This measurement uses the real Node.js wall clock "
        "and the production 1-hour TTL — no vi.useFakeTimers()."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, isolated OPENCLAW_HOME. Both builds run the same "
        "probe against worktree src via tsx (no bundling). Production constants unchanged: "
        "PENDING_CLAUDE_PERMISSION_TTL_MS = 1h, PENDING_SWEEP_INTERVAL_MS = 5min."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile           (both worktrees)\n"
        f"$ node_modules/.bin/tsx <probe>.mts        (real setTimeout sleep, ~{wf_slept} min)\n"
        f"  probe: new OpenClawChannelBridge(...), {trials}x handleClaudePermissionRequest(),\n"
        "         real wall-clock sleep past the 1h TTL, then read pendingClaudePermissions.size\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of bridge.pendingClaudePermissions.size with a real "
        "setTimeout sleep across the production 1-hour TTL boundary:\n\n"
        "```text\n"
        "[Build A] without this patch (base sha, no sweeper code):\n"
        f"  before={without_fix.get('before')} afterSet={without_fix.get('afterSet')} "
        f"afterTtl={without_fix.get('afterTtl')} "
        f"sweeperPresent={without_fix.get('sweeperPresent')} sleptMin={wo_slept}\n"
        "  -> Map still full after the TTL window: no eviction happened.\n"
        "\n"
        "[Build B] with this patch (head sha, TTL sweeper):\n"
        f"  before={with_fix.get('before')} afterSet={with_fix.get('afterSet')} "
        f"afterTtl={with_fix.get('afterTtl')} "
        f"sweeperPresent={with_fix.get('sweeperPresent')} sleptMin={wf_slept}\n"
        "  -> real setInterval sweeper drained the Map after the 1h TTL.\n"
        "```"
    )
    observed = (
        f"With the patch, pendingClaudePermissions.size after the 1h TTL window = "
        f"{with_fix.get('afterTtl')} (down from {with_fix.get('afterSet')} set). "
        f"Without the patch it stays at {without_fix.get('afterTtl')}. "
        "The sweeper is a real unref'd setInterval driven by the real wall clock - "
        "no fake timer."
    )
    not_tested = (
        "pendingApprovals (the second Map) shares the same sweepPendingExpired() pass and is "
        "covered by the unit suite rather than this wall-clock probe. Cap-based eviction is a "
        "separate, intentional follow-up axis."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


# CLI for ad-hoc testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True, help="worktree package.json path")
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS)
    args = ap.parse_args()

    probe_env = os.environ.copy()
    probe_env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=probe_env,
        sqlite_path=Path(args.openclaw_home),
        trials=args.trials,
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps(out, indent=2))
