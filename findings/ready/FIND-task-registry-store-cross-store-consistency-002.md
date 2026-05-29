---
id: FIND-task-registry-store-cross-store-consistency-002
cell: task-registry-store-cross-store-consistency
title: 'updateTask: 인메모리 갱신 후 persist throw 시 sqlite-direct reader 가 stale 값 노출'
file: src/tasks/task-registry.ts
line_range: 997-1011
evidence: "```ts\ntasks.set(taskId, next);\nif (patch.runId && patch.runId !== current.runId)\
  \ {\n  rebuildRunIdIndex();\n}\nif (sessionIndexChanged) {\n  deleteOwnerKeyIndex(taskId,\
  \ current);\n  addOwnerKeyIndex(taskId, next);\n  deleteRelatedSessionKeyIndex(taskId,\
  \ current);\n  addRelatedSessionKeyIndex(taskId, next);\n}\nif (parentFlowIndexChanged)\
  \ {\n  deleteParentFlowIdIndex(taskId, current);\n  addParentFlowIdIndex(taskId,\
  \ next);\n}\npersistTaskUpsert(next);\n```\n"
symptom_type: cross-store-gap
problem: '태스크 상태 갱신 시 in-memory Map 이 먼저 next 로 바뀌고 sqlite persist 가 throw 하면, in-memory
  는 next/sqlite 는 current(stale) 로 발산한다. sqlite 를 직접 읽는 listFreshTasksForOwnerKey
  경로가 같은 프로세스 안에서 reload 없이 곧바로 stale 상태(예: 완료 태스크를 여전히 running)를 노출한다.'
mechanism: "crash 주입 시점 = persistTaskUpsert (line 1011) 내부 sqlite write 호출.\n1) line\
  \ 997 tasks.set(taskId, next) 로 in-memory 가 먼저 next 로 commit (인덱스도 1001-1010 갱신).\n\
  2) line 1011 persistTaskUpsert(next) 가 store.upsertTaskWithDeliveryState\n   ->\
  \ upsertTaskWithDeliveryStateToSqlite -> withWriteTransaction(BEGIN IMMEDIATE) 로\
  \ 진입.\n3) BEGIN IMMEDIATE/COMMIT 가 SQLITE_BUSY(다중-writer busy_timeout 5s 초과)/SQLITE_FULL/IOERR\
  \ 로\n   throw -> ROLLBACK + re-throw (task-registry.store.sqlite.ts:551-558, 503-506)\
  \ -> sqlite 는 current 유지.\n4) updateTask 의 persistTaskUpsert 호출은 try/catch 밖(try\
  \ 블록 1012 는 syncFlowFromTask 만 감쌈) ->\n   throw 가 전파되고 in-memory 의 next 는 되돌려지지\
  \ 않는다.\n5) listFreshTasksForOwnerKey (task-registry.ts:2102) 가 store.listTasksForOwnerKey\n\
  \   -> listTaskRegistryRecordsByOwnerKeyFromSqlite 로 sqlite 를 직접 읽어 stale current\
  \ 를 반환.\n   reload 없이 같은 프로세스 안에서 in-memory(next) 와 즉시 불일치.\n"
root_cause_chain:
- why: 갱신 시 in-memory mutate 가 sqlite persist 보다 먼저 실행된다
  because: tasks.set(taskId, next) (997) 가 persistTaskUpsert(next) (1011) 앞에 위치
  evidence_ref: src/tasks/task-registry.ts:997
- why: persist 호출이 보호 블록 밖이라 throw 시 in-memory 를 되돌리지 못한다
  because: persistTaskUpsert (1011) 다음의 try (1012) 는 syncFlowFromTask 만 감싼다
  evidence_ref: src/tasks/task-registry.ts:1012
- why: sqlite 를 직접 읽는 production reader 가 존재해 발산이 즉시 관측된다
  because: listFreshTasksForOwnerKey 가 store.listTasksForOwnerKey(sqlite)를 읽고, media-generation-task-status-shared
    가 이를 호출
  evidence_ref: src/tasks/task-registry.ts:2102
- why: sqlite write 는 production 에서 실제로 throw 한다
  because: upsert 도 withWriteTransaction 경유 -> BEGIN/COMMIT 가 BUSY/FULL/IOERR 시 ROLLBACK
    + re-throw
  evidence_ref: src/tasks/task-registry.store.sqlite.ts:551
impact_hypothesis: wrong-output
impact_detail: '방향성: in-memory(next, 예: status=done) vs sqlite(current stale, 예: status=running).

  in-memory 경로(listTasksForOwnerKey, line 2089)와 sqlite-direct 경로(listFreshTasksForOwnerKey,

  line 2092)가 같은 태스크에 대해 상반된 status/필드를 반환 -> 호출자별 모순.

  발현 조건: persist throw 시점(disk-full / 다중-writer BUSY 5s 초과 / IOERR).

  결과: media-generation-task-status-shared (line 119,314) 가 fresh(sqlite) 경로로 stale
  상태를

  읽어 완료 태스크를 진행중으로 오판하거나 갱신된 delivery/notify 필드를 놓침.

  재기동/reload 시에는 sqlite stale 값이 in-memory 로 적재되어 갱신 자체가 영구 유실(lost-update).

  '
severity: P2
counter_evidence:
  path: src/tasks/task-registry.store.sqlite.ts
  line: 547
  reason: "upsertTaskWithDeliveryStateToSqlite (547-559) 는 withWriteTransaction(BEGIN\
    \ IMMEDIATE) 로\ntask upsert + delivery state replace 를 원자 단위로 묶는다 -> sqlite 측\
    \ 두 write 는 일관.\n결함은 sqlite 트랜잭션 부재가 아니라 in-memory<->sqlite 교차 경계 부재.\n[실행조건 분류]\n\
    | 경로 | 조건 |\n|---|---|\n| sqlite 2-write 원자성 (upsertTaskWithDeliveryStateToSqlite,\
    \ withWriteTransaction) | unconditional (default store) |\n| in-memory rollback-on-persist-fail\
    \ | 없음 |\n| sqlite-direct reader 노출 (listFreshTasksForOwnerKey) | unconditional\
    \ (media-generation caller) |\n[반증 카테고리]\n- 트랜잭션/락: sqlite 측은 보호되나 in-memory mutate\
    \ 를 묶는 상위 경계 없음.\n- derived-cache 아님: 갱신이 in-memory 에서 먼저 발생해 in-memory 가 SoT\
    \ 보다 앞섬.\n  두 reader 경로(memory vs sqlite-direct)가 공존해 발산이 즉시 관측되며 자가복구 아님.\n-\
    \ 호출 빈도: updateTask 는 태스크 lifecycle 의 hot-path(상태 전이마다 호출),\n  listFreshTasksForOwnerKey\
    \ 도 media-generation status 조회 production 경로.\n"
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-task-registry-store-cross-store-consistency-001
---
# updateTask: 인메모리 갱신 후 persist throw 시 sqlite-direct reader 가 stale 값 노출

## 문제
`updateTask` 는 in-memory `tasks` Map 을 먼저 `next` 로 갱신한 뒤(line 997) sqlite persist 를 호출한다(line 1011). persist 가 throw 하면 in-memory 는 `next`, sqlite 는 `current`(stale) 로 발산한다. sqlite 를 직접 읽는 `listFreshTasksForOwnerKey` 경로가 reload 없이 같은 프로세스 안에서 즉시 stale 값(예: 완료 태스크를 여전히 running)을 노출하여 호출자별 상반된 결과를 반환한다.

## 발현 메커니즘
crash(예외) 주입 지점은 `persistTaskUpsert`(line 1011) 내부 sqlite write 다.
1. line 997 `tasks.set(taskId, next)` 로 in-memory 가 먼저 `next` 로 commit 되고, line 1001-1010 에서 owner/relatedSession/parentFlow 인덱스도 `next` 기준으로 갱신된다.
2. line 1011 `persistTaskUpsert(next)` 는 default store 의 `upsertTaskWithDeliveryState` -> `upsertTaskWithDeliveryStateToSqlite` -> `withWriteTransaction(BEGIN IMMEDIATE)`(store.sqlite.ts:551-558) 로 진입한다.
3. `BEGIN IMMEDIATE`/`COMMIT` 가 SQLITE_BUSY(다중-writer busy_timeout 5s 초과)/SQLITE_FULL/SQLITE_IOERR 로 throw 하면 `withWriteTransaction` 이 ROLLBACK 후 re-throw(store.sqlite.ts:503-506) 한다 — sqlite 는 `current` 유지.
4. `updateTask` 에서 `persistTaskUpsert`(1011) 는 try/catch 밖이다. 바로 다음 try(line 1012)는 `syncFlowFromTask` 만 감싸므로 persist throw 는 그 try 에 잡히지 않고 caller 로 전파되며, in-memory 의 `next` 는 되돌려지지 않는다.
5. 이후 `listFreshTasksForOwnerKey`(line 2092-2117)가 `store.listTasksForOwnerKey`(line 2102) -> `listTaskRegistryRecordsByOwnerKeyFromSqlite` 로 sqlite 를 직접 읽어 stale `current` 를 반환한다. 같은 프로세스에서 in-memory 경로(`listTasksForOwnerKey`, line 2089)는 `next` 를, sqlite-direct 경로는 `current` 를 반환 -> 즉시 불일치.

## 근본 원인 분석
교차-스토어(in-memory Map <-> sqlite) 갱신을 묶는 트랜잭션/보상 경계가 없다. sqlite 내부의 upsert+delivery-state replace 는 `withWriteTransaction` 으로 원자적이지만, in-memory mutate 가 sqlite write 보다 먼저 commit 되고 persist 실패를 rollback 하는 경로가 없다. 더욱이 `listFreshTasksForOwnerKey` 라는 sqlite-direct reader 가 production 에 존재해(media-generation status 경로) in-memory 캐시를 우회하므로, 발산이 reload 를 기다리지 않고 같은 프로세스 안에서 곧바로 관측된다. 재기동/reload 시에는 sqlite stale 이 SoT 로 적재되어 갱신이 영구 유실(lost-update)된다.

## 영향
- impact_hypothesis: wrong-output (모순된 status/필드 반환). reload 시점에는 lost-update 로 전환.
- 발산 방향: in-memory(`next`) vs sqlite(`current` stale).
- 트리거: production sqlite write throw — disk-full(SQLITE_FULL), 다중-writer BEGIN IMMEDIATE busy_timeout 5s 초과(SQLITE_BUSY), IOERR.
- 결과: `media-generation-task-status-shared`(line 119, 314)가 fresh(sqlite) 경로로 stale 상태를 읽어 완료 태스크를 진행중으로 오판하거나, 갱신된 delivery/notify/terminal 필드를 놓친다. 같은 ownerKey 에 대해 memory 경로와 sqlite 경로가 상반된 답을 주는 모순 상태.

## 반증 탐색
- 트랜잭션/락: `upsertTaskWithDeliveryStateToSqlite`(store.sqlite.ts:547-559)는 `withWriteTransaction(BEGIN IMMEDIATE)`로 두 write 를 원자화 — sqlite 측은 보호됨. 결함은 sqlite 트랜잭션 부재가 아니라 in-memory<->sqlite 경계 부재.
- derived-cache 여부: in-memory 는 sqlite 의 캐시지만 갱신이 in-memory 에서 먼저 발생해 캐시가 SoT 보다 앞선다. 게다가 sqlite-direct reader 가 공존해 두 경로가 동시에 상반된 값을 반환 -> 자가복구형 derived-cache 면죄 적용 불가.
- 호출 빈도: `updateTask` 는 태스크 상태 전이마다 호출되는 hot-path. `listFreshTasksForOwnerKey` 도 media-generation status 조회의 production 경로(line 119, 314)로 cold-only 가 아님.

## Self-check
### 내가 확실한 근거
- in-memory set(997)이 persist(1011)보다 먼저 실행됨 — 코드 순서로 확정.
- persistTaskUpsert(1011)가 try(1012, syncFlowFromTask 전용) 밖이라 보호되지 않음 — 함수 본문 확인.
- listFreshTasksForOwnerKey 가 sqlite 를 직접 읽음(2102) + production caller 존재(media-generation-task-status-shared.ts:119,314).
- upsert 가 withWriteTransaction 경유 + throw 시 ROLLBACK/re-throw(store.sqlite.ts:551, 503).

### 내가 한 가정
- persist throw 빈도는 환경 의존(disk-full/다중-writer 경합). 단일 프로세스에서도 disk-full/IOERR 유효.
- media-generation reader 가 발산 창(persist 실패~다음 성공 사이) 안에 조회를 수행해야 관측됨 — 타이밍 의존.

### 확인 안 한 것 중 영향 가능성
- persist throw 가 caller 로 전파된 뒤 상위 핸들러가 즉시 reload 를 트리거하는지까지는 추적 안 함 — reload 시 stale 이 SoT 로 적재되어 lost-update 로 악화될 수 있음.
- in-memory 인덱스(owner/parentFlow)도 next 기준으로 갱신되어, persist 실패 후 인덱스와 sqlite 간 추가 불일치가 있을 수 있으나 본 FIND 는 status/필드 값 발산에 한정.
