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
