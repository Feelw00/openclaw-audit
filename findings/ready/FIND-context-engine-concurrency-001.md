---
id: FIND-context-engine-concurrency-001
cell: context-engine-concurrency
title: resolveContextEngine entry snapshot leaks stale owner past clear race
file: src/context-engine/registry.ts
line_range: 546-601
evidence: "```ts\n  const entry = getContextEngineRegistryState().engines.get(engineId);\n\
  \  if (!entry) {\n    if (isDefaultEngine) {\n      throw new Error(\n        `Context\
  \ engine \"${engineId}\" is not registered. ` +\n          `Available engines: ${listContextEngineIds().join(\"\
  , \") || \"(none)\"}`,\n      );\n    }\n    console.error(\n      `[context-engine]\
  \ Context engine \"${sanitizeForLog(engineId)}\" is not registered; ` +\n      \
  \  `falling back to default engine \"${defaultEngineId}\".`,\n    );\n    return\
  \ resolveDefaultContextEngine(defaultEngineId, factoryCtx);\n  }\n\n  let engine:\
  \ ContextEngine;\n  try {\n    engine = await entry.factory(factoryCtx);\n  } catch\
  \ (factoryError) {\n    if (isDefaultEngine) {\n      throw factoryError;\n    }\n\
  \    console.error(\n      `[context-engine] Context engine \"${sanitizeForLog(engineId)}\"\
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
  \  }\n\n  return wrapResolvedContextEngine(engine, { owner: entry.owner });\n```\n"
symptom_type: concurrency-race
problem: 'resolveContextEngine (registry.ts:527-602) captures engines.get(engineId)
  into a closure-bound entry at L546, then awaits entry.factory(factoryCtx) at L563.
  While suspended on that await, an external sync caller can invoke clearContextEnginesForOwner(entry.owner)
  (plugins/registry.ts:2932 rollbackPluginGlobalSideEffects from plugins/loader.ts:2406
  catch). When the factory resolves, L601 wrapResolvedContextEngine(engine, { owner:
  entry.owner }) uses the closure snapshot — wrapped engine permanently records owner
  metadata for a plugin that is no longer registered. The wrapped engine itself is
  functional but resolveContextEngineOwnerPluginId returns a dangling pluginId used
  by lifecycle attribution in pi-embedded-runner/run.ts, context-engine-maintenance.ts,
  compact.queued.ts.'
mechanism: "T0 caller A invokes resolveContextEngine(config{slot=\"X\"}).\n   L546\
  \ entry = engines.get(\"X\") snapshot { factory, owner: \"plugin:X\" } (sync).\n\
  T1 L563 engine = await entry.factory(factoryCtx) → yields to microtask queue.\n\
  T2 Concurrent fiber: plugin X install's later phase (hooks/cli) throws →\n   plugins/loader.ts:2406\
  \ catch → plugins/registry.ts:2932 rollbackPluginGlobalSideEffects(\"X\") →\n  \
  \ clearContextEnginesForOwner(\"plugin:X\") → registry.ts:427-435 sync engines.delete(\"\
  X\").\nT3 Factory promise resolves; caller A resumes. engine valid. contract pass\
  \ at L578.\nT4 L601 wrapResolvedContextEngine(engine, { owner: entry.owner }).\n\
  \   entry.owner === \"plugin:X\" (stale closure snapshot).\n   L326 RESOLVED_CONTEXT_ENGINE_METADATA.set(wrapped,\
  \ { owner: \"plugin:X\" }).\nT5 caller pins wrapped engine; resolveContextEngineOwnerPluginId(wrapped)\
  \ returns \"X\"\n   but engines map has no \"X\". Stale owner attribution lasts\
  \ until wrapped engine is dropped.\n"
root_cause_chain:
- why: snapshot freshness not enforced between entry capture and use
  because: entry = engines.get(engineId) at L546 is reused at L601 without revalidation
    (no engines.get(engineId)?.owner ?? entry.owner pattern, no version stamp, no
    entry.invalidated flag)
  evidence_ref: registry.ts:546 and registry.ts:601
- why: no lock primitive in context-engine domain
  because: registry mutation (set L395, delete L432) and resolution snapshot (get
    L546) live in different microtasks but no Mutex/Semaphore/AsyncLock. R-3 grep
    returns 0 in src/context-engine/
  evidence_ref: R-3 grep results documented in counter_evidence
- why: clearContextEnginesForOwner external-call timing assumption undocumented
  because: helper added 2026-04-17 (59d07f0ab4) and wired to plugins/registry.ts:2932
    rollback path. Implicit assumption "rollback only fires at boot partial-failure"
    but plugin lazy-install / hot-reload would invoke the same helper concurrently
    with resolve
  evidence_ref: plugins/registry.ts:2932 and plugins/loader.ts:2406
- why: wrapResolvedContextEngine metadata is write-once
  because: RESOLVED_CONTEXT_ENGINE_METADATA.set at registry.ts:326 has no update API.
    Wrapped engine pinned by caller permanently retains stale owner
  evidence_ref: registry.ts:321-328
impact_hypothesis: wrong-output
impact_detail: Lifecycle attribution misreports plugin owner for resolved context
  engines. pi-embedded-runner/run.ts:1070 and context-engine-maintenance.ts:355 and
  compact.queued.ts:99 use resolveContextEngineOwnerPluginId for metric/log/maintenance
  attribution. After the race, operator sees "plugin X still active" while X is unregistered;
  maintenance code may attempt context-engine maintenance using a now-missing pluginId
  producing downstream lookup warnings. The wrapped engine itself remains functional
  (compact/ingest/assemble unaffected) so this is hygienic/attribution corruption
  — severity P3.
severity: P3
counter_evidence:
  path: src/context-engine/registry.ts
  line: 527-602
  reason: 'R-3 grep 5종 results (src/context-engine/):

    - Mutex|Semaphore|AsyncLock|acquire|release → 0 matches (types.ts:85 "released"
    is SubagentEndReason enum string, not lock).

    - AbortController|AbortSignal → 0 matches.

    - Promise.race|Promise.all|Promise.allSettled → 0 production matches (1 test only).

    - once\(|prependOnceListener|removeAllListeners → 0 matches.

    - setImmediate|queueMicrotask|process.nextTick → 0 matches.

    R-5 execution-condition table places resolveContextEngine L546→L601 entry lifetime
    as the only unguarded race window in the domain; all other register/clear/wrap
    paths are sync atomic. R-7 hot-path check confirms resolveContextEngine production
    callers in subagent-registry.ts:325 and pi-embedded-runner/run.ts:1066 and compact.queued.ts:63
    (session hot-path); clearContextEnginesForOwner production caller is plugins/registry.ts:2932
    (rollback). Concurrent activation requires plugin partial-failure rollback while
    a session caller is resolving the same slot — rare under boot-once plugin lifecycle
    but plausible under lazy-install/hot-reload. R-8 upstream check: no race-related
    fix in src/context-engine/ since 59d07f0ab4 introduced clearContextEnginesForOwner;
    gh pr list --state open --search "resolveContextEngine snapshot OR context-engine
    race" → 0 matches. CAL-001 (primary-path inversion) test: no other entrypoint
    refreshes entry between L546 and L601 — no hidden guard.

    '
status: discovered
discovered_by: concurrency-auditor
discovered_at: '2026-05-14'
cross_refs: []
rejected_reasons: []
---
# resolveContextEngine: entry snapshot leaks stale owner past clear race

## 문제

`resolveContextEngine` (registry.ts:527-602) 은 L546 에서 registry 의 entry 를 동기적으로 캡처한 뒤 L563 에서 plugin factory 를 await 한다. factory 가 resolve 되기까지의 microtask suspension 동안 외부의 다른 비동기 흐름이 `clearContextEnginesForOwner(entry.owner)` 를 호출하여 같은 entry 를 registry 에서 제거할 수 있다 (plugin loader 의 catch 블록에서 발생 — plugins/registry.ts:2932, plugins/loader.ts:2406).

await 가 끝난 뒤 L601 의 `wrapResolvedContextEngine(engine, { owner: entry.owner })` 는 closure 에 잡힌 stale `entry.owner` 를 그대로 사용하여 wrapped engine 의 metadata 를 등록한다. caller 는 valid 한 engine 인스턴스를 받지만 그 owner metadata 는 더 이상 registry 에 존재하지 않는 plugin 의 id 를 가리킨다. `resolveContextEngineOwnerPluginId` 가 이 metadata 를 읽어 lifecycle attribution (pi-embedded-runner/run.ts:1070, context-engine-maintenance.ts:355, run/attempt.ts:934 등) 에 사용한다.

context-engine 도메인 안에는 `Mutex|Semaphore|AsyncLock|acquire|release` 0건, `AbortController|AbortSignal` 0건, `Promise.race|all|allSettled` 0건 (test 만 1건), listener 0건, microtask scheduling 0건이다. 즉 snapshot 무효화를 막을 동기화 메커니즘이 부재하다.

## 발현 메커니즘

```
T0  caller A 가 resolveContextEngine(config{slot="X"}) 호출.
    L546: entry = engines.get("X") 캡처 (sync).
T1  L563: engine = await entry.factory(factoryCtx). microtask yield.
T2  동시에 plugin loader 의 다른 fiber: plugin X install 의 후속 단계 throw.
    plugins/loader.ts:2406 catch → plugins/registry.ts:2932 rollbackPluginGlobalSideEffects
      → clearContextEnginesForOwner("plugin:X")
        → registry.ts:427-435 sync engines.delete("X").
T3  T1 factory promise resolve. caller A 재개. engine valid.
    L578 describeResolvedContextEngineContractError → null.
T4  L601 wrapResolvedContextEngine(engine, { owner: entry.owner }).
    entry.owner === "plugin:X" (stale closure snapshot).
    L326 RESOLVED_CONTEXT_ENGINE_METADATA.set(wrapped, { owner: "plugin:X" }).
T5  caller A 가 wrapped engine 보유.
T6  후속 resolveContextEngineOwnerPluginId(wrapped) → "X" 반환.
    그러나 registry 에는 X 없음.
```

## 근본 원인 분석

1. **snapshot freshness 미보장**: `entry = engines.get(engineId)` 결과를 await 이후에도 재검증 없이 사용. L601 의 `entry.owner` 를 revalidate 하는 패턴 (예: `engines.get(engineId)?.owner ?? entry.owner`) 또는 version stamp 부재.

2. **lock primitive 부재**: registry mutation (set L395 / delete L432) 과 resolution snapshot (get L546) 이 서로 다른 microtask 에 걸칠 수 있는데도 동기화 primitive 0건. context-engine 전체가 "각 함수 자체가 동기" atomicity 에 의존하지만 resolveContextEngine 만 명시적 await 보유.

3. **clearContextEnginesForOwner 외부 호출 시점 가정 불명확**: 2026-04-17 `59d07f0ab4` 로 추가됐고 plugins/registry.ts:2932 rollback path 에 연결. 도입 시 "rollback 은 부팅 단계 partial failure 만" 이라는 implicit 가정. plugin lazy-install / hot-reload 가 활성화되면 동일 helper 가 런타임에 호출되어 resolve 와 동시 발생 가능.

4. **metadata 영구 attach**: `wrapResolvedContextEngine` 가 WeakMap 에 1회 set 하면 update 경로 없음. wrapped engine 인스턴스가 caller pin 되어 있는 한 stale owner 영구 보유.

## 영향

- **lifecycle attribution 손상**: `pi-embedded-runner/run.ts:1070`, `context-engine-maintenance.ts:355`, `compact.queued.ts:99`, `run/attempt.ts:934` 등이 `resolveContextEngineOwnerPluginId` 결과를 metric/logging/maintenance attribution 에 사용. stale pluginId 노출 시:
  - 운영자가 "plugin X 가 동작 중" 으로 오해 (이미 unregister 됐음에도).
  - context-engine-maintenance 가 unregister 된 plugin context 로 maintenance 호출 → downstream lookup 실패 시 warn 노이즈.
- **재현성**: plugin install partial failure rollback (catch 블록) 과 동일 slot 의 resolveContextEngine 동시 활성화 필요. 부팅 단계에서 plugin 초기화 후 session 시작 일반 흐름에서는 거의 발생 안 함. plugin lazy-install / on-demand install / hot-reload 활성 시 발생 가능.
- **severity P3 (hygienic)**: wrapped engine 자체 valid. metadata 정확성/추적성만 손상. functional 동작 (compact/ingest/assemble) 정상.

## 반증 탐색

### R-3 Grep 결과

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/context-engine/
  → types.ts:85 "released" — SubagentEndReason enum string. lock 의미 아님.
  → lock primitive match 없음.

rg -n "AbortController|AbortSignal" src/context-engine/
  → match 없음.

rg -n "Promise.race|Promise.all|Promise.allSettled" src/context-engine/
  → context-engine.test.ts:1202 (test 만). production 0건.

rg -n "once\(|prependOnceListener|removeAllListeners" src/context-engine/
  → match 없음.

rg -n "setImmediate|queueMicrotask|process.nextTick" src/context-engine/
  → match 없음.
```

방어 메커니즘 부재 확인.

### R-5 실행 조건 분류 표

| 경로 | 작용 | 가드 | 실행 조건 분류 |
|---|---|---|---|
| registerContextEngineForOwner (L374-397) | sync get→check→set | 함수 동기 → microtask atomic | unconditional sync atomic |
| clearContextEnginesForOwner (L427-435) | sync entries 순회 + delete | 함수 동기 → microtask atomic | unconditional sync atomic |
| resolveContextEngine 전반부 (L546-559) | sync entry get + early-return | await 없음 | unconditional sync atomic |
| **resolveContextEngine L546→L601 entry lifetime** | L563 await entry.factory() 중 yield | **재검증 없음 / version stamp 없음** | **unguarded race window** |
| wrapResolvedContextEngine (L321-328) | sync wrap + WeakMap.set | await 없음 | unconditional sync atomic |
| init.ts ensureContextEnginesInitialized (L13-23) | initialized flag check-then-set | 함수 동기 | unconditional sync atomic |
| delegate.ts compactRuntimePromise ??= import() (L11-18) | single expression | ??= 동기 평가 | unconditional sync atomic |
| wrapContextEngineWithSessionKeyCompat isLegacy/rejectedKeys (L278-318) | closure mutable state | invokeWithLegacyCompat 안에서 add/set | race 가능하나 functional bug 없음 (Set add 멱등) |

`resolveContextEngine` 의 entry lifetime 만 unguarded.

### R-7 hot-path vs test-path

- **resolveContextEngine production caller**:
  - src/agents/subagent-registry.ts:325 — subagent spawn.
  - src/agents/pi-embedded-runner/run.ts:1066 — embedded session 시작.
  - src/agents/pi-embedded-runner/compact.queued.ts:63 — compaction queue.
  - 모두 session lifecycle hot-path.
- **clearContextEnginesForOwner production caller**:
  - src/plugins/registry.ts:2932 (rollbackPluginGlobalSideEffects) — plugins/loader.ts:2406 catch 블록.
  - plugin install partial failure rollback.
- 일반 부팅 흐름에서는 session 이 plugin init 완료 후 시작 → 빈도 낮음. plugin lazy-install / hot-reload 시 동시 발생 가능성 증가.
- **기존 테스트 부재**: context-engine.test.ts:1161-1208 의 동시성 테스트는 서로 다른 id 의 concurrent register 만 검증. resolve vs clear 동시 발생은 테스트 0건.

### R-8 upstream 최신성

- HEAD 부근 `git log --since="3 weeks ago" -- src/context-engine/` 검토. race/concurrent/lock/atomic 키워드 새 fix 없음.
- `59d07f0ab4` (2026-04-17) 이 `clearContextEnginesForOwner` 도입 + plugin rollback 연결. 이 시점 이후 본 race window 가 처음 존재. resolveContextEngine 측 snapshot freshness 미검토.
- CAL-008 (open PR 중복 회피): `gh pr list --state open --search "resolveContextEngine snapshot" OR "context-engine race"` → 0 매치.

### R-9 boundary 명료화

- 메인테이너 측 반론 예상: "owner 는 logging 용이고 wrapped engine 자체 정상이니 무해".
- 본 FIND 는 owner metadata 가 단순 로깅이 아닌 lifecycle attribution (context-engine-maintenance.ts:355, run/attempt.ts:934, compact.queued.ts:99) 에 사용된다는 점을 명시. 그러나 functional 동작 자체는 깨지지 않음 → severity P3.

## Self-check

### 내가 확실한 근거

- L546 `entry = engines.get(engineId)` (registry.ts:546, Read 확인).
- L563 `engine = await entry.factory(factoryCtx)` — 명시적 await suspension point.
- L601 `wrapResolvedContextEngine(engine, { owner: entry.owner })` — closure entry 재사용.
- L321-328 `wrapResolvedContextEngine` 가 WeakMap.set 후 update 경로 없음.
- `clearContextEnginesForOwner` 가 plugins/registry.ts:2932 의 rollback path 에서 호출.
- context-engine 전체에 lock/abort/race/listener/microtask 0건.
- upstream HEAD 부근 본 race 에 대한 fix 부재.

### 내가 한 가정

- plugin install 이 "register 성공 → 후속 단계 throw" 의 partial failure 시나리오 진입 가능. plugins/loader.ts:2406 catch 블록 존재로 추정.
- `resolveContextEngineOwnerPluginId` 가 단순 로깅 이상의 lifecycle attribution 으로 사용 (context-engine-maintenance.ts:355, run/attempt.ts:934) — 정확한 lifecycle 동작 미확인 (allowed_paths 외).
- 동시 발생 빈도 추정 "낮음" 은 plugin lifecycle 이 부팅 1회 모델이라는 전제. plugin lazy-install / hot-reload 의 실제 운영 빈도 미측정.

### 확인 안 한 것 중 영향 가능성

- `pi-embedded-runner` 측 (allowed_paths 외) 에서 stale pluginId 가 실제 lifecycle bug 를 유발하는 경로 — agents-runner 도메인 감사 시 함께 검증.
- plugin lazy-install / hot-reload 의 실제 활성화 여부 — plugins 도메인 감사 시 검증.
- `clearContextEnginesForOwner` 의 다른 caller 존재 여부 — 현재 src/plugins/registry.ts:2932 1곳만 확인.
- factory 가 long-running 인 3rd-party engine 의 경우 await window 가 길어져 race 빈도 상승.
