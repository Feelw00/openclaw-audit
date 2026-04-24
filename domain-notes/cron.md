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

### memory-leak-hunter 재방문 (2026-04-19)

이전 세션 (2026-04-18) 이 `cron-memory` 셀에 대해 **FIND 0건** 으로 결론. 24시간 내 변경 파일 재검증:

- `src/cron/store.ts` (Apr 18 22:00 mtime): `serializedStoreCache` 구조 동일. `.set` (97, 141, 160) + `.delete` (101, ENOENT 경로). 키는 `storePath` — 프로세스 내 1-2 항목. 성장률 0.
- `src/cron/service/jobs.ts` (Apr 18 22:00 mtime): 모듈-스코프 구조는 `staggerOffsetCache` 만 존재 (40). FIFO eviction at cap 4096 (76-81). 변화 없음.
- `src/cron/service/ops.ts` (Apr 18 22:00 mtime): `nextManualRunId` (395) — 단조 증가 **number** (Map/Set 아님) → leak 카테고리 해당 없음.
- `src/cron/service/timer.test.ts` (Apr 18 22:00 mtime): 테스트 파일. 프로덕션 경로 영향 없음.

`rg "^(const|let|var)\s+\w+\s*=\s*new (Map|Set|WeakMap)" src/cron/` 재실행 결과 이전 인벤토리와 100% 일치 (7개 Map). 신규 누수 후보 없음.

재결론: **FIND 0건** (품질 > 수량). primary-path inversion 후 모든 후보가 unconditional cleanup / bounded key domain / TTL+cap 중 하나에 해당.

### memory-leak-hunter fresh re-audit (2026-04-19, CAL-007 대응)

이전 두 세션(2026-04-18, 2026-04-19)은 stale upstream(913 commits behind) 에서 수행됐음이 CAL-007 로 확인됨.
오퍼레이터가 upstream/main @ `8879ed153d` 로 local 을 fast-forward 한 뒤 `cron-memory` 셀 재투입.

**변경 사항**: 지난 2주(upstream/main `--since="2 weeks ago"`) 내 `src/cron/` 커밋 100+ 건 — 대다수가
test/perf/refactor (lazy-load runtime 분리, dedupe, seam mock) 이고, 메모리 의미에 영향을 주는 production
변경은 다음 소수:

| commit | 파일 | 메모리 측면 영향 |
|---|---|---|
| 9501656a8e (#67807) | isolated-agent/delivery-dispatch.ts | `cleanupDirectCronSessionIfNeeded` 에 `directCronSessionDeleted` idempotency guard 추가. `COMPLETED_DIRECT_CRON_DELIVERIES` 의 set/delete/prune 경로(240-262, 282, 293) 불변. 누수 방향 변화 없음. |
| 13a0d7a9e0 / 49ae60d6ca / 31437b9e3b | message-tool policy | payload-filter 로직. 자료구조 증가 없음. |
| f61896b03c (#68210) | awareness 라벨 preserve | normalize 경로. 자료구조 증가 없음. |
| 190a4b4869 (#66113) / c602824215 (#66083) | next-run backoff/refire 방지 | nextRunAtMs 의 truthiness 가드. 메모리 누수 아닌 logic fix. |
| 905e56d191 (#63507) | zero nextRunAtMs invalid | validator. 자료구조 변화 없음. |
| ae3d731810 / ab4efa47b5 / 28787985c4 … (perf: lazy-load) | 각 runtime.ts seam | `*RuntimePromise` 단일 dynamic-import promise — 모듈당 1회 할당 후 재사용. leak 아님. |
| active-jobs.ts 리팩터 (이전 세션 대비) | `resolveGlobalSingleton` 으로 이동 | `activeJobIds` 가 module-level → global singleton 구조로 변경됐으나 의미 동일 (add/delete/clear 경로 불변). |

**Map/Set 인벤토리 (fresh)**: 2026-04-18 기록과 100% 동일한 7개 Map + 1개 Set (global singleton).

```
store.ts:9              serializedStoreCache          (storePath key, set/delete + overwrite)
service/locked.ts:3     storeLocks                    (storePath key, delete 없음 — growth rate 0)
session-reaper.ts:22    lastSweepAtMsByStore          (storePath key, test-only clear)
service/jobs.ts:40      staggerOffsetCache            (FIFO cap 4096)
schedule.ts:7           cronEvalCache                 (FIFO cap 512 + test clear)
run-log.ts:82           writesByPath                  (owner-guard delete in finally)
isolated-agent/delivery-dispatch.ts:182 COMPLETED_DIRECT_CRON_DELIVERIES (TTL 24h + cap 2000 FIFO)
active-jobs.ts:11       activeJobIds (singleton)      (add/delete per markCronJobActive/clear)
```

**타이머/리스너** (`setTimeout`/`clearTimeout`/`addEventListener`/`removeEventListener` 전수):

| 페어 | 파일:라인 | cleanup 경로 | 조건 |
|---|---|---|---|
| delivery.ts | 61 set, 89 clear | finally | unconditional |
| service/timer.ts (executeJobCoreWithTimeout) | 92 set, 100 clear | finally | unconditional |
| service/timer.ts (armTimer) | 661 set ↔ 616 clear | 재무장 전 | unconditional |
| service/timer.ts (armRunningRecheckTimer) | 676 set ↔ 674 clear | 재무장 전 | unconditional |
| service/timer.ts (waitForAbortOrTimeout) | 1149 set, 1154 clear, 1150/1155 removeEventListener, 1158 addEventListener{once:true} | onAbort + finally | unconditional (양방향) |
| service/timer.ts (stopTimer) | 1400 clear | shutdown | shutdown |

`.on(` EventEmitter 등록은 cron/ 내부 프로덕션 경로에 0건. `storePaths` (timer.ts:816), `interruptedOneShotIds`
(ops.ts:107), `prunedSessions` (session-reaper.ts:79), `referencedSessionIds` (session-reaper.ts:111),
`latestByChild` (subagent-followup.ts:32), `allowFromOverride` (delivery-target.ts:201) 는 모두 함수-scope
allocation — GC 대상.

**R-5 primary-path inversion 재적용**:

- 모든 Map/Set 에 대해 "이 누수가 성립하려면 어떤 cleanup 경로가 실패해야 하는가?" 질문 → 각 구조마다 최소 1개
  unconditional cleanup 경로 존재. 따라서 FIND 불성립.
- 예: `writesByPath.delete` 는 `try { await next } finally { if (get === next) delete }` 에서 finally
  가 try 이후 무조건 실행되고, guard 는 concurrent-overwrite 시만 no-op (다른 owner 가 책임). 실제 leak 시나리오
  재현 불가.
- `storeLocks` 는 delete 부재가 진짜 defensive weakness 이지만 키 도메인이 `state.deps.storePath` 구성상수로
  프로세스당 1-2. 성장률 0 → leak 주장 성립 안 함.

**결론**: fresh upstream (8879ed153d) 에서 재검증한 결과 **FIND 0건**. 이전 두 세션의 0 판정 유지.
최근 2주 변경은 메모리 의미에 영향 없음 (test/perf/lazy-load 리팩터, delivery idempotency guard 추가).

### error-boundary-auditor (2026-04-24, upstream @ 22f23fa5ab)

셀: `cron-error-boundary` (allowed_paths: `src/cron/**`). upstream/main fast-forward 확인 (HEAD == upstream/main == `22f23fa5ab`, behind=0). 결론: **FIND 0건** (정직한 0 판정).

#### 적용 카테고리 (agents/error-boundary-auditor.md R-5 축)

- [x] unhandled-rejection-in-timer-callback — applied
- [x] throw-in-primary-handler-without-catch — applied
- [x] floating-promise-no-error-propagation — applied
- [x] process.exit-pre-cleanup-skipped — skip (cron/ 내부 process.exit 0건)
- [x] JSON.parse 미보호 — skip (run-log.ts:267 try 로 방어)

#### R-3 방어 경로 Grep (결과)

1) `rg -n "process\.on\(|process\.once\(" src/cron/` → **0건**. cron/ 은 process-level handler 미설치 (책임 경계는 src/infra/).
2) `rg -n "\.catch\(" src/cron/` → 14건. 모두 실제 방어 (store.ts chmod/unlink, timer.ts timer callback, ops.ts enqueue, run-log.ts fs read/stat).
3) `rg -n "try\s*\{" src/cron/` → production 파일 기준 18건. 주요 방어 포인트 전수 맵핑됨.
4) `rg -n "void\s+\w+" src/cron/` → production 에서 3건: `timer.ts:362 sendCronFailureAlert...catch`, `timer.ts:666/681 onTimer...catch`, `ops.ts:704 enqueueCommandInLane...catch`. 모두 `.catch` 체이닝 有.
5) `rg -n "setImmediate|process\.nextTick|process\.exit" src/cron/` → **0건**.
6) `rg -n "JSON\.parse" src/cron/` production 1건 (`run-log.ts:268`) — try 블록 내.

#### R-5 execution condition 분류 (error-boundary 축 후보)

| 후보 | 위치 | primary-path throw 조건 | 방어 상태 |
|---|---|---|---|
| `onTimer` 내 `await persist/ensureLoaded` throw | timer.ts:706-814 | disk EACCES/ENOSPC/EBUSY | try/finally → finally 가 `armTimer` 재호출. setTimeout 콜백 `.catch` 가 재throw 흡수. **self-healing** |
| `onTimer` finally 블록 내 `resolveSessionStorePath(agentId)` throw | timer.ts:820-834 | callback 은 `resolveStorePath` wrapper (paths.ts:280) — 순수 string 처리. throw 조건 사실상 없음 (resolveRequiredHomeDir 은 cwd fallback) | **production path 에서 throw 불가** — R-1 위반 FIND 금지 |
| `emit` 내 `onEvent` async throw | timer.ts:1431-1437 | 주입된 onEvent 가 async function 이고 내부 throw | **try/catch 는 sync throw 만 잡음**. async rejection 은 unhandled. 단 gateway/server-cron.ts (L275) 의 onEvent 는 sync 함수임 — production caller 에서 async onEvent 없음. FIND 불성립 |
| `executeJob` (L1332-1375) 의 `applyJobResult` throw → `clearCronJobActive` 누락 | timer.ts:1360 | applyJobResult 내부 `emitFailureAlert → enqueueSystemEvent` throw | cron-memory 재감사 (2026-04-19) 에서 R-5 conditional-edge 로 이미 분류. primary-path throw 가능성 없음 — 중복 abandon |
| timer callback `state.timer = setTimeout(() => { void onTimer(state).catch(...) })` 의 catch 후 rearm 부재 | timer.ts:665-669 | onTimer 의 throw 가 finally 전에 발생하는 경로 필요. 하지만 try 는 L706 부터, finally 는 L815 — **throw 경로는 모두 try 안** → finally 실행 → armTimer | **방어됨** |
| `emitFailureAlert` 의 `state.deps.enqueueSystemEvent` throw (L380) | timer.ts:380-383 | enqueueSystemEvent 가 throw 하면 applyJobResult 상위로 전파 | enqueueSystemEvent (infra/system-events.ts:90) 는 module-level queue.push — throw 조건 없음 |

#### primary-path inversion (CAL-001) 결과

각 후보별로 "이 error-boundary gap 이 trigger 되려면 어떤 주입된 callback/의존성이 production 에서 throw 해야 하는가?" 질문:

- `resolveSessionStorePath`: `path.resolve + path.join` 수준 (paths.ts:280-312) — throw 조건 `process.cwd()` 실패뿐인데 fallback 으로 guard. **production throw 조건 없음**.
- `enqueueSystemEvent`: `system-events.ts:90` 의 in-memory queue push — throw 조건 없음.
- `onEvent` async rejection: gateway 구현 (server-cron.ts:275) 이 **sync function** 이라 async rejection 생성 불가.

R-1 (production hot-path caller 증명) 미충족 → 모든 후보 FIND 불생성.

#### upstream 최근 cron fix (6 주 기준)

`git log upstream/main --since="6 weeks ago" -- src/cron/` (60+ 커밋). error-boundary 의미상 관련된 commit:

| commit | 요약 |
|---|---|
| `9501656a8e` (#67807) | `cleanupDirectCronSessionIfNeeded` idempotency guard (directCronSessionDeleted Set). 본 셀 영역 밖 (delivery-dispatch). |
| `190a4b4869` (#66113) / `c602824215` (#66083) | `nextRunAtMs` truthiness 가드 — logic fix. 에러 경계 무관. |
| `905e56d191` (#63507) | zero `nextRunAtMs` validator — logic fix. 에러 경계 무관. |
| `4be6ff9d5f` (#63105) | jobs.json split into config + state file — store.ts 구조 변화. 에러 경계 의미 변화 없음 (saveCronStore / loadCronStore 에러 handling 유지). |
| `cb16d22780` | retire bundled mcp runtimes — runtime 제거 리팩터. 에러 경계 무관. |
| `9db67e79a5` / `851bef9c25` | accountId spoof guard — 인증 로직. 에러 경계 무관. |

**error-boundary 축으로 upstream 최근에 merge 된 fix 없음**. 본 셀 결과에 영향 주는 선행 fix 부재.

#### 중복 축 회피 (cron-memory / cron-concurrency 와 분리)

| 축 | 이전 셀 결과 | 본 셀 재평가 |
|---|---|---|
| Map/Set growth (activeJobIds, writesByPath, …) | cron-memory 재감사 (2026-04-19) FIND 0 | 본 셀 재탐지 금지 (overlap) |
| runningAtMs claim race | cron-concurrency FIND 001/003 → CAND-002 (파이프라인 이동 중) | 본 셀 재탐지 금지 |
| setTimeout/setInterval leak | cron-memory 재감사 FIND 0 (모두 unconditional clear) | 본 셀 재탐지 금지 |
| throw propagation in timer callback | 이전 셀 축 밖 | **본 셀 고유** — 위에 R-5 적용, 0건 |
| unhandledRejection / process-level handler | allowed_paths 밖 (src/infra/) | 본 셀에선 skip |

#### 확인 못 한 영역 (self-critique)

- `state.deps.onEvent` 의 async variant 가 미래에 도입될 가능성 — 현재 gateway 구현은 sync 이나 cron 본체 타입 시그니처는 `(evt: CronEvent) => void` 로 명시되어 있음 (state.ts:117). 따라서 async 주입은 타입 에러 → 사실상 compile-time 방어.
- `src/cron/isolated-agent/**` 하위 모듈 (delivery-dispatch 등): 본 셀 priority_files 밖. 이 서브디렉터리는 이전 cron-memory 재감사에서 일부 확인 (COMPLETED_DIRECT_CRON_DELIVERIES TTL 방어). error-boundary 축으로 미탐색 — 별도 셀 (`cron-isolated-agent-error-boundary`) 정당화 가능하나 우선순위 낮음.
- `src/gateway/server-cron.ts` 의 `void (async () => await postCronWebhook(...))()` 패턴 (L455, L490): IIFE 에 try/catch 없음. 하지만 `postCronWebhook` 자체가 자체 catch 내장 여부 미확인 — **allowed_paths 밖** (gateway/). 별도 셀 `gateway-cron-error-boundary` 에서 다뤄야 함.
- `persist()` throw 후 in-memory store 와 disk 상태 divergence: error-boundary 가 아닌 data-consistency 축. 본 페르소나 scope 밖.

#### 결론

cron/ 내부는 error-boundary 축에서 정합한 방어를 갖춤:
- 모든 timer callback 은 `.catch` 로 감쌈
- onTimer 의 try/finally 가 armTimer rearm 을 보장
- emit 의 sync try/catch 가 onEvent throw 삼킴 (타입상 async 주입 불가)
- JSON.parse 는 try 블록 내
- process-level handler 는 책임 경계상 cron/ 외부 (infra/)

**FIND 0건**. 메인테이너 우선순위 (cron + reliability) 에 중요한 모듈이라 이미 hardening 완료된 상태. false-positive 비용 고려하여 정직한 0 판정.

### clusterer (2026-04-24)

- **CAND-024 (epic)**: FIND-cron-lifecycle-001 + FIND-cron-lifecycle-002 을 공통 원인 "upstream
  commit `7d1575b5df` (#60310, 2026-04-04) 가 activeJobIds 싱글톤 도입 시 timer.ts 의 runDueJob /
  executeJob 2개 경로에만 mark/clear 주입하고 runStartupCatchupCandidate (timer.ts:1043-1081) 와
  prepareManualRun / finishPreparedManualRun (ops.ts:548-686) 두 경로를 간과한 partial merge
  gap" 으로 묶음. 두 FIND 모두 root_cause_chain[0] 에서 동일 commit 을 evidence_ref 로 지목하며,
  FIND-002 가 이미 `cross_refs: [FIND-cron-lifecycle-001]` 로 연결 선언. fix surface 공통
  (mark/clear 호출 주입), regression test axis 공통 (real activeJobIds Set + tryCreate*TaskRun
  통합), 메인테이너 커뮤니케이션 공통 (#68157 이 양쪽 증상 manifestation). CAND-002 epic 선례
  (단일 CAS 축으로 두 동시성 경계 묶음) 패턴 대응. Severity P2 (양쪽 동일). related_issue
  #68157 (OPEN 2026-04-23) 기록.
- 인접 lifecycle 축 (async-stop-not-awaited / partial-rollback-on-failure / storeLocks 재사용) 은
  plugin-lifecycle-auditor 세션에서 upstream-dup (PR #43832, #68112) 또는 이전 cron-memory 셀
  결과 (growth rate 0) 로 이미 abandon 된 상태 — epic scope 확장 금지.

### plugin-lifecycle-auditor (2026-04-24, upstream @ 22f23fa5ab)

셀: `cron-lifecycle` (allowed_paths: `src/cron/**`). upstream HEAD == local HEAD == `22f23fa5ab` (behind=0 확인). 결론: **FIND 2건 (P2/P2)**.

#### 적용 카테고리 (lifecycle 축 분류 — R-5)

- [x] **init-without-init (cross-module contract gap)**: `activeJobIds` singleton → task-registry 의 `hasBackingSession` 이 신뢰하는 유일한 cron liveness signal. mark 경로가 실행 경로 3개 중 1개 (runDueJob, timer.ts:746) + executeJob (timer.ts:1344) 만 커버. runStartupCatchupCandidate (timer.ts:1043) 와 manual run (prepareManualRun/finishPreparedManualRun, ops.ts:548-686) 두 경로에서 mark 부재 → FIND-001/002.
- [x] **async-stop-not-awaited**: `stopTimer` (timer.ts:1424-1429) 이 in-flight onTimer 를 await 하지 않음. onTimer.finally → armTimer (853) 가 stop 후 timer 재장착. **PR #43832 (OPEN, 2026-03-12)** `stopGraceful()` 이 동일 root 를 fixing 중 → CAL-008 per upstream-dup-check reject (FIND 생성 금지).
- [x] **partial-rollback-on-failure (start path)**: `runMissedJobs` 가 throw 시 `armTimer` 호출 안 됨 (ops.ts:119). **PR #68112 (OPEN, 2026-04-17)** 가 fixing 중 → CAL-008 reject.
- [x] **duplicate-register-on-reentry**: `start()` 재호출 시 `armTimer` 가 내부적으로 `if(state.timer) clearTimeout` 선행(619-622) 후 재장착. 대칭 확보됨 → FIND 없음.
- [x] **storeLocks 재사용**: 모듈-scope Map, delete 없음. 키 도메인 constant (storePath) → growth rate 0 → cron-memory 에서 이미 abandon 한 축. 새 FIND 없음.

#### R-3 대응 경로 Grep 결과

- `rg -n "markCronJobActive" src/` → production 2건 (timer.ts:746, 1344). runStartupCatchupCandidate·prepareManualRun 에 부재 확인.
- `rg -n "clearCronJobActive" src/` → production 1건 (timer.ts:586, applyOutcomeToStoredJob) + 1건 (timer.ts:1374, executeJob). **manual run 경로에 부재** 확인.
- `rg -n "isCronJobActive" src/` → `src/tasks/task-registry.maintenance.ts:127` (caller, allowed_paths 밖이라 참조만), cron 본체에서 호출 없음.
- `git log upstream/main -- src/cron/active-jobs.ts` → 1건 (`7d1575b5df`, #60310, 2026-04-04). `git show 7d1575b5df -- src/cron/service/ops.ts` → 해당 파일 수정 없음 → manual run gap 이 의도된 공백이 아니라 scope 부족.

#### Upstream open PRs 관련 영역 (CAL-008 gate 통과 후보)

| PR | 주제 | lifecycle 축 | 본 세션 FIND 와 관계 |
|---|---|---|---|
| #43832 (OPEN 2026-03-12) | stopGraceful drain in-flight ops on hot reload | async-stop-not-awaited | 본 셀의 동일 root → FIND 억제 |
| #68112 (OPEN 2026-04-17) | guard runMissedJobs throw → armTimer | partial-rollback-on-failure (start) | 본 셀의 동일 root → FIND 억제 |
| #68168 (CLOSED 2026-04-17) | active-jobs singleton unit test 커버리지 | 단순 test, asymmetry 수정 아님 | 영향 없음 |
| #60566 (CLOSED 2026-04-03) | cron/cli no-childSessionKey → missing reconcile | 증상 기반 patch (symptom over cause) | 본 FIND 의 gap 을 덮으려다 미채택. FIND 의 근본 원인 유효성 역설적 지지 |

#### Related open issue

- **#68157 (OPEN 2026-04-23)** — "Cron isolated agentTurn: already-running survives restart, run history always empty": 사용자가 heartbeat-dispatch (isolated, 30분 주기) 의 `openclaw cron runs` history 가 empty 인데 실제 script 는 로그 확인되는 증상 보고. 본 FIND-001 (startup) + FIND-002 (manual) 의 정확한 manifestation. upstream 에 아직 fix 없음.

#### R-5 집계 (cleanup 경로 분류표)

| 경로 | mark | clear | execution condition |
|---|---|---|---|
| `runDueJob` (timer.ts:746) | YES (746) | via applyOutcomeToStoredJob (586) | unconditional |
| `executeJob` (timer.ts:1344) | YES (1344) | YES (1374) | unconditional |
| `runStartupCatchupCandidate` (timer.ts:1048) | **부재** | via applyOutcomeToStoredJob (586, no-op) | — (FIND-001) |
| `prepareManualRun`/`finishPreparedManualRun` (ops.ts:577/611) | **부재** | **부재** | — (FIND-002) |

#### 확인 못 한 영역 (self-critique)

- `src/tasks/task-registry.maintenance.ts` 의 `TASK_RECONCILE_GRACE_MS` 실제 값, sweep 주기, `lost` 전이 조건: allowed_paths 밖 → R-2 로 파일 Read 안 함. 재현 시 필수 확인 항목.
- `enqueueRun` (ops.ts:697-732) 의 command lane queue 처리 내 별도 mark 로직 존재 여부: `enqueueCommandInLane` 은 `src/process/command-queue.ts` 로 R-2 외부.
- CronService 가 gateway reload 시 단일 인스턴스인지 매번 재생성인지: `src/gateway/server-reload-handlers.ts` 일부 라인만 식별 (cronState.cron.stop at 137) — 상세 flow 는 R-2 로 미확인.
- Stagger (src/cron/stagger.ts) 의 생애주기 영향: missed job catch-up 의 deferred 경로 (applyStartupCatchupOutcomes 1101-1112) 에서 mark/clear 부재 여부. deferred 는 재장착만 되고 실행은 이후 timer tick 이 담당 → runDueJob 경로로 재합류하므로 mark 확보. gap 없음.

#### upstream 최근 cron fix (R-8, `git log upstream/main --since="6 weeks ago" -- src/cron/` 중 lifecycle 축 관련)

| commit | 요약 |
|---|---|
| `7d1575b5df` (#60310, 2026-04-04) | activeJobIds singleton 도입. mark 를 runDueJob/executeJob 두 경로에 추가. startup catchup / manual run 경로는 **누락** (본 셀 FIND 의 직접 원인) |
| `7a16e14301` (#60495, 2026-04-04) | interrupted recurring jobs 의 restart 복귀 (start()의 skipJobIds 에서 kind==="at" 만 제외로 분기 세분화) |
| `0787266637` (#68886) | detached task lifecycle runtime 분리 — createRunningTaskRun 의 export path 변경 (본 셀 grep 경로에 직접 영향 없음) |
| `c602824215` (#66083) / `190a4b4869` (#66113) | unresolved nextRunAtMs refire loop 방지 (lifecycle 이 아니라 scheduling correctness) |
| `905e56d191` (#63507) | zero nextRunAtMs invalid 처리 (동일) |

이들 upstream fix 중 **markCronJobActive asymmetry 를 직접 해소하는 커밋은 없음**.

#### 결론

cron-lifecycle 셀 2 FIND (P2/P2, 둘 다 `lifecycle-gap`). 핵심은 upstream 의 부분 구현된 activeJobIds 대칭 계약의 보완. cron-concurrency (CAS race) / cron-memory (Map 성장) / cron-error-boundary (예외 격리) 가 **같은 파일들을** 이미 감사했음에도 이 축은 lifecycle 페르소나의 "init-without-init" 렌즈로만 보이는 cross-module 계약 gap. false-positive 비용 고려해 upstream open PR (#43832, #68112) 영역은 의도적으로 abandon.
