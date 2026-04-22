---
candidate_id: CAND-021
type: single
finding_ids:
  - FIND-gateway-concurrency-001
cluster_rationale: |
  단독 FIND. 같은 셀(gateway-concurrency)의 FIND-002/003 과 "gateway RPC
  idempotency check-then-act race" 라는 상위 테마를 공유하지만 R-5 분류 /
  파일·라인·guard 설계 / fix 축 모두 독립.

  FIND-001 (send/message.action) R-5 분류:
    "unconditional race in primary dispatch" — `resolveGatewayInflightMap`
    호출과 `runGatewayInflightWork.set` 사이에 `resolveRequestedChannel` 등
    real I/O await 이 다수. inflight map 은 존재하나 set 이 너무 뒤에 있음.
    **fix 축**: set 을 check 바로 뒤(minimal I/O 이전)로 이동 또는 sentinel
    pre-insert.

  FIND-002 (poll) R-5 분류:
    "no inflight infrastructure" — inflight map 자체 미적용. 완료-dedupe
    cache 만 존재. upstream 60ec7ca0f1 refactor 가 poll 에 확장 안 됨.
    **fix 축**: `resolveGatewayInflightMap`/`runGatewayInflightWork` helper
    를 poll 에 적용 (mechanical extension).

  FIND-003 (chat.send attachment) R-5 분류:
    "conditional race — attachment branch 에서만" — `chatAbortControllers.get`
    과 `.set` 사이에 `resolveGatewayModelSupportsImages` +
    `parseMessageWithAttachments` real I/O. no-attachment branch 는 race 없음
    (CAL-001 올바른 guard 예시).
    **fix 축**: attachment parsing 을 guard 안으로 이동 또는 post-parse 재체크.

  세 FIND 는 각 RPC handler 의 idempotency guard 설계가 독립적이고, 파일도
  send.ts vs chat.ts 두 갈래. fix 전략도 "set 이동" / "helper 확장" / "분기
  재구조" 로 세 갈래. concurrency-auditor 가 domain-notes/gateway.md 에
  "epic 보다 개별 CAND 가 적절" 로 명시 (CAND-014/015/016 의 memory FIND
  구조와 동일). CONTRIBUTING.md "one thing per PR" 관점에서도 분리 우선.
  → 각각 single CAND.
proposed_title: "gateway/send: idempotencyKey check-then-act race — inflight map set after real I/O await duplicates outbound delivery"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-22
---

# gateway/send: idempotencyKey check-then-act race — inflight map set 이 real I/O await 뒤에 있어 outbound 중복 전송

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/send.ts:400-405` 의
`resolveGatewayInflightMap` 체크와 L560 의 `runGatewayInflightWork.set`
사이에 `resolveRequestedChannel` (L422) 등 real I/O await 이 다수. 동일
idempotencyKey 로 동시에 들어온 두 `send` RPC (또는 `message.action`) 가
이 window 에서 만나면 둘 다 check 를 통과하고 각자 outbound 채널
(Slack/WhatsApp/Telegram 등) 에 **동일 메시지를 dispatch**.

## 관련 FIND

- FIND-gateway-concurrency-001 (P1): client retry (타임아웃 → 동일
  idempotencyKey 재전송) 가 흔한 시나리오. microtask + real I/O 로 check/set
  이 atomic 하지 않음. `deliverOutboundPayloads` 가 2회 호출되어 플랫폼에
  duplicate 메시지 발생.

## 근거 위치

- check: `src/gateway/server-methods/send.ts:402` (`resolveGatewayInflightMap`)
- set: `src/gateway/server-methods/send.ts:92` (`runGatewayInflightWork` 내부)
- race window 구간 (여러 await): `src/gateway/server-methods/send.ts:422, 460, 480, 514, 527`
- `message.action` 동일 패턴: `src/gateway/server-methods/send.ts:314-372`
- 상위 직렬화 부재: `src/gateway/server/ws-connection/message-handler.ts:1497-1509`
  (`void (async IIFE)().catch(...)` — per-connection 직렬화 없음)
- 완료-dedupe 는 in-flight 방어 불가: `src/gateway/server-methods/agent-wait-dedupe.ts:206-220`
- upstream 선행 refactor (race 는 그대로): git `60ec7ca0f1 refactor: share
  gateway send inflight handling`

## 영향

- `impact_hypothesis: wrong-output` — outbound 채널에 duplicate 메시지/미디어.
- 빈도: 클라이언트 retry 는 idempotencyKey 의 존재 근거이므로 흔함. config/I/O
  지연이 길수록 race window 증가.
- 재현: `withMessageChannelSelection` mock 에 100 ms delay + 동일
  idempotencyKey 로 2회 호출 → `deliverOutboundPayloads` 2회.
- severity P1 — outbound platform duplicate delivery 는 reliability 의 핵심
  관심사. 비용·UX·관리자 cleanup 부담 동반.

## 대응 방향 (제안만)

- `runGatewayInflightWork` 의 `.set` 을 `resolveGatewayInflightMap` 호출
  직후 (await 없이) sentinel Promise 로 선점한 뒤, 이후 실제 work promise 로
  교체.
- 혹은 `resolveGatewayInflightMap` 내부에서 check + reserve 를 atomic 하게
  수행하도록 API 변경.
- 단일 PR 로 send/message.action 모두 커버 가능한 범위 (같은 helper 를
  공유하므로).

구체 구현은 SOL 단계.

## 중복 검사 (upstream)

domain-notes/gateway.md concurrency-auditor 섹션 R-8: 최근 3주
`src/gateway/` race 관련 commit 10건 중 send/poll/chat.send 의 idempotency
check-then-set race 는 이번 감사가 첫 발굴. CAL-008 duplicate 경고 없음.

`git log upstream/main --since="3 weeks ago" -- src/gateway/server-methods/send.ts`
→ refactor 1건 (`60ec7ca0f1`) 뿐. race fix 없음.

## 반증 메모

- outbound 채널 플러그인 (특히 WhatsApp) 의 자체 messageId uniqueness 가 일부
  중복 dispatch 를 흡수할 수 있음 — 그러나 gateway 층에서 중복 dispatch 자체를
  막지 못함은 여전히 결함.
- `idempotencyKey` 가 클라이언트 라이브러리에서 항상 채워지는지 — 미지정
  시 본 FIND 이전에 이미 idempotency 자체가 없음 (별개 이슈).
- `loadConfig()` 가 캐시 히트면 L422 의 await window 가 짧아져 재현 난이도
  상승 — 그래도 microtask 경계만으로도 race 는 성립.
