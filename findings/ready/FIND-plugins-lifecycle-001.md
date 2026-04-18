---
id: FIND-plugins-lifecycle-001
cell: plugins-lifecycle
title: plugin register throw 시 httpRoutes/services/commands/hooks 부분 등록 잔존
file: src/plugins/loader.ts
line_range: 1839-1862
evidence: "```ts\n      } catch (err) {\n        restoreRegisteredAgentHarnesses(previousAgentHarnesses);\n\
  \        restoreRegisteredCompactionProviders(previousCompactionProviders);\n  \
  \      restoreRegisteredMemoryEmbeddingProviders(previousMemoryEmbeddingProviders);\n\
  \        restoreMemoryPluginState({\n          corpusSupplements: previousMemoryCorpusSupplements,\n\
  \          promptBuilder: previousMemoryPromptBuilder,\n          promptSupplements:\
  \ previousMemoryPromptSupplements,\n          flushPlanResolver: previousMemoryFlushPlanResolver,\n\
  \          runtime: previousMemoryRuntime,\n        });\n        recordPluginError({\n\
  \          logger,\n          registry,\n          record,\n          seenIds,\n\
  \          pluginId,\n          origin: candidate.origin,\n          phase: \"register\"\
  ,\n          error: err,\n          logPrefix: `[plugins] ${record.id} failed during\
  \ register from ${record.source}: `,\n          diagnosticMessagePrefix: \"plugin\
  \ failed during register: \",\n        });\n      }\n```\n"
symptom_type: lifecycle-gap
problem: '플러그인의 register() 함수가 api.registerHttpRoute / registerService / registerCommand
  / registerHook 등을

  순차 호출하던 중 N+1번째 register 또는 plugin 자체 코드에서 throw 하면, 이미 0..N 번째로 registry.httpRoutes,

  registry.services, registry.commands, registry.hooks 리스트에 push 된 엔트리들이 제거되지 않은 채
  잔존한다.

  record.status = "error" 로 표시되고 record 는 registry.plugins 에도 push 되지만,

  registry 의 route/service/command/hook 테이블은 "정상 등록" 으로 계속 참조된다.

  '
mechanism: "1. loader.ts:1799 `createApi(record, ...)` 로 plugin api 생성 (registerHttpRoute\
  \ 등은 registry.ts 의\n   registerHttpRoute(record, routeParams) → registry.httpRoutes.push(...)\
  \ 에 직접 연결)\n2. loader.ts:1805-1812 에서 previousAgentHarnesses / previousCompactionProviders\
  \ / previousMemory* 만 snapshot\n3. loader.ts:1815 `register(api)` 호출 — plugin 이\
  \ api.registerHttpRoute(\"/foo\"), api.registerService({id:\"svc\"}),\n   api.registerCommand({name:\"\
  cmd\"}) 을 순차 호출 → registry.httpRoutes/services/commands 에 각각 push 됨\n4. plugin register\
  \ 본체의 끝부분 또는 N+1번째 registerHook 등에서 throw\n5. loader.ts:1839 catch 블록 진입 → restoreRegisteredAgentHarnesses/Compaction/MemoryEmbedding/Memory*\
  \ 만 복구\n6. registry.httpRoutes, registry.services, registry.commands, registry.hooks\
  \ 의 push 된 엔트리는 제거되지 않음\n7. recordPluginError (loader.ts:837) 에서 record.status =\
  \ \"error\" 설정 후 registry.plugins 에 record push\n8. 이후 gateway runtime 은 registry.httpRoutes/services/commands\
  \ 를 그대로 공개 → 고아 route/service 노출\n"
root_cause_chain:
- why: register throw 시 왜 httpRoutes/services/commands/hooks 엔트리가 살아남는가?
  because: catch 블록에서 restoreRegisteredAgentHarnesses 등 process-global runtime state
    만 복구하고, registry 배열들은 복구하지 않는다
  evidence_ref: src/plugins/loader.ts:1839-1849
- why: 왜 registry 배열들은 snapshot 하지 않았는가?
  because: register() 콜백이 registry.ts 의 registerHttpRoute/Service/Command 를 직접 호출하여
    registry.httpRoutes.push 에 바로 append 하는 설계이고, loader 는 registry 중간 상태 복원 책임을 agentHarnesses
    계열에만 할당했다
  evidence_ref: src/plugins/registry.ts:388-411 (registry.httpRoutes.push), src/plugins/registry.ts:961-968
    (registry.services.push)
- why: 왜 register 실패 후에도 route/service 가 유효하다고 취급되는가?
  because: registry 소비자는 pluginId 기반 필터링 대신 registry.httpRoutes 전체를 공개한다. record.status='error'
    여도 이 플러그인이 push 한 entry 는 동일 pluginId 를 가진 채 남아있어 외부에서 구분 불가능
  evidence_ref: src/plugins/http-registry.ts:28 (`registry.httpRoutes = routes`),
    src/plugins/loader.ts:837-842 (status=error 후 registry.plugins.push 만 수행)
- why: 왜 pluginId 로 post-filter 하지 않는가?
  because: failed plugin 의 부분 등록 개념이 현재 설계에 없음 — status='error' 플러그인의 등록물은 애초에 registry
    에 들어가지 않았어야 한다는 가정. 그러나 실제로는 register() 가 중간까지 진행 후 throw 하면 들어가버림
  evidence_ref: 'N/A — 설계 의도의 부재를 입증할 코드는 없음. 반증: `registry.plugins.filter(e => e.status===''error'')`
    소비자 post-filter 경로 grep 결과 없음'
impact_hypothesis: wrong-output
impact_detail: "정성: 플러그인이 register 함수의 마지막 제어 흐름에서 throw 하거나, 다수의 register*\n호출 중간에\
  \ throw 할 때 발생. 영향:\n- 부분 등록된 HTTP route 가 gateway 에 그대로 expose → 해당 route 호출 시\
  \ 플러그인 handler 가\n  초기화 실패 상태에서 실행되어 undefined behavior (record 에 할당되었을 state 가\
  \ 없음)\n- 부분 등록된 service 가 registry.services 에 남아 다른 플러그인/core 의 service lookup 에\n\
  \  응답 → 잘못된 service 반환\n- record.status='error' 는 표시되지만 외부 소비자는 httpRoutes/services\
  \ 를 status 로 필터하지 않음\n빈도: register() 중간 throw 는 드문 경로 (정상 플러그인은 throw 하지 않음). 그러나\
  \ 플러그인\n설정 오류, 의존 리소스 부재(port 충돌/파일 없음), jiti alias 해결 실패 등으로 런타임 환경에서 발생 가능.\n"
severity: P1
counter_evidence:
  path: src/plugins/loader.ts
  line: 1840-1849
  reason: "R-3 Grep 수행:\n(1) `rg -n \"(unregister|dispose|teardown|unload|cleanup|rollback).*(httpRoute|service|command|hook)\"\
    \ src/plugins/`\n    → production code 상 매치 0건 (test helper cleanup* 이름만 매치).\n\
    (2) `rg -n \"registry\\.(httpRoutes|services|commands|hooks).*(filter|splice|delete|pop)\"\
    \ src/plugins/`\n    → 매치 0건. registry 배열에서 엔트리를 제거하는 production 경로 없음.\n(3) `rg\
    \ -n \"recordPluginError\" src/plugins/loader.ts` → 5개 호출 지점 모두 registry\n   \
    \ 배열 rollback 없이 record.status='error' 만 설정.\n(4) catch 블록이 복구하는 대상 (line 1840-1849):\
    \ restoreRegisteredAgentHarnesses,\n    restoreRegisteredCompactionProviders,\
    \ restoreRegisteredMemoryEmbeddingProviders,\n    restoreMemoryPluginState — 이들은\
    \ process-global runtime state (agent harness list,\n    compaction provider list,\
    \ memory prompt supplements 등) 만 복구.\nhttpRoutes/services/commands/hooks 부분 등록\
    \ rollback 대응 경로 미존재 → FIND 성립.\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-04-18'
related_tests:
- src/plugins/plugin-graceful-init-failure.test.ts
- src/plugins/loader.test.ts
---
# plugin register throw 시 httpRoutes/services/commands/hooks 부분 등록 잔존

## 문제

`src/plugins/loader.ts` 의 plugin 로드 경로는 `register(api)` 호출 (line 1815) 을 try/catch
로 감싸고 있으나, catch 블록 (line 1839-1862) 이 복구하는 대상은 **process-global runtime
state 4종** (agent harnesses, compaction providers, memory embedding providers, memory
plugin state) 에 국한된다. 플러그인이 `register()` 본체 안에서 `api.registerHttpRoute`,
`api.registerService`, `api.registerCommand`, `api.registerHook`, `api.registerGatewayMethod`,
`api.registerProvider` 등을 **여러 번 순차 호출**한 뒤 throw 하면, 이미 `registry.httpRoutes`,
`registry.services`, `registry.commands`, `registry.hooks` 배열에 push 된 N개의 엔트리들은
그대로 잔존한다. record 는 `status='error'` 로 표시되나, registry 의 route/service 테이블을
소비하는 gateway 코드는 status 로 필터링하지 않으므로 실패한 플러그인의 부분 등록물이
정상 등록물과 구분 없이 공개된다.

## 발현 메커니즘

1. `loader.ts:1799` `createApi(record, { config, pluginConfig, hookPolicy, registrationMode })`
   로 plugin api 생성. `registry.ts:1126` 의 `createApi` 가 `handlers.registerHttpRoute =
   (routeParams) => registerHttpRoute(record, routeParams)` 등을 주입.
2. `loader.ts:1805-1812` 에서 **agent harness / compaction / memory 계열만** snapshot
   (previousAgentHarnesses 등).
3. `loader.ts:1815` `register(api)` 호출. 플러그인 내부에서 다음이 순차 실행된다고 가정:
   - `api.registerHttpRoute({path:"/foo", auth:"plugin", handler})` → `registry.ts:388` 에서
     `registry.httpRoutes.push(...)` 수행.
   - `api.registerService({id:"svc-1", ...})` → `registry.ts:962` 에서 `registry.services.push(...)`.
   - `api.registerCommand({name:"cmd-x", ...})` → `registry.ts:971+` 에서 command registry 갱신.
   - 이후 플러그인 로직 (config 검증, 파일 시스템 접근, port bind 등) 에서 throw.
4. `loader.ts:1839` catch 블록 진입.
5. `restoreRegisteredAgentHarnesses(previousAgentHarnesses)` 등 **4종만 복구**.
6. `registry.httpRoutes`, `registry.services`, `registry.commands`, `registry.hooks` 배열의
   push 된 N개 엔트리는 제거되지 않음.
7. `recordPluginError` (`loader.ts:813`) 가 `record.status = "error"` 설정 후 `registry.plugins.push(record)`.
8. 플러그인 로드 완료. gateway 가 `registry.httpRoutes` 를 그대로 HTTP 라우터에 바인딩 →
   /foo 호출 시 초기화 실패한 플러그인의 handler 가 invoked (플러그인 내부 state 가 미완성이어서
   undefined behavior).

## 근본 원인 분석

1. **catch 블록의 복구 범위 편향** (`loader.ts:1839-1849`): 복구 대상이
   `restoreRegisteredAgentHarnesses`, `restoreRegisteredCompactionProviders`,
   `restoreRegisteredMemoryEmbeddingProviders`, `restoreMemoryPluginState` 4종에만 한정. 이
   4종은 process-global singleton (listRegistered* / getMemory* 로 조회) 이지만,
   `registry.httpRoutes` / `registry.services` / `registry.commands` 는 동일한 로더 세션
   내 registry 객체의 배열 필드로, snapshot 되지 않고 직접 mutation 된다.

2. **register 함수들의 즉시 push 설계** (`registry.ts:388, 962`): `registerHttpRoute` 와
   `registerService` 등은 호출 즉시 `registry.httpRoutes.push(...)` / `registry.services.push(...)`
   를 수행. deferred/staging 영역 없이 바로 공개 상태가 됨. try/catch 없이 직접 push 되므로
   plugin register 의 "부분 진행" 상태가 그대로 반영된다.

3. **소비자 측 post-filter 부재** (`http-registry.ts:28` 등): `registry.httpRoutes = routes`
   같이 registry 전체를 그대로 소비. `record.status === 'error'` 인 플러그인의 pluginId 엔트리를
   걸러내는 공통 헬퍼가 없고, grep 결과 `registry.plugins.filter(e => e.status==='error')`
   기반 post-filter 소비 경로 없음.

4. **활성화 실패 플러그인의 semantic 모호함**: `record.status='error'` 는 "초기화 실패"를
   의미해야 하지만, 동시에 registry 에 부분 등록된 artifacts 를 노출하는 것은 모순. 현재
   설계에서 이 모순을 해소하는 dispose/rollback 책임 주체가 부재.

## 영향

- **impact_hypothesis**: wrong-output (부분 등록 리소스가 정상인 것처럼 노출되어 잘못된 응답)
- **재현 시나리오**:
  1. 테스트 플러그인 작성: register 본체에서
     `api.registerHttpRoute({path:"/hello", auth:"plugin", handler})` 호출 후
     `throw new Error("config missing")`.
  2. loader.loadPlugins() 실행.
  3. 결과: `registry.plugins.find(p=>p.id==='test').status === 'error'` 이지만
     `registry.httpRoutes.some(r=>r.pluginId==='test')` → true.
  4. gateway 가 /hello 요청을 받으면 초기화 실패한 플러그인의 handler 호출 (state undefined).
- **빈도**: register() 중간 throw 는 드문 경로지만, 플러그인 config schema 검증 실패,
  파일 시스템 접근 실패(rootDir 권한), port bind 실패, jiti alias 해결 실패,
  downstream require throw 등으로 프로덕션에서 발생 가능. 특히 plugin hot-reload 시나리오
  (사용자가 config 에서 잘못된 값을 입력 → 다음 loadPlugins 사이클에서 re-register throw) 에서
  누적 가능성 있음.
- **P1 근거**: 데이터 손실/크래시는 아니지만, failed plugin 이 HTTP route 를 계속 노출하는 것은
  security-adjacent (auth='plugin' handler 가 미완성 state 로 동작) 하며 silent wrong-output 유발.

## 반증 탐색

**R-3 Grep 명령 + 결과:**

1. `rg -n "(unregister|dispose|teardown|unload|cleanup|rollback).*(httpRoute|service|command|hook)" src/plugins/`
   → production code 0건. test helper `cleanupTrackedTempDirs` / `cleanupPluginLoaderFixturesForTest` 만.
2. `rg -n "registry\.(httpRoutes|services|commands|hooks).*(filter|splice|delete|pop)" src/plugins/`
   → 0건. registry 배열에서 엔트리 제거하는 production 경로 없음.
3. `rg -n "recordPluginError" src/plugins/loader.ts`
   → 5개 호출 (line 1664, 1685, 1850, 2148, 2240). 전부 registry 배열 rollback 없이 record.status='error' 설정만.

**추가 반증 탐색:**

- **숨은 상위 try/catch**: loader.ts 최상위 함수는 `maybeThrowOnPluginLoadError`
  (line 1883) 에서 throw 여부만 결정. 전체 registry reset 경로 없음.
- **allSettled 격리**: 플러그인 로드는 for 루프로 순차 진행 (Promise.all/allSettled 아님).
  한 플러그인 throw 시 restart 하지 않고 다음 플러그인으로 넘어감 → 부분 등록 잔존.
- **기존 테스트 커버리지**: `src/plugins/plugin-graceful-init-failure.test.ts` 존재.
  파일명으로 유추하면 init 실패 graceful 처리를 다루지만, registry 잔존물 cleanup 까지
  검증하는지는 파일 내부 확인 필요 (본 세션 범위 외 — Self-check 의 "확인 안 한 것" 에 기재).
- **설정 핫 리로드**: plugin 재로드 시 새 registry 객체 생성 (loader 에서 createPluginRegistry
  호출) → 기존 registry GC 됨. 따라서 **동일 loader 세션 내 잔존** 이 주 문제. 장시간 같은
  gateway 인스턴스에서 동일 registry 가 유지되는 한.
- **post-filter 구현 가능성**: grep 결과 `registry.plugins.filter(e=>e.status==='error')`
  로 failed plugin 을 식별하는 소비자 없음.

## Self-check

### 내가 확실한 근거

- `loader.ts:1839-1862` catch 블록이 `restoreRegisteredAgentHarnesses`,
  `restoreRegisteredCompactionProviders`, `restoreRegisteredMemoryEmbeddingProviders`,
  `restoreMemoryPluginState` 만 호출. 직접 읽어 확인함.
- `registry.ts:388-411` `registerHttpRoute` 가 `registry.httpRoutes.push/findIndex` 로 배열 직접 mutation.
- `registry.ts:961-968` `registerService` 가 `registry.services.push` 로 즉시 공개.
- `http-registry.ts:28` `registry.httpRoutes = routes` 로 routes 전체를 외부에 전달.
- Grep: registry 배열에서 엔트리 제거하는 production 경로 부재.

### 내가 한 가정

- gateway HTTP 라우터가 `registry.httpRoutes` 를 status 필터 없이 소비한다 — 파일명에서 유추.
  만약 소비 시점에 `registry.plugins.find(p=>p.id===route.pluginId).status` 체크가 있다면
  wrong-output 리스크는 무력화됨 (FIND severity 하향 가능).
- 플러그인 register() 가 `api.registerHttpRoute` 후 throw 하는 시나리오가 프로덕션에서 "드물게"
  발생한다 — 정량화 못 함.

### 확인 안 한 것 중 영향 가능성

- `src/plugins/plugin-graceful-init-failure.test.ts` 본문: 이미 이 FIND 시나리오를 기대/검증하는
  지 여부. 만약 "부분 등록 후 throw 시 registry 에 잔존 엔트리 있어야 한다" 로 의도를 고정했다면
  FIND 의 상태가 "design" 이 되어 severity 재평가 필요.
- `registerHttpRoute` 소비자 (gateway 의 route dispatcher) 가 record.status 를 체크하는지:
  loader.ts 경계 안에서 답을 확정할 수 없음 (`src/gateway/**` 는 allowed_paths 외).
- `recordPluginError` 의 다른 4개 호출 지점 (line 1664, 1685, 2148, 2240) 이 "load" phase 에
  서 호출되는 경우 register() 는 아직 호출되지 않았을 수 있어 동일 결함 없을 가능성. 본 FIND 는
  register phase (line 1850) 에 집중.
- registry.commands / registry.hooks / registry.gatewayMethods / registry.providers 등 그 외
  배열에 대해서도 동일 패턴의 부분 등록 잔존이 있는지 — 각각 별도 FIND 로 분리해야 할 수도 있으나
  본 배치 토큰 예산 안에서는 단일 카드로 수렴.
