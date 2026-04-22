---
candidate_id: CAND-023
type: single
finding_ids:
  - FIND-gateway-concurrency-003
cluster_rationale: |
  단독 FIND. CAND-021 의 cluster_rationale 참조 — 같은 셀의 FIND-001/002 와
  각각 다른 root cause axis.

  FIND-003 root_cause_chain:
    [0] "attachment branch 에서 chatAbortControllers.get(L1920) 과
         .set(L1960) 사이에 resolveGatewayModelSupportsImages + parseMessage
         WithAttachments real I/O."
    [1] "완료 dedupe (L1912) 는 in-flight 방어 불가 — setGatewayDedupeEntry
         는 terminal snapshot 에만 저장."
    [2] "attachment parsing 을 guard 밖에 둔 설계 결정 — input validation
         성격이라 handler 초반에 두었으나 concurrency window 를 열어둠."

  특이점: no-attachment branch 는 race 없음 (L1920 → L1960 사이 await 부재,
  CAL-001 올바른 guard 예시). FIND-001/002 와 달리 "conditional race" 이고,
  fix 축은 "attachment parsing 을 guard 안으로 이동" 또는 "post-parse 재체크"
  로 파일 (chat.ts) + 접근 방식 모두 독립.
proposed_title: "gateway/chat.send: attachment branch race — chatAbortControllers check/set separated by image+media I/O spawns duplicate agent runs"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-22
---

# gateway/chat.send: attachment 경로에서 `chatAbortControllers` check/set 사이 I/O await race — 동일 runId 로 중복 agent run spawn

## 공통 패턴

단일 FIND 기반 single CAND. `src/gateway/server-methods/chat.ts:1920-1968`
의 `chat.send` 핸들러는 L1920 에서 `chatAbortControllers.get(clientRunId)`
sync 확인하고 L1960 에서 set. **attachment 가 있는 경우** 그 사이에
`resolveGatewayModelSupportsImages` (L1930) 와 `parseMessageWithAttachments`
(L1936) 의 real I/O await 이 끼어든다. 동일 `clientRunId` 로 재전송된
chat.send 2개가 이 window 에서 만나면 둘 다 set 을 수행하고 각자 agent
run 을 spawn → **동일 runId 로 LLM 호출 2회 + lifecycle event 충돌**.

## 관련 FIND

- FIND-gateway-concurrency-003 (P1): attachment 포함 chat.send 의 재시도 —
  image/문서 첨부 chat UI 에서 가장 흔한 경로. parseMessageWithAttachments
  의 수십~수백 ms I/O 가 race window 를 열어둠. cross-ref: FIND-gateway-
  memory-003 (`agentRunStarts` safety belt 부재) 와 조합 시 중복 spawn 의
  cleanup 미싱으로 Map leak 가능.

## 근거 위치

- sync check: `src/gateway/server-methods/chat.ts:1920`
- attachment branch: `src/gateway/server-methods/chat.ts:1928-1956`
  - `resolveGatewayModelSupportsImages` await: L1930
  - `parseMessageWithAttachments` await: L1936
- set (race window 이후): `src/gateway/server-methods/chat.ts:1960`
- 완료-dedupe 는 in-flight 방어 불가: `src/gateway/server-methods/agent-wait-dedupe.ts:206-220`
- no-attachment branch (race 없음, CAL-001 올바른 guard 예시): L1920→L1960
  사이 await 없음
- cross-ref: CAND-016 (`agentRunStarts` safety belt — upstream PR #68801 로
  duplicate 회피 하였음)

## 영향

- `impact_hypothesis: wrong-output` — 동일 runId 로 LLM 호출 2회 + 비용 ×2,
  session transcript 에 delta chunk interleave, 일부 lifecycle 이벤트 누락.
- 빈도: 모바일 네트워크에서 큰 첨부 업로드 → 타임아웃 → retry 시 재현.
  production 에서 흔한 경로.
- severity P1 — LLM 비용 + session state corruption 가능성 + agent
  lifecycle 정합성 붕괴.

## 대응 방향 (제안만)

- 옵션 A: attachment parsing 을 `chatAbortControllers.set` 이후 (work IIFE
  안) 로 이동. guard 의 "첫 statement" 를 set 으로.
- 옵션 B: attachment parsing 직후 `chatAbortControllers.get(clientRunId)`
  로 재체크하고 이미 존재 시 조기 반환.
- 옵션 C: sentinel pre-insert — L1920 직후 placeholder entry 를 set 하고
  parsing 실패 시 delete.

구체 구현은 SOL 단계.

## 중복 검사 (upstream)

`git log upstream/main --since="3 weeks ago" -- src/gateway/server-methods/chat.ts`
→ 관련 race fix 없음. CAL-008 경고 없음. domain-notes/gateway.md R-8
확인에서도 chat attachment race 는 이번 감사가 첫 발굴.

## 반증 메모

- agent runner (`src/agents/`) 내부의 runId 기반 dedupe 가 있을 가능성 —
  있다면 피해 일부 완화. gateway 층 guard 의 의도 무효화는 여전히 결함.
- `parseMessageWithAttachments` 가 fs cache hit 인 경우 window 매우 짧음 —
  그래도 microtask 경계만으로도 race 성립.
- cross-ref 로 CAND-023 fix 가 FIND-gateway-memory-003 (agentRunStarts
  drift) 의 발현 조건에도 영향. CAND-016 이 upstream PR #68801 로 abandon
  됐음에도 중복 spawn 자체가 줄어 indirect 효과 있음.
