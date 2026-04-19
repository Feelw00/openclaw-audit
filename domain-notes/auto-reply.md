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
