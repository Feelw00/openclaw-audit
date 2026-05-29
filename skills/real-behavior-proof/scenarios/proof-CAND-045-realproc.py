#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-045-realproc.py

CAND-045 / FIND-agent-session-store-data-integrity-001 (P1) real-process-boundary proof.
SOL-0014 (auth.json atomic write) 의 post-sol 전용.

이 시나리오는 proof-CAND-045.py 의 "authPath 직접 truncate" 모델 한계를 대체한다. 기존 모델은
probe 가 authPath 를 직접 truncate 하므로 fix(replaceFileAtomicSync = temp + rename)와 무관하게
raw/atomic 양쪽이 동일 lockout → with/without 구분 불가(post-sol blocked-env). 여기서는 진짜
자식 Node 프로세스를 write 진행 중에 SIGKILL 해 raw 와 atomic 의 crash 디스크 상태를 실제로
가른다:

- raw writeFileSync(authPath, big):
  open(authPath, O_TRUNC) 가 write 시작 즉시 authPath 를 0 바이트로 만든 뒤 큰 payload write
  진행 중 → SIGKILL → authPath 가 truncate/partial 로 잔존 → 다음 reload 의 JSON.parse throw →
  loadError → getApiKey 빈값 → persistProviderChange early-return → lockout.
- replaceFileAtomicSync({ filePath: authPath, content: big, flag "wx", rename }):
  authPath 는 미접촉, sibling temp 파일에만 write. rename 전 SIGKILL → authPath = 옛 seed 그대로
  (또는 직전 rename 으로 커밋된 valid JSON) → reload 정상 → lockout 미발현.

원리 (skip_build / tsx 직접 실행, REQUIRES_EXTERNAL_DEP=False):
- worktree(node_entry) 의 src/agents/sessions/auth-storage.ts 를 자식 Node(tsx)가 직접 import.
- isolated OPENCLAW_HOME(env 제공) 아래 trial 별 디렉터리에 명시 authPath 로 AuthStorage 사용
  (getAgentDir/홈디렉터리 의존 우회 → production ~/.openclaw 절대 비접촉).

한 trial:
1. seed: AuthStorage.create(authPath).set(seedProvider, 작은 valid api_key) 로 valid auth.json
   완전 커밋. 부모가 seed 가 디스크에 valid 함을 reopen 으로 사전 확인.
2. 자식 spawn(tsx writer.ts): 자식이 동일 authPath 의 AuthStorage 로 seedProvider 키에 큰
   payload(수 MB 문자열 — write 가 수십~수백 ms 지속)를 set() 으로 반복 persist. set() 루프
   진입 직전 stdout 에 sentinel("BEFORE_SET") 출력 + flush.
3. 부모: sentinel 수신 후 짧은 지연(kill_delay_ms; open 은 끝나고 write 진행 중) 뒤 SIGKILL.
4. kill 후 새 AuthStorage 로 reload → getApiKey(seedProvider) 살아있나 + on-disk authPath
   검사(존재/크기/parse 가능). loadError 또는 빈 키 → lockout.

*** 타이밍 견고화 (per-trial 재시도) ***
실측 결과 macOS/APFS 에서 단일 writeFileSync(문자열) 은 page cache 로의 단일 write() 로
완료되어 signal 전달(SIGKILL/SIGSTOP)보다 빨리 끝나는 경우가 잦다. 즉 한 번의 kill 시도가
write 진행 중(부분 파일 잔존)을 잡을 확률은 ~10% 수준이다. 그래서 한 trial 은 한 번의 kill 이
아니라, **새 seed 위에서 spawn+SIGKILL 을 attempts_per_trial 회까지 반복**해 그 중 한 번이라도
on-disk auth.json 을 손상(부분/빈/parse 실패/키 부재) 상태로 얼리면 그 trial 을 lockout 으로
센다. raw 는 부분 write 가 디스크에 노출될 수 있으므로 여러 시도 중 거의 항상 한 번은 손상을
잡지만(높은 trial lockout_rate), atomic 은 temp+rename 구조상 authPath 가 절대 부분 노출되지
않아 attempts 를 아무리 늘려도 0 이다 (구조적 0). 측정은 lockout_trials/lockout_rate(권위) +
attempt 단위 raw freeze_count/attempts_total(per-attempt 빈도) 둘 다 보고한다.

손상 판정은 부모(Python)가 on-disk 파일을 직접 읽어 (parse + seed provider 의 api_key 존재)
판정한다 — SOL-0014 가 닫는 결함이 바로 "on-disk auth.json 의 truncate/partial 손상" 이며,
proper-lockfile advisory lock(SIGKILL 된 holder 가 남기는 stale lock)은 raw/atomic 양쪽에서
동일한 fix-blind 직교 artifact 라 파일 바이트 판정에는 무관하다(파일 읽기는 lock 무관).

without-fix(base sha): lockout_rate > 0 (raw O_TRUNC partial 잔존을 여러 시도 중 포착).
with-fix(head sha): lockout_rate == 0 (atomic rename, authPath 미손상 — 구조적).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-045-realproc"
DEFAULT_TRIALS = 20
# 한 trial 당 spawn+SIGKILL 시도 횟수. 단일 시도의 partial-freeze 확률(~10%)을 흡수해
# raw trial 의 lockout 을 견고하게 만든다 (1-0.9^25 ~ 93%). atomic 은 시도 수 무관 0.
DEFAULT_ATTEMPTS_PER_TRIAL = 25

SEED_PROVIDER = "anthropic"
SEED_KEY = "sk-test-SEED-REALPROC-12345"
SENTINEL = "BEFORE_SET"
# big payload size (chars). 큰 string 이라 한 writeFileSync 가 수십~수백 ms 지속 → kill 창 확보.
DEFAULT_PAYLOAD_BYTES = 6_000_000
# sentinel 수신 후 SIGKILL 까지 지연(ms). open(O_TRUNC) 직후 write 진행 중을 노린다.
DEFAULT_KILL_DELAY_MS = 60


def _writer_script() -> str:
    """자식 Node(tsx) writer. authPath 의 AuthStorage 로 seedProvider 에 큰 payload 반복 persist.

    set() 루프 진입 직전 sentinel 출력 + flush. 부모가 그 직후 SIGKILL.
    정상 종료(부모가 못 죽임)는 측정상 비정상이지만 timeout 으로 회수.
    """
    return """\
import { AuthStorage } from './src/agents/sessions/auth-storage.ts';

function die(msg) {
  process.stderr.write('WRITER_ERR ' + msg + '\\n');
  process.exit(3);
}

async function main() {
  const authPath = process.env.PROBE_AUTH_PATH;
  const provider = process.env.PROBE_PROVIDER;
  const payloadBytes = Number(process.env.PROBE_PAYLOAD_BYTES || '0');
  if (!authPath || !provider || !payloadBytes) {
    die('missing PROBE_AUTH_PATH/PROBE_PROVIDER/PROBE_PAYLOAD_BYTES');
    return;
  }

  // Open the existing seeded store (must already hold a valid credential).
  const store = AuthStorage.create(authPath);

  // Build a multi-MB credential value so each persist's writeFileSync spends
  // tens-to-hundreds of ms flushing bytes. The parent SIGKILLs us mid-write.
  const big = 'B'.repeat(payloadBytes);

  // Signal the parent that we are about to enter the write loop, then flush so
  // the byte actually leaves our buffer before we start writing.
  process.stdout.write('""" + SENTINEL + """\\n');
  // Best-effort fsync of stdout is not available cross-platform; the newline +
  // synchronous nature of the following loop is enough for the parent to arm the kill.

  // Persist repeatedly. Each set() -> persistProviderChange -> withLock -> the
  // patched/unpatched write site. raw: open(authPath, O_TRUNC) then write big.
  // atomic: write temp (flag wx) then rename. The kill lands somewhere inside
  // this loop; for raw that means authPath is mid-truncate/partial.
  // We loop so the kill window is wide regardless of a single write's duration.
  for (;;) {
    store.set(provider, { type: 'api_key', key: big });
  }
}

main().catch((e) => die(String(e && e.stack ? e.stack : e)));
"""


def _seed_script() -> str:
    """authPath 에 seed 자격증명을 완전히 커밋하고 valid 함을 확인. JSON 1줄 출력.

    직전 attempt 의 SIGKILL 된 writer 가 남긴 stale advisory lock(<authPath>.lock) + 6MB
    잔여 파일을 먼저 제거하고 깨끗한 상태에서 seed 한다. set() 이 loadError 로 막히면
    lock 을 재차 비우고 재시도해 stale-lock 직교 artifact 를 흡수한다.
    """
    return """\
import { existsSync, readFileSync, rmSync } from 'node:fs';
import { AuthStorage } from './src/agents/sessions/auth-storage.ts';

function clearStale(authPath) {
  try { rmSync(authPath + '.lock', { recursive: true, force: true }); } catch {}
  try { rmSync(authPath, { force: true }); } catch {}
}
function sleepSync(ms) { const e = Date.now() + ms; while (Date.now() < e) {} }

async function main() {
  const authPath = process.env.PROBE_AUTH_PATH;
  const provider = process.env.PROBE_PROVIDER;
  const seedKey = process.env.PROBE_SEED_KEY;
  if (!authPath || !provider || !seedKey) {
    console.log(JSON.stringify({ error: 'missing seed env' }));
    process.exit(0);
  }

  let seeded = false, loadFailed = true, bytes = 0;
  for (let attempt = 0; attempt < 5 && !seeded; attempt++) {
    clearStale(authPath);  // remove stale lock + any leftover partial/big file -> clean slate
    const store = AuthStorage.create(authPath);
    store.set(provider, { type: 'api_key', key: seedKey });

    const reopened = AuthStorage.create(authPath);
    loadFailed = reopened.drainErrors().length > 0;
    const key = await reopened.getApiKey(provider);
    bytes = existsSync(authPath) ? readFileSync(authPath, 'utf-8').length : 0;
    seeded = !loadFailed && key === seedKey;
    if (!seeded) sleepSync(15);
  }
  console.log(JSON.stringify({ seeded, loadFailed, bytes }));
  process.exit(0);
}

main().catch((e) => {
  console.log(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(0);
});
"""


def _write_temp_script(wt_path: Path, body: str, prefix: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=".ts", prefix=prefix, mode="w", delete=False, dir=str(wt_path)
    ) as f:
        f.write(body)
        return f.name


def _run_tsx_capture(tsx_bin: Path, script_path: str, wt_path: Path, env: dict, timeout: int) -> dict:
    """tsx 동기 실행 후 마지막 stdout 라인 JSON 파싱."""
    proc = subprocess.run(
        [str(tsx_bin), script_path],
        cwd=str(wt_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    if not out:
        return {"error": f"no stdout (rc={proc.returncode})", "stderr": (proc.stderr or "")[:400]}
    try:
        return json.loads(out.splitlines()[-1])
    except Exception as e:
        return {"error": f"parse failed: {e}", "stdout": out[:400], "stderr": (proc.stderr or "")[:400]}


def _disk_integrity(auth_path: Path) -> dict[str, Any]:
    """on-disk auth.json 무결성 판정 (부모가 직접 읽음, proper-lockfile lock 무관).

    corrupt = 파일 없음 / parse 실패 / seed provider 의 api_key 부재. 이게 SOL-0014 결함이다.
    """
    if not auth_path.exists():
        return {"exists": False, "bytes": 0, "parseOk": False, "diskKeyPresent": False, "corrupt": True}
    try:
        raw = auth_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"exists": True, "bytes": -1, "parseOk": False, "diskKeyPresent": False, "corrupt": True}
    n = len(raw)
    try:
        parsed = json.loads(raw)
        parse_ok = True
    except Exception:
        parsed = None
        parse_ok = False
    cred = (parsed or {}).get(SEED_PROVIDER) if isinstance(parsed, dict) else None
    disk_key = bool(
        isinstance(cred, dict)
        and cred.get("type") == "api_key"
        and isinstance(cred.get("key"), str)
        and len(cred.get("key")) > 0
    )
    return {
        "exists": True,
        "bytes": n,
        "parseOk": parse_ok,
        "diskKeyPresent": disk_key,
        "corrupt": (not parse_ok) or (not disk_key),
    }


def _one_kill_attempt(
    *,
    tsx_bin: Path,
    wt_path: Path,
    env: dict,
    auth_path: Path,
    seed_script: str,
    writer_script: str,
    kill_delay_ms: int,
    writer_timeout_s: int,
) -> dict[str, Any]:
    """한 번의 crash 시도: seed → spawn writer → sentinel 후 SIGKILL → on-disk 판정."""
    # 0. 직전 시도의 SIGKILL 된 writer 가 남긴 stale advisory lock 제거 (raw/atomic 공통의
    #    fix-blind 직교 artifact). 없으면 다음 seed 의 AuthStorage.create 가 ELOCKED.
    lock_dir = Path(str(auth_path) + ".lock")
    if lock_dir.exists():
        import shutil as _shutil
        try:
            _shutil.rmtree(lock_dir, ignore_errors=True)
        except Exception:
            pass

    # 1. fresh seed (valid auth.json 완전 커밋)
    seed_res = _run_tsx_capture(tsx_bin, seed_script, wt_path, env, timeout=60)
    if not seed_res.get("seeded"):
        return {"attempt_error": "seed failed", "seed_res": seed_res}

    # 2. writer spawn (실제 process boundary)
    sentinel_seen = False
    killed = False
    writer_exit = None
    proc = subprocess.Popen(
        [str(tsx_bin), writer_script],
        cwd=str(wt_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        if line.strip() == SENTINEL:
            sentinel_seen = True
            time.sleep(kill_delay_ms / 1000.0)
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
                killed = True
        try:
            writer_exit = proc.wait(timeout=writer_timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            writer_exit = proc.wait(timeout=10)
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    integ = _disk_integrity(auth_path)
    integ.update({"sentinel_seen": sentinel_seen, "killed": killed, "writer_exit": writer_exit})
    return integ


def _run_one_trial(
    *,
    tsx_bin: Path,
    wt_path: Path,
    base_env: dict,
    trial_dir: Path,
    seed_script: str,
    writer_script: str,
    payload_bytes: int,
    kill_delay_ms: int,
    writer_timeout_s: int,
    attempts_per_trial: int,
) -> dict[str, Any]:
    """단일 trial = 독립 crash 시나리오. attempts_per_trial 회까지 spawn+SIGKILL 반복.

    한 시도라도 on-disk auth.json 을 손상 상태로 얼리면 trial=lockout. raw 는 여러 시도 중
    거의 항상 한 번은 partial 을 잡고, atomic 은 구조적으로 절대 손상되지 않는다(0).
    """
    trial_dir.mkdir(parents=True, exist_ok=True)
    auth_path = trial_dir / "auth.json"

    env = dict(base_env)
    env["PROBE_AUTH_PATH"] = str(auth_path)
    env["PROBE_PROVIDER"] = SEED_PROVIDER
    env["PROBE_SEED_KEY"] = SEED_KEY
    env["PROBE_PAYLOAD_BYTES"] = str(payload_bytes)

    attempts_used = 0
    freezes = 0  # 이 trial 안에서 partial-freeze 를 잡은 시도 수
    killed_count = 0
    sentinel_count = 0
    first_corrupt: dict[str, Any] | None = None
    last_integ: dict[str, Any] | None = None

    for _ in range(max(1, attempts_per_trial)):
        attempts_used += 1
        a = _one_kill_attempt(
            tsx_bin=tsx_bin,
            wt_path=wt_path,
            env=env,
            auth_path=auth_path,
            seed_script=seed_script,
            writer_script=writer_script,
            kill_delay_ms=kill_delay_ms,
            writer_timeout_s=writer_timeout_s,
        )
        if "attempt_error" in a:
            # seed 실패 등 — trial 자체를 에러로 처리
            return {"trial_error": a.get("attempt_error", "attempt error"), "attempt": a}
        last_integ = a
        if a.get("sentinel_seen"):
            sentinel_count += 1
        if a.get("killed"):
            killed_count += 1
        if a.get("corrupt"):
            freezes += 1
            if first_corrupt is None:
                first_corrupt = a
            break  # 이 trial 은 lockout 확정 — 조기 종료

    lockout = freezes > 0
    rep = first_corrupt or last_integ or {}
    return {
        "lockout": bool(lockout),
        "attempts_used": attempts_used,
        "attempts_cap": max(1, attempts_per_trial),
        "freezes": freezes,
        "killed_count": killed_count,
        "sentinel_count": sentinel_count,
        # 대표 on-disk 상태 (lockout 이면 첫 손상, 아니면 마지막 시도)
        "exists": rep.get("exists"),
        "bytes": rep.get("bytes"),
        "parseOk": rep.get("parseOk"),
        "diskKeyPresent": rep.get("diskKeyPresent"),
    }


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused (호환성)
    trials: int = DEFAULT_TRIALS,
    payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
    kill_delay_ms: int = DEFAULT_KILL_DELAY_MS,
    attempts_per_trial: int = DEFAULT_ATTEMPTS_PER_TRIAL,
    **kwargs: Any,
) -> dict[str, Any]:
    """skip_build: node_entry 는 worktree 루트. tsx 로 worktree 의 src ts 를 자식이 직접 실행.

    probe 는 env["OPENCLAW_HOME"] 아래 trial 별 디렉터리의 명시 authPath 만 사용 (production
    ~/.openclaw 비접촉). harness 가 temp HOME 주입 책임. 한 trial 은 attempts_per_trial 회까지
    spawn+SIGKILL 반복 (per-attempt partial-freeze 확률 ~10% 흡수).
    """
    wt_path = node_entry if node_entry.is_dir() else node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    home = env.get("OPENCLAW_HOME")
    if not home:
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": "OPENCLAW_HOME 미지정 (isolated temp HOME 필수)"}

    # kwargs 로 들어올 수 있는 harness 옵션 흡수 (sleep_seconds/timeout_seconds 미사용)
    writer_timeout_s = int(kwargs.get("timeout_seconds") or 90)

    seed_script = _write_temp_script(wt_path, _seed_script(), "p045seed_")
    writer_script = _write_temp_script(wt_path, _writer_script(), "p045writer_")

    trials_root = Path(home) / "agent" / "realproc-trials"

    trial_results: list[dict[str, Any]] = []
    try:
        for i in range(trials):
            trial_dir = trials_root / f"t{i:03d}"
            try:
                tr = _run_one_trial(
                    tsx_bin=tsx_bin,
                    wt_path=wt_path,
                    base_env=env,
                    trial_dir=trial_dir,
                    seed_script=seed_script,
                    writer_script=writer_script,
                    payload_bytes=payload_bytes,
                    kill_delay_ms=kill_delay_ms,
                    writer_timeout_s=writer_timeout_s,
                    attempts_per_trial=attempts_per_trial,
                )
            except subprocess.TimeoutExpired as e:
                tr = {"trial_error": f"timeout: {e}"}
            tr["trial"] = i
            trial_results.append(tr)
    finally:
        for p in (seed_script, writer_script):
            try:
                os.unlink(p)
            except Exception:
                pass

    valid = [t for t in trial_results if "trial_error" not in t]
    errored = [t for t in trial_results if "trial_error" in t]
    # 적어도 한 시도에서 writer 를 실제로 kill 한 trial (process boundary 실측 확인)
    killed_trials = [t for t in valid if (t.get("killed_count") or 0) > 0]
    lockouts = [t for t in valid if t.get("lockout")]

    measured = len(valid)
    lockout_count = len(lockouts)
    lockout_rate = (lockout_count / measured) if measured else 0.0

    # per-attempt raw partial-freeze 빈도 (참고 지표)
    attempts_total = sum(t.get("attempts_used", 0) for t in valid)
    freeze_count = sum(t.get("freezes", 0) for t in valid)
    per_attempt_freeze_rate = (freeze_count / attempts_total) if attempts_total else 0.0

    return {
        "scenario": SCENARIO_NAME,
        "trials": trials,
        "measured_trials": measured,
        "errored_trials": len(errored),
        "attempts_per_trial": attempts_per_trial,
        "attempts_total": attempts_total,
        "freeze_count": freeze_count,
        "per_attempt_freeze_rate": round(per_attempt_freeze_rate, 4),
        "killed_trials": len(killed_trials),
        "killed_during_write": len(killed_trials),  # _is_usable 호환 alias
        "lockout_count": lockout_count,
        "lockout_rate": round(lockout_rate, 4),
        "payload_bytes": payload_bytes,
        "kill_delay_ms": kill_delay_ms,
        "trial_results": trial_results,
    }


def _is_usable(m: dict[str, Any]) -> bool:
    """측정이 유효한가: error 없음 + 실제로 kill 된 trial 이 있고 측정 trial 이 있음."""
    if not m or m.get("trials", 0) == 0 or "error" in m:
        return False
    if m.get("measured_trials", 0) == 0:
        return False
    if m.get("killed_during_write", 0) == 0:
        return False
    return True


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: real-process SIGKILL 후 lockout_rate > 0 이면 collected.

    - error / kill 미발생 / 측정 0 → blocked-env
    - lockout_rate > 0 → collected
    - lockout_rate == 0 → unreproducible
    """
    if not _is_usable(measurements):
        return "blocked-env"
    return "collected" if measurements.get("lockout_rate", 0.0) > 0 else "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: without lockout_rate>0 AND with lockout_rate==0 이면 collected.

    - 어느 빌드든 측정 무효(_is_usable False) → blocked-env
    - without 에서 lockout 미발현 → unreproducible (baseline 결함 안 보임)
    - with 에서 lockout 잔존 → unreproducible (fix 효과 없음)
    - 둘 다 충족 → collected
    """
    for m in (without_fix, with_fix):
        if not _is_usable(m):
            return "blocked-env"
    wo = without_fix.get("lockout_rate", 0.0)
    wf = with_fix.get("lockout_rate", 0.0)
    if wo > 0 and wf == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    wo_n = without_fix.get("measured_trials")
    wf_n = with_fix.get("measured_trials")
    wo_lock = without_fix.get("lockout_count")
    wf_lock = with_fix.get("lockout_count")
    wo_rate = without_fix.get("lockout_rate")
    wf_rate = with_fix.get("lockout_rate")
    wo_att = without_fix.get("attempts_total")
    wf_att = with_fix.get("attempts_total")
    wo_freeze = without_fix.get("freeze_count")
    wf_freeze = with_fix.get("freeze_count")
    wo_peratt = without_fix.get("per_attempt_freeze_rate")
    wf_peratt = with_fix.get("per_attempt_freeze_rate")
    cap = without_fix.get("attempts_per_trial") or with_fix.get("attempts_per_trial")
    payload = without_fix.get("payload_bytes") or with_fix.get("payload_bytes")
    kdelay = without_fix.get("kill_delay_ms") or with_fix.get("kill_delay_ms")
    trials = without_fix.get("trials") or with_fix.get("trials")

    behavior = (
        "Without this patch, FileAuthStorageBackend.withLock / withLockAsync persist auth.json "
        "(the sole on-disk store for API keys and OAuth refresh tokens) with a raw writeFileSync, "
        "which opens the target path with O_TRUNC and rewrites it in place. If the process is "
        "SIGKILLed (or the machine loses power) after that O_TRUNC truncate and before the write "
        "completes, auth.json is left as a 0-byte or partial-JSON file. The next launch's reload() "
        "calls JSON.parse on that partial string, which throws, sets loadError, and leaves every "
        "provider credential unloadable; persistProviderChange() then early-returns while loadError "
        "is set, so the corrupt file is never overwritten and the user is locked out until they "
        "manually /login. With this patch both write sites use replaceFileAtomicSync (write to a "
        "sibling temp file with flag wx, then atomic rename), the same helper already used by "
        "session-file-repair.ts:409, so a SIGKILL before the rename leaves auth.json holding its "
        "prior valid contents and no partial auth.json is ever exposed."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw checked out at the base sha (raw write) and head "
        "sha (atomic write) and run via tsx (--skip-build, src TypeScript imported directly by a real "
        "child process). Isolated temp OPENCLAW_HOME; each trial's auth.json lives under <temp>/agent "
        "and is passed as an explicit authPath, so production ~/.openclaw is never touched. No external "
        "dependencies (api_key credential only; no OAuth/LLM/channel/network calls). Same probe across "
        "both builds."
    )
    steps = (
        "Each trial is an independent crash scenario driven through the production set() path. A trial "
        "re-seeds a valid auth.json, spawns a real child node (tsx) writer that persists a multi-MB "
        "credential, and the parent SIGKILLs that child shortly after a sentinel. Because a single "
        "writeFileSync often completes before signal delivery, the trial repeats the spawn+SIGKILL up to "
        "a cap until one attempt freezes auth.json mid-write (or the cap is reached). The parent then "
        "reads auth.json off disk and checks it parses and still holds the seed key. Same probe on both "
        "builds:\n\n"
        "| step | command / action |\n"
        "| --- | --- |\n"
        "| 1 isolate | export OPENCLAW_HOME=$(mktemp -d) (per build; production ~/.openclaw untouched) |\n"
        "| 2 seed | node_modules/.bin/tsx seed.ts -> AuthStorage.set(api_key) then reopen+getApiKey (valid) |\n"
        f"| 3 spawn | node_modules/.bin/tsx writer.ts -> child set() loop persists a {payload}-byte key; prints sentinel before the loop |\n"
        f"| 4 kill | parent waits {kdelay} ms after the sentinel, then sends SIGKILL while the write is in progress |\n"
        f"| 5 check | parent reads auth.json off disk: JSON.parse + seed api_key present? (lock-agnostic) |\n"
        f"| 6 retry | repeat steps 2-5 up to {cap} attempts per trial until one freezes auth.json corrupt |\n"
        f"| 7 repeat | {trials} trials per build -> lockout_count / lockout_rate (per-trial) |"
    )
    evidence = (
        "Live child-process SIGKILL measurement of credential lockout (real process boundary; the kill "
        "lands while the child's writeFileSync is in progress). lockout_rate is per-trial (a trial locks "
        "out if any attempt freezes auth.json corrupt); per_attempt_freeze_rate is the raw single-kill "
        "frequency:\n\n"
        "| build | sha role | trials | lockout_count | lockout_rate | attempts | freezes | per_attempt_freeze_rate |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        f"| A (without patch) | base (raw writeFileSync) | {wo_n} | {wo_lock} | {wo_rate} | {wo_att} | {wo_freeze} | {wo_peratt} |\n"
        f"| B (with patch) | head (replaceFileAtomicSync) | {wf_n} | {wf_lock} | {wf_rate} | {wf_att} | {wf_freeze} | {wf_peratt} |\n\n"
        "Build A: a SIGKILL that lands mid-write freezes auth.json after O_TRUNC zeroed it / before the "
        "rewrite completed, so the on-disk file is empty or partial JSON, the next reload's JSON.parse "
        "throws and the seed credential is gone -> lockout. Build B: the write always targets a sibling "
        "temp file (flag wx) and only an atomic rename publishes it, so a SIGKILL leaves auth.json holding "
        "its prior valid contents -> no corrupt auth.json is ever observed across every attempt of every "
        "trial -> lockout_rate 0 (structural, independent of attempt count)."
    )
    observed = (
        f"With the patch, no attempt across any trial ever leaves auth.json corrupt "
        f"(lockout_rate={wf_rate}, {wf_freeze}/{wf_att} attempts froze a partial file) versus a per-trial "
        f"lockout_rate of {wo_rate} ({wo_lock}/{wo_n} trials, {wo_freeze}/{wo_att} attempts froze a partial "
        f"file) without the patch. The atomic temp+rename keeps auth.json intact through the crash; the raw "
        f"O_TRUNC write can leave it truncated/partial."
    )
    not_tested = (
        f"Probabilistic timing (SIGKILL during write), trials={trials} with up to {cap} kill attempts each. "
        "A single writeFileSync of a buffered string often completes (into the page cache) before the "
        "SIGKILL is delivered, because signals land at syscall boundaries, so the per-attempt freeze rate "
        f"is modest (~{wo_peratt} for the raw build here); the proof relies on repeating the kill until one "
        "attempt catches the O_TRUNC partial window. The atomic build is a structural zero regardless of "
        "attempt count (auth.json is never the write target before the rename). Real power-loss (which "
        "also discards the page cache, widening the corruption window) is not reproduced. The OAuth refresh "
        "path (withLockAsync, auth-storage.ts:164) shares the identical raw writeFileSync and the identical "
        "fix but is not separately exercised here (would require a registered OAuth provider). Sibling "
        "writers in the original CAND-045 cluster (session transcript .jsonl, settings.json) are out of "
        "scope for this single-FIND proof. macOS/APFS only; the rename atomicity guarantee is POSIX-level "
        "and not separately re-verified on other platforms."
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
    ap.add_argument("--payload-bytes", type=int, default=DEFAULT_PAYLOAD_BYTES)
    ap.add_argument("--kill-delay-ms", type=int, default=DEFAULT_KILL_DELAY_MS)
    ap.add_argument("--attempts-per-trial", type=int, default=DEFAULT_ATTEMPTS_PER_TRIAL)
    args = ap.parse_args()

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        payload_bytes=args.payload_bytes,
        kill_delay_ms=args.kill_delay_ms,
        attempts_per_trial=args.attempts_per_trial,
    )
    print(json.dumps(out, indent=2))
