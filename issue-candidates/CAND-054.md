---
candidate_id: CAND-054
type: epic
finding_ids:
  - FIND-acp-control-plane-concurrency-001
  - FIND-acp-control-plane-concurrency-002
cluster_rationale: |
  공통 근본 원인 (clusterer.md Step 3, FIND 본문 cross_refs 존중): ACP turn 생애주기의
  동시성 직렬화가 SessionActorQueue 단일 + eventGate boolean 플래그에만 의존하고, 그
  외 atomic cut-off/lock 프리미티브가 부재하여, 특정 경로가 그 단일 기제를 우회하거나
  (cancelSession queue bypass) flag 가 atomic 닫힘+drain 을 보장하지 못해(eventGate
  single-check) in-flight 작업이 직렬화 없이 누출/경합한다는 동일 축의 발현이다.
  FIND-002 는 frontmatter 에서 FIND-001 을 cross_ref 한다(FIND-002.cross_refs=[FIND-001]).

  각 FIND root_cause_chain 인용:
  - FIND-...-001 root_cause_chain[2] ("왜 manager 가 cancel↔close 순서를 보장하지
    못하는가?"): "동시성 직렬화가 오직 SessionActorQueue 한 군데뿐이고 외부
    Mutex/Semaphore 가 없으며, cancelSession 은 응답성 때문에 그 큐를 의도적으로
    건너뛴다. turn 종료 시 in-flight cancel 을 기다리는 별도 가드가 없다"
    (evidence_ref: src/acp/control-plane/manager.core.ts:1274)
  - FIND-...-001 root_cause_chain[0] ("왜 cancel 과 close 가 동일 handle 에 직렬화 없이
    동시 호출되는가?"): "cancelSession 의 active-turn 분기가 SessionActorQueue(turn/close
    가 쓰는 유일한 직렬화 기제)를 우회한다. turn 과 그 finally 의 close 는 withSessionActor
    안, cancel 은 밖"
    (evidence_ref: src/acp/control-plane/manager.core.ts:1274-1289)
  - FIND-...-002 root_cause_chain[2] ("왜 가드가 in-flight 이벤트를 회수하지 못하는가?"):
    "eventGate 가 단순 boolean 플래그라 닫힘과 진행 중 전달 취소를 atomic 하게 못 한다.
    await 경계를 넘어선 이벤트 1건은 항상 누출 가능"
    (evidence_ref: src/acp/control-plane/manager.turn-stream.ts:11-13)
  - FIND-...-002 root_cause_chain[1] ("왜 gate 가 await 도중에 닫히는가?"): "turn timeout
    시 turnPromise 가 detach 되어 계속 도는데, 같은 흐름의 onTimeout 이 eventGate.open=false
    를 set 하므로 detach 된 루프와 gate-close 가 인터리빙된다"
    (evidence_ref: src/acp/control-plane/manager.core.ts:1138, :953)

  공통 기준선/반증 공유: 두 FIND 모두 `rg "Mutex|Semaphore|AsyncLock" src/acp/control-plane/`
  → production match 없음으로 외부 lock 부재를 확정하고, 이 도메인의 동시성 보장이
  SessionActorQueue(직렬화)와 eventGate(emit 필터)라는 비-lock 기제 두 가지에만 의존함을
  같은 사실로 인정한다. 결함은 그 기제가 atomic cut-off 를 제공하지 못하는 지점
  (cancelSession 의 queue 우회 / eventGate 의 per-iteration single-check)에서 발현한다.

  epic 으로 묶는 이유: 같은 도메인(acp-control-plane)의 turn 생애주기 동시성 관리라는 같은
  결함 클래스(SessionActorQueue/boolean-flag 외 atomic 프리미티브 부재 → 우회/누출)를
  공유하고, 위반된 기준선(handle 별 atomic 직렬화/회수 부재)도 동일하며, FIND 본문이 서로를
  cross_ref 한다. severity 는 최고값 P2 상속. (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "ACP turn 동시성: cancelSession actor-queue 우회로 cancel↔close 동일 handle 경합 + eventGate single-check TOCTOU 로 timeout 후 이벤트 1건 누출"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-29
---

# acp-control-plane: turn 동시성 atomic 프리미티브 부재 → cancel/close 경합 + eventGate 누출

## 공통 패턴

ACP turn 생애주기의 동시성은 SessionActorQueue(직렬화)와 eventGate(boolean emit 필터)
두 비-lock 기제에만 의존한다(`rg "Mutex|Semaphore|AsyncLock" src/acp/control-plane/` →
production match 0). 그 기제가 atomic cut-off 를 보장하지 못하는 두 지점에서 in-flight
작업이 직렬화 없이 경합/누출한다.

- **cancelSession 의 actor-queue 우회(FIND-001)**: `cancelSession` 의 active-turn 분기
  (manager.core.ts:1274-1289)는 응답성을 위해 `withSessionActor`(turn/close 가 쓰는 유일
  직렬화 기제)를 의도적으로 건너뛰고 즉시 `runtime.cancel` 한다. cancel 의 abort 가 oneshot
  turn 을 cancelled-done 으로 종료시키면, turn 의 finally(:1046-1058)가 같은 `AcpRuntimeHandle`
  로 `runtime.close` 를 호출하는데, close 는 actor queue 안·cancel 은 밖이라 manager 레벨에서
  둘을 순서 짓는 lock 이 없다. cancelPromise dedupe(:1277)는 cancel 중복만 막고 close 는 별개
  필드라 미차단. 동일 handle 에 cancel/close 가 직렬화 없이 in-flight → backend 구현에 따라
  cancel 이 ACP_TURN_FAILED 로 실패하거나 중복 종료/취소로 backend 상태 불일치(wrong-output).
- **eventGate single-check TOCTOU(FIND-002)**: `consumeAcpTurnEvents`(turn-stream.ts:32-48)는
  매 iteration 시작에 `eventGate.open` 을 1회만 검사하고, 통과 후 `await onOutputEvent`(:45)/
  `await onEvent`(:47)에서 양보한다. 그 await 경계 동안 turn timeout 핸들러
  (manager.core.ts:953)가 `eventGate.open=false` 로 닫아도, 이미 :33 검사를 통과한 현재
  이벤트는 caller 로 전달된다. timeout 으로 detach 된 turn(manager.core.ts:1138)이 gate-close
  직전 통과한 이벤트 최대 1건을 종료된 turn 의 caller 에 누출하고, :50 throw 가드는 닫힌 gate
  에서 error 이벤트를 삼킨다(wrong-output, 누출량 최대 1).

공통 기준선/반증: 두 FIND 모두 외부 lock 부재(rg production match 0)를 확정하고, eventGate 는
lock 이 아니라 단순 boolean 플래그(turn-stream.ts:11-13)임을 인정한다. detach 자체는 의도된
설계(commit 83e19ca469 "keep ACP turns on OpenClaw timeouts")이나, 단일 직렬화 기제를
우회/필터하는 구조라 atomic cut-off 가 없으면 경합/누출이 불가피하다. 기존 테스트
(manager.test.ts:2508 등)는 cancel-during-turn happy path 만 검증하고 cancel↔close 동시성이나
gate-close↔onEvent await 인터리빙은 미검증.

## 관련 FIND

- FIND-acp-control-plane-concurrency-001 (P2): `manager.core.ts:1274-1289`. cancelSession
  active-turn 분기가 SessionActorQueue 를 우회 → cancel 의 abort 가 oneshot turn 을 종료시키면
  turn finally 가 동일 handle 로 close 호출. cancel(queue 밖)↔close(queue 안)가 직렬화 없이
  in-flight → cancel ACP_TURN_FAILED 실패 또는 backend 중복 종료/취소 상태 불일치. oneshot
  모드에서만 close 분기 활성. cancelSession 은 abort.ts/task-registry/session-reset 등 다수
  production entry + 데몬 싱글톤이라 turn↔cancel 겹침이 정상 시나리오, P2.

- FIND-acp-control-plane-concurrency-002 (P3): `manager.turn-stream.ts:32-48`. eventGate.open
  per-iteration single-check 후 onEvent/onOutputEvent await 동안 timeout 이 gate 를 닫아도 현재
  이벤트는 caller 로 누출(최대 1건) + :50 가드가 닫힌 gate 에서 error 이벤트 삼킴. detach 된
  turn 루프와 onTimeout gate-close 의 인터리빙. timeout 발생 + 그 순간 backend emit 중일 때만
  발동하는 드문 타이밍 + 누출 1건이라 위생 수준, P3.
