# acp-control-plane

ACP 세션 매니저 도메인 노트. `src/acp/control-plane/**` + `persistent-bindings.lifecycle.ts` +
`persistent-bindings.resolve.ts`. 각 페르소나는 자기 섹션에만 append (append-only).

## 도메인 개요

`AcpSessionManager` (manager.core.ts) 는 데몬 전역 싱글톤 (`getAcpSessionManager()`,
manager.ts:18) 으로, ACP 백엔드(acpx 등) 런타임 핸들을 sessionKey 별로 캐싱하고 turn
(prompt/runTurn) 실행, cancel, close, idle eviction 을 관리한다.

핵심 경계:
- **turn 실행**: runTurn/prompt → `withSessionActor` → backend failover 루프 (manager.core.ts:739~1076).
- **turn timeout**: `awaitTurnWithTimeout` (manager.core.ts:1093) + `cleanupTimedOutTurn` (:1162) +
  `awaitCleanupWithGrace` (:1206).
- **이벤트 스트림**: `consumeAcpTurnStream` (manager.turn-stream.ts:106) + `consumeAcpTurnEvents` (:20),
  `eventGate` boolean 으로 emit 차단.
- **런타임 핸들 캐시**: `RuntimeCache` (runtime-cache.ts) — Map 기반, idle TTL eviction
  (`evictIdleRuntimeHandles` manager.core.ts:1952).
- **직렬화**: `SessionActorQueue` (session-actor-queue.ts) = `KeyedAsyncQueue` wrapper.
  per-sessionKey FIFO 직렬화. **유일한 동시성 직렬화 기제** (외부 Mutex/Semaphore 없음).
- **persistent bindings**: persistent-bindings.lifecycle.ts 는 매니저를 호출하는 상위
  오케스트레이션 (ensure/reset). 자체 공유 state/동시성 primitive 없음.

## 공유 mutable state 인벤토리 (manager.core.ts)

| state | 정의 | 접근 경로 | 직렬화 |
| `activeTurnBySession: Map<string, ActiveTurnState>` | :173 | set :901 (queue 안), delete :1033-1034 (queue 안 finally), get :1274 (**cancelSession queue 밖**), has :1968 (eviction queue 안) | turn/close/eviction 은 actor queue 안; cancelSession early path 는 **밖** |
| `ActiveTurnState.cancelPromise` | manager.types.ts:132 | set :1278 (cancelSession), set :1169 (cleanupTimedOutTurn) | 공유 dedupe 필드 — cancel 중복만 막음 (close 와 무관) |
| `ActiveTurnState.abortController` | manager.types.ts:131 | abort :1276 (cancelSession), :1167 (timeout), :888/891 (caller abort) | 멱등 abort |
| `eventGate: { open: boolean }` | :908 (turn 별 새 객체) | read turn-stream.ts:33/50/78, write false :953 (onTimeout) | boolean 플래그, lock 아님 — single-check TOCTOU |
| `runtimeCache: RuntimeCache` (Map) | :172 | get/set/clear 전부 actor queue 안 (ensureRuntimeHandle/eviction/close) | actor queue 직렬화 |
| `turnLatencyStats / errorCountsByCode / evictedRuntimeCount` | :174-181 | 통계 카운터, 동기 증가 | JS 단일스레드 동기 연산이라 race 무관 |

## turn lifecycle race 분석

1. **turn vs idle eviction**: `evictIdleRuntimeHandles` (:1966) 는 candidate 별로
   `actorQueue.run` 안에서 `activeTurnBySession.has` + `lastTouchedAt` 재검사 후 close.
   unconditional guard → race 없음 (방어 견고). FIND 대상 아님.
2. **turn vs cancelSession (queue bypass)**: cancelSession active-turn 분기 (:1274-1289) 는
   withSessionActor 우회. cancel 의 abort 가 turn 을 종료시키면 turn finally 의 oneshot
   `runtime.close` (:1054) 와 cancelSession 의 `runtime.cancel` (:1278) 가 동일 handle 에
   직렬화 없이 겹침 → **FIND-001** (P2).
3. **timeout 후 detach + late emit**: timeout 시 turnPromise detach (:1138), eventGate.open=false
   (:953). eventGate single-check (turn-stream.ts:33) 와 onEvent await (:47) 사이 TOCTOU 로
   gate close 직전 통과한 이벤트 1건 누출 → **FIND-002** (P3). detach 자체는 commit 83e19ca469
   의 의도된 설계.
4. **timeout cleanup 의 detached allSettled vs 새 turn 재캐시**: `cleanupTimedOutTurn` 의
   `void Promise.allSettled([...]).then(clearCachedRuntimeStateIfHandleMatches)` (:1198-1203) 은
   `runtimeHandlesMatch` (:2224) 로 handle 일치 시에만 clear → 다른 handle 로 재캐시되면 no-op.
   safe-by-design (mismatch 시 stale 캐시 미삭제일 뿐, 잘못된 삭제는 없음). FIND 대상 아님.
5. **cancelPromise dedupe**: :1168/:1277 의 `if (!activeTurn.cancelPromise)` 는 동기
   check-then-set 이라 JS 단일스레드에서 cancel 중복은 막힘 (await 없음). race 아님.

## 실행 이력

### concurrency-auditor (2026-05-29, 셀 `acp-control-plane-concurrency`)

HEAD: `61c538e2fc` (upstream/main, audit time).

#### R-8 upstream 최근 commit 확인

allowed_paths 대상 최근 6주 commit 검토. race 관련:
- `83e19ca469 fix: keep ACP turns on OpenClaw timeouts (#82997)` — timeout 시 turn 을 죽이지
  않고 detach+gate 로 살려두는 설계 도입. FIND-002 의 eventGate/detach 가 이 설계의 잔여
  TOCTOU 임을 확인 (중복 fix 아님, 그 fix 가 남긴 micro-window).
- `635b947e32 honor terminal turn results`, `2640244d35 refresh runtime handles on config changes`,
  `f8ae0fb1c4 narrow ACP timeout config suppression` — config/terminal 처리, 본 FIND 들과
  중복 없음.

#### R-3 Grep 결과 (production, allowed_paths)

```
rg "Mutex|Semaphore|AsyncLock|acquire|release"  → production match 없음 (test deferred 뿐)
rg "AbortController|AbortSignal|signal\.(abort|addEventListener)" → manager.core.ts 다수
   (internalAbortController :886, caller abort :888-893, AbortSignal.any :904-907, withSessionActor :2164)
rg "Promise\.(race|all|allSettled)\(" → turn-stream.ts:133/158, core:1136/1198/1236
rg "once\(|prependListener|removeAllListeners" → addEventListener {once:true} :893/2164 만
rg "setImmediate|queueMicrotask|process\.nextTick" → match 없음 (turn-stream.ts:69 setTimeout 0 뿐)
```

결론: 외부 lock 부재 확정. 동시성 직렬화는 SessionActorQueue 단일. abort 전파는 존재하나
turn 종료 후 close 호출을 막진 못함.

#### 적용 카테고리 (concurrency-auditor A~H)

- [x] A. shared mutable state async race — activeTurnBySession (FIND-001), 나머지 actor queue 직렬화로 방어.
- [x] B. Promise.race loser — awaitTurnWithTimeout 의 detach (FIND-002 의 근거), turn-stream Promise.race 는 closeStream 으로 loser 정리됨 (방어 존재).
- [ ] C. listener register race — **skipped**: addEventListener 는 {once:true} + finally removeEventListener (:1030-1032), double-register 없음.
- [x] D. AbortController 전파 — abort 는 전파되나 close 호출을 막지 못함 (FIND-001 근거).
- [ ] E. microtask ordering — **skipped**: queueMicrotask/nextTick/setImmediate 없음.
- [x] F. Map atomicity — RuntimeCache/activeTurnBySession 모두 동기 Map 연산, actor queue 밖 접근은 cancelSession 만 (FIND-001).
- [x] G. double-dispatch — cancelPromise dedupe 로 cancel 중복 차단 (race 아님), turn 은 actor queue FIFO 로 직렬.
- [x] H. cleanup race — timeout cleanup detached allSettled 는 handle-match 가드로 safe (race 분석 #4).
- [x] primary-path inversion — SessionActorQueue 가 유일 atomic guard. cancelSession 이 우회하는 지점이 FIND-001.
- [x] hot-path vs test-path — cancelSession 은 abort.ts/task-registry/session-reset 등 다수 production entry, 데몬 싱글톤 → hot-path 확인.

#### 발견 요약

| FIND | severity | 요약 |
| FIND-001 | P2 | cancelSession queue 우회 → turn finally oneshot close 와 동일 handle cancel/close 경합 |
| FIND-002 | P3 | eventGate single-check TOCTOU → timeout 후 진행 중 이벤트 1건 caller 누출 + error 이벤트 삼킴 |

방어 견고하여 FIND 제외한 항목: idle eviction (actor queue 재검사), cancelPromise dedupe
(동기 check-then-set), timeout cleanup allSettled (handle-match 가드), listener register
({once:true}+finally remove). 외부 lock 없이 SessionActorQueue 단일 직렬화에 의존하는 구조라,
queue 를 우회하는 cancelSession early path 가 동시성 위험의 주 표면.

### clusterer (2026-05-29)

- CAND-054 (epic): 공통 원인 "ACP turn 동시성이 SessionActorQueue 단일 + eventGate boolean
  플래그에만 의존(외부 Mutex/Semaphore rg match 0), atomic cut-off/회수 프리미티브 부재 →
  우회/누출" 으로 2 FIND 묶음 (FIND 본문 상호 cross_ref 존중).
  - FIND-001 root_cause_chain[2]: "동시성 직렬화가 오직 SessionActorQueue 한 군데뿐이고
    외부 Mutex/Semaphore 가 없으며, cancelSession 은 응답성 때문에 그 큐를 의도적으로
    건너뛴다" (manager.core.ts:1274).
  - FIND-002 root_cause_chain[2]: "eventGate 가 단순 boolean 플래그라 닫힘과 진행 중 전달
    취소를 atomic 하게 못 한다" (manager.turn-stream.ts:11-13).
  - 같은 turn 생애주기 동시성 관리 결함 클래스 + 둘 다 외부 lock 부재(rg match 0) 기준선
    공유 + FIND-002 가 FIND-001 cross_ref → epic. severity 최고값 P2 상속.
