---
id: FIND-channels-error-boundary-001
cell: channels-error-boundary
title: draft-stream-loop void flush() 가 send throw 시 pendingText 유실 + unhandled
file: src/channels/draft-stream-loop.ts
line_range: 54-78
evidence: "```ts\n  const schedule = () => {\n    if (timer) {\n      return;\n  \
  \  }\n    const delay = Math.max(0, params.throttleMs - (Date.now() - lastSentAt));\n\
  \    timer = setTimeout(() => {\n      void flush();\n    }, delay);\n  };\n\n \
  \ return {\n    update: (text: string) => {\n      if (params.isStopped()) {\n \
  \       return;\n      }\n      pendingText = text;\n      if (inFlightPromise)\
  \ {\n        schedule();\n        return;\n      }\n      if (!timer && Date.now()\
  \ - lastSentAt >= params.throttleMs) {\n        void flush();\n        return;\n\
  \      }\n      schedule();\n```\n"
symptom_type: error-boundary-gap
problem: createDraftStreamLoop 의 update / schedule 경로가 `void flush()` 만 호출하고 .catch
  를 붙이지 않아, sendOrEditStreamMessage 가 throw 하면 flush 가 reject 되며 (1) pendingText 가
  이미 비워진 상태라 해당 draft 조각이 영구 유실되고 (2) 프로세스 수준 unhandledRejection 이 발생한다.
mechanism: '1. agent streaming reply 가 chunk 단위로 `loop.update(text)` 호출 (auto-reply/reply
  draft 경로).

  2. throttle 조건 충족 시 line 74-76 의 `void flush()` 또는 line 59-61 의 `setTimeout(() =>
  void flush(), delay)` 가 flush 비동기 호출.

  3. flush 내부 line 35 에서 `pendingText = ""` 로 reset 후 line 36 `params.sendOrEditStreamMessage(text)`
  호출.

  4. 어댑터가 HTTP 429 / network error / TypeError 등으로 throw → flush 가 reject.

  5. 호출 측은 `void` 로 swallow — `.catch` 없음. pendingText 는 이미 "" 라 다음 update 까지 text
  조각 복구 불가 (line 42 의 `sent === false` 분기는 throw 경로에 도달하지 못함).

  6. node process 전체에서 `process.on("unhandledRejection")` handler (src/infra/unhandled-rejections.ts:345)
  가 수신 — transient network 오류는 warn 만 찍지만, non-transient (TypeError, RangeError, Discord
  API permission error 등) 은 `exitWithTerminalRestore` 로 process.exit(1) 까지 갈 수 있음.

  7. 결과: 사용자에게 "streaming 되던 답변 일부가 사라지고 (wrong-output), 드물게 프로세스가 재시작된다".

  '
root_cause_chain:
- why: 왜 flush 호출 시 .catch 를 붙이지 않았는가?
  because: 함수가 "throttle 기반 best-effort 전송" 으로 설계되어 reject 를 silently drop 하는 것이 당연하다고
    가정. 실제로 flush 내부의 `.finally` (line 36-40) 는 inFlightPromise 정리만 담당하고 reject 자체는
    처리하지 않는다.
  evidence_ref: src/channels/draft-stream-loop.ts:36-50
- why: 왜 pendingText 를 line 35 에서 즉시 비우는가 (retry 이전에)?
  because: 동시 update() 가 도착해도 lost-update 방지 목적 — `const text = pendingText; pendingText
    = "";` 는 sendOrEdit 가 resolve 된 경우 (line 43 `sent === false`) 만 `pendingText =
    text;` 로 복구. throw 경로는 catch 가 없어 복구 skip.
  evidence_ref: src/channels/draft-stream-loop.ts:30-45
- why: 왜 sendOrEditStreamMessage 가 throw 하는가 (assumption 의 반증)?
  because: 팩토리 createFinalizableDraftStreamControls (draft-stream-controls.ts:38-44)
    는 caller (plugin SDK) 가 전달한 sendOrEditStreamMessage 를 그대로 사용. 각 채널 plugin (telegram/discord/slack
    등) 의 editMessage / sendMessage 는 네트워크 오류나 rate-limit (HTTP 429, Discord 50013,
    Telegram 429) 에서 throw 하는 것이 표준. openclaw 내부에서 throw-safe wrap 강제 계약 없음.
  evidence_ref: src/channels/draft-stream-controls.ts:32-56
impact_hypothesis: wrong-output
impact_detail: '정량 난이: streaming draft reply 중 channel adapter 가 transient network
  error 를 낼 때마다 해당 throttle window 의 draft 조각이 유실된다. chunk 빈도 ~수초 간격, 네트워크 불안정 구간에서
  사용자에게 "답변이 중간에 끊긴 상태로 남아 있는" 화면 재현. 추가 unhandledRejection 로그가 infra/unhandled-rejections.ts
  경로로 들어감 — transient code (ECONNRESET/UND_ERR_* 등) 는 warn 처리로 crash 회피, 그러나 논리 오류
  (TypeError 등) 는 process.exit(1) 가능.'
severity: P2
counter_evidence:
  path: src/channels/draft-stream-controls.ts
  line: 53-68
  reason: 'R-3 Grep 결과:

    - `rg -n "void\s+flush\(\)" src/channels/draft-stream-loop.ts` — line 60, 75 두
    건 모두 .catch 없음.

    - `rg -n "\.catch\(" src/channels/draft-stream-loop.ts` — 0 hits.

    - `rg -n "process\.on\([''\"]unhandledRejection" src/` — infra/unhandled-rejections.ts:345
    만 설치. transient network 코드는 warn 후 continue, 그 외는 `exitWithTerminalRestore`.

    방어 경로:

    - draft-stream-controls.ts:53-56 의 `stop()` 는 `await loop.flush()` 로 await → reject
    propagate, caller 에서 catch 가능 (정상 경로).

    - stop/seal 경로 (line 64-68) 는 `loop.stop(); await loop.waitForInFlight();` — await
    으로 handled.

    - 단 line 60, 75 의 `void flush()` 는 일반 update 경로 (스트리밍 hot path) 라 여기만 미방어.

    R-5 execution condition:

    | 경로 | 조건 | 비고 |

    |---|---|---|

    | line 60 setTimeout(void flush) | conditional-edge | throttle window 시작 + update()
    중 |

    | line 75 update(void flush) | conditional-edge | inFlightPromise 없음 + lastSentAt
    경과 |

    | line 42 `sent === false` 복구 | conditional-edge | resolve 경로 only — throw 시 unreachable
    |

    | stop()/seal() await flush | unconditional | final flush — reject propagate OK
    |

    Primary-path inversion: update() → void flush() 경로는 streaming reply 의 hot path.
    adapter throw 가 빈도 낮다는 counter-argument 가능하지만, channel plugin 별로 rate-limit/timeout
    은 production 에서 관측되는 class — severity P2 유지.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-22'
---
# draft-stream-loop void flush() 가 send throw 시 pendingText 유실 + unhandled

## 문제

`createDraftStreamLoop` 는 agent streaming reply 를 channel adapter 로 throttled 전송하는 helper. 내부 `flush()` 는 `pendingText` 를 한 번에 비운 뒤 `sendOrEditStreamMessage(text)` 를 await 한다. update/schedule 경로는 이 flush 를 `void flush()` 로만 호출하고 `.catch()` 를 붙이지 않는다. 어댑터가 throw 하면:
1. flush 가 reject → 호출측 swallow (no catch).
2. pendingText 는 이미 비워진 상태라 해당 draft 조각 영구 유실.
3. process-level `unhandledRejection` handler 가 로그 기록 + 케이스에 따라 process.exit(1).

## 발현 메커니즘

1. agent streaming reply 에서 chunk 마다 `loop.update(text)` 호출.
2. (a) `!timer && Date.now() - lastSentAt >= throttleMs` 조건이면 line 74-76 즉시 `void flush()`, (b) 아니면 line 54-62 schedule → setTimeout → `void flush()`.
3. flush 진입 시 (line 30) `const text = pendingText; pendingText = "";` — text 복사 후 reset.
4. (line 36) `params.sendOrEditStreamMessage(text)` 가 throw — 예: Telegram `429 Too Many Requests`, Discord `ECONNRESET`, Slack `invalid_auth`, runtime `TypeError` (malformed payload).
5. await 실패로 flush 가 reject. line 42-46 의 `sent === false` 복구는 resolve 경로 전용 → 도달 못 함.
6. caller `void flush()` → rejection swallow. node 가 `unhandledRejection` 이벤트 발행.
7. src/infra/unhandled-rejections.ts:345 handler 가 수신:
   - transient network 코드 (ECONNRESET/EAI_AGAIN/UND_ERR_CONNECT/...): warn 후 continue.
   - non-transient (TypeError, custom Error 등): `exitWithTerminalRestore("unhandled rejection")` → process.exit(1).
8. 사용자 관점: 스트리밍 답변 일부 증발 (transient) 또는 프로세스 재시작 (non-transient).

## 근본 원인 분석

draft-stream-loop 는 "throttle 기반 best-effort" 로 설계되었지만 pendingText reset 전략 때문에 best-effort 와 reject 이 양립 불가. flush 의 `.finally` (line 36-40) 는 inFlightPromise 정리만 담당하고 reject 는 그대로 propagate. update/schedule 경로가 reject 를 잡지 않는 것은 설계 누락.

channel plugin (telegram/discord/slack) 의 editMessage/sendMessage 는 네트워크 오류와 rate-limit 시 throw 하는 것이 표준 — openclaw 가 plugin-sdk 경계에서 throw-safe wrap 을 강제하지 않는다. 따라서 flush 호출 측이 책임져야 한다.

## 영향

- 영향 유형: wrong-output (draft 조각 유실) + 드물게 crash (non-transient).
- 관측 가능성: unhandled rejection log 는 남지만, "어떤 draft 가 유실됐는가" 는 로그 없음 (draft-stream-loop 가 payload 를 자체 log 하지 않음).
- 재현:
  - Telegram bot 에서 editMessage 를 rate-limit 넘도록 연속 호출 → 429 → throw.
  - test harness 에서 `sendOrEditStreamMessage: () => { throw new Error("boom"); }` stub 주입 후 `loop.update("...")` 호출 → 즉시 재현.
- 심각도: P2. streaming reply 는 hot-path. transient 네트워크 오류는 실제 관측되는 빈도 높음.

## 반증 탐색

R-3 Grep:
- `rg -n "void\s+flush\(\)" src/channels/draft-stream-loop.ts` — line 60, 75. 둘 다 .catch 없음.
- `rg -n "\.catch\(" src/channels/draft-stream-loop.ts` — 0 hits.
- `rg -n "process\.on\(['\"]unhandledRejection" src/` — infra/unhandled-rejections.ts:345 유일. transient warn-only, non-transient crash.
- draft-stream-controls.ts:53-68 의 stop/seal 은 `await loop.flush()` / `await loop.waitForInFlight()` 로 정상 await, reject propagate — hot-path 와 무관한 경로.

R-5 execution condition:
| 경로 | 조건 | 비고 |
|---|---|---|
| line 60 setTimeout(() => void flush()) | conditional-edge | throttle window 시작 + update 대기 |
| line 75 if-branch void flush() | conditional-edge | inFlightPromise 없음 + lastSentAt 경과 |
| line 42-46 `sent === false` pendingText 복구 | conditional-edge | resolve 경로 only |
| stop/seal await flush | unconditional | final flush 경로 — reject caller propagate |

unconditional 방어는 final-stop 경로 뿐. update() 스트리밍 경로에는 방어 없음.

Primary-path inversion: "adapter 가 거의 throw 하지 않는다" counter-argument 가능. 그러나 Telegram/Slack/Discord 모두 rate-limit 429 를 throw 로 신호하는 것이 표준이고 (plugin-sdk 어댑터 구현 다수 관례), transient network 오류는 관측되는 일반 시나리오. 따라서 P2 유지.

기존 테스트 커버리지: `src/channels/draft-stream-controls.test.ts` 는 reject 경로를 test 하지만 (confirmed by filename only; 이 draft 는 hot-path 누수 자체가 대상), production loop 의 `void flush()` 경로는 명시적 reject test 없음.

## Self-check

### 내가 확실한 근거
- line 60, 75 가 `void flush()` 로만 호출, .catch 없음 — Read 로 확인.
- flush 내부 `pendingText = ""` (line 35) 가 send 이전 수행, resolve 경로만 line 44 `pendingText = text` 복구.
- infra/unhandled-rejections.ts:345 가 process-wide handler — transient warn, non-transient process.exit(1).
- stop/seal 은 await flush 로 방어. update/schedule 은 방어 없음.

### 내가 한 가정
- production 에서 channel adapter editMessage/sendMessage 가 실제 throw 경로를 타는 빈도 — "rate-limit 은 흔하다" 는 일반 관례. 정량 지표 없음.
- non-transient 오류 (TypeError 등) 가 flush 내부에서 발생할 빈도 — payload mismatch / malformed response 의 희귀 케이스.

### 확인 안 한 것 중 영향 가능성
- plugin-sdk 측 reply-payload.ts 등이 한 층 위에서 throw 를 wrap 하는지 전수 확인 안 함. 만약 wrap 하면 flush 의 send throw 자체가 이론적이 됨 (severity P3 하향 필요).
- draft-stream-controls.test.ts 에 reject stub 경로가 이미 있는지 확인 안 함 — 있을 경우 "known" 문제일 수 있음.
- `sent === false` (line 43) 를 반환하는 어댑터가 실제로 존재하는지 — 존재하면 그 경로는 정상 복구. throw 경로만 문제.
