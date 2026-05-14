---
id: FIND-context-engine-error-boundary-002
cell: context-engine-error-boundary
title: invokeWithLegacyCompat retries side-effectful methods on pattern-matched errors
file: src/context-engine/registry.ts
line_range: 239-267
evidence: "```ts\n  try {\n    return await method(currentParams);\n  } catch (error)\
  \ {\n    let currentError = error;\n    while (true) {\n      const rejectedKeys\
  \ = detectRejectedLegacyCompatKeys(currentError, availableKeys);\n      let learnedNewKey\
  \ = false;\n      for (const key of rejectedKeys) {\n        if (!activeRejectedKeys.has(key))\
  \ {\n          activeRejectedKeys.add(key);\n          learnedNewKey = true;\n \
  \       }\n      }\n\n      if (!learnedNewKey) {\n        throw currentError;\n\
  \      }\n\n      opts?.onLegacyModeDetected?.();\n      opts?.onLegacyKeysDetected?.(rejectedKeys);\n\
  \      currentParams = withoutLegacyCompatKeys(params, activeRejectedKeys);\n\n\
  \      try {\n        return await method(currentParams);\n      } catch (retryError)\
  \ {\n        currentError = retryError;\n      }\n    }\n  }\n```\n"
symptom_type: error-boundary-gap
problem: '''`invokeWithLegacyCompat` (registry.ts:220-268) 는 `wrapContextEngineWithSessionKeyCompat`

  Proxy (registry.ts:295-314) 에서 호출되며, plugin context-engine 의 어떤 메서드 (compact /

  ingest / assemble / afterTurn / bootstrap / maintain / ingestBatch) 가 throw 한 error
  에

  "sessionKey" 또는 "prompt" 라는 token 이 포함된 unrecognized-key 패턴 (registry.ts:128-147

  의 7개 정규식 ×2 키 = 14패턴) 으로 매치되면 method 를 한 번 더 호출한다 (key 별로 최대

  2회 retry). 이 retry 는 **method 의 side-effect 가 이미 일어났는지 여부와 무관** 하게

  발사된다 — 예컨대 `compact()` 가 LLM API call 을 보낸 후 응답 파싱 단계에서 "sessionKey" 단어를

  포함한 에러로 throw 하면 retry 가 또 LLM call 을 보낸다. 사용자/플러그인은 단 1회 compact 를

  요청했지만 LLM provider 에는 최대 3 회 (1차 + key=sessionKey retry + key=prompt retry) 발사.

  현재 패턴 매칭은 정확하지만 "unrecognized_keys" 에러 메시지가 LLM 응답 본문이나 다른 무관한

  layer 에 우연히 포함될 가능성이 있고 (예: tool result 가 JSON parsing 실패 시 메시지에

  "sessionKey" 라는 token 이 포함된 사용자 입력 echo), 그럴 경우 retry 가 발현되어 idempotent

  하지 않은 method 가 중복 실행된다.''

  '
mechanism: "'1. caller (예: cli-compaction.ts:152) 가 `contextEngine.compact({ sessionId,\
  \ sessionKey,\n   ... })` 호출. contextEngine 은 wrapContextEngineWithSessionKeyCompat\
  \ 의 Proxy.\n2. Proxy.get → property=\"compact\" 이 SESSION_KEY_COMPAT_METHODS 에 포함됨\
  \ → L295 의\n   함수 반환. caller 가 호출하면 invokeWithLegacyCompat 실행.\n3. L239 `await method(currentParams)`\
  \ — plugin engine 의 compact() 호출. LLM provider\n   에 API 요청 발사. 응답 받고 파싱 / 후처리.\
  \ 어느 단계에서 throw — 에러 객체에\n   \"sessionKey\" 토큰 포함.\n4. L241 catch → detectRejectedLegacyCompatKeys\
  \ 가 에러 chain 을 순회하며 정규식 매칭.\n   L128-137 의 sessionKey 패턴 중 하나라도 매치 → rejectedKeys.add(\"\
  sessionKey\").\n5. L246-251 activeRejectedKeys 에 추가, learnedNewKey=true. L257-258\
  \ 콜백 발사\n   (visibility 일부 확보) — 그러나 콜백은 isLegacy=true 와 rejectedKeys 만 set,\n \
  \  \"이미 호출된 LLM call 이 있다\" 는 신호 없음.\n6. L259 `withoutLegacyCompatKeys` 로 sessionKey\
  \ 제거한 params 재계산.\n7. L262 `await method(currentParams)` — **plugin engine 의 compact()\
  \ 재호출. LLM API\n   2번째 발사**. 이미 1차 호출의 LLM 응답이 도착했지만 그것은 invokeWithLegacyCompat\n\
  \   의 try 블록 내부에서 throw 된 후라 caller 입장에서 보이지 않음. 사용자/플러그인은\n   compact 결과를 1개만 받지만\
  \ LLM provider 에는 2개 요청 청구.\n8. 2번째 호출이 또 \"prompt\" 키로 reject 되면 3번째 호출. allowedKeys\
  \ 가 finite\n   (compact 의 경우 [\"sessionKey\"] L55, 단 1개) 이므로 1회 retry 만 가능. 그러나\n\
  \   `assemble` 은 [\"sessionKey\", \"prompt\"] (L54) 라 최대 2회 retry — LLM call 3 발사.\n\
  9. session-key 토큰이 정상 에러 메시지에 포함되는 가짜 양성 시나리오:\n   - tool execute 가 사용자의 raw error\
  \ 메시지를 그대로 throw 한 경우.\n   - LLM response 의 system error 메시지가 우연히 \"additional property:\
  \ sessionKey\" 같은\n     문장 포함 (다른 schema validation 도구의 일반적 메시지).\n   - JSON 파싱\
  \ 실패 시 raw input 의 echo 가 메시지에 들어가는 경우.\n   이런 양성 매칭은 production 빈도가 낮지만 모두 idempotency-위반\
  \ 결과.'\n"
root_cause_chain:
- why: 왜 retry 가 method 의 side-effect 여부를 무시하는가?
  because: '''본 retry 의 설계 의도는 plugin engine 이 "sessionKey/prompt 미지원" 을 schema

    validation 으로 거부할 때 caller 가 알 필요 없이 호환 모드로 fallback 하는 것.

    그러나 retry 결정 신호 (정규식 매칭) 가 **에러의 시멘틱** 이 아닌 **에러 메시지의

    텍스트 패턴** 에 의존하므로, schema validation 이 실제로 발생한 시점인지 (= side-effect

    이전) 메서드 본문 실행 중인지 (= side-effect 이후) 구분 불가능.''

    '
  evidence_ref: src/context-engine/registry.ts:128-147
- why: 왜 retry 가 wrapper 측이 아니라 engine 측에서 발사되는가?
  because: '''wrapContextEngineWithSessionKeyCompat 의 Proxy 가 모든 SESSION_KEY_COMPAT_METHODS

    를 가로채 invokeWithLegacyCompat 으로 감싼다 (L304-314). plugin engine 의 메서드는

    retry 가 자기 책임이 아니라 wrapper 가 임의로 발사하는 점을 모른다. 따라서 plugin

    엔진이 자체 idempotency / dedup 을 구현했다 해도 wrapper 의 retry 는 동일 sessionId

    + 약간 다른 params (sessionKey 제거) 라 deduplication 키가 다르게 보일 수 있다.''

    '
  evidence_ref: src/context-engine/registry.ts:295-315
- why: 왜 retry 발생 시 caller 에 신호가 없는가?
  because: '''`onLegacyModeDetected` / `onLegacyKeysDetected` 콜백은 wrapper 의 closure
    내부에

    isLegacy / rejectedKeys 를 set 할 뿐 (L305-311), 외부 caller 의 telemetry / billing

    / observability 로는 전파되지 않는다. caller (`cli-compaction.ts`, `pi-embedded-runner/

    compact.queued.ts`) 는 1회 compact 호출에 대해 1개 결과를 받지만, LLM provider

    쪽에는 1~3 회 호출. 비용 회계 (provider usage tracking) 가 caller 쪽 단일 호출

    가정으로 설계됐다면 불일치.''

    '
  evidence_ref: src/context-engine/registry.ts:257-258
- why: 왜 정규식 매칭 자체가 false-positive 위험이 있는가?
  because: '''L128-147 의 패턴은 "sessionKey" 또는 "prompt" 토큰을 single-quote / double-quote
    /

    backtick 으로 감싼 경우 모두 매칭 (예: /[\''"`]sessionKey[\''"`]/). 사용자의 prompt

    안에 "make sure to set the sessionKey" 같은 텍스트가 있고 LLM 응답 파싱 실패 시

    raw response 가 에러 메시지에 echo 되면 매칭 가능. 또한 zod 외의 schema validator

    (Joi, Ajv, yup) 도 같은 메시지 패턴을 사용하므로 plugin 의 의도와 무관한 schema

    validation 이 사용자 input 에 대해 발생해도 매칭.''

    '
  evidence_ref: src/context-engine/registry.ts:128-147
impact_hypothesis: wrong-output
impact_detail: "'정성: false-positive 매칭 발현 시 LLM provider 에 1회 사용자 요청당 최대 3회 API 호출\n\
  (assemble) 또는 2회 (compact). 비용 측면: LLM token 비용 ×2~3. 시간 측면: 사용자가\n관측하는 compact\
  \ 지연 ×2~3 (각 호출이 직렬). idempotency 측면: assemble 은 일반적으로\nread-only 라 영향 작음, ingest\
  \ 는 message persistence side-effect 있어 duplicate write\n위험.\n발현 빈도 추정: schema validator\
  \ 의 unrecognized-key 메시지 패턴이 plugin engine 의\n비-schema-validation 경로 (LLM response\
  \ parse error, tool result format error, etc) 에서\n발생할 확률에 의존. 일반적 plugin 구현에서는 1%\
  \ 미만으로 추정. 다만 false-positive 1\n회당 영구 isLegacy=true latch (L306) 가 발생 → 해당 Proxy\
  \ 인스턴스의 후속 호출들은\n`isLegacy && rejectedKeys.has(key)` 분기 (L298-303) 로 sessionKey\
  \ 가 silent 하게 stripped.\n즉 false-positive 1회 = 해당 engine 인스턴스의 sessionKey 영구 disable.\n\
  재현 조건:\n(1) plugin context engine 이 compact() 안에서 LLM 호출 후 응답 파싱 실패 시 raw error\n\
  \    를 그대로 throw,\n(2) raw error 메시지에 정규식 패턴 매칭되는 문자열이 포함됨 (예: 사용자 prompt 가\n  \
  \  \"sessionKey\" 단어 포함, LLM 응답이 그것을 echo 한 후 parse 실패).\n현재 plugin 생태계가 작아 (legacy\
  \ 외 실제 사용 plugin 미확인) 빈도 매우 낮음. P3.'\n"
severity: P3
counter_evidence:
  path: src/context-engine/registry.ts
  line: 230-237
  reason: "'R-3 방어 경로 Grep + 분석:\n(1) `rg -n \"isLegacy\" src/context-engine/registry.ts`\
    \ → L278,299,306 — Proxy 가 한\n    번 legacy 모드 latched 되면 retry 안 함 (L298-303 의\
    \ early bypass). 즉 idempotency\n    위반은 첫 번째 latch 발생 시 1회만 발생, 이후는 silent stripping.\
    \ 다만 첫 1회의\n    side-effect 중복은 차단 안 됨.\n(2) `rg -n \"availableKeys\" src/context-engine/registry.ts`\
    \ → L231 — `params` 에 실제로\n    sessionKey/prompt 키가 있을 때만 retry 진입. params 에 키\
    \ 없으면 L232 의 early\n    return 으로 retry 경로 자체 차단. caller 가 sessionKey 를 전달하지 않으면\
    \ 안전.\n    그러나 SESSION_KEY_COMPAT_METHODS 의 7개 메서드는 모두 sessionKey 를 옵션으로\n   \
    \ 받음 (compact: L55, assemble: L54 등) — 정상 호출에서는 거의 항상 sessionKey 전달.\n(3) `rg\
    \ -n \"detectRejectedLegacyCompatKeys\" src/context-engine/registry.ts` → 정의 +\n\
    \    2 호출처 (L207, 244). 정규식 매칭은 strict 하지만 false positive 가능성 위 분석.\n(4) `rg -n\
    \ \"while \\\\(true\\\\)|for \\\\(;;\\\\)\" src/context-engine/` → registry.ts:243\
    \ 만\n    매치. 이 루프는 `learnedNewKey === false` 시 break (L253) 되며 allowedKeys 가\n\
    \    max 2 (sessionKey, prompt) 이라 finite. infinite loop 위험 없음.\n\nR-5 실행 조건 분류:\n\
    - L240 1차 method 호출: unconditional (params 에 키 있을 때).\n- L244 detectRejectedLegacyCompatKeys:\
    \ unconditional (catch 시).\n- L253 throw currentError: conditional-edge (매번 같은\
    \ key set 이면 진입).\n- L262 retry method 호출: conditional-edge (새 key 학습 시).\n- L298-303\
    \ silent strip (latched): unconditional (한 번 isLegacy=true 후).\n1차 호출은 정상 동작,\
    \ retry 는 conditional-edge. 따라서 idempotent method 에 대해서는\n안전, side-effectful method\
    \ 에 대해서만 위험. compact/ingest 가 side-effectful.\n\nR-7 production hot-path 검증:\n\
    - compact() caller: cli-compaction.ts:152, pi-embedded-runner/compact.queued.ts:165,\n\
    \  pi-embedded-runner/run.ts:1639/1814 — 모두 production 경로.\n- ingest() caller:\
    \ pi-embedded-runner/tool-result-context-guard.ts:318.\n- 단 현재 production 에서 사용되는\
    \ context engine 은 LegacyContextEngine 1개\n  (legacy.ts) 만 확인. LegacyContextEngine\
    \ 은 sessionKey 를 받기만 하고 무시하므로\n  (compact 가 delegateCompactionToRuntime 으로 forward\
    \ — sessionKey 사용 안 함)\n  schema validation throw 가 발생하지 않아 retry 도 발사 안 됨. 따라서\
    \ 현재\n  production 에서 발현 0. third-party plugin engine 이 등장하고 그 engine 이\n  sessionKey\
    \ 를 zod/joi 로 validate 하는 시점에 발현.\n\nPrimary-path inversion (CAL-001): retry 자체가\
    \ 의도된 boundary mechanism. 본 FIND\n는 retry \"유무\" 가 아니라 retry **조건** 의 너무 넓은 매칭\
    \ + side-effect 처리 부재가\n문제. silent catch 가 아니라 silent retry 라는 점에서 동일 함정 (visibility\
    \ gap).\n\nCAL-003 (Hot-path-vs-test-path):\n`context-engine.test.ts` 의 retry\
    \ 테스트들은 strictEngine (L590,616,643 등) 이 zod-style\nerror 를 던지는 mock — production\
    \ plugin 이 zod 외 validator 를 쓸 가능성 검증 안 됨.\ntest 가 production 패턴을 정확히 반영하므로 CAL-003\
    \ 직접 위반은 아니나, false-positive\n매칭 시나리오 (정규식이 schema validation 외 텍스트에 매치) 는 test\
    \ 부재.\n\nCAL-004/008 upstream-dup 확인:\n`git log --since=\"6 weeks ago\" -- src/context-engine/registry.ts`\
    \ 9건. 메모리 의미\n변경 (59d07f0ab4 clearContextEnginesForOwner, 263a190fc9 info.id mismatch\
    \ 허용) 외\ninvokeWithLegacyCompat / detectRejectedLegacyCompatKeys 관련 수정 0건.\n`gh\
    \ pr list --state open --search \"invokeWithLegacyCompat OR legacyCompat retry\
    \ in:title,body\"`\n→ 매치 0.\n\n개념적 한계 솔직 표기: 본 FIND 는 현재 production 에서는 발현 0 (legacy\
    \ engine 이\nsessionKey 무시). third-party plugin 도입 시점에 활성. 따라서 P3 위생/future-proofing\n\
    범주. severity 상향 근거 부족.'\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-14'
cross_refs: []
---
# invokeWithLegacyCompat retries side-effectful methods on pattern-matched errors

## 문제

`invokeWithLegacyCompat` (registry.ts:220-268) 는 `wrapContextEngineWithSessionKeyCompat` Proxy 가 SESSION_KEY_COMPAT_METHODS (`compact`, `ingest`, `assemble`, `afterTurn`, `bootstrap`, `maintain`, `ingestBatch`) 의 모든 호출을 감싸 등록한다. method 가 throw 한 에러의 메시지가 L128-147 의 unrecognized-key 정규식 (sessionKey 또는 prompt 토큰을 quote 로 감싼 14 패턴) 에 매치되면, wrapper 가 해당 키를 params 에서 제거하고 method 를 재호출한다.

이 retry 는 **method 가 이미 어떤 side-effect 를 수행했는지 무관** 하게 발사된다. 예: `compact()` 가 LLM API 호출 → 응답 파싱 단계에서 "sessionKey" 토큰을 포함한 메시지로 throw 하면 wrapper 가 retry 발사 → LLM API 2번째 호출. 사용자/caller 는 단 1회 compact 를 요청했지만 LLM provider 에는 1~3 회 청구.

## 발현 메커니즘

```
caller                          wrapper (Proxy)              plugin engine
 |--- compact({sessionId,        |                            |
 |    sessionKey,...}) -------->│                            │
 |                              │ invokeWithLegacyCompat:    │
 |                              │ L240 await method(params)─>│
 |                              │                            │ LLM API call #1
 |                              │                            │ response parse FAIL
 |                              │ <─── throw "...['sessionKey']..."
 |                              │ L244 detectRejectedLegacy: │
 |                              │   매치됨, learnedNewKey=true
 |                              │ L257 onLegacyModeDetected  │
 |                              │   (closure only, no telemetry)
 |                              │ L259 strip sessionKey      │
 |                              │ L262 await method(params)─>│
 |                              │                            │ LLM API call #2
 |                              │                            │ (idempotent? no)
 |                              │ <───── result              │
 | <─── result                  │                            │
```

LLM provider 청구: 2회. caller 입장에서 결과: 1회. observability gap.

## 근본 원인 분석

1. **텍스트 패턴 기반 retry 결정**: L244 `detectRejectedLegacyCompatKeys` 는 에러 메시지의 정규식 매칭으로 retry 여부를 결정한다 (L128-147). 에러의 **시멘틱** (schema validation vs runtime error) 을 구분할 수 없다.

2. **side-effect 가능성 무시**: SESSION_KEY_COMPAT_METHODS 의 메서드는 LegacyContextEngine 같은 단순 wrapper 가 아닌 한 LLM API call / DB write 등 side-effect 를 수반한다. retry 전 "method 가 어느 단계에서 throw 했는가" 를 알 방법이 wrapper 측에 없다.

3. **visibility gap**: `onLegacyModeDetected` / `onLegacyKeysDetected` 콜백 (L257-258) 은 wrapper closure 내부 상태만 갱신. caller (`cli-compaction.ts`, `compact.queued.ts`) 는 retry 가 발생했는지 인지하지 못한다. LLM provider usage tracking, 비용 회계, 사용자 progress 표시 등이 모두 단일 호출 가정.

4. **정규식 매칭의 false-positive 위험**: L128-137 의 sessionKey 패턴은 `/[\'"`]sessionKey[\'"`]/` 같은 단순 토큰 매칭을 포함. plugin engine 의 LLM 응답 파싱 실패 시 raw error 메시지가 사용자 prompt 의 "sessionKey" 토큰을 echo 하면 매칭. schema validator 가 아닌 곳에서 발생해도 retry 발사.

## 영향

- **impact_hypothesis**: `wrong-output` — false-positive 매칭 시 LLM provider 에 중복 호출 + 단일 결과 반환. 사용자/billing 입장에서는 잘못된 결과 (비용 ×2~3, 결과 1개).

- **발현 빈도**: 현재 production 에서 사용되는 context engine 은 `LegacyContextEngine` 1개. legacy 는 sessionKey 를 받기만 하고 무시 (legacy.ts:46-54 의 assemble 은 messages pass-through, compact 는 delegateCompactionToRuntime forward — sessionKey 미사용) → schema validation throw 미발생 → retry 0회. 따라서 **현재 production 에서 발현 0**. third-party plugin 이 도입되고 그 plugin 이 sessionKey 를 schema validate 할 때 활성화.

- **severity 분류**: P3 위생/future-proofing.

- **재현 조건**:
  1. third-party plugin 의 context engine 이 compact() 안에서 LLM 호출 + 응답 파싱 실패 시 raw error throw.
  2. raw error 메시지가 정규식 패턴 매칭 (사용자 prompt 에 "sessionKey" 단어 포함하고 LLM 응답이 echo).
  3. plugin engine 이 idempotent 하지 않음 (대부분의 LLM 호출 wrapper 가 idempotent 아님).

## 반증 탐색

### R-3 방어 경로 Grep 결과

```
rg -n "isLegacy" src/context-engine/registry.ts
  → registry.ts:278,299,306. 한 번 latched 후 silent strip (L298-303) — 이후 retry 차단,
    그러나 첫 1회의 중복 호출은 막지 못함.
rg -n "availableKeys" src/context-engine/registry.ts
  → L231. params 에 sessionKey/prompt 키 부재 시 early return — 정상 호출 path 에서는
    sessionKey 거의 항상 전달됨.
rg -n "while \(true\)|for \(;;\)" src/context-engine/
  → registry.ts:243 만 매치. allowedKeys 가 max 2 이라 finite, infinite loop 없음.
rg -n "detectRejectedLegacyCompatKeys" src/context-engine/registry.ts
  → 정의 1 + 호출 2 (L207, L244).
```

### R-5 실행 조건 분류

| 코드 | 경로 | 조건 | 평가 |
|---|---|---|---|
| L240 1차 method() | 정상 flow | unconditional (params 에 키 있을 때) | side-effect 시작 지점 |
| L244 정규식 매칭 | catch 시 | unconditional | retry 결정 |
| L253 throw | 매번 같은 key | conditional-edge | 정상 break |
| L262 retry method() | 새 key 학습 시 | conditional-edge | **여기서 중복 side-effect** |
| L298-303 silent strip | 2회+ Proxy 호출 | unconditional (isLegacy=true 후) | 후속은 silent OK |

### R-7 Production hot-path 검증

- `compact()` caller: cli-compaction.ts:152, pi-embedded-runner/compact.queued.ts:165, pi-embedded-runner/run.ts:1639/1814 — 모두 production.
- `ingest()` caller: pi-embedded-runner/tool-result-context-guard.ts:318.
- `assemble()` caller: pi-embedded-runner/tool-result-context-guard.ts:330, harness/context-engine-lifecycle.ts:76.
- 그러나 현재 wrapper 가 감싸는 engine 은 LegacyContextEngine 만 활성. legacy 는 sessionKey 무시 → schema throw 0 → retry 0.

### Primary-path inversion (CAL-001)

retry mechanism 자체는 의도된 design. 본 FIND 는 retry **유무** 가 아니라 retry **조건의 과도한 매칭 + side-effect 무시** 가 문제. silent retry → CAL-001 함정과 유사 (visibility gap) 하지만 silent catch 와는 다름.

### Hot-path-vs-test-path consistency (CAL-003)

`context-engine.test.ts` 의 retry 테스트 (L590, 616, 643 등) 는 strictEngine mock 이 zod-style error throw — 본 production 패턴과 일치. 다만 false-positive 시나리오 (정규식이 schema 외 텍스트에 매치) 테스트 부재.

### Upstream-dup check (CAL-004/008)

- `git log --since="6 weeks ago" -- src/context-engine/registry.ts` 9건. invokeWithLegacyCompat / detectRejectedLegacyCompatKeys 관련 수정 0건.
- `gh pr list --state open --search "invokeWithLegacyCompat OR legacyCompat retry in:title,body"` → 매치 0.

### 외부 영향 분석

- LegacyContextEngine 이외의 plugin engine 이 production 에 도입되기 전까지 발현 0. 본 FIND 는 **future-proofing** 카드.
- 마이그레이션 vector: openclaw 의 lossless-claw 같은 plugin engine 이 sessionKey 를 zod schema 로 validate 하는 순간 retry 가 활성화. 그 시점에 plugin 작성자가 idempotency 를 가정하지 않으면 위 issue 발현.

## Self-check

### 내가 확실한 근거

- registry.ts:220-268 invokeWithLegacyCompat 함수 본문 (Read 확인).
- L128-147 정규식 패턴 (Read 확인 — sessionKey/prompt 각 7패턴).
- L295-315 Proxy 가 SESSION_KEY_COMPAT_METHODS 의 메서드를 invokeWithLegacyCompat 으로 감싸 등록 (Read 확인).
- caller (compact / ingest / assemble) 의 production 위치 (rg 결과).
- LegacyContextEngine 이 sessionKey 를 사용하지 않는 점 (legacy.ts:46-54 Read 확인).

### 내가 한 가정

- third-party plugin 의 context engine 이 LLM 호출 후 schema validation 실패 시 메시지에 sessionKey 토큰을 포함하는 시나리오 — 가능한 시나리오지만 빈도 정량화 못 함.
- LLM provider 의 비용 회계가 caller-side 의 단일 호출 가정으로 설계됐다는 점 — 일반적 패턴이라 합리적 가정.
- plugin engine 이 idempotent 가 아니라는 가정 — LLM call 자체가 비용 측면에서 idempotent 가 아니라 합리적이지만, plugin 작성자가 dedup 키를 자체 구현한 경우 영향 다를 수 있음.

### 확인 안 한 것 중 영향 가능성

- LegacyContextEngine 외 production 환경에서 실제 사용되는 third-party plugin engine 인스턴스 — 본 audit 범위 외 (src/plugins/ 외 또는 외부 npm package). 그 plugin 의 코드를 직접 읽지 않음.
- `prepareSubagentSpawn` (subagent-spawn.ts:463) 도 SESSION_KEY_COMPAT_METHODS 에 포함되지 않은 별도 메서드라 retry 영향 받지 않음.
- Proxy 가 메서드 호출 시 매번 새 closure 를 생성하지 않고 동일 closure 의 isLegacy/rejectedKeys 를 공유함 (L278-279) — engine 인스턴스 단위 latch. 따라서 false-positive 1회 = 해당 engine 인스턴스의 sessionKey 영구 disable. resolveContextEngine 이 매 호출마다 새 engine 인스턴스 생성 (registry.ts:563 await entry.factory(...)) 하므로 latch 는 호출 단위 격리. 다만 caller 가 engine 을 보관하는 경우 (run.ts:1066 처럼 한 번 resolve 후 retry 동안 재사용) latch 가 장기 유지 가능.
