---
id: FIND-cron-lifecycle-002
cell: cron-lifecycle
title: manual run path creates task record but never marks/clears active
file: src/cron/service/ops.ts
line_range: 564-592
evidence: "```ts\n  return await locked(state, async () => {\n    // Reserve this\
  \ run under lock, then execute outside lock so read ops\n    // (`list`, `status`)\
  \ stay responsive while the run is in progress.\n    const job = findJobOrThrow(state,\
  \ id);\n    if (typeof job.state.runningAtMs === \"number\") {\n      return { ok:\
  \ true, ran: false, reason: \"already-running\" as const };\n    }\n    job.state.runningAtMs\
  \ = preflight.now;\n    job.state.lastError = undefined;\n    // Persist the running\
  \ marker before releasing lock so timer ticks that\n    // force-reload from disk\
  \ cannot start the same job concurrently.\n    await persist(state);\n    emit(state,\
  \ { jobId: job.id, action: \"started\", runAtMs: preflight.now });\n    const taskRunId\
  \ = tryCreateManualTaskRun({\n      state,\n      job,\n      startedAt: preflight.now,\n\
  \    });\n    const executionJob = structuredClone(job);\n    return {\n      ok:\
  \ true,\n      ran: true,\n      jobId: job.id,\n      taskRunId,\n      startedAt:\
  \ preflight.now,\n      executionJob,\n    } as const;\n  });\n}\n```\n"
symptom_type: lifecycle-gap
problem: '''`prepareManualRun` (ops.ts:548-592) 는 사용자가 `openclaw cron run <id>` 를
  호출할 때 실행되는

  manual run 경로로, `tryCreateManualTaskRun` 으로 task ledger 에 runtime="cron", status="running"

  레코드를 생성한다. 그러나 이 함수와 대응 완료 함수 `finishPreparedManualRun` (ops.ts:594-686)

  어느 쪽도 `markCronJobActive(job.id)` / `clearCronJobActive(job.id)` 를 호출하지 않는다.

  timer.ts 의 다른 두 실행 경로 (runDueJob, executeJob) 는 모두 대칭적으로 mark + clear 하는 것과 대비.''

  '
mechanism: "'1) 사용자가 `openclaw cron run <jobId>` 를 호출 → `CronService.run` → `ops.run`\
  \ (ops.ts:688-695).\n2) `prepareManualRun` (ops.ts:548-592) 이 locked 블록에서 runningAtMs=now\
  \ 를 찍고 persist.\n3) `tryCreateManualTaskRun` (ops.ts:425-455) 이 detached task runtime\
  \ 에 `createRunningTaskRun({runtime:\"cron\"})` 호출. task ledger 에 \"running\" 레코드\
  \ 등록.\n4) **markCronJobActive 호출 없이** `executionJob` clone 을 반환.\n5) `finishPreparedManualRun`\
  \ (ops.ts:594-686) 이 locked 밖에서 `executeJobCoreWithTimeout` 으로 실제 실행.\n6) isolated\
  \ agentTurn 은 LLM 호출 + delivery 로 수십 초~수 분 지속.\n7) 그 사이 task-registry maintenance\
  \ (src/tasks/task-registry.maintenance.ts:127, allowed_paths 외 참조만)\n   이 이 task\
  \ 의 `hasBackingSession` 을 판정 — cron runtime 분기는 `isCronJobActive(jobId)` 만 신뢰 →\n\
  \   Set 에 없음 → false → `hasLostGraceExpired` 와 결합하여 `missing`/`lost` reconcile.\n\
  8) 실행 완료 시 `tryFinishManualTaskRun` 으로 task 를 complete/fail 로 전환하지만 clearCronJobActive\n\
  \   도 없음 — 원래 mark 가 없었으므로 대칭은 무해하나, 런타임 도중 reconcile 로 lost 된 task 가\n   이후 completed\
  \ 로 업데이트되는 이상 transition 발생 가능.'\n"
root_cause_chain:
- why: 왜 manual run 경로는 markCronJobActive 를 호출하지 않는가?
  because: upstream commit 7d1575b5df (#60310, 2026-04-04) 이 activeJobIds 싱글톤 도입 시
    timer.ts 내부 두 경로만 건드렸고, ops.ts 의 manual run 분기 (prepareManualRun / finishPreparedManualRun)
    는 대응되지 않았다. `git show 7d1575b5df -- src/cron/service/ops.ts` → no change in that
    file.
  evidence_ref: 'git: 7d1575b5df fix: reconcile stale cron and chat-backed tasks (#60310)'
- why: 왜 manual run 중 이 누락이 버그가 되는가?
  because: tryCreateManualTaskRun 이 detached task ledger 에 running 레코드를 등록한 이상 (ops.ts:432-446),
    task-registry maintenance 는 해당 runtime="cron" task 를 reconcile 대상에 포함. cron 분기는
    isCronJobActive 한 신호에 의존 → activeJobIds Set 비어있어 false. 결과 lost 로 분류.
  evidence_ref: src/cron/service/ops.ts:432-446 (createRunningTaskRun runtime=cron,
    ownerKey="", scopeKind="system")
- why: 왜 startup 경로와 별도 FIND 로 분리되는가?
  because: FIND-cron-lifecycle-001 은 재시작 직후 missed job 경로, 본 FIND 는 런타임 중 사용자 명령 경로.
    두 경로는 호출 주체·빈도·설정이 달라 수정 지점도 독립. 같은 cross_refs 하에 묶어서 fix 가능하지만 발현 조건이 분리됨.
  evidence_ref: src/cron/service/ops.ts:688-695 (public `run` entry) vs src/cron/service/timer.ts:1043
    (startup candidate)
- why: 왜 clearCronJobActive 누락이 동시에 있는데도 detect 안 됐는가?
  because: mark 가 애초에 없었으므로 clear 누락은 Set.delete 시도도 없음 → 상태는 시종일관 "empty" 로 consistent.
    어떤 invariant 도 깨지지 않음. 비대칭은 cross-module 차원 (task-registry 와의 계약) 에서만 드러남.
  evidence_ref: src/cron/active-jobs.ts:22-27 (clearCronJobActive 는 empty key 에 no-op)
impact_hypothesis: wrong-output
impact_detail: '''정성: `openclaw cron run <id>` 로 isolated agentTurn job 을 수동 실행할 때
  task ledger 상 running

  으로 생성된 레코드가 task-registry maintenance 에 의해 lost/missing 으로 reconcile. 사용자는 "수동

  실행이 실패한 것처럼 보이지만 실제로는 성공적으로 수행" 같은 혼란. 빈도: 수동 실행 빈도에 비례하며

  isolated long-running job (agentTurn) 에만 영향 (main-session wake 는 fire-and-forget
  으로 task 생성 없음).

  재현 조건: job.sessionTarget=isolated + payload.kind=agentTurn + 실행 시간 > TASK_RECONCILE_GRACE_MS.

  issue #68157 과 유사한 증상 (만약 사용자가 `cron run` 으로 trigger 시).''

  '
severity: P2
counter_evidence:
  path: src/cron/service/ops.ts
  line: '577'
  reason: '''확인한 반증 카테고리:

    (1) 숨은 방어: 없음 — `rg -n "markCronJobActive|clearCronJobActive" src/cron/service/ops.ts`
    → 0 건.

    manual run 경로가 별도 import 하거나 우회하는 방법 없음.

    (2) 기존 테스트 커버: src/cron/service/ops.test.ts, ops.regression.test.ts 내부에 manual
    run →

    isCronJobActive 어설션 없음 (`rg -n "isCronJobActive" src/cron/` → 0 건).

    (3) 호출 빈도: 사용자 명령 `openclaw cron run`, `enqueueRun` 통해 invoke. 빈도는 운영자 의존.

    (4) 설정: 해당 asymmetry 는 cronConfig flag 영향 없음.

    (5) primary-path inversion (CAL-001): mark 가 없는 상태에서 "cleanup 실패 → leak" 이 아니라

    "존재 자체가 부재" → cross-module 계약 위반. 정상 경로 의존성 없이 직접 뚫려 있음.

    (6) hot-path-vs-test-path (CAL-003): manual run 경로 테스트가 `isCronJobActive` 을 stub
    처리하면

    실제 프로덕션 재현 불가. 재현 테스트는 real activeJobIds Set + tryCreateManualTaskRun spy 로 짜야.

    (7) upstream-dup (CAL-004/008): `gh pr list --search "manual cron run mark OR
    cron task registry manual"`

    → 관련 open PR 없음. PR #60566 (CLOSED, 2026-04-03) 은 runtime==="cron" && no childSessionKey
    를

    `missing` 으로 직접 분류하려 시도 → 이 FIND 와 반대 방향 (bug 증상을 확정적 lost 로). MERGED 된

    #60310 은 idle 경로만 수정. 본 gap 을 직접 해결하는 upstream fix 없음.''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-04-24'
cross_refs:
- FIND-cron-lifecycle-001
---
# manual run path creates task record but never marks/clears active

## 문제

`ops.run` (ops.ts:688) → `prepareManualRun` (ops.ts:548-592) → `finishPreparedManualRun` (ops.ts:594-686) 경로가 사용자 수동 실행 시 cron task ledger 에 `runtime="cron", status="running"` 레코드를 등록하지만, `markCronJobActive` / `clearCronJobActive` 호출이 양쪽 모두 부재. timer.ts 의 runDueJob (746), executeJob (1344) 는 모두 대칭 mark/clear 함.

## 발현 메커니즘

1. 사용자가 `openclaw cron run <jobId>` 호출 → `CronService.run(id, mode)` → `ops.run(state, id, mode)` (ops.ts:688-695).
2. `prepareManualRun` 이 locked 블록 진입:
   - `job.state.runningAtMs = preflight.now` 찍고 persist (ops.ts:571-575)
   - `tryCreateManualTaskRun` 호출 → `createRunningTaskRun({runtime:"cron", sourceId:job.id, status:running, ...})` (ops.ts:432-446)
   - **markCronJobActive 호출 없음**
   - `executionJob = structuredClone(job)` 으로 snapshot 생성 후 반환 (ops.ts:582-590)
3. `finishPreparedManualRun` 이 **locked 밖** 에서 `executeJobCoreWithTimeout(state, executionJob)` 호출 (ops.ts:606). 이 구간이 isolated agentTurn 의 경우 수 분 소요.
4. 실행 중 task-registry maintenance sweep 이 주기적으로 돌면서 해당 task 의 `hasBackingSession` 을 판정 → runtime==="cron" 분기 → `isCronJobActive(jobId)` → Set 에 entry 없음 → false.
5. `hasLostGraceExpired` 통과 시 task record → `lost` (또는 #60566 PR 제안된 `missing`) 으로 reconcile. 사용자는 "이미 lost" 로 관찰.
6. 실행 완료 시 `finishPreparedManualRun` 의 `tryFinishManualTaskRun` (ops.ts:611-615) 이 task 를 complete/fail 로 업데이트. **clearCronJobActive 호출 없음** — 원래 mark 가 없었으므로 무해하지만 대칭 위반.
7. 결과: task ledger 에 `running → lost → complete` 같은 이상 transition. UI 에는 `enqueued → lost` 만 노출되는 경우도 발생.

## 근본 원인 분석

1. **Upstream patch scope 부족**: `#60310` 의 `activeJobIds` 도입이 timer.ts 의 두 경로만 다뤘고 ops.ts 의 manual run 경로 (`prepareManualRun`/`finishPreparedManualRun`) 는 건드리지 않았다. `git show 7d1575b5df -- src/cron/service/ops.ts` → 해당 파일 변경 없음.
2. **Cross-module contract gap**: task-registry maintenance (src/tasks/) 가 cron runtime 의 backing evidence 로 `isCronJobActive` 만 신뢰하는 단일 신호. mark 누락 시 fallback 부재.
3. **Lock vs. execution 경계**: `prepareManualRun` 은 locked 안에서 runningAtMs 를 persist 하지만, 실제 실행 구간 (`executeJobCoreWithTimeout`) 은 locked 밖. 이 구간이 긴 경우 maintenance sweep 이 반드시 찔러봄 → activeJobIds 누락이 가시화.

## 영향

- **현상**: 수동 실행된 isolated agentTurn job 이 UI/status 상 lost/missing 으로 분류되면서 실제 실행은 정상 완료. 사용자 혼란.
- **대상 job 유형**: `sessionTarget="isolated"` + `payload.kind="agentTurn"` (긴 실행 시간). `sessionTarget="main"` 은 wake 이후 cron 이 task 를 관리 안 함.
- **재현**: `openclaw cron run <isolated-agent-job>` 후 TASK_RECONCILE_GRACE_MS (기본값 확인 못 했으나 분 단위 추정) 내 `openclaw tasks list` 로 lost/missing 관측 기대.
- **빈도**: 수동 실행 빈도 의존. 자동 스케줄은 runDueJob 경로를 타므로 영향 없음.
- **중복 위험**: issue #68157 의 증상 일부 (isolated agentTurn 이 "이상한 상태" 에 빠짐) 가 본 FIND 와 startup 경로 FIND 의 중첩 효과일 수 있음.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "markCronJobActive|clearCronJobActive" src/cron/service/ops.ts` → 0 건.
- ops.ts import 절 (1-43): `markCronJobActive` / `clearCronJobActive` import 없음.
- `tryCreateManualTaskRun` 내부 (ops.ts:425-455) 에 activeJobIds 관련 호출 없음.

### 기존 테스트 커버리지

- `rg -n "isCronJobActive|markCronJobActive" src/cron/` → active-jobs.ts 구현부만 매치, service 테스트에서 어설션 부재.
- `src/tasks/task-registry.maintenance.issue-60299.test.ts` (allowed_paths 밖) 는 `isCronJobActive` 를 stub 처리 → 실제 mark/clear 누락 경로 검증 불가.

### 호출 빈도 / 경로 활성

- `ops.run` 은 CronService.run / CronService.enqueueRun 로 public API. 운영자 수동 trigger.
- isolated agentTurn 이 아니면 (예: main wake) task 생성이 별도 경로로 가므로 이 FIND 영향 없음.

### 설정 / feature flag

- cronConfig 에서 이 대칭을 toggle 하는 옵션 없음.

### Primary-path inversion (CAL-001)

"mark 누락 → cross-module lost" 성립을 위해 어떤 정상 경로가 실패해야 하는가? → 아무것도 실패할 필요 없음. mark 가 존재하지 않는 것 자체가 primary gap. CAL-001 의 "unconditional cleanup 을 놓침" 과 반대 방향.

### Hot-path-vs-test-path consistency (CAL-003)

재현 테스트는 `resetCronActiveJobsForTests()` 로 Set 초기화 후, manual run 호출 → `isCronJobActive(jobId)` 가 false 를 반환하는지 확인. stub 금지.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/cron/service/ops.ts` → 여러 커밋 있으나 activeJobIds 관련 수정 없음.
- `gh pr list --repo openclaw/openclaw --state all --search "cron manual run task active OR markCronJobActive ops"` → 관련 open PR 없음.
- PR #60566 (CLOSED, 2026-04-03) 은 `runtime==="cron" && no childSessionKey → missing` 로 reconcile 규칙 강화 시도 (본 FIND 의 gap 을 임시로 덮으려는 방향) 지만 CLOSED 로 미채택.

## Self-check

### 내가 확실한 근거

- ops.ts:548-592, 594-686 를 Read 로 전체 확인. `markCronJobActive` / `clearCronJobActive` 부재 직접 확인.
- ops.ts import section (line 1-43) 에 active-jobs 모듈 import 없음.
- timer.ts 의 대조 경로 (runDueJob 746, executeJob 1344) 는 Read 로 직접 확인 — 둘 다 mark 호출.
- upstream `7d1575b5df` commit 의 file list 에 src/cron/service/ops.ts 없음 (`git show --stat`).

### 내가 한 가정

- `task-registry.maintenance.ts:127` 의 `runtime==="cron"` 분기가 오직 isCronJobActive 로만 판정한다는 가정은 이전 그레핑 결과(allowed_paths 밖 읽기 최소화). 세부 동작 달라질 수 있으나 caller 참조는 분명히 `isCronJobActive` 를 유일 신호로 사용.
- TASK_RECONCILE_GRACE_MS 값 미확인 — 값이 매우 크면 (예: 1h) manual run 대부분 완료 전 reconcile 안 되어 영향 희소. 값 작으면 빈발.
- issue #68157 의 "already-running survives restart" 는 주로 runningAtMs persistence 가 원인일 가능성이 높고, 본 FIND 의 manual run 경로는 그 증상의 부수적 기여일 수 있음.

### 확인 안 한 것 중 영향 가능성

- `enqueueRun` 경로 (ops.ts:697-732) 의 커맨드 큐 처리와 isCronJobActive interaction — enqueueRun 내부 enqueueCommandInLane 이 별도 큐 매니저에 마크하는지는 allowed_paths 밖.
- `task-registry.maintenance.ts` 의 실제 TASK_RECONCILE_GRACE_MS, sweep 주기, lost 전이 조건 — 모두 R-2 로 확인 안 함.
- `structuredClone(job)` 로 executionJob 을 clone 하는 이유 — 실행 중 state 변화로부터 격리 용도로 보이나, clone 된 job.state.runningAtMs 는 preflight.now 로 고정되어 isRunnableJob 에 영향 없음.
