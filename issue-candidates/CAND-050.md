---
candidate_id: CAND-050
type: single
finding_ids:
  - FIND-infra-delivery-queue-ordering-causality-001
cluster_rationale: |
  단독 결함 (clusterer.md Step 4): infra-delivery-queue 도메인의 유일한 신규 FIND.
  session-delivery 큐의 복구 경로 drainQueuedEntry 가 deliver 성공(agentTurn 재실행 +
  플랫폼 전송 완료) 직후 ack 직전 crash 시, 큐 파일이 pending 으로 남아 다음 복구가
  동일 agentTurn 을 reconciliation 없이 blind replay 한다.

  root_cause_chain 인용:
  - root_cause_chain[0] ("왜 unack 된 성공 전달이 그대로 재전달되는가"): "drainQueuedEntry
    가 deliver 호출 결과를 recovered/failed 두 갈래로만 분류하고, 재진입 시 '이미
    전송되었는지' 를 묻는 reconciliation 분기가 없다. deliver 직전·직후에
    send-attempt/outcome 마커도 기록하지 않는다"
    (evidence_ref: src/infra/session-delivery-queue-recovery.ts:107)
  - root_cause_chain[2] ("왜 평행 outbound 큐와 정책이 갈리는가"): "outbound 큐는 deliver 가
    send_attempt_started/unknown_after_send 마커를 기록하고(deliver.ts:600,618), 복구 시 그
    상태면 adapter reconcileUnknownSend 로 실제 전송 여부를 확인한 뒤에만 replay 하며 확인
    불가 시 blind replay 를 거부한다. session 큐는 이 메커니즘 전체를 복제하지 않았다"
    (evidence_ref: src/infra/outbound/delivery-queue-recovery.ts:370)

  single 인 이유: 이 도메인에 묶을 다른 신규 FIND 가 없다(FIND 1 ↔ CAND 1). 결함의 본질은
  session 큐 storage 에 recoveryState/send-attempt 마커 자리 자체가 없고(QueuedSessionDelivery
  타입에 필드 부재, storage.ts:62) idempotencyKey(enqueue-only) / expectedSessionId(세션 변경만
  차단) 가드가 unack 재-deliver 를 막지 못한다는 점이다. 비대칭 warrant 의 기준선이 평행
  outbound 큐(reconcileUnknownSend + blind replay 거부)라는 점은 cross-store 가 아니라 동일
  도메인 내 두 큐의 정책 divergence 다 — 별도 도메인이 아니므로 단일 CAND. (해결책 자체는 본
  CAND 범위 밖.)
proposed_title: "session-delivery 큐: unacked agentTurn 을 reconciliation 없이 blind replay → crash 후 턴 중복 실행 + 응답 중복 전송 (outbound 큐 대비 비대칭)"
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
---

# session-delivery 큐: unacked agentTurn blind replay → 턴 중복 실행 + 응답 중복 전송

## 공통 패턴

session-delivery 큐의 복구 경로 `drainQueuedEntry`(session-delivery-queue-recovery.ts:105-124)는
entry 를 `deliver(entry)` 한 뒤 `ackSessionDelivery` 로 큐 파일을 제거한다(at-least-once 큐).
deliver 성공(agentTurn 재실행 + 플랫폼 응답 전송 완료) 직후 ack 직전에 프로세스가 죽으면 큐
파일이 pending 으로 남고, 다음 복구가 그 entry 를 다시 deliver 한다. 그러나 deliver 결과는
"이미 전송됨"(unack)과 "전송 전 transient 실패" 를 구분할 정보가 큐에 없어 무조건 재전달된다 →
같은 agentTurn 두 번 실행 + 응답 중복 전송.

근본적으로 `QueuedSessionDelivery` 타입에 recoveryState/platformSendStartedAt 필드가 없고
markStarted/markUnknown 류 함수가 storage 에 정의되지 않아(grep
`recoveryState|markSession|attemptStarted|platformSend` → session 파일 match 0) 인과 정보를
영속할 자리 자체가 없다. 보조 가드도 무력하다 — idempotencyKey 는 enqueue 중복 삽입만
dedup(동일 id 재-deliver 엔 무효), expectedSessionId 는 세션이 *바뀐* 경우만 skip(턴 이미
실행/전송됨은 미검사).

비대칭(핵심 warrant): 평행 outbound 큐는 정확히 이 시나리오를 deliver 직전
`markDeliveryPlatformSendAttemptStarted`(deliver.ts:600) + 결과 후
`markDeliveryPlatformOutcomeUnknown`(deliver.ts:618) 마커로 영속하고, 복구 시 그 상태면
`reconcileUnknownQueuedDelivery` 로 adapter 에 실제 전송 여부를 물어(sent/not_sent) 확인 후에만
replay 하며 확인 불가 시 blind replay 를 명시 거부한다(delivery-queue-recovery.ts:417 "refusing
blind replay without adapter reconciliation"). session 큐는 이 메커니즘 전체를 복제하지 않아
같은 도메인 안에서 두 큐의 보장 수준이 갈린다.

## 관련 FIND

- FIND-infra-delivery-queue-ordering-causality-001 (P1):
  `session-delivery-queue-recovery.ts:105-124`. session-delivery 큐의 production enqueue
  caller 는 restart continuation(server-restart-sentinel.ts:606) — 게이트웨이 재시작 후 중단된
  사용자 턴을 이어 실행한다. deliver=`deliverQueuedSessionDelivery` 가 agentTurn 일 때
  `dispatchAssembledChannelTurn` 으로 (a) 에이전트 턴 재실행(LLM/툴 등 비-idempotent
  side-effect) + (b) 응답 플랫폼 전송 을 한다. deliver 성공 후 ack 전 crash 가 들어가면 다음
  복구가 같은 턴을 다시 실행해 응답이 두 번 전달되고 턴 내부 비-idempotent 액션이 중복 수행될
  수 있다(wrong-output). crash window 는 좁으나 restart continuation 은 정의상 crash/restart
  직후 경로라 활성화가 자연스러움 → P1.
