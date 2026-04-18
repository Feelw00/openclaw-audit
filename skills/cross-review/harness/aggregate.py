#!/usr/bin/env python3
"""
skills/cross-review/harness/aggregate.py

입력: target + mode (agent 들이 /tmp/cross-review-<role>-<target>.json 에 저장한 JSON 파일들)
출력: consensus JSON + 매트릭스 마크다운
부작용: metrics/cross-review-<target>-<YYYYMMDD-HHMMSS>.jsonl 에 영구 기록
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: pyyaml 필요. `pip install pyyaml`\n")
    sys.exit(2)

SKILL_ROOT = Path(__file__).resolve().parent.parent
AUDIT_ROOT = SKILL_ROOT.parent.parent
MODES_DIR = SKILL_ROOT / "modes"
METRICS_DIR = AUDIT_ROOT / "metrics"
TMP_DIR = Path("/tmp")

VALID_VERDICTS = {
    "real-problem-real-fix",
    "real-problem-fix-insufficient",
    "synthetic-only",
    "false-positive",
    "upstream-duplicate",
}


def load_mode(mode: str) -> dict:
    path = MODES_DIR / f"{mode}.yaml"
    if not path.exists():
        sys.exit(f"ERROR: mode '{mode}' 없음")
    return yaml.safe_load(path.read_text())


def _sanitize_target(target: str) -> str:
    """file path / PR#123 → 파일명 safe 변환"""
    return target.replace("/", "_").replace("#", "").replace(" ", "_")


def load_agent_verdicts(target: str, roles: list[str] | None = None) -> list[dict]:
    """각 role 의 /tmp/cross-review-<role>-<target>.json 을 읽어 리스트로 반환."""
    safe = _sanitize_target(target)
    verdicts: list[dict] = []

    if roles:
        candidates = [f"cross-review-{r}-{safe}.json" for r in roles]
    else:
        # glob 으로 전부 수집
        candidates = [p.name for p in TMP_DIR.glob(f"cross-review-*-{safe}.json")]

    for fname in candidates:
        path = TMP_DIR / fname
        if not path.exists():
            sys.stderr.write(f"[skip] {path} 없음\n")
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[skip] {path} JSON 파싱 실패: {e}\n")
            continue
        if "role" not in data or "verdict" not in data:
            sys.stderr.write(f"[skip] {path} 스키마 위반 (role / verdict 누락)\n")
            continue
        if data["verdict"] not in VALID_VERDICTS:
            sys.stderr.write(f"[skip] {path} 잘못된 verdict: {data['verdict']}\n")
            continue
        verdicts.append(data)

    return verdicts


def compute_consensus(verdicts: list[dict], mode_cfg: dict) -> dict:
    counts = {v: 0 for v in VALID_VERDICTS}
    for v in verdicts:
        counts[v["verdict"]] += 1

    real_count = counts["real-problem-real-fix"]
    fix_insufficient_count = counts["real-problem-fix-insufficient"]
    synthetic_count = counts["synthetic-only"]
    false_positive_count = counts["false-positive"]
    upstream_duplicate_count = counts["upstream-duplicate"]

    # 합의 규칙 평가 (YAML 의 condition 문자열 → Python eval, 제한된 namespace)
    rules = mode_cfg.get("consensus_rules", {})
    namespace = {
        "real_count": real_count,
        "fix_insufficient_count": fix_insufficient_count,
        "synthetic_count": synthetic_count,
        "false_positive_count": false_positive_count,
        "upstream_duplicate_count": upstream_duplicate_count,
    }

    decisions = []
    for name, rule in rules.items():
        cond = rule.get("condition", "").replace(" AND ", " and ").replace(" OR ", " or ")
        if cond == "ALWAYS":
            # 무조건 동반 action (예: tone checklist)
            decisions.append({"rule": name, "condition": "ALWAYS", "action": rule.get("action", "")})
            continue
        try:
            if eval(cond, {"__builtins__": {}}, namespace):
                decisions.append({"rule": name, "condition": cond, "action": rule.get("action", "")})
        except Exception as e:
            decisions.append({"rule": name, "error": f"condition eval 실패: {e}", "condition": cond})

    # primary decision: 가장 먼저 매치된 non-ALWAYS rule
    primary = next((d for d in decisions if d.get("condition") != "ALWAYS"), None)

    return {
        "counts": counts,
        "summary": {
            "real_count": real_count,
            "fix_insufficient_count": fix_insufficient_count,
            "synthetic_count": synthetic_count,
            "false_positive_count": false_positive_count,
            "upstream_duplicate_count": upstream_duplicate_count,
        },
        "matched_rules": decisions,
        "primary_decision": primary,
    }


def render_matrix(verdicts: list[dict]) -> str:
    rows = [
        "| # | Role | Verdict | Confidence | Summary |",
        "|---|---|---|---|---|",
    ]
    for i, v in enumerate(verdicts, 1):
        summary = (v.get("summary") or "").replace("\n", " ")[:120]
        rows.append(
            f"| {i} | {v['role']} | {v['verdict']} | {v.get('confidence', '?')} | {summary}... |"
        )
    return "\n".join(rows)


def persist_to_metrics(target: str, mode: str, verdicts: list[dict], consensus: dict) -> Path:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = _sanitize_target(target)
    path = METRICS_DIR / f"cross-review-{safe}-{stamp}.jsonl"
    with path.open("w") as f:
        # 첫 줄 = 메타 + consensus
        meta = {
            "_meta": True,
            "target": target,
            "mode": mode,
            "timestamp": dt.datetime.now().isoformat(),
            "agent_count": len(verdicts),
            "consensus": consensus,
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        # 이후 줄들 = 각 agent verdict
        for v in verdicts:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="cross-review 결과 집계")
    ap.add_argument("--target", required=True)
    ap.add_argument("--mode", required=True, choices=["post-harness", "pre-pr", "maintainer-response"])
    ap.add_argument("--roles", help="comma-separated. 생략 시 /tmp/ glob")
    ap.add_argument("--no-persist", action="store_true", help="metrics 파일 저장 안 함 (dry-run)")
    args = ap.parse_args()

    mode_cfg = load_mode(args.mode)
    roles = [r.strip() for r in args.roles.split(",")] if args.roles else None
    verdicts = load_agent_verdicts(args.target, roles)

    if not verdicts:
        sys.exit("ERROR: agent verdict JSON 파일을 하나도 찾지 못했습니다.")

    min_agents = mode_cfg.get("min_agents", 3)
    if len(verdicts) < min_agents:
        sys.stderr.write(
            f"WARN: {args.mode} 는 min_agents={min_agents} 필요 (현재 {len(verdicts)}). 결과는 잠정적.\n"
        )

    consensus = compute_consensus(verdicts, mode_cfg)
    matrix = render_matrix(verdicts)

    result = {
        "target": args.target,
        "mode": args.mode,
        "agent_count": len(verdicts),
        "verdicts": verdicts,
        "consensus": consensus,
        "matrix_markdown": matrix,
    }

    if not args.no_persist:
        path = persist_to_metrics(args.target, args.mode, verdicts, consensus)
        result["metrics_file"] = str(path)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
