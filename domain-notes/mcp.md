# mcp 도메인 노트

openclaw 의 MCP (Model Context Protocol) — server + client transport — 서브시스템에 대한 영구 관찰 기록. 페르소나/세션별 append-only.

## 도메인 개요

openclaw 의 MCP 도메인은 **두 개의 서로 다른 라이프사이클 흐름** 으로 구성된다:

1. **MCP server (out-of-process, stdio)**: openclaw 가 외부(Claude Code SDK / Codex / 임의 MCP client) 에 자기 기능을 노출. CLI command `openclaw mcp serve` 로 long-running stdio 프로세스 기동. 핵심: `src/mcp/channel-server.ts` + `channel-bridge.ts`.

2. **MCP client transport (in-process)**: openclaw 가 외부 MCP server (uvx/npx 로 띄워지는 3rd-party 도구, 또는 url-based HTTP MCP) 를 child_process 로 spawn 해 tool 을 가져오는 역방향. `src/agents/mcp-stdio-transport.ts` + `mcp-transport.ts` + `mcp-transport-config.ts`.

도메인 경계 메모:
- `src/agents/mcp-http.ts` 는 **client 쪽 launch config 해석** (server 가 아닌 client 가 외부 HTTP MCP 에 연결하는 설정). gateway 의 mcp-http 와 혼동 금지 — gateway 도메인의 mcp-http.* 는 별 파일 (control plane) 로, allowed_paths 에서 본 셀은 그 파일을 다루지 않는다.
- `src/agents/pi-bundle-mcp-*` 와 `src/agents/embedded-pi-mcp.ts` 는 agents 도메인 (pi-bundle-mcp-runtime.ts 가 transport 의 detachStderr / disposeSession 의 호출자). 본 셀 allowed_paths 에 포함 안 됨 — cross-domain 정보로 인용만.

## 파일 구조 (upstream/main @ `c070509b7f`, 2026-04-25)

| 파일 | 라인 | 역할 |
|---|---|---|
| `src/mcp/channel-server.ts` | 102 | `serveOpenClawChannelMcp` — stdio MCP server 본체, shutdown wiring (SIGINT/SIGTERM/stdin close) |
| `src/mcp/channel-bridge.ts` | 516 | `OpenClawChannelBridge` — gateway client + queue + pending Maps + permission/approval state. **메모리 서피스 집중부** |
| `src/mcp/channel-shared.ts` | 209 | 타입 정의 + helper. `PendingApproval`, `QueueEvent`, `ClaudePermissionRequestSchema` (zod) |
| `src/mcp/channel-tools.ts` | 188 | server.tool() 등록 (conversations_list / messages_send / permissions_respond 등 9개 tool) |
| `src/mcp/tools-stdio-server.ts` | 48 | generic stdio MCP server factory (plugin-tools / openclaw-tools 가 공유) |
| `src/mcp/plugin-tools-handlers.ts` | 67 | listTools / callTool handler (plugin proxy 모음) |
| `src/mcp/plugin-tools-serve.ts` | 52 | plugin tools standalone MCP server entry |
| `src/mcp/openclaw-tools-serve.ts` | 37 | openclaw built-in tools (cron-tool 등) standalone server entry |
| `src/agents/mcp-stdio-transport.ts` | 147 | `OpenClawStdioClientTransport` — child_process spawn + ReadBuffer + stderr PassThrough |
| `src/agents/mcp-stdio.ts` | 54 | stdio launch config 해석 (env / cwd / args) |
| `src/agents/mcp-http.ts` | 73 | http(s) launch config 해석 |
| `src/agents/mcp-transport.ts` | 126 | `resolveMcpTransport` — config → Transport 인스턴스 (stdio / sse / streamable-http) + attachStderrLogging |
| `src/agents/mcp-transport-config.ts` | 150 | stdio/http 우선순위 결정 + connectionTimeoutMs |
| `src/agents/mcp-config-shared.ts` | 66 | env/headers/array sanitize helper |
| `src/config/mcp-config.ts` | 162 | listConfiguredMcpServers / setConfiguredMcpServer / unsetConfiguredMcpServer (config file mutate) |
| `src/cli/mcp-cli.ts` | 150 | commander 등록 (`openclaw mcp serve|list|show|set|unset`) |

## 메모리 서피스 인벤토리 (channel-bridge.ts 중심)

### Map / Set / Array (instance-level)

| 변수 | 파일:라인 | 쓰기 경로 | 정리 경로 | 키 도메인 / 성장률 / 평가 |
|---|---|---|---|---|
| `queue: QueueEvent[]` | channel-bridge.ts:48 | L354 `queue.push(event)` | L355-357 `while (queue.length > QUEUE_LIMIT) queue.shift()` (cap 1000) | **bounded by QUEUE_LIMIT=1000** ✓ |
| `pendingWaiters: Set<PendingWaiter>` | channel-bridge.ts:49 | L269 `add(waiter)` | (a) waiter.resolve 내부 L259 `delete`, (b) close() L148 `clear()`, (c) setTimeout fallback L265 → resolve | **bounded** (close-clear + per-waiter timeout) ✓ |
| `pendingClaudePermissions: Map<string, ClaudePermissionRequest>` | channel-bridge.ts:50 | L279 `set(requestId, …)` | L459 `delete(requestId)` (정규식 매칭 시에만, conditional-edge) | **unbounded** — TTL/cap/close-clear 모두 부재 → **FIND-mcp-memory-001** |
| `pendingApprovals: Map<string, PendingApproval>` | channel-bridge.ts:51 | L374 `set(id, … expiresAtMs)` | L389 `delete(id)` (gateway resolved 이벤트 수신 시에만, conditional-edge) | **unbounded** — expiresAtMs 저장만, expiry 로직 부재 + close-clear 부재 → **FIND-mcp-memory-002** |

### 모듈-level singleton

`channel-server.ts`, `channel-bridge.ts`, `tools-stdio-server.ts`, `plugin-tools-serve.ts`, `openclaw-tools-serve.ts`, `openclaw-tools-serve.ts` — 모듈-level Map/Set 0건. 모든 state 는 instance-bound (Bridge / Server 객체).

### setInterval / setTimeout

| 위치 | 용도 | 정리 |
|---|---|---|
| channel-bridge.ts:265 `setTimeout(() => waiter.resolve(null), timeoutMs)` | waitForEvent timeout | resolve 시 `clearTimeout(waiter.timeout)` (L143, L362). close() 도 clear 후 `pendingWaiters.clear()`. ✓ |
| mcp-stdio-transport.ts:23 `setTimeout(resolve, ms).unref()` | close 의 race 용 delay() | 자체 unref + Promise.race 후 GC. ✓ |

setInterval / sweeper 0건. → leak Map 의 자동 expiry 가 없는 이유.

### child_process / stream handle

`OpenClawStdioClientTransport` (mcp-stdio-transport.ts):
- L67 `this.process = child` 보관.
- L74 child.on('close') → `this.process = undefined`.
- L79 `child.stdout.on('data')` arrow function — close 시 별도 detach 없음. 단, child 종료 시 stream 도 close 되어 listener 자동 GC.
- L85 `child.stderr.pipe(this.stderrStream)` — child 종료 시 자동 unpipe.
- L112 close() 가 stdin.end() → killProcessTree(pid) → readBuffer.clear() 까지 정상 정리.

`stderrStream: PassThrough | null` (L33, L38) — stderr="pipe"|"overlapped" 이면 항상 생성. instance 생애 동안 유지. transport 객체 GC 시 함께 회수.

`attachStderrLogging` (mcp-transport.ts:21-47):
- stderr.on('data', onData) 등록 후 detach closure 반환.
- 호출자: `pi-bundle-mcp-runtime.ts:255` (BundleMcpSession 에 보관) → `disposeSession` (L134) `session.detachStderr?.()` 호출 ✓.
- caller 가 disposeSession 을 안 부르면 listener 잔존 — pi-bundle-mcp-runtime 도메인 책임.

### EventEmitter / addEventListener

`channel-server.ts` shutdown wiring:
- L89-92 `process.stdin.once("end" / "close")` + `process.once("SIGINT" / "SIGTERM")` 등록.
- L80-83 shutdown() 첫 줄에서 `process.stdin.off()` + `process.off()` 로 detach. shuttingDown guard 로 idempotent.
- 같은 패턴이 `tools-stdio-server.ts` L42-45 / L35-38 에 있음.

`channel-bridge.ts` 의 GatewayClient onEvent / onHelloOk / onConnectError / onClose 콜백 (L113-126) — 객체 생애 동안 유지. `gateway.stopAndWait()` 후 GC.

→ **process-level listener 누수 없음**. close 패턴이 idempotent + once + off 짝.

## 각 서피스의 cleanup/TTL/cap 상태 (R-5 분류)

| 서피스 | 경로 | 조건 | 평가 |
|---|---|---|---|
| `queue` cap | L355 while-shift | unconditional on push | **bounded** ✓ |
| `pendingWaiters` clear (close) | L148 | unconditional on close() | **bounded** ✓ |
| `pendingWaiters` setTimeout | L265 | per-waiter, configurable | conditional-edge (timeoutMs > 0) |
| `pendingClaudePermissions` delete | L459 | regex match + sessionKey check | **conditional-edge** — 미응답시 영구 잔존 |
| `pendingApprovals` delete | L389 | gateway resolved event | **conditional-edge** — gateway 누락시 영구 잔존 |
| close() Map clear | 부재 | — | **gap** for two pending Maps |
| sweeper / setInterval | 부재 | — | **gap** — TTL 기반 정리 0건 |
| cap/FIFO for pending Maps | 부재 | — | **gap** — queue 의 QUEUE_LIMIT 미적용 |

## transport boundary (stdio vs http)

- **stdio path**: `OpenClawStdioClientTransport` 가 child_process spawn → spawn 실패 / error / close 모두 wired. close() 가 stdin.end → kill-tree → readBuffer.clear. detachStderr 는 transport 외부 (mcp-transport.ts) 책임.
- **HTTP/SSE path**: `SSEClientTransport` / `StreamableHTTPClientTransport` 는 SDK 측. openclaw 가 헤더/fetch 만 주입. transport 자체 lifetime 은 SDK + caller 가 관리. detachStderr 무관.

본 셀에서 stdio 쪽이 leak 후보가 더 많고 http 쪽은 SDK 위임 — FIND 가 stdio 변형에 집중되는 이유.

## 메인테이너 우선순위 매핑

메인테이너 공개 우선순위 (CLAUDE.md 인용): "memory, plugin loading, cron, reliability".

본 도메인의 memory 축은:
- channel-bridge.ts 의 두 pending Map (FIND-001/002) — Claude SDK / Codex 채널 브리지가 long-running stdio 프로세스로 hours 단위 가동.
- plugin loading 측면: `plugin-tools-serve.ts` 의 `resolvePluginTools` 가 매 process 시작 시 1회만 호출 → tools 배열은 startup-fixed, runtime growth 없음.
- reliability: WS drop 시 pendingApprovals 누수가 reliability 면에서도 의미 (서버-클라이언트 sync mismatch).

## 향후 셀 후보 단서

이 도메인은 다음 type 축 확장 여지 충분:

### `mcp-lifecycle` 후보 (lifecycle-auditor 적합)

- `OpenClawChannelBridge.start()` 실패 후 partial 정리 — `bootstrap` 실패 vs `gateway.start()` 실패 vs `sessions.subscribe` 실패 (L322-329) — 각 단계에서 close() 호출 보장 검증 필요.
- `OpenClawStdioClientTransport.start()` 의 spawn 실패 시 `this.process = child` 가 set 된 채 reject (L67 set, L70 reject). 후속 close() 가 처리 가능한 상태인지 — 현재 L113 `processToClose = this.process` 로 안전해 보이나 spawn 'error' 후 close 와 child.on('close') 사이의 race 검토 가치.
- `pi-bundle-mcp-runtime.ts:255` `disposeSession` 가 catalog build 중간에 실패 시 부분 등록된 sessions 들이 정리되는지 — agents 도메인이지만 MCP transport 와 강결합.
- `tools-stdio-server.ts` 의 shutdown 이 `void server.close()` (L39) — `await` 없이 floating promise. server.close 가 throw 시 처리 부재.

### `mcp-concurrency` 후보 (concurrency-auditor 적합)

- `OpenClawChannelBridge` 가 gateway WS 재연결 시 기존 `pendingWaiters` 의 timeout 과 새 inbound event 매칭 race.
- `pendingClaudePermissions.set` (L279) 과 `delete` (L459) 사이에 이벤트 순서 race — Claude 가 같은 requestId 로 중복 보내면 set 이 entry 덮어쓴 상태에서 첫 응답이 삭제 → 두 번째 응답은 false-negative.
- gateway event handler 가 async (L113 `void this.handleGatewayEvent(event)`) — handler 내부 enqueue/trackApproval 순서가 multiple concurrent event 에서 보장되는지.

### `mcp-error-boundary` 후보 (error-boundary-auditor 적합)

- `channel-bridge.ts` `requestGateway` (L297) — gateway null 시 throw, 그러나 await 없이 호출되는 `void this.handleGatewayEvent(event)` (L113) 안에서 sync throw 가 unhandledRejection 으로 전파 가능.
- `channel-server.ts` shutdown() 의 `close().then(resolveClosed, resolveClosed)` (L85) — close 가 throw 시 reject 도 resolveClosed 로 흡수. 일부 close 실패가 silent 가 됨.
- `mcp-stdio-transport.ts` `processReadBuffer` (L98-110) try/catch 에서 SDK parse error 가 onerror 로만 보고. `start()` 의 reject path 와 별개 — onerror 콜백이 등록 안 됐으면 silent.
- `plugin-tools-handlers.ts` `callTool` (L44-65) 의 try/catch 가 plugin tool throw 를 wrap 하지만, `tool.execute` 가 reject 안 하고 hang 하면 timeout 부재 (handler 차원의 timeout 없음).

## 확인 못 한 영역 (self-critique)

- **gateway WS reconnect 정책**: `onClose` (channel-bridge.ts:122) 가 단순 reject ready — 자동 재연결 / missed event catchup 코드는 GatewayClient (gateway 도메인) 안. 본 셀 scope 외라 catchup 가능 여부 미검증. FIND-002 의 핵심 가정.
- **pi-bundle-mcp-runtime.ts** (allowed_paths 외): `disposeSession` 가 어떤 lifecycle 시점에 호출되는지 (idle TTL? session end?) 미확인. transport detachStderr 가 호출 안 되면 stderr listener 잔존. agents 도메인 셀에서 검증 필요.
- **CODEOWNERS**: `src/agents/mcp-stdio-transport.ts` 가 `*auth*` 또는 `sandbox*` 매치 안 함. 본 도메인 파일들은 일반 ownership 으로 가정 (직접 검증 안 함).
- **SDK side**: `@modelcontextprotocol/sdk` 의 Server / Client 객체 자체의 listener 정리 책임은 SDK 안. openclaw 측에서 `await server.close()` 호출은 함 (channel-server.ts L60). SDK 가 그 안에서 listener detach 하는지는 SDK 신뢰.
- **upstream PR #56420 충돌 분석**: PR #56420 가 sessionKey binding 추가 시 pending entry 키 도메인이 늘어남 (sessionKey 매칭 실패 = 응답 reject = entry 잔존). FIND-001 의 누수 가속화 가능성. 충돌 보다는 가속화 — 별도 PR 으로 leak fix 가 필요.

## 실행 이력

### memory-leak-hunter (2026-04-25, upstream `c070509b7f`)

**셀**: `mcp-memory` (allowed_paths: `src/mcp/**` + 8개 src/agents/mcp-*).
**결론**: **FIND 2건 (P2/P2)**.

**적용 카테고리 (agents/memory-leak-hunter.md §탐지 카테고리)**:

- [x] A. 무제한 자료구조 성장 — 적용 (FIND-001 / FIND-002)
- [x] B. EventEmitter / 리스너 누수 — 적용 (결과: 없음. process listener 들은 once + off 짝, GatewayClient 콜백은 instance 생애)
- [x] C. 강한 참조 체인 (weak 부재) — 적용 (결과: 없음. WeakRef/FinalizationRegistry 사용처 없음. closure 가 큰 객체 잡는 패턴 없음)
- [x] D. 핸들/리소스 누수 — 적용 (결과: child_process / PassThrough / readBuffer 모두 close() 에 wired. stderrStream 은 transport instance 와 동일 lifetime)
- [x] E. 캐시 TTL 부재 — 적용 (FIND-002 의 expiresAtMs gap 이 caching-style 결함과 동치)

**R-3 Grep 핵심 결과**:

```
rg -n "pendingClaudePermissions\.(delete|clear|evict|splice|shift|pop)" src/mcp/ src/agents/ src/cli/ src/config/
  → channel-bridge.ts:459 (conditional-edge)

rg -n "pendingApprovals\.(delete|clear|evict|splice|shift|pop)" src/mcp/ src/agents/ src/cli/ src/config/
  → channel-bridge.ts:389 (conditional-edge)

rg -n "(cap|max|limit|size).*pendingClaudePermissions" / pendingApprovals
  → 0 matches

rg -n "while.*\.size" src/mcp/ src/agents/mcp*
  → 0 matches

rg -n "setInterval\(|clearInterval" src/mcp/ src/agents/mcp*
  → 0 matches (sweeper 부재)

rg -n "expiresAtMs" src/mcp/
  → channel-shared.ts:73 (type 정의)
  → channel-bridge.ts:382 (set 시 entry 에 복사) — 사용처 0
```

**R-7 production hot-path 검증**:
- FIND-001: `handleClaudePermissionRequest` 는 channel-server.ts L42 setNotificationHandler 가 production 의 유일한 호출 경로. Claude SDK 의 모든 tool-use 가 trigger.
- FIND-002: `trackApproval` 는 `handleGatewayEvent` 의 4개 case (exec/plugin × requested/resolved) 가 production 호출. gateway 가 보내는 EventFrame 마다 trigger.

**R-8 upstream 최신성**:
- HEAD `c070509b7f` (2026-04-25 ff 완료).
- 6주 channel-bridge.ts 커밋 6건 (e157c83c65 / 0f7d9c9570 / 74e7b8d47b / ba02905c4f / ec5877346c / 71f37a59ca) — 모두 리팩터/seam 분리/스모크 강화. expiry / cap 추가 0건.
- PR #56420 (OPEN, sessionKey binding 보안축) — 직교. leak fix 미포함.

**CAL-001 회귀 방지**: pending Map 의 cleanup 경로를 R-5 표로 분류, conditional-edge 임을 명시. unconditional 경로 부재 확인 (sweeper 0건 + close-clear 0건).

**CAL-007 회귀 방지**: fresh upstream `c070509b7f` 기준. 6주 commit 분석에서 동일 결함의 fix 부재 확인.

**CAL-008 회귀 방지**: PR #56420 OPEN 이지만 보안축 직교. leak 축 PR / abandoned candidate 검색 결과 dup 없음.

**자체 한계**:
- gateway 의 missed-event catchup 정책 (allowed_paths 외) 미검증. 만약 catchup 보장이 있다면 FIND-002 severity 하향.
- pi-bundle-mcp-runtime 의 disposeSession 호출 시점 미확인. transport 측 leak 은 caller 책임이라 본 FIND 에 포함 안 함.
- production 운영 metrics 부재 — 누적률 정량은 모델 기반 추정.

**다음 페르소나를 위한 힌트**:

- **mcp-lifecycle 셀 후보**: `start()` partial-failure 정리, `OpenClawStdioClientTransport.start()` 의 spawn-error / close race, `tools-stdio-server.ts` 의 floating `void server.close()`.
- **mcp-concurrency 셀 후보**: gateway event handler async + 동일 requestId/approval id 중복 입력 race.
- **mcp-error-boundary 셀 후보**: `void this.handleGatewayEvent(event)` 의 sync throw 가 unhandledRejection 으로 전파, `processReadBuffer` onerror 미등록 silent path.
- **agents 도메인 (pi-bundle-mcp-runtime)**: BundleMcpSession 의 disposeSession 호출 lifecycle 검증 (idle TTL, session end, error path).

### plugin-lifecycle-auditor (2026-05-14, upstream `6a41a54212`)

**셀**: `mcp-lifecycle` (allowed_paths: `src/mcp/**` + 8 개 src/agents/mcp-* + src/config/mcp-config.ts + src/cli/mcp-cli.ts).
**결론**: **FIND 2 건 (P2/P2)** — in-flight cleanup gap 의 두 측면.

**적용 카테고리 (agents/plugin-lifecycle-auditor.md §탐지 카테고리)**:

- [x] A. Load 실패 rollback 부재 — 적용 (결과: `OpenClawChannelBridge.start()` partial init 후보 검토 → caller `serveOpenClawChannelMcp` 의 try/finally + shutdown idempotency 가 unconditional cleanup 제공. FIND 폐기).
- [x] B. Dispose / Unload 경로 누락 — 적용 (FIND-001: tools-stdio-server.ts:39 floating `void server.close()`).
- [x] C. Dynamic import 에러 격리 — 적용 (channel-server.ts:24 의 `await import("../config/config.js")` 와 channel-bridge.ts:89-101 의 `Promise.all([5 imports])` — 둘 다 caller finally 가 catch. FIND 폐기).
- [x] D. Manifest parse 실패 후 partial state — 적용 (mcp-config.ts 의 `replaceConfigFile` 가 atomic validate-then-write. partial state 없음).
- [x] E. Enable/Disable 상태 drift — 적용 (config reload 시 active transport dispose 호출은 host (agents 도메인) 책임. 본 셀 file 영역엔 enable flag 없음).
- [x] F (셀 hints 4-5 in-flight): plugin reload/shutdown 중 in-flight tool call 처리 — **FIND-002**: plugin-tools-handlers.ts:45-70 의 callTool 이 `extra.signal` 무시 → cancellation 전파 부재.

**Lifecycle 핵심 신규 사실 (mcp-memory 셀 도메인 노트에 없는 정보)**:

| 흐름 | 위치 | 동기 vs 비동기 close | drain 보장 |
|---|---|---|---|
| `serveOpenClawChannelMcp` shutdown | channel-server.ts:83-94 | `close().then(resolveClosed, resolveClosed)` + finally `await closed` (L102-109) | unconditional (try/finally) |
| `connectToolsMcpServerToStdio` shutdown | tools-stdio-server.ts:30-41 | `void server.close()` — floating | **부재** (caller 도 finally 없음) |
| `OpenClawChannelBridge.close()` | channel-bridge.ts:162-178 | `await gateway?.stopAndWait().catch(() => undefined)` | unconditional |
| `OpenClawStdioClientTransport.close()` | mcp-stdio-transport.ts:112-131 | stdin.end → race(close, 2s) → killProcessTree → race(close, 2s) → readBuffer.clear | unconditional (within transport scope) |

**SDK abort/signal 전파 패턴 (신규 인벤토리)**:

| handler 등록 위치 | extra 인자 수신 | tool.execute 의 signal 전달 |
|---|---|---|
| `tools-stdio-server.ts:16` (ListToolsRequestSchema) | 무시 | n/a (list-only) |
| `tools-stdio-server.ts:17` (CallToolRequestSchema) | **무시** | **전달 안 함 (plugin-tools-handlers.ts:54)** |
| `channel-server.ts:50` (ClaudePermissionRequestSchema) | params 만 사용 | n/a (notification handler) |
| `channel-tools.ts:registerChannelMcpTools` | (server.tool 등록 — McpServer 의 high-level API) | 별도 검증 필요 |

→ MCP host cancellation (`notifications/cancelled`) 가 plugin-tools server 의 in-flight tool 호출에 도달하지 못함. 이는 lifecycle 계약 위반 (host 의 graceful cancel 정책 vs server 의 silent ignore).

**Caller wrapper finally 보장 여부**:

| caller | 위치 | finally drain |
|---|---|---|
| `serveOpenClawChannelMcp` | channel-server.ts:73-110 | **있음** (L106-109 finally shutdown + await closed) |
| `servePluginToolsMcp` | plugin-tools-serve.ts:67-80 | **없음** (단일 await 후 return) |
| `serveOpenClawToolsMcp` | openclaw-tools-serve.ts:27-30 | **없음** (단일 await 후 return) |
| `mcp-cli.ts:46-71` (channel CLI action) | cli/mcp-cli.ts | try/catch 로 stderr.exit. shutdown drain 은 `serveOpenClawChannelMcp` 내부의 finally 에 위임 |

**R-3 Grep 핵심 결과**:

```
rg -n "await\s+server\.close|close\(\)\.then" src/mcp/
  → channel-server.ts:68 (`await bridge.close(); await server.close()`)
  → channel-server.ts:93 (`close().then(resolveClosed, resolveClosed)`)
  → tools-stdio-server.ts:39 (`void server.close()`)   — 비대칭

rg -n "AbortSignal|abortSignal|signal:" src/mcp/
  → 0 매치 (signal 인자 미사용)

rg -n "tool\.execute\(.*signal" src/mcp/
  → 0 매치

rg -n "shutdown|in.flight|abort" src/mcp/tools-stdio-server.ts src/mcp/plugin-tools-serve.ts src/mcp/openclaw-tools-serve.ts src/mcp/plugin-tools-handlers.ts
  → tools-stdio-server.ts:30-45 의 shutdown 함수 + listener detach 만. in-flight drain / abort 처리 0 매치.

rg -n "finally" src/mcp/plugin-tools-serve.ts src/mcp/openclaw-tools-serve.ts
  → 0 매치 (caller 측 cleanup 부재)

rg -n "ensureStandalonePluginToolRegistryLoaded|resolvePluginTools" src/mcp/
  → plugin-tools-serve.ts:23, 45, 49 (startup-fixed registry load. runtime growth 없음)
```

**R-7 production hot-path 검증**:

- FIND-001: `connectToolsMcpServerToStdio` 는 `servePluginToolsMcp` 와 `serveOpenClawToolsMcp` 두 standalone entry 의 유일한 stdio wiring. SIGTERM/SIGINT/stdin close 는 host (Claude Code SDK, ACPX bridge, Codex) restart 의 정규 신호 — primary hot-path.
- FIND-002: `setRequestHandler(CallToolRequestSchema, ...)` 는 plugin-tools / openclaw-tools server 의 모든 callTool 요청 진입 지점 — primary hot-path. cancellation 은 host UX 정규 경로.

**R-8 upstream 최신성**:

- HEAD `6a41a54212` (2026-05-14 ff 완료).
- 6 주 file 영역 commit 분석:
  - `tools-stdio-server.ts`: `61ab68f5c9 refactor: share MCP tools stdio server` (본 패턴 도입 commit, 후속 fix 없음).
  - `plugin-tools-handlers.ts`: 5 건 (policy / 결과 serialization / 보안 / 테스트) — signal/cancel 축 0.
  - `mcp-stdio-transport.ts`: `e1a7c5b860 fix EPIPE on stdin writes (#75602)` — send() 의 write callback 만 다룸. start() 의 listener cleanup 미터치 (priors §3 일치).
  - `channel-bridge.ts` / `channel-server.ts` / `channel-shared.ts` / `channel-tools.ts`: 본 셀 hints 1 의 close 비대칭 후보는 mcp-memory 셀 PR #71648 가 OPEN 인 상태로 axis 분리 유지.

**CAL-001 회귀 방지**: 두 FIND 모두 unconditional cleanup 경로 부재를 명시. R-3 grep 결과로 대안 경로 (await close, signal listener) 가 코드에 존재하지 않음을 확인.

**CAL-003 회귀 방지**: production hot-path (실제 SDK Server + 실제 stdio transport + 실제 tool.execute) 가 두 FIND 의 발현 조건. 기존 test 들은 SDK/transport 를 mock 으로 우회 (plugin-tools-serve.test.ts:43-45) — production 동작과 다른 branch. 재현 테스트는 실 SDK 인스턴스 필수.

**CAL-004 회귀 방지**: PR #71648 (mcp-memory v2, close-time Map clear) 와 axis 분리 — 본 FIND 는 (1) tools-stdio-server 의 shutdown drain, (2) plugin-tools-handlers 의 cancel 전파. 두 file 모두 PR #71648 의 patch 범위 (channel-bridge.ts) 외.

**CAL-008 회귀 방지**: `gh pr list --repo openclaw/openclaw --state open --search "plugin-tools-serve OR tools-stdio-server in:title,body"` + `--search "callTool OR AbortSignal mcp plugin"` 양쪽 검색. bundle-mcp (agents 도메인) PR #73536 / #78160 은 client-side timeout 축 (본 셀 외). 본 셀 file 영역 OPEN PR 없음 확인.

**자체 한계**:

- SDK `Server.close()` 의 정확한 in-flight handler 처리 동작 (abort trigger, resolve buffer 여부) 은 SDK 소스 미확인. protocol.d.ts type 정의에서 `extra.signal: AbortSignal` 제공 사실만 확인.
- AnyAgentTool 구현체들 (cron-tool, browser, memory-lancedb 등, allowed_paths 외) 이 signal 을 실제로 honor 하는지 미확인. 만약 다수 tool 이 signal listener 를 안 등록하면 본 FIND-002 의 발현 강도가 약화.
- production cancellation 빈도 metrics 부재 — host (Claude Code SDK) 의 사용자 cancel 빈도, child restart 빈도 정량 자료 없음.
- 폐기한 후보 (bridge.start partial init, stdio transport spawn-error race) 의 finally cleanup 이 모든 caller 에서 보장된다는 가정은 mcp-cli.ts (production CLI caller) 만 확인. test/programmatic caller 는 try/finally 없이 호출할 수 있어 lifecycle 측면에서 위험은 잔존 — 그러나 그것은 caller 측 책임이라 본 셀 file 의 결함이 아님.

**다음 페르소나를 위한 힌트**:

- **mcp-concurrency 셀 후보** (재확인): channel-bridge.ts:127-148 의 `onEvent`/`onClose` 콜백이 `void this.handleGatewayEvent(event)` 패턴으로 async-fire. handleGatewayEvent 내부의 trackApproval/resolveTrackedApproval (L398-420) 가 같은 id 에 대해 set→delete 순서 race 시 leak.
- **mcp-error-boundary 셀 후보** (재확인): `mcp-stdio-transport.ts:78` `child.stdin?.on('error', err => this.onerror?.(error))` 만 호출. child kill 이나 readBuffer.clear 안 함 → onerror 핸들러가 등록 안 됐으면 silent. 본 셀에서 error-boundary 축으로 재방문 시 cluster 가능.
- **agents 도메인 (pi-bundle-mcp-runtime)**: BundleMcpSession 의 disposeSession 이 in-flight tool 호출에 signal 전달하는지 검증. 본 셀 FIND-002 의 server-side 대응으로 client-side 도 동일 gap 가능성.

#### 클러스터 관찰 — clusterer (2026-05-14)

**CAND-026 (epic, FIND-mcp-lifecycle-001 + FIND-mcp-lifecycle-002)** 로 묶음.

**왜 같은 도메인 다른 axis 인가 (CAND-025 와의 분리)**:

| 축 | CAND-025 (mcp-memory) | CAND-026 (mcp-lifecycle) |
|---|---|---|
| 결함 surface | channel-bridge.ts 의 두 pending Map (메모리 무한 성장) | tools-stdio-server.ts + plugin-tools-handlers.ts 의 in-flight callTool hook 부재 |
| 결함 카테고리 | A. 무제한 자료구조 / E. 캐시 TTL 부재 | B. Dispose / Unload 경로 누락 / F. in-flight tool call 처리 |
| 발현 trigger | 외부 (Claude SDK / gateway WS) 미응답 누적 | host SIGTERM / `notifications/cancelled` / stdin close |
| 시간 축 | hours-units long-running (단조 증가) | 매 child restart 또는 매 cancel (event-driven) |
| fix surface 겹침 | 없음 (다른 file) | 없음 (다른 file) |
| 양방향 cross_refs | CAND-026 추가 (frontmatter) | CAND-025 추가 (rationale 본문) |

→ 두 CAND 는 같은 mcp 도메인의 직교 두 축. 한 PR 으로 합치면 안 되고, 별 PR 두 개로 진행.

**일반화된 anti-pattern (MCP server in-flight tool call lifecycle gap)**:

`connectToolsMcpServerToStdio` (tools-stdio-server.ts:24-48) 는 plugin-tools 와 openclaw-tools 두 standalone MCP 프로세스의 유일한 wiring 함수다. 이 한 함수가 host 와의 lifecycle 계약 ("종료/취소 신호 시 in-flight tool call 을 정리하고 응답 또는 abort 한다") 의 양 끝점을 동시에 놓침:

1. **outbound 측 (shutdown)**: 종료 신호 도착 시 in-flight handler.callTool 의 응답을 host 로 송신할 기회 부재. `void server.close()` 가 promise 를 버려 transport 가 닫힌 뒤에도 process 가 잠시 살아있어 응답이 도달할 수도 있고 못할 수도 있는 race. drain 보장 없음.

2. **inbound 측 (cancel)**: 취소 신호 도착 시 in-flight tool.execute 에 abort 를 전달할 channel 부재. SDK 가 정상적으로 `extra.signal` 을 abort 해도 wrapper 가 extra 를 받지 않아 손실.

같은 함수 내 두 hook 의 누락이 같은 lifecycle 계약 위반에 귀속 → epic.

**channel-server.ts 와의 비대칭이 핵심 단서**: `serveOpenClawChannelMcp` (channel-server.ts:73-110) 는 같은 도메인의 다른 server entry 이면서 shutdown drain (L93 then + L102-109 finally `await closed`) 을 갖춤. tools-stdio-server.ts 는 channel-server.ts 패턴을 따르지 않음 — `61ab68f5c9 refactor: share MCP tools stdio server` 가 통합 시 drain 패턴을 가져오지 않은 한 commit. fix 도 channel-server.ts 패턴 이식이 자연.

**signal 전파 결함이 wiring (extra 무시) + API (callTool signature) 두 layer 에 분포**: tools-stdio-server.ts:17 에서 extra 를 받아도 plugin-tools-handlers.ts:45 의 callTool 이 signal 파라미터를 안 받으면 전달 불가. 두 file 의 변경이 필연적으로 짝 → epic 자연.

**메인테이너 수용 가능성 (CLAUDE.md 인용)**: maintainer 우선순위가 "memory, plugin loading, cron, reliability". 본 CAND 는 **reliability** 축 (host 종료/취소 정규 경로에서 응답 누락 / 자원 graceful release 실패) + **plugin loading 의 사후 lifecycle** 측면. CONTRIBUTING.md feature-freeze 와 무관 (bug fix).

**다음 단계 (gatekeeper / publisher 입력)**:

- one-thing-per-PR 검토 통과 — 3 hunk / 2 files / XS-S.
- pre-pr cross-review 시 fix surface 의 channel-server.ts 패턴 이식 vs in-flight Set 추적 vs caller finally drain 세 옵션 중 선택지 정리.
- 회귀 테스트 인프라가 channel-server.shutdown-unhandled-rejection.test.ts 와 유사 (실 SDK Server + 실 stdio transport) → 본 CAND 의 회귀 테스트는 그 패턴 follow.
