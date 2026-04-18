#!/usr/bin/env python3
"""
openclaw-audit — upstream 중복 이슈/PR 탐지.

발행 전 **반드시** 실행:
  python dedup.py CAND-XXX [--repo owner/name] [--json]

동작:
  1. CAND + 연결된 FIND 파일 읽기
  2. 키워드·파일경로·symptom 기반 다중 검색:
     - gh issue list --search <title 키워드>
     - gh issue list --search <file 경로 기본 토큰>
     - gh pr list --search <file 경로>
     - gh pr list --search <symptom + domain>
  3. 결과를 각 소스별 table 로 출력
  4. "비어있지 않은 매치" 가 있으면 exit 1 (사람 판단 필요)

exit code:
  0 → 매치 없음. 발행 안전.
  1 → 매치 있음. 사람이 검토 후 --acknowledge 로 publish.
  2 → 실행 오류.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_state import (  # noqa: E402
    AUDIT_ROOT,
    CANDIDATES_DIR,
    OPENCLAW_ROOT,
    READY_DIR,
    get_item,
    parse_frontmatter,
    read_md,
)


# ────────────────────────────────────────────────────────────
def detect_repo() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(OPENCLAW_ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False,
        )
        if out.returncode != 0:
            return None
        url = out.stdout.strip()
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def gh_search(kind: str, repo: str, query: str, limit: int = 20) -> list[dict]:
    """kind: 'issue' or 'pr'. Returns list of {number, state, title, url, author}."""
    cmd = [
        "gh", kind, "list",
        "--repo", repo,
        "--state", "all",
        "--search", query,
        "--json", "number,state,title,url,author",
        "--limit", str(limit),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        return json.loads(out.stdout)
    except Exception as e:
        print(f"[WARN] gh {kind} list failed for '{query}': {e}", file=sys.stderr)
        return []


def gh_search_commits(repo: str, query: str, limit: int = 10) -> list[dict]:
    """CAL-004: upstream 이 이미 merge 한 fix 를 commit 메시지로 탐지."""
    owner, name = repo.split("/", 1)
    cmd = [
        "gh", "search", "commits",
        "--owner", owner,
        "--repo", name,
        query,
        "--json", "sha,commit,url,repository",
        "--limit", str(limit),
        "--sort", "author-date",
        "--order", "desc",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        return json.loads(out.stdout)
    except Exception as e:
        print(f"[WARN] gh search commits failed for '{query}': {e}", file=sys.stderr)
        return []


# ────────────────────────────────────────────────────────────
def extract_search_terms(cand_fm: dict, finds: list[tuple[dict, str]]) -> dict:
    """CAND + FIND 에서 검색 키 추출."""
    title = cand_fm.get("proposed_title", "")
    # title 의 주요 명사 단어 (영문) — 한국어 제외
    title_en_words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", title)

    files = set()
    symptom_types = set()
    function_names = set()

    for fm, _ in finds:
        f = fm.get("file")
        if f:
            files.add(f)
        st = fm.get("symptom_type")
        if st:
            symptom_types.add(st)
        # evidence 블록에서 함수 이름 추출 (e.g., `function foo(` 또는 `const foo =`)
        ev = str(fm.get("evidence", ""))
        for m in re.finditer(r"\bfunction\s+(\w+)\s*\(", ev):
            function_names.add(m.group(1))
        for m in re.finditer(r"\b(const|let|var)\s+(\w+)\s*=", ev):
            function_names.add(m.group(2))
        # 제목의 영문 식별자
        t_en = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", fm.get("title", ""))
        title_en_words.extend(t_en)

    # 파일명(basename) + 디렉터리 토큰
    file_tokens = set()
    for f in files:
        file_tokens.add(Path(f).stem)  # e.g., "subagent-registry"
        for part in Path(f).parts:
            if part and not part.endswith(".ts"):
                file_tokens.add(part)

    return {
        "title_keywords": sorted(set(title_en_words)),
        "files": sorted(files),
        "file_tokens": sorted(file_tokens),
        "symptom_types": sorted(symptom_types),
        "function_names": sorted(function_names),
    }


# ────────────────────────────────────────────────────────────
def build_queries(terms: dict) -> list[tuple[str, str, str]]:
    """
    (label, kind, query) 튜플 목록.
    쿼리는 gh search 문법 (공백 = AND, 따옴표 = phrase).
    """
    queries: list[tuple[str, str, str]] = []

    # 가장 강한 시그널: 함수/변수 이름 (정확도 높음)
    for fn in terms["function_names"][:3]:
        if len(fn) >= 6:  # 너무 짧은 이름은 noise
            queries.append((f"issue: function {fn}", "issue", fn))
            queries.append((f"pr: function {fn}", "pr", fn))

    # 파일 stem (예: subagent-registry)
    for stem in terms["file_tokens"]:
        if "-" in stem and len(stem) >= 8:  # multi-token stem
            queries.append((f"issue: file stem {stem}", "issue", stem))
            queries.append((f"pr: file stem {stem}", "pr", stem))

    # 제목 키워드 조합 (상위 3개)
    top_title_words = [w for w in terms["title_keywords"] if len(w) >= 5][:3]
    if len(top_title_words) >= 2:
        phrase = " ".join(top_title_words[:2])
        queries.append((f"issue: title '{phrase}'", "issue", phrase))
        queries.append((f"pr: title '{phrase}'", "pr", phrase))

    # 중복 제거
    seen = set()
    unique: list[tuple[str, str, str]] = []
    for lbl, kind, q in queries:
        key = (kind, q.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append((lbl, kind, q))
    return unique


# ────────────────────────────────────────────────────────────
def load_cand(cand_id: str) -> tuple[dict, list[tuple[dict, str]]]:
    cand_fm, _ = read_md(CANDIDATES_DIR / f"{cand_id}.md")
    if cand_fm is None:
        raise RuntimeError(f"CAND not found: {cand_id}")
    find_ids = cand_fm.get("finding_ids") or []
    finds: list[tuple[dict, str]] = []
    for fid in find_ids:
        fm, body = read_md(READY_DIR / f"{fid}.md")
        if fm is None:
            # 이미 rejected 된 FIND 는 skip (retract 된 경우)
            continue
        finds.append((fm, body))
    return cand_fm, finds


# ────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cand_id")
    p.add_argument("--repo", default=None, help="target repo (default: openclaw origin 에서 추론)")
    p.add_argument("--json", action="store_true", help="JSON 출력 (machine-readable)")
    p.add_argument("--limit", type=int, default=15)
    args = p.parse_args()

    repo = args.repo or detect_repo()
    if not repo:
        print("[ERROR] repo 추론 실패. --repo 로 지정.", file=sys.stderr)
        sys.exit(2)

    try:
        cand_fm, finds = load_cand(args.cand_id)
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(2)

    terms = extract_search_terms(cand_fm, finds)
    queries = build_queries(terms)

    # 자기 자신 (이전에 발행된 이슈/PR) 은 제외 — 여러 소스에서 수집
    item = get_item(args.cand_id) or {}
    extras = item.get("extras", {}) or {}
    self_ids: set = {
        cand_fm.get("openclaw_issue"),
        cand_fm.get("openclaw_pr"),
        extras.get("openclaw_issue_number"),
        extras.get("openclaw_pr_number"),
    }
    # index.yaml 의 published 배열도 확인
    try:
        import yaml as _yaml
        idx_path = CANDIDATES_DIR / "index.yaml"
        if idx_path.exists():
            idx = _yaml.safe_load(idx_path.read_text(encoding="utf-8")) or {}
            for entry in idx.get("candidates", []):
                if entry.get("id") == args.cand_id:
                    self_ids.add(entry.get("openclaw_issue"))
                    self_ids.add(entry.get("openclaw_pr"))
            for pub in idx.get("published", []):
                if pub.get("cand_id") == args.cand_id:
                    self_ids.add(pub.get("issue"))
                    self_ids.add(pub.get("pr"))
    except Exception:
        pass
    self_ids.discard(None)

    # CAL-004: upstream 이 이미 merge 한 fix 를 commit 메시지로 탐지
    commit_hits: list[dict] = []
    seen_shas: set[str] = set()
    for fn in terms["function_names"][:3]:
        if len(fn) >= 6:
            for c in gh_search_commits(repo, fn, limit=5):
                sha = c.get("sha", "")
                if sha and sha not in seen_shas:
                    seen_shas.add(sha)
                    commit_hits.append(c)
    if commit_hits:
        print(f"\n── upstream merged commits (CAL-004 guard)")
        for c in commit_hits[:10]:
            msg = (c.get("commit") or {}).get("message", "").split("\n")[0][:100]
            print(f"  {c.get('sha','')[:10]}  {msg}")
            print(f"    {c.get('url','')}")

    results: list[dict] = []
    for label, kind, query in queries:
        hits = gh_search(kind, repo, query, limit=args.limit)
        filtered = [h for h in hits if h["number"] not in self_ids]
        results.append({
            "label": label,
            "kind": kind,
            "query": query,
            "hits": filtered,
        })

    if args.json:
        print(json.dumps({
            "cand_id": args.cand_id,
            "repo": repo,
            "terms": terms,
            "results": results,
        }, ensure_ascii=False, indent=2))
        any_hits = any(r["hits"] for r in results)
        sys.exit(1 if any_hits else 0)

    # 사람용 출력
    print(f"\n=== upstream dedup: {args.cand_id} on {repo} ===")
    print(f"검색 키워드: {terms['title_keywords'][:5]}")
    print(f"파일: {terms['files']}")
    print(f"함수/변수: {terms['function_names'][:5]}")

    any_hits = False
    for r in results:
        print(f"\n── {r['label']}  (query='{r['query']}')")
        if not r["hits"]:
            print("  매치 없음")
            continue
        any_hits = True
        for h in r["hits"][:10]:
            auth = (h.get("author") or {}).get("login", "?")
            print(f"  #{h['number']} [{h['state']}] ({auth}) {h['title'][:90]}")
            print(f"    {h['url']}")

    print()
    if any_hits:
        print("⚠ 매치 있음 — 사람 검토 필수.")
        print("  각 링크 확인 후 중복이 아니면 publish.py 에 --acknowledge-dedup 추가.")
        sys.exit(1)
    else:
        print("✓ 매치 없음. 발행 안전.")
        sys.exit(0)


if __name__ == "__main__":
    main()
