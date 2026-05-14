---
id: FIND-channels-concurrency-002
cell: channels-concurrency
title: MessageReceiveContext.ack() check-then-await-then-set race → onAck 중복 발사
file: src/channels/message/receive.ts
line_range: 69-82
evidence: "```ts\n    shouldAckAfter: (stage) => shouldAckMessageAfterStage(ctx.ackPolicy,\
  \ stage),\n    ack: async () => {\n      if (ctx.ackState === \"acked\") {\n   \
  \     return;\n      }\n      await params.onAck?.();\n      ctx.ackState = \"acked\"\
  ;\n      ctx.ackedAt = Date.now();\n      delete ctx.nackErrorMessage;\n    },\n\
  \    nack: async (error) => {\n      await params.onNack?.(error);\n      ctx.ackState\
  \ = \"nacked\";\n      ctx.nackErrorMessage = normalizeAckErrorMessage(error);\n\
  \    },\n```\n"
symptom_type: concurrency-race
problem: '`createMessageReceiveContext` 가 반환하는 `ctx.ack()` 는 `ctx.ackState === "acked"`

  check 후 `await params.onAck?.()` 를 거쳐 비로소 `ctx.ackState = "acked"` 를 set 한다.

  check 와 set 사이에 await 가 있어 두 caller 가 동일 ctx 의 `.ack()` 를 동시에 호출

  하면 양쪽 모두 L70 check 를 통과 (state="pending"), 양쪽 모두 `await params.onAck()`

  를 실행한다. 결과: platform 의 ack 콜백이 두 번 발사되어 동일 메시지에 대해 두 번

  ack 가 plaform 으로 송신될 수 있음.

  '
mechanism: "타임라인 (단일 Node.js 이벤트 루프, 동일 message 의 ctx 가 두 dispatch 경로에서\n공유될 때):\n\
  \n1. T0: receive context 가 만들어지고 두 경로로 전달됨 (예: per-stage ack 경로와\n   manual fallback\
  \ 경로, 또는 retry/recovery 경로).\n2. T1: 경로 A 가 `ctx.ack()` 호출 → L70 check: ackState==\"\
  pending\", 통과 →\n   L73 `await params.onAck?.()` — onAck 가 platform API (telegram\
  \ setReceived,\n   slack ack-response, discord interaction-respond, whatsapp markRead)\
  \ 으로 발사.\n   await 진행 중.\n3. T2: 경로 B 가 `ctx.ack()` 호출 (await 사이의 microtask 또는 다른\
  \ await 경계에서):\n   - L70 check: ackState 가 아직 \"pending\" (경로 A 가 L74 set 전).\n\
  \   - L73 `await params.onAck?.()` — **두 번째 platform API 호출이 발사됨**.\n4. T3: 경로 A\
  \ 의 await 종료 → L74 ackState=\"acked\", L75 ackedAt set.\n5. T4: 경로 B 의 await 종료\
  \ → L74 ackState=\"acked\" (이미 그러함), L75 ackedAt 갱신.\n\n결과: platform 에 같은 messageId\
  \ 에 대해 ack 가 두 번 송신. 일부 platform 은 두\n번째 ack 를 무시 (idempotent) 하지만 일부 (slack 의 response_url\
  \ POST, discord\ninteraction 응답) 는 \"already acknowledged\" 오류 또는 webhook 응답 형식\
  \ 오염을\n유발할 수 있다.\n"
root_cause_chain:
- why: 왜 check-then-set 사이에 await 가 있는가?
  because: 'L73 `await params.onAck?.()` 가 platform-side 의 실제 ack 전송 결과를

    기다리도록 의도. 그러나 가드 변수 (ackState) 의 set 이 await 이후 (L74) 에 있어

    ''이미 ack 중'' 임을 다른 호출자에게 알릴 방법이 없음. atomic test-and-set 부재.

    '
  evidence_ref: src/channels/message/receive.ts:69-77
- why: 왜 concurrent 호출이 가능한 production 시나리오가 있는가?
  because: 'ack 정책 (after_receive_record / after_agent_dispatch / after_durable_send)

    별로 다른 stage 에서 ack 가 호출됨. 만약 동일 ctx 가 두 stage 의 fan-out

    경로 (예: agent dispatch 와 durable send 가 fan-in 으로 같은 ack 를 트리거)

    또는 retry 와 normal path 의 fan-in 으로 공유되면 concurrent 호출 발생.

    또한 manual ack 경로와 stage-driven ack 경로가 동시에 활성화될 수 있는

    fallback 코드도 위험.

    '
  evidence_ref: src/channels/message/receive.ts:27-42
- why: 왜 기존 테스트가 race 를 포착 못하는가?
  because: 'lifecycle.test.ts:298-299 는 `await ctx.ack(); await ctx.ack();` 로

    sequential 호출만 검증 (onAck 1회 호출 확인). concurrent 호출 (Promise.all 기반)

    검증 부재.

    '
  evidence_ref: src/channels/message/lifecycle.test.ts:282-309
- why: '왜 in-flight token (예: pendingAckPromise) 방어 패턴을 안 썼는가?'
  because: '본 구현은 ackState 1비트 (pending/acked/nacked) 만 사용. ''ack in-flight''

    상태가 별도 token 으로 표현되지 않아 두 번째 호출이 첫 호출의 결과를 기다리거

    나 noop 할 수 없음.

    '
  evidence_ref: src/channels/message/receive.ts:69-77
impact_hypothesis: wrong-output
impact_detail: "정성: platform 별 ack idempotency 차이로 인한 영향:\n- telegram: setMessageReaction\
  \ 류는 보통 idempotent → 영향 없음.\n- slack: response_url POST 또는 chat.postMessage ack\
  \ reply 는 idempotent 아님.\n  두 번째 호출 시 \"operation_already_completed\" 또는 응답 형식 오류\
  \ 가능.\n- discord: interaction respond 는 첫 응답만 유효. 두 번째 호출 시 InteractionAlreadyAcknowledged.\n\
  - whatsapp: markRead 류는 idempotent 이지만 webhook ack 응답은 단일 ack 만 허용.\n- 일반 message-queue\
  \ 어댑터 (Kafka/SQS commit) 의 경우 double-commit 시 offset\n  가드에 따라 noop 또는 오류.\n\n정량:\
  \ race window = `params.onAck?.()` 의 await 시간. 일반 platform API 100ms 수준,\nrate-limit\
  \ 시 수 초. 재현 조건 = 같은 ctx 의 .ack() concurrent 호출. 본 ctx\nshape 가 plugin SDK (channel-message.ts:45)\
  \ 로 export 되어 4 adapter 의 plugin\n코드가 직접 호출. 다중 stage 정책 (`ackPolicy: \"after_receive_record\"\
  ` vs\n`\"after_agent_dispatch\"` vs `\"after_durable_send\"`) 이 의도와 다른 caller flow\n\
  로 fan-in 될 가능성 존재.\n\nR-7 hot-path: production caller 는 `extensions/**` (plugin-sdk\
  \ 통해) 와 본 코드\n내부에서 직접 호출됨. allowed_paths 외 이므로 4 adapter 의 실제 사용 패턴\n(single-caller\
  \ vs multi-caller fan-in) 검증 불가 — **본 P3 severity 의 주요\n근거**. concurrent 호출 시나리오가\
  \ 실제 production 에서 빈번한지 측정 데이터\n부재.\n"
severity: P3
counter_evidence:
  path: src/channels/message/receive.ts
  line: 69-82
  reason: "R-3 / R-5 에 따른 방어 탐색:\n\n1) `rg -n \"Mutex|Semaphore|AsyncLock|\\.acquire\\\
    (|\\.release\\(\" src/channels src/routing`\n   → match 0건. 외부 락 없음.\n2) `rg -n\
    \ \"AbortController|AbortSignal|signal\\.(abort|addEventListener)\"\n   src/channels\
    \ src/routing` → ctx.signal 은 입력으로 받으나 ack/nack 자체는\n   signal 통합 없음. abort 시\
    \ ack/nack 가 cancel 되는 경로 부재.\n3) `rg -n \"Promise\\.race|Promise\\.all|Promise\\\
    .allSettled\" src/channels src/routing`\n   → message/state.ts 의 Promise.all 들은\
    \ multi-identifier normalize 용,\n   ack/nack 직렬화에 사용 안 됨.\n4) `rg -n \"in-flight|pending.*Ack|ackPromise\"\
    \ src/channels` → match 0건. ack\n   in-flight token 부재 확인.\n5) `rg -n \"queueMicrotask|setImmediate|process\\\
    .nextTick\" src/channels src/routing`\n   → match 0건. microtask 직렬화 부재.\n\nR-5\
    \ 실행 조건 분류:\n| 경로 | 실행 조건 | guard 효과 |\n|---|---|---|\n| ack L70 ackState check\
    \ | unconditional 매 호출 | check-then-set, await 가 사이에 있어 race |\n| ack L74 ackState\
    \ set | unconditional await 완료 후 | \"이미 acked\" 식별 가능하나 too late |\n| nack L80\
    \ ackState set | unconditional await 완료 후 | nack 도 동일 패턴이나 nack 는 멱등 안 함 (overwrite)\
    \ |\n\nprimary-path inversion: 본 race 차단을 위해 (a) ack 시작 시점에 sync 로 ackState\n\
    를 \"acking\" 으로 set 하고 finally 에서 \"acked\" 또는 \"pending\" 복귀, 또는 (b)\npendingAckPromise\
    \ = onAck() 형태로 in-flight promise 를 캐시하여 두 번째\n호출이 동일 promise 를 await, 둘 중 하나가\
    \ 필요. 양쪽 모두 부재.\n\nR-7 hot-path 검증 — 한계:\n- 본 allowed_paths (src/channels/**,\
    \ src/routing/**) 내 production caller 는\n  lifecycle.test.ts 만 존재 (rg -n \"createMessageReceiveContext|ctx\\\
    .ack\\(\\)\"\n  src/channels src/routing → 모두 test 또는 정의).\n- 실제 4 adapter (telegram/slack/discord/whatsapp)\
    \ 의 사용 패턴은\n  `src/channels/extensions/**` 또는 plugin SDK 소비자에서 발생 — out-of-scope.\n\
    - 같은 ctx 를 두 경로가 공유하는지 (manual + stage-driven fan-in 등) 확인 불가.\n- **이로 인해 본 FIND\
    \ 는 borderline P3** — clusterer/gatekeeper 가 실제 사용\n  패턴 확인 후 abandon 결정 가능.\n\
    \n예상되는 maintainer 반론:\n- \"동일 ctx 에 ack 를 두 번 호출하는 caller 가 없으므로 race 무의미\" →\
    \ 그 가정이\n  ctx shape 의 정확성 보장 (예: 첫 호출 후 ctx 가 sealed) 으로 명시되어 있지\n  않음. 본 ctx\
    \ 가 plugin SDK 로 export 되어 외부 adapter 가 자유롭게 호출.\n- \"platform 어댑터가 자체 idempotency\
    \ 가짐\" → telegram 은 그러하나 slack/discord\n  interaction 류는 그렇지 않음.\n\nupstream 검사\
    \ (CAL-008):\n`git log upstream/main --since=\"6 weeks ago\" -- src/channels/message/receive.ts`\n\
    → 본 파일 직접 수정 0건. message/* 디렉터리 commit 은 send/types 위주.\nparallel work 위험 낮음.\n"
status: rejected
discovered_by: concurrency-auditor
discovered_at: 2026-05-14
cross_refs: []
domain_notes_ref: domain-notes/channels.md
related_tests:
- src/channels/message/lifecycle.test.ts
rejected_reasons:
- 'B-1-2c: evidence mismatch at src/channels/message/receive.ts:69-82 — whitespace
  or content differs'
---
# MessageReceiveContext.ack() check-then-await-then-set race → onAck 중복 발사

## 문제

`createMessageReceiveContext` 가 반환하는 ctx 의 `ack()` 메서드 (receive.ts:69-77) 는
다음 구조이다:

```
if (ctx.ackState === "acked") return;
await params.onAck?.();
ctx.ackState = "acked";
```

check (L70) 와 set (L74) 사이에 `await` (L73) 가 있어 두 호출자가 동시에 ack 를 호출
하면 양쪽 모두 check 를 통과한 뒤 둘 다 `params.onAck()` 를 await 한다. 결과: 동일
메시지에 대해 platform-side ack callback 이 두 번 실행됨.

## 발현 메커니즘

frontmatter `mechanism` 참조. 핵심은 single-thread JS 의 microtask 경계에서 두 caller
가 check 를 통과한 직후 둘 다 awaiter 가 되어 platform onAck 가 두 번 발사된다는 점.

## 근본 원인 분석

1. **check-then-set with await between**: 가장 흔한 race 안티패턴. test-and-set 이
   atomic 이 아니라 check 와 set 사이에 await 가 끼어 있어 사이에 진입한 호출자가
   같은 check 결과를 사용.
2. **In-flight token 부재**: ack 가 시작된 사실을 표현하는 별도 변수가 없음. 만약
   `acking` 또는 `pendingAckPromise` 토큰이 있다면 두 번째 호출이 같은 promise 를
   재사용하거나 noop 가능.
3. **AbortSignal 통합 부재**: ctx.signal 이 존재하지만 ack/nack 가 signal 을 check
   하거나 signal.abort 시 cancel 되는 경로 부재. 결과적으로 ack 의 fan-out 을
   조정할 외부 신호도 없음.
4. **테스트 누락**: lifecycle.test.ts:298-299 는 sequential 호출만 검증. concurrent
   호출 (`Promise.all([ctx.ack(), ctx.ack()])`) 검증 없음.

## 영향

- **impact_hypothesis: wrong-output**.
- platform-별 영향:
  - slack/discord 의 ack 류 API 는 idempotency 보장 안 함 → 두 번째 호출 시 오류 또는
    응답 형식 깨짐.
  - telegram/whatsapp 의 ack/markRead 류는 보통 idempotent → 영향 최소.
- 본 ctx 가 plugin SDK (channel-message.ts:45) 로 4 adapter 에 export → 사용 패턴
  광범위. concurrent 호출이 실재하면 영향 부정적.
- 정량 한계: production caller 추적이 allowed_paths 외 (extensions/**) → 실제 빈도
  측정 불가. **borderline P3** 로 분류.

## 반증 탐색

- **외부 lock / Abort / Promise.race / microtask**: R-3 grep 5종 모두 본 race
  차단 메커니즘 부재 확인 (frontmatter counter_evidence 참조).
- **In-flight token 검색**: `rg "in-flight|pending.*Ack|ackPromise" src/channels`
  매치 0건.
- **primary-path inversion**: 본 race 가 발현되려면 (a) sync 시점에 "acking" 표시
  하는 가드, 또는 (b) pendingAckPromise 캐싱 후 재사용 패턴이 있어야 함. 양쪽 부재.
- **R-7 hot-path 한계**: 실제 production caller 가 src/channels/extensions/** 또는
  plugin SDK 소비자에 있어 allowed_paths 외. 따라서 4 adapter 중 어느 것이 concurrent
  ack 를 호출하는지 본 셀에서 확정 불가.
- **테스트 커버**: lifecycle.test.ts 에 sequential 두 번 호출만 있음 (test 의도가
  idempotency 검증이라면 concurrent 도 검증해야 함).
- **upstream 검사**: receive.ts 최근 6주 수정 없음 (CAL-008).

## Self-check

### 내가 확실한 근거
- receive.ts:69-77 의 check-await-set 구조가 명백한 check-then-act race anti-pattern.
- in-flight token 부재 확인 (rg 매치 0건).
- lifecycle.test.ts 의 ack 테스트가 sequential 만 다룸.
- ctx 가 plugin SDK 통해 export 됨 (channel-message.ts:45 확인).

### 내가 한 가정
- 어떤 adapter (특히 slack/discord) 가 production 에서 동일 ctx 의 .ack() 를 concurrent
  으로 호출하는 시나리오가 있다고 가정. 실제 verification 은 allowed_paths 외.
  P3 severity 의 핵심 근거.
- platform 별 ack API idempotency 차이를 가정 (slack/discord 는 idempotent 아님).
- ctx 가 두 경로로 공유되는 fan-in 시나리오가 가능하다고 가정.

### 확인 안 한 것 중 영향 가능성
- src/channels/extensions/** 의 4 adapter 코드에서 ack 호출이 single-path 로만
  이루어지는지 (이라면 본 race 가 영원히 발현 안 함 → abandon 대상).
- ackState 가 외부에서 변경되는 경로 (만약 있다면 race 변수 추가).
- nack() 도 동일 check 없이 매번 set 하므로 ack 와 nack 가 동시 호출되면 마지막
  set 이 이김 — 본 FIND 범위 외이지만 동일 디자인 결함의 연장.
