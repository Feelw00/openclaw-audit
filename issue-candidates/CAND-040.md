---
candidate_id: CAND-040
type: single
finding_ids:
- FIND-infra-process-concurrency-001
cluster_rationale: 단일 결함 — approval-handler-runtime.ts:498-537 deliverTarget 의 두 await
  (deliverPending L505, bindPending L517) 사이에 onStopped (L656-672) 가 실행되어 activeEntries.clear()
  호출 시, deliverTarget resume 후 L529-535 에서 wrapped entry 가 비어있던 Map 에 다시 등록되고 stopped
  핸들러로는 finalizeResolved/Expired 가 호출 안 됨 → native binding orphan + leak. Map operation
  자체는 atomic 이지만 await sync block 분리가 read-modify-write 를 외부 mutation 에 노출. file 내
  동기화 원시 0건 (Mutex/Semaphore/AsyncLock/AbortController/Promise.race/once/microtask
  모두 grep 0). 다른 FIND 와 file/axis 다름.
proposed_title: 'infra/approval-handler-runtime: stopped handler does not abort in-flight
  deliverTarget'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
cross_review_metric: metrics/cross-review-CAND-040-20260514-082000.jsonl
cross_review_decision: 'proceed-with-caveat: avg 0.81, critical-devil medium proceed-with-caveat.
  race mechanism 자체는 abandon 불가 (outer/inner 두 layer 모두 in-flight handleRequested
  promise 추적 부재). 단 FIND 가 outer approval-native-runtime.ts 만 지목하고 inner exec-approval-channel-runtime.ts:393-415
  spawn detached + stop() inflight await 부재 (진짜 root enabler) 를 누락. PR 본문 시 fix surface
  를 inner runtime stop() inflight tracking 으로 확장 권고. severity P3 유지.'
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - '566cbb24aa refactor: trim approval infra exports'
  - 'ce73e6647c refactor: trim approval runtime reexports'
  - 194c516957 (확인 필요)
  - '0f7d9c9570 fix(runtime): split approval and gateway client seams'
  - 'd78512b09d Refactor: centralize native approval lifecycle assembly (#62135)'
  finding: 6주 approval-handler-runtime.ts commit 모두 type/export refactor 또는 lifecycle
    assembly centralize. race/concurrent/lock 키워드 commit 0건. d78512b09d (#62135) 가
    lifecycle assembly 정리하면서 in-flight delivery vs onStopped race 영역 미터치.
  pr_search: gh pr list --search 'approval-handler-runtime race deliverTarget' → 0
    매치.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs: []
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-040-pre-20260516-105708.md
  prior_proof_records:
  - proofs/PROOF-CAND-040-pre-20260514-102041.md  # unit-level (2026-05-14)
  - proofs/PROOF-CAND-040-pre-20260515-104500-e2e-blocked-handler-wire.md  # blocked (2026-05-15)
  measurements:
    scenario: proof-CAND-040-e2e
    trials: 3
    chain_works_trials: 3
    leak_trials: 3
    fix_observed_trials: 0
    fire_rate: 1.0
    per_trial_counts:
    - prepareTarget: 1
      'deliverPending.enter': 1
      'deliverPending.exit': 1
      bindPending: 1
      unbindPending: 0
    - prepareTarget: 1
      'deliverPending.enter': 1
      'deliverPending.exit': 1
      bindPending: 1
      unbindPending: 0
    - prepareTarget: 1
      'deliverPending.enter': 1
      'deliverPending.exit': 1
      bindPending: 1
      unbindPending: 0
    base_sha: 859286b95e
    summary: '옵션 C 성공 (이 세션, 2026-05-16). silent skip root cause = (a) plugin-loader-side
      store ≠ caller-side store, (b) register 시점은 startAccount(opts) 안에서 opts.channelRuntime
      사용 (telegram pattern), (c) capability.native (ChannelApprovalNativeAdapter) 없으면
      deliveryPlan.targets=[], (d) prepareTarget 반환에 dedupeKey 필요, (e) server-side lease
      dispose 별도 file-flag watcher 필요. 5 발견 정리 + stub 보강 + probe ack-wait 제거.
      race window 직접 측정: deliverPending park → server-side onStopped fire (activeEntries.clear)
      → deliverPending release → bindPending → activeEntries.set 재삽입 → unbindPending 영구
      미호출. 3/3 trial leak.'
    stub_plugin_dir: skills/real-behavior-proof/harness/cand040-stub-plugin/
    scenario_file: skills/real-behavior-proof/scenarios/proof-CAND-040-e2e.py
  scenario: proof-CAND-040-e2e
---

# infra/approval-handler-runtime: stopped handler does not abort in-flight deliverTarget

## 문제 요약

`src/infra/approval-handler-runtime.ts:498-537` `createChannelApprovalHandlerFromCapability` 내부 closure 의 `activeEntries: Map<...>` (L444) 는 deliverTarget / finalizeResolved / finalizeExpired / onStopped 네 경로가 공유하는 shared mutable state. file 내 동기화 원시 0건 (Mutex/Semaphore/AsyncLock/AbortController/Promise.race/once/microtask 모두 grep 0).

deliverTarget (L498-537) 의 진행:
1. L505 `await nativeRuntime.transport.deliverPending(...)` — pending entry 생성
2. L517 `await nativeRuntime.interactions?.bindPending?.(...)` — binding 생성
3. L525-535 wrapped 생성 + `activeEntries.get(request.id) ?? {...entries:[]}` + push + `activeEntries.set(request.id, ...)`

이 두 await 사이에 onStopped (L656-672) 가 실행되어 activeEntries 의 기존 entry 들에 unbindWrappedEntries 호출 후 L671 `activeEntries.clear()` 수행 → 핸들러 stopped. deliverTarget resume → L529 get → undefined (clear 됨) → fallback `entries: []` 신규 객체 생성 → set 으로 비어있던 Map 에 다시 등록.

wrapped 는 stopped 핸들러로는 어떤 finalize 경로도 도달 안 함 (caller dispatcher 가 stopped 핸들러 dispatch 중단). 결과: wrapped.binding 이 unbindPending 으로 도달 못 함 → native 측 listener / 통신 채널 등록이 명시적 unbind 없이 영구 점유. GC 만으로는 정리 안 됨 (unbindPending 의 존재 자체가 외부 자원 cleanup 계약 시사).

발현 빈도: channel/plugin reload 또는 핸들러 교체 시. 정상 운영에서는 드물지만 config watcher 가 channel approval 설정을 자주 갱신하는 환경에서 누적.

## fix surface

옵션 A (minimal): deliverTarget 진입 시 stopped flag 체크 — onStopped 에서 set 한 flag 를 deliverPending await 후 / bindPending await 후 / set 전에 확인. set 진입 전 abort 시 native cleanup 수행 후 null 반환.
옵션 B (정밀): AbortController 도입 — onStopped 가 abort signal 발사 + deliverTarget 의 각 await 가 signal 체크. 다만 nativeRuntime.transport.deliverPending / bindPending 시그니처가 signal 받지 않으므로 그쪽 변경 동반.
옵션 C (mutex): activeEntries 접근에 명시적 Mutex 추가 — onStopped 가 in-flight deliverTarget 의 mutex 획득까지 대기.

옵션 A 가 minimal (XS PR, in-file 만 변경).

## upstream-dup 검사 결과

- 6주 approval-handler-runtime.ts commit 모두 type/export refactor. race/concurrent 키워드 commit 0건.
- duplicate_decision: not-duplicate

## next steps

1. caller (approval-native-runtime.ts, 셀 밖) 가 onStopped 호출 전 in-flight deliverTarget 의 promise 를 await 하는지 R-7 확인 — 그렇다면 race 미발생 (P3 → abandon). 미확인 시 P3 유지.
2. fix branch — deliverTarget 진입 / 각 await 후 / set 직전에 stopped flag 체크 + native cleanup 분기.
3. 회귀 테스트 — Deferred gate 로 deliverPending await park → onStopped 호출 → gate release → wrapped 가 unbind 되었는지 또는 새 Map slot 에 안 들어갔는지 검증.

## 관련 FIND

- FIND-infra-process-concurrency-001: deliverTarget 의 activeEntries RMW 가 onStopped 의 clear 와 race → wrapped binding orphan + native resource leak.
