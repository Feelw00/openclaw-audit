---
candidate_id: CAND-035
type: single
finding_ids:
  - FIND-context-engine-lifecycle-002
cluster_rationale: "단일 결함 — init.ts:13-23 `ensureContextEnginesInitialized` 가 `initialized=true` (L19) 를 `registerLegacyContextEngine()` (L22) *이전*에 set 하는 set-before-action 안티패턴. register throw 시 initialized 가 latch 된 상태로 throw propagate → 동일 process 후속 호출은 early return 으로 legacy 영구 미등록 → resolveContextEngine 의 모든 default fallback 영구 throw. 현재 코드에서 register throw 빈도는 낮으나 (SQLite migration revert 694ca50e97 의 시도가 있었음, future re-attempt 시 즉시 활성), set-before-action 자체가 future-fragility. **DEDUP 결정**: FIND-context-engine-error-boundary-001 과 FIND-context-engine-lifecycle-002 가 동일 코드 (init.ts:19 `initialized = true`) 를 다른 axis (error-boundary / lifecycle) 로 진단. 동일 fix surface (ordering swap or try/catch+flag rollback). FIND-012 가 lifecycle 측면에서 보다 정밀한 framing (init flag latch → permanent lockout) 을 가지므로 본 CAND 에 포함, FIND-009 는 cluster 에서 제외 (ready 에 잔존). FIND 본문 자체는 append-only 라 수정 없음."
proposed_title: "context-engine/init: register-then-set ordering to allow retry on legacy registration failure"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "694ca50e97 Revert 'refactor: move runtime state to SQLite'"
    - "f91de52f0d refactor: move runtime state to SQLite"
    - "59d07f0ab4 fix(plugins): roll back failed register globals"
    - "2677f7cf14 fix: validate resolved context engine contracts (#63222)"
  finding: "6주 init.ts 직접 commit 0건. 인접 영역의 SQLite migration 시도/revert (f91de52f0d → 694ca50e97, 2026-05-13) 가 register 의 async/IO surface 확장 가능성을 시사 — 본 FIND 의 ordering fragility 가 활성화될 미래 trigger. 59d07f0ab4 (registry rollback) 는 init flag reset 경로 미추가."
  pr_search: "gh pr list --search 'ensureContextEnginesInitialized OR initialized partial context-engine' → PR #81242 (subagent isolation, init 호출 우회 측면) + #73161 (Discord 무관). init.ts flag 패턴 변경 PR 0건."
  related_open_pr: null
  related_open_pr_notes: "PR #81242 는 init 호출 자체를 우회하는 다른 axis. flag ordering 과 직접 충돌 없음."
  duplicate_decision: not-duplicate
  cross_refs_other_cells:
    - context-engine-error-boundary (FIND-context-engine-error-boundary-001 dedup 처리: 동일 fix surface 라 본 CAND 에 흡수, finding_ids 미포함)
cross_refs: []
---

# context-engine/init: register-then-set ordering to allow retry on legacy registration failure

## 문제 요약

`src/context-engine/init.ts:13-23` `ensureContextEnginesInitialized()`:

```ts
let initialized = false;
export function ensureContextEnginesInitialized(): void {
  if (initialized) return;
  initialized = true;          // L19 — set 우선
  registerLegacyContextEngine(); // L22 — 실제 작업
}
```

`registerLegacyContextEngine` 이 throw 하면 (현재 코드에선 가능성 0 에 가깝지만 SQLite migration 재시도 / sealed globalThis sandbox 환경 등에서 surface 활성화), `initialized=true` 가 이미 latch 된 채 throw propagate. 동일 process 의 후속 caller 가 호출해도 early return → 영구 미등록 → `resolveContextEngine` 가 default 경로 (`engines.get("legacy")` undefined → L549 throw `"Context engine \"legacy\" is not registered"`) 로 영구 실패. process restart 만 복구.

## dedup 결정

이 CAND 는 두 FIND 의 중복 진단을 단일 fix surface 로 통합:

- **FIND-context-engine-error-boundary-001** (P3 error-boundary): "init.ts sets initialized=true before register — partial-state lockout on throw" — 동일 init.ts:19 / L22 line, error-boundary 관점.
- **FIND-context-engine-lifecycle-002** (P3 lifecycle): "ensureContextEnginesInitialized flips flag before legacy registration" — 동일 코드, lifecycle 관점.

두 FIND 의 fix surface 가 동일 (ordering swap or try/catch+flag rollback) 이고 1줄 PR 로 함께 해결. clusterer 판정: lifecycle framing (init flag latch → resolveContextEngine permanent lockout) 이 더 직접적인 발현 chain 을 보여주므로 FIND-context-engine-lifecycle-002 를 finding_ids 에 포함. FIND-context-engine-error-boundary-001 은 ready/ 에 잔존 (FIND 본문 append-only — 수정 없음). 두 FIND 의 본문은 cross-referencible.

## fix surface

옵션 A (ordering swap):
```ts
export function ensureContextEnginesInitialized(): void {
  if (initialized) return;
  registerLegacyContextEngine();
  initialized = true;  // 성공 후에만
}
```
재진입 위험 없음 (현재 register 가 module side-effect 트리거 안 함 grep 확인).

옵션 B (Result + flag rollback): registerLegacyContextEngine 이 `Result<void, E>` 반환하도록 + caller 가 ok 시에만 flag set.

XS PR (1-3 라인).

## upstream-dup 검사 결과

- 6주 init.ts 직접 commit 0건.
- SQLite migration revert (694ca50e97) 가 본 ordering 의 future-fragility evidence.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — ordering swap (옵션 A) 시도.
2. 회귀 테스트 — `registerLegacyContextEngine` throw mock + ensureContextEnginesInitialized 호출 + 재호출 시 register 재시도 검증 (context-engine.test.ts:1137 의 idempotent 테스트 옆에).
3. CAL-003 회피 — primary path inversion 아님 (보호 경로 자체가 부재, set-before-action ordering 의 fragility).

## 관련 FIND

- FIND-context-engine-lifecycle-002 (CAND 에 포함): lifecycle 관점 (flag latch → permanent lockout).
- FIND-context-engine-error-boundary-001 (CAND 에서 제외, ready 잔존): error-boundary 관점 (동일 fix surface 로 통합).
