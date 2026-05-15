---
target: "CAND-038"
phase: "pre"
scenario: "proof-CAND-038-e2e"
status: "blocked-external-dep"
base_sha: "b672be59ae"
head_sha: null
started_at: "2026-05-15T05:21:08Z"
finished_at: "2026-05-15T05:21:08Z"
---

# PROOF — CAND-038 (pre, e2e attempt)

**status**: `blocked-external-dep`
**scenario**: `proof-CAND-038-e2e`

## 결함 (재확인)

`src/gateway/server/ws-connection.ts:351-421` 의 WS connection close 핸들러가 chatAbortControllers 의 ownerConnId 매칭 entry 를 abort 하지 않음 → user disconnect 후 maintenance interval (>2분) 까지 runner 가 abort 없이 진행.

## production execution path (target)

1. probe1: gateway WS connect → connect.challenge → connect frame → hello.ok
2. probe1: chat.send RPC → server-methods/chat.ts:2189 `registerChatAbortController({chatAbortControllers, ownerConnId, ...})` 가 entry 등록
3. probe1: ws.close 발사
4. server-side: ws-connection.ts:351-421 close handler 실행 — chatAbortControllers 미 iterate (결함)
5. observable: probe2 (다른 WS connection) 가 같은 sessionKey 의 `chat` event subscribe — without-fix 면 chat.aborted broadcast 미도달 (chatAbortControllers entry 잔존), with-fix 면 도달 (close handler 가 abortChatRunById 호출 → broadcastChatAborted)

## blocked 사유

audit-side infrastructure 신규 작업 필요 — 한 세션 cap 초과 (multi-session size, telegram-e2e.md 패턴과 동등).

부재 인프라:

1. **device pairing helper** — `scripts/e2e/lib/upgrade-survivor/update-restart-auth.sh:140-225` 의 ed25519 keypair 생성 + `<state-dir>/identity/device.json` + `device-auth.json` + `devices/paired.json` 3 file 작성 패턴 audit harness 이식. `cli` mode connect 시 `NOT_PAIRED: device identity required` 우회.
2. **chat workflow chain 부팅 검증** — `chat.send` RPC handler 가 backend (agents/openai chain) 호출 → `registerChatAbortController` 실 entry 등록 도달 보장. 현 isolated_home 가 codex/openai plugin 활성화 (boot test stdout `agent model: openai/gpt-5.5`) 하지만 chat.send 실제 entry 등록 도달 미검증.
3. **mock LLM disconnect detection 보강** — `scripts/e2e/mock-openai-server.mjs` 가 `req.on("close")` 미구현. with-fix 시 server LLM fetch abort 가 mock 측에 도달했는지 측정 path 부재. 별도 mock 작성 또는 mock-openai-server.mjs 수정 (production-faithful 보존 위해 별도 mock 권장).
4. **두 audit ws probe 의 chat event subscription** — probe2 가 같은 sessionKey 의 `chat` event 받는 path. `subscribeSessionEvents` RPC 또는 webchat 자동 subscription 흐름 확인 필요.

skeleton `skills/real-behavior-proof/harness/proof_CAND_038_e2e.py` (2026-05-15 작성) 가 SUT spawn 까지 진행. connect frame schema (PROTOCOL_VERSION=4 + ConnectParamsSchema 정확형) 확인 완료 — `client.id=cli` 까지 통과 후 device identity 부재로 reject.

## 1차 시도 결과 (이 세션, 2026-05-15)

- gateway run --auth none --bind loopback --port {port} --allow-unconfigured: ✅ 부팅 (1.6s ready)
- audit ws probe connect.challenge 수신: ✅
- connect frame {minProtocol:4, maxProtocol:4, client:{id:"cli", mode:"cli"}}: ❌ `NOT_PAIRED: device identity required` (auth.mode=none 인데도 device 필수)
- mode=webchat 시도: ❌ `origin not allowed`
- mode=probe / id=openclaw-probe 시도: ❌ device identity required

device pairing 인프라 추가는 신규 작업 multi-session. NEXT.md §0 의 "외부 환경 필요" 분류 (=audit-side infrastructure 부재) 와 일치.

## 다음 세션 starting points

(상세: `gateway-e2e.md`)

1. device pairing helper 작성 (`skills/real-behavior-proof/harness/device_pairing.py`)
2. audit ws client helper (`harness/audit_ws_client.py`) — connect handshake + chat.send/subscribe RPC wrapping
3. mock-openai-server-cand038.mjs (disconnect detection 추가)
4. `scenarios/proof-CAND-038-e2e.py` 완성 — probe1+probe2 패턴 + chat aborted event 측정
5. evaluate_pre 작성 + collected/unreproducible 판정

## not_tested

- unit-level 결과 (2026-05-14 PROOF-CAND-038-pre-20260514-102353.md, aAborted=false bAborted=false) 가 production execution path 발현을 보였지만, full wire-level (실 gateway + 실 LLM fetch + 실 broadcast) 미검증.
- false positive 가능성 추가 검증 — broadcast 흐름이 실 production 에서 발생하는가.
