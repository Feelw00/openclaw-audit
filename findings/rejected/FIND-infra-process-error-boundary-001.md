---
id: FIND-infra-process-error-boundary-001
cell: infra-process-error-boundary
title: process.exit 직전 cleanup 이 restoreTerminalState 1개 뿐 — DB/log flush 누락
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
symptom_type: error-boundary-gap
problem: 'unhandledRejection 핸들러가 치명적 오류로 프로세스를 종료하기 직전 cleanup 단계가

  `restoreTerminalState` 한 개로 한정되어 있음. DB 핸들(close), 로그 buffer flush,

  session-write-lock release 는 수행되지 않은 채 `process.exit(1)` 가 즉시 호출된다.

  '
mechanism: "1. 런타임 중 unhandledRejection 발생 (FATAL / CONFIG / 기타 non-transient).\n\
  2. exitWithTerminalRestore(reason) 호출.\n3. restoreTerminalState(reason, { resumeStdinIfPaused:\
  \ false }) 만 실행 — 터미널 raw mode 복원,\n   cursor 복구 정도만 수행.\n4. 즉시 process.exit(1)\
  \ — Node event loop 종료.\n5. process.on(\"exit\", ...) 동기 리스너만 실행 가능 (session-write-lock.ts:265\
  \ 에 등록됨).\n   async cleanup (DB close, async log flush) 은 실행 기회가 없음.\n6. 결과: 진행\
  \ 중 stdout/stderr 버퍼링된 출력 일부 유실, better-sqlite3 등의 열린 DB 핸들은\n   OS 에 의해 강제 회수되며,\
  \ WAL/journal 상태가 깨끗하게 마감되지 않을 수 있음.\n"
root_cause_chain:
- why: 왜 process.exit(1) 직전에 restoreTerminalState 만 호출되는가?
  because: exitWithTerminalRestore 의 내부 구현이 터미널 복구 외의 cleanup 을 알지 못함.
  evidence_ref: src/infra/unhandled-rejections.ts:340-343
- why: 왜 통합 cleanup registry (onBeforeExit / onProcessExit) 가 이 경로에 연결되지 않았나?
  because: 세션 락 cleanup 은 process.on('exit') 에 개별 등록되어 있고, installUnhandledRejectionHandler
    에서 명시적으로 호출하지 않음.
  evidence_ref: src/agents/session-write-lock.ts:257-266
- why: 왜 cleanup 을 모으는 공용 facade 가 없는가?
  because: 현 코드베이스는 'process.on(exit) 에 각자 등록' 패턴에 의존. 동기 리스너만 동작하는 제약이 있으며 async
    flush 를 위한 경로 미설계.
  evidence_ref: N/A — 설계 부재 (grep 결과 onBeforeExit/registerCleanup 공용 API 없음)
- why: 왜 이 설계가 유지되고 있나?
  because: unhandled-rejection 은 '복구 불가 상황' 으로 간주되어 cleanup 을 최소화하는 편향. 단, 치명적 rejection
    이 실제로는 logic bug 원인일 수 있어 상태 일관성 보존이 중요.
  evidence_ref: src/infra/unhandled-rejections.ts:357-361
impact_hypothesis: data-loss
impact_detail: '정성: better-sqlite3 / SQLite 계열 DB 가 열려있는 상태에서 process.exit(1) 시

  transient SQLite 에러(ERR_SQLITE_ERROR)의 경우 WAL 미체크포인트로 데이터 복구 지연.

  또한 async log writer (파일로 직접 쓰는 경우) 버퍼 일부 유실 가능.

  재현 조건: FATAL/CONFIG unhandledRejection 발생 시점 + DB 쓰기 in-flight.

  빈도는 낮으나, 발생 시 영구적 저장 손상 가능.

  '
severity: P1
counter_evidence:
  path: src/agents/session-write-lock.ts
  line: 257-266
  reason: 'R-3 Grep 결과:

    - `rg -n "try\s*\{" src/infra/unhandled-rejections.ts` → 1 hit (line 325, 핸들러
    내부 반복 try/catch 로 무관).

    - `rg -n "process\.on\([''\"](uncaughtException|unhandledRejection)[''\"]" src/`
    → 13 hits, 그 중 프로덕션 코드는 src/index.ts:92 (uncaughtException), src/cli/run-main.ts:225
    (uncaughtException), src/infra/unhandled-rejections.ts:345 (unhandledRejection).
    index.ts:92-96 의 uncaughtException 경로도 동일하게 restoreTerminalState → exit(1) 만 수행
    (defense-in-depth 의 ''다른 방어 경로'' 조건 미충족).

    - `rg -n "onBeforeExit|onProcessExit|registerCleanup|exitHandler" src/` → cleanup
    facade 부재. session-write-lock.ts 가 process.on("exit") 에 동기 핸들러 하나 등록 (line 265).
    이는 exit code 는 정리되지만 async flush 는 불가.

    요약: 방어 경로로 session-write-lock 의 exit 리스너가 존재하나 동기 제약이 있어 DB close / log flush
    보장 불가.

    '
status: rejected
discovered_by: error-boundary-auditor
discovered_at: '2026-04-18'
related_tests:
- src/mcp/channel-server.shutdown-unhandled-rejection.test.ts
rejected_reasons:
- 'B-1-4: duplicate with FIND-infra-process-error-boundary-002 — overlaps src/infra/unhandled-rejections.ts:339-380'
---
# process.exit 직전 cleanup 이 restoreTerminalState 1개 뿐 — DB/log flush 누락

## 문제

`installUnhandledRejectionHandler` 내부의 `exitWithTerminalRestore` 가 치명적 unhandled
rejection 처리 종단에서 **터미널 상태 복원만** 수행한 뒤 즉시 `process.exit(1)` 호출한다.
DB 핸들 close, 로그 buffer flush, session-write-lock release 경로가 이 exit 경로에
연결되어 있지 않다.

## 발현 메커니즘

```
unhandledRejection
  → isUnhandledRejectionHandled? no
  → isAbortError? no
  → isFatalError / isConfigError / (non-transient)
    → exitWithTerminalRestore(reason)
      1. restoreTerminalState(reason, { resumeStdinIfPaused: false })
      2. process.exit(1)  ← event loop 즉시 중단
         - process.on("exit") 동기 리스너만 실행 (session-write-lock)
         - async flush / DB close 은 기회 없음
```

## 근본 원인 분석

1. `exitWithTerminalRestore` 의 몸체 (340-343) 가 의도적으로 최소화되어 있음.
   터미널 복구는 화면 상태에 대한 동기 작업이라 적합하지만, 동일 함수가 `process.exit` 를
   함께 호출하기 때문에 cleanup 확장 훅이 삽입될 슬롯이 없음.

2. session-write-lock 의 exit 리스너 (session-write-lock.ts:257-266) 는 파일 시스템 수준의
   락 해제를 담당하며 `process.on("exit")` 동기 컨텍스트에서 실행. 이는 설계상 async 작업을
   보장하지 못함 (Node 의 exit 이벤트는 반환값을 기다리지 않음).

3. openclaw 전역 cleanup facade (onBeforeExit / registerCleanup 류) 가 grep 결과 존재하지
   않는다. 즉, 각 모듈이 자체적으로 `process.on("exit")` 에 등록하는 분산 패턴만 있음. DB
   close 등 async 작업을 보장하는 sequence 가 없음.

4. 치명적 rejection 경로는 "복구 불가" 로 취급되어 빠른 종료를 선호. 다만 CONFIG/FATAL 구분
   (357-367) 이 이미 '의도적 종료' 에 가깝다면 graceful cleanup 여지가 있다.

## 영향

- impact_hypothesis: data-loss (SQLite WAL/journal 미체크포인트).
- 재현 시나리오:
  1. 대용량 DB 쓰기 진행 중 (예: agent 세션 저장 또는 task-registry 쓰기).
  2. 동시에 다른 async 작업이 rejection 발생 (예: MISSING_API_KEY CONFIG_ERROR).
  3. exitWithTerminalRestore → process.exit(1).
  4. WAL 파일 checkpoint 미수행, 다음 기동 시 복구 지연/경고.
- 빈도: 치명적 rejection 자체가 드물지만, 장기 실행 gateway 에서 누적 위험.

## 반증 탐색

- **숨은 방어**: src/index.ts:92-96 uncaughtException 경로도 동일 패턴 → 추가 방어 없음.
- **기존 테스트**: `src/mcp/channel-server.shutdown-unhandled-rejection.test.ts` 가 shutdown
  시나리오 일부 커버하나 DB/log flush 보장 여부 확인 안 됨.
- **호출 빈도**: unhandledRejection 핸들러는 매 프로세스 수명당 1회. 도달 시 비가역.
- **주변 맥락**: session-write-lock 의 exit 리스너가 최소한의 락 해제 담당 (line 257-266).
  그러나 DB close 는 커버되지 않음.

## Self-check

### 내가 확실한 근거
- line 340-343 에서 restoreTerminalState → process.exit(1) 외에 다른 호출 없음 (직접 읽음).
- 전체 src/ 에서 `onBeforeExit|onProcessExit|registerCleanup` grep 결과 없음 (session-write-lock.exitHandler 만 존재).

### 내가 한 가정
- better-sqlite3 / 유사 DB 가 런타임에 열려있다고 가정 (cron.md / domain-notes 에서 간접
  확인, 직접 Read 안 함).
- async log writer 가 존재하여 buffer 가 있다고 가정. logging/subsystem.ts 는 읽지 않음.

### 확인 안 한 것 중 영향 가능성
- Node 의 process.exit 이 stdout/stderr drain 을 수행하는지 플랫폼별 동작 (TTY vs pipe) 차이.
- test 에서 이미 "다음 기동 시 WAL 복구" 시나리오를 커버하는지.
