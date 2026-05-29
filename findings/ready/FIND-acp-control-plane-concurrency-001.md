---
id: FIND-acp-control-plane-concurrency-001
cell: acp-control-plane-concurrency
title: cancelSession queue 우회로 turn finally 의 oneshot close 와 동일 handle cancel/close
  경합
file: src/acp/control-plane/manager.core.ts
line_range: 1274-1289
evidence: "```ts\n    const activeTurn = this.activeTurnBySession.get(actorKey);\n\
  \    if (activeTurn) {\n      activeTurn.abortController.abort();\n      if (!activeTurn.cancelPromise)\
  \ {\n        activeTurn.cancelPromise = activeTurn.runtime.cancel({\n          handle:\
  \ activeTurn.handle,\n          reason: params.reason,\n        });\n      }\n \
  \     await withAcpRuntimeErrorBoundary({\n        run: async () => await activeTurn.cancelPromise!,\n\
  \        fallbackCode: \"ACP_TURN_FAILED\",\n        fallbackMessage: \"ACP cancel\
  \ failed before completion.\",\n      });\n      return;\n    }\n```\n"
symptom_type: concurrency-race
problem: 'cancelSession 의 active-turn 분기 (manager.core.ts:1274-1289) 는 withSessionActor

  (SessionActorQueue 직렬화) 를 거치지 않고 즉시 실행된다. 따라서 같은 sessionKey 의

  진행 중 turn (runTurn/prompt) 이 actor queue 안에서 돌고 있는 동안, cancelSession 은

  queue 밖에서 동일한 AcpRuntimeHandle 에 runtime.cancel 을 호출한다. cancel 이 turn

  의 combinedSignal 을 abort 시켜 turn 이 cancelled-done 으로 종료되면, turn 의 finally

  (:1046-1066) 가 oneshot 세션에 대해 동일 handle 로 runtime.close 를 호출한다. 결과적으로

  같은 handle 에 대해 cancel (queue 밖) 과 close (queue 안) 가 직렬화 없이 동시에 in-flight

  상태가 된다.

  '
mechanism: "1. T1: runTurn(sessionKey, mode=oneshot) → withSessionActor 안에서 turn 실행,\n\
  \   activeTurnBySession.set(actorKey, E) (:901). consumeAcpTurnStream 진행 중.\n2.\
  \ T2: cancelSession(sessionKey) 진입. evictIdleRuntimeHandles 후 :1274 에서\n   E = activeTurnBySession.get(actorKey)\
  \ 읽음. withSessionActor 미경유.\n3. T2: E.abortController.abort() (:1276) → turn 의 combinedSignal\
  \ (:904-907) abort.\n   E.cancelPromise = runtime.cancel({handle, reason}) (:1278)\
  \ 발행, await 시작 (:1284).\n4. T1: abort 로 turn stream 이 cancelled done 이벤트로 종료 → sawTerminalEvent=true\n\
  \   → turn 정상 return (:993) → finally (:1029) 진입. skipPostTurnCleanup=false\n  \
  \ (timeout 아님), retryFreshHandle=false 이므로 reconcileRuntimeSessionIdentifiers\n\
  \   (:1037) 후 meta.mode===\"oneshot\" 분기에서 runtime.close({handle, reason:\"oneshot-complete\"\
  })\n   (:1054-1058) 호출.\n5. 동일 handle 에 대해 T2 의 runtime.cancel 와 T1 의 runtime.close\
  \ 가 직렬화 없이\n   겹친다. cancel↔close ordering 이 백엔드 런타임 구현에 위임되며, manager 레벨에서는\n  \
  \ 아무 lock/queue 도 두 호출을 순서 짓지 않는다 (cancelSession 은 의도적으로 queue 우회).\n"
root_cause_chain:
- why: 왜 cancel 과 close 가 동일 handle 에 직렬화 없이 동시 호출되는가?
  because: cancelSession 의 active-turn 분기가 SessionActorQueue (turn/close 가 쓰는 유일한
    직렬화 기제) 를 우회한다. turn 과 그 finally 의 close 는 withSessionActor 안, cancel 은 밖.
  evidence_ref: src/acp/control-plane/manager.core.ts:1274-1289, src/acp/control-plane/manager.core.ts:739
- why: 왜 cancel 이 close 분기까지 활성화시키는가?
  because: cancel 의 abort 가 turn 을 cancelled-done 으로 종료시키고, cancel-driven 종료는 skipPostTurnCleanup
    을 set 하지 않아 (그 플래그는 timeout 경로에서만) finally 의 oneshot close 분기가 활성화된다.
  evidence_ref: src/acp/control-plane/manager.core.ts:954, src/acp/control-plane/manager.core.ts:1046-1058
- why: 왜 manager 가 cancel↔close 순서를 보장하지 못하는가?
  because: 동시성 직렬화가 오직 SessionActorQueue 한 군데뿐이고 외부 Mutex/Semaphore 가 없으며, cancelSession
    은 응답성 때문에 그 큐를 의도적으로 건너뛴다. turn 종료 시 in-flight cancel 을 기다리는 별도 가드가 없다.
  evidence_ref: src/acp/control-plane/manager.core.ts:1274, N/A - 외부 lock 부재는 rg production
    match 없음으로 확인
- why: 왜 cancelPromise dedupe 가 이를 막지 못하는가?
  because: if (!activeTurn.cancelPromise) 가드는 cancel 호출 중복만 막고 (cancel+cancel → 1회),
    close 는 전혀 다른 호출이라 dedupe 대상이 아니다.
  evidence_ref: src/acp/control-plane/manager.core.ts:1277-1282, src/acp/control-plane/manager.types.ts:128-133
impact_hypothesis: wrong-output
impact_detail: '정성: cancel↔close 가 동일 handle 에 동시 진행. runtime/types.ts:182-192 의 cancel/close

  계약은 reentrancy/ordering 을 명시하지 않으므로 backend 구현에 따라 (a) close 가 cancel

  완료 전 handle 을 해제해 cancel 이 ACP_TURN_FAILED 로 실패 (cancelSession 이 불필요한

  에러 반환), 또는 (b) 두 호출이 같은 backend 프로세스에 중복 종료/취소 요청을 보내 백엔드

  상태가 불일치. 빈도: cancelSession 은 abort.ts:369 / task-registry.ts:1914 /

  session-reset-service.ts:443 / translator cancel-scoping 등 다수 entry 에서 호출되고,

  getAcpSessionManager() 는 데몬 싱글톤이라 동일 sessionKey 에 turn 과 cancel 이 겹치는

  것이 정상 사용 시나리오 (사용자가 oneshot turn 중 취소). oneshot 모드에서만 close 분기

  활성 → 영향 범위는 oneshot 세션.

  '
severity: P2
counter_evidence:
  path: src/acp/control-plane/manager.core.ts
  line: 1167-1173, 1277-1282
  reason: '탐색한 방어 (execution-condition 분류):

    | 방어 | 위치 | 조건 | race 차단? |

    | cancelPromise 공유 dedupe | :1168, :1277 `if (!activeTurn.cancelPromise)` | unconditional
    (동기 check-then-set) | cancel 중복만 차단. cancel↔close 는 별개 필드라 미차단 |

    | SessionActorQueue 직렬화 | withSessionActor :2114 | turn/close 는 queue 안, cancel
    은 queue 밖 | cancelSession early path 가 queue 우회하므로 cancel vs close 미직렬화 |

    | skipPostTurnCleanup | :954 | timeout 시에만 true | cancel-driven 종료에는 false → oneshot
    close 분기 활성 |

    | AbortController | :1276 | unconditional | turn 을 멈추게 할 뿐, close 호출 자체를 막지 않음
    |

    rg "Mutex|Semaphore|AsyncLock|acquire|release" src/acp/control-plane/ → 프로덕션 코드

    match 없음 (test deferred 만). 외부 lock 부재 확정. cancelPromise dedupe 는 cancel 호출

    2회를 막지만 close 와는 무관하므로 cancel↔close 겹침은 unconditional guard 로 차단되지 않음.

    기존 테스트 manager.test.ts:2508 은 cancel-during-turn happy path 만 검증 (runTurn 이

    cancel 시 즉시 done yield, oneshot close 와 cancel 의 동시성 미검증).

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: 2026-05-29
domain_notes_ref: domain-notes/acp-control-plane.md
related_tests:
- src/acp/control-plane/manager.test.ts
---
# cancelSession 의 actor-queue 우회 경로가 진행 중 turn 의 post-turn close 와 동일 handle 에 cancel/close 동시 호출

## 문제

`cancelSession` (manager.core.ts:1263) 은 진행 중인 turn 을 즉시 중단해야 하므로,
`activeTurnBySession` 에 항목이 있으면 `withSessionActor` (SessionActorQueue 직렬화)
를 거치지 않고 곧장 `runtime.cancel` 을 호출한다 (:1274-1289). 이는 의도된 설계다 —
cancel 이 turn 뒤에서 queue 를 기다리면 cancel 의 의미가 사라지기 때문이다.

그러나 이 우회 때문에, cancel 이 turn 을 abort 시켜 turn 이 종료되면, turn 의 finally
블록이 oneshot 세션에 대해 동일한 `AcpRuntimeHandle` 로 `runtime.close` 를 호출하는데
(:1046-1058), 이 close 는 actor queue 안에서 돌고 cancel 은 queue 밖에서 돌기 때문에
manager 레벨에서 두 호출을 직렬화하는 어떤 lock/queue 도 없다. 같은 handle 에 cancel 과
close 가 동시에 in-flight 상태가 된다.

## 발현 메커니즘

```
T1 (runTurn, oneshot, queue 안)            T2 (cancelSession, queue 밖)
--------------------------------          ---------------------------------
:901  activeTurnBySession.set(key, E)
:913  consumeAcpTurnStream 진행
                                          :1274 E = activeTurnBySession.get(key)
                                          :1276 E.abortController.abort()
                                          :1278 E.cancelPromise = runtime.cancel(handle)
                                          :1284 await cancelPromise ...
:904  combinedSignal abort 감지
      turn stream cancelled-done 종료
:965  sawTerminalEvent=true → return
:1029 finally: skipPostTurnCleanup=false
:1054 runtime.close({handle, "oneshot-complete"})   <-- close in-flight
                                          (cancel 아직 in-flight)
      ==> 동일 handle 에 cancel + close 동시
```

핵심: 4번 (turn 종료) 과 cancelSession 의 await 가 서로 다른 비동기 컨텍스트에서 진행되며,
turn 의 finally close 와 cancelSession 의 cancel 사이에 manager 가 부과하는 순서가 없다.

## 근본 원인 분석

- **why1**: cancel 과 close 가 동일 handle 에 직렬화 없이 동시 호출되는가?
  because: cancelSession 의 active-turn 분기가 SessionActorQueue (turn/close 가 사용하는
  유일한 직렬화 기제) 를 우회한다. evidence_ref: manager.core.ts:1274-1289 (withSessionActor
  미경유), manager.core.ts:739 (turn 은 withSessionActor 안에서 실행)
- **why2**: 우회가 cancel↔close 충돌로 이어지는 이유는?
  because: cancel 의 abort 가 turn 을 종료시키고, cancel-driven 종료는 skipPostTurnCleanup
  을 세팅하지 않아 (그 플래그는 timeout 경로 :954 에서만 set) finally 의 oneshot close 분기
  (:1046-1058) 가 활성화된다. evidence_ref: manager.core.ts:954, manager.core.ts:1046-1058
- **why3**: manager 가 cancel↔close 순서를 보장하지 못하는 근본 이유는?
  because: 동시성 직렬화가 오직 SessionActorQueue 한 군데에만 있고 (외부 Mutex/Semaphore
  없음), cancelSession 은 응답성 때문에 그 큐를 의도적으로 건너뛴다. cancel 과 turn-finally
  -close 가 같은 handle 을 만질 때를 위한 별도 가드(예: turn 종료 시 in-flight cancel 을
  기다림)가 없다. evidence_ref: manager.core.ts:1274 (queue bypass), N/A — 외부 lock 부재는
  rg 결과로 확인 (production match 없음)
- **why4**: cancelPromise dedupe 가 이를 막지 못하는 이유는?
  because: `if (!activeTurn.cancelPromise)` (:1277, :1168) 는 cancel 호출의 중복만 막고
  (cancel + cancel → 1회), close 는 전혀 다른 필드/호출이라 dedupe 대상이 아니다.
  evidence_ref: manager.core.ts:1277-1282, manager.types.ts:128-133 (cancelPromise 만 공유 필드)

## 영향

impact_hypothesis: wrong-output.

재현 시나리오: 사용자가 oneshot ACP 세션에서 긴 turn 을 시작 → 진행 중 abort/취소
명령 (abort.ts:369 또는 사용자 /cancel) → cancelSession 이 queue 밖에서 cancel 발행 →
abort 로 turn 이 종료되며 oneshot finally 가 같은 handle 에 close 발행. 두 호출이 backend
런타임 (acpx 등) 의 동일 프로세스/세션에 동시 도달.

`runtime/types.ts:182-192` 의 cancel/close 시그니처는 reentrancy 나 ordering 을 명시하지
않으므로 backend 구현에 의존: close 가 먼저 handle 을 해제하면 cancel 이 ACP_TURN_FAILED
로 실패해 cancelSession 이 불필요한 에러를 throw (호출자가 cancel 실패로 인지) 하거나,
중복 종료/취소 요청으로 backend 상태 불일치 (예: 다음 ensureSession 이 stale handle 재사용
probe 통과 못함). oneshot 모드에서만 close 분기가 활성이므로 영향은 oneshot 세션 한정.

빈도: cancelSession 은 다수 entry (abort.ts:369, task-registry.ts:1914,
session-reset-service.ts:443, translator cancel-scoping) 에서 호출되고 데몬 싱글톤이라
정상 사용 흐름에서 turn 과 cancel 이 겹치는 것이 비정상 경로가 아님.

## 반증 탐색

탐색한 카테고리:

1. **primary-path inversion (atomic guard 탐색)**: 이 race 가 재현되려면 어떤 직렬화가
   우회돼야 하는가? → SessionActorQueue. 확인 결과 cancelSession active-turn 분기 (:1274-1289)
   가 명시적으로 withSessionActor 를 호출하지 않음 (else 분기 :1291 만 queue 사용). 즉 우회는
   설계상 항상 발생.
2. **외부 lock**: `rg "Mutex|Semaphore|AsyncLock|acquire|release" src/acp/control-plane/`
   → 프로덕션 코드 match 없음 (manager.test.ts 의 releaseFirstTurn deferred 뿐). 외부 동기화
   부재 확정.
3. **cancelPromise dedupe**: :1168/:1277 의 `if (!activeTurn.cancelPromise)` 는 동기
   check-then-set 이라 JS 단일스레드에서 cancel 중복은 막힌다. 그러나 close 는 별개 호출이라
   미차단 (counter_evidence 표 참조).
4. **skipPostTurnCleanup**: timeout 경로 (:954) 에서만 set → cancel-driven 종료에는
   false → oneshot close 분기 활성. close 를 막는 조건 아님.
5. **기존 테스트**: manager.test.ts:2508 은 cancel-during-turn 을 검증하나, runTurn mock 이
   abort 즉시 done 을 yield 하고 cancel 한 번만 검증 (`cancel toHaveBeenCalledTimes(1)`).
   oneshot 모드 + cancel↔close 동시성은 미검증 (이 테스트는 mode 명시 안 함; readySessionMeta
   기본 mode 확인 필요하나 close 호출 검증 자체가 없음).

반증 결론: cancel↔close 의 manager-레벨 직렬화 부재는 unconditional 한 설계 (queue bypass).
유일한 가드인 cancelPromise dedupe 는 cancel-vs-cancel 만 막아 이 주장을 반증하지 못함.

## Self-check

### 내가 확실한 근거
- cancelSession active-turn 분기가 withSessionActor 를 거치지 않음 (:1274-1289, else 분기만 :1291).
- turn 은 withSessionActor 안에서 실행 (:739) 되고 finally 의 oneshot close 도 그 안 (:1054).
- skipPostTurnCleanup 는 timeout 에서만 set (:954); cancel-driven 정상 종료에는 close 분기 활성.
- 외부 lock 부재 (rg production match 없음). 직렬화는 SessionActorQueue 단일.

### 내가 한 가정
- cancel 의 abort 가 진행 중 oneshot turn 을 cancelled-done 으로 종료시킨다 (manager.test.ts:2481-2491
  의 mock 이 abort 시 done yield 하는 패턴을 근거로 production runtime 도 유사 동작 가정).
- backend runtime (acpx) 이 동일 handle 동시 cancel+close 를 안전 직렬화하지 않는다 (types.ts:182-192
  에 ordering 보장 명시 없음 — extensions/acpx 는 allowed_paths 밖이라 미확인).

### 확인 안 한 것 중 영향 가능성
- acpx runtime 구현이 내부적으로 handle 별 직렬화를 둘 수도 있음 (allowed_paths 밖, 미검증).
  만약 그렇다면 backend 레벨에서 방어되어 severity 하향 (P3). 그래서 P2 로 둠 (P0/P1 아님).
- cancel 이 abort 만으로 turn 을 종료시키지 못하고 backend cancel RPC 완료까지 turn 이 안 끝나는
  경우, finally close 시점이 cancel 완료 이후로 밀려 동시성 window 가 닫힐 수 있음 (이 경우 race 미성립).
