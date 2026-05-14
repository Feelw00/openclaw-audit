---
candidate_id: CAND-041
type: single
finding_ids:
  - FIND-channels-concurrency-001
cluster_rationale: |
  단일 CAND. 본 FIND 는 `createTypingKeepaliveLoop` (src/channels/typing-lifecycle.ts:38-45)
  의 `stop()` 이 in-flight tick 의 await 완료와 무관하게 `tickInFlight=false` 를
  강제 reset 한다는 단일 file 단일 메커니즘 결함이다. CAND-042 (message receive.ts
  ack race) / CAND-043 (context-engine resolve snapshot race) 와 file / 메커니즘 /
  fix surface 모두 직교 — Step 1~3 (정확 중복 / 동일 file 다른 각도 / cross-file
  공통 root cause) 어느 묶음 기준도 충족 안 함. 다른 ready FIND 들과 cross-file
  으로 묶을 공통 axis 없음 → Step 4 single CAND.

  root_cause_chain[0] 의 because: "L44 `tickInFlight = false` 가 stop 직후 다음
  tick 이 ''초기 상태'' 로 보이도록 의도된 reset. 그러나 in-flight tick 의
  finally (L25) 가 동일 변수를 false 로 set 할 예정이므로 stop 의 reset 은 sync
  시점에서 필요 없을 뿐 아니라 in-flight tick 의 ownership 을 가로채는 부작용을
  가진다" — 본 결함은 다른 셀로 일반화 불가, 단일 함수 본문 내 fix.
proposed_title: "fix(channels): typing keepalive stop() leaks tickInFlight reset, allows concurrent onTick after restart"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: abandoned
cross_review_metric: metrics/cross-review-CAND-041-20260514-082000.jsonl
retracted_reason: 'cross-review CAL-001: avg 0.63 abandon. critical-devil abandon/high — hidden guards 2개 발견. (1) channels/typing.ts:72-74 `if (closed) return` (unconditional) — `fireStop` (L91) 이 `closed=true` 를 `keepaliveLoop.stop()` 호출 *이전* unconditional set, heartbeat 경로에서 stop 후 새 onReplyStart 진입 자체가 불가능. (2) auto-reply/reply/typing.ts:124-127 `triggerInFlight` mutex — inner `channels/typing.ts:71` onReplyStart 의 유일한 호출 경로를 직렬화, 두 inner stop→fireStart→start 시퀀스 인터리브 불가. 회귀 테스트 (typing.test.ts:201-217 "does not restart keepalive after idle cleanup") 가 closed 가드의 race-차단 동작을 이미 검증. FIND 의 R-3 grep 5종 (Mutex|Semaphore|AsyncLock 등) 이 closed 같은 simple state-flag mutex 와 outer wrapper (auto-reply/reply/*) 를 검사 범위에서 누락 — CAL-001 그대로 재발.'
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    typing_lifecycle_ts:
      - "(none)"  # src/channels/typing-lifecycle.ts 직접 수정 0 건
    typing_ts:
      - ca01994900  # refactor: trim startup channel type exports
      - 45b8645079  # fix(channels): keep typing indicators off reply critical path
  finding: |
    `src/channels/typing-lifecycle.ts` 직접 수정 6 주 0 건.
    `src/channels/typing.ts` 는 2 commit — ca01994900 은 type export 정리
    (race 축 무관), 45b8645079 (clawsweeper #75403 의 원본 commit) 은 onReplyStart
    의 fireStart 를 fire-and-forget 으로 옮긴 변경 (typing.ts:79). 이 변경은
    stop→start 시퀀스의 *상대 순서* 만 조정했을 뿐 typing-lifecycle.ts:44 의
    `tickInFlight=false` 강제 reset 자체 (본 race 의 1차 원인) 는 그대로 유지.
    in-flight tick ownership 토큰 / self-token finally / await pending 등
    본 fix 축에 해당하는 변경 0 건.
  pr_search:
    - 'gh pr list --repo openclaw/openclaw --state open --search "typing-lifecycle OR typing keepalive in:title,body"'
    - 'gh pr list --repo openclaw/openclaw --state open --search "typing tickInFlight OR keepalive race in:title,body"'
  related_open_pr: 75403
  related_open_pr_notes: |
    PR #75403 (clawsweeper-commit-openclaw-openclaw-45b86450795d, OPEN 2026-05-01)
    이 src/channels/typing.ts 의 onReplyStart fire-and-forget 회귀를 다룬다.
    그러나 그 PR 의 fix surface 는 `typing.ts` (auto-reply 측 cleanup 직렬화) 한정
    이고 본 CAND 의 fix surface 는 `typing-lifecycle.ts:38-45` 의 stop / tick
    ownership 패턴 (다른 file, 다른 race 축) 이다. 두 race 가 공존 가능 (PR #75403
    은 "stop 이 pending start 보다 먼저 도착하는 race", 본 CAND 는 "stop 이 in-flight
    tick 의 ownership 토큰을 가로채 다음 tick 이 concurrent onTick 발사" — root
    cause axis 직교). duplicate 아님.
  duplicate_decision: not-duplicate
cross_refs: []
---

# fix(channels): typing keepalive stop() leaks tickInFlight reset, allows concurrent onTick after restart

## 공통 패턴

본 CAND 는 `createTypingKeepaliveLoop` (src/channels/typing-lifecycle.ts) 의
`stop()` (L38-45) 이 in-flight tick 의 ownership 플래그 `tickInFlight` 를
**unconditional** 으로 `false` 로 reset 하는 단일 메커니즘 결함이다.

```ts
const stop = () => {
  if (!timer) {
    return;
  }
  clearInterval(timer);
  timer = undefined;
  tickInFlight = false;  // ← 이전 tick 의 await 미완 여부 무시
};
```

`tick()` 자체는 L17-27 에서 `if (tickInFlight) return; tickInFlight=true;
try { await params.onTick(); } finally { tickInFlight=false; }` 구조로
single-in-flight 를 의도하지만, `stop()` 이 in-flight tick 의 플래그를 가로채면
다음 `start()` 후의 새 tick 이 "tickInFlight===false" 를 보고 두 번째
`params.onTick()` 을 concurrent 로 실행.

production hot-path 인 `src/channels/typing.ts:71-88 onReplyStart` 는 매 reply
turn 마다 L77 `keepaliveLoop.stop()` → L79 `fireStart()` →
`.then(()=>{ keepaliveLoop.start(); })` (L80-86) 시퀀스를 실행. tick 의
`await params.onTick()` 가 platform API (telegram sendChatAction / slack
assistant.threads.setStatus / discord trigger-typing) 의 latency 또는 rate-limit
백오프로 await 중일 때 이 시퀀스가 trigger 되면 concurrent onTick 발현.

## 관련 FIND

- **FIND-channels-concurrency-001** (P3, src/channels/typing-lifecycle.ts:38-45):
  `stop()` 의 L44 `tickInFlight=false` 강제 reset 이 in-flight tick ownership
  토큰을 가로챔. self-token 검증 패턴 부재 (tick 이 myTickId 들고 finally 에서
  자기 소유만 reset 하는 패턴 없음). AbortController 통과 부재로 in-flight
  cancel 도 불가. R-3 grep 5종 (lock / abort / race / listener-sync / microtask)
  매치 0건.

## 영향

`impact_hypothesis: wrong-output` — 한 conversation 의 typing indicator 가
짧은 시간 안에 platform API 로 burst 호출. 정량 race window = tick 의 await
시간 (platform API latency 100-500ms, rate-limit 시 수 초). 매 reply turn 의
onReplyStart 가 이 window 와 겹치면 발현.

직접 영향: typing indicator 시각적 burst (P3 수준). 간접 영향:
typing-start-guard.ts:41 `consecutiveFailures += 1` 카운터가 두 concurrent
catch 경로에서 동시 증분되면 maxConsecutiveFailures=2 default (typing.ts:26)
에 빨리 도달 → onTrip 콜백 (typing.ts:36-38) 에서 `keepaliveLoop.stop()` 가
정상 turn 중간에 keepalive 를 끄게 됨 → 사용자 관점에서 typing indicator 가
reply 완료 전 사라지는 결함.

## fix surface (gatekeeper / publisher 입력)

같은 파일 (`src/channels/typing-lifecycle.ts`) 내 1-2 hunk:

1. tick 의 self-token 패턴 (`const myTickId = ++lastTickId; ... finally { if
   (currentTickId === myTickId) tickInFlight=false; }`) 도입, **또는**
2. `stop()` 의 L44 `tickInFlight=false` 제거 (finally 에서만 reset),
   **또는**
3. `stop()` 을 async 로 만들고 pending in-flight tick promise 를 await.

세 옵션 중 어느 하나로 race 차단 가능 (단일 file 1 hunk). 회귀 테스트:
typing.test.ts 에 "tick 이 await 중일 때 stop→start 시퀀스 → 새 tick 이
concurrent onTick 발사 안 함" 시나리오 추가.

## upstream-dup 검사 결과

- `git log upstream/main --since="6 weeks ago" -- src/channels/typing-lifecycle.ts`
  → 0 건. 본 file 직접 수정 부재.
- `git log upstream/main --since="6 weeks ago" -- src/channels/typing.ts`
  → 2 건. ca01994900 (type export 정리, race 축 무관) +
  45b8645079 (clawsweeper PR #75403 의 원본 commit, onReplyStart fire-and-forget).
  본 race (tickInFlight ownership) 와 다른 race 축.
- `gh pr list --search "typing-lifecycle OR typing keepalive in:title,body"` →
  관련 OPEN PR 중 본 fix surface 와 직접 겹치는 것 없음 (PR #75403 은 typing.ts
  한정).
- 결론: **not-duplicate**. 본 single CAND 발행 진행.

## next steps (gatekeeper / publisher 입력)

- one-thing-per-PR 검토: "typing keepalive in-flight tick ownership guard" 한 축
  → XS 단일 PR 가능 (1 hunk / 1 file + 회귀 테스트 1 hunk).
- 회귀 테스트: typing.test.ts 에 await-in-flight 시나리오 추가
  (`new Promise(() => {})` 또는 deferred resolve 로 onTick 을 영원히 await
  시킨 후 stop→start 시퀀스 → params.start 호출 횟수 1회 보장).
- CODEOWNERS 검사: `src/channels/typing-lifecycle.ts` 는 `*auth*` / `sandbox*` /
  `cron/service/jobs.ts` / `cron/stagger.ts` 매치 안 함 — 일반 ownership.
- PR #75403 와 file 영역 겹침 없음 (typing.ts vs typing-lifecycle.ts) → rebase
  충돌 위험 없음. 단 PR #75403 이 먼저 merge 되면 onReplyStart 시퀀스의 정확한
  라인 번호가 시프트할 수 있으므로 PR 본문에 "rebase 시 typing.ts 라인 시프트
  주의" 명시.
- AI-assisted 표시: PR 본문 12섹션 (openclaw-contribution.md 요건) 포함.
