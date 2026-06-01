---
candidate_id: CAND-056
type: single
finding_ids:
- FIND-infra-delivery-queue-ordering-causality-001
origin:
  supersedes_cand: CAND-050
  closed_pr: 88029-na  # see note: closed PR is #88016 (SOL-0018)
  closed_pr_number: 88016
  closed_sol: SOL-0018
  upstream_substrate_change: 88665  # "refactor: move delivery queues to SQLite"
cluster_rationale: |
  CAND-050 (SOL-0018 / 닫힌 PR #88016) 의 SQLite-아키텍처 재검증 CAND.

  upstream #88665 ("refactor: move delivery queues to SQLite", commit 1af4c035e4)
  가 session-delivery 큐 저장 계층을 파일기반에서 공용 SQLite backing
  (src/infra/delivery-queue-sqlite.ts) 으로 재작성했다. CAND-050 / SOL-0018 의
  결함 진단·repro·proof 는 전부 파일기반 큐 시절에 검증된 것이라, 현재 upstream
  (7562afdca3) 기준으로 결함 생존 여부와 fix 형태를 재검증한다 (CAL-007).

  결함은 substrate 변경에도 그대로 살아있다(코드 레벨): 복구 경로
  drainQueuedEntry (src/infra/session-delivery-queue-recovery.ts:111) 와
  recoverPendingSessionDeliveries (:214) 는 #88665 가 건드리지 않았다. drain 은
  여전히 deliver(entry) 성공 직후 ackSessionDelivery(:121) 로 entry 를 제거하는
  at-least-once 흐름이고, deliver 성공 후 ack 전 crash 시 entry 가 pending 으로
  남아 다음 복구가 같은 agentTurn 을 reconciliation 없이 blind replay 한다.

  변경된 것은 fix 의 자리다. SQLite 이관이 공용 backing 에 recovery_state 컬럼을
  이미 추가했다 (delivery-queue-sqlite.ts:27 recoveryState? / :38 recovery_state /
  :66 load 매핑 / :118 persist). 그러나 session-delivery 쪽은 그 컬럼을 쓰지 않는다:
  QueuedSessionDelivery 타입(session-delivery-queue-storage.ts)에 recoveryState
  필드가 노출돼 있지 않고, 마커를 set/clear 하는 함수도 없으며, drainQueuedEntry 가
  recovery_state 를 읽어 blind replay 를 거부하는 분기도 없다. 따라서 fix 는
  파일기반 마커를 재구현하는 게 아니라 *이미 존재하는 recovery_state 컬럼을
  session-delivery drain 에 배선* 하는 것으로 단순화된다 (해결책 자체는 본 CAND
  범위 밖).

  비대칭 warrant 는 CAND-050 과 동일하되 현행 코드로 갱신: 평행 outbound 큐는
  복구 시 recovery_state 마커를 보고 adapter reconcile 후에만 replay 하며 확인
  불가 시 blind replay 를 거부한다 (src/infra/outbound/delivery-queue-recovery.ts).
  #88665 로 두 큐가 같은 SQLite backing + recovery_state 컬럼을 공유하게 됐는데도
  session 큐만 그 가드를 안 쓴다 — 동일 도메인 내 두 큐의 정책 divergence.

  single 인 이유: 도메인에 묶을 다른 신규 FIND 없음 (FIND 1 ↔ CAND 1).
proposed_title: 'fix(infra): wire session-delivery drain recovery guard onto the shared
  SQLite recovery_state column (unacked agentTurn blind replay → crash 후 턴 중복 실행)'
proposed_severity: P1
existing_issue: null
created_at: 2026-06-01
pre_sol_proof:
  status: pending
  note: |
    CAND-050 의 옛 proof (PROOF-CAND-050-pre-20260529-070811.md, deliverCount=2,
    recoveryStateAfterCrash=field-absent) 는 파일기반 큐 기준이라 무효. SQLite
    아키텍처(현재 upstream) 에서 pre-sol real-behavior-proof 로 결함 생존을 재확인하는
    것이 본 사이클의 게이트다. Node 24 확보(StatementSync.columns) 로 로컬 실측 가능.
    재현되면 cross-review → 새 SOL(recovery_state 배선) → post-sol proof → PR.
    재현 안 되면 abandon (false-positive-by-reproduction / superseded).
---

# session-delivery 큐 (SQLite 이관 후): unacked agentTurn blind replay → 턴 중복 실행 + 응답 중복 전송

## 배경: 왜 CAND-050 을 재검증하는가

CAND-050 → SOL-0018 → PR #88016 은 session-delivery 큐의 복구 경로가 crash 후
unacked agentTurn 을 blind replay 하는 결함을, 파일기반 큐에 recoveryState 마커를
추가해 outbound 큐와 같은 reconcile-or-refuse 정책으로 맞추는 fix 였다.

PR 발행 후 upstream #88665("refactor: move delivery queues to SQLite",
commit `1af4c035e4`) 가 delivery 큐 저장 계층 전체를 공용 SQLite backing
(`src/infra/delivery-queue-sqlite.ts`, 신규 +239) 으로 재작성하면서 PR #88016 이
CONFLICTING 이 됐다. 파일기반 마커를 SQLite 위로 force-fit 하는 대신, 현재 upstream
기준으로 (a) 결함이 여전히 살아있는지, (b) fix 가 새 아키텍처에서 어떤 형태인지를
재검증하기 위해 PR #88016 을 close 하고 본 CAND 로 파이프라인을 다시 돈다.

## 공통 패턴 (현재 upstream 7562afdca3 기준)

복구 경로 `drainQueuedEntry`(`src/infra/session-delivery-queue-recovery.ts:111`)는
entry 를 `deliver(entry)` 한 뒤 `ackSessionDelivery`(:121)로 큐 엔트리를 제거한다
(at-least-once). #88665 는 이 recovery 모듈을 변경하지 않았다 —
`recoverPendingSessionDeliveries`(:214) 포함 drain/ack 흐름이 그대로다.

deliver 성공(agentTurn 재실행 + 플랫폼 응답 전송 완료) 직후 ack 직전에 프로세스가
죽으면 엔트리가 pending 으로 남고, 다음 복구가 그 엔트리를 다시 deliver 한다.
deliver 결과는 "이미 전송됨"(unack)과 "전송 전 transient 실패"를 구분할 정보가
없어 무조건 재전달된다 → 같은 agentTurn 두 번 실행 + 응답 중복 전송.

## substrate 변경이 fix 자리를 바꿈

#88665 로 저장 계층이 SQLite 로 이동하면서 공용 backing 에 recovery_state 컬럼이
이미 생겼다:

- `src/infra/delivery-queue-sqlite.ts:27` — `recoveryState?: string` (메타 타입)
- `:38` — `recovery_state: string | null` (행 스키마)
- `:66` — load 시 `recovery_state` → `recoveryState` 매핑
- `:118` / `:136` — upsert 시 `recovery_state` persist

그러나 session-delivery 쪽은 이 컬럼을 쓰지 않는다:

- `QueuedSessionDelivery`(`session-delivery-queue-storage.ts`) 타입은 여전히
  `id / enqueuedAt / retryCount / lastAttemptAt / lastError` 만 노출 —
  `recoveryState` 필드 미노출.
- session-delivery storage 에 send-attempt/outcome 마커를 set/clear 하는 함수 없음.
- `drainQueuedEntry` 에 recovery_state 를 읽어 blind replay 를 거부하는 분기 없음.

즉 fix 는 파일기반 마커 재구현이 아니라 *이미 있는 recovery_state 컬럼을
session-delivery drain 에 배선* 하는 최소 변경이 된다 (해결책 자체는 본 CAND 범위 밖).

## 비대칭 (핵심 warrant)

평행 outbound 큐는 복구 시 recovery_state 마커 상태면 adapter 로 실제 전송 여부를
확인한 뒤에만 replay 하고 확인 불가 시 blind replay 를 명시 거부한다
(`src/infra/outbound/delivery-queue-recovery.ts`). #88665 로 두 큐가 같은 SQLite
backing 과 recovery_state 컬럼을 공유하게 됐는데도 session 큐만 그 가드를 안 쓴다 —
동일 도메인 안에서 두 큐의 보장 수준이 갈린다.

## 관련 FIND

- FIND-infra-delivery-queue-ordering-causality-001 (P1):
  production enqueue caller 는 restart continuation(server-restart-sentinel.ts) —
  게이트웨이 재시작 후 중단된 사용자 턴을 이어 실행한다. deliver 가 agentTurn 일 때
  `dispatchAssembledChannelTurn` 으로 (a) 에이전트 턴 재실행(LLM/툴 등 비-idempotent
  side-effect) + (b) 응답 플랫폼 전송 을 한다. deliver 성공 후 ack 전 crash 시 다음
  복구가 같은 턴을 다시 실행해 응답이 두 번 전달되고 비-idempotent 액션이 중복
  수행될 수 있다. restart continuation 은 정의상 crash/restart 직후 경로라 활성화가
  자연스러움 → P1.

## 게이트 (다음 액션)

pre-sol real-behavior-proof 로 SQLite 아키텍처(현재 upstream)에서 위 blind-replay 가
실제 재현되는지 확인한다 (Node 24 필수 — StatementSync.columns). 재현되면 정상
파이프라인(cross-review → SOL → post-sol → PR), 재현 안 되면 abandon.
