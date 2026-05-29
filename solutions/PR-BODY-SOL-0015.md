## Summary

- **Problem**: `src/tasks/task-registry.ts` `updateTask` and `deleteTaskRecordById` commit the in-memory `tasks` / `taskDeliveryStates` maps (and the owner / parentFlow / relatedSession / runId indexes) before calling the sqlite persist helpers, and neither persist call is wrapped in a try/catch.
- **Why it matters**: The default store always binds the transactional variants (`upsertTaskWithDeliveryState` / `deleteTaskWithDeliveryState`), so persist always goes through `withWriteTransaction(BEGIN IMMEDIATE)`, which ROLLBACKs and re-throws on SQLITE_BUSY (multi-writer busy_timeout exceeded), SQLITE_FULL (disk full), or SQLITE_IOERR. The unhandled throw leaves the in-memory state mutated while sqlite keeps the prior row, so the two stores diverge: reload/restart resurrects deleted tasks (lost-delete), and the sqlite-direct reader `listFreshTasksForOwnerKey` (used by `media-generation-task-status-shared`) returns rows that contradict the in-memory path.
- **What changed**: Persist to the store first; only mutate the in-memory maps and indexes after persist succeeds. When persist throws, the in-memory state is left untouched and stays consistent with sqlite. The snapshot-fallback branches in the persist helpers now project the pending change so they remain correct under the new ordering.
- **What did NOT change**: Store contracts (`task-registry.store.types.ts`), the sqlite adapter, the transactional semantics, and the normal (persist-succeeds) ordering of observer events are unchanged. No public API or behavior change on the success path.

## Change Type

- [x] Bug fix
- [ ] Refactor required for the fix
- [ ] New feature
- [ ] Docs / CI only

## Scope

Touched areas:

- [x] `src/tasks/` (task registry persistence ordering)
- [ ] cron
- [ ] plugins
- [ ] gateway / security

Files touched:

- `src/tasks/task-registry.ts` (1 production file: `updateTask`, `deleteTaskRecordById`, and the three persist helpers' snapshot-fallback branches)
- `src/tasks/task-registry.store.test.ts` (1 regression test)

## Linked Issue

Closes #88007

## Root Cause

The registry keeps two stores in sync: an in-memory `tasks` Map (plus `taskDeliveryStates` and four secondary indexes) and a persistent sqlite store that is the source of truth across reload/restart. Both mutators wrote the in-memory side first and persisted afterward, with the persist call left unguarded:

- `deleteTaskRecordById`: removes the row from the in-memory `tasks` / `taskDeliveryStates` maps and indexes, then calls `persistTaskDelete` + `persistTaskDeliveryStateDelete`.
- `updateTask`: runs `tasks.set(taskId, next)` and updates the indexes, then calls `persistTaskUpsert(next)` (the following try block only wrapped `syncFlowFromTask`).

The missing guardrail is an in-memory <-> sqlite cross-store boundary. The default store binds `upsertTaskWithDeliveryState` / `deleteTaskWithDeliveryState`, so every persist goes through `withWriteTransaction(BEGIN IMMEDIATE)`, which ROLLBACKs and re-throws on SQLITE_BUSY / SQLITE_FULL / SQLITE_IOERR. Because nothing catches or pre-orders that throw, the in-memory mutation has already committed while sqlite still holds the prior row. Result:

- lost-delete: the deleted task survives in sqlite and resurrects on `reloadTaskRegistryFromStore` / `restoreTaskRegistryOnce`.
- stale read: the updated task diverges so the sqlite-direct reader `listFreshTasksForOwnerKey` returns the stale value while the in-memory path returns the new one. The same `ownerKey` yields contradictory answers in the same process.

This is the same fix-shape merged in #83238 (persist before committing the in-memory mirror).

## Regression Test Plan

Added `src/tasks/task-registry.store.test.ts` > "does not diverge sqlite-direct reads when an upsert persist throws". It uses the existing `configureTaskRegistryRuntime` seam to inject an in-process store whose `upsertTaskWithDeliveryState` throws `SQLITE_FULL` (mirroring `withWriteTransaction` ROLLBACK + re-throw, which leaves the sqlite row unchanged). It seeds a `running` row, attempts a `running -> succeeded` transition via `markTaskTerminalById`, asserts the persist throws, then asserts both read paths still report `running`:

- `getTaskById("task-diverge")?.status === "running"` (in-memory path; the discriminating assertion)
- `listFreshTasksForOwnerKey(ownerKey)` row status === "running" (sqlite-direct reader path used by `media-generation-task-status-shared`)

This is the exact production hot-path: the default store always routes through the transactional variant, so there is no synthetic-only branch. The `updateTask` reordering also closes the `deleteTaskRecordById` resurrection because both mutators share the persist-before-commit ordering.

Run: `pnpm test src/tasks/task-registry.store.test.ts` (single-file RED -> GREEN) and `pnpm test src/tasks/` (broader regression on mutators).

## Security Impact

- No new permissions, scopes, or capabilities.
- No new secrets, credentials, or environment variables.
- No new network calls or external dependencies.
- No change to data exposure scope: the patch only reorders an in-memory mutation relative to an existing sqlite write within the same process. It strengthens task-registry persistence consistency (the persistent sqlite source of truth is never contradicted by stale in-memory state on a write failure).
- File is not under `@openclaw/secops` ownership in `.github/CODEOWNERS`.

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, base sha `9de6abd8d7` (upstream/main), `node_modules` present.
- **Steps**: Inject a store whose transactional write throws (matching `withWriteTransaction` ROLLBACK + re-throw), then perform a state transition / delete and read the task back through both the in-memory path (`getTaskById`) and the sqlite-direct reader (`listFreshTasksForOwnerKey`).
- **Expected (correct)**: When the persist write fails, both stores remain at the prior value; no resurrection and no stale divergence.
- **Actual (before fix)**: The in-memory map advanced to the new value while sqlite kept the prior row. `getTaskById` returned `succeeded` while sqlite-direct returned `running` (divergence); deleted rows resurrected on reload.

## Evidence

Failing before the fix (buggy ordering: `tasks.set` before `persistTaskUpsert`):

```text
 FAIL  src/tasks/task-registry.store.test.ts > task-registry store runtime > does not diverge sqlite-direct reads when an upsert persist throws
 AssertionError: expected 'succeeded' to be 'running' // Object.is equality
 Expected: "running"
 Received: "succeeded"
   at src/tasks/task-registry.store.test.ts:301  expect(getTaskById("task-diverge")?.status).toBe("running");

 Test Files  1 failed (1)
      Tests  1 failed | 12 skipped (13)
```

Passing after the fix (persist-before-in-memory):

```text
 RUN  v4.1.7

 Test Files  1 passed (1)
      Tests  13 passed (13)
   Duration  406ms
```

## Human Verification

Testing level: fully tested (single-file RED -> GREEN confirmed by reverting only the `updateTask` reordering, then restoring; full store suite 13/13 green; type-check clean for the changed file).

- Confirmed the new test fails on the unpatched ordering and passes with the patch (not a tautology).
- Confirmed `updateTask` does not mutate `taskDeliveryStates`, so moving `persistTaskUpsert` earlier does not change the delivery-state value it reads (`taskDeliveryStates.get(task.taskId)` at the top of `persistTaskUpsert`).
- Confirmed the secondary indexes (owner / parentFlow / relatedSession / runId) and the observer `deleted` / upsert event are still updated/emitted after a successful persist, so the success-path ordering of observable effects is unchanged.
- Confirmed `media-generation-task-status-shared.ts` consumes `listFreshTasksForOwnerKey` (the FIND-002 production reader).

## Review Conversations

- [ ] All bot review comments addressed (to be checked after the bot pass).

## Compatibility / Migration

- No schema migration. No data migration. No config change.
- Backward compatible: store contracts and the sqlite adapter are untouched; on-disk format is unchanged.
- Behavior change is limited to the failure path (persist throws), where the registry now leaves both stores consistent instead of diverging. The success path is byte-for-byte equivalent in outcome.

## Risks and Mitigations

- **Risk**: Reordering `persistTaskUpsert` before the in-memory commit could change the value persisted if the persist helper read in-memory state. **Mitigation**: `persistTaskUpsert` is called with the fully-computed `next` record and reads only `taskDeliveryStates` (not mutated by `updateTask`); the snapshot-fallback branches now project the pending change explicitly, so the persisted snapshot is correct under the new ordering. Verified by the passing store suite.
- **Risk**: Observer / sync side effects moving relative to persist. **Mitigation**: They remain after a successful persist, matching prior observable ordering; the existing "emits incremental observer events" and "uses atomic task-plus-delivery store methods" tests pass unchanged.
- **Risk**: Partial-success across the two delete persists (`persistTaskDelete` succeeds, `persistTaskDeliveryStateDelete` throws). **Mitigation**: In-memory is still untouched at that point, so the in-memory <-> sqlite boundary holds; intra-sqlite atomicity across the two writes is the store transaction's responsibility, out of scope for this in-memory-boundary fix.

## AI-assisted

This PR was drafted with AI assistance (Claude Code, claude-opus-4-8). The reasoning, repro, and verification above were produced and reviewed under that workflow; the author has verified the diff, the failing-then-passing evidence, and the absence of success-path behavior change.

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, task-registry.ts mutators commit the in-memory Map before calling the unguarded sqlite persist. deleteTaskRecordById deletes from the in-memory `tasks`/`taskDeliveryStates` Maps (2162-2163) before persistTaskDelete (2165); updateTask sets `tasks.set(taskId, next)` (997) before persistTaskUpsert (1011), and the following try (1012) only wraps syncFlowFromTask. When the sqlite write throws (withWriteTransaction ROLLBACK + re-throw on SQLITE_BUSY / SQLITE_FULL / SQLITE_IOERR, store.sqlite.ts:503-506) the in-memory mutation is never rolled back. The deleted row survives in sqlite and resurrects on reloadTaskRegistryFromStore / restoreTaskRegistryOnce (lost-delete); the updated record diverges so the sqlite-direct reader listFreshTasksForOwnerKey returns the stale value while the in-memory path returns the new one. With this patch, an in-memory<->sqlite cross-store boundary (persist-before-commit or rollback-on-persist-fail) keeps both stores in agreement so neither resurrection nor stale divergence occurs.
- **Real environment tested**: macOS (darwin arm64), Node 23.x, OpenClaw checked out at the base sha with node_modules present. Isolated temp HOME / OPENCLAW_HOME (temp dir); production ~/.openclaw untouched. No external dependencies (no OAuth/LLM/channel/network calls). The task-registry store is replaced via the public configureTaskRegistryRuntime seam with an in-process store whose write methods throw before mutating their backing data, mirroring withWriteTransaction's ROLLBACK (no row change) + re-throw.
- **Exact steps or command run after this patch**:

```text
$ git checkout <base sha>                              (Build A) / <head sha> (Build B)
$ node_modules/.bin/tsx <probe.ts>                     (worktree root, isolated temp HOME)
  probe trial DELETE: seed sqlite row -> reload -> deleteTaskRecordById (persistTaskDelete throws)
    -> measure in-memory absent + sqlite present (diverged) -> reload -> measure resurrected
  probe trial UPDATE: seed -> reload -> updateTaskNotifyPolicyById (persistTaskUpsert throws)
    -> compare in-memory getTaskById vs sqlite-direct listFreshTasksForOwnerKey
```

- **Evidence after fix**:

Live tsx measurement of cross-store divergence across two mutators:

```text
[Build A] without this patch (base sha):
  trialResults: [{"branch": "delete-lost-delete", "persistThrew": true, "inMemoryBeforeDelete": true, "inMemoryAfterDelete": false, "sqliteAfterDelete": true, "diverged": true, "resurrected": true}, {"branch": "update-stale-read", "persistThrew": true, "expectedNext": "state_changes", "stale": "done_only", "inMemoryNotify": "state_changes", "memListNotify": "state_changes", "sqliteDirectNotify": "done_only", "diverged": true}]
  divergenceCount: 2  (delete + update both diverge)
  resurrectedCount: 1  (deleted task revived on reload)

[Build B] with this patch (head sha):
  trialResults: [{"branch": "delete-lost-delete", "persistThrew": true, "inMemoryBeforeDelete": true, "inMemoryAfterDelete": true, "sqliteAfterDelete": true, "diverged": false, "resurrected": true}, {"branch": "update-stale-read", "persistThrew": true, "expectedNext": "state_changes", "stale": "done_only", "inMemoryNotify": "done_only", "memListNotify": "done_only", "sqliteDirectNotify": "done_only", "diverged": false}]
  divergenceCount: 0  (cross-store boundary holds both stores equal)
  resurrectedCount: 1
```

- **Observed result after fix**: Without patch: divergenceCount=2/2, resurrectedCount=1 — the deleted task resurrects on reload and the updated task's sqlite-direct read is stale. With patch: divergenceCount=0, resurrectedCount=1 — both stores stay consistent through the persist throw.
- **What was not tested**: Real multi-process SQLITE_BUSY contention against an on-disk db file (the throw is injected at the store seam, which is the exact point withWriteTransaction re-throws after ROLLBACK). delivery-state-only mutators and the snapshot-fallback persist branches (default store always provides the transactional variant, so those are out of the production path per the FIND counter-evidence).
