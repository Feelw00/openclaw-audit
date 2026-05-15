# Gateway E2E 인프라

CAND-038 / CAND-039 / CAND-040 의 production end-to-end re-verification 작업.
2026-05-15 1차 시도 → 세 CAND 모두 `blocked-external-dep` (audit-side infrastructure 부재) 영속화.

`telegram-e2e.md` 와 같은 패턴 — multi-session 진행. 각 CAND 별 e2e 작업 시작 시점에 이 파일 읽어 starting points 확인.

## 1차 시도 결과 요약 (2026-05-15)

세 CAND 의 production execution path 가 같은 종류의 audit-side 인프라 부재로 막힘:

| CAND | 핵심 결함 | 부재 인프라 | proof record |
|---|---|---|---|
| 038 | ws-connection.ts:351-421 close handler 가 chatAbortControllers 의 ownerConnId 매칭 entry abort 안 함 | device pairing / chat workflow chain / mock LLM disconnect detection / 두 ws probe subscription | `proofs/PROOF-CAND-038-pre-20260515-052108-e2e-blocked.md` |
| 039 | ✅ **collected (2026-05-15)** — recovery IIFE fire 5/5 trial. close prelude 가 43ms↔2.3s 두 패턴, 후자는 shutdown 이 recovery 완료 기다리는 결함 직접 관측 | (해소) pending state seed inline + state-dir 이중 .openclaw 처리 + IIFE only 측정 | `proofs/PROOF-CAND-039-pre-20260515-061713.md` |
| 040 | approval-handler-runtime.ts:498-537 deliverTarget 의 activeEntries RMW 가 onStopped clear 와 race | native runtime stub / capability 등록 path / approval trigger / activeEntries 측정 sideband | `proofs/PROOF-CAND-040-pre-20260515-052108-e2e-blocked.md` |

## 부팅 baseline (확인된 사실)

- `node /Users/lucas/Project/openclaw/openclaw.mjs gateway run --auth none --bind loopback --port <p> --allow-unconfigured` 가 isolated_home 환경에서 정상 부팅 (1.4-1.6s, plugins 9개, agent model: openai/gpt-5.5).
- close prelude 가 graceful 46ms 측정 — SIGTERM 후 빠르게 죽음. timing window 측정 제약.
- audit ws connect.challenge 수신 + connect frame schema (PROTOCOL_VERSION=4 + ConnectParamsSchema) 확인 완료.

## CAND 별 다음 세션 작업 분할

### CAND-038 (chat run abort on disconnect)

**audit-side infra 필요 작업**:

1. ~~**device pairing helper**~~ ✅ **완료 (2026-05-15)**: `skills/real-behavior-proof/harness/device_pairing.py`
   작성 + 검증. inline Node script (ed25519 keypair + PEM 인코딩) + Python wrapper 패턴.
   참조: `/Users/lucas/Project/openclaw/scripts/e2e/lib/upgrade-survivor/update-restart-auth.sh:140-225`
   동일 로직. 작성한 파일: `identity/device.json` + `identity/device-auth.json` +
   `devices/paired.json` + `devices/pending.json` (모두 0o600).
   반환: `{deviceId, publicKeyPem, privateKeyPem, publicKeyRawBase64Url, token, role, scopes, files}`

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

### CAND-039 (startup recovery cancel on shutdown) — ✅ **collected** (2026-05-15)

**1차 e2e 시도** (2026-05-15): `proof-CAND-039-e2e` 시나리오 5 trial 전부 fire (5/5, fire_rate=1.0).
production-faithful 결함 발현 직접 관측 — recovery IIFE 가 `[gateway] ready` 직후 ~25ms 만에 fire,
SIGTERM 후에도 일부 trial 은 `Recovered delivery ... on telegram` + `Delivery recovery complete: 1
recovered` 출력. close_prelude_ms 가 43ms ↔ 2280-2306ms 두 패턴 — 후자는 recovery in-flight 인
상태에서 shutdown 이 background 완료를 기다리는 결함의 직접 측정. PROOF:
`proofs/PROOF-CAND-039-pre-20260515-061713.md`. CAND frontmatter `pre_sol_proof.status=collected`,
state transition `proof-blocked-pre → proof-collected-pre`.

**축약된 인프라** (multi-session 추정 4.5h → 실 ~2h):
1. **pending state seed** — 시나리오 내부 inline. delivery-queue/<uuid>.json 만으로 충분
   (restart-sentinel/session-delivery 추가는 setTimeout fire window 측정에 필요하지만 IIFE
   측정만으로 결함 증명 충분). state-dir 는 `${OPENCLAW_HOME}/.openclaw` (env_isolate 의
   이중 .openclaw 구조).
2. **close prelude 지연 plugin 불필요** — IIFE 는 ready 직후 즉시 fire, close prelude 짧아도
   buffer 에 fire log 잡힘. setTimeout 1250 측정은 보류 (slow-shutdown plugin 작업 ~1.5h
   추가 필요, 가성비 낮음).
3. **observable**: `[delivery-recovery] Found N pending delivery entries — starting recovery`
   stdout 출력. ANSI 컬러 코드 strip + 패턴 매칭으로 검출.
4. **시나리오**: `scenarios/proof-CAND-039-e2e.py` — REQUIRES_EXTERNAL_DEP=False, CLI flag
   `--auth none --bind loopback --port <p> --allow-unconfigured` 가 cfg 보다 우선이라 cfg 패치
   없이 부팅 가능 (env_isolate 의 새 schema 와 불일치한 minimal cfg 우회).

**build worktree 우회** — 메인 repo (`/Users/lucas/Project/openclaw`) 의 `openclaw.mjs`
(upstream/main HEAD 빌드) 직접 spawn. 30분 pnpm build 회피. ad-hoc runner
(`/tmp/cand039_runner.py`, `/tmp/cand039_persist.py` — 일회용, cleanup 후 삭제됨).

**다음 (post-sol 또는 SOL 작성)**: pre-sol collected 이라 SOL-CAND-039 작성 진입 가능. fix
surface 옵션 A (isClosing 가드 + timer handle 반환) 또는 옵션 B (AbortSignal 전파) 결정.
post-sol 단계에서 with-fix 빌드 비교 + PR body 6 필드 evidence 산출.

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

진행 상태 (2026-05-15 기준):

1. ~~**CAND-039**~~ ✅ **collected (2026-05-15)** — pre-sol 완료. SOL 작성 또는 post-sol 진입.
2. **CAND-038** (~3.5h 남음) — device pairing helper ✅ 완료. **다음 단계**:
   - audit ws client manual frame (connect frame 에 device payload 첨부) ~1h
   - mock LLM disconnect detection (req.on("close") 패치) ~0.5h
   - chat workflow chain 디버깅 (chat.send → registerChatAbortController) ~1h
   - 시나리오 + 실행 ~1h
3. **CAND-040** (~5.5h) — capability stub + approval flow. 가장 복잡.

각 CAND 진행 시 새 세션 시작 + 이 문서 starting points 부터 + multi-session 분할 가능.

## CAND-039 e2e 에서 검증된 패턴 (CAND-038/040 에 재사용)

1. **build worktree 우회** — 메인 repo `/Users/lucas/Project/openclaw/openclaw.mjs` 직접 spawn.
   upstream/main 동기화만 유지하면 base sha 빌드 우회. 30분 cost 0.
2. **state-dir 이중 .openclaw** — env_isolate 의 OPENCLAW_HOME 이 ".openclaw" 끝나면 production
   resolveStateDir 는 `${OPENCLAW_HOME}/.openclaw` (이중). seed file 도 그 안에.
3. **cfg 패치 불필요** — CLI flag (`--auth none --bind loopback --port <p> --allow-unconfigured`)
   가 cfg 보다 우선. env_isolate 의 minimal cfg 가 새 schema 와 불일치해도 무시됨.
4. **ANSI 컬러 코드 strip** — logger 출력에 ANSI 코드 포함. `re.sub(r"\x1b\[[0-9;]*m", "", text)`
   후 패턴 매칭.
5. **tempfile 로 stdout/stderr redirect + polling** — subprocess.PIPE 는 buffer-full 위험. 파일
   redirect 후 폴링하면서 ready marker / fire log 검사.
6. **ready marker**: `[gateway] ready` (server-startup-post-attach.ts:818). port listen 만으론
   부족 — onSidecarsReady → activateScheduledServicesWhenReady 가 그 후.
7. **ad-hoc runner + persist 패턴** — `/tmp/<cand>_runner.py` (env_isolate + run_scenario 호출),
   `/tmp/<cand>_persist.py` (render_proof + state_mod 직접 호출). build.py 우회. 일회용 cleanup.

## 대안 — unit-level final 채택 (NEXT.md §0 옵션 1)

2026-05-14 의 9 CAND pre-sol unit-level 결과를 SOL 작성 진입 evidence 로 인정:
- 38: aAborted=false bAborted=false (signal abort 미발현 확인)
- 39: totalFired=4/4 (recovery callback fire 확인)
- 40: totalUnbind=0 totalLeak=5/5 (native binding orphan 확인)

CAL-003 위험 (synthetic-only) 인지하되 PR body 의 `proof: supplied` 만 부여 (`proof: sufficient` 미부여). NEXT.md §0 의 "결정 사항" 의 옵션 1.

사용자 결정 (2026-05-14) 이 옵션 2 (외부 환경 set-up 후 재시도) 채택했으므로 multi-session audit-infra 작업 우선. 단 작업 cost 부담 큰 경우 옵션 1 로 회귀 가능.
