---
candidate_id: CAND-028
type: single
finding_ids:
  - FIND-agents-registry-error-boundary-002
cluster_rationale: "단일 결함 — registry.ts:676-714 restoreSubagentRunsOnce 의 `restoreAttempted=true` 가 try 진입 *이전* (L680) 에 set 되고 catch 본문은 empty + restoreSubagentRunsFromDisk 가 subagentRuns Map 을 in-place mutate. step 1 성공 후 후속 step (reconcile/ensureListener/startSweeper/resume) 의 throw 가 silent swallow 되어 entries 잔존 + wire-up 미완 → 외부 query 는 runs 로 보이지만 lifecycle event 처리 불가. 다른 cell FIND (FIND-001/003/004) 와 다른 file 또는 다른 함수라 단일 PR 가능."
proposed_title: "agents/subagent-registry: restore wire-up failures leave runs without listener/sweeper"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "845040214e fix: recover subagent waits after transport drops"
    - "11efbf5a2e fix: prevent stale subagent failure announces"
    - "d6bb36730b fix(agents): stabilize subagent lifecycle"
  finding: "restoreSubagentRunsOnce 의 set-before-action latch + silent catch 패턴은 6주 commit 어느 것도 다루지 않음."
  pr_search: "gh pr list --search 'subagent restore wireup ensureListener' → 0 매치. PR #54765 (CyberSpencer 'sessions: fix durable restore after recovery' OPEN) 가 인접 영역이나 본 FIND 의 set-before-action axis 와 직접 충돌 안 함 — PR #54765 diff 는 orphan-prune kill cleanup 경유."
  related_open_pr: 54765
  related_open_pr_notes: "PR #54765 'sessions: fix durable restore after recovery' (CyberSpencer, OPEN 2026-03-26) 는 본 FIND 와 인접 영역이나 axis 가 다름 (orphan-prune kill cleanup 의 bookkeeping). cross_refs 후보 (publisher 가 issue 발행 시 PR body 에 'related to #54765' 언급 권장)."
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
  - CAND-027  # 같은 file 다른 함수
---

# agents/subagent-registry: restore wire-up failures leave runs without listener/sweeper

## 문제 요약

`src/agents/subagent-registry.ts:676-714` `restoreSubagentRunsOnce` 는 process startup 의 단일 호출 함수. 함수 전체가 `try { ... } catch { /* ignore restore failures */ }` 로 wrap, 재시도 차단 flag (`restoreAttempted = true`) 는 try 진입 *이전* L680 에 set. `restoreSubagentRunsFromDisk` (state.ts:31) 는 `params.runs.set(runId, entry)` 로 in-memory Map 을 직접 mutate.

step 1 (Map 적재) 성공 후 step 2~5 (reconcileOrphanedRestoredRuns / ensureListener / startSweeper / resumeSubagentRun / scheduleSubagentOrphanRecovery) 중 어느 것이 throw 해도 outer catch 가 silent swallow → entries 는 잔존하지만 listener 미설치, sweeper 미가동, resume 미실행. `restoreAttempted=true` 가 이미 set 되어 후속 호출은 early return — process 재시작이 유일한 복구.

## fix surface

옵션 A (작음): catch 본문에 `log.warn`/`log.error` 추가 + counter/metric 노출. 단 functional recovery 는 부재.
옵션 B (중간): `restoreAttempted = true` 를 try 본문 끝으로 이동 — partial-failure 시 재시도 가능. + step 1 (Map mutate) 을 transactional local-builder 패턴으로 변경 (local Map 빌드 → 모든 wire-up 성공 시 atomic swap).
옵션 C (보수): 옵션 A + restore 결과를 'restored-not-wired' / 'fully-wired' 두 state 로 분리. 후속 ensureListener/startSweeper 가 fully-wired 진입까지 idempotent retry.

XS-S 추정.

## upstream-dup 검사 결과

- 6주 subagent registry commit 30+ 중 restoreSubagentRunsOnce 본 라인 변경 0건.
- 관련 OPEN PR: #54765 (durable restore after recovery) — orphan-prune kill cleanup axis. 본 FIND 의 set-before-action latch 와 다름.
- duplicate_decision: not-duplicate (다만 PR body 에 cross_refs #54765 로 인접 영역 알림 권장)

## next steps

1. 옵션 A (logging only) 로 minimal PR 시도 (XS) — primary-path inversion 위험 낮음.
2. 옵션 B 는 별도 follow-up (atomic restore semantic 변경, 회귀 테스트 필요).
3. CAL-003 회피 — restoreSubagentRunsOnce 의 wire-up step throw 시 test fixture 구성 (disk corruption mock, plugin DI mock).

## 관련 FIND

- FIND-agents-registry-error-boundary-002: restoreSubagentRunsOnce silent catch + set-before-action latch + in-place mutation 의 결합으로 wire-up 부분 실패 후 영구 partial state.
