---
id: FIND-cron-concurrency-001
cell: cron-concurrency
title: locked() 는 프로세스-내 Map 기반 락으로 멀티 인스턴스 공유 store 에서 중복 실행 허용
file: src/cron/service/locked.ts
line_range: 1-22
evidence: "```ts\nimport type { CronServiceState } from \"./state.js\";\n\nconst storeLocks\
  \ = new Map<string, Promise<void>>();\n\nconst resolveChain = (promise: Promise<unknown>)\
  \ =>\n  promise.then(\n    () => undefined,\n    () => undefined,\n  );\n\nexport\
  \ async function locked<T>(state: CronServiceState, fn: () => Promise<T>): Promise<T>\
  \ {\n  const storePath = state.deps.storePath;\n  const storeOp = storeLocks.get(storePath)\
  \ ?? Promise.resolve();\n  const next = Promise.all([resolveChain(state.op), resolveChain(storeOp)]).then(fn);\n\
  \n  // Keep the chain alive even when the operation fails.\n  const keepAlive =\
  \ resolveChain(next);\n  state.op = keepAlive;\n  storeLocks.set(storePath, keepAlive);\n\
  \n  return (await next) as T;\n}\n```\n"
symptom_type: concurrency-race
problem: 'cron 서비스가 store(파일 기반 JSON) 를 직렬화 수정할 때 사용하는 locked() 가

  모듈-스코프 Map<string, Promise<void>>(storeLocks) 하나에만 의존한다.

  즉 동일 프로세스 내부의 async 체인은 직렬화되지만, 같은 storePath 를 공유하는

  다른 프로세스(두 번째 openclaw gateway 인스턴스, CLI `openclaw cron run`,

  보조 헬퍼 프로세스) 는 락을 전혀 모르고 독립적으로 ensureLoaded → persist 를 실행한다.

  → 같은 cron job 이 두 번 실행되거나, 한쪽 persist 가 다른 쪽 변경을 last-writer-wins 으로 덮어쓴다.

  '
mechanism: "1. 인스턴스 A 와 B 가 동일한 storePath(예: ~/.openclaw/cron/store.json) 로 기동.\n\
  2. 어떤 job 의 nextRunAtMs 가 now 보다 과거가 되어 두 인스턴스 모두 timer 가 점화.\n3. A: `onTimer` →\
  \ `locked(state, ...)` 진입 → `ensureLoaded({forceReload:true})` → due job 에\n   `runningAtMs\
  \ = now` 세팅 → `persist()` 저장.\n4. B: 거의 동시에 `onTimer` → `locked(...)` 진입 (B 의 storeLocks\
  \ 은 A 와 분리된 Map).\n   → A 의 persist 이전에 파일을 읽었다면 `runningAtMs` 가 아직 undefined →\
  \ due 로 판정 →\n   동일 job 실행.\n5. A 와 B 모두 `executeJobCoreWithTimeout` 호출 → 하위 heartbeat/systemEvent\
  \ 이중 전송 →\n   사용자는 같은 알림 2회 수신, 채널에 중복 메시지.\n6. B 의 persist 가 A 의 최종 결과(`lastStatus:\
  \ ok`) 를 덮어쓰거나 그 반대 →\n   run-log / 실패 카운터가 정합성을 잃음.\n"
root_cause_chain:
- why: 왜 locked() 가 프로세스-내 Map 만 쓰는가?
  because: storeLocks 가 파일 시스템 락이 아닌 모듈-스코프 변수로 선언돼 있어 프로세스 간 공유되지 않는다
  evidence_ref: src/cron/service/locked.ts:3
- why: 왜 파일 락 또는 분산 락으로 감싸지 않았는가?
  because: locked() 가 단일 gateway 프로세스 전제 하에 구현됐고 다중 인스턴스 배포를 가정하지 않는다. infra/file-lock.ts
    의 withFileLock 유틸이 이미 있지만 cron 경로에서는 호출하지 않는다
  evidence_ref: src/cron/service/locked.ts:11-22
- why: '왜 job claim 단계에서 원자적 조건부 upsert(예: CAS) 를 하지 않는가?'
  because: '`onTimer` 의 locked 블록 안에서 단순 in-memory 필드 대입(`job.state.runningAtMs =
    now`) 후 persist 만 하며, 읽기-수정-쓰기 사이클이 다른 프로세스의 동일 사이클에 대해 보호되지 않는다'
  evidence_ref: src/cron/service/timer.ts:722-727
- why: 왜 기존 테스트가 이 경우를 커버하지 않는가?
  because: cron 테스트 스위트는 단일 CronServiceState 인스턴스로 구동하며, 동일 storePath 를 공유하는 두 번째
    state 를 띄워 경합을 재현하는 케이스가 없다 (service.restart-catchup.test.ts 는 순차 재시작만 모사)
  evidence_ref: N/A — cron 테스트 파일 전수조사 결과 multi-instance race 시나리오 부재
impact_hypothesis: data-loss
impact_detail: '정성: 동일 storePath 를 두 프로세스가 참조하는 모든 배포(예: 사용자가 CLI 로 `openclaw cron
  run <id>`

  를 수동 실행한 상태에서 gateway 가 돌고 있을 때, 또는 supervisor 가 auto-restart 중 새 프로세스가

  구 프로세스 완전 종료 전에 기동되는 1~2 초 window) 에서 중복 실행 / store 덮어쓰기 가능.

  외부 heartbeat/메시지 전송을 포함하는 job 은 사용자에게 즉시 관측되는 duplicate delivery 증상.

  재현 난이도: 같은 storePath 로 두 인스턴스를 동시 기동하면 수 초 이내 재현 가능.

  '
severity: P1
counter_evidence:
  path: src/cron/service/locked.ts
  line: '3'
  reason: "R-3 Grep 결과:\n(1) `rg -n \"Redisson|redlock|Redis.*lock|distributed.*lock|file.*lock\"\
    \ src/` →\n    cron 경로에 분산 락 일체 없음. `src/infra/file-lock.ts` / `src/plugin-sdk/file-lock.ts`\
    \ 는\n    존재하지만 `src/cron/**` 에서 import 하는 호출지점 0건 (Grep 결과 cron 디렉터리 매치 없음).\n\
    \    `src/cron/session-reaper.ts:49` 주석이 \"session-store file lock\" 을 언급하지만 이는\n\
    \    세션 저장소 락이며 cron store 락이 아님.\n(2) `rg -n \"clearTimeout\\(|AbortController|signal\\\
    .abort\" src/cron/` →\n    타이머/abort 경로는 존재하지만 store 경합과 무관.\n(3) `rg -n \"heartbeat|liveness|stuck.*recover\"\
    \ src/cron/` →\n    \"heartbeat\" 는 cron→main-agent 통신 의미이고 분산 lock liveness 가\
    \ 아님.\n결론: locked() 가 프로세스 간 보호를 제공한다는 반증 증거 없음.\n"
status: discovered
discovered_by: cron-reliability-auditor
discovered_at: '2026-04-18'
---
# locked() 는 프로세스-내 Map 기반 락으로 멀티 인스턴스 공유 store 에서 중복 실행 허용

## 문제

`src/cron/service/locked.ts` 가 노출하는 `locked(state, fn)` 헬퍼는 cron store
(파일 기반 JSON) 의 read-modify-write 경로를 직렬화하기 위한 유일한 상호배제 수단이다.
그러나 구현은 **모듈-스코프** `Map<string, Promise<void>>` (`storeLocks`) 하나와
`state.op` 프로미스 체인만으로 작동한다. 둘 다 프로세스 내부 자료구조이므로 같은
`storePath` 파일을 공유하는 두 프로세스 사이에서는 어떠한 직렬화도 이루어지지 않는다.

## 발현 메커니즘

```
프로세스 A                                프로세스 B
---------                                ---------
onTimer() 진입                            (정지)
  locked(state, async () => {
    ensureLoaded({forceReload:true})
      ← 파일 read: job.runningAtMs=undef
    due = collectRunnableJobs(...)
                                          onTimer() 진입
                                            locked(state, async () => {
                                              ensureLoaded({forceReload:true})
                                                ← 파일 read: 여전히 undef
                                              due = collectRunnableJobs(...)
    due.forEach(j => runningAtMs=now)
    persist()  ← 파일 write: runningAtMs=nowA
                                              due.forEach(j => runningAtMs=now)
                                              persist()  ← 덮어쓰기: runningAtMs=nowB
  })
                                            })

A: executeJobCoreWithTimeout(job)         B: executeJobCoreWithTimeout(job)
   → heartbeat/메시지 1회                    → heartbeat/메시지 1회 (중복)
```

두 프로세스의 `storeLocks` Map 은 서로 다른 V8 힙에 있으므로 상호가시성 0.
`state.op` 도 각 프로세스 고유의 `CronServiceState` 인스턴스에 매여 있다.

## 근본 원인 분석

1. **Lock primitive 자체가 in-process** — `storeLocks` 는 `Map` 이며 파일/DB/Redis 기반
   어떤 외부 공유 상태도 접근하지 않는다 (line 3). `state.op` 도 동일.
2. **파일 락 유틸이 있는데 사용하지 않음** — `src/plugin-sdk/file-lock.ts` 가 `.lock` sidecar
   기반 재진입 락을 제공하지만 `src/cron/**` 전체에서 이를 import 하는 지점이 없다.
   session-reaper 주석은 "session-store file lock" 을 언급하지만 그 락은 `agents/session-write-lock`
   에 관한 것이고 cron store 와 다른 파일이다.
3. **Claim 이 원자적 CAS 가 아님** — `onTimer` (`timer.ts:722-727`) 는 "runningAtMs 가 undef 인
   due job 을 필터 → 메모리에서 runningAtMs 대입 → persist" 순서이며, 두 프로세스가 동일한
   read snapshot 을 본 뒤 각자 persist 하면 두 번째 persist 가 last-writer-wins 로 첫 번째 결과를
   덮는다. 조건부 update (optimistic lock: 읽은 version 과 현재 version 비교) 가 없다.
4. **멀티 인스턴스를 가정하지 않는 설계** — 클래스 주석/README 에 "single-instance only" 명시는
   없으나 코드상 명백히 단일 프로세스 전제. 배포 문서에서 이 제약을 강제하는 체크도 관측되지 않음.

## 영향

- **가장 가능성 높은 결과**: data-loss — run-log, lastStatus, nextRunAtMs 가 경쟁적 persist 로
  덮어써짐. 사용자는 `openclaw cron list` 에서 부정확한 lastRunAtMs 를 보게 됨.
- **사용자 관측 증상**: 동일 cron job 의 메시지/heartbeat 가 2번 이상 전송. Signal/Telegram/Discord
  채널에 중복 알림. `cron:<jobId>` contextKey 로 enqueueSystemEvent 가 두 번 호출되면 main agent
  lane 에서 같은 프롬프트 2회 실행 가능.
- **재현 시나리오**:
  1. 같은 `~/.openclaw/cron/store.json` 을 참조하는 두 gateway 프로세스 기동.
  2. 1초 단위 cron(`* * * * * *` 또는 유사) 등록.
  3. 수 초 이내에 heartbeat 중복 혹은 runningAtMs 덮어쓰기 로그 관측.
- **노출 경로**: supervisor/launchd auto-restart race window(~100-2000ms), 사용자가 CLI 로
  `openclaw cron run` 을 수동 실행 중 gateway 가 같은 job 을 picking 하는 경우.

## 반증 탐색

- **외부 락 존재 여부**: R-3 Grep 1번 결과 — cron 경로에 `Redisson/redlock/Redis.*lock/
  distributed.*lock/file.*lock` 매치 0건. `src/plugin-sdk/file-lock.ts` 는 존재하나 cron 코드가
  import 하지 않음.
- **주변 코드 주석**: `locked.ts` 의 주석은 "Keep the chain alive even when the operation fails"
  만 언급하고 멀티 프로세스 제약은 기술 없음.
- **기존 테스트 커버리지**: `service.restart-catchup.test.ts`, `service.prevents-duplicate-timers.test.ts`,
  `service.issue-13992-regression.test.ts` 등 regression 테스트는 모두 단일
  `CronServiceState` 로 동작. 같은 storePath 로 두 state 를 띄우는 케이스 없음.
- **배포 토폴로지 명시**: repo 루트 docs 를 cron cell allowed_paths 밖이므로 직접 확인 불가. 단
  session-reaper 주석이 session-store 용 file lock 을 언급한다는 것은 "파일 공유 가능성" 을 repo
  가 인지하고 있음을 시사 (즉 cron 도 공유 시나리오를 배제하지 않음).
- **설정/feature flag**: `src/config/schema.base.generated.ts` (cell 밖) 에 cron 전용 single-instance
  강제 flag 는 존재 여부 미확인 (스코프 밖).

## Self-check

### 내가 확실한 근거
- `storeLocks` 가 모듈-스코프 `Map` 이며 외부 저장소와 동기화하지 않는다 (line 3).
- `src/cron/**` 전체에서 `file-lock` 이나 `withFileLock` import 0건 (R-3 Grep).
- `onTimer` 의 claim 코드가 조건부 upsert 가 아닌 단순 대입 후 persist 다 (timer.ts:722-727).

### 내가 한 가정
- 프로덕션 배포에서 두 프로세스가 동일 storePath 를 공유할 수 있다고 가정 (supervisor race,
  수동 CLI, 개발자 실수 등). repo 구조상 명시적 single-instance 락이 있다면 이 가정은 깨진다.
- persist() 가 파일을 atomic 교체(temp + rename) 로 쓰는지 이 FIND 범위에서 확인 안 함 —
  atomic 이어도 read-modify-write 사이 race 는 그대로 존재.

### 확인 안 한 것 중 영향 가능성
- allowed_paths 밖의 설정(`~/.openclaw/config.json` 의 cron 섹션) 이 multi-instance 를 막는
  check 를 할 수도 있음. 스코프 제약으로 미확인.
- launchd/systemd 단위 파일이 PID 기반 single-instance 를 강제할 가능성 — 이는 배포 정책이며
  코드 방어는 아님.
