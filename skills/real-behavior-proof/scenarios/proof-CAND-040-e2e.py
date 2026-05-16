#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-040-e2e.py

CAND-040 (infra/approval-handler-runtime.ts:498-537 deliverTarget RMW race) e2e proof.

production execution path:
1. gateway boots with audit-stub channel plugin (--link installed) that exposes
   approvalCapability.nativeRuntime with file-IPC Deferred-gated transport stubs.
2. server-channels.ts:494 startChannelApprovalHandlerBootstrap creates handler via
   createChannelApprovalHandlerFromCapability and calls handler.start() — the inner
   exec-approval-channel-runtime attaches to gateway client events.
3. audit ws probe completes device pairing + connect handshake then sends
   exec.approval.request RPC. gateway server-methods/exec-approval.ts:153 records
   the request and calls deliverRequest → forwarder → exec.approval.requested event
   → plugin handler handleRequested → adapter.deliverTarget.
4. deliverTarget calls nativeRuntime.transport.deliverPending(stub) which awaits a
   file IPC release flag (deliver-pending.release).
5. audit harness signals the stub plugin's runtime context lease to dispose
   (dispose-lease.release) — channel-runtime-context unregister event fires →
   startChannelApprovalHandlerBootstrap watcher calls handler.stop() → onStopped →
   activeEntries.clear() — first codepath of the race.
6. audit harness then releases the deliverPending gate → deliverPending returns
   the wrapped entry → adapter.deliverTarget reaches activeEntries.get → undefined
   (already cleared) → fallback {entries:[]} → push wrapped → activeEntries.set
   re-inserts the orphaned entry on the cleared Map — second codepath of the race.
7. without-fix: wrapped never reaches finalizeResolved/finalizeExpired/onStopped
   → unbindPending stub NOT called → leak observable in sideband calls.jsonl.
8. with-fix: race guard short-circuits the second codepath → wrapped is unbound
   immediately → unbindPending stub IS called.

REQUIRES_EXTERNAL_DEP = True (real gateway boot + stub plugin install + ws probe + IPC).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
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
SCENARIO_NAME = "proof-CAND-040-e2e"
DEFAULT_TRIALS = 1

HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS_DIR))

_dp_spec = importlib.util.spec_from_file_location(
    "_device_pairing_helper",
    str(HARNESS_DIR / "device_pairing.py"),
)
_dp_mod = importlib.util.module_from_spec(_dp_spec)
_dp_spec.loader.exec_module(_dp_mod)
seed_paired_device = _dp_mod.seed_paired_device

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
READY_MARKER_PATTERN = re.compile(r"\[gateway\]\s+ready\b")
GATEWAY_READY_TIMEOUT_SEC = 60.0
INSTALL_TIMEOUT_SEC = 10.0
HARNESS_REPO = Path("/Users/lucas/Project/openclaw")
STUB_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "harness" / "cand040-stub-plugin"


def _alloc_free_port(start: int = 18200, end: int = 18299) -> int:
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}-{end}")


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


def _install_stub_plugin(env: dict, node_entry: Path) -> dict:
    """openclaw plugins install --link /tmp/cand040-stub-plugin.

    install command sometimes hangs (event loop keep-alive by stub register)
    but the install record is written within ~2s. so we spawn + sleep + kill
    + verify installs.json.
    """
    out = tempfile.mkstemp(prefix="c040-install-out-", suffix=".log")[1]
    err = tempfile.mkstemp(prefix="c040-install-err-", suffix=".log")[1]
    with open(out, "w") as so, open(err, "w") as se:
        proc = subprocess.Popen(
            [
                "node", str(node_entry),
                "plugins", "install", "--link", str(STUB_PLUGIN_DIR),
            ],
            env=env,
            stdout=so,
            stderr=se,
        )
    deadline = time.time() + INSTALL_TIMEOUT_SEC
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    installs_json = Path(env["OPENCLAW_HOME"]) / ".openclaw" / "plugins" / "installs.json"
    installed = installs_json.exists() and "audit-stub-c040" in installs_json.read_text()
    result = {
        "installed": installed,
        "installs_json_exists": installs_json.exists(),
    }
    for fp in (out, err):
        try: os.unlink(fp)
        except OSError: pass
    return result


def _build_ws_probe_ts(*, gateway_port: int, device: dict, control_dir: Path) -> str:
    privateKeyPemJson = json.dumps(device["privateKeyPem"])
    publicKeyRawJson = json.dumps(device["publicKeyRawBase64Url"])
    deviceIdJson = json.dumps(device["deviceId"])
    tokenJson = json.dumps(device["token"])
    role = device["role"]
    scopes = device["scopes"]
    scopes_json = json.dumps(scopes)
    role_json = json.dumps(role)
    scopes_csv = ",".join(scopes)
    control_dir_json = json.dumps(str(control_dir))

    return textwrap.dedent(
        f"""\
        import crypto from "node:crypto";
        import fs from "node:fs";
        import path from "node:path";
        import {{ WebSocket }} from "ws";

        const privateKeyPem = {privateKeyPemJson};
        const publicKeyRaw = {publicKeyRawJson};
        const deviceId = {deviceIdJson};
        const deviceToken = {tokenJson};
        const role = {role_json};
        const scopes = {scopes_json};
        const scopesCsv = {json.dumps(scopes_csv)};
        const controlDir = {control_dir_json};

        function base64UrlEncode(buf) {{
          return buf.toString("base64").replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
        }}
        function buildPayloadV2(signedAtMs, nonce) {{
          return ["v2", deviceId, "cli", "cli", role, scopesCsv, String(signedAtMs), deviceToken, nonce].join("|");
        }}
        function signPayload(payload) {{
          const key = crypto.createPrivateKey(privateKeyPem);
          const sig = crypto.sign(null, Buffer.from(payload, "utf8"), key);
          return base64UrlEncode(sig);
        }}
        function readCalls() {{
          const fp = path.join(controlDir, "calls.jsonl");
          if (!fs.existsSync(fp)) return [];
          return fs.readFileSync(fp, "utf8").split("\\n").filter(Boolean).map((s) => JSON.parse(s));
        }}

        const url = "ws://127.0.0.1:{gateway_port}/ws";
        const ws = new WebSocket(url);

        const out: any = {{
          phase: "connecting",
          connected: false,
          helloOk: false,
          execApprovalAck: null,
          execApprovalErr: null,
          wsCloseCode: null,
          frames: [] as any[],
          errors: [] as any[],
          steps: [] as any[],
        }};

        function step(label, extra) {{
          out.steps.push({{ ts: Date.now(), label, ...(extra ?? {{}}) }});
        }}
        function send(obj) {{ ws.send(JSON.stringify(obj)); }}

        let connectAckId = null;
        let execApprovalReqId = null;

        ws.on("open", () => {{
          out.connected = true; out.phase = "connected"; step("ws.open");
        }});
        ws.on("close", (code) => {{ out.wsCloseCode = code; step("ws.close", {{ code }}); }});
        ws.on("error", (e) => {{ out.errors.push(String(e?.message ?? e)); }});

        ws.on("message", (raw) => {{
          let frame;
          try {{ frame = JSON.parse(String(raw)); }} catch {{ return; }}
          out.frames.push({{ ts: Date.now(), type: frame.type, event: frame.event, id: frame.id, ok: frame.ok, error: frame.error?.code }});

          if (frame.type === "event" && frame.event === "connect.challenge") {{
            const nonce = String(frame.payload?.nonce ?? "");
            const signedAtMs = Date.now();
            const payload = buildPayloadV2(signedAtMs, nonce);
            const signature = signPayload(payload);
            connectAckId = crypto.randomUUID();
            send({{
              type: "req",
              id: connectAckId,
              method: "connect",
              params: {{
                minProtocol: 1, maxProtocol: 4,
                client: {{ id: "cli", version: "1.0.0", platform: "darwin", mode: "cli" }},
                role, scopes,
                device: {{ id: deviceId, publicKey: publicKeyRaw, signature, signedAt: signedAtMs, nonce }},
                auth: {{ token: deviceToken }},
              }},
            }});
            step("connect.req.sent");
            return;
          }}

          if (frame.type === "res" && frame.id === connectAckId) {{
            step("connect.res", {{ ok: frame.ok, error: frame.error?.code }});
            if (frame.ok && frame.payload?.type === "hello-ok") {{
              out.helloOk = true;
              // Fire exec.approval.request — non-blocking from the ws probe's
              // perspective; gateway sends back ack quickly while the stub
              // plugin's deliverPending remains parked behind the IPC gate.
              execApprovalReqId = crypto.randomUUID();
              send({{
                type: "req",
                id: execApprovalReqId,
                method: "exec.approval.request",
                params: {{
                  command: "echo audit-c040-race",
                  timeoutMs: 60_000,
                  twoPhase: false,
                }},
              }});
              step("exec.approval.request.sent");
              // Don't wait for ack — the gateway forwards exec.approval.request
              // to the plugin handler synchronously, and the ack response is
              // emitted only AFTER deliverPending resolves. Since our stub
              // parks deliverPending behind the IPC gate, the ack would never
              // arrive in time. Start polling immediately for the stub's
              // deliverPending.enter sideband line and drive the race from
              // there.
              const waitDeliverDeadline = Date.now() + 15_000;
              const waitForDeliverEnter = setInterval(() => {{
                const calls = readCalls();
                const enter = calls.find((c) => c.call === "deliverPending.enter");
                if (enter) {{
                  clearInterval(waitForDeliverEnter);
                  step("deliverPending.enter.observed");
                  // STEP A: trigger lease dispose so onStopped fires while
                  // deliverPending is parked.
                  fs.writeFileSync(path.join(controlDir, "dispose-lease.release"), "1");
                  step("dispose-lease.release.written");
                  // STEP B: small delay to let onStopped → activeEntries.clear,
                  // then release the deliverPending gate so wrapped is set on
                  // the cleared Map (without-fix bug).
                  setTimeout(() => {{
                    fs.writeFileSync(path.join(controlDir, "deliver-pending.release"), "1");
                    step("deliver-pending.release.written");
                    // wait for unbindPending observation window
                    setTimeout(() => {{
                      out.finalCalls = readCalls();
                      ws.close(1000, "audit-c040-done");
                      setTimeout(() => {{
                        process.stdout.write(JSON.stringify(out));
                        process.exit(0);
                      }}, 1500);
                    }}, 4_000);
                  }}, 1_500);
                }} else if (Date.now() > waitDeliverDeadline) {{
                  clearInterval(waitForDeliverEnter);
                  step("deliverPending.enter.timeout");
                  out.finalCalls = readCalls();
                  process.stdout.write(JSON.stringify(out));
                  process.exit(0);
                }}
              }}, 100);
              return;
            }} else {{
              process.stdout.write(JSON.stringify(out)); process.exit(0);
            }}
          }}

          if (frame.type === "res" && frame.id === execApprovalReqId) {{
            if (frame.ok) {{ out.execApprovalAck = frame.payload; }}
            else {{ out.execApprovalErr = frame.error; }}
            step("exec.approval.request.res.late", {{ ok: frame.ok }});
            return;
          }}
        }});

        // Hard safety net.
        setTimeout(() => {{
          out.phase = "global-timeout";
          out.finalCalls = readCalls();
          process.stdout.write(JSON.stringify(out));
          process.exit(0);
        }}, 60_000);
        """
    )


def _run_one_trial(
    *,
    node_entry: Path,
    env: dict,
    state_dir: Path,
    gateway_port: int,
    trial_idx: int,
) -> dict[str, Any]:
    # 1. device pairing (operator.approvals scope required for exec.approval.request)
    device = seed_paired_device(
        state_dir=state_dir,
        display_name=f"audit-cand040-t{trial_idx}",
        role="operator",
        client_mode="cli",
        scopes=["operator.read", "operator.write", "operator.approvals", "operator.admin"],
    )

    # 2. cfg overwrite (CAND-038 옵션 A 패턴)
    cfg_overwrite = {
        "agents": {"defaults": {"model": {"primary": "openai/gpt-5"}}},
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

    # 3. control dir for stub plugin
    control_dir = Path(tempfile.mkdtemp(prefix=f"c040-stub-ctl-t{trial_idx}-"))

    # 4. install stub plugin (link)
    sut_env = env.copy()
    sut_env["OPENCLAW_AUDIT_STUB_C040_DIR"] = str(control_dir)
    install_result = _install_stub_plugin(sut_env, node_entry)
    if not install_result["installed"]:
        shutil.rmtree(control_dir, ignore_errors=True)
        return {
            "trial_idx": trial_idx,
            "status": "stub_install_failed",
            "install_result": install_result,
        }

    # 5. gateway boot
    sut_stdout = Path(tempfile.mkstemp(prefix=f"c040-sut-out-t{trial_idx}-", suffix=".log")[1])
    sut_stderr = Path(tempfile.mkstemp(prefix=f"c040-sut-err-t{trial_idx}-", suffix=".log")[1])
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
    ready = _wait_for_ready_marker(sut_proc, deadline, sut_stdout, sut_stderr)
    if not ready:
        if sut_proc.poll() is None:
            sut_proc.terminate()
            try: sut_proc.wait(timeout=10)
            except subprocess.TimeoutExpired: sut_proc.kill(); sut_proc.wait(timeout=5)
        return {
            "trial_idx": trial_idx,
            "status": "sut_not_ready",
            "sut_stderr_tail": sut_stderr.read_text()[-1500:],
            "sut_stdout_tail": sut_stdout.read_text()[-1500:],
        }

    # 6. audit ws probe (tsx)
    probe_src = _build_ws_probe_ts(
        gateway_port=gateway_port,
        device=device,
        control_dir=control_dir,
    )
    tsx_bin = HARNESS_REPO / "node_modules" / ".bin" / "tsx"
    probe_path = Path(tempfile.mkstemp(
        prefix=f"c040-probe-t{trial_idx}-", suffix=".ts", dir=str(HARNESS_REPO)
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
            timeout=80,
        )
        probe_stdout = probe_proc.stdout
        probe_stderr = probe_proc.stderr
    finally:
        try: probe_path.unlink()
        except OSError: pass

    # 7. SUT shutdown
    if sut_proc.poll() is None:
        sut_proc.terminate()
        try: sut_proc.wait(timeout=10)
        except subprocess.TimeoutExpired: sut_proc.kill(); sut_proc.wait(timeout=5)

    # 8. parse probe stdout
    probe_result: dict = {}
    try:
        probe_result = json.loads(probe_stdout.strip())
    except Exception:
        probe_result = {"parse_error": True, "raw": probe_stdout[-2000:]}

    # 9. sideband final read
    calls_jsonl = control_dir / "calls.jsonl"
    lease_jsonl = control_dir / "lease.jsonl"
    calls: list[dict] = []
    if calls_jsonl.exists():
        for line in calls_jsonl.read_text().splitlines():
            try: calls.append(json.loads(line))
            except Exception: continue
    lease_lines: list[dict] = []
    if lease_jsonl.exists():
        for line in lease_jsonl.read_text().splitlines():
            try: lease_lines.append(json.loads(line))
            except Exception: continue

    counts = {
        "prepareTarget": sum(1 for c in calls if c.get("call") == "prepareTarget"),
        "deliverPending.enter": sum(1 for c in calls if c.get("call") == "deliverPending.enter"),
        "deliverPending.exit": sum(1 for c in calls if c.get("call") == "deliverPending.exit"),
        "bindPending": sum(1 for c in calls if c.get("call") == "bindPending"),
        "unbindPending": sum(1 for c in calls if c.get("call") == "unbindPending"),
    }
    lease_states = [l.get("call") for l in lease_lines]

    # DEBUG: keep tempfiles for inspection
    debug_paths = {
        "control_dir": str(control_dir),
        "sut_stdout": str(sut_stdout),
        "sut_stderr": str(sut_stderr),
    }

    return {
        "trial_idx": trial_idx,
        "status": "ok",
        "debug_paths": debug_paths,
        "all_calls": calls,
        "probe_phase": probe_result.get("phase"),
        "probe_connected": probe_result.get("connected"),
        "probe_helloOk": probe_result.get("helloOk"),
        "probe_execApprovalAck": probe_result.get("execApprovalAck"),
        "probe_execApprovalErr": probe_result.get("execApprovalErr"),
        "probe_wsCloseCode": probe_result.get("wsCloseCode"),
        "probe_errors": probe_result.get("errors"),
        "probe_steps": probe_result.get("steps"),
        "stub_call_counts": counts,
        "lease_states": lease_states,
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

    trial_results = []
    leak_trials = 0  # without-fix: unbindPending=0 + deliverPending.exit>=1 + bindPending>=1
    fix_observed_trials = 0  # with-fix mode 1: unbindPending>=1 (race 발생 후 cleanup)
    pre_race_blocked_trials = 0  # with-fix mode 2: deliverPending.exit>=1 & bindPending=0
    chain_works_trials = 0  # bindPending>=1 (chain reached the race window)
    chain_reach_trials = 0  # deliverPending.exit>=1 (chain reached deliverPending; fix can guard before bindPending)
    for i in range(trials):
        t = _run_one_trial(
            node_entry=node_entry,
            env=env,
            state_dir=state_dir,
            gateway_port=gateway_port,
            trial_idx=i,
        )
        trial_results.append(t)
        counts = t.get("stub_call_counts") or {}
        if counts.get("deliverPending.exit", 0) >= 1:
            chain_reach_trials += 1
        if counts.get("bindPending", 0) >= 1:
            chain_works_trials += 1
            if counts.get("unbindPending", 0) == 0 and counts.get("deliverPending.exit", 0) >= 1:
                leak_trials += 1
            if counts.get("unbindPending", 0) >= 1:
                fix_observed_trials += 1
        elif counts.get("deliverPending.exit", 0) >= 1:
            # deliverPending 까지 도달했으나 bindPending 미도달: fix 의 pre-race 가드.
            pre_race_blocked_trials += 1

    return {
        "scenario": SCENARIO_NAME,
        "trials": trials,
        "chain_works_trials": chain_works_trials,
        "chain_reach_trials": chain_reach_trials,
        "leak_trials": leak_trials,
        "fix_observed_trials": fix_observed_trials,
        "pre_race_blocked_trials": pre_race_blocked_trials,
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
        return "blocked-external-dep"
    leak = measurements.get("leak_trials", 0)
    if leak > 0:
        return "collected"
    # chain worked but no leak — could be timing / fix already present
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    # without-fix 가 race window (bindPending) 도달 못 했으면 결함 자체 재현 불가.
    if without_fix.get("chain_works_trials", 0) == 0:
        return "blocked-external-dep"
    # with-fix 의 chain reach 는 deliverPending.exit 으로 평가 (fix 가 bindPending 직전 차단 가능).
    if with_fix.get("chain_reach_trials", 0) == 0:
        return "blocked-external-dep"
    wo_leak = without_fix.get("leak_trials", 0)
    wf_fix_observed = with_fix.get("fix_observed_trials", 0)
    wf_pre_race_blocked = with_fix.get("pre_race_blocked_trials", 0)
    # fix 효과 = race 발생 후 cleanup (fix_observed) 또는 race 직전 차단 (pre_race_blocked).
    if wo_leak > 0 and (wf_fix_observed > 0 or wf_pre_race_blocked > 0):
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, `src/infra/approval-handler-runtime.ts:498-537` deliverTarget "
        "performs two sequential awaits (deliverPending L505, bindPending L517) before "
        "RMW'ing activeEntries (L529-535). If onStopped (L656-672) executes between the "
        "deliverPending await and the activeEntries.set, the closure's activeEntries Map is "
        "cleared but the resumed deliverTarget unconditionally inserts the wrapped entry into "
        "the now-empty Map. The orphaned wrapped never reaches finalizeResolved/Expired or "
        "the new onStopped, so wrapped.binding's unbindPending is never invoked — native "
        "binding leak. With this patch, deliverTarget detects the stopped state (or holds an "
        "abort signal) and unbinds the wrapped entry immediately."
    )
    environment = (
        "macOS (darwin arm64), Node.js, OpenClaw worktree built from base/head shas. "
        "End-to-end test installs an audit-side stub channel plugin (`/tmp/cand040-stub-plugin/`) "
        "via `openclaw plugins install --link` whose approvalCapability.nativeRuntime exposes "
        "file-IPC Deferred-gated deliverPending and counter-based bindPending/unbindPending "
        "stubs. The gateway boots with this plugin; once channel activation calls "
        "startChannelApprovalHandlerBootstrap and handler.start() runs, an audit ws probe "
        "completes the device-pairing connect handshake then sends exec.approval.request RPC."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile && pnpm build  (both worktrees)\n"
        "$ # install stub plugin (link)\n"
        "$ openclaw plugins install --link /tmp/cand040-stub-plugin\n"
        "$ # boot gateway with stub plugin enabled\n"
        "$ openclaw gateway run --auth none --bind loopback --port <p> --allow-unconfigured\n"
        "$ # wait [gateway] ready\n"
        "$ # audit ws probe: device-pair connect → exec.approval.request → wait for stub deliverPending.enter\n"
        "$ # STEP A: trigger lease dispose → onStopped fires → activeEntries.clear()\n"
        "$ # STEP B: release deliverPending gate → wrapped re-inserted on cleared Map → leak\n"
        "$ # assert (without-fix) unbindPending=0 + deliverPending.exit>=1, (with-fix) unbindPending>=1\n"
        "```"
    )
    evidence = (
        "Stub plugin sideband call counts (calls.jsonl):\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  trials:                       {without_fix.get('trials')}\n"
        f"  chain_reach_trials:           {without_fix.get('chain_reach_trials')}\n"
        f"  chain_works_trials:           {without_fix.get('chain_works_trials')}\n"
        f"  leak_trials:                  {without_fix.get('leak_trials')}\n"
        f"  fix_observed_trials:          {without_fix.get('fix_observed_trials')}\n"
        f"  pre_race_blocked_trials:      {without_fix.get('pre_race_blocked_trials')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  trials:                       {with_fix.get('trials')}\n"
        f"  chain_reach_trials:           {with_fix.get('chain_reach_trials')}\n"
        f"  chain_works_trials:           {with_fix.get('chain_works_trials')}\n"
        f"  leak_trials:                  {with_fix.get('leak_trials')}\n"
        f"  fix_observed_trials:          {with_fix.get('fix_observed_trials')}\n"
        f"  pre_race_blocked_trials:      {with_fix.get('pre_race_blocked_trials')}\n"
        "```"
    )
    observed = (
        f"Without the patch, {without_fix.get('leak_trials', 0)}/{without_fix.get('trials', 0)} "
        "trials leave the wrapped entry orphaned on the activeEntries Map without an "
        "unbindPending call — native binding leak directly observed. With the patch, "
        f"{with_fix.get('fix_observed_trials', 0)}/{with_fix.get('trials', 0)} trials "
        "issue unbindPending on the wrapped entry, evidencing the race guard."
    )
    not_tested = (
        "Caller-level inflight-await rescue (approval-native-runtime.ts handleRequested "
        "promise tracking, outer layer) and natural production timing variance (where "
        "deliverPending normally completes in <10ms making the race window vanish) are "
        "not exercised — the stub plugin uses a Deferred gate to deterministically park "
        "deliverPending and open the race window. CAL-003 caveat: in production with a "
        "real channel plugin's deliverPending latency, the race window may rarely open."
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
    ap.add_argument("--gateway-port", type=int, default=17888)
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
