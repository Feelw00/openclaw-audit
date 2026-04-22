---
id: FIND-gateway-concurrency-002
cell: gateway-concurrency
title: poll RPC 의 inflight map 부재로 idempotencyKey 중복 spawn
file: src/gateway/server-methods/send.ts
line_range: 589-598
evidence: "```ts\n    const idem = request.idempotencyKey;\n    const cached = context.dedupe.get(`poll:${idem}`);\n\
  \    if (cached) {\n      respond(cached.ok, cached.payload, cached.error, {\n \
  \       cached: true,\n      });\n      return;\n    }\n    const to = request.to.trim();\n\
  \    const resolvedChannel = await resolveRequestedChannel({\n```\n"
symptom_type: concurrency-race
problem: '`poll` RPC 는 `send` 와 달리 **inflight map 을 전혀 사용하지 않는다**.

  동일 idempotencyKey 로 두 번 들어온 poll 요청은 둘 다 `outbound.sendPoll`

  을 호출해 Slack/WhatsApp 등에 **중복 poll** 을 생성한다.

  '
mechanism: "1. T1: `context.dedupe.get('poll:X')` (L590, sync) → undefined.\n2. T1:\
  \ `await resolveRequestedChannel(...)` (L598) — microtask boundary.\n3. T2 (동일 idem\
  \ 재전송): handler 진입 → L590 synchronous 체크도 undefined\n   (T1 아직 dedupe 에 저장 안 함,\
  \ L676 에서만 저장됨).\n4. T1 과 T2 모두 L666 `outbound.sendPoll(...)` 실행 →\n   해당 채널에 **2개의\
  \ poll 이 생성됨**.\n5. `cacheGatewayDedupeSuccess` (L674) 는 각 성공 후에 호출되지만 이때는\n   이미\
  \ outbound 쪽에서 2개의 별개 poll ID 가 만들어진 뒤다.\n"
root_cause_chain:
- why: 왜 두 poll 요청이 동시에 sendPoll 을 호출하는가?
  because: L590 은 완료-dedupe cache 만 검사. inflight 요청 자체는 추적하지 않음. `send` / `message.action`
    핸들러에 있는 `resolveGatewayInflightMap`/`runGatewayInflightWork` 헬퍼가 poll 에는 적용되지
    않음.
  evidence_ref: src/gateway/server-methods/send.ts:589-598, 666-680
- why: 왜 send/poll 가 같은 패턴으로 통합되지 않았는가?
  because: 'upstream commit `60ec7ca0f1 refactor: share gateway send inflight handling`
    이 send/message.action 에만 inflight helper 를 도입. poll 은 legacy 코드로 남아 있음.'
  evidence_ref: git hash 60ec7ca0f1
- why: 왜 단일 thread JS 에서 race 가 성립하는가?
  because: L590 의 sync 체크 뒤 L598 의 `await resolveRequestedChannel` 이 microtask boundary
    를 만들고, 이후 `maybeResolveIdLikeTarget`/`sendPoll` 등 추가 await 이 다수. 두 번째 호출자가 해당
    window 에 동일 경로를 통과.
  evidence_ref: src/gateway/server-methods/send.ts:598, 666
impact_hypothesis: wrong-output
impact_detail: '정량: 재시도 기반 RPC 에서 동일 idempotencyKey 의 2회 도착이 drop-in 시

  duplicate poll 이 실제로 생성됨. `send` FIND-001 보다 race window 가 넓음

  (`resolveGatewayInflightMap` 같은 early-stage 가드가 없어 **재시도가

  겹치면 거의 100% 재현**).

  정성: poll 은 메시지보다 시각적 노이즈가 크고 제거하기 번거로움 (관리자가

  중복 poll 을 수동 삭제). 특히 그룹 챗에 poll 2개가 동시 등록되면 UX 교란.

  '
severity: P1
counter_evidence:
  path: src/gateway/server-methods/send.ts
  line: '676'
  reason: 'R-3 Grep 확인:

    - `rg -n "inflightMap|withSerialized|Mutex" src/gateway/server-methods/send.ts`
    → inflightMap 은 send/message.action 에만 존재, poll 미적용.

    - `rg -n "cacheGatewayDedupe" src/gateway/server-methods/send.ts` → L674 (poll
    성공) / L682 (poll 실패) 에서만 dedupe 저장. **실행 전 inflight lock 없음**.

    R-5 execution condition 분류:

    | 경로 | 조건 | 위치 |

    |---|---|---|

    | `context.dedupe.get(''poll:...'')` sync 체크 | unconditional | L590 |

    | `await resolveRequestedChannel` | unconditional | L598 |

    | `await outbound.sendPoll` | unconditional (정상 flow) | L666 |

    | `cacheGatewayDedupeSuccess` | unconditional (sendPoll 성공 후) | L674 |

    `cacheGatewayDedupeSuccess` 가 L666 의 await 뒤 (= 실제 플랫폼에 poll 생성된 뒤) 에만 호출되므로,
    race window 는 sendPoll 완료 직전까지 모두 포함.

    R-7 hot-path 재현: 프로덕션 `poll` RPC 는 항상 `resolveRequestedChannel` 을 await 하므로 test
    branch 와 production branch 가 동일. synthetic-only 아님.

    상위 연결 직렬화 부재: `message-handler.ts:1497` 의 `void (async IIFE)().catch` 가 per-connection
    직렬화 안 함.

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: '2026-04-22'
domain_notes_ref: domain-notes/gateway.md
cross_refs:
- FIND-gateway-concurrency-001
---
# poll RPC 는 inflight map 자체가 없어 idempotencyKey 중복 spawn 이 더 쉽게 발생

## 문제

`poll` RPC 핸들러 (`server-methods/send.ts:562-695`) 는 동일 `idempotencyKey`
로 동시에 도착한 두 요청 모두에 대해 플랫폼 API (`outbound.sendPoll`) 를
호출한다. 결과적으로 Slack/WhatsApp 등에 **같은 질문의 poll 이 2개**
생성된다. send/message.action 에 있는 inflight map 보호조차 poll 에는
없다.

## 발현 메커니즘

```
T1: handler entry
T1: sync L590 → dedupe.get('poll:X') → undefined
T1: await resolveRequestedChannel               [L598] ─ microtask + I/O
                T2: handler entry (retry, same idem)
                T2: sync L590 → dedupe.get('poll:X') → undefined (T1 미저장)
                T2: await resolveRequestedChannel
T1: await outbound.sendPoll(...)                [L666] → 플랫폼에 poll #1 생성
T1: cacheGatewayDedupeSuccess('poll:X', ...)    [L674]
                T2: await outbound.sendPoll(...) → 플랫폼에 poll #2 생성 ⚠
                T2: cacheGatewayDedupeSuccess   (덮어쓰기)
```

결과: 사용자가 동일 질문의 poll 을 2개 본다.

## 근본 원인 분석

1. **poll 경로만 inflight helper 미적용**. upstream refactor
   `60ec7ca0f1 refactor: share gateway send inflight handling` 이 `send` 와
   `message.action` 에 `resolveGatewayInflightMap`/`runGatewayInflightWork` 를
   도입했으나 poll 핸들러는 legacy 스타일 그대로.
2. **완료-dedupe cache 만으로는 불충분**. `cacheGatewayDedupeSuccess` 가
   호출되는 시점 (L674) 은 이미 `outbound.sendPoll` 이 완료된 뒤. 그 이전에
   concurrent caller 가 진입하면 둘 다 sendPoll 을 호출함.
3. **JS single-thread 에서도 race 성립**. microtask boundary 와 real I/O
   await 이 여럿 있어 체크와 저장이 atomic 하지 않음.

## 영향

- impact_hypothesis: wrong-output
- poll 중복 생성은 UX 교란 + 관리자 cleanup 부담. poll 은 일반 메시지보다
  시각적으로 크고, 2개가 공존하면 어느 쪽에 응답해야 할지 혼란.
- 재현: client retry (timeout → retransmit with same idempotencyKey) 는
  idempotencyKey 의 존재 근거이므로 흔함. FIND-001 보다 재현 난이도 낮음
  (L590 의 sync 체크만 있고 이후 모든 await 구간이 race window).

## 반증 탐색

**R-3 defense grep**:
- `rg -n "inflightMap" src/gateway/server-methods/send.ts` → send/message.action 섹션에만 존재, poll 섹션 (L562-L695) 에는 0 matches.
- `rg -n "Mutex|Semaphore|AsyncLock|withSerialized" src/gateway/server-methods/send.ts` → 0 matches.
- poll helper 경로 (`../../infra/outbound/`) 확인 — `deliverOutboundPayloads` 는 플랫폼별 dedupe 가 아니라 payload dispatch 함수.

**upstream 중복 fix 확인**: `git log upstream/main -- src/gateway/server-methods/send.ts` 에서 poll 관련 race fix 없음.

**기존 테스트**: `server-methods/send.ts` 의 테스트 커버리지 확인 — poll
handler 의 concurrent retry 시나리오 테스트 없음.

**execution 분류** (R-5): L590 의 sync 체크는 **unconditional**, 이어지는
`await resolveRequestedChannel` → `await outbound.sendPoll` 은 모두
**unconditional**. set (dedupe 저장) 은 L674 에서 execution 완료 후.
unconditional guard 가 부재하다.

## Self-check

### 내가 확실한 근거
- L590 의 sync 체크만 있고 이후 L598, L666 의 await 이 존재함을 코드로 확인.
- L674 의 dedupe 저장이 `sendPoll` 완료 이후임을 확인.
- send/message.action 과 달리 poll 에 inflight helper 가 적용되지 않았음을
  확인.

### 내가 한 가정
- `outbound.sendPoll` 플러그인이 플랫폼 레벨 poll 중복 제거를 수행하지 않음
  — Slack/WhatsApp 등 상당수 플랫폼은 별도 pollId 를 자동 부여하므로 중복
  생성이 실제 발생 가능.
- 재시도가 겹칠 확률은 클라이언트 retry 정책에 의존.

### 확인 안 한 것 중 영향 가능성
- 개별 outbound 플러그인 (각 채널) 내부의 idempotency 처리 여부.
- poll 요청 빈도 (실제 프로덕션 stats 없음) — 가능한 최악은 bot/integration
  이 대량 재시도할 때.
