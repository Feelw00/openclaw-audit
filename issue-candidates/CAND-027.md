---
candidate_id: CAND-027
type: single
finding_ids:
  - FIND-agents-registry-error-boundary-001
cluster_rationale: "단일 결함 — registry.ts:897-968 의 lifecycle listener IIFE 가 sync 콜백 안에서 void (async () => {...})() 패턴을 쓰면서 .catch 미부착. inner await completeSubagentRun() 의 reject (plugin hook / dynamic import / MCP retire 의 throw) 가 unhandledRejection 으로 escalate → infra/unhandled-rejections.ts:511 의 global handler 가 transient 분류 외 모두 exitWithTerminalRestore → process.exit(1). PR #68669 (CAND-011) 의 browserCleanup wrapper dedup 과는 다른 layer (listener wrapper 자체 catch 부재) 라 별도 PR 가치."
proposed_title: "agents/subagent-registry: catch lifecycle listener rejections to avoid process exit"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "7c5222a195 fix: preserve pending subagent sessions during maintenance (#81498)"
    - "845040214e fix: recover subagent waits after transport drops"
    - "d6bb36730b fix(agents): stabilize subagent lifecycle"
    - "48042c3875 fix(agents): avoid duplicate subagent ended hook loads"
  finding: "registry.ts:897-968 listener IIFE 에 catch chain 부재. 어떤 6주 commit 도 listener wrapper 의 try/catch 추가 영역을 다루지 않음. d6bb36730b 는 stabilize 라는 wording 이지만 diff 영역이 announce / completion 축 (PR #68669 가족) 이라 listener wrapper 의 unhandled-rejection 축과 다름."
  pr_search: "gh pr list --search 'unhandled rejection subagent listener' → 0 매치. 'subagent listener catch' → 0 매치."
  related_open_pr: null
  related_open_pr_notes: "PR #68669 (CAND-011 우리 PR) 은 cleanupBrowserSessionsForLifecycleEnd wrapper 의 dispatch flag dedup 축. 본 FIND 의 listener IIFE catch 누락과 다른 layer. PR #75462 (waitForSubagentCompletion silent catch) 도 별 영역."
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
  - CAND-011  # 같은 file (subagent-registry.ts) 다른 layer (lifecycle wrapper vs listener IIFE wrapper)
---

# agents/subagent-registry: catch lifecycle listener rejections to avoid process exit

## 문제 요약

`src/agents/subagent-registry.ts:897-968` 의 `ensureListener` 가 등록하는 onAgentEvent 콜백은 sync 콜백 안에서 `void (async () => { ... })()` 패턴으로 async IIFE 를 invoke 한다. IIFE 외곽에 `.catch(...)` 또는 try/catch 가 없다. inner await `completeSubagentRun(...)` (L958-966) 가 reject 하면 (e.g. `emitSubagentEndedHookForRun` 의 plugin hook throw, `loadCleanupBrowserSessionsForLifecycleEnd` 의 dynamic import 실패, `retireRunModeBundleMcpRuntime` 의 sessionKey 해석 throw), Node.js 가 unhandledRejection 으로 분류한다.

`src/infra/unhandled-rejections.ts:511-544` 의 global handler 는 `isTransientUnhandledRejectionError` (network/sqlite/file-watch 만 분류) 외 모든 케이스를 `exitWithTerminalRestore(...) → process.exit(1)` 로 처리. 결과: subagent cleanup 의 transient 결함 1건이 **main process 전체를 종료**, 모든 다른 subagent / 세션 / pending IO 가 비정상 단절.

## fix surface

- registry.ts:898 의 IIFE 에 `.catch(err => defaultRuntime.log(...))` 부착 (lifecycle.ts:706/717/753 등 동 파일 fire-and-forget 경로의 패턴 reuse).
- 또는 onAgentEvent API 시그니처 자체를 async-aware 로 변경 (shared/listeners.ts:15-22 의 RegisterListener — 다만 그 영역은 CODEOWNERS 외 별도 risk).
- XS PR 추정 (3-5 라인 catch handler 추가).

## upstream-dup 검사 결과

- `git log upstream/main --since="6 weeks ago" -- src/agents/subagent-registry.ts src/agents/subagent-registry-run-manager.ts` 30+ commits, listener wrapper 의 catch 부착 commit 0건.
- `gh pr list --search "unhandled rejection subagent listener"` 0 매치.
- PR #68669 (우리 측 CAND-011) 은 같은 file 이지만 line 871-877 영역 (browser cleanup dispatch flag) 으로 다른 layer.
- duplicate_decision: not-duplicate

## next steps

1. publisher 가 issue title + body 작성 (PR 본문 12섹션 의 Root Cause / Regression Test Plan / Security Impact 포함).
2. fix branch 에 listener IIFE catch handler 추가 + 회귀 테스트 (`emitSubagentEndedHookForRun` 의 throw mock + listener wrapper 가 process.exit 으로 escalate 되지 않음 검증).
3. CAL-003 회피 — synthetic-only 위험 점검: production hot-path 가 cleanup throw 인지 확인. lifecycle.ts:706/717/753 의 기존 .catch 가 정상 pattern 임을 reference.

## 관련 FIND

- FIND-agents-registry-error-boundary-001: listener IIFE catch 부재 → unhandled rejection → process.exit(1)
