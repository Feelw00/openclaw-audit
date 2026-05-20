---
id: FIND-infra-retry-error-boundary-001
cell: infra-retry-error-boundary
title: retryAsync 의 catch 블록 내 콜백 throw 가 무방비로 누출되어 원본 에러를 가린다
file: src/infra/retry.ts
line_range: 122-130
evidence: "```ts\n    try {\n      return await fn();\n    } catch (err) {\n      lastErr = err;\n      if (attempt >= maxAttempts || !shouldRetry(err, attempt)) {\n        break;\n      }\n\n      const retryAfterMs = options.retryAfterMs?.(err);\n```\n"
symptom_type: error-boundary-gap
problem: '`retryAsync` 의 options 경로 (src/infra/retry.ts:121-176) 는 호출자가 주입한 세 개의 콜백 `shouldRetry` (L126), `retryAfterMs` (L130), `onRetry` (L166) 를 `catch (err)` 블록 안에서 호출하지만 어느 것도 `try`/`.catch` 로 감싸지 않는다. `try` 블록 (L122-123) 은 오직 `await fn()` 만 보호한다. 세 콜백 중 하나라도 throw 하면 그 throw 는 `catch` 가 이미 진입한 상태이므로 다시 잡히지 않고 retry loop 와 함수 밖으로 직접 전파된다. 이때 호출자가 받는 에러는 retry 가 추적하던 원본 작업 에러 `lastErr` 가 아니라 콜백이 던진 에러이며, `fn()` 의 실제 실패 원인 (`lastErr`) 은 throw 되지 못하고 swallow 된다.'
mechanism: "1. 호출자가 `retryAsync(fn, { shouldRetry, retryAfterMs, onRetry })` 형태로 콜백을 주입.\n   실제 production 주입처: `src/infra/retry-policy.ts:71,109` 가 `getChannelApiRetryAfterMs`\n   와 `resolveChannelApiShouldRetry` 의 결과를, `src/media/fetch.ts:523` 가 caller\n   제공 `shouldRetry` 를, `src/agents/compaction.ts:357` 가 `shouldRetry` 를 넘긴다.\n2. attempt N 에서 `fn()` 이 reject → `catch (err)` 진입, `lastErr = err` (L125).\n3. L126 에서 `shouldRetry(err, attempt)` 호출. 콜백이 throw 하면 (예: caller predicate\n   가 `err` 의 특정 필드를 비방어적으로 접근 - `err.response.status` 인데 `err.response`\n   가 undefined, 또는 `formatErrorMessage` 가 순환참조 객체에서 throw):\n   - 이 throw 는 catch 블록 안에서 발생 → 같은 catch 가 다시 잡지 않음.\n   - retry loop 즉시 종료, `retryAsync` 가 콜백 에러를 reject.\n   - L179 의 `throw lastErr ?? ...` 에 도달하지 못함 → 원본 `fn()` 실패 (`lastErr`)\n     는 영영 전파되지 않는다.\n4. `retryAfterMs(err)` (L130) 도 동일. retry-policy 가 주입하는 `getChannelApiRetryAfterMs`\n   자체는 typeof 가드가 촘촘하나, caller 가 직접 `retryAfterMs` 를 주입할 수 있는\n   공개 옵션 (RetryOptions.retryAfterMs,\
  \ L23) 이므로 무방비 콜백 슬롯은 일반 결함.\n5. `onRetry(info)` (L166) 도 동일. retry-policy.ts:74-80,111-116 의 `onRetry` 는\n   `log.warn(...)` 만 하므로 현재는 안전하나, `RetryOptions.onRetry` (L24) 는 공개\n   옵션이고 `src/agents/compaction.ts` 등 다른 caller 가 onRetry 에서 상태 갱신/이벤트\n   emit 을 하면 throw 가능. onRetry throw 시에도 `lastErr` swallow.\n6. number 오버로드 경로 (L90-106) 는 콜백이 없어 이 결함 미해당.\n"
root_cause_chain:
- why: 왜 콜백 throw 가 retry loop 밖으로 누출되는가?
  because: L122-123 의 `try` 는 `return await fn()` 만 감싼다. `shouldRetry`/`retryAfterMs`/`onRetry` 호출 (L126,130,166) 은 모두 `catch (err)` 블록 안에 있어 같은 try 의 보호를 받지 못한다. catch 블록 내부에서 발생한 throw 는 그 catch 가 재포착하지 않는 것이 JS 의미론.
  evidence_ref: src/infra/retry.ts:122-130
- why: 왜 원본 fn() 에러가 swallow 되는가?
  because: 원본 작업 실패는 `lastErr` 에 저장되고 (L125) 정상 종료 시 L179 의 `throw lastErr` 로 전파된다. 콜백이 catch 블록 안에서 throw 하면 제어가 L179 에 도달하기 전에 함수를 벗어나므로, `lastErr` 는 throw 되지 못하고 콜백 에러가 그 자리를 대체한다.
  evidence_ref: src/infra/retry.ts:166-179
- why: 왜 이 무방비 상태가 유지되는가?
  because: retry.test.ts 의 콜백 관련 테스트 (L136-143 shouldRetry, L145-173 onRetry, L196-215 retryAfterMs) 는 모두 콜백이 정상 반환하는 경우만 검증한다. 콜백이 throw 하는 시나리오 (rejects/throw 단언) 가 한 건도 없다. retry-policy.test.ts 도 동일. 기존 테스트가 happy-path 콜백만 lock 하고 있어 변경 압력이 없다.
  evidence_ref: src/infra/retry.test.ts:136-215
impact_hypothesis: data-loss
impact_detail: "정성 (error-boundary 관점):\n- 진단 정보 손실: `fn()` 의 실제 실패 원인 (`lastErr` - 예: 채널 API 의 429/timeout\n  원본 에러) 이 호출자/로그에 도달하지 못하고, retry 의 부수 콜백 에러 (예: predicate\n  의 TypeError \"Cannot read properties of undefined\") 가 대신 전파된다. 장애 원인\n  오진단으로 이어진다.\n- 누출 throw 위치가 호출자의 try 범위 밖이면 (fire-and-forget retry, floating\n  promise) unhandledRejection 으로 격상될 수 있다.\n- retry-policy 가 만든 channel-API runner (`createChannelApiRetryRunner`,\n  retry-policy.ts:104) 는 메시지 전송 경로에서 쓰인다. caller 가 strictShouldRetry\n  경로로 자체 predicate 를 주입했을 때 그 predicate 가 비방어적이면, 전송 실패\n  원본 에러 대신 predicate 에러가 올라가 재시도 자체가 조기 종료된다 (남은 attempt\n  포기) → 본래 retry 로 회복 가능했던 transient 실패가 hard failure 로 보고됨.\n정량: 콜백 3종 모두 무방비. 영향 크기는 caller 콜백의 견고함에 의존하므로 P2.\n현재 in-repo caller (retry-policy, media/fetch, compaction) 의 콜백은 비교적\n방어적이라 즉시 재현되지는 않음 → P1 이 아닌 P2.\n"
severity: P2
counter_evidence:
  path: src/infra/retry.ts
  line: 121-176
  reason: "R-3 방어 경로 Grep + R-5 실행조건 분류:\n\n1) `rg -n \"try\\s*\\{|catch\\s*\\(|finally\" src/infra/retry.ts`\n   → L94 try, L96 catch (number 경로), L122 try, L124 catch (options 경로).\n   options 경로의 try 는 L122-123 `return await fn()` 만 감싼다. 콜백 호출\n   (L126,130,166) 을 감싸는 별도 try/finally 없음.\n\n2) `rg -n -B2 -A2 \"shouldRetry\\(err|retryAfterMs\\?\\.\\(err\\)|onRetry\\?\\.\" src/infra/retry.ts`\n   → 세 콜백 호출 모두 `catch (err)` 블록 (L124~) 안. 어느 것도 자체 try 또는\n   `.catch()` 로 감싸이지 않음. 명시적 방어 0건.\n\n3) 기존 테스트 (retry.test.ts):\n   - L136-143 `stops when shouldRetry returns false`: shouldRetry 가 정상 `false`\n     반환만 검증.\n   - L145-173 `calls onRetry with retry metadata`: onRetry 가 정상 실행만 검증.\n   - L196-215 retryAfterMs 케이스: 콜백이 정상 number 반환만 검증.\n   - 콜백 throw 시나리오 (`.rejects` / `expect(...).toThrow` 이 콜백 발 throw 를\n     겨냥) 테스트 0건. retry-policy.test.ts 도 동일.\n   → 현행 무방비 동작을 happy-path 로만 lock. 콜백 throw 처리 스펙 부재.\n\n4) R-5 실행 조건 분류표:\n   | 경로 | 파일:라인 | 실행 조건 | 비고 |\n   |---|---|---|---|\n   | `try\
    \ { return await fn() }` | retry.ts:122-123 | unconditional | fn() 만 보호 |\n   | `shouldRetry(err, attempt)` 호출 | retry.ts:126 | conditional-edge (catch 진입 시) | 무방비 |\n   | `retryAfterMs?.(err)` 호출 | retry.ts:130 | conditional-edge (catch 진입 시) | 무방비 |\n   | `onRetry?.(info)` 호출 | retry.ts:166 | conditional-edge (catch 진입 시) | 무방비 |\n   | 콜백 throw 를 잡는 try/catch | N/A | **부재** | |\n   콜백 호출은 unconditional 방어로 보호되지 않음 → R-5 상 FIND 성립.\n\n5) Result 패턴 반증: CLAUDE.md 가 `Result<T,E>` 를 권장하나 `RetryOptions` 콜백\n   시그니처 (L22-24) 는 `boolean`/`number|undefined`/`void` 동기 반환이며 throw\n   가능. Result 로 콜백 에러를 표현하는 경계 없음.\n\n6) 상위 handler 반증: retry.ts 자체에 process-level handler 없음. 호출자가\n   try 로 감싸면 누출 throw 를 잡을 수는 있으나, 그 경우에도 콜백 에러가 원본\n   `lastErr` 를 대체하는 진단 손실은 그대로 남는다 → 상위 try 가 있어도 FIND\n   무효화 안 됨.\n\n약화 조건: 모든 caller 콜백이 항상 throw 하지 않음이 보장되면 현실 재현은\n없다. 그러나 `RetryOptions` 는 공개 옵션이고 콜백 무방비는 인터페이스 계약\n수준 결함 → P2 유지 (P1 아님: 현 in-repo caller 콜백이 비교적 방어적).\n"
status: rejected
discovered_by: error-boundary-auditor
discovered_at: 2026-05-20
cross_refs: []
domain_notes_ref: domain-notes/infra-retry.md
related_tests:
- src/infra/retry.test.ts
- src/infra/retry-policy.test.ts
rejected_reasons:
- '2026-05-20 CAND-044 abandon (false-positive-by-reproduction): retryAsync 콜백 무방비는 실재 코드 결함이나 in-repo caller 전수 방어적 + production trigger 0건. R-12 pre-sol proof production-faithful 하게는 unreproducible. cross-review reproduction-realist synthetic_risk=medium / CAL-003 parallel. 확인된 실제 문제 아님 → abandon.'
---
# retryAsync 의 콜백이 catch 블록 밖에서 무방비 호출되어 콜백 throw 가 원본 에러를 가린다

## 문제

`retryAsync` 의 options 경로는 `try` 블록으로 `await fn()` 만 보호한다 (retry.ts:122-123).
호출자 주입 콜백 `shouldRetry` (L126), `retryAfterMs` (L130), `onRetry` (L166) 는 모두
`catch (err)` 블록 *안에서* 호출되며 어느 것도 try/`.catch()` 로 감싸이지 않는다.
콜백이 throw 하면 catch 가 이미 진입한 상태라 재포착되지 않고 retry loop 밖으로 누출된다.
이때 호출자가 받는 것은 retry 가 추적하던 원본 작업 실패 `lastErr` 가 아니라 콜백 에러이며,
`fn()` 의 실제 실패 원인은 L179 의 `throw lastErr` 에 도달하지 못해 swallow 된다.

## 발현 메커니즘

1. 호출자가 콜백을 주입 (retry-policy.ts:71/109, media/fetch.ts:523, compaction.ts:357).
2. attempt N 에서 `fn()` reject → `catch (err)` 진입, `lastErr = err`.
3. L126 `shouldRetry(err, attempt)` 가 throw (caller predicate 의 비방어적 필드 접근,
   `formatErrorMessage` 의 순환참조 throw 등) → catch 가 재포착 안 함 → 콜백 에러가
   `retryAsync` 밖으로 reject, `lastErr` 는 영영 throw 안 됨.
4. `retryAfterMs(err)` (L130), `onRetry(info)` (L166) 도 동일 메커니즘.
5. number 오버로드 (L90-106) 는 콜백 없음 → 미해당.

## 근본 원인 분석

1. L122-123 의 try 가 `return await fn()` 만 감싼다. 콜백 호출은 catch 블록 안 → 같은
   try 보호 밖. catch 내부 throw 는 그 catch 가 재포착하지 않음 (JS 의미론).
2. 원본 실패는 `lastErr` 에 저장되어 L179 에서 throw 될 예정이나, 콜백 throw 가
   제어를 L179 이전에 함수 밖으로 빼내 `lastErr` 를 대체한다.
3. retry.test.ts/retry-policy.test.ts 의 콜백 테스트는 happy-path (정상 반환) 만 검증.
   콜백 throw 처리 스펙이 테스트에 없어 무방비 상태가 유지된다.

## 영향

- **impact_hypothesis: data-loss** (진단 정보 손실 축).
- `fn()` 의 실제 실패 원인이 호출자/로그에 도달하지 못하고 콜백 부수 에러가 대체 →
  장애 오진단.
- channel-API retry runner 경로에서 caller predicate 가 throw 하면 남은 retry attempt
  포기 → transient 실패가 hard failure 로 보고.
- 누출 throw 가 호출자 try 범위 밖이면 unhandledRejection 격상 가능.

## 반증 탐색

- **숨은 방어**: 콜백 3종 호출 어디에도 try/`.catch()` 없음 (L126,130,166 주변 Grep).
- **기존 테스트**: shouldRetry/onRetry/retryAfterMs 테스트 모두 정상 반환만 검증, throw
  시나리오 0건 → 현행 무방비를 스펙으로 lock.
- **상위 handler**: 호출자가 try 로 감싸도 콜백 에러가 `lastErr` 를 대체하는 진단 손실은
  남으므로 FIND 무효화 안 됨.
- **Result 패턴**: 콜백 시그니처가 동기 boolean/number/void 반환이며 throw 가능. 에러
  경계가 Result 로 표현되지 않음.

## Self-check

### 내가 확실한 근거
- L122-123 의 try 는 `return await fn()` 만 감싼다 (retry.ts:122-123 직접 확인).
- shouldRetry(L126)/retryAfterMs(L130)/onRetry(L166) 는 모두 `catch (err)` 블록 안.
- 콜백 호출을 감싸는 try/finally/`.catch()` 가 retry.ts 전체에 없음 (Grep 결과).
- 콜백 throw 시나리오 테스트가 retry.test.ts / retry-policy.test.ts 에 0건.

### 내가 한 가정
- caller 가 throw 하는 콜백을 주입할 수 있다고 가정. `RetryOptions` 가 공개 옵션이므로
  타당하나, 현재 in-repo caller (retry-policy/media-fetch/compaction) 의 콜백은 비교적
  방어적이라 즉시 재현은 안 됨 → severity P2 로 절제 (P1 아님).
- `formatErrorMessage` 가 순환참조 등에서 throw 할 수 있다고 가정. 해당 함수 본체
  (src/infra/errors.ts) 는 allowed_paths 밖이라 미확인 — 가정 표시.

### 확인 안 한 것 중 영향 가능성
- `src/infra/errors.ts` 의 `formatErrorMessage` 가 실제로 throw 가능한지 (allowed_paths 밖).
- media/fetch.ts:523 의 caller-provided `shouldRetry` 가 production 에서 얼마나 자주
  비방어적으로 주입되는지 (allowed_paths 밖).
- compaction.ts 의 onRetry 미주입 — 현 시점엔 onRetry caller 가 retry-policy 의
  log.warn 뿐이라 onRetry 축 재현은 가장 약함.
