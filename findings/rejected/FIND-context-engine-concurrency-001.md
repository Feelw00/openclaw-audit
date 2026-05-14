---
_parse_error: "while parsing a block mapping\n  in \"<unicode string>\", line 83,\
  \ column 3:\n    - why: 왜 clearContextEnginesForOwn ... \n      ^\nexpected <block\
  \ end>, but found '<scalar>'\n  in \"<unicode string>\", line 84, column 42:\n \
  \    ... : 'clearContextEnginesForOwner' (registry.ts:427-435) 는 동기 함수지만  ... \n\
  \                                         ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: while parsing a block mapping\n  in \"<unicode string>\"\
  , line 83, column 3:\n    - why: 왜 clearContextEnginesForOwn ... \n      ^\nexpected\
  \ <block end>, but found '<scalar>'\n  in \"<unicode string>\", line 84, column\
  \ 42:\n     ... : 'clearContextEnginesForOwner' (registry.ts:427-435) 는 동기 함수지만\
  \  ... \n                                         ^"
---
# resolveContextEngine: entry snapshot leaks stale owner past clear race

## 문제

`resolveContextEngine` (registry.ts:527-602) 은 L546 에서 registry 의 entry 를 동기적으로 캡처한 뒤 L563 에서 plugin factory 를 await 한다. factory 가 resolve 되기까지의 microtask suspension 동안 외부의 다른 비동기 흐름이 `clearContextEnginesForOwner(entry.owner)` 를 호출하여 같은 entry 를 registry 에서 제거할 수 있다 (plugin loader 의 catch 블록에서 발생 — plugins/registry.ts:2932, plugins/loader.ts:2406).

await 가 끝난 뒤 L601 의 `wrapResolvedContextEngine(engine, { owner: entry.owner })` 는 closure 에 잡힌 stale `entry.owner` 를 그대로 사용하여 wrapped engine 의 metadata 를 등록한다. caller 는 valid 한 engine 인스턴스를 받지만 그 owner metadata 는 더 이상 registry 에 존재하지 않는 plugin 의 id 를 가리킨다. `resolveContextEngineOwnerPluginId` 가 이 metadata 를 읽어 lifecycle attribution (pi-embedded-runner/run.ts:1070, context-engine-maintenance.ts:355, run/attempt.ts:934 등) 에 사용한다.

context-engine 도메인 안에는 `Mutex|Semaphore|AsyncLock|acquire|release` 0건, `AbortController|AbortSignal` 0건, `Promise.race|all|allSettled` 0건 (test 만 1건), listener 0건, microtask scheduling 0건이다. 즉 snapshot 무효화를 막을 동기화 메커니즘이 부재하다.

## 발현 메커니즘

```
T0  caller A 가 resolveContextEngine(config{slot="X"}) 호출.
    L546: entry = engines.get("X") → { factory: factoryX, owner: "plugin:X" } 캡처 (sync).
T1  L563: engine = await entry.factory(factoryCtx).
    factory 가 promise 반환 → 함수 yield. caller A 의 microtask 가 queue 에서 대기.

T2  동시에 plugin loader 의 다른 fiber: plugin X 의 install 이 register 이후 단계 (hooks/cli/channels) 에서 throw.
    plugins/loader.ts:2406 catch → plugins/registry.ts:2932 rollbackPluginGlobalSideEffects("X")
      → clearContextEnginesForOwner("plugin:X")
        → registry.ts:427-434 sync delete: engines.delete("X").

T3  T1 의 factory promise resolve. caller A 의 execution 재개.
    engine = ContextEngine 인스턴스 (valid).
    L578: describeResolvedContextEngineContractError(...) → null (contract pass).

T4  L601: wrapResolvedContextEngine(engine, { owner: entry.owner }).
    entry 는 T0 snapshot. entry.owner === "plugin:X" (stale).
    L326: RESOLVED_CONTEXT_ENGINE_METADATA.set(wrapped, { owner: "plugin:X" }).

T5  caller A 가 wrapped engine 받음. agents 도메인이 이를 pin (subagent-registry 등).
T6  후속 'resolveContextEngineOwnerPluginId(wrapped)' 호출 → "X" 반환.
    그러나 registry 에는 X 가 없다.
```

## 근본 원인 분석

1. **snapshot freshness 미보장**: `entry = engines.get(engineId)` 의 결과를 await 이후에도 그대로 사용한다. L601 에서 `entry.owner` 를 다시 검증하거나 (예: `engines.get(engineId)?.owner ?? entry.owner` 같은 패턴) version stamp 를 두지 않는다.

2. **lock primitive 부재**: registry mutation (set L395 / delete L432) 과 resolution snapshot (get L546) 이 서로 다른 microtask 에 걸칠 수 있는데도 동기화 primitive 가 0건. context-engine 전체가 "각 함수 자체가 동기" 라는 atomicity 에 의존하지만 resolveContextEngine 만이 명시적 await 를 가진다.

3. **clearContextEnginesForOwner 의 외부 호출 시점 가정 불명확**: 이 helper 는 2026-04-17 `59d07f0ab4` 로 추가됐고 plugins/registry.ts:2932 의 rollback path 에 연결됐다. 도입 당시 "rollback 은 부팅 단계 partial failure 만" 이라는 implicit 가정이 있었던 듯하다. 그러나 plugin lazy-install / hot-reload 가 활성화되면 동일 helper 가 런타임에 호출되어 resolve 와 동시 발생 가능.

4. **metadata 영구 attach**: `wrapResolvedContextEngine` 가 WeakMap 에 1회 set 하면 update 경로가 없다. wrapped engine 인스턴스가 caller pin 되어 있는 한 stale owner 영구 보유.

## 영향

- **lifecycle attribution 손상**: `pi-embedded-runner/run.ts:1070`, `context-engine-maintenance.ts:355`, `compact.queued.ts:99`, `run/attempt.ts:934` 등이 `resolveContextEngineOwnerPluginId` 결과를 metric/logging/maintenance attribution 에 사용. stale pluginId 가 노출되면:
  - 운영자가 "plugin X 가 동작 중" 으로 오해 (이미 unregister 됐음에도).
  - context-engine-maintenance 가 unregister 된 plugin 의 컨텍스트로 maintenance 호출 시도 → downstream 에서 plugin lookup 실패 시 에러/warn 노이즈.
- **재현성**: plugin install 의 partial failure rollback (catch 블록) 과 동일 slot 의 resolveContextEngine 이 동시에 활성화돼야 함. 부팅 단계에서 plugin 초기화가 끝난 후 session 이 시작되는 일반 흐름에서는 거의 발생 안 함. plugin lazy-install / on-demand install / hot-reload 가 활성 시 발생 가능.
- **severity P3 (hygienic)**: wrapped engine 자체는 valid 하게 동작. metadata 의 정확성/추적성만 손상. functional 동작 (compact/ingest/assemble) 은 정상.

재현 시나리오:
```
1. plugin X 가 'registerContextEngineForOwner("X", factory, "plugin:X")' 성공.
2. plugin X 의 후속 hook 등록이 throw 직전 microtask 에 진입.
3. caller A 가 'resolveContextEngine(config{slot="X"})' 호출 — entry snapshot 캡처 후 factory await.
4. plugin X 의 hook 등록이 throw → loader catch → 'clearContextEnginesForOwner("plugin:X")' 동기 실행 → engines.delete("X").
5. factory await resolve → wrap 진행 → wrapped engine 의 owner = "plugin:X" (stale).
6. caller A 가 wrapped engine 보유. 후속 'resolveContextEngineOwnerPluginId' 는 "X" 반환.
```

## 반증 탐색

### Primary-path inversion (CAL-001 필수)

본 race 가 성립하려면 (a) `entry` 캡처와 사용 사이의 invalidation guard 부재, (b) `clearContextEnginesForOwner` 가 동시 호출 가능, (c) 동기화 primitive 부재 — 셋 모두 충족해야 한다.

- (a) L546 ~ L601 사이 entry 재검증 없음 (Read 확인).
- (b) clearContextEnginesForOwner 의 caller 는 plugins/registry.ts:2932 (`rollbackPluginGlobalSideEffects`), loader.ts:2406 의 catch 블록에서 호출 (`rg -n "clearContextEnginesForOwner" src/` 확인).
- (c) R-3 Grep 5종 모두 0건 (lock/abort/race/listener/microtask).

### R-3 Grep 결과 (명령 + 결과)

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/context-engine/
  → src/context-engine/types.ts:85 "released" — SubagentEndReason enum string 으로 lock 의미 아님.
  → lock primitive **match 없음**.

rg -n "AbortController|AbortSignal|signal\.(abort|addEventListener)" src/context-engine/
  → **match 없음**.

rg -n "Promise\.race\(|Promise\.all\(|Promise\.allSettled\(" src/context-engine/
  → src/context-engine/context-engine.test.ts:1202 (test 의 Promise.all). production source **0건**.

rg -n "once\(|prepend(Once)?Listener\(|removeAllListeners\(" src/context-engine/
  → **match 없음**.

rg -n "setImmediate|queueMicrotask|process\.nextTick" src/context-engine/
  → **match 없음**.
```

방어 메커니즘 부재 확인.

### R-5 실행 조건 분류 표

| 경로 | 작용 | 가드 | 실행 조건 분류 |
|---|---|---|---|
| registerContextEngineForOwner (L374-397) | sync get→check→set | 함수 동기 → microtask atomic | unconditional sync atomic |
| clearContextEnginesForOwner (L427-435) | sync entries 순회 + delete | 함수 동기 → microtask atomic | unconditional sync atomic |
| resolveContextEngine 전반부 (L546-559) | sync entry get + early-return | await 없음 | unconditional sync atomic |
| **resolveContextEngine L546→L601 entry lifetime** | L563 'await entry.factory()' 중 yield | **재검증 없음 / version stamp 없음** | **unguarded race window** |
| wrapResolvedContextEngine (L321-328) | sync wrap + WeakMap.set | await 없음 | unconditional sync atomic |
| init.ts ensureContextEnginesInitialized (L13-23) | initialized flag check-then-set | 함수 동기 (await 없음) | unconditional sync atomic |
| delegate.ts compactRuntimePromise ??= import() (L11-18) | single expression | ??= 동기 평가 | unconditional sync atomic |
| wrapContextEngineWithSessionKeyCompat isLegacy/rejectedKeys (L278-318) | closure mutable state | invokeWithLegacyCompat 안에서 add/set | **race 가능하나 functional bug 없음** (Set add 멱등, retry 가 양쪽 모두 성공) |

`resolveContextEngine` 의 entry lifetime 만 unguarded.

### R-7 hot-path vs test-path

- **resolveContextEngine production caller**:
  - src/agents/subagent-registry.ts:325 — subagent spawn.
  - src/agents/pi-embedded-runner/run.ts:1066 — embedded session 시작.
  - src/agents/pi-embedded-runner/compact.queued.ts:63 — compaction queue.
  - 모두 session lifecycle 의 hot-path.
- **clearContextEnginesForOwner production caller**:
  - src/plugins/registry.ts:2932 (rollbackPluginGlobalSideEffects) — plugins/loader.ts:2406 catch 블록.
  - plugin install partial failure rollback.
- **두 경로의 동시 활성 조건**:
  - plugin 부팅 중 register 까지 성공 → 후속 단계 throw → rollback.
  - 같은 시점에 session caller 가 plugin slot resolve.
  - 일반 부팅 흐름에서는 session 이 plugin init 완료 후에 시작하므로 빈도 낮음.
  - plugin lazy-install / on-demand install / 재설치 흐름에서는 동시 발생 가능성 증가.
- **기존 테스트 부재**:
  - context-engine.test.ts:1161-1208 의 동시성 테스트는 서로 다른 id 의 concurrent register 만 검증.
  - resolve vs clear 동시 발생은 테스트 0건.

### R-8 upstream 최신성

- HEAD 부근 `git log --since="3 weeks ago" -- src/context-engine/` 검토. race/concurrent/lock/atomic 키워드 검색 결과 새 fix 없음.
- `59d07f0ab4` (2026-04-17) 이 `clearContextEnginesForOwner` 도입 + plugin rollback 연결. 이 시점 이후 본 race window 가 처음 존재. resolveContextEngine 측 snapshot freshness 는 미검토.
- CAL-008 (open PR 중복 회피): 'gh pr list --state open --search "resolveContextEngine snapshot"' / "context-engine race" 관련 open PR 없음 확인.

### R-9 boundary 명료화

- 메인테이너 측 반론 예상: "owner 는 logging 용이고 wrapped engine 자체는 정상이니 무해".
- 본 FIND 는 owner metadata 가 단순 로깅이 아닌 lifecycle attribution (context-engine-maintenance.ts:355, run/attempt.ts:934, compact.queued.ts:99) 에서 사용된다는 점을 명시. 그러나 functional 동작 자체는 깨지지 않음 → severity P3.

### 추가 탐색

- **외부 lock 없음** (R-3 확인).
- **AbortController 없음** — factory 가 throw 하면 catch (L564) 가 fallback engine 으로 우회. cancellation 개념 자체 부재.
- **WeakMap 갱신 경로 없음**: `RESOLVED_CONTEXT_ENGINE_METADATA.set` (L326) 외 update API 부재. wrapped engine drop 전까지 metadata stale.
- **단일 caller 전제 코드 주석 없음**: types.ts 및 registry.ts 의 doc 에 "single concurrent resolution" 주석 없음.

## Self-check

### 내가 확실한 근거

- L546 `entry = engines.get(engineId)` (Read 확인, registry.ts:546).
- L563 `engine = await entry.factory(factoryCtx)` — 명시적 await suspension point (Read 확인, registry.ts:563).
- L601 `wrapResolvedContextEngine(engine, { owner: entry.owner })` — closure entry 재사용 (Read 확인, registry.ts:601).
- L321-328 `wrapResolvedContextEngine` 가 WeakMap 에 set 하고 update 경로 없음 (Read 확인).
- `clearContextEnginesForOwner` 가 plugins/registry.ts:2932 의 rollback path 에서 호출 (Read 확인, plugins/registry.ts:2932; Bash rg 확인).
- context-engine 전체에 lock/abort/race/listener/microtask 0건 (R-3 5종 Grep 결과).
- upstream HEAD 부근 본 race 에 대한 fix 부재 (Bash git log 확인).

### 내가 한 가정

- plugin install 이 "register 성공 → 후속 단계 throw" 의 partial failure 시나리오로 진입 가능. plugins/loader.ts:2406 의 catch 블록 존재로 추정. 실제 production 에서 이 시나리오 빈도는 미측정.
- `resolveContextEngineOwnerPluginId` 가 단순 로깅 이상의 lifecycle attribution 으로 사용된다는 가정 — context-engine-maintenance.ts:355, run/attempt.ts:934 에서 metric/log 외에 어떤 lifecycle 동작에 쓰이는지 정확히 미확인 (allowed_paths 외).
- 동시 발생 빈도 추정 "낮음" 은 plugin lifecycle 이 부팅 1회 모델이라는 전제. plugin lazy-install / hot-reload 의 실제 운영 빈도 미측정.

### 확인 안 한 것 중 영향 가능성

- `pi-embedded-runner` 측 (allowed_paths 외) 에서 stale pluginId 가 실제 lifecycle bug 를 유발하는 경로 — agents-runner 도메인 감사 시 함께 검증 필요.
- plugin lazy-install / hot-reload 의 실제 활성화 여부 (config / runtime) — plugins 도메인 감사 시 검증.
- `clearContextEnginesForOwner` 의 다른 caller 존재 여부 — 현재 src/plugins/registry.ts:2932 1곳만 확인. 추가 caller 가 있다면 race 빈도 증가.
- factory 가 long-running (예: DB connection 대기) 인 3rd-party engine 의 경우 await window 가 길어져 race 빈도 상승. 본 분석은 factory 가 빠르게 resolve 한다는 평균 가정.
