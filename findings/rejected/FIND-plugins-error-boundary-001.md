---
_parse_error: "mapping values are not allowed here\n  in \"<unicode string>\", line\
  \ 65, column 73:\n     ... plugin-entry.ts:159) 가 `register: (api: OpenClawPluginApi)\
  \ => vo ... \n                                         ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: mapping values are not allowed here\n  in \"<unicode\
  \ string>\", line 65, column 73:\n     ... plugin-entry.ts:159) 가 `register: (api:\
  \ OpenClawPluginApi) => vo ... \n                                         ^"
---
# bundled-capability-runtime 의 void register(api) 가 async register Promise 를 에러 격리 없이 폐기

## 문제

`src/plugins/bundled-capability-runtime.ts:300` 의 `void register(captured.api)` 는
bundled plugin 의 register 함수가 async function 인 경우 반환된 Promise 를 try/catch
밖으로 escape 시킨다. 두 가지 동시 증상이 발생한다:

1. **unhandled rejection**: Promise rejection 이 어떤 handler 체인에도 연결되지 않아
   Node 프로세스 level 의 unhandledRejection 이벤트로 올라간다. `--unhandled-rejections=strict`
   또는 fatal rejection handler 가 설치된 환경에선 process crash.
2. **silent registration loss**: async body 에서 수행되는 `api.registerHttpRoute` 등의
   side effect 는 `captured` 가 이미 snapshot 된 뒤에 실행되어 record/registry 의
   `*Ids` 배열과 `providers/cliBackends/...` 배열에 반영되지 않는다.

## 발현 메커니즘

1. `contracts/registry.ts:267 / 347 / 409` 에서 `loadBundledCapabilityRuntimeRegistry({ pluginIds })` 호출.
2. `bundled-capability-runtime.ts:183-474` 본체 진입. line 216 `discoverOpenClawPlugins` 로
   plugin 목록 scan, line 220 `loadPluginManifestRegistry` 로 manifest 확인.
3. line 235 for 루프에서 각 bundled candidate 에 대해:
   - line 283 `mod = getJiti(safeSource)(safeSource)` 로 jiti 기반 CJS/ESM interop import.
   - line 289-290 `resolvePluginModuleExport(mod)` 로 register 함수 resolve.
4. line 298 `try {` 블록 진입.
5. line 299 `createCapturedPluginRegistration()` — capture 객체 sync 생성.
6. line 300 `void register(captured.api);` — plugin 의 register 호출.
   - plugin 이 sync register (SDK contract 준수) 면 `register()` 는 undefined 리턴, `captured.*`
     가 register 본체에서 동기 mutation 된 결과 반영.
   - plugin 이 `async register` (SDK contract 위반이나 TS 타입상 허용) 면 register()
     본체 실행은 첫 await 지점까지만 동기 진행되고 Promise 반환. `void` 연산자는 반환값을
     버리지만 Promise 자체는 microtask queue 에 남음.
7. line 301-467: `captured.*Ids.map(...)` 로 record/registry 필드 populate.
   async register 의 경우 첫 await 지점 이전까지 실행된 sync registration 만 captured 에 반영.
8. line 467 `registry.plugins.push(record)` 로 레지스트리에 추가 후 line 468 catch 도달하지 않고 정상 종료.
9. **microtask queue 에 남은 register Promise** 가 resolve/reject:
   - resolve: `api.registerHttpRoute({ ... })` 등 호출 → captured 는 이미 main flow 에 소비된 상태이므로
     이 시점에 registration mutation 해도 **record/registry 에 반영 안 됨** (참조 관계가 이미 끊어짐).
   - reject: Promise 에 `.catch` 가 연결되지 않았고 상위 try 는 이미 닫혔으므로 node event loop 가
     unhandledRejection 이벤트 emit.

## 근본 원인 분석

1. **Promise escape** (`bundled-capability-runtime.ts:300`): `void` 연산자는 TS/JS 관점에서
   "결과값을 무시하라" 는 hint 일 뿐, Promise 의 lifecycle 에 어떤 영향도 없다. microtask queue
   에 그대로 남는다. 안전하게 swallow 하려면 `void Promise.resolve(register(...)).catch(noop)`
   또는 `try { await register(...) } catch { /* ... */ }` 형태가 필요.

2. **SDK contract vs runtime reality** (`src/plugin-sdk/plugin-entry.ts:159`): SDK 타입상
   `register: (api: OpenClawPluginApi) => void` 로 async 를 표현하지 않음. 그러나 TS 의 void 타입은
   "return value 를 쓰지 않겠다" 는 의미이지 "sync function 이어야 한다" 는 의미가 아니다. 따라서
   `async register(api): Promise<void>` 도 void 반환 타입 slot 에 들어갈 수 있음. ts-strict 수준
   lint rule (`no-misused-promises`) 이 없는 한 통과.

3. **실제 bundled 확장이 async register 사용** (`extensions/webhooks/index.ts:10`):
   webhooks 플러그인의 definePluginEntry call 이 `async register(api) { const routes = await resolveWebhooksPluginConfig(...) }` 형태. 이 경로가 bundled-capability-runtime 으로 로드되면
   line 300 의 void 가 Promise 를 drop.

4. **다른 경로와의 불일치** (`loader.ts:1815-1823`): full loader 의 동일 호출 지점은
   `const result = register(api); if (result && typeof result.then === 'function') { diagnostic push }`
   형태로 최소한 diagnostic 을 남긴다. bundled-capability-runtime.ts 는 이 defensive detection 도
   없음 — 동일 concern 에 대한 대응이 두 경로에서 불일치.

## 영향

- **impact_hypothesis**: crash (unhandled rejection) + wrong-output (silent registration loss)
- **재현 시나리오**:
  1. webhooks plugin 이 enabled 상태에서 OpenClaw CLI startup.
  2. provider contract cache 가 warm 상태가 아니면 `contracts/registry.ts:347`
     `providerContractRegistryCache = loadBundledCapabilityRuntimeRegistry({...})` 호출.
  3. for 루프가 webhooks candidate 에 도달 → line 283 jiti import 성공 → line 300 `void register(captured.api)`.
  4. webhooks 의 register 본체: `await resolveWebhooksPluginConfig(...)` — 가정: config 파일 파싱 실패
     또는 path.join 결과가 workspace root 밖으로 escape → Promise rejection.
  5. rejection 이 handler 없이 node event loop 에 도달 → `unhandledRejection` 이벤트.
  6. Node 기본값 (Node 22) 에선 warning + `node:process` 의 default handler 가 fatal 처리할 수 있음.
- **부가 영향 (registration loss)**: webhooks 가 설령 정상 resolve 되어도 `api.registerHttpRoute`
  가 captured 에 반영되지 않음 — webhook route 가 gateway 에 노출되지 않음 (기능 완전 부재).
  이 영향은 webhooks contract validation 경로 전반에서 발생.
- **빈도**: bundled extensions 가 현재 webhooks 하나만 async register 를 사용하지만, 플러그인
  저자 관점에서 `async register` 는 자연스러운 패턴이라 신규 bundled plugin 에 대해 언제든 확장됨.
- **P1 근거**: crash (unhandled rejection) 과 기능 부재 (wrong-output) 을 동시에 만족하는 boundary gap.
  현재 webhooks 가 sync init-only 로 의도되더라도 `await resolveWebhooksPluginConfig` 실패 경로가
  존재하므로 production trigger 가능.

## 반증 탐색

**R-3 Grep 명령 + 결과:**

1. `rg -n "void register|await register" src/plugins/bundled-capability-runtime.ts`
   → 오직 line 300 `void register(captured.api);`. `await register` 경로 없음.
2. `rg -n "\.catch\(|Promise\." src/plugins/bundled-capability-runtime.ts`
   → 0건. Promise chaining 없음. 방어 없음.
3. `rg -n "process\.on.*unhandledRejection" src/plugins/`
   → 0건 (test 파일 제외). 로컬 suppression handler 없음.

**추가 반증 탐색 (R-5 primary-path inversion):**

- **"이 crash 가 성립하려면 어떤 상위 try 가 없어야 하는가?"**: bundled-capability-runtime 의 try (line 298-470)
  는 sync throw 만 잡음 (Promise rejection 은 catch 에 안 도달). caller contracts/registry.ts:267 도 sync 호출 사용 (`const registry = loadBundledCapabilityRuntimeRegistry({...})`) 로 감싸지 않음. 따라서 Promise
  rejection 은 async stack 에서 process 에 직접 도달.
- **"async register 사용이 실제로 발생하는가?"**: 예. `extensions/webhooks/index.ts:10` 이 정확히
  `async register(api: OpenClawPluginApi)` 를 사용.
- **SDK 타입 방어**: `src/plugin-sdk/plugin-entry.ts:159` 에서 `register: (api) => void`.
  TypeScript 의 void return type 은 async function (Promise<void>) 을 widening 으로 받아들임.
  ts-strict `no-misused-promises` rule 미적용 → TS 체크 통과.
- **loader.ts 의 동일 패턴과의 일관성**: loader.ts:1815-1823 은 async detection 코드 존재 (diagnostic push).
  그러나 diagnostic 만 남기고 Promise 는 동일하게 floating. 즉 full loader 도 동일 쎄라퉁이지만
  최소한 "사용자가 observable 한 warning" 을 제공. bundled-capability-runtime 은 silent.
- **기존 테스트**: `cli.test.ts:322` 에서 "plugin register returned a promise; async registration is ignored"
  메시지가 loader 의 diagnostic 으로 존재함을 확인하는 테스트 있음. bundled-capability 경로에 대한
  해당 테스트는 grep 결과 없음.
- **webhooks 가 "load" 경로에서만 async register 가 실행되는가?**: 확인 결과 webhooks 는
  definePluginEntry 에 async register 전달. bundled-capability-runtime 은 이 definePluginEntry 의
  결과 객체의 `register` 를 호출하므로 항상 async 로 실행.

## Self-check

### 내가 확실한 근거

- `bundled-capability-runtime.ts:300` `void register(captured.api);` — 직접 Read.
- `loader.ts:1815-1823` 에 async detection 과 diagnostic push 존재 — 직접 Read.
- `extensions/webhooks/index.ts:10` `async register(api: OpenClawPluginApi)` — 직접 Read.
- `plugin-entry.ts:159` `register: (api: OpenClawPluginApi) => void` SDK 타입 — 직접 Read.
- `contracts/registry.ts:267/347/409` 가 `loadBundledCapabilityRuntimeRegistry` 를 sync 호출 — Grep 결과.

### 내가 한 가정

- webhooks 의 `resolveWebhooksPluginConfig` 가 production 에서 reject 하는 경로가 실제 존재한다 —
  config 파일 permission/파싱 실패 등. 코드 정밀 검증 없음. 만약 resolveWebhooksPluginConfig 가
  try/catch 로 완벽하게 self-contained 하면 rejection 이 안 날 수 있음.
- Node 22 기본 unhandled-rejections 정책: warn 이후 silence. 그러나 `--unhandled-rejections=strict`
  플래그 또는 fatal rejection handler 가 설치된 환경 (예: 개발 환경 도커 이미지) 에선 crash.
  운영 환경 정확한 설정을 확인하지 않음.
- webhooks 외 bundled 확장 중 async register 쓰는 곳은 없다고 확인 (extensions 디렉토리 grep 결과
  webhooks 만 매치). 그러나 third-party plugins 는 본 감사 scope 외.

### 확인 안 한 것 중 영향 가능성

- webhooks async body 가 reject 없이 항상 resolve 한다면 "crash" 시나리오는 무효화되고 오직
  "silent registration loss" 만 남음. severity 는 P1 → P2 로 하향 가능.
- `void` 연산자가 Node 의 특정 버전에서 Promise rejection tracking 을 묵시적으로 suppress 하는
  semantics 변경 가능성 (`node --unhandled-rejections=none` 등). 검증 안 함.
- `contracts/registry.ts` 의 다른 호출 지점 (line 267/347/409) 중 일부만 webhooks 를 대상으로
  pluginIds 에 포함시키는지 — bundled-capability-runtime.ts:237 의 필터 `pluginIds.has(manifest.id)` 로
  caller 쪽 pluginIds 가 webhooks 를 포함해야만 트리거. 각 caller 의 pluginIds 구성을 trace 하지 않음.
