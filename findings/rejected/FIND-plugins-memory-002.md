---
id: FIND-plugins-memory-002
cell: plugins-memory
title: openAllowlistWarningCache Set에 무제한 추가되고 eviction 정책 없음
file: src/plugins/loader.ts
line_range: 200-220
evidence: "```ts\nconst openAllowlistWarningCache = new Set<string>();\n\nexport function\
  \ clearPluginLoaderCache(): void {\n  registryCache.clear();\n  inFlightPluginRegistryLoads.clear();\n\
  \  openAllowlistWarningCache.clear();\n  clearAgentHarnesses();\n  clearCompactionProviders();\n\
  \  clearMemoryEmbeddingProviders();\n  clearMemoryPluginState();\n}\n```\n"
symptom_type: memory-leak
problem: openAllowlistWarningCache 는 플러그인 로드 시 경고 출력 여부를 추적하기 위한 Set으로, warnWhenAllowlistIsOpen()
  함수에서 키를 계속 add() 만 수행하고 eviction 정책 없이 무제한 성장할 수 있다. clearPluginLoaderCache() 는
  process teardown/테스트 cleanup 시에만 호출되므로 runtime 중에는 계속 누적된다.
mechanism: '1. warnWhenAllowlistIsOpen(params) 함수 호출 시 warningCacheKey 생성 (line 1070)

  2. openAllowlistWarningCache.has(warningCacheKey) 로 이미 본 키인지 확인 (line 1083)

  3. 처음 본 경우에만 경고 log + openAllowlistWarningCache.add(warningCacheKey) 호출 (line 1091)

  4. 서로 다른 워크스페이스/플러그인 조합이 로드될 때마다 새 키 추가

  5. delete/eviction 메커니즘 없음 → 프로세스 생애 동안 계속 성장

  '
root_cause_chain:
- why: openAllowlistWarningCache가 무제한 성장할 수 있는가
  because: Set의 add() 메소드만 사용되고, delete/clear/cap 메커니즘이 없음
  evidence_ref: src/plugins/loader.ts:1091
- why: 왜 runtime 중 cleanup이 되지 않는가
  because: clearPluginLoaderCache() 는 export 되지 않아 매 로드 사이클마다 호출되지 않음. 대신 process
    shutdown/테스트 cleanup 에서만 호출됨
  evidence_ref: src/plugins/loader.ts:217-225
- why: 왜 TTL/eviction 정책을 도입하지 않았는가
  because: warningCacheKey 는 텍스트 구성 (workspace/pluginIds 조합) 이므로 LRU/TTL 없이는 중복 제거
    불가능. registryCache 와 달리 cap 상수도 없음
  evidence_ref: src/plugins/loader.ts:200
impact_hypothesis: memory-growth
impact_detail: '정성: 워크스페이스/플러그인 조합이 다양한 gateway 프로세스에서 플러그인 로드 주기가 반복될 때마다 (예: 개발
  중 스크립트 재실행, 동적 플러그인 추가/제거) openAllowlistWarningCache Set 크기 증가.


  정량: 하루 중 로드 사이클이 N회 있고 각 사이클마다 새로운 워크스페이스/플러그인 조합 추가 시, registryCache 는 최대 128개
  entry로 capped 되지만 openAllowlistWarningCache는 제한 없음.

  '
severity: P2
counter_evidence:
  path: null
  line: null
  reason: 'none_found: clearPluginLoaderCache() 호출 경로 전체 확인 못함. 만약 request/cycle 단위로
    clearPluginLoaderCache() 호출 로직이 있다면 실제 문제 아님. 기존 테스트에서 Set 크기 검증 없음.'
status: rejected
discovered_by: memory-leak-hunter
discovered_at: '2026-04-18'
rejected_reasons:
- 'B-1-2c: evidence mismatch at src/plugins/loader.ts:200-220 — whitespace or content
  differs'
---
# openAllowlistWarningCache Set에 무제한 추가되고 eviction 정책 없음

## 문제

openAllowlistWarningCache 는 플러그인 로드 시 plugins.allow 가 빈 경우 경고를 한 번만 출력하기 위해 사용되는 Set 자료구조다. 하지만 warnWhenAllowlistIsOpen() 에서 set.add() 만 호출하고 delete/eviction 메커니즘이 없어서 프로세스 생애 동안 무제한 성장할 수 있다.

## 발현 메커니즘

1. 플러그인 로드 시 warnWhenAllowlistIsOpen() 함수 호출
2. warningCacheKey 생성 (workspace/pluginIds 조합 기반)
3. openAllowlistWarningCache.has(key) 로 이미 본 키인지 확인
4. 처음 본 경우 경고 log + set.add(key) 호출
5. 새로운 워크스페이스/플러그인 조합 로드 시 새 키 추가
6. delete 경로 부재 → 계속 누적

## 근본 원인 분석

1. 왜 openAllowlistWarningCache가 무제한 성장할 수 있는가? → Set.add() 만 있고 eviction 정책 없음 (loader.ts:1091)
2. 왜 clearPluginLoaderCache() 가 runtime 중 호출되지 않는가? → export 되지만 매 로드마다 호출되지 않음 (loader.ts:217-225)
3. 왜 TTL/cap을 도입하지 않았는가? → registryCache 와 달리 openAllowlistWarningCache 는 MAX 상수도, cycle 기반 cleanup도 없음 (loader.ts:200)

## 영향

개발 환경이나 동적 플러그인 로드 시나리오에서 장시간 실행되면 openAllowlistWarningCache 가 계속 커진다. 경고 캐시 크기가 수만 개에 달할 수 있으며, 각 entry 는 warningCacheKey (문자열) 를 보유하므로 메모리가 누적된다.

## 반증 탐색

- **숨은 cleanup 경로**: clearPluginLoaderCache() 가 매 로드 사이클마다 호출되는지 확인 필요. 만약 호출된다면 실제 누수가 아님.
- **테스트 커버리지**: Set 크기 증가를 검증하는 기존 테스트가 있는지 확인 필요.
- **워크스페이스 스코프**: 일반적인 배포/프로덕션 환경에서 실제로 워크스페이스/플러그인 조합이 얼마나 다양한지 확인.

탐색 결과: 두 카테고리 모두 반증 발견 안 됨.

## Self-check

### 내가 확실한 근거

- src/plugins/loader.ts:200 의 선언부
- src/plugins/loader.ts:1091 의 add() 호출
- src/plugins/loader.ts:217-225 의 clearPluginLoaderCache() 함수 (add() 경로와 다른 모듈 export 구조)

### 내가 한 가정

- warnWhenAllowlistIsOpen() 이 자주 호출된다는 가정 (실제 빈도 미확인)
- clearPluginLoaderCache() 가 프로세스 shutdown/테스트 cleanup 외에는 호출되지 않는다는 가정 (전체 grep 필요)

### 확인 안 한 것 중 영향 가능성

- warnWhenAllowlistIsOpen() 의 호출 빈도 및 실제 워크스페이스/플러그인 조합 다양성
- clearPluginLoaderCache() 호출 경로 전체 (scheduler, lifecycle hooks, gateway shutdown)
- 개발/스테이징 환경에서의 실제 메모리 증가량 측정
