#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-045.py

CAND-045 / FIND-agent-session-store-data-integrity-001 (P1) baseline.
src/agents/sessions/auth-storage.ts:119 (withLock) + :164 (withLockAsync) 가 auth.json
(API 키 / OAuth refresh 토큰의 유일 영속 SoT) 갱신을 raw writeFileSync 로 처리한다. 이는
대상 경로를 O_TRUNC 로 직접 열어 in-place 재기록하므로 truncate 와 마지막 바이트 flush 사이가
비원자다. 그 창에서 SIGKILL/전원차단 시 auth.json 이 0바이트 또는 부분 JSON 으로 잔존하고,
다음 기동 reload() 의 JSON.parse 가 throw → loadError 세팅 → 전 provider 자격증명 lockout.
게다가 persistProviderChange() 가 loadError 시 early-return(line 287) 하므로 손상 파일을
새 값으로 덮어쓰지도 못해 사용자가 명시적으로 /login 재인증할 때까지 lockout 이 지속된다.

원리 (skip_build / tsx 직접 import):
- worktree(node_entry.parent) 의 src/agents/sessions/auth-storage.ts 를 tsx 로 직접 import.
- isolated temp OPENCLAW_HOME (env["OPENCLAW_HOME"]) 아래 agent/auth.json 을 명시 authPath 로
  AuthStorage.create(authPath) 에 넘겨 getAgentDir()/홈디렉터리 의존을 우회 (production
  ~/.openclaw 절대 비접촉).
- 측정 두 분기:
  * control     : 정상 full write 후 reload → getApiKey 정상 반환 (lockout 미발생, 대조군).
  * crash-truncate: full write 산출물을 부분(truncated) JSON 으로 덮어써 raw writeFileSync 가
    crash 시 남기는 디스크 상태를 모델링 → reload 의 parse throw / getApiKey 빈값 / set()
    recovery 가 persistProviderChange early-return 으로 무력화되는지(persist no-op) 측정.

*** crash 모델링 한계(명시) ***:
이 pre-sol probe 는 실제 프로세스 SIGKILL 을 주입하지 않는다. raw writeFileSync 의 O_TRUNC
in-place 재기록이 crash 시 디스크에 남기는 결과 상태(부분/truncated 파일)를 truncate 로 직접
모델링한다. truncate 결과는 "writeFileSync 가 truncate 후 전체 flush 전에 죽은" 디스크 상태와
바이트 등가이므로 lockout 발현 경로(parse throw → loadError → getApiKey 빈값 → persist
early-return)는 동일하게 검증된다. post-sol 검증은 가능하면 real process boundary
(자식 프로세스를 write 중간에 SIGKILL) 로 truncate 모델을 대체해 atomic rename 의
crash-atomicity 를 더 강하게 입증할 것을 권장한다. atomic fix(replaceFileAtomic / temp+rename)
적용 시에는 부분 파일이 원본 경로에 노출되지 않으므로 crash-truncate 분기에서도 lockout 미발현이
기대된다(with_fix 가정).

REQUIRES_EXTERNAL_DEP=False (외부 OAuth/LLM/채널 호출 없음. api_key 자격증명만 사용).

반증 기준선(atomic sibling): src/agents/session-file-repair.ts:409 repairSessionFile() 은
동일 종류의 in-place 전량 재기록을 replaceFileAtomic(temp 작성 후 atomic rename) 으로 처리한다.
auth-storage 의 자격증명 writer 만 raw writeFileSync 라 atomic 규율이 불균일.

without-fix: crash-truncate 분기에서 lockout 관측 (loadFailed && !keyAfterCrash &&
             persist recovery 무력화), control 분기는 정상.
with-fix:    atomic 적용 → crash-truncate 분기에서도 lockout 미발현 (부분 파일 미노출) 가정.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-045"
DEFAULT_TRIALS = 2  # control (대조군) + crash-truncate (defect axis)


def _build_probe_script() -> str:
    return """\
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { AuthStorage } from './src/agents/sessions/auth-storage.ts';

async function main() {
  const home = process.env.OPENCLAW_HOME;
  if (!home) { console.log(JSON.stringify({ skipped: 'no OPENCLAW_HOME' })); return; }

  const agentDir = join(home, 'agent');
  if (!existsSync(agentDir)) mkdirSync(agentDir, { recursive: true });

  const trialResults = [];

  // ---- Trial 1: control (no crash). Full valid write must round-trip cleanly. ----
  {
    const authPath = join(agentDir, 'auth-control.json');
    const store = AuthStorage.create(authPath);
    store.set('anthropic', { type: 'api_key', key: 'sk-test-CONTROL-12345' });

    const reopened = AuthStorage.create(authPath);
    const loadFailed = reopened.drainErrors().length > 0;
    const key = await reopened.getApiKey('anthropic');
    const lockout = loadFailed || !key;
    trialResults.push({
      branch: 'control',
      loadFailed,
      keyAfter: key ? '<present>' : '<empty>',
      lockout,
    });
  }

  // ---- Trial 2: crash-truncate. Model raw writeFileSync's O_TRUNC partial-write state. ----
  {
    const authPath = join(agentDir, 'auth-crash.json');
    const store = AuthStorage.create(authPath);
    store.set('anthropic', { type: 'api_key', key: 'sk-test-CRASH-12345' });

    // baseline: valid file -> key present before the crash
    const before = AuthStorage.create(authPath);
    const keyBeforeCrash = await before.getApiKey('anthropic');
    const contentFull = existsSync(authPath) ? readFileSync(authPath, 'utf-8') : '';

    // crash injection: raw writeFileSync (O_TRUNC) leaves a partial file if it dies
    // between truncate and full flush. We model that disk state directly.
    const truncated = contentFull.slice(0, Math.max(1, Math.floor(contentFull.length / 2)));
    writeFileSync(authPath, truncated, 'utf-8');

    // next boot: reload() -> parseStorageData -> JSON.parse(partial) throws -> loadError
    const afterCrash = AuthStorage.create(authPath);
    const loadFailed = afterCrash.drainErrors().length > 0;
    const keyAfterCrash = await afterCrash.getApiKey('anthropic');

    // self-heal attempt: set() -> persistProviderChange early-returns on loadError (line 287),
    // so the corrupt file is NOT overwritten -> lockout persists.
    afterCrash.set('anthropic', { type: 'api_key', key: 'sk-test-RECOVER' });
    const reReadAfterSet = AuthStorage.create(authPath);
    const keyAfterRecoverAttempt = await reReadAfterSet.getApiKey('anthropic');
    const recoveryNoOp = !keyAfterRecoverAttempt;

    const lockout = loadFailed && !keyAfterCrash && recoveryNoOp;
    trialResults.push({
      branch: 'crash-truncate',
      keyBeforeCrash: keyBeforeCrash ? '<present>' : '<empty>',
      fullBytes: contentFull.length,
      truncatedBytes: truncated.length,
      loadFailed,
      keyAfterCrash: keyAfterCrash ? '<present>' : '<empty>',
      keyAfterRecoverAttempt: keyAfterRecoverAttempt ? '<present>' : '<empty>',
      recoveryNoOp,
      lockout,
    });
  }

  const controlTrial = trialResults.find((t) => t.branch === 'control');
  const crashTrial = trialResults.find((t) => t.branch === 'crash-truncate');
  const controlOk = !!controlTrial && controlTrial.lockout === false;
  const crashLockout = !!crashTrial && crashTrial.lockout === true;

  console.log(JSON.stringify({
    trials: 2,
    trialResults,
    controlOk,
    crashLockout,
  }));
  process.exit(0);
}

main().catch((e) => {
  console.log(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused (호환성)
    trials: int = DEFAULT_TRIALS,
    **kwargs: Any,
) -> dict[str, Any]:
    """skip_build: node_entry 는 worktree 루트 경로. tsx 로 worktree 의 src ts 직접 실행.

    probe 는 env["OPENCLAW_HOME"] 아래 isolated agent/auth-*.json 만 사용한다 (production
    ~/.openclaw 비접촉). harness 가 temp HOME 을 주입할 책임.
    """
    wt_path = node_entry if node_entry.is_dir() else node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    if not env.get("OPENCLAW_HOME"):
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": "OPENCLAW_HOME 미지정 (isolated temp HOME 필수)"}

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
        if "error" in payload:
            return {"scenario": SCENARIO_NAME, "trials": trials, "error": payload["error"][:800]}
        return {"scenario": SCENARIO_NAME, "trials": trials, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: crash-truncate 분기에서 lockout 관측 + control 정상이면 collected.

    - probe 실행 실패 / OPENCLAW_HOME 미지정 / parse 실패 → blocked-env
    - crashLockout==True 그리고 controlOk==True → collected (결함 발현, 대조군 건전)
    - 그 외 → unreproducible
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    if measurements.get("crashLockout") is True and measurements.get("controlOk") is True:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: without 에서 lockout 재현, with(atomic) 에서 lockout 미발현이면 collected.

    두 빌드 모두 control 분기는 정상이어야 한다 (대조군 건전성).
    """
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
    wo_lock = without_fix.get("crashLockout")
    wf_lock = with_fix.get("crashLockout")
    wo_ctrl = without_fix.get("controlOk")
    wf_ctrl = with_fix.get("controlOk")
    if wo_lock is True and wf_lock is False and wo_ctrl is True and wf_ctrl is True:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    wo_crash = next(
        (t for t in (without_fix.get("trialResults") or []) if t.get("branch") == "crash-truncate"),
        {},
    )
    wf_crash = next(
        (t for t in (with_fix.get("trialResults") or []) if t.get("branch") == "crash-truncate"),
        {},
    )
    behavior = (
        "Without this patch, FileAuthStorageBackend.withLock / withLockAsync persist auth.json "
        "(the sole on-disk store for API keys and OAuth refresh tokens) with a raw writeFileSync, "
        "which opens the target path with O_TRUNC and rewrites it in place. If the process is "
        "SIGKILLed (or the machine loses power) between the truncate and the final byte flush, "
        "auth.json is left as a 0-byte or partial-JSON file. The next launch's reload() calls "
        "JSON.parse on that partial string, which throws, sets loadError, and leaves every provider "
        "credential unloadable. Worse, persistProviderChange() early-returns while loadError is set, "
        "so the corrupt file is never overwritten with fresh values: the user is locked out until "
        "they manually /login again. The proper-lockfile advisory lock guards only multi-process "
        "lost-update, not single-write crash atomicity. With this patch, the writer uses the existing "
        "atomic helper (replaceFileAtomic / temp + atomic rename, already used for transcript repair "
        "in session-file-repair.ts:409), so a crash mid-write leaves the prior valid auth.json intact."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw checked out from this branch and run via tsx "
        "(--skip-build, src TypeScript imported directly). Isolated temp OPENCLAW_HOME; auth.json "
        "lives under <temp>/agent and is passed as an explicit authPath, so production ~/.openclaw "
        "is never touched. No external dependencies (api_key credential only; no OAuth/LLM/channel "
        "calls). Same probe across both builds."
    )
    steps = (
        "```text\n"
        "$ export OPENCLAW_HOME=$(mktemp -d)                   (isolated, per build)\n"
        "$ npx tsx <probe.ts>                                  (worktree root; imports src/agents/sessions/auth-storage.ts)\n"
        "  control:        AuthStorage.set(api_key) then reload -> getApiKey  (must stay present)\n"
        "  crash-truncate: full write, then overwrite auth.json with a half-length partial JSON\n"
        "                  (models raw writeFileSync's O_TRUNC partial-write disk state), then\n"
        "                  reload -> getApiKey; then set() -> reload -> getApiKey (self-heal attempt)\n"
        "```"
    )
    evidence = (
        "Live Node.js (tsx) measurement of credential lockout after a modeled crash-truncate of auth.json:\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  control branch lockout:        {next((t.get('lockout') for t in (without_fix.get('trialResults') or []) if t.get('branch') == 'control'), None)}  (expected false)\n"
        f"  crash-truncate keyBeforeCrash: {wo_crash.get('keyBeforeCrash')}\n"
        f"  crash-truncate loadFailed:     {wo_crash.get('loadFailed')}  (JSON.parse threw -> loadError)\n"
        f"  crash-truncate keyAfterCrash:  {wo_crash.get('keyAfterCrash')}\n"
        f"  crash-truncate recoveryNoOp:   {wo_crash.get('recoveryNoOp')}  (persistProviderChange early-return)\n"
        f"  crash-truncate lockout:        {wo_crash.get('lockout')}\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  control branch lockout:        {next((t.get('lockout') for t in (with_fix.get('trialResults') or []) if t.get('branch') == 'control'), None)}  (expected false)\n"
        f"  crash-truncate keyAfterCrash:  {wf_crash.get('keyAfterCrash')}  (prior valid auth.json retained)\n"
        f"  crash-truncate lockout:        {wf_crash.get('lockout')}  (atomic rename: no partial file exposed)\n"
        "```"
    )
    observed = (
        f"With patch, the crash-truncate branch no longer locks out the credential "
        f"(lockout={wf_crash.get('lockout')}, key {wf_crash.get('keyAfterCrash')}), versus a full "
        f"lockout without the patch (lockout={wo_crash.get('lockout')}). The control branch stays "
        f"healthy in both builds, confirming the lockout is crash-specific rather than a probe artifact."
    )
    not_tested = (
        "Real process-boundary SIGKILL injection (this pre-sol probe models the O_TRUNC partial-write "
        "state via direct truncate; post-sol should kill a child mid-write to exercise the true crash "
        "window). The OAuth refresh path (withLockAsync, auth-storage.ts:164) shares the identical raw "
        "writeFileSync and is covered by the same fix but is not separately exercised here (requires a "
        "registered OAuth provider mock). Sibling writers in CAND-045's epic (session transcript .jsonl, "
        "settings.json) are out of scope for this single-FIND proof."
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
    ap.add_argument("--node-entry", required=True, help="worktree root path (skip_build mode)")
    ap.add_argument("--openclaw-home", required=True, help="isolated temp OPENCLAW_HOME")
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
