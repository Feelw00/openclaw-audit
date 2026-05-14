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
| gatekeeper `approve` + cross-review 2/3 이상 real | **R-12 pre-sol real behavior proof** — `skills/real-behavior-proof/` 스킬 (mode: pre-sol). without-fix 빌드 단독으로 production-like 환경 결함 재현. 모든 severity 적용. 아래 §7.5 참조 |
| pre-sol `collected` / `blocked-external-dep` / `blocked-env` | 사람 최종 검토 → SOL 작성 착수 (blocked 사유는 SOL frontmatter `pre_sol_proof.status` 에 trace) |
| pre-sol `unreproducible` | CAND abandon (false-positive-by-reproduction). cross-review 가 놓친 false positive 의 마지막 안전망 |
| gatekeeper `uncertain` / `needs-human-review` | cross-review 결과로 approve/scope-축소/abandon 결정 |
| `solutions/` 에 `status: drafted` SOL + `chosen_fix` 결정 + 사용자 허락 | **R-13 post-sol real behavior proof** — `skills/real-behavior-proof/` 스킬 (mode: post-sol). with-fix vs without-fix 비교 + 6 필드 PR body evidence 산출. §7.5 참조 |
| post-sol `collected` | pre-pr cross-review (§7.2) → PR 발행 (PR body 에 `pr_body_section` paste, `proof: supplied` 자동 부여) |
| post-sol `blocked-external-dep` / `blocked-env` | 회귀 테스트만으로 PR 발행 + PR body 에 blocked 사유 명시 (`proof: sufficient` 미부여 수용) |
| post-sol `unreproducible` | fix 효과 없음 → SOL abandon 또는 `chosen_fix` 재선택 |
| 위 전부 없음 | 새 셀 선택 (아래 §3) |

## 3. 새 셀 선택

```bash
# 아직 안 돌린 Phase 1 셀 확인
grep -A3 "phase: 1" grid.yaml | grep -E "^  - id:|state:"

# 감사 완료 셀 인벤토리 (axis 중복 회피용 — 새 셀 진입 시 참조).
# 상세 outcome / PR / 종결 사유는 issue-candidates/index.yaml + solutions/SOL-*.md + openclaw-pr-tracker.md.
#
# Phase 1 (5/5):
#   ✓ plugins-memory / plugins-lifecycle / cron-concurrency / agents-registry-memory / infra-process-error-boundary
#
# Phase 2 (5/5):
#   ✓ plugins-error-boundary / cron-memory / infra-retry-concurrency / infra-process-memory / agents-registry-concurrency
#
# Phase 3 (6/6):
#   ✓ auto-reply-concurrency / gateway-memory / gateway-error-boundary / gateway-concurrency
#   ✓ channels-error-boundary / channels-lifecycle (2 FIND validate REJECT — YAML frontmatter 복구 작업 대기)
#
# Phase 4 (4/4):
#   ✓ plugins-concurrency / context-engine-memory / cron-error-boundary / cron-lifecycle
#
# Phase 5 (1/N — "plugin loading" 영역):
#   ✓ mcp-memory  (cap/FIFO 후속 v2 셀 보류 — PR #71648 머지 후 착수)
#
# OPEN openclaw PR (다음 세션에서 상태 확인 우선):
#   • #68669 (CAND-011, agents-registry-concurrency) — `proof: supplied+sufficient` + `triage: refactor-only`. 무대응 유지.
#   • #71648 (CAND-025→SOL-0008, mcp channel-bridge TTL sweeper) — `proof: supplied` 만. 메인테이너 리뷰 대기.
# 상세 + 종결된 PR / merged history → openclaw-pr-tracker.md.
#
# 다음 세션 액션 우선순위 (잔여):
#
#   ## 0. 최우선 — 활성 9 CAND pre-sol real behavior proof (사용자 결정 2026-05-14)
#
#   사용자 지시: "다중 세션으로 전부 proof. 실제 테스트 후 문제가 아니면 SOL 작성 불필요" — pre-sol 을 false-positive
#   filter 로 SOL 작성 *전* 게이트. NEXT.md 결정 트리 (`pre-sol unreproducible → CAND abandon`,
#   `pre-sol collected → SOL 작성 착수`) 와 정합.
#
#   대상 9 CAND (gatekeeper approve + cross-review proceed/proceed-with-caveat):
#   • CAND-026 — agents-registry-error-boundary (restoreSubagentRunsOnce silent catch + set-before-action)
#   • CAND-030 — agents-registry-lifecycle (markSubagentRunTerminated 의 clearPendingLifecycleTimeout 누락)
#   • CAND-031 — auto-reply/queue/drain self-recurse retry (max-attempts/backoff/dead-letter 부재)
#   • CAND-032 — auto-reply/reply-run-registry void backend.queueMessage unhandled rejection
#   • CAND-033 — auto-reply/queue/drain collect mode auth-groups snapshot
#   • CAND-037 — context-engine/registry resolveContextEngine validation fallback dispose 부재
#   • CAND-038 — gateway/ws-connection chatAbortControllers ownerConnId abort 누락
#   • CAND-039 — gateway/server-runtime-services recovery IIFE SIGTERM 미가드
#   • CAND-040 — infra/approval-handler-runtime activeEntries deliverTarget 중 onStopped race
#
#   사전 작성 완료 (2026-05-14, 직전 세션):
#     skills/real-behavior-proof/scenarios/proof-CAND-{026,030,031,032,033,037,038,039,040}.py
#     — 각 CAND 의 측정 binary, 필요 __test hook 목록, evaluate_pre/post 규칙, render_pr_evidence
#       6 필드를 docstring 에 명시.
#     — 모두 REQUIRES_EXTERNAL_DEP=False (in-process measurement).
#     — harness/run.py 가 file-stem 으로 동적 import 하므로 `--scenario proof-CAND-NNN` 즉시 작동.
#     — hook 미존재 시 시나리오가 stdout 에 `{"skipped": "..."}` 출력 → blocked-env 로 분류.
#
#   잔여 작업 분류 (2026-05-14 시나리오 사전작성 후):
#
#   | 유형 | CAND | 잔여 작업 | 1건당 추정 |
#   |---|---|---|---|
#   | hook 기존 가정 | 026, 032 | 빌드 + pre-sol 실행 (skipped 잡히면 hook 1-2줄 추가) | 0.5-1.5h |
#   | __test export hook 추가 필요 | 030, 031, 033, 037, 040 | hook 1-2줄 instrumentation + 빌드 + 실행 | 1-2h |
#   | gateway fake-deps 인프라 필요 | 038, 039 | __test.installFakeDeps / setRecoveryProbe stub + 빌드 + 실행 | 2-3h |
#
#   총 잔여 추정: 약 12-20시간 → 다중 세션. blocked-env 는 적용 안 함 (hook 추가는 instrumentation
#   이지 fix 가 아님 — worktree-local 만, 커밋 안 함).
#
#   진행 순서 (순차 proof 테스트):
#     세션 N+1: CAND-026 + CAND-032 — end-to-end 파이프라인 검증 우선 (skipped 잡으면 hook 추가)
#     세션 N+2: CAND-030/031/033 (auto-reply / agents 도메인 hook 추가)
#     세션 N+3: CAND-037/040 (context-engine / infra 도메인 hook 추가)
#     세션 N+4-5: CAND-038/039 (gateway fake-deps 인프라)
#
#   각 세션 진입 시 첫 액션:
#     1. 사용자 허락 받기 (skills/real-behavior-proof/SKILL.md §Step 1 gate).
#     2. upstream/main fetch + behind 확인 (§1.B). 변경 있으면 git pull upstream main --ff-only.
#     3. 해당 CAND 의 file:line 재확인 (upstream HEAD 변경 → CAL-007 stale risk).
#     4. 시나리오 docstring 의 필요 hook 목록 확인:
#          /Users/lucas/Project/openclaw-audit/skills/real-behavior-proof/scenarios/proof-CAND-NNN.py
#     5. worktree 생성 (/Users/lucas/Project/openclaw-worktrees/proof-CAND-NNN).
#     6. __test export hook 추가 (worktree-local instrumentation only, 커밋 안 함).
#     7. pnpm build → harness/run.py --target CAND-NNN --mode pre-sol --scenario proof-CAND-NNN.
#     8. status 평가 (collected / unreproducible / blocked-*).
#     9. local-state + SOL 영역 transition (proofs/PROOF-CAND-NNN-pre-*.md 생성).
#     10. unreproducible → CAND abandon. collected → SOL 작성 단계 진입.
#
#   참고: 시나리오는 사전 작성됨 (이번 세션 산출물). SOL 작성은 pre-sol collected 결과를 받은
#   후 — proof 가 정직한 게이트.
#
#   ## 1-5. 기존 잔여 액션 (위 0번 이후)
#
#   1. PR #68669 무대응 유지 — close 트리거 시 race fix 논거 제시 + reopen 또는 CAL-010 credit-only.
#   2. PR #71648 메인테이너 리뷰 대기 — sufficient 자동평가 미부여 (fake timer 추정). real wall-clock 재시도는 mcp-pending-ttl
#      scenario + TTL env override hook 도입에 의존.
#   3. CAL-011 calibration 정식 문서 작성 — alternative-axis acceptance 패턴 (PR #71040 사례).
#   4. real-behavior-proof skill end-to-end 검증 — §0 의 CAND-026 + CAND-032 진행이 첫 end-to-end 검증 케이스
#      (9 CAND 시나리오 사전작성 완료 2026-05-14, 다음 세션부터 순차 실행).
#      (참고: SOL-0004 는 2026-04-21 PR #68842 로 머지 완료 — proof skill 도입 전이라 미적용).
#   5. Phase 5 후속 셀 — mcp-lifecycle / mcp-concurrency / mcp-memory v2 / agents-registry-lifecycle / 신규 도메인 (event-bus / channel-bridge-concurrency).
#
# CLOSED (이전 액션, 완료):
#   • PR #68341 (CAL-008 upstream-competing) — 2026-05-11 MERGED. CAND-021/022 retract 결정 사후 검증.
#     CAL-008 outcome 섹션 참조.
#
# 신규 셀 정의 시 grid.yaml §types 에 id 추가 후 §cells 확장.
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

## 4. 다음 셀 선택 시 우선순위

Phase 1-5 의 21 셀 모두 1차 audit 완료. 새 셀 착수 시:

1. **메인테이너 공개 우선순위 부합 도메인** 우선 (memory / plugin loading / cron / reliability)
2. PR queue 여유 확인 (`gh pr list --author "@me" --repo openclaw/openclaw --state open` ≤ 7)
3. 기존 abandoned CAND 와 axis 중복 회피 (CAL-004/CAL-008 패턴)
4. 신규 도메인 진입 시 `domain-notes/<name>.md` 신규 작성 의무

현재 후보 (위 §3 마지막 코멘트 참조): mcp-lifecycle / mcp-concurrency / mcp-memory v2 / agents-registry-lifecycle / event-bus.

## 5. 졸업 조건 (shadow → 자동화)

```bash
wc -l metrics/shadow-runs.jsonl metrics/human-verdicts.jsonl metrics/self-consistency.jsonl
# 목표: 50 / 10 / 10
```

현재 (2026-05-14): **28 / 23 / 10** — self-consistency 졸업, human-verdicts 졸업,
shadow-runs 22 누적 더 필요. real-behavior-proof skill 도입으로 SOL 단계마다 evidence 증가 → shadow 누적 가속 예상.

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

## 7.5 Real behavior proof (R-12 pre-sol / R-13 post-sol)

**스킬**: `skills/real-behavior-proof/` (별도 — cross-review 와 분리. build/run/measure 가 본질).

openclaw 의 외부 PR 라벨 정책 (`triage: needs-real-behavior-proof` / `proof: supplied` / `proof: sufficient`,
출처 `scripts/github/real-behavior-proof-policy.mjs`) 에 맞춰 SOL 작성 전/후 production-like 환경에서
실제 결함 재현/검증을 자동화. 머지된 5 PR 의 V2/V3/V4 사후 evidence 강화 패턴을 SOL 단계로 forward-shift.

**모드 2개**:
- `pre-sol` (R-12): gatekeeper approve + cross-review proceed 직후. without-fix 빌드 단독.
  목적: false positive 사전 차단 + post-sol baseline 확보.
- `post-sol` (R-13): SOL chosen_fix 결정 후. with-fix vs without-fix 두 빌드 비교.
  산출물: openclaw 6 필드 PR body 텍스트 (`pr_body_section`).

**모든 severity 적용 (P3 포함)**. 재현 불가능은 강등 없이 별도 상태 (`blocked-external-dep` 등).

**호출 (사용자 허락 필수)**:
```bash
# pre-sol (single build, scenario 1회)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 --mode pre-sol --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> --trials 100

# post-sol (build pair + scenario × 2)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 --mode post-sol --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> --head-sha <fix HEAD> --trials 100
```

**산출물 위치**:
- `proofs/PROOF-{target}-{pre|post}-{ts}.md` — 영구 evidence + PR body section
- `solutions/SOL-NNNN.md` frontmatter 의 `pre_sol_proof` / `post_sol_proof` 객체
- `local-state/state.yaml` + `history.jsonl` transition (`proof-{collected|unreproducible|blocked|skipped}-{pre|post}`)

**status enum** (둘 다 동일): `pending | collected | unreproducible | blocked-external-dep | blocked-env | skipped-by-user`

**시나리오 카탈로그** (`skills/real-behavior-proof/scenarios/`):
- `cron-manual-run` — PR #78243 baseline. sqlite task_runs status='lost' 측정. `REQUIRES_EXTERNAL_DEP=True` (OAuth + LLM 호출).
- `gateway-map-size` — SOL-0004 패턴. Map.size 측정. 외부 의존 없음.
- `mcp-pending-ttl` — PR #71648 패턴. real wall clock TTL (fake timer 회피, sufficient 라벨 노림).

**격리**: `env_isolate.isolated_home()` 가 `OPENCLAW_HOME=/tmp/proof-{uuid}/.openclaw` redirect.
production `~/.openclaw/` 손상 0. OAuth profile 만 read-only 복사 (LLM 호출 가능).

**openclaw policy 호환 검증**: `harness/render_proof.py` 가 6 필드 형식 정확 mirror.
검증: `node -e` 로 `evaluateRealBehaviorProof()` 직접 호출 → `status: "passed"` 확인.
guard `_check_no_inline_heading` 가 line-start `# ` 패턴 (policy 가 거기서 break) 자동 raise.

**상세**: `skills/real-behavior-proof/SKILL.md` (5 단계 호출 규약 + 시나리오 작성 규약).

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
  - `calibration/CAL-010-indirect-merge-with-credit.md` (PR #70142 — 메인테이너가 우월한 fix 로 직접 commit + 우리 PR closed + changelog credit. atomic helper 반환 contract 활용 미스 + indirect-merge outcome 분류 신설)
  - **CAL-011 (TODO 작성)** — alternative-axis acceptance + cross-review-driven retract. CAL-008 (dup-axis 선제) + CAL-010 (indirect-merge with credit) 의 hybrid 패턴. 메인테이너가 다른 axis 로 동일 문제 해결 시 우리 PR close + 잔여 영역만 follow-up issue 로 좁게 분리.
- **PR 트래커 (모든 내 openclaw PR)**: `openclaw-pr-tracker.md`
  - 파이프라인 외 PR + 종결된 PR + Greptile 재리뷰 수동 트리거 절차
