#!/usr/bin/env python3
"""첫 telegram user account 인증 (Telethon).

사용자가 직접 terminal 에서 한 번 실행 — SMS code + (선택) 2FA password 입력.
성공 시 session 파일 영구 저장 (~/.openclaw-audit-secrets/telethon.session, 600 권한).
이후 audit driver 들이 이 session 재사용 (재인증 불필요).

사용:
  set -a && source ~/.openclaw-audit-secrets/telegram.env && set +a
  /tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/telegram_login.py

env 요구:
  OPENCLAW_QA_TELEGRAM_API_ID
  OPENCLAW_QA_TELEGRAM_API_HASH
  OPENCLAW_QA_TELEGRAM_USER_PHONE
  OPENCLAW_QA_TELEGRAM_SESSION (선택, default ~/.openclaw-audit-secrets/telethon)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from telethon.sync import TelegramClient
except ImportError:
    sys.exit("ERROR: telethon 미설치. /tmp/openclaw-audit-venv/bin/pip install telethon")


def main() -> int:
    try:
        api_id = int(os.environ["OPENCLAW_QA_TELEGRAM_API_ID"])
        api_hash = os.environ["OPENCLAW_QA_TELEGRAM_API_HASH"]
        phone = os.environ["OPENCLAW_QA_TELEGRAM_USER_PHONE"]
    except KeyError as e:
        sys.exit(f"ERROR: env {e} 미설정. ~/.openclaw-audit-secrets/telegram.env source 했는지 확인.")

    session_base = os.environ.get(
        "OPENCLAW_QA_TELEGRAM_SESSION",
        str(Path.home() / ".openclaw-audit-secrets" / "telethon"),
    )
    session_path = Path(session_base)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"session base: {session_path}")
    print(f"phone:        {phone}")
    print(f"api_id:       {api_id}")
    print()

    client = TelegramClient(str(session_path), api_id, api_hash)
    # Telethon 의 .start(phone=...) 가 SMS code prompt + 2FA password prompt 자동 처리.
    # interactive — Claude Code 가 stdin 못 받음. 사용자 직접 terminal 에서 실행 필수.
    client.start(phone=phone)

    me = client.get_me()
    username = f"@{me.username}" if me.username else "<no username>"
    print(f"\n✓ logged in: {me.first_name} (id={me.id}, {username})")

    client.disconnect()

    # session file 권한 강화
    session_file = Path(str(session_path) + ".session")
    if session_file.exists():
        session_file.chmod(0o600)
        print(f"✓ session saved: {session_file} (perm 600, size={session_file.stat().st_size}B)")
    else:
        print(f"WARN: session file 미생성 — 예상 path: {session_file}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
