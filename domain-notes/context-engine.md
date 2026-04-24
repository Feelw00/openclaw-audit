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
