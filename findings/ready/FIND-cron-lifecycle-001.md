---
id: FIND-cron-lifecycle-001
cell: cron-lifecycle
title: startup catch-up path creates task record but never marks job active
file: src/cron/service/timer.ts
line_range: 1043-1081
evidence: "```ts\nasync function runStartupCatchupCandidate(\n  state: CronServiceState,\n\
  \  candidate: StartupCatchupCandidate,\n): Promise<TimedCronRunOutcome> {\n  const\
  \ startedAt = state.deps.nowMs();\n  const taskRunId = tryCreateCronTaskRun({\n\
  \    state,\n    job: candidate.job,\n    startedAt,\n  });\n  emit(state, { jobId:\
  \ candidate.job.id, action: \"started\", runAtMs: startedAt });\n  try {\n    const\
  \ result = await executeJobCoreWithTimeout(state, candidate.job);\n    return {\n\
  \      jobId: candidate.jobId,\n      taskRunId,\n      status: result.status,\n\
  \      error: result.error,\n      summary: result.summary,\n      delivered: result.delivered,\n\
  \      sessionId: result.sessionId,\n      sessionKey: result.sessionKey,\n    \
  \  model: result.model,\n      provider: result.provider,\n      usage: result.usage,\n\
  \      startedAt,\n      endedAt: state.deps.nowMs(),\n    };\n  } catch (err) {\n\
  \    return {\n      jobId: candidate.jobId,\n      taskRunId,\n      status: \"\
  error\",\n      error: normalizeCronRunErrorText(err),\n      startedAt,\n     \
  \ endedAt: state.deps.nowMs(),\n    };\n  }\n}\n```\n"
symptom_type: lifecycle-gap
problem: '''startup catch-up 이 missed job 을 실행할 때 `tryCreateCronTaskRun` 으로 detached
  task 레코드를

  생성하지만 `markCronJobActive(job.id)` 를 호출하지 않는다. 반면 정상 timer 경로 (runDueJob,

  timer.ts:746) 와 executeJob 경로 (timer.ts:1344) 는 작업 시작 시 markCronJobActive 를 호출한다.

  두 경로 모두 tryFinishCronTaskRun 완료 지점 (applyOutcomeToStoredJob, timer.ts:586) 에서

  `clearCronJobActive(result.jobId)` 를 수행하므로, startup 경로만 mark-없이 clear-만 발생한다.''

  '
mechanism: "'1) 게이트웨이 재시작 → ops.start() (ops.ts:87-142) 진입.\n2) stale runningAtMs\
  \ 정리 후 `runMissedJobs(state)` 호출 (ops.ts:119).\n3) planStartupCatchup 이 missed candidate\
  \ 들에 runningAtMs=now 마킹 후 persist (timer.ts:1019-1023).\n4) executeStartupCatchupPlan\
  \ → runStartupCatchupCandidate 가 각 candidate 를 순차 실행.\n5) 이 단계에서 `tryCreateCronTaskRun`\
  \ 은 detached task ledger 에 runtime=\"cron\", status=\"running\"\n   레코드를 남기지만 `markCronJobActive`\
  \ 는 호출되지 않음.\n6) task-registry maintenance (src/tasks/task-registry.maintenance.ts:127,\
  \ allowed_paths 밖이나 참조\n   관계상 언급) 가 주기적으로 돌면서 runtime=cron 작업의 `hasBackingSession`\
  \ 을\n   `isCronJobActive(jobId)` 로 판정. 여기서 `activeJobIds` Set 에 entry 가 없어 false\
  \ 반환.\n7) lost grace (TASK_RECONCILE_GRACE_MS) 가 지나면 task 가 `lost`/`missing` 으로\
  \ reconcile 되어\n   UI/status 에는 완료된 것으로 표시되지만 실제 cron 실행은 계속 진행 중. 반대로 이미 완료된 직후\n\
  \   applyOutcomeToStoredJob(586) 의 `clearCronJobActive` 는 존재하지 않는 key 에 대한 Set.delete\
  \ →\n   silent no-op.'\n"
root_cause_chain:
- why: 왜 startup catch-up 은 markCronJobActive 를 호출하지 않는가?
  because: upstream commit 7d1575b5df (#60310, 2026-04-04) 이 activeJobIds 메커니즘을 도입하면서
    timer.ts:746 (runDueJob) 과 timer.ts:1344 (executeJob) 두 경로에만 mark 를 추가하고, runStartupCatchupCandidate
    (timer.ts:1043-1081) 는 mark 없이 남겨졌다.
  evidence_ref: 'git: 7d1575b5df fix: reconcile stale cron and chat-backed tasks (#60310)'
- why: 왜 이 누락이 버그가 되는가?
  because: task-registry maintenance 의 `hasBackingSession` (src/tasks/task-registry.maintenance.ts:124-128)
    이 runtime==="cron" 일 때 오직 isCronJobActive(jobId) 만으로 backing evidence 를 판정한다.
    mark 가 빠지면 이 판정이 false 가 되어 active cron task 가 `missing`/`lost` 로 분류된다.
  evidence_ref: src/tasks/task-registry.maintenance.ts:124-128 (caller 레벨 참조만; R-2
    준수로 allowed_paths 외 Read 안 함)
- why: 왜 hasBackingSession false-negative 가 프로덕션에서 실제로 발현되는가?
  because: 'openclaw#68157 (2026-04-23 OPEN) — "Cron isolated agentTurn: already-running
    survives restart, run history always empty" — 가 정확히 이 증상을 보고. heartbeat-dispatch
    (sessionTarget=isolated, 30분 주기) 가 restart 후 이미 running 상태로 기록되지만 `cron runs`
    히스토리가 empty. startup 경로에서 task 는 "running" 으로 생성되나 activeJobIds 에 없어 reconcile
    로 lost 처리 경로.'
  evidence_ref: 'github issue: openclaw/openclaw#68157 (OPEN 2026-04-23)'
- why: '왜 이미 merged 된 #60310 의 증상 기반 patch 로 해결이 안 되는가?'
  because: '#60310 의 applyOutcomeToStoredJob 에 추가된 clearCronJobActive 는 대응되는 mark
    없이도 idempotent 하게 호출 (Set.delete on missing key = no-op). 따라서 비대칭이 발견 안 되고 증상
    (task 가 lost 로 분류) 만 반복 관찰됨.'
  evidence_ref: src/cron/service/timer.ts:586 (clearCronJobActive(result.jobId) —
    no-op 시 예외 없음)
impact_hypothesis: wrong-output
impact_detail: '''정성: startup catch-up 에서 실행되는 missed job (sessionTarget=isolated,
  agentTurn 류) 는 task

  ledger 에 running 으로 기록되지만 task-registry maintenance 가 orphan 으로 판단하여 `lost`/`missing`

  으로 reconcile. UI (openclaw cron runs, task status) 는 empty 내지 lost 로 표시, 사용자는 cron
  이

  돌지 않는다고 판단. 실제 실행은 진행 중. 재현 조건: (a) 게이트웨이 재시작 직후, (b) 누적된 missed

  job 중 isolated agentTurn, (c) TASK_RECONCILE_GRACE_MS (기본 5-10분) 초과. 재현 빈도:

  issue #68157 이 30분 cron 으로 관찰했으므로 매 restart 당 최대 `maxMissedJobsPerRestart` (기본 5)
  만큼.''

  '
severity: P2
counter_evidence:
  path: src/cron/service/timer.ts
  line: '586'
  reason: '''applyOutcomeToStoredJob 은 startup 경로의 outcome 에도 clearCronJobActive 를
    호출하지만 (line 586)

    이는 mark 가 없는 상태에서 Set.delete 노op 이므로 "대칭 cleanup" 이 아님. 확인한 반증 카테고리:

    (1) 숨은 방어: 없음 — markCronJobActive 호출은 오직 runDueJob (746) 과 executeJob (1344) 뿐임

    (`rg -n "markCronJobActive" src/` 로 4 건만 매치: active-jobs.ts export 2건 + timer.ts
    2건).

    (2) 기존 테스트 커버: `rg -n "markCronJobActive|isCronJobActive" src/cron/` 결과 cron 내
    production

    code 에서 해당 사이클을 assert 하는 테스트 없음. task-registry.maintenance.issue-60299.test.ts
    가

    존재하지만 allowed_paths 밖 + startup 경로 특정 시나리오 (maxMissedJobsPerRestart 로 executeStartupCatchupPlan

    진입) 는 타겟하지 않음 (인지상).

    (3) 호출 빈도: runMissedJobs 는 모든 start() 호출 후 최소 1회 실행 (ops.ts:119). missed job 0
    건이면

    실효 없음; ≥1 건이면 반드시 runStartupCatchupCandidate 진입.

    (4) 설정 flag: maxMissedJobsPerRestart 가 0 이면 startupCandidates 가 빈 배열이 되나 (timer.ts:1001)

    default 는 5 (DEFAULT_MAX_MISSED_JOBS_PER_RESTART, timer.ts:55). 운영 환경 대부분 영향.

    (5) primary-path inversion (CAL-001): 이 asymmetry 가 성립하려면 어떤 경로가 실패해야 하는가 ->

    mark 가 **추가로** 있어야 대칭인데, 존재 자체가 부재. 정상 경로 실패 의존 없음. primary 경로가 직접

    뚫려 있음.

    (6) upstream-dup (CAL-004/008): `git log upstream/main -- src/cron/active-jobs.ts`
    → 1건 (7d1575b5df,

    #60310). 후속 follow-up 없음. `gh pr list --search markCronJobActive` 에서 이 누락을 직접
    수정하는

    open PR 없음. openclaw#68157 issue 는 2026-04-23 신규 OPEN 으로 증상 보고만 됐고 PR 제출 안 됨.''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-04-24'
cross_refs: []
---
# startup catch-up path creates task record but never marks job active

## 문제

`runStartupCatchupCandidate` (src/cron/service/timer.ts:1043-1081) 는 missed cron job 을 실행하기 직전에 `tryCreateCronTaskRun` 으로 detached task ledger 에 레코드를 남긴다. 이 레코드는 `runtime="cron"`, `status="running"` 으로 task-registry 에 등록된다. 그러나 이 함수는 `markCronJobActive(job.id)` 를 호출하지 않는다.

반면 같은 파일 내 다른 실행 경로는 모두 대칭적으로 mark + clear 를 수행한다:
- `runDueJob` (timer.ts:746): `markCronJobActive(job.id)` 호출.
- `executeJob` (timer.ts:1344): `markCronJobActive(job.id)` 호출.

세 경로 모두 완료 시 `clearCronJobActive(result.jobId)` (applyOutcomeToStoredJob, timer.ts:586) 를 거친다. Startup 경로만 mark 없이 clear 만 존재하는 **init-without-init** 비대칭.

## 발현 메커니즘

1. 게이트웨이 재시작 → `ops.start(state)` (src/cron/service/ops.ts:87-142).
2. stale `runningAtMs` 정리 후 `runMissedJobs(state, ...)` 호출 (ops.ts:119).
3. `runMissedJobs` → `planStartupCatchup` 이 missed candidate 를 선정, 각 job 에 `runningAtMs=now` persist (timer.ts:1019-1023).
4. `executeStartupCatchupPlan` 이 각 candidate 에 대해 `runStartupCatchupCandidate` 를 순차 실행.
5. 이 시점에 `tryCreateCronTaskRun` 은 task ledger 에 `status="running"` 으로 레코드 생성 (timer.ts:1048-1052). 그러나 `activeJobIds` Set 에는 **entry 추가 안 됨**.
6. job execution (`executeJobCoreWithTimeout`) 진행 중 — 예: isolated agentTurn 이 LLM 호출 중이면 수십 초~수 분 지속.
7. 그 사이 task-registry maintenance sweep 이 돌면서 이 task 의 `hasBackingSession` 을 판정. runtime==="cron" 분기 (task-registry.maintenance.ts:124-128) 는 `isCronJobActive(jobId)` 반환. Set 에 없으니 `false`.
8. `hasLostGraceExpired` 통과 시 task → `lost`/`missing` reconcile.
9. 최종적으로 `applyOutcomeToStoredJob` (timer.ts:585-616) 의 `clearCronJobActive` (586) 는 애초에 없었던 key 에 대한 delete → silent no-op, 대칭 복구 없이 상태만 얼룩덜룩.

## 근본 원인 분석

1. **Asymmetric wire-up**: upstream `7d1575b5df` (#60310, 2026-04-04) 이 `activeJobIds` 싱글톤과 `markCronJobActive`/`clearCronJobActive` API 를 도입하면서 `runDueJob` 과 `executeJob` 두 경로에만 mark 를 추가했고, `runStartupCatchupCandidate` 는 간과됨.
2. **Cross-module contract 위반**: `isCronJobActive` 는 `task-registry.maintenance.ts` (src/tasks/) 가 cron runtime task 의 liveness signal 로 사용하는 유일한 신호이므로, mark 누락은 task-registry 와의 계약 위반이며 단일 모듈 내부 버그가 아님.
3. **Silent failure mode**: Set.delete on missing key 는 예외 없이 false 반환 → 비대칭이 런타임에 드러나지 않아 테스트·리뷰에서 놓치기 쉬움.

## 영향

- **현상**: startup catch-up 실행 중인 cron job 의 task 레코드가 UI/status 상 `lost` 로 분류. 사용자는 "cron 이 안 돌았다" 고 관찰.
- **실측 증거**: `openclaw/openclaw#68157` (2026-04-23 OPEN) — heartbeat-dispatch (isolated agentTurn, 30분 주기) 가 restart 후 `openclaw cron runs` 에서 `total: 0` 이지만 실제 shell script 는 실행된 log 확인. 정확히 이 FIND 의 메커니즘.
- **빈도 상한**: `DEFAULT_MAX_MISSED_JOBS_PER_RESTART = 5` (timer.ts:55). restart 1회당 최대 5개 missed isolated job 이 이 버그에 노출.
- **범위**: sessionTarget="isolated" + payload.kind="agentTurn" 조합. main-session cron job 은 task-registry reconcile 대상이 다르므로 영향 적음.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "markCronJobActive" src/` → `active-jobs.ts:15, 19` (export + 본체), `timer.ts:11, 746, 1344`. **totaling 2 production callsites** — runDueJob 과 executeJob 만. `runStartupCatchupCandidate` (timer.ts:1043) 는 존재하지 않음.
- `rg -n "hasBackingSession|isCronJobActive" src/tasks/` 로 대응 측 확인 (allowed_paths 밖이나 caller 레벨만): task-registry.maintenance.ts 에서 cron runtime 이면 **오직** isCronJobActive 로 backing evidence 판정. fallback 없음.

### 기존 테스트 커버리지

- `src/tasks/task-registry.maintenance.issue-60299.test.ts` 가 `isCronJobActive` stub 기반으로 존재 — 하지만 allowed_paths 밖이고, 중요한 것은 **startup catch-up → mark 없는 상태** 의 통합 시나리오는 부재 (페르소나 인지 범위).
- cron 쪽 `src/cron/**` 에서 markCronJobActive 관련 테스트 없음 (`rg -n "markCronJobActive|isCronJobActive" src/cron/` → 0 건).

### 호출 빈도 / 경로 활성 여부

- `runMissedJobs` 는 모든 `start()` 이후 호출 (ops.ts:119). missed 0 건이면 no-op. 1건 이상이면 반드시 runStartupCatchupCandidate 진입.
- `maxMissedJobsPerRestart=0` 환경설정은 희소 (openclaw 기본값 5). 대부분 환경 영향.

### 설정 / feature flag

- `state.deps.maxMissedJobsPerRestart` 가 0 이면 `startupCandidates` 빈 배열 (timer.ts:1001) → 해당 함수 진입 안 함. 하지만 이는 startup-catchup 자체를 끄는 설정이며 기능 무효화.

### Primary-path inversion (CAL-001)

이 asymmetry 가 버그가 되려면 어떤 정상 경로가 실패해야 하는가? 분석: **정상 경로는 존재 자체가 없다** — mark 는 아예 호출되지 않으므로 mask 할 "unconditional cleanup" 이 없다. 이는 CAL-001 의 함정 (숨은 defensive cleanup 을 놓침) 과 반대 상황.

### Hot-path-vs-test-path consistency (CAL-003)

기존 unit test 가 issue-60299.test.ts 처럼 `isCronJobActive` 를 stub 으로 고정하면 이 버그가 관찰 불가. 프로덕션 hot-path (startup catch-up) 에서의 실제 Set 상태는 stub 이 우회. 재현 테스트는 반드시 real `activeJobIds` Set 을 사용해야 함 (resetCronActiveJobsForTests 로 초기화 후).

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/cron/active-jobs.ts` → 1건 (`7d1575b5df`, 2026-04-04). 후속 follow-up 없음.
- `gh pr list --repo openclaw/openclaw --state all --search "markCronJobActive OR activeJobIds"` → martingarramon:test/cron-active-jobs-coverage (#68168, CLOSED 2026-04-17, test 추가 시도만). 이 gap 을 수정하는 OPEN PR 없음.
- `gh issue list --search startup catchup task` → #68157 (OPEN 2026-04-23) 이 증상 보고. upstream 에 아직 fix 없음.

## Self-check

### 내가 확실한 근거

- timer.ts:746 (`markCronJobActive(job.id)` — runDueJob 경로) vs timer.ts:1043-1081 (`runStartupCatchupCandidate`) 의 라인별 차이 Read 로 확인.
- `rg -n "markCronJobActive"` 전수 매치 결과 production code 2건만.
- upstream commit `7d1575b5df` 가 3 곳을 수정했으나 startup 경로는 건드리지 않음 (git show 확인).
- issue #68157 본문에 "already-running survives restart, run history always empty" 명시.

### 내가 한 가정

- `task-registry.maintenance.ts:124-128` 의 cron 분기 구현 세부는 CAL-007/R-2 준수로 **파일 Read 안 함**. 해당 파일 동작 요약은 이전 확인(allowed_paths 밖이지만 참조용 grep) + 주석 수준에 의존. 잘못된 가정이면 impact 가 wrong-output 보다 가볍거나 반대로 심각할 수 있음.
- `DEFAULT_MAX_MISSED_JOBS_PER_RESTART=5` 가 운영 환경 기본값이라는 가정 — CronConfig 로 override 가능. 0 또는 매우 작은 값이면 영향 제한.
- #68157 가 정확히 이 메커니즘의 증상이라는 진단은 이슈 본문만으로 강하게 지지되나, 같은 증상의 다른 원인 (예: runningAtMs persistence 문제) 가능성도 완전히 배제 못 함.

### 확인 안 한 것 중 영향 가능성

- `prepareManualRun`/`finishPreparedManualRun` (ops.ts:548-686) 의 manual run 경로도 동일한 asymmetry 를 가짐 → FIND-cron-lifecycle-002 로 분리.
- `stop()` 경로에서 activeJobIds 싱글톤을 flush 하지 않음 — 이 gap 은 CAL-008 상 PR #43832 (stopGraceful) 가 확장하면 커버 가능하므로 별도 FIND 금지.
- `hasLostGraceExpired` 의 TASK_RECONCILE_GRACE_MS 실제 값은 allowed_paths 밖 — 재현 시 이 상수가 크면 (예: 1h) 증상이 드물게 나타나고 작으면 (예: 1min) 빈발.
