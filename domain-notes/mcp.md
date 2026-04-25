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
