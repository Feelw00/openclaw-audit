---
id: FIND-diagnostic-recovery-ordering-causality-001
cell: diagnostic-recovery-ordering-causality
title: 2-Set in-flight 키 비대칭 (ref:generation vs ref) 으로 dedup 입도 불일치
file: src/logging/diagnostic-session-recovery-coordinator.ts
line_range: 63-69
evidence: "```ts\nfunction recoveryRequestKey(request: StuckSessionRecoveryRequest):\
  \ string | undefined {\n  const ref = request.sessionKey?.trim() || request.sessionId?.trim();\n\
  \  if (!ref) {\n    return undefined;\n  }\n  return `${ref}:${request.stateGeneration\
  \ ?? \"unknown\"}`;\n}\n```\n"
symptom_type: ordering-causality-gap
problem: 같은 논리 복구가 두 곳에서 in-flight 추적되는데 키 정의가 다르다. coordinator (`recoveryRequestKey`,
  :63-69)는 `ref:generation`, runtime (`recoveryKey`, runtime.ts:55-57)는 `ref` 만 쓴다.
  heartbeat 가 같은 세션을 다른 generation 으로 재요청하면 coordinator dedup 은 통과(새 키)하지만 runtime
  dedup 은 skip(같은 ref) 하여, coordinator 가 발사한 요청이 runtime 단에서 already_in_flight 로 흡수된다.
  두 dedup 계층의 입도가 어긋나 중복 session.recovery.requested 이벤트가 발행되고, in-flight 추적이 일관되지
  않는다.
mechanism: '1. heartbeat tick T1: 세션 S(generation=G, expectedState=processing)가 stuck
  → requestStuckSessionRecovery(coordinator.ts:1268 caller). coordinator inFlightKey=`S:G`
  를 recoveryRequestsInFlight 에 add (coordinator.ts:166).

  2. recover() = recoverStuckSession (diagnostic.ts:159) → dynamic import 후 recoverStuckDiagnosticSession(runtime.ts:84)
  async 실행. runtime key=`S` 를 recoveriesInFlight 에 add (runtime.ts:98). abort/drain
  await 로 수백 ms~settleMs(15s) 동안 in-flight.

  3. 그 사이 logMessageQueued(diagnostic.ts:657) 가 generation 을 G+1 로 bump (state 는 idle/processing
  유지).

  4. heartbeat tick T2(30초 후): 같은 세션이 여전히 stuck 으로 판정 → requestStuckSessionRecovery
  재호출. coordinator inFlightKey=`S:G+1` → recoveryRequestsInFlight.has(`S:G+1`)=false
  → dedup **통과** (coordinator.ts:151). emitSessionRecoveryRequested(coordinator.ts:168)
  가 두 번째 requested 이벤트 발행.

  5. recover() 재진입 → runtime recoveryKey=`S` → recoveriesInFlight.has(`S`)=true (T1
  아직 진행 중) → runtime 이 skipped/already_in_flight 반환(runtime.ts:88-96).

  6. 결과: coordinator 는 "새 복구를 시작했다"고 믿고 inFlightKey `S:G+1` 을 add 했지만 실제 복구는 일어나지
  않았고, runtime 의 단일 ref 키가 진실의 원천이 되어 두 계층의 추적 상태가 어긋난다.

  '
root_cause_chain:
- why: 왜 같은 복구가 한쪽은 통과하고 한쪽은 skip 되는가?
  because: coordinator 키는 generation 을 포함(`${ref}:${stateGeneration ?? "unknown"}`)하고
    runtime 키는 ref 만 쓴다. generation 이 바뀌면 coordinator 키는 달라지지만 runtime 키는 동일하다.
  evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:68
- why: 왜 runtime 키는 generation 을 안 쓰는가?
  because: runtime 의 recoveryKey 는 `params.sessionKey?.trim() || params.sessionId?.trim()`
    만 반환하고 stateGeneration 필드를 참조하지 않는다.
  evidence_ref: src/logging/diagnostic-stuck-session-recovery.runtime.ts:55-57
- why: 왜 generation 이 in-flight 윈도우 중 바뀔 수 있는가?
  because: logMessageQueued / logSessionStateChange / markDiagnosticSessionProgress
    가 전부 동기적으로 state.generation += 1 하며, heartbeat 의 async recover() 가 완주하기 전에 호출될
    수 있다.
  evidence_ref: src/logging/diagnostic.ts:657
- why: 왜 coordinator dedup 통과가 무의미한 요청을 발생시키는가?
  because: coordinator 가 새 키로 dedup 을 통과하면 emitSessionRecoveryRequested(coordinator.ts:168)
    를 발행하고 recover() 를 다시 부르지만, runtime 이 단일 ref 로 skip 하므로 실제 복구는 없고 중복 requested
    이벤트만 남아 관측 신뢰성이 깨진다.
  evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:151-171
impact_hypothesis: wrong-output
impact_detail: '정성:

  - session.recovery.requested 이벤트가 동일 세션에 대해 generation 마다 중복 발행되어, 메트릭/관측(복구 시도
  횟수, 복구 latency)이 실제보다 부풀려진다. runtime 은 skipped 만 반환하므로 requested 와 completed(stale/skipped)
  가 1:1 로 맞지 않는다.

  - coordinator 의 recoveryRequestsInFlight 에는 `S:G`, `S:G+1` 등 여러 키가 동시에 쌓일 수 있다.
  T1 의 finally clearInFlight(coordinator.ts:201)는 `S:G` 만 지우므로, runtime 이 skip 한 `S:G+1`
  요청의 키는 그 요청의 finally 가 도는 시점에 제거되지만, 그 사이 dedup 입도 불일치로 인해 coordinator 계층은 "중복 아님"으로
  오판한다.

  - 데이터 손상은 아님 (skipped 는 state 를 mutate 하지 않음, diagnostic-session-recovery.ts:88-93).
  영향은 관측/중복 이벤트 + 두 dedup 계층의 일관성 상실로 한정 → severity P2.

  - 재현: 단일 세션을 stuck 상태로 두고 recover() 가 settleMs(최대 15s) await 하는 동안 logMessageQueued
  로 generation 을 bump 한 뒤 다음 heartbeat tick 을 돌리면 coordinator 가 두 번째 requested 이벤트를
  발행하고 runtime 은 already_in_flight 를 반환한다.

  '
severity: P2
counter_evidence:
  path: src/logging/diagnostic-stuck-session-recovery.runtime.ts
  line: 55-57
  reason: 'seq/generation 가드·terminal 보호·await 직렬화 3축 + 실제 도착순서 탐색:


    | 경로 | 실행 조건 |

    |---|---|

    | coordinator recoveryRequestsInFlight (key=ref:generation) | dedup 통과 가능 (generation
    변하면 키 달라짐) |

    | runtime recoveriesInFlight (key=ref) | unconditional skip (같은 ref 면 무조건 already_in_flight)
    |


    1) terminal 보호: SessionStateValue 에 terminal(done/failed) 없음(state.ts:1). 비대칭
    자체는 terminal 무관.

    2) await 직렬화: runtime 은 recoveriesInFlight 로 같은 ref 의 동시 복구를 막지만(runtime.ts:88),
    coordinator 는 같은 ref 의 다른 generation 요청을 막지 못함 → 직렬화가 ref 단위로만 성립, generation
    교차 시 비대칭.

    3) 기존 테스트: `rg -n "in_flight|already_in_flight|recoveriesInFlight" src/logging/*.test.ts`
    — runtime.test.ts 에 already_in_flight 단위 테스트는 있으나 coordinator 키와 runtime 키의 **비대칭**(같은
    ref 다른 generation)을 검증하는 테스트는 없음. integration.test.ts 에도 두 계층 dedup 교차 시나리오 없음.

    4) 실제 도착순서: heartbeat 가 30초 간격으로 같은 stuck 세션을 반복 판정 + recover() 가 settleMs(15s)까지
    await 하는 production 경로(diagnostic.ts:1268)에서 generation bump 가 끼어들면 현실적으로 발생.
    단 결과가 skipped(non-mutating)라 영향은 관측 한정 → P2.

    '
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
cross_refs: []
domain_notes_ref: domain-notes/diagnostic-recovery.md
---
# 2-Set in-flight 키 비대칭 (ref:generation vs ref) 으로 dedup 입도 불일치

## 문제

같은 stuck-session 복구를 두 모듈이 각자의 Set 으로 in-flight 추적하는데 키 정의가 다르다.
coordinator 의 `recoveryRequestKey` 는 `${ref}:${stateGeneration ?? "unknown"}` 를,
runtime 의 `recoveryKey` 는 `ref`(sessionKey 또는 sessionId)만 쓴다. generation 이 바뀐 재요청은
coordinator dedup 을 통과하지만 runtime dedup 은 통과하지 못해, 두 계층의 dedup 입도가 어긋난다.

## 발현 메커니즘

1. heartbeat tick T1: 세션 S(generation=G) stuck → coordinator 가 `S:G` add, recover() async 시작.
2. recover() = recoverStuckDiagnosticSession 이 runtime key `S` 를 add 하고 abort/drain await.
3. await 윈도우 중 logMessageQueued 가 generation 을 G+1 로 bump.
4. heartbeat tick T2: coordinator inFlightKey `S:G+1` → recoveryRequestsInFlight 에 없음 → dedup 통과.
5. emitSessionRecoveryRequested 가 두 번째 requested 이벤트 발행, recover() 재진입.
6. runtime key `S` 는 이미 in-flight(T1 진행 중) → skipped/already_in_flight 반환.
7. coordinator 는 복구를 시작했다고 믿지만 실제로는 skip 됨 → 두 dedup 계층 상태 불일치.

## 근본 원인 분석

1. coordinator 키(:68)는 generation 포함, runtime 키(runtime.ts:55-57)는 ref only → 입도 불일치.
2. generation 은 logMessageQueued(:657) 등 동기 mutator 가 +1 하며 async recover() 윈도우 중 변할 수 있다.
3. coordinator 의 dedup 통과는 emitSessionRecoveryRequested + recover() 재호출을 유발하지만(:151-171),
   runtime 이 ref 단위로 skip 하므로 실제 복구 없이 중복 이벤트만 남는다.

## 영향

- **impact_hypothesis: wrong-output** — 복구 관측(시도 횟수/latency 메트릭) 부풀림. requested 와
  completed 가 1:1 로 안 맞음.
- coordinator 의 in-flight Set 에 generation 별 키가 누적될 수 있으나 각 요청의 finally 가 자기 키만
  지운다.
- state mutate 는 없음(skipped 는 non-mutating, diagnostic-session-recovery.ts:88-93) → 데이터 손상 아님.
- severity P2: 영향이 관측/중복 이벤트 + dedup 계층 불일치로 한정.

## 반증 탐색

- **seq/generation 가드**: runtime 은 ref 단위로 직렬화하지만 coordinator 는 generation 을 키에 섞어
  같은 ref 의 다른 generation 을 별개로 취급 → 직렬화가 ref 단위로만 성립.
- **terminal 보호**: SessionStateValue 에 terminal 상태 없음(state.ts:1). 비대칭과 무관.
- **기존 테스트**: runtime.test.ts 에 already_in_flight 단위 테스트는 있으나 coordinator/runtime 키
  비대칭(같은 ref 다른 generation)을 검증하는 테스트는 없음. integration.test.ts 에도 교차 dedup 없음.
- **실제 도착순서**: heartbeat 30초 반복 + recover() settleMs(15s) await + generation bump 가 production
  에서 현실적으로 겹친다(diagnostic.ts:1268). 단 결과가 skipped 라 영향 한정.

## Self-check

### 내가 확실한 근거
- coordinator 키는 `${ref}:${stateGeneration ?? "unknown"}`(:68), runtime 키는 ref only(runtime.ts:55-57).
- generation 은 logMessageQueued(:657)/logSessionStateChange(:869)/markDiagnosticSessionProgress(:915)가 동기 +1.
- skipped outcome 은 recoveryOutcomeMutatesSessionState=false (diagnostic-session-recovery.ts:88-93) → state 불변.
- recover() 는 dynamic import + abort/drain await 로 in-flight 윈도우가 길다(runtime.ts:173).

### 내가 한 가정
- heartbeat 가 같은 stuck 세션을 연속 tick 에서 반복 판정한다고 가정(generation 이 바뀌어도 stuck 유지).
- production 에서 settleMs 동안 새 메시지가 큐잉되어 generation 이 bump 되는 빈도가 무시 못 할 수준이라고 가정.

### 확인 안 한 것 중 영향 가능성
- coordinator dedup 통과 후 두 번째 recover() 가 runtime skip 으로 끝나는 대신, T1 이 그 직전 완료되어
  runtime 키가 비어 있으면 **실제로 두 번째 복구가 실행**된다. 이 경우 같은 세션에 대해 중복 abort/drain
  이 발생할 수 있으나(이중 abort), abort 가 idempotent 한지(runs.ts)는 이 셀 범위 밖이라 미확인.
  idempotent 하지 않다면 severity 상향 가능.
