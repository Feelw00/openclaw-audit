# Gateway E2E 인프라

CAND-038 / CAND-039 / CAND-040 의 production end-to-end re-verification 작업.
2026-05-15 1차 시도 → 세 CAND 모두 `blocked-external-dep` (audit-side infrastructure 부재) 영속화.

`telegram-e2e.md` 와 같은 패턴 — multi-session 진행. 각 CAND 별 e2e 작업 시작 시점에 이 파일 읽어 starting points 확인.

## 1차 시도 결과 요약 (2026-05-15)

세 CAND 의 production execution path 가 같은 종류의 audit-side 인프라 부재로 막힘:

| CAND | 핵심 결함 | 부재 인프라 | proof record |
|---|---|---|---|
| 038 | ws-connection.ts:351-421 close handler 가 chatAbortControllers 의 ownerConnId 매칭 entry abort 안 함 | device pairing / chat workflow chain / mock LLM disconnect detection / 두 ws probe subscription | `proofs/PROOF-CAND-038-pre-20260515-052108-e2e-blocked.md` |
| 039 | server-runtime-services.ts:156-190 recovery 함수 stop handle 부재 + caller cancellation 통로 없음 | pending state file injection / close prelude 지연 trigger / recovery observable 강화 | `proofs/PROOF-CAND-039-pre-20260515-052108-e2e-blocked.md` |
| 040 | approval-handler-runtime.ts:498-537 deliverTarget 의 activeEntries RMW 가 onStopped clear 와 race | native runtime stub / capability 등록 path / approval trigger / activeEntries 측정 sideband | `proofs/PROOF-CAND-040-pre-20260515-052108-e2e-blocked.md` |

## 부팅 baseline (확인된 사실)

- `node /Users/lucas/Project/openclaw/openclaw.mjs gateway run --auth none --bind loopback --port <p> --allow-unconfigured` 가 isolated_home 환경에서 정상 부팅 (1.4-1.6s, plugins 9개, agent model: openai/gpt-5.5).
- close prelude 가 graceful 46ms 측정 — SIGTERM 후 빠르게 죽음. timing window 측정 제약.
- audit ws connect.challenge 수신 + connect frame schema (PROTOCOL_VERSION=4 + ConnectParamsSchema) 확인 완료.

## CAND 별 다음 세션 작업 분할

### CAND-038 (chat run abort on disconnect)

**audit-side infra 필요 작업**:

1. **device pairing helper** (`skills/real-behavior-proof/harness/device_pairing.py`)
   - 참고: `/Users/lucas/Project/openclaw/scripts/e2e/lib/upgrade-survivor/update-restart-auth.sh:140-225`
   - ed25519 keypair 생성 → `<state-dir>/identity/device.json` + `device-auth.json` + `devices/paired.json` 작성
   - 반환: `{deviceId, publicKeyRaw, privateKeyPem, token, scopes}`
   - 작업량: ~1.5h

2. **audit ws client helper** (`harness/audit_ws_client.py`)
   - WebSocket connect + connect frame (device payload 포함) + hello.ok 대기
   - RPC wrapper: chat.send / subscribe / unsubscribe / heartbeat
   - 이벤트 listener (frame 별 callback)
   - 작업량: ~1h

3. **mock-openai-server-cand038.mjs** (`scripts/audit/` 또는 audit-side 위치)
   - 기존 `scripts/e2e/mock-openai-server.mjs` 의 fork
   - `req.on("close")` 추가 → MOCK_REQUEST_LOG 에 `{event:"client_disconnected", ts, requestId}` append
   - hold-then-complete 모드 (SSE stream 5-15s hold 후 송신)
   - 작업량: ~0.5h

4. **`scenarios/proof-CAND-038-e2e.py`**
   - probe1: connect → chat.send → ws.close 2s 후
   - probe2: connect → subscribeSessionEvents(probe1's sessionKey) → chat aborted event timeout 대기 (30s)
   - mock_llm: MOCK_REQUEST_LOG 에서 client_disconnected event 검색
   - evaluate_pre: probe2 가 chat aborted event 받았는가 (with-fix 일치, without-fix 결함) + mock log 의 client disconnect 시점 측정
   - 작업량: ~2h

**chain 의존성 우려**: chat.send 가 backend (agents/openai) 까지 chain 작동해야 registerChatAbortController 가 entry 등록. isolated_home 에서 codex/openai plugin 활성화 확인됐지만 (boot test) chat.send 실 entry 등록 도달 미검증. 작업 중 막히면 minimal-chain 시도 (agent.request 또는 다른 RPC 가 같은 chatAbortControllers 사용).

총 ~5h, 1-2 세션.

### CAND-039 (startup recovery cancel on shutdown)

**audit-side infra 필요 작업**:

1. **pending state pre-injection helper** (`harness/pending_state_seed.py`)
   - delivery-queue/<id>.json: `delivery-queue-storage.ts:23 QUEUE_DIRNAME` + `loadPendingDeliveries` → QueuedDelivery format (id, enqueuedAt, retryCount, payload 등)
   - restart-sentinel.json: `restart-sentinel.ts:42 RestartSentinelPayload` (kind=restart 또는 update + sessionKey + deliveryContext)
   - 세션 entry 동반 prep (sessionKey 가 valid 해야 recovery 가 channel route 시도)
   - 작업량: ~1.5h

2. **close prelude 지연 path 식별**
   - 옵션 A: custom plugin (`audit/plugins/slow-shutdown-plugin/`) 가 gateway_stop hook 에서 1.5-2s sleep
   - 옵션 B: production code 의 channel cleanup 지연 path (실 channel 등록 후 channel.dispose 가 IO 대기)
   - 옵션 C: setTimeout 1250 fire window 를 강제로 늘리는 다른 trigger
   - 작업량: ~1.5h

3. **observable 강화**
   - recovery 의 stderr log keyword (`delivery-recovery` / `session-delivery-recovery` subsystem) 정확 형식 확인
   - sentinel file removal 시점 측정 (recovery 진행 → sentinel 파일 read + 처리 후 unlink)
   - 작업량: ~0.5h

4. **`scenarios/proof-CAND-039-e2e.py`**
   - pre-inject pending entry → gateway run + slow shutdown plugin → ready 후 SIGTERM → setTimeout 1250 fire window 강제 → log/sentinel observable 측정
   - evaluate_pre: recovery 가 fire 됐는가 (without-fix 일치) + with-fix 시 fire 차단
   - 작업량: ~1h

총 ~4.5h, 1-2 세션.

### CAND-040 (approval handler stop race)

**audit-side infra 필요 작업**:

1. **native runtime stub** (`harness/native_runtime_stub.py` 또는 tsx-inline)
   - `nativeRuntime.transport.deliverPending/bindPending/unbindPending` minimal 구현
   - `interactions.bindPending` 구현
   - deliverPending/bindPending await 지연 통제 (Deferred-like sync gate)
   - 호출 횟수 + 호출 시 entry 정보 sideband 기록
   - 작업량: ~1.5h

2. **channel capability 등록 path 식별**
   - config-driven 또는 plugin-driven 또는 runtime API
   - audit harness 가 어디서 capability 등록 trigger 가능한가
   - 작업량: ~1.5h

3. **approval request 강제 trigger**
   - audit-side direct call (capability handler 의 handleRequested 직접 호출)
   - 또는 inbound channel 시뮬 (audit ws probe 가 channel message 보내는 path)
   - 작업량: ~1h

4. **`scenarios/proof-CAND-040-e2e.py`**
   - capability + approval handler attach + race trigger (Deferred gate 로 deliverPending await park → handler.stop() → gate release → activeEntries.size 측정)
   - sideband 에서 unbindPending 호출 횟수 (without-fix=0, with-fix=1)
   - 작업량: ~1.5h

총 ~5.5h, 1-2 세션.

## 진행 순서 (권고)

가장 짧은 시간 cost + 가장 가성비 높은 CAND 부터:

1. **CAND-039 부터** (~4.5h) — pending state injection + slow shutdown plugin. observable 강화가 핵심.
2. **CAND-038** (~5h) — device pairing + chat workflow chain + mock LLM. chain 작동 디버깅 cost 클 가능성.
3. **CAND-040** (~5.5h) — capability stub + approval flow. 가장 복잡 (channel/native 의존).

각 CAND 진행 시 새 세션 시작 + 이 문서 starting points 부터 + multi-session 분할 가능.

## 대안 — unit-level final 채택 (NEXT.md §0 옵션 1)

2026-05-14 의 9 CAND pre-sol unit-level 결과를 SOL 작성 진입 evidence 로 인정:
- 38: aAborted=false bAborted=false (signal abort 미발현 확인)
- 39: totalFired=4/4 (recovery callback fire 확인)
- 40: totalUnbind=0 totalLeak=5/5 (native binding orphan 확인)

CAL-003 위험 (synthetic-only) 인지하되 PR body 의 `proof: supplied` 만 부여 (`proof: sufficient` 미부여). NEXT.md §0 의 "결정 사항" 의 옵션 1.

사용자 결정 (2026-05-14) 이 옵션 2 (외부 환경 set-up 후 재시도) 채택했으므로 multi-session audit-infra 작업 우선. 단 작업 cost 부담 큰 경우 옵션 1 로 회귀 가능.
