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

---

### error-boundary-auditor (2026-05-20)

셀: `infra-retry-error-boundary`. allowed_paths: `src/infra/retry*.ts`,
`src/infra/backoff.ts`. 도메인: infra-retry. upstream HEAD: `0c67dc7f82`
(2026-05-20). 축: 재시도/백오프 코드의 error boundary (catch 누락 / 에러 swallow /
콜백 throw 누출).

#### 산출: FIND 2건

- **FIND-infra-retry-error-boundary-001 (P2)** — `retryAsync` options 경로의
  `shouldRetry`/`retryAfterMs`/`onRetry` 콜백 (retry.ts:126,130,166) 이 `catch
  (err)` 블록 안에서 무방비 호출. try 는 `await fn()` (L122-123) 만 보호.
  콜백 throw 시 retry loop 밖으로 누출 + 원본 `lastErr` swallow.
- **FIND-infra-retry-error-boundary-002 (P3)** — `throw lastErr ?? new
  Error("Retry failed")` (retry.ts:105, 179) 가 `fn` 의 falsy reject 값
  (undefined/null) 을 generic Error 로 silent 치환. 원본 실패 식별자 손실.

#### hint 별 검증 결과

grid.yaml 이 제시한 5개 hint 를 코드와 대조:

1. **retry.ts shouldRetry/onRetry 콜백 throw → catch 부재 누출** — 성립 →
   FIND-001. shouldRetry(L126)/retryAfterMs(L130)/onRetry(L166) 셋 다 무방비.
2. **최종 attempt 실패 + abort 경합 시 error 전파** — FIND 아님. `retryAsync`
   는 options/number 양 경로 모두 AbortSignal 개념이 *없다* (`rg -n
   "AbortController|AbortSignal|signal\.abort|\.aborted" src/infra/retry.ts`
   → 0 매치). abort 경합 자체가 retry.ts 내부에서 성립 불가. abort 미전파는
   별개 축 (FIND-infra-retry-concurrency-001 이 이미 다룸).
3. **retry-policy.ts 정책 평가 throw → 호출자 도달 형태** — 독립 FIND 아님,
   FIND-001 에 흡수. `resolveChannelApiShouldRetry` (retry-policy.ts:18-30) 와
   `getChannelApiRetryAfterMs` (L32-52) 가 retry.ts 콜백 슬롯으로 주입됨.
   `getChannelApiRetryAfterMs` 자체는 모든 객체 접근에 typeof 가드가 촘촘해
   throw 안 함. 그러나 retry-policy 가 만든 predicate 가 retry.ts 의 무방비
   콜백 슬롯에 들어간다는 것이 FIND-001 의 메커니즘 일부 → 별도 FIND 불필요.
4. **backoff.ts delay 계산 NaN/음수/Infinity** — FIND 아님 (R-7 적용).
   `computeBackoff` (backoff.ts:8-12) 는 `policy.initialMs/factor/maxMs/jitter`
   에 유효성 검사가 전혀 없어 NaN policy 면 `Math.round(base+jitter)=NaN` 반환,
   이게 `sleepWithAbort` 의 `ms` 로 가면 `ms<=0` 이 NaN 에서 false → `setTimeout(fn,
   NaN)` 즉시 fire. 그러나 `computeBackoff` 의 production caller 3곳 (`rg -n
   "computeBackoff" src` → `server-channels.ts:591` CHANNEL_RESTART_POLICY,
   `context.ts:212` CONFIG_LOAD_RETRY_POLICY, `logs-cli.ts:343`
   FOLLOW_BACKOFF_POLICY) 의 정책이 **전부 정적 상수 리터럴**이며 모두 유한
   양수 (initialMs/maxMs/factor/jitter 직접 확인). `attempt` 인자도 정수
   카운터. 동적/외부 입력 정책을 만드는 caller 가 없어 production hot-path 에서
   NaN/Infinity 가 `computeBackoff` 에 도달하는 branch 가 존재하지 않음 (R-7).
   순수 이론적 결함이라 FIND 미생성. `retry.ts` 의 `resolveRetryConfig` 는
   `asFiniteNumber`/`clampNumber` 로 철저히 방어하는 것과 대조적이나, backoff.ts
   의 미방어가 현재 결함으로 발현되지 않음.
5. **sleepWithAbort 이중 settle / unhandled rejection** — FIND 아님. `settled`
   플래그 (backoff.ts:19) + `addEventListener("abort", onAbort, { once: true })`
   (L37) + onAbort/timer-callback 양 분기 모두 명시 cleanup (L22-23 settled
   체크, L26-29 clearTimeout, L31/47 removeEventListener). 이중 settle 방어가
   unconditional 하게 존재 → R-5 상 FIND 금지. backoff.test.ts:71-89
   "listener-registration-race" 가 이를 lock.

#### R-3 Grep 결과

```
rg -n "try\s*\{|catch\s*\(|finally" src/infra/retry.ts src/infra/retry-policy.ts src/infra/backoff.ts
  → retry.ts:94 try / :96 catch (number 경로, fn() 만 보호)
  → retry.ts:122 try / :124 catch (options 경로, fn() 만 보호)
  → retry-policy.ts / backoff.ts: 0 매치 (try/catch 자체 없음)

rg -n "AbortController|AbortSignal|signal\.abort|\.aborted" 위 3파일
  → backoff.ts:14,38,54 (sleepWithAbort 만). retry.ts/retry-policy.ts: 0 매치

rg -n "Number.isFinite|asFiniteNumber|Infinity|NaN" 위 3파일
  → retry.ts:1,35,114,131 (resolveRetryConfig/retryAfterMs 방어)
  → retry-policy.ts:51 (getChannelApiRetryAfterMs 의 retry_after 가드)
  → backoff.ts: 0 매치 (computeBackoff NaN 미방어 - hint 4 참조)
```

#### R-5 실행 조건 분류표 (콜백 경로)

| 경로 | 파일:라인 | 실행 조건 | 비고 |
|---|---|---|---|
| `try { return await fn() }` | retry.ts:122-123 | unconditional | fn() 만 보호 |
| `shouldRetry(err, attempt)` | retry.ts:126 | conditional-edge (catch 진입 시) | 무방비 → FIND-001 |
| `retryAfterMs?.(err)` | retry.ts:130 | conditional-edge | 무방비 → FIND-001 |
| `onRetry?.(info)` | retry.ts:166 | conditional-edge | 무방비 → FIND-001 |
| 콜백 throw 포착 try/catch | N/A | **부재** | FIND-001 근거 |
| `sleepWithAbort` 이중 settle 방어 | backoff.ts:19,22-23,37 | unconditional | FIND 금지 |
| `computeBackoff` 입력 유효성 검사 | N/A | **부재** | 단 caller 정책 전부 정적 안전 → FIND 아님 (R-7) |

#### 카테고리별 적용/skip (페르소나 §탐지 카테고리 A~E)

- [x] A. unhandledRejection / handler chain — skip. retry/backoff 파일에
  `process.on` 핸들러 없음 (allowed_paths 밖). 단 콜백 누출 throw 가 호출자
  try 밖이면 unhandledRejection 으로 격상 가능 (FIND-001 영향에 기술).
- [x] B. Floating promise — skip. `retryAsync` 는 `for + await` 시퀀셜,
  `sleepWithAbort` 는 단일 await Promise. fire-and-forget 패턴 없음.
- [x] C. JSON.parse 미보호 — skip. 세 파일에 JSON.parse 없음.
- [x] D. AbortController 전파 — 부분 적용. `sleepWithAbort` 는 abort 를
  올바르게 처리 (이중 settle 방어 unconditional). `retryAsync` 는 abort 개념
  부재 (별개 축 FIND-infra-retry-concurrency-001).
- [x] E. fs/network 동기 호출 — skip. 세 파일 모두 순수 timing/제어 유틸,
  동기 IO 없음.
- [x] 추가 (콜백 무방비) — 적용 → FIND-001.
- [x] 추가 (falsy throw 값 silent 치환) — 적용 → FIND-002.

#### CAL-007 — upstream fix 검사

`git log --oneline -15 -- src/infra/retry.ts`:
- 최근 retry.ts 변경 (b7fc2451e7, a77bd213ce, 317e474b84, 62dff64700,
  d49480527e) 은 전부 jitter / Retry-After contract 수치 축 — error-boundary
  축 (콜백 throw 처리 / lastErr 치환) 을 손댄 commit 없음.
- L122-123 의 try 범위 (`return await fn()` 만 보호) 는 `e5f677803f1`
  (2025-11-26) 이후 미변경. L105/L179 의 `?? new Error("Retry failed")` 도
  동일 시기 도입 후 미변경 (git blame 확인).
- 즉 FIND-001/002 의 의심 지점은 upstream 에서 fix 되지 않은 stale 아닌 현행
  결함.

#### 확인 못 한 영역 (self-critique)

- `src/infra/errors.ts` 의 `formatErrorMessage` 가 순환참조/getter throw 등에서
  실제 throw 가능한지 (allowed_paths 밖). FIND-001 의 shouldRetry throw 시나리오
  중 retry-policy predicate 경로의 현실성은 이 함수 거동에 의존.
- retryAsync caller (`media/fetch.ts`, `agents/compaction.ts`,
  `plugin-sdk/*`) 의 `fn` 내부가 falsy 값으로 reject 하는 경로가 있는지
  (allowed_paths 밖) — FIND-002 의 production 재현 정량화 제약, severity P3 절제.
- `computeBackoff` 의 hint 4 결함은 caller 정책이 모두 정적 안전이라 미발현이나,
  미래에 동적 정책 caller 가 추가되면 재평가 필요. 본 감사 시점 기준 FIND 아님.

---

### clusterer (2026-05-20)

error-boundary-auditor (2026-05-20) 가 산출한 FIND 2건을 클러스터링.

- **CAND-044 (epic)**: FIND-infra-retry-error-boundary-001 (P2) +
  FIND-infra-retry-error-boundary-002 (P3) 를 하나의 epic 으로 묶음.

#### 1개 epic vs 2개 single 판정 근거

두 FIND 는 같은 파일 (`src/infra/retry.ts`) + 같은 함수 (`retryAsync`) +
같은 `symptom_type` (`error-boundary-gap`) — clusterer.md Step 2 (동일 파일
내 같은 함수 + 같은 symptom_type → merge/epic) 대상이다.

- Step 1 (정확 중복) 아님: line_range 가 겹치지 않는다. FIND-001 은 콜백
  호출부 (retry.ts:122-130), FIND-002 는 종료 throw (retry.ts:105). 메커니즘도
  다르다 (콜백 무방비 누출 vs `??` falsy 치환).
- 그러나 단순 2 single 로 가르지 않은 이유 — 두 FIND 의 root_cause_chain 이
  같은 종료 라인을 지목한다:
  - FIND-001 `root_cause_chain[1]` 이 L179 의 `throw lastErr` 를 명시 인용
    하며 "콜백 throw 가 그 throw 에 도달하기 전 함수를 벗어나 `lastErr` 를
    대체" 를 결함으로 진단.
  - FIND-002 `root_cause_chain[0]` 이 L105/L179 의 `throw lastErr ?? new
    Error(...)` 표현식 자체를 결함으로 진단.
  - 즉 두 FIND 는 `retryAsync` 의 **단일 에러 종료 경계** (catch 블록의
    무방비 콜백 슬롯 + L105/L179 종료 throw) 가 서로 다른 방향에서 원본 실패
    `lastErr` 를 손실시키는 동일 boundary 의 두 면이다.
  - 두 FIND 모두 `impact_hypothesis: data-loss` (진단 정보 / 에러 식별자
    손실) 로 동일.
  - 두 FIND 의 `root_cause_chain[2]` 가 모두 "retry.test.ts/retry-policy.test.ts
    가 happy-path 콜백 + truthy Error reject 만 lock, 에러 경계 스펙 부재" 라는
    동일 테스트 공백을 결함 유지 원인으로 지목.
- fix surface 가 retryAsync 본문 (retry.ts:90-179) 의 catch 블록 콜백 호출부
  + 종료 throw 라는 인접 영역으로 수렴 + 회귀 테스트가 retry.test.ts 같은
  describe 에서 "콜백 throw 시 원본 보존" / "falsy reject 시 원본 보존" 한
  쌍으로 검증 가능 → one thing per PR 관점에서 단일 task. **epic 정당**.

#### 분할 가능성 메모

FIND-001 (P2) 과 FIND-002 (P3) 는 severity / 재현 난이도가 다르다. epic
severity 는 max = P2 상속 (clusterer.md 규약). gatekeeper/solution 단계에서
P3 축 (FIND-002 falsy 치환) 분리 또는 P2 축 단독 진행하는 scope-down 이
정당할 수 있음을 CAND-044 본문에 명시함. clusterer 는 해결책 미기술.

#### cross-cell 관찰

- CAND-009 (FIND-infra-retry-concurrency-003, PR #68543 merged) 와 같은 함수
  `retryAsync` 이나 axis 가 직교 (jitter/Retry-After 타이밍 vs 에러 경계).
  CAND-044 의 cross_refs 로만 연결.
- backoff.ts / retry-policy.ts 는 error-boundary 축 FIND 0건 (위
  error-boundary-auditor 섹션 hint 4/5 참조). 본 epic 은 retry.ts 의
  `retryAsync` 단일 함수에 국한.

---

### plugin-lifecycle-auditor (2026-05-20)

셀: `infra-retry-lifecycle`. allowed_paths: `src/infra/retry*.ts`,
`src/infra/backoff.ts`. 도메인: infra-retry. upstream HEAD: `0c67dc7f82`
(2026-05-20). 축: 재시도/백오프 코드의 lifecycle 결함 (초기화·해제 비대칭 /
shutdown 시 in-flight 처리 / dispose 경로 부재 / 상태 drift).

#### 결론: lifecycle-axis FIND 0건 (no lifecycle gap found)

본 셀의 retry/backoff 코드는 **per-key 누적 상태도, 모듈 레벨 mutable 자료구조도,
장기 timer 도, 해제가 필요한 자원 핸들도 보유하지 않는 순수 stateless 함수형
유틸리티**다. lifecycle 결함 5 카테고리 (A. load 실패 rollback / B. dispose/unload
경로 누락 / C. dynamic import 격리 / D. partial state / E. enable/disable drift)
모두 적용 불가하거나 부재 확인. 유일하게 실재하는 lifecycle-shaped gap (in-flight
retry 의 shutdown 미반응) 은 기존 `FIND-infra-retry-concurrency-001` 이 이미
`symptom_type: shutdown-gap` 으로 명시적으로 다뤘으며, 동일 root cause 의 재포장이
되므로 신규 FIND 미생성.

#### hint 별 검증 결과

grid.yaml 이 제시한 5개 hint 를 코드와 대조:

1. **retry.ts in-flight retry 의 SIGTERM/shutdown 종료 + caller cancellation
   통로** — 실재하나 신규 FIND 아님. `retryAsync` 의 `await sleep(delay)`
   (retry.ts:174) 는 취소 불가. shutdown 시 caller 가 abort 해도 sleep 이 완주
   후 다음 `fn()` 을 한 번 더 시도한다. 그러나 이 정확한 gap (AbortSignal 부재로
   in-flight sleep 이 완주 + 추가 fn() 시도 + shutdown 지연) 은 이미
   `FIND-infra-retry-concurrency-001` 이 `symptom_type: shutdown-gap`,
   `impact_hypothesis: hang` 으로 다뤘다 (해당 FIND 의 mechanism 4-5번,
   impact_detail "process exit 이 `delay * (attempts-1)` 만큼 지연" 참조).
   호출 컨텍스트의 "중복 회피용 알려진 인접 사실" 에도 명시. lifecycle 축으로
   재포장하면 중복 FIND → 미생성.
2. **retryAsync 가 AbortSignal 을 받지 않는 lifecycle gap** — hint 1 과 동일
   결함. 마찬가지로 concurrency-001 에 흡수됨.
3. **createChannelApiRetryRunner / createRateLimitRetryRunner 의 dispose/teardown
   hook** — FIND 아님. 두 runner factory (retry-policy.ts:54-83, 85-119) 는
   `resolveRetryConfig` 결과 + 콜백을 closure 로 캡처한 함수를 반환할 뿐, 해제가
   필요한 자원 (timer / listener / handle / 외부 연결) 을 일절 보유하지 않는다.
   `rg -n "setInterval|setTimeout|addEventListener|new Map|new Set|\.on\("
   src/infra/retry-policy.ts` → 0 매치. closure 는 caller 가 runner 참조를
   버리면 GC 로 회수되며 (memory-leak-hunter 섹션이 이미 "closure 크기 O(1)
   constant" 로 확인), dispose hook 이 *필요 없는* 설계다. dispose hook 부재는
   lifecycle 비대칭이 아니라 "해제할 것이 없음" → 카테고리 B 미해당.
4. **sleepWithAbort 의 abort listener / timer 가 retry 중단·정상완료 양 경로에서
   정리되는가** — FIND 아님 (이미 2개 셀이 확인). `sleepWithAbort`
   (backoff.ts:14-59) 의 `setTimeout` timer 와 `addEventListener("abort",
   onAbort, { once: true })` 는 (a) abort 경로: onAbort 가 `clearTimeout` +
   `removeEventListener` 무조건 실행 (L26-32), (b) 정상 완주 경로: timer 콜백이
   `removeEventListener` + `timer=null` (L46-50) — 양 경로 모두 unconditional
   cleanup. `infra-retry-memory` 셀과 `infra-retry-error-boundary` 셀이 동일
   결론. R-5 상 unconditional cleanup 존재 → FIND 금지.
5. **backoff.ts stateless 여부 / per-key lifecycle state** — FIND 아님.
   `computeBackoff` (backoff.ts:8-12) 는 `(policy, attempt)` 를 받아 즉시 숫자를
   반환하는 순수 함수. per-key Map / 모듈 mutable state / 누적 카운터 일절 없음
   (`rg -n "new Map|new Set|let |^let " src/infra/backoff.ts` → module-level
   mutable state 0건). reset/dispose 경로가 *필요 없는* stateless 설계.

#### R-3 Grep 결과 (lifecycle 대응 경로 탐색)

```
rg -n "(dispose|teardown|cleanup|shutdown|destroy|reset|unload)" \
   src/infra/retry.ts src/infra/retry-policy.ts src/infra/backoff.ts
  → retry-policy.ts:15  CHANNEL_API_RETRY_RE 의 "reset" 문자열 리터럴 (regex, 무관)
  → backoff.ts:27       sleepWithAbort 내부 clearTimeout (hint 4 - unconditional)
  → retry.ts:156        주석 텍스트 "server-cleared" (무관)
  → 그 외 dispose/teardown/destroy/unload 함수·메서드: 0 매치

rg -n "AbortSignal|AbortController|signal\.abort|\.aborted" \
   src/infra/retry.ts src/infra/retry-policy.ts
  → 0 매치 (retry.ts / retry-policy.ts 어디에도 abort 개념 없음)
  → backoff.ts:14,38,54 (sleepWithAbort 만 — retry 경로는 이를 사용 안 함)

rg -n "new Map|new Set|new WeakMap|setInterval|addEventListener" \
   src/infra/retry.ts src/infra/retry-policy.ts src/infra/backoff.ts
  → backoff.ts:37  sleepWithAbort 의 abort listener ({once:true}, hint 4)
  → 그 외: 0 매치 (mutable 자료구조 / 장기 timer / EventEmitter 부재)
```

#### R-5 실행 조건 분류표 (lifecycle 관점)

| 자원/상태 | 위치 | 해제 경로 | 실행 조건 |
|---|---|---|---|
| `sleepWithAbort` setTimeout timer | backoff.ts:44 | abort: L27 clearTimeout / 정상: L49 timer=null | 양 경로 모두 unconditional |
| `sleepWithAbort` abort listener | backoff.ts:37 | `{once:true}` + L31/L47 removeEventListener | 양 경로 모두 unconditional |
| retry runner closure (config+콜백) | retry-policy.ts:63-82, 98-118 | caller 가 runner 참조 폐기 시 GC | unconditional (자원 핸들 없음 — dispose 불요) |
| `retryAsync` 의 `lastErr` | retry.ts:92,119,125 | 매 attempt 단일 ref 덮어쓰기 | unconditional (누적 없음) |
| `retryAsync` in-flight `sleep(delay)` 취소 | retry.ts:174 | **부재** | concurrency-001 이 이미 다룸 — 본 셀 중복 회피 |

#### 카테고리별 적용/skip (페르소나 §탐지 카테고리 A~E)

- [x] A. Load 실패 rollback 부재 — skip (해당 없음). retry/backoff 에 "load /
  register / activate" 단계 자체가 없음. 함수형 유틸.
- [x] B. Dispose / Unload 경로 누락 — applied → FIND 아님. retry-policy runner
  와 backoff/retry 함수 모두 해제 필요 자원이 없어 dispose 가 *불필요*.
  "register* 있는데 unregister* 없음" 류 비대칭 부재 (애초에 register* 없음).
- [x] C. Dynamic import 에러 격리 — skip. 세 파일에 `await import()` 없음.
- [x] D. Manifest parse 실패 후 partial state — skip. manifest / JSON.parse /
  registry entry 부재.
- [x] E. Enable / Disable 상태 drift — skip. config enable flag 와 동기화할
  runtime registry 자체가 없음. retry 는 호출 1회 수명 범위.
- [x] 추가 (in-flight retry shutdown 미반응) — applied → 결함 실재하나
  `FIND-infra-retry-concurrency-001` 과 동일 root cause → 중복으로 FIND 미생성.

#### CAL-007 — upstream fix 검사

`git log --oneline -10` (retry.ts / retry-policy.ts / backoff.ts):
- retry.ts 최근 commit (b7fc2451e7, a77bd213ce, 317e474b84, 62dff64700,
  d49480527e) 전부 jitter / Retry-After contract 수치 축. lifecycle (abort 전파
  / dispose / shutdown) 을 손댄 commit 없음.
- retry-policy.ts 최근 commit (63b728de43, 3987ca4099, eb09d8dd71 등) 은
  Telegram shouldRetry 조합 / 421 처리 — runner lifecycle 무관.
- backoff.ts 는 11006d1245 (`refactor: share backoff helpers`) 이후 lifecycle
  변경 없음. `sleepWithAbort` 의 양 경로 cleanup 구조 유지.
- 즉 in-flight shutdown gap 은 upstream 에서 fix 되지 않은 현행 상태이나, 이미
  concurrency 축 FIND 가 다룸.

#### 확인 못 한 영역 (self-critique)

- `src/infra/abort-signal.ts` 의 `waitForAbortSignal` 은 abort-aware 대기를
  제공하나 allowed_paths 밖. `plugin-sdk/runtime-env.ts` 가 `retryAsync` 와
  `waitForAbortSignal` 을 나란히 export 한다 — abort-aware 유틸이 infra 에
  존재하는데 retry.ts 가 안 쓴다는 점은 concurrency-001 의 근거와 일치.
- `compaction.ts:357` 의 caller 가 `shouldRetry: (err)=>!isAbortError(err)` +
  `fn()` 내부 `params.signal` 조합으로 abort-aware 패턴을 우회 구현한다. 이
  패턴은 `catch` 직후 (sleep 진입 전) 에만 abort 를 평가하므로, sleep 구간에
  들어온 abort 는 다음 attempt 의 `fn()` 호출 (이미 aborted signal → 즉시
  reject → catch → shouldRetry false → 탈출) 까지 1 사이클 지연된다. 이는
  concurrency-001 mechanism 의 직접 귀결이며 별개 root cause 아님 → 신규 FIND
  부적격 (R-7: 동일 결함의 다른 표현).
- retry runner 가 production 에서 long-lived singleton 으로 유지될 때 closure
  retention 은 memory-leak-hunter 셀이 이미 O(1) 으로 판정. 본 셀 범위 밖.

#### 산출

- FIND-infra-retry-lifecycle-NNN: **0건**. 사유: retry/backoff/retry-policy 는
  순수 stateless 함수형 유틸로 초기화·해제 비대칭 / dispose 경로 부재 / 상태
  drift 같은 lifecycle 결함이 성립할 자원·상태·등록 단계 자체가 없다. 유일하게
  실재하는 lifecycle-shaped gap (in-flight retry 의 shutdown 미반응) 은
  `FIND-infra-retry-concurrency-001` 이 `symptom_type: shutdown-gap` 으로
  이미 다룬 동일 root cause 이므로 중복 회피상 신규 FIND 미생성.

