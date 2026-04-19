# gateway 도메인 감시 기록

이 파일은 `gateway-*` 셀들에서 공통으로 쓰이는 도메인 노트다. 각 페르소나는 자기 section 에만 append 한다. conflict 피하려면 section header 를 명확히 분리.

## 도메인 개요

`src/gateway/**` — openclaw 의 중앙 WebSocket 게이트웨이. operator UI / node (mobile/desktop) / webchat / cli client 가 붙어 RPC (connect + method) 를 교환한다.

주요 경계:
- **Wire protocol**: `src/gateway/protocol/schema/*.ts` (zod/typebox 스키마, `protocol/schema.ts` 가 barrel).
- **WS connection lifecycle**: `src/gateway/server/ws-connection.ts` (connection 수락, close/error 이벤트, preauth budget), `src/gateway/server/ws-connection/message-handler.ts` (handshake + RPC dispatch).
- **RPC dispatch**: `src/gateway/server-methods.ts` 의 `handleGatewayRequest` → `coreGatewayHandlers[method]` (server-methods/*.ts 로 분기).
- **Auth**: `src/gateway/auth.ts`, `auth-rate-limit.ts`, `auth-token-resolution.ts`, `auth-surface-resolution.ts`, `connection-auth.ts`, `server/ws-connection/auth-context.ts`.
- **Node event bus**: `src/gateway/server-node-events.ts` (node → gateway event dispatch), `server-runtime-services.ts` (startup/shutdown orchestration).

---

## 실행 이력

### error-boundary-auditor (2026-04-19, 셀 `gateway-error-boundary`)

HEAD: `8879ed153d` (upstream/main fresh at audit time, CAL-007 staleness gate 통과).

#### R-3 Grep 결과

```
rg -n 'try\s*\{' src/gateway/           → 80+ hits (production+test)
rg -n '\.catch\(' src/gateway/          → 60+ hits
rg -n 'throw new' src/gateway/          → 200+ hits (main)
rg -n 'JSON\.parse|z\.parse|safeParse'  → 90+ hits
rg -n 'void\s+[a-zA-Z_]+\s*\('          → 45+ hits — all paired with .catch or side-effect-only
rg -n 'process\.on\('                   → 4 hits (2 test, 2 test-test)
```

#### 적용 카테고리 (A~E)

- [x] A. unhandledRejection/uncaughtException chain — **skipped**: gateway scope 에는 process.on 없음. `src/infra/unhandled-rejections.ts` (infra-process-error-boundary 셀) 가 담당.
- [x] B. Floating promise — 확인. `void X(...).catch(...)` 패턴 45건 모두 `.catch` 있음. CAL-007 의 upstream fix 반영된 상태 확인 (`bundled-capability-runtime.ts:301` 는 여기 scope 아님).
- [x] C. JSON.parse 미보호 — server-node-events.ts 의 silent return 경로 2건 (FIND-001 참조). message-handler.ts L325 는 outer try 로 방어. 나머지는 test 또는 config 로딩 (doctor 경로에서 try 감싸져 있음).
- [x] D. AbortController/AbortSignal — chat abort 경로 (chat-abort.ts) 존재. 기존 테스트로 커버됨. error-boundary 관점 gap 없음.
- [x] E. fs/network 동기 호출 — handshake hot-path 에 sync fs 호출 없음 확인.

#### R-5 Execution condition 주요 분류표

| 경로 | 조건 | 위치 | 비고 |
|---|---|---|---|
| `send` try/catch swallow | unconditional | ws-connection.ts:236-242 | 모든 응답 전송 경로 — close 된 socket 에도 조용히 무시. 의도적 graceful. |
| `close` try/catch swallow | unconditional | ws-connection.ts:251-266 | socket.close throw 무시. 의도적. |
| message-handler outer try | unconditional (entry) | message-handler.ts:324-1465 | 모든 in-flight 처리 커버 |
| message-handler L1459 catch | conditional-edge | post-handshake sync throw | FIND-002 대상 |
| RPC IIFE `.catch` → respond(UNAVAILABLE) | unconditional | message-handler.ts:1455-1458 | async dispatch reject 을 모두 UNAVAILABLE 로 변환 — plugin hook throw 방어 |
| `respondUnavailableOnThrow` (nodes.ts:1118) | unconditional | server-methods/nodes.ts | node.event RPC 내부 handleNodeEvent throw 방어 |
| hookRunner `.catch` (server-startup-post-attach.ts) | unconditional | L221, L226, L353 | runGatewayStart/runSessionStart/runSessionEnd 실패 로깅 후 계속 |
| JSON.parse try/catch silent return (server-node-events.ts) | conditional-edge | L253-256, L271-274, L404-408 | FIND-001 대상 — observability gap |
| sendFrame await throw → close | conditional-edge | message-handler.ts:1317-1323 | hello 전송 실패 시 connection close |
| bootstrap token post-connect try | conditional-edge | message-handler.ts:1325-1357 | revoke/redeem 실패해도 연결 유지 (의도적 graceful) |

#### 핵심 발견

1. **plugin throw → gateway RPC 응답 경로**: handler 내부에서 throw → `handleGatewayRequest` 가 await propagate → IIFE `.catch` (L1455) 가 `respond(false, UNAVAILABLE, err)`. **unconditional 방어 있음**. plugin throw 가 process crash 로 번지지 않는다 (gateway scope). 단, err 메시지가 그대로 wire 로 나가 information disclosure 가능성은 security 관심사.

2. **malformed protocol message**: outer try (L324) 가 JSON.parse 를 감싸므로 parse 실패는 process crash 안 냄. pre-handshake 면 close, post-handshake 면 log-only (FIND-002). safeParse/ajv validateRequestFrame 은 typed validation 으로 추가 방어.

3. **websocket drop 시 in-flight RPC**: 
   - pending handler Promise 는 reject 되지 않음 (close 이벤트가 handler 에 전파되지 않음).
   - 대신 handler 완료 후 `respond(send(...))` 가 closed socket 에 호출 → `send` swallow → 조용히 drop.
   - 즉 **stall 은 아니다** (Promise 는 resolve 됨). 단, 부작용 (파일 쓰기, 외부 호출) 은 그대로 실행됨. 이건 error-boundary 가 아니라 cancellation gap (lifecycle-auditor 범위).

4. **auth handler throw → 다른 connection 영향**: auth 경로는 모두 per-connection closure 내부. assertGatewayAuthConfigured 는 startup 시 한 번만 throw (misconfig). resolveConnectAuthDecision 등 runtime auth 경로는 Result 스타일. **cross-connection 영향 없음**. unconditional 방어.

#### 산출물

- FIND-gateway-error-boundary-001 (P3, silent drop of agent.request payload parse fail, observability gap)
- FIND-gateway-error-boundary-002 (P3, post-handshake outer catch close 부재, 이론적 hang)

#### 탐색했으나 FIND 포기 (unconditional 방어 존재)

- `send` JSON.stringify throw silent swallow: 이론상 silent response drop 이지만 production payload 는 schema 검증됨 + logWs("out", ...) 로 응답 기록 → 완전 silent 아님.
- `void (async () => handleGatewayRequest(...))().catch(...)` 의 catch 가 respond 를 호출하지만 handler 가 이미 respond(true) 한 뒤 throw 하면 같은 id 로 2개 응답 가능 — 그러나 `server-methods/agent.ts:279-281` 주석 "Send a second res frame (same id) so TS clients with expectFinal can wait" 로 **의도된 동작** 으로 문서화됨.
- `server-startup-post-attach.ts` 의 3개 hook runner catch 는 모두 log-only + 계속 — 의도적 graceful degradation.

#### 주의사항 (다른 페르소나용)

- `server-methods/agent.ts:279-281` "second res frame with same id" 가 규약이다. 이걸 "duplicate response" 로 FIND 만들지 말 것 — false positive.
- `void X.catch(() => {})` 패턴은 이 도메인에서 매우 흔함 (best-effort side-effect). `.catch` 가 `() => {}` 여도 대부분 의도적 — 단, **연속된 상태 업데이트 체인** 에 걸려 있으면 lifecycle-auditor 와 cross-review 필요.
- `server-node-events.ts` 의 silent return catch 3개는 정책적 선택 (voice best-effort). 전체를 fix 대상으로 보지 말고 agent.request 처럼 user-initiated 경로만 선별.

---

### memory-leak-hunter (2026-04-19, 셀 `gateway-memory`)

HEAD: `8879ed153d` (upstream/main fresh, 0 commits behind — CAL-007 staleness gate 통과).

**스코프**: `src/gateway/**` (agents-registry, infra 셀 제외).

#### R-3 Grep 전역 인벤토리

```
rg -n 'new (Map|Set|WeakMap|WeakSet)\b' src/gateway/ --exclude='*.test.ts'
  → 225 matches across ~70 production files.
  → 모듈-레벨 선언 ~40 건. 중 정적 리터럴 Set/Map (enum 상수) 제외하면 22개 동적 자료구조.

rg -n 'setInterval\(|setTimeout\(' src/gateway/  (production only)
  → setInterval 선언 ~15 건. 전부 server-close.ts / server-maintenance.ts / channel-health
    monitor / auth-rate-limit / sessions-history-http SSE heartbeat 이 각각 clearInterval 로 쌍을
    이룸.
  → setTimeout 다수, 대부분 closure-scoped 일회성 timer.
```

#### 인벤토리 분류표 (핵심 22개 동적 자료구조)

| # | 자료구조 | 위치 | 키 도메인 | cap/TTL/cleanup | 판정 |
|---|---|---|---|---|---|
| 1 | `controlPlaneBuckets` | control-plane-rate-limit.ts:14 | subject(ip) | per-op delete + clear + oldest evict | OK |
| 2 | `transformCache` | hooks-mapping.ts:84 | modulePath::export | 없음 (config-bounded) | OK (config domain finite) |
| 3 | `responseSessionMap` | openresponses-http.ts:89 | responseId | MAX_RESPONSE_SESSION_ENTRIES + TTL prune | OK |
| 4 | `stateByNodeId` | node-pending-work.ts:46 | nodeId | pruneStateIfEmpty on drain/ack | OK (paired device bounded) |
| 5 | `pendingAttempts` | rate-limit-attempt-serialization.ts:3 | scope:ip | finally delete if tail | OK (self-cleaning chain) |
| 6 | `resolvedSessionKeyByRunId` | server-session-key.ts:16 | runId | 256 cap + oldest-first FIFO evict | OK |
| 7 | `TRANSCRIPT_SESSION_KEY_CACHE` | session-transcript-key.ts:15 | transcript path | MAX=256 + FIFO + on-mismatch delete | OK |
| 8 | `sessionTitleFieldsCache` | session-utils.fs.ts:28 | filePath+flag | MAX=5000 + FIFO + mtime invalidate | OK |
| 9 | `recentVoiceTranscripts` | server-node-events.ts:45 | sessionKey | MAX=200 + TTL filter + FIFO | OK |
| 10 | `recentExecFinishedRuns` | server-node-events.ts:46 | sessionKey::runId | MAX=2000 + TTL filter + FIFO | OK |
| 11 | `agentRunCache` | server-methods/agent-job.ts:11 | runId | pruneAgentRunCache TTL=10min on record | OK |
| 12 | `agentRunStarts` | server-methods/agent-job.ts:12 | runId | **없음** (lifecycle end/error event 의존) | **FIND 후보** |
| 13 | `pendingAgentRunErrors` | server-methods/agent-job.ts:13 | runId | 15s timer body unconditional delete + clearPendingAgentRunError | OK (CAL-001 대조 — 본 케이스는 timer body 첫 줄 무조건 delete) |
| 14 | `AGENT_WAITERS_BY_RUN_ID` | server-methods/agent-wait-dedupe.ts:10 | runId | disposer 에서 remove; empty Set 삭제 | OK |
| 15 | `nodeWakeById` | server-methods/nodes.ts:72 | nodeId | clearNodeWakeState on WS disconnect | **FIND 후보** (set BEFORE registration check) |
| 16 | `nodeWakeNudgeById` | server-methods/nodes.ts:73 | nodeId | 동 #15 (clearNodeWakeState 공유) | 동 #15 연관 |
| 17 | `pendingNodeActionsById` | server-methods/nodes.ts:102 | nodeId | TTL=10min + MAX=64/node + prune/ack delete | OK |
| 18 | `costUsageCache` | server-methods/usage.ts:65 | startMs-endMs | **cap 없음, prune 없음, TTL on-read 만** | **FIND 후보** (stale entries linger) |
| 19 | `approvalDeliveriesById` | exec-approval-ios-push.ts:259 | approval id | handleResolved/Expired unconditional delete | OK (terminal event 커버) |
| 20 | `pendingDeliveryStateById` | exec-approval-ios-push.ts:260 | approval id | 동 #19 | OK |
| 21 | `ExecApprovalManager.pending` | exec-approval-manager.ts:41 | record id | setTimeout grace-delete unconditional | OK |
| 22 | `McpLoopbackToolCache.#entries` | mcp-http.runtime.ts:26 | sessionKey|provider|account|owner | TTL prune on every resolve | OK |

**기타 확인된 모듈-레벨 Set/Map (정적 상수 — skip)**: `SUPPORTED_OFFLOAD_MIMES`,
`ROOT_MOUNTED_GATEWAY_PROBE_PATHS`, `STATIC_ASSET_EXTENSIONS`, `BLOCKED_PATH_KEYS`,
`KNOWN_WEAK_GATEWAY_TOKENS/PASSWORDS`, `NATIVE_TOOL_EXCLUDE`, `NODE_ROLE_METHODS`,
`METHOD_SCOPE_BY_NAME`, `WRAPPER_PROVIDERS`, `WS_META_SKIP_KEYS`, `CHAT_ERROR_KINDS`,
`GATEWAY_PROBE_STATUS_BY_PATH`, `CONTROL_PLANE_WRITE_METHODS`, `GATEWAY_CLIENT_ID_SET`,
`GATEWAY_CLIENT_MODE_SET`, `CONNECT_RECOVERY_NEXT_STEP_VALUES`, `ALLOWED_FILE_NAMES`,
`CHANNEL_AGNOSTIC_SESSION_SCOPES`, `CHANNEL_SCOPED_SESSION_SHAPES`, `WEB_LOGIN_METHODS`,
`MEMORY_TOOL_NAMES`, `BOOTSTRAP_FILE_NAMES` — 전부 상수 enum 기반, 성장하지 않음.

**기타 특수**:
- `ChannelManager` 내부 `channelStores`/`restartAttempts`/`manuallyStopped` (server-channels.ts:187-191):
  key = `channelId:accountId`, config-bounded.
- `nodePresenceTimers` (server-node-session-runtime.ts:14): **dead code** — 선언·전파·shutdown
  clear 만, 프로덕션 `.set` 호출 부재 (grep 전체 확인).
- WeakMap 2건 (`pluginGatewayAuthBypassPathsCache` server-http.ts:185, `normalizedPrefixesCache`
  security-path.ts:129): 참조 GC 시 자동 회수.
- `operatorIdentityPathByPrefix` (server.auth.control-ui.suite.ts:34): 테스트 suite 파일
  (.suite.ts), 프로덕션 런타임 미포함.

#### 적용 카테고리 (A~E)

- [x] A. 무제한 자료구조 성장 — **FIND 3건**.
- [x] B. EventEmitter/리스너 누수 — `onAgentEvent` 패턴은 disposer 반환.
  `AGENT_WAITERS_BY_RUN_ID`, `node-subscription-manager` 모두 subscribe/unsubscribe 짝. 문제 없음.
- [x] C. 강한 참조 체인 — `approvalDeliveriesById` 의 PendingEntry 는 record + resolve/reject
  closure 보유, grace-delete 로 회수. 의도적 WeakMap 사용처 (security-path, http plugin cache) 적절.
- [ ] D. 핸들/리소스 누수 — skipped. fs API 는 동기 (statSync, readFileSync) 위주. HTTP
  abortController 는 rate-limit 경로에서 확인.
- [x] E. 캐시 TTL 부재 — `costUsageCache` 가 대표 사례 (TTL on-read 만, prune/cap 부재).

#### R-5 Primary-path Inversion 분석

**FIND-001 (`costUsageCache`)**:
- 엔트리가 "교체" 되는 경우 = 동일 cacheKey 로 새 요청. daily sliding window 는 매일 새 key 로
  생성됨. 이전 key 의 엔트리는 **영속**.
- 유일 삭제 경로: `__test.costUsageCache.clear()` (테스트 전용).
- 분류: **no-cleanup**.

**FIND-002 (`nodeWakeById` / `nodeWakeNudgeById`)**:
- set 경로: `maybeWakeNodeWithApns` 진입 시 L312-313 unconditional — `loadApnsRegistration` 호출
  이전에 set.
- 삭제 경로: `clearNodeWakeState` ← WS `close` 이벤트 중 `role === "node"` 분기에서
  `context.nodeRegistry.unregister(connId)` 가 truthy 일 때만 실행.
- 누출 조건: nodeId 가 한 번도 WS 로 연결/해제 되지 않는 경우. 특히
  `path: "no-registration"` 분기 (L334-336) 에서도 L313 set 은 이미 수행됨.
- 분류: `conditional-event-dependent` with precondition that **may never hold**.

**FIND-003 (`agentRunStarts`)**:
- set: lifecycle "start" event.
- delete: lifecycle "end" 또는 "error" event (`unconditional within event branch`).
- 정상 경로: `handleAgentEnd` 가 try/finally 로 `emitLifecycleTerminalOnce` — robust.
- 실패 경로: emitAgentEvent 가 아예 호출되지 않는 경우 (runner crash 이전 start 이후 end 전 throw
  uncaught, agent-events pipeline 중단). low frequency 지만 TTL/cap safety belt 부재.
- CAL-001 대조: `pendingAgentRunErrors` (동일 파일, 15s timer body 첫 줄 무조건 delete) 와 달리
  `agentRunStarts` 는 timer 자체가 없어 이벤트 실패 시 fallback 부재.

#### 스킵 사유 (false-positive 방지)

- `pendingAgentRunErrors` (agent-job.ts:13): `schedulePendingAgentRunError` 의 setTimeout 콜백
  L57-62 는 `if (!pending) return;` 다음 **즉시 `pendingAgentRunErrors.delete(snapshot.runId)`**
  (L61 unconditional). CAL-001 반례로 삼아 재확인 — 본 케이스는 clean.
- `restartAttempts`/`manuallyStopped`/`channelStores` (server-channels.ts): 키 domain 이
  config 으로 bounded. 프로덕션 수명 내 성장 없음.
- `stateByNodeId` (node-pending-work.ts): 각 엔트리 O(2 items). paired device 도메인.
  pruneStateIfEmpty 있음. 엄밀히는 enqueue-only node 가 상태 유지하나 per-state 크기 trivial,
  P4 수준이라 제외.
- `transformCache` (hooks-mapping.ts): 키 = config `hooks.transforms` 경로. user config 로 bounded.
- `wsInflightCompact` / `wsInflightSince` (ws-log.ts): isVerbose + style=compact/full 경로에서만
  populate. `wsInflightOptimized` 는 non-verbose 경로에서 populate 되나 size > 2000 시
  `.clear()` 로 aggressive flush (L331). 누수 아님.
- `nodePresenceTimers`: **dead code**, set 호출 없음.

#### 산출물

- FIND-gateway-memory-001 (P3): `costUsageCache` — stale 엔트리 무한 누적 (TTL on-read only).
- FIND-gateway-memory-002 (P3): `nodeWakeById`/`nodeWakeNudgeById` — `maybeWakeNodeWithApns` 가
  registration 체크 전에 set; 미등록 nodeId 대상 반복 호출 시 누수.
- FIND-gateway-memory-003 (P3): `agentRunStarts` — lifecycle end/error event 실패 경로에서
  safety belt 부재 (TTL/cap 없음).

#### Self-critique (미확인 영역)

- 프로덕션 metrics: 맵 크기 시계열 telemetry 없음. 장기 가동 서버의 실제 누적 속도 미측정.
- `hooks-mapping.ts` 의 config reload 경로에서 기존 `transformCache` 엔트리가 유지되는지
  (`config-reload.ts` 와의 상호작용) 미조사.
- `exec-approval-ios-push.ts` `handleRequested` 내부 `plan` resolution 이 throw 할 때 외부
  `.catch((err) => ..., return false)` 가 보호하지만 `pendingDeliveryStateById` 엔트리 cleanup
  skip 시나리오 존재. 단독 FIND 로 격리하기엔 증거 약함 (drift candidate).
- `mcp-http.runtime.ts` 의 cacheKey 에 sessionKey 포함 → 세션 많은 워크스페이스에서 TTL=30s
  도달 전 pile-up 가능. 다만 매 resolve 시 TTL prune → self-limiting.

---

## 다음 페르소나를 위한 힌트 (memory 이후)

### concurrency-auditor

- `ExecApprovalManager.pending` 의 register/resolve race: L60-72 existing check + L87 timer set 은
  동기 블록이나 resolve() 의 grace setTimeout(15s) 과 새 register() 사이 동일 id 재사용 시
  L71 throw. 동작은 정상이나 caller 영향 검토 가치.
- `nodeWakeById` 의 `state.inFlight` promise 공유 (L325) — concurrent wake 는 하나로 통합되나
  `clearNodeWakeState` 가 in-flight 중 호출되면 state 객체 변경 전파 여부.

### cron-reliability-auditor

- `server-maintenance.ts` 의 interval 들은 종료시 clearInterval 되나, config-reload 기반
  runtime reload 경로에서 중복 타이머 생성 여부 미검증.

### clusterer (2026-04-19)

- CAND-014 (single): FIND-gateway-memory-001 (costUsageCache) — R-5 분류
  "no-cleanup" (write-path cap/prune 부재, TTL on-read only).
- CAND-015 (single): FIND-gateway-memory-002 (nodeWakeById) — R-5 분류
  "conditional-event-dependent with precondition that may never hold"
  (no-registration 경로 cleanup 누락).
- CAND-016 (single): FIND-gateway-memory-003 (agentRunStarts) — R-5 분류
  "conditional-event-dependent with low failure rate + no TTL safety belt"
  (같은 파일 pendingAgentRunErrors 의 CAL-001 반례 패턴 부재).
- CAND-017 (single): FIND-gateway-error-boundary-001 — silent JSON.parse catch
  in agent.request (observability gap).
- CAND-018 (single): FIND-gateway-error-boundary-002 — post-handshake outer
  catch log-only (potential hang, hot-path 증거 부재).

**Epic 지양 근거**: 세 memory FIND 는 "Map eviction 부재" 라는 상위 관찰 테마를
공유하지만 R-5 execution-condition 분류가 셋 모두 독립이고 파일·자료구조·fix
축(write-path cap 추가 vs. cleanup precondition 재설계 vs. fallback timer 추가) 이
서로 다르다. CONTRIBUTING.md 의 "one thing per PR" 관점에서도 개별 PR 이 적합.
두 error-boundary FIND 도 다른 파일/다른 symptom/다른 fix 축으로 single 유지.

## 기술 빚 / 미결

- telemetry: production map 크기 시계열 없음. 장기 운영 (주/월 단위) 프로세스 관측 필요.
- `nodePresenceTimers` dead code 제거 또는 용도 복구 필요.
- `costUsageCache` 에 TTL prune + size cap 도입 필요 (해결책은 SOL 단계에서).

