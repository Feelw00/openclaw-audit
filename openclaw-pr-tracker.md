# openclaw 진행중 PR 트래커

파이프라인 외부에서 이미 진행중인 openclaw PR + 파이프라인이 발행한 PR 의 현재 상태.

## 내 active PR (Feelw00)

### SOL 0014-0019 배치 상태 (2026-06-01 갱신)

2026-05-29 발행한 SOL 0014~0019 6 PR + 과거 미머지 2건(#68669/#71648)의 결과. upstream/main 이 584→681 커밋 전진하며 일부 PR 이 CONFLICTING 됨. 2026-05-31 일괄 rebase(메인 ff + node_modules 동기화 후 worktree 별 build+check) + force-push 대응.

- **MERGED 6 (전부 steipete 직접 머지)**:
  - #68669 (SOL-0002) 2026-05-30 · #71648 (SOL-0008) 2026-05-30
  - #88029 (SOL-0014) 2026-05-31 · #88018 (SOL-0017) 2026-05-31 · #88011 (SOL-0019) 2026-05-31 · #88008 (SOL-0015) 2026-05-31
  - #88008 특기: rebase 충돌해소 시 test import(`markTaskTerminalById`/`getTaskById`)를 working tree 에만 고치고 커밋 누락 → 첫 push 후 CI `check-test-types` TS2304 + node test 실패. amend(276adadc73) + Node24 로 22/22 실측 후 재push → 머지. 교훈=[[feedback_verify_committed_not_worktree]].
- **#88016 (SOL-0018) CLOSED 2026-06-01 → #88885 (SOL-0020)로 대체**: #88665("move delivery queues to SQLite")가 storage 계층을 파일→SQLite 재작성 → #88016 CONFLICTING. 파일기반 마커 force-fit 대신 CAND-056 재검증 사이클로 재구현. #88016 정직한 close 코멘트(substrate 변경 + 새 PR 예고) 후 close.
- **OPEN 2**:
  - #88885 (SOL-0020, CAND-056, Closes #88015): **OPEN 2026-06-01 발행**, head **4a7a072873**. session-delivery drain 에 기존 SQLite recovery_state 컬럼 배선(crash 후 unacked agentTurn blind-replay 거부). post-sol with=1/without=2(SQLite, Node24) → `proof: supplied`. R-14 dogfood(Layer A diff_guard + Layer B fix-hardening).
    - **clawsweeper R1(head 501944c50a 이전 ce765f008c)**: needs-changes. [P1]#1=catch가 thrown deliver마다 마커 clear→sent-before-error 중복. [P1]#2=fail-safe at-most-once(메인테이너 결정). CI red 2=현재 main 이슈(types.ts:408 deprecated-JSDoc + prompt snapshots, 미변경 파일, upstream 92커밋 전진분).
    - **대응(사용자 "근본개선" 선호 → (B) 채택)**: deliver seam(server-restart-sentinel.ts)이 dispatchAssembledChannelTurn 직전 onSendAttemptStart, busy면 onSendDeferred 보고 → recovery가 turn-실행 경계에서 마커 set/clear, sendAttempted로 refuse/retry. P1#1(sent-before-error→refuse) + pre-send/busy/no-op retryable(at-least-once) + Layer B no-op over-refusal 동시 해소. force-push 501944c50a. P1#2=Risks 문서화 reply.
    - **byungskers(NONE, 04:03 O2 리뷰) 코멘트**: pre-send retry 경로(clearSessionDeliveryRecoveryState) 테스트 부재 지적. 정확(O2 기준). (B)에 이미 pre-send-never-started 테스트 + busy-deferred(clear) 테스트 추가 → head 4a7a072873. 간소 reply + @clawsweeper review 재트리거(ack 4589676277).
    - **clawsweeper R2(4a7a072873) = ready-for-maintainer-review**: Overall 🐚 platinum hermit, Proof 🦞 diamond lobster, Patch 🐚 platinum hermit (unranked krab→상승). proof:sufficient 재부여, status 👀 ready for maintainer look, CI FAILURE 0(base 이슈도 해소). "best bounded fix short of a larger adapter reconciliation API" 명시 인정. P1#1(코드결함) 해소. 잔여 [P1]2건=메인테이너 명시 수용 항목(fail-safe vs at-least-once 트레이드오프 + proof deterministic-harness)으로 둘 다 PR body Risks/What-was-not-tested에 문서화 완료. clawsweeper 권장=option1(트레이드오프 수용 후 merge). 우리 코드 액션 없음 → 메인테이너(steipete, #88665 owner 지목) 판단 대기.
  - #88013 (SOL-0016): **2026-06-01 최신 upstream(c0195f7ed5) 위 rebase → MERGEABLE**, head a2104c9b29(31536f00a1에서). 충돌=src/secrets/apply.ts commit-loop 1곳(upstream lint commit이 그 루프 `write`→`writeLocal` 리네임만; OURS=stage-then-commit 유지) + 새 strict no-shadow 규칙 위반으로 staging 루프 `write`→`writeLocal` 동반 리네임. 게이트: check exit0 + build exit0 + apply.test 22/22(Node24) + oxlint clean. clawsweeper 재트리거. (rating platinum 유지. supersede 아님 — upstream secrets 변경은 lint/cleanup.) diff_guard: 초기 randomUUID/node:crypto FAIL은 false-positive(tlon 테스트가 node:crypto를 자기목적 mock, secrets 미로드) → diff_guard에 node:builtin 제외 추가 후 OK.

### #88029 — fix(agents): atomic auth.json write to prevent credential lockout on crash  **MERGED 2026-05-31** (steipete)

- **유형**: 파이프라인 SOL-0014 (CAND-045 / FIND-agent-session-store-data-integrity-001, P1). issue #88028.
- **상태**: **MERGED 2026-05-31T19:06 by steipete**. 머지 직전 2026-05-31 upstream/main(5c5711f061) 위 linear rebase(사용자의 옛 merge-of-main 커밋을 fix 커밋 685ff77ab9로 교체) + force-push. build+check green.
- **fix**: `withLock`/`withLockAsync` 의 raw `writeFileSync(auth.json)`+`chmodSync` → `replaceFileAtomicSync` (temp+rename, `syncTempFile`+`syncParentDir` durable flush). 2-hunk + 회귀테스트.
- **proof**: post-sol collected (real-process RLIMIT_FSIZE interrupted-write: base raw lockout 20/20 / head atomic 0/20). `proofs/PROOF-SOL-0014-post-20260529-113603.md`. PR body `evaluateRealBehaviorProof`=passed.
- **트리거 서사**: interrupted write = disk-full(ENOSPC)/quota(EDQUOT)/power-loss. SIGKILL 은 macOS 단일 write() syscall 이라 비-트리거(PR body What-was-not-tested 에 명시).
- **clawsweeper R1 (head 46a1a9478e)**: `proof: supplied`+`proof: sufficient`(🦞 diamond lobster) + patch [P1] durability finding(`syncTempFile`/`syncParentDir` 누락 → claim 한 power-loss 미구현). `P2`/`merge-risk: auth-provider`/`rating: gold shrimp`/`status: waiting on author`.
- **대응 (head edd0dbf578)**: 두 호출에 `syncTempFile: true`+`syncParentDir: true` 추가(power-loss durable) → 대상 회귀테스트 green → force-push + PR body 갱신 + `@clawsweeper review` 재트리거(comment 4575917779). **R2 verdict 대기.**
- **게이트**: secops(`/src/agents/**/*auth*.ts`) CODEOWNERS 승인 필요. clawsweeper R2 대기 (Greptile 2026-05-29 폐지).

### #68669 — fix(agents): dedupe subagent browser session cleanup wrapper with dispatch flag  **MERGED 2026-05-30** (steipete)

- **유형**: 파이프라인 CAND-011, post-harness + pre-pr + post-commit cross-review (총 11 agent) 모두 real
- **상태**: **MERGED 2026-05-30T20:04 by steipete** (mergeCommit a9a86f788b). 장기 대기 후 직접 머지.
- **라벨**: `proof: supplied + sufficient`, `triage: refactor-only` (vincentkoc 일괄 부여, 우리 PR 도 영향)
- **steipete 코멘트 (2026-04-25)**: "Codex deep review: this looks correct and worth landing" — Bug/behavior 인정
- **결정**: 무대응 유지. close 트리거 시 (a) race fix 논거 제시 + reopen 또는 (b) CAL-010 credit-only.
- **관련**: issue #68668

### #71648 — fix(mcp): bound pendingClaudePermissions / pendingApprovals via TTL sweeper + close clear  **MERGED 2026-05-30** (steipete)

- **유형**: 파이프라인 CAND-025 → SOL-0008, pre-pr 3/3 real (round 2)
- **상태**: **MERGED 2026-05-30T20:36 by steipete** (mergeCommit c6b1fede5a). 2026-05-30 upstream/main 위 rebase(504 커밋 forward) 후 머지.
- **라벨**: `proof: supplied` 만 (sufficient 미부여). 추정 원인: V4 evidence 가 `vi.useFakeTimers` 사용 → real wall-clock 측정과 차이 (다른 5 PR 은 sufficient 자동 부여)
- **fix scope**: A (sweeper+ttl-only). cap/FIFO 의도적 후속 PR 분리
- **CI**: tsgo + 8/8 unit + check-test-types green (eef0be2a2e 에서 BridgeInternals intersection type 좁힘)
- **결정**: 메인테이너 리뷰 대기. real wall-clock 재시도 가치는 mcp-pending-ttl scenario 의 TTL env override hook 도입에 의존
- **관련**: Closes #71646

## 종결된 PR

### #82482 (CAND-040 → SOL-0013, **MERGED 2026-05-16** infra approval handler drop stopped delivery)
- **결과**: merged by steipete at 2026-05-16T15:41:08Z. merge_commit `2fcaab0010e5b44b1c4de22aa24edc12ff3e6abc`, head SHA `47c2487b797dc7bbe5dd4812c0b4b538bba28e60`. closing issue #82485
- **fix**: approval-handler-runtime.ts onStopped 시 in-flight `deliverPending` 의 후속 `bindPending` 을 차단하고 activeEntries 재삽입 leak 회피. 추가로 matrix channel cleanup hook (`cancelDelivered`) 옵셔널 정의 + matrix config-update test + 채널 플러그인 docs
- **evidence**: PROOF-CAND-040-pre / PROOF-SOL-0013-post (production-faithful e2e, with-fix 0 leak vs without-fix 3/3 leak, fire_rate 1.0)
- **steipete merge 코멘트 (Gate)**: pnpm test src/infra/approval-handler-runtime.test.ts + pnpm test extensions/matrix/src/matrix/config-update.test.ts + pnpm lint:extensions + pnpm docs:list + git diff --check + codex-review clean + CI 47c2487b green (check-lint, check-additional-extension-bundled, checks-node-core-fast/runtime-shared, build-artifacts, CodeQL critical quality, real behavior proof)
- **라벨 시소**: `proof: sufficient` clawsweeper 부여 ↔ openclaw-barnacle bot 제거 4회 반복. 머지 직전 unlabeled 상태로 머지 (메인테이너 manual judgment). R2 5-agent codex-rebuttal + R3 3-agent self-review 사이클 (commit c8d8629) 이 sufficient retention 에 기여
- **clawsweeper Codex 평가**: "Codex review: needs maintainer review before merge. Sufficient (logs)" + actionable finding 없음
- **교훈**: clawsweeper sufficient 라벨이 머지 보장 아님. 메인테이너가 proof 라벨과 무관하게 manual merge 가능. 단 라벨 시소 자체가 force-push synchronize 흐름과 충돌하지 않음 (#82482 는 force-push 후에도 머지 도달)

### #82483 (CAND-038 → SOL-0011, **CLOSED 2026-05-16 — maintainer-verdict reject, invariant 위반**)
- **결과**: closed (not merged) by steipete at 2026-05-16T15:25:42Z. mergedAt null. 16분 후 #82482 머지된 동일 세션
- **fix (우리)**: gateway/server/ws-connection.ts close handler 에 chat-abort helper import + ownerConnId 매칭 `chatAbortControllers` entry 일괄 abort + 단위 테스트
- **evidence**: PROOF-CAND-038-pre (production bundle + mock OpenAI, 3 trials, mock_client_disconnected=[], probe_wsCloseCode=1000) 으로 ws close 후 close handler 의 abort 미실행 직접 측정
- **메인테이너 verdict (steipete close 코멘트)**: "WebSocket disconnect is not the ownership boundary for an active run. The socket is primarily an observation/control channel: a tab refresh, transient network drop, or another client in the same session should be able to reconnect and observe the still-running agent. Treating every WS close as cancellation would regress that intended behavior. Explicit cancellation should continue to go through the existing stop / abort paths, and abandoned runs remain covered by the timeout cleanup path."
- **clawsweeper Codex P2 (06:39 review)**: "Preserve reconnectable runs on transient WS closes (`src/gateway/server/ws-connection.ts:395-396`)" + Best solution: "grace/rebind-aware cleanup path that preserves reconnect" — 메인테이너 verdict 와 본질 동일 지적. 우리는 CAL-009 프로토콜에서 반박 가능 판단했으나 invariant 자체가 무너짐
- **교훈 후보 (CAL 작성 대상)**: cross-review 5-agent (positive/critical/hot-path-tracer/reproduction-realist/upstream-dup-checker) 가 모두 fix scope 만 검증하고 lifecycle invariant 자체 (ws = observation/control vs ownership) 를 흔들지 못함. CAL-001 (post-merge reject) 과 다른 결: **pre-merge reject 이며 bot 도 같은 지적을 했으나 우리가 반박**. 신규 cross-review 역할 후보 — "lifecycle-invariant-challenger" (메인테이너 시각에서 fix 의 핵심 가정 자체를 의심)
- **답변 (2026-05-18 Feelw00)**: stop/abort + timeout cleanup 분리 수용. closing makes sense

### #82426 (CAND-026 → SOL-0010, **CLOSED 2026-05-16 — indirect-merge with credit, CAL-010 두 번째 사례**)
- **결과**: 사실상 win — 직접 merge 아님. 메인테이너 joshavant 가 commit `b7d61c8daf` (PR #82443, "fix: forward MCP tool abort signals") 로 우월한 fix 를 main 에 직접 머지 + closeout 코멘트로 우리 진단·소스 fix 인정 + closeout 호출
- **경로**: 2026-05-15 wire-level e2e proof (production bundle stdio + 실 SDK Client + notifications/cancelled, signalDefined false→true) → 2026-05-16 발행 (clawsweeper Codex 평가 "Sufficient + needs maintainer landing choice") → 동일 06:33 - 05:07 sub 1h closeout. 사람 리뷰 0건 / clawsweeper 라벨링 + Codex 평가만
- **fix (우리)**: setRequestHandler `(request, extra)` + extra.signal → callTool signal param → tool.execute 4번째 인자 (XS, 3 hunk / 2 files)
- **fix (main, 우월)**: 동일 core wiring (SDK signal preserve + tool.execute(..., signal) 전달). 핵심 차이는 **regression test 의 timing 견고성** — 우리: fixed `setTimeout(r, 20)` 후 abort. 머지본: tool.execute 가 signal 수신할 때까지 await 후 abort → assertion 이 timing 의존 없음
- **메인테이너 코멘트**: "Your diagnosis and source fix were correct ... #82443 became the closeout PR because its regression test waits until `tool.execute` has actually received the signal before aborting, which makes the cancellation assertion less timing-dependent than a fixed sleep. Appreciate the careful report, patch, and proof."
- **clawsweeper Codex 평가**: 5가지 모두 인정 (Current main drops the SDK handler extra / Current main calls execute without a signal / Tool contract already has a cancellation parameter / MCP SDK contract supplies the signal / PR forwards the existing signal path)
- **교훈 (CAL-010 확장)**: 다음 SOL 의 regression test 는 sleep-based timing 회피 → "await actual state transition before triggering action" 패턴 사용. fixed sleep 의 race window 가 closeout PR 의 머지 우선순위를 결정한 첫 사례 (이전 CAL-010 #70142 는 helper 반환 contract 활용 차이)
- **관련**: Closes #82424. closeout PR #82443

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

### #68842 (CAND-014, **MERGED 2026-04-21**)
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
