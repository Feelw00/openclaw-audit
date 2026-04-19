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
# 기여규칙 상수 (openclaw-contribution.md §2, §5 반영)
# ────────────────────────────────────────────────────────────
RESTRICTED_CODEOWNERS = {"@openclaw/secops"}   # 소유자 명시 동의 없으면 수정 금지
PR_WARN_THRESHOLD = 7                          # 열린 PR 수 7 이상 경고
PR_BLOCK_THRESHOLD = 10                        # 10 이상 hard block (openclaw 자동 close)


# ────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────
def detect_openclaw_repo() -> str | None:
    """openclaw/ remote 에서 owner/name 추출. upstream 우선 (PR/issue 는 upstream 에 발행)."""
    for remote in ("upstream", "origin"):
        try:
            out = subprocess.run(
                ["git", "-C", str(OPENCLAW_ROOT), "remote", "get-url", remote],
                capture_output=True, text=True, check=False,
            )
            if out.returncode != 0:
                continue
            url = out.stdout.strip()
            # git@github.com:owner/name.git | https://github.com/owner/name(.git)?
            m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def parse_codeowners(codeowners_path: Path) -> list[tuple[str, list[str]]]:
    """CODEOWNERS 파일 파싱. [(pattern, [owners])] 순서 유지."""
    rules: list[tuple[str, list[str]]] = []
    if not codeowners_path.exists():
        return rules
    for line in codeowners_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], parts[1:]))
    return rules


def codeowners_match(pattern: str, path: str) -> bool:
    """
    GitHub CODEOWNERS glob 간소화 매칭.
    - '/' 선두는 repo root 앵커
    - '/' 후미는 디렉터리 prefix 매치
    - '*' 는 슬래시 미포함 임의, '**' 는 임의
    """
    import fnmatch
    pat = pattern[1:] if pattern.startswith("/") else pattern
    if pat.endswith("/"):
        return path.startswith(pat) or path == pat.rstrip("/")
    if "**" in pat:
        regex = fnmatch.translate(pat).replace(r"\*\*", ".*")
        import re as _re
        return bool(_re.match(regex, path))
    return fnmatch.fnmatch(path, pat) or path.startswith(pat + "/")


def check_codeowners(repo_root: Path, files: list[str]) -> list[tuple[str, list[str]]]:
    """각 file 에 대해 마지막으로 매치된 owner 조회. RESTRICTED 포함 시 반환."""
    codeowners_path = repo_root / ".github" / "CODEOWNERS"
    rules = parse_codeowners(codeowners_path)
    if not rules:
        return []
    out: list[tuple[str, list[str]]] = []
    for f in files:
        last_owners: list[str] | None = None
        for pattern, owners in rules:
            if codeowners_match(pattern, f):
                last_owners = owners
        if last_owners:
            restricted = [o for o in last_owners if o in RESTRICTED_CODEOWNERS]
            if restricted:
                out.append((f, restricted))
    return out


def check_open_pr_count(repo: str) -> tuple[int, list[str]]:
    """gh pr list --author @me --state open. (-1, []) if gh 실패."""
    cmd = ["gh", "pr", "list", "--repo", repo, "--author", "@me",
           "--state", "open", "--json", "number,title"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return (-1, [])
    try:
        items = json.loads(out.stdout)
        return (len(items), [f"#{i['number']} {i.get('title','')}" for i in items])
    except Exception:
        return (-1, [])


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
def run_dedup_check(cand_id: str, repo: str) -> bool:
    """dedup.py 를 서브프로세스로 실행. 매치 있으면 False."""
    script = Path(__file__).resolve().parent / "dedup.py"
    cmd = [sys.executable, str(script), cand_id, "--repo", repo]
    out = subprocess.run(cmd, capture_output=False, text=True, check=False)
    return out.returncode == 0


def process(cand_id: str, apply_mode: bool, repo: str, force: bool, labels: list[str],
            acknowledge_dedup: bool = False, acknowledge_codeowners: bool = False) -> bool:
    cand_path = CANDIDATES_DIR / f"{cand_id}.md"
    if not cand_path.exists():
        print(f"[ERROR] CAND not found: {cand_path}", file=sys.stderr)
        return False

    # 기여규칙 Gate 1: 저자당 열린 PR 수 (openclaw-contribution.md §2 — 10 넘으면 자동 close)
    print(f"\n─ open PR count gate ({repo})")
    pr_count, titles = check_open_pr_count(repo)
    if pr_count < 0:
        print("  [WARN] gh pr list 실패 (auth?). skip")
    else:
        print(f"  열린 PR {pr_count} 건 (block={PR_BLOCK_THRESHOLD}, warn={PR_WARN_THRESHOLD}):")
        for t in titles:
            print(f"    - {t}")
        if pr_count >= PR_BLOCK_THRESHOLD and not force:
            print(f"\n[STOP] 열린 PR {pr_count} 건 ≥ {PR_BLOCK_THRESHOLD}. openclaw 가 자동 close.", file=sys.stderr)
            return False
        if pr_count >= PR_WARN_THRESHOLD:
            print(f"  [WARN] 열린 PR 7+ — 새 PR 전 기존 close/merge 권장")

    # CAND 먼저 로드 (CODEOWNERS 체크에 FIND 파일 경로 필요)
    cand_fm, cand_body = parse_frontmatter(cand_path.read_text(encoding="utf-8"))
    if cand_fm is None:
        print("[ERROR] CAND frontmatter parse failed", file=sys.stderr)
        return False

    find_ids = cand_fm.get("finding_ids") or cand_fm.get("perf_refs") or []
    finds = load_finds(find_ids)
    if not finds:
        print("[ERROR] no FIND files loaded", file=sys.stderr)
        return False

    # 기여규칙 Gate 2: CODEOWNERS restricted 파일 차단 (openclaw-contribution.md §5)
    print(f"\n─ CODEOWNERS restricted check ({OPENCLAW_ROOT})")
    find_files = [fm.get("file", "") for fm, _ in finds if fm.get("file")]
    restricted = check_codeowners(OPENCLAW_ROOT, find_files)
    if restricted:
        print(f"  [RESTRICTED] {len(restricted)} 파일이 secops 오소유:")
        for f, owners in restricted:
            print(f"    {f}  ←  {', '.join(owners)}")
        if not (acknowledge_codeowners or force):
            print(f"\n[STOP] CODEOWNERS restricted. 소유자 명시 동의 있으면 --acknowledge-codeowners.", file=sys.stderr)
            return False
        if acknowledge_codeowners:
            print("  [INFO] --acknowledge-codeowners 로 진행 (소유자 동의 확인)")
    else:
        print(f"  OK ({len(find_files)} 파일 모두 non-restricted)")

    # Pre-publish dedup check on upstream. Retrofit after CAND-004 — we should
    # never file without at least a keyword/function search against the repo.
    print(f"\n─ upstream dedup pre-check ({repo})")
    dedup_ok = run_dedup_check(cand_id, repo)
    if not dedup_ok and not (acknowledge_dedup or force):
        print(f"\n[STOP] dedup 매치 있음. 사람 검토 후 --acknowledge-dedup 로 진행.", file=sys.stderr)
        return False
    if not dedup_ok and acknowledge_dedup:
        print("[INFO] --acknowledge-dedup 로 진행 (사람이 중복 아님을 확인)")

    # 상태 확인
    item = get_item(cand_id) or {}
    if item.get("state") != "gatekeeper-approved" and not force:
        print(f"[ERROR] {cand_id} state = {item.get('state')} (expected gatekeeper-approved). "
              f"Use --force to override.", file=sys.stderr)
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
    p.add_argument("--acknowledge-dedup", action="store_true",
                   help="dedup 매치 확인 후 중복 아님을 확인했을 때만 진행")
    p.add_argument("--acknowledge-codeowners", action="store_true",
                   help="CODEOWNERS restricted 파일 수정 — 소유자 명시 동의 있을 때만 진행")
    p.add_argument("--label", action="append", default=None, help="추가 라벨 (기본: bug)")
    args = p.parse_args()

    repo = args.repo or detect_openclaw_repo()
    if not repo:
        print("[ERROR] repo 추론 실패. --repo 로 지정하세요.", file=sys.stderr)
        sys.exit(2)

    labels = args.label if args.label else list(DEFAULT_LABELS)

    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    ok = process(args.cand_id, args.apply, repo, args.force, labels,
                 acknowledge_dedup=args.acknowledge_dedup,
                 acknowledge_codeowners=args.acknowledge_codeowners)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
