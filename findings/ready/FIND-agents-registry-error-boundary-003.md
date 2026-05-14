---
id: FIND-agents-registry-error-boundary-003
cell: agents-registry-error-boundary
title: registerSubagentRun swallows createRunningTaskRun throw; split state
file: src/agents/subagent-registry-run-manager.ts
line_range: 424-453
evidence: "```ts\n    params.runs.set(runId, entry);\n    try {\n      createRunningTaskRun({\n\
  \        runtime: \"subagent\",\n        sourceId: runId,\n        ownerKey: requesterSessionKey,\n\
  \        scopeKind: \"session\",\n        requesterOrigin,\n        childSessionKey,\n\
  \        runId,\n        label: registerParams.label,\n        task: registerParams.task,\n\
  \        deliveryStatus:\n          registerParams.expectsCompletionMessage ===\
  \ false ? \"not_applicable\" : \"pending\",\n        startedAt: now,\n        lastEventAt:\
  \ now,\n      });\n    } catch (error) {\n      log.warn(\"Failed to create background\
  \ task for subagent run\", {\n        runId: registerParams.runId,\n        error,\n\
  \      });\n    }\n    params.ensureListener();\n    params.persist();\n    // Always\
  \ start sweeper — session-mode runs (no archiveAtMs) also need TTL cleanup.\n  \
  \  params.startSweeper();\n    // Wait for subagent completion via gateway RPC (cross-process).\n\
  \    // The in-process lifecycle listener is a fallback for embedded runs.\n   \
  \ void waitForSubagentCompletion(runId, waitTimeoutMs, entry);\n```\n"
symptom_type: error-boundary-gap
problem: "`registerSubagentRun` (run-manager.ts:374-454) 은 새 subagent run 을 등록할 때:\n\
  1. line 424 `params.runs.set(runId, entry)` 로 in-memory Map 에 entry 적재.\n2. line\
  \ 425-446 `try { createRunningTaskRun(...) } catch (error) { log.warn\n   (...)\
  \ }` 로 background task record 생성 시도. 실패 시 warn 만 찍고 진행.\n3. line 447-453 ensureListener\
  \ / persist / startSweeper / waitForSubagentCompletion\n   계속 진행.\n\n문제: createRunningTaskRun\
  \ 이 throw 하면 *subagent run 은 등록되지만 task 추적\nrecord 는 없는 split state* 가 된다. 이후 lifecycle\
  \ 의 모든 task 갱신 호출\n(lifecycle.ts:157 setDetachedTaskDeliveryStatusByRunId, lifecycle.ts:182\n\
  completeTaskRunByRunId, lifecycle.ts:193 failTaskRunByRunId, run-manager.ts:543\n\
  persistSubagentSessionTiming) 이 runId 로 task 를 찾지 못해 silent no-op 된다\n(task-executor.ts:172\
  \ finalizeTaskRunByRunId 가 empty array 리턴 — registry\n에 record 없으면 변경 사항 없음). 사용자\
  \ task UI 에 해당 subagent 가 영원히\n보이지 않으며, 외부 deliveryStatus polling 도 deliveryStatus\
  \ 변경을 감지하지\n못한다.\n\ncatch 가 log.warn 으로 진단은 남기지만, *복구 시도 없음 + 후속 작업 진행으로\ninconsistent\
  \ state 영구화*. partial-state 를 감지/롤백할 수 있는 healthcheck\n도 없다.\n"
mechanism: "1. caller (sessions-spawn-tool.ts:446 또는 subagent-spawn.ts:1241) 가\n \
  \  registerSubagentRun(params) 호출.\n2. line 424 `params.runs.set(runId, entry)`\
  \ — in-memory subagent registry 에\n   entry 적재 완료. 이후 모든 lifecycle event / sweeper\
  \ / resume 가 이 entry\n   를 인식.\n3. line 425-440 createRunningTaskRun({runtime: \"\
  subagent\", sourceId: runId,\n   runId, ...}) 호출. 내부 createTaskRecord (task-executor.ts:110)\
  \ 가 임의\n   이유로 throw 가능 (e.g. task store FS error, validation failure,\n   ensureSingleTaskFlow\
  \ 의 enqueue conflict).\n4. catch (line 441-446) 가 `log.warn(\"Failed to create background\
  \ task for\n   subagent run\", ...)` 만 찍고 swallow.\n5. line 447 ensureListener /\
  \ line 448 persist / line 450 startSweeper /\n   line 453 void waitForSubagentCompletion\
  \ 그대로 진행. subagent registry\n   입장에선 정상 등록 완료.\n6. 사용자의 subagent 가 정상 완료되면 lifecycle\
  \ event 발사 → listener →\n   completeSubagentRun → safeFinalizeSubagentTaskRun (lifecycle.ts:174-212):\n\
  \   - line 181-191 completeTaskRunByRunId({runId, runtime: \"subagent\",\n     sessionKey,\
  \ endedAt, ...}) 호출.\n   - task-executor.ts:162-176 finalizeTaskRunByRunId → registry\
  \ 에 record\n     없으므로 빈 array 리턴. 변경 사항 없음. throw 안 함.\n   - safeFinalizeSubagentTaskRun\
  \ 의 try/catch 도 throw 검출 못함 (애초에\n     throw 없음). warn 도 안 찍힘.\n7. safeSetSubagentTaskDeliveryStatus\
  \ 도 동일하게 silent no-op (lifecycle.ts:\n   150-172). entry.deliveryStatus=\"delivered\"\
  \ 가 task record 에 propagate\n   안 됨.\n8. 사용자 task UI 는 해당 subagent 의 \"running →\
  \ completed\" transition 을\n   영원히 못 본다. 디스크에는 subagent registry 의 entry 만 남고 task\
  \ store\n   에는 없다.\n"
root_cause_chain:
- why: 왜 task record 생성 실패가 subagent 등록을 막지 않는가
  because: 'catch 본문이 log.warn 만 수행하고 break/return/abort 없이 fall-through

    한다. 즉 "task 추적은 best-effort, subagent 자체는 진행" 이라는 design

    intent 가 있다.

    '
  evidence_ref: src/agents/subagent-registry-run-manager.ts:441-446
- why: 왜 design intent 가 inconsistent state 를 허용하는가
  because: 'subagent registry 와 detached-task registry 가 분리된 자료구조 (memory.ts

    의 subagentRuns Map vs tasks/task-registry.ts) 인데 둘 사이의 정합성을

    enforce 하는 contract 가 함수 호출 순서로만 표현된다. transactional

    rollback 패턴 없음.

    '
  evidence_ref: src/agents/subagent-registry-memory.ts:1-3
- why: 왜 downstream 의 task 갱신이 inconsistency 를 감지 못 하는가
  because: 'task-executor.ts 의 *ByRunId 함수들이 registry 에 record 없을 때 빈 array

    를 silent 리턴하지 throw 하지 않는다. 즉 contract 가 "있어도 되고 없어도

    된다" 의 partial-write 허용 형태.

    '
  evidence_ref: src/tasks/task-executor.ts:162-176
- why: 왜 retry / compensating action 이 없는가
  because: 'registerSubagentRun 은 sync 함수로 caller 에 return type 없음 (line 374

    도 void return). 실패 신호 전달 채널이 log.warn 뿐 → 운영 모니터링

    도구가 이를 actionable 로 catch 해야 하지만 그런 alerting 미 enforce.

    '
  evidence_ref: src/agents/subagent-registry-run-manager.ts:374
impact_hypothesis: data-loss
impact_detail: "정성: subagent 자체는 정상 동작하지만 사용자 시점에서 'background task UI'\n에 영원히 보이지\
  \ 않는다. completeTaskRunByRunId / failTaskRunByRunId /\nsetDetachedTaskDeliveryStatusByRunId\
  \ 호출이 silent no-op 되어 task 의\nstatus / deliveryStatus 정보가 영원히 \"초기 미생성\" 상태. 외부\
  \ polling 이나\n통합 UI 가 \"이 작업이 어떻게 종료됐는가\" 를 알 수 없다. data-loss 범주.\n\n재현 시나리오 (정성):\n\
  1. task store (task-registry persistence) 가 disk full / FS quota 초과 /\n   write\
  \ protection 상태.\n2. 사용자가 subagent spawn → registerSubagentRun 진입.\n3. line 424\
  \ subagentRuns Map 적재 성공 (메모리 only).\n4. line 426 createRunningTaskRun 진입 — 내부 createTaskRecord\
  \ 가 disk 에\n   persist 시도 시 throw.\n5. catch 가 warn 만 찍고 진행.\n6. subagent 작업 N분\
  \ 진행 후 완료 → lifecycle event → completeSubagentRun →\n   safeFinalizeSubagentTaskRun\
  \ → 빈 array 리턴.\n7. 사용자: \"spawn 했는데 task list 에 아무것도 없네?\" → 사실 subagent 는\n  \
  \ 이미 끝났지만 추적 record 가 없어 UI 가 비어 있다.\n\n빈도: task store 의 throw 빈도에 비례. 정상 환경에서는\
  \ 드물지만 disk\npressure / quota 환경에서는 재현 가능. P3 적정.\n"
severity: P3
counter_evidence:
  path: src/agents/subagent-registry-lifecycle.ts
  line: 150-212
  reason: "R-3 Grep 결과:\n1. `rg -n \"try\\s*\\{|catch\\s*\\(|\\.catch\\(\" src/agents/subagent-registry*.ts`:\n\
    \   run-manager.ts:425-446 의 try/catch 가 createRunningTaskRun 만 보호.\n   이후 ensureListener\
    \ / persist / startSweeper / waitForSubagentCompletion\n   호출은 try 바깥. 만약 ensureListener\
    \ 가 throw 하면 caller 까지 escalate\n   (subagent register 자체 abort) — 별도 결함. 본 FIND\
    \ 는 catch 처리된 throw\n   의 partial-state 잔존.\n2. `rg -n \"throw new|throw err|throw\
    \ error\" src/agents/subagent-registry*.ts\n   src/agents/live-cache-test-support.ts`:\n\
    \   - subagent-registry-helpers.ts:200, 236 — fs.realpath 의 ENOENT 외\n     re-throw.\
    \ (별도 영역)\n   - subagent-registry.store.ts:207 — disk write throw. (createTaskRecord\n\
    \     경로 무관)\n   createRunningTaskRun 내부의 명시적 throw 는 본 grep 범위 밖 (tasks/) 이지만\n\
    \   task-executor.ts 의 createTaskRecord → task-registry 의 persist 가\n   throw\
    \ 가능.\n3. 4-caller 검사 (R-5): registerSubagentRun 의 caller:\n   - src/agents/tools/sessions-spawn-tool.ts:446\
    \ (`registerSubagentRun(...)`)\n   - src/agents/subagent-spawn.ts:1241 (`registerSubagentRun(...)`)\n\
    \   두 caller 모두 return value 없는 sync 호출. throw 받지 않으므로 caller\n   가 inconsistency\
    \ 감지 불가. primary-path inversion 없음 — 모든 caller 가\n   catch 본문의 silent-warn 효과를\
    \ 그대로 수용.\n4. defense-in-depth:\n   - safeFinalizeSubagentTaskRun / safeSetSubagentTaskDeliveryStatus\n\
    \     (lifecycle.ts:150-212) 가 task 갱신 호출에 try/catch. 그러나 task\n     registry\
    \ 에 record 없을 때 silent no-op 라 catch 의 onError 경로 자체\n     미진입.\n   - task-registry\
    \ / detached-task-runtime 의 lost-task sweeper 가 별도\n     존재 (tasks/task-registry.ts\
    \ 의 markTaskRunLostById 등) — 단,\n     createRunningTaskRun 자체가 미실행이라 lost 분류도\
    \ 안 됨.\n5. CAL-008 upstream 검사:\n   - `gh pr list --repo openclaw/openclaw --state\
    \ open --search \"subagent\n     task background\"`: 직접 매칭 PR 없음.\n   - PR #75462\
    \ (waitForSubagentCompletion silent catch) 와 다른 axis.\n   - PR #80544 \"Add native\
    \ subagent completion ownership\" (anyech, OPEN)\n     는 ownership 모델 변경 — 본 FIND\
    \ 의 createTaskRun 실패 시 partial-\n     state 와 직접 충돌 안 함.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: 2026-05-14
cross_refs: []
related_tests:
- src/agents/subagent-registry.test.ts
- src/agents/subagent-registry.persistence.test.ts
---
# registerSubagentRun swallows createRunningTaskRun throw; split state

## 문제

`registerSubagentRun` (run-manager.ts:374-454) 은 새 subagent run 의 등록 시:
1. line 424 `params.runs.set(runId, entry)` — in-memory subagent Map 에 entry
   적재.
2. line 425-446 `try { createRunningTaskRun(...) } catch (error) {
   log.warn(...) }` — background task record 생성 시도, 실패 시 warn 만 출력.
3. line 447-453 — ensureListener / persist / startSweeper /
   waitForSubagentCompletion 그대로 진행.

createRunningTaskRun 이 throw 하면 *subagent registry 에는 등록되었지만 task
registry 에는 record 없음* 의 split state 가 영구화된다. 이후 모든 task 갱신
경로 (lifecycle.ts:157 setDetachedTaskDeliveryStatusByRunId, lifecycle.ts:182
completeTaskRunByRunId, lifecycle.ts:193 failTaskRunByRunId) 가 task-executor.ts
의 *ByRunId 함수에서 빈 array 를 silent 리턴 — record 가 없으니 변경 사항 없음.

결과: 사용자 task UI 는 해당 subagent 를 영원히 못 본다. 외부 polling 의
deliveryStatus 도 변경 안 됨. subagent 자체는 정상 spawn/완료되지만 추적 신호가
끊긴다.

## 발현 메커니즘

1. caller (sessions-spawn-tool.ts:446, subagent-spawn.ts:1241) 가
   registerSubagentRun(params) 호출.
2. run-manager.ts:424 `params.runs.set(runId, entry)` — subagent registry 적재
   완료.
3. line 425-440 createRunningTaskRun 호출. 내부 task-executor.ts:109-118
   createTaskRecord + ensureSingleTaskFlow. 가능한 throw 원인:
   - task store FS 쓰기 실패 (disk full, FS quota, permission)
   - validation failure (필수 필드 누락)
   - ensureSingleTaskFlow 의 enqueue conflict (단일 flow 보장 위반)
4. catch (line 441-446) 가 log.warn("Failed to create background task for
   subagent run") 만 찍고 swallow. 복구/롤백 시도 없음.
5. line 447-453 ensureListener / persist / startSweeper /
   waitForSubagentCompletion 진행. subagent 입장에선 정상 등록.
6. subagent 작업 진행 후 완료 → lifecycle event "end" → registry.ts:897
   listener IIFE → completeSubagentRun (lifecycle.ts:765-886).
7. lifecycle.ts:823 safeFinalizeSubagentTaskRun({entry, outcome}) 호출 →
   line 181-191 completeTaskRunByRunId / line 193-203 failTaskRunByRunId →
   task-executor.ts:172 finalizeTaskRunByRunId → task-registry 에 record
   없으므로 빈 TaskRecord[] 리턴. 외부 callback 호출 없음. throw 안 함.
8. safeFinalizeSubagentTaskRun 의 try/catch (line 204-211) 도 진입 안 함
   (애초에 throw 없음). warn log 도 안 찍힘.
9. lifecycle.ts:153 safeSetSubagentTaskDeliveryStatus 도 동일하게 silent no-op.
10. 사용자 task UI 는 빈 상태 영구.

## 근본 원인 분석

1. **catch 본문이 warn 만 수행하고 진행** (run-manager.ts:441-446): 코드 의도가
   "task 추적은 best-effort" 이지만, 실제 효과는 *영구 split state*. catch 후
   `return` 또는 `params.runs.delete(runId); return;` 로 롤백할 수도 있었다.

2. **subagent registry 와 task registry 의 정합성 unenforced**: 두 자료구조
   (subagent-registry-memory.ts:1-3 vs tasks/task-registry.ts) 가 분리된 in-
   memory store. 둘 사이 invariant 가 함수 호출 순서로만 표현되며 transactional
   pattern (e.g. compensating action, two-phase commit) 없음.

3. **downstream silent no-op**: task-executor.ts:162-176 `finalizeTaskRunByRunId`
   가 runId 로 못 찾으면 throw 가 아닌 빈 array 리턴. 즉 contract 가
   "record 가 있어도 없어도 OK" 형태로 partial-write 를 허용. 이는 다른 정상
   use-case (이미 cleanup 된 runId 의 redundant call) 에는 합리적이지만,
   "create 실패 후 update" 시나리오를 구분하지 못한다.

4. **registerSubagentRun 의 return type 없음** (run-manager.ts:374): sync void
   리턴이라 caller (sessions-spawn-tool.ts, subagent-spawn.ts) 가 실패 신호를
   못 받음. 유일한 신호가 log.warn 뿐 → 자동화된 actionable alert 불가.

## 영향

`impact_hypothesis: data-loss` — subagent 자체는 정상 동작하나 *사용자 시점의
task 추적 정보 손실*. 사용자가 spawn 한 작업이 task UI 에 영원히 안 보임. 외부
시스템이 deliveryStatus 를 polling 하면 변경 신호 없이 stuck.

재현 시나리오 (정성):
1. task store (task-registry persistence) 가 disk full / FS quota 초과 /
   write protection.
2. 사용자가 subagent spawn → registerSubagentRun 진입.
3. line 424 subagentRuns Map 적재 성공.
4. line 426 createRunningTaskRun → task-registry persist 시도 시 throw.
5. catch 가 warn 만 찍고 진행. ensureListener / startSweeper 등 성공.
6. subagent N 분 진행 후 정상 완료 → lifecycle event → completeSubagentRun →
   safeFinalizeSubagentTaskRun → task-registry 에 record 없으므로 빈 array
   리턴. 변경 사항 없음.
7. 사용자: 작업 끝났지만 task UI 빈 상태. 외부 polling 도 변경 신호 없음.

빈도: task store throw 빈도에 비례. 정상 환경에서는 드물지만 disk pressure /
FS quota / write protection 시 재현 가능. P3 적정.

## 반증 탐색

R-3 Grep 결과:
1. `rg -n "try\s*\{|catch\s*\(|\.catch\(" src/agents/subagent-registry*.ts`:
   run-manager.ts:425-446 의 try/catch 가 createRunningTaskRun 만 wrap.
2. `rg -n "throw new|throw err|throw error" src/agents/subagent-registry*.ts
   src/agents/live-cache-test-support.ts`:
   - subagent-registry-helpers.ts:200, 236 (fs.realpath ENOENT 외 re-throw)
   - subagent-registry.store.ts:207 (disk write throw)
   createTaskRecord 내부의 throw 경로는 tasks/ 영역으로 본 audit 범위 밖이지만
   sufficient evidence — task-executor.ts:109-118 의 createTaskRecord 가
   persist 단계에서 throw 가능 (out-of-scope deep-read).
3. `rg -n "console\.|log\.|emit\.(error|warn)"
   src/agents/subagent-registry*.ts`:
   run-manager.ts:442 `log.warn("Failed to create background task for subagent
   run")` — 진단 신호는 있으나 actionable alert 미연결.
4. 4-caller 검사 (R-5 silent catch):
   `rg -n "registerSubagentRun" src/ --type ts | grep -v test`:
   - src/agents/tools/sessions-spawn-tool.ts:446
   - src/agents/subagent-spawn.ts:1241
   - src/agents/subagent-registry.ts:1018-1019 (re-export wrapper)
   - src/agents/subagent-registry-run-manager.ts:374 (정의)
   모든 production caller (sessions-spawn-tool, subagent-spawn) 가 sync void
   호출로 throw 받지 않음. primary-path inversion 없음.
5. defense-in-depth:
   - safeFinalizeSubagentTaskRun / safeSetSubagentTaskDeliveryStatus 의
     try/catch 는 task 갱신 호출의 throw 만 잡음. silent no-op 는 catch 진입
     안 함.
   - task-registry 의 lost-task sweeper (markTaskRunLostById 등) 는 *기존*
     task record 의 stuck 처리. record 자체가 미생성이면 sweeper 도 못 잡음.
6. CAL-008 upstream 검사:
   - `gh pr list --repo openclaw/openclaw --state open --search "subagent
     task"`: PR #80544 "Add native subagent completion ownership" (anyech,
     OPEN, 2026-05-11) — ownership 모델 변경. 본 FIND 의 partial-state
     시나리오와 직접 충돌 안 함.
   - PR #75462, #76332, #68669, #54765 모두 다른 axis.
   - 본 FIND 의 axis (createRunningTaskRun 실패 후 split state) 는
     OPEN PR 중 미커버.

## Self-check

### 내가 확실한 근거

- run-manager.ts:374-454 함수 전체를 직접 읽음. line 424 set, line 425-446
  try/catch, line 447-453 fall-through 확인.
- task-executor.ts:109-118 `createRunningTaskRun` 가 createTaskRecord +
  ensureSingleTaskFlow 호출함을 확인.
- task-executor.ts:162-176 `completeTaskRunByRunId` 가 finalizeTaskRunByRunId
  를 호출하고, registry 에 record 없을 때 빈 array 리턴 (또는 no-op) 임을
  확인. throw 안 함.
- lifecycle.ts:150-212 의 safeFinalizeSubagentTaskRun / safe...DeliveryStatus
  가 try/catch 로 task 갱신 throw 만 잡음을 확인.
- caller (sessions-spawn-tool.ts:446, subagent-spawn.ts:1241) 가 sync void
  호출임을 grep 으로 확인.

### 내가 한 가정

- createRunningTaskRun 의 실제 throw 빈도 — task store throw 시나리오 (disk
  full / FS quota) 는 production 에서 드물지만 발생 가능한 path 로 보았으나
  정량 미측정.
- task-registry 의 *ByRunId 함수가 record 없을 때 silent 리턴함을 task-
  executor.ts 시그니처 + 일반적 registry 패턴으로 inferred. deep-read 미수행.

### 확인 안 한 것 중 영향 가능성

- task-registry 가 별도 disk persistence 를 가지고, 해당 disk 가 별도 sweeper
  로 lost-task 분류해 자동 복구하는 경로가 있는지 미확인. 만약 있다면 영향이
  완화될 수 있음.
- ensureSingleTaskFlow 의 enqueue conflict 가 어떤 조건에서 발생하는지 deep-
  read 미수행. 실제 throw 빈도에 영향.
- registerSubagentRun 의 caller 가 throw 받는 경우 (line 447 ensureListener
  throw 시) 의 graceful 처리 — 별도 FIND 후보지만 본 FIND 범위 외.
