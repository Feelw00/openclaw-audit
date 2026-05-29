<!--
FILE THIS AS A BUG ISSUE (openclaw/openclaw), then put its number in PR-BODY-SOL-0016.md "Closes #<TBD>".
  title:  [Bug]: `secrets apply` half-migrates credentials when a satellite write faults mid-commit (config=ref while an auth-store stays plaintext)
  labels: bug
NOTE: the fix PR touches /src/secrets/ (CODEOWNERS @openclaw/openclaw-secops). Tag secops on the PR.
gh issue create --repo openclaw/openclaw --title "<title above>" --label bug --body-file solutions/ISSUE-BODY-SOL-0016.md
-->

**Bug type**: Behavior bug (incorrect output/state without crash)

**Beta release blocker**: No

## Summary

`runSecretsApply` migrates plaintext credentials to `SecretRef`s across `openclaw.json` plus N satellite files (`auth-profiles.json` / legacy `auth.json` / `.env`) with no cross-file transaction boundary, so a fault partway through the commit leaves config migrated to refs while one or more auth-stores still hold plaintext (credential source-of-truth divergence).

## Steps to reproduce

Deterministic, no external services (the OS write fault is injected via the `@openclaw/fs-safe` test-only DI seam, gated on `NODE_ENV=test`, because real OS permission denial is self-healed by `ensurePrivateDirectorySync`):

1. Seed `openclaw.json` plus two agent auth-stores, each holding a plaintext api_key, so the commit touches >= 2 satellite files.
2. Run `runSecretsApply({ write: true })` with a sticky `ENOSPC` scoped to the second store's directory, so its write throws after config and the first store already committed. The same fault also defeats the best-effort restore.
3. Read each store's final on-disk state (ref / plaintext / missing).

## Expected behavior

All-or-nothing: either every store + config advanced to refs, or every store + config stayed in its original state. The diverged state (`config=ref AND a store=plaintext`) must never persist.

## Actual behavior

`config` advanced to a ref while the second store kept plaintext (`migratedStores=1/2`, `diverged=true`). The `catch` block runs a best-effort rollback (`restoreFileSnapshot`, commented "Best effort only"), but the same fault that aborted the commit also fails the restore write, so the partial migration persists permanently.

A live two-build probe in an isolated temp HOME measured `migratedStores=1/2 diverged=True` (without fix) vs `2/2 diverged=False` (with fix); the no-fault control trial is `diverged=False` on both builds.

## OpenClaw version

main branch, built from source (base sha `c559776c51` for the probe; defect lines quoted below). Run under `tsx`.

## Operating system

macOS (darwin arm64). The defect is OS-independent (non-atomic multi-file commit).

## Install method

Built from source (`pnpm`), `tsx --skip-build`.

## Model

Not applicable. The defect is in the secrets-apply filesystem commit path and is reproduced by a deterministic probe that invokes no model.

## Provider / routing chain

Not applicable (no provider, gateway, or network call is on the defect path).

## Logs, screenshots, and evidence

```text
src/secrets/apply.ts (~835-855): write-mode commit block
  replaceConfigFile(openclaw.json)                 // config committed first
  for (file of satelliteFiles) writeTextFileAtomic(file)   // plain loop, NO cross-file transaction / journal / single lock
  catch { restoreFileSnapshot(...) }               // "Best effort only" - swallows failures; same fault defeats the restore

Live two-build probe (isolated temp HOME, no network; fault via @openclaw/fs-safe test DI seam):
 without fix: defect trial threw=True  stores={store#1:"ref", store#2:"plaintext"} config=ref   migratedStores=1/2 diverged=True
 with fix:    defect trial threw=False stores={store#1:"ref", store#2:"ref"}       config=ref   migratedStores=2/2 diverged=False
 control (no fault): stores={store#1:"ref", store#2:"ref"} config=ref diverged=False on both builds
```

## Impact and severity

- Affected: any deployment running `secrets apply` (plaintext -> SecretRef migration) across more than one credential file when a write faults (disk full, EACCES, EIO).
- Severity: credential-safety / reliability. A divergence leaves either plaintext credentials un-scrubbed on disk (leak surface) or config refs that no longer resolve (credentials fail to load).
- Frequency: edge case (fault during apply), but the partial commit is permanent because the rollback is defeated by the same fault.
- Consequence: credential source-of-truth (the satellite auth-stores) diverges from config and cannot self-heal.

## Additional information

A fix is being prepared (two-phase stage-then-commit so the fault-prone writes never touch live files until staged, making the migration all-or-nothing). The fix PR modifies `/src/secrets/`, which `.github/CODEOWNERS` assigns to `@openclaw/openclaw-secops`; secops review is required. No fixture contains a real credential (stub tokens carry the `// pragma: allowlist secret` annotation already used in that file). AI-assisted analysis (Claude Code), grounded in the quoted source and the live before/after probe.
