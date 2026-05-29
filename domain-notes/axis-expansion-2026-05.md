# 감사 축 확장 정찰 (2026-05-29)

5-agent 병렬 정찰로 기존 4축(memory/lifecycle/concurrency/error-boundary,
전부 "자원/구조" lens)이 원리적으로 못 잡는 직교 축을 발굴/검증. upstream HEAD 61c538e2fc.

배경: 40 셀 전부 1차 감사 완료. CAND 44건 중 실효 8(18%) / abandon 35(80%).
병목은 후보 "양"이 아니라 "정밀도(머지 가능한 진짜 버그)". 구조 4축은 사실상 소진.

## 슬라이스별 STRONG 결과 매트릭스

| 슬라이스 (정찰 담당) | STRONG 축 |
|---|---|
| 1. 스케줄링/재시도/cron/process | 없음 (cron 은 core 최강 방어 영역: runningAtMs/locked/persist-before-exec/catch-up 멱등) |
| 2. 플러그인/SDK/MCP | **cross-store-consistency** |
| 3. agents 런타임/sessions/context-engine | **ordering-causality** (조건부) |
| 4. gateway/channels/routing/auto-reply | 없음 (idempotency/dedup/backpressure/ordering 전부 성숙) |
| 5. 영속/config/저장/로깅 | **data-integrity** |

idempotency 는 5/5 슬라이스에서 REJECT/WEAK (전부 기존 guard 완비 + 과거 CAND-007 abandon).

## 채택 축 (3) + 보류 축 (1)

### data-integrity  [STRONG]
- 정의: 영속 파일 쓰기의 atomic 규율 불균일 + 부분 multi-write 논리 불일치. crash/SIGKILL 시 truncate 손상 -> 복구 불가.
- 직교성: 4축의 "atomic" 은 in-memory single-thread interleaving 의미로만 다룸 (domain-notes 다수에 "JS 단일스레드 = atomic" 으로 race 폐기). 디스크 쓰기 원자성(temp+rename+fsync)은 어느 축도 안 봄. lock(mutual exclusion)은 crash 원자성 미보장 - 단일 writer 가 쓰다 죽어도 손상.
- 반증 기준선(패턴 존재 증명): `src/infra/exec-approvals.ts:710` (.tmp+wx+rename), `src/infra/session-cost-usage.ts:259` (temp+link), helper `@openclaw/fs-safe/atomic` `replaceFileAtomic`.
- seed 증거 (미방어 사이트):
  - `src/agents/sessions/auth-storage.ts:119,164` — auth.json 을 raw `writeFileSync(path,next,"utf-8")`. crash 시 truncate -> reload `JSON.parse` 실패 -> `loadError` -> `persistProviderChange` early-return(:287) -> **전 자격증명 silent lockout**, `getApiKey` 빈값. (critical)
  - `src/agents/sessions/settings-manager.ts:216` — 세션 settings 동일 raw write.
  - `src/channels/session.ts:45-55` — `recordSessionMetaFromInbound` 가 `void metaTask`(fire-and-forget), 뒤이어 `updateLastRoute` 는 await. meta 실패 + lastRoute 성공 시 라우트 포인터만 갱신된 부분쓰기 영속.
  - (저severity 동류) `src/config/io.ts:530,543`, `src/transcripts/store.ts:120,266` raw write.
- maintainer_fit: 높음 (reliability/stability + auth/startup 안정성). one-thing-per-PR: auth.json 단건 XS PR.
- reproducibility: 높음 (truncated JSON 주입 -> lockout 재현, fix 는 writeFileSync -> replaceFileAtomicSync 한 줄).
- 첫 셀 후보: data-integrity × (신규 도메인 sessions/agent-persistence 또는 기존 infra-process 확장).

### cross-store-consistency  [STRONG]
- 정의: 트랜잭션 경계 없이 갈라진 복수 영속 스토어 갱신 시 lock/CAS 가 한쪽에만 있어 동시 writer 가 다른 스토어를 lost-update/부분커밋.
- 직교성: concurrency 축 = in-memory race(락 부재/ordering). 이건 각 파일 쓰기는 atomic 이고 in-memory race 도 없는데, "두 파일을 한 트랜잭션으로 묶는" 경계 부재. data-integrity(단일 파일)와도 직교 — 각 파일은 멀쩡, 둘 사이 정합 붕괴.
- seed 증거:
  - `src/config/mutate.ts:149-186` — config 는 in-process 큐 + cross-process `withFileLock` + baseHash CAS + `ConfigMutationConflictError` 재시도로 동시 writer 완전 직렬화.
  - `src/cli/plugins-install-record-commit.ts:90-125` — install-records 인덱스를 **먼저** unlocked write(:99) 후 config commit(:105, lock 안). 인덱스 쓰기는 lock/CAS 미적용.
  - `src/plugins/installed-plugin-index-record-reader.ts:286-302` — sync 리더가 async 판(:279-281 generation 재검사)과 달리 cache invalidation race guard 부재.
  - 시나리오: 동시 `plugins install A`/`install B` 가 같은 base 인덱스 읽고 `{...prev,A}`/`{...prev,B}` 로 unlocked 덮어씀 -> 나중 writer 가 상대 plugin 의 install-record(npm integrity/shasum 등 파일시스템 복구불가 authoritative 데이터) 누락. config 두 commit 은 직렬 성공 -> config 엔 둘 다, 인덱스엔 하나만 남는 붕괴.
- maintainer_fit: plugin loading + reliability 직결.
- reproducibility: 중간 (다중 프로세스 동시성 타이밍 의존이나, sync reader 의 generation-guard 부재 비대칭은 결정적 시연 가능).
- PR pitch 방어 포인트: 다중 프로세스 동시 install 이 실 워크로드인지(데몬 단일 vs CLI 병행) 정당화 필요.
- 첫 셀 후보: cross-store-consistency × plugins (allowed_paths 에 src/config/mutate.ts + src/cli/plugins-* 포함 필요).

### ordering-causality  [STRONG 조건부]
- 정의: 이벤트/상태전이가 인과 순서를 위반한 채 영속됨 (역순 도착 lifecycle event 가 종료 세션 reactivate 등).
- 직교성: concurrency race(공유 메모리 동시 접근)와 구분 — 단일 스레드에서도 async defer/fire-and-forget 로 순서가 뒤바뀐 채 적용. seq 단조성·causal happens-before 가 lens.
- seed 증거:
  - `src/gateway/server-chat.ts:465,1084` — `void persistGatewaySessionLifecycleEvent(...)` 이벤트마다 fire-and-forget, 이벤트 간 직렬화 없음.
  - `src/gateway/session-lifecycle-state.ts:102-113` — `phase:"start"` 스냅샷이 무조건 `status:"running"`,`endedAt:undefined` 로 완료 세션 reactivate (테스트가 의도된 동작임을 확인). -> `end` 영속이 지연된 `start` 보다 먼저 끝나면 세션 "running" 박제.
  - `src/gateway/server-runtime-subscriptions.ts:98-100` — listener 가 `void getLifecycleEventHandler().then(h=>h(evt))` async import 후 deferred, handler 도 async 라 적용 순서 미보장.
  - 부분 guard(직교 확인): `src/infra/agent-events.ts:209-234` seq(per-run 단조) + `src/gateway/client.ts:1025-1028` onGap — agent stream 순서는 계측되나 lifecycle persistence 경로엔 seq 게이트 없음.
- maintainer_fit: 높음 ("세션 영원히 running stuck" = 전형적 reliability 버그).
- reproducibility: 중간 (start/end lifecycle event 인위 지연 인터리브 -> store 상태로 단정).
- 슬라이스 경계 주의: 수정 타깃은 gateway, emitter 는 audited subagent-registry -> 셀 정의 시 allowed_paths 협의.
- 첫 셀 후보: ordering-causality × gateway.

### idempotency  [DEFERRED — 저수율, 문서 lens 로만 등록]
- 5/5 슬라이스 정찰 결과 이미 방어 완비:
  - cron: `timer.ts` runningAtMs persist-before-exec, `ops.ts:749-756` 수동실행 lock 재확인, catch-up "정확히 1회" 회귀, runId PK upsert.
  - delivery: `infra/session-delivery-queue-storage.ts:117-138` idempotencyKey->sha256 dedup, `outbound/delivery-queue-recovery.ts` reconcileUnknownSend.
  - inbound: `auto-reply/reply/inbound-dedupe.ts` claim/commit/release, `queue/enqueue.ts` message-id dedup.
  - state: `infra/state-migrations.ts` "idempotent" 1급 설계 목표 + 재실행 가드.
- 과거 CAND-007 abandoned (memory 축 중복).
- 결론: 신규 surface 빈약. grid 에 lens 로만 등록(사용자 지정 준수), 셀 실행 보류. 미방어 가능 잔여 = restart-recovery 의 `randomUUID` idempotencyKey(단 `abortedLastRun` 영속 플래그가 cross-process 중복 차단) — 단독 STRONG 아님.

## REJECT/WEAK 로 폐기한 다른 후보 (재제안 방지 기록)
- timeout-watchdog-correctness (WEAK): cron watchdog phase 전이 정교+포화.
- schedule-arithmetic-correctness (WEAK): 시간/DST 계산, issue-driven 테스트 포화.
- build-cache-staleness (WEAK): jiti fsCache 키 불완전 의심이나 jiti 내부 동작 실행검증 필요.
- state-machine-consistency (WEAK): ordering-causality 의 하위 증상(terminal->running 역행)과 동일 근원.
- reentrancy (REJECT): embedded-agent-subscribe in-flight/unsubscribed 플래그 촘촘.
- exactly-once-delivery / backpressure / ordering(delivery) / input-validation (REJECT): gateway/auto-reply 에서 cap+dropPolicy / Zod / FIFO 로 성숙. raw payload 파싱은 extensions 소관(core scope 밖).
- schema-migration-safety / resource-exhaustion(disk/fd) (REJECT/WEAK): sqlite WAL+트랜잭션+user_version 성숙, disk-budget 부분 커버.

## 다음 단계 (이 노트 기반)
1. grid.yaml §types 에 3축(+idempotency deferred) 등록 — 완료.
2. persona 작성 필요: `data-integrity-auditor`(cross-store 겸업), `ordering-causality-auditor`. memory-leak-hunter 의 R-1~R-7 규율 상속.
3. 신규/확장 도메인 정의: data-integrity 첫 셀은 sessions(또는 agent-persistence) 도메인 신설 필요(auth-storage/settings-manager 가 src/agents/sessions/). cross-store 는 plugins 도메인 allowed_paths 확장(+src/config/mutate.ts, src/cli/plugins-*). ordering-causality 는 기존 gateway 도메인.
4. 첫 셀 우선순위: data-integrity (재현/수정 최소, maintainer-fit 최고) > cross-store-consistency > ordering-causality.
