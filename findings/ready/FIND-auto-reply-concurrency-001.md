---
id: FIND-auto-reply-concurrency-001
cell: auto-reply-concurrency
title: drain finally 의 FOLLOWUP_QUEUES.delete 가 identity 검증 없어 후속 queue 를 orphan 시킴
file: src/auto-reply/reply/queue/drain.ts
line_range: 263-271
evidence: "```ts\n    } finally {\n      queue.draining = false;\n      if (queue.items.length\
  \ === 0 && queue.droppedCount === 0) {\n        FOLLOWUP_QUEUES.delete(key);\n \
  \       clearFollowupDrainCallback(key);\n      } else {\n        scheduleFollowupDrain(key,\
  \ effectiveRunFollowup);\n      }\n    }\n```\n"
symptom_type: concurrency-race
problem: '`scheduleFollowupDrain` 내 drain IIFE 의 finally 블록 (L263-270) 은 자신이 들고 있는

  `queue` 참조가 이미 `FOLLOWUP_QUEUES` map 의 현재 entry 와 동일한지 확인하지 않고

  `FOLLOWUP_QUEUES.delete(key)` + `clearFollowupDrainCallback(key)` 를 호출한다.

  `/stop` (commands-session-abort.ts:146) 이나 subagent kill (subagent-control.ts:186)

  또는 session reset (session-reset-service.ts:258) 이 drain 의 `await` 지점에서

  `clearSessionQueues` → `clearFollowupQueue(key)` 를 호출하면 Q1 이 map 에서 제거되고

  items 이 in-place 로 비워진다. 이후 enqueueFollowupRun 이 새 Q2 를 map 에 등록하고

  D2 를 kick 해도, D1 의 finally 가 늦게 실행되며 map 에서 Q2 를 지우고 Q2 의 callback 도

  삭제한다. Q2 는 orphan 된 상태로 D2 에서만 처리되며, `getFollowupQueueDepth(key)` 는

  0 을 반환하여 `/status` 등 관찰자가 실제 pending 수를 잘못 본다.

  '
mechanism: "타임라인 (단일 Node.js 이벤트 루프, FOLLOWUP_QUEUES 는 전역 Map):\n\n1. T0: user msg1\
  \ → enqueueFollowupRun → Q1 created + drain D1 스케줄.\n   D1 은 L216 `await effectiveRunFollowup(...)`\
  \ 에서 장시간 대기 (agent 실행은 수 초~수 분).\n2. T1: user `/stop` → commands-session-abort.ts:146\
  \ `clearSessionQueues([key])` 호출.\n   → cleanup.ts:67 `clearFollowupQueue(cleaned)`\
  \ → state.ts:80-86:\n     queue.items.length = 0         // Q1.items 비워짐 (D1 이 들고\
  \ 있는 배열과 동일 참조)\n     FOLLOWUP_QUEUES.delete(cleaned) // map 에서 Q1 제거\n   → cleanup.ts:68\
  \ `clearFollowupDrainCallback(cleaned)` → FOLLOWUP_RUN_CALLBACKS.delete.\n3. T2:\
  \ user msg2 → enqueueFollowupRun:\n   - state.ts:40 `FOLLOWUP_QUEUES.get(key)` →\
  \ undefined (T1 에서 삭제됨)\n   - L49-71 new Q2 (items=[], draining=false) 생성, map.set(key,\
  \ Q2)\n   - enqueue.ts:96 `queue.items.push(run)` → Q2.items=[msg2]\n   - enqueue.ts:101\
  \ rememberFollowupDrainCallback (caller 가 runFollowup 제공 시)\n   - enqueue.ts:106-108\
  \ `!queue.draining` → kickFollowupDrainIfIdle\n   - drain.ts:37-43 kickFollowupDrainIfIdle\
  \ → scheduleFollowupDrain(key, cb)\n   - drain.ts:139 beginQueueDrain(FOLLOWUP_QUEUES,\
  \ key) → Q2 발견, Q2.draining=true → D2 시작\n4. T3: D1 의 `await` 종료. L222 splice(0,\
  \ groupItems.length) — Q1.items 가 비어 있어 no-op.\n   L259 loop guard `queue.items.length\
  \ > 0 || queue.droppedCount > 0` → false. 정상 종료.\n5. T4: D1 finally (L263-270):\n\
  \   - L264 `queue.draining = false` (Q1 — dangling, 영향 없음)\n   - L265 `queue.items.length\
  \ === 0 && queue.droppedCount === 0` → true (Q1 은 비어 있음)\n   - L266 `FOLLOWUP_QUEUES.delete(key)`\
  \ ← **map 에서 Q2 가 제거됨** (key 가 동일하므로)\n   - L267 `clearFollowupDrainCallback(key)`\
  \ ← Q2 용 callback 삭제.\n6. T5: `getFollowupQueueDepth(key)` (status-text.ts:247)\
  \ → enqueue.ts:114:\n   `getExistingFollowupQueue(key)` → undefined → 반환 0. 실제로는\
  \ Q2.items=[msg2] 가 D2 에서\n   처리 중이거나 처리되지 않은 상태이지만 observer 는 0 으로 본다.\n7. T6 (이후):\
  \ user msg3 → enqueueFollowupRun → getFollowupQueue 는 map 에 key 없음을 보고\n   Q3 를\
  \ 새로 만든다. runFollowup 제공 시 callback 재설정되어 D3 시작. 이때 Q2 의 잔여 items\n   는 D2 에서 처리되고\
  \ Q3 는 별도 경로로 진행되어 **동일 key 에 대해 D2 와 D3 가 동시에 실행**.\n"
root_cause_chain:
- why: 왜 finally 의 delete 가 Q2 를 지울 수 있는가?
  because: L266 은 `FOLLOWUP_QUEUES.delete(key)` 로 key 로만 지우고, 현재 map entry 가 자신이 들고
    있는 `queue` 참조와 같은지 (`FOLLOWUP_QUEUES.get(key) === queue`) 검증하지 않는다. clearFollowupQueue
    (state.ts:86) 가 중간에 map 에서 Q1 을 제거하고 이후 enqueue 가 Q2 를 등록해도 D1 은 이를 모른다.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:265-267
- why: 왜 D1 이 Q1 의 items 가 비어 있음을 "정상 완료" 로 판단하는가?
  because: clearFollowupQueue 는 in-place 로 `queue.items.length = 0` (state.ts:81)
    하여 D1 의 array reference 를 그 자리에서 비운다. D1 의 loop guard 는 `queue.items.length >
    0` 이므로 items 가 비면 정상 exit 경로를 탄다. 여기에 "외부 클리어" 를 구분하는 플래그가 없다.
  evidence_ref: src/auto-reply/reply/queue/state.ts:80-86
- why: 왜 Q2 가 orphan 되어도 D2 가 계속 동작하는가?
  because: D2 는 `beginQueueDrain(map, key)` 시점에 Q2 를 잡아 `queue` 로 closure 에 캡처한다 (drain.ts:139-145).
    이후 map entry 가 제거되어도 D2 의 `queue.items` 는 여전히 Q2.items 배열을 가리키므로 processing 은
    이어진다. 그러나 외부 observer (getFollowupQueueDepth, getExistingFollowupQueue) 는 map
    만 보므로 Q2 의 존재를 모른다.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:139-147
- why: 왜 현재 테스트가 이 race 를 잡지 못하는가?
  because: queue.drain-restart.test.ts:236-260 은 clearSessionQueues 이후 enqueue 한 msg
    가 처리되지 "않음"을 검증할 뿐, D1 finally 가 늦게 실행되어 Q2 callback 을 지우는 경로를 검증하지 않는다. debounceMs=0
    + OPENCLAW_TEST_FAST 환경에서 await 윈도우가 거의 0 이라 T1→T2→T3 가 겹치는 시퀀스가 자연 발생하지 않는다.
  evidence_ref: src/auto-reply/reply/queue.drain-restart.test.ts:236-260
impact_hypothesis: wrong-output
impact_detail: "정성: `/stop` 직후 다음 메시지가 들어오는 흔한 시나리오에서 발생.\n- 관찰 지표 오염: `/status` 명령이\
  \ 읽는 `getFollowupQueueDepth` (status-text.ts:247) 가 실제\n  pending 수 (Q2.items) 를\
  \ 0 으로 보고. 유저가 \"queue 비어 있음\" 으로 인식.\n- 콜백 손실 윈도우: D1.finally 가 `clearFollowupDrainCallback(key)`\
  \ 한 후, 그 사이에 들어온\n  enqueue 가 runFollowup 을 생략하면 (이론상; production caller 인 agent-runner.ts:1002\
  \ 는\n  항상 제공) 재시작할 callback 이 없어 메시지 drop 가능.\n- Double-drain: Q2 에 대한 D2 와 Q3 에\
  \ 대한 D3 가 동일 session key 로 동시에 실행되어 agent\n  run 이 병렬화됨 (intended 는 per-key serialization).\n\
  정량: 재현 조건 = (drain 내 최소 1회 await) AND (await 중 clearSessionQueues 호출) AND\n(clearSessionQueues\
  \ 이후 enqueueFollowupRun). drain.ts 는 L151, L161, L216 등 여러 await 존재.\nagent 실행은\
  \ 초~분 단위이므로 window 가 넓음.\n"
severity: P2
counter_evidence:
  path: src/auto-reply/reply/queue/drain.ts
  line: 263-271
  reason: "R-3 / R-5 에 따른 방어 탐색:\n\n1) `rg -n \"Mutex|Semaphore|AsyncLock|acquire|release\"\
    \ src/auto-reply/reply/queue/` → 매치 0건.\n   외부 락 없음.\n2) `rg -n \"AbortController|AbortSignal|signal\\\
    .(abort|addEventListener)\" src/auto-reply/reply/queue/`\n   → 매치 0건. Abort 신호\
    \ 전파 없음.\n3) identity check 탐색: `rg -n \"FOLLOWUP_QUEUES\\.get\\(key\\)\\s*===|map\\\
    .get\\(key\\)\\s*===\" src/auto-reply/`\n   → 매치 0건. drain finally 가 current map\
    \ entry 와 자신의 queue 가 동일한지 검증하지 않음.\n4) `FOLLOWUP_QUEUES.delete` grep (drain.ts:266,\
    \ state.ts:86, cleanup 경로):\n   - state.ts:86 (clearFollowupQueue): `/stop` 경로에서\
    \ unconditional 실행.\n   - drain.ts:266 (D finally): 매 drain 의 종료마다 unconditional\
    \ 실행.\n   양쪽 다 identity 검증 없음.\n\nR-5 실행 조건 분류:\n| 경로 | 조건 | 영향 |\n|---|---|---|\n\
    | state.ts:81-86 (clearFollowupQueue) | `/stop`, session reset, subagent kill\
    \ 시 호출 | unconditional in-place clear + map delete |\n| drain.ts:266 (finally\
    \ delete) | drain 종료 시 items=0 && dropped=0 | unconditional delete without identity\
    \ |\n| drain.ts:267 (finally clearCallback) | drain 종료 시 items=0 && dropped=0\
    \ | unconditional clear without identity |\n| drain.ts:269 (finally reschedule)\
    \ | items>0 OR dropped>0 | 신규 scheduleFollowupDrain 호출 |\n\nR-7 hot-path 검증: production\
    \ caller 는 `/stop` (commands-session-abort.ts:146), session reset\n(session-reset-service.ts:258),\
    \ subagent kill (subagent-control.ts:186). 모두 clearSessionQueues\n를 호출하며 직후 동일\
    \ session key 로 enqueue 가 오는 흐름이 일반적.\nprimary-path inversion: \"이 race 가 재현되려면\
    \ 어떤 atomic guard 가 우회돼야 하는가?\" →\ndrain finally 의 identity check (`FOLLOWUP_QUEUES.get(key)\
    \ === queue`) 가 실재해야 하나\n현재 파일에 없음 (rg 확인). 주장 성립.\n\n관련 참고: subagent-announce-queue.ts:204\
    \ 도 동일 패턴 (identity check 없이 delete). 주석\n(L63-64: \"Clearing the map alone isn't\
    \ enough because drain loops capture `queue` by reference\")\n에서 maintainer 가\
    \ dangling reference 를 인지하고 있으나 테스트 리셋 경로에만 대응.\n"
status: discovered
discovered_by: concurrency-auditor
discovered_at: 2026-04-19
cross_refs: []
domain_notes_ref: domain-notes/auto-reply.md
related_tests:
- src/auto-reply/reply/queue.drain-restart.test.ts
- src/auto-reply/reply/queue/cleanup.test.ts
---
# drain finally 의 FOLLOWUP_QUEUES.delete 가 identity 검증 없어 후속 queue 를 orphan 시킴

## 문제

`scheduleFollowupDrain` 의 drain IIFE 는 종료 시 finally 에서 `FOLLOWUP_QUEUES.delete(key)`
+ `clearFollowupDrainCallback(key)` 를 호출한다. 이는 "drain 이 처리 중이던 queue == 현재 map
에 등록된 queue" 라는 암묵 전제에 의존한다. 그러나 drain 의 `await` 경로 중 `/stop` 이나
session reset 이 `clearSessionQueues` → `clearFollowupQueue` 를 호출하면 map entry 가
제거되고, 이어진 enqueue 가 새 queue (Q2) 를 등록하면 원래 drain (D1) 의 finally 가 Q2 의
map entry 와 callback 을 지워버린다. Q2 는 orphan 되어 map 관찰자 관점에서 사라지고,
D2 는 분리된 경로로 실행된다.

## 발현 메커니즘

위 frontmatter `mechanism` 참조 (타임라인 T0-T7).

핵심은 **3-way interleaving**:
- D1 await 중 (agent 실행 ~ 수 초 ~ 수 분)
- clearSessionQueues (map.delete + in-place clear)
- enqueueFollowupRun (new Q2 map.set + D2 kick)
- D1 await 복귀 → finally 가 Q2 map entry 를 지움

## 근본 원인 분석

1. drain.ts:266 의 `FOLLOWUP_QUEUES.delete(key)` 는 key 만으로 삭제. 현재 map entry 가 자신이
   캡처한 `queue` 인지 확인하지 않음.
2. clearFollowupQueue (state.ts:80-86) 는 queue.items 를 in-place 로 비우고 map 에서 제거
   하므로, D1 이 "정상적인 drain 완료" 와 "외부 clear" 를 구분할 수 없다.
3. enqueueFollowupRun + kickFollowupDrainIfIdle 조합은 "map 에 queue 가 없거나 !draining"
   이면 새 drain 을 시작하므로, D1 의 await 중에도 D2 가 병렬 실행될 수 있다.
4. 기존 테스트 (queue.drain-restart.test.ts) 는 debounceMs=0 + OPENCLAW_TEST_FAST 환경에서
   await window 가 거의 0 이라 세 이벤트가 겹치는 시퀀스를 자연스럽게 만들지 못한다.

## 영향

- **impact_hypothesis: wrong-output** — queue depth 관찰 오류, 동일 key 에 대한 의도치 않은
  double-drain.
- `/status` 명령이 `getFollowupQueueDepth` 로 읽는 pending 수가 실제 Q2 에 남은 항목을 반영
  하지 못함 (map 조회 기반이므로 orphan Q2 는 0 으로 보고).
- Q2 에 대한 D2 와 이후 enqueue 로 생성된 Q3 에 대한 D3 가 동일 session key 로 동시에 agent
  run 을 트리거 → per-session serialization 위반.
- production caller (agent-runner.ts:1002) 는 runFollowup 을 항상 전달하므로 callback 손실로
  인한 메시지 drop 은 일반 흐름에서는 발생하지 않음. 그러나 관찰 지표/직렬화 invariant 는 깨짐.
- 재현 조건: `/stop` 직후 다음 메시지라는 가장 흔한 사용 시나리오. agent 실행이 수 초 이상
  걸릴 때 자연스럽게 트리거.

## 반증 탐색

- **외부 lock**: `rg "Mutex|Semaphore|AsyncLock"` 매치 0건. queue 보호 락 없음.
- **AbortController**: `rg "AbortController|AbortSignal"` 매치 0건. abort 전파 없음.
- **identity check**: `rg "FOLLOWUP_QUEUES\.get\(key\)\s*===|map\.get\(key\)\s*==="` 매치 0건.
  finally 의 delete 에서 자신의 queue 가 여전히 map 의 entry 인지 확인하는 로직 부재.
- **기존 테스트**: queue.drain-restart.test.ts:236-260 은 callback 이 clearSessionQueues 이후
  유지되지 않음을 검증하나, Q2 orphan 시나리오는 커버하지 않음.
- **주변 코드 맥락**: subagent-announce-queue.ts:62-72 `resetAnnounceQueuesForTests` 주석
  ("Clearing the map alone isn't enough because drain loops capture `queue` by reference")
  에서 maintainer 가 dangling reference 문제를 인지. 그러나 test-reset 경로에만 대응했을 뿐
  production finally 의 identity 보호는 없음.
- **primary-path inversion**: identity check (`FOLLOWUP_QUEUES.get(key) === queue`) 가
  unconditional 하게 finally 에 존재해야 race 가 차단됨. 현재 부재 → race 재현 가능.

## Self-check

### 내가 확실한 근거
- drain.ts:266-267 은 key 만으로 map.delete + callback clear 를 수행. identity check 없음.
- state.ts:80-86 clearFollowupQueue 는 in-place items 비우기 + map.delete. D1 의 array
  reference 는 동일하므로 즉시 length=0 으로 관찰.
- enqueue.ts:60-110 enqueueFollowupRun 은 map 에 key 가 없으면 새 queue 를 만들어 set 하고
  !draining 이면 drain 을 kick. D1 의 await 중에도 D2 가 시작됨.
- status-text.ts:247 은 getFollowupQueueDepth(key) 를 map 조회에 의존.

### 내가 한 가정
- `/stop` 직후 다음 메시지가 들어오는 시나리오가 실제 user 행동에 흔하다고 가정. 빈도 측정
  데이터 없음.
- agent 실행 시간 (effectiveRunFollowup 의 await) 이 수 초~분 단위라고 가정. 짧은 agent run
  일수록 window 가 좁아 race 확률 감소.
- production caller 가 항상 runFollowup 을 enqueue 에 전달한다고 가정. agent-runner.ts:1002
  확인했으나 다른 경로가 있을 수 있음 (allowed_paths 밖).

### 확인 안 한 것 중 영향 가능성
- `drainCollectQueueStep` 경로에서 isCrossChannel 재계산 여부 (별도 FIND-auto-reply-concurrency-002).
- subagent-announce-queue.ts:204 의 동일 패턴 (out-of-scope: src/agents/).
- `refreshQueuedFollowupSession` (state.ts:90-152) 이 drain 의 await 중 호출되었을 때의 영향.
- status-text.ts 이외에 getFollowupQueueDepth 를 사용하는 다른 관찰 지표.
