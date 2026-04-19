---
id: FIND-gateway-memory-002
cell: gateway-memory
title: '`nodeWakeById` set 이 APNs registration 검증 전에 실행되어 미등록 nodeId 누수'
file: src/gateway/server-methods/nodes.ts
line_range: 308-336
evidence: "```ts\nexport async function maybeWakeNodeWithApns(\n  nodeId: string,\n\
  \  opts?: { force?: boolean; wakeReason?: string },\n): Promise<NodeWakeAttempt>\
  \ {\n  const state = nodeWakeById.get(nodeId) ?? { lastWakeAtMs: 0 };\n  nodeWakeById.set(nodeId,\
  \ state);\n\n  if (state.inFlight) {\n    return await state.inFlight;\n  }\n\n\
  \  const now = Date.now();\n  const force = opts?.force === true;\n  if (!force\
  \ && state.lastWakeAtMs > 0 && now - state.lastWakeAtMs < NODE_WAKE_THROTTLE_MS)\
  \ {\n    return { available: true, throttled: true, path: \"throttled\", durationMs:\
  \ 0 };\n  }\n\n  state.inFlight = (async () => {\n    const startedAtMs = Date.now();\n\
  \    const withDuration = (attempt: Omit<NodeWakeAttempt, \"durationMs\">): NodeWakeAttempt\
  \ => ({\n      ...attempt,\n      durationMs: Math.max(0, Date.now() - startedAtMs),\n\
  \    });\n\n    try {\n      const registration = await loadApnsRegistration(nodeId);\n\
  \      if (!registration) {\n        return withDuration({ available: false, throttled:\
  \ false, path: \"no-registration\" });\n      }\n```\n"
symptom_type: memory-leak
problem: '`maybeWakeNodeWithApns` 는 진입 즉시 `nodeWakeById.set(nodeId, state)` 를 수행하지만
  (line 312-313), `loadApnsRegistration` 실패(`path: "no-registration"`) 경로에서도 엔트리는
  남는다. 엔트리 삭제는 `clearNodeWakeState` — WS `close` 이벤트에서 해당 nodeId 가 등록된 경우에만 실행된다.
  등록되지 않은 / 재페어링으로 바뀐 / 오탈자가 섞인 nodeId 로 wake 호출이 들어오면 엔트리가 영속한다.'
mechanism: "1. 인증된 operator 가 `node.pending.enqueue` RPC 를 arbitrary `nodeId` 로 호출.\
  \ 혹은\n   `node.invoke` 가 wake 경로를 트리거 (server-methods/nodes.ts:920, 943, 1050).\n\
  2. `maybeWakeNodeWithApns(nodeId)` 진입 → line 312-313 이 즉시 `nodeWakeById.set(nodeId,\
  \ state)`.\n3. line 333 `await loadApnsRegistration(nodeId)` 가 `null` 반환 (nodeId\
  \ 에 대한 APNs 등록 없음 /\n   이전에 `clearApnsRegistrationIfCurrent` 로 제거됨 / 사용자 오탈자).\n\
  4. line 334-336 `path: \"no-registration\"` 으로 조기 반환. 이 시점에서 `nodeWakeById` 에는 이미\n\
  \   **의미 없는** 엔트리 `{ lastWakeAtMs: 0 }` 가 남아 있음.\n5. 해당 nodeId 에 연결된 실제 WS 가 없으므로\
  \ `clearNodeWakeState` (호출 위치\n   server/ws-connection.ts:327) 는 트리거되지 않는다.\n6.\
  \ operator 가 같은 nodeId 또는 다른 미등록 nodeId 로 호출을 반복하면 `nodeWakeById` /\n   `nodeWakeNudgeById`\
  \ (L73, 483) 엔트리가 누적.\n7. 동일 구조: `nodeWakeNudgeById.set(nodeId, Date.now())` (L483)\
  \ 역시 `sendApnsAlert` 성공 시에만\n   발동 — 이 맵은 `clearNodeWakeState` 로만 지워짐.\n"
root_cause_chain:
- why: 왜 set 이 registration 검증 이전에 실행되는가?
  because: '`state.inFlight` throttle / dedupe 로직(L315-323)이 맵에 엔트리가 있어야 동작하도록 설계됨
    — 동일 nodeId 에 대한 동시 wake 호출 하나로 coalesce 하기 위함. 따라서 set 은 registration 결과와 무관하게
    수행해야 한다는 제약.'
  evidence_ref: src/gateway/server-methods/nodes.ts:312-323
- why: 왜 `no-registration` 반환 이후 정리하지 않는가?
  because: L315 state.inFlight 반환 전에 set 된 state 는 caller 가 동시에 또 요청하면 재사용해야 하기 때문.
    하지만 "miss + no-reg → delete" 정도는 안전하게 추가 가능하다. 현재 L333-336 경로에서 엔트리 정리 로직 부재.
  evidence_ref: src/gateway/server-methods/nodes.ts:333-336
- why: 왜 WS close 경로 외에는 cleanup 이 없는가?
  because: '`clearNodeWakeState` (L525-528) 는 호출자가 server/ws-connection.ts:327 하나뿐.
    "role=node 의 연결이 실제로 끊길 때" 를 기준으로 cleanup 이 설계됨. 즉 wake 를 호출한 nodeId 가 한 번이라도
    WS 로 연결된 적이 없다면 cleanup 시점이 없다.'
  evidence_ref: src/gateway/server/ws-connection.ts:316-328
- why: 왜 operator 가 arbitrary nodeId 를 보낼 수 있는가?
  because: '`node.pending.enqueue` (nodes-pending.ts:60-158) 는 params.nodeId 를 operator
    인풋으로 받는다. validator `validateNodePendingEnqueueParams` 는 타입 체크만 수행 — nodeId 가
    현재 유효한 paired device 인지 교차 검증하지 않는다. 오타/재페어링 후 legacy nodeId 가 들어올 가능성 있음.'
  evidence_ref: src/gateway/server-methods/nodes-pending.ts:60-88
impact_hypothesis: memory-growth
impact_detail: "정량 상한 (관측치 없음):\n- 누적 속도 = 미등록 nodeId 로 들어오는 wake 호출 수. 정상 사용에서는 매우\
  \ 낮지만, operator\n  tooling 이 오래된 nodeId 를 재사용하거나 스크립팅/자동화에서 재페어링 이후 stale id 를\n\
  \  재활용하면 N 일에 N 엔트리.\n- 엔트리 크기: `NodeWakeState = { lastWakeAtMs: number; inFlight?:\
  \ Promise<NodeWakeAttempt> }`\n  — 40~100 bytes. `nodeWakeNudgeById` 엔트리는 `number`\
  \ 하나 (더 작음).\n- 가장 현실적 리스크: 장기 구동 + paired device churn 이 있는 설치에서 수백~수천 엔트리. OOM\n\
  \  까지는 아니고 slow heap growth.\n"
severity: P3
counter_evidence:
  path: src/gateway/server-methods/nodes.ts
  line: 525-528
  reason: "R-3 Grep (audit HEAD 8879ed153d):\n```\nrg -n \"nodeWakeById\\.(delete|clear)\"\
    \ src/gateway/\n  → nodes.ts:526    (clearNodeWakeState 본체)\n  # 기타 delete 경로\
    \ 없음.\n\nrg -n \"nodeWakeNudgeById\\.(delete|clear)\" src/gateway/\n  → nodes.ts:527\
    \    (clearNodeWakeState 본체)\n  # 기타 delete 경로 없음.\n\nrg -n \"clearNodeWakeState\"\
    \ src/gateway/\n  → ws-connection.ts:327  (WS close, role=node + registered nodeId)\n\
    \  → nodes.ts:525          (정의)\n  # 호출자 한 곳 뿐.\n\nrg -n \"(cap|max|limit).*nodeWake\"\
    \ src/gateway/\n  → NODE_WAKE_THROTTLE_MS (15s), NODE_WAKE_NUDGE_THROTTLE_MS (10min)\n\
    \  # 크기 상한 없음.\n\nrg -n \"while.*nodeWakeById\\.size|nodeWakeById\\.size\\s*>\"\
    \ src/gateway/\n  → match 없음.\n```\n\nR-5 execution condition 분류:\n| 경로 | 조건 |\
    \ 비고 |\n|---|---|---|\n| L313 `nodeWakeById.set(...)` | unconditional entry |\
    \ 호출마다 set |\n| L526 `nodeWakeById.delete(nodeId)` | conditional (WS close + role=node\
    \ + registered) | 미등록 nodeId 에 대해서는 fire 안 함 |\n| L527 `nodeWakeNudgeById.delete(nodeId)`\
    \ | 동 L526 |\n| cap/FIFO/TTL evict | 없음 |\n\nPrimary-path inversion: \"누적 안 된다\"\
    \ 가 참이려면 모든 wake 호출이 최종적으로 WS 연결\ncycle 을 거친 nodeId 에 대해서만 이뤄져야 한다. 그러나 `maybeWakeNodeWithApns`\
    \ 는\n`loadApnsRegistration(nodeId) → null` 경로를 **정상 반환 경로** 로 취급\n(L334 `{ available:\
    \ false, path: \"no-registration\" }`). 즉 \"미등록 nodeId 로 호출되는 것\" 을\n설계가 명시적으로\
    \ 허용. 따라서 누적 경로는 hot-path 로 간주해야 한다.\n\n이미 cleanup 있는지: `clearStaleApnsRegistrationIfNeeded`\
    \ (L374) 는 APNs registration 자체를\n지우지만 `nodeWakeById` 는 건드리지 않음. 둘이 분리된 cleanup\
    \ 경로라 gap 존재.\n\n\"intentional\" 주석 없음: L312 부근에 \"always set before registration\
    \ check\" 의도를 설명하는\n주석 부재.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-19'
---
# `maybeWakeNodeWithApns` 가 registration 체크 이전에 `nodeWakeById` 에 엔트리를 set 하여 미등록 nodeId 에 대한 누수 가능

## 문제

`nodeWakeById` / `nodeWakeNudgeById` 는 APNs wake 호출에 대한 throttle/dedupe state 를 `nodeId` 로 보관한다. `maybeWakeNodeWithApns` 는 진입 즉시 맵에 엔트리를 `set` 한 뒤 (L312-313), `loadApnsRegistration` 결과를 보고 미등록이면 `path: "no-registration"` 으로 조기 반환한다. 삭제는 `clearNodeWakeState` — WS `close` 이벤트 중 해당 nodeId 가 registered 인 경우에만 실행. 미등록/재페어링/오탈자 nodeId 로 wake 가 호출되면 엔트리가 정리되지 않는다.

## 발현 메커니즘

1. 인증된 operator 가 `node.pending.enqueue` 또는 `node.invoke` RPC 호출 (nodeId 는 operator 인풋).
2. `maybeWakeNodeWithApns(nodeId)` 진입 → L312-313 `nodeWakeById.set(nodeId, state)`.
3. L333 `await loadApnsRegistration(nodeId)` 가 null 반환 (APNs 미등록 / 이전에 clear 됨).
4. L334-336 에서 `path: "no-registration"` 으로 조기 반환. `nodeWakeById` 엔트리는 남음.
5. 해당 nodeId 의 WS 연결이 없으므로 `clearNodeWakeState` 가 절대 호출되지 않음.
6. 동일 또는 다른 미등록 nodeId 로 호출이 반복되면 엔트리 누적. `nodeWakeNudgeById` 는 `sendApnsAlert` 성공 시에만 set (L483) — 일반 경로는 주로 `nodeWakeById` 에 누수.

## 근본 원인 분석

설계상 L312-313 의 unconditional set 은 `state.inFlight` 공유를 통해 동일 nodeId 에 대한 concurrent wake 호출을 하나로 통합하기 위함이다. 그러나 `no-registration` 경로에서 엔트리를 지우는 cleanup 이 추가되지 않았다. cleanup 은 WS close 경로에만 존재 — 전제는 "wake 가 호출되는 nodeId 는 언젠가 WS 연결을 경험한다" 지만, `maybeWakeNodeWithApns` 는 `no-registration` 반환을 정상 결과로 둔다 (L334). 따라서 전제가 깨진다.

`node.pending.enqueue` validator 는 nodeId 를 operator 인풋으로 받으며 현재 paired 상태인지 교차 검증하지 않는다 (nodes-pending.ts:60-88). operator tooling 이 재페어링 후 이전 nodeId 를 재사용하거나 스크립팅 오류로 잘못된 id 를 보내면 누수 경로가 활성화된다.

## 영향

- 영향 유형: **memory-growth** (slow leak, 엔트리 크기 작음).
- 관측: `nodeWakeById` / `nodeWakeNudgeById` size 가 미등록 nodeId 호출 횟수에 비례하여 증가.
- 재현: 임의 nodeId 로 `node.pending.enqueue` 를 반복 호출 → `nodeWakeById.has(nodeId)` 영구 true.
- severity P3: 엔트리 크기 ~100B 로 작고 누적 속도가 auth-gated 한 호출 빈도에 의존. 단, `clearStaleApnsRegistrationIfNeeded` (L374) 로 registration 이 지워진 후에도 `nodeWakeById` 엔트리는 살아남아 "좀비" 가 될 수 있음.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 으로 `nodeWakeById.(delete|clear)` / `clearNodeWakeState` 탐색. delete 경로는 `clearNodeWakeState` 하나, 호출자는 `ws-connection.ts:327` 하나. WS close 이벤트 + role=node + registered nodeId 삼중 조건. 미등록 nodeId 에 대해서는 fire 안 함.

**카테고리 2 (외부 경계 장치)**: `clearStaleApnsRegistrationIfNeeded` (L374) 는 APNs registration 자체를 정리하지만 `nodeWakeById` 엔트리는 건드리지 않음. 두 맵이 decoupled cleanup 이라 gap 존재. cron sweeper / maintenance interval 에서 정리하는 로직 없음 (R-3 Grep `while.*nodeWakeById.size` 0 hits).

**카테고리 3 (호출 맥락)**: `node.pending.enqueue` 는 인증된 operator 인풋 기반. 빈도는 낮으나 스크립팅/자동화 환경에서 꾸준히 발생.

**카테고리 4 (기존 테스트)**: `nodes.invoke-wake.test.ts:332-351` 가 `clearNodeWakeState` 의 동작을 검증 — "WS close 기반 cleanup" 만 테스트. "미등록 nodeId 호출 이후 엔트리가 남는다" 는 누수 시나리오는 커버되지 않음.

**카테고리 5 (주석/의도)**: L312 부근 주석 없음. `no-registration` 경로에서 cleanup 생략이 의도적이라는 표시 없음.

**Primary-path inversion**: "누적 안 된다" 가 참이려면 모든 wake 호출이 최종적으로 WS 연결 cycle 을 거친 nodeId 에 대해서만 이뤄져야 한다. 그러나 설계는 `no-registration` 을 정상 반환 경로로 허용 — 성립 안 함.

## Self-check

### 내가 확실한 근거
- `src/gateway/server-methods/nodes.ts:312-336, 483, 525-528` Read 로 확인.
- `src/gateway/server/ws-connection.ts:316-328` 에서 `clearNodeWakeState` 호출처 단일 확인.
- R-3 Grep 으로 cap/TTL/prune 경로 0 매치 확인.

### 내가 한 가정
- operator tooling 이 미등록 nodeId 로 wake 를 호출할 빈도 — 추정. 실제 관측치 없음.
- 엔트리 크기가 약 100B — `NodeWakeState` 구조 기반 추정.

### 확인 안 한 것 중 영향 가능성
- operator 가 실제로 arbitrary nodeId 를 보낼 수 있는지 (RBAC 범위) 확인 안 함. 만일 상위 auth 레이어에서 paired nodeId allowlist 강제하면 이 FIND 의 빈도는 더 낮아진다.
- `nodeWakeNudgeById` 의 set 경로 (L483) 가 `sendApnsAlert` 성공 시에만 발동 — 미등록 nodeId 는 `no-registration` 으로 조기 반환하므로 nudge 맵에는 덜 영향. 주 누수는 `nodeWakeById`.
- `state.inFlight` 가 promise resolve 이후 `finally` 등으로 nullify 되는지 직접 확인 안 함 — 만약 promise ref 가 state 에 계속 잡혀 있으면 엔트리당 메모리가 증가한다 (추가 drift risk).
