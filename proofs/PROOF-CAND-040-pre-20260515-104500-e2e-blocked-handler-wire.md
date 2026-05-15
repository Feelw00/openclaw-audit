---
target: "CAND-040"
phase: "pre"
scenario: "proof-CAND-040-e2e"
status: "blocked-external-dep"
base_sha: "a099acc557"
head_sha: null
started_at: "2026-05-15T10:00:00Z"
finished_at: "2026-05-15T10:45:00Z"
---

# PROOF — CAND-040 (pre, e2e) — blocked-external-dep (handler wiring)

**status**: `blocked-external-dep`
**scenario**: `proof-CAND-040-e2e`

옵션 C (gateway 부팅 + audit-side stub channel plugin + sideband) 진행 — 2 세션 추정 중
이번 세션은 **gateway boot path 까지 도달** 했으나 **approval handler 가 stub 의
nativeRuntime 와 wire 안 됨**. 다음 세션 인계.

## 옵션 결정 배경 (2026-05-15)

CAND-040 production execution path 분석 후 3 옵션 제시:
- 옵션 B (in-process direct, ~1-2h, CAL-003 risk): tsx 스크립트로 production module 직접 호출
- 옵션 C (gateway + stub plugin + sideband, ~3-4h 추정): 사용자 선택
- 옵션 A (full production trigger via real channel plugin, ~5.5h+ multi-session): 가장 production-faithful

사용자 결정: 옵션 C. 진행 중 cost 가 당초보다 큼 (8h+ 가능성) 확인됨. 사용자 "전부 진행해"
+ "계속 dive" 지시로 진행. 막힘 분석 후 영속화 + 다음 세션 인계로 종료 (사용자 결정
2026-05-15).

## 진행된 부분 (이번 세션)

### 1. stub channel plugin scaffold

`skills/real-behavior-proof/harness/cand040-stub-plugin/` (이번 세션 commit 으로 영속화):
- `openclaw.plugin.json` — manifest (channels: ["audit-stub-c040"], enabledByDefault: true)
- `package.json` — `openclaw.extensions: ["./index.mjs"]`
- `index.mjs` — ChannelPlugin object + approvalCapability.nativeRuntime (file-IPC Deferred
  gate + sideband 측정 stub)
- `README.md` — 진행 상태 + 다음 세션 trial-and-error 후보

### 2. install + boot 검증

- `openclaw plugins install --link <dir>` 성공 (installs.json 에 audit-stub-c040 등록)
- 부팅 시 plugin 11개로 인식 (`http server listening (11 plugins: ..., audit-stub-c040, ...)`)
- stub 의 `register(api)` 호출 — discovery + full mode 둘 다 trigger (register-mode.jsonl)
- `channelRuntime.runtimeContexts.register` 성공 + **자기 lease readback 가능**
  (lease.jsonl 의 `readback_self: {source: "audit-stub-c040"}`) → store 일치 확인
- `gateway.startAccount` 호출 + abort.signal 까지 영구 유지 (calls.jsonl)

### 3. audit ws probe + RPC

`scenarios/proof-CAND-040-e2e.py` (debug_paths 포함):
- device pairing (operator role + operator.{read,write,approvals,admin} scopes)
- connect handshake (CAND-038 동일 패턴)
- `exec.approval.request` RPC 발사 → ack `{id, decision:null}` 받음 (gateway 처리)

### 4. cfg schema 디버깅 (2 라운드)

1차: `api: "responses"` → `api: "openai-responses"` (enum)
2차: scope `operator.write` → `operator.approvals` 추가 (missing scope: operator.approvals)
3차: capability `"channel-approval-native-runtime-context"` → `"approval.native"` (constant
명 가져옴)

## 막힌 지점

stub 의 다음 method 호출 **0회** (sideband 확인):
- `availability.isConfigured`
- `availability.shouldHandle`
- `transport.prepareTarget`
- `transport.deliverPending`
- `interactions.bindPending` / `unbindPending`
- `presentation.*`
- `observe.onDelivered`

즉 `startChannelApprovalHandlerBootstrap` → `createChannelApprovalHandlerFromCapability` →
`handler.start()` → `adapter.isConfigured()` 의 어딘가에서 **silent skip**.

## 가설 + 검증 결과

| 가설 | 검증 |
|---|---|
| store 분리 (plugin runtime ↔ channel runtime) | ❌ — readback 성공으로 일치 확인 |
| channelRuntime undefined | ❌ — store readback 가능 |
| capability.nativeRuntime null | ❌ — stub object 명시 보유 |
| createChannelApprovalHandlerFromCapability null return | 가능 — 단 검증 어려움 |
| handler.start silent throw → retry deferred | 가능 — 단 boot log 에 retry/error 메시지 없음 |
| stub plugin 의 capabilities 빈 → production 가 skip | 추측 — 검증 위해 trial-and-error 필요 |
| openclaw.plugin.json 의 channelConfigs 누락 → bootstrap 진입 가드 막힘 | 추측 (boot 경고 명시) |

## 다음 세션 trial-and-error 후보 (README.md 참조)

1. **`openclaw.plugin.json#channelConfigs` 추가** — boot 경고 명시한 metadata
2. **`ChannelPlugin.capabilities`** — `{}` → `{approvals: true}` 또는 다른 flag
3. **누락 adapter 식별** — mattermost / discord 의 minimal form 과 비교
4. **`origin` 검증** — bundled 가 아닐 시 server-channels.ts:327 분기에서 startup runtime 못 받을 가능성

각 후보 시도 후 stub 의 sideband 에 availability.isConfigured 호출 확인 = wire 성공.

## 시간 비용 평가

이번 세션 누계 7-8h (production path 분석 + scaffold + install + boot + RPC + 4 라운드
디버깅 + 시나리오 작성 + 막힘 dive). 다음 세션 추정 1-3h (위 trial-and-error 4개 후보).
성공 보장 없음.

CAND-040 의 severity (P3) 대비 시간 비용 큼. 대안:
- **옵션 B (in-process direct, ~1-2h, CAL-003 risk 수용)** — tsx 스크립트로 production
  module 직접 호출 + Deferred-gated stub + handleRequested 직접 호출. unit-level proof 의
  e2e 격상 정도. CAL-003 caveat 명시.
- **CAND-040 unit-level final 채택** — 2026-05-14 unit-level proof
  (`PROOF-CAND-040-pre-20260514-102041.md`, totalUnbind=0 totalLeak=5/5) 를 final 로
  수용. PR body 에 `proof: supplied` 만 부여, `sufficient` 미부여 + CAL-003 caveat.

다음 세션 첫 결정: 옵션 C 의 trial-and-error 더 진행 vs 옵션 B/C 전환 vs unit-level final.
