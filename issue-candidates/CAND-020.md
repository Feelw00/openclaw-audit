---
candidate_id: CAND-020
type: single
finding_ids:
  - FIND-channels-error-boundary-002
cluster_rationale: |
  단독 FIND. CAND-019 의 cluster_rationale 참조 — 같은 셀의 FIND-001 과
  symptom/fix 축 모두 독립.

  FIND-002 root_cause_chain:
    [0] ".then(onFulfilled) 에 onRejected 미지정 — ack-add reject 시 콜백
         skip + void swallow."
    [1] "onError 가 remove() 전용으로 오배선. ack-add 실패 관측 불가."
    [2] "Promise<boolean> 타입 시그니처는 caller 의 사전 catch 계약 강제
         불가 — 외부 계약 상 이 함수 내 방어 필요."

  FIND-001 은 데이터 유실 (pendingText drop) 동반 P2. FIND-002 는 visual
  상태 불일치 (stale emoji) + 드문 unhandledRejection propagation, P3. 사용자
  영향 경로와 fix 라인 수 모두 다름.
proposed_title: "channels/ack-reactions: removeAckReactionAfterReply .then has no onRejected — stale ack emoji + unhandled rejection"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-22
---

# channels/ack-reactions: `removeAckReactionAfterReply` 의 `.then(onFulfilled)` 에 onRejected 부재 → stale emoji + unhandled rejection

## 공통 패턴

단일 FIND 기반 single CAND. `src/channels/ack-reactions.ts:97-102` 의
`void params.ackReactionPromise.then((didAck) => ...)` 는 onFulfilled 만
지정. ack 부착 promise 가 reject 되면:
1. `.then` 콜백 skip → remove() 미호출 → stale ack emoji 가 메시지에 남음.
2. rejection 이 `void` chain 으로 escape → process-level unhandledRejection.

onError 파라미터는 line 101 `params.remove().catch` 에만 연결 — ack-add 실패는
못 받음.

## 관련 FIND

- FIND-channels-error-boundary-002 (P3): reply 완료 후 "ack emoji 제거" 모드
  에서 ack 부착이 rate-limit/permission 오류로 reject 하면 이모지가 stale 로
  남고, rejection 이 `src/infra/unhandled-rejections.ts:345` 로 propagate —
  transient warn, non-transient `process.exit(1)`.

## 근거 위치

- 문제 `.then`: `src/channels/ack-reactions.ts:97-102`
- onError 오배선 (remove 전용): `src/channels/ack-reactions.ts:101`
- 대조 (동일 도메인 올바른 패턴): `src/channels/status-reactions.ts:234-254`
  `applyEmoji` 의 try/catch + onError
- process-level 수신: `src/infra/unhandled-rejections.ts:345`

## 영향

- `impact_hypothesis: wrong-output` — "답장은 왔는데 👀 ack emoji 가 그대로"
  라는 시각적 상태 불일치.
- 빈도: Slack/Telegram/Discord 의 rate-limit / permission 오류 + "removeAfterReply
  true" 설정 조합에서만. 데이터 유실 없음.
- 추가: transient warn, non-transient process.exit(1) 가능성 (드물지만 real).
- severity P3 — 메시지 delivery 자체를 해치지 않음. wrong-output 이지만
  hot-path 아님.

## 대응 방향 (제안만)

세 옵션 중 하나 (혹은 조합):
1. `.then(onFulfilled, (err) => params.onError?.(err))` — 기존 onError 의미
   확장. API 문서화 필요.
2. caller (plugin-sdk / auto-reply) 에서 ackReactionPromise 생성 직후 사전
   `.catch(logger)` 부착 계약화. Promise<boolean> 타입을 이미-fulfilled 만
   받도록 좁히는 branded type 도입 고려.
3. 본 함수 내에서 `params.ackReactionPromise.then(...).catch((err) =>
   params.onError?.(err))` 로 chain 완결.

구체 구현은 SOL 단계.

## 중복 검사 (upstream)

`git log upstream/main --since="3 weeks ago" -- src/channels/ack-reactions.ts`
→ 0 commits. 중복 fix 없음.

## 반증 메모

- caller 가 이미 `ackReactionPromise.catch(...)` 를 선-부착해 fulfillment-only
  Promise 를 전달하는 계약이라면 본 FIND 는 성립 안 함 — plugin-sdk / auto-reply
  reply 파이프라인의 실제 구현 확인 필요 (FIND self-check 에 적시). 이 경우
  CAND drop 대상.
- ack 부착 어댑터가 실제로 throw 하는 빈도 — telegram/slack/discord reaction
  API 오류율 production telemetry 부재.
- CAL-017 관점: pure observability gap 이 아닌 wrong-output (stale emoji)
  경로가 있어 reliability scope 유지. 다만 P3 이라 CONTRIBUTING.md feature-
  freeze 와 tension. 제출 타이밍 판단 필요.
