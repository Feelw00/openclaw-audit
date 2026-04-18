# plugins 도메인 감시 기록

## 도메인 개요

`src/plugins/**` 와 `src/plugin-sdk/**` 는 openclaw 의 플러그인 발견, 로드, 레지스트리, 계약 검증을 담당하는 핵심 도메인.

### 주요 모듈

- `loader.ts` — 플러그인 로드 orchestration, manifest 검증, registry 캐싱
- `setup-registry.ts` — setup-time provider/cli backend 로드
- `public-surface-loader.ts` — bundled plugin public surface 캐싱
- `runtime-web-channel-plugin.ts` — WhatsApp web channel 플러그인 런타임 모듈 로더
- `manifest-registry.ts` — plugin manifest 파싱/검증
- `discovery.ts` — 파일시스템 기반 플러그인 탐지

---

## 실행 이력

### memory-leak-hunter (2026-04-18)

**적용 카테고리:**
- [x] A. 무제한 자료구조 성장 — 발견 2건
- [x] B. EventEmitter/리스너 누수 — 발견 0건 (해당 없음, process.on/off 쌍 properly paired)
- [x] C. 강한 참조 체인 — skipped (심화 분석 필요)
- [x] D. 핸들/리소스 누수 — skipped (fs, HTTP 관련 코드 minimal)
- [x] E. 캐시 TTL 부재 — 발견 2건 (registryCache 는 capped 하지만 유사 구조 있음)

**발견 FIND:**
- FIND-plugins-memory-002: `openAllowlistWarningCache` Set 무제한 증가
- FIND-plugins-memory-003: `runtime-web-channel-plugin.ts` jitiLoaders Map cleanup 부재

**주요 관찰:**

1. **registryCache LRU 정책 존재**: loader.ts:346 에서 cap 도달 시 oldest key 를 delete 하는 eviction 로직이 있음 → FIND-001 의 우려는 부분적으로 해결됨.

2. **openAllowlistWarningCache 특이성**: 경고 dedup 용도로 설계되었으나 clearPluginLoaderCache() 가 process-level teardown/테스트 cleanup 외에는 호출되지 않음 → 프로덕션 중 무제한 누적 가능.

3. **jitiLoaders 패턴**: public-surface-loader.ts, setup-registry.ts, runtime-web-channel-plugin.ts, doctor-contract-registry.ts, bundled-capability-runtime.ts 등 여러 파일에 동일 패턴 (Map 선언, getCachedPluginJitiLoader 호출, cleanup 없음).

4. **jiti loader lifecycle**: jiti 라이브러리의 require.cache 메커니즘으로 인해 버전/경로 변경 시 이전 loader 인스턴스들이 메모리에 계속 유지됨. 특히 web channel plugin 처럼 자주 재로드되는 module 은 누적이 두드러질 수 있음.

5. **EventEmitter 리스너**: setup-registry.test.ts:29-35 에서 process.on/off 쌍이 properly paired 되어 있음. 플러그인 내부 hook runner 도 listener 추가/제거가 대칭적.

---

## 다음 페르소나를 위한 힌트

### lifecycle-auditor (plugins-lifecycle)

1. **load 실패 시 rollback**: loader.ts 에서 load error 발생 시 registry.diagnostics 에 기록만 하고, 부분 entry 가 registry 에 남는지 확인.
2. **manifest-registry parse 실패**: manifest-registry.ts 에서 parse 실패 후 partial entry cleanup 이 제대로 되는지.
3. **dispose/unload 경로**: clearPluginLoaderCache(), clearAgentHarnesses(), clearCompactionProviders() 등의 함수들이 실제로 호출되는지, 그리고 플러그인 언로드 시 모두 호출되는지.
4. **jiti loader cleanup**: jitiLoaders.clear() 메소드 호출 경로 확인. 특히 bundled 플러그인 disable 시.

### concurrency-auditor (plugins 장기 도입 예정)

1. **Promise.race 처리**: plugin load timeout 시 loser promise handling.
2. **registry mutation**: 동시 load 시 inFlightPluginRegistryLoads 를 이용한 serialization 이 제대로 작동하는지.

---

## 기술 빚 / 미결

1. **jitiLoaders cleanup 미정책**: 여러 module 에서 동일 패턴으로 jiti loader cache 를 유지하지만, 명시적 cleanup API 가 부재. 다음 phase 에서 통일된 lifecycle hook 도입 필요.

2. **openAllowlistWarningCache 용도 재검토**: 현재 process 생애 동안 모든 warning 을 suppress 하는 것이 의도인지, 아니면 per-load-cycle 또는 TTL 기반이어야 하는지 명확화 필요.

3. **clearPluginLoaderCache 호출 빈도**: 검색 결과 테스트 환경에서만 명시적으로 호출되고 있음. 프로덕션 gateway shutdown 에서 호출되는지 확인 필요.

### clusterer (2026-04-18)

- CAND-001 (epic): 공통 원인 "plugins 도메인 글로벌 Map 캐시에 eviction 정책 부재" 로 2 FIND 묶음 (FIND-plugins-memory-001: registryCache, FIND-plugins-memory-003: jitiLoaders)

### clusterer (2026-04-18, 2차)

- 본 배치(ready/ 5건)에 FIND-plugins-memory-003 이 포함되어 있었으나 이미 기존 CAND-001 에 수용되어
  있으므로 새 CAND 생성하지 않음 (CAND-001 은 `needs-human-review` 상태, 본 세션에서 수정 금지).
- 다른 도메인 FIND 들(cron 계열: runningAtMs 관련, agents-registry 계열: sweeper self-stop) 과
  plugins FIND-003(jitiLoaders eviction 부재) 의 root_cause_chain 의미론 비교:
  - cron 은 "특정 필드의 claim/liveness 동시성" 축 — 공통성 없음.
  - agents-registry 는 "sweeper cleanup 조건 불완전" 축 — jitiLoaders 는 sweeper 가 아예 없는
    unbounded Map 이므로 축 다름.
  - 결론: plugins FIND-003 을 다른 도메인 FIND 와 묶을 epic 근거 없음. CAND-001 유지.

### plugin-lifecycle-auditor (2026-04-18)

**적용 카테고리:**
- [x] A. Load 실패 rollback 부재 — 발견 1건 (FIND-plugins-lifecycle-001)
- [ ] B. Dispose/Unload 경로 누락 — skipped (본 세션 토큰 예산. R-3 Grep 결과로
      production `unregister*`/`dispose` 부재 확인했으나 결함으로 승격 전에 loader 세션 수명
      경계와 gateway shutdown 호출 경로 확인 필요)
- [ ] C. Dynamic import 에러 격리 — skipped (loader.ts:1653-1677 recordPluginError 경로로
      jiti throw 는 이미 격리됨. 단일 플러그인 throw 가 for 루프를 중단시키지 않음 — 반증이
      이미 존재하여 결함 없음)
- [ ] D. Manifest parse 실패 후 partial state — skipped (manifest-registry.ts:532-538
      `manifestRes.ok` false 시 continue 로 clean skip. records.push 는 단일 시점.
      이론적으로 `seenIds.set` (line 631) 이 `records.push` (line 634) 이전이라 throw 발생
      시 gap 가능하나 buildRecord throw 는 매우 희귀하고 다음 load 가 cache 초기화로 해소됨)
- [ ] E. Enable/Disable 상태 drift — skipped (enable.ts 는 24 LOC 로 shallow, toggle-config 확인 필요)

**발견 FIND:**
- FIND-plugins-lifecycle-001 (P1, lifecycle-gap): register() throw 시 httpRoutes/services/commands/hooks 부분 등록 잔존

**R-3 Grep 결과 — dispose/unload/cleanup 경로 매핑 테이블:**

| register 함수 | registry 필드 | dispose/cleanup 경로 | 상태 |
|---|---|---|---|
| `registerHttpRoute` (registry.ts:351) | `registry.httpRoutes` (직접 push) | 없음 | 대칭 결여 |
| `registerService` (registry.ts:941) | `registry.services` (직접 push) | 없음 | 대칭 결여 |
| `registerCommand` (registry.ts:971) | `registry.commands` / command-registry-state (activate 분기) | 없음 | 대칭 결여 |
| `registerHook` (registry.ts:213) | `registry.hooks` (직접 push 추정) | 없음 | 대칭 결여 |
| `registerGatewayMethod` (registry.ts:308) | `registry.gatewayMethods` | 없음 | 대칭 결여 |
| `registerProvider` (registry.ts:501) | `registry.providers` | 없음 | 대칭 결여 |
| `registerAgentHarness` | process-global via registerAgentHarness() | **restoreRegisteredAgentHarnesses** (loader.ts:1805,1826,1840) | **대칭 존재** |
| `registerCompactionProvider` | process-global | **restoreRegisteredCompactionProviders** (loader.ts:1806,1827,1841) | **대칭 존재** |
| `registerMemoryEmbeddingProvider` | process-global | **restoreRegisteredMemoryEmbeddingProviders** (loader.ts:1807,1828,1842) | **대칭 존재** |
| `registerMemoryRuntime`/Prompt/Corpus/FlushPlan | process-global memory state | **restoreMemoryPluginState** (loader.ts:1829-1835, 1843-1849) | **대칭 존재** |

**핵심 관찰:**
1. registry 객체의 **배열 필드** (httpRoutes/services/commands/hooks/gatewayMethods/providers) 는 rollback 대상에서 일관되게 제외됨. loader 는 process-global 싱글톤 4종만 복구함.
2. manifest-registry 의 partial state 는 grid hint 와 달리 실제로는 관측되지 않음. parse 실패 시 continue 로 clean skip.
3. 런타임 plugin 타입별 `dispose()` 메소드 호출 일관성: runtime-channel.ts:275-315 에서 channel subscription 에 대한 dispose 는 존재하나, 플러그인 차원의 통합 dispose API 는 부재 (플러그인 전체 unload 개념 자체가 production 에 구현되지 않음 — uninstall.ts 는 config 수정용이며 runtime registry 를 건드리지 않음).

**자체 한계:**
- `src/plugins/plugin-graceful-init-failure.test.ts` 및 `loader.runtime-registry.test.ts` 내부 미확인 — 이들이 부분 등록 잔존을 기대/금지하는지 확인 필요.
- gateway 소비자 (src/gateway/**) 가 route/service 를 status 필터링하는지 — allowed_paths 외라 미확인.
- recordPluginError 의 다른 4개 호출 지점 중 register phase 외 (load/validation phase) 는 api 호출 이전이므로 본 FIND 영향 없음을 가정.

### clusterer (2026-04-18)

- **CAND-005 (single)**: FIND-plugins-lifecycle-001. register() throw 시
  httpRoutes/services/commands/hooks 부분 등록 rollback 부재 (P1).
- **Cross-domain 관찰**: 동 배치의 FIND-infra-process-error-boundary-003
  (emitGatewayRestart catch 의 sigusr1AuthorizedCount 누수) 와 "catch 블록
  부분 상태 롤백" 이라는 상위 추상 패턴을 공유함. 그러나 도메인/자료구조/
  실패 경로가 모두 상이 (registry 배열 vs 카운터; 플러그인 내부 throw vs
  process.kill EPERM) 하고 clusterer.md Step 3 의 "같은 인프라 축" 기준을
  충족하지 않아 epic 불가. 각각 single CAND 로 분리 (CAND-005, CAND-006)
  하고 cross_refs 로만 관련성 표시.
