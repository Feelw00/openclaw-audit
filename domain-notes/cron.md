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
