# infra-retry 도메인 노트

openclaw 의 `src/infra/retry*.ts` + `src/infra/backoff.ts` 서브시스템에 대한 영구 관찰 기록.
페르소나/세션별로 append-only 로 추가.

---

### cron-reliability-auditor (2026-04-18)

셀: `infra-retry-concurrency`. allowed_paths: `src/infra/retry*.ts`, `src/infra/backoff.ts`.

#### 대상 파일 현황

| 파일 | LOC | 책임 |
|---|---|---|
| `src/infra/retry.ts` | 137 | `retryAsync` 본체 (number 오버로드 + options 오버로드), `resolveRetryConfig`, `applyJitter` |
| `src/infra/retry-policy.ts` | 118 | `createRateLimitRetryRunner`, `createChannelApiRetryRunner`, `getChannelApiRetryAfterMs` |
| `src/infra/backoff.ts` | 59 | `computeBackoff`, `sleepWithAbort` |
| `src/infra/retry.test.ts` | 257 | `retryAsync` 단위 테스트 |
| `src/infra/backoff.test.ts` | 81 | `computeBackoff` / `sleepWithAbort` 단위 테스트 |

#### 적용 카테고리 (agents/cron-reliability-auditor.md §탐지 카테고리)

- [x] A. 분산 락 / 중복 실행 — skip
  - 사유: 이 셀은 retry 유틸리티로 멀티 인스턴스 claim 책임 없음. 호출자(gateway, agents)의 책임.
- [x] B. Promise.race 잔여 promise — skip (적용 불가)
  - 사유: `retryAsync` 는 `for (attempt)` + `await fn()` + `await sleep(delay)` 시퀀셜 루프.
    `Promise.race` 미사용. `sleepWithAbort`(backoff.ts:14-59) 은 race 유사 구조이나
    `{ once: true }` listener + `settled` 플래그 + dual aborted check(L38-41, L53-57)로
    loser 취소가 보장됨. backoff.test.ts:62-80 "listener-registration-race" 테스트가
    이를 lock.
- [x] C. Catch-up / startup ordering — skip (해당 없음)
- [x] D. Timer 수명·재무장 — 부분 적용
  - `sleepWithAbort`(backoff.ts:21-34) 의 onAbort 는 `settled` 플래그 + `clearTimeout` +
    `removeEventListener` 를 무조건 실행. timer leak 없음.
  - `retryAsync` 의 `await sleep(delay)`(retry.ts:131)는 clearTimeout 경로 없음 — abort
    무시되므로 cancel 불가 (FIND-001 참고).
- [x] E. Stuck state 복구 — 적용 → FIND-infra-retry-concurrency-001 (abort 미전파로 shutdown 지연)
- [x] F. 시간 영역 — skip

#### R-3 Grep 결과

1) `rg -n "Redisson|redlock|Redis.*lock|distributed.*lock|file.*lock" src/infra/retry*.ts src/infra/backoff.ts`
   - 매치 0건. retry 유틸은 호스트 프로세스 내부 단독 실행이며 락 없음. 해당 없음.

2) `rg -n "clearTimeout\(|AbortController|signal\.abort" src/infra/retry*.ts src/infra/backoff.ts`
   - `src/infra/backoff.ts:27` — `sleepWithAbort` 내부 `clearTimeout(timer)` (onAbort 경로).
   - `src/infra/backoff.ts:14` — `sleepWithAbort(ms, abortSignal?)` 시그니처.
   - `src/infra/retry.ts` — **매치 0건**. `retryAsync` 는 abort 개념 자체를 모른다.
   - `src/infra/retry-policy.ts` — **매치 0건**. retry runner 레이어도 signal 을 안 넘긴다.
   - 분류표 (R-5):

     | 경로 | 파일:라인 | 실행 조건 | 비고 |
     |---|---|---|---|
     | `sleepWithAbort.onAbort → clearTimeout` | backoff.ts:27 | conditional-edge | abort 가 와야만 실행. 정상 sleep 완주 경로는 L44-51 의 setTimeout 콜백이 timer=null 대입. |
     | `sleepWithAbort` setTimeout 완주 | backoff.ts:44-51 | unconditional (abort 없을 시) | resolve 후 removeEventListener + timer=null |
     | `retryAsync` 내부 sleep cancel | N/A | **부재** | retry.ts 전체에 AbortSignal 전혀 없음 → FIND-001 |

3) `rg -n "heartbeat|liveness|stuck.*recover" src/infra/retry*.ts src/infra/backoff.ts`
   - 매치 0건. retry 는 단일 호출 수명 범위라 해당 없음.

4) 추가 검증 — `generateSecureFraction` vs `Math.random` 사용 비일관성:
   - `rg -n "generateSecureFraction|Math\.random" src/infra/retry*.ts src/infra/backoff.ts`
   - `retry.ts:3,65` — `generateSecureFraction` (CSPRNG) 사용.
   - `backoff.ts:10` — `Math.random()` (non-CSPRNG, Xorshift/PRNG) 사용.
   - 동일 서브시스템 내 jitter 소스가 이중화됨. (FIND-002 참고)

5) 추가 검증 — `computeBackoff` 의 jitter 수식 대칭성:
   - `backoff.ts:10` — `const jitter = base * policy.jitter * Math.random();` → `[0, +base*jitter]` (단방향 상향).
   - `retry.ts:65` — `const offset = (generateSecureFraction() * 2 - 1) * jitter;` → `[-jitter, +jitter]` (대칭 양방향).
   - 동시성 스파이크 분산 관점에서 단방향 jitter 는 thundering-herd 완화 효과 반감.
     (FIND-002)

6) 추가 검증 — retryAfter 적용 시 jitter 오염:
   - `retry.ts:115-121` — `baseDelay = Math.max(retryAfterMs, minDelayMs)` → `Math.min(base, maxDelayMs)` → `applyJitter(...)` → `Math.min(Math.max(delay, minDelayMs), maxDelayMs)`.
   - jitter 가 음(-) 방향일 경우 `retryAfterMs` 로 서버가 명시한 시간보다 먼저 재요청 가능.
   - 예: `retryAfterMs=1000`, `minDelayMs=0`, `jitter=0.1` → `delay ∈ [900, 1100]`. 서버 의도는
     "1000ms 후 이후" 이나 최소 900ms 에도 전송 → 재차 429 반송 유발.
   - `CHANNEL_API_RETRY_DEFAULTS.jitter = 0.1` (retry-policy.ts:11) → 실제 default 경로에서 10% 위반 가능.
   - (FIND-003)

#### 주요 관찰

- `retryAsync` 는 **AbortSignal 개념 자체가 없다**. 호출자(gateway/restart loop 등)가
  외부 abort 를 trigger 하더라도, `retryAsync` 루프 내부의 `sleep(delay)`(retry.ts:131)
  는 무조건 완주 → shutdown latency + 원하지 않는 최종 fn() 시도. 셀 스코프 내 가장
  심각한 gap. (FIND-001)
- Backoff jitter 의 PRNG 선택과 대칭성이 `retry.ts` 와 `backoff.ts` 사이 불일치.
  동일 의도(jitter) 를 두 곳에서 서로 다르게 구현. `computeBackoff` 쪽은 단방향 +
  `Math.random()` 으로 동시 재시작 다수 인스턴스의 분산이 약함.
  `CHANNEL_RESTART_POLICY`(server-channels.ts:22-27, jitter=0.1, maxMs=5min) 같은 실제
  production 정책에서 jitter 유효범위가 [+0, +10%] 로 편향. (FIND-002)
- `retryAfterMs` 가 설정되어 있을 때 jitter 적용이 서버 지시를 하방 위반. 429 응답의
  Retry-After 값을 충실히 지키려면 jitter 를 상방 전용으로 적용하거나 retry-after 경로에서
  jitter bypass 해야 함. (FIND-003)

#### 주변 callers 요약

- `retryAsync`:
  - `src/infra/retry-policy.ts:67,104` — rate-limit / channel-API 래퍼.
  - `src/memory-host-sdk/host/batch-http.ts:13` — HTTP 배치.
  - `src/agents/compaction.ts:317` — summarize retry.
- `sleepWithAbort`:
  - `src/cron/isolated-agent/delivery-dispatch.ts:382` — delivery 재시도 대기.
  - `src/agents/pi-embedded-runner/run.ts:547` — overload failover backoff.
  - `src/agents/pi-embedded-runner/context-engine-maintenance.ts:394` — shutdown-aware 대기.
  - `src/gateway/server-channels.ts:470` — 채널 자동 재시작.
  - `src/infra/transport-ready.ts:54` — transport 준비 폴링.
- `computeBackoff`:
  - `src/gateway/server-channels.ts:460` (`CHANNEL_RESTART_POLICY`, jitter=0.1, maxMs=300000).
  - `src/agents/context.ts:188`.

이들 callers 의 대부분은 `sleepWithAbort` 를 쓰므로 개별 콜사이트는 abort 전파 OK — 그러나
`retryAsync` 를 거치는 경로 (batch-http, compaction, retry-policy runners) 는 abort 미지원이
전체 체인으로 전파됨. (FIND-001 의 영향)

#### 확인 못 한 영역 (self-critique)

- `memory-host-sdk/host/batch-http.ts:13` 호출자 체인에서 외부 abort 가 어떻게 진입하는지
  (HTTP client 가 자체 timeout/abort 를 가지는지) 는 allowed_paths 밖이라 미확인. FIND-001 의
  현실적 impact 정량화에 제약.
- `compaction.ts:317` 경로가 프로세스 종료 시 `retryAsync` 의 pending sleep 때문에 얼마나
  지연되는지 프로파일 측정 없음. impact_detail 에 "정성: shutdown 지연 + 최대 30s(기본
  maxDelayMs) * maxAttempts 까지 대기" 로만 기술.
- `computeBackoff` 가 `retry.ts` 의 delay 계산을 대체하려는 의도였는지 혹은 독립 설계였는지
  commit history 추적 미수행. 불일치가 의도적인지 우발적인지 판단 불가 → FIND-002 를
  P3 위생 수준으로 분류.
- 429 응답의 실제 Retry-After 헤더 값이 production 에서 얼마나 자주 오는지, jitter 10% 가
  실제로 재차 429 를 유발하는지는 실측 데이터 없음. FIND-003 severity 는 P2 로 절제.

### clusterer (2026-04-18, Phase 2)

- **CAND-008 (single)**: FIND-infra-retry-concurrency-002. `computeBackoff`
  단방향 + `Math.random()` jitter (P3).
- **CAND-009 (single)**: FIND-infra-retry-concurrency-003. `retryAsync` 가
  `retryAfterMs` 에도 대칭 jitter 적용해 서버 Retry-After 하방 위반 (P2).
- **Epic 불가 판정 근거**: 두 FIND 모두 "jitter 인프라" 축이지만 root cause
  방향이 반대이다.
  - FIND-002 root_cause_chain[0]: `base * policy.jitter * Math.random()` 이
    단방향이라 `[0, +)` → thundering-herd 분산 반감. **대칭화가 fix 방향**.
  - FIND-003 root_cause_chain[2]: `applyJitter` 가 대칭 `[-jitter, +jitter]`
    이라 음의 offset 이 retryAfterMs 를 하방 위반. **대칭 bypass 가 fix 방향**.
  - 한쪽을 대칭화하면 다른 쪽 위반이 심화되는 방향성. 공유 fix 불가 → 각각
    single CAND. 공유 헬퍼 도입 같은 상위 리팩터는 본 clusterer 범위 밖
    (해결책 제안 금지).
- **Cross-cell 관찰**: FIND-infra-process-memory-001 과는 도메인·파일·root
  cause 모두 상이 (process listener vs retry delay). 완전 독립.
