#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/env_isolate.py

production ~/.openclaw/ 손상을 막기 위한 격리 환경 구성.

원리:
- OPENCLAW_HOME 환경변수로 home 디렉터리 redirect
- 임시 home 에 minimal openclaw.json + 빈 sqlite/cron/agents 디렉터리만 생성
- OAuth profile 만 production 에서 read-only 복사 (LLM 호출 가능하게)
- gateway 포트 충돌 회피 (production 18789 → 임시 17000-17999 범위)

호출자: harness/run.py (mode 시작 직후, build 와 reproduce 사이)

cleanup: with IsolatedHome(...) as env: ... 구문으로 자동 rmtree.
사용자 ~/.openclaw 는 읽기만 (권한 없는 write 시도는 raise).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PRODUCTION_HOME = Path.home() / ".openclaw"
PROOF_TMP_BASE = Path("/tmp")
PORT_RANGE = (17000, 17999)


def _alloc_free_port(start: int = PORT_RANGE[0], end: int = PORT_RANGE[1]) -> int:
    """주어진 범위에서 bind 가능한 첫 포트 반환. 없으면 raise."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in range {start}-{end}")


def _build_minimal_config(
    *,
    gateway_port: int,
    auth_profile_id: str | None,
) -> dict:
    """proof 환경 minimal openclaw.json.

    실제 production config 의 키를 참조하되 (위 ls 출력) 토큰값은 빈 placeholder.
    auth.profiles 는 production 에서 ref 만 복사 (실 token 값은 별도 파일).
    """
    cfg: dict = {
        "agents": {
            "defaults": {
                "model": {"primary": "openai-codex/gpt-5.5"},
                "agentRuntime": {"id": "codex"},
            },
        },
        "gateway": {
            "mode": "local",
            "port": gateway_port,
            "bind": "127.0.0.1",
            "auth": {"mode": "token", "token": secrets.token_hex(16)},
            "tailscale": {"mode": "off", "resetOnExit": True},
        },
        "session": {"dmScope": "per-channel-peer"},
        "plugins": {
            "entries": {
                "codex": {"enabled": True},
                "openai": {"enabled": True},
            }
        },
        "meta": {
            "lastTouchedVersion": "real-behavior-proof-harness",
            "lastTouchedAt": None,
            "_isolated_proof_env": True,
        },
    }
    if auth_profile_id:
        cfg["auth"] = {"profiles": {auth_profile_id: {"provider": "openai-codex", "mode": "oauth"}}}
    return cfg


def _copy_auth_profile(production_home: Path, isolated_home: Path) -> str | None:
    """production 의 auth-profiles.json 만 read-only 로 복사.

    OAuth token 자체는 별도 파일이라 LLM 호출이 필요한 시나리오에서만 복사.
    반환: 복사된 profile id (없으면 None).
    """
    src = production_home / "agents" / "main" / "agent" / "auth-profiles.json"
    if not src.exists():
        return None
    dest = isolated_home / "agents" / "main" / "agent" / "auth-profiles.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(0o400)  # read-only
    try:
        with src.open() as f:
            data = json.load(f)
        profiles = data.get("profiles", {})
        # 첫 active profile id 반환
        for pid, prof in profiles.items():
            if isinstance(prof, dict):
                return pid
    except Exception:
        pass
    return None


@contextmanager
def isolated_home(
    *,
    require_oauth: bool = False,
    proof_id: str | None = None,
) -> Iterator[dict]:
    """proof 실행용 임시 OPENCLAW_HOME 컨텍스트.

    yield: dict { 'home': Path, 'gateway_port': int, 'sqlite': Path, 'env': dict (subprocess.run env=) }

    require_oauth=True 면 production auth-profiles.json 복사. external dep 시나리오에 필수.
    proof_id 지정 시 디렉터리 이름에 사용 (디버깅 편의), 미지정 시 랜덤.
    """
    pid = proof_id or secrets.token_hex(6)
    root = PROOF_TMP_BASE / f"proof-{pid}" / ".openclaw"
    if root.exists():
        shutil.rmtree(root.parent)
    root.mkdir(parents=True)

    # 필수 디렉터리 (openclaw 내부에서 mkdir 안 하는 경로 미리 만듦)
    for sub in ("agents", "cron", "tasks", "logs", "memory", "plugin-runtime-deps"):
        (root / sub).mkdir(exist_ok=True)

    auth_profile = _copy_auth_profile(PRODUCTION_HOME, root) if require_oauth else None
    gateway_port = _alloc_free_port()

    cfg = _build_minimal_config(gateway_port=gateway_port, auth_profile_id=auth_profile)
    (root / "openclaw.json").write_text(json.dumps(cfg, indent=2))

    # tasks/runs.sqlite 는 openclaw 가 첫 실행 시 자동 생성 (스키마 마이그레이션 포함).
    # 미리 빈 파일 만들면 마이그레이션 충돌 가능 → touch 안 함.
    sqlite_path = root / "tasks" / "runs.sqlite"

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = str(root)
    # production 18789 와 충돌 회피 표시 (디버깅용)
    env["OPENCLAW_GATEWAY_PORT"] = str(gateway_port)

    try:
        yield {
            "home": root,
            "gateway_port": gateway_port,
            "sqlite": sqlite_path,
            "auth_profile_id": auth_profile,
            "env": env,
            "proof_id": pid,
        }
    finally:
        # cleanup — 단, sqlite 가 살아 있으면 측정 도중 raise 가능. 호출자가 끝났을 때만.
        try:
            shutil.rmtree(root.parent)
        except Exception as e:
            sys.stderr.write(f"WARN: cleanup failed for {root.parent}: {e}\n")


# CLI for ad-hoc inspection
if __name__ == "__main__":
    import argparse, time

    ap = argparse.ArgumentParser(description="env_isolate — preview isolated home layout")
    ap.add_argument("--require-oauth", action="store_true")
    ap.add_argument("--hold-seconds", type=int, default=2, help="show layout then cleanup")
    args = ap.parse_args()

    with isolated_home(require_oauth=args.require_oauth) as env:
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in env.items() if k != "env"}, indent=2))
        print(f"contents of {env['home']}:")
        for p in sorted(env["home"].rglob("*")):
            print(f"  {p.relative_to(env['home'])}")
        time.sleep(args.hold_seconds)
    print("cleanup done")
