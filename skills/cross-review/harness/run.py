#!/usr/bin/env python3
"""
skills/cross-review/harness/run.py

입력: target (CAND-NNN / SOL-NNNN / PR#NNNNN / path) + mode + 선택적 roles
출력: JSON { target, mode, prompts: [{ role, prompt }, ...] }

메인 세션은 이 출력을 읽고 각 prompt 로 Agent tool 을 병렬 호출한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: pyyaml 필요. `pip install pyyaml`\n")
    sys.exit(2)

SKILL_ROOT = Path(__file__).resolve().parent.parent
AUDIT_ROOT = SKILL_ROOT.parent.parent
MODES_DIR = SKILL_ROOT / "modes"
ROLES_MD = SKILL_ROOT / "ROLES.md"


def load_mode(mode_name: str) -> dict:
    path = MODES_DIR / f"{mode_name}.yaml"
    if not path.exists():
        sys.exit(f"ERROR: mode '{mode_name}' 존재 안 함 ({path})")
    with path.open() as f:
        return yaml.safe_load(f)


def parse_roles_md() -> dict[str, str]:
    """ROLES.md 에서 각 역할의 '### 프롬프트 템플릿' 섹션 파싱.

    반환: { role_name: prompt_template_string }
    """
    if not ROLES_MD.exists():
        sys.exit(f"ERROR: ROLES.md 없음 ({ROLES_MD})")
    text = ROLES_MD.read_text()
    roles: dict[str, str] = {}
    # Match '## Role: <name>' sections, then the '```' code block after '### 프롬프트 템플릿'
    pattern = re.compile(
        r"## Role:\s*(?P<name>[\w\-]+)\s*\n.*?### 프롬프트 템플릿\s*\n\s*```(?:\w*)?\s*\n(?P<prompt>.*?)\n```",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        name = m.group("name").strip()
        prompt = m.group("prompt").rstrip()
        roles[name] = prompt
    return roles


def resolve_target_files(target: str) -> list[str]:
    """target 에 따라 읽어야 할 필수 파일 경로 목록을 반환."""
    files: list[str] = []

    if target.startswith("CAND-"):
        cand_path = AUDIT_ROOT / "issue-candidates" / f"{target}.md"
        if cand_path.exists():
            files.append(str(cand_path))
        # Cross-reference 된 FIND 파일들
        if cand_path.exists():
            with cand_path.open() as f:
                cand_text = f.read()
            for fm in re.findall(r"FIND-[\w\-]+", cand_text):
                for subdir in ("findings/ready", "findings/drafts", "findings/rejected"):
                    fp = AUDIT_ROOT / subdir / f"{fm}.md"
                    if fp.exists() and str(fp) not in files:
                        files.append(str(fp))
                        break
    elif target.startswith("FIND-"):
        for subdir in ("findings/ready", "findings/drafts", "findings/rejected"):
            fp = AUDIT_ROOT / subdir / f"{target}.md"
            if fp.exists():
                files.append(str(fp))
                break
    elif target.startswith("SOL-"):
        sp = AUDIT_ROOT / "solutions" / f"{target}.md"
        if sp.exists():
            files.append(str(sp))
    elif target.startswith("PR#") or target.startswith("#"):
        # PR — 사용자가 --extra-files 로 지정하거나, gh api 호출은 메인 세션이.
        # 여기서는 target 자체만 전달.
        pass
    else:
        # Path 직접
        p = Path(target)
        if p.exists():
            files.append(str(p.resolve()))
        elif (AUDIT_ROOT / target).exists():
            files.append(str((AUDIT_ROOT / target).resolve()))

    return files


def render_prompt(
    template: str,
    target: str,
    target_files: list[str],
    extra_context: dict,
) -> str:
    """역할 프롬프트 템플릿의 {placeholder} 를 실제 값으로 치환.

    지원 placeholder:
    - {target_type}       : CAND / FIND / SOL / PR / file
    - {target_files}      : bullet list
    - {target}            : 원본 target 문자열
    - {maintainer_quote}  : maintainer-response mode 전용
    - {invariant}         : maintainer-response mode 전용
    - {pr_reference}      : maintainer-response mode 전용
    - {relevant_paths}    : upstream-dup-checker 전용 (빈 문자열이면 src/ 기본)
    """
    target_type = _infer_target_type(target)
    files_block = "\n".join(f"- {p}" for p in target_files) if target_files else "(없음 — target 자체 참조)"

    replacements = {
        "target_type": target_type,
        "target_files": files_block,
        "target": target,
        "maintainer_quote": extra_context.get("maintainer_quote", "(N/A)"),
        "invariant": extra_context.get("invariant", "(N/A)"),
        "pr_reference": extra_context.get("pr_reference", "(N/A)"),
        "relevant_paths": extra_context.get("relevant_paths", "src/"),
    }

    out = template
    for k, v in replacements.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _infer_target_type(target: str) -> str:
    if target.startswith("CAND-"):
        return "candidate"
    if target.startswith("FIND-"):
        return "finding"
    if target.startswith("SOL-"):
        return "solution"
    if target.startswith("PR#") or target.startswith("#"):
        return "pull request"
    return "file"


def main() -> None:
    ap = argparse.ArgumentParser(description="cross-review 프롬프트 렌더러")
    ap.add_argument("--target", required=True, help="CAND-011 / SOL-0002 / PR#68543 / path")
    ap.add_argument("--mode", required=True, choices=["post-harness", "pre-pr", "maintainer-response"])
    ap.add_argument("--roles", help="comma-separated role names (생략 시 mode 기본값)")
    ap.add_argument("--maintainer-quote", default="")
    ap.add_argument("--invariant", default="")
    ap.add_argument("--pr-reference", default="")
    ap.add_argument("--relevant-paths", default="src/")
    args = ap.parse_args()

    mode_cfg = load_mode(args.mode)
    roles_catalog = parse_roles_md()

    # Role 선택
    if args.roles:
        selected = [r.strip() for r in args.roles.split(",") if r.strip()]
    else:
        selected = list(mode_cfg.get("required_roles", []))
        selected.extend(mode_cfg.get("recommended_roles", []))
        # default_agents 가 지정되면 거기에 맞춰 자름
        default_n = mode_cfg.get("default_agents", len(selected))
        if default_n and len(selected) > default_n:
            selected = selected[:default_n]

    # 최소 요건 체크
    min_agents = mode_cfg.get("min_agents", 3)
    if len(selected) < min_agents:
        sys.exit(f"ERROR: mode '{args.mode}' 는 최소 {min_agents} agent 필요 (현재 {len(selected)})")

    # 존재하지 않는 role 필터
    unknown = [r for r in selected if r not in roles_catalog]
    if unknown:
        sys.exit(f"ERROR: 알 수 없는 role: {unknown}. ROLES.md 확인.")

    # maintainer-response 필수 context 체크
    if args.mode == "maintainer-response":
        missing = []
        if not args.maintainer_quote:
            missing.append("--maintainer-quote")
        if not args.invariant:
            missing.append("--invariant")
        if not args.pr_reference:
            missing.append("--pr-reference")
        if missing:
            sys.exit(f"ERROR: maintainer-response mode 는 다음 인자 필수: {missing}")

    target_files = resolve_target_files(args.target)

    extra_context = {
        "maintainer_quote": args.maintainer_quote,
        "invariant": args.invariant,
        "pr_reference": args.pr_reference,
        "relevant_paths": args.relevant_paths,
    }

    prompts = []
    for role in selected:
        tmpl = roles_catalog[role]
        prompt = render_prompt(tmpl, args.target, target_files, extra_context)
        prompts.append({"role": role, "prompt": prompt})

    out = {
        "target": args.target,
        "mode": args.mode,
        "target_files": target_files,
        "roles_selected": selected,
        "min_agents": min_agents,
        "consensus_rules": mode_cfg.get("consensus_rules", {}),
        "prompts": prompts,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
