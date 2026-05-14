---
candidate_id: CAND-036
type: single
finding_ids:
  - FIND-context-engine-error-boundary-002
cluster_rationale: "단일 결함 — registry.ts:239-267 invokeWithLegacyCompat 의 retry 가 method 의 side-effect 여부 무관하게 fire. 정규식 매칭 (L128-147 14패턴) 기반 retry 결정이 schema validation 외 텍스트 (LLM raw error echo 등) 에도 false-positive 매칭 가능 → idempotent 가 아닌 compact()/ingest() 가 LLM API 에 1-3회 호출. 현재 LegacyContextEngine 만 production 활성이라 발현 0 (future-proofing), 그러나 third-party plugin engine 도입 시 즉시 활성. 다른 context-engine FIND 와 file/axis 모두 다름."
proposed_title: "context-engine/registry: invokeWithLegacyCompat retries side-effectful methods on pattern-matched errors"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: abandoned
cross_review_metric: metrics/cross-review-CAND-036-20260514-082000.jsonl
retracted_reason: 'cross-review CAL-001 + low-impact + synthetic: avg 0.51 abandon. critical-devil weaken-or-abandon/high — production 활성 engine 이 LegacyContextEngine 단일, third-party engine 미도입 (263a190fc9 의 third-party 허용은 인프라만 추가, 실제 등록 0). hot-path-tracer valid-but-low-impact/high — retry 가 idempotent 가 아닌 method 에 fire 하더라도 LegacyContextEngine 의 compact()/ingest() 는 LLM API 호출이지만 raw error echo 매칭은 정규식 14패턴 중 매우 좁은 surface. reproduction-realist abandon-as-synthetic — third-party engine mock 없이 회귀 테스트 의미 불명.'
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "263a190fc9 Context engine/plugins: accept third-party engines whose info.id differs from registered slot id (#66678)"
    - "59d07f0ab4 fix(plugins): roll back failed register globals"
    - "6aa4515798 fix(context-engine): gracefully degrade to legacy engine on third-party plugin resolution failure (#66930)"
    - "2677f7cf14 fix: validate resolved context engine contracts (#63222)"
    - "d8a600f2ad context-engine: pass runtime context to ContextEngineFactory (#67243)"
  finding: "6주 registry.ts commit 9건 중 invokeWithLegacyCompat / detectRejectedLegacyCompatKeys 영역 수정 0건. 263a190fc9 (info.id mismatch 허용) 와 6aa4515798 (graceful-degrade) 가 인접 lifecycle 영역이지만 retry 의 side-effect-safety 축 미터치."
  pr_search: "gh pr list --search 'invokeWithLegacyCompat OR legacyCompat retry' → 0 매치."
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs: []
---

# context-engine/registry: invokeWithLegacyCompat retries side-effectful methods on pattern-matched errors

## 문제 요약

`src/context-engine/registry.ts:220-268` `invokeWithLegacyCompat` 가 `wrapContextEngineWithSessionKeyCompat` Proxy (L295-314) 에 의해 SESSION_KEY_COMPAT_METHODS (compact/ingest/assemble/afterTurn/bootstrap/maintain/ingestBatch) 의 모든 호출을 감싼다. plugin engine method 가 throw 한 error 메시지가 L128-147 의 14개 정규식 (sessionKey/prompt 토큰을 quote 로 감싼 패턴) 에 매치되면 sessionKey/prompt 키를 params 에서 제거하고 method 를 재호출 — 키 별로 최대 2회 retry (assemble 최대 3 호출).

이 retry 는 method 의 **side-effect 가 이미 일어났는지 여부와 무관** 하게 발사. compact() 가 LLM API 호출 → 응답 파싱 단계에서 "sessionKey" 토큰 포함 메시지로 throw 하면 retry 가 LLM API 2번째 호출 발사. caller 입장에서 결과는 1개지만 LLM provider 청구 1-3 회.

false-positive 위험: 정규식이 schema validation 외 텍스트 (사용자 prompt 에 "sessionKey" 단어 포함 + LLM 응답이 echo + parse 실패) 에도 매치 가능.

현재 production: LegacyContextEngine 만 활성 (sessionKey 무시 → schema throw 0 → retry 0). third-party plugin engine 도입 시점에 즉시 활성. P3 future-proofing.

## fix surface

옵션 A (보수): retry 결정에 method 별 idempotency hint 추가 (e.g. SESSION_KEY_COMPAT_METHODS 를 단순 array 대신 `{name, idempotent: boolean}`). compact/ingest 는 idempotent=false → retry 회피, assemble 은 idempotent=true → retry 허용.
옵션 B (보다 정밀): retry 발사 전 plugin engine 에 capabilities probe 또는 별도 pre-check 호출 — 다만 복잡.
옵션 C (전면 재설계): retry 자체를 plugin SDK 의 명시적 contract 로 옮김 — engine 이 자기 retry 책임 표현. PR 크기 커서 보류.

옵션 A 가 minimal (S PR).

## upstream-dup 검사 결과

- 6주 invokeWithLegacyCompat 영역 commit 0건.
- duplicate_decision: not-duplicate

## next steps

1. third-party plugin engine 의 production 활성 시점에 P3 → P2 격상 검토.
2. fix branch — SESSION_KEY_COMPAT_METHODS 의 idempotency 메타데이터 추가.
3. 회귀 테스트 — idempotent=false 메서드의 throw mock + retry 미발사 검증.

## 관련 FIND

- FIND-context-engine-error-boundary-002: invokeWithLegacyCompat 의 정규식 기반 retry 가 side-effectful method 에 중복 호출.
