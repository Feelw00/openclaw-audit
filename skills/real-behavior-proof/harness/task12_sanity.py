#!/usr/bin/env python3
"""Task 12 sanity — openclaw cli + telegram channel + mock LLM 한 바퀴.

목적: Task 10 (telegram_sut_home) + Task 11 (mock_llm) + telegram_driver 가 wire-level
로 실 작동하는지 검증. 결함 trigger 없이 happy path 만.

흐름:
  1. mock LLM server spawn (mock_llm_server)
  2. isolated openclaw home + sut config (telegram_sut_home)
  3. openclaw.mjs gateway subprocess spawn (env=sut env, sut bot token 주입)
  4. gateway 가 telegram channel listener 시작할 때까지 대기 (stdout grep)
  5. TelegramDriver 로 mention 메시지 send
  6. sut bot 응답 polling (get_recent_after)
  7. mock LLM 의 SUCCESS_MARKER 가 응답 본문에 포함되는지 확인
  8. cleanup

성공 기준: SUCCESS_MARKER 포함된 sut bot 응답 1건 이상 관찰.
실패 시: stdout/stderr log dump, mock request log dump.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from telegram_sut_home import telegram_sut_home  # noqa: E402
from mock_llm import mock_llm_server  # noqa: E402

OPENCLAW_REPO = Path("/Users/lucas/Project/openclaw")
OPENCLAW_ENTRY = OPENCLAW_REPO / "openclaw.mjs"
DRIVER_USER_ID = 8419869822  # telegram-e2e.md
GATEWAY_READY_TIMEOUT = 60.0  # gateway 부팅 + telegram listener 시작 대기
REPLY_TIMEOUT = 90.0  # mock LLM round-trip 여유


def load_secrets() -> dict[str, str]:
    env_file = Path.home() / ".openclaw-audit-secrets" / "telegram.env"
    out: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def wait_for_marker(log_path: Path, *, marker: str, deadline: float, proc: subprocess.Popen) -> bool:
    """stdout/stderr log 에 marker 문자열 등장 대기."""
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            content = log_path.read_text(errors="replace")
        except FileNotFoundError:
            content = ""
        if marker in content:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Task 12 sanity")
    ap.add_argument("--keep-home", action="store_true", help="cleanup 생략 (디버깅)")
    args = ap.parse_args()

    secrets = load_secrets()
    for k, v in secrets.items():
        os.environ[k] = v
    sut_token = secrets["OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN"]
    group_id = int(secrets["OPENCLAW_QA_TELEGRAM_GROUP_ID"])

    # telegram_driver lazy import (telethon 의존)
    from telegram_driver import TelegramDriver  # noqa: E402

    success_marker = f"OPENCLAW_E2E_OK_T12_{int(time.time())}"
    mock_req_log = Path(tempfile.mkstemp(prefix="mock-req-", suffix=".jsonl")[1])

    proc: subprocess.Popen | None = None
    # log 파일은 isolated_home cleanup 와 무관하게 살아남도록 격리 외부 tempfile 로
    stdout_path = Path(tempfile.mkstemp(prefix="t12-openclaw-stdout-", suffix=".log")[1])
    stderr_path = Path(tempfile.mkstemp(prefix="t12-openclaw-stderr-", suffix=".log")[1])
    sut_home: Path | None = None
    success = False

    try:
        with mock_llm_server(success_marker=success_marker, request_log=mock_req_log) as mock:
            print(json.dumps({"event": "mock_llm_ready", "port": mock["port"], "marker": success_marker}))

            with telegram_sut_home(
                sut_bot_token=sut_token,
                mock_llm_port=mock["port"],
                telegram_group_id=group_id,
                driver_user_id=DRIVER_USER_ID,
                proof_id="t12-sanity",
            ) as sut:
                sut_home = sut["home"]
                print(json.dumps({
                    "event": "sut_home_ready",
                    "home": str(sut_home),
                    "gateway_port": sut["gateway_port"],
                }))

                # openclaw gateway spawn
                env = sut["env"]
                env["OPENCLAW_HOME"] = str(sut_home)
                proc = subprocess.Popen(
                    ["node", str(OPENCLAW_ENTRY), "gateway", "--verbose"],
                    env=env,
                    stdout=stdout_path.open("wb"),
                    stderr=stderr_path.open("wb"),
                    cwd=str(OPENCLAW_REPO),
                )
                print(json.dumps({"event": "openclaw_spawned", "pid": proc.pid}))

                # gateway ready 대기 — telegram listener / channel ready signal grep
                # 우선 일반적 'gateway listening' / 'channel.*telegram' 패턴 시도
                ready_deadline = time.time() + GATEWAY_READY_TIMEOUT
                ready_markers = ("telegram", "listening", "ready", "bound to")
                gateway_ready = False
                while time.time() < ready_deadline and proc.poll() is None:
                    try:
                        log = stdout_path.read_text(errors="replace") + stderr_path.read_text(errors="replace")
                    except FileNotFoundError:
                        log = ""
                    if any(m in log.lower() for m in ready_markers):
                        gateway_ready = True
                        break
                    time.sleep(1.0)

                if not gateway_ready or proc.poll() is not None:
                    print(json.dumps({
                        "event": "gateway_not_ready",
                        "exited": proc.poll() is not None,
                        "rc": proc.returncode if proc.poll() is not None else None,
                    }))
                    return 2

                print(json.dumps({"event": "gateway_ready", "elapsed_s": int(time.time() - (ready_deadline - GATEWAY_READY_TIMEOUT))}))

                # driver send + sut bot reply polling
                with TelegramDriver() as d:
                    marker_text = f"@openclaw_audit_sut_bot Reply with exactly {success_marker}"
                    msg_id = d.send(marker_text)
                    print(json.dumps({"event": "driver_sent", "msg_id": msg_id}))

                    replies = d.get_recent_after(msg_id, timeout=REPLY_TIMEOUT, poll_interval=2.0)
                    sut_replies = [r for r in replies if r["sender_id"] != d.me_id]
                    matched = [r for r in sut_replies if success_marker in r["text"]]

                    print(json.dumps({
                        "event": "polled",
                        "total_msgs": len(replies),
                        "sut_replies": len(sut_replies),
                        "matched": len(matched),
                        "sample_replies": [{"sender": r["sender_id"], "text": r["text"][:200]} for r in sut_replies[:3]],
                    }, default=str))

                    success = len(matched) > 0
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

        # 로그 path 항상 노출 (성공/실패 무관) — debugging 편의
        print(json.dumps({
            "event": "logs",
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "mock_req": str(mock_req_log),
        }))
        if not success:
            if stdout_path.exists():
                print("\n--- openclaw stdout (tail 4000) ---")
                print(stdout_path.read_text(errors="replace")[-4000:])
            if stderr_path.exists():
                print("\n--- openclaw stderr (tail 4000) ---")
                print(stderr_path.read_text(errors="replace")[-4000:])
            if mock_req_log.exists() and mock_req_log.stat().st_size > 0:
                print("\n--- mock LLM request log (tail 1500) ---")
                print(mock_req_log.read_text()[-1500:])

        if args.keep_home and sut_home:
            print(json.dumps({"event": "kept_home", "path": str(sut_home.parent)}))

    print(json.dumps({"event": "done", "success": success}))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
