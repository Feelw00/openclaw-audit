<!--
FILE THIS AS A BUG ISSUE (openclaw/openclaw), then put its number in PR-BODY-SOL-0019.md "Closes #N".
  title:  [Bug]: diagnostic stuck-session recovery emits a phantom `session.recovery.requested` event when generation bumps mid-flight
  labels: bug
gh issue create --repo openclaw/openclaw --title "<title above>" --label bug --body-file solutions/ISSUE-BODY-SOL-0019.md
-->

**Bug type**: Behavior bug (incorrect output/state without crash)

**Beta release blocker**: No

## Summary

The diagnostic stuck-session recovery uses two in-flight dedup layers with different key granularities: the coordinator keys on `${ref}:${stateGeneration}`, the runtime keys on `ref` alone. When a session's generation is bumped during the in-flight abort/drain window, the coordinator treats it as a new request and emits a duplicate `session.recovery.requested` event, while the runtime collapses both to the same ref and skips the actual recovery, leaving a phantom requested event with no matching work.

## Steps to reproduce

Deterministic, no external services (probe imports the production coordinator + runtime + session-state modules directly):

1. Seed a session `S` in an idle-queued-recoverable-stall state (embedded-run ownership, idle, stale `lastProgressAgeMs`), generation `G`.
2. Tick T1: `requestStuckSessionRecovery(req@G)`. The coordinator adds its in-flight key `S:G` and emits `session.recovery.requested` #1; hold `recover()` in-flight (it does a dynamic import then awaits abort/drain, settleMs up to 15s).
3. Bump the session generation `G -> G+1` inside the in-flight window via a queued message (`logMessageQueued` increments `state.generation` synchronously).
4. Tick T2: `requestStuckSessionRecovery(req@G+1)` + `recoverStuckDiagnosticSession(req@G+1)`.
5. Count `session.recovery.requested` events and read the runtime dedup outcome. (Use the idle-queued-stall branch: `logMessageQueued` bumps generation but does not refresh `lastProgressAgeMs`, so the stall classification holds; the processing branch self-cancels because its mutators reset `lastActivity`.)

## Expected behavior

Exactly one `session.recovery.requested` event for the same logical session while a recovery is in flight (the event layer agrees with the work layer's ref-level dedup).

## Actual behavior

Two `session.recovery.requested` events fire (generations `[0, 1]`), but only one runtime recovery runs; the second returns `already_in_flight` (a non-mutating `skipped` outcome). The coordinator key `S:G` and `S:G+1` are distinct so dedup is bypassed, while the runtime keys on ref `S` and absorbs the second. The result is a phantom `requested` event with no matching recovery work.

A live two-build probe measured `requested count = 2 (generations [0,1])` with a generation bump (without fix) vs `1` (with fix); the no-bump control is `1` on both builds.

## OpenClaw version

main branch, built from source at upstream/main `9de6abd8d7`. Defect lines quoted below; the asymmetric keys are confirmed still present on this commit. Run under `tsx`.

## Operating system

macOS (darwin arm64). OS-independent (in-memory dedup-set behavior).

## Install method

Built from source (`pnpm`), `tsx`.

## Model

Not applicable. The defect is in the diagnostic recovery in-flight dedup; the probe drives the coordinator/runtime directly and invokes no model.

## Provider / routing chain

Not applicable (no provider, gateway, or network call is on the defect path).

## Logs, screenshots, and evidence

```text
src/logging/diagnostic-session-recovery-coordinator.ts:63-69   recoveryRequestKey = `${ref}:${request.stateGeneration ?? "unknown"}`
src/logging/diagnostic-stuck-session-recovery.runtime.ts:55-57 recoveryKey        = ref (sessionKey || sessionId) only
  -> 30s heartbeat re-classifies the stall each tick; a mid-flight generation bump makes the coordinator key change while the runtime key stays the same

Regression repro:
  RED (before fix): requestedEvents.length === 2  (coordinator double-fires; runtime absorbs the 2nd by ref)
  GREEN (after fix): requestedEvents.length === 1

Live two-build probe (isolated OPENCLAW_HOME, no network):
 without fix (with bump): requested count = 2 (generations [0,1]); runtime T2 outcome = None;  control (no bump) = 1
 with fix    (with bump): requested count = 1;                                                control (no bump) = 1
```

## Impact and severity

- Affected: any deployment running the diagnostic stuck-session recovery where a session's generation is bumped (e.g. a queued message) while a recovery is in flight.
- Severity: P2, observability / reliability (not data corruption: the skipped recovery outcome is non-mutating).
- Frequency: edge case (requires a generation bump inside the in-flight abort/drain window), but deterministic in that window.
- Consequence: inflated recovery-attempt / latency metrics and a broken `session.recovery.requested` / `completed` 1:1 invariant, making diagnostic event streams unreliable.

## Additional information

A fix is being prepared (align the coordinator's in-flight dedup key to ref-only, matching the runtime; `stateGeneration` stays in staleness validation and in the event payloads, only the in-flight dedup granularity changes). `src/logging/` is not under any `@openclaw/secops` CODEOWNERS constraint. AI-assisted analysis (Claude Code), grounded in the quoted source, the RED/GREEN regression test, and the live before/after probe.
