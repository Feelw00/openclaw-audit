#!/usr/bin/env python3
"""
openclaw-audit — 승인된 CAND/SOL 을 openclaw repo 에 GitHub Issue 로 발행.

사용:
  python publish.py <CAND-ID>                    # dry-run, body 미리보기만
  python publish.py <CAND-ID> --apply            # 실제 발행
  python publish.py <CAND-ID> --repo owner/name  # 타깃 repo 지정 (기본: openclaw 원격에서 추론)

전제 조건:
  - CAND 의 local-state 가 gatekeeper-approved 이어야 함 (--force 로 우회 가능하지만 비권장)
  - 같은 증거 fingerprint 의 Issue 가 이미 있으면 dedup (건너뜀)

발행 내용:
  - 제목: CAND.proposed_title
  - 본문: 문제 요약 + 각 FIND 의 증거·메커니즘·근본원인 + 재현 힌트
  - 라벨: openclaw 기본 `bug` 만 부착 (커스텀 perf:* 없음)
  - 본문 말미에 audit 추적 앵커: `<!-- openclaw-audit: CAND-XXX -->`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_state import (  # noqa: E402
    CANDIDATES_DIR,
    OPENCLAW_ROOT,
    READY_DIR,
    apply_transition,
    get_item,
    now_iso,
    parse_frontmatter,
    read_md,
)

DEFAULT_LABELS = ["bug"]
ANCHOR_PREFIX = "<!-- openclaw-audit:"
ANCHOR_SUFFIX = "-->"


# ────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────
def detect_openclaw_repo() -> str | None:
    """openclaw/ origin 에서 owner/name 추출."""
    try:
        out = subprocess.run(
            ["git", "-C", str(OPENCLAW_ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        )
        if out.returncode != 0:
            return None
        url = out.stdout.strip()
        # git@github.com:owner/name.git | https://github.com/owner/name(.git)?
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def fingerprint_for_find(fm: dict) -> str:
    file = fm.get("file", "")
    chain = fm.get("root_cause_chain") or []
    first_because = ""
    if chain and isinstance(chain, list):
        first_because = str(chain[0].get("because", ""))
    norm = re.sub(r"\s+", " ", first_because.lower()).strip()[:120]
    key = f"{file}|{norm}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ────────────────────────────────────────────────────────────
# Body 구성
# ────────────────────────────────────────────────────────────
def build_body(cand_fm: dict, cand_body: str, finds: list[tuple[dict, str]]) -> str:
    parts: list[str] = []

    parts.append("## 요약")
    parts.append("")
    parts.append(cand_body.strip() or cand_fm.get("cluster_rationale", "").strip() or "_(요약 누락)_")
    parts.append("")

    parts.append("## 관련 Finding 상세")
    parts.append("")
    for i, (fm, body) in enumerate(finds, 1):
        parts.append(f"### {i}. {fm.get('title', fm.get('id'))}")
        parts.append("")
        parts.append(f"- **파일**: `{fm.get('file')}:{fm.get('line_range')}`")
        parts.append(f"- **증상 유형**: {fm.get('symptom_type')}")
        parts.append(f"- **예상 영향**: {fm.get('impact_hypothesis')} — {fm.get('impact_detail', '')}")
        parts.append("")
        parts.append("<details><summary>증거 / 메커니즘 / 근본 원인</summary>")
        parts.append("")
        parts.append(body.strip())
        parts.append("")
        parts.append("</details>")
        parts.append("")

    parts.append("---")
    parts.append("")
    parts.append(
        "<sub>이 이슈는 [openclaw-audit](https://github.com/lucas-infini/openclaw-audit) "
        "로컬 신뢰성 감사 파이프라인에서 생성됨. 재현 테스트와 수정은 별도 PR 에 포함됩니다.</sub>"
    )

    anchor = (
        f"{ANCHOR_PREFIX} cand={cand_fm.get('candidate_id')} "
        f"fingerprints={','.join(fingerprint_for_find(fm) for fm, _ in finds)} "
        f"at={now_iso()} {ANCHOR_SUFFIX}"
    )
    return "\n".join(parts) + "\n\n" + anchor + "\n"


def load_finds(find_ids: list[str]) -> list[tuple[dict, str]]:
    loaded: list[tuple[dict, str]] = []
    missing: list[str] = []
    for fid in find_ids:
        fm, body = read_md(READY_DIR / f"{fid}.md")
        if fm is None:
            missing.append(fid)
        else:
            loaded.append((fm, body))
    if missing:
        print(f"[WARN] missing FIND files: {missing}", file=sys.stderr)
    return loaded


# ────────────────────────────────────────────────────────────
# Dedup (openclaw repo 기존 Issue 탐색)
# ────────────────────────────────────────────────────────────
def search_existing(repo: str, fingerprints: list[str]) -> list[dict]:
    results: list[dict] = []
    seen: set[int] = set()
    for fp in fingerprints:
        cmd = [
            "gh", "issue", "list",
            "--repo", repo,
            "--state", "all",
            "--search", fp,
            "--json", "number,state,title,body",
            "--limit", "10",
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if out.returncode != 0 or not out.stdout.strip():
                continue
            items = json.loads(out.stdout)
            for item in items:
                body = item.get("body", "")
                if fp not in body:
                    continue
                if ANCHOR_PREFIX not in body:
                    continue
                if item["number"] in seen:
                    continue
                seen.add(item["number"])
                item["matched_fingerprint"] = fp
                item.pop("body", None)
                results.append(item)
        except Exception as e:
            print(f"[WARN] dedup search failed for fp={fp}: {e}", file=sys.stderr)
    return results


# ────────────────────────────────────────────────────────────
# 발행
# ────────────────────────────────────────────────────────────
def create_issue(repo: str, title: str, body: str, labels: list[str]) -> tuple[int | None, str]:
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
    for lbl in labels:
        cmd.extend(["--label", lbl])
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {out.stderr.strip()}")
    url = out.stdout.strip().splitlines()[-1]
    m = re.search(r"/issues/(\d+)$", url)
    return (int(m.group(1)) if m else None, url)


# ────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────
def process(cand_id: str, apply_mode: bool, repo: str, force: bool, labels: list[str]) -> bool:
    cand_path = CANDIDATES_DIR / f"{cand_id}.md"
    if not cand_path.exists():
        print(f"[ERROR] CAND not found: {cand_path}", file=sys.stderr)
        return False

    cand_fm, cand_body = parse_frontmatter(cand_path.read_text(encoding="utf-8"))
    if cand_fm is None:
        print("[ERROR] CAND frontmatter parse failed", file=sys.stderr)
        return False

    # 상태 확인
    item = get_item(cand_id) or {}
    if item.get("state") != "gatekeeper-approved" and not force:
        print(f"[ERROR] {cand_id} state = {item.get('state')} (expected gatekeeper-approved). "
              f"Use --force to override.", file=sys.stderr)
        return False

    find_ids = cand_fm.get("finding_ids") or cand_fm.get("perf_refs") or []
    finds = load_finds(find_ids)
    if not finds:
        print("[ERROR] no FIND files loaded", file=sys.stderr)
        return False

    title = cand_fm.get("proposed_title", cand_id)
    if len(title) > 250:
        title = title[:247] + "..."

    body = build_body(cand_fm, cand_body, finds)
    fps = [fingerprint_for_find(fm) for fm, _ in finds]

    print(f"\n── {cand_id}")
    print(f"  repo:  {repo}")
    print(f"  title: {title}")
    print(f"  label: {labels}")
    print(f"  finds: {len(finds)}")
    print(f"  body:  {len(body)} chars")
    print(f"  fps:   {fps}")

    # dedup
    existing = search_existing(repo, fps)
    if existing:
        print(f"  [DEDUP] 기존 Issue {len(existing)} 건 — 사람 검토 필요:")
        for item in existing:
            print(f"    #{item['number']} [{item['state']}] {item['title']}")
        if not force:
            print("  → --force 없으면 발행 스킵")
            return False

    if not apply_mode:
        preview = CANDIDATES_DIR / f"{cand_id}.preview.md"
        preview.write_text(body, encoding="utf-8")
        print(f"  [DRY-RUN] preview: {preview}")
        return True

    try:
        issue_num, url = create_issue(repo, title, body, labels)
        print(f"  [APPLY] #{issue_num} → {url}")
    except RuntimeError as e:
        print(f"  [ERROR] {e}", file=sys.stderr)
        return False

    extras = {
        "openclaw_repo": repo,
        "openclaw_issue_number": issue_num,
        "openclaw_issue_url": url,
        "published_at": now_iso(),
    }
    apply_transition(
        cand_id,
        from_state=None,
        to_state="published",
        actor="publish",
        reason=f"issue #{issue_num} created on {repo}",
        kind="candidate",
        extras=extras,
    )
    return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cand_id")
    p.add_argument("--apply", action="store_true", help="실제 발행 (기본 dry-run)")
    p.add_argument("--repo", default=None, help="target repo (owner/name). 기본: openclaw origin")
    p.add_argument("--force", action="store_true", help="state 불일치/dedup 무시")
    p.add_argument("--label", action="append", default=None, help="추가 라벨 (기본: bug)")
    args = p.parse_args()

    repo = args.repo or detect_openclaw_repo()
    if not repo:
        print("[ERROR] repo 추론 실패. --repo 로 지정하세요.", file=sys.stderr)
        sys.exit(2)

    labels = args.label if args.label else list(DEFAULT_LABELS)

    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    ok = process(args.cand_id, args.apply, repo, args.force, labels)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
