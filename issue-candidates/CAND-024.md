---
candidate_id: CAND-024
type: epic
finding_ids:
  - FIND-cron-lifecycle-001
  - FIND-cron-lifecycle-002
cluster_rationale: "두 FIND 는 upstream commit 7d1575b5df (#60310, 2026-04-04) 가 activeJobIds 싱글톤과 markCronJobActive/clearCronJobActive API 를 도입하면서 실행 경로 4개 중 2개 (runDueJob timer.ts:746, executeJob timer.ts:1344) 에만 mark/clear 를 주입하고 나머지 2개 (runStartupCatchupCandidate timer.ts:1043, prepareManualRun/finishPreparedManualRun ops.ts:548-686) 를 간과한 partial merge gap 이라는 단일 근본 원인을 공유한다. 증상 축도 동일: task-registry maintenance 의 hasBackingSession(src/tasks/task-registry.maintenance.ts:124-128) 이 runtime==='cron' task 의 liveness 신호로 오직 isCronJobActive(jobId) 만 신뢰하므로 mark 가 빠진 경로는 task 가 lost/missing 으로 오분류된다. 파일/함수 축이 다르지만(timer.ts vs ops.ts) fix surface 는 양쪽에 mark/clear 호출 주입이라는 동일 해결 패턴이며, openclaw 의 'one thing per PR' 원칙에서 '#60310 의 activeJobIds 대칭 계약 완성' 은 단일 task 로 간주 가능. CAND-002 epic 선례 (CAS 부재라는 단일 축으로 두 동시성 경계 FIND 묶음) 에 정확히 대응."
proposed_title: "cron: activeJobIds mark/clear 가 startup catchup 과 manual run 경로에서 누락"
proposed_severity: P2
existing_issue: null
related_issue: 68157
created_at: 2026-04-24
state: pending_gatekeeper
---

# cron: activeJobIds mark/clear 가 startup catchup 과 manual run 경로에서 누락

## 공통 패턴

upstream `7d1575b5df` (#60310, "fix: reconcile stale cron and chat-backed tasks", 2026-04-04) 이
`activeJobIds` 싱글톤 (src/cron/active-jobs.ts) 과 대칭 API `markCronJobActive` /
`clearCronJobActive` 를 도입. 이 Set 은 task-registry maintenance 가 runtime==="cron" task 의
`hasBackingSession` 신호로 사용하는 **유일한** cron liveness 증거다.

문제는 upstream patch 가 cron 의 4개 실행 경로 중 2개에만 mark/clear 를 주입하고 나머지 2개를
간과한 **partial merge gap** 이다:

| 경로 | 위치 | mark | clear | 출처 |
|---|---|---|---|---|
| runDueJob | timer.ts:746 | YES | via applyOutcomeToStoredJob 586 | #60310 커버 |
| executeJob | timer.ts:1344 | YES | timer.ts:1374 | #60310 커버 |
| runStartupCatchupCandidate | timer.ts:1043-1081 | **부재** | 586 에서 no-op (key 없음) | **FIND-cron-lifecycle-001** |
| prepareManualRun / finishPreparedManualRun | ops.ts:548-686 | **부재** | **부재** | **FIND-cron-lifecycle-002** |

결과적으로 두 누락 경로가 실행하는 cron task 는 ledger 상 `running` 으로 기록되지만 maintenance
sweep 이 `isCronJobActive(jobId)` false 를 반환받아 `TASK_RECONCILE_GRACE_MS` 경과 후
`lost`/`missing` 으로 오분류. 사용자 관찰 증상: "cron 이 안 돌았다" — 실제로는 정상 실행 중.

### 근거 인용 (각 FIND root_cause_chain 에서 직접)

**FIND-cron-lifecycle-001** (startup catchup 경로):
- `root_cause_chain[0].because`: "upstream commit 7d1575b5df (#60310, 2026-04-04) 이 activeJobIds
  메커니즘을 도입하면서 timer.ts:746 (runDueJob) 과 timer.ts:1344 (executeJob) 두 경로에만 mark 를
  추가하고, runStartupCatchupCandidate (timer.ts:1043-1081) 는 mark 없이 남겨졌다."
- `root_cause_chain[1].because`: "task-registry maintenance 의 hasBackingSession
  (src/tasks/task-registry.maintenance.ts:124-128) 이 runtime==='cron' 일 때 오직
  isCronJobActive(jobId) 만으로 backing evidence 를 판정한다."

**FIND-cron-lifecycle-002** (manual run 경로):
- `root_cause_chain[0].because`: "upstream commit 7d1575b5df (#60310, 2026-04-04) 이 activeJobIds
  싱글톤 도입 시 timer.ts 내부 두 경로만 건드렸고, ops.ts 의 manual run 분기 (prepareManualRun /
  finishPreparedManualRun) 는 대응되지 않았다. `git show 7d1575b5df -- src/cron/service/ops.ts` →
  no change in that file."
- `root_cause_chain[1].because`: "tryCreateManualTaskRun 이 detached task ledger 에 running
  레코드를 등록한 이상 (ops.ts:432-446), task-registry maintenance 는 해당 runtime='cron' task 를
  reconcile 대상에 포함. cron 분기는 isCronJobActive 단일 신호에 의존 → activeJobIds Set 비어있어
  false. 결과 lost 로 분류."

두 FIND 모두 `evidence_ref` 에 동일 upstream commit `7d1575b5df` 를 지목 — **같은 partial merge
의 서로 다른 surface**. FIND-002 는 이미 `cross_refs: [FIND-cron-lifecycle-001]` 로 두 축의 연결을
선언하고 있다.

### Epic 으로 묶는 이유

1. **단일 원인**: 한 upstream commit 의 scope 부족이 두 경로를 동시에 만들었다. 독립 발견이 아닌 한
   리팩터의 파편.
2. **단일 계약**: 양쪽 다 task-registry 와의 cross-module contract (runtime==='cron' ↔
   isCronJobActive) 위반. 해결 의미가 "이 계약을 완전히 구현" 하나로 수렴.
3. **단일 fix surface**: 양쪽 모두 `markCronJobActive(job.id)` + `clearCronJobActive(job.id)` 호출
   주입. 구체적 해결 방식은 solution 단계에서 확정 (try/finally 래핑 포함).
4. **단일 regression test axis**: task-registry reconcile 을 real activeJobIds Set + real
   tryCreateCronTaskRun/tryCreateManualTaskRun 로 돌리는 통합 테스트 하나가 양쪽을 커버 가능
   (stub 금지 — CAL-003).
5. **메인테이너 커뮤니케이션**: related issue #68157 (OPEN 2026-04-23) 이 증상만 보고 중. PR
   description 에 "startup + manual 양 경로 통합 fix" 로 기술하는 편이 "같은 issue 를 두 PR 로 쪼개
   고치는" 것보다 리뷰 비용 낮음.

## 관련 FIND

- **FIND-cron-lifecycle-001** (P2, symptom_type=lifecycle-gap):
  `runStartupCatchupCandidate` (src/cron/service/timer.ts:1043-1081) 가 missed job 을 실행할 때
  `tryCreateCronTaskRun` 으로 task ledger 에 레코드를 남기지만 `markCronJobActive` 호출 없음.
  재시작 직후 catchup 경로에서 실행되는 isolated agentTurn job 들이 lost 로 오분류.
- **FIND-cron-lifecycle-002** (P2, symptom_type=lifecycle-gap):
  `prepareManualRun` / `finishPreparedManualRun` (src/cron/service/ops.ts:548-686) 가 사용자 수동
  실행 시 task ledger 를 등록하지만 mark/clear 양쪽 모두 부재. `openclaw cron run <id>` 호출한
  isolated agentTurn 이 실행 중 lost/missing 으로 오분류.

## 관련 upstream artifact

- **Upstream commit `7d1575b5df`** (#60310, merged 2026-04-04): activeJobIds 싱글톤 도입. 본
  epic 이 보완하는 대상 patch.
- **Related open issue `openclaw/openclaw#68157`** (OPEN 2026-04-23): "Cron isolated agentTurn:
  already-running survives restart, run history always empty" — 본 epic 의 정확한 symptom
  manifestation. upstream 에 아직 fix 없음. PR 시 reference.
- **Upstream PR `#68168`** (CLOSED 2026-04-17): activeJobIds singleton unit test 커버리지 추가
  시도. asymmetry 수정은 없고 CLOSED. 본 epic 과 비경합.
- **Upstream PR `#60566`** (CLOSED 2026-04-03): runtime==='cron' && no childSessionKey 를 `missing`
  으로 직접 분류하려는 증상 기반 patch 시도. 본 epic 의 gap 을 덮으려다 미채택 — 본 epic 의 근본
  원인 진단 유효성 역설적 지지.

## Upstream-dup check 결과 (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/cron/active-jobs.ts` → 1건 (`7d1575b5df`
  자체). 후속 follow-up commit 없음.
- `git log upstream/main --since="6 weeks ago" -- src/cron/service/timer.ts src/cron/service/ops.ts`
  → 변경 다수 있으나 activeJobIds 대칭 보완 commit 없음 (lazy-load runtime 분리 / zero nextRunAtMs
  / nextRunAtMs truthiness / jobs.json split 등 이 축과 무관한 수정들).
- `gh pr list --repo openclaw/openclaw --state all --search "markCronJobActive OR activeJobIds"`
  → #68168 (CLOSED) 만 매치. OPEN PR 없음.
- `gh issue list --search "startup catchup task OR cron run history empty"` → #68157 (OPEN) 증상
  보고만. upstream 에서 이 gap 을 직접 해결하려는 작업 진행 중 없음.

결론: upstream-dup 없음. epic 진행 가능.

## Proposed severity 근거

양쪽 FIND 모두 P2. 증상이 wrong-output (task status 오표시 + 사용자 혼란) 으로 데이터 손실이나
서비스 중단은 아님. 그러나 #68157 이 실제 사용자 보고로 존재하며 isolated agentTurn (heartbeat
dispatch 등 reliability-sensitive 사용 사례) 에 영향 → P3 가 아닌 P2 유지. 메인테이너 우선순위
(cron + reliability) 에 정확 부합.

## 수정 scope (solution 단계로 이월)

Solution-drafter 가 구체 fix 를 결정. 본 CAND 는 문제/원인 공통성까지만 기술.
- mark/clear 주입 지점 후보: timer.ts:1048 직전 + 1078 직후 (FIND-001), ops.ts:577 부근 +
  ops.ts:611-615 부근 (FIND-002)
- try/finally 래핑으로 예외 경로에서도 clear 보장
- 재현 테스트: real activeJobIds Set 사용 (resetCronActiveJobsForTests 후) + tryCreate*TaskRun
  path 를 통합해서 isCronJobActive 가 실행 중 true, 완료 후 false 임을 assert

## 제외된 인접 lifecycle 축 (중복 방지)

- **async-stop-not-awaited** (stopTimer 가 in-flight onTimer 대기 안 함): upstream PR #43832
  (OPEN 2026-03-12, stopGraceful) 가 동일 root 수정 중 → CAL-008 으로 FIND 생성 금지 (plugin-
  lifecycle-auditor 세션 노트 참조).
- **partial-rollback-on-failure (start path)** (runMissedJobs throw → armTimer 건너뜀): upstream
  PR #68112 (OPEN 2026-04-17) 가 수정 중 → CAL-008 로 억제.
- **duplicate-register-on-reentry** (start 재호출): armTimer 내부 `if(state.timer) clearTimeout`
  선행으로 대칭 확보 → FIND 없음.
- **storeLocks 재사용** (모듈-scope Map, delete 없음): cron-memory 재감사에서 growth rate 0 으로
  이미 abandon → 본 epic 범위 밖.
