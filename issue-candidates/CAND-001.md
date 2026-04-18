---
candidate_id: CAND-001
type: single
finding_ids:
  - FIND-plugins-memory-003
cluster_rationale: "원래 epic 으로 FIND-001 과 함께 묶였으나 gatekeeper counter_evidence 가 FIND-001 을 반증 — loader.ts:346-352 의 setCachedPluginRegistry 에 FIFO cap eviction 존재함. FIND-001 retract 후 FIND-003 (runtime-web-channel-plugin.ts 의 jitiLoaders Map unbounded) 만 유지. Gatekeeper 는 FIND-003 의 같은 파일 내 reset 경로 반증을 찾지 못했으나 production cleanup lifecycle 호출 여부는 미확인."
proposed_title: "runtime-web-channel-plugin jitiLoaders Map 에 eviction/cleanup 정책 부재"
proposed_severity: P3
existing_issue: null
created_at: 2026-04-18
revised_at: 2026-04-18
revision_note: "gatekeeper counter_evidence 후 FIND-001 제외, single 로 축소. 재-gatekeep 필요."
---

# plugins 도메인 글로벌 캐시에 eviction 정책 부재로 인한 메모리 누적

## 공통 패턴

plugins 도메인의 플러그인 로딩 및 런타임 모듈 캐싱 로직에서 다음과 같은 공통 메커니즘 발견:

1. **전역 Map 선언**: registryCache, jitiLoaders 등 모듈 스코프의 Map 자료구조
2. **캐시 갱신**: 새 플러그인 버전/경로 → cache.set(key, value)
3. **누락된 eviction**: cap 비교, TTL, 또는 명시적 delete 로직 부재
4. **결과**: 장시간 실행 및 버전 변경 시 메모리 점진적 성장

### 근거

**FIND-plugins-memory-001** (registryCache):
- `src/plugins/loader.ts:198` — Map 선언 및 set() 호출만 확인
- `root_cause_chain[0]`: "Map 기본 동작은 cap 없이 set 마다 추가, 명시적 eviction 정책 부재"
- MAX_PLUGIN_REGISTRY_CACHE_ENTRIES=128 상수 선언되었으나 cap 비교 로직 미발견

**FIND-plugins-memory-003** (jitiLoaders):
- `src/plugins/runtime/runtime-web-channel-plugin.ts:111` — Map 선언
- `root_cause_chain[0]`: "getCachedPluginJitiLoader() 함수가 cache.set() 만 수행하고, 플러그인 버전/경로 변경 시 이전 key 를 delete 하지 않음"
- 플러그인 버전 업데이트 시 이전 jiti 인스턴스들이 메모리에 계속 유지

## 관련 FIND

- **FIND-plugins-memory-001**: registryCache Map 에 eviction/TTL 정책 없이 계속 set 만 호출됨 (loader.ts:196-199)
- **FIND-plugins-memory-003**: runtime-web-channel-plugin.ts 의 jitiLoaders Map과 module cache 에 cleanup 경로 없음 (runtime-web-channel-plugin.ts:111)
