#!/usr/bin/env python3
"""
openclaw-audit — Solution Gatekeeper 로컬 파이프라인.

원본 pethroom 파이프라인의 gatekeep.py 를 단순화:
- 입력 소스: CAND 파일 (GH Issue body 아님)
- FSM: local-state/state.yaml (GH 라벨 전이 없음)

하위 명령:
  sanitize <CAND-ID>
    CAND + 포함된 FIND 들의 본문을 Gatekeeper 입력으로 가공 (PERF-ID / severity 숨김).
    stdout 에 JSON { sanitized_body, evidence_paths_whitelist } 출력.

  apply <CAND-ID> --verdict-json <path> [--shadow]
    Gatekeeper 반환 JSON 을 검증 + local-state 전이.
    - shadow: 판정만 기록 (metrics/shadow-runs.jsonl), 상태 유지.
    - 정식(apply): gatekeeper-approved | needs-human-review 전이.

  record-shadow <CAND-ID> --verdict-json <path>
    이미 존재하는 verdict 파일을 shadow 메트릭에 추가.

  record-human <CAND-ID> --verdict ... --confidence ... [--severity ...] [--notes ...]
    사람 판정 기록 (calibration).

  record-consistency <CAND-ID> --verdict-a <path> --verdict-b <path>
    같은 CAND 2회 실행 self-consistency.

  drafter-gate <CAND-ID>
    Drafter 진입 직전 결정적 재검증 (code drift 감지).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_state import (  # noqa: E402
    AUDIT_ROOT,
    CANDIDATES_DIR,
    METRICS_DIR,
    OPENCLAW_ROOT,
    READY_DIR,
    apply_transition,
    get_item,
    now_iso,
    parse_frontmatter,
    read_md,
)

# Gatekeeper 출력 스키마
REQUIRED_VERDICT_FIELDS = [
    "verdict", "confidence", "rationale",
    "counter_evidence", "evidence_paths",
    "suggested_verifier_rules", "explored_categories",
]
ALLOWED_VERDICTS = {"approve", "uncertain", "reject_suspected"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
FORBIDDEN_VERDICTS = {"invalid", "wontfix"}


# ────────────────────────────────────────────────────────────
# 입력 sanitize
# ────────────────────────────────────────────────────────────
def _read_find(find_id: str) -> tuple[dict | None, str]:
    return read_md(READY_DIR / f"{find_id}.md")


def _read_cand(cand_id: str) -> tuple[dict | None, str]:
    return read_md(CANDIDATES_DIR / f"{cand_id}.md")


def build_gatekeeper_input(cand_id: str) -> dict:
    """
    CAND + 연결된 FIND 들을 Gatekeeper 입력 JSON 으로.
    PERF-ID, severity, status 등은 제거해서 appeal-to-authority 편향 차단.
    """
    cand_fm, cand_body = _read_cand(cand_id)
    if cand_fm is None:
        raise RuntimeError(f"CAND not found: {cand_id}")

    find_ids = cand_fm.get("finding_ids") or cand_fm.get("perf_refs") or []
    finds = []
    evidence_whitelist: list[str] = []
    for fid in find_ids:
        fm, body = _read_find(fid)
        if fm is None:
            continue
        file = fm.get("file", "")
        rng = fm.get("line_range", "")
        if file and rng:
            evidence_whitelist.append(f"{file}:{rng}")
        finds.append({
            "title": fm.get("title"),
            "file": file,
            "line_range": rng,
            "evidence": fm.get("evidence"),
            "problem": fm.get("problem"),
            "mechanism": fm.get("mechanism"),
            "root_cause_chain": fm.get("root_cause_chain"),
            "impact_hypothesis": fm.get("impact_hypothesis"),
            "impact_detail": fm.get("impact_detail"),
            "body": body,
        })

    return {
        "cand_id": cand_id,
        "cluster_rationale": cand_fm.get("cluster_rationale"),
        "proposed_title": cand_fm.get("proposed_title"),
        "findings": finds,
        "evidence_paths_whitelist": evidence_whitelist,
    }


def cmd_sanitize(args: argparse.Namespace) -> None:
    payload = build_gatekeeper_input(args.cand_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ────────────────────────────────────────────────────────────
# Verdict 검증
# ────────────────────────────────────────────────────────────
def parse_path_line(s: str) -> tuple[str, int, int] | None:
    m = re.match(r"^(.+?):([0-9]+)(?:-([0-9]+))?$", s)
    if not m:
        return None
    return (m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else int(m.group(2)))


def path_line_in_whitelist(claim: str, whitelist: list[str]) -> bool:
    c = parse_path_line(claim)
    if c is None:
        return False
    c_path, c_s, c_e = c
    for wl in whitelist:
        w = parse_path_line(wl)
        if w is None:
            continue
        w_path, w_s, w_e = w
        if c_path == w_path and c_s >= w_s and c_e <= w_e:
            return True
    return False


def validate_verdict_schema(v: dict) -> list[str]:
    errors: list[str] = []
    for f in REQUIRED_VERDICT_FIELDS:
        if f not in v:
            errors.append(f"missing field: {f}")
    if errors:
        return errors

    verdict = v.get("verdict")
    if verdict in FORBIDDEN_VERDICTS:
        errors.append(f"verdict '{verdict}' forbidden (no invalid/wontfix authority)")
    elif verdict not in ALLOWED_VERDICTS:
        errors.append(f"verdict '{verdict}' not in {sorted(ALLOWED_VERDICTS)}")

    c = v.get("confidence")
    if c not in ALLOWED_CONFIDENCE:
        errors.append(f"confidence '{c}' not in {sorted(ALLOWED_CONFIDENCE)}")

    ce = v.get("counter_evidence")
    if not isinstance(ce, dict):
        errors.append("counter_evidence must be an object")
    else:
        for k in ("path", "line", "reason"):
            if k not in ce:
                errors.append(f"counter_evidence.{k} missing")
        if isinstance(ce.get("reason"), str) and not ce["reason"].strip():
            errors.append("counter_evidence.reason empty")

    ep = v.get("evidence_paths")
    if not isinstance(ep, list) or not ep:
        errors.append("evidence_paths must be non-empty list")

    ec = v.get("explored_categories")
    if not isinstance(ec, list) or len(ec) < 3:
        cnt = len(ec) if isinstance(ec, list) else "non-list"
        errors.append(f"explored_categories must be list of 3+ items (got {cnt})")

    rat = v.get("rationale")
    if not isinstance(rat, str) or len(rat.strip()) < 30:
        errors.append("rationale too short (< 30 chars)")

    return errors


def validate_grounding(v: dict, whitelist: list[str]) -> list[str]:
    errors: list[str] = []
    for p in v.get("evidence_paths", []):
        if not path_line_in_whitelist(p, whitelist):
            errors.append(f"evidence_path '{p}' not in FIND evidence whitelist")
    return errors


def compute_evidence_fingerprint(evidence_paths: list[str]) -> dict:
    files = []
    for ep in evidence_paths:
        p = parse_path_line(ep)
        if p is None:
            continue
        path, start, end = p
        abs_path = OPENCLAW_ROOT / path
        if not abs_path.exists():
            files.append({"path": path, "line_range": f"{start}-{end}", "sha": "FILE_MISSING"})
            continue
        try:
            lines = abs_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            files.append({"path": path, "line_range": f"{start}-{end}", "sha": "NOT_UTF8"})
            continue
        if end > len(lines):
            files.append({"path": path, "line_range": f"{start}-{end}", "sha": "OUT_OF_BOUNDS"})
            continue
        snippet = "\n".join(lines[start - 1:end])
        h = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
        files.append({"path": path, "line_range": f"{start}-{end}", "sha": f"sha256:{h[:16]}"})
    return {"files": files, "computed_at": now_iso()}


# ────────────────────────────────────────────────────────────
# Apply
# ────────────────────────────────────────────────────────────
def cmd_apply(args: argparse.Namespace) -> None:
    verdict_path = Path(args.verdict_json)
    if not verdict_path.exists():
        print(f"[ERROR] verdict JSON not found: {verdict_path}", file=sys.stderr)
        sys.exit(2)
    try:
        v = json.loads(verdict_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] verdict JSON parse: {e}", file=sys.stderr)
        sys.exit(2)

    payload = build_gatekeeper_input(args.cand_id)
    whitelist = payload["evidence_paths_whitelist"]

    schema_errors = validate_verdict_schema(v)
    grounding_errors = [] if schema_errors else validate_grounding(v, whitelist)
    all_errors = schema_errors + grounding_errors

    verdict = v.get("verdict")
    confidence = v.get("confidence")

    if all_errors:
        decision = "needs-human-review"
        reason = f"gatekeeper output invalid: {'; '.join(all_errors[:5])}"
    elif verdict == "approve" and confidence == "high":
        decision = "gatekeeper-approved"
        reason = f"approve@high: {v['rationale'][:200]}"
    elif verdict == "approve" and confidence == "medium":
        decision = "needs-human-review"
        reason = "approve@medium — defer to human (retry policy TBD)"
    else:
        decision = "needs-human-review"
        reason = f"verdict={verdict} confidence={confidence}"

    print(f"\n── {args.cand_id}")
    print(f"  verdict:    {verdict} (confidence={confidence})")
    print(f"  decision:   {decision}")
    print(f"  schema:     {'OK' if not schema_errors else 'FAIL'}")
    for e in schema_errors:
        print(f"    · {e}")
    print(f"  grounding:  {'OK' if not grounding_errors else 'FAIL'}")
    for e in grounding_errors:
        print(f"    · {e}")
    print(f"  reason:     {reason}")

    if args.shadow:
        print("  [SHADOW] 상태 전이 없음. 기록만.")
        _record_shadow_entry(args.cand_id, v, decision, schema_errors, grounding_errors, whitelist, payload)
        return

    fingerprint = compute_evidence_fingerprint(v.get("evidence_paths", []))
    extras = {
        "gatekeeper": {
            **v,
            "decision": decision,
            "reason": reason,
            "at": now_iso(),
        },
        "evidence_fingerprint": fingerprint,
    }
    try:
        apply_transition(
            args.cand_id,
            from_state=None,
            to_state=decision,
            actor="solution-gatekeeper",
            reason=reason[:500],
            kind="candidate",
            extras=extras,
        )
        print(f"  [APPLY] state → {decision}")
    except RuntimeError as e:
        print(f"  [ERROR] transition: {e}", file=sys.stderr)
        sys.exit(1)


# ────────────────────────────────────────────────────────────
# 메트릭 기록
# ────────────────────────────────────────────────────────────
def _record_shadow_entry(
    cand_id: str, verdict: dict, decision: str,
    schema_errors: list[str], grounding_errors: list[str],
    whitelist: list[str], payload: dict,
) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    ce = verdict.get("counter_evidence", {})
    ce_found = bool(ce.get("path"))
    ce_concrete = ce_found and not str(ce.get("reason", "")).lower().startswith("none_found")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    record = {
        "run_id": f"shadow-{cand_id}-{now_ms}",
        "cand_id": cand_id,
        "at": now_iso(),
        "verdict": verdict.get("verdict"),
        "confidence": verdict.get("confidence"),
        "decision": decision,
        "schema_pass": not schema_errors,
        "grounding_pass": not grounding_errors,
        "explored_categories_count": len(verdict.get("explored_categories", [])),
        "suggested_rules_count": len(verdict.get("suggested_verifier_rules", [])),
        "evidence_paths_count": len(verdict.get("evidence_paths", [])),
        "counter_evidence_found": ce_found,
        "counter_evidence_concrete": ce_concrete,
        "findings_count": len(payload["findings"]),
    }
    with (METRICS_DIR / "shadow-runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  [REC] shadow-runs.jsonl += {record['run_id']}")

    rules_path = METRICS_DIR / "verifier-rule-candidates.jsonl"
    with rules_path.open("a", encoding="utf-8") as f:
        for rule in verdict.get("suggested_verifier_rules", []):
            f.write(json.dumps({
                "cand_id": cand_id,
                "rule_template": rule.get("template"),
                "slot_values": rule.get("slot_values", {}),
                "source_run_id": record["run_id"],
                "at": record["at"],
            }, ensure_ascii=False) + "\n")


def cmd_record_shadow(args: argparse.Namespace) -> None:
    v = json.loads(Path(args.verdict_json).read_text(encoding="utf-8"))
    payload = build_gatekeeper_input(args.cand_id)
    schema_errors = validate_verdict_schema(v)
    grounding_errors = [] if schema_errors else validate_grounding(v, payload["evidence_paths_whitelist"])

    if schema_errors or grounding_errors:
        decision = "needs-human-review"
    elif v.get("verdict") == "approve" and v.get("confidence") == "high":
        decision = "gatekeeper-approved"
    else:
        decision = "needs-human-review"

    _record_shadow_entry(args.cand_id, v, decision, schema_errors, grounding_errors,
                         payload["evidence_paths_whitelist"], payload)


def cmd_record_human(args: argparse.Namespace) -> None:
    item = get_item(args.cand_id) or {}
    gk = (item.get("extras") or {}).get("gatekeeper", {})
    gk_v = gk.get("verdict")
    gk_c = gk.get("confidence")

    record = {
        "cand_id": args.cand_id,
        "human_verdict": args.verdict,
        "human_confidence": args.confidence,
        "human_severity": args.severity,
        "human_notes": args.notes,
        "gatekeeper_verdict_at_time": gk_v,
        "gatekeeper_confidence_at_time": gk_c,
        "match_verdict": (args.verdict == gk_v) if gk_v else None,
        "match_confidence": (args.confidence == gk_c) if gk_c else None,
        "recorded_at": now_iso(),
    }
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with (METRICS_DIR / "human-verdicts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[REC] human-verdicts.jsonl += {args.cand_id}")
    print(f"      human: {args.verdict}/{args.confidence}  match_verdict={record['match_verdict']}")


def cmd_record_consistency(args: argparse.Namespace) -> None:
    va = json.loads(Path(args.verdict_a).read_text(encoding="utf-8"))
    vb = json.loads(Path(args.verdict_b).read_text(encoding="utf-8"))

    def tokens(s: str) -> set[str]:
        return set(re.findall(r"\w+", str(s).lower()))

    ra, rb = tokens(va.get("rationale", "")), tokens(vb.get("rationale", ""))
    jaccard = (len(ra & rb) / len(ra | rb)) if (ra | rb) else 0.0

    ea, eb = set(va.get("evidence_paths", [])), set(vb.get("evidence_paths", []))
    ev_jaccard = (len(ea & eb) / len(ea | eb)) if (ea | eb) else 0.0

    record = {
        "cand_id": args.cand_id,
        "run_a_verdict": va.get("verdict"),
        "run_b_verdict": vb.get("verdict"),
        "run_a_confidence": va.get("confidence"),
        "run_b_confidence": vb.get("confidence"),
        "verdict_match": va.get("verdict") == vb.get("verdict"),
        "confidence_match": va.get("confidence") == vb.get("confidence"),
        "rationale_jaccard": round(jaccard, 3),
        "evidence_paths_jaccard": round(ev_jaccard, 3),
        "at": now_iso(),
    }
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with (METRICS_DIR / "self-consistency.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[REC] self-consistency.jsonl += {args.cand_id}")
    print(f"      verdict_match: {record['verdict_match']}  rationale_jaccard={record['rationale_jaccard']}")


def cmd_drafter_gate(args: argparse.Namespace) -> None:
    item = get_item(args.cand_id)
    if item is None:
        print(f"[FAIL] G-0: {args.cand_id} not in local-state", file=sys.stderr)
        sys.exit(1)

    extras = item.get("extras", {})
    gk = extras.get("gatekeeper", {})
    stored_fp = extras.get("evidence_fingerprint")

    failures: list[str] = []

    if item.get("state") != "gatekeeper-approved":
        failures.append(f"G-1: state is '{item.get('state')}', expected 'gatekeeper-approved'")

    if not stored_fp or not stored_fp.get("files"):
        failures.append("G-3: evidence_fingerprint missing or empty")

    gk_paths = gk.get("evidence_paths", [])
    if not gk_paths:
        failures.append("G-4: gatekeeper.evidence_paths missing")

    if stored_fp and stored_fp.get("files") and gk_paths:
        current = compute_evidence_fingerprint(gk_paths)
        stored_map = {f"{f['path']}:{f['line_range']}": f["sha"] for f in stored_fp["files"]}
        current_map = {f"{f['path']}:{f['line_range']}": f["sha"] for f in current["files"]}
        for k in stored_map:
            if k not in current_map:
                failures.append(f"G-5: {k} missing in current")
                continue
            if stored_map[k] != current_map[k]:
                failures.append(f"G-5: drift at {k} — stored={stored_map[k]}, current={current_map[k]}")

    print(f"── {args.cand_id}")
    if not failures:
        print("  G-1 state == gatekeeper-approved : pass")
        print("  G-3 fingerprint stored           : pass")
        print("  G-4 evidence_paths present       : pass")
        print("  G-5 fingerprint recompute        : pass")
        print("  DRAFTER_GATE_PASS")
        sys.exit(0)

    print("  FAIL:")
    for f in failures:
        print(f"    · {f}")
    if args.apply:
        apply_transition(
            args.cand_id,
            from_state=None,
            to_state="needs-human-review",
            actor="drafter-gate",
            reason=f"drift: {'; '.join(failures[:3])}",
            kind="candidate",
        )
        print("  → gatekeeper-approved → needs-human-review")
    sys.exit(1)


# ────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("sanitize", help="CAND → gatekeeper 입력 JSON")
    p1.add_argument("cand_id")

    p2 = sub.add_parser("apply", help="verdict JSON 적용")
    p2.add_argument("cand_id")
    p2.add_argument("--verdict-json", required=True)
    p2.add_argument("--shadow", action="store_true")

    p3 = sub.add_parser("record-shadow", help="verdict → shadow-runs.jsonl")
    p3.add_argument("cand_id")
    p3.add_argument("--verdict-json", required=True)

    p4 = sub.add_parser("record-human", help="사람 판정 기록")
    p4.add_argument("cand_id")
    p4.add_argument("--verdict", required=True, choices=["approve", "uncertain", "reject_suspected"])
    p4.add_argument("--confidence", required=True, choices=["low", "medium", "high"])
    p4.add_argument("--severity", default=None)
    p4.add_argument("--notes", default="")

    p5 = sub.add_parser("record-consistency", help="동일 CAND 2회 self-consistency")
    p5.add_argument("cand_id")
    p5.add_argument("--verdict-a", required=True)
    p5.add_argument("--verdict-b", required=True)

    p6 = sub.add_parser("drafter-gate", help="drafter 진입 직전 drift 체크")
    p6.add_argument("cand_id")
    p6.add_argument("--apply", action="store_true")

    args = p.parse_args()
    {
        "sanitize": cmd_sanitize,
        "apply": cmd_apply,
        "record-shadow": cmd_record_shadow,
        "record-human": cmd_record_human,
        "record-consistency": cmd_record_consistency,
        "drafter-gate": cmd_drafter_gate,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
