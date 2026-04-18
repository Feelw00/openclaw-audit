---
candidate_id: CAND-009
type: single
finding_ids:
  - FIND-infra-retry-concurrency-003
cluster_rationale: |
  단일 FIND. `retryAsync` (src/infra/retry.ts:113-121) 가 서버 지시
  `retryAfterMs` 에도 대칭 jitter 를 적용해 `retryAfterMs` 하방으로 delay 가
  깎일 수 있는 문제.

  FIND-002 와 같은 jitter 축이지만 root cause 가 반대 (CAND-008 rationale 참조).
  FIND-001 과는 도메인 자체가 다름 (process listener vs retry delay).
  공통 fix 불가 → single CAND.
proposed_title: "retry.ts: retryAsync applies jitter to server-provided retryAfterMs, allowing undershoot"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-18
---

# retry.ts: retryAsync applies jitter to server-provided retryAfterMs, allowing undershoot

## 공통 패턴

단일 FIND. `retryAsync` 의 retry-after 분기가 jitter 스킵 / 하한 복원 둘 다 부재:
- L119-120: `hasRetryAfter` 플래그는 `baseDelay` 분기에만 쓰이고 jitter skip 조건이
  아님 → retry-after 경로에서도 `applyJitter` 무조건 적용.
- L121: 최종 clamp 가 `minDelayMs` 만 하한으로 쓰고 `retryAfterMs` 를 하한으로
  복원하지 않음.
- L65 `applyJitter` 수식이 `[-jitter, +jitter]` 대칭 → 약 50% 경로에서 음의 offset.

`CHANNEL_API_RETRY_DEFAULTS.jitter = 0.1` default 에서 서버가 `retry_after: 1`
(1000ms) 을 반환해도 최종 delay 가 900ms 일 수 있어 서버 지시 하방 위반. Telegram
등 엄격 rate-limiter 에서 추가 429 또는 일시 ban 위험.

근거:
- root_cause_chain[0]: `hasRetryAfter` 가 jitter 스킵 조건으로 쓰이지 않음.
- root_cause_chain[1]: L121 clamp 가 retryAfterMs 를 하한으로 복원하지 않음.
- root_cause_chain[2]: applyJitter 가 대칭이라 음의 offset 생성.
- root_cause_chain[3]: 기존 retry.test.ts 의 retryAfter 케이스가 `jitter: 0` 으로만
  검증해 교차 시나리오 미커버.

## 관련 FIND

- FIND-infra-retry-concurrency-003: retryAsync 가 서버 Retry-After 에도 대칭 jitter
  적용하여 지시값보다 이른 재요청 가능. `CHANNEL_API_RETRY_DEFAULTS.jitter=0.1`
  default 에서 약 50% 경로가 하방 위반. (P2)
