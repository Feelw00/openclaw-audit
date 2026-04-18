# CAL-005: Bot reviewer contradiction — PR #68543 boundary 해석

**날짜**: 2026-04-18
**PR**: openclaw#68543 (CAND-009)

## 이슈

같은 한 줄 (`canHonorRetryAfter: retryAfterMs <=/< maxDelayMs`) 에 대해 두 봇이 **정반대 P1** 지적.

| 봇 | 지적 | 방향 |
|---|---|---|
| Greptile (rebase 후 first review) | `retryAfterMs === maxDelayMs` 에서 positive jitter → final clamp → 모든 retry 동시 fire | `<=` → `<` (symmetric fallback 확장) |
| Codex (그 직후) | `<` 로 바꾸면 symmetric jitter 가 Retry-After 하방 위반 (1000→500) | `<` → `<=` (positive 유지) |

## 결정

**Codex 쪽** 선택. 근거:
1. PR 의 primary goal (본문 명시): "Server-supplied Retry-After is a lower-bound contract... symmetric jitter would let roughly half the retries land before the requested time and invite escalation."
2. Retry-After 하방 위반 → 외부 rate limiter (Telegram/Discord) 이 429 로 추가 제재 → 실 사용자 영향
3. Boundary thundering herd → 모두 `maxDelayMs === retryAfterMs` 에서 동시 fire. 하지만 그 시점은 서버가 이미 "retry OK" 라고 알린 시점. 실 영향 local coordination 만.
4. 즉 contract 위반 > local coordination 낭비

비대칭적 trade-off 선호.

## 파이프라인 교훈

- 2 개 이상 봇이 충돌하는 리뷰는 **PR 의 명시된 의도** 로 타이브레이커
- 봇 review 는 local correctness 검증에 강하지만 **design intent priority** 판단은 제한적
- 새 규율 제안 (R-9): bot 지적이 PR 본문 의도와 상충하면 의도 우선. 답변에 명시적으로 "intent hierarchy: A > B" 명시.

## 최종 코드

```ts
// retry.ts:141-145
const canHonorRetryAfter =
  hasRetryAfter && typeof retryAfterMs === "number" && retryAfterMs <= maxDelayMs;
delay = applyJitter(delay, jitter, canHonorRetryAfter ? "positive" : "symmetric");
delay = Math.min(Math.max(delay, minDelayMs), maxDelayMs);
```

Boundary regression test 로 `delays[0] === 1000` (contract honor at cap) 고정.

## 메타 관찰

- Cross-review 가 양 방향 모두 잡아 **의도 문서화** 가 강해짐 — 메인테이너가 리뷰할 때 trade-off 가 명시적.
- CAL-003 (self-cross-review) 이후로 4번째 calibration. 각 CAL 이 파이프라인 다른 blind spot 을 drain.
