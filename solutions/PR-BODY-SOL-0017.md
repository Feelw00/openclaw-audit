# fix(infra): guard against overwriting corrupt target session store during migration

> AI-assisted PR. Drafted and implemented with Claude Code (claude-opus-4-8). Fully tested: new regression test added and run (RED before fix, GREEN after); adjacent infra state-migration suites verified green.

## Summary

- **Problem**: During legacy session migration, `migrateLegacySessions` overwrites a *corrupt* target `agents/{id}/sessions/sessions.json` with a legacy-only merge, permanently destroying both the salvageable corrupt bytes and any target-only session records.
- **Why it matters**: This fires on the cold doctor/startup path exactly when a user most wants to recover a damaged store. The last on-disk copy is atomically clobbered with no warning, and recovery becomes impossible.
- **What changed**: When the target store exists but is unreadable, skip the save (and skip deleting the legacy store), and push a warning that mirrors the existing legacy-unreadable path. The corrupt target is left untouched for manual recovery; the legacy store is preserved so a later clean startup can re-migrate.
- **What did NOT change**: `saveSessionStore` and `readSessionStoreJson5` signatures are untouched. Happy-path behavior (target readable, or target file absent) is unchanged. No new public API, config, permission, or dependency.

## Change Type

- [x] Bug fix
- [ ] Refactor required for the fix
- [ ] New feature
- [ ] Docs / tests only

## Scope

- [x] `src/infra` (state migrations)
- [ ] Other

Touched files:
- `src/infra/state-migrations.ts` (migrateLegacySessions: add `targetReadable` gate)
- `src/infra/state-migrations.test.ts` (new regression test)

## Linked Issue

Closes #88017

## Root Cause

`migrateLegacySessions` reads the target store with `readSessionStoreJson5`. When the target `sessions.json` is corrupt (JSON5 parse fails), `readSessionStoreJson5` swallows the error and returns `{ store: {}, ok: false }`, so the corrupt target is treated as an empty store. The merge then becomes legacy-only (`{ ...emptyTarget, ...legacy }`).

The save gate was `(legacyParsed.ok || targetParsed.ok)`. Because the legacy store is readable, `legacyParsed.ok` alone satisfies the gate, and `targetParsed.ok` is never consulted on the save path. As a result `saveSessionStore` atomically overwrites the still-on-disk corrupt target with the legacy-only store. Target-only session records (keys with no legacy counterpart) are lost permanently, and the corrupt file can no longer be recovered by hand.

The missing guardrail is asymmetry: legacy corruption was already guarded (warn + skip delete), but target corruption had no symmetric check, so the overwrite was unconditional.

## Regression Test Plan

Added one focused test to the existing `describe("state migrations", ...)` block in `src/infra/state-migrations.test.ts`:

- `preserves a corrupt target session store instead of overwriting it with legacy-only data`

It reuses the existing `createLegacyStateFixture` / `detectLegacyStateMigrations` / `runLegacyStateMigrations` helpers, writes a corrupt target `sessions.json` (trailing garbage so JSON5 parse fails) that also holds a target-only key, then runs the public migration entry point and asserts:

1. The corrupt bytes survive on disk unchanged (`afterRaw === corruptBytes`).
2. No `Merged sessions store` change was committed.
3. A warning matching `/unreadable|corrupt/i` was pushed.
4. The legacy data is preserved (left in a `sessions.legacy-*` backup dir), so it is still recoverable.

Run locally:

```text
node scripts/run-vitest.mjs run --config test/vitest/vitest.infra.config.ts src/infra/state-migrations.test.ts
```

## Security Impact

No security-relevant change.

- **New permissions**: none.
- **Secrets**: no secret read/write/handling added or changed.
- **Network**: no network call added; this is a pure local filesystem migration.
- **Data scope**: the change strictly *narrows* the data the migration mutates - it preserves the existing target session store and the legacy store when the target is unreadable, rather than overwriting them. Session-store migration data is preserved, never deleted on the corrupt-target path. No CODEOWNERS @openclaw/secops paths are touched (`src/infra/state-migrations.ts` matches none of the `*auth*` / `sandbox*` / `cron/service/jobs.ts` / `cron/stagger.ts` patterns).

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, OpenClaw at branch base upstream/main `9de6abd8d7`.
- **Steps**:
  1. Have a legacy sessions dir with a readable `sessions/sessions.json`.
  2. Have a corrupt target `agents/{id}/sessions/sessions.json` (JSON5 parse fails) that also holds a target-only key.
  3. Trigger `runLegacyStateMigrations` (doctor / cold startup).
- **Expected**: the corrupt target is left in place, a warning is surfaced, legacy data is preserved.
- **Actual (before fix)**: the corrupt target is overwritten with a legacy-only store; the corrupt bytes and the target-only record are gone; no warning.

## Evidence

Failing before, passing after (same test file):

```text
# BEFORE fix (source reverted to pre-fix, new test kept): 1 failed
 FAIL  src/infra/state-migrations.test.ts > preserves a corrupt target session store ...
   expect(afterRaw).toContain("corrupt trailing garbage")
   - corrupt trailing garbage
   + {"agent:worker-1:legacydirect":{...},"agent:worker-1:desk":{...}}
 Test Files  1 failed (1)
      Tests  1 failed | 2 skipped (3)

# AFTER fix: all pass
 Test Files  1 passed (1)
      Tests  3 passed (3)
```

Adjacent infra state-migration suites after fix (no regression):

```text
state-migrations.fs / session-roundtrip / state-dir : 18 passed
state-migrations.orphan-keys                         : 11 passed
```

## Human Verification

- Manually inspected the `migrateLegacySessions` save gate and the legacy-delete guard to confirm `targetReadable` flips `overwritten`, `targetCorruptBytesSurvived`, and `warnedAboutTargetCorruption` exactly as measured.
- Manually confirmed the new test fails at the precise overwrite assertion when the source fix is reverted (the corrupt bytes are replaced by a legacy-only JSON blob), and passes with the fix.
- Confirmed the working tree is clean and the only behavioral delta is on the corrupt-target path.

## Review Conversations

- No bot comments yet (pre-publish).

## Compatibility / Migration

- Fully backward compatible. The change only adds a skip-and-warn branch for an unreadable target store. Existing migrations with a readable target or an absent target file behave identically (`targetReadable` is `true` in both cases).
- When the corrupt-target path is taken, the legacy store is intentionally preserved (renamed to a `sessions.legacy-{ts}` backup), so a subsequent clean startup can re-attempt the migration once the target is repaired or removed.

## Risks and Mitigations

- **Risk**: a corrupt target now persists instead of being silently replaced, so the migration does not "complete" on that path. **Mitigation**: this is intentional and strictly safer than destroying the last copy; the user is warned, and the legacy data is preserved for re-migration. Automatic repair/backup-then-save is deliberately out of scope (would expand the change and entangle with the separate multi-store atomicity work).
- **Risk**: regression in the happy path. **Mitigation**: `targetReadable` is `true` whenever the target is readable or absent, so the save and legacy-delete branches are unchanged there; verified by the adjacent infra suites (29 tests green).

---

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, migrateLegacySessions (state-migrations.ts:1197-1212, reached via the public runLegacyStateMigrations entry point used by doctor/cold-startup) gates the sessions save on the OR condition `(legacyParsed.ok || targetParsed.ok)` and never inspects `targetParsed.ok` on the save path. When the target sessions.json is corrupt (JSON5 parse fails), readSessionStoreJson5 swallows the error and returns `{store:{}, ok:false}`, so the corrupt target is treated as empty. Because the legacy store is readable, the gate passes and saveSessionStore overwrites the still-on-disk corrupt target file with a legacy-only merge. The corrupt bytes a human could have salvaged, and any target-only session records, are permanently destroyed. Legacy corruption is guarded (:1191 warn, :1239), but target corruption has no symmetric check. With this patch, a `targetParsed.ok` guard preserves the corrupt target instead of overwriting it.
- **Real environment tested**: macOS (darwin arm64), Node 23.x, OpenClaw run via tsx against this branch's TypeScript sources (no separate build step). Fully isolated: probe creates its own temp OPENCLAW_STATE_DIR and only touches files inside it; production state is never read or written. No external dependencies (no OAuth/LLM/channel/network) - pure filesystem migration. Deterministic: the OR-gate's asymmetric guard gap means the corrupt target is always overwritten when legacy is readable; no crash or timing is required.
- **Exact steps or command run after this patch**:

```text
$ # base sha (without patch) and head sha (with patch), each via tsx
$ npx tsx <probe.ts>                                  (worktree-relative)
  fixture in isolated OPENCLAW_STATE_DIR:
    legacy  sessions/sessions.json            valid JSON5, 2 keys w/ sessionId
    target  agents/main/sessions/sessions.json  corrupt JSON5 (trailing garbage)
                                                + 1 recoverable target-only record
  run: detectLegacyStateMigrations({}) -> runLegacyStateMigrations({detected})
  measure: was corrupt target overwritten / did corrupt bytes survive / any warning
```

- **Evidence after fix**:

Live Node.js (tsx) measurement of the corrupt target sessions.json after migration:

```text
[Build A] without this patch (base sha):
  beforeHasSentinel (target was corrupt):     True
  overwritten (corrupt -> legacy-only):       True
  targetCorruptBytesSurvived:                 False
  targetOnlyKeyPresent (recoverable record):  False
  legacyKeysPresent (legacy written to tgt):  True
  warnedAboutTargetCorruption:                False
  afterKeys:                                  ["agent:main:hooks:legacy-key-1", "agent:main:hooks:legacy-key-2"]

[Build B] with this patch (head sha):
  beforeHasSentinel (target was corrupt):     True
  overwritten (corrupt -> legacy-only):       False
  targetCorruptBytesSurvived:                 True
  targetOnlyKeyPresent (recoverable record):  False
  warnedAboutTargetCorruption:                True
  afterKeys:                                  []
```

- **Observed result after fix**: Without the patch, the corrupt target was overwritten with a legacy-only store (overwritten=True, corruptBytesSurvived=False, recoverable target-only record present=False) with no target-corruption warning (warned=False). With the patch, the corrupt target is preserved (overwritten=False, corruptBytesSurvived=True), so the file remains available for manual recovery.
- **What was not tested**: Multi-store split-state on mid-migration throw (FIND-002, separate axis - covered by a different scenario/fix). Real-world frequency of target sessions.json corruption (out of scope; the defect is the unconditional overwrite, not the corruption source). Doctor preview/confirm UI surfacing of corruption to the operator (out of allowed_paths).
