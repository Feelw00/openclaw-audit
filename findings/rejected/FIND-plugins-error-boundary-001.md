---
id: FIND-plugins-error-boundary-001
cell: plugins-error-boundary
title: bundled-capability register void-escapes async Promise to process fatal
file: src/plugins/bundled-capability-runtime.ts
line_range: 299-301
evidence: "```ts\n    try {\n      const captured = createCapturedPluginRegistration();\n\
  \      void register(captured.api);\n```\n"
symptom_type: error-boundary-gap
problem: src/plugins/bundled-capability-runtime.ts:301 의 void register(captured.api)
  는 register 가 async function 일 때 반환되는 Promise 를 try/catch 와 분리된 microtask queue 로
  내보낸다. Promise rejection 은 상위 try 로 잡히지 않고 Node 전역 unhandledRejection 핸들러(src/infra/unhandled-rejections.ts:345-379)
  로 전파되며, 해당 핸들러는 AbortError/fatal-classified/transient-network/transient-sqlite 로
  분류되지 않는 일반 Error 에 대해 process.exit(1) 을 호출한다. 즉 async register 를 쓰는 bundled plugin
  이 첫 await 이후 reject 하면 gateway 프로세스가 즉시 종료된다. 또한 resolve 경로에서도 captured 가 main flow
  에서 이미 소비된 뒤에 api.registerHttpRoute 등이 실행되어 record/registry 에 반영되지 않는 silent registration
  loss 가 동반된다.
mechanism: '1. contracts/registry.ts:267/347/409 에서 loadBundledCapabilityRuntimeRegistry({
  pluginIds }) 를 sync 호출.

  2. bundled-capability-runtime.ts:235 for 루프가 각 bundled candidate 에 대해 line 281-286
  try 로 jiti 기반 module import.

  3. line 290-291 resolvePluginModuleExport(mod) 로 register 함수 resolve.

  4. line 299 try 블록 진입, line 300 createCapturedPluginRegistration(), line 301 void
  register(captured.api).

  5. plugin 이 async register (SDK 타입상 허용) 이면 register() 는 첫 await 까지만 동기 실행 후 Promise
  반환. void 연산자는 반환값만 버리고 Promise 는 microtask queue 에 그대로 살아있음.

  6. line 302-330 captured.*Ids 로 record 필드 populate 후 line 계속 registry.push.

  7. try 블록 종료(line 338 catch). sync 예외가 아니므로 catch 미진입.

  8. microtask 가 실행되며 async register body 의 await 이후 로직 평가.

  9a. reject 경로. Promise 에 .catch 연결 없음 -> node 이벤트 루프가 unhandledRejection emit.

  9b. src/infra/unhandled-rejections.ts:346 installUnhandledRejectionHandler 가 등록한
  글로벌 리스너 실행.

  9c. reason 이 AbortError/fatal-code/config-code/transient-network/transient-sqlite
  중 어느 것도 아니면 line 377-378 console.error 후 exitWithTerminalRestore -> process.exit(1).

  10. resolve 경로에서도 api.registerHttpRoute 등의 side effect 가 captured 에 반영되어도 이미 record/registry
  배열은 main flow 에서 snapshot 되어 registry 에 insert 된 후이므로 추가분은 관측되지 않음.

  '
root_cause_chain:
- why: 왜 void register(captured.api) 가 async Promise 를 격리 없이 폐기하는가?
  because: void 연산자는 반환값을 버릴 뿐 Promise 수명을 관리하지 않음. catch 체인 또는 await 이 필요.
  evidence_ref: src/plugins/bundled-capability-runtime.ts:301
- why: 왜 상위 try 가 async rejection 을 잡지 못하는가?
  because: try/catch 는 sync throw 만 포착. Promise rejection 은 microtask queue 로 이동해
    async stack 에서 별도 경로로 propagate.
  evidence_ref: src/plugins/bundled-capability-runtime.ts:299-338
- why: 왜 process-level 핸들러가 이 rejection 을 fatal 로 처리하는가?
  because: installUnhandledRejectionHandler 는 AbortError/fatal-code/config/transient
    를 제외한 모든 reason 에 대해 exitWithTerminalRestore -> process.exit(1) 을 호출. plugin 의
    일반 Error 는 어느 분류에도 속하지 않음.
  evidence_ref: src/infra/unhandled-rejections.ts:345-379
- why: 왜 SDK 가 sync register 만 강제하지 않는가?
  because: 'src/plugin-sdk/plugin-entry.ts 의 register: (api) => void 시그니처는 TypeScript
    의 void 반환 타입으로 async function 을 widening 허용. no-misused-promises lint 미적용으로 async
    register 가 타입 체크 통과.'
  evidence_ref: src/plugin-sdk/plugin-entry.ts
- why: 왜 같은 도메인의 setup-registry.ts 는 안전한데 이 경로는 아닌가?
  because: setup-registry.ts:272-279 의 ignoreAsyncSetupRegisterResult 는 void Promise.resolve(result).catch(()
    => undefined) 로 명시 swallow. bundled-capability-runtime 경로는 해당 유틸을 재사용하지 않고 raw
    void 만 사용 - 설계 단계에서 async 처리 정책 일관성 부재.
  evidence_ref: src/plugins/setup-registry.ts:272-279
impact_hypothesis: crash
impact_detail: '정성 - async register 를 사용하는 bundled plugin 이 어떤 await 이후 Error 를 throw
  하면 gateway process 가 즉시 process.exit(1) 로 종료됨.

  정량 근거:

  - 실제 async register 사용처: extensions/webhooks/index.ts:10 (await resolveWebhooksPluginConfig(...))
  - 확인됨.

  - 발동 빈도: contracts/registry.ts 의 loadBundledCapabilityRuntimeRegistry 가 호출되는 모든
  경로 (provider contract cache warm, gateway startup, cli-metadata). webhooks 가 enabled
  상태면 매번 경로 통과.

  - 부가 영향: resolve 경로에서도 registration 이 registry 에 반영되지 않음 -> webhooks HTTP route
  가 gateway 에 노출 안 됨 (기능 부재).

  '
severity: P0
counter_evidence:
  path: src/plugins/setup-registry.ts
  line: 272-279
  reason: "R-3 Grep 명령 + 결과:\n(1) rg -n \"void register|await register\" src/plugins/bundled-capability-runtime.ts\n\
    \    -> 오직 line 301 void register(captured.api). await 경로 없음.\n(2) rg -n \"\\\
    .catch\\(|Promise\\.\" src/plugins/bundled-capability-runtime.ts\n    -> 0 건.\
    \ Promise chaining 없음.\n(3) rg -n 'process\\.on\\([\"]unhandledRejection' src/plugins/\n\
    \    -> 0 건 (test 제외). 로컬 suppression handler 없음.\n(4) rg -n 'process\\.on\\([\"\
    ]unhandledRejection' src/infra/\n    -> src/infra/unhandled-rejections.ts:345.\
    \ 글로벌 핸들러가 fatal 처리.\nR-5 실행 조건 분류:\n- line 299-338 try/catch: unconditional for\
    \ sync, does-not-cover-async (Promise rejection 미포착).\n- setup-registry.ts:272-279\
    \ ignoreAsyncSetupRegisterResult: unconditional swallow. bundled-capability-runtime\
    \ 은 해당 helper 미사용.\n- src/infra/unhandled-rejections.ts:345 글로벌 핸들러: unconditional,\
    \ 일반 Error 는 line 377-378 에서 process.exit(1).\nunconditional 방어가 async rejection\
    \ 경로에 존재하지 않아 주장 성립.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-19'
related_tests:
- src/plugins/cli.test.ts
- src/plugins/loader.cli-metadata.test.ts
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# bundled-capability-runtime void register escapes async Promise to process fatal handler

## 문제

`src/plugins/bundled-capability-runtime.ts:301` 의 `void register(captured.api)` 는
bundled plugin 의 register 함수가 async function 인 경우 반환된 Promise 를 try/catch
밖으로 escape 시킨다. 두 가지 동시 증상이 발생한다.

1. unhandled rejection. Promise rejection 이 어떤 handler 체인에도 연결되지 않아
   Node 프로세스 레벨 unhandledRejection 이벤트로 올라간다. 이는
   `src/infra/unhandled-rejections.ts:345-379` 의 글로벌 handler 에서
   AbortError/fatal-code/config-code/transient-network/transient-sqlite 로 분류되지
   않는 모든 일반 Error 에 대해 `process.exit(1)` 을 호출한다 - 즉 플러그인 register
   async 본체의 일반 Error 는 gateway 프로세스 종료를 유발한다.
2. silent registration loss. async body 에서 수행되는 `api.registerHttpRoute` 등은
   `captured` 가 이미 snapshot 된 뒤에 실행되어 record/registry 배열에 반영되지 않는다.

## 발현 메커니즘

1. `contracts/registry.ts:267 / 347 / 409` 에서 `loadBundledCapabilityRuntimeRegistry({ pluginIds })` 호출.
2. `bundled-capability-runtime.ts:235` for 루프에서 각 bundled candidate 를 순회.
3. line 281-286 try/catch 로 jiti 기반 CJS/ESM interop import 수행.
4. line 290-291 `resolvePluginModuleExport(mod)` 로 register 함수 resolve.
5. line 299 try 블록 시작. line 300 `createCapturedPluginRegistration()` 으로 capture 생성.
6. line 301 `void register(captured.api)` 호출.
   - sync register: 본체의 `api.register*` side effect 가 동기 완료.
   - async register (webhooks 형태): 첫 await 지점까지만 동기 진행, 나머지는 microtask 큐로.
7. line 302-330 `captured.*Ids.map(...)` 로 record 필드 populate.
8. try 블록 종료. async rejection 은 catch 에 도달하지 않음.
9a. async reject 경로. Promise 는 어떤 .catch 도 없이 unhandledRejection 으로.
9b. `installUnhandledRejectionHandler` (unhandled-rejections.ts:339) 가 AbortError/fatal/config/transient 판정을 순차 실행.
9c. plugin 의 일반 Error 는 어디에도 속하지 않으므로 line 377-378 `console.error` 후 `exitWithTerminalRestore("unhandled rejection")` -> `process.exit(1)`.
10. async resolve 경로. `api.registerHttpRoute` 호출이 captured 에 반영되어도 main flow 의 record 는 이미 registry.plugins 에 push 된 상태라 late insert 가 관측되지 않음.

## 근본 원인 분석

1. Promise escape (`bundled-capability-runtime.ts:301`). `void` 연산자는 TS/JS
   관점에서 "결과값을 무시하라" hint 일 뿐, Promise lifecycle 에 어떤 영향도 없다.
   microtask queue 에 그대로 남는다. 안전하게 swallow 하려면
   `void Promise.resolve(register(...)).catch(noop)` 또는
   `try { await register(...) } catch { ... }` 가 필요.

2. SDK contract vs runtime reality (`src/plugin-sdk/plugin-entry.ts:159`). SDK 타입
   `register: (api: OpenClawPluginApi) => void` 는 sync 를 의도하지만 TS 의 void
   반환 타입은 async function 을 block 하지 않는다. ts-strict `no-misused-promises`
   rule 이 없는 한 통과.

3. 실제 bundled 확장이 async register 사용 (`extensions/webhooks/index.ts:10`).
   webhooks 플러그인의 definePluginEntry 가 `async register(api) { const routes = await resolveWebhooksPluginConfig(...) }` 형태. 이 경로가 bundled-capability-runtime 으로
   로드될 때마다 line 301 의 void 가 Promise 를 drop.

4. 동일 도메인 안전 패턴 미채택 (`src/plugins/setup-registry.ts:272-279`).
   `ignoreAsyncSetupRegisterResult` 는 `void Promise.resolve(result).catch(() => undefined)`
   로 명시적 swallow. bundled-capability-runtime 은 이 helper 를 사용하지 않고 raw void
   만 사용. 정책 불일치.

5. Process level 핸들러가 fatal (`src/infra/unhandled-rejections.ts:345-379`).
   등록된 핸들러는 reason 을 classification 하여 AbortError/fatal/config/transient 가
   아닌 모든 Error 를 "unhandled promise rejection" 으로 간주, `process.exit(1)`.

## 영향

- **impact_hypothesis**: crash (unhandled rejection -> process.exit(1)) + wrong-output (silent registration loss).
- **재현 시나리오**:
  1. webhooks plugin 이 enabled 상태에서 OpenClaw CLI 또는 gateway startup.
  2. provider contract cache 가 warm 상태가 아니면 `contracts/registry.ts:347`
     `providerContractRegistryCache = loadBundledCapabilityRuntimeRegistry({...})` 호출.
  3. for 루프가 webhooks candidate 에 도달 -> line 281 jiti import 성공 -> line 301 `void register(captured.api)`.
  4. webhooks 의 register 본체. `await resolveWebhooksPluginConfig(...)` 중 설정
     I/O 에러 혹은 workspace root 외 경로 escape 가 발생하면 Promise rejection.
  5. rejection 이 handler 없이 node 이벤트 루프에 도달 -> `unhandledRejection`.
  6. unhandled-rejections.ts 글로벌 handler 가 일반 Error 로 분류 -> process.exit(1).
- **부가 영향 (registration loss)**: webhooks 가 정상 resolve 되어도 `api.registerHttpRoute`
  가 captured 에 반영되지 않아 webhook route 가 gateway 에 노출 안 됨 (기능 완전 부재).
- **빈도**: bundled extensions 중 현재 async register 가 확정된 것은 webhooks 이며,
  loadBundledCapabilityRuntimeRegistry 경로는 gateway/CLI startup 혹은 contract cache
  warm 시 실행. webhooks 가 enabled 되어 있으면 매 startup 에서 관문 통과.
- **P0 근거**: crash (unhandled rejection) + 기능 부재 (wrong-output) 를 동시 유발하며,
  unhandled-rejections.ts 가 fatal 처리를 확인 (AbortError 등 분류 없으면 `process.exit(1)`).
  production trigger 조건 (webhooks async body 의 config 에러) 가 합리적으로 존재.

## 반증 탐색

R-3 Grep 명령 + 결과.

1. `rg -n "void register|await register" src/plugins/bundled-capability-runtime.ts`
   -> 오직 line 301 `void register(captured.api)`. await register 경로 없음.
2. `rg -n "\.catch\(|Promise\." src/plugins/bundled-capability-runtime.ts`
   -> 0 건. Promise chaining 없음. 방어 없음.
3. `rg -n 'process\.on\(["]unhandledRejection' src/plugins/`
   -> 0 건 (test 제외). 로컬 suppression handler 없음.
4. `rg -n 'process\.on\(["]unhandledRejection' src/infra/`
   -> `src/infra/unhandled-rejections.ts:345`. 글로벌 핸들러가 fatal 처리.

추가 반증 탐색 (R-5 primary-path inversion).

- "이 crash 가 성립하려면 어떤 상위 try 가 없어야 하는가?"
  bundled-capability-runtime 의 try (line 299-338) 는 sync throw 만 잡는다.
  caller `contracts/registry.ts:267/347/409` 도 sync 호출이고 감싸지 않는다.
  따라서 Promise rejection 은 async stack 에서 process 에 직접 도달.
- "async register 사용이 실제로 발생하는가?" 예. `extensions/webhooks/index.ts:10` 이
  `async register(api: OpenClawPluginApi)` 를 사용.
- "SDK 타입 방어?" `src/plugin-sdk/plugin-entry.ts` 는 `register: (api) => void`.
  TypeScript 의 void return type 은 async function 을 widening 으로 받아들인다.
- "동일 도메인 안전 패턴 존재?" `src/plugins/setup-registry.ts:272-279`
  `ignoreAsyncSetupRegisterResult` 가 `void Promise.resolve(result).catch(() => undefined)`
  로 명시적 swallow. bundled-capability-runtime 은 이 helper 를 사용하지 않음.
- "기존 테스트?" `cli.test.ts:322` 에서 loader 의 diagnostic 메시지 테스트 있음.
  bundled-capability 경로의 async Promise 안전성 테스트는 grep 결과 없음.
- "unhandled-rejections 분류 확인". `src/infra/unhandled-rejections.ts:357-378`
  AbortError/FATAL_ERROR_CODES/CONFIG_ERROR_CODES/TRANSIENT_* 중 어디에도 속하지 않는
  일반 Error 는 line 377-378 에서 `console.error` 후 `exitWithTerminalRestore` ->
  `process.exit(1)` 호출. "log-only" 분기 없음.

## Self-check

### 내가 확실한 근거

- `bundled-capability-runtime.ts:299-338` 본체 직접 Read. try/catch 범위와 void 사용 확인.
- `src/infra/unhandled-rejections.ts:339-379` 글로벌 handler 의 분기 직접 Read.
  fatal/config/transient 외에는 `process.exit(1)` 경로 확정.
- `setup-registry.ts:272-279` 의 안전 패턴 직접 Read. 동일 도메인 내 우수 사례 존재.
- `loader.ts:1753-1762` 의 대체 경로 (diagnostic push) 직접 Read. bundled-capability 와 다른 전략.

### 내가 한 가정

- `extensions/webhooks/index.ts:10` 의 async register 를 grep 결과로만 확인했고,
  `resolveWebhooksPluginConfig` 가 실제 production 에서 reject 하는 경로가 존재한다고
  가정. 설정 I/O 실패, path escape 등의 경로는 보편적이나 정량 근거 없음.
- bundled-capability-runtime.ts 가 production gateway startup 경로에서 실행됨을 가정.
  contracts/registry.ts 호출 경로 trace 는 allowed_paths 내 확인 (loadBundledCapabilityRuntimeRegistry
  3곳 호출). 단 각 caller 의 pluginIds 구성이 webhooks 를 포함하는지 세부 trace 는 미수행.
- unhandled-rejections 글로벌 핸들러가 gateway process 에서 실제 설치됨을 가정.
  `installUnhandledRejectionHandler` export 만 확인했고 caller (src/index.ts 또는
  src/cli/run-main.ts) 에서 호출되는지 직접 검증은 allowed_paths 외.

### 확인 안 한 것 중 영향 가능성

- unhandled-rejections.ts 가 실제 gateway startup 에서 install 되는지. 일부 entrypoint
  에서 install 스킵되면 P0 는 crash 가 아닌 silent (기능 부재만) 로 하향.
- webhooks async body 가 항상 resolve 하는 happy path 만 존재한다면 crash 는 미발현.
  그러면 severity 는 P1 로 하향 (registration loss 만 남음).
- 다른 bundled extension 중 async register 를 쓰는 후보. grep 결과 webhooks 만 매치했지만
  third-party plugin 은 scope 외.
- `contracts/registry.ts` 의 세 호출 지점 중 어느 것이 production hot path 인지의 세부.
  단일 호출만 실제 gateway startup 에서 실행된다면 빈도 하향.
