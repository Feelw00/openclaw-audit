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
