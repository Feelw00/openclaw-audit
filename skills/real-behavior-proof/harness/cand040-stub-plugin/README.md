# cand040-stub-plugin

CAND-040 (`src/infra/approval-handler-runtime.ts:498-537` deliverTarget race) e2e proof 의
옵션 C (gateway 부팅 + stub plugin + sideband) 를 위한 audit-side stub channel plugin.

## install + 부팅 (검증된 부분)

```bash
OPENCLAW_HOME=<isolated home> OPENCLAW_AUDIT_STUB_C040_DIR=<control dir> \
  node /Users/lucas/Project/openclaw/openclaw.mjs plugins install --link \
  /Users/lucas/Project/openclaw-audit/skills/real-behavior-proof/harness/cand040-stub-plugin

OPENCLAW_HOME=<isolated home> OPENCLAW_AUDIT_STUB_C040_DIR=<control dir> \
  node /Users/lucas/Project/openclaw/openclaw.mjs gateway run \
  --auth none --bind loopback --port <p> --allow-unconfigured
```

## sideband 파일 (OPENCLAW_AUDIT_STUB_C040_DIR 안)

- `register-mode.jsonl` — `register(api)` 호출 시 mode (`discovery` / `full` / `cli-metadata` / `tool-discovery`)
- `lease.jsonl` — `channelRuntime.runtimeContexts.register` 호출 + readback + runtime keys
- `calls.jsonl` — 모든 stub method 호출 (`gateway.startAccount.{enter,exit}`,
  `availability.{isConfigured,shouldHandle}`, `transport.prepareTarget`,
  `transport.deliverPending.{enter,exit}`, `interactions.{bindPending,unbindPending}`,
  `presentation.{buildPendingPayload,buildResolvedResult,buildExpiredResult}`,
  `observe.onDelivered`)

## 현재 진행 상태 (2026-05-16)

**✅ collected — race window 직접 측정 성공** (`proofs/PROOF-CAND-040-pre-20260516-105708.md`):
- trials=3 chain_works=3 leak=3 fix_observed=0 fire_rate=1.0
- 각 trial: prepareTarget=1, deliverPending.enter=1, deliverPending.exit=1, bindPending=1, unbindPending=0

## silent skip root cause — 5 발견 정리 (이 plugin 의 design lesson)

직전 세션 (2026-05-15) "handler wire 안 됨" 막힘의 정확한 원인:

1. **plugin-loader-side store ≠ caller-side store** — `loader.ts:1621 createPluginRuntime()` 가
   매번 별개 `createRuntimeChannel()` instance 생성. plugin 의 `register(api)` 가 받는 api
   의 channelRuntime.runtimeContexts 는 server-side (`server.impl.ts:164 getChannelRuntime` cache)
   와 다른 store. self-loop readback 은 일치 증명 아님.

2. **register 시점/위치** — `register(api)` 안에서 등록하지 말 것. **`gateway.startAccount(opts)`
   안에서 `opts.channelRuntime`** (caller 가 server-side store wrap 으로 전달) 사용해서
   `registerChannelRuntimeContext` 호출. telegram pattern: `extensions/telegram/src/monitor.ts:163-170, 215-222`.

3. **`capability.native` 필수** — `ChannelApprovalNativeAdapter` 가 없으면
   `approval-native-delivery.ts:42-56` 가 `targets: []` 반환 → `prepareTarget` 호출 안 됨.
   minimal: `{describeDeliveryCapabilities: () => ({enabled: true, preferredSurface: "origin",
   supportsOriginSurface: true}), resolveOriginTarget: async () => ({to: "...", threadId: "..."})}`.

4. **`prepareTarget` 반환에 `dedupeKey`** — `approval-native-runtime.ts:96`
   `deliveredKeys.has(preparedTarget.dedupeKey)`. 부재 시 첫 trial 만 진행, 이후 undefined 가
   key 가 되어 skip.

5. **server-side lease dispose 별도 watcher** — scenario 의 `dispose-lease.release` flag 는
   stub 의 startAccount lease 만 dispose 시키도록 별도 file-flag watcher 필요. plugin-loader-side
   lease 만 dispose 해도 `approval-handler-bootstrap.ts:179 stopHandler` 가 fire 안 됨 — server-side
   lease dispose 가 emitRuntimeContextEvent("unregistered") 발사 →
   `approval-handler-bootstrap.ts:179` watch onEvent → stopHandler → handler.stop → onStopped → race 발현.

## scenario 추가 발견 (probe pattern)

- gateway exec.approval.request 의 ack 는 plugin 의 deliverPending 완료 후 회신. probe 가
  ack 대기하면 race window trigger 안 됨. probe 가 `exec.approval.request.sent` 직후 calls.jsonl
  polling 시작해서 `deliverPending.enter` 직접 관측 + race trigger 발사.

## 검증 도구

- 시나리오: `scenarios/proof-CAND-040-e2e.py` — `run_scenario` 가 1 trial 만 default,
  `debug_paths` 가 result 에 포함 (control_dir, sut_stdout, sut_stderr) — 다음 세션에서
  cleanup 후 production 시나리오로 전환 시 제거.
- ad-hoc runner: `/tmp/cand040_runner.py` (일회용, 삭제됨). 다음 세션에서 재작성 또는
  `harness/run.py` 의 정식 entry 사용.

## 결함 핵심 (시나리오 docstring 참조)

ws.close 시 deliverTarget 의 두 await 사이 race 로 wrapped entry 가 cleared activeEntries
Map 에 re-insert → unbindPending 영구 미호출 → native binding leak. 측정: stub 의
`unbindPending` 호출 횟수 (without-fix=0, with-fix>=1).
