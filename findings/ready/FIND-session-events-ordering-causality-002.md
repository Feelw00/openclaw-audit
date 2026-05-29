---
id: FIND-session-events-ordering-causality-002
cell: session-events-ordering-causality
title: lifecycle 이벤트 seq/version 토큰 부재로 지연 create 가 ended 세션에 reason:create 발행
file: src/sessions/session-lifecycle-events.ts
line_range: 20-28
evidence: "```ts\nexport function emitSessionLifecycleEvent(event: SessionLifecycleEvent):\
  \ void {\n  for (const listener of SESSION_LIFECYCLE_LISTENERS) {\n    try {\n \
  \     listener(event);\n    } catch {\n      // Best-effort, do not propagate listener\
  \ errors.\n    }\n  }\n}\n```\n"
symptom_type: ordering-causality-gap
problem: SessionLifecycleEvent 는 sessionKey/reason 만 싣고 seq/version/timestamp 같은 순서
  토큰이 없다. emit 은 전역 Set 동기 fan-out 이고, 소비자(server-runtime-subscriptions.ts:98-100)는
  void getHandler().then(h=>h(evt)) 로 모든 처리를 microtask 로 defer 한다. 지연 도착한 reason:"create"
  가 이미 ended 된 세션에 대해 reason:"create" 를 브로드캐스트해도 소비자가 staleness 를 감지/거부할 토큰이 없다.
mechanism: '1. subagent-spawn.ts:1350 가 reason:"create" 를 emit (자식 세션 생성 시).

  2. subagent-registry-lifecycle.ts:1106 가 reason:"ended" 류를 emit (세션 종료 시).

  3. 두 emit 은 서로 다른 async 컨텍스트에서 발생 - 동일 producer FIFO 가 아니다.

  4. 소비자는 onSessionLifecycleEvent((evt)=>void getLifecycleEventHandler().then(h=>h(evt)))
  로 처리를 defer.

  5. 이벤트에 seq/version 이 없어, create 가 ended 보다 늦게 처리되는 경로가 생기면 소비자는 reason:"create"
  를 ended 세션에 대해 브로드캐스트한다. 거부할 가드가 없다.

  '
root_cause_chain:
- why: 왜 소비자가 stale lifecycle 이벤트를 거부할 수 없는가
  because: 이벤트 payload 에 단조 seq/version 토큰이 전혀 없다
  evidence_ref: src/sessions/session-lifecycle-events.ts:1
- why: 왜 순서가 미보장인가
  because: emit 은 동기 fan-out 이나 소비자가 void ...then() 으로 비동기 defer 하고, 두 reason 이 서로
    다른 async 컨텍스트에서 emit 된다
  evidence_ref: src/gateway/server-runtime-subscriptions.ts:98
- why: 왜 reason 이 stale 해도 브로드캐스트되는가
  because: emit 은 무조건(unconditional) listener 호출 - 상태/순서 검사 없음
  evidence_ref: src/sessions/session-lifecycle-events.ts:21
- why: 왜 영향이 reason 주석에 국한되는가
  because: 소비자 핸들러가 loadGatewaySessionRow 로 snapshot 을 fresh 재조회하므로 권위 상태는 current
    - reason 라벨만 인과 역전
  evidence_ref: src/gateway/server-session-events.ts:195
impact_hypothesis: wrong-output
impact_detail: '정성: sessions.changed SSE 이벤트의 reason 필드가 인과 역전될 수 있다. 지연 reason:"create"
  가 이미 종료된 세션에 대해 broadcast 되면 클라이언트 UI 가 죽은 세션을 잠깐 "생성됨/활성" 으로 표시. 다만 동봉 snapshot(buildGatewaySessionSnapshot)은
  loadGatewaySessionRow 로 fresh 조회되어 권위 상태는 정확 - 라벨/transient UI 만 영향. single-process
  에서는 microtask FIFO 가 emit 순서를 대체로 보존하므로 역전 창은 좁다. 멀티 트랜스포트/재진입 spawn 시 현실화 가능.'
severity: P3
counter_evidence:
  path: src/gateway/server-session-events.ts
  line: 194-199
  reason: 'authoritative-state-protected: lifecycle 핸들러가 loadGatewaySessionRow(event.sessionKey)
    로 세션 snapshot 을 fresh 재조회한다(:195). 따라서 stale reason:"create" 가 와도 동봉 상태는 current(ended).
    reason 라벨만 인과적으로 어긋남. 또 single-process 에서 void ...then() 의 microtask 콜백은 emit
    등록 순서(FIFO)대로 실행되므로 동일-emit-source 순서는 보존됨 - 역전은 두 reason 이 서로 다른 async 컨텍스트에서
    wall-clock 역순으로 emit 될 때만. terminal 보호 grep(rg terminal|reactivat|status.*running
    src/sessions): none_found - 세션 상태 mutate 는 sessions 스코프 밖(gateway/store)에 있어 본
    emit 은 상태를 직접 덮지 않음(주석만).'
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
---
# lifecycle 이벤트 seq/version 토큰 부재로 지연 create 가 ended 세션에 reason:create 발행

## 문제

`SessionLifecycleEvent`(session-lifecycle-events.ts:1-7)는 `sessionKey`, `reason`,
`parentSessionKey`, `label`, `displayName` 만 갖고 단조 `seq`/`version`/`timestamp`
같은 순서 토큰이 전혀 없다. `emitSessionLifecycleEvent`(:20-28)는 전역
`SESSION_LIFECYCLE_LISTENERS` Set 에 무조건(unconditional) 동기 fan-out 하고,
소비자는 이를 비동기로 defer 한다(server-runtime-subscriptions.ts:98-100). 결과적으로
소비자는 lifecycle 이벤트가 인과적으로 stale 한지 판별할 수단이 없다.

## 발현 메커니즘

1. `subagent-spawn.ts:1350` 가 자식 세션 생성 시 `reason: "create"` emit.
2. `subagent-registry-lifecycle.ts:1106` 가 세션 종료 시 `reason: "ended"` 류 emit.
3. 두 emit 은 서로 다른 async 컨텍스트(spawn 흐름 vs registry 정리 흐름)에서 발생 -
   동일 producer FIFO 큐가 아니다.
4. 소비자는
   `onSessionLifecycleEvent((evt) => void getLifecycleEventHandler().then(h => h(evt)))`
   로 *모든* 처리를 microtask 로 defer 한다(server-runtime-subscriptions.ts:98).
5. 이벤트에 순서 토큰이 없으므로, `create` 가 `ended` 보다 늦게 처리되는 경로가
   생기면 소비자는 `reason: "create"` 를 이미 종료된 세션에 대해 broadcast 한다.
   `createLifecycleEventBroadcastHandler`(server-session-events.ts:180-204)에는
   이를 거부할 staleness/seq 가드가 없다.

## 근본 원인 분석

- **1단계 (토큰 부재)**: 이벤트 타입(session-lifecycle-events.ts:1-7)에 seq/version
  필드가 없어 소비자가 인과 순서를 복원할 수 없다.
- **2단계 (defer 로 인한 순서 미보장)**: emit 자체는 동기 fan-out 이나 소비자가
  `void ...then()` 으로 비동기화하고(server-runtime-subscriptions.ts:98), 두 reason 이
  독립 async 컨텍스트에서 나므로 wall-clock 역전 가능.
- **3단계 (무조건 호출)**: `emitSessionLifecycleEvent`(:21)는 상태/순서 검사 없이
  listener 를 무조건 호출한다 - last-emitter-wins.
- **4단계 (영향 국한)**: 다행히 핸들러가 `loadGatewaySessionRow`
  (server-session-events.ts:195)로 snapshot 을 fresh 재조회하므로 권위 상태는
  current 다. 인과 역전은 `reason` 주석 라벨로 한정된다.

## 영향

`impact_hypothesis: wrong-output`. `sessions.changed` SSE 이벤트의 `reason` 필드가
인과적으로 역전될 수 있다. 지연된 `reason: "create"` 가 이미 종료된 세션에 대해
broadcast 되면 클라이언트 UI 가 죽은 세션을 일시적으로 "생성됨/활성" 으로 표시할 수
있다.

다만 동봉되는 snapshot(`buildGatewaySessionSnapshot`)은 `loadGatewaySessionRow` 로
fresh 조회되어 권위 상태는 정확하다 - 따라서 영향은 transient UI 라벨에 한정되며
영구 stuck/데이터 유실이 아니다. single-process 환경에서는 microtask FIFO 가 emit
순서를 대체로 보존하므로 역전 창이 좁다. 멀티 트랜스포트나 재진입 spawn(create →
즉시 expiry/ended) 시 현실화 가능. 이 때문에 P3 로 분류.

## 반증 탐색

- **권위 상태 보호 (authoritative-state-protected)**: lifecycle 핸들러가
  `loadGatewaySessionRow(event.sessionKey)` 로 세션 snapshot 을 매번 fresh
  재조회한다(server-session-events.ts:195). 그래서 stale `reason:"create"` 가 와도
  동봉 상태는 current(ended). 인과 역전은 `reason` 라벨에만 남는다 - 이것이 P3 로
  낮춘 핵심 이유.
- **microtask FIFO 부분 보호**: single-process 에서 `void getHandler().then(...)` 의
  콜백은 동일 (resolved/pending) 핸들러 promise 에 등록 순서대로 attach 되어
  emit 순서(FIFO)를 보존한다. 역전은 두 reason 이 *서로 다른* async 컨텍스트에서
  wall-clock 역순으로 emit 될 때만.
- **terminal 보호 grep**: `rg -n "terminal|reactivat|status.*running|endedAt"
    src/sessions/` → none_found. 세션 상태의 실제 mutate 는 sessions 스코프 밖
  (gateway/store)에 있어 본 emit 은 상태를 직접 덮지 않는다(이벤트는 알림 채널일
  뿐). 즉 "ended 세션을 running 으로 박제" 류 영구 stuck 은 본 경로에서 발생 안 함.
- **기존 테스트**: session-lifecycle-events.test.ts:18-55 는 emit/listener 등록과
  throw swallow 를 검증하나 순서/인과는 검증 안 함 - 순서 보장이 의도된 invariant 로
  못박힌 곳 없음.

## Self-check

### 내가 확실한 근거
- 이벤트 타입에 seq/version 부재(session-lifecycle-events.ts:1-7) 직접 확인.
- emit 의 무조건 동기 fan-out(:20-28)과 소비자의 비동기 defer
  (server-runtime-subscriptions.ts:98-100) 직접 확인.
- 핸들러의 fresh snapshot 재조회(server-session-events.ts:195) 직접 확인 -
  영향이 reason 라벨로 국한됨의 근거.

### 내가 한 가정
- create 와 ended 가 wall-clock 역순으로 emit 되는 production 타임라인이 실재한다 -
  spawn-then-immediate-expiry 같은 경로를 추정했으나 정확한 시퀀스는 trace 안 함.
- single-process FIFO 가 대부분 보존한다는 판단 - V8 microtask 의미론에 의존,
  멀티 트랜스포트 시 깨질 수 있음.

### 확인 안 한 것 중 영향 가능성
- 클라이언트(웹 UI)가 `reason` 라벨을 어떻게 소비하는지 미확인 - reason 만으로
  지속 상태를 갱신한다면 영향이 P3 보다 클 수 있음(snapshot 무시 시).
- subagent-registry-lifecycle.ts 의 ended emit 이 실제로 spawn 의 create emit 보다
  먼저 스케줄될 수 있는 정확한 retry/expiry 타이밍은 미검증.
