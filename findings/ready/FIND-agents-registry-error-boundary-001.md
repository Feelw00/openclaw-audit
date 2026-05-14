---
id: FIND-agents-registry-error-boundary-001
cell: agents-registry-error-boundary
title: lifecycle listener IIFE has no catch; cleanup rejection escapes to exit
file: src/agents/subagent-registry.ts
line_range: 897-968
evidence: "```ts\n  listenerStop = subagentRegistryDeps.onAgentEvent((evt) => {\n\
  \    void (async () => {\n      if (!evt || evt.stream !== \"lifecycle\") {\n  \
  \      return;\n      }\n      const phase = evt.data?.phase;\n      const entry\
  \ = subagentRuns.get(evt.runId);\n      if (!entry) {\n        if (phase === \"\
  end\" && typeof evt.sessionKey === \"string\") {\n          await refreshFrozenResultFromSession(evt.sessionKey);\n\
  \        }\n        return;\n      }\n      if (phase === \"start\") {\n       \
  \ clearPendingLifecycleError(evt.runId);\n        clearPendingLifecycleTimeout(evt.runId);\n\
  \        const startedAt = typeof evt.data?.startedAt === \"number\" ? evt.data.startedAt\
  \ : undefined;\n        if (startedAt) {\n          entry.startedAt = startedAt;\n\
  \          if (typeof entry.sessionStartedAt !== \"number\") {\n            entry.sessionStartedAt\
  \ = startedAt;\n          }\n          persistSubagentRuns();\n        }\n     \
  \   return;\n      }\n      if (phase !== \"end\" && phase !== \"error\") {\n  \
  \      return;\n      }\n      const endedAt = typeof evt.data?.endedAt === \"number\"\
  \ ? evt.data.endedAt : Date.now();\n      const error = typeof evt.data?.error ===\
  \ \"string\" ? evt.data.error : undefined;\n      if (phase === \"error\") {\n \
  \       schedulePendingLifecycleError({\n          runId: evt.runId,\n         \
  \ endedAt,\n          error,\n        });\n        return;\n      }\n      if (evt.data?.aborted)\
  \ {\n        schedulePendingLifecycleTimeout({\n          runId: evt.runId,\n  \
  \        endedAt,\n        });\n        return;\n      }\n      if (evt.data?.yielded\
  \ === true) {\n        if (\n          markSubagentRunPausedAfterYield({\n     \
  \       entry,\n            endedAt,\n            startedAt:\n              typeof\
  \ evt.data?.startedAt === \"number\" ? evt.data.startedAt : entry.startedAt,\n \
  \         })\n        ) {\n          persistSubagentRuns();\n        }\n       \
  \ return;\n      }\n      clearPendingLifecycleError(evt.runId);\n      clearPendingLifecycleTimeout(evt.runId);\n\
  \      await completeSubagentRun({\n        runId: evt.runId,\n        endedAt,\n\
  \        outcome: { status: \"ok\" },\n        reason: SUBAGENT_ENDED_REASON_COMPLETE,\n\
  \        sendFarewell: true,\n        accountId: entry.requesterOrigin?.accountId,\n\
  \        triggerCleanup: true,\n      });\n    })();\n  });\n```\n"
symptom_type: error-boundary-gap
problem: '`ensureListener` 의 lifecycle handler (registry.ts:897-968) 는 매 event 마다

  `void (async () => { ... })()` 패턴으로 async IIFE 를 invoke 한다. 이 IIFE 에는

  `.catch(...)` 가 붙어 있지 않다. 안쪽 `await completeSubagentRun(...)`

  (line 958-966) 가 reject 하면 (e.g. `emitSubagentEndedHookForRun` 의 hook

  payload 가 plugin runtime 로딩 실패, `loadCleanupBrowserSessionsForLifecycleEnd`

  의 dynamic import 실패, `retireRunModeBundleMcpRuntime` 의 비-onError 경로

  throw 등), promise 가 unhandled rejection 으로 Node.js 에 전파된다.

  src/infra/unhandled-rejections.ts:511-544 의 global handler 는 `isFatalError`,

  `isConfigError`, `isTransientUnhandledRejectionError` 분류만 ''warn and

  continue'' 처리하고 나머지는 `exitWithTerminalRestore(...) → process.exit(1)`.

  즉 하나의 cleanup 부수 effect 실패가 **프로세스 전체를 종료** 시킨다.

  PR #68669 의 axis (completeSubagentRun 내부 announce cleanup throw 시 후속

  cleanup 미실행) 와는 다른 layer 이슈 — 본 FIND 는 listener wrapper 가 어떠한

  catch chain 없이 promise 를 fire-and-forget 한다는 sync-edge 결함.

  '
mechanism: "1. subagent run 의 lifecycle event 가 `infra/agent-events.ts:209-235\n \
  \  emitAgentEvent` 로 발사. `notifyListeners(state.listeners, enriched)` 호출\n   (shared/listeners.ts:1-13)\
  \ 시 `onError?` callback 미전달 → 동기 throw 는\n   무성히 swallow.\n2. registry.ts:897 의\
  \ listener 가 sync 콜백 안에서 `void (async () => {...})()`\n   로 async 작업을 fire-and-forget.\n\
  3. lifecycle event phase === \"end\" + !aborted + !yielded 분기 진입 → 라인\n   958-966\
  \ `await completeSubagentRun({ triggerCleanup: true })`.\n4. completeSubagentRun\
  \ (lifecycle.ts:765-886) 내부에서 throw 가능 지점:\n   - L859 `params.emitSubagentEndedHookForRun(...)`\
  \ — try/catch 없음.\n     emitSubagentEndedHookOnce 가 plugin runtime load 실패 또는 hook\
  \ 콜백\n     에러 시 propagate.\n   - L873 `loadCleanupBrowserSessionsForLifecycleEnd()`\
  \ — dynamic import\n     (lifecycle.ts:48-50 lazy loader). import 실패 시 throw.\n\
  \   - L879 `retireRunModeBundleMcpRuntime(...)` — `onError` callback 은\n     inner\
  \ retire 함수의 known-error path 만 잡고, retire 자체의 다른\n     throw (e.g. sessionKey resolution\
  \ error) 는 propagate.\n5. await 가 reject → async IIFE 의 promise 가 rejected. listener\
  \ 콜백 자체는\n   이미 sync 리턴 (void). 외곽 `.catch` 미부착.\n6. Node.js 가 unhandledRejection\
  \ 으로 분류 → infra/unhandled-rejections.ts:511\n   의 global handler 진입.\n7. error 가\
  \ `isTransientNetworkError | isTransientSqliteError |\n   isTransientFileWatchError`\
  \ (line 420-424) 중 어느 것도 아니면 L543\n   `console.error(\"[openclaw] Unhandled promise\
  \ rejection:\", ...)` 후 L544\n   `exitWithTerminalRestore(\"unhandled rejection\"\
  , reason,\n   \"unhandled_rejection\")` → `process.exit(1)`.\n8. 결과: subagent cleanup\
  \ 의 transient 결함 (plugin runtime, dynamic import,\n   MCP retire) 이 메인 process 전체를\
  \ 종료 → 모든 다른 subagent / 세션 /\n   pending IO 가 비정상 중단.\n"
root_cause_chain:
- why: 왜 cleanup 부수 effect 실패가 process 를 죽이는가
  because: listener IIFE 가 catch 없이 fire-and-forget 되어 unhandled rejection 으로 전파
  evidence_ref: src/agents/subagent-registry.ts:898
- why: 왜 catch 가 부착되지 않는가
  because: '`subagentRegistryDeps.onAgentEvent(...)` API 는 sync callback 만 받는다

    (shared/listeners.ts:15-22 registerListener 시그니처). 안에서 async 작업을

    해야 하므로 `void (async () => {...})()` 관례를 썼지만 `.catch(...)` 가

    누락됨.

    '
  evidence_ref: src/shared/listeners.ts:1-22
- why: 왜 global handler 가 transient 처리를 못 하는가
  because: '`isTransientUnhandledRejectionError` (infra/unhandled-rejections.ts:420)

    는 network/sqlite/file-watch 만 분류. plugin runtime load, dynamic import,

    MCP retire 의 일반적 throw 는 fatal 로 취급되어 process.exit(1).

    '
  evidence_ref: src/infra/unhandled-rejections.ts:420-424
- why: 왜 listenerStop 호출자나 emitAgentEvent 가 sync throw 도 못 잡는가
  because: '`notifyListeners` (shared/listeners.ts:1-13) 는 try/catch 로 sync throw
    는

    swallow 하지만 `onError?` 콜백을 받지 않으면 silent. emitAgentEvent

    (infra/agent-events.ts:234) 는 onError 미전달.

    '
  evidence_ref: src/infra/agent-events.ts:234
impact_hypothesis: crash
impact_detail: "정성: subagent cleanup 의 부수 effect (plugin runtime 로드, dynamic import,\n\
  MCP retire) 가 일시적으로 실패하면 main process 가 종료된다. 영향 규모는\ncleanup 실패율에 비례 — plugin runtime\
  \ load 실패는 disk pressure / lazy chunk\nhash mismatch 등으로 production 에서 관측 가능한 transient\
  \ 결함. 한 build\n내에 dynamic import target 모듈이 deleted/rebuilt 되거나 plugin manifest\
  \ 가\nstale 이면 재현 가능. 정량 부재: telemetry 없음.\n\n재현 시나리오:\n1. 임베디드 subagent run 종료 (lifecycle\
  \ phase end + !aborted + !yielded).\n2. completeSubagentRun 의 `emitSubagentEndedHookForRun`\
  \ 가 임의 plugin\n   hook 실행 중 throw (e.g. plugin 의 onSubagentEnded 가 reject).\n3.\
  \ listener IIFE 의 await 가 reject → unhandled rejection.\n4. infra/unhandled-rejections.ts:543-544\
  \ 가 process.exit(1).\n5. 같은 process 안의 다른 subagent / 세션이 종료. terminal state 는\n\
  \   restoreTerminalState 로 복원 시도되지만 pending IO 는 즉시 단절.\n"
severity: P2
counter_evidence:
  path: src/infra/unhandled-rejections.ts
  line: 511-544
  reason: "R-3 Grep 결과:\n1. `rg -n \"try\\s*\\{|catch\\s*\\(|\\.catch\\(\" src/agents/subagent-registry*.ts\n\
    \   src/agents/live-cache-test-support.ts` — registry.ts:898 IIFE 에는\n   try/catch\
    \ 또는 .catch 부착이 없음.\n2. `rg -n \"process\\.on\\(['\\\"](uncaughtException|unhandledRejection)\"\
    \n   src/` — infra/unhandled-rejections.ts:511 에 global handler 존재.\n   이는 R-5\
    \ 의 unconditional 방어 후보이지만, 실행 효과가 'classify and\n   process.exit(1)' 이라 silent-swallow\
    \ 가 아닌 fatal-crash 처리. 즉\n   방어로서의 graceful-recovery 효과 없음.\n3. `rg -n \"\\\\\
    .catch\\\\(\" src/agents/subagent-registry*.ts` — registry.ts\n   내 .catch 사용처:\
    \ 없음 (해당 listener wrapper 외 모든 .catch 는\n   lifecycle.ts/run-manager.ts 의 다른 fire-and-forget\
    \ 경로). PR #68669\n   의 dedup wrapper 는 line 871-877 영역으로 본 FIND 의 listener wrapper\n\
    \   와 다른 위치.\n4. 4-caller 분석 (R-5): listenerStop 으로 등록되는 콜백은 registry.ts:897\n\
    \   의 단일 호출자만 존재 (`rg -n \"onAgentEvent\\(\" src/agents/`). primary\n   path inversion\
    \ 없음.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: 2026-05-14
cross_refs: []
related_tests:
- src/agents/subagent-registry.test.ts
- src/agents/subagent-registry.persistence.test.ts
---
# lifecycle listener IIFE has no catch; cleanup rejection escapes to exit

## 문제

`subagent-registry.ts:897-968` 에 등록되는 onAgentEvent 콜백은 sync 콜백 안에서
`void (async () => { ... })()` 패턴으로 async 작업을 fire-and-forget 한다. async
IIFE 에 `.catch(...)` 가 부착되어 있지 않다. 안쪽 `await completeSubagentRun(...)`
이 reject 하면 (plugin runtime load, dynamic import, MCP retire 의 transient
실패가 대표), Node.js 의 unhandledRejection event 가 발사된다.

`src/infra/unhandled-rejections.ts:511` 의 global handler 는 classify 후 대부분의
non-transient 에러를 `exitWithTerminalRestore(...) → process.exit(1)` 로 종료시킨다
(line 544). 즉 하나의 subagent cleanup 부수 effect 실패가 main process 전체를
종료시킨다.

본 FIND 는 PR #68669 (browser cleanup dedup wrapper, line 871-877 영역) 와 다른
axis 의 결함이다. PR #68669 는 *completeSubagentRun 내부 announce cleanup throw 시
후속 cleanup 미실행* 을 다루며, 본 FIND 는 *listener wrapper 자체에 어떠한 catch
chain 도 없어 cleanup throw 가 process exit 으로 직결* 되는 sync-edge layer
이슈다.

## 발현 메커니즘

1. subagent run 의 lifecycle event 가 `src/infra/agent-events.ts:209-235`
   `emitAgentEvent` 로 발사된다. `notifyListeners(state.listeners, enriched)`
   호출 (shared/listeners.ts:1-13) 시 `onError?` callback 이 전달되지 않으므로
   listener 의 동기 throw 는 무성히 swallow.
2. registry.ts:897 의 listener 콜백이 sync 호출 안에서
   `void (async () => {...})()` 로 async 작업 시작.
3. lifecycle event phase==="end" + !aborted + !yielded 분기 진입 →
   line 958-966 `await completeSubagentRun({ triggerCleanup: true })`.
4. completeSubagentRun (lifecycle.ts:765-886) 내부의 unguarded throw 지점:
   - L859 `params.emitSubagentEndedHookForRun(...)` — try/catch 없음. plugin
     hook 의 임의 throw 가 propagate.
   - L873 `loadCleanupBrowserSessionsForLifecycleEnd()` — lazy dynamic import.
     모듈 해시 mismatch / 디스크 압력 / FS 오류 시 throw.
   - L879 `retireRunModeBundleMcpRuntime(...)` — onError callback 은 inner
     의 known-error 만 잡으므로 sessionKey 해석/접근 단계 throw 는 propagate.
5. await reject → async IIFE 의 promise rejected.
6. listener 콜백 자체는 sync return (void). 외곽에 `.catch` 부착 없음 →
   Node.js 가 unhandledRejection 분류.
7. infra/unhandled-rejections.ts:511 global handler 진입:
   - isAbortError / isFatalError / isConfigError 분기 일치 안 함
   - isTransientUnhandledRejectionError (line 420-424) 는
     network/sqlite/file-watch 만 분류 → plugin/import/MCP retire 는 미해당
   - L543 `console.error("[openclaw] Unhandled promise rejection:", ...)`
   - L544 `exitWithTerminalRestore("unhandled rejection", reason,
     "unhandled_rejection")` → `process.exit(1)`.
8. main process 종료 → 같은 process 의 다른 subagent / 세션 / pending IO
   비정상 단절.

## 근본 원인 분석

1. **listener IIFE 의 catch 누락** (registry.ts:898): 비대칭이다. lifecycle.ts
   내 다른 fire-and-forget 경로들 (e.g. lifecycle.ts:706, 717, 753) 은 모두
   `.catch((err) => defaultRuntime.log(...))` 가 부착되어 있다. registry.ts:898
   은 가장 hot-path 인 listener 자체임에도 catch 가 없다.

2. **onAgentEvent API 의 sync-callback shape**: shared/listeners.ts:15-22
   `registerListener` 의 시그니처는 `(event: T) => void` 로 sync 콜백만 받는다.
   안에서 async 작업을 해야 하므로 `void (async()=>...)()` 관례를 썼지만,
   catch chain 부착 책임이 콜백 작성자에게 있는 invariant 가 enforce 되지
   않는다.

3. **global unhandled handler 의 분류 한계**: isTransientUnhandledRejectionError
   (infra/unhandled-rejections.ts:420-424) 는 network/sqlite/file-watch 만
   transient 로 분류. plugin runtime load 실패 / dynamic import 실패 /
   MCP retire 의 일반적 throw 는 미분류 → fatal 로 취급되어 process.exit(1).
   즉 "subagent cleanup 의 transient 결함" 이라는 정상적 운영 상황이 fatal-class
   에 잘못 분류된다.

4. **notifyListeners 의 silent sync-throw catch**: shared/listeners.ts:7-11 의
   try/catch 가 onError 콜백 없이 사용될 경우 sync throw 자체도 silent swallow.
   즉 listener body 의 sync 단계 (e.g. `subagentRuns.get(evt.runId)` 가 어떤
   이유로 throw) 도 진단 불가.

## 영향

`impact_hypothesis: crash` — production 에서 subagent cleanup 의 transient
결함이 main process 전체 종료로 직결.

재현 시나리오:
1. 임베디드 subagent run 의 정상 lifecycle 종료 (phase="end", !aborted,
   !yielded).
2. `emitSubagentEndedHookForRun` 가 plugin onSubagentEnded hook 실행 중 throw
   (e.g. plugin manifest reload 가 인-플라이트, hook 인자 검증 실패).
3. listener IIFE 의 await 가 reject → unhandled rejection.
4. infra/unhandled-rejections.ts:543-544 가 process.exit(1).
5. 같은 process 안의 다른 subagent / 세션 모두 종료. restoreTerminalState 호출
   되나 pending IO 는 즉시 단절. user 는 "OpenClaw crashed" 만 경험.

체감 빈도: telemetry 미 부재. 정량 추정 불가. 단, plugin hook / dynamic import /
MCP retire 는 모두 production 에서 빈번히 호출되는 경로이며 각 step 의 throw
확률은 0 이 아니다. 따라서 P2 가 적정.

## 반증 탐색

R-3 Grep 결과:
1. `rg -n "try\s*\{|catch\s*\(|\.catch\(" src/agents/subagent-registry*.ts
   src/agents/live-cache-test-support.ts`:
   - registry.ts:898 의 IIFE 에는 try/catch 또는 .catch 부착이 **없다**.
   - 다른 fire-and-forget 경로 (lifecycle.ts:446, 655, 688, 706, 717, 753,
     756) 는 모두 `.catch(...)` 부착되어 있어 비대칭.
2. `rg -n "process\.on\(['\"](uncaughtException|unhandledRejection)" src/`:
   infra/unhandled-rejections.ts:511 에 global handler 존재. **R-5 unconditional
   defense 후보지만** 실행 효과가 'classify and process.exit(1)' 이므로
   silent-swallow 가 아닌 fatal-crash 처리 — graceful recovery 효과 없음. 본
   FIND 의 주장 "단일 cleanup 실패로 process 종료" 는 이 handler 의 *존재
   자체* 가 메커니즘의 일부.
3. `rg -n "isTransientUnhandledRejectionError" src/infra/unhandled-rejections.ts`:
   line 420-424. 분류 범위가 network/sqlite/file-watch 로 좁아 plugin/import/MCP
   retire 의 throw 는 fatal 로 취급됨.
4. 4-caller 분석 (R-5): `rg -n "onAgentEvent\(" src/agents/`:
   - registry.ts:897 (단일 production 호출자, 본 FIND 의 대상)
   - 그 외 모두 test 파일. **primary-path inversion 없음**. listener wrapper 가
     production 의 유일한 lifecycle event 처리 경로다.
5. `rg -n "ensureListener" src/agents/`: registry.ts:892 정의, 701 / 983 호출.
   run-manager.ts:119/366/447 도 `params.ensureListener()` 로 동일 함수 호출 →
   본 listener wrapper 가 모든 production register/resume 흐름의 root 임이
   확인됨.
6. CAL-008 upstream 검사:
   - `gh pr list --repo openclaw/openclaw --state open --search "subagent OR
     registry"` 결과 15+ PR 확인. PR #68669, #75462, #76332, #54764 등.
   - PR #75462 (SebTardif) 는 *waitForSubagentCompletion* 의 silent catch 만
     수정. listener IIFE 와 무관.
   - PR #76332 (neilofneils404) 는 *완전히 다른* axis (browser cleanup blocking
     announce cleanup; preclaim lease). listener wrapper 와 직접 충돌 없음.
   - PR #68669 (본 audit 의 우리 PR) 는 lifecycle.ts 의 browser cleanup dedup
     wrapper. listener wrapper 와 다른 layer.
   - 본 FIND 의 axis (listener IIFE catch 누락) 는 이 OPEN PR 중 어느 것도
     다루지 않는다.

## Self-check

### 내가 확실한 근거

- registry.ts:897-968 의 listener 콜백 본문 전체를 직접 확인했다. `void (async
  () => {...})()` 후에 `.catch` 또는 try/catch wrapper 가 없음을 확인.
- shared/listeners.ts:1-13 `notifyListeners` 가 onError 미전달 시 silent swallow
  함을 코드로 확인.
- infra/agent-events.ts:234 `notifyListeners(state.listeners, enriched)` 호출
  에서 onError 인자 미전달 확인.
- infra/unhandled-rejections.ts:511-544 global handler 의 분류와 process.exit
  로직을 직접 읽음.
- lifecycle.ts:859 `params.emitSubagentEndedHookForRun(...)` 가 try/catch
  바깥에 있음을 확인 (line 858-865).

### 내가 한 가정

- `emitSubagentEndedHookForRun` 가 실제 production 에서 throw 할 빈도 추정 —
  plugin manifest stale, hook 인자 검증 실패, 임의 plugin 의 onSubagentEnded
  reject 등을 "가능한 경로" 로 보았으나 정확한 확률 미측정.
- `loadCleanupBrowserSessionsForLifecycleEnd()` 의 dynamic import 실패는
  build 후 정상 환경에서 흔하지 않음. 단 vite/esbuild hash mismatch 또는
  rolling update 중 발생 가능.
- isTransientUnhandledRejectionError 가 향후 분류 범위를 확장할 가능성은 별도
  PR 영역 — 본 FIND 는 현재 분류 기준 기반.

### 확인 안 한 것 중 영향 가능성

- 실제 production telemetry 부재 → "얼마나 자주 process exit 발생하는가" 미측정.
- `retireRunModeBundleMcpRuntime` 의 내부 sessionKey 해석 path 가 throw 하는
  실제 조건은 pi-bundle-mcp-tools.ts (out-of-scope) 미 deep-read.
- agent-events.ts:234 `notifyListeners` 가 미래에 onError 콜백을 받도록 변경되면
  방어 layer 가 변동될 가능성.
- listener body 안의 sync throw 경로 (`subagentRuns.get`, `clearPendingLifecycleError`)
  는 본 FIND 범위 외 — notifyListeners 가 silent swallow 하므로 별도 진단.
