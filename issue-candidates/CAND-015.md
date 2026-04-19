---
candidate_id: CAND-015
type: single
finding_ids:
  - FIND-gateway-memory-002
cluster_rationale: |
  단독 FIND. CAND-014 의 cluster_rationale 참조 — 세 gateway-memory FIND 는
  R-5 분류상 독립적 root cause axis 를 갖는다.

  FIND-002 root_cause_chain[0]:
    "state.inFlight throttle/dedupe 로직이 맵에 엔트리가 있어야 동작 →
     set 은 registration 결과와 무관하게 수행해야 하는 제약."
  [1]: "'miss + no-reg → delete' cleanup 누락."
  [2]: "clearNodeWakeState 호출자 = ws-connection.ts:327 한 곳뿐. 미등록
        nodeId 는 WS 연결 cycle 없음."

  CAND-014 (costUsageCache) 는 no-cleanup, CAND-016 (agentRunStarts) 는
  event-dependent cleanup with robust try/finally — 이 FIND 는
  "set-before-validation + cleanup precondition never holds" 로 셋 중 가장
  hot-path 에 가깝다 (operator 가 arbitrary nodeId 를 보내기만 하면 됨).
proposed_title: "gateway/nodes: maybeWakeNodeWithApns sets nodeWakeById before registration check, leaking entries for unregistered nodeIds"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-19
---

# gateway/nodes: maybeWakeNodeWithApns 가 registration 체크 전에 nodeWakeById 에 엔트리를 set 하여 미등록 nodeId 에 대한 누수

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/nodes.ts:312-313` 에서
`maybeWakeNodeWithApns` 진입 즉시 `nodeWakeById.set(nodeId, state)` 가 수행된다.
이후 L333 `loadApnsRegistration(nodeId)` 이 null 을 반환하면 L334-336 에서
`path: "no-registration"` 으로 조기 반환되지만, 이 경로에서 엔트리를 정리하는
cleanup 은 부재하다.

## 관련 FIND

- FIND-gateway-memory-002: 미등록/재페어링된/오탈자 nodeId 로 wake 호출이 들어오면
  `nodeWakeById` (및 연관 `nodeWakeNudgeById`) 에 엔트리가 누적. 유일 삭제 경로인
  `clearNodeWakeState` 는 WS close 이벤트 + role=node + registered 삼중 조건에만
  발사되므로 미등록 nodeId 에 대해서는 fire 안 함.

## 근거 위치

- 선언: `src/gateway/server-methods/nodes.ts:72` (`nodeWakeById`), :73 (`nodeWakeNudgeById`)
- set 경로 (registration 전): `src/gateway/server-methods/nodes.ts:312-313`
- no-registration 반환: `src/gateway/server-methods/nodes.ts:333-336`
- 유일 delete 경로: `src/gateway/server-methods/nodes.ts:525-528` (`clearNodeWakeState`)
- 호출자: `src/gateway/server/ws-connection.ts:327` (WS close, role=node + registered)

## 영향

- `impact_hypothesis: memory-growth` (slow leak, 엔트리 크기 ~100B)
- 스크립팅/자동화 환경에서 오래된 nodeId 재사용 시 누적
- `clearStaleApnsRegistrationIfNeeded` (L374) 는 APNs registration 만 지우고
  `nodeWakeById` 는 안 건드림 → "좀비 엔트리" 가능
- P3 — 빈도는 auth-gated, 엔트리 크기 작음

## 대응 방향 (제안만)

no-registration 분기에 conditional `nodeWakeById.delete(nodeId)` (단, `state.inFlight`
참조 공유 invariant 를 깨지 않도록 주의). 또는 `clearStaleApnsRegistrationIfNeeded`
에서 함께 정리. 구체는 SOL 단계.

## 반증 메모

- operator 가 arbitrary nodeId 를 보낼 수 있는 RBAC 범위 미확인 → allowlist 있으면
  빈도 추가 감소.
- `state.inFlight` promise 가 resolve 후 nullify 되는지 확인 안 함 (drift risk).
