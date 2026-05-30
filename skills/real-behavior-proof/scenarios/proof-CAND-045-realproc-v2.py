#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-045-realproc-v2.py

CAND-045 / FIND-agent-session-store-data-integrity-001 (P1) real-process-boundary proof, v2.
SOL-0014 (auth.json atomic write) 의 post-sol 전용.

v1(proof-CAND-045-realproc.py) 한계 회고:
- writer 가 `for(;;) store.set()` 무한루프 + sentinel 후 고정 60ms 뒤 SIGKILL.
- macOS/APFS 에서 한 writeFileSync 의 O_TRUNC->write partial 창이 sub-ms~수ms 인데, 고정 delay 가
  그 창이 아니라 lock 획득 / JSON.stringify / Buffer 인코딩 구간에 떨어져 raw freeze=0/100.
- 무한루프라 orchestrator 중단 시 자식 node 가 무한 증식(210개 발생).

v2 설계 (사용자 결정: watch-then-kill 우선 + ulimit fallback):
1. writer 는 **단일 set() 1회 후 자가 종료** (for(;;) 제거). 부모가 못 죽여도 자식이 스스로 끝나
   구조적 runaway 0.
2. payload 를 대용량(기본 64MB)으로 키워 한 번의 writeFileSync 의 write 창을 수십~수백 ms 확보.
3. mode="watch-kill" (기본): 부모가 trial 디렉터리를 0.5ms 간격 폴링하다 "write 진행 중" 순간
   즉시 SIGKILL.
   - raw build: writeFileSync(authPath, O_TRUNC) -> authPath 가 seed 크기를 벗어남(0->grow) -> 즉시 kill
     -> authPath 가 partial 로 얼어 JSON.parse 실패 -> lockout.
   - atomic build: replaceFileAtomicSync 가 sibling temp(`auth.json.<pid>.<uuid>.tmp`, flag wx)에만 write
     -> authPath 무접촉(seed 크기 유지). 부모는 temp 출현을 보고 kill. authPath = seed 그대로 -> lockout 미발현.
4. mode="ulimit" (fallback): 자식에 RLIMIT_FSIZE(기본 1MB) 부과 + payload(기본 6MB) > limit.
   write 가 한도 초과 시 커널이 SIGXFSZ(기본 terminate) 또는 write EFBIG -> 자식 중단. 타이밍 무관 결정적.
   - raw: authPath 가 한도 바이트까지 partial -> corrupt.
   - atomic: temp 만 한도까지 partial, authPath 무접촉 -> 정상.

손상 판정은 부모(Python)가 on-disk authPath 를 직접 읽어 (JSON.parse + seed provider 의 api_key 존재)
판정한다. proper-lockfile advisory lock(SIGKILL 된 holder 의 stale lock)은 raw/atomic 공통의 fix-blind
직교 artifact 라 파일 바이트 판정에는 무관(파일 읽기는 lock 무관). 매 attempt 시작 시 stale lock + 잔여
temp + 잔여 authPath 를 청소해 clean slate 에서 seed.

skip_build / tsx 직접 실행, REQUIRES_EXTERNAL_DEP=False. isolated OPENCLAW_HOME 아래 trial 별 디렉터리에
명시 authPath 사용 -> production ~/.openclaw 절대 비접촉.

without-fix(base 5fbeffd56b): lockout_rate > 0.
with-fix  (head 46a1a9478e): lockout_rate == 0 (구조적, authPath 가 write 대상이 된 적 없음).
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = False
SCENARIO_NAME = "proof-CAND-045-realproc-v2"
DEFAULT_TRIALS = 20
# watch-kill 은 O_TRUNC 순간 잡으므로 catch 율이 높아 attempts 가 적어도 됨.
DEFAULT_ATTEMPTS_PER_TRIAL = 5

SEED_PROVIDER = "anthropic"
SEED_KEY = "sk-test-SEED-REALPROC-12345"
SENTINEL_BEFORE = "BEFORE_SET"
SENTINEL_AFTER = "AFTER_SET"

# watch-kill 기본: 대용량 payload 로 write 창 확보 (64MB).
DEFAULT_PAYLOAD_BYTES = 64 * 1024 * 1024
# ulimit fallback 기본: payload(6MB) > fsize-limit(1MB) 로 한도 초과 강제.
DEFAULT_ULIMIT_PAYLOAD_BYTES = 6 * 1024 * 1024
DEFAULT_FSIZE_LIMIT_BYTES = 1 * 1024 * 1024

DEFAULT_POLL_MS = 0.5          # 부모 폴링 간격 (ms)
DEFAULT_MAX_WAIT_MS = 8000     # 한 attempt 에서 write 진행을 감지할 최대 대기 (ms)


def _writer_script() -> str:
    """자식 Node(tsx) writer. authPath 의 AuthStorage 로 seedProvider 에 큰 payload 를 **단일 set()** 1회.

    set() 진입 직전 sentinel(BEFORE_SET) 출력. set() 은 동기(withLock)라 이벤트 루프를 블록하며
    그 사이 부모가 SIGKILL. 정상 완료되면 AFTER_SET 출력 후 자가 종료(루프 없음 -> runaway 0).
    """
    return (
        "import { AuthStorage } from './src/agents/sessions/auth-storage.ts';\n"
        "function emit(t){ try { process.stdout.write(t + '\\n'); } catch {} }\n"
        "function die(msg){ try { process.stderr.write('WRITER_ERR ' + msg + '\\n'); } catch {} process.exit(3); }\n"
        "async function main(){\n"
        "  const authPath = process.env.PROBE_AUTH_PATH;\n"
        "  const provider = process.env.PROBE_PROVIDER;\n"
        "  const payloadBytes = Number(process.env.PROBE_PAYLOAD_BYTES || '0');\n"
        "  if (!authPath || !provider || !payloadBytes) { die('missing PROBE env'); return; }\n"
        "  const store = AuthStorage.create(authPath);\n"
        "  // multi-MB credential value. each persist's writeFileSync spends real time flushing bytes.\n"
        "  const big = 'B'.repeat(payloadBytes);\n"
        "  emit('" + SENTINEL_BEFORE + "');\n"
        "  // single synchronous persist. raw: writeFileSync(authPath, O_TRUNC) then write big.\n"
        "  //                          atomic: writeFileSync(temp, flag wx) then rename.\n"
        "  // the parent SIGKILLs us mid-write; if it completes we exit cleanly (no loop).\n"
        "  store.set(provider, { type: 'api_key', key: big });\n"
        "  // persistProviderChange swallows write errors into recordError(); surface the count so the\n"
        "  // parent can confirm the ulimit actually cut the write (EFBIG) rather than a no-op.\n"
        "  const errs = store.drainErrors();\n"
        "  emit('PERSIST_ERR=' + errs.length);\n"
        "  emit('" + SENTINEL_AFTER + "');\n"
        "  process.exit(0);\n"
        "}\n"
        "main().catch((e) => die(String(e && e.stack ? e.stack : e)));\n"
    )


def _seed_script() -> str:
    """authPath 에 작은 valid seed 자격증명을 완전 커밋하고 valid 함을 확인. JSON 1줄 출력."""
    return (
        "import { existsSync, readFileSync, rmSync } from 'node:fs';\n"
        "import { AuthStorage } from './src/agents/sessions/auth-storage.ts';\n"
        "function clearStale(authPath) {\n"
        "  try { rmSync(authPath + '.lock', { recursive: true, force: true }); } catch {}\n"
        "  try { rmSync(authPath, { force: true }); } catch {}\n"
        "}\n"
        "function sleepSync(ms) { const e = Date.now() + ms; while (Date.now() < e) {} }\n"
        "async function main() {\n"
        "  const authPath = process.env.PROBE_AUTH_PATH;\n"
        "  const provider = process.env.PROBE_PROVIDER;\n"
        "  const seedKey = process.env.PROBE_SEED_KEY;\n"
        "  if (!authPath || !provider || !seedKey) { console.log(JSON.stringify({ error: 'missing seed env' })); process.exit(0); }\n"
        "  let seeded = false, loadFailed = true, bytes = 0;\n"
        "  for (let attempt = 0; attempt < 5 && !seeded; attempt++) {\n"
        "    clearStale(authPath);\n"
        "    const store = AuthStorage.create(authPath);\n"
        "    store.set(provider, { type: 'api_key', key: seedKey });\n"
        "    const reopened = AuthStorage.create(authPath);\n"
        "    loadFailed = reopened.drainErrors().length > 0;\n"
        "    const key = await reopened.getApiKey(provider);\n"
        "    bytes = existsSync(authPath) ? readFileSync(authPath, 'utf-8').length : 0;\n"
        "    seeded = !loadFailed && key === seedKey;\n"
        "    if (!seeded) sleepSync(15);\n"
        "  }\n"
        "  console.log(JSON.stringify({ seeded, loadFailed, bytes }));\n"
        "  process.exit(0);\n"
        "}\n"
        "main().catch((e) => { console.log(JSON.stringify({ error: String(e && e.stack ? e.stack : e) })); process.exit(0); });\n"
    )


def _write_temp_script(wt_path: Path, body: str, prefix: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=".ts", prefix=prefix, mode="w", delete=False, dir=str(wt_path)
    ) as f:
        f.write(body)
        return f.name


def _run_tsx_capture(tsx_bin: Path, script_path: str, wt_path: Path, env: dict, timeout: int) -> dict:
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


def _clear_trial_dir(trial_dir: Path, auth_path: Path) -> None:
    """attempt 시작 전 청소: stale lock + 잔여 temp + 잔여 authPath 제거 (clean slate)."""
    import shutil
    lock_dir = Path(str(auth_path) + ".lock")
    if lock_dir.exists():
        shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        auth_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    # leftover atomic temp siblings: auth.json.<pid>.<uuid>.tmp
    for p in trial_dir.glob("auth.json.*.tmp"):
        try:
            p.unlink()
        except OSError:
            pass


def _temp_in_progress(trial_dir: Path) -> bool:
    """atomic build 의 진행 중 temp(`auth.json.*.tmp`) 존재 여부."""
    for _ in trial_dir.glob("auth.json.*.tmp"):
        return True
    return False


def _disk_integrity(auth_path: Path) -> dict[str, Any]:
    """on-disk authPath 무결성 판정 (부모가 직접 읽음, proper-lockfile lock 무관).

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


def _make_fsize_preexec(limit_bytes: int):
    """ulimit mode: 자식에 RLIMIT_FSIZE 부과 (자식 생성 직전 실행)."""
    def _set_limit() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit_bytes, limit_bytes))
    return _set_limit


def _one_kill_attempt(
    *,
    tsx_bin: Path,
    wt_path: Path,
    env: dict,
    trial_dir: Path,
    auth_path: Path,
    seed_script: str,
    writer_script: str,
    mode: str,
    fsize_limit: int,
    poll_s: float,
    max_wait_s: float,
    writer_timeout_s: int,
) -> dict[str, Any]:
    """한 번의 crash 시도: clean -> seed -> spawn writer -> (watch-kill | ulimit) -> on-disk 판정."""
    _clear_trial_dir(trial_dir, auth_path)

    seed_res = _run_tsx_capture(tsx_bin, seed_script, wt_path, env, timeout=60)
    if not seed_res.get("seeded"):
        return {"attempt_error": "seed failed", "seed_res": seed_res}
    try:
        seed_size = auth_path.stat().st_size
    except OSError:
        return {"attempt_error": "seed stat failed"}

    preexec = _make_fsize_preexec(fsize_limit) if mode == "ulimit" else None
    sentinel_seen = False
    killed = False
    persist_err: int | None = None
    mechanism_fired = False
    writer_exit: int | None = None
    proc = subprocess.Popen(
        [str(tsx_bin), writer_script],
        cwd=str(wt_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=preexec,
    )
    try:
        line = proc.stdout.readline() if proc.stdout else ""
        if line.strip() == SENTINEL_BEFORE:
            sentinel_seen = True

        if mode == "watch-kill":
            if sentinel_seen:
                deadline = time.monotonic() + max_wait_s
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break  # 자식이 write 를 완료(AFTER_SET)하고 스스로 종료 -> 이 attempt 는 미포착
                    # raw: authPath 가 O_TRUNC 로 seed 크기를 벗어남. atomic: temp 출현.
                    try:
                        cur = auth_path.stat().st_size
                        auth_changed = cur != seed_size
                    except FileNotFoundError:
                        auth_changed = True
                    except OSError:
                        auth_changed = False
                    if auth_changed or _temp_in_progress(trial_dir):
                        proc.send_signal(signal.SIGKILL)
                        killed = True
                        break
                    time.sleep(poll_s)
            mechanism_fired = killed
            try:
                writer_exit = proc.wait(timeout=writer_timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                writer_exit = proc.wait(timeout=10)
        else:
            # ulimit mode: RLIMIT_FSIZE 로 write 가 EFBIG 되면 persistProviderChange 가 삼키고 정상 종료(exit 0).
            # 남은 stdout 을 drain 하며 PERSIST_ERR(=write 가 한도에 잘려 기록된 에러 수)를 파싱.
            try:
                rest_out, _rest_err = proc.communicate(timeout=writer_timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                rest_out, _rest_err = proc.communicate(timeout=10)
            writer_exit = proc.returncode
            for ln in (rest_out or "").splitlines():
                s = ln.strip()
                if s.startswith("PERSIST_ERR="):
                    try:
                        persist_err = int(s.split("=", 1)[1])
                    except ValueError:
                        persist_err = None
            # 한도가 실제로 write 를 잘랐는가 (EFBIG 가 기록됨). 이게 ulimit 의 "mechanism fired".
            mechanism_fired = persist_err is not None and persist_err > 0
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    integ = _disk_integrity(auth_path)
    integ.update({
        "sentinel_seen": sentinel_seen,
        "killed": killed,
        "mechanism_fired": mechanism_fired,
        "persist_err": persist_err,
        "writer_exit": writer_exit,
        "seed_size": seed_size,
    })
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
    mode: str,
    fsize_limit: int,
    poll_s: float,
    max_wait_s: float,
    writer_timeout_s: int,
    attempts_per_trial: int,
) -> dict[str, Any]:
    """단일 trial = 독립 crash 시나리오. attempts_per_trial 회까지 (clean+seed+spawn+kill) 반복.

    한 attempt 라도 on-disk authPath 를 손상 상태로 얼리면 trial=lockout. raw 는 거의 항상 첫 시도에서
    partial 을 잡고, atomic 은 구조적으로 절대 손상되지 않는다(0).
    """
    trial_dir.mkdir(parents=True, exist_ok=True)
    auth_path = trial_dir / "auth.json"

    env = dict(base_env)
    env["PROBE_AUTH_PATH"] = str(auth_path)
    env["PROBE_PROVIDER"] = SEED_PROVIDER
    env["PROBE_SEED_KEY"] = SEED_KEY
    env["PROBE_PAYLOAD_BYTES"] = str(payload_bytes)

    attempts_used = 0
    freezes = 0
    killed_count = 0
    mechanism_count = 0
    sentinel_count = 0
    first_corrupt: dict[str, Any] | None = None
    last_integ: dict[str, Any] | None = None

    for _ in range(max(1, attempts_per_trial)):
        attempts_used += 1
        a = _one_kill_attempt(
            tsx_bin=tsx_bin,
            wt_path=wt_path,
            env=env,
            trial_dir=trial_dir,
            auth_path=auth_path,
            seed_script=seed_script,
            writer_script=writer_script,
            mode=mode,
            fsize_limit=fsize_limit,
            poll_s=poll_s,
            max_wait_s=max_wait_s,
            writer_timeout_s=writer_timeout_s,
        )
        if "attempt_error" in a:
            return {"trial_error": a.get("attempt_error", "attempt error"), "attempt": a}
        last_integ = a
        if a.get("sentinel_seen"):
            sentinel_count += 1
        if a.get("killed"):
            killed_count += 1
        if a.get("mechanism_fired"):
            mechanism_count += 1
        if a.get("corrupt"):
            freezes += 1
            if first_corrupt is None:
                first_corrupt = a
            break  # lockout 확정 -> 조기 종료

    lockout = freezes > 0
    rep = first_corrupt or last_integ or {}
    return {
        "lockout": bool(lockout),
        "attempts_used": attempts_used,
        "attempts_cap": max(1, attempts_per_trial),
        "freezes": freezes,
        "killed_count": killed_count,
        "mechanism_count": mechanism_count,
        "sentinel_count": sentinel_count,
        "exists": rep.get("exists"),
        "bytes": rep.get("bytes"),
        "parseOk": rep.get("parseOk"),
        "diskKeyPresent": rep.get("diskKeyPresent"),
        "persist_err": rep.get("persist_err"),
        "writer_exit": rep.get("writer_exit"),
    }


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused (호환성)
    trials: int = DEFAULT_TRIALS,
    payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
    mode: str = "watch-kill",
    fsize_limit: int = DEFAULT_FSIZE_LIMIT_BYTES,
    poll_ms: float = DEFAULT_POLL_MS,
    max_wait_ms: float = DEFAULT_MAX_WAIT_MS,
    attempts_per_trial: int = DEFAULT_ATTEMPTS_PER_TRIAL,
    **kwargs: Any,
) -> dict[str, Any]:
    wt_path = node_entry if node_entry.is_dir() else node_entry.parent
    tsx_bin = wt_path / "node_modules" / ".bin" / "tsx"
    if not tsx_bin.exists():
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"tsx missing: {tsx_bin}"}

    home = env.get("OPENCLAW_HOME")
    if not home:
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": "OPENCLAW_HOME 미지정 (isolated temp HOME 필수)"}

    if mode not in ("watch-kill", "ulimit"):
        return {"scenario": SCENARIO_NAME, "trials": 0, "error": f"unknown mode: {mode}"}

    writer_timeout_s = int(kwargs.get("timeout_seconds") or 120)
    poll_s = max(0.0001, poll_ms / 1000.0)
    max_wait_s = max(0.5, max_wait_ms / 1000.0)

    seed_script = _write_temp_script(wt_path, _seed_script(), "p045v2seed_")
    writer_script = _write_temp_script(wt_path, _writer_script(), "p045v2writer_")

    trials_root = Path(home) / "agent" / "realproc-v2-trials"

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
                    mode=mode,
                    fsize_limit=fsize_limit,
                    poll_s=poll_s,
                    max_wait_s=max_wait_s,
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
    # mechanism_fired = 실패 메커니즘이 실제로 발화한 trial (watch-kill: SIGKILL 송신 / ulimit: EFBIG 기록).
    fired_trials = [t for t in valid if (t.get("mechanism_count") or 0) > 0]
    killed_trials = [t for t in valid if (t.get("killed_count") or 0) > 0]
    lockouts = [t for t in valid if t.get("lockout")]

    measured = len(valid)
    lockout_count = len(lockouts)
    lockout_rate = (lockout_count / measured) if measured else 0.0

    attempts_total = sum(t.get("attempts_used", 0) for t in valid)
    freeze_count = sum(t.get("freezes", 0) for t in valid)
    per_attempt_freeze_rate = (freeze_count / attempts_total) if attempts_total else 0.0

    return {
        "scenario": SCENARIO_NAME,
        "mode": mode,
        "trials": trials,
        "measured_trials": measured,
        "errored_trials": len(errored),
        "attempts_per_trial": attempts_per_trial,
        "attempts_total": attempts_total,
        "freeze_count": freeze_count,
        "per_attempt_freeze_rate": round(per_attempt_freeze_rate, 4),
        "killed_trials": len(killed_trials),
        "mechanism_fired_trials": len(fired_trials),
        "killed_during_write": len(fired_trials),  # _is_usable 호환 alias (메커니즘 발화 기준)
        "lockout_count": lockout_count,
        "lockout_rate": round(lockout_rate, 4),
        "payload_bytes": payload_bytes,
        "fsize_limit": fsize_limit if mode == "ulimit" else None,
        "poll_ms": poll_ms if mode == "watch-kill" else None,
        "trial_results": trial_results,
    }


def _is_usable(m: dict[str, Any]) -> bool:
    if not m or m.get("trials", 0) == 0 or "error" in m:
        return False
    if m.get("measured_trials", 0) == 0:
        return False
    if m.get("killed_during_write", 0) == 0:
        return False
    return True


def evaluate_pre(measurements: dict[str, Any]) -> str:
    if not _is_usable(measurements):
        return "blocked-env"
    return "collected" if measurements.get("lockout_rate", 0.0) > 0 else "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    for m in (without_fix, with_fix):
        if not _is_usable(m):
            return "blocked-env"
    wo = without_fix.get("lockout_rate", 0.0)
    wf = with_fix.get("lockout_rate", 0.0)
    if wo > 0 and wf == 0:
        return "collected"
    return "unreproducible"


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--node-entry", required=True, help="worktree root path (skip_build mode)")
    ap.add_argument("--openclaw-home", required=True, help="isolated temp OPENCLAW_HOME")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--mode", choices=["watch-kill", "ulimit"], default="watch-kill")
    ap.add_argument("--payload-bytes", type=int, default=0, help="0 => mode 별 기본값")
    ap.add_argument("--fsize-limit", type=int, default=DEFAULT_FSIZE_LIMIT_BYTES)
    ap.add_argument("--poll-ms", type=float, default=DEFAULT_POLL_MS)
    ap.add_argument("--max-wait-ms", type=float, default=DEFAULT_MAX_WAIT_MS)
    ap.add_argument("--attempts-per-trial", type=int, default=DEFAULT_ATTEMPTS_PER_TRIAL)
    args = ap.parse_args()

    payload = args.payload_bytes
    if payload <= 0:
        payload = DEFAULT_ULIMIT_PAYLOAD_BYTES if args.mode == "ulimit" else DEFAULT_PAYLOAD_BYTES

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
        payload_bytes=payload,
        mode=args.mode,
        fsize_limit=args.fsize_limit,
        poll_ms=args.poll_ms,
        max_wait_ms=args.max_wait_ms,
        attempts_per_trial=args.attempts_per_trial,
    )
    print(json.dumps(out, indent=2))
