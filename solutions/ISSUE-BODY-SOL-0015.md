<!--
FILE THIS AS A BUG ISSUE (openclaw/openclaw), then put its number in PR-BODY-SOL-0015.md "Closes #N".
  title:  [Bug]: task-registry in-memory state diverges from sqlite when a persist write throws (lost-delete + stale sqlite-direct reads)
  labels: bug
gh issue create --repo openclaw/openclaw --title "<title above>" --label bug --body-file solutions/ISSUE-BODY-SOL-0015.md
-->

**Bug type**: Behavior bug (incorrect output/state without crash)

**Beta release blocker**: No

## Summary

`task-registry.ts` mutates its in-memory `tasks` map (and the secondary indexes) before the unguarded sqlite persist call, so when the sqlite write throws (`SQLITE_BUSY` / `SQLITE_FULL` / `SQLITE_IOERR`, after `withWriteTransaction(BEGIN IMMEDIATE)` ROLLBACKs and re-throws) the two stores diverge: deleted tasks resurrect on reload and the sqlite-direct reader returns a value that contradicts the in-memory path.

## Steps to reproduce

Deterministic, no external services. Replace the default store via the public `configureTaskRegistryRuntime` seam with an in-process store whose transactional write throws (this mirrors `withWriteTransaction` ROLLBACK + re-throw, which leaves the sqlite row unchanged):

1. Seed a `running` task row and reload the registry from the store.
2. Attempt a `running -> succeeded` transition via `markTaskTerminalById` (drives `updateTask`), with the injected store's `upsertTaskWithDeliveryState` throwing `SQLITE_FULL`.
3. Read the task back through both paths: `getTaskById(...)` (in-memory) and `listFreshTasksForOwnerKey(ownerKey)` (sqlite-direct reader used by `media-generation-task-status-shared`).
4. Separately: delete a row via `deleteTaskRecordById` while `persistTaskDelete` throws, then call `reloadTaskRegistryFromStore`.

This is the production hot path: the default store always binds the transactional variants, so every persist routes through `withWriteTransaction`. There is no synthetic-only branch.

## Expected behavior

When the persist write fails, both stores stay at the prior value: no resurrection of deleted tasks, and the in-memory path and the sqlite-direct reader agree.

## Actual behavior

- Update: `getTaskById` returns `succeeded` while `listFreshTasksForOwnerKey` returns `running` for the same `ownerKey` in the same process (divergence).
- Delete: the deleted row survives in sqlite and resurrects on `reloadTaskRegistryFromStore` / `restoreTaskRegistryOnce` (lost-delete).

A live two-build probe in an isolated temp HOME measured `divergenceCount=2/2` and `resurrectedCount=1` on the current source.

## OpenClaw version

main branch, built from source at commit `9de6abd8d7`. The defect lines are quoted below from this commit.

## Operating system

macOS (darwin arm64). The defect is OS-independent (pure in-memory-vs-sqlite ordering).

## Install method

Built from source (`pnpm`), run under `tsx` / vitest.

## Model

Not applicable. The defect is in the task-registry persistence-ordering path and is reproduced by a deterministic test/probe that invokes no model.

## Provider / routing chain

Not applicable (no provider, gateway, or network call is on the defect path).

## Logs, screenshots, and evidence

```text
src/tasks/task-registry.ts
  updateTask:            tasks.set(taskId, next)  (in-memory commit)  ->  persistTaskUpsert(next)  (unguarded; following try only wraps syncFlowFromTask)
  deleteTaskRecordById:  remove from tasks / taskDeliveryStates + indexes  ->  persistTaskDelete + persistTaskDeliveryStateDelete  (unguarded)
store.sqlite.ts: withWriteTransaction(BEGIN IMMEDIATE) ROLLBACKs and re-throws on SQLITE_BUSY / SQLITE_FULL / SQLITE_IOERR

Regression repro (RED on this commit):
 FAIL  src/tasks/task-registry.store.test.ts > does not diverge sqlite-direct reads when an upsert persist throws
 AssertionError: expected 'succeeded' to be 'running'

Live two-build probe (isolated temp HOME, no network):
 without fix: divergenceCount=2/2  resurrectedCount=1   (delete resurrects on reload; update sqlite-direct read stale)
```

## Impact and severity

- Affected: any deployment where a task-registry write can fail at the sqlite layer (multi-writer `SQLITE_BUSY` past busy_timeout, disk full `SQLITE_FULL`, `SQLITE_IOERR`).
- Severity: reliability / data-consistency (the persistent sqlite source of truth is contradicted by stale in-memory state).
- Frequency: edge case (only on a persist failure), but deterministic once the failure occurs.
- Consequence: deleted tasks resurrect across reload/restart; the same `ownerKey` yields contradictory task status from the in-memory path vs the sqlite-direct reader.

## Additional information

A fix is being prepared (persist-before-commit / rollback-on-persist-fail at the in-memory <-> sqlite boundary), matching the fix-shape already merged in #83238. AI-assisted analysis (Claude Code). This report is grounded in the quoted source lines, the RED regression test, and the live before/after probe described above.
