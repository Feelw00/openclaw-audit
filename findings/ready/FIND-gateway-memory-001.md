---
id: FIND-gateway-memory-001
cell: gateway-memory
title: '`costUsageCache` 에 cap/prune 부재로 distinct (startMs, endMs) 마다 엔트리 영속 누적'
file: src/gateway/server-methods/usage.ts
line_range: 302-352
evidence: "```ts\nasync function loadCostUsageSummaryCached(params: {\n  startMs:\
  \ number;\n  endMs: number;\n  config: OpenClawConfig;\n}): Promise<CostUsageSummary>\
  \ {\n  const cacheKey = `${params.startMs}-${params.endMs}`;\n  const now = Date.now();\n\
  \  const cached = costUsageCache.get(cacheKey);\n  if (cached?.summary && cached.updatedAt\
  \ && now - cached.updatedAt < COST_USAGE_CACHE_TTL_MS) {\n    return cached.summary;\n\
  \  }\n\n  if (cached?.inFlight) {\n    if (cached.summary) {\n      return cached.summary;\n\
  \    }\n    return await cached.inFlight;\n  }\n\n  const entry: CostUsageCacheEntry\
  \ = cached ?? {};\n  const inFlight = loadCostUsageSummary({\n    startMs: params.startMs,\n\
  \    endMs: params.endMs,\n    config: params.config,\n  })\n    .then((summary)\
  \ => {\n      costUsageCache.set(cacheKey, { summary, updatedAt: Date.now() });\n\
  \      return summary;\n    })\n    .catch((err) => {\n      if (entry.summary)\
  \ {\n        return entry.summary;\n      }\n      throw err;\n    })\n    .finally(()\
  \ => {\n      const current = costUsageCache.get(cacheKey);\n      if (current?.inFlight\
  \ === inFlight) {\n        current.inFlight = undefined;\n        costUsageCache.set(cacheKey,\
  \ current);\n      }\n    });\n\n  entry.inFlight = inFlight;\n  costUsageCache.set(cacheKey,\
  \ entry);\n\n  if (entry.summary) {\n    return entry.summary;\n  }\n  return await\
  \ inFlight;\n}\n```\n"
symptom_type: memory-leak
problem: '`costUsageCache` 는 키마다 `CostUsageSummary` 결과를 무기한 유지한다. TTL(30초) 은 read-path
  에서 stale 값을 무시할 때만 쓰이고, 엔트리 자체를 evict 하는 경로는 프로덕션에 없다. distinct (startMs, endMs)
  조합마다 엔트리 1개씩 축적되어 게이트웨이 장기 가동 시 메모리가 꾸준히 증가한다.'
mechanism: "1. Control UI / CLI 가 `usage.cost` 또는 `sessions.usage` RPC 를 호출. `parseDateRange`\
  \ 가\n   `(startMs, endMs)` 를 계산 — 기본값은 `todayStartMs` 기반으로 **매일** 새 값.\n2. `loadCostUsageSummaryCached`\
  \ 가 `cacheKey = ${startMs}-${endMs}` 로 `costUsageCache.set(...)`\n   수행. 첫 호출 시\
  \ 새 엔트리 생성.\n3. TTL(30초) 경과 후에도 동일 키로 다시 요청하면 L310 조건이 거짓이 되어 새 inFlight 요청을\n \
  \  시작하고 L328 에서 같은 키 엔트리를 **갱신** (overwrite) — 메모리 추가 없음.\n4. 그러나 이전 날짜/다른 윈도우에\
  \ 해당하는 키는 더 이상 요청되지 않음. 해당 엔트리는\n   `costUsageCache` 안에 계속 살아남아 결코 제거되지 않는다 (delete/prune/evict\
  \ 호출 없음).\n5. 오퍼레이터가 매일 dashboard 를 여는 것만으로 `todayStartMs` 가 하루 단위로 증가 →\n   매일\
  \ 새 cacheKey. 30일 운영 시 30개 stale 엔트리. 각 엔트리는 `CostUsageSummary` (세션별\n   usage 집계\
  \ 결과) 를 담고 있어 엔트리 1개가 수 KB ~ 수백 KB 크기.\n"
root_cause_chain:
- why: 왜 TTL 이 있으면서도 엔트리를 지우지 않는가?
  because: TTL 검사(L310)는 read-path 의 "stale 무시" 용도로만 설계되었다. write-path 에서 오래된 키를 정리하는
    로직이 추가되지 않았다. 다른 gateway 캐시들(`resolvedSessionKeyByRunId`, `TRANSCRIPT_SESSION_KEY_CACHE`,
    `sessionTitleFieldsCache`)은 모두 set 시점에 MAX 초과 시 `keys().next().value` 기반 FIFO
    를 수행하는데, 이 파일만 누락.
  evidence_ref: src/gateway/server-methods/usage.ts:302-352
- why: 왜 cacheKey 가 (startMs, endMs) 기반이라서 누적되는가?
  because: '`parseDateRange` (L222-252) 가 `todayStartMs` 에서 days 를 빼서 startMs 를 계산한다.
    `todayStartMs = getTodayStartMs(now, interpretation)` (L231) 은 현재 시각 기반 — 매일 새
    값. UTC offset 조합까지 고려하면 하루에도 여러 cacheKey 가 생성될 수 있다.'
  evidence_ref: src/gateway/server-methods/usage.ts:222-252
- why: 왜 테스트에서 드러나지 않았는가?
  because: '`__test.costUsageCache.clear()` (L365) 는 테스트 전용 reset. 프로덕션에서는 호출되지 않음.
    테스트 (usage.test.ts) 는 매 케이스마다 clear 로 초기화하므로 누적을 관측할 수 없다.'
  evidence_ref: src/gateway/server-methods/usage.test.ts:27
impact_hypothesis: memory-growth
impact_detail: "정량 상한 (프로덕션 관측치 없음, 모델 기반):\n- distinct (startMs, endMs) 엔트리 수 ≈ (운영\
  \ 일수) × (UI 에서 노출되는 date range option 수,\n  예: 1일/7일/30일) × (UTC offset 조합 수).\n\
  - 단일 operator 1년 운영 시 대략 1,000~3,000 엔트리. 엔트리 1개당 `CostUsageSummary` 는 세션\n  개수에\
  \ 비례하는 집계 결과 — 프로덕션 세션 규모에서 10~100KB 수준으로 추정.\n- 합계 ≈ 수십 MB. OOM 까지 진행하기보다 장기 heap\
  \ 증가 (slow leak) 로 드러남.\n- GC-hostile: 엔트리 자체가 Map 에 잡혀 있어 major GC 에서도 회수 안 됨.\n"
severity: P3
counter_evidence:
  path: src/gateway/server-methods/usage.ts
  line: 302-366
  reason: "R-3 Grep (audit HEAD 8879ed153d):\n```\nrg -n \"costUsageCache\\.(delete|clear|evict|splice|shift|pop)\"\
    \ src/gateway/\n  → server-methods/usage.test.ts:27 (테스트 clear)\n  → server-methods/usage.ts:365\
    \    (__test export, 테스트 전용 clear)\n  # 프로덕션 delete/evict 경로 match 없음.\n\nrg -n\
    \ \"costUsageCache\\.(set|get)\" src/gateway/\n  → usage.ts:309 get   (read-path)\n\
    \  → usage.ts:328 set   (on inFlight resolve — 동일 키 update)\n  → usage.ts:338-341\
    \ set (on finally — inFlight reset)\n  → usage.ts:346 set   (initial set on miss)\n\
    \nrg -n \"(cap|max|limit|size).*costUsageCache\" src/gateway/\n  → match 없음.\n\
    \nrg -n \"while.*costUsageCache\\.size\" src/gateway/\n  → match 없음.\n```\n\n\
    R-5 execution condition 분류:\n| 경로 | 조건 | 비고 |\n|---|---|---|\n| L365 `__test.costUsageCache.clear()`\
    \ | test-only | 프로덕션 미발동 |\n| L310 TTL check | read-path only | 엔트리 유지, 읽기 값만\
    \ 무시 |\n| L328, L346 `set` | 동일 key overwrite | 새 키 누적 방지 못함 |\n| cap/FIFO evict\
    \ 경로 | 없음 | 같은 파일 내 다른 cache (resolvedSessionKeyByRunId:22-30) 와 대조 |\n\nPrimary-path\
    \ inversion: \"누적 안 된다\" 가 참이려면 모든 요청이 동일 (startMs, endMs) 조합을\n쓰거나 프로세스가 짧게 재시작되어야\
    \ 한다. 그러나 `todayStartMs` 는 매일 바뀌고 operator 가\n`days` 파라미터를 바꾸기만 해도 새 키. UTC offset\
    \ 파라미터까지 가변.\n\n이미 누수 입증하는 테스트/주석 없음: `__test` export 는 테스트 편의일 뿐 누수 경고가 아님.\n\
    \"intentional unbounded cache\" 라는 주석 부재.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-19'
---
# `costUsageCache` 에 cap/prune 부재로 distinct (startMs, endMs) 마다 엔트리 영속 누적

## 문제

`costUsageCache` 는 `(startMs, endMs)` 쌍을 키로 `CostUsageSummary` 결과를 보관하는 모듈-레벨 Map 이다. TTL(`COST_USAGE_CACHE_TTL_MS = 30_000`) 은 read-path 에서 stale 값을 무시할 때만 사용되고, 엔트리를 Map 에서 제거하는 경로는 프로덕션에 존재하지 않는다. operator 가 매일 usage dashboard 를 열면 `todayStartMs` 가 바뀌어 새 cacheKey 가 누적되고, 이전 키는 무한히 남는다.

## 발현 메커니즘

1. operator / CLI 가 `usage.cost` 또는 `sessions.usage` RPC 호출.
2. `parseDateRange` 는 `getTodayStartMs(now, interpretation)` 을 기준으로 `(startMs, endMs)` 계산. 기본은 last-30-days, optional 인자 `days` / `startDate` / `endDate` / `utcOffset` 조합.
3. `loadCostUsageSummaryCached(params)` → `cacheKey = ${params.startMs}-${params.endMs}` 로 `costUsageCache.get`.
4. miss 시 새 promise 를 만들어 `costUsageCache.set(cacheKey, { inFlight })` (L346), resolve 후 `costUsageCache.set(cacheKey, { summary, updatedAt })` (L328). 성공해도 엔트리는 계속 Map 에 남음.
5. 다음 날 `todayStartMs` 가 하루 이동 → 이전 키는 더 이상 갱신/삭제되지 않음. Map 이 monotonically 커진다.

## 근본 원인 분석

`loadCostUsageSummary` 의 결과는 세션별 토큰/비용 집계를 포함하는 구조적으로 큰 객체다 (`CostUsageSummary` — src/agents/usage.ts). 이를 키별로 저장하는 캐시는 메모리 민감도가 높다. 같은 파일 내 다른 캐시들 (예: `sessionTitleFieldsCache` L63-69 의 while-loop FIFO evict; `resolvedSessionKeyByRunId` L22-30 의 oldest-first evict) 과 대조했을 때 usage cache 만 cap/evict 가 부재하다. 설계에서 TTL=30초 read-invalidation 만으로 "짧게 유지되는 캐시" 라고 오인한 것으로 보이지만, 실제로는 **키 공간이 시간에 따라 팽창** 하므로 stale 값이 읽히지 않을 뿐 Map 에는 남는다.

## 영향

- 영향 유형: **memory-growth** (slow leak).
- 관측: 프로세스 heap 이 시간에 따라 증가. 즉각적 OOM 아님.
- 재현: `usage.cost` 를 매일 또는 매주 호출하며 `days` 값을 변형 → 30일 후 Map.size ≥ 30.
- severity P3: 누적 속도 느리고 엔트리 크기는 세션 규모 의존. 몇 달 ~ 1년 이상의 장기 구동 서버에서 문제.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 으로 `costUsageCache.(delete|clear|evict|splice|shift|pop)` 탐색. 프로덕션 delete 경로 없음. `__test.costUsageCache.clear()` 는 테스트 전용. same-key overwrite 는 엔트리 수를 줄이지 못함.

**카테고리 2 (외부 경계 장치)**: `server-maintenance.ts` 의 interval 들 — dedupe cleanup, tick, health — 은 이 캐시를 건드리지 않는다. 서버 종료 시 Map 은 해제되나 프로세스 재시작 전까지는 쌓임. graceful shutdown 에서 clear 경로 없음.

**카테고리 3 (호출 맥락)**: `usage.cost` / `sessions.usage` 는 operator UI 의 usage dashboard 에서 호출. 일반 operator 는 최소 주 단위 접근. 자동 polling client 가 있으면 누적 속도 증가.

**카테고리 4 (기존 테스트)**: usage.test.ts 27번 라인에서 `__test.costUsageCache.clear()` 로 reset. 누적 시나리오 테스트 없음.

**카테고리 5 (주석/의도)**: 파일 내 "unbounded" 또는 "intentional" 주석 없음. 실수로 보임.

**Primary-path inversion**: "엔트리가 쌓이지 않는다" 가 참이려면 모든 요청이 동일 cacheKey 를 쓰거나 프로세스가 짧게 재시작되어야 한다. `todayStartMs` 매일 변화 + `days`/`utcOffset` 가변 → 성립 안 함.

## Self-check

### 내가 확실한 근거
- `src/gateway/server-methods/usage.ts:65, 302-352` 을 Read 로 확인. delete/prune 경로 부재.
- R-3 Grep 으로 프로덕션 삭제 경로 match 0 확인.
- 동일 파일 내 다른 캐시 (resolvedSessionKeyByRunId L22-30, sessionTitleFieldsCache L63-69) 의 cap/FIFO 구현과 비교 — 이 파일만 누락.

### 내가 한 가정
- `CostUsageSummary` 크기가 세션당 수 KB 수준이라는 추정 (프로덕션 관측치 없음).
- 프로세스 uptime 이 수 주 ~ 수 개월이라는 가정. launchd / systemd 환경에서는 타당.

### 확인 안 한 것 중 영향 가능성
- `loadCostUsageSummary` 의 결과 객체 실제 크기 프로파일링 안 함. 세션 수가 적으면 P4 급으로 내려갈 가능성.
- config reload 경로에서 이 Map 이 리셋되는지 — `config-reload.ts` 직접 trace 안 함. 만약 reload 시 clear 된다면 severity 추가 감소.
- operator 가 실제로 얼마나 자주 usage.cost 를 호출하는지 metrics 부재.
