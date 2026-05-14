#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/state.py

proof 결과 영속화. 세 곳에 동시 기록:
1. proofs/PROOF-{target}-{phase}-{ts}.md  (영구 evidence + PR body section)
2. solutions/SOL-NNNN.md frontmatter 의 pre_sol_proof / post_sol_proof 객체 갱신
3. local-state/state.yaml + history.jsonl 의 transition (skills/openclaw-audit/harness/local_state.py 재사용)

호출자: skills/real-behavior-proof/harness/run.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: pyyaml 필요. /tmp/openclaw-audit-venv/bin/pip install pyyaml\n")
    sys.exit(2)

HARNESS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = HARNESS_DIR.parent
AUDIT_ROOT = SKILL_ROOT.parent.parent

# local_state.py 재사용
sys.path.insert(0, str(AUDIT_ROOT / "skills" / "openclaw-audit" / "harness"))
import local_state  # noqa: E402

VALID_STATUSES = (
    "pending",
    "collected",
    "unreproducible",
    "blocked-external-dep",
    "blocked-env",
    "skipped-by-user",
)


def write_proof_record(path: Path, body: str) -> None:
    """proofs/PROOF-*.md 작성. 디렉터리 자동 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def update_sol_frontmatter(
    sol_path: Path,
    phase: str,
    *,
    status: str,
    proof_record: str,
    measurements: dict[str, Any],
    scenario: str,
    pr_body_section: str | None = None,
) -> None:
    """SOL-NNNN.md 의 frontmatter 에 pre_sol_proof / post_sol_proof 객체 set/replace.

    body (frontmatter 이후) 는 보존. frontmatter append-only 규약 (CLAUDE.md) 의
    예외 — pre_sol_proof / post_sol_proof 는 schema 상 mutable trace.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; allowed: {VALID_STATUSES}")
    if phase not in ("pre", "post"):
        raise ValueError(f"phase must be 'pre' or 'post', got {phase!r}")

    text = sol_path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{sol_path}: frontmatter delimiter not found at start")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{sol_path}: closing frontmatter delimiter not found")
    fm_text = text[4:end]
    body = text[end + 5 :]

    fm = yaml.safe_load(fm_text) or {}
    key = "pre_sol_proof" if phase == "pre" else "post_sol_proof"
    proof_obj: dict[str, Any] = {
        "status": status,
        "proof_record": proof_record,
        "measurements": measurements,
        "scenario": scenario,
    }
    if phase == "post" and pr_body_section is not None:
        proof_obj["pr_body_section"] = pr_body_section
    fm[key] = proof_obj

    new_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    sol_path.write_text(f"---\n{new_fm}---\n{body}")


def update_cand_frontmatter_pre_proof(
    cand_path: Path,
    *,
    status: str,
    proof_record: str,
    measurements: dict[str, Any],
    scenario: str,
) -> None:
    """CAND-NNN.md 가 아직 SOL 로 변환 전인 경우 (pre-sol proof) CAND frontmatter 에 기록.

    SOL 로 변환되면 update_sol_frontmatter(phase='pre') 가 추가로 호출되며
    SOL frontmatter 가 source-of-truth 가 됨.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")

    text = cand_path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{cand_path}: frontmatter delimiter not found")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{cand_path}: closing frontmatter delimiter not found")
    fm_text = text[4:end]
    body = text[end + 5 :]

    fm = yaml.safe_load(fm_text) or {}
    fm["pre_sol_proof"] = {
        "status": status,
        "proof_record": proof_record,
        "measurements": measurements,
        "scenario": scenario,
    }
    new_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    cand_path.write_text(f"---\n{new_fm}---\n{body}")


def record_transition(
    item_id: str,
    *,
    from_state: str | None,
    to_state: str,
    actor: str,
    reason: str,
) -> None:
    """local-state/state.yaml + history.jsonl 에 전이 기록.

    item_id 는 CAND-NNN 또는 SOL-NNNN.
    to_state 는 proof 단계 전용 enum (state machine 에 새로 도입):
      - proof-pending-pre / proof-collected-pre / proof-unreproducible-pre / proof-blocked-pre
      - proof-pending-post / proof-collected-post / proof-unreproducible-post / proof-blocked-post

    local_state.apply_transition 은 모듈 상수 STATE_PATH/HISTORY_PATH 를 직접 쓰며
    내부에서 load/save 한다. from_state=None 이면 현재 상태 mismatch 검증 skip.
    """
    kind = "cand" if item_id.startswith("CAND-") else "sol"
    local_state.apply_transition(
        item_id=item_id,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        reason=reason,
        kind=kind,
    )


def proof_status_to_transition(phase: str, status: str) -> str:
    """proof status → state machine 전이 라벨.

    예: ('pre', 'collected') → 'proof-collected-pre'
    """
    if phase not in ("pre", "post"):
        raise ValueError(f"phase must be 'pre' or 'post', got {phase!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    short = {
        "pending": "pending",
        "collected": "collected",
        "unreproducible": "unreproducible",
        "blocked-external-dep": "blocked",
        "blocked-env": "blocked",
        "skipped-by-user": "skipped",
    }[status]
    return f"proof-{short}-{phase}"


# CLI for ad-hoc testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="state.py — proof state writer")
    ap.add_argument("--target", required=True, help="SOL-NNNN or CAND-NNN")
    ap.add_argument("--phase", required=True, choices=["pre", "post"])
    ap.add_argument("--status", required=True, choices=list(VALID_STATUSES))
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--proof-record", required=True, help="path to proofs/PROOF-*.md")
    ap.add_argument("--measurements-json", required=True, help="JSON string of measurements dict")
    args = ap.parse_args()

    measurements = json.loads(args.measurements_json)
    target = args.target
    if target.startswith("SOL-"):
        sol_path = AUDIT_ROOT / "solutions" / f"{target}.md"
        update_sol_frontmatter(
            sol_path,
            args.phase,
            status=args.status,
            proof_record=args.proof_record,
            measurements=measurements,
            scenario=args.scenario,
        )
    elif target.startswith("CAND-"):
        cand_path = AUDIT_ROOT / "issue-candidates" / f"{target}.md"
        if args.phase != "pre":
            sys.exit("ERROR: CAND target only supports phase=pre")
        update_cand_frontmatter_pre_proof(
            cand_path,
            status=args.status,
            proof_record=args.proof_record,
            measurements=measurements,
            scenario=args.scenario,
        )
    else:
        sys.exit(f"ERROR: target must be SOL-NNNN or CAND-NNN, got {target!r}")

    record_transition(
        target,
        from_state=None,
        to_state=proof_status_to_transition(args.phase, args.status),
        actor="real-behavior-proof",
        reason=f"scenario={args.scenario} measurements={measurements}",
    )
    print(f"OK: {target} {args.phase}={args.status} record={args.proof_record}")
