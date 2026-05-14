---
id: FIND-gateway-lifecycle-001
cell: gateway-lifecycle
title: WS connection close does not abort runs owned by the disconnecting connId
file: src/gateway/server/ws-connection.ts
line_range: 385-407
evidence: "```ts\n      if (client && isWebchatClient(client.connect.client)) {\n\
  \        logWsControl.info(\n          `webchat disconnected code=${code} reason=${logReason\
  \ || \"n/a\"} conn=${connId}`,\n        );\n      }\n      const context = buildRequestContext();\n\
  \      context.unsubscribeAllSessionEvents(connId);\n      let currentDisconnectedNodeId:\
  \ string | null = null;\n      if (client?.connect?.role === \"node\") {\n     \
  \   currentDisconnectedNodeId = context.nodeRegistry.unregister(connId);\n     \
  \ }\n      if (\n        client?.presenceKey &&\n        (client.connect.role !==\
  \ \"node\" || currentDisconnectedNodeId !== null)\n      ) {\n        upsertPresence(client.presenceKey,\
  \ { reason: \"disconnect\" });\n        broadcastPresenceSnapshot({ broadcast, incrementPresenceVersion,\
  \ getHealthVersion });\n      }\n      if (currentDisconnectedNodeId) {\n      \
  \  removeRemoteNodeInfo(currentDisconnectedNodeId);\n        context.nodeUnsubscribeAll(currentDisconnectedNodeId);\n\
  \        clearNodeWakeState(currentDisconnectedNodeId);\n      }\n```\n"
symptom_type: lifecycle-gap
problem: '''gateway WS connection 의 close 핸들러 (ws-connection.ts:351-421) 는 session
  이벤트 구독,

  node registry, presence, node wake state 등 connection-scoped 리소스는 정리하지만,

  해당 connection 이 등록한 in-flight `chatAbortControllers` 엔트리는 abort 도 cleanup 도

  하지 않는다. `registerChatAbortController` 는 `ownerConnId` 를 entry 에 저장하지만

  이 필드는 오직 abort RPC 의 권한 판정 (`canRequesterAbortChatRun`, chat.ts:1562-1581) 에서만

  소비되고, owner disconnect 시 자동 abort 트리거로는 사용되지 않는다.''

  '
mechanism: "'1) user (operator/webchat/cli/node) 가 WS 로 gateway 에 붙고 connId=X 로 등록.\n\
  2) `chat.send` 또는 `agent.request` RPC 발행 → `registerChatAbortController` (chat-abort.ts:71-109)\n\
  \   이 새 AbortController 와 entry 를 `chatAbortControllers.set(runId, {... ownerConnId:\
  \ X})` 로 저장.\n3) 에이전트 runner 가 LLM 호출, tool 실행, 파일 쓰기, 외부 API 호출을 시작 (수십초~수분).\n\
  4) 그 사이 user 가 네트워크 단절/탭 종료 등으로 WS disconnect. close 핸들러 진입.\n5) 핸들러는 session/node/presence\
  \ 만 정리. `chatAbortControllers` 순회 + ownerConnId===connId\n   매칭 abort 호출은 없다. AbortController\
  \ 는 살아있고 controller.signal.aborted=false.\n6) runner 는 abort signal 을 받지 못하고 끝까지\
  \ 진행. 결과 응답은 닫힌 socket 으로 send()\n   되어 swallow (error-boundary 셀 노트 §3 참조). 부작용\
  \ (파일 쓰기, 외부 호출) 은 그대로 실행.\n7) entry 는 `expiresAtMs` (chat-abort.ts:35-54, 최소 2분,\
  \ 최대 24h) 이후 maintenance 인터벌\n   (server-maintenance.ts:153-174) 이 timeout 으로 abort\
  \ 처리.'\n"
root_cause_chain:
- why: 왜 WS close 핸들러가 chatAbortControllers 를 건드리지 않는가?
  because: close 핸들러 (ws-connection.ts:351-421) 는 처음 설계 시 session/node 단위 구독·등록만 cleanup
    대상으로 모델링했고, run-scoped abort controller 는 별개 lifetime (idempotency dedupe + 명시적
    abort RPC) 로 다뤘다. `ownerConnId` 필드 자체는 권한 체크 (canRequesterAbortChatRun, chat.ts:1577)
    용도로 추가됐을 뿐 disconnect cleanup 의 트리거로 의도되지 않음.
  evidence_ref: src/gateway/server/ws-connection.ts:385-407
- why: 왜 ownerConnId 가 abort 권한 체크 외에는 활용되지 않는가?
  because: 'chat run 의 lifecycle 은 "사용자가 명시적으로 stop 한다" 또는 "에이전트가 자연히 종료한다" 또는 "expiresAtMs
    도달 시 maintenance 가 abort" 의 세 경로만 가정. WS disconnect 가 abort 의도를 내포한다는 매핑이 없다.
    `kind: "chat-send" | "agent"` 두 종류 모두 동일. ownerConnId 는 rg 결과 chat.ts:1570/1577/2196
    와 agent.ts:1330 단 4건만 참조.'
  evidence_ref: src/gateway/server-methods/chat.ts:1562-1581
- why: 왜 maintenance interval 의 timeout 만으로는 부족한가?
  because: expiresAtMs 는 (timeoutMs + 60s grace) 또는 최소 2분 (resolveChatRunExpiresAtMs,
    chat-abort.ts:35-54) 이후로 설정. 즉 user 가 disconnect 한 직후부터 최소 2분 (실제로는 runtime timeoutMs
    인 5-30분이 흔함) 동안 runner 가 abort 없이 계속 실행. 그 동안의 부작용 (LLM 토큰 과금, 도구 호출, 파일 변경) 은
    cancel 의도와 무관하게 진행.
  evidence_ref: src/gateway/chat-abort.ts:35-54
- why: 왜 close 핸들러에 cleanup 을 추가해도 안전한가? (반증 시도)
  because: ownerConnId === connId 매칭은 disconnect 한 연결이 등록한 run 만 잡고, 다른 연결이 동일 sessionKey
    로 재접속한 경우 그 connection 의 connId 는 다르므로 영향 없음. abort() 는 idempotent. abort 후 broadcastChatAborted
    (chat-abort.ts:130-156) 가 모든 subscriber 에게 알림. 유일한 부정 시나리오는 "잠시 끊어진 후 같은 user
    가 즉시 재접속해 run 결과를 받고 싶다" 인데, 현재 경로에서도 결과는 closed socket 으로 send → swallow 되어 잃는다
    (error-boundary 셀 §3).
  evidence_ref: src/gateway/chat-abort.ts:158-210
impact_hypothesis: wrong-output
impact_detail: '''정성: chat/agent run 의 owner connection 이 비정상 종료된 후 최소 2분, 일반적으로 5-30분
  (timeoutMs

  영향) 동안 runner 가 cancel 신호 없이 계속 실행. 그 사이 발생할 수 있는 부작용:

  - LLM 토큰 소비 (user 가 결과를 받을 수 없는데 과금 발생).

  - 도구 실행 (shell, file write, MCP) — 사용자 의도와 분리된 시점에 외부 상태 변경.

  - tool_use 결과 broadcast 가 connId-targeted subscriber 가 없어 (`broadcastToConnIds`
  도) drop.

  재현 조건: chat.send/agent.request 발행 → 응답 도착 전 WS disconnect (Ctrl-C, 네트워크 단절,

  탭 종료). 일반 user 작업 흐름에서 자주 발생할 패턴이지만 정량 빈도 미측정.

  Severity P3: 메모리 누수/크래시는 아니며 (maintenance 가 결국 회수), UX 분실/리소스 낭비 위주.''

  '
severity: P3
counter_evidence:
  path: src/gateway/server-maintenance.ts
  line: 153-174
  reason: '''maintenance interval (60초) 의 expiresAtMs 기반 abort 가 최종 안전망 역할은 한다. 확인한
    반증 카테고리:

    (1) 숨은 방어: `unsubscribeAllSessionEvents(connId)` (ws-connection.ts:391) 는 connId-targeted

    event subscription 만 제거하고 chatAbortControllers entry 와는 별개 자료구조. 다른 경로에서도

    ownerConnId 를 cleanup 트리거로 쓰는 코드 없음 (`rg -n "ownerConnId" src/gateway/` → 4건

    전부 권한 판정용).

    (2) 기존 테스트 커버리지: ws-connection.test.ts 의 close 시나리오는 session/node 구독 cleanup

    만 assert (확인된 mock: unsubscribeAllSessionEvents, nodeUnsubscribeAll). chatAbortControllers

    integration 테스트 없음 (chat-abort.test.ts 는 abortChatRunById 단독 unit).

    (3) primary-path inversion (CAL-001): 정상 경로에서 maintenance interval 이 timeout 으로

    abort 하므로 누수는 발생하지 않는다 → 메모리 측면 영향 없음. 그러나 "abort intent timing" 자체가

    disconnect 시점에 발화되지 않는 것이 본 FIND 의 골자. memory-leak 이 아닌 cancellation-gap 으로

    분류 (lifecycle-gap symptom_type 적합).

    (4) hot-path 활성: chat.send / agent.request 모두 production primary path. `kind:
    "chat-send"`

    + `kind: "agent"` 두 종류 모두 동일 패턴. webchat / operator UI / mobile node 어디서든 트리거.

    (5) upstream-dup (CAL-008): `git log upstream/main --since="3 weeks ago" -- src/gateway/chat-abort.ts

    src/gateway/server/ws-connection.ts` → chat-abort 관련 fix 없음 (race-related: dfe0e49c8a
    는

    extensions/memory-core scope). ownerConnId 도입은 chat run authorization 추가 시점.

    (6) error-boundary 셀 노트와 일치: 동일 도메인 노트 §3 "websocket drop 시 in-flight RPC"

    에서 본 셀로 위임됨. CAND-017/018 과 cross-ref 가치.''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs:
- FIND-gateway-error-boundary-001
- FIND-gateway-error-boundary-002
---
# WS connection close does not abort runs owned by the disconnecting connId

## 문제

gateway WS connection close 핸들러 (`src/gateway/server/ws-connection.ts:351-421`) 는 다음을 정리한다:

- `unsubscribeAllSessionEvents(connId)` — session event 구독.
- `nodeRegistry.unregister(connId)` — node role 인 경우 node 등록.
- `nodeUnsubscribeAll(nodeId)` — node 의 모든 구독.
- `clearNodeWakeState(nodeId)` — node wake state.
- `removeRemoteNodeInfo`, `upsertPresence({reason: "disconnect"})`.

그러나 같은 connection 이 `chat.send` / `agent.request` 로 등록한 `chatAbortControllers` 엔트리는 손대지 않는다. `registerChatAbortController` (`src/gateway/chat-abort.ts:71-109`) 가 entry 에 저장한 `ownerConnId` 필드는 `canRequesterAbortChatRun` (`src/gateway/server-methods/chat.ts:1562-1581`) 의 권한 판정에만 쓰이고, owner disconnect 가 자동 abort 트리거로 매핑되지 않는다.

결과: connection 이 비정상 종료된 후 최소 2분 (`resolveChatRunExpiresAtMs` 의 minMs, `chat-abort.ts:46`), 일반적으로 runtime timeoutMs (5-30분) 동안 runner 가 abort signal 없이 진행한다.

## 발현 메커니즘

1. operator/webchat/cli/mobile-node 가 WS 로 gateway 에 붙고 `connId = X` 로 등록.
2. RPC `chat.send` 호출 → `registerChatAbortController` 가 새 `AbortController` 생성 후 `chatAbortControllers.set(runId, { controller, sessionKey, expiresAtMs, ownerConnId: X, ownerDeviceId: ?, kind: "chat-send" })` (chat.ts:2189-2199).
3. RPC accepted 응답 전송. 에이전트 runner 가 LLM 호출, tool 실행, 외부 API 호출 시작.
4. user 가 네트워크 단절/탭 종료/Ctrl-C 등으로 WS disconnect → ws-connection.ts:351 `socket.once("close")` 진입.
5. close 핸들러는 본문 "문제" 에 열거한 connection-scoped 리소스만 cleanup. `chatAbortControllers` 순회 + `entry.ownerConnId === connId` 매칭 abort 호출은 부재.
6. AbortController 는 `signal.aborted === false` 인 채로 살아있다. runner 는 abort 신호 미수신 → 작업 진행.
7. runner 가 진행하는 동안의 모든 emit (assistant text delta, tool_use, lifecycle) 는 `broadcast` 로 모든 connection 에 전파되지만, 원래 owner connId 는 닫힘. close 후의 `send(JSON.stringify(...))` 는 try/catch swallow (ws-connection.ts:298-304).
8. 최소 2분 후 maintenance interval (60초 주기, server-maintenance.ts:128 의 sweep 진입) 이 `now > entry.expiresAtMs` 인 entry 를 `abortChatRunById` 로 정리. 이 시점에야 비로소 `controller.abort()` 호출.

## 근본 원인 분석

1. **Cleanup boundary 매핑 누락**: close 핸들러는 *connection lifetime* 단위 리소스 (session 구독, node 등록, presence) 만 정리 대상으로 설계됐다. *run lifetime* 리소스 (chatAbortControllers entry) 는 별개 lifetime (idempotency dedupe + 명시적 abort RPC + timeout) 으로 다뤄지며 disconnect signal 과 cross-domain 매핑이 없다.
2. **ownerConnId 의 의도 한정**: `ownerConnId` 는 abort 권한 체크 (`canRequesterAbortChatRun`, chat.ts:1577) 용도로 추가됐다. disconnect 시 cleanup trigger 로 쓰이지 않음. `rg -n "ownerConnId" src/gateway/` 전수 매치 결과 chat.ts:1570/1571/1577/2196 + agent.ts:1330 단 5건이고 모두 권한 판정 또는 등록.
3. **AbortController 의 idle 상태**: signal.aborted=false 인 상태에서 runner 의 abort listener 는 발화하지 않으므로, runner 는 disconnect 인지 없이 끝까지 실행. broadcast 결과는 owner 가 닫힌 socket 임에도 send→swallow (error-boundary 셀 노트 §3) 되어 silent drop.
4. **Maintenance interval 의 latency**: expiresAtMs 는 `min(maxMs, max(now + minMs, now + timeoutMs + graceMs))` (chat-abort.ts:36-54). minMs=2분, graceMs=60초. 일반 timeoutMs 가 수 분이라 평균 5-10분 후에야 maintenance 가 abort 처리. 그 사이 runner 진행은 그대로.

## 영향

- **현상**: 사용자가 chat/agent run 중 disconnect 후 재접속해도, disconnect 시점부터 maintenance abort 까지 runner 가 진행한 모든 부작용 (파일 변경, MCP tool 호출, 외부 API call, LLM 토큰 과금) 은 되돌릴 수 없다.
- **재현**: `chat.send` 또는 `agent.request` 호출 → ack 받은 직후 WS close (`socket.terminate()` 또는 process kill). 동일 sessionKey 로 다른 connection 에서 abort RPC 도 안 보내고 그대로 둠 → 최소 2분 이상 runner 진행.
- **빈도**: 일반 user 작업 흐름에서 흔한 패턴 (모바일 backgrounding, laptop sleep, network glitch). 정확한 정량 없음.
- **자료 손실 vs 자원 낭비**: 결과 데이터는 broadcast 로 다른 connection 들에 전파되므로 동일 session 의 다른 client 가 받으면 됨. 단, owner-only kind="chat-send" 에서 어떤 connection 도 보지 못한 채 부작용만 발생하는 경우가 핵심 문제.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "chatAbortControllers" src/gateway/ --type ts` → 30+ hits. close 핸들러에서 등장 안 함 (확인 완료). cleanup 트리거는 maintenance interval (server-maintenance.ts:153-174) 의 timeout-기반 sweep 만.
- `unsubscribeAllSessionEvents(connId)` 는 session 메시지 fan-out 만 끄고 abort 와는 무관 (broadcast 는 여전히 closed socket 으로 send→swallow).
- `ownerConnId` 추적: `rg -n "ownerConnId" src/gateway/` → 5건 (chat.ts:1570, 1571, 1577, 2196, agent.ts:1330). 모두 권한 판정 또는 등록 코드. cleanup 사용처 없음.

### 기존 테스트 커버리지

- `chat-abort.test.ts` → `abortChatRunById` 단독 unit. WS disconnect 통합 시나리오 없음.
- `ws-connection.test.ts` → `unsubscribeAllSessionEvents` / `nodeUnsubscribeAll` mock 호출 assert. chatAbortControllers 는 mock 으로 빈 Map 만 주입되고 호출 assert 없음.
- `server-maintenance.test.ts` → expiresAtMs sweep 동작 cover. disconnect-triggered cleanup 가설 부재.

### 호출 빈도 / 경로 활성 여부

- chat.send + agent.request 는 모든 chat 사용 시나리오의 핫패스. webchat / operator UI / mobile node / cli 모두 영향.
- WS disconnect 의 close 핸들러는 모든 close 케이스 (normal + error + 1006 abnormal) 에서 단일 entry-point (ws-connection.ts:351 `socket.once("close")`).

### 설정 / feature flag

- timeoutMs 가 설정 가능 (chat.send opts). 매우 짧게 설정하면 expiresAtMs 도 짧아지나, minMs=2분이 floor.
- DEFAULT_CHAT_RUN_ABORT_GRACE_MS=60_000 (chat-abort.ts:5) 는 grace. 0 으로 설정 못 함 (코드 const).

### Primary-path inversion (CAL-001)

이 FIND 가 성립하려면 어떤 정상 경로가 실패해야 하는가? 정답: maintenance interval 의 timeout 기반 sweep 이 결국 abort 를 수행 → **메모리 누수 측면에서는 실패 없음**. 본 FIND 의 본질은 "abort intent timing 이 disconnect 시점에 발화 안 됨" 으로, lifecycle-gap symptom_type 적합. memory-leak / hang / crash 가설 아님.

### Hot-path-vs-test-path consistency (CAL-003)

테스트가 maintenance interval 의 timer fake 로 sweep 을 강제하면 expiresAtMs 만족 시 abort 가 호출되는 것은 확인된다. 그러나 production hot-path 는 timer 가 실시간이고 minMs=2분 floor 가 적용되므로 실측 abort 지연이 발생. 재현 테스트는 real timer (또는 큰 fake delta) 로 작성해야 함.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/gateway/chat-abort.ts src/gateway/server/ws-connection.ts` → chat-abort.ts 의 본 라인 6주간 fix 없음. ws-connection.ts 도 본 close 핸들러 변경 없음.
- `gh pr list --search "chatAbortControllers OR ownerConnId" --state all` → 해당 cleanup gap 을 다루는 OPEN PR 없음 (인지 범위).
- gateway domain 노트의 error-boundary 셀 §3 도 "cancellation gap (lifecycle-auditor 범위)" 로 본 셀에 위임. 중복 작업 아님.

## Self-check

### 내가 확실한 근거

- `src/gateway/server/ws-connection.ts:351-421` close 핸들러 본문에 chatAbortControllers 미참조 (Read 로 확인).
- `rg -n "ownerConnId" src/gateway/ --type ts` 전수 매치 5건, 모두 권한 판정 또는 등록 코드.
- `src/gateway/chat-abort.ts:71-109` registerChatAbortController 가 ownerConnId 를 entry 에 저장하나 disconnect trigger 부재.
- `src/gateway/server-maintenance.ts:153-174` 의 sweep 만 timeout 기반 abort 수행.
- gateway domain 노트 error-boundary §3 의 "cancellation gap" 위임 기록.

### 내가 한 가정

- `broadcastChatAborted` (chat-abort.ts:130-156) 결과가 다른 connection 들에게는 정상 전파된다는 가정 (broadcast 함수 자체는 본 셀 밖). owner 외 connection 이 같은 sessionKey 를 보고 있다면 그쪽으로는 알림이 가야 정상.
- expiresAtMs 기반 maintenance abort 가 *결국* 실행된다는 가정 — `server-maintenance.ts` 의 interval 이 정지된 상태에서 발생하는 경계 (post-shutdown) 는 본 FIND 와 별개.
- mobile node 의 backgrounding/disconnect 빈도는 production 실측 없음. UX 영향 정량 추정 불가.

### 확인 안 한 것 중 영향 가능성

- `agent.request` (server-methods/agent.ts:1322-1334) 의 동일 패턴은 cross-ref 했으나 별도 FIND 분리 가치 평가 안 함. fix 축 동일 (close 핸들러에 ownerConnId match cleanup 추가) 이므로 single FIND 로 묶을 수 있음.
- `chatRunBuffers`, `chatDeltaSentAt` 등 chat run 부속 자료구조는 abort 시 함께 정리되나, disconnect-only path 에서는 그대로 남아있다 abort 시점까지 누적. memory 셀 분류로는 P4 수준 (maintenance 가 결국 정리).
- 재접속 시 동일 user 가 같은 runId 의 결과를 받을 수 있는지 — `agentRunCache` / replay 경로 영향 미조사 (셀 밖).
