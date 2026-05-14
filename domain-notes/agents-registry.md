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

---

### plugin-lifecycle-auditor (2026-05-14, Phase 6 재진입)

**재진입 사유**: Phase 4 (2026-04-18~19) 에서 PR #68669 인접 scope 혼란 위험으로 보류했었음.
2026-05-14 PR #68669 무대응 유지 상태에서 분리된 axis 만 재탐색.

**R-3 lifecycle 축 Grep 결과**:

```
rg -n "dispose|teardown|cleanup|unregister|deregister|destroy" src/agents/subagent-registry*.ts
  → 대량 cleanup* 매치 (entry.cleanupHandled / cleanupCompletedAt 필드, browserCleanup*).
    dispose/teardown/unregister/deregister/destroy 키워드 0 매치 (domain 의 cleanup 의미는
    field-level 만).

rg -n "process\.(on|once|off)" src/agents/subagent-registry*.ts
  → 0 매치. process-level shutdown 핸들러 부재 (graceful shutdown gap 은 phase 4 에서 기록됨).

rg -n "AbortController|AbortSignal" src/agents/subagent-registry*.ts
  → 0 매치 (concurrency 세션에서도 동일 확인).

rg -n "try\s*\{[\s\S]*?finally" src/agents/subagent-registry*.ts
  → 0 매치 (try/finally pattern 부재; emitSubagentEndedHookOnce 의 try/finally 만 completion.ts:88-120).

rg -n "\.delete\(|\.clear\(\)" src/agents/subagent-registry*.ts src/agents/live-cache-test-support.ts
  → 33 매치. 주요 cluster:
    - subagentRuns/resumedRuns: registry.ts:632/828/858/1028-1029, lifecycle.ts:459/492/595/640
    - pendingLifecycleErrorByRunId: registry.ts:376/383/410
    - pendingLifecycleTimeoutByRunId: registry.ts:392/399/447 (신규 marker, PR 845040214e)
    - resumeRetryTimers: registry.ts:628/1027
    - scheduledResumeTimers: lifecycle.ts:93/107 (controller-local)
    - endedHookInFlightRunIds: registry.ts:1030, completion.ts:119 (finally unconditional)
```

**Lifecycle marker dispose 매트릭스** (CAL-001 / R-5 execution condition 분류):

| 경로 | clearError | clearTimeout | entry delete | 조건 |
|---|---|---|---|---|
| schedulePendingLifecycleError (reg:402-404) | self | cross-clear | — | unconditional |
| schedulePendingLifecycleTimeout (reg:439-441) | cross-clear | self | — | unconditional |
| listener phase=start (reg:911-912) | both | both | — | unconditional |
| listener phase=end (reg:956-957) | both | both | — | unconditional |
| sweeper TTL (reg:870-879) | TTL 5분 | TTL 5분 | sweeper 도달 시 | conditional (TTL) |
| finalizeInterruptedSubagentRun (reg:1100-1102) | both | both | — | unconditional |
| **replaceSubagentRunAfterSteer (run-mgr:307-311)** | error only | **missing** | line 311 delete | safe (entry 삭제로 timer fire safe-exit) |
| **releaseSubagentRun (run-mgr:457/470)** | error only | **missing** | line 470 delete | safe (entry 삭제 + test-only caller) |
| **markSubagentRunTerminated (run-mgr:504)** | error only | **missing** | maintain (cleanupCompletedAt only) | **functional impact** |

`pendingLifecycleTimeoutByRunId` 는 `git show 845040214e` 로 신규 marker 확인 (registry.ts:362-368, fix: recover subagent waits after transport drops, 2026-04-25). 추가된 후 markSubagentRunTerminated 에 dispose 가 incidental 누락된 것으로 추정.

**replaceSubagentRunAfterSteer / releaseSubagentRun** 도 같은 누락이지만:
- 둘 다 entry 즉시 delete → timer callback (registry.ts:442-466) L449 `if (!entry) return;` 으로 safe-exit.
- marker map 에 5분 동안 잔존 (sweeper TTL 까지) → P3 수준 memory pressure 만.
- functional impact 없음 → FIND 생성 보류.
- releaseSubagentRun 은 production caller 부재 (`rg -n "releaseSubagentRun\b" src/` → test only).

**markSubagentRunTerminated** 만 entry 를 maintain (cleanupCompletedAt=now + 5분 잔존) → timer fire 시 callback 의 모든 guard 통과 → completeSubagentRun L780-791 reset 분기 발동 → endedReason KILLED→COMPLETE / outcome error→timeout 으로 덮어쓰임. **functional bug P2**.

**적용 카테고리 (lifecycle-auditor 페르소나)**:
- [x] A. Load 실패 rollback 부재 — registerSubagentRun (run-mgr:374-454) 의 try/catch (425-446) 가 createRunningTaskRun 만 swallow, runs.set 은 그 이전 (424). detached-task-tracker 가 가지 못해도 subagent 자체는 OK. impact 약함 — skip.
- [x] B. Dispose / Unload 경로 누락 — **본 finding** (markTerminated dispose 비대칭).
- [ ] C. Dynamic import 에러 격리 — browserCleanupLoader (registry.ts:110) 의 .load() 가 throw 시 caller 가 await — try/catch 없는 곳 있음. 하지만 본 cell allowed_paths 안의 호출자 모두 try/catch 로 감싸짐. skip.
- [ ] D. Manifest parse 실패 후 partial state — 본 도메인 manifest 부재. skip.
- [ ] E. Enable / Disable 상태 drift — 본 도메인 enable flag 부재. skip.

**스킵 사유 (false-positive 방지)**:
- `scheduledResumeTimers` (lifecycle.ts:89) controller-local Set 의 production graceful shutdown 경로 부재 — `clearScheduledResumeTimers` 는 controller export 이지만 production caller 없음 (test only). 그러나 timer 가 entry guard (line 94) 로 safe-exit. timer.unref() 도 적용 → memory 영향 미미. P4 이하 — skip.
- `replaceSubagentRunAfterSteer` 의 attachmentsDir double-touch (line 309 `void safeRemoveAttachmentsDir(source)` + 새 entry 가 동일 attachmentsDir 사용 가능) — 본 cell 범위 race axis. 별도 concurrency-auditor 영역. skip.
- `registerSubagentRun` (run-mgr:447) 의 ensureListener throw 가 partial init 만들 가능성 — listenerStarted=true set 후 throw 면 silent. 그러나 onAgentEvent 의 throw 가능성은 out-of-scope (gateway). evidence 부족 → skip.
- `pendingLifecycleErrorByRunId / pendingLifecycleTimeoutByRunId` 의 sweeper TTL cleanup 이 sweeper self-stop 후 무력화되는 경로 — 이전 FIND-agents-registry-memory-002 (sweeper self-stop) 의 cross-ref. 본 finding 의 axis 와 다름 (memory-002 는 sweeper, 본 finding 은 marker dispose). skip (중복 회피).

**Self-critique (미확인)**:
- markTerminated 의 production callsite (gateway-side kill, agent.kill 명령, subagent-orphan-recovery.ts) 는 allowed_paths 밖. 빈도 실측 불가.
- completeSubagentRun L780-791 reset 분기의 도입 의도 (killed → COMPLETE late arrival) 는 코드 주석 부재. git blame 으로 추적해도 PR overlap (CAL-008 dup) 가능성.
- detached task runtime 의 status enum 외부 visibility 미확인.
- emitSubagentEndedHookOnce 의 async timing 으로 endedHookEmittedAt set 이 markTerminated 직후 *완료 전* 인 시점에 stale timer 가 fire 하면 두 번째 hook 발사 가능 — 정확한 확률 미측정.

**다음 페르소나 hints**:

### concurrency-auditor (재실행 시)

- `replaceSubagentRunAfterSteer` 의 attachmentsDir 동기성 race (source delete + new entry create 가 같은 attachmentsDir 사용 시) 가 axis 후보. R-7 production hot-path 확인 필요.
- markTerminated 의 emit 직후 stale timer fire 의 hook double-dispatch 가능성 — endedHookEmittedAt set 의 async-ness 가 trigger.

### shutdown / error-boundary-auditor

- process-level graceful shutdown 경로 부재가 본 도메인 전체 axis. registry.ts:1022 resetSubagentRegistryForTests 만 존재. production shutdown 시 in-flight emit / pending marker / sweeper / listener 가 정리되지 않음.

---

### error-boundary-auditor (2026-05-14, Phase 6 batch 2)

**셀**: agents-registry-error-boundary. upstream HEAD `af3d9333aa`.

**페르소나 카테고리 적용 (A~E)**:
- [x] A. unhandledRejection / uncaughtException handler chain — 외곽 global handler 만 존재
  (infra/unhandled-rejections.ts:511). 본 도메인 내부 등록 없음. global handler 의 분류
  (`isTransientUnhandledRejectionError`, line 420-424) 가 network/sqlite/file-watch 만 transient
  처리, 그 외는 `process.exit(1)` → FIND-001 의 핵심 메커니즘.
- [x] B. Floating promise / fire-and-forget async — `void` 패턴 30+. registry.ts:898 listener IIFE
  가 catch chain 없는 유일한 production hot-path → FIND-001.
- [ ] C. JSON.parse / 외부 입력 미보호 — allowed_paths 내 JSON.parse 직접 호출 없음. skip.
- [x] D. AbortController / AbortSignal 전파 — concurrency-auditor (2026-04-19) 가 이미 검사.
  본 페르소나 범위 외.
- [ ] E. fs/network 동기 호출 — out-of-scope (helpers.ts realpathSync 는 cleanup 영역). skip.

**R-3 Grep (방어 경로 + throw)**:

```
rg -n "try\s*\{|catch\s*\(|\.catch\(" src/agents/subagent-registry*.ts \
   src/agents/live-cache-test-support.ts
  핵심:
  - registry.ts:480-498  notifyContextEngineSubagentEnded try/catch (best-effort warn) — 정상
  - registry.ts:681-713  restoreSubagentRunsOnce try / // ignore restore failures  ← FIND-002
  - registry.ts:840-857  sweeper sessions.delete try/catch warn (정상)
  - registry.ts:897-968  listener IIFE *catch chain 없음*  ← FIND-001
  - run-manager.ts:153-244 waitForSubagentCompletion try / // ignore
    ← **PR #75462 (SebTardif, OPEN) 가 수정 중** → CAL-008 회피, FIND 생성 안 함
  - run-manager.ts:425-446 createRunningTaskRun try/catch warn  ← FIND-003
  - lifecycle.ts:156-211 safeSetSubagentTaskDeliveryStatus / safeFinalizeSubagentTaskRun
    try/catch warn (defense layer, 정상)
  - lifecycle.ts:446, 655, 688, 706, 717, 753, 756 fire-and-forget `.catch(...)` 부착 — 정상

rg -n "throw new|throw err|throw error"
  → helpers.ts:200, 236 (fs.realpath ENOENT 외 re-throw), store.ts:207 (disk write throw)
  → 본 audit 내 명시적 throw 적음. 외부 의존성 (createRunningTaskRun, plugin hook, dynamic
    import) 의 throw 가 주 벡터.

rg -n "console\.|log\.|emit\.(error|warn)"
  → log.info/warn 진단 신호 정상. silent 영역은 // ignore 가 catch 표지자.

rg -n "^\s*void " src/agents/subagent-registry*.ts | grep -v test
  → 30+ matches. .catch 부착 점검 완료. registry.ts:898 만 unprotected.

rg -n "process\.exit|process\.kill"
  → match 없음. global handler 만 종료 경로 (FIND-001 메커니즘).
```

**R-5 silent catch 4-caller 분석 (CAL-001)**:

| Silent catch | callers | primary-path inversion | FIND |
|---|---|---|---|
| run-manager.ts:243 `// ignore` | 4 callers all `void` | 없음 | **PR #75462 수정 중** (skip) |
| registry.ts:712 `// ignore restore` | 단일 startup | 없음 | FIND-002 |
| run-manager.ts:441 catch warn | sessions-spawn-tool, subagent-spawn (sync void) | 없음 | FIND-003 |
| registry.ts:898 (catch 부재) | onAgentEvent 단일 production | 없음 | FIND-001 |
| state.ts:11 `// ignore persistence` | persist 호출자 다수 | 미확인 | skip (P4) |
| store.ts:169 `// ignore migration` | one-shot | — | skip |

**CAL-008 upstream 6주 OPEN PR 검사**:

| PR | 본 audit 관계 |
|---|---|
| #68669 | 우리 PR. lifecycle.ts:871-877 browser dedup. **본 audit 의 listener IIFE 와 다른 layer** — FIND-001 분리 명시. |
| #75462 | run-manager.ts:243 silent catch 수정 — **직접 충돌 axis 회피, FIND 생성 안 함**. |
| #76332 | completeSubagentRun preclaim lease. listener wrapper 와 다른 axis. |
| #54765 | durable restore. FIND-002 와 일부 영역 중첩 가능. cross_refs 후보 (PR diff 미 deep-read). |
| #54764 | orphan-prune 통합. 다른 axis. |
| #80544 | ownership 모델. FIND-003 와 무관. |

**신규 FIND (3건)**:
- FIND-agents-registry-error-boundary-001 (P2): listener IIFE 의 catch chain 부재 → cleanup
  transient throw 가 global handler 통해 `process.exit(1)`. PR #68669 axis (line 871-877 browser
  dedup) 와 명확히 분리된 *listener wrapper* layer 결함.
- FIND-agents-registry-error-boundary-002 (P3): restoreSubagentRunsOnce 의 silent catch +
  restoreAttempted try-진입-전 set. 부분 wire-up 실패 영구화 (entries 적재 후 listener/sweeper
  미가동).
- FIND-agents-registry-error-boundary-003 (P3): registerSubagentRun 의 createRunningTaskRun
  catch 가 warn 만 출력하고 진행 → subagent registry vs task registry split state. 사용자 task UI
  영구 누락.

**스킵 사유**:
- **PR #68669 axis 회피**: lifecycle.ts:871-877 browser cleanup wrapper / completeSubagentRun 내
  announce cleanup throw 시 후속 cleanup 미실행 — 본 audit 에서 새 FIND 생성 안 함.
- **PR #75462 axis 회피**: waitForSubagentCompletion silent catch (run-manager.ts:243).
- live-cache-test-support.ts: `LIVE_CACHE_TEST_ENABLED` env guard (line 19-20) 로 test-only.
  importer 모두 `.live.test.ts` 또는 regression-runner. production 영향 없음.
- persistSubagentRunsToDisk silent catch (state.ts:11): caller 다수, 영향 정량 부족, P4. drop.
- safe* helpers (lifecycle.ts:156-211): 의도된 defense, 정상.
- notifyContextEngineSubagentEnded best-effort catch (registry.ts:495-497): 의도된 design.

**Self-critique**:
- `emitSubagentEndedHookForRun` 실제 throw 빈도 미측정. plugin runtime / hook 콜백 production
  failure mode 정량 부재.
- `loadCleanupBrowserSessionsForLifecycleEnd` dynamic import 의 production 실패 빈도 미측정.
- PR #54765 diff deep-read 미수행 — FIND-002 와 일부 중첩 가능성.
- `retireRunModeBundleMcpRuntime` 내부 throw 경로는 pi-bundle-mcp-tools.ts out-of-scope.
- ensureListener 의 `listenerStarted=true` 가 throw 전 set 되는 fragile 패턴은 별도 FIND 후보지만
  4건 한도 + 단독 trigger 빈도 부족으로 본 세션 제외 (FIND-002 의 mechanism 안에서 메모만).


