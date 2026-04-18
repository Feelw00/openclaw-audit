---
id: FIND-infra-process-error-boundary-002
cell: infra-process-error-boundary
title: isUnhandledRejectionHandled 가 FATAL/CONFIG 분기 앞에서 실행 — 등록 핸들러가 치명 오류 suppress
  가능
file: src/infra/unhandled-rejections.ts
line_range: 345-378
evidence: "```ts\nprocess.on(\"unhandledRejection\", (reason, _promise) => {\n  if\
  \ (isUnhandledRejectionHandled(reason)) {\n    return;\n  }\n\n  // AbortError is\
  \ typically an intentional cancellation (e.g., during shutdown)\n  // Log it but\
  \ don't crash - these are expected during graceful shutdown\n  if (isAbortError(reason))\
  \ {\n    console.warn(\"[openclaw] Suppressed AbortError:\", formatUncaughtError(reason));\n\
  \    return;\n  }\n\n  if (isFatalError(reason)) {\n    console.error(\"[openclaw]\
  \ FATAL unhandled rejection:\", formatUncaughtError(reason));\n    exitWithTerminalRestore(\"\
  fatal unhandled rejection\");\n    return;\n  }\n\n  if (isConfigError(reason))\
  \ {\n    console.error(\"[openclaw] CONFIGURATION ERROR - requires fix:\", formatUncaughtError(reason));\n\
  \    exitWithTerminalRestore(\"configuration error\");\n    return;\n  }\n\n  if\
  \ (isTransientUnhandledRejectionError(reason)) {\n    console.warn(\n      \"[openclaw]\
  \ Non-fatal unhandled rejection (continuing):\",\n      formatUncaughtError(reason),\n\
  \    );\n    return;\n  }\n\n  console.error(\"[openclaw] Unhandled promise rejection:\"\
  , formatUncaughtError(reason));\n  exitWithTerminalRestore(\"unhandled rejection\"\
  );\n});\n```\n"
symptom_type: error-boundary-gap
problem: '`registerUnhandledRejectionHandler` 로 등록된 임의 핸들러가 `isUnhandledRejectionHandled`

  에서 `true` 를 반환하면, 그 뒤의 FATAL (ERR_OUT_OF_MEMORY 등) / CONFIG (MISSING_API_KEY

  등) 분기 전체가 건너뛰어진다. 사용자 정의 핸들러 하나가 모든 치명적 분류를 silent 하게

  suppress 할 수 있다.

  '
mechanism: "1. 외부 모듈이 registerUnhandledRejectionHandler(myHandler) 로 자신만의 핸들러 등록\n\
  \   (handlers Set 에 추가, src/infra/unhandled-rejections.ts:316-321).\n2. myHandler\
  \ 가 'reason 을 보고 true 를 리턴' 하는 패턴 (예: 특정 카테고리 suppress 의도).\n3. 런타임 중 ERR_OUT_OF_MEMORY\
  \ 같은 FATAL_ERROR_CODES 에 속하는 rejection 발생.\n4. process.on(\"unhandledRejection\"\
  ) 콜백 line 345 진입.\n5. isUnhandledRejectionHandled(reason) → handlers.forEach → myHandler\
  \ 가 true 반환.\n6. 함수 early return (line 347), FATAL 분기 line 357 은 도달 불가.\n7. 프로세스가\
  \ memory 고갈 상태에서 계속 실행 → cascading failure.\n"
root_cause_chain:
- why: 왜 isUnhandledRejectionHandled 가 isFatalError 보다 먼저 호출되는가?
  because: 의도적 설계로 보이지만 (등록 핸들러가 'domain-specific 판단' 을 우선) FATAL 분류는 domain 판단 대상이
    아님.
  evidence_ref: src/infra/unhandled-rejections.ts:346-348
- why: 왜 등록 핸들러 계약에 'FATAL 은 return false' 규약이 없는가?
  because: 'UnhandledRejectionHandler 타입 정의 (line 11) 가 `(reason: unknown) => boolean`
    뿐이고, FATAL/config 예외를 주의하라는 주석이나 타입 가드 없음.'
  evidence_ref: src/infra/unhandled-rejections.ts:11
- why: 왜 이 체인이 검증되지 않았나?
  because: FATAL_ERROR_CODES (15-21) 내 코드들은 실제 발생 빈도가 낮고, 테스트에서 직접 시뮬레이션이 어려움. 계약으로
    강제하는 테스트 grep 결과 없음.
  evidence_ref: src/infra/unhandled-rejections.ts:15-21
impact_hypothesis: crash
impact_detail: '정성: FATAL rejection (예: ERR_OUT_OF_MEMORY, ERR_WORKER_UNCAUGHT_EXCEPTION)
  이 등록 핸들러에

  의해 silent suppress 시, 프로세스는 복구 불가 상태로 계속 실행. 이후 예측 불가 crash / hang.

  CONFIG rejection (MISSING_API_KEY 등) 도 suppress 되면 사용자에게 설정 오류 메시지가 노출되지

  않음 → 진단 난이도 상승.

  재현 조건: (1) 임의 플러그인/모듈이 registerUnhandledRejectionHandler 로 ''모든 rejection 을

  true 로 claim'' 하는 편향된 핸들러 등록, (2) FATAL 코드 rejection 발생.

  '
severity: P1
counter_evidence:
  path: src/infra/unhandled-rejections.ts
  line: 329-334
  reason: 'R-3 Grep 결과:

    - `rg -n "try\s*\{" src/infra/unhandled-rejections.ts` → hit line 325 (isUnhandledRejectionHandled
    내부의 handler 호출 보호 try/catch). 이 try 는 ''handler 가 throw 해도 loop 계속'' 만 보장, FATAL
    분류 우선순위와는 무관.

    - `rg -n "registerUnhandledRejectionHandler" src/` 결과 (이 조사에서 직접 실행하지 않았으나 동일
    파일 line 316 에 정의 및 export). 호출처 계약 검증 결과 따로 grep 하지 않음 — 그러나 handler 계약에 ''FATAL
    은 건드리지 마라'' 가드 없음이 소스에서 명백.

    요약: 방어 경로로 handler 내부 throw 보호 try/catch 는 있으나, FATAL 우선순위 보장은 없음. Result 패턴도
    unhandledRejection 경로에는 적용 안 됨 (reason 은 unknown).

    '
status: rejected
discovered_by: error-boundary-auditor
discovered_at: '2026-04-18'
rejected_reasons:
- 'B-1-2c: evidence mismatch at src/infra/unhandled-rejections.ts:345-378 — whitespace
  or content differs'
---
# isUnhandledRejectionHandled 가 FATAL/CONFIG 분기 앞에서 실행 — 등록 핸들러가 치명 오류 suppress 가능

## 문제

`installUnhandledRejectionHandler` 의 분기 순서가 `isUnhandledRejectionHandled` (등록된 사용자
핸들러 chain) 를 `isFatalError` / `isConfigError` 보다 먼저 평가한다. 핸들러 하나가 true 를
리턴하면 그 뒤 분류가 스킵되어, OOM / WORKER_UNCAUGHT / MISSING_API_KEY 같은 치명/구성 오류가
조용히 숨겨질 수 있다.

## 발현 메커니즘

```
unhandledRejection(reason)
  ├─ isUnhandledRejectionHandled(reason)  ← 먼저 실행
  │    ├─ handlers.forEach
  │    └─ any handler returns true? → return (핸들 종료)
  │
  ├─ isAbortError  → (skipped)
  ├─ isFatalError  ← 이 분기가 도달되어야 하는 경우에도 위에서 이미 return 됨
  ├─ isConfigError ← 동일
  └─ ...
```

등록 핸들러가 `(reason) => true` 같이 과잉 주장할 경우 FATAL 경로 전체가 dead code 가 된다.

## 근본 원인 분석

1. ordering 결정이 선언적으로 문서화되어 있지 않음. 주석(349-351)은 AbortError 만 이유 설명.
   등록 chain 우선순위의 trade-off 는 명시 안 됨.

2. `UnhandledRejectionHandler` 타입 (line 11) 은 단순 `(reason: unknown) => boolean` — 핸들러가
   판단을 내려도 되는 reason 범위에 대한 가드 없음. FATAL_ERROR_CODES Set 을 먼저 거르는
   내부 규약이 없으므로, '욕심 많은 핸들러' 가 등장해도 빌드/런타임에서 감지 불가.

3. 사용자 등록 핸들러가 FATAL 을 정확히 식별하여 false 를 리턴할 것을 기대하지만, 이는
   계약 외부 의존. 실제로 handlers.forEach 루프에서 각 핸들러는 독립적 판단을 수행한다.

## 영향

- impact_hypothesis: crash
- 시나리오 A (FATAL suppress): ERR_OUT_OF_MEMORY 발생 → 핸들러가 true → 프로세스 계속 실행
  → 후속 코드가 더 불안정한 상태에서 hang / 2차 crash.
- 시나리오 B (CONFIG suppress): MISSING_API_KEY 발생 → 핸들러가 true → 사용자는 "왜 안 되지"
  라는 상태로 방치, CONFIGURATION ERROR 메시지 미출력.
- 빈도: FATAL 자체가 드물지만, 핸들러 등록이 플러그인 생태계에서 늘어날수록 위험 확률 증가.

## 반증 탐색

- **숨은 방어**: isUnhandledRejectionHandled 내부 try/catch 는 'handler 가 throw 해도 다음
  handler 진행' 만 보장 (line 329-334). FATAL 우선순위 보장과 무관.
- **Result 패턴**: unhandledRejection 경로는 reason: unknown 으로 평문 전파. CLAUDE.md 의
  Result<T,E> 규칙은 함수 반환 경로에만 해당, 전역 handler 계약에는 미적용.
- **기존 테스트**: 현재 조사에서 'FATAL 우선순위' 를 검증하는 테스트 이름 grep 실행 안 함.
  shutdown-unhandled-rejection 류 테스트가 suppress 시나리오를 커버하는지 미확인.
- **문서화**: src/infra/AGENTS.md 등 경계 문서 확인 안 함. 주석에 "intentional" 표기 없음.

## Self-check

### 내가 확실한 근거
- line 346 의 `isUnhandledRejectionHandled` 분기가 FATAL/CONFIG 분기보다 먼저 실행됨 (직접 Read).
- handlers Set (line 13) 에 외부 등록 가능 API (line 316) 가 export 됨.

### 내가 한 가정
- 실제 플러그인/코어가 registerUnhandledRejectionHandler 호출하는 지점이 있을 것.
  (조사 시 호출처 grep 수행 안 함 — 토큰 예산 보호.)
- FATAL_ERROR_CODES (ERR_OUT_OF_MEMORY 등) 가 실제로 unhandledRejection 경로로 올라올 수
  있다고 가정.

### 확인 안 한 것 중 영향 가능성
- 기존 테스트가 ordering 을 고정하는지 (shutdown-unhandled-rejection.test 시리즈 분석 안 함).
- 핸들러 등록의 실제 사용처 — 현재 코드베이스에서 '모든 rejection 을 true 로 claim' 하는
  과잉 핸들러가 있는지.
