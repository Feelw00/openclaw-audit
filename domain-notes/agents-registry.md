# agents-registry 도메인 감시 기록

## 도메인 개요

`src/agents/subagent-registry*.ts` 는 openclaw 의 서브에이전트 런타임, 레지스트리, 라이프사이클 관리를 담당하는 핵심 도메인.

### 주요 모듈

- `subagent-registry.ts` — 메인 registry, in-memory 자료구조 (subagentRuns, pendingLifecycleErrorByRunId, resumeRetryTimers), sweeper, lifecycle listener
- `subagent-registry-memory.ts` — 글로벌 `subagentRuns Map` 선언
- `subagent-registry-lifecycle.ts` — 초기화/해제 orchestration, lifecycle controller
- `subagent-registry-completion.ts` — 완료 시퀀스
- `subagent-registry-helpers.ts` — 쿼리, orphan detection, TTL 로직
- `live-cache-test-support.ts` — 라이브 테스트 유틸 (메모리 관련 없음)

---

## 실행 이력

### memory-leak-hunter (2026-04-18)

**적용 카테고리:**
- [x] A. 무제한 자료구조 성장 — 발견 1건
- [x] B. EventEmitter/리스너 누수 — 발견 0건 (on/off 쌍 properly paired)
- [x] C. 강한 참조 체인 — skipped (심화 분석 필요, agent 라이프사이클 전반)
- [x] D. 핸들/리소스 누수 — skipped (fs, HTTP 관련 코드 minimal)
- [x] E. 캐시 TTL 부재 — skipped (subagentRuns 는 sweeper TTL cleanup 있음)

**발견 FIND:**
- FIND-agents-registry-memory-001: `pendingLifecycleErrorByRunId` grace-period timer orphan leak (15s→5min gap)

**주요 관찰:**

1. **subagentRuns Map cap/eviction 정책**:
   - No hard cap on Map size.
   - Sweeper (line 524-530, 60초 주기) 가 TTL 기반 cleanup 수행:
     - Session-mode runs (no archiveAtMs): 5분 TTL (cleanupCompletedAt 기준)
     - Non-session runs: archiveAtMs 초과 시 cleanup
   - Eviction 로직 없음, TTL 만 있음 → startup 후 cleanup 까지 모두 메모리 유지.

2. **pendingLifecycleErrorByRunId 타이머 orphan 누출**:
   - Grace-period timer (15초) 가 entry 없음을 감지하면 조기 반환.
   - 이 경우 pending error 가 Map 에서 삭제되지 않음.
   - Sweeper 의 5분 TTL cleanup 까지 orphaned entries 누적 가능.
   - **이것이 FIND-agents-registry-memory-001 의 이슈.**

3. **resumeRetryTimers Set cleanup**:
   - Timer callback (line 424-430) 에서 자체 삭제 (line 425: resumeRetryTimers.delete(timer))
   - Entry 부재 check (line 426) 있지만, timer 는 **무조건 자신을 삭제** → cleanup OK
   - Test reset (line 750-753) 에서도 명시적 clear

4. **Process-level cleanup**:
   - `resetSubagentRegistryForTests()` (line 748-773) 는 모든 구조를 clear
   - 프로덕션 graceful shutdown 경로 미확인
   - Process 재시작 시 pending error 정리 메커니즘 불명확

5. **Sweeper 신뢰성**:
   - 60초 주기, `sweepInProgress` 플래그로 동시 실행 방지
   - `subagentRuns.size === 0` 일 때 자동 중지 (line 613-614)
   - 높은 GC 부하 시 sweeper 지연 가능성 미테스트

---

## 다음 페르소나를 위한 힌트

### lifecycle-auditor (agents-registry-lifecycle 미정)

1. **Lifecycle start/end 비대칭**: 
   - `registerSubagentRun()` (line 726+) vs cleanup 경로 확인.
   - Entry 추가 시 어떤 상태 초기화되고, 삭제 시 cleanup 순서 검증.

2. **Error state 복구**:
   - Grace-period error handling (line 656-661) 와 recovery flow.
   - Orphan reconciliation (line 439-454) 가 모든 부착 상태를 정리하는지.

3. **Restore from disk**:
   - `restoreSubagentRunsFromDisk()` (line 475+) 이후 pending error cleanup 여부.
   - Process restart 후 기존 pending error entries 처리.

### concurrency-auditor (agents-registry-concurrency 미정)

1. **Concurrent lifecycle events**:
   - Listener (line 621-679) 가 동시 error/end 이벤트를 올바르게 처리하는지.
   - Race between `schedulePendingLifecycleError()` 와 `completeSubagentRun()`.

2. **Resume retry 동시성**:
   - `resumeSubagentRun()` (line 381+) 중 `resumedRuns` Set 의 멀티스레드 안전성.
   - Timer callback 중 entry 교체 (line 426) 와 orphan 처리.

---

## 기술 빚 / 미결

1. **pendingLifecycleErrorByRunId orphan 누출** (FIND-agents-registry-memory-001):
   - Timer 콜백이 entry 부재 시 pending error 를 정리하지 않음.
   - Eager cleanup 경로 부재 → 5분 TTL 대기.
   - 해결 방안: Timer callback 에서 entry 부재 시 **무조건** `pendingLifecycleErrorByRunId.delete(runId)` 실행.

2. **subagentRuns 크기 모니터링**:
   - No metrics for Map size trends.
   - 프로덕션에서 실제 누적 규모 미측정.

3. **Sweeper reliability**:
   - `sweepInProgress` 중에 new runs 추가되면 cleanup 지연 가능.
   - Test 에서 sweeper 지연/실패 시나리오 커버리지 미진.

4. **Process shutdown**:
   - Graceful shutdown 이 pending error 및 retry timer 를 정리하는지 미명시.
   - Production 에서 이 상태가 persist 되지 않도록 보장 필요.

---

## Appendix: Checked Code Paths

### pendingLifecycleErrorByRunId Usage Trace

| Line | Operation | Context |
|---|---|---|
| 218 | Declare | Global const |
| 228-233 | `clearPendingLifecycleError()` | Function defn |
| 237-240 | `clearAllPendingLifecycleErrors()` | Function defn |
| 250 | `delete` (inside timer) | Conditional on `pending` existence |
| 567 | `clearPendingLifecycleError()` | In completeSubagentRun() |
| 604-608 | Sweeper TTL cleanup | Absolute 5min TTL |
| 640, 664 | `clearPendingLifecycleError()` | Lifecycle start/end events |
| 750-753 | Manual clear | Test reset |

**Problem**: Line 250 (timer delete) 는 pending 존재 확인만 하고, entry 부재 시 delete 미실행.

### resumeRetryTimers Usage Trace

| Line | Operation | Context |
|---|---|---|
| 123 | Declare | Global const Set |
| 425 | `delete` (inside timer) | Self-delete, unconditional |
| 433 | `add` | After timer scheduled |
| 750-753 | Manual clear | Test reset |

**Status**: Clean. Self-delete 보장.

### subagentRuns Usage Trace

| Line | Operation | Context |
|---|---|---|
| 3 (memory.ts) | Declare | Export from memory module |
| ~49 entries | `get()`, `set()`, `delete()`, `size` | Various queries/mutations |

**Cleanup Paths**:
- Line 563: Sweeper, session-mode TTL
- Line 593: Sweeper, archiveAtMs exceeded
- Line 754: Test reset

**Assurance**: Sweeper cleanup present, but no hard cap.

