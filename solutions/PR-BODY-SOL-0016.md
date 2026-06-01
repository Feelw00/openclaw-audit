# fix(secrets): stage all files then commit so apply never half-migrates

> AI-assisted PR. Drafted with Claude Code (claude-opus-4-8). Fully tested: a new regression test in `src/secrets/apply.test.ts` fails before this change and passes after, and the fix was verified with a live before/after real-behavior proof (see below). Author has reviewed and understands the change.

## 1. Summary

- **Problem**: `runSecretsApply` migrates plaintext credentials to `SecretRef`s across `openclaw.json` plus N satellite files (`auth-profiles.json` / legacy `auth.json` / `.env`) with no cross-file transaction boundary. A fault partway through the commit leaves config and the auth-stores diverged.
- **Why it matters**: The satellite files are the credential source-of-truth (OAuth / api_key tokens). A divergence means either plaintext credentials left un-scrubbed on disk (leak surface) or config refs that no longer resolve (credentials fail to load). The existing rollback is best-effort and the same fault (disk full / EIO) that aborts the commit also defeats the restore, so the divergence becomes permanent.
- **What changed**: The commit block is restructured into a two-phase stage-then-commit. Phase A writes every satellite file's final content to a temp file in its own directory (through the same private-store write the live commit uses); if any staging throws, the temps are discarded and the apply rethrows without touching a single live file. Phase B commits config first via `replaceConfigFile`, then renames the staged temps into place (cheap metadata ops). Both fault-prone writes (the satellite content writes in Phase A and the config write in Phase B) now happen before any live satellite file advances, so a write fault on either leaves every store un-migrated.
- **What did NOT change**: No public API / contract change. `runSecretsApply`'s signature is unchanged, no new exports, no behavior change on the healthy (no-fault) path, and `replaceConfigFile` is still the config writer. The legacy best-effort snapshot rollback is retained for the now-rare Phase B failure path.

## 2. Change Type

- [x] Bug fix
- [ ] Refactor required for the fix
- [ ] New feature
- [ ] Docs / CI only

## 3. Scope

Touched areas:
- [x] `src/secrets/` (secrets apply commit path)

Files:
- `src/secrets/apply.ts` (+92 / -2): two-phase commit block (config committed before satellite renames), `stageWrite` / `commitStaged` / `discardStaged` helpers.
- `src/secrets/apply.test.ts` (+181): two new regression tests (satellite-write fault, config-write fault).

## 4. Linked Issue

Closes #88012

## 5. Root Cause

`runSecretsApply`'s write-mode commit block committed `openclaw.json` first via `replaceConfigFile`, then wrote the satellite credential files in a plain `for` loop of `writeTextFileAtomic` with **no cross-file transaction, journal, or single lock boundary**. Each file write is individually atomic (temp + rename), but there is no boundary spanning the set. If a satellite write faulted mid-loop (ENOSPC, EACCES from a `0o700` parent mkdir, or EIO) after config and earlier stores had already committed, config was migrated to refs while some auth-stores still held plaintext.

The missing guardrail: the `catch` block rolls back **best-effort only** (`restoreFileSnapshot` calls `writeTextFileAtomic` inside a `try/catch` that swallows failures, commented "Best effort only"). The fault that caused the throw (disk full / IO error) also fails the restore write, so the partial migration persists. There was no point in the flow where the fault-prone writes were forced to complete before any live file advanced.

## 6. Regression Test Plan

Two new tests in `src/secrets/apply.test.ts`, one per fault-prone write covered by the two-phase commit:

1. `"does not leave config and auth-store diverged when a satellite write faults mid-commit"` (the satellite write, Phase A):
   - Seeds a second agent auth-store with a plaintext api_key alongside config + the main store, so the commit touches >= 2 satellite files and opens a partial-commit window.
   - Injects a deterministic, sticky ENOSPC at the OS write primitive (`fs.writeFileSync`) scoped to the second auth-store's directory. The same fault also defeats the best-effort restore, reproducing the production failure mode. A real read-only parent dir does not work here because the private file store chmods the parent back to its dirMode before every write.
   - Asserts all-or-nothing: `config migrated => the faulting store also migrated`, and `faulting store still plaintext => config rolled back`. The diverged state (`config=ref AND store=plaintext`) must never persist.
2. `"does not leave config and auth-store diverged when the final config write faults after staging"` (the config write, Phase B):
   - Lets satellite staging succeed, then injects a sticky ENOSPC at the async write primitive (`node:fs/promises.writeFile`) scoped to the config temp (`replaceConfigFile` -> `replaceFileAtomic`).
   - Asserts the config-write fault leaves every store un-migrated: config keeps its plaintext and no satellite is advanced. Because config is committed before any satellite rename, the fault cannot leave migrated satellites diverged against an old config.

Before this change a satellite-write fault advances config to refs while a store keeps plaintext (migrated 1/2); after, nothing advances. The config-write test guards the ordering: committing config before any rename keeps a config fault all-or-nothing.

No fixture exposes a real credential value: stub tokens use the `// pragma: allowlist secret` annotation already established in this file.

Run: `pnpm test src/secrets/apply.test.ts`. Full gate before merge: `pnpm build && pnpm check && pnpm test`.

## 7. Security Impact (required)

This PR touches a **CODEOWNERS-gated security path** and handles plaintext credentials. It must be reviewed by the secrets owners before merge.

- **CODEOWNERS gate**: `.github/CODEOWNERS` assigns `/src/secrets/` to `@openclaw/openclaw-secops`. The only modified files (`src/secrets/apply.ts`, `src/secrets/apply.test.ts`) sit inside that gate, so this PR cannot merge without explicit secops review. Please notify `@openclaw/openclaw-secops`.
- **Credential source-of-truth path**: `runSecretsApply` migrates plaintext OAuth / api_key tokens into `SecretRef`s. The bug being fixed is itself a credential-safety issue: on a faulting apply, plaintext credentials could be left un-scrubbed on disk (a leak surface) or config refs could be left dangling (credentials fail to load). The fix makes the migration all-or-nothing, which is a net improvement to credential-handling safety.
- **No new permissions, no network calls, no new data exposure**: The change is purely a local filesystem commit-ordering change. Staging reuses the **same private-store write contract as the live commit**: `stageWrite` calls `writeTextFileAtomic`, which for the `0o600` credential stores routes through `@openclaw/fs-safe`'s private file store (re-creating the parent directory at `0o700`, guarding its directory identity, and writing the temp at `0o600`) before Phase B renames it into place in the same directory. No raw `fs.writeFileSync` is introduced on the credential path, so the existing private-store hardening (directory-mode re-assertion, directory-identity guard) is preserved end-to-end. No credential value is logged or added to test fixtures in plaintext.
- **Adjacent gate (informational)**: a follow-up for the concurrent-writer axis (FIND-002, `AUTH_STORE_LOCK` bypass during apply) would touch `/src/agents/auth-profiles/`, also secops-owned. It is intentionally out of scope here and will be a separate PR.

## 8. Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, OpenClaw checked out at base and head shas.
- **Steps**:
  1. Seed config + two agent auth-stores with plaintext credentials.
  2. Run `runSecretsApply({ write: true })` while a sticky ENOSPC is injected at the OS write primitive scoped to the second store's directory (so its commit throws and the rollback restore also throws).
  3. Read each store's final on-disk state.
- **Expected (after fix)**: all-or-nothing. Either every store + config advanced to refs, or every store + config stayed in its original state. Never `config=ref AND a store=plaintext`.
- **Actual (before fix)**: config advanced to a ref while the second store kept plaintext (migrated 1/2, diverged). The best-effort rollback could not restore because the same fault failed its write.

## 9. Evidence

Failing test before the change, passing after:

```text
# before this change (commit block as the plain for-loop)
$ pnpm test src/secrets/apply.test.ts
  x does not leave config and auth-store diverged when a satellite write faults mid-commit
    AssertionError: expected true to be false
    (configMigrated=true && failedStillPlaintext=true -> diverged persisted)

# after this change (two-phase stage-then-commit)
$ pnpm test src/secrets/apply.test.ts
  ok does not leave config and auth-store diverged when a satellite write faults mid-commit
  (all apply.test.ts cases green)
```

See the Real behavior proof section below for the live before/after measurement against the actual `runSecretsApply` (not just the unit test).

## 10. Human Verification

- Read the full diff and confirmed the two-phase ordering: Phase A stages every satellite write to a temp via `writeTextFileAtomic` (the same private-store atomic write the live commit uses); on any Phase A throw, temps are discarded and the apply rethrows with no live file touched; Phase B commits config first via `replaceConfigFile`, then renames the staged temps into place.
- Confirmed the healthy path is unaffected: the no-fault control trial migrates all stores + config exactly as before (verified in the real-behavior proof control trial).
- Confirmed no public contract change: `runSecretsApply` signature unchanged, no new exports. `stageWrite` reuses `writeTextFileAtomic` from `shared.ts` (the same primitive `restoreFileSnapshot` already uses), so staging shares the established private-store write path instead of introducing a new one.
- Confirmed no plaintext credential is written to logs or fixtures (stub tokens carry `// pragma: allowlist secret`).

## 11. Review Conversations

- [ ] secops (CODEOWNERS) review requested and addressed.
- [ ] Bot review comments (clawsweeper / Greptile) triaged and resolved.

## 12. Compatibility / Migration

No migration required. On-disk file formats, paths, modes, and the public API are unchanged. Existing data and a healthy apply behave identically; the only behavioral difference is on the fault path, where a faulting apply now leaves all stores un-migrated instead of half-migrated. No config or version bump.

## 13. Risks and Mitigations

- **Residual crash window (disclosed)**: Both fault-prone writes are now ordered before any live satellite advances (satellite content writes in Phase A, the config write at the start of Phase B), so a write fault on either leaves every store un-migrated. The only residual is a process crash *between* individual Phase B renames, which could leave a subset renamed. Renames are same-directory metadata ops with a fault window orders of magnitude smaller than writes, so the practical exposure is much lower than the original write-loop window. True crash-atomicity would require a single journal or directory rename, which is out of scope (the secrets files are spread across multiple directories; that is an M+ change). This is called out in the Real behavior proof "What was not tested".
- **Phase B failure rollback**: A rare Phase B failure (the config commit, which runs first, or a later rename collision) still falls back to the existing best-effort snapshot restore and discards any un-renamed temps, degrading to the original state rather than a partial commit. A config-commit fault happens before any rename, so nothing has advanced.
- **Temp file leftovers**: Temps use `process.pid` + `randomUUID()` names and are written through the private-store atomic write (exclusive `wx` create at the fs-safe layer, no clobber of a concurrent stage); `discardStaged` removes any un-committed temps in both the Phase A and Phase B failure paths.
- **CODEOWNERS / merge risk**: Cannot merge without secops review (mitigation: secops explicitly tagged in Security Impact).

---

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, runSecretsApply (src/secrets/apply.ts:835-855) commits one logical credential-migration transaction (plaintext credential -> SecretRef) as a non-atomic sequence: replaceConfigFile for openclaw.json, then a `for` loop of writeTextFileAtomic over N auth-profiles.json / legacy auth.json / .env. There is no cross-file transaction, journal, or single lock boundary. If a satellite write throws mid-loop (ENOSPC / EACCES / EIO) after earlier stores already committed, the catch block runs a best-effort rollback (restoreFileSnapshot wrapped in try/catch that swallows failures, "Best effort only"). When the same fault also fails a restore write, the partial migration persists: some auth-stores are left migrated to SecretRef while others stay plaintext, diverging the credential source-of-truth. With this patch, a mid-commit fault leaves no partial migration (all-or-nothing: full rollback or full commit).
- **Real environment tested**: macOS (darwin arm64), Node 23.x, OpenClaw worktrees checked out at the base and head shas (base = 615199a6a4, the fork-point this branch rebases onto). tsx executes src/secrets/apply.ts directly (--skip-build). Each trial builds a fresh isolated temp HOME via fs.mkdtempSync (os.tmpdir()); production ~/.openclaw and real credentials are never touched, and the temp HOME is removed after each trial. No external dependencies (no OAuth / LLM / network). The disk fault is injected deterministically through @openclaw/fs-safe's test-only DI seam (__setFsSafeTestHooksForTest.beforeFileStoreSyncPrivateWrite, gated on NODE_ENV=test) because OS permission denial is self-healed by fs-safe's ensurePrivateDirectorySync (it re-mkdirs and chmods the store directory).
- **Exact steps or command run after this patch**:

```text
$ pnpm install --frozen-lockfile                      (both worktrees, --skip-build)
$ node_modules/.bin/tsx <probe.ts>                    (worktree-relative)
  Control trial : runSecretsApply(write) with no fault -> expect full commit, no divergence.
  Defect trial  : fault injected so store#2 commit throws AND store#1 rollback restore throws.
                  Measures each store's final on-disk state (ref / plaintext / missing).
```

- **Evidence after fix**:

Live Node.js measurement of per-store final state after a mid-commit throw:

```text
[Build A] without this patch (base sha):
  control trial: stores={"store#1-alpha": "ref", "store#2-beta": "ref"} config=ref diverged=False
  defect  trial: threw=True stores={"store#1-alpha": "ref", "store#2-beta": "plaintext"} config=plaintext
                 migratedStores=1/2 diverged=True  (partial migration persisted)

[Build B] with this patch (head sha):
  control trial: stores={"store#1-alpha": "ref", "store#2-beta": "ref"} config=ref diverged=False
  defect  trial: threw=True stores={"store#1-alpha": "plaintext", "store#2-beta": "plaintext"} config=plaintext
                 migratedStores=0/2 diverged=False  (fault reached staging; every store rolled back)
```

- **Observed result after fix**: Without patch, a mid-commit throw leaves migratedStores=1/2 with diverged=True (a strict non-empty subset of auth-stores migrated to SecretRef while the rest stay plaintext). With patch, the same injected fault now throws inside Phase A staging (threw=True) and yields migratedStores=0/2 with diverged=False: because staging routes through the same private-store write the live commit uses, the injected fs-safe fault reaches the new `stageWrite` path and the apply aborts before any live file is touched, leaving every store in its original (un-migrated) state. The control trial reports diverged=False/False on both builds, confirming the healthy full-commit path is not flagged.
- **What was not tested**: SECURITY-SENSITIVE PATH: src/secrets/apply.ts touches the credential source-of-truth and is adjacent to the .github/CODEOWNERS /src/agents/*auth* gate (auth-profiles store). Any fix that introduces a cross-file transaction/journal or acquires AUTH_STORE_LOCK_OPTIONS during apply commits must be reviewed under that CODEOWNERS gate before merge. Not tested here: (1) the concurrent-writer lost-update axis (FIND-secrets-apply-cross-store-consistency-002, AUTH_STORE_LOCK bypass) which is a separate child task of this epic; (2) production-scale fault timing under real ENOSPC/EIO (modeled deterministically via the fs-safe DI seam rather than real disk exhaustion); (3) the legacy auth.json and .env satellite writes (same loop, not exercised by this plan); (4) the residual window where the process crashes between individual Phase B renames is not closed (true crash-atomicity would need a single journal or directory rename, which is out of scope here).
