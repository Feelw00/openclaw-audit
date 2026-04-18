---
candidate_id: CAND-003
type: single
finding_ids:
  - FIND-cron-concurrency-002
cluster_rationale: "FIND-cron-concurrency-002 는 runningAtMs 가 이미 세팅된 이후의 stale-marker 복구가 `nowMs - runningAt > STUCK_RUN_MS(2h)` 단일 상수 비교에만 의존하고 heartbeat/keepalive/owner-identity(PID/boot-ID/runner-token) 가 없는 것이 문제. 근접 FIND (CAND-002 의 001/003) 는 'claim 연산의 원자성' 문제인 반면 본 FIND 는 'claim 이후 liveness 와 소유권 검증' 문제로 축이 다름. root_cause_chain[0].because=\"runningAtMs 는 job 시작 시점(startedAt) 이 한 번만 세팅되고 실행 중 업데이트되지 않는다\"; root_cause_chain[1].because=\"CronJobState 스키마에 runningAtMs 외에 owner 를 식별할 필드가 없고, normalizeJobTickState 도 시간 차이만 비교한다\"; root_cause_chain[2].because=\"긴 inference(agentTurn) job 을 오탐으로 clear 하지 않기 위해 넉넉히 잡았다. 동시에 heartbeat 이 없으므로 이 값이 곧 '프로세스 사망 감지 지연 상한' 이 된다 — 둘을 동시에 만족시킬 수 없다\". 공통 원인 epic 없음 — single."
proposed_title: "cron stuck runningAtMs 복구가 2h 상수에만 의존, heartbeat/ownership 부재"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-18
---

# cron stuck runningAtMs 복구가 2h 상수에만 의존, heartbeat/ownership 부재

## 공통 패턴

cron job 의 실행 상태는 `CronJobState.runningAtMs` 타임스탬프 한 필드로 표현되며, job 시작
시점에 1회 세팅된 뒤 종료 시 clear 된다. SIGKILL / OOM / 호스트 재부팅 등으로 finally 블록
(timer.ts:405-412) 이 실행되지 않으면 이 필드가 store 에 잔존한다.

복구 경로는 `normalizeJobTickState`(jobs.ts:399-407) 한 지점이며 조건은 단일 상수 비교
`nowMs - runningAt > STUCK_RUN_MS (2 시간)` 뿐이다. 즉:

- 실행 중 runningAtMs 를 주기적으로 touch 하는 heartbeat/keepalive 경로 없음.
- job state 에 PID/boot-ID/runner-token 등 프로세스 소유권 식별자 없음 — 다른(새) 프로세스가
  "이 marker 는 죽은 인스턴스가 남긴 것" 인지 판단할 기준이 없음.
- STUCK_RUN_MS 상수 하나가 (A) 긴 agentTurn 을 오탐 clear 하지 않을 만큼 길어야 한다는 요구와
  (B) crash 감지 지연을 최소화해야 한다는 요구를 동시에 만족해야 하는데, heartbeat 없이는
  두 요구가 본질적으로 충돌한다. 현재 2h 는 (A) 쪽을 택한 결과.

결과: 비정상 종료 직후 최대 2시간 동안 해당 job 이 "실행 중" 으로 잘못 간주되어 스케줄에서
제외된다. 1분 cron 은 worst-case 120회, 15분 cron 은 8회, 매시간 cron 은 2회 누락 가능.

### 근거 인용 (root_cause_chain 에서 직접)

- `root_cause_chain[0].because`: "runningAtMs 는 job 시작 시점(startedAt) 이 한 번만 세팅되고
  실행 중 업데이트되지 않는다. executeJobCoreWithTimeout 호출 중간에 runningAtMs 를 새 값으로
  persist 하는 경로가 없다" (src/cron/service/timer.ts:1319)
- `root_cause_chain[1].because`: "CronJobState 스키마에 runningAtMs 외에 owner 를 식별할 필드가
  없고, normalizeJobTickState 도 시간 차이만 비교한다" (src/cron/service/jobs.ts:399-407)
- `root_cause_chain[2].because`: "긴 inference(agentTurn) job 을 오탐으로 clear 하지 않기 위해
  넉넉히 잡았다. 동시에 heartbeat 이 없으므로 이 값이 곧 '프로세스 사망 감지 지연 상한' 이 된다
  — 둘을 동시에 만족시킬 수 없다" (src/cron/service/jobs.ts:38)

반증 탐색 결과(domain-notes/cron.md 의 R-3 Grep 3번): cron 디렉터리 내 liveness / heartbeat
기반 stuck 복구 경로 0건 확인.

## 관련 FIND

- **FIND-cron-concurrency-002** (P1): `stuck runningAtMs 복구가 고정 2시간 상수에만 의존,
  heartbeat/liveness 갱신 없음` (src/cron/service/jobs.ts:399-409).

## 인접 CAND 와의 구분

- **CAND-002 (FIND-001/003 epic)** 는 "runningAtMs 를 세팅하는 claim 연산이 원자적 CAS 가 아님"
  이 공통 원인. 본 CAND-003 은 "세팅된 runningAtMs 의 liveness/소유권 검증 부재" 가 원인.
  세팅 시점 vs 세팅 이후, 서로 다른 축이므로 별개 CAND 로 분리.
