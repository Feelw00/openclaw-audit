---
id: FIND-cron-error-boundary-001
cell: cron-error-boundary
title: logCronDeliveryErrorDeferred floating promise 가 catch 부재로 crash 로 증폭
file: src/cron/isolated-agent/delivery-dispatch.ts
line_range: 244-248
evidence: "```ts\nfunction logCronDeliveryErrorDeferred(message: string): void {\n\
  \  void loadDeliveryLoggerRuntime().then(({ logError }) => {\n    logError(message);\n\
  \  });\n}\n```\n"
symptom_type: error-boundary-gap
problem: '''logCronDeliveryErrorDeferred (delivery-dispatch.ts:244-248) 는 lazy import
  promise 에 `.then()` 만 붙이고 `.catch()` 를 붙이지 않는 floating promise 다. `void` 로 promise
  를 버리므로 rejection 을 받을 핸들러가 함수 안에 전혀 없다. `loadDeliveryLoggerRuntime()` (createLazyImportLoader
  기반, delivery-dispatch.ts:182-184/220-222) 가 reject 하거나 `.then` 콜백의 `logError(message)`
  가 throw 하면, 그 rejection 은 `.then` 체인을 그대로 빠져나가 process-level `unhandledRejection`
  핸들러까지 도달한다. 같은 파일의 sync 버전 logCronDeliveryError / logCronDeliveryWarn (239-242/234-237)
  은 `async` + `await` 라 호출처의 try/catch 에 잡히지만, deferred 변형만 경계가 없다.'''
mechanism: "'1) isolated cron job 이 delivery.bestEffort=true 로 실행된다 (resolveCronDeliveryBestEffort,\n\
  \   run.ts:1026 -> dispatchCronDelivery).\n2) dispatchCronDelivery 내부 direct delivery\
  \ 경로에서 `onError` 콜백이 구성됨 (delivery-dispatch.ts:910-917).\n   이 콜백은 bestEffort 일\
  \ 때만 정의되고, payload 별 전송 실패 시 sendDurableMessageBatch 가\n   동기적으로 호출한다.\n3) onError\
  \ 는 `logCronDeliveryErrorDeferred(...)` 를 호출 (913).\n4) logCronDeliveryErrorDeferred\
  \ 는 `void loadDeliveryLoggerRuntime().then(...)` 실행 — `.catch` 없음.\n5) 만약 `loadDeliveryLoggerRuntime()`\
  \ 의 dynamic import (`import(\"./delivery-logger.runtime.js\")`,\n   delivery-dispatch.ts:183)\
  \ 가 reject 하면, `.then` 콜백은 실행되지 않고 reject 가 체인 끝까지 전파된다.\n   createLazyPromiseLoader\
  \ (shared/lazy-promise.ts:16-26) 는 cacheRejections!==true 일 때 *자기 사본*\n   에만 `void\
  \ loaded.catch(...)` 를 달아 캐시를 리셋할 뿐, load() 가 반환한 promise (호출처가 then 으로 이어받은\n \
  \  바로 그 promise) 의 rejection 은 호출처 책임으로 남는다.\n6) 처리되지 않은 rejection 은 process 의 unhandledRejection\
  \ 핸들러 (src/infra/unhandled-rejections.ts:513)\n   로 간다. import 실패 reason 은 AbortError\
  \ 도 transient 도 아니므로 line 545-546 의 generic 분기에\n   떨어져 `exitWithTerminalRestore(\"\
  unhandled rejection\")` -> `process.exit(1)` 로 게이트웨이가 종료된다.\n7) 동일하게 `.then` 콜백의\
  \ `logError(message)` 가 동기 throw 해도 `.then` 이 반환한 promise 가 reject 되어\n   같은 경로를\
  \ 탄다.'\n"
root_cause_chain:
- why: 왜 logCronDeliveryErrorDeferred 에는 catch 가 없는가?
  because: upstream commit 139a3f49fe (2026-04-13, 'perf(cron) lazy-load delivery
    logger runtime') 이 동기 logger 호출을 lazy-import 로 바꾸면서 fire-and-forget deferred 변형을
    추가했다. sync 변형 logCronDeliveryError 는 await 기반이라 호출처 try/catch 에 잡히지만, deferred
    변형은 promise 를 void 로 버리며 자체 catch 를 두지 않았다.
  evidence_ref: 'git: 139a3f49fe perf(cron): lazy-load delivery logger runtime (2026-04-13)'
- why: 왜 lazy import 의 rejection 이 호출처로 전파되는가?
  because: createLazyPromiseLoader (src/shared/lazy-promise.ts:16-26) 의 내부 `void loaded.catch(...)`
    는 cacheRejections 미설정 시 캐시 무효화 전용이며, load() 반환 promise 자체의 rejection 은 소비하지 않는다.
    deliveryLoggerRuntimeLoader 는 createLazyImportLoader 를 옵션 없이 호출 (delivery-dispatch.ts:182-184)
    하므로 cacheRejections 미설정. 따라서 load() 의 reject 는 호출처가 잡아야 한다.
  evidence_ref: src/shared/lazy-promise.ts:16-26 (createLazyPromiseLoader)
- why: 왜 이 unhandled rejection 이 단순 로그가 아니라 프로세스 종료로 이어지는가?
  because: process-level unhandledRejection 핸들러 (src/infra/unhandled-rejections.ts:513-547)
    는 AbortError / transient / config 분류에 해당하지 않는 generic reason 에 대해 line 545-546
    에서 `exitWithTerminalRestore` -> `process.exit(1)` 를 호출한다. 모듈 로드 실패 Error 는 이 generic
    분기에 떨어진다.
  evidence_ref: src/infra/unhandled-rejections.ts:545-546 (exitWithTerminalRestore
    on generic unhandled rejection)
impact_hypothesis: crash
impact_detail: '''정성: bestEffort isolated cron job 의 payload 전송이 부분 실패하는 순간 onError
  -> logCronDeliveryErrorDeferred 가 실행되고, 그 시점에 delivery-logger.runtime.js 의 dynamic
  import 가 reject 하면 process-level handler 가 게이트웨이를 process.exit(1) 로 종료시킨다. 즉 error
  를 *로깅하려는* 코드가 오히려 크래시를 유발한다. 다만 production trigger 가능성은 매우 낮다: import 대상은 같은 빌드에
  번들된 로컬 모듈이라 정상 배포에서는 reject 하지 않는다. 실제 발현은 빌드 손상 / 디스크 손상 / 부분 배포 같은 비정상 환경을 전제로
  한다. 또한 logError 가 동기 throw 하는 경우도 경로상 성립하나 logError 는 pino 계열 logger 호출이라 사실상 throw
  하지 않는다. 따라서 코드상 error-boundary gap (category B floating promise) 은 명확히 성립하고 crash
  까지 체인이 이어지지만, 현재 코드 기준 production 재현은 확인되지 않음 -> P3.'''
severity: P3
counter_evidence:
  path: src/infra/unhandled-rejections.ts
  line: 513-547
  reason: '''R-3 방어 경로 Grep 결과:

    (1) `rg -n "process\.on\([''\"](uncaughtException|unhandledRejection)" src/` ->
    production 핸들러는 src/index.ts:93 (uncaughtException), src/infra/unhandled-rejections.ts:513
    (unhandledRejection), src/cli/run-main.ts:686 (uncaughtException) 3건. 즉 상위 process
    boundary 는 *존재한다* — 하지만 이 핸들러는 silent-log 가 아니라 generic reason 에 대해 process.exit(1)
    을 호출하는 crash 핸들러다 (unhandled-rejections.ts:545-546). 따라서 상위 boundary 가 결함을 "흡수"
    하지 않고 오히려 crash 로 증폭한다 -> FIND 금지 사유에 해당하지 않음.

    (2) 함수 내부 방어: `rg -n "\.catch\(" src/cron/isolated-agent/delivery-dispatch.ts`
    -> logCronDeliveryErrorDeferred (244-248) 자체에는 catch 없음. 같은 파일 sync 변형 logCronDeliveryError/Warn
    (234-242) 은 async+await 라 호출처 try/catch 에 잡히나 deferred 변형만 경계 부재.

    (3) loader 측 방어: createLazyPromiseLoader (src/shared/lazy-promise.ts:16-26) 의

    `void loaded.catch(() => { promise = undefined; })` 는 cacheRejections 미설정 시 *캐시
    리셋 전용*이며 load() 반환 promise 의 rejection 을 소비하지 않는다. deliveryLoggerRuntimeLoader
    는 옵션 없이 생성 (delivery-dispatch.ts:182-184) -> cacheRejections 미설정.

    (4) R-5 실행 조건 분류: onError 콜백 (delivery-dispatch.ts:910-917) 은 `deliveryBestEffort
    ? ... : undefined` 로 bestEffort job 에서만 정의되고, 그 안의 logCronDeliveryErrorDeferred
    호출은 payload 부분 실패 시에만 도달 -> conditional-edge (정상 flow 에서 항상 실행되는 unconditional
    아님). 따라서 unconditional 방어 부재가 곧 gap 성립을 막지 못함.

    (5) production trigger 실재 (작업 지시 핵심 항목): 결함 메커니즘은 코드상 성립하나, 이를 trigger 하는 실제 caller
    경로의 *발생 확률*이 거의 0 이다. `import("./delivery-logger.runtime.js")` 는 동일 빌드에 번들된 로컬
    모듈로, 정상 배포 환경에서 dynamic import 가 reject 하는 사례는 없다. 빌드 손상 / 디스크 손상 / 부분 배포 같은 비정상
    환경이 전제되어야 한다. logError 동기 throw 경로도 pino logger 특성상 사실상 발생 안 함. 즉 "코드상 error-boundary
    gap 은 명확하나 production 재현 미확인" -> severity P3 으로 하향.

    (6) upstream-dup: `git log -S "logCronDeliveryErrorDeferred" -- src/cron/isolated-agent/delivery-dispatch.ts`
    -> 1건 (139a3f49fe). 이 floating promise 를 catch 로 보강하는 후속 commit / open PR 없음 (인지
    범위).'''
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-21'
cross_refs: []
---
# deferred cron delivery logger floating promise has no catch — runtime import rejection escapes to process-level handler

## 문제

`logCronDeliveryErrorDeferred` (src/cron/isolated-agent/delivery-dispatch.ts:244-248) 는 lazy import promise 에 `.then()` 만 연결하고 `.catch()` 를 연결하지 않는 floating promise 다:

```ts
function logCronDeliveryErrorDeferred(message: string): void {
  void loadDeliveryLoggerRuntime().then(({ logError }) => {
    logError(message);
  });
}
```

`void` 로 promise 를 버리므로 함수 내부에 rejection 을 받을 핸들러가 없다. 같은 파일의 동기 변형 `logCronDeliveryError` / `logCronDeliveryWarn` (239-242 / 234-237) 은 `async` + `await` 라 호출처의 try/catch 에 rejection 이 잡히지만, deferred 변형은 경계가 전혀 없다.

이는 error-boundary-auditor 탐지 카테고리 B (Floating promise — `void ...().then(...)` 패턴에서 error path 없음) 의 구체 사례다.

## 발현 메커니즘

1. isolated cron job 이 `delivery.bestEffort=true` 로 실행 (`resolveCronDeliveryBestEffort`, run.ts:1026 → `dispatchCronDelivery`).
2. direct delivery 경로에서 `onError` 콜백이 `deliveryBestEffort ? ... : undefined` 로 구성됨 (delivery-dispatch.ts:910-917). bestEffort 일 때만 정의된다.
3. `sendDurableMessageBatch` 가 payload 별 전송 실패 시 `onError(err, payload)` 를 동기 호출.
4. `onError` 는 `logCronDeliveryErrorDeferred(...)` 호출 (delivery-dispatch.ts:913).
5. `logCronDeliveryErrorDeferred` 는 `void loadDeliveryLoggerRuntime().then(...)` 실행. `.catch` 없음.
6. `loadDeliveryLoggerRuntime()` 의 dynamic `import("./delivery-logger.runtime.js")` (delivery-dispatch.ts:183) 가 reject 하면, `.then` 콜백은 실행되지 않고 rejection 이 체인 끝까지 전파.
7. `createLazyPromiseLoader` (shared/lazy-promise.ts:16-26) 의 내부 `void loaded.catch(...)` 는 `cacheRejections!==true` 일 때 *자기 사본* 의 캐시 리셋만 담당. `load()` 가 반환한 promise (호출처가 `.then` 으로 이어받은 그 promise) 의 rejection 은 호출처 책임으로 남는다.
8. 처리되지 않은 rejection → process-level `unhandledRejection` 핸들러 (src/infra/unhandled-rejections.ts:513). 모듈 로드 실패 Error 는 `isAbortError` / `isTransientUnhandledRejectionError` / `isConfigError` 어디에도 해당하지 않아 line 545-546 의 generic 분기로 떨어져 `exitWithTerminalRestore("unhandled rejection")` → `process.exit(1)`.
9. 동일하게 `.then` 콜백의 `logError(message)` 가 동기 throw 해도 `.then` 이 반환한 promise 가 reject 되어 같은 경로를 탄다.

즉 delivery error 를 *로깅하려는* 코드가 오히려 게이트웨이 crash 를 유발할 수 있는 비대칭 구조다.

## 근본 원인 분석

1. **lazy-load 전환 시 경계 누락**: upstream `139a3f49fe` (2026-04-13, `perf(cron): lazy-load delivery logger runtime`) 이 동기 logger 를 lazy-import 로 바꾸면서 fire-and-forget deferred 변형을 추가. await 기반 sync 변형과 달리 deferred 변형은 promise 를 `void` 로 버리며 자체 `.catch` 를 두지 않음.
2. **loader 계약 오해 가능성**: `createLazyPromiseLoader` 의 내부 `.catch` 는 캐시 무효화 전용이지 caller 의 rejection 처리가 아니다. 옵션 없이 생성된 loader 의 `load()` rejection 은 호출처가 명시적으로 잡아야 한다.
3. **상위 boundary 가 흡수가 아닌 증폭**: process-level `unhandledRejection` 핸들러는 silent-log 가 아니라 generic reason 에 대해 `process.exit(1)` 을 호출하는 crash 핸들러. 따라서 "상위에서 처리되니 FIND 금지" 가 성립하지 않는다.

## 영향

- **현상**: bestEffort isolated cron job 의 payload 부분 실패 + 그 순간 delivery-logger.runtime 모듈 로드 실패가 겹치면 게이트웨이가 `process.exit(1)` 으로 종료.
- **production trigger 실재 (작업 지시 핵심 항목)**: **매우 낮음**. `import("./delivery-logger.runtime.js")` 는 동일 빌드에 번들된 로컬 모듈로, 정상 배포 환경에서 dynamic import 가 reject 하는 경로가 없다. 빌드 손상 / 디스크 손상 / 부분 배포 같은 비정상 환경이 전제되어야 한다. `logError` 동기 throw 경로도 pino logger 특성상 사실상 발생하지 않는다.
- **결론**: 코드상 error-boundary gap (category B) 은 명확히 성립하고 crash 까지 체인이 이어지지만, 현재 upstream/main 코드 기준 production 재현은 확인되지 않는다 → **P3** (이론적, 현재 미재현).

## 반증 탐색

### R-3 방어 경로 Grep

- `rg -n "process\.on\(['\"](uncaughtException|unhandledRejection)" src/` → production 핸들러 3건: src/index.ts:93, src/infra/unhandled-rejections.ts:513, src/cli/run-main.ts:686. 상위 process boundary 는 **존재한다**.
- 그러나 unhandled-rejections.ts:513-547 핸들러는 generic reason 에 대해 `process.exit(1)` 을 호출하는 crash 핸들러다 (545-546). 결함을 흡수하지 않고 crash 로 증폭 → "상위 boundary 가 처리하므로 FIND 금지" 사유에 해당하지 않음.
- `rg -n "\.catch\(" src/cron/isolated-agent/delivery-dispatch.ts` → `logCronDeliveryErrorDeferred` (244-248) 본체에 catch 없음.

### R-5 실행 조건 분류

| 경로 | 실행 조건 |
|---|---|
| `onError` 콜백 정의 (delivery-dispatch.ts:910-917) | `conditional-edge` — `deliveryBestEffort` 인 job 에서만 정의 |
| `onError` 내 `logCronDeliveryErrorDeferred` 호출 (913) | `conditional-edge` — payload 부분 실패 시에만 |
| sync 변형 `logCronDeliveryError` (239-242) | await 기반, 호출처 try/catch 에 잡힘 (gap 없음) |
| process-level unhandledRejection handler | `unconditional` — 단, 이것은 *흡수* 가 아닌 *crash* 동작 |

정상 flow 에서 항상 실행되는 unconditional 방어가 없으므로 R-5 상 "unconditional 방어 존재로 gap 무효" 가 성립하지 않는다.

### 기존 테스트 커버리지

- `rg -n "logCronDeliveryErrorDeferred" src/cron/` → production code 정의 1건 + 호출 1건. deferred logger 의 import-rejection 경로를 assert 하는 테스트 없음.

### Primary-path inversion (CAL-001)

이 gap 이 버그가 되려면 어떤 정상 경로가 실패해야 하는가? → deferred 변형에는 catch 가 *아예 없으므로* mask 할 unconditional cleanup 자체가 부재. 정상 경로 실패 의존이 아니라 경계 자체가 비어 있는 구조.

### Upstream-dup (CAL-004/008)

- `git log -S "logCronDeliveryErrorDeferred" -- src/cron/isolated-agent/delivery-dispatch.ts` → 1건 (139a3f49fe, 2026-04-13). 이 floating promise 를 `.catch` 로 보강하는 후속 commit / open PR 없음 (인지 범위).

## Self-check

### 내가 확실한 근거

- delivery-dispatch.ts:244-248 의 `void ...().then(...)` 에 `.catch` 부재 — Read 로 확인.
- sync 변형 `logCronDeliveryError` / `logCronDeliveryWarn` (234-242) 은 `async`+`await` 라 호출처 try/catch 의존 — Read 로 확인.
- `createLazyPromiseLoader` 의 `void loaded.catch(...)` 가 캐시 리셋 전용 — src/shared/lazy-promise.ts:16-26 Read 로 확인.
- process unhandledRejection 핸들러가 generic reason 에 대해 `process.exit(1)` — src/infra/unhandled-rejections.ts:545-546 Read 로 확인.
- `onError` 콜백이 bestEffort job 에서만 정의되고 payload 실패 시 호출 — delivery-dispatch.ts:910-917 Read 로 확인.

### 내가 한 가정

- `loadDeliveryLoggerRuntime()` 의 dynamic import 가 production 에서 reject 할 확률이 거의 0 이라는 판단 — 번들된 로컬 모듈 특성 기반. 만약 이 모듈이 런타임에 lazy-fetch 되는 환경 (예: 분할 번들 / 원격 chunk) 이면 trigger 확률이 올라가 severity 상향 여지 있음. 현재 빌드 구성 확인은 allowed_paths 밖이라 미수행.
- `logError` 가 동기 throw 하지 않는다는 가정 — pino 계열 logger 특성. delivery-logger.runtime.js 본체는 allowed_paths 밖이라 Read 안 함.

### 확인 안 한 것 중 영향 가능성

- delivery-logger.runtime.js 의 모듈 top-level 코드가 import 시 throw 할 수 있는지는 미확인 (allowed_paths 밖). top-level throw 가 있으면 import reject 확률이 가정보다 높아진다.
- 동일 파일 내 다른 deferred/void 패턴 (`logCronDeliveryErrorDeferred` 외) 은 grep 상 추가 매치 없음.
