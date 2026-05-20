---
id: FIND-infra-retry-error-boundary-002
cell: infra-retry-error-boundary
title: retryAsync 의 throw lastErr ?? new Error 가 falsy reject 값을 generic Error 로 치환
file: src/infra/retry.ts
line_range: 105-105
evidence: "```ts\n    throw lastErr ?? new Error(\"Retry failed\");\n```\n"
symptom_type: error-boundary-gap
problem: '`retryAsync` 의 두 종료 지점 (retry.ts:105 number 경로, retry.ts:179 options 경로) 은 `throw lastErr ?? new Error("Retry failed")` 로 최종 에러를 던진다. `??` 는 `lastErr` 가 `null` 또는 `undefined` 일 때만 fallback 하지만, JS 에서 `throw` 의 피연산자는 임의 값을 허용한다. `fn()` 이 `throw undefined` / `throw null` 로 reject 되면 `lastErr` 는 그 falsy 값이 되고, `??` 가 이를 fallback 으로 간주해 원본 throw 값을 일반 `Error("Retry failed")` 로 **silent 치환**한다. 호출자의 `catch` 가 받는 것은 작업이 실제로 던진 식별 가능한 값이 아니라 retry 가 합성한 generic Error 이며, 원본 reject 값(예: 특정 sentinel `undefined`, 에러 코드 0) 과의 동일성(`===`) 비교나 instanceof 분기가 모두 깨진다.'
mechanism: "1. `fn()` 이 `throw undefined` 또는 `Promise.reject(undefined)` / `reject(null)` 로\n   실패. (라이브러리/네트워크 스택이 빈 reject 를 내거나, 코드가 sentinel 로\n   `throw undefined` 를 쓰는 경우.)\n2. `catch (err)` 진입, `lastErr = err` → `lastErr === undefined` (또는 null).\n3. 모든 attempt 소진 후 종료 지점 도달:\n   - number 경로 L105: `throw lastErr ?? new Error(\"Retry failed\")`.\n   - options 경로 L179: 동일.\n4. `lastErr ?? X`: `lastErr` 가 `undefined`/`null` 이면 `??` 가 우변 `X` 선택.\n   → 원본 reject 값(undefined/null) 이 버려지고 `new Error(\"Retry failed\")` 가 throw.\n5. 호출자의 catch 는 작업 고유의 reject 값이 아닌 generic Error 를 받는다:\n   - `err === ORIGINAL_SENTINEL` 비교가 false.\n   - 빈 reject 를 \"특정 무에러 종료\" 로 구분하던 분기가 일반 에러로 흡수됨.\n   - 로그에 표시되는 message 가 실제 원인이 아닌 \"Retry failed\" 고정 문자열.\n6. 부가: 첫 attempt 부터 catch 한 번도 안 들어오고 loop 가 끝나는 경우는 없다\n   (attempts >= 1 보장, retry.ts:91/111). 따라서 fallback 이 의도대로 동작하는\n   유일한 정당 케이스는 `lastErr` 가 한 번도 대입되지 않은 경우 = 발생 불가.\n   즉 L105/L179 의 `?? new Error(...)` 는 사실상 \"falsy throw 값\" 에만 발동하며,\n   그\
  \ 발동이 곧 결함이다 (dead fallback that is only reachable as a bug).\n"
root_cause_chain:
- why: 왜 원본 throw 값이 generic Error 로 바뀌는가?
  because: L105/L179 의 `lastErr ?? new Error("Retry failed")` 에서 `??` 는 좌변이 `null`/`undefined` 이면 우변을 택한다. `fn()` 이 `throw undefined`/`reject(null)` 하면 `lastErr` 가 정확히 그 값이 되어, retry 가 추적한 "실제 마지막 실패값" 이 fallback 조건과 충돌한다.
  evidence_ref: src/infra/retry.ts:105
- why: 왜 fallback 이 필요하다고 본 것인가?
  because: '`lastErr` 는 `let lastErr: unknown;` 로 선언 (retry.ts:92,119) 후 catch 에서만 대입된다. loop 가 한 번도 catch 에 안 들어가고 종료하면 `lastErr` 가 미대입 `undefined` 일 수 있다는 우려로 `?? new Error(...)` 를 둔 것으로 보이나, 실제로는 attempts>=1 (L91,111) + loop 가 정상 종료하려면 마지막 attempt 의 catch 를 반드시 거치므로 `lastErr` 미대입 상태로 L105/L179 에 도달하는 경로는 없다.'
  evidence_ref: src/infra/retry.ts:92-105
- why: 왜 이 치환이 발견되지 않는가?
  because: retry.test.ts 의 실패 케이스 (L116-122 `propagates after exhausting retries`, L188-194 `clamps attempts`) 는 모두 `new Error("boom")` 같은 truthy Error 객체로만 reject 한다. `throw undefined`/`reject(null)` 로 reject 하는 테스트가 0건이라 falsy throw 값 경로가 한 번도 실행되지 않는다.
  evidence_ref: src/infra/retry.test.ts:116-194
impact_hypothesis: data-loss
impact_detail: "정성 (error-boundary - 에러 식별자 손실 축):\n- `fn()` 이 falsy 값으로 reject 하면 (`throw undefined`, `reject(null)`,\n  드물게 `throw 0` / `throw \"\"` - 단 `0`/`\"\"` 는 `??` 가 fallback 하지 *않으므로*\n  영향은 undefined/null 에 한정) 호출자는 작업 고유의 reject 값을 받지 못하고\n  `Error(\"Retry failed\")` 를 받는다.\n- 호출자가 빈 reject(undefined/null) 를 의미 있는 신호로 쓰던 경우 (예: \"취소됨\"\n  sentinel, abort 후 비어있는 reject) 그 신호가 일반 실패 에러로 흡수되어\n  분기 로직이 잘못된 경로를 탄다.\n- 로그/관측에서 message 가 항상 \"Retry failed\" 로 고정되어 실제 원인 추적 불가.\n정량/현실성: in-repo 의 retryAsync caller (retry-policy runner → channel send,\nmedia/fetch, compaction) 가 `fn()` 안에서 falsy 값으로 reject 하는 경로는\n현재 확인되지 않음. Telegram/HTTP 클라이언트가 빈 reject 를 낼 수 있으나\nallowed_paths 밖이라 미확인. 재현 빈도가 낮고 영향이 \"식별자 손실\" 위생 수준\n→ P3.\n"
severity: P3
counter_evidence:
  path: src/infra/retry.ts
  line: 105-179
  reason: "R-3 방어 Grep + 정당성 검토:\n\n1) `rg -n \"lastErr\" src/infra/retry.ts`\n   → L92 `let lastErr: unknown;` (number), L119 동일 (options), L98/125 `lastErr = err`,\n     L105/179 `throw lastErr ?? new Error(\"Retry failed\")`.\n   `lastErr` 는 catch 에서만 대입. 그 외 정규화/falsy 보정 코드 없음.\n\n2) `rg -n \"isNullish|!= null|=== undefined|=== null\" src/infra/retry.ts`\n   → 매치 0건. `lastErr` 가 \"실제 던져진 falsy 값\" 인지 \"미대입 undefined\" 인지\n     구분하는 가드 없음. 둘을 구분하는 별도 플래그(`caught: boolean`)도 없다.\n\n3) 기존 테스트 (retry.test.ts):\n   - L116-122 `propagates after exhausting retries`: `new Error(\"boom\")` 로 reject.\n   - L188-194 `clamps attempts to at least 1`: `new Error(\"boom\")` 로 reject.\n   모두 truthy Error 객체. `mockRejectedValue(undefined)` / `(null)` 케이스 없음\n   → falsy throw 값 경로 미커버. 현행 치환 동작이 테스트로 잠겨있지 않음 (스펙 공백).\n\n4) R-5 실행 조건 분류표:\n   | 경로 | 파일:라인 | 실행 조건 | 비고 |\n   |---|---|---|---|\n   | `throw lastErr` (truthy err) | retry.ts:105/179 | unconditional (정상 실패) | 정상 |\n   | `?? new Error(\"Retry\
    \ failed\")` (lastErr 미대입) | retry.ts:105/179 | **도달 불가** | attempts>=1 + 종료 전 마지막 catch 필수 |\n   | `?? new Error(\"Retry failed\")` (lastErr=undefined/null, 실제 throw 값) | retry.ts:105/179 | conditional-edge | falsy reject 시 발동 = 결함 |\n   fallback 의 \"정당한\" 실행 조건은 도달 불가하고, 실제 도달 조건은 결함 케이스뿐.\n\n5) 정당성 inversion: \"이 `??` fallback 이 버그 없이 발동하는 입력이 존재하는가?\"\n   → 존재하지 않는다. loop 가 종료 지점에 도달하려면 `break` (catch 안) 또는\n     loop 조건 종료가 필요한데, options 경로 (L121-177) 는 break 가 모두 catch\n     안 (L127), number 경로 (L93-104) 도 break 가 catch 안 (L99). loop 조건\n     자연 종료 시에도 마지막 iteration 의 catch 를 거친다 (성공이면 L95/123 의\n     `return`). 따라서 L105/L179 도달 = `lastErr` 가 반드시 한 번 이상 대입됨.\n     fallback 은 falsy throw 값에서만 의미를 가지며 그 의미가 곧 silent 치환.\n\n약화 조건: `fn()` 이 절대 falsy 값으로 reject 하지 않으면 재현 없음. 현재\nin-repo caller 에서 falsy reject 경로가 확인되지 않아 severity 를 P3 로 절제.\n그러나 `retryAsync` 는 범용 공개 유틸이고 임의 `fn` 을 받으므로 인터페이스\n계약상 falsy reject 는 유효 입력 → 위생 수준 FIND 로 기록.\n"
status: rejected
discovered_by: error-boundary-auditor
discovered_at: 2026-05-20
cross_refs: []
domain_notes_ref: domain-notes/infra-retry.md
related_tests:
- src/infra/retry.test.ts
rejected_reasons:
- 'cross-review CAND-044 scope_down (2026-05-20): synthetic-only — in-repo caller(channel send/media-fetch/compaction)의 fn 이 falsy 값(undefined/null)으로 reject 하는 경로 0건. reproduction-realist synthetic_risk=high, abandon-as-synthetic 권고. metrics/cross-review-CAND-044-20260520-174033.jsonl'
---
# retryAsync 가 falsy 한 throw 값을 generic "Retry failed" 로 silent 치환한다

## 문제

`retryAsync` 의 두 종료 지점 (retry.ts:105 number 경로, retry.ts:179 options 경로) 은
`throw lastErr ?? new Error("Retry failed")` 로 최종 에러를 던진다. `??` 는 `lastErr` 가
`null`/`undefined` 일 때 fallback 한다. 그런데 `fn()` 이 `throw undefined` /
`Promise.reject(null)` 로 실패하면 `lastErr` 가 그 falsy 값이 되어, `??` 가 이를
fallback 대상으로 간주하고 원본 reject 값을 generic `Error("Retry failed")` 로
silent 치환한다. 호출자는 작업 고유의 reject 값이 아닌 합성 Error 를 받게 되어
`===` 동일성 비교나 빈-reject sentinel 분기가 깨진다.

## 발현 메커니즘

1. `fn()` 이 `throw undefined` / `reject(null)` 로 실패.
2. `catch (err)` → `lastErr = err`, `lastErr` 는 undefined/null.
3. attempt 소진 후 L105 (또는 L179) 도달.
4. `lastErr ?? new Error(...)`: `??` 가 좌변 falsy → 우변 `new Error("Retry failed")` throw.
5. 원본 reject 값이 버려지고 호출자는 generic Error 수신.

## 근본 원인 분석

1. `??` 는 `null`/`undefined` 를 fallback 트리거로 본다. `fn` 의 실제 throw 값이
   undefined/null 이면 "실패값" 과 "fallback 조건" 이 충돌.
2. `lastErr` 는 `let lastErr: unknown` 후 catch 에서만 대입 — "미대입" 과 "falsy 값
   대입" 을 구분하는 플래그가 없다.
3. retry.test.ts 의 실패 케이스가 전부 truthy `new Error("boom")` 로만 reject →
   falsy throw 값 경로가 테스트에 없어 치환 동작이 드러나지 않음.

## 영향

- **impact_hypothesis: data-loss** (에러 식별자 손실 축).
- 호출자가 빈 reject 를 sentinel 로 쓰면 그 신호가 일반 실패로 흡수 → 잘못된 분기.
- 로그 message 가 항상 "Retry failed" 로 고정 → 실제 원인 추적 불가.
- in-repo caller 에서 falsy reject 경로 미확인 → 재현 빈도 낮음, P3.

## 반증 탐색

- **숨은 방어**: `lastErr` 정규화/falsy 보정/미대입 구분 플래그 없음 (Grep 0건).
- **기존 테스트**: 실패 케이스가 전부 truthy Error. `mockRejectedValue(undefined/null)`
  케이스 0건 → falsy 경로 미커버, 치환 동작 미잠금.
- **정당성 inversion**: `?? new Error(...)` 가 버그 없이 발동하는 입력이 없다. L105/L179
  도달 = `lastErr` 가 반드시 대입됨 (attempts>=1 + break 가 모두 catch 안). fallback 은
  falsy throw 값에서만 발동하며 그것이 곧 silent 치환.

## Self-check

### 내가 확실한 근거
- L105/L179 모두 `throw lastErr ?? new Error("Retry failed")` (직접 확인).
- `??` 는 null/undefined 에서 우변 선택 (JS 의미론).
- `lastErr` 는 catch 에서만 대입, 미대입/falsy-값 구분 플래그 없음 (Grep).
- retry.test.ts 의 reject 케이스가 전부 truthy Error 객체.

### 내가 한 가정
- `fn()` 이 falsy 값으로 reject 할 수 있다고 가정. `retryAsync` 가 임의 `fn` 을 받는
  범용 유틸이므로 인터페이스상 유효하나, in-repo caller 에서 그 경로는 미확인.
- `0`/`""` 같은 falsy-but-not-nullish 값은 `??` 가 fallback 하지 *않으므로* 영향
  범위를 undefined/null 로 한정함.

### 확인 안 한 것 중 영향 가능성
- retry-policy/media-fetch/compaction 의 `fn` 내부가 빈 reject 를 낼 수 있는지
  (해당 모듈은 allowed_paths 밖).
- Telegram/HTTP 클라이언트 라이브러리가 undefined reject 를 내는 빈도 (외부 의존).
- 위 미확인 때문에 severity 를 P3 로 절제. 인터페이스 계약 위반 수준의 위생 FIND.
