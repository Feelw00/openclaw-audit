---
candidate_id: CAND-033
type: single
finding_ids:
- FIND-auto-reply-lifecycle-001
cluster_rationale: '단일 결함 — drain.ts:239-264 collect mode 의 auth-groups inner for
  loop 가 snapshot 패턴 (L221 queue.items.slice() + L223 splitCollectItemsByAuthorization)
  으로 동작. 각 iteration 의 await 사이에 외부 clearSessionQueues / clearFollowupQueue 가 발생해도
  inner for 본문은 pre-captured groupItems 를 계속 model 로 전달. CAND-012 (PR #68839 MERGED)
  의 identity guard 는 outer finally 만 보호 — inner-loop axis 별개. /stop 부분 적용 (사용자 abort
  후에도 일부 메시지 모델 전달) 으로 P2.'
proposed_title: 'auto-reply/queue/drain: collect-mode inner loop ignores mid-iteration
  session reset'
proposed_severity: P2
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - '137d566422 fix(auto-reply): guard FOLLOWUP_QUEUES delete against late drain finally'
  - '712644f0d9 fix(queue): preserve pending items during drains'
  - '43d4be9027 fix(queue): split collect batches by auth context (#66024)'
  - '8a23485472 fix(reply): preserve queue metadata after perf cherry-picks'
  - '468c6a0101 perf(core): trim reply and agent allocation churn'
  - '3e2bc28e51 fix: forward chat images to acp dispatch'
  finding: 6주 drain.ts commit 8건 모두 outer guarantee / data integrity 축. inner for-loop
    x mid-await cancel race 0건. 137d566422 (CAND-012) 가 outer finally identity guard
    만 추가했고 inner-loop axis 는 검토 범위 외였다.
  pr_search: gh pr list --search 'auto-reply queue drain in:title,body' → 본 file OPEN
    PR 없음.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
- CAND-012
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-033-pre-20260514-101425.md
  measurements:
    scenario: proof-CAND-033
    trials: 3
    trialResults:
    - trial: 0
      callList:
      - X
      - Y
    - trial: 1
      callList:
      - X
      - Y
    - trial: 2
      callList:
      - X
      - Y
    yLeakCount: 3
  scenario: proof-CAND-033
pre_sol_proof_e2e:
  status: abandoned
  proof_record: proofs/PROOF-CAND-033-pre-20260515-090000-e2e.md
  decision_date: 2026-05-15
  reason: 'authGroups ≥ 2 trigger 조건이 single telegram user account 로 만들 수 없음.
    resolveFollowupAuthorizationKey 가 sender/exec 필드만 보는데 single account 의 메시지는
    모두 동일 sender → authGroups=1 → inner-for race window 자체가 안 열림. burner phone +
    2nd telegram account 인프라 (multi-session size) 시 e2e 가능하나 effective severity 추정
    낮음 + CAL-003 위험 (CAND-032 와 같은 종류 좁은 race window) 으로 사용자 drop 결정.'
retracted_reason: 'drop-2026-05-15: production reproducibility 인프라 부담 큼 + effective
  severity 추정 낮음. unit-level yLeakCount=3 은 결함 메커니즘 입증이지만 production
  hot-path 자연 발현 가능성 낮고 (multi-sender 동시 메시지 + drain 도중 clearSessionQueues
  race window) 2nd telegram account 인프라 비용 큼. CAL-003 의 좁은 race window CAND 패턴
  (CAND-032 와 동일 종류) — SOL 작성 가치 의문이라 CAND 자체 drop.'
---

# auto-reply/queue/drain: collect-mode inner loop ignores mid-iteration session reset

## 문제 요약

`src/auto-reply/reply/queue/drain.ts:194-265` collect mode branch 는 outer while iter 시작 시 L221 `queue.items.slice()` + L223 `splitCollectItemsByAuthorization` 으로 authGroups snapshot. inner for loop (L239-264) 가 각 group 에 대해 `await effectiveRunFollowup(...)` (L252) — 매 iteration 사이 외부 cleanup (`clearSessionQueues` / `clearFollowupQueue`) 발생 시 inner for 가 그 신호를 받을 채널이 부재.

발현:
- T0 queue.items=[A,B,C] (auth K1,K1,K2 → authGroups=[[A,B],[C]])
- T2 iter1 await effectiveRunFollowup([A,B]) microtask yield
- T3 다른 채널에서 /stop 도착 → clearSessionQueues → queue.items.length=0 + FOLLOWUP_QUEUES.delete + clearFollowupDrainCallback
- T4 iter1 resolve → queue.items.splice(0,2) no-op (empty)
- T5 iter2 groupItems=[C] (pre-captured) → effectiveRunFollowup → **C 가 model 로 전달되어 실행됨** (사용자 abort 후에도 model 호출 + tool 실행 + LLM 비용)

CAND-012 fix (137d566422) 는 drain.ts:307 outer finally identity guard 만 적용. inner for 본문에 동등 가드 없음. snapshot 패턴 자체가 외부 mutation 면역 — clearSessionQueues 가 in-flight drain 을 cancel 할 채널 부재.

## fix surface

옵션 A (가장 정밀): inner for 의 각 iteration 시작 시 `if (FOLLOWUP_QUEUES.get(key) !== queue) break;` 가드 추가. CAND-012 의 identity check 를 inner-loop level 로 확장.
옵션 B (추가 보호): clearSessionQueues 에 AbortController 전파 — drain IIFE 가 받은 signal 의 aborted 체크. 다만 closure 수명 / multi-key 처리가 복잡.

옵션 A 가 minimal (XS PR). 옵션 B 는 별도 follow-up.

## upstream-dup 검사 결과

- 6주 drain.ts commit 8건 모두 outer level. inner-loop axis 0건.
- duplicate_decision: not-duplicate (CAND-012 와 동일 가족, 다른 axis)

## next steps

1. fix branch — inner for 본문에 identity check 추가.
2. 회귀 테스트: drain.collect-mode.inner-cancel.test.ts (가칭) — Deferred gate 로 iter1 await park → clearSessionQueues 호출 → gate release → iter2 의 effectiveRunFollowup 미호출 확인.
3. CAL-003 — production hot-path 가 'collect mode + N≥2 authGroups + /stop' 임을 splitCollectItemsByAuthorization auth key (sender/exec context) 의 production 분포로 입증.

## 관련 FIND

- FIND-auto-reply-lifecycle-001: collect mode inner for-loop snapshot 패턴이 mid-await clearSessionQueues 면역 → 사용자 abort 후에도 pre-captured group 이 모델로 전달.
