fix(logging): align diagnostic recovery in-flight dedup keys

> AI-assisted PR. Drafted and verified with Claude Code (claude-opus-4-8). Testing level: fully tested (RED/GREEN regression test in `src/logging/diagnostic.test.ts` plus a live two-build real-behavior probe; see Evidence and Real behavior proof). Author has reviewed and understands the change.

## Summary

- **Problem**: The diagnostic stuck-session recovery has two in-flight dedup layers that use different key granularities. The coordinator's `recoveryRequestKey` keys on `${ref}:${stateGeneration}`, while the runtime's `recoveryKey` keys on `ref` (sessionKey || sessionId) alone. When a session's generation is bumped during the in-flight abort/drain window, the coordinator treats the same session as a new request and emits a duplicate `session.recovery.requested` event, while the runtime collapses both to the same ref and skips the actual recovery.
- **Why it matters**: The duplicate `session.recovery.requested` event has no matching recovery work, which inflates recovery-attempt/latency metrics and breaks the `requested`/`completed` 1:1 invariant. The two dedup layers' in-flight views diverge, making diagnostic event streams unreliable.
- **What changed**: The coordinator's in-flight dedup key is aligned to ref-only, matching the runtime. A regression test was added under the idle-queued-stall branch.
- **What did NOT change**: No state-mutating behavior changes. Staleness validation still uses `stateGeneration` (in `applyRecoveryOutcomeToDiagnosticState` and the runtime stale check). The `session.recovery.requested` / `completed` event payloads still carry `stateGeneration` unchanged. The runtime's own dedup key is untouched.

## Change Type

- [x] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature
- [ ] Refactor required for the fix
- [ ] Breaking change

## Scope

- [x] `src/logging` (diagnostic stuck-session recovery coordinator)
- Touched areas: diagnostic session recovery in-flight deduplication.

## Linked Issue

(Issue to be filed before publish; will link `Closes #N`.)

## Root Cause

The stuck-session recovery path tracks in-flight recoveries in two independent `Set`s:

- Coordinator (`src/logging/diagnostic-session-recovery-coordinator.ts:63-69`): `recoveryRequestKey` returns `${ref}:${request.stateGeneration ?? "unknown"}`.
- Runtime (`src/logging/diagnostic-stuck-session-recovery.runtime.ts:55-57`): `recoveryKey` returns `ref` (sessionKey || sessionId) only.

The 30s diagnostic heartbeat re-classifies the same stuck session on every tick. While the first `recover()` is still in-flight (it does a dynamic import then awaits abort/drain, settleMs up to 15s), a queued message increments `state.generation` synchronously (e.g. `logMessageQueued`). On the next tick the coordinator key becomes `S:G+1`, which is not present in its `Set`, so it passes dedup, emits a second `session.recovery.requested` event, and re-enters `recover()`. The runtime, however, keys on the same ref `S`, finds it already in-flight, and returns `already_in_flight` (a non-mutating `skipped` outcome). The net effect is a phantom `requested` event with no matching recovery work.

The missing guardrail is consistency of dedup granularity: in-flight dedup must collapse concurrent recoveries for the same logical session, so both layers must key on the same identity (the session ref). Mixing `stateGeneration` into the coordinator key is the wrong granularity for an in-flight gate; generation belongs to staleness validation, not concurrency dedup.

## Regression Test Plan

- New test in `src/logging/diagnostic.test.ts` (describe block `stuck session diagnostics threshold`): `does not re-emit session.recovery.requested when generation bumps mid-flight (idle-queued stall)`.
- It reuses the existing idle-queued-stall harness (fake timers + a pending-Promise `recoverStuckSession` stub + `onDiagnosticEvent` collection + `markDiagnosticEmbeddedRunStarted` / `logMessageQueued` / `logSessionStateChange`), so no new test helpers are introduced.
- The test deliberately uses the idle-queued-stall setup (idle + queueDepth > 0 + embedded-run ownership + stale `lastProgressAgeMs`). The processing branch cannot reproduce this, because the generation mutators also reset `state.lastActivity`, which self-cancels the stall on the next tick. `logMessageQueued` bumps generation but does not refresh `lastProgressAgeMs` (it does not go through `touchSessionActivity`), so the idle-queued-stall classification holds while the coordinator key changes.
- RED (before fix): coordinator key `${ref}:${generation}` makes `S:G` and `S:G+1` distinct, so a second `session.recovery.requested` event fires and `requestedEvents.length === 2`.
- GREEN (after fix): coordinator key is ref-only, so `S:G+1` collides with the in-flight `S` and is absorbed as `already_in_flight`; `requestedEvents.length === 1`.
- Run locally: `node scripts/run-vitest.mjs run --config test/vitest/vitest.logging.config.ts src/logging/diagnostic.test.ts` (this config includes `src/logging/**/*.test.ts` and is part of the full `pnpm test` suite).

## Security Impact

- No new permissions, scopes, or capabilities introduced.
- No secrets read, written, or changed.
- No new network calls, no external service interaction.
- No change to data scope or persisted state: the change only affects in-memory diagnostic-event deduplication for stuck-session recovery. The fix collapses duplicate in-flight requests (strictly coarser/stronger dedup, never weaker), so it cannot widen any data or execution path.
- This is a reliability/observability bug, not a security vulnerability, so the normal bug-fix PR flow applies (no private advisory needed).

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, isolated OPENCLAW_HOME.
- **Steps**: Seed a session in an idle-queued-recoverable-stall state (embedded-run ownership, idle, stale `lastProgressAgeMs`). On tick T1, the coordinator adds the in-flight key and emits `session.recovery.requested` #1; hold `recover()` in-flight. Bump the session generation inside the in-flight window. On tick T2, the same session is re-classified as a stall.
- **Expected (correct behavior)**: exactly one `session.recovery.requested` event for the same logical session while a recovery is in flight.
- **Actual (before fix)**: two `session.recovery.requested` events (generations `[0, 1]`), but only one runtime recovery actually runs (the second is `already_in_flight` / `skipped`), leaving a phantom requested event.

## Evidence

Failing before, passing after, with the regression test described above.

- Before fix (RED): `requestedEvents.length === 2` (coordinator double-fires; runtime absorbs the second by ref).
- After fix (GREEN): `requestedEvents.length === 1` (both dedup layers agree on ref granularity).
- Targeted run: `node scripts/run-vitest.mjs run --config test/vitest/vitest.logging.config.ts src/logging/diagnostic.test.ts`.

A live two-build (base sha vs head sha) real-behavior probe corroborates this end to end; see the Real behavior proof section below.

## Human Verification

- Confirmed upstream/main (`9de6abd8d7`) still carries the asymmetric key: coordinator `recoveryRequestKey` returns `${ref}:${stateGeneration ?? "unknown"}` while runtime `recoveryKey` is ref-only, so the defect is live and not already fixed upstream.
- Confirmed the runtime layer already serializes the actual abort/drain work by ref-only key (`recoveriesInFlight`), so tightening the coordinator to ref-only cannot open a same-ref double abort/drain; ref-only is strictly stronger dedup than `ref:generation`.
- Verified the new test lands in the existing `stuck session diagnostics threshold` describe block and reuses the established harness.

## Review Conversations

(No bot comments yet; will resolve clawsweeper / Greptile findings on this PR before requesting merge.)

## Compatibility / Migration

- No migration. No public API, event payload, or schema change. The `session.recovery.requested` and `session.recovery.completed` event shapes are unchanged (they still include `stateGeneration`). Behavior change is limited to suppressing a duplicate in-flight `requested` event for the same logical session.

## Risks and Mitigations

- **Risk**: A legitimately new recovery for the same session could be coalesced into the in-flight one. **Mitigation**: This is the intended runtime semantics already (the runtime keys on ref-only and skips concurrent same-ref recoveries as `already_in_flight`). The coordinator change only brings the event layer into agreement with the work layer; it cannot drop a recovery the runtime would have actually performed.
- **Risk**: Staleness handling regresses. **Mitigation**: `stateGeneration` is still validated independently in `applyRecoveryOutcomeToDiagnosticState` and the runtime stale check; only the in-flight dedup granularity changed.
- **Risk regression scope**: Existing tests that assert same-generation dedup (`diagnostic.test.ts` `does not start duplicate recovery for the same processing generation`) and single-tick recovery tests remain green, because same-ref/same-generation requests still dedup under a ref-only key and single-tick scenarios are unaffected.

<!-- PASTE-VERBATIM-FROM-SOL-0019-post_sol_proof.pr_body_section -->
## Real behavior proof

- **Behavior or issue addressed**: Without this patch, the diagnostic stuck-session recovery dedup uses two Set keys at different granularities: the coordinator's recoveryRequestKey (diagnostic-session-recovery-coordinator.ts:63-69) is `${ref}:${stateGeneration}` while the runtime's recoveryKey (diagnostic-stuck-session-recovery.runtime.ts:55-57) is `ref` alone. When the same session's generation is bumped during the in-flight abort/drain window (logMessageQueued etc. increment generation synchronously), the coordinator dedup treats `S:G+1` as a brand-new key and fires a SECOND session.recovery.requested event, but the runtime collapses both to ref `S` and returns already_in_flight. The result is a duplicate requested event with no matching recovery work (skipped is non-mutating, so no state corruption; the impact is observability: inflated recovery-attempt metrics and a requested/completed mismatch). With this patch, the coordinator key is aligned to the same ref granularity as the runtime, so the bumped re-request dedups instead of emitting a phantom requested event.
- **Real environment tested**: macOS (darwin arm64), Node 23.x, isolated OPENCLAW_HOME. Both builds run the same probe against worktree src via tsx (no bundling). No external dependencies (no OAuth/LLM/channel calls) - the probe imports the production coordinator + runtime + session-state modules directly and drives requestStuckSessionRecovery / recoverStuckDiagnosticSession.
- **Exact steps or command run after this patch**:

```text
$ pnpm install --frozen-lockfile           (both worktrees)
$ node_modules/.bin/tsx <probe>.mts        (worktree-relative)
  probe: seed session S (generation=G, state=processing); count session.recovery.requested;
         T1 requestStuckSessionRecovery(req@G) -> recover() holds runtime in-flight;
         bump session generation G -> G+1 inside the in-flight window;
         T2 requestStuckSessionRecovery(req@G+1) + recoverStuckDiagnosticSession(req@G+1);
         read requested-event count, runtime dedup outcome, control run (no bump)
```

- **Evidence after fix**:

Live Node.js measurement of session.recovery.requested emission and runtime ref-dedup across the two dedup layers (defect axis = generation bump; control = no bump):

```text
[Build A] without this patch (base sha):
  with generation bump:
    coordinator session.recovery.requested count = 2 (generations seen = [0, 1])
    runtime T2 outcome = None/None
  control (no bump):
    coordinator session.recovery.requested count = 1
  -> asymmetry reproduced: True (coordinator double-fires on bump; runtime absorbs by ref)

[Build B] with this patch (head sha):
  with generation bump:
    coordinator session.recovery.requested count = 1
  control (no bump):
    coordinator session.recovery.requested count = 1
  -> coordinator key aligned to ref granularity: bumped re-request dedups (no phantom event)
```

- **Observed result after fix**: With the patch, the coordinator emits 1 session.recovery.requested event(s) after a generation bump (down from 2 without the patch), matching the runtime's ref-level dedup. The two in-flight tracking layers now agree on dedup granularity, eliminating the phantom requested event whose recovery the runtime had silently skipped via already_in_flight.
- **What was not tested**: P2 / observability-only impact: the skipped recovery outcome is non-mutating (recoveryOutcomeMutatesSessionState=false), so this is a metrics/event consistency defect, not data corruption - that bound is asserted, not independently measured here. The alternative race where T1 completes just before T2 (runtime key cleared) so the second recovery actually runs (potential double abort/drain) depends on embedded-run abort idempotency, which is out of this scope and not exercised by this probe.
