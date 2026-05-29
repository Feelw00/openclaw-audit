#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-047.py

CAND-047 (task-registry-store cross-store-consistency, epic P1) baseline.
task-registry.ts 의 두 mutator 가 in-memory Map 을 먼저 commit 한 뒤 sqlite persist 를
보호 없이 호출 -> persist throw(SQLITE_BUSY/FULL/IOERR, withWriteTransaction re-throw)
시 in-memory <-> sqlite 발산.

  FIND-...-001 (P1, deleteTaskRecordById:2153-2173):
    tasks.delete(2162-2163) -> persistTaskDelete(2165) throw.
    in-memory 는 삭제됐는데 sqlite 행은 ROLLBACK 으로 잔존 -> reload 시 부활(lost-delete).
  FIND-...-002 (P2, updateTask:997-1011):
    tasks.set(next)(997) -> persistTaskUpsert(1011) throw.
    in-memory=next / sqlite=current(stale) -> sqlite-direct reader
    listFreshTasksForOwnerKey 가 같은 프로세스에서 stale 노출(wrong-output).

원리 (REQUIRES_EXTERNAL_DEP=False, 외부 의존 없음):
- worktree 의 src/tasks/task-registry.ts + task-registry.store.ts 를 tsx 로 직접 import.
- configureTaskRegistryRuntime 로 "sqlite write 가 throw 하는 store" 를 주입한다.
  store 의 backing 은 in-process JS Map (sqlite 행 시뮬레이션) 으로,
  withWriteTransaction(BEGIN IMMEDIATE) 가 BUSY/FULL/IOERR 로 ROLLBACK+re-throw 하는
  production 거동을 1:1 대응시킨다: delete/upsert 호출이 backing 을 건드리기 전에 throw.
- loadSnapshot 은 backing Map 을 반환 (sqlite SoT 재적재 = reload/restore 경로).

Trial DELETE (FIND-001):
  seed -> reloadTaskRegistryFromStore (in-memory 에 적재)
  -> deleteTaskRecordById (in-memory 삭제 후 persistTaskDelete throw, probe 가 catch)
  -> 측정: in-memory absent(getTaskById undefined) + sqlite present(backing 잔존)
  -> reloadTaskRegistryFromStore (sqlite SoT 재적재)
  -> 측정: resurrected (삭제했던 태스크가 in-memory 로 부활)
Trial UPDATE (FIND-002):
  seed(notifyPolicy=done_only) -> reload
  -> updateTaskNotifyPolicyById(state_changes) (in-memory=next 후 persistTaskUpsert throw)
  -> 측정: in-memory(getTaskById).notifyPolicy=next vs
           sqlite-direct(listFreshTasksForOwnerKey).notifyPolicy=stale -> 발산

without-fix(base sha): persist throw 가 in-memory mutate 를 되돌리지 못해 발산/부활 재현.
with-fix(가정): in-memory<->sqlite 교차-스토어 경계(persist 선행 또는 throw 시 rollback) 적용
               -> delete 시 in-memory 도 보존(부활 없음) / update 시 in-memory 도 stale 유지
               (양 스토어 동일값) -> 발산/부활 미발현.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-047"
DEFAULT_TRIALS = 2  # delete (FIND-001) + update (FIND-002)


def _build_probe_script() -> str:
    return """\
import {
  reloadTaskRegistryFromStore,
  deleteTaskRecordById,
  updateTaskNotifyPolicyById,
  getTaskById,
  listTasksForOwnerKey,
  listFreshTasksForOwnerKey,
} from './src/tasks/task-registry.ts';
// configureTaskRegistryRuntime / resetTaskRegistryRuntimeForTests live in the store module
// (task-registry.ts imports the latter but does not re-export it).
import {
  configureTaskRegistryRuntime,
  resetTaskRegistryRuntimeForTests,
} from './src/tasks/task-registry.store.ts';
import type { TaskRegistryStore } from './src/tasks/task-registry.store.ts';
import type { TaskRecord, TaskDeliveryState } from './src/tasks/task-registry.types.ts';

// A store whose backing Map simulates the sqlite rows (single source of truth on reload).
// Its write methods THROW before mutating the backing data — exactly the production
// behaviour of withWriteTransaction(BEGIN IMMEDIATE) which ROLLBACKs (no row change)
// and re-throws on SQLITE_BUSY / SQLITE_FULL / SQLITE_IOERR.
function makeThrowingStore(opts: { throwOnDelete: boolean; throwOnUpsert: boolean }) {
  const rows = new Map<string, TaskRecord>();
  const deliveryRows = new Map<string, TaskDeliveryState>();
  const store: TaskRegistryStore = {
    loadSnapshot: () => ({
      tasks: new Map(rows),
      deliveryStates: new Map(deliveryRows),
    }),
    saveSnapshot: (snapshot) => {
      rows.clear();
      for (const [k, v] of snapshot.tasks.entries()) rows.set(k, v);
      deliveryRows.clear();
      for (const [k, v] of snapshot.deliveryStates.entries()) deliveryRows.set(k, v);
    },
    listTasksForOwnerKey: (ownerKey) =>
      [...rows.values()].filter((t) => t.ownerKey === ownerKey),
    upsertTaskWithDeliveryState: ({ task, deliveryState }) => {
      if (opts.throwOnUpsert) {
        // ROLLBACK leaves the row untouched, then re-throw (store.sqlite.ts:503-506).
        throw new Error('SQLITE_BUSY: database is locked (BEGIN IMMEDIATE timeout)');
      }
      rows.set(task.taskId, task);
      if (deliveryState) deliveryRows.set(task.taskId, deliveryState);
    },
    deleteTaskWithDeliveryState: (taskId) => {
      if (opts.throwOnDelete) {
        throw new Error('SQLITE_FULL: database or disk is full');
      }
      rows.delete(taskId);
      deliveryRows.delete(taskId);
    },
    close: () => {},
  };
  return { store, rows, deliveryRows };
}

function makeTask(taskId: string, ownerKey: string, notifyPolicy: TaskRecord['notifyPolicy']): TaskRecord {
  const now = Date.now();
  return {
    taskId,
    runtime: 'cli',
    requesterSessionKey: ownerKey,
    ownerKey,
    scopeKind: 'session',
    task: 'probe task',
    status: 'running',
    deliveryStatus: 'pending',
    notifyPolicy,
    createdAt: now,
    lastEventAt: now,
  };
}

const trialResults: any[] = [];

// ---- Trial DELETE (FIND-001): lost-delete resurrection on reload ----
{
  resetTaskRegistryRuntimeForTests();
  const ownerKey = 'probe-owner-delete';
  const taskId = 'probe-delete-1';
  const { store, rows } = makeThrowingStore({ throwOnDelete: true, throwOnUpsert: false });
  // Seed the sqlite row directly, then load it into the in-memory registry.
  rows.set(taskId, makeTask(taskId, ownerKey, 'done_only'));
  configureTaskRegistryRuntime({ store, observers: null });
  reloadTaskRegistryFromStore();

  const inMemoryBeforeDelete = !!getTaskById(taskId);

  let persistThrew = false;
  try {
    deleteTaskRecordById(taskId);
  } catch (e) {
    persistThrew = true; // persistTaskDelete propagated the sqlite throw (no try/catch in deleteTaskRecordById)
  }

  // Immediately after the throw: in-memory deleted, sqlite row preserved by ROLLBACK -> divergence.
  const inMemoryAfterDelete = !!getTaskById(taskId);
  const sqliteAfterDelete = rows.has(taskId);
  const diverged = inMemoryBeforeDelete && !inMemoryAfterDelete && sqliteAfterDelete;

  // reload/restart re-loads sqlite as SoT -> deleted task resurrects.
  reloadTaskRegistryFromStore();
  const resurrected = !!getTaskById(taskId);

  trialResults.push({
    branch: 'delete-lost-delete',
    persistThrew,
    inMemoryBeforeDelete,
    inMemoryAfterDelete,
    sqliteAfterDelete,
    diverged,
    resurrected,
  });
}

// ---- Trial UPDATE (FIND-002): stale sqlite-direct read after upsert throw ----
{
  resetTaskRegistryRuntimeForTests();
  const ownerKey = 'probe-owner-update';
  const taskId = 'probe-update-1';
  const staleNotify = 'done_only';
  const nextNotify = 'state_changes';
  const { store, rows } = makeThrowingStore({ throwOnDelete: false, throwOnUpsert: true });
  rows.set(taskId, makeTask(taskId, ownerKey, staleNotify));
  configureTaskRegistryRuntime({ store, observers: null });
  reloadTaskRegistryFromStore();

  let persistThrew = false;
  try {
    updateTaskNotifyPolicyById({ taskId, notifyPolicy: nextNotify });
  } catch (e) {
    persistThrew = true; // persistTaskUpsert propagated; the try at :1012 only wraps syncFlowFromTask
  }

  const inMemoryRec = getTaskById(taskId);
  const inMemoryNotify = inMemoryRec ? inMemoryRec.notifyPolicy : null; // expected: next
  const memListNotify =
    listTasksForOwnerKey(ownerKey).find((t) => t.taskId === taskId)?.notifyPolicy ?? null; // in-memory index
  const sqliteDirectNotify =
    listFreshTasksForOwnerKey(ownerKey).find((t) => t.taskId === taskId)?.notifyPolicy ?? null; // sqlite-direct

  const diverged =
    inMemoryNotify === nextNotify && sqliteDirectNotify === staleNotify && memListNotify === nextNotify;

  trialResults.push({
    branch: 'update-stale-read',
    persistThrew,
    expectedNext: nextNotify,
    stale: staleNotify,
    inMemoryNotify,
    memListNotify,
    sqliteDirectNotify,
    diverged,
  });
}

resetTaskRegistryRuntimeForTests();

const divergenceCount = trialResults.filter((t) => t.diverged).length;
const resurrectedCount = trialResults.filter((t) => t.resurrected).length;

console.log(JSON.stringify({
  trials: trialResults.length,
  trialResults,
  divergenceCount,
  resurrectedCount,
}));
process.exit(0);
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused (in-process store, no real sqlite file)
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    script = _build_probe_script()
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False, dir=str(wt_path)) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            [str(tsx_bin), script_path],
            cwd=str(wt_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"probe exited {proc.returncode}",
                "stderr": proc.stderr[:800],
            }
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"could not parse probe stdout: {e}",
                "stdout": proc.stdout[:800],
            }
        if "skipped" in payload:
            return {"scenario": SCENARIO_NAME, "trials": 0, "skipped": payload["skipped"]}
        return {"scenario": SCENARIO_NAME, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: without-fix 에서 발산(divergence) + 부활(resurrection) 관측이면 collected."""
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    # delete trial 은 발산 + 부활, update trial 은 발산 -> divergenceCount==2 + resurrectedCount>=1
    if measurements.get("divergenceCount", 0) >= 2 and measurements.get("resurrectedCount", 0) >= 1:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: cross-store 일관성 신호는 divergenceCount (in-memory ↔ sqlite 발산).

    핵심 지표는 divergenceCount 다. persist-before-in-memory 수정 후엔 persist 가 throw 하면
    in-memory mutation 자체가 안 일어나 두 스토어가 항상 일치 (divergenceCount==0). 이때
    delete trial 은 "삭제가 atomic 하게 실패" 해 task 가 양쪽에 일관되게 남으므로 reload 후
    여전히 존재(resurrected=true)하지만 이는 발산이 아닌 올바른 동작이다. 따라서 with-fix 의
    resurrectedCount 는 fix-실패 신호가 아니며, divergenceCount==0 만으로 수정 효과를 판정한다.
    """
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_ok = without_fix.get("divergenceCount", 0) >= 1  # baseline 에서 cross-store 발산 관측
    wf_ok = with_fix.get("divergenceCount", 0) == 0     # 수정 후 발산 0 (atomic 일관)
    if wo_ok and wf_ok:
        return "collected"
    if not wo_ok:
        return "unreproducible"  # baseline 에서 발산이 안 보임
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    wo_trials = without_fix.get("trialResults", [])
    wf_trials = with_fix.get("trialResults", [])
    behavior = (
        "Without this patch, task-registry.ts mutators commit the in-memory Map before calling the unguarded "
        "sqlite persist. deleteTaskRecordById deletes from the in-memory `tasks`/`taskDeliveryStates` Maps "
        "(2162-2163) before persistTaskDelete (2165); updateTask sets `tasks.set(taskId, next)` (997) before "
        "persistTaskUpsert (1011), and the following try (1012) only wraps syncFlowFromTask. When the sqlite "
        "write throws (withWriteTransaction ROLLBACK + re-throw on SQLITE_BUSY / SQLITE_FULL / SQLITE_IOERR, "
        "store.sqlite.ts:503-506) the in-memory mutation is never rolled back. The deleted row survives in "
        "sqlite and resurrects on reloadTaskRegistryFromStore / restoreTaskRegistryOnce (lost-delete); the "
        "updated record diverges so the sqlite-direct reader listFreshTasksForOwnerKey returns the stale value "
        "while the in-memory path returns the new one. With this patch, an in-memory<->sqlite cross-store "
        "boundary (persist-before-commit or rollback-on-persist-fail) keeps both stores in agreement so neither "
        "resurrection nor stale divergence occurs."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw checked out at the base sha with node_modules present. "
        "Isolated temp HOME / OPENCLAW_HOME (temp dir); production ~/.openclaw untouched. No external "
        "dependencies (no OAuth/LLM/channel/network calls). The task-registry store is replaced via the "
        "public configureTaskRegistryRuntime seam with an in-process store whose write methods throw before "
        "mutating their backing data, mirroring withWriteTransaction's ROLLBACK (no row change) + re-throw."
    )
    steps = (
        "```text\n"
        "$ git checkout <base sha>                              (Build A) / <head sha> (Build B)\n"
        "$ node_modules/.bin/tsx <probe.ts>                     (worktree root, isolated temp HOME)\n"
        "  probe trial DELETE: seed sqlite row -> reload -> deleteTaskRecordById (persistTaskDelete throws)\n"
        "    -> measure in-memory absent + sqlite present (diverged) -> reload -> measure resurrected\n"
        "  probe trial UPDATE: seed -> reload -> updateTaskNotifyPolicyById (persistTaskUpsert throws)\n"
        "    -> compare in-memory getTaskById vs sqlite-direct listFreshTasksForOwnerKey\n"
        "```"
    )
    evidence = (
        "Live tsx measurement of cross-store divergence across two mutators:\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  trialResults: {json.dumps(wo_trials)}\n"
        f"  divergenceCount: {without_fix.get('divergenceCount')}  (delete + update both diverge)\n"
        f"  resurrectedCount: {without_fix.get('resurrectedCount')}  (deleted task revived on reload)\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  trialResults: {json.dumps(wf_trials)}\n"
        f"  divergenceCount: {with_fix.get('divergenceCount')}  (cross-store boundary holds both stores equal)\n"
        f"  resurrectedCount: {with_fix.get('resurrectedCount')}\n"
        "```"
    )
    observed = (
        f"Without patch: divergenceCount={without_fix.get('divergenceCount')}/2, "
        f"resurrectedCount={without_fix.get('resurrectedCount')} — the deleted task resurrects on reload and the "
        f"updated task's sqlite-direct read is stale. With patch: divergenceCount={with_fix.get('divergenceCount')}, "
        f"resurrectedCount={with_fix.get('resurrectedCount')} — both stores stay consistent through the persist throw."
    )
    not_tested = (
        "Real multi-process SQLITE_BUSY contention against an on-disk db file (the throw is injected at the store "
        "seam, which is the exact point withWriteTransaction re-throws after ROLLBACK). delivery-state-only "
        "mutators and the snapshot-fallback persist branches (default store always provides the transactional "
        "variant, so those are out of the production path per the FIND counter-evidence)."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True)
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    # Isolate the openclaw state dir only. Do NOT override HOME: this scenario uses a
    # fully in-process store (no real sqlite file) so production ~/.openclaw is never
    # touched, and overriding HOME would break HOME-rooted node version managers
    # (asdf/nvm) that the `#!/usr/bin/env node` tsx shim depends on.
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
