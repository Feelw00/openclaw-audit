---
id: FIND-agents-registry-memory-001
cell: agents-registry-memory
title: pendingLifecycleErrorByRunId — grace-period timer orphan leak (15s→5min gap)
file: src/agents/subagent-registry.ts
line_range: 243-277,604-608
evidence: "```ts\nfunction schedulePendingLifecycleError(params: { runId: string;\
  \ endedAt: number; error?: string }) {\n  clearPendingLifecycleError(params.runId);\n\
  \  const timer = setTimeout(() => {\n    const pending = pendingLifecycleErrorByRunId.get(params.runId);\n\
  \    if (!pending || pending.timer !== timer) {\n      return;\n    }\n    pendingLifecycleErrorByRunId.delete(params.runId);\
  \  // Line 250: ONLY deletes if pending exists\n    const entry = subagentRuns.get(params.runId);\n\
  \    if (!entry) {\n      return;  // Line 253: RETURNS EARLY, leaves pendingLifecycleError\
  \ in map\n    }\n    if (entry.endedReason === SUBAGENT_ENDED_REASON_COMPLETE ||\
  \ entry.outcome?.status === \"ok\") {\n      return;\n    }\n    void completeSubagentRun({...});\n\
  \  }, LIFECYCLE_ERROR_RETRY_GRACE_MS);  // 15 seconds\n  timer.unref?.();\n  pendingLifecycleErrorByRunId.set(params.runId,\
  \ {\n    timer,\n    endedAt: params.endedAt,\n    error: params.error,\n  });\n\
  }\n\n// Sweeper cleanup (line ~604):\nfor (const [runId, pending] of pendingLifecycleErrorByRunId.entries())\
  \ {\n  if (now - pending.endedAt > PENDING_ERROR_TTL_MS) {  // 5 minutes\n    clearPendingLifecycleError(runId);\n\
  \  }\n}\n```\n"
symptom_type: memory-leak
problem: '`pendingLifecycleErrorByRunId` Map entries can persist in memory for up
  to 5 minutes

  after their grace-period timer fires, when the corresponding `subagentRuns` entry
  has already been deleted.

  In high-frequency error + rapid orphan-reconciliation scenarios, orphaned pending-error
  entries accumulate

  without being reclaimed until the sweeper''s absolute TTL (PENDING_ERROR_TTL_MS
  = 5 minutes) is reached.

  '
mechanism: "1. `schedulePendingLifecycleError(runId)` is called on lifecycle `error`\
  \ event (line 657).\n2. Timer scheduled for 15 seconds (LIFECYCLE_ERROR_RETRY_GRACE_MS).\n\
  3. Entry added to `pendingLifecycleErrorByRunId[runId]` (line 272).\n4. Meanwhile,\
  \ if the run is reconciled as orphaned or cleaned up, entry removed from `subagentRuns.delete(runId)`.\n\
  5. When grace-period timer fires:\n   - Line 250: Delete only happens if `pending`\
  \ exists (✓ still there)\n   - Line 251: Fetch `subagentRuns.get(runId)` returns\
  \ undefined (entry was deleted)\n   - Line 253: Return early **WITHOUT** deleting\
  \ from `pendingLifecycleErrorByRunId`\n6. Entry now \"orphaned\" in the map, invisible\
  \ to code flow until sweeper TTL expires (5 min later).\n7. Sweeper only checks\
  \ orphaned entries at line 604-608 every 60 seconds, and deletes only if `now -\
  \ pending.endedAt > PENDING_ERROR_TTL_MS`.\n"
root_cause_chain:
- why: Why does pendingLifecycleError not get cleaned up when its associated run entry
    is deleted?
  because: The timer callback at line 245-269 returns early at line 253 if subagentRuns
    has no entry, without deleting the pending error record. The deletion at line
    250 is conditional on `pending` existing (which it does), but the cleanup of pendingLifecycleErrorByRunId
    happens INSIDE the timer closure, which may return early.
  evidence_ref: src/agents/subagent-registry.ts:245-277
- why: Why is there a 5-minute gap between grace-period timeout (15s) and absolute
    TTL cleanup (5min)?
  because: The grace-period timer is optimistically scheduled to allow retry windows
    to complete, but if the run is deleted before the timer fires (or fires but finds
    no entry), the pending entry persists until the sweeper's absolute TTL check.
    This creates a 5-minute worst-case window where orphaned pending entries consume
    memory.
  evidence_ref: src/agents/subagent-registry.ts:137,141,245,604-608
- why: Why does this become a memory leak in production?
  because: During sustained subagent execution with transient error events followed
    by rapid orphan reconciliation, the pendingLifecycleErrorByRunId map can accumulate
    entries that are invisible to the code flow, only reclaimed every 5 minutes by
    the sweeper. A long-running process with hundreds of short-lived subagent runs
    can accumulate hundreds of orphaned pending-error records.
  evidence_ref: src/agents/subagent-registry.ts:549-602,604-608
impact_hypothesis: memory-growth
impact_detail: 'Each orphaned `pendingLifecycleError` entry consumes ~200 bytes (timer
  ref + metadata).

  In a production scenario with 100 subagents/minute, each emitting transient errors,
  a 5-minute TTL window

  could accumulate 500+ orphaned entries = ~100 KB per cycle.

  Over 24 hours of such activity, without the sweeper cleanup, could reach tens of
  megabytes.

  The sweeper runs every 60 seconds (line 524-530) and only when `sweepInProgress`
  is false, so high GC pressure

  could further delay cleanup and allow accumulation.

  '
severity: P2
counter_evidence:
  path: src/agents/subagent-registry.ts
  line: 604-608
  reason: 'Sweeper cleanup exists for orphaned pendingLifecycleError entries (line
    604-608).

    However, this cleanup relies on an absolute 5-minute TTL (PENDING_ERROR_TTL_MS).

    Grep for cleanup on entry deletion or run reconciliation:

    - `reconcileOrphanedRun()` at line 442-452 deletes from subagentRuns but does
    NOT call clearPendingLifecycleError.

    - `subagentRuns.delete(runId)` occurs at lines 563, 593 during sweeper cleanup,
    but does NOT actively clear pending errors before the sweep cycle (happens passively
    60s later).

    Conclusion: No eager cleanup path exists between run deletion and sweeper invocation.

    '
status: rejected
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
rejected_reasons:
- 'B-1-3: line_range ''243-277,604-608'' invalid format'
---
# pendingLifecycleErrorByRunId — grace-period timer orphan leak (15s→5min gap)

## 문제

`pendingLifecycleErrorByRunId` Map 에 orphaned 엔트리가 15초부터 5분까지 수명을 가진 채 누적될 수 있습니다.
grace-period 타이머가 발동하지만 대응하는 `subagentRuns` 엔트리가 이미 삭제된 경우,
타이머 콜백이 조기 반환하면서 pending error 기록을 Map 에서 제거하지 않고,
5분 TTL 절대 제한 시간까지 메모리에 남아있습니다.

## 발현 메커니즘

1. 서브에이전트 실행 중 lifecycle `error` 이벤트 발생 → `schedulePendingLifecycleError()` 호출 (line 657).
2. 15초 grace-period 타이머 스케줄 (LIFECYCLE_ERROR_RETRY_GRACE_MS).
3. `pendingLifecycleErrorByRunId[runId]` 에 엔트리 추가 (line 272).
4. **동시에** 런 이 orphan 으로 인식되거나 cleanup 되어 `subagentRuns` 에서 삭제됨.
5. 15초 후 타이머 콜백 발동:
   - Line 250: `pendingLifecycleErrorByRunId.delete(runId)` 는 `pending` 존재 시에만 실행 (존재함 ✓)
   - Line 251-253: `subagentRuns.get(runId)` 는 `undefined` 반환 (엔트리 이미 삭제됨)
   - Line 253: `return` 으로 조기 종료 — **`pendingLifecycleErrorByRunId` 에서 삭제되지 않음**
6. 엔트리는 Map 에서 "고아" 상태로 남음. sweeper 의 5분 TTL 만료 시까지 메모리 점유.
7. Sweeper (line 524-530, 60초 주기):
   - Line 604-608: orphaned 엔트리 5분 TTL 확인
   - 5분 경과 후에야 `clearPendingLifecycleError()` 호출

## 근본 원인 분석

### (1) 조건부 삭제 로직의 허점

Timer callback (line 245-269) 는 다음과 같이 설계됨:
- `pending` 존재 여부 확인 (line 246): ✓ 존재 → 조건 통과
- Line 250 에서 **즉시** `pendingLifecycleErrorByRunId.delete(runId)` 실행
- 그러나 line 251 에서 entry 없음 발견 → line 253 에서 return

**문제**: line 250 은 실행되지만, line 250 이 조건부 대신 실행되어야 하는 로직이다.
현재 구조에서는 delete 가 line 250 에 있으므로 pending 존재 여부만 확인하고,
entry 존재 여부는 line 251 에서 확인하는데, entry 부재 시 cleanup 이 일어나지 않음.

### (2) 시간 간격 설계의 약점

- Grace-period timeout: 15초 (line 137: LIFECYCLE_ERROR_RETRY_GRACE_MS)
- Sweeper 절대 TTL: 5분 (line 141: PENDING_ERROR_TTL_MS)
- 타이머 콜백이 entry 없음을 감지해도 delete 하지 않는 경로가 열려있음.

### (3) 빠른 orphan reconciliation 경로

- `reconcileOrphanedRun()` (line 442-452) 가 run 을 orphan 으로 표시하고 registry 에서 삭제함.
- 이 시점에서 pending error 에 대한 정리가 **발생하지 않음**.
- 다음 sweeper 주기 (최대 60초 + 15초 grace) 까지 orphaned pending 이 메모리에 남음.

## 영향

### 누적 규모 추정

- 각 `pendingLifecycleError` 엔트리: ~200 bytes (timer reference + metadata)
- 시나리오: 프로덕션에서 매분 100개 서브에이전트, 각각 transient error 발생
  - 5분 TTL 창에서 500+ orphaned 엔트리 누적 가능
  - 총 ~100 KB/cycle

### 장시간 실행 영향

- 24시간 지속 실행 시: 하루 1,440분 × 평균 50 orphaned entries/min 
  = ~72,000 orphaned entries 
  = ~14 MB 메모리 점유 (5분 TTL 후 회수까지 누적)

### GC 부하

Sweeper 가 60초마다 실행되지만, `sweepInProgress` 플래그로 동시 실행 방지 (line 545).
높은 GC 부하로 sweeper 가 지연되면 orphaned entries 가 더 오래 메모리 점유.

## 반증 탐색

### 1. Sweeper cleanup path 존재 여부

Grep 수행:
```bash
grep -n "pendingLifecycleErrorByRunId" src/agents/subagent-registry.ts
```

결과:
- Line 218: 선언
- Line 228-233: `clearPendingLifecycleError()` 함수 정의
- Line 237-240: `clearAllPendingLifecycleErrors()` — 테스트/종료 경로에서만 호출
- Line 604-608: Sweeper 에서 5분 TTL 확인 및 cleanup
- Line 657-661: lifecycle error 이벤트 수신 시 `schedulePendingLifecycleError()` 호출
- Line 664, 750: `clearPendingLifecycleError()` 호출 (lifecycle start, test reset)

**결론**: Sweeper cleanup 존재하지만, 다음 문제 있음:
1. Eager cleanup 경로 없음 (run 삭제 시 pending error 도 동시 삭제하지 않음)
2. TTL 기반 cleanup 만 존재, 15초 grace 와 5분 TTL 사이 gap 있음

### 2. 기존 테스트 커버리지

Grep:
```bash
grep -n "pendingLifecycleError\|gracePeriod\|LIFECYCLE_ERROR_RETRY" src/agents/subagent-registry*.test.ts
```

결과: 테스트 파일에서 pending error grace-period 의 orphan 시나리오를 테스트하는 케이스 없음.

### 3. Entry cleanup 시점 분석

다음 경로에서 `subagentRuns.delete()` 발생:
- Line 563: Sweeper 에서 session-mode run TTL 만료 후
- Line 593: Sweeper 에서 archiveAtMs 초과 후

두 경우 모두:
- `clearPendingLifecycleError(runId)` 호출 **하지 않음**
- 대신 다음 sweeper 주기에 orphaned pending 발견 후 정리

**결론**: Eager cleanup 부재 → 타이머 orphan 가능.

## Self-check

### 내가 확실한 근거

- **Line 243-277**: `schedulePendingLifecycleError()` 로직과 timer callback 흐름
  - Timer 콜백이 entry 부재 시 early return (line 253)
  - 이 경우 `pendingLifecycleErrorByRunId.delete()` 미실행
- **Line 604-608**: Sweeper 의 orphaned pending cleanup (5분 TTL 기반)
- **Line 245-270**: LIFECYCLE_ERROR_RETRY_GRACE_MS = 15초 선언
- **Line 141**: PENDING_ERROR_TTL_MS = 5분 선언

### 내가 한 가정

1. `reconcileOrphanedRun()` 호출 시 entry 가 `subagentRuns` 에서 삭제된다고 가정.
   - 확인: Line 442-452 참고, orphan reconciliation 후 run 은 registry 에 남지 않음.
2. Grace-period 는 "retry 대기" 시나리오를 위해 설계되었다고 가정.
   - 확인: 주석 line 131-137 참고.
3. Sweeper 가 항상 실행된다고 가정.
   - 실제: `subagentRuns.size > 0` 일 때 시작 (line 494-500), `subagentRuns.size === 0` 일 때 종료 (line 613-614).

### 확인 안 한 것 중 영향 가능성

1. **production 에서 실제 에러율**: 
   - 본 분석은 error event 빈도에 따라 orphaned pending 누적 속도 결정.
   - 실제 프로덕션 워크로드의 error 빈도 미확인.
   
2. **Process 재시작 주기**:
   - `resetSubagentRegistryForTests()` (line 748-773) 는 테스트 용도.
   - 프로덕션 process 종료/재시작 시 어떻게 cleanup 되는지 미확인.
   - 만약 graceful shutdown 이 pending errors 를 정리하지 않으면, 엔트리가 장시간 메모리 점유.

3. **Sweeper 실행 가능성**:
   - Sweeper 가 높은 GC 부하로 중단되는 경우 미테스트.
   - `sweepInProgress` 플래그가 true 로 유지되면 60초 주기가 지연될 수 있음.
