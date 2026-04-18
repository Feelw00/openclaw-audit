---
id: FIND-infra-retry-concurrency-001
cell: infra-retry-concurrency
title: retryAsync 가 AbortSignal 을 받지 않아 shutdown 중에도 재시도 sleep 이 완주한다
file: src/infra/retry.ts
line_range: 105-137
evidence: "```ts\nfor (let attempt = 1; attempt <= maxAttempts; attempt += 1) {\n\
  \  try {\n    return await fn();\n  } catch (err) {\n    lastErr = err;\n    if\
  \ (attempt >= maxAttempts || !shouldRetry(err, attempt)) {\n      break;\n    }\n\
  \n    const retryAfterMs = options.retryAfterMs?.(err);\n    const hasRetryAfter\
  \ = typeof retryAfterMs === \"number\" && Number.isFinite(retryAfterMs);\n    const\
  \ baseDelay = hasRetryAfter\n      ? Math.max(retryAfterMs, minDelayMs)\n      :\
  \ minDelayMs * 2 ** (attempt - 1);\n    let delay = Math.min(baseDelay, maxDelayMs);\n\
  \    delay = applyJitter(delay, jitter);\n    delay = Math.min(Math.max(delay, minDelayMs),\
  \ maxDelayMs);\n\n    options.onRetry?.({\n      attempt,\n      maxAttempts,\n\
  \      delayMs: delay,\n      err,\n      label: options.label,\n    });\n    if\
  \ (delay > 0) {\n      await sleep(delay);\n    }\n  }\n}\n\nthrow lastErr ?? new\
  \ Error(\"Retry failed\");\n}\n```\n"
symptom_type: shutdown-gap
problem: '`retryAsync` (`src/infra/retry.ts:69-137`) 는 어떤 오버로드에서도 AbortSignal 을 받지
  않는다.

  루프 내부의 `await sleep(delay)` (L131) 는 plain `setTimeout` 래퍼(`src/utils.ts:76-78`)라

  취소할 수 없다. 호출자가 process shutdown / reload / 사용자 취소로 abort 를 signal 해도

  `retryAsync` 는 delay 를 끝까지 기다린 뒤 다음 `fn()` 을 한 번 더 시도한다.

  '
mechanism: "1. 호출자가 `retryAsync(fn, { attempts: 3, minDelayMs: 400, maxDelayMs: 30_000,\
  \ jitter: 0.1 })` 호출\n   (e.g. `retry-policy.ts:104` createChannelApiRetryRunner\
  \ → Telegram send).\n2. `fn()` 이 첫 시도에서 \"429/timeout/reset\" 로 throw.\n3. L115-121\
  \ 에서 `delay` 계산 후 L131 `await sleep(30_000)` 진입.\n4. 이 시점에 호스트 프로세스가 SIGTERM 을 받거나,\
  \ 채널이 manually stop 되거나,\n   사용자가 플러그인을 unload 한다 → `abort.signal.abort()` 가 호출됨.\n\
  5. `retryAsync` 는 abort 를 몰라서 30초 sleep 을 완주하고 `fn()` 을 **한 번 더 실행**.\n   시도된 send/HTTP\
  \ 가 shutdown 중임에도 네트워크를 건드리고, 응답은 무시될 수 있으며,\n   process exit 이 최악의 경우 `delay *\
  \ (attempts-1)` 만큼 지연된다.\n"
root_cause_chain:
- why: 왜 retry 루프가 abort 에 반응하지 않는가?
  because: retryAsync 시그니처(L69-73, L92)에 AbortSignal 파라미터 자체가 없다.
  evidence_ref: src/infra/retry.ts:69-73
- why: 왜 시그니처에 signal 이 없는가?
  because: 내부 sleep 이 plain `sleep(ms)` (utils.ts:76-78) 이므로 signal 을 전달할 곳이 없다. backoff.ts
    의 sleepWithAbort(14-59)는 이 경로에서 사용되지 않는다.
  evidence_ref: src/infra/retry.ts:131
- why: 왜 retry-policy 레이어도 signal 을 주입하지 않는가?
  because: createRateLimitRetryRunner/createChannelApiRetryRunner 가 `retryAsync(fn,
    {...})` 에 options 만 전달하고, 호출자로부터 받는 signal 을 그대로 내려보내지 않는다.
  evidence_ref: src/infra/retry-policy.ts:67,104
- why: 왜 sleepWithAbort 는 존재하는데 retry 경로는 plain sleep 을 쓰는가?
  because: '`sleepWithAbort` (backoff.ts:14-59)는 `src/cron/isolated-agent/delivery-dispatch.ts:382`,
    `src/gateway/server-channels.ts:470`, `src/agents/pi-embedded-runner/run.ts:547`
    등 직접 호출 경로에서만 쓰임. retry.ts 는 backoff.ts 를 import 하지 않음 (cross-check: `grep -n
    ''backoff'' src/infra/retry.ts` 매치 0건).'
  evidence_ref: src/infra/retry.ts:1-3
impact_hypothesis: hang
impact_detail: "정성 + 상한값:\n- 프로세스 종료(SIGTERM 처리/unload/reload)가 `delay * (maxAttempts-1)`\
  \ 만큼 지연.\n  `CHANNEL_API_RETRY_DEFAULTS` (retry-policy.ts:7-12: attempts=3, maxDelayMs=30_000)\
  \ 기준\n  최대 60초 shutdown lag.\n- `createRateLimitRetryRunner` 를 쓰는 호출자가 더 긴 `maxDelayMs`\
  \ 를 설정하면 선형 확대\n  (e.g. 60_000ms * 5 시도 = 4분).\n- shutdown lag 뿐 아니라, delay 만료 후\
  \ 시도되는 `fn()` 이 외부 API 를 **추가로 건드린다**\n  (Telegram sendMessage 등). Non-idempotent\
  \ 송신이 `strictShouldRetry=false` 인 경우 중복\n  메시지 위험 (retry-policy.ts:27-29 의 regex\
  \ fallback 과 결합).\n- 재현: Telegram 채널 `startChannel` → 429 응답 → retry 루프에서 30s sleep\
  \ 진입 →\n  `stopChannel` 호출 (abort.abort()) → 30초 대기 후 `sendMessage` 한 번 더 시도됨.\n"
severity: P2
counter_evidence:
  path: src/infra/retry.ts
  line: 69-137
  reason: "숨은 방어/기존 테스트/호출자 주입 3축 탐색 모두 \"정상 방어 없음\":\n\n1) `rg -n \"clearTimeout\\\
    \\(|AbortController|signal\\\\.abort\" src/infra/retry*.ts` → **매치 0건**.\n   retry.ts/retry-policy.ts\
    \ 어디에도 abort 관련 토큰이 없음.\n2) `rg -n \"AbortSignal\" src/infra/retry*.ts` → **매치\
    \ 0건**. 파라미터로 받지도 않고\n   내부에서 생성도 안 함.\n3) R-5 분류표:\n   | 경로 | 실행 조건 |\n   |---|---|\n\
    \   | `sleepWithAbort.onAbort → clearTimeout` (backoff.ts:27) | conditional-edge.\
    \ 단, retry.ts 가 이 함수를 사용하지 않음 |\n   | `retryAsync` 의 `sleep(delay)` 취소 | **부재**\
    \ |\n   | retry-policy runner → retryAsync 호출 시 signal 전달 | **부재** (retry-policy.ts:66-81,\
    \ 103-118) |\n4) 기존 테스트 (retry.test.ts 전체 258라인) 에서 AbortSignal / abort / cancel\
    \ 키워드 검색:\n   `rg -n \"AbortSignal|abort|cancel\" src/infra/retry.test.ts` → **매치\
    \ 0건**.\n   기존 테스트는 abort 시나리오를 전혀 다루지 않음.\n5) primary-path inversion: 이 shutdown-gap\
    \ 이 성립하려면 \"retryAsync 가 sleep 중에\n   abort 를 감지하는 경로\" 가 존재해야 한다. 해당 경로가 Grep\
    \ 결과 0건 → gap 성립.\n\n유일한 약화 조건: 호출자가 사전에 `fn` 자체에 signal 을 심고, `fn()` 이 abort\
    \ 감지\n시 즉시 throw 하도록 구현하면 재시도 루프는 `shouldRetry(err)` 에서 \"aborted\" 를\nfalse 처리하여\
    \ 탈출할 수 있다. 그러나 이는 caller-responsibility 이며 retry 유틸의\n방어가 아니다. 또한 이미 sleep 에\
    \ 진입한 뒤의 abort 는 caller 가 뭘 해도 잡을 수 없음.\n"
status: rejected
discovered_by: cron-reliability-auditor
discovered_at: 2026-04-18
cross_refs: []
domain_notes_ref: domain-notes/infra-retry.md
related_tests:
- src/infra/retry.test.ts
- src/infra/backoff.test.ts
rejected_reasons:
- 'B-1-4: duplicate with FIND-infra-retry-concurrency-003 — overlaps src/infra/retry.ts:105-137'
---
# retryAsync 가 AbortSignal 을 받지 않아 shutdown 중에도 재시도 sleep 이 완주한다

## 문제

`retryAsync` 는 내부 `await sleep(delay)` 를 취소할 수단이 없다. 호출자가 프로세스 종료,
플러그인 언로드, 사용자 취소 등으로 abort 를 전달해도 retry 루프는 delay 를 끝까지 기다리고
추가 `fn()` 시도를 수행한다. 취소-aware 한 `sleepWithAbort`(backoff.ts:14-59)가 같은 infra
폴더에 존재하지만 retry.ts 는 이를 사용하지 않는다.

## 발현 메커니즘

1. caller → `retryAsync(fn, { attempts, minDelayMs, maxDelayMs })`
2. `fn()` throw → L110 `shouldRetry` 통과 → L115-121 delay 계산 → L131 `await sleep(delay)`
3. 외부에서 abort 발생 (SIGTERM / stopChannel / plugin unload)
4. `sleep(delay)` 는 abort 를 모름. `setTimeout` 의 타이머가 만료될 때까지 루프가 정지.
5. 만료 후 다음 attempt 에서 `fn()` 한 번 더 실행 → 종료 직전의 프로세스가 외부 I/O 유발.
6. 최악의 경우 `(maxAttempts - 1) * maxDelayMs` 동안 프로세스 종료 대기.

## 근본 원인 분석

1. `retryAsync` 의 두 오버로드 모두 `signal?: AbortSignal` 파라미터가 없다 (L69-73, L92).
2. 내부는 `sleep(delay)` plain 버전 (utils.ts:76-78 `new Promise(r => setTimeout(r, ms))`)
   을 직접 사용한다 (L131). `clearTimeout` 핸들도 외부로 노출되지 않는다.
3. retry-policy 레이어 (`createRateLimitRetryRunner`, `createChannelApiRetryRunner`) 도
   signal 을 받지 않고 `retryAsync` 에 그대로 옵션만 넘긴다 (retry-policy.ts:67, 104).
4. 동일 infra 폴더의 `sleepWithAbort`(backoff.ts:14-59)는 signal 을 지원하지만 retry.ts 가
   import 하지 않는다 (retry.ts:1-3 의 import 문에 backoff 없음).

## 영향

- **impact_hypothesis: hang** — shutdown latency 증가.
- 재현: Telegram 채널이 429 로 retry 루프에 진입한 상태에서 채널 stop / 프로세스 SIGTERM
  → 최대 `(attempts-1) * maxDelayMs` ms 만큼 종료 지연 + abort 무시된 최종 API 시도.
- 데이터 측면: `strictShouldRetry=false` 경로에서는 "timeout|reset|closed|unavailable"
  regex 가 `shouldRetry` 를 true 로 만들 수 있고 (retry-policy.ts:14, 27-29), shutdown 중
  네트워크 단절이 이 regex 와 매칭되므로 retry 가 계속됨.

## 반증 탐색

- **숨은 방어**: `rg "AbortSignal|abort|signal" src/infra/retry*.ts` 매치 0건.
  retry-policy.ts 에도 signal 관련 코드 없음.
- **기존 테스트 커버리지**: `rg "abort|cancel|AbortSignal" src/infra/retry.test.ts` 매치 0건.
  총 258라인의 테스트가 이 시나리오를 전혀 다루지 않음.
- **caller 조립**: 주요 caller 중 compaction.ts:317, batch-http.ts:13 은 retryAsync 에
  signal 을 넘기려 해도 시그니처가 받지 않음.
- **설정/feature flag**: retry 비활성화 플래그 없음 (`attempts=1` 만 가능).
- **주변 코드 맥락**: 같은 폴더의 backoff.ts 에 취소-aware 한 `sleepWithAbort` 가 존재 →
  retry 레이어도 같은 취소 semantics 를 가졌어야 한다는 기대가 자연스러움. 불일치가 의도적
  이라는 주석 없음.
- **primary-path inversion**: "이 shutdown-gap 이 성립하려면 어떤 cancel 경로가 실패해야
  하는가?" → 실패할 정상 경로 자체가 없다 (cancel 경로 부재). 따라서 gap 은 unconditional.

## Self-check

### 내가 확실한 근거
- retry.ts 전체 파일에 AbortSignal 이 전혀 등장하지 않는다 (Grep 결과 0건).
- retry.ts 는 backoff.ts 를 import 하지 않는다 (L1-3 import 문 확인).
- retry-policy.ts 도 signal 을 받지 않는다 (Grep 결과 0건).
- retry.test.ts 는 abort 시나리오를 테스트하지 않는다 (Grep 결과 0건).

### 내가 한 가정
- caller(예: Telegram 채널 stop 경로) 가 실제로 shutdown 시 abort 를 signal 할 의도였다고
  가정. stopChannel 구현 자체는 확인하지 않음.
- `maxDelayMs=30_000` 이 실제 production 에서 쓰인다고 가정 (CHANNEL_API_RETRY_DEFAULTS).
- "한 번 더 시도되는 fn()" 이 실제로 네트워크 I/O 를 발생시킨다고 가정. fn 내부 구현을
  확인하지는 않음.

### 확인 안 한 것 중 영향 가능성
- `compaction.ts:317` 의 compaction retry 가 shutdown 시 얼마나 지연을 초래하는지 실측 없음.
  만약 compaction 은 원래 shutdown 이후 지속돼도 문제없다면 severity 하향 가능.
- `batch-http.ts:13` 의 외부 HTTP 클라이언트가 자체 timeout 을 가질 수 있으나, 그것은 `fn()`
  내부의 개별 호출을 자르는 것이지 retry 루프의 sleep 을 취소하지 못함.
- Node.js 의 `setTimeout` 은 event loop 를 blocking 하지 않으므로 "프로세스 exit" 자체는
  막지 않는다. 그러나 `process.on("beforeExit")` 기반 graceful shutdown 훅이 등록된 환경
  에서는 pending microtask + pending timer 로 인해 정상 cleanup 이 지연됨.
