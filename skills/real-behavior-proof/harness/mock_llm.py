#!/usr/bin/env python3
"""mock LLM server (openai-responses + chat/completions) subprocess lifecycle.

scripts/e2e/mock-openai-server.mjs (openclaw 의 e2e harness 가 검증된 mock) 를 그대로
spawn. audit 측은 port allocation + /health ready check + stdout 캡처 + cleanup 만.

audit 가 mock-openai-server.mjs 자체를 수정하지 않는다 (production-faithful 보존).
fault injection 이 필요하면 후속 시나리오에서 *별도* mock 작성하거나 env 변수 추가.

사용:
  with mock_llm_server() as mock:
      port = mock["port"]
      # ... port 로 openai client 가 향하도록 config 작성, openclaw spawn
      # cleanup 시 자동 terminate
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

OPENCLAW_REPO = Path("/Users/lucas/Project/openclaw")
MOCK_SCRIPT_DEFAULT = OPENCLAW_REPO / "scripts" / "e2e" / "mock-openai-server.mjs"
PORT_RANGE = (18800, 18899)
READY_TIMEOUT_SEC = 10.0


def _alloc_free_port(start: int = PORT_RANGE[0], end: int = PORT_RANGE[1]) -> int:
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"no free port in range {start}-{end}")


def _wait_for_health(port: int, *, deadline: float, proc: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"mock-openai server exited prematurely (rc={proc.returncode}). "
                "stdout/stderr 캡처 확인."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise TimeoutError(f"mock-openai not ready at {url} within {READY_TIMEOUT_SEC}s")


@contextmanager
def mock_llm_server(
    *,
    success_marker: str = "OPENCLAW_E2E_OK",
    script_path: Path | None = None,
    request_log: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[dict]:
    """mock-openai-server.mjs subprocess context.

    Args:
        success_marker: response 본문 marker (driver 가 sut bot 응답 식별용).
        script_path: mock script override (기본: openclaw repo).
        request_log: 지정 시 mock 이 받은 요청을 JSONL append (per-trial 비교용).
        extra_env: 추가 환경변수 (fault injection 시나리오용).

    Yields:
        {"port": int, "proc": Popen, "stdout_log": Path, "stderr_log": Path,
         "success_marker": str, "request_log": Path | None}
    """
    script = script_path or MOCK_SCRIPT_DEFAULT
    if not script.exists():
        raise RuntimeError(f"mock-openai script not found: {script}")

    port = _alloc_free_port()
    stdout_log = Path(tempfile.mkstemp(prefix="mock-llm-stdout-", suffix=".log")[1])
    stderr_log = Path(tempfile.mkstemp(prefix="mock-llm-stderr-", suffix=".log")[1])

    env = os.environ.copy()
    env["MOCK_PORT"] = str(port)
    env["SUCCESS_MARKER"] = success_marker
    if request_log:
        env["MOCK_REQUEST_LOG"] = str(request_log)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.Popen(
        ["node", str(script)],
        env=env,
        stdout=stdout_log.open("wb"),
        stderr=stderr_log.open("wb"),
        cwd=str(OPENCLAW_REPO),
    )

    try:
        _wait_for_health(port, deadline=time.time() + READY_TIMEOUT_SEC, proc=proc)
        yield {
            "port": port,
            "proc": proc,
            "stdout_log": stdout_log,
            "stderr_log": stderr_log,
            "success_marker": success_marker,
            "request_log": request_log,
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def main_sanity() -> int:
    """CLI sanity: spawn mock + /health + /v1/responses POST + dump."""
    import json

    body = json.dumps({"model": "gpt-5.5", "input": "sanity OPENCLAW_E2E_OK", "stream": False}).encode()
    with mock_llm_server() as mock:
        port = mock["port"]
        print(json.dumps({"event": "ready", "port": port}))
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/responses",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read())
        # output[0].content[0].text 추출
        text = payload.get("output", [{}])[0].get("content", [{}])[0].get("text", "")
        print(json.dumps({"event": "responded", "text": text, "status": payload.get("status")}))
    print(json.dumps({"event": "cleanup_done"}))
    return 0


if __name__ == "__main__":
    sys.exit(main_sanity())
