---
id: FIND-mcp-lifecycle-002
cell: mcp-lifecycle
title: plugin-tools callTool ignores RequestHandlerExtra.signal — no cancellation
file: src/mcp/plugin-tools-handlers.ts
line_range: 45-70
evidence: "```ts\n    callTool: async (params: CallPluginToolParams) => {\n      const\
  \ tool = toolMap.get(params.name);\n      if (!tool) {\n        return {\n     \
  \     content: [{ type: \"text\", text: `Unknown tool: ${params.name}` }],\n   \
  \       isError: true,\n        };\n      }\n      try {\n        const result =\
  \ await tool.execute(`mcp-${Date.now()}`, params.arguments ?? {});\n        const\
  \ rawContent =\n          result && typeof result === \"object\" && \"content\"\
  \ in result\n            ? (result as { content?: unknown }).content\n         \
  \   : result;\n        return {\n          content: Array.isArray(rawContent)\n\
  \            ? rawContent\n            : [{ type: \"text\", text: coerceChatContentText(rawContent)\
  \ }],\n        };\n      } catch (err) {\n        return {\n          content: [{\
  \ type: \"text\", text: `Tool error: ${formatErrorMessage(err)}` }],\n         \
  \ isError: true,\n        };\n      }\n    },\n```\n"
symptom_type: lifecycle-gap
problem: '''`createPluginToolsMcpHandlers` 의 `callTool` 이 MCP SDK 가 제공하는 cancellation

  signal 을 받지 않는다. tools-stdio-server.ts:17 에서 SDK 의 setRequestHandler

  콜백을 `async (request) => handlers.callTool(request.params)` 로 wrap 하면서

  두 번째 인자 `RequestHandlerExtra` (signal: AbortSignal 포함) 를 무시하고, callTool

  자체도 signal 파라미터를 받지 않는다. 결과적으로 `tool.execute` 의 4번째 시그니처

  `signal?: AbortSignal` (src/agents/tools/common.ts:21-28) 가 영구히 undefined.

  MCP host (Claude Code SDK / Codex) 가 cancellation notification 을 송신해도,

  transport 가 close 되어도 in-flight tool.execute 는 자기 timeout (없으면 영원히)

  까지 hang. 이는 host-side cancellation 의 lifecycle 계약 위반이며 FIND-mcp-lifecycle-001

  의 in-flight drain 부재와 짝을 이룬다.''

  '
mechanism: "'1. host (Claude Code SDK) 가 callTool 요청 송신. MCP SDK 의 `Protocol` layer\
  \ 가\n   internal requestId 마다 AbortController 생성, handler 호출 시 `extra.signal` 로\n\
  \   전달.\n2. tools-stdio-server.ts:17 의 wrapper `async (request) => handlers.callTool(request.params)`\n\
  \   가 `extra` 무시 — request.params 만 전달.\n3. plugin-tools-handlers.ts:54 `tool.execute(\\\
  `mcp-\\${Date.now()}\\`, params.arguments ?? {})`\n   — signal 자리에 아무것도 안 들어감.\n\
  4. host 가 cancel: (a) MCP `notifications/cancelled` 송신 → SDK 가 internal\n   AbortController.abort()\
  \ 호출 → extra.signal 의 listener 가 trigger 되지만\n   handler 가 무시했으므로 tool.execute 는\
  \ 모름. (b) host 가 child stdin close\n   또는 SIGTERM → tools-stdio-server.ts shutdown\
  \ → server.close() floating\n   (FIND-mcp-lifecycle-001) — 마찬가지로 in-flight 미통보.\n\
  5. tool.execute 가 외부 호출 (browser navigate, memory_recall 의 vector search,\n   cron_list\
  \ 의 storage 접근) 중이면 자체 timeout 까지 hang. tool 별 timeout 부재 시\n   영구 hang → child\
  \ process 가 host 의 강제 kill (SIGKILL after grace period)\n   으로만 종료.\n6. 종료 시점에 외부\
  \ 자원 (DB connection, browser session, network socket) 의\n   graceful close 가 skip\
  \ — 자원 누수.'\n"
root_cause_chain:
- why: 왜 callTool 이 signal 을 받지 않는가?
  because: 'tools-stdio-server.ts:17 의 setRequestHandler callback 시그니처가 `async (request)`

    로 작성됨. SDK 의 setRequestHandler 두 번째 인자 `extra: RequestHandlerExtra`

    를 통째로 무시. 그리고 plugin-tools-handlers.ts 의 `callTool` 자체도 signal

    파라미터를 받지 않는 API 로 설계됨 (L45 `async (params: CallPluginToolParams)`).

    '
  evidence_ref: src/mcp/tools-stdio-server.ts:17-19
- why: 왜 tool.execute 의 signal 인자가 비어 있는가?
  because: 'AnyAgentTool.execute 시그니처는 `execute(toolCallId, params, signal?, onUpdate?)`

    (src/agents/tools/common.ts:22-28). 4 위치 모두 optional 이지만 cancellation

    을 위한 정상 흐름은 signal 을 전달하는 것. plugin-tools-handlers.ts:54 는

    `(toolCallId, params)` 만 전달 → signal 자리에 undefined.

    '
  evidence_ref: src/mcp/plugin-tools-handlers.ts:54
- why: 왜 host cancel 이 본 channel 로 도달하는가?
  because: 'MCP 프로토콜 spec 의 `notifications/cancelled` 가 host→server 표준 메시지.

    SDK 의 `Protocol` 가 requestId 마다 AbortController 를 보유, cancelled 도착

    시 abort() 호출. 또한 transport close (stdin close / SIGTERM) 시에도 SDK 가

    pending request 들을 abort. 즉 cancel signal 은 정상 도착하나 handler 가

    consume 안 함.

    '
  evidence_ref: 'node_modules/@modelcontextprotocol/sdk/dist/esm/shared/protocol.d.ts:177
    (RequestHandlerExtra.signal: AbortSignal)'
- why: 왜 이 gap 이 메모리/리소스 측면에서 lifecycle 결함인가?
  because: 'tool.execute 가 외부 자원 (PassThrough stream, DB connection, browser session,

    child process 등) 을 hold 하는 동안 signal 미수신 = graceful release 시점

    상실. host kill 시 SIGKILL 로만 종료 → 자원이 OS 회수에 의존 (DB rollback,

    browser temp file, network socket FIN 절차 skip). 동일 child 가 자주 spawn

    되는 환경 (예: agent loop 가 매 turn 당 plugin-tools server spawn) 에서

    FD/메모리 / DB lock 누수 누적.

    '
  evidence_ref: src/agents/tools/common.ts:21-29 (signal 인자가 의도된 cleanup hook 임을 명세)
impact_hypothesis: resource-exhaustion
impact_detail: '''정성: in-flight tool 호출이 host-side cancellation 에 반응하지 않으므로 (a)

  host UI 가 cancel 을 받아들였다 표시해도 child 는 계속 작업 (특히 LLM streaming,

  vector DB scan, browser navigation 처럼 부분 결과를 누적하는 tool), (b) host 가

  child 를 kill 하면 외부 자원 (DB transaction, browser context, FD) 의 graceful

  cleanup 단계 skip. 정량 가능 항목: tool 별 평균 실행 시간 × 누적 cancel 빈도.

  long-running tool (memory-lancedb 의 large vector recall, browser navigation,

  shell exec) 에서 가장 두드러짐. 재현 조건: (1) plugin tool 이 외부 자원 hold,

  (2) host 가 cancellation 송신, (3) child 가 응답 안 함 → host SIGKILL.

  재현 가능성 결정적 (signal 전달 라인이 missing 임을 코드로 확인 가능).''

  '
severity: P2
counter_evidence:
  path: src/mcp/plugin-tools-handlers.ts
  line: 45-70
  reason: "'확인한 반증 카테고리:\n(1) 숨은 방어 / defense-in-depth: `rg -n \"AbortSignal|abortSignal|signal:\"\
    \ src/mcp/`\n    → 매치 0. `rg -n \"tool\\\\.execute\\\\(.*signal\" src/` → 매치 0.\
    \ handler 어디서도\n    signal 을 받거나 전달하는 코드 없음. SDK abort 가 호출되어도 destination\n \
    \   없음.\n(2) 기존 테스트 커버: `rg -n \"AbortSignal|cancelled|cancel\" src/mcp/plugin-tools-serve.test.ts\n\
    \    src/mcp/plugin-tools-handlers.ts` → 매치 0 (handlers 자체 test 부재 +\n    serve\
    \ test 도 cancellation 미검증).\n(3) 호출 빈도: `setRequestHandler` 가 등록된 callTool handler\
    \ 는 host 의 모든\n    callTool 요청에 진입 (tools-stdio-server.ts:17-19) — production\
    \ primary\n    path. plugin tool 사용 frequency 가 cell 의 hot-path.\n(4) 설정 / feature\
    \ flag: signal 전달을 켜는 flag 없음. 코드 자체에서 누락.\n(5) primary-path inversion (CAL-001):\
    \ \"signal 을 다른 경로로 받는 코드가 있나\"\n    → 부재. tool.execute 가 호출되는 다른 entrypoint (agent\
    \ loop) 는 본 셀 외\n    (src/agents/**) 이지만 본 셀 file 안에서는 signal 누락이 유일한 흐름.\n(6)\
    \ Hot-path-vs-test-path (CAL-003): handler 의 test 자체가 부재 (plugin-tools-handlers\n\
    \    은 plugin-tools-serve.test.ts 안에서 indirect 검증, signal 처리 assertion 없음).\n\
    \    production 에서는 SDK 가 항상 extra.signal 을 제공 → mock 으로 우회하는 함정 없음.\n(7) Upstream\
    \ dup (CAL-004/008): `git log upstream/main --since=\"6 weeks ago\"\n    -- src/mcp/plugin-tools-handlers.ts`\
    \ → 5 건 (471489159b honor plugin tool\n    policy, 0df90d9b8d trace plugin tool\
    \ factory timings, e4b09e1bf3 serialize\n    raw result, 5fa0d282a8 stringify\
    \ safely, 8f3b99c512 owner-only block) —\n    모두 policy / 결과 변환 축. signal/cancel\
    \ 미언급. `gh pr list --search\n    \"callTool OR AbortSignal mcp plugin in:title,body\"\
    ` → bundle-mcp PR #73536\n    (callTool timeout, 본 셀 외 agents 도메인) + #78160 (extend\
    \ bundle MCP\n    timeout, 동일 도메인 외). 본 file 의 signal/cancel 축 OPEN PR 0.\n\n\
    \    주의: FIND-mcp-lifecycle-001 과 같은 cell 이지만 (file, line_range) 가 다름 —\n    tools-stdio-server.ts:29-40\
    \ vs plugin-tools-handlers.ts:45-70. 두 결함은\n    in-flight cleanup gap 의 두 측면이므로\
    \ cross_refs 로 연결한다.'\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs:
- FIND-mcp-lifecycle-001
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# plugin tools MCP handlers ignore RequestHandlerExtra.signal — in-flight tool.execute never cancellable

## 문제

`createPluginToolsMcpHandlers` 가 반환하는 `callTool` (plugin-tools-handlers.ts:45-70) 은 `tool.execute(toolCallId, params)` 두 인자만 전달한다. `AnyAgentTool.execute` 시그니처는 `(toolCallId, params, signal?, onUpdate?)` (src/agents/tools/common.ts:21-29) 로 cancellation 을 위한 `AbortSignal` 자리를 명시적으로 갖고 있으나 본 handler 는 이 자리를 비운다.

상위 wiring 도 같은 문제. tools-stdio-server.ts:17 의 SDK setRequestHandler 등록부는 `async (request) => handlers.callTool(request.params)` 로 작성되어 두 번째 인자 `extra: RequestHandlerExtra<...>` (signal 포함, SDK protocol.d.ts:173-194) 를 받지 않는다. 그 결과 host 가 MCP `notifications/cancelled` 를 송신하거나 transport 가 close 되어 SDK 가 abort 를 trigger 해도 in-flight `tool.execute` 는 통보받지 못한다.

이 결함은 FIND-mcp-lifecycle-001 (floating `void server.close()`) 과 짝을 이룬다 — shutdown 측에서 cleanup 못 하고, handler 측에서 cancel 도 전파 못 하니 in-flight tool 호출은 자체 timeout (또는 호스트 SIGKILL) 까지 절대로 멈추지 않는다.

## 발현 메커니즘

```
host                         SDK Protocol                 handler                tool
                                                                       
 callTool ─────────────────> Protocol assigns requestId,
                              creates AbortController.
                              setRequestHandler callback
                              invoked with (request, extra).
                                  │
                                  │ (extra.signal ignored
                                  │  by tools-stdio-server.ts:17)
                                  ▼
                                handler.callTool(params)
                                  │
                                  │ (no signal parameter on
                                  │  the function signature)
                                  ▼
                                tool.execute(id, args)
                                  ──────────────────────> external work
                                                          (LLM stream, DB scan,
                                                           browser navigate)
 cancel  ────────────────>   AbortController.abort()
                              extra.signal fires.
                              (no listener registered.)
 stdin close ────────────>   SDK Server.close()
                              (in-flight handler keeps
                              running — handler never
                              checks signal.)
 kill (after grace) ────>    process killed.
                              tool.execute promise
                              still pending —
                              resources not released
                              gracefully.
```

## 근본 원인 분석

1. **SDK wrapper 가 extra 무시**: tools-stdio-server.ts:17 의 setRequestHandler callback 이 `async (request) => handlers.callTool(request.params)` — 두 번째 인자 `extra` (signal/requestId/authInfo/sessionId/_meta) 를 받지 않음. SDK 가 제공하는 정상 abort hook 이 손실.

2. **Handler API 가 signal 인자 부재**: `callTool: async (params: CallPluginToolParams) => ...` (plugin-tools-handlers.ts:45) 가 signal 을 받지 않는 형태로 설계. 따라서 tools-stdio-server.ts 측에서 extra.signal 을 알게 된다 해도 handlers.callTool 으로 전달할 channel 자체가 없음. 결함이 두 file 에 걸쳐 분포.

3. **tool.execute 의 signal slot 영구 undefined**: AnyAgentTool 설계는 cancel-aware (signal optional 3rd arg). 본 handler 에서 이 slot 이 비면 모든 plugin tool 의 cleanup hook 이 disabled. 동일 tool 이 agent loop 에서 호출될 때는 (다른 caller) signal 이 전달될 수 있으나 MCP plugin-tools server 경유 호출만 누락 → 같은 plugin 의 cancellation 동작이 호출 경로별 일관되지 않음.

## 영향

- **Host UX**: cancel 버튼이 시각적으로는 반응하지만 child process 는 작업을 계속. 사용자 컨텍스트에서 "cancel 했는데 결과가 나중에 나옴" 또는 "cancel 후에도 자원이 점유됨".
- **자원 누수**: long-running tool 이 외부 자원 (browser context, DB transaction, network socket) 을 hold. host 가 후속 SIGKILL 로만 종료 → graceful close skip. 누적 결과: FD 누수, DB lock orphan, browser temp file 잔존.
- **FIND-mcp-lifecycle-001 와의 합산 효과**: shutdown 측 drain 부재 (001) + cancel 전파 부재 (002) 가 함께 작용. host 입장에서는 "재시작 했더니 응답이 오지 않고 child 가 갑자기 죽음" 의 정확한 mechanism.
- **빈도**: plugin-tools / openclaw-tools standalone MCP 서버를 사용하는 host (Claude Code, ACPX bridge, Codex) 의 모든 cancellation 경로.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "AbortSignal|abortSignal|signal:" src/mcp/` → 매치 0. handler 어디서도 signal 을 받거나 listener 등록 없음.
- `rg -n "tool\.execute\(.*signal" src/mcp/` → 매치 0. signal 을 tool.execute 로 전달하는 경로 부재.
- SDK 측 우회 경로? — SDK 의 `Server.close()` 가 pending request abort 만 하고 in-flight handler 의 결과는 무시. handler 가 signal 을 받지 않으면 우회 불가.

### 기존 테스트 커버리지

- `src/mcp/plugin-tools-serve.test.ts` (271 lines) — `rg -n "cancel|AbortSignal|signal" src/mcp/plugin-tools-serve.test.ts` 매치 0.
- `plugin-tools-handlers` 단독 test 부재 (`ls src/mcp/plugin-tools-handlers.test.ts` 없음, commit `e75cd46ba6 test: isolate plugin tools mcp handlers` 가 추가했다고 명명되어 있으나 실제 test 는 plugin-tools-serve.test.ts 내부에서 indirect 검증).
- cancellation 단위 테스트 부재.

### 호출 빈도 / 경로 활성 여부

- `setRequestHandler(CallToolRequestSchema, ...)` (tools-stdio-server.ts:17) 는 plugin-tools / openclaw-tools MCP server 가 띄워질 때마다 등록. host 의 모든 callTool 요청이 본 handler 경유.
- 실제 cancellation 빈도는 host UX (사용자 cancel 누름, host 재시작, tool 자체 timeout 초과) 에 종속. 어느 경로든 본 handler 가 통과 지점.

### 설정 / feature flag

- signal 전달을 켜는 flag 없음. handler 작성에서 누락된 코드 라인. config 로 우회 불가.

### Primary-path inversion (CAL-001)

unconditional cancel 경로 (다른 entrypoint 가 동일 tool 을 cancel 가능하게 호출) 가 존재하는가? agent loop (src/agents/**, 본 셀 외) 에서는 동일 plugin tool 이 signal 과 함께 호출될 수 있으나, 그 경로는 plugin-tools-handlers.ts 를 거치지 않음. **본 file 의 흐름에서는 cancel 경로가 존재 자체가 없다.** CAL-001 의 함정 (defensive cancel 을 못 본 척) 아님.

### Hot-path-vs-test-path consistency (CAL-003)

기존 test 가 handler 를 mock 으로 우회 (`plugin-tools-serve.test.ts:11-14` 의 `createToolsMcpServerMock`). production hot-path 에서는 SDK 가 항상 extra.signal 을 제공하나 handler 가 받지 않아 미사용. 재현 테스트는 실제 SDK Server 인스턴스 + 실제 `setRequestHandler` 등록 + `Protocol.cancelRequest(...)` 또는 transport.close() 를 trigger 한 후 `tool.execute` 의 signal 가 abort 됐는지 확인. mock 으로 SDK 측을 단순화하면 false negative.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/mcp/plugin-tools-handlers.ts` → 5 건:
  - `471489159b fix(mcp): honor plugin tool policy` — policy 축.
  - `0df90d9b8d fix: trace plugin tool factory timings` — observability 축.
  - `e4b09e1bf3 fix(mcp): serialize raw plugin tool results` — 결과 serialization.
  - `5fa0d282a8 fix(mcp): stringify plugin tool content safely` — 결과 serialization.
  - `8f3b99c512 fix(mcp): block owner-only tools in ACPX bridge` — 보안.
  signal / cancellation 축 0.
- `gh pr list --repo openclaw/openclaw --state open --search "callTool OR AbortSignal mcp plugin in:title,body"` → bundle-mcp PR #73536 (`pass configured timeout to MCP callTool requests`) 와 #78160 (`extend bundle MCP tool request timeout`) — 둘 다 agents 도메인 (bundle-mcp transport 의 client side) + 다른 축 (timeout 전달). 본 셀의 plugin-tools-handlers signal 전파 0 건.

## Self-check

### 내가 확실한 근거

- plugin-tools-handlers.ts:54 `tool.execute(\`mcp-\${Date.now()}\`, params.arguments ?? {})` 의 인자 개수 (2 개) 가 AnyAgentTool.execute 시그니처 (최대 4 개) 보다 2 개 적음 (Read 로 확인).
- tools-stdio-server.ts:17-19 의 setRequestHandler callback 이 `async (request) => handlers.callTool(request.params)` — extra 무시 (Read 로 확인).
- SDK protocol.d.ts:173-194 의 RequestHandlerExtra 타입에 `signal: AbortSignal` 명시 (Read 로 확인).
- AnyAgentTool.execute 시그니처 (src/agents/tools/common.ts:21-29) 가 signal 4번째 위치 인자 (Read 로 확인).

### 내가 한 가정

- SDK 의 `Protocol` 가 cancellation notification 도착 시 정말로 extra.signal 의 AbortController 를 abort 한다는 가정 — SDK 동작에 의존. SDK 소스 직접 확인 안 함 (protocol.d.ts type 만 확인).
- AnyAgentTool 구현체들이 실제로 signal 을 honor 한다는 가정 — 본 셀 외 (src/agents/tools/) 의 cron-tool, browser, memory-lancedb 등의 execute 구현이 signal listener 를 갖고 있는지는 확인 안 함. 만약 다수의 tool 이 signal 을 무시한다면 본 결함의 영향이 줄어듦. 그러나 시그니처가 의도적으로 signal 자리를 비워둔다는 것은 호출 측에서도 전달해야 한다는 계약.
- host (Claude Code SDK) 가 cancellation notification 을 적시에 송신한다는 가정 — host UX 에 의존. host 가 송신 안 하면 본 결함이 발현 안 됨 (대신 transport close 경로에서만 발현).

### 확인 안 한 것 중 영향 가능성

- 다른 ACPX bridge 또는 in-process 호출 (예: pi-tools 의 직접 호출) 에서는 signal 이 전달될 수 있음. plugin-tools-handlers 만 누락 — production 영향 범위는 MCP 경로 한정.
- bundle-mcp 도메인 (agents/pi-bundle-mcp-*.ts) 의 client 측은 cancellation 을 송신하는 측 — host role. 본 셀에서는 server role 만 다루지만 양측 통합 시 lifecycle 일관성 부족.
- SDK 의 `Server.close()` 가 pending request 에 대해 어떤 abort 정책을 갖는지 — protocol.d.ts 만 보면 명시 안 됨. abort 가 발생해도 handler 가 무시하면 child 가 응답 없이 멈춤.
