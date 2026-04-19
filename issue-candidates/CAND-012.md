---
candidate_id: CAND-012
type: single
finding_ids:
  - FIND-auto-reply-concurrency-001
cluster_rationale: |
  단독 FIND. 같은 셀(auto-reply-concurrency)의 FIND-auto-reply-concurrency-002
  와 cell/file 공유하지만 root cause 가 독립적이므로 merge 금지.

  FIND-001 root_cause_chain[0]:
    "L266 은 `FOLLOWUP_QUEUES.delete(key)` 로 key 로만 지우고, 현재 map entry 가
     자신이 들고 있는 `queue` 참조와 같은지 (`FOLLOWUP_QUEUES.get(key) === queue`)
     검증하지 않는다." → **queue identity 검증 부재** (drain finally 의 lifecycle race).

  FIND-002 root_cause_chain[0]:
    "L159 에서 한 번 계산하고 L161 의 drainCollectQueueStep 에 parameter 로 전달.
     재계산 지점이 없으며..." → **stale snapshot + chimera routing** (batch 경로의
     data dependency 문제).

  두 FIND 는 drain.ts 라는 같은 파일에 있으나:
  - FIND-001 은 queue lifecycle management (Map ownership)
  - FIND-002 은 batch computation staleness (data freshness)
  근본 원인 축(axis)이 서로 다르며, 해결책도 서로 독립적(identity guard 추가 vs.
  isCrossChannel 재계산 또는 authorization key 확장). → 각각 single CAND 로 분리.
proposed_title: "auto-reply queue: drain finally deletes FOLLOWUP_QUEUES entry without identity check, orphaning post-/stop queue"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-19
---

# auto-reply queue: drain finally 가 identity 검증 없이 map entry 를 삭제해 후속 queue 를 orphan 시킴

## 공통 패턴

단일 FIND 기반 single CAND. `scheduleFollowupDrain` 의 drain IIFE 는 finally 블록
(src/auto-reply/reply/queue/drain.ts:263-270) 에서 `FOLLOWUP_QUEUES.delete(key)` +
`clearFollowupDrainCallback(key)` 를 호출한다. 이 두 호출은 key 만으로 제거하며
자신이 들고 있는 `queue` 참조가 현재 map 의 entry 와 동일한지 검증하지 않는다.

## 관련 FIND

- FIND-auto-reply-concurrency-001: `/stop` 또는 session reset 이 drain 의 `await`
  지점에서 `clearSessionQueues` → `clearFollowupQueue(key)` 를 호출하고, 이후
  `enqueueFollowupRun` 이 새 Q2 를 등록하면, 원래 D1 의 finally 가 Q2 의 map entry
  와 callback 까지 지워 Q2 가 orphan 되는 시나리오.

## 재현 시나리오 요약

- T0: msg1 → Q1 생성 + D1 drain 시작 (await effectiveRunFollowup 에서 대기).
- T1: `/stop` → `clearFollowupQueue` 가 Q1.items 를 in-place 비우고 map 에서 Q1 제거.
- T2: msg2 → 새 Q2 를 map 에 등록, D2 kick.
- T3-T4: D1 의 await 반환 → finally 가 L266 `FOLLOWUP_QUEUES.delete(key)` 실행 →
  현재 map entry 인 Q2 가 삭제됨.
- T5: `getFollowupQueueDepth(key)` → 0 반환 (실제 Q2.items 는 비어있지 않음).

## 영향

- `impact_hypothesis: wrong-output`
- `/status` observability 오염 (queue depth 0 으로 보고)
- 동일 session key 에서 D2/D3 병렬 실행 가능 (per-session serialization 위반)
- production caller (agent-runner.ts:1002) 는 runFollowup 을 항상 전달하므로
  callback 손실은 일반 흐름에서 발생 안 함. 그러나 invariant 깨짐.

## 대응 방향 (제안만)

drain finally 에서 `if (FOLLOWUP_QUEUES.get(key) === queue) { delete; clearCallback; }`
형태의 identity guard. 구체는 SOL 단계에서 결정.

## 참고

- 동일 패턴이 `src/agents/subagent-announce-queue.ts:204` 에도 존재하며 maintainer
  주석 (L63-64) 이 dangling reference 문제를 인지함 (테스트 reset 경로에만 대응).
  이 CAND 의 hot-path 는 production finally 경로.
