# domain-notes: bash-process-execution

셀: `bash-process-execution-memory` / 도메인: `bash-process-execution`
작성: memory-leak-hunter, 2026-05-29
대상 upstream: openclaw @ 61c538e2fc (읽기 전용)

allowed_paths:
- src/agents/bash-tools*.ts
- src/agents/bash-process-registry.ts
- src/agents/bash-process-references.ts
- src/agents/run-cleanup-timeout.ts
- src/agents/run-termination.ts

## 아키텍처 요약

exec tool (`bash-tools.exec.ts:createExecTool`) → `runExecProcess`
(bash-tools.exec-runtime.ts) → supervisor spawn → child process.
세션 상태는 모듈 전역 registry (`bash-process-registry.ts`) 의 두 Map 에 보관:
- `runningSessions`: 실행 중 세션
- `finishedSessions`: backgrounded 로 살아남아 종료된 세션 (poll/log 조회용)

`markExited` → `moveToFinished` 가 running→finished 전이를 담당하고, child stdio
destroy + removeAllListeners 수동 정리도 여기서 한다. TTL sweeper(setInterval)가
finishedSessions 를 주기적으로 prune.

exec tool 의 abort 처리는 `onAbortSignal` 리스너로, 전달받은 AbortSignal 에
`{ once: true }` 등록. signal 출처는 embedded-agent-runner 의 per-run
`runAbortController.signal` (attempt.ts:712, 모든 tool.execute 가 공유).

## Map / Set / Array 인벤토리

| 구조 | 위치 | 추가 경로 | 비고 |
|---|---|---|---|
| `runningSessions` Map | registry.ts:98 | addSession:114 | 실행 중 세션. exit 시 finished 로 이동 |
| `finishedSessions` Map | registry.ts:99 | moveToFinished:225 (backgrounded 만) | aggregated 출력 보유. **size cap 없음** |
| `pendingStdout`/`pendingStderr` `string[]` | ProcessSession 필드 | appendOutput:140 push | drainSession:163 / capPendingBuffer:258 로 char-cap. drain 시 [] 리셋 → 무한 성장 아님 |
| `PREFLIGHT_ENV_OPTIONS_WITH_VALUES` 등 Set | exec.ts:96,109,255,... | const 초기화 | 고정 lookup 테이블. 누적 아님 |

## Timer 인벤토리

| timer | 위치 | clear 경로 | 실행 조건 분류 | 누수? |
|---|---|---|---|---|
| `sweeper` setInterval | registry.ts:335 (unref) | stopSweeper:343 (clearInterval) | shutdown/test (resetProcessRegistryForTests, setJobTtlMs) + idempotent guard:332 | NO — 단일 interval, unref |
| `yieldTimer` setTimeout | exec.ts:1699 | clearTimeout: onYieldNow:1685, promise.then:1712, promise.catch:1727 | unconditional (3개 terminal 경로 전부 clear) | NO |
| cleanup `timeoutHandle` setTimeout | run-cleanup-timeout.ts:99 (unref) | clearTimeout:110 | unconditional (race 후 항상 clear) | NO |

## Listener 인벤토리

| listener | 위치 | remove 경로 | 실행 조건 분류 | 누수? |
|---|---|---|---|---|
| `onAbortSignal` (signal "abort") | exec.ts:1659 `{ once: true }` | removeEventListener **없음**; once 는 abort fire 시에만 | conditional-edge (abort 발생 시에만 자동제거) | **YES** → FIND-001 |
| child stdio listeners | registry.ts:199 removeAllListeners | moveToFinished:199 (unconditional, 모든 markExited 경유) | unconditional | NO |
| external abort listener | attempt.ts:907 add / 909 remove | removeEventListener:909 명시 | unconditional | NO (셀 범위 밖, 대조용) |

## Eviction 경로 표 (R-3 / R-5 종합)

| 대상 | eviction 경로 | grep 결과 | 실행 조건 | 판정 |
|---|---|---|---|---|
| `runningSessions` | moveToFinished:189 delete, deleteSession:125, clear:308(test) | delete/clear 다수 | **unconditional** (moveToFinished 가 모든 exit 에서 호출: exec-runtime.ts:908/924/959/983) | leak 아님 |
| `finishedSessions` | prune:326(TTL), deleteSession:126, clearFinished:304(dead), reset:309(test) | size cap match **없음** | TTL prune=conditional-edge(sweeper 의존), deleteSession=conditional(process clear/remove), clearFinished=프로덕션 호출자 없음 | **leak** → FIND-002 |
| `onAbortSignal` | once 자동제거(abort 시) | removeEventListener match **없음** | conditional-edge | **leak** → FIND-001 |

## 발견 요약

- **FIND-bash-process-execution-memory-001** (P2, memory-leak): exec abort 리스너가
  per-run 공유 AbortSignal 에 `{ once: true }` 로 등록되지만 정상 종료 경로에서
  removeEventListener 가 없어, 한 run 안의 exec 호출마다 리스너 + run/session
  그래프(출력 버퍼 포함)가 run 종료까지 누적. signal 이 per-run 공유
  (attempt.ts:712/3003)인 점이 핵심 조건.
- **FIND-bash-process-execution-memory-002** (P2, memory-leak): finishedSessions Map
  에 size cap/LRU 가 없고 자동 제거가 TTL prune(sweeper 의존) 하나뿐. TTL(기본
  30분, 최대 3시간) 윈도 안에서 고빈도 background-exec 는 무제한 누적 가능하고,
  sweeper 중지/미시작 시 만료분도 정리 안 됨.

두 FIND 는 cross_refs 로 연결 (둘 다 bash-process-execution 세션 수명 관리의
cleanup-누락 계열).

## leak 아님으로 종결한 항목 (재감사 절약용)

- `runningSessions`: moveToFinished/deleteSession 의 unconditional delete. R-5 규율상 FIND 금지.
- `sweeper` / `yieldTimer` / cleanup `timeoutHandle`: 전부 대응 clear 경로가
  unconditional 또는 idempotent guard 존재.
- `pendingStdout`/`pendingStderr`: char-cap(capPendingBuffer) + drain 리셋으로 bound.
- child stdio listeners: moveToFinished:199 의 removeAllListeners 가 모든 exit 에서 unconditional 실행.
- moveToFinished 의 stdin/child destroy(192-219): 정상 경로에서 호출되나, 예외/early-return 시
  FD 누수 가능성은 이론적이며 destroy 호출 자체는 try 없이 직접 호출 → 본 감사에서는 명확한
  무제한 성장 증상이 아니라 제외(필요 시 resource-exhaustion 축으로 별도 재감사 대상).

### clusterer (2026-05-29)

- CAND-055 (epic): 공통 원인 "exec/session 수명 관리에서 set/add(hot-path 무조건 실행)에
  대응하는 unconditional eviction 경로 부재(제거가 conditional-edge 또는 dead) → 보유 컨테이너
  선형/무제한 성장, retained 가 exec 출력 버퍼" 로 2 FIND 묶음 (FIND 본문 상호 cross_ref 존중).
  - FIND-001 root_cause_chain[0]: "`{ once: true }` 는 이벤트 발생 시에만 제거를 보장하는데,
    정상 종료/yield 경로에는 removeEventListener(onAbortSignal) 호출이 전혀 없다"
    (bash-tools.exec.ts:1659).
  - FIND-002 root_cause_chain[0]: "set(225)에 대응하는 자동 제거가 TTL prune(326) 하나뿐이고
    개수 상한(cap/LRU)이 없다" (bash-process-registry.ts:225).
  - 둘 다 R-3 grep 으로 "unconditional eviction 경로 없음" 확정 + retained 데이터가 exec
    출력 버퍼라는 점 공유 + FIND frontmatter 상호 cross_ref → epic. severity 둘 다 P2.
