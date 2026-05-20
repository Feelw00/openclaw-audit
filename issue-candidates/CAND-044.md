---
candidate_id: CAND-044
type: single
finding_ids:
  - FIND-infra-retry-error-boundary-001
cluster_rationale: |
  두 FIND 는 같은 파일 (src/infra/retry.ts) 의 같은 함수 (retryAsync) 안에서,
  같은 symptom_type (error-boundary-gap) 으로, **retryAsync 의 에러 종료 경계가
  fn() 의 원본 실패 (lastErr) 를 호출자에게 충실히 전달하지 못한다** 는 단일
  계약 위반을 서로 다른 라인에서 드러낸다. clusterer.md Step 2 (동일 파일 +
  동일 함수 + 동일 symptom_type → merge/epic) 적용 대상.

  단순 line-overlap merge (Step 1) 는 아니다 — FIND-001 은 콜백 호출부
  (retry.ts:122-130, options 경로의 catch 블록 내 콜백 슬롯), FIND-002 는 최종
  throw 지점 (retry.ts:105/179) 으로 라인이 겹치지 않고 결함 메커니즘도 다르다.
  그러나 Step 2 의 "같은 symptom_type → merge" 에 해당하며, 두 FIND 를 잇는
  실질 공통 축이 존재하므로 두 single CAND 가 아닌 **하나의 epic CAND** 로 묶는다.

  공통 축 (각 FIND root_cause_chain 에서 직접 인용):
  - FIND-001 root_cause_chain[1].because: "원본 작업 실패는 lastErr 에 저장되고
    (L125) 정상 종료 시 L179 의 throw lastErr 로 전파된다. 콜백이 catch 블록
    안에서 throw 하면 제어가 L179 에 도달하기 전에 함수를 벗어나므로, lastErr 는
    throw 되지 못하고 콜백 에러가 그 자리를 대체한다."
  - FIND-002 root_cause_chain[0].because: "L105/L179 의 lastErr ?? new
    Error(\"Retry failed\") 에서 ?? 는 좌변이 null/undefined 이면 우변을
    택한다. fn() 이 throw undefined/reject(null) 하면 lastErr 가 정확히 그
    값이 되어, retry 가 추적한 \"실제 마지막 실패값\" 이 fallback 조건과 충돌한다."

  두 FIND 모두 종착점이 동일 코드 라인 (retry.ts:179 의 throw lastErr 표현식,
  number 경로는 L105) 이다. FIND-001 은 그 throw 에 *도달하기 전에* 콜백 throw
  가 lastErr 를 가로채고, FIND-002 는 그 throw 표현식 *자체* 가 falsy lastErr 를
  generic Error 로 치환한다. 즉 retryAsync 의 단일 에러 종료 경계 (L105/L179 의
  throw lastErr ?? ... + 그 직전 catch 블록의 무방비 콜백) 가 두 방향에서
  원본 실패 식별자를 손실시키는 동일 boundary 의 결함이다. 두 FIND 모두
  impact_hypothesis 가 data-loss (진단 정보 손실 / 에러 식별자 손실) 로 같다.

  epic 으로 묶는 이유 (해결책 자체는 기술 금지, 공통성만):
  1. 단일 파일 / 단일 함수 / 단일 종료 경계. fix surface 가 retryAsync 본문 내
     catch 블록 + 종료 throw 라는 인접 영역으로 자연 수렴 — openclaw 의 one
     thing per PR 관점에서 "retryAsync 의 에러 종료 경계 정합성" 한 task.
  2. 단일 회귀 테스트 축: retry.test.ts 에 "콜백 throw 시 원본 lastErr 보존" +
     "fn 이 falsy 값으로 reject 시 원본 값 보존" 두 케이스가 같은 파일 같은
     describe 블록에서 retryAsync 의 에러 전파 계약을 한 번에 검증 가능.
  3. 두 FIND 모두 retry.test.ts/retry-policy.test.ts 가 happy-path 콜백 +
     truthy Error reject 만 lock 하고 있어 에러 경계 스펙이 통째로 비어있다는
     동일 테스트 공백 (FIND-001 root_cause_chain[2], FIND-002
     root_cause_chain[2]) 을 공유.

  단 FIND-001 (P2) 과 FIND-002 (P3) 는 severity 와 재현 난이도가 다르므로,
  gatekeeper/solution 단계에서 P3 축 (FIND-002) 만 분리하거나 P2 축만 좁게
  진행하는 scope-down 결정이 정당할 수 있다. 본 CAND 는 epic 으로 두 FIND 의
  공통 boundary 를 기록하되, 분할 가능성을 명시한다.
proposed_title: "fix(infra): retryAsync 에러 종료 경계가 원본 fn() 실패를 호출자에게 전달하지 못한다"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-20
state: gatekeeper-approved
revision_note: '2026-05-20 cross-review scope_down — FIND-002 (falsy reject 치환) abandon, FIND-001 단독으로 epic→single 재편. type/finding_ids 갱신.'
cross_review_metric: metrics/cross-review-CAND-044-20260520-174033.jsonl
cross_review_decision: 'scope_down (5-agent: real-problem-real-fix 2 / real-problem-fix-insufficient 3). 결함 코드 실재 + upstream 중복 아님. in-repo caller 가 throwing 콜백/falsy reject 를 생성 안 함 → FIND-002 synthetic-only abandon. FIND-001 은 plugin-SDK RetryOptions 콜백 계약 hardening 으로 reframe 후 단독 진행. gatekeeper shadow verdict=uncertain 을 cross-review + 사용자 결정으로 approve(scoped).'
cross_refs:
  - CAND-009  # infra-retry 도메인, retryAsync retryAfterMs 하방 위반 (pr-merged, 다른 axis)
---

# fix(infra): retryAsync 에러 종료 경계가 원본 fn() 실패를 호출자에게 전달하지 못한다

## 공통 패턴

`retryAsync` (src/infra/retry.ts) 의 **에러 종료 경계** — 즉 attempt 소진 후
호출자에게 최종 에러를 던지는 경로 — 가 retry 가 추적하던 원본 작업 실패
`lastErr` 를 충실히 전달하지 못한다. 두 FIND 가 같은 함수의 이 단일 경계를 서로
다른 라인에서 드러낸다.

```text
retryAsync (options 경로, retry.ts:121-179)
  for attempt:
    try { return await fn() }            # L122-123  fn() 만 보호
    catch (err) {
      lastErr = err                      # L125      원본 실패 저장
      if (... || !shouldRetry(err))      # L126  ← 콜백 무방비 (FIND-001)
        break
      retryAfterMs?.(err)                # L130  ← 콜백 무방비 (FIND-001)
      ...
      onRetry?.(info)                    # L166  ← 콜백 무방비 (FIND-001)
    }
  throw lastErr ?? new Error("Retry failed")   # L179  ← falsy 치환 (FIND-002)
                                               # number 경로는 L105 동일
```

- **FIND-001 (P2)**: `catch (err)` 블록 안에서 호출되는 호출자 콜백
  `shouldRetry`/`retryAfterMs`/`onRetry` (L126,130,166) 가 try/`.catch()` 로
  감싸이지 않는다. try 는 `await fn()` (L122-123) 만 보호한다. 콜백이 throw
  하면 catch 가 이미 진입한 상태라 재포착되지 않고 retry loop 밖으로 누출되며,
  제어가 L179 의 `throw lastErr` 에 *도달하기 전에* 함수를 벗어난다. 호출자는
  원본 작업 실패 `lastErr` 대신 콜백이 던진 부수 에러를 받고, `fn()` 의 실제
  실패 원인은 swallow 된다.

- **FIND-002 (P3)**: 종료 지점 L105/L179 의 `throw lastErr ?? new
  Error("Retry failed")` 가 `fn()` 의 falsy reject 값 (`undefined`/`null`) 을
  `??` 의 fallback 조건으로 오인해 generic `Error("Retry failed")` 로 silent
  치환한다. 호출자는 작업 고유의 reject 값 대신 합성 Error 를 받아 `===` 동일성
  비교나 빈-reject sentinel 분기가 깨진다.

두 결함의 종착점은 동일하다: **호출자가 받는 에러가 retry 가 추적한 원본 `fn()`
실패 (`lastErr`) 가 아니다.** FIND-001 은 종료 throw 에 도달하기 전에 콜백
에러가 `lastErr` 를 가로채고, FIND-002 는 종료 throw 표현식 자체가 falsy
`lastErr` 를 generic Error 로 치환한다. 둘 다 `retryAsync` 의 단일 에러 종료
경계가 원본 실패 식별자를 손실시키는 결함이며, 두 FIND 의 `impact_hypothesis`
가 모두 `data-loss` (진단 정보 / 에러 식별자 손실) 로 동일하다.

### 근거 인용 (각 FIND root_cause_chain 에서 직접)

**FIND-infra-retry-error-boundary-001** (콜백 무방비 누출):
- `root_cause_chain[0].because`: "L122-123 의 try 는 `return await fn()` 만
  감싼다. shouldRetry/retryAfterMs/onRetry 호출 (L126,130,166) 은 모두
  `catch (err)` 블록 안에 있어 같은 try 의 보호를 받지 못한다."
- `root_cause_chain[1].because`: "원본 작업 실패는 `lastErr` 에 저장되고
  (L125) 정상 종료 시 L179 의 `throw lastErr` 로 전파된다. 콜백이 catch 블록
  안에서 throw 하면 제어가 L179 에 도달하기 전에 함수를 벗어나므로, `lastErr`
  는 throw 되지 못하고 콜백 에러가 그 자리를 대체한다."

**FIND-infra-retry-error-boundary-002** (falsy 값 silent 치환):
- `root_cause_chain[0].because`: "L105/L179 의 `lastErr ?? new Error(\"Retry
  failed\")` 에서 `??` 는 좌변이 `null`/`undefined` 이면 우변을 택한다.
  `fn()` 이 `throw undefined`/`reject(null)` 하면 `lastErr` 가 정확히 그 값이
  되어, retry 가 추적한 \"실제 마지막 실패값\" 이 fallback 조건과 충돌한다."
- `root_cause_chain[2].because`: "retry.test.ts 의 실패 케이스 (L116-122, L188-194)
  는 모두 `new Error(\"boom\")` 같은 truthy Error 객체로만 reject 한다.
  `throw undefined`/`reject(null)` 로 reject 하는 테스트가 0건이라 falsy throw
  값 경로가 한 번도 실행되지 않는다."

두 FIND 의 종료 라인 인용이 같다 (L179, number 경로 L105). FIND-001 은
`root_cause_chain[1]` 에서 L179 의 `throw lastErr` 를 명시적으로 지목하며 그
throw 에 *도달 못 함* 을 결함으로, FIND-002 는 그 throw *표현식 자체* 의
`?? ` 치환을 결함으로 진단 — 같은 boundary 의 두 면이다.

### Epic 으로 묶는 이유

1. **단일 경계**: 두 결함 모두 `retryAsync` 의 에러 종료 경계 (catch 블록의
   무방비 콜백 슬롯 + L105/L179 의 종료 throw) 라는 같은 함수의 같은 영역에서
   발생. 독립 발견이 아니라 한 함수의 에러 전파 계약이 두 곳에서 새는 것.
2. **단일 계약**: `retryAsync` 의 에러 전파 계약 = "attempt 소진 시 호출자에게
   `fn()` 의 원본 실패를 던진다". FIND-001 (콜백 누출) 과 FIND-002 (falsy 치환)
   둘 다 이 계약 위반. 해결 의미가 "retryAsync 가 원본 실패를 보존해 던지도록"
   하나로 수렴.
3. **단일 fix surface**: 양쪽 모두 retryAsync 본문 (retry.ts:90-179) 내부 —
   catch 블록의 콜백 호출부와 종료 throw 표현식 — 으로 fix 가 인접. one thing
   per PR 관점에서 "retryAsync 에러 종료 경계 정합성" 한 task.
4. **단일 회귀 테스트 축**: retry.test.ts 의 같은 describe 블록에 "콜백 throw
   시 원본 `lastErr` 가 호출자에게 도달" + "fn 이 falsy 값으로 reject 시 원본
   값이 호출자에게 도달" 두 케이스가 함께 들어가 retryAsync 의 에러 전파
   계약을 한 번에 검증 (stub 금지 — per-instance 콜백/`mockRejectedValue`).
5. **공통 테스트 공백**: 두 FIND 의 `root_cause_chain[2]` 가 모두 동일하게
   "retry.test.ts/retry-policy.test.ts 가 happy-path 콜백 + truthy Error
   reject 만 lock, 에러 경계 스펙 부재" 를 결함 유지 원인으로 지목. 테스트
   공백도 공유된다.

## 관련 FIND

- **FIND-infra-retry-error-boundary-001** (P2, src/infra/retry.ts:122-130,
  symptom_type=error-boundary-gap): `retryAsync` options 경로의
  `catch (err)` 블록 안에서 호출되는 호출자 콜백 `shouldRetry` (L126),
  `retryAfterMs` (L130), `onRetry` (L166) 가 try/`.catch()` 로 감싸이지 않음.
  콜백 throw 시 retry loop 밖으로 누출되고 원본 `fn()` 실패 `lastErr` 가
  L179 의 `throw lastErr` 에 도달하지 못해 swallow. channel-API retry runner
  경로에서 caller predicate throw 시 남은 attempt 포기 → transient 실패가
  hard failure 로 오보고. 누출 throw 가 호출자 try 밖이면 unhandledRejection
  격상 가능.
- **FIND-infra-retry-error-boundary-002** (P3, src/infra/retry.ts:105,
  symptom_type=error-boundary-gap): 종료 지점 L105 (number 경로) / L179
  (options 경로) 의 `throw lastErr ?? new Error("Retry failed")` 가 `fn()` 의
  falsy reject 값 (`undefined`/`null`) 을 `??` fallback 으로 오인해 generic
  Error 로 silent 치환. 호출자의 `===` sentinel 비교 / 빈-reject 분기가 깨지고
  로그 message 가 항상 "Retry failed" 로 고정. `0`/`""` 는 `??` 가 fallback
  하지 않으므로 영향은 undefined/null 한정.

## 영향

- 두 FIND 모두 `impact_hypothesis: data-loss` (FIND-001 진단 정보 손실 축,
  FIND-002 에러 식별자 손실 축).
- 공통 운영 영향: `retryAsync` 호출자가 받는 에러가 `fn()` 의 실제 실패 원인이
  아니게 되어 로그/관측이 잘못된 에러를 표시 → 장애 오진단.
- FIND-001 고유: caller predicate throw 시 retry 조기 종료로 transient 실패가
  hard failure 로 보고. 누출 throw 의 unhandledRejection 격상 가능.
- FIND-002 고유: 빈 reject 를 sentinel 로 쓰는 호출자의 분기 로직이 generic
  Error 흡수로 잘못된 경로 진입.

## proposed severity 근거

두 FIND 의 max severity = P2 (FIND-001) 를 epic severity 로 상속
(clusterer.md "severity 는 가장 높은 값 상속"). FIND-002 단독은 P3 (falsy
reject 의 in-repo caller 경로 미확인, 위생 수준). 단 FIND-001 도 즉시 재현은
아님 — 현 in-repo caller (retry-policy/media-fetch/compaction) 콜백이 비교적
방어적이라 P1 이 아닌 P2. `RetryOptions` 가 공개 옵션이고 콜백 무방비는
인터페이스 계약 수준 결함이라 P2 유지.

## 분할 가능성 (gatekeeper / solution 단계 입력)

본 CAND 는 epic 으로 두 FIND 의 공통 boundary 를 기록하나, 두 FIND 는
severity (P2 vs P3) 와 재현 난이도가 다르다. gatekeeper/solution 단계에서:
- P3 축 (FIND-002, falsy 치환) 만 분리하거나,
- P2 축 (FIND-001, 콜백 누출) 만 좁게 진행하는 scope-down 이 정당할 수 있다.
fix surface 자체는 retryAsync 본문 인접 영역으로 공유되므로 단일 XS-S PR 로
양쪽 통합도 가능. 구체 결정은 후속 단계로 이월 (clusterer 는 해결책 미기술).

## 수정 scope (solution 단계로 이월)

solution-drafter 가 구체 fix 를 결정. 본 CAND 는 문제/원인 공통성까지만 기술.
- fix surface 후보: retryAsync 본문 (retry.ts:90-179) 의 catch 블록 콜백
  호출부 (L126,130,166) + 종료 throw 표현식 (L105,179).
- 회귀 테스트: retry.test.ts 에 콜백 throw 보존 케이스 + falsy reject 보존
  케이스 추가 (per-instance 콜백/`mockRejectedValue`, 프로토타입 변경 금지).
- CODEOWNERS 검사: `src/infra/retry.ts` 는 보안 민감 경로 매치 안 함 — 일반
  ownership (단 gatekeeper 가 재확인).

## cross-cell 관찰

- **CAND-009** (infra-retry 도메인, PR #68543 merged): `retryAsync` 가
  `retryAfterMs` 에 대칭 jitter 를 적용해 서버 Retry-After 를 하방 위반하던
  결함. 같은 함수 `retryAsync` 이나 axis 가 다름 (jitter/타이밍 vs 에러 경계).
  cross_refs 로만 연결.
- backoff.ts / retry-policy.ts 는 error-boundary 축 FIND 0건 (도메인 노트
  error-boundary-auditor 2026-05-20 섹션 hint 4/5 참조: `sleepWithAbort` 이중
  settle 방어 unconditional, `computeBackoff` NaN 미방어는 caller 정책이 전부
  정적 안전이라 R-7 상 미발현). 본 epic 은 retry.ts 의 retryAsync 단일 함수에
  국한.

## Scope-down (cross-review 2026-05-20)

위 본문은 epic(FIND-001 + FIND-002) 기준 분석이다. 2026-05-20 post-harness
cross-review (5-agent, `metrics/cross-review-CAND-044-20260520-174033.jsonl`)
결과 **scope_down** 합의에 따라 본 CAND 는 **single** 로 재편됐다.

- **합의**: real-problem-real-fix 2 (positive-advocate, upstream-dup-checker) /
  real-problem-fix-insufficient 3 (critical-devil, hot-path-tracer,
  reproduction-realist). false-positive 0, upstream-duplicate 0.
- **공통 관측**: retry.ts 의 두 결함 코드는 실재하고 evidence 도 정확하며
  upstream 중복이 아니다. 그러나 in-repo caller (retry-policy channel runner,
  media/fetch, compaction) 의 콜백이 전부 방어적이고 어느 caller 의 `fn` 도
  falsy 값으로 reject 하지 않는다.
- **FIND-002 abandon**: falsy reject 치환은 in-repo trigger 0건.
  reproduction-realist 가 synthetic_risk=high, abandon-as-synthetic 권고.
  `findings/rejected/` 로 이동, FSM `rejected`.
- **FIND-001 유지 (단독)**: 콜백 무방비 누출은 P2 인터페이스 계약 gap.
  production hot-path 는 아니나 `retryAsync` 가 plugin-SDK 로 re-export 되어
  외부 plugin 이 비방어적 predicate 를 주입하면 발현. **plugin-SDK
  RetryOptions 콜백 계약 hardening** 으로 성격을 재정의해 단독 진행한다.
  재현 테스트는 "production hot-path 재현" 이 아니라 "공개 옵션 계약 회귀
  방지" 로 정직하게 표기할 것 (reproduction-realist 권고).
- 사용자 결정 (2026-05-20): scope-down 진행. gatekeeper shadow verdict
  `uncertain` 은 cross-review + 사용자 판단으로 approve(scoped) 처리.
