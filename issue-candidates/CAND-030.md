---
candidate_id: CAND-030
type: single
finding_ids:
  - FIND-agents-registry-lifecycle-001
cluster_rationale: "단일 결함 — run-manager.ts:503-528 markSubagentRunTerminated 의 dispose loop 가 `clearPendingLifecycleError` 만 호출하고 `clearPendingLifecycleTimeout` 누락 (deps interface gap). 동등한 종착점 finalizeInterruptedSubagentRun (registry.ts:1100-1102) 은 두 marker 모두 clear — API 비대칭. listener (registry.ts:937) 의 aborted 분기가 15초 grace timer 를 걸어 둔 상태에서 markTerminated 가 호출되면 timer 가 entry 잔존 동안 fire 가능 → completeSubagentRun L780-791 reset 분기가 stale grace fire 를 'late COMPLETE event' 로 오인 → killed entry 의 endedReason / outcome / cleanupCompletedAt 가 모두 덮어쓰여 KILLED → COMPLETE/timeout 으로 외부 hook 관측 왜곡. 다른 P2 (FIND-001) 와 다른 file (run-manager vs listener wrapper) 다른 axis (timer marker vs unhandled rejection)."
proposed_title: "agents/subagent-registry: markSubagentRunTerminated drops pendingLifecycleTimeout marker"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "845040214e fix: recover subagent waits after transport drops"
    - "7c5222a195 fix: preserve pending subagent sessions during maintenance (#81498)"
    - "f17fd735ef fix(agents): avoid kill-recovery hook bootstrap race"
  finding: "pendingLifecycleTimeoutByRunId 자체가 845040214e (2026-04-25) 의 신규 도입. 같은 PR 이 listener 의 phase=='end'+aborted=='true' 에 timer 를 set 하면서 markTerminated 의 대칭 clear 를 누락한 partial fix. 6주 markTerminated + clearPendingLifecycleTimeout 키워드 commit 없음."
  pr_search: "gh pr list --search 'markSubagentRunTerminated clearPendingLifecycleTimeout' → 1 match: PR #54764 'sessions: recover orphaned subagent cleanup' (CyberSpencer, OPEN 2026-03-26). 'gh pr diff 54764 | grep clearPendingLifecycleTimeout' → 0 매치 — 본 axis 미수정."
  related_open_pr: 54764
  related_open_pr_notes: "PR #54764 는 markSubagentRunTerminated 영역을 일부 수정하지만 diff 에 clearPendingLifecycleTimeout 추가 없음. 본 FIND 의 axis (marker clearance 비대칭) 와 분리 가능. 추가 OPEN PR #77415 / #80886 도 listener side (first-progress/startup-failed) timer clear 추가 축으로 markTerminated 와 다른 layer."
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
  - CAND-027  # 같은 file (subagent-registry) 다른 layer
---

# agents/subagent-registry: markSubagentRunTerminated drops pendingLifecycleTimeout marker

## 문제 요약

`src/agents/subagent-registry-run-manager.ts:503-528` markSubagentRunTerminated 의 dispose loop 가 L504 `params.clearPendingLifecycleError(runId)` 만 호출하고 동등한 timeout 측 marker (`pendingLifecycleTimeoutByRunId`, registry.ts:362-368) 는 clear 하지 않는다. 동일 종착점인 `finalizeInterruptedSubagentRun` (registry.ts:1100-1102) 은 두 marker 를 모두 clear — API contract 비대칭.

원인: run-manager.ts:105-147 의 deps interface 에 `clearPendingLifecycleTimeout` 자체가 노출되지 않음 (L123 에 clearPendingLifecycleError 만 등록). registry.ts:386 의 함수가 module-local 이라 controller 가 access 불가.

발현 sequence:
1. listener (registry.ts:937) phase=='end' + aborted===true → schedulePendingLifecycleTimeout(runId, 15s)
2. 15초 이내 user kill / orphan recovery → markSubagentRunTerminated → clearPendingLifecycleError 만 clear, timer 잔존 + entry maintained (cleanupCompletedAt=now, delete 안 함, sweeper TTL 5분)
3. timer fire → completeSubagentRun({reason: COMPLETE, outcome:{status:'timeout'}, triggerCleanup:true})
4. lifecycle.ts:780-791 reset 분기 fire: suppressAnnounceReason='killed' && cleanupHandled → 모두 reset
5. endedReason KILLED → COMPLETE, outcome error → timeout, hook 관측치 왜곡, detached task tracker deliveryStatus 'failed' → 'timed_out'

## fix surface

- run-manager.ts:123 deps interface 에 `clearPendingLifecycleTimeout: (runId: string) => void` 추가.
- registry.ts:975-993 controller 생성부 (`createSubagentRunManager` 호출) 에서 `clearPendingLifecycleTimeout` 전달.
- run-manager.ts:504 dispose loop 에서 `params.clearPendingLifecycleTimeout(runId)` 호출 추가.
- 회귀 테스트 — aborted 후 15초 grace 안에 markTerminated 호출 + timer fire 시점 entry 가 KILLED 로 유지되는지 검증.

XS PR 추정 (3-5 hunk).

## upstream-dup 검사 결과

- 845040214e (2026-04-25) 가 pendingLifecycleTimeoutByRunId 신규 도입하면서 markTerminated 의 대칭 clear 누락 → 본 FIND 는 partial fix gap.
- PR #54764 (markTerminated 영역 OPEN) diff 에 clearPendingLifecycleTimeout 추가 없음.
- PR #77415, #80886, #76332 등 listener side 의 timer clear 추가 PR 들도 markTerminated 축 미수정.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — deps interface 보강 + dispose loop 수정.
2. 회귀 테스트 fixture: schedulePendingLifecycleTimeout 후 markTerminated → 15초 timer fire 시점 검증 (vi.useFakeTimers + advanceTimersByTime).
3. CAL-003 회피 — production hot-path 가 'aborted event 후 user kill 시퀀스' 임을 확인 (gateway-side handler diff 참조).

## 관련 FIND

- FIND-agents-registry-lifecycle-001: markSubagentRunTerminated 의 clearPendingLifecycleTimeout 누락으로 stale grace timer 가 killed entry 를 COMPLETE/timeout 으로 덮어씀.
