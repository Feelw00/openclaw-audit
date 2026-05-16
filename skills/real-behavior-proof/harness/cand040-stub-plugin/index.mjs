// audit-side stub channel plugin for CAND-040 race window measurement
//
// production execution path that this stub enables:
// - registered as a channel plugin with approvalCapability.nativeRuntime
// - on gateway boot, startChannelApprovalHandlerBootstrap creates the approval handler
//   via createChannelApprovalHandlerFromCapability and calls handler.start()
// - audit-side ws probe sends exec.approval.request RPC -> handleRequested -> deliverTarget
// - deliverTarget calls nativeRuntime.transport.deliverPending which awaits a Deferred
//   gate (file-based IPC with the audit harness)
// - audit-side then triggers context unregister via fs flag -> onStopped fires ->
//   activeEntries.clear()
// - audit-side releases the deferred gate -> deliverPending returns ->
//   activeEntries.set re-inserts wrapped entry on cleared map (without-fix bug)
// - audit-side observes sideband file: unbindPending call count
//
// minimal ChannelPlugin: only the surfaces needed by the production approval bootstrap +
// runtime context registration are populated.

import { promises as fs } from "node:fs";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CONTROL_DIR_ENV = "OPENCLAW_AUDIT_STUB_C040_DIR";

function controlDir() {
  return process.env[CONTROL_DIR_ENV] || path.join(os.tmpdir(), "cand040-stub-control");
}

function ensureControlDir() {
  const dir = controlDir();
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function sidebandPath(name) {
  return path.join(ensureControlDir(), name);
}

async function appendSideband(file, obj) {
  const line = JSON.stringify({ ts: Date.now(), ...obj }) + "\n";
  await fs.appendFile(sidebandPath(file), line, "utf8");
}

async function waitForFile(file, timeoutMs, intervalMs = 50) {
  const fp = sidebandPath(file);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (existsSync(fp)) {
      return readFileSync(fp, "utf8");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`waitForFile timeout: ${fp}`);
}

function makeStubChannelPlugin() {
  return {
    id: "audit-stub-c040",
    meta: { label: "Audit Stub C040", order: 1000 },
    capabilities: {},
    config: {
      // minimum required by normalizeRegisteredChannelPlugin (channel-validation.ts)
      listAccountIds: () => ["audit-stub-c040"],
      resolveAccount: (_cfg, id) => ({
        accountId: id,
        configured: true,
      }),
      isAccountConfigured: () => true,
      isConfigured: () => true,
    },
    // gateway.startAccount must exist or startChannelInternal in server-channels.ts:367
    // returns early before reaching startChannelApprovalHandlerBootstrap. The returned
    // promise must hold until abortSignal fires; otherwise server-channels.ts:535
    // treats the channel as "exited without an error" and triggers auto-restart.
    gateway: {
      startAccount: async ({ accountId, abortSignal, channelRuntime }) => {
        await appendSideband("calls.jsonl", {
          call: "gateway.startAccount.enter",
          accountId,
          hasChannelRuntime: !!channelRuntime,
          hasRuntimeContexts: !!channelRuntime?.runtimeContexts,
          runtimeContextsKeys: channelRuntime?.runtimeContexts
            ? Object.keys(channelRuntime.runtimeContexts)
            : null,
        });
        // register the native approval runtime context on the SERVER-SIDE store
        // (caller passes channelRuntimeForTask from server-channels.ts:526 — same
        // store used by startChannelApprovalHandlerBootstrap via
        // getChannelRuntimeContext / watchChannelRuntimeContexts).
        // capability constant from src/infra/approval-handler-adapter-runtime.ts:8.
        let lease;
        if (channelRuntime?.runtimeContexts?.register) {
          try {
            lease = channelRuntime.runtimeContexts.register({
              channelId: "audit-stub-c040",
              accountId,
              capability: "approval.native",
              context: { source: "audit-stub-c040", token: "stub" },
              abortSignal,
            });
            await appendSideband("lease.jsonl", {
              call: "registered-on-startAccount",
              accountId,
              at: Date.now(),
            });
          } catch (err) {
            await appendSideband("lease.jsonl", {
              call: "register-error-on-startAccount",
              accountId,
              error: String(err),
            });
          }
        } else {
          await appendSideband("lease.jsonl", {
            call: "no-runtimeContexts-in-startAccount",
            accountId,
          });
        }
        // background watcher: dispose-lease.release file flag from the audit
        // probe triggers server-side lease dispose so onStopped fires while
        // deliverPending is parked.
        let watcherStop = false;
        let leaseDisposedByFlag = false;
        void (async () => {
          while (!watcherStop && !leaseDisposedByFlag) {
            if (existsSync(sidebandPath("dispose-lease.release"))) {
              try {
                lease?.dispose?.();
                leaseDisposedByFlag = true;
                await appendSideband("lease.jsonl", {
                  call: "startAccount-lease-disposed-by-flag",
                  accountId,
                  at: Date.now(),
                });
              } catch (err) {
                await appendSideband("lease.jsonl", {
                  call: "startAccount-lease-dispose-error",
                  accountId,
                  error: String(err),
                });
              }
              return;
            }
            await new Promise((r) => setTimeout(r, 100));
          }
        })();
        try {
          await new Promise((resolve) => {
            if (abortSignal?.aborted) {
              resolve();
              return;
            }
            abortSignal?.addEventListener("abort", () => resolve(), { once: true });
          });
        } finally {
          watcherStop = true;
          if (!leaseDisposedByFlag) {
            try {
              lease?.dispose?.();
            } catch {}
          }
          await appendSideband("calls.jsonl", {
            call: "gateway.startAccount.exit",
            accountId,
          });
        }
      },
    },
    approvalCapability: {
      // ChannelApprovalNativeAdapter — populates resolveChannelNativeApprovalDeliveryPlan
      // so the runtime actually iterates targets and calls prepareTarget/deliverTarget.
      // Without this, deliveryPlan.targets is [] and the race window never opens.
      native: {
        describeDeliveryCapabilities: () => ({
          enabled: true,
          preferredSurface: "origin",
          supportsOriginSurface: true,
        }),
        resolveOriginTarget: async () => ({ to: "audit-stub-origin", threadId: "stub" }),
      },
      nativeRuntime: {
        eventKinds: new Set(["exec"]),
        resolveApprovalKind: () => "exec",
        availability: {
          isConfigured: (ctx) => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "availability.isConfigured" }) + "\n"); } catch {}
            return true;
          },
          shouldHandle: (ctx) => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "availability.shouldHandle", requestId: ctx?.request?.id }) + "\n"); } catch {}
            return true;
          },
        },
        transport: {
          prepareTarget: async ({ plannedTarget, request }) => {
            await appendSideband("calls.jsonl", {
              call: "prepareTarget",
              requestId: request?.id,
              surface: plannedTarget?.surface,
            });
            // dedupeKey required by approval-native-runtime.ts:96 deliveredKeys.has.
            return {
              dedupeKey: `audit-stub:${request?.id}:${plannedTarget?.surface}`,
              target: { kind: "audit-stub-prepared", planned: plannedTarget },
            };
          },
          deliverPending: async ({ request, approvalKind }) => {
            await appendSideband("calls.jsonl", {
              call: "deliverPending.enter",
              requestId: request?.id,
              approvalKind,
            });
            await waitForFile("deliver-pending.release", 30_000);
            await appendSideband("calls.jsonl", {
              call: "deliverPending.exit",
              requestId: request?.id,
            });
            return {
              kind: "audit-stub-entry",
              requestId: request?.id,
              issuedAtMs: Date.now(),
            };
          },
        },
        interactions: {
          bindPending: async ({ entry, request }) => {
            await appendSideband("calls.jsonl", {
              call: "bindPending",
              requestId: request?.id,
              entryId: entry?.requestId,
            });
            return { kind: "audit-stub-binding", entryRequestId: entry?.requestId };
          },
          unbindPending: async ({ entry, binding, request }) => {
            await appendSideband("calls.jsonl", {
              call: "unbindPending",
              requestId: request?.id,
              entryId: entry?.requestId,
              bindingKind: binding?.kind,
            });
          },
        },
        presentation: {
          buildPendingPayload: async () => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "presentation.buildPendingPayload" }) + "\n"); } catch {}
            return { kind: "stub-pending" };
          },
          buildResolvedResult: async () => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "presentation.buildResolvedResult" }) + "\n"); } catch {}
            return { kind: "stub-resolved" };
          },
          buildExpiredResult: async () => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "presentation.buildExpiredResult" }) + "\n"); } catch {}
            return { kind: "stub-expired" };
          },
        },
        observe: {
          onDelivered: () => {
            try { appendFileSync(sidebandPath("calls.jsonl"), JSON.stringify({ ts: Date.now(), call: "observe.onDelivered" }) + "\n"); } catch {}
          },
        },
      },
    },
  };
}

// bundled-channel-entry contract (see src/plugin-sdk/channel-entry-contract.ts:437)
export default {
  kind: "bundled-channel-entry",
  id: "audit-stub-c040",
  name: "Audit Stub CAND-040",
  description: "audit-side stub for CAND-040 race window measurement",
  configSchema: { schema: { type: "object", additionalProperties: false, properties: {} } },
  loadChannelPlugin: () => makeStubChannelPlugin(),
  register(api) {
    const mode = api.registrationMode;
    void appendSideband("register-mode.jsonl", { mode, at: Date.now() });
    if (mode === "cli-metadata" || mode === "tool-discovery") {
      return;
    }
    const plugin = makeStubChannelPlugin();
    api.registerChannel({ plugin });
    // only the "full" mode (gateway main path) needs the runtime context lease
    // with a long-running background watcher; "discovery" mode is invoked by
    // CLI commands like `plugins install` and must return promptly so the
    // command can exit cleanly.
    if (mode !== "full") {
      return;
    }
    const channelRuntime = api.runtime?.channel;
    if (channelRuntime?.runtimeContexts?.register) {
      const lease = channelRuntime.runtimeContexts.register({
        channelId: plugin.id,
        accountId: plugin.id,
        capability: "approval.native",
        context: { source: "audit-stub-c040" },
      });
      // verify: can we read back our own lease via getContext?
      let readback;
      try {
        readback = channelRuntime.runtimeContexts.get?.({
          channelId: plugin.id,
          accountId: plugin.id,
          capability: "approval.native",
        });
      } catch (err) {
        readback = { error: String(err) };
      }
      void appendSideband("lease.jsonl", {
        call: "registered",
        at: Date.now(),
        runtimeContextsKeys: Object.keys(channelRuntime.runtimeContexts ?? {}),
        readback_self: readback,
        runtimeChannelKeys: Object.keys(channelRuntime ?? {}),
      });
      void (async () => {
        try {
          await waitForFile("dispose-lease.release", 600_000);
          lease.dispose();
          await appendSideband("lease.jsonl", { call: "disposed", at: Date.now() });
        } catch (err) {
          await appendSideband("lease.jsonl", {
            call: "dispose-error",
            error: String(err),
          });
        }
      })();
    } else {
      void appendSideband("lease.jsonl", {
        call: "no-channelRuntime",
        runtimeKeys: Object.keys(api.runtime ?? {}),
      });
    }
  },
};
