#!/usr/bin/env python3
"""
openclaw-audit — findings/drafts/ FIND 카드 게이트키핑.

사용:
  python validate.py <draft-path>
  python validate.py --all          # drafts/ 전체
  python validate.py --all --move   # 통과 → ready/, 반려 → rejected/

종료 코드:
  0: 전체 통과
  1: 1건 이상 반려
  2: 실행 오류

스키마: schema/finding.schema.yaml 와 동기화.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_state import (  # noqa: E402
    AUDIT_ROOT,
    DRAFTS_DIR,
    READY_DIR,
    REJECTED_DIR,
    OPENCLAW_ROOT,
    apply_transition,
    find_cell,
    find_domain,
    load_grid,
    parse_frontmatter,
    serialize_frontmatter,
)

# ────────────────────────────────────────────────────────────
# 스키마 상수 (schema/finding.schema.yaml 와 동기화)
# ────────────────────────────────────────────────────────────
ID_RE = re.compile(r"^FIND-[a-z][a-z0-9-]*-[0-9]{3}$")
CELL_RE = re.compile(r"^[a-z]+(-[a-z]+)+$")
RANGE_RE = re.compile(r"^([0-9]+)(?:-([0-9]+))?$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

STATUS_VALUES = {
    "draft", "discovered", "candidate", "gatekeeper-approved", "published", "rejected",
}
SEVERITY_VALUES = {"P0", "P1", "P2", "P3"}
SYMPTOM_VALUES = {
    "memory-leak", "lifecycle-gap", "concurrency-race",
    "error-boundary-gap", "shutdown-gap", "other",
}
IMPACT_VALUES = {
    "memory-growth", "crash", "hang", "data-loss", "wrong-output", "resource-exhaustion",
}

REQUIRED_FIELDS = [
    "id", "cell", "title", "file", "line_range", "evidence",
    "symptom_type", "problem", "mechanism", "root_cause_chain",
    "impact_hypothesis", "impact_detail", "severity",
    "counter_evidence", "status", "discovered_by", "discovered_at",
]

REQUIRED_SECTIONS = [
    "## 문제",
    "## 발현 메커니즘",
    "## 근본 원인 분석",
    "## 영향",
    "## 반증 탐색",
    "## Self-check",
]

REQUIRED_SELFCHECK_SUBS = [
    "### 내가 확실한 근거",
    "### 내가 한 가정",
    "### 확인 안 한 것 중 영향 가능성",
]


# ────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────
def glob_to_regex(pattern: str) -> re.Pattern:
    escaped = re.escape(pattern)
    regex = (
        escaped
        .replace(r"\*\*/", "(?:.*/)?")
        .replace(r"\*\*", ".*")
        .replace(r"\*", "[^/]*")
    )
    return re.compile(f"^{regex}$")


def glob_match(path_str: str, pattern: str) -> bool:
    return glob_to_regex(pattern).match(path_str) is not None


def normalize_code(text: str) -> str:
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            result.append(re.sub(r"\s+", " ", stripped))
    return "\n".join(result)


def extract_code_from_fence(evidence: str) -> str:
    m = re.search(r"```[a-zA-Z0-9]*\n(.*?)```", evidence, re.DOTALL)
    return m.group(1) if m else evidence


# ────────────────────────────────────────────────────────────
# 게이트 규칙
# ────────────────────────────────────────────────────────────
def check_b1_schema(fm: dict, errors: list[str]) -> None:
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"B-1-3: missing required field '{field}'")

    if "id" in fm and not ID_RE.match(str(fm["id"])):
        errors.append(f"B-1-3: id '{fm['id']}' does not match {ID_RE.pattern}")
    if "cell" in fm and not CELL_RE.match(str(fm["cell"])):
        errors.append(f"B-1-3: cell '{fm['cell']}' invalid pattern")
    if "severity" in fm and fm["severity"] not in SEVERITY_VALUES:
        errors.append(f"B-1-3: severity '{fm['severity']}' not in {sorted(SEVERITY_VALUES)}")
    if "status" in fm and fm["status"] not in STATUS_VALUES:
        errors.append(f"B-1-3: status '{fm['status']}' not in {sorted(STATUS_VALUES)}")
    if "symptom_type" in fm and fm["symptom_type"] not in SYMPTOM_VALUES:
        errors.append(f"B-1-3: symptom_type '{fm['symptom_type']}' not in {sorted(SYMPTOM_VALUES)}")
    if "impact_hypothesis" in fm and fm["impact_hypothesis"] not in IMPACT_VALUES:
        errors.append(f"B-1-3: impact_hypothesis '{fm['impact_hypothesis']}' not in {sorted(IMPACT_VALUES)}")
    if "discovered_at" in fm and not DATE_RE.match(str(fm["discovered_at"])):
        errors.append("B-1-3: discovered_at must be YYYY-MM-DD")
    if "title" in fm and len(str(fm["title"])) > 80:
        errors.append("B-1-3: title exceeds 80 chars")
    if "line_range" in fm and not RANGE_RE.match(str(fm["line_range"])):
        errors.append(f"B-1-3: line_range '{fm['line_range']}' invalid format")


def check_b1_path_scope(fm: dict, grid: dict, errors: list[str]) -> None:
    cell_id = fm.get("cell")
    file_path = fm.get("file")
    if not cell_id or not file_path:
        return
    cell_info = find_cell(grid, cell_id)
    if cell_info is None:
        errors.append(f"B-1-1: cell '{cell_id}' not found in grid.yaml")
        return
    domain = cell_info.get("domain")
    dom_info = find_domain(grid, domain) if domain else None
    if dom_info is None:
        errors.append(f"B-1-1: domain '{domain}' not found in grid.yaml")
        return
    allowed = dom_info.get("allowed_paths", [])
    if not any(glob_match(file_path, p) for p in allowed):
        errors.append(f"B-1-1: path '{file_path}' not in allowed_paths {allowed}")


def check_b1_evidence(fm: dict, errors: list[str]) -> None:
    """evidence 코드 블록이 openclaw repo 의 실제 파일 내용과 일치."""
    file_path = fm.get("file")
    line_range = fm.get("line_range")
    evidence = fm.get("evidence", "")
    if not file_path or not line_range:
        return
    abs_path = OPENCLAW_ROOT / file_path
    if not abs_path.exists():
        errors.append(f"B-1-2a: file '{file_path}' not found in openclaw repo ({OPENCLAW_ROOT})")
        return
    m = RANGE_RE.match(str(line_range))
    if not m:
        return  # 포맷 오류는 B-1-3 에서 잡힘
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    try:
        file_lines = abs_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        errors.append(f"B-1-2: file '{file_path}' not utf-8")
        return
    if end > len(file_lines) or start < 1 or start > end:
        errors.append(
            f"B-1-2b: line_range {line_range} out of bounds "
            f"(file has {len(file_lines)} lines)"
        )
        return
    actual = "\n".join(file_lines[start - 1:end])
    evidence_code = extract_code_from_fence(str(evidence))
    if normalize_code(actual) != normalize_code(evidence_code):
        errors.append(
            f"B-1-2c: evidence mismatch at {file_path}:{line_range} — "
            "whitespace or content differs"
        )


def check_b1_duplicate(fm: dict, path: Path, errors: list[str]) -> None:
    this_id = fm.get("id")
    this_cell = fm.get("cell")
    this_file = fm.get("file")
    this_range = fm.get("line_range")
    if not all([this_id, this_cell, this_file, this_range]):
        return
    m = RANGE_RE.match(str(this_range))
    if not m:
        return
    t_start = int(m.group(1))
    t_end = int(m.group(2)) if m.group(2) else t_start
    for scan_dir in [READY_DIR, DRAFTS_DIR]:
        if not scan_dir.exists():
            continue
        for md in scan_dir.glob("FIND-*.md"):
            if md.resolve() == path.resolve():
                continue
            try:
                other_fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not other_fm or other_fm.get("cell") != this_cell:
                continue
            if other_fm.get("file") != this_file:
                continue
            om = RANGE_RE.match(str(other_fm.get("line_range", "")))
            if not om:
                continue
            o_start = int(om.group(1))
            o_end = int(om.group(2)) if om.group(2) else o_start
            if t_start <= o_end and o_start <= t_end:
                errors.append(
                    f"B-1-4: duplicate with {other_fm.get('id')} — "
                    f"overlaps {this_file}:{this_range}"
                )
                return


def check_b1_root_cause(fm: dict, errors: list[str]) -> None:
    chain = fm.get("root_cause_chain")
    if not isinstance(chain, list):
        errors.append("B-1-5: root_cause_chain must be a list")
        return
    if len(chain) < 3:
        errors.append(f"B-1-5: root_cause_chain has {len(chain)} steps, minimum 3")
        return
    if len(chain) > 5:
        errors.append(f"B-1-5: root_cause_chain has {len(chain)} steps, maximum 5")
    concrete = 0
    for i, step in enumerate(chain):
        if not isinstance(step, dict):
            errors.append(f"B-1-5: step {i} not a dict")
            continue
        for key in ("why", "because", "evidence_ref"):
            if key not in step:
                errors.append(f"B-1-5: step {i} missing '{key}'")
        ref = str(step.get("evidence_ref", ""))
        if not ref.upper().startswith("N/A"):
            concrete += 1
    if concrete < 2:
        errors.append(
            f"B-1-5: only {concrete} concrete evidence_refs; minimum 2 required"
        )


def check_b1_sections(body: str, errors: list[str]) -> None:
    for section in REQUIRED_SECTIONS:
        if section not in body:
            errors.append(f"B-1-6: missing section '{section}'")
    for sub in REQUIRED_SELFCHECK_SUBS:
        if sub not in body:
            errors.append(f"B-1-6: missing Self-check subsection '{sub}'")


def check_b1_counter_evidence(fm: dict, errors: list[str]) -> None:
    ce = fm.get("counter_evidence")
    if not isinstance(ce, dict):
        errors.append("B-1-7: counter_evidence must be an object")
        return
    reason = ce.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("B-1-7: counter_evidence.reason is empty")


# ────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────
def validate_file(path: Path, grid: dict) -> list[str]:
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"IO: {e}"]
    fm, body = parse_frontmatter(text)
    if fm is None:
        return ["B-1-3: frontmatter missing or --- markers broken"]
    if "_parse_error" in fm:
        return [f"B-1-3: frontmatter YAML error: {fm['_parse_error']}"]
    check_b1_schema(fm, errors)
    check_b1_path_scope(fm, grid, errors)
    check_b1_evidence(fm, errors)
    check_b1_duplicate(fm, path, errors)
    check_b1_root_cause(fm, errors)
    check_b1_sections(body, errors)
    check_b1_counter_evidence(fm, errors)
    return errors


def move_with_reasons(path: Path, errors: list[str], target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        shutil.move(str(path), str(target_dir / path.name))
        return
    fm["status"] = "rejected"
    fm["rejected_reasons"] = errors
    (target_dir / path.name).write_text(serialize_frontmatter(fm, body), encoding="utf-8")
    path.unlink()
    find_id = fm.get("id", path.stem)
    try:
        apply_transition(
            find_id,
            from_state=None,
            to_state="rejected",
            actor="validate",
            reason=f"gate rejection: {'; '.join(errors[:3])}",
            kind="find",
            extras={"errors": errors},
        )
    except RuntimeError:
        pass  # state already tracked elsewhere


def promote(path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        shutil.move(str(path), str(target_dir / path.name))
        return
    fm["status"] = "discovered"
    (target_dir / path.name).write_text(serialize_frontmatter(fm, body), encoding="utf-8")
    path.unlink()
    find_id = fm.get("id", path.stem)
    try:
        apply_transition(
            find_id,
            from_state=None,
            to_state="discovered",
            actor="validate",
            reason="validate passed",
            kind="find",
        )
    except RuntimeError:
        pass


def main() -> None:
    if not DRAFTS_DIR.exists():
        print(f"[ERROR] drafts dir not found: {DRAFTS_DIR}", file=sys.stderr)
        sys.exit(2)
    try:
        grid = load_grid()
    except Exception as e:
        print(f"[ERROR] grid.yaml load: {e}", file=sys.stderr)
        sys.exit(2)

    args = sys.argv[1:]
    move = "--move" in args
    args = [a for a in args if a != "--move"]

    if not args:
        print(__doc__)
        sys.exit(2)

    if args[0] == "--all":
        targets = sorted(DRAFTS_DIR.glob("FIND-*.md"))
    else:
        targets = [Path(args[0])]

    if not targets:
        print("[INFO] no drafts to validate")
        sys.exit(0)

    total_pass = 0
    total_fail = 0
    for path in targets:
        errors = validate_file(path, grid)
        if errors:
            total_fail += 1
            print(f"[REJECT] {path.name}")
            for e in errors:
                print(f"  - {e}")
            if move:
                move_with_reasons(path, errors, REJECTED_DIR)
                print(f"  → moved to {REJECTED_DIR.relative_to(AUDIT_ROOT)}/")
        else:
            total_pass += 1
            print(f"[PASS]   {path.name}")
            if move:
                promote(path, READY_DIR)
                print(f"  → promoted to {READY_DIR.relative_to(AUDIT_ROOT)}/")

    print(f"\n총 {total_pass + total_fail} 건: {total_pass} 통과 / {total_fail} 반려")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
