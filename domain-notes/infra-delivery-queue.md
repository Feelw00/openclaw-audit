# infra-delivery-queue 도메인 노트

openclaw 의 두 영속 전송 큐 — outbound delivery queue (`src/infra/outbound/delivery-queue*.ts`, `delivery-commit-hooks.ts`) 와 session delivery queue (`src/infra/session-delivery-queue*.ts`) — 에 대한 영구 관찰 기록. 페르소나/세션별 append-only.

---

### ordering-causality-auditor (2026-05-29)

셀: `infra-delivery-queue-ordering-causality`. allowed_paths: `src/infra/outbound/delivery-queue*.ts`, `src/infra/outbound/delivery-commit-hooks.ts`, `src/infra/session-delivery-queue*.ts`. 도메인: infra-delivery-queue. upstream HEAD: `61c538e2fc`. 축: 단일스레드 async 의 happens-before 위반 (fire-and-forget 영속 + 무조건 상태갱신으로 인과 역전이 영속).

#### 대상 파일 현황

| 파일 | LOC | 책임 |
|---|---|---|
| `src/infra/outbound/delivery-queue-recovery.ts` | 682 | outbound 복구/재시도/ack, send_attempt/unknown reconciliation, drain claim |
| `src/infra/outbound/delivery-queue-storage.ts` | 251 | outbound entry 영속, recoveryState 마커(markStarted/markUnknown), ack 2-phase |
| `src/infra/outbound/delivery-commit-hooks.ts` | 47 | WeakMap 기반 after-commit hook 등록/실행 |
| `src/infra/outbound/delivery-queue.ts` | 33 | barrel re-export |
| `src/infra/session-delivery-queue-recovery.ts` | 276 | session 복구/재시도/ack, drain claim |
| `src/infra/session-delivery-queue-storage.ts` | 184 | session entry 영속, idempotencyKey dedup, ack 2-phase |
| `src/infra/session-delivery-queue.ts` | 20 | barrel re-export |

#### 두 큐 구조 비교 (핵심)

두 큐는 같은 fs-safe durable-queue primitive (`@openclaw/fs-safe/store` 의 write/ack/move/loadPending)를 공유하고, 동일한 복구 골격(enqueuedAt 정렬 → claim → re-read-after-claim → maxRetries/backoff 검사 → deliver → ack/fail)을 가진다. 그러나 **전달의 인과 안전(이미 보냈는가)** 처리에서 정책이 갈린다.

| 항목 | outbound delivery queue | session delivery queue |
|---|---|---|
| 복구 정렬 | `enqueuedAt` 오름차순 (recovery.ts:496, 601) | `enqueuedAt` 오름차순 (recovery.ts:143, 215) |
| in-process claim | `entriesInProgress` Set + `drainInProgress` Map (recovery.ts:80-81) | 동형 `entriesInProgress` Set + `drainInProgress` Map (recovery.ts:34-35) |
| cross-process lock | 없음 (rg flock/lockfile → 0) | 없음 (rg flock/lockfile → 0) |
| re-read-after-claim | 있음 (recovery.ts:516) | 있음 (recovery.ts:152, 229) |
| **send-attempt 마커** | `send_attempt_started` / `unknown_after_send` 영속 (storage.ts:199-219; deliver.ts:600·618) | **부재** (storage.ts 에 recoveryState 필드/함수 0건) |
| **복구 시 전송여부 reconciliation** | adapter `reconcileUnknownSend` 로 sent/not_sent 판별, 불가 시 **blind replay 거부** (recovery.ts:370-440, :417) | **부재** — `drainQueuedEntry` 가 무조건 `deliver(entry)` 재호출 (recovery.ts:107) |
| enqueue 중복 dedup | 없음 (매번 uuid) | idempotencyKey sha256 + exists 체크 (storage.ts:122-129) — *enqueue 단계만* |
| permanent-error 분류 | PERMANENT_ERROR_PATTERNS → moveToFailed (recovery.ts:66-78, 453) | 없음 (모든 실패 transient 취급, 재큐) |
| deliver 내용 | 플랫폼 메시지 재전송 (`deliverOutboundPayloadsInternal`) | 에이전트 턴 재실행 + 응답 전송 (`dispatchAssembledChannelTurn`, restart-sentinel.ts:253-377) |

#### 순서/인과 보장 분석 (R-3)

happens-before 의 핵심 쌍은 **E1(deliver 성공: 전송/effect 발생) → E2(ack: 큐 제거)**. 두 큐 모두 ack 가 deliver 뒤에 오는 at-least-once 큐다 (crash 시 미전송 재시도 보장). 따라서 "전송 성공 후 ack 전 crash" window 에서 entry 가 pending 으로 남아 **재전달**되는 것이 구조적 공통점이다. 결정적 차이는:

- **outbound 는 인과 정보를 영속한다.** 전송 직전 `markDeliveryPlatformSendAttemptStarted`(deliver.ts:600), 결과 반환 후 `markDeliveryPlatformOutcomeUnknown`(deliver.ts:618)을 큐 파일에 기록한다. 복구가 그 entry 를 다시 만나면 recoveryState 가 send_attempt_started/unknown_after_send 이므로 adapter 의 `reconcileUnknownSend` 로 "실제로 보냈는지" 를 물어 sent → ack(재전송 안 함) / not_sent → replay / unresolved → blind replay 명시 거부(recovery.ts:417 "refusing blind replay without adapter reconciliation")로 분기한다. 즉 재전달의 인과 안전이 **guarded**.

- **session 은 인과 정보를 전혀 남기지 않는다.** `QueuedSessionDelivery` 타입에 recoveryState/platformSendStartedAt 필드가 없고 markStarted/markUnknown 류 함수가 storage 에 없다 (rg `recoveryState|markSession|attemptStarted|platformSend` → session 파일 match 0). `drainQueuedEntry` 는 deliver 를 recovered/failed 두 갈래로만 분류하고 재진입 시 "이미 전송됨" 을 묻는 분기가 없어 무조건 재전달한다 (recovery.ts:105-124). 즉 재전달이 **unconditional + non-terminal-protected**.

R-5 실행조건 분류표:

| 경로 | 파일:라인 | 조건 |
|---|---|---|
| outbound reconcileUnknownSend gate | recovery.ts:370-440 | guarded (send_attempt/unknown 시 전송여부 확인 후에만 replay) |
| outbound blind-replay 거부 | recovery.ts:417 | terminal-shaped (확인 불가 시 moveToFailed/fail, replay 차단) |
| session deliver→ack 재전달 | recovery.ts:107-108 | unconditional (재진입 시 무검사 재호출) |
| session recoveryState 마커 | N/A | 부재 (none) → FIND-001 근거 |
| session idempotencyKey | storage.ts:122-129 | enqueue-only (재-deliver 무효) |
| session expectedSessionId | restart-sentinel.ts:265 | partial (세션 *변경* 만 차단, "이미 실행됨" 미검사) |
| 두 큐 enqueuedAt 정렬 tie-break | recovery.ts:496/601, 143/215 | 비결정적 (동일 ms 시 secondary key 없음) — 단 아래 사유로 FIND 아님 |

#### R-3 Grep 결과

```
rg -n "seq|sequence|generation|version|nextSeq|monotonic|happens-before|onGap" \
   src/infra/outbound/delivery-queue-recovery.ts src/infra/outbound/delivery-queue-storage.ts \
   src/infra/session-delivery-queue-recovery.ts src/infra/session-delivery-queue-storage.ts
  → 0 매치. 어느 큐에도 단조 seq/generation/version 가드 없음 (순서는 enqueuedAt 정렬에만 의존).

rg -n "recoveryState|send_attempt|unknown_after|reconcile|platformSendStarted|blind replay" \
   src/infra/session-delivery-queue-recovery.ts src/infra/session-delivery-queue-storage.ts
  → 0 매치. session 큐엔 전송여부 마커/reconciliation 자체가 없음. (outbound 엔 다수 존재)

rg -n "flock|lockfile|O_EXCL|advisory|exclusive" 두 큐 recovery 4파일
  → 0 매치. cross-process lock 없음. claim 은 in-process Set/Map (drainInProgress/entriesInProgress) 뿐.

rg -rn "enqueueSessionDelivery" src --glob '!*.test.ts'
  → production caller 1곳: server-restart-sentinel.ts:606 (restart continuation, 단일 entry).

rg -n "\.toSorted\(|\.sort\(" 두 큐 recovery
  → 양쪽 모두 (a,b)=>a.enqueuedAt-b.enqueuedAt. enqueuedAt 은 Date.now() (storage.ts:153/134) → 동일 ms tie 가능.
```

#### 카테고리별 적용/skip (페르소나 §탐지 카테고리 A~E)

- [x] A. fire-and-forget 영속 + 직렬화 부재 — applied. `void persistX` 패턴은 두 큐에 없음 (모든 ack/fail/mark 가 await). 단 "deliver 성공↔ack" 인과는 session 에서 마커 부재로 보호 안 됨 → FIND-001 (fire-and-forget 은 아니나 인과 정보 미영속이라는 동축 결함).
- [x] B. 무조건 상태 갱신 / terminal reactivate — applied → FIND-001. session 재전달이 unconditional + non-terminal-protected.
- [x] C. persist-then-emit / emit-then-persist 순서 — applied. outbound 는 ack 성공 후에만 commit hook 실행(deliver.ts:1338-1351, recovery.ts:444-447)이고 reconciled 경로도 ack→afterCommit 순(recovery.ts:381-388)이라 guarded. session 엔 commit hook 개념 없음. FIND 아님.
- [x] D. deferred async handler 적용 순서 — skip. `void getHandler().then(...)` 류 deferred dispatch 없음. drain 은 await 직렬 루프.
- [x] E. 중복 in-flight 키 비대칭 — applied → FIND 아님. 두 큐 모두 claim 키가 `entry.id` 로 일관. outbound drain/recover, session drain/recover 가 동일 id 키 사용 (recovery.ts:507/616, 146/224). 비대칭 없음.

#### 산출: FIND 1건

- **FIND-infra-delivery-queue-ordering-causality-001 (P1)** — session-delivery 큐가 deliver 성공(에이전트 턴 재실행 + 응답 전송) 후 ack 전 crash 로 남은 unacked entry 를 reconciliation 없이 무조건 재전달 → agentTurn 중복 실행 + 응답 중복 전송. 평행 outbound 큐는 send_attempt/unknown 마커 + reconcileUnknownSend + blind-replay 거부로 정확히 이 시나리오를 차단하나 session 큐엔 동등 가드 부재 (정책 divergence). file: `src/infra/session-delivery-queue-recovery.ts:105-124`. impact: wrong-output.

#### FIND 후보였으나 탈락 (사유 명시)

- **enqueuedAt 정렬 tie-break 비결정성** — 두 큐 모두 `(a,b)=>a.enqueuedAt-b.enqueuedAt` 로 정렬하며 enqueuedAt 이 `Date.now()` (storage.ts:153/134) 라 동일 ms 의 두 entry 는 secondary key(id/seq) 없이 비결정적으로 정렬된다. **그러나 FIND 미생성**:
  - session 큐: production enqueue caller 가 restart continuation **1곳, 단일 entry** (server-restart-sentinel.ts:606). 한 복구 배치에 인과 순서를 가진 복수 session entry 가 동시에 존재하는 production 경로가 없다 → R-7 미충족 (현실 도착 순서 부재).
  - outbound 큐: entry 들은 서로 다른 메시지/타깃에 대한 **인과 독립** 전송이라, 큐 차원에서 strict cross-message 순서를 보장한다는 계약이 문서/코드에 없다. enqueuedAt 정렬은 "오래된 것 먼저" 라는 fairness 휴리스틱이지 인과 보장이 아니다. 동일 타깃 동일 채널의 두 메시지가 같은 ms 에 enqueue 되어 역순 전송될 이론적 여지는 있으나, deliver 경로가 메시지별 단발이고 그 순서 보장을 누구도 의존하지 않아 ordering-causality-gap 의 "인과 역전이 영속" 요건에 미달 → 이론적, P3 미만. 절제.
- **cross-process lock 부재 (동시 복구)** — 두 큐의 claim 이 in-process Set/Map 뿐이라 멀티 프로세스가 동시에 같은 stateDir 를 복구하면 같은 entry 를 둘 다 deliver 할 수 있다. **그러나 본 셀 미생성**: 이는 단일스레드 async happens-before 가 아니라 공유 자원(파일시스템 큐)에 대한 cross-process 동시접근 = concurrency 축이다. 페르소나 §절대금지("concurrency race 와 혼동 금지"). re-read-after-claim(recovery.ts:516, 152/229)이 *같은 프로세스 내* ack-후-stale 만 막고 cross-process 는 못 막는다는 관찰만 기록. concurrency 셀이 다룰 사안 (cross_refs 후보).

#### 확인 못 한 영역 (self-critique)

- `dispatchAssembledChannelTurn` (turn/kernel) 내부에 messageId 기반 platform-level dedup 이 있는지 미확인 (allowed_paths 밖). 강한 dedup 이 있으면 FIND-001 의 "응답 중복 전송" 은 흡수되고 영향이 "턴 재실행 부작용" 으로 축소된다. severity P1 은 이 불확실성을 반영해 P0 에서 절제한 값.
- ack 의 2-phase(rename→unlink, storage.ts:176-187 주석) 가 crash window 를 얼마나 좁히는지 정량 미측정. rename *시작 전* crash 면 entry 는 pending 그대로라 메커니즘은 유효하나, 실제 명중률은 미측정.
- session deliver 의 `deliverQueuedSessionDelivery` 가 systemEvent 경로(restart-sentinel.ts:260-262)는 `enqueueRestartSentinelWake` 만 호출해 재실행 부작용이 작다. FIND-001 의 심각도는 agentTurn 경로에 집중되며, 큐에 들어오는 kind 분포(systemEvent vs agentTurn)는 미측정.
- maxEnqueuedAt cutoff(recovery.ts:208-209, 233)와 RESTART_CONTINUATION_BUSY_MAX_ATTEMPTS 가 반복 재전달을 결국 종료시키나, 그 사이 이미 발생한 중복 실행/전송은 비가역. 종료까지의 최대 재전달 횟수(maxRetries 기본 5 또는 entry.maxRetries) 정량은 코드상 상한만 확인.

---

### clusterer (2026-05-29)

- CAND-050 (single, P1): FIND-infra-delivery-queue-ordering-causality-001 단독. 이 도메인의
  유일한 신규 FIND 라 묶을 짝 없음(FIND 1 ↔ CAND 1). 원인 "session-delivery 큐의 drainQueuedEntry
  가 unacked agentTurn 을 reconciliation 없이 blind replay → deliver 성공 후 ack 전 crash 시
  턴 중복 실행 + 응답 중복 전송". root_cause_chain[0](recovery.ts:107 deliver 결과를 recovered/failed
  두 갈래로만 분류, reconciliation 분기/마커 부재) + [2](outbound/delivery-queue-recovery.ts:370
  평행 outbound 큐는 reconcileUnknownSend + blind replay 거부로 차단, 정책 divergence) 인용.
  비대칭 기준선이 같은 도메인 내 평행 outbound 큐라 cross-store 가 아닌 큐 정책 divergence → single.
- 도메인 분리 규율 적용: 타 도메인과 병합 금지, infra-delivery-queue 단독 CAND.
