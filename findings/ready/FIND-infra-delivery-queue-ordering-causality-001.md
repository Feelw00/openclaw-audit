---
id: FIND-infra-delivery-queue-ordering-causality-001
cell: infra-delivery-queue-ordering-causality
title: session-delivery 큐가 unacked agentTurn 을 reconciliation 없이 blind replay 해 재실행
file: src/infra/session-delivery-queue-recovery.ts
line_range: 105-124
evidence: "```ts\nconst { entry } = opts;\ntry {\n  await opts.deliver(entry);\n \
  \ await ackSessionDelivery(entry.id, opts.stateDir);\n  opts.onRecovered?.(entry);\n\
  \  return \"recovered\";\n} catch (err) {\n  const errMsg = formatErrorMessage(err);\n\
  \  opts.onFailed?.(entry, errMsg);\n  try {\n    await failSessionDelivery(entry.id,\
  \ errMsg, opts.stateDir);\n    return \"failed\";\n  } catch (failErr) {\n    if\
  \ (getErrnoCode(failErr) === \"ENOENT\") {\n      return \"already-gone\";\n   \
  \ }\n    return \"failed\";\n  }\n}\n}\n```\n"
symptom_type: ordering-causality-gap
problem: session-delivery 큐 entry 가 deliver 성공(에이전트 턴 재실행 + 플랫폼 전송 완료) 직후 ack 직전에
  프로세스가 죽으면, 큐 파일이 pending 으로 남아 다음 복구가 동일 agentTurn 을 무조건 재전달한다. 같은 사용자 메시지에 대해 에이전트
  턴이 두 번 실행되고 응답 메시지가 중복 전송된다. 평행 outbound 큐는 이 시나리오를 reconciliation 으로 막지만 session
  큐에는 동등한 가드가 없다.
mechanism: 'E1 = deliver(entry) 가 dispatchAssembledChannelTurn 으로 에이전트 턴을 실행하고 플랫폼
  메시지를 성공 전송 (effect 영속/외부 발생).

  E2 = ackSessionDelivery(entry.id) 가 큐 파일을 제거.

  의도된 인과 순서: E1 → E2 (전달 성공이 ack 보다 먼저).

  역전/유실 시퀀스:

  1. drainQueuedEntry: `await opts.deliver(entry)` 성공 — 에이전트 턴 재실행 + 응답 전송 완료 (recovery.ts:107).

  2. crash / SIGKILL / OOM 발생 — `await ackSessionDelivery` 도달 전 (recovery.ts:108).

  3. 큐 파일은 여전히 pending. recoveryState/마커 미기록(session 큐엔 markStarted/markUnknown 자체가
  없음).

  4. 재시작 → recoverPendingSessionDeliveries → drainQueuedEntry 재진입 → `await opts.deliver(entry)`
  재호출.

  5. deliver 가 "성공했지만 unack" 과 "전송 전 transient 실패" 를 구분할 방법이 없어 무조건 재실행. 결과: 동일 agentTurn
  두 번 실행 + 응답 메시지 중복 전송. (이미 전송됨 ↔ 미전송 인과 정보가 큐에 없음)

  '
root_cause_chain:
- why: 왜 unack 된 성공 전달이 그대로 재전달되는가
  because: drainQueuedEntry 가 deliver 호출 결과를 recovered/failed 두 갈래로만 분류하고, 재진입 시 "이미
    전송되었는지" 를 묻는 reconciliation 분기가 없다. deliver 직전·직후에 send-attempt/outcome 마커도 기록하지
    않는다.
  evidence_ref: src/infra/session-delivery-queue-recovery.ts:107
- why: 왜 마커가 없는가 — session 큐 storage 에 recoveryState 가 부재
  because: 'QueuedSessionDelivery 타입에 recoveryState/platformSendStartedAt 필드가 없고,
    markSession*SendAttemptStarted/Unknown 류 함수가 storage 에 정의되지 않았다(grep: markSession|attemptStarted|platformSend
    → match 0).'
  evidence_ref: src/infra/session-delivery-queue-storage.ts:62
- why: 왜 평행 outbound 큐와 정책이 갈리는가
  because: outbound 큐는 deliver 가 send_attempt_started/unknown_after_send 마커를 기록하고(deliver.ts:600,618),
    복구 시 그 상태면 adapter reconcileUnknownSend 로 실제 전송 여부를 확인한 뒤에만 replay 하며 확인 불가 시
    blind replay 를 거부한다. session 큐는 이 메커니즘 전체를 복제하지 않았다.
  evidence_ref: src/infra/outbound/delivery-queue-recovery.ts:370
- why: 왜 idempotencyKey 가 이를 막지 못하는가
  because: idempotencyKey 는 enqueue 중복 삽입만 dedup 한다(buildEntryId → sha256, jsonDurableQueueEntryExists
    체크). 동일 entry 의 재-deliver 는 같은 id 로 재진입하므로 enqueue 단계 dedup 이 작동하지 않는다.
  evidence_ref: src/infra/session-delivery-queue-storage.ts:125
- why: 왜 expectedSessionId 가드가 이를 막지 못하는가
  because: deliverQueuedSessionDelivery 의 expectedSessionId 검사는 세션이 *바뀐* 경우(sessionId
    불일치)만 skip 한다. crash-후 재전달은 세션이 동일하므로 가드를 통과하고, "이 턴이 이미 실행/전송됨" 은 검사하지 않는다.
  evidence_ref: src/gateway/server-restart-sentinel.ts:265
impact_hypothesis: wrong-output
impact_detail: '정성+조건부: session-delivery 큐의 production caller 는 restart continuation(server-restart-sentinel.ts:606)로,
  게이트웨이 재시작 후 중단된 사용자 턴을 이어 실행한다. deliver=deliverQueuedSessionDelivery 는 agentTurn
  일 때 dispatchAssembledChannelTurn 으로 (a) 에이전트 턴 재실행 (LLM 호출/툴 실행 등 비-idempotent side-effect
  포함) + (b) 응답 플랫폼 전송 을 수행한다. deliver 성공 후 ackSessionDelivery 전 crash 가 들어가면(전달 자체가
  게이트웨이 재시작 직후 불안정 구간에서 일어남) 다음 복구가 같은 턴을 다시 실행해 사용자에게 응답이 두 번 전달되고, 턴 내부 비-idempotent
  액션(예: 외부 메시지 발송/명령 수행)이 중복 수행될 수 있다. 빈도는 "전달 성공↔ack" 사이 좁은 crash window 에 의존해 낮지만,
  restart continuation 은 정의상 crash/restart 직후 경로라 그 window 가 활성화되기 쉽다. outbound 큐는
  동일 상황을 reconciliation 으로 차단하므로 두 큐의 보장 수준이 비대칭이다.

  '
severity: P1
counter_evidence:
  path: src/infra/outbound/delivery-queue-recovery.ts
  line: '370'
  reason: '상태 갱신/전달 경로 실행조건 분류 (CAL-001):

    | 경로 | 조건 |

    |---|---|

    | session drainQueuedEntry deliver→ack (recovery.ts:107-108) | unconditional —
    재진입 시 "이미 전송됨" 검사 없이 무조건 deliver 재호출 |

    | session recoveryState/send-attempt 마커 | 부재(none) — storage 에 필드/함수 0건 (grep:
    recoveryState|markSession|platformSend → match 0) |

    | session expectedSessionId 가드 (restart-sentinel.ts:265) | partial — 세션 *변경* 만
    차단, "턴 이미 실행/전송됨" 은 미검사 → 인과 정보 아님 |

    | session idempotencyKey (storage.ts:125) | enqueue-only — 중복 *삽입* 만 dedup, 동일
    id 재-deliver 엔 무효 |

    | outbound reconcileUnknownSend (recovery.ts:370-440) | guarded — send_attempt_started/unknown_after_send
    면 adapter 로 실제 전송 여부 확인 후에만 replay, 확인 불가 시 blind replay 거부 (:417) |

    | session re-read-after-claim (recovery.ts:152) | 작동하나 무관 — 다른 복구 경로가 *ack 해 파일을
    지운* 경우만 막음. ack 전 crash 로 파일이 남은 경우는 currentEntry!=null 이라 그대로 deliver |

    기존 테스트: session-delivery-queue.recovery.test.ts 는 (a)성공→ack, (b)transient throw→재큐
    만 검증하고 "전달 성공 후 unack 재진입 시 중복 전달" 을 의도된 동작으로 못박은 케이스가 없음 → blind replay 가 의도
    확정된 바 없음.

    '
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
---
# session-delivery 큐가 unacked agentTurn 을 reconciliation 없이 blind replay 해 재실행

## 문제
session-delivery 큐의 복구 경로 `drainQueuedEntry` 는 entry 를 `deliver(entry)` 한 뒤 `ackSessionDelivery` 로 큐 파일을 제거한다. deliver 가 성공(에이전트 턴 재실행 + 플랫폼 응답 전송 완료)했지만 ack 직전에 프로세스가 죽으면 큐 파일은 pending 으로 남는다. 다음 복구는 그 entry 를 다시 `deliver` 하는데, deliver 결과는 "이미 전송됨" 과 "전송 전 transient 실패" 를 구분할 정보(전송 여부 마커/reconciliation)가 큐에 없으므로 무조건 재전달된다. 결과적으로 같은 사용자 턴이 두 번 실행되고 응답이 중복 전송된다. 평행 outbound 큐는 정확히 이 시나리오를 `send_attempt_started`/`unknown_after_send` 마커 + adapter reconcileUnknownSend 로 차단하지만, session 큐에는 동등한 가드가 전혀 없다(정책 divergence).

## 발현 메커니즘
의도된 happens-before: E1(deliver 성공: 턴 실행 + 전송) → E2(ack: 큐 제거). 두 큐 모두 ack 가 deliver 뒤에 오는 "at-least-once" 큐다. 핵심 차이는 outbound 는 전송 시점에 마커를 영속해 재진입 시 "이미 보냈을 수 있음" 을 알지만 session 은 그 인과 정보를 전혀 남기지 않는다는 점이다.

1. `await opts.deliver(entry)` 성공 — `deliverQueuedSessionDelivery` 가 agentTurn 에서 `dispatchAssembledChannelTurn` 으로 에이전트 턴 실행 + 응답 플랫폼 전송 완료 (recovery.ts:107, restart-sentinel.ts:324·354).
2. crash/SIGKILL/OOM — `await ackSessionDelivery(entry.id)` 도달 전 (recovery.ts:108). 게이트웨이 재시작 직후 불안정 구간이라 이 window 가 현실적이다.
3. 큐 파일은 pending 그대로. session 큐 storage 에는 `send_attempt_started`/`unknown_after_send` 같은 전송-여부 마커가 아예 없어 "이미 보냈음" 흔적이 없다 (storage.ts:62 타입에 recoveryState 부재).
4. 재시작 → `recoverPendingSessionDeliveries` → `drainQueuedEntry` 재진입 → `currentEntry` 가 여전히 존재하므로 (recovery.ts:152, 229) `await opts.deliver(entry)` 재호출.
5. deliver 는 "성공했지만 unack" 과 "전송 전 transient 실패" 를 구분 못 해 무조건 재실행. 같은 agentTurn 이 두 번 실행되고 응답이 중복 전송된다.

## 근본 원인 분석
1. `drainQueuedEntry` 가 deliver 를 recovered/failed 두 갈래로만 분류하고 재진입 시 "이미 전송되었는가" 를 묻는 reconciliation 분기가 없다. deliver 직전/직후에 전송-여부 마커도 기록하지 않는다 (recovery.ts:107).
2. 더 근본적으로 `QueuedSessionDelivery` 타입에 `recoveryState`/`platformSendStartedAt` 필드가 없고 markStarted/markUnknown 류 함수가 storage 에 정의되지 않았다(grep `recoveryState|markSession|attemptStarted|platformSend` → session 파일에서 match 0). 즉 인과 정보를 영속할 자리 자체가 없다 (storage.ts:62).
3. 평행 outbound 큐는 deliver 가 전송 직전 `markDeliveryPlatformSendAttemptStarted`(deliver.ts:600), 결과 반환 후 `markDeliveryPlatformOutcomeUnknown`(deliver.ts:618)을 영속하고, 복구 시 그 상태면 `reconcileUnknownQueuedDelivery` 로 실제 전송 여부를 adapter 에 물어 sent/not_sent 를 가린 뒤에만 replay 하며 확인 불가 시 blind replay 를 명시적으로 거부한다(recovery.ts:417 "refusing blind replay without adapter reconciliation"). session 큐는 이 메커니즘 전체를 복제하지 않아 정책이 갈린다 (delivery-queue-recovery.ts:370).
4. idempotencyKey 는 enqueue 중복 삽입만 dedup 한다(buildEntryId sha256 + jsonDurableQueueEntryExists). 동일 entry 재-deliver 는 같은 id 로 재진입하므로 enqueue dedup 이 작동하지 않는다 (storage.ts:125).
5. `expectedSessionId` 가드는 세션이 *바뀐* 경우만 skip 한다. crash-후 재전달은 세션이 동일하므로 가드를 통과하고 "이 턴이 이미 실행/전송됨" 은 검사하지 않는다 (restart-sentinel.ts:265).

## 영향
impact_hypothesis: wrong-output. session-delivery 큐의 유일한 production enqueue caller 는 restart continuation(`server-restart-sentinel.ts:606` 단일 entry)으로, 게이트웨이 재시작 후 중단된 사용자 턴을 이어 실행한다. agentTurn deliver 는 `dispatchAssembledChannelTurn` 으로 (a) 에이전트 턴 재실행(LLM/툴 등 비-idempotent side-effect 포함) + (b) 응답 플랫폼 전송 을 한다. deliver 성공 후 ack 전 crash 가 들어가면 다음 복구가 같은 턴을 다시 실행해 사용자에게 응답이 두 번 전달되고, 턴 내부 비-idempotent 액션이 중복 수행될 수 있다. 빈도는 "전송 성공↔ack" 사이 좁은 crash window 에 의존하지만, restart continuation 은 정의상 crash/restart 직후 경로라 그 window 가 활성화되기 쉽다.

재현 시나리오 (R-7 준수, production hot-path 동일 branch): (a) restart continuation 으로 agentTurn entry 를 enqueue. (b) `deliverQueuedSessionDelivery` 의 deliver 가 `dispatchAssembledChannelTurn` 송신을 완료하도록 하되 `ackSessionDelivery` 직전에 프로세스 중단(또는 ack 호출을 한 번 차단)으로 unack pending 을 만든다. (c) 재시작 후 `recoverPendingSessionDeliveries` 재진입. (d) 동일 entry 가 다시 deliver 되어 deliver(=dispatch) 가 2회 호출됨을 관측. outbound 큐를 같은 시나리오로 돌리면 reconcileUnknownSend 가드로 2회차 전송이 차단되는 대비가 드러난다. 두 이벤트(전송 성공 / crash)는 게이트웨이 재시작 직후라는 실제 production 발생 근접 구간에서 일어나므로 synthetic 지연 강제가 아니다.

## 반증 탐색
- seq/generation 가드: `rg 'seq|sequence|generation|version|nextSeq|monotonic'` 를 두 큐 recovery/storage 4파일에 → match 0. 어느 경로에도 단조 seq/generation 검사 없음.
- terminal 보호: session entry 에 done/failed 류 terminal 영속 상태가 없다. ack 는 파일 삭제(=제거)지 terminal 마킹이 아니라, crash 로 ack 가 빠지면 "전송됨" 흔적이 0. terminal-protected 아님.
- await 직렬화: drainQueuedEntry 는 entry 들을 await 직렬로 돌려 *entry 간* 순서는 유지하나, *한 entry 의 deliver↔ack 인과* 를 crash 에 대해 보호하지 못한다(직렬화는 중복 재전달과 무관).
- outbound 대비 가드: outbound 는 reconcileUnknownSend(recovery.ts:370-440) + blind replay 거부(:417)로 정확히 이 시나리오를 막음. session 에는 동등 경로 부재 → 비대칭이 결함의 핵심 warrant.
- re-read-after-claim: session 복구도 claim 후 `loadPendingSessionDelivery` 재읽기(recovery.ts:152, 229)를 하지만, 이는 *다른 복구 경로가 이미 ack 해 파일이 사라진* 경우만 막는다. ack 전 crash 로 파일이 남은 케이스는 currentEntry!=null 이라 그대로 deliver 로 흘러간다.
- 기존 테스트: session-delivery-queue.recovery.test.ts 는 성공→ack, transient throw→재큐만 검증. "전송 성공 후 unack 재진입 시 중복 전달" 을 의도된 동작으로 못박은 테스트 없음 → blind replay 가 의도 확정되지 않음.
- 호출 빈도: production enqueue caller 1곳(restart continuation, 단일 entry). hot-path 는 아니나 crash/restart 경로 전용이라 window 활성화가 자연스러움 → P0 가 아닌 P1.

## Self-check
### 내가 확실한 근거
- session `drainQueuedEntry` 가 deliver→ack 순서로 호출하며 재진입 시 reconciliation 분기가 없음 (recovery.ts:105-124, Read 확인).
- session 큐 storage/recovery 에 recoveryState/send-attempt 마커 및 reconcile 함수가 전무(grep match 0).
- outbound 큐는 동일 시나리오를 send_attempt_started/unknown_after_send + reconcileUnknownSend 로 막고 blind replay 를 명시 거부(recovery.ts:370-440, :417, deliver.ts:600·618).
- idempotencyKey 는 enqueue 중복만 dedup(storage.ts:122-129), expectedSessionId 는 세션 변경만 차단(restart-sentinel.ts:265-277).
- deliver=deliverQueuedSessionDelivery 가 agentTurn 에서 dispatchAssembledChannelTurn(턴 실행+전송)을 수행(restart-sentinel.ts:253-324, 354-377).

### 내가 한 가정
- "deliver 성공 후 ack 전 crash" window 가 production 에서 실제로 들어갈 수 있다고 가정. ack 는 atomic rename 두-단계지만 그 *시작 전* crash 이면 entry 는 pending 그대로다.
- dispatchAssembledChannelTurn 의 송신 단계가 deliver 성공 시점까지 완료된다고 가정(send.status==='sent' 라야 throw 안 함, restart-sentinel.ts:370-376). 부분 전송이면 영향은 다를 수 있으나 중복-실행 위험 방향은 동일.
- agentTurn 재실행이 비-idempotent side-effect 를 포함한다고 가정(LLM/툴). 순수 idempotent 라면 사용자 체감은 응답 중복 정도로 한정.

### 확인 안 한 것 중 영향 가능성
- dispatchAssembledChannelTurn 내부에 별도의 message-level dedup/idempotency(예: messageId 기반 platform 측 dedup)가 있는지는 turn/kernel 경로가 allowed_paths 밖이라 미확인. 만약 거기서 강한 dedup 이 있으면 중복 전송은 흡수되고 영향은 "턴 재실행 부작용" 으로 축소될 수 있음.
- 실제 crash 빈도(게이트웨이 재시작 직후 deliver↔ack window 명중률)는 정량 미측정. 낮으면 발현은 드묾.
- maxRetries 소진 후 moveToFailed(recovery.ts:160-172) 가 일부 반복 재전달을 결국 종료시키나, 그 사이 이미 발생한 중복 실행/전송은 되돌리지 못함.
