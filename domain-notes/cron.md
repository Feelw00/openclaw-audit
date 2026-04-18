# cron 도메인 노트

openclaw 의 `src/cron/**` 서브시스템에 대한 영구 관찰 기록.
페르소나/세션별로 append-only 로 추가.

---

### cron-reliability-auditor (2026-04-18)

#### 적용 카테고리 (agents/cron-reliability-auditor.md §탐지 카테고리)

- [x] A. 분산 락 / 중복 실행 — 적용 → FIND-cron-concurrency-001, 003
- [x] B. Promise.race 잔여 promise — skip
  - 사유: `executeJobCoreWithTimeout`(timer.ts:77-103) 에 AbortController + finally 의 clearTimeout
    이 이미 있다. loser 경로도 runAbortController.abort() 호출로 취소되고, timeoutId 는
    finally 블록에서 항상 clear. 이전 세션의 구두 의심은 확인 결과 "이미 방어됨" 으로 결론.
- [x] C. Catch-up / startup ordering — skip (부분)
  - 사유: `planStartupCatchup`(timer.ts:971-1026) 는 locked 블록 안에서 runnable job 에 runningAtMs
    를 선점하고 persist 한 뒤 execute → applyStartupCatchupOutcomes(1079-1116) 로 마무리한다.
    동일 프로세스 내부 ordering 은 stagger + sorted by nextRunAtMs 로 결정적.
    그러나 **멀티 인스턴스 경합** 은 FIND-001 의 상위 문제에 포섭되므로 중복 카드 생성 안 함.
    이전 세션의 "startup catch-up ordering 우려" 는 단일 인스턴스 맥락에서는 재현 시나리오 미발견.
- [x] D. Timer 수명·재무장 — skip
  - 사유: `armTimer`(timer.ts:614-670), `armRunningRecheckTimer`(672-681), `stopTimer`(1398-) 모두
    재무장 전 `clearTimeout(state.timer)` 를 일관되게 수행. early return 경로(619-622, 624-643)
    에서도 state.timer 를 null 로 설정. 파일 내 clearTimeout 호출: 616, 674, 1154, 1400.
    pending timer 누출 의심 지점 발견 못 함.
- [x] E. Stuck state 복구 — 적용 → FIND-cron-concurrency-002
- [ ] F. 시간 영역 — skip
  - 사유: 이번 셀 스코프는 동시성/재시작 race 이며 UTC/DST 는 별도 도메인 (cron-schedule/timezone).

#### R-3 Grep 결과

1) `rg -n "Redisson|redlock|Redis.*lock|distributed.*lock|file.*lock" src/`
   - cron 경로 매치 0건. `src/plugin-sdk/file-lock.ts` / `src/infra/file-lock.ts` 는 존재하지만
     `src/cron/**` 에서 import 하는 호출지점 없음.
   - `src/cron/session-reaper.ts:49` 주석이 "session-store file lock" 을 언급하나, 이는
     `agents/session-write-lock` (다른 파일) 에 대한 주석으로 cron store 와 무관.
   - 결론: cron 경로에 분산 락 전혀 없음 → FIND-001 성립.

2) `rg -n "clearTimeout\(|AbortController|signal\.abort" src/cron/`
   - `src/cron/delivery.ts:60,89` — outbound delivery 의 timeout 처리 (AbortController + clearTimeout).
   - `src/cron/service/timer.ts:86,90,93,100,616,674,1154,1400` — 타임아웃·재무장 경로.
   - `src/cron/service.test-harness.ts:209`, 각 regression 테스트들 — 테스트 유틸.
   - 결론: timer cleanup / abort 경로 정상 방어됨 → 카테고리 B, D skip.

3) `rg -n "heartbeat|liveness|stuck.*recover" src/cron/`
   - "heartbeat" 매치 다수: 모두 **main-agent heartbeat** (wakeMode: "next-heartbeat",
     runHeartbeatOnce, heartbeat-policy.ts 등) — cron job 자체의 liveness/keepalive 가 아님.
   - "liveness" 매치 0건.
   - "stuck.*recover" 매치 0건.
   - `service.armtimer-tight-loop.test.ts:13-15`, `service.issue-13992-regression.test.ts:143`,
     `service.restart-catchup.test.ts:136,215` 에 "stuck runningAtMs" 주석/주입이 있으나 모두
     **STUCK_RUN_MS 초과 후 clear 확인** 테스트.
   - 결론: runningAtMs 에 대한 heartbeat-based liveness 없음 → FIND-002 성립.

#### 주요 관찰

- `locked()` 는 `Map<string, Promise<void>>` (module-scope) + `state.op` 프로미스 체인이 전부.
  멀티 프로세스 환경 전제 없음. 파일 락/분산 락 전혀 미사용. (FIND-001)
- `Promise.race` 경로는 timer.ts:89-97 에만 존재하며 AbortController + try/finally 로 loser 취소.
  이전 세션 의심 해소.
- Startup catch-up (planStartupCatchup/applyStartupCatchupOutcomes) 은 단일 프로세스 범위에서
  결정적 ordering 유지. Deferred jobs 는 `baseNow + staggerMs * offset` 으로 분산 (timer.ts:1097-
  1107). 다중 인스턴스 경합은 FIND-001 로 포섭.
- stuck runningAtMs 복구는 **오직** `normalizeJobTickState`(jobs.ts:399-407) 의 `nowMs - runningAt
  > STUCK_RUN_MS (2h)` 검사에 의존. 프로세스 ownership/heartbeat 부재. (FIND-002)
- `executeJob`(timer.ts:1309) 은 locked 밖에서 runningAtMs 를 조건 없이 대입 → onTimer 경로와
  race. `_opts.forced` 는 시그니처에만 있고 body 에서 참조되지 않음. (FIND-003)

#### 확인 못 한 영역 (self-critique)

- `src/cron/service/ops.ts` 의 `run` 공개 API 가 executeJob 을 감싸는 locked/preflight 전체 경로:
  일부 Grep 으로 확인했으나 `executeJob` 직접 호출자 전수 추적 미완. FIND-003 에 해당 영향 기술.
- `runDueJobs`(timer.ts:1118-1126) 호출자와 호출 빈도 — onTimer 와 중복 호출되는 경로가 있는지
  미확인.
- Gateway 수준에서 동일 jobId run 요청을 singleflight 로 묶는 middleware 가능성 — allowed_paths
  밖이라 확인 불가.
- `persist()` 가 atomic rename 기반인지 여부 — allowed_paths 내 store.ts 를 읽지는 않음.
  atomic 이어도 read-modify-write 사이 race 는 그대로 존재하므로 FIND-001 의 결론에 영향 없음.
- UTC/DST (카테고리 F): 본 셀 스코프에서 의도적으로 제외.

### clusterer (2026-04-18)

- **CAND-002 (epic)**: FIND-cron-concurrency-001 + FIND-cron-concurrency-003 을 공통 원인
  "`CronJobState.runningAtMs` 의 claim 이 원자적 조건부 교체(CAS) 가 아니라 비조건 read-modify-write
  로 구현됨" 으로 묶음. 두 FIND 모두 root_cause_chain 에서 CAS/guard 부재를 직접 지적하며,
  FIND-003 이 `cross_refs: [FIND-cron-concurrency-001]` 를 이미 선언하고 있었다. 세팅 주체가 프로세스
  경계(001) vs 코루틴 경계(003) 로 다르나 해결 근본 축이 공통. Severity 는 P1 (001 상속).
- **CAND-003 (single)**: FIND-cron-concurrency-002. 원인 축이 "세팅된 runningAtMs 의 liveness/소유권
  검증 부재" 로 CAND-002 (claim 연산 원자성) 와 다름. heartbeat/PID/boot-ID/lease token 부재 + 단일
  STUCK_RUN_MS 상수 의존. Severity P1.
- 두 CAND 는 인접하지만 독립적 해결 축(atomic claim vs liveness probe) 을 가지므로 epic 통합하지 않음.

### memory-leak-hunter (2026-04-18)

셀: `cron-memory` (allowed_paths: `src/cron/**`). 결론: **FIND 0건** (품질 > 수량).

#### 적용 카테고리 (agents/memory-leak-hunter.md §탐지 카테고리)

- [x] A. 무제한 자료구조 성장 — 적용 (결과: FIND 없음. 아래 상세)
- [x] B. EventEmitter / 리스너 누수 — 적용 (결과: FIND 없음)
- [x] C. 강한 참조 체인 (weak 부재) — 적용 (결과: FIND 없음)
- [x] D. 핸들/리소스 누수 — 적용 (결과: FIND 없음)
- [x] E. 캐시 TTL 부재 — 적용 (결과: FIND 없음)

#### R-3 Grep 결과

모듈-스코프 Map/Set 전수 조사 (`rg "^(const|let|var)\s+\w+\s*=\s*new (Map|Set|WeakMap)" src/cron/`):

| 변수 | 파일:라인 | 쓰기 | 정리 | 키 도메인 |
|---|---|---|---|---|
| `staggerOffsetCache` | `service/jobs.ts:40` | `.set` 82 | `.delete` 79 (FIFO, cap 4096) | `${staggerMs}:${jobId}` — 잡 개수 경계 |
| `cronEvalCache` | `schedule.ts:7` | `.set` 30 | `.delete` 26 (FIFO, cap 512) | `${tz}\0${expr}` — 크론 표현식 경계 |
| `COMPLETED_DIRECT_CRON_DELIVERIES` | `isolated-agent/delivery-dispatch.ts:142` | `.set` 242 | `.delete` 204, 220 (TTL + cap 2000) | idempotency key — TTL 24h |
| `writesByPath` | `run-log.ts:81` | `.set` 169 | `.delete` 174 (`get===next` guard, finally) | 파일 경로 — 잡당 1 |
| `serializedStoreCache` | `store.ts:9` | `.set` 162,164,232,248 | `.delete` 170,171 (ENOENT), 매 write 덮어쓰기 | storePath — 프로세스당 1-2 |
| `storeLocks` | `service/locked.ts:3` | `.set` 19 | **없음** | `state.deps.storePath` — 프로세스당 1-2 |
| `lastSweepAtMsByStore` | `session-reaper.ts:22` | `.set` 74,106 | `.clear` 148 (**test-only**) | storePath — 프로세스당 1-2 |
| `activeJobIds` (글로벌 싱글톤) | `active-jobs.ts:11` | `.add` 19 | `.delete` 26 (markCronJobActive↔clearCronJobActive) | jobId — 구성된 잡 개수 경계 |

`rg "setInterval\(|setTimeout\(" src/cron/` / `rg "clearInterval|clearTimeout" src/cron/`:
- `delivery.ts:61` setTimeout ↔ `:89` clearTimeout (finally, unconditional).
- `service/timer.ts:92` setTimeout ↔ `:100` clearTimeout (finally, unconditional).
- `service/timer.ts:1149` setTimeout ↔ `:1154` clearTimeout (onAbort callback, unconditional once fired or aborted).
- `service/timer.ts:661, 676` `state.timer = setTimeout(...)` ↔ `armTimer`, `armRunningRecheckTimer`, `stopTimer` 의 `clearTimeout(state.timer)` (616, 674, 1400). 재무장/종료 시 항상 clear. 기존 cron-reliability-auditor 기록과 일치.

`rg "\.on\(|addEventListener|removeListener|\.off\(|removeEventListener" src/cron/`:
- `service/timer.ts:1158` `abortSignal.addEventListener("abort", onAbort, { once: true })` ↔ `:1150, :1155` `removeEventListener`. `once: true` + 양방향 removeListener 패턴. 누수 없음.
- 다른 EventEmitter 리스너 등록 없음.

#### R-5 execution condition 분류 (counter_evidence 각 후보당)

| 후보 | 경로 | 조건 |
|---|---|---|
| `storeLocks` | 없음 | — (저장 경로 상수화로 성장 바운드) |
| `lastSweepAtMsByStore` | `resetReaperThrottle` (148) | **test-only** |
| `activeJobIds` — 타이머 틱 | `applyOutcomeToStoredJob` 첫 줄 `clearCronJobActive(result.jobId)` (timer.ts:582) | **unconditional** — `applyOutcomeToStoredJob` 진입 시 무조건 실행. |
| `activeJobIds` — ops 경로 | `executeJob` 말미 `clearCronJobActive(job.id)` (timer.ts:1350) | **conditional-edge** — try/finally 감싸기 없음. 하지만 이전 단계(`applyJobResult`, `emitJobFinished`) 가 예외 던질 가능성 낮음 (순수 상태 변이 + try-wrapped emit). |
| `writesByPath` | `writesByPath.delete(resolved)` (run-log.ts:174) in finally with `get===next` guard | **unconditional (소유자 promise 기준)** — 최신 set 의 finally 가 자신 엔트리를 반드시 delete. |
| `serializedStoreCache` | 매 `saveCronStore` 시 `set` 으로 덮어쓰기 | **unconditional** — 새 값이 오래된 값을 대체. |
| `COMPLETED_DIRECT_CRON_DELIVERIES` | `pruneCompletedDirectCronDeliveries` (200-222) in `remember` + `get` 양쪽 | **unconditional** — TTL 24h + cap 2000 FIFO 병행. |
| `staggerOffsetCache`, `cronEvalCache` | FIFO eviction at cap | **unconditional** |

#### FIND 생성 금지 근거 (R-5 적용)

- **storeLocks / lastSweepAtMsByStore / serializedStoreCache**: 키가 `state.deps.storePath` 로 구성-레벨 상수. 단일 게이트웨이 프로세스에서 1-2 항목. 이론상 누수지만 *growth rate = 0* 이므로 leak 주장 불성립.
- **activeJobIds (ops 경로)**: clearCronJobActive 미-finally 배치는 defensive weakness 이나 (a) `applyJobResult`/`emitJobFinished` 가 실질적으로 throw 하지 않고, (b) Set 키가 구성된 jobId 로 바운드되어 동일 jobId 재마크는 `Set.add` idempotent → 성장 없음. CAL-001 의 false-positive 패턴 (primary-path 부재 전제) 을 반복할 위험 커서 FIND 미생성.
- **writesByPath**: 가드된 delete 패턴 (`get===next`) 은 의도된 unconditional cleanup (소유자-promise 기준). race 분석 결과 Node single-thread 마이크로태스크 순서로 safe.
- **타이머 / AbortSignal 리스너**: 모두 unconditional clear. 기존 cron-reliability-auditor 도 B/D 카테고리 skip 확인.

#### CAL-002 반영 (array + object-typed field 양쪽 검증)

cron 도메인은 registry-style public field (`registry.X.push` + `registry.X[key]=`) 가 없음. `state.store.jobs` 는 배열이고 delete는 filter 교체. object-typed hidden-field 누수 대상 모듈 구조 없음.

#### 확인 못 한 영역 (self-critique)

- `src/gateway/server-cron.ts` (allowed_paths 밖): `warnedLegacyWebhookJobs = new Set<string>()` 는 delete 없음. 이벤트 jobId 마다 add 하나 발생 (실패 알림 legacy path). 본 셀 스코프 밖이므로 별도 셀 (`gateway-memory`) 에서 다뤄야 함.
- `state.deps.onEvent` 콜백이 caller 측에서 리스너 누적 구조를 쓰는지: 콜백 등록은 caller 책임. cron 본체 내부 leak 아님.
- `isolated-agent/run-*.runtime.ts` 의 runtime 캐시: dynamic-import promise 변수 (`gatewayCallRuntimePromise` 등) 는 모듈 한 번만 로드하므로 leak 아님.
- `task-executor` / `task-registry` 연동 (`createRunningTaskRun` 등) 은 allowed_paths 밖. 해당 registry 의 cleanup 은 외부 책임.
