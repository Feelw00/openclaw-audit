---
name: cross-review
description: "openclaw-audit 파이프라인의 multi-agent cross-review 실행. post-harness (gatekeeper 직후 real-problem 확정), pre-pr (PR 발행 직전 최종 검증), maintainer-response (메인테이너 리뷰 답변 전) 3 모드 지원. 사용자 허락 게이트 필수."
---

# cross-review Skill

CAL-003 (synthetic-only false positive) + CAL-006 (maintainer tone misstep) 사고 재발 방지를 위해 **여러 역할의 subagent 를 병렬 실행해 독립적 판정을 모으는** 검증 단계.

## 위치

```
/Users/lucas/Project/openclaw-audit/skills/cross-review/
├── SKILL.md           (이 파일)
├── ROLES.md           역할 카탈로그 (8 역할)
├── modes/
│   ├── post-harness.yaml
│   ├── pre-pr.yaml
│   └── maintainer-response.yaml
└── harness/
    ├── run.py         target + mode → 역할별 프롬프트 렌더
    └── aggregate.py   agent JSON 수집 → consensus + 매트릭스
```

## 호출 규약 (5단계)

### Step 1. 사용자 허락

**필수**. cross-review 는 subagent 5개까지 병렬 실행하므로 리소스 비용이 있음. 사용자가 명시적으로 허락하지 않으면 실행 금지.

허락 프롬프트 (mode 별):
- post-harness: "CAND-NNN 에 대해 cross-review 5 agent 병렬 실행할까? (y/n)"
- pre-pr: "SOL-NNNN PR 발행 직전 cross-review 3 agent 병렬 실행할까? (y/n)"
- maintainer-response: "메인테이너 리뷰 답변 전 cross-review 5 agent 병렬 실행할까? (y/n)"

### Step 2. harness/run.py 로 프롬프트 렌더

```bash
cd /Users/lucas/Project/openclaw-audit
/tmp/openclaw-audit-venv/bin/python skills/cross-review/harness/run.py \
  --target CAND-011 \
  --mode post-harness \
  [--roles positive-advocate,critical-devil,reproduction-realist,hot-path-tracer,upstream-dup-checker] \
  > /tmp/cross-review-CAND-011-prompts.json
```

출력 JSON:
```json
{
  "target": "CAND-011",
  "mode": "post-harness",
  "prompts": [
    { "role": "positive-advocate", "prompt": "너는 ... 에이전트다 ..." },
    { "role": "critical-devil", "prompt": "..." },
    ...
  ]
}
```

### Step 3. Agent tool 로 병렬 dispatch

메인 세션이 한 메시지 안에 여러 Agent tool 호출을 **병렬** 실행. 각 agent 는 Write tool 로 `/tmp/cross-review-<role>-<target>.json` 에 결과 저장.

### Step 4. harness/aggregate.py 로 집계

```bash
/tmp/openclaw-audit-venv/bin/python skills/cross-review/harness/aggregate.py \
  --target CAND-011 \
  --mode post-harness \
  > /tmp/cross-review-CAND-011-result.json
```

출력: consensus verdict + 매트릭스 마크다운. 동시에 `metrics/cross-review-<target>-<timestamp>.jsonl` 에 영구 기록.

### Step 5. 결정

aggregate 결과의 `decision` 필드가 `proceed / scope_down / abandon / upstream_wait / ...` 중 하나. 해당 action 에 따라 다음 단계:
- `proceed` → SOL 작성 또는 PR 발행
- `scope_down` → FIND retract + CAND 재편 + re-cross-review
- `abandon` → index.yaml 에 retracted_reason 기록 + local_state 전이
- `upstream_wait` → CAL-004 reference 달고 abandon

## 3 모드 요약

### Mode 1: post-harness (R-11)

**언제**: gatekeeper 가 판정 (approve/uncertain/reject_suspected) 한 직후, SOL 작성 전.
**기본 역할**: positive-advocate, critical-devil, reproduction-realist, hot-path-tracer, upstream-dup-checker
**합의 규칙**: real ≥ 3/5 → proceed, fix-insufficient ≥ 2 → scope-down, false-positive ≥ 1 → abandon

### Mode 2: pre-pr (CAL-003)

**언제**: fix commit + 재현 테스트 준비 완료, PR body 작성 직전.
**기본 역할**: positive-advocate, critical-devil, reproduction-realist
**합의 규칙**: 3/3 real → PR 발행, 그 외 → scope-down 또는 retract

### Mode 3: maintainer-response (R-10/CAL-006)

**언제**: openclaw 메인테이너 (CODEOWNERS / maintainers.md 인물) 가 CHANGES_REQUESTED 또는 COMMENT 남긴 직후.
**필수 context**: maintainer_quote, invariant, pr_reference
**기본 역할**: critical-devil, maintainer-invariant-hunter, schema-boundary-fuzzer, caller-surface-auditor, reproduction-realist
**합의 규칙**:
- 모든 agent 가 invariant_satisfied → 사과 + 재검토 결과 보고
- adjacent_violations 발견 → fix commit 먼저 → 답변에 commit SHA + "thanks for catching"
- 톤 체크리스트 (maintainer-response.yaml의 tone_checklist) 자동 확인

## 결과 저장

```
metrics/cross-review-{target}-{YYYYMMDD-HHMMSS}.jsonl
```

각 줄 = 한 agent 의 완전 JSON (공통 스키마). aggregate.py 는 이 JSONL 을 읽어 consensus 계산.

주간 리포트 (`metrics/report.py`) 에 cross-review 이력 포함.

## 역할 확장

새 역할 추가 시 `ROLES.md` 에 섹션 추가 → 해당 mode YAML 에 등록. 공통 스키마만 준수하면 aggregate.py 는 자동 호환.

## 한계

- 5 agent 병렬 실행 시 resource 비용 큼. 사용자 허락 gate 필수.
- agent 간 독립성 보장 필요: 다른 agent 의 중간 결과를 인용 금지 (프롬프트에 명시)
- consensus 규칙은 **heuristic**. 극단적 합의 (5/5) 라도 사람 최종 검토 권장.
- cross-review 는 pipeline 검증 단계이지 **SOL 작성 대체 아님** — real 판정 후 SOL 별도 작성.
