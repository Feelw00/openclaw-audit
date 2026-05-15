---
target: "CAND-039"
phase: "pre"
scenario: "proof-CAND-039-e2e"
status: "blocked-external-dep"
base_sha: "b672be59ae"
head_sha: null
started_at: "2026-05-15T05:21:08Z"
finished_at: "2026-05-15T05:21:08Z"
---

# PROOF — CAND-039 (pre, e2e attempt)

**status**: `blocked-external-dep`
**scenario**: `proof-CAND-039-e2e`

## 결함 (재확인)

`src/gateway/server-runtime-services.ts:156-190` 의 두 recovery 함수:
- `recoverPendingOutboundDeliveries` (즉시 async IIFE)
- `recoverPendingSessionDeliveries` (setTimeout 1250ms)

stop handle 반환 부재 + caller (`server.impl.ts:1366-1389`) 측 cancellation 통로 미보유 → boot 직후 1250ms 이내 SIGTERM 시 tearing-down state 에서 recovery 가 dynamic import + dispose 된 deps 사용 시도.

## production execution path (target)

1. isolated env 에 pending entry 미리 주입 — `<state-dir>/delivery-queue/<id>.json` (outbound) + `<state-dir>/restart-sentinel.json` (session)
2. gateway run + verbose
3. boot ready 후 SIGTERM (close prelude 시작)
4. observable:
   - without-fix: setTimeout 1250 callback fire → "session-delivery-recovery" subsystem log 출력 또는 sentinel file removal 또는 channelManager dispose 후 호출 error log
   - with-fix: clearTimeout / isClosing 가드로 fire 차단 → log 없음, sentinel 잔존

## blocked 사유

audit-side infrastructure + timing window 양쪽 모두 부재.

부재 인프라:

1. **pending entry pre-injection helper** — `<state-dir>/delivery-queue/<id>.json` (delivery-queue-storage.ts:234 QueuedDelivery format) + `<state-dir>/restart-sentinel.json` (restart-sentinel.ts:42 RestartSentinelPayload format) 의 minimal-valid file 작성 패턴. valid sessionKey + sessionEntry 동반 필요 가능.
2. **close prelude 지연 trigger** — production gateway 의 close prelude 가 46ms 측정됨 (boot test 정상 5s + SIGTERM → 46ms cleanup). setTimeout 1250 가 fire 전 process 가 죽음 → race window 미발생. 강제 trigger 옵션: gateway_stop hook (custom plugin) 가 1.5s sleep 또는 channel cleanup 의 인공 지연.
3. **observable 강화** — 정상 boot 의 stderr/stdout 검색에 "recover", "delivery-recovery", "session-delivery" 키워드 0건 (pending 없으면 no-op + log 안 찍힘). pending entry 주입 후에야 log 출력.

## 1차 시도 결과 (이 세션, 2026-05-15)

- gateway run 정상 boot ready + 5s 대기 + SIGTERM: ✅ rc=0, close prelude 46ms
- 200ms 후 SIGTERM (boot 진행 중): rc=-15, stdout/stderr len=0 (config 로딩 전 죽음)
- 1500ms 후 SIGTERM (starting HTTP server 진행 중): rc=0, recovery 키워드 0건 (recovery 활성화 전 죽음 또는 pending 없어 no-op)
- recovery 의 production-faithful observable 확인 불가 — pending entry 부재 + close prelude 짧음.

## 다음 세션 starting points

(상세: `gateway-e2e.md`)

1. pending delivery entry pre-injection helper (`harness/pending_state_seed.py`)
2. session/sentinel valid file format 검증 (delivery-queue-storage.ts:loadPendingDeliveries / restart-sentinel.ts:readRestartSentinel signature)
3. close prelude 지연 plugin (`custom-slow-shutdown-plugin/`) 또는 channel 자체의 cleanup 지연 path
4. `scenarios/proof-CAND-039-e2e.py` 완성 — pre-inject + timed SIGTERM + log/sentinel observable 측정

## not_tested

- unit-level 결과 (2026-05-14 PROOF-CAND-039-pre-20260514-102655.md, totalFired=4/4) — instrumentation hook `__test.setRecoveryProbe` 의존이라 production wiring 우회. e2e 가 그 우회 없이 동일 발현 검증 목적.
- session-delivery recovery 의 deps (CliDeps) chain 가 실 production 에서 어떤 path 로 dispose 되는지.
