---
id: FIND-infra-retry-concurrency-003
cell: infra-retry-concurrency
title: retryAsync 가 retryAfterMs 에도 jitter 를 적용해 서버 Retry-After 시간을 하방 위반한다
file: src/infra/retry.ts
line_range: 113-121
evidence: "```ts\n    const retryAfterMs = options.retryAfterMs?.(err);\n    const\
  \ hasRetryAfter = typeof retryAfterMs === \"number\" && Number.isFinite(retryAfterMs);\n\
  \    const baseDelay = hasRetryAfter\n      ? Math.max(retryAfterMs, minDelayMs)\n\
  \      : minDelayMs * 2 ** (attempt - 1);\n    let delay = Math.min(baseDelay, maxDelayMs);\n\
  \    delay = applyJitter(delay, jitter);\n    delay = Math.min(Math.max(delay, minDelayMs),\
  \ maxDelayMs);\n```\n"
symptom_type: concurrency-race
problem: '`retryAsync` 는 서버가 명시한 `Retry-After` (예: Telegram 429 의 `parameters.retry_after`)

  를 `retryAfterMs` 로 받은 뒤에도, 이어지는 L120 `applyJitter(delay, jitter)` 에서 **대칭**

  jitter 를 적용한다. `applyJitter` 수식(retry.ts:61-67)은 `[-jitter, +jitter]` 범위이므로

  최종 delay 가 `retryAfterMs` 보다 **작아질 수 있다**. L121 의 최종 clamp 도 `minDelayMs`

  하한만 복원할 뿐 `retryAfterMs` 자체를 복원하지 않는다.

  결과적으로 서버가 "N ms 후에 다시 시도해라" 고 지시해도 클라이언트는 그보다 이르게 재시도

  할 수 있다. 서버 측에서는 재차 429 를 반송하거나 엄격한 rate-limiter 에서는 ban 처리.

  '
mechanism: "1. 채널 send API 호출 → 서버가 429 + `{ parameters: { retry_after: 1 } }` 반환\
  \ (초 단위).\n2. `getChannelApiRetryAfterMs` (retry-policy.ts:31-51) 가 `1 * 1000 =\
  \ 1000` ms 계산, options.retryAfterMs 로 넘김.\n3. retry.ts:116-117: `baseDelay = Math.max(1000,\
  \ minDelayMs=400) = 1000`.\n4. retry.ts:119: `delay = Math.min(1000, maxDelayMs=30_000)\
  \ = 1000`.\n5. retry.ts:120: `applyJitter(1000, 0.1)` → `offset = (secureFraction*2\
  \ - 1) * 0.1`\n   → `1000 * (1 + offset)` → 범위 `[900, 1100]`.\n6. retry.ts:121:\
  \ `delay = min(max(delay, minDelayMs=400), 30_000) = [900, 1100]`.\n   → 최종 delay\
  \ 가 900ms 일 수 있음. **서버가 요구한 1000ms 보다 100ms 일찍 재시도**.\n7. 서버가 다시 429 반송 → attempt\
  \ 2 에서 또 retry → 재귀적 위반 + ban 위험.\n"
root_cause_chain:
- why: 왜 retryAfterMs 경로에도 jitter 가 적용되는가?
  because: L119-120 이 retry-after 분기와 exponential 분기를 구분 없이 같은 applyJitter 를 통과시킨다.
    `hasRetryAfter` 플래그는 baseDelay 계산에만 쓰이고 jitter 스킵 조건으로는 쓰이지 않는다.
  evidence_ref: src/infra/retry.ts:113-121
- why: 왜 최종 clamp 가 retryAfterMs 를 복원하지 않는가?
  because: L121 `Math.min(Math.max(delay, minDelayMs), maxDelayMs)` 는 전역 min/max 로만
    clamp. retryAfterMs 를 하한으로 쓰지 않는다. 이 분기에서 retryAfterMs 를 하한으로 다시 넣는 코드 부재.
  evidence_ref: src/infra/retry.ts:121
- why: 왜 applyJitter 가 대칭인 것이 문제를 키우는가?
  because: L65 `offset = (fraction*2 - 1) * jitter` 로 `[-jitter, +jitter]` 대칭. 음의
    offset 이 retryAfterMs 하방으로 delay 를 깎음. 만약 단방향 상방 jitter 였다면 이 FIND 성립 안 함.
  evidence_ref: src/infra/retry.ts:61-67
- why: 왜 기존 테스트가 이를 놓쳤는가?
  because: 'retry.test.ts:186-205 의 retryAfterMs 테스트들은 `jitter: 0` 으로만 실행(`runRetryAfterCase`
    함수는 retryAfterMs 를 받지만 jitter 는 암묵적 0). retry-after 와 jitter 교차 시나리오를 검증하지 않음.'
  evidence_ref: src/infra/retry.test.ts:22-47,186-205
impact_hypothesis: wrong-output
impact_detail: "정성 + 정량:\n- `CHANNEL_API_RETRY_DEFAULTS` (retry-policy.ts:7-12) 의\
  \ `jitter: 0.1` 이 default → 10% 하방\n  위반 가능. Telegram `retry_after` 가 1초일 때 최대 100ms\
  \ 일찍 재시도.\n- 위반 확률(대칭 가정): retry 중 약 50% 의 경우 `fraction < 0.5` 이므로 offset 이 음수 →\n\
  \  약 **50% 경로에서** 서버 지시보다 이르게 재시도.\n- Telegram 은 `retry_after` 를 엄격히 지키지 않으면 추가\
  \ 429 또는 일시 ban. 결과적으로\n  retry 루프가 즉시 exhaust 되고 메시지 송신 실패가 사용자에게 표면화.\n- `strictShouldRetry=false`\
  \ 경로(retry-policy.ts:27-29) 에서는 regex fallback 이 429 를\n  다시 retry 로 처리해 위반 누적 증가.\n"
severity: P2
counter_evidence:
  path: src/infra/retry.ts
  line: 113-121
  reason: "숨은 방어 / 기존 테스트 / primary-path inversion 3축 탐색:\n\n1) `rg -n \"hasRetryAfter\"\
    \ src/infra/retry.ts` → L115, L116 2건.\n   - L115: flag 선언.\n   - L116: baseDelay\
    \ 분기.\n   jitter 스킵 분기로 쓰이는 곳 **없음**.\n2) `rg -n \"retryAfter\" src/infra/retry-policy.ts`\
    \ → L60, L71, L108.\n   - retry-policy 는 `retryAfterMs` 를 retry.ts 로 넘기기만 하고 jitter\
    \ 처리에 개입하지 않음.\n   - `getChannelApiRetryAfterMs` (L31-51) 는 값만 추출.\n3) 기존 테스트\
    \ 매트릭스 (retry.test.ts):\n   | 테스트 | retryAfterMs | jitter | 하방 위반 확인? |\n   |---|---|---|---|\n\
    \   | \"uses retryAfterMs when provided\" (L188-191) | 500 | 0 | 검증 안 함 |\n  \
    \ | \"clamps retryAfterMs to maxDelayMs\" (L192-195) | 500 | 0 | 검증 안 함 |\n  \
    \ | \"clamps retryAfterMs to minDelayMs\" (L196-199) | 50 | 0 | 검증 안 함 |\n   |\
    \ \"uses secure jitter when configured\" (L207-229) | 없음 | 0.5 | retry-after 미사용\
    \ |\n   → **retryAfterMs 와 jitter 를 교차로 검증하는 테스트 없음**. 기존 테스트가 이 동작을 lock 하지 않음.\n\
    4) R-5 분류표:\n   | 경로 | 실행 조건 |\n   |---|---|\n   | jitter 를 retry-after 경로에서 스킵\
    \ | **부재** |\n   | 최종 clamp 가 retryAfterMs 를 하한으로 복원 | **부재** (L121 은 minDelayMs\
    \ 만 하한) |\n   | 음의 jitter offset 생성 | unconditional (L65 `fraction*2-1` 의 결과 `<\
    \ 0` 시) |\n   | hasRetryAfter 이면 jitter=0 강제 | **부재** |\n5) primary-path inversion:\
    \ \"이 위반이 성립하려면 어떤 guard 가 없어야 하는가?\" →\n   (a) retry-after 경로에서 jitter 스킵 또는\
    \ (b) final clamp 의 하한을 retryAfterMs 로\n   교체, 둘 중 하나. 코드상 둘 다 없음 → 위반 성립.\n6)\
    \ 부분 방어 존재: L121 최종 clamp 의 `Math.max(delay, minDelayMs)` 는 매우 작은 값에서\n   음의 offset\
    \ 으로 0 이하로 떨어지는 것만 막음. `retryAfterMs > minDelayMs` 인 정상\n   상황(Telegram 429 기준:\
    \ retryAfter=1000, minDelayMs=400)에서는 하한이 minDelayMs 이므로\n   retryAfter 하방 위반을\
    \ 막지 못함.\n\n약화 조건: 호출자가 `jitter: 0` 으로 설정하면 이 FIND 무효. 그러나\n`CHANNEL_API_RETRY_DEFAULTS.jitter\
    \ = 0.1` 이 default 이므로 실무 경로에서 성립.\n"
status: discovered
discovered_by: cron-reliability-auditor
discovered_at: 2026-04-18
cross_refs: []
domain_notes_ref: domain-notes/infra-retry.md
related_tests:
- src/infra/retry.test.ts
---
# retryAsync 가 retryAfterMs 에도 jitter 를 적용해 서버 Retry-After 시간을 하방 위반한다

## 문제

서버가 명시한 Retry-After (Telegram 429 응답의 `parameters.retry_after` 등) 를 받은 경우에도
retry 루프는 jitter 를 대칭 적용하여 최종 delay 를 지시값보다 작게 만들 수 있다. 결과적으로
클라이언트가 서버 지시를 위반하고 이른 재요청을 보내게 된다.

## 발현 메커니즘

1. send API 호출 → 서버 429 + `{ parameters: { retry_after: 1 } }` 응답.
2. `getChannelApiRetryAfterMs`(retry-policy.ts:31-51) 가 1000ms 추출.
3. retry.ts L116: `baseDelay = Math.max(1000, 400) = 1000`.
4. retry.ts L119: `delay = Math.min(1000, 30_000) = 1000`.
5. retry.ts L120: `applyJitter(1000, 0.1)` → `[900, 1100]`.
6. retry.ts L121: `Math.max(delay, 400)` 만 하한 → 최종 `[900, 1100]`.
7. 최악의 경우 **900ms 후 재요청** → 서버 1초 지시보다 100ms 이르다 → 다시 429 반송.

## 근본 원인 분석

1. L119-120 은 retry-after 분기(baseDelay 선택) 이후에 jitter 를 **무조건** 적용한다.
   `hasRetryAfter` 플래그는 baseDelay 계산 분기로만 쓰이고 jitter 스킵 조건으로는 쓰이지 않음.
2. L121 의 최종 clamp 는 `minDelayMs` 만 하한으로 쓰고 `retryAfterMs` 를 하한으로 복원하지 않음.
3. L65 `applyJitter` 의 수식은 `[-jitter, +jitter]` 대칭 → 50% 경로가 음의 offset 을 가짐.
4. 기존 테스트(retry.test.ts:22-47 의 runRetryAfterCase, L186-205 의 retryAfter 케이스들) 는
   `jitter: 0` 으로만 retry-after 를 검증 → 교차 시나리오 미커버 → 이 동작이 "스펙" 으로
   간주되지 않음에도 회귀 방어가 없다.

## 영향

- **impact_hypothesis: wrong-output** — 클라이언트가 서버 rate-limit 지시를 하방 위반.
- `CHANNEL_API_RETRY_DEFAULTS.jitter = 0.1` default → 10% 하방 편차 가능.
- 약 50% 확률로 `retryAfterMs` 보다 이른 재요청 → 연속 429 → retry budget 조기 소진 →
  상위 caller 에게 실패 전파. Telegram 의 경우 잦은 위반은 봇 계정 일시 ban 위험.
- 비-idempotent 송신 (`strictShouldRetry=false` 경로) 에서는 regex fallback 이 429 를 계속
  retry-able 로 처리 → 위반 누적.

## 반증 탐색

- **숨은 방어**: `hasRetryAfter` 플래그를 jitter 스킵으로 쓰는 분기 부재 (Grep 확인).
  final clamp 의 하한이 retryAfterMs 를 복원하지도 않음.
- **기존 테스트 커버리지**: retryAfter + jitter 교차 검증 부재. `runRetryAfterCase`(L22-47)
  는 `jitter: 0` 을 암묵적으로 설정. "uses secure jitter when configured"(L207-229) 는
  retryAfter 를 쓰지 않음 (rejectedValueOnce 만). 매트릭스상 빈칸.
- **호출자 설정**: CHANNEL_API_RETRY_DEFAULTS(retry-policy.ts:7-12) 가 `jitter: 0.1` default.
  호출자가 override 하지 않는 한 위반 경로.
- **주변 코드 맥락**: retry-policy.ts 는 `retryAfterMs` 를 받아 그대로 전달하므로 정책 레이어
  에서도 별도 방어 없음.
- **primary-path inversion**: 위반이 성립하려면 (a) jitter 를 retry-after 에 적용하거나 (b)
  final clamp 가 retryAfterMs 를 하한으로 쓰지 않아야 함. 둘 다 현재 코드에 존재 → 성립.

## Self-check

### 내가 확실한 근거
- L116-121 의 분기와 clamp 로직을 라인 단위로 읽음.
- `applyJitter` 수식이 대칭이라는 점은 L65 에서 직접 확인.
- 기존 테스트는 `jitter: 0` 으로만 retryAfter 검증 (runRetryAfterCase).
- `getChannelApiRetryAfterMs` 는 순수 변환 함수이며 정책 개입 없음.
- `CHANNEL_API_RETRY_DEFAULTS.jitter` 가 0.1 이라는 값은 retry-policy.ts:7-12 에서 직접 확인.

### 내가 한 가정
- Telegram/Slack/Discord 가 Retry-After 를 엄격히 지키지 않을 때 추가 429 또는 ban 으로
  대응한다고 가정. 각 채널 API 의 실제 제재 정책은 이번 allowed_paths 밖.
- "50% 확률로 음의 offset" 은 `generateSecureFraction` 이 `[0, 1)` 균등 분포라고 가정할 때.
  이 가정은 CSPRNG 성질상 타당.
- 사용자가 `strictShouldRetry=false` 를 default 로 쓴다고 가정. production 설정 미확인.

### 확인 안 한 것 중 영향 가능성
- Telegram API 의 실제 429 leniency (1초 요구에 900ms 가 실제로 429 재유발하는지).
  서버측 grace 가 있다면 severity 가 P3 로 내려갈 수 있음.
- 다른 플랫폼(Slack, Discord) 의 retryAfter 포맷이 `getChannelApiRetryAfterMs` 의 셰이프
  검사를 통과하는지 (L34-49 의 3중 optional chain). 파싱 실패 시 `undefined` 반환이라
  jitter 적용 문제는 해당 플랫폼에서 미발생할 수 있음.
- 호출자 중 일부가 `jitter: 0` 으로 override 한다면 해당 경로에서 본 FIND 무효. 전수 조사
  미수행.
