# 세션 플레이북

새 세션 시작 시 이 파일 하나만 보고 다음 액션 결정.

## 1. 부팅 3 명령

```bash
cd /Users/lucas/Project/openclaw-audit
git pull --ff-only
/tmp/openclaw-audit-venv/bin/python skills/openclaw-audit/harness/local_state.py show | head -40
ls findings/drafts/ findings/ready/ issue-candidates/ solutions/ 2>/dev/null
```

(venv 없으면: `python3 -m venv /tmp/openclaw-audit-venv && /tmp/openclaw-audit-venv/bin/pip install pyyaml`)

## 2. 결정 트리 (위에서 아래 순으로 체크)

| 조건 | 액션 |
|---|---|
| PR 에 follow-up commit push 완료 | **Greptile 자동 재리뷰 없음** — PR 에 `@greptile review and provide confidence score` 코멘트로 수동 트리거 (`openclaw-pr-tracker.md` §"Greptile 재리뷰" 참조) |
| PR 에 리뷰/봇 코멘트 있음 | 해당 worktree 에서 대응 → push → Greptile 재트리거 |
| `findings/drafts/` 에 파일 있음 | `validate.py --all --move` |
| `findings/ready/` ≥ 2 건 (같은 도메인 누적) | clusterer 페르소나 호출 |
| `issue-candidates/` 에 gatekeeper 미평가 CAND 있음 (`state: pending_gatekeeper`) | gatekeep 3-step (sanitize → agent → apply --shadow) |
| gatekeeper 판정 `needs-human-review` CAND 있음 | 사람 검토 → approve/reject 결정, SOL 작성 착수 |
| `solutions/` 에 `status: drafted` SOL 있음 | worktree 에서 재현 테스트 + fix → PR 경로 |
| 위 전부 없음 | 새 셀 선택 (아래 §3) |

## 3. 새 셀 선택

```bash
# 아직 안 돌린 Phase 1 셀 확인
grep -A3 "phase: 1" grid.yaml | grep -E "^  - id:|state:"

# 현재 Phase 1 상태 (2026-04-18 기준)
# ✓ plugins-memory         — done (CAND-001 uncertain)
# ✓ cron-concurrency       — done (CAND-002 uncertain, CAND-003 retracted)
# ✓ agents-registry-memory — done (CAND-004 published, PR #68489)
# ☐ plugins-lifecycle      — 페르소나: plugin-lifecycle-auditor
# ☐ infra-process-error-boundary — 페르소나: error-boundary-auditor
```

셀 실행 프롬프트 템플릿 (Agent 도구, `subagent_type=general-purpose`):
```
너는 {페르소나 이름} 페르소나다.
/Users/lucas/Project/openclaw-audit/agents/{페르소나}.md 완전히 읽고 R-1~R-4 엄수.

openclaw repo: /Users/lucas/Project/openclaw
audit repo   : /Users/lucas/Project/openclaw-audit
셀: {cell-id}
allowed_paths: {grid.yaml 해당 도메인}

산출물 (Write tool 필수):
- findings/drafts/FIND-{cell-id}-{NNN}.md (최대 3~4 건)
- domain-notes/{domain}.md append

R-3 Grep 결과를 counter_evidence.reason 에 명시.
```

## 4. Phase 2 로 확장 (Phase 1 완료 후)

```
cells 에서 phase: 2 항목 찾기 (plugins-error-boundary, cron-memory,
infra-retry-concurrency, infra-process-memory, agents-registry-concurrency)
```

## 5. 졸업 조건 (shadow → 자동화)

```bash
wc -l metrics/shadow-runs.jsonl metrics/human-verdicts.jsonl metrics/self-consistency.jsonl
# 목표: 50 / 10 / 10
```

현재 (2026-04-18): 4 / 0 / 0 → 갈 길 멀다. 매 세션 +1~2 shadow 씩 누적.

## 6. 세션 종료

```bash
# 1. 변경사항 commit
git add -A
git status --short
git commit -m "<action-oriented 요약>"

# 2. push
git push

# 3. 다음 세션을 위해 상태 간단 메모 (선택)
echo "next: {한 줄}" >> orchestrator-log.md
```

## 7. PR 발행 전 cross-review (CAL-003 필수)

PR 제출 **직전** 3 에이전트 병렬 호출 — 긍정/비판/중립:

```
Agent × 3 (general-purpose, 병렬):
- Positive: "왜 머지해야 하는가" 증거 수집
- Critical: "왜 close 해야 하는가" 반증 (primary-path inversion 적극)
- Neutral: 균형
```

합의 2/3 미만이면 retract 또는 scope 축소. 특히 **긍정 시점마저 real 판정 못 하면** 거의 확실한 false positive.

판정 enum:
- `real-problem-real-fix`
- `real-problem-fix-insufficient`
- `synthetic-only` (test path ≠ production hot-path)
- `false-positive` (primary cleanup 이 이미 처리)

## 8. 긴급 참조

- 운영 상세: `OPERATIONS.md`
- 기여 규칙: `openclaw-contribution.md`
- 페르소나 규율: `agents/memory-leak-hunter.md` §"필수 규율 R-1~R-7"
- **과거 false positive 회고 (반드시 읽기)**:
  - `calibration/CAL-001-maintainer-verdict-CAND-004.md` (메인테이너가 잡음)
  - `calibration/CAL-002-greptile-review-CAND-005.md` (Greptile bot)
  - `calibration/CAL-003-cross-review-retract-CAND-006.md` (self-caught, R-7 원천)
- **PR 트래커 (모든 내 openclaw PR)**: `openclaw-pr-tracker.md`
  - 파이프라인 외 PR (#63105 cron-store split) 포함
  - Greptile 재리뷰 수동 트리거 절차
