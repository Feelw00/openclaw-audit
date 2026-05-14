---
candidate_id: CAND-037
type: single
finding_ids:
- FIND-context-engine-lifecycle-001
cluster_rationale: 단일 결함 — registry.ts:561-599 resolveContextEngine 의 contract validation
  fallback 분기 (factory throw / validation throw / contractError) 에서 이미 instantiated
  engine 의 dispose?.() 호출 부재. factory 가 SQLite / chokidar / HTTP keep-alive 등 native
  resource 셋업 시 leak. caller-side dispose (run.ts:3094 / compact.queued.ts:108/301)
  는 정상 반환 engine 에만 적용 — fallback path 의 invalid engine 은 caller 도달 안 함. resolveContextEngine
  내부가 유일한 cleanup 책임 지점이나 호출 부재. 다른 context-engine FIND 와 file/axis 다름.
proposed_title: 'context-engine/registry: dispose invalid engine before fallback on
  contract failure'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - '2677f7cf14 fix: validate resolved context engine contracts (#63222)'
  - '6aa4515798 fix(context-engine): gracefully degrade to legacy engine on third-party
    plugin resolution failure (#66930)'
  - '263a190fc9 Context engine/plugins: accept third-party engines whose info.id differs
    from registered slot id (#66678)'
  - '59d07f0ab4 fix(plugins): roll back failed register globals'
  finding: 2677f7cf14 (#63222, 2026-04-13) 가 contract validation + graceful fallback
    한 번에 도입했으나 새로 등장한 'factory 성공 후 invalid engine 인스턴스' 의 cleanup 경로는 함께 추가 안 됨.
    6주 dispose 추가 PR 0건.
  pr_search: gh issue list --search 'context engine dispose'  / gh pr list --search
    'resolveContextEngine dispose' → 0 매치. upstream 미인지 영역.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs: []
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-037-pre-20260514-101620.md
  measurements:
    scenario: proof-CAND-037
    trials: 2
    trialResults:
    - branch: factory-throw
      disposeCalls: 0
    - branch: contract-error
      disposeCalls: 0
    totalDispose: 0
    branchesWithDispose: 0
  scenario: proof-CAND-037
---

# context-engine/registry: dispose invalid engine before fallback on contract failure

## 문제 요약

`src/context-engine/registry.ts:527-602` `resolveContextEngine` 가 config.plugins.slots.contextEngine 으로 지정된 3rd-party engine 을 해상할 때:

1. L563 `engine = await entry.factory(factoryCtx)` 성공 → factory 가 SQLite open / chokidar watcher / HTTP keep-alive 등 native resource 셋업 가능.
2. L578 `describeResolvedContextEngineContractError(engineId, engine)` 또는 그 호출이 throw / non-null 반환 (contract violation).
3. L572-598 의 세 graceful-degrade 분기 (factory throw / validation throw / contract error) 어디에도 `engine.dispose?.()` 호출 또는 try/finally cleanup 부재.
4. `return resolveDefaultContextEngine(...)` 으로 legacy engine 반환 → invalid engine 인스턴스는 caller 에 도달 안 함.

caller-side dispose (`run.ts:3094` / `compact.queued.ts:108,301`) 는 resolveContextEngine 의 *반환* engine 에만 적용. invalid engine 은 반환 안 되므로 caller 가 받지 못함. **resolveContextEngine 자체가 유일한 dispose 책임 지점** 이나 호출이 없다. native resource 는 GC 시점까지 (또는 finalizer 없는 native handle 의 경우 process 종료까지) 방치.

호출 빈도: 매 turn (run.ts:1065), 매 compact (compact.queued.ts:56), 매 spawn (subagent-spawn.ts:461), 매 CLI /compact (cli-compaction.ts:208) — 시간당 수십~수백 회. fd / native handle 누적 → 장기 daemon 에서 EMFILE 위험.

## fix surface

L572-598 의 세 fallback 분기 각각에 `engine?.dispose?.()` 호출 추가 (try/catch swallow 로 안전 — caller-side 패턴 (runAgentCleanupStep) 와 동일). factory throw 분기는 engine 미할당이라 skip 가능.

```ts
} catch (validationError) {
  if (isDefaultEngine) throw validationError;
  await engine?.dispose?.().catch(() => {}); // 추가
  console.error(...);
  return resolveDefaultContextEngine(...);
}
```

XS PR (3-5 hunk).

## upstream-dup 검사 결과

- 6주 registry.ts contract validation 추가 PR (2677f7cf14, 6aa4515798) 이 dispose 처리 함께 안 함.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — 세 fallback 분기에 dispose 호출 + try/catch swallow 추가.
2. 회귀 테스트 — invalid factory mock (e.g. compact 함수 누락) + dispose vi.fn() 호출 검증.
3. types.ts 의 dispose contract 강화 (별도 follow-up): "factory 의 setup 단계에서 잡은 resource 는 dispose 에서 정리해야 한다" 명시.

## 관련 FIND

- FIND-context-engine-lifecycle-001: resolveContextEngine 의 contract validation fallback 에서 instantiated engine dispose 미호출.
