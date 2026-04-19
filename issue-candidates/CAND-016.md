---
candidate_id: CAND-016
type: single
finding_ids:
  - FIND-gateway-memory-003
cluster_rationale: |
  단독 FIND. CAND-014 의 cluster_rationale 참조. 세 FIND 모두 "Map eviction
  insufficient" 상위 테마이지만 R-5 분류와 fix 축 독립.

  FIND-003 root_cause_chain[2]:
    "CAL-001 패턴 (unconditional timer delete) 이 여기엔 없다. 같은 파일의
     pendingAgentRunErrors 는 setTimeout 콜백에서 unconditional delete 이나
     agentRunStarts 는 이런 타이머 자체가 없음."

  CAND-015 (nodeWakeById) 와 달리 이 FIND 는 lifecycle end/error event pipeline
  의 robustness 에 의존하며 hot-path 에서는 try/finally 로 견고함.
  그러나 emitAgentEvent pipeline 실패 / runner SIGKILL 경로에서 fallback 부재.
  즉 "event-dependent + no TTL safety belt" 구조 자체가 문제.
proposed_title: "gateway/agent-job: agentRunStarts relies solely on lifecycle end/error events — no TTL/cap safety belt"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-19
---

# gateway/agent-job: agentRunStarts 가 lifecycle end/error 이벤트에만 의존 (TTL/cap safety belt 부재)

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/agent-job.ts:12` 의
`agentRunStarts: Map<runId, startedAt>` 은 lifecycle `phase: "start"` 이벤트에서
set (L114), `phase: "end" | "error"` 이벤트에서 delete (L129). 다른 prune/evict/timer
safety belt 경로가 전무하다. 동일 파일의 `agentRunCache` (pruneAgentRunCache TTL=10min)
나 `pendingAgentRunErrors` (setTimeout unconditional delete) 와 대조된다.

## 관련 FIND

- FIND-gateway-memory-003: runner 프로세스 SIGKILL / OOM / event pipeline 중간 실패
  시 end/error 이벤트가 발사되지 않아 엔트리가 영구히 남는다. hot-path 는
  `handleAgentEnd` 의 try/finally 로 robust 하나 0 은 아닌 실패율에서 slow leak.

## 근거 위치

- 선언: `src/gateway/server-methods/agent-job.ts:12`
- set: `src/gateway/server-methods/agent-job.ts:114` (on lifecycle "start")
- delete: `src/gateway/server-methods/agent-job.ts:129` (on lifecycle "end"|"error")
- 대조 (same file, safety belt 있음): L31-37 `pruneAgentRunCache` TTL prune,
  L53-66 `schedulePendingAgentRunError` 의 setTimeout unconditional delete (CAL-001 반례)

## 영향

- `impact_hypothesis: memory-growth` (매우 slow)
- 엔트리 크기 60~100 bytes. runId 는 gateway 수명 내 무한 공간.
- 기능적 오작동은 관측 안 됨 (순수 메모리 영향). `get(runId)` 호출부는 fallback 값
  사용.
- P3 — hot-path robust, edge case 만 문제.

## 대응 방향 (제안만)

같은 파일 `pendingAgentRunErrors` 패턴 (`schedulePendingAgentRunError`) 참조 —
start 이벤트 시 N 초 후 `agentRunStarts.delete(runId)` 수행하는 safety timer,
혹은 lifecycle end 이벤트 수신 시 함께 정리. 구체는 SOL 단계.

## 반증 메모

- `onAgentEvent` dispatcher 가 listener throw 를 swallow/재전파 하는 동작 미확인
  (agent-events.ts allowed_paths 외부).
- graceful shutdown 시 리셋 경로 미확인 — launchd/systemd restart 가 잦으면 누수량
  자연 감소.
