# 세션 플레이북

새 세션 시작 시 이 파일 하나만 보고 다음 액션 결정.

## 1. 부팅 절차 (CAL-004, CAL-007 반영)

### A. audit repo sync
```bash
cd /Users/lucas/Project/openclaw-audit
git pull --ff-only
/tmp/openclaw-audit-venv/bin/python skills/openclaw-audit/harness/local_state.py show | head -40
ls findings/drafts/ findings/ready/ issue-candidates/ solutions/ 2>/dev/null
```

### B. openclaw repo **반드시** upstream 최신화 + fork 동기화 (CAL-007 원천)

**FIND / CAND 작업 시작 전 필수**. stale 코드로 감사하면 이미 fixed 된 결함을 false positive 로 재탐지하게 됨.

```bash
cd /Users/lucas/Project/openclaw
git remote | grep -q upstream || git remote add upstream https://github.com/openclaw/openclaw.git
git fetch upstream main
BEHIND=$(git rev-list --count HEAD..upstream/main)
echo "behind upstream: $BEHIND commits"

# main 이 upstream/main 뒤면 fast-forward + fork 도 push
if [ "$BEHIND" -gt 0 ]; then
  git pull upstream main --ff-only
  git push origin main  # fork 도 동기화 (Feelw00/openclaw main)
fi

# 관심 영역 최근 변경 확인 (upstream 발견 fix 와 중복 방지)
git log upstream/main --since="2 weeks ago" --oneline -- src/plugins/ src/cron/ src/infra/ src/agents/ src/context-engine/ | head -30
```

### C. PR 작업 중 worktree 는 별도

**worktree 위치 규칙**: 모든 PR worktree 는 `/Users/lucas/Project/openclaw-worktrees/pr-NNN/` 에 생성.
`Project/` 루트 직접 생성 금지 (지저분함).

```bash
# 신규 worktree 생성 예
cd /Users/lucas/Project/openclaw
git worktree add /Users/lucas/Project/openclaw-worktrees/pr-NNN -b fix/<branch-name> upstream/main
```

open PR worktree 는 `fix/*` 브랜치라 main 업데이트와 독립. rebase 필요 시 `git rebase upstream/main` 로 개별 처리.

(venv 없으면: `python3 -m venv /tmp/openclaw-audit-venv && /tmp/openclaw-audit-venv/bin/pip install pyyaml`)

## 2. 결정 트리 (위에서 아래 순으로 체크)

| 조건 | 액션 |
|---|---|
| **메인테이너 (CODEOWNERS / maintainers.md 인물) CHANGES_REQUESTED / COMMENT** | **R-10 필수** — 답변 쓰기 전 3 agent cross-review (positive/critical/neutral) 먼저. 특히 critical agent 에 "메인테이너가 놓친 edge case 도 함께 탐색" 프롬프트. pushback 톤 금지. `calibration/CAL-006-maintainer-review-tone.md` 참조 |
| PR 에 follow-up commit push 완료 | **Greptile 자동 재리뷰 없음** — PR 에 `@greptile review and provide confidence score` 코멘트로 수동 트리거 |
| **PR 에 Codex / Greptile bot P1+ 지적** | **CAL-009 프로토콜** — 병렬 2-agent (positive + critical) 로 지적 검증 → 반박 가능 시 공손 reply + `gh api graphql resolveReviewThread` / 반영 필요 시 worktree 에서 수정 → push → Greptile 재트리거 |
| PR 에 bot 리뷰/코멘트 있음 (P2+) | 해당 worktree 에서 대응 → push → Greptile 재트리거 |
| `findings/drafts/` 에 파일 있음 | `validate.py --all --move` |
| `findings/ready/` ≥ 2 건 (같은 도메인 누적) | clusterer 페르소나 호출 |
| `issue-candidates/` 에 gatekeeper 미평가 CAND 있음 (`state: pending_gatekeeper`) | gatekeep 3-step (sanitize → agent → apply --shadow) |
| gatekeeper 판정 끝난 CAND + 사용자 허락 | **R-11 post-harness cross-review** — `skills/cross-review/` 스킬 사용. `harness/run.py --target CAND-NNN --mode post-harness` 로 프롬프트 렌더 → Agent tool 로 병렬 실행 → `aggregate.py` 로 집계. 허락 없이 자동 실행 금지. 아래 §7.1 참조 |
| gatekeeper `approve` + cross-review 2/3 이상 real | 사람 최종 검토 → SOL 작성 착수 |
| gatekeeper `uncertain` / `needs-human-review` | cross-review 결과로 approve/scope-축소/abandon 결정 |
| `solutions/` 에 `status: drafted` SOL 있음 | worktree 에서 재현 테스트 + fix → PR 경로 |
| 위 전부 없음 | 새 셀 선택 (아래 §3) |

## 3. 새 셀 선택

```bash
# 아직 안 돌린 Phase 1 셀 확인
grep -A3 "phase: 1" grid.yaml | grep -E "^  - id:|state:"

# 현재 Phase 1+2+3+4 상태 (2026-04-24 기준, 17 셀 전부 감사 완료 — Phase 4 본 세션 추가)
#
# Phase 1 (5/5 done):
#   ✓ plugins-memory               — CAND-001 abandoned
#   ✓ plugins-lifecycle            — CAND-005 abandoned (CAL-004 upstream superseded)
#   ✓ cron-concurrency             — CAND-002/003 abandoned
#   ✓ agents-registry-memory       — CAND-004 abandoned (CAL-001 maintainer reject)
#   ✓ infra-process-error-boundary — CAND-006 abandoned (CAL-003 synthetic-only)
#
# Phase 2 (5/5 done):
#   ✓ plugins-error-boundary       — 0 FIND (CAL-007 fresh 재감사, upstream 이미 fix)
#   ✓ cron-memory                  — 0 FIND (전 Map/timer 방어 확인)
#   ✓ infra-retry-concurrency      — adjacent: CAND-008 abandoned + CAND-009 open PR #68543
#   ✓ infra-process-memory         — adjacent: CAND-007 abandoned
#   ✓ agents-registry-concurrency  — adjacent: CAND-010 abandoned + CAND-011 open PR #68669
#
# Phase 3 (6/6 done, 2026-04-22 잔여 3 셀 감사 완료):
#   ✓ auto-reply-concurrency       — CAND-012 → PR #68839 (proceed) + CAND-013 scope_down
#   ✓ gateway-memory               — CAND-014 → PR #68842 ✅ MERGED + CAND-015 → PR #68848 + CAND-016 abandoned (CAL-008)
#   ✓ gateway-error-boundary       — CAND-017/018 abandoned (synthetic + observability scope 밖)
#   ✓ gateway-concurrency          — 3 FIND → CAND-021 approve(cross-review 대기) / CAND-022 abandoned (CAL-008 PR #68341) / CAND-023 → SOL-0006 + PR #70142 (리뷰 대기)
#   ✓ channels-error-boundary      — 2 FIND → CAND-019 abandoned (primary-path: 4 adapter swallow) / CAND-020 abandoned (primary-path: 3 caller 2-arg then)
#   ✓ channels-lifecycle           — 2 FIND validate REJECT (YAML frontmatter error), 별도 복구 작업 대기
#
# Phase 4 (4/4 done, 2026-04-24 본 세션 — 메인테이너 공개 우선순위 "memory/plugin loading/cron/reliability" 정면):
#   ✓ plugins-concurrency          — 0 FIND (CAL-008 dup: 2a283e87a7+59d07f0ab4+e8fd148437+c95507978f+d1e3ed3743+13821fd54b+cc343febfb 7 fix 커밋으로 sync 강제+rollback 확립, file-lock race 는 PR #67876 bandaid 인지)
#   ✓ context-engine-memory        — 0 FIND (신규 도메인. CAL-008 dup: 59d07f0ab4 로 clearContextEnginesForOwner primary cleanup 이미 반영. rejectedKeys Set 은 literal type bounded. 타이머/리스너 0건. domain-notes/context-engine.md 신규 작성)
#   ✓ cron-error-boundary          — 0 FIND (resolveStorePath throw 불가, onEvent 타입 sync 라 async injection compile-time 차단, onTimer try/finally self-healing. upstream 6주 cron fix 중 error-boundary 축 없음)
#   ✓ cron-lifecycle               — 2 FIND (P2) → CAND-024 epic (activeJobIds partial merge gap: upstream 7d1575b5df (#60310) 가 runDueJob/executeJob 만 수정하고 startup catchup + manual run 간과. related issue #68157 OPEN 2026-04-23 증상 보고 중)
#
# 살아있는 PR 6건 (2026-04-25 기준, upstream/main 최신 동기화 HEAD dd78b7f773 — 직전 세션 b7fba2100f 에서 884 commits fast-forward):
#   • #68543 (CAND-009, infra-retry, head acc85fe0ff) — steipete invariant 이미 반영됨. 메인테이너 재리뷰 대기
#   • #68669 (CAND-011, agents-registry, head 00cab4264f) — Codex P2 2라운드 resolved. 메인테이너 리뷰 대기
#   • #68839 (CAND-012, auto-reply drain identity guard, head 1236d56668) — 리뷰 대기
#   • #68848 (CAND-015, nodeWakeById cleanup, head 5b9103c7e0) — 리뷰 대기
#   • **#70142 (CAND-023, gateway chat.send attachment race, head 39ccb9c4a2, 2026-04-25 rebase)** — upstream chat.ts 충돌 (assistant display + scheduleChatHistoryManagedImageCleanup 추가) 해결 후 force-push. 우리 patch 영역 (L2238-2277 in_flight re-check + offloadedRefs cleanup) 위치 컨텍스트 정확. gateway/server-methods 43 files / 496 tests + check + build green. Greptile 수동 재트리거 완료. 리뷰 대기
#   • **#71040 (CAND-024→SOL-0007, cron active-jobs symmetry, head c2cf00742e, 2026-04-24 발행)** — Greptile 5/5 자동 통과. pre-pr cross-review 2/3 real + 1/3 fix-insufficient → **scope-down 반영: Fixes #68157 → Related #68157 (partial)**. critical-devil 지적: ops.ts:100-106 의 startup 무조건 runningAtMs clear 가 #68157 "already-running survives restart" 증상을 restart 로 self-heal. mechanism + fix 자체는 3/3 real 인정. **상태 변화 (2026-04-25)**: #68157 vincentkoc 이 #40868 cron-lifecycle cluster dedupe 로 closed (#40868 도 closed). upstream PR #71547 (924271385b) 가 ops.ts start() 의 runningAtMs 처리 강화 — **다른 필드 (runningAtMs vs activeJobIds), 직교**. 로컬 rebase textually clean (push 안 함). 메인테이너 리뷰 대기
#
# merged: #68842 (CAND-014, 파이프라인 첫 merge), #63105 (파이프라인 외 cron-store split, 2026-04-20 merged). warn=7 / block=10 기준 active 6 → warn 경계 근접.
#
# 잔여 미처리 (다음 세션 우선순위 순):
#   1. CAND-021 (gateway/send idempotencyKey race) — approve@high, hot-path=5, P1 → post-harness 5-agent cross-review 대기 (사용자 허락 필요)
#   2. PR #71040 본문 갱신 검토 — "Related #68157" 이 closed-as-dedupe 라 stale. 메인테이너 visibility 우려로 코드 변경 없는 본문-only 편집은 노이즈 0 (Greptile/메인테이너 알림 안 감). 선택사항
#
# 신규 셀 정의 필요 시 grid.yaml §types 에 id 추가 후 §cells 확장.
# 다음 Phase 5 후보 (보류 중): agents-registry-lifecycle (PR #68669 리뷰 완료 후 착수), mcp-memory / mcp-lifecycle (신규 도메인 경계 조사 필요), cron-concurrency 신축 (이미 audit 된 영역이라 우선순위 낮음).
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

## 7. Cross-review 단계 (R-11 post-harness + CAL-003 PR 직전)

### 7.0 실행 경로 (cross-review 스킬)

모든 cross-review 는 `skills/cross-review/` 스킬 사용. 5단계 프로토콜:

1. **사용자 허락** (gate 필수)
2. `run.py --target <T> --mode <M>` 로 프롬프트 JSON 렌더
3. Agent tool 병렬 dispatch (한 메시지, 여러 tool_use 블록)
4. `aggregate.py --target <T> --mode <M>` 로 집계 + `metrics/cross-review-*.jsonl` 영구 기록
5. `primary_decision.action` 에 따라 다음 단계

상세: `skills/cross-review/SKILL.md`. 역할 카탈로그: `skills/cross-review/ROLES.md`. 모드 프리셋: `skills/cross-review/modes/*.yaml`.

### 7.1 Post-harness cross-review (severity gate + 사용자 허락)

**트리거 조건** (CAL-008 이후, Option C 하이브리드):

| severity | gatekeeper verdict | 행동 |
|---|---|---|
| P0 / P1 / P2 | approve / uncertain | **사용자 허락 후 cross-review 필수** — 메인테이너 visibility 높음, false positive 비용 큼 |
| P3 | approve | **skip cross-review** — gatekeeper 의 upstream-dup + primary-path inversion 만으로 커버. SOL 작성 가치는 사용자 판단 |
| P3 | uncertain | 과거 패턴 (CAND-007/008) 대로 기본 abandon. cross-review 는 사용자 요청 시만 |
| 전체 | reject_suspected | skip cross-review — 이미 reject |

이 gate 의 근거:
- CAL-008: gatekeeper 에 upstream-dup check 가 추가되면 P3 단순 leak 은 단독 판단으로 충분
- cross-review 비용 = 5 agent × CAND. P3 에 5-agent 투입은 오버엔지니어링
- P2+ 는 false positive 리스크가 실제 PR/메인테이너 관계 비용으로 이어져 추가 필터 가치 있음

**사용자 허락** 은 P2+ 에서 여전히 필수 (5 agent 병렬 리소스).

mode: `post-harness`.

기본 역할 세트 (5개 기본, 3 최소):

| 역할 | 프롬프트 초점 |
|---|---|
| **positive-advocate** | "왜 merge 해야 하는가" — 문제 존재 증거, production 영향 경로, 메인테이너 수용 가능성 |
| **critical-devil** | "왜 close 해야 하는가" — primary-path inversion, CAL-001~005 재확인, unconditional guard 재탐색 |
| **reproduction-realist** | 재현 테스트가 production hot-path 와 동일 branch 인가 (CAL-003). synthetic race 위험. fake timer / mock 의존도. |
| **hot-path-tracer** | production caller stack 추적 — 문제 경로가 정상 사용자 시나리오에서 taken 되는가 |
| **upstream-dup-checker** | `git log upstream/main` 에서 동일/유사 fix 이미 있는지 (CAL-004) |

판정 enum (각 에이전트가 반드시 반환):
- `real-problem-real-fix`
- `real-problem-fix-insufficient`
- `synthetic-only` (test path ≠ production hot-path)
- `false-positive` (primary cleanup 이 이미 처리)
- `upstream-duplicate` (이미 upstream 에서 해결)

**결과 해석**:
- 3/3 real → approve → SOL 작성
- 2/3 real + 1 scope 우려 → scope 축소 후 진행
- 긍정 시점마저 real 판정 못 함 → false-positive 가능성 높음 → abandon
- 재현 에이전트가 synthetic-only → test 를 production branch 로 재작성하거나 abandon

**CAL 추가 반영**:
- CAL-001: critical agent 에 primary-path inversion 필수 포함
- CAL-003: reproduction realist 필수 포함
- CAL-004: upstream dup checker 포함 권장

### 7.2 PR 발행 직전 cross-review (CAL-003, mode: pre-pr)

PR 제출 **직전** 3 agent 병렬 재검증. fix 포함 최종 diff 기준.
합의 2/3 미만이면 retract 또는 scope 축소. **긍정 시점마저 real 판정 못 하면** 거의 확실한 false positive.

### 7.3 메인테이너 리뷰 답변 전 cross-review (R-10/CAL-006, mode: maintainer-response)

메인테이너 CHANGES_REQUESTED / COMMENT 받으면 답변 전 `skills/cross-review/harness/run.py --target PR#NNNNN --mode maintainer-response --maintainer-quote "..." --invariant "..." --pr-reference "PR#NNNNN @<sha>"` 실행.
기본 5 agent: critical-devil, maintainer-invariant-hunter, schema-boundary-fuzzer, caller-surface-auditor, reproduction-realist.
톤 체크리스트: `modes/maintainer-response.yaml` 의 `tone_checklist`.

## 8. 긴급 참조

- 운영 상세: `OPERATIONS.md`
- 기여 규칙: `openclaw-contribution.md`
- 페르소나 규율: `agents/memory-leak-hunter.md` §"필수 규율 R-1~R-7"
- **과거 실패 회고 (반드시 읽기)**:
  - `calibration/CAL-001-maintainer-verdict-CAND-004.md` (메인테이너 post-merge reject, R-5 원천)
  - `calibration/CAL-002-greptile-review-CAND-005.md` (Greptile bot partial gap)
  - `calibration/CAL-003-cross-review-retract-CAND-006.md` (self-caught synthetic-only, R-7 원천)
  - `calibration/CAL-004-upstream-merge-lag-CAND-005.md` (upstream superseded, R-8 원천)
  - `calibration/CAL-005-bot-contradiction-boundary.md` (bot contradiction, R-9 원천)
  - `calibration/CAL-006-maintainer-review-tone.md` (메인테이너 톤 실수, **R-10 원천 — 가장 위험**)
  - `calibration/CAL-007-stale-fetch-before-find.md` (stale upstream 기반 FIND, NEXT.md §1 fast-forward 강제 원천)
  - `calibration/CAL-008-gatekeeper-upstream-dup-gap.md` (gatekeeper upstream-dup check 필수 원천)
  - `calibration/CAL-009-codex-bot-review-rebuttal.md` (Codex/Greptile bot 지적 병렬 검증 → 반박/반영 결정 프로토콜)
- **PR 트래커 (모든 내 openclaw PR)**: `openclaw-pr-tracker.md`
  - 파이프라인 외 PR (#63105 cron-store split) 포함
  - Greptile 재리뷰 수동 트리거 절차
