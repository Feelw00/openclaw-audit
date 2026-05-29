#!/usr/bin/env python3
"""
skills/real-behavior-proof/scenarios/proof-CAND-049.py

CAND-049 (infra-state-migrations cross-store-consistency, P1) baseline.
FIND-001: state-migrations.ts:1197-1212 의 sessions save 게이트가 OR 조건
`(legacyParsed.ok || targetParsed.ok)` 이고 `targetParsed.ok` 를 save 경로에서 검사하지
않는다. target sessions.json 이 손상(JSON5 파싱 실패)이면 readSessionStoreJson5 가
{store:{}, ok:false} 로 swallow → targetStore={} 로 둔갑. legacy 가 정상(ok=true)이고
비어있지 않으면 게이트가 통과하여, 손상되었지만 디스크에 그대로 남아있던 target sessions.json 이
legacy-only 병합본으로 saveSessionStore(:1209) 에 의해 무조건 덮어써진다. 손상 파일을
수동 복구할 마지막 기회가 영구 소실된다. legacy 손상엔 :1191 warn + :1239 가드가 있으나
target 손상엔 대응 검사가 전혀 없어 비대칭 무방비.

가장 결정론적인 시나리오 — crash/타이밍 불필요. OR 게이트의 비대칭 보호 부재라
손상 target 이 항상 (legacy ok 인 한) 덮어써진다.

원리:
- worktree 의 src/infra/state-migrations.ts 를 tsx 로 직접 import (skip_build).
- 격리된 임시 state-dir (OPENCLAW_STATE_DIR) 에 layout 구성:
    (a) legacy  <stateDir>/sessions/sessions.json          → 유효 JSON5, sessionId 보유 키
    (b) target  <stateDir>/agents/main/sessions/sessions.json → 손상 JSON5 (trailing garbage)
        + 사람이 복구 가능했을 sentinel 바이트 + target-only 세션 레코드
- detectLegacyStateMigrations({cfg}) → runLegacyStateMigrations({detected}) (public entry,
  내부에서 미export migrateLegacySessions 를 그대로 탄다 = production doctor/startup 경로).
- 마이그레이션 후 target 파일을 다시 읽어:
    * overwritten: 손상 sentinel 이 사라지고 파일이 valid JSON5 로 재작성됐는가
    * targetCorruptBytesSurvived: 손상 원본 바이트가 보존됐는가 (false = 영구 소실)
    * warnedAboutTargetCorruption: target 손상 경고가 떴는가 (false = silent overwrite)

REQUIRES_EXTERNAL_DEP=False — OAuth/LLM/채널/네트워크 없음. 순수 fs 마이그레이션.

without-fix: 손상 target 이 legacy-only 로 무조건 덮어써짐
             (overwritten=true, targetCorruptBytesSurvived=false, warnedAboutTargetCorruption=false)
with-fix:    targetParsed.ok 가드 추가 시 손상 target 보존 (덮어쓰기 skip 또는 abort/backup)
             (overwritten=false OR targetCorruptBytesSurvived=true; 손상 경고 발생)
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
SCENARIO_NAME = "proof-CAND-049"
DEFAULT_TRIALS = 1  # 결정론적 단일 trial — OR 게이트 비대칭이라 반복 불필요


# trailing garbage sentinel — JSON5.parse 가 반드시 throw 하게 만드는 마커.
# 마이그레이션이 손상 target 을 덮어쓰면 이 바이트는 디스크에서 사라진다.
_CORRUPT_SENTINEL = "@@@CAND049-UNRECOVERABLE-TARGET-BYTES@@@"


def _build_probe_script() -> str:
    return f"""\
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {{
  detectLegacyStateMigrations,
  runLegacyStateMigrations,
}} from './src/infra/state-migrations.ts';

const SENTINEL = {json.dumps(_CORRUPT_SENTINEL)};

// Isolated temp state-dir. OPENCLAW_STATE_DIR override → resolveStateDir returns this verbatim.
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cand049-state-'));
process.env.OPENCLAW_STATE_DIR = stateDir;
// resolveDefaultAgentId(cfg) with empty cfg → DEFAULT_AGENT_ID = 'main'.
const agentId = 'main';

const legacyDir = path.join(stateDir, 'sessions');
const legacyStorePath = path.join(legacyDir, 'sessions.json');
const targetDir = path.join(stateDir, 'agents', agentId, 'sessions');
const targetStorePath = path.join(targetDir, 'sessions.json');

fs.mkdirSync(legacyDir, {{ recursive: true }});
fs.mkdirSync(targetDir, {{ recursive: true }});

// (a) Legacy store — valid JSON5, entries carry sessionId so they survive normalizeSessionEntry.
const legacyStore = {{
  'agent:main:hooks:legacy-key-1': {{ sessionId: 'legacy-session-1', updatedAt: 1000 }},
  'agent:main:hooks:legacy-key-2': {{ sessionId: 'legacy-session-2', updatedAt: 2000 }},
}};
fs.writeFileSync(legacyStorePath, JSON.stringify(legacyStore, null, 2), 'utf-8');

// (b) Target store — CORRUPT (trailing garbage → JSON5.parse throws). Contains a
//     target-only session record a human could have salvaged, then unrecoverable bytes.
const targetRecoverablePrefix = JSON.stringify({{
  'agent:main:hooks:target-only-key': {{ sessionId: 'target-session-RECOVERABLE', updatedAt: 9999 }},
}}, null, 2);
const corruptTargetRaw = targetRecoverablePrefix + '\\n' + SENTINEL + '\\n}}}} broken trailing';
fs.writeFileSync(targetStorePath, corruptTargetRaw, 'utf-8');

// Pre-flight sanity: confirm the target really is corrupt (JSON5 would fail). We do not
// import JSON5 here; the migration's readSessionStoreJson5 swallow is what matters.
const beforeRaw = fs.readFileSync(targetStorePath, 'utf-8');
const beforeHasSentinel = beforeRaw.includes(SENTINEL);

// Exercise the production entry point. detect → runLegacyStateMigrations → migrateLegacySessions.
const cfg = {{}}; // empty config: default agent 'main', default mainKey/scope.
const detected = await detectLegacyStateMigrations({{ cfg }});
let result;
let threw = null;
try {{
  result = await runLegacyStateMigrations({{ detected }});
}} catch (e) {{
  threw = String(e && e.message ? e.message : e);
}}

const warnings = (result && Array.isArray(result.warnings)) ? result.warnings : [];
const changes = (result && Array.isArray(result.changes)) ? result.changes : [];

// Re-read the target file after migration.
let afterRaw = '';
let targetFileExists = false;
try {{
  afterRaw = fs.readFileSync(targetStorePath, 'utf-8');
  targetFileExists = true;
}} catch {{ /* file gone */ }}

const targetCorruptBytesSurvived = afterRaw.includes(SENTINEL);
// overwritten = corrupt original replaced (sentinel gone) AND now-valid-looking JSON object.
let afterParsesAsObject = false;
let afterKeys = [];
try {{
  const parsed = JSON.parse(afterRaw);
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {{
    afterParsesAsObject = true;
    afterKeys = Object.keys(parsed);
  }}
}} catch {{ /* still not valid JSON */ }}

const overwritten = beforeHasSentinel && !targetCorruptBytesSurvived && afterParsesAsObject;
const targetOnlyKeyPresent = afterKeys.includes('agent:main:hooks:target-only-key');
const legacyKeysPresent = afterKeys.some((k) => k.startsWith('agent:main:hooks:legacy-key'));

const warnedAboutTargetCorruption = warnings.some((w) =>
  /target|unreadable|corrupt/i.test(String(w)) &&
  String(w).includes(targetStorePath));

// cleanup
try {{ fs.rmSync(stateDir, {{ recursive: true, force: true }}); }} catch {{}}
delete process.env.OPENCLAW_STATE_DIR;

console.log(JSON.stringify({{
  beforeHasSentinel,
  targetFileExists,
  overwritten,
  targetCorruptBytesSurvived,
  afterParsesAsObject,
  targetOnlyKeyPresent,        // target-only recoverable record present after migration?
  legacyKeysPresent,           // were legacy keys written into the target file?
  warnedAboutTargetCorruption,
  afterKeys,
  warnings,
  changes,
  threw,
}}));
process.exit(0);
"""


def run_scenario(
    *,
    node_entry: Path,
    env: dict,
    sqlite_path: Path,  # unused
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
        return {"scenario": SCENARIO_NAME, "trials": trials, **payload}
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def evaluate_pre(measurements: dict[str, Any]) -> str:
    """pre-sol: without-fix 빌드에서 손상 target 이 legacy-only 로 무조건 덮어써지면 collected.

    결정론적 — OR 게이트 비대칭 보호 부재라 손상 target 이 항상 덮어써진다.
    """
    if measurements.get("trials", 0) == 0 or "error" in measurements:
        return "blocked-env"
    # 전제: 손상 target 이 실제로 손상이었고(sentinel 존재), 마이그레이션이 throw 없이 진행됐다.
    if not measurements.get("beforeHasSentinel"):
        return "blocked-env"  # fixture 자체가 손상 안 됨 → 측정 무효
    overwritten = measurements.get("overwritten") is True
    sentinel_lost = measurements.get("targetCorruptBytesSurvived") is False
    legacy_written = measurements.get("legacyKeysPresent") is True
    target_only_lost = measurements.get("targetOnlyKeyPresent") is False
    silent = measurements.get("warnedAboutTargetCorruption") is False
    # 손상 target 이 legacy-only 로 덮어써졌고, 손상 원본 바이트 + target-only 레코드가 소실됐으며,
    # target 손상 경고가 없었다 = 결함 관측.
    if overwritten and sentinel_lost and legacy_written and target_only_lost and silent:
        return "collected"
    return "unreproducible"


def evaluate_post(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> str:
    """post-sol: targetParsed.ok 가드 추가 시 손상 target 보존되면 collected.

    with-fix 기대: 손상 target 을 legacy-only 로 덮어쓰지 않는다 →
      손상 원본 바이트 보존(targetCorruptBytesSurvived=true) 또는 덮어쓰기 skip(overwritten=false).
    """
    for m in (without_fix, with_fix):
        if m.get("trials", 0) == 0 or "error" in m:
            return "blocked-env"
        if not m.get("beforeHasSentinel"):
            return "blocked-env"
    # without-fix 가 결함을 보여야 대조가 성립.
    wo_defect = (
        without_fix.get("overwritten") is True
        and without_fix.get("targetCorruptBytesSurvived") is False
    )
    if not wo_defect:
        return "unreproducible"
    # with-fix 가 손상 target 을 보존: 덮어쓰기 안 했거나(overwritten=false) 원본 바이트 살아있음.
    wf_preserved = (
        with_fix.get("overwritten") is False
        or with_fix.get("targetCorruptBytesSurvived") is True
    )
    if wf_preserved:
        return "collected"
    return "unreproducible"


def render_pr_evidence(without_fix: dict[str, Any], with_fix: dict[str, Any]) -> dict[str, str]:
    behavior = (
        "Without this patch, migrateLegacySessions (state-migrations.ts:1197-1212, reached via the public "
        "runLegacyStateMigrations entry point used by doctor/cold-startup) gates the sessions save on the OR "
        "condition `(legacyParsed.ok || targetParsed.ok)` and never inspects `targetParsed.ok` on the save "
        "path. When the target sessions.json is corrupt (JSON5 parse fails), readSessionStoreJson5 swallows "
        "the error and returns `{store:{}, ok:false}`, so the corrupt target is treated as empty. Because "
        "the legacy store is readable, the gate passes and saveSessionStore overwrites the still-on-disk "
        "corrupt target file with a legacy-only merge. The corrupt bytes a human could have salvaged, and "
        "any target-only session records, are permanently destroyed. Legacy corruption is guarded "
        "(:1191 warn, :1239), but target corruption has no symmetric check. With this patch, a "
        "`targetParsed.ok` guard preserves the corrupt target instead of overwriting it."
    )
    environment = (
        "macOS (darwin arm64), Node 23.x, OpenClaw run via tsx against this branch's TypeScript sources "
        "(no separate build step). Fully isolated: probe creates its own temp OPENCLAW_STATE_DIR and only "
        "touches files inside it; production state is never read or written. No external dependencies "
        "(no OAuth/LLM/channel/network) — pure filesystem migration. Deterministic: the OR-gate's "
        "asymmetric guard gap means the corrupt target is always overwritten when legacy is readable; "
        "no crash or timing is required."
    )
    steps = (
        "```text\n"
        "$ # base sha (without patch) and head sha (with patch), each via tsx\n"
        "$ npx tsx <probe.ts>                                  (worktree-relative)\n"
        "  fixture in isolated OPENCLAW_STATE_DIR:\n"
        "    legacy  sessions/sessions.json            valid JSON5, 2 keys w/ sessionId\n"
        "    target  agents/main/sessions/sessions.json  corrupt JSON5 (trailing garbage)\n"
        "                                                + 1 recoverable target-only record\n"
        "  run: detectLegacyStateMigrations({}) -> runLegacyStateMigrations({detected})\n"
        "  measure: was corrupt target overwritten / did corrupt bytes survive / any warning\n"
        "```"
    )
    evidence = (
        "Live Node.js (tsx) measurement of the corrupt target sessions.json after migration:\n\n"
        "```text\n"
        "[Build A] without this patch (base sha):\n"
        f"  beforeHasSentinel (target was corrupt):     {without_fix.get('beforeHasSentinel')}\n"
        f"  overwritten (corrupt -> legacy-only):       {without_fix.get('overwritten')}\n"
        f"  targetCorruptBytesSurvived:                 {without_fix.get('targetCorruptBytesSurvived')}\n"
        f"  targetOnlyKeyPresent (recoverable record):  {without_fix.get('targetOnlyKeyPresent')}\n"
        f"  legacyKeysPresent (legacy written to tgt):  {without_fix.get('legacyKeysPresent')}\n"
        f"  warnedAboutTargetCorruption:                {without_fix.get('warnedAboutTargetCorruption')}\n"
        f"  afterKeys:                                  {json.dumps(without_fix.get('afterKeys'))}\n"
        "\n"
        "[Build B] with this patch (head sha):\n"
        f"  beforeHasSentinel (target was corrupt):     {with_fix.get('beforeHasSentinel')}\n"
        f"  overwritten (corrupt -> legacy-only):       {with_fix.get('overwritten')}\n"
        f"  targetCorruptBytesSurvived:                 {with_fix.get('targetCorruptBytesSurvived')}\n"
        f"  targetOnlyKeyPresent (recoverable record):  {with_fix.get('targetOnlyKeyPresent')}\n"
        f"  warnedAboutTargetCorruption:                {with_fix.get('warnedAboutTargetCorruption')}\n"
        f"  afterKeys:                                  {json.dumps(with_fix.get('afterKeys'))}\n"
        "```"
    )
    observed = (
        f"Without the patch, the corrupt target was overwritten with a legacy-only store "
        f"(overwritten={without_fix.get('overwritten')}, corruptBytesSurvived="
        f"{without_fix.get('targetCorruptBytesSurvived')}, recoverable target-only record "
        f"present={without_fix.get('targetOnlyKeyPresent')}) with no target-corruption warning "
        f"(warned={without_fix.get('warnedAboutTargetCorruption')}). With the patch, the corrupt "
        f"target is preserved (overwritten={with_fix.get('overwritten')}, corruptBytesSurvived="
        f"{with_fix.get('targetCorruptBytesSurvived')}), so the file remains available for manual recovery."
    )
    not_tested = (
        "Multi-store split-state on mid-migration throw (FIND-002, separate axis — covered by a different "
        "scenario/fix). Real-world frequency of target sessions.json corruption (out of scope; the defect "
        "is the unconditional overwrite, not the corruption source). Doctor preview/confirm UI surfacing of "
        "corruption to the operator (out of allowed_paths)."
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
    env["OPENCLAW_HOME"] = args.openclaw_home

    out = run_scenario(
        node_entry=Path(args.node_entry),
        env=env,
        sqlite_path=Path(args.openclaw_home) / "tasks" / "runs.sqlite",
        trials=args.trials,
    )
    print(json.dumps(out, indent=2))
