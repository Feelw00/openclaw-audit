---
id: FIND-acp-control-plane-concurrency-002
cell: acp-control-plane-concurrency
title: 'eventGate single-check TOCTOU: timeout 으로 gate 닫혀도 진행 중 이벤트 1건 caller 누출'
file: src/acp/control-plane/manager.turn-stream.ts
line_range: 32-48
evidence: "```ts\n  for await (const event of params.events) {\n    if (!params.eventGate.open)\
  \ {\n      continue;\n    }\n    if (event.type === \"done\") {\n      sawTerminalEvent\
  \ = true;\n    } else if (event.type === \"error\") {\n      streamError = new AcpRuntimeError(\n\
  \        normalizeAcpErrorCode(event.code),\n        normalizeText(event.message)\
  \ || \"ACP turn failed before completion.\",\n      );\n    } else if (event.type\
  \ === \"text_delta\" || event.type === \"tool_call\") {\n      sawOutput = true;\n\
  \      await params.onOutputEvent?.(event);\n    }\n    await params.onEvent?.(event);\n\
  \  }\n```\n"
symptom_type: concurrency-race
problem: 'consumeAcpTurnEvents 의 이벤트 루프는 매 iteration 시작에서 eventGate.open 을 한 번만

  검사한다 (turn-stream.ts:33). 검사를 통과한 뒤 await params.onOutputEvent (:45) 와

  await params.onEvent (:47) 가 실행되는데, 이 await 지점에서 제어가 양보되는 동안 turn

  timeout 핸들러 (manager.core.ts:953) 가 eventGate.open=false 로 닫을 수 있다. gate 가

  닫힌 시점에 이미 :33 검사를 통과한 현재 이벤트는 onEvent/onOutputEvent 로 그대로 전달된다.

  즉 turn 이 timeout 으로 ACP_TURN_FAILED 를 반환한 뒤에도, detach 된 turnPromise

  (manager.core.ts:1138) 가 계속 돌며 gate-close 직전에 검사를 통과한 in-flight 이벤트

  1건을 caller 의 onEvent 로 emit 한다.

  '
mechanism: "1. turn timeout 발생 (manager.core.ts:947 awaitTurnWithTimeout 의 Promise.race\
  \ 가\n   timeoutToken 으로 resolve, :1137). turnPromise (consumeAcpTurnStream) 는 detach\n\
  \   (:1138 void observedTurnPromise.then) 되어 계속 실행.\n2. detach 된 turn 의 consumeAcpTurnEvents\
  \ 루프가 이벤트 X 를 받아 :33 `eventGate.open`\n   == true 확인 통과.\n3. :45 `await params.onOutputEvent?.(event)`\
  \ 또는 :43~:46 분기 후 :47\n   `await params.onEvent?.(event)` 에서 await → 마이크로태스크 양보.\n\
  4. 같은 awaitTurnWithTimeout 흐름의 onTimeout 콜백 (manager.core.ts:952-963) 이 실행되어\n \
  \  `eventGate.open = false` (:953) set.\n5. 3번의 await 가 resolve 되면 onEvent(X) 가\
  \ 이미 호출되었거나 (await 진입 전 동기 호출 시작)\n   호출 진행 → caller 는 turn 이 ACP_TURN_FAILED 로\
  \ 끝난 뒤 이벤트 X 를 수신.\n추가로 streamError throw 가드 (:50 `if (params.eventGate.open &&\
  \ streamError) throw`) 도\ngate 가 닫혀 있으면 error 이벤트를 삼켜, timeout 이후 도착한 error event\
  \ 가 조용히 무시된다.\n"
root_cause_chain:
- why: 왜 timeout 후에도 caller 가 이벤트를 받는가?
  because: eventGate.open 검사가 루프 iteration 시작 1회뿐이고, 그 후 onEvent/onOutputEvent await
    동안 gate 가 닫혀도 현재 이벤트 전달은 이미 진행 중이라 막히지 않는다.
  evidence_ref: src/acp/control-plane/manager.turn-stream.ts:33, src/acp/control-plane/manager.turn-stream.ts:45-47
- why: 왜 gate 가 await 도중에 닫히는가?
  because: turn timeout 시 turnPromise 가 detach 되어 계속 도는데, 같은 흐름의 onTimeout 이 eventGate.open=false
    를 set 하므로 detach 된 루프와 gate-close 가 인터리빙된다.
  evidence_ref: src/acp/control-plane/manager.core.ts:1138, src/acp/control-plane/manager.core.ts:953
- why: 왜 가드가 in-flight 이벤트를 회수하지 못하는가?
  because: eventGate 가 단순 boolean 플래그라 닫힘과 진행 중 전달 취소를 atomic 하게 못 한다. await 경계를 넘어선
    이벤트 1건은 항상 누출 가능.
  evidence_ref: src/acp/control-plane/manager.turn-stream.ts:11-13, src/acp/control-plane/manager.turn-stream.ts:33
- why: 왜 의도된 detach 설계임에도 잔여 누출이 남는가?
  because: commit 83e19ca469 은 timeout 시 turn 을 죽이지 않고 살려두는 방향(detach + gate)이며, gate
    는 emit 만 막는 사후 필터라 정확한 cut-off 를 보장하지 않는다.
  evidence_ref: git 83e19ca469, src/acp/control-plane/manager.core.ts:1138
impact_hypothesis: wrong-output
impact_detail: '정성: turn 이 timeout 으로 종료 (caller 가 ACP_TURN_FAILED 수신) 된 후, gate-close
  직전

  검사를 통과한 출력/툴콜 이벤트 최대 1건이 caller onEvent 로 추가 전달된다. eventGate 는

  이 누출을 줄이기 위한 방어이지만 per-iteration single-check 라서 await 경계 1개 분량의

  window 가 남는다. 영향: caller (translator, allowed_paths 밖) 가 종료된 turn 에 대해

  지연 이벤트를 받아 UI/ledger 에 stale delta 를 반영하거나, error 이벤트가 gate 로 삼켜져

  (:50) 진단 정보가 유실. 빈도: turn timeout 이 실제 발생하고 (긴 ACP turn) 그 순간 backend

  가 이벤트를 emit 중일 때만 → 드문 타이밍. 누출량은 최대 1 이벤트 (다음 iteration 의 :33

  재검사에서 차단).

  '
severity: P3
counter_evidence:
  path: src/acp/control-plane/manager.turn-stream.ts
  line: 33, 50, 78
  reason: '탐색한 방어 (execution-condition 분류):

    | 방어 | 위치 | 조건 | TOCTOU 차단? |

    | eventGate.open 루프 가드 | turn-stream.ts:33 | unconditional (매 iteration) | 다음
    iteration 부터 차단. 현재 in-flight 이벤트는 미차단 |

    | streamError throw 가드 | :50 `if (eventGate.open && streamError)` | unconditional
    | 오히려 닫힌 gate 에서 error 삼킴 (다른 증상) |

    | notifyTerminalResult 가드 | :78 `if (!eventGate.open) return` | 함수 진입 시 1회 | 진입
    후 await 중 닫혀도 미반영 (동일 패턴) |

    rg "Mutex|Semaphore|AsyncLock" → production match 없음. eventGate 는 boolean 플래그

    (manager.turn-stream.ts:11-13) 로 lock 아님. detach 는 manager.core.ts:1138 에서 의도된

    설계 (commit 83e19ca469 ''keep ACP turns on OpenClaw timeouts'' 가 turn 을 죽이지 않고

    살려두는 방향). 따라서 detach + late-emit 자체는 의도, 다만 gate 의 single-check 가 await

    경계 1개 분량 누출을 남기는 것은 잔여 race. 기존 테스트에 gate-close 와 onEvent await 의

    인터리빙을 검증하는 케이스 없음 (manager.test.ts 의 cancel/timeout 테스트는 done yield 후

    종료만 확인). primary-path: gate 가 atomic 하게 "닫힘 + drain" 을 보장하지 않는 한 await

    경계 누출은 불가피 — boolean 플래그라 in-flight 이벤트를 회수할 수단 없음.

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: 2026-05-29
domain_notes_ref: domain-notes/acp-control-plane.md
related_tests:
- src/acp/control-plane/manager.test.ts
cross_refs:
- FIND-acp-control-plane-concurrency-001
---
# eventGate TOCTOU: gate open 체크 후 await onEvent 사이 timeout 이 gate 닫아도 진행 중 이벤트 1건은 caller 로 누출

## 문제

`consumeAcpTurnEvents` (manager.turn-stream.ts:20) 는 turn 이 timeout 으로 종료된 뒤
detach 된 채 계속 도는 이벤트 스트림에서, caller 로의 이벤트 전달을 `eventGate.open`
boolean 으로 차단한다. 그러나 가드는 루프 iteration 시작에서 단 한 번 (:33) 검사되고, 그
직후 `await params.onOutputEvent` (:45) / `await params.onEvent` (:47) 에서 제어를
양보한다. 이 await 경계 동안 turn timeout 핸들러 (manager.core.ts:953) 가
`eventGate.open=false` 로 닫더라도, 이미 :33 검사를 통과한 현재 이벤트는 caller 로 전달된다.

## 발현 메커니즘

```
detach 된 turn (consumeAcpTurnEvents)        awaitTurnWithTimeout / onTimeout
-------------------------------------        --------------------------------
:32 for await event X
:33 eventGate.open == true → 통과
:43 event.type text_delta
:45 await onOutputEvent(X)  ----양보---->
                                             core:1137 timeoutToken resolve
                                             core:953 eventGate.open = false
:47 await onEvent(X)  (X 가 caller 로 전달)
   ==> turn 은 이미 ACP_TURN_FAILED 반환됨
       caller 는 종료된 turn 의 이벤트 X 수신
```

다음 iteration (:32 재진입) 의 :33 검사부터는 gate 가 닫혀 있으므로 차단된다. 따라서 누출은
await 경계 1개 분량 (최대 1 이벤트). 별개로 :50 `if (params.eventGate.open && streamError)
throw` 는 gate 가 닫힌 상태에서는 누적된 error 이벤트를 throw 하지 않고 삼킨다.

## 근본 원인 분석

- **why1**: timeout 후에도 caller 가 이벤트를 받는 이유는?
  because: eventGate.open 검사가 루프 iteration 시작 1회뿐이고 (:33), 그 후 onEvent/
  onOutputEvent await (:45,:47) 동안 gate 가 닫혀도 현재 이벤트 전달은 진행됨.
  evidence_ref: manager.turn-stream.ts:33, manager.turn-stream.ts:45-47
- **why2**: gate 가 await 중에 닫히는 이유는?
  because: turn timeout 시 turnPromise 가 detach (manager.core.ts:1138) 되어 계속 도는데,
  같은 흐름의 onTimeout (manager.core.ts:953) 이 eventGate.open=false 를 set 하므로 detach
  된 루프와 close 가 인터리빙. evidence_ref: manager.core.ts:1138, manager.core.ts:953
- **why3**: 가드가 in-flight 이벤트를 회수하지 못하는 근본 이유는?
  because: eventGate 가 단순 boolean 플래그 (turn-stream.ts:11-13) 라 "닫힘 + 진행 중 전달
  취소" 를 atomic 하게 못 함. await 경계를 넘어선 이벤트 1건은 항상 누출 가능.
  evidence_ref: manager.turn-stream.ts:11-13 (AcpTurnEventGate = { open: boolean }),
  N/A — boolean 플래그의 한계는 타입 정의로 자명
- **why4**: 이 detach 가 의도된 설계임에도 잔여 누출이 남는 이유는?
  because: commit 83e19ca469 (keep ACP turns on OpenClaw timeouts) 는 timeout 시 turn 을
  죽이지 않고 살려두는 방향 (detach + gate). gate 는 emit 만 막는 사후 필터라 정확한 cut-off
  를 보장하지 않는다. evidence_ref: git 83e19ca469, manager.core.ts:1138

## 영향

impact_hypothesis: wrong-output.

재현 시나리오: 긴 ACP turn → turn timeout (manager.core.ts:947 의 race 가 timeoutToken
으로 종료) → caller 가 ACP_TURN_FAILED 수신. 그 직후 detach 된 turn 루프가 backend 의
다음 text_delta/tool_call/error 이벤트를 받아 :33 검사 통과 → onEvent 로 전달. caller
(translator, allowed_paths 밖) 가 종료된 turn 에 stale delta 를 ledger/UI 에 반영하거나,
error 이벤트가 :50 가드로 삼켜져 진단 정보 유실.

빈도/규모: timeout 이 실제 발생 + 그 순간 backend 가 이벤트 emit 중일 때만 (드문 타이밍).
누출은 await 경계 1개 분량 (최대 1 이벤트). 그래서 P3 (위생 수준, 사용자 체감 낮음).

## 반증 탐색

탐색한 카테고리:

1. **primary-path inversion (atomic guard)**: 이 누출이 재현되려면 어떤 atomic cut-off 가
   우회돼야 하는가? → eventGate 가 그 cut-off 인데, boolean 플래그라 per-iteration single-check.
   확인 결과 닫힘과 진행 중 전달 취소가 atomic 하지 않음 → await 경계 누출 불가피.
2. **외부 lock**: `rg "Mutex|Semaphore|AsyncLock" src/acp/control-plane/` production match
   없음. eventGate 는 lock 이 아니라 단순 플래그 (turn-stream.ts:11-13).
3. **detach 의도성**: manager.core.ts:1138 의 detach 와 commit 83e19ca469 은 timeout 시
   turn 을 살려두는 의도된 설계. 따라서 detach 자체는 결함 아님. 잔여 race 는 gate single-check
   의 누출 window 한정 → severity P3 으로 좁힘.
4. **추가 가드 (:50, :78)**: streamError throw 가드 (:50) 와 notifyTerminalResult 가드 (:78)
   모두 같은 single-check 패턴이라 await 중 닫힘에 취약 (counter_evidence 표). :50 은 닫힌 gate
   에서 error 를 삼키는 별개 부작용까지 있음.
5. **기존 테스트**: manager.test.ts 의 cancel/timeout 테스트들은 done yield 후 종료만 확인하고,
   gate-close 와 onEvent await 의 인터리빙 (gate 닫힘 직전 통과 이벤트) 을 검증하는 케이스 없음.

반증 결론: eventGate 의 누출 window 는 boolean single-check 의 구조적 한계 (unconditional).
다른 어떤 lock 도 이를 막지 않음. 다만 detach 가 의도된 설계이고 누출 규모가 1 이벤트라
실질 영향이 작아 P3.

## Self-check

### 내가 확실한 근거
- eventGate.open 검사는 루프 iteration 시작 1회 (:33), 이후 await onEvent/onOutputEvent (:45,:47).
- timeout 시 turnPromise detach (manager.core.ts:1138) + onTimeout 이 eventGate.open=false (:953).
- eventGate 는 boolean 플래그 (turn-stream.ts:11-13), lock/회수 메커니즘 없음.
- :50 의 throw 가드는 gate 닫힘 시 error 이벤트를 삼킴.

### 내가 한 가정
- detach 된 turn 루프와 onTimeout 의 gate-close 가 실제로 인터리빙 가능하다 (둘 다
  awaitTurnWithTimeout 흐름에서 비롯; onTimeout 은 race resolve 후 await, detach 루프는 그와
  독립 진행 → 마이크로태스크 경계에서 교차 가능 가정).
- backend 가 timeout 시점 부근에 이벤트를 emit 중일 수 있다 (스트리밍 ACP 의 정상 동작 가정).

### 확인 안 한 것 중 영향 가능성
- caller (translator, allowed_paths 밖) 가 turn 실패 후 들어온 이벤트를 자체적으로 무시할 수도
  있음 — 그렇다면 사용자 체감 0 (이미 P3 으로 반영).
- onEvent 가 동기 함수면 await 가 즉시 resolve 되어 양보 window 가 더 좁아질 수 있으나,
  타입상 Promise 반환 가능 (manager.types.ts:66) 이라 비동기 caller 에서는 window 존재.
