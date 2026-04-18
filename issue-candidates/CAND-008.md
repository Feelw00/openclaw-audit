---
candidate_id: CAND-008
type: single
finding_ids:
  - FIND-infra-retry-concurrency-002
cluster_rationale: |
  단일 FIND. `computeBackoff` (src/infra/backoff.ts:8-12) 의 jitter 가
  단방향 (`[0, +base*jitter]`) + `Math.random()` (non-CSPRNG) 인 문제.

  FIND-003 과 같은 "jitter 인프라" 축으로 보이지만 root cause 가 정반대:
  - 002: jitter 가 *대칭이 아니어서* 문제 (방향성 부재가 원인).
  - 003: jitter 가 *대칭이어서* 문제 (음의 offset 이 retryAfterMs 하방 위반).
  파일도 다름 (backoff.ts vs retry.ts). 공통 fix 불가 — 한쪽을 고쳐도
  다른 쪽이 자동 해결되지 않음. Epic 근거 부재.

  따라서 single CAND 로 처리.
proposed_title: "backoff.ts: computeBackoff uses unidirectional non-CSPRNG jitter, inconsistent with retry.ts"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-18
---

# backoff.ts: computeBackoff uses unidirectional non-CSPRNG jitter, inconsistent with retry.ts

## 공통 패턴

단일 FIND. `computeBackoff` 의 jitter 수식이 `base * policy.jitter * Math.random()`
으로 단방향 (`[0, +base*jitter]`), 따라서 delay 는 `[base, base*(1+jitter)]` 로 상방
편향. 동시 실패 인스턴스들이 `base` 근방 좁은 window 에 밀집하여 thundering-herd
완화 효과가 반감.

추가로 같은 infra 폴더의 `retry.ts:65` `applyJitter` 는 `(generateSecureFraction()
* 2 - 1) * jitter` 로 대칭 + CSPRNG. 동일 infra 서브시스템 내 jitter 전략 이원화.

근거:
- root_cause_chain[0]: L10 수식이 `Math.random() ∈ [0, 1)` 을 곱해 `[0, +base*jitter]`.
- root_cause_chain[1]: retry.ts:65 와 수식·PRNG 모두 상이, 공유 헬퍼 없음.
- root_cause_chain[3]: backoff.test.ts:13-30 이 현행 단방향 수식을 잠금.

## 관련 FIND

- FIND-infra-retry-concurrency-002: `computeBackoff` jitter 가 상방 단방향 +
  `Math.random()` 사용. `CHANNEL_RESTART_POLICY.jitter = 0.1` 기준 동시 재시도
  window 밀도 2배, retry.ts 와 비일관. (P3)
