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

### plugin-lifecycle-auditor (2026-05-14)

**R-3 Grep 결과:**

- `rg -n "dispose|teardown|cleanup|unregister|deregister"` (allowed 5종)
  → 1 hit (restart.ts:629 주석에 "deregistered from launchd" 단순 단어).
  명시적 dispose/teardown 함수 0건.
- `rg -n "process\.(on|once|off|removeListener)"` (allowed 5종)
  → 1 hit. `process.on("unhandledRejection", ...)` (unhandled-rejections.ts:511) 만.
  본 셀 범위 내 process listener detach 호출은 production·테스트 모두 0건
  (테스트 파일들의 `process.off("unhandledRejection", ...)` 호출은 각 테스트가
  자체적으로 등록한 listener 를 떼는 것; installUnhandledRejectionHandler 가
  설치한 익명 콜백을 떼는 경로는 없음).
- `rg -n "AbortController|AbortSignal|addEventListener|removeEventListener"`
  → abort-signal.ts:7/10 의 `addEventListener("abort", onAbort, {once:true})`
  + 내부 `removeEventListener` 쌍. **{once:true} + 명시 remove 이중 안전망.**
  abort-signal.test.ts:30-56 가 contract 검증.
- `rg -n "setInterval|clearInterval|setTimeout|clearTimeout"` (allowed 5종)
  → restart.ts 9 hits. memory-leak-hunter 가 이미 모든 exit branch
  (clearInterval@L490/L497/L509, clearActiveDeferralPolls@L71, callback self-null
  @L484, clearPendingScheduledRestart@L46-49) 검증.
- `rg -n "try\s*\{"` → 15 hits. lifecycle 관점에서 신규 발견 없음.

**R-5 (CAL-001) execution-condition 분류표 (lifecycle 축):**

| listener / handle | 등록 경로 | cleanup 경로 | 분류 |
|---|---|---|---|
| `process.on("unhandledRejection")` (unhandled-rejections.ts:511) | `installUnhandledRejectionHandler()` 호출 | **없음** (uninstaller 미제공) | `none` (CAND-007 axis 인접) |
| `handlers` Set entries (unhandled-rejections.ts:20) | `registerUnhandledRejectionHandler` (L457) | closure return `handlers.delete` (L459); caller bonjour/telegram/whatsapp finally·explicit 호출 | `unconditional` (호출자 책임) |
| `exceptionHandlers` Set (unhandled-rejections.ts:30) | `registerUncaughtExceptionHandler` (L480) | closure return `exceptionHandlers.delete` (L482); tui.ts:303 cleanup 함수 반환 | `unconditional` (호출자 책임) |
| `signal.addEventListener("abort")` (abort-signal.ts:10) | `waitForAbortSignal` 진입 | onAbort 내부 `removeEventListener` (L7) + `{once:true}` | `unconditional` (이중 안전) |
| `pendingRestartTimer` setTimeout (restart.ts:786) | `scheduleGatewaySigusr1Restart` | callback 본체 self-null (L790-794), `clearPendingScheduledRestart` (L46-49) | `unconditional` |
| `activeDeferralPolls` setInterval (restart.ts:485) | `deferGatewayRestartUntilIdle` (L453) | 3 branch clearInterval+delete (L490/L497/L509), `clearActiveDeferralPolls` (L71) | `unconditional` |
| spawn child (process-respawn.ts:27) | `spawnDetachedGatewayProcess` | `child.unref()` + caller exit; detached=true → OS 가 lifetime 관리 | `unconditional` (OS 위임) |
| `activeEntries` Map entries (approval-handler-runtime.ts:535) | `deliverTarget` callback | `consumeActiveWrappedEntries` (L96 delete), `onStopped` (L671 clear) | `unconditional` (정상 flow) |
| native `binding` (approval-handler-runtime.ts:524) | `bindPending` 호출 후 wrapped.binding 저장 | finalize 시 `unbindPending` (L597, L632), `onStopped` → `unbindWrappedEntries` (L662) | `unconditional` (finalize/stop) |

**적용 카테고리 (lifecycle 축):**

- [x] A. listener attach 후 detach 누락
  → `process.on("unhandledRejection")` 만 uninstaller 부재. 그러나
    (1) CAND-007 의 idempotency axis 와 같은 listener,
    (2) production normal flow 에서 "install 했다가 끄는" 시나리오 없음 (in-process
        restart 시에도 globalThis 캐시 재사용. resetGatewayRestartStateForInProcessRestart
        도 listener 는 건드리지 않음 — 의도된 설계),
    (3) process exit 시 자동 GC 로 leak 부담 0
  → 별 axis 의 단단한 결함 아님. **FIND 금지.**
- [x] B. AbortController register 후 abort listener detach 누락
  → abort-signal.ts 는 {once:true} + 명시 remove 이중 방어. error-boundary-auditor
    이전 평가 (FIND 금지) 와 동일. plugin-sdk re-export 라 production hot-path
    in-repo 호출자 0 (R-7 미충족). **Skip.**
- [x] C. approval handler register/unregister 비대칭
  → `consumeActiveWrappedEntries` (L96 delete) + `onStopped` clear (L671) 모두
    unconditional. binding 도 finalize/unbind 쌍 완비. 단, `deliverTarget`
    내부 `bindPending` (L517) throw 시 wrapped 가 activeEntries 에 등록되지
    않아 native side 에 deliver 된 entry 가 orphan 가능 — 그러나 caller
    (approval-native-runtime.ts, 범위 밖) 가 throw 를 어떻게 처리하는지 contract
    추적 불가. confidence 낮음. **Skip.**
- [x] D. timeout / interval cleanup 누락
  → memory-leak-hunter 가 이미 검증. 모든 exit branch에 clearInterval/clearTimeout
    + Set delete. **Skip.**
- [x] E. emitGatewayRestart listener lifetime
  → emitGatewayRestart 자체는 listener 등록 안 함 (process.emit/process.kill 만).
    SIGUSR1 listener 는 cli/gateway-cli/run-loop (범위 밖) 가 attach.
    CAND-006 의 부분 롤백 axis 와도 무관. **Skip.**
- [x] F. respawn loop graceful shutdown vs immediate exit
  → process-respawn.ts 는 detached spawn + child.unref() 후 caller 가 exit.
    timeout 사용 안 함, listener 등록 안 함. cleanup 대상 자체가 없음.
    **Skip.**

**핵심 관찰:**

1. **dispose/teardown 함수 부재가 결함 아닌 설계 의도**: allowed 5 파일 전체에서
   "dispose|teardown|cleanup|unregister|deregister" 매치 0건이지만, listener·
   timer·child 각각이 OS/process exit/once-true/closure-return 방식으로 lifetime
   을 위임함. install/uninstall 비대칭처럼 보이는 `installUnhandledRejectionHandler`
   는 process-level signal handler 라 "lifetime = process lifetime" 가 정상 설계.

2. **CAND-006/007 와 별 axis 후보가 없음**: 후보들이 결국 같은 file/같은 자료구조
   /비슷한 mechanism 으로 수렴. lifecycle 축 자체로 epic 가능한 새 결함 없음.

3. **approval-handler-runtime.ts 의 bindPending orphan 가능성은 외부 contract
   의존**: deliverPending 성공 → bindPending throw 시 wrapped 미등록.
   approval-native-runtime.ts (범위 밖) 가 throw 를 swallow 하는지, native
   side entry 가 어떻게 cleanup 되는지 본 셀 범위에서 확인 불가. R-7
   production hot-path branch 동작 불명 → confidence 낮음, abandon.

4. **deferGatewayRestartUntilIdle hook callback throw 시 setInterval orphan**:
   line 499/505/511 의 hook 호출은 try/catch 없음. hook throw 시 clearInterval
   skip → poll 영구. 그러나 production caller (server-reload-handlers.ts:423)
   는 hook 에 logger.warn/info 만 공급. R-7 적용 시 production branch 의 실제
   동작 = "hook throw 없음". false positive 위험 → abandon.

**발견 FIND:** 0건. lifecycle 축에서 단단한 새 결함은 발견되지 않음.

**CAL-008 활동 검토:** restart.ts 는 6주 내 fix 다수 (preserve restart hooks
during async prep `2a4514af`, preserve restart hooks across coalescing
`ab32c531`, keep restart emitting after ack prep failure `46ce666b`, bound
default restart deferral `1f41b8b4`, expose restart drain controls `f6f8d747`)
— lifecycle 영역의 변동성 매우 큼. 열린 PR 도 #46303 #70466 #72224 등 다수.
upstream 이 활발히 다듬는 영역이라 현재 코드는 안정화 단계로 판단.
unhandled-rejections.ts 도 transient error 분류 정교화 중 (`d1365fef` ENOSPC,
`db6951088a` ECONNREFUSED 등). approval-handler-runtime.ts 는 type/export
refactor 가 주로 진행됨 (lifecycle 관련 변경 없음).

### concurrency-auditor (2026-05-14)

**R-3 Grep 결과 (5종, 본 셀 5 파일 전체):**

- `rg -n "Mutex|Semaphore|AsyncLock|acquire|release" {5파일}` → **0 hits.**
  본 셀 file 군 내 동기화 원시 부재.
- `rg -n "AbortController|AbortSignal|signal\.(abort|addEventListener)" {5파일}`
  → 3 hits, 모두 abort-signal.ts (L1/L2/L10). 새 AbortController 생성 0건.
- `rg -n "Promise\.race\(|Promise\.all\(|Promise\.allSettled\(" {5파일}` → **0 hits.**
- `rg -n "once\(|prepend(Once)?Listener\(|removeAllListeners\(" {5파일}` → **0 hits.**
- `rg -n "setImmediate|queueMicrotask|process\.nextTick" {5파일}` → **0 hits.**

5종 중 4종이 zero match. 본 셀은 동기화 원시도 race-prone API 도 거의 사용 안 함.
나머지 race 표면은 (a) `await` 사이의 shared mutable state read-modify-write,
(b) 외부 process signal emit 동시 호출 두 카테고리로 좁혀짐.

**R-5 (CAL-001) execution-condition 분류 (concurrency 축):**

| race 후보 | 위치 | unconditional guard | 분류 |
|---|---|---|---|
| `restart.ts emitGatewayRestart` 동시 두 호출 | L280-320 | `hasUnconsumedRestartSignal()` (L281) — sync check, set/clear도 같은 sync 블록 | `unconditional` → FIND 금지 |
| `scheduleGatewaySigusr1Restart` 동시 두 호출 | L684-819 | L711 + L730 coalesce/pull-earlier 분기 (sync) | `unconditional` |
| `emitPreparedGatewayRestart` 의 `pendingRestartEmitHooks` chain | L413-446 | 2a4514afca + ab32c53103 이미 fix (R-8) — while-loop 가 await 사이 추가된 hooks pickup | `unconditional` (chain fix) |
| `deferGatewayRestartUntilIdle` 멀티 instance 의 `activeDeferralPolls` | L453-516 | `activeDeferralPolls` Set 의 add/delete 가 sync. 첫 emit 가 token 점유 → 나머지 bail | `unconditional` |
| `installUnhandledRejectionHandler` 의 process listener 재등록 | L502-546 | idempotency guard 없음 (CAND-007 abandoned) | `none` (memory axis 별도) |
| `isUnhandledRejectionHandled` iteration 중 handlers Set 변이 | L463-477 | JS Set 의 iteration semantics + handlers.delete 안전 | `unconditional` (JS semantics) |
| `process-respawn.ts spawnDetached` 동시 두 호출 | L25-34 | gateway-lock 외부 가드 (범위 밖) | `conditional-edge` (외부 의존, 본 셀 가드 없음) |
| `abort-signal.ts` L2 check 와 L10 addEventListener 사이 race | L1-12 | L2 와 L10 사이에 await 없음 (sync 블록) | `unconditional` (JS single-thread) |
| `approval-handler-runtime.ts activeEntries` read-modify-write (L529-535) | deliverTarget L498-537 | 없음 — line 505/517 두 await 가 sync 블록 분리 | `none` → **FIND-001** |
| `consumeActiveWrappedEntries` (L89-97) 직후 deliverTarget 진입 race | finalize* L587-655 | caller (out-of-scope) 의 dispatch ordering 에 의존 (R-7) | `none` 가능, **R-7 confidence 낮음 abandon** |

**적용 카테고리:**

- [x] A. Shared mutable state 의 async 갱신 race
  → activeEntries Map 의 read-modify-write 가 두 await (deliverPending /
    bindPending) 뒤에 일어남. onStopped clear 와 race → **FIND-001 (P3).**
- [x] B. Promise.race loser 처리
  → R-3 grep 0 hits. **Skip.**
- [x] C. Listener / hook register-unregister race
  → unhandled-rejections.ts 의 idempotency 부재는 CAND-007 (memory axis) 중복.
    restart.ts emitGatewayRestart 의 process.emit("SIGUSR1") 후 동기 reentrance
    가능성 분석 — run-loop SIGUSR1 listener 는 async IIFE 라 sync 부분이 즉시
    반환, reentrance 없음. **Skip.**
- [x] D. AbortController / AbortSignal 전파 단절
  → 본 셀에 AbortController 사용처 0. abort-signal.ts 는 receiver-only helper,
    R-3 race 분석상 race 미발생 (L2-L10 sync). **Skip.**
- [x] E. Microtask / setImmediate ordering 가정
  → R-3 grep 0 hits. **Skip.**
- [x] F. Map/Set operation atomicity
  → activeEntries (approval-handler-runtime), handlers (unhandled-rejections),
    activeDeferralPolls (restart) 세 Map/Set 점검. 후자 둘은 unconditional 가드
    완비. activeEntries 만 race → FIND-001 에서 처리.
- [x] G. Double-dispatch / re-entrance
  → emitGatewayRestart 의 single-flight token guard 검증 (L281). 동시 두 호출
    중 두번째는 bail. process.emit 후 markGatewaySigusr1RestartHandled 가 동기
    실행되더라도 sync 블록 내에서 token 정합. **Skip.**
- [x] H. Race with cleanup / disposal
  → onStopped (approval-handler-runtime) 가 in-flight deliverTarget 를 await
    하지 않고 activeEntries.clear() 하는 패턴이 FIND-001 의 본질.
- [x] **primary-path inversion (CAL-001)**: race 가 재현되려면 어떤 atomic guard
  가 우회돼야 하는가 — activeEntries 접근에 file-local lock 없음, caller (out-of-
  scope) 의 dispatch ordering 만이 사실상 가드. unconditional guard 부재 확인.
- [x] **hot-path vs test-path (R-7)**: caller approval-native-runtime.ts (범위 밖)
  가 deliverTarget 와 onStopped 의 ordering 을 보장하는지 본 셀 범위에서 확인
  불가. confidence 낮아 P3 로 격하. CAL-008 "borderline P3 OK" 가이드에 따라
  FIND 기록.

**핵심 관찰:**

1. **본 셀은 동기화 원시를 거의 쓰지 않음.** 대신 (a) 모든 자료구조 변이를 sync
   block 안에 묶거나, (b) token 패턴 (emittedRestartToken / consumedRestartToken)
   으로 single-flight 를 보장. await 사이 외부 mutation 가능성을 인지하고 설계.

2. **유일하게 await 사이 race 가 남은 곳이 approval-handler-runtime 의 deliverTarget
   activeEntries 갱신**. caller (approval-native-runtime.ts) 의 dispatch contract
   에 race 방지 책임을 위임한 형태. 본 셀 코드만으로는 race 확정 불가하지만,
   guard 부재가 명확하므로 FIND 기록 후 cross-cell 또는 메인테이너 리뷰에서 caller
   contract 확인 필요.

3. **CAL-008 upstream 변동성 검토**: restart.ts 의 concurrency 관련 fix 가 매우
   활발 (2a4514afca, ab32c53103, 46ce666b04, fe5f0cddb9). 본 셀 내 race 후보 중
   `pendingRestartEmitHooks` chain 은 upstream 이 이미 fix → 본 audit 에서 FIND
   금지 (R-8). approval-handler-runtime.ts 는 race/concurrent 키워드 commit 0건.

4. **abort-signal.ts 는 plugin-sdk re-export 만 됨** (production hot-path in-repo
   caller 0). FIND 대상 아님.

**발견 FIND:**
- FIND-infra-process-concurrency-001: deliverTarget 의 activeEntries 갱신 사이
  onStopped 가 clear 하면 wrapped binding orphan (P3, resource-exhaustion).
