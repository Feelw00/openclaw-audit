---
id: FIND-diagnostic-recovery-ordering-causality-003
cell: diagnostic-recovery-ordering-causality
title: staleness 가드(peek)와 mutate 대상(get)이 split-state 에서 다른 엔트리로 갈림
file: src/logging/diagnostic-session-state.ts
line_range: 181-187
evidence: "```ts\nexport function peekDiagnosticSessionState(ref: SessionRef): SessionState\
  \ | undefined {\n  const key = resolveSessionKey(ref);\n  return (\n    diagnosticSessionStates.get(key)\
  \ ??\n    (ref.sessionId ? findStateEntryBySessionId(ref.sessionId)?.[1] : undefined)\n\
  \  );\n}\n```\n"
symptom_type: ordering-causality-gap
problem: staleness 가드는 `peekDiagnosticSessionState`(coordinator.ts:95) 와 그 내부의 `isDiagnosticSessionStateCurrent`(peek
  기반, state.ts:207)로 generation 을 검증하지만, 실제 mutate 대상은 `getDiagnosticSessionState`(coordinator.ts:118)로
  가져온다. peek 와 get 은 같은 ref 에 대해 서로 다른 SessionState 객체를 반환할 수 있다 — get 은 by-key/by-id
  로 split 된 두 엔트리를 merge (generation=max)하고 by-key 를 반환하지만 peek 은 merge 없이 by-key
  또는 by-id 한쪽만 본다. 결과적으로 가드가 검증한 generation 과 다른 generation 의 엔트리를 idle 로 강제 덮어쓴다.
mechanism: '1. 세션 상태가 split 됨: by-key 엔트리(map key=sessionKey)와 by-id 엔트리(state.sessionId
  일치, 다른 map key)가 동시에 존재. 이는 sessionKey 없이 sessionId 로만 기록되다가 나중에 sessionKey 가 붙는
  등 ref 진화 과정에서 발생(state.ts:78-86, 142-166).

  2. 두 엔트리의 generation 이 다를 수 있음: 예 by-key.generation=G, by-id.generation=G'' (G''>G).

  3. heartbeat 가 by-key 또는 merge 된 generation 으로 recover 요청 → stateGeneration 전달.

  4. recover() 완료 후 apply: peekDiagnosticSessionState(coordinator.ts:95) → `map.get(key)
  ?? findStateEntryBySessionId(id)`(state.ts:181-186). request 에 sessionKey 가 있으면
  by-key 엔트리(gen=G)를 반환. currentGeneration=G 로 가드 검사 → 통과.

  5. mutate: getDiagnosticSessionState(coordinator.ts:118) → direct(by-key)와 sessionIdEntry(by-id)가
  둘 다 있고 다르면 mergeSessionState(state.ts:149-151) 실행 → direct.generation = max(G, G'')=G'',
  by-id 엔트리 삭제, direct 반환.

  6. 가드는 generation G 를 current 로 확인했지만, 실제 mutate 대상은 merge 로 generation 이 G'' 로
  점프한 엔트리다. apply 는 이 엔트리에 state.state=idle, generation=G''+1 을 강제하여, 가드가 본 적 없는 G''
  세계의 상태(더 최근 활동을 반영한)를 stale 복구결과로 덮어쓴다.

  '
root_cause_chain:
- why: 왜 가드가 검증한 엔트리와 mutate 대상이 다른가?
  because: apply 가 읽기는 peek(merge 없음), 쓰기는 get(merge 있음)을 쓴다. 같은 ref 가 split 되어 있으면
    두 호출이 다른 객체로 resolve 된다.
  evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:95
- why: 왜 peek 와 get 의 resolve 결과가 다를 수 있는가?
  because: get 은 by-key 와 by-id 엔트리가 둘 다 있으면 merge(generation=max) 후 삭제하지만, peek 은
    merge/삭제 없이 `map.get(key) ?? byId` 한쪽만 반환한다.
  evidence_ref: src/logging/diagnostic-session-state.ts:148-166
- why: 왜 merge 가 generation 을 점프시키는가?
  because: mergeSessionState 가 target.generation = Math.max(target, source) 로 두 엔트리
    중 큰 값을 택한다.
  evidence_ref: src/logging/diagnostic-session-state.ts:110
- why: 왜 이것이 인과 위반인가?
  because: staleness 가드의 목적은 "내가 복구를 계획한 시점(generation G)의 세계가 아직 유효한가" 를 확인하는 것인데,
    mutate 대상이 G' 엔트리라면 G 검증은 G' 세계에 대한 보증이 아니다. G' 의 더 최근 활동(다른 ref 경로로 기록된)을 모른
    채 idle 로 덮는다.
  evidence_ref: src/logging/diagnostic-session-state.ts:198-214
impact_hypothesis: wrong-output
impact_detail: '정성:

  - split-state(by-key + by-id 동시 존재)인 세션에서, staleness 가드가 검증한 generation 과 실제 mutate
  대상 엔트리의 generation 이 다르면, 가드를 우회한 형태로 stale 복구결과(idle 강제 + queueDepth 재계산)가 적용된다.
  by-id 엔트리에 누적된 더 최근의 queueDepth/ 활동이 by-key 로 merge 되면서 idle 로 덮여 "잘못된 idle 상태"
  가 노출될 수 있다.

  - split-state 발생 빈도가 결함 빈도를 좌우. sessionId-only 기록 후 sessionKey 부착(또는 그 역)이 일어나는
  세션에서만 두 엔트리가 공존(state.ts:142-166 의 merge/promote 로직 자체가 이 공존을 전제로 존재). 항상 split
  인 것은 아니므로 드묾 → severity P2.

  - 재현: getDiagnosticSessionState({sessionId}) 로 by-id 엔트리 생성(gen 진행) + 별도로 sessionKey
  키 엔트리 존재 상태에서, sessionKey+sessionId 둘 다 담은 request 로 apply 호출 → peek 는 by-key, get
  는 merge 결과를 반환하는지 확인.

  '
severity: P2
counter_evidence:
  path: src/logging/diagnostic-session-state.ts
  line: 181-187
  reason: 'peek/get 일관성·merge 단조성·테스트 4축:


    | 경로 | 실행 조건 |

    |---|---|

    | 가드 read = peekDiagnosticSessionState (coordinator.ts:95) | merge 없음, `map.get(key)
    ?? byId` |

    | 가드 내부 isDiagnosticSessionStateCurrent (state.ts:207) | 역시 peek 기반 |

    | mutate read = getDiagnosticSessionState (coordinator.ts:118) | split 시 merge(generation=max)
    후 by-key 반환 |


    1) generation 단조성: merge 가 max 를 취하므로(state.ts:110) generation 은 절대 감소 안 함 → 가드가
    본 G 보다 mutate 대상이 작아지는 일은 없음. 그러나 *커지는*(G→G'') 방향의 불일치는 가드를 무의미하게 만든다(검증 안 한 세계에
    mutate).

    2) terminal 보호: state 에 terminal 없음 → 무관.

    3) 직렬화: peek 와 get 사이에 다른 동기 호출이 끼지 않더라도(같은 함수 내 연속 호출), 두 함수의 resolve 의미 자체가
    달라 split 시 다른 객체를 본다. 시간 race 가 아니라 정의 불일치.

    4) 기존 테스트: diagnostic.test.ts:204 "merges split sessionId and sessionKey state
    without leaving stale queued work" 가 merge 동작은 테스트하나, **apply 의 가드(peek)와 mutate(get)가
    split 시 다른 엔트리를 보는** 시나리오는 미검증. recovery apply 경로에서 split-state 를 만든 테스트 없음(`rg
    -n "peek|split" src/logging/diagnostic*.test.ts`).

    '
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-diagnostic-recovery-ordering-causality-002
domain_notes_ref: domain-notes/diagnostic-recovery.md
---
# staleness 가드(peek)와 mutate 대상(get)이 split-state 에서 다른 엔트리로 갈림

## 문제

복구 apply 는 staleness 를 `peekDiagnosticSessionState`(merge 없음)로 검증하지만 실제 mutate 는
`getDiagnosticSessionState`(split 시 merge, generation=max)로 한다. 같은 ref 가 by-key/by-id 로
split 되어 있으면 두 함수가 서로 다른 SessionState 객체를 반환하여, 가드가 검증한 generation 과
다른 generation 의 엔트리에 idle 강제 + queueDepth 재계산이 적용된다.

## 발현 메커니즘

1. 세션이 split: by-key 엔트리(gen=G)와 by-id 엔트리(gen=G', G'>G) 공존(state.ts:142-166).
2. heartbeat 가 G 기반 recover 요청 dispatch.
3. recover() 완료 후 apply: peek(coordinator.ts:95) → by-key 엔트리(gen=G) 반환 → 가드 통과(currentGen=G).
4. mutate: get(coordinator.ts:118) → by-key/by-id merge → generation=max(G,G')=G', by-id 삭제, by-key 반환.
5. 가드는 G 를 검증했으나 mutate 대상은 G' 엔트리 → state=idle 강제, generation=G'+1.
6. G' 세계(다른 ref 경로로 기록된 더 최근 활동)를 모른 채 stale 복구결과로 덮음.

## 근본 원인 분석

1. apply 가 read=peek(merge 없음, coordinator.ts:95), write=get(merge 있음, coordinator.ts:118)로 비대칭.
2. get 은 split 시 mergeSessionState(state.ts:149-151) 실행, peek 은 `map.get(key) ?? byId` 한쪽만(state.ts:181-186).
3. merge 가 generation=max(state.ts:110) → G→G' 점프.
4. staleness 가드의 보증(G 세계 유효)이 mutate 대상(G' 세계)에 적용 안 됨 → 가드 무의미.

## 영향

- **impact_hypothesis: wrong-output** — split-state 세션에서 가드 우회 형태로 stale 복구결과가 적용,
  by-id 에 누적된 더 최근 queueDepth/활동이 idle 로 덮여 잘못된 idle 노출.
- split-state 발생 시에만(sessionId-only→sessionKey 부착 등 ref 진화 경로) 노출 → 드묾, severity P2.

## 반증 탐색

- **generation 단조성**: merge 가 max 라 generation 감소는 없음(state.ts:110). 단 G→G' 증가 방향
  불일치가 가드를 무의미하게 만든다.
- **terminal 보호**: state 에 terminal 없음 → 무관.
- **직렬화 아님**: peek/get 사이 race 가 아니라 두 함수의 resolve 의미 차이(정의 불일치). split 시 항상 갈림.
- **기존 테스트**: diagnostic.test.ts:204 가 merge 동작은 테스트하나, apply 의 peek-가드 vs get-mutate
  엔트리 분기는 미검증. recovery apply 에서 split-state 를 만든 테스트 없음.

## Self-check

### 내가 확실한 근거
- coordinator apply 는 read=peek(:95), write=get(:118)로 비대칭.
- peek 은 merge 없음(state.ts:181-187), get 은 split 시 merge+삭제(state.ts:148-166).
- merge 는 generation=Math.max(state.ts:110).
- isDiagnosticSessionStateCurrent 도 peek 기반(state.ts:207).

### 내가 한 가정
- split-state(by-key + by-id 공존, 다른 generation)가 production 에서 실제 발생한다고 가정. merge/promote
  로직(state.ts:142-166)이 존재한다는 것은 이 공존이 가능함을 전제로 하나, 빈도는 미측정.
- request 에 sessionKey 와 sessionId 가 둘 다 있어 peek 가 by-key, get 가 merge 를 타는 조합이라고 가정.
  sessionKey 만 있거나 sessionId 만 있으면 peek/get 이 같은 엔트리로 수렴해 결함 미발생.

### 확인 안 한 것 중 영향 가능성
- 두 엔트리의 generation 이 실제로 의미있게 벌어지는지(G vs G') 미측정. 거의 동기화되어 있다면 영향 미미.
- merge 후 idle 강제가 by-id 의 활동(queueDepth)을 실제로 손상시키는지는 FIND-002 의 queueDepth clear
  로직과 결합해야 구체화됨 → cross_ref FIND-002. 단독으로는 "가드-mutate 대상 불일치" 라는 구조 결함.
