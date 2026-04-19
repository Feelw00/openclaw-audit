---
id: FIND-gateway-memory-003
cell: gateway-memory
title: '`agentRunStarts` cleanup 이 lifecycle end/error 이벤트에만 의존 (TTL/cap 부재)'
file: src/gateway/server-methods/agent-job.ts
line_range: 99-137
evidence: "```ts\nfunction ensureAgentRunListener() {\n  if (agentRunListenerStarted)\
  \ {\n    return;\n  }\n  agentRunListenerStarted = true;\n  onAgentEvent((evt) =>\
  \ {\n    if (!evt) {\n      return;\n    }\n    if (evt.stream !== \"lifecycle\"\
  ) {\n      return;\n    }\n    const phase = evt.data?.phase;\n    if (phase ===\
  \ \"start\") {\n      const startedAt = typeof evt.data?.startedAt === \"number\"\
  \ ? evt.data.startedAt : undefined;\n      agentRunStarts.set(evt.runId, startedAt\
  \ ?? Date.now());\n      clearPendingAgentRunError(evt.runId);\n      // A new start\
  \ means this run is active again (or retried). Drop stale\n      // terminal snapshots\
  \ so waiters don't resolve from old state.\n      agentRunCache.delete(evt.runId);\n\
  \      return;\n    }\n    if (phase !== \"end\" && phase !== \"error\") {\n   \
  \   return;\n    }\n    const snapshot = createSnapshotFromLifecycleEvent({\n  \
  \    runId: evt.runId,\n      phase,\n      data: evt.data,\n    });\n    agentRunStarts.delete(evt.runId);\n\
  \    if (phase === \"error\") {\n      schedulePendingAgentRunError(snapshot);\n\
  \      return;\n    }\n    clearPendingAgentRunError(evt.runId);\n    recordAgentRunSnapshot(snapshot);\n\
  \  });\n}\n```\n"
symptom_type: memory-leak
problem: '`agentRunStarts` 는 `phase === "start"` 이벤트에서 `set`, `phase === "end"` 또는
  `"error"` 이벤트에서만 `delete` 된다. lifecycle end/error 이벤트가 어떠한 이유로든 발사되지 않는 경로 (runner
  프로세스 강제 종료, agent-events pipeline 실패, start 이후 handleAgentEnd 진입 전 상위 throw) 에서는
  엔트리가 영구히 남는다. `agentRunCache` 와 달리 TTL prune / cap 안전장치가 없다.'
mechanism: "1. 에이전트 run 시작 → `handleAgentStart` (pi-embedded-subscribe.handlers.lifecycle.ts:23-37)\
  \ 가\n   `emitAgentEvent({ runId, stream: \"lifecycle\", data: { phase: \"start\"\
  , startedAt } })` 호출.\n2. gateway 의 `ensureAgentRunListener` 가 event 수신 → `agentRunStarts.set(evt.runId,\
  \ startedAt)`\n   (L114). 이 시점에서 엔트리 생성.\n3. 정상 종료 시 `handleAgentEnd` 가 `emitLifecycleTerminalOnce`\
  \ 를 try/finally 로 호출하여\n   `{ phase: \"end\" }` 또는 `{ phase: \"error\" }` 이벤트 발사\
  \ → listener 가 L129\n   `agentRunStarts.delete(evt.runId)` 수행.\n4. 그러나 다음 경로에서는\
  \ end/error 이벤트가 발사되지 않음:\n   - runner 프로세스 OOM-kill / SIGKILL (try/finally 실행 불가)\n\
  \   - `emitAgentEvent` 자체가 listener callback 실행 중 throw (내부 subscribe 리스트 일부가\n\
  \     rejection 을 전파하는 경우 — infra/agent-events.ts 의 구현에 의존)\n   - `onAgentEvent`\
  \ 에 등록된 이 listener 가 callback 내부에서 synchronous throw 하는 경우\n     (L112-135 의 어느\
  \ 한 줄이 예기치 않게 throw). 본 callback 은 대부분 defensive 이지만\n     `createSnapshotFromLifecycleEvent`\
  \ (L79-97) 가 data 필드 접근 중 throw 가능성 — data 가\n     `null` 이어도 optional chaining\
  \ 으로 방어되나 unexpected type 이면 coerce 실패.\n5. 엔트리가 남으면 다른 정리 경로 부재. `agentRunCache`\
  \ 는 `pruneAgentRunCache` (L31-37) 가\n   TTL=10min 으로 evict 하지만, `agentRunStarts`\
  \ 에 해당하는 prune 함수가 없다.\n"
root_cause_chain:
- why: 왜 agentRunStarts 는 TTL prune 이 없는가?
  because: 이 맵의 목적은 "agent 가 active 인 동안의 startedAt timestamp 를 lookup 하기 위함" 으로,
    정상 flow 에서는 start 와 end 가 항상 페어링된다는 가정에 기반. 따라서 design-time 에는 safety belt 가 불필요해
    보였다. 그러나 예외 경로 (process kill, event pipeline 실패) 를 고려하면 fallback 이 필요.
  evidence_ref: src/gateway/server-methods/agent-job.ts:99-137
- why: 왜 agentRunCache 는 prune 이 있고 agentRunStarts 는 없는가?
  because: agentRunCache 는 snapshot 결과를 장기 저장 (waiters 가 과거 결과 조회) 하므로 TTL=10min 이
    명시적. agentRunStarts 는 "start ↔ end 페어링 중간 임시 저장" 으로 설계되어 장기 저장 가정이 없음. 즉 설계 의도는
    "곧 end 가 올 것" 인데 현실에서는 안 오는 경우가 있다.
  evidence_ref: src/gateway/server-methods/agent-job.ts:31-42
- why: 왜 CAL-001 패턴 (unconditional timer delete) 이 여기엔 없는가?
  because: 같은 파일의 `schedulePendingAgentRunError` (L53-66) 는 setTimeout callback 에서
    `pendingAgentRunErrors.delete(snapshot.runId)` 를 무조건 실행하는 safety-belt timer 가
    있다. 그러나 agentRunStarts 에는 이런 타이머가 없다 — "start event 시 N 초 후 자동 cleanup" 같은 fallback
    부재.
  evidence_ref: src/gateway/server-methods/agent-job.ts:53-66
- why: 왜 runId 마다 메모리 점유가 누적될 수 있는가?
  because: runId 는 gateway 프로세스 수명 내에서 사실상 무한한 공간 (각 agent.request 마다 새 UUID 생성).
    장기 구동 서버에서 누적된 runId 수는 서버 부팅 후 시작된 모든 run 의 수와 같다. safety belt 없이 end/error 이벤트
    실패율이 0.01% 만 되어도 days 단위에서 관측 가능한 누수.
  evidence_ref: src/infra/agent-events.ts:89
impact_hypothesis: memory-growth
impact_detail: "정량 상한 (관측치 없음):\n- 엔트리: `(runId: string, timestamp: number)` — 60~100\
  \ bytes.\n- 누수율 = (총 run 수) × (end/error 이벤트 실패율). 정상 운영에서 실패율은 매우 낮지만 0 은 아님\n\
  \  (process kill, OOM 등).\n- 100 runs/시간, 실패율 0.1% 가정: 하루 2.4 엔트리 누수 × 60B = ~150B/day.\
  \ 30일 = 4.5KB. 느리지만\n  복구 불가.\n- 더 현실적 시나리오: 공용 서버에서 동시 많은 embedded run 을 띄우는 경우\
  \ per-run rate 가 높아\n  전체 누수 속도 배가. 그래도 단일 수치는 작음 — severity 는 P3.\n"
severity: P3
counter_evidence:
  path: src/gateway/server-methods/agent-job.ts
  line: 99-137
  reason: "R-3 Grep (audit HEAD 8879ed153d):\n```\nrg -n \"agentRunStarts\\.(set|delete|clear|has|get|size)\"\
    \ src/\n  → agent-job.ts:86    .get(runId)                     (read — createSnapshotFromLifecycleEvent)\n\
    \  → agent-job.ts:114   .set(evt.runId, ...)            (on lifecycle \"start\"\
    )\n  → agent-job.ts:129   .delete(evt.runId)              (on lifecycle \"end\"\
    |\"error\")\n  # 그 외 prune/TTL/cap 대응 없음.\n\nrg -n \"pruneAgentRunStarts|agentRunStarts.*prune|agentRunStarts.*evict\"\
    \ src/\n  → match 없음.\n\nrg -n \"while.*agentRunStarts\\.size|agentRunStarts\\\
    .size\\s*>\" src/\n  → match 없음.\n\nrg -n \"setTimeout.*agentRunStarts|setInterval.*agentRunStarts\"\
    \ src/\n  → match 없음.\n```\n\nR-5 execution condition 분류 (CAL-001 recalibration):\n\
    | 경로 | 조건 | 비고 |\n|---|---|---|\n| L114 set | unconditional on phase=\"start\"\
    \ | lifecycle start 이벤트마다 |\n| L129 delete | conditional (phase===\"end\" OR \"\
    error\") | 이벤트 도착 필요 |\n| timer safety belt | 없음 | 같은 파일 `pendingAgentRunErrors`\
    \ (L53-66) 와 대조 |\n| pruneAgentRunStarts | 없음 | `pruneAgentRunCache` (L31-37,\
    \ TTL=10min) 와 대조 |\n\nPrimary-path inversion: \"누수 안 된다\" 가 참이려면 모든 \"start\"\
    \ 이벤트가 반드시 \"end\"\n또는 \"error\" 이벤트 발사로 이어져야 한다. `handleAgentEnd` 는 try/finally\
    \ 로 robust 하지만\n(pi-embedded-subscribe.handlers.lifecycle.ts:195-212), process\
    \ SIGKILL / OOM / pipeline 중간\n실패 경로를 완전히 커버하진 못한다. 또한 `onAgentEvent` listener\
    \ callback 내부의 throw 가\ndispatcher 구현에 따라 후속 listener 를 건너뛰거나 중단시킬 가능성도 있다 (agent-events.ts\n\
    구현 의존 — 본 세션에서는 allowed_paths 외부).\n\n이미 cleanup 있는지: `resetAgentRunStartsForTests`\
    \ 같은 helper 없음 (테스트 reset 경로조차\n부재). `pruneAgentRunCache` 는 **agentRunCache 만**\
    \ 정리 — `agentRunStarts` 는 터치 안 함.\n\n\"intentional\" 주석 없음: L114 / L129 부근에 \"\
    start/end pairing 가정\" 을 명시한 주석 부재.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-19'
---
# `agentRunStarts` 는 lifecycle end/error 이벤트에만 의존해 cleanup — TTL/cap safety belt 부재

## 문제

`agentRunStarts: Map<runId, startedAt>` 는 embedded agent run 의 start timestamp 를 lookup 용으로 저장한다. set 은 lifecycle `phase: "start"` 이벤트 수신 시 (L114), delete 는 `phase: "end" | "error"` 이벤트 수신 시 (L129). end/error 이벤트가 발사되지 않는 경로 — runner 프로세스 강제 종료, event pipeline 실패, listener callback 내 unexpected throw — 에서 엔트리는 영구히 남는다. 같은 파일의 `pendingAgentRunErrors` 는 15초 setTimeout 으로 unconditional delete 를 보유하지만, `agentRunStarts` 는 그 safety belt 가 부재하다 (CAL-001 의 반례 패턴).

## 발현 메커니즘

1. Embedded run 시작 → `handleAgentStart` 가 `emitAgentEvent({ phase: "start", startedAt })` 호출.
2. gateway listener (`ensureAgentRunListener`) 가 L114 에서 `agentRunStarts.set(evt.runId, ...)` 수행.
3. 정상 종료 시 `handleAgentEnd` 의 try/finally 가 `emitLifecycleTerminalOnce` 를 호출하여 `phase: "end"` 또는 `"error"` 이벤트 발사 → L129 delete.
4. 이 chain 이 깨지는 경우:
   - Runner 프로세스 OOM / SIGKILL / SIGSEGV — try/finally 실행 불가.
   - `onAgentEvent` dispatcher 가 listener callback 의 예외를 swallow 하지 않으면 다음 listener 가 start 이벤트를 놓치고 end 이벤트만 처리할 수 있음.
   - `createSnapshotFromLifecycleEvent` 가 기대하지 않은 `data` 타입 (예: runtime drift 로 `data.aborted` 가 non-boolean) 에서 throw 가능 → L129 에 도달하기 전에 throw 로 해당 이벤트 처리 실패 가능.
5. 해당 entry 는 정리되지 않고 `agentRunStarts` 에 남는다. 다른 prune/evict 경로 부재.

## 근본 원인 분석

이 맵의 설계 의도는 "start ↔ end 페어링 중간에 startedAt 을 lookup 하기 위한 임시 저장" 이며, 정상 flow 에서는 end 이벤트가 거의 항상 도착한다는 암묵적 전제에 서 있다. 같은 파일 내 `agentRunCache` 는 `pruneAgentRunCache` (L31-37) 로 TTL=10min 을 강제하고, `pendingAgentRunErrors` 는 `schedulePendingAgentRunError` 의 setTimeout callback (L61) 이 **본체 첫 줄에서 무조건** `pendingAgentRunErrors.delete(snapshot.runId)` 를 수행한다 (CAL-001 의 "unconditional timer delete" 패턴). `agentRunStarts` 는 두 safety belt 중 어느 것도 보유하지 않는다.

runId 공간은 gateway 프로세스 수명 내에서 사실상 무한 (매 run 마다 새 UUID). 따라서 end/error 이벤트 실패 경로가 0.01% 만 존재해도 장기 구동 서버에서 엔트리가 축적된다. 엔트리 크기는 작지만 복구 수단이 graceful shutdown 외에는 없다.

## 영향

- 영향 유형: **memory-growth** (매우 slow).
- 관측: `agentRunStarts.size` 가 서버 부팅 후 생성된 모든 run 수 - 정상 종료된 run 수 이상으로 증가.
- 재현: `emitAgentEvent({ phase: "start", runId: "X" })` 만 발사하고 end/error 을 발사하지 않는 synthetic test → `agentRunStarts.has("X") === true` 영속 관측 (단, `pi-embedded-subscribe.handlers.lifecycle.ts` 의 try/finally 가 robust 하므로 프로덕션에서 재현은 쉽지 않다).
- severity P3: 엔트리 크기 작음 + 실패 빈도 낮음. 그러나 예외 경로 fallback 부재는 기술 빚.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 결과 `agentRunStarts.delete` 는 L129 단일 경로. `prune|evict|clear|setTimeout.*agentRunStarts` 모두 매치 0. `pruneAgentRunCache` (L31-37) 는 `agentRunCache` 만 처리 — `agentRunStarts` 는 손 대지 않음.

**카테고리 2 (외부 경계 장치)**: `server-maintenance.ts` 의 interval 들 어디에서도 `agentRunStarts` 를 정리하지 않는다. graceful shutdown 에서 module-level clear 경로 없음 (`resetAgentRunStartsForTests` 같은 helper도 부재).

**카테고리 3 (호출 맥락)**: production hot-path 는 `handleAgentEnd` 의 try/finally 로 robust. 실패 경로는 "프로세스 강제 종료" 계열로 빈도 낮음. 그러나 0 은 아니다.

**카테고리 4 (기존 테스트)**: agent-job.test.ts / server-methods.test.ts 에서 start+end 페어링된 시나리오 다수. "start 후 end 미발사" 시나리오 커버 부재.

**카테고리 5 (주석/의도)**: "start/end pairing" 가정을 명시하는 주석 없음. safety belt 생략이 의도적이라는 표시 없음.

**Primary-path inversion (CAL-001 recalibration)**: `pendingAgentRunErrors` (동일 파일 L53-66) 는 setTimeout 본체 첫 줄에서 unconditional delete — leak 아님. `agentRunStarts` 는 그 패턴을 따르지 않음. CAL-001 의 R-5 분류표에서 `agentRunStarts` 의 delete 는 `conditional-event-dependent` (end/error event 의존), fallback 없음.

## Self-check

### 내가 확실한 근거
- `src/gateway/server-methods/agent-job.ts:12, 99-137` Read 로 확인.
- R-3 Grep 전범위 (`src/`) 로 delete/prune 경로 L129 단일 확인.
- 동일 파일 내 `pruneAgentRunCache` (L31-37), `schedulePendingAgentRunError` (L53-66) safety belt 존재 — 이 맵만 누락.
- `handleAgentEnd` (pi-embedded-subscribe.handlers.lifecycle.ts:195-212) 의 try/finally 로 정상 경로는 robust 확인.

### 내가 한 가정
- runner SIGKILL / OOM 빈도가 "0 은 아니지만 매우 낮다" — 추정. 프로덕션 관측치 없음.
- `onAgentEvent` dispatcher 의 callback throw 처리 동작 — `src/infra/agent-events.ts` (allowed_paths 외부) 직접 확인 안 함. 만약 dispatcher 가 모든 callback exception 을 catch 하여 다른 listener 로 전파하지 않는다면 이 FIND 의 frequency 는 훨씬 낮음.

### 확인 안 한 것 중 영향 가능성
- `emitAgentEvent` 가 내부에서 lifecycle end/error 을 항상 발사하도록 보장하는 higher-level invariant 의 존재 — agent-events pipeline 의 전체 구현을 scope 제한으로 trace 못함.
- process graceful shutdown 경로에서 이 맵이 리셋되는지 — 확인 안 함. launchd/systemd restart 주기가 짧으면 누수량 자연적 감소.
- `agentRunStarts.get(runId)` 호출 caller (L86 `createSnapshotFromLifecycleEvent`) 는 stale entry 를 읽어도 문제 없음 (startedAt 을 override 하는 fallback 값). 즉 누수된 entry 의 **기능적 오작동** 은 관측되지 않고 순수 메모리 영향만.
