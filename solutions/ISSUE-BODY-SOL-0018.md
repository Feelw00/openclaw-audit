<!--
FILE THIS AS A BUG ISSUE (openclaw/openclaw), then set PR-BODY-SOL-0018.md "Linked Issue" to "Closes #N".
  title:  [Bug]: session-delivery recovery blind-replays an unacked agent turn after a crash (duplicate turn re-run + duplicate reply)
  labels: bug
NOTE: PR-BODY-SOL-0018 currently states "no tracking issue filed yet (stands alone)". If you file this issue, update that section to "Closes #N".
gh issue create --repo openclaw/openclaw --title "<title above>" --label bug --body-file solutions/ISSUE-BODY-SOL-0018.md
-->

**Bug type**: Behavior bug (incorrect output/state without crash)

**Beta release blocker**: No

## Summary

The session-delivery recovery queue blind-replays an unacked agent-turn delivery after a crash: if a restart-continuation `agentTurn` delivery succeeds (the turn re-runs and the platform reply is sent) but the process dies before the entry is acked, the next recovery re-delivers the same entry unconditionally, re-running a non-idempotent turn and re-sending its reply.

## Steps to reproduce

Deterministic, no external services (isolated `OPENCLAW_HOME` + explicit `OPENCLAW_STATE_DIR`; the `deliver` callback only counts invocations):

1. `enqueueSessionDelivery` an `agentTurn` entry.
2. PASS 1: run `recoverPendingSessionDeliveries` so `deliver(entry)` succeeds, but the ack does not complete (crash-before-ack). The entry stays pending; the on-disk state is byte-identical to a real crash before `ackSessionDelivery`'s atomic rename.
3. PASS 2: run `recoverPendingSessionDeliveries` again (restart).
4. Count `deliver` invocations on the same entry.

The recovery code path under test is the production one (`drainQueuedEntry` -> `deliver` is `deliverQueuedSessionDelivery`, wired from `server-restart-sentinel.ts`).

## Expected behavior

The entry is delivered once: the second recovery pass refuses to replay an entry whose send already happened (or cannot be confirmed not to have happened).

## Actual behavior

The entry is delivered twice (`deliverCount=2`): the turn re-runs and the reply is re-sent. `drainQueuedEntry` runs `await deliver(entry)` then `ackSessionDelivery`, and `QueuedSessionDelivery` has no `recoveryState` field, so a pending entry with no marker is treated as fresh and blind-replayed.

The parallel **outbound** queue already guards exactly this scenario (`send_attempt_started` / `unknown_after_send` markers + refuse-blind-replay on recovery); the **session** queue replicates none of it, so the two queues' durability guarantees diverge.

A live two-build probe measured `deliverCount=2 recoveryStateAfterCrash=field-absent` (without fix) vs `deliverCount=1 recoveryStateAfterCrash=unknown_after_send` (with fix).

## OpenClaw version

main branch, built from source at base upstream/main `9de6abd8d7`. Defect lines quoted below. Run under `tsx`.

## Operating system

macOS (darwin arm64). OS-independent (the on-disk pending-without-marker state is byte-identical across platforms).

## Install method

Built from source (`pnpm`), `tsx`.

## Model

Not applicable. The defect is in the session-delivery durable-queue recovery path; the probe replaces the deliver seam with an invocation counter and invokes no model.

## Provider / routing chain

Not applicable on the defect path. In production the replayed `deliver` reaches `dispatchAssembledChannelTurn` (channel adapter), but the bug (blind replay) is independent of which provider/channel is configured.

## Logs, screenshots, and evidence

```text
src/infra/session-delivery-queue-recovery.ts  drainQueuedEntry (105-124): await deliver(entry) -> ackSessionDelivery; classified only as recovered/failed
src/infra/session-delivery-queue-storage.ts:   QueuedSessionDelivery has NO recoveryState field
  -> crash after deliver succeeds, before ack -> pending file with no marker -> next recovery re-delivers unconditionally
parallel guard that this queue lacks: src/infra/outbound/delivery-queue-recovery.ts:370 (send_attempt_started / unknown_after_send), :417 "refusing blind replay without adapter reconciliation"

Regression repro (RED on base, profile infra):
  x does not re-deliver a session entry whose first delivery succeeded but was left unacked by a crash
    AssertionError: expected 2 to be 1  (deliverCount: blind replay)

Live two-build probe (isolated state dir, no network):
 without fix: pendingAfterCrash=1 recoveryStateAfterCrash=field-absent     deliverCount=2  (same entry delivered twice)
 with fix:    pendingAfterCrash=1 recoveryStateAfterCrash=unknown_after_send deliverCount=1  (recovery refuses blind replay)
```

## Impact and severity

- Affected: any deployment using the durable session-delivery queue for restart-continuation agent turns, when the process is killed in the window after deliver succeeds but before ack.
- Severity: reliability / crash-recovery correctness. The harm is a non-idempotent user turn (LLM/tool side-effects) re-executing plus a duplicate platform reply; no message-level dedup can absorb the turn re-run.
- Frequency: edge case (the crash window between deliver and ack), but deterministic in that window.
- Consequence: duplicate agent turn execution and duplicate user-visible reply on restart.

## Additional information

A fix is being prepared (port the outbound queue's `send_attempt_started` / `unknown_after_send` marker + refuse-blind-replay pattern to the session queue; fail-safe to `failed/` rather than replay). `src/infra/session-delivery-queue-*` is not under any `@openclaw/secops` CODEOWNERS constraint. The crash is modeled as deliver-success-then-ack-skip + a real restart-recovery pass; a real separate-process SIGKILL was not exercised (the on-disk state is byte-identical). AI-assisted analysis (Claude Code), grounded in the quoted source, the RED regression test, and the live before/after probe.
