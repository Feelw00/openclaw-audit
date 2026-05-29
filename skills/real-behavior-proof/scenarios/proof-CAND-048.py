#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-048.py

CAND-048 (secrets-apply cross-store-consistency, P1) baseline.
Primary axis = FIND-secrets-apply-cross-store-consistency-001:
apply.ts:835-855 runSecretsApply(write:true) 의 커밋 블록이 config(replaceConfigFile) +
N개 auth-profiles.json(for 루프 writeTextFileAtomic)를 파일 간 트랜잭션/저널/단일 lock
경계 없이 순차 커밋한다. config 커밋 성공 후 satellite write 루프 중간에서 throw 하면
config=ref / 일부 auth-store=plaintext 로 발산하고, catch 의 best-effort 롤백
(restoreFileSnapshot, try/catch 로 실패 삼킴)이 동일 fault 로 함께 실패하면 부분
마이그레이션이 영속한다.

결정론적 재현 원리 (REQUIRES_EXTERNAL_DEP=False, tsx --skip-build):
- worktree 의 src/secrets/apply.ts 의 runSecretsApply 를 tsx 로 직접 import.
- isolated temp HOME (mkdtempSync, production ~/.openclaw 미접촉) 에 openclaw.json
  (config 인라인 secret) + 2개 에이전트 auth-profiles.json (각 plaintext api_key) 준비.
- @openclaw/fs-safe 의 test-only DI seam (__setFsSafeTestHooksForTest 의
  beforeFileStoreSyncPrivateWrite, NODE_ENV=test gate) 으로 디스크 fault 를 결정론적으로
  주입한다. 이 seam 은 writeTextFileAtomic(mode 0o600) → privateFileStoreSync().writeText
  의 모든 private write 직전에 fire 한다. OS 권한 기반 주입(chmod read-only)은
  fs-safe 의 ensurePrivateDirectorySync 가 부모 dir 을 self-heal(mkdir+chmod back)하므로
  결정론적이지 않다 → fault 주입은 이 seam 으로 모델링한다.
- 주입 정책 (FIND-001 메커니즘 충실 재현):
    * store #2(beta) 의 commit write(첫 write)에서 throw  → 커밋 루프 중간 throw
      (config + store #1 은 이미 커밋된 상태).
    * store #1(alpha) 의 rollback restore write(두 번째 write)에서 throw → best-effort
      롤백이 동일 fault 로 실패 ("Best effort only" catch 가 삼킴).
- 측정: apply 가 throw 한 뒤 각 스토어의 최종 상태(REF=migrated / plaintext / missing).
  부분집합이 migrate 된 채 잔존하면(= 일부 REF + 일부 plaintext) cross-store 발산.

without-fix: store #1=REF(migrated, 롤백 실패로 잔존), store #2=plaintext(커밋 throw),
            config=plaintext(롤백 성공) → migrated_stores=1, total_stores=2, diverged=True.
with-fix:   파일 간 트랜잭션/저널 경계(또는 보장된 전량 롤백)로 mid-commit throw 시
            부분 커밋이 잔존하지 않음 → migrated_stores ∈ {0, total_stores}, diverged=False.

대조군 trial: fault 주입 없이 동일 plan write → 정상 전량 커밋(migrated=total, diverged=False)
            로 측정 경로가 healthy path 에서 발산을 오탐하지 않음을 확인.

보안경로 주의: secrets/apply.ts + agents/auth-profiles 는 자격증명 SoT 경로.
.github/CODEOWNERS 의 /src/agents/*auth* 게이트 대상 인접 영역이라 SOL/PR 단계에서
CODEOWNERS 동의가 필요하다(render_pr_evidence not_tested 에 명시).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-048"
DEFAULT_TRIALS = 2  # control (no fault) + defect (mid-commit throw + rollback failure)


def _build_probe_script() -> str:
    return r"""
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { __setFsSafeTestHooksForTest } from "@openclaw/fs-safe/test-hooks";
import { runSecretsApply } from "./src/secrets/apply.ts";

function mkdirp(p, mode = 0o700) { fs.mkdirSync(p, { recursive: true, mode }); }
function writeJson(p, v) {
  mkdirp(path.dirname(p));
  fs.writeFileSync(p, JSON.stringify(v, null, 2) + "\n", { mode: 0o600 });
}
function readJsonIf(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch { return null; } }

// Plaintext credential material lives only inside the per-trial temp HOME.
const PCFG = "sk-plain-cfg-PROBEONLY";
const PA = "sk-plain-alpha-PROBEONLY";
const PB = "sk-plain-beta-PROBEONLY";

// fs-safe test seam is gated on NODE_ENV=test / VITEST=true.
process.env.NODE_ENV = "test";

function buildPlan(env) {
  return {
    version: 1,
    protocolVersion: 1,
    generatedAt: new Date().toISOString(),
    generatedBy: "manual",
    targets: [
      {
        type: "agents.defaults.memorySearch.remote.apiKey",
        path: "agents.defaults.memorySearch.remote.apiKey",
        ref: { source: "env", provider: "local", id: "OPENCLAW_KEY_CFG" },
      },
      {
        type: "auth-profiles.api_key.key",
        path: "profiles.prof-a.key",
        agentId: "alpha",
        authProfileProvider: "openai",
        ref: { source: "env", provider: "local", id: "OPENCLAW_KEY_ALPHA" },
      },
      {
        type: "auth-profiles.api_key.key",
        path: "profiles.prof-b.key",
        agentId: "beta",
        authProfileProvider: "openai",
        ref: { source: "env", provider: "local", id: "OPENCLAW_KEY_BETA" },
      },
    ],
  };
}

// Build an isolated temp HOME with config + 2 agent auth-profiles.json.
function setupHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "proof-cand048-"));
  const stateDir = path.join(home, ".openclaw");
  mkdirp(stateDir);
  const configPath = path.join(stateDir, "openclaw.json");
  writeJson(configPath, {
    secrets: { providers: { local: { source: "env" } } },
    agents: { defaults: { memorySearch: { remote: { apiKey: PCFG } } } },
  });
  const storeA = path.join(stateDir, "agents", "alpha", "agent", "auth-profiles.json");
  const storeB = path.join(stateDir, "agents", "beta", "agent", "auth-profiles.json");
  writeJson(storeA, { version: 1, profiles: { "prof-a": { type: "api_key", provider: "openai", key: PA } } });
  writeJson(storeB, { version: 1, profiles: { "prof-b": { type: "api_key", provider: "openai", key: PB } } });
  const env = {
    ...process.env,
    NODE_ENV: "test",
    OPENCLAW_HOME: home,
    HOME: home,
    OPENCLAW_KEY_CFG: PCFG,
    OPENCLAW_KEY_ALPHA: PA,
    OPENCLAW_KEY_BETA: PB,
  };
  return { home, stateDir, configPath, storeA, storeB, env };
}

// Classify a store's final on-disk state for its single profile.
function classifyStore(p, profileId) {
  const obj = readJsonIf(p);
  if (!obj || typeof obj !== "object" || !obj.profiles || !obj.profiles[profileId]) {
    return "missing";
  }
  const prof = obj.profiles[profileId];
  const hasRef = prof && typeof prof === "object" && "keyRef" in prof;
  const hasPlain = prof && typeof prof === "object" && "key" in prof;
  if (hasRef && !hasPlain) return "ref";
  if (hasPlain && !hasRef) return "plaintext";
  return "mixed";
}

function classifyConfig(configPath) {
  const cfg = readJsonIf(configPath);
  const v = cfg && cfg.agents && cfg.agents.defaults && cfg.agents.defaults.memorySearch
    && cfg.agents.defaults.memorySearch.remote
    ? cfg.agents.defaults.memorySearch.remote.apiKey
    : undefined;
  if (v && typeof v === "object") return "ref";
  if (typeof v === "string") return "plaintext";
  return "missing";
}

async function runTrial(injectFault) {
  const s = setupHome();
  const plan = buildPlan(s.env);
  let threw = false;
  let errMsg = "";

  if (injectFault) {
    const writeCounts = new Map();
    __setFsSafeTestHooksForTest({
      beforeFileStoreSyncPrivateWrite: (filePath) => {
        const n = (writeCounts.get(filePath) ?? 0) + 1;
        writeCounts.set(filePath, n);
        // store #2 (beta): fail its commit (first) write -> mid-loop throw.
        if (filePath === s.storeB && n === 1) {
          throw new Error("INJECTED-FAULT: ENOSPC at store#2 commit");
        }
        // store #1 (alpha): fail its rollback restore (second) write -> best-effort rollback fails.
        if (filePath === s.storeA && n === 2) {
          throw new Error("INJECTED-FAULT: EACCES at store#1 rollback");
        }
      },
    });
  }

  try {
    await runSecretsApply({ plan, env: s.env, write: true });
  } catch (e) {
    threw = true;
    errMsg = String(e).slice(0, 200);
  } finally {
    if (injectFault) __setFsSafeTestHooksForTest(undefined);
  }

  const stores = {
    "store#1-alpha": classifyStore(s.storeA, "prof-a"),
    "store#2-beta": classifyStore(s.storeB, "prof-b"),
  };
  const config = classifyConfig(s.configPath);
  const storeStates = Object.values(stores);
  const migratedStores = storeStates.filter((x) => x === "ref").length;
  const totalStores = storeStates.length;
  // Divergence = a strict, non-empty subset of stores migrated (some ref + some non-ref).
  const diverged = migratedStores > 0 && migratedStores < totalStores;

  // best-effort cleanup of temp HOME (credential material).
  try { fs.rmSync(s.home, { recursive: true, force: true }); } catch {}

  return { injectFault, threw, errMsg, config, stores, migratedStores, totalStores, diverged };
}

(async () => {
  // Control trial: no fault -> healthy full commit (must NOT register divergence).
  const control = await runTrial(false);
  // Defect trial: mid-commit throw + rollback failure -> partial migration persists.
  const defect = await runTrial(true);

  console.log(JSON.stringify({
    trials: 2,
    control,
    defect,
    // top-level summary keyed for evaluate_pre/post.
    defectThrew: defect.threw,
    defectDiverged: defect.diverged,
    defectMigratedStores: defect.migratedStores,
    defectTotalStores: defect.totalStores,
    controlDiverged: control.diverged,
    controlMigratedStores: control.migratedStores,
  }));
  process.exit(0);
})().catch((e) => {
  console.log(JSON.stringify({ trials: 0, error: "probe-threw: " + String(e).slice(0, 300) }));
  process.exit(0);
});
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent  # worktree root (skip_build: node_entry == wt/package.json)
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    apply_src = wt_path / "src" / "secrets" / "apply.ts"
    if not apply_src.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"apply.ts missing: {apply_src}"}

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
            timeout=180,
        )
        # probe always exits 0 and prints JSON (errors are encoded in the payload).
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as e:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"could not parse probe stdout: {e}",
                "stdout": proc.stdout[:600],
                "stderr": proc.stderr[:600],
            }
        if payload.get("trials", 0) == 0 or "error" in payload:
            return {"scenario": SCENARIO_NAME, **payload}
        return {"scenario": SCENARIO_NAME, "trials": trials, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: without-fix 빌드가 부분커밋 발산을 보이면 collected.

    blocked-env : 플랜 구성/실행 실패 (tsx 없음, apply.ts 없음, probe throw, 파싱 실패).
    collected   : 결함축이 reproduce — 미드커밋 throw + 발산(일부 store 만 migrate 잔존)
                  + 대조군은 발산 없음.
    unreproducible : throw 했으나 부분커밋 발산이 관측 안 됨 (전량 롤백/전량 커밋) 또는
                  대조군이 healthy path 에서 발산 오탐.
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    defect = measurements.get("defect") or {}
    control = measurements.get("control") or {}
    if not defect or not control:
        return "blocked-env"
    # 대조군이 healthy full-commit 인데도 발산을 보고하면 측정 신뢰 불가.
    if control.get("diverged") is True:
        return "unreproducible"
    # 결함축: 커밋 루프 중간 throw + 부분 마이그레이션 발산.
    if defect.get("threw") is True and defect.get("diverged") is True:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: without 에서는 발산, with 에서는 발산 없음(전량 롤백 or 전량 커밋)이면 collected."""
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo = without_fix.get("defect") or {}
    wf = with_fix.get("defect") or {}
    wo_ctrl = without_fix.get("control") or {}
    wf_ctrl = with_fix.get("control") or {}
    if not wo or not wf:
        return "blocked-env"
    # 양 빌드 모두 대조군(healthy)에서는 발산이 없어야 측정이 유효.
    if wo_ctrl.get("diverged") is True or wf_ctrl.get("diverged") is True:
        return "unreproducible"
    # without-fix: 미드커밋 throw 시 부분커밋 발산.
    wo_ok = wo.get("threw") is True and wo.get("diverged") is True
    # with-fix: 동일 fault 에서 발산 해소 (migrated ∈ {0, total}).
    wf_migrated = wf.get("migratedStores")
    wf_total = wf.get("totalStores")
    wf_fixed = (
        wf.get("diverged") is False
        and wf_migrated is not None
        and wf_total is not None
        and wf_migrated in (0, wf_total)
    )
    if wo_ok and wf_fixed:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    wo = without_fix.get("defect") or {}
    wf = with_fix.get("defect") or {}
    wo_ctrl = without_fix.get("control") or {}
    wf_ctrl = with_fix.get("control") or {}

    behavior = (
        "Without this patch, runSecretsApply (src/secrets/apply.ts:835-855) commits one logical "
        "credential-migration transaction (plaintext credential -> SecretRef) as a non-atomic "
        "sequence: replaceConfigFile for openclaw.json, then a `for` loop of writeTextFileAtomic "
        "over N auth-profiles.json / legacy auth.json / .env. There is no cross-file transaction, "
        "journal, or single lock boundary. If a satellite write throws mid-loop (ENOSPC / EACCES / "
        "EIO) after earlier stores already committed, the catch block runs a best-effort rollback "
        "(restoreFileSnapshot wrapped in try/catch that swallows failures, \"Best effort only\"). "
        "When the same fault also fails a restore write, the partial migration persists: some "
        "auth-stores are left migrated to SecretRef while others stay plaintext, diverging the "
        "credential source-of-truth. With this patch, a mid-commit fault leaves no partial "
        "migration (all-or-nothing: full rollback or full commit)."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw worktrees checked out at the base and head shas "
        "(base = c559776c51). tsx executes src/secrets/apply.ts directly (--skip-build). Each trial "
        "builds a fresh isolated temp HOME via fs.mkdtempSync (os.tmpdir()); production ~/.openclaw "
        "and real credentials are never touched, and the temp HOME is removed after each trial. "
        "No external dependencies (no OAuth / LLM / network). The disk fault is injected "
        "deterministically through @openclaw/fs-safe's test-only DI seam "
        "(__setFsSafeTestHooksForTest.beforeFileStoreSyncPrivateWrite, gated on NODE_ENV=test) "
        "because OS permission denial is self-healed by fs-safe's ensurePrivateDirectorySync "
        "(it re-mkdirs and chmods the store directory)."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile                      (both worktrees, --skip-build)\n"
        "$ node_modules/.bin/tsx <probe.ts>                    (worktree-relative)\n"
        "  Control trial : runSecretsApply(write) with no fault -> expect full commit, no divergence.\n"
        "  Defect trial  : fault injected so store#2 commit throws AND store#1 rollback restore throws.\n"
        "                  Measures each store's final on-disk state (ref / plaintext / missing).\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of per-store final state after a mid-commit throw:\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  control trial: stores={json.dumps(wo_ctrl.get('stores'))} "
        f"config={wo_ctrl.get('config')} diverged={wo_ctrl.get('diverged')}\n"
        f"  defect  trial: threw={wo.get('threw')} stores={json.dumps(wo.get('stores'))} "
        f"config={wo.get('config')}\n"
        f"                 migratedStores={wo.get('migratedStores')}/{wo.get('totalStores')} "
        f"diverged={wo.get('diverged')}  (partial migration persisted)\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  control trial: stores={json.dumps(wf_ctrl.get('stores'))} "
        f"config={wf_ctrl.get('config')} diverged={wf_ctrl.get('diverged')}\n"
        f"  defect  trial: threw={wf.get('threw')} stores={json.dumps(wf.get('stores'))} "
        f"config={wf.get('config')}\n"
        f"                 migratedStores={wf.get('migratedStores')}/{wf.get('totalStores')} "
        f"diverged={wf.get('diverged')}  (no partial migration)\n"
        "```"
    )
    observed = (
        f"Without patch, a mid-commit throw leaves migratedStores="
        f"{wo.get('migratedStores')}/{wo.get('totalStores')} with diverged={wo.get('diverged')} "
        f"(a strict non-empty subset of auth-stores migrated to SecretRef while the rest stay "
        f"plaintext). With patch, the same injected fault yields migratedStores="
        f"{wf.get('migratedStores')}/{wf.get('totalStores')} with diverged={wf.get('diverged')} "
        f"(all-or-nothing). The control trial reports diverged="
        f"{wo_ctrl.get('diverged')}/{wf_ctrl.get('diverged')} on both builds, confirming the "
        f"healthy full-commit path is not flagged."
    )
    not_tested = (
        "SECURITY-SENSITIVE PATH: src/secrets/apply.ts touches the credential source-of-truth and "
        "is adjacent to the .github/CODEOWNERS /src/agents/*auth* gate (auth-profiles store). Any "
        "fix that introduces a cross-file transaction/journal or acquires AUTH_STORE_LOCK_OPTIONS "
        "during apply commits must be reviewed under that CODEOWNERS gate before merge. "
        "Not tested here: (1) the concurrent-writer lost-update axis "
        "(FIND-secrets-apply-cross-store-consistency-002, AUTH_STORE_LOCK bypass) which is a "
        "separate child task of this epic; (2) production-scale fault timing under real ENOSPC/EIO "
        "(modeled deterministically via the fs-safe DI seam rather than real disk exhaustion); "
        "(3) the legacy auth.json and .env satellite writes (same loop, not exercised by this plan)."
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
    ap.add_argument("--node-entry", required=True, help="worktree package.json (skip_build) or openclaw.mjs")
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
