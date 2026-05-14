---
id: FIND-mcp-lifecycle-001
cell: mcp-lifecycle
title: tools-stdio-server shutdown floats server.close() — drops in-flight responses
file: src/mcp/tools-stdio-server.ts
line_range: 30-40
evidence: "```ts\n  const shutdown = () => {\n    if (shuttingDown) {\n      return;\n\
  \    }\n    shuttingDown = true;\n    process.stdin.off(\"end\", shutdown);\n  \
  \  process.stdin.off(\"close\", shutdown);\n    process.off(\"SIGINT\", shutdown);\n\
  \    process.off(\"SIGTERM\", shutdown);\n    void server.close();\n  };\n```\n"
symptom_type: lifecycle-gap
problem: '''`connectToolsMcpServerToStdio` 의 shutdown 핸들러가 `server.close()` 를 `void`

  로 발사 (L39) — promise 를 await 하지 않고 핸들러가 즉시 return 한다. 이 server 는

  `servePluginToolsMcp` (plugin-tools-serve.ts:79) 와 `serveOpenClawToolsMcp`

  (openclaw-tools-serve.ts:29) 둘 다 standalone stdio MCP 프로세스의 유일한 lifetime

  앵커이며, plugin tool 실행 중 (예: memory-lancedb 의 memory_recall, cron-tool 의

  cron_list 등) SIGTERM / SIGINT / stdin close 가 도달하면 (a) caller 들은 즉시 return

  하여 process 가 자연 종료를 시작하고, (b) in-flight `tool.execute` await 의 결과가

  돌아와도 floating `server.close()` 가 이미 transport 를 닫는 중이라 JSON-RPC

  response 가 stdout 로 전송되지 못한 채 child 가 exit. 더해서 SDK 의

  `Server.close()` 가 reject 하면 (transport 가 이미 닫혔거나, in-flight notification

  큐가 비정상 상태) caller 가 await 하지 않으므로 `unhandledRejection` 으로 전파된다 —

  channel-server.ts:68 의 `await server.close()` 와 비대칭.''

  '
mechanism: "'1. host (Claude Code / Codex / ACPX bridge) 가 `node ... src/mcp/plugin-tools-serve.ts`\n\
  \   를 stdio child 로 spawn.\n2. host 가 listTools 후 callTool({name: \"memory_recall\"\
  , ...}) 요청 송신.\n3. handler.callTool (plugin-tools-handlers.ts:45-70) 이 `tool.execute(...)`\
  \ await.\n   plugin tool 이 외부 API / LLM / DB 호출이면 수 초~수십 초 소요.\n4. 그 시점 host 가 user\
  \ cancel / config reload / parent exit 등으로 child stdin 을\n   close 또는 SIGTERM 전송.\n\
  5. tools-stdio-server.ts:30-41 `shutdown` 발사. listener detach 후 L39\n   `void server.close()`\
  \ — promise discarded.\n6. caller `servePluginToolsMcp` 의 `await connectToolsMcpServerToStdio(server)`\n\
  \   (plugin-tools-serve.ts:79) 는 `await server.connect(transport)` 만 await 후\n \
  \  resolve, 함수 종료. process 가 event loop 비기를 기다림.\n7. SDK Server.close() 가 transport.close()\
  \ 호출 → stdio transport 의 stdout 종료.\n   in-flight `tool.execute` 가 아직 resolve 안\
  \ 했으면 ① 결과 도착 시 stdout 가 닫혀\n   `EPIPE`/silent drop, ② tool.execute 가 promise 로만\
  \ living 인 동안 process 가 종료\n   순서를 결정. node 의 SIGTERM 기본 동작 (없으면 immediate exit)\
  \ 은 once SIGTERM 등록\n   으로 흡수했지만, listener detach 후 두 번째 SIGTERM 이 오면 default action\
  \ = process\n   killed (exit 코드 143) → response 절대 못 보냄.\n8. server.close() 가 reject\
  \ 하는 경우 (SDK 내부 transport state 가 이미 closed —\n   예: stdin close 와 SDK 의 close()\
  \ 가 race 하면 가능) `void` 으로 swallowed →\n   node `unhandledRejection` 이벤트 trigger.'\n"
root_cause_chain:
- why: 왜 `server.close()` 가 await 되지 않는가?
  because: '`connectToolsMcpServerToStdio` 가 단일 await 지점 (`await server.connect(transport)`,

    L47) 후 즉시 return 하는 fire-and-forget 패턴을 채택. shutdown 핸들러는 등록 직후

    sync return 되어야 SIGINT/SIGTERM 의 default action (process kill) 을 피하므로

    close() 를 await 못 함 — 그러나 그렇다 해서 floating `void` 으로 두면 reject 시

    uncaught.

    '
  evidence_ref: src/mcp/tools-stdio-server.ts:39
- why: 왜 await 없는 close 가 in-flight 응답 누락을 유발하는가?
  because: 'SDK 의 `Server.close()` 는 transport 와 protocol layer 를 동기적으로 invalidate.

    handler.callTool 의 `await tool.execute(...)` 가 동시에 진행 중이면 execute 가

    resolve 한 시점에는 이미 transport 가 닫혀 있어 SDK 가 response 를 send 시도해도

    stdout 가 EOF, 또는 internal "transport not connected" error. 어느 쪽이든 host

    쪽으로는 응답이 가지 않고 client request timeout 까지 hang.

    '
  evidence_ref: src/mcp/tools-stdio-server.ts:17-19 (setRequestHandler callback —
    response 송신은 SDK 가 담당)
- why: 왜 비대칭이 유지되어 왔는가?
  because: '같은 repo 의 `channel-server.ts:68` 은 동일 close 를 `await server.close()` 로

    처리한다. `tools-stdio-server.ts` 는 commit `61ab68f5c9` (refactor: share MCP

    tools stdio server) 에서 통합되며 plugin/openclaw built-in 두 caller 의 공유

    shutdown 으로 도입됐고, channel-server.ts 와 다르게 caller wrapper 의 finally

    cleanup 도 부재. shutdown 핸들러가 sync 여야 한다는 제약 때문에 floating 으로

    두었으나 in-flight 호출 drain 패턴 (try/await/timeout) 부재.

    '
  evidence_ref: 'git: 61ab68f5c9 refactor: share MCP tools stdio server'
- why: 왜 host 측 timeout 으로 회복되지 않는가?
  because: 'MCP host (Claude Code SDK) 의 callTool timeout 은 default 60 초 단위. 그 사이

    child process 가 정확히 SIGTERM 받은 시점에 한해 발생하므로 평소엔 silent.

    그러나 production 환경 (cron stagger, plugin hot-reload, host restart) 에서

    child SIGTERM 은 정규 경로이며 매번 발생. 누적 결과: 모든 host-initiated

    restart 시 마지막 in-flight tool call 의 응답이 host log 에 "timeout / no

    response" 로 기록되고 host 가 retry 또는 fail.

    '
  evidence_ref: src/mcp/plugin-tools-handlers.ts:54 (tool.execute 가 await 인데 cancel
    signal 없음 — FIND-mcp-lifecycle-002 와 연결)
impact_hypothesis: wrong-output
impact_detail: '''정성: plugin-tools / openclaw-tools standalone MCP 서버를 stdio 로 띄운
  host

  (Claude Code, Codex, ACPX bridge) 가 child 를 SIGTERM/SIGINT/stdin close 로 종료

  할 때, 종료 시점에 in-flight 였던 callTool 요청은 응답을 받지 못한다. host 쪽 효과:

  (a) callTool promise 가 timeout (host 기본 60 초) 까지 hang, (b) 사용자에게는 tool

  실행이 silent fail 또는 "no response" 로 표면화. 정량 추정: plugin-tools server

  의 통상 호출 빈도 (대화당 N 회) × child restart 빈도 (host 재시작/hot-reload).

  추가로 server.close() 가 reject 하면 process 종료 직전에 unhandledRejection 발생 —

  Node 22 default 에서는 deprecation warning, 향후 default-throw 정책 적용 시 child

  exit code 변경 가능. 재현 조건: (1) plugin tool 이 100ms 이상 걸리는 외부 호출,

  (2) 호출 도중 child 에 SIGTERM 송신. 재현 가능성 결정적.''

  '
severity: P2
counter_evidence:
  path: src/mcp/channel-server.ts
  line: 67-68
  reason: "'channel-server.ts 의 shutdown (L83-94) 은 `close().then(resolveClosed, resolveClosed)`\n\
    로 close 의 success/failure 양쪽을 resolveClosed 에 흡수하고, 더 위의 try/finally\n(L102-109)\
    \ 가 `await closed` 로 close 완료 후에만 함수 return — **이쪽은\nunconditional drain**. tools-stdio-server.ts\
    \ 는 같은 패턴이 부재.\n\n확인한 반증 카테고리:\n(1) 숨은 방어 / defense-in-depth: `rg -n \"await\\\
    \\s+server\\\\.close|close\\\\(\\\\)\\\\.then\" src/mcp/`\n    → channel-server.ts\
    \ 만 매치. tools-stdio-server.ts 의 caller\n    (`servePluginToolsMcp`, `serveOpenClawToolsMcp`)\
    \ 도 wrapper try/finally 부재.\n(2) 기존 테스트: `rg -n \"server\\\\.close|shutdown\"\
    \ src/mcp/plugin-tools-serve.test.ts\n    src/mcp/openclaw-tools-serve.test.ts`\
    \ → 둘 다 connectToolsMcpServerToStdio\n    를 mock 으로 대체 (plugin-tools-serve.test.ts:43-45),\
    \ shutdown 시 in-flight\n    drain 시나리오 어서트 없음.\n(3) 호출 빈도: plugin-tools/openclaw-tools\
    \ MCP server 는 host (Claude Code SDK,\n    Codex, ACPX bridge) 가 매 session restart\
    \ / config reload 마다 spawn → SIGTERM\n    는 정규 경로. 발현 조건 = production hot-path.\n\
    (4) primary-path inversion (CAL-001): unconditional drain 경로 (await close) 의\n\
    \    존재 여부 = **부재**. shutdown 의 floating void 만 존재 → 정상 경로 자체가\n    없음. CAL-001\
    \ 함정 (defensive cleanup 못 본 척) 아님.\n(5) Hot-path-vs-test-path (CAL-003): plugin-tools-serve.test.ts\
    \ 가\n    connectToolsMcpServerToStdio 를 mock 하므로 production hot-path 의 shutdown\n\
    \    sequence 는 test 에서 검증되지 않음. 재현 테스트는 실제 SDK Server 인스턴스\n    + 실제 stdio transport\
    \ 로 SIGTERM 직전 callTool 을 발사하는 방식 필요.\n(6) Upstream dup (CAL-004/008): `git log\
    \ upstream/main --since=\"6 weeks ago\"\n    -- src/mcp/tools-stdio-server.ts\
    \ src/mcp/plugin-tools-serve.ts\n    src/mcp/openclaw-tools-serve.ts src/mcp/plugin-tools-handlers.ts`\
    \ → 5 건\n    (e4b09e1bf3 serialize, 5fa0d282a8 stringify, 61ab68f5c9 refactor\
    \ share,\n    8f3b99c512 owner-only block, e75cd46ba6 test isolate) — 모두 result\n\
    \    serialization / 보안 / refactor 축, in-flight drain 0 건. `gh pr list --state\
    \ open\n    --search \"plugin-tools-serve OR tools-stdio-server in:title,body\"\
    ` → PR #71648\n    (mcp-memory 셀, channel-bridge 한정) 외 본 file 영역 OPEN PR 없음.'\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
- 'B-1-2c: evidence mismatch at src/mcp/tools-stdio-server.ts:29-40 — whitespace or
  content differs'
---
# standalone tools MCP server shutdown drops in-flight tool responses via floating server.close()

## 문제

`connectToolsMcpServerToStdio` (src/mcp/tools-stdio-server.ts:24-48) 가 등록하는 shutdown 핸들러는 L39 에서 `void server.close()` — promise 를 `void` 으로 버린다. 같은 repo 의 `channel-server.ts:68` 은 같은 동작을 `await server.close()` 로 처리하는데, tools-stdio-server.ts 만 비대칭이다.

이 floating close 는 두 문제를 만든다:

1. **In-flight call response 누락**: SIGTERM/SIGINT/stdin close 가 도착한 시점에 `handler.callTool` 이 `tool.execute(...)` 를 await 중이면, close() 가 transport 를 비동기로 닫는 동안 process 는 caller (`servePluginToolsMcp` / `serveOpenClawToolsMcp`) 가 이미 return 한 상태라 event-loop drain 만 기다린다. tool.execute 가 resolve 한 후 SDK 가 response 를 stdout 으로 송신하려 해도 stdout 가 EOF, host 쪽에는 timeout 까지 응답이 가지 않는다.

2. **Unhandled rejection**: `SDK.Server.close()` 가 reject 하는 경우 (transport 가 이미 닫혔거나 internal state mismatch) `void` 으로 swallowed → Node 의 `unhandledRejection` 이벤트 trigger. 현재 (Node 22) default 는 warning 이지만 향후 default-throw 정책 활성화 시 child exit code 가 변경된다.

## 발현 메커니즘

```
host                         child (servePluginToolsMcp)
 |--- spawn ----------------->|
 |                            | connectToolsMcpServerToStdio
 |                            | - register stdin/SIGINT/SIGTERM listeners
 |                            | - await server.connect(transport) ✓
 |                            | (function returns; event-loop alive on stdio)
 |
 |--- callTool ------------> handler.callTool
 |                            | tool.execute(...)  ── awaiting LLM/DB ──
 |
 |--- SIGTERM ────────────────|
 |                            shutdown() fires:
 |                              - detach listeners
 |                              - void server.close()     ← floating
 |                            shutdown returns sync.
 |                            tool.execute eventually resolves.
 |                            handler tries to send response.
 |                            transport.send() → stdout closed → silent drop
 |                            or unhandledRejection from close() promise.
 |--- (no response) <─────────┘
host: callTool timeout (~60s) → fail
```

`channel-server.ts` 와 비교:

| 면 | channel-server.ts | tools-stdio-server.ts |
|---|---|---|
| close 호출부 | L93 `close().then(resolveClosed, resolveClosed)` | L39 `void server.close()` |
| close 완료 대기 | L105/108 `await closed` 두 곳 | 없음 |
| close 실패 처리 | resolveClosed 흡수 | swallowed → unhandledRejection |
| caller 의 finally drain | L106-109 try/finally | 없음 (`servePluginToolsMcp`/`serveOpenClawToolsMcp` 둘 다 await 후 즉시 return) |

## 근본 원인 분석

1. **Refactor 통합 시 누락**: commit `61ab68f5c9 refactor: share MCP tools stdio server` 에서 plugin-tools / openclaw-tools 두 caller 의 shutdown 을 통합하면서 channel-server.ts 의 close-then-await 패턴을 그대로 가져오지 않음. shutdown 핸들러가 sync 여야 한다는 제약 (SIGINT/SIGTERM listener 가 async 이면 process kill 까지 race) 으로 fire-and-forget 채택.

2. **In-flight call drain 패턴 부재**: SDK Server.close() 는 in-flight handler 결과를 buffer 하지 않는다. 정상 패턴은 (a) accept 멈추기 → (b) in-flight 종료까지 await → (c) transport 닫기 순. 본 코드는 (b) 가 부재. handler.callTool 자체가 abort signal 을 받지 않는 점도 동일 cluster (FIND-mcp-lifecycle-002 와 cross_refs).

3. **Caller wrapper 의 cleanup 부재**: `servePluginToolsMcp` (plugin-tools-serve.ts:67-80) 와 `serveOpenClawToolsMcp` (openclaw-tools-serve.ts:27-30) 둘 다 `await connectToolsMcpServerToStdio(server)` 하나만 호출 후 함수 종료. try/finally 가 없어 shutdown 후 추가 drain 불가능한 구조.

## 영향

- **사용자 체감**: Claude Code / Codex / ACPX bridge 에서 plugin tool 호출 도중 host 가 child 를 restart 하면 tool 응답이 timeout. UI 에는 "tool call timed out" 로 표시. 다음 시도에서 retry 되지만 LLM context 에서는 "이전 tool call 실패" 로 잘못 반영될 수 있음.
- **노출 경로**: standalone MCP server 가 띄워지는 모든 상황 — Claude Code SDK 가 외부 MCP server 로 openclaw plugin-tools 를 등록하는 경우, ACPX bridge, Codex sandbox.
- **빈도**: child restart 마다 1 회 (in-flight 가 있을 때만). plugin tool (예: memory-lancedb, browser, search) 중 외부 호출이 잦은 도구는 항상 in-flight 일 확률이 비례.
- **추가 위험**: `server.close()` reject 시 unhandledRejection. Node 22 default 는 warning 이지만 `--unhandled-rejections=strict` 환경에서 즉시 process exit 1.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "await\s+server\.close|close\(\)\.then" src/mcp/` → channel-server.ts L68/L93 만 매치. tools-stdio-server.ts 는 floating only.
- `rg -n "drain|in.flight|pending.*callTool" src/mcp/` → 매치 0. SDK 측 drain 보장 없음 (SDK 의 `Server.close()` 는 in-flight handler 무시).
- caller wrapper (servePluginToolsMcp / serveOpenClawToolsMcp) try/finally 부재 — `rg -n "finally" src/mcp/plugin-tools-serve.ts src/mcp/openclaw-tools-serve.ts` → 0.

### 기존 테스트 커버리지

- `src/mcp/plugin-tools-serve.test.ts` (271 lines) 는 `connectToolsMcpServerToStdio` 를 `connectToolsMcpServerToStdioMock` (L43-45) 으로 대체. 실제 shutdown sequence 검증 없음.
- `src/mcp/openclaw-tools-serve.test.ts` (22 lines) 도 shutdown 시나리오 미포함.
- `src/mcp/channel-server.shutdown-unhandled-rejection.test.ts` 는 channel-server 의 shutdown 만 검증. tools-stdio-server 의 동등 테스트는 부재.

### 호출 빈도 / 경로 활성 여부

- `servePluginToolsMcp` 와 `serveOpenClawToolsMcp` 는 둘 다 `import.meta.url === pathToFileURL(process.argv[1] ?? "").href` 분기로 standalone 실행 (plugin-tools-serve.ts:82-86, openclaw-tools-serve.ts:32-36). production 에서 host 가 stdio child 로 spawn 하는 정규 경로.
- SIGTERM 은 host process supervisor (Claude Code SDK, Codex) 가 child 재시작 시 표준 신호.

### Primary-path inversion (CAL-001)

unconditional drain 경로 (await close, finally drain) 존재 여부 = **부재**. floating void 만 존재. 즉 "숨은 defensive cleanup 을 놓치고 false positive 가 된다" 는 CAL-001 의 함정에 빠지지 않는다 — 대안 경로 자체가 없다.

### Hot-path-vs-test-path consistency (CAL-003)

기존 test 가 `connectToolsMcpServerToStdio` 자체를 mock 처리. production hot-path (실제 stdio transport, 실제 SDK Server, 실제 tool.execute) 에서의 shutdown sequence 는 test 로 검증되지 않음. 재현 테스트는 다음 조건 필요:
- 실제 `Server` 인스턴스 + 실제 stdio transport.
- `tool.execute` 가 100ms 이상 걸리는 deferred resolve.
- 그 사이 process.emit("SIGTERM") 송신 (또는 stdin close).
- handler 가 응답 송신 시도 → stdout 가 닫혔으면 silent drop 또는 unhandledRejection 관측.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/mcp/tools-stdio-server.ts src/mcp/plugin-tools-serve.ts src/mcp/openclaw-tools-serve.ts src/mcp/plugin-tools-handlers.ts` → 5 건:
  - `e4b09e1bf3 fix(mcp): serialize raw plugin tool results` — 결과 serialization 축.
  - `5fa0d282a8 fix(mcp): stringify plugin tool content safely` — 결과 serialization 축.
  - `61ab68f5c9 refactor: share MCP tools stdio server` — 본 패턴 도입 commit.
  - `8f3b99c512 fix(mcp): block owner-only tools in ACPX bridge` — 보안 축.
  - `e75cd46ba6 test: isolate plugin tools mcp handlers` — 테스트 축.
  In-flight drain / await close 추가 0 건.
- `gh pr list --repo openclaw/openclaw --state open --search "plugin-tools-serve OR tools-stdio-server in:title,body"` → 매치된 OPEN PR 없음 (PR #71648 은 channel-bridge memory 축 한정).
- `gh pr list --search "callTool OR \"void server.close\" OR abort signal mcp in:title,body"` → in-flight 축 매치 0. bundle-mcp 도메인 PR #73536 은 callTool **timeout** 만 다룸 (본 셀 외 + 다른 축).

## Self-check

### 내가 확실한 근거

- L39 `void server.close();` 실재 (tools-stdio-server.ts Read 로 확인).
- channel-server.ts L93/105/108 의 await drain 패턴 실재 (Read 로 확인).
- caller `servePluginToolsMcp`/`serveOpenClawToolsMcp` 가 finally 없이 단일 await 후 return 하는 구조 (Read 로 확인).
- SDK `Server.close()` 가 in-flight handler 결과를 buffer 하지 않는다는 점은 SDK protocol.d.ts (`RequestHandlerExtra.signal: AbortSignal` 만 제공) 로 간접 확인.

### 내가 한 가정

- host 측 callTool timeout 이 60 초라는 기본값 — Claude Code SDK 의 default 이며 host 가 override 가능. 작으면 fail 이 빨라지고, 매우 크면 hang.
- `child` 가 SIGTERM 후에도 in-flight `await tool.execute` 가 실제로 resolve 한다는 가정 — node 의 SIGTERM default action 을 `process.once("SIGTERM", shutdown)` 으로 흡수했으므로 child 가 즉시 종료되지 않고 event-loop drain 까지 살아있다고 가정. 두 번째 SIGTERM 이 오면 default kill 로 결과 변경.
- `Server.close()` 가 transport 가 닫혔을 때 throw 한다는 가정은 SDK 동작에 의존 — SDK 의 정확한 close 동작은 직접 확인 안 함.

### 확인 안 한 것 중 영향 가능성

- SDK 가 in-flight pending request 의 abort 를 trigger 할 때 handler.callTool 안의 `await tool.execute` 가 reject 되는지 — RequestHandlerExtra.signal 을 handler 가 무시하면 abort 가 전파 안 됨 (FIND-mcp-lifecycle-002 와 연결). 그렇다면 in-flight 가 timeout 까지 정말로 hang.
- channel-server.ts:93 의 `close().then(resolveClosed, resolveClosed)` 패턴이 unhandledRejection 을 완전히 차단하는지 — `.then` 이 두 핸들러 모두 등록되므로 차단 OK. tools-stdio-server.ts 의 floating 만이 unhandledRejection 노출.
- host (Claude Code SDK) 가 child 응답 누락을 retry 하는 정책 — 보장 안 됨. 사용자 컨텍스트에서는 "tool call failed" 로 보인다.
