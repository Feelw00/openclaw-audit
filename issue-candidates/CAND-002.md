---
candidate_id: CAND-002
type: single
finding_ids:
  - FIND-cron-concurrency-001
cluster_rationale: "원래 FIND-001 + FIND-003 epic 이었으나 gatekeeper 가 FIND-003 을 반증 — prepareManualRun(ops.ts:587-590) 의 CAS guard 로 intra-process race 차단됨. 남은 FIND-001 은 inter-process (멀티 인스턴스 동일 storePath) claim race: locked.ts 의 storeLocks 는 모듈-스코프 Map 으로 프로세스 내부만 보호. 배포 토폴로지가 multi-instance 를 지원/권장하는지는 openclaw 상위 문서 확인 필요 (openclaw 가 단일 gateway 프로세스 전제일 가능성도 있음)."
proposed_title: "cron storeLocks in-memory-only: 멀티 인스턴스 동일 storePath 배포 시 중복 claim"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-18
revised_at: 2026-04-18
revision_note: "FIND-003 gatekeeper 반증 후 제외, single 로 축소"
---

# cron runningAtMs claim 이 원자적 CAS 가 아니어서 inter/intra-process 중복 실행

## 공통 패턴

cron 서비스는 job 이 실행 중임을 `CronJobState.runningAtMs` 타임스탬프 한 필드로만 표현하고,
그 세팅을 "파일에서 현재 값 읽기 → 메모리에서 필드 대입 → 전체 store persist" 의 비조건적
read-modify-write 순서로 수행한다. 원자적 조건부 교체(CAS: "runningAtMs 가 undefined 인 경우에만
now 로 세팅") 나 버전 기반 optimistic lock 이 없다.

이 설계 결함이 두 서로 다른 동시성 경계에서 중복 실행 증상으로 나타난다:

1. **FIND-001 (프로세스 경계)**: `locked()` 가 모듈-스코프 `Map<string, Promise<void>>` 기반의
   in-process 직렬화만 제공. 동일 `storePath` 파일을 공유하는 두 openclaw 인스턴스(supervisor
   auto-restart race, 수동 CLI + gateway 동시 실행 등) 는 서로의 락을 보지 못해 같은 job 을
   병렬로 claim 한다.

2. **FIND-003 (코루틴 경계, 단일 프로세스)**: `executeJob`(timer.ts:1309) 이 `locked()` 블록
   바깥에서 `runningAtMs = startedAt` 을 조건 없이 대입한다. onTimer 가 locked 안에서 막 claim
   한 fresh marker 가 있어도 manual `run` 커맨드(또는 `runDueJobs`) 가 진입하면 가드 없이 그
   marker 를 덮어쓰고 병렬 실행을 시작한다.

두 경로 모두에서 "이미 누군가 이 job 의 runningAtMs 를 설정했는가?" 를 검사하는 조건문이 없다.
해결의 근본 축은 "runningAtMs 대입을 원자적 조건부 연산으로 바꾸는 것" 으로 공통이다(구체적
해결 수단은 solution 단계에서 결정).

### 근거 인용 (각 FIND root_cause_chain 에서 직접)

**FIND-cron-concurrency-001** (multi-instance race):
- `root_cause_chain[0].because`: "storeLocks 가 파일 시스템 락이 아닌 모듈-스코프 변수로 선언돼
  있어 프로세스 간 공유되지 않는다" (src/cron/service/locked.ts:3)
- `root_cause_chain[2].because`: "onTimer 의 locked 블록 안에서 단순 in-memory 필드 대입(`job.state.runningAtMs = now`)
  후 persist 만 하며, 읽기-수정-쓰기 사이클이 다른 프로세스의 동일 사이클에 대해 보호되지 않는다"
  (src/cron/service/timer.ts:722-727)

**FIND-cron-concurrency-003** (intra-process timer × manual run race):
- `root_cause_chain[0].because`: "executeJob 자체는 긴 agentTurn 을 포함할 수 있어 locked 로
  감싸면 onTimer 가 그 동안 blocking. 실행 중에는 store 접근을 하지 않으므로 의도적으로 lock
  밖에서 돌리는 구조" (src/cron/service/timer.ts:1309-1333)
- `root_cause_chain[1].because`: "함수가 `forced: boolean` 옵션을 받지만 내부적으로 사용하지
  않고(변수명 `_opts`), 조건 분기가 없다. onTimer 가 이미 claim 한 상태인지 확인하는 guard 가
  없음" (src/cron/service/timer.ts:1313-1319)

FIND-003 이 이미 `cross_refs: [FIND-cron-concurrency-001]` 로 선언되어 있어, 원작성자도 두 이슈가
같은 축 위에 있음을 인지하고 있었다.

## 관련 FIND

- **FIND-cron-concurrency-001** (P1): `locked()` 가 프로세스-내 Map 기반 락으로 동일 storePath
  공유 시 멀티 인스턴스 중복 실행 허용 (src/cron/service/locked.ts:1-22).
- **FIND-cron-concurrency-003** (P2): `executeJob` 이 runningAtMs 선점 검사 없이 바로 덮어써
  타이머와 수동 run 동시 실행 가능 (src/cron/service/timer.ts:1309-1351).

## 제외된 FIND

- **FIND-cron-concurrency-002** 는 같은 cron-concurrency cell 이지만 원인 축이 다르다. 002 는
  "이미 세팅된 stale runningAtMs 를 복구하는 경로가 heartbeat/ownership 없이 2h 상수에만 의존"
  — 즉 claim 이후의 liveness 문제이며, claim 자체의 원자성 문제가 아니다. 별도 CAND-003 으로 분리.
