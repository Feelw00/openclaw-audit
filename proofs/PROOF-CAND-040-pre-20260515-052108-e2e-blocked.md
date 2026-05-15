---
target: "CAND-040"
phase: "pre"
scenario: "proof-CAND-040-e2e"
status: "blocked-external-dep"
base_sha: "b672be59ae"
head_sha: null
started_at: "2026-05-15T05:21:08Z"
finished_at: "2026-05-15T05:21:08Z"
---

# PROOF — CAND-040 (pre, e2e attempt)

**status**: `blocked-external-dep`
**scenario**: `proof-CAND-040-e2e`

## 결함 (재확인)

`src/infra/approval-handler-runtime.ts:498-537` `createChannelApprovalHandlerFromCapability` 의 deliverTarget 두 await (deliverPending L505 / bindPending L517) 사이에 onStopped (L656-672) 가 activeEntries.clear() → deliverTarget resume 시 L529-535 가 비어있던 Map 에 wrapped entry 재등록 + onStopped 가 그 entry 에 도달 못함 → unbindWrappedEntries 미호출 → native binding orphan.

## production execution path (target)

1. gateway run + channel capability 등록 (real or stub)
2. approval handler attach (createChannelApprovalHandlerFromCapability)
3. approval request 도착 (실 user approval flow 또는 audit-side trigger)
4. deliverTarget 진입 — deliverPending await 시작
5. handler.stop() 호출 (channel reload 시뮬)
6. onStopped 실행 — activeEntries.clear()
7. deliverTarget resume — bindPending await + L529 set
8. observable: handler stop 후 activeEntries.size > 0 (orphan) + nativeRuntime.transport.unbindPending 호출 횟수 (without-fix=0, with-fix=1)

## blocked 사유

audit-side infrastructure 신규 작업 필요 — multi-session size.

부재 인프라:

1. **native runtime capability stub** — `nativeRuntime.transport.deliverPending/bindPending/unbindPending` + `interactions.bindPending` 의 minimal-faithful stub. deliverPending/bindPending 의 await 지연 통제 가능해야 race window 강제 생성.
2. **channel capability 등록 흐름** — gateway 부팅 시 capability 가 어떻게 등록되는가 (config 또는 plugin 또는 runtime API). 현 isolated_home config 에 capability 미정의.
3. **approval request trigger** — 실 user 의 channel inbound 또는 audit-side direct trigger. inbound 시뮬 가능성 미검증.
4. **activeEntries Map size 외부 측정** — closure 의 private Map 이라 외부 inspect 불가. 측정에는 `interactions.bindPending` stub 의 호출 시점 + unbindPending 호출 횟수 sideband 기록. 또는 production code 의 instrumentation hook 추가 (module-level 분류).

unit-level proof (`proof-CAND-040.py`, 2026-05-14 totalUnbind=0 totalLeak=5/5) 는 production code 의 createChannelApprovalHandlerFromCapability 를 직접 호출 + Deferred gate 로 race window 강제. wire-level e2e 는 gateway 부팅 + capability 등록 + 실 approval flow 통해 같은 결함 발현.

NEXT.md §0 분류 "외부 환경 필요" = audit-side infrastructure 부재. 사용자 결정 (2026-05-14) "외부 환경 set-up 후 재시도" 의 audit-side infra 작업이 본 세션 cap 초과.

## 1차 시도 결과 (이 세션, 2026-05-15)

- production code 검토: createChannelApprovalHandlerFromCapability 가 module-level named export. wire-level inject 가능.
- 단 `nativeRuntime.transport` + `interactions` 의 contract 확인 + stub 작성이 multi-hour.
- gateway 부팅 자체는 정상 (boot test 1.6s). approval handler 의 등록 path 는 channel cleanup hook 또는 capability bootstrap 통해 자동 attach 가능성 — 확인 필요.

## 다음 세션 starting points

(상세: `gateway-e2e.md`)

1. nativeRuntime.transport + interactions stub (`harness/native_runtime_stub.py` 또는 tsx-inline)
2. capability 등록 path 식별 — config-driven 또는 plugin-driven
3. approval request 강제 trigger path (audit-side direct call)
4. `scenarios/proof-CAND-040-e2e.py` 완성 — race window strong-trigger (Deferred-like sync gate via shared promise) + activeEntries 측정 sideband

## not_tested

- unit-level FIND 가 outer `createChannelApprovalHandlerFromCapability` 만 지목 + cross_review_decision 의 caveat (inner `exec-approval-channel-runtime.ts:393-415` spawn detached + stop() inflight await 부재) — wire-level 에서 inner-outer 두 layer 모두 검증 필요.
- production 의 채널 reload 빈도 (config watcher) — 결함 발현 빈도 추정용.
