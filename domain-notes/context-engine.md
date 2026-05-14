# context-engine 도메인 노트

openclaw 의 `src/context-engine/**` 서브시스템에 대한 영구 관찰 기록. 페르소나/세션별 append-only.

## 도메인 개요

openclaw 의 세션 conversation history 를 assemble / compact / ingest 하는 플러그블 엔진 레이어. 기본 구현(`LegacyContextEngine`) 은 기존 `compactEmbeddedPiSessionDirect` 경로로 위임하며, 3rd-party 플러그인이 플러그인 슬롯(`plugins.slots.contextEngine`) 을 통해 다른 엔진을 등록/교체할 수 있다.

### 파일 구조 (upstream/main @ `22f23fa5ab`, 2026-04-24)

| 파일 | 라인 | 역할 |
|---|---|---|
| `registry.ts` | 557 | module-level singleton Map 기반 엔진 레지스트리 + `registerContextEngineForOwner`, `resolveContextEngine`, `clearContextEnginesForOwner`, sessionKey/prompt 호환 Proxy |
| `delegate.ts` | 101 | `delegateCompactionToRuntime` — 엔진이 compact 알고리즘 공유 시 호출. lazy `compactRuntimePromise ??= import(...)` |
| `init.ts` | 23 | `ensureContextEnginesInitialized` — legacy 엔진 자동 등록 (모듈-level `initialized` 플래그) |
| `legacy.ts` | 87 | `LegacyContextEngine` 클래스 — ingest/assemble/afterTurn no-op, compact 는 `delegateCompactionToRuntime` 로 위임. `dispose()` 는 no-op |
| `legacy.registration.ts` | 8 | `registerContextEngineForOwner("legacy", ..., "core", { allowSameOwnerRefresh: true })` |
| `index.ts` | 26 | public barrel |
| `types.ts` | 267 | ContextEngine 인터페이스 및 관련 타입 |
| `context-engine.test.ts` | 1060+ | 유닛 테스트 |

### 주요 entry point

- `registerContextEngine(id, factory)` / `registerContextEngineForOwner(id, factory, owner, opts)`
- `resolveContextEngine(config)` — 플러그인 슬롯 기반 해상. Proxy 로 wrapping 후 반환
- `clearContextEnginesForOwner(owner)` — owner 단위 cleanup (2026-04-17 `59d07f0ab4` 로 추가)
- `ensureContextEnginesInitialized()` — 부팅 시 legacy 엔진 등록

### caller 요약 (allowed_paths 외, evidence 전용)

- `src/agents/subagent-registry.ts:220`
- `src/agents/subagent-spawn.ts:364`
- `src/agents/pi-embedded-runner/run.ts:613`
- `src/agents/pi-embedded-runner/compact.queued.ts:50`
- `src/plugins/registry.ts:1615` — `clearContextEnginesForOwner(\`plugin:${pluginId}\`)` 호출 (plugin register rollback 경로)

## 메모리 서피스 인벤토리 (fresh upstream @ `22f23fa5ab`)

### Map/Set/WeakMap/WeakSet

| 변수 | 파일:라인 | 쓰기 경로 | 정리 경로 | 키 도메인 / 성장률 |
|---|---|---|---|---|
| `engines` (module-level singleton Map) | `registry.ts:311,325` | `registry.set(id, { factory, owner })` (367) | (a) `registry.delete(id)` (404) in `clearContextEnginesForOwner` — owner 단위. (b) 같은 id 재등록은 덮어쓰기 (367) | 키 = unique engine id. 플러그인 등록 수로 bounded. 같은 id 재등록은 덮어쓰기/거절. 성장률 = 등록된 플러그인 수 |
| `rejectedKeys` (proxy-closure, `wrapContextEngineWithSessionKeyCompat`) | `registry.ts:260` | `rejectedKeys.add(key)` (291) | — | **bounded at 2** — LegacyCompatKey literal 타입은 `sessionKey`/`prompt` 2개. 성장률 0 |
| `rejectedKeys` (function-local, `detectRejectedLegacyCompatKeys`) | `registry.ts:192` | `.add` (195) | 함수 return 시 GC | 함수-local. 누수 아님 |
| `rejectedKeys` / `activeRejectedKeys` (function-local, `invokeWithLegacyCompat`) | `registry.ts:211` | `.add` (229) | 함수 return 시 GC | 함수-local. 누수 아님 |
| `seen` Set (`iterateErrorChain` generator) | `registry.ts:98` | `.add` (100) | generator 소멸 시 GC | 함수-local. 에러 chain 깊이로 bounded |

### setInterval / setTimeout

**없음**. `src/context-engine/` 전수 grep 결과 `setInterval(|setTimeout(|clearInterval|clearTimeout` 매치 0건.

### EventEmitter / addEventListener

**없음**. `\.on\(|\.off\(|removeListener|addEventListener|removeEventListener|once\(|prependListener` 매치 0건.

### Proxy / WeakRef / FinalizationRegistry

- `registry.ts:261` `new Proxy(engine, ...)` — `resolveContextEngine` 반환 경로에서 매 호출마다 새 Proxy 생성.
  - guard: `LEGACY_SESSION_KEY_COMPAT` symbol (L255) — 이미 wrap 된 engine 은 재wrap 하지 않음 (idempotent)
  - Proxy closure: `isLegacy` boolean + `rejectedKeys` Set (bounded at 2)
  - lifetime: Proxy 는 caller 가 보관하는 한 유지. caller 가 drop 하면 target engine + Proxy + closure 모두 GC
- WeakRef/FinalizationRegistry 사용 0건

### lazy `??= import(...)` 패턴

- `delegate.ts:11,16` `compactRuntimePromise ??= import("../agents/pi-embedded-runner/compact.runtime.js")` — 동일 패키지 sibling 모듈. dist 패키징 원자적이라 transient 실패 불가. 기존 `plugins-error-boundary` 의 FIND-003 와 같은 pattern 이나 R-7 (production hot-path) 미충족으로 FIND 금지.

## 각 서피스의 cleanup/TTL/cap 상태 (R-5 execution condition 분류)

| 서피스 | 경로 | 조건 | 평가 |
|---|---|---|---|
| `engines` — same-id overwrite | L367 `registry.set` | `allowSameOwnerRefresh === true` 인 경우 (예: legacy registration) | conditional-same-owner |
| `engines` — owner rollback | L404 `registry.delete(id)` (loop) | `clearContextEnginesForOwner(owner)` 호출 시 (plugin register 실패 시 plugins/registry.ts:1615) | **unconditional for rollback path** |
| `engines` — unload path | 없음 (context-engine 내부) | plugin unload/disable 시 context-engine 내부에서 제거 호출은 없음 | lifecycle scope (plugins 도메인 책임) |
| `rejectedKeys` (proxy-closure) | Set 생성 자체가 upper bound = 2 | — | 크기 bounded by type literal |
| `compactRuntimePromise` | `??=` 한 번만 | — | fail-once-fail-always 이론상 가능, 실제 프로덕션 경로 아님 |

## FIND 생성 금지 근거 (R-5 primary-path inversion)

각 Map/Set 에 대해 "이 누수가 성립하려면 어떤 cleanup 경로가 실패해야 하는가?" 질문:

- **`engines` Map**: 누수 성립 조건 = (a) 같은 id 중복 등록에서 entry 수가 무제한 증가 OR (b) unique id 가 프로세스 생애 동안 무한 증가.
  - (a) 불가: L354-366 에서 existing 이면 early return (`allowSameOwnerRefresh` 경로는 덮어쓰기라 entry 수 증가 없음).
  - (b) 불가: id 는 플러그인 개수 + core("legacy") 로 bounded. 세션당 등록이 아니라 플러그인 load 시점 1회성.
  - 추가 방어: owner rollback path 가 plugins/registry.ts:1615 에서 throw 경로에 연결됨 (upstream 2026-04-17 commit `59d07f0ab4` 로 확립).
- **`rejectedKeys` (proxy-closure)**: 최대 크기 2. 타입 literal 이 추가 growth 불가능.
- **`rejectedKeys` (function-local 2곳)**: 함수 return 시 즉시 GC.
- **Proxy closure**: `LEGACY_SESSION_KEY_COMPAT` symbol guard 로 재wrap 방지. caller 가 engine 을 pin 하는 한 유지는 caller 책임.
- **타이머/리스너 0건**: 해당 카테고리 해당 없음.

## upstream 최근 리팩터 요약 (2026-03-01 이후)

| commit | 날짜 | 내용 | 메모리 의미 |
|---|---|---|---|
| `22201` (`fee91fefce`) | 2026-03-06 | feat: plugin system 으로 custom context management | 기반 구조 도입 |
| `40115` (`4bfa800cc7`) | 2026-03-08 | fix: share context engine registry across bundled chunks | `resolveGlobalSingleton` 도입 — 중복 chunk 간 map 공유 |
| `47595` (`85dd0ab2f8`, `7931f06c00`) | 2026-03-15 | Plugins: reserve/harden context engine ownership | owner 필드 도입, core-owned id 예약 |
| `44779` (`094a0cc412`) | 2026-03-18 | fix: preserve legacy plugin sessionKey interop | `wrapContextEngineWithSessionKeyCompat` Proxy 도입 (rejectedKeys closure 등장) |
| `49061`, `51191`, `47437`, `50848` | 2026-03-17 ~ 21 | feat: compaction delegate, transcript maintenance, modelId, prompt | API 확장 (메모리 의미 중립) |
| `63222` (`2677f7cf14`) | 2026-04-13 | fix: validate resolved context engine contracts | `describeResolvedContextEngineContractError` 도입 — validation gap 방어 |
| `64936` (`f04e045815`), `74f31241ed`, `e26edee39e`, `747b26ea0f` | 2026-04-11 | fix(cycles): cut madge back-edges, lazy-load legacy engine | `delegate.ts` 의 literal `??= import()` pattern 확정 |
| `66930` (`6aa4515798`) | 2026-04-15 | fix: gracefully degrade to legacy on third-party resolution failure | `resolveDefaultContextEngine` 분리 — fallback path 방어 |
| `59d07f0ab4` | 2026-04-17 | fix(plugins): roll back failed register globals | **`clearContextEnginesForOwner` helper 추가** (owner 단위 cleanup API). plugins/registry.ts:1615 에서 catch 블록 rollback. 결과적으로 `engines` Map 의 누수 벡터 1개 봉쇄 |
| `66678` (`263a190fc9`) | 2026-04-20 | Context engine/plugins: accept 3rd-party engines with info.id != slot id | contract validation 완화 — 메모리 의미 중립 |

**핵심 상류 변화**: 2026-04-17 `59d07f0ab4` 로 owner-scoped rollback 이 도입되어 plugin register 실패 경로의 엔진 누적이 차단됨. 이전 세션(CAND-005 → CAL-004)에서 이 영역 PR 이 upstream 에 같은 날 merge 된 바 있음.

## 확인 못 한 영역 (self-critique)

- **allowed_paths 외 caller**: `src/plugins/registry.ts` 의 `unregister*`/`dispose*` 경로가 `clearContextEnginesForOwner` 를 **성공 load 후 unload** 경로에서도 호출하는지 확인 안 함. plugin-lifecycle-auditor 다음 세션에서 검증 권장. 현재 1곳 (L1615) 은 register 실패 rollback 경로로 확인됨.
- **Proxy lifetime**: `resolveContextEngine` 반환 engine 을 caller (subagent-registry, subagent-spawn, pi-embedded-runner) 가 어떻게 보관하는지 미확인. caller 측 memory leak 이 있으면 context-engine 의 Proxy/엔진 인스턴스가 pin 됨. caller 도메인 (agents) 감사 시 함께 검증.
- **dispose contract**: `types.ts` 의 `dispose?(): Promise<void>` optional. 3rd-party 엔진이 dispose 에서 resource (DB connection 등) 를 정리하도록 설계됐지만, caller 가 dispose 를 호출하는 시점 정의 부재. caller 책임 구분이 모호 — 향후 lifecycle-auditor 에서 `resolveContextEngine().dispose` 호출 경로 확인 필요.
- **multi-chunk race**: `resolveGlobalSingleton` 이 chunk 간 공유를 보장하지만, 동시 `ensureContextEnginesInitialized()` 호출이 겹칠 때 `initialized = true` flag 는 chunk-local. 여러 chunk 에서 동시 `registerLegacyContextEngine()` 호출 시 → `allowSameOwnerRefresh: true` 로 safe (덮어쓰기). 실질 경합 없음.

---

## 실행 이력

### memory-leak-hunter (2026-04-24, upstream `22f23fa5ab`)

**셀**: `context-engine-memory` (allowed_paths: `src/context-engine/**`).
**결론**: **FIND 0건**.

**적용 카테고리 (agents/memory-leak-hunter.md §탐지 카테고리)**:

- [x] A. 무제한 자료구조 성장 — 적용 (결과: 없음)
- [x] B. EventEmitter / 리스너 누수 — 적용 (결과: 없음. .on/addEventListener 매치 0건)
- [x] C. 강한 참조 체인 (weak 부재) — 적용 (결과: Proxy 가 engine 을 strong ref 로 잡지만 caller-drop 시 GC. 모듈-level 저장 없음)
- [x] D. 핸들/리소스 누수 — 적용 (결과: fs/http/child_process 사용 없음)
- [x] E. 캐시 TTL 부재 — 적용 (결과: `engines` Map 은 캐시가 아니라 registry. TTL 개념 해당 없음. `compactRuntimePromise` 는 `??= import()` singleton)

**R-3 Grep 결과**:

```
rg -n "new Map\(|new Set\(|new WeakMap\(|new WeakSet\(" src/context-engine/
  → registry.ts:211 (function-local Set), 260 (proxy-closure Set), 325 (engines Map)
  → context-engine.test.ts: test helpers 만
rg -n "setInterval\(|setTimeout\(|clearInterval|clearTimeout" src/context-engine/
  → 0 matches
rg -n "\.on\(|\.off\(|addEventListener|removeEventListener" src/context-engine/
  → 0 matches
rg -n "engines\.(set|delete|clear)" src/context-engine/
  → registry.ts:367 (set), 404 (delete in clearContextEnginesForOwner)
rg -n "clearContextEnginesForOwner" src/
  → context-engine/registry.ts:399 (정의), plugins/registry.ts:11 (import), 1615 (호출 in plugin rollback)
```

**R-8 upstream 최신성 확인**:

`git log upstream/main --since="6 weeks ago" -- src/context-engine/` 결과 25+ commits. 주요 메모리 의미 변경: `59d07f0ab4` (2026-04-17) 가 `clearContextEnginesForOwner` 추가 + plugin register rollback 에 연결. 본 세션의 누수 후보였던 `engines` Map 에 대한 owner-scoped cleanup 경로는 이 시점에 upstream 에서 확립됨.

**R-5 primary-path inversion 결론**:

- `engines` Map: 누수 성립 조건 (a) same-id 증식 (불가 — L354-366 early return) (b) unique id 증식 (불가 — plugin load 시점 1회성, 플러그인 수로 bounded) → **leak 주장 불성립**
- `rejectedKeys` (proxy-closure): 타입 literal 이 upper bound 2 → **growth rate 0**
- `rejectedKeys` (function-local ×2): 함수 return 시 GC → 누수 아님
- `compactRuntimePromise`: `??= import()` sibling module — R-7 production hot-path 미충족 (transient 실패 경로 비현실적)
- Proxy: `LEGACY_SESSION_KEY_COMPAT` guard 로 재wrap 방지. caller-side pinning 은 caller 책임

**R-7 production hot-path 검증**: `resolveContextEngine` 은 heartbeat/subagent spawn/pi-embedded-runner 마다 호출되어 매번 새 engine + Proxy 인스턴스 생성하지만, 반환값이 registry 나 map 에 저장되지 않고 caller 의 함수-local 변수로만 전달 → 정상 사용 시 각 호출이 GC-safe.

**CAL-001 회귀 방지**: `engines` Map 의 cleanup 경로(`clearContextEnginesForOwner`)가 plugin rollback 하나뿐이므로, "unload 시 cleanup 호출됨" 을 내 scope 에서 검증하지 못함. 이 점을 FIND 로 올리려면 allowed_paths 외 plugin unload/disable 경로를 읽어야 함. 본 셀에서는 주장 성립 불가 → FIND 금지.

**CAL-004/CAL-007 회귀 방지**: upstream `22f23fa5ab` (fresh) 기준으로 검증. 2026-04-17 `59d07f0ab4` 로 owner rollback 이 upstream 에 이미 merged. stale base 에서 보았다면 "rollback 부재" 라고 오탐했을 법한 영역을 fresh upstream 에서 확인함.

**CAL-008 회귀 방지**: `gh pr list --state all --search "clearContextEnginesForOwner"` / 최근 6주 log 확인 → 관련 open/duplicate PR 없음 확인.

**자체 한계**:

- plugin unload/disable 시 `clearContextEnginesForOwner` 를 호출하는지 (register rollback 외) 는 `src/plugins/**` scope — 본 세션에서 검증 불가. 호출 부재면 "successfully loaded plugin 이 unload 됐을 때 engines Map 에 stale entry" 가능성이 이론상 존재. 단 openclaw 의 production 경로에 플러그인 dynamic unload 가 실제로 구현됐는지 (또는 process restart 전까지 load-once 모델인지) 미확인.
- `resolveContextEngine` caller 측 engine 보관 패턴 미확인 (agents 도메인). 해당 도메인에서 engine 을 장기 Map 에 저장하면 engine 인스턴스 누적 가능 — 단 그건 caller 측 누수이며 context-engine scope 아님.

**다음 페르소나를 위한 힌트**:

- **plugin-lifecycle-auditor**: `src/plugins/` 에서 plugin unload/disable 시 `clearContextEnginesForOwner(\`plugin:${pluginId}\`)` 호출 경로 확인. 현재 확인된 call site 는 register 실패 rollback 1곳뿐.
- **concurrency-auditor**: 여러 chunk 가 동시 `ensureContextEnginesInitialized()` 를 호출할 때 `initialized = true` race 는 safe (덮어쓰기 허용) 이나, 같은 원리가 3rd-party plugin 에서도 성립하는지 (owner mismatch + race) 확인 가치 있음.
- **agents 도메인 감사 시**: `resolveContextEngine` 결과 engine 을 장기 자료구조 (subagent-registry 등) 에 저장하는 경로가 있다면 caller 측 memory 관찰 필요 — context-engine 자체는 caller-side 참조에 의존.

---

### concurrency-auditor (2026-05-14, upstream HEAD 부근)

**셀**: `context-engine-concurrency` (allowed_paths: `src/context-engine/**`).
**결론**: **FIND 1건** (P3 borderline, hygienic).

**R-3 Grep 5종 결과 (allowed_paths 한정)**:

```
rg -n "Mutex|Semaphore|AsyncLock|acquire|release" src/context-engine/
  → types.ts:85 "released" (SubagentEndReason enum string, lock 의미 아님). lock primitive **0건**.
rg -n "AbortController|AbortSignal|signal\.(abort|addEventListener)" src/context-engine/
  → **0건**.
rg -n "Promise\.race\(|Promise\.all\(|Promise\.allSettled\(" src/context-engine/
  → context-engine.test.ts:1202 (test 의 Promise.all). production source **0건**.
rg -n "once\(|prepend(Once)?Listener\(|removeAllListeners\(" src/context-engine/
  → **0건**.
rg -n "setImmediate|queueMicrotask|process\.nextTick" src/context-engine/
  → **0건**.
```

→ context-engine 전체에 동기화 primitive / cancellation / 명시적 task scheduling **부재**. 모든 함수가 "단일 microtask 안에 끝난다 (동기)" 는 single-thread 모델에 의존.

**적용 카테고리 (agents/concurrency-auditor.md §탐지 카테고리)**:

- [x] A. Shared mutable state async 갱신 race — 적용. 결과: `engines` Map check-then-act (L382-395) 는 함수 전체 동기 → race 불성립. `resolveContextEngine` 의 entry snapshot (L546) → factory await (L563) → entry.owner 재사용 (L601) 만 unguarded → FIND-001.
- [x] B. Promise.race loser — 적용 (결과: 사용 0건).
- [x] C. Listener register-unregister — 적용 (결과: emitter 사용 0건).
- [x] D. AbortController 전파 — 적용 (결과: 사용 0건. factory 가 throw 시 fallback 으로 우회. cancellation 개념 자체 없음).
- [x] E. Microtask/setImmediate ordering — 적용 (결과: 사용 0건. delegate.ts 의 `compactRuntimePromise ??= import()` 는 single sync expression).
- [x] F. Map/Set operation atomicity — 적용. `engines` Map 의 set/delete 는 함수 동기 → 직렬화. 단 `clearContextEnginesForOwner` 호출이 `resolveContextEngine` 의 await 와 겹치면 entry snapshot 이 stale (FIND-001).
- [x] G. Double-dispatch / re-entrance — 적용. `ensureContextEnginesInitialized` (init.ts L13-23) 의 `initialized` flag 는 함수 동기 → 같은 chunk 안에서 재진입 불가. multi-chunk 시 chunk-local 이지만 `allowSameOwnerRefresh: true` 라 functional bug 없음.
- [x] H. Cleanup race — 적용. `RESOLVED_CONTEXT_ENGINE_METADATA` WeakMap 은 GC 기반 cleanup, race 불성립.
- [x] primary-path inversion — 적용. FIND-001 의 counter_evidence 에 명시.
- [x] hot-path vs test-path — 적용. resolveContextEngine 은 session/heartbeat 마다 호출되는 hot-path. clear path 는 plugin install partial-failure rollback 의 catch 블록 (plugins/registry.ts:2932). 두 경로 동시 활성 빈도는 plugin lifecycle 모델 (부팅 1회 vs lazy/hot-reload) 에 의존.

**R-5 실행 조건 분류 표** (cross-domain summary):

| 경로 | await 여부 | 가드 | 평가 |
|---|---|---|---|
| registerContextEngineForOwner | 없음 | 함수 동기 | atomic |
| clearContextEnginesForOwner | 없음 | 함수 동기 | atomic |
| resolveContextEngine 전반부 | 없음 (L546-559) | 함수 동기 | atomic |
| **resolveContextEngine entry lifetime** | **L563 await** | **재검증 없음** | **unguarded race window → FIND-001** |
| wrapResolvedContextEngine | 없음 | 함수 동기 | atomic |
| ensureContextEnginesInitialized | 없음 | 함수 동기 | atomic |
| compactRuntimePromise ??= import() | ??= 자체 동기 | single expression | atomic |
| wrapContextEngineWithSessionKeyCompat closure (isLegacy/rejectedKeys) | invokeWithLegacyCompat 안에 await | Set add 멱등, 양쪽 retry 모두 성공 | race 가능하나 functional bug 없음 → FIND 아님 |

**고려했으나 FIND 부적합 사례**:

- **registerContextEngineForOwner check-then-act (L382-395)**: 함수 전체 동기. microtask suspension 없음. race 불성립.
- **rejectedKeys Set concurrent add (registry.ts:260, invokeWithLegacyCompat 의 활성 호출 path)**: Set.add 멱등. 두 동시 method 호출이 첫 시도 실패 후 양쪽 모두 retry → 둘 다 성공. duplicate work 비용 있으나 functional bug 없음.
- **init.ts `initialized` flag (L13-23)**: 함수 동기. chunk-local 이라 multi-chunk 시 두 chunk 모두 `registerLegacyContextEngine()` 실행 가능하나 `allowSameOwnerRefresh: true` 라 덮어쓰기 OK.
- **delegate.ts `compactRuntimePromise ??= import()` (L11-18)**: `??=` 는 single expression. read+null check+assign 사이 microtask yield 없음. race 불성립.
- **wrapContextEngineWithSessionKeyCompat idempotent guard (L274-275)**: 같은 raw engine 의 동시 wrap 시 두 새 Proxy 생성 가능 (LEGACY_SESSION_KEY_COMPAT 는 Proxy 통과 후에만 true). 그러나 production 경로에서 같은 engine 인스턴스가 동시에 두 번 wrap 되는 시나리오 부재 — `resolveContextEngine` 마다 factory 호출로 새 engine 인스턴스가 매번 생성됨.

**자체 한계**:

- `pi-embedded-runner` 측 (allowed_paths 외) 에서 stale `resolveContextEngineOwnerPluginId` 결과가 단순 metric/log 외에 어떤 lifecycle 동작에 사용되는지 미확인 — agents-runner 도메인 감사 시 검증.
- plugin lazy-install / hot-reload 가 실제 production 에서 활성화되는지 미확인 — plugins-lifecycle 도메인 감사 시 검증. lazy/hot-reload 활성 시 FIND-001 race 빈도 증가.
- factory 가 long-running (DB connection 등) 한 3rd-party engine 의 경우 await window 가 길어져 race 빈도 상승. 본 분석은 평균적인 factory 가정.

**다음 페르소나를 위한 힌트**:

- **plugin-lifecycle-auditor**: `clearContextEnginesForOwner` 의 추가 caller (현재 plugins/registry.ts:2932 외) 가 있는지 확인. unload/disable/reinstall 경로에서 호출되면 본 race 빈도 증가.
- **agents-runner-auditor**: `resolveContextEngineOwnerPluginId` 가 어떤 lifecycle 동작 (단순 metric vs 실제 capability lookup) 에 사용되는지 확인. 실제 lookup 에 사용된다면 FIND-001 severity 상향 (P3 → P2 가능).

---

### plugin-lifecycle-auditor (2026-05-14, upstream `af3d9333aa`)

**셀**: `context-engine-lifecycle` (allowed_paths: `src/context-engine/**`).
**결론**: **FIND 2건** (P3 / P3).

**적용 카테고리 (agents/plugin-lifecycle-auditor.md §탐지 카테고리)**:

- [x] A. Load 실패 rollback 부재 — 적용 (결과: register 자체는 atomic Map.set 이라 partial 잔존 불가; init.ts ordering 의 향후 fragility 는 FIND-002 로 기록)
- [x] B. Dispose / Unload 경로 누락 — 적용 (결과: FIND-001 — contract validation fail 시 instantiated engine 의 dispose 미호출. caller-side dispose 는 fallback path 의 leaked engine 을 보지 못함)
- [x] C. Dynamic import 에러 격리 — 적용 (결과: `delegate.ts` 의 `compactRuntimePromise ??=` 패턴은 sibling chunk 라 transient 실패 surface 부재 — R-7 미충족, FIND 금지)
- [x] D. Manifest parse 실패 후 partial state — N/A (context-engine 에 manifest 개념 없음. 등록은 in-process factory)
- [x] E. Enable / Disable 상태 drift — 적용 (결과: context-engine 내부에 enable/disable 개념 없음. plugin 측 책임 — plugins 도메인 scope)

**R-3 Grep 결과 (lifecycle 축 fresh @ `af3d9333aa`)**:

```
rg -n "dispose|teardown|cleanup|unregister|deregister|destroy" src/context-engine/
  → legacy.ts:84 (LegacyContextEngine.dispose no-op)
  → types.ts:325 (dispose? contract)
  → registry.ts:523 (dispose 안 됨 — 주석만 "Non-default engines that fail... silently replaced")
  → context-engine.test.ts: dispose 테스트만
rg -n "try\s*\{" src/context-engine/
  → registry.ts:239 (invokeWithLegacyCompat), 261 (재시도 inner try), 562 (factory await), 577 (validation 호출)
rg -nP "engine\.dispose|dispose\?\." src/context-engine/registry.ts
  → 0 매치 (registry.ts 내부에 dispose 호출 없음 — FIND-001 의 핵심 evidence)
rg -nP "engine\.dispose|contextEngine\.dispose" src/
  → run.ts:3094 (finally + runAgentCleanupStep), compact.queued.ts:108 (early return), 301 (finally)
rg -n "finally" src/context-engine/
  → context-engine.test.ts:947 (테스트 cleanup만)
```

**R-5 (CAL-001) cleanup execution condition 분류표 — lifecycle 축**:

| 경로 | 위치 | 조건 | 평가 |
|---|---|---|---|
| `engine.dispose?.()` — 정상 종료 | run.ts:3094 (finally + runAgentCleanupStep) | turn 종료 시 unconditional | unconditional (caller-side) |
| `engine.dispose?.()` — compact 완료 | compact.queued.ts:301 (finally) | compact 종료 시 unconditional | unconditional (caller-side) |
| `engine.dispose?.()` — compact harness early return | compact.queued.ts:108 | harness result 분기 | conditional-edge (caller-side) |
| `engine.dispose?.()` — resolveContextEngine fallback (factory throw) | registry.ts:572-574 | **부재** | **gap (FIND-001)** — engine 변수 미할당이라 dispose 부를 객체 없음, factory 책임 |
| `engine.dispose?.()` — resolveContextEngine fallback (validation throw) | registry.ts:587-588 | **부재** | **gap (FIND-001)** — engine 변수 할당됐으나 dispose 호출 없음 |
| `engine.dispose?.()` — resolveContextEngine fallback (contractError) | registry.ts:596-598 | **부재** | **gap (FIND-001)** — 동상 |
| `clearContextEnginesForOwner` — plugin register rollback | plugins/registry.ts:2932 | catch 블록 unconditional | unconditional (plugin 도메인) |
| `clearContextEnginesForOwner` — plugin unload/disable | (확인 안 됨) | plugins 도메인 책임 | **out-of-scope** — plugins-lifecycle 셀에서 확인 필요 |
| `initialized` flag reset on register throw | init.ts:19,22 | **flag-first ordering** | **fragility (FIND-002)** — register throw 시 partial init 고착 |

**R-7 production hot-path 검증**:

- FIND-001: `resolveContextEngine` 의 contract-error path 가 활성화되려면 invalid 3rd-party 엔진이 배포 + 사용돼야 함 — production 환경에서는 edge case. 다만 `resolveContextEngine` 자체는 turn / compact / spawn / CLI 마다 호출되는 hot-path 이므로 한 번 활성화되면 빈도 자체는 매우 높음. P3 책정.
- FIND-002: `ensureContextEnginesInitialized` 의 throw 경로가 활성화되려면 future change (SQLite migration 재시도, embedder sandbox 등) 가 필요. 현재 base 에서는 trigger 부재. confidence 정책상 borderline P3 — primary-path inversion 아니므로 기록.

**CAL-008 upstream 6주 commit 검사 (2026-04-02~2026-05-14)**:

| commit | 내용 | 본 셀 영향 |
|---|---|---|
| `2677f7cf14` (#63222, 2026-04-13) | fix: validate resolved context engine contracts | **FIND-001 의 도입 commit** — contract validation 추가됐으나 dispose 처리 누락 |
| `6aa4515798` (#66930, 2026-04-15) | fix: gracefully degrade to legacy on third-party resolution failure | FIND-001 의 fallback 경로 확립. 그러나 fallback engine cleanup 미추가 |
| `59d07f0ab4` (2026-04-17) | fix(plugins): roll back failed register globals | `clearContextEnginesForOwner` 추가 + plugins/registry.ts 의 register rollback path 와 연결. 본 셀 lifecycle 축에 register-rollback 만 커버, unload 미커버 |
| `263a190fc9` (#66678, 2026-04-20) | accept third-party engines whose info.id differs | contract 완화만 — lifecycle 의미 중립 |
| `d8a600f2ad` (#67243) | pass runtime context to ContextEngineFactory | factory ctx 확장만 |
| `42584964ac` (#74255) | honor assembled prompt authority in precheck | assemble path — lifecycle 무관 |
| `9e1e59717f` (#64294) | feat(plugin-sdk): add LLM completion API | runtimeContext 확장 — lifecycle 무관 |
| `694ca50e97` (2026-05-13) | Revert "refactor: move runtime state to SQLite" | **FIND-002 의 fragility evidence** — registry storage 를 IO 로 옮기려는 시도가 한 번 있었음. 재시도 시 register throw 가능 surface 활성화 |

`gh pr list --search "context engine dispose"` / `gh issue list --search "context engine lifecycle"` → 0 매치. upstream 미인지 영역.

**고려했으나 FIND 부적합 사례**:

- **`allowSameOwnerRefresh: true` 의 in-flight engine 인스턴스 dispose 누락 (registry.ts:395 overwrite)**: registry 가 보관하는 건 factory 만. 인스턴스는 caller-side. 새 factory 등록 시 이전 인스턴스 dispose 호출 의무는 caller (plugin reload 핸들러) 측 — context-engine scope 아님.
- **`LegacyContextEngine.dispose()` no-op (legacy.ts:84-86)**: LegacyContextEngine 은 instance-level state 없음 (`compactRuntimePromise` 는 module-level). 정상.
- **`registerContextEngineForOwner` 의 atomic Map.set**: L395 single op 이라 partial 잔존 불가. 정상.
- **`RESOLVED_CONTEXT_ENGINE_METADATA` WeakMap (L37)**: WeakMap 이라 wrapped engine GC 시 자동 정리. lifecycle gap 아님.
- **`describeResolvedContextEngineContractError` 가 `dispose` 메소드 존재 미검증 (L482-490)**: dispose 가 optional contract 이므로 검증 부재가 정상.

**자체 한계**:

- **plugin unload/disable 시 context-engine 정리 경로 미확인** (`src/plugins/**` 직접 Read 안 함, allowed_paths 외): 본 셀 도메인 노트의 "다음 페르소나를 위한 힌트" 가 지적한 그대로. plugins-lifecycle 셀에서 검증 필요. unload 가 `clearContextEnginesForOwner` 를 호출하지 않으면 "successfully loaded plugin 의 unload 후 engines Map 에 stale entry" 가 lifecycle gap. plugins 도메인 책임.
- **3rd-party context engine 의 실제 factory 구현 미관찰**: factory 가 native resource 잡는다는 가정은 contract 명세 (types.ts:323-325) 기반 plausible scenario. plain-object factory 만 쓰는 환경이면 FIND-001 의 실효 영향 0.
- **caller-side throw handling**: pi-embedded-runner/run.ts:1065 등이 `ensureContextEnginesInitialized` throw 를 어떻게 다루는지 — caller 도메인 (agents) 책임. catch + retry 면 FIND-002 의 lock-in 효과; throw 그대로 propagate 면 process crash → supervisor restart 로 자연 복구.

**다음 페르소나를 위한 힌트**:

- **plugins-lifecycle-auditor** (다음 셀): `clearContextEnginesForOwner` 가 plugin unload / disable / reinstall 경로에서 호출되는지 확인. plugins/registry.ts:2932 (register rollback) 외 호출 site 가 없으면 lifecycle gap (load 성공한 plugin 의 unload 시 stale entry 잔존).
- **agents-runner-auditor**: pi-embedded-runner/run.ts:3094 의 `runAgentCleanupStep` 이 dispose throw 를 어떻게 swallow 하는지 — context engine dispose 가 throw 하면 다른 cleanup 단계가 skip 되는지 확인. cleanup ordering 의 cascading failure 가능성.
- **stability-auditor**: FIND-002 의 fragility 가 실제 발현된 적 있는지 issue/incident 검색. 없으면 P3 유지; 있으면 ordering bug 자체로 활성화 P2 가능.
- **error-boundary-auditor**: `resolveContextEngine` 의 factory await (L563) 가 unhandled rejection 으로 빠지는 경로 (catch 가 L564 에 있어 잡지만 fallback 으로 우회) — fallback 으로 우회 시 caller 가 다른 engine 을 받는다는 점은 caller 가 인지하는가? subagent spawn 경로에서 expected engine != actual engine 일 때 후속 동작 검토 가치 있음.

---

### error-boundary-auditor (2026-05-14, upstream `af3d9333aa`)

**셀**: `context-engine-error-boundary` (allowed_paths: `src/context-engine/**`).
**결론**: **FIND 2건** (P3 / P3 — 둘 다 borderline, future-proofing).

**R-3 방어 경로 Grep (`src/context-engine/`)**:

```
try { / catch ( / .catch(  → registry.ts:239,241,261,263,562,564,577,579  (4 try/catch 쌍)
throw                       → registry.ts:254,364,549,566,581,592,616,624  (8건 boundary 명시)
console.|log.               → registry.ts:554,568,583,595                  (silent fallback 4곳)
void (non-`void 0`)         → init.ts:15, registry.ts:427, legacy.registration.ts:4
                              (모두 함수 return-type 선언, 누락 catch 아님)
process.on(...)            → 0 매치
```

**R-5 silent catch caller 전수조사 (CAL-001)**:

`resolveContextEngine` 의 4개 silent fallback (registry.ts:554/568/583/595) caller 들 — `pi-embedded-runner/run.ts:1066`, `subagent-spawn.ts:462`, `compact.queued.ts:63`, `harness/context-engine-lifecycle.ts:76`, `attempt.ts:934` — 어느 caller 도 fallback 발생 여부를 분기 신호로 활용 안 함. `resolveContextEngineOwnerPluginId(contextEngine) === undefined` 가 간접 신호이긴 하지만 사용자가 plugin slot 을 명시 설정했을 때 silent downgrade 됐다는 사실은 표면화되지 않음. 그러나 **registry.ts:523-525 의 주석에 명시된 의도** ("Non-default engines that fail ... are logged and silently replaced") → 의도된 design → **FIND 금지** (CAL-001 함정 회피).

**카테고리 적용 결과** (agents/error-boundary-auditor.md §탐지 카테고리):

- [x] A. unhandledRejection / uncaughtException — 적용 (process.on 매치 0건. caller 측 의존)
- [x] B. Floating promise — 적용 (void return-type 외 floating 없음. delegate.ts 의 `compactRuntimePromise ??=` 도 await 처리)
- [x] C. JSON.parse 미보호 — 적용 (JSON 사용 0건)
- [x] D. AbortController/AbortSignal 전파 — 적용 (사용 0건. context-engine 자체는 abort 미사용)
- [x] E. fs/network 동기 호출 — 적용 (매치 0건)

**FIND-001 (P3) 요약**: `init.ts:19` 가 `initialized = true` 를 `registerLegacyContextEngine()` (L22) **호출 전** 에 set — set-flag-before-action 안티패턴. register throw 시 영구 partial-state lockout (`initialized=true` latched + legacy engine 미등록). 현재 코드에서 register throw 실재성 낮음 (`registerContextEngineForOwner` 는 throw 대신 ok/false-result 패턴, owner="core" non-empty). 그러나 미래 변경 (validation 추가, sealed globalThis 환경) 시 즉시 활성. `legacy.registration.ts:4-7` 가 register 결과를 무시하는 점도 동일 fragility.

**FIND-002 (P3) 요약**: `invokeWithLegacyCompat` (registry.ts:239-267) 가 unrecognized-key 정규식 매칭 (L128-147, sessionKey/prompt 각 7패턴) 으로 method retry — `compact`/`ingest`/`assemble` 등 side-effect 가능한 메서드가 LLM call 후 응답 파싱 단계에서 throw 한 경우에도 retry 발사 → LLM API 중복 호출. 현재 production 활성 engine 이 LegacyContextEngine 1개 (sessionKey 무시) 이라 발현 0. third-party plugin engine 등장 시 활성. visibility gap: caller 는 retry 발생 인지 불가 (`onLegacyModeDetected`/`onLegacyKeysDetected` 콜백은 wrapper closure 내부 상태만 갱신).

**FIND 후보 제외 (의도된 design / production 미충족)**:

- `resolveContextEngine` 의 silent fallback (registry.ts:554/568/583/595): 주석 L523-525 가 의도 명시. CAL-001 함정 회피 위해 FIND 금지.
- `delegate.ts` 의 try/catch 부재: caller (run.ts:1066, compact.queued.ts:63, subagent-spawn.ts:460) 가 try/catch 로 감싸므로 propagate 가 정상 design.
- `wrapContextEngineWithSessionKeyCompat` 의 silent strip (registry.ts:298-303): isLegacy=true latched 후 sessionKey 자동 제거는 의도된 호환성. 첫 retry 의 side-effect 중복은 FIND-002 가 다룸.
- `delegate.ts` `compactRuntimePromise ??= import()` 의 fail-once-fail-always 이론: 동일 패키지 sibling chunk 라 transient 실패 불가능 — R-7 미충족 (memory-leak-hunter 세션의 동일 결론과 일치).

**R-7 production hot-path 검증**: FIND-001 의 evidence (init.ts) caller 는 subagent-registry.ts:324, cli-compaction.ts:208 등 production 정규 경로. FIND-002 의 wrapper 가 감싸는 메서드 caller 도 cli-compaction.ts:152, pi-embedded-runner/run.ts:1639/1814 등 production 경로. 다만 FIND-002 의 trigger (plugin engine 의 schema validation throw) 는 LegacyContextEngine 만 활성인 현재 production 에서 발현 0.

**CAL-004/008 회귀 방지**:

- `git log --since="6 weeks ago" -- src/context-engine/init.ts` → 0건 (6주 무수정).
- `git log --since="6 weeks ago" -- src/context-engine/registry.ts` → 9건 (메모리/contract validation/info.id mismatch 등). init flag / invokeWithLegacyCompat 관련 수정 0건.
- `gh pr list --state open --search "ensureContextEnginesInitialized OR invokeWithLegacyCompat OR initialized partial context-engine in:title,body"` → PR #81242 (subagent isolation 측면), #73161 (Discord 무관). init flag / retry 패턴 변경 PR 0.

**고려했으나 FIND 부적합 사례 (본 세션 추가)**:

- **`init.ts:22` 의 register 결과 무시**: `legacy.registration.ts` 가 `ContextEngineRegistrationResult` 를 받아 던지지만 caller 로 전달 안 함 (`void` return). 현재 코드에서 ok:false 발현 실재성 낮으나 future-proofing 결여 — FIND-001 의 보조 evidence 로 포함.
- **`describeResolvedContextEngineContractError` (registry.ts:454-497) 의 plain-string error**: Result<T,E> 패턴 (closed error code) 대신 freeform string. CLAUDE.md 규칙 "Result<T, E>, 닫힌 에러 코드 사용 (freeform string 금지)" 와 미세하게 어긋나지만 본 함수는 contract validation 결과 reporting 용 (예외 처리 아님) 이라 규칙 위반 아님 — FIND 금지.

**자체 한계**:

- FIND-002 의 false-positive 시나리오 (사용자 prompt 의 "sessionKey" 토큰이 LLM 응답 에러에 echo) 정량 빈도 미측정. third-party plugin engine 의 실제 구현이 등장하는 시점에 재평가 필요.
- FIND-001 의 lockout 발현 트리거가 현재 코드에서 매우 제한적 — register throw 경로 실재성 분석은 `requireContextEngineOwner` / `registerContextEngineForOwner` 본문 + `resolveGlobalSingleton` 의 globalThis 접근만 살펴봄. LegacyContextEngine 생성자 (legacy.ts:21-87) 자체는 클래스 본문에 side-effect 없음 (instance field 초기화만).
- caller (`subagent-spawn.ts:460-484`, `cli-compaction.ts`) 의 try/catch 가 lockout 후의 사용자 UX 를 어떻게 표면화하는지 — caller 도메인 책임. silent degradation (status:"error" 반환) 패턴이라 사용자가 retry 해도 동일 프로세스에서는 영구 실패.

**다음 페르소나를 위한 힌트**:

- **agents-runner-auditor (다음 셀 후보)**: `pi-embedded-runner/run.ts:1066-1070` 가 `resolveContextEngine` 결과의 silent fallback 을 인지하는 신호 (resolveContextEngineOwnerPluginId === undefined) 를 후속 로직에서 활용하는지 검토. 활용한다면 silent fallback 자체가 functional bug surface — context-engine FIND 후보 재활성. 활용 안 한다면 사용자 plugin 선택이 silent 하게 무시되는 UX gap (P3 위생).
- **plugins-lifecycle-auditor**: 플러그인 register 결과 `{ok: false; existingOwner}` 처리가 적절한지. 본 셀에서 확인한 바: `legacy.registration.ts` 가 결과를 무시 (FIND-001 의 보조 fragility). 다른 register 호출도 결과를 무시하면 동일 fragility.
- **error-boundary-auditor (재방문 시)**: third-party plugin engine 이 production 에 도입되는 시점에 FIND-002 의 정량 빈도 재평가. 그 plugin 의 schema validator 라이브러리 (zod / joi / yup) 와 LLM 응답 파싱 경로의 텍스트 매칭 빈도 측정.
