---
candidate_id: CAND-018
type: single
finding_ids:
  - FIND-gateway-error-boundary-002
cluster_rationale: |
  단독 FIND. CAND-017 의 cluster_rationale 참조 — 같은 셀의 FIND-001 과 axis
  독립.

  FIND-002 root_cause_chain:
    [0] "outer catch 가 pre-handshake/post-handshake 두 책무를 겸임."
    [1] "req.id 복원 context 부재 → 특정 request 에 대한 error 응답 불가."
    [2] "`.catch(respond UNAVAILABLE)` (L1455) 는 async dispatch reject 만 커버,
         pre-dispatch sync throw 는 outer catch 로 직행."

  hot-path 재현 증거 없음 (self-check 에 명시) — hardening 수준 P3. CAL-003
  warning 이 self-check 에 이미 적시되어 있음. publisher 단에서 재현성 기준
  추가 검증 필요할 수 있음.
proposed_title: "gateway/ws-connection: post-handshake outer catch logs only and leaves connection open (potential hang)"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-19
---

# gateway/ws-connection: post-handshake outer catch 가 connection 을 닫지 않고 로그만 남긴다

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server/ws-connection/message-handler.ts:1459-1465`
의 최상위 catch 는 `if (!getClient()) close();` 가드로 pre-handshake 일 때만
connection 을 닫는다. post-handshake synchronous throw (pre-dispatch 단계) 는
log-only + respond 없음 → 클라이언트가 해당 request id 응답을 영원히 받지 못함
→ client-side timeout 까지 hang.

## 관련 FIND

- FIND-gateway-error-boundary-002: IIFE `.catch` (L1455) 는 async dispatch reject 만
  UNAVAILABLE 응답으로 변환. pre-dispatch sync throw (logWs, setCloseCause, respond
  closure 정의 등) 는 outer catch 로 직행. req 변수가 try-block 지역이라 catch
  에서 id 복원 불가.

## 근거 위치

- 문제 catch: `src/gateway/server/ws-connection/message-handler.ts:1459-1465`
- 대조 unconditional 방어: 같은 파일 L1455 `.catch(respond(UNAVAILABLE))`
- req 지역 변수 선언: L1384 `const req = parsed;` (catch 에서 접근 불가)

## 영향

- `impact_hypothesis: hang` — 개별 connection 에 국한
- 다른 connection 영향 없음 (per-socket handler)
- production-observed 증거 없음 (pre-dispatch sync throw 경로가 매우 좁음)
- P3 hardening — CAL-003 warning: hot-path 재현 증거 부재. PR 제출 시 재현
  테스트 필요.

## 대응 방향 (제안만)

- post-handshake 에서도 respond(UNAVAILABLE) 또는 close 중 하나를 수행
- `const req = parsed;` 를 outer scope 로 끌어올려 catch 에서 id 복원 가능하게
- 또는 pre-dispatch 로직 자체를 try/catch 로 개별 감싸 `.catch(respond)` 패턴에
  편입

구체는 SOL 단계.

## 반증 메모

- client.ts 기본 RPC timeout + heartbeat/cancel 동작 미확인 → 실제 hang 이 client
  측 자동 reconnect 로 흡수될 가능성.
- validateRequestFrame/logWs/setCloseCause 는 null-safe 설계 → sync throw 경로
  좁음.
- 이 FIND 는 코드 구조 gap 주장 — publisher 단에서 hot-path 재현 실패 시 drop
  가능성 있음.
