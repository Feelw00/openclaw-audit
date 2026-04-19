---
candidate_id: CAND-014
type: single
finding_ids:
  - FIND-gateway-memory-001
cluster_rationale: |
  단독 FIND. 같은 셀(gateway-memory)의 FIND-002/003 과 "Map 누수" 라는 상위
  패턴은 공유하지만 R-5 execution-condition 분류가 다름.

  FIND-001 (costUsageCache) R-5 분류:
    "no-cleanup" — delete/prune 경로가 아예 없음 (순수 설계 누락).
    TTL on-read 만 존재, write-path 에 size cap / FIFO evict 부재.

  FIND-002 (nodeWakeById) R-5 분류:
    "conditional-event-dependent with precondition that may never hold" —
    WS close 경로에만 의존, 미등록 nodeId 는 WS 연결 cycle 없음.

  FIND-003 (agentRunStarts) R-5 분류:
    "conditional-event-dependent with low failure rate" — lifecycle end/error
    이벤트에 의존, 프로세스 kill 등 edge 에서만 누수.

  세 FIND 는 모두 "Map with insufficient eviction" 이라는 관찰 수준의 공통
  테마를 갖지만, 실제 root cause 축(write-path cap 부재 / cleanup precondition
  불일치 / fallback timer 부재) 이 파일·자료구조·fix 방식 모두 다르다.
  domain-notes/gateway.md 의 memory-leak-hunter 실행 기록 (L158-208) 도 세
  건을 각각 독립 FIND 로 명시.

  → Epic 대신 3 single CANDs. 같은 PR 로 묶는 것은 CONTRIBUTING.md 의
  "one thing per PR" 위반.
proposed_title: "gateway/usage: costUsageCache has no cap/prune — stale entries accumulate across days"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-19
---

# gateway/usage: costUsageCache 에 cap/prune 부재로 distinct (startMs, endMs) 마다 엔트리 영속 누적

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/usage.ts:65` 의
`costUsageCache: Map<string, CostUsageCacheEntry>` 는 `(startMs, endMs)` 를 key 로
`CostUsageSummary` 결과를 보관한다. TTL(`COST_USAGE_CACHE_TTL_MS = 30_000`) 은
read-path 에서 stale 값을 무시할 때만 사용되며, 프로덕션에는 엔트리를 제거하는
경로가 존재하지 않는다.

## 관련 FIND

- FIND-gateway-memory-001: `parseDateRange` 가 `getTodayStartMs(now, ...)` 기반이라
  매일 새 cacheKey 생성. 이전 key 의 엔트리는 무기한 남는다. 운영자 dashboard
  노출 횟수에 비례하여 Map 성장.

## 근거 위치

- 선언: `src/gateway/server-methods/usage.ts:65`
- 누수 경로: `src/gateway/server-methods/usage.ts:302-352`
- test-only clear: `src/gateway/server-methods/usage.ts:365` (`__test.costUsageCache.clear()`)
- 대조 (same file, 올바른 eviction): L22-30 `resolvedSessionKeyByRunId` 의 oldest-first
  FIFO, L63-69 `sessionTitleFieldsCache` 의 while-loop FIFO evict

## 영향

- `impact_hypothesis: memory-growth` (slow leak)
- 운영 30일 ≈ 30 stale 엔트리. `CostUsageSummary` 는 세션 집계 결과 — 규모 의존적.
- 즉각적 OOM 은 아니나 장기(수 개월) 가동 서버에서 heap drift.
- P3 — 누적 속도 느림 + 엔트리 크기 세션 규모 의존.

## 대응 방향 (제안만)

동일 파일의 `sessionTitleFieldsCache` 패턴 (MAX + while-loop FIFO evict + optional
TTL prune-on-set) 참조. 구체 구현은 SOL 단계.

## 반증 메모

- `config-reload.ts` 경로에서 리셋되는지 미확인 (FIND self-check 에 명시).
- `loadCostUsageSummary` 결과 크기 프로파일링 안 함 — 세션 규모 작을 시 P4 강등
  가능.
