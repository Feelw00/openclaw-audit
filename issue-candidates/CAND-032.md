---
candidate_id: CAND-032
type: single
finding_ids:
- FIND-auto-reply-error-boundary-002
cluster_rationale: 단일 결함 — reply-run-registry.ts:505 `void backend.queueMessage(text)`
  가 .catch 부재. backend (pi-embedded-runner 의 activeSession.steer) reject 시 unhandled
  rejection → infra/unhandled-rejections.ts 의 non-transient 분류 시 process.exit(1) 위험.
  자매 경로 (pi-embedded-runner/runs.ts:148-154) 는 `.catch(err => diag.debug(...))` 로
  보호 — 비대칭 누락. crash 가능성 (P2) 으로 다른 auto-reply FIND 들과 분리.
proposed_title: 'auto-reply/reply-run-registry: catch floating backend.queueMessage
  to avoid unhandled rejection'
proposed_severity: P2
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - '3d3a2399b5 fix(logging): track reply runs in diagnostics'
  - '8a23485472 fix(reply): preserve queue metadata after perf cherry-picks'
  - '0909df1a4f refactor: centralize reply followup drain lifecycle'
  finding: 6주 reply-run-registry.ts 영역 commit 어디에도 queueReplyRunMessage 의 catch chain
    추가 0건. 자매 경로 (pi-embedded-runner/runs.ts:148-154) 의 .catch 패턴은 별 위치에서 유지.
  pr_search: gh pr list --search 'queueReplyRunMessage OR backend.queueMessage' →
    0 매치.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs: []
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-032-pre-20260514-100639.md
  measurements:
    scenario: proof-CAND-032
    trials: 5
    unhandledCount: 5
    graceMs: 500
    results:
    - sessionId: proof-session-0-1778753193032
      queued: true
    - sessionId: proof-session-1-1778753193032
      queued: true
    - sessionId: proof-session-2-1778753193032
      queued: true
    - sessionId: proof-session-3-1778753193032
      queued: true
    - sessionId: proof-session-4-1778753193032
      queued: true
  scenario: proof-CAND-032
---

# auto-reply/reply-run-registry: catch floating backend.queueMessage to avoid unhandled rejection

## 문제 요약

`src/auto-reply/reply/reply-run-registry.ts:496-507` `queueReplyRunMessage` 는 boolean 만 sync 로 보고하는 API. L505 `void backend.queueMessage(text)` 가 `.catch` 없이 fire-and-forget. backend 구현 (`src/agents/pi-embedded-runner/run/attempt.ts:2767-2772`) 은 `await activeSession.steer(text)` — IO + state mutation 동반이라 throw 가능 (abort race, internal RuntimeError, transient network).

reject 시:
- transient class (ECONNRESET, EAI_AGAIN 등): infra/unhandled-rejections.ts:345 의 handler 가 warn-only → 사용자 메시지 silent loss (caller chain prepareEmbeddedPiQueueMessage 는 이미 `queued:true` 반환했음)
- non-transient class (TypeError, generic Error): `exitWithTerminalRestore` → process.exit(1) → gateway 전체 crash

자매 경로 (pi-embedded-runner/runs.ts:148-154) 의 동일 fire-and-forget 패턴은 `.catch(err => diag.debug(...))` 로 보호 — 동일 author / 동일 디자인 intent 에서 본 라인만 비대칭 누락.

## fix surface

- reply-run-registry.ts:505 를 `void backend.queueMessage(text).catch(err => diag.debug(\`queue message rejected after enqueue: ...\`))` 로 변경.
- diag log 채널은 자매 경로의 동일 형태 reuse.
- XS PR (1 라인 변경).

## upstream-dup 검사 결과

- 6주 reply-run-registry.ts commit 어디에도 본 라인 catch 추가 0건.
- 자매 경로의 .catch 패턴이 별 위치에 이미 존재 — 본 file 만 누락.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — 1 라인 .catch 부착.
2. 회귀 테스트 — reply-run-registry.test.ts 에 `vi.fn(async () => { throw new Error('boom') })` stub 으로 reject 시나리오 추가 + unhandled rejection 미escalate 검증.
3. CAL-003 — backend reject 가 production race window 임을 자매 경로 author 동일 의도 / 보호 부착 사실로 입증.

## 관련 FIND

- FIND-auto-reply-error-boundary-002: queueReplyRunMessage 의 void backend.queueMessage 가 .catch 부재 → process crash (non-transient) 또는 silent loss (transient).
