# auto-reply 도메인 노트

openclaw 의 `src/auto-reply/` 는 inbound 채널 메시지에 대한 자동 응답 파이프라인. 
큐잉·debounce·collect batch·command detection·chunking·routing 을 담당.

## 지도

- `src/auto-reply/reply/queue/` — followup queue 상태·enqueue·drain·cleanup.
  - `state.ts` — `FOLLOWUP_QUEUES` 전역 Map, `FollowupQueueState` 정의, 
    `getFollowupQueue`/`clearFollowupQueue`/`refreshQueuedFollowupSession`.
  - `enqueue.ts` — `enqueueFollowupRun` (dedupe + push + kickIfIdle), 
    `getFollowupQueueDepth`.
  - `drain.ts` — `scheduleFollowupDrain` 의 async IIFE, `FOLLOWUP_RUN_CALLBACKS`, 
    `resolveCrossChannelKey`, `splitCollectItemsByAuthorization`, 
    `resolveOriginRoutingMetadata`.
  - `cleanup.ts` — `clearSessionQueues` (queue + command lane 한꺼번에 정리).
- `src/auto-reply/command-detection.ts` — 순수 동기. `hasControlCommand`, 
  `isControlCommandMessage`, `shouldComputeCommandAuthorized`. race 위험 없음.
- `src/auto-reply/chunk.ts` — 순수 함수 text chunking. mutable state 없음.
- `src/utils/queue-helpers.ts` — `beginQueueDrain`, `drainCollectQueueStep`, 
  `drainNextQueueItem`, `waitForQueueDebounce`, `previewQueueSummaryPrompt`, 
  `hasCrossChannelItems`. 공용 helper.

## 동시성 모델

- JS single-threaded → race 는 `await` / microtask yield boundary 에서만 가능.
- 전역 shared state: `FOLLOWUP_QUEUES` (Map), `FOLLOWUP_RUN_CALLBACKS` (Map), 
  `RECENT_QUEUE_MESSAGE_IDS` (dedupe cache). 외부 lock 없음.
- drain 은 `queue.draining` boolean 을 mutex 처럼 사용. `beginQueueDrain` 이 check-set-
  return 을 동기 수행하여 double-drain 차단.
- `clearFollowupQueue` 는 queue.items 를 **in-place 로 비움** (`length = 0`). drain 이 
  캡처한 array 참조와 동일하므로 mid-await 에서 즉시 반영됨. 그러나 map entry 제거와 
  drain 종료 정합성은 identity check 가 없어 race 발생 가능 
  (→ FIND-auto-reply-concurrency-001).

## 최근 upstream 변경 (3 주 이내)

| 커밋 | 날짜 | 설명 |
|---|---|---|
| 712644f0d9 | 2026-04-18 | fix(queue): preserve pending items during drains. 
  drain.ts:222, subagent-announce-queue.ts:165, system-events.ts:170 의 `splice(0)` → 
  `splice(0, N)` 로 수정. await 중 새로 push 된 items 가 실수로 삭제되는 문제 해결. |
| 43d4be9027 | 이전 | fix(queue): split collect batches by auth context. 
  splitCollectItemsByAuthorization 도입. |
| 622b91d04e | 이전 | fix: queue model switches behind busy runs. |
| 02e07a157d | 이전 | fix(reply): clear idle followup callbacks. |
| a35dcf608e | 이전 | fix(reply): refresh followup drain callbacks. |

## 감사 주목 영역

### 1. drain finally identity race (FIND-auto-reply-concurrency-001)
- `scheduleFollowupDrain` finally (drain.ts:263-271) 의 `FOLLOWUP_QUEUES.delete(key)` + 
  `clearFollowupDrainCallback(key)` 는 자신이 들고 있는 queue 참조가 현재 map entry 와 
  동일한지 검증하지 않음.
- `/stop` + 연속 메시지 시나리오에서 orphan Q2 발생.
- `subagent-announce-queue.ts:62-72` 주석에 동일 패턴 언급 ("Clearing the map alone isn't 
  enough because drain loops capture `queue` by reference").

### 2. cross-channel batch race (FIND-auto-reply-concurrency-002)
- drain.ts:159 `isCrossChannel` 계산과 L185 `items.slice()` 사이 L161 await 에서 
  microtask yield. webhook fan-in 시 cross-channel item 이 batch 에 뭉쳐 wrong channel 
  로 배달 가능.
- `splitCollectItemsByAuthorization` (drain.ts:68-110) 의 auth key 에 channel 없음. 
  `resolveOriginRoutingMetadata` (drain.ts:50-60) 는 각 필드 독립 find 로 chimera routing.

## 감사 범위에서 제외된 경로 (참조)

- `src/agents/subagent-announce-queue.ts` — 동일 `splice(0)` fix 가 적용된 자매 파일. 
  allowed_paths 밖 (agents/ 도메인). identity race 도 동일 존재 가능성.
- `src/infra/system-events.ts` — 같은 fix 적용 대상. allowed_paths 밖.
- `src/gateway/server-methods/sessions.ts` — clearSessionQueues 호출 경로.
- `src/gateway/session-reset-service.ts` — clearSessionQueues 호출 경로.

## 검증된 non-issues (탐색했으나 race 아님)

- `command-detection.ts` 전체 — 순수 동기 함수. shared state 없음.
- `chunk.ts` 전체 — 순수 string transformation. mutable state 없음. 설정 cache 도 없음.
- `drainNextQueueItem` (queue-helpers.ts:147-158) — `items.shift()` 는 `await` 이후 
  동기 실행이며 첫 index 가 처리된 item 과 동일 (enqueue 는 항상 push 뒤로 추가). 
  index drift race 없음.
- `RECENT_QUEUE_MESSAGE_IDS` dedupe (enqueue.ts:13-17) — TTL map. race 는 이론상 
  messageId 중복 확인과 check 사이에 있으나 false negative (같은 메시지 2번 처리) 
  영향은 기존 dedupe 메커니즘의 보조 방어로 흡수.

### clusterer (2026-04-19)

- CAND-012 (single): FIND-auto-reply-concurrency-001 — drain finally identity race.
  같은 셀의 FIND-002 와 파일(drain.ts) 공유하지만 root cause axis 는 "queue
  lifecycle (Map ownership)" 로 독립 → single.
- CAND-013 (single): FIND-auto-reply-concurrency-002 — isCrossChannel stale +
  chimera routing. root cause axis 는 "batch computation staleness +
  grouping/routing data dependency" 로 CAND-012 와 독립. 세 subcause (재계산
  부재 / auth key 채널 누락 / field-wise find) 는 하나의 symptom (cross-channel
  wrong delivery) 으로 수렴하므로 single CAND 에 통합.

## memory 축 (auto-reply-memory cell, 2026-05-14 감사)

memory-leak-hunter 가 `src/auto-reply/**` 전 범위를 5개 카테고리 (무제한 자료구조, 리스너 누적, 강한 참조 체인, 핸들 누수, 캐시 TTL) 로 1회 sweep. **strong leak 후보 0건**. 결과는 negative finding 으로 정리.

### 검증된 non-issue (cap/TTL/cleanup 충분히 작동)

| 자료구조 | 위치 | cleanup 메커니즘 |
|---|---|---|
| `ABORT_MEMORY` | `reply/abort-primitives.ts:49` | cap=2000 + FIFO `pruneAbortMemory` (L93-106). add 마다 prune. **unconditional**. |
| `mentionMatchRegexCompileCache`, `mentionStripRegexCompileCache` | `reply/mentions.ts:35-36` | cap=512 + `cache.clear()` on overflow (`cacheMentionRegexes` L82-85). **unconditional**. |
| `mentionPatternWarningCache` | `reply/mentions.ts:38` | cap=512 + `clear()` on overflow (L65-68). **unconditional**. |
| `silentExactRegexByToken`, `silentTrailingRegexByToken`, `silentLeadingAttachedRegexByToken`, `silentLeadingRegexByToken` | `tokens.ts:6-8, 90` | cap 없으나 token 인자는 `SILENT_REPLY_TOKEN`/`HEARTBEAT_TOKEN`/`ANNOUNCE_SKIP_TOKEN`/`REPLY_SKIP_TOKEN` 4개 상수 (`agents/tools/sessions-send-tokens.ts:6-11`) → **사실상 cap=4**. |
| `foregroundReplyFenceByKey` | `dispatch.ts:39` | `endForegroundReplyFence` 에서 `activeDispatches <= 0` 시 delete (L110-112). `dispatchInboundMessageWithBufferedDispatcher` finally 보장 (L327). **unconditional**. |
| `FOLLOWUP_QUEUES` | `reply/queue/state.ts:29` | drain finally 에서 identity guard 후 delete (`drain.ts:307-310`, CAND-012 fix 반영). `clearSessionQueues` 별도 경로 (`cleanup.ts:67`). |
| `FOLLOWUP_RUN_CALLBACKS` | `reply/queue/drain.ts:22` | drain finally 에서 queue empty + identity match 시 `clearFollowupDrainCallback` (L309). `clearSessionQueues` 직접 호출 (`cleanup.ts:68`). 두 경로 모두 정상 flow 에서 실행. |
| `RECENT_QUEUE_MESSAGE_IDS` | `reply/queue/enqueue.ts:15` | `resolveGlobalDedupeCache` (TTL=5min, max=10k). `infra/dedupe.ts` 의 `createDedupeCache` 가 add 시 prune. **unconditional**. |
| `inboundDedupeCache`, `inboundDedupeInFlight` | `reply/inbound-dedupe.ts:22-29` | TTL=20min, max=5000. `commitInboundDedupe`/`releaseInboundDedupe` 가 finally 에서 호출 (`dispatch-from-config.ts:1709-1711`). |
| `replyRunState.{activeRunsByKey,activeSessionIdsByKey,activeKeysBySessionId,waitKeysBySessionId,waitersByKey}` | `reply/reply-run-registry.ts:91-107` | `complete`/`fail`/`abortByUser` 가 `clearReplyRunState` 호출. `waitForIdle` timeout 시 waiter cleanup. **unconditional**. |
| `activeDispatchers` | `reply/dispatcher-registry.ts:12` | `unregister()` 가 `pending===0` 도달 시 호출 (`reply-dispatcher.ts:281-282, 301`). `withReplyDispatcher` finally 에서 `markComplete` 보장 (`dispatch-dispatcher.ts:23`). |
| `toolMessageByCallId` | `reply/dispatch-acp-delivery.ts:152, 228` | per-turn coordinator local state — coordinator GC 시 함께 GC. |
| `toolLifecycleById` | `reply/acp-projector.ts:210` | `resetTurnState` 가 `done`/`error` 이벤트마다 호출 (L500-503). **unconditional**. |
| `buffers`, `keyChains` | `inbound-debounce.ts:59-60` | cap=2048 (`DEFAULT_MAX_TRACKED_KEYS`) + `canTrackKey` (L183-188) overflow fallback. flush 시 delete. |
| `replyPayloadMetadata` | `reply-payload.ts:75` | **WeakMap** — payload 객체 GC 시 자동. |
| `cachedTextAliasMap`, `cachedDetection` | `commands-registry-normalize.ts:21,23` | commands 가 정적 → 사실상 cache size 1. |
| `cachedNativeCommandSurfaces` | `commands-text-routing.ts:9` | registry version 변경 시 재구축 — bounded by plugin count. |
| `MODEL_CONTEXT_TOKEN_CACHE` | `agents/context-cache.ts:1` | allowed_paths 외 (참고만). |

### setTimeout/setInterval

`auto-reply/` 전체 timer 사용은 전부 짝맞춤 (clearTimeout 또는 idempotent finalize):

- `inbound-debounce.ts:177` — `scheduleFlush` 의 `setTimeout`, `flushBuffer` L155-157 에서 clear.
- `reply/typing.ts:106, 223` — `typingTtlTimer`, `dispatchIdleTimer` 모두 `cleanup` 에서 clear (L75-82).
- `reply/acp-projector.ts:256` — `liveIdleTimer`, `clearLiveIdleTimer` 가 `flush`/`resetTurnState` 에서 호출.
- `reply/pending-tool-task-drain.ts:19` — Promise.race 후 `timeout.clear()` 보장 (L56).
- `reply/block-reply-coalescer.ts:52` — `clearIdleTimer` 가 `flush`/`stop` 에서 호출.
- `reply/block-reply-pipeline.ts:76` — 외부 race timeout, settle 시 clear.
- `reply/conversation-label-generator.ts:70` — `controller.abort()` 의 forced timeout, AbortSignal 정리됨.
- `reply/reply-run-registry.ts:448` — `ReplyRunWaiter.timer`, `notifyReplyRunEnded` 또는 timeout 자체에서 cleanup.
- `setInterval` 사용 **없음** (도메인 전체).

### EventEmitter 리스너

- `reply/reply-run-registry.ts:279-286` — upstream AbortSignal 에 `addEventListener("abort", ..., { once: true })`. **once 옵션 사용** — auto-remove. OK.
- `addEventListener` 직접 사용 외에는 EventEmitter `.on(...)` 의 unconditional 등록 패턴 없음.

### 결론

`auto-reply-memory` 셀에서 강한 누수 후보 없음. P0/P1 후보 0건. 도메인 전반에 cap/TTL/identity-guard 가 명시적으로 배치되어 있고 (특히 CAND-012 fix 이후) 메모리 측면에서 매우 견고. 추가 angle 발생 시 (e.g. orphan queue under crash path) 재방문 후보로만 두고 현 시점에는 published 가능 finding 부재. 셀 상태는 **done (FIND 0)** 으로 마감 권장.

## error-boundary 축 (auto-reply-error-boundary cell, 2026-05-14 감사)

error-boundary-auditor 가 `src/auto-reply/**` 를 5개 카테고리 (handler chain / floating promise / JSON parse 미보호 / abort signal / sync IO) 로 sweep. **FIND 2건** (P3 1, P2 1).

### FIND-auto-reply-error-boundary-001 (P3): drain retry 무한 루프

- `src/auto-reply/reply/queue/drain.ts:298-313` 의 catch + finally 가 `effectiveRunFollowup` throw 시 max-attempts/backoff 없이 self-recurse.
- queue.collect.test.ts:716 'retries collect-mode batches' 는 transient (1회 fail → 다음 성공) 만 cover. deterministic-fail 시 debounceMs=500ms 간격 무한 재시도 → log 폭주 + typing keepalive reset 폭주.
- counter: queue.drain-restart.test.ts / queue.collect.test.ts 등 정상 retry 경로만 검증. max-attempts/dead-letter 변수 0건.
- 가능 trigger: preflight compaction fs ENOENT/EACCES, resolveQueuedReplyExecutionConfig provider plugin throw.

### FIND-auto-reply-error-boundary-002 (P2): queueReplyRunMessage 의 floating Promise

- `src/auto-reply/reply/reply-run-registry.ts:505` `void backend.queueMessage(text)` 에 `.catch` 없음.
- backend 구현 (agents/pi-embedded-runner/run/attempt.ts:2767-2772) 은 `await activeSession.steer(text)` → throw 가능.
- 자매 경로 `agents/pi-embedded-runner/runs.ts:148-154` 는 동일 fire-and-forget 을 `.catch(err => diag.debug(...))` 로 보호 — 본 라인만 비대칭.
- caller chain (prepareEmbeddedPiQueueMessage:192-213) 는 boolean 만 받아 `queued:true, gatewayHealth:"live"` outcome 반환 → silent loss + non-transient 시 process.exit(1) 위험.

### 검증된 non-issue (방어 경로 충분)

| 경로 | 보호 |
|---|---|
| `tokens.ts:57-72` isSilentReplyEnvelopeText | JSON.parse try/catch 외부 envelope 시그니처 사전 검증 |
| `chunk.ts` 전체 | 순수 함수, throw 경로 없음. parseFenceSpans (markdown/fences.ts:9) 도 non-throwing |
| `command-detection.ts` 전체 | 정적 RegExp (hardcoded patterns), escapeRegExp 통과 후 컴파일. throw 경로 없음 |
| `commands-registry-normalize.ts:130` | 패턴 source = escapeRegExp(alias) — 동적 user input 미사용 |
| `directives.ts:22-34` | hardcoded names + escapeRegExp |
| `dispatch-dispatcher.ts:15-25` withReplyDispatcher | run reject → finally settleReplyDispatcher → waitForIdle 는 reply-dispatcher.ts:266-269 의 `.catch` 로 fulfilled-only → finally-shadow 없음 |
| `reply-dispatcher.ts:247-285` sendChain | `.catch` 후 `.finally` 로 fulfilled-only. options.onError 가 throw 시에만 chain 가 한 번 rejected → 다음 .catch 가 흡수 |
| `inbound-debounce.ts:72-83` runFlush | 이중 try/catch (onError 내부 throw 도 swallow) |
| `inbound-debounce.ts:177-181` setTimeout(async flushBuffer) | flushBuffer 의 await buffer.task 는 reservedTask = enqueueKeyTask 의 `next` 가 runFlush 통해 보호됨 |
| `followup-runner.ts:258-363` inner catch | runWithModelFallback 결과만 보호. **outer try (line 220) 는 catch 없음** — preflight/config 단계 throw 시 drain 의 catch 까지 propagate → FIND-001 의 retry storm 진입원 |
| `route-reply.ts:215-267` | send.status check + try/catch → Result<{ok,error}> 반환 |
| `command-auth.ts:197-227` resolveProviderAllowFrom | plugin throw 시 fallback allowFrom + console.warn |
| `dispatch-acp-delivery.ts:256` void Promise.resolve().catch | catch 부착 |
| `block-reply-pipeline.ts:139-187` sendChain | `.catch(err => logVerbose(...))` + timeout error 별도 처리 |
| `typing.ts:128-138` triggerTyping | 자체 try/catch + log |
| `agent-runner-execution.ts:1522-1524` assistantBridgeDelivery | `.catch(() => undefined)` chain healing |
| `session-reset-model.ts:126-134` void import().catch | 명시 catch + 의도된 silent |
| `session.ts:903, 928` void hookRunner.run* .catch(() => {}) | 명시 silent (hook isolation policy) |

### 탐색 방법 (재현용)

```bash
rg -n 'try\s*\{|catch\s*\(|\.catch\(' src/auto-reply
rg -n 'throw ' src/auto-reply
rg -n 'JSON\.parse' src/auto-reply
rg -n 'void [a-zA-Z]' src/auto-reply | grep -v 'void 0' | grep -v '\.catch'
rg -n 'process\.on\([\x27\x22](uncaughtException|unhandledRejection)[\x27\x22]' src/
rg -n 'AbortController|signal\.abort|AbortSignal' src/auto-reply
rg -n 'queueMessage' src/                                    # FIND-002 자매 경로 확인
rg -n 'scheduleFollowupDrain' src/auto-reply                 # FIND-001 진입원/재시도
rg -n 'maxAttempts|backoff|circuit|retry' src/auto-reply/reply/queue
rg -n 'new RegExp' src/auto-reply                            # dynamic regex compile (none in hot path)
```

## 탐색 방법 (재현용)

```bash
rg -n 'async\s+function|async\s+\(' src/auto-reply/reply/queue/
rg -n 'await\s+' src/auto-reply/reply/queue/drain.ts
rg -n 'Promise\.race|Promise\.all' src/auto-reply/
rg -n 'Mutex|Semaphore|AsyncLock|acquire|release' src/auto-reply/
rg -n 'AbortController|AbortSignal|signal\.(abort|addEventListener)' src/auto-reply/
rg -n '(\.splice\(|\.shift\(|\.push\()' src/auto-reply/reply/queue/
rg -n 'FOLLOWUP_QUEUES\.delete|clearFollowupQueue' src/auto-reply/
rg -n 'getFollowupQueueDepth' src/
git log upstream/main --since="3 weeks ago" -p -- src/auto-reply/
git show 712644f0d9
```

## lifecycle 축 (auto-reply-lifecycle cell, 2026-05-14 감사)

plugin-lifecycle-auditor 가 `src/auto-reply/**` 를 5개 카테고리 (load rollback / dispose 누락 / dynamic import isolation / manifest partial state / enable-disable drift) + R-5 cleanup execution condition 분류로 sweep. **FIND 2건** (P2 1, P3 1).

### R-3 대응 경로 매핑 (register/add/set ↔ cleanup)

| 등록자 | 대응 cleanup 경로 | execution condition |
|---|---|---|
| `FOLLOWUP_QUEUES.set` (state.ts:70) | `clearFollowupQueue` (state.ts:74-88) + drain finally identity guard (drain.ts:307-310, CAND-012 fix) | **unconditional** in 정상 drain 종료, **unconditional** via clearSessionQueues |
| `FOLLOWUP_RUN_CALLBACKS.set` (drain.ts:30) | `clearFollowupDrainCallback` (drain.ts:33-35) — drain finally + cleanup.ts:68 | **unconditional** both paths |
| `foregroundReplyFenceByKey.set` (dispatch.ts:88) | `endForegroundReplyFence` (dispatch.ts:104-113) — `activeDispatches <= 0` 시 delete | **unconditional** (finally L327) |
| `activeDispatchers.add` (dispatcher-registry.ts:29) | unregister (dispatcher-registry.ts:32-34) — `pending===0` 시 reply-dispatcher.ts:281-282/301 호출 | **unconditional** via withReplyDispatcher finally → markComplete |
| `buffers.set` / `keyChains.set` (inbound-debounce.ts:261, 89/104) | flushBuffer (L151-163) + keyChains cleanup (L90-95, df27091f5f fix) | **conditional-edge** (timer 미발화 또는 unref 시 drain 안 됨) |
| `buffer.timeout = setTimeout` (inbound-debounce.ts:177) | timer callback → flushBuffer | **conditional-edge** (unref → shutdown 시 발화 안 함, FIND-002) |
| `replyOperation.controller` AbortController (reply-run-registry.ts:238) | clearState → activeRunsByKey/SessionIdsByKey delete (complete/fail/abortByUser/abortForRestart) | **conditional-edge** (running 상태에서 caller 가 complete/fail 호출해야) |
| `replyOperation upstream abort listener` (reply-run-registry.ts:279-285) | `{once: true}` 자동 제거 | **conditional-edge** (abort 발화 시에만; 발화 안 되면 upstream signal lifetime 동안 attached) |
| `typingTtlTimer`, `dispatchIdleTimer` (typing.ts:106, 223) | cleanup() (L75-82) — markRunComplete/markDispatchIdle/TTL 자체 발화 | **unconditional** via sealed flag |
| `liveIdleTimer` (acp-projector.ts:256) | clearLiveIdleTimer (L212-218) — flush/resetTurnState | **unconditional** via projector.flush(true) |
| `BlockReplyCoalescer idleTimer` (block-reply-coalescer.ts:52) | clearIdleTimer (L31-37) — flush/stop | **unconditional** in 정상 flow (agent-runner.ts:1510 flush 후 stop) |

### FIND-auto-reply-lifecycle-001 (P2): drain inner auth-groups loop 가 mid-await clearSessionQueues 무시

- `src/auto-reply/reply/queue/drain.ts:239-264` 의 inner for-loop 는 L221 `queue.items.slice()` snapshot 과 L223 `authGroups` 를 closure 안에 잡아 둔 채 N 회 await. 첫 iteration 의 await 도중 외부 `clearSessionQueues` 가 호출돼도 (a) snapshot 면역, (b) inner for 본문에 identity check 부재 → 나머지 groupItems 가 그대로 effectiveRunFollowup 으로 전달돼 model 호출 발생.
- CAND-012 fix (137d566422) 의 identity guard 는 outer finally (drain.ts:307) 만 보호. 같은 family / 다른 axis.
- cross_refs: FIND-auto-reply-concurrency-001 (queue lifecycle map ownership). counter: `rg -n "FOLLOWUP_QUEUES\.get\(key\) === queue" src/auto-reply/reply/queue/drain.ts` → outer finally 1건만 매치.
- 재현: Deferred gate 로 iter1 await park → clearSessionQueues 호출 → gate release → iter2 의 effectiveRunFollowup 호출 관측.

### FIND-auto-reply-lifecycle-002 (P3): inbound-debounce graceful shutdown drain 부재

- `src/auto-reply/inbound-debounce.ts:177-180` 의 `setTimeout(...).unref?.()` + 반환 API `{enqueue, flushKey}` (L265) 에 flushAll/dispose 부재. shutdown 시 buffered inbound items silent drop.
- gateway SIGTERM handler (`src/cli/gateway-cli/run-loop.ts:512`) 가 debouncer flush 호출 안 함 (`rg -n "flushKey|flushAll" src/cli src/gateway src/channels` → 0건).
- platform ack (200 OK) 이 이미 발신된 시점이므로 retry 회복 안 됨. severity 보수 P3 — gateway shutdown ordering 다른 작업이 timer 발화 시간 확보할 수도 있어 빈도 race 의존.
- upstream 최근 fix: f5ebe63ecd (2026-05-14 preserve debounce ordering), df27091f5f (2026-04-13 avoid leaking cleanup) — shutdown drain 축 0건.

### 검증된 non-issue (lifecycle 측 cleanup 견고)

- **drain identity guard (outer finally)**: CAND-012 fix (137d566422) 가 `FOLLOWUP_QUEUES.get(key) === queue` 가드로 outer finally 의 map / callback orphan 완전 차단. test `drain.identity-guard.test.ts` 가 verify.
- **reply-dispatcher reservation pattern**: `pending=1` reservation + markComplete microtask 가 No-reply 시나리오에서도 unregister 보장 (reply-dispatcher.ts:288-306). `withReplyDispatcher` finally 가 항상 markComplete 호출 (dispatch-dispatcher.ts:23). unconditional.
- **foreground reply fence**: dispatch.ts:282-333 의 try/finally 가 `endForegroundReplyFence` 보장. activeDispatches 카운터 + generation token 으로 supersede race 차단.
- **typing controller sealed flag**: typing.ts:71-91 의 `sealed` boolean 이 double-cleanup / late callback 봉인. TTL timer + dispatch-idle grace timer 두 경로가 모두 `cleanup()` 도달.
- **acp projector flush**: dispatch-acp.ts:508/569 두 path (success/catch) 모두 `projector.flush(true)` 호출 → `clearLiveIdleTimer()` 보장.
- **block-reply-pipeline flush-then-stop**: agent-runner.ts:1509-1511 가 `await flush({force:true})` 후 `stop()` 순서 보장. coalescer.stop 단독 호출 시 데이터 loss 가능하지만 caller 가 flush 선행.
- **upstreamAbortSignal listener**: reply-run-registry.ts:279-285 의 `{once: true}` 가 abort 발화 시 자동 제거. 발화 안 될 경우 upstream signal lifetime 동안 closure attach — memory-leak-hunter 가 검토 후 OK (도메인 노트 line 139).
- **inboundDedupeInFlight Set**: dispatch-from-config.ts 의 모든 return path (성공 / case-별 / catch) 가 commitInboundDedupeIfClaimed 또는 releaseInboundDedupe 호출 — finally 가 아닌 분기별 명시 호출이지만 grep 으로 누락 path 발견 못 함.
- **abort-primitives ABORT_MEMORY**: cap=2000 + FIFO prune (abort-primitives.ts:93-106) — memory 축에서 이미 검증.

### 결론

`auto-reply-lifecycle` 셀에서 strong cleanup gap 1건 (FIND-001, P2: inner-loop mid-await race) + graceful-shutdown gap 1건 (FIND-002, P3: unref + drain API 부재). CAND-012 fix 이후 outer finally / map-ownership 측면은 견고하지만, inner snapshot loop 의 cancel-channel 부재가 같은 family 다른 axis 의 결함. 셀 상태는 published 후 done 마감 권장.

## 탐색 방법 (lifecycle 축 재현용)

```bash
rg -n "dispose|teardown|cleanup|close" src/auto-reply
rg -n "AbortController|AbortSignal" src/auto-reply
rg -n "setInterval|clearInterval|setTimeout|clearTimeout" src/auto-reply
rg -n "FOLLOWUP_QUEUES\.get\(key\) === queue" src/auto-reply/reply/queue/drain.ts
rg -n "flushKey|flushAll|drainDebouncer" src/cli src/gateway src/channels --type=ts -g '!*.test.ts'
git log upstream/main --since="6 weeks ago" -- src/auto-reply/reply/queue/drain.ts
git log upstream/main --since="6 weeks ago" -- src/auto-reply/inbound-debounce.ts
git show 137d566422
git show f5ebe63ecd
git show df27091f5f
```
