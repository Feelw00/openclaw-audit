---
id: FIND-cron-concurrency-003
cell: cron-concurrency
title: executeJob 이 runningAtMs 선점 검사 없이 바로 덮어써, 타이머와 수동 run 동시 실행 가능
file: src/cron/service/timer.ts
line_range: 1309-1351
evidence: "```ts\nexport async function executeJob(\n  state: CronServiceState,\n\
  \  job: CronJob,\n  _nowMs: number,\n  _opts: { forced: boolean },\n) {\n  if (!job.state)\
  \ {\n    job.state = {};\n  }\n  const startedAt = state.deps.nowMs();\n  job.state.runningAtMs\
  \ = startedAt;\n  job.state.lastError = undefined;\n  markCronJobActive(job.id);\n\
  \  emit(state, { jobId: job.id, action: \"started\", runAtMs: startedAt });\n\n\
  \  let coreResult: {\n    status: CronRunStatus;\n    delivered?: boolean;\n  }\
  \ & CronRunOutcome &\n    CronRunTelemetry;\n  try {\n    coreResult = await executeJobCoreWithTimeout(state,\
  \ job);\n  } catch (err) {\n    coreResult = { status: \"error\", error: String(err)\
  \ };\n  }\n\n  const endedAt = state.deps.nowMs();\n  const shouldDelete = applyJobResult(state,\
  \ job, {\n    status: coreResult.status,\n    error: coreResult.error,\n    delivered:\
  \ coreResult.delivered,\n    startedAt,\n    endedAt,\n  });\n\n  emitJobFinished(state,\
  \ job, coreResult, startedAt);\n\n  if (shouldDelete && state.store) {\n    state.store.jobs\
  \ = state.store.jobs.filter((j) => j.id !== job.id);\n    emit(state, { jobId: job.id,\
  \ action: \"removed\" });\n  }\n  clearCronJobActive(job.id);\n}\n```\n"
symptom_type: concurrency-race
problem: '`executeJob`(수동 `run` 커맨드 및 내부 호출 사용) 은 시작 시

  `job.state.runningAtMs = startedAt` 을 조건 없이 덮어쓴다. 같은 프로세스의 onTimer 경로는

  `locked(...)` 블록 안에서 `runningAtMs` 를 claim 하지만, executeJob 경로는 해당 locked 블록

  바깥에서 독립적으로 필드를 대입한다. 결과적으로 (a) onTimer 가 job 을 선점한 직후 사용자가

  CLI/gateway-API 로 `run` 을 호출하거나 (b) runDueJobs → executeJob 과 onTimer 가 동시에 같은

  job 을 선택하면, 동일 job 이 병렬로 2회 실행되고 나중에 끝나는 실행이 persist 단계에서 앞선

  실행의 lastStatus / lastRunAtMs / nextRunAtMs 를 덮어쓴다.

  '
mechanism: "1. T0: onTimer 가 locked 안에서 job A 의 runningAtMs=T0 세팅, persist 완료, locked\
  \ exit.\n2. T0+ε: onTimer 바깥의 runDueJob Promise 가 executeJobCoreWithTimeout(job\
  \ A) 실행 시작\n   (timer.ts:748).\n3. T0+ε': 사용자가 `openclaw cron run A` 호출 → ops.ts\
  \ 경로로 executeJob(timer.ts:1309) 진입.\n   → 이 함수는 runningAtMs 를 무조건 startedAt 으로 덮어씀\
  \ (timer.ts:1319).\n   → locked 블록도 없고 executeJobCoreWithTimeout 이 진행 중인지도 확인하지\
  \ 않음.\n4. 동일 job 이 두 coroutine 에서 병렬 실행 → 외부 side effect(메시지 전송, systemEvent 큐잉,\n\
  \   taskRun 생성) 중복.\n5. 먼저 끝난 실행이 applyJobResult → persist 로 lastStatus 기록 → 나중에\
  \ 끝난 실행이 다시\n   applyJobResult → persist 로 덮어씀. run-log/task-run 레코드가 이중 작성.\n"
root_cause_chain:
- why: 왜 executeJob 은 locked() 로 감싸지 않았는가?
  because: executeJob 자체는 긴 agentTurn 을 포함할 수 있어 locked 로 감싸면 onTimer 가 그 동안 blocking.
    실행 중에는 store 접근을 하지 않으므로 의도적으로 lock 밖에서 돌리는 구조
  evidence_ref: src/cron/service/timer.ts:1309-1333
- why: 왜 시작 시 runningAtMs 를 conditional CAS(미설정 시에만 대입) 로 하지 않는가?
  because: '함수가 `forced: boolean` 옵션을 받지만 내부적으로 사용하지 않고(변수명 `_opts`), 조건 분기가 없다. onTimer
    가 이미 claim 한 상태인지 확인하는 guard 가 없음'
  evidence_ref: src/cron/service/timer.ts:1313-1319
- why: 왜 ops.ts 의 호출자가 runningAtMs preflight 를 하지 않는가?
  because: ops.ts:587-590 은 stuck runningAtMs 를 clear 하고 새로 대입하는 경로가 있으나, executeJob
    직접 호출 경로(runDueJobs 등)는 동일 preflight 없이 바로 진입. runDueJobs(timer.ts:1118-1126)
    도 due job 을 순차 executeJob 호출
  evidence_ref: src/cron/service/timer.ts:1118-1126
- why: 왜 테스트가 잡지 못했나?
  because: service.rearm-timer-when-running.test.ts 는 runningAtMs 로 인해 skip 되는 경로만
    검증하고, '수동 run 도중 timer 가 트리거되면 중복 실행되는지' 는 커버하지 않는다
  evidence_ref: N/A — cron 테스트 파일 전수조사 결과 manual-run × timer 동시성 재현 없음
impact_hypothesis: wrong-output
impact_detail: '정성: 사용자 수동 `openclaw cron run <id>` 와 스케줄 기반 tick 이 근접 타이밍에 겹치면 같은
  job 이

  병렬 실행되어 (1) 외부 메시지/heartbeat 중복 발송, (2) task-executor 가 같은 jobId 로 두 개의

  taskRun 생성, (3) run-log 레코드 이중. 1분 이하 짧은 주기 cron 에서 사용자가 "즉시 한 번 더

  돌려보자" 로 run 을 호출하면 현실적 재현 가능.

  정량: 타이밍 window ≈ onTimer lock-exit ~ executeJobCoreWithTimeout 완료 사이 (agentTurn
  은

  수 초~수 분). 이 window 안에 manual run 이 오면 100% 중복.

  '
severity: P2
counter_evidence:
  path: src/cron/service/timer.ts
  line: '1319'
  reason: "R-3 Grep 결과:\n(1) `rg -n \"Redisson|redlock|Redis.*lock|distributed.*lock|file.*lock\"\
    \ src/` →\n    cron 경로에 process-local 이상의 락 없음.\n(2) `rg -n \"clearTimeout\\(|AbortController|signal\\\
    .abort\" src/cron/` →\n    AbortController 는 timeout 용이고 중복 실행 guard 아님. signal.abort\
    \ 는\n    executeJobCoreWithTimeout 내부 타임아웃 경로(timer.ts:93).\n(3) `rg -n \"heartbeat|liveness|stuck.*recover\"\
    \ src/cron/` →\n    ops.ts 가 stuck runningAtMs 를 `clear + 새로 대입` 하는 preflight(529-590)\
    \ 가 있지만\n    이는 수동 trigger 의 \"stale marker 제거\" 목적이며, \"onTimer 가 방금 claim 한\
    \ fresh\n    marker\" 를 보호하지 않는다. 두 경로의 runningAtMs 모두 동일 필드를 공유 → 구분 불가.\n결론:\
    \ executeJob 의 preflight/guard 부재가 맞다.\n반증 후보: ops.ts:587-590 의 preflight 가 최근\
    \ runningAtMs 를 잠시 보호할 수 있으나,\npreflight 는 ops 의 특정 진입점에만 있고 runDueJobs 직접 호출\
    \ 경로에는 없음\n(timer.ts:1118-1126 참조).\n"
status: discovered
discovered_by: cron-reliability-auditor
discovered_at: '2026-04-18'
cross_refs:
- FIND-cron-concurrency-001
---
# executeJob 이 runningAtMs 선점 검사 없이 바로 덮어써, 타이머와 수동 run 동시 실행 가능

## 문제

`src/cron/service/timer.ts` 의 `executeJob` (line 1309) 은 cron job 을 실행하는 공개 함수로,
CLI 수동 `run`, 일부 테스트 헬퍼, 그리고 `runDueJobs`(line 1118-1126) 에서 호출된다. 함수는
진입 즉시 `job.state.runningAtMs = startedAt` 을 조건 없이 대입한다 (line 1319). `locked()`
블록도 없고, "다른 경로가 이미 이 job 을 실행 중인가?" 를 묻는 가드도 없다.

onTimer 경로는 locked 블록 내부에서 `runningAtMs = now` 로 claim 한 뒤 locked 를 빠져나와
`executeJobCoreWithTimeout` 을 호출한다 (timer.ts:703-748). 이 시점부터 executeJobCore 가
끝날 때까지 store 에는 "runningAtMs 세팅됨, 실제 실행 진행 중" 상태가 지속된다. 이 window 에
사용자/다른 코드가 `executeJob` 경로로 진입하면 가드 없이 runningAtMs 만 새 값으로 덮어쓰고
병렬 실행을 시작한다.

## 발현 메커니즘

```
시각       onTimer (T=가정: locked 진입)           executeJob (수동 run)
─────      ──────                                    ──────────
T0         locked 진입
T0+1ms     ensureLoaded → due=[A]
T0+2ms     A.runningAtMs = T0; persist
T0+3ms     locked exit
T0+4ms     executeJobCoreWithTimeout(A) 시작
           (agentTurn, 수 초~분)
                                                     executeJob(A) 진입 (수동 run)
T0+5ms                                               A.runningAtMs = T0+5ms  ← 덮어씀
                                                     executeJobCoreWithTimeout(A) 시작
                                                     (또 다른 agentTurn 병렬 실행)

... 두 coroutine 모두 외부 side effect 발생 ...

T0+12s     첫 번째 완료 → applyJobResult(lastStatus=ok, lastRunAtMs=T0)
                                                     T0+15s 두 번째 완료 → applyJobResult(덮어씀)
                                                       lastStatus, lastRunAtMs, nextRunAtMs 재기록
```

외부에서 관측되는 증상:
- 메시지/heartbeat 2번 전송.
- task-executor 가 동일 jobId 로 두 개의 taskRun 레코드를 생성 (timer.ts:745 `tryCreateCronTaskRun`
  가 두 번 호출).
- run-log 도 이중 기록.
- 최종 persist 결과는 늦게 끝난 실행 것 → 유저가 보는 UI 에서 실제 최초 실행 결과가 사라짐.

## 근본 원인 분석

1. **executeJob 이 locked 밖에서 구동** — 의도적 설계. 긴 agentTurn 을 lock 안에 가두면
   onTimer 가 blocking. 대신 "runningAtMs 가 claim guard 역할" 을 한다는 가정인데, executeJob
   자체가 이 guard 를 확인하지 않으므로 가정이 깨짐 (timer.ts:1315-1320).
2. **`forced` 옵션이 실질 미사용** — 시그니처는 `_opts: { forced: boolean }` 를 받지만 body 에서
   참조 없음 (underscore prefix). `forced=false` 일 때 "이미 runningAtMs 가 세팅됐으면 skip" 같은
   기본 보호 없음.
3. **runDueJobs 경로도 동일 취약** — `runDueJobs`(timer.ts:1118-1126) 는 `collectRunnableJobs`
   결과를 순차 `executeJob` 에 넘기는데, 이 collectRunnableJobs 결과가 stale 일 수 있고
   (locked 밖에서 호출), executeJob 내부도 가드가 없으므로 runDueJobs 가 별개 타이밍에 호출되면
   동일 race.
4. **테스트가 skip 방향만 검증** — `service.rearm-timer-when-running.test.ts` 및 관련 regression
   들은 runningAtMs 세팅 상태에서 timer 가 skip/rearm 하는 시나리오만 본다. "executeJob 이
   runningAtMs 존재 여부와 무관하게 선점을 덮어쓴다" 는 부작용은 테스트 부재.

## 영향

- **가장 가능성 높은 결과**: wrong-output — 메시지/heartbeat 중복 전송, task-run 중복 생성,
  run-log 이중 기록으로 통계 왜곡.
- **정량 추정**: 타이밍 window = (onTimer 의 locked exit) ~ (executeJobCoreWithTimeout 완료) 구간.
  agentTurn 이 10~60s 수준이면 사용자가 그 안에 `run` 을 호출할 때마다 100% 재현. 1시간당 수회
  이상 수동 trigger 하는 사용자에게 현실 위협.
- **재현 시나리오**:
  1. 10초 이상 걸리는 agentTurn 기반 job 등록.
  2. 스케줄이 막 지나 onTimer 가 해당 job 을 pick 한 직후(<1s) 사용자가 `openclaw cron run <id>` 호출.
  3. 두 번 실행 관측 (CLI stdout, 메시지 채널, run-log).
- **노출 경로**: CLI 수동 run, gateway API 의 cron run endpoint, 자동화 스크립트가 주기적으로
  run 을 호출하는 환경.

## 반증 탐색

- **preflight 존재 여부**: `src/cron/service/ops.ts:529-590` 에서 `runningAtMs` 가 세팅된 상태에서
  preflight 로 stale marker 를 clear 한 뒤 새 값으로 덮어쓰는 코드가 있다 (stale 판정 후). 이
  preflight 는 "일부 공개 ops" 경로(stuck marker clearing) 에 한정되고 `executeJob` 진입 전의
  가드는 아니다. 실제로 preflight 는 `STUCK_RUN_MS` 초과 여부만 보므로 방금(T0+2ms) 세팅된
  fresh marker 는 stale 로 판정되지 않는다. → 보호 없음.
- **abort 경로**: executeJobCoreWithTimeout 에는 AbortController 가 있지만 이는 timeout 용이고
  "다른 경로가 선점했으니 abort" 용도가 아니다 (timer.ts:86-102).
- **기존 테스트**: 앞서 언급한 테스트들 모두 run vs timer 레이스 미커버. `service.prevents-
  duplicate-timers.test.ts` 는 timer 재무장 중복만 본다.
- **상위 호출자 책임 위임 여부**: ops.ts 에서 executeJob 을 호출하기 전 locked 로 preflight 하는
  경로가 있지만, preflight 가 끝난 뒤 executeJob 자체는 locked 밖에서 실행되므로 preflight 와
  execute 사이에 다른 프로세스/코루틴이 claim 을 가로챌 수 있다.
- **설정/flag**: `cronConfig` 에 "prevent concurrent run" 같은 flag 는 allowed_paths 내 파일에서
  확인 안 됨.

## Self-check

### 내가 확실한 근거
- `executeJob`(timer.ts:1309-1351) 이 진입 즉시 `runningAtMs = startedAt` 을 무조건 대입하며
  locked 블록 없음.
- onTimer 경로는 locked 에서 claim 후 locked 를 빠져나와 실행 (timer.ts:703-748).
- 두 경로가 같은 `CronJobState.runningAtMs` 필드를 공유.

### 내가 한 가정
- ops.ts 의 preflight 가 `STUCK_RUN_MS` 초과 판정에만 의존한다는 이해 (FIND-002 의 분석과 일치).
  만약 preflight 경로가 locked + conditional 로 runningAtMs 를 원자 교체한다면 일부 공개 ops
  진입점에서는 이 race 가 완화됨.
- 수동 `run` 커맨드가 실제로 `executeJob` 을 호출한다고 가정 (ops.ts 에 `executeJob` 호출자 존재
  여부는 파일 내 Grep 결과로 식별했으나 상세 경로는 추적 미완).

### 확인 안 한 것 중 영향 가능성
- ops.ts 의 `run` 공개 API 가 executeJob 대신 별도 wrapper 에서 locked 로 보호할 가능성 — 그렇다면
  이 FIND 는 runDueJobs 경로만 유효하고 severity 가 낮아짐. ops.ts 전체 라인 580~650 추가 확인
  필요.
- Gateway 수준에서 동일 jobId 에 대한 run 요청을 singleflight 로 묶는 middleware 가 있을 가능성
  — allowed_paths 밖이라 확인 불가.
