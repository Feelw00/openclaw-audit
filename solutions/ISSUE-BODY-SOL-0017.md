<!--
FILE THIS AS A BUG ISSUE (openclaw/openclaw), then put its number in PR-BODY-SOL-0017.md "Closes #<ISSUE_NUMBER>".
  title:  [Bug]: legacy session migration overwrites a corrupt target sessions.json with legacy-only data (permanent data loss)
  labels: bug
gh issue create --repo openclaw/openclaw --title "<title above>" --label bug --body-file solutions/ISSUE-BODY-SOL-0017.md
-->

**Bug type**: Behavior bug (incorrect output/state without crash)

**Beta release blocker**: No

## Summary

During legacy session migration, `migrateLegacySessions` overwrites a *corrupt* target `agents/{id}/sessions/sessions.json` with a legacy-only merge, permanently destroying both the salvageable corrupt bytes and any target-only session records, with no warning.

## Steps to reproduce

Deterministic, no external services, pure filesystem (probe uses its own temp `OPENCLAW_STATE_DIR`):

1. Create a legacy sessions dir with a readable `sessions/sessions.json` (valid JSON5, 2 keys).
2. Create a corrupt target `agents/{id}/sessions/sessions.json` (trailing garbage so JSON5 parse fails) that also holds a target-only key with no legacy counterpart.
3. Trigger the public migration entry point: `detectLegacyStateMigrations({})` then `runLegacyStateMigrations({detected})` (the doctor / cold-startup path).
4. Inspect the target file bytes and whether any warning was surfaced.

## Expected behavior

When the target store is unreadable, the migration leaves the corrupt target in place (so a human can salvage it), surfaces a warning, and preserves the legacy store so a later clean startup can re-migrate.

## Actual behavior

The corrupt target is atomically overwritten with a legacy-only store. The corrupt bytes and the target-only record are gone, and no target-corruption warning is emitted.

Root of the asymmetry: `readSessionStoreJson5` swallows the parse error and returns `{ store: {}, ok: false }`, so the corrupt target is treated as empty and the merge becomes legacy-only (`{ ...emptyTarget, ...legacy }`). The save gate is `(legacyParsed.ok || targetParsed.ok)`; because the legacy store is readable, `legacyParsed.ok` alone satisfies it and `targetParsed.ok` is never consulted on the save path. Legacy corruption is already guarded (warn + skip delete), but target corruption has no symmetric check, so the overwrite is unconditional.

A live two-build probe measured `overwritten=True, corruptBytesSurvived=False, warned=False` (without fix) vs `overwritten=False, corruptBytesSurvived=True, warned=True` (with fix).

## OpenClaw version

main branch, built from source at base upstream/main `9de6abd8d7`. Defect lines quoted below. Run under `tsx`.

## Operating system

macOS (darwin arm64). OS-independent (the overwrite is unconditional, no crash or timing required).

## Install method

Built from source (`pnpm`), `npx tsx`.

## Model

Not applicable. The defect is in the state-migration filesystem path and is reproduced by a deterministic probe that invokes no model.

## Provider / routing chain

Not applicable (no provider, gateway, or network call is on the defect path).

## Logs, screenshots, and evidence

```text
src/infra/state-migrations.ts  migrateLegacySessions (~1197-1212), via public runLegacyStateMigrations (doctor / cold-startup)
  save gate: (legacyParsed.ok || targetParsed.ok)   // targetParsed.ok never consulted on save path
  readSessionStoreJson5 on corrupt target -> { store: {}, ok: false }  // corrupt treated as empty
  legacy corruption guarded at :1191 (warn) / :1239 (skip delete); target corruption has NO symmetric guard

Regression repro (RED on this commit):
 FAIL  src/infra/state-migrations.test.ts > preserves a corrupt target session store ...
   expect(afterRaw).toContain("corrupt trailing garbage")
   - corrupt trailing garbage
   + {"agent:worker-1:legacydirect":{...},"agent:worker-1:desk":{...}}

Live two-build probe (isolated OPENCLAW_STATE_DIR):
 without fix: overwritten=True  corruptBytesSurvived=False warned=False  afterKeys=[legacy keys only]
 with fix:    overwritten=False corruptBytesSurvived=True  warned=True   afterKeys=[]
```

## Impact and severity

- Affected: users upgrading through the legacy session migration who have a damaged (corrupt) target `sessions.json`.
- Severity: data loss. This fires on the cold doctor/startup path exactly when the user most wants to recover a damaged store; the last on-disk copy is clobbered.
- Frequency: edge case (requires a corrupt target), but deterministic and irreversible when it occurs.
- Consequence: salvageable corrupt bytes and target-only session records are destroyed; recovery becomes impossible.

## Additional information

A fix is being prepared (add a `targetReadable` / `targetParsed.ok` guard symmetric to the existing legacy-corruption guard: skip the save and the legacy delete, push a warning, preserve the legacy store for re-migration). `src/infra/state-migrations.ts` matches no `@openclaw/secops` CODEOWNERS path. AI-assisted analysis (Claude Code), grounded in the quoted source, the RED regression test, and the live before/after probe.
