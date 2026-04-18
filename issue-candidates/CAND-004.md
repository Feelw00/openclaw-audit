---
candidate_id: CAND-004
type: single
finding_ids:
  - FIND-agents-registry-memory-002
cluster_rationale: "FIND-agents-registry-memory-002 는 sweepSubagentRuns 의 self-stop 조건이 `subagentRuns.size === 0` 만 검사하고 `pendingLifecycleErrorByRunId.size` 를 무시해, TTL 미도달 pending error 엔트리가 sweeper 가 멈춘 뒤 무기한 잔존하는 문제. 본 배치의 다른 FIND 들과 root_cause_chain 의미론이 겹치지 않음(cron 계열은 runningAtMs 의 CAS/liveness, plugins 계열은 Map eviction 부재). FIND 자체가 이미 FIND-agents-registry-memory-001(grace-period timer 누출) 과 cross_refs 로 연결되어 있으나 FIND-001 은 본 배치에 포함되지 않음 — 본 CAND 는 FIND-002 만 단독 포함. root_cause_chain[0].because=\"line 613-614 의 중지 조건 subagentRuns.size === 0 은 오직 run Map 의 크기만 본다. pendingLifecycleErrorByRunId.size 는 고려하지 않으므로, pending error 엔트리가 남아있어도 sweeper 가 멈춘다\"; root_cause_chain[1].because=\"stopSweeper() 는 clearInterval(sweeper) 만 수행하고 pendingLifecycleErrorByRunId 나 그 내부 timer 를 전혀 건드리지 않는다\"; root_cause_chain[2].because=\"startSweeper() 는 registerSubagentRun / replaceSubagentRunAfterSteer / restoreSubagentRunsOnce 에서만 호출된다. 새 subagent run 이 생기지 않는 동안 sweeper 는 dead 상태\"."
proposed_title: "subagent sweeper self-stop 이 pending error map TTL cleanup 을 무력화"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-18
---

# subagent sweeper self-stop 이 pending error map TTL cleanup 을 무력화

## 공통 패턴

`sweepSubagentRuns()` (src/agents/subagent-registry.ts:603-615) 는 두 가지 책임을 가진다:

1. `subagentRuns` Map 의 TTL-기반 cleanup (session 5분, non-session archiveAtMs).
2. `pendingLifecycleErrorByRunId` Map 의 5분 절대 TTL cleanup.

그러나 sweep cycle 종료 시점의 자체 중지 조건은 오직 `subagentRuns.size === 0` 만 본다:

```ts
if (subagentRuns.size === 0) {
  stopSweeper();
}
```

이 조건은 `pendingLifecycleErrorByRunId.size` 나 그 TTL 잔여 시간을 전혀 고려하지 않는다.
한 cycle 안에서 run 이 모두 삭제되고 동시에 pending error 엔트리가 아직 5분 TTL 에 도달하지
않은 경우, sweeper 가 그 pending entry 를 회수하기 위한 추가 cycle 을 스스로 거부한다.

더 나아가:

- `stopSweeper()` (line 533-539) 는 `clearInterval(sweeper)` + `sweeper = null` 만 수행하고
  pending map 이나 그 내부 timer 를 정리하지 않는다.
- `startSweeper()` 재시작은 `registerSubagentRun` / `replaceSubagentRunAfterSteer` /
  `restoreSubagentRunsOnce` 세 경로에서만 발생. idle 워크로드(하루 1회 subagent 실행)에서는
  재시작 대기 시간이 수시간~수일에 달할 수 있다.
- listener(`ensureListener`, line 632-638) 는 `subagentRuns.get(evt.runId)` 가 undefined 이면
  phase==="end" 외에 early-return 하므로 뒤늦게 도착한 event 로도 orphaned pending 을 청소하지 못한다.
- 프로덕션 graceful shutdown 경로가 pending map 을 비우는 hook 을 가지고 있지 않다
  (`rg -n "process\\.on\\(.SIGTERM|SIGINT.|beforeExit" src/agents/subagent-registry*.ts` → 0 매치).
  `clearAllPendingLifecycleErrors` 는 `resetSubagentRegistryForTests` (line 748-773) 에서만 호출되는
  테스트 전용 경로.

결과: orphan 이 된 pending entry (timer ref + endedAt + optional error string, ~150-800 B/entry)
가 다음 새 subagent run 등록 또는 프로세스 재시작까지 무기한 메모리 상주. 개별 엔트리 크기는
작지만 Node.js timers heap 에 unref'd timer 들이 누적되어 O(log N) 타이머 연산 비용이 다른 hot
timer(예: resumeRetryTimers) 로 전파될 수 있다.

### 근거 인용 (root_cause_chain 에서 직접)

- `root_cause_chain[0].because`: "line 613-614 의 중지 조건 `subagentRuns.size === 0` 은 **오직
  run Map** 의 크기만 본다. `pendingLifecycleErrorByRunId.size` 는 고려하지 않으므로, pending
  error 엔트리가 남아있어도 sweeper 가 멈춘다" (src/agents/subagent-registry.ts:613-614)
- `root_cause_chain[1].because`: "`stopSweeper()` (line 533-539) 는 `clearInterval(sweeper)` 만
  수행하고 `pendingLifecycleErrorByRunId` 나 그 내부 timer 를 전혀 건드리지 않는다"
  (src/agents/subagent-registry.ts:533-539)
- `root_cause_chain[2].because`: "`startSweeper()` 는 오직 `registerSubagentRun()`,
  `replaceSubagentRunAfterSteer()`, `restoreSubagentRunsOnce()` 에서만 호출된다. 새 subagent run
  이 생기지 않는 동안 sweeper 는 dead 상태" (src/agents/subagent-registry.ts:500,520-531)

## 관련 FIND

- **FIND-agents-registry-memory-002** (P2): `sweeper self-stop strands pendingLifecycleError entries
  indefinitely` (src/agents/subagent-registry.ts:603-615).

## 인접 FIND 와의 연결

- FIND 자체가 `cross_refs: [FIND-agents-registry-memory-001]` 를 선언. FIND-001 은 grace-period
  timer 의 15s → 5min gap 이라는 시간 창 문제였고, 본 FIND-002 는 그 5min TTL 자체가 무력화되는
  정적 구조 문제. FIND-001 은 현재 배치에 포함되지 않아 본 CAND 에서는 FIND-002 만 단독 처리.
  향후 FIND-001 이 ready/ 에 재등장하면 epic 재평가 대상.
