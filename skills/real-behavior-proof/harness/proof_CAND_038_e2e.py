#!/usr/bin/env python3
"""CAND-038 e2e proof — gateway WS close handler 가 chatAbortControllers 미 iterate.

결함 (issue-candidates/CAND-038.md):
  src/gateway/server/ws-connection.ts:351-421 close handler 가
  session/node/presence/nodeWake 만 cleanup, chatAbortControllers 의 ownerConnId
  매칭 entry 는 abort 안 함.

  → user disconnect (ws.close) 후 chat runner 가 LLM 호출 / tool 실행 / 외부
    API call 끝까지 진행. maintenance interval sweep (>2분 expiresAtMs floor) 까지
    abort 없음.

production-faithful e2e trigger:
  1. mock LLM (stream-hold-then-complete) — first /v1/responses 요청에 5-30초 hold
     후 SSE stream 시작 → 완전 응답 송신.
  2. SUT 부팅 (openclaw.mjs start) — isolated_home + gateway loopback + auth=none
     + openai mock provider + agents.codex / openai plugins 활성화.
  3. audit ws client (production gateway/client.ts 의 RequestFrame 패턴 manual)
     → connect.challenge → connect frame (auth.mode=none 이므로 payload 없음) →
     hello 수신.
  4. chat.send RPC 발사 (sessionKey/idempotencyKey audit-generated) →
     SUT 가 chat runner 시작 → mock LLM 호출 → mock hold 시작.
  5. 1-3초 후 audit ws.close (TCP-level close, code=1000).
  6. SUT close handler 실행 (ws-connection.ts:351-421) — 결함 시 chatAbortControllers
     미 iterate.
  7. mock hold 끝 (15초) → SSE stream 시작 → SUT chat runner 가 stream 받는가?
     - without-fix: stream 끝까지 받음 (LLM 호출 / tool 진행). mock-openai-server.mjs
       가 stream end 시점에 client connection alive 관측.
     - with-fix: SUT 의 LLM client 가 controller.signal.aborted → fetch abort →
       mock 측에서 req close (client disconnect) 관측.

측정 (mock-openai-server.mjs MOCK_REQUEST_LOG):
  - request_started: chat.send 직후
  - stream_started: hold 끝 시점
  - stream_completed | client_disconnected: stream 끝 또는 client 가 끊음

CAVEAT — 본 세션 작성된 skeleton. 실 실행 / 디버깅은 다음 세션.
미해결 의문:
  - audit-side sessionKey 가 자동 새 session 생성 path 인지 또는 sessions.create RPC 선행 필요한가
  - mock-openai-server.mjs 가 req close 감지 + 로깅 enhance 필요한가 (현 기록 항목 미상)
  - openclaw cli start 가 gateway server + agent runtime 까지 부팅하는 entry 인지 또는
    `openclaw gateway start` 별도 명령인지 (proof_CAND_032 패턴은 openclaw.mjs 직접 spawn)
  - codex / openai plugin 의 dependency (codex-cli binary 또는 등) 가 isolated_home 환경에
    설치 필요한지
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from env_isolate import isolated_home  # noqa: E402
from mock_llm import mock_llm_server  # noqa: E402

OPENCLAW_REPO = Path("/Users/lucas/Project/openclaw")
OPENCLAW_ENTRY = OPENCLAW_REPO / "openclaw.mjs"
GATEWAY_READY_TIMEOUT = 60.0
HOLD_MS_DEFAULT = 15000
WS_CLOSE_AFTER_SEND_SEC = 2.0
POST_CLOSE_WAIT_SEC = 30.0  # hold 끝 (15s) + stream + 측정 여유


def _build_audit_ws_probe(*, gateway_port: int, hold_ms: int) -> str:
    """tsx 로 직접 실행하는 ws client probe (production GatewayClient 동등 manual).

    개요:
      - ws connect → connect.challenge 수신 → connect frame (auth.mode=none 이라 payload 없음)
      - hello.ok 수신 → 연결 완료
      - chat.send RPC 발사 (random sessionKey + idempotencyKey)
      - WS_CLOSE_AFTER_SEND_SEC 후 socket.close()
      - stdout 에 JSON: {connected, helloOk, chatSendOk, closedAt}
    """
    return f"""\
import {{ WebSocket }} from 'ws';

const url = 'ws://127.0.0.1:{gateway_port}/ws';
const ws = new WebSocket(url);

const out: any = {{
  connected: false,
  helloOk: false,
  chatSendResponse: null,
  chatSendError: null,
  closedAt: null,
}};

function send(obj: any) {{
  ws.send(JSON.stringify(obj));
}}

ws.on('open', () => {{
  out.connected = true;
}});

ws.on('message', (raw: any) => {{
  let frame: any;
  try {{ frame = JSON.parse(String(raw)); }} catch {{ return; }}
  if (frame.type === 'event' && frame.event === 'connect.challenge') {{
    // auth.mode=none: connect frame 의 deviceAuth payload 생략
    send({{
      type: 'req',
      id: crypto.randomUUID(),
      method: 'connect',
      params: {{
        clientName: 'audit-ws-probe',
        clientMode: 'cli',
        protocolVersion: 1,
        nonceEcho: frame.payload.nonce,
      }},
    }});
  }} else if (frame.type === 'res' && frame.result && frame.result.helloOk) {{
    out.helloOk = true;
    // chat.send 발사
    send({{
      type: 'req',
      id: crypto.randomUUID(),
      method: 'chat.send',
      params: {{
        sessionKey: 'audit-probe-' + Date.now(),
        message: 'probe message — long enough to trigger LLM stream',
        idempotencyKey: crypto.randomUUID(),
      }},
    }});
  }} else if (frame.type === 'res' && frame.method === 'chat.send') {{
    out.chatSendResponse = frame;
  }}
}});

ws.on('error', (e: any) => {{
  out.chatSendError = String(e);
}});

setTimeout(() => {{
  out.closedAt = Date.now();
  ws.close(1000, 'audit-test-close');
  // hold + stream 시간 확보
  setTimeout(() => {{
    console.log(JSON.stringify(out));
    process.exit(0);
  }}, {hold_ms} + 5000);
}}, {int(WS_CLOSE_AFTER_SEND_SEC * 1000)});

setTimeout(() => {{
  console.log(JSON.stringify({{ ...out, timeout: true }}));
  process.exit(0);
}}, {hold_ms} + 20000);
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="CAND-038 e2e proof (skeleton)")
    ap.add_argument("--hold-ms", type=int, default=HOLD_MS_DEFAULT)
    ap.add_argument("--keep-home", action="store_true", help="cleanup 생략 (디버깅)")
    args = ap.parse_args()

    measurements: dict = {
        "scenario": "proof-CAND-038-e2e",
        "trials": 1,
        "skeleton": True,
        "phase_reached": None,
    }

    success_marker = f"OPENCLAW_E2E_OK_C038_{int(time.time())}"
    mock_req_log = Path(tempfile.mkstemp(prefix="mock-req-c038-", suffix=".jsonl")[1])
    stdout_path = Path(tempfile.mkstemp(prefix="c038-openclaw-stdout-", suffix=".log")[1])
    stderr_path = Path(tempfile.mkstemp(prefix="c038-openclaw-stderr-", suffix=".log")[1])
    probe_stdout_path = Path(tempfile.mkstemp(prefix="c038-probe-stdout-", suffix=".log")[1])

    sut_proc: subprocess.Popen | None = None
    try:
        with isolated_home(require_oauth=False, proof_id="c038") as env_isol:
            measurements["phase_reached"] = "isolated_home"

            # sut config override — auth.mode=none + gateway.bind=loopback
            home = env_isol["home"]
            cfg_path = home / "openclaw.json"
            cfg = json.loads(cfg_path.read_text())
            cfg.setdefault("gateway", {})["bind"] = "loopback"
            cfg["gateway"]["auth"] = {"mode": "none"}

            with mock_llm_server() as mock:
                measurements["phase_reached"] = "mock_llm_started"
                mock_port = mock["port"]

                # openai provider 를 mock 으로
                cfg.setdefault("models", {}).setdefault("providers", {})["openai"] = {
                    "api": "openai-responses",
                    "apiKey": {"source": "env", "provider": "default", "id": "OPENAI_API_KEY"},
                    "baseUrl": f"http://127.0.0.1:{mock_port}/v1",
                    "request": {"allowPrivateNetwork": True},
                }
                cfg_path.write_text(json.dumps(cfg, indent=2))

                sut_env = env_isol["env"].copy()
                sut_env["OPENAI_API_KEY"] = "sk-mock-c038"
                sut_env["MOCK_REQUEST_LOG"] = str(mock_req_log)
                sut_env["MOCK_PORT"] = str(mock_port)
                sut_env["SUCCESS_MARKER"] = success_marker

                # SUT spawn
                with stdout_path.open("w") as so, stderr_path.open("w") as se:
                    sut_proc = subprocess.Popen(
                        ["node", str(OPENCLAW_ENTRY), "gateway", "start", "--auth", "none"],
                        env=sut_env,
                        stdout=so,
                        stderr=se,
                    )
                measurements["phase_reached"] = "sut_spawned"

                # gateway ready 대기 (loopback port listen)
                gateway_port = env_isol["gateway_port"]
                deadline = time.time() + GATEWAY_READY_TIMEOUT
                ready = False
                while time.time() < deadline:
                    if sut_proc.poll() is not None:
                        measurements["error"] = f"sut exited early rc={sut_proc.returncode}"
                        measurements["stderr_tail"] = stderr_path.read_text()[-2000:]
                        return 1
                    import socket
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        try:
                            s.connect(("127.0.0.1", gateway_port))
                            ready = True
                            break
                        except OSError:
                            time.sleep(0.3)
                if not ready:
                    measurements["error"] = "gateway port not listening"
                    return 1
                measurements["phase_reached"] = "gateway_ready"

                # audit ws probe (tsx) — production code base 에서 ws client manual
                probe_src = _build_audit_ws_probe(gateway_port=gateway_port, hold_ms=args.hold_ms)
                tsx_bin = OPENCLAW_REPO / "node_modules" / ".bin" / "tsx"
                if not tsx_bin.exists():
                    measurements["error"] = f"tsx missing: {tsx_bin}"
                    return 1
                with tempfile.NamedTemporaryFile(
                    suffix=".ts", mode="w", delete=False, dir=str(OPENCLAW_REPO)
                ) as f:
                    f.write(probe_src)
                    probe_path = f.name

                try:
                    with probe_stdout_path.open("w") as po:
                        probe_proc = subprocess.run(
                            [str(tsx_bin), probe_path],
                            cwd=str(OPENCLAW_REPO),
                            stdout=po,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=args.hold_ms / 1000 + 60,
                        )
                    measurements["phase_reached"] = "probe_ran"
                    probe_stdout = probe_stdout_path.read_text()
                    measurements["probe_stdout"] = probe_stdout[-2000:]
                    measurements["probe_stderr"] = probe_proc.stderr[-1000:] if probe_proc.stderr else ""
                finally:
                    try: os.unlink(probe_path)
                    except Exception: pass

                # mock LLM request log 분석
                if mock_req_log.exists():
                    measurements["mock_log_lines"] = mock_req_log.read_text().count("\n")
                    measurements["mock_log_tail"] = mock_req_log.read_text()[-2000:]

                # SUT stdout/stderr 분석
                measurements["sut_stdout_tail"] = stdout_path.read_text()[-2000:]
                measurements["sut_stderr_tail"] = stderr_path.read_text()[-2000:]

                # TODO: ws.close 후 mock LLM 의 후속 request 가 끊겼는지 측정 (with-fix vs without-fix 비교)
                # 본 skeleton 은 측정 path 만 자리 잡음. 다음 세션에 evaluate_pre 작성.

    finally:
        if sut_proc and sut_proc.poll() is None:
            sut_proc.terminate()
            try: sut_proc.wait(timeout=10)
            except subprocess.TimeoutExpired: sut_proc.kill()

    print(json.dumps(measurements, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
