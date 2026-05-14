---
id: FIND-auto-reply-lifecycle-002
cell: auto-reply-lifecycle
title: inbound debouncer drops buffered inbound on shutdown — timer unref + no flushAll
file: src/auto-reply/inbound-debounce.ts
line_range: 173-181
evidence: "```ts\n  const scheduleFlush = (key: string, buffer: DebounceBuffer<T>)\
  \ => {\n    if (buffer.timeout) {\n      clearTimeout(buffer.timeout);\n    }\n\
  \    buffer.timeout = setTimeout(async () => {\n      await flushBuffer(key, buffer);\n\
  \    }, buffer.debounceMs);\n    buffer.timeout.unref?.();\n  };\n```\n"
symptom_type: lifecycle-gap
problem: '''`createInboundDebouncer` 가 setTimeout 으로 flush 를 예약한 뒤 `buffer.timeout.unref?.()`

  (L180) 로 timer 를 event-loop 의 alive 카운터에서 제외시킨다. unref() 효과: process 가

  shutdown (SIGTERM, 정상 exit, gateway restart) 으로 event-loop 가 비기 시작하면 timer 가

  발화하지 않은 채 process 종료. buffered items 은 메모리에서 사라진다.


  또한 반환 객체 (L265 `return { enqueue, flushKey }`) 에 `flushAll` / `dispose` / `keys()`

  같은 graceful drain API 가 없어, caller (gateway / channel inbound handler) 가 shutdown

  시점에 모든 pending key 를 flush 하려 해도 enumerate 할 방법이 없다. inbound webhook 이

  platform 에 이미 200 OK 로 응답했다면 (정상 패턴) 메시지는 사용자 측에서 "전송됨" 상태로

  보이지만 실제로는 모델에 도달하지 않고 silent drop 된다.''

  '
mechanism: "'1. inbound webhook (Telegram/Slack/Discord) 도착 → channel layer 가 `createChannelInboundDebouncer`\n\
  \   로 생성된 debouncer 의 `enqueue(item)` 호출 (channels/inbound-debounce-policy.ts:47).\n\
  2. enqueue path (L190-263): canDebounce && key && !existing 케이스 (L230-263) →\n \
  \  `scheduleFlush(key, buffer)` 호출 → setTimeout(..., debounceMs) + `unref()`.\n\
  3. channel 이 platform 에 200 OK 응답 — 메시지는 platform 측에서 acknowledged.\n4. 그 시점 gateway\
  \ 가 SIGTERM 수신 또는 정상 종료 시작. event-loop 의 다른 작업이\n   완료되면 unref 된 timer 만 남는데, unref()\
  \ 때문에 event-loop 가 그 timer 를\n   기다리지 않음 → process exit.\n5. timer 의 callback 은\
  \ fire 되지 않음. `flushBuffer` 가 호출되지 않음. `params.onFlush(items)`\n   도 호출되지 않음. items\
  \ (buffer.items) 는 메모리에서 사라짐.\n6. 다음 process 시작 시 platform 은 이미 acknowledged 한 메시지를\
  \ 재전송하지 않음 →\n   **사용자 시점에서 silent drop**.\n\nshutdown 측 graceful drain 도 부재:\n\
  7. createInboundDebouncer 반환 객체에 `flushAll` / `dispose` / `keys()` API 없음\n   (`return\
  \ { enqueue, flushKey }`, L265). caller 가 shutdown 시 모든 pending key 를\n   enumerate\
  \ 하여 flush 하려 해도 방법 없음.\n8. `process.on(\"SIGTERM\", ...)` hook 에서 debouncer.flush*()\
  \ 류 호출 부재 (`rg -n\n   \"flushKey|drainDebouncer\" src/cli src/gateway src/channels`\
  \ → 0 건).'\n"
root_cause_chain:
- why: 왜 `unref()` 가 사용되었나?
  because: long-running gateway process 에서 debounce timer 가 event-loop 를 인위적으로 alive
    상태로 유지하지 않도록 — 다른 작업이 없으면 process 가 자연 종료될 수 있게 하려는 의도. test 환경 / CLI 단발 실행에서
    timer 가 process 종료를 막지 않게 하는 표준 패턴 (timer.unref).
  evidence_ref: src/auto-reply/inbound-debounce.ts:180
- why: 왜 unref 된 timer 가 shutdown 시 buffered items 손실을 유발하는가?
  because: Node 의 `Timeout.unref()` 는 event-loop 의 alive counter 에서 timer 를 제외시킴.
    process 가 graceful exit 으로 들어가 event-loop 가 비기 시작하면 unref 된 timer 의 callback 은
    발화 못 함. buffered items 은 closure 안 buffer.items 에 살아있지만 flush 가 일어나지 않으므로 process
    종료와 함께 사라짐. timer 가 ref 되어 있었으면 event-loop 가 timer 까지 기다림.
  evidence_ref: src/auto-reply/inbound-debounce.ts:177-180
- why: 왜 graceful drain API 가 없는가?
  because: 반환 객체는 `{ enqueue, flushKey }` (L265) 만 노출. flushKey 는 특정 key 만 flush 가능
    — 모든 key 를 한번에 drain 할 API 부재. `buffers` Map 은 closure private 이라 caller 가 enumerate
    할 방법 없음. 의도된 API 표면이 "enqueue + 개별 flush 트리거" 에 한정되어 shutdown drain 시나리오는 design
    space 에서 누락.
  evidence_ref: src/auto-reply/inbound-debounce.ts:265
- why: 왜 platform 측 retry 로 회복되지 않나?
  because: inbound webhook (Telegram bot api, Slack events api 등) 은 server 가 200 OK
    응답을 보내면 platform 이 정상 처리로 간주하여 재전송 안 함. debouncer 의 enqueue 는 "성공적으로 buffered"
    상태이므로 caller 가 platform 에 200 응답하기 좋은 시점이며, 실제로 channel inbound handler 패턴이 그러하다.
    즉 user 의 메시지는 platform side 에서 delivered 로 기록되지만 실제 처리는 없는 silent drop 상태가 된다.
  evidence_ref: N/A — Telegram/Slack inbound 의 ack 패턴은 외부 spec
- why: 왜 production hot-path 에서 실제로 트리거되나?
  because: 'gateway 는 SIGTERM 으로 빈번하게 재시작된다 (deploy, config reload, supervisor health
    check). debounce window 는 default config 에서 수십~수백 ms 이고, 그 window 안에 SIGTERM 이
    도착하면 buffered 메시지가 손실. 빠른 user input (예: 사용자가 1-2 문장을 빠르게 보낸 직후 deploy 가 발생) 의
    확률은 deploy 빈도 × debounce window 비율.'
  evidence_ref: src/cli/gateway-cli/run-loop.ts:512 (gateway SIGTERM 처리)
impact_hypothesis: data-loss
impact_detail: '''정성: inbound 메시지의 silent drop. user 가 plaformm 측 UI 에서는 메시지가 "보내짐"

  상태로 보이지만 실제로 gateway 측 모델 호출이 일어나지 않는다. 정량 추정: gateway

  shutdown 빈도 (deploy / restart / supervisor cycle) × debounce 활성 비율 × debounce

  window 비율. 메시지 비율은 작지만 (debounce window 50-500ms) 정확한 timing 일치 시

  100% 손실. 사용자 체감: "보냈는데 답이 없네" — debug 도 어려움 (로그 측 enqueue 흔적은

  있지만 onFlush 호출 흔적이 없음). 추가 위험: inbound 메시지가 "trigger" 였다면 user 가

  의존하는 후속 액션 (예: alert 또는 reminder) 도 함께 누락. 재현 조건: process 가 빨리

  종료하면서 (다른 작업이 없으면 unref 효과 더 큼) debounce timer 가 발화 못 할 때.''

  '
severity: P3
counter_evidence:
  path: src/auto-reply/inbound-debounce.ts
  line: 165-171
  reason: "'`flushKey(key)` (L165-171) 는 caller 가 특정 key 의 buffer 를 즉시 flush 할 수 있게\n\
    하는 API. 그러나 (a) caller 가 어떤 key 가 buffered 인지 enumerate 할 방법 없음\n(`buffers` Map\
    \ 미노출), (b) shutdown 시 모든 key 를 drain 할 graceful 경로 부재. 따라서\nflushKey 는 본 lifecycle\
    \ gap 의 mitigant 가 못 됨.\n\n확인한 반증 카테고리:\n(1) 숨은 방어 / defense-in-depth:\n    `rg\
    \ -n \"flushKey|flushAll|drainDebouncer\" src/cli src/gateway src/channels --type=ts\n\
    \     -g \"!*.test.ts\"` → 0 건. gateway SIGTERM handler (run-loop.ts:512) 가 debouncer\n\
    \    flush 호출 안 함.\n    `rg -n \"buffers\\.|buffers\\.keys\" src/auto-reply/inbound-debounce.ts`\
    \ → 내부 사용만,\n    외부 노출 없음.\n(2) Telegram/Slack inbound 의 platform retry 로 보호되는지:\
    \ webhook spec 상 200 OK 가\n    ack 이므로 server-side drop 은 retry 안 됨. polling 모드라도\
    \ once consumed (offset\n    advanced) 면 동일.\n(3) 기존 테스트 커버리지:\n    `src/auto-reply/inbound.test.ts`\
    \ 의 setTimeout/clearTimeout spy 테스트는 timer 동작\n    시뮬레이션만, process exit 시나리오 0\
    \ 건.\n(4) Primary-path inversion (CAL-001): graceful drain 경로 = **부재**. unref\
    \ 가 의도된\n    설계지만 drain API 가 동반되지 않은 비대칭.\n(5) Hot-path-vs-test-path (CAL-003):\
    \ production hot-path = gateway deploy cycle.\n    test 는 동일 process 내 timer 호출만\
    \ — 재현 테스트 필요.\n(6) Upstream-dup (CAL-004/008):\n    `git log upstream/main --since=\"\
    6 weeks ago\" -- src/auto-reply/inbound-debounce.ts`\n    → f5ebe63ecd (preserve\
    \ debounce ordering, 2026-05-14), df27091f5f (avoid leaking\n    inbound debounce\
    \ cleanup, 2026-04-13). 두 commit 모두 in-process ordering / leak\n    fix 축이고 shutdown\
    \ drain 축 0 건.\n    `gh pr list --state open --search \"inbound-debounce graceful\
    \ shutdown in:title,body\"`\n    → 0 건.\n\n설계 의도 측면 — unref 자체는 합리적 (test/cli\
    \ 에서 process hang 방지). 그러나 drain\nAPI 비대칭이 본 gap 의 근원. severity P3 (위생 수준) 으로\
    \ 보수 평가.'\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
---
# inbound debouncer drops buffered inbound on shutdown — timer unref + no flushAll

## 문제

`createInboundDebouncer` 는 setTimeout 으로 debounce flush 를 예약하고 즉시 `buffer.timeout.unref?.()` 로 timer 를 event-loop 의 alive 카운터에서 제외시킨다 (inbound-debounce.ts:177-180). Node 의 `Timer.unref()` 는 graceful exit 시점에 event-loop 가 비기 시작하면 timer callback 을 발화하지 않고 process 가 종료한다는 의미.

반환 API (L265 `return { enqueue, flushKey }`) 에 `flushAll` / `dispose` / `keys()` 가 없어 shutdown handler 가 모든 buffered key 를 drain 할 수 없다 — `buffers` Map 은 closure private. 그 결과 SIGTERM / 정상 exit / gateway restart 시점에 debounce window 안에 buffered 된 inbound 메시지는 모델로 도달하지 못한 채 silent drop.

inbound 메시지의 platform-side ack (webhook 200 OK 응답) 은 이미 send 시점에 발신되므로, 메시지가 사실상 처리되지 않은 데도 platform 은 정상 delivered 로 기록한다 → retry 없음. 사용자 시점: "보냈는데 답이 없네".

## 발현 메커니즘

```
T0  channel inbound webhook 도착 → channel handler 가 debouncer.enqueue(item) 호출.
T1  enqueue path (L230-263): canDebounce + new key →
      buffer = {items:[item], timeout:null, debounceMs:50ms, ...};
      buffers.set(key, buffer);
      scheduleFlush(key, buffer):
        buffer.timeout = setTimeout(async () => { await flushBuffer(key,buffer); }, 50);
        buffer.timeout.unref?.();          ← event-loop alive 에서 제외
T2  channel handler 가 platform 에 200 OK 응답 (정상 패턴).
T3  process 가 SIGTERM 수신 또는 다른 in-flight 작업이 완료 → event-loop drain.
T4  유일하게 남은 작업은 unref 된 timer → event-loop 가 기다리지 않음 → process exit.
T5  timer callback 발화 안 함 → flushBuffer 미호출 → params.onFlush(items) 미호출.
T6  process 재시작 후 platform 은 메시지를 재전송하지 않음 (이미 ack).
    → 사용자 메시지가 사라짐.
```

graceful drain 경로:

| 면 | 현재 상태 |
|---|---|
| 반환 API | `{ enqueue, flushKey }` 만. `flushAll` 없음 |
| buffers Map | closure private. caller 가 enumerate 불가 |
| SIGTERM handler 의 drain | `rg -n "flushKey" src/cli src/gateway src/channels` → 0 건 |
| timer 의 ref 상태 | unref → event-loop 가 기다리지 않음 |

## 근본 원인 분석

1. **`unref()` 의도와 drain API 의 비대칭**: unref 는 test/cli/단발 실행에서 timer 가 process 종료를 막지 않도록 하는 표준 패턴. 그러나 production gateway 처럼 long-running process 에서 graceful shutdown 시 buffered items 의 drain 이 필요하면 unref 단독으로는 부족하고 flushAll/dispose 등의 graceful-drain API 가 동반돼야 한다. 본 API 는 그 절반만 구현.

2. **closure-private buffers**: `buffers: Map<string, DebounceBuffer<T>>` (L59) 는 외부에서 enumerate 불가. caller 가 어떤 key 가 buffered 인지 모름 → flushKey 를 모든 key 에 호출하는 것도 불가능.

3. **caller 측 SIGTERM hook 부재**: gateway / channel 의 SIGTERM handler (gateway-cli/run-loop.ts:512 등) 가 debouncer flush 를 호출하지 않음. 더 나아가 호출하고 싶어도 API 부재 (위 2 번).

4. **platform ack 의 비가역성**: inbound webhook 의 200 OK 가 이미 보내졌으므로 platform side 의 retry 정책이 작동하지 않는다. 즉 silent drop 이 사용자 측에서 회복 불가능.

## 영향

- **사용자 체감**: "보냈는데 답이 없네". debounce window 안에 정확히 SIGTERM 이 도착해야 발현하므로 빈도는 낮지만, deploy / config reload / supervisor restart 가 잦은 환경에서 누적.
- **노출 경로**: production gateway 가 inbound 채널을 받는 모든 케이스. inbound-debounce-policy.ts 가 사용되는 채널 = Telegram, Slack, Discord 등 webhook 기반 채널.
- **빈도 추정**: deploy 빈도 × debounce window (50-500ms) × inbound rate. CI/CD 가 잦은 환경에서 매 deploy 마다 1 건 이하지만 결정적 (timing 일치 시 100% 손실).
- **debug 난이도**: enqueue 흔적은 로그에 있지만 onFlush 호출 흔적이 없음 — incident 추적 어려움.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "flushKey|flushAll|drainDebouncer" src/cli src/gateway src/channels --type=ts -g '!*.test.ts'` → 0 건. graceful shutdown 경로에서 debouncer 호출 없음.
- `rg -n "buffers\." src/auto-reply/inbound-debounce.ts` → 모두 closure 내 사용. 외부 노출 0.
- gateway SIGTERM handler (`src/cli/gateway-cli/run-loop.ts:512`) 가 debouncer 인스턴스를 enumerate 할 hook 없음.

### 기존 테스트 커버리지

- `src/auto-reply/inbound.test.ts` 의 setTimeout/clearTimeout spy 테스트는 timer fire 시뮬레이션만 — process exit 시나리오 0 건.
- df27091f5f (2026-04-13 fix: avoid leaking inbound debounce cleanup) 와 f5ebe63ecd (2026-05-14 fix: preserve debounce ordering) 의 추가 테스트는 in-process ordering / cleanup 만 다룸.

### 호출 빈도 / 경로 활성 여부

- inbound-debounce-policy.ts:47 의 `createChannelInboundDebouncer` 는 channels 의 inbound handler 가 생성. Telegram/Slack/Discord 등 모든 inbound 채널이 잠재적 노출.
- gateway deploy 는 production CI/CD 에서 정규 경로. SIGTERM 직후 in-flight 가 없는 시점에 buffered 만 남았을 때 trigger.

### Primary-path inversion (CAL-001)

graceful drain 경로 = **부재**. unref 자체는 의도된 design 이지만, drain API 가 동반되지 않은 점이 본 gap. defensive 누락 (false positive 함정 아님).

### Hot-path-vs-test-path consistency (CAL-003)

production hot-path = gateway deploy cycle + 빠른 inbound. test 는 in-process timer 만 → 재현 테스트는 child process spawn + SIGTERM 송신 패턴 필요.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/auto-reply/inbound-debounce.ts` → 2 건:
  - `f5ebe63ecd` fix: preserve debounce ordering (2026-05-14)
  - `df27091f5f` fix: avoid leaking inbound debounce cleanup (2026-04-13)

  shutdown drain 축 commit 0 건.
- `gh pr list --state open --search "inbound-debounce graceful shutdown in:title,body"` → 0 건.

## Self-check

### 내가 확실한 근거

- L177-180 의 `setTimeout(...).unref?.()` 호출 실재 (Read 확인).
- L265 의 반환 API 가 `{ enqueue, flushKey }` 뿐 (Read 확인).
- gateway SIGTERM hook (`src/cli/gateway-cli/run-loop.ts:512` 등) 이 debouncer flush 를 호출하지 않음 (Bash rg 확인).
- Node 의 `Timeout.unref()` 의 의미는 정확히 "이 timer 가 event-loop alive 에 기여하지 않음" — node 공식 spec.

### 내가 한 가정

- inbound webhook (Telegram/Slack) 이 200 OK 시점에 platform 측에서 ack 처리한다는 가정 — 외부 spec 에 의존. 실제로는 platform 별로 retry 정책 다름 (e.g. Slack 은 3 sec timeout 후 retry).
- gateway 가 SIGTERM 직후 quick exit 한다는 가정 — gateway 의 정확한 shutdown ordering 미확인. graceful shutdown 이 `await pending` 패턴을 가지면 timer 가 발화할 시간 확보 가능. 그러나 unref 때문에 event-loop 가 그 timer 를 기다리지 않으므로 다른 작업이 없으면 즉시 exit.
- debounce window 의 default 값 (50-500ms 추정) — config 에서 override 가능.

### 확인 안 한 것 중 영향 가능성

- gateway 의 graceful shutdown sequencer 가 debouncer 외 다른 in-flight 작업을 await 하는 동안 timer 가 자연 발화할 가능성 — 만약 그렇다면 본 gap 의 실제 빈도는 낮아짐. 그러나 unref 의 정의상 event-loop 가 빈 순간이 발생하면 즉시 exit 이므로 단지 다른 작업의 timing 에 의존하는 race.
- inbound 채널별 retry 정책 — Slack 의 3 sec timeout retry 는 본 gap 을 부분적으로 mask 할 수 있음. Telegram 의 long polling 은 offset advance 시 retry 없음.
- df27091f5f / f5ebe63ecd 후속으로 동일 영역 추가 fix 가 진행 중인지 — `gh pr list` 로 OPEN PR 0 건 확인했지만 비공개 / 작업 중일 가능성.
