---
candidate_id: CAND-031
type: single
finding_ids:
  - FIND-auto-reply-error-boundary-001
cluster_rationale: "단일 결함 — drain.ts:298-313 의 catch+finally 가 max-attempts / backoff / dead-letter 없이 scheduleFollowupDrain self-recurse 무한 재시도. deterministic-fail (preflight compaction 의 ENOENT/EACCES, resolveQueuedReplyExecutionConfig invalid throw 등) 시 동일 head item 에 debounceMs=500ms 간격 무한 retry → 24h ≈ 170k log lines + typing keepalive 폭주. 다른 auto-reply FIND 들과 다른 axis (lifecycle vs error-boundary 무한 재시도)."
proposed_title: "auto-reply/queue/drain: bound retry loop on non-transient failures"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
cross_review_metric: metrics/cross-review-CAND-031-20260514-082000.jsonl
cross_review_decision: 'proceed-with-caveat: avg 0.89, critical-devil medium proceed-with-caveat. fix surface (attempts counter) 는 정당, 단 evidence 의 deterministic source 예시는 잘못된 source (fs ENOENT 는 catch{} swallow). PR 본문 시 contextEngine.compact() / ensureRuntimePluginsLoaded / resolveCommandSecretRefsViaGateway plugin throw 로 교체 필요. severity P3 유지.'
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "137d566422 fix(auto-reply): guard FOLLOWUP_QUEUES delete against late drain finally"
    - "712644f0d9 fix(queue): preserve pending items during drains"
    - "43d4be9027 fix(queue): split collect batches by auth context (#66024)"
    - "0909df1a4f refactor: centralize reply followup drain lifecycle"
  finding: "6주 drain.ts commit 8건 모두 ordering / data-integrity / centralize 축. retry 상한 / circuit breaker / dead-letter 도입 commit 0건. preserve-pending-items 정책 (712644f0d9) 자체가 retry storm 의 enabler."
  pr_search: "gh pr list --search 'drain followup scheduleFollowupDrain' → 0 매치. retry 상한 / poison-pill 키워드 PR 없음."
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
  - CAND-012  # 같은 file (drain.ts), 다른 axis (identity guard 는 outer finally, 본 FIND 는 catch+finally retry 정책)
---

# auto-reply/queue/drain: bound retry loop on non-transient failures

## 문제 요약

`src/auto-reply/reply/queue/drain.ts:298-313` 의 try/catch/finally 가 effectiveRunFollowup callback 의 throw 를 catch 에서 `defaultRuntime.error?.(\`...${String(err)}\`)` 로만 log 하고, finally 에서 `queue.items.length > 0` 이면 무조건 `scheduleFollowupDrain` 를 재호출한다. max-attempts / exponential backoff / dead-letter / circuit-break 부재.

비-transient deterministic fail (preflight compaction 의 fs ENOENT/EACCES, resolveQueuedReplyExecutionConfig 의 invalid config throw, runtimeConfig JSON RangeError 등) 시 동일 key, 동일 head item 에 대해 debounceMs=500ms (state.ts:19 DEFAULT_QUEUE_DEBOUNCE_MS) 간격 무한 재시도. drainNextQueueItem (queue-helpers.ts:151-156) 의 `await run(next)` → `items.shift()` 순서로 throw 시 shift unreachable → items[0] 영구 잔존.

24h ≈ 172,800 회 동일 error log + createReplyOperation/typing.markRunComplete/markDispatchIdle 폭주 → typing keepalive reset 으로 Telegram/Slack adapter typing ping 부하.

## fix surface

옵션 A (minimal): queue/state.ts 에 `attempts: number` + `lastErrorAt: number` 추가. drain 의 catch 에서 attempts++, MAX_ATTEMPTS (e.g. 5) 초과 시 dead-letter (queue.items.shift + log.error + emit metric) 후 다음 item.
옵션 B (정밀): exponential backoff (attempts*debounceMs, cap 30s) + dead-letter Map 별도 (DEAD_LETTER_FOLLOWUP) 보관 + 운영자가 manual drain 가능.

XS PR (옵션 A) 추정.

## upstream-dup 검사 결과

- 6주 drain.ts commit 8건 모두 ordering / data-integrity. retry 상한 축 0건.
- preserve-pending-items 정책 (712644f0d9) 자체가 retry storm enabler — 본 FIND 는 그 정책의 escape valve 추가.
- duplicate_decision: not-duplicate

## next steps

1. 옵션 A 시도 (state.ts attempts field + drain catch MAX_ATTEMPTS check + dead-letter shift) — XS PR.
2. 회귀 테스트: queue.collect.test.ts:716 의 transient retry 시나리오는 그대로 통과. 추가: deterministic-fail run mock + N회 retry 후 dead-letter 확인.
3. CAL-003 회피: production deterministic-fail 시나리오 (preflight compaction ENOENT 등) 가 hot-path 인지 확인.

## 관련 FIND

- FIND-auto-reply-error-boundary-001: drain catch+finally self-recurse 가 deterministic-fail 시 debounceMs=500ms 간격 무한 재시도.
