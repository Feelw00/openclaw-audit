---
_parse_error: "while parsing a block mapping\n  in \"<unicode string>\", line 47,\
  \ column 3:\n    - why: 왜 `.catch(noop)` 를 붙이지 않았는가?\n      ^\nexpected <block end>,\
  \ but found '<scalar>'\n  in \"<unicode string>\", line 48, column 44:\n     ...\
  \ \"async registration is ignored\" 설계 의도는 \"async register 를 사용한 plu ... \n   \
  \                                      ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: while parsing a block mapping\n  in \"<unicode string>\"\
  , line 47, column 3:\n    - why: 왜 `.catch(noop)` 를 붙이지 않았는가?\n      ^\nexpected\
  \ <block end>, but found '<scalar>'\n  in \"<unicode string>\", line 48, column\
  \ 44:\n     ... \"async registration is ignored\" 설계 의도는 \"async register 를 사용한\
  \ plu ... \n                                         ^"
---
# loader.ts 의 register async Promise 가 diagnostic 만 남기고 handler 없이 부동

## 문제

`src/plugins/loader.ts:1814-1823` 의 plugin register 호출 블록은 plugin 이 async register 를
사용한 경우 return 된 Promise 를 감지하여 diagnostic 에 `level: "warn"` 으로 push 하지만,
**Promise 자체에는 `.catch()` 또는 await 을 수행하지 않는다**. 블록이 끝나면 Promise 는
local variable scope 를 벗어나 handler 없이 microtask queue 에 남는다.

- rejected 되면 → Node 의 unhandledRejection 이벤트 트리거 → warning 또는 process crash (환경 설정에 따라).
- resolved 되면 → async body 의 `api.registerHttpRoute` 등 late registration 이 loader finalize
  후 실행되어 registry 에 반영되지 않거나 snapshot consumer 관찰 밖에서 insert 발생.

## 발현 메커니즘

1. `loader.ts:1793-1797` register 함수 유효성 확인.
2. `loader.ts:1799-1804` `createApi(record, ...)` 로 plugin api 생성.
3. `loader.ts:1805-1812` process-global state snapshot (restore* 변수들).
4. `loader.ts:1814` `try {` 블록 시작.
5. `loader.ts:1815` `const result = register(api);`.
   - plugin 이 sync → undefined.
   - plugin 이 async → Promise<void>.
6. `loader.ts:1816` `if (result && typeof result.then === "function")` 로 async 판정.
7. `loader.ts:1817-1822` diagnostics.push — 메시지 "plugin register returned a promise; async registration is ignored".
   이 블록 안에 `.catch()`, `.then()`, `await` 어느 것도 없음.
8. `loader.ts:1825-1836` snapshot restore (shouldActivate false 시).
9. `loader.ts:1837-1838` registry.plugins.push + seenIds.set.
10. `loader.ts:1839` catch (sync throw 만 cover).
11. try 블록 종료. result Promise 는 모든 reference 를 잃지만 microtask queue 는 살아있음.
12. next microtask 에서 register body 의 첫 await 이후 로직 실행:
    - resolve path: `api.registerHttpRoute`, `api.registerService` 등 호출. 이때 registry.httpRoutes.push 가 실행되지만, 이미 loader 메인 루프가 다음 plugin 처리 중이거나 loadOpenClawPlugins 가 종료된 시점.
    - reject path: 어떤 .catch() 도 없으므로 node event loop 가 unhandledRejection 이벤트 emit.

## 근본 원인 분석

1. **Promise ownership 포기** (`loader.ts:1816-1822`): if 블록이 diagnostic push 만 수행하고
   Promise 를 unwrap/attach 하지 않음. 최소한 `void Promise.resolve(result).catch(noop)` 또는
   `result.catch((err) => registry.diagnostics.push({level: 'warn', ...}))` 가 필요.

2. **경로별 정책 불일치** (`loader.ts:1815` vs `loader.ts:2236`): full/setup-runtime 경로는
   sync `register(api)` 로 Promise 를 부동. cli-metadata 경로 (line 2236) 는 `await register(api)`
   로 async rejection 을 try/catch 에 통합. 동일 loader 내 두 정책이 공존.

3. **SDK 타입 한계** (`src/plugin-sdk/plugin-entry.ts:159`): `register: (api) => void` 는 sync 를
   의도하지만 TypeScript 의 void 반환 타입은 async function 을 block 하지 않음. ts-lint
   `no-misused-promises` 룰도 설정되지 않음.

4. **의도된 "무시" 의 불완전 구현**: 설계 의도상 full loader 는 async register 를 "무시" 해야
   하지만, "무시" 의 구현이 Promise 의 수명까지 관리하지 않음. 이상적이라면 `void result.catch(noop)` 로 swallow.

## 영향

- **impact_hypothesis**: crash (unhandled rejection 경로) / wrong-output (late-insert)
- **재현 시나리오**:
  1. webhooks 와 같은 async register 를 사용하는 plugin 이 설치된 환경.
  2. OpenClaw gateway startup 시 `loadOpenClawPlugins({ config, registrationMode: 'full' })` 호출.
  3. for 루프가 webhooks candidate 에 도달 → line 1815 `const result = register(api)` →
     result = Promise.
  4. Line 1817-1822 diagnostic push. result 는 floating.
  5a. webhooks 의 `await resolveWebhooksPluginConfig(...)` 가 reject → Promise rejection 이
      unhandledRejection 이벤트로 escalation. `src/infra/unhandled-rejections.ts` 의 handler
      정책에 따라 log-only 또는 process.exit.
  5b. 정상 resolve 하면 `api.registerHttpRoute` 호출. 이 시점에 `registry.httpRoutes.push`
      가 수행되지만 loader 는 이미 다음 plugin 처리 중 또는 종료. 일부 consumer 는 이미
      `registry.httpRoutes` snapshot 을 가져간 상태 → late insert 는 observe 되지 않음.
- **빈도**: webhooks 가 enable 된 gateway 시작 시마다. 현재 async register 를 쓰는 bundled
  plugin 은 webhooks 뿐이지만 third-party plugin 도 동일 경로.
- **P2 근거**: crash 가능하지만 현재 정확한 fatal handler 정책 (gateway-level unhandledRejection 설정) 을
  확인하지 않았고, 대부분의 async register body 가 reject 하지 않는 happy path 가정 가능. 또한
  diagnostic push 가 최소한 관측 가능한 신호를 남김. FIND-plugins-error-boundary-001 의
  bundled-capability-runtime 경로 (silent, P1) 보다는 덜 심각.

## 반증 탐색

**R-3 Grep 명령 + 결과:**

1. `rg -n "register\(api\)" src/plugins/loader.ts`
   → 2개 경로. line 1815 (sync — 본 FIND), line 2236 (await — cli-metadata).
   **정책 불일치** 확인.
2. `rg -n "\.catch\(" src/plugins/loader.ts`
   → 0건. loader.ts 내 어디에도 Promise.catch 사용 없음.
3. `rg -n "async registration is ignored" src/plugins/`
   → 3개 매치 (loader.ts:1821 본체 + cli.test.ts:322 + loader.cli-metadata.test.ts:585).
   테스트에서 "이 메시지가 나오면 async 는 무시된다" 를 lock-in. 그러나 Promise rejection
   처리 테스트는 grep 결과 없음.

**추가 반증 탐색 (R-5 primary-path inversion):**

- **"이 crash 가 성립하려면 어떤 방어가 없어야 하는가?"**:
  - loader.ts:1814 try/catch: sync throw 만 cover. Promise rejection 은 catch 에 도달하지 않음.
    R-5 분류: unconditional for sync, **does-not-cover-async**.
  - 상위 caller 의 try/catch: Promise rejection 은 async stack 으로 이미 main call 에서 떨어진
    후 → caller try/catch 도 cover 못함.
  - 유일한 방어는 process-level unhandledRejection handler. src/infra/unhandled-rejections.ts
    (allowed_paths 외). log-only 면 P3, fatal 이면 P1. 중간값 P2 로 평가.
- **"register resolve 시 late insert 가 실제 문제가 되는가?"**:
  - registry.httpRoutes 소비자가 어떤 시점 snapshot 을 취하는지에 따라 다름. 만약 consumer
    가 매 요청마다 registry.httpRoutes 를 읽으면 late insert 도 반영됨. 만약 startup 시 한 번
    snapshot 하면 late insert 미반영. http-registry.ts:28 `registry.httpRoutes = routes` 는
    assignment 지만 consumer chain 은 확인 안 함.
- **기존 테스트**: async register 가 사용될 때 diagnostic 이 push 되는지 확인하는 테스트 존재
  (cli.test.ts:322). Promise rejection 안전성 테스트는 grep 결과 없음.
- **Result pattern 의 부재**: CLAUDE.md 규칙 "Result<T, E> 로 결과 명시적 반환" 은 이 경로에
  적용되지 않음. register 의 반환 타입은 여전히 void. Result pattern 이 적용되었다면 async
  rejection 이 명시적 Error Result 로 converted.
- **문서화된 의도**: 주석/문서에 "async register 는 fire-and-forget 으로 허용되며 rejection 은
  무시된다" 는 명시 없음. cli.test.ts:313 의 `falls back to awaited CLI metadata collection when runtime loading ignored async registration` 테스트 이름만 간접적 언급.

## Self-check

### 내가 확실한 근거

- `loader.ts:1815-1823` 본체 직접 Read. if 블록에 catch/await 없음 확인.
- `loader.ts:2236` `await register(api)` 사용 — 동일 파일 내 다른 경로. 직접 Read.
- `extensions/webhooks/index.ts:10` async register 사용 — 직접 Read.
- `plugin-entry.ts:159` SDK 타입 `register: (api) => void` — 직접 Read.
- `cli.test.ts:322` 에서 diagnostic 메시지 테스트 존재 — Grep 결과 확인.

### 내가 한 가정

- webhooks 의 `resolveWebhooksPluginConfig` 가 production 에서 실제로 reject 될 수 있음 — 확정 없음.
- `src/infra/unhandled-rejections.ts` 의 handler 정책이 "fatal" 이라면 이 FIND 의 severity 상향.
  현재 감사 경계 밖이라 확인 안 함.
- registry.httpRoutes 소비자가 초기 snapshot 으로 고정 vs 매 요청 동적 read 인지 미확인.
- async register 를 쓰는 bundled extension 이 webhooks 외 존재하지 않음 — grep 으로 확인했으나
  third-party plugin 는 scope 외.

### 확인 안 한 것 중 영향 가능성

- src/infra/unhandled-rejections.ts 의 정확한 handler 정책 (log vs fatal). 이에 따라 severity
  P1/P2/P3 변동 가능.
- loader.ts:1815 외 full loader 내 async promise escape 경로가 있는지 (e.g., api.registerX 가
  내부적으로 async 하게 registry 조작하는 경로). 감사 scope 내 plugin-sdk 방향도 확인 필요.
- webhooks 외 async register 가 third-party plugin 에서 얼마나 널리 쓰이는지 실측 데이터 없음.
- setup-registry.ts:278 의 `void Promise.resolve(result).catch(() => undefined);` 패턴은 이 파일과
  유사한 async register 처리를 swallow-safe 하게 구현 — loader.ts 가 해당 패턴을 채택하지 않은
  이유 (의도적 차이 or 실수) 불명.
