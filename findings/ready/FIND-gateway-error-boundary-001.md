---
id: FIND-gateway-error-boundary-001
cell: gateway-error-boundary
title: node agent.request payloadJSON malformed 시 silent drop 으로 observability gap
file: src/gateway/server-node-events.ts
line_range: 403-408
evidence: "```ts\n      let link: AgentDeepLink | null = null;\n      try {\n    \
  \    link = JSON.parse(evt.payloadJSON) as AgentDeepLink;\n      } catch {\n   \
  \     return;\n      }\n```\n"
symptom_type: error-boundary-gap
problem: node 에서 보낸 agent.request 이벤트의 payloadJSON 이 malformed JSON 인 경우 catch 블록이
  로그 없이 early-return 하여 drop 된 요청을 관측할 수 없다.
mechanism: "1. 원격 node (mobile/desktop client) 가 gateway 에 연결 후 `node.event` RPC 로\
  \ `agent.request`\n   이벤트 발행. payloadJSON 필드가 깨진 JSON 이거나 schema 위반.\n2. `handleGatewayNodeEvent`\
  \ → `handleNodeEvent` → `case \"agent.request\"` 분기로 진입.\n3. line 404 `JSON.parse`\
  \ 가 throw → line 406 catch 블록이 `return;` 만 호출 (로그/메트릭 없음).\n4. 클라이언트는 `node.event`\
  \ RPC 에 `ok: true` 응답을 받지만 (method handler 는 parse\n   실패 전 이미 respond) gateway\
  \ 측에서는 해당 agent.request 가 실행되지 않은 상태.\n5. node 구현에 버그가 있거나 wire 손상이 반복되면 지속적으로 silent\
  \ drop 되어 사용자\n   관점에서 \"메시지를 보냈는데 assistant 가 답을 안 한다\" 가 재현. 디버깅 단서 없음.\n"
root_cause_chain:
- why: 왜 JSON.parse 실패 시 로그 없이 return 인가?
  because: 같은 파일의 다른 parsePayloadObject / parseSessionKeyFromPayloadJSON 도 동일한 패턴(L254-256,
    L272-274)으로 silent return 하므로 일관된 스타일로 채택된 것으로 보인다.
  evidence_ref: src/gateway/server-node-events.ts:250-263
- why: 왜 catch 안에서 최소한 ctx.logGateway.warn 조차 호출하지 않는가?
  because: handleNodeEvent 의 `voice.transcript` 경로(L316-334)는 parsePayloadObject 결과가
    null 일 때 마찬가지로 silent return — 파싱 실패를 node-side 잡음으로 간주하고 gateway 로그를 오염시키지 않으려는
    의도로 읽힌다. 다만 agent.request 는 voice wake 와 달리 사용자가 명시적으로 트리거한 요청이라 trade-off 가 다름.
  evidence_ref: src/gateway/server-node-events.ts:312-378
- why: 왜 상위 handler (server-methods/nodes.ts) 수준에서 catch 후 로깅 가능성 없는가?
  because: nodes.ts L1118 `respondUnavailableOnThrow` 는 throw 가 올라와야 작동하는데 handleNodeEvent
    내부가 이미 throw 를 swallow 하므로 상위 catch 는 트리거되지 않는다. 즉 silent drop 은 이 지점에서 최종 관측
    실패.
  evidence_ref: src/gateway/server-methods/nodes.ts:1102-1147
impact_hypothesis: wrong-output
impact_detail: '정성: malformed agent.request 이벤트 1건당 유저 요청 1건이 응답 없이 사라진다. 재현 조건 —
  node 측 직렬화 버그(예: 유니코드 broken surrogate), packet 손상, 혹은 legacy node 버전의 schema mismatch.
  빈도는 node 클라이언트 품질에 의존 (프로덕션 관측치 없음).'
severity: P3
counter_evidence:
  path: src/gateway/server-node-events.ts
  line: 250-278
  reason: "R-3 Grep:\n- `rg -n \"JSON\\.parse|z\\.parse|safeParse\" src/gateway/`\
    \ 78 hits — 대부분 try 블록 내.\n- `rg -n \"ctx\\.logGateway\\.warn|logGateway\\.warn\"\
    \ src/gateway/server-node-events.ts` 에서\n  `voice session-store update failed`\
    \ (L246) 처럼 warning 로깅은 존재하므로 logger 접근은\n  가능. silent swallow 는 의도적 선택으로 보임.\n\
    R-5 execution condition:\n- L406 catch 는 `conditional-edge` — 정상 node payload\
    \ 는 valid JSON 이므로 실패 경로.\n- 동일 패턴의 다른 catch 블록들(L254, L272)은 unconditional-silent,\
    \ 즉 callers 가 null\n  반환을 기대하는 설계. 이건 정당한 defense-in-depth.\nPrimary-path inversion:\
    \ silent-drop 주장이 성립하려면 \"node 가 malformed JSON 을 보냄\" 이 전제.\nopenclaw 자체 node\
    \ client 는 z.parse 검증된 payload 만 발행(protocol/schema 경유)하므로\n빈도는 매우 낮음. 따라서 이 FIND\
    \ 는 P3 (위생) 수준.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-19'
---
# node agent.request payloadJSON malformed 시 silent drop 으로 observability gap

## 문제

`handleNodeEvent` 의 `agent.request` 분기는 node 가 보낸 이벤트의 `payloadJSON` 필드를 `JSON.parse` 로 역직렬화한다. 이 호출이 throw 하면 catch 블록은 `return;` 만 수행하고 로그·메트릭·에러 응답 어느 것도 남기지 않는다. 그 결과, 잘못된 payload 를 가진 agent.request 는 gateway 측에서 조용히 사라지며, 운영자는 "요청이 실행되지 않았다" 를 추후에 알 방법이 없다.

## 발현 메커니즘

1. node 가 `node.event` RPC 를 요청하며 `{ event: "agent.request", payloadJSON: "<corrupt>" }` 를 보낸다.
2. server-methods/nodes.ts L1102 `node.event` handler 가 `handleNodeEvent(ctx, nodeId, evt)` 를 호출.
3. server-node-events.ts L380 `case "agent.request"` 진입 → L404 `JSON.parse(evt.payloadJSON)` 이 throw.
4. L406 catch `{ return; }` — 외부로 propagate 없음. 로그/메트릭 없음.
5. server-methods/nodes.ts L1145 `respond(true, { ok: true })` 가 실행 — node 는 "정상 수신" 응답을 받지만 실제 agent 작업은 시작되지 않음.

## 근본 원인 분석

이 silent pattern 은 server-node-events.ts 의 다른 parse helper (L250 parseSessionKeyFromPayloadJSON, L265 parsePayloadObject) 와 동일. voice wake 와 같은 best-effort 이벤트에는 적합하지만 사용자-유발 agent.request 에는 observability 요구가 다르다. 로깅을 호출할 수 있는 `ctx.logGateway` 는 동일 함수 내 L246 등에서 이미 사용되고 있어 access 자체는 문제없다. 즉 결여된 것은 설계 판단뿐.

## 영향

- 영향 유형: wrong-output (유저 메시지 silent loss).
- 관측: gateway log, metrics 어디에도 흔적 없음.
- 재현: malformed payload 를 갖는 node.event RPC 를 보내면 즉시 재현.
- 심각도 낮음 (P3): 프로덕션 node client 는 protocol/schema 로 검증된 payload 만 발행하므로 실제 빈도는 매우 낮다.

## 반증 탐색

R-3 Grep 결과:
- `rg -n "try\s*\{" src/gateway/` 80+ hits. server-node-events.ts 는 3개의 silent-return catch 를 갖지만 나머지 파일은 대부분 catch 안에서 warn/error 로깅.
- `rg -n "\.catch\(" src/gateway/` 60+ hits — RPC dispatch 상위(`message-handler.ts:1455`)는 UNAVAILABLE 응답 포함한 full catch. 반면 nested handleNodeEvent 는 inner-most swallow.
- `rg -n "void\s+[a-zA-Z_]+\s*\(" src/gateway/` 45+ hits — 모두 `.catch` 동반 (CAL-007 과 일치, upstream fix 반영된 상태).

R-5 execution condition 분류:
| 경로 | 조건 | 비고 |
|---|---|---|
| L404 JSON.parse | unconditional | agent.request 들어올 때마다 항상 실행 |
| L406 catch return | conditional-edge | malformed payload 에 한함 |
| server-methods/nodes.ts:1118 respondUnavailableOnThrow | unconditional | handleNodeEvent 가 throw 해야 작동 — 하지만 L406 이 swallow |

Primary-path inversion: "silent drop" 주장이 성립하려면 "valid node client 가 실수로 malformed 보냄" 이 필요. openclaw 자체 node 구현은 protocol/schema 검증 후 전송하므로 hot-path 아님. 다만 node 버전 drift/legacy client 시나리오에서는 가능.

기존 테스트 커버리지: `server-node-events.test.ts` 35개 케이스 모두 valid JSON 으로 호출하므로 이 실패 경로는 미커버. counter-evidence 로서는 "observability gap 이 테스트로도 보이지 않는다" 를 방증.

## Self-check

### 내가 확실한 근거
- L404-408 catch 블록이 로그·메트릭·응답 변경 어느 것도 수행하지 않음을 Read 로 확인.
- 동일 파일 L246 이 `ctx.logGateway.warn` 을 이미 사용 — logger 접근 가능함.
- server-methods/nodes.ts:1145 이 handleNodeEvent 완료 후 `ok: true` 응답함을 확인.

### 내가 한 가정
- node client 가 실제로 malformed payload 를 보낼 빈도가 높지 않다 — 추정.
- "silent drop = observability gap" 이 사용자 관점에서 문제라고 평가 — 개발자 관점 판단.

### 확인 안 한 것 중 영향 가능성
- 상위 layer (예: ws frame 검증 ajv) 가 evt.payloadJSON 의 JSON 유효성을 handleNodeEvent 도달 전에 검증하는지 전수 확인 안 함. 만약 검증하면 이 catch 는 dead code 에 가깝고 심각도 더 내려감.
- node.event RPC 의 `payload` (파싱 전 object) 와 `payloadJSON` (문자열) 중 어느 경로가 hot-path 인지 trace 완료 안 함 — server-methods/nodes.ts:1112 에서 둘 다 허용됨을 확인했으나 node client 가 실무에서 어느 쪽을 보내는지는 확인 필요.
