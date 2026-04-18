---
id: FIND-agents-registry-memory-002
cell: agents-registry-memory
title: sweeper self-stop strands pendingLifecycleError entries indefinitely
file: src/agents/subagent-registry.ts
line_range: 603-615
evidence: "```ts\n    // Sweep orphaned pendingLifecycleError entries (absolute TTL).\n\
  \    for (const [runId, pending] of pendingLifecycleErrorByRunId.entries()) {\n\
  \      if (now - pending.endedAt > PENDING_ERROR_TTL_MS) {\n        clearPendingLifecycleError(runId);\n\
  \      }\n    }\n\n    if (mutated) {\n      persistSubagentRuns();\n    }\n   \
  \ if (subagentRuns.size === 0) {\n      stopSweeper();\n    }\n```\n"
symptom_type: memory-leak
problem: '`pendingLifecycleErrorByRunId` Map 엔트리와 그 내부의 `NodeJS.Timeout` 핸들이

  sweeper 종료 이후 무기한 메모리에 상주할 수 있다.


  `sweepSubagentRuns()` 는 매 cycle 마지막에 `subagentRuns.size === 0` 이면 `stopSweeper()`

  를 호출해 setInterval 을 정지시킨다. 그러나 같은 cycle 에서 pendingLifecycleError 의 sweep

  은 동일 TTL(5분) 만료분만 정리한다. 그 결과 "run 은 모두 정리되었지만 pending error 는 아직

  5분 TTL 이 도달하지 않은" 상태로 sweeper 가 멈추면, 그 orphaned pending entry 는 다음에

  `registerSubagentRun()` / `replaceSubagentRunAfterSteer()` / `restoreSubagentRunsOnce()`
  가

  `startSweeper()` 를 재호출할 때까지 메모리를 점유한다.

  '
mechanism: "1. 서브에이전트 run A 실행 중 lifecycle `error` 이벤트 → `schedulePendingLifecycleError(A)`\n\
  \   호출 (line 657) → `pendingLifecycleErrorByRunId.set(A, { timer, endedAt, error\
  \ })` (line 272).\n2. 15초 LIFECYCLE_ERROR_RETRY_GRACE_MS 내에 run A 가 정상 `end` 로 마감\n\
  \   → `clearPendingLifecycleError(A)` (line 664) → 이 경우는 clean.\n3. 혹은 run A 가 `end`\
  \ 이벤트 없이 sweeper 에서 sessionMode TTL 또는 archiveAtMs 로 삭제됨\n   (line 563/593). sweeper\
  \ 는 run 삭제 직전에 `clearPendingLifecycleError(runId)`\n   를 호출하므로 보통 clean.\n4. 그러나\
  \ **다른 pendingLifecycleError 엔트리 B** (error 이벤트는 수신되었지만 대응\n   subagentRuns 엔트리가\
  \ 다른 경로 — 예: `reconcileOrphanedRun()` line 442-452,\n   `replaceSubagentRunAfterSteer()`\
  \ — 로 이미 삭제됨) 는 sweeper 의 run-삭제 경로\n   `clearPendingLifecycleError` 를 거치지 않는다.\
  \ 또한 B 의 15s grace timer callback (line\n   245-253) 도 entry 부재 시 early-return 하여\
  \ `pendingLifecycleErrorByRunId.delete` 가\n   실행되지 않는다 (이 부분은 FIND-001 에서 다룬 grace-period\
  \ 누출).\n5. 이제 같은 sweep cycle 에서 sweeper 가 마지막 run A 를 삭제하고 `subagentRuns.size ===\
  \ 0`\n   를 만든 뒤, line 604-608 에서 B 의 5분 TTL 아직 도달 않음 → cleanup skip.\n6. Line 613-614\
  \ 에서 `stopSweeper()` 호출 → setInterval clear.\n7. **그 시점부터 sweeper 는 정지**. B 의 timer\
  \ 는 eventually fire 하여 entry 가 존재하지\n   않음을 확인하고 early-return (line 247-248). 그러나\
  \ map 의 entry B 는 여전히 Map 안에\n   남아 있다 (step 4 의 orphan 상태 유지).\n8. 다음에 `registerSubagentRun()`\
  \ 또는 restore 경로 (line 500) 가 발생해\n   `startSweeper()` 가 재시작될 때까지 B 의 orphaned 엔트리\
  \ (timer ref + error string +\n   endedAt number) 는 메모리 상주.\n9. session-free 워크로드\
  \ (예: 하루 1건 sub-agent, 나머지 시간 idle) 에서는 이 상주 기간이\n   수시간~수일에 달할 수 있다.\n"
root_cause_chain:
- why: 왜 sweeper 가 pendingLifecycleError 정리 책임을 가진 채 스스로 멈출 수 있는가?
  because: line 613-614 의 중지 조건 `subagentRuns.size === 0` 은 **오직 run Map** 의 크기만 본다.
    `pendingLifecycleErrorByRunId.size` 는 고려하지 않으므로, pending error 엔트리가 남아있어도 sweeper
    가 멈춘다.
  evidence_ref: src/agents/subagent-registry.ts:613-614
- why: 왜 sweeper 정지 시 pending error map 을 강제로 비우지 않는가?
  because: '`stopSweeper()` (line 533-539) 는 `clearInterval(sweeper)` 만 수행하고 `pendingLifecycleErrorByRunId`
    나 그 내부 timer 를 전혀 건드리지 않는다. 반면 test-only `resetSubagentRegistryForTests` (line
    748-773) 는 `clearAllPendingLifecycleErrors()` 를 호출하지만 프로덕션 경로에서는 동일 로직이 없다.'
  evidence_ref: src/agents/subagent-registry.ts:533-539
- why: 왜 sweeper 가 재시작되지 않으면 cleanup 이 영원히 일어나지 않는가?
  because: '`startSweeper()` 는 오직 `registerSubagentRun()` (run-manager line 356),
    `replaceSubagentRunAfterSteer()` (run-manager line 259), `restoreSubagentRunsOnce()`
    (line 500) 에서만 호출된다. 새 subagent run 이 생기지 않는 동안 sweeper 는 dead 상태. 또한 line 604-608
    의 TTL cleanup 은 오직 sweeper cycle 안에서만 실행되므로, sweeper 없이는 5분 TTL 도 무의미하다.'
  evidence_ref: src/agents/subagent-registry.ts:500,520-531
- why: 왜 listener 는 orphaned pending 을 청소할 수 없는가?
  because: '`ensureListener()` 의 콜백은 `subagentRuns.get(evt.runId)` 가 undefined 이면
    phase===''end'' 외에 early-return 한다 (line 632-638). 이미 run 이 삭제된 B 에 대해 뒤늦게 lifecycle
    이벤트가 오더라도 `clearPendingLifecycleError(B)` 가 호출되는 경로는 start/complete 시 (line 640,
    664) 뿐이며, 해당 phase 는 도달하지 않는다.'
  evidence_ref: src/agents/subagent-registry.ts:632-638
impact_hypothesis: memory-growth
impact_detail: '정성: 각 orphaned entry 는 pending record (약 80-200 B: timer ref + endedAt
  + optional error

  string) 를 보유한다. Timer 는 `timer.unref?.()` 로 unref 되어 있으므로 프로세스 종료는 막지

  않지만, Node.js timers heap 과 Map bucket 을 계속 점유한다.


  정량 추정: long-running openclaw 세션 (수일) 에서 하루당 1~5건의 transient error 가 orphan

  경로에 들어간다 가정하면 일간 ~100 bytes × 5 = 500 bytes 순증. 일 별로는 작지만 sweeper

  재시작이 **결코 일어나지 않는** 워크로드 (예: sub-agent 1회 실행 후 idle) 에서는 5분 TTL

  cleanup 이 발동 못 해 endless accumulation.


  심각도 측면에서 더 결정적인 것은 `NodeJS.Timeout` object 자체가 대응 closure (schedule

  callback 의 scope 에서 참조하는 `completeSubagentRun`, `subagentRegistryDeps` 등) 를

  retain 한다는 점이다. 모듈 scope 전역 참조이므로 heap 크기에 미치는 즉각적 영향은 적지만,

  이것이 축적되면 timers heap 정렬 비용 (O(log N)) 이 다른 타이머 연산에 부하로 번진다.

  '
severity: P2
counter_evidence:
  path: src/agents/subagent-registry.ts
  line: 748-757
  reason: "`resetSubagentRegistryForTests()` 는 `stopSweeper()` (line 762) 와\n`clearAllPendingLifecycleErrors()`\
    \ (line 757) 를 함께 호출하지만 **테스트 전용**\n경로다 (함수 이름 `*ForTests`). 프로덕션 shutdown 경로에서\
    \ 동일 cleanup 을 수행하는\n코드를 다음 Grep 으로 탐색:\n\n  rg -n \"stopSweeper\\(\" src/agents/\
    \  → 매치 2건 (line 614 self-stop, line 762 testReset)\n  rg -n \"clearAllPendingLifecycleErrors\"\
    \ src/agents/  → 매치 2건 (line 236 defn, 757 testReset)\n  rg -n \"process\\.on\\\
    (.SIGTERM|SIGINT.|beforeExit\" src/agents/subagent-registry*.ts  → 매치 0건\n\n결론:\
    \ 프로덕션 shutdown 경로에서 pendingLifecycleError map 을 정리하는 코드 없음.\n또한 sweeper 자체도 `subagentRuns.size\
    \ === 0` 에서 self-stop 후 재시작 조건이\n**새 run 등록** 에만 묶여 있으므로, idle 기간이 길수록 누출 창이 길어진다.\n\
    \n추가로 `stopSweeper()` 구현 (line 533-539) 을 읽어보면 pending map 정리 로직이\n전혀 없음을 확인:\
    \ `clearInterval` + `sweeper = null` 만 수행.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
cross_refs:
- FIND-agents-registry-memory-001
rejected_reasons:
- 'B-1-3: title exceeds 80 chars'
---
# sweeper self-stop strands pendingLifecycleError entries indefinitely

## 문제

`sweepSubagentRuns()` 는 cycle 마지막에 `subagentRuns.size === 0` 이면 `stopSweeper()`
를 호출해 자신을 종료시킨다 (line 613-614). 그러나 같은 판단에서 `pendingLifecycleErrorByRunId`
의 크기나 TTL 상태는 고려하지 않는다.

결과: pending error 엔트리가 아직 5분 TTL 도달 전이면서 subagentRuns 는 비었을 때,
sweeper 가 멈춘 뒤 **다음에 누군가 `startSweeper()` 를 호출할 때까지** 해당 엔트리들이
영구히 메모리에 남는다. `startSweeper()` 는 `registerSubagentRun`, `replaceSubagentRunAfterSteer`,
`restoreSubagentRunsOnce` 에서만 호출되므로, **idle workloads** 에서는 재시작 자체가
일어나지 않을 수 있다.

FIND-001 이 "15s → 5min gap" 이라는 시간 창을 다뤘다면, 이 FIND 는 "5min TTL 자체가 무력화
되는 조건부 무기한 누출" 을 다룬다. 두 이슈는 직렬적으로 연결되어 있으며 (FIND-001 에서
만들어진 orphan 이 FIND-002 에서 영구화) cross-refs 로 묶는다.

## 발현 메커니즘

### 시퀀스

```
t0    : register run A + run B
t1    : lifecycle error for B → schedulePendingLifecycleError(B)
         → pendingLifecycleErrorByRunId.set(B, {timer=T, endedAt=t1, error})
t2    : reconcileOrphanedRun(B) or replaceSubagentRunAfterSteer
         → subagentRuns.delete(B)  [clearPendingLifecycleError NOT called here]
t3    : run A finishes normally, sweeper run:
         - subagentRuns.delete(A)  (archiveAtMs exceeded)
         - pendingLifecycleError B: t3 - t1 < 5min → skip
         - subagentRuns.size === 0 → stopSweeper()  ← sweeper dies
t4=t1+15s : timer T fires
         - pending = map.get(B) → exists (still there from t1)
         - pending.timer === T → ok, passes guard
         - map.delete(B)  ← OK, executed (line 250)
         - entry = runs.get(B) → undefined → return
         → timer 자체는 자신의 entry 를 지웠으므로 B 는 map 에서 정리됨

         ** 하지만 위는 entry.timer === T 인 경우만. **
         FIND-001 경로에서 map entry 가 clearPendingLifecycleError 로 교체되어
         timer != T 이면 line 247 `pending.timer !== timer` 에서 early-return,
         map 에서 삭제되지 않음 → B 영구 잔존.
```

### 영구화의 이중 경로

1. **B 의 map entry 가 T 의 fire 이전에 다른 schedulePendingLifecycleError 로 덮어쓰기**
   되어 새 timer T' 가 들어있는 경우: T 의 callback 은 `pending.timer !== timer` (line 247)
   에서 early return. map 에서 B 를 지우지 않음. 이 시점에 이미 sweeper 가 stopped 되어
   있다면 → T' 의 fire 가 정상 경로로 정리하더라도 그 시점의 fire 자체가 sweeper 가 없는
   상태에서 runs 부재 확인만 하고 early-return (FIND-001 경로). 결국 잔존.

2. **sweeper self-stop 타이밍**: line 613-614 의 stopSweeper 체크는 current cycle 안에서
   `pendingLifecycleErrorByRunId.size > 0` 인 것을 무시. 즉 TTL 미도달 pending 이 있어도
   runs.size === 0 이면 중지.

## 근본 원인 분석

### (1) sweeper 종료 조건의 불완전성

`sweepSubagentRuns` 의 종료 조건 (line 613-614):

```ts
if (subagentRuns.size === 0) {
  stopSweeper();
}
```

이 조건은 pending map 크기나 TTL 잔여 시간을 보지 않는다. 한 cycle 안에서 run 이 모두
삭제되고 동시에 pending 엔트리가 TTL 미도달이면, sweeper 가 그 pending 을 회수하기 위한
추가 cycle 을 **스스로 거부**한다.

### (2) `stopSweeper` 의 부분 cleanup

`stopSweeper()` (line 533-539) 는:

```ts
function stopSweeper() {
  if (!sweeper) return;
  clearInterval(sweeper);
  sweeper = null;
}
```

`pendingLifecycleErrorByRunId` 의 timer 들을 `clearTimeout` 하거나 map 을 비우는 코드가
없다. 따라서 sweeper 정지 시점의 pending 엔트리들은 그대로 방치된다.

### (3) 프로덕션 shutdown 경로의 부재

Grep 결과 (R-3):
- `rg -n "process\.on\(.SIGTERM|SIGINT.|beforeExit" src/agents/subagent-registry*.ts` → **0 매치**
- `clearAllPendingLifecycleErrors` 는 오직 line 757 (`resetSubagentRegistryForTests`) 에서만
  호출됨.

프로덕션 프로세스가 정상 종료할 때 pending map 을 비우는 경로가 없으므로, process lifetime
이 매우 긴 장시간 실행 세션에서 누적.

### (4) `startSweeper` 재시작 조건의 희소성

`startSweeper()` 호출 위치 (R-3 Grep):
- `subagent-registry.ts:500` (`restoreSubagentRunsOnce` — 프로세스 startup/restart 경로)
- `subagent-registry-run-manager.ts:259` (`replaceSubagentRunAfterSteer`)
- `subagent-registry-run-manager.ts:356` (`registerSubagentRun` — 새 서브에이전트 등록)

idle 워크로드 (예: 사용자가 subagent 를 하루 1회만 사용) 에서는 재시작 대기 시간이 수시간
~ 수일에 달한다.

## 영향

### 정량 시나리오 A: idle

- 사용자가 오전 9시 subagent 1회 실행 → transient error → orphaned pending entry 생성.
- 오전 9시 10분: sweeper cycle, subagent run 만 정리, pending 은 TTL 미도달 (<5min 아님)
  또는 FIND-001 경로로 timer mismatch → 잔존.
- 오전 9시 10분 직후 `subagentRuns.size === 0` → stopSweeper.
- 오후 6시 다음 subagent 실행까지 **9시간 동안** orphaned pending 엔트리와 timer 메모리 점유.

### 정량 시나리오 B: 지속 사용

- 업무 시간대 평균 10건/시간 subagent, error rate 1% → 시간당 0.1건 orphan 생성.
- sweeper 가 매 cycle active → 5분 TTL 내 대체로 정리.
- 그러나 `replaceSubagentRunAfterSteer` 경로에서 만들어진 orphan 은 pending.timer 와 map
  entry 의 timer 불일치 케이스 (FIND-001) 를 통해 일부가 5분 TTL 까지 잔존.

### 정량 추정

엔트리당 메모리 점유:
- map entry overhead: ~48 B (Node.js V8 Map bucket)
- pending object: ~32 B (3 properties)
- error string: ~0-500 B
- NodeJS.Timeout object: ~~100 B + internal timers heap node

총 ~150-800 B/entry. 누적 100건 시 ~15-80 KB. 큰 양은 아니나 자료구조 성장 방향이
"무제한" 이라는 점이 invariant 이슈.

### 부가 영향

NodeJS timer heap 안정성: unref'd timer 라도 heap 에 유지됨. 수천 개 누적 시 timer insertion
/ fire 시 `O(log N)` 비용 증가. 다른 hot timer (예: setInterval sweeper 자체는 stopped
상태이므로 해당 없음, 그러나 resumeRetryTimers 등) 의 스케줄링 지연 가능.

## 반증 탐색

### 1. 프로덕션 shutdown 경로 (graceful cleanup) 존재 여부

R-3 Grep:
```bash
rg -n "process\.on\(.SIGTERM|SIGINT.|beforeExit" src/agents/subagent-registry*.ts
# 결과: 매치 없음
rg -n "stopSweeper\(" src/agents/
# 결과:
#   subagent-registry.ts:614      (self-stop inside sweepSubagentRuns)
#   subagent-registry.ts:762      (resetSubagentRegistryForTests)
```

→ 프로덕션 shutdown hook 부재. Test-only reset 만 존재.

### 2. `stopSweeper` 내부 pending cleanup 확인

Read (line 533-539): `clearInterval` + `sweeper = null` 만 수행. pending 관련 로직 없음.

### 3. listener 기반 recovery 가능성

line 632-638:
```ts
const entry = subagentRuns.get(evt.runId);
if (!entry) {
  if (phase === "end" && typeof evt.sessionKey === "string") {
    await refreshFrozenResultFromSession(evt.sessionKey);
  }
  return;
}
```

entry 가 없으면 `clearPendingLifecycleError` 를 호출하는 경로가 없다. 즉 뒤늦게 event 가
도착해도 pending 은 청소되지 않음.

### 4. 기존 테스트 커버리지

```bash
rg -n "subagentRuns\.size.*=== 0|stopSweeper" src/agents/subagent-registry*.test.ts
# 결과: 테스트 파일들이 resetSubagentRegistryForTests 를 호출하는 것만 확인, sweeper
# self-stop 이후 pending 잔존 시나리오를 검증하는 테스트 없음.
```

### 5. 엔트리 단기 cleanup 경로

다른 경로에서 orphaned pending 을 청소하는 코드를 탐색:

```bash
rg -n "pendingLifecycleErrorByRunId\.(delete|clear)" src/agents/
# subagent-registry.ts:233  (clearPendingLifecycleError — run-scoped)
# subagent-registry.ts:240  (clearAllPendingLifecycleErrors — test-only)
# subagent-registry.ts:250  (timer callback — grace-period, FIND-001 경로)
```

Run-scoped cleanup 은 runId 기반. sweeper-stopped 상태에서는 외부 트리거 (명시적 cleanup
호출) 없이 orphan 이 사라지지 않는다.

### 6. 주석으로 의도된 설계 여부 확인

Read (line 603): `// Sweep orphaned pendingLifecycleError entries (absolute TTL).` — 절대
TTL 이 sweeper cycle 내부에서만 의미가 있음을 인지한 주석. 그러나 sweeper self-stop 과의
상호작용을 고려한 주석은 찾지 못함.

## Self-check

### 내가 확실한 근거

- **Line 613-614**: `if (subagentRuns.size === 0) { stopSweeper(); }` — sweeper self-stop
  조건이 pending map 을 무시.
- **Line 533-539**: `stopSweeper()` 가 `clearInterval` 만 수행, pending 정리 없음.
- **Line 500, run-manager 259/356**: `startSweeper()` 재시작은 새 run 등록 또는 restore
  에서만 발생.
- **Line 604-608**: 5분 TTL cleanup 은 **sweeper cycle 내에서만** 실행됨. sweeper stopped
  상태에서는 TTL 이 의미 없음.
- **Line 632-638**: listener 가 entry 부재 시 pending 정리 경로 없음.

### 내가 한 가정

1. `reconcileOrphanedRun`, `replaceSubagentRunAfterSteer` 같은 경로에서 `subagentRuns.delete`
   이후 `clearPendingLifecycleError` 가 호출되지 않는다고 가정.
   - 근거: FIND-001 의 reviewer comment (line 442-452 확인 주석).
2. idle 워크로드에서 새 subagent 등록이 수시간 넘게 발생하지 않을 수 있다고 가정.
   - 근거: 개인 사용자 시나리오 (VS Code 기반 CLI 사용 패턴).
3. `timer.unref?.()` 가 적용되어도 timer object 와 closure 는 재고 (heap retained) 된다고
   가정.
   - 근거: Node.js docs — `unref` 는 event loop liveness 에만 영향, GC 에 영향 없음.

### 확인 안 한 것 중 영향 가능성

1. **프로덕션 graceful shutdown 경로**: 상위 orchestrator (e.g. gateway main loop) 가
   process exit 전에 registry 를 명시적으로 비우는 API 를 호출하는지 allowed_paths 밖에서
   확인 필요.

2. **누적 속도의 실제 측정**: 프로덕션 telemetry 에서 `pendingLifecycleErrorByRunId.size`
   의 시계열을 추적하는 지표가 있는지 미확인.

3. **`replaceSubagentRunAfterSteer` 경로의 pending clear 여부**: allowed_paths 내
   `subagent-registry-run-manager.ts` 는 범위 밖이지만, 이 경로에서 old runId 에 대한
   `clearPendingLifecycleError` 호출 여부를 확인하지 않았다. 만약 호출된다면 시나리오 B 의
   일부가 완화될 수 있다.

4. **테스트 커버리지 밖**: sweeper self-stop 이후 새 run 등록까지 10분 이상 경과 시점에서
   pending entry 가 여전히 존재하는지 검증하는 integration test 미확인.

### 체크리스트 보고

- [x] applied — 무제한 자료구조 성장 탐색 (sweeper self-stop → pending map 영구 상주 1건 발견)
- [x] applied — EventEmitter 리스너 누수 탐색 (listener `ensureListener` 는 중복 시작 방지,
  `listenerStop` cleanup 함수 존재; `.on` 는 subagentRegistryDeps.onAgentEvent 의 unsubscribe
  handle 을 반환받는 구조. 누수 없음)
- [x] applied — 캐시 TTL 부재 (pending map TTL 은 5분 존재하나 sweeper 정지 시 무력화 —
  본 FIND 의 핵심 issue)
- [ ] skipped — 핸들/리소스 누수 (fs/HTTP 핸들 없음, 이 파일은 Map/Set/Timer 만 다룸)
- [ ] skipped — 강한 참조 체인 (timer closure 의 `completeSubagentRun` retain 은 본 FIND 의
  mechanism 에 포함됨. 별도 FIND 로 분리 가치 낮음)
