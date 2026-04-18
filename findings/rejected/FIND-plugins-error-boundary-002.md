---
id: FIND-plugins-error-boundary-002
cell: plugins-error-boundary
title: loader full-path register floats Promise with diagnostic but no .catch
file: src/plugins/loader.ts
line_range: 1753-1762
evidence: "```ts\n      try {\n        const result = register(api);\n        if (result\
  \ && typeof result.then === \"function\") {\n          registry.diagnostics.push({\n\
  \            level: \"warn\",\n            pluginId: record.id,\n            source:\
  \ record.source,\n            message: \"plugin register returned a promise; async\
  \ registration is ignored\",\n          });\n        }\n```\n"
symptom_type: error-boundary-gap
problem: src/plugins/loader.ts:1753-1762 의 register 호출 블록은 plugin 이 async register
  를 사용한 경우 반환된 Promise 를 감지하여 diagnostic push 만 수행하고 Promise 자체에는 .catch 또는 await
  이 연결되지 않는다. try/catch 블록이 닫히면 Promise 는 local variable scope 를 벗어나 handler 없이 microtask
  queue 에 남는다. reject 경로에선 src/infra/unhandled-rejections.ts:345-379 의 글로벌 handler
  가 fatal classification 에 해당되지 않는 일반 Error 에 대해 process.exit(1) 호출 -> gateway crash.
  resolve 경로에선 api.registerHttpRoute 등 late registration 이 registry finalize 이후 실행되어
  consumer 가 관측하지 못하는 silent late-insert 발생.
mechanism: '1. loader.ts:1735-1752 register 함수 유효성 확인 및 api 준비, process-global state
  의 previous* 스냅샷 확보.

  2. line 1753 try 블록 시작.

  3. line 1754 const result = register(api) - plugin sync 면 undefined, async 면 Promise<void>.

  4. line 1755-1762 if (result && typeof result.then === function) 로 async 판정 후 diagnostic
  push. 이 블록 안에 .catch/.then/await 모두 없음.

  5. line 1764-1775 snapshot restore (shouldActivate false 시).

  6. line 1776-1777 registry.plugins.push + seenIds.set.

  7. line 1778 catch 블록 - sync throw 만 cover. restore* 호출 후 recordPluginError.

  8. try 블록 종료. async result 는 모든 reference 를 잃지만 microtask queue 는 살아있음.

  9. 다음 microtask 에서 register body 의 await 이후 실행.

  9a. resolve 경로 - api.registerHttpRoute 등 registry mutate. 이미 메인 루프 다음 plugin 처리
  중 또는 loadOpenClawPlugins 종료 상태.

  9b. reject 경로 - 어떤 .catch 도 연결되지 않아 unhandledRejection emit -> unhandled-rejections.ts
  글로벌 handler -> 일반 Error 분류로 process.exit(1).

  '
root_cause_chain:
- why: 왜 diagnostic push 후 Promise 를 unwrap 하지 않는가?
  because: if 블록이 obervable signal (diagnostic) 만 남기고 Promise 수명을 떼어내지 않음. 최소한 void
    Promise.resolve(result).catch(noop) 또는 result.catch((err) => diagnostics.push(...))
    가 필요.
  evidence_ref: src/plugins/loader.ts:1755-1762
- why: 왜 같은 파일의 다른 경로와 정책이 다른가?
  because: loader.ts:2175 cli-metadata 경로는 await register(api) 를 사용해 Promise 를 try/catch
    로 통합. full/setup-runtime 경로는 sync register 후 diagnostic 만. 동일 loader 내 두 정책 공존은
    의도된 설계보다 우발적 누락 가능성.
  evidence_ref: src/plugins/loader.ts:2175
- why: 왜 SDK 타입이 async register 를 block 하지 않는가?
  because: 'src/plugin-sdk/plugin-entry.ts 의 register: (api) => void 시그니처는 TS void
    return 타입. async function 을 widening 으로 허용. no-misused-promises lint 미적용.'
  evidence_ref: src/plugin-sdk/plugin-entry.ts
- why: 왜 process-level handler 가 자동으로 swallow 하지 않는가?
  because: src/infra/unhandled-rejections.ts:345-379 의 handler 는 AbortError/fatal/config/transient
    만 log-only 또는 fatal 로 분기. 일반 plugin Error 는 default branch 에서 exitWithTerminalRestore
    호출 - log-only path 없음.
  evidence_ref: src/infra/unhandled-rejections.ts:377-378
- why: 왜 setup-registry.ts 와 달리 safe swallow 를 쓰지 않는가?
  because: setup-registry.ts:272-279 ignoreAsyncSetupRegisterResult 가 명시적으로 catch
    -> undefined 로 swallow 구현. loader.ts 는 해당 유틸 재사용하지 않고 독자적 diagnostic-only 로직.
    공유 helper 미추출로 각 경로가 자체 정책을 가짐.
  evidence_ref: src/plugins/setup-registry.ts:272-279
impact_hypothesis: crash
impact_detail: '정성 - async register 를 쓰는 plugin 이 production 환경에서 reject 하면 gateway
  process 종료. Node 기본 unhandled-rejections 정책이 아닌 openclaw 고유 handler 에 의해 강제 exit.

  정량 근거:

  - loader.ts:1753 은 loadOpenClawPlugins (full registration) 의 핵심 경로. gateway 및 CLI
  대부분의 run 진입점에서 실행됨.

  - 실제 async register 사용처 - extensions/webhooks/index.ts:10 (await resolveWebhooksPluginConfig).
  webhooks 가 enabled 된 모든 환경에서 통과.

  - bundled-capability-runtime.ts 경로와 별개로 full loader 경로가 존재하여 webhooks 가 양쪽 모두 타짐.
  동일 실패가 양쪽에서 재현.

  - 부가 영향 - late insert 가 registry.httpRoutes snapshot consumer 에 반영되지 않을 수 있음 (gateway
  의 http 라우팅이 startup 시 snapshot 기반이면 webhook route 누락).

  '
severity: P1
counter_evidence:
  path: src/plugins/loader.ts
  line: '2175'
  reason: "R-3 Grep 명령 + 결과:\n(1) rg -n \"register\\(api\\)\" src/plugins/loader.ts\n\
    \    -> line 1754 (sync) 와 line 2175 (await) 2개 경로. 정책 불일치.\n(2) rg -n \"\\.catch\\\
    (\" src/plugins/loader.ts\n    -> 0 건. loader.ts 내 어디에도 Promise.catch 사용 없음.\n\
    (3) rg -n \"async registration is ignored\" src/plugins/\n    -> 3개 매치 (loader.ts:1760,\
    \ cli.test.ts:322, loader.cli-metadata.test.ts:585).\n    테스트는 \"이 메시지가 나오면 async\
    \ 는 무시된다\" 를 lock-in. 그러나 Promise rejection\n    처리 테스트 존재 여부 별도 확인 필요.\n(4) rg\
    \ -n 'process\\.on\\([\"]unhandledRejection' src/infra/\n    -> src/infra/unhandled-rejections.ts:345.\
    \ 글로벌 handler fatal 경로 확인.\nR-5 실행 조건 분류:\n- line 1753-1778 try/catch: unconditional\
    \ for sync, does-not-cover-async.\n- line 2175 await register(api): unconditional\
    \ for cli-metadata 경로 (다른 경로).\n- setup-registry.ts:272-279 ignoreAsyncSetupRegisterResult:\
    \ unconditional swallow, 다른 경로.\n- src/infra/unhandled-rejections.ts:345 글로벌 handler:\
    \ unconditional, 일반 Error -> process.exit(1).\nunconditional 방어 중 어느 것도 loader.ts:1754\
    \ 의 async rejection 을 cover 하지 않음.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-19'
related_tests:
- src/plugins/cli.test.ts
- src/plugins/loader.cli-metadata.test.ts
- src/plugins/cli-registry-loader.ts
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# loader.ts full-path register returns floating Promise with diagnostic but no catch

## 문제

`src/plugins/loader.ts:1753-1762` 의 plugin register 호출 블록은 plugin 이 async
register 를 사용한 경우 반환된 Promise 를 감지하여 diagnostic 에 `level: "warn"`
으로 push 하지만, **Promise 자체에는 `.catch()` 또는 await 을 수행하지 않는다**.
블록이 끝나면 Promise 는 local variable scope 를 벗어나 handler 없이 microtask
queue 에 남는다.

- reject 경로. `src/infra/unhandled-rejections.ts:345-379` 의 글로벌 handler 가
  AbortError / fatal-code / config-code / transient-network / transient-sqlite 가
  아닌 일반 Error 에 대해 `exitWithTerminalRestore` -> `process.exit(1)` 을 호출.
  gateway process 가 강제 종료.
- resolve 경로. async body 의 `api.registerHttpRoute` 등 late registration 이
  loader finalize 후 실행되어 registry consumer 가 관측하지 못하는 silent late-insert
  발생.

## 발현 메커니즘

1. `loader.ts:1735-1752` 에서 validatedConfig, api, previous* 스냅샷 준비.
2. line 1753 try 블록 시작.
3. line 1754 `const result = register(api)`. sync -> undefined, async -> Promise<void>.
4. line 1755-1762 `if (result && typeof result.then === "function")` 로 async 판정.
   diagnostics.push 수행. 이 블록 안에 `.catch()`, `.then()`, `await` 전무.
5. line 1764-1775 snapshot restore (shouldActivate false 시).
6. line 1776-1777 `registry.plugins.push` + `seenIds.set`.
7. line 1778 catch 블록. sync throw 만 cover. restoreRegisteredAgentHarnesses 등 호출 후 `recordPluginError`.
8. try 블록 종료. result 는 모든 reference 를 잃지만 microtask queue 는 살아있음.
9. 다음 microtask 에서 register body 의 await 이후 로직 실행.
   - resolve path. `api.registerHttpRoute` 등이 registry.httpRoutes.push 수행.
     이미 loader 메인 루프가 다음 plugin 처리 중이거나 `loadOpenClawPlugins` 가 종료된 시점.
   - reject path. 어떤 `.catch` 도 없으므로 node 이벤트 루프가 unhandledRejection emit.
     `src/infra/unhandled-rejections.ts:345-379` 글로벌 handler 실행. 일반 Error 는 line 377-378 에서 `process.exit(1)`.

## 근본 원인 분석

1. Promise ownership 포기 (`loader.ts:1755-1762`). if 블록이 diagnostic push 만
   수행하고 Promise 를 unwrap/attach 하지 않음. 최소한
   `void Promise.resolve(result).catch(noop)` 또는
   `result.catch((err) => registry.diagnostics.push({level: "warn", ...}))` 가 필요.

2. 경로별 정책 불일치 (`loader.ts:1754` vs `loader.ts:2175`). full/setup-runtime
   경로는 sync `register(api)` 로 Promise 를 부동. cli-metadata 경로는
   `await register(api)` 로 async rejection 을 try/catch 에 통합. 동일 loader 안에 두 정책 공존.

3. SDK 타입 한계 (`src/plugin-sdk/plugin-entry.ts`). `register: (api) => void` 는
   sync 를 의도하지만 TypeScript 의 void 반환 타입은 async function 을 block 하지 않음.
   ts-lint `no-misused-promises` 룰도 설정되지 않음.

4. 의도된 "무시" 의 불완전 구현. 설계 의도상 full loader 는 async register 를 "무시"
   해야 하지만, "무시" 의 구현이 Promise 의 수명까지 관리하지 않음. 이상적이라면
   `void result.catch(noop)` 로 swallow.

5. 동일 도메인 안전 helper 미채택 (`setup-registry.ts:272-279`).
   `ignoreAsyncSetupRegisterResult` 가 Promise rejection 을 명시적으로 catch 하여
   swallow 하는 helper 를 제공. loader.ts 는 이를 import 하지 않고 독자 처리.

## 영향

- **impact_hypothesis**: crash (unhandled rejection -> process.exit(1)) 또는 wrong-output (late-insert).
- **재현 시나리오**:
  1. webhooks 처럼 async register 를 쓰는 plugin 이 설치된 환경.
  2. OpenClaw gateway startup 시 `loadOpenClawPlugins({ config, registrationMode: 'full' })` 호출.
  3. for 루프가 webhooks candidate 에 도달 -> line 1754 `const result = register(api)` ->
     result = Promise.
  4. line 1755-1762 diagnostic push. result 는 floating.
  5a. webhooks 의 `await resolveWebhooksPluginConfig(...)` 가 reject -> Promise rejection ->
      unhandledRejection 이벤트 -> `src/infra/unhandled-rejections.ts:377-378` ->
      `process.exit(1)`.
  5b. 정상 resolve 하면 `api.registerHttpRoute` 호출. 이 시점에 `registry.httpRoutes.push`
      가 수행되지만 loader 는 이미 다음 plugin 처리 중 또는 종료. 일부 consumer 가 이미
      `registry.httpRoutes` snapshot 을 가져간 상태면 late insert 는 미관측.
- **빈도**: webhooks 가 enable 된 gateway 시작 시마다. 현재 async register 를 쓰는 bundled
  plugin 은 webhooks 뿐이지만 third-party plugin 도 동일 경로.
- **P1 근거**: unhandled-rejections.ts 가 fatal 경로임을 확인하여 실제 crash 가능 (FIND-001 과 같은 근거).
  diagnostic push 가 observable signal 을 남기지만 rejection 을 swallow 하지 않음. FIND-001 (P0) 과 달리
  full loader 경로는 상대적으로 명시적 신호가 있어 P1 로 평가. 운영 환경에서 async register
  plugin 이 reject 하는 것이 얼마나 흔한지 정량 증거 미확보로 P0 에 미치지 못함.

## 반증 탐색

R-3 Grep 명령 + 결과.

1. `rg -n "register\(api\)" src/plugins/loader.ts`
   -> 2개 경로. line 1754 (sync, 본 FIND), line 2175 (await, cli-metadata). 정책 불일치 확인.
2. `rg -n "\.catch\(" src/plugins/loader.ts`
   -> 0 건. loader.ts 내 어디에도 Promise.catch 사용 없음.
3. `rg -n "async registration is ignored" src/plugins/`
   -> 3개 매치 (loader.ts:1760 본체, cli.test.ts:322, loader.cli-metadata.test.ts:585).
   테스트에서 "이 메시지가 나오면 async 는 무시된다" 를 lock-in. 그러나 Promise rejection
   처리 테스트는 grep 결과 없음.
4. `rg -n 'process\.on\(["]unhandledRejection' src/infra/`
   -> `src/infra/unhandled-rejections.ts:345`. 글로벌 handler fatal 경로 확인.

추가 반증 탐색 (R-5 primary-path inversion).

- "이 crash 가 성립하려면 어떤 방어가 없어야 하는가?"
  - loader.ts:1753 try/catch - sync throw 만 cover. Promise rejection 은 catch 에 미도달.
    R-5 분류 - unconditional for sync, does-not-cover-async.
  - 상위 caller try/catch - Promise rejection 은 async stack 으로 main call 에서 떨어진 후라 cover 안 됨.
  - 유일한 방어는 process-level unhandledRejection handler. `src/infra/unhandled-rejections.ts:345-379`
    은 일반 Error 에 대해 `process.exit(1)` 호출. log-only 분기 없음.
- "register resolve 시 late insert 가 실제 문제가 되는가?"
  registry.httpRoutes 소비자가 어떤 시점 snapshot 을 취하는지에 따라 다름. 매 요청마다 dynamic
  read 하면 late insert 반영됨. startup 시 한 번 snapshot 하면 미반영. http-registry.ts:28
  `registry.httpRoutes = routes` 는 assignment 지만 consumer chain 은 allowed_paths 외라 미확인.
- "기존 테스트". async register diagnostic push 확인 테스트 있음 (cli.test.ts:322).
  Promise rejection 안전성 테스트는 grep 결과 없음.
- "Result pattern 의 부재". CLAUDE.md 의 "Result<T, E> 로 결과 명시적 반환" 은 이 경로에
  적용되지 않음. register 의 반환 타입은 여전히 void.
- "문서화된 의도". 주석/문서에 "async register 는 fire-and-forget 으로 허용되며 rejection 은
  무시된다" 는 명시 없음. 테스트 이름만 "async registration is ignored" 로 간접 언급.
- "동일 도메인 안전 패턴 비교". `setup-registry.ts:272-279` 가 동일 상황에서
  `ignoreAsyncSetupRegisterResult` 로 `void Promise.resolve(result).catch(() => undefined)`
  를 수행. 공유 helper 를 import 하지 않은 채 loader.ts 독자 처리.

## Self-check

### 내가 확실한 근거

- `loader.ts:1753-1778` 본체 직접 Read. if 블록에 catch/await 없음 확인.
- `loader.ts:2175` `await register(api)` 사용 - 동일 파일 내 다른 경로. 직접 Read.
- `src/infra/unhandled-rejections.ts:345-379` 글로벌 handler 로직 직접 Read. 일반 Error 에 대해 process.exit(1).
- `setup-registry.ts:272-279` 의 안전 helper 직접 Read.
- `cli.test.ts:322` 에서 diagnostic 메시지 테스트 존재 - Grep 결과 확인.

### 내가 한 가정

- webhooks 의 `resolveWebhooksPluginConfig` 가 production 에서 reject 될 수 있음. 확정 없음.
- `src/infra/unhandled-rejections.ts:installUnhandledRejectionHandler` 가 gateway process 에서
  실제 install 되는지. `src/index.ts` 또는 `src/cli/run-main.ts` 에 install 호출 존재 여부는
  allowed_paths 외라 간접 Grep 만.
- registry.httpRoutes 소비자가 초기 snapshot 고정 vs 매 요청 dynamic read 인지 미확인.
- async register 를 쓰는 third-party plugin 의 실제 빈도.

### 확인 안 한 것 중 영향 가능성

- unhandled-rejections.ts 가 실제 install 안 되는 entrypoint 가 있다면 crash 경로 약화.
  그래도 silent late-insert (wrong-output) 만 남아 P2 로 하향 가능.
- loader.ts:1754 외 full loader 내 async promise escape 경로가 있는지 (api.registerX 가
  내부적으로 async 하게 registry 조작하는 경로). plugin-sdk 방향도 추가 탐색 가능.
- setup-registry.ts:278 의 안전 helper 가 loader.ts 에서 채택되지 않은 이유
  (의도적 차이 or 실수) 불명.
