---
candidate_id: CAND-001
type: single
finding_ids:
  - FIND-plugins-memory-001
cluster_rationale: "단일 FIND. plugin registryCache 의 cap-but-no-eviction 패턴."
proposed_title: "plugin registryCache: cap 상수만 있고 eviction 정책 부재"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-18
---

# plugin registryCache: cap 상수만 있고 eviction 정책 부재

## 공통 패턴
`src/plugins/loader.ts:196` 에 `MAX_PLUGIN_REGISTRY_CACHE_ENTRIES=128` 상수만 있고, 이 값에 기반한 eviction/삭제 로직이 없음.

## 관련 FIND
- FIND-plugins-memory-001: registryCache Map 에 eviction/TTL 정책 없이 계속 set 만 호출됨
