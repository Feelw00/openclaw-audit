---
id: FIND-diagnostic-recovery-ordering-causality-002
cell: diagnostic-recovery-ordering-causality
title: idle 분기 generation+1 완화가 async 윈도우 중 새 메시지/작업의 인과를 무시하고 stale 복구결과로 idle 덮어씀
file: src/logging/diagnostic-session-recovery-coordinator.ts
line_range: 98-117
evidence: "```ts\nconst stateIsCurrent =\n  expectedState === \"idle\" &&\n  params.request.stateGeneration\
  \ !== undefined &&\n  params.outcome.action === \"abort_embedded_run\"\n    ? currentState?.state\
  \ === \"idle\" &&\n      (currentGeneration === requestGeneration || currentGeneration\
  \ === requestGeneration + 1)\n    : isDiagnosticSessionStateCurrent({\n        sessionId:\
  \ params.request.sessionId,\n        sessionKey: params.request.sessionKey,\n  \
  \      generation: params.request.stateGeneration,\n        state: expectedState,\n\
  \      });\nif (!stateIsCurrent) {\n  emitSessionRecoveryCompleted({\n    request:\
  \ params.request,\n    outcome: params.outcome,\n    stale: true,\n  });\n  return;\n\
  }\n```\n"
symptom_type: ordering-causality-gap
problem: idle 세션의 abort_embedded_run 복구는 staleness 가드를 `isDiagnosticSessionStateCurrent`
  (generation 정확 일치)에서 `generation === requestGeneration || generation === requestGeneration+1`
  로 완화한다. recover() 의 async 윈도우 동안 logMessageQueued 가 generation 을 한 번 bump(+1) 하여
  새 메시지가 큐잉되어도, 이 완화가 G+1 을 여전히 current 로 받아들여 G 시점에 계산된 stale 복구결과(queueDepth 0 으로
  clear)를 적용한다. 결과적으로 윈도우 중 도착한 follow-up 메시지의 queueDepth 가 사라지고, state 가 무조건 idle
  로 덮어써진다.
mechanism: '1. heartbeat: idle 세션 S 에 큐잉 작업 존재(logMessageQueued 가 generation 을 이미
  G 로 만든 상태). isIdleQueuedRecoverableSessionStall 판정(diagnostic.ts:1247) → recover
  요청에 stateGeneration=G, expectedState=idle 전달(diagnostic.ts:1278).

  2. recover() = recoverStuckDiagnosticSession async 실행. runtime 진입부 isDiagnosticSessionStateCurrent(G)
  통과(runtime.ts:101). abort/drain await — 이 사이 generation 은 G 그대로 잠금 안 됨.

  3. async 윈도우 중 **두 번째 follow-up 메시지 도착**: logMessageQueued(diagnostic.ts:645) →
  queueDepth+1, generation = G+1, state 는 idle 유지(:657).

  4. recover() 완료. outcome = {status:aborted, action:abort_embedded_run, released>0,
  queuedCount 없음} ← G 시점 세계 기준.

  5. applyRecoveryOutcomeToDiagnosticState(coordinator.ts:98-103): expectedState==idle
  && action==abort_embedded_run 분기 → currentState.state===idle(참) && (currentGeneration===G?
  아님, ===G+1? **참**) → stateIsCurrent=true.

  6. 가드 통과 → coordinator.ts:118-131 실행: state.state=idle 강제, generation=G+2, recoveryOutcomeClearsQueuedSessionState(outcome)=true(released>0
  && queuedCount 0, diagnostic-session-recovery.ts:98-100) → queueDepth=0.

  7. 3번에서 도착한 follow-up 의 queueDepth+1 이 0 으로 덮어써진다. 그 메시지는 큐 카운트에서 사라지고, 다음 heartbeat
  는 queueDepth 0 인 idle 세션을 stuck 으로 보지 않아 복구도 안 한다 → 처리 누락.

  '
root_cause_chain:
- why: 왜 G+1 세대의 세션에 G 시점 복구결과가 적용되는가?
  because: idle 분기가 정확 generation 일치 대신 `generation === requestGeneration+1` 을 명시적으로
    허용하여, 한 번의 generation bump 를 staleness 로 치지 않는다.
  evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:103
- why: 왜 그 한 번의 bump 가 인과적으로 중요한가?
  because: logMessageQueued 는 generation 을 +1 하면서 queueDepth 도 +1 한다. G+1 은 "새 작업이
    추가된 세계"이며 G 시점 outcome 은 이 작업을 모른다.
  evidence_ref: src/logging/diagnostic.ts:655-657
- why: 왜 stale outcome 적용이 queueDepth 를 손상시키는가?
  because: recoveryOutcomeClearsQueuedSessionState 가 released>0 인 aborted 를 queueDepth=0
    으로 clear 하는데(:98-100), 이는 G 시점 큐를 가정한 것이라 G+1 에서 추가된 메시지까지 함께 0 으로 만든다.
  evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:127
- why: 왜 처리 누락으로 이어지는가?
  because: queueDepth 가 0 으로 박히면 다음 heartbeat 의 isIdleQueuedRecoverableSessionStall
    가 큐 없는 idle 로 보고 stuck 판정/복구를 안 한다. follow-up 메시지가 디스패치되지 않은 채 잊혀진다.
  evidence_ref: src/logging/diagnostic.ts:1247-1255
impact_hypothesis: data-loss
impact_detail: '정성 + 지속성:

  - async recover() 윈도우(dynamic import + abortAndDrainEmbeddedAgentRun settleMs 최대
  15s, runtime.ts:176) 중에 도착한 follow-up 사용자 메시지의 queueDepth 가 stale 복구결과에 의해 0 으로
  덮어써진다. 해당 메시지는 큐 회계에서 소실된다.

  - 지속성: queueDepth=0 인 idle 세션은 다음 heartbeat 의 stuck 판정을 통과하지 못해(diagnostic.ts:1252-1255)
  영구히 복구 대상에서 빠진다. 즉 한 번 소실되면 자동 복구 없음. 새 메시지가 또 와서 generation/queueDepth 를 올려야만 다시
  활성화된다.

  - generation+1 완화는 정확히 "한 번의 bump" 를 허용하므로, async 윈도우 중 메시지가 1개 도착하는 가장 흔한 케이스에서
  발동한다. 2개 이상 도착하면 G+2 가 되어 stale 처리(가드 실패)되므로 오히려 안전해지는 역설.

  - 재현(R-7, production branch 동일): idle 세션 + 큐 1개 → heartbeat 가 idle abort 복구 dispatch(test
  diagnostic.test.ts:934 동일 경로) → recover() resolve 직전 logMessageQueued 한 번 호출 → outcome
  적용 시 queueDepth 0 확인. 비현실적 지연 강제 아님: recover() 가 import+await 로 자연스럽게 1 tick 이상
  늦고, 그 사이 메시지 1개 도착은 평범한 시나리오.

  '
severity: P1
counter_evidence:
  path: src/logging/diagnostic-session-state.ts
  line: 198-215
  reason: 'generation 가드·terminal·직렬화·테스트 의도 4축:


    | 경로 | 실행 조건 |

    |---|---|

    | non-idle 분기 isDiagnosticSessionStateCurrent | guarded (generation 정확 일치, state.ts:211-214)
    — async 윈도우 중 1회 bump 면 stale 처리 → 안전 |

    | idle+abort_embedded_run 분기 | **완화** (generation===G OR ===G+1, coordinator.ts:103)
    — 1회 bump 를 current 로 수용 |

    | state.state=idle mutate (coordinator.ts:120) | 가드 통과 시 unconditional |

    | queueDepth clear (coordinator.ts:127) | recoveryOutcomeClearsQueuedSessionState=true
    면 unconditional 0 |


    1) 이 완화는 **의도된 것**: 테스트 diagnostic.test.ts:934 "recovers idle queued embedded-run
    stalls" 가 logMessageQueued(generation bump) 후 idle 복구가 queueDepth 0 으로 끝나는 것을
    정상으로 못박음(:995 expect queueDepth 0). 단 그 테스트는 **단 한 번의** logMessageQueued 만 하고
    recover() resolve 전 추가 메시지를 보내지 않음 → async 윈도우 중 도착하는 *두 번째* 메시지 손실 시나리오는 미검증.
    R-5 규율상 "의도된 reactivate" 가 아니라 가드 완화가 *추가* 메시지의 인과를 무시하는 경우라 결함 성립.

    2) terminal 보호: state 에 terminal 없음 → 무관.

    3) 직렬화: recover() 동안 generation 은 잠기지 않음. logMessageQueued 가 동기로 끼어들 수 있음(diagnostic.ts:645).

    4) 도착순서: idle 세션에 메시지가 큐잉되는 것은 정상 흐름. recover() 의 import+await 지연 동안 추가 메시지 1개
    도착은 현실적(R-7). 비현실적 지연 강제 아님.

    '
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-diagnostic-recovery-ordering-causality-003
domain_notes_ref: domain-notes/diagnostic-recovery.md
---
# idle 분기 generation+1 완화가 async 윈도우 중 새 메시지/작업의 인과를 무시하고 stale 복구결과로 idle 덮어씀

## 문제

idle 세션의 abort_embedded_run 복구는 staleness 가드를 정확 generation 일치
(`isDiagnosticSessionStateCurrent`)에서 `generation === requestGeneration || requestGeneration+1`
로 완화한다. recover() async 윈도우 중 logMessageQueued 가 generation 을 한 번 bump(+1)하며 새
메시지를 큐잉해도, 이 완화가 G+1 을 여전히 current 로 받아 G 시점에 계산된 stale 복구결과를 적용,
state 를 idle 로 덮고 queueDepth 를 0 으로 clear 하여 새 메시지를 회계에서 소실시킨다.

## 발현 메커니즘

1. heartbeat: idle + 큐 1개 세션 S(generation=G) → idle abort 복구 dispatch, stateGeneration=G.
2. recover() async (dynamic import + abort/drain await). runtime 진입 가드(runtime.ts:101) 통과.
3. 윈도우 중 follow-up 메시지 도착 → logMessageQueued: queueDepth+1, generation=G+1, state=idle 유지.
4. recover() 완료. outcome(aborted, released>0)은 G 시점 세계 기준.
5. apply 의 idle 분기: state===idle && (gen===G+1) → stateIsCurrent=true (완화로 통과).
6. state.state=idle 강제, generation=G+2, recoveryOutcomeClearsQueuedSessionState → queueDepth=0.
7. 3에서 도착한 메시지 큐가 0 으로 덮임 → 처리 누락.

## 근본 원인 분석

1. idle 분기가 정확 일치 대신 `generation === requestGeneration+1` 을 명시 허용(:103).
2. logMessageQueued 는 generation+1 과 동시에 queueDepth+1 을 한다(diagnostic.ts:655-657). G+1 은 새
   작업이 추가된 세계이고 G 시점 outcome 은 이를 모른다.
3. recoveryOutcomeClearsQueuedSessionState 가 released>0 aborted 를 queueDepth=0 으로 clear(:127,
   diagnostic-session-recovery.ts:98-100) → G+1 에서 추가된 큐까지 0 으로 만든다.
4. queueDepth=0 idle 세션은 다음 heartbeat stuck 판정 미통과(diagnostic.ts:1247-1255) → 자동 복구 없음.

## 영향

- **impact_hypothesis: data-loss** — async 윈도우 중 도착한 follow-up 메시지의 queueDepth 소실.
- 지속성: 한 번 소실되면 새 메시지가 다시 와서 generation/queueDepth 를 올리기 전까지 복구 대상에서 영구 제외.
- generation+1 완화는 "정확히 한 번의 bump" 를 허용 → 메시지 1개 도착(가장 흔한 케이스)에서 발동.
  2개 이상 도착하면 G+2 가 되어 오히려 stale 처리되는 역설.
- severity P1: 현실적 도착 순서 + 데이터(큐) 소실 + 자동 복구 불가.

## 반증 탐색

- **generation 가드**: non-idle 분기는 정확 일치라 1회 bump 면 stale → 안전. idle 분기만 완화되어 노출.
- **terminal 보호**: state 에 terminal 없음 → 무관.
- **테스트 의도**: diagnostic.test.ts:934 가 logMessageQueued 1회 후 idle 복구→queueDepth 0 을 정상으로
  못박음(:995). 그러나 그 테스트는 recover() resolve 전 *추가* 메시지를 안 보냄 → 윈도우 중 도착한
  두 번째 메시지 손실은 미검증. R-5 규율상 "의도된 reactivate" 가 아니라 가드 완화가 추가 메시지의
  인과를 무시하는 경우라 결함.
- **실제 도착순서(R-7)**: idle 세션 큐잉은 정상 흐름. recover() 의 import+await 지연 동안 메시지 1개
  도착은 production 에서 흔함. 비현실적 지연 강제 아님.

## Self-check

### 내가 확실한 근거
- idle 분기가 `generation === requestGeneration + 1` 을 명시 허용(coordinator.ts:103).
- logMessageQueued 는 generation+1 과 queueDepth+1 을 동시 수행(diagnostic.ts:655-657), state 는 idle 유지.
- recoveryOutcomeClearsQueuedSessionState(released>0 aborted)=true → queueDepth=0 (diagnostic-session-recovery.ts:98-100).
- recover() 는 dynamic import + abortAndDrainEmbeddedAgentRun(settleMs 최대 15s) await 로 윈도우가 길다(runtime.ts:176).
- 다음 heartbeat 의 stuck 판정은 queueDepth>0 또는 processing 을 요구(diagnostic.ts:1252-1255).

### 내가 한 가정
- recover() 의 outcome 이 released>0 인 aborted 라고 가정(clearsQueued 발동 조건). released=0 이거나
  queuedCount>0 이면 preserveQueuedIdleWork 분기(coordinator.ts:125-130)로 queueDepth 가 보존될 수 있어
  손실이 완화된다. 즉 손실은 특정 outcome 형태에서 발생.
- async 윈도우 중 정확히 메시지 1개가 도착한다고 가정. 0개면 정상, 2개 이상이면 G+2 로 stale 처리.

### 확인 안 한 것 중 영향 가능성
- generation+1 완화가 추가된 정확한 의도(어떤 동기 mutator 가 recover 직전 generation 을 1 올리는 정상
  경로를 수용하려는 것인지)를 git blame 으로 확인하지 못함. 만약 "recover dispatch 와 같은 tick 의
  logSessionStateChange(idle)" 를 수용하려는 의도였다면 그 케이스는 정당하나, follow-up 메시지 큐잉까지
  무차별 수용하는 부수효과가 문제다.
- queueDepth 소실이 실제 메시지 디스패치 누락으로 이어지는 하류 경로(command-queue/embedded-runner)는
  이 셀 범위 밖이라 end-to-end 미확인. diagnostic state 의 queueDepth 가 실제 디스패치 트리거인지에
  따라 severity 가 P1↔P2 로 흔들릴 수 있음.
