---
id: FIND-infra-process-memory-001
cell: infra-process-memory
title: installUnhandledRejectionHandler 호출이 idempotent 하지 않아 process 리스너 중복 등록
file: src/infra/unhandled-rejections.ts
line_range: 339-380
evidence: "```ts\nexport function installUnhandledRejectionHandler(): void {\n  const\
  \ exitWithTerminalRestore = (reason: string) => {\n    restoreTerminalState(reason,\
  \ { resumeStdinIfPaused: false });\n    process.exit(1);\n  };\n\n  process.on(\"\
  unhandledRejection\", (reason, _promise) => {\n    if (isUnhandledRejectionHandled(reason))\
  \ {\n      return;\n    }\n\n    // AbortError is typically an intentional cancellation\
  \ (e.g., during shutdown)\n    // Log it but don't crash - these are expected during\
  \ graceful shutdown\n    if (isAbortError(reason)) {\n      console.warn(\"[openclaw]\
  \ Suppressed AbortError:\", formatUncaughtError(reason));\n      return;\n    }\n\
  \n    if (isFatalError(reason)) {\n      console.error(\"[openclaw] FATAL unhandled\
  \ rejection:\", formatUncaughtError(reason));\n      exitWithTerminalRestore(\"\
  fatal unhandled rejection\");\n      return;\n    }\n\n    if (isConfigError(reason))\
  \ {\n      console.error(\"[openclaw] CONFIGURATION ERROR - requires fix:\", formatUncaughtError(reason));\n\
  \      exitWithTerminalRestore(\"configuration error\");\n      return;\n    }\n\
  \n    if (isTransientUnhandledRejectionError(reason)) {\n      console.warn(\n \
  \       \"[openclaw] Non-fatal unhandled rejection (continuing):\",\n        formatUncaughtError(reason),\n\
  \      );\n      return;\n    }\n\n    console.error(\"[openclaw] Unhandled promise\
  \ rejection:\", formatUncaughtError(reason));\n    exitWithTerminalRestore(\"unhandled\
  \ rejection\");\n  });\n}\n```\n"
symptom_type: memory-leak
problem: installUnhandledRejectionHandler 가 호출될 때마다 process.on('unhandledRejection',
  ...) 리스너가 중복 등록되며, 함수 자체에 idempotency 가드가 없다. 동일 프로세스에서 두 진입점이 호출되는 정상 경로에서 이미 2개
  등록되며, 재진입 가능한 라이브러리/테스트 경로에서는 Node 기본 MaxListeners(10) 경고가 발생할 수 있다.
mechanism: '1. `installUnhandledRejectionHandler()` 본체는 `process.on("unhandledRejection",
  ...)` 호출만 수행 (L345).

  2. 호출 전 `process.listenerCount("unhandledRejection")` 검사도 없고, 이전 등록 리스너에 대한 참조도
  없음 — 따라서 호출 횟수만큼 리스너가 누적된다.

  3. 실제 정상 CLI 진입: `src/index.ts:90` 에서 한 번 호출 → 이후 `runLegacyCliEntry(process.argv)`
  (`src/index.ts:98`) → `runCli()` (`src/cli/run-main.ts:150`) → L223 에서 다시 호출. **같은
  프로세스에서 리스너 2개 등록**.

  4. 리스너가 2개 이상이면 하나의 unhandled rejection 이 발생할 때 모든 리스너가 순차 실행. 첫 번째 리스너가 fatal 판단
  → `process.exit(1)` 호출. Node 는 exit 후에도 이벤트 loop 에 큐된 리스너를 즉시 종료하지만, `exit` 이전에
  다른 리스너가 이미 동일한 reason 으로 `console.error` 를 중복 출력할 수 있다.

  5. 반면 첫 번째 리스너가 transient / handled 판정으로 return 만 한 경우 두 번째 리스너도 실행되며 **중복 콘솔 출력**
  발생 (같은 rejection 에 대해 "Suppressed AbortError" × 2, "Non-fatal unhandled rejection"
  × 2 등).

  6. 라이브러리/테스트 환경 (vitest, child worker, module re-entry) 에서 진입이 반복되면 리스너 수는 선형으로
  누적되며, 11개에 도달하면 Node 가 `MaxListenersExceededWarning` 를 stderr 로 출력. 이는 계속 쌓이면 실질적인
  메모리(클로저 체인) 누적 뿐 아니라 모든 rejection 이벤트에서 N회 반복 분기 실행으로 이어진다.

  '
root_cause_chain:
- why: installUnhandledRejectionHandler 는 호출될 때마다 process.on 을 무조건 호출
  because: 함수 시작부에 '이미 설치됨' 가드가 없음
  evidence_ref: src/infra/unhandled-rejections.ts:339-345
- why: 두 개의 별개 진입점이 있는 정상 CLI 경로에서 함수가 실제로 두 번 불린다
  because: src/index.ts:90 와 src/cli/run-main.ts:223 에서 각각 독립 호출; 두 번째 진입이 첫 번째를 대체하지
    않고 누적
  evidence_ref: src/index.ts:90
- why: 내부적으로 '이전 리스너 참조' 를 저장하는 모듈 변수가 존재하지 않음
  because: unhandled-rejections.ts 전체에서 process.on 반환값이나 bound listener 를 캡처해 둔 let/const
    없음 (`handlers` Set 은 registerUnhandledRejectionHandler 용이지 자기 자신 리스너용이 아님)
  evidence_ref: src/infra/unhandled-rejections.ts:13
- why: 리스너 해제 public API (`uninstallUnhandledRejectionHandler` 등) 도 노출되지 않음
  because: export 된 함수는 `isAbortError`, `isTransient*`, `registerUnhandledRejectionHandler`,
    `isUnhandledRejectionHandled`, `installUnhandledRejectionHandler` 뿐; uninstall
    없음
  evidence_ref: src/infra/unhandled-rejections.ts:184-339
impact_hypothesis: memory-growth
impact_detail: 기본 CLI 한 번 실행 → 리스너 2개 (각 함수 클로저 + 캡처된 exitWithTerminalRestore). 테스트
  실행 시 installUnhandledRejectionHandler 가 반복 호출되며 vitest 는 한 worker 에서 여러 suite 를
  순차 수행 — 리스너 선형 누적. 11개 이후 MaxListenersExceededWarning. 실제 production 단일 기동에서는 +1
  중복이므로 메모리 양보다 '모든 rejection 이벤트에서 중복 분기 실행' 이 더 눈에 띄는 증상.
severity: P3
counter_evidence:
  path: src/infra/unhandled-rejections.ts
  line: '345'
  reason: 'R-3 Grep 확인:

    - `rg -n "process\\.removeListener|process\\.off|removeAllListeners.*unhandledRejection"
    src/infra/unhandled-rejections.ts` → match 없음. uninstall 경로 부재.

    - `rg -n "listenerCount.*unhandledRejection|unhandledRejection.*listenerCount"
    src/` → match 없음. 호출 전 중복 검사 부재.

    - `rg -n "installUnhandledRejectionHandler\\(\\)" src/` → 2 hits 프로덕션 (`src/index.ts:90`,
    `src/cli/run-main.ts:223`) + 1 테스트. 두 프로덕션 진입이 모두 실행되는지 확인: `src/index.ts:85`
    `if (isMain)` 블록에서 installUnhandledRejectionHandler() 호출 후 `runLegacyCliEntry(process.argv)`
    (L98) → `./cli/run-main.js` 동적 import → `runCli(argv)` → L223 에서 다시 installUnhandledRejectionHandler().
    두 호출 모두 fire.


    R-5 execution condition 분류 (이 FIND 는 "cleanup 경로" 가 **부재** 한 케이스):

    | 경로 | 조건 |

    |---|---|

    | (없음) | 프로세스 종료까지 리스너 제거 없음 — `shutdown` path 조차 없음 |


    반증 탐색 카테고리:

    1. **이미 cleanup 있는지**: 동일 파일 내 `process.removeListener` / `process.off` / `process.removeAllListeners`
    grep 없음. `__testing` export 도 없음.

    2. **외부 경계 장치**: installUnhandledRejectionHandler 본체가 반환하는 unregister 함수 없음. 호출자가
    참조를 저장해서 off 할 수단 없음.

    3. **호출 맥락**: 위 1번에서 프로덕션 2회 호출 확인.

    4. **기존 테스트**: unhandled-rejections.fatal-detection.test.ts:20 에서 installUnhandledRejectionHandler()
    호출하지만 각 test 간 `process.removeAllListeners("unhandledRejection")` 호출이 beforeEach
    에 명시되지 않는 한 vitest 단일 worker 안에서 누적. (별도 확인: grep 결과 removeAllListeners 부재.)

    5. **Primary-path inversion**: "이 leak 이 성립하려면 무엇이 실패해야 하는가?" — installUnhandledRejectionHandler
    호출 경로가 **완전히 하나로 수렴** 하거나 **내부 guard** 가 존재해야 한다. 두 조건 모두 거짓임을 확인했다.

    '
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
---
# installUnhandledRejectionHandler 호출이 idempotent 하지 않아 process 리스너 중복 등록

## 문제

`installUnhandledRejectionHandler()` 는 호출될 때마다 `process.on("unhandledRejection", handler)` 를 호출하지만, 이미 설치됐는지 확인하는 가드나 이전 리스너를 교체/해제하는 로직이 없다. 결과적으로 같은 함수가 프로세스 안에서 N번 불릴 때 리스너가 N개 등록되어 단일 rejection 이벤트가 N번 분기 처리된다.

## 발현 메커니즘

1. CLI 기동 시 `src/index.ts:85` 의 `if (isMain)` 블록:
   - L90: `installUnhandledRejectionHandler()` → 리스너 1 등록.
   - L98: `runLegacyCliEntry(process.argv)` 호출.
2. `runLegacyCliEntry` 내부 (`src/index.ts:47-54`) → `loadLegacyCliDeps()` → `import("./cli/run-main.js")` → `runCli(argv)`.
3. `runCli` (`src/cli/run-main.ts:150`) → L213-218 에서 `installUnhandledRejectionHandler` 를 dynamic import 후 L223 에서 호출 → 리스너 2 등록.
4. 이제 `process.on("unhandledRejection", ...)` 리스너는 **동일한 클로저 로직이지만 서로 다른 함수 객체** 로 2개.
5. Unhandled rejection 이벤트가 한 번 발생하면 Node 는 두 리스너를 순차 실행. 첫 리스너가 `process.exit(1)` 에 도달하면 두 번째도 start 하지만 exit 가 시퀀셜하게 진행될 수 있다 (Node 의 emit 내부는 리스너를 동기 루프로 호출 — process.exit 은 나머지 emit 루프를 끊는다).
6. 첫 리스너가 return (handled / AbortError / transient) 한 경우 두 번째 리스너도 실행되어 **동일한 콘솔 메시지가 2번 출력** 된다 (예: "Suppressed AbortError: …" × 2, "Non-fatal unhandled rejection (continuing): …" × 2).
7. 테스트 러너처럼 단일 프로세스에서 여러 번 install 이 호출되는 환경 (혹은 라이브러리 import path 가 반복되는 경우) 리스너는 선형 누적. Node 기본 MaxListeners = 10 초과 시 warning + 이후 무제한 증가에 따라 메모리 성장.

## 근본 원인 분석

1. 함수가 state 를 갖지 않는다: 첫 줄에 `let installed = false; if (installed) return; installed = true;` 같은 가드 없음.
2. 리스너 자체를 모듈 변수로 캡처해 두지 않는다: `process.on` 에 전달되는 익명 arrow 가 매 호출마다 새로 생성. 따라서 나중에 `process.off(...)` 로 제거할 수단도 없다.
3. public uninstall API 부재: `installUnhandledRejectionHandler` 의 대응 해제 함수가 export 되지 않아 호출자가 책임지고 해제할 수도 없다.
4. 두 진입점이 서로 모른 채 각각 독립적으로 handler 를 설치한다: `src/index.ts:90` 와 `src/cli/run-main.ts:223`. 리팩터 중 "양쪽 모두에 설치" 가 의도였다면 최소한 idempotent 가드가 필요하다.

## 영향

- **런타임 증상**: 단일 CLI 기동 시 `process.on("unhandledRejection", ...)` 리스너 2개. Unhandled rejection 발생 시 두 handler 가 같은 reason 으로 분기 — `isUnhandledRejectionHandled`, `isAbortError` 등의 분기를 두 번 평가, 콘솔 메시지 중복 출력, 최악의 경우 `restoreTerminalState` 도 중복 호출되어 terminal state machine 이 비정상 상태가 될 수 있음.
- **메모리 증상**: 클로저 2개 (exitWithTerminalRestore bound + handler arrow) — 단일 기동에선 작지만, 반복 진입 환경에서 선형 누적. MaxListenersExceededWarning 이후 무제한 증가.
- **전파 범위**: `handlers` Set 의 `registerUnhandledRejectionHandler` 경로는 이 문제와 별개로 정상. 영향은 "최상위 unhandledRejection 핸들러 그 자체의 중복".

## 반증 탐색

- **이미 cleanup 있는지**: removeListener / off / removeAllListeners grep 결과 0건. 없음.
- **외부 경계 장치**: install 함수가 unregister cleanup 을 반환하지 않음. 외부에서 정리할 수단 없음.
- **호출 맥락**: 프로덕션 CLI 경로에서 두 번 호출 확인 (`src/index.ts:90`, `src/cli/run-main.ts:223`).
- **기존 테스트**: `unhandled-rejections.fatal-detection.test.ts:20` 에서 install 호출, 하지만 같은 파일에서 removeAllListeners 로 정리하는 비포/애프터 훅이 없음 (grep 확인). 테스트 러너 단일 worker 에서 handler 누적 가능.
- **주석/문서**: "이 함수는 한 번만 호출해야 함" 같은 JSDoc 없음 (L339 시그니처 직전 주석 없음).
- **Primary-path inversion**: 이 주장이 성립하려면 (a) install 호출 경로가 한 곳으로 수렴, 또는 (b) 함수 내 idempotent guard 중 하나가 있어야 함. 두 조건 모두 현재 부재.

## Self-check

### 내가 확실한 근거

- `src/infra/unhandled-rejections.ts:345` — `process.on("unhandledRejection", ...)` 가 install 호출마다 실행되며, 주변에 guard / 캐시 / unregister 경로 없음.
- `src/index.ts:90` — 첫 install 호출 지점. 그 다음 L98 에서 `runLegacyCliEntry` 호출 → runCli 로 이어진다.
- `src/cli/run-main.ts:213-223` — runCli 내부에서 두 번째 install 호출 지점.
- `handlers` Set (L13) 은 registerUnhandledRejectionHandler 용이지 자기 자신 최상위 리스너 관리와 무관.

### 내가 한 가정

- `src/index.ts` 의 `isMain` 블록이 실제 실행되는 주된 CLI 기동 경로라고 가정. (`isMain` 은 entry resolution 을 체크하고 true 일 때 runLegacyCliEntry 를 호출한다 — shebang 진입 시 true.)
- Node.js 의 `process.on` 은 중복 콜백 objects 를 모두 저장한다는 표준 동작을 가정 (EventEmitter semantics — 확인됨).
- `vitest` 테스트 러너가 단일 worker 에서 suite 간 `process.removeAllListeners` 를 자동 호출하지 않는다는 가정 — 기본 동작이며 별도 setup 이 없는 한 그렇다.

### 확인 안 한 것 중 영향 가능성

- `enableConsoleCapture()` (run-main L211) 가 rejection 로그 출력을 re-route 하는지 미확인 — route 하더라도 "중복 호출" 자체는 여전히 일어남.
- `isMain` 체크가 npm wrapper / bun / node bin 에 따라 false 로 평가되는 edge 경로가 있는지 미확인. 만약 isMain=false 면 L90 install 은 skip 되고 L223 만 실행 → 리스너 1개 정상. 이 경로가 지배적이면 본 FIND 의 production 영향은 줄어든다.
- `process.on("uncaughtException", ...)` 는 본 FIND 의 범위 밖 (allowed_paths 경계 외) — 하지만 동일 패턴의 중복이 거기에도 존재함 (L92 + run-main:225). 별도 FIND 가능성 있음.
