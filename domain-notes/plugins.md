# plugins 도메인 감시 기록

## 도메인 개요

`src/plugins/**` 와 `src/plugin-sdk/**` 는 openclaw 의 플러그인 발견, 로드, 레지스트리, 계약 검증을 담당하는 핵심 도메인.

### 주요 모듈

- `loader.ts` — 플러그인 로드 orchestration, manifest 검증, registry 캐싱
- `setup-registry.ts` — setup-time provider/cli backend 로드
- `public-surface-loader.ts` — bundled plugin public surface 캐싱
- `runtime-web-channel-plugin.ts` — WhatsApp web channel 플러그인 런타임 모듈 로더
- `manifest-registry.ts` — plugin manifest 파싱/검증
- `discovery.ts` — 파일시스템 기반 플러그인 탐지

---

## 실행 이력

### memory-leak-hunter (2026-04-18)

**적용 카테고리:**
- [x] A. 무제한 자료구조 성장 — 발견 2건
- [x] B. EventEmitter/리스너 누수 — 발견 0건 (해당 없음, process.on/off 쌍 properly paired)
- [x] C. 강한 참조 체인 — skipped (심화 분석 필요)
- [x] D. 핸들/리소스 누수 — skipped (fs, HTTP 관련 코드 minimal)
- [x] E. 캐시 TTL 부재 — 발견 2건 (registryCache 는 capped 하지만 유사 구조 있음)

**발견 FIND:**
- FIND-plugins-memory-002: `openAllowlistWarningCache` Set 무제한 증가
- FIND-plugins-memory-003: `runtime-web-channel-plugin.ts` jitiLoaders Map cleanup 부재

**주요 관찰:**

1. **registryCache LRU 정책 존재**: loader.ts:346 에서 cap 도달 시 oldest key 를 delete 하는 eviction 로직이 있음 → FIND-001 의 우려는 부분적으로 해결됨.

2. **openAllowlistWarningCache 특이성**: 경고 dedup 용도로 설계되었으나 clearPluginLoaderCache() 가 process-level teardown/테스트 cleanup 외에는 호출되지 않음 → 프로덕션 중 무제한 누적 가능.

3. **jitiLoaders 패턴**: public-surface-loader.ts, setup-registry.ts, runtime-web-channel-plugin.ts, doctor-contract-registry.ts, bundled-capability-runtime.ts 등 여러 파일에 동일 패턴 (Map 선언, getCachedPluginJitiLoader 호출, cleanup 없음).

4. **jiti loader lifecycle**: jiti 라이브러리의 require.cache 메커니즘으로 인해 버전/경로 변경 시 이전 loader 인스턴스들이 메모리에 계속 유지됨. 특히 web channel plugin 처럼 자주 재로드되는 module 은 누적이 두드러질 수 있음.

5. **EventEmitter 리스너**: setup-registry.test.ts:29-35 에서 process.on/off 쌍이 properly paired 되어 있음. 플러그인 내부 hook runner 도 listener 추가/제거가 대칭적.

---

## 다음 페르소나를 위한 힌트

### lifecycle-auditor (plugins-lifecycle)

1. **load 실패 시 rollback**: loader.ts 에서 load error 발생 시 registry.diagnostics 에 기록만 하고, 부분 entry 가 registry 에 남는지 확인.
2. **manifest-registry parse 실패**: manifest-registry.ts 에서 parse 실패 후 partial entry cleanup 이 제대로 되는지.
3. **dispose/unload 경로**: clearPluginLoaderCache(), clearAgentHarnesses(), clearCompactionProviders() 등의 함수들이 실제로 호출되는지, 그리고 플러그인 언로드 시 모두 호출되는지.
4. **jiti loader cleanup**: jitiLoaders.clear() 메소드 호출 경로 확인. 특히 bundled 플러그인 disable 시.

### concurrency-auditor (plugins 장기 도입 예정)

1. **Promise.race 처리**: plugin load timeout 시 loser promise handling.
2. **registry mutation**: 동시 load 시 inFlightPluginRegistryLoads 를 이용한 serialization 이 제대로 작동하는지.

---

## 기술 빚 / 미결

1. **jitiLoaders cleanup 미정책**: 여러 module 에서 동일 패턴으로 jiti loader cache 를 유지하지만, 명시적 cleanup API 가 부재. 다음 phase 에서 통일된 lifecycle hook 도입 필요.

2. **openAllowlistWarningCache 용도 재검토**: 현재 process 생애 동안 모든 warning 을 suppress 하는 것이 의도인지, 아니면 per-load-cycle 또는 TTL 기반이어야 하는지 명확화 필요.

3. **clearPluginLoaderCache 호출 빈도**: 검색 결과 테스트 환경에서만 명시적으로 호출되고 있음. 프로덕션 gateway shutdown 에서 호출되는지 확인 필요.

### clusterer (2026-04-18)

- CAND-001 (epic): 공통 원인 "plugins 도메인 글로벌 Map 캐시에 eviction 정책 부재" 로 2 FIND 묶음 (FIND-plugins-memory-001: registryCache, FIND-plugins-memory-003: jitiLoaders)

### clusterer (2026-04-18, 2차)

- 본 배치(ready/ 5건)에 FIND-plugins-memory-003 이 포함되어 있었으나 이미 기존 CAND-001 에 수용되어
  있으므로 새 CAND 생성하지 않음 (CAND-001 은 `needs-human-review` 상태, 본 세션에서 수정 금지).
- 다른 도메인 FIND 들(cron 계열: runningAtMs 관련, agents-registry 계열: sweeper self-stop) 과
  plugins FIND-003(jitiLoaders eviction 부재) 의 root_cause_chain 의미론 비교:
  - cron 은 "특정 필드의 claim/liveness 동시성" 축 — 공통성 없음.
  - agents-registry 는 "sweeper cleanup 조건 불완전" 축 — jitiLoaders 는 sweeper 가 아예 없는
    unbounded Map 이므로 축 다름.
  - 결론: plugins FIND-003 을 다른 도메인 FIND 와 묶을 epic 근거 없음. CAND-001 유지.

### plugin-lifecycle-auditor (2026-04-18)

**적용 카테고리:**
- [x] A. Load 실패 rollback 부재 — 발견 1건 (FIND-plugins-lifecycle-001)
- [ ] B. Dispose/Unload 경로 누락 — skipped (본 세션 토큰 예산. R-3 Grep 결과로
      production `unregister*`/`dispose` 부재 확인했으나 결함으로 승격 전에 loader 세션 수명
      경계와 gateway shutdown 호출 경로 확인 필요)
- [ ] C. Dynamic import 에러 격리 — skipped (loader.ts:1653-1677 recordPluginError 경로로
      jiti throw 는 이미 격리됨. 단일 플러그인 throw 가 for 루프를 중단시키지 않음 — 반증이
      이미 존재하여 결함 없음)
- [ ] D. Manifest parse 실패 후 partial state — skipped (manifest-registry.ts:532-538
      `manifestRes.ok` false 시 continue 로 clean skip. records.push 는 단일 시점.
      이론적으로 `seenIds.set` (line 631) 이 `records.push` (line 634) 이전이라 throw 발생
      시 gap 가능하나 buildRecord throw 는 매우 희귀하고 다음 load 가 cache 초기화로 해소됨)
- [ ] E. Enable/Disable 상태 drift — skipped (enable.ts 는 24 LOC 로 shallow, toggle-config 확인 필요)

**발견 FIND:**
- FIND-plugins-lifecycle-001 (P1, lifecycle-gap): register() throw 시 httpRoutes/services/commands/hooks 부분 등록 잔존

**R-3 Grep 결과 — dispose/unload/cleanup 경로 매핑 테이블:**

| register 함수 | registry 필드 | dispose/cleanup 경로 | 상태 |
|---|---|---|---|
| `registerHttpRoute` (registry.ts:351) | `registry.httpRoutes` (직접 push) | 없음 | 대칭 결여 |
| `registerService` (registry.ts:941) | `registry.services` (직접 push) | 없음 | 대칭 결여 |
| `registerCommand` (registry.ts:971) | `registry.commands` / command-registry-state (activate 분기) | 없음 | 대칭 결여 |
| `registerHook` (registry.ts:213) | `registry.hooks` (직접 push 추정) | 없음 | 대칭 결여 |
| `registerGatewayMethod` (registry.ts:308) | `registry.gatewayMethods` | 없음 | 대칭 결여 |
| `registerProvider` (registry.ts:501) | `registry.providers` | 없음 | 대칭 결여 |
| `registerAgentHarness` | process-global via registerAgentHarness() | **restoreRegisteredAgentHarnesses** (loader.ts:1805,1826,1840) | **대칭 존재** |
| `registerCompactionProvider` | process-global | **restoreRegisteredCompactionProviders** (loader.ts:1806,1827,1841) | **대칭 존재** |
| `registerMemoryEmbeddingProvider` | process-global | **restoreRegisteredMemoryEmbeddingProviders** (loader.ts:1807,1828,1842) | **대칭 존재** |
| `registerMemoryRuntime`/Prompt/Corpus/FlushPlan | process-global memory state | **restoreMemoryPluginState** (loader.ts:1829-1835, 1843-1849) | **대칭 존재** |

**핵심 관찰:**
1. registry 객체의 **배열 필드** (httpRoutes/services/commands/hooks/gatewayMethods/providers) 는 rollback 대상에서 일관되게 제외됨. loader 는 process-global 싱글톤 4종만 복구함.
2. manifest-registry 의 partial state 는 grid hint 와 달리 실제로는 관측되지 않음. parse 실패 시 continue 로 clean skip.
3. 런타임 plugin 타입별 `dispose()` 메소드 호출 일관성: runtime-channel.ts:275-315 에서 channel subscription 에 대한 dispose 는 존재하나, 플러그인 차원의 통합 dispose API 는 부재 (플러그인 전체 unload 개념 자체가 production 에 구현되지 않음 — uninstall.ts 는 config 수정용이며 runtime registry 를 건드리지 않음).

**자체 한계:**
- `src/plugins/plugin-graceful-init-failure.test.ts` 및 `loader.runtime-registry.test.ts` 내부 미확인 — 이들이 부분 등록 잔존을 기대/금지하는지 확인 필요.
- gateway 소비자 (src/gateway/**) 가 route/service 를 status 필터링하는지 — allowed_paths 외라 미확인.
- recordPluginError 의 다른 4개 호출 지점 중 register phase 외 (load/validation phase) 는 api 호출 이전이므로 본 FIND 영향 없음을 가정.

### clusterer (2026-04-18)

- **CAND-005 (single)**: FIND-plugins-lifecycle-001. register() throw 시
  httpRoutes/services/commands/hooks 부분 등록 rollback 부재 (P1).
- **Cross-domain 관찰**: 동 배치의 FIND-infra-process-error-boundary-003
  (emitGatewayRestart catch 의 sigusr1AuthorizedCount 누수) 와 "catch 블록
  부분 상태 롤백" 이라는 상위 추상 패턴을 공유함. 그러나 도메인/자료구조/
  실패 경로가 모두 상이 (registry 배열 vs 카운터; 플러그인 내부 throw vs
  process.kill EPERM) 하고 clusterer.md Step 3 의 "같은 인프라 축" 기준을
  충족하지 않아 epic 불가. 각각 single CAND 로 분리 (CAND-005, CAND-006)
  하고 cross_refs 로만 관련성 표시.

### error-boundary-auditor (2026-04-18)

**적용 카테고리:**
- [x] A. unhandledRejection / uncaughtException handler chain — 상위 process-level handler 가
      plugins 경계 밖 (src/infra/unhandled-rejections.ts) 이라 간접 탐색만.
- [x] B. Floating promise — 발견 2건 (FIND-001, FIND-002). async register 의 Promise 가
      `void register(...)` 또는 diagnostic-only 처리로 escape.
- [x] C. JSON.parse 미보호 — skipped (모든 JSON.parse 지점에 try/catch 또는 Result 기반 ok/error
      반환 확인: conversation-binding.ts:337, marketplace.ts:262+328, bundle-config-shared.ts:40,
      bundle-commands.ts:65, clawhub.ts:432, bundled-plugin-metadata.ts:64, manifest.ts:664 전부
      방어됨 — unconditional try/catch 존재).
- [x] D. AbortController / AbortSignal 전파 — skipped (runtime-channel.ts:297 의 abort listener
      는 once:true + disposed flag guard 로 safe. 누수 없음).
- [x] E. fs/network 동기 호출 — skipped (fs.readFileSync 가 hot path 가 아닌 manifest/config 경로.
      본 감사에서 error-boundary 가 아닌 perf 축. 에러는 모두 try 안).

**발견 FIND:**
- FIND-plugins-error-boundary-001 (P1, error-boundary-gap): bundled-capability-runtime.ts:300
  `void register(captured.api)` — async register 시 unhandled rejection + silent registration
  loss. webhooks 가 실제 async register 사용 → production trigger 확정.
- FIND-plugins-error-boundary-002 (P2, error-boundary-gap): loader.ts:1815-1823 — full loader 의
  async register 감지는 되어 있으나 Promise 는 diagnostic push 만 하고 `.catch()` 없이 floating.
- FIND-plugins-error-boundary-003 (P2, error-boundary-gap): install.ts:20-25,
  provider-runtime.runtime.ts:15-22, provider-discovery.ts:7-12, provider-auth-ref.ts:18-22 의
  `xxxRuntimePromise ??= import(...)` 패턴. 첫 import reject 시 rejected Promise 영구 캐시.
  프로세스 재시작 전 recovery 불가.

**R-3 Grep 결과 — 방어 경로 매핑 테이블:**

| 경로 | 방어 유무 | R-5 분류 |
|---|---|---|
| `try { JSON.parse(...) }` in conversation-binding.ts:332-364 | 존재 | unconditional |
| `try { JSON.parse(...) }` in marketplace.ts:261-265 | 존재 | unconditional |
| `try { JSON.parse(...) }` in bundle-config-shared.ts:39-49 | 존재 | unconditional |
| `try { JSON.parse(...) }` in bundle-commands.ts:64-73 | 존재 | unconditional |
| `try { JSON.parse(...) }` in clawhub.ts:430-437 | 존재 | unconditional |
| `try { JSON.parse(...) }` in bundled-plugin-metadata.ts:63-67 | 존재 | unconditional |
| `try { JSON.parse(...) }` in manifest.ts:663-673 | 존재 | unconditional |
| `void Promise.resolve(result).catch(() => undefined)` in setup-registry.ts:278 | 존재 (swallow) | unconditional |
| `void register(captured.api)` in bundled-capability-runtime.ts:300 | **부재** (sync-only try) | conditional for sync / **gap for async** |
| `const result = register(api); if (async) { diagnostic.push }` in loader.ts:1815-1823 | **부분** (diagnostic only) | conditional — Promise 부동 |
| `xxxRuntimePromise ??= import(...)` 4곳 | **부재** (reset 경로 없음) | conditional-edge (첫 실패 후 stuck) |
| `runtime-channel.ts:297 abort listener` | 존재 (once:true + disposed flag) | unconditional |

**핵심 관찰:**

1. **SDK contract vs runtime reality gap**: SDK 타입 `register: (api) => void` 은 sync 를
   의도하지만 TS 의 void 반환 타입은 async function 을 widening 으로 허용. `no-misused-promises`
   lint rule 미적용. 실제 bundled extension `extensions/webhooks/index.ts:10` 이 `async register`
   사용 중 → 타입 시스템 밖 gap.

2. **두 loader 경로 간 정책 불일치**: loader.ts:1815 (full/setup-runtime) 은 sync register +
   diagnostic only, loader.ts:2236 (cli-metadata) 은 `await register(api)`. 동일 plugin 이 두 경로
   모두 타지만 async 처리 방식 상반됨.

3. **`??=` lazy-load idiom 의 reliability 맹점**: 4곳 모두 동일 패턴으로 rejected promise 가
   프로세스 lifetime 동안 영구 캐시. `src/plugins/install-security-scan.ts:37-39` 는 다른 패턴
   (`return await import(...)`) 으로 매 호출 재시도 가능 — 같은 도메인 내에 안전 대안 존재.

4. **상위 try/catch 의 async rejection 비-보호**: bundled-capability-runtime.ts:298-470,
   loader.ts:1814-1862 양쪽 모두 try/catch 가 **sync throw 만** 잡음. Promise rejection 은
   async stack 으로 도달하므로 동기 try/catch 로 격리 불가. R-5 primary-path inversion 에 따라
   "이 crash 가 성립하려면 어떤 try 가 없어야 하는가?" → "Promise chain 에 .catch 가 없어야
   한다" 조건 자체가 현재 코드에 성립.

**자체 한계:**

- `src/infra/unhandled-rejections.ts` 의 정확한 handler 정책 (log-only / fatal / 선택적) 은
  allowed_paths 외. 이 정책이 fatal 이면 FIND-001/002 severity 상향 (P1 → P0, P2 → P1) 가능.
- dynamic import 의 Node 22 정확한 재시도 semantics — spec 상 evaluation failure 는 registry 에
  cache 되지 않지만 구현 디테일 미검증. FIND-003 의 P2 가 "실제 transient recovery 가능성" 가정에
  기댐.
- third-party plugin 에서 async register 사용 빈도 실측 데이터 없음 — bundled extension 만
  grep 했고 webhooks 한 건 매치. 실제 production 영향 정량화 제한.
- registry.httpRoutes 의 consumer snapshot vs live read 여부 — gateway/** 확인 필요 (allowed_paths 외).
  이 세부에 따라 async register resolve 시 "silent late-insert" 가 기능 부재 vs 기능 작동 여부 결정.

## 다음 페르소나를 위한 힌트

### concurrency-auditor / gatekeeper

- FIND-001/002/003 의 severity 재평가에 src/infra/unhandled-rejections.ts 정책 확인 필수.
- FIND-001 과 FIND-002 는 root cause axis 유사 (async register 의 floating Promise) — clusterer
  가 epic 으로 묶을지, 또는 별도 CAND 로 분리할지 판단. bundled-capability-runtime 경로 vs
  full loader 경로가 **다른 실행 경로** 이므로 각각 별도 SOL 이 필요할 가능성 높음.
- FIND-003 은 독립 축 (dynamic import cache). 다른 FIND 와 epic 근거 약함.
- webhooks async register 가 실제 production 에서 reject 하는 경로가 있는지 (config I/O 에러 등)
  재현 테스트 작성 시 구체화 필요.

### error-boundary-auditor (2026-04-19, 재실행)

**배경**: 2026-04-18 세션의 FIND 001/002/003 이 YAML frontmatter parse error (R-6 위반: 콜론 포함 문자열, backtick, 이모지 등) 로 전원 reject 됨. 본 세션은 **내용은 동일 gap, YAML 규율 복원** 으로 재제출.

**추가 확보된 반증/증거 (전 세션 미확인 부분)**:

1. **src/infra/unhandled-rejections.ts:339-379 글로벌 handler 로직 직접 Read**:
   - `installUnhandledRejectionHandler` 가 `process.on("unhandledRejection", ...)` 로 리스너 설치.
   - reason classification 순서: `isUnhandledRejectionHandled` -> `isAbortError` (warn-only) -> `isFatalError` (exit) -> `isConfigError` (exit) -> `isTransientUnhandledRejectionError` (warn-only) -> **default branch: `process.exit(1)`**.
   - 즉 plugin 의 일반 Error (fatal/config/transient 분류 미해당) 은 default branch 에서 **확정적 process crash**.
   - 이 발견으로 FIND-001 severity **P1 -> P0 상향** (crash 경로 production-grade fatal 확인).

2. **FIND-002 severity 는 P2 -> P1 로 상향**: 동일 unhandled-rejections 경로 공유. full loader 경로는 diagnostic push 가 있어 silent 보다 낫지만 Promise rejection 이 여전히 fatal 로 가는 사실은 동일.

3. **discovery.ts 의 fs.statSync race 후보 (line 729-738)**: `fs.existsSync(resolved)` 후 `fs.statSync(resolved)` 가 try 없이 이어진다. 파일이 두 호출 사이에 삭제되면 stat 이 throw 하여 discoverFromPath 를 중단. 본 세션은 error-boundary 셀 scope 내이나 **매우 edge 조건** (빈도 낮음) 이고 memory-leak/lifecycle 영향 없음 → FIND 로 승격 안 함. lifecycle-auditor 다음 세션 후보로만 기록.

**발견 FIND (재제출)**:
- FIND-plugins-error-boundary-001 (P0, error-boundary-gap): bundled-capability-runtime.ts:301 `void register(captured.api)` - async Promise 가 try/catch 밖으로 escape, unhandled-rejections global handler 가 process.exit(1) 확정. severity **P1 → P0 상향**.
- FIND-plugins-error-boundary-002 (P1, error-boundary-gap): loader.ts:1753-1762 - async register 감지는 하지만 Promise 는 diagnostic push 뿐 .catch 없음. 동일 crash 경로. severity **P2 → P1 상향**.
- FIND-plugins-error-boundary-003 (P2, error-boundary-gap): install.ts/provider-runtime.runtime.ts/provider-discovery.ts/provider-auth-ref.ts 4곳의 `??= import(...)` lazy-load idiom - 첫 import reject 시 rejected Promise 영구 캐시. 프로세스 재시작 전 recovery 불가. severity P2 유지.

**R-3 Grep 결과 — async register/Promise escape 경로 방어 매핑 (갱신)**:

| 경로 | 방어 유무 | R-5 분류 |
|---|---|---|
| `void register(captured.api)` in bundled-capability-runtime.ts:301 | **부재** (sync-only try) | does-not-cover-async → global handler 에서 process.exit(1) |
| `const result = register(api); if (async) diagnostic.push` in loader.ts:1753-1762 | **부분** (diagnostic only, .catch 없음) | does-not-cover-async → global handler 에서 process.exit(1) |
| `void Promise.resolve(result).catch(() => undefined)` in setup-registry.ts:272-279 ignoreAsyncSetupRegisterResult | **존재** (명시 swallow) | unconditional swallow |
| `await register(api)` in loader.ts:2175 (cli-metadata) | **존재** (try/catch 통합) | unconditional |
| `xxxRuntimePromise ??= import(...)` 4곳 | **부재** (reset 경로 없음) | conditional-edge (첫 실패 후 stuck) |
| `void notifyPluginConversationBindingResolved(params).catch(...)` in conversation-binding.ts:929 | **존재** (log.warn catch) | unconditional swallow |
| `src/infra/unhandled-rejections.ts:345-379` 글로벌 handler | **존재** | unconditional, 일반 Error → process.exit(1) |

**핵심 결론**:

1. **두 경로 (bundled-capability-runtime 와 full loader) 모두 async Promise escape → global handler 의 default branch → process.exit(1) 확정 경로**. severity 상향 근거 확립.
2. **setup-registry.ts 의 `ignoreAsyncSetupRegisterResult` 가 같은 파일 내 안전 대안으로 존재**. 이 helper 를 양쪽 경로가 채택했다면 전부 방어됨. 공유 미채택은 코드 일관성 결함.
3. **설계 관점 — SDK 타입 `register: (api) => void` 가 async function 을 막지 않는다는 사실이 근본 취약 지점**. no-misused-promises lint 또는 signature 변경 없이는 실수 재발 우려.

**clusterer 를 위한 힌트**:
- FIND-001, FIND-002 는 같은 root axis (async register Promise floating + 글로벌 handler fatal) 이지만 **다른 실행 경로** (bundled-capability-runtime vs full loader). 별도 CAND 로 분리하되 cross_refs 로 연결 가능. 공통 SOL 후보: setup-registry.ts:272-279 의 helper 를 공유화.
- FIND-003 은 독립 축 (dynamic import cache). 다른 FIND 와 epic 불가.
- FIND-plugins-lifecycle-001 (register throw rollback 부재, CAND-005) 와 FIND-001/002 의 관계: **rollback 부재 축과 별개**. rollback 은 sync throw 를 catch 에서 잡은 후 부분 등록이 남는 문제, 본 FIND 는 애초에 catch 가 trigger 되지 않는 경로. 별도 CAND 유지.

**자체 한계**:
- src/infra/unhandled-rejections.ts 의 installUnhandledRejectionHandler 가 gateway process 에서 실제 호출되는 call site 는 allowed_paths 외 (src/index.ts, src/cli/run-main.ts). 본 세션은 export 존재 + 로직 확인만. 만약 특정 entrypoint 에서 install 누락이면 FIND-001/002 crash 영향 하향.
- third-party plugin 에서 async register 사용 빈도 정량 데이터 없음.
- registry.httpRoutes 의 consumer snapshot vs live read 여부 (gateway/** 은 scope 외). 이에 따라 late-insert 가 silent 기능 부재로 귀결되는지 판별.
