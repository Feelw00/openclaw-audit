---
id: FIND-context-engine-error-boundary-001
cell: context-engine-error-boundary
title: init.ts sets initialized=true before register — partial-state lockout on throw
file: src/context-engine/init.ts
line_range: 13-23
evidence: "```ts\nlet initialized = false;\n\nexport function ensureContextEnginesInitialized():\
  \ void {\n  if (initialized) {\n    return;\n  }\n  initialized = true;\n\n  //\
  \ Always available – safe fallback for the \"legacy\" slot default.\n  registerLegacyContextEngine();\n\
  }\n```\n"
symptom_type: error-boundary-gap
problem: '''`ensureContextEnginesInitialized` (init.ts:15-23) 가 `initialized = true`
  를 L19 에서 set 한

  뒤 L22 에서 `registerLegacyContextEngine()` 을 호출한다. 만일 register 가 throw 하면 throw

  는 caller 로 전파되지만, `initialized` 는 이미 true 로 latch 됨. 이후 동일/다른 caller 가

  `ensureContextEnginesInitialized()` 를 재호출해도 L16 의 early-return 으로 register 가 다시

  시도되지 않는다 — 프로세스 생애 동안 legacy 엔진 미등록 상태가 영구 고착. 결과적으로

  `resolveContextEngine()` 의 fallback path (registry.ts:558, 573, 588, 598) 가 호출하는

  `resolveDefaultContextEngine("legacy", ...)` (registry.ts:614) 가 `defaultEntry ===
  undefined`

  로 영구 throw — 이 경로는 정의상 last-resort fallback 이라 추가 복구 없음.

  현재 코드 경로에서 register throw 의 실재 가능성은 낮으나 (registerLegacyContextEngine →

  registerContextEngineForOwner("legacy", ..., "core", { allowSameOwnerRefresh: true
  }) 는

  owner="core" non-empty 라 requireContextEngineOwner throw 없음, existing 동일 owner 면

  allowSameOwnerRefresh:true 로 overwrite, 다른 owner 면 ok:false return — throw 경로는

  resolveGlobalSingleton 의 frozen globalThis edge 정도), 이 anti-pattern 은 register 함수가

  미래 변경 (예: 새로운 built-in engine 추가, validation 강화) 으로 throw 를 도입하면 즉시

  단방향 lockout 으로 전환된다. CAL-001 적용: 본 init.ts 함수 외에 ``initialized`` 를 reset

  하거나 register 결과를 확인하는 보호 경로 부재 (grep 결과 init.ts 가 유일한 정의).''

  '
mechanism: "'1. 프로세스 부팅: subagent-registry.ts:324 또는 cli-compaction.ts:208 등에서\n \
  \  ensureContextEnginesInitialized() 가 1차 호출.\n2. init.ts:16 `if (initialized) return;`\
  \ → false, 통과.\n3. init.ts:19 `initialized = true;` 설정.\n4. init.ts:22 `registerLegacyContextEngine()`\
  \ 호출.\n   이론적 throw 경로 (현재 코드에서 실제로 발현될 가능성 낮음):\n   (a) resolveGlobalSingleton\
  \ 의 globalThis 접근이 sealed/frozen 환경에서 throw,\n   (b) 미래에 registerContextEngineForOwner\
  \ 가 validation throw 추가 (예: factory 검증).\n5. throw 전파 → caller (subagent-spawn /\
  \ cli-compaction) 의 try/catch 가 흡수하거나\n   상위로 전파. 어느 경우든 init.ts 의 `initialized\
  \ = true` 는 이미 latched.\n6. 이후 동일 또는 다른 chunk 의 caller 가 ensureContextEnginesInitialized()\
  \ 를 재호출.\n   L16 의 early-return 으로 register 미시도 → engines Map 에 \"legacy\" 엔트리 부재.\n\
  7. resolveContextEngine() (registry.ts:546) 의 default 경로:\n   - getContextEngineRegistryState().engines.get(\"\
  legacy\") → undefined.\n   - isDefaultEngine=true → L549 throw \"Context engine\
  \ \\\"legacy\\\" is not registered\".\n   - 또는 plugin engine 이 fallback 으로 resolveDefaultContextEngine\
  \ 호출 시\n     registry.ts:614 `defaultEntry === undefined` → L616 throw.\n8. 모든 후속\
  \ context-engine 의존 경로 (compact, ingest, assemble) 영구 실패. 프로세스\n   restart 외 복구\
  \ 불가 — `initialized` flag 를 false 로 reset 하는 API 부재.'\n"
root_cause_chain:
- why: 왜 initialized 가 register 호출 전에 set 되는가?
  because: '''재귀 방지 의도로 보인다 — registerLegacyContextEngine → (LegacyContextEngine 생성자)
    →

    (delegate.ts import) 중 어딘가가 ensureContextEnginesInitialized() 를 재호출할

    가능성을 차단하려 set-first 패턴 채택. 그러나 현재 코드의 register chain 은

    ensureContextEnginesInitialized 를 콜백하지 않는다 (grep 으로 init 함수가 init.ts

    외에서 호출되는 case 모두 callable-as-deps 패턴). 재귀 방지 필요성 자체가

    현재 코드에서 미입증.''

    '
  evidence_ref: src/context-engine/init.ts:19
- why: 왜 register 실패 시 fallback / retry 가 없는가?
  because: '''`ensureContextEnginesInitialized()` 는 return type 이 void 이고 결과를 보고하지

    않는다. caller (subagent-registry.ts:324, cli-compaction.ts:208) 는 register 성공

    여부를 알 수 없고, `resolveContextEngine()` 호출 시점에서야 throw 로 인지한다.

    registerContextEngineForOwner 자체는 ContextEngineRegistrationResult ({ ok: true
    } |

    { ok: false; existingOwner }) 를 return 하지만, registerLegacyContextEngine

    (legacy.registration.ts:5) 이 이 결과를 무시 (`void` return 으로 캐스팅 없음).

    따라서 ok:false (예: legacy id 가 다른 owner 에게 선점) 경우조차 silent.''

    '
  evidence_ref: src/context-engine/legacy.registration.ts:4-7
- why: 왜 initialized flag 재시도 경로가 없는가?
  because: '''모듈-level `let initialized = false;` 는 unexported. 외부에서 reset 불가능.

    `clearContextEnginesForOwner("core")` 는 engines Map 만 비울 뿐 init flag 와는

    독립이라 reset 효과 없음. 결과: 한 번 partial state 진입 시 process restart 만

    복구 경로.''

    '
  evidence_ref: src/context-engine/init.ts:13
- why: 왜 이 패턴이 design 으로 채택됐는가?
  because: '''비교: registry.ts 의 `engines.set(id, ...)` (L395) 는 register 작업 자체이고

    conditional check (L389-394) 후 단일 set 으로 atomic. init.ts 만 set-flag-then-call

    패턴이라 비대칭이다. 추정: 초기 도입 시 register 가 trivial 했지만 (단순 Map.set

    덮어쓰기) future-proofing 부재. upstream `59d07f0ab4` (2026-04-17) 가

    clearContextEnginesForOwner 를 추가했을 때도 init flag reset 경로는 추가되지

    않았다.''

    '
  evidence_ref: 'git: 59d07f0ab4 fix(plugins): roll back failed register globals (init
    flag 미수정)'
impact_hypothesis: crash
impact_detail: "'정성: 현재 코드 경로 (registerLegacyContextEngine 의 throw 경로 실재성 낮음) 에서는\n\
  발현 빈도 극히 낮음. 그러나 다음 조건에서 발현:\n(a) 미래에 registerLegacyContextEngine 또는 LegacyContextEngine\
  \ 생성자에 validation\n    throw 가 추가됨,\n(b) resolveGlobalSingleton 의 globalThis 접근이\
  \ sealed/frozen 환경 (예: 일부 Worker /\n    VM2 sandbox / Cloudflare-style isolate)\
  \ 에서 실패,\n(c) third-party plugin 이 owner=plugin:X 로 legacy id 를 선점한 후 같은 프로세스에서\n\
  \    ensureContextEnginesInitialized 가 호출되어 ok:false 가 silent return.\n(c) 의 경우\
  \ throw 가 아니지만 register 결과 무시로 인해 \"legacy\" 엔트리가 plugin\n    소유로 남고, 이후 `resolveContextEngine()`\
  \ 가 default(=\"legacy\") 경로에서 plugin\n    factory 를 호출 — 사용자가 의도하지 않은 third-party\
  \ 코드 실행. 단 registry.ts:384-388\n    가 default slot id 에 대해 non-core owner 등록을 거부하므로\
  \ (c) 의 선점 자체는 차단됨.\n최악 시나리오: (a)/(b) 발현 시 모든 후속 resolveContextEngine 영구 throw →\
  \ subagent\nspawn / compact / cli /compact 명령 모두 실패. 프로세스 restart 만 복구. impact 빈도는\n\
  낮으나 impact 깊이는 큼 (전체 context-engine 의존 경로 마비).\n재현 조건 (현재 코드): registerLegacyContextEngine\
  \ 호출 1줄을 throw 로 mock한 단위 테스트\n로 검증 가능. 프로덕션 재현: 위 (a)~(c) 조건 충족 환경 필요.'\n"
severity: P3
counter_evidence:
  path: src/context-engine/init.ts
  line: 13-23
  reason: "'R-3 방어 경로 Grep 결과:\n(1) `rg -n \"try\\s*\\{|catch\\s*\\(|\\.catch\\(\"\
    \ src/context-engine/init.ts` → 0 매치.\n    init.ts 내 register 호출 보호 try/catch\
    \ 부재.\n(2) `rg -n \"initialized\\s*=\" src/context-engine/` → init.ts:13,19 두\
    \ 곳뿐.\n    외부 reset API 없음.\n(3) `rg -n \"ensureContextEnginesInitialized\" src/`\
    \ → 정의 1곳 + caller 7곳\n    (subagent-registry.ts:324, cli-compaction.ts:208, subagent-spawn.runtime.ts:12\
    \ export\n    등). caller 측 try/catch 는 일부 존재 (subagent-spawn.ts:460-484 가 ensureContextEnginesInitialized\n\
    \    + resolveContextEngine 묶음을 try/catch) 하지만, init flag 가 latched 된 후의 재시도\n\
    \    경로 부재 — try/catch 가 흡수해도 동일 프로세스 내 후속 호출은 영구 실패.\n\nR-5 실행 조건 분류:\n- L19\
    \ `initialized = true`: unconditional (정상 flow 에서 1회 set)\n- L22 `registerLegacyContextEngine()`:\
    \ unconditional 1회 호출\n- L16 early-return: 2번째 호출부터 unconditional\nregister 실패에\
    \ 대한 어떤 보호 경로도 unconditional 으로 존재하지 않음.\n\nR-7 production hot-path 검증: caller\
    \ 는 subagent-registry.ts:324 / cli-compaction.ts:208\n이 정규 경로. test-only 가 아님.\n\
    \nPrimary-path inversion (CAL-001): 보호 경로 자체가 부재. silent catch 누락이 아니라\nerror-boundary\
    \ 자체가 없음 → false positive 위험 낮음. 다만 register throw 의 실재성이\n현재 코드에서 매우 낮아 severity\
    \ 를 P3 로 한정.\n\nCAL-004/008 upstream-dup 확인:\n`git log --since=\"6 weeks ago\"\
    \ -- src/context-engine/init.ts` → 0 매치 (init.ts 6주\n무수정). registry.ts 변경 9건 있으나\
    \ init flag 관련 0건.\n`gh pr list --state open --search \"ensureContextEnginesInitialized\
    \ OR initialized partial\ncontext-engine in:title,body\"` → PR #81242 (subagent\
    \ isolation, init 호출 우회 측면) 와\nPR #73161 (Discord 관련, 무관). init.ts 의 flag 패턴 변경\
    \ PR 없음.'\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-14'
cross_refs: []
---
# init.ts sets initialized=true before register — partial-state lockout on throw

## 문제

`src/context-engine/init.ts:15-23` 의 `ensureContextEnginesInitialized()` 함수는 `initialized = true` 를 L19 에서 먼저 set 한 후 L22 에서 `registerLegacyContextEngine()` 을 호출한다. register 가 throw 하면 throw 가 caller 로 전파되는 동시에 `initialized` flag 는 이미 true 로 latch 된다. 이후 동일 또는 다른 caller 가 `ensureContextEnginesInitialized()` 를 재호출해도 L16 의 early-return 으로 register 가 재시도되지 않는다. 결과적으로 프로세스 생애 동안 "legacy" 엔진이 미등록인 채로 영구 lock 되며, `resolveContextEngine()` 의 모든 default fallback 경로가 throw 한다.

현재 코드에서 register 가 throw 할 실재 확률은 낮다 (`requireContextEngineOwner("core")` non-empty, `registerContextEngineForOwner` 는 ok/false-result 패턴 사용). 그러나 anti-pattern 자체가 미래 변경 (생성자 validation 추가, sealed globalThis 환경) 에서 즉시 단방향 lockout 으로 전환된다.

## 발현 메커니즘

```
process boot
  └─ caller#1 (subagent-registry.ts:324) → ensureContextEnginesInitialized()
       ├─ L16 if (initialized) return  → false, 통과
       ├─ L19 initialized = true       ← latched
       └─ L22 registerLegacyContextEngine()
            └─ (hypothetical) throw    ← future validation / sealed globalThis
  throw → caller 로 전파, 그러나 initialized 는 true 인 채로 유지

caller#2 (cli-compaction.ts:208) 또는 retry → ensureContextEnginesInitialized()
  └─ L16 if (initialized) return       ← true 이므로 즉시 return
                                          register 재시도 없음

resolveContextEngine(...) 호출
  └─ getContextEngineRegistryState().engines.get("legacy") → undefined
  └─ isDefaultEngine = true
  └─ registry.ts:549 throw "Context engine \"legacy\" is not registered"
```

비교: `registry.ts:381-396` 의 `registerContextEngineForOwner` 는 ok/false-result 를 명시적으로 반환하며 partial state 를 만들지 않는다. 그러나 `legacy.registration.ts:5-7` 는 이 결과를 무시 (`void` return) — 따라서 register 가 ok:false 를 반환해도 init.ts 는 모르고 `initialized = true` 만 latched.

## 근본 원인 분석

1. **set-flag-before-action 안티패턴**: 정상 패턴은 (a) 작업 수행 → (b) 성공 시에만 flag set, 또는 (a) try/catch 로 flag reset on failure. init.ts 는 둘 다 아닌 set-first.

2. **registration 결과 무시**: `registerLegacyContextEngine` (legacy.registration.ts:4-7) 가 `registerContextEngineForOwner` 의 `ContextEngineRegistrationResult` 를 받아 던지지만 caller 로 전달하지 않는다. ok:false 가 silent 로 흡수되며 init.ts 는 register 가 성공했다고 가정.

3. **재시도 / reset API 부재**: 모듈-level `let initialized = false;` 는 unexported. 외부 코드가 partial state 를 회복할 수 없다. `clearContextEnginesForOwner("core")` 는 engines Map 만 비울 뿐 init flag 와 independent.

4. **upstream 변천**: 2026-04-17 commit `59d07f0ab4` 가 registry 에 `clearContextEnginesForOwner` 추가 + plugin rollback 경로 정비. 그러나 init flag 의 reset 경로는 추가되지 않음 — registry 와 init 의 lifecycle 비대칭 유지.

## 영향

- **impact_hypothesis**: `crash` — 일단 lockout 진입 시 모든 후속 `resolveContextEngine` 이 throw 하여 subagent spawn / `/compact` / cli-compaction 등 context-engine 의존 경로 전체 마비. 프로세스 restart 외 복구 없음.

- **현재 코드의 발현 빈도**: 극히 낮음. registerLegacyContextEngine throw 경로의 실재성이 약함:
  - `requireContextEngineOwner("core")` non-empty 라 throw 없음.
  - `registerContextEngineForOwner` 는 throw 대신 `ok:false` return 패턴.
  - `resolveGlobalSingleton` 의 globalThis 접근은 정상 Node.js 에서 throw 없음.

- **잠재 발현 조건**:
  1. 미래 commit 이 LegacyContextEngine 생성자나 register 경로에 validation throw 도입.
  2. 실행 환경이 sealed/frozen globalThis (Cloudflare Worker, VM2 sandbox 등) 라 `globalStore[key] = created` 가 TypeError.
  3. 동일 chunk 에서 다른 chunk 가 ensureContextEnginesInitialized 를 시도하는 경합 (각 chunk 가 자체 `initialized` flag 보유 — 사실 chunk-local 이므로 이 경우 lockout 은 chunk-local, 다른 chunk 는 정상 register 가능).

- **재현 조건**: 단위 테스트에서 `registerLegacyContextEngine` 을 throw mock + `ensureContextEnginesInitialized` 호출 + 재호출 → 두 번째 호출은 register 미시도 검증.

## 반증 탐색

### R-3 방어 경로 Grep 결과

```
rg -n "try\s*\{|catch\s*\(|\.catch\(" src/context-engine/init.ts
  → 0 매치
rg -n "initialized\s*=" src/context-engine/
  → init.ts:13,19 두 곳 (정의 + set). reset 경로 없음.
rg -n "ensureContextEnginesInitialized" src/
  → init.ts:15 (정의)
  → subagent-registry.ts:324 (호출, try/catch 안)
  → cli-compaction.ts:208 (호출, try/catch 밖)
  → subagent-spawn.runtime.ts:12 (export)
  → subagent-spawn.ts:65 (deps import)
```

### R-5 실행 조건 분류

| 코드 | 경로 | 조건 | 평가 |
|---|---|---|---|
| init.ts:19 `initialized = true` | 정상 flow | unconditional | 1회 set, reset 없음 |
| init.ts:22 register 호출 | 정상 flow | unconditional | register 1회 시도 |
| init.ts:16 early-return | 2회+ 호출 | unconditional | register 재시도 차단 |
| register 실패 보호 경로 | — | **부재** | unconditional 보호 없음 |

### 호출 빈도 / 경로 활성 여부

- `subagent-registry.ts:324`: subagent 등록 시점 (production hot-path).
- `cli-compaction.ts:208`: `/compact` cli 명령 시점 (정규 경로).
- 부팅 시점이 아니라 첫 사용 시점에 lazy init 됨. lockout 진입 후 처음 caller 의 try/catch 가 흡수해도 동일 프로세스의 후속 caller 는 영구 실패.

### Primary-path inversion (CAL-001)

본 케이스는 silent catch 가 아니라 **error-boundary 부재** 다. register throw 가 caller 로 propagate 하므로 명시적이지만, flag 의 set-first 패턴이 retry/recover 가능성을 사후적으로 차단한다. CAL-001 의 함정 (defensive cleanup 못 본 척) 에 해당하지 않음 — 보호 경로 자체가 없음.

### Hot-path-vs-test-path consistency (CAL-003)

`context-engine.test.ts:1137-1141` 가 `ensureContextEnginesInitialized` 의 idempotent 동작만 검증 (`expect(...).toBeUndefined()` 두 번). register throw 시의 partial state 시나리오는 test 부재.

### Upstream-dup check (CAL-004/008)

- `git log --since="6 weeks ago" -- src/context-engine/init.ts` → 0 매치 (init.ts 6주 무수정).
- `git log --since="6 weeks ago" -- src/context-engine/registry.ts` → 9 매치, init flag 관련 0건.
- `gh pr list --state open --search "ensureContextEnginesInitialized OR initialized partial context-engine in:title,body"` → PR #81242 (subagent isolation), #73161 (Discord 무관) — init flag 패턴 변경 PR 0.

### 호출 결과 무시 (legacy.registration.ts)

```ts
// src/context-engine/legacy.registration.ts:4-7
export function registerLegacyContextEngine(): void {
  registerContextEngineForOwner("legacy", async () => new LegacyContextEngine(), "core", {
    allowSameOwnerRefresh: true,
  });
}
```

`registerContextEngineForOwner` 의 `ContextEngineRegistrationResult` 가 무시됨. `ok: false` 발현 조건 (registry.ts:384-394):
- `id === defaultSlotIdForKey("contextEngine")` ("legacy") && owner !== "core" → 본 호출은 owner="core" 라 첫 분기 통과.
- existing && existing.owner !== "core" → 다른 chunk 가 "legacy" 를 plugin owner 로 선점한 경우 (단 위 첫 분기가 차단함).
- existing && !allowSameOwnerRefresh → 본 호출은 allowSameOwnerRefresh:true 라 분기 통과.
- 결론: 현재 코드 경로에서 ok:false 발현 실재성 낮음. 그러나 결과를 보지 않는 패턴 자체가 future-proofing 결여.

## Self-check

### 내가 확실한 근거

- init.ts:13-23 의 코드 본문 (Read 로 직접 확인).
- registry.ts:381-396 `registerContextEngineForOwner` 의 ok/false-result 반환 패턴 (Read 확인).
- legacy.registration.ts:4-7 가 결과를 무시하는 점 (Read 확인).
- ensureContextEnginesInitialized 의 caller 7곳 (rg 결과).
- context-engine.test.ts:1137-1141 가 idempotent 만 검증, partial state 미검증.

### 내가 한 가정

- `registerLegacyContextEngine` 이 throw 할 수 있다는 미래 시나리오는 추정. 현재 코드에서는 throw 경로 실재성 낮음.
- sealed/frozen globalThis 환경에서 `resolveGlobalSingleton` 의 `globalStore[key] = created` 가 throw 한다는 가정은 일반적 ES 사양 (frozen object 에 set 시 strict mode TypeError) 에 근거. openclaw 가 그런 환경에서 실행되는지 검증 안 함.
- chunk-local vs process-global: `initialized` flag 는 모듈-local 변수라 chunk 간 공유 안 됨. `resolveGlobalSingleton` 으로 globalThis 에 저장된 engines Map 과 비대칭. 이 비대칭 자체가 추가 risk 일 수 있으나 본 FIND 의 주축은 아님.

### 확인 안 한 것 중 영향 가능성

- multi-chunk 환경에서 각 chunk 의 `initialized` flag 가 독립이므로, 한 chunk 에서 lockout 발생해도 다른 chunk 가 정상 init 가능. 이 점이 실제 발현을 추가 완화할 수 있으나 사용자 코드에서 어느 chunk 가 호출되는지는 비결정적.
- `subagent-spawn.ts:460` 의 try/catch 가 ensureContextEnginesInitialized + resolveContextEngine 묶음을 처리하지만, 흡수 후 caller 로 status:"error" 반환 시 사용자가 retry 하면 동일 프로세스 내에서 또 실패 — UX 측면에서 silent degradation.
- third-party plugin 이 자체 ContextEngine 을 등록하는 시점이 ensureContextEnginesInitialized 보다 먼저인 경우, plugin 의 register 가 성공해도 legacy fallback 이 영구 부재 → plugin engine resolution 실패 시 graceful degrade 불가능. 본 case 는 grid `context-engine-lifecycle` 셀 hint 와 직접 연관됨 (cross_refs 후보).
