---
id: FIND-context-engine-lifecycle-002
cell: context-engine-lifecycle
title: ensureContextEnginesInitialized flips flag before legacy registration
file: src/context-engine/init.ts
line_range: 13-23
evidence: "```ts\nlet initialized = false;\n\nexport function ensureContextEnginesInitialized():\
  \ void {\n  if (initialized) {\n    return;\n  }\n  initialized = true;\n\n  //\
  \ Always available – safe fallback for the \"legacy\" slot default.\n  registerLegacyContextEngine();\n\
  }\n```\n"
symptom_type: lifecycle-gap
problem: '''`ensureContextEnginesInitialized` 가 `initialized = true` 를 `registerLegacyContextEngine()`

  **이전에** 설정한다 (L19 vs L22). 만약 `registerLegacyContextEngine()` 호출이 어떤 이유로

  throw 하면, throw 가 caller 에 전파되지만 module-level `initialized` flag 는 이미 true 상태로

  남아 있다. 같은 process 의 후속 `ensureContextEnginesInitialized()` 호출은 idempotency guard

  (L16-18) 로 인해 즉시 return → legacy engine 은 영구 미등록. 그 시점에 `resolveContextEngine`

  이 default ("legacy") 슬롯을 해상하려고 시도하면 L548-553 의 `Context engine "legacy" is

  not registered` 에서 throw 가 propagate (default engine 은 fallback 없음, L549) — 게이트웨이의

  turn / spawn / compact path 가 영구 crash.''

  '
mechanism: "'1) gateway 부팅 → pi-embedded-runner/run.ts:1065 또는 subagent-spawn.ts:461,\
  \ compact.queued.ts:56,\n   cli-compaction.ts:208 중 하나가 `ensureContextEnginesInitialized()`\
  \ 첫 호출.\n2) L16 `if (initialized) return` → 초기값 false 라 통과.\n3) L19 `initialized\
  \ = true` 설정.\n4) L22 `registerLegacyContextEngine()` 호출 → 내부에서 `registerContextEngineForOwner(\"\
  legacy\",\n   async () => new LegacyContextEngine(), \"core\", { allowSameOwnerRefresh:\
  \ true })`\n   (legacy.registration.ts:5-7) 실행.\n5) `registerContextEngineForOwner`\
  \ 가 throw 하는 경로 (가능성 1) `requireContextEngineOwner(\"core\")`\n   의 trim 결과가 empty\
  \ — \"core\" literal 이라 0%. (2) `getContextEngineRegistryState().engines`\n   의\
  \ `resolveGlobalSingleton` (shared/global-singleton.ts:4-12) 이 `globalThis` 에 entry\n\
  \   를 set 하는 도중 sealed/frozen Object.defineProperty 등으로 throw — Node.js 표준 환경에서는\n\
  \   0%. (3) 같은 chunk 가 중복 로드되어 두 인스턴스 동시 `ensureContextEnginesInitialized` 진입\n\
  \   시 두 번째가 race 로 첫 번째의 set 을 보지 못한 채 진입 — `allowSameOwnerRefresh: true` 라\n  \
  \ overwrite 로 succeed, throw 안 함.\n6) 즉 throw 경로는 사실상 부재. **그러나** `registerContextEngineForOwner`\
  \ 는 synchronous 이지만\n   향후 리팩터에서 async I/O (예: registry 를 SQLite persistent store\
  \ 로 마이그레이션 — 이 PR\n   이 한 번 시도됐다가 revert 됨, `694ca50e97` \"Revert ... move runtime\
  \ state to SQLite\",\n   2026-05-13) 가 도입되면 throw 가능 surface 가 확장됨. revert 됐다고 해서\
  \ 향후 재시도 없을\n   보장 없음.\n7) throw 시점에 `initialized = true` 이미 set. catch 가 outer\
  \ scope 에서 잡히지 않으면 그 process\n   의 모든 후속 `ensureContextEnginesInitialized()` 호출은\
  \ 즉시 return — legacy 미등록 상태가\n   영구 고착.\n8) 후속 `resolveContextEngine(undefined)`\
  \ → L546 `engines.get(\"legacy\")` undefined → L548 isDefaultEngine\n   분기 → L549-552\
  \ throw `\"Context engine \\\\\"legacy\\\\\" is not registered. Available engines:\
  \ (none)\"`.\n   이 throw 가 caller 에 전파. cli-compaction 의 경우 사용자에게 cryptic error,\
  \ gateway 의\n   경우 turn 실패.'\n"
root_cause_chain:
- why: 왜 idempotency flag 가 register 호출 이전에 set 되는가?
  because: '재진입 방지 (reentrant `ensureContextEnginesInitialized` 호출 — 예: registerLegacyContextEngine
    이 module-load side-effect 로 또 다른 init 을 트리거하는 시나리오) 의도로 보임. 그러나 register 함수의 throw
    가 발생하면 이 ordering 이 "init failed but marked initialized" 의 모순 상태를 만든다. 정상 init
    순서는 (a) register 시도, (b) 성공 시에만 flag set 인데, 현재는 (a) 와 (b) 가 역전.'
  evidence_ref: src/context-engine/init.ts:13-23
- why: 왜 이게 production 에서 발현될 가능성이 낮은데도 기록할 가치가 있는가?
  because: 현재 registerContextEngineForOwner 가 throw 안 한다는 평가는 "현재 코드 기준" 의 사실. upstream
    history 상 registry storage 를 SQLite 로 옮기려는 시도 (f91de52f0d → 694ca50e97 revert,
    2026-05-13) 가 한 번 있었다. 다음 refactor 가 register 를 async/IO-touching 으로 바꾸면 throw
    surface 가 확장되고, 이 ordering bug 는 즉시 활성화. lifecycle gap 의 정의는 "현재 무해해도 부분 init
    잔존 잠재 — 향후 변경에 fragile".
  evidence_ref: 'git: 694ca50e97 Revert "refactor: move runtime state to SQLite"'
- why: 왜 단순한 patch (set 을 register 뒤로 이동) 로 해결되지 않은 채 남아있나?
  because: 본 패턴은 ECMAScript module-level 초기화 idiom 으로 흔히 쓰임 (set-flag-first 으로 재진입
    차단). reviewer 가 "register 가 throw 안 한다" 는 현재 사실을 신뢰하면 ordering 우선순위가 낮아짐. CAL-001
    의 "primary path 에 의존한 cleanup 추론" 의 변형 — primary path 가 throw 안 한다는 가정이 ordering
    결정에 들어감.
  evidence_ref: src/context-engine/init.ts:19,22 (현재 ordering 의 명시적 evidence)
impact_hypothesis: crash
impact_detail: '''정성: 현재 코드 base 에서는 register 가 throw 안 하므로 실효 영향 0. 그러나 (a) 향후 register

  의 async I/O 도입 (revert 된 SQLite 시도와 동질의 변경) 또는 (b) `resolveGlobalSingleton` 의

  globalThis 접근이 보안 sandbox 환경에서 throw 시 본 경로가 활성화. 활성화 시 그 process 는

  영구 legacy 미등록 → 모든 resolveContextEngine() 가 L549 throw → turn / compact / spawn
  /

  CLI 가 동일 메시지로 영구 실패. recovery 는 process restart 만. 빈도/속도 정량 불가 (조건부

  활성). 운영 환경에서 보일 패턴은 cryptic `"Context engine \\"legacy\\" is not registered"`

  로그 + 모든 turn 실패.''

  '
severity: P3
counter_evidence:
  path: src/context-engine/init.ts
  line: '22'
  reason: '''관찰한 반증 후보들과 결과:

    (1) 현재 throw surface: `registerContextEngineForOwner` 가 실제로 throw 하는 경로를 grep

    및 코드 추적으로 확인 → trim 결과 empty 체크 (L362) 만 throw, "core" literal 은 통과. Map.set/get

    은 throw 안 함. 즉 현재 base 에서는 본 FIND 의 trigger 불가.

    (2) 다른 caller 가 retry 로 복구: `ensureContextEnginesInitialized` 의 caller (pi-embedded-runner/run.ts:1065,

    subagent-spawn.ts:461 등) 가 throw 잡고 retry → flag reset 경로 없음. retry 도 idempotency

    guard 로 인해 no-op.

    (3) 다른 곳에서 legacy 등록 가능: `registerLegacyContextEngine` export 가 init.ts 외부에서

    호출되는지 — `rg -n "registerLegacyContextEngine" src/` 결과 init.ts:1,22 + legacy.registration.ts:4

    + context-engine.test.ts:700, 843 만. production code 에서 다시 호출하는 경로 없음.

    (4) primary-path inversion (CAL-001): 정상 path 의 cleanup 이 누락된 게 아니라, **ordering

    자체가 부분 init 을 허용한다는 잠재성 (fragility)**. CAL-001 의 본래 함정 — 정상 경로의 숨은

    cleanup 을 못 본 채 부재로 단정 — 은 본 FIND 에 해당 안 함; 다만 본 FIND 의 활성화 자체가

    primary path (register throw) 에 의존하므로 R-7 production hot-path 미충족. P3 책정 근거.

    (5) 기존 테스트: context-engine.test.ts:1136-1144 의 "ensureContextEnginesInitialized()
    is

    idempotent and registers legacy" 는 정상 path 만 검증. register throw 시 flag 가 true
    인

    채 남는 시나리오는 부재 (`rg -n "ensureContextEnginesInitialized.*throw|throw.*registerLegacy"

    src/context-engine/` → 0 매치).

    (6) upstream-dup (CAL-008): `git log upstream/main --since="6 weeks ago" -- src/context-engine/init.ts`

    → 0 commits (init.ts 는 2026-04-14 마지막 수정 후 변경 없음). 관련 PR 없음.

    (7) confidence 정책: 페르소나 가이드라인이 "borderline P3 도 FIND 기록 OK" 명시. 본 FIND 는

    primary-path inversion 아님 (정상 path 의 cleanup 누락이 아니라 ordering fragility) 이므로

    abandon 사유 미충족.''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
domain_notes_ref: domain-notes/context-engine.md
---
# ensureContextEnginesInitialized flips flag before legacy registration

## 문제

`ensureContextEnginesInitialized` (src/context-engine/init.ts:15-23) 는 module-level boolean `initialized` 를 idempotency guard 로 사용한다. 현재 ordering 은:

1. (L16-18) `if (initialized) return;` — 재진입 차단
2. (L19) `initialized = true;` — flag 우선 set
3. (L22) `registerLegacyContextEngine();` — 실제 등록 작업

이 순서는 "register 가 throw 한 적이 없고 앞으로도 없을 것" 이라는 묵시적 invariant 에 의존한다. invariant 가 깨지면 — register 가 한 번이라도 throw 하면 — `initialized = true` 가 이미 set 된 상태로 throw 가 propagate. 같은 process 의 후속 `ensureContextEnginesInitialized()` 호출은 L17 의 early return 으로 인해 즉시 종료하고 legacy 등록이 절대 일어나지 않는다. 그 process 는 "legacy 엔진 영구 미등록" 상태로 고착되어 모든 `resolveContextEngine()` 가 `"Context engine \"legacy\" is not registered"` 로 실패한다.

## 발현 메커니즘

1. gateway 가 부팅하고 첫 turn / spawn / compact 가 발생. caller (pi-embedded-runner/run.ts:1065, subagent-spawn.ts:461, compact.queued.ts:56, cli-compaction.ts:208 중 하나) 가 `ensureContextEnginesInitialized()` 호출.
2. L16 guard 통과 (initialized=false). L19 에서 `initialized = true`.
3. L22 `registerLegacyContextEngine()` 가 throw. 현재 base 에서 이 throw 는 가능성 0 에 가까움 — 그러나 다음 두 종류의 미래 변경이 trigger surface 를 연다:
   - upstream `f91de52f0d → 694ca50e97 revert` (2026-05-13) 가 보여주듯, registry storage 를 SQLite 등 persistent IO 로 옮기려는 시도가 있었다. 이런 변경이 재시도되면 `registerContextEngineForOwner` 가 async/IO-touching 이 되고 throw 가능 surface (디스크 권한, schema migration, lock contention) 가 활성화.
   - `resolveGlobalSingleton` (shared/global-singleton.ts:4-12) 의 `globalStore[key] = created` 가 보안 sandbox 환경 (frozen globalThis, Object.preventExtensions) 에서 throw 가능. 현재 openclaw 의 production 환경은 Node.js standard 라 0% 이지만, embedder (예: third-party app 이 openclaw 를 embed) 가 globalThis 를 잠그면 활성화.
4. throw 가 caller 의 outer scope 까지 전파. caller 가 retry 해도 next `ensureContextEnginesInitialized()` 는 L17 의 `if (initialized) return` 으로 즉시 종료.
5. legacy engine 은 registry 에 없는 상태. config.plugins.slots.contextEngine 이 미지정/legacy 면 default path 로 `engines.get("legacy")` → undefined → L548 isDefaultEngine 분기 → L549-552 throw `"Context engine \"legacy\" is not registered. Available engines: (none)"`.
6. 이 throw 가 모든 후속 turn / compact / spawn 에서 발생. process 재시작 전까지 복구 불가.

## 근본 원인 분석

1. **`Result<T, E>` 미사용 + flag-first ordering**: openclaw 의 CLAUDE.md 규약은 `Result<T, E>` 와 닫힌 에러 코드를 강조하지만, 본 함수는 throw 와 boolean flag 만으로 init protocol 을 관리. throw 가 발생하면 flag 와 실제 상태 사이의 일관성이 깨진다. flag-after-register ordering 으로 바꾸면 throw 시 flag=false 가 유지되어 다음 시도 재진입 가능.
2. **묵시적 invariant 의존**: 코드 작성 시점 (2026-04-14) 에서는 register 가 throw 안 하므로 ordering 이 무해해 보였다. lifecycle gap 의 정의는 "현재 무해해도 미래의 작은 변경이 활성화시킬 부분 init 잔존" — flag-first ordering 은 정확히 이 카테고리.
3. **재진입 차단 의도와의 충돌**: ordering 을 뒤집어 (register 호출 후 flag set) 했을 때 reentrant 시나리오 (register 가 module-load 중 또 다른 init 트리거) 에서 무한 루프 위험 → 그래서 현 ordering 채택했을 가능성. 그러나 현재 register 가 module side-effect 트리거 안 함 (legacy.registration.ts 만 import). reentrancy 가 실재하지 않는데 flag-first 는 partial-init 위험만 남김.

## 영향

- **현재 base**: 실효 영향 0. register 가 throw 안 함.
- **미래 fragility**: SQLite registry, embedder 보안 sandbox, async I/O 도입 등 변경 시 즉시 활성화.
- **활성화 시**: 그 process 의 모든 resolveContextEngine 가 같은 메시지로 영구 throw. 사용자 관찰: gateway 시작 후 모든 turn 이 즉시 실패하며 로그에 `"Context engine \"legacy\" is not registered"` 가 반복.
- **recovery**: process restart 만. flag 가 module-level 이라 reset 함수 부재 (test 코드는 `chunks[1].resolveContextEngine` 식으로 새 chunk import 로 우회).

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `rg -n "registerLegacyContextEngine" src/` → init.ts:1, 22 + legacy.registration.ts:4 + context-engine.test.ts (테스트만). production code 에서 init.ts 외부에서 다시 등록하는 경로 없음.
- caller (pi-embedded-runner/run.ts:1065 등) 의 retry 가 정상 동작하는지 — retry 도 idempotency guard 로 인해 no-op. 복구 불가.
- `resolveContextEngine` 의 default-engine fallback 자체가 없음 (L549 throw, fallback 없음). 즉 default 가 미등록일 때 우회 경로 부재.

### 기존 테스트 커버리지

- context-engine.test.ts:1136-1144 의 idempotent 테스트는 정상 path 만 검증.
- `rg -n "ensureContextEnginesInitialized.*throw|throw.*registerLegacy" src/context-engine/` → 0 매치.
- register throw 시 flag 가 true 인 채 남는 시나리오는 테스트 없음.

### 호출 빈도 / 경로 활성 여부

- `ensureContextEnginesInitialized` caller 수: production code 4곳 (run.ts:1065, subagent-spawn.ts:461, compact.queued.ts:56, cli-compaction.ts:208) + 다수 test mock. 매 process 첫 호출 시 1회 실행.
- throw 가 실제로 발생할 조건: 현재 base 에서는 부재. 미래 변경에 의존.

### 설정 / feature flag

- feature flag 없음. ordering 자체가 코드 상수.

### Primary-path inversion (CAL-001)

본 FIND 는 "정상 cleanup 의 숨은 존재를 못 본 채 부재로 단정" 형태가 아님 — flag-first ordering 자체가 코드 상 명백. 다만 본 gap 의 **활성화** 가 primary path (register throw) 에 의존하므로 R-7 production hot-path 미충족. confidence P3 책정.

### Hot-path-vs-test-path consistency (CAL-003)

기존 테스트는 register 가 throw 안 하는 fast path 만 검증. 미래 변경 시 production hot-path (register 가 IO touch) 와 test path (mock register) 가 불일치 가능성 — 본 ordering 이 test 통과해도 production 활성화 가능.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/context-engine/init.ts` → 0 commits. init.ts 는 2026-04-14 이후 무변경.
- `gh pr list --search "ensureContextEnginesInitialized"` / `gh issue list --search "context engine legacy not registered"` → 0 건. upstream 미인지.
- 관련 SQLite revert (`694ca50e97`, 2026-05-13) 는 본 ordering 을 직접 건드리지 않았으나 향후 재시도 시 본 gap 의 trigger 가 됨.

## Self-check

### 내가 확실한 근거

- init.ts:13-23 의 전 구간 Read 로 ordering (flag set L19 → register call L22) 확인.
- registerContextEngineForOwner 내부 (registry.ts:374-397) 의 모든 throw surface 추적 → 현재 base 에서 throw 부재 확인.
- context-engine.test.ts:1136-1144 가 정상 path 만 검증 — register throw 시 partial init 테스트 부재.
- caller production code 4곳 확인 (run.ts:1065 등).
- upstream history 상 SQLite migration 시도 (`f91de52f0d`) 와 revert (`694ca50e97`) 확인 — 향후 register async/IO 변경 가능성의 evidence.

### 내가 한 가정

- `resolveGlobalSingleton` 이 standard Node.js 환경에서 throw 안 한다는 가정. 코드 (global-singleton.ts:4-12) 상 단순 `globalThis[key] = ...` 이라 안전. 다만 embedder 환경 가정 미확인.
- caller 가 init throw 를 catch 해서 retry 한다는 가정 — 실제 caller 들의 catch 패턴은 미확인 (allowed_paths 밖). 만약 caller 가 throw 를 그대로 propagate 시켜 process crash 면 본 FIND 의 "영구 미등록" 시나리오는 process restart 와 동시에 해소되어 실효 영향 더 약화.
- 본 FIND 의 활성화 시나리오 (future change in register) 가 합리적 미래 — SQLite migration 의 한 번 시도 + revert 가 evidence. 영구 미시도 가능성도 있어 borderline P3.

### 확인 안 한 것 중 영향 가능성

- `pi-embedded-runner/run.ts:1065` 등 caller 가 throw 를 어떻게 handle 하는지 (allowed_paths 밖) — try/catch 로 잡고 continue 하면 본 FIND 활성화 시 영구 미등록 시나리오. process 즉시 crash 면 외부 supervisor 가 restart → 실효 영향 적음.
- subagent registry 가 `initialized = true` flag 의 chunk-locality 와 어떻게 상호작용 — `resolveGlobalSingleton` 으로 engines Map 은 chunk-shared 이나 `initialized` 는 chunk-local boolean. 즉 chunk A 에서 throw 했어도 chunk B 가 새로 ensureContextEnginesInitialized 호출 가능 — 그러나 chunk B 가 새 LegacyContextEngine 을 등록하면 (chunk-shared Map) 결과적으로 등록됨. multi-chunk 환경이 본 FIND 의 자연 mitigation 일 가능성. context-engine.test.ts:1144-1192 의 chunks 테스트 패턴 참조.
- `allowSameOwnerRefresh: true` 와의 상호작용 — chunk B 가 새로 등록 시 chunk A 의 이전 시도가 partial 로 entry 를 남겼다면 owner mismatch 가능성 (그러나 chunk A 가 throw 한 상태라 entry 자체 부재 — Map.set 은 단일 ops 라 partial set 불가). gap 없음.
