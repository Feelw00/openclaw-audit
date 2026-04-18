# CAL-003: Cross-review → PR #68511 (CAND-006) 선제 retract

**날짜**: 2026-04-18
**CAND**: CAND-006
**PR**: openclaw#68511 (self-closed, not merged)
**트리거**: 사용자 요청 — "각 PR 에 3개의 에이전트(긍정/비판/중립)로 실제 문제가 발생하는지 확인"
**결과**: 3개 시점 중 **긍정 시점마저 `synthetic-only` 판정**

## Cross-review 판정 매트릭스

| PR | Positive | Critical | Neutral | 합의 |
|---|---|---|---|---|
| #68511 (CAND-006, restart auth leak) | synthetic-only | real-real | real-fix-insufficient | **reject 쪽** |
| #68531 (CAND-005, plugin register rollback) | real-real | real-real | real-real | ✅ merge |
| #68543 (CAND-009, retry-after lower bound) | real-real | real-real | real-real | ✅ merge |

## PR #68511 의 정확한 오류

### 재현 테스트의 전제

```ts
// src/infra/restart.emit-authorization-leak.test.ts (내가 쓴 것)
const killSpy = vi.spyOn(process, "kill").mockImplementation(() => {
  throw new Error("simulated kill failure");
});
```

`process.kill` 을 throw 시킴으로써 catch 블록 진입 유도.

### 프로덕션 경로

```ts
// src/infra/restart.ts:128-132
if (process.listenerCount("SIGUSR1") > 0) {
  process.emit("SIGUSR1");
} else {
  process.kill(process.pid, "SIGUSR1");  // ← 테스트가 타겟한 경로
}
```

```ts
// src/cli/gateway-cli/run-loop.ts:244 (프로덕션)
process.on("SIGUSR1", onSigusr1);
```

Gateway 는 **시작 시 SIGUSR1 리스너를 등록** 한다. `process.listenerCount("SIGUSR1") > 0` 이 항상 true → `process.emit` 경로만 탐. `process.kill` throw 경로에는 프로덕션에서 도달하지 않음.

### 게다가 `process.emit` 은 동기 핸들러 디스패치

```ts
// run-loop.ts:221 onSigusr1 핸들러 본체
const authorized = consumeGatewaySigusr1RestartAuthorization();
```

`process.emit("SIGUSR1")` 호출 시 동기적으로 `onSigusr1` 실행 → 첫 줄에서 authorization count 소비. try 블록 나갈 때는 이미 count=0. 그 사이 stray SIGUSR1 이 authorization 를 훔칠 window 도 없음.

## CAL-001 과의 대비

| 항목 | CAL-001 (CAND-004) | CAL-003 (CAND-006) |
|---|---|---|
| 트리거 | 메인테이너 리뷰 후 close | cross-review agent 검증 후 self-close |
| 시점 | 메인테이너 시간 낭비함 | 메인테이너 리뷰 전 retract |
| 실수 | schedulePendingLifecycleError 의 15초 timer (unconditional delete) 를 gatekeeper 가 놓침 | 프로덕션 hot-path (process.emit) 와 test path (process.kill) 불일치를 gatekeeper 가 놓침 |
| R-5 여부 | R-5 없었음 | R-5 있었으나 "hot-path 일치 검증" 차원 부재 |

## 파이프라인 보완 — 새 규율 R-7

### R-7. 재현 테스트는 프로덕션 hot-path 와 동일 분기여야 함

FIND / SOL 에 제안된 재현 테스트가 production code path 와 **다른 branch** 를 타고 있으면 false positive 의심.

구체적 점검:
1. 함수 내부에 branch 가 여러 개 있다면, **프로덕션에서 실제로 taken 되는 branch** 가 어느 것인지 확인
2. 그 branch 에서 bug 이 재현되는지 직접 exercise
3. 테스트가 mock 으로 다른 branch 를 강제한다면, 그 branch 가 **프로덕션에서 실재하는** 경로인지 caller 추적
4. production caller 가 0건이거나 edge case 인 경우 FIND 를 `P3` 이하 또는 abandon

### Cross-review 프로세스 공식화 (NEXT.md 반영)

PR 발행 **직전** 3 에이전트 체크 의무화:
- Positive: "왜 머지해야 하는가" 증거
- Critical: "왜 close 해야 하는가" 반증
- Neutral: 균형

합의 2/3 미만이면 retract 또는 scope 축소.

## Gatekeeper 보강

`gatekeep.py` `explored_categories` 에 "primary-path inversion" 필수 (CAL-001).
**추가**: "hot-path-vs-test-path consistency" 도 필수 카테고리로.

## 메트릭 반영

- shadow-runs.jsonl: CAND-006 approve@medium 기록은 그대로 (noise 데이터)
- human-verdicts.jsonl: 신규 +1 (CAND-006 reject_suspected@high, reason: cross-review synthetic-only)

shadow vs human 불일치 2건째 (CAL-001 에 이어).

## 교훈

- 긍정 시점 agent 가 synthetic-only 로 판정하면 **확실히 false positive**. 긍정 시점은 옹호 역할인데도 반박 증거를 찾은 것.
- Cross-review 는 메인테이너 시간 보호 + 파이프라인 calibration 양쪽에 유효.
- R-5 만으로는 부족 — "대응 cleanup 경로 classification" 만 하고 "production caller 가 이 branch 를 타는가" 는 별도 검증 필요.

## 재실수 방지 체크리스트 (다음 세션부터)

- [ ] 재현 테스트가 production code 의 hot-path branch 와 일치하는가?
- [ ] Mock 이 강제하는 상태가 실제 프로덕션에서 발생 가능한가?
- [ ] PR 발행 전 3 에이전트 cross-review (긍정/비판/중립) 합의 ≥ 2?
- [ ] 긍정 시점이 real 판정 못 하면 retract?
