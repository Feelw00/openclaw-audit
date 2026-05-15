#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-038-e2e.py

CAND-038 (gateway/ws-connection close handler 가 chatAbortControllers 미 iterate) e2e proof.

production execution path:
1. ws connect (audit probe → gateway loopback) → device pairing + connect.challenge/response
2. chat.send RPC → registerChatAbortController(ownerConnId=connId) + LLM 호출 시작 →
   mock LLM 이 hold (5s) 시작
3. hold 도중 audit probe 가 ws.close (TCP-level close, code=1000)
4. SUT 의 ws-connection.ts:351-421 close handler 실행 — **결함**: chatAbortControllers
   ownerConnId 매칭 entry abort 안 함.
5. without-fix: LLM client (fetch) 가 abort 없이 hold 끝까지 진행 → mock 에서 stream
   complete 응답 → mock req close 시점 completed=true.
6. with-fix: SUT close handler 가 chatAbortControllers iterate + abort →
   LLM client 가 fetch abort → mock 측 `req.on("close")` fire 시점이 hold 도중 +
   completed=false.

측정:
- mock LLM (mock_openai_cand038.mjs) 의 MOCK_REQUEST_LOG JSONL.
- `event:"client_disconnected", completed:false` 출현 → with-fix 발현.
- `event:"stream_completed"` 또는 `client_disconnected, completed:true` → without-fix.

REQUIRES_EXTERNAL_DEP = True (실 gateway 부팅 + mock LLM HTTP server + ws connect chain).

caveat:
- chat.send 가 실 backend chain (codex/openai plugin) 까지 작동해야 registerChatAbortController
  진입. plugins 9개 (acpx 포함) 가 env_isolate 의 default config 로 로드되는지 확인 필요.
- 첫 시도에서 chain 작동 안 하면 backend 시작 단계까지 추적 (run.py 의 method handler 또는
  agent.runChat path) — 디버깅 cost 잠재적으로 큼.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = True
SCENARIO_NAME = "proof-CAND-038-e2e"
DEFAULT_TRIALS = 1

HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS_DIR))

# device pairing helper. dash 가 없는 module name 으로 import.
_dp_spec = importlib.util.spec_from_file_location(
    "_device_pairing_helper",
    str(HARNESS_DIR / "device_pairing.py"),
)
_dp_mod = importlib.util.module_from_spec(_dp_spec)
_dp_spec.loader.exec_module(_dp_mod)
seed_paired_device = _dp_mod.seed_paired_device

# ANSI strip for log inspection
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# server-startup-post-attach.ts:818 `[gateway] ready` marker
READY_MARKER_PATTERN = re.compile(r"\[gateway\]\s+ready\b")

GATEWAY_READY_TIMEOUT_SEC = 60.0
WS_CLOSE_AFTER_SEND_SEC = 2.0
MOCK_HOLD_MS = 5000
POST_CLOSE_WAIT_SEC = 12.0
HARNESS_REPO = Path("/Users/lucas/Project/openclaw")


def _alloc_free_port(start: int = 18000, end: int = 18999) -> int:
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}-{end}")


def _wait_for_port(port: int, deadline: float, proc: subprocess.Popen) -> bool:
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.1)
    return False


def _wait_for_ready_marker(
    proc: subprocess.Popen,
    deadline: float,
    stdout_path: Path,
    stderr_path: Path,
) -> bool:
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        for fp in (stdout_path, stderr_path):
            try:
                raw = fp.read_text(errors="replace")
            except FileNotFoundError:
                continue
            text = _ANSI_RE.sub("", raw)
            if READY_MARKER_PATTERN.search(text):
                return True
        time.sleep(0.1)
    return False


def _build_ws_probe_ts(
    *,
    gateway_port: int,
    device: dict,
    ws_close_after_sec: float,
    post_close_wait_sec: float,
) -> str:
    """audit-side WebSocket probe (TS, tsx 로 실행).

    connect handshake (connect.challenge → device payload v2 signed → hello-ok) →
    chat.send → ws_close_after_sec 후 ws.close → post_close_wait_sec 대기 후 결과 stdout JSON.
    """
    privateKeyPemJson = json.dumps(device["privateKeyPem"])
    publicKeyRawJson = json.dumps(device["publicKeyRawBase64Url"])
    deviceIdJson = json.dumps(device["deviceId"])
    tokenJson = json.dumps(device["token"])
    role = device["role"]
    scopes = device["scopes"]
    scopes_json = json.dumps(scopes)
    role_json = json.dumps(role)
    scopes_csv = ",".join(scopes)

    return textwrap.dedent(
        f"""\
        import crypto from "node:crypto";
        import {{ WebSocket }} from "ws";

        const privateKeyPem = {privateKeyPemJson};
        const publicKeyRaw = {publicKeyRawJson};
        const deviceId = {deviceIdJson};
        const deviceToken = {tokenJson};
        const role = {role_json};
        const scopes = {scopes_json};
        const scopesCsv = {json.dumps(scopes_csv)};

        function base64UrlEncode(buf: Buffer): string {{
          return buf.toString("base64").replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
        }}

        function buildPayloadV2(signedAtMs: number, nonce: string): string {{
          return [
            "v2", deviceId, "cli", "cli", role, scopesCsv,
            String(signedAtMs), deviceToken, nonce,
          ].join("|");
        }}

        function signPayload(payload: string): string {{
          const key = crypto.createPrivateKey(privateKeyPem);
          const sig = crypto.sign(null, Buffer.from(payload, "utf8"), key);
          return base64UrlEncode(sig);
        }}

        const url = "ws://127.0.0.1:{gateway_port}/ws";
        const ws = new WebSocket(url);

        const out: any = {{
          phase: "connecting",
          connected: false,
          challengeReceived: false,
          connectAck: null,
          helloOk: false,
          chatSendAck: null,
          chatSendErr: null,
          wsClosedAt: null,
          wsCloseCode: null,
          frames: [] as any[],
          errors: [] as any[],
        }};

        function send(obj: any) {{
          ws.send(JSON.stringify(obj));
        }}

        const sessionKey = "audit-c038-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
        let connectAckId: string | null = null;
        let chatSendId: string | null = null;
        let helloReceived = false;

        ws.on("open", () => {{
          out.connected = true;
          out.phase = "connected";
        }});

        ws.on("close", (code: number) => {{
          out.wsCloseCode = code;
        }});

        ws.on("error", (e: any) => {{
          out.errors.push(String(e?.message ?? e));
        }});

        ws.on("message", (raw: any) => {{
          let frame: any;
          try {{ frame = JSON.parse(String(raw)); }} catch {{ return; }}
          out.frames.push({{
            ts: Date.now(),
            type: frame.type,
            event: frame.event,
            method: frame.method,
            id: frame.id,
            ok: frame.ok,
            error: frame.error?.code,
          }});

          if (frame.type === "event" && frame.event === "connect.challenge") {{
            out.challengeReceived = true;
            const nonce = String(frame.payload?.nonce ?? "");
            if (!nonce) {{
              out.errors.push("challenge.nonce missing");
              return;
            }}
            const signedAtMs = Date.now();
            const payload = buildPayloadV2(signedAtMs, nonce);
            const signature = signPayload(payload);
            connectAckId = crypto.randomUUID();
            send({{
              type: "req",
              id: connectAckId,
              method: "connect",
              params: {{
                minProtocol: 1,
                maxProtocol: 4,
                client: {{
                  id: "cli",
                  version: "1.0.0",
                  platform: "darwin",
                  mode: "cli",
                }},
                role,
                scopes,
                device: {{
                  id: deviceId,
                  publicKey: publicKeyRaw,
                  signature,
                  signedAt: signedAtMs,
                  nonce,
                }},
                auth: {{
                  token: deviceToken,
                }},
              }},
            }});
            out.phase = "connect_sent";
            return;
          }}

          if (frame.type === "res" && frame.id === connectAckId) {{
            out.connectAck = {{ ok: frame.ok, error: frame.error?.code, payload: frame.payload }};
            out.phase = "connect_acked";
            if (frame.ok && frame.payload?.type === "hello-ok") {{
              out.helloOk = true;
              helloReceived = true;
              // chat.send 발사
              chatSendId = crypto.randomUUID();
              send({{
                type: "req",
                id: chatSendId,
                method: "chat.send",
                params: {{
                  sessionKey,
                  message: "probe message — CAND-038 e2e (long enough to trigger LLM stream).",
                  idempotencyKey: crypto.randomUUID(),
                }},
              }});
              out.phase = "chat_send_dispatched";
              setTimeout(() => {{
                out.wsClosedAt = Date.now();
                ws.close(1000, "audit-cand038-close");
                out.phase = "ws_closed";
                setTimeout(() => {{
                  process.stdout.write(JSON.stringify(out));
                  process.exit(0);
                }}, {int(post_close_wait_sec * 1000)});
              }}, {int(ws_close_after_sec * 1000)});
            }} else {{
              process.stdout.write(JSON.stringify(out));
              process.exit(0);
            }}
            return;
          }}

          if (frame.type === "res" && frame.id === chatSendId) {{
            if (frame.ok) {{
              out.chatSendAck = frame.payload;
            }} else {{
              out.chatSendErr = frame.error;
            }}
            return;
          }}
        }});

        // Hard timeout safety net (30s).
        setTimeout(() => {{
          out.phase = "timeout";
          process.stdout.write(JSON.stringify(out));
          process.exit(0);
        }}, 30_000);
        """
    )


def _run_one_trial(
    *,
    node_entry: Path,
    env: dict,
    state_dir: Path,
    gateway_port: int,
    mock_port: int,
    trial_idx: int,
) -> dict[str, Any]:
    # 1. device pairing seed
    device = seed_paired_device(
        state_dir=state_dir,
        display_name=f"audit-cand038-t{trial_idx}",
        role="operator",
        client_mode="cli",
    )

    # 2. mock LLM env config (audit ws probe 가 chat.send 후 SUT 가 mock_port 에 POST 함)
    mock_req_log = Path(tempfile.mkstemp(prefix=f"c038-mock-req-t{trial_idx}-", suffix=".jsonl")[1])
    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(mock_port)
    mock_env["MOCK_HOLD_MS"] = str(MOCK_HOLD_MS)
    mock_env["MOCK_MODE"] = "hold-then-complete"
    mock_env["MOCK_REQUEST_LOG"] = str(mock_req_log)

    mock_stderr = Path(tempfile.mkstemp(prefix=f"c038-mock-err-t{trial_idx}-", suffix=".log")[1])
    mock_stdout = Path(tempfile.mkstemp(prefix=f"c038-mock-out-t{trial_idx}-", suffix=".log")[1])

    mock_script = HARNESS_DIR / "mock_openai_cand038.mjs"

    with mock_stdout.open("w") as mo, mock_stderr.open("w") as me:
        mock_proc = subprocess.Popen(
            ["node", str(mock_script)],
            env=mock_env,
            stdout=mo,
            stderr=me,
        )
    # mock ready
    deadline = time.time() + 10
    mock_ready = _wait_for_port(mock_port, deadline, mock_proc)
    if not mock_ready:
        if mock_proc.poll() is None:
            mock_proc.terminate()
            mock_proc.wait(timeout=5)
        return {
            "trial_idx": trial_idx,
            "status": "mock_not_ready",
            "mock_stderr_tail": mock_stderr.read_text()[-1000:] if mock_stderr.exists() else "",
        }

    # 3. cfg overwrite — 옵션 A (NEXT.md 결정 2026-05-15).
    # env_isolate 의 minimal cfg 는 agentRuntime.id="codex" 라 codex CLI binary 부재
    # 환경에서 chain reach LLM 불가. 새 schema 에 맞춰 inline 으로 다시 쓰기:
    #   - agents.defaults.model.primary = "openai/gpt-5" (codex prefix 회피)
    #   - agentRuntime 명시 안 함 → resolveAgentHarnessPolicy 가 "auto" 후
    #     openAIProviderUsesCodexRuntimeByDefault false → runtime="auto" 유지 → pi default
    #   - models.providers.openai.baseUrl = mock_port URL + api=responses 명시
    cfg_overwrite = {
        "agents": {
            "defaults": {
                "model": {"primary": "openai/gpt-5"},
            },
        },
        "models": {
            "providers": {
                "openai": {
                    "baseUrl": f"http://127.0.0.1:{mock_port}/v1",
                    "apiKey": "sk-mock-c038",
                    "auth": "api-key",
                    "models": [
                        {"id": "gpt-5", "name": "gpt-5", "api": "openai-responses"},
                    ],
                },
            },
        },
        "gateway": {
            "mode": "local",
            "port": gateway_port,
            "bind": "loopback",
            "auth": {"mode": "none"},
            "tailscale": {"mode": "off", "resetOnExit": True},
        },
        "session": {"dmScope": "per-channel-peer"},
    }
    (state_dir / "openclaw.json").write_text(json.dumps(cfg_overwrite, indent=2))

    # 3a. SUT spawn (gateway run with mock LLM endpoint env)
    sut_env = env.copy()
    sut_env["OPENAI_BASE_URL"] = f"http://127.0.0.1:{mock_port}/v1"
    sut_env["OPENAI_API_KEY"] = "sk-mock-c038"
    # 일부 path 가 다른 env 변수 봄
    sut_env["OPENCLAW_OPENAI_BASE_URL"] = f"http://127.0.0.1:{mock_port}/v1"

    sut_stdout = Path(tempfile.mkstemp(prefix=f"c038-sut-out-t{trial_idx}-", suffix=".log")[1])
    sut_stderr = Path(tempfile.mkstemp(prefix=f"c038-sut-err-t{trial_idx}-", suffix=".log")[1])

    with sut_stdout.open("w") as so, sut_stderr.open("w") as se:
        sut_proc = subprocess.Popen(
            [
                "node", str(node_entry),
                "gateway", "run",
                "--auth", "none",
                "--bind", "loopback",
                "--port", str(gateway_port),
                "--allow-unconfigured",
            ],
            env=sut_env,
            stdout=so,
            stderr=se,
            text=True,
        )

    deadline = time.time() + GATEWAY_READY_TIMEOUT_SEC
    sut_ready = _wait_for_ready_marker(sut_proc, deadline, sut_stdout, sut_stderr)
    if not sut_ready:
        if sut_proc.poll() is None:
            sut_proc.terminate()
        try:
            sut_proc.wait(timeout=POST_CLOSE_WAIT_SEC)
        except subprocess.TimeoutExpired:
            sut_proc.kill()
        mock_proc.terminate()
        mock_proc.wait(timeout=5)
        return {
            "trial_idx": trial_idx,
            "status": "sut_not_ready",
            "sut_stderr_tail": sut_stderr.read_text()[-1000:],
            "sut_stdout_tail": sut_stdout.read_text()[-1500:],
        }

    # 4. audit ws probe
    probe_src = _build_ws_probe_ts(
        gateway_port=gateway_port,
        device=device,
        ws_close_after_sec=WS_CLOSE_AFTER_SEND_SEC,
        post_close_wait_sec=POST_CLOSE_WAIT_SEC,
    )
    tsx_bin = HARNESS_REPO / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        sut_proc.terminate()
        sut_proc.wait(timeout=5)
        mock_proc.terminate()
        mock_proc.wait(timeout=5)
        return {"trial_idx": trial_idx, "status": "tsx_missing", "tsx_bin": str(tsx_bin)}

    probe_path = Path(tempfile.mkstemp(
        prefix=f"c038-probe-t{trial_idx}-", suffix=".ts", dir=str(HARNESS_REPO)
    )[1])
    probe_path.write_text(probe_src)
    probe_stdout = ""
    probe_stderr = ""
    try:
        probe_proc = subprocess.run(
            [str(tsx_bin), str(probe_path)],
            cwd=str(HARNESS_REPO),
            capture_output=True,
            text=True,
            timeout=40,
        )
        probe_stdout = probe_proc.stdout
        probe_stderr = probe_proc.stderr
    finally:
        try:
            probe_path.unlink()
        except OSError:
            pass

    # 5. SUT graceful shutdown
    if sut_proc.poll() is None:
        sut_proc.terminate()
        try:
            sut_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sut_proc.kill()
            sut_proc.wait(timeout=5)
    mock_proc.terminate()
    mock_proc.wait(timeout=5)

    # 6. parse probe stdout JSON
    probe_result: dict = {}
    try:
        probe_result = json.loads(probe_stdout.strip())
    except Exception:
        probe_result = {"parse_error": True, "raw": probe_stdout[-1500:]}

    # 7. mock req log lines parse
    mock_lines = []
    if mock_req_log.exists():
        for line in mock_req_log.read_text().splitlines():
            try:
                mock_lines.append(json.loads(line))
            except Exception:
                continue

    client_disconnected = [m for m in mock_lines if m.get("event") == "client_disconnected"]
    stream_completed = [m for m in mock_lines if m.get("event") == "stream_completed"]
    hold_started = [m for m in mock_lines if m.get("event") == "hold_started"]

    # cleanup tempfiles
    for fp in (mock_req_log, mock_stdout, mock_stderr, sut_stdout, sut_stderr):
        try: fp.unlink()
        except OSError: pass

    return {
        "trial_idx": trial_idx,
        "status": "ok",
        "probe_phase": probe_result.get("phase"),
        "probe_connected": probe_result.get("connected"),
        "probe_helloOk": probe_result.get("helloOk"),
        "probe_chatSendAck": probe_result.get("chatSendAck"),
        "probe_chatSendErr": probe_result.get("chatSendErr"),
        "probe_wsCloseCode": probe_result.get("wsCloseCode"),
        "probe_errors": probe_result.get("errors"),
        "probe_frames": probe_result.get("frames"),
        "mock_request_started_count": sum(1 for m in mock_lines if m.get("event") == "request_started" and m.get("path") == "/v1/responses"),
        "mock_hold_started_count": len(hold_started),
        "mock_stream_completed_count": len(stream_completed),
        "mock_client_disconnected": client_disconnected,
        "probe_stderr_tail": probe_stderr[-500:] if probe_stderr else "",
    }


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    home = Path(env["OPENCLAW_HOME"])
    gateway_port = int(env.get("OPENCLAW_GATEWAY_PORT", "17000"))
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)

    # mock LLM 별도 port
    mock_port = _alloc_free_port(18000, 18999)

    trial_results = []
    abort_observed_trials = 0
    chain_works_trials = 0  # chat.send chain reached LLM
    for i in range(trials):
        t = _run_one_trial(
            node_entry=node_entry,
            env=env,
            state_dir=state_dir,
            gateway_port=gateway_port,
            mock_port=mock_port,
            trial_idx=i,
        )
        trial_results.append(t)
        if t.get("mock_request_started_count", 0) > 0:
            chain_works_trials += 1
        for cd in t.get("mock_client_disconnected", []) or []:
            if cd.get("completed") is False:
                abort_observed_trials += 1
                break

    return {
        "scenario": SCENARIO_NAME,
        "trials": trials,
        "chain_works_trials": chain_works_trials,
        "abort_observed_trials": abort_observed_trials,
        "trial_results": trial_results,
    }


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if "error" in measurements:
        return "blocked-env"
    trials = measurements.get("trials", 0)
    if trials == 0:
        return "blocked-env"
    chain_works = measurements.get("chain_works_trials", 0)
    if chain_works == 0:
        # chain reach LLM 도달 못 함 (audit-side blocked)
        return "blocked-external-dep"
    abort_observed = measurements.get("abort_observed_trials", 0)
    # without-fix 가정: chain works + abort 미발현 (mock req 가 hold 끝 완료)
    if chain_works > 0 and abort_observed == 0:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
        if m.get("chain_works_trials", 0) == 0:
            return "blocked-external-dep"
    wo_abort = without_fix.get("abort_observed_trials", 0)
    wf_abort = with_fix.get("abort_observed_trials", 0)
    if wo_abort == 0 and wf_abort > 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, the gateway WebSocket connection's `close` handler in "
        "`src/gateway/server/ws-connection.ts:351-421` only cleans up session/node/presence/"
        "nodeWake state and never iterates `chatAbortControllers` looking for entries owned by "
        "the closing connection (`ownerConnId === conn.id`). When a client disconnects mid-chat "
        "(ws.close), the in-flight chat runner registered via `registerChatAbortController` keeps "
        "running — LLM HTTP fetch continues, tools execute, external API calls fire, and partial "
        "results may be persisted. With this patch, the close handler aborts the matching entry's "
        "controller, so the chat runner observes signal.aborted and tears down cleanly."
    )
    environment = (
        "macOS (darwin arm64), Node.js, OpenClaw worktree built from base/head shas. "
        "End-to-end test spawns the production bundle `openclaw.mjs gateway run --auth none "
        "--bind loopback --port <p> --allow-unconfigured` against an isolated OPENCLAW_HOME, "
        "with `<state-dir>/identity/device.json` + `device-auth.json` + `devices/paired.json` "
        "pre-seeded (ed25519 keypair) and a mock OpenAI server in hold-then-complete mode (5s "
        "stream hold). An audit ws probe completes the connect handshake (v2 device signature), "
        "sends chat.send, waits 2s, and closes the WebSocket. The mock server records "
        "`req.on('close')` events with `completed=false` if abort propagates before the hold "
        "elapses."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile && pnpm build  (both worktrees)\n"
        "$ # spawn mock OpenAI (mock_openai_cand038.mjs, MOCK_MODE=hold-then-complete, hold=5s)\n"
        "$ # spawn gateway: openclaw gateway run --auth none --bind loopback --port <p> --allow-unconfigured\n"
        "$ # wait for [gateway] ready in stdout\n"
        "$ # spawn audit ws probe (tsx): connect.challenge → device-payload v2 signed → connect → hello-ok → chat.send → wait 2s → ws.close\n"
        "$ # wait POST_CLOSE_WAIT_SEC (12s) for hold + propagation\n"
        "$ # parse MOCK_REQUEST_LOG; assert (without-fix) no client_disconnected/completed=false, (with-fix) ≥1 such entry\n"
        "```"
    )
    evidence = (
        "Live wire-level measurement of whether ws.close propagates abort into the in-flight "
        "LLM fetch:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  chain_works_trials:        {without_fix.get('chain_works_trials')}\n"
        f"  abort_observed_trials:     {without_fix.get('abort_observed_trials')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  chain_works_trials:        {with_fix.get('chain_works_trials')}\n"
        f"  abort_observed_trials:     {with_fix.get('abort_observed_trials')}\n"
        "```"
    )
    observed = (
        f"Without the patch, "
        f"{without_fix.get('abort_observed_trials', 0)}/{without_fix.get('trials', 0)} "
        f"trials propagate abort to the mock LLM. With the patch, "
        f"{with_fix.get('abort_observed_trials', 0)}/{with_fix.get('trials', 0)} trials "
        "observe a hold-time client disconnect at the mock LLM, evidencing the close-handler "
        "fix iterating chatAbortControllers and aborting the in-flight LLM fetch."
    )
    not_tested = (
        "Cross-connection abort sharing (one connId's close affecting another's chat) and "
        "subagent/tool-call chains beyond the LLM fetch are not exercised in this scenario. "
        "Maintenance-sweep timeout fallback (>2min expiresAtMs floor) still exists as a "
        "second-line safety net but is not the primary path measured here."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True)
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--gateway-port", type=int, default=17900)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home
    env["OPENCLAW_GATEWAY_PORT"] = str(args.gateway_port)

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
