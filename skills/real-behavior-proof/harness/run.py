#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/run.py

real-behavior-proof skill 의 entry point.

사용:
  # pre-sol (SOL 작성 전, base 빌드 단독으로 결함 재현)
  python skills/real-behavior-proof/harness/run.py \
      --target SOL-0004 --mode pre-sol \
      --scenario gateway-map-size \
      --base-sha <upstream/main HEAD> \
      --trials 100

  # post-sol (SOL chosen_fix 결정 후, with/without 비교)
  python skills/real-behavior-proof/harness/run.py \
      --target SOL-0004 --mode post-sol \
      --scenario gateway-map-size \
      --base-sha <upstream/main HEAD> --head-sha <fix HEAD> \
      --trials 100

흐름:
  1. mode YAML 로드 (modes/<mode>.yaml)
  2. scenario 모듈 동적 import (scenarios/<scenario>.py)
  3. env_isolate.isolated_home() 으로 격리 환경
  4. build.build_single() (pre-sol) 또는 build_pair() (post-sol)
  5. scenario.run_scenario() 호출 (각 빌드별)
  6. scenario.evaluate_pre/post() 로 status 결정
  7. render_proof + state 로 결과 영속화
  8. proofs/PROOF-*.md 경로 + status JSON stdout
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml

HARNESS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = HARNESS_DIR.parent
AUDIT_ROOT = SKILL_ROOT.parent.parent
MODES_DIR = SKILL_ROOT / "modes"
SCENARIOS_DIR = SKILL_ROOT / "scenarios"

sys.path.insert(0, str(HARNESS_DIR))
import build  # noqa: E402
import env_isolate  # noqa: E402
import render_proof  # noqa: E402
import state as state_mod  # noqa: E402


def load_mode(mode: str) -> dict:
    path = MODES_DIR / f"{mode}.yaml"
    if not path.exists():
        sys.exit(f"ERROR: mode '{mode}' 없음 ({path})")
    with path.open() as f:
        return yaml.safe_load(f)


def load_scenario(name: str):
    """scenarios/<name>.py 동적 import. dash 가 있는 파일 이름 지원."""
    path = SCENARIOS_DIR / f"{name}.py"
    if not path.exists():
        sys.exit(f"ERROR: scenario '{name}' 없음 ({path})")
    mod_name = f"scenario_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCENARIOS_DIR))
    sys.modules[mod_name] = mod  # Python 3.14 dataclass 가 sys.modules 에서 cls.__module__ 조회
    spec.loader.exec_module(mod)
    required = ["run_scenario", "evaluate_pre", "evaluate_post", "SCENARIO_NAME"]
    missing = [a for a in required if not hasattr(mod, a)]
    if missing:
        sys.exit(f"ERROR: scenario '{name}' missing attrs: {missing}")
    return mod


def _run_pre_sol(args, mode_cfg: dict, scenario_mod) -> dict[str, Any]:
    """pre-sol: base 빌드 단독으로 scenario 실행."""
    require_oauth = getattr(scenario_mod, "REQUIRES_EXTERNAL_DEP", False)
    started_at = render_proof.utc_now()

    with env_isolate.isolated_home(require_oauth=require_oauth) as env:
        try:
            built = build.build_single(
                sha=args.base_sha, label="base", proof_id=env["proof_id"], skip_install=args.skip_install
            )
        except Exception as e:
            return {
                "status": "blocked-env",
                "measurements": {"error": f"build failed: {e}", "trace": traceback.format_exc()[:1000]},
                "base_sha": args.base_sha,
                "head_sha": None,
                "started_at": started_at,
                "finished_at": render_proof.utc_now(),
                "pr_body_section": None,
            }
        try:
            measurements = scenario_mod.run_scenario(
                node_entry=built.node_entry,
                env=env["env"],
                sqlite_path=env["sqlite"],
                trials=args.trials,
                **{k: getattr(args, k) for k in ("sleep_seconds", "timeout_seconds") if hasattr(args, k) and getattr(args, k) is not None},
            )
            status = scenario_mod.evaluate_pre(measurements)
        except Exception as e:
            measurements = {"error": str(e), "trace": traceback.format_exc()[:1000]}
            status = "blocked-env"
        finally:
            if not args.keep_worktrees:
                built.cleanup()

    return {
        "status": status,
        "measurements": measurements,
        "base_sha": args.base_sha,
        "head_sha": None,
        "started_at": started_at,
        "finished_at": render_proof.utc_now(),
        "pr_body_section": None,
    }


def _run_post_sol(args, mode_cfg: dict, scenario_mod) -> dict[str, Any]:
    """post-sol: base + head 두 빌드 → 비교."""
    require_oauth = getattr(scenario_mod, "REQUIRES_EXTERNAL_DEP", False)
    started_at = render_proof.utc_now()
    if not args.head_sha:
        sys.exit("ERROR: post-sol mode 는 --head-sha 필수")

    pair: build.BuildPair | None = None
    measurements: dict[str, Any]
    status = "pending"
    pr_body_section = None

    try:
        # base build → run, then head build → run.
        # 한 isolated_home 두 빌드 같이 쓰면 cron jobs/state 가 섞이므로 빌드별 격리.
        # 시간 비용을 줄이려면 build 는 둘 다 먼저 만들고, scenario 만 isolated_home 반복.
        pair = build.build_pair(
            base_sha=args.base_sha, head_sha=args.head_sha, skip_install=args.skip_install
        )
    except Exception as e:
        return {
            "status": "blocked-env",
            "measurements": {"error": f"build_pair failed: {e}", "trace": traceback.format_exc()[:1000]},
            "base_sha": args.base_sha,
            "head_sha": args.head_sha,
            "started_at": started_at,
            "finished_at": render_proof.utc_now(),
            "pr_body_section": None,
        }

    try:
        # base run
        with env_isolate.isolated_home(require_oauth=require_oauth) as env_base:
            without_fix = scenario_mod.run_scenario(
                node_entry=pair.base.node_entry,
                env=env_base["env"],
                sqlite_path=env_base["sqlite"],
                trials=args.trials,
                **{k: getattr(args, k) for k in ("sleep_seconds", "timeout_seconds") if hasattr(args, k) and getattr(args, k) is not None},
            )
        # head run
        with env_isolate.isolated_home(require_oauth=require_oauth) as env_head:
            with_fix = scenario_mod.run_scenario(
                node_entry=pair.head.node_entry,
                env=env_head["env"],
                sqlite_path=env_head["sqlite"],
                trials=args.trials,
                **{k: getattr(args, k) for k in ("sleep_seconds", "timeout_seconds") if hasattr(args, k) and getattr(args, k) is not None},
            )

        measurements = {"without_fix": without_fix, "with_fix": with_fix}
        status = scenario_mod.evaluate_post(without_fix, with_fix)

        if status == "collected" and hasattr(scenario_mod, "render_pr_evidence"):
            fields = scenario_mod.render_pr_evidence(without_fix, with_fix)
            pr_body_section = render_proof.render_pr_body_section(**fields)
    except Exception as e:
        measurements = {"error": str(e), "trace": traceback.format_exc()[:1000]}
        status = "blocked-env"
    finally:
        if not args.keep_worktrees and pair is not None:
            pair.cleanup()

    return {
        "status": status,
        "measurements": measurements,
        "base_sha": args.base_sha,
        "head_sha": args.head_sha,
        "started_at": started_at,
        "finished_at": render_proof.utc_now(),
        "pr_body_section": pr_body_section,
    }


def _persist(args, mode_cfg: dict, result: dict[str, Any], scenario_name: str) -> dict[str, Any]:
    """proofs/PROOF-*.md 작성 + SOL/CAND frontmatter 갱신 + state transition 기록."""
    phase = "pre" if args.mode == "pre-sol" else "post"
    proof_path = render_proof.proof_file_path(AUDIT_ROOT, args.target, phase)
    body = render_proof.render_proof_record(
        target=args.target,
        phase=phase,
        scenario=scenario_name,
        status=result["status"],
        measurements=result["measurements"],
        pr_body_section=result.get("pr_body_section"),
        base_sha=result.get("base_sha"),
        head_sha=result.get("head_sha"),
        started_at=result["started_at"],
        finished_at=result["finished_at"],
    )
    state_mod.write_proof_record(proof_path, body)

    proof_record_rel = str(proof_path.relative_to(AUDIT_ROOT))

    if args.target.startswith("SOL-"):
        sol_path = AUDIT_ROOT / "solutions" / f"{args.target}.md"
        if sol_path.exists():
            state_mod.update_sol_frontmatter(
                sol_path,
                phase,
                status=result["status"],
                proof_record=proof_record_rel,
                measurements=result["measurements"],
                scenario=scenario_name,
                pr_body_section=result.get("pr_body_section"),
            )
    elif args.target.startswith("CAND-") and phase == "pre":
        cand_path = AUDIT_ROOT / "issue-candidates" / f"{args.target}.md"
        if cand_path.exists():
            state_mod.update_cand_frontmatter_pre_proof(
                cand_path,
                status=result["status"],
                proof_record=proof_record_rel,
                measurements=result["measurements"],
                scenario=scenario_name,
            )

    state_mod.record_transition(
        args.target,
        from_state=None,
        to_state=state_mod.proof_status_to_transition(phase, result["status"]),
        actor="real-behavior-proof",
        reason=f"scenario={scenario_name} mode={args.mode}",
    )

    return {
        "target": args.target,
        "mode": args.mode,
        "scenario": scenario_name,
        "status": result["status"],
        "proof_record": proof_record_rel,
        "pr_body_section_included": result.get("pr_body_section") is not None,
        "next_action": mode_cfg.get("next_action_by_status", {}).get(result["status"], "(미정 — 사람 검토)"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="real-behavior-proof harness entry")
    ap.add_argument("--target", required=True, help="SOL-NNNN or CAND-NNN")
    ap.add_argument("--mode", required=True, choices=["pre-sol", "post-sol"])
    ap.add_argument("--scenario", required=True, help="scenarios/<name>.py basename")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", help="post-sol 모드 필수")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--sleep-seconds", type=int)
    ap.add_argument("--timeout-seconds", type=int)
    ap.add_argument("--skip-install", action="store_true", help="pnpm install 생략 (디버그)")
    ap.add_argument("--keep-worktrees", action="store_true", help="실행 후 worktree 보존 (디버그)")
    ap.add_argument("--dry-run", action="store_true", help="build/run 생략, persist 만 (테스트)")
    ap.add_argument(
        "--dry-run-measurements",
        help="dry-run 시 사용할 measurements JSON",
        default='{"trials": 0, "lost_count": 0}',
    )
    ap.add_argument(
        "--dry-run-status",
        default="collected",
        choices=list(state_mod.VALID_STATUSES),
    )
    args = ap.parse_args()

    mode_cfg = load_mode(args.mode)
    scenario_mod = load_scenario(args.scenario)
    scenario_name = scenario_mod.SCENARIO_NAME

    if args.dry_run:
        measurements = json.loads(args.dry_run_measurements)
        result = {
            "status": args.dry_run_status,
            "measurements": measurements,
            "base_sha": args.base_sha,
            "head_sha": args.head_sha,
            "started_at": render_proof.utc_now(),
            "finished_at": render_proof.utc_now(),
            "pr_body_section": None,
        }
        if args.mode == "post-sol" and args.dry_run_status == "collected" and hasattr(scenario_mod, "render_pr_evidence"):
            # 가짜 with/without 측정으로 PR body 렌더
            fake_without = {"trials": 1, "lost_count": 1, "trial_results": [{"status": "lost"}], "sleep_seconds": 420}
            fake_with = {"trials": 1, "lost_count": 0, "trial_results": [{"status": "failed"}], "sleep_seconds": 420}
            fields = scenario_mod.render_pr_evidence(fake_without, fake_with)
            result["pr_body_section"] = render_proof.render_pr_body_section(**fields)
            result["measurements"] = {"without_fix": fake_without, "with_fix": fake_with}
    elif args.mode == "pre-sol":
        result = _run_pre_sol(args, mode_cfg, scenario_mod)
    else:
        result = _run_post_sol(args, mode_cfg, scenario_mod)

    if args.dry_run:
        # 영속화 skip — state.yaml/proofs/SOL frontmatter 변경 안 함.
        # 검증 목적: PR body section 이 openclaw policy 통과하는지 확인.
        summary = {
            "target": args.target,
            "mode": args.mode,
            "scenario": scenario_name,
            "status": result["status"],
            "dry_run": True,
            "pr_body_section_included": result.get("pr_body_section") is not None,
            "pr_body_section_preview": (result.get("pr_body_section") or "")[:600],
        }
    else:
        summary = _persist(args, mode_cfg, result, scenario_name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
