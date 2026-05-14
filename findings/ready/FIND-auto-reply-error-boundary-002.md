---
id: FIND-auto-reply-error-boundary-002
cell: auto-reply-error-boundary
title: queueReplyRunMessage 의 void backend.queueMessage 가 .catch 없이 floating promise
file: src/auto-reply/reply/reply-run-registry.ts
line_range: 496-507
evidence: "```ts\nexport function queueReplyRunMessage(sessionId: string, text: string):\
  \ boolean {\n  const operation = resolveReplyRunForCurrentSessionId(sessionId);\n\
  \  const backend = operation ? getAttachedBackend(operation) : undefined;\n  if\
  \ (!operation || operation.phase !== \"running\" || !backend?.queueMessage) {\n\
  \    return false;\n  }\n  if (!backend.isStreaming()) {\n    return false;\n  }\n\
  \  void backend.queueMessage(text);\n  return true;\n}\n```\n"
symptom_type: error-boundary-gap
problem: 'queueReplyRunMessage 은 backend.queueMessage(text) 의 반환 Promise 를 `void`
  로 swallow 하고 .catch 를 붙이지 않는다. 백엔드 (embedded pi agent 의 activeSession.steer 등) 가
  rejection 으로 답하면 unhandled rejection 이 발생한다. 동일 author 가 작성한 pi-embedded-runner/runs.ts:148-154
  의 자매 경로 (`prepared.handle.queueMessage(...).catch(...)`) 는 명시적으로 catch + diag.debug
  로 보호하고 있어 비대칭이 명확하다. 한편 호출 측 (prepareEmbeddedPiQueueMessage → returns `queued: true,
  gatewayHealth: live`) 는 success 만 가정하므로 rejection 발생 시 사용자에게 보고 가능한 surface 가 없다.

  '
mechanism: '1. external caller (예: pi-embedded-runner/runs.ts:198 prepareEmbeddedPiQueueMessage)
  가 `queueReplyRunMessage(sessionId, text)` 호출. 활성 embedded run 핸들이 ACTIVE_EMBEDDED_RUNS
  에 없고 reply-run-registry 에 있을 때 fallback 경로.

  2. 라인 497-500: operation/backend.queueMessage 존재 확인.

  3. 라인 502-504: isStreaming 확인 — 정상 sync gate.

  4. 라인 505: `void backend.queueMessage(text)` 호출. 반환은 `Promise<void>` (reply-run-registry.ts:18
  의 타입 `queueMessage?: (text: string) => Promise<void>`).

  5. backend 실제 구현은 agents/pi-embedded-runner/run/attempt.ts:2767-2772 의 `async (text,
  options) => { ... await activeSession.steer(text) }`. activeSession.steer 가 throw
  가능 (네트워크 오류, agent 상태 오류, embed session 의 abort 와 race 등).

  6. .catch 없음 → rejection 이 microtask 로 escalate → node 의 `unhandledRejection` 이벤트
  발행.

  7. infra/unhandled-rejections.ts:345 handler 가 수신: transient 분류이면 warn-only 로 logging,
  non-transient (RuntimeError 등) 이면 `exitWithTerminalRestore` 경로로 process.exit(1)
  가능 (FIND-channels-error-boundary-001 의 인용 경로와 동일).

  8. 라인 506: 함수는 즉시 `return true` — caller 는 "queued" 로 판단.

  9. 호출 측 chain (pi-embedded-runner/runs.ts:198-209) 도 `queued: true, target: "reply_run",
  gatewayHealth: "live"` 반환. 실패 신호 surface 없음. 사용자 메시지 silent loss 가능 (수신 처리 안 됨에도
  user 에게 "queued" 응답).

  '
root_cause_chain:
- why: 왜 queueReplyRunMessage 는 backend.queueMessage 의 Promise 에 .catch 를 붙이지 않았는가?
  because: '이 함수는 boolean (queued or not) 만 sync 로 보고하는 API 시그니처. queueMessage 의 async
    완료를 caller 가 관찰할 방법이 없어 "발사 후 망각" 으로 설계됨. 그러나 같은 비동기-fire-and-forget 정책을 따르는 자매
    경로 (pi-embedded-runner/runs.ts:148-154) 는 동일 author 가 `.catch(err => diag.debug(\`queue
    message rejected after enqueue: ...\`))` 로 명시 보호 — 의도된 fire-and-forget 가 아니라 단순
    누락.

    '
  evidence_ref: src/agents/pi-embedded-runner/runs.ts:148-154
- why: 왜 backend.queueMessage 가 reject 할 수 있는가?
  because: 'attempt.ts:2767-2772 의 구현은 `await activeSession.steer(text)`. activeSession.steer
    (agents/pi-embedded-runner/run/attempt.ts 내부) 는 agent 의 send_message 도구를 즉시 inject
    하는 경로로 IO + state mutation 동반. user 가 메시지를 보내는 동시에 abort 이벤트, network reconnect,
    internal Result error 등이 발생하면 throw 가 자연스러운 경로.

    '
  evidence_ref: src/agents/pi-embedded-runner/run/attempt.ts:2767-2772
- why: 왜 caller chain 이 reject 를 관찰할 수 없게 설계됐는가?
  because: 'queueReplyRunMessage 가 `boolean` 만 반환. caller (prepareEmbeddedPiQueueMessage)
    는 true 를 받자마자 `{ queued: true, gatewayHealth: "live" }` 로 outcome 빌드 후 반환. 비동기
    reject 가 발생해도 outcome 을 retroactively 수정할 수 없으므로 자매 경로처럼 .catch 안에서 logging 만이라도
    해야 함.

    '
  evidence_ref: src/agents/pi-embedded-runner/runs.ts:192-213
- why: 왜 process-wide unhandledRejection handler 가 보조 방어가 안 되는가?
  because: 'infra/unhandled-rejections.ts:345 의 handler 는 transient (ECONNRESET 등)
    코드는 warn-only 로 continue, non-transient (TypeError, generic Error) 는 `exitWithTerminalRestore`
    로 process.exit(1) 까지 갈 수 있다. 즉 보조 방어는 "robust silent" 가 아니라 "process crash 위험".
    non-transient 한 backend error 1건이 전체 process 를 죽일 위험.

    '
  evidence_ref: src/infra/unhandled-rejections.ts (line 인용은 채널 audit 의 FIND-channels-error-boundary-001
    에서 사용된 라인 345 기준; 본 file 은 본 audit 의 allowed_paths 밖)
impact_hypothesis: crash
impact_detail: '정량 난이: queueReplyRunMessage 트리거 빈도는 사용자 메시지가 들어왔지만 ACTIVE_EMBEDDED_RUNS
  에 없는 fallback 경로에 비례. embedded run 이 막 종료되거나 새 메시지가 race 로 들어올 때. 빈도는 운영 환경에 의존하지만
  "embedded run + 동시 메시지" 패턴은 일반적.

  - transient backend error (ECONNRESET, EAI_AGAIN): warn-only 로 끝나 사용자 메시지 silent
  loss (caller 는 queued=true 신호받음). 사용자 입장에서는 보낸 메시지가 처리 안 되는 wrong-output / data-loss.

  - non-transient error (코드 버그, RangeError, TypeError 등): process.exit(1) 까지 도달 →
  gateway crash → 모든 active session 영향.

  자매 경로 (pi-embedded-runner/runs.ts:148-154) 는 catch + diag.debug 로 silent loss 만
  발생하고 crash 위험 차단. 본 file 의 라인 505 는 동일 가드 부재.

  '
severity: P2
counter_evidence:
  path: src/agents/pi-embedded-runner/runs.ts
  line: 148-154
  reason: 'R-3 Grep:

    - `rg -n "void\s+backend\.queueMessage" src/auto-reply/reply/reply-run-registry.ts`
    — line 505. .catch 미부착.

    - `rg -n "\.catch\(" src/auto-reply/reply/reply-run-registry.ts` — 0 hits 함수 외
    in-file.

    - `rg -n "queueMessage" src/` — call sites: reply-run-registry.ts:505 (void),
    pi-embedded-runner/runs.ts:149 (catch 동반), pi-embedded-runner/runs.ts:177 (await
    + try/catch outer).

    - `rg -n "throw " src/auto-reply/reply/reply-run-registry.ts` — 0 hits (이 file
    자체는 throw 없음; backend 가 throw).

    - `rg -n "process\.on\([''\"](uncaughtException|unhandledRejection)[''\"]" src/`
    — infra/unhandled-rejections.ts 1건. transient warn-only / non-transient crash.


    방어 경로:

    - infra/unhandled-rejections.ts process handler: 보조 방어이나 non-transient 에서 process.exit.
    unconditional-but-imperfect.

    - 자매 경로 (pi-embedded-runner/runs.ts:148-154): .catch(err => diag.debug(...)) —
    fire-and-forget logging. 본 라인 505 는 동일 보호 부재.

    - getAttachedBackend / backend?.queueMessage / backend.isStreaming() — sync gate
    만, async rejection 은 미보호.


    R-5 execution condition:


    | 경로 | 조건 | 비고 |

    |---|---|---|

    | line 505 void backend.queueMessage(text) | conditional-edge | operation phase=running
    + backend.queueMessage 존재 + isStreaming 시 |

    | infra/unhandled-rejections.ts handler | unconditional | process-wide. transient
    warn-only / non-transient process.exit |

    | 자매 catch (pi-embedded-runner/runs.ts:150-154) | conditional-edge | 동일 fire-and-forget
    의 정상 대응 |


    primary-path inversion 없음. process-wide handler 가 "fallback" 이긴 하나 transient class
    가 아닌 backend rejection 은 process.exit 으로 escalate 되므로 unconditional 방어가 아니다. 따라서
    severity P2 유지.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-14'
domain_notes_ref: domain-notes/auto-reply.md
cross_refs:
- FIND-channels-error-boundary-001
related_tests:
- src/auto-reply/reply/reply-run-registry.test.ts
---
# queueReplyRunMessage 의 void backend.queueMessage() 가 .catch 없이 floating promise — 자매 경로 대비 비대칭

## 문제

`queueReplyRunMessage` (reply-run-registry.ts:496-507) 는 활성 reply operation 의 backend 가 정의된 `queueMessage(text): Promise<void>` 를 `void backend.queueMessage(text)` 로만 호출하고 `.catch` 를 붙이지 않는다. backend (embedded pi agent 의 `activeSession.steer`) 가 reject 하면 unhandled rejection 이 발생한다. 동일 author 가 작성한 자매 경로 (`pi-embedded-runner/runs.ts:148-154`) 는 같은 fire-and-forget 정책을 `.catch(err => diag.debug(\`...rejected after enqueue: ...\`))` 로 보호하고 있어 본 라인 505 만 비대칭 누락.

## 발현 메커니즘

1. 외부 caller (pi-embedded-runner/runs.ts:198 `prepareEmbeddedPiQueueMessage`) 가 ACTIVE_EMBEDDED_RUNS 에 핸들이 없을 때 fallback 으로 `queueReplyRunMessage(sessionId, text)` 호출.
2. reply-run-registry.ts:497-500 — operation/backend.queueMessage 존재 확인 (sync gate).
3. 라인 502-504 — isStreaming 확인.
4. 라인 505 — `void backend.queueMessage(text)` 호출. 반환 Promise 는 어디에도 await 되지 않고 catch 도 없다.
5. backend 구현 (agents/pi-embedded-runner/run/attempt.ts:2767-2772):
   ```
   queueMessage: async (text, options) => {
     if (options?.steeringMode) { activeSession.agent.steeringMode = options.steeringMode; }
     await activeSession.steer(text);
   }
   ```
   `activeSession.steer(text)` 가 throw 가능 (네트워크 오류, agent abort race, internal RuntimeError 등).
6. throw 발생 → fulfilled state 미도달 → rejected Promise → node 이벤트 루프가 `unhandledRejection` 발행.
7. `infra/unhandled-rejections.ts:345` handler 수신 (channel audit FIND-001 에서 인용):
   - transient code (ECONNRESET, EAI_AGAIN 등): warn-only, process 유지.
   - non-transient (Error, TypeError 등): `exitWithTerminalRestore("unhandled rejection")` → process.exit(1).
8. queueReplyRunMessage 는 라인 506 에서 즉시 `return true`. caller 는 "queued" 로 판단 → outcome `{ queued: true, target: "reply_run", gatewayHealth: "live" }` 를 사용자에게 반환.
9. 결과: backend 가 transient reject 면 사용자 메시지 silent loss (UI 는 queued 표시), non-transient 면 process crash.

## 근본 원인 분석

queueReplyRunMessage 는 boolean (queued 여부) 만 sync 로 보고하는 API. backend.queueMessage 의 async 완료를 caller 가 관찰할 surface 가 없어 fire-and-forget 으로 작성됐다. 그러나 fire-and-forget 자체가 문제가 아니라 fire-and-forget **without .catch** 가 문제. 동일 author 가 작성한 pi-embedded-runner/runs.ts:148-154 는 fire-and-forget 임에도 `.catch(err => diag.debug(\`queue message rejected after enqueue: sessionId=${sessionId} err=${formatQueueError(err)}\`))` 로 명시 보호 — 의도된 fire-and-forget 정책이 정상화돼 있음.

backend.queueMessage 가 reject 할 가능성은 attempt.ts:2767-2772 의 `await activeSession.steer(text)` 가 agent IO + state mutation 동반이라는 점에서 충분히 현실적. user 가 메시지를 보낸 직후 abort 이벤트 또는 internal error 가 발생하면 throw 가 자연스럽다.

caller chain (prepareEmbeddedPiQueueMessage:192-213) 는 queueReplyRunMessage 가 true 를 반환하면 outcome 을 `queued:true, target:"reply_run", gatewayHealth:"live"` 로 빌드. 비동기 reject 발생 시 outcome 을 retroactive 수정할 수 없으므로, queueReplyRunMessage 가 자체적으로 .catch 안에서 logging 해야만 운영자가 silent loss 를 알아챌 수 있다.

infra/unhandled-rejections.ts:345 의 process-wide handler 는 보조 방어로 작동하지만 (a) transient 분류는 사용자 메시지 loss 를 silent 로 처리, (b) non-transient 는 process.exit(1) 로 escalation — gateway 전체 crash 위험.

## 영향

- 영향 유형: crash (non-transient 시) + data-loss (transient 시).
- 정량:
  - 트리거 빈도: ACTIVE_EMBEDDED_RUNS 에 없고 reply-run-registry 에 있는 session 에 대한 메시지 송신 — embedded run 막 종료 + 동시 메시지 race 시 발생. 운영 환경에 따라 다르나 multi-session gateway 에서 일상적 race window.
  - 사용자 visible: caller chain 은 `queued: true, gatewayHealth: "live"` 를 받지만 실제 message 는 backend rejection 으로 lost.
  - process crash 사례: backend.queueMessage 가 transient 가 아닌 generic Error (e.g. internal assertion, plugin contract violation) 로 throw → infra handler 가 process.exit(1) → gateway 전체 down.
- 재현:
  - reply-run-registry.test.ts:158 의 `queueMessage = vi.fn(async () => { throw new Error("boom") })` 형태 stub 으로 즉시 시연 가능 (현재 테스트는 reject 케이스 미커버; `vi.fn(async () => {})` 만 사용).
- 심각도: P2. unhandledRejection 의 process crash 위험은 P1 후보지만, 트리거가 conditional-edge (reply-run-registry fallback + non-transient backend error) 이라 P2.

## 반증 탐색

R-3 Grep:
- `rg -n "void\s+backend\.queueMessage" src/auto-reply/reply/reply-run-registry.ts` — line 505. 단일 site, .catch 없음.
- `rg -n "\.catch\(" src/auto-reply/reply/reply-run-registry.ts` — 0 hits.
- `rg -n "queueMessage" src/` — auto-reply/reply/reply-run-registry.ts:505 (void), agents/pi-embedded-runner/runs.ts:149 (catch), agents/pi-embedded-runner/runs.ts:177 (await + outer try/catch). 즉 auto-reply 경로만 보호 없음.
- `rg -n "throw " src/auto-reply/reply/reply-run-registry.ts` — 0 hits (이 file 자체는 throw 없음, backend 가 throw 진입원).
- `rg -n "process\.on\(['\"](uncaughtException|unhandledRejection)['\"]" src/` — infra/unhandled-rejections.ts:345 1건.
- `rg -n "JSON\.parse" src/auto-reply/reply/reply-run-registry.ts` — 0 hits.

방어 경로 / 기존 테스트:
- reply-run-registry.test.ts:158 `const queueMessage = vi.fn(async () => {});` — 정상 케이스만, reject 시나리오 0건.
- get-reply-run.media-only.test.ts:985 `queueMessage: vi.fn(async () => {})` — 동일.
- 자매 경로 pi-embedded-runner/runs.ts:148-154 `.catch(err => diag.debug(\`queue message rejected after enqueue: ...\`))` — pattern 으로 보호. reply-run-registry.ts:505 누락.
- process-wide handler (infra/unhandled-rejections.ts:345) 는 fallback 이지만 non-transient class crash 위험 — unconditional-but-imperfect.

R-5 execution condition:

| 경로 | 조건 | 비고 |
|---|---|---|
| line 505 void backend.queueMessage(text) | conditional-edge | operation.phase==='running' + backend.queueMessage 존재 + isStreaming() |
| 자매 catch (pi-embedded-runner/runs.ts:150-154) | conditional-edge | 정상 보호 reference |
| infra/unhandled-rejections.ts:345 handler | unconditional | warn-or-exit |
| (in-file .catch) | 없음 | 본 file 보호 부재 |

primary-path inversion: "backend.queueMessage 가 거의 throw 하지 않는다" 가능. 그러나 자매 경로가 명시적으로 catch 를 부착한 사실이 maintainer 들이 throw 빈도를 무시 가능 수준으로 보지 않는다는 증거. severity P2 유지.

Self-reference (CAL-001 회피): silent catch 가 아닌 missing catch 이므로 "caller 들이 throw 받는지" 질문은 적용 못 함. 대신 caller chain 이 boolean 만 받고 async 실패를 신호 받을 surface 가 없다는 점이 충분조건.

## Self-check

### 내가 확실한 근거
- reply-run-registry.ts:505 `void backend.queueMessage(text)` 에 .catch 없음 — Read 확인.
- reply-run-registry.ts:18 `queueMessage?: (text: string) => Promise<void>` 타입 — Read 확인.
- pi-embedded-runner/runs.ts:148-154 의 자매 catch — Read 확인 (동일 fire-and-forget pattern + .catch).
- pi-embedded-runner/run/attempt.ts:2767-2772 의 backend 구현 `await activeSession.steer(text)` — Read 확인.
- caller chain (prepareEmbeddedPiQueueMessage:192-213) 는 outcome 을 sync 빌드 — Read 확인.
- reply-run-registry.test.ts 가 reject 시나리오 미커버 — `vi.fn(async () => {})` 만 사용, 확인.

### 내가 한 가정
- activeSession.steer 가 production 에서 실제 throw 하는 빈도. concurrent abort race / embed agent state 오류는 가능성 추측이며 정량 미확보.
- infra/unhandled-rejections.ts:345 가 non-transient 를 어떻게 분류하는지의 정확한 코드 — channel audit FIND 의 인용을 신뢰 (line 345). 본 audit allowed_paths 밖이라 직접 읽지 않음.
- pi-embedded-runner author 와 reply-run-registry author 가 동일인이라는 가정 — git blame 미확인. "자매 경로" 라는 표현은 동일 함수 시그니처 + 동일 fire-and-forget pattern 기반.

### 확인 안 한 것 중 영향 가능성
- queueReplyRunMessage 외에 backend.queueMessage 를 호출하는 다른 path 가 같은 문제 가지는지 — Grep 으로 단일 site 확인. embedded run 경로는 await + try/catch 로 보호.
- backend handle 이 detach 와 race 하는 동안 isStreaming() 가 true 였다가 queueMessage 가 reject 하는 시나리오의 정확한 빈도.
- attempt.ts:2767 의 await activeSession.steer 가 abort race 시 어떤 종류 Error 를 throw 하는지 (transient class 인지) — infra/unhandled-rejections 의 classification 영향.
