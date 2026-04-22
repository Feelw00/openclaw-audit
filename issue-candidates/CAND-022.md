---
candidate_id: CAND-022
type: single
finding_ids:
  - FIND-gateway-concurrency-002
cluster_rationale: |
  단독 FIND. CAND-021 의 cluster_rationale 참조 — 같은 셀의 FIND-001 과
  FIND-003 과 각각 다른 root cause axis.

  FIND-002 root_cause_chain:
    [0] "poll 핸들러는 완료-dedupe cache 만 검사 (L590). inflight map 자체
         미적용."
    [1] "upstream 60ec7ca0f1 refactor 가 send/message.action 에만 inflight
         helper 도입. poll 은 legacy."
    [2] "sync check 이후 resolveRequestedChannel + sendPoll 등 real I/O
         await 다수 → race window 매우 넓음."

  FIND-001 (send) 대비 race window 가 훨씬 넓음 — inflight helper 자체가
  없으므로 재시도 겹침 시 거의 100% duplicate spawn. fix 는 mechanical:
  helper 를 poll 에 extend.
proposed_title: "gateway/poll: inflight map absent — idempotencyKey retries spawn duplicate polls"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-22
---

# gateway/poll: inflight map 부재로 idempotencyKey 재시도 시 poll 중복 생성

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/send.ts:589-598` 의
`poll` RPC 핸들러는 `context.dedupe.get('poll:${idem}')` 만 검사하고
**inflight map 자체가 없다**. 동일 idempotencyKey 로 동시에 도착한 두
요청 모두 `outbound.sendPoll` (L666) 을 호출 → 플랫폼 (Slack/WhatsApp/
Telegram 등) 에 **동일 질문의 poll 이 2개** 생성.

## 관련 FIND

- FIND-gateway-concurrency-002 (P1): `send` 와 `message.action` 에는 upstream
  refactor (60ec7ca0f1) 로 inflight helper 가 도입됐으나 `poll` 핸들러는
  legacy 스타일 그대로. `cacheGatewayDedupeSuccess` (L674) 는 `sendPoll` 완료
  후에만 호출되어 in-flight 동안은 unconditional guard 부재.

## 근거 위치

- poll handler: `src/gateway/server-methods/send.ts:562-695`
- sync check (insufficient): `src/gateway/server-methods/send.ts:590`
- real I/O await: `src/gateway/server-methods/send.ts:598` (`resolveRequestedChannel`),
  `src/gateway/server-methods/send.ts:666` (`outbound.sendPoll`)
- 완료 시점에만 dedupe 저장: `src/gateway/server-methods/send.ts:674, 682`
- 대조 (send/message.action 의 inflight helper): `src/gateway/server-methods/send.ts:63-92`
- upstream 선행 refactor (poll 누락): git `60ec7ca0f1 refactor: share gateway
  send inflight handling`

## 영향

- `impact_hypothesis: wrong-output` — 동일 질문의 poll 2개가 channel 에
  공존. UX 교란 + 관리자 수동 cleanup 필요.
- 빈도: FIND-001 보다 재현 난이도 낮음. L590 sync check 이후 모든 await
  구간이 race window. 거의 모든 "idempotencyKey 를 공유한 concurrent poll"
  이 duplicate 를 만듦.
- severity P1 — poll 은 일반 메시지보다 시각적 무게가 크고, 그룹 챗 내
  충돌 시 사용자가 어느 쪽에 응답해야 할지 혼란.

## 대응 방향 (제안만)

- `resolveGatewayInflightMap` + `runGatewayInflightWork` helper 를 poll
  handler 에 적용 — send/message.action 의 패턴을 그대로 재사용.
- 단, CAND-021 (send race) 을 먼저 fix 한 뒤 poll 로 extend 하면 helper
  자체의 개선 (set 을 check 직후로) 이 poll 에도 자동 적용 — 순서 고려 필요.

구체 구현은 SOL 단계.

## 중복 검사 (upstream)

`git log upstream/main --since="3 weeks ago" -- src/gateway/server-methods/send.ts`
→ refactor 1건 (`60ec7ca0f1`) 뿐. poll race 관련 fix 없음. CAL-008 경고
없음.

## 반증 메모

- outbound 플러그인 별 poll 생성 시 플랫폼 자체 dedupe (pollId uniqueness)
  가 있다면 피해 감소 — 그러나 Slack/WhatsApp 은 대체로 자동 pollId 부여라
  중복 생성 자체는 발생.
- poll RPC 의 production 빈도 (대시보드 telemetry 없음) — bot/integration
  이 대량 재시도할 때 최악.
- client 가 poll RPC 를 재시도하는 정책 유무. 기본 client 의 retry 여부
  확인 안 함.
