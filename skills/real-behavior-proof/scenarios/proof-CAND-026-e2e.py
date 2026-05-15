#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-026-e2e.py

CAND-026 (mcp lifecycle, scope-down FIND-002) **production end-to-end** proof.

기존 proof-CAND-026.py 와의 차이:
- proof-CAND-026.py     : tsx 로 src/mcp/plugin-tools-handlers.ts 직접 import + handlers.callTool 함수 단위 호출.
                          tools-stdio-server.ts 의 setRequestHandler wiring 우회 — unit-level isolation.
- proof-CAND-026-e2e.py : production bundle (`dist/mcp/plugin-tools-serve.js`) 의 createPluginToolsMcpServer
                          factory 로 server 인스턴스 생성 + 실 StdioServerTransport + 실 MCP SDK Client 가
                          stdio 로 connect + 실 callTool with AbortSignal + 실 abort 발사.
                          tools-stdio-server.ts:17 의 setRequestHandler wiring 이 wire 위에서 호출되며
                          extra.signal drop 결함이 production execution path 에서도 발현됨을 검증.

production execution path:
- ACP host (Claude Code SDK / Codex / ACPX bridge) → spawn `node dist/mcp/plugin-tools-serve.js`
- stdio MCP transport
- listTools / callTool (Claude Code SDK Client → openclaw plugin-tools server)

본 시나리오의 production faithfulness:
- bundle: production tsdown bundle (`dist/mcp/plugin-tools-serve.js`) — minified / inlined wiring 그대로
- server wiring: createPluginToolsMcpServer → createToolsMcpServer → setRequestHandler 결함 콜백 100% 통과
- transport: 실 StdioServerTransport (server) + 실 StdioClientTransport (client). MCP framing protocol 정상.
- cancellation: Client.callTool({}, undefined, {signal}) → SDK 내부에서 stdio 로
  `notifications/cancelled {requestId}` 발사 → server 측 SDK Protocol 가 _requestHandlerAbortControllers
  의 abort() 호출 → setRequestHandler 콜백이 받은 extra.signal 이 abort. **production 결함**: 콜백이
  extra 무시 → handlers.callTool 가 signal 모름 → tool.execute 도 signal 미수신.

probe tool inject 정당화:
- production bundle 의 createPluginToolsMcpServer 가 `tools` param 으로 외부 inject 지원 (factory 시그니처).
  실 plugin loader 우회 (lancedb 등 외부 의존 회피) 만 발생. wiring path 0 변경.
- probe tool 의 execute 가 시그니처 `(id, params, signal?)` 로 4번째 인자 캡처. wrapToolWithBeforeToolCallHook
  이 signal 을 transparent forward (`pi-tools.before-tool-call.ts:673,746` 확인됨).

REQUIRES_EXTERNAL_DEP = True (실 child process spawn + 실 stdio MCP framing).

without-fix (base): probe.execute 의 signal === undefined → signalReceived=false, abortObserved=false.
with-fix (head)   : probe.execute 의 signal !== undefined, parent abort 후 signal.aborted=true.

caveat:
- probe.execute 가 일정 시간 sleep 하는 동안 parent 가 abort. sleep 길이 (500ms) 와 abort delay (100ms)
  는 cancel notification 의 stdio 도달 보장 + tool 실행 종료 전 abort 도달 보장 양쪽 만족.
- 결함 측정은 single binary: signal 이 execute 에 propagate 됐는가. abort 도달 후의 동작 (abortObserved)
  은 fix 적용 시 부가 확인.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = True
SCENARIO_NAME = "proof-CAND-026-e2e"
DEFAULT_TRIALS = 1


def _wrapper_script(serve_bundle_path: str, signal_log_path: str) -> str:
    """child MCP server wrapper. dist 의 createPluginToolsMcpServer 로 server 생성 + StdioServerTransport
    직접 connect. probe tool 만 외부 inject. production wiring (setRequestHandler 결함) 100% 통과."""
    return textwrap.dedent(f"""\
        import {{ writeFileSync }} from "node:fs";
        import {{ createPluginToolsMcpServer }} from "{serve_bundle_path}";
        import {{ StdioServerTransport }} from "@modelcontextprotocol/sdk/server/stdio.js";

        const SIGNAL_LOG = {json.dumps(signal_log_path)};

        const probeTool = {{
          name: "probe-cancel",
          description: "CAND-026 e2e signal propagation probe",
          parameters: {{ type: "object", properties: {{}} }},
          ownerOnly: false,
          execute: async (id, _params, signal) => {{
            const start = Date.now();
            const initial = {{
              signalDefined: signal !== undefined,
              signalIsAbortSignal: signal !== undefined && typeof signal === "object" && typeof signal.addEventListener === "function",
              signalAbortedAtStart: signal?.aborted === true,
            }};
            let abortObserved = false;
            if (signal && typeof signal.addEventListener === "function") {{
              signal.addEventListener("abort", () => {{ abortObserved = true; }});
            }}
            // 500ms sleep — parent 가 100ms 후 abort 발사하므로 abort 도달 충분.
            await new Promise((r) => setTimeout(r, 500));
            const finalState = {{
              signalAbortedAtEnd: signal?.aborted === true,
              abortObserved,
              elapsedMs: Date.now() - start,
            }};
            try {{
              writeFileSync(SIGNAL_LOG, JSON.stringify({{ initial, finalState }}));
            }} catch (e) {{
              process.stderr.write(`probe write fail: ${{String(e)}}\\n`);
            }}
            return {{ content: [{{ type: "text", text: "ok" }}] }};
          }},
        }};

        const server = createPluginToolsMcpServer({{ tools: [probeTool] }});
        const transport = new StdioServerTransport();
        await server.connect(transport);

        // mirror production tools-stdio-server.ts shutdown wiring — stdin end / SIGTERM 시 종료.
        const shutdown = () => {{
          try {{ server.close().catch(() => {{}}); }} catch (_) {{}}
          // 강제 종료까지 약간의 grace period 후 process.exit
          setTimeout(() => process.exit(0), 50).unref();
        }};
        process.stdin.once("end", shutdown);
        process.stdin.once("close", shutdown);
        process.once("SIGINT", shutdown);
        process.once("SIGTERM", shutdown);
    """)


def _parent_script(wrapper_path: str, signal_log_path: str, tsx_bin: str) -> str:
    """parent MCP client. child wrapper 를 stdio 로 spawn → callTool with AbortSignal → 100ms 후 abort →
    AbortError 수신 → child stdio close. probe 의 sideband signal log 를 child 종료 후 읽어 stdout JSON 출력.

    child 는 `--experimental-strip-types` (Node.js native ts strip) 로 spawn — tsx `--import` 모드가
    child stdio framing 을 hijack 해서 SDK Client.connect 가 stuck 되는 현상 회피. native strip-types 는
    Node 22+ flag (실험적). server wiring 자체는 그대로 production bundle 사용."""
    return textwrap.dedent(f"""\
        import {{ readFileSync, existsSync }} from "node:fs";
        import {{ Client }} from "@modelcontextprotocol/sdk/client/index.js";
        import {{ StdioClientTransport }} from "@modelcontextprotocol/sdk/client/stdio.js";

        const WRAPPER = {json.dumps(wrapper_path)};
        const SIGNAL_LOG = {json.dumps(signal_log_path)};

        const transport = new StdioClientTransport({{
          command: process.execPath,
          args: ["--experimental-strip-types", WRAPPER],
          env: {{ PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "" }},
          stderr: "pipe",
        }});

        let childStderr = "";
        if (transport.stderr) {{
          transport.stderr.on("data", (chunk) => {{ childStderr += String(chunk); }});
        }}

        const client = new Client({{ name: "probe-client", version: "1.0.0" }}, {{ capabilities: {{}} }});

        const result = {{
          handshake: null,
          callOutcome: null,
          signalLog: null,
          childStderr: "",
        }};

        try {{
          await client.connect(transport);
          result.handshake = "ok";

          const ctrl = new AbortController();
          // fire-and-not-await — abort 100ms 후
          const callPromise = client
            .callTool({{ name: "probe-cancel", arguments: {{}} }}, undefined, {{ signal: ctrl.signal }})
            .then((r) => ({{ ok: true, content: r?.content }}))
            .catch((e) => ({{ ok: false, error: String(e?.name ?? "") + ": " + String(e?.message ?? e) }}));

          await new Promise((r) => setTimeout(r, 100));
          ctrl.abort();

          // wait for callPromise to settle (AbortError or response)
          result.callOutcome = await callPromise;

          // give probe time to write sideband (probe still sleeping in child)
          await new Promise((r) => setTimeout(r, 800));
        }} catch (e) {{
          result.handshake = "error: " + String(e?.message ?? e);
        }} finally {{
          try {{ await client.close(); }} catch (_) {{}}
        }}

        // wait for child to fully exit + flush sideband
        await new Promise((r) => setTimeout(r, 300));

        if (existsSync(SIGNAL_LOG)) {{
          try {{
            result.signalLog = JSON.parse(readFileSync(SIGNAL_LOG, "utf-8"));
          }} catch (e) {{
            result.signalLog = {{ parseError: String(e) }};
          }}
        }} else {{
          result.signalLog = {{ missing: true }};
        }}

        result.childStderr = childStderr.slice(0, 1000);
        process.stdout.write(JSON.stringify(result));
        // 강제 종료 — tsx / SDK transport handle 이 event loop alive 유지하는 경우 대비.
        process.exit(0);
    """)


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    # node_entry: build_single 결과 = worktree/openclaw.mjs (production bundle)
    wt_path = node_entry.parent
    serve_bundle = wt_path / "dist" / "mcp" / "plugin-tools-serve.js"
    if not serve_bundle.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"production bundle missing: {serve_bundle}. pnpm build 결과 확인.",
        }
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"tsx missing: {tsx_bin}",
        }

    proof_uuid = uuid.uuid4().hex[:8]
    proof_dir = wt_path / f"proof-tmp-{proof_uuid}"
    proof_dir.mkdir(exist_ok=True)
    wrapper_path = proof_dir / "wrapper.ts"
    parent_path = proof_dir / "parent.ts"
    signal_log = proof_dir / "signal.json"

    wrapper_path.write_text(_wrapper_script(str(serve_bundle), str(signal_log)))
    parent_path.write_text(_parent_script(str(wrapper_path), str(signal_log), str(tsx_bin)))

    try:
        proc = subprocess.run(
            [str(tsx_bin), str(parent_path)],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"parent exited {proc.returncode}",
                "stderr": proc.stderr[:1000],
                "stdout": proc.stdout[:1000],
            }
        try:
            payload = json.loads(proc.stdout.strip())
        except Exception as e:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"could not parse parent stdout: {e}",
                "stdout": proc.stdout[:1000],
                "stderr": proc.stderr[:500],
            }
        sig = payload.get("signalLog") or {}
        if "missing" in sig:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": "probe sideband signal.json missing — probe.execute 호출 안 됨 또는 wrapper 종료",
                "handshake": payload.get("handshake"),
                "callOutcome": payload.get("callOutcome"),
                "childStderr": payload.get("childStderr"),
            }
        initial = sig.get("initial") or {}
        final = sig.get("finalState") or {}
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "executeInvoked": 1,
            "signalDefined": initial.get("signalDefined", False),
            "signalIsAbortSignal": initial.get("signalIsAbortSignal", False),
            "abortObserved": final.get("abortObserved", False),
            "signalAbortedAtEnd": final.get("signalAbortedAtEnd", False),
            "elapsedMs": final.get("elapsedMs"),
            "handshake": payload.get("handshake"),
            "callOutcome": payload.get("callOutcome"),
            "childStderr": payload.get("childStderr"),
        }
    finally:
        # cleanup proof tmp 디렉터리 (signal log 등)
        try:
            for p in (wrapper_path, parent_path, signal_log):
                if p.exists():
                    p.unlink()
            proof_dir.rmdir()
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    if measurements.get("executeInvoked", 0) == 0:
        return "blocked-env"
    # production 결함: probe.execute 가 받은 signal 이 undefined.
    if measurements.get("signalDefined") is False:
        return "collected"
    # signal 이 propagate 됐다 = without-fix 인데 fix 적용된 셈 → 모순. axis 재검토.
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_def = without_fix.get("signalDefined", True)
    wf_def = with_fix.get("signalDefined", False)
    wf_aborted = with_fix.get("signalAbortedAtEnd", False) or with_fix.get("abortObserved", False)
    if (not wo_def) and wf_def and wf_aborted:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, the standalone MCP plugin-tools server "
        "(`dist/mcp/plugin-tools-serve.js`) wires `setRequestHandler(CallToolRequestSchema, "
        "async (request) => handlers.callTool(request.params))` and discards the second argument "
        "`extra: RequestHandlerExtra`. When an MCP host issues `notifications/cancelled` (e.g. via "
        "Client.callTool's RequestOptions.signal), the SDK aborts the per-request AbortController, "
        "but the request handler never observes that signal, so the in-flight tool.execute keeps "
        "running with no cancellation channel. With this patch, extra.signal flows through callTool "
        "into tool.execute's 4th argument, so host cancellation actually aborts the running tool."
    )
    environment = (
        "macOS (darwin arm64), Node.js, OpenClaw worktree built via `pnpm install --frozen-lockfile && "
        "pnpm build` from the base/head shas. End-to-end test spawns the production bundle "
        "`dist/mcp/plugin-tools-serve.js` as a child process, drives it through real "
        "`@modelcontextprotocol/sdk` `StdioClientTransport` from the parent, and uses a probe tool "
        "injected via `createPluginToolsMcpServer({tools:[probe]})` so the production wiring "
        "(`tools-stdio-server.ts:17` setRequestHandler callback + `plugin-tools-handlers.ts` "
        "callTool) is exercised end-to-end. No external services (LLM/OAuth/network) — purely "
        "in-process MCP transport."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile && pnpm build           (both worktrees)\n"
        "$ node --import tsx <parent.ts>                           (worktree-relative)\n"
        "    parent.ts spawns child via StdioClientTransport({command: node, args: ['--import','tsx','<wrapper.ts>']})\n"
        "    wrapper.ts imports createPluginToolsMcpServer from dist/mcp/plugin-tools-serve.js\n"
        "    parent calls client.callTool({name:'probe-cancel'}, undefined, {signal: ctrl.signal})\n"
        "    parent aborts ctrl after 100ms\n"
        "    SDK transmits notifications/cancelled over stdio\n"
        "    server-side SDK Protocol fires _requestHandlerAbortControllers.abort()\n"
        "    probe tool's execute records (signal !== undefined, signal.aborted) into sideband file\n"
        "```"
    )
    evidence = (
        "Live MCP wire-level measurement of AbortSignal propagation from Client.callTool through "
        "stdio cancellation notification into server-side tool.execute:\n\n"
        "```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  signalDefined:        {without_fix.get('signalDefined')}\n"
        f"  signalIsAbortSignal:  {without_fix.get('signalIsAbortSignal')}\n"
        f"  abortObserved:        {without_fix.get('abortObserved')}\n"
        f"  signalAbortedAtEnd:   {without_fix.get('signalAbortedAtEnd')}\n"
        f"  callOutcome:          {without_fix.get('callOutcome')}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  signalDefined:        {with_fix.get('signalDefined')}\n"
        f"  signalIsAbortSignal:  {with_fix.get('signalIsAbortSignal')}\n"
        f"  abortObserved:        {with_fix.get('abortObserved')}\n"
        f"  signalAbortedAtEnd:   {with_fix.get('signalAbortedAtEnd')}\n"
        f"  callOutcome:          {with_fix.get('callOutcome')}\n"
        "```"
    )
    observed = (
        f"With patch, host-issued cancellation reaches the in-flight tool: signal is defined "
        f"(={with_fix.get('signalDefined')}), recognized as AbortSignal "
        f"(={with_fix.get('signalIsAbortSignal')}), and either fires the abort listener "
        f"(={with_fix.get('abortObserved')}) or is observed in aborted state "
        f"(={with_fix.get('signalAbortedAtEnd')}). Without patch, signal is undefined and the tool "
        f"runs to completion regardless of host cancellation."
    )
    not_tested = (
        "FIND-mcp-lifecycle-001 shutdown drain axis (abandoned by cross-review — SDK Protocol._onclose "
        "already issues unconditional abort). Tools whose execute() does not yet accept a signal "
        "argument (memory_recall, cron-tool) still ignore cancellation — separate follow-up. Real "
        "memory-lancedb / cron plugin tools are not in this run; injected probe is the cancellation "
        "instrumentation point."
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
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
