---
candidate_id: CAND-011
type: single
finding_ids:
  - FIND-agents-registry-concurrency-002
cluster_rationale: |
  CAND-010 (epic, 2 FIND) 의 5-agent cross-review 결과 scope-down.
  FIND-001 은 P3 + hot-path score 1/5 (announceRetryCount>=3 + 재시작 + steer-restart
  우연 충돌이라는 매우 좁은 window) + critical agent 가 숨은 guards 4건 발견:
    - registry.ts:403 `if (entry.cleanupCompletedAt) return;` (keep-path 재진입 차단)
    - registry.ts:384 `if (!entry) return;` (runs.delete 후 재진입 차단)
    - registry.ts:465-468 `restoreAttempted` flag (restore 자체 재진입 불가)
    - lifecycle.ts:310 `beginSubagentCleanup` inside retryDeferredCompletedAnnounces
  → CAL-001 패턴 반복 위험 → FIND-001 abandon.

  FIND-002 는 hot-path score 4/5 (모든 subagent 완료 경로), production
  dual-dispatch 확정적 (registerSubagentRun 이 ensureListener + waitForSubagentCompletion
  둘 다 무조건 기동), cleanupBrowserSessionsForLifecycleEnd 가 실제 I/O (browser
  driver call) → real-problem-real-fix 합의 3/5, real-problem-fix-insufficient 2/5
  (0 false-positive).

  Upstream 검증: 72b2e413d6 (#60146, 2026-04-03) 가 unguarded call site 도입,
  후속 refactor (605f48556b) 도 가드 추가 안 함. 48042c3875 는 endedHookEmittedAt
  가드로 훅만 보호 → browser cleanup 은 여전히 노출. 열린 관련 PR (#68464, #55712,
  #53314) 은 scope 상이 → 중복 없음 (CAL-004 risk low).
proposed_title: "subagent-registry: cleanupBrowserSessionsForLifecycleEnd invoked outside beginSubagentCleanup guard causes duplicate browser driver I/O"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-19
---

# subagent-registry: cleanupBrowserSessionsForLifecycleEnd invoked outside beginSubagentCleanup guard

## 문제 요약

`src/agents/subagent-registry-lifecycle.ts:635-644` 의 `completeSubagentRun` 이 `cleanupBrowserSessionsForLifecycleEnd` (line 639) 를 `startSubagentAnnounceCleanupFlow` (line 644, 내부에 `beginSubagentCleanup` 원자 가드 포함) **이전**에 호출한다. `registerSubagentRun` (run-manager.ts:348, :354) 은 embedded-mode 에서 `ensureListener()` 와 `void waitForSubagentCompletion(runId, ...)` 을 **항상 둘 다** 기동하므로 두 경로가 동일 runId 에 대해 `completeSubagentRun` 을 병렬 호출 가능 → 동일 `childSessionKey` 에 대한 브라우저 세션 정리 I/O (runBestEffortCleanup → closeTrackedBrowserTabsForSessions → browser plugin driver call) 가 2회 발사.

## 근거 위치

- 호출 site (guard 밖): `src/agents/subagent-registry-lifecycle.ts:635-644`
- 원자 guard (내부): `src/agents/subagent-registry-lifecycle.ts:280-291` (beginSubagentCleanup)
- guard 적용 지점: `src/agents/subagent-registry-lifecycle.ts:476, 492` (startSubagentAnnounceCleanupFlow 내부)
- Dual dispatch 기동: `src/agents/subagent-registry-run-manager.ts:348, 354`
- Listener 경로: `src/agents/subagent-registry.ts:659-667`
- RPC 경로: `src/agents/subagent-registry-run-manager.ts:119-128`
- 최근 관련 upstream commit: `72b2e413d6` (#60146, 2026-04-03 merged) — unguarded site 도입
- Hook dedup 선행: `48042c3875` (endedHookEmittedAt 만 보호, browser cleanup 미보호)

## 공통 패턴

단일 FIND (FIND-agents-registry-concurrency-002). `registerSubagentRun` 이 두 완료 경로를 무조건 dual-dispatch 하는 것을 전제로, 두 번째 completeSubagentRun 호출이 triggerCleanup=true 로 line 639 를 통과 → browser cleanup 중복 실행.

## 재현 시나리오

1. 사용자가 Task tool / sessions-spawn 으로 subagent spawn → `registerSubagentRun`
2. `ensureListener()` 와 `void waitForSubagentCompletion(runId, ...)` 둘 다 pending
3. subagent 정상 종료 → onAgentEvent phase="end" 발행
4. 경로 A (listener): registry.ts:659 이벤트 콜백 → `completeSubagentRun(runId, ...)` 호출 → lifecycle.ts:639 `cleanupBrowserSessionsForLifecycleEnd` 실행
5. 경로 B (RPC resolve): agent.wait gateway RPC 가 같은 event 로 resolve → run-manager.ts:119 `completeSubagentRun(runId, ...)` 호출 → lifecycle.ts:639 재실행
6. `beginSubagentCleanup` 는 startSubagentAnnounceCleanupFlow (line 644) 내부에서 처음 taken 되므로 cleanupHandled 플래그는 line 639 진입 시점에 false → 가드 우회

## Impact

- **매 subagent 완료 시** embedded mode 에서 browser driver I/O 중복 (driver tab close 명령 2회)
- 대상 tab 이 이미 없다면 best-effort warn 2회 + 로그 증폭
- 대상 tab 이 아직 있다면 close 명령 2회 (driver 구현이 idempotent 여도 IPC round-trip 낭비)
- 48042c3875 가 선행 fix 한 endedHookEmittedAt 가드의 자연 확장 — 메인테이너가 이미 인정한 race family

## 대응 방향 (제안만, 해결책 아님)

`beginSubagentCleanup(runId)` 을 `cleanupBrowserSessionsForLifecycleEnd` 호출 **이전** 으로 이동시켜 sync atomic guard 가 두 경로 중 하나만 통과시키도록. 또는 browser cleanup 을 `startSubagentAnnounceCleanupFlow` 내부로 재배치.

구체 구현은 SOL 작성 단계에서 확정.
