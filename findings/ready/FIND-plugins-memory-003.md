---
id: FIND-plugins-memory-003
cell: plugins-memory
title: runtime-web-channel-plugin.ts 의 jitiLoaders Map과 module cache 에 cleanup 경로
  없음
file: src/plugins/runtime/runtime-web-channel-plugin.ts
line_range: 106-111
evidence: '```ts

  let cachedHeavyModulePath: string | null = null;

  let cachedHeavyModule: WebChannelHeavyRuntimeModule | null = null;

  let cachedLightModulePath: string | null = null;

  let cachedLightModule: WebChannelLightRuntimeModule | null = null;


  const jitiLoaders: PluginJitiLoaderCache = new Map();

  ```

  '
symptom_type: memory-leak
problem: runtime-web-channel-plugin.ts 는 web channel (WhatsApp) 플러그인의 런타임 모듈을 동적으로
  로드하기 위해 jitiLoaders Map 과 cachedHeavyModule, cachedLightModule 변수를 전역으로 보유한다. 하지만
  module path 가 변경되거나 새 버전 로드 시 이전 캐시된 모듈을 명시적으로 제거하거나 메모리 해제하는 경로가 없어서 누적될 수 있다.
mechanism: '1. loadWebChannelLightModule() / loadWebChannelHeavyModule() 호출

  2. modulePath 계산 후 cache hit 여부 확인

  3. cache miss 시 loadPluginBoundaryModuleWithJiti(modulePath, jitiLoaders) 호출 → jitiLoaders
  Map에 새 jiti loader 추가

  4. loaded 모듈을 cachedHeavyModule / cachedLightModule 에 할당

  5. 플러그인 버전 업그레이드 또는 경로 변경 시 새 modulePath로 재로드

  6. 이전 캐시 entry 는 삭제되지 않음 → jitiLoaders 와 cached module 모두 누적

  '
root_cause_chain:
- why: jitiLoaders Map이 무제한 성장하는가
  because: getCachedPluginJitiLoader() 함수가 cache.set() 만 수행하고, 플러그인 버전/경로 변경 시 이전
    key 를 delete 하지 않음
  evidence_ref: src/plugins/runtime/runtime-web-channel-plugin.ts:111
- why: cachedHeavyModule / cachedLightModule 은 계속 할당되는가
  because: 경로가 같으면 cache 되지만, 경로가 변하면 새 모듈을 로드해 변수에 재할당. 이전 참조는 GC 가능하지만 jitiLoaders
    에 남은 jiti loader 인스턴스는 메모리 유지
  evidence_ref: src/plugins/runtime/runtime-web-channel-plugin.ts:154-155, 169-170
- why: 왜 cleanup/reset 메소드가 없는가
  because: 전역 상태이면서도 독립적인 cleanup API 가 없음. public-surface-loader.ts 는 resetBundledPluginPublicArtifactLoaderForTest()
    가 있지만 runtime-web-channel-plugin.ts 에는 없음
  evidence_ref: src/plugins/runtime/runtime-web-channel-plugin.ts (파일 전체 내 reset/clear
    메소드 없음)
impact_hypothesis: memory-growth
impact_detail: '정성: WhatsApp 플러그인이 버전 업데이트되거나 경로가 변경되는 환경(development, CI/CD, canary
  배포 등)에서 jitiLoaders Map 이 이전 jiti 인스턴스들을 계속 보유.


  정량: jiti 인스턴스는 require.cache/module cache 를 메모리에 유지하므로, 플러그인 버전 변경 N회 시 N개의 불필요한
  jiti loader 와 모듈 캐시가 프로세스 메모리에 남음. 각 jiti loader 는 별도의 module resolver/cache 를 보유하므로
  누적되면 상당한 메모리 사용.

  '
severity: P2
counter_evidence:
  path: null
  line: null
  reason: 'none_found: jitiLoaders 가 프로세스 shutdown 이전에 어디서든 clear 되는지 확인 못함. 만약 플러그인이
    unload 될 때 자동으로 cleanup 되거나, gateway shutdown 핸들러에서 호출된다면 실제 누수가 아님. 관련 lifecycle
    hook 미확인.'
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
---
# runtime-web-channel-plugin.ts 의 jitiLoaders Map과 module cache 에 cleanup 경로 없음

## 문제

runtime-web-channel-plugin.ts 는 web channel (WhatsApp) 플러그인의 동적 로드를 위해 jitiLoaders Map 과 module cache 변수들을 전역으로 보유한다. 플러그인 버전이 변경되거나 경로가 업데이트될 때 이전 캐시된 모듈/jiti 인스턴스를 명시적으로 제거하는 메커니즘이 없어서 메모리가 누적될 수 있다.

## 발현 메커니즘

1. loadWebChannelLightModule() / loadWebChannelHeavyModule() 호출
2. modulePath 를 resolveWebChannelRuntimeModulePath() 로 계산
3. 캐시 hit 확인 (path 가 같으면 cached module 반환)
4. cache miss 시 loadPluginBoundaryModuleWithJiti(modulePath, jitiLoaders) 호출
5. jiti loader 를 jitiLoaders Map에 저장
6. 플러그인 버전 업데이트/경로 변경 → 새 modulePath 계산 → 새 jiti loader 생성 및 추가
7. 이전 jitiLoaders entry 및 cached module 은 삭제되지 않음
8. 여러 번 반복하면 jitiLoaders Map 내 불필요한 jiti 인스턴스 누적

## 근본 원인 분석

1. 왜 jitiLoaders Map이 계속 성장하는가? → getCachedPluginJitiLoader() 함수가 cache.set() 만 수행하고 이전 entry 를 delete 하지 않음 (runtime-web-channel-plugin.ts:111, jiti-loader-cache.ts:58)
2. 왜 cachedHeavyModule/cachedLightModule 할당이 메모리 누수인가? → 변수 자체는 덮어쓰이지만, jitiLoaders 에 저장된 jiti loader 인스턴스들이 이전 모듈 버전의 require.cache 를 계속 참조하고 있음 (runtime-web-channel-plugin.ts:154-155, 169-170)
3. 왜 cleanup API 가 없는가? → 전역 module cache 로 설계되었으나 lifecycle event 나 teardown hook 과 연결되지 않음 (FIND evidence 참고: public-surface-loader.ts 의 resetBundledPluginPublicArtifactLoaderForTest() 와 비교)

## 영향

플러그인 버전 업데이트가 빈번한 개발/스테이징/canary 배포 환경에서 jitiLoaders Map 이 이전 jiti 인스턴스들을 계속 메모리에 유지한다. 각 jiti loader 는 독립적인 require.cache 및 module resolver 를 보유하므로, N번의 버전 변경 시 N개의 불필요한 jiti 인스턴스가 누적되어 상당한 메모리 누수가 발생할 수 있다.

## 반증 탐색

- **lifecycle cleanup**: 플러그인이 unload/dispose 될 때 runtime-web-channel-plugin 의 cache 를 자동으로 clear 하는 로직이 있는지 확인.
- **shutdown handler**: gateway shutdown 시점에 jitiLoaders 를 clear 하는 핸들러가 있는지 확인.
- **process lifecycle**: process.exit 전에 cleanup 을 호출하는 logic 확인.

탐색 결과: 위 카테고리 모두 반증 발견 안 됨. runtime-web-channel-plugin.ts 파일 내에 reset/cleanup 메소드가 없음.

## Self-check

### 내가 확실한 근거

- src/plugins/runtime/runtime-web-channel-plugin.ts:111 의 jitiLoaders 선언
- src/plugins/runtime/runtime-web-channel-plugin.ts:150-156 의 loadWebChannelLightModule() 함수 (새 로드 시마다 추가, 이전 entry 미삭제)
- src/plugins/runtime/runtime-web-channel-plugin.ts:159-172 의 loadWebChannelHeavyModule() 함수 (동일 패턴)
- jiti-loader-cache.ts:58 의 params.cache.set(scopedCacheKey, loader) (eviction 없음)

### 내가 한 가정

- 플러그인 버전이 자주 변경된다는 가정 (실제 프로덕션 빈도 미확인)
- jitiLoaders 에 저장된 jiti loader 인스턴스들이 require.cache 를 통해 큰 메모리를 점유한다는 가정 (jiti 라이브러리 내부 구조 미확인)

### 확인 안 한 것 중 영향 가능성

- 플러그인 버전 변경 빈도 및 실제 메모리 증가량
- jiti loader 인스턴스당 메모리 footprint (모듈 캐시 크기)
- 전체 plugin unload lifecycle 과 runtime-web-channel-plugin 의 연결 여부
- gateway shutdown 순서와 cleanup 호출 시점
