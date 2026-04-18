---
candidate_id: CAND-010
type: epic
finding_ids:
  - FIND-agents-registry-concurrency-001
  - FIND-agents-registry-concurrency-002
cluster_rationale: |
  공통 원인: "subagent-registry 의 beginSubagentCleanup atomic guard
  (lifecycle.ts:280-291, cleanupHandled/cleanupCompletedAt 플래그 기반 sync check-then-set)
  가 일부 완료/복원 경로를 보호하지 못해, 동일 runId 에 대한 두 번째 호출이
  side-effect 를 중복 dispatch 한다."

  두 FIND 모두 **같은 registry + 같은 atomic guard** 의 커버리지 갭을 노리며,
  symptom_type (concurrency-race) 과 발현 패턴 (동일 runId 에 대한 두 비동기 경로
  동시 진입 → guard 바깥 side-effect 2회 발사) 이 일치한다.

  근거 (root_cause_chain 직접 인용):

  - FIND-agents-registry-concurrency-001 root_cause_chain[1]:
    "retryDeferredCompletedAnnounces (lifecycle.ts:309-312) 는
    finalizeResumedAnnounceGiveUp 호출 직전 `if (!beginSubagentCleanup(runId))
    continue;` 가드를 거친다. ... resumeSubagentRun 은 이 가드를 적용하지 않는다."
    → resume 경로가 guard 를 건너뛰고 finalizeResumedAnnounceGiveUp 을 직접 dispatch.

  - FIND-agents-registry-concurrency-002 root_cause_chain[1]:
    "completeSubagentRun 의 순서가 lifecycle.ts:639 cleanupBrowserSessions 호출 →
    :644 startSubagentAnnounceCleanupFlow 호출 순이다. beginSubagentCleanup 는
    startSubagentAnnounceCleanupFlow 내부 (lifecycle.ts:476, 492) 에서
    호출되므로 :639 cleanupBrowserSessions 시점에는 cleanupHandled 플래그가 아직
    false."
    → complete 경로가 browser cleanup 을 guard 진입 전에 실행.

  두 FIND 모두 동일한 `beginSubagentCleanup` / `cleanupHandled` 메커니즘이
  해결의 열쇠이며, R-5 실행 조건 분류 표가 양쪽에서 "unguarded call site" 를
  특정한다:

  - FIND-001 R-5: registry.ts:408 (resume retry-limit) / 420 (resume expiry) →
    "beginSubagentCleanup guard 없음 / resumedRuns.add 없음 → no-guard".
  - FIND-002 R-5: lifecycle.ts:639 (cleanupBrowserSessionsForLifecycleEnd) →
    "guard 없음 / unguarded race window".

  Epic 묶는 이유 (해결책 공통성 추정, 해결책 자체 기술 금지):
  두 결함 모두 "beginSubagentCleanup atomic guard 의 보호 범위 확장" 이라는
  동일 축의 수정으로 접근 가능하며, 같은 코드 소유자 / 같은 테스트 파일
  (subagent-registry*.test.ts) 범위에 놓인다. one-thing-per-PR 원칙상 각
  호출 지점마다 별도 커밋이 필요할 수 있으나, 동일 issue 하에서 task 단위로
  정리하는 것이 리뷰 effort 및 중복 설명을 줄인다.

  cross-file 차이 (다른 파일 registry.ts vs lifecycle.ts) 와 다른 trigger source
  (restore+steer-restart vs listener+gateway-RPC) 는 존재하지만, 공통 근본 원인
  (guard coverage gap) 이 명확하고 두 FIND 를 합쳐야 "subagent-registry 의
  cleanup guard 설계가 일관되지 않음" 이라는 상위 이슈가 드러난다.
proposed_title: "subagent-registry: beginSubagentCleanup atomic guard coverage gap causes double side-effect dispatch across resume/complete paths"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-19
---

# subagent-registry: beginSubagentCleanup atomic guard coverage gap causes double side-effect dispatch across resume/complete paths

## 공통 패턴

`src/agents/subagent-registry-lifecycle.ts:280-291` 의 `beginSubagentCleanup`
는 `entry.cleanupCompletedAt || entry.cleanupHandled` 를 check-then-set 하는
**sync atomic guard** 로, 동일 runId 에 대한 cleanup side-effect 를 1회로 묶는
역할을 한다. 그러나 이 guard 는 `startSubagentAnnounceCleanupFlow`
(lifecycle.ts:476, 492) 와 `retryDeferredCompletedAnnounces`
(lifecycle.ts:310) 에서만 호출된다.

- **resume 경로** (`resumeSubagentRun` registry.ts:379-462) 의 retry-limit/
  expiry 분기는 `void finalizeResumedAnnounceGiveUp(...)` 를 guard 없이 직접
  호출하며, `resumedRuns.add(runId)` 도 생략한다. 결과적으로 `resumedRuns.has`
  재진입 방지가 무력화되어 같은 runId 에 대한 후속 resume 이 같은 분기로
  재진입, `completeCleanupBookkeeping` 내부의 `notifyContextEngineSubagentEnded`
  와 `retryDeferredCompletedAnnounces` 가 2회 dispatch.

- **complete 경로** (`completeSubagentRun` lifecycle.ts:541-645) 의
  `cleanupBrowserSessionsForLifecycleEnd` 호출 (line 639) 은 `startSubagent
  AnnounceCleanupFlow` (line 644) 보다 **먼저** 실행되므로 beginSubagentCleanup
  보호 바깥에 놓인다. 임베디드 run 에서 listener + waitForSubagentCompletion
  가 동시 활성화될 때 두 호출자 모두 line 639 를 통과해 동일 childSessionKey
  에 대한 브라우저 세션 정리가 2회 dispatch.

공통 구조: **원자 가드가 있는 영역과 없는 영역이 병존하며, 없는 영역에서
side-effect 가 먼저 일어나므로 guard 가 도착했을 때는 이미 늦다**.

## 관련 FIND

- **FIND-agents-registry-concurrency-001** (P3):
  `resumeSubagentRun` 의 retry-limit/expiry 분기 (registry.ts:407-425) 가
  `resumedRuns.add` 와 `beginSubagentCleanup` 를 모두 누락. 동일 runId 에 대한
  후속 resume 호출이 line 380 `resumedRuns.has` 를 false 로 통과해 재진입,
  `finalizeResumedAnnounceGiveUp` 가 2회 dispatch. `completeCleanupBookkeeping`
  은 진입부 idempotency 없이 `notifyContextEngineSubagentEnded({reason:"deleted"})`
  와 `retryDeferredCompletedAnnounces` 를 매번 실행.

- **FIND-agents-registry-concurrency-002** (P2):
  `registerSubagentRun` (run-manager.ts:260-355) 이 `ensureListener()` +
  `void waitForSubagentCompletion(runId, ...)` 를 모두 기동. 임베디드 subagent
  종료 시 listener (registry.ts:659) 와 waitForSubagentCompletion
  (run-manager.ts:119) 이 동시에 `completeSubagentRun` 호출. 진입부 일반 경로
  early-return guard 부재 + `cleanupBrowserSessionsForLifecycleEnd`
  (lifecycle.ts:639) 가 `beginSubagentCleanup` 보다 먼저 실행 → 동일
  childSessionKey 에 대한 브라우저 세션 정리 2회 dispatch.

## 통합 Impact

- **Side-effect 중복 dispatch (양쪽 공통)**: guard 바깥 side-effect 가 2회
  dispatch 되며, 대상 모듈 (context-engine onSubagentEnded / browser-lifecycle-
  cleanup) 의 idempotency 에 따라 실제 영향 범위가 달라진다. 실제 구현은
  out-of-scope 이지만 non-idempotent 일 경우 세션 메트릭 이중 카운트, 캐시 키
  중복 삭제, warn 로그 중복 등.

- **데이터 정합성 리스크**:
  FIND-002 line 571 `if (entry.endedAt !== endedAt) entry.endedAt = endedAt`
  는 두 호출자의 `endedAt` 값이 다르면 last-writer-wins 로 덮어쓴다. listener 의
  `evt.data?.endedAt` 과 waitForAgentRun 의 `wait.endedAt` 이 다를 수 있다.

- **CPU / I/O 낭비**:
  FIND-001 `retryDeferredCompletedAnnounces` 중복 이터레이션 (O(N) runs 순회
  2회), `params.persist()` 중복 disk 쓰기, `safeRemoveAttachmentsDir` 중복 rm.
  FIND-002 `persistSubagentSessionTiming` + `freezeRunResultAtCompletion` +
  browser cleanup full-path 2회 실행.

- **hot-path 일치**:
  양쪽 모두 production 경로. 테스트는 단일 호출 시나리오만 커버 (`rg -n
  "resumeSubagentRun" / "ensureListener.*waitForSubagentCompletion"
  src/agents/*.test.ts` → 동시 호출 시나리오 매치 없음).

- **upstream/main 상태**:
  양쪽 모두 upstream/main (54cf4cd857 이후) 에 동일 패턴 존재, 미해결. 최근
  commit 48042c3875 는 `emitSubagentEndedHookForRun` 의 `endedHookEmittedAt`
  가드만 추가했고 나머지 구간은 동일 race 에 노출.

## 설계상 일관성 문제

`beginSubagentCleanup` 는 lifecycle.ts 내부에서는 **unconditional sync
atomic guard** 로 설계되어 있다 (280-291). 하지만 caller 쪽 계약이 일관되지
않다:

| 호출 지점 | beginSubagentCleanup guard | 분류 |
|---|---|---|
| `retryDeferredCompletedAnnounces` (lifecycle.ts:310→313) | 있음 | guarded |
| `startSubagentAnnounceCleanupFlow` (lifecycle.ts:476/492) | 있음 | guarded |
| `resumeSubagentRun` retry-limit (registry.ts:407-413) | 없음 | unguarded |
| `resumeSubagentRun` expiry (registry.ts:415-425) | 없음 | unguarded |
| `completeSubagentRun` → browser cleanup (lifecycle.ts:639) | 없음 | unguarded |
| `completeSubagentRun` → announce cleanup (lifecycle.ts:644) | 있음 (내부) | guarded |

즉, 동일 registry 내부에서 guard 적용 규칙이 경로마다 달라 "guard 우회
경로" 가 국소적으로 존재한다. 이 epic 은 해당 guard 커버리지 갭을
구조적으로 드러낸다.
