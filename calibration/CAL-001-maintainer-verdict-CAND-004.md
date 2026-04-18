# CAL-001: CAND-004 false positive 회고 (메인테이너 judgment)

**날짜**: 2026-04-18
**CAND**: CAND-004 (FIND-agents-registry-memory-002)
**Issue/PR**: openclaw#68487 / openclaw#68489 (closed)
**Shadow gatekeeper 판정**: approve@medium
**메인테이너 judgment**: reject_suspected (high confidence — "happy to revisit if concrete repro")
**불일치 유형**: shadow false-positive → human reject

## 메인테이너 코멘트 (요지)

> `schedulePendingLifecycleError()` already clears the pending entry in its 15-second timer callback, so keeping the sweeper alive here doesn't look necessary. Closing for now, but happy to revisit if you can share a concrete repro showing the entry getting stranded.

## 정확한 원인

`schedulePendingLifecycleError` (src/agents/subagent-registry.ts:242-276) 의 15초 grace timer:

```ts
const timer = setTimeout(() => {
  const pending = pendingLifecycleErrorByRunId.get(params.runId);
  if (!pending || pending.timer !== timer) return;
  pendingLifecycleErrorByRunId.delete(params.runId);  // line 249, unconditional
  ...
}, LIFECYCLE_ERROR_RETRY_GRACE_MS);
```

각 pending entry 는 **자기 자신의 15초 timer** 가 line 249 에서 무조건 `delete` 수행.
sweeper 의 `PENDING_ERROR_TTL_MS=5min` cleanup 은 **defense-in-depth** 일 뿐 primary cleanup path 가 아님.

### Production path

1. `schedulePendingLifecycleError(runId)` → entry 등록 + 15초 timer arm
2. 15초 후 → entry 자체 timer 가 line 249 에서 delete
3. 맵 비어짐 → sweeper 멈추든 말든 이미 정리됨

### 내 테스트 (synthetic)

1. `schedulePendingLifecycleErrorForTest(...)` → entry 등록
2. `runSweepForTest()` → 한 번 sweep (fake time=0)
3. `expect(isSweeperActiveForTest()).toBe(true)` — FAIL without fix

**함정**: fake timers 환경에서 15초 advance 를 안 했기 때문에 entry 의 자체 timer 가 안 돌았음. red-green 만 증명했지 **production path 동일 결과** 를 검증 안 함.

## 파이프라인 실패 지점

### 1. 페르소나 (memory-leak-hunter) R-3 수행

agent 가 `rg "pendingLifecycleErrorByRunId\.(delete|clear)"` 실행 → 4 경로 열거:
- L233 clearPendingLifecycleError
- L240 clear
- L250 timer callback delete
- L606 sweeper TTL

**놓친 것**: 각 delete 의 **실행 조건** 분류. L250 은 "fires unconditionally 15s after schedule" 인데 agent 가 그 맥락을 읽지 않고 "entry 부재 시 return" 을 오독해서 L250 을 조건부로 분류함.

### 2. Gatekeeper counter_evidence

페르소나가 넘긴 counter_evidence 가 "sweeper self-stop = leak" 가정을 승인. gatekeeper 탐색 카테고리는:
- 숨은 방어
- 호출 빈도
- 기존 테스트
- 설정
- 주변 맥락

**놓친 카테고리**: "이 leak 주장이 성립하려면 무엇이 실패해야 하는가?" (primary-path inversion). 즉 "15초 timer 가 안 돌아야만 leak" 이라는 전제를 명시적으로 찾아 확인 안 함.

### 3. 재현 테스트 검증

red-green 으로만 증명. "fake timer 에서 production flow 를 실제로 흘려봤을 때도 버그가 드러나는가" 검증 안 함.

## 하네스 보완 조치 (이 커밋 포함)

### A. 페르소나 R-5 추가 (memory-leak-hunter / cron-reliability-auditor / plugin-lifecycle-auditor / error-boundary-auditor)

**R-5: delete/cleanup 경로의 execution condition 분류**

| 경로 | 조건 | 예시 |
|---|---|---|
| unconditional | 항상 실행 | `setTimeout(() => map.delete(id), TTL)` 본체의 첫 무조건 delete |
| conditional-edge | edge case 에서만 | sweeper 의 5min TTL cleanup (정상 path 가 먼저) |
| test-only | 테스트 리셋 | `testReset` / `clearAllForTests` |
| shutdown | process exit | `process.on("SIGTERM", ...)` |

counter_evidence 에 경로별 분류 명시 의무.

### B. Gatekeeper 페르소나 새 카테고리

**"Primary-path inversion"**: 주장된 결함이 성립하려면 어떤 정상 경로가 실패해야 하는가? 그 실패 경로를 명시적으로 탐색.

예시 질문:
- "이 entry 가 leak 되려면 무슨 cleanup 이 실패해야 하는가?"
- "주장된 race 가 재현되려면 어떤 atomic guard 가 우회돼야 하는가?"
- "주장된 crash 가 성립하려면 어떤 error boundary 가 없어야 하는가?"

이 카테고리는 `explored_categories` 에 **필수** 포함.

### C. 재현 테스트 verification 체크리스트 (SOL 스키마)

SOL 의 `repro_test_draft` 에 대해 solution-drafter (또는 수동 작성자) 가 **"production-equivalent flow" 섹션** 을 추가:

- fake timer 사용 시: 해당 자료구조와 엮인 **모든 setInterval/setTimeout 을 advance 했을 때** 의 결과 예측 + 검증
- mock 사용 시: mock 이 replace 한 경로가 실제 production 에서도 같은 결과 내는지 수동 추적

## 메트릭 반영

| 파일 | 레코드 |
|---|---|
| `metrics/shadow-runs.jsonl` | `shadow-CAND-004-1776498519921` (approve@medium) |
| `metrics/human-verdicts.jsonl` | CAND-004 human: reject_suspected/high |

shadow vs human **첫 불일치** 기록. 졸업 조건 (human ≥ 10) 중 1 확보.

## 재실수 방지 체크리스트

다음 CAND 생성/평가 시:
- [ ] 페르소나가 cleanup 경로의 execution condition 분류를 counter_evidence 에 포함?
- [ ] Gatekeeper verdict 의 `explored_categories` 에 "primary-path inversion" 포함?
- [ ] SOL 재현 테스트에 production-equivalent flow 검증 섹션?
- [ ] 재현 테스트가 fake timer 전체 advance 했을 때도 여전히 fail 하는가?
