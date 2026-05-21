---
id: FIND-plugins-lifecycle-002
cell: plugins-lifecycle
title: register throw 시 hostedMediaResolvers/gatewayDiscoveryServices 잔존 (restore
  미커버)
file: src/plugins/loader.ts
line_range: 429-468
evidence: "```ts\nfunction restorePluginRegistry(registry: PluginRegistry, snapshot:\
  \ PluginRegistrySnapshot): void {\n  registry.tools = snapshot.arrays.tools;\n \
  \ registry.hooks = snapshot.arrays.hooks;\n  registry.typedHooks = snapshot.arrays.typedHooks;\n\
  \  registry.channels = snapshot.arrays.channels;\n  registry.channelSetups = snapshot.arrays.channelSetups;\n\
  \  registry.providers = snapshot.arrays.providers;\n  registry.modelCatalogProviders\
  \ = snapshot.arrays.modelCatalogProviders;\n  registry.cliBackends = snapshot.arrays.cliBackends;\n\
  \  registry.textTransforms = snapshot.arrays.textTransforms;\n  registry.speechProviders\
  \ = snapshot.arrays.speechProviders;\n  registry.realtimeTranscriptionProviders\
  \ = snapshot.arrays.realtimeTranscriptionProviders;\n  registry.realtimeVoiceProviders\
  \ = snapshot.arrays.realtimeVoiceProviders;\n  registry.mediaUnderstandingProviders\
  \ = snapshot.arrays.mediaUnderstandingProviders;\n  registry.imageGenerationProviders\
  \ = snapshot.arrays.imageGenerationProviders;\n  registry.videoGenerationProviders\
  \ = snapshot.arrays.videoGenerationProviders;\n  registry.musicGenerationProviders\
  \ = snapshot.arrays.musicGenerationProviders;\n  registry.webFetchProviders = snapshot.arrays.webFetchProviders;\n\
  \  registry.webSearchProviders = snapshot.arrays.webSearchProviders;\n  registry.migrationProviders\
  \ = snapshot.arrays.migrationProviders;\n  registry.codexAppServerExtensionFactories\
  \ = snapshot.arrays.codexAppServerExtensionFactories;\n  registry.agentToolResultMiddlewares\
  \ = snapshot.arrays.agentToolResultMiddlewares;\n  registry.memoryEmbeddingProviders\
  \ = snapshot.arrays.memoryEmbeddingProviders;\n  registry.agentHarnesses = snapshot.arrays.agentHarnesses;\n\
  \  registry.httpRoutes = snapshot.arrays.httpRoutes;\n  registry.cliRegistrars =\
  \ snapshot.arrays.cliRegistrars;\n  registry.reloads = snapshot.arrays.reloads;\n\
  \  registry.nodeHostCommands = snapshot.arrays.nodeHostCommands;\n  registry.nodeInvokePolicies\
  \ = snapshot.arrays.nodeInvokePolicies;\n  registry.securityAuditCollectors = snapshot.arrays.securityAuditCollectors;\n\
  \  registry.services = snapshot.arrays.services;\n  registry.commands = snapshot.arrays.commands;\n\
  \  registry.sessionActions = snapshot.arrays.sessionActions;\n  registry.conversationBindingResolvedHandlers\
  \ =\n    snapshot.arrays.conversationBindingResolvedHandlers;\n  registry.diagnostics\
  \ = snapshot.arrays.diagnostics;\n  registry.gatewayHandlers = snapshot.gatewayHandlers;\n\
  \  registry.gatewayMethodDescriptors = snapshot.gatewayMethodDescriptors;\n  registry.coreGatewayMethodNames\
  \ = snapshot.coreGatewayMethodNames;\n}\n```\n"
symptom_type: lifecycle-gap
problem: 'loader.ts 의 plugin register 실패 rollback 은 2026-05 시점 `snapshotPluginRegistry`
  /

  `restorePluginRegistry` 페어로 registry 배열을 복원하도록 강화됐다 (1차 감사 FIND-001

  의 메커니즘은 이로 해소). 그러나 `restorePluginRegistry` 가 복원하는 배열 목록에

  `registry.hostedMediaResolvers` 와 `registry.gatewayDiscoveryServices` 두 배열이

  빠져 있다. 이 둘은 plugin register API (`api.registerHostedMediaResolver`,

  `api.registerGatewayDiscoveryService`) 가 호출 즉시 push 하는 registry 배열이다.

  플러그인 register() 가 이 두 메서드 중 하나를 호출한 뒤 throw 하면, catch 블록의

  `restorePluginRegistry` 가 다른 ~35개 배열은 snapshot 으로 되돌리지만 이 두 배열의

  push 된 엔트리는 제거하지 못한다. 결과적으로 status=''error'' 인 실패 플러그인의

  hosted media resolver / gateway discovery service 가 registry 에 잔존하여

  소비자(`web-media.ts`, `server-discovery-runtime.ts`)에 정상 등록물로 노출된다.

  '
mechanism: "1. loader.ts:2408 `const registrySnapshot = snapshotPluginRegistry(registry)`\
  \ 로 register\n   직전 registry 배열들을 얕은 복사. snapshotPluginRegistry (loader.ts:385-427)\
  \ 가\n   복사하는 배열 목록에 hostedMediaResolvers / gatewayDiscoveryServices 없음.\n2. loader.ts:2423\
  \ `runPluginRegisterSync(register, api)` 호출. plugin 의 register(api)\n   가 full registrationMode\
  \ 의 capabilityHandlers (registry.ts:2511-2557) 를 통해\n   `api.registerHostedMediaResolver(resolver)`\
  \ 또는\n   `api.registerGatewayDiscoveryService({id, advertise})` 를 호출.\n3. registry.ts:862\
  \ `(registry.hostedMediaResolvers ??= []).push({pluginId, resolver, ...})`\n   또는\
  \ registry.ts:1590 `registry.gatewayDiscoveryServices.push({pluginId, service, ...})`\n\
  \   로 registry 배열에 즉시 append.\n4. 이후 plugin register 본체에서 throw (config 검증 실패, 의존\
  \ 리소스 부재, 다음\n   register* 호출 실패 등).\n5. loader.ts:2439 catch 블록 진입. `rollbackPluginGlobalSideEffects(record.id)`\n\
  \   (process-global side-effect: commands/interactive/contextEngines/hooks) +\n\
  \   `restorePluginRegistry(registry, registrySnapshot)` 실행.\n6. restorePluginRegistry\
  \ (loader.ts:429-468) 는 tools/hooks/httpRoutes/services/commands\n   등 ~35개 배열을\
  \ snapshot 으로 교체하지만 hostedMediaResolvers /\n   gatewayDiscoveryServices 는 대입문 자체가\
  \ 없음 → step 3 에서 push 된 엔트리 잔존.\n7. recordPluginError (loader-records.ts:124-128)\
  \ 가 record.status='error' 설정 후\n   registry.plugins.push(record).\n8. 소비자: web-media.ts:79\
  \ `for (const entry of registry?.hostedMediaResolvers ?? [])`\n   는 status 필터 없이\
  \ 실패 플러그인의 resolver 를 호출. server-discovery-runtime.ts:63\n   `for (const entry of\
  \ params.gatewayDiscoveryServices ?? [])` 는 실패 플러그인의\n   discovery service 의 `entry.service.advertise(context)`\
  \ 를 gateway 시작 시 실행.\n"
root_cause_chain:
- why: register throw 시 왜 hostedMediaResolvers / gatewayDiscoveryServices 엔트리가 살아남는가?
  because: catch 블록의 restorePluginRegistry 가 복원하는 registry 배열 목록 (loader.ts:430-464)
    에 이 두 필드에 대한 대입문이 누락되어 있다. snapshotPluginRegistry (loader.ts:385-426) 도 이 두 배열을
    복사하지 않으므로 애초에 복원 대상이 아니다.
  evidence_ref: src/plugins/loader.ts:429-468 (restorePluginRegistry 본문 — 두 필드 부재),
    src/plugins/loader.ts:385-427 (snapshotPluginRegistry 본문 — 두 필드 부재)
- why: 왜 이 두 배열만 snapshot/restore 목록에서 빠졌는가?
  because: snapshotPluginRegistry/restorePluginRegistry 는 PluginRegistry 타입의 배열 필드를
    명시적 화이트리스트로 열거하는 구조이고, hostedMediaResolvers (registry-types.ts:450, optional)
    와 gatewayDiscoveryServices (registry-types.ts:457) 가 이 화이트리스트에 추가되지 않은 채 누락됨.
    타입 시스템이 누락을 강제하지 못함 (Partial 열거).
  evidence_ref: src/plugins/registry-types.ts:450 (hostedMediaResolvers 필드), src/plugins/registry-types.ts:457
    (gatewayDiscoveryServices 필드)
- why: 왜 plugin register 가 이 메서드들을 호출한 뒤 throw 하면 실제로 push 가 일어나는가?
  because: registerHostedMediaResolver (registry.ts:849-869) 와 registerGatewayDiscoveryService
    (registry.ts:1568-1596) 는 deferred/staging 없이 호출 즉시 registry 배열에 push 하며, full
    registrationMode 의 capabilityHandlers 로 plugin api 에 직접 노출된다 (registry.ts:2518-2519,
    2556-2557). register() 콜백이 부분 진행 후 throw 하는 시나리오에서 push 가 그대로 반영.
  evidence_ref: src/plugins/registry.ts:862 (registry.hostedMediaResolvers.push),
    src/plugins/registry.ts:1590 (registry.gatewayDiscoveryServices.push)
- why: 왜 잔존 엔트리가 실제 동작에 영향을 주는가?
  because: 두 소비자 모두 plugin status 필터 없이 배열 전체를 순회한다. web-media.ts:79 는 hosted media
    URL 해석 시 실패 플러그인의 resolver 를 호출 (잘못된/미초기화 resolver 가 응답). server-discovery-runtime.ts:63
    는 gateway 시작 시 실패 플러그인의 discovery service.advertise() 를 실행 (미초기화 service 가 LAN
    광고 시도).
  evidence_ref: src/media/web-media.ts:79-92 (hostedMediaResolvers status 무필터 순회),
    src/gateway/server-discovery-runtime.ts:63-81 (gatewayDiscoveryServices status
    무필터 순회 + service.advertise 호출)
impact_hypothesis: wrong-output
impact_detail: "정성: 플러그인 register() 가 api.registerHostedMediaResolver 또는\napi.registerGatewayDiscoveryService\
  \ 를 호출한 뒤 같은 register() 본체에서 throw 할 때\n발생. 영향:\n- 부분 등록된 hosted media resolver\
  \ 가 registry.hostedMediaResolvers 에 잔존 →\n  resolveHostedPluginMediaUrl 이 미초기화 상태의\
  \ resolver 를 호출. resolver 내부\n  closure 가 미완성 plugin state 를 참조하면 잘못된 media URL\
  \ 반환 또는 throw\n  (web-media.ts:85 가 throw 는 catch 하지만, 잘못된 non-empty 문자열 반환은 그대로\n\
  \  채택 — 다른 정상 플러그인의 resolver 를 가로채는 wrong-output).\n- 부분 등록된 gateway discovery service\
  \ 가 registry.gatewayDiscoveryServices 에 잔존\n  → gateway 시작 시 server-discovery-runtime\
  \ 이 실패 플러그인의 service.advertise() 를\n  호출. advertise 가 미초기화 state 에 의존하면 LAN mDNS\
  \ 광고가 잘못된 정보로 나가거나\n  advertise 내부 throw (Promise.resolve().then 체인이라 unhandled\
  \ rejection 가능성).\n빈도: register() 중간 throw 는 정상 플러그인에서는 드문 경로지만, plugin config schema\n\
  검증 실패, 의존 파일/포트 부재, 다단계 register* 호출 중 N+1번째 실패 (예: discovery\nservice 등록 후 추가 provider\
  \ 등록 실패) 등 런타임 환경 의존 오류로 발생 가능. 특히\nhosted media / gateway discovery 를 등록하는 플러그인은\
  \ 통상 추가 초기화 로직 (네트워크\n바인딩, 외부 storage 연결) 을 동반하므로 register 후반부 throw 확률이 상대적으로 높다.\n"
severity: P1
counter_evidence:
  path: src/plugins/loader.ts
  line: 2439-2462
  reason: "R-3 Grep 수행 (대응 cleanup 경로 탐색):\n(1) `rg -n \"(unregister|dispose|teardown|unload|cleanup|rollback).*(hostedMediaResolver|gatewayDiscoveryService|GatewayDiscovery|HostedMedia)\"\
    \ src/plugins/`\n    → production code 매치 0건. unregister/dispose 계열 cleanup 함수\
    \ 미존재.\n(2) `rg -n \"registry\\.(hostedMediaResolvers|gatewayDiscoveryServices)\\\
    .(filter|splice|pop|shift)|registry\\.(hostedMediaResolvers|gatewayDiscoveryServices)\\\
    s*=\" src/plugins/`\n    → production 매치 0건. 유일한 매치는 src/plugins/loader.test.ts:4445\n\
    \    (test 코드, duplicate 등록 검증용 .filter — cleanup 아님).\n분류 (R-5):\n- rollbackPluginGlobalSideEffects\
    \ (registry.ts:2959-2987): unconditional 실행되나\n  대상은 deactivatePluginSideEffectGuards\
    \ / clearPluginCommandsForPlugin /\n  clearPluginInteractiveHandlersForPlugin\
    \ / clearContextEnginesForOwner /\n  pluginHookRollback 5종 process-global side-effect\
    \ 만. hostedMediaResolvers /\n  gatewayDiscoveryServices registry 배열 미포함.\n- restorePluginRegistry\
    \ (loader.ts:429-468): unconditional 실행 (catch 블록 line 2441).\n  그러나 복원 대상 화이트리스트\
    \ (line 430-464) 에 hostedMediaResolvers /\n  gatewayDiscoveryServices 대입문 부재 —\
    \ 직접 읽어 확인. 이 두 배열에 한해\n  복원 경로가 unconditional 하게 존재하지 않음.\n- snapshotPluginRegistry\
    \ (loader.ts:385-427): 이 두 배열을 snapshot 하지도 않음.\n즉 unconditional cleanup 경로가 다른\
    \ ~35개 배열은 처리하지만 이 두 배열은 처리하지\n않음 → R-5 의 'unconditional 경로 존재 시 FIND 금지' 에 해당하지\
    \ 않음. FIND 성립.\n\nproduction trigger 실재 확인:\n- register 실패 catch 경로 (loader.ts:2439):\
    \ plugin register() throw 시 무조건 진입하는\n  production hot-path (validateOnly/snapshot\
    \ 아닌 full activation 로드에서 도달).\n- api.registerHostedMediaResolver / api.registerGatewayDiscoveryService:\
    \ full\n  registrationMode 의 capabilityHandlers (registry.ts:2511-2557) 로 모든 일반\
    \ 플러그인\n  register(api) 에 노출 — 실제 plugin 이 호출 가능한 production API.\n- 소비자 production\
    \ 경로 실재: web-media.ts:79 (resolveHostedPluginMediaUrl, hosted\n  media URL 해석\
    \ 시 호출), server-discovery-runtime.ts:63 (gateway 시작 LAN discovery\n  활성화 시 호출).\
    \ 둘 다 getActivePluginRegistry / params.pluginRegistry 를 통해\n  활성 registry 를 소비하는\
    \ production 경로.\n따라서 코드상 메커니즘 + production trigger caller + production 소비자 모두\
    \ 실재.\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-21'
related_tests:
- src/plugins/loader.test.ts
- src/plugins/plugin-graceful-init-failure.test.ts
---
# register throw 시 hostedMediaResolvers / gatewayDiscoveryServices 부분 등록 잔존

## 문제

`src/plugins/loader.ts` 의 plugin register 실패 rollback 은 1차 감사 (2026-04) 이후
`snapshotPluginRegistry` (loader.ts:385-427) / `restorePluginRegistry` (loader.ts:429-468)
페어로 강화되었다. register 직전 registry 배열들을 얕은 복사로 snapshot 하고, register()
throw 시 catch 블록에서 snapshot 으로 되돌린다. 이로써 1차 감사 FIND-plugins-lifecycle-001
이 지적한 httpRoutes/services/commands/hooks 부분 등록 잔존은 해소되었다 (해당 FIND 는
rejected).

그러나 `restorePluginRegistry` 가 복원하는 배열 화이트리스트 (loader.ts:430-464) 에
`registry.hostedMediaResolvers` 와 `registry.gatewayDiscoveryServices` **두 배열의 대입문이
누락**되어 있다. 두 배열은 plugin register API 가 호출 즉시 push 하는 registry 배열이다:

- `api.registerHostedMediaResolver(resolver)` → `registry.ts:862`
  `(registry.hostedMediaResolvers ??= []).push(...)`
- `api.registerGatewayDiscoveryService(service)` → `registry.ts:1590`
  `registry.gatewayDiscoveryServices.push(...)`

플러그인 register() 가 이 두 메서드 중 하나를 호출한 뒤 throw 하면, catch 블록의
`restorePluginRegistry` 가 tools/hooks/httpRoutes/services/commands 등 ~35개 배열은
snapshot 으로 되돌리지만 이 두 배열에 push 된 엔트리는 그대로 잔존한다. 실패 플러그인
(`record.status='error'`) 의 hosted media resolver / gateway discovery service 가
registry 에 정상 등록물처럼 남는다.

## 발현 메커니즘

1. `loader.ts:2408` `const registrySnapshot = snapshotPluginRegistry(registry)`.
   `snapshotPluginRegistry` (loader.ts:385-427) 가 복사하는 배열 목록에
   `hostedMediaResolvers` / `gatewayDiscoveryServices` 없음.
2. `loader.ts:2423` `runPluginRegisterSync(register, api)`. plugin register(api) 가 full
   `registrationMode` 의 `capabilityHandlers` (registry.ts:2511-2557) 를 통해
   `api.registerHostedMediaResolver(resolver)` (registry.ts:2518-2519) 또는
   `api.registerGatewayDiscoveryService({id, advertise})` (registry.ts:2556-2557) 호출.
3. `registry.ts:862` `(registry.hostedMediaResolvers ??= []).push({pluginId, pluginName,
   resolver, source, rootDir})` 또는 `registry.ts:1590` `registry.gatewayDiscoveryServices
   .push({pluginId, pluginName, service, source, rootDir})` 로 registry 배열에 즉시 append.
   (gateway discovery 의 경우 registry.ts:1589 `record.gatewayDiscoveryServiceIds.push(id)`
   도 함께 수행.)
4. 이후 plugin register 본체에서 throw — config 검증 실패, 의존 리소스(파일/포트) 부재,
   다단계 register* 호출 중 N+1번째 실패 등.
5. `loader.ts:2439` catch 블록 진입.
   - `rollbackPluginGlobalSideEffects(record.id)` (registry.ts:2959-2987): process-global
     side-effect 5종 (commands, interactive handlers, context engines, hooks,
     side-effect guards) 복구. registry 배열은 대상 아님.
   - `restorePluginRegistry(registry, registrySnapshot)` (loader.ts:429-468): registry
     배열 ~35개를 snapshot 으로 교체.
6. `restorePluginRegistry` 본문 (loader.ts:430-464) 에는 `registry.hostedMediaResolvers = ...`
   / `registry.gatewayDiscoveryServices = ...` 대입문이 존재하지 않음 → step 3 에서 push 된
   엔트리 그대로 잔존.
7. `recordPluginError` (loader-records.ts:124-128) 가 `record.status='error'` 설정 후
   `registry.plugins.push(record)`.
8. 소비자가 실패 플러그인의 등록물을 정상으로 취급:
   - `src/media/web-media.ts:79` `for (const entry of registry?.hostedMediaResolvers ?? [])`
     — status 필터 없이 실패 플러그인의 `entry.resolver(mediaUrl)` 호출.
   - `src/gateway/server-discovery-runtime.ts:63` `for (const entry of
     params.gatewayDiscoveryServices ?? [])` — gateway 시작 시 실패 플러그인의
     `entry.service.advertise(context)` 실행.

## 근본 원인 분석

1. **restorePluginRegistry 화이트리스트 누락** (`loader.ts:429-468`): 이 함수는 PluginRegistry
   의 배열 필드를 명시적 대입문으로 하나씩 열거한다 (line 430-464). `httpRoutes`,
   `services`, `commands`, `agentHarnesses`, `migrationProviders` 등은 포함되나
   `hostedMediaResolvers` / `gatewayDiscoveryServices` 는 빠져 있다. `snapshotPluginRegistry`
   (loader.ts:385-427) 의 복사 목록에서도 동일하게 누락 — 애초에 snapshot 대상이 아니다.

2. **타입 시스템이 누락을 강제하지 못함**: `snapshotPluginRegistry` 의 반환 타입
   `PluginRegistrySnapshot` 은 `PluginRegistry` 의 일부 필드만 골라 정의한 별도 타입이다
   (loader.ts:340 부근). PluginRegistry 에 새 배열 필드가 추가되어도 (hostedMediaResolvers
   는 `registry-types.ts:450` 에서 optional, gatewayDiscoveryServices 는 `:457`) snapshot
   타입이 그것을 누락해도 컴파일 에러가 나지 않는다. 화이트리스트와 실제 registry 필드
   집합의 동기화가 수동이며 drift 했다.

3. **register API 의 즉시 push 설계** (`registry.ts:862, 1590`): `registerHostedMediaResolver`
   (registry.ts:849-869), `registerGatewayDiscoveryService` (registry.ts:1568-1596) 는
   deferred/staging 영역 없이 호출 즉시 registry 배열에 push. full registrationMode 의
   `capabilityHandlers` (registry.ts:2511-2557) 로 모든 일반 플러그인 register(api) 에
   노출되므로, register() 의 "부분 진행" 상태가 곧바로 registry 에 반영된다.

4. **소비자 측 status post-filter 부재** (`web-media.ts:79`, `server-discovery-runtime.ts:63`):
   두 소비자 모두 배열 전체를 순회하며 `record.status === 'error'` 인 플러그인의 엔트리를
   걸러내지 않는다. 따라서 실패 플러그인의 잔존 엔트리가 정상 등록물과 구분 없이 동작에
   참여한다.

## 영향

- **impact_hypothesis**: wrong-output (부분 등록 resolver/discovery service 가 정상인 것처럼
  소비자에 노출되어 잘못된 동작/응답).
- **재현 시나리오 (hosted media)**:
  1. 테스트 플러그인: `register(api) { api.registerHostedMediaResolver(() => "https://x/");
     throw new Error("init failed"); }`.
  2. `loadOpenClawPlugins` (full activation 모드) 실행.
  3. 결과: `registry.plugins.find(p=>p.id==='test').status === 'error'` 이지만
     `registry.hostedMediaResolvers.some(r=>r.pluginId==='test')` → true.
  4. `resolveHostedPluginMediaUrl` 호출 시 실패 플러그인의 resolver 가 invoked.
- **재현 시나리오 (gateway discovery)**:
  1. 테스트 플러그인: `register(api) { api.registerGatewayDiscoveryService({id:"d",
     advertise(){}}); throw new Error("init failed"); }`.
  2. `loadOpenClawPlugins` 실행.
  3. 결과: `registry.gatewayDiscoveryServices.some(e=>e.pluginId==='test')` → true.
  4. gateway 시작 시 server-discovery-runtime 이 `entry.service.advertise()` 호출.
- **빈도**: register() 중간 throw 는 드문 경로지만, hosted media / gateway discovery 를
  등록하는 플러그인은 통상 네트워크 바인딩·외부 storage 연결 등 추가 초기화를 동반하므로
  register 후반부 throw 확률이 상대적으로 높다. config schema 검증 실패, 포트/파일 부재,
  다단계 register* 중 후속 호출 실패 등으로 프로덕션에서 trigger 가능.
- **P1 근거**: 데이터 손실/크래시는 아니나, 1차 감사 FIND-001 과 동일한 부분 등록 잔존
  메커니즘이 `restorePluginRegistry` 의 fix 가 커버하지 못한 두 배열에 그대로 잔존.
  실패 플러그인의 미초기화 resolver/discovery service 가 production 소비자에서 실행되어
  silent wrong-output (잘못된 media URL 가로채기) 또는 미초기화 state 의존 advertise
  실행을 유발. server-discovery-runtime 의 `Promise.resolve().then(advertise)` 체인은
  advertise throw 시 처리 경로에 따라 unhandled rejection 위험.

## 반증 탐색

**R-3 Grep 명령 + 결과:**

1. `rg -n "(unregister|dispose|teardown|unload|cleanup|rollback).*(hostedMediaResolver|gatewayDiscoveryService|GatewayDiscovery|HostedMedia)" src/plugins/`
   → production code 매치 0건. unregister/dispose/cleanup 계열 함수 미존재.
2. `rg -n "registry\.(hostedMediaResolvers|gatewayDiscoveryServices)\.(filter|splice|pop|shift)|registry\.(hostedMediaResolvers|gatewayDiscoveryServices)\s*=" src/plugins/`
   → production 매치 0건. 유일한 매치는 `src/plugins/loader.test.ts:4445` — duplicate
   등록 검증용 `.filter`, cleanup 아닌 test assertion.

**R-5 실행 조건 분류:**

| 경로 | 실행 조건 | 대상 | 두 배열 커버? |
|---|---|---|---|
| `rollbackPluginGlobalSideEffects` (registry.ts:2959) | unconditional (catch line 2440) | commands / interactive / contextEngines / hooks / side-effect guards (process-global) | No |
| `restorePluginRegistry` (loader.ts:429) | unconditional (catch line 2441) | registry 배열 화이트리스트 ~35개 | **No — 화이트리스트에 부재** |
| `snapshotPluginRegistry` (loader.ts:385) | unconditional (register 전 line 2408) | 위와 동일 화이트리스트 | No |

→ unconditional cleanup 경로가 다른 ~35개 배열은 처리하지만 `hostedMediaResolvers` /
`gatewayDiscoveryServices` 두 배열에 한해서는 처리 경로가 부재. R-5 의 'unconditional
경로 존재 시 FIND 금지' 조건에 해당하지 않음 → FIND 성립.

**production trigger 실재:**

- register 실패 catch 경로 (loader.ts:2439) 는 full activation 로드에서 plugin register()
  throw 시 무조건 진입하는 production hot-path (validateOnly / snapshot 로드 제외).
- `api.registerHostedMediaResolver` / `api.registerGatewayDiscoveryService` 는 full
  registrationMode 의 capabilityHandlers (registry.ts:2511-2557) 로 모든 일반 플러그인
  register(api) 에 노출 — 실제 호출 가능한 production API.
- 소비자: `web-media.ts:79` (resolveHostedPluginMediaUrl), `server-discovery-runtime.ts:63`
  (gateway 시작 LAN discovery 활성화) 모두 활성 registry 를 소비하는 production 경로.

**추가 반증 탐색:**

- **숨은 상위 try/catch**: loader 의 plugin 로드 루프는 plugin 별 register 를 개별
  try/catch (loader.ts:2419-2471) 로 격리. 한 플러그인 register throw 시 다음 플러그인으로
  넘어가며 전체 registry reset 경로 없음. `maybeThrowOnPluginLoadError` (loader.ts:2499)
  는 throw 여부만 결정.
- **cli-metadata register 경로** (loader.ts:2894-2916): 동일 `snapshotPluginRegistry`/
  `restorePluginRegistry` 페어 사용하나 이 경로는 `registerCli` 만 노출 (handlers 가
  registerCli 단일) — `registerHostedMediaResolver`/`registerGatewayDiscoveryService` 미노출
  이므로 이 경로에서는 두 배열에 push 가 일어나지 않음. 결함은 full register 경로
  (loader.ts:2402-2462) 에 한정.
- **기존 테스트 커버리지**: `loader.test.ts:4438-4457` 의 gateway discovery service 테스트는
  **duplicate 등록** (동일 id 를 두 플러그인이 등록) 만 검증. register-throw 후 rollback
  은 커버하지 않음. `plugin-graceful-init-failure.test.ts` 의 내부는 본 세션에서 미확인
  (Self-check 에 기재).

## Self-check

### 내가 확실한 근거

- `loader.ts:429-468` `restorePluginRegistry` 본문을 직접 읽음. `registry.tools` ~
  `registry.coreGatewayMethodNames` 까지 대입문을 전수 확인 — `registry.hostedMediaResolvers`,
  `registry.gatewayDiscoveryServices` 대입문 없음.
- `loader.ts:385-427` `snapshotPluginRegistry` 본문 직접 확인 — 두 배열 미복사.
- `registry.ts:862` `(registry.hostedMediaResolvers ??= []).push(...)`,
  `registry.ts:1590` `registry.gatewayDiscoveryServices.push(...)` 직접 확인.
- `registry.ts:2518-2519, 2556-2557` 에서 두 메서드가 full registrationMode
  capabilityHandlers 로 plugin api 에 노출됨을 확인.
- `web-media.ts:79-92`, `server-discovery-runtime.ts:63-81` 소비자가 status 필터 없이
  배열 순회함을 직접 확인.
- R-3 Grep: 두 배열에 대한 production unregister/cleanup/element-removal 경로 부재 확인.

### 내가 한 가정

- plugin register() 가 `api.registerHostedMediaResolver` / `api.registerGatewayDiscoveryService`
  호출 후 throw 하는 시나리오가 프로덕션에서 발생한다 — 정량화 못 함. hosted media /
  discovery 등록 플러그인이 추가 초기화 동반으로 register 후반부 throw 가능성이 있다는
  것은 정성 추론.
- `web-media.ts` 의 resolver 가 미초기화 closure state 를 참조하면 wrong-output 이 된다 —
  resolver 구현이 plugin state 에 의존한다는 일반적 가정. resolver 가 순수 함수면 영향 없음.

### 확인 안 한 것 중 영향 가능성

- `src/plugins/plugin-graceful-init-failure.test.ts` 본문: 이 시나리오 (register 후
  registry 배열 잔존) 를 이미 검증/기대하는지 미확인. 만약 두 배열 잔존을 design 으로
  고정했다면 severity 재평가 필요.
- `server-discovery-runtime.ts` 의 `advertise()` throw 시 `Promise.resolve().then` 체인
  (line 80-) 의 후속 처리: unhandled rejection 으로 이어지는지 vs catch 되는지 —
  allowed_paths (src/plugins/**) 밖이라 경계 안에서 확정 불가. impact_detail 에 "위험"
  으로만 표기.
- PluginRegistry 의 다른 optional 배열 필드 (`sessionExtensions`, `trustedToolPolicies`,
  `toolMetadata`, `controlUiDescriptors`, `runtimeLifecycles`, `agentEventSubscriptions`,
  `sessionSchedulerJobs`) 도 restorePluginRegistry 화이트리스트에서 누락되어 있다. 단
  본 세션 Grep 기준 이들은 `registry.ts` 내에서 register API 의 직접 push 사이트가
  확인되지 않아 (별도 등록 경로일 가능성) 본 FIND 범위에서 제외. 동일 화이트리스트 drift
  의 잠재 확장 — 후속 감사 시 각 필드의 register 사이트 추적 필요.
- 두 배열 외에 register-throw 후 rollback 미동기화 가능성이 있는 process-global state
  (rollbackPluginGlobalSideEffects 가 커버하지 않는 것) 는 본 FIND 범위 외.
