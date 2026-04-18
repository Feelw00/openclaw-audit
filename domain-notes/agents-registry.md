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

---

### memory-leak-hunter (2026-04-18, 재실행)

**재실행 사유**: FIND-agents-registry-memory-001 은 `line_range` multi-range 포맷 반려 (B-1-3).
본 세션은 R-1 (단일 연속 라인 범위) 엄수 + FIND-001 과 **의미론적으로 다른** 이슈 한 건 발견.

**R-3 Grep 결과 (자료구조별)**:

```
rg -n "pendingLifecycleErrorByRunId\.(delete|clear)" src/agents/
  subagent-registry.ts:233  delete  (clearPendingLifecycleError — run-scoped, explicit)
  subagent-registry.ts:240  clear   (clearAllPendingLifecycleErrors — TEST ONLY)
  subagent-registry.ts:250  delete  (timer callback, grace-period guard, FIND-001)

rg -n "subagentRuns\.(delete|clear)" src/agents/
  subagent-registry.ts:563             delete   (sweeper, session TTL)
  subagent-registry.ts:593             delete   (sweeper, archiveAtMs)
  subagent-registry.ts:754             clear    (testReset)
  subagent-registry.test-helpers.ts:7  clear    (test helper)

rg -n "resumeRetryTimers\.(delete|clear)" src/agents/
  subagent-registry.ts:425  delete  (timer callback, self-delete unconditional)
  subagent-registry.ts:753  clear   (testReset)

rg -n "setInterval\(|setTimeout\(" src/agents/subagent-registry*.ts
  subagent-registry.ts:245            setTimeout  (pending error grace timer)
  subagent-registry.ts:424            setTimeout  (resume retry timer)
  subagent-registry.ts:524            setInterval (sweeper, 60s)
  subagent-registry-lifecycle.ts:71   setTimeout  (lifecycle timeout — out of scope)

rg -n "sweeper|reaper|cleanup|ttl|TTL" subagent-registry*.ts  (대량)
  - LIFECYCLE_ERROR_RETRY_GRACE_MS = 15_000   (line 137)
  - SESSION_RUN_TTL_MS = 5 * 60_000           (line 139)
  - PENDING_ERROR_TTL_MS = 5 * 60_000         (line 141)
  - sweeper startup: line 500 (restore), run-manager 259/356 (register/steer)
  - sweeper self-stop: line 613-614           ← FIND-002 핵심
  - resetForTests: line 748-773               (테스트 전용 전체 cleanup)
```

**cap / eviction / TTL 테이블**:

| 자료구조 | set 라인 | delete 라인 | TTL | cleanup 신뢰성 |
|---|---|---|---|---|
| `subagentRuns` (Map) | run-manager:330, 255 (register/steer) | 563, 593 (sweeper) | session: 5min after cleanupCompletedAt; non-session: archiveAtMs | sweeper 가능 시 OK. sweeper self-stop 후 새 run 대기 — **FIND-002 연관** |
| `pendingLifecycleErrorByRunId` (Map) | 272 (schedulePending) | 233 (clear), 240 (clearAll), 250 (timer), 557, 574, 606 (sweeper) | 5min (PENDING_ERROR_TTL_MS) | 15s grace mismatch 경로에서 timer delete skip (FIND-001). 또한 sweeper self-stop 시 TTL cleanup 자체 중단 (**FIND-002**) |
| `resumeRetryTimers` (Set) | 433 | 425 (timer self-delete unconditional), 753 (testReset) | timer fire 시 즉시 | **OK**. 자체 delete 무조건 실행. No leak. |
| `resumedRuns` (Set) | 216 decl, 434, 462 | 429 (timer), 755 (testReset), `resetFlagFor` 함수 | N/A | allowed_paths 내 reset 경로 다수. 추가 FIND 대상 아님. |
| `endedHookInFlightRunIds` (Set) | 217 decl | testReset only | N/A | run-manager 범위 밖. 본 세션에서 deep-audit 미수행. |
| `sweeper` (setInterval) | 524 | 537 (stopSweeper), 614 (self-stop), 762 (testReset) | 60s period | self-stop 조건이 pending map 무시 → **FIND-002** |

**적용 카테고리 (재실행)**:
- [x] A. 무제한 자료구조 성장 — 발견 1건 (FIND-002, 이전 FIND-001 과 cross_ref)
- [x] B. EventEmitter/리스너 누수 — 재확인. `ensureListener` guard + `listenerStop` cleanup 정상.
- [x] C. 강한 참조 체인 — timer closure retention 은 FIND-002 mechanism 에 흡수.
- [ ] D. 핸들/리소스 누수 — skipped (fs/HTTP 없음)
- [x] E. 캐시 TTL 부재 — **cleanup 조건부 무력화** (FIND-002 의 본질)

**신규 FIND**:
- FIND-agents-registry-memory-002: `sweeper self-stop on empty runs strands pendingLifecycleError entries indefinitely` (P2, single line_range 603-615)

**FIND-001 과의 차별화 (중복 금지 요건)**:
- FIND-001: grace-period timer callback 의 early-return 으로 15s → 5min gap 동안 map 잔존
  (*동적 실행 중* 의 시간 창 문제).
- FIND-002: sweeper self-stop 조건이 pending map 크기를 무시해 5min TTL 이 **발동 자체가
  중단** 되는 정적 구조 문제 (*sweeper shutdown 후* 의 무기한 누출).

두 이슈는 `cross_refs: [FIND-agents-registry-memory-001]` 으로 연결됨.

**스킵 사유 (false-positive 방지)**:
- `resumeRetryTimers`: timer callback 내 `resumeRetryTimers.delete(timer)` 가 **unconditional
  first statement** (line 425). FIND 대상 아님.
- `subagentRuns` 의 단순 무제한 성장: sweeper 가 정상 가동 시 TTL 로 cleanup. FIND-002 의
  sweeper stopped 경로는 **pendingLifecycleErrorByRunId** 문제로 특정 — subagentRuns 자체는
  `size === 0` 이 되는 시점에만 stop 하므로 runs 누적과 무관.
- EventEmitter/리스너: `ensureListener` 의 `listenerStarted` guard 와 `listenerStop` cleanup
  handle 이 올바르게 쌍을 이룸.

**Self-critique (미확인 영역)**:
- `replaceSubagentRunAfterSteer` (run-manager.ts) 의 내부 로직은 allowed_paths 제한으로
  본 세션에서 deep-read 못함. 거기서 `clearPendingLifecycleError(oldRunId)` 를 호출한다면
  FIND-002 의 일부 시나리오가 완화될 수 있다.
- 프로덕션 process graceful shutdown 경로 (상위 orchestrator) 는 allowed_paths 밖.
- telemetry (map size 시계열) 부재로 실제 누적 속도 미측정. 추정치만 보고.

### clusterer (2026-04-18)

- **CAND-004 (single)**: FIND-agents-registry-memory-002. `sweepSubagentRuns` 의 self-stop 조건이
  `subagentRuns.size === 0` 만 검사하고 `pendingLifecycleErrorByRunId.size` 를 무시하여 TTL 미도달
  pending error 엔트리가 무기한 잔존하는 문제를 단독 CAND 로 발행. FIND 의 cross_refs 는
  FIND-agents-registry-memory-001 (grace-period timer 누출) 을 가리키나 FIND-001 은 본 배치에
  포함되지 않아 epic 구성 불가 — FIND-001 이 ready/ 에 재등장하면 CAND-004 의 epic 승격 재평가.
- 본 도메인의 다른 FIND 들(cron/plugins)과 root_cause_chain 의미론 중복 없음. 공통 원인으로 묶을
  epic 후보 타 도메인에서 발견되지 않음.

### concurrency-auditor (2026-04-19)

**적용 카테고리:**
- [x] A. Shared mutable state async 갱신 race — 발견 1건 (FIND-001, resumedRuns check-then-act)
- [x] B. Promise.race loser 처리 — live-cache-test-support.ts `Promise.race([completeSimple(signal), timeout])` 확인. AbortController 로 loser 취소 정상. FIND 없음.
- [x] C. Listener register race — `ensureListener` 의 `listenerStarted` sync guard 정상. `48042c3875` (endedHookEmittedAt) 이 hook duplicate 는 해결했으나 browser cleanup 은 미보호 → FIND-002.
- [x] D. AbortController 전파 — waitForSubagentCompletion (run-manager.ts:75-132) 가 AbortSignal 을 받지 않음. listener 와 RPC 폴링 각각 독립 → FIND-002 의 일부 맥락. 단독 FIND 아님.
- [ ] E. Microtask ordering — skipped. 대상 파일에 queueMicrotask 없음, setImmediate 는 테스트 파일 1건.
- [x] F. Map/Set operation atomicity — resumedRuns check-then-act (FIND-001). 기타 Set/Map 동작은 sync prefix 안에서 원자적.
- [x] G. Double-dispatch / re-entrance — FIND-001 (resumeSubagentRun retry-limit 재진입), FIND-002 (completeSubagentRun 병행 호출).
- [ ] H. Race with cleanup/disposal — 프로덕션 graceful shutdown 경로 부재 (allowed_paths 외부). 테스트 전용 reset 만 있음.
- [x] Primary-path inversion — beginSubagentCleanup, endedHookEmittedAt, inFlightRunIds Set 을 primary guard 후보로 탐색. 각 FIND 에서 guard 위치 표로 분석.
- [x] Hot-path vs test-path — FIND-001 production hot-path (restore + steer-restart 겹침), FIND-002 production hot-path (embedded run listener + gateway RPC 둘 다 fire). 테스트는 둘 다 single-path 만 커버.

**R-3 Grep 결과 요약**:

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → releaseSubagentRun (semantic, lock 아님). lock primitive match 없음.

rg -n "AbortController|AbortSignal|signal\.(abort|addEventListener)" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → live-cache-test-support.ts:76 (completeSimpleWithLiveTimeout 내부). 나머지 파일 match 없음.
  → subagent-registry/lifecycle 모듈은 AbortController 를 아예 사용하지 않는다.

rg -n "Promise\.race\(|Promise\.all\(|Promise\.allSettled\(" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → subagent-registry-helpers.ts:188 (Promise.all([rootReal, dirReal])) — 파일 경로 realpath 병렬, loser 처리 무관.
  → live-cache-test-support.ts:88 (completeSimple vs timeout race, AbortController 전파).
  → subagent-registry.ts 본체에는 Promise.race/all 사용 없음. async 호출이 직접 이어진다.

rg -n "once\(|prependListener|removeAllListeners\(" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → match 없음. 리스너는 `listenerStop = onAgentEvent(cb)` 에서 unsubscribe 핸들 반환 방식.

rg -n "setImmediate|queueMicrotask|process\.nextTick" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → subagent-registry.steer-restart.test.ts:119 뿐. 본체 코드엔 없음.

rg -n "beginSubagentCleanup|endedHookEmittedAt|endedHookInFlightRunIds" src/agents/subagent-registry*.ts
  → beginSubagentCleanup: lifecycle.ts:280 정의, 310/476/492 호출 (세 호출자 모두 선행 가드로 사용).
  → endedHookEmittedAt: registry.ts:327, completion.ts:58/91, run-manager.ts:231 (steer 재설정 시 undefined 로 reset).
  → endedHookInFlightRunIds: registry.ts:216 선언, 346/675/742 전달/clear, completion.ts:61/65/97 가드.
```

**Upstream 사전 확인 (R-8)**:
- `git log upstream/main --since="3 weeks ago" --oneline -- src/agents/subagent-registry.ts src/agents/subagent-registry-memory.ts src/agents/live-cache-test-support.ts`
- 최근 10 commit 중 race/concurrent/lock/atomic/serialize 키워드 없음.
- `48042c3875 fix(agents): avoid duplicate subagent ended hook loads` 는 endedHookEmittedAt 가드 추가 — FIND-002 는 **그 fix 바깥 범위** (browser cleanup). FIND-001 은 resumedRuns / finalizeResumedAnnounceGiveUp 무가드로 완전 별개.
- `54cf4cd857 test(agents): isolate shared subagent state` 는 test isolation 관련. 본 findings 의 hot-path race 와 무관.
- 현재 로컬 HEAD (d7cc6f7643) 가 upstream/main 보다 뒤쳐져 있으나 resumeSubagentRun 패턴은 upstream 에서도 유지 (직접 `git show upstream/main:src/agents/subagent-registry.ts` 으로 370-470 라인 재확인).

**신규 FIND**:
- FIND-agents-registry-concurrency-001 (P3): `resumeSubagentRun` retry-limit/expiry 분기가 resumedRuns.add + beginSubagentCleanup 둘 다 생략 → `finalizeResumedAnnounceGiveUp` 중복 dispatch → `notifyContextEngineSubagentEnded("deleted")` 2회 fire.
- FIND-agents-registry-concurrency-002 (P2): `completeSubagentRun` 이 listener + waitForSubagentCompletion 두 경로에서 병행 호출될 때 `cleanupBrowserSessionsForLifecycleEnd` 가 `beginSubagentCleanup` 가드 바깥에 있어 동일 childSessionKey 에 대해 2회 발사.

**스킵 사유 (false-positive 방지)**:
- Promise.race loser (live-cache-test-support.ts): AbortController 가 전파되고 finally 가 두 timer 를 clear. loser 정리 정상. FIND 아님.
- listener `ensureListener` double-register: `listenerStarted` sync flag 로 보호. FIND 아님.
- `schedulePendingLifecycleError` 의 timer race (CAL-001 지목 함수): line 244-269 timer callback 의 `pending.timer !== timer` 체크 (line 246) 가 replacement 를 올바르게 감지. line 249 delete 는 replace 이후 old timer 가 fire 해도 새 pending 을 건드리지 않는다 — primary-path guard 정상. CAL-001 교훈 반영 확인.
- `endedHookEmittedAt` idempotency (48042c3875 의 핵심): registry.ts:327 + completion.ts:58/91 + inFlightRunIds 조합이 hook 이중 발사를 막는다. 본 세션 FIND-002 는 이 가드 **바깥** 의 browser cleanup 에 대한 race 이며, hook race 자체는 해결됐다.
- `sweeper self-stop` (FIND-agents-registry-memory-002 기존 이슈): sweeper 중지 조건 자체는 concurrency race 아닌 TTL cleanup gap. 본 세션 중복 아님.
- `persistSubagentRuns()` 동시 호출 시 disk 쓰기 충돌: subagent-registry.store.ts out-of-scope.
- `refreshFrozenResultFromSession` 동시 호출 시 `captureSubagentCompletionReply` race: captureSubagentCompletionReply 가 subagent-announce.ts 에 있어 allowed_paths 외부. 단독 FIND 생성 불가.
- `markSubagentRunTerminated` vs `completeSubagentRun(COMPLETE)` 교차 시 `endedReason` last-writer-wins: 실제 영향은 훅 emit 시 reason 불일치 뿐인데 endedHookEmittedAt 이 1회로 제한 → 의미론적 race 이지만 실재 증상 제한적. P4 수준이라 FIND 4건 제한 하에 제외.

**Self-critique (미확인 영역)**:
- `subagent-orphan-recovery.js` (out-of-scope) 가 resumeSubagentRun 을 어떻게 호출하는지 미확인. 만일 recovery 가 같은 runId 에 반복 호출하면 FIND-001 재현 빈도 상승.
- `browser-lifecycle-cleanup` 구현체 idempotency 미검증 — FIND-002 의 severity 는 "구현 의존" 으로 표기.
- `waitForAgentRun` (run-wait.ts) 의 실제 동작 — 임베디드 run 에서 gateway RPC 로 resolve 되는지 재확인 없이 "그렇다고 가정" (주석 근거).
- telemetry 부재로 실제 프로덕션 관측 불가. FIND 두 건 모두 정성적 영향만 기술.

### clusterer (2026-04-19)

- **CAND-010 (epic)**: FIND-agents-registry-concurrency-001 + FIND-agents-registry-concurrency-002 를
  공통 원인 "subagent-registry 의 beginSubagentCleanup atomic guard 커버리지 갭" 으로 묶어 epic
  발행. 두 FIND 모두 동일 `cleanupHandled` / `cleanupCompletedAt` sync guard 의 보호 범위 바깥에서
  side-effect 가 dispatch 되는 구조적 결함을 드러낸다.
  - FIND-001 (P3): resume 경로가 guard 를 건너뛰고 `finalizeResumedAnnounceGiveUp` 직접 dispatch.
  - FIND-002 (P2): complete 경로에서 `cleanupBrowserSessionsForLifecycleEnd` 가 guard 진입 전에
    실행.
  - 두 FIND 의 file/symbol/trigger-source 는 다르지만 (registry.ts vs lifecycle.ts, resume vs
    complete, restore+steer-restart vs listener+gateway-RPC), root_cause_chain 에서
    "beginSubagentCleanup guard 가 해당 경로에 적용되지 않는다" 가 공통되게 확인됨. 따라서 해결책
    축 ("guard coverage 확장") 이 공통이라고 추정, epic 으로 처리.
  - proposed_severity: P2 (두 FIND 중 상위 값 상속).
- 도메인 내 다른 FIND 들 (memory-001/002) 과는 root cause 가 달라 묶지 않음 (memory 계열은 sweeper
  self-stop / grace-period timer gap 축).

