---
id: FIND-channels-concurrency-001
cell: channels-concurrency
title: typing-lifecycle stop() 이 tickInFlight 강제 reset → restart 시 concurrent onTick
file: src/channels/typing-lifecycle.ts
line_range: 38-45
evidence: "```ts\n  const stop = () => {\n    if (!timer) {\n      return;\n    }\n\
  \    clearInterval(timer);\n    timer = undefined;\n    tickInFlight = false;\n\
  \  };\n```\n"
symptom_type: concurrency-race
problem: '`createTypingKeepaliveLoop` 의 `stop()` (L38-45) 은 in-flight tick 의 await
  완료 여부와 무관하게

  `tickInFlight = false` 를 강제로 reset 한다. 이후 `start()` 가 재호출되어 새 interval 이

  세팅되면, 이전 tick 의 `params.onTick()` 이 아직 await 중인 상태에서 새 tick 이

  `tickInFlight===false` 를 보고 두 번째 `params.onTick()` 을 동시 실행할 수 있다.

  production hot-path 는 `typing.ts:71-88 onReplyStart`: L77 `keepaliveLoop.stop()`
  직후

  L79 `fireStart()` → `.then(()=>{ keepaliveLoop.start(); })` (L80-86). 즉 stop-start

  시퀀스가 한 reply turn 안에서 발생.

  '
mechanism: "타임라인 (단일 Node.js 이벤트 루프, typing keepalive 가 활성 상태):\n\n1. T0: 이전 onReplyStart\
  \ 가 끝나면서 L84 `keepaliveLoop.start()` 호출됨. timer 가 세팅\n   되어 매 3000ms (default keepaliveIntervalMs)\
  \ 마다 `void tick()` 실행.\n2. T1: interval 첫 fire → tick A 실행:\n   - L18 tickInFlight=false\
  \ → 통과\n   - L21 tickInFlight=true\n   - L23 `await params.onTick()` — onTick 은\
  \ `fireStart` (typing.ts:47) →\n     `startGuard.run(() => params.start())` → 어댑터\
  \ platform API\n     (telegram sendChatAction / slack assistant.threads.setStatus\
  \ 등) 호출.\n     네트워크 지연이나 백오프로 awaiting 진행.\n3. T2: 새 inbound 메시지 도착 → 새 reply turn\
  \ → `onReplyStart` (typing.ts:71) 진입:\n   - L72 closed=false → 통과\n   - L75 stopSent=false\n\
  \   - L76 startGuard.reset() (consecutiveFailures=0, tripped=false)\n   - L77 `keepaliveLoop.stop()`\
  \ 실행:\n     - L39 timer 존재 → L42 clearInterval\n     - L43 timer=undefined\n   \
  \  - L44 **tickInFlight=false ← 여전히 awaiting 중인 tick A 의 플래그가 강제로 해제됨**\n   - L79\
  \ `const startPromise = fireStart()` — 직접 fireStart (B) 호출.\n     startGuard.run\
  \ → params.start() 두 번째 호출이 platform API 로 발사.\n   - L80-86: startPromise.then 안에서\
  \ `keepaliveLoop.start()` 가 큐에 등록.\n   - L87 await Promise.resolve() — onReplyStart\
  \ 가 미세하게 yield.\n4. T3: startPromise.then callback 실행 → L84 `keepaliveLoop.start()`:\n\
  \   - L30 timer===undefined → 통과, 새 setInterval 등록.\n5. T4: 새 interval 의 첫 fire\
  \ (다음 3000ms 후) → tick C:\n   - L18 tickInFlight=false (T2 에서 강제 reset 되었고 tick\
  \ A 의 finally 아직 미실행) → 통과\n   - L21 tickInFlight=true\n   - L23 `await params.onTick()`\
  \ — fireStart (C) → params.start() 세 번째 호출.\n6. T5: tick A 의 await 끝남 → finally\
  \ L25 tickInFlight=false 실행. (tick C 가 awaiting\n   이지만 그 플래그도 false 로 덮어쓰여 무용해짐.)\n\
  7. T6: 다음 interval fire → tick D:\n   - L18 tickInFlight=false → 통과 (C 가 awaiting\
  \ 중인데도 D 진입 허용)\n   - L21 tickInFlight=true, L23 await params.onTick() → params.start()\
  \ 네 번째.\n\n순서: A 와 C, C 와 D 등 onTick 두 개 이상이 동시 in-flight 상태가 됨. 본 가드의\n의도 (한 번에\
  \ 하나의 onTick) 위반.\n"
root_cause_chain:
- why: 왜 stop() 이 tickInFlight 를 forcibly false 로 만드는가?
  because: L44 `tickInFlight = false` 는 stop 호출 후 (start 가 다시 들어오기 전) 다음 tick 이 "초기
    상태" 로 보이도록 의도된 reset. 그러나 in-flight tick 의 finally (L25) 가 이미 동일 변수를 false 로 set
    할 예정이므로, stop 의 reset 은 sync 시점에서는 필요 없을 뿐 아니라 in-flight tick 의 ownership 을 가로채는
    부작용을 가진다.
  evidence_ref: src/channels/typing-lifecycle.ts:38-45
- why: 왜 새 tick 진입 시 "이전 tick 이 아직 in-flight" 임을 알 수 없는가?
  because: tickInFlight 는 boolean 1비트 가드일 뿐 ownership 토큰이 아님. stop 이 reset 한 후에는 "이전
    tick 이 끝났음" 과 "이전 tick 이 끝나지 않았으나 stop 으로 인해 cleared" 를 구분할 수 없다. tick 자체가 self-token
    을 들고 있다가 finally 에서 자기 토큰일 때만 reset 하는 패턴이 부재.
  evidence_ref: src/channels/typing-lifecycle.ts:17-27
- why: 왜 onReplyStart 가 stop→start 시퀀스를 사용하는가?
  because: typing.ts:77 `keepaliveLoop.stop()` 은 이전 turn 의 keepalive 가 새 turn 의 fireStart
    와 겹쳐 burst 를 만들지 않도록 의도된 reset. 그러나 stop 직후 L79 fireStart 와 L84 `keepaliveLoop.start()`
    로 같은 키에 대해 다시 가동시키는 구조이므로 "in-flight tick 이 stop 시점에 존재한다" 는 정상 시나리오.
  evidence_ref: src/channels/typing.ts:71-88
- why: 왜 이 race 가 production 에서 발현되는가?
  because: 본 race 의 발현 조건은 (1) keepalive 가 활성, (2) tick 의 fireStart 가 await 중 (platform
    API 지연/백오프), (3) 동일 turn 또는 다음 turn 의 onReplyStart 가 trigger. (1) 은 default keepaliveIntervalMs=3000ms
    이고 long-running reply (수 초~분) 에서는 항상 활성. (2) 는 telegram/slack rate-limit, 네트워크
    흔들림 등으로 자연 발생. (3) 은 user 의 빠른 follow-up 또는 multi-turn streaming.
  evidence_ref: src/channels/typing.ts:71-88
impact_hypothesis: wrong-output
impact_detail: '정성: 한 conversation 의 typing indicator 가 짧은 시간 안에 두세 번 platform API
  로

  burst 호출되는 결과. telegram sendChatAction, slack assistant.threads.setStatus,

  discord trigger-typing 모두 rate-limit 가 있어 burst 가 transient 실패를 유발할 수

  있다. typing-start-guard.ts:41 `consecutiveFailures += 1` 카운터가 두 concurrent

  run 의 catch 에서 동시 증분되면 race-of-counter 가 추가로 발생 (Math 단위 ++ 이지만

  두 await 가 동일 catch 경로를 지나면 둘 다 +1 → tripping 빨라짐).


  정량: race window = tick 의 await 시간 (platform API latency). telegram 일반 latency

  100-500ms, rate-limit 시 ~수 초. 이 window 동안 onReplyStart 가 트리거되면 발현.

  실제 영향: 단발성 burst 는 typing indicator 의 시각적 불일치 수준 (P3). 다만 본

  guard 가 fail counter 와 결합되면서 maxConsecutiveFailures=2 default (typing.ts:26)

  에서 두 race-failure 가 동시에 잡혀 tripped → `keepaliveLoop.stop()` 이 onTrip 콜백

  (typing.ts:36-38) 에서 실행됨 → 정상 turn 인데 keepalive 가 조기 종료될 수 있음.


  R-7 hot-path: production 경로는 typing.ts onReplyStart → fireStart + startPromise.then

  → keepaliveLoop.start. tick 의 onTick 은 fireStart 자체 (typing.ts:47). branch

  분기 없이 동일 함수 호출 — synthetic 가 아님.

  '
severity: P3
counter_evidence:
  path: src/channels/typing-lifecycle.ts
  line: 38-45
  reason: "R-3 / R-5 에 따른 방어 탐색:\n\n1) `rg -n \"Mutex|Semaphore|AsyncLock|\\.acquire\\\
    (|\\.release\\(\" src/channels src/routing`\n   → match 0건. 외부 락 없음.\n2) `rg -n\
    \ \"AbortController|AbortSignal|signal\\.(abort|addEventListener)\"\n   src/channels\
    \ src/routing` → keepalive 경로에 abort 가 통과하지 않음.\n   run-state-machine.ts / stall-watchdog.ts\
    \ 만 abort 통합, typing 계열은 closed\n   플래그로만 lifecycle 관리.\n3) `rg -n \"Promise\\\
    .race|Promise\\.all|Promise\\.allSettled\" src/channels src/routing`\n   → typing\
    \ 계열 0건. 본 경로에 race 동기화 부재.\n4) `rg -n \"\\.on\\(|\\.once\\(|prependListener|addEventListener\"\
    \ src/channels src/routing`\n   → typing 계열에는 event-based 직렬화 부재.\n5) `rg -n \"\
    setImmediate|queueMicrotask|process\\.nextTick\" src/channels src/routing`\n \
    \  → match 0건. microtask 직렬화 부재.\n\nR-5 실행 조건 분류 (tick / start / stop 경로):\n|\
    \ 경로 | 실행 조건 | guard 효과 |\n|---|---|---|\n| tick L18 tickInFlight check | unconditional\
    \ 매 tick fire | self-token 부재, stop 으로 우회 가능 |\n| tick L25 finally tickInFlight=false\
    \ | unconditional tick 종료 | 자기 소유 검증 없음 |\n| stop L44 tickInFlight=false | unconditional\
    \ stop 호출 | in-flight tick 의 ownership 침범 |\n| start L30 timer 검사 | unconditional\
    \ | double-start 방어 (대칭 정상) |\n\nprimary-path inversion 검토: 본 race 가 차단되려면 \"\
    tick A 의 finally 가 in-flight\n여부와 무관히 tickInFlight 를 자기 token 으로만 reset\" 하는 atomic\
    \ guard 가 필요.\n또는 stop() 이 await pending tick 을 기다리거나 tickInFlight 를 reset 하지\
    \ 말아야 함.\n현재 코드에는 양쪽 모두 부재 → race 재현 가능.\n\nR-7 hot-path 검증: production caller\
    \ (typing.ts:71-88 onReplyStart) 는 stop→start\n시퀀스를 매 reply turn 마다 통과. mock 으로\
    \ 다른 branch 를 강제할 필요 없음.\n\n예상되는 maintainer 반론:\n- \"tickInFlight 는 best-effort\
    \ 가드일 뿐 정확성 보장 안 함\" → 그렇다면 stop 의\n  L44 reset 의 의도가 무엇인지 주석 필요. 본 코드는 그 reset\
    \ 이 안전한 듯한\n  코드 구조이나 실제는 race 창문을 만든다.\n- \"platform API 가 idempotent 하므로 double-fire\
    \ 무해\" → typing API 자체는 보통\n  idempotent 이지만 rate-limit 카운터 / startGuard.consecutiveFailures\
    \ 가 정확\n  성 의존. P3 수준 영향 최소화는 인정.\n\n자체 한계:\n- typing-lifecycle 이 다른 keepalive\
    \ 용도로도 사용되는지 (out-of-scope) 미확인.\n  현재 production 호출처는 typing.ts 하나 (rg 확인).\n"
status: discovered
discovered_by: concurrency-auditor
discovered_at: 2026-05-14
cross_refs: []
domain_notes_ref: domain-notes/channels.md
related_tests:
- src/channels/typing.test.ts
rejected_reasons:
- 'B-1-2c: evidence mismatch at src/channels/typing-lifecycle.ts:38-45 — whitespace
  or content differs'
---
# typing-lifecycle stop() 이 tickInFlight 강제 reset → restart 시 concurrent onTick

## 문제

`createTypingKeepaliveLoop` 의 `stop()` (typing-lifecycle.ts L38-45) 은 `tickInFlight = false`
를 unconditional 하게 설정한다. 그러나 in-flight tick (이전 fire 의 `await params.onTick()`
이 아직 미완) 이 존재할 때 stop 이 호출되면, 그 in-flight tick 의 ownership 플래그가
강제로 false 가 되어버린다. 직후 `start()` 가 새 interval 을 등록하면 새 tick 이
"tickInFlight==false" 를 보고 두 번째 onTick 을 concurrent 로 실행한다. 본 가드의
single-in-flight 의도 위반.

## 발현 메커니즘

frontmatter `mechanism` 참조 (T0-T6 타임라인).

핵심은 stop→start sequence 가 production hot-path (typing.ts:71-88 `onReplyStart`)
의 매 reply turn 마다 발생하는데, stop 시점에 keepalive 가 활성이고 tick 의 fireStart
가 platform API await 중일 가능성이 높다는 점.

## 근본 원인 분석

1. **stop 의 sync reset**: L44 `tickInFlight = false` 는 stop 직후 또 다른 start 가
   호출될 때 다음 tick 이 안전하게 첫 in-flight 가 되도록 의도. 그러나 in-flight
   ownership 토큰이 boolean 한 비트라 stop 이 가로채면 이전 tick 이 finally 에서
   reset 한 것과 구분 불가.
2. **finally 의 무조건 reset**: L25 `tickInFlight = false` 도 자기 소유 검증 없이
   reset. tick 마다 `myTickId` 토큰을 들고 마지막에 `if (currentTickId===myTickId)
   tickInFlight=false` 처럼 자기 소유만 풀어야 race 차단.
3. **stop 이 pending await 을 기다리지 않음**: stop 은 sync 함수이므로 in-flight tick
   의 await 결말을 알 수 없음. AbortController 가 통과하지 않아 강제 cancel 도 불가.
4. **start 의 double-start 방어는 정상**: L30 `if (timer) return` 으로 중복 setInterval
   방지. 그러나 이는 timer 자체의 double-fire 만 막을 뿐 tick 들 간 in-flight
   overlap 은 해결 안 함.

## 영향

- **impact_hypothesis: wrong-output**.
- 한 conversation 의 typing indicator 가 짧은 시간 안에 두세 번 platform API 로 burst
  호출. telegram/slack/discord 각 rate-limit 정책에 따라 transient 실패 또는 throttling.
- typing-start-guard.ts:41 `consecutiveFailures += 1` 카운터가 concurrent run 의 catch
  경로에서 동시 증분 → maxConsecutiveFailures=2 default 도달 빠름 → onTrip 콜백 호출
  → `keepaliveLoop.stop()` 이 정상 turn 중간에 keepalive 를 끄게 됨. 사용자 관점에서
  typing indicator 가 reply 완료 전 사라지는 시각적 결함.
- 정량: race window = tick 의 await 시간 (platform API latency). 평균 100-500ms,
  rate-limit 시 수 초. 매 reply turn 의 onReplyStart 가 그 window 와 겹치면 발현.
- 데이터 손실 없음 (P0/P1 아님). 시각적 + counter-state 오염 수준 → P3.

## 반증 탐색

- **외부 lock**: `rg "Mutex|Semaphore|AsyncLock"` → 매치 0건.
- **AbortController**: typing 계열에서 abort 신호 미통과. run-state-machine.ts /
  stall-watchdog.ts 와 달리 typing-lifecycle 은 closed 플래그로만 lifecycle 관리.
- **Promise.race / serialization primitive**: 본 모듈에 없음.
- **microtask ordering**: queueMicrotask/setImmediate/process.nextTick 매치 0건.
- **identity check**: tick 의 finally 가 자기 토큰을 들고 그것만 reset 하는 패턴 부재.
- **primary-path inversion**: 본 race 차단을 위해 (a) tick self-token + (b) stop 의
  tickInFlight reset 제거 또는 (c) start 가 in-flight tick 의 await 완료 후 시작,
  중 하나가 있어야 함. 셋 다 부재.
- **테스트 커버**: typing.test.ts 는 stop→start 시퀀스를 mock 으로 검증하나 platform
  API await 중에 stop 이 들어오는 시나리오는 다루지 않음.

## Self-check

### 내가 확실한 근거
- typing-lifecycle.ts:44 stop 이 tickInFlight=false 를 unconditional reset.
- typing-lifecycle.ts:17-27 tick 이 finally 에서 self-token 검증 없이 reset.
- typing.ts:77-86 onReplyStart 가 stop→fireStart→start 시퀀스를 매 reply turn 실행.
- typing.ts:47 onTick=fireStart 로 platform API 호출 경로.
- R-3 grep 5종 매치 0건 (lock/abort/race/listener-sync/microtask).

### 내가 한 가정
- platform API latency 가 stop→start 시퀀스 사이 (sync 영역) 보다 길다고 가정.
  매우 빠른 mock 환경 (debounce=0, 즉시 resolve) 에서는 자연 발생 빈도 낮음.
- typing-start-guard 의 consecutiveFailures 가 race-counter 결함 영향을 받는다고
  가정. 두 concurrent run 이 동일 try/catch 경로 통과 시 일반적인 JS race 행동.
- onReplyStart 가 user-facing reply turn 마다 한 번 호출된다고 가정.

### 확인 안 한 것 중 영향 가능성
- typing-lifecycle 의 다른 사용처 (extension code 등 out-of-scope) 가 stop→start
  시퀀스를 더 자주 또는 더 드물게 호출하는지.
- platform 별 typing API 실제 rate-limit (telegram 50ms throttle? slack 10/s?) 와
  burst 영향 정량 미실측.
- typing-start-guard.consecutiveFailures 의 ++ 가 정확한 race-counter 문제로
  나타나는지 빈도 측정 데이터 없음 (이론상 두 catch path 가 동시이면 +1 두 번
  은 V8 single-thread 라 정확하지만 두 별개의 catch 가 동시 진입 시 다른 path 의
  reset (L38 consecutiveFailures=0) 와 interleave 가능).
