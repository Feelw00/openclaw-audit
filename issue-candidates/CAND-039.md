---
candidate_id: CAND-039
type: single
finding_ids:
- FIND-gateway-lifecycle-002
cluster_rationale: 단일 결함 — server-runtime-services.ts:156-190 의 두 recovery 함수 (recoverPendingOutboundDeliveries
  즉시 IIFE, recoverPendingSessionDeliveries setTimeout(1250ms)) 가 stop handle 반환 부재
  + caller (server.impl.ts:1366-1389) 가 cancellation 통로 미보유. timer.unref() 는 event-loop
  alive 만 막을 뿐 callback fire 차단 아님. boot 직후 1250ms 이내 SIGTERM 시 tearing-down state
  에서 recovery 가 dynamic import + dispose 된 deps 사용 시도 → error log 노이즈 + 의도치 않은 외부
  메시지 발사 + partial state 디스크 잔존. 같은 file 의 scheduleGatewayPostReadyMaintenance (L103-154)
  가 isClosing 가드 + clearGatewayMaintenanceHandles 패턴 보유 — 비대칭 누락. 다른 gateway FIND
  와 axis 다름.
proposed_title: 'gateway/runtime-services: cancel startup recovery jobs on shutdown'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - 'f4f98f45c7 fix(gateway): cancel post-ready maintenance on close'
  - '654b70dde8 fix(gateway): keep cron startup after maintenance failure'
  - '0b1fbeabed perf(gateway): defer cron and sentinel startup work'
  - 'a903df02f5 fix(gateway): bound restart continuation recovery'
  - '0ac81d41b6 fix(gateway): durably hand off restart continuations'
  finding: f4f98f45c7 가 post-ready maintenance 에 cancellation 추가하면서 같은 파일의 recovery
    함수에는 동일 패턴 미적용. a903df02f5 / 0ac81d41b6 (restart continuation) 는 recovery 의 다른
    axis (bound semantics, hand-off durability). cancellation handle 부재 fix 0건.
  pr_search: gh pr list --search 'recoverPendingOutboundDeliveries OR recoverPendingSessionDeliveries'
    → 0 매치.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
- CAND-038
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-039-pre-20260514-102655.md
  measurements:
    scenario: proof-CAND-039
    trials: 2
    trialResults:
    - trial: 0
      outboundFired: true
      sessionFired: true
    - trial: 1
      outboundFired: true
      sessionFired: true
    outboundCount: 2
    sessionCount: 2
    totalFired: 4
  scenario: proof-CAND-039
---

# gateway/runtime-services: cancel startup recovery jobs on shutdown

## 문제 요약

`src/gateway/server-runtime-services.ts:156-190` 의 두 recovery 함수:

- `recoverPendingOutboundDeliveries` (L156-170): 즉시 fire-and-forget async IIFE, 동적 `await import("../infra/outbound/delivery-queue.js")` → `recoverPendingDeliveries(...)`. handle 또는 stop 함수 반환 없음.
- `recoverPendingSessionDeliveries` (L172-190): `setTimeout(callback, 1250)`. timer.unref?.() 만 호출. handle 미반환.

caller (`server.impl.ts:1366-1389` `activateScheduledServicesWhenReady`) 는 startup guard (`closePreludeStarted`) 로 activate 진입만 보호, 진입한 후 background work 의 cancellation 통로 없음. `activateGatewayScheduledServices` 의 return 타입 (L255-258 `{heartbeatRunner, stopModelPricingRefresh}`) 에 recovery stop 핸들 부재.

`timer.unref()` 는 event-loop alive 카운터에서 timer 를 제외할 뿐, queued callback 자체는 process 가 다른 작업으로 살아있는 동안 fire. shutdown (`runGatewayClosePrelude` + close handler) 이 startup 직후 1250ms 이내 시작되면 recovery 가 tearing-down state 에서 진행:
- `deliverOutboundPayloadsInternal` 이 dispose 된 channelManager 호출 → error log
- 외부 채널 (Slack/Telegram/SMS) 에 의도치 않은 message 발사
- partial state 디스크 잔존

같은 파일 `scheduleGatewayPostReadyMaintenance` (L103-154) 가 `isClosing()` 가드 + `clearGatewayMaintenanceHandles` 패턴을 보유 — 동일 패턴이 recovery 에 적용 가능했음을 시사.

## fix surface

옵션 A (minimal): 두 recovery 함수 에 isClosing 가드 추가 + setTimeout 의 handle 을 host (server.impl.ts) 에 반환해 close 시 clearTimeout. recoverPendingOutboundDeliveries 의 IIFE 내부에는 (a) 각 await 사이 isClosing 체크 또는 (b) AbortSignal 파라미터 전달 — 후자가 더 안전.
옵션 B (정밀): recovery 함수 시그니처에 `signal: AbortSignal` 추가. `recoverPendingDeliveries` / `recoverPendingRestartContinuationDeliveries` (셀 밖) 에도 signal 전파 — 별도 PR.

XS-S 추정 (옵션 A).

## upstream-dup 검사 결과

- f4f98f45c7 가 post-ready maintenance 에 cancellation 추가 시점에 recovery 함수에는 동일 패턴 미적용.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — isClosing 가드 + timer handle 반환.
2. 회귀 테스트 — fake timer + immediate close trigger + recovery callback 미fire 검증.
3. CAL-003 회피 — boot→shutdown 1250ms 이내 window 가 CI/dev/smoke test 환경 hot-path 임을 명시.

## 관련 FIND

- FIND-gateway-lifecycle-002: recoverPendingOutboundDeliveries / recoverPendingSessionDeliveries 가 cancellation handle 미반환 + caller 측 cancellation 통로 부재.
