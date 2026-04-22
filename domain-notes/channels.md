# channels + routing 도메인 노트

**최초 작성**: 2026-04-22, error-boundary-auditor (cell: channels-error-boundary)
**스코프**: `src/channels/**`, `src/routing/**` (upstream/main abf940db61 기준)

## 디렉터리 구조

### `src/channels/`
- **루트 파일 (~50개)**: 공통 helper (정규화, 매칭, 세션 meta). 대부분 **순수 함수**.
  - 주요: `allow-from.ts`, `allowlist-match.ts`, `ack-reactions.ts`, `session.ts`, `targets.ts`, `sender-identity.ts`, `conversation-label.ts`, `inbound-debounce-policy.ts`, `status-reactions.ts`, `typing.ts`, `typing-lifecycle.ts`, `typing-start-guard.ts`, `draft-stream-loop.ts`, `draft-stream-controls.ts`, `run-state-machine.ts`.
  - webhook/입력 adapter 코드는 여기에 없음 — 각 plugin 이 소유.
- **`plugins/`**: channel plugin registry + factory.
  - `registry.ts`, `registry-loader.ts`, `bundled.ts`, `catalog.ts` — plugin 로딩.
  - `outbound/`, `actions/` — outbound 전송 + 액션 dispatch.
  - `bundled-root.ts`, `bundled-ids.ts` — telegram/slack/discord/whatsapp 등 bundled adapter 경로 관리 (실제 extension 코드는 `extensions/` 아래, 본 스코프 외).
- **`allowlists/`**: `resolve-utils.ts` 하나. allowlist 병합/canonicalize 유틸 (Set 기반).
- **`transport/`**: `stall-watchdog.ts` — interval 기반 idle timeout 체크. abort signal 전파 포함.
- **`web/`**: index.ts 하나 (entry barrel).

### `src/routing/`
- 모두 순수 함수 / in-memory cache. throw/async 최소.
- `resolve-route.ts`: 바인딩 tier 8단계 (peer → parent → wildcard → guild+roles → guild → team → account → channel). WeakMap cache.
- `bindings.ts`, `binding-scope.ts`, `account-id.ts`, `account-lookup.ts`, `session-key.ts`, `resolve-route.ts`, `peer-kind-match.ts`.

## 에러 전파 패턴 (감사 요약)

### JSON.parse 경로 (risk hint 1)

src/channels/** 내 JSON.parse 3 hits:
| 파일 | 라인 | 경로 성격 | 방어 |
|---|---|---|---|
| `plugins/catalog.ts` | 134 | catalog load (startup) | try/catch → 무시 (line 136-138) |
| `bundled-channel-catalog-read.ts` | 58 | bundled extension package.json | try/catch → continue (line 62-64) |
| `bundled-channel-catalog-read.ts` | 76 | official catalog dist file | try/catch → continue (line 80-82) |

**모두 silent catch** 이나 **startup 비 hot-path**, 실패 시 빈 배열로 정상 회귀. **reliability 영향 부재 → CAL-017 기각 패턴 (observability-only) 으로 FIND 제외**.

### Regex 경로 (risk hint 3 — ReDoS)

`new RegExp(...)` 로 동적 생성되는 곳 없음 (test 파일 제외). 런타임 regex 는 모두 **상수 리터럴** (`sender-identity.ts:23,32`, `conversation-label.ts:18,21`, `plugins/helpers.ts:64`). allowlist 매칭은 **Set.has() 기반**이라 regex 아님.

**allowlists/resolve-utils.ts** 의 `addAllowlistUserEntriesFromConfigEntry`, `mergeAllowlist` 는 Set/array dedupe — user-controlled regex 주입 불가.

**risk hint 3 는 이 셀에서 부재** — counter_evidence 로 명시.

### channel adapter throw → routing 전파 (risk hint 2)

outbound 쪽: `plugins/outbound/direct-text-media.ts` 의 `sendDirect` / `sendText` / `sendMedia` 는 `await send(...)` 로 throw propagate → caller 책임. routing 쪽으로는 adapter 가 throw 해도 문제 없음 (synchronous await chain).

**문제는 fire-and-forget 경로**:
- `draft-stream-loop.ts:60,75` `void flush()` — send throw 시 .catch 없음 → FIND-001.
- `ack-reactions.ts:97` `void ackReactionPromise.then(onFulfilled)` — onRejected 없음 → FIND-002.
- `session.ts:43-51` `void runtime.recordSessionMetaFromInbound(...).catch(onRecordError)` — **.catch 있음, 방어됨**.
- `draft-stream-loop.ts` 의 stop/seal 경로는 `await loop.flush()` 로 정상 await.
- `typing-lifecycle.ts:34` `void tick()` — tick 내부 try/finally. onTick 이 throw 하면 finally 이후 rethrow, void 로 swallow. 다만 caller 가 `createTypingKeepaliveLoop({ onTick: fireStart })` 전달 시 fireStart → startGuard.run 이 **try/catch 로 swallow** (typing-start-guard.ts:36-52, default `rethrowOnError=false`) → safe.
- `status-reactions.ts:280,288` `void enqueue(...)` — enqueue chain 은 `chainPromise.then(fn, fn)` 패턴으로 이전 reject 도 복구. `applyEmoji` 는 try/catch + onError 로 방어. 최종 chain tail 만 이론적 문제이나 debounce-driven 이라 매 tick cleanup. **safe level**.
- `draft-stream-loop.ts:27` `await inFlightPromise;` — inFlightPromise 가 reject 시 flush 전체 reject → `void flush()` 경로에서 swallow. FIND-001 에 포함.

### unhandledRejection handler

`src/infra/unhandled-rejections.ts:345` 가 process-level 설치.
- transient network codes (ECONNRESET / UND_ERR_* / etc.) → warn + continue.
- non-transient → `exitWithTerminalRestore` → process.exit(1).
- sqlite transient → warn + continue.
- AbortError → suppressed.

따라서 channels 의 transient network 류 unhandled 는 warn-only. non-transient (TypeError 등) 은 crash.

### webhook entry points

src/channels/** 내 webhook payload JSON.parse 처리 파일은 없음 — 각 channel plugin (`extensions/**` 또는 plugin-sdk) 이 소유. 이 셀의 스코프 밖. bundled `catalog.ts` / `bundled-channel-catalog-read.ts` 는 config file JSON 파싱 (네트워크 입력 아님).

## allowlist 의사결정

Slack/Discord/Telegram 공통 allowlist 매칭은 all Set-based:
- `allow-from.ts` `isSenderIdAllowed` — `allow.entries.includes(senderId)` (Array.includes, O(n) but small).
- `allowlist-match.ts` `resolveCompiledAllowlistMatch` — `Set.has(value)` O(1).
- wildcard `*` 는 `hasWildcard` flag 로 분리.

ReDoS 위험 부재. 단 `Array.includes` 는 큰 allowlist 에서 O(n) — 성능 이슈는 memory 팀 스코프.

## routing 핵심 흐름

`resolveAgentRoute` (`resolve-route.ts:610-814`):
1. 입력 normalization (channel, accountId, peer, guildId, teamId, roles, dmScope).
2. bindings cache lookup (WeakMap by cfg).
3. 8-tier 매칭 (binding.peer → ... → binding.channel → default).
4. `choose()` 가 sessionKey + mainSessionKey 생성 + cache put.

모두 **sync + pure** — throw 없음, async 없음. routing 쪽은 error-boundary 공격 표면 **없음**.

## 본 셀에서 기각한 후보

- **catalog.ts / bundled-channel-catalog-read.ts JSON.parse silent catch**: startup 경로, fallback 빈 배열 → reliability 영향 부재 → CAL-017 패턴 기각.
- **typing-lifecycle.ts:34 void tick()**: typing-start-guard.ts 가 default throw swallow 로 방어 → 기각.
- **session.ts:43 void runtime.recordSessionMetaFromInbound**: `.catch(onRecordError)` 로 방어됨 → 기각.
- **status-reactions.ts:280,288 void enqueue**: applyEmoji 내부 try/catch + onError, enqueue chain `then(fn, fn)` 복구 패턴 → 기각.
- **allowlist ReDoS (risk hint 3)**: runtime regex 전부 상수 리터럴, allowlist 매칭은 Set 기반 → FIND 없음.

## 다음 감사 시 참고

- `channels-lifecycle` 셀: plugin load/unload, setup-registry, bundled-root cache reset — memory/lifecycle 관점.
- `channels-concurrency`: enqueue chain, typing-start-guard consecutiveFailures 카운터, resolve-route cache eviction — concurrency 관점.
- channel plugin extensions (`extensions/**`): webhook handler, reconnect loop, keepalive — 본 스코프 밖이지만 error-boundary 연장선.

---

## Lifecycle 조사 (2026-04-22, plugin-lifecycle-auditor, cell: channels-lifecycle)

**스코프**: `src/channels/**`, `src/routing/**` (upstream/main abf940db61)
**선행 확인**: upstream e8fd148437 + 2a283e87a7 + c95507978f (plugins-lifecycle-001 resolved) 의 snapshot/rollback 패턴이 channels 측에 파급됐는지 점검 → channels 는 read-only registry facade 라 해당 패턴 필요 없음.

### 적용 카테고리

- [x] A. Load 실패 rollback 부재 — channels 쪽은 registry 를 직접 mutate 하지 않음 (src/plugins/ 에서 pin). 해당 없음.
- [x] B. Dispose / Unload 경로 누락 — 발견 2건 (FIND-001, FIND-002).
- [ ] C. Dynamic import 에러 격리 — `channels/session.ts:12 ??= import(...)` 는 plugins-error-boundary-003 와 동일 패턴, 이미 rejected (sibling module, R-7 transient production path 아님). 생략.
- [ ] D. Manifest parse 실패 partial state — channels 측 manifest 파싱 없음. 해당 없음.
- [x] E. Enable / Disable 상태 drift — FIND-001 (thread/message-tool api cache stale) + FIND-002 (logged error dedup stale) 가 본 축 교차.

### 발견 FIND

- **FIND-channels-lifecycle-001** (P2, lifecycle-gap):
  `thread-binding-api.ts:28 threadBindingApiCache` + `message-tool-api.ts:15
  messageToolApiCache` 가 channel id 만 키로 사용, registryVersion 무시 →
  plugin re-pin 이후 stale public-surface module 반환. `__testing` 네임스페이스의
  clear 만 노출. 동일 디렉터리 `configured-binding-compiler.ts:184-212`,
  `registry-loaded.ts:59-97`, `setup-registry.ts:55-80` 은 version 기반
  invalidation 을 이미 구현 — consistency gap.
- **FIND-channels-lifecycle-002** (P2, lifecycle-gap):
  `message-action-discovery.ts:43 loggedMessageActionErrors` Set 이
  pluginId+operation+message 키로 dedup 하지만 module-scope + test-only
  clear 로 plugin lifecycle 미연동. re-pin 후 새 plugin 의 동일 문자열
  regression 을 silence → 관측성 손실. 부차적으로 dynamic payload
  포함 error message 시 Set unbounded 성장.

### R-3 Grep 매핑 테이블 (register/cleanup/rollback)

| 모듈 | register/set 경로 | dispose/clear 경로 | 실행 조건 (R-5) | 평가 |
|---|---|---|---|---|
| `configured-binding-consumers.ts` | `registerConfiguredBindingConsumer` (line 52) | `unregisterConfiguredBindingConsumer` (line 67) | 양방향 존재, idempotent | **대칭 존재** |
| `stateful-target-drivers.ts` | `registerStatefulBindingTargetDriver` (line 44) | `unregisterStatefulBindingTargetDriver` (line 57) | 양방향 존재 | **대칭 존재** |
| `stateful-target-builtins.ts` | `ensureStatefulTargetBuiltinsRegistered` (async) (line 20) | `resetStatefulTargetBuiltinsForTesting` (test only) + catch 에서 `builtinsRegisteredPromise = null` | 부분 reset, acpDriverModulePromise 는 reset 안 됨 | **부분 대칭** — plugins-error-boundary-003 와 동일 ??= 캐시 패턴이라 신규 FIND 불가 (rejected) |
| `bootstrap-registry.ts` | `getBootstrapChannelPlugin` 간접 set (line 140, 160) | `clearBootstrapChannelPluginCache` (line 164) | production 호출 0건 | memory 축 (channels-memory 셀 후보) |
| `registry-loaded.ts` | `resolveCachedChannelPlugins` 버전 invalidation (line 57-98) | 자동 invalidation | version+ref 비교 | **대칭 존재 (모범)** |
| `setup-registry.ts` | `resolveCachedChannelSetupPlugins` (line 55-80) | 자동 invalidation | version+ref 비교 | **대칭 존재 (모범)** |
| `configured-binding-compiler.ts` | `compiledRegistryCache` (line 34, WeakMap) + `primeCompiledBindingRegistry` (line 201) | WeakMap 자동 GC + version 비교 | WeakMap + version | **대칭 존재 (모범)** |
| `thread-binding-api.ts` | `loadBundledChannelThreadBindingApi` (line 30-49) | `__testing.clearThreadBindingApiCache` (line 81) | **test-only** | **FIND-001** 대칭 결여 |
| `message-tool-api.ts` | `loadBundledChannelMessageToolApi` (line 17-36) | `__testing.clearMessageToolApiCache` (line 62) | **test-only** | **FIND-001** 대칭 결여 |
| `message-action-discovery.ts` | `loggedMessageActionErrors.add` (line 80) | `__testing.resetLoggedMessageActionErrors` (line 362) | **test-only** | **FIND-002** 대칭 결여 |
| `lifecycle-startup.ts` | plugin.lifecycle.runStartupMaintenance (line 17-22) | 없음 (maintenance 는 cleanup 아님) | 독립 try/catch per plugin | **cleanup 개념 부적절 — 결함 아님** |
| `stall-watchdog.ts` | setInterval (line 92) + abortSignal listener (line 91) | stop() 에서 clearInterval + removeEventListener (line 47-55) | abort-driven unconditional | **대칭 존재** |
| `typing-lifecycle.ts` | setInterval (line 33) | stop() clearInterval (line 42) | `closed` 플래그 재진입 방지 | **대칭 존재** |
| `typing.ts` | setInterval + setTimeout (line 56-62) | fireStop clearTtlTimer + keepalive.stop (line 87-96) | `closed` 플래그 | **대칭 존재** |
| `draft-stream-loop.ts` | setTimeout (line 59) | stop() + resetThrottleWindow() clearTimeout (line 81-95) | isStopped() 체크 | **대칭 존재** |
| `run-state-machine.ts` | setInterval (line 48) + abortSignal listener (line 70) | deactivate() / onAbort() clearHeartbeat (line 58-65) | abort + activeRuns<=0 | **대칭 존재** (onAbort once:true 로 자동 제거) |
| `status-reactions.ts` | setTimeout debounce/stall (line 222-292) | clearAllTimers (line 186-199) | `finished` 플래그 재진입 방지 | **대칭 존재** |

### 핵심 관찰

1. **lifecycle 대칭은 대부분 존재**: timer / listener / register 계열 cleanup 은
   production 경로에서 일관되게 구현되어 있다. 특히 `registry-loaded.ts`,
   `setup-registry.ts`, `configured-binding-compiler.ts` 는 `registryVersion`
   + `registryRef` 쌍 비교로 cache invalidation 을 구현한 **모범 패턴**.

2. **Gap 은 "plugin registry re-pin 이라는 lifecycle 전이에 version-aware
   invalidation 을 전파하지 않은 2차 캐시 2개 + dedup Set 1개"** 에 집중.
   FIND-001 과 FIND-002 는 같은 근본 축을 공유 → clusterer 가 epic 으로
   묶을 가능성 높음.

3. **channels-domain 의 registry mutation 은 전적으로 src/plugins/ 쪽에
   집중** (pin / activeVersion). channels/ 측은 read-only consumer 로서
   version 신호를 추적할 의무를 져야 하며, FIND-001/002 는 이 의무의
   국지적 누락.

4. **plugins-lifecycle-001 의 snapshot/rollback 패턴 (e8fd148437)** 은
   channels 측에 직접 적용 불가 (channels 는 registry array 를 mutate 하지
   않으므로 rollback 대상 없음). 그 대신 "downstream cache invalidation
   propagation" 이라는 별도 축이 channels 측 lifecycle gap 의 본질.

### 자체 한계 / 미확인 항목

- `src/plugins/runtime.ts` 의 `pinPluginChannelRegistry` 등 실제 re-pin
  production caller 가 어떤 flow 에서 호출되는지 추적 불가 (allowed_paths
  외). 이 호출이 shutdown-only 또는 test-only 라면 FIND-001/002 영향
  미미, P2 → P3 하향 가능.
- bundled channel extensions (`extensions/**`) 는 scope 외. FIND-001 의
  "plugin 아티팩트가 프로세스 수명 중 변경되는 시나리오" 빈도 정량
  데이터 부재.
- channels 측에서 error message 에 동적 payload (file path, timestamp 등)
  가 실제로 포함되는 빈도 — FIND-002 의 secondary unbounded 성장
  현실성 정량 불가.

### clusterer 를 위한 힌트

- **FIND-001 & FIND-002 공통 축**: "plugin channel registry version 을
  downstream 소비자 캐시에 전파하지 않음." 동일 root axis 이므로 epic
  CAND 로 묶어 SOL 단일 fix 후보 (add registryVersion+registryRef to
  cache key / clear on version bump).
- **cross-domain 관계**: plugins 도메인 FIND-plugins-memory-002
  (`openAllowlistWarningCache` Set 무제한) 와 FIND-channels-lifecycle-002
  (`loggedMessageActionErrors` Set) 는 **"module-scope dedup Set 이 test-only
  clear 만 보유"** 라는 상위 추상 패턴 공유. 다만 도메인/빈도/트리거가 상이
  하여 clusterer.md Step 3 의 "같은 인프라 축" 미충족 가능성 — 별도
  CAND 유지 + cross_refs 로만 연결하는 것을 권장.

---

## 클러스터 관찰 (2026-04-22, clusterer, error-boundary 후속)

- CAND-019 (single): FIND-channels-error-boundary-001 (draft-stream-loop) —
  R-5 분류 "conditional-edge + primary hot-path" (line 60/75 `void flush()`
  에 `.catch` 부재 + `pendingText = ""` 가 send 이전에 수행되어 reject 경로
  복구 불가). **데이터 유실 (draft chunk drop) 동반**, severity P2.
- CAND-020 (single): FIND-channels-error-boundary-002 (ack-reactions) —
  R-5 분류 "conditional-edge + secondary visual" (`.then(onFulfilled)` 만
  지정, onError 가 remove() 전용으로 오배선). **stale emoji + unhandled
  rejection**, severity P3.

**Epic 지양 근거**: 두 FIND 는 "fire-and-forget 에 onRejected 누락" 이라는
상위 관찰 테마를 공유하지만:
- severity 차이 (P2 vs P3) — CONTRIBUTING.md PR 분리 선호.
- fix 축 상이:
  - FIND-001: `void flush().catch(onError)` 부착 + pendingText 복구 전략
    재설계 (retry-safe). multi-step.
  - FIND-002: `.then(fn1, fn2)` 로 onRejected 추가 or caller 계약 변경.
    single-line.
- symptom 차이: 데이터 유실 (FIND-001) vs 시각적 불일치 (FIND-002).

→ 각각 single CAND. gateway-memory/gateway-concurrency 셀에서 확립된 "동일
상위 테마라도 fix 축이 다르면 single" 기준을 본 셀에도 적용.

**upstream 중복 검사 (CAL-008)**: `git log upstream/main --since="3 weeks ago"
-- src/channels/draft-stream-loop.ts src/channels/ack-reactions.ts`
→ 0 commits. CAL-004 상황 아님.

**공통 반증 고려사항 (publisher 단계 전 확인 가치)**:
- plugin-sdk 또는 reply-payload.ts 수준에서 throw 를 미리 wrap 하는 상위
  레이어가 있다면 두 FIND 모두 P3 이하로 하향 or drop.
- `sent === false` (draft-stream-loop) 를 반환하는 adapter 가 실재하는지
  미확인 — 존재하면 resolve 경로 복구는 정상.
- ackReactionPromise 생성부에서 caller 가 선-`.catch` 부착하는 계약이 실재
  하는지 미확인 — 존재하면 CAND-020 drop.
