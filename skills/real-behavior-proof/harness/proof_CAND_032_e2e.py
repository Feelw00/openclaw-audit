#!/usr/bin/env python3
"""CAND-032 e2e proof — backend.queueMessage 의 .catch 부재 unhandled rejection.

결함 (issue-candidates/CAND-032.md):
  src/auto-reply/reply/reply-run-registry.ts:505
    void backend.queueMessage(text);  // .catch 부재
  backend.queueMessage 구현 (pi-embedded-runner/run/attempt.ts:2806-2810):
    queueMessage: async (text) => { await activeSession.steer(text); }
  activeSession.steer 가 throw 시 catch 없어서 unhandledRejection.

production-faithful e2e trigger:
  1. mock LLM (fault: stream-hold-then-close) — 첫 응답 SSE 시작 후 5초 hold → abrupt close
  2. SUT 부팅 + telegram channel listen
  3. driver 가 첫 mention 메시지 send → SUT 가 LLM 호출 → mock 이 hold 시작
     (SUT 의 backend.isStreaming === true 유지)
  4. driver 가 1초 후 두 번째 mention 메시지 send
     → reply-run-registry 의 queueReplyRunMessage 호출
     → backend.queueMessage 호출
     → activeSession.steer 호출
  5. mock hold 끝 (5초) → res.destroy → stream error 발생
  6. activeSession.steer 가 stream error propagate → Promise reject
  7. .catch 부재 → infra/unhandled-rejections.ts handler 호출
  8. 분류 (transient → warn-only / non-transient → process.exit(1))

측정:
  - stdout/stderr grep `[openclaw].*nhandled` (Unhandled / Non-fatal / FATAL)
  - process exit code (transient 면 살아있음, non-transient 면 1)
  - mock LLM 의 /v1/responses 카운트 (2 이상이면 두 LLM 호출 시도 확인)
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
FAULT_MOCK_SCRIPT = THIS_DIR / "mock_openai_fault.mjs"
DRIVER_USER_ID = 8419869822
GATEWAY_READY_TIMEOUT = 60.0
INTER_MESSAGE_GAP_SEC = 0.8  # 좁은 race window 노릴 때 debounce 회피
POST_FAULT_WAIT_SEC = 60.0
HOLD_MS = 30000  # stream-hold-then-close 모드에서만 의미 있음

UNHANDLED_PATTERN = re.compile(
    r"\[openclaw\].*(?:unhandled\s+promise\s+rejection|fatal\s+unhandled\s+rejection|non-fatal\s+unhandled\s+rejection|suppressed\s+aborterror)",
    re.IGNORECASE,
)


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


def main() -> int:
    ap = argparse.ArgumentParser(description="CAND-032 e2e proof")
    ap.add_argument(
        "--fault-mode",
        default="stream-hold-then-close",
        choices=["stream-hold-then-close", "stream-immediate-close", "stream-error-event"],
    )
    ap.add_argument("--hold-ms", type=int, default=HOLD_MS)
    args = ap.parse_args()

    secrets = load_secrets()
    for k, v in secrets.items():
        os.environ[k] = v
    sut_token = secrets["OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN"]
    group_id = int(secrets["OPENCLAW_QA_TELEGRAM_GROUP_ID"])

    from telegram_driver import TelegramDriver  # noqa: E402

    success_marker = f"OPENCLAW_E2E_OK_C032_{int(time.time())}"
    mock_req_log = Path(tempfile.mkstemp(prefix="mock-req-c032-", suffix=".jsonl")[1])
    stdout_path = Path(tempfile.mkstemp(prefix="c032-openclaw-stdout-", suffix=".log")[1])
    stderr_path = Path(tempfile.mkstemp(prefix="c032-openclaw-stderr-", suffix=".log")[1])

    proc: subprocess.Popen | None = None
    measurements: dict = {
        "scenario": "proof-CAND-032-e2e",
        "fault_mode": args.fault_mode,
        "hold_ms": args.hold_ms,
    }

    try:
        with mock_llm_server(
            success_marker=success_marker,
            request_log=mock_req_log,
            script_path=FAULT_MOCK_SCRIPT,
            extra_env={
                "MOCK_FAULT_MODE": args.fault_mode,
                "MOCK_HOLD_MS": str(args.hold_ms),
                "REQUEST_INDEX_FAULT": "1",
            },
        ) as mock:
            print(json.dumps({"event": "mock_fault_ready", "port": mock["port"]}))

            with telegram_sut_home(
                sut_bot_token=sut_token,
                mock_llm_port=mock["port"],
                telegram_group_id=group_id,
                driver_user_id=DRIVER_USER_ID,
                proof_id="c032-e2e",
            ) as sut:
                env = sut["env"]
                proc = subprocess.Popen(
                    ["node", str(OPENCLAW_ENTRY), "gateway", "--verbose"],
                    env=env,
                    stdout=stdout_path.open("wb"),
                    stderr=stderr_path.open("wb"),
                    cwd=str(OPENCLAW_REPO),
                )
                print(json.dumps({"event": "openclaw_spawned", "pid": proc.pid}))

                # gateway ready 대기 (Task 12 패턴)
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
                    measurements["status"] = "gateway_not_ready"
                    measurements["exit_code"] = proc.returncode
                    return _finalize(measurements, stdout_path, stderr_path, mock_req_log, success=False)

                print(json.dumps({"event": "gateway_ready"}))

                # driver: 첫 메시지 send → SUT 가 LLM 호출 → mock hold 시작
                with TelegramDriver() as d:
                    msg1_text = f"@openclaw_audit_sut_bot first_{success_marker}"
                    msg1_id = d.send(msg1_text)
                    print(json.dumps({"event": "driver_sent_first", "msg_id": msg1_id}))

                    # SUT 가 LLM 호출 (mock 의 /v1/responses 1번째 — hold 진입) 까지 대기
                    time.sleep(INTER_MESSAGE_GAP_SEC)

                    # 두 번째 메시지 — queueReplyRunMessage trigger
                    msg2_text = f"@openclaw_audit_sut_bot second_{success_marker}"
                    msg2_id = d.send(msg2_text)
                    print(json.dumps({"event": "driver_sent_second", "msg_id": msg2_id}))

                    # mock hold 끝 + unhandled rejection 발화 + classify 완료 대기
                    time.sleep(POST_FAULT_WAIT_SEC)

                # 측정 — log scan
                stdout_text = stdout_path.read_text(errors="replace")
                stderr_text = stderr_path.read_text(errors="replace")
                combined = stdout_text + "\n" + stderr_text

                matches = UNHANDLED_PATTERN.findall(combined)
                measurements["unhandled_log_match_count"] = len(matches)
                measurements["unhandled_log_samples"] = matches[:5]

                # reply-run path 활성화 진단 — pi-embedded-runner runs.ts:206 의 target=reply_run
                # 또는 logMessageQueued 호출 흔적
                reply_run_path_active = 'target":"reply_run"' in combined or "target=reply_run" in combined
                measurements["reply_run_path_active"] = reply_run_path_active

                # message processed / queue 진단 흔적
                measurements["queue_message_failed_lines"] = [
                    line for line in combined.splitlines() if "queue message" in line.lower()
                ][:10]

                # mock /v1/responses 호출 횟수 (JSON 공백 없음 패턴 맞춤)
                if mock_req_log.exists():
                    responses_calls = sum(
                        1
                        for line in mock_req_log.read_text().splitlines()
                        if line and '"path":"/v1/responses"' in line
                    )
                    measurements["mock_responses_calls"] = responses_calls

                # process exit code (SIGTERM 보낸 후 대기 — 우리 cleanup 전에 이미 죽었으면 그 코드)
                if proc.poll() is not None:
                    measurements["openclaw_exited_during_run"] = True
                    measurements["openclaw_exit_code_during_run"] = proc.returncode
                else:
                    measurements["openclaw_exited_during_run"] = False

                # 결함 발화 판정:
                # - log match 발생 또는 process 가 unhandled rejection 으로 exit(1)
                triggered = (
                    measurements["unhandled_log_match_count"] > 0
                    or (measurements.get("openclaw_exited_during_run") and proc.returncode == 1)
                )
                measurements["status"] = "collected" if triggered else "unreproducible"

                return _finalize(measurements, stdout_path, stderr_path, mock_req_log, success=triggered)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def _finalize(measurements: dict, stdout_path: Path, stderr_path: Path, mock_req_log: Path, *, success: bool) -> int:
    measurements["logs"] = {
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "mock_req": str(mock_req_log),
    }
    print(json.dumps({"event": "measurements", **measurements}, default=str))

    if not success:
        if stdout_path.exists():
            print("\n--- stdout (tail 6000) ---")
            print(stdout_path.read_text(errors="replace")[-6000:])
        if stderr_path.exists():
            print("\n--- stderr (tail 6000) ---")
            print(stderr_path.read_text(errors="replace")[-6000:])

    print(json.dumps({"event": "done", "success": success, "status": measurements.get("status")}))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
