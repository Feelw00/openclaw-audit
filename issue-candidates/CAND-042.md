---
candidate_id: CAND-042
type: single
finding_ids:
  - FIND-channels-concurrency-002
cluster_rationale: |
  단일 CAND. 본 FIND 는 `createMessageReceiveContext` (src/channels/message/receive.ts:69-82)
  의 `ack()` 메서드가 `check → await onAck → set` 구조로 check-then-act race
  anti-pattern 을 갖고 있다는 단일 file 단일 메커니즘 결함이다. CAND-041 (typing
  keepalive ownership race) / CAND-043 (context-engine resolve snapshot race)
  와는 file / 메커니즘 / fix surface 모두 직교 — Step 1~3 (정확 중복 / 동일 file
  다른 각도 / cross-file 공통 root cause) 어느 묶음 기준도 충족 안 함 → Step 4
  single CAND.

  root_cause_chain[0] 의 because: "L73 `await params.onAck?.()` 가 platform-side
  의 실제 ack 전송 결과를 기다리도록 의도. 그러나 가드 변수 (ackState) 의 set 이
  await 이후 (L74) 에 있어 ''이미 ack 중'' 임을 다른 호출자에게 알릴 방법이 없음.
  atomic test-and-set 부재." — race 본질이 단일 함수 내 check/await/set 순서이며,
  다른 셀로 일반화되지 않는다. fix 도 같은 함수 본문 내 in-flight 표시 또는
  pendingAckPromise 캐싱으로 self-contained.

  borderline P3: production caller 가 plugin SDK export (channel-message.ts:45)
  를 거쳐 src/channels/extensions/** (allowed_paths 외) 의 4 adapter 에서 호출.
  본 셀에서 실제 concurrent 호출 빈도를 확정 불가 — gatekeeper 가 R-7 hot-path
  를 다른 셀 감사 시 또는 maintainer 응답 단계에서 재검증 필요.
proposed_title: "fix(channels): MessageReceiveContext.ack() check-then-await-then-set race fires platform onAck twice"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    receive_ts:
      - 8bfabd6bb1  # feat: add channel message lifecycle sdk (본 코드 도입 commit)
    channels_message_dir:
      - 09116464b6  # test: dedupe message lifecycle mock read
      - 69f7269e7d  # test: dedupe message send mock read
      - 16ce9c1618  # test: guard channel send mock calls
      - 6a1ae65b5c  # test: guard channel lifecycle mock calls
      - ac15f1887f  # test: guard channel mock calls
      - 933306475c  # perf: keep channel SDK runtime imports lazy
      - aa720a6bb7  # test: tighten durable message send assertions
      - 102a670cf3  # test: tighten channel message lifecycle assertions
      - 021565bd71  # test: tighten channel outbound bridge assertions
      - 90f2a0b23c  # test: tighten channel message receipt assertions
      - 666ed4d889  # test: tighten channel message contract assertions
      - a4b17d65a8  # refactor: consolidate message delivery API
      - 9d94e6f847  # test: tighten lifecycle nack assertion
  finding: |
    `src/channels/message/receive.ts` 직접 수정은 8bfabd6bb1 본 코드 도입 1 건.
    `src/channels/message/` 디렉터리 다른 commit 13 건은 모두 test / refactor /
    perf 축. ack race / in-flight token / atomic test-and-set 축 변경 0 건.
  pr_search:
    - 'gh pr list --repo openclaw/openclaw --state open --search "MessageReceiveContext OR ack race in:title,body"'
    - 'gh pr list --repo openclaw/openclaw --state open --search "ack onAck duplicate in:title,body"'
  related_open_pr: null
  related_open_pr_notes: |
    본 file 영역 (src/channels/message/receive.ts) OPEN PR 없음. 검색에서 잡힌
    PR 들 (#80845 voice-call email delivery, #78186 line webhook respond
    early) 은 다른 file / 다른 axis 로 file 영역 겹침 없음.
  duplicate_decision: not-duplicate
cross_refs: []
---

# fix(channels): MessageReceiveContext.ack() check-then-await-then-set race fires platform onAck twice

## 공통 패턴

본 CAND 는 `createMessageReceiveContext` (src/channels/message/receive.ts) 가
반환하는 ctx 의 `ack()` 메서드 (L69-77) 의 classic check-then-act race
anti-pattern 단일 메커니즘 결함이다.

```ts
ack: async () => {
  if (ctx.ackState === "acked") {     // L70 — check
    return;
  }
  await params.onAck?.();              // L73 — await between check and set
  ctx.ackState = "acked";              // L74 — set (too late)
  ctx.ackedAt = Date.now();
  delete ctx.nackErrorMessage;
},
```

check (L70) 와 set (L74) 사이에 `await` (L73) 가 있어 두 caller 가 동시
`.ack()` 를 호출하면 양쪽 모두 L70 check 를 통과 (state="pending") → 양쪽 모두
`await params.onAck()` 를 실행 → 동일 메시지에 대해 platform-side ack callback
이 두 번 발사.

`createMessageReceiveContext` 가 반환하는 ctx 는 plugin SDK
(src/channels/sdk/channel-message.ts:45 export) 를 통해 4 adapter
(telegram / slack / discord / whatsapp) 의 plugin 코드로 노출. 다중 stage 정책
(`ackPolicy: "after_receive_record"` / `"after_agent_dispatch"` /
`"after_durable_send"`, receive.ts:27-42) 별로 다른 stage 에서 ack 호출 → 동일
ctx 가 두 경로의 fan-in 으로 공유되거나 manual ack 경로와 stage-driven ack
경로가 동시 활성화 시 concurrent 호출.

## 관련 FIND

- **FIND-channels-concurrency-002** (P3, src/channels/message/receive.ts:69-82):
  `ack()` check-then-await-then-set race. in-flight token (예:
  `pendingAckPromise` / `acking` 플래그) 부재. AbortSignal 통합 부재. R-3 grep
  5종 매치 0건 (lock / abort / race / in-flight token / microtask).
  lifecycle.test.ts:298-309 의 ack 테스트가 sequential 만 다룸 (concurrent
  Promise.all 검증 없음).

## 영향

`impact_hypothesis: wrong-output` — platform 별 ack idempotency 차이로 영향
스펙트럼:

- slack: response_url POST / chat.postMessage ack reply 는 idempotent 아님
  → 두 번째 호출 시 "operation_already_completed" 또는 응답 형식 깨짐.
- discord: interaction respond 첫 응답만 유효 → InteractionAlreadyAcknowledged.
- whatsapp: webhook ack 응답 단일 ack 만 허용.
- telegram: setMessageReaction 류는 보통 idempotent → 영향 최소.
- message-queue 어댑터 (Kafka/SQS commit): double-commit 시 offset 가드에
  따라 noop 또는 오류.

정량 race window = `params.onAck?.()` 의 await 시간 (일반 platform API 100ms,
rate-limit 시 수 초). 본 셀에서 실제 production 빈도 측정 불가 — borderline P3
(extensions/** 가 allowed_paths 외).

## fix surface (gatekeeper / publisher 입력)

같은 파일 (`src/channels/message/receive.ts`) 내 1-2 hunk:

옵션 A (in-flight token):
```ts
ack: async () => {
  if (ctx.ackState === "acked") return;
  if (pendingAck) return pendingAck;        // 두 번째 호출 reuse
  pendingAck = (async () => {
    try { await params.onAck?.(); }
    finally { /* state transition */ }
  })();
  return pendingAck;
},
```

옵션 B (sync "acking" 가드):
```ts
ack: async () => {
  if (ctx.ackState === "acked" || ctx.ackState === "acking") return;
  ctx.ackState = "acking";                  // sync before await
  try {
    await params.onAck?.();
    ctx.ackState = "acked";
  } catch (e) { ctx.ackState = "pending"; throw e; }
},
```

회귀 테스트: lifecycle.test.ts 에 `await Promise.all([ctx.ack(), ctx.ack()])`
시나리오 추가 → onAck 호출 횟수 1 보장. nack 도 동일 패턴이므로 함께 확인.

## R-7 한계 / gatekeeper 검증 요청

본 FIND 는 production caller 가 src/channels/extensions/** 또는 plugin SDK
소비자에 있어 allowed_paths 외 → 실제 concurrent 호출 시나리오를 본 셀에서
확정 불가. **borderline P3**.

gatekeeper 검토 권장 항목:
- 4 adapter 중 어느 것이 동일 ctx 의 `.ack()` 를 single-path 로만 호출하는지.
  전부 single-path 라면 본 race 가 영원히 발현 안 함 → abandon 후보.
- `ackPolicy` 정책 fan-in 시나리오가 production 에서 실제 활성화되는지.
- maintainer 응답 시 "ctx 가 첫 호출 후 sealed 되어 외부 caller 가 두 번 호출
  안 함" 으로 반박될 가능성 — ctx shape 의 sealed 계약이 코드에 명시 안 됨이
  근거.

## upstream-dup 검사 결과

- `git log upstream/main --since="6 weeks ago" -- src/channels/message/receive.ts`
  → 1 건 (8bfabd6bb1, 본 코드 도입 commit). 후속 race 축 fix 0 건.
- `git log upstream/main --since="6 weeks ago" -- src/channels/message/` → 13
  commits 모두 test / refactor / perf 축. ack race 축 변경 0 건.
- `gh pr list --search "MessageReceiveContext OR ack race"` → 매치 0.
- `gh pr list --search "ack onAck duplicate"` → 매치 0.
- 결론: **not-duplicate**. 본 single CAND 발행 진행 (단 R-7 한계 명시).

## next steps (gatekeeper / publisher 입력)

- one-thing-per-PR 검토: "MessageReceiveContext ack atomic test-and-set" 한 축
  → XS 단일 PR 가능 (1-2 hunk / 1 file + 회귀 테스트 1 hunk).
- R-7 hot-path 재확인: gatekeeper 가 src/channels/extensions/** 의 4 adapter
  ack 호출 패턴을 sample 확인 → single-path 만이면 abandon, fan-in 또는
  retry/recovery 공유 시 진행.
- CODEOWNERS 검사: `src/channels/message/receive.ts` 는 보안 민감 경로 매치
  안 함 — 일반 ownership.
- AI-assisted 표시: PR 본문 12섹션 포함.
- maintainer 반론 대비 본문 명시: ctx shape 의 sealed 계약 부재 + plugin SDK
  export 로 외부 adapter 가 자유 호출 가능.
