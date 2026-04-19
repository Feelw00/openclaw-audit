---
candidate_id: CAND-017
type: single
finding_ids:
  - FIND-gateway-error-boundary-001
cluster_rationale: |
  단독 FIND. 같은 셀(gateway-error-boundary)의 FIND-002 와 root cause axis 독립.

  FIND-001 root_cause_chain[0]:
    "JSON.parse 실패 시 catch 가 `return;` 만 수행. 로그/메트릭/에러 응답 없음."
    → **silent observability gap** in node event JSON parsing.

  FIND-002 root_cause_chain[0]:
    "post-handshake 에서 close 를 하지 않음. 정상 client 가 일시적 잘못된 frame
     을 보낼 수 있다고 보고 연결 유지" (trade-off 기반 선택).
    → **outer catch close gate for post-handshake** (연결 lifecycle 문제).

  두 FIND 는 다른 파일(server-node-events.ts vs. message-handler.ts), 다른
  symptom (wrong-output/observability gap vs. potential hang), 다른 해결 축
  (logger 추가 vs. close 조건 재설계). 공통은 "error boundary 내부에서 정보
  손실" 이라는 상위 테마뿐이고 fix 기전이 다르다. → 각각 single CAND.
proposed_title: "gateway/node-events: agent.request payloadJSON parse failure silently dropped (no log, no metric)"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-19
---

# gateway/node-events: node agent.request payloadJSON malformed 시 silent drop 으로 observability gap

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-node-events.ts:403-408` 의
`agent.request` 분기는 node 이벤트의 `payloadJSON` 을 `JSON.parse` 로 역직렬화한다.
throw 시 catch 블록은 `return;` 만 호출 — 로그/메트릭/에러 응답 부재. 상위
`node.event` RPC handler (server-methods/nodes.ts:1145) 는 이미 `respond(true,
{ ok: true })` 응답했기 때문에 client 에게도 실패 신호가 가지 않는다.

## 관련 FIND

- FIND-gateway-error-boundary-001: node 측 직렬화 버그/wire 손상/schema drift 로
  malformed JSON 이 도달하면 agent.request 1건이 조용히 사라짐. "메시지를 보냈는데
  답이 없다" 진단 단서 부재.

## 근거 위치

- 문제 catch: `src/gateway/server-node-events.ts:403-408`
- 동일 파일 silent-return 자매 catch: L253-256 (parseSessionKeyFromPayloadJSON),
  L271-274 (parsePayloadObject)
- 상위 응답 경로 (ok:true): `src/gateway/server-methods/nodes.ts:1102-1147`
- logger 접근 가능 증거 (same file): L246 `ctx.logGateway.warn` 사용처

## 영향

- `impact_hypothesis: wrong-output` (유저 메시지 silent loss)
- gateway log/metrics 에 흔적 없음
- 재현: malformed payloadJSON 으로 node.event RPC 호출
- P3 — openclaw 자체 node client 는 schema 검증 경유. legacy node / 직렬화 drift
  시나리오에서만 발생.

## 대응 방향 (제안만)

catch 블록 내 최소한 `ctx.logGateway.warn({ nodeId, err })` 호출 + 가능하면 metric
이 도달 여부 counter. 다른 silent catch (voice wake best-effort) 와 구분하여
user-initiated 경로인 agent.request 만 선별 대응. 구체는 SOL 단계.

## 반증 메모

- 상위 ws frame ajv 검증이 `payloadJSON` 유효성까지 강제하는지 미확인 → 강제하면
  dead code 에 가까워 심각도 하락.
- node client 가 `payload` (object) vs `payloadJSON` (string) 중 어느 경로를 주로
  쓰는지 production telemetry 부재.
