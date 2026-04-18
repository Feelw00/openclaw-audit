---
id: FIND-cron-concurrency-002
cell: cron-concurrency
title: stuck runningAtMs 복구가 고정 2시간 상수에만 의존, heartbeat/liveness 갱신 없음
file: src/cron/service/jobs.ts
line_range: 399-409
evidence: "```ts\n  const runningAt = job.state.runningAtMs;\n  if (typeof runningAt\
  \ === \"number\" && nowMs - runningAt > STUCK_RUN_MS) {\n    state.deps.log.warn(\n\
  \      { jobId: job.id, runningAtMs: runningAt },\n      \"cron: clearing stuck\
  \ running marker\",\n    );\n    job.state.runningAtMs = undefined;\n    changed\
  \ = true;\n  }\n\n  return { changed, skip: false };\n```\n"
symptom_type: concurrency-race
problem: 'cron job 이 실행 중 프로세스가 SIGKILL / OOM / 정전 등으로 비정상 종료되면

  store 에 `runningAtMs` 가 세팅된 채로 남는다. 재기동 후 해당 job 은

  `isRunnableJob` (timer.ts:870) 과 `collectRunnableJobs` 에서 모두 "이미 실행 중" 으로

  간주되어 스케줄에서 제외된다. 복구는 오직 `normalizeJobTickState` 의 `nowMs - runningAt >

  STUCK_RUN_MS`(2시간) 비교 한 줄에만 의존한다. heartbeat/keepalive 로 runningAtMs 를

  갱신하거나, 프로세스 PID / boot ID / owner token 을 검증하는 경로가 없다.

  → 최악의 경우 최대 2시간 동안 job 이 "유령 실행 중" 상태로 미실행.

  '
mechanism: "1. T0: job 시작, `runningAtMs = T0` 로 persist (timer.ts:724 또는 timer.ts:1319).\n\
  2. T0+30s: 프로세스 kill -9 (OOM, host reboot, launchd stop --timeout 등).\n   → finally\
  \ 블록(timer.ts:405-412)이 실행되지 않아 runningAtMs 가 store 에 잔존.\n3. T0+60s: supervisor\
  \ 가 새 프로세스 기동 → `ensureLoaded` → 모든 job 의 runningAtMs 그대로.\n4. 새 프로세스의 onTimer →\
  \ collectRunnableJobs → runningAtMs != undefined 인 job 은\n   `isRunnableJob`(timer.ts:870-872)\
  \ 에서 즉시 false 반환 → skip.\n5. `planStartupCatchup` 도 동일한 `collectRunnableJobs` 를\
  \ 사용하므로(timer.ts:986) 놓친 job\n   catch-up 에서도 제외.\n6. 2시간(STUCK_RUN_MS) 경과 후 다음\
  \ maintenance tick 의 `normalizeJobTickState` 가 stuck 마커를\n   clear → 그때서야 복구. 그\
  \ 사이 1분 cron 은 120회 누락, 1시간 cron 은 2회 누락.\n"
root_cause_chain:
- why: 왜 runningAtMs 를 heartbeat 로 갱신하지 않는가?
  because: runningAtMs 는 job 시작 시점(startedAt) 이 한 번만 세팅되고 실행 중 업데이트되지 않는다. executeJobCoreWithTimeout
    호출 중간에 runningAtMs 를 새 값으로 persist 하는 경로가 없다
  evidence_ref: src/cron/service/timer.ts:1319
- why: 왜 프로세스 소유권(PID/boot ID/runner token)을 검증하지 않는가?
  because: CronJobState 스키마에 runningAtMs 외에 owner 를 식별할 필드가 없고, normalizeJobTickState
    도 시간 차이만 비교한다
  evidence_ref: src/cron/service/jobs.ts:399-407
- why: 왜 STUCK_RUN_MS 가 2시간처럼 길게 잡혔는가?
  because: 긴 inference(agentTurn) job 을 오탐으로 clear 하지 않기 위해 넉넉히 잡았다. 동시에 heartbeat
    이 없으므로 이 값이 곧 '프로세스 사망 감지 지연 상한' 이 된다 — 둘을 동시에 만족시킬 수 없다
  evidence_ref: src/cron/service/jobs.ts:38
- why: 왜 기존 테스트가 이 gap 을 문제 삼지 않는가?
  because: service.issue-13992-regression.test.ts / service.restart-catchup.test.ts
    의 stuck-recovery 케이스는 `staleRunningAt = now - (STUCK_RUN_MS + buffer)` 처럼 이미 STUCK_RUN_MS
    를 넘긴 상태를 주입하고 clear 되는지만 검증. 2시간 이내에 stuck 인 경우의 미실행은 기대 동작으로 취급
  evidence_ref: src/cron/service.issue-13992-regression.test.ts:143-151
impact_hypothesis: wrong-output
impact_detail: '정성: "1분마다 메시지 전송" 같은 job 이 서버 crash 직후 재기동했을 때 최대 ~2시간(120회)

  누락. "매 정시에 실행" 같은 매시간 cron 도 1~2회 누락.

  정량: 1분 cron 의 경우 worst-case 119 miss (120분 / 1분 - 1 catch-up).

  재현: runningAtMs 를 `Date.now() - 60_000`(1분 전) 로 수동 주입 후 재기동 → 해당 job 은

  향후 2시간 동안 실행되지 않음 (collectRunnableJobs 필터링).

  노출: OOM/SIGKILL/host reboot/`openclaw` 강제 종료 후 재기동 — 실제 운영에서 드물지 않음.

  '
severity: P1
counter_evidence:
  path: src/cron/service/jobs.ts
  line: '38'
  reason: "R-3 Grep 결과:\n(1) `rg -n \"heartbeat|liveness|stuck.*recover\" src/cron/`\
    \ →\n    \"heartbeat\" 매치 다수 있으나 전부 **cron→main-agent heartbeat** 의미 (wakeMode,\n\
    \    runHeartbeatOnce 등). runningAtMs 를 주기적으로 touch 하는 self-heartbeat 경로\n   \
    \ 0건. \"liveness\" / \"stuck.*recover\" 매치 전혀 없음.\n(2) `rg -n \"Redisson|redlock|Redis.*lock|distributed.*lock|file.*lock\"\
    \ src/` →\n    cron 코드에 process ownership 검증용 lease/token 없음.\n(3) `rg -n \"clearTimeout\\\
    (|AbortController|signal\\.abort\" src/cron/` →\n    timer/abort 경로 존재하지만 crash\
    \ 시에는 finally 자체가 호출되지 않음.\n결론: 2시간 상수 외에 stuck 복구 메커니즘 없다는 반증 확인.\n"
status: discovered
discovered_by: cron-reliability-auditor
discovered_at: '2026-04-18'
---
# stuck runningAtMs 복구가 고정 2시간 상수에만 의존, heartbeat/liveness 갱신 없음

## 문제

cron 서비스는 job 이 실행 중임을 표현하기 위해 `CronJobState.runningAtMs`
타임스탬프를 설정한다(job 시작 시 1회). `isRunnableJob` 은 이 값이 존재하면 해당 job 을
schedule 에서 제외한다. 정상 종료 경로(timer.ts:405-412)는 finally 에서 이를 clear 하지만,
프로세스가 SIGKILL / OOM / 갑작스런 장비 재부팅으로 비정상 종료되면 store 파일에 값이 영구 잔존한다.

복구 로직은 `normalizeJobTickState`(jobs.ts:399-407) 한 지점밖에 없고 조건은
`nowMs - runningAt > STUCK_RUN_MS(2시간)`. runningAtMs 를 주기적으로 갱신하는 heartbeat 도,
PID/boot-ID 로 프로세스 소유권을 확인하는 validator 도 없다. 결과적으로 비정상 종료 후
최대 2시간 동안 해당 job 은 "실행 중" 으로 잘못 간주되어 미실행.

## 발현 메커니즘

```
T0        : executeJob → job.state.runningAtMs = T0 (persist)
T0+30s    : 프로세스 SIGKILL (OOM / kill -9 / host reboot)
            ─ finally 블록(timer.ts:405-412) 실행 안 됨
            ─ runningAtMs=T0 가 store.json 에 잔존
T0+60s    : supervisor 가 새 프로세스 기동
            ensureLoaded → 모든 job 의 runningAtMs 를 파일에서 복구
T0+60s 이후 매 onTimer():
            collectRunnableJobs →
              isRunnableJob(timer.ts:870) → runningAtMs != undefined → false
            → due 목록에서 제외, skipped (hot-loop 도 회피되지만 실행 자체가 안 됨)
            planStartupCatchup 도 동일 필터 사용 → catch-up 에서도 제외
T0+2h+Δ   : 다음 onTimer 의 normalizeJobTickState
            → nowMs - T0 > STUCK_RUN_MS → runningAtMs = undefined 로 clear
T0+2h+Δ+ε: 비로소 다음 tick 에서 정상 실행 재개
```

1분 주기 cron 의 경우 T0~T0+2h 구간에서 최대 120회 누락. `*/15 * * * *` 도 7~8회 누락.

## 근본 원인 분석

1. **runningAtMs 가 1회성 타임스탬프** — `timer.ts:741` (onTimer 경로) 와 `timer.ts:1319`
   (수동 run 경로) 가 `job.state.runningAtMs = startedAt` 한 번만 대입. 실행 중 update 없음.
2. **Owner identity 부재** — CronJobState 에 PID/boot-ID/runner-token 같은 소유권 필드가 없어
   다른 프로세스가 "이 runningAtMs 는 내가 안 만든 것이다" 를 판단할 수 없다. 재기동 직후
   runningAtMs 를 모두 clear 해도 되지만 — 그러면 실제로 다른 인스턴스가 실행 중인 job 을
   중복 실행하게 되므로 안전하지 않음. 멀티 인스턴스 가정(→ FIND-cron-concurrency-001) 과
   얽혀 있다.
3. **상수 하나에 두 요구사항 충돌** — STUCK_RUN_MS 는 (A) 긴 agentTurn 을 오탐 clear 하지 않을 만큼
   길어야 하고 (B) crash 감지 지연을 최소화할 만큼 짧아야 한다. heartbeat 이 없는 한 두 요구를
   동시에 만족 불가. 현재 2시간 = (A) 측 최악 케이스로 설계.
4. **테스트는 "2시간 경과 후 clear" 만 검증** — `service.issue-13992-regression.test.ts:143` 에서
   `staleRunningAt` 을 staleTime 으로 준 뒤 clear 되는지만 확인. "2시간 이내 비정상 종료된 job 은
   미실행 상태로 놔두는 것이 의도된 동작" 처럼 regression 이 굳어졌다.

## 영향

- **가장 가능성 높은 결과**: wrong-output — 매분/매시간 cron 이 서버 crash 후 최대 2시간 동안
  skip. 사용자는 "cron 이 실행돼야 하는데 안 됐다" 증상만 관측, 로그에는 별다른 error 없이 단순히
  해당 job 이 collectRunnableJobs 결과에서 빠져 있음.
- **정량**: worst-case miss = `STUCK_RUN_MS / scheduleIntervalMs` = 120분 / 1분 = 120회 (1분 cron).
  15분 cron = 8회. 매시간 cron = 2회.
- **재현 시나리오**:
  1. `store.json` 의 특정 job 에 `state.runningAtMs = <현재-30초>` 로 수동 편집.
  2. gateway 기동 → `openclaw cron list` 로 runningAt 확인.
  3. 향후 2시간 동안 해당 job 은 trigger 되지 않음 (로그에 "blocked by runningAtMs" 도 없음).
- **노출 빈도**: OOM kill 은 장기 운영 중 흔하고, host reboot / launchd force-stop 도 일상적.

## 반증 탐색

- **Heartbeat 경로 존재 여부**: R-3 Grep 3번 결과 — cron 내 "heartbeat" 매치는 모두 main-agent
  wake/heartbeat 의미이며 cron job 자체의 keepalive 가 아님. `state.deps.runHeartbeatOnce` 는
  main 세션을 깨우는 호출이지 runningAtMs 갱신과 무관.
- **다른 fast-path recovery**: `src/cron/service/ops.ts:113-118` 에서 특정 작업(ops.regression.test
  #17554 가 커버) 이 stuck runningAtMs 를 수동 clear 하는 경로가 있으나, 이는 사용자가
  `openclaw cron run <id>` 를 직접 호출한 "manual trigger" 흐름이다. 자동 실행 경로는 혜택 없음.
- **기존 테스트 커버리지**: `service.issue-13992-regression.test.ts:143-151` 는 STUCK_RUN_MS 초과
  후 clear 를 확인. 2시간 이내 stuck 상태에서 missed run 이 recover 되는지 검증하는 테스트 없음.
- **설정 override**: `STUCK_RUN_MS` 는 하드코딩 상수(`const STUCK_RUN_MS = 2 * 60 * 60 * 1000`,
  jobs.ts:38). cronConfig 또는 deps 로 override 할 수 없음.
- **주변 주석**: jobs.ts:400 의 warn 로그는 "cron: clearing stuck running marker" 로
  이미 이 시나리오를 인지하고 있음 — 단 inhibit 기간 자체를 2시간으로 받아들임.

## Self-check

### 내가 확실한 근거
- `STUCK_RUN_MS = 2 * 60 * 60 * 1000` 이 jobs.ts:38 에 하드코딩 (Bash 로 확인).
- `isRunnableJob` 이 runningAtMs 존재 시 즉시 false 반환 (timer.ts:870-872).
- `collectRunnableJobs` 는 `planStartupCatchup` 에서도 동일하게 사용 → catch-up 에서도 제외
  (timer.ts:986-990).
- cron 디렉터리 전체에서 liveness / heartbeat 기반 stuck 복구 없음 (R-3 Grep 3번 0건).

### 내가 한 가정
- supervisor 가 재기동한 새 프로세스가 "직전 인스턴스가 죽었다" 는 신호 없이 단순 파일 읽기로
  runningAtMs 를 신뢰한다고 가정. 실제 배포에서 wrapper script 가 기동 전 store.json 을 sanitize
  한다면 이 FIND 의 심각도 감소.
- STUCK_RUN_MS 경계 근처에서 long-running agentTurn job 은 오탐 clear 되지 않는다고 가정 (2시간
  이내 종료 기준).

### 확인 안 한 것 중 영향 가능성
- ops.ts 의 stuck 복구 경로(113-118, 529-591) 가 자동 trigger 가능한 공개 API 에서도 호출되는지
  상세 확인 안 함. 만약 매 tick 마다 호출된다면 2시간 지연 문제는 완화됨. (초기 Grep 결과는
  manual-run/service-start 시점에만 호출되는 것으로 보임.)
- configuration 에 STUCK_RUN_MS override 가 있는지 cron cell 밖 설정 스키마 미확인.
