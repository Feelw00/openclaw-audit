---
id: FIND-agents-registry-concurrency-002
cell: agents-registry-concurrency
title: 'completeSubagentRun: browser-session cleanup outside atomic guard'
file: src/agents/subagent-registry-lifecycle.ts
line_range: 635-644
evidence: "```ts\n    if (!completeParams.triggerCleanup || suppressedForSteerRestart)\
  \ {\n      return;\n    }\n\n    await (params.cleanupBrowserSessionsForLifecycleEnd\
  \ ?? cleanupBrowserSessionsForLifecycleEnd)({\n      sessionKeys: [entry.childSessionKey],\n\
  \      onWarn: (msg) => params.warn(msg, { runId: entry.runId }),\n    });\n\n \
  \   startSubagentAnnounceCleanupFlow(completeParams.runId, entry);\n```\n"
symptom_type: concurrency-race
problem: completeSubagentRun 가 listener (subagent-registry.ts:659-667) 와 waitForSubagentCompletion
  (subagent-registry-run-manager.ts:119-128) 두 경로에서 병행 호출될 수 있다. 두 호출 모두 line 639
  의 cleanupBrowserSessionsForLifecycleEnd 를 통과하지만, 이 호출은 beginSubagentCleanup 원자 가드
  (line 644의 startSubagentAnnounceCleanupFlow 안에서만 적용) 바깥에 위치한다. 결과적으로 동일 childSessionKey
  에 대한 브라우저 세션 정리 요청이 2회 발사된다.
mechanism: "T0  subagentRunManager.registerSubagentRun 이 ensureListener() + waitForSubagentCompletion\
  \ 을 모두 기동 (run-manager.ts:348-354). 두 경로가 동시에 활성.\nT1  child subagent 가 종료 → gateway\
  \ agent-event 로 phase='end' lifecycle 이벤트 발사.\nT2  listener (registry.ts:617-669)\
  \ 의 async IIFE 가 사건 접수:\n     - line 623: entry = subagentRuns.get(runId) (OK)\n\
  \     - line 655: clearPendingLifecycleError sync\n     - line 659: await completeSubagentRun({runId,\
  \ outcome:\"ok\", reason:COMPLETE, triggerCleanup:true})\nT3  동시에 waitForAgentRun\
  \ 이 resolve 되어 waitForSubagentCompletion (run-manager.ts:75-132) 이 line 119-128\
  \ 에서 `await params.completeSubagentRun({runId, outcome, reason, triggerCleanup:true})`\
  \ 호출.\nT4  두 completeSubagentRun 호출이 병행 진행 (둘 다 async):\n     - A (listener): lifecycle.ts:550\
  \ clearPending sync, :551 entry, :557-582 idempotent 필드 mutate, :584 `await freezeRunResultAtCompletion(entry)`\
  \ yield.\n     - B (waitForCompletion): 같은 sync 프리픽스, :584 에서 yield.\nT5  둘 다 :584\
  \ await 에서 yield 후 재개. 둘 다 :597 `await persistSubagentSessionTiming` 통과 (내부 try/catch).\n\
  T6  둘 다 :626-633 `emitSubagentEndedHookForRun` 호출. 첫 호출이 endedHookEmittedAt 를 set\
  \ → 두 번째는 no-op. 훅은 1회 발사 (48042c3875 가드 정상 동작).\nT7  둘 다 :635 `if (!completeParams.triggerCleanup\
  \ || suppressedForSteerRestart) return;` 통과 (둘 다 triggerCleanup:true).\nT8  둘 다\
  \ :639 `await cleanupBrowserSessionsForLifecycleEnd({sessionKeys:[entry.childSessionKey],\
  \ ...})` 호출. **동일 childSessionKey 에 대한 브라우저 세션 정리가 2회 발사**. \nT9  둘 다 :644 `startSubagentAnnounceCleanupFlow(runId,\
  \ entry)` 호출.\n     - 내부 beginSubagentCleanup(runId) (lifecycle.ts:476 또는 492) 는\
  \ 첫 호출에서 cleanupHandled=false → true 로 atomic set 후 true 반환. 둘째 호출은 cleanupHandled=true\
  \ 감지해 false 반환 → 중복 announce cleanup 차단.\n     - 단, cleanupBrowserSessionsForLifecycleEnd\
  \ 는 이미 2회 호출 완료 후.\n"
root_cause_chain:
- why: 왜 동일 runId 에 대한 completeSubagentRun 이 2회 호출 가능한가
  because: registerSubagentRun 이 run-manager.ts:348 `params.ensureListener()` 와 line
    354 `void waitForSubagentCompletion(runId, waitTimeoutMs)` 를 모두 기동한다. listener
    는 in-process agent-event 콜백, waitForSubagentCompletion 은 gateway RPC 폴링 기반. 주석
    (run-manager.ts:352-353 "Wait for subagent completion via gateway RPC (cross-process).
    The in-process lifecycle listener is a fallback for embedded runs.") 가 양쪽을 "fallback"
    으로 기술하지만 코드상 dedupe 가 없어 임베디드 run 에서 gateway RPC 와 in-process lifecycle 둘 다 완료
    신호를 전달하면 두 경로가 **동시에** completeSubagentRun 을 호출한다.
  evidence_ref: src/agents/subagent-registry-run-manager.ts:348-354
- why: 왜 cleanupBrowserSessionsForLifecycleEnd 호출이 beginSubagentCleanup 보호 밖에 있는가
  because: completeSubagentRun 의 순서가 lifecycle.ts:639 cleanupBrowserSessions 호출 →
    :644 startSubagentAnnounceCleanupFlow 호출 순이다. beginSubagentCleanup 는 startSubagentAnnounceCleanupFlow
    내부 (lifecycle.ts:476, 492) 에서 호출되므로 :639 cleanupBrowserSessions 시점에는 cleanupHandled
    플래그가 아직 false. 두 동시 호출자가 모두 cleanupBrowserSessions 를 통과한 뒤에야 :644 에서 beginSubagentCleanup
    가드가 걸린다.
  evidence_ref: src/agents/subagent-registry-lifecycle.ts:635-644
- why: 왜 이 순서인가 (설계 의도)
  because: browser 세션 정리는 announce cleanup flow 와 독립된 리소스 해제라 announce-cleanup 가드
    바깥에 배치한 듯하다. 그러나 이는 single-call 가정을 전제한 설계 — 동일 runId 에 대한 두 완료 경로가 함께 활성화되는 케이스
    (임베디드 + gateway 폴링) 를 고려하지 않는다.
  evidence_ref: src/agents/subagent-registry-run-manager.ts:352-353
- why: 왜 gateway 와 listener 둘 다 같은 runId 를 처리하는가
  because: waitForAgentRun (run-manager.ts:77-81) 은 gateway 측 agent-run state 를 폴링.
    in-process listener (registry.ts:617) 는 local agent-event emitter 구독. 임베디드 (in-process)
    subagent 실행 시 동일 프로세스에서 이벤트가 발행되고 gateway 를 통한 RPC 응답도 resolve 된다. 주석상 fallback
    이지만 실제로는 양쪽 다 fire.
  evidence_ref: src/agents/subagent-registry-run-manager.ts:75-132
impact_hypothesis: wrong-output
impact_detail: '정성적: cleanupBrowserSessionsForLifecycleEnd 가 동일 childSessionKey 에
  대해 2회 발사된다. browser-lifecycle-cleanup 구현체 (openclaw core, out-of-scope) 가 idempotent
  여도 2회 I/O 는 CPU/FS 낭비. 만일 idempotent 가 아니면 로그 warn 중복, 세션 메타데이터 삭제 실패 warn, 또는 측정
  메트릭 이중 카운트.


  재현 조건:

  - 임베디드 subagent run (in-process) 실행.

  - registerSubagentRun 이 ensureListener + waitForSubagentCompletion 둘 다 기동.

  - subagent 종료 시 in-process agent-event phase=''end'' 와 gateway RPC resolve 가 거의
  동시.

  - 두 경로 모두 completeSubagentRun 호출 → line 639 2회 통과.


  빈도: 임베디드 run 종료 시마다 발생할 가능성. 다만 gateway 폴링 간격에 따라 한 쪽이 먼저 entry 를 터치하고 sync 프리픽스에서
  completeCleanupBookkeeping 가 완료되면 다른 쪽은 entry.endedAt!==undefined 등의 guard 로 차단될
  수 있다. 단 :571 `if (entry.endedAt !== endedAt)` 는 값이 다르면 overwrite 로 이어지며, :639 자체의
  가드는 없다.


  severity: P2 (브라우저 cleanup 이중 dispatch — idempotency 는 구현 의존).

  '
severity: P2
counter_evidence:
  path: src/agents/subagent-registry-lifecycle.ts
  line: 476-493
  reason: "Primary-path inversion (CAL-001):\n이 race 가 재현되려면 completeSubagentRun 진입부\
    \ 또는 line 635-639 사이에 unconditional idempotency 가드가 없어야 한다. 실제 진입부 (lifecycle.ts:541-567)\
    \ 는 `suppressAnnounceReason === \"killed\" && (cleanupHandled || cleanupCompletedAt)`\
    \ 분기 (557-567) 만 있고, 일반 경로에서는 entry.endedAt 같은 플래그로 조기 return 하지 않는다.\n\n`beginSubagentCleanup`\
    \ (lifecycle.ts:280-291) 이 atomic 가드 역할을 한다:\n```\nif (entry.cleanupCompletedAt\
    \ || entry.cleanupHandled) return false;\nentry.cleanupHandled = true;\n```\n\
    sync 동작이라 check-then-set 원자적. 그러나 이 가드는 **startSubagentAnnounceCleanupFlow** (lifecycle.ts:476,\
    \ 492) 와 **retryDeferredCompletedAnnounces** (lifecycle.ts:310) 에서만 호출된다. completeSubagentRun\
    \ 진입부와 cleanupBrowserSessionsForLifecycleEnd 호출(lifecycle.ts:639) 사이에는 **호출되지\
    \ 않는다**.\n\nR-3 Grep 결과:\n- rg -n \"Mutex|Semaphore|AsyncLock|acquire|release\"\
    \ src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts\n  →\
    \ 외부 lock 부재 확인.\n- rg -n \"cleanupBrowserSessionsForLifecycleEnd\" src/agents/\n\
    \  → subagent-registry.ts:1, 86 (import/deps), subagent-registry-lifecycle.ts:639\
    \ (호출). 호출 측 idempotency check 없음.\n- rg -n \"beginSubagentCleanup\" src/agents/subagent-registry*.ts\n\
    \  → lifecycle.ts:280 (정의), 310 (retryDeferredCompletedAnnounces 선행), 476, 492\
    \ (startSubagentAnnounceCleanupFlow 내부). completeSubagentRun 본체 (541-645) 에서는\
    \ 직접 호출 안 함.\n- rg -n \"completeSubagentRun\" src/agents/\n  → 호출 지점: registry.ts:257,\
    \ 659, run-manager.ts:119, 692. listener (:659) 와 run-manager:119 (waitForSubagentCompletion)\
    \ 둘 다 triggerCleanup:true 전달.\n\n실행 조건 분류 표 (R-5):\n| 호출 지점 | runId entry idempotency\
    \ guard | browser cleanup before guard? | 분류 |\n|---|---|---|---|\n| completeSubagentRun\
    \ 진입부 (lifecycle.ts:541-567) | killed 분기만 제한적 | n/a | conditional-edge guard |\n\
    | cleanupBrowserSessionsForLifecycleEnd (line 639) | 없음 | YES | unguarded |\n\
    | startSubagentAnnounceCleanupFlow (line 644) | beginSubagentCleanup | 가드 후 실행\
    \ | unconditional guard |\n| emitSubagentEndedHookForRun (line 627) | endedHookEmittedAt\
    \ | 가드 | unconditional guard (48042c3875) |\n\ncleanupBrowserSessionsForLifecycleEnd\
    \ 는 line 644 의 unconditional guard 보다 **앞서** 실행되므로 두 동시 호출자 모두 통과. 이것이 race 성립의\
    \ 정확한 지점.\n\nHot-path vs test-path (R-7):\n- registerSubagentRun (run-manager.ts:260-355)\
    \ 는 호출 시 ensureListener + waitForSubagentCompletion 양쪽 기동 (line 348, 354). Production\
    \ 경로.\n- listener 의 phase='end' 처리 (registry.ts:655-667) 는 local agent-event emitter\
    \ 구독이며 임베디드 run 에서 trigger.\n- waitForSubagentCompletion 의 gateway RPC (run-manager.ts:77-81)\
    \ 는 cross-process 관찰용. 임베디드 run 에서는 gateway 가 local 에 있어도 RPC 반환.\n- 두 경로 모두 hot-path\
    \ 이며 임베디드 실행 시 동시 활성. 테스트 (subagent-registry.test.ts) 는 listener 와 waitForAgentRun\
    \ 을 개별 mock 하므로 현재 이 race 는 test 에서 재현되지 않는다.\n\n**Hot-path 일치 확인**. 테스트-only\
    \ 재현이 아니며 production 임베디드 run 에서 실제 가능. severity P2.\n\n기존 테스트 커버리지:\n- `rg -n\
    \ \"completeSubagentRun\" src/agents/*.test.ts` → lifecycle.test.ts 가 controller\
    \ 내부 completeSubagentRun 를 단독 호출하지만 listener + waitForCompletion 동시 호출 시나리오는 커버\
    \ 없음.\n- cleanupBrowserSessionsForLifecycleEnd mock 은 lifecycle.test.ts:114 에서\
    \ vi.fn 으로 주입되며 호출 횟수 단언은 없다.\n\nAbortController/event ordering:\n- waitForAgentRun\
    \ 은 AbortSignal 을 받지 않음 (run-manager.ts:77-81).\n- listener 는 unsubscribe 전 event\
    \ drain 보장 없음.\n- `runOutcomesEqual` (completion.ts:13-30) 로 sync 필드 중복 감지 → mutated=false\
    \ 만 처리. 그러나 browser cleanup 은 mutated 와 무관하게 항상 실행.\n"
discovered_by: concurrency-auditor
discovered_at: '2026-04-19'
related_tests:
- src/agents/subagent-registry.test.ts
- src/agents/subagent-registry-lifecycle.test.ts
status: discovered
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# completeSubagentRun 의 cleanupBrowserSessionsForLifecycleEnd 호출이 listener / waitForSubagentCompletion 경로에서 beginSubagentCleanup 가드 바깥에 놓여 동일 childSessionKey 에 대해 중복 호출된다

## 문제

`subagent-registry-run-manager.ts:260-355` 의 `registerSubagentRun` 이 `ensureListener()` (line 348) 과 `waitForSubagentCompletion(runId, waitTimeoutMs)` (line 354) 를 둘 다 기동한다. 이 두 경로는 주석(line 352-353)상 "gateway RPC 주, in-process lifecycle 은 fallback" 관계이지만 실제로는 dedupe 가 없다. 임베디드 subagent 완료 시 양 경로가 모두 `completeSubagentRun` 을 호출한다.

`completeSubagentRun` (lifecycle.ts:541-645) 은 진입부에서 `entry.endedAt` / `cleanupHandled` 같은 atomic guard 로 조기 return 하지 않는다. 두 병행 호출이 모두 line 639 의 `await cleanupBrowserSessionsForLifecycleEnd({sessionKeys:[entry.childSessionKey], ...})` 까지 진행한다. 원자 가드 `beginSubagentCleanup` 는 line 644 의 `startSubagentAnnounceCleanupFlow` 내부에서만 호출되므로 browser cleanup 이중 발사를 막지 못한다.

## 발현 메커니즘

1. `registerSubagentRun(runId=X)` 호출 → `ensureListener()` + `void waitForSubagentCompletion(X, timeoutMs)` 둘 다 활성.
2. 임베디드 subagent X 가 종료 → `onAgentEvent` 가 phase='end' 이벤트 발사 AND gateway agent-run state 업데이트.
3. Listener IIFE (registry.ts:618-668):
   - `const entry = subagentRuns.get(X)` (line 623) → entry.
   - phase='end' → `clearPendingLifecycleError` sync → `await completeSubagentRun({runId:X, outcome:{status:"ok"}, reason:COMPLETE, triggerCleanup:true})` (line 659).
4. 동시에 waitForSubagentCompletion 내부 (run-manager.ts:75-132):
   - `await waitForAgentRun(...)` resolve → entry lookup → idempotent mutate → `await params.completeSubagentRun({runId:X, outcome, reason, triggerCleanup:true})` (line 119).
5. 두 completeSubagentRun 호출이 병행 진행:
   - 진입부 line 541-567: suppressAnnounceReason!=="killed" 이라 killed 분기 skip.
   - line 569-582: entry.endedAt/outcome/endedReason 을 set (양쪽 모두 같은 값이면 mutated=false, 다르면 last-writer-wins overwrite).
   - line 584 `await freezeRunResultAtCompletion(entry)` → 양쪽 yield.
   - line 591 `safeFinalizeSubagentTaskRun({entry, outcome})` sync.
   - line 596-604 `await persistSubagentSessionTiming(entry)` → 양쪽 yield.
   - line 626-633 `await params.emitSubagentEndedHookForRun({entry, reason, ...})`:
     - 첫 호출: `emitSubagentEndedHookForRun` (registry.ts:321-349) 가 endedHookEmittedAt 체크 → false → 진행.
     - 내부 `emitSubagentEndedHookOnce` (completion.ts:58-98) 의 inFlightRunIds guard 가 작동.
     - 첫 호출 성공 시 endedHookEmittedAt 세팅 (completion.ts:91).
     - 둘째 호출은 endedHookEmittedAt 세팅된 것을 감지 → no-op.
   - line 635 `if (!triggerCleanup || suppressedForSteerRestart) return;` → 둘 다 triggerCleanup:true 라 통과.
   - line 639 `await cleanupBrowserSessionsForLifecycleEnd({sessionKeys:[entry.childSessionKey]})` → **둘 다 실행. 동일 childSessionKey 에 대한 브라우저 세션 정리가 2회 dispatch**.
   - line 644 `startSubagentAnnounceCleanupFlow(runId, entry)` → 내부 beginSubagentCleanup 가드가 둘째를 false 로 차단하지만 browser cleanup 은 이미 2회 발사 완료.

## 근본 원인 분석

1. **이벤트 소스 중복**: `registerSubagentRun` 이 in-process listener 와 gateway RPC 폴링 둘 다 활성화. 주석상 "fallback" 설계지만 dedupe 가 호출 시점에 없다.
2. **원자 가드 배치 불일치**: `beginSubagentCleanup` 은 announce cleanup flow (startSubagentAnnounceCleanupFlow 내부) 에서만 호출. completeSubagentRun 진입부 또는 cleanupBrowserSessionsForLifecycleEnd 호출 전에 없어 browser cleanup 이중 dispatch.
3. **triggerCleanup 플래그의 의미 범위**: triggerCleanup:true 는 announce/cleanup 전체를 의미하지만 실제로는 browser cleanup 까지 포함한다. beginSubagentCleanup 이 cleanupHandled 를 set 하는 시점이 너무 늦다.
4. **runOutcomesEqual 의 부분적 dedupe**: line 575 의 `runOutcomesEqual` 체크는 필드 mutate 만 dedupe 하며 후속 cleanup 액션은 영향 주지 않는다.

## 영향

- **브라우저 세션 정리 중복 dispatch**: cleanupBrowserSessionsForLifecycleEnd 이 동일 childSessionKey 에 대해 2회 호출. browser-lifecycle-cleanup 구현이 idempotent 여도 2회 I/O. 만일 non-idempotent 면:
  - warn 로그 중복 (onWarn 콜백 2회 fire).
  - 세션 추적 메트릭 이중 감산.
  - BrowserSession 해제 후 재해제 시 "already released" 류 warn 노이즈.
- **CPU 낭비**: completeSubagentRun 자체의 full-path 실행이 2회 (persistSubagentSessionTiming 2회, freezeRunResultAtCompletion 2회).
- **순서 뒤바뀜 위험**: line 571 `if (entry.endedAt !== endedAt) entry.endedAt = endedAt` 는 동일 runId 에 대한 두 호출이 서로 다른 `endedAt` 값을 가지면 last-writer-wins 로 덮어쓴다. listener 의 `evt.data?.endedAt` 과 waitForAgentRun 의 `wait.endedAt` 은 출처가 달라 값이 미세하게 다를 수 있다.

재현 시나리오:
```
// registerSubagentRun("runX", ...)
// 1. ensureListener 기동 (in-process)
// 2. waitForSubagentCompletion 기동 (gateway RPC 폴링)
// 3. runX 임베디드 완료 → phase='end' 발행
// 4. listener IIFE + waitForAgentRun 둘 다 resolve
// 5. 두 경로가 completeSubagentRun 호출 → line 639 2회 통과
```

## 반증 탐색

### Primary-path inversion (CAL-001 필수)

이 race 가 성립하려면 completeSubagentRun 본체에서 line 639 이전에 동시 진입 차단 가드가 없어야 한다.

- 진입부 (541-567): suppressAnnounceReason==="killed" 조건 한정 분기만 있고 일반 경로 early-return 없음.
- 569-582: 필드 mutate idempotent. mutated 플래그는 persist 여부만 결정, 후속 browser cleanup 에 영향 없음.
- 584 freezeRunResultAtCompletion: state 변경 가능하나 dedupe 가드 아님.
- 596-604 persistSubagentSessionTiming: 외부 I/O, idempotency 미검증.
- 626-633 emitSubagentEndedHookForRun: endedHookEmittedAt 가드 **있음** — 훅은 1회만. 그러나 cleanupBrowserSessions 는 **그 다음** 실행.
- 635 triggerCleanup 조건: 둘 다 true 전달받으므로 통과.
- **639 cleanupBrowserSessionsForLifecycleEnd: 가드 없음** ← 핵심 race point.
- 644 startSubagentAnnounceCleanupFlow → beginSubagentCleanup: 가드 **있음** — 둘째 호출 차단.

**line 639 은 unconditional guard 바깥**. race 성립.

### R-3 Grep 결과 (명령 + 결과)

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → subagent-registry.ts:776 releaseSubagentRun (semantic), run-manager.ts:357/481 (run cleanup). lock primitive match 없음.

rg -n "cleanupBrowserSessionsForLifecycleEnd" src/agents/
  → subagent-registry.ts:1 (import), 86 (defaultDeps), 366 (passthrough), subagent-registry-lifecycle.ts:639 (호출).
  → 호출 측 idempotency flag/Set/Map 없음.

rg -n "beginSubagentCleanup" src/agents/subagent-registry*.ts
  → lifecycle.ts:280 정의 (entry.cleanupCompletedAt || entry.cleanupHandled 체크 후 cleanupHandled=true), 310 (retryDeferredCompletedAnnounces 선행 가드), 476/492 (startSubagentAnnounceCleanupFlow 내부).
  → completeSubagentRun 본체 (541-645) 에서는 직접 호출 **안 함**.

rg -n "completeSubagentRun" src/agents/
  → registry.ts:257 (timer callback), 659 (listener phase='end'), run-manager.ts:119 (waitForSubagentCompletion), 692 (deps export). 호출 지점 4곳 중 listener/runManager 가 동시 활성 가능.

rg -n "waitForSubagentCompletion|ensureListener\\(\\)" src/agents/subagent-registry-run-manager.ts
  → line 75 정의, 256 (replaceSubagentRunAfterSteer), 354 (registerSubagentRun), 483 (export). registerSubagentRun 의 line 348 ensureListener + 354 waitForSubagentCompletion 가 pair.

rg -n "Promise\\.race\\(|Promise\\.all\\(|Promise\\.allSettled\\(" src/agents/subagent-registry*.ts
  → match 없음.
```

### R-5 실행 조건 분류 표

| 위치 | 작용 | 가드 | 실행 조건 |
|---|---|---|---|
| completeSubagentRun 진입 (:541) | 함수 시작 | killed 분기만 | unconditional 진입 가드 없음 |
| emitSubagentEndedHookForRun (:627) | 훅 호출 | endedHookEmittedAt | **unconditional** (inFlightRunIds + persisted flag) |
| cleanupBrowserSessionsForLifecycleEnd (:639) | 브라우저 세션 정리 | **없음** | **unguarded race window** |
| startSubagentAnnounceCleanupFlow (:644) | announce cleanup | beginSubagentCleanup | **unconditional** (cleanupHandled) |

**line 639 단독으로 unconditional 가드 바깥**.

### R-7 Hot-path vs test-path

- Production: `registerSubagentRun` 이 hot path. 임베디드 run 기본 설정. listener + waitForSubagentCompletion 둘 다 기동.
- Test: `subagent-registry.test.ts` 는 cleanupBrowserSessionsForLifecycleEnd 를 `vi.fn(async () => {})` 로 mock (lifecycle.test.ts:114). 호출 횟수 단언 없어 중복 호출 미감지.
- listener + waitForAgentRun 동시 fire 시나리오 테스트 부재 확인: `rg -n "ensureListener\\(\\).*waitForSubagentCompletion" src/agents/*.test.ts` → match 없음. 테스트는 한 경로씩만 검증.

**Production hot-path 상 실제 double-dispatch 가능**. test-only synthetic 재현 아님. severity P2.

### 추가 탐색

- **AbortController 전파**: waitForSubagentCompletion 은 AbortSignal 을 받지 않는다 (run-manager.ts:75-81). gateway RPC 가 완료를 reject 해도 listener 는 독립적으로 계속 반응.
- **배포 토폴로지**: subagentRuns 는 프로세스-로컬 Map. 단일 프로세스 내에서 listener + polling 중복 dispatch.
- **48042c3875 의 영향 범위**: 그 fix 는 `emitSubagentEndedHookForRun` 의 endedHookEmittedAt guard 를 추가했지만 cleanupBrowserSessionsForLifecycleEnd 는 건드리지 않음. 같은 종류의 race (동일 runId 에 대한 두 완료 경로) 이지만 hook 만 보호되었다.
- **upstream/main** (54cf4cd857) 에도 동일 패턴. 미해결.
- **runOutcomesEqual 의 의미**: mutated 만 결정. cleanup 액션 short-circuit 안 함.

## Self-check

### 내가 확실한 근거

- `registerSubagentRun` 이 listener + waitForSubagentCompletion 둘 다 기동 (run-manager.ts:348, 354, Read 확인).
- `completeSubagentRun` 진입부 (lifecycle.ts:541-567) 에 일반 경로 early-return 가드 부재 (Read 확인).
- `cleanupBrowserSessionsForLifecycleEnd` 호출 (lifecycle.ts:639) 이 beginSubagentCleanup 보다 먼저 실행 (Read 확인 line 639 vs 644).
- beginSubagentCleanup 은 startSubagentAnnounceCleanupFlow 내부 (lifecycle.ts:476, 492) 에서만 호출 (Grep 확인).
- 48042c3875 는 endedHookEmittedAt 가드만 추가, browser cleanup 은 건드리지 않음 (`git show 48042c3875` 확인).

### 내가 한 가정

- listener 의 phase='end' 이벤트와 waitForAgentRun 의 gateway RPC resolve 가 거의 동시 발생한다 (임베디드 run 에서 양쪽 모두 local process 참조). 실제 타이밍 차이는 수 ms 로 추정.
- browser-lifecycle-cleanup 모듈 (out-of-scope) 이 non-idempotent 일 가능성. 실제 구현 미검증.
- waitForAgentRun 이 임베디드 run 에서도 실제로 resolve 된다 (gateway 가 local 이라도 RPC path 타는 경우). run-manager 주석 "gateway RPC (cross-process)" 를 일부러 fallback 이 아닌 primary 로 설계했다는 단서와 일관.

### 확인 안 한 것 중 영향 가능성

- `browser-lifecycle-cleanup` (out-of-scope) 의 실제 구현 idempotency 미검증. non-idempotent 면 severity P1 까지 상향 가능.
- gateway 설정에 따라 waitForAgentRun 가 실제로 resolve 되는 조건 (crosss-process vs embedded) 상세 미확인.
- `persistSubagentSessionTiming` (lifecycle.ts:596-604) 의 동시 호출 시 disk 쓰기 충돌 가능성 별도 검토 미실시 (subagent-registry.store.ts out-of-scope).
- 프로덕션 로그에서 실제 "cleanupBrowserSessionsForLifecycleEnd called twice for same sessionKey" 가 관측되는지 telemetry 부재로 미확인.
