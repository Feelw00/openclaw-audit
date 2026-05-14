# openclaw 진행중 PR 트래커

파이프라인 외부에서 이미 진행중인 openclaw PR + 파이프라인이 발행한 PR 의 현재 상태.

## 내 active PR (Feelw00)

### #68669 — fix(agents): dedupe subagent browser session cleanup wrapper with dispatch flag

- **유형**: 파이프라인 CAND-011, post-harness + pre-pr + post-commit cross-review (총 11 agent) 모두 real
- **상태**: OPEN (2026-05-14 기준), head `7067f30ab2`. 2026-05-11 force-push 후 신규 코멘트 없음
- **라벨**: `proof: supplied + sufficient`, `triage: refactor-only` (vincentkoc 일괄 부여, 우리 PR 도 영향)
- **steipete 코멘트 (2026-04-25)**: "Codex deep review: this looks correct and worth landing" — Bug/behavior 인정
- **결정**: 무대응 유지. close 트리거 시 (a) race fix 논거 제시 + reopen 또는 (b) CAL-010 credit-only.
- **관련**: issue #68668

### #71648 — fix(mcp): bound pendingClaudePermissions / pendingApprovals via TTL sweeper + close clear

- **유형**: 파이프라인 CAND-025 → SOL-0008, pre-pr 3/3 real (round 2)
- **상태**: OPEN (2026-05-14 기준), head `eb69de7135`. 2026-05-11 force-push 후 신규 코멘트 없음
- **라벨**: `proof: supplied` 만 (sufficient 미부여). 추정 원인: V4 evidence 가 `vi.useFakeTimers` 사용 → real wall-clock 측정과 차이 (다른 5 PR 은 sufficient 자동 부여)
- **fix scope**: A (sweeper+ttl-only). cap/FIFO 의도적 후속 PR 분리
- **CI**: tsgo + 8/8 unit + check-test-types green (eef0be2a2e 에서 BridgeInternals intersection type 좁힘)
- **결정**: 메인테이너 리뷰 대기. real wall-clock 재시도 가치는 mcp-pending-ttl scenario 의 TTL env override hook 도입에 의존
- **관련**: Closes #71646

## 종결된 PR

### #78243 (CAND-024 → SOL-0009, **MERGED 2026-05-11** — cron manual-run mark/clear)
- **결과**: merged — SOL-0007 (PR #71040 closed CAL-011) 의 manual-only scope-down 후속이 채택됨
- **fix**: ops.ts `prepareManualRun` (markCronJobActive) + `finishPreparedManualRun` (try/finally clearCronJobActive). timer.ts 미수정 (1fae716a04 sweeper recovery axis 회피)
- **evidence**: real wall-clock with/without 빌드 sqlite `task_runs.status` 비교 (lost vs failed). V2 → V4 강화 모두 통과 → `proof: supplied + sufficient`
- **관련**: Fixes #78233 (이전 #71040 close 시 우리가 발행한 follow-up issue)

### #68543 (CAND-009, **MERGED 2026-05-11** — infra-retry retryAsync retry-after lower bound)
- **결과**: merged — steipete 의 "deliberately falls back to symmetric jitter" 오독 정황 + commit `60ad4714` 실제 diff 인용 답변 후
- **fix**: `retryAsync` 가 server-supplied Retry-After 헤더를 하방으로 위반하지 않도록 boundary check
- **evidence**: V3 (50 trial real `node:http` server timing) — without 23/50 (46%) below 1000ms (worst 508ms), with 0/50 (min 1037ms)
- **Greptile**: 5/5 safe-to-merge (초기). Math.round → Math.ceil follow-up fix `11430f641c`
- **관련**: Closes #68541

### #68839 (CAND-012 → SOL-0003, **MERGED 2026-05-11** — auto-reply drain identity guard)
- **결과**: merged. V1 (회귀 테스트 1/1 drain.identity-guard) 만으로도 sufficient 자동 부여 패턴
- **fix**: drain IIFE finally 의 `FOLLOWUP_QUEUES.delete(key)` 가 자신의 queue 와 map 의 entry 가 같은지 identity 검증 후 삭제
- **관련**: Closes #68838

### #68848 (CAND-015 → SOL-0005, **MERGED 2026-05-11** — gateway nodeWakeById cleanup)
- **결과**: merged. V4 (100 unregistered RPC Map size) — without 100 / with 0
- **fix**: `maybeWakeNodeWithApns` no-registration early-return path 에 cleanup. 새 main 이 wake state 를 nodes-wake-state.ts 별도 모듈로 분리 → fix 이식 매끄럽게 진행
- **관련**: Closes #68847

### #71040 (CAND-024 → SOL-0007, **CLOSED 2026-05-06 — alternative-axis acceptance, CAL-011**)
- **결과**: closed (잔여 manual-run 영역만 follow-up #78233 + SOL-0009 + PR #78243 으로 분리되어 MERGED)
- **사유**: 메인테이너 commit `1fae716a04` (fix: recover stale cron task records, 2026-04-26, PR 발행 2일 후) 가 task-registry.maintenance.ts 에 sweeper-side 사후 복구 함수 (resolveDurableCronTaskRecovery / resolveCronRunLogRecovery / resolveCronJobStateRecovery) 추가 — 우리 PR 의 producer-side mark/clear 와 다른 axis 채택. 같은 axis PR #71968 메인테이너 close. #68191 (sweeper 입장) OPEN. 새 main `deferAgentTurnJobs:true` (7877182b6f) 가 핵심 isolated agentTurn 시나리오 차단
- **cross-review 결과**: pre-pr 5-agent (metrics/cross-review-PR71040-20260506-030011.jsonl) → 4/5 scope-down + 1/5 merge-as-is
- **교훈 (CAL-011)**: alternative-axis 메인테이너 fix → 우리 PR close + follow-up issue 로 좁은 잔여 영역 분리. CAL-008 (dup-axis 선제) + CAL-010 (indirect-merge with credit) 의 hybrid. 사용자가 cross-review 능동 트리거 (PR 작성 2주+ 후 잔존 검증) 한 운영 패턴 신설

### #63105 (파이프라인 외 본인 feature PR, **MERGED 2026-04-20**)
- **결과**: merged — feat(cron): split jobs.json into config and runtime state files
- **경로**: 파이프라인 이전 작업 → Greptile 5/5 (2026-04-15) → @gumadeiras 메인테이너 merge
- **fix**: cron jobs.json 을 config (사람 편집) + runtime state (앱 기록) 로 분리
- **관련 이슈**: Closes #53581 (@Daanvdplas 요청)

### #68842 (CAND-014, **MERGED 2026-04-22**)
- **결과**: merged — 파이프라인 **첫 merged PR**
- **경로**: post-harness 5/5 real → SOL-0004 → issue #68841 + PR #68842 → Greptile 5/5 → Codex P2 CAL-009 반박 + thread resolved → 메인테이너 merge
- **fix**: gateway costUsageCache MAX=256 + FIFO eviction (`src/gateway/server-methods/usage.ts`)
- **교훈**: CAL-009 (bot review 병렬 검증 후 sibling consistency 반박) 가 merged-track 으로 실증됨. prior art (PR #36682 CLOSED) 있어도 차별화 (LRU+MAX=64 vs FIFO+MAX=256) + 명확한 scope 이면 merge 가능

### #70142 (CAND-023, **CLOSED 2026-04-26 — indirect-merge with credit, CAL-010**)
- **결과**: **사실상 win** — 직접 merge 아님, 그러나 메인테이너가 commit `8bc4d4bcd4` 로 우월한 fix 를 main 에 직접 반영 + changelog credit (`Thanks @Feelw00`) + clawsweeper 자동 close
- **경로**: pre-SOL 5-agent + post-harness 5-agent + pre-pr 5-agent + CAL-009 2-agent (Codex P2) = 총 17 agent 검증 → 2026-04-22 발행 → 2026-04-25 chat.ts upstream 충돌 rebase (head `39ccb9c4a2`) → 2026-04-26 메인테이너 직접 fix → clawsweeper close
- **fix (우리)**: post-attachment-parse 시점 `chatAbortControllers.get` 재호출 + offloaded media cleanup 루프 (174 prod / 173 test)
- **fix (main, 우월)**: pre-parse 시점 `registerChatAbortController.registered` 플래그 활용 — race window 자체 제거 (7 prod / 136 test)
- **차이의 본질**: 우리는 atomic helper 의 `.registered` 반환 contract 를 활용 못 하고 post-await 보정형으로 우회. 메인테이너는 helper 가 이미 atomic 이라는 사실을 활용해 attachment parse 를 아예 건너뜀
- **changelog**: `Gateway/chat: keep duplicate attachment-backed chat.send retries with the same idempotency key on the documented in-flight path so aborts still target the real active run. Fixes #70139. Thanks @Feelw00.`
- **잔여 follow-up**: clawsweeper 가 "offloaded-media cleanup narrow follow-up" 가능성 언급, 그러나 main 의 새 흐름에서는 media 가 만들어지지도 않음 → 우선순위 낮음
- **교훈 (CAL-010)**: race fix 설계 전 관련 helper 의 반환 contract (`.registered`, `.added` 등) 먼저 확인. cross-review 프롬프트에 "기존 helper 반환 contract 활용 가능성" 카테고리 추가 검토. indirect-merge 는 abandon 이 아닌 별도 outcome 으로 분류

### #68489 (CAND-004, maintainer closed)
- **결과**: false positive — CAL-001 참조
- **사유**: schedulePendingLifecycleError 의 line 249 unconditional delete 가 primary cleanup path. sweeper cleanup 은 fallback.
- **교훈**: R-5 (execution condition 분류) 추가

### #68511 (CAND-006, self-closed)
- **결과**: false positive — CAL-003 참조
- **사유**: test 가 process.kill branch throw 를 강제하지만 production 은 process.emit branch (listener 항상 등록) 만 탐
- **교훈**: R-7 (hot-path vs test-path 일관성) + PR 발행 전 cross-review 3 에이전트 의무화

### #68531 (CAND-005, self-closed)
- **결과**: upstream superseded — CAL-004 참조
- **사유**: upstream commit `59d07f0ab4 + e8fd148437 + 2a283e87a7` 가 내 PR 하루 전에 병합되어 동일 race 해결
- **교훈**: R-8 (upstream 최신 commit 사전 확인) + dedup.py commit 검색 helper

---

## Greptile 재리뷰 수동 트리거 절차

Greptile 은 commit push 후 **자동으로 재리뷰하지 않음**. 수동 요청 필요.

### 방법 1: 코멘트 트리거 (권장)

PR 에 다음 코멘트 작성:
```
@greptile review and provide confidence score
```

변형:
- `@greptile review` — 기본 재리뷰
- `@greptile review and provide confidence score` — confidence 점수 포함 재리뷰 (권장)

### 방법 2: Greptile 웹 UI

`app.greptile.com/api/retrigger?id=<review_id>` 링크 — 각 Greptile 코멘트 하단에 포함됨.

### 언제 재요청

- Follow-up commit 으로 지적 해결 후
- 초기 리뷰가 비어있거나 오래된 경우
- confidence 점수 다시 받아 메인테이너에게 시그널 주고 싶을 때

### 주의

- **너무 자주 트리거 금지** — rate limit 걸릴 수 있음
- commit push 후 최소 수 분 대기 (CI 완료 후)
- 같은 PR 에 연속 2-3회 이상 코멘트 자제

### 파이프라인 flow 에 포함

```
1. 커밋 + 푸시
2. CI 완료 대기 (gh pr checks <N> --watch)
3. 지적사항 있으면 follow-up 커밋
4. 재-push 후 `@greptile review and provide confidence score` 코멘트
5. Greptile 5/5 확보 후 메인테이너 리뷰 대기
```

## 체크 빈도

주 1회 또는 세션 시작 시 `gh pr list --author Feelw00 --repo openclaw/openclaw --state open` 로 상태 확인.

## R-10: 메인테이너 리뷰 대응 (CAL-006)

메인테이너 review 가 오면 **답변 전 필수 절차**:

1. 답변 draft 금지 — cross-review (3 agent: positive/critical/neutral) 먼저
2. Critical agent 에 "메인테이너가 말한 불변식 + 주변 edge case 동시 탐색" 프롬프트
3. 답변 톤: 사과 + 재검토 결과 + 새 fix commit SHA + 선택지 열기
4. 상세 프로토콜: `maintainer-review-protocol.md`

Anti-pattern (금지):
- "이미 구현됐다" 로 단정 시작
- "file:line 알려달라" 로 책임 전가
- cross-review 없이 답변
- code 변경 없이 comment 만

메인테이너 목록: CONTRIBUTING.md §Maintainers (steipete, obviyus, tyler6204, gumadeiras 등).

### 파이프라인 주의사항 (2026-04-22 추가)

- **issue/PR 본문은 반드시 영어** (`openclaw-contribution.md §8`). publish.py 는 CAND 본문 (한국어) 을 그대로 issue 에 사용하므로, **publish 전에 영어 본문을 별도 준비해서 `gh issue edit --body-file` 로 덮어쓰거나**, CAND body 를 영어로 작성할 것. #70139 발행 직후 한국어 남은 것을 사용자 지적으로 재작성.
