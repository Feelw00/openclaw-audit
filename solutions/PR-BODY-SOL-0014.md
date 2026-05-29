## Summary

- **Problem**: `FileAuthStorageBackend.withLock` (sync, `auth-storage.ts:119`) and `withLockAsync` (async, `auth-storage.ts:164`) persist `auth.json` with a raw `writeFileSync(this.authPath, next)` + `chmodSync`. That opens the destination with `O_TRUNC` in place, so the truncate and the final byte flush are not atomic.
- **Why it matters**: `auth.json` is the sole on-disk source of truth for API keys and OAuth refresh tokens (not regenerable). If the process is SIGKILLed or the machine loses power inside that non-atomic window, `auth.json` is left empty or as partial JSON. On the next launch `reload()`'s `JSON.parse` throws, `loadError` is set, and `persistProviderChange()` early-returns while `loadError` is set (`:287`) so the corrupt file is never overwritten. Every provider credential is locked out until the user manually re-runs `/login`. OAuth refresh (`withLockAsync`) performs the same write on every token expiry, so the exposure recurs.
- **What changed**: Both credential-write sites now call `replaceFileAtomicSync({ filePath, content: next, mode: 0o600, tempPrefix: "auth.json" })`, the same temp-file + atomic-rename helper already used in this tree by `session-file-repair.ts:409` for in-place transcript rewrites. A crash now lands on the sibling temp file and `auth.json` keeps its previous contents. The `mode: 0o600` option preserves the credential file permission, removing the separate `chmodSync`.
- **What did NOT change**: Public `AuthStorage` API (`set` / `get` / `getApiKey` / `withLock` / `withLockAsync` signatures), the success-path round-trip, the proper-lockfile advisory lock, and the initial `ensureFileExists` seed write (an empty `{}` whose corruption is not a credential lockout). No behavior change on the success path. Sibling writers in the original CAND-045 cluster (session transcript `.jsonl`, `settings.json`) are out of scope for this single fix.

## Change Type

- [x] Bug fix
- [ ] Refactor required for the fix
- [ ] New feature
- [ ] Docs / CI only

## Scope

Touched areas:

- [x] `src/agents/sessions/` (credential persistence atomicity)
- [ ] cron
- [ ] plugins
- [x] gateway / security (credential-bearing path; `@openclaw/secops`-owned file, see Security Impact)

Files touched:

- `src/agents/sessions/auth-storage.ts` (1 production file: `withLock` + `withLockAsync` write sites, plus the helper import; ~10 lines)
- `src/agents/sessions/auth-storage.test.ts` (1 new regression test)

## Linked Issue

Closes #TBD

(Issue to be filed at publish time; this line is updated with the real number before opening the PR.)

## Root Cause

`auth.json` holds API keys and OAuth refresh tokens and is the only persistent copy. The two persist paths rewrote it in place:

- `withLock` (sync, `:119`): `writeFileSync(this.authPath, next, "utf-8")` then `chmodSync(this.authPath, 0o600)`.
- `withLockAsync` (async, `:164`): identical raw write, hit on every OAuth refresh.

`writeFileSync` opens the target with `O_TRUNC`, zeroing the existing bytes before writing the new ones. A SIGKILL / power-loss / panic between that truncate and the final flush leaves `auth.json` as a 0-byte or partial-JSON file. On the next boot:

1. `reload()` -> `parseStorageData` -> `JSON.parse(partial)` throws.
2. The catch sets `loadError` and `this.data = {}`.
3. `persistProviderChange()` has `if (this.loadError) return;` (`:287`), so even a subsequent credential write cannot overwrite the corrupt file.

The result is a silent, total credential lockout with no automatic recovery: every provider is unauthenticated until the user notices and re-runs `/login`. The proper-lockfile advisory lock (`:115`) guards only multi-process lost-update; it does nothing for single-write crash atomicity (if the holder crashes, the file is already truncated).

The same codebase already solves this exact shape elsewhere: `session-file-repair.ts:409` `repairSessionFile()` rewrites its file via `replaceFileAtomic` (temp + atomic rename). The credential writer was the inconsistent one. This patch makes it consistent with the established in-tree pattern, reusing `replaceFileAtomicSync` re-exported at `src/infra/replace-file.ts:13`.

## Regression Test Plan

Added `src/agents/sessions/auth-storage.test.ts` > "does not lock out credentials when a write crashes mid-flush". It seeds a valid credential into a real temp `auth.json`, then models a SIGKILL during the next persist and asserts the seed still loads on reopen.

The crash is injected at the actual write call, not by corrupting `auth.json` directly, so the test discriminates raw vs atomic:

- It mocks `node:fs` (via `vi.mock`) so both the named-import `writeFileSync` used by `auth-storage.ts` and the default-import `writeFileSync` used by the fs-safe atomic helper route through a single crash hook. The hook fires only when the write target is `auth.json` itself: it truncates the file and throws (modeling the `O_TRUNC` partial-write state and the dying process).
- RED (raw `writeFileSync`): the persist writes straight to `auth.json`, the hook fires, `auth.json` is truncated, the next `reload()` throws on `JSON.parse`, `getApiKey("anthropic")` returns `undefined` -> lockout. Test fails.
- GREEN (`replaceFileAtomicSync`): the persist writes to a sibling temp file then renames; it never writes `auth.json` directly, the hook never fires, `set()` completes cleanly, and `auth.json` retains the seed. Test passes.

Confirmed RED on the unpatched file and GREEN on the patched file by swapping only `auth-storage.ts` between the base and head versions and rerunning the single test (see Evidence). This is the exact production path: `set()` -> `persistProviderChange` -> `withLock` -> the patched write site.

Run: `node_modules/.bin/vitest run src/agents/sessions/auth-storage.test.ts` (single-file RED -> GREEN) and `pnpm test src/agents/sessions/` (broader regression on the auth backend).

## Security Impact

- This file is under `@openclaw/secops` ownership in `.github/CODEOWNERS` (`/src/agents/**/*auth*.ts`). Secops review/approval is required before merge.
- The patch handles plaintext credentials (`auth.json` stores API keys and OAuth refresh tokens). It does not weaken their protection:
  - File permission is preserved at `0o600`. `replaceFileAtomicSync` creates the temp file with `{ mode: 0o600, flag: "wx" }` and re-applies `chmodSync(filePath, 0o600)` after the rename, so the credential file is never world/group-readable, including the transient temp file (the prior code applied `0o600` only after the write; the new code applies it at temp-create time as well).
  - The temp file lives in the same directory as `auth.json` (which is created `mode 0o700`), is named with a fixed `auth.json` prefix, and is unlinked on success or cleaned up on failure / process exit, so no plaintext credential is left in a less-protected location.
- No new permissions, scopes, secrets, environment variables, network calls, or external dependencies. `replaceFileAtomicSync` is already a dependency of this tree.
- Net security posture improves: a crash no longer leaves a 0-byte or partial credential file, and credentials survive the crash instead of being lost.

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, OpenClaw checked out at base sha `9de6abd8d7` (upstream/main) / head sha `294819ced4`, `node_modules` present. Isolated temp `OPENCLAW_HOME`; `auth.json` lives under a temp dir and is passed as an explicit `authPath`, so production `~/.openclaw` is never touched. No external dependencies (api_key credential only; no OAuth/LLM/channel calls).
- **Steps**: Seed a valid credential, then model a crash during the next persist (truncate the destination and throw at the write call), then reopen and read the credential back.
- **Expected (correct)**: After a crash mid-write the prior valid credential still loads on the next boot.
- **Actual (before fix)**: `auth.json` is truncated, `reload()` fails `JSON.parse`, `getApiKey` returns `undefined`, and `persistProviderChange` early-returns on `loadError` so the corrupt file is never repaired -> full lockout.

## Evidence

Live `node` (tsx) measurement of the without-fix lockout against a modeled crash-truncate of `auth.json` (pre-sol baseline, `PROOF-CAND-045-pre-20260529-070725.md`, isolated temp `OPENCLAW_HOME`):

```text
$ export OPENCLAW_HOME=$(mktemp -d)
$ node_modules/.bin/tsx <probe.ts>     (worktree root; imports src/agents/sessions/auth-storage.ts)
  control branch        lockout: false   (full write round-trips cleanly)
  crash-truncate branch keyBeforeCrash: <present>
  crash-truncate branch loadFailed:     true     (JSON.parse threw -> loadError)
  crash-truncate branch keyAfterCrash:  <empty>
  crash-truncate branch recoveryNoOp:   true      (persistProviderChange early-return on loadError)
  crash-truncate branch lockout:        true
```

Regression test failing before the fix (raw `writeFileSync`, RED) and passing after (atomic, GREEN), confirmed by swapping only `auth-storage.ts` between base and head and rerunning `node_modules/.bin/vitest`:

```text
[RED] base auth-storage.ts (raw writeFileSync):
 FAIL  src/agents/sessions/auth-storage.test.ts > does not lock out credentials when a write crashes mid-flush
 AssertionError: expected undefined to be 'sk-test-SEED-12345'
 Expected: "sk-test-SEED-12345"
 Received: undefined
   at src/agents/sessions/auth-storage.test.ts:100  expect(await reopened.getApiKey("anthropic")).toBe("sk-test-SEED-12345")
 Test Files  1 failed (1)

[GREEN] head auth-storage.ts (replaceFileAtomicSync):
 RUN  v4.1.7
 Test Files  1 passed (1)
      Tests  1 passed (1)
```

This RED -> GREEN swap (run via `node`, against the real production code path `set()` -> `withLock` -> the write site) is the authoritative with/without evidence for this fix: the crash is injected at the write call, so the raw build truncates `auth.json` and locks out while the atomic build writes a temp file and preserves the credential.

## Human Verification

Testing level: fully tested. The regression test was confirmed to fail on the unpatched (raw `writeFileSync`) file and pass on the patched (`replaceFileAtomicSync`) file by swapping only `auth-storage.ts` between the base and head versions; not a tautology. Type-check clean for the changed file.

- Confirmed the crash hook fires for the raw path (writes `auth.json` directly) and never fires for the atomic path (writes a sibling temp file + rename), which is precisely the crash-safety being asserted.
- Confirmed `chmodSync` and `writeFileSync` imports remain used by `ensureFileExists`, so the diff introduces no unused-import lint failure.
- Confirmed `replaceFileAtomicSync` applies `0o600` at temp-create and re-applies it after rename, preserving the credential permission.
- Confirmed upstream/main still has the raw `writeFileSync` + `chmodSync` at `:119` / `:164` (not fixed upstream) and that no open/merged PR touches `src/agents/sessions/auth-storage.ts`'s crash-atomicity (PRs #39059 auth-store sealing, #87697 cooldowns, #66223/#66911 auth-clean all touch different files: `auth-profiles/*`, `sealed-json-file.ts`).

## Review Conversations

- [ ] All bot review comments addressed (to be checked after the bot pass).

## Compatibility / Migration

- No schema migration. No data migration. No config change.
- Backward compatible: the on-disk `auth.json` format and the public `AuthStorage` API are unchanged. The success-path outcome (write -> reload round-trip) is identical; only the failure path changes (a crash now preserves the prior credential instead of corrupting it).
- The async `withLockAsync` now performs a sync write internally, which is acceptable: the I/O was already synchronous (`writeFileSync`), the helper is sync, and no caller contract depends on the write being async.

## Risks and Mitigations

- **Risk**: The atomic rename interacts with the proper-lockfile advisory lock. **Mitigation**: the lock file is keyed off the original path in a separate lock directory and is unaffected by renaming the data file into place (reviewed; the lock is held across the whole `withLock` body, and the rename targets `auth.json`, not the lock).
- **Risk**: `replaceFileAtomicSync` could change the persisted permission. **Mitigation**: it writes the temp file with `{ mode: 0o600 }` and re-`chmod`s the destination to `0o600` after rename, matching the prior `chmodSync(authPath, 0o600)`.
- **Risk**: The initial `ensureFileExists` seed write (`:77`) is still a raw `writeFileSync("{}")`. **Mitigation**: deliberately out of scope; an empty `{}` seed that gets truncated holds no credential, so its corruption is not a lockout (no existing key is lost). Scoped to keep the change XS and focused on the credential-loss path.

## AI-assisted

This PR was drafted with AI assistance (Claude Code, claude-opus-4-8). The reasoning, repro, and verification above were produced and reviewed under that workflow; the author has verified the diff, the failing-then-passing evidence, the permission preservation, and the absence of success-path behavior change.

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, `FileAuthStorageBackend.withLock` (`auth-storage.ts:119`) and `withLockAsync` (`:164`) persist `auth.json` (the sole on-disk store for API keys and OAuth refresh tokens) with a raw `writeFileSync` that `O_TRUNC`-opens the destination in place. A SIGKILL or power loss between the truncate and the final flush leaves `auth.json` 0-byte or partial. The next launch's `reload()` calls `JSON.parse` on the partial string, which throws, sets `loadError`, and leaves every provider credential unloadable; `persistProviderChange()` then early-returns while `loadError` is set (`:287`), so the corrupt file is never overwritten and the user is locked out until they manually `/login`. OAuth refresh repeats the same write on every token expiry. With this patch both write sites use `replaceFileAtomicSync` (temp file + atomic rename, the same helper used by `session-file-repair.ts:409`), so a crash mid-write hits the temp file and `auth.json` keeps its prior valid contents.
- **Real environment tested**: macOS (darwin arm64), Node 23.x, OpenClaw checked out at base sha `9de6abd8d7` (raw write) and head sha `294819ced4` (atomic), `node_modules` present. Isolated temp `OPENCLAW_HOME`; `auth.json` lives under a temp dir and is passed as an explicit `authPath`, so production `~/.openclaw` is never touched. No external dependencies (api_key credential only; no OAuth/LLM/channel/network calls). This is not mock-only: the without-fix lockout was measured by running the real `auth-storage.ts` under `node` (tsx), and the with/without delta was measured by running the regression against the real production code path on both builds.
- **Exact steps or command run after this patch**:

```text
$ export OPENCLAW_HOME=$(mktemp -d)                     (isolated temp home, production ~/.openclaw untouched)
$ node_modules/.bin/tsx <probe.ts>                      (worktree root; imports src/agents/sessions/auth-storage.ts)
    control:        AuthStorage.set(api_key) then reload -> getApiKey  (must stay present)
    crash-truncate: full write, then model the O_TRUNC partial-write disk state, then
                    reload -> getApiKey; then set() -> reload -> getApiKey (self-heal attempt)
$ node_modules/.bin/vitest run src/agents/sessions/auth-storage.test.ts   (run on base file = RED, on head file = GREEN)
```

- **Evidence after fix**:

Live `node` (tsx) measurement of the without-fix credential lockout (pre-sol baseline):

```text
[without fix] base sha 9de6abd8d7, raw writeFileSync:
  control branch        lockout: false   (control healthy -> lockout is crash-specific, not a probe artifact)
  crash-truncate branch loadFailed:     true   (JSON.parse threw -> loadError)
  crash-truncate branch keyAfterCrash:  <empty>
  crash-truncate branch recoveryNoOp:   true   (persistProviderChange early-return on loadError)
  crash-truncate branch lockout:        true
```

Regression run on both builds (the authoritative with/without evidence; the crash is injected at the write call so it discriminates raw vs atomic):

```text
[without fix] base auth-storage.ts run under node (vitest): test FAILS
  getApiKey("anthropic") -> undefined   (auth.json truncated -> reload JSON.parse threw -> lockout)

[with fix] head auth-storage.ts run under node (vitest): test PASSES
  getApiKey("anthropic") -> "sk-test-SEED-12345"   (write hit the temp file; auth.json kept its prior contents)
```

- **Observed result after fix**: With the patch the crash no longer locks out the credential: the regression passes because `auth.json` retains the seed (`getApiKey` returns the seed key), whereas without the patch the same code path truncates `auth.json` and `getApiKey` returns `undefined` (full lockout). The control branch stays healthy in both builds, confirming the lockout is crash-specific rather than a measurement artifact.
- **What was not tested**: Real process-boundary SIGKILL injection during a live multi-second write (both the pre-sol `node` probe and the regression model the `O_TRUNC` partial-write disk state at the write call rather than killing a child mid-syscall). The automated post-sol scenario could not produce a with/without delta on its own: a `node`/tsx probe cannot route the named-import `writeFileSync` in `auth-storage.ts` through a single crash hook (ESM namespaces are frozen, `module.registerHooks` cannot source-replace the `node:fs` builtin, and a default-import monkeypatch does not rebind the named-import binding), so a tsx probe cannot automatically distinguish a raw direct-write from an atomic temp-write and both builds report the same modeled lockout. The regression test is therefore the authoritative with/without evidence, because `vitest`'s `vi.mock("node:fs")` routes both the named and default `writeFileSync` through one hook and so does distinguish raw (writes `auth.json`, hook fires, truncates) from atomic (writes a temp file, hook never fires). The OAuth refresh path (`withLockAsync`, `:164`) shares the identical write and the identical fix but is not separately exercised (would require a registered OAuth provider). Sibling writers in the original cluster (session transcript `.jsonl`, `settings.json`) are out of scope for this single fix.
- **Proof limitations or environment constraints**: Run on macOS with a modeled crash (`O_TRUNC` partial-write disk state) rather than a kernel-level SIGKILL during the write syscall; the modeled state is byte-equivalent to a process that died after truncate and before the full flush. Credentials in the proof are throwaway test keys in an isolated temp home.
