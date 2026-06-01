#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-056.py

CAND-056 (infra-delivery-queue ordering-causality, P1) — CAND-050 의 SQLite-아키텍처 재검증.
FIND-infra-delivery-queue-ordering-causality-001.

배경: upstream #88665 (1af4c035e4 "refactor: move delivery queues to SQLite") 가
session-delivery 큐 저장 계층을 파일기반에서 공용 SQLite backing
(src/infra/delivery-queue-sqlite.ts) 으로 재작성했다. CAND-050/SOL-0018/PR #88016 의
파일기반 proof 는 무효이므로 현재 upstream 기준으로 결함 생존을 재확인한다 (CAL-007).

결함 (코드 레벨, #88665 가 미변경): 복구 경로 drainQueuedEntry
(src/infra/session-delivery-queue-recovery.ts:111) 가 entry 를 `await deliver(entry)` 한 뒤
`ackSessionDelivery`(:121) 로 entry 를 제거한다(at-least-once). deliver 성공(agentTurn 재실행 +
플랫폼 응답 전송 완료) 직후 ack 직전에 프로세스가 죽으면 entry 가 pending 으로 남고, 다음
복구(recoverPendingSessionDeliveries:214)가 동일 entry 를 reconciliation 없이 무조건 재-deliver 한다.

substrate 변경 (fix 자리): #88665 가 공용 SQLite backing 에 recovery_state 컬럼을 이미 추가했으나
(delivery-queue-sqlite.ts:27/38/66/118) session-delivery 쪽은 그 컬럼을 쓰지 않는다 —
QueuedSessionDelivery 타입에 recoveryState 필드 미노출, 마커 set/clear 함수 부재, drainQueuedEntry 에
recovery_state 를 보고 blind replay 를 거부하는 분기 부재. 평행 outbound 큐
(outbound/delivery-queue-recovery.ts)는 recovery_state 마커 + reconcile 후에만 replay 하며 확인
불가 시 blind replay 거부 — 동일 SQLite backing 을 공유하면서도 session 큐만 가드 미사용.

원리 (REQUIRES_EXTERNAL_DEP=False):
- isolated temp HOME(OPENCLAW_HOME) + 명시 OPENCLAW_STATE_DIR 로 production state SQLite 격리.
- worktree 의 src/infra/session-delivery-queue-{storage,recovery}.ts 를 tsx 로 직접 import (--skip-build).
- production enqueueSessionDelivery 로 agentTurn entry 1건 enqueue (SQLite delivery_queue 행 생성).
- PASS 1 (crash-before-ack 모델): deliver(entry) 성공 후 ackSessionDelivery 미호출
  (= ack 직전 SIGKILL/OOM). entry 는 pending 으로 잔존, recovery_state 마커 미기록(base 는 능력 부재).
- PASS 2 (restart 모델): production recoverPendingSessionDeliveries 호출 — real drainQueuedEntry +
  real ackSessionDelivery. base 는 reconciliation 분기 없음 → 동일 entry blind replay → deliverCount==2.
- stdout JSON.

SQLite 요구: 새 storage 는 node:sqlite StatementSync.columns() 를 쓴다 → Node 24+ 필요
(로컬 기본 v23.9.0 불가). run_scenario 가 /tmp/node-v24*-<arch>/bin 을 찾아 PATH 에 prepend 한다.

crash 모델링 한계 (정직성): crash 를 "ack skip + 동일 프로세스 재-drain" 으로 모델링 (별도 프로세스
SIGKILL 아님). 핵심 인과(pending 잔존 + recovery_state 가드 부재 → blind replay)는 동일 재현.
post-sol 은 real process restart 로 강화 권장.

without-fix(base): PASS 2 blind replay → deliverCount==2.
with-fix(post-sol): drainQueuedEntry 가 recovery_state(send_attempt_started/unknown_after_send)를
                    보고 blind replay 거부 → deliverCount==1 (가정; post-sol 패치 빌드에서 측정).
"""

from __future__ import annotations

import glob
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-056"
DEFAULT_TRIALS = 1


def _node24_bin() -> str | None:
    """SQLite 테스트용 Node 24+ 바이너리 디렉터리 탐색 (StatementSync.columns).
    로컬 기본 node 가 24 미만일 때 /tmp 에 받아둔 공식 빌드를 prepend 한다."""
    arch = "arm64" if platform.machine() == "arm64" else "x64"
    osname = "darwin" if platform.system() == "Darwin" else "linux"
    for pat in (
        f"/tmp/node-v24*-{osname}-{arch}/bin",
        f"/tmp/node-v2[4-9]*-{osname}-{arch}/bin",
    ):
        for d in sorted(glob.glob(pat), reverse=True):
            if Path(d, "node").exists():
                return d
    return None


def _build_probe_script() -> str:
    # private drainQueuedEntry 는 export 되지 않으므로, PASS 1 의 crash 모델을
    # production drainQueuedEntry 의 마커 영속 seam 으로 정렬한다 (with-fix 경로 정합):
    #   1. markSessionDeliveryPlatformSendAttemptStarted(id)  (fix 의 pre-deliver 마커, typeof 가드)
    #   2. deliver(entry)                                      (전송 성공 == throw 안 함)
    #   3. markSessionDeliveryPlatformOutcomeUnknown(id)       (fix 의 post-deliver 마커, typeof 가드)
    #   4. ackSessionDelivery SKIP                             (= ack 직전 crash)
    # base(without-fix) 는 mark* export 가 없으므로 typeof 가드로 건너뛴다 (base 현실: 마커 부재).
    # PASS 2 는 public recoverPendingSessionDeliveries 를 그대로 돌린다 (real 복구 경로).
    return """\
import * as storage from "./src/infra/session-delivery-queue-storage.js";
import { recoverPendingSessionDeliveries } from "./src/infra/session-delivery-queue-recovery.js";

const {
  enqueueSessionDelivery,
  loadPendingSessionDeliveries,
  loadPendingSessionDelivery,
} = storage;

function emit(obj) {
  console.log("PROOF_RESULT:" + JSON.stringify(obj));
}

const silentLog = { info() {}, warn() {}, error() {} };

try {
  const stateDir = process.env.PROOF_STATE_DIR;
  if (!stateDir) {
    emit({ error: "PROOF_STATE_DIR unset" });
    process.exit(0);
  }

  // with-fix 는 단일 마커(send_attempt_started)만 노출한다(O2). base 에는 이 export 가 없다.
  const hasMarkerApi =
    typeof storage.markSessionDeliveryPlatformSendAttemptStarted === "function";

  let deliverCount = 0;
  const deliveredIds = [];
  const deliver = async (entry) => {
    deliverCount += 1;
    deliveredIds.push(entry.id);
    // 성공: throw 하지 않음 == 전송 완료.
  };

  // --- enqueue: restart continuation 이 만드는 agentTurn entry 1건 (SQLite delivery_queue 행) ---
  const id = await enqueueSessionDelivery(
    {
      kind: "agentTurn",
      sessionKey: "proof-session",
      message: "proof user turn",
      messageId: "proof-msg-1",
      expectedSessionId: "proof-session-id",
    },
    stateDir,
  );

  const pendingAfterEnqueue = (await loadPendingSessionDeliveries(stateDir)).length;

  // --- PASS 1: crash-before-ack 모델 ---
  const entry1 = await loadPendingSessionDelivery(id, stateDir);
  if (!entry1) {
    emit({ error: "entry missing before pass1 deliver" });
    process.exit(0);
  }
  if (hasMarkerApi) {
    await storage.markSessionDeliveryPlatformSendAttemptStarted(entry1.id, stateDir);
  }
  await deliver(entry1);
  // <-- 여기서 crash. ackSessionDelivery 미호출. entry 는 pending 으로 잔존.
  // (with-fix 는 단일 마커 send_attempt_started 만 쓴다 — deliver 전 1회 영속이 곧 crash 증거.
  //  post-deliver upgrade 는 redundant 라 제거됨[O2]. base 는 마커 API 자체가 없다.)

  const pendingAfterCrash = (await loadPendingSessionDeliveries(stateDir)).length;
  const stillPendingEntry = await loadPendingSessionDelivery(id, stateDir);
  const recoveryStateAfterCrash =
    stillPendingEntry && "recoveryState" in stillPendingEntry
      ? stillPendingEntry.recoveryState ?? null
      : "field-absent";

  // --- PASS 2: restart 모델 (real recovery 경로) ---
  const summary = await recoverPendingSessionDeliveries({
    deliver,
    log: silentLog,
    stateDir,
    maxRecoveryMs: 30_000,
  });

  const pendingAfterRecover = (await loadPendingSessionDeliveries(stateDir)).length;

  emit({
    trials: 1,
    deliverCount,
    deliveredIds,
    markerApiPresent: hasMarkerApi,
    pendingAfterEnqueue,
    pendingAfterCrash,
    recoveryStateAfterCrash,
    pendingAfterRecover,
    recovered: summary.recovered,
    failed: summary.failed,
    skippedMaxRetries: summary.skippedMaxRetries,
    deferredBackoff: summary.deferredBackoff,
    storageBackend: "sqlite",
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
    sqlite_path: Path,  # state sqlite path (session-delivery queue now lives in shared state DB)
    trials: int = DEFAULT_TRIALS,
    timeout_seconds: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry.parent  # worktree root
    storage_src = wt_path / "src" / "infra" / "session-delivery-queue-storage.ts"
    recovery_src = wt_path / "src" / "infra" / "session-delivery-queue-recovery.ts"
    if not storage_src.exists() or not recovery_src.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"src missing: {storage_src} / {recovery_src}. worktree checkout 확인.",
        }
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": f"tsx missing: {tsx_bin}. pnpm install 확인.",
        }

    home = env.get("OPENCLAW_HOME")
    if not home:
        return {
            "scenario": SCENARIO_NAME,
            "trials": 0,
            "error": "OPENCLAW_HOME unset — isolated temp HOME required.",
        }
    state_dir = str(Path(home) / ".openclaw")
    probe_env = dict(env)
    probe_env["PROOF_STATE_DIR"] = state_dir
    probe_env["OPENCLAW_STATE_DIR"] = state_dir

    # Node 24+ 필요 (StatementSync.columns). 로컬 기본 node 가 낮으면 /tmp Node24 prepend.
    node24 = _node24_bin()
    node_note = "inherited"
    if node24:
        probe_env["PATH"] = node24 + os.pathsep + probe_env.get("PATH", "")
        node_note = node24

    script = _build_probe_script()
    with tempfile.NamedTemporaryFile(
        suffix=".mts", mode="w", delete=False, dir=str(wt_path), prefix="proof-cand056-"
    ) as f:
        f.write(script)
        script_path = f.name

    timeout = timeout_seconds if timeout_seconds is not None else 120
    try:
        proc = subprocess.run(
            [str(tsx_bin), script_path],
            cwd=str(wt_path),
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=timeout,
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
                "node_bin": node_note,
                "stderr": proc.stderr[-1000:],
                "stdout": proc.stdout[-400:],
            }
        payload = json.loads(line)
        if "error" in payload:
            return {
                "scenario": SCENARIO_NAME,
                "trials": trials,
                "node_bin": node_note,
                "error": payload["error"][:1000],
            }
        return {"scenario": SCENARIO_NAME, "node_bin": node_note, **payload}
    except subprocess.TimeoutExpired:
        return {
            "scenario": SCENARIO_NAME,
            "trials": trials,
            "error": f"probe timed out after {timeout}s",
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: without-fix 에서 crash 후 동일 entry 가 reconciliation 없이 재-deliver 되면 collected.
    deliverCount == 2 (PASS1 전송 + PASS2 재전송) + crash 후 pending 잔존 + recovery_state 가드 부재.
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    deliver_count = measurements.get("deliverCount")
    if deliver_count is None:
        return "blocked-env"
    pending_after_crash = measurements.get("pendingAfterCrash", 0)
    recovery_state = measurements.get("recoveryStateAfterCrash")
    if pending_after_crash < 1:
        return "blocked-env"
    if deliver_count == 2 and recovery_state in (None, "field-absent"):
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: without 중복 deliver(2), with recovery_state reconciliation 로 blind replay 거부(1)."""
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo = without_fix.get("deliverCount")
    wf = with_fix.get("deliverCount")
    if wo is None or wf is None:
        return "blocked-env"
    if wo == 2 and wf == 1:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, the session-delivery recovery queue blind-replays an unacked agent "
        "turn after a crash. drainQueuedEntry (session-delivery-queue-recovery.ts) runs "
        "`await deliver(entry)` then `ackSessionDelivery`. If the process is killed after deliver "
        "succeeds (the agent turn re-runs via dispatchAssembledChannelTurn and the platform reply "
        "is sent) but before ack, the entry stays pending. The next recovery re-delivers it "
        "unconditionally, re-running the turn and re-sending the reply. #88665 moved the delivery "
        "queues onto a shared SQLite backing (delivery-queue-sqlite.ts) that already carries a "
        "recovery_state column, but the session-delivery drain does not set or read it; the parallel "
        "outbound queue (outbound/delivery-queue-recovery.ts) does, reconciling actual send status "
        "before replay and refusing blind replay when it cannot confirm. With this patch the session "
        "drain wires the existing recovery_state column so it refuses blind replay of an already-sent "
        "turn, matching the outbound queue."
    )
    environment = (
        "macOS (darwin arm64), Node 24.x (node:sqlite StatementSync.columns), OpenClaw built from "
        "this branch. Isolated OPENCLAW_HOME + explicit OPENCLAW_STATE_DIR via the real-behavior-proof "
        "harness env isolation; the session-delivery queue now lives in the shared state SQLite DB. No "
        "external dependencies (no OAuth/LLM/channel calls) - the deliver callback only counts "
        "invocations. Production touched: none. The crash is modelled deterministically as "
        "deliver-success-then-ack-skip followed by a real recoverPendingSessionDeliveries restart "
        "pass (same production recovery code path); a real process SIGKILL/restart strengthens this "
        "in the post-sol run."
    )
    steps = (
        "```text\n"
        "$ node_modules/.bin/tsx <probe>.mts          (worktree-relative, isolated state dir, Node 24)\n"
        "  probe: enqueueSessionDelivery(agentTurn) ->\n"
        "         PASS 1 deliver(entry) success, SKIP ack   (crash-before-ack model) ->\n"
        "         PASS 2 recoverPendingSessionDeliveries()   (restart, real recovery path) ->\n"
        "         count deliver() invocations on the same entry\n"
        "```"
    )
    evidence = (
        "Live Node.js measurement of deliver() invocation count for one unacked agentTurn entry "
        "across a crash-before-ack then restart-recovery sequence (SQLite-backed session queue):\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  pendingAfterEnqueue={without_fix.get('pendingAfterEnqueue')} "
        f"pendingAfterCrash={without_fix.get('pendingAfterCrash')} "
        f"recoveryStateAfterCrash={without_fix.get('recoveryStateAfterCrash')}\n"
        f"  deliverCount={without_fix.get('deliverCount')}  recovered={without_fix.get('recovered')} "
        f"pendingAfterRecover={without_fix.get('pendingAfterRecover')}\n"
        "  -> same entry delivered twice (blind replay): turn re-run + reply re-sent.\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  pendingAfterEnqueue={with_fix.get('pendingAfterEnqueue')} "
        f"pendingAfterCrash={with_fix.get('pendingAfterCrash')} "
        f"recoveryStateAfterCrash={with_fix.get('recoveryStateAfterCrash')}\n"
        f"  deliverCount={with_fix.get('deliverCount')}  recovered={with_fix.get('recovered')} "
        f"pendingAfterRecover={with_fix.get('pendingAfterRecover')}\n"
        "  -> recovery sees the persisted recovery_state and refuses blind replay: delivered once.\n"
        "```"
    )
    observed = (
        f"With the patch, deliver() is invoked {with_fix.get('deliverCount')}x for the unacked "
        f"agentTurn (vs {without_fix.get('deliverCount')}x without the patch). Wiring the existing "
        "shared recovery_state column into the session drain lets it refuse blind replay of an "
        "already-sent turn, matching the outbound queue's reconciliation guarantee."
    )
    not_tested = (
        "Real adapter-side reconcile round-trip (post-sol fix scope; this proof measures replay "
        "suppression via the persisted recovery_state). Real OS-level SIGKILL between deliver and ack "
        "(modelled here as ack-skip + restart pass; post-sol uses a real forked process restart). "
        "Platform-side message dedup inside dispatchAssembledChannelTurn (turn/kernel path out of "
        "scope)."
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
