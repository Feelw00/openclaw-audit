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
| PR #68489 등 openclaw 에 올린 PR 에 리뷰/봇 코멘트 있음 | `/Users/lucas/Project/openclaw-pr` 에서 대응 → force push |
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

## 7. 긴급 참조

- 운영 상세: `OPERATIONS.md`
- 기여 규칙: `openclaw-contribution.md`
- 페르소나 규율: `agents/memory-leak-hunter.md` §"필수 규율 R-1~R-5"
- **과거 false positive 회고 (반드시 읽기)**: `calibration/CAL-001-maintainer-verdict-CAND-004.md`
- PR #68511 리뷰: `https://github.com/openclaw/openclaw/pull/68511` (CAND-006)
- PR #68489 (closed): `https://github.com/openclaw/openclaw/pull/68489` (CAND-004, retracted by maintainer)
