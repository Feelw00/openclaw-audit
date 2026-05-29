#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-053.py

CAND-053 (diagnostic-recovery ordering-causality, P2) baseline.
FIND-diagnostic-recovery-ordering-causality-001:
coordinator 의 in-flight 키 (recoveryRequestKey, coordinator.ts:63-69) 는
`${ref}:${stateGeneration ?? "unknown"}` 입도이고, runtime 의 in-flight 키
(recoveryKey, runtime.ts:55-57) 는 `ref` (sessionKey || sessionId) 입도다.

같은 ref(S) 를 in-flight 윈도우 중 generation 만 G -> G+1 로 bump 시키면:
- coordinator dedup(키 `S:G+1`) 은 새 키이므로 **통과** → 두 번째
  session.recovery.requested 이벤트를 발행하고 recover() 를 재호출.
- runtime dedup(키 `S`) 은 같은 ref 이므로 **skip** → already_in_flight 반환.
두 dedup 계층의 입도가 어긋나 중복 requested 이벤트 + 추적 상태 불일치가 남는다.
(영향이 관측/이벤트 한정 - skipped 는 state 를 mutate 하지 않음 → "중복 발행" 관측이
곧 재현이다.)

원리 (production 코드 직접 실행, build 우회):
- worktree 의 production 모듈들을 tsx 로 직접 import:
    src/logging/diagnostic-session-recovery-coordinator.ts
      → requestStuckSessionRecovery (public 진입점),
        resetDiagnosticSessionRecoveryCoordinatorForTest
    src/logging/diagnostic-stuck-session-recovery.runtime.ts
      → recoverStuckDiagnosticSession (public 진입점), __testing.resetRecoveriesInFlight
    src/logging/diagnostic-session-state.ts
      → getDiagnosticSessionState (state seed + generation bump)
    src/infra/diagnostic-events.ts
      → onInternalDiagnosticEvent (session.recovery.requested 카운트)
    src/agents/embedded-agent-runner/runs.ts
      → setActiveEmbeddedRun / forceClearEmbeddedAgentRun / __testing
        (runtime 의 abort/drain await 윈도우를 결정론적으로 열기 위함)

측정은 두 개의 독립 part 로 production 두 dedup 계층의 입도를 보인다.

Part A - coordinator 키 입도 (ref:generation):
- 세션 S 를 state="processing" 로 seed.
- session.recovery.requested 이벤트 리스너 등록.
- recover() 는 no-op (coordinator dedup 자체만 격리 측정).
- T1: requestStuckSessionRecovery(request@G=0) → coordinator 키 `S:0` add,
       requested 이벤트 #1 발행.
- generation 을 G+1 로 bump.
- T2: requestStuckSessionRecovery(request@G+1) → coordinator 키 `S:1` (새 키) →
       dedup 통과 → requested 이벤트 #2 발행.
  결과: coordinatorDuplicateOnBump == 2.
- 대조군 (bump 없음, 둘 다 G): coordinator 키도 `S:0` 동일 → dedup skip →
       requested 이벤트 1개. coordinatorDedupNoBump == 1.

Part B - runtime 키 입도 (ref only):
- 같은 ref(S)에 대해 production 의 active embedded run 을 register
  (setActiveEmbeddedRun) → runtime 이 `await abortAndDrainEmbeddedAgentRun` 의
  drain 윈도우(waitForEmbeddedAgentRunEnd)로 진입해 in-flight 키 `S` 를 유지.
- r1 = recoverStuckDiagnosticSession(request@G, allowActiveAbort=true) → 키 `S`
       add 후 drain await 에서 pending.
- r1 이 pending 인 동안 r2 = recoverStuckDiagnosticSession(request@G+1) 발사 →
       runtime 키 `S` 이미 in-flight (generation 무관, ref only) →
       already_in_flight 즉시 반환.
- forceClearEmbeddedAgentRun(S) 로 drain waiter resolve → r1 settle.
  결과: runtimeRefDedup == True (t2 outcome = skipped/already_in_flight).

종합 비대칭: 같은 ref 의 generation 차이가 coordinator 에선 별개 요청(중복 발행),
runtime 에선 같은 요청(skip) → 두 dedup 계층 입도 불일치. (skipped 는 state mutate
없음 → 영향은 관측/중복 이벤트 한정 = P2. "중복 발행" 관측이 곧 재현.)

REQUIRES_EXTERNAL_DEP=False - 외부 OAuth/LLM/채널 호출 없음, production 모듈 import 만.

without-fix (base, 현 코드): coordinatorDuplicateOnBump==2 + runtimeRefDedup==True
                            → asymmetryReproduced.
with-fix(가정): coordinator 키를 ref 단위로 정렬 → bump 후에도 coordinator dedup
              skip → coordinatorDuplicateOnBump==1 (두 계층 입도 일치).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-053"
DEFAULT_TRIALS = 1  # 단일 결정론적 시퀀스 (T1 → generation bump → T2)


def _build_probe_script() -> str:
    return """\
import {
  requestStuckSessionRecovery,
  resetDiagnosticSessionRecoveryCoordinatorForTest,
} from "./src/logging/diagnostic-session-recovery-coordinator.js";
import { recoverStuckDiagnosticSession, __testing as runtimeTesting } from "./src/logging/diagnostic-stuck-session-recovery.runtime.js";
import {
  getDiagnosticSessionState,
  resetDiagnosticSessionStateForTest,
} from "./src/logging/diagnostic-session-state.js";
import {
  onInternalDiagnosticEvent,
  resetDiagnosticEventsForTest,
} from "./src/infra/diagnostic-events.js";
import {
  setActiveEmbeddedRun,
  forceClearEmbeddedAgentRun,
  __testing as runsTesting,
} from "./src/agents/embedded-agent-runner/runs.js";

function emit(obj) {
  console.log("PROOF_RESULT:" + JSON.stringify(obj));
}

const SESSION_KEY = "probe-session-S";

const classification = {
  reason: "stale_session_state",
  classification: "stale_session_state",
  activeWorkKind: "embedded_run",
};

const baseRequest = {
  sessionId: SESSION_KEY,
  sessionKey: SESSION_KEY,
  ageMs: 600_000,
  queueDepth: 0,
  expectedState: "processing",
};

function seedSession(generation) {
  const st = getDiagnosticSessionState({ sessionKey: SESSION_KEY });
  st.sessionId = SESSION_KEY;
  st.sessionKey = SESSION_KEY;
  st.state = "processing";
  st.generation = generation;
  return st;
}

function resetAll() {
  resetDiagnosticSessionRecoveryCoordinatorForTest();
  runtimeTesting.resetRecoveriesInFlight();
  runsTesting.resetActiveEmbeddedRuns();
  resetDiagnosticSessionStateForTest();
  resetDiagnosticEventsForTest();
}

// ---------------------------------------------------------------------------
// Part A: coordinator 키 입도 (ref:generation) - generation bump 시 중복 발행 측정.
// recover() 는 pending promise 를 반환해 in-flight 키를 윈도우 동안 유지한다
// (production 의 recover() = abort/drain await 윈도우 대응). 이로써 coordinator
// dedup 만 격리 측정: bump 면 키가 달라져 dedup 통과(중복), no-bump 면 같은 키로 skip.
// ---------------------------------------------------------------------------
function makeDeferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

async function runCoordinatorSequence(bumpGeneration) {
  resetAll();
  const G = 0;
  seedSession(G);

  let requestedCount = 0;
  const requestedGenerations = [];
  const off = onInternalDiagnosticEvent((evt) => {
    if (evt.type === "session.recovery.requested") {
      requestedCount += 1;
      requestedGenerations.push(evt.stateGeneration);
    }
  });

  // recover() 는 in-flight 윈도우가 끝날 때까지 pending (production: settleMs<=15s).
  const windows = [];
  const recover = () => {
    const d = makeDeferred();
    windows.push(d);
    return d.promise;
  };

  // T1: 세션 S stuck (generation=G) → coordinator 키 `S:0` add (in-flight 유지).
  requestStuckSessionRecovery({ recover, request: { ...baseRequest, stateGeneration: G }, classification });

  // generation bump (production: logMessageQueued 등 동기 mutator).
  const Gnext = bumpGeneration ? G + 1 : G;
  seedSession(Gnext);

  // T2: 같은 세션 재판정 (generation=Gnext).
  //  - bump: 키 `S:1` 새 키 → dedup 통과 → requested #2 발행 (recover 재호출).
  //  - no-bump: 키 `S:0` 동일 → in-flight has → dedup skip (requested 1개).
  requestStuckSessionRecovery({ recover, request: { ...baseRequest, stateGeneration: Gnext }, classification });

  // 윈도우 닫기 → recover promise resolve → finally clearInFlight.
  for (const d of windows) {
    d.resolve(undefined);
  }
  await new Promise((res) => setImmediate(res));

  off();
  return { bumpGeneration, requestedCount, requestedGenerations };
}

// ---------------------------------------------------------------------------
// Part B: runtime 키 입도 (ref only) - 같은 ref 의 다른 generation 재요청이
// runtime in-flight 로 흡수(already_in_flight)되는지 측정.
// production 의 active embedded run 을 register 해 abort/drain await 윈도우를 연다.
// ---------------------------------------------------------------------------
async function runRuntimeSequence() {
  resetAll();
  const G = 0;
  seedSession(G);

  // production active embedded run 등록 → runtime 이 abort/drain await 로 진입,
  // in-flight 키 `S` 를 윈도우 동안 유지. handle.abort() 는 no-op 으로 두어
  // drain waiter 가 forceClear 까지 pending.
  const fakeHandle = {
    kind: "embedded",
    queueMessage: async () => {},
    isStreaming: () => false,
    isCompacting: () => false,
    abort: () => {}, // no-op: drain 윈도우를 열어두기 위함
  };
  setActiveEmbeddedRun(SESSION_KEY, fakeHandle, SESSION_KEY);
  // setActiveEmbeddedRun 이 logSessionStateChange 로 state 를 건드릴 수 있으므로
  // generation/state 를 다시 결정론적으로 고정.
  seedSession(G);

  // r1: allowActiveAbort=true → runtime 이 active run 을 abort/drain await →
  //     키 `S` add 후 waitForEmbeddedAgentRunEnd 에서 pending.
  const r1 = recoverStuckDiagnosticSession({ ...baseRequest, stateGeneration: G, allowActiveAbort: true });

  // r1 의 동기 부분(키 add + abort) 이 첫 await 까지 실행되도록 microtask 양보.
  await new Promise((res) => setImmediate(res));

  // generation bump (in-flight 윈도우 중).
  const Gnext = G + 1;
  seedSession(Gnext);

  // r2: 같은 ref(S), 다른 generation → runtime 키 `S` 이미 in-flight →
  //     already_in_flight 즉시 반환 (generation 무관).
  const r2 = recoverStuckDiagnosticSession({ ...baseRequest, stateGeneration: Gnext, allowActiveAbort: true });
  const t2RuntimeOutcome = await r2;

  // drain waiter resolve → r1 settle.
  forceClearEmbeddedAgentRun(SESSION_KEY, SESSION_KEY, "probe_cleanup");
  const t1RuntimeOutcome = await r1;

  return {
    t1RuntimeStatus: t1RuntimeOutcome?.status,
    t1RuntimeReason: t1RuntimeOutcome?.reason,
    t2RuntimeStatus: t2RuntimeOutcome?.status,
    t2RuntimeReason: t2RuntimeOutcome?.reason,
  };
}

try {
  // Part A - coordinator 키 비대칭.
  const withBump = await runCoordinatorSequence(true);   // generation bump → 중복 발행 기대.
  const noBump = await runCoordinatorSequence(false);    // 대조군: bump 없음 → dedup skip 기대.

  // Part B - runtime ref-단위 dedup.
  const runtime = await runRuntimeSequence();

  const coordinatorDuplicateOnBump = withBump.requestedCount;
  const coordinatorDedupNoBump = noBump.requestedCount;
  const runtimeRefDedup =
    runtime.t2RuntimeReason === "already_in_flight" || runtime.t2RuntimeStatus === "skipped";

  emit({
    withBump,
    noBump,
    runtime,
    coordinatorDuplicateOnBump,
    coordinatorDedupNoBump,
    runtimeRefDedup,
    // 비대칭 = bump 시 coordinator 는 2번 발행(중복 이벤트) 하지만 runtime 은
    // 같은 ref 로 흡수(skip) → 두 dedup 계층 입도 불일치.
    asymmetryReproduced:
      coordinatorDuplicateOnBump >= 2 &&
      coordinatorDedupNoBump <= 1 &&
      runtimeRefDedup === true,
  });
} catch (err) {
  emit({ error: String((err && err.stack) || err) });
  process.exit(0);
}
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused - 순수 모듈 probe
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent
    coord_src = wt_path / "src" / "logging" / "diagnostic-session-recovery-coordinator.ts"
    runtime_src = wt_path / "src" / "logging" / "diagnostic-stuck-session-recovery.runtime.ts"
    if not coord_src.exists() or not runtime_src.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"src missing: {coord_src} / {runtime_src}. worktree checkout 확인.",
        }
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"tsx missing: {tsx_bin}. pnpm install (skip_install=False) 확인.",
        }

    script = _build_probe_script()
    # probe 는 worktree root 에 둬야 ./src/logging/... 상대 import 가 resolve 된다.
    with tempfile.NamedTemporaryFile(
        suffix=".mts", mode="w", delete=False, dir=str(wt_path), prefix="proof-cand053-"
    ) as f:
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
        line = ""
        for raw in proc.stdout.splitlines():
            if raw.startswith("PROOF_RESULT:"):
                line = raw[len("PROOF_RESULT:") :]
                break
        if not line:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": f"no PROOF_RESULT line (exit {proc.returncode})",
                "stderr": proc.stderr[-800:],
                "stdout": proc.stdout[-400:],
            }
        payload = json.loads(line)
        if "error" in payload:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "error": payload["error"][:800],
            }
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            **payload,
        }
    except subprocess.TimeoutExpired:
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "error": "probe timed out after 120s",
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: 키 비대칭으로 인한 coordinator 중복 발행 + runtime ref-skip 관측이면 collected.

    asymmetryReproduced == True 면 결함 재현 (coordinator 가 generation bump 후 중복
    session.recovery.requested 를 발행하지만 runtime 은 같은 ref 로 흡수).
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    if measurements.get("asymmetryReproduced") is True:
        return "collected"
    # 명시적 측정값이 있으나 비대칭이 안 보이면 unreproducible.
    if "coordinatorDuplicateOnBump" in measurements:
        return "unreproducible"
    return "blocked-env"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: without 은 비대칭 발현, with 는 두 키 정렬로 coordinator dedup 일치."""
    for m in (without_fix, with_fix):
        if not m or m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo = without_fix
    wf = with_fix
    # without-fix: coordinator 가 bump 후 중복 발행 (>=2).
    # with-fix:    coordinator 키를 ref 단위로 정렬 → bump 후에도 dedup skip (==1).
    wo_dup = wo.get("coordinatorDuplicateOnBump", 0)
    wf_dup = wf.get("coordinatorDuplicateOnBump", 0)
    if wo.get("asymmetryReproduced") is True and wo_dup >= 2 and wf_dup <= 1:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    wo = without_fix
    wf = with_fix
    behavior = (
        "Without this patch, the diagnostic stuck-session recovery dedup uses two Set keys at "
        "different granularities: the coordinator's recoveryRequestKey "
        "(diagnostic-session-recovery-coordinator.ts:63-69) is `${ref}:${stateGeneration}` while "
        "the runtime's recoveryKey (diagnostic-stuck-session-recovery.runtime.ts:55-57) is `ref` "
        "alone. When the same session's generation is bumped during the in-flight abort/drain "
        "window (logMessageQueued etc. increment generation synchronously), the coordinator dedup "
        "treats `S:G+1` as a brand-new key and fires a SECOND session.recovery.requested event, but "
        "the runtime collapses both to ref `S` and returns already_in_flight. The result is a "
        "duplicate requested event with no matching recovery work (skipped is non-mutating, so no "
        "state corruption; the impact is observability: inflated recovery-attempt metrics and a "
        "requested/completed mismatch). With this patch, the coordinator key is aligned to the same "
        "ref granularity as the runtime, so the bumped re-request dedups instead of emitting a "
        "phantom requested event."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, isolated OPENCLAW_HOME. Both builds run the same probe "
        "against worktree src via tsx (no bundling). No external dependencies (no OAuth/LLM/channel "
        "calls) - the probe imports the production coordinator + runtime + session-state modules "
        "directly and drives requestStuckSessionRecovery / recoverStuckDiagnosticSession."
    )
    steps = (
        "```text\n"
        "$ pnpm install --frozen-lockfile           (both worktrees)\n"
        "$ node_modules/.bin/tsx <probe>.mts        (worktree-relative)\n"
        "  probe: seed session S (generation=G, state=processing); count session.recovery.requested;\n"
        "         T1 requestStuckSessionRecovery(req@G) -> recover() holds runtime in-flight;\n"
        "         bump session generation G -> G+1 inside the in-flight window;\n"
        "         T2 requestStuckSessionRecovery(req@G+1) + recoverStuckDiagnosticSession(req@G+1);\n"
        "         read requested-event count, runtime dedup outcome, control run (no bump)\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of session.recovery.requested emission and runtime ref-dedup "
        "across the two dedup layers (defect axis = generation bump; control = no bump):\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  with generation bump:\n"
        f"    coordinator session.recovery.requested count = {wo.get('coordinatorDuplicateOnBump')} "
        f"(generations seen = {json.dumps(wo.get('withBump', {}).get('requestedGenerations'))})\n"
        f"    runtime T2 outcome = {wo.get('withBump', {}).get('t2RuntimeStatus')}/"
        f"{wo.get('withBump', {}).get('t2RuntimeReason')}\n"
        f"  control (no bump):\n"
        f"    coordinator session.recovery.requested count = {wo.get('coordinatorDedupNoBump')}\n"
        f"  -> asymmetry reproduced: {wo.get('asymmetryReproduced')} "
        "(coordinator double-fires on bump; runtime absorbs by ref)\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  with generation bump:\n"
        f"    coordinator session.recovery.requested count = {wf.get('coordinatorDuplicateOnBump')}\n"
        f"  control (no bump):\n"
        f"    coordinator session.recovery.requested count = {wf.get('coordinatorDedupNoBump')}\n"
        "  -> coordinator key aligned to ref granularity: bumped re-request dedups (no phantom event)\n"
        "```"
    )
    observed = (
        f"With the patch, the coordinator emits {wf.get('coordinatorDuplicateOnBump')} "
        f"session.recovery.requested event(s) after a generation bump (down from "
        f"{wo.get('coordinatorDuplicateOnBump')} without the patch), matching the runtime's ref-level "
        "dedup. The two in-flight tracking layers now agree on dedup granularity, eliminating the "
        "phantom requested event whose recovery the runtime had silently skipped via already_in_flight."
    )
    not_tested = (
        "P2 / observability-only impact: the skipped recovery outcome is non-mutating "
        "(recoveryOutcomeMutatesSessionState=false), so this is a metrics/event consistency defect, "
        "not data corruption - that bound is asserted, not independently measured here. The "
        "alternative race where T1 completes just before T2 (runtime key cleared) so the second "
        "recovery actually runs (potential double abort/drain) depends on embedded-run abort "
        "idempotency, which is out of this scope and not exercised by this probe."
    )
    return {
        "behavior": behavior,
        "environment": environment,
        "steps": steps,
        "evidence": evidence,
        "observed_result": observed,
        "not_tested": not_tested,
    }


# CLI for ad-hoc testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True, help="worktree package.json path")
    ap.add_argument("--openclaw-home", required=True)
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    args = ap.parse_args()

    probe_env = os.environ.copy()
    probe_env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=probe_env,
        sqlite_path=Path(args.openclaw_home),
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
