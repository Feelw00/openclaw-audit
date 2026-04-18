---
id: FIND-infra-retry-concurrency-002
cell: infra-retry-concurrency
title: computeBackoff 의 jitter 가 단방향·비암호화라 동시 실패 인스턴스들이 같은 window 로 몰린다
file: src/infra/backoff.ts
line_range: 8-12
evidence: "```ts\nexport function computeBackoff(policy: BackoffPolicy, attempt: number)\
  \ {\n  const base = policy.initialMs * policy.factor ** Math.max(attempt - 1, 0);\n\
  \  const jitter = base * policy.jitter * Math.random();\n  return Math.min(policy.maxMs,\
  \ Math.round(base + jitter));\n}\n```\n"
symptom_type: concurrency-race
problem: '`computeBackoff` (src/infra/backoff.ts:8-12) 의 jitter 는 `base * policy.jitter
  * Math.random()`

  형태로, 값이 항상 `[0, +base*jitter]` 범위이다. 음의 방향이 없어 실제 delay 가 `[base, base*(1+jitter)]`

  로 단방향 편향된다. 동시다발적으로 재시작이 필요한 다수의 인스턴스(채널/에이전트) 가 같은

  initial delay window 앞쪽에 집중되어, thundering-herd 완화라는 jitter 의 본래 목적이 반감된다.

  추가로 PRNG 로 `Math.random()` 을 쓰는데, 동일 프로세스 내 `src/infra/retry.ts` 의 `applyJitter`

  는 `generateSecureFraction()` (CSPRNG) 을 쓰는 비일관 이원화 상태.

  '
mechanism: "1. 다수 인스턴스(예: N개 Telegram 계정 채널) 가 동일 원인(네트워크 단절 복구, 외부 API 장애 회복) 으로\n\
  \   동시에 restart 경로 진입 (`server-channels.ts:460` → `computeBackoff(CHANNEL_RESTART_POLICY,\
  \ attempt)`).\n2. `CHANNEL_RESTART_POLICY = { initialMs: 5_000, factor: 2, jitter:\
  \ 0.1, maxMs: 300_000 }` (server-channels.ts:22-27).\n3. attempt=1 → `base = 5_000`,\
  \ `jitter ∈ [0, 500]` → `delay ∈ [5_000, 5_500]`.\n   → 500ms 폭 window 안에 N 개 인스턴스가\
  \ 모두 재시도 진입.\n4. 대칭 jitter 였다면 `[4_500, 5_500]` 1_000ms 폭으로 두 배 분산.\n   단방향이기 때문에\
  \ 분산 효율이 절반.\n5. attempt=6 → `base = 160_000` → `delay ∈ [160_000, 176_000]`. max\
  \ 로 clamp 전에는 폭이 16_000ms\n   로 늘어나지만 여전히 \"delay 는 base 이상\" 이라는 편향 유지.\n6. `Math.random()`\
  \ 은 process-shared PRNG 로, 동일 node.js 프로세스에서 여러 번 호출 시 좋은 균등\n   분포를 주지만 **다중 인스턴스\
  \ 간 독립성**을 보장하는 보안적 randomness 와는 다르다.\n   retry.ts 쪽이 `generateSecureFraction`\
  \ (L65) 을 쓰는 것과 비일관.\n"
root_cause_chain:
- why: 왜 jitter 가 항상 양의 방향인가?
  because: L10 수식 `base * policy.jitter * Math.random()` 은 `Math.random() ∈ [0, 1)`
    이므로 곱한 결과도 `[0, +base*jitter]`. 음수 offset 이 만들어지지 않는다.
  evidence_ref: src/infra/backoff.ts:10
- why: 왜 retry.ts 와 다른 수식을 쓰는가?
  because: retry.ts:65 의 applyJitter 는 `(generateSecureFraction() * 2 - 1) * jitter`
    로 `[-jitter, +jitter]` 대칭. 두 파일이 같은 infra 폴더에 있음에도 jitter 전략이 서로 다르며 공유 헬퍼가 없다.
  evidence_ref: src/infra/retry.ts:61-67
- why: 왜 Math.random 을 쓰는가?
  because: backoff.ts 는 `generateSecureFraction` 을 import 하지 않는다 (L1 이후 import 문 자체
    부재). retry.ts 는 같은 폴더의 secure-random.js 를 import (L3).
  evidence_ref: src/infra/backoff.ts:1
- why: 왜 이 불일치가 유지되는가?
  because: backoff.test.ts:13-30 는 jitter 범위 검증을 `Math.random` 을 0 또는 1 로 spy 하여 경계값만
    확인. 대칭성/CSPRNG 요구를 lock 하는 테스트 없음. 기존 테스트가 현행 수식을 '의도' 로 받아들이고 있음.
  evidence_ref: src/infra/backoff.test.ts:13-30
impact_hypothesis: resource-exhaustion
impact_detail: "정성 + 정량:\n- `CHANNEL_RESTART_POLICY` 기반: N 개 채널이 동시에 장애 회복 시 `[base,\
  \ base*1.1]` 10% window\n  안에 모든 재시도가 몰린다. 대칭 jitter 대비 **동시 재시도 밀도 2배**.\n- 외부\
  \ API (Telegram/Slack/Discord) 에 대한 순간 부하 증폭 → 재차 rate limit 응답 → 재진입 루프.\n- `computeBackoff`\
  \ 직접 호출자는 2곳 (`server-channels.ts:460`, `agents/context.ts:188`).\n  두 호출 모두 jitter=0.1\
  \ 수준으로 추정(확인된 CHANNEL_RESTART_POLICY 값 기준).\n- `Math.random()` 은 crypto-random 이\
  \ 아니므로 multi-process 에서 PRNG 가 seed 공유 시 더욱 클러스터링\n  될 수 있으나, node.js 기본 Math.random\
  \ 은 process-local 이라 현실적 severity 는 P3 수준.\n"
severity: P3
counter_evidence:
  path: src/infra/backoff.ts
  line: 8-12
  reason: "숨은 방어 / 기존 테스트 / 설정 3축 탐색:\n\n1) `rg -n \"Math.abs|symmetric|signed.*jitter\"\
    \ src/infra/backoff.ts` → 매치 0건.\n   대칭 변환 코드 없음.\n2) `rg -n \"generateSecureFraction\"\
    \ src/infra/backoff.ts` → 매치 0건.\n   backoff.ts 는 CSPRNG 를 import 하지 않음. retry.ts:3\
    \ 과의 대비.\n3) 기존 테스트 (backoff.test.ts:13-30):\n   - `Math.random` 을 0 으로 mock →\
    \ `computeBackoff(policy, 0) === 100` (base 정확).\n   - `Math.random` 을 1 로 mock\
    \ → `computeBackoff(policy, 2) === 250` (maxMs clamp).\n   jitter 가 항상 base 이상이라는\
    \ **현행 구현을 검증**. 대칭성/하방 편차를 요구하는\n   테스트 없음.\n4) R-5 분류표:\n   | 경로 | 실행 조건 |\n\
    \   |---|---|\n   | `policy.jitter = 0` 일 때 jitter=0 → 단방향이어도 문제 없음 | conditional-edge\
    \ (policy 에 따라) |\n   | `policy.jitter > 0` 일 때 `[base, base*(1+jitter)]` 편향 |\
    \ unconditional (L10 수식은 무조건 이 형태) |\n   | 대칭 jitter 로 분산 | **부재** |\n5) primary-path\
    \ inversion: \"thundering-herd 분산이 제대로 되려면 어떤 대칭 로직이 필요한가?\"\n   → 현재 파일에 대칭 로직\
    \ 없음. 오히려 retry.ts:65 에 존재하는 것이 backoff.ts 에 없는\n   상태 = 명백한 불일치.\n6) 보안적 randomness:\
    \ backoff 가 보안 경계가 아니므로 CSPRNG 요구는 약함 (P3 수준 유지 사유).\n\n약화 조건: `policy.jitter\
    \ = 0` 이면 이 FIND 무효. CHANNEL_RESTART_POLICY(0.1) 기준 성립.\n"
status: discovered
discovered_by: cron-reliability-auditor
discovered_at: 2026-04-18
cross_refs: []
domain_notes_ref: domain-notes/infra-retry.md
related_tests:
- src/infra/backoff.test.ts
- src/infra/retry.test.ts
---
# computeBackoff 의 jitter 가 단방향·비암호화라 동시 실패 인스턴스들이 같은 window 로 몰린다

## 문제

`computeBackoff` 의 jitter 수식은 `base * policy.jitter * Math.random()` 으로 항상 양수.
`delay ∈ [base, base*(1+jitter)]` 로 상방 편향되어 thundering-herd 완화 효과가 반감된다.
또한 같은 infra 폴더의 `retry.ts` 는 `generateSecureFraction()` 기반 대칭 jitter 를 쓰는데,
`backoff.ts` 는 `Math.random()` 기반 단방향 jitter 로 구현이 이원화되어 있다.

## 발현 메커니즘

1. `server-channels.ts:460` 에서 `computeBackoff(CHANNEL_RESTART_POLICY, attempt)` 호출.
2. `CHANNEL_RESTART_POLICY = { initialMs: 5_000, factor: 2, jitter: 0.1, maxMs: 300_000 }`.
3. attempt=1 → `base=5_000` → `delay ∈ [5_000, 5_500]` (500ms window).
4. N 개 채널이 동시에 restart 루프 진입 시, 대칭 jitter 였다면 `[4_500, 5_500]` (1_000ms window)
   에 분산되지만, 단방향이라 500ms window 에 밀집.
5. attempt 이 커질수록 window 폭은 절대값으로 늘어나지만 "절대로 base 보다 일찍 재시도되지
   않는다" 는 하방 편향 유지 → 앞쪽 경계(base) 에서 동시 스파이크.

## 근본 원인 분석

1. L10 수식 자체가 단방향. `Math.random() ∈ [0, 1)` 이므로 곱셈 결과도 [0, +).
2. retry.ts:65 는 `(generateSecureFraction() * 2 - 1) * jitter` 로 `[-jitter, +jitter]` 대칭.
   같은 infra 폴더 내 동일 의도의 두 구현이 어긋남.
3. backoff.ts 는 `secure-random.js` 를 import 하지 않음 (L1 이후 import 부재). 공유 헬퍼가 없어
   PRNG 선택이 파일 단위로 흩어짐.
4. 기존 테스트(backoff.test.ts:13-30)는 현행 수식을 잠그는 형태로 작성되어 있어 변경 압력을
   주지 않음 → 불일치가 계속 유지됨.

## 영향

- **impact_hypothesis: resource-exhaustion** — 외부 API 에 대한 재시도 burst.
- 정량(추정): 대칭 jitter 대비 첫 window 밀도 2배. N=10 채널이면 5_000ms 근방 500ms 폭에
  10개 재연결 집중.
- `Math.random()` 의 비암호화 특성은 multi-instance 환경에서 이론상 클러스터링을 악화시킬 수
  있으나 Node.js 의 기본 PRNG 은 process-local seed 를 쓰므로 실무 영향은 제한적 (→ P3).

## 반증 탐색

- **숨은 방어**: backoff.ts 에 대칭화/무작위 seed 보정 코드 없음 (L1-12 전체).
- **기존 테스트**: backoff.test.ts:13-30 은 `Math.random` 을 0/1 로 고정해 경계만 확인.
  대칭성/하방 편차 요구 테스트 없음. 현행 동작을 "스펙" 으로 잠금.
- **호출자 설정**: `CHANNEL_RESTART_POLICY.jitter = 0.1` (server-channels.ts:26) 이 실제 쓰이는
  값. 0 이 아니므로 FIND 성립.
- **주변 코드**: retry.ts:61-67 의 `applyJitter` 는 대칭 + CSPRNG. 동일 infra 폴더 내 두 구현이
  서로 다르다는 것은 주변 맥락상 부자연스러움 (공유 헬퍼 부재).
- **primary-path inversion**: "이 cluster effect 가 완화되려면 어떤 대칭 로직이 실행돼야
  하는가?" → 현재 파일에 해당 로직 없음. 주장 성립.

## Self-check

### 내가 확실한 근거
- L10 수식은 `Math.random() * base * policy.jitter` 로 무조건 양수.
- retry.ts 의 applyJitter 는 대칭이며 CSPRNG 사용 (L61-67).
- backoff.ts 는 secure-random.js 를 import 하지 않는다 (L1 import 문).
- 기존 테스트는 단방향 수식을 검증할 뿐 대칭성 요구 없음.

### 내가 한 가정
- "thundering-herd" 완화가 jitter 의 주된 의도라고 가정. 다른 의도(최소 delay 보장 등)가
  설계 의도였다면 이 FIND 는 약화됨.
- N 개 채널이 동시에 장애 회복하는 시나리오가 실제로 발생한다고 가정. 발생 빈도 측정은 없음.
- `CHANNEL_RESTART_POLICY.jitter = 0.1` 가 대표 설정이라고 가정. 다른 caller 정책은 `agents/
  context.ts:188` 하나뿐이고 해당 policy 값은 이번 allowed_paths 밖이라 미확인.

### 확인 안 한 것 중 영향 가능성
- `agents/context.ts:188` 의 BackoffPolicy 값 (allowed_paths 밖).
- `Math.random` 의 Node.js 구현이 프로세스 간 독립 seed 를 보장하는지 실험 미수행.
- 실제 production 에서 동시 restart 밀도 측정 없음 → severity 를 P3 로 절제.
