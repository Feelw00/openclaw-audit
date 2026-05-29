---
candidate_id: CAND-047
type: epic
finding_ids:
  - FIND-task-registry-store-cross-store-consistency-001
  - FIND-task-registry-store-cross-store-consistency-002
cluster_rationale: |
  공통 근본 원인 (cross-cut within file, clusterer.md Step 2/3): task-registry.ts 의
  delete/update 두 mutator 가 동일한 ordering+보호 결함을 공유한다 — in-memory Map
  (tasks/taskDeliveryStates) 을 먼저 commit 한 뒤 sqlite persist 를 호출하고, 그 persist
  호출을 try/catch/rollback 으로 감싸지 않는다. sqlite write 가 throw(BUSY/FULL/IOERR,
  withWriteTransaction re-throw)하면 in-memory 와 sqlite 가 발산하는데, in-memory↔sqlite
  를 묶는 상위 트랜잭션/보상 경계가 전무하다. 두 FIND 는 frontmatter 에서 서로 cross_refs
  되어 있어 동일 축으로 보는 저자 의도가 명시돼 있다(persona: "이미 cross_refs 된 경우 그
  의도 존중").

  각 FIND root_cause_chain 인용:
  - FIND-...-001 root_cause_chain[0] ("삭제 시 in-memory mutate 가 sqlite persist 보다
    먼저 실행된다"): "tasks.delete/taskDeliveryStates.delete(2162-2163)가 persistTaskDelete
    (2165) 앞에 위치" (evidence_ref: src/tasks/task-registry.ts:2162)
  - FIND-...-001 root_cause_chain[1] ("persist 호출이 try/catch 로 감싸이지 않아 throw 시
    in-memory 를 되돌리지 못한다") (evidence_ref: src/tasks/task-registry.ts:2165)
  - FIND-...-002 root_cause_chain[0] ("갱신 시 in-memory mutate 가 sqlite persist 보다
    먼저 실행된다"): "tasks.set(taskId, next)(997)가 persistTaskUpsert(next)(1011) 앞에 위치"
    (evidence_ref: src/tasks/task-registry.ts:997)
  - FIND-...-002 root_cause_chain[1] ("persist 호출이 보호 블록 밖이라 throw 시 in-memory
    를 되돌리지 못한다"): "persistTaskUpsert(1011) 다음의 try(1012)는 syncFlowFromTask 만
    감싼다" (evidence_ref: src/tasks/task-registry.ts:1012)

  두 FIND 의 반증 카테고리도 동일하다: sqlite 측은 withWriteTransaction(BEGIN IMMEDIATE)
  으로 보호되어 sqlite 내부 부분커밋은 없고(결함은 sqlite 트랜잭션 부재가 아님),
  derived-cache 면죄도 적용 불가하다(in-memory 가 SoT 보다 앞서 mutate 됨). 즉 결함의
  본질이 "in-memory↔sqlite 교차-스토어 경계 부재" 라는 단일 원인으로 수렴한다.

  epic 으로 묶는 이유: 같은 파일의 같은 mutator 군(delete/update)이 같은 순서 패턴 +
  같은 보호 부재를 공유하고, 위반된 기준선(sqlite 측 withWriteTransaction)도 동일하다.
  공통 인프라 축(persist throw 시 in-memory rollback / 교차-스토어 보상 경계)을 다루므로
  GH Issue 1건 + 자식 task 가 적합. severity 는 최고값 P1 상속. (해결책 자체는 본 CAND
  범위 밖.)
proposed_title: "task-registry.ts: deleteTaskRecordById / updateTask 가 in-memory 선커밋 후 sqlite persist 를 보호 없이 호출 → persist throw 시 교차-스토어 발산(lost-delete / stale-read)"
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
---

# task-registry.ts: in-memory 선커밋 후 sqlite persist 무보호 호출 → 교차-스토어 발산

## 공통 패턴

task-registry.ts 의 두 mutator 가 동일한 ordering+보호 결함을 공유한다:

- in-memory `tasks`/`taskDeliveryStates` Map(및 인덱스)을 먼저 mutate-commit 한 뒤
  sqlite persist 를 호출한다 (delete: 2162-2163 → 2165, update: 997 → 1011).
- 이 persist 호출은 try/catch/rollback 으로 감싸이지 않는다. sqlite write 는
  production 에서 실제로 throw 할 수 있다 — `withWriteTransaction(BEGIN IMMEDIATE)` 가
  SQLITE_BUSY(다중-writer busy_timeout 5s 초과) / SQLITE_FULL(disk-full) / SQLITE_IOERR
  시 ROLLBACK 후 re-throw 한다(store.sqlite.ts:503-506). throw 가 그대로 전파되어
  in-memory mutate 는 되돌려지지 않는다.
- 결과: in-memory(새 상태)와 sqlite(이전 상태)가 발산한다. sqlite 측은
  withWriteTransaction 으로 내부 원자성이 보장되므로, 결함의 본질은 sqlite 트랜잭션
  부재가 아니라 **in-memory↔sqlite 교차-스토어 경계 부재**다. derived-cache 면죄도
  적용 불가하다 — in-memory 캐시가 SoT(sqlite)보다 앞서 mutate 되어 자가복구가 오히려
  발산/부활 방향으로 작동한다.

## 관련 FIND

- FIND-task-registry-store-cross-store-consistency-001 (P1): `task-registry.ts:2153-2173`
  `deleteTaskRecordById`. in-memory 에서 먼저 삭제(2162-2163) 후 `persistTaskDelete`(2165)
  throw 시 sqlite 행은 ROLLBACK 으로 보존. reload(`reloadTaskRegistryFromStore`,
  run-loop.ts:802)/재기동(`restoreTaskRegistryOnce`, :946)이 sqlite 를 SoT 로 재적재해
  삭제했던 태스크가 부활(lost-delete) → 중복 delivery, retention/cleanup 회피, owner
  목록 오염. 자가복구 경로 없음.

- FIND-task-registry-store-cross-store-consistency-002 (P2): `task-registry.ts:997-1011`
  `updateTask`. in-memory 를 `next` 로 먼저 갱신(997) 후 `persistTaskUpsert`(1011) throw
  시 in-memory=next / sqlite=current(stale) 발산. sqlite-direct reader
  `listFreshTasksForOwnerKey`(2102)가 media-generation status 경로(media-generation-task-
  status-shared.ts:119,314)에서 stale 값을 읽어 완료 태스크를 진행중으로 오판(wrong-output).
  같은 ownerKey 에 대해 in-memory 경로와 sqlite-direct 경로가 상반된 답을 주는 모순.
  reload 시 sqlite stale 이 SoT 로 적재되어 lost-update 로 악화.
