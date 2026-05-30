### Bug type

Behavior bug (incorrect output/state without crash)

### Beta release blocker

No

### Summary

`FileAuthStorageBackend` persists `auth.json` with a non-atomic `writeFileSync` (`O_TRUNC` in place), so a credential write that does not complete (full disk, storage quota, or power loss) leaves `auth.json` truncated; the next launch fails to parse it and silently locks out every provider with no automatic recovery.

### Steps to reproduce

Deterministic reproduction (a file-size limit produces the same partial-write on-disk state as a full disk / over-quota volume):

1. Seed a valid credential into an `auth.json` (e.g. `AuthStorage.create(authPath).set("anthropic", { type: "api_key", key: "<key>" })`), confirm it round-trips.
2. Run the process under a file-size limit smaller than the next write (e.g. `RLIMIT_FSIZE` = 2 MiB) and trigger any credential write through `set()` (a normal credential update or an OAuth token refresh; the payload only needs to exceed the limit). The write to `auth.json` is cut partway and fails with `EFBIG`, exactly as a full disk fails with `ENOSPC` / an over-quota volume with `EDQUOT`.
3. Restart and read any credential back, e.g. `AuthStorage.create(authPath).getApiKey("anthropic")`.

Real-world trigger (no rlimit needed): the disk fills up, the container hits its volume quota, or power is lost while `auth.json` is being rewritten on a credential change or OAuth refresh.

### Expected behavior

A credential write that is interrupted partway must not destroy the existing `auth.json`: the prior valid credentials should still load on the next launch. The same repository already does this for session transcripts via an atomic temp-file + rename (`src/agents/sessions/session-file-repair.ts:409`, `replaceFileAtomic`).

### Actual behavior

`auth.json` is left as a 0-byte or partial-JSON file on disk. `persistProviderChange()` (`src/agents/sessions/auth-storage.ts:286`) wraps the write in `try/catch` and only records the error without rethrowing, so the failed save returns as if it succeeded. On the next launch, `reload()` -> `JSON.parse` throws, `loadError` is set, every `getApiKey()` returns `undefined`, and `persistProviderChange()`'s `if (this.loadError) return;` guard (`:287`) prevents even a re-login from overwriting the corrupt file. The result is a silent, total credential lockout for all providers until the user manually deletes `auth.json` and re-runs `/login`. Measured: a credential store that is intact before the interrupted write returns `undefined` from `getApiKey` after it, in 20/20 trials.

### OpenClaw version

Reproduced against `main` at commit `9de6abd8d7`. `src/agents/sessions/auth-storage.ts` is unchanged on current `upstream/main` (`2644f26a35`), so current `main` is affected (the raw `writeFileSync` + `chmodSync` are still at `:119` and `:164`).

### Operating system

macOS (darwin arm64, Darwin 25.4.0), Node 23.x. The defect is OS-independent (it is in the credential-file write path); the on-disk partial-write state from `ENOSPC` / `EDQUOT` / power loss occurs on any platform.

### Install method

Source checkout, run via `tsx` (the production `src/agents/sessions/auth-storage.ts` is exercised by a real child `node` process).

### Model

N/A (model-independent; the defect is in the on-disk credential store writer, not the model/inference path).

### Provider / routing chain

Any provider. `auth.json` is the single on-disk store for all providers' API keys and OAuth refresh tokens, so a corrupt file makes every configured provider unauthenticated at once.

### Logs, screenshots, and evidence

```text
Real child-process measurement, isolated temp OPENCLAW_HOME, 20 trials per build.
The OS cuts the credential write at a 2 MiB file-size limit (the full-disk / quota / power-loss truncation),
then auth.json is read off disk: does it parse and still hold the seed key?

| build                | auth-storage.ts                | trials | lockout | write cut | on-disk auth.json after the cut write      |
| -------------------- | ------------------------------ | ------ | ------- | --------- | ------------------------------------------ |
| current (unpatched)  | raw writeFileSync(auth.json)   | 20     | 20/20   | 20/20     | 2,097,152 bytes, partial, JSON.parse fails |
| atomic temp + rename | replaceFileAtomicSync          | 20     | 0/20    | 20/20     | 88 bytes, intact, seed key present         |

[unpatched] raw writeFileSync(auth.json):
  on-disk auth.json: 2097152 bytes (cut at the limit), parseOk=false, seed key present=false
  reload() JSON.parse threw -> loadError -> getApiKey("anthropic") -> undefined  -> lockout in 20/20 trials
  (the write error was swallowed by persistProviderChange, so set() reported success)
```

Note on a non-trigger: an external `SIGKILL` during the write does NOT reproduce this on macOS/APFS, because a single buffered `writeFileSync` of a string is one `write()` syscall that the kernel completes before delivering the signal (verified: a 64 MB write SIGKILLed mid-flight still produced a complete, parseable file). The trigger is a write that the kernel itself cannot complete (full disk / quota / power loss), not process termination mid-write.

### Impact and severity

- Affected: any user whose disk fills, whose volume hits a quota, or who loses power while `auth.json` is being written (credential change or OAuth token refresh). All providers at once.
- Severity: high impact (silent, total credential lockout with no automatic recovery; manual delete + `/login` required), data-integrity of the sole credential store.
- Frequency: low / edge-case (requires an interrupted write), but unrecoverable when it happens.
- Consequence: the agent is fully unauthenticated on next launch and stays that way silently until the user notices and manually re-authenticates.

### Additional information

- Scope: only the `auth.json` credential writer is in question here. Sibling writers in the same area (session transcript `.jsonl`, `settings.json`) are out of scope for this report.
- Proposed fix direction (already the established in-tree pattern): route both write sites (`withLock` `:119`, `withLockAsync` `:164`) through `replaceFileAtomicSync` (write a sibling temp file with flag `wx`, then atomic rename), the same helper used by `session-file-repair.ts:409`, so an interrupted write lands on the throwaway temp and `auth.json` keeps its prior valid contents. A fix PR will reference this issue.
- AI-assisted: this report was prepared with AI assistance (Claude Code); the code references, the reproduction, and the measured 20/20 vs 0/20 result were verified by the author.
