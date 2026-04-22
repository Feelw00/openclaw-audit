---
id: FIND-channels-error-boundary-002
cell: channels-error-boundary
title: ack-reactions removeAfterReply 가 ack reject 시 stale emoji + unhandled rejection
file: src/channels/ack-reactions.ts
line_range: 81-103
evidence: "```ts\nexport function removeAckReactionAfterReply(params: {\n  removeAfterReply:\
  \ boolean;\n  ackReactionPromise: Promise<boolean> | null;\n  ackReactionValue:\
  \ string | null;\n  remove: () => Promise<void>;\n  onError?: (err: unknown) =>\
  \ void;\n}) {\n  if (!params.removeAfterReply) {\n    return;\n  }\n  if (!params.ackReactionPromise)\
  \ {\n    return;\n  }\n  if (!params.ackReactionValue) {\n    return;\n  }\n  void\
  \ params.ackReactionPromise.then((didAck) => {\n    if (!didAck) {\n      return;\n\
  \    }\n    params.remove().catch((err) => params.onError?.(err));\n  });\n}\n```\n"
symptom_type: error-boundary-gap
problem: removeAckReactionAfterReply 는 `void params.ackReactionPromise.then(...)`
  로 ack 결과를 감시하지만 `.then` 에 onRejected 핸들러가 없다. ack 부착 자체가 reject 하면 (1) remove()
  경로가 건너뛰어져 stale ack emoji 가 메시지에 남고 (2) rejection 이 process-level unhandled rejection
  으로 escape 한다. 제공된 onError 는 오직 remove() 의 reject 만 받음.
mechanism: "1. 상위 레이어 (auto-reply reply 경로) 가 ack emoji 부착을 시도하며 `ackReactionPromise`\
  \ 를 생성 — 성공 시 resolve(true), 건너뛰었으면 resolve(false), 네트워크/permission 오류면 reject.\n\
  2. reply 가 끝난 뒤 `removeAckReactionAfterReply({ ackReactionPromise, remove, onError,\
  \ ... })` 호출 — \"ack 가 실제로 붙었다면 답장 후에 제거\" 정책.\n3. line 97-102 의 `void ackReactionPromise.then(didAck\
  \ => ...)` 는 onFulfilled 만 지정. reject 경로는 skip.\n4. ack 부착이 reject 한 경우:\n   - `.then`\
  \ 콜백 자체가 실행 안 됨 → remove() 호출 skip → 만약 실제로는 일부 ack 가 걸렸는데 reject 로 끝났다면 (partial\
  \ failure) stale emoji 가 메시지에 남음.\n   - rejection 이 아래 chain 으로 propagate → `void`\
  \ 로 swallow → node 가 unhandledRejection 발행.\n5. src/infra/unhandled-rejections.ts:345\
  \ handler 가 수신. transient (ECONNRESET 등) 은 warn + continue, non-transient 은 process.exit(1).\n\
  6. 사용자 관점: \"답장은 보냈는데 \U0001F440 이모지가 그대로 남아 있는 메시지\" (wrong-output) — 특히 reaction\
  \ add 에 permission 오류가 섞인 상황.\n"
root_cause_chain:
- why: 왜 .then 에 onRejected 를 붙이지 않았는가?
  because: onError 파라미터가 제공되지만 내부에서는 오직 remove() 의 catch 에만 연결됨. ackReactionPromise
    자체의 실패는 "처음부터 ack 가 없었다" 로 간주하는 가정. 그러나 실제로는 partial-ack (첫 번째 reaction 이 붙었는데
    두 번째가 실패) 나 remove 대상인 emoji 가 이미 걸려있는 케이스 구분 불가.
  evidence_ref: src/channels/ack-reactions.ts:97-102
- why: 왜 `void` 로 fire-and-forget 인가?
  because: caller (auto-reply reply 파이프라인) 가 답장 path 를 blocking 없이 진행하기 위해 fire-and-forget.
    hot-path 지연 회피. 그러나 .catch 없이 void 하면 unhandled 로 propagate 하는 것은 CAL-007 upstream
    fix 에서 이미 확립된 반패턴.
  evidence_ref: src/channels/ack-reactions.ts:97
- why: 왜 onError 가 ackReactionPromise 에도 연결되지 않았는가?
  because: 함수 인터페이스 설계상 onError 는 "remove 실패 전용" 콜백처럼 이름지어짐. 그러나 reliability 관점에서는
    ack-add 실패가 remove 실패보다 상위 문제 — remove 를 호출할지 여부 자체가 달라짐.
  evidence_ref: src/channels/ack-reactions.ts:81-103
impact_hypothesis: wrong-output
impact_detail: '정성: ack reaction (e.g. 👀 queued emoji) 이 "답장 후 제거" 모드인데 부착 promise
  가 reject 하면 이모지가 stale 로 남아 사용자에게 잘못된 상태 신호. 빈도는 channel plugin 별 reaction API 오류율에
  의존 — Telegram/Slack/Discord 는 rate-limit 과 permission 오류가 드물지 않음. 추가로 unhandledRejection
  유입으로 transient warn log 증가, 드물게 non-transient 시 process restart. status-reactions.ts
  쪽은 applyEmoji 내부에 try/catch + onError 로 보호되지만, 본 path (removeAckReactionAfterReply)
  는 외부 ackReactionPromise 에 의존하므로 적용 안 됨.'
severity: P3
counter_evidence:
  path: src/channels/ack-reactions.ts
  line: 101
  reason: 'R-3 Grep:

    - `rg -n "\.then\(" src/channels/ack-reactions.ts` — line 97 하나. onRejected 없음.

    - `rg -n "\.catch\(" src/channels/ack-reactions.ts` — line 101 `params.remove().catch`
    하나. ackReactionPromise 에는 없음.

    - `rg -n "void\s+params\.ackReactionPromise" src/` — 이 파일만 hit.

    방어 경로:

    - line 101 `params.remove().catch(err => params.onError?.(err))` — remove() 쪽만
    방어.

    - status-reactions.ts applyEmoji (line 234-254) 는 try/catch + onError — 별도 컨트롤러,
    이 함수와 무관.

    - caller 가 ackReactionPromise 를 만들 때 `.catch` 를 선-부착해두는지는 본 파일 스코프에서는 확인 불가 (외부
    계약). 확인 필요.

    R-5 execution condition:

    | 경로 | 조건 | 비고 |

    |---|---|---|

    | line 97 void .then(onFulfilled) | conditional-edge | removeAfterReply && ackReactionPromise
    && ackReactionValue |

    | line 101 remove().catch | conditional-edge | didAck === true 경로 |

    | ackReactionPromise reject 경로 | conditional-edge | adapter setReaction throw
    — rate-limit/permission |

    unconditional 방어 없음. caller 가 ackReactionPromise 를 사전-catch 한다면 이 FIND 는 성립 안
    함 — 그러나 openclaw/src/channels 내부만으로는 그 계약이 보이지 않음. severity P3 로 조정.

    Primary-path inversion: "ackReactionPromise 는 caller 가 이미 .catch 된 Promise 를 전달한다"
    가 계약이라면 본 FIND 무효. 그러나 외부 코드에서 사전 catch 를 강제하는 타입 시그니처는 없음 (Promise<boolean>)
    — plugin-sdk 구현체 확인 필요.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-22'
---
# ack-reactions removeAfterReply 가 ack reject 시 stale emoji + unhandled rejection

## 문제

`removeAckReactionAfterReply` 는 "답장 후 ack 이모지 제거" 정책을 구현. 내부에서 `void ackReactionPromise.then(didAck => ...)` 로 ack 부착 결과만 기다린다. `.then` 에 onRejected 가 없어 ackReactionPromise 가 reject 되는 경우:
1. remove() 호출이 스킵 — 이모지 stale.
2. rejection 이 `void` chain 으로 escape → process-level unhandled rejection.

onError 파라미터는 remove() 실패만 받도록 연결되어 있어 ack-add 실패를 놓친다.

## 발현 메커니즘

1. reply 파이프라인이 ack emoji 를 비동기로 부착 → `ackReactionPromise: Promise<boolean>` 생성.
2. 답장 완료 후 `removeAckReactionAfterReply(...)` 호출.
3. line 97 `void params.ackReactionPromise.then((didAck) => ...)` 이 await 대신 fire-and-forget.
4. 만약 ack 부착 어댑터 호출이 throw 하면 ackReactionPromise → reject.
5. `.then` 의 onRejected 가 없으므로 콜백 skip. rejection 은 그대로 chain 에 남고 `void` 로 swallow → unhandledRejection 이벤트.
6. `src/infra/unhandled-rejections.ts:345` 가 수신. transient 은 warn, non-transient 은 process.exit(1).
7. 사용자 관점: ack 이모지가 남아 있는 채로 답장만 도착 → "assistant 가 아직 처리 중인 것처럼 보임" (wrong visual state).

## 근본 원인 분석

onError 이 "remove 실패 전용" 콜백으로 연결되어 있는 것이 설계 누락. ack-add 실패는 더 상위의 reliability 이슈 — 적어도 "stale ack emoji 가 남을 수 있음" 을 로깅해야 함. 그리고 fire-and-forget 경로의 rejection 은 CAL-007 upstream fix 에서도 이미 확립된 반패턴 — `.catch` 부착이 표준.

## 영향

- 영향 유형: wrong-output (stale ack emoji) + 드물게 crash (non-transient reject).
- 관측 가능성: rejection 이 infra/unhandled-rejections 에서 warn 으로 기록 — 단 "어떤 reply / 어떤 채널" 인지 문맥 없음.
- 재현: test harness 에서 `ackReactionPromise: Promise.reject(new Error("permission"))` 주입 → removeAckReactionAfterReply 호출 → unhandledRejection + remove() 미호출.
- 심각도: P3. ack emoji 부착 실패 자체의 빈도가 높지 않고, reliability core (메시지 delivery) 를 직접 해치지 않음. 단 wrong-output 경로는 존재하여 observability-only 는 아님.

## 반증 탐색

R-3 Grep:
- `rg -n "\.then\(" src/channels/ack-reactions.ts` — 1 hit, line 97 onRejected 없음.
- `rg -n "\.catch\(" src/channels/ack-reactions.ts` — 1 hit, line 101 (remove() 만).
- `rg -n "void\s+params\.ackReactionPromise|ackReactionPromise\." src/` — 이 파일만.

방어 경로:
- line 101 `params.remove().catch(err => params.onError?.(err))` — remove() 만 방어.
- status-reactions.ts 의 `applyEmoji` 는 try/catch + onError 로 방어되지만, 본 함수는 외부 주입 ackReactionPromise 에 의존.
- caller (plugin-sdk / auto-reply) 가 ackReactionPromise 에 사전 .catch 를 걸어두는 계약이라면 FIND 무효. 그러나 타입 시그니처 `Promise<boolean>` 은 이를 강제하지 않음.

R-5 execution condition:
| 경로 | 조건 | 비고 |
|---|---|---|
| line 97 void .then | conditional-edge | removeAfterReply && ackReactionPromise && ackReactionValue |
| line 101 remove().catch | conditional-edge | didAck === true 경로 |
| ackReactionPromise reject | conditional-edge | adapter setReaction throw |

unconditional 방어는 없음. update hot-path 는 caller 쪽 사전 catch 유무에 의존.

Primary-path inversion: "caller 가 `ackReactionPromise.catch(...)` 를 이미 걸고 동일 참조를 넘긴다" 가 실제 계약이면 이 FIND 는 성립 안 함 (이 경우 rejection 은 이미 consumed 되어 여기서는 warn-level 로 repeated). plugin-sdk / auto-reply reply 파이프라인의 실제 구현 확인 필요 — 이 셀 스코프 밖. severity P3 로 조정.

## Self-check

### 내가 확실한 근거
- line 97 의 `.then` 콜백은 onFulfilled 만 전달, onRejected 없음 — Read 로 확인.
- onError 는 line 101 에서 remove() 의 catch 에만 연결.
- `void ackReactionPromise.then(...)` chain 이 reject 시 `void` 로 누락 — JS 표준 동작.
- infra/unhandled-rejections.ts:345 가 수신.

### 내가 한 가정
- caller 가 ackReactionPromise 에 사전 `.catch` 를 걸지 않는다 — plugin-sdk 구현 전수 확인 안 함.
- "stale emoji" 가 사용자 혼란을 일으킨다 — UX 판단.
- ack 부착 실패 빈도 — rate-limit / permission 오류는 "드문 편" 이지만 real.

### 확인 안 한 것 중 영향 가능성
- plugin-sdk / auto-reply 내 ackReactionPromise 생성부가 이미 `.catch` 로 fulfillment-only Promise 로 변환하는지 여부. 그럴 경우 이 FIND 는 dead code-level observability-only → severity P3 미만 → drop 대상.
- didAck === false 인 정상 경로가 production 에서 얼마나 빈번한지 — 이 경로는 문제 없음.
