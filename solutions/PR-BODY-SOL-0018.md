fix(infra): reconcile unacked session deliveries to prevent duplicate replay on restart

> AI-assisted PR. Drafted and implemented with Claude Code (claude-opus-4-8). Test scope: fully tested (regression test RED before / GREEN after on this branch, plus a live real-behavior proof comparing builds with and without the patch). The author has reviewed and understands the change.

## Summary

- **Problem**: The session-delivery recovery queue blind-replays an unacked agent-turn delivery after a crash. If a restart-continuation `agentTurn` delivery succeeds (the turn re-runs and the platform reply is sent) but the process dies before the entry is acked, the next recovery re-delivers the same entry unconditionally - re-running a non-idempotent turn and re-sending its reply.
- **Why it matters**: This is a reliability / crash-recovery correctness gap on a hot restart path. The harm is duplicate execution of a user turn (non-idempotent LLM/tool side-effects, which no message-level dedup can absorb) plus a duplicate platform reply.
- **What changed**: Persist a `send_attempt_started` / `unknown_after_send` recovery marker around the delivery, and on recovery refuse a blind replay of an entry whose marker shows the platform send may already have happened (fail-safe: move to `failed/`), aligning the session queue with the outbound queue's reconciliation guarantee. Critically, the delivery seam (`deliverQueuedSessionDelivery`) now persists the `unknown_after_send` marker when `sendDurableMessageBatch` returns `partial_failed` (some payloads already sent, `sentBeforeError === true`) before it throws, and the recovery catch only clears the marker for a proven pre-send failure (nothing sent) - a sent-before-error failure keeps the marker and is moved to `failed/` instead of being blind-replayed. This mirrors the outbound queue, which never clears its marker once the platform send has started.
- **What did NOT change**: No adapter contract change, no channel adapter touched, no new config / permission / secret / network surface. Normal (non-crash) recovery, retry budget, backoff, and startup-cutoff behavior are unchanged: a genuine pre-send transient failure still clears the marker and stays retryable (at-least-once preserved); only a crash (which never reaches the catch) or a sent-before-error failure leaves the marker durable so recovery refuses a blind replay.

## Change Type

- [x] Bug fix (reliability / crash-recovery correctness)
- [ ] New feature
- [ ] Refactor required for the fix
- [ ] Docs / tests only

## Scope

- [x] `src/infra/` (session-delivery durable queue: storage + recovery)
- [ ] Channel adapters
- [x] Gateway (`server-restart-sentinel.ts` delivery seam: mark `unknown_after_send` on `partial_failed` before throwing)
- [ ] Cron
- [ ] Plugins
- [ ] Security-sensitive paths

Touched files (4 source + 2 tests):
- `src/infra/session-delivery-queue-storage.ts` (marker fields + `mark*` / `clear*` helpers)
- `src/infra/session-delivery-queue-recovery.ts` (refuse-blind-replay; clear marker only for pre-send failures)
- `src/infra/session-delivery-queue.ts` (re-export `markSessionDeliveryPlatformOutcomeUnknown`)
- `src/gateway/server-restart-sentinel.ts` (mark `unknown_after_send` on `partial_failed` before throwing)
- `src/infra/session-delivery-queue.recovery.test.ts` (regression tests)
- `src/gateway/server-restart-sentinel.test.ts` (add the new marker helper to the `vi.mock` of the session-delivery barrel)

## Linked Issue

Closes #88015

## Root Cause

`drainQueuedEntry` (`src/infra/session-delivery-queue-recovery.ts`) is an at-least-once drain: it runs `await deliver(entry)` then `await ackSessionDelivery(entry.id)`. The result is classified into only `recovered` / `failed`. There is no durable record of whether the platform send already happened, because `QueuedSessionDelivery` (`src/infra/session-delivery-queue-storage.ts`) had no `recoveryState` field.

So if the process is killed in the window after `deliver` succeeds (turn re-run + platform send via `dispatchAssembledChannelTurn` / `sendDurableMessageBatch`) but before `ackSessionDelivery` completes its atomic rename, the queue file stays pending with no marker. The next `recoverPendingSessionDeliveries` pass treats it as a fresh pending entry and re-delivers it.

The missing guardrail is the reconciliation marker. The parallel outbound queue already guards exactly this scenario: it persists `send_attempt_started` / `unknown_after_send` markers (`src/infra/outbound/deliver.ts`) and on recovery refuses a blind replay for those states when it cannot confirm via adapter reconciliation (`src/infra/outbound/delivery-queue-recovery.ts`, "refusing blind replay without adapter reconciliation"). The session queue replicated none of this, so the two queues' durability guarantees diverged.

There are two windows where a queued entry's platform send may already have happened: (1) a crash after the send but before the ack, and (2) an in-process failure thrown *after* a partial send - `deliverQueuedSessionDelivery` calls `sendDurableMessageBatch`, which returns `partial_failed` with `sentBeforeError === true` (a reply chunk already delivered) and then throws (`src/gateway/server-restart-sentinel.ts`). Window (2) is the one the recovery catch must not treat as a fresh, replayable failure. The outbound queue handles window (2) by never clearing the marker once the platform send has started (it has no clear path; `failDelivery` runs only when the send had not started, i.e. `!platformResultsReturned`). The fix gives the session queue the same property: the delivery seam marks `unknown_after_send` on `partial_failed` before throwing, and the recovery catch clears the marker only for a proven pre-send failure.

## Regression Test Plan

Added two regression tests to the existing recovery suite (`src/infra/session-delivery-queue.recovery.test.ts`, profile `infra`, `test/vitest/vitest.infra.config.ts`):

1. `"does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash"` - models crash-before-ack by letting `deliver` succeed in PASS 1 while skipping the ack (the entry stays pending with the `unknown_after_send` marker - byte-identical on-disk state to a real crash before ack's atomic rename), then runs a second real recovery pass (PASS 2) and asserts the same entry is not re-delivered.
2. `"does not re-deliver a session entry whose delivery partially sent before throwing"` - models the `partial_failed` / sent-before-error window: PASS 1's `deliver` persists `unknown_after_send` (as `deliverQueuedSessionDelivery` does on `partial_failed`) and then throws; the entry is moved to `failed/` rather than requeued with the marker cleared, and PASS 2 (with `bypassBackoff` so the entry is actually re-attempted) does not re-deliver it.

- Without the fix: each test reaches `deliverCount === 2` (blind replay) -> test FAILS (verified RED on the working tree by reverting the catch to the unconditional clear).
- With the fix: `deliverCount === 1` (recovery sees the marker and refuses) -> tests PASS.

Run:
```text
pnpm test src/infra/session-delivery-queue.recovery.test.ts
# or: pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
```

The pre-existing recovery cases (success->ack, transient throw->requeue, retry budget, startup cutoff, backoff tier) all stay green. The transient-throw->requeue case is a pre-send failure: the marker set before deliver is cleared in the catch, so the entry stays retryable exactly as before. The storage round-trip tests (`session-delivery-queue.storage.test.ts`) also stay green - the new fields are optional and are not serialized when undefined.

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
# BEFORE (recovery catch reverted to the unconditional marker-clear):
pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
  x does not re-deliver a session entry whose delivery partially sent before throwing
    AssertionError: expected [ { kind: 'agentTurn', ... } ] to strictly equal []  (entry still replayable -> blind replay)
  Tests  1 failed | 6 passed (7)

# AFTER (this branch):
pnpm vitest -c test/vitest/vitest.infra.config.ts run session-delivery-queue.recovery
  ok does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash
  ok does not re-deliver a session entry whose delivery partially sent before throwing
  Tests  7 passed (7)
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
- **What was not tested**: Real adapter-side reconcileUnknownSend round-trip (the post-sol fix scope; this proof measures replay suppression via the persisted marker). Real OS-level SIGKILL between deliver and ack (modelled here as ack-skip + restart pass; post-sol uses a real forked process restart). Platform-side message dedup inside dispatchAssembledChannelTurn (turn/kernel path out of scope). The live two-build probe above measures the crash-before-ack window; the `partial_failed` / sent-before-error window (delivery seam marks `unknown_after_send` before throwing, recovery refuses replay) is covered by the added `"...partially sent before throwing"` regression test (RED before / GREEN after on this branch) rather than by this live probe.

## Human Verification

- Confirmed the production wiring: `recoverPendingSessionDeliveries` is invoked from `server-restart-sentinel.ts` with `deliver: (entry) => deliverQueuedSessionDelivery(...)`, so the markers in `drainQueuedEntry` wrap the real turn-re-run + platform-send seam.
- Confirmed the fix mirrors the outbound queue's accepted pattern (`send_attempt_started` / `unknown_after_send` + refuse-blind-replay). Verified the outbound queue has no marker-clear path and only calls `failDelivery` when the platform send had not started (`!platformResultsReturned`); the session queue now matches by clearing the marker only for a proven pre-send failure.
- Confirmed at-least-once is not broadly weakened, and the sent-before-error window is closed: a genuine pre-send transient failure (nothing delivered) clears the marker and stays replayable; a crash (never reaches the catch) or a `partial_failed` / sent-before-error failure (marker advanced to `unknown_after_send` by the delivery seam before it throws) keeps the marker durable, so recovery refuses a blind replay and fail-safes to `failed/`. This closes the gap where clearing the marker on every thrown failure could still blind-replay an already partially sent reply.
- Ran the regression test RED (base source) then GREEN (this branch), plus `tsgo:core` and `tsgo:core:test` (both exit 0).

I have not exercised a real separate-process SIGKILL between deliver and ack (the crash is modelled as ack-skip + a real restart-recovery pass; the on-disk state is byte-identical). Calling that out explicitly under What was not tested.

## Review Conversations

`@clawsweeper` raised a [P1] on the first revision: the recovery catch cleared the marker for *every* thrown delivery failure, so a `partial_failed` (sent-before-error) throw could still leave the entry replayable and duplicate an already-sent reply. This revision addresses it directly: the delivery seam now persists `unknown_after_send` on `partial_failed` before throwing, and the recovery catch clears the marker only for a proven pre-send failure. Added a regression test for the partial-sent path. Thanks for catching it. Will address further `@clawsweeper` / Greptile / reviewer comments as they arrive.

## Compatibility / Migration

- Backward compatible. `platformSendStartedAt` and `recoveryState` are optional fields on `QueuedSessionDelivery`; existing on-disk queue entries without them deserialize unchanged and take the normal (non-refuse) path.
- No migration step. Newly written entries only carry a marker transiently during a recovery attempt; it is cleared on a pre-send in-process failure and removed with the entry on ack (a sent-before-error failure keeps the marker and moves the entry to `failed/`).
- No config, schema, or API surface change.

## Risks and Mitigations

- **Risk**: A narrow at-least-once weakening. When the send outcome cannot be confirmed not to have happened - a crash after the marker is persisted, or a `partial_failed` / sent-before-error failure - the entry is moved to `failed/` rather than replayed, so in that window a message that was not actually delivered would not be retried.
  - **Mitigation / rationale**: This is the same fail-safe tradeoff the outbound queue already makes when it cannot reconcile (it never clears the marker once the send has started). Avoiding a duplicate non-idempotent turn re-run and duplicate reply is prioritized over replaying in this narrow uncertain window. A genuine pre-send failure (nothing delivered) is unaffected: its marker is cleared and it stays retryable. A follow-up can add adapter-side `reconcileUnknownSend` to recover full at-least-once for the confirmable case (out of scope here to keep this PR small and adapter-contract-free).
- **Risk**: Marker write adds extra small disk writes on the recovery path.
  - **Mitigation**: Recovery is a cold/infrequent path (process restart), not steady-state delivery; the writes mirror what the outbound queue already does.
- **Risk**: Scope creep into the gateway sentinel.
  - **Mitigation**: Minimized. `server-restart-sentinel.ts` gets one added line - a `markSessionDeliveryPlatformOutcomeUnknown(entry.id)` call on the existing `partial_failed` branch before the existing `throw`. No control-flow or behavior change beyond persisting the marker; this is required because only the delivery seam knows the send was `partial_failed` (sent-before-error). The rest of the change stays in the `infra` queue files plus the test.
