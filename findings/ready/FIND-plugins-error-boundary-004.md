---
id: FIND-plugins-error-boundary-004
cell: plugins-error-boundary
title: loader activation-only registrations run outside per-plugin try/catch
file: src/plugins/loader.ts
line_range: 2370-2380
evidence: "```ts\n      if (registrationPlan.runFullActivationOnlyRegistrations) {\n\
  \        if (definition?.reload) {\n          registerReload(record, definition.reload);\n\
  \        }\n        for (const nodeHostCommand of definition?.nodeHostCommands ??\
  \ []) {\n          registerNodeHostCommand(record, nodeHostCommand);\n        }\n\
  \        for (const collector of definition?.securityAuditCollectors ?? []) {\n\
  \          registerSecurityAuditCollector(record, collector);\n        }\n     \
  \ }\n```\n"
symptom_type: error-boundary-gap
problem: src/plugins/loader.ts:2370-2380 의 activation-only registration 블록 (registerReload
  / registerNodeHostCommand / registerSecurityAuditCollector) 은 per-plugin for 루프
  (line 1760) 본문 안에 있지만 어떤 try/catch 로도 감싸여 있지 않다. 같은 루프 본문의 module load (line 2088)
  와 register (line 2419) 는 각각 try/catch 로 격리되지만, 그 사이 구간 (line 2314-2418) 은 무방비다.
  registerReload 는 src/plugins/registry.ts:1393-1400 에서 plugin 이 export 한 definition.reload
  의 restartPrefixes / hotPrefixes / noopPrefixes 에 대해 (values ?? []).map((value) =>
  value.trim()) 을 수행하는데, resolvePluginModuleExport (loader.ts:1418-1459) 는 definition
  을 런타임 검증 없이 OpenClawPluginDefinition 로 cast 만 하므로 plugin 이 reload.restartPrefixes
  를 배열이 아닌 truthy 값 (문자열, 객체) 으로 내보내면 values.map 이 TypeError 를 throw 한다. 배열이어도 원소가
  비문자열이면 value.trim 이 throw 한다. 이 sync throw 는 line 2372 호출에서 per-plugin 루프 본문으로 전파되고,
  loadOpenClawPlugins 의 메인 try (line 1582) 는 catch 없이 finally 만 (line 2534) 가지므로 그대로
  loadOpenClawPlugins 밖으로 propagate 한다. 결과적으로 malformed plugin 1개가 (a) 자기 자신뿐 아니라
  루프의 후속 plugin 전체 로딩을 중단시키고 (b) loadOpenClawPlugins 호출자 (gateway startup) 로 예외를 던진다.
mechanism: "1. src/gateway/server-plugins.ts 가 loadOpenClawPlugins({ ..., registrationMode\
  \ })\n   를 gateway startup 에서 호출. mode === 'full' 이면 createRegistrationPlan\n  \
  \ (loader.ts:1078) 가 runFullActivationOnlyRegistrations: true 로 plan 생성.\n2. loadOpenClawPlugins\
  \ 메인 try 진입 (loader.ts:1582). 이 try 는 catch 없이\n   finally (line 2534) 만 가짐.\n3.\
  \ line 1760 for (const candidate of orderedCandidates) per-plugin 루프 진입.\n4. line\
  \ 2088 try/catch 로 plugin module 을 import (module load 격리).\n5. line 2314 resolvePluginModuleExport(mod)\
  \ 로 definition / register resolve.\n   resolvePluginModuleExport (loader.ts:1418-1459)\
  \ 는 resolved 를 def as\n   OpenClawPluginDefinition 로 cast 만 하고 shape 검증을 하지 않음.\
  \ definition.reload\n   / definition.nodeHostCommands 는 plugin module 이 export 한\
  \ raw 런타임 값.\n6. line 2370 if (registrationPlan.runFullActivationOnlyRegistrations)\
  \ 진입\n   (full mode 이므로 true). 이 블록은 어떤 try 로도 감싸여 있지 않음 - 직전 try 는 line\n   2088-2113\
  \ 에서 닫혔고 다음 try 는 line 2419 부터.\n7. line 2371-2372 definition?.reload 가 truthy 이면\
  \ registerReload(record,\n   definition.reload) 호출.\n8. registry.ts:1394-1395 의\
  \ normalize = (values) => (values ?? []).map((value)\n   => value.trim()) 가 restartPrefixes\
  \ 등에 대해 실행.\n   8a. restartPrefixes 가 배열이 아닌 truthy 값 (예: 문자열 'cmd', 객체) 이면\n  \
  \     (values ?? []) 는 nullish 가 아니므로 그대로 유지되고 values.map 이 존재하지\n       않거나 (객체)\
  \ 문자/원소에 대해 .trim() 평가 - TypeError throw.\n   8b. restartPrefixes 가 배열이어도 원소가 비문자열\
  \ (number / null / object) 이면\n       value.trim 이 TypeError throw.\n9. 또는 line\
  \ 2374 for (const nodeHostCommand of definition?.nodeHostCommands ?? [])\n   에서\
  \ nodeHostCommands 가 non-iterable truthy 객체이면 for...of 가 TypeError throw,\n   혹은\
  \ line 2375 registerNodeHostCommand 안의 nodeCommand.command.trim()\n   (registry.ts:1428)\
  \ 이 command 가 비문자열일 때 throw.\n10. throw 가 line 2372 호출 frame 에서 per-plugin 루프 본문으로\
  \ 전파. 루프 본문에\n    이를 잡는 try 없음 -> for 루프 자체가 중단 (후속 candidate 미처리).\n11. throw 가\
  \ loadOpenClawPlugins 메인 try (line 1582) 로 전파. catch 절 부재,\n    finally (line 2534)\
  \ 만 실행 후 예외 재전파.\n12. loadOpenClawPlugins 가 throw 로 반환 -> gateway startup 의 plugin\
  \ 로딩 단계가\n    예외로 abort.\n"
root_cause_chain:
- why: 왜 activation-only registration 블록이 try/catch 밖에 있는가?
  because: per-plugin 루프 본문은 module load (line 2088) 와 register (line 2419) 만 개별 try/catch
    로 격리하고, 그 사이의 definition 처리 + activation-only registration 구간 (line 2314-2418)
    은 어떤 try 로도 감싸지 않음. 루프 본문 전체를 감싸는 try 도 없음 (loop body indentation 의 try 는 2088
    과 2419 뿐).
  evidence_ref: src/plugins/loader.ts:2088-2439
- why: 왜 registerReload 가 비배열 입력에서 throw 하는가?
  because: registry.ts:1394 의 normalize 가 (values ?? []).map((value) => value.trim())
    형태. nullish 만 빈 배열로 대체하고 비배열 truthy 값은 그대로 두어 .map 또는 .trim 호출에서 TypeError 가 난다.
    같은 파일의 registerNodeInvokePolicy (registry.ts:1478) 는 Array.isArray(policy.commands)
    가드를 쓰는데 registerReload 는 동일 가드를 쓰지 않음 - 입력 검증 정책 불일치.
  evidence_ref: src/plugins/registry.ts:1393-1400
- why: 왜 definition 의 reload/nodeHostCommands 가 신뢰할 수 없는 값인가?
  because: resolvePluginModuleExport (loader.ts:1418-1459) 가 plugin module export
    를 resolved as OpenClawPluginDefinition 로 cast 만 하고 reload / nodeHostCommands /securityAuditCollectors
    필드의 런타임 타입을 전혀 검증하지 않음. TypeScript 타입은 런타임 보장이 아님 - plugin 이 잘못된 build 산출물이나 손상된
    export 를 내면 임의 형태가 들어옴.
  evidence_ref: src/plugins/loader.ts:1418-1459
- why: 왜 loadOpenClawPlugins 메인 try 가 이 throw 를 흡수하지 못하는가?
  because: line 1582 의 메인 try 는 catch 절이 없고 line 2534 의 finally 만 가짐. finally 는 cleanup
    만 수행하고 예외를 흡수하지 않으므로 throw 가 loadOpenClawPlugins 밖으로 그대로 전파됨.
  evidence_ref: src/plugins/loader.ts:1582-2536
impact_hypothesis: crash
impact_detail: '정성 - full registrationMode (gateway startup) 에서 definition.reload
  또는 definition.nodeHostCommands 가 잘못된 런타임 형태인 plugin 1개가 loadOpenClawPlugins 전체를
  throw 로 중단시킨다. 같은 루프의 module load / register 실패는 recordPluginError 로 격리되어 다른 plugin
  로딩에 영향이 없는 반면, activation-only registration 단계의 throw 는 (1) 그 plugin 이후의 모든 candidate
  로딩을 건너뛰고 (2) loadOpenClawPlugins 호출자로 예외를 던진다.

  정량 근거.

  - 영향 경로 - registrationMode ''full'' 일 때만 (createRegistrationPlan, loader.ts:1078
  runFullActivationOnlyRegistrations: mode === ''full''). gateway startup 의 server-plugins.ts
  가 full 모드로 호출하므로 production hot path.

  - throw 벡터 3개 - (a) registerReload 의 restartPrefixes/hotPrefixes/noopPrefixes 가
  비배열 truthy 또는 비문자열 원소 (registry.ts:1394), (b) nodeHostCommands 가 non-iterable truthy
  객체 (loader.ts:2374 for...of), (c) nodeHostCommand.command 가 비문자열 (registry.ts:1428
  .trim()).

  - production trigger 실재 - definition.reload 등을 export 하는 것은 reload 기능을 쓰는 plugin
  의 정상 사용. 손상된 build 산출물, 잘못 작성된 plugin, 또는 plugin 업데이트 중 부분적으로 쓰여진 export 로 필드 타입이
  어긋날 수 있음. 단 현재 bundled extension 들이 이 결함을 trigger 하는 malformed reload 를 내보낸다는 직접
  증거는 본 세션에서 확인하지 못했음 (extensions/ 는 allowed_paths 밖). third-party plugin / 손상된 산출물이
  주된 trigger 표면.'
severity: P1
counter_evidence:
  path: src/plugins/loader.ts
  line: 2088-2439
  reason: "R-3 Grep 명령 + 결과.\n(1) awk 'NR>=1760 && NR<=2540 { loop-body indentation\
    \ try/catch }' src/plugins/loader.ts\n    -> per-plugin 루프 본문 (6-space indent)\
    \ 의 try/catch 는 line 2088 (module\n    load) 과 line 2419 (register) 단 2개. activation-only\
    \ registration 블록\n    (line 2370-2380) 은 이 두 try 사이의 무방비 구간 (line 2314-2418)\
    \ 에 위치.\n(2) rg -n 'try \\{|\\} catch|\\} finally' src/plugins/loader.ts (line\
    \ 1582-2536)\n    -> 메인 try 는 line 1582, 대응 종료는 line 2534 의 } finally 뿐. catch\
    \ 절\n    없음. 메인 try 는 throw 를 흡수하지 못함.\n(3) registry.ts:1394 normalize vs registry.ts:1478\
    \ registerNodeInvokePolicy\n    비교 -> 후자는 Array.isArray(policy.commands) 가드 사용,\
    \ 전자 registerReload\n    은 (values ?? []).map 으로 비배열 truthy 미방어.\n(4) resolvePluginModuleExport\
    \ (loader.ts:1418-1459) -> definition 을\n    OpenClawPluginDefinition 로 cast 만,\
    \ reload/nodeHostCommands shape 검증 없음.\nR-5 실행 조건 분류.\n- line 2088 try/catch (module\
    \ load): unconditional, 다른 단계 - activation-only\n  registration throw 를 cover\
    \ 안 함.\n- line 2419 try/catch (register): unconditional, 다른 단계 - line 2370-2380\n\
    \  이후에 시작하므로 cover 안 함.\n- line 1582 메인 try: catch 없음, finally 만 - throw 흡수 불가.\n\
    - 루프 본문 전체를 감싸는 try: 부재 (grep 확인).\nunconditional 방어 중 어느 것도 activation-only registration\
    \ 구간의 sync throw\n를 cover 하지 않음 -> 주장 성립.\nproduction trigger 검토. definition.reload\
    \ 를 export 하는 것은 reload 기능\nplugin 의 정상 동작. malformed 형태의 직접 production 사례는 미확인이나,\
    \ plugin\nexport 의 런타임 타입은 어디서도 검증되지 않으므로 손상된 build 산출물 / 잘못\n작성된 third-party\
    \ plugin 이 합리적 trigger. 정상 plugin 만 있으면 미발현 -\nseverity 는 그래서 P0 가 아닌 P1.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-21'
related_tests:
- src/plugins/loader.test.ts
- src/plugins/loader.runtime-registry.test.ts
- src/plugins/plugin-registry.test.ts
---
# loader activation-only registrations run outside per-plugin try/catch

## 문제

`src/plugins/loader.ts:2370-2380` 의 activation-only registration 블록
(`registerReload` / `registerNodeHostCommand` / `registerSecurityAuditCollector`)
은 per-plugin `for` 루프 (line 1760) 본문 안에 있지만 어떤 try/catch 로도
감싸여 있지 않다.

같은 루프 본문에서 plugin module load (line 2088-2113) 와 register
(line 2419-2439) 는 각각 자체 try/catch 로 격리되어, 실패 시 `recordPluginError`
로 진단만 남기고 다음 plugin 으로 넘어간다. 그러나 그 두 try 사이의 구간
(line 2314-2418) 은 무방비다. 이 구간에서 sync throw 가 나면:

1. per-plugin `for` 루프가 중단되어 후속 candidate 가 모두 로딩되지 않는다.
2. throw 가 `loadOpenClawPlugins` 의 메인 try (line 1582) 로 전파되는데 이 try 는
   `catch` 절 없이 `finally` (line 2534) 만 가지므로 예외가 그대로
   `loadOpenClawPlugins` 밖으로 propagate 된다.

throw 의 발생원은 `registerReload` 다. `src/plugins/registry.ts:1393-1400` 의
`normalize` 는 plugin 이 export 한 `definition.reload` 의
`restartPrefixes`/`hotPrefixes`/`noopPrefixes` 에 대해
`(values ?? []).map((value) => value.trim())` 를 수행한다.
`resolvePluginModuleExport` (loader.ts:1418-1459) 는 `definition` 을 런타임 검증
없이 `OpenClawPluginDefinition` 으로 cast 만 하므로, plugin 이
`reload.restartPrefixes` 를 배열이 아닌 truthy 값으로 내보내면 `values.map` 이
`TypeError` 를 throw 한다 (배열이어도 원소가 비문자열이면 `value.trim` 이 throw).

## 발현 메커니즘

1. `src/gateway/server-plugins.ts` 가 gateway startup 에서 `loadOpenClawPlugins`
   를 `registrationMode: 'full'` 로 호출. `createRegistrationPlan`
   (loader.ts:1078) 이 `runFullActivationOnlyRegistrations: true` 로 plan 생성.
2. `loadOpenClawPlugins` 메인 try 진입 (line 1582). 이 try 는 `catch` 절이 없고
   `finally` (line 2534) 만 가진다.
3. line 1760 `for (const candidate of orderedCandidates)` per-plugin 루프 진입.
4. line 2088 try/catch 로 plugin module import (module load 격리). catch 는
   line 2099-2113, `continue`.
5. line 2314 `resolvePluginModuleExport(mod)` 로 `definition`/`register` resolve.
   `resolvePluginModuleExport` 는 module export 를 `def as OpenClawPluginDefinition`
   로 cast 만 하고 `reload`/`nodeHostCommands`/`securityAuditCollectors` 의 런타임
   타입을 전혀 검증하지 않는다.
6. line 2370 `if (registrationPlan.runFullActivationOnlyRegistrations)` 진입
   (full 모드라서 true). 이 블록은 try 밖이다 - 직전 try 는 line 2113 에서
   닫혔고 다음 try 는 line 2419 부터.
7. line 2371-2372 `definition?.reload` 가 truthy 이면
   `registerReload(record, definition.reload)` 호출.
8. registry.ts:1394-1395 의 `normalize` 가 `restartPrefixes` 등에 적용.
   - `restartPrefixes` 가 비배열 truthy 값 (문자열, 객체) -> `(values ?? [])` 는
     nullish 가 아니라 그대로 유지 -> `values.map` 부재 (객체) 또는 문자/원소
     `.trim()` 에서 `TypeError`.
   - `restartPrefixes` 가 배열이어도 원소가 비문자열 (number/null/object) ->
     `value.trim` 에서 `TypeError`.
9. 대안 throw 벡터.
   - line 2374 `for (const nodeHostCommand of definition?.nodeHostCommands ?? [])`
     에서 `nodeHostCommands` 가 non-iterable truthy 객체이면 `for...of` 가
     `TypeError` throw.
   - line 2375 `registerNodeHostCommand` 안 `nodeCommand.command.trim()`
     (registry.ts:1428) 이 `command` 가 비문자열일 때 throw.
10. throw 가 line 2372 호출 frame 에서 per-plugin 루프 본문으로 전파.
    루프 본문에 이를 잡는 try 없음 -> `for` 루프 자체가 중단되어 후속 candidate
    미처리.
11. throw 가 메인 try (line 1582) 로 전파. `catch` 부재, `finally` (line 2534)
    만 실행 후 예외 재전파.
12. `loadOpenClawPlugins` 가 throw 로 반환 -> gateway 의 plugin 로딩 단계 abort.

## 근본 원인 분석

1. **격리 경계 누락** (`loader.ts:2370-2380`). per-plugin 루프 본문은 module load
   (2088) 와 register (2419) 만 try/catch 로 격리한다. 그 사이의 definition 처리 +
   activation-only registration 구간 (2314-2418) 은 무방비이고, 루프 본문 전체를
   감싸는 try 도 없다.

2. **registerReload 의 입력 검증 부재** (`registry.ts:1394`).
   `normalize = (values) => (values ?? []).map((value) => value.trim())` 는
   nullish 만 빈 배열로 대체하고 비배열 truthy 값은 그대로 둔다. 같은 파일의
   `registerNodeInvokePolicy` (registry.ts:1478) 는
   `Array.isArray(policy.commands) ? ... : []` 가드를 쓰는데 `registerReload` 은
   동일 가드를 쓰지 않는다. 입력 검증 정책 불일치.

3. **definition shape 미검증** (`loader.ts:1418-1459`).
   `resolvePluginModuleExport` 가 plugin module export 를
   `resolved as OpenClawPluginDefinition` 으로 cast 만 하고 필드의 런타임 타입을
   검증하지 않는다. TypeScript 타입은 런타임 보장이 아니다.

4. **메인 try 가 catch 없음** (`loader.ts:1582-2536`). line 1582 의 메인 try 는
   `catch` 절이 없고 line 2534 의 `finally` 만 가진다. `finally` 는 cleanup 만
   하고 예외를 흡수하지 않으므로 throw 가 `loadOpenClawPlugins` 밖으로 전파된다.

## 영향

- **impact_hypothesis**: crash. activation-only registration 단계의 sync throw 가
  `loadOpenClawPlugins` 밖으로 전파되어 gateway 의 plugin 로딩 단계를 abort 시킨다.
- **격리 비대칭**: 같은 루프의 module load / register 실패는 `recordPluginError`
  로 격리되어 다른 plugin 에 영향이 없는 반면, activation-only registration 단계의
  throw 는 (1) 그 plugin 이후 모든 candidate 로딩을 건너뛰고 (2) 호출자로 예외를
  던진다. 즉 하나의 malformed plugin 이 plugin subsystem 전체를 무너뜨린다.
- **재현 시나리오**:
  1. `registrationMode: 'full'` (gateway startup) 로 `loadOpenClawPlugins` 호출.
  2. `definition.reload.restartPrefixes` 가 배열이 아닌 truthy 값 (또는 배열이지만
     비문자열 원소 포함) 인 plugin 이 candidate 에 포함.
  3. line 2372 `registerReload` -> registry.ts:1394 `values.map`/`value.trim` ->
     `TypeError` throw.
  4. throw 가 per-plugin 루프 -> 메인 try (catch 없음) -> `loadOpenClawPlugins`
     밖으로 전파.
- **빈도 / trigger**: `definition.reload` 를 export 하는 것은 reload 기능 plugin 의
  정상 동작이다. 손상된 build 산출물, 잘못 작성된 third-party plugin, plugin
  업데이트 중 부분 기록된 export 가 필드 타입을 어긋나게 만들 수 있다. 단 현재
  bundled extension 이 malformed `reload` 를 내보낸다는 직접 증거는 본 세션에서
  확인하지 못했다 (extensions/ 는 allowed_paths 밖).
- **P1 근거**: crash 가 성립하고 (메인 try 가 catch 없음 확인), full 모드는
  gateway startup hot path 다. 그러나 trigger 가 정상 plugin 이 아닌 malformed
  export 이므로 정상 환경에서는 미발현 - P0 (정상 flow 에서 crash) 에는 못 미친다.

## 반증 탐색

R-3 Grep 명령 + 결과.

1. `awk` 로 per-plugin 루프 본문 (line 1760-2540) 의 loop-body indentation
   (6-space) try/catch 추출
   -> line 2088 (module load), line 2419 (register) 단 2개. activation-only
   registration 블록 (line 2370-2380) 은 이 두 try 사이의 무방비 구간
   (line 2314-2418) 에 위치. 다른 try (2185/2214/2289) 는 setup-channel 분기
   안쪽 (8+ space indent) 이라 이 경로와 무관.
2. `loadOpenClawPlugins` 메인 try (line 1582) 의 대응 종료 탐색
   -> line 2534 `} finally` 뿐. `catch` 절 없음. 메인 try 는 throw 를 흡수 불가.
3. registry.ts:1394 `normalize` vs registry.ts:1478 `registerNodeInvokePolicy`
   비교 -> 후자는 `Array.isArray(policy.commands)` 가드 사용. 전자 `registerReload`
   은 `(values ?? []).map` 으로 비배열 truthy 미방어.
4. `resolvePluginModuleExport` (loader.ts:1418-1459) 본체 Read
   -> `definition` 을 `OpenClawPluginDefinition` 으로 cast 만, `reload` /
   `nodeHostCommands` / `securityAuditCollectors` shape 검증 없음.

R-5 실행 조건 분류.

- line 2088 try/catch (module load): `unconditional`, 다른 단계. activation-only
  registration throw 를 cover 안 함.
- line 2419 try/catch (register): `unconditional`, 다른 단계. line 2370-2380
  이후에 시작하므로 cover 안 함.
- line 1582 메인 try: `catch` 없음, `finally` 만. throw 흡수 불가.
- 루프 본문 전체를 감싸는 try: 부재 (grep 확인).
- `unconditional` 방어 중 어느 것도 activation-only registration 구간의 sync
  throw 를 cover 하지 않음 -> 주장 성립.

production trigger 검토.

- `definition.reload` export 자체는 reload 기능 plugin 의 정상 동작.
- malformed 형태 (`restartPrefixes` 가 비배열) 의 직접 production 사례는 미확인.
- 그러나 plugin export 의 런타임 타입이 어디서도 검증되지 않으므로 손상된 build
  산출물 / 잘못 작성된 third-party plugin 이 합리적 trigger 표면.
- 정상 plugin 만 있는 환경에서는 미발현 - 그래서 severity 는 P0 가 아닌 P1.

## Self-check

### 내가 확실한 근거

- `loader.ts:2370-2380` activation-only registration 블록을 직접 Read. try 밖임
  확인 (loop-body try 는 2088, 2419 뿐).
- `loader.ts:1582` 메인 try 와 `loader.ts:2534` `} finally` 직접 확인. `catch`
  절 부재.
- `registry.ts:1393-1422` `registerReload` 본체 직접 Read.
  `(values ?? []).map((value) => value.trim())` 확인.
- `registry.ts:1424-1469` `registerNodeHostCommand` 직접 Read.
  `nodeCommand.command.trim()` 확인.
- `registry.ts:1478` `registerNodeInvokePolicy` 의 `Array.isArray` 가드 직접
  Read - 같은 파일 내 안전 패턴 대비 확인.
- `loader.ts:1418-1459` `resolvePluginModuleExport` 가 cast 만 하고 shape
  검증 없음 직접 확인.
- `loader.ts:1078` `runFullActivationOnlyRegistrations: mode === 'full'` 확인.

### 내가 한 가정

- `src/gateway/server-plugins.ts` 가 full 모드로 `loadOpenClawPlugins` 를
  호출한다는 점은 grep 으로 caller 목록만 확인했고 (allowed_paths 밖) 세부
  registrationMode 인자는 직접 검증하지 않았다.
- malformed `definition.reload` 를 내보내는 실제 plugin 의 존재. third-party
  plugin / 손상된 산출물은 합리적이나 정량 근거 없음.
- `loadOpenClawPlugins` 호출자 (gateway) 측에 outer try/catch 가 있을 수 있다.
  있더라도 본 FIND 의 핵심 (하나의 malformed plugin 이 후속 plugin 전체 로딩을
  중단) 은 그대로 성립한다.

### 확인 안 한 것 중 영향 가능성

- gateway 가 `loadOpenClawPlugins` throw 를 catch 해서 degraded 모드로 계속
  진행한다면 impact 는 process crash 가 아니라 "plugin subsystem 부분 부재" 로
  하향 가능. 그래도 후속 plugin 로딩 중단은 남는다.
- `definition.nodeHostCommands` / `securityAuditCollectors` 를 쓰는 plugin 빈도.
- `OpenClawPluginReloadRegistration` 을 빌드 타임에 검증하는 manifest schema 가
  존재한다면 trigger 표면이 좁아질 수 있음 - manifest schema 는 export 가 아닌
  `openclaw.plugin.json` 검증이라 별개로 보이나 세부 미확인.
