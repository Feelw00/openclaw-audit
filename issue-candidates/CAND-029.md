---
candidate_id: CAND-029
type: single
finding_ids:
  - FIND-agents-registry-error-boundary-003
cluster_rationale: "단일 결함 — run-manager.ts:424-453 registerSubagentRun 의 createRunningTaskRun try/catch 가 warn 만 출력하고 fall-through. subagent registry 적재는 성공했으나 task registry 미생성 → 후속 모든 task 갱신 호출 (completeTaskRunByRunId / failTaskRunByRunId / setDetachedTaskDeliveryStatusByRunId) 이 silent no-op. 사용자 task UI 가 영원히 빈 상태. 다른 agents-registry FIND 와 file/메커니즘 모두 다름."
proposed_title: "agents/subagent-registry: split state when createRunningTaskRun throws — task UI permanently empty"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "0787266637 tasks: extract detached task lifecycle runtime (#68886)"
    - "ca3e5ffd89 refactor: reduce subagent requester wrapper duplication"
    - "3b2db583cd refactor: share subagent registry query helpers"
  finding: "registerSubagentRun 의 catch warn-only fall-through 는 0787266637 (#68886) 의 detached task lifecycle 추출 이후에도 동일 패턴 유지. 6주 내 createRunningTaskRun 실패 처리 변경 commit 없음."
  pr_search: "gh pr list --search 'createRunningTaskRun OR registerSubagentRun rollback' → 0 매치. PR #80544 'Add native subagent completion ownership' (anyech, OPEN) 는 ownership 모델 변경 — partial-state axis 와 충돌 안 함."
  related_open_pr: 80544
  related_open_pr_notes: "PR #80544 는 native subagent completion ownership 변경 — 본 FIND 의 createTaskRun 실패 fall-through 와 다른 axis. 다만 ownership 모델이 future-fix 시 함께 변경될 가능성."
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
  - CAND-027
  - CAND-028
---

# agents/subagent-registry: split state when createRunningTaskRun throws — task UI permanently empty

## 문제 요약

`src/agents/subagent-registry-run-manager.ts:424-453` `registerSubagentRun` 의 처리 순서:
1. L424 `params.runs.set(runId, entry)` — in-memory subagent registry 적재.
2. L425-446 `try { createRunningTaskRun(...) } catch (error) { log.warn(...) }` — task registry persist 시도, 실패 시 warn 만.
3. L447-453 ensureListener / persist / startSweeper / waitForSubagentCompletion — 정상 진행.

createRunningTaskRun 이 throw (task store disk full / FS quota / validation failure) 하면 subagent registry 에는 entry 가 있지만 task registry 에는 record 없음. 이후 모든 task 갱신 호출 (`completeTaskRunByRunId`, `failTaskRunByRunId`, `setDetachedTaskDeliveryStatusByRunId`) 이 task-executor.ts:162-176 에서 record 못 찾고 빈 array silent 리턴. subagent 자체는 정상 spawn/완료되지만 사용자 task UI / 외부 deliveryStatus polling 이 영원히 변경 신호 받지 못함.

## fix surface

옵션 A (compensating rollback): catch 본문에 `params.runs.delete(runId); return false;` 추가 + registerSubagentRun 시그니처를 `Result<boolean, E>` 로 변경. caller (sessions-spawn-tool.ts:446, subagent-spawn.ts:1241) 가 분기 처리.
옵션 B (state contract): task-executor.ts 의 `*ByRunId` 함수가 record 없을 때 throw 또는 명시적 'not-found' Result 반환. registerSubagentRun 의 catch 는 그대로 두고 downstream 에서 split state 감지.
옵션 C (proactive recheck): registerSubagentRun 후 별도 healthcheck (sweeper 의 인접 시점) 가 subagent registry vs task registry 의 join 미스 record 를 발견하면 task registry 에 best-effort 재생성.

XS-M 범위, 옵션 A 가 가장 minimal.

## upstream-dup 검사 결과

- 0787266637 (#68886) 가 detached task lifecycle 을 분리했으나 본 fail-through 패턴은 미변경.
- 관련 OPEN PR: #80544 (subagent completion ownership) — 다른 axis.
- duplicate_decision: not-duplicate

## next steps

1. 옵션 A 시도 — caller (2개 production callsite) 의 분기 처리가 minimal 인지 확인 (PR XS 가능성).
2. 회귀 테스트 — task store throw mock + registerSubagentRun 호출 + downstream task UI query 시나리오.

## 관련 FIND

- FIND-agents-registry-error-boundary-003: createRunningTaskRun warn-only fall-through 가 subagent/task registry split state 영구화.
