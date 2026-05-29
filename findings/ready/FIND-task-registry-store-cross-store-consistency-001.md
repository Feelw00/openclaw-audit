---
id: FIND-task-registry-store-cross-store-consistency-001
cell: task-registry-store-cross-store-consistency
title: 'deleteTaskRecordById: 인메모리 삭제 후 sqlite persist throw 시 reload 부활 (lost-delete)'
file: src/tasks/task-registry.ts
line_range: 2153-2173
evidence: "```ts\nexport function deleteTaskRecordById(taskId: string): boolean {\n\
  \  ensureTaskRegistryReady();\n  const current = tasks.get(taskId);\n  if (!current)\
  \ {\n    return false;\n  }\n  deleteOwnerKeyIndex(taskId, current);\n  deleteParentFlowIdIndex(taskId,\
  \ current);\n  deleteRelatedSessionKeyIndex(taskId, current);\n  tasks.delete(taskId);\n\
  \  taskDeliveryStates.delete(taskId);\n  rebuildRunIdIndex();\n  persistTaskDelete(taskId);\n\
  \  persistTaskDeliveryStateDelete(taskId);\n  emitTaskRegistryObserverEvent(() =>\
  \ ({\n    kind: \"deleted\",\n    taskId: current.taskId,\n    previous: cloneTaskRecord(current),\n\
  \  }));\n  return true;\n}\n```\n"
symptom_type: cross-store-gap
problem: 태스크를 삭제한 직후 in-memory Map 에서는 사라지지만, sqlite write 가 throw 하면 sqlite 행은 남는다.
  이후 reload/재기동 시 삭제했던 태스크가 부활(lost-delete)하여 종료/정리한 태스크가 다시 active 로 나타난다.
mechanism: "crash 주입 시점 = persistTaskDelete (line 2165) 내부 sqlite write 호출.\n1) tasks.delete\
  \ / taskDeliveryStates.delete 가 in-memory 에서 먼저 commit (line 2162-2163).\n2) persistTaskDelete(taskId)\
  \ (line 2165) 가 store.deleteTaskWithDeliveryState\n   -> deleteTaskAndDeliveryStateFromSqlite\
  \ -> withWriteTransaction(BEGIN IMMEDIATE) 로 진입.\n3) BEGIN IMMEDIATE 가 SQLITE_BUSY\
  \ (다중 프로세스가 같은 db 파일 공유, busy_timeout 5s 초과)\n   또는 COMMIT 단계가 SQLITE_FULL/SQLITE_IOERR\
  \ 로 throw. withWriteTransaction 은 ROLLBACK 후\n   error 를 re-throw (task-registry.store.sqlite.ts:503-506).\n\
  4) deleteTaskRecordById 에는 persist 호출을 감싸는 try/catch 가 없어 throw 가 그대로 전파.\n   in-memory\
  \ 는 이미 삭제 완료, sqlite 행은 ROLLBACK 으로 보존 -> 두 스토어 발산.\n5) reloadTaskRegistryFromStore()\
  \ (run-loop.ts:802) 나 재기동 시 restoreTaskRegistryOnce 가\n   loadSnapshot() 으로 sqlite\
  \ SoT 를 재적재 -> 삭제했던 태스크가 in-memory 로 복귀(부활).\n"
root_cause_chain:
- why: 삭제 시 in-memory mutate 가 sqlite persist 보다 먼저 실행된다
  because: tasks.delete/taskDeliveryStates.delete (2162-2163) 가 persistTaskDelete
    (2165) 앞에 위치
  evidence_ref: src/tasks/task-registry.ts:2162
- why: persist 호출이 try/catch 로 감싸이지 않아 throw 시 in-memory 를 되돌리지 못한다
  because: persistTaskDelete/persistTaskDeliveryStateDelete (2165-2166) 가 보호 블록 밖.
    함수 내 try 는 없음
  evidence_ref: src/tasks/task-registry.ts:2165
- why: sqlite write 는 production 에서 실제로 throw 할 수 있다
  because: withWriteTransaction 의 BEGIN/COMMIT 은 SQLITE_BUSY(busy_timeout 5s 초과)/SQLITE_FULL/IOERR
    시 throw 후 ROLLBACK + re-throw
  evidence_ref: src/tasks/task-registry.store.sqlite.ts:503
- why: reload/재기동 시 sqlite 가 단일 SoT 라 in-memory 삭제가 무효화된다
  because: restoreTaskRegistryOnce 가 loadSnapshot() 결과로 tasks/taskDeliveryStates 를
    재적재
  evidence_ref: src/tasks/task-registry.ts:946
impact_hypothesis: data-loss
impact_detail: '방향성: in-memory 가 sqlite 보다 앞서감(삭제 반영) -> sqlite 가 stale(행 잔존).

  reload 또는 재기동 시 sqlite 가 SoT 이므로 in-memory 삭제가 silently 되돌려져 부활.

  발현 조건: sqlite write 가 throw 하는 시점 (disk-full / 동시 다중-writer 의 BUSY 5s 초과 / IOERR).

  결과: 종료/정리한 태스크가 active 로 부활 -> 중복 delivery, retention/cleanup 누수, owner 별

  태스크 목록 오염. 자동 복구 경로 없음(다음 명시적 delete 성공 전까지 영속).

  '
severity: P1
counter_evidence:
  path: src/tasks/task-registry.store.sqlite.ts
  line: 567
  reason: "deleteTaskAndDeliveryStateFromSqlite (567-572) 는 withWriteTransaction(BEGIN\
    \ IMMEDIATE)\n으로 두 DELETE 를 원자 단위로 묶는다 -> sqlite 내부의 두 write 자체는 일관(부분커밋 없음).\n\
    즉 결함은 sqlite 트랜잭션 부재가 아니라 \"in-memory <-> sqlite\" 교차-스토어 경계 부재.\n[실행조건 분류]\n\
    | 경로 | 조건 |\n|---|---|\n| sqlite 2-write 원자성 (deleteTaskAndDeliveryStateFromSqlite,\
    \ withWriteTransaction) | unconditional (default store) |\n| in-memory rollback-on-persist-fail\
    \ | 없음 (conditional-edge 조차 부재) |\n[반증 카테고리]\n- 트랜잭션/락: sqlite 측은 트랜잭션 보호됨. 그러나\
    \ in-memory mutate 와 sqlite write 를\n  묶는 상위 경계 없음 -> 교차 발산은 막지 못함.\n- derived-cache\
    \ 아님: sqlite 가 SoT, in-memory 는 캐시지만 삭제는 in-memory 에서 먼저 발생해\n  \"캐시가 SoT 보다 앞섬\"\
    \ -> 자가복구가 잘못된 방향(부활)으로 작동.\n비트랜잭션 변형 deleteTaskRegistryRecordFromSqlite (561-565,\
    \ 두 단발 DELETE) 는\nstore.deleteTask 에 매핑되나 default store 가 deleteTaskWithDeliveryState(트랜잭션)를\n\
    항상 제공하므로 persistTaskDelete 의 fallback 분기로만 도달 -> production 미도달\n(configureTaskRegistryRuntime\
    \ override 는 test-utils/test-harness 전용,\nsrc/test-utils/task-registry-runtime.ts:66\
    \ / src/auto-reply/reply/commands.test-harness.ts:61).\n따라서 그 비트랜잭션 변형은 R-3/R-7\
    \ 상 production cross-store-gap 아님(FIND 미생성).\n"
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-task-registry-store-cross-store-consistency-002
---
# deleteTaskRecordById: 인메모리 삭제 후 sqlite persist throw 시 reload 부활 (lost-delete)

## 문제
`deleteTaskRecordById` 는 in-memory `tasks` Map 과 `taskDeliveryStates` Map 에서 먼저 레코드를 제거한 뒤(line 2162-2163) sqlite persist 를 호출한다(line 2165-2166). persist 가 throw 하면 in-memory 는 이미 삭제 완료지만 sqlite 행은 트랜잭션 ROLLBACK 으로 보존되어 두 스토어가 발산한다. reload(`reloadTaskRegistryFromStore`)나 재기동 시 sqlite 가 단일 SoT 로 재적재되어 삭제했던 태스크가 다시 나타난다(lost-delete / 부활).

## 발현 메커니즘
crash(예외) 주입 지점은 `persistTaskDelete` (line 2165) 내부의 sqlite write 다.
1. line 2162-2163 에서 `tasks.delete(taskId)` / `taskDeliveryStates.delete(taskId)` 로 in-memory 가 먼저 commit 된다. 앞선 line 2159-2161 의 인덱스 제거도 마찬가지.
2. line 2165 `persistTaskDelete(taskId)` 는 default store 의 `deleteTaskWithDeliveryState` -> `deleteTaskAndDeliveryStateFromSqlite` -> `withWriteTransaction(BEGIN IMMEDIATE)` 로 진입한다.
3. 다중 프로세스가 동일 sqlite 파일을 공유하는 상황에서 `BEGIN IMMEDIATE` 가 `busy_timeout`(5s, store.sqlite.ts:484) 을 초과하면 SQLITE_BUSY, 디스크가 가득 차면 COMMIT 단계에서 SQLITE_FULL/SQLITE_IOERR 로 throw 한다. `withWriteTransaction` 은 ROLLBACK 후 error 를 re-throw 한다(store.sqlite.ts:503-506).
4. `deleteTaskRecordById` 에는 persist 호출을 감싸는 try/catch 가 없으므로 throw 가 caller 로 그대로 전파되고, in-memory 의 삭제는 되돌려지지 않는다.
5. 이후 `reloadTaskRegistryFromStore()`(run-loop.ts:802) 또는 프로세스 재기동의 `restoreTaskRegistryOnce`(line 946) 가 `loadSnapshot()` 으로 sqlite 를 재적재하면, ROLLBACK 으로 살아남은 행이 in-memory 로 복귀하여 태스크가 부활한다.

## 근본 원인 분석
교차-스토어(인메모리 process-state Map <-> sqlite) 갱신을 묶는 트랜잭션/보상 경계가 없다. sqlite 내부의 두 DELETE 는 `withWriteTransaction` 으로 원자적이지만(그래서 sqlite 측 부분커밋은 없음), in-memory mutate 가 sqlite write 보다 먼저 commit 되고 persist 실패 시 이를 rollback 하는 경로가 전무하다. sqlite 가 reload 시 SoT 로 작동하므로, "in-memory 가 앞서 삭제 -> sqlite 잔존 -> reload 부활" 이라는 단방향 발산이 영속화된다.

## 영향
- impact_hypothesis: data-loss (의미상 lost-delete = 삭제 의도 유실).
- 발산 방향: in-memory(삭제됨) vs sqlite(잔존). reload/재기동 시 sqlite 가 이겨 부활.
- 트리거: production sqlite write throw — disk-full(SQLITE_FULL), 동시 다중-writer 의 BEGIN IMMEDIATE busy_timeout 5s 초과(SQLITE_BUSY), IOERR.
- 결과: 종료/정리한 태스크가 active 로 부활하여 중복 delivery, retention/cleanup 회피, owner 별 목록 오염. 자가복구 경로 없음(다음 성공적 delete 까지 영속).

## 반증 탐색
- 트랜잭션/락: `deleteTaskAndDeliveryStateFromSqlite`(store.sqlite.ts:567-572)는 `withWriteTransaction(BEGIN IMMEDIATE)` 로 두 DELETE 를 원자화한다 — sqlite 측은 보호됨. 그러나 in-memory<->sqlite 상위 경계가 없어 교차 발산은 막지 못한다. 따라서 결함의 본질은 sqlite 트랜잭션 부재가 아니라 교차-스토어 경계 부재다.
- derived-cache 여부: sqlite 가 SoT, in-memory 는 그 캐시. 하지만 삭제가 in-memory 에서 먼저 일어나므로 "캐시가 SoT 보다 앞섬" 상태가 되고, 자가복구(reload)가 부활이라는 잘못된 방향으로 작동한다 -> derived-cache 면죄 적용 불가.
- 비트랜잭션 sibling: `deleteTaskRegistryRecordFromSqlite`(561-565)는 두 단발 DELETE 로 비트랜잭션이지만 default store 의 `deleteTask` 에만 매핑되고, default store 는 항상 트랜잭션 변형(`deleteTaskWithDeliveryState`)을 제공하므로 `persistTaskDelete`(288-302)의 fallback 분기로만 도달 -> production 미도달. override 는 test-utils 전용(task-registry-runtime.ts:66, commands.test-harness.ts:61)이라 R-3/R-7 상 별도 FIND 미생성.

## Self-check
### 내가 확실한 근거
- in-memory delete(2162-2163)가 persist(2165-2166)보다 먼저 실행됨 — 코드 순서로 확정.
- persist 호출이 try/catch 밖이며 함수 내 보호 블록 없음 — 함수 전체(2153-2173) 확인.
- `withWriteTransaction` 이 error 를 re-throw 함(store.sqlite.ts:503-506).
- reload/restore 가 sqlite 를 SoT 로 재적재함(task-registry.ts:946, run-loop.ts:802).
- 비트랜잭션 변형은 default store 에서 미도달(store.ts:61-62, persistTaskDelete 분기 288-302).

### 내가 한 가정
- production 에서 sqlite write 가 throw 하는 빈도는 환경 의존(disk-full/다중-writer 경합). 흔하지는 않으나 가능.
- 다중 프로세스가 동일 task-registry sqlite 를 공유하는 배포가 존재한다고 가정(BUSY 시나리오 전제). 단일 프로세스라도 disk-full/IOERR 는 유효.

### 확인 안 한 것 중 영향 가능성
- `deleteTaskRecordById` 의 모든 production caller 가 throw 를 어떻게 처리하는지(swallow vs propagate) 까지는 추적하지 않음 — 단, 발산은 caller 처리와 무관하게 발생.
- persist throw 후 동일 프로세스가 reload 없이 계속 실행되면 in-memory 는 삭제 상태 유지되어 즉시 부활은 안 보일 수 있음(발현은 reload/재기동 시점).
