---
id: FIND-gateway-lifecycle-002
cell: gateway-lifecycle
title: startup recovery jobs have no shutdown cancellation hook
file: src/gateway/server-runtime-services.ts
line_range: 156-190
evidence: "```ts\nfunction recoverPendingOutboundDeliveries(params: {\n  cfg: OpenClawConfig;\n\
  \  log: GatewayRuntimeServiceLogger;\n}): void {\n  void (async () => {\n    const\
  \ { recoverPendingDeliveries } = await import(\"../infra/outbound/delivery-queue.js\"\
  );\n    const { deliverOutboundPayloadsInternal } = await import(\"../infra/outbound/deliver.js\"\
  );\n    const logRecovery = params.log.child(\"delivery-recovery\");\n    await\
  \ recoverPendingDeliveries({\n      deliver: deliverOutboundPayloadsInternal,\n\
  \      log: logRecovery,\n      cfg: params.cfg,\n    });\n  })().catch((err) =>\
  \ params.log.error(`Delivery recovery failed: ${String(err)}`));\n}\n\nfunction\
  \ recoverPendingSessionDeliveries(params: {\n  deps: import(\"../cli/deps.types.js\"\
  ).CliDeps;\n  log: GatewayRuntimeServiceLogger;\n  maxEnqueuedAt: number;\n}): void\
  \ {\n  const timer = setTimeout(() => {\n    void (async () => {\n      const {\
  \ recoverPendingRestartContinuationDeliveries } =\n        await import(\"./server-restart-sentinel.js\"\
  );\n      const logRecovery = params.log.child(\"session-delivery-recovery\");\n\
  \      await recoverPendingRestartContinuationDeliveries({\n        deps: params.deps,\n\
  \        log: logRecovery,\n        maxEnqueuedAt: params.maxEnqueuedAt,\n     \
  \ });\n    })().catch((err) => params.log.error(`Session delivery recovery failed:\
  \ ${String(err)}`));\n  }, 1_250);\n  timer.unref?.();\n}\n```\n"
symptom_type: lifecycle-gap
problem: '''`activateGatewayScheduledServices` (server-runtime-services.ts:245-283)
  가 호출하는 두

  recovery 작업은 시작/정지 비대칭이다. (a) `recoverPendingOutboundDeliveries` 는 즉시 fire-and-forget

  async IIFE 로 진입, (b) `recoverPendingSessionDeliveries` 는 setTimeout(1250ms) 으로 예약된

  후 동일하게 fire-and-forget async IIFE 로 진입. 두 경로 모두 handle 또는 stop 함수를 반환하지

  않으며 호출 측 (server.impl.ts:1375-1389) 도 cancellation 메커니즘이 없다. `timer.unref()` 는

  event-loop 유지를 막을 뿐 callback 자체는 실행된다. gateway shutdown 이 activate 직후에 시작되면

  recovery 작업이 tearing-down state 에서 deps (CliDeps) / dynamic import 모듈을 사용한다.''

  '
mechanism: "'1) `activateGatewayScheduledServices` (server.impl.ts:1375) 가 startup\
  \ 후 호출되어 둘 다 fire.\n2) `recoverPendingOutboundDeliveries` 는 즉시 IIFE 진입: `await import(\"\
  ../infra/outbound/delivery-queue.js\")`\n   → `await import(\"../infra/outbound/deliver.js\"\
  )` → `await recoverPendingDeliveries(...)` 시작.\n   이 await chain 은 file I/O + 동적\
  \ import 가 포함되어 수 ms~ 수십 ms.\n3) `recoverPendingSessionDeliveries` 의 setTimeout\
  \ 은 1250ms 후 fire. callback 본체는 동일하게\n   dynamic import + recovery await.\n4) shutdown\
  \ (`runGatewayClosePrelude` + close handler) 이 같은 process tick 또는 1250ms\n   이내에\
  \ 시작되는 경우: server-close.ts 는 setTimeout 핸들을 모르므로 clearTimeout 호출 없음.\n   recoverPendingOutboundDeliveries\
  \ 의 IIFE 도 abort signal 없음. recovery 가 그대로 진행되어\n   `deliverOutboundPayloadsInternal`\
  \ 또는 `recoverPendingRestartContinuationDeliveries` 가\n   이미 닫힌 wss / dispose 된 plugin\
  \ services / 정리된 channelManager 를 호출하려 시도.'\n"
root_cause_chain:
- why: 왜 recovery 함수가 cancellation handle 을 반환하지 않는가?
  because: '`activateGatewayScheduledServices` 의 return 타입 (server-runtime-services.ts:255-258)
    은 `{ heartbeatRunner, stopModelPricingRefresh }` 두 stop 핸들만 정의. recovery 함수들은
    "once-only background work" 로 분류되어 stop 인터페이스가 없다. void return.'
  evidence_ref: src/gateway/server-runtime-services.ts:255-258
- why: 왜 host (server.impl.ts) 가 자체 abort signal 을 전달하지 않는가?
  because: '`activateScheduledServicesWhenReady` (server.impl.ts:1366-1389) 는 startup
    가드 (`closePreludeStarted || !startupSidecarsReady`) 로 *activate 진입 자체* 만 보호한다.
    진입한 후의 background work 가 shutdown 신호를 받을 통로는 만들어지지 않았다. 비교: scheduleGatewayPostReadyMaintenance
    (server-runtime-services.ts:103-154) 는 setTimeout 안에서 isClosing() 가드 + clearGatewayMaintenanceHandles
    를 호출하는 대조 패턴이 있다.'
  evidence_ref: src/gateway/server.impl.ts:1366-1389
- why: 왜 recovery 의 부작용이 문제가 되는가?
  because: '`recoverPendingDeliveries` (../infra/outbound/delivery-queue.js, 셀 밖)
    는 deliver 콜백 (`deliverOutboundPayloadsInternal`) 으로 outbound queue 항목을 재전송 시도.
    shutdown 중 channelManager 가 stopChannel 로 정리된 후 deliver 가 channel handle 을 요구하면
    race 또는 Result.err. `recoverPendingRestartContinuationDeliveries` (server-restart-sentinel.ts:481-491)
    도 `deps` (CliDeps) 와 session delivery 메커니즘에 의존. 두 경로 모두 race window 동안의 diagnostic
    warning log 와 부분 retry-loop 동작 (`waitForOutboundRetry` 호출) 으로 shutdown 노이즈를 증가.'
  evidence_ref: src/gateway/server-restart-sentinel.ts:481-491
- why: 왜 timer.unref() 만으로는 충분하지 않은가?
  because: unref 는 timer 가 event loop alive 유지를 막을 뿐, queued callback 자체는 process
    가 살아있는 동안 (다른 작업으로 인해) 실행된다. shutdown 은 wss/http close + 다수 await 으로 수 초간 process
    가 유지되므로 그 사이 callback fire 가능. clearTimeout(timer) 가 진짜 cancel.
  evidence_ref: 'Node.js docs: timer.unref() 동작 정의 (background reference, not citation
    in repo)'
impact_hypothesis: wrong-output
impact_detail: "'정성: shutdown 중에 recovery 가 진행되어 발생할 수 있는 부작용:\n- shutdown log 에 \"\
  Delivery recovery failed\" / \"Session delivery recovery failed\" 노이즈\n  (이미 정리된\
  \ deps/channelManager 사용 시도로 인한 error).\n- outbound queue 의 entry 가 진짜 deliver 시도되어\
  \ 외부 (Slack/Telegram/SMS 등) 에 메시지\n  전송. shutdown 사용자 의도와 무관한 시점.\n- restart 시나리오:\
  \ gateway 가 곧 다시 부팅될 예정인데 직전 인스턴스가 recovery 를 일부 진행 →\n  재부팅 후에도 recovery 재진입 →\
  \ 중복 deliver risk (delivery-queue 가 idempotency 보장 시\n  무해, 아니면 double-send).\n\
  재현 조건: startup 직후 1250ms 이내 SIGTERM/SIGINT (예: container orchestrator 의 빠른\n  redeploy/scale-down,\
  \ `openclaw restart` 한 직후). 일반 운영에서 빈도는 낮으나 CI / smoke\n  test / rapid restart 환경에서\
  \ 관찰 가능. Severity P3 (운영 중 핵심 장애 아닌 위생 수준).'\n"
severity: P3
counter_evidence:
  path: src/gateway/server-runtime-services.ts
  line: 103-154
  reason: '''같은 파일의 `scheduleGatewayPostReadyMaintenance` (103-154) 가 setTimeout callback
    내부에

    `isClosing()` 가드 + `clearGatewayMaintenanceHandles(maintenance)` 패턴으로 shutdown
    safety

    를 구현했음 → 동일 패턴을 recovery 함수에 적용 가능했음을 보여준다 (의도된 디자인 vs. 누락).

    확인한 반증 카테고리:

    (1) 숨은 방어: `recoverPendingDeliveries` / `recoverPendingRestartContinuationDeliveries`

    내부에서 자체 abort signal 또는 isClosing 체크 여부 — 셀 밖 (`src/infra/outbound/**`,

    `src/gateway/server-restart-sentinel.ts`). server-restart-sentinel.ts:481-491
    의 함수 시그니처는

    `(deps, log, maxEnqueuedAt)` 만 받음 → signal 파라미터 부재. 추가 방어 없음.

    (2) 기존 테스트 커버리지: `server-runtime-services.test.ts` 의 활성 테스트 (74-345) 는

    `recoverPendingDeliveries` / `recoverPendingRestartContinuationDeliveries` 호출
    여부만 assert

    (L195, L200, L334). shutdown race 시나리오 테스트 부재.

    (3) 호출 빈도: `activateGatewayScheduledServices` 는 정상 startup 마다 1회 호출

    (server.impl.ts:1375). minimalTestGateway=true 이면 early return (L256-258) 으로 recovery

    호출 자체 skip. 운영 환경에서는 매 boot 1회 fire.

    (4) primary-path inversion (CAL-001): 정상 shutdown 은 startup 후 충분한 시간이 지나 발생

    → recovery 가 이미 완료된 상태에서 close 진입 → race 발생 안 함. abnormal shutdown (startup

    1250ms 이내) 만이 race window. **abnormal path 만 영향**. CAL-001 의 "정상 경로에 의해 mask

    됨" 사례 — Severity P3 권장.

    (5) hot-path-vs-test-path consistency (CAL-003): production hot-path 는 real timer
    + real

    SIGTERM. 테스트는 mock fn 으로 호출만 assert → race window 자체를 cover 못 함. 재현 테스트는

    setTimeout fake 시계 + immediate shutdown 으로 구성해야 함.

    (6) upstream-dup check (CAL-004/008): `git log upstream/main --since="6 weeks
    ago" -- src/gateway/server-runtime-services.ts`

    → 본 함수 라인 변경 없음. `gh pr list --search "recoverPendingSessionDeliveries OR recoverPendingOutboundDeliveries"`

    → 본 race 를 fix 하는 OPEN PR 없음 (인지 범위).''

    '
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
---
# startup recovery jobs have no shutdown cancellation hook

## 문제

`activateGatewayScheduledServices` (`src/gateway/server-runtime-services.ts:245-283`) 가 startup 후 호출하는 두 recovery 작업은 시작/정지 비대칭이다:

- `recoverPendingOutboundDeliveries` (L156-170): 즉시 fire-and-forget async IIFE. `await import(...)` + `await recoverPendingDeliveries(...)` 진입. handle 또는 stop 함수 반환 없음.
- `recoverPendingSessionDeliveries` (L172-190): `setTimeout(callback, 1250)` 으로 예약. timer handle 은 unref 만 호출되고 caller 에게 반환되지 않음.

둘 다 ChannelManager / deps (CliDeps) / dynamic-imported modules 에 의존. gateway shutdown 이 startup 후 짧은 시간 내 (특히 1250ms 이내) 시작되면, recovery 가 tearing-down state 에서 실행된다. `timer.unref()` 는 event-loop 유지를 막을 뿐 callback fire 자체를 막지 않는다.

같은 파일의 `scheduleGatewayPostReadyMaintenance` (L103-154) 는 setTimeout 내부에서 `isClosing()` 가드 + `clearGatewayMaintenanceHandles` 호출의 대조 패턴을 보여줘, 이 함수들에도 동일 패턴이 적용 가능했음을 시사한다.

## 발현 메커니즘

1. gateway boot → `activateGatewayScheduledServices` 호출 (server.impl.ts:1375).
2. 동기적으로 `recoverPendingOutboundDeliveries` 진입: async IIFE 시작, `await import("../infra/outbound/delivery-queue.js")` → `await import("../infra/outbound/deliver.js")` → `await recoverPendingDeliveries(...)`. import + I/O 합산 수 ms~수십 ms.
3. 동기적으로 `recoverPendingSessionDeliveries` 진입: `setTimeout(callback, 1250)`. 1250ms 후 callback 이 IIFE 시작 → dynamic import → recovery.
4. shutdown trigger (SIGTERM/SIGINT 또는 `openclaw restart`) 가 이 사이 발생 → `runGatewayClosePrelude` (server-close.ts:139-161) 진입 → 다음으로 `createGatewayCloseHandler` 의 close 함수 → channelManager stop, plugin-services stop, wss close 등.
5. server-close.ts 는 `recoverPendingSessionDeliveries` 의 timer handle 을 모름 → `clearTimeout` 호출 없음. 1250ms 가 지나면 callback 이 fire 하고 IIFE 의 await chain 이 진행. `params.deps.cron` 등은 이미 stop 처리됨.
6. `recoverPendingDeliveries` 가 fire 중이면 `deliverOutboundPayloadsInternal` 이 channel handle 을 요구할 때 channel 이 이미 dispose → error log 또는 Result.err. 일부 entry 는 진짜로 외부 (Slack/Telegram/SMS) 에 deliver 시도되어 shutdown 사용자 의도와 분리된 시점에 메시지 발사.
7. process exit 까지 남은 short window 동안 recovery 가 mid-await 상태로 abandon 되어 incomplete state 가 디스크에 남을 가능성 (file I/O 의 partial write 가 외부 라이브러리에 위임).

## 근본 원인 분석

1. **Return-type contract 의 단순화**: `activateGatewayScheduledServices` 의 return 타입은 stop 가능한 두 핸들 (`heartbeatRunner.stop`, `stopModelPricingRefresh`) 만 정의. recovery 는 "once-only background" 로 분류되어 인터페이스 자체가 누락.
2. **Host 측 abort 통로 부재**: `activateScheduledServicesWhenReady` (server.impl.ts:1366-1389) 는 entry guard (`closePreludeStarted`) 만 두고, 진입한 후의 background work 가 shutdown signal 을 받을 통로를 만들지 않음. 같은 파일의 `scheduleGatewayPostReadyMaintenance` 가 isClosing 가드를 가진 것과 비교.
3. **Dynamic import + fire-and-forget 의 결합**: dynamic import 자체는 lifecycle-aware 가 아니므로 외부 abort signal 없이 자동 취소 불가. async IIFE 는 abort 의 hook 지점 없음.
4. **`timer.unref()` 의 의미 오해 가능성**: unref 는 alive 유지를 막을 뿐이지 cancel 이 아님. clearTimeout 만이 실 cancel. process 가 다른 작업으로 살아있으면 unref 된 timer 도 fire.

## 영향

- **현상**: startup 후 1250ms 이내 shutdown 시 recovery 가 tearing-down state 에서 실행. error log 노이즈, 외부 메시지 의도치 않은 발사, partial state 디스크 잔존.
- **재현 조건**: container orchestrator 의 빠른 redeploy (예: rolling deploy 의 termination grace 가 짧음), `openclaw restart` 직후 또 다른 restart, CI smoke test 의 빠른 boot/kill 사이클.
- **빈도**: 일반 운영에서는 낮음 (운영 lifetime 이 보통 시간~일 단위). dev/CI/smoke 환경에서 자주 노출.
- **데이터 정합성 위협 수준**: recovery 의 deliver 콜백이 idempotent 라고 가정하면 부분 deliver 는 다음 부트 때 재시도되며 정합성 유지. idempotency 미보장 외부 채널 (특정 third-party 플러그인) 에서는 double-send 가능.

## 반증 탐색

### 숨은 방어 / defense-in-depth

- `recoverPendingDeliveries` (`src/infra/outbound/delivery-queue.js`) 와 `recoverPendingRestartContinuationDeliveries` (server-restart-sentinel.ts:481-491) 시그니처는 `(deps, log, maxEnqueuedAt)` 만 받고 abort signal 파라미터 없음 → 내부 isClosing 가드 부재.
- `scheduleGatewayPostReadyMaintenance` (server-runtime-services.ts:103-154) 는 `isClosing` 가드 + `clearGatewayMaintenanceHandles` 패턴을 가진 대조군. recovery 함수에 동일 패턴 미적용.
- `recordPostReadyMemory` 도 isClosing 가드 (L146-148) 로 보호. recovery 만 가드 부재.

### 기존 테스트 커버리지

- `server-runtime-services.test.ts` (74-345) — `recoverPendingDeliveries` / `recoverPendingRestartContinuationDeliveries` 호출 여부 assert (L195, L200, L334). shutdown race 시나리오 cover 없음.
- `server-restart-sentinel.test.ts` — sentinel write/read 로직 위주. activate 후 즉시 shutdown 시나리오 부재.

### 호출 빈도 / 경로 활성 여부

- `activateGatewayScheduledServices` 는 정상 boot 마다 1회 fire. `minimalTestGateway=true` 인 경우 L256-258 early return 으로 recovery skip.
- `params.startCron === false` 인 경우 cron start 만 skip; recovery 호출은 그대로 수행 (L266, L270).

### 설정 / feature flag

- `OPENCLAW_SKIP_CRON=1` 은 cron start 영향 (server-cron-lazy.ts:20). recovery 자체는 별도 toggle 없음.
- `isVitestRuntimeEnv()` 가 true 면 modelPricingRefresh 만 skip (L275-281). recovery 는 vitest 에서도 호출됨 (테스트 mock 으로 stub).

### Primary-path inversion (CAL-001)

이 FIND 가 성립하려면 어떤 정상 경로가 실패해야 하는가? 정상 boot 후 충분히 (수 분~수 시간) 운영된 후의 shutdown 은 recovery 가 이미 완료된 상태 → race window 부재. 이 FIND 는 **boot→shutdown short window** 시나리오 한정. 정상 경로가 mask. CAL-001 의 "primary path 가 정상이면 결함이 드러나지 않음" 사례, severity P3 정당.

### Hot-path-vs-test-path consistency (CAL-003)

production hot-path = real timer + real shutdown signal. 테스트는 `vi.fn()` mock + `expect(toHaveBeenCalled)` 만 assert → race window 자체 cover 못 함. 재현 테스트는 fake timer + immediate close trigger + recovery 미완료 상태 verification 으로 구성 가능.

### Upstream-dup check (CAL-004/008)

- `git log upstream/main --since="6 weeks ago" -- src/gateway/server-runtime-services.ts` → 본 함수 라인 변경 없음.
- `gh pr list --repo openclaw/openclaw --state all --search "recoverPendingSessionDeliveries OR recoverPendingOutboundDeliveries"` → 본 race 를 fix 하는 OPEN PR 없음 (인지 범위).
- 같은 파일에 `scheduleGatewayPostReadyMaintenance` 의 isClosing 패턴이 존재한다는 점이 fix 가능성 시사 — upstream 도 한 번 더 일관성 보강 가치 있음.

## Self-check

### 내가 확실한 근거

- `src/gateway/server-runtime-services.ts:156-190` 본문에서 두 recovery 함수가 handle 반환 없음 (Read 확인).
- 같은 파일 L103-154 의 `scheduleGatewayPostReadyMaintenance` 가 isClosing 가드 + clearGatewayMaintenanceHandles 패턴 보유 (대조 증거).
- `server.impl.ts:1366-1389` 의 activate 호출 측이 cancellation 통로 미보유.
- `server-close.ts:139-161` 의 closePrelude 에서 recovery cancellation 없음.

### 내가 한 가정

- `recoverPendingDeliveries` 와 `recoverPendingRestartContinuationDeliveries` 내부에 자체 isClosing 가드 가 없다는 가정 — 셀 밖 (`src/infra/outbound/**`) 파일은 Read 하지 않음. 함수 시그니처만 보고 추론.
- `timer.unref()` 동작 모델은 Node.js 표준 동작에 의존 — repo 내 직접 citation 없음.
- 외부 channel plugin (Slack/Telegram/SMS) 의 idempotency 보장 여부는 plugin 별로 다름 — production 빈도 정량 부재.

### 확인 안 한 것 중 영향 가능성

- `recoverPendingOutboundDeliveries` 의 `recoverPendingDeliveries` 내부에서 별도 timer/interval 을 추가 등록하는 경우 — `delivery-queue.js` Read 안 함 → 추가 lifecycle gap 가능성. 셀 밖.
- restart 시나리오의 `restartExpectedMs` (server-close.ts:208-219) 가 set 된 경우 pre-restart hook 이 호출되어 partial cleanup 이 다르게 동작. 본 FIND 의 race window 와 상호작용 미조사.
- `applyMaintenance` 의 timer 핸들들 (tickInterval/healthInterval/dedupeCleanup/mediaCleanup) 은 server-close.ts 에서 정상 clear 됨 (L330-335 확인). recovery 와 무관한 별개 경로.
