---
id: FIND-auto-reply-concurrency-002
cell: auto-reply-concurrency
title: isCrossChannel stale 로 await 중 유입된 교차채널 item 이 batch 에 병합되어 오라우팅
file: src/auto-reply/reply/queue/drain.ts
line_range: 159-221
evidence: "```ts\n          const isCrossChannel = hasCrossChannelItems(queue.items,\
  \ resolveCrossChannelKey);\n\n          const collectDrainResult = await drainCollectQueueStep({\n\
  \            collectState,\n            isCrossChannel,\n            items: queue.items,\n\
  \            run: effectiveRunFollowup,\n          });\n          if (collectDrainResult\
  \ === \"empty\") {\n            const summaryOnlyPrompt = previewQueueSummaryPrompt({\
  \ state: queue, noun: \"message\" });\n            const run = queue.lastRun;\n\
  \            if (summaryOnlyPrompt && run) {\n              await effectiveRunFollowup({\n\
  \                prompt: summaryOnlyPrompt,\n                run,\n            \
  \    enqueuedAt: Date.now(),\n              });\n              clearQueueSummaryState(queue);\n\
  \              continue;\n            }\n            break;\n          }\n     \
  \     if (collectDrainResult === \"drained\") {\n            continue;\n       \
  \   }\n\n          const items = queue.items.slice();\n          const summary =\
  \ previewQueueSummaryPrompt({ state: queue, noun: \"message\" });\n          const\
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
  \    enqueuedAt: Date.now(),\n              ...routing,\n            });\n```\n"
symptom_type: concurrency-race
problem: 'collect-mode drain 의 batch 처리 경로 (L185-227) 는 L159 에서 `isCrossChannel` 을
  미리

  계산한 뒤 L161 의 `await drainCollectQueueStep(...)` 에서 microtask 경계를 넘는다.

  `drainCollectQueueStep` 은 `!forceIndividualCollect && !isCrossChannel` 인 경우

  `Promise.resolve("skipped")` 를 동기 반환하지만, `await` 자체가 이벤트 루프에 yield

  하므로 다른 async caller (webhook 핸들러, inbound handler) 가 그 틈에 `enqueueFollowupRun`

  을 실행할 수 있다.


  이후 L185 `queue.items.slice()` snapshot 은 새로 유입된 교차 채널 item 을 포함하지만

  `isCrossChannel` 은 재계산되지 않는다. `splitCollectItemsByAuthorization` 은 authorization

  key 만으로 grouping 하며 채널을 보지 않으므로, 같은 sender/auth 에서 들어온 서로 다른

  채널의 메시지가 하나의 group 으로 뭉친다. `resolveOriginRoutingMetadata` (L50-60) 는

  각 필드 (channel, to, accountId, threadId) 를 **독립적으로** `find` 로 골라 첫 번째 truthy

  값을 반환하므로, 서로 다른 item 에서 뽑힌 (channel=A, to=B의to, thread=C의thread) 같은

  키메라 routing 이 생성되어 잘못된 목적지로 하나의 combined prompt 가 배달된다.

  '
mechanism: "1. Q 에 msg_tg (telegram, chat=100) 와 msg_tg2 (telegram, chat=100) 가 들어\
  \ 있음 — 동일 채널.\n2. D 진입: L159 `hasCrossChannelItems([msg_tg, msg_tg2], ...)` → keys={telegram|100|\"\
  \"|\"\"}\n   size=1 → false. isCrossChannel=false.\n3. L161 `await drainCollectQueueStep({isCrossChannel:false,\
  \ ...})`:\n   drainCollectItemIfNeeded (queue-helpers.ts:167-175) 의 first condition\n\
  \   `!forceIndividualCollect && !isCrossChannel` 만족 → return \"skipped\".\n   async\
  \ 함수라 `Promise.resolve(\"skipped\")` 가 yield 를 유발, microtask 로 넘어감.\n4. microtask\
  \ 경계에서 inbound handler (예: Slack webhook) 가 `enqueueFollowupRun(key, msg_slack,\n\
  \   ...)` 를 실행. state.ts:40 getFollowupQueue 는 map 의 existing Q 를 반환 (Q 는 D 의\n\
  \   drain 중이므로 Q.draining=true). enqueue.ts:96 `queue.items.push(msg_slack)` 으로\
  \ Q.items\n   에 [msg_tg, msg_tg2, msg_slack] 이 됨.\n5. D resume: collectDrainResult=\"\
  skipped\" 가 L161 에서 반환. \"skipped\" 는 L167, L181 의\n   분기 어디에도 매치하지 않아 L185 로 폴스루.\n\
  6. L185 `const items = queue.items.slice()` → [msg_tg, msg_tg2, msg_slack] 스냅샷.\n\
  7. L187 splitCollectItemsByAuthorization: authorization key 기준 grouping.\n   resolveFollowupAuthorizationKey\
  \ (drain.ts:68-81) 는 senderId, senderE164, senderIsOwner,\n   execOverrides, bashElevated\
  \ 만 본다 — 채널/account 는 무시. 세 메시지가 같은 sender 면\n   한 그룹 [[msg_tg, msg_tg2, msg_slack]].\n\
  8. L203-227 for groupItems in authGroups:\n   - L209 `resolveOriginRoutingMetadata(groupItems)`\
  \ (drain.ts:50-60):\n     originatingChannel = items.find(i=>i.originatingChannel)?.originatingChannel\n\
  \     originatingTo      = items.find(i=>i.originatingTo)?.originatingTo\n     originatingAccountId\
  \ = items.find(i=>i.originatingAccountId)?.originatingAccountId\n     originatingThreadId\
  \ = items.find(i=>i.originatingThreadId!=null && i.originatingThreadId!==\"\")?.originatingThreadId\n\
  \     각 필드가 **독립 find** 이므로 서로 다른 item 에서 뽑힐 수 있음.\n   - L210-215 buildCollectPrompt:\
  \ 3개 메시지 모두 포함한 단일 prompt 생성.\n   - L216-221 `await effectiveRunFollowup({prompt,\
  \ run, ...routing})`: routing 의\n     originatingChannel=telegram, originatingTo=chat=100\
  \ (둘 다 telegram 에서 온 값이면\n     다행). 그러나 `slack thread_ts` 가 threadId 로 섞일 수 있음.\n\
  9. 결과: msg_slack 의 내용이 포함된 combined prompt 가 telegram chat 100 에 배달되며,\n   Slack\
  \ 유저에게 응답이 전달되지 않는다. thread routing 혼합도 가능.\n\n현실 가능성: `isCrossChannel=false` 전제에서만\
  \ batch 경로 진입. collect 모드는 multiple\ninbound 가 debounce 창에 모이는 것이 목적이므로, debounce\
  \ 만료 직후의 drain 진입 시점에\n새 메시지가 들어오는 것은 정상 흐름의 일부. microtask window 1 tick 은 매우 짧으나\n\
  webhook 동시 fan-in 환경에서 non-zero.\n"
root_cause_chain:
- why: 왜 isCrossChannel 을 await 이후 재계산하지 않는가?
  because: L159 에서 한 번 계산하고 L161 의 drainCollectQueueStep 에 parameter 로 전달. "skipped"
    분기를 타고 L185 snapshot 으로 진행할 때 isCrossChannel 은 stale. 재계산 지점이 없으며 코드 주석 (L153-158)
    은 "배치가 mixed 되면 다시 collect 하지 않는다" 라는 정반대 방향의 가드만 언급.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:159-161
- why: 왜 snapshot 기반 batch 가 채널 분리를 인지하지 못하는가?
  because: splitCollectItemsByAuthorization (drain.ts:83-110) 은 authorization key
    (sender/ exec/bashElevated 기반) 로만 분리. resolveFollowupAuthorizationKey (L68-81)
    에는 channel/ accountId/to 가 전혀 포함되지 않음. 채널이 섞인 items 가 같은 group 으로 처리됨.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:68-110
- why: 왜 resolveOriginRoutingMetadata 가 키메라 routing 을 만드는가?
  because: L50-60 의 네 필드가 각각 독립적으로 items.find(i => i.field)?.field 로 뽑힘. 첫 번째 truthy
    값이 각 필드별로 서로 다른 item 에서 올 수 있음. 같은 item 에서 모두 뽑도록 강제하는 로직 없음. 예시 — item[0] channel=A
    to=undefined, item[1] channel=B to=Bto → 결과 channel=A to=Bto 같은 조합이 탄생.
  evidence_ref: src/auto-reply/reply/queue/drain.ts:50-60
- why: 왜 upstream 의 최근 fix (712644f0d9) 가 이 경로를 다루지 않았는가?
  because: 712644f0d9 는 `queue.items.splice(0)` → `queue.items.splice(0, groupItems.length)`
    로 다른 group 의 items 가 실수로 삭제되는 문제만 수정. isCrossChannel 재계산 / 채널 기반 splitting / routing
    조합 문제는 커버하지 않음.
  evidence_ref: git:712644f0d9
impact_hypothesis: wrong-output
impact_detail: "정성: 같은 sender (예: 한 명의 owner) 가 서로 다른 채널에서 queue 로 들어오는 경우 (예:\nTelegram\
  \ 에서 여러 메시지 연속 → debounce → 거의 동시에 Slack 에서 하나 추가).\n- 증상: 하나의 combined prompt 가\
  \ 모든 메시지를 포함한 채 \"첫 번째 channel\" 에만 배달.\n  다른 채널 유저는 응답을 받지 못함 (message loss from\
  \ user perspective).\n- 혼합된 threadId 로 wrong thread 로 응답이 갈 수 있음 (예: telegram topic\
  \ id + slack\n  thread_ts 가 섞여 유효하지 않은 combination).\n\n정량: 발생 조건 = (collect 모드\
  \ + 같은 auth key + 서로 다른 채널에서 concurrent inbound).\n- mode=collect 는 whatsapp group,\
  \ discord 서버 등 batch reply 를 원하는 채널 설정에서 사용.\n- debounce 창이 1초 (DEFAULT_QUEUE_DEBOUNCE_MS)\
  \ 이므로 webhook fan-in 실전 시나리오에서\n  race window 는 microtask 1 tick ~ 수 ms.\n- 실제 openclaw\
  \ 사용자가 동시에 여러 채널에 bound 된 \"same owner\" 로 메시지를 보낼 가능성은\n  edge case 에 가까움. severity\
  \ P2 ~ P3.\n"
severity: P2
counter_evidence:
  path: src/auto-reply/reply/queue/drain.ts
  line: 159-221
  reason: "R-3 / R-5 / R-7 에 따른 방어 탐색:\n\n1) `rg -n \"Mutex|Semaphore|AsyncLock|acquire|release\"\
    \ src/auto-reply/` → 매치 0건.\n2) `rg -n \"isCrossChannel\" src/auto-reply/reply/queue/drain.ts`\
    \ → L159, L163 두 곳만.\n   재계산 로직 없음.\n3) `rg -n \"hasCrossChannelItems\" src/auto-reply/reply/queue/drain.ts`\
    \ → L159 1곳.\n   await 이후 재호출 없음.\n4) splitCollectItemsByAuthorization 의 키 구성\
    \ 확인 (drain.ts:68-81):\n   senderId, senderE164, senderIsOwner, execOverrides.*,\
    \ bashElevated.* 만 포함.\n   channel/to/accountId 없음. 채널 기반 분리 없음.\n5) resolveOriginRoutingMetadata\
    \ 의 field 독립 find 확인 (drain.ts:50-60): 주석에는\n   \"Support both number (Telegram\
    \ topic) and string (Slack thread_ts)\" 이 언급되어 서로\n   다른 채널의 thread id 가 섞일 수\
    \ 있음을 암묵적으로 인정.\n6) 기존 테스트: `rg -n \"resolveOriginRoutingMetadata|isCrossChannel.*race\"\
    \ src/auto-reply/`\n   → 매치 없음 (race 검증 테스트 부재).\n\nR-5 실행 조건 분류:\n| 경로 | 조건 |\
    \ 영향 |\n|---|---|---|\n| L159 isCrossChannel 계산 | drain loop 진입마다 unconditional\
    \ | 초기값만 계산 |\n| L161 await yield | unconditional (async fn) | microtask boundary\
    \ 생성 |\n| L185 snapshot | unconditional 폴스루 | stale isCrossChannel 로 batch 진입\
    \ |\n| L209 resolveOriginRoutingMetadata | groupItems 각 group 마다 unconditional\
    \ | chimera routing 생성 |\n| 채널 기반 재분리 | **부재** | mixed-channel group 방치 |\n\n\
    R-7 hot-path: collect mode 는 group chat / batch 원하는 설정에서 enabled. inbound webhook\n\
    핸들러는 async 이며 event loop 에 microtask 를 enqueue 한다. race window 는 microtask\n1\
    \ tick 으로 좁으나 production 에서 완전 배제 불가.\n\nprimary-path inversion: \"이 chimera routing\
    \ 이 재현되려면 어떤 guard 가 우회돼야 하는가?\"\n→ isCrossChannel 재계산 OR splitCollectItemsByAuthorization\
    \ 에 채널 포함 OR\nresolveOriginRoutingMetadata 가 단일 item 을 택. 세 경로 모두 부재.\n\nJS single-threaded\
    \ 제약 확인: race 는 L161 await 의 microtask yield 에서만 성립. 순수 sync\n구간에서는 발생 불가. 재현\
    \ 조건 = 외부 async caller (webhook handler) 가 동시에 enqueue.\n"
status: discovered
discovered_by: concurrency-auditor
discovered_at: 2026-04-19
cross_refs:
- FIND-auto-reply-concurrency-001
domain_notes_ref: domain-notes/auto-reply.md
related_tests:
- src/auto-reply/reply/reply-flow.test.ts
- src/auto-reply/reply.triggers.trigger-handling.targets-active-session-native-stop.e2e.test.ts
---
# isCrossChannel stale 로 await 중 유입된 교차채널 item 이 batch 에 병합되어 오라우팅

## 문제

collect mode drain 이 L159 에서 isCrossChannel 을 스냅샷으로 계산한 뒤 L161 의
`await drainCollectQueueStep(...)` 에서 microtask yield 를 유발한다. 그 사이 외부 async
caller (webhook 등) 가 `enqueueFollowupRun` 으로 다른 채널의 메시지를 Q 에 push 하면,
L185 snapshot 은 새 item 을 포함하지만 isCrossChannel 은 재계산되지 않는다. 결과로
cross-channel items 가 batch collect 경로로 진입하여 하나의 combined prompt 로 합쳐지며,
`resolveOriginRoutingMetadata` 의 필드별 독립 `find` 로 인해 키메라 routing 이 생성되어
잘못된 채널/스레드로 응답이 전달된다.

## 발현 메커니즘

위 frontmatter `mechanism` 참조 (단계 1-9).

핵심 요약:
- `await drainCollectQueueStep` 이 "skipped" 를 즉시 반환하더라도 async function 이므로
  microtask 경계 생성.
- 외부 inbound handler 가 그 틈에 cross-channel item 을 enqueue.
- L185 snapshot 는 새 item 포함, isCrossChannel 은 stale (false).
- splitCollectItemsByAuthorization 은 authorization key 만 봄 (채널 무시).
- resolveOriginRoutingMetadata 는 각 필드를 독립 `find` → 서로 다른 item 에서 뽑힘 가능.

## 근본 원인 분석

1. drain.ts:159 의 isCrossChannel 계산이 L161 await 이후 재실행되지 않음. `while` loop 는
   다음 iteration 에서 다시 계산하지만, 현재 iteration 의 L185 batch 경로는 stale 값에
   의존.
2. resolveFollowupAuthorizationKey (drain.ts:68-81) 에 channel/to/accountId 가 포함되지 않아
   sender 가 같으면 다른 채널 items 도 같은 group 으로 합쳐짐.
3. resolveOriginRoutingMetadata (drain.ts:50-60) 가 네 필드를 독립 find 로 뽑음. 같은 item
   에서 모두 뽑도록 강제하는 항등성 없음.
4. 기존 테스트 (reply-flow.test.ts, targets-active-session-native-stop.e2e.test.ts) 는
   일반 cross-channel 시나리오를 cover 하나 await boundary race 는 시뮬레이션하지 않음.
5. upstream 최근 fix (712644f0d9) 는 splice(0) → splice(0, groupItems.length) 로 다른 group
   삭제 문제만 수정. 본 race 는 별개 경로.

## 영향

- **impact_hypothesis: wrong-output** — cross-channel 메시지가 wrong channel 로 배달되거나
  mixed thread 로 전달.
- 재현 조건: (a) mode=collect, (b) 동일 sender 가 서로 다른 채널에 bound, (c) debounce 만료
  직후 drain 진입 시점에 다른 채널에서 inbound webhook.
- 재현 window: microtask 1 tick ~ 수 ms. 매우 좁으나 고빈도 webhook fan-in 환경에서
  non-zero.
- severity P2: edge case 이나 관찰 시 완전한 silent message loss (유저가 반응 없다고 인식)
  이며 원인 진단이 어려움.

## 반증 탐색

- **외부 lock / Abort**: `rg "Mutex|Semaphore|AbortController"` src/auto-reply/reply/queue →
  0건. 방어 락/취소 없음.
- **isCrossChannel 재계산**: `rg "hasCrossChannelItems" src/auto-reply/reply/queue/drain.ts` →
  L159 1곳. await 이후 재확인 경로 부재.
- **auth key 에 channel 포함 여부**: drain.ts:68-81 확인. 없음.
- **기존 테스트**: reply-flow.test.ts 및 e2e 테스트는 주로 "의도된 cross-channel" 케이스를
  검증. race 유발 interleaving 부재.
- **주변 주석**: L153-158 주석은 "once the batch is mixed, never collect again within this
  drain" 이라는 반대 방향 가드만 언급. 본 race 를 인지하지 못함.
- **primary-path inversion**: isCrossChannel 재계산 OR 채널 기반 group 분리 OR routing
  항등성 중 하나가 unconditional 하게 적용돼야 race 차단. 세 경로 모두 부재.

## Self-check

### 내가 확실한 근거
- drain.ts:159 의 isCrossChannel 은 L161 await 이전 한 번만 계산.
- drain.ts:161 `await drainCollectQueueStep(...)` 은 async function 이므로 skipped path
  에서도 microtask yield 발생 (JS 명세).
- drain.ts:68-81 resolveFollowupAuthorizationKey 에 channel/to/accountId 없음.
- drain.ts:50-60 resolveOriginRoutingMetadata 의 네 필드는 각자 독립 find.
- 재계산 / 채널 분리 / routing 항등성 guard 부재 (rg 확인).

### 내가 한 가정
- 동일 sender 가 서로 다른 채널 계정에 bound 되어 있는 사용자 환경이 실제로 존재한다고
  가정. openclaw 사용자에게는 흔하지 않을 수 있음.
- webhook 핸들러가 drain 과 동시에 event loop 에 async task 를 넣는 상황이 발생한다고 가정.
  single-process 에서 production 빈도 측정 없음.
- `drainCollectQueueStep` 의 "skipped" path 가 실제로 microtask 를 소비한다고 가정 (async
  function 명세상 그러함).

### 확인 안 한 것 중 영향 가능성
- `forceIndividualCollect` 가 true 가 된 이후 iteration 에서 이 race 가 얼마나 완화되는지.
- collect mode 가 활성화되는 채널 설정의 실사용 분포.
- `resolveFollowupAuthorizationKey` 가 channel/to/accountId 를 의도적으로 제외한 설계 의도
  (예: 동일 사용자의 cross-channel dedupe) — 의도된 설계라면 본 FIND 의 "splitting 에 채널
  포함" 제안은 회귀 유발 가능.
- 실제 production 에서 wrong-channel delivery 가 observed 되었는지 (버그 리포트 검색 필요).
