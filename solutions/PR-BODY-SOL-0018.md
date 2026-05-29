fix(infra): reconcile unacked session deliveries to prevent duplicate replay on restart

> AI-assisted PR. Drafted and implemented with Claude Code (claude-opus-4-8). Test scope: fully tested (regression test RED before / GREEN after on this branch, plus a live real-behavior proof comparing builds with and without the patch). The author has reviewed and understands the change.

## Summary

- **Problem**: The session-delivery recovery queue blind-replays an unacked agent-turn delivery after a crash. If a restart-continuation `agentTurn` delivery succeeds (the turn re-runs and the platform reply is sent) but the process dies before the entry is acked, the next recovery re-delivers the same entry unconditionally - re-running a non-idempotent turn and re-sending its reply.
- **Why it matters**: This is a reliability / crash-recovery correctness gap on a hot restart path. The harm is duplicate execution of a user turn (non-idempotent LLM/tool side-effects, which no message-level dedup can absorb) plus a duplicate platform reply.
- **What changed**: Port the outbound queue's existing reconciliation pattern to the session queue. Persist a `send_attempt_started` / `unknown_after_send` marker around the delivery seam, and on recovery refuse a blind replay of an entry that carries that marker (fail-safe: move to `failed/`), exactly like the outbound queue already does.
- **What did NOT change**: No adapter contract change, no channel adapter touched, no new config / permission / secret / network surface. `server-restart-sentinel.ts` is unchanged - the markers wrap the existing `deliver(entry)` seam in the recovery code, which is the production platform-send seam (`deliver` is `deliverQueuedSessionDelivery`). Normal (non-crash) recovery, retry budget, backoff, and startup-cutoff behavior are unchanged.

## Change Type

- [x] Bug fix (reliability / crash-recovery correctness)
- [ ] New feature
- [ ] Refactor required for the fix
- [ ] Docs / tests only

## Scope

- [x] `src/infra/` (session-delivery durable queue: storage + recovery)
- [ ] Channel adapters
- [ ] Gateway
- [ ] Cron
- [ ] Plugins
- [ ] Security-sensitive paths

Touched files (2 source + 1 test):
- `src/infra/session-delivery-queue-storage.ts`
- `src/infra/session-delivery-queue-recovery.ts`
- `src/infra/session-delivery-queue.recovery.test.ts`

## Linked Issue

Closes #88015

## Root Cause

`drainQueuedEntry` (`src/infra/session-delivery-queue-recovery.ts`) is an at-least-once drain: it runs `await deliver(entry)` then `await ackSessionDelivery(entry.id)`. The result is classified into only `recovered` / `failed`. There is no durable record of whether the platform send already happened, because `QueuedSessionDelivery` (`src/infra/session-delivery-queue-storage.ts`) had no `recoveryState` field.

So if the process is killed in the window after `deliver` succeeds (turn re-run + platform send via `dispatchAssembledChannelTurn` / `sendDurableMessageBatch`) but before `ackSessionDelivery` completes its atomic rename, the queue file stays pending with no marker. The next `recoverPendingSessionDeliveries` pass treats it as a fresh pending entry and re-delivers it.

The missing guardrail is the reconciliation marker. The parallel outbound queue already guards exactly this scenario: it persists `send_attempt_started` / `unknown_after_send` markers (`src/infra/outbound/deliver.ts`) and on recovery refuses a blind replay for those states when it cannot confirm via adapter reconciliation (`src/infra/outbound/delivery-queue-recovery.ts`, "refusing blind replay without adapter reconciliation"). The session queue replicated none of this, so the two queues' durability guarantees diverged.

## Regression Test Plan

Added one regression test to the existing recovery suite:
- `src/infra/session-delivery-queue.recovery.test.ts` -> `"does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash"` (profile: `infra`, `test/vitest/vitest.infra.config.ts`).

It models crash-before-ack by letting `deliver` succeed in PASS 1 while skipping the ack (the entry stays pending with the send marker - byte-identical on-disk state to a real crash before ack's atomic rename), then runs a second real `recoverPendingSessionDeliveries` restart pass (PASS 2) and asserts the same entry is not re-delivered.

- Without the fix: `deliverCount === 2` (blind replay) -> test FAILS.
- With the fix: `deliverCount === 1` (recovery sees the marker and refuses) -> test PASSES.

Run:
```text
pnpm test src/infra/session-delivery-queue.recovery.test.ts
# or: pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
```

The pre-existing recovery cases (success->ack, transient throw->requeue, retry budget, startup cutoff, backoff tier) all stay green: they never set `recoveryState`, so they never hit the new refuse branch. The storage round-trip tests (`session-delivery-queue.storage.test.ts`) also stay green - the new fields are optional and are not serialized when undefined.

## Security Impact

- No new permissions, scopes, or capabilities.
- No secrets read, written, or logged.
- No new network calls or external endpoints.
- No change to data exposure or message routing.
- Net effect on message-handling is a strengthening of delivery idempotency: a crash-orphaned, already-sent agent turn is no longer blind-replayed, so a user is less likely to receive a duplicate reply or have a non-idempotent turn re-execute. No security-sensitive file is touched; `.github/CODEOWNERS` places no `@openclaw/secops` constraint on `src/infra/session-delivery-queue-*`.

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 23.x, OpenClaw built from this branch; isolated `OPENCLAW_HOME` + explicit `OPENCLAW_STATE_DIR`.
- **Steps**:
  1. Enqueue an `agentTurn` session delivery.
  2. PASS 1: run recovery so `deliver` succeeds, but the ack does not complete (crash-before-ack).
  3. PASS 2: run `recoverPendingSessionDeliveries` again (restart).
  4. Count `deliver` invocations on the same entry.
- **Expected (correct)**: the entry is delivered once; the second recovery refuses to replay it.
- **Actual (before fix)**: the entry is delivered twice (blind replay) - the turn re-runs and the reply is re-sent.

## Evidence

Failing before, passing after, on this branch (profile `infra`):

```text
# BEFORE (source reverted to base, new test present):
pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
  x does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash
    AssertionError: expected 2 to be 1  (deliverCount: blind replay)
  Tests  1 failed | 5 passed (6)

# AFTER (this branch):
pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
  ok does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash
  Tests  6 passed (6)
```

Type checks clean on this branch:
```text
pnpm tsgo:core        -> exit 0
pnpm tsgo:core:test   -> exit 0
```

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, the session-delivery recovery queue blind-replays an unacked agent turn. drainQueuedEntry (session-delivery-queue-recovery.ts:105-124) runs `await deliver(entry)` then `ackSessionDelivery`. If the process is killed after deliver succeeds (the agent turn re-runs via dispatchAssembledChannelTurn and the platform reply is sent) but before ack, the queue file stays pending with no send-attempt marker - QueuedSessionDelivery has no recoveryState field. The next recovery re-delivers the same entry unconditionally, re-running the turn and re-sending the reply. The parallel outbound queue (outbound/delivery-queue-recovery.ts:370) persists send_attempt_started / unknown_after_send markers and reconciles actual send status before replay, refusing blind replay when it cannot confirm (:417); the session queue replicates none of this. With this patch, the session queue records an equivalent marker so recovery refuses blind replay of an already-sent turn.
- **Real environment tested**: macOS (darwin arm64), Node 23.x, OpenClaw built from this branch. Isolated OPENCLAW_HOME + explicit OPENCLAW_STATE_DIR via skills/real-behavior-proof harness env isolation. No external dependencies (no OAuth/LLM/channel calls) - the deliver callback only counts invocations. Production touched: none. The crash is modelled deterministically as deliver-success-then-ack-skip followed by a real recoverPendingSessionDeliveries restart pass (same production recovery code path under test); a real process SIGKILL/restart strengthens this in the post-sol run.
- **Exact steps or command run after this patch**:

```text
$ pnpm install --frozen-lockfile             (both worktrees)
$ node_modules/.bin/tsx <probe>.mts          (worktree-relative, isolated state dir)
  probe: enqueueSessionDelivery(agentTurn) ->
         PASS 1 deliver(entry) success, SKIP ack   (crash-before-ack model) ->
         PASS 2 recoverPendingSessionDeliveries()   (restart, real recovery path) ->
         count deliver() invocations on the same entry
```

- **Evidence after fix**:

Live Node.js measurement of deliver() invocation count for one unacked agentTurn entry across a crash-before-ack then restart-recovery sequence:

```text
[Build A] without this patch (base sha):
  pendingAfterEnqueue=1 pendingAfterCrash=1 recoveryStateAfterCrash=field-absent
  deliverCount=2  recovered=1 pendingAfterRecover=0
  -> same entry delivered twice (blind replay): turn re-run + reply re-sent.

[Build B] with this patch (head sha):
  pendingAfterEnqueue=1 pendingAfterCrash=1 recoveryStateAfterCrash=unknown_after_send
  deliverCount=1  recovered=0 pendingAfterRecover=0
  -> recovery sees the persisted marker and refuses blind replay: delivered once.
```

- **Observed result after fix**: With the patch, deliver() is invoked 1x for the unacked agentTurn (vs 2x without the patch). The persisted recovery marker lets the session queue refuse blind replay of an already-sent turn, matching the outbound queue's reconciliation guarantee.
- **What was not tested**: Real adapter-side reconcileUnknownSend round-trip (the post-sol fix scope; this proof measures replay suppression via the persisted marker). Real OS-level SIGKILL between deliver and ack (modelled here as ack-skip + restart pass; post-sol uses a real forked process restart). Platform-side message dedup inside dispatchAssembledChannelTurn (turn/kernel path out of scope).

## Human Verification

- Confirmed the production wiring: `recoverPendingSessionDeliveries` is invoked from `server-restart-sentinel.ts` with `deliver: (entry) => deliverQueuedSessionDelivery(...)`, so the markers in `drainQueuedEntry` wrap the real turn-re-run + platform-send seam.
- Confirmed the fix mirrors the outbound queue's accepted pattern (`send_attempt_started` / `unknown_after_send` + refuse-blind-replay), so the two queues now converge.
- Confirmed at-least-once is not broadly weakened: an in-process thrown failure calls `clearSessionDeliveryRecoveryState` so transient failures stay replayable; only a genuine crash (which never reaches the catch) leaves the marker durable.
- Ran the regression test RED (base source) then GREEN (this branch), plus `tsgo:core` and `tsgo:core:test` (both exit 0).

I have not exercised a real separate-process SIGKILL between deliver and ack (the crash is modelled as ack-skip + a real restart-recovery pass; the on-disk state is byte-identical). Calling that out explicitly under What was not tested.

## Review Conversations

No bot comments yet at submission time. Will address `@clawsweeper` / Greptile / reviewer comments as they arrive.

## Compatibility / Migration

- Backward compatible. `platformSendStartedAt` and `recoveryState` are optional fields on `QueuedSessionDelivery`; existing on-disk queue entries without them deserialize unchanged and take the normal (non-refuse) path.
- No migration step. Newly written entries only carry a marker transiently during a recovery attempt; it is cleared on in-process failure and removed with the entry on ack.
- No config, schema, or API surface change.

## Risks and Mitigations

- **Risk**: A narrow at-least-once weakening. If a crash happens after the `send_attempt_started` marker is persisted but before the platform send actually went out, that entry is failed (moved to `failed/`) rather than replayed, so a never-sent message could be dropped in that window.
  - **Mitigation / rationale**: This is the same fail-safe tradeoff the outbound queue already makes when it cannot reconcile. Avoiding a duplicate non-idempotent turn re-run and duplicate reply is prioritized over replaying in this narrow crash window. A follow-up can add adapter-side `reconcileUnknownSend` to recover full at-least-once for the confirmable case (out of scope here to keep this PR small and adapter-contract-free).
- **Risk**: Marker write adds extra small disk writes on the recovery path.
  - **Mitigation**: Recovery is a cold/infrequent path (process restart), not steady-state delivery; the writes mirror what the outbound queue already does.
- **Risk**: Scope creep into the gateway sentinel.
  - **Mitigation**: Avoided. Markers wrap the existing `deliver` seam in recovery, so `server-restart-sentinel.ts` is untouched; the change is confined to the two `infra` queue files plus the test.
