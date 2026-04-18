---
candidate_id: CAND-007
type: single
finding_ids:
  - FIND-infra-process-memory-001
cluster_rationale: |
  단일 FIND. installUnhandledRejectionHandler 의 idempotency 부재 문제는
  `src/infra/unhandled-rejections.ts` 파일에만 국한되며, 다른 FIND 들과
  공통 root cause 없음.

  - FIND-infra-retry-concurrency-002/003 은 jitter 수식 (backoff.ts /
    retry.ts) 문제로 파일·자료구조·symptom_type 모두 상이.
  - 해당 FIND 의 root_cause_chain[0] "function body 에 'already installed'
    guard 없음" 은 다른 FIND 어느 단계와도 의미론적으로 겹치지 않음.

  따라서 single CAND 로 처리.
proposed_title: "unhandled-rejections: installUnhandledRejectionHandler lacks idempotency, duplicates process listener"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-18
---

# unhandled-rejections: installUnhandledRejectionHandler lacks idempotency, duplicates process listener

## 공통 패턴

단일 FIND. `installUnhandledRejectionHandler()` (src/infra/unhandled-rejections.ts:339-345)
가 호출될 때마다 `process.on("unhandledRejection", ...)` 를 조건 없이 실행.
- 함수 내부에 "already installed" guard 없음 (root_cause_chain[0]).
- 정상 CLI 기동 경로에서 `src/index.ts:90` 와 `src/cli/run-main.ts:223` 두 곳에서
  독립 호출 → 리스너 2개 등록 (root_cause_chain[1]).
- uninstall public API 부재 (root_cause_chain[3]).

결과적으로 같은 rejection 이벤트에 대해 분기 로직이 중복 실행되고, 반복 진입
환경(vitest / 라이브러리 import) 에서는 리스너가 선형 누적되어 MaxListeners 경고.

## 관련 FIND

- FIND-infra-process-memory-001: installUnhandledRejectionHandler 가 호출마다
  process.on 호출 + idempotent guard 부재 + uninstall 경로 부재로 동일 프로세스 내
  리스너 중복 등록. 프로덕션 CLI 기본 경로에서 2회 호출 확인 (index.ts:90,
  run-main.ts:223). (P3)
