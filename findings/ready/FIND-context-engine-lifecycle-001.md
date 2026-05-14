---
id: FIND-context-engine-lifecycle-001
cell: context-engine-lifecycle
title: resolveContextEngine drops engine without dispose on contract failure
file: src/context-engine/registry.ts
line_range: 561-599
evidence: "```ts\n  let engine: ContextEngine;\n  try {\n    engine = await entry.factory(factoryCtx);\n\
  \  } catch (factoryError) {\n    if (isDefaultEngine) {\n      throw factoryError;\n\
  \    }\n    console.error(\n      `[context-engine] Context engine \"${sanitizeForLog(engineId)}\"\
  \ factory threw during resolution: ` +\n        `${sanitizeForLog(factoryError instanceof\
  \ Error ? factoryError.message : String(factoryError))}; ` +\n        `falling back\
  \ to default engine \"${defaultEngineId}\".`,\n    );\n    return resolveDefaultContextEngine(defaultEngineId,\
  \ factoryCtx);\n  }\n\n  let contractError: string | null;\n  try {\n    contractError\
  \ = describeResolvedContextEngineContractError(engineId, engine);\n  } catch (validationError)\
  \ {\n    if (isDefaultEngine) {\n      throw validationError;\n    }\n    console.error(\n\
  \      `[context-engine] Context engine \"${sanitizeForLog(engineId)}\" contract\
  \ validation threw: ` +\n        `${sanitizeForLog(validationError instanceof Error\
  \ ? validationError.message : String(validationError))}; ` +\n        `falling back\
  \ to default engine \"${defaultEngineId}\".`,\n    );\n    return resolveDefaultContextEngine(defaultEngineId,\
  \ factoryCtx);\n  }\n  if (contractError) {\n    if (isDefaultEngine) {\n      throw\
  \ new Error(contractError);\n    }\n    // contractError includes engineId from\
  \ plugin config; sanitizeForLog covers it\n    console.error(\n      `[context-engine]\
  \ ${sanitizeForLog(contractError)}; falling back to default engine \"${defaultEngineId}\"\
  .`,\n    );\n    return resolveDefaultContextEngine(defaultEngineId, factoryCtx);\n\
  \  }\n```\n"
symptom_type: lifecycle-gap
problem: '''resolveContextEngine() 가 3rd-party 엔진의 contract 검증 실패 (validator 자체 throw
  또는 contractError !== null)

  를 감지하면 legacy fallback 으로 graceful degrade 한다. 그러나 이미 `entry.factory(factoryCtx)`
  (L563)

  가 성공적으로 반환한 partial-but-invalid `engine` 인스턴스의 `dispose?.()` 가 fallback 경로 (L572-574,

  L587-588, L596-598) 에서 호출되지 않는다. factory 구현이 ContextEngine.dispose 의 명세대로

  "any resources held by the engine" (types.ts:323) 를 셋업 단계에서 잡았다면 (DB 커넥션, 파일 핸들,

  listener subscription, child process 등) 해당 리소스는 dispose 없이 GC 까지 leak.''

  '
mechanism: "'1) 게이트웨이 부팅 또는 subagent spawn / pi-embedded-runner / cli-compaction 시\
  \ `resolveContextEngine(config)`\n   호출. config.plugins.slots.contextEngine = \"\
  my-custom-engine\" 처럼 3rd-party 엔진 id.\n2) registry 에서 entry 발견 → `engine = await\
  \ entry.factory(factoryCtx)` (L563) 성공. 이 시점에\n   factory 가 (a) better-sqlite3 open()\
  \ 같은 fd 점유, (b) RemoteEmbeddingClient 처럼 keep-alive\n   HTTP socket, (c) chokidar.watch\
  \ 같은 native watcher, (d) cron-style setInterval 등\n   production resource 를 셋업.\n\
  3) `describeResolvedContextEngineContractError(engineId, engine)` (L578) 또는 그 호출\n\
  \   자체가 throw. 후자 (L579-588) 든 전자가 string 반환 (L590-598) 든 두 경로 모두 `return\n   resolveDefaultContextEngine(...)`\
  \ 만 수행 — engine 인스턴스에 대한 cleanup 없음.\n4) `engine` 변수는 try 블록 scope 외부 (L561 `let\
  \ engine: ContextEngine`) 라 즉시 GC 되지 않음;\n   fallback path 가 호출 종료되며 함수 스택과 함께 unreachable\
  \ 가 되어야 GC 큐에 들어감.\n   그 사이 잡힌 OS resources / unsubscribed listeners 는 dispose 없이\
  \ 방치 — finalizer\n   없는 자원은 영구 leak, finalizer 있는 자원도 GC 시점까지 지연.\n5) `resolveContextEngine`\
  \ 은 매 subagent spawn / 매 compact / 매 cli /compact 마다 호출되는\n   hot path 이므로 contract\
  \ error 가 한 번이라도 발현되는 plugin slot 설정 (예: 신규\n   plugin 배포 후 SDK 변경 → ingest 누락)\
  \ 환경에서는 호출 횟수 만큼 instance + resource leak.'\n"
root_cause_chain:
- why: 왜 fallback path 가 engine.dispose 를 호출하지 않는가?
  because: resolveContextEngine 의 L572-598 의 세 graceful-degrade 분기 (factory throw
    / validation throw / contract error) 어디에도 `engine.dispose?.()` 호출 또는 `try/finally`
    cleanup 이 없다. factory throw 분기 (L564-574) 는 engine 변수가 미할당 상태라 dispose 부를 객체가
    없으나, validation/contract 두 분기 (L579-598) 는 engine 인스턴스가 이미 존재하는 상태.
  evidence_ref: src/context-engine/registry.ts:561-599
- why: 왜 ContextEngine.dispose 의 호출은 단지 호의가 아니라 contract 인가?
  because: 'types.ts:323-325 가 `dispose?(): Promise<void>` 를 "Dispose of any resources
    held by the engine" 으로 명시. caller-side 에서도 정상 lifecycle 종료 시 dispose 를 호출하는 패턴이
    확립됨 (pi-embedded-runner/run.ts:3094 의 finally + runAgentCleanupStep, compact.queued.ts:108
    early return, compact.queued.ts:301 finally). 정상 경로에서 dispose 가 호출된다는 것은 factory
    가 dispose 안에서 리소스 정리하는 것을 기대한다는 뜻 — 이 기대가 resolveContextEngine 의 fallback 경로에서만
    깨진다.'
  evidence_ref: src/context-engine/types.ts:322-325
- why: 왜 resolveContextEngine 자체가 dispose 책임을 가져야 하는가?
  because: factory 가 반환한 engine 은 resolveContextEngine 의 fallback 경로에서 결코 caller 에게
    전달되지 않는다 (L573/588/598 모두 다른 engine 으로 fallback). 따라서 caller-side 의 dispose 훅
    (run.ts:3094 등) 은 이 invalid engine 을 절대 보지 못한다. 즉 이 engine 의 dispose 를 호출할 수 있는
    유일한 지점은 resolveContextEngine 내부.
  evidence_ref: src/agents/pi-embedded-runner/run.ts:3088-3095 (caller 가 resolveContextEngine
    의 반환값에 대해서만 dispose 호출; rejected/invalid engine 은 caller 미도달)
- why: 왜 이 gap 이 contract validation 도입 시점에 잡히지 않았나?
  because: 'upstream `2677f7cf14` (#63222, "fix: validate resolved context engine
    contracts", 2026-04-13) 이 describeResolvedContextEngineContractError 와 fallback
    로직을 추가했지만, L578-598 의 새 catch/return 분기에서 engine 인스턴스 cleanup 경로를 함께 추가하지 않았다.
    이전 코드는 contract error 가 없었으므로 fallback 시점에 정리할 인스턴스 자체가 없었다 — fallback 도입과 dispose
    책임 분기가 같은 PR 에 들어왔어야 했다.'
  evidence_ref: 'git: 2677f7cf14 fix: validate resolved context engine contracts (#63222)'
impact_hypothesis: resource-exhaustion
impact_detail: '''정성: 3rd-party context engine plugin 이 contract 위반 (ingest/assemble/compact
  함수 누락,

  info.name 빈 문자열 등 — L482-489 6개 위반 카테고리) 으로 fallback 되는 환경에서, resolveContextEngine

  의 매 호출이 engine 인스턴스 1개와 그에 따른 native resource 1세트를 dispose 없이 leak.

  재현 조건: (a) config.plugins.slots.contextEngine 이 non-default 엔진 id, (b) 해당 factory

  가 ContextEngine 메소드를 모두 갖추지 않음 (예: ingest 만 미구현), (c) resolveContextEngine

  이 반복 호출되는 hot-path 활성화. 빈도: pi-embedded-runner/run.ts (turn 마다 1회), compact.queued.ts

  (compact 마다 1회), subagent-spawn.ts:461 (spawn 마다 1회), cli-compaction.ts (CLI /compact

  마다 1회). 즉 시간당 수십~수백 회. 1회당 leak 의 절대량은 factory 가 무엇을 셋업했느냐에

  100% 의존하므로 정량 불가; SQLite open + watcher 가정 시 fd 1~3개 + memory KB~MB 단위.

  process lifetime 이 짧으면 (CLI) 영향 적고, 장기 daemon (gateway) 이면 점진 누적.''

  '
severity: P3
counter_evidence:
  path: src/agents/pi-embedded-runner/run.ts
  line: '3094'
  reason: '''관찰한 반증 후보들과 결과:

    (1) caller-side dispose: run.ts:3094 / compact.queued.ts:108,301 가 resolveContextEngine

    이 반환한 engine 에 대해 finally 블록에서 dispose 호출. 그러나 본 FIND 의 leak engine 은

    fallback path 에서 caller 에게 전달되지 않으므로 (return 으로 다른 engine 반환) caller

    side 의 finally 가 이 invalid engine 을 받지 못함. defense-in-depth 미커버.

    (2) WeakMap finalization: RESOLVED_CONTEXT_ENGINE_METADATA (L37) 가 WeakMap 이라
    invalid

    engine 도 GC 시 자동 제거 — 그러나 dispose 를 통한 native resource cleanup 은 WeakMap 이

    대신 수행하지 못함 (FinalizationRegistry 미사용).

    (3) factory contract: types.ts 는 factory 가 throw 전 자기 cleanup 해야 한다고 명시 안 함;

    contract error 케이스는 factory 가 success path 였으므로 "throw 전 cleanup" 패턴도 적용 안 됨.

    (4) 기존 테스트 커버: context-engine.test.ts:1054-1057 의 dispose 테스트는 legacy engine 의

    dispose 가 throw 안 하는지만 확인. contract-error fallback path 의 dispose 호출 여부 테스트

    부재 — `rg -nP "contractError.*dispose|fallback.*dispose" src/context-engine/` 0
    매치.

    (5) primary-path inversion (CAL-001): 이 lifecycle gap 이 성립하려면 "정상 경로의 cleanup

    이 실패해야" 하는가? — 아니오. resolveContextEngine 내부에 dispose 호출 자체가 부재 (`rg -nP

    "engine\.dispose|dispose\?\." src/context-engine/registry.ts` → 0 매치). primary
    path

    역전 아님, 직접 부재.

    (6) R-7 production hot-path: resolveContextEngine 의 catch 두 분기와 contract-error
    분기

    가 production 에서 taken 되려면 invalid 3rd-party 엔진이 실제 배포돼야 함 — 이는 edge case.

    P3 책정 근거.

    (7) upstream-dup (CAL-008): `git log upstream/main --since="6 weeks ago" -- src/context-engine/registry.ts`

    상 contract validation 추가 (2677f7cf14, 2026-04-13) 와 graceful-degrade 강화 (6aa4515798,

    2026-04-15) 가 최근 변경. dispose 추가 PR 없음. `gh issue list --search "context engine
    dispose"`

    → openclaw 측 issue 0 건. upstream 미인지 영역.''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
domain_notes_ref: domain-notes/context-engine.md
---
# resolveContextEngine drops engine without dispose on contract failure

## 문제

`resolveContextEngine` (src/context-engine/registry.ts:527-602) 은 config.plugins.slots.contextEngine 으로 지정된 3rd-party 엔진을 해상할 때, factory 호출이 성공해 `engine` 인스턴스 변수에 할당된 후 contract 검증을 수행한다. 두 가지 contract 검증 분기:

1. `describeResolvedContextEngineContractError` 자체가 throw (L579-588)
2. 검증 함수가 non-null string 반환 — 즉 contract 위반 (L590-598)

두 분기 모두 비-default 엔진이면 graceful fallback 으로 `resolveDefaultContextEngine` 을 호출하고 그 반환을 그대로 return. **이 fallback 직전에 `engine.dispose?.()` 호출이 없다.** 따라서 factory 가 setup 단계에서 잡은 OS-level / network-level resource 는 caller 도달 전 함수 스택 종료와 함께 unreachable 가 되어 GC 큐에 들어가지만, ContextEngine 의 `dispose?(): Promise<void>` 가 명세하는 "resource cleanup" 은 누구도 수행하지 않는다.

## 발현 메커니즘

1. 게이트웨이가 부팅하고 `ensureContextEnginesInitialized()` 가 legacy 등록 후, plugin 시스템이 `record.id="my-rag-engine"` 플러그인을 로드. 해당 plugin 의 onLoad 에서 `api.registerContextEngine("my-rag", factory)` 호출 → owner=`"plugin:my-rag-engine"` 로 registry 에 entry 생성.
2. config.plugins.slots.contextEngine = "my-rag" 로 설정. 이후 subagent spawn / pi-embedded-runner / cli-compaction 등이 `resolveContextEngine(config)` 호출.
3. `entry.factory(factoryCtx)` (L563) 가 정상 반환. 이 factory 가 내부에서 (예시): SQLite DB 파일 open, vector index 캐시 디렉터리 mmap, embedding API 의 keep-alive HTTP agent 생성, chokidar watcher 시작.
4. `describeResolvedContextEngineContractError(engineId, engine)` (L578) 가 engine 의 `ingest` 가 함수가 아니거나 info.id 가 빈 문자열인 등을 발견하고 비-null 문자열 반환.
5. `if (contractError) {` (L590) 진입. non-default 이므로 console.error 후 `return resolveDefaultContextEngine(...)` (L598). 함수 종료.
6. 호출자 (예: pi-embedded-runner/run.ts:1065 가 호출한 resolveContextEngine) 는 legacy engine 만 받음. invalid `my-rag` engine 인스턴스는 절대 caller scope 에 도달하지 않음.
7. invalid engine 의 dispose() 가 호출될 수 있는 유일한 지점이었던 resolveContextEngine 내부에서 호출이 없으므로, factory 가 잡은 resource 는 GC 시점까지 (또는 finalizer 없는 native handle 의 경우 process 종료까지) 방치.
8. 이 시퀀스가 매 호출마다 반복. resolveContextEngine 은 turn / compact / spawn 마다 호출되는 hot-path 이므로 leak 누적.

## 근본 원인 분석

1. **부분 lifecycle 책임의 분산**: caller-side 는 자기가 받은 engine 만 dispose 한다 (run.ts:3094 / compact.queued.ts:108, 301). resolveContextEngine 의 fallback 경로에서 caller 에 도달하지 못한 engine 은 caller-side 코드가 알 길이 없다. 결국 resolveContextEngine 자체가 그 인스턴스의 dispose 를 책임져야 하는데, 코드에는 그 호출이 부재.
2. **incremental refactor 의 누락**: upstream `2677f7cf14` (#63222, 2026-04-13) 가 contract validation 과 graceful fallback 을 한 번에 도입했지만, 새로 등장한 "factory 는 성공했으나 invalid 인 engine 인스턴스" 라는 상태가 정리되어야 한다는 점이 PR 의 review 범위에 없었다. validation 함수 자체와 fallback 로직만 추가됨.
3. **dispose contract 의 caller 측 책임 모호**: types.ts:323-325 의 dispose 주석이 "Dispose of any resources held by the engine" 만 명시할 뿐, "누가 언제 호출하는가" 는 명시 안 됨. 결과적으로 resolveContextEngine 작성자와 caller-side 작성자 사이에 책임 회색 지대가 발생.

## 영향

- **현상**: production gateway 에서 3rd-party context engine 이 contract violation 으로 fallback 되는 환경 (개발 중 plugin, beta 릴리스, SDK 버전 mismatch) 마다 resolveContextEngine 호출 당 1 engine instance + 그 factory 가 점유한 native resource 1세트가 dispose 없이 leak.
- **빈도**: turn 마다 (pi-embedded-runner/run.ts:1065), compact 마다 (compact.queued.ts:56), subagent spawn 마다 (subagent-spawn.ts:461), CLI /compact 마다 (cli-compaction.ts:208). 시간당 수십~수백 회.
- **1회당 양**: factory 구현에 100% 의존. 가장 자주 보이는 패턴 (SQLite open + chokidar watcher + HTTP keepalive agent) 가정 시 fd 1~3개 + 메모리 KB~MB 단위.
- **장기 영향**: 장기 실행 daemon (gateway 의 cron / scheduler) 에서 점진 fd 고갈 → `EMFILE: too many open files` 가능. memory 측면은 GC 가 cycle 마다 일부 회수 가능하지만 dispose 가 호출됐어야만 정리되는 자원 (예: 외부 HTTP 연결, 외부 child process) 은 영구 leak.
- **재현 가능성**: invalid factory 구현 1줄로 재현 가능 (예: factory 에서 `await fs.open(...)` 후 `return {info, ingest, assemble}` — compact 메소드 누락 → contract error). dispose 가 안 불리는지 검증은 fs.open 의 fd 확인.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -nP "engine\.dispose|dispose\?\." src/context-engine/registry.ts` → **0 매치**. registry.ts 내부에 dispose 호출 자체 없음.
- caller-side dispose (run.ts:3094, compact.queued.ts:108, 301) 는 resolveContextEngine 의 **반환 값** 에만 적용. invalid engine 은 반환되지 않으므로 caller 가 받지 못함 — defense-in-depth 미커버.
- WeakMap (`RESOLVED_CONTEXT_ENGINE_METADATA`, L37) 은 invalid engine 을 metadata 에 set 한 적이 없으므로 (L601 wrapResolvedContextEngine 진입 전 fallback) 자동 정리 대상도 아님. 또 metadata 자체는 native resource cleanup 도구가 아님.
- FinalizationRegistry 사용 0 매치 (`rg -nP "FinalizationRegistry" src/context-engine/` → 0).

### 기존 테스트 커버리지

- `rg -nP "contractError.*dispose|invalid.*dispose" src/context-engine/` → **0 매치**.
- context-engine.test.ts:1054-1057 의 dispose 테스트는 legacy engine 의 dispose 가 reject 안 하는지만 확인. contract-error fallback 시 dispose 호출 여부는 검증 없음.
- context-engine.test.ts:945-948 의 cleanup helper (`registryState.engines.clear()` in finally) 는 registry entry 만 정리. invalid engine 인스턴스의 dispose 호출 검증 아님.

### 호출 빈도 / 경로 활성 여부

- production 에서 resolveContextEngine 이 contract-error path 로 들어가는 조건은 "invalid 3rd-party 엔진 + non-default slot 설정" 의 결합. openclaw 기본 설정에서는 slot 이 "legacy" 라 default path 진입 → throw → 다른 카테고리. 즉 본 FIND 는 3rd-party plugin 사용 환경에 한정.
- 그러나 매 turn / compact / spawn 호출되는 hot-path 이므로 한 번 setup 이 invalid 면 빈도 자체는 매우 높음.

### 설정 / feature flag

- `config.plugins.slots.contextEngine` 미지정 또는 "legacy" 면 default path 라 본 케이스 진입 안 함 (L548 isDefaultEngine 분기로 throw, fallback 없음).
- non-default 엔진이 contract 를 정확히 구현하면 본 케이스 진입 안 함.

### 주변 코드 맥락

- caller (run.ts:3088-3095) 의 dispose 호출은 `runAgentCleanupStep` 로 래핑돼 try/catch 가 보장됨 — 즉 dispose 실패 시 process crash 위험은 없는 안전한 호출 패턴. 같은 패턴을 resolveContextEngine 내부에 도입해도 risk-free.
- 비교: registerContextEngineForOwner (L374-397) 의 register 자체는 atomic Map.set 이라 partial-state 잔존 불가. 본 FIND 는 register 측 문제가 아니라 resolve 측 문제임을 명확히 한다.

### Primary-path inversion (CAL-001)

이 lifecycle gap 이 성립하려면 어떤 정상 경로가 실패해야 하는가? — **부재 자체가 gap**. resolveContextEngine 의 fallback 분기 안에 dispose 호출이 처음부터 없다. 정상 경로 의존 없이 그 자체로 누락. CAL-001 의 함정 (숨은 cleanup 을 못 보고 부재로 오인) 의 반대 상황 — grep 으로 정말 부재함을 확인.

### Hot-path-vs-test-path consistency (CAL-003)

기존 context-engine.test.ts 는 in-memory engine 으로 dispose 미체크하므로 본 시나리오는 unit test 에서 잡히지 않는다. 실제 production hot-path (factory 가 native resource 잡는 3rd-party) 와 test path (factory 가 plain object 반환) 가 불일치.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/context-engine/registry.ts` 의 graceful-degrade 관련 commit: `2677f7cf14` (#63222, 2026-04-13), `6aa4515798` (#66930, 2026-04-15), `263a190fc9` (#66678, 2026-04-20), `59d07f0ab4` (2026-04-17 — owner rollback). dispose 추가 PR 없음.
- `gh issue list --search "context engine dispose"` / `gh pr list --search "resolveContextEngine dispose"` → 0 건. upstream 미인지 영역.

## Self-check

### 내가 확실한 근거

- registry.ts:527-602 의 전 구간 Read 로 확인 — fallback 세 분기 모두 dispose 호출 없음.
- registry.ts 전체에 dispose 호출 grep → 0 매치.
- caller-side dispose 호출 site 3곳 확인 (run.ts:3094 / compact.queued.ts:108, 301) — 모두 resolveContextEngine 의 정상 반환 engine 에 대해서만.
- types.ts:322-325 의 dispose 명세가 "resources held by the engine" 으로 적힘.
- upstream commit 2677f7cf14 가 contract validation 도입 — 같은 PR 에 dispose 처리 없음 (git show 확인).

### 내가 한 가정

- 3rd-party context engine factory 가 실제로 setup 단계에서 OS resource 를 잡는다는 가정. openclaw 외부 plugin 의 구체 구현은 본 페르소나가 보지 못함. plain-object factory 만 사용하는 환경이면 leak 의 실효 영향은 0.
- `resolveContextEngine` 이 다른 곳에서 dispose 를 lazily 호출하지 않는다는 가정 — registry.ts 외 src/context-engine/* 에 dispose grep 결과 legacy.ts:84 (자기 dispose) 와 context-engine.test.ts 만 — 다른 호출 site 없음 확인됨.
- engine 인스턴스가 wrapResolvedContextEngine (L601) 을 통과하지 않은 채 caller 에 도달하는 경로는 없다는 가정 — 코드 흐름상 fallback 은 항상 resolveDefaultContextEngine 이 반환하는 legacy engine 만 caller 에 전달.

### 확인 안 한 것 중 영향 가능성

- WeakMap RESOLVED_CONTEXT_ENGINE_METADATA 가 wrap 단계에서만 set 되므로 invalid engine 은 metadata 안 들어감 — 확정. 다만 caller 가 invalid engine 의 reference 를 어떻게 받아도 metadata 조회는 항상 undefined. resolveContextEngineOwnerPluginId 가 plugin id 못 찾는 부수효과 발생 가능 — 단 본 FIND 의 leak 과는 별개 영향.
- LegacyContextEngine (legacy.ts:21) 의 factory 도 `async () => new LegacyContextEngine()` 으로 단순. 만약 default 가 LegacyContextEngine 아닌 다른 core engine 으로 바뀌면 default path 도 동일 gap 노출 — 현재는 default engine throw 시 throw propagate (L565-566) 라 본 FIND 와 다른 경로.
- 3rd-party engine factory 가 dispose 가 아닌 다른 cleanup 이름 (close/destroy/teardown) 으로 자기 자원을 정리할 수도 있음. 그 경우 dispose 호출 강제만으로는 cleanup 실현 안 됨 — types.ts 의 dispose contract 강화도 필요한 후속 영역.
