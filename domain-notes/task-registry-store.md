# domain-notes: task-registry-store

작성: 2026-05-29 (data-integrity-auditor, cross-store-consistency 축)
대상: openclaw upstream 61c538e2fc, src/tasks/**
스코프: task registry 의 영속 계층 — in-memory process-state Map 과 sqlite store 간 정합성.

## 아키텍처 요약

두 스토어가 공존한다.
- in-memory: `task-registry.ts` 모듈 스코프 `tasks` / `taskDeliveryStates` Map + 파생 인덱스
  (`taskIdsByRunId`, `taskIdsByOwnerKey`, `taskIdsByParentFlowId`, `taskIdsByRelatedSessionKey`).
- sqlite: `task-registry.store.sqlite.ts` (`task_runs` + `task_delivery_state` 테이블).

SoT 규약: 재기동/reload 시 `restoreTaskRegistryOnce`(task-registry.ts:946) 와
`reloadTaskRegistryFromStore`(974, run-loop.ts:802 에서 production 호출) 가 `loadSnapshot()` 으로
sqlite 를 읽어 in-memory 를 재구성한다. 즉 **sqlite 가 SoT, in-memory 는 그 캐시**.
하지만 모든 write 는 in-memory 를 먼저 mutate 한 뒤 sqlite 로 persist 한다 (아래 참조).

store wiring (`task-registry.store.ts:55-66`, default store): 모든 메서드가 sqlite 함수에 매핑.
`configureTaskRegistryRuntime` override 는 test-utils/test-harness 전용
(test-utils/task-registry-runtime.ts:66, auto-reply/reply/commands.test-harness.ts:61).
=> production 은 항상 default(sqlite) store.

## sqlite write 경로별 트랜잭션 여부

| 함수 (store.sqlite.ts) | write 수 | 트랜잭션 | default store 매핑 | production 도달 |
|---|---|---|---|---|
| saveTaskRegistryStateToSqlite (529) | clear*2 + N upsert | withWriteTransaction | saveSnapshot | O (snapshot 경로) |
| upsertTaskRegistryRecordToSqlite (542) | 1 (upsertRow) | 없음 (단발) | upsertTask | fallback 분기만 |
| upsertTaskWithDeliveryStateToSqlite (547) | 2 (upsert+replace/delete) | withWriteTransaction | upsertTaskWithDeliveryState | O (primary 분기) |
| deleteTaskRegistryRecordFromSqlite (561) | 2 (deleteRow+deleteDelivery) | 없음 (단발 2회) | deleteTask | fallback 분기만 |
| deleteTaskAndDeliveryStateFromSqlite (567) | 2 (delete*2) | withWriteTransaction | deleteTaskWithDeliveryState | O (primary 분기) |
| upsertTaskDeliveryStateToSqlite (574) | 1 (replace) | 없음 (단발) | upsertDeliveryState | O |
| deleteTaskDeliveryStateFromSqlite (579) | 1 (deleteDelivery) | 없음 (단발) | deleteDeliveryState | O |

핵심:
- 단일 statement(1 write) 비트랜잭션은 sqlite auto-commit 으로 원자적 — 결함 아님 (R-3).
- **두 개 이상 write 한 논리단위**인데 비트랜잭션인 것: `deleteTaskRegistryRecordFromSqlite`(561,
  2 DELETE), `upsertTaskRegistryRecordToSqlite`(542, 단일이라 무해). 전자가 표면상 cross-store-gap
  후보지만 default store 의 `deleteTask` 에만 매핑되고, `persistTaskDelete`(task-registry.ts:288-302)
  가 항상 트랜잭션 변형(`deleteTaskWithDeliveryState`)을 먼저 선택하므로 **fallback 분기로만 도달**
  -> production 미도달. seed 의 "비트랜잭션 변형" 가설은 R-3/R-7 상 production gap 아님 (FIND 미생성).
- 즉 sqlite **내부**의 multi-write 는 production 경로에서 전부 트랜잭션 보호됨.
  반증 기준선(트랜잭션 sibling)도 같은 파일에 존재: 567(delete), 547(upsert), 529(snapshot).

## in-memory <-> sqlite 동기화 분석 (진짜 결함 축)

모든 production write 경로가 동일 안티패턴: **in-memory mutate -> sqlite persist, persist 를 감싸는
try/catch 없음**. persist throw 시 in-memory 는 이미 변경, sqlite 는 미반영 -> 교차 발산.

| 경로 (task-registry.ts) | in-memory mutate | persist 호출 | try/catch | 발산 시 결과 |
|---|---|---|---|---|
| createTask (~1603) | tasks.set(1603) | persistTaskUpsert(1612) | 없음 (1613 try 는 syncFlow 만) | in-memory 만 생성; reload 시 유실 |
| updateTask (980) | tasks.set(997) | persistTaskUpsert(1011) | 없음 (1012 try 는 syncFlow 만) | memory=next/sqlite=current 발산 (FIND-002) |
| upsertTaskDeliveryState (1038) | set(1052) | persistTaskDeliveryStateUpsert(1053) | 없음 | delivery state 발산 |
| deleteTaskRecordById (2153) | delete(2162-2163) | persistTaskDelete(2165)+Delete(2166) | 없음 | reload 시 부활 lost-delete (FIND-001) |

persist throw 의 production 트리거: sqlite write 가 SQLITE_BUSY(다중-writer BEGIN IMMEDIATE 가
busy_timeout 5s 초과, store.sqlite.ts:484), SQLITE_FULL(disk-full), SQLITE_IOERR 시
withWriteTransaction 이 ROLLBACK 후 re-throw(store.sqlite.ts:503-506).

발산이 관측되는 두 reader 경로:
- in-memory: `listTasksForOwnerKey`(2083) -> 인덱스 기반, next 반환.
- sqlite-direct: `listFreshTasksForOwnerKey`(2092) -> store.listTasksForOwnerKey(2102) ->
  listTaskRegistryRecordsByOwnerKeyFromSqlite, **sqlite 직접** 읽음.
  production caller: media-generation-task-status-shared.ts:119,314 / (cf. session-async-task-status,
  acp-spawn 은 in-memory listTasksForOwnerKey 사용).
  => 두 reader 가 같은 프로세스에서 상반된 값 반환 가능 (reload 불필요, FIND-002 의 즉시 관측 근거).

## 발견 요약

| FIND | 경로 | symptom | severity | 핵심 |
|---|---|---|---|---|
| FIND-...-001 | deleteTaskRecordById (2153-2173) | cross-store-gap | P1 | in-memory 삭제 후 persist throw -> reload 부활(lost-delete) |
| FIND-...-002 | updateTask (997-1011) | cross-store-gap | P2 | in-memory 갱신 후 persist throw -> sqlite-direct reader 가 stale 노출(wrong-output), reload 시 lost-update |

두 FIND 는 동일 근본원인(persist 미보호 + mutate-first 순서 + sqlite SoT) 의 두 발현. cross_refs 연결.
createTask/upsertTaskDeliveryState 도 같은 패턴이나, createTask 발산은 "신규 레코드 in-memory 만
존재 -> reload 시 단순 유실" 로 부활/모순 같은 신규 영향이 약해 별도 FIND 미생성(001 의 영향 집합에 포함).

## counter_evidence 정리 (반증 결과)

- sqlite 내부 multi-write 트랜잭션 부재 가설: production 경로에서 반증됨 (전부 withWriteTransaction).
  비트랜잭션 변형(deleteTaskRegistryRecordFromSqlite/upsert*RecordToSqlite)은 test-only override
  로만 도달.
- derived-cache 면죄: 적용 불가. in-memory 가 SoT 보다 앞서 mutate 되고, sqlite-direct reader 공존
  으로 자가복구가 아니라 발산 방향으로 작동.
- 외부 패키지 경계: sqlite 핸들은 node:sqlite(in-tree requireNodeSqlite), 결함은 in-tree 소비
  코드(persist 미보호)에 있음.

## 미탐색/후속

- task-flow-registry.* (별도 셀). task registry 와 flow registry 간 syncFlowFromTask(1013) 교차
  갱신은 본 셀 스코프 밖이나 또 다른 교차-스토어 표면일 수 있음.
- persist throw 후 caller 의 reload 트리거 여부(즉시 lost-delete/lost-update 악화 조건)는 미추적.

## clusterer (2026-05-29)

- CAND-047 (epic, P1): FIND-...-001(P1) + -002(P2) 묶음 (frontmatter 상호 cross_refs, 의도 존중).
  공통 원인 "in-memory Map 선커밋 후 sqlite persist 를 try/catch/rollback 없이 호출 → persist
  throw(BUSY/FULL/IOERR, withWriteTransaction re-throw) 시 in-memory↔sqlite 교차-스토어 발산".
  FIND-001 root_cause_chain[0](task-registry.ts:2162 delete 가 persist 앞) + [1](2165 persist
  무보호), FIND-002 root_cause_chain[0](997 set 이 persist 앞) + [1](1012 try 는 syncFlowFromTask
  만 감쌈)가 동일 ordering+보호 부재 축으로 수렴. sqlite 측은 withWriteTransaction 으로 원자적이라
  결함은 sqlite 트랜잭션 부재가 아니라 교차-스토어 경계 부재 → epic, severity 최고값 P1 상속.
- 도메인 분리 규율 적용: 타 도메인과 "교차-스토어 경계 부재" root cause 유사하나 별도 PR scope 이라
  병합 금지, task-registry-store 단독 CAND.
