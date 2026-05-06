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
# Phase 5 (1/N — 본 세션 2026-04-25/26 — 메인테이너 우선순위 "plugin loading" 영역 진입):
#   ✓ mcp-memory                   — 2 FIND (P2) → CAND-025 epic → SOL-0008 → issue #71646 + PR #71648 (초기 head 61eb79c67a → 9f16dd4823 → 현재 eef0be2a2e). OpenClawChannelBridge (src/mcp/channel-bridge.ts) 의 두 pending Map (pendingClaudePermissions L50, pendingApprovals L51) 이 TTL/sweeper/close-clear/cap 동시 결여 → fix scope A (sweeper+ttl-only): lazy-start 5min sweepPendingExpired interval (.unref()) + 1h TTL (Claude perm) / expiresAtMs?? trackedAtMs+30min (approvals) + close-clear + closed-guard. Pre-PR round 1 critical-devil 가 fallback 버그 (createdAtMs?? now 가 매 sweep 마다 expiry 재계산) 잡음 → PendingApprovalEntry wrapper + trackedAtMs instance bookkeeping 으로 수정 + 3 추가 fix → round 2 3/3 real → proceed_to_pr. PR diff 174 prod + 173 test (7 it). **2026-04-27 정정**: pre-PR 검증 시 보고한 "check + build green" 은 사실 unit test (1575/1575) 만 의미했고 `pnpm check:test-types` (tsgo) 는 안 돌렸음 → CI 의 check + check-test-types 가 50+ TS2339 'never' 에러로 fail. `OpenClawChannelBridge & BridgeInternals` intersection 이 private+public 같은 이름 충돌로 `never` 로 collapse 한 type 만 문제, 머지/main 무관, PR head 단독 재현. eef0be2a2e 에서 makeBridge 반환 타입을 BridgeInternals 단독으로 좁히고 `as unknown as` 캐스트 + handleClaudePermissionRequest/close 를 BridgeInternals 에 추가. types-only test-helper, prod 코드 변경 0. 8/8 unit + tsgo:core:test + tsgo:extensions:test 모두 green 재확인. domain-notes/mcp.md 신규 작성.
#
# 살아있는 PR 6건 (2026-05-06 기준, upstream/main 최신 동기화 HEAD ea391c6df2 — warn=7 미만 유지):
#   • **#78243 (SOL-0009, cron manual-run mark/clear, head 5c4458de83, 2026-05-06 발행)** — Fixes #78233. SOL-0007 (PR #71040) 의 manual-only scope-down 후속. ops.ts prepareManualRun (markCronJobActive) + finishPreparedManualRun (try/finally clearCronJobActive) 만 수정. timer.ts 미수정 (1fae716a04 sweeper recovery axis + deferAgentTurnJobs:true 정책 회피). 회귀 테스트 2/2 + tsgo green. **2026-05-06 with-fix vs without-fix 직접 비교 evidence 확보**: 같은 cron job (sleep 420 agentTurn + codex via ChatGPT Plus OAuth) → without-fix 빌드 (ops.ts at base) `task_runs.status='lost'` + `error="backing session missing"` (T0+5분 59초), with-fix 빌드 `status='failed'` (lost 마킹 차단, T0+7분 19초 정상 finalize). sqlite `task_runs` 직접 조회로 확정. **Real behavior proof CI PASS** (v2 비교 evidence + `**Field**:` regex 형식). 정책 자동검증 및 production 진짜 시연 모두 충족. 임시 commit 3건 (`chore: re-run CI` × 2, body fix × 1) PR head 누적 — squash 여부 사용자 결정. 메인테이너 리뷰 대기.
#   • **(env setup)** Codex CLI 0.128.0 + ChatGPT Plus OAuth 로그인. `~/.openclaw/openclaw.json` 에 `agents.defaults.model.primary: openai-codex/gpt-5.5` + `agentRuntime.id: codex` + `plugins.entries.codex.enabled: true`. `~/.openclaw/agents/main/agent/auth-profiles.json` 의 `openai-codex:<email>` profile (expires 2026-05-16). 이 setup 으로 다른 5 PR 의 Real behavior proof evidence 도 같은 환경으로 캡쳐 가능 (worktree 별 빌드 + 같은 ~/.openclaw home 공유).
#   • #68543 (CAND-009, infra-retry, head b9973d2868) — Real behavior proof **v2 (with-fix vs without-fix 비교)** 추가. without-fix: 4 tests 중 3 fail (e.g. `expected 500 to be greater than or equal to 1000` — base 의 retry.ts 가 jitter 침범 그대로 재현). with-fix: 4/4 pass. **`proof: supplied` 부여**, sufficient 는 메인테이너/clawsweeper 평가 대기. steipete invariant 이미 반영됨, reviewDecision: CHANGES_REQUESTED 유지.
#   • #68669 (CAND-011, agents-registry, head 81d901eba6) — Real behavior proof **v2 비교** 추가. without-fix: `expected vi.fn() to be called 1 times, but got 2 times` (parallel completion 시 cleanup 두 번 호출 직접 재현). with-fix: 36/36 pass. **`proof: supplied` 부여**. `triage: refactor-only` 그대로 (vincentkoc 일괄). **결정: 우선 두고 close 되면 대응**.
#   • #68839 (CAND-012, auto-reply drain identity guard, head 781014ea72) — Real behavior proof v1 추가 (회귀 테스트 1/1 drain.identity-guard) → **`proof: supplied` 부여**. v2 비교 미진행 (사용자 결정 — 4 PR 만 강화).
#   • #68848 (CAND-015, nodeWakeById cleanup, head e82e53ae2c) — Real behavior proof **v2 비교** 추가. without-fix: 4 tests 4 fail (Cannot read properties of undefined for `__testing.resetWakeState` + leak detect). with-fix: 4/4 pass. **`proof: supplied` 부여**. 새 main 이 wake state 를 nodes-wake-state.ts 별도 모듈로 분리, fix 이식.
#   • **#71648 (CAND-025→SOL-0008, mcp channel-bridge pending Maps TTL sweeper, head bc04d6a789)** — Real behavior proof **v2 비교** 추가. without-fix: 8/8 fail (sweeper/close-clear/closed-guard 모두 부재). with-fix: 8/8 pass. **`proof: supplied` 부여**. Closes #71646. 이전 history 그대로. 리뷰 대기.
#
# **2026-05-06 push 후 CI 16건 fail — base (384432fd22) 자체의 broken test (archive/fs-safe/tmp-dir/media/plugin contract 등 우리 fix 와 무관 영역)**. main push CI 는 해당 잡 skipped 라 main 에서 안 보임, PR CI 풀 잡으로 드러남. 5 PR 동일 16개 fail = base 문제 결정적 증거. 메인테이너가 main 자체 broken 인지 가능성 → fix-PR 평가 영향 적을 수 있음. 우리 fix 와 무관, 추적 안 함.
#
# merged: #68842 (CAND-014, 파이프라인 첫 merge), #63105 (파이프라인 외 cron-store split, 2026-04-20 merged).
# closed (indirect-merge with credit, CAL-010): #70142 (CAND-023, 2026-04-26) — clawsweeper auto-close. 메인테이너 commit `8bc4d4bcd4` 우월 fix. changelog `Fixes #70139. Thanks @Feelw00.` credit.
# **closed (alternative-axis acceptance, CAL-008+CAL-010 hybrid): #71040 (CAND-024→SOL-0007, 2026-05-06)** — pre-pr 5-agent cross-review (metrics/cross-review-PR71040-20260506-030011.jsonl) 결과 4/5 scope-down + 1/5 merge-as-is. 결정적 발견: 메인테이너 commit `1fae716a04` (fix: recover stale cron task records, 2026-04-26, PR 발행 2일 후) 가 **task-registry.maintenance.ts 에 resolveDurableCronTaskRecovery + resolveCronRunLogRecovery + resolveCronJobStateRecovery 추가** — sweeper-side 사후 복구로 우리 PR 의 producer-side mark/clear 와 다른 axis 채택. 같은 axis PR #71968 메인테이너 close. #68191 OPEN 으로 sweeper 입장 표명. 추가로 새 main `deferAgentTurnJobs:true` (7877182b6f) 가 PR body 의 핵심 isolated agentTurn 시나리오 차단. 잔여 manual run 영역도 사후 복구 (`applyJobResult`→lastRunStatus + `resolveCronJobStateRecovery` 매치) 로 결국 정정 — 우리 fix 의 valid 가치는 5분 grace 내 transient 'lost' marker + Background task lost 시스템 메시지 emit 차단 (UX noise) 좁은 영역만. close 코멘트로 메인테이너 axis 인정 + follow-up issue **#78233** 발행 ("cron: transient 'lost' marker on long-running manual runs before sweeper recovery", manual-run UX gap 좁게 분리). 새 calibration 후보: **CAL-011 (alternative-axis acceptance + cross-review-driven retract)** — 메인테이너가 다른 axis 로 같은 문제 해결 시 우리 PR close + follow-up issue 로 좁은 잔여 분리하는 패턴. CAL-008 (dup-axis 선제) + CAL-010 (indirect-merge with credit) 변형.
# warn=7 / block=10 기준 active 5 → 여전히 warn 미만, 신규 PR 발행 가능.
#
# 잔여 미처리 (다음 세션 우선순위 순):
#   1. **PR #68669 — 무대응 결정 유지** (2026-04-27). 위 NEXT.md 기록 그대로. close 트리거 시 (a) race fix 논거 제시 + reopen 또는 (b) CAL-010 credit-only.
#   2. PR #68341 모니터 (CAL-008 upstream-competing) — thesomewhatyou grab-bag PR. CAND-021 + CAND-022 abandoned 근거. PR close-without-merge 시 재오픈 검토.
#   3. **CAL-011 작성 검토** — PR #71040 사례 정착. CAL-008 (dup-axis 선제) 와 CAL-010 (indirect-merge with credit) 의 hybrid: alternative-axis 메인테이너 fix + cross-review-driven retract + follow-up issue 로 좁은 영역 분리. 사용자가 cross-review 를 능동 트리거 (PR 작성 2주+ 후 잔존 검증) 한 점도 새 운영 패턴.
#   4. Phase 5 후속 셀 (PR queue 여유) — mcp-lifecycle / mcp-concurrency / mcp-memory v2 (cap/FIFO 후속) / agents-registry-lifecycle (PR #68669 리뷰 완료 후) 중 택일.
#
# CAND-021 종결 (2026-04-25): 5-agent post-harness cross-review (metrics/cross-review-CAND-021-20260425-224410.jsonl) primary_decision=upstream_wait. real_count=3 (positive/critical/reproduction-realist) + 1 fix-insufficient (hot-path-tracer score=3/5) + 1 upstream-duplicate (PR #68341, Greptile 5/5). FIND-001 자체는 valid race 였으나 PR #68341 이 동일 fix 축 선제 → CAND-016 (PR #68801 dup) 패턴.
# CAND-022 종결 (2026-04-25): cross-review 생략 (CAL-008 dup 직접 확인). PR #68341 의 poll 핸들러 inflight extend + 'dedupes concurrent poll sends' 테스트가 본 FIND-002 의 fix 축과 일치 → state.yaml/CAND-022.md/index.yaml 동기화만 수행.
# CAND-025 종결 (2026-04-26): mcp-memory 첫 셀 → issue #71646 + PR #71648 (head 61eb79c67a) 발행 완료. gatekeeper approve@high → 5-agent post-harness primary_decision=proceed (metrics/cross-review-CAND-025-20260425-233553.jsonl) → SOL-0008 chosen_fix=A (sweeper+ttl-only) → 3-agent pre-pr round 1: critical-devil 가 sweepPendingExpired fallback 버그 (createdAtMs ?? now 매 sweep 마다 expiry 재계산해 영원히 expire 안 됨) 잡음 → PendingApprovalEntry wrapper + trackedAtMs instance bookkeeping + closed-guard + 테스트 보강 (vi.getTimerCount + 양 undefined + close 후 set) 으로 수정 → round 2 3/3 real_problem_real_fix proceed_to_pr (metrics/cross-review-SOL-0008-20260426-005346.jsonl). cap/FIFO 의도적 후속 PR 분리.
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
  - `calibration/CAL-010-indirect-merge-with-credit.md` (PR #70142 — 메인테이너가 우월한 fix 로 직접 commit + 우리 PR closed + changelog credit. atomic helper 반환 contract 활용 미스 + indirect-merge outcome 분류 신설)
- **PR 트래커 (모든 내 openclaw PR)**: `openclaw-pr-tracker.md`
  - 파이프라인 외 PR (#63105 cron-store split) 포함
  - Greptile 재리뷰 수동 트리거 절차
