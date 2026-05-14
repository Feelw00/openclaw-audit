#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/build.py

worktree 자동 생성 + pnpm build (base / head 두 빌드).

동작:
1. /Users/lucas/Project/openclaw-worktrees/proof-{uuid}-{phase}/ 두 worktree 생성
   - base: --base-sha (보통 upstream/main 또는 PR base commit)
   - head: --head-sha (fix 적용된 commit, 또는 patch 적용 후 commit)
2. 각 worktree 에 pnpm install + pnpm build (직렬 — pnpm store lock 충돌 회피)
3. 두 worktree path + node entry path 반환

cleanup: 호출자가 BuildPair 의 cleanup() 호출. worktree 는 디스크 사용 크므로
proof 실행 직후 정리 권장 (단, evidence 가 worktree path 를 인용하면 PR body 끝까지 보존 필요).
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

OPENCLAW_REPO = Path("/Users/lucas/Project/openclaw")
WORKTREES_DIR = Path("/Users/lucas/Project/openclaw-worktrees")
DEFAULT_BUILD_TIMEOUT_SEC = 1800  # 30 min — pnpm build 가 느릴 수 있음


@dataclass
class BuildResult:
    path: Path
    sha: str
    branch: str
    node_entry: Path  # `node {node_entry} <args>` 로 openclaw 실행

    def cleanup(self) -> None:
        """worktree 제거. push 안 했으므로 brach 도 함께 제거."""
        try:
            subprocess.run(
                ["git", "-C", str(OPENCLAW_REPO), "worktree", "remove", "--force", str(self.path)],
                check=False,
                capture_output=True,
            )
        except Exception as e:
            sys.stderr.write(f"WARN: worktree remove failed for {self.path}: {e}\n")
        try:
            subprocess.run(
                ["git", "-C", str(OPENCLAW_REPO), "branch", "-D", self.branch],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass


@dataclass
class BuildPair:
    base: BuildResult
    head: BuildResult

    def cleanup(self) -> None:
        self.base.cleanup()
        self.head.cleanup()


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """capture stdout/stderr, raise on non-zero. fast feedback for build failures."""
    print(f"  $ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""), file=sys.stderr)
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _create_worktree(sha: str, label: str, proof_id: str) -> tuple[Path, str]:
    """worktree 생성. 반환: (path, branch_name)."""
    branch = f"proof/{proof_id}-{label}"
    wt_path = WORKTREES_DIR / f"proof-{proof_id}-{label}"
    if wt_path.exists():
        # 기존 stale worktree → 강제 제거 후 재생성
        subprocess.run(
            ["git", "-C", str(OPENCLAW_REPO), "worktree", "remove", "--force", str(wt_path)],
            check=False,
            capture_output=True,
        )
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    _run(
        ["git", "-C", str(OPENCLAW_REPO), "worktree", "add", "-b", branch, str(wt_path), sha],
        timeout=120,
    )
    return wt_path, branch


def _build_one(
    wt_path: Path,
    *,
    skip_install: bool = False,
    skip_build: bool = False,
    build_timeout: int = DEFAULT_BUILD_TIMEOUT_SEC,
) -> Path:
    """pnpm install + pnpm build. node entry path 반환.

    skip_install=True 면 install 단계 생략 (개발 중 빠른 iteration).
    skip_build=True 면 build 단계 생략 — 시나리오가 src ts 를 직접 import 하는 경우
    (tsx / --experimental-strip-types) 30분 빌드 우회. install 만으로 node_modules 확보.
    pnpm 은 hoisted store 라 두 worktree 가 같은 store 공유 → install 빠름.

    skip_build=True 시 entry 는 worktree root 자체 (시나리오가 `node_entry.parent` 로
    worktree path 만 쓰는 패턴 호환). 빌드 artifact 의존 시나리오는 skip_build 와 양립 불가.
    """
    if not skip_install:
        _run(["pnpm", "install", "--frozen-lockfile"], cwd=wt_path, timeout=900)
    if skip_build:
        # 시나리오가 src ts 의존 — 빌드 우회. entry 는 worktree root 의 sentinel 파일.
        sentinel = wt_path / "package.json"
        if not sentinel.exists():
            raise RuntimeError(f"worktree malformed: no package.json at {wt_path}")
        return sentinel
    _run(["pnpm", "build"], cwd=wt_path, timeout=build_timeout)
    # package.json bin: openclaw → openclaw.mjs
    entry = wt_path / "openclaw.mjs"
    if not entry.exists():
        # fallback: dist/cli.js or build output
        for cand in (wt_path / "dist" / "cli.js", wt_path / "dist" / "openclaw.js"):
            if cand.exists():
                return cand
        raise RuntimeError(f"openclaw entry not found in {wt_path} (looked for openclaw.mjs)")
    return entry


def build_pair(
    *,
    base_sha: str,
    head_sha: str,
    proof_id: str | None = None,
    skip_install: bool = False,
    skip_head_install: bool = True,
    skip_build: bool = False,
) -> BuildPair:
    """base/head 두 worktree 생성 + 빌드 후 반환.

    skip_head_install=True (default): base 가 install 후 같은 pnpm store 공유하므로 head 는 install 생략 가능.
    skip_build=True: 시나리오가 src ts 직접 import (tsx) — build 우회.
    proof_id: 디렉터리 이름에 사용 (env_isolate 와 같은 id 권장).
    """
    pid = proof_id or secrets.token_hex(6)

    print(f"[build] creating base worktree at {base_sha[:10]}", file=sys.stderr)
    base_path, base_branch = _create_worktree(base_sha, "base", pid)
    print(f"[build] base = {base_path}", file=sys.stderr)
    base_entry = _build_one(base_path, skip_install=skip_install, skip_build=skip_build)

    print(f"[build] creating head worktree at {head_sha[:10]}", file=sys.stderr)
    head_path, head_branch = _create_worktree(head_sha, "head", pid)
    print(f"[build] head = {head_path}", file=sys.stderr)
    head_entry = _build_one(head_path, skip_install=skip_install or skip_head_install, skip_build=skip_build)

    return BuildPair(
        base=BuildResult(path=base_path, sha=base_sha, branch=base_branch, node_entry=base_entry),
        head=BuildResult(path=head_path, sha=head_sha, branch=head_branch, node_entry=head_entry),
    )


def build_single(
    *,
    sha: str,
    label: str = "single",
    proof_id: str | None = None,
    skip_install: bool = False,
    skip_build: bool = False,
) -> BuildResult:
    """단일 빌드 (pre-sol 모드의 without-fix only).

    pre-sol mode 는 head 빌드 없이 base 만 (결함이 base 에 있는지 확인).
    skip_build=True: 시나리오가 src ts 직접 import (tsx) — build 우회.
    """
    pid = proof_id or secrets.token_hex(6)
    print(f"[build] creating {label} worktree at {sha[:10]}", file=sys.stderr)
    wt_path, branch = _create_worktree(sha, label, pid)
    entry = _build_one(wt_path, skip_install=skip_install, skip_build=skip_build)
    return BuildResult(path=wt_path, sha=sha, branch=branch, node_entry=entry)


# CLI for ad-hoc testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="build.py — worktree builder")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--cleanup", action="store_true", help="cleanup after build (test mode)")
    args = ap.parse_args()

    if args.head_sha:
        pair = build_pair(
            base_sha=args.base_sha, head_sha=args.head_sha, skip_install=args.skip_install
        )
        print(f"base: {pair.base.path} -> {pair.base.node_entry}")
        print(f"head: {pair.head.path} -> {pair.head.node_entry}")
        if args.cleanup:
            pair.cleanup()
            print("cleanup done")
    else:
        single = build_single(sha=args.base_sha, skip_install=args.skip_install)
        print(f"single: {single.path} -> {single.node_entry}")
        if args.cleanup:
            single.cleanup()
            print("cleanup done")
