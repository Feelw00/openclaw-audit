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

---

### memory-leak-hunter (2026-05-14, Phase 6 batch 2)

셀: `infra-retry-memory`. allowed_paths: `src/infra/retry*.ts`, `src/infra/backoff.ts`.
도메인: infra-retry. upstream HEAD: `af3d9333aa`.

#### 결론: memory-axis FIND 0건 (no leak found)

본 셀의 retry/backoff 코드는 **per-key 누적 상태도, 모듈 레벨 cache 도, 장기 timer
도 보유하지 않는 함수형 stateless 유틸리티**다. 메모리 leak 패턴 5 카테고리
(A. 무제한 자료구조 / B. EventEmitter 누수 / C. 강한 참조 체인 / D. 핸들 누수 /
E. 캐시 TTL 부재) 모두 부재 확인.

#### R-3 Grep 결과 — 모두 0 매치 또는 안전한 패턴만

```
rg -n "Map<|new Map\(|new Set\(|new WeakMap" src/infra/retry*.ts src/infra/backoff.ts
  → 0 matches

rg -n "setInterval|setTimeout" same
  → src/infra/backoff.ts:20,44 (sleepWithAbort timer 단일)

rg -n "clearTimeout|clearInterval" same
  → src/infra/backoff.ts:27 (onAbort 경로)

rg -n "TTL|expir|prun|sweep|evict" same
  → 0 matches

rg -n "delete |\.clear\(\)|\.shift\(|\.pop\(" same
  → 0 matches

rg -n "EventEmitter|\.on\(|\.addEventListener\(" same
  → src/infra/backoff.ts:37 ("abort", onAbort, { once: true })

rg -n "removeListener|\.off\(|removeEventListener" same
  → src/infra/backoff.ts:31, 47 (양 분기 모두 cleanup)

rg -n "\.push\(|\.set\(|\.add\(" same
  → 0 matches
```

#### R-5 cleanup execution condition 분류표

| 자료구조/리스너 | 위치 | cleanup 경로 | 실행 조건 |
|---|---|---|---|
| `setTimeout` timer | backoff.ts:44 | `clearTimeout(timer); timer=null` (L27-28) | abort 시: `conditional-edge`. 정상 완주 콜백 자체 (L45-50) 도 `timer=null` 대입으로 `unconditional` 종료. |
| `addEventListener("abort", onAbort)` | backoff.ts:37 | `{once: true}` + L31 onAbort 경로 + L47 timer-callback 경로 | abort/정상 둘 다 `unconditional` (양 분기 모두 명시 removeEventListener). |
| `lastErr` 참조 | retry.ts:92, 119, 125 | `lastErr = err` (다음 attempt 가 덮어씀) | `unconditional` — 매 attempt 마다 단일 ref 유지, 누적 없음. |
| `retryConfig` closure | retry-policy.ts:62, 97 | runner instance lifetime | `unconditional` — runner 객체 GC 시 같이 해제. caller (gateway, agents) 가 runner 폐기 시. |

**규율 R-3 적용**: 모든 자료구조/리스너에 `unconditional` cleanup 경로가 존재 → FIND 생성 금지.

#### 카테고리별 적용/skip 보고

- [x] applied — 무제한 자료구조 (A): 0건 (Map/Set/Array 자체 부재).
- [x] applied — EventEmitter 누수 (B): backoff.ts:37 의 abort listener 만, `{once: true}` + 양 분기 명시 remove. 누수 없음.
- [x] applied — 강한 참조 체인 (C): `retryAsync` 의 `lastErr` 는 매 attempt 덮어쓰기 단일 ref. `onRetry` callback 에 err 노출은 호출자 책임. retry 본체에 retain 없음.
- [x] applied — 핸들 누수 (D): fs/http/db 핸들 미사용 (순수 timing 유틸).
- [x] applied — 캐시 TTL 부재 (E): 캐시 자체 부재.
- [x] applied — Timer 누수 (B 확장): `sleepWithAbort` 의 `setTimeout` 은 정상 완주/abort 양 분기 모두 timer=null + clearTimeout. retry.ts:174 의 `await sleep(delay)` 는 utils.sleep (셀 밖) 호출 후 return, retry.ts 측에서 retain 없음.

#### CAL-008 — 6주 upstream fix 검사

`git log --oneline --since="6 weeks ago"` (retry.ts, retry-policy.ts, backoff.ts):

```
b7fc2451e7 fix(infra): positive jitter 는 Math.ceil 로 ...
a77bd213ce fix(infra): Retry-After 경계(===)에서 contract 우선 ...
317e474b84 fix(infra): retryAfterMs === maxDelayMs 경계 ...
62dff64700 fix(infra): retryAfterMs > maxDelayMs symmetric ...
d49480527e fix(infra): retry-after 를 jitter 가 침범하지 않도록 ...
c15b295a85 (context-engine 이외)
1a08d23e09 refactor: dedupe finite number coercion
e4b5027c5e refactor(plugins): move extension seams ...
eecb36eff4 fix(ci): stabilize zero-delay retry ...
```

모두 jitter / Retry-After contract 경계 조정 (Phase 2 의 FIND-003 후속 fix
연쇄). 메모리 영역 신규 결함 도입 commit 없음.

#### 반증 — borderline P3 후보까지 점검했으나 없음

- `retryAsync` 의 `options.onRetry?.({...err...})` (retry.ts:166-172) 는 호출자가
  err 를 외부 array 에 push 하면 호출자 측 leak 가능. 그러나 retry 본체는 retain X
  → 본 셀 도메인 범위 밖.
- `createRateLimitRetryRunner` (retry-policy.ts:62-82) 가 closure 로 params 캡처.
  per-call 이 아니라 셋업 1회당 1 closure. caller 가 runner 를 모듈 레벨 single-
  ton 으로 보유해도 closure 크기 자체가 O(1) constant. 누수 패턴 아님.
- `applyJitter` 의 `generateSecureFraction()` 호출 (retry.ts:73) 은 매 retry 마다
  CSPRNG syscall — CPU 비용은 있으나 메모리 retain 없음.

#### 확인 안 한 영역 (self-critique)

- `utils.js` 의 `sleep` 구현 자체는 allowed_paths 밖이라 미점검. 호출 후 retry.ts
  쪽에서 retain X 가 본 셀 결론이며, sleep 내부 leak 가 있다면 다른 셀의 도메인.
- `secure-random.ts` 의 `generateSecureFraction` 동일.
- production 에서 retry runner 가 long-lived singleton 으로 유지되는 caller 수
  량 / err 객체의 외부 retention 여부는 caller 측 셀 (gateway-memory,
  agents-memory 등) 의 책임.

#### 산출

- FIND-infra-retry-memory-NNN: **0건**. 사유: 메모리 leak 패턴 5 카테고리 모두
  부재 + 모든 timer/listener 에 unconditional cleanup. borderline P3 후보도 closure
  retention 분석에서 caller 책임으로 분리됨. R-3 규율상 unconditional cleanup 경로
  발견 시 FIND 생성 금지.

