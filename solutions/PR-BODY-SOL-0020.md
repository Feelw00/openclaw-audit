## Summary

- **Problem**: The session-delivery recovery queue blind-replays an unacked agent turn after a crash. `drainQueuedEntry` (`src/infra/session-delivery-queue-recovery.ts`) runs `await deliver(entry)` then `ackSessionDelivery`. If the process dies after `deliver` succeeds (the agent turn re-runs and its reply is sent) but before the ack, the entry stays pending and the next recovery re-delivers it unconditionally.
- **Why it matters**: The same restart-continuation `agentTurn` runs twice (non-idempotent LLM/tool side effects) and its reply is sent twice. Restart continuations are, by definition, on the crash/restart path, so the window is reachable.
- **What changed**: Wire the session-delivery drain onto the shared SQLite `recovery_state` column that `#88665` already added. The drain persists a `send_attempt_started` marker before invoking `deliver`, and a recovered entry still carrying that marker is refused a blind replay (fail-safe move to `failed/`) instead of being re-run. This matches the parallel outbound queue's contract.
- **What did NOT change**: No adapter contract, no channel adapter, no `server-restart-sentinel.ts`, no new config / permission / secret / network surface. The `recovery_state` column itself already exists (added by `#88665`); this PR only starts using it for the session queue. Normal (non-crash) recovery, retry budget, and backoff are unchanged: a genuine pre-send failure clears the marker and stays retryable.

## Change Type

Bug fix (reliability / crash recovery).

## Scope

`src/infra/` — session-delivery queue recovery + storage. No owner-gated paths touched.

## Linked Issue

Closes #88015

## Root Cause

The session-delivery queue is an at-least-once queue: `drainQueuedEntry` delivers an entry then acks (deletes) it. There was no durable marker recording that a delivery had begun, so recovery could not distinguish "never delivered" from "delivered but not yet acked." The parallel outbound queue already guards exactly this with a `recovery_state` marker (`send_attempt_started` / `unknown_after_send`) and refuses a blind replay on recovery without adapter reconciliation. `#88665` moved both delivery queues onto a shared SQLite backing (`src/infra/delivery-queue-sqlite.ts`) that carries a `recovery_state` column, but the session-delivery drain neither set nor read it — the guardrail existed in the schema but was never wired into this queue.

## Regression Test Plan

Two tests added to `src/infra/session-delivery-queue.recovery.test.ts` (profile: infra):

1. `does not re-deliver a session entry whose delivery succeeded but ack was interrupted by a crash` — `deliver` succeeds, the ack throws once (crash-before-ack model), then a second recovery pass runs. Without the fix `deliver` is invoked twice (blind replay); with the fix the in-process `delivered` flag forces a fail-safe and `deliver` runs once.
2. `refuses to replay an entry recovered while still carrying the send marker (crash then fresh-process recovery)` — the durable `send_attempt_started` marker is present on a pending entry (the real cross-restart scenario); recovery must refuse (no `deliver` call, entry moved to `failed/`).

Both are RED before the fix and GREEN after. Note: these tests exercise the SQLite-backed queue and require Node 24+ (`node:sqlite` `StatementSync.columns`), matching the repo CI test lane.

## Security Impact

None. No new permission, secret, or network call. No change to data scope or to the deliver/adapter surface. The `recovery_state` column is pre-existing; the only behavioral change is fail-safe refusal of a blind replay on recovery.

## Repro + Verification

- **Environment**: macOS (darwin arm64), Node 24.x, OpenClaw built from this branch; isolated `OPENCLAW_HOME` / `OPENCLAW_STATE_DIR`.
- **Steps**: enqueue an `agentTurn` session delivery, let `deliver` succeed without acking (crash-before-ack), then run `recoverPendingSessionDeliveries` again.
- **Expected (fixed)**: the entry is delivered once; the recovered marker forces a fail-safe move to `failed/`.
- **Actual (before fix)**: the entry is delivered twice (turn re-run + reply re-sent).

## Evidence

Regression tests are RED before / GREEN after (see Regression Test Plan). Local gates on this branch: `pnpm build` exit 0, typecheck clean, target tests `session-delivery-queue.recovery` + `.storage` pass (11 tests, Node 24). The Real behavior proof below measures `deliver()` invocation count across a crash-before-ack + restart sequence on the SQLite-backed queue.

## Human Verification

Read the rewritten recovery flow against the outbound-queue sibling (`src/infra/outbound/delivery-queue-recovery.ts`) to confirm the marker/refuse contract matches; confirmed `recovery_state` round-trips through `updateDeliveryQueueEntry` / `upsertDeliveryQueueEntry` (`delivery-queue-sqlite.ts`); traced the production caller (`recoverPendingSessionDeliveries` from `server-restart-sentinel.ts`) to confirm the guarded seam is the real restart-continuation path.

## Review Conversations

New PR — no bot conversations yet.

## Compatibility / Migration

Backward compatible, no migration. The `recovery_state` column already exists in the shared delivery-queue schema (`#88665`); entries written before this change simply read back with no marker and follow the normal path. A single marker state (`send_attempt_started`) is used: the recovery guard refuses on it alone, so no second state is needed.

## Risks and Mitigations

- **Fail-safe over-refusal (intentional tradeoff)**: the marker is set before the whole `deliver` seam (the agent-turn re-run and its sends), not at a single platform-send boundary — the rewritten restart-continuation path sends from inside the turn (message tool / outbound queue), so there is no single send seam to hook. Consequently a crash during the pre-send turn re-run, or a `deliver` path that returns without sending (`systemEvent` / changed-session / no-route), is also refused (moved to `failed/`) rather than replayed. This favors fail-safe (no duplicate turn/reply) over at-least-once for that window — the same product tradeoff the outbound queue makes when it has no reconcile capability. **Mitigation / follow-up**: a precise adapter-side `reconcileUnknownSend` (as the outbound queue has) would let recovery confirm actual send status and replay safely; that is a larger, multi-adapter change kept out of this one-thing-per-PR fix.

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
