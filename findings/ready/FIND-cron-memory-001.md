---
id: FIND-cron-memory-001
cell: cron-memory
title: onTimer locked() 의 ensureLoaded throw 시 activeJobIds 에서 jobId 미정리 (영구 잔류)
file: src/cron/service/timer.ts
line_range: 1316-1335
evidence: "```ts\n    const completedResults: TimedCronRunOutcome[] = results.filter(\n\
  \      (entry): entry is TimedCronRunOutcome => entry !== undefined,\n    );\n\n\
  \    if (completedResults.length > 0) {\n      await locked(state, async () => {\n\
  \        await ensureLoaded(state, { forceReload: true, skipRecompute: true });\n\
  \        for (const result of completedResults) {\n          applyOutcomeToStoredJob(state,\
  \ result);\n        }\n\n        // Use maintenance-only recompute to avoid advancing\
  \ past-due\n        // nextRunAtMs values that became due between findDueJobs and\
  \ this\n        // locked block.  The full recomputeNextRuns would silently skip\n\
  \        // those jobs (advancing nextRunAtMs without execution), causing\n    \
  \    // daily cron schedules to jump 48 h instead of 24 h (#17852).\n        recomputeNextRunsForMaintenance(state);\n\
  \        await persist(state);\n      });\n    }\n```\n"
symptom_type: memory-leak
problem: cron 타이머 틱마다 due job 은 `markCronJobActive(job.id)` 로 프로세스 전역 Set `activeJobIds`
  에 등록되고, `clearCronJobActive` 는 오직 `applyOutcomeToStoredJob` 안에서만 호출된다. 그런데 `applyOutcomeToStoredJob`
  는 post-execution `locked()` 블록 안에서 `ensureLoaded(forceReload:true)` **뒤에** 실행된다.
  그 `ensureLoaded` 가 throw 하면 `for` 루프가 통째로 건너뛰어, 그 틱에서 실행된 모든 job 의 id 가 `activeJobIds`
  에 영구 잔류한다. 잔류 entry 는 메모리뿐 아니라 `hasActiveCronJobs()` 를 영구히 true 로 만들어 heartbeat
  runner 가 매번 deferral 된다.
mechanism: "1. `onTimer` 가 due job 들을 `runDueJob` 으로 실행. `runDueJob` 첫 부분(timer.ts:1262)에서\n\
  \   `markCronJobActive(job.id)` 로 전역 Set 에 등록.\n2. `runDueJob` 는 `executeJobCoreWithTimeout`\
  \ 를 try/catch 로 감싸 항상 `TimedCronRunOutcome`\n   객체를 반환 → 모든 job 이 `completedResults`\
  \ 에 들어감.\n3. 실행 종료 후 `if (completedResults.length > 0)` 블록(timer.ts:1320)에서 `locked()`\
  \ 진입,\n   첫 줄 `await ensureLoaded(state, { forceReload: true, skipRecompute: true\
  \ })` (timer.ts:1322).\n4. `ensureLoaded(forceReload:true)` 는 `loadCronStore` 로\
  \ store 파일을 디스크에서 재독한다.\n   파일이 그 사이 손상(JSON parse 실패) 되거나, 권한 변경(EACCES)/IO 오류(EIO)\
  \ 가 나거나,\n   `normalizeCronJobInput` 가 invalid-session-target 이외의 오류를 throw 하면(store.ts:88-89\n\
  \   에서 rethrow) `ensureLoaded` 가 reject.\n5. `ensureLoaded` 가 throw 하면 그 다음 `for\
  \ (const result of completedResults)\n   applyOutcomeToStoredJob(...)` 루프(timer.ts:1323-1325)는\
  \ **한 번도 실행되지 않는다**.\n6. `clearCronJobActive` 는 `applyOutcomeToStoredJob` 첫 줄(timer.ts:1085)에서만\
  \ 호출되므로,\n   그 틱에서 `markCronJobActive` 된 모든 jobId 가 `activeJobIds` 에 남는다.\n7. `onTimer`\
  \ 의 `finally` 블록(timer.ts:1336-1375)은 session reaper + `armTimer` 만 하고\n   active\
  \ job 정리는 하지 않는다. throw 는 `locked()` 를 통해 전파되어 `armTimer` 의\n   `setTimeout` 콜백\
  \ `.catch()` 에서 로깅만 되고 삼켜진다 — 프로세스는 죽지 않고 다음 틱은 정상.\n8. 잔류 entry 는 `clearCronJobActive(jobId)`\
  \ 가 동일 jobId 로 호출되는 후속 정상 틱(같은 job\n   이 다시 due → 다시 markCronJobActive → 정상 applyOutcomeToStoredJob)이\
  \ 와야 비로소 제거.\n   하지만 one-shot(`at`) job, 또는 그 사이 삭제된 job 은 다시 실행되지 않으므로 entry 가\n\
  \   프로세스 수명 내내 잔류.\n"
root_cause_chain:
- why: 왜 markCronJobActive 한 jobId 가 잔류할 수 있는가?
  because: '`clearCronJobActive` 호출이 `applyOutcomeToStoredJob` 본체 내부(timer.ts:1085)에만
    있고, 이 함수는 `locked()` 콜백에서 `ensureLoaded` 성공을 전제로 한 `for` 루프 안에서만 불린다. `markCronJobActive`(timer.ts:1262)와
    `clearCronJobActive`(timer.ts:1085)를 한 쌍으로 묶는 try/finally 가 onTimer 경로에 없다.'
  evidence_ref: src/cron/service/timer.ts:1085
- why: 왜 ensureLoaded 가 그 시점에 throw 할 수 있는가?
  because: '`ensureLoaded({forceReload:true})` 는 `loadCronStore` 로 store 파일을 디스크 재독한다.
    `loadCronStore` 는 ENOENT 만 graceful 처리(빈 store 반환)하고, JSON parse 실패는 `Failed to
    parse cron store` 로 throw, EACCES/EIO 등 ENOENT 이외 fs 오류도 rethrow 한다 (store.ts:267-273).
    또한 store.ts:85-90 의 `normalizeCronJobInput` 는 invalid-session-target 이외 오류를 rethrow
    한다.'
  evidence_ref: src/cron/store.ts:267-273
- why: 왜 ensureLoaded throw 시 for 루프가 통째로 건너뛰는가?
  because: '`await ensureLoaded(...)` 가 `locked()` 콜백의 첫 statement (timer.ts:1322)
    이고, `applyOutcomeToStoredJob` 를 도는 `for` 루프(timer.ts:1323-1325)는 그 다음 statement
    다. `ensureLoaded` 의 rejection 이 콜백을 abort 시키므로 루프는 0회 실행되고, `locked` 는 rejection
    을 그대로 전파(locked.ts:21)한다.'
  evidence_ref: src/cron/service/timer.ts:1321-1325
- why: 왜 잔류 entry 가 메모리 누수를 넘어 기능 장애를 유발하는가?
  because: '`activeJobIds` 는 `hasActiveCronJobs()` 의 백킹 스토어이고, heartbeat-runner.ts
    가 이를 호출해 cron 이 활성이면 heartbeat 를 deferral 한다. 잔류 entry 1개만 있어도 `hasActiveCronJobs()`
    가 영구 true → heartbeat 가 프로세스 수명 내내 deferral 될 수 있다.'
  evidence_ref: src/cron/active-jobs.ts:36-38
impact_hypothesis: memory-growth
impact_detail: "정성 (프로덕션 관측치 없음, 코드 기반):\n- 누수량 자체는 작다: entry 는 jobId 문자열 1개. throw\
  \ 1회당 그 틱의 due job 수만큼 잔류\n  (보통 1~수 개). 메모리 절대량 관점에선 P3~P4.\n- 그러나 잔류 entry 는 `clearCronJobActive`\
  \ 가 동일 jobId 로 다시 불려야 사라진다. 재실행되지\n  않는 job (one-shot `at` job, 또는 그 틱 이후 삭제된 job)\
  \ 의 id 는 프로세스 재시작 전까지\n  영구 잔류 — 진성 leak.\n- 더 큰 영향은 기능 측: 잔류 entry 1개로 `hasActiveCronJobs()`\
  \ 영구 true → heartbeat-runner\n  가 매 사이클 cron 활성으로 오판하여 heartbeat 를 deferral. 사용자\
  \ 입장에서 \"cron job 은\n  다 끝났는데 heartbeat 가 계속 미뤄지는\" 증상.\n- 트리거 빈도는 낮음 (store 파일\
  \ 손상/권한 오류는 드문 edge). 그래서 severity P3.\n"
severity: P3
counter_evidence:
  path: src/cron/service/timer.ts
  line: 1085-1336
  reason: "R-3 Grep (openclaw upstream/main HEAD 0c67dc7f82):\n```\nrg -n \"markCronJobActive|clearCronJobActive\"\
    \ src/cron/ -g '!*.test.ts'\n  → timer.ts:1262 markCronJobActive   (runDueJob,\
    \ onTimer 경로)\n  → timer.ts:1085 clearCronJobActive  (applyOutcomeToStoredJob\
    \ 첫 줄)\n  → timer.ts:1951 markCronJobActive   (executeJob — repo-wide 호출자 0, dead\
    \ code)\n  → timer.ts:1982 clearCronJobActive  (executeJob 마지막 줄)\n  → ops.ts:722\
    \  markCronJobActive     (prepareManualRun)\n  → ops.ts:836  clearCronJobActive\
    \    (finishPreparedManualRun finally)\nrg -n \"activeJobIds\\.(delete|clear)\"\
    \ src/cron/\n  → active-jobs.ts:26 delete (clearCronJobActive 본체)\n  → active-jobs.ts:41\
    \ clear  (resetCronActiveJobsForTests — test-only)\n```\nR-5 execution condition\
    \ 분류:\n| 경로 | 조건 | 비고 |\n|---|---|---|\n| ops.ts finishPreparedManualRun `clearCronJobActive`\
    \ | unconditional (finally) | 수동 run 경로는 try/finally 로 안전 |\n| timer.ts executeJob\
    \ `clearCronJobActive` | n/a | executeJob 은 repo 전체 호출자 0 — dead code, 무해 |\n\
    | timer.ts:1085 `clearCronJobActive` (applyOutcomeToStoredJob) | conditional-edge\
    \ | locked() 콜백에서 `ensureLoaded` 성공 후 for 루프 안에서만 실행. ensureLoaded throw 시 skip\
    \ |\n| active-jobs.ts:41 clear | test-only | resetCronActiveJobsForTests |\nonTimer\
    \ 경로에는 `markCronJobActive`/`clearCronJobActive` 를 묶는 try/finally 가 없음.\n수동 run\
    \ 경로(ops.ts)는 finally 보호가 있으므로 이 FIND 대상 아님.\n\nProduction trigger 실재 여부:\n- 트리거\
    \ caller 는 실재한다 — `onTimer` 는 cron scheduler 의 핵심 hot-path 로 매 틱 실행되고,\n  due\
    \ job 이 있으면 `markCronJobActive` → completedResults → post-execution `locked()`\
    \ 를\n  반드시 탄다. cron 이 enabled 인 모든 프로덕션 배포에서 매 틱 실행.\n- 단, leak 이 실제 발생하려면 그 `locked()`\
    \ 안의 `ensureLoaded(forceReload:true)` 가\n  throw 해야 한다. ENOENT 는 graceful 처리되므로\
    \ 제외; JSON parse 실패(store 파일 손상),\n  EACCES/EIO, `normalizeCronJobInput` 의 비-session-target\
    \ throw 가 조건. 이들은 정상\n  운영에서 드문 edge — \"흔한 트리거\" 아님. 따라서 메커니즘은 코드상 성립하고 트리거 경로\n\
    \  (onTimer)도 실재하나, 발현 빈도는 낮다 → severity P3.\n- 이미 누수를 입증하는 테스트/주석 없음. service.store-load-invalid-main-job.test.ts\
    \ /\n  service.session-reaper-in-finally.test.ts 는 인접 시나리오를 다루지만 active-jobs 잔류는\n\
    \  검증하지 않음.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-05-21'
---
# onTimer locked() 의 ensureLoaded throw 시 activeJobIds 에서 jobId 미정리 (영구 잔류)

## 문제

cron 타이머 틱(`onTimer`)은 due job 을 `runDueJob` 으로 실행하면서 시작부에서 `markCronJobActive(job.id)` (timer.ts:1262) 로 프로세스 전역 Set `activeJobIds` (active-jobs.ts:4, `resolveGlobalSingleton` 기반) 에 jobId 를 등록한다. 대응하는 `clearCronJobActive` 는 onTimer 경로에서 오직 `applyOutcomeToStoredJob` 첫 줄(timer.ts:1085) 한 곳에서만 호출된다.

`applyOutcomeToStoredJob` 는 실행 종료 후 post-execution `locked()` 블록(timer.ts:1320-1335) 안에서 `for` 루프로 호출되는데, 그 루프 **직전**에 `await ensureLoaded(state, { forceReload: true, skipRecompute: true })` (timer.ts:1322) 가 있다. 이 `ensureLoaded` 가 throw 하면 `for` 루프가 통째로 건너뛰어, 그 틱에서 `markCronJobActive` 된 모든 jobId 가 정리되지 못하고 `activeJobIds` 에 잔류한다.

## 발현 메커니즘

1. cron enabled 배포에서 `onTimer` 가 매 틱 실행, due job 이 있으면 `runDueJob` 로 실행.
2. `runDueJob` 은 시작부에서 `markCronJobActive(job.id)` (timer.ts:1262) 호출, `executeJobCoreWithTimeout` 를 try/catch 로 감싸 항상 `TimedCronRunOutcome` 반환 → 모든 job 이 `completedResults` 에 들어감.
3. `if (completedResults.length > 0)` (timer.ts:1320) 블록에서 `locked()` 진입, 첫 줄이 `await ensureLoaded(state, { forceReload: true, skipRecompute: true })`.
4. `ensureLoaded(forceReload:true)` 는 `loadCronStore` 로 store 파일을 디스크 재독한다. store 파일이 그 사이 손상(JSON parse 실패)되거나 권한/IO 오류가 나면 `loadCronStore` 가 throw (store.ts:222-225, 267-273 — ENOENT 만 graceful). `normalizeCronJobInput` 의 비-session-target throw 도 rethrow (store.ts:88-89).
5. `ensureLoaded` reject → 그 다음 `for (const result of completedResults) applyOutcomeToStoredJob(...)` (timer.ts:1323-1325) 는 0회 실행.
6. `clearCronJobActive` 는 `applyOutcomeToStoredJob` 첫 줄(timer.ts:1085)에만 있으므로 그 틱의 모든 jobId 가 `activeJobIds` 에 잔류.
7. `locked` 는 rejection 을 전파(locked.ts:21), `onTimer` 의 `finally` 블록은 session reaper + `armTimer` 만 하고 active job 정리는 안 함. throw 는 `armTimer` 의 timer 콜백 `.catch()` (timer.ts:1182-1184)에서 로깅만 되고 삼켜짐 → 프로세스 생존, 다음 틱 정상.
8. 잔류 entry 는 동일 jobId 가 다시 due 가 되어 정상 `applyOutcomeToStoredJob` 를 타야 제거됨. one-shot `at` job 이나 그 사이 삭제된 job 은 재실행되지 않아 프로세스 수명 내내 잔류.

## 근본 원인 분석

핵심은 `markCronJobActive`(획득)와 `clearCronJobActive`(해제)가 **try/finally 로 묶이지 않은** 점이다. 수동 run 경로(`ops.ts` `prepareManualRun`/`finishPreparedManualRun`)는 `clearCronJobActive(jobId)` 를 `finally` (ops.ts:835-837) 에 둬서 안전하다. 그러나 `onTimer` 의 scheduled 경로는 해제를 `applyOutcomeToStoredJob` 본체에 묻어두고, 그 함수를 `ensureLoaded` 성공을 전제로 한 `locked()` 콜백의 `for` 루프 안에서만 호출한다. `ensureLoaded` 가 콜백 첫 줄에서 reject 하면 루프 자체가 사라진다.

`activeJobIds` 는 단순 메모리 누수를 넘어, `hasActiveCronJobs()` (active-jobs.ts:36-38) 의 백킹 스토어다. heartbeat-runner.ts 가 이를 호출해 cron 활성 여부를 판단하고 활성이면 heartbeat 를 deferral 한다. 잔류 entry 1개만 있어도 `hasActiveCronJobs()` 가 영구 true → cron job 이 전부 끝났는데도 heartbeat 가 계속 미뤄지는 기능 장애로 이어진다.

## 영향

- 영향 유형: **memory-growth** (작은 절대량) + 기능 장애(heartbeat 영구 deferral).
- 누수량: throw 1회당 그 틱의 due job 수만큼 jobId 문자열 잔류. 절대량은 작다.
- 진성 leak 조건: 재실행 안 되는 job (one-shot `at`, 또는 삭제된 job) 의 id 는 프로세스 재시작 전까지 영구 잔류.
- 트리거 빈도: `onTimer` 자체는 cron hot-path 라 매 틱 실행되나, leak 발현은 그 틱의 `ensureLoaded(forceReload:true)` 가 throw 해야만 — store 파일 손상/권한 오류는 드문 edge. 그래서 severity **P3**.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 으로 `activeJobIds.(delete|clear)` 탐색. production delete 는 `clearCronJobActive` 본체(active-jobs.ts:26) 하나뿐이고, 그것을 호출하는 onTimer 경로는 `applyOutcomeToStoredJob`(timer.ts:1085) 한 곳. `clear` 는 `resetCronActiveJobsForTests` (test-only). onTimer 경로에 보상용 cleanup 없음.

**카테고리 2 (외부 경계 장치)**: `onTimer` 의 `finally` 블록(timer.ts:1336-1375)은 session reaper sweep 과 `armTimer` 만 수행. `activeJobIds` 를 건드리지 않음. `CronService.stop()` → `ops.stop` 도 timer 만 정리. 프로세스 재시작 전까지 잔류.

**카테고리 3 (호출 맥락)**: `onTimer` 는 cron scheduler 의 핵심 hot-path — cron enabled 배포에서 매 틱 실행. 트리거 caller 실재 확인됨. 단 leak 발현은 `ensureLoaded` throw 라는 추가 조건 필요.

**카테고리 4 (기존 테스트)**: `service.store-load-invalid-main-job.test.ts`, `service.session-reaper-in-finally.test.ts` 가 인접 시나리오를 다루나, post-execution `locked()` 의 `ensureLoaded` throw 시 `activeJobIds` 잔류 여부는 검증하지 않음.

**카테고리 5 (주석/의도)**: timer.ts:1320-1334 주석은 `recomputeNextRunsForMaintenance` 의 의도만 설명. active-jobs 정리 누락에 대한 "intentional" 표기 없음 — 실수로 보임.

## Self-check

### 내가 확실한 근거
- `src/cron/service/timer.ts:1262` `markCronJobActive(job.id)` (runDueJob), `1085` `clearCronJobActive` (applyOutcomeToStoredJob 첫 줄), `1320-1335` post-execution locked 블록 — Read 로 확인.
- `src/cron/store.ts:267-273` `loadCronStore` 가 ENOENT 만 graceful, 그 외 throw — Read 로 확인. `store.ts:88-89` normalizeCronJobInput rethrow.
- `src/cron/service/locked.ts:21` rejection 전파 — Read 로 확인.
- `src/cron/active-jobs.ts:36-38` `hasActiveCronJobs` 가 `activeJobIds.size>0` — Read 로 확인.
- `executeJob` (timer.ts:1939) 는 repo 전체 호출자 0 (rg 확인) — dead code 라 별도 FIND 불필요.

### 내가 한 가정
- `ensureLoaded(forceReload:true)` 가 프로덕션에서 throw 하는 빈도가 낮다는 가정 (store 파일 손상/권한 오류 기반). 만약 특정 배포에서 store 파일이 자주 손상되면 severity 상향.
- heartbeat-runner.ts 가 `hasActiveCronJobs()` true 시 heartbeat 를 deferral 한다는 것은 grep 결과(`if (n() || ...)`)와 import 관계로 추론. heartbeat-runner.ts 는 allowed_paths(src/cron/**) 밖이라 전체 본문은 trace 안 함.

### 확인 안 한 것 중 영향 가능성
- `ensureLoaded` 가 throw 한 직후 다음 틱에서 store 파일이 복구되면, 같은 job 이 다시 due 가 되어 정상 `applyOutcomeToStoredJob` 를 타며 entry 가 제거될 수 있다 (recurring job 한정). one-shot/삭제 job 만 진성 잔류.
- `heartbeat-runner.ts` 의 정확한 deferral 로직 (영구 skip 인지, 일정 횟수 후 강제 진행인지) 은 src/cron 밖이라 미확인 — 기능 영향의 정확한 강도는 불확실.
