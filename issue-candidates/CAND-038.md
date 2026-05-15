---
candidate_id: CAND-038
type: single
finding_ids:
- FIND-gateway-lifecycle-001
cluster_rationale: 단일 결함 — ws-connection.ts:351-421 의 WS close 핸들러가 session/node/presence/nodeWake
  state 만 cleanup 하고 chatAbortControllers 의 ownerConnId 매칭 entry 는 abort 안 함. registerChatAbortController
  (chat-abort.ts:71-109) 의 ownerConnId 필드는 abort RPC 권한 판정 (canRequesterAbortChatRun)
  에만 사용. user disconnect 후 maintenance interval 의 expiresAtMs sweep (최소 2분 floor)
  까지 runner 가 abort 없이 계속 진행 — LLM 토큰 과금 + tool 실행 + 외부 API call 누적. 다른 gateway FIND
  와 file/axis 다름.
proposed_title: 'gateway/server: abort chat runs owned by disconnecting WS connection'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - '1819e41d26 fix(gateway): preserve node reconnect state (#78351)'
  - '8cae2ed645 fix(gateway): allow chat.abort to stop agent RPC runs'
  - '047c03cc88 fix(gateway): drop stale webchat handshakes'
  - '1f1f70a23f fix(gateway): align sessions abort wait semantics (#74751)'
  finding: 6주 ws-connection.ts / chat-abort.ts commit 어디에도 close 핸들러의 ownerConnId
    매칭 cleanup 추가 0건. 1819e41d26 는 node reconnect axis, 8cae2ed645 는 chat.abort RPC
    의 agent RPC 까지 확장 (다른 axis), 1f1f70a23f 는 abort wait semantics 의 timing 정합성 (다른
    axis).
  pr_search: gh pr list --search 'chatAbortControllers ws disconnect ownerConnId'
    → 0 매치.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
- CAND-017
- CAND-018
pre_sol_proof:
  status: blocked-external-dep
  proof_record: proofs/PROOF-CAND-038-pre-20260515-052108-e2e-blocked.md
  measurements:
    scenario: proof-CAND-038-e2e
    trials: 0
    e2e_attempt: true
    blocked_reason: audit-side infrastructure missing (device pairing / pending state
      injection / native runtime stub) — multi-session work
    next_session: gateway-e2e.md
  scenario: proof-CAND-038-e2e
---

# gateway/server: abort chat runs owned by disconnecting WS connection

## 문제 요약

`src/gateway/server/ws-connection.ts:351-421` 의 WS connection close 핸들러는 다음만 cleanup:
- `unsubscribeAllSessionEvents(connId)` — session 이벤트 구독.
- `nodeRegistry.unregister(connId)` — node 등록.
- `nodeUnsubscribeAll(nodeId)` / `clearNodeWakeState(nodeId)` / `removeRemoteNodeInfo` — node 측 state.
- `upsertPresence({reason: "disconnect"})` — presence.

`chatAbortControllers` 의 ownerConnId === connId 매칭 entry 는 abort 하지 않는다. `registerChatAbortController` (`src/gateway/chat-abort.ts:71-109`) 가 entry 에 저장한 `ownerConnId` 는 `canRequesterAbortChatRun` (`src/gateway/server-methods/chat.ts:1562-1581`) 의 권한 판정 5건 (chat.ts:1570/1571/1577/2196 + agent.ts:1330) 에만 소비.

발현:
1. user (operator/webchat/cli/mobile-node) 가 chat.send / agent.request 발행 → AbortController + entry 등록 (`ownerConnId: X`).
2. runner 가 LLM 호출, tool 실행, 외부 API call 시작 (수십초~수분).
3. user 가 네트워크 단절 / 탭 종료 / Ctrl-C 로 WS close.
4. close 핸들러는 chatAbortControllers 미터치 — AbortController.signal.aborted=false 유지.
5. runner 가 abort 미수신, 끝까지 진행. broadcast 결과는 닫힌 socket 으로 send→swallow.
6. expiresAtMs (minMs=2분 floor + graceMs=60s, 보통 5-30분) 후 maintenance interval (server-maintenance.ts:153-174) 의 sweep 이 timeout 으로 abort.

영향: LLM 토큰 과금 (user 가 결과 못 받음에도), tool 실행 (file write, MCP, shell) — user 의도와 분리된 시점에 외부 상태 변경.

## fix surface

ws-connection.ts:351-421 의 close 핸들러에 추가:
```ts
for (const [runId, entry] of chatAbortControllers) {
  if (entry.ownerConnId === connId) {
    abortChatRunById(runId, { reason: "owner-disconnect" });
  }
}
```

abort() 는 idempotent + broadcastChatAborted 가 다른 connection 들에 알림. 잠시 disconnect 후 같은 user 가 재접속해 결과 받고 싶은 시나리오는 현재 경로에서도 closed socket → swallow 로 잃으므로 (cross_refs CAND-017) regression 아님.

XS PR (1 loop + 1 import).

## upstream-dup 검사 결과

- 6주 ws-connection.ts / chat-abort.ts commit 어디에도 ownerConnId 매칭 disconnect cleanup 추가 0건.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — close 핸들러에 ownerConnId loop 추가.
2. 회귀 테스트 — ws-connection.test.ts 에 close → chatAbortControllers 의 ownerConnId 매칭 entry 가 abort 되는지 + 다른 connId entry 는 그대로인지 assert.
3. CAL-003 회피 — production hot-path (모바일 backgrounding, laptop sleep) 자료 인용 필요.

## 관련 FIND

- FIND-gateway-lifecycle-001: WS close 핸들러가 chatAbortControllers 의 ownerConnId 매칭 entry 를 abort 하지 않음 → maintenance sweep 까지 (수 분~수십 분) runner 부작용 누적.
