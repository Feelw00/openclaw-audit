#!/usr/bin/env python3
"""
openclaw-audit — 로컬 FSM 상태 관리.

원본 pethroomcare 파이프라인은 GH Issue body 의 JSON 앵커 + 라벨로 FSM 을 구동했지만,
openclaw 는 남의 OSS 라 커스텀 라벨을 걸 수 없다. 그래서 모든 상태를 local-state/ 에만 둔다.

저장소 레이아웃:
  local-state/state.yaml    — 현재 상태 (finding/candidate/solution 단위)
  local-state/history.jsonl — 모든 상태 전이 기록 (append-only)

상태 transitions (논리 FSM):
  FIND:   draft → discovered | rejected
          discovered → candidate | orphan
  CAND:   pending → gatekeeper-approved | needs-human-review
          gatekeeper-approved → solution-drafted
          solution-drafted → published | abandoned
  ISSUE:  published (issue_number, pr_number 연결)

이 모듈은 공통 유틸만 제공. 비즈니스 로직은 각 명령 스크립트에서.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("[ERROR] pyyaml required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


# ────────────────────────────────────────────────────────────
# 경로
# ────────────────────────────────────────────────────────────
def _find_audit_root(start: Path) -> Path:
    """openclaw-audit/ 루트 탐색. grid.yaml 존재로 식별."""
    cur = start.resolve()
    while cur != cur.parent:
        if (cur / "grid.yaml").exists() and (cur / "local-state").exists():
            return cur
        cur = cur.parent
    raise RuntimeError("openclaw-audit root not found (looking for grid.yaml + local-state/)")


SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_ROOT = _find_audit_root(SCRIPT_DIR)

# openclaw repo (증거 파일 해석용)
OPENCLAW_ROOT = Path(
    os.environ.get("OPENCLAW_ROOT", "/Users/lucas/Project/openclaw")
).resolve()

GRID_PATH = AUDIT_ROOT / "grid.yaml"
DRAFTS_DIR = AUDIT_ROOT / "findings" / "drafts"
READY_DIR = AUDIT_ROOT / "findings" / "ready"
REJECTED_DIR = AUDIT_ROOT / "findings" / "rejected"
CANDIDATES_DIR = AUDIT_ROOT / "issue-candidates"
SOLUTIONS_DIR = AUDIT_ROOT / "solutions"
METRICS_DIR = AUDIT_ROOT / "metrics"
STATE_DIR = AUDIT_ROOT / "local-state"
STATE_PATH = STATE_DIR / "state.yaml"
HISTORY_PATH = STATE_DIR / "history.jsonl"
SCHEMA_DIR = AUDIT_ROOT / "schema"


# ────────────────────────────────────────────────────────────
# 시간 유틸
# ────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ────────────────────────────────────────────────────────────
# Frontmatter 파싱
# ────────────────────────────────────────────────────────────
def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """`---\\n...\\n---\\n` YAML frontmatter 와 본문 분리."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    fm_text = text[4:end]
    body = text[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
        return fm, body
    except yaml.YAMLError as e:
        return {"_parse_error": str(e)}, body


def serialize_frontmatter(fm: dict, body: str) -> str:
    fm_text = yaml.dump(fm, allow_unicode=True, sort_keys=False)
    return f"---\n{fm_text}---\n{body}"


# ────────────────────────────────────────────────────────────
# State 파일 R/W
# ────────────────────────────────────────────────────────────
def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "items": {}}
    with STATE_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("version", 1)
    data.setdefault("items", {})
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(state, f, allow_unicode=True, sort_keys=False)
    tmp.replace(STATE_PATH)


def append_history(entry: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def apply_transition(
    item_id: str,
    from_state: str | None,
    to_state: str,
    actor: str,
    reason: str,
    kind: str = "find",
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    로컬 FSM 전이. 원본의 tag.apply_transition 대체.

    - GH 라벨·Issue body 앵커 조작 없음.
    - state.yaml items[item_id] 갱신 + history.jsonl append.
    - 반환: 갱신된 item dict.
    """
    state = load_state()
    items = state["items"]
    cur = items.get(item_id, {"kind": kind, "state": None, "transitions": []})
    prev = cur.get("state")
    if from_state is not None and prev != from_state:
        raise RuntimeError(
            f"state mismatch for {item_id}: expected from={from_state}, actual={prev}"
        )
    ts = now_iso()
    trans = {
        "from": prev,
        "to": to_state,
        "actor": actor,
        "reason": reason,
        "at": ts,
    }
    cur["kind"] = kind
    cur["state"] = to_state
    cur.setdefault("created_at", ts)
    cur["updated_at"] = ts
    cur.setdefault("transitions", []).append(trans)
    if extras:
        cur.setdefault("extras", {}).update(extras)
    items[item_id] = cur
    save_state(state)
    append_history({"item_id": item_id, **trans, "extras": extras or {}})
    return cur


def get_item(item_id: str) -> dict | None:
    return load_state()["items"].get(item_id)


# ────────────────────────────────────────────────────────────
# FIND / CAND / SOL 파일 조회 유틸
# ────────────────────────────────────────────────────────────
def read_md(path: Path) -> tuple[dict | None, str]:
    if not path.exists():
        return None, ""
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def find_path(find_id: str) -> Path | None:
    """FIND-xxx 의 현재 위치 찾기 (ready > drafts > rejected 순)."""
    for d in [READY_DIR, DRAFTS_DIR, REJECTED_DIR]:
        p = d / f"{find_id}.md"
        if p.exists():
            return p
    return None


def cand_path(cand_id: str) -> Path:
    return CANDIDATES_DIR / f"{cand_id}.md"


def sol_path(sol_id: str) -> Path:
    return SOLUTIONS_DIR / f"{sol_id}.md"


# ────────────────────────────────────────────────────────────
# openclaw 파일 접근
# ────────────────────────────────────────────────────────────
def openclaw_file(rel_path: str) -> Path:
    """openclaw repo 내 상대경로 → 절대경로."""
    return OPENCLAW_ROOT / rel_path


def read_openclaw_lines(rel_path: str, start: int, end: int) -> list[str] | None:
    p = openclaw_file(rel_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    if start < 1 or end > len(lines) or start > end:
        return None
    return lines[start - 1:end]


# ────────────────────────────────────────────────────────────
# Grid 로드
# ────────────────────────────────────────────────────────────
def load_grid() -> dict[str, Any]:
    with GRID_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_cell(grid: dict, cell_id: str) -> dict | None:
    for c in grid.get("cells", []):
        if c["id"] == cell_id:
            return c
    return None


def find_domain(grid: dict, domain_id: str) -> dict | None:
    for d in grid.get("domains", []):
        if d["id"] == domain_id:
            return d
    return None


# ────────────────────────────────────────────────────────────
# CLI (디버그용)
# ────────────────────────────────────────────────────────────
def _cli():
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="state.yaml 전체 덤프")

    p_get = sub.add_parser("get", help="item 조회")
    p_get.add_argument("item_id")

    p_set = sub.add_parser("set", help="수동 상태 전이")
    p_set.add_argument("item_id")
    p_set.add_argument("--to", required=True)
    p_set.add_argument("--from", dest="from_", default=None)
    p_set.add_argument("--actor", default="manual")
    p_set.add_argument("--reason", default="")
    p_set.add_argument("--kind", default="find")

    args = p.parse_args()
    if args.cmd == "show":
        state = load_state()
        print(yaml.safe_dump(state, allow_unicode=True, sort_keys=False))
    elif args.cmd == "get":
        item = get_item(args.item_id)
        if item is None:
            print(f"[NOT FOUND] {args.item_id}")
            sys.exit(1)
        print(yaml.safe_dump(item, allow_unicode=True, sort_keys=False))
    elif args.cmd == "set":
        item = apply_transition(
            args.item_id,
            from_state=args.from_,
            to_state=args.to,
            actor=args.actor,
            reason=args.reason or f"manual set to {args.to}",
            kind=args.kind,
        )
        print(f"[OK] {args.item_id}: {args.from_} → {args.to}")
        print(yaml.safe_dump(item, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    _cli()
