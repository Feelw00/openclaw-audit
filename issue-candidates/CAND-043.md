---
candidate_id: CAND-043
type: single
finding_ids:
  - FIND-context-engine-concurrency-001
cluster_rationale: |
  단일 CAND. 본 FIND 는 `resolveContextEngine` (src/context-engine/registry.ts:546-601)
  의 entry snapshot 이 L546 sync 캡처 → L563 `await entry.factory(factoryCtx)`
  → L601 `wrapResolvedContextEngine(engine, { owner: entry.owner })` 동안
  외부의 `clearContextEnginesForOwner(entry.owner)` 호출로 인해 stale 해질 수
  있다는 단일 file 단일 메커니즘 결함이다. CAND-041 (typing keepalive) / CAND-042
  (message ack race) 와는 file / 메커니즘 / fix surface 모두 직교 — Step 1~3
  어느 묶음 기준도 충족 안 함 → Step 4 single CAND.

  본 CAND 는 같은 도메인의 CAND-035 (init.ts:13-23 set-before-action ordering)
  / CAND-036 (registry.ts:239-267 invokeWithLegacyCompat regex retry) / CAND-037
  (registry.ts:561-599 contract validation fallback dispose 부재) 와 cross_refs
  로 연결 — 모두 context-engine 도메인이지만 다른 race window / 다른 fix surface.

  root_cause_chain[0] 의 because: "entry = engines.get(engineId) at L546 is
  reused at L601 without revalidation (no engines.get(engineId)?.owner ??
  entry.owner pattern, no version stamp, no entry.invalidated flag)" — fix 가
  같은 함수 본문 내 entry revalidation 1 라인 또는 version stamp 도입으로
  self-contained. 다른 셀로 일반화 불가.
proposed_title: "fix(context-engine): resolveContextEngine entry snapshot leaks stale owner across clear race"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: abandoned
cross_review_metric: metrics/cross-review-CAND-043-20260514-082000.jsonl
retracted_reason: 'cross-review CAL-001: avg 0.60 abandon. critical-devil abandon/high — plugins/loader.ts:491-494 runPluginRegisterSync 가 atomic sync frame (전체 register 가 한 micro-task 내 완료) + plugins/loader.ts:1489 loadOpenClawPlugins 가 sync 함수. clearContextEnginesForOwner 자체는 외부에서 호출 가능하나 resolveContextEngine 의 await entry.factory 와 인터리브 가능한 caller 가 production 에서 부재 — entry.owner=''plugin:X'' 는 provenance metadata 이지 registry membership 의 atomic 키가 아님. R-3 grep 5종이 context-engine/ scope 만 검사 + plugins/loader.ts atomicity model 누락. CAL-001 R-3 scope 누락 재발.'
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    context_engine_registry_ts:
      - 694ca50e97  # Revert "refactor: move runtime state to SQLite"
      - f91de52f0d  # refactor: move runtime state to SQLite
      - 9e1e59717f  # feat(plugin-sdk): add LLM completion API to plugin (#64294)
      - d8a600f2ad  # context-engine: pass runtime context to ContextEngineFactory (#67243)
      - 263a190fc9  # context-engine: accept third-party engines whose info.id differs (#66678)
      - 59d07f0ab4  # fix(plugins): roll back failed register globals (clearContextEnginesForOwner 도입)
      - 6aa4515798  # fix(context-engine): gracefully degrade on third-party plugin failure (#66930)
      - 2677f7cf14  # fix: validate resolved context engine contracts (#63222)
      - 95bc417944  # fix(cycles): split residual shared type seams
    context_engine_dir:
      - ccb6e581d3  # test: check context and music options
      - 15cf49222f  # build: refresh deps + testbox crabbox
      - f4addf8713  # test: tighten context engine assertions
      - d0f484d024  # test: clarify runtime event assertions
      - 9ef37d1907  # test: tighten assertions and harness coverage
      - 5bdc901601  # refactor: trim context engine prompt cache types
      - 42584964ac  # fix(context-engine): honor assembled prompt authority in precheck (#74255)
      - 3c95327b34  # Fix compacted session transcript rotation
      - cd392b947c  # test: dedupe memory and context suites
      - 4f4d2ef1df  # chore: remove dead compat barrels
      - f5042adf27  # feat: add forked subagent context
  finding: |
    `src/context-engine/registry.ts` 6 주 9 commits 중 본 race window
    (L546→L601 entry lifetime) 관련 변경 0 건:
    - 59d07f0ab4 (2026-04-17) 가 `clearContextEnginesForOwner` 추가 + plugins
      rollback path 연결 — 본 race window 가 처음 생긴 시점이지만 resolve 측
      snapshot freshness 미검토.
    - 2677f7cf14 (validate resolved contracts), 6aa4515798 (graceful degrade
      on factory failure), 263a190fc9 (third-party engine id mismatch),
      d8a600f2ad (runtime context to factory), 9e1e59717f (plugin LLM API),
      42584964ac (precheck prompt authority), 3c95327b34 (compaction session
      rotation), 5bdc901601 (prompt cache types refactor): 본 race 축
      (entry snapshot revalidation / wrap metadata update) 변경 0 건.
    - f91de52f0d / 694ca50e97: SQLite runtime state 도입 후 즉시 revert →
      net no-op.
  pr_search:
    - 'gh pr list --repo openclaw/openclaw --state open --search "resolveContextEngine OR context-engine race OR clearContextEnginesForOwner in:title,body"'
    - 'gh pr list --repo openclaw/openclaw --state open --search "wrapResolvedContextEngine OR context-engine owner in:title,body"'
    - 'gh pr list --repo openclaw/openclaw --state open --search "context engine resolve in:title,body"'
  related_open_pr: null
  related_open_pr_notes: |
    OPEN PR 매칭 중 본 race window (entry snapshot owner staleness) 와 직접
    겹치는 것 없음:
    - PR #78309 (start selected context engine slot) — plugin install 측,
      registry.ts 의 resolve path 와 file 영역 겹침 0.
    - PR #81242 (skip context engine preparation for isolated subagent) —
      caller-side 우회 PR, registry.ts:546-601 미수정.
    - PR #81079 (currentTokenCount in assemble) — ContextEngine 계약 확장,
      resolution race 축 무관.
    - PR #81164 (interceptCompaction contract) — context-engine plugin 계약
      확장, resolution race 축 무관.
    - PR #69270 (compaction restore invariants) — hook-message-provider 측,
      registry.ts 미수정.
    - PR #73704 (resolve compaction provider/model before register) —
      safeguard 모듈, context-engine registry.ts 외.
  duplicate_decision: not-duplicate
cross_refs:
  - CAND-035  # context-engine init.ts:13-23 set-before-action (다른 race window, 같은 도메인)
  - CAND-036  # context-engine registry.ts:239-267 invokeWithLegacyCompat regex retry (다른 axis)
  - CAND-037  # context-engine registry.ts:561-599 contract validation dispose 부재 (인접 라인, 다른 axis)
---

# fix(context-engine): resolveContextEngine entry snapshot leaks stale owner across clear race

## 공통 패턴

본 CAND 는 `resolveContextEngine` (src/context-engine/registry.ts:527-602) 의
L546 sync entry snapshot 이 L563 `await entry.factory(factoryCtx)` 의
microtask suspension 동안 외부 `clearContextEnginesForOwner(entry.owner)`
호출로 인해 stale 해질 수 있고, await 종료 후 L601 `wrapResolvedContextEngine(
engine, { owner: entry.owner })` 가 stale closure 의 `entry.owner` 를 그대로
사용해 wrapped engine 의 metadata 가 **registry 에 더 이상 존재하지 않는
plugin id 를 영구히 가리킨다** 는 단일 메커니즘 결함이다.

```text
T0 caller A: entry = engines.get("X") (sync, L546). owner = "plugin:X".
T1 await entry.factory(factoryCtx) (L563) → microtask yield.
T2 동시 fiber: plugins/loader.ts:2406 catch
   → plugins/registry.ts:2932 rollbackPluginGlobalSideEffects("X")
   → clearContextEnginesForOwner("plugin:X")
   → registry.ts:427-435 sync engines.delete("X").
T3 factory resolve → caller A 재개 → engine valid.
T4 L601 wrapResolvedContextEngine(engine, { owner: entry.owner })
   ← entry.owner = "plugin:X" (stale closure).
   RESOLVED_CONTEXT_ENGINE_METADATA.set(wrapped, { owner: "plugin:X" }).
T5 resolveContextEngineOwnerPluginId(wrapped) → "X".
   그러나 engines map 에 "X" 없음. 영구 stale attribution.
```

`wrapResolvedContextEngine` 의 metadata 는 WeakMap.set 으로 1회 기록 후 update
경로 부재 (registry.ts:321-328) → caller pin 되어 있는 한 stale owner 영구
보유. lifecycle attribution 소비처 (pi-embedded-runner/run.ts:1070,
context-engine-maintenance.ts:355, compact.queued.ts:99, run/attempt.ts:934)
가 `resolveContextEngineOwnerPluginId` 로 metric / log / maintenance attribution
수행 → 운영자 시각에 "plugin X 가 동작 중" 으로 오해 + maintenance 가 unregister
된 plugin id 로 호출.

## 관련 FIND

- **FIND-context-engine-concurrency-001** (P3, src/context-engine/registry.ts:546-601):
  L546→L601 entry lifetime 의 unguarded race window. context-engine 도메인
  전체에 lock primitive / AbortController / Promise.race / listener-sync /
  microtask scheduling 0건 (R-3 grep). R-5 실행조건 표에서 본 race window 만
  unguarded (다른 mutation/wrap 경로는 모두 sync atomic).
  `clearContextEnginesForOwner` 가 2026-04-17 `59d07f0ab4` 로 도입되며 본 race
  window 가 처음 생긴 시점.

## 영향

`impact_hypothesis: wrong-output` (hygienic / attribution corruption) —
wrapped engine 자체는 functional (compact/ingest/assemble 정상). 그러나
lifecycle attribution 이 손상:

- `pi-embedded-runner/run.ts:1070` — embedded session 시작 시 owner 기록.
- `context-engine-maintenance.ts:355` — maintenance sweep 시 owner 별
  context-engine 정리 호출.
- `compact.queued.ts:99` — compaction queue attribution.
- `run/attempt.ts:934` — agent run 별 owner 기록.

운영 측 영향: (a) 운영자 대시보드/로그에 unregistered plugin id 가 active 로
표시. (b) maintenance code 가 unregister 된 pluginId 로 lookup 호출 → downstream
warn 노이즈 (lookup 실패는 graceful degrade 되지만 hygiene 불량).

severity P3 (hygienic) — functional bug 없음, attribution 정확성 결함.

## fix surface (gatekeeper / publisher 입력)

같은 파일 (`src/context-engine/registry.ts`) 내 1 hunk:

옵션 A (revalidate at use site):
```ts
// L601 직전
const freshEntry = getContextEngineRegistryState().engines.get(engineId);
const owner = freshEntry?.owner ?? entry.owner;  // stale fallback 도 명시적
return wrapResolvedContextEngine(engine, { owner });
```

옵션 B (entry invalidated flag):
```ts
// clearContextEnginesForOwner 의 delete 시 entry.invalidated=true set
// L601 직전: if (entry.invalidated) throw or fallback
```

옵션 A 가 minimal change. 회귀 테스트: context-engine.test.ts 에 "factory
await 중 clearContextEnginesForOwner 호출 → wrapped engine 의 owner metadata
가 stale 한 pluginId 가 아님" 시나리오 추가.

## R-7 hot-path 확인

- `resolveContextEngine` production caller:
  - src/agents/subagent-registry.ts:325 (subagent spawn)
  - src/agents/pi-embedded-runner/run.ts:1066 (embedded session 시작)
  - src/agents/pi-embedded-runner/compact.queued.ts:63 (compaction queue)
- `clearContextEnginesForOwner` production caller:
  - src/plugins/registry.ts:2932 (rollbackPluginGlobalSideEffects)
  - plugins/loader.ts:2406 catch 에서만 호출.
- 일반 부팅 흐름은 plugin init 완료 후 session 시작 → 동시 발생 빈도 낮음.
  plugin lazy-install / on-demand install / hot-reload 활성 시 빈도 증가.

## upstream-dup 검사 결과

- `git log upstream/main --since="6 weeks ago" -- src/context-engine/registry.ts`
  → 9 commits. 본 race 축 변경 0 건 (위 finding 블록 참조).
- `git log upstream/main --since="6 weeks ago" -- src/context-engine/` → 20+
  commits, 모두 test / refactor / 다른 fix 축. resolveContextEngine entry
  snapshot 축 0.
- `gh pr list --search "resolveContextEngine OR context-engine race OR
  clearContextEnginesForOwner"` → 매치 0.
- `gh pr list --search "wrapResolvedContextEngine OR context-engine owner"` →
  매치 0.
- `gh pr list --search "context engine resolve"` → 6 PR 검출되나 본 race
  window 와 file 영역 / fix 축 겹침 0 (위 related_open_pr_notes 상세).
- 결론: **not-duplicate**. 본 single CAND 발행 진행.

## next steps (gatekeeper / publisher 입력)

- one-thing-per-PR 검토: "resolveContextEngine entry snapshot revalidation"
  한 축 → XS 단일 PR 가능 (1 hunk / 1 file + 회귀 테스트 1 hunk).
- 회귀 테스트: context-engine.test.ts:1161-1208 의 동시성 테스트 옆에
  "factory await 중 clearContextEnginesForOwner → owner metadata 가 freshEntry
  를 반영" 시나리오 추가. 기존 테스트는 서로 다른 id 의 concurrent register
  만 검증, resolve vs clear 동시는 미검증.
- CODEOWNERS 검사: `src/context-engine/registry.ts` 는 보안 민감 경로 매치
  안 함 — 일반 ownership.
- AI-assisted 표시: PR 본문 12섹션 포함.
- CAND-035/036/037 (같은 도메인 인접 race) 와 PR 동시 진행 시 같은 file
  (registry.ts) 의 다른 hunk 와 rebase 충돌 가능 — 분리 PR 권장 (각 axis 가
  독립).
