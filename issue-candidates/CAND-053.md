---
candidate_id: CAND-053
type: single
finding_ids:
  - FIND-diagnostic-recovery-ordering-causality-001
cluster_rationale: |
  단독 결함 (clusterer.md Step 4): diagnostic-recovery 도메인의 나머지 두 FIND
  (FIND-002/003)는 복구 apply 경로의 staleness 가드 정합성이라는 축으로 서로를 명시
  cross_ref 하여 CAND-052(epic)로 묶이나, 본 FIND-001 은 그것들과 무관한 별도 축이다.
  FIND-001 의 cross_refs 는 비어 있고(frontmatter cross_refs: []), 결함의 본질은 *복구
  요청 dedup* 계층의 키 입도 불일치이지 *복구 결과 적용* 가드가 아니다.

  root_cause_chain 인용:
  - root_cause_chain[0] ("왜 같은 복구가 한쪽은 통과하고 한쪽은 skip 되는가?"):
    "coordinator 키는 generation 을 포함(`${ref}:${stateGeneration ?? "unknown"}`)하고
    runtime 키는 ref 만 쓴다. generation 이 바뀌면 coordinator 키는 달라지지만 runtime
    키는 동일하다"
    (evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:68)
  - root_cause_chain[1] ("왜 runtime 키는 generation 을 안 쓰는가?"): "runtime 의
    recoveryKey 는 `params.sessionKey?.trim() || params.sessionId?.trim()` 만 반환하고
    stateGeneration 필드를 참조하지 않는다"
    (evidence_ref: src/logging/diagnostic-stuck-session-recovery.runtime.ts:55-57)
  - root_cause_chain[3] ("왜 coordinator dedup 통과가 무의미한 요청을 발생시키는가?"):
    "coordinator 가 새 키로 dedup 을 통과하면 emitSessionRecoveryRequested 를 발행하고
    recover() 를 다시 부르지만, runtime 이 단일 ref 로 skip 하므로 실제 복구는 없고 중복
    requested 이벤트만 남아 관측 신뢰성이 깨진다"
    (evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:151-171)

  single 인 이유: 결함 surface 가 두 in-flight 추적 Set 의 키 정의 불일치
  (coordinator recoveryRequestsInFlight = `ref:generation` vs runtime recoveriesInFlight =
  `ref`)로, FIND-002/003 의 apply 가드 정합성과는 다른 코드 경로(요청 dispatch vs 결과
  apply)이자 다른 결함 클래스(dedup 입도 vs staleness 검증)다. FIND-001 본문도 self-check
  에서 "두 번째 recover() 가 runtime skip 대신 실제 실행되면 이중 abort 가능 — 단 그 경로는
  이 셀 범위 밖" 이라 하여 FIND-002/003 과 다른 표면을 명시한다. 같은 도메인이나 root cause
  가 무관하므로 별도 single CAND 로 분리(FIND 1 ↔ CAND 1). 영향은 관측/중복 이벤트 +
  dedup 계층 불일치로 한정(state mutate 없음, skipped non-mutating) → P2.
  (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "diagnostic 복구 in-flight 추적 2-Set 키 비대칭(coordinator ref:generation vs runtime ref) → 중복 session.recovery.requested 이벤트 + dedup 계층 불일치"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-29
---

# diagnostic-recovery: in-flight 키 비대칭으로 dedup 입도 불일치 → 중복 requested 이벤트

## 공통 패턴

같은 stuck-session 복구를 두 모듈이 각자의 Set 으로 in-flight 추적하는데 키 정의가 다르다.
coordinator 의 `recoveryRequestKey`(coordinator.ts:63-69)는 `${ref}:${stateGeneration ??
"unknown"}` 를, runtime 의 `recoveryKey`(runtime.ts:55-57)는 `ref`(sessionKey 또는
sessionId)만 쓴다. heartbeat 가 같은 세션을 다른 generation 으로 재요청하면 coordinator
dedup 은 새 키라 통과하지만(coordinator.ts:151) runtime dedup 은 같은 ref 라 skip 한다
(runtime.ts:88). 결과적으로 coordinator 가 emitSessionRecoveryRequested 로 두 번째 requested
이벤트를 발행하고 recover() 를 재호출하지만, runtime 은 already_in_flight 를 반환해 실제 복구는
일어나지 않는다. 두 dedup 계층의 입도가 어긋나 중복 requested 이벤트 + 추적 상태 불일치가 남는다.

발현 핵심: recover() 는 dynamic import + abort/drain await(settleMs 최대 15s, runtime.ts:173)로
in-flight 윈도우가 길고, 그 사이 logMessageQueued/logSessionStateChange/markDiagnosticSessionProgress
가 동기적으로 state.generation += 1(diagnostic.ts:657 등) 한다. heartbeat 가 30초 간격으로 같은
stuck 세션을 반복 판정하는 production 경로(diagnostic.ts:1268)에서 generation bump 가 끼어들면
현실적으로 발생한다.

기준선/반증: SessionStateValue 에 terminal 상태가 없어 terminal 보호와 무관. runtime 은 ref
단위로 직렬화하나 coordinator 는 generation 을 키에 섞어 같은 ref 의 다른 generation 을 별개로
취급 → 직렬화가 ref 단위로만 성립. skipped outcome 은 recoveryOutcomeMutatesSessionState=false
(diagnostic-session-recovery.ts:88-93)라 state 를 mutate 하지 않으므로 데이터 손상은 아니고,
영향은 관측(복구 시도 횟수/latency 메트릭 부풀림, requested↔completed 1:1 불일치) + 두 dedup
계층 일관성 상실로 한정된다.

## 관련 FIND

- FIND-diagnostic-recovery-ordering-causality-001 (P2):
  `diagnostic-session-recovery-coordinator.ts:63-69`. coordinator(`ref:generation`)와
  runtime(`ref`)의 in-flight 키 입도 불일치로, generation bump 후 재요청이 coordinator
  dedup 은 통과(새 키)·runtime dedup 은 skip(같은 ref) → 두 번째 session.recovery.requested
  이벤트 발행되나 실제 복구 없음. 복구 관측 메트릭 부풀림 + requested/completed 비대칭
  (wrong-output). state mutate 없음(skipped non-mutating)이라 데이터 손상 아님, P2.
  (self-check: T1 이 직전 완료되어 runtime 키가 비면 두 번째 복구가 실제 실행되어 이중
  abort/drain 가능 — abort idempotency 는 셀 범위 밖이라 미확인, idempotent 아니면 severity
  상향 가능.)
