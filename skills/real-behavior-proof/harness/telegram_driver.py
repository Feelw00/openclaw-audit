#!/usr/bin/env python3
"""Audit-side telegram driver — Telethon user account.

CAND-031/032/033 e2e 시나리오의 trigger driver. user account 로 telegram group 에
메시지 send + sut bot 응답 관찰. user → bot 통신이라 telegram bot-to-bot 차단 우회.

Telethon 선택 이유:
- TDLib 1.8.0 (homebrew prebuilt) 이 2026년 telegram server 의 신 layer 요구 못 맞춤
  (UPDATE_APP_TO_LOGIN error). TDLib master 빌드는 30-60분 + ABI risk.
- Telethon: pip 한 줄, pure python, 자체 layer maintenance.
- driver 는 production code 검증과 무관 — 라이브러리 차이는 e2e 결과에 영향 0.

사용:
  with TelegramDriver() as d:
      msg_id = d.send("@openclaw_audit_sut_bot trigger-text")
      # ... openclaw process 가 받아 처리, audit 가 process internal 측정
      # 선택: sut bot 응답 관찰
      replies = d.get_recent_after(msg_id, timeout=5)

env 요구 (~/.openclaw-audit-secrets/telegram.env):
  OPENCLAW_QA_TELEGRAM_API_ID
  OPENCLAW_QA_TELEGRAM_API_HASH
  OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN  (driver 자체는 미사용, sanity check 용)
  OPENCLAW_QA_TELEGRAM_GROUP_ID
  OPENCLAW_QA_TELEGRAM_SESSION (선택, default ~/.openclaw-audit-secrets/telethon)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator


REQUIRED_ENV = (
    "OPENCLAW_QA_TELEGRAM_API_ID",
    "OPENCLAW_QA_TELEGRAM_API_HASH",
    "OPENCLAW_QA_TELEGRAM_GROUP_ID",
)


def ensure_credentials_present() -> None:
    """secret env 누락 시 안내 + raise."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"telegram credentials 누락: {missing}. "
            "set -a && source ~/.openclaw-audit-secrets/telegram.env && set +a 했는지 확인."
        )


def default_session_path() -> Path:
    return Path(
        os.environ.get(
            "OPENCLAW_QA_TELEGRAM_SESSION",
            str(Path.home() / ".openclaw-audit-secrets" / "telethon"),
        )
    )


class TelegramDriver:
    """Stateful driver — context manager. session 한 번 load → 여러 send 재사용.

    동시 여러 instance 금지 (session file lock 충돌). CAND 시나리오마다 한 instance.
    """

    def __init__(
        self,
        *,
        session_path: str | Path | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        group_id: int | None = None,
    ) -> None:
        ensure_credentials_present()
        self.session_path = Path(session_path) if session_path else default_session_path()
        self.api_id = int(api_id if api_id is not None else os.environ["OPENCLAW_QA_TELEGRAM_API_ID"])
        self.api_hash = api_hash or os.environ["OPENCLAW_QA_TELEGRAM_API_HASH"]
        self.group_id = int(group_id if group_id is not None else os.environ["OPENCLAW_QA_TELEGRAM_GROUP_ID"])
        self._client = None
        self._me_id: int | None = None

    def __enter__(self) -> "TelegramDriver":
        from telethon.sync import TelegramClient

        self._client = TelegramClient(str(self.session_path), self.api_id, self.api_hash)
        self._client.connect()
        if not self._client.is_user_authorized():
            self._client.disconnect()
            raise RuntimeError(
                f"telethon session 미인증: {self.session_path}.session — "
                "skills/real-behavior-proof/harness/telegram_login.py 재실행 필요"
            )
        me = self._client.get_me()
        self._me_id = me.id
        return self

    @property
    def me_id(self) -> int:
        if self._me_id is None:
            raise RuntimeError("driver not entered")
        return self._me_id

    def send(self, text: str) -> int:
        """group 에 메시지 send. 반환: msg.id (driver side numbering)."""
        if not self._client:
            raise RuntimeError("driver not entered")
        msg = self._client.send_message(self.group_id, text)
        return msg.id

    def get_recent_after(
        self,
        after_msg_id: int,
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.5,
        limit: int = 50,
    ) -> list[dict]:
        """after_msg_id 보다 큰 msg id 수집 (oldest-first). timeout 까지 polling.

        반환 dict 키: id, sender_id, text, date.
        """
        if not self._client:
            raise RuntimeError("driver not entered")
        deadline = time.time() + timeout
        while True:
            msgs = list(
                self._client.iter_messages(self.group_id, min_id=after_msg_id, limit=limit)
            )
            if msgs:
                return [
                    {
                        "id": m.id,
                        "sender_id": m.sender_id,
                        "text": m.text or "",
                        "date": m.date.isoformat() if m.date else None,
                    }
                    for m in reversed(msgs)
                ]
            if time.time() >= deadline:
                return []
            time.sleep(poll_interval)

    def get_recent(self, limit: int = 10) -> list[dict]:
        if not self._client:
            raise RuntimeError("driver not entered")
        msgs = list(self._client.iter_messages(self.group_id, limit=limit))
        return [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "text": m.text or "",
                "date": m.date.isoformat() if m.date else None,
            }
            for m in reversed(msgs)
        ]

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._client:
            self._client.disconnect()
            self._client = None


def main_sanity() -> int:
    """CLI sanity check: driver send → 5초 polling → recent 출력."""
    import json

    marker = f"driver-sanity-{int(time.time())}"
    with TelegramDriver() as d:
        print(json.dumps({"event": "driver_ready", "me_id": d.me_id, "group_id": d.group_id}))
        msg_id = d.send(f"@openclaw_audit_sut_bot {marker}")
        print(json.dumps({"event": "sent", "msg_id": msg_id, "marker": marker}))
        time.sleep(2)
        recent = d.get_recent_after(msg_id - 1, timeout=2)
        print(json.dumps({"event": "recent_after", "count": len(recent), "messages": recent}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_sanity())
