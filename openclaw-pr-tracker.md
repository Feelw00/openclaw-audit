# openclaw 진행중 PR 트래커

파이프라인 외부에서 이미 진행중인 openclaw PR + 파이프라인이 발행한 PR 의 현재 상태.

## 내 active PR (Feelw00)

### #68543 — fix(infra): keep retryAsync delays above server-supplied Retry-After

- **유형**: 파이프라인 CAND-009, cross-review 3/3 real
- **상태**: OPEN, 체크 green, 2026-04-22 upstream rebase 2회 (head `acc85fe0ff`)
- **Greptile**: **5/5 safe-to-merge** (초기), 이후 Math.round → Math.ceil follow-up fix `11430f641c`
- **steipete 재점검 (2026-04-22)**: steipete 가 리뷰한 commit_id `60ad4714c0` = (rebase 전) `032532ecae` = "Retry-After 경계(===)에서 contract 우선, symmetric 은 >만 (codex override)" **= 이미 steipete 지적이 반영된 수정 commit**. 시퀀스: `3429270354` 에서 `===` 를 symmetric 으로 확장 (문제 발생, 14:41 UTC) → `032532ecae`/60ad4714 에서 `<=` 로 되돌림 + boundary test `toBe(1_000)` (수정, 14:53 UTC) → steipete review (16:02 UTC) → `71c24d731a` Math.ceil 추가 (16:21 UTC). steipete 텍스트 "deliberately falls back to symmetric jitter" 는 `60ad4714` 실제 코드 (`canHonorRetryAfter = ... && retryAfterMs <= maxDelayMs`) 와 불일치 → **메인테이너가 수정 후 diff 를 수정 전으로 오독한 정황**. 답변은 R-10 cross-review 경로로 별도 준비 (`60ad4714` 실제 diff 인용)
- **대기**: 메인테이너 @steipete 답변
- **관련**: issue #68541

### #68669 — fix(agents): dedupe subagent browser session cleanup wrapper with dispatch flag

- **유형**: 파이프라인 CAND-011, post-harness + pre-pr + post-commit cross-review (총 11 agent) 모두 real
- **상태**: OPEN, 체크 green (parity 포함 93/93), 2026-04-22 upstream rebase 3회 (head `00cab4264f`)
- **Greptile**: 자동 summary 완료
- **Codex 1라운드 (2026-04-21)**: P2 "Reset dispatch flag when browser cleanup fails" (before-await set → throw 시 영구 skip 우려) → CAL-009 병렬 2-agent 검증: `runBestEffortCleanup` wrap 으로 throw 구조적 차단 근거로 반박. reply + thread resolved. reply 에서 sibling `endedHookEmittedAt` timing 이 실제로는 after-await 임을 솔직히 인정 (커밋 메시지 overclaim 수정)
- **Codex 2라운드 (2026-04-22)**: "lazy `import()` reject pre-wrapper" 재반박 (`subagent-registry.ts:114-115` fresh evidence) → CAL-009 2라운드 5-agent post-harness cross-review (positive/critical/repro-realist/hot-path-tracer/upstream-dup) → **production bundle 에 lazy import 자체가 부재** 확인 (`dist/subagent-registry-Zhtu8A2W.js:835, 2183, 2495, 2666` 에 tsdown/rollup inline). reply 에 dist 번들 line 직접 인용하며 source-level-only 임을 증명, defense-in-depth 수정조차 불필요 근거 제시 → thread resolved, 코드 변경 없음
- **대기**: 메인테이너 리뷰
- **관련**: issue #68668
- **특이사항**: cross-review 가 narrative overclaim 을 조기 탐지 → "IPC 중복" → "wrapper overhead + defense-in-depth" 로 정직하게 scope-down. 2라운드에서 설계 과잉 확장 (옵션 c sibling 완전 동형) 제안이 올라왔으나 dist 번들 실측으로 production 효용 0 확인 → "근본 해결은 반박으로 이미 완료" 로 정정

### #68839 — fix(auto-reply): guard FOLLOWUP_QUEUES delete against late drain finally

- **유형**: 파이프라인 CAND-012, post-harness 5/5 + pre-pr v2 3/3 real
- **상태**: OPEN, 체크 green (parity 포함), 2026-04-22 upstream rebase (head `1236d56668`)
- **대기**: Greptile/Codex 리뷰 + 메인테이너 리뷰
- **관련**: issue #68838
- **특이사항**: pre-pr v1 에서 repro import 경로 + assertion 버그 발견 → repro v2 재작성 (restartIfIdle=false 패턴으로 D2 kick 억제 → D1 finally 만 유일 mutator 로 격리)

### #70142 — fix(gateway): re-check chatAbortControllers after attachment parse

- **유형**: 파이프라인 CAND-023, pre-SOL 5-agent confirmation + post-harness 5-agent + pre-pr 5-agent + CAL-009 2-agent (Codex P2) = 총 17 agent 검증
- **상태**: OPEN, 2026-04-22 발행, head `091b4f0fbd` (rebase 1회 + amend 1회 post Codex fix)
- **Greptile**: summary 완료 (real race condition 인정)
- **Codex (2026-04-22)**: P2 "Clean up offloaded media before early in_flight return" — early-return 경로에서 parseMessageWithAttachments 가 offload 한 >2MB disk file 이 cleanup 없이 남음 지적. CAL-009 병렬 2-agent (positive + critical) 검증 → **반박 불가, accept-and-fix 합의**. `deleteMediaBuffer` import + loop 추가 (server-node-events.ts:448-458 선례 미러) + test payload 2MB+ 확장 + deletedMediaIds assertion 추가. reply + thread resolved
- **대기**: 메인테이너 리뷰
- **관련**: issue #70139, PR #69208 (umbrella), PR #68801 (complementary), PR #69747 (adjacent)
- **특이사항**:
  - 원래 주장 "LLM 호출 2회 + transcript corruption" 은 `claimInboundDedupe` (inbound-dedupe.ts:94-112) 가 이미 차단 — pre-SOL 5-agent 에서 **발견된 숨은 방어**로 인해 scope 수정
  - 재정의된 harm: `chatAbortControllers` map overwrite → **user abort 기능 파손** (+ duplicate ack/media writes/dedupe double-write)
  - severity P1 → **P2** 하향
  - Option A (post-parse re-check) 선택 — XS, no-attachment sibling 무변경
  - test 파일명 mismatch (chat.directive-tags.test.ts 에 추가) commit msg + PR body 에서 자진 인정, follow-up split 제안
  - CAL-009 Codex P2 accept 정당화 근거: `server-node-events.ts:448-458` 동일 구조 precedent 존재, `mediaCleanup` sweep 은 `cfg.media.ttlHours` opt-in 만 작동 (default 미동작), 내 PR body 스스로 duplicate media writes 를 race harm 으로 나열했는데 첫 커밋은 해결 안 함 (자기모순)

### #68848 — fix(gateway): clear nodeWakeById on no-registration early-return

- **유형**: 파이프라인 CAND-015, post-harness 5/5 + pre-pr 3/3 real
- **상태**: OPEN, 체크 green (parity 포함 93/93), 2026-04-22 upstream rebase 2회 (head `5b9103c7e0`)
- **대기**: Greptile/Codex 리뷰 + 메인테이너 리뷰
- **관련**: issue #68847
- **특이사항**: PR #63709 (clearNodeWakeState on WS close) 과 scope 구분 명시 — 이 PR 은 unregistered-nodeId early-return path 처리 (complementary). 최소한의 `__testing` seam 추가 (agent-wait-dedupe.ts:223 / agents.ts:78 house style 미러)

## 종결된 PR

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
