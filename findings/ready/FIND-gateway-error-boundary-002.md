---
id: FIND-gateway-error-boundary-002
cell: gateway-error-boundary
title: post-handshake 에서 ws message handler outer catch 가 connection 을 닫지 않고 로그만 남긴다
file: src/gateway/server/ws-connection/message-handler.ts
line_range: 1459-1465
evidence: "```ts\n    } catch (err) {\n      logGateway.error(`parse/handle error:\
  \ ${String(err)}`);\n      logWs(\"out\", \"parse-error\", { connId, error: formatForLog(err)\
  \ });\n      if (!getClient()) {\n        close();\n      }\n    }\n```\n"
symptom_type: error-boundary-gap
problem: socket.on("message") 의 최상위 catch 는 pre-handshake 에 한해 close() 를 호출한다. post-handshake
  (client 존재) 상태에서 synchronous throw 가 발생하면 error 로그만 남기고 connection 을 유지한다. 클라이언트는
  응답을 받지 못하고 timeout 까지 대기할 수 있다.
mechanism: "1. handshake 완료된 ws connection 에서 client 가 RPC frame 전송.\n2. JSON.parse\
  \ 성공 → validateRequestFrame 통과 → `void (async () => { await handleGatewayRequest(...)\
  \ })().catch(...)` 로 진입 직전 또는 직후에 synchronous throw 발생 가능 지점:\n   - logWs(\"in\"\
  , \"req\", ...) 중 serializer throw (L1385)\n   - client.usesSharedGatewayAuth 분기\
  \ 중 setCloseCause 의존 속성 접근 throw (L1386-1400)\n   - respond closure 내부 `errorShape`\
  \ / `isUnauthorizedRoleError` 호출 중 throw\n3. outer catch (L1459) 로 전파.\n4. `getClient()`\
  \ 는 non-null (handshake 완료) → L1462 조건 false → close 안 함.\n5. 클라이언트는 요청 id 에 대한\
  \ 응답을 받지 못한 채 무한 대기. Gateway 는 log 1줄 외에 아무 조치 없음.\n"
root_cause_chain:
- why: 왜 post-handshake 에서 close 를 하지 않는가?
  because: pre-handshake 단계에서 JSON.parse 실패는 악의적 probe/scanner 가능성이 높아 연결을 끊어야 한다는
    판단. 반면 post-handshake 는 정상 client 가 일시적 잘못된 frame 을 보낼 수 있다고 보고 연결 유지가 더 우호적이라는
    trade-off.
  evidence_ref: src/gateway/server/ws-connection/message-handler.ts:1459-1465
- why: 왜 client 에게 error 응답을 돌려주지 않는가?
  because: catch 는 req.id 를 복원할 컨텍스트를 잃은 상태 — `const req = parsed;` (L1384) 는 try
    블록 안 지역 변수. catch 에서 접근 불가. 따라서 특정 request 에 대한 응답 매핑 불가능하다고 포기한 것으로 보인다.
  evidence_ref: src/gateway/server/ws-connection/message-handler.ts:1384-1458
- why: 왜 `.catch((err) => respond(false, UNAVAILABLE))` (L1455) 와 같은 방어가 이 outer 에는
    없는가?
  because: L1455 catch 는 `handleGatewayRequest` 의 async reject 을 받는다. 그 외 outer 동기
    throw (dispatch 이전 pre-dispatch 로직) 는 이 catch 로 잡히지 않는다. 즉 pre-dispatch 단계의 sync
    throw 만 outer catch 로 흘러가고, 거기서는 req 복원 실패로 응답 포기.
  evidence_ref: src/gateway/server/ws-connection/message-handler.ts:1446-1458
impact_hypothesis: hang
impact_detail: '정성: 클라이언트 RPC 가 timeout 까지 대기 (기본 gateway 클라이언트 타임아웃은 src/gateway/call.ts
  에서 결정). 실제로 pre-dispatch 경로의 sync throw 는 매우 드물어 (모든 접근은 null-safe) 프로덕션 관측 증거 없음.'
severity: P3
counter_evidence:
  path: src/gateway/server/ws-connection/message-handler.ts
  line: 1446-1458
  reason: 'R-3 Grep:

    - `rg -n "try\s*\{" src/gateway/server/ws-connection/message-handler.ts` → 4 try
    블록. L1318(hello send)과 L1325(bootstrap bookkeeping)은 inner try 로 개별 실패 처리. L324-1459
    outer try 는 pre/post handshake 전체 커버.

    - `rg -n "\.catch\(" src/gateway/server/ws-connection/message-handler.ts` → 6
    hits. L1455 가 RPC dispatch async reject 의 unconditional guard. 나머지는 best-effort
    side-effect (refreshHealth, updatePairedNodeMetadata 등).


    R-5 execution condition:

    | 경로 | 조건 | 비고 |

    |---|---|---|

    | L1455 .catch(respond(UNAVAILABLE)) | unconditional | handleGatewayRequest async
    reject 시 모두 응답 |

    | L1459 outer catch log-only | conditional-edge | pre-dispatch sync throw 만 도달
    (드묾) |

    | L1462 close() | conditional-edge | pre-handshake 에 한함 |


    Primary-path inversion: "hang 주장" 이 성립하려면 (a) pre-dispatch 경로에서 synchronous throw,
    (b) handshake 는 이미 완료. validateRequestFrame ajv, logWs, setCloseCause 모두 throw
    가능성이 낮게 작성됨 — 실제 hot-path 에서 이 catch 가 트리거되는 빈도는 추정 불가 (증거 없음).


    production hot-path: handshake 성공한 유효 클라이언트가 정상 JSON frame 을 보내는 것이 정상 경로. 그 경로에서
    pre-dispatch sync throw 는 입증된 bug 없음. 이 FIND 는 hardening 수준으로 P3.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-19'
---
# post-handshake 에서 ws message handler outer catch 가 connection 을 닫지 않고 로그만 남긴다

## 문제

`attachGatewayWsMessageHandler` 의 `socket.on("message", async (data) => { ... })` 핸들러는 L324 부터 L1465 까지의 거대한 try-catch 블록으로 모든 in-flight 처리를 감싼다. Outer catch (L1459-1465) 는 pre-handshake 에서 throw 된 경우 connection 을 닫지만, post-handshake (getClient() 가 non-null) 에서 throw 된 경우에는 log 만 남기고 connection 을 그대로 둔다. 클라이언트는 해당 request id 에 대한 응답을 받지 못하므로 client-side timeout 까지 무한 대기.

## 발현 메커니즘

post-handshake RPC 처리 경로는 다음 순서:
1. JSON.parse → parsed (L325)
2. validateRequestFrame 검사 (L1372)
3. `const req = parsed;` (L1384)
4. logWs("in", ...) (L1385)
5. sharedGatewayAuth 체크 (L1386-1400)
6. respond closure 정의 (L1401)
7. `void (async () => handleGatewayRequest(...))().catch(respondUNAVAILABLE)` (L1446-1458)

위 2~6 단계는 synchronous throw 가능 지점이지만 `.catch` 가 걸린 IIFE (7단계) 밖에 있다. 따라서 이 구간의 throw 는 outer catch (L1459) 로 직행하고, post-handshake 이므로 close 안 됨 + respond 안 됨 → hang.

## 근본 원인 분석

L1459 catch 는 두 책무를 겸임한다: (a) pre-handshake 악의적 probe 차단, (b) post-handshake 일반 예외 관용. 이 두 역할은 실은 양립하지 않는다. req.id 를 복원할 컨텍스트가 catch 블록에 없어 error 응답을 돌려줄 수 없고, connection 유지가 client 관점에서 hang 을 유발할 수 있다.

IIFE `.catch` (L1455) 는 async dispatch 의 reject 을 모두 잡아 UNAVAILABLE 응답을 보낸다 — 이 방어는 `unconditional` 하고 잘 작동한다. 하지만 IIFE 바깥의 pre-dispatch synchronous throw 는 이 방어를 지나치므로 일관되지 않은 error 처리가 된다.

## 영향

- 영향 유형: hang.
- 실제 빈도: pre-dispatch 단계의 synchronous throw 경로가 매우 좁아 production-observed 증거 없음.
- 영향 폭: 개별 connection 에 국한 (outer handler 는 per-socket). 다른 connection 에는 영향 없음.
- P3 (위생).

## 반증 탐색

1. 숨은 방어: IIFE `.catch` (L1455) 가 대부분의 handler 예외를 이미 잡음. pre-dispatch sync throw 는 좁은 창.
2. 기존 테스트: `server-channels.test.ts`, `ws-connection.test.ts` 는 main handshake path 커버. pre-dispatch sync throw 시나리오 미커버.
3. 호출 빈도: validateRequestFrame, logWs, setCloseCause 는 null-safe 로 설계되어 throw 가능성 낮음.
4. 설정: 관련 config 없음.
5. 주변 코드 맥락: 파일 최상단 주석/L1460 로그 메시지 "parse/handle error" 는 이 catch 가 주로 pre-handshake 악성 trafffic 을 의식하고 쓰였음을 시사 — post-handshake 케이스는 후속 설계 변경 중에 노출된 gap 일 가능성.

## Self-check

### 내가 확실한 근거
- L1459-1465 catch 블록 내용 전체 확인. `if (!getClient())` 가드가 post-handshake 에서 close 를 막음.
- L1455 IIFE `.catch` 가 async dispatch reject 에 한해 UNAVAILABLE 응답 — pre-dispatch sync throw 는 별도 경로.
- L1384 req 는 try 블록 내부 지역 변수 — catch 블록에서 접근 불가.

### 내가 한 가정
- "pre-dispatch sync throw 가 실제로 발생한다" 는 증거 없음 — 이 FIND 는 코드-구조 gap 주장이며 hot-path 재현은 없다.
- client-side timeout 이 유일한 복구 경로라는 것 — 실제 client 구현은 다른 heartbeat/cancel 메커니즘이 있을 수 있음.

### 확인 안 한 것 중 영향 가능성
- client.ts 수준의 RPC timeout 기본값과 ping/pong idle detection 완전히 trace 안 함. client-side 에서 이 hang 이 detect 되어 자동 reconnect 할 가능성 있음. 그 경우 영향 더 낮음.
- handshake 완료 후 L1264 setSocketMaxPayload 실패 같은 corner case 가 outer catch 로 흘러가는지 별도 확인 필요.
- CAL-003 hot-path 검증: 이 FIND 가 주장하는 경로가 production 에서 실재 taken 되는지 증거 없음. PR 제출 시에는 더 구체적 재현 필요.
