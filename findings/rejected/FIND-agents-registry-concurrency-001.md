---
id: FIND-agents-registry-concurrency-001
cell: agents-registry-concurrency
title: resumeSubagentRun retry-limit branch skips atomic cleanup guard
file: src/agents/subagent-registry.ts
line_range: 406-414
evidence: "```ts\n  // Skip entries that have exhausted their retry budget or expired\
  \ (#18264).\n  if ((entry.announceRetryCount ?? 0) >= MAX_ANNOUNCE_RETRY_COUNT)\
  \ {\n    void finalizeResumedAnnounceGiveUp({\n      runId,\n      entry,\n    \
  \  reason: \"retry-limit\",\n    });\n    return;\n  }\n```\n"
symptom_type: concurrency-race
problem: resumeSubagentRun 의 retry-limit 분기가 finalizeResumedAnnounceGiveUp 를 fire-and-forget
  으로 호출하면서 beginSubagentCleanup 원자 가드와 resumedRuns.add 를 모두 건너뛰어, 동일 runId 에 대한 두
  번째 resumeSubagentRun 호출이 같은 분기로 재진입할 수 있다. 결과적으로 finalizeResumedAnnounceGiveUp →
  completeCleanupBookkeeping → notifyContextEngineSubagentEnded / retryDeferredCompletedAnnounces
  가 중복 발사된다.
mechanism: "T0  restoreSubagentRunsOnce() 가 디스크에서 runX 를 복원, announceRetryCount >=\
  \ MAX_ANNOUNCE_RETRY_COUNT 상태.\nT1  restoreSubagentRunsOnce 의 for-루프가 resumeSubagentRun(runX)\
  \ 호출 → line 407 분기 진입 → `void finalizeResumedAnnounceGiveUp({...})` 실행 후 즉시 return.\n\
  \     - 이 분기는 resumedRuns.add(runX) 를 실행하지 않는다 (line 441/447/453/461 과 달리).\n  \
  \   - beginSubagentCleanup(runX) 같은 cleanupHandled 플래그 체크도 없다.\nT2  finalizeResumedAnnounceGiveUp\
  \ 가 첫 await safeRemoveAttachmentsDir (lifecycle.ts:265) 에서 yield.\nT3  다른 경로(예:\
  \ clearSubagentRunSteerRestart run-manager.ts:169, retry-timer callback registry.ts:438-439,\
  \ 또는 scheduleSubagentOrphanRecovery 가 트리거한 retry) 가 resumeSubagentRun(runX) 을 재호출.\n\
  \     - line 380: resumedRuns.has(runX) → false (T1 분기가 추가하지 않았으므로).\n     - line\
  \ 383-384: subagentRuns.get(runX) → entry 존재 (completeCleanupBookkeeping 아직 미도달).\n\
  \     - line 407: retryCount 조건 여전히 성립 → `void finalizeResumedAnnounceGiveUp({...})`\
  \ **두 번째** 호출.\nT4  두 invocation 이 병행 진행.\n     - 각자 safeRemoveAttachmentsDir 중복\
  \ 호출 (idempotent rm -rf 이지만 낭비).\n     - 각자 completeCleanupBookkeeping(lifecycle.ts:271)\
  \ 실행:\n       * 첫 호출: runs.delete(runX) 성공, persist, notifyContextEngineSubagentEnded({reason:\"\
  deleted\"}), retryDeferredCompletedAnnounces.\n       * 둘째 호출: runs.delete(runX)\
  \ no-op (false), persist (빈 상태 재기록), notifyContextEngineSubagentEnded **두 번째** 호출,\
  \ retryDeferredCompletedAnnounces **두 번째** 호출.\nT5  결과: context-engine 에 동일 sessionKey\
  \ 에 대한 \"deleted\" notify 가 2회 dispatch.\n     retryDeferredCompletedAnnounces 도\
  \ 2회 실행되어 다른 runs 에 대해 중복 cleanup 스케줄링.\n"
root_cause_chain:
- why: 왜 resumeSubagentRun retry-limit 분기가 중복 실행되는가
  because: line 407-413 분기가 void finalizeResumedAnnounceGiveUp(...) 을 호출하고 resumedRuns.add(runId)
    / beginSubagentCleanup(runId) 둘 다 실행하지 않아, 동일 runId 에 대한 후속 resumeSubagentRun
    호출이 line 380 의 resumedRuns.has 체크를 통과하고 다시 같은 분기로 들어온다.
  evidence_ref: src/agents/subagent-registry.ts:407-413
- why: 왜 retryDeferredCompletedAnnounces 는 이 문제가 없는데 resumeSubagentRun 은 있는가
  because: retryDeferredCompletedAnnounces (lifecycle.ts:309-312) 는 finalizeResumedAnnounceGiveUp
    호출 직전 `if (!beginSubagentCleanup(runId)) continue;` 가드를 거친다. beginSubagentCleanup
    는 cleanupHandled/cleanupCompletedAt 기반 원자적 sync 플래그 세팅이라 두 번째 호출자가 false 를 받는다.
    resumeSubagentRun 은 이 가드를 적용하지 않는다.
  evidence_ref: src/agents/subagent-registry-lifecycle.ts:285-291
- why: 왜 finalizeResumedAnnounceGiveUp 자체에는 idempotency 가 없는가
  because: lifecycle.ts:249-278 의 finalizeResumedAnnounceGiveUp 는 safeSetSubagentTaskDeliveryStatus/safeRemoveAttachmentsDir/completeCleanupBookkeeping/emitCompletionEndedHookIfNeeded
    를 순차 호출할 뿐, 진입 시 runs 에 여전히 entry 가 있는지 또는 cleanupHandled 플래그를 보지 않는다. 호출자가 가드해야
    한다는 암묵적 계약이 있으나 resumeSubagentRun 에서는 누락되었다.
  evidence_ref: src/agents/subagent-registry-lifecycle.ts:249-278
- why: 왜 fire-and-forget void 가 이 경로에 필요한가
  because: resumeSubagentRun 는 동기 함수이고 restoreSubagentRunsOnce 의 for-루프 안에서 호출되므로
    await 를 쓰면 복원 과정이 blocking 되어 sweeper / listener 기동이 지연된다. void + return 으로 비동기
    완료를 잘라내는 설계는 정당하지만, 그로 인해 호출자 레벨에서 중복 dispatch 가드를 넣어야 한다.
  evidence_ref: src/agents/subagent-registry.ts:464-494
impact_hypothesis: wrong-output
impact_detail: '정성적: context-engine 의 onSubagentEnded({reason:"deleted"}) 가 동일 sessionKey
  에 대해 2회 호출된다. 이 콜백은 try/catch 로 warn 만 남기지만 (notifyContextEngineSubagentEnded registry.ts:282-298),
  구현체에 따라 idempotent 가 아닌 경우 상태 불일치 (예: context 세션 메트릭 중복 차감, 캐시 키 중복 삭제) 발생. retryDeferredCompletedAnnounces
  중복 실행은 다른 runs 의 cleanup 을 두 번 예약 → 각 run 마다 beginSubagentCleanup 가드는 있으므로 2차 피해는
  차단되지만 CPU 낭비.


  재현 조건:

  - announceRetryCount >= MAX_ANNOUNCE_RETRY_COUNT 상태인 run 이 디스크에 있다.

  - restoreSubagentRunsOnce() 직후 clearSubagentRunSteerRestart 또는 orphan-recovery 가
  같은 runId 에 대해 resumeSubagentRun 을 트리거한다.

  - finalizeResumedAnnounceGiveUp 가 첫 await (safeRemoveAttachmentsDir) 에서 yield 하는
  윈도우 안에서 두 번째 호출이 진입해야 한다.


  빈도: 낮음 (특정 타이밍 + 특정 announceRetryCount 상태). 프로덕션에서 자주 관측되지 않을 가능성. P3.

  '
severity: P3
counter_evidence:
  path: src/agents/subagent-registry-lifecycle.ts
  line: 249-278
  reason: "Primary-path inversion (CAL-001):\n이 race 가 재현되려면 finalizeResumedAnnounceGiveUp\
    \ 진입 시 idempotency 가드가 없어야 한다. lifecycle.ts:249-278 전체를 읽은 결과 진입부에 cleanupHandled\
    \ / cleanupCompletedAt 또는 runs.has(runId) 같은 가드가 없다. 호출자가 beginSubagentCleanup\
    \ 을 선행하는 계약만 존재한다.\n\nR-3 Grep 결과:\n- rg -n 'Mutex|Semaphore|AsyncLock|acquire|release'\
    \ src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts\n  →\
    \ match 없음 (external lock 부재 확인).\n- rg -n 'beginSubagentCleanup' src/agents/subagent-registry*.ts\n\
    \  → lifecycle.ts:280, 310, 476, 492 에서 호출. resume 경로 registry.ts:407-413 에서는\
    \ 호출 안 됨.\n- rg -n 'resumedRuns\\.(add|delete|has)' src/agents/subagent-registry.ts\n\
    \  → line 380 has, 438 delete, 441/447/453/461 add, 438 delete, 462 add. retry-limit/expiry\
    \ 분기 (407-425) 에는 add 없음.\n- rg -n 'finalizeResumedAnnounceGiveUp' src/agents/\n\
    \  → registry.ts:408/420, lifecycle.ts:249/313. retryDeferredCompletedAnnounces\
    \ 경로(lifecycle.ts:313)는 beginSubagentCleanup guard (lifecycle.ts:310) 사용. resumeSubagentRun\
    \ 경로(registry.ts:408/420)만 가드 누락.\n\n실행 조건 분류 표 (R-5):\n| 호출 지점 | beginSubagentCleanup\
    \ guard | resumedRuns.add | 분류 |\n|---|---|---|---|\n| lifecycle.ts:313 (retryDeferredCompletedAnnounces)\
    \ | 있음 (line 310) | n/a | unconditional guard |\n| registry.ts:408 (resume retry-limit)\
    \ | 없음 | 없음 | no-guard |\n| registry.ts:420 (resume expiry) | 없음 | 없음 | no-guard\
    \ |\n\nunconditional 가드가 재호출 경로에 존재하지 않음 → race 주장 성립.\n\nHot-path vs test-path\
    \ (R-7):\n재현에는 restoreSubagentRunsOnce + clearSubagentRunSteerRestart (또는 orphan-recovery)\
    \ 의 concurrent 진입이 필요. restoreSubagentRunsOnce 는 프로세스 부팅 시 1회, clearSubagentRunSteerRestart\
    \ 는 steer-restart flow 에서 호출. 두 flow 가 겹치려면 restart 직후 steer-cancel 이 빠르게 발생해야\
    \ 하며, 프로덕션에서 드문 시나리오. 단 test-only 재현이 아니라 hot-path 상 실제 가능한 시나리오이므로 severity 유지\
    \ (P3).\n\n기존 테스트 커버리지: subagent-registry.lifecycle-retry-grace.e2e.test.ts, subagent-registry.persistence.resume.test.ts\
    \ 가 있지만 \"같은 runId 에 대해 resumeSubagentRun 을 병행 두 번 호출\" 시나리오는 커버하지 않음 (Grep: `rg\
    \ -n \"resumeSubagentRun\" src/agents/*.test.ts` → 테스트에서는 단일 호출만).\n"
discovered_by: concurrency-auditor
discovered_at: '2026-04-19'
cross_refs:
- FIND-agents-registry-memory-001
- FIND-agents-registry-memory-002
related_tests:
- src/agents/subagent-registry.lifecycle-retry-grace.e2e.test.ts
- src/agents/subagent-registry.persistence.resume.test.ts
status: discovered
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# resumeSubagentRun retry-limit branch invokes finalizeResumedAnnounceGiveUp without atomic guard — double notifyContextEngineSubagentEnded + double completeCleanupBookkeeping

## 문제

`resumeSubagentRun` (src/agents/subagent-registry.ts:379-462) 는 check-then-act 패턴으로 `resumedRuns.has(runId)` (line 380) 을 확인해 재진입을 차단한다. 그러나 retry-limit 분기 (line 407-413) 와 expiry 분기 (line 415-425) 는 `void finalizeResumedAnnounceGiveUp({...})` 호출 후 즉시 `return` 하면서 `resumedRuns.add(runId)` 를 실행하지 않는다. 따라서 동일 `runId` 에 대한 후속 `resumeSubagentRun` 호출이 다시 같은 분기로 진입하고, `finalizeResumedAnnounceGiveUp` 가 중복 실행된다. 내부의 `completeCleanupBookkeeping` 는 `notifyContextEngineSubagentEnded({reason:"deleted"})` 를 2회 dispatch 한다.

## 발현 메커니즘

1. 디스크에 저장된 run `X` 의 `announceRetryCount` 가 `MAX_ANNOUNCE_RETRY_COUNT` 이상 (retry budget 소진).
2. 프로세스 재기동 시 `restoreSubagentRunsOnce()` (registry.ts:464-502) 가 실행되어 line 492-494 의 for-loop 로 `resumeSubagentRun(X)` 호출. line 407 retry-limit 분기 → `void finalizeResumedAnnounceGiveUp({runId:X, entry, reason:"retry-limit"})` dispatch 후 `return` (line 413). `resumedRuns` 에 `X` 가 추가되지 않음.
3. `finalizeResumedAnnounceGiveUp` 가 lifecycle.ts:264 `await safeRemoveAttachmentsDir(...)` 에서 yield.
4. 이 yield 윈도우 안에서 다른 경로가 `resumeSubagentRun(X)` 을 재호출. 가능한 경로:
   - `clearSubagentRunSteerRestart` (run-manager.ts:151-172) 가 `params.resumedRuns.delete(key)` 후 `params.resumeSubagentRun(key)` 호출 (line 167-169).
   - `retryDeferredCompletedAnnounces` (lifecycle.ts:330-331) 가 `params.resumedRuns.delete(runId); params.resumeSubagentRun(runId);`.
   - Retry-timer callback (registry.ts:438-439) 가 재귀적으로 resumeSubagentRun 호출 (expiresCompletionMessage 플로우).
5. 두 번째 `resumeSubagentRun(X)` 이 line 380 의 `resumedRuns.has(X)` 를 false 로 통과, line 383 의 `subagentRuns.get(X)` 도 여전히 entry 반환 (completeCleanupBookkeeping 가 아직 delete 하지 않았으므로). line 407 의 retry-limit 조건 그대로 성립 → `void finalizeResumedAnnounceGiveUp(...)` **두 번째** dispatch.
6. 두 invocation 이 병행 resume:
   - `safeRemoveAttachmentsDir` 2회 호출 (rm -rf idempotent, 낭비).
   - `completeCleanupBookkeeping` (lifecycle.ts:335-361) 2회 호출.
     - 첫 호출: `params.clearPendingLifecycleError(X)`, `void notifyContextEngineSubagentEnded({reason:"deleted"})`, `params.runs.delete(X)` (성공), `params.persist()`, `retryDeferredCompletedAnnounces(X)`.
     - 둘째 호출: 동일 시퀀스. `runs.delete(X)` 는 false 반환. 그러나 `notifyContextEngineSubagentEnded` 는 **다시** dispatch. `retryDeferredCompletedAnnounces` 는 여러 runs 에 대해 재이터 → 각 run 의 `beginSubagentCleanup` 가드가 잡지만 CPU 낭비.
7. `emitCompletionEndedHookIfNeeded` 도 2회 호출되지만 `entry.endedHookEmittedAt` 체크 (completion.ts:58) 가 두 번째를 no-op 화 → 훅은 1회만 emit (48042c3875 fix 덕분).

## 근본 원인 분석

1. **Check-then-act 분기의 add 누락**: `resumeSubagentRun` 의 다른 분기들 (445-462) 은 `resumedRuns.add(runId)` 로 재진입 방지하지만, retry-limit (407-413) 와 expiry (415-425) 는 `void` 호출 후 즉시 return 하며 add 를 생략.
2. **Caller 계약 불일치**: `retryDeferredCompletedAnnounces` (lifecycle.ts:309-312) 는 `beginSubagentCleanup(runId)` 가드를 선행하지만, `resumeSubagentRun` 은 동일한 `finalizeResumedAnnounceGiveUp` 를 직접 호출하면서 이 선행 가드를 생략. 즉, `finalizeResumedAnnounceGiveUp` 의 암묵 계약이 두 호출자 간 일관성이 없다.
3. **`completeCleanupBookkeeping` 자체 비idempotent**: `notifyContextEngineSubagentEnded` / `retryDeferredCompletedAnnounces` 는 호출 시점에 효과가 발생하며, runs.delete 의 실패를 감지해 조기 return 하지 않는다.
4. **Fire-and-forget void 선택의 부작용**: `resumeSubagentRun` 동기 계약을 유지하기 위해 `void finalizeResumedAnnounceGiveUp` 를 택했으나, 이로 인해 호출자 레벨에서 state transition 을 완료하지 못한 채 return 한다.

## 영향

- **데이터 정합성**: context-engine 의 onSubagentEnded 구현이 idempotent 가 아니면 세션 카운터/캐시/메트릭 중복 발사. 실제 영향은 구현체(out-of-scope) 에 따라 다름.
- **CPU 낭비**: retryDeferredCompletedAnnounces 이터레이션 중복. 런타임 runs 수가 큰 경우 O(N) 작업 2회.
- **디스크 쓰기**: `params.persist()` 중복 → 같은 내용 2회 기록 (부작용 없지만 불필요).
- **헤드 영역 attachment 디렉터리 중복 rm**: I/O 중복 (rm -rf, idempotent).

재현 시나리오:
```
// (프로세스 재기동)
restoreSubagentRunsOnce()  // for runX: resumeSubagentRun(X) → retry-limit 분기 → void finalize
// (steer-restart 플로우)
clearSubagentRunSteerRestart(X)  // resumedRuns.delete(X); resumeSubagentRun(X) → retry-limit 분기 → void finalize (중복)
```

## 반증 탐색

### Primary-path inversion (CAL-001 필수)

- 이 race 가 재현되려면 `finalizeResumedAnnounceGiveUp` 또는 `completeCleanupBookkeeping` 또는 `notifyContextEngineSubagentEnded` 중 하나에 unconditional idempotency 가드가 존재하고 실제 taken 되어야 한다.
- `finalizeResumedAnnounceGiveUp` (lifecycle.ts:249-278): 진입부에 `runs.has` 또는 `cleanupHandled` 체크 **없음**.
- `completeCleanupBookkeeping` (lifecycle.ts:335-361): 진입부에 idempotency 가드 **없음**. `runs.delete(runId)` 는 이미 삭제됐어도 false 만 반환하고 상위 return 시키지 않는다.
- `notifyContextEngineSubagentEnded` (registry.ts:282-298): try/catch 로 감싸 있지만 dedupe 없음. onSubagentEnded 는 매번 호출.

**Unconditional 가드 부재 확인**.

### R-3 Grep 결과 (명령 + 결과)

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → src/agents/subagent-registry.ts:776 releaseSubagentRun (semantic release, lock/semaphore 아님)
  → src/agents/subagent-registry-run-manager.ts:357, 481 releaseSubagentRun 정의/export
  → run manager 의 release 는 리소스 lock 해제가 아닌 run cleanup. Mutex/Semaphore/AsyncLock match 없음.

rg -n "beginSubagentCleanup" src/agents/subagent-registry*.ts
  → lifecycle.ts:280 (정의), 310 (retryDeferredCompletedAnnounces 가드), 476 (startSubagentAnnounceCleanupFlow 가드), 492 (동일)
  → registry.ts 의 resume 경로 (407-425) 에서는 호출되지 않음.

rg -n "resumedRuns\.(add|delete|has)" src/agents/subagent-registry.ts
  → line 380 has (진입 가드), 438 delete (retry-timer self-cleanup), 441 add (retry-timer 분기), 447 add (steer-restart 분기), 453 add (announce cleanup 분기), 461 add (waitForCompletion 분기), 462 add
  → line 407-425 의 retry-limit/expiry 분기에는 add 없음.

rg -n "finalizeResumedAnnounceGiveUp" src/agents/
  → registry.ts:408 (retry-limit, 가드 없음), 420 (expiry, 가드 없음)
  → lifecycle.ts:249 (정의), 277 (마지막 라인), 313 (retryDeferredCompletedAnnounces, 가드 있음)
  → lifecycle.test.ts:189, 353 (테스트)
```

### R-5 실행 조건 분류 표

| 경로 | 위치 | beginSubagentCleanup | resumedRuns.add | 분류 |
|---|---|---|---|---|
| restoreSubagentRunsOnce 루프 | registry.ts:492 | 없음 (transitively line 407) | 없음 | 최초 진입 |
| clearSubagentRunSteerRestart | run-manager.ts:167-169 | 없음 | 없음 | concurrent trigger |
| retryDeferredCompletedAnnounces | lifecycle.ts:310 **선행** → :313 finalize | **있음 (unconditional)** | n/a | guarded |
| retry-timer self-callback | registry.ts:437-440 | 없음 | 직전 delete 후 재호출 | retry flow |

**unconditional guard** 가 존재하는 경로는 `retryDeferredCompletedAnnounces` 뿐. `resumeSubagentRun` 경로는 가드 부재.

### R-7 Hot-path vs test-path

- Production: `restoreSubagentRunsOnce` 는 프로세스 부팅 경로 (scheduleSubagentOrphanRecovery 와 동반). `clearSubagentRunSteerRestart` 는 steer-restart 처리 경로. 두 경로 모두 실제 프로덕션에서 실행되는 hot-path.
- Test: subagent-registry 테스트는 개별 시나리오를 단독 실행하며 "동일 runId 에 concurrent resume" 은 커버하지 않는다 (`rg -n "resumeSubagentRun" src/agents/*.test.ts` 로 확인).
- Production 에서 실제 trigger 되려면 재기동 직후 steer-cancel 또는 orphan-recovery 가 같은 runId 에 빠르게 도달해야 함. 조건은 까다롭지만 test-only synthetic 재현이 아님. **severity P3 유지**.

### 추가 탐색

- **외부 Mutex/Semaphore**: 부재. 파일 내 Mutex/Semaphore/AsyncLock match 없음.
- **AbortController 전파**: resumeSubagentRun 는 AbortController 를 사용하지 않음. finalizeResumedAnnounceGiveUp 도 signal 을 받지 않는다.
- **배포 토폴로지**: subagentRuns 는 프로세스-로컬 Map. 단일 프로세스 내 concurrent 재호출 시나리오 가능.
- **상위 guard (orphan-recovery 내부)**: scheduleSubagentOrphanRecovery → scheduleOrphanRecovery (orphan-recovery.js, out of scope) 가 내부적으로 resumeSubagentRun 을 재호출하는지는 이번 세션에서 미검증. 만일 recovery 모듈이 resume 을 여러 번 호출한다면 재현 가능성이 올라간다.

## Self-check

### 내가 확실한 근거

- `resumeSubagentRun` (registry.ts:407-425) 의 retry-limit/expiry 분기가 `resumedRuns.add` 와 `beginSubagentCleanup` 를 모두 누락했음 (직접 Read 로 확인, line 407-425).
- `finalizeResumedAnnounceGiveUp` (lifecycle.ts:249-278) 진입부 idempotency 가드 부재 (Read 확인).
- `completeCleanupBookkeeping` (lifecycle.ts:335-361) 진입부 idempotency 가드 부재 (Read 확인).
- `retryDeferredCompletedAnnounces` (lifecycle.ts:309-312) 는 동일 함수를 호출하기 전 `beginSubagentCleanup` 를 선행 — 일관된 패턴 존재 → resume 경로의 가드 누락이 실수 또는 design gap 일 가능성 시사.
- upstream/main (54cf4cd857 이후) 에도 동일 패턴 존재. 이 race 는 upstream 에서도 미해결.

### 내가 한 가정

- 두 번째 `resumeSubagentRun(X)` 호출이 첫 호출의 `finalizeResumedAnnounceGiveUp` 완료 전에 도달한다. 실제 microtask 타이밍은 JS 엔진 구현에 의존하나, `await safeRemoveAttachmentsDir` 는 파일시스템 I/O 라 충분한 yield 윈도우 존재.
- `notifyContextEngineSubagentEnded` 의 context-engine 구현은 idempotent 가 아닐 수 있다고 가정. 실제 구현 (resolveContextEngine 의 onSubagentEnded) 은 out-of-scope 이라 검증하지 않음.
- `retryDeferredCompletedAnnounces` 중복 이터레이션의 2차 피해는 각 run 의 `beginSubagentCleanup` 가드가 차단한다고 가정 (lifecycle.ts:310 참조).

### 확인 안 한 것 중 영향 가능성

- `subagent-orphan-recovery.js` (out-of-scope) 내부가 어떻게 resume 을 호출하는지 미확인. 만일 recovery 가 동일 runId 에 대해 여러 번 resume 을 호출한다면 재현 빈도 상승.
- `captureSubagentCompletionReply` 와 동시 실행 시 추가 race 가능성 미검증.
- context-engine 의 onSubagentEnded 실제 구현 idempotency 미확인 (scope 밖).
- production 로그에서 notifyContextEngineSubagentEnded "deleted" 중복 발사 사례가 실제로 관측되는지 telemetry 부재로 미확인.
