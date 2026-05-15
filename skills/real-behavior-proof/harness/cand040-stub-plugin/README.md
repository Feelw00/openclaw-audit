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

## 현재 진행 상태 (2026-05-15)

**완료 — gateway boot path 까지 도달**:
- install --link 성공 (installs.json 에 audit-stub-c040 등록)
- boot 시 plugin 11개 인식 (audit-stub-c040 포함)
- `register(api)` 호출 (discovery + full mode 둘 다)
- `channelRuntime.runtimeContexts.register` 성공 + **자기 lease readback 가능** → store 일치 확인
- `gateway.startAccount` 호출 + abort.signal 까지 영구 유지
- audit ws probe 가 `exec.approval.request` RPC 발사 → ack `{id, decision:null}` 받음

**막힘 — approval handler 가 stub 와 wire 안 됨**:
- stub 의 `availability.isConfigured`, `shouldHandle`, `transport.*`, `presentation.*` 호출 0회
- 즉 `startChannelApprovalHandlerBootstrap` → `createChannelApprovalHandlerFromCapability` →
  `handler.start()` 의 어딘가에서 silent skip
- boot log 에 error/retry 메시지 없음 → silent fail path

## 가능한 빠진 것 (다음 세션 trial-and-error 후보)

1. **`openclaw.plugin.json#channelConfigs`** — boot 경고 명시. capability config schema + setup
   surface 지정. approval bootstrap 가 이걸 보고 진입 가드 통과 여부 결정 가능.
2. **`ChannelPlugin.capabilities`** — 현재 `{}`. `{approvals: true}` 또는 다른 flag 필요할 수도.
3. **누락 adapter** — `outbound`, `status`, `messaging` 등. mattermost / discord 의 minimal
   ChannelPlugin 과 비교해서 우리 stub 에 빠진 필수 surface 식별.
4. **`origin`** 가 "bundled" 가 아닌 다른 값 — server-channels.ts:327 의 분기에서 startup
   runtime 못 받을 수도. linked plugin 의 origin 확인.

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
