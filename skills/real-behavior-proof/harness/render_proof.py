#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/render_proof.py

openclaw 의 `## Real behavior proof` 섹션 6 필드 형식 mirror.
참조 source-of-truth: /Users/lucas/Project/openclaw/scripts/github/real-behavior-proof-policy.mjs
- L9-46  requiredProofFields (필드 라벨 + 별칭 + allowNone)
- L58-59 mockOnlyEvidenceRegex (회피 대상)
- L67-68 liveCommandRegex (포함 필수: openclaw|node|docker|curl|gh|ssh|adb|xcrun|xcodebuild|open|npm run|pnpm openclaw)
- L207-281 evaluateRealBehaviorProof (PR body 검증 로직)

이 모듈은 measurement 결과 + scenario 메타에서 PR body 섹션 텍스트를 만든다.
출력 텍스트는 외부 PR 인 경우 evaluateRealBehaviorProof() 가 status="passed" 를 반환해야 한다.
검증: smoke test 가 node -e 로 위 함수 직접 호출.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# openclaw policy 의 필드 라벨 (canonical 만 사용 — 별칭은 검증용)
FIELD_LABELS = {
    "behavior": "Behavior or issue addressed",
    "environment": "Real environment tested",
    "steps": "Exact steps or command run after this patch",
    "evidence": "Evidence after fix",
    "observedResult": "Observed result after fix",
    "notTested": "What was not tested",
}

import re

# policy mjs 의 정규식 mirror (real-behavior-proof-policy.mjs L58-68 참조).
# 동기화 검증: smoke test 가 node -e 로 실 정책에 통과 확인.
_LIVE_COMMAND_RE = re.compile(
    r"\b(?:openclaw|node|docker|curl|gh|ssh|adb|xcrun|xcodebuild|open|npm\s+run|pnpm\s+openclaw)\b",
    re.I,
)
_EVIDENCE_DESCRIPTOR_RE = re.compile(
    r"\b(?:screenshot|screen\s*recording|recording|terminal\s+(?:capture|screenshot|transcript|output)"
    r"|console\s+(?:output|log)|runtime\s+logs?|redacted\s+logs?|live\s+output|actual\s+output"
    r"|observed\s+output|stdout|stderr|stack trace|trace excerpt|log excerpt|linked\s+artifacts?"
    r"|artifact\s+links?)\b|```[\s\S]*\n[\s\S]*\n```",
    re.I,
)
_ARTIFACT_EVIDENCE_RE = re.compile(
    r"!\[[^\]]*\]\([^)]+\)|github\.com/user-attachments/assets/"
    r"|github\.com/[^/\s]+/[^/\s]+/actions/runs/\d+/artifacts/\d+"
    r"|https?://\S+\.(?:png|jpe?g|gif|webp|mp4|mov|webm)\b",
    re.I,
)


_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)


def _check_no_inline_heading(field_name: str, value: str) -> None:
    """policy mjs L150 의 `^#{1,6}\\s+\\S` break 조건 회피.

    fenced code block 안이라도 line-start 의 `#` heading 패턴은 본문 추출을 거기서 잘라버려
    필드가 missing 처리됨. 검증 단계에서 raise 하여 시나리오 작성자가 즉시 발견.
    """
    if _HEADING_RE.search(value):
        m = _HEADING_RE.search(value)
        snippet = value[m.start() : m.start() + 60].splitlines()[0]
        raise ValueError(
            f"field '{field_name}' contains line-start markdown heading: {snippet!r}\n"
            f"openclaw policy extractFieldValue breaks at `^#{{1,6}}\\s+\\S` even inside fenced code. "
            f"Replace `#` with `[Section]` or indent the line."
        )


def _passes_real_evidence_check(evidence: str, observed: str, steps: str) -> bool:
    """policy mjs L249-263 의 hasRealEvidence 로직 mirror.

    real-evidence = artifact OR (descriptor AND non-mock payload) OR live-command.
    payload 검사는 단순화: evidence + observed 가 비어있지 않으면 OK.
    """
    evidence_content = f"{evidence}\n{observed}"
    proof_content = f"{evidence}\n{observed}\n{steps}"

    if _ARTIFACT_EVIDENCE_RE.search(evidence_content):
        return True
    if _EVIDENCE_DESCRIPTOR_RE.search(evidence_content) and evidence_content.strip():
        return True
    if _LIVE_COMMAND_RE.search(evidence_content):
        return True
    # policy 는 evidence + observedResult 에서만 liveCommand 검사하지만,
    # 우리는 steps 에도 있으면 evidence 가 그 출력의 캡쳐라고 판단 가능 → fenced code 동반 시 허용.
    if _LIVE_COMMAND_RE.search(proof_content) and "```" in evidence_content:
        return True
    return False


def render_pr_body_section(
    *,
    behavior: str,
    environment: str,
    steps: str,
    evidence: str,
    observed_result: str,
    not_tested: str = "",
) -> str:
    """openclaw policy 호환 `## Real behavior proof` 섹션 마크다운 생성.

    각 필드는 `- **{label}**:` 라인으로 시작 + 다음 라인부터 본문 (코드 블록 포함 가능).
    빈 값은 정책상 missing 으로 간주되므로 호출자가 보장. notTested 만 'None' 등 허용.
    """
    for fname, fval in [
        ("behavior", behavior),
        ("environment", environment),
        ("steps", steps),
        ("evidence", evidence),
        ("observed_result", observed_result),
        ("not_tested", not_tested),
    ]:
        _check_no_inline_heading(fname, fval)

    if not _passes_real_evidence_check(evidence, observed_result, steps):
        raise ValueError(
            "evidence/observedResult 가 real-behavior-proof-policy.mjs hasRealEvidence 검사를 통과 못 함. "
            "artifact (이미지/비디오 링크) 또는 evidenceDescriptor (screenshot/terminal/console output 등) "
            "또는 liveCommand 키워드 (openclaw/node/docker/curl/gh/...) 중 하나 필요."
        )

    sections = [
        ("behavior", behavior),
        ("environment", environment),
        ("steps", steps),
        ("evidence", evidence),
        ("observedResult", observed_result),
        ("notTested", not_tested or "None — 본 fix 의 trigger 경로 외 별도 미테스트 영역 없음."),
    ]

    out = ["## Real behavior proof", ""]
    for key, value in sections:
        label = FIELD_LABELS[key]
        body = value.strip()
        if "\n" in body or body.startswith("```"):
            out.append(f"- **{label}**:")
            out.append("")
            out.append(body)
            out.append("")
        else:
            out.append(f"- **{label}**: {body}")
    return "\n".join(out).rstrip() + "\n"


def render_proof_record(
    *,
    target: str,
    phase: str,
    scenario: str,
    status: str,
    measurements: dict[str, Any],
    pr_body_section: str | None,
    base_sha: str | None,
    head_sha: str | None,
    started_at: str,
    finished_at: str,
    notes: str = "",
) -> str:
    """proofs/PROOF-{target}-{phase}-{ts}.md 의 본문 생성.

    frontmatter + 사람이 읽을 수 있는 요약 + 원시 measurements JSON.
    """
    fm = {
        "target": target,
        "phase": phase,
        "scenario": scenario,
        "status": status,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    fm_lines = ["---"]
    for k, v in fm.items():
        if v is None:
            fm_lines.append(f"{k}: null")
        else:
            fm_lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    fm_lines.append("---")

    parts = ["\n".join(fm_lines), ""]
    parts.append(f"# PROOF — {target} ({phase})")
    parts.append("")
    parts.append(f"**status**: `{status}`")
    parts.append(f"**scenario**: `{scenario}`")
    parts.append("")

    parts.append("## Measurements")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(measurements, ensure_ascii=False, indent=2, sort_keys=True))
    parts.append("```")
    parts.append("")

    if pr_body_section:
        parts.append("## PR body section (paste into PR description)")
        parts.append("")
        parts.append(pr_body_section)
        parts.append("")

    if notes:
        parts.append("## Notes")
        parts.append("")
        parts.append(notes)
        parts.append("")

    return "\n".join(parts)


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proof_file_path(audit_root: Path, target: str, phase: str) -> Path:
    """proofs/PROOF-{target}-{phase}-{YYYYMMDD-HHMMSS}.md 경로 생성."""
    if phase not in ("pre", "post"):
        raise ValueError(f"phase must be 'pre' or 'post', got {phase!r}")
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return audit_root / "proofs" / f"PROOF-{target}-{phase}-{ts}.md"


# CLI for ad-hoc testing
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="render_proof — PR body / proof record renderer")
    ap.add_argument("--demo", action="store_true", help="render a sample PR body section")
    args = ap.parse_args()

    if args.demo:
        sample = render_pr_body_section(
            behavior="Without this patch, repeated `openclaw cron run` invocations leak Map entries.",
            environment="macOS 25.4 (darwin arm64), Node 23.9, locally built.",
            steps="```text\n$ pnpm build\n$ openclaw cron run <job-id>\n```",
            evidence="```text\nMap.size after 100 invocations:\n  without-fix: 100\n  with-fix:    0\n```",
            observed_result="With patch, Map.size remains 0 across 100 trials.",
            not_tested="OAuth provider failure paths (out of scope).",
        )
        print(sample)
