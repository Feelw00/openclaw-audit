---
id: FIND-gateway-concurrency-003
cell: gateway-concurrency
title: 'chat.send attachment path: chatAbortControllers check/set 간 I/O await race'
file: src/gateway/server-methods/chat.ts
line_range: 1920-1968
evidence: "```ts\n    const activeExisting = context.chatAbortControllers.get(clientRunId);\n\
  \    if (activeExisting) {\n      respond(true, { runId: clientRunId, status: \"\
  in_flight\" as const }, undefined, {\n        cached: true,\n        runId: clientRunId,\n\
  \      });\n      return;\n    }\n    if (normalizedAttachments.length > 0) {\n\
  \      const modelRef = resolveSessionModelRef(cfg, entry, agentId);\n      const\
  \ supportsImages = await resolveGatewayModelSupportsImages({\n        loadGatewayModelCatalog:\
  \ context.loadGatewayModelCatalog,\n        provider: modelRef.provider,\n     \
  \   model: modelRef.model,\n      });\n      try {\n        const parsed = await\
  \ parseMessageWithAttachments(inboundMessage, normalizedAttachments, {\n       \
  \   maxBytes: 5_000_000,\n          log: context.logGateway,\n          supportsImages,\n\
  \        });\n        parsedMessage = parsed.message;\n        parsedImages = parsed.images;\n\
  \        imageOrder = parsed.imageOrder;\n        offloadedRefs = parsed.offloadedRefs;\n\
  \      } catch (err) {\n        respond(\n          false,\n          undefined,\n\
  \          errorShape(\n            err instanceof MediaOffloadError ? ErrorCodes.UNAVAILABLE\
  \ : ErrorCodes.INVALID_REQUEST,\n            String(err),\n          ),\n      \
  \  );\n        return;\n      }\n    }\n\n    try {\n      const abortController\
  \ = new AbortController();\n      context.chatAbortControllers.set(clientRunId,\
  \ {\n        controller: abortController,\n        sessionId: entry?.sessionId ??\
  \ clientRunId,\n        sessionKey: rawSessionKey,\n        startedAtMs: now,\n\
  \        expiresAtMs: resolveChatRunExpiresAtMs({ now, timeoutMs }),\n        ownerConnId:\
  \ normalizeOptionalText(client?.connId),\n        ownerDeviceId: normalizeOptionalText(client?.connect?.device?.id),\n\
  \      });\n```\n"
symptom_type: concurrency-race
problem: '동일 `clientRunId` (= `idempotencyKey`) 로 attachments 가 있는 `chat.send` 가

  두 번 도착하면 L1920 의 `chatAbortControllers` check 통과 후 attachment

  parsing (`resolveGatewayModelSupportsImages` + `parseMessageWithAttachments`) 의

  real I/O await 구간에서 두 번째 호출이 race window 를 통과한다. 두 호출자가

  각자 `chatAbortControllers.set` 을 수행하고 각자 agent run 을 spawn → **동일 runId 로 2개의 chat
  실행**.

  '
mechanism: '1. T1: `const activeExisting = context.chatAbortControllers.get(clientRunId)`
  (L1920, sync) → undefined.

  2. T1: `normalizedAttachments.length > 0` → enter L1928 branch.

  3. T1: `await resolveGatewayModelSupportsImages(...)` (L1930) — model catalog load
  (I/O).

  4. T1: `await parseMessageWithAttachments(...)` (L1936) — attachment parsing (fs/stream
  I/O).

  5. 이 사이에 T2 (동일 clientRunId) 진입: L1920 → undefined (T1 아직 set 안 함) → 동일 branch →
  동일 await 경로.

  6. T1: `context.chatAbortControllers.set(clientRunId, ...)` (L1960).

  7. T2: `context.chatAbortControllers.set(clientRunId, ...)` (L1960, T1 덮어쓰기).

  8. T1 과 T2 모두 chat run lifecycle 진행 → agent pipeline 이 runId=clientRunId 로 2회 시작
  → 이벤트 buffer / lifecycle event 가 중첩.

  '
root_cause_chain:
- why: 왜 attachment 경로에서 L1920 check 가 race 를 막지 못하는가?
  because: L1920 은 sync get 이지만 그 직후 L1930/L1936 의 real I/O await 이 있어 check 와 L1960
    의 set 사이에 microtask + I/O 지연 발생. attachment 파싱은 I/O-bound (supportsImages 조회 +
    파일/스트림 처리) 라 window 가 길다.
  evidence_ref: src/gateway/server-methods/chat.ts:1920, 1930, 1936, 1960
- why: 왜 상위 dedupe.get("chat:...") (L1912) 도 막지 못하는가?
  because: chat dedupe 엔트리는 chat run 완료 시 setGatewayDedupeEntry 로 저장 — in-flight 동안은
    undefined. 완료-cache 만으로는 중복 spawn 을 막을 수 없다.
  evidence_ref: src/gateway/server-methods/agent-wait-dedupe.ts:206-220
- why: 왜 no-attachment 경로는 race 가 없는가?
  because: L1928 의 branch 가 false 이면 L1920 (sync check) 와 L1960 (sync set) 사이에 await
    이 없어 single-thread JS 의 원자 실행 구간. 두 번째 caller 는 반드시 set 이후에 도착하여 L1920 check 를
    통과 못함.
  evidence_ref: src/gateway/server-methods/chat.ts:1920-1968
- why: 왜 attachment path 에 별도 guard 가 없는가?
  because: L1920 의 설계 의도는 "완료 dedupe 이전의 in-flight duplicate" 를 막는 것이지만, attachment
    parsing 을 guard 밖에 둔 것이 보호 window 를 무효화. 가드를 set 바로 위로 끌어올리거나 attachment parsing
    을 work IIFE 안으로 옮겨야 한다.
  evidence_ref: src/gateway/server-methods/chat.ts:1928-1956
impact_hypothesis: wrong-output
impact_detail: '정량: attachment 포함 chat.send 재시도가 parseMessageWithAttachments 완료

  (수십~수백 ms, 파일 크기에 비례) 이전에 두 번째 RPC 가 도착하면 동일

  clientRunId 로 두 개의 agent run 이 시작됨. 결과로 LLM 호출이 2회, 비용 +

  응답 중복 broadcast.

  정성: attachment 있는 chat.send 는 image/문서 첨부 chat UI 에서 가장 흔한

  경로. 네트워크 jitter 로 retry 되면 사용자 측에서는 같은 메시지에 대해

  agent 응답이 두 번 관찰되거나 동일 runId 의 lifecycle 이벤트 충돌.

  '
severity: P1
counter_evidence:
  path: src/gateway/server-methods/chat.ts
  line: '1920'
  reason: 'R-3 Grep 확인:

    - `rg -n "chatAbortControllers\.(set|get|delete)" src/gateway/` → 5 hits. set
    은 chat.ts:1960 단 한 곳. guard (Mutex) 없음.

    - `rg -n "Mutex|Semaphore|AsyncLock|withSerialized" src/gateway/server-methods/chat.ts`
    → 0 matches.

    - `rg -n "Promise\.race\(" src/gateway/server-methods/chat.ts` → 0 matches.

    R-5 execution condition 분류 (attachment path):

    | 경로 | 조건 | 위치 |

    |---|---|---|

    | `chatAbortControllers.get` sync 체크 | unconditional (L1928 branch 진입 여부와 무관)
    | chat.ts:1920 |

    | `resolveGatewayModelSupportsImages` await | conditional (attachments.length
    > 0) | chat.ts:1930 |

    | `parseMessageWithAttachments` await | conditional (attachments.length > 0) |
    chat.ts:1936 |

    | `chatAbortControllers.set` | unconditional | chat.ts:1960 |

    no-attachment branch 에서는 L1920→L1960 사이 await 이 없어 **unconditional guard** 성립
    (CAL-001 의 제대로 된 guard 예시). attachment branch 에서만 race 성립 — **conditional race**
    이라 FIND 로 남기되 severity 는 P1.

    R-7 hot-path 재현: 프로덕션 chat.send 의 attachment 경로는 실제 모바일/desktop client 의 image
    upload 에서 자주 taken. synthetic-only 아님.

    상위 dedupe (chat:clientRunId) 는 완료 후에만 채워지므로 in-flight 방어 불가.

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: '2026-04-22'
domain_notes_ref: domain-notes/gateway.md
cross_refs:
- FIND-gateway-concurrency-001
- FIND-gateway-concurrency-002
---
# chat.send attachment path race — chatAbortControllers check-then-set separated by image/attachment I/O

## 문제

`chat.send` 핸들러 (`server-methods/chat.ts:1764`) 는 재시도 dedupe 를 위해
L1920 에서 `chatAbortControllers` 존재 여부를 sync 확인하고 L1960 에서 set
한다. **attachment 가 있는 경우** 그 사이에 `resolveGatewayModelSupportsImages`
와 `parseMessageWithAttachments` 의 real I/O await 이 끼어들어 race window 가
생긴다. 동일 `clientRunId` 로 재전송된 chat.send 2개가 이 window 에서
만나면 둘 다 L1920 의 "not in-flight" 판단을 받고 각자 agent run 을 spawn.

## 발현 메커니즘

```
T1: enter chat.send
T1: L1912 dedupe.get('chat:X') → undefined
T1: L1920 chatAbortControllers.get('X') → undefined
T1: enter L1928 (attachments.length > 0)
T1: await resolveGatewayModelSupportsImages       [L1930]
                T2: enter chat.send (retry same idempotencyKey)
                T2: L1920 → undefined (T1 미저장)
                T2: enter L1928
                T2: await resolveGatewayModelSupportsImages
T1: await parseMessageWithAttachments              [L1936] — 수십~수백 ms
                T2: await parseMessageWithAttachments
T1: chatAbortControllers.set(X, {...})             [L1960]
                T2: chatAbortControllers.set(X, {...}) 덮어쓰기
T1: agent run 시작 (runId=X)
                T2: agent run 시작 (runId=X) — duplicate
```

**결과**: LLM 호출 2회, 동일 runId 로 두 stream 이 동시에 response delta
이벤트를 emit. broadcast/session state 가 섞여 사용자에게 interleaved
text chunk 가 보이거나, 둘 중 하나의 lifecycle end 이벤트만 기록되고
다른 하나는 agentRunStarts leak (FIND-gateway-memory-003 과 연계).

## 근본 원인 분석

1. **check 와 set 사이의 real I/O**: L1920 의 sync check 이후 attachment
   branch 에서 2 회 await (model catalog + media parsing). 단일 thread JS
   라도 microtask + I/O 보류로 두 caller 가 같은 check 를 통과.
2. **완료 dedupe 는 in-flight 를 못 막음**: L1912 의 `context.dedupe.get`
   cache 는 `setGatewayDedupeEntry` 에서 terminal snapshot 만 저장. pending
   상태는 추적 안 함. agent-wait-dedupe.ts 의 `readTerminalSnapshotFromDedupeEntry`
   가 `accepted/started/in_flight` 에 대해 `null` 반환하는 것도 같은 이유.
3. **attachment parsing 을 guard 밖에 둔 설계 결정**: 해당 parsing 은
   input validation 의 성격이 있어 handler 초반에 두고 싶어했을 가능성.
   하지만 concurrency 관점에서는 `chatAbortControllers.set` 을 guard 의
   "첫 statement" 로 옮기거나 attachment parsing 이후에 **한 번 더 get**
   을 추가해야 한다.

## 영향

- impact_hypothesis: wrong-output
- 영향 경로: attachment (image/file) 포함 chat.send 의 재시도. 모바일
  네트워크 환경에서 비교적 흔함 (큰 첨부 → 타임아웃 → retry).
- 결과: LLM 호출 2회 + 비용 × 2, session transcript 에 상호 간섭 (delta
  chunk 가 tasks 순서 없이 섞임), 일부 lifecycle 이벤트 누락 가능.
- cross-ref: FIND-gateway-memory-003 (`agentRunStarts` safety belt 부재) 와
  조합되면 중복 spawn 의 cleanup 미싱으로 Map leak 가능.

## 반증 탐색

**R-3 defense grep**:
- `rg -n "chatAbortControllers\.(set|get|delete)" src/gateway/` → 5 hits, 전부 chat.ts/chat-abort.ts 내부. 외부 guard 없음.
- `rg -n "Mutex|Semaphore|AsyncLock|withSerialized" src/gateway/server-methods/chat.ts` → 0 matches.
- `rg -n "Promise\.race\(" src/gateway/server-methods/chat.ts` → 0 matches.

**상위 직렬화**: `message-handler.ts:1497` 의 `void (async IIFE)().catch` 가
per-connection/per-idem 직렬화를 하지 않음.

**execution 분류** (R-5):
- **no-attachment branch**: L1920 check 와 L1960 set 사이 await 없음 →
  unconditional guard 성립 (CAL-001 의 올바른 사례). 이 경로는 race 없음.
- **attachment branch**: L1920 → L1930/L1936 await → L1960 set. 정상
  flow 에 race window 가 생김. conditional-on-attachment 라 severity 를
  P1 로 유지 (P0 은 아님 — no-attachment 가 전체 chat.send 의 상당 비율).

**upstream 중복 fix 확인** (CAL-004): `git log upstream/main -- src/gateway/server-methods/chat.ts` 최근 20 commit 스캔 — attachment race fix 없음.

## Self-check

### 내가 확실한 근거
- L1920/L1930/L1936/L1960 의 위치와 L1928-1956 의 branch 조건을 코드로 확인.
- `chatAbortControllers` 의 다른 set 호출 부재 (grep 결과 chat.ts:1960 유일).
- no-attachment branch 가 race 가 없음을 await 흐름 추적으로 확인.

### 내가 한 가정
- 재시도 시 동일 clientRunId 로 두 RPC 가 도착할 수 있다 (idempotencyKey
  정책).
- `parseMessageWithAttachments` 의 await 이 수십~수백 ms 범위라는 관측 —
  정확한 수치는 미측정.
- agent pipeline 내부에서 동일 runId 의 중복 run 을 감지하여 abort 시키는
  별도 layer 가 없다 — 코드 탐색 범위 (`src/gateway/`) 내에서 확인.

### 확인 안 한 것 중 영향 가능성
- agent runner (`src/agents/`) 내부에서 runId 기반 dedupe 가 있을 가능성.
  있다면 피해는 일부 완화될 수 있음 — 그러나 gateway 층에서 guard 의 의도가
  무효화되는 것은 여전히 결함.
- `parseMessageWithAttachments` 가 fs 캐시 hit 인 경우 실제 I/O 가 짧아
  race window 가 좁을 수 있음. 그래도 microtask 경계만으로도 race 는 성립.
