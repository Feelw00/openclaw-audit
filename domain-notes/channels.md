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

---

## Memory 축 조사 (2026-05-14, memory-leak-hunter, cell: channels-memory, Phase 6)

**스코프**: `src/channels/**`, `src/routing/**` (upstream/main af3d9333aa)
**선행 확인**: channels-error-boundary (Phase 3) + channels-lifecycle (Phase 3) 이미
완료. CAND-019/020 abandoned (primary-path inversion). lifecycle FIND-001/002
가 `thread-binding-api`/`message-tool-api`/`loggedMessageActionErrors` 의
plugin re-pin signal 전파 부재를 다룸 — 본 memory 셀에서 동일 코드 재진입 시
cross-axis dup 발생 위험 → 회피.

### 적용 카테고리

- [x] applied — 무제한 Map/Set 성장 (module-scope, fn-scope 분리)
- [x] applied — setInterval/setTimeout 미정리 (clearTimeout/clearInterval 대칭)
- [x] applied — EventEmitter / abortSignal listener 누적 (addEventListener vs removeEventListener)
- [x] applied — 캐시 TTL/cap 부재 (LRU/WeakMap 사용 여부)
- [x] applied — Strong reference chain (WeakMap 미사용 한계)

### Module-scope long-lived state 인벤토리 (production 경로)

| 파일:라인 | 자료구조 | 키 도메인 | cap/eviction | R-5 평가 |
|---|---|---|---|---|
| `routing/account-id.ts:12,13` | normalize cache (2 Map) | account id 문자열 | `cap 512 + FIFO` (setNormalizeCache) | unconditional |
| `routing/resolve-route.ts:127` `agentLookupCacheByCfg` | WeakMap | OpenClawConfig | WeakMap + agentsRef ID-equality 무효화 | unconditional |
| `routing/resolve-route.ts:204` `evaluatedBindingsCacheByCfg` | WeakMap | OpenClawConfig | WeakMap + inner `MAX_EVALUATED_BINDINGS_CACHE_KEYS=2000` clear-on-overflow | unconditional |
| `routing/resolve-route.ts:206` `resolvedRouteCacheByCfg` | WeakMap | OpenClawConfig | WeakMap + inner `MAX_RESOLVED_ROUTE_CACHE_KEYS=4000` clear-on-overflow | unconditional |
| `channels/bundled-channel-catalog-read.ts:23` `officialCatalogFileCache` | Map | `listPackageRoots()` (≤2 paths) | bounded-by-domain (cap 불요) | conditional but domain-bounded |
| `channels/plugins/catalog.ts:75` `officialCatalogEntriesByPath` | Map | resolveOfficialCatalogPaths(≤4 paths) | bounded-by-domain | conditional but domain-bounded |
| `channels/plugins/bundled.ts:96` `bundledChannelLoadContextsByRoot` | Map | rootScope.cacheKey | `MAX_BUNDLED_CHANNEL_LOAD_CONTEXTS=32` FIFO (line 329-334) | unconditional |
| `channels/plugins/bundled.ts:97` `sourceBundledEntryLoaderCache` | Map | modulePath-derived | cap 없으나 bundled plugin set = build-time fixed (~5 paths) | conditional but domain-bounded |
| `channels/plugins/module-loader.ts:14` `jitiLoaders` | Map | scopedCacheKey = `${import.meta.url}::channel-plugin-module-loader::${aliasMap+tryNative}` (modulePath 비포함) | cap 없으나 cacheKey 도메인이 alias/tryNative 의 함수 — 매우 작음 | conditional but domain-bounded |
| `channels/plugins/package-state-probes.ts:32` `sourcePackageStateLoaderCache` | Map | bundled channel catalog enum 의 specifier path | cap 없으나 enum 도메인 bounded | conditional but domain-bounded |
| `channels/plugins/read-only.ts:44` `moduleLoaders` | Map | loader (line 105) 또는 manifest setupSource (line 386) | cap 없으나 plugin manifest set bounded | conditional but domain-bounded |
| `channels/plugins/stateful-target-drivers.ts:38` | Map | driver id | `unregister` 양방향 (line 57-58) | unconditional symmetric |
| `channels/plugins/configured-binding-consumers.ts:35` | Map | consumer id | `unregister` 양방향 | unconditional symmetric |
| `channels/plugins/message-action-discovery.ts:43` `loggedMessageActionErrors` | Set | pluginId+op+message | test-only clear (line 362) | **channels-lifecycle FIND-002 cross-axis** — 본 셀에서 신규 FIND 만들지 않음 |
| `channels/plugins/thread-binding-api.ts` cache + `message-tool-api.ts` cache | Map | channel id | test-only clear | **channels-lifecycle FIND-001 cross-axis** — 본 셀에서 신규 FIND 만들지 않음 |

### Timer / listener parity (전부 대칭, R-3 + R-5 통과)

| 모듈 | 등록 | 해제 | 조건 |
|---|---|---|---|
| `draft-stream-loop.ts:59` setTimeout | `clearTimeout` line 22/84/94 | unconditional (stop()+ resetThrottleWindow) |
| `status-reactions.ts:225/229/307` setTimeout (×3) | `clearAllTimers` line 189-202 + early-exit | unconditional |
| `transport/stall-watchdog.ts:92` setInterval + `:91` addEventListener("abort") | `clearInterval` line 39 + `removeEventListener` line 54 | unconditional |
| `typing-lifecycle.ts:33` setInterval | `clearInterval` line 42 | unconditional |
| `typing.ts:56` setTimeout (ttl) | `clearTimeout` line 66 | unconditional |
| `run-state-machine.ts:48` setInterval + `:70` addEventListener("abort", once:true) | `clearInterval` line 40 + auto-detach | unconditional |
| `plugins/binding-routing.ts:173` setTimeout (race) | `clearTimeout` line 197 | unconditional (Promise.race finally) |

### status-reactions.ts `activeEmojis` Set (라인 176)

controller closure-scope (turn-scoped lifetime). `applyEmoji` (line 268) add,
`removeActiveEmojis` (line 250) delete. `clear()`/`finishWithEmoji()` 가
무조건 호출 — controller 종료 시 모든 emoji 제거. **unconditional cleanup**.

### CAL-008 upstream 검사 (6주 기준)

`git log upstream/main --since="6 weeks ago" -- src/channels src/routing` 의
memory/cache 관련 fix:
- `7b05b4b68e fix(channels): share plugin module jiti cache helper` (2026-04-14)
- `3e63b7c112 fix: align channel module loader cache import` (2026-05-02)
- `855c220a63 fix(channels): preserve bundled channel load caches` (2026-04-29)
- `e27fe55aa8 refactor: simplify plugin cache boundaries` (2026-04-29)
- `7a5b419843 refactor(plugins): simplify plugin cache boundaries`
- `1ecd46f49b fix(channels): cache selected channel registry lookups`
- `dc469a3db5 fix(gateway): preserve channel plugin identity in cache`
- `cdaa70facb refactor: cache repeated lazy imports`
- `ad0d87d881 perf: cache startup package metadata`

→ **upstream 이 channel plugin/module loader cache 영역에 활발히 정리 중**.
module-loader.ts:14 `jitiLoaders` 는 7b05b4b68e 가 이미 jiti-loader-cache 공용
헬퍼로 옮김. 본 셀에서 신규 memory FIND 만들면 CAL-004/CAL-008 (upstream
parallel work) dup 위험.

### 결론 — FIND 0건 (abandon)

본 채널/라우팅 memory 축의 production-relevant 무제한 자료구조는 부재:
1. **routing 측 캐시 3종** 은 WeakMap + clear-on-overflow cap 으로 보호됨.
2. **module-scope Map/Set 11개** 중 9개는 cap+FIFO/WeakMap 또는 build-time fixed
   key 도메인 (≤32 entries) 으로 bounded. 나머지 2개 (thread-binding-api /
   message-tool-api / loggedMessageActionErrors) 는 channels-lifecycle FIND-001/002
   와 동일 코드 영역 — cross-axis dup 회피.
3. **adapter parity**: 4 bundled adapter (telegram/slack/discord/whatsapp) 는
   `src/channels/extensions/**` 에 있어 본 셀 allowed_paths 외. plugin 코드
   소관 — 본 channels-domain 코드 내 module-scope adapter-specific state 없음.
4. **timer/listener 7종** 전부 production-path unconditional cleanup.
5. **CAL-008**: upstream 이 이미 module loader cache 영역을 적극 정리 중 — 신규
   FIND 가 평가 단계에서 dup 으로 abandon 될 가능성 매우 높음.

### 자체 한계

- adapter 자체 (`extensions/**` 하의 telegram/slack/discord/whatsapp) 의
  in-memory state (rate-limit, dedup, presence) 는 scope 외라 확인 불가.
  본 셀은 channels routing core 만 다룸.
- `read-only.ts:44` `moduleLoaders` 의 line 386 호출은 `params.record.setupSource`
  (per-manifest path) — manifest set 크기가 production 에서 10 미만이라 추정,
  실제 telemetry 데이터 부재.
- routing WeakMap 캐시 3종은 cfg lifetime 에 묶이는데, openclaw 가 config
  reload 시 cfg object 를 replace 하는지 mutate 하는지 미확인. replace 면
  GC 후 cleanup 자동.

---

## Concurrency 축 조사 (2026-05-14, concurrency-auditor, cell: channels-concurrency, Phase 6 batch 2)

**스코프**: `src/channels/**`, `src/routing/**` (upstream/main af3d9333aa)
**선행 확인**: channels-error-boundary/lifecycle/memory 모두 완료. CAND-019/020 abandoned
(primary-path inversion). memory FIND 0건. 본 셀은 race-condition 축으로만 신규 탐색.

### 적용 카테고리

- [x] applied — shared mutable state race (check-then-act)
- [x] applied — Promise.race loser
- [x] applied — listener register race
- [x] applied — AbortController 전파
- [x] applied — primary-path inversion (lock/CAS 탐색)
- [x] applied — hot-path vs test-path
- [x] skipped — microtask ordering — 사유: queueMicrotask/setImmediate/process.nextTick
  매치 0건 (R-3 grep 5/5).

### R-3 Grep 매핑 (전체 결과)

| 분류 | 결과 |
|---|---|
| Mutex/Semaphore/AsyncLock | 0 매치 |
| AbortController/AbortSignal | 8 매치 — 모두 입력 receiver / receive.ts:25 neverAbortedSignal default / send.ts:106 default / message types — abort 가 receive ack 와 통합되지 않음 |
| Promise.race | 1 매치 — binding-routing.ts:178 (intentional with logging loser observer, 8ed52c1463) |
| Promise.all/allSettled | 4 매치 — message-access state.ts / runtime.ts (independent fan-out, race 없음) |
| listener register (`addEventListener`) | 2 매치 — stall-watchdog.ts:91 / run-state-machine.ts:70 (둘 다 once:true + removeEventListener 대칭) |
| listener cleanup (`removeEventListener` 등) | 1 매치 — stall-watchdog.ts:54 (대칭 정상) |
| microtask | 0 매치 |

### 핵심 race 후보 평가

| 후보 | 위치 | 평가 | 결과 |
|---|---|---|---|
| typing-lifecycle tickInFlight | typing-lifecycle.ts:38-45 | stop 이 in-flight tick 의 플래그 강제 reset → restart 시 concurrent onTick | **FIND-001 (P3)** |
| MessageReceiveContext.ack | message/receive.ts:69-77 | check-then-await-then-set, in-flight token 부재 | **FIND-002 (P3, R-7 한계)** |
| binding-routing Promise.race loser | binding-routing.ts:178 | 30s timeout 후 readyPromise 가 abort 안 됨, side effect 누수 | **기각** — 8ed52c1463 commit 메시지 + L187 `readyPromise.then(...)` 의 명시적 observer 로 author 가 의도적 채택. AbortSignal 통합은 ensureReady 계약 변경 필요 (out-of-scope architectural). |
| ack-reactions.removeAckReactionAfterReply | ack-reactions.ts:134-139 | `.then` 만 있고 onRejected 없음 | **기각** — CAND-020 abandoned 와 동일 코드. primary-path inversion. |
| status-reactions enqueue chain | status-reactions.ts:181-184 `chainPromise.then(fn, fn)` | 두 핸들러 모두 동일 fn 으로 reject 도 동일 처리. serialization 정상 | **safe** (대칭 처리) |
| status-reactions scheduleEmoji race | status-reactions.ts:280-320 | pendingEmoji 의 sync 영역 mutation + chain 직렬화 | **safe** — chain 으로 serialize, race window 부재 |
| draft-stream-loop flush re-entrance | draft-stream-loop.ts:20-52 | inFlightPromise 가드로 multiple flush 가 co-operative | **safe** — inFlightPromise check + await 로 직렬화 |
| run-state-machine activeRuns | run-state-machine.ts:18-99 | closure-scope, 모든 mutation sync | **safe** (closure 단일) |
| typing.ts onReplyStart 의 stopSent 재설정 | typing.ts:75 | 다음 turn 시작 시 reset, fireStop 와 race 가능하나 closed 플래그가 우선 차단 | **safe** |
| stateful-target-builtins.ts ??= 캐시 | stateful-target-builtins.ts:9-32 | plugins-error-boundary-003 와 동일 패턴 (R-7 transient 아님) | **기각** (lifecycle 셀에서 이미 abandon) |
| inboundSessionRuntimePromise ??= import | session.ts:7-14 | 동일 ??= 패턴, init guard | **safe** (single-shot import) |
| listBindings 순회 + bind/unbind | routing/bindings.ts 전체 | 전부 sync + pure, mutation 없음 | **safe** |
| resolveAccountEntry | routing/account-lookup.ts | 전부 sync + pure | **safe** (cell hint 의 "fetch dedup" 은 hint 가 잘못 — async fetch 없음) |

### Promise.race loser 의 의도적 누수 (binding-routing.ts:165-199)

upstream commit **8ed52c1463** (2026-04-26, "fix: bound configured acp binding readiness") 가
명시적으로 Promise.race + 명시적 then() observer 패턴을 채택:

```ts
readyPromise.then(
  (lateResult) => logVerbose(`... settled after timeout (ok=${lateResult.ok})`),
  (err) => logVerbose(`... rejected after timeout: ${String(err)}`),
);
```

author 가 loser 의 fate 를 관측은 하나 cancel 하지 않음. 이는 `ensureConfiguredBindingTargetReady`
→ `driver.ensureReady` 의 contract 가 AbortSignal 을 받지 않기 때문 (acp-stateful-target-driver.ts:70-90).
loser 의 side effect (ACP subprocess setup, socket open) 가 누수될 수 있으나 architectural
변경 필요 — 본 셀의 FIND 로 분리 안 함.

### Hot-path vs test-path 비교 (R-7 핵심)

- **FIND-001 (typing-lifecycle)**: production caller (typing.ts:71-88 onReplyStart) 가
  stop→fireStart→start sequence 를 매 reply turn 마다 실행. mock 으로 다른 branch 강제
  필요 없음. **production 일치 확인**.
- **FIND-002 (receive ack)**: production caller 가 src/channels/extensions/** (out-of-scope)
  로 본 셀 내 verification 불가. 따라서 P3 + counter_evidence 에 한계 명시. **R-7 한계 상태**.

### CAL-008 upstream 검사 (6주 기준)

`git log upstream/main --since="6 weeks ago" -- src/channels src/routing` 중 race 키워드:
- `8ed52c1463 fix: bound configured acp binding readiness` — Promise.race + loser
  observer 패턴 도입. 본 셀 FIND-002 가 영향받는 영역 아님 (receive.ts).
- `45b8645079 fix(channels): keep typing indicators off reply critical path` — typing.ts
  의 onReplyStart 를 `await fireStart() + start()` → `void fireStart().then(start)` 로
  변경 (2026-05-01). FIND-001 의 typing-lifecycle 자체는 수정 안 됨. **typing 영역
  parallel work 중** — FIND-001 가 reviewer 에 의해 typing.ts 측 수정 일환으로
  유보될 가능성 있음.

### 결론 — FIND 2건 (둘 다 P3)

1. **FIND-001**: typing-lifecycle stop() 의 tickInFlight reset 으로 인한 in-flight tick
   ownership 침범. Production hot-path 일치. 그러나 영향은 typing indicator 시각적
   불일치 + counter race → P3.
2. **FIND-002**: receive context ack() 의 check-then-await-then-set race. plugin SDK
   로 export 되어 4 adapter 사용. 실 production caller 가 allowed_paths 외이므로
   R-7 한계 → P3 borderline.

clusterer/gatekeeper 단계에서 추가 정보 반영 후 abandon/re-promote 결정 가능.

### Clusterer 를 위한 힌트

- **FIND-001 + 가능성 있는 SOL**: tick self-token 패턴 (각 tick 이 myToken 들고
  finally 에서 자기 token 일 때만 reset) 또는 stop() 에서 tickInFlight reset 제거.
  fix 가 typing-lifecycle 단일 파일 수정 → XS PR. 단 upstream 45b8645079 의 typing.ts
  연쇄 수정과 충돌 검토 필요.
- **FIND-002 + 가능성 있는 SOL**: in-flight token 패턴 — `pendingAckPromise = onAck()`
  캐싱 후 두 번째 호출자가 동일 promise 를 await. ackState 를 "pending|acking|acked|
  nacked" 4-상태로 확장하는 대안도 가능. fix 가 receive.ts 단일 함수 → S PR.
- **cross-axis**: 둘 다 "check-then-act with await between" 동일 root pattern. 그러나
  fix 축이 다르므로 epic 묶지 말고 single CAND.

### 자체 한계 / 미확인 항목

- FIND-002 의 실제 caller 확인: src/channels/extensions/** 가 allowed_paths 외라 4
  adapter 의 ack() 사용 패턴 (single-path vs fan-in) 검증 불가. clusterer 가
  필요 시 expanded scope 로 재검토.
- FIND-001 의 race window 정량: tick 의 onTick (= fireStart) 의 platform API
  latency 실측 데이터 부재. 추정 100ms-수 초.
- typing-start-guard.ts:41 `consecutiveFailures += 1` 의 concurrent race-counter
  영향: 이론상 V8 single-thread 라 ++ 자체는 atomic 이지만 두 catch path 가 모두
  진입하면 두 번 증분 — onTrip 의도와 차이.
- 4 adapter (telegram/slack/discord/whatsapp) 의 platform-specific ack idempotency
  실측 데이터 부재.
