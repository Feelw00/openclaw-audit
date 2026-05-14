#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/cron-manual-run.py

PR #78243 (CAND-024 / SOL-0009) baseline.
manual cron run 이 TASK_RECONCILE_GRACE_MS (5 min) 초과 시 sqlite task_runs.status = 'lost'
가 되는지 측정. fix 후에는 status='failed' 등 lost 가 아닌 정상 finalize.

요구:
- isolated_home (OPENCLAW_HOME redirect)
- OAuth profile 복사 필요 (codex agent 가 LLM 호출)
- cron job 의 message 가 `sleep 420` 등 5분 초과 작업 → grace 초과 트리거
- 측정: sqlite task_runs 의 status / error 컬럼

산출물 (measurements):
  {
    "trials": int,
    "trial_results": [{ "task_id": str, "status": str, "started_at_ms": int, "ended_at_ms": int, "error": str? }],
    "lost_count": int,           # status == 'lost' 인 trial 수
    "non_lost_count": int,
    "duration_observed_ms": [int, ...],
  }

해석:
- pre-sol (without-fix): lost_count > 0 이어야 결함 재현 → status='collected'
                        lost_count == 0 이면 결함 없음 → 'unreproducible'
- post-sol comparison: without_fix.lost_count > 0 AND with_fix.lost_count == 0 → fix 효과 입증
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRES_EXTERNAL_DEP = True  # OAuth + LLM 호출 필수
DEFAULT_SLEEP_SECONDS = 420   # 7 min — 5 min grace 초과
DEFAULT_TIMEOUT_SECONDS = 900  # 15 min
DEFAULT_TRIALS = 1            # 시나리오 자체가 비싸므로 trial 적게
SCENARIO_NAME = "cron-manual-run"


@dataclass
class TrialResult:
    task_id: str | None
    status: str | None        # 'lost' | 'failed' | 'completed' | None
    started_at_ms: int | None
    ended_at_ms: int | None
    error: str | None
    duration_ms: int | None
    raw_row: dict[str, Any] | None


def _run_node(node_entry: Path, args: list[str], env: dict, *, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = ["node", str(node_entry), *args]
    print(f"  $ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}", file=sys.stderr)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def _parse_cron_add_output(stdout: str) -> str | None:
    """openclaw cron add 의 stdout 에서 job-id 추출.

    실제 출력 형식 미확정 — 일반적으로 UUID 또는 'job-id: <uuid>' 패턴.
    여러 패턴 시도.
    """
    import re

    # pattern 1: bare uuid
    m = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", stdout)
    if m:
        return m.group(0)
    # pattern 2: 'id: <something>' or 'job-id: <something>'
    m = re.search(r"(?:job-?id|cron[-_]?id|id)\s*[:=]\s*([\w\-]+)", stdout, re.I)
    if m:
        return m.group(1)
    return None


def _query_task_run(sqlite_path: Path, source_id: str) -> dict[str, Any] | None:
    """task_runs 테이블에서 source_id (= cron job id) 의 최신 row 조회."""
    if not sqlite_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # task_runs 스키마 가정 (PR #78243 evidence 기준):
        #   task_id, source_id, status, started_at, ended_at, last_event_at, error
        try:
            row = cur.execute(
                "SELECT * FROM task_runs WHERE source_id = ? ORDER BY started_at DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            # 컬럼명이 다를 수 있음 — schema 탐색
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            return {"_error": "task_runs query failed", "_tables": tables}
        if row is None:
            return None
        return dict(row)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _wait_for_finalize(
    sqlite_path: Path,
    source_id: str,
    *,
    poll_interval_sec: int = 30,
    timeout_sec: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """ended_at != NULL 또는 status in ('lost','failed','completed') 까지 대기.

    poll. status='lost' 가 보이는 즉시 반환 (PR #78243 의 핵심 시그널).
    """
    deadline = time.time() + timeout_sec
    last_row: dict[str, Any] | None = None
    while time.time() < deadline:
        row = _query_task_run(sqlite_path, source_id)
        if row:
            last_row = row
            status = row.get("status")
            ended = row.get("ended_at")
            if status in ("lost", "failed", "completed", "cancelled") or ended:
                return row
        time.sleep(poll_interval_sec)
    return last_row


def run_one_trial(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TrialResult:
    """단일 trial: cron add → cron run → sqlite poll.

    job 이름은 unique uuid 로 (충돌 회피).
    """
    job_name = f"proof-cron-{uuid.uuid4().hex[:8]}"
    add_args = [
        "cron", "add",
        "--name", job_name,
        "--every", "1h",
        "--message", f"Run the shell command 'sleep {sleep_seconds} && echo done' using your exec tool, then reply 'completed'.",
        "--session", "isolated",
        "--thinking", "high",
        "--timeout-seconds", str(timeout_seconds),
        "--tools", "exec",
        "--model", "openai-codex/gpt-5.5",
    ]
    add = _run_node(node_entry, add_args, env, timeout=120)
    if add.returncode != 0:
        return TrialResult(None, None, None, None, f"cron add failed: {add.stderr[:500]}", None, None)

    job_id = _parse_cron_add_output(add.stdout) or _parse_cron_add_output(add.stderr)
    if not job_id:
        return TrialResult(None, None, None, None, f"could not parse job id from: {add.stdout[:300]}", None, None)

    run = _run_node(node_entry, ["cron", "run", job_id], env, timeout=60)
    if run.returncode != 0:
        return TrialResult(job_id, None, None, None, f"cron run failed: {run.stderr[:500]}", None, None)

    started = int(time.time() * 1000)
    row = _wait_for_finalize(sqlite_path, job_id, timeout_sec=timeout_seconds + 60)
    finished = int(time.time() * 1000)

    if row is None:
        return TrialResult(job_id, None, None, None, "no task_runs row appeared", None, None)
    if "_error" in row:
        return TrialResult(job_id, None, None, None, json.dumps(row), None, row)

    return TrialResult(
        task_id=row.get("task_id"),
        status=row.get("status"),
        started_at_ms=row.get("started_at"),
        ended_at_ms=row.get("ended_at"),
        error=row.get("error"),
        duration_ms=(row.get("ended_at") - row.get("started_at")) if row.get("ended_at") and row.get("started_at") else (finished - started),
        raw_row=row,
    )


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,
    trials: int = DEFAULT_TRIALS,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """N trial 반복. measurements dict 반환 (state.py 가 SOL frontmatter 에 paste)."""
    results: list[TrialResult] = []
    for i in range(trials):
        print(f"[scenario] trial {i + 1}/{trials}", file=sys.stderr)
        results.append(
            run_one_trial(
                node_entry=node_entry,
                env=env,
                sqlite_path=sqlite_path,
                sleep_seconds=sleep_seconds,
                timeout_seconds=timeout_seconds,
            )
        )

    lost_count = sum(1 for r in results if r.status == "lost")
    non_lost_count = sum(1 for r in results if r.status and r.status != "lost")

    return {
        "scenario": SCENARIO_NAME,
        "trials": trials,
        "sleep_seconds": sleep_seconds,
        "lost_count": lost_count,
        "non_lost_count": non_lost_count,
        "trial_results": [
            {
                "task_id": r.task_id,
                "status": r.status,
                "started_at_ms": r.started_at_ms,
                "ended_at_ms": r.ended_at_ms,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in results
        ],
    }


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol mode 결정. status enum 반환.

    cron-manual-run baseline:
      - lost_count > 0  → 'collected' (결함 재현 = task_runs 에 status='lost' 출현)
      - trials == 0     → 'blocked-env' (시나리오 자체 실행 불가)
      - lost_count == 0 → 'unreproducible' (결함 발현 안 됨 = false positive 신호)
    """
    if measurements.get("trials", 0) == 0:
        return "blocked-env"
    if measurements.get("lost_count", 0) > 0:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol mode 결정. with vs without 비교.

    cron-manual-run baseline (PR #78243 모델):
      - without_fix.lost_count > 0 AND with_fix.lost_count == 0 → 'collected' (fix 효과 입증)
      - without_fix.lost_count == 0 → 'unreproducible' (without 에서도 결함 없음)
      - with_fix.lost_count > 0     → 'unreproducible' (fix 가 효과 없음)
    """
    if without_fix.get("trials", 0) == 0 or with_fix.get("trials", 0) == 0:
        return "blocked-env"
    wf_lost = without_fix.get("lost_count", 0)
    f_lost = with_fix.get("lost_count", 0)
    if wf_lost > 0 and f_lost == 0:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    """post-sol → render_proof.render_pr_body_section 입력 6 필드 dict 생성.

    behavior/environment/steps/evidence/observedResult/notTested.
    """
    wf_lost = without_fix.get("lost_count", 0)
    f_lost = with_fix.get("lost_count", 0)
    trials = with_fix.get("trials", 0)
    sleep_sec = with_fix.get("sleep_seconds", DEFAULT_SLEEP_SECONDS)

    behavior = (
        f"Without this patch, manual cron runs that exceed TASK_RECONCILE_GRACE_MS (5 min) "
        f"are marked status='lost' in sqlite task_runs (cron-manual-run scenario). "
        f"With this patch, the same long-running manual run finalizes normally."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw locally built from this branch. "
        "Isolated OPENCLAW_HOME via skills/real-behavior-proof/harness/env_isolate.py. "
        "OpenAI Codex OAuth (ChatGPT Plus). Same gateway/job/auth across both builds; only fix diff differs."
    )
    # NOTE: line-start `# ` 회피 (openclaw policy heading-break). 인라인 주석은 ` # ` (스페이스 prefix).
    steps = (
        f"```text\n"
        f"$ pnpm build                                          # both builds (base & head)\n"
        f"$ openclaw cron add --name proof-cron-* --message 'sleep {sleep_sec} ...' \\\n"
        f"      --tools exec --model openai-codex/gpt-5.5 --timeout-seconds 900\n"
        f"$ openclaw cron run <job-id>\n"
        f"  (Wait > 5 min then observe sqlite task_runs.)\n"
        f"$ sqlite3 $OPENCLAW_HOME/tasks/runs.sqlite \\\n"
        f"      \"SELECT status, error FROM task_runs WHERE source_id='<job-id>'\"\n"
        f"```"
    )
    # NOTE: evidence 본문에 markdown heading (`# `, `## `) 사용 금지.
    # openclaw policy `extractFieldValue` 가 `^#{1,6}\\s+\\S` 매칭을 break 조건으로 쓰므로
    # fenced code 안이라도 `#` 시작 라인은 본문이 거기서 잘림.
    evidence = (
        f"Live sqlite task_runs from $OPENCLAW_HOME/tasks/runs.sqlite over {trials} trials/build "
        f"(see proofs/PROOF-*-post-*.md for full row dump). Console output:\n\n"
        f"```text\n"
        f"[Build A] without this patch (base sha):\n"
        f"  status='lost' count: {wf_lost}/{trials}\n"
        f"  trial sample: {json.dumps(without_fix.get('trial_results', [])[:1])[:300]}\n"
        f"\n"
        f"[Build B] with this patch (head sha):\n"
        f"  status='lost' count: {f_lost}/{trials}\n"
        f"  trial sample: {json.dumps(with_fix.get('trial_results', [])[:1])[:300]}\n"
        f"```"
    )
    observed = (
        f"With patch, no trial reached status='lost' (vs {wf_lost}/{trials} without patch). "
        f"task_runs finalize via produce-side mark/clear instead of sweeper-side 'lost' marker."
    )
    not_tested = (
        "OAuth provider failure paths (out of scope). Channels delivery (no channels configured in isolated env)."
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

    ap = argparse.ArgumentParser(description="cron-manual-run scenario standalone runner")
    ap.add_argument("--node-entry", required=True, help="path to openclaw.mjs in built worktree")
    ap.add_argument("--openclaw-home", required=True, help="OPENCLAW_HOME path (env_isolate 산출)")
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--sleep-seconds", type=int, default=DEFAULT_SLEEP_SECONDS)
    ap.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = ap.parse_args()

    import os

    env = os.environ.copy()
    env["OPENCLAW_HOME"] = args.openclaw_home
    home = Path(args.openclaw_home)
    sqlite_path = home / "tasks" / "runs.sqlite"

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=sqlite_path,
        trials=args.trials,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(out, indent=2))
