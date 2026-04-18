---
id: FIND-plugins-memory-001
cell: plugins-memory
title: registryCache Map 에 eviction/TTL 정책 없이 계속 set 만 호출됨
file: src/plugins/loader.ts
line_range: 196-199
evidence: '```ts

  const MAX_PLUGIN_REGISTRY_CACHE_ENTRIES = 128;

  let pluginRegistryCacheEntryCap = MAX_PLUGIN_REGISTRY_CACHE_ENTRIES;

  const registryCache = new Map<string, CachedPluginState>();

  const inFlightPluginRegistryLoads = new Set<string>();

  ```

  '
symptom_type: memory-leak
problem: registryCache Map 의 cap(MAX=128) 은 상수로 정의되어 있지만, cap 도달 시 오래된 항목을 제거하는 eviction
  정책이 Map 기본 동작에만 의존. Map 은 FIFO/LRU 자동 evict 없이 단순히 키 덮어쓰기만 함.
mechanism: '1. 플러그인 load 시점에 registryCache.set(key, CachedPluginState) 호출

  2. 키는 플러그인 id + version 조합 — version 변경 시 새 key

  3. 128 개 넘으면 Map 은 그저 삽입 계속. 메모리는 계속 성장

  4. runtime 중 flush/clear 호출 경로 부재 (process exit teardown 외)

  '
root_cause_chain:
- why: 왜 registryCache 가 무한히 성장할 수 있는가
  because: Map 기본 동작은 cap 없이 set 마다 추가, 명시적 eviction 정책 부재
  evidence_ref: src/plugins/loader.ts:198
- why: 왜 MAX_PLUGIN_REGISTRY_CACHE_ENTRIES 상수가 실효적으로 미사용인가
  because: cap 비교·삭제 로직이 이 파일 어디에도 없음 (Grep 결과)
  evidence_ref: src/plugins/loader.ts:196
- why: 왜 dispose/teardown 경로가 runtime cache eviction 을 커버하지 않는가
  because: N/A — dispose 경로를 모두 확인하지 못했음. 더 많은 grep 필요
  evidence_ref: N/A — subsequent Round B work
impact_hypothesis: memory-growth
impact_detail: '정성: 장시간 실행되는 gateway 프로세스에서 plugin version 이 다양하게 load 될 경우 registryCache
  가 점진적으로 성장.

  정량: cap 비교 없으면 N 개 plugin × M 개 version → O(N×M) 엔트리.

  '
severity: P2
counter_evidence:
  path: null
  line: null
  reason: 'none_found: dispose/teardown 경로를 Read 로 전부 확인하지 못함. flushRegistryCache
    같은 명시적 cleanup API 는 이 파일에서 발견 못함. 기존 test 에서 Map 크기 검증 없음 (vitest.plugins.config.ts
    샘플).'
status: rejected
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
rejected_reasons:
  - "retracted: gatekeeper counter_evidence 에서 setCachedPluginRegistry (loader.ts:346-352) 의 while-loop FIFO eviction 로직 발견. registryCache 는 실제로 pluginRegistryCacheEntryCap 상한 강제됨. FIND 는 선언부(196-199) 만 보고 cap 강제 로직을 누락한 성급한 결론."
  - "evidence retained for calibration in metrics/shadow-runs.jsonl"
---
# registryCache Map 에 eviction/TTL 정책 없이 계속 set 만 호출됨

## 문제
registryCache 는 플러그인 로드 상태 캐시로 Map 자료구조를 사용한다. MAX_PLUGIN_REGISTRY_CACHE_ENTRIES=128 상수는 선언돼 있으나 이 값에 기반한 eviction/삭제 로직이 발견되지 않는다.

## 발현 메커니즘
1. loader 경로에서 플러그인 load → registryCache.set(key, state)
2. 키가 새로울 때마다 Map 크기 증가
3. 128 임계 돌파 후에도 eviction 없이 계속 성장
4. runtime 중 registryCache.clear() 나 TTL 기반 만료 로직 부재

## 근본 원인 분석
1. 왜 registryCache 가 무한 성장할 수 있는가? → Map 기본 동작 (loader.ts:198)
2. 왜 MAX 상수가 실효적 미사용인가? → cap 비교 로직 없음 (loader.ts:196)
3. 왜 dispose 가 이를 커버하지 않는가? → 추가 조사 필요

## 영향
장시간 gateway 프로세스에서 plugin version 변경이 누적되면 메모리 증가. 당장 OOM 은 아니나 관측 가능.

## 반증 탐색
- 숨은 방어: `rg -n "registryCache\.(clear|delete)" src/plugins/` — match 없음.
- 기존 테스트: `src/plugins/loader.test.ts` 에서 Map 크기 검증 테스트 없음.
탐색 카테고리 2 적용. 결과 반증 없음.

## Self-check

### 내가 확실한 근거
- src/plugins/loader.ts:196-199 의 선언부
- eviction 로직 부재는 해당 파일 내 Grep 결과

### 내가 한 가정
- plugin version 이 다양하게 load 된다는 가정 (실제 빈도 미확인)
- dispose 경로가 cache clear 를 하지 않을 거라는 가정 (전면 Grep 안 했음)

### 확인 안 한 것 중 영향 가능성
- src/plugins/loader.ts 파일 전체 (2274줄) Read 미완료
- dispose/teardown 경로의 cleanup 범위
