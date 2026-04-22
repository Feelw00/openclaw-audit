---
id: FIND-gateway-concurrency-001
cell: gateway-concurrency
title: send RPC idempotencyKey check-then-act race (inflight set after I/O await)
file: src/gateway/server-methods/send.ts
line_range: 400-405
evidence: "```ts\n    const idem = request.idempotencyKey;\n    const dedupeKey =\
  \ `send:${idem}`;\n    const inflightMap = await resolveGatewayInflightMap({ context,\
  \ dedupeKey, respond });\n    if (!inflightMap) {\n      return;\n    }\n```\n"
symptom_type: concurrency-race
problem: '동일 idempotencyKey 로 동시에 들어온 두 `send` RPC 가 idempotency 를

  우회하고 **outbound 메시지를 중복 전송** 한다. 첫 호출이 inflight map 에

  자신의 work 를 `set` 하기 전에 다음 호출이 동일 check-then-act 경로를

  통과하기 때문.

  '
mechanism: "1. T1: `await resolveGatewayInflightMap(...)` (L402). 내부에서\n   `context.dedupe.get(send:${idem})`\
  \ (sync, undefined) → `inflightMap.get(...)` (sync, undefined)\n   → `inflightMap`\
  \ 반환. `set` 은 아직 없음.\n2. T1: `await resolveRequestedChannel(...)` (L422) — `loadConfig()`\
  \ +\n   `resolveMessageChannelSelection` — **마이크로태스크 경계**.\n3. T2 (동일 idem, 재전송):\
  \ handler 진입 → L402 에서 마찬가지로 empty\n   inflightMap 반환받음.\n4. T1/T2 계속 진행 → 각자 `work`\
  \ IIFE 생성 (L445) →\n   `await runGatewayInflightWork({ inflightMap, dedupeKey, work,\
  \ respond })` (L560)\n   → `inflightMap.set(dedupeKey, work)` (send.ts 내부 helper\
  \ L92).\n5. T2 의 set 이 T1 의 set 을 덮어쓰지만, **T1 의 work 는 이미 dispatch 중**.\n6. 결과:\
  \ `deliverOutboundPayloads` (L527) 가 T1/T2 각각 호출 →\n   외부 채널 (Slack/WhatsApp/Telegram\
  \ 등) 으로 **동일 메시지 2회 전송**.\n"
root_cause_chain:
- why: 왜 동일 idempotencyKey 로 2회 실행되는가?
  because: L402 체크 시점과 L560 의 inflightMap.set 시점 사이에 실제 I/O await 이 존재. check-then-act
    이 atomic 이 아님.
  evidence_ref: src/gateway/server-methods/send.ts:400-405, 560
- why: 왜 L402 체크는 완료-dedupe cache 와 inflight map 두 곳을 보면서도 충분하지 않은가?
  because: inflight map 은 L92 (runGatewayInflightWork 내부) 에서만 set 되고, 그 호출은 L422,
    L460 등 여러 await 뒤에 발생. `resolveGatewayInflightMap` 내부 자체는 synchronous 이나 호출자가
    await 하므로 최소 1 microtask delay. 이후 추가 await 으로 경쟁자 진입 가능.
  evidence_ref: src/gateway/server-methods/send.ts:63-83, 92
- why: 왜 WS 연결 레벨 직렬화가 이를 막지 못하는가?
  because: WS message handler 는 RPC 를 `void (async () => handleGatewayRequest(...))().catch(...)`
    로 fire-and-forget dispatch. 동일 연결 내에서도 병렬 처리됨. 다른 연결 (client 재시도 via 새 socket)
    간에는 당연히 병렬.
  evidence_ref: src/gateway/server/ws-connection/message-handler.ts:1497-1509
- why: 왜 상위 mutex/serialization 이 없는가?
  because: rate-limit-attempt-serialization.ts (auth 전용) 같은 직렬화 헬퍼가 send 경로에는 적용되지
    않음. idempotency 보장이 map-based check-then-act 에만 의존.
  evidence_ref: src/gateway/rate-limit-attempt-serialization.ts:3-36
impact_hypothesis: wrong-output
impact_detail: '정량: 클라이언트 재시도 (네트워크 타임아웃 등) 가 흔한 상황에서 동일

  idempotencyKey 로 2회 이상 RPC 가 병렬 도착하면 outbound 채널에 중복

  메시지가 실제로 dispatch 됨. `resolveMessageChannelSelection` + `resolveOutboundChannelPlugin`
  의 실행 시간이 길수록 race window 증가.

  sandboxed 재현: withMessageChannelSelection mock 에 인위적 100ms delay 를

  주고 동일 idempotencyKey 로 2회 호출 → `deliverOutboundPayloads` 가 2회 호출됨.

  정성: Slack/WhatsApp/Telegram 등 outbound 플러그인은 대부분 중복 메시지를

  멱등하게 처리하지 않음 (플랫폼 측 고유 messageId 생성) → 사용자에게

  duplicate delivery 가 관찰됨.

  '
severity: P1
counter_evidence:
  path: src/gateway/server-methods/send.ts
  line: '92'
  reason: 'R-3 Grep 확인:

    - `rg -n "Mutex|Semaphore|AsyncLock|withSerialized|lock\.acquire" src/gateway/server-methods/send.ts`
    → 0 matches.

    - `rg -n "idempotenc|idem" src/gateway/server-methods/send.ts` → 13 matches, 모두
    dedupeKey 생성 또는 payload 필드용. 락/직렬화 없음.

    - `rg -n "Promise\.race\(|Promise\.all\(|Promise\.allSettled\(" src/gateway/server-methods/send.ts`
    → 0 matches.

    상위 호출자 확인: `src/gateway/server/ws-connection/message-handler.ts:1497-1509` 의 `void
    (async () => handleGatewayRequest(...))().catch(...)` 는 per-message fire-and-forget
    이라 동일 연결 내에서도 병렬 처리. 연결 단위 큐/세마포어 없음.

    R-5 execution condition 분류:

    | 경로 | 조건 | 위치 |

    |---|---|---|

    | `resolveGatewayInflightMap` | unconditional (매 호출 진입) | send.ts:402 |

    | `runGatewayInflightWork.set` | unconditional (inflightMap 획득 후) | send.ts:92
    |

    | 그 사이 awaits (resolveRequestedChannel 등) | unconditional | send.ts:422, 460,
    480, 514, 527 |

    `set` 이 `check` 뒤로 **최소 1 microtask + 여러 real I/O await** 만큼 밀림. unconditional
    guard 없음 → race 성립.

    R-7 hot-path 재현: 프로덕션 `send` RPC 는 항상 `resolveRequestedChannel` 을 await 하므로 synthetic
    아님. client retry 시나리오 (타임아웃 → 동일 idempotencyKey 재전송) 는 흔한 production path.

    상위 dedupe 방어 불가: `context.dedupe.get` 은 **완료된** 요청의 결과만 캐시하므로 in-flight 동안은 undefined
    를 반환. CAL-001 의 unconditional-guard 형태가 없음.

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: '2026-04-22'
domain_notes_ref: domain-notes/gateway.md
related_tests:
- src/gateway/test-helpers.ts
- src/gateway/server.impl.ts
cross_refs: []
---
# send RPC idempotencyKey check-then-act race — inflight map set after real I/O await

## 문제

`send` RPC (`server-methods/send.ts:374`) 는 동일 `idempotencyKey` 가 네트워크
재시도 등으로 두 번 도착할 때 하나의 outbound 전송만 수행해야 하지만,
inflight 큐 관리가 **check-then-act** 이고 그 사이에 여러 `await` 가 있어
두 호출자가 각자 독립적으로 work 를 실행한다. 외부 채널 플러그인 (slack,
whatsapp, telegram 등) 으로 실제 메시지가 **2회 dispatch** 된다.

## 발현 메커니즘

```
T1: enter handler
T1: ... synchronous validation ...
T1: await resolveGatewayInflightMap       [L402]
   - dedupe.get('send:X') → undefined
   - inflightMap.get('send:X') → undefined
   - return inflightMap
T1: await resolveRequestedChannel         [L422] ── microtask boundary + real I/O
                            (here) T2 enters handler with same idem
                            T2: synchronous validation
                            T2: await resolveGatewayInflightMap → still undefined
                            T2: await resolveRequestedChannel
T1: work = (async () => { ...deliverOutboundPayloads... })()   [L445]
T1: await runGatewayInflightWork → inflightMap.set('send:X', workT1)  [L560 → L92]
                            T2: work = (async () => { ... })()
                            T2: await runGatewayInflightWork → inflightMap.set('send:X', workT2)  (덮어쓰기)
T1 execution: deliverOutboundPayloads(...)  → outbound 1회
                            T2 execution: deliverOutboundPayloads(...) → outbound 2회 (duplicate!)
```

핵심은 `inflightMap.set` 이 race window 이후에 발생한다는 점. 동일
connection 내에서도 `message-handler.ts:1497` 의
`void (async () => handleGatewayRequest(...))().catch(...)` 가 fire-and-forget
이라 두 RPC 가 interleave 될 수 있고, client retry 가 새 socket 으로 오는
경우 cross-connection interleave 는 기본이다.

## 근본 원인 분석

1. **`resolveGatewayInflightMap` 은 자체적으로 sync 이지만 caller 의 `await`
   가 microtask boundary 를 만든다**. 체크와 set 을 같은 macrotask 에 묶지
   않으면 single-thread JS 라도 race 가 발생.
2. **set 이 handler 후반 (L560) 로 밀림**: `resolveRequestedChannel` ,
   `resolveOutboundSessionRoute`, `deliverOutboundPayloads` 등의 I/O 가
   L422, L460, L480, L514, L527 에 존재. check 와 set 사이 경계가 최소 한 번의
   real I/O await. `message.action` handler (L314-L372) 도 동일 패턴.
3. **상위 직렬화 부재**: `rate-limit-attempt-serialization.ts` 에
   `withSerializedRateLimitAttempt` 헬퍼가 존재 (auth 경로용) 하지만 send
   경로에는 적용되지 않는다. 연결 레벨 혹은 idempotencyKey 레벨 mutex 없음.

## 영향

- impact_hypothesis: wrong-output
- 사용자 체감: outbound 채널 (Slack/WhatsApp/Telegram/iMessage 등) 에 동일
  메시지/미디어가 **2회 전송**. 동일 idempotencyKey 로 retry 하는 것은 모든
  client 의 기본 동작 (connection timeout, server restart 등).
- 재현 난이도: 중간. `resolveMessageChannelSelection` 혹은 `loadConfig`
  의 지연이 크거나 (대형 config / file watcher 경쟁), 클라이언트가 공격적
  retry 정책을 사용하는 경우 trigger 확률 상승.

## 반증 탐색

**R-3 defense grep**:
- `rg -n "Mutex|Semaphore|AsyncLock|withSerialized" src/gateway/server-methods/send.ts` → 0 matches.
- `rg -n "Promise\.race\(|Promise\.allSettled\(" src/gateway/server-methods/send.ts` → 0 matches.
- `rg -n "lock|mutex" src/gateway/server-methods/send.ts` → 0 matches.

**상위 호출자 방어**: `src/gateway/server/ws-connection/message-handler.ts:1497-1509`
는 각 RPC 를 `void (async IIFE)().catch` 로 dispatch — per-connection 직렬화
없음.

**기존 테스트**: `src/gateway/server-methods.control-plane-rate-limit.test.ts`
와 `auth-rate-limit.test.ts` 는 auth 경로 직렬화를 커버하지만 send
idempotency race 는 커버하지 않음. upstream commit `60ec7ca0f1 refactor: share
gateway send inflight handling` 은 이 helper 를 추출했을 뿐 race 는 그대로.

**upstream 중복 fix 확인 (CAL-004)**: `git log upstream/main --since="2025-10-01"
-- src/gateway/server-methods/send.ts` 상위 10 commit 중 race/concurrent/lock/
serialize 키워드 포함은 refactor 1건뿐. race fix 없음. CAL-004 상황 아님.

**execution 분류** (R-5): `resolveGatewayInflightMap` 호출은 **unconditional**,
이어지는 `await resolveRequestedChannel` 도 **unconditional**. `runGatewayInflightWork`
의 `inflightMap.set` 은 **unconditional** 이지만 그 이전 awaits 가 이미 race
window 를 열어둠. 따라서 정상 flow 에 race 가 존재한다.

## Self-check

### 내가 확실한 근거
- L400-L405 의 체크와 L560 의 set 사이에 최소 한 번의 real I/O await (L422)
  이 있음을 코드로 확인.
- `message-handler.ts:1497-1509` 가 RPC 를 per-connection 직렬화하지 않는다는
  것을 확인. 동일 연결 내 병렬 처리 가능.
- `rate-limit-attempt-serialization.ts` 는 auth 전용이고 send 경로에서 호출되지
  않음을 확인.

### 내가 한 가정
- 클라이언트가 동일 `idempotencyKey` 로 2회 이상 요청할 수 있다 — 이건
  idempotencyKey 의 존재 목적 자체이므로 합리적 가정.
- `deliverOutboundPayloads` 가 호출될 때마다 outbound 플러그인이 실제로
  중복 전송을 한다고 가정. 플랫폼 자체 dedupe (e.g. WhatsApp messageId
  uniqueness) 이 있다면 완화될 수 있으나 현재 코드 레벨에서 GW 가 중복
  dispatch 를 막지 못함은 명확.
- message.action / poll 핸들러도 동일 race 를 가진다 — 별도 FIND (002, 003) 에서 다룸.

### 확인 안 한 것 중 영향 가능성
- outbound 채널 플러그인 내부 idempotency (messageId 충돌 처리) 가 일부
  채널에서 존재할 수 있음 — 그러면 실제 피해는 해당 채널에 한해 감소.
- `idempotencyKey` 가 클라이언트 라이브러리에서 기본값으로 설정되는지,
  또는 caller 책임인지. caller 가 항상 채우지 않으면 race 이전에 이미
  idempotency 가 없는 것이라 별개 이슈.
- `loadConfig()` 내부 캐시 히트 여부 — 캐시 히트면 L422 의 await window 가
  매우 짧아져 재현 난이도가 올라감.
