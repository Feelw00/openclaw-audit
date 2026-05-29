---
candidate_id: CAND-052
type: epic
finding_ids:
  - FIND-diagnostic-recovery-ordering-causality-002
  - FIND-diagnostic-recovery-ordering-causality-003
cluster_rationale: |
  공통 근본 원인 (clusterer.md Step 3, FIND 본문 cross_refs 존중): 두 FIND 는 모두
  stuck-session 복구 apply 경로(applyRecoveryOutcomeToDiagnosticState)의 staleness
  가드가 "내가 복구를 계획한 generation 의 세계가 아직 유효한가" 를 올바르게 검증하지
  못해, async recover() 윈도우 중 발생한 더 최근 활동(새 메시지/큐)을 무시한 채 stale
  복구결과를 적용한다는 동일 축의 발현이다. 두 FIND 는 frontmatter 에서 서로를 명시
  cross_ref 한다(FIND-002.cross_refs=[FIND-003], FIND-003.cross_refs=[FIND-002]).

  각 FIND root_cause_chain 인용:
  - FIND-...-002 root_cause_chain[0] ("왜 G+1 세대의 세션에 G 시점 복구결과가
    적용되는가"): "idle 분기가 정확 generation 일치 대신 `generation ===
    requestGeneration+1` 을 명시적으로 허용하여, 한 번의 generation bump 를 staleness 로
    치지 않는다" (evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:103)
  - FIND-...-002 root_cause_chain[2] ("왜 stale outcome 적용이 queueDepth 를
    손상시키는가"): "recoveryOutcomeClearsQueuedSessionState 가 released>0 인 aborted 를
    queueDepth=0 으로 clear 하는데, 이는 G 시점 큐를 가정한 것이라 G+1 에서 추가된
    메시지까지 함께 0 으로 만든다"
    (evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:127)
  - FIND-...-003 root_cause_chain[0] ("왜 가드가 검증한 엔트리와 mutate 대상이
    다른가"): "apply 가 읽기는 peek(merge 없음), 쓰기는 get(merge 있음)을 쓴다. 같은
    ref 가 split 되어 있으면 두 호출이 다른 객체로 resolve 된다"
    (evidence_ref: src/logging/diagnostic-session-recovery-coordinator.ts:95)
  - FIND-...-003 root_cause_chain[3] ("왜 이것이 인과 위반인가"): "staleness 가드의
    목적은 '내가 복구를 계획한 시점(generation G)의 세계가 아직 유효한가' 를 확인하는
    것인데, mutate 대상이 G' 엔트리라면 G 검증은 G' 세계에 대한 보증이 아니다"
    (evidence_ref: src/logging/diagnostic-session-state.ts:198-214)

  공통 surface: 두 FIND 모두 apply 의 가드 read 와 mutate 대상 사이의 generation
  semantics 불일치다. FIND-002 는 *시간축* 불일치(idle 분기가 G+1 를 current 로 수용하여
  async 윈도우 중 +1 된 세계를 stale 처리 못 함), FIND-003 은 *주소축* 불일치(가드 peek
  와 mutate get 이 split-state 에서 다른 엔트리로 resolve 되어 검증한 generation 과 다른
  엔트리를 덮음). FIND-003 self-check 도 "merge 후 idle 강제가 by-id 활동(queueDepth)을
  손상시키는지는 FIND-002 의 queueDepth clear 로직과 결합해야 구체화됨 → cross_ref
  FIND-002" 라 명시해 두 결함이 같은 apply 경로에서 결합함을 인정한다.

  epic 으로 묶는 이유: 동일 함수(applyRecoveryOutcomeToDiagnosticState, coordinator.ts:95-131)
  의 staleness 가드 정합성이라는 같은 결함 클래스 + 같은 surface(가드가 검증한 세계와 실제
  mutate 대상의 generation 일치 보증)를 공유하고, FIND 본문이 서로를 cross_ref 한다.
  severity 는 최고값 P1 상속. (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "diagnostic 복구 apply 의 staleness 가드 정합성 결함: idle 분기 generation+1 완화(시간축) + peek-가드/get-mutate 엔트리 불일치(주소축)로 stale 복구결과 적용"
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
---

# diagnostic-recovery: 복구 apply staleness 가드가 검증한 세계와 다른 세계에 stale 결과 적용

## 공통 패턴

stuck-session 복구 apply(`applyRecoveryOutcomeToDiagnosticState`, coordinator.ts:95-131)는
recover() async 윈도우 동안 generation 이 잠기지 않으므로, 복구 계획 시점(generation G)의
세계가 적용 시점에도 유효한지를 staleness 가드로 검증해야 한다. 그러나 두 갈래에서 가드가
"검증한 세계 ≠ 실제 mutate 대상 세계" 가 되어 stale 복구결과가 적용된다.

- **시간축 불일치 — idle 분기 generation+1 완화(FIND-002)**: idle + abort_embedded_run
  분기는 staleness 를 정확 generation 일치 대신 `generation === requestGeneration ||
  requestGeneration+1`(coordinator.ts:103)로 완화한다. recover() 윈도우 중 logMessageQueued
  가 generation 을 한 번 bump(+1)하며 queueDepth+1 을 동시에 하는데(diagnostic.ts:655-657),
  완화가 G+1 을 여전히 current 로 수용해 G 시점 outcome 을 적용한다.
  recoveryOutcomeClearsQueuedSessionState(released>0 aborted)가 queueDepth=0 으로 clear
  (:127)하여 윈도우 중 도착한 follow-up 메시지의 큐가 소실되고, queueDepth=0 idle 세션은
  다음 heartbeat stuck 판정을 통과하지 못해(diagnostic.ts:1247-1255) 영구 복구 제외(data-loss).
- **주소축 불일치 — peek-가드 / get-mutate split(FIND-003)**: 가드 read 는
  `peekDiagnosticSessionState`(merge 없음, coordinator.ts:95), 실제 mutate read 는
  `getDiagnosticSessionState`(split 시 merge, generation=max, :118)다. 같은 ref 가
  by-key/by-id 로 split 되어 있으면 peek 은 by-key(gen=G) 반환→가드 통과, get 은 merge 로
  gen=max(G,G')=G' 엔트리를 반환→그 엔트리를 idle 강제 + queueDepth 재계산한다. 가드가 본
  적 없는 G' 세계(다른 ref 경로로 기록된 더 최근 활동)를 stale 복구결과로 덮는다(wrong-output).

공통 기준선/반증: 두 FIND 모두 non-idle 분기의 정확 generation 일치
(isDiagnosticSessionStateCurrent, state.ts:211-214)는 안전함을 인정한다 - async 윈도우 중
1회 bump 면 stale 처리되어 mutate skip. 또 SessionStateValue 에 terminal 상태가 없어
terminal 보호와는 무관. 결함은 idle 분기의 완화(시간축)와 peek/get resolve 의미 차이(주소축)가
정확 일치 가드를 우회시키는 데 있다.

## 관련 FIND

- FIND-diagnostic-recovery-ordering-causality-002 (P1):
  `diagnostic-session-recovery-coordinator.ts:98-117`. idle abort_embedded_run 복구가
  generation+1 완화로 async 윈도우 중 도착한 follow-up 메시지를 stale 처리 못 하고 G 시점
  outcome 으로 queueDepth=0 clear → 메시지 큐 소실 + queueDepth=0 idle 세션이 영구 복구 제외.
  완화가 "정확히 한 번의 bump" 를 허용해 메시지 1개 도착(가장 흔한 케이스)에서 발동하고,
  2개 이상이면 G+2 로 stale 처리되는 역설. 현실적 도착 순서 + 큐 소실 + 자동 복구 불가로
  data-loss, P1.

- FIND-diagnostic-recovery-ordering-causality-003 (P2):
  `diagnostic-session-state.ts:181-187`. 복구 apply 의 가드 read=peek(merge 없음)와
  mutate read=get(split 시 merge, generation=max)이 split-state 세션에서 다른 엔트리로
  resolve → 가드가 검증한 generation 과 다른 generation 엔트리를 idle 강제 + queueDepth
  재계산. by-id 에 누적된 더 최근 활동이 idle 로 덮여 잘못된 idle 노출(wrong-output).
  split-state(sessionId-only→sessionKey 부착 등 ref 진화)에서만 노출되어 드묾, P2.
  단독으로는 구조 결함이나 queueDepth 손상 구체화는 FIND-002 의 clear 로직과 결합.
