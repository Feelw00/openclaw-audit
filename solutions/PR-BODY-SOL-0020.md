## Summary

- **Problem**: The session-delivery recovery queue blind-replays an unacked agent turn after a crash. `drainQueuedEntry` (`src/infra/session-delivery-queue-recovery.ts`) runs `await deliver(entry)` then `ackSessionDelivery`. If the agent turn re-runs (`dispatchAssembledChannelTurn`, which can send via the message tool) but the process dies before the ack, the entry stays pending and the next recovery re-delivers it.
- **Why it matters**: The same restart-continuation `agentTurn` runs twice (non-idempotent LLM/tool side effects) and its reply is sent twice. Restart continuations are, by definition, on the crash/restart path.
- **What changed**: Wire the shared SQLite `recovery_state` column (`#88665` already added it) into the session drain, at the turn-execution boundary. The deliver seam (`server-restart-sentinel.ts`) reports `onSendAttemptStart` right before the agent turn runs, and `onSendDeferred` if that attempt is merely deferred because the session is busy. `drainQueuedEntry` persists the marker on `onSendAttemptStart` and refuses a recovered marked entry (fail-safe move to `failed/`). On a thrown deliver, a turn that began running is refused even if it threw after sending; a pre-send / busy-deferred / no-op failure stays retryable.
- **What did NOT change**: No adapter contract, no channel adapter, no new config / permission / secret / network surface. The `recovery_state` column itself already exists (`#88665`). Genuine pre-send failures still retry (at-least-once preserved); the no-op deliver paths (`systemEvent` / changed-session / no-route) never mark and stay retryable.

## Change Type

Bug fix (reliability / crash recovery).

## Scope

`src/infra/` (session-delivery queue recovery + storage) and `src/gateway/server-restart-sentinel.ts` (the deliver seam reports the send boundary). No owner-gated paths touched.

## Linked Issue

Closes #88015

## Root Cause

The session-delivery queue is at-least-once: `drainQueuedEntry` delivers then acks. There was no durable marker recording that a delivery's turn had begun, so recovery could not distinguish "never ran" from "ran but not yet acked." The parallel outbound queue already guards exactly this with a `recovery_state` marker and refuses a blind replay on recovery without adapter reconciliation. `#88665` moved both queues onto a shared SQLite backing carrying a `recovery_state` column, but the session-delivery drain neither set nor read it.

## Regression Test Plan

Four tests in `src/infra/session-delivery-queue.recovery.test.ts` (profile: infra), RED before / GREEN after:

1. `does not re-deliver a session entry whose delivery succeeded but ack was interrupted by a crash` — the turn runs (signals `onSendAttemptStart`); the ack throws once; a second recovery pass must not replay it.
2. `refuses to replay an entry recovered while still carrying the send marker` — durable `send_attempt_started` on a pending entry (the cross-restart scenario); recovery refuses (no `deliver`, moved to `failed/`).
3. `refuses to replay a turn that sent before throwing (sent-before-error)` — the turn signals `onSendAttemptStart` then throws; recovery must not treat it as pre-send and replay it.
4. `retries a pre-send failure that never started the turn` — `deliver` throws before `onSendAttemptStart`; the entry stays pending and retryable with no marker (at-least-once preserved).

These exercise the SQLite-backed queue and require Node 24+ (`node:sqlite` `StatementSync.columns`), matching the repo CI test lane.

## Security Impact

None. No new permission, secret, or network call; no change to data scope or the adapter surface. The only behavioral change is fail-safe refusal of a blind replay on recovery.

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 24.x, OpenClaw built from this branch; isolated `OPENCLAW_HOME` / `OPENCLAW_STATE_DIR`.
- **Steps**: enqueue an `agentTurn` session delivery, let the turn run (mark) without acking (crash-before-ack), then run `recoverPendingSessionDeliveries` again.
- **Expected (fixed)**: delivered once; the recovered marker forces a fail-safe move to `failed/`.
- **Actual (before fix)**: delivered twice (turn re-run + reply re-sent).

## Evidence

Regression tests RED before / GREEN after (see Regression Test Plan). Local gates on this branch: `pnpm build` exit 0, typecheck clean, `pnpm check` exit 0; target tests `session-delivery-queue.recovery` + `.storage` + `server-restart-sentinel` pass on Node 24. The Real behavior proof below measures `deliver()` invocation count across a crash-before-ack + restart sequence on the SQLite-backed queue.

## Human Verification

Compared the rewritten recovery flow against the outbound-queue sibling (`src/infra/outbound/delivery-queue-recovery.ts`) to confirm the marker/refuse contract matches; confirmed the busy continuation path (which produces a busy-retry response, no durable work) clears the marker so it stays retryable; traced the production caller (`recoverPendingSessionDeliveries` ← `server-restart-sentinel.ts`, `deliverQueuedSessionDelivery` → `dispatchAssembledChannelTurn`) to place the `onSendAttemptStart` boundary at the actual turn execution.

## Review Conversations

New PR — earlier internal review flagged that clearing the marker on every thrown deliver would let a sent-before-error turn replay; this revision moves the marker to the turn-execution boundary so a turn that began running is refused while genuine pre-send failures stay retryable.

## Compatibility / Migration

Backward compatible, no migration. The `recovery_state` column already exists in the shared delivery-queue schema (`#88665`); pre-change entries read back with no marker and follow the normal path. A single marker state (`send_attempt_started`) is used.

## Risks and Mitigations

- **Fail-safe over the crash window (intentional tradeoff)**: a crash in the narrow window after the turn starts running but before the ack moves the entry to `failed/` rather than replaying it. This favors no-duplicate over at-least-once for that window — the same product choice the outbound queue makes when it has no reconcile capability. Pre-send failures, busy-deferred attempts, and no-op deliver paths are unaffected (they stay retryable). **Mitigation / follow-up**: a precise adapter-side `reconcileUnknownSend` (as the outbound queue has) would let recovery confirm actual send status and replay safely; that is a larger, multi-adapter change kept out of this one-thing-per-PR fix.

## Real behavior proof

- **Behavior or issue addressed**: Without this patch, the session-delivery recovery queue blind-replays an unacked agent turn after a crash. drainQueuedEntry (session-delivery-queue-recovery.ts) runs `await deliver(entry)` then `ackSessionDelivery`. If the process is killed after deliver succeeds (the agent turn re-runs via dispatchAssembledChannelTurn and the platform reply is sent) but before ack, the entry stays pending. The next recovery re-delivers it unconditionally, re-running the turn and re-sending the reply. #88665 moved the delivery queues onto a shared SQLite backing (delivery-queue-sqlite.ts) that already carries a recovery_state column, but the session-delivery drain does not set or read it; the parallel outbound queue (outbound/delivery-queue-recovery.ts) does, reconciling actual send status before replay and refusing blind replay when it cannot confirm. With this patch the session drain wires the existing recovery_state column so it refuses blind replay of an already-sent turn, matching the outbound queue.
- **Real environment tested**: macOS (darwin arm64), Node 24.x (node:sqlite StatementSync.columns), OpenClaw built from this branch. Isolated OPENCLAW_HOME + explicit OPENCLAW_STATE_DIR via the real-behavior-proof harness env isolation; the session-delivery queue now lives in the shared state SQLite DB. No external dependencies (no OAuth/LLM/channel calls) - the deliver callback only counts invocations. Production touched: none. The crash is modelled deterministically as deliver-success-then-ack-skip followed by a real recoverPendingSessionDeliveries restart pass (same production recovery code path); a real process SIGKILL/restart strengthens this in the post-sol run.
- **Exact steps or command run after this patch**:

```text
$ node_modules/.bin/tsx <probe>.mts          (worktree-relative, isolated state dir, Node 24)
  probe: enqueueSessionDelivery(agentTurn) ->
         PASS 1 deliver(entry) success, SKIP ack   (crash-before-ack model) ->
         PASS 2 recoverPendingSessionDeliveries()   (restart, real recovery path) ->
         count deliver() invocations on the same entry
```

- **Evidence after fix**:

Live Node.js measurement of deliver() invocation count for one unacked agentTurn entry across a crash-before-ack then restart-recovery sequence (SQLite-backed session queue):

```text
[Build A] without this patch (base sha):
  pendingAfterEnqueue=1 pendingAfterCrash=1 recoveryStateAfterCrash=field-absent
  deliverCount=2  recovered=1 pendingAfterRecover=0
  -> same entry delivered twice (blind replay): turn re-run + reply re-sent.

[Build B] with this patch (head sha):
  pendingAfterEnqueue=1 pendingAfterCrash=1 recoveryStateAfterCrash=send_attempt_started
  deliverCount=1  recovered=0 pendingAfterRecover=0
  -> recovery sees the persisted recovery_state and refuses blind replay: delivered once.
```

- **Observed result after fix**: With the patch, deliver() is invoked 1x for the unacked agentTurn (vs 2x without the patch). Wiring the existing shared recovery_state column into the session drain lets it refuse blind replay of an already-sent turn, matching the outbound queue's reconciliation guarantee.
- **What was not tested**: Real adapter-side reconcile round-trip (post-sol fix scope; this proof measures replay suppression via the persisted recovery_state). Real OS-level SIGKILL between deliver and ack (modelled here as ack-skip + restart pass; post-sol uses a real forked process restart). Platform-side message dedup inside dispatchAssembledChannelTurn (turn/kernel path out of scope).

---

AI-assisted: drafted with Claude Code (claude-opus-4-8); fully tested (build + typecheck + Node 24 target tests + real-behavior-proof). Author reviewed and understands the change.
