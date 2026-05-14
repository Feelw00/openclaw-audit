---
id: FIND-auto-reply-lifecycle-001
cell: auto-reply-lifecycle
title: followup drain auth-groups inner loop ignores mid-iteration clearSessionQueues
file: src/auto-reply/reply/queue/drain.ts
line_range: 221-265
evidence: "```ts\n          const items = queue.items.slice();\n          const summary\
  \ = previewQueueSummaryPrompt({ state: queue, noun: \"message\" });\n          const\
  \ authGroups = splitCollectItemsByAuthorization(items);\n          if (authGroups.length\
  \ === 0) {\n            const run = queue.lastRun;\n            if (!summary ||\
  \ !run) {\n              break;\n            }\n            await effectiveRunFollowup({\n\
  \              prompt: summary,\n              run,\n              enqueuedAt: Date.now(),\n\
  \            });\n            clearQueueSummaryState(queue);\n            continue;\n\
  \          }\n\n          let pendingSummary = summary;\n          for (const groupItems\
  \ of authGroups) {\n            const run = groupItems.at(-1)?.run ?? queue.lastRun;\n\
  \            if (!run) {\n              break;\n            }\n\n            const\
  \ routing = resolveOriginRoutingMetadata(groupItems);\n            const prompt\
  \ = buildCollectPrompt({\n              title: \"[Queued messages while agent was\
  \ busy]\",\n              items: groupItems,\n              summary: pendingSummary,\n\
  \              renderItem: renderCollectItem,\n            });\n            await\
  \ effectiveRunFollowup({\n              prompt,\n              run,\n          \
  \    enqueuedAt: Date.now(),\n              ...routing,\n              ...collectQueuedImages(groupItems),\n\
  \            });\n            queue.items.splice(0, groupItems.length);\n      \
  \      if (pendingSummary) {\n              clearQueueSummaryState(queue);\n   \
  \           pendingSummary = undefined;\n            }\n          }\n          continue;\n\
  ```\n"
symptom_type: lifecycle-gap
problem: '''collect mode 의 auth-groups inner for loop (drain.ts:239-264) 는 매 iteration
  의

  `await effectiveRunFollowup(...)` (L252) 뒤에 queue.items 가 외부 cleanup

  (clearSessionQueues / clearFollowupQueue) 에 의해 비워졌는지 검사하지 않는다.

  snapshot `items` (L221) 와 `authGroups` (L223) 는 loop 시작 전에 캡처된 메모리상의

  배열이므로, 첫 group 의 await 도중 사용자가 /stop 으로 session queue 를 clear 하더라도

  pre-captured groupItems[1..N-1] 이 그대로 effectiveRunFollowup 으로 흘러간다. 결과:

  사용자가 abort 한 후에도 이전에 큐잉됐던 메시지들이 모델로 전달되어 실행됨 (cancel

  semantics 위반). CAND-012 의 identity guard (drain.ts:307) 는 outer finally 만 보호하므로

  inner loop 의 mid-await race 는 잡지 못한다.''

  '
mechanism: "'1. queue.items = [A, B, C] (auth keys K1, K1, K2 → authGroups [[A,B],\
  \ [C]]).\n2. drain entered collect branch; L221 snapshot items = [A,B,C], L223 authGroups\
  \ = [[A,B], [C]].\n3. iteration 1: effectiveRunFollowup(group [A,B]) — L252 await\
  \ yields microtask.\n4. 그 사이 다른 inbound channel 에서 user 가 /stop 송신 →\n   gateway\
  \ 의 session reset 경로가 `clearSessionQueues([key])` 호출\n   (cleanup.ts:67 `clearFollowupQueue(cleaned)`):\n\
  \   queue.items.length = 0; queue.droppedCount = 0; FOLLOWUP_QUEUES.delete(key).\n\
  \   `clearFollowupDrainCallback(key)` 도 호출.\n5. iteration 1 의 await 가 resolve →\
  \ L259 `queue.items.splice(0, 2)` — empty array 에\n   splice 는 no-op. L260-263 pendingSummary\
  \ undef.\n6. iteration 2: groupItems = [C] (pre-captured). L240 run = C.run ?? queue.lastRun\n\
  \   (queue.lastRun 도 clearFollowupQueue 에서 undefined 로 설정되었지만 groupItems 의\n   run\
  \ 이 있으므로 resolve). L252 await effectiveRunFollowup(group [C]) → C 가 model 로\n  \
  \ 전달되어 실행됨. **사용자가 명시적으로 abort 한 뒤에도 C 가 처리됨**.\n7. for 루프 종료 후 outer while: queue.items.length\
  \ === 0 → 종료. finally L307 identity\n   check: FOLLOWUP_QUEUES.get(key) → undefined\
  \ (cleared) !== queue → delete 안 함 (정상).\n   callback 도 clearSessionQueues 가 이미\
  \ 제거함. orphan 은 없음, 그러나 C 의 실행은\n   이미 발생.\n8. concurrent enqueue 도 동일 race: clearSessionQueues\
  \ 가 finite window 안에 발생하지\n   않더라도, refresh/abort hooks 의 sync 경로가 같은 microtask\
  \ boundary 에서 발생할\n   수 있다.'\n"
root_cause_chain:
- why: 왜 inner auth-groups for loop 가 mid-await 에서 외부 clear 를 감지 못 하나?
  because: snapshot 패턴 (L221 `queue.items.slice()`, L223 `splitCollectItemsByAuthorization`)
    이 for loop 진입 전에 한 번만 일어나므로, await yield 동안 underlying state (queue.items, FOLLOWUP_QUEUES
    매핑) 가 바뀌어도 loop 가 그 신호를 받을 channel 이 부재. inner for 본문에 `FOLLOWUP_QUEUES.get(key)
    === queue` 같은 identity check 나 `queue.items.length === 0 && queue.droppedCount
    === 0` 가드가 없다.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:239-264
- why: 왜 CAND-012 의 identity guard 가 이 경로를 못 막나?
  because: CAND-012 fix (137d566422) 는 drain IIFE 의 `finally` block (drain.ts:301-314)
    에만 `FOLLOWUP_QUEUES.get(key) === queue` identity 비교를 추가했다. inner auth-groups for
    의 각 iteration 사이에는 동등한 가드가 없고, snapshot 패턴 자체가 외부 clear 의 효과를 무력화한다 — clearSessionQueues
    는 queue.items 만 비우지 snapshot 배열을 무력화할 방법이 없다.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:301-314
- why: 왜 clearSessionQueues 가 in-flight drain 을 abort 시킬 수 없나?
  because: cleanup.ts 의 `clearSessionQueues` (52-72) 는 sync 호출로 `clearFollowupQueue`
    (state.ts:74-88) → queue.items.length = 0 + FOLLOWUP_QUEUES.delete(cleaned) 만
    수행. 이미 in-flight drain IIFE 의 closure 가 자신만의 `queue` 참조를 들고 있고, snapshot `items`
    / `authGroups` / `groupItems` 도 closure variable. cancel 신호 / AbortSignal / drain
    측 mutex 가 없으므로 clearSessionQueues 가 drain 의 진행을 멈출 수 없다.
  evidence_ref: src/auto-reply/reply/queue/cleanup.ts:52-72
- why: 왜 이 inner-loop race 가 외부 cleanup 의 정의된 의도와 어긋나는가?
  because: '`clearSessionQueues` 의 호출 경로 (e.g. session reset, /stop, gateway server-methods/sessions.ts,
    session-reset-service.ts) 의 contract 는 "이 key 의 모든 pending followup 을 즉시 중단" 이다.
    inner loop 가 snapshot 으로 N 개 group 을 처리하는 도중 N-1 개의 group 이 user 의 abort 시점 이후에도
    model 에 도달하면, 사용자에게는 /stop 이 부분적으로만 동작한 것으로 보인다. 이는 CAND-012 의 motivation 과 동일
    가족이지만 별도 axis 의 문제.'
  evidence_ref: src/auto-reply/reply/session-reset-cleanup.ts:2 (cleanup wrapper),
    src/gateway/server-methods/sessions.ts (clearSessionQueues 호출 경로)
- why: 왜 production 에서 자주 트리거되나?
  because: 'collect mode 는 `queueMode: "collect"` 설정 시 활성화되어 multiple queued messages
    를 single agent run 에 batch 한다. 동일 sessionKey 로 빠른 연속 inbound + 바로 이어진 /stop 시나리오는
    정규 (사용자가 잘못 전송 후 즉시 stop) UX. authGroups 가 2개 이상이려면 단지 빠른 메시지 사이에 sender 또는 exec
    context 가 바뀌면 된다 — 예: A=user1, B=user2 in 그룹 chat 또는 A=text, B=command (execOverrides
    변경) 등.'
  evidence_ref: src/auto-reply/reply/queue/drain.ts:104-131 (splitCollectItemsByAuthorization)
impact_hypothesis: wrong-output
impact_detail: '''정성: collect mode 가 켜진 followup queue 에서 user 가 /stop 으로 session
  을

  clear 한 후에도, 직전 drain IIFE 가 capture 한 authGroups 의 나머지 group 들이

  model 로 전달되어 실행된다. 사용자 체감: "stop 했는데 모델이 계속 응답함".

  심각도 측면 — 단순 visual race 가 아니라 실제 LLM 호출 비용 + 의도치 않은 action

  (tool 실행 포함). 정량 추정: collect mode + 한 batch 안에 ≥2 auth groups + /stop

  의 교차 시점. /stop 으로 abort 한 모든 메시지 중 stale execution 비율은 batch

  size 와 timing distribution 에 의존하지만 결정적 재현 가능 (Deferred gate 로 첫

  iteration 의 await 를 park 후 clearSessionQueues 호출 → 두 번째 group 의 실행

  관측). 추가: clearFollowupDrainCallback 까지 cleanup 이 진행됐으므로 drain 종료

  시 후속 enqueue 가 kickIfIdle 로 살아나지 않아 "이미 abort 한 메시지는 사라졌지만

  중간 메시지는 처리됨" 의 모순적 상태가 user 에게 노출.''

  '
severity: P2
counter_evidence:
  path: src/auto-reply/reply/queue/drain.ts
  line: 301-314
  reason: "'CAND-012 fix (137d566422) 가 outer finally 의 `FOLLOWUP_QUEUES.get(key)\
    \ === queue`\nidentity guard 를 추가했고, drain 종료 시 map entry 와 callback 의 orphan\
    \ / dup\ndelete 는 차단된다 — 그러나 inner auth-groups for loop 의 mid-await race 는 다른\n\
    axis 이고 같은 가드가 적용되지 않았다.\n\n확인한 반증 카테고리:\n(1) 숨은 방어 / defense-in-depth:\n    `rg\
    \ -n \"FOLLOWUP_QUEUES\\.get\\(key\\) === queue|queue\\.items\\.length === 0\"\
    \n     src/auto-reply/reply/queue/drain.ts`\n    → drain.ts:307 (outer finally)\
    \ 만 매치. inner for 안에는 0 건.\n    `rg -n \"AbortSignal|abort\" src/auto-reply/reply/queue/`\n\
    \    → drain.ts 의 IIFE 에 abort signal channel 0 건.\n(2) outer while 가드: drain.ts:185\
    \ `while (queue.items.length > 0 || queue.droppedCount > 0)`\n    는 매 iteration\
    \ 시작 시 live state 를 본다. 그러나 inner for 가 끝난 뒤 `continue`\n    (L265) 로 outer 로\
    \ 돌아가야 검사가 일어나므로, inner for 의 중간 group 은 검사\n    받지 못함.\n(3) 기존 테스트 커버리지:\n  \
    \  `rg -n \"clearSessionQueues|clearFollowupQueue\" src/auto-reply/reply/queue/*.test.ts`\n\
    \    → state.test 와 cleanup.test 만 매치. drain.ts 의 inner-loop x clearSessionQueues\n\
    \    교차 race 를 다루는 테스트 0 건. drain.identity-guard.test.ts (CAND-012) 는 outer\n\
    \    finally 만 verify.\n(4) primary-path inversion (CAL-001) — inner for 에서 unconditional\
    \ cleanup 경로\n    존재 여부 = **부재**. snapshot 패턴 자체가 외부 신호를 받을 수 없다 →\n    defensive\
    \ cleanup 누락 (false positive 함정 아님).\n(5) Hot-path-vs-test-path (CAL-003): drain.identity-guard.test.ts\
    \ 의 시나리오는\n    outer finally 에 집중. inner for 의 N≥2 authGroups 시나리오는 별도 재현 필요.\n\
    \    production hot-path = collect mode + 그룹 chat / 빠른 연속 메시지 → 정규 경로.\n(6) Upstream-dup\
    \ (CAL-004/008):\n    `git log upstream/main --since=\"6 weeks ago\" -- src/auto-reply/reply/queue/drain.ts`\n\
    \    → 712644f0d9 (preserve pending items splice), 43d4be9027 (split by auth),\n\
    \    137d566422 (identity guard outer finally), 8a23485472 (preserve queue metadata),\n\
    \    468c6a0101 (perf trim), e27c32b9b0 / 3eec9e4642 (refactor route helpers),\n\
    \    3e2bc28e51 (forward chat images), 155162a8cd (lint cleanup) — 모두 outer\n\
    \    guarantee / data integrity 축. inner for-loop x mid-await cleanup race 0 건.\n\
    \    `gh pr list --state open --search \"auto-reply queue drain identity in:title,body\"\
    `\n    → 본 cell 내 OPEN PR 없음.'\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs:
- FIND-auto-reply-concurrency-001
---
# followup drain auth-groups inner loop ignores mid-iteration clearSessionQueues

## 문제

`scheduleFollowupDrain` 의 collect mode branch (drain.ts:194-265) 는 queue.items 를 snapshot 한 뒤 `splitCollectItemsByAuthorization` 으로 authGroups 를 만들고, 각 group 마다 `effectiveRunFollowup` 을 await 한다. 이 inner for loop (L239-264) 는 각 group 의 await 사이에 외부 cleanup (`clearSessionQueues` / `clearFollowupQueue`) 이 발생했는지 검사하지 않는다.

snapshot 변수 (items, authGroups, groupItems) 는 모두 closure 안에 살아있으므로, await 도중 user 가 /stop 으로 session 을 reset 하더라도 pre-captured group 들이 그대로 model 에 전달된다. CAND-012 (drain.identity-guard) 의 fix 는 outer finally 의 map entry 정합성만 보장하고, inner loop 의 in-flight cancel 은 다루지 않는다.

## 발현 메커니즘

```
T0  enqueueFollowupRun: queue.items = [A, B, C]   (A.auth=K1, B.auth=K1, C.auth=K2)
T1  scheduleFollowupDrain entered; outer while iter:
      waitForQueueDebounce → resolve
      mode=collect → drainCollectQueueStep returns "ready"
      L221 items = [A, B, C].slice()
      L223 authGroups = [[A,B], [C]]
T2  iter1: L252 await effectiveRunFollowup(group=[A,B])           ── microtask yield ──
T3  다른 inbound: user 가 /stop 도착 → session reset path 가
      clearSessionQueues([key]) 호출 (cleanup.ts:67-69):
        clearFollowupQueue(key): queue.items.length = 0;
                                 queue.droppedCount = 0;
                                 FOLLOWUP_QUEUES.delete(key)
        clearFollowupDrainCallback(key): FOLLOWUP_RUN_CALLBACKS.delete(key)
T4  iter1 의 await resolve.
      L259 queue.items.splice(0, 2) — empty array → no-op.
      L260 pendingSummary clear.
T5  iter2: groupItems = [C] (pre-captured!).
      L240 run = C.run.
      L252 await effectiveRunFollowup({prompt:..., run:C.run, ...})
      → **C 가 LLM 으로 전달되어 실행됨**.
T6  for 루프 종료, L265 continue → outer while: queue.items.length=0 → 종료.
T7  finally L301-314: queue.draining=false; identity check
      FOLLOWUP_QUEUES.get(key) → undefined → 조건 false → delete 안 함 (정상).
```

key 시점: T3 (clearSessionQueues) 와 T2~T5 (inner loop await) 의 교차. clearSessionQueues 는 drain IIFE 의 closure 에 직접 도달할 채널이 없고, snapshot 배열은 외부 mutation 면역.

CAND-012 의 fix 와 차이:

| 영역 | CAND-012 fix (outer finally) | 본 FIND (inner for) |
|---|---|---|
| 보호 대상 | map entry / callback 의 orphan | in-flight item 의 cancel |
| 가드 | `FOLLOWUP_QUEUES.get(key) === queue` | **부재** |
| 적용 위치 | drain.ts:307 | drain.ts:239-264 (inner for) |
| 시나리오 | drain 종료 시점의 map race | drain 중간의 user abort |

## 근본 원인 분석

1. **Snapshot 패턴 + 외부 cancel 부재**: L221 `queue.items.slice()` 와 L223 `splitCollectItemsByAuthorization(items)` 가 outer while 의 매 iteration 시작 시 capture. inner for 본문 전반에서 snapshot 만 본다. 외부 cleanup 의 효과를 inner for 가 알 수 있는 channel (AbortSignal, identity check, mutex) 부재.

2. **CAND-012 fix scope 한정**: 137d566422 의 identity guard 는 finally 만 수정 (drain.ts:307). 같은 motivation (외부 clearSessionQueues 와의 race) 이지만 inner-loop axis 는 검토 범위 밖이었다. drain.identity-guard.test.ts 도 outer finally 시나리오만 cover.

3. **cleanup contract 미스매치**: `clearSessionQueues` 의 호출 경로 (gateway/server-methods/sessions.ts, session-reset-service.ts, reply/session-reset-cleanup.ts) 의 의도는 "이 key 의 모든 pending 을 즉시 중단". 그러나 drain IIFE 는 자기 closure 의 snapshot 으로 동작하므로 cleanup 의 효과가 inner for 의 진행을 막지 못한다 — 사용자 시점에서 abort 가 부분적으로만 적용된다.

## 영향

- **사용자 체감**: collect mode + N≥2 auth groups + /stop 의 교차에서, /stop 으로 abort 한 메시지 일부가 모델로 전달되어 응답이 계속 흘러나옴. tool 실행이 들어간 경우 비용 + 의도치 않은 action.
- **노출 경로**: production collect mode (configurable per provider/channel; 그룹 chat 의 빠른 연속 메시지 처리, 다중 sender) 가 정규 경로. authGroups 가 2개 이상이려면 같은 batch 안에서 sender (senderId/senderE164) 또는 execOverrides 또는 bashElevated 가 바뀌면 충분.
- **빈도**: collect mode 가 켜진 모든 followup queue 에서 batch size > 1 인 모든 drain 호출이 잠재적 노출. /stop 의 timing distribution 에 의존.
- **재현 가능성**: Deferred gate 로 inner for 의 iter1 await 를 park, 그 사이 clearSessionQueues 호출, gate release → iter2 의 effectiveRunFollowup 호출 관측. 결정적.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "FOLLOWUP_QUEUES\.get\(key\) === queue|queue\.items\.length === 0|queue\.draining"  src/auto-reply/reply/queue/drain.ts` → outer finally (L303-310) 만 매치. inner for 안에 동등 가드 0 건.
- `rg -n "AbortSignal|abort" src/auto-reply/reply/queue/` → drain.ts 의 IIFE 본문에 abort signal channel 0 건. effectiveRunFollowup 자체는 ReplyOperation 의 abortSignal 을 받지만, 그건 그 단일 followup 의 model run 만 abort. drain loop 의 진행은 별개.
- `clearSessionQueues` 가 in-flight drain 을 cancel 할 수 있는 API 부재 — cleanup.ts:52-72 는 sync mutation 만.

### 기존 테스트 커버리지

- `src/auto-reply/reply/queue/drain.identity-guard.test.ts` (CAND-012) 는 outer finally 의 map entry race 만 검증. inner for x clearSessionQueues 교차 시나리오 0 건.
- `src/auto-reply/reply/queue/cleanup.test.ts` (있다면) 는 clearSessionQueues 의 sync 동작만 검증할 가능성.
- `rg -n "clearSessionQueues" src/auto-reply --type=ts -g '*.test.ts'` → drain 의 inner for 와 교차하는 시나리오 미커버.

### 호출 빈도 / 경로 활성 여부

- collect mode 는 `queueMode: "collect"` 설정 시 활성화. 그룹 chat 의 빠른 연속 메시지 또는 다중 sender batch 에서 authGroups ≥ 2 가 자주 발생.
- `splitCollectItemsByAuthorization` (drain.ts:104-131) 의 key 는 senderId/senderE164/senderIsOwner/execOverrides/bashElevated. 그룹 환경에서 sender 가 바뀌면 split 보장.
- `clearSessionQueues` 호출 경로: gateway 의 session reset, `/stop` command handler, dispatchInboundMessage 의 abort 분기 — production hot-path.

### Primary-path inversion (CAL-001)

inner for 에서 unconditional cleanup / cancel 경로 존재 = **부재**. snapshot 패턴 자체가 외부 신호 면역. defensive cleanup 누락 (false positive 함정 아님).

### Hot-path-vs-test-path consistency (CAL-003)

production hot-path = collect mode + N≥2 authGroups + 동시 /stop. 기존 테스트의 drain.identity-guard.test.ts 는 outer finally만 reproduce. inner for 의 mid-await race 는 별도 Deferred 기반 재현 필요.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/auto-reply/reply/queue/drain.ts` → 8 건:
  - `137d566422` fix: guard FOLLOWUP_QUEUES delete against late drain finally (CAND-012, outer finally 만)
  - `712644f0d9` fix(queue): preserve pending items during drains (splice(0) → splice(0, N))
  - `43d4be9027` fix(queue): split collect batches by auth context (splitCollectItemsByAuthorization 도입)
  - `8a23485472` fix(reply): preserve queue metadata after perf cherry-picks
  - `468c6a0101` perf(core): trim reply and agent allocation churn
  - `e27c32b9b0` / `3eec9e4642` refactor: route helpers
  - `3e2bc28e51` fix: forward chat images to acp dispatch
  - `155162a8cd` chore(lint)

  inner for-loop x mid-await cancel race 다루는 commit **0 건**.
- `gh pr list --repo openclaw/openclaw --state open --search "auto-reply queue drain in:title,body"` → 본 file 의 OPEN PR 없음.
- 본 audit 의 CAND-012 (PR #68839 MERGED) 와 동일 가족 / 다른 axis. cross_refs 로 FIND-auto-reply-concurrency-001 연결.

## Self-check

### 내가 확실한 근거

- drain.ts:221-264 의 snapshot 패턴 + inner for 구조 (Read 로 확인).
- drain.ts:301-314 의 outer finally identity guard 가 CAND-012 fix 의 적용 범위 (137d566422 commit + drain.identity-guard.test.ts).
- cleanup.ts:52-72 의 clearSessionQueues 동작이 sync 이며 in-flight drain 을 cancel 할 channel 부재 (Read 확인).
- splitCollectItemsByAuthorization (drain.ts:104-131) 의 auth key 가 sender/exec context 로 자주 split 되는 점 (groupKey field 확인).
- collect mode 의 production 활성 — config 옵션이고, 그룹 chat 의 batch 처리에 사용된다는 점 (resolvedQueue.mode 사용처 추적 가능).

### 내가 한 가정

- collect mode 가 사용자 환경에서 enable 되는 빈도는 환경 변수 / 채널 설정에 의존. 그러나 collect mode 가 disabled 인 경우 outer while 의 second branch (drainNextQueueItem L294) 로 가는데, 그 branch 는 매 iteration 시작 시 `previewQueueSummaryPrompt` 와 `drainNextQueueItem` 로 live state 를 본다 → race 영향 다름.
- `/stop` 의 timing 이 inner for 의 첫 iteration await 와 일치할 확률 — 빠른 사용자 input 시나리오에서는 충분히 발생.
- effectiveRunFollowup 이 받은 group 의 run.sessionId 가 stop 이후 아직 같은 session 을 가리키는 점 — clearSessionQueues 는 queue 만 비우고 session 자체는 보존되므로 run 의 sessionId 는 그대로 유효. 모델 호출이 실제로 실행됨.

### 확인 안 한 것 중 영향 가능성

- clearSessionQueues 이후 followup run 의 model call 이 abortSignal 로 중단되는지 — replyOperation 의 abortSignal 은 별도 lifecycle 이므로 drain 의 in-flight model run 도 같이 abort 될 가능성 있음. 만약 그렇다면 user 체감은 줄지만 "한 번이라도 호출됐다" 는 비용 / 부작용 (tool 실행) 은 남는다. 검증 필요.
- collect mode 의 production 사용 비율 — 환경 변수 빈도 확인 안 함.
- pendingSummary 의 라이프사이클 (L260-263) 이 외부 clear 이후 wrong-state 로 흐를 가능성 — clearQueueSummaryState 를 inner for 가 호출하는데 queue 자체가 이미 cleared → 무해해 보이지만 corner case 가능.
