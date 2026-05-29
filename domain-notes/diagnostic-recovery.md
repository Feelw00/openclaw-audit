# domain-notes: diagnostic-recovery

stuck-session 복구 상태머신의 인과/순서 분석. 셀 `diagnostic-recovery-ordering-causality`
(ordering-causality-auditor, 2026-05-29).

## 1. 복구 상태머신 개요

heartbeat (`diagnostic.ts:1241` 30초 interval) 가 `diagnosticSessionStates` Map 을 순회하며
각 세션의 stuck 여부를 판정하고 복구를 dispatch 한다. 경로:

```
heartbeat tick
  → classifySessionAttention (diagnostic-session-attention.ts:27)
  → recoveryEligible 면 requestStuckSessionRecovery (coordinator.ts:145)
       ├─ recoveryRequestsInFlight dedup (key = ref:generation)
       ├─ recover(request)  ← 실제로는 recoverStuckDiagnosticSession (runtime.ts:84) async
       │     └─ recoveriesInFlight dedup (key = ref only)
       └─ applyRecoveryOutcomeToDiagnosticState (coordinator.ts:83)
             ├─ staleness 검사 (idle 분기는 generation 또는 generation+1 허용)
             └─ 통과 시 state.state="idle", generation+1, queueDepth 재계산
```

세션 상태값은 `idle | processing | waiting` (diagnostic-session-state.ts:1) 3종뿐.
**terminal(done/failed/ended) 상태는 없다.** 즉 category B(terminal reactivate) 의 고전형
"종료 세션을 running 으로 박제" 는 직접 적용 안 됨 — 대신 generation 단조 카운터가 staleness
가드 역할을 한다.

## 2. generation 인과 분석

`state.generation` 은 세션 상태 변화의 논리 시계(logical clock). 증가 지점 (전부 동기, +1):

| 함수 | 위치 | state 부수효과 |
|---|---|---|
| `logMessageQueued` | diagnostic.ts:657 | state 불변(idle 유지), queueDepth+1 |
| `logSessionStateChange` | diagnostic.ts:869 | state 교체 (idle↔processing↔waiting) |
| `markDiagnosticSessionProgress` | diagnostic.ts:915 | state 불변 |
| `applyRecoveryOutcomeToDiagnosticState` | coordinator.ts:122 | state="idle" 강제 |
| `mergeSessionState` | diagnostic-session-state.ts:110 | generation=max(t,s) |

**중요**: `markDiagnosticEmbeddedRunStarted` (diagnostic-run-activity.ts:254) 는
generation 을 건드리지 않는다. 새 embedded run 이 시작돼도 activity Map 만 갱신되고
session state 의 generation 은 그대로다. 즉 "새 작업이 실제 시작됐는데 generation 은 안 오른"
상태가 production 에서 발생할 수 있다.

staleness 가드 `isDiagnosticSessionStateCurrent` (diagnostic-session-state.ts:198):
- `generation === undefined` → 무조건 current(true) — generation 없는 요청은 가드 무력
- 그 외 → `state.generation === params.generation && state===expectedState` 정확 일치

runtime 진입부(runtime.ts:101)와 coordinator apply 직전(coordinator.ts:104) 모두 이 가드를
부른다 → 이중 검사. 단 coordinator 의 **idle 분기**(coordinator.ts:98-103)는 가드를 우회하고
`generation === requestGeneration || generation === requestGeneration+1` 로 완화한다.

## 3. 2-Set in-flight 키 비대칭 (seed E)

| Set | 위치 | 키 정의 |
|---|---|---|
| `recoveryRequestsInFlight` | coordinator.ts:21 | `recoveryRequestKey` = `${ref}:${stateGeneration ?? "unknown"}` (:63-69) |
| `recoveriesInFlight` | runtime.ts:27 | `recoveryKey` = `ref` only (sessionKey\|\|sessionId) (:55-57) |

두 Set 은 같은 논리 복구를 별개로 추적한다. coordinator 의 dedup 입도(per-generation)가
runtime(per-ref)보다 미세. 같은 ref 의 다른 generation 요청은 coordinator dedup 통과 →
runtime 에서 `already_in_flight` skip 으로 귀결될 수 있다. skipped 결과는
`recoveryOutcomeMutatesSessionState`=false (diagnostic-session-recovery.ts:88-93)라 state mutate
는 없으나, coordinator 가 `session.recovery.requested` 이벤트를 중복 발행하고 dedup 의 두 계층이
어긋난 채로 동작한다. 자세한 결함은 FIND-...-001 참조.

## 4. peek vs get 엔트리 분기 (split-state)

`peekDiagnosticSessionState` (state.ts:181) 와 `getDiagnosticSessionState` (state.ts:142) 가
같은 ref 에 대해 **다른 SessionState 객체**를 반환할 수 있다:
- peek: `map.get(key) ?? findStateEntryBySessionId(id)` — merge/mutate 없음
- get: by-key 와 by-id 엔트리가 둘 다 있고 다르면 **merge**(generation=max) 후 by-key 반환

apply 경로(coordinator.ts:95,104 → peek 기반 가드 / coordinator.ts:118 → get 기반 mutate)는
가드가 검사한 객체와 실제 mutate 대상 객체가 split-state 상황에서 불일치할 수 있다.
FIND-...-003 참조.

## 5. 발견 요약

| FIND | severity | 한 줄 |
|---|---|---|
| FIND-diagnostic-recovery-ordering-causality-001 | P2 | 2-Set in-flight 키 비대칭으로 coordinator dedup 통과/runtime skip 갈림, 중복 requested 이벤트 + dedup 계층 불일치 |
| FIND-diagnostic-recovery-ordering-causality-002 | P1 | idle 분기 generation+1 완화가 async 윈도우 중 도착한 새 메시지/작업의 인과를 무시하고 stale 복구결과로 idle/queueDepth 덮어씀 |
| FIND-diagnostic-recovery-ordering-causality-003 | P2 | staleness 가드(peek)와 mutate 대상(get)이 split-state 에서 다른 엔트리 → 가드 검증한 generation 과 다른 엔트리를 idle 로 강제 |

## 6. 반증으로 기각한 후보

- **coordinator failRecovery sync-throw 시 clearInFlight 누락**(seed): catch 블록
  (coordinator.ts:209-214)이 `try { failRecovery } finally { clearInFlight }` 로 감싸 sync throw
  에도 clearInFlight 보장됨. 누수 없음 → FIND 아님.
- **processing 분기 terminal reactivate**: non-idle 분기는 `isDiagnosticSessionStateCurrent` 의
  정확 generation 일치를 요구(state.ts:211-214)하므로 async 윈도우 중 generation 이 한 번이라도
  오르면 stale 처리되어 mutate skip. 인과 역전 불가 → FIND 아님.
- **runtime fire-and-forget 영속**: runtime 은 `void persistX` 패턴 없음. 모든 abort/drain 은
  `await abortAndDrainEmbeddedAgentRun` (runtime.ts:173,203) 으로 직렬화됨 → category A 미해당.

### clusterer (2026-05-29)

- CAND-052 (epic): 공통 원인 "복구 apply(applyRecoveryOutcomeToDiagnosticState,
  coordinator.ts:95-131)의 staleness 가드가 검증한 세계와 실제 mutate 대상 세계가 불일치 →
  stale 복구결과 적용" 으로 2 FIND 묶음 (FIND 본문 상호 cross_ref 존중).
  - FIND-002 root_cause_chain[0]: "idle 분기가 정확 generation 일치 대신 `generation ===
    requestGeneration+1` 을 명시적으로 허용" (coordinator.ts:103) — 시간축 불일치.
  - FIND-003 root_cause_chain[0]: "apply 가 읽기는 peek(merge 없음), 쓰기는 get(merge 있음)을
    쓴다. 같은 ref 가 split 되어 있으면 두 호출이 다른 객체로 resolve 된다"
    (coordinator.ts:95) — 주소축 불일치.
  - FIND-003 self-check 가 queueDepth 손상 구체화를 FIND-002 의 clear 로직과 결합한다고
    명시 + 두 FIND frontmatter 상호 cross_ref → epic. severity 최고값 P1 상속.
- CAND-053 (single): FIND-diagnostic-recovery-ordering-causality-001.
  - root_cause_chain[0]: "coordinator 키는 generation 을 포함, runtime 키는 ref 만 쓴다"
    (coordinator.ts:68 / runtime.ts:55-57) — in-flight 추적 2-Set 키 입도 불일치.
  - cross_refs: [] (FIND-002/003 의 apply 가드 정합성과 다른 코드 경로=요청 dispatch vs
    결과 apply, 다른 결함 클래스=dedup 입도 vs staleness 검증) → 별도 single 분리.
    state mutate 없음(skipped non-mutating)이라 P2.
