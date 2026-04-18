# infra-process 도메인 감시 기록

## 도메인 개요

`src/infra/**` 중 **프로세스 경계·에러 핸들링** 을 담당하는 모듈.
- `unhandled-rejections.ts` — 전역 unhandledRejection 핸들러 설치, transient/fatal 분류.
- `process-respawn.ts` — detached 자식 프로세스 spawn 으로 fresh PID 재시작.
- `restart.ts` — SIGUSR1 기반 gateway restart orchestration, supervisor 연동 (launchd /
  systemd / schtasks).
- `abort-signal.ts` — `waitForAbortSignal` helper (AbortSignal → Promise).
- `approval-handler-runtime.ts` — 채널별 native approval 핸들러 어댑터 (finalizeResolved /
  Expired / Stopped 에서 try/catch 적용).

### 상위 진입 경로

- `src/index.ts:90` → `installUnhandledRejectionHandler()`
- `src/index.ts:92-96` → `process.on("uncaughtException", ...)` (동일하게 restoreTerminalState
  → process.exit(1))
- `src/cli/run-main.ts:225` → 또 다른 uncaughtException 등록 (병렬 설치)

---

## 실행 이력

### error-boundary-auditor (2026-04-18)

**R-3 Grep 결과:**
- `rg -n "try\s*\{" src/infra/unhandled-rejections.ts` → 1 hit (line 325, handler loop 보호용).
- `rg -n "process\.on\(['\"](uncaughtException|unhandledRejection)['\"]" src/` → 13 hits;
  프로덕션 경로는 `src/index.ts:92` (uncaughtException), `src/cli/run-main.ts:225`
  (uncaughtException), `src/infra/unhandled-rejections.ts:345` (unhandledRejection). 나머지는
  전부 테스트 파일.
- `rg -n "AbortController|signal\.abort|AbortSignal" src/infra/abort-signal.ts` → 2 hits
  (export signature + signal.aborted 가드).
- `rg -n "\.catch\(" src/infra/{unhandled-rejections,process-respawn,restart,abort-signal,approval-handler-runtime}.ts` → **no matches**. 허용 파일군에 promise `.catch` 없음.

**적용 카테고리:**
- [x] A. unhandledRejection / uncaughtException handler chain — 발견 2건 (FIND-001, FIND-002)
- [x] B. Floating promise — skipped (subagent-registry 범위 밖, 허용 파일 내 float promise 없음)
- [x] C. JSON.parse 미보호 — skipped (허용 파일군에 JSON.parse 없음)
- [x] D. AbortController / AbortSignal 전파 — skipped (abort-signal.ts 테스트가 listener 쌍을 검증,
  방어 경로 존재하여 FIND 금지)
- [x] E. fs/network 동기 호출 — 발견 0건 (spawnSync 는 restart 경로에서 timeout 2s 가드; 부분
  적용. 발견 1건 후보가 있었으나 severity 낮아 제외)

**추가 발견:**
- [x] F. 오류 복구 경로의 state 부분 롤백 — FIND-003 (restart.ts emitGatewayRestart 의
  catch 가 authorization 은 놔두고 cycle 만 롤백).

**발견 FIND:**
- FIND-infra-process-error-boundary-001: exitWithTerminalRestore 가 DB/log flush 없이 exit (P1)
- FIND-infra-process-error-boundary-002: isUnhandledRejectionHandled 가 FATAL/CONFIG 분기 앞에
  서 실행되어 등록 핸들러가 치명 오류 suppress 가능 (P1)
- FIND-infra-process-error-boundary-003: emitGatewayRestart catch 가 sigusr1AuthorizedCount
  롤백 누락 (P2)

---

## try-catch / AbortController / process.on 매핑 테이블

| 파일 | try/catch (line) | scope | AbortController/Signal | process.on / process.emit |
|---|---|---|---|---|
| `unhandled-rejections.ts` | 325-334 | handler loop 보호 (handler throw 시 loop 계속) | isAbortError(reason) 판정만 (line 184-198) | `process.on("unhandledRejection", ...)` (line 345), `process.exit(1)` (line 342) |
| `process-respawn.ts` | 55-67 | spawn() call 보호, 실패 시 `{mode:"failed", detail}` 반환 | - | `process.execArgv/argv` 복사, `process.platform` 분기 |
| `restart.ts` | 127-137 (emitGatewayRestart), 216-222/233-241 (deferGatewayRestartUntilIdle) | SIGUSR1 emit 보호 (부분 롤백), getPendingCount 보호 | - | `process.listenerCount("SIGUSR1")`, `process.emit("SIGUSR1")`, `process.kill(pid, "SIGUSR1")`, `process.once("SIGINT/SIGTERM")` 없음 — 다른 모듈이 담당 |
| `abort-signal.ts` | 없음 | - | 입력 AbortSignal 의존 (새 Controller 생성 없음), `addEventListener("abort", {once:true})` + `removeEventListener` 쌍 | - |
| `approval-handler-runtime.ts` | 113-123 (finalizeWrappedEntries per-entry), 140-154 (unbindWrappedEntries per-entry) | 개별 entry 실패가 loop 중단시키지 않도록 try/catch | - | - |

**주요 관찰:**

1. **handler chain ordering**: unhandled-rejections.ts 의 핸들러 우선순위가
   `isUnhandledRejectionHandled → isAbortError → isFatalError → isConfigError →
   isTransientUnhandledRejectionError → default` 로 고정. 등록 핸들러가 첫 번째이므로
   overreach 시 FATAL/CONFIG 가 가려지는 FIND-002 의 근거.

2. **두 갈래 uncaughtException 핸들러**: src/index.ts:92 와 src/cli/run-main.ts:225 에 병행
   등록. 중복 실행 여부 및 ordering 은 본 조사 범위 밖이지만 cross-cell 연결 후보.

3. **restartCycleToken 설계**: restart.ts 는 emit/consume 쌍으로 cycle 을 구분 (line 41-43).
   그러나 부분 실패 시 롤백이 비대칭이다 (FIND-003).

4. **approval-handler-runtime 은 per-entry try/catch 가 일관됨**: resolved / expired /
   stopped 3 경로 모두 `for...of` 루프 내부에 try/catch 를 둬 한 entry 의 실패가 다른 entry
   에 영향 없도록 설계. 개별 오류는 log.error 로 남김. 이 패턴은 정상.

5. **abort-signal.ts 는 최소 표면이지만 테스트가 contract 를 명시**: 리스너 등록/해제 pair
   가 abort-signal.test.ts:30-56 에 검증됨. 방어 경로 '존재' 로 인정하여 FIND 작성 안 함.

6. **허용 파일군에 promise `.catch(` 없음**: fire-and-forget 패턴 (void fn().catch(...)) 이
   부재. 이는 긍정(에러 silent swallow 경로 없음) 또는 부정(awaited 경로의 catch 누락 가능성
   상존) 양면 해석. 추가 범위의 파일(예: cli/run-main.ts) 조사는 다른 셀에서 해야 함.

### clusterer (2026-04-18)

- **CAND-006 (single)**: FIND-infra-process-error-boundary-003. emitGatewayRestart
  catch 가 emittedRestartToken 만 롤백, sigusr1AuthorizedCount deauthorize 누락
  (P2).
- **Cross-domain 관찰**: 동 배치의 FIND-plugins-lifecycle-001 (plugin register
  throw 시 registry 배열 부분 등록 잔존) 와 "try 블록에서 변경된 N 개 상태 중
  일부만 catch 에서 복구" 상위 패턴을 공유함. 본 도메인의 "restartCycleToken
  설계 - 부분 실패 시 롤백 비대칭" 관찰 3 과 plugins 도메인의 "register*/
  restoreRegistered* 대칭 결여" 테이블은 서로 독립 근거임. 다른 도메인/자료구조/
  실패 경로로 인해 epic 불가, 각각 single CAND 로 분리 (CAND-005, CAND-006)
  하고 cross_refs 로만 연결.

### memory-leak-hunter (2026-04-18)

**R-3 Grep 결과:**

- `rg -n "activeDeferralPolls\\.(add|delete|clear|size)" src/infra/restart.ts`
  → 5 hits. add at L257, delete at L237/244/252 (세 conditional 경로 모두),
  clear at L58 (clearActiveDeferralPolls) — 모든 exit path 커버. R-5 분류:
  | 경로 | 조건 |
  |---|---|
  | L237 delete+clearInterval | getPendingCount throws 시 |
  | L244 delete+clearInterval | current<=0 drain 시 |
  | L252 delete+clearInterval | elapsed>=maxWaitMs timeout 시 |
  | L58 clear (clearActiveDeferralPolls) | emitGatewayRestart 진입 + `__testing` |
  모든 interval 이 세 branch 중 하나로 종료되며 emit 단에서 추가 sweep 있음
  → **leak 아님. FIND 생성 금지.**
- `rg -n "handlers\\.(add|delete|clear)" src/infra/unhandled-rejections.ts`
  → add at L317, delete at L319 (registerUnhandledRejectionHandler 반환 cleanup).
  호출자 확인 — bonjour.ts:254 finally block 에서 cleanup, telegram monitor.ts:233
  에서 unregisterHandler(), whatsapp monitor 에서도 저장. 모두 cleanup 경로 있음
  → **leak 아님.**
- `rg -n "activeEntries\\.(set|delete|clear)" src/infra/approval-handler-runtime.ts`
  → set at L576 (deliverTarget), delete at L102 (consumeActiveWrappedEntries),
  clear at L699/L712 (onStopped). R-5 분류:
  | 경로 | 조건 |
  |---|---|
  | L102 delete | `unconditional` on resolved/expired event (finalizeResolved L629 / finalizeExpired L664 호출) |
  | L699 clear | `shutdown` (onStopped 조기 반환) |
  | L712 clear | `shutdown` (onStopped 정상 완료) |
  normal flow (resolved/expired) 에서 unconditional delete 존재 → **leak 아님.**
  단, stop()/onStopped 가 deliverTarget 의 await deliverPending 사이에 실행되고,
  deliverPending resolve 후 resumption 이 L576 set 을 수행하는 race 는 이론적으로
  가능 (orphan entry). 그러나 process/handler lifecycle 종료 경로여서 후속 GC 가
  Map 을 전체 회수. **memory-leak 으로 FIND 화하지 않음** (correctness 이슈임).
- `rg -n "pendingRestartTimer\\s*=|clearTimeout.*pendingRestartTimer" src/infra/restart.ts`
  → 6 hits. setTimeout return stored at L480; callback 본체 L482-484 unconditional
  null 화; clearPendingScheduledRestart L46-49 도 clearTimeout + null. `unconditional`
  + `shutdown` 경로 모두 존재 → **leak 아님.**
- `rg -n "process\\.(removeListener|off)|removeAllListeners" src/infra/unhandled-rejections.ts`
  → **match 없음.** installUnhandledRejectionHandler 가 설치한 process 리스너의
  제거 경로 **전무**. 이는 FIND-infra-process-memory-001 의 핵심 근거.

**적용 카테고리:**

- [x] A. 무제한 자료구조 성장 — 발견 1건 (FIND-001: process 리스너 누적)
- [x] B. EventEmitter / 리스너 누수 — FIND-001 이 여기에 해당
- [x] C. 강한 참조 체인 — 적용했으나 발견 0 (모듈 변수 closure 는 cleanup 경로 있음)
- [x] D. 핸들/리소스 누수 — process-respawn.ts 의 detached spawn + unref 정상,
  abort-signal.ts 의 addEventListener+removeEventListener+{once:true} 이중 안전망
  정상 → 발견 0
- [x] E. 캐시 TTL — 적용, 셀에 캐시 없음 → skipped

**핵심 관찰:**

1. **idempotency 부재 패턴 (FIND-001)**: installUnhandledRejectionHandler 는
   idempotent 가드가 없고 uninstall 경로도 없어 N 번 호출 시 N 개 리스너 등록.
   프로덕션 CLI 기본 경로 (`src/index.ts:90` → `runLegacyCliEntry` →
   `src/cli/run-main.ts:223`) 에서 2 회 호출됨 (각 독립 파일). 리스너 2개로
   시작, 재진입 / 테스트 러너 / 라이브러리 import 반복 시 선형 누적. 동일 패턴이
   uncaughtException 최상위 리스너 (index.ts:92, run-main.ts:225) 에도 존재
   하지만 범위 밖.
2. **handlers Set (L13) 은 별개 설계**: registerUnhandledRejectionHandler 로
   호출자가 handler 를 add/delete 하는 보조 체인. FIND-001 의 최상위 리스너
   누적 문제와 독립. 보조 체인 자체는 cleanup 경로 모든 호출자에서 확인.
3. **restart.ts 의 자료구조 4종 (activeDeferralPolls, pendingRestartTimer,
   sigusr1AuthorizedCount, restartCycleToken) 모두 cleanup 경로 완비**:
   activeDeferralPolls 는 3 exit branch + shutdown sweep, pendingRestartTimer
   는 self-null + clearPendingScheduledRestart, sigusr1AuthorizedCount 는
   consume 시 expiry 검사, restartCycleToken 은 scalar. 유일한 문제는
   **정상 flow 가 아닌 error 롤백 부분 비대칭** (CAND-006 에서 처리 중, 중복 금지).
4. **approval-handler-runtime.ts 의 activeEntries Map 는 normal flow
   unconditional delete (L102) 존재**: R-5 규율상 FIND 금지. stop-during-deliver
   race 는 correctness 이슈이며 memory 증상 아님 (handler 교체 시 Map 전체 GC).
5. **process-respawn.ts / abort-signal.ts 는 최소 표면 + 방어적 설계**:
   spawn 은 detached+unref, addEventListener 는 {once:true} 로 자동 해제 + explicit
   removeEventListener. 테스트가 contract 를 명시 (abort-signal.test.ts:30-56).
   FIND 금지.

**발견 FIND:**
- FIND-infra-process-memory-001: installUnhandledRejectionHandler idempotency
  부재로 process.on("unhandledRejection") 리스너 중복 등록 (P3).

### clusterer (2026-04-18, Phase 2)

- **CAND-007 (single)**: FIND-infra-process-memory-001.
  installUnhandledRejectionHandler 의 idempotency 부재 + uninstall 경로 부재로
  리스너 중복 등록 (P3).
- **Cross-cell 관찰**: 동 배치의 FIND-infra-retry-concurrency-002/003 (jitter
  infrastructure 문제) 과 root cause 공유 없음 — 파일·symptom·자료구조 모두
  상이. epic 근거 부재로 single CAND 분리. 본 도메인 이전 관찰 "두 갈래
  uncaughtException 핸들러" (memory-leak-hunter §1, index.ts:92 + run-main:225)
  와 같은 "two-entrypoint duplicate registration" 패턴의 unhandledRejection
  변종이나, uncaughtException 은 allowed_paths 밖이라 본 CAND 에서 미포함.
