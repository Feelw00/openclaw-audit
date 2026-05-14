---
name: real-behavior-proof
description: "openclaw 의 외부 PR 라벨 정책 (`triage: needs-real-behavior-proof` / `proof: supplied` / `proof: sufficient`) 에 맞춰 SOL 작성 전(pre-sol)과 SOL 작성 후(post-sol) production-like 환경에서 결함 재현/검증을 자동화. worktree 별 pnpm build + isolated OPENCLAW_HOME + scenario 실행 + sqlite/Map.size 측정 + PR body 6 필드 evidence 생성."
---

# real-behavior-proof Skill

CAL-009/CAL-010 이후 openclaw 가 외부 PR 에 real behavior proof 라벨 정책을 도입함
(`scripts/github/real-behavior-proof-policy.mjs`). PR body `## Real behavior proof` 섹션에
6 필드 (behavior/environment/steps/evidence/observedResult/notTested) 가 채워져 있고
evidence 가 mock 만이 아니면 `proof: supplied` 자동 부여, clawsweeper bot 이 real wall-clock
측정을 보면 `proof: sufficient` 추가 부여.

이 skill 은 그 evidence 수집을 **SOL 작성 전/후** 두 단계로 자동화한다:

1. **pre-sol**: gatekeeper approve + post-harness cross-review proceed 직후, **without-fix 빌드만**
   으로 production-like 환경에서 결함 재현. false positive 사전 차단 + post-sol baseline 확보.
2. **post-sol**: SOL chosen_fix 결정 후, **with-fix vs without-fix 두 빌드 비교**. PR body 6 필드
   evidence 텍스트 산출 → PR 발행 시 그대로 paste.

**모든 severity 에 적용 (P3 포함).** 재현 불가능한 경우 (외부 OAuth/채널 의존 등) 강등 없이
별도 상태 (`blocked-external-dep`) 로 마킹하고 진행.

## 위치

```
/Users/lucas/Project/openclaw-audit/skills/real-behavior-proof/
├── SKILL.md           (이 파일)
├── modes/
│   ├── pre-sol.yaml   single build, scenario.evaluate_pre()
│   └── post-sol.yaml  pair build (base + head), scenario.evaluate_post() + render_pr_evidence()
├── scenarios/
│   ├── cron-manual-run.py     PR #78243 baseline (sqlite task_runs status='lost')
│   ├── gateway-map-size.py    SOL-0004/CAND-014 패턴 (Map.size 측정)
│   └── mcp-pending-ttl.py     PR #71648 패턴 (real wall clock TTL)
└── harness/
    ├── run.py             entry point (mode/scenario dispatch)
    ├── build.py           worktree 자동 생성 + pnpm build
    ├── env_isolate.py     OPENCLAW_HOME 격리 + 임시 sqlite/포트 + OAuth read-only mount
    ├── render_proof.py    openclaw policy 6 필드 PR body 렌더 + 검증
    └── state.py           proofs/PROOF-*.md + SOL frontmatter + local-state transition

/Users/lucas/Project/openclaw-audit/proofs/   (영구 evidence 디렉터리)
└── PROOF-{target}-{pre|post}-{YYYYMMDD-HHMMSS}.md
```

## 호출 규약 (5단계)

### Step 1. 사용자 허락 (gate 필수)

build (각 ~10-30분) + scenario 실행 (시나리오별 5-30분) 비용. 사용자 명시적 허락 없이 실행 금지.

허락 프롬프트 (modes/<mode>.yaml 의 `permission_prompt`):
- pre-sol: "{target} 에 대해 pre-sol real behavior proof 실행 (build + scenario 1회). 진행할까? (y/n)"
- post-sol: "{target} 에 대해 post-sol real behavior proof 실행 (build × 2 + scenario × 2). 진행할까? (y/n)"

### Step 2. harness/run.py 실행

```bash
cd /Users/lucas/Project/openclaw-audit

# pre-sol (without-fix 단독 재현)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 \
  --mode pre-sol \
  --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> \
  --trials 100

# post-sol (with/without 비교)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 \
  --mode post-sol \
  --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> --head-sha <fix HEAD> \
  --trials 100
```

내부 흐름 (자동):
1. `env_isolate.isolated_home()` — `/tmp/proof-{uuid}/.openclaw` 격리 (OAuth profile 만 read-only 복사)
2. `build.build_single()` 또는 `build_pair()` — `/Users/lucas/Project/openclaw-worktrees/proof-{uuid}-*/` worktree + `pnpm install` + `pnpm build`
3. `scenario.run_scenario()` — `node <worktree>/openclaw.mjs ...` 시나리오 N trial 실행 + 측정
4. `scenario.evaluate_pre()` 또는 `evaluate_post()` — measurement → status (collected/unreproducible/blocked-*)
5. `render_proof.render_pr_body_section()` — post-sol + collected 시 6 필드 PR body 텍스트 생성

### Step 3. 결과 영속화 (자동)

`harness/state.py` 가 세 곳에 동시 기록:
1. `proofs/PROOF-{target}-{pre|post}-{ts}.md` — 영구 evidence (frontmatter + measurements + PR body section)
2. `solutions/SOL-NNNN.md` frontmatter 의 `pre_sol_proof` / `post_sol_proof` 객체 갱신
3. `local-state/state.yaml` + `history.jsonl` transition (`proof-{collected|unreproducible|blocked|skipped}-{pre|post}`)

### Step 4. stdout JSON 결과 확인

```json
{
  "target": "SOL-0004",
  "mode": "post-sol",
  "scenario": "gateway-map-size",
  "status": "collected",
  "proof_record": "proofs/PROOF-SOL-0004-post-20260514-014230.md",
  "pr_body_section_included": true,
  "next_action": "pre-pr cross-review → PR 발행 (PR body 에 pr_body_section paste)"
}
```

### Step 5. 결정 + 다음 단계

mode YAML 의 `next_action_by_status` 참조:

| mode    | status                  | 다음 액션 |
|---------|-------------------------|----------|
| pre-sol | collected               | 사람 최종 검토 → SOL 작성 착수 |
| pre-sol | unreproducible          | CAND abandon (false-positive-by-reproduction) 또는 scope 재검토 |
| pre-sol | blocked-external-dep    | SOL 작성 진행 (post-sol 도 blocked 가능성 인지) |
| post-sol| collected               | pre-pr cross-review → PR 발행 (PR body 에 `pr_body_section` paste) |
| post-sol| unreproducible          | fix 효과 없음 → SOL abandon 또는 chosen_fix 재선택 |
| post-sol| blocked-external-dep    | 회귀 테스트만으로 PR 발행 (PR body 에 blocked 사유 명시, sufficient 미부여 수용) |
| both    | blocked-env             | 환경 setup 재시도 또는 시나리오 변경 |
| both    | skipped-by-user         | 사용자 책임 |

## openclaw policy 호환

`harness/render_proof.py` 는 `/Users/lucas/Project/openclaw/scripts/github/real-behavior-proof-policy.mjs`
와 형식적으로 일치한다:

- 6 필드 라벨 (`Behavior or issue addressed` / `Real environment tested` / ...) 정확 mirror.
- evidence 본문이 `evidenceDescriptorRegex` (fenced code block) 또는 `liveCommandRegex` (openclaw/node/docker/curl/gh/...) 통과 보장.
- guard `_check_no_inline_heading` 가 line-start `# ` 패턴을 raise (policy `extractFieldValue` 가 거기서 break 해 필드 missing 처리됨).

검증: smoke test 가 `node -e "import('./scripts/github/real-behavior-proof-policy.mjs').then(m => m.evaluateRealBehaviorProof({pullRequest: {body, ...}}))"` 직접 호출, `status: "passed"` 확인.

## 시나리오 작성 규약

새 시나리오 추가 시 `scenarios/<name>.py` 에 다음 export 필수:

```python
SCENARIO_NAME = "<dash-case-name>"
REQUIRES_EXTERNAL_DEP = bool  # OAuth/channel 등 외부 호출 필요?

def run_scenario(*, node_entry: Path, env: dict, sqlite_path: Path, trials: int, **kwargs) -> dict:
    """N trial 실행 + 측정. measurements dict 반환."""

def evaluate_pre(measurements: dict) -> str:
    """pre-sol status enum 반환 (collected/unreproducible/blocked-*)."""

def evaluate_post(without_fix: dict, with_fix: dict) -> str:
    """post-sol status enum 반환."""

def render_pr_evidence(without_fix: dict, with_fix: dict) -> dict:
    """6 필드 dict (behavior/environment/steps/evidence/observed_result/not_tested) 반환.
    render_proof.render_pr_body_section 의 입력."""
```

본문에 line-start `# ` markdown heading 절대 금지 (policy break 조건).

## 격리 + 안전성

- production `~/.openclaw/` 손상 방지: `env_isolate.isolated_home()` 가 `OPENCLAW_HOME=/tmp/proof-{uuid}/.openclaw` redirect.
- production OAuth profile 는 read-only (`chmod 0o400`) 로만 복사. token 위변조 불가.
- gateway 포트 충돌 방지: `_alloc_free_port(17000-17999)` 로 임시 할당 (production 18789 회피).
- worktree 는 `/Users/lucas/Project/openclaw-worktrees/proof-{uuid}-*/` (NEXT.md §C 규약 준수).
- cleanup: `with isolated_home(...)` + `pair.cleanup()` 으로 자동 rmtree + `git worktree remove`.

## 비목표

- openclaw 의 `proof: sufficient` 자동 부여 메커니즘 영향 (clawsweeper bot 외부 결정).
- cross-review skill 단계 대체 (post-harness/pre-pr/maintainer-response 그대로 별도).
- 머지 완료된 5 PR 의 사후 evidence 강화 (이미 완료, 비목표).
