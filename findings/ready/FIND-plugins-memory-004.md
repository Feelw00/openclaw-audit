---
id: FIND-plugins-memory-004
cell: plugins-memory
title: runContextByRunId Map 이 terminal lifecycle 이벤트 미발생 시 runId 별 항목을 영구 누적
file: src/plugins/host-hook-runtime.ts
line_range: 28-38
evidence: "```ts\ntype PluginHostRuntimeState = {\n  runContextByRunId: Map<string,\
  \ PluginRunContextByPlugin>;\n  schedulerJobsByPlugin: Map<string, Map<string, SchedulerJobRecord>>;\n\
  \  nextSchedulerJobGeneration: number;\n  pendingAgentEventHandlersByRunId: Map<string,\
  \ Set<Promise<void>>>;\n  closedRunIds: Set<string>;\n  terminalEventCleanupExpiredRunIds:\
  \ Set<string>;\n};\n\nconst PLUGIN_HOST_RUNTIME_STATE_KEY = Symbol.for(\"openclaw.pluginHostRuntimeState\"\
  );\nconst CLOSED_RUN_IDS_MAX = 512;\n```\n"
symptom_type: memory-leak
problem: 'globalThis singleton 으로 보관되는 runContextByRunId Map 은 plugin 이 setRunContext
  API 로

  쓴 run-context 를 runId 별로 보관한다. 같은 state 객체의 closedRunIds / terminalEventCleanupExpiredRunIds

  Set 은 CLOSED_RUN_IDS_MAX=512 LRU 상한이 강제되지만, runContextByRunId 는 size 기반 cap 도

  TTL 도 없다. cleanup 은 (a) 해당 runId 의 terminal lifecycle 이벤트 dispatch 또는 (b) registry

  teardown/restart 두 조건부 경로에만 의존한다. terminal 이벤트가 한 번도 오지 않는 runId 의 항목은

  process 종료까지 회수되지 않는다.

  '
mechanism: "1. plugin 이 api.runContext.setRunContext({runId, namespace, value}) 호출\
  \ → registry.ts:2725\n   → setPluginRunContext (host-hook-runtime.ts:171) → getPluginRunContextNamespaces({create:true})\n\
  \   가 runContextByRunId.set(runId, new Map()) 으로 runId 항목 생성.\n2. runContextByRunId\
  \ 의 유일한 삭제 지점은 clearPluginRunContext 안의 delete (host-hook-runtime.ts:268).\n3. clearPluginRunContext({runId})\
  \ 가 호출되는 production 경로는 두 곳뿐:\n   - dispatchPluginAgentEventSubscriptions 가 terminal\
  \ lifecycle 이벤트(stream===\"lifecycle\",\n     phase===\"end\"|\"error\")를 받았을 때\
  \ (host-hook-runtime.ts:346-353).\n   - runPluginHostCleanup 이 pluginId/runId 인자와\
  \ 함께 호출될 때 (host-hook-cleanup.ts:442-448),\n     이는 registry swap/disable/restart\
  \ 시점.\n4. dispatchPluginAgentEventSubscriptions 자체는 syncPluginAgentEventBridge 가\
  \ onAgentEvent 브리지를\n   설치했을 때만 실행된다 (runtime.ts:140-144). 브리지는 live registry 가\
  \ 존재할 때 설치되지만,\n   terminal 이벤트가 그 runId 로 실제 emit 되는지는 보장되지 않는다.\n5. plugin 이 agentEventSubscription\
  \ 을 등록하지 않고 runContext API 만 쓰는 경우, 또는 run 이\n   terminal lifecycle 이벤트 없이 종료/중단되는\
  \ 경우, 해당 runId 항목은 (b) registry teardown\n   까지 영구 누적. gateway 는 장시간 단일 registry\
  \ 로 운영되므로 (b) 가 거의 발동하지 않는다.\n6. runId 는 run 마다 고유 → O(처리한 run 수) 만큼 Map 성장.\n"
root_cause_chain:
- why: 왜 runContextByRunId 가 무제한 성장할 수 있는가
  because: 'size 기반 cap / TTL 이 없고, 삭제가 terminal 이벤트 또는 registry teardown 이라는

    조건부 경로에만 의존한다. 같은 파일의 closedRunIds/terminalEventCleanupExpiredRunIds 는

    CLOSED_RUN_IDS_MAX 상한이 있으나 runContextByRunId 에는 동일 방어가 없다.

    '
  evidence_ref: src/plugins/host-hook-runtime.ts:38
- why: 왜 terminal 이벤트 cleanup 이 모든 runId 를 커버하지 못하는가
  because: 'cleanup 은 dispatchPluginAgentEventSubscriptions 가 phase end/error 이벤트를
    받아야

    발동한다. setRunContext 는 agentEventSubscription 과 독립된 plugin API surface 라

    (types.ts:2551 runContext vs 2540 agent events), subscription 없이 runContext 만
    쓰는

    plugin 의 runId 는 이 경로를 타지 않는다.

    '
  evidence_ref: src/plugins/host-hook-runtime.ts:346
- why: 왜 registry teardown cleanup 이 실효적 안전망이 되지 못하는가
  because: 'runPluginHostCleanup 의 clearPluginRunContext 는 pluginId 또는 runId 가 있을
    때만 실행되고

    (host-hook-cleanup.ts:442-448), cleanupReplacedPluginHostRegistry 는 registry swap
    시에만

    pluginId 별로 호출된다. gateway 는 단일 registry 로 장시간 실행되므로 swap 이 드물다.

    '
  evidence_ref: src/plugins/host-hook-cleanup.ts:447
impact_hypothesis: memory-growth
impact_detail: '정성: runContext API 를 쓰지만 agentEventSubscription 을 등록하지 않은 plugin,
  또는 terminal

  lifecycle 이벤트 없이 끝나는 run 이 있는 환경에서, gateway 의 장시간 단일 registry 운영 동안

  runContextByRunId 가 처리한 run 수에 비례해 성장.

  정량: run 당 최소 1개 Map 항목 + plugin/namespace 별 중첩 Map + structuredClone 된 JSON 값.

  하루 수천~수만 run 을 처리하는 gateway 라면 며칠 단위로 관측 가능한 누적. terminal 이벤트가

  정상 발생하는 run 은 누수 없음 → 누수 속도는 "비정상 종료 run + subscription 미등록 plugin"

  비율에 의존.

  '
severity: P2
counter_evidence:
  path: src/plugins/host-hook-runtime.ts
  line: '268'
  reason: "R-3 Grep 으로 대응 cleanup 경로 탐색:\n- `rg -n \"runContextByRunId\\.(delete|clear|evict|splice|shift|pop)\"\
    \ src/plugins/`\n  → host-hook-runtime.ts:268 의 delete 1건만 존재 (clearPluginRunContext\
    \ 내부).\n- `rg -n \"(cap|max|limit|MAX).*runContext|runContextByRunId.*size\" src/plugins/`\n\
    \  → production 코드 match 없음 (테스트 파일만).\n- `rg -n \"while.*runContextByRunId\"\
    \ src/plugins/` → match 없음.\ncleanup 경로 실행 조건 분류 (R-5):\n| 경로 | 조건 |\n|---|---|\n\
    | dispatchPluginAgentEventSubscriptions 의 clearPluginRunContext (L351) | conditional-edge\
    \ — 해당 runId 로 terminal lifecycle(end/error) 이벤트가 dispatch 될 때만 |\n| runPluginHostCleanup\
    \ 의 clearPluginRunContext (host-hook-cleanup.ts:447) | conditional-edge — registry\
    \ swap/disable/restart 시점, pluginId/runId 인자 필요 |\n| clearPluginHostRuntimeState\
    \ 의 full clear (L607-611) | shutdown — pluginId/runId 미지정 시에만 |\n| plugin 의 api.runContext.clearRunContext\
    \ | conditional — plugin 이 자발적으로 호출할 때만 |\nunconditional cleanup 경로 없음 → R-5 규율상\
    \ leak 후보 성립.\nproduction trigger 실재 여부: runContext API (types.ts:2551) 는 agentEventSubscription\n\
    (types.ts:2540) 과 별개의 plugin API surface 이므로, subscription 없이 setRunContext 만\n\
    쓰는 plugin 이 코드상 가능. 다만 (1) 실제 그런 plugin 이 bundled 로 존재하는지, (2) terminal\n이벤트 없이\
    \ 끝나는 run 의 빈도는 본 감사 범위(src/plugins/**, src/plugin-sdk/**)에서 단정\n불가 — agent run\
    \ lifecycle emit 보장은 src/agents, src/gateway 소관. 따라서 메커니즘은\n코드상 성립하나 실제 발현 빈도는\
    \ 외부 경로에 의존하며 severity 를 P1 이 아닌 P2 로 책정.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-05-21'
---
# runContextByRunId Map 이 terminal lifecycle 이벤트 미발생 시 runId 별 항목을 영구 누적

## 문제
`getPluginHostRuntimeState()` 가 `globalThis` 싱글톤으로 보관하는 `runContextByRunId` Map 은
plugin 이 `setRunContext` API 로 기록한 run-context 를 `runId` 키로 저장한다. 같은 state 객체의
`closedRunIds` / `terminalEventCleanupExpiredRunIds` Set 은 `CLOSED_RUN_IDS_MAX = 512` LRU
상한이 강제되지만, `runContextByRunId` 는 size cap 도 TTL 도 없다.

## 발현 메커니즘
1. plugin → `api.runContext.setRunContext({runId, namespace, value})` (registry.ts:2725)
   → `setPluginRunContext` (host-hook-runtime.ts:171) → `getPluginRunContextNamespaces({create:true})`
   가 `runContextByRunId.set(runId, ...)` 으로 항목 생성.
2. 삭제 지점은 `clearPluginRunContext` 내부 `state.runContextByRunId.delete(runId)`
   (host-hook-runtime.ts:268) 단 하나.
3. 이 delete 에 도달하는 production 경로는 (a) terminal lifecycle 이벤트 dispatch 시
   (host-hook-runtime.ts:346-353), (b) registry teardown/restart (host-hook-cleanup.ts:442-448)
   둘뿐. 둘 다 조건부.
4. plugin 이 `agentEventSubscription` 을 등록하지 않고 `runContext` API 만 사용하거나, run 이
   terminal lifecycle 이벤트 없이 끝나면 (a) 경로가 발동하지 않는다. gateway 는 단일 registry 로
   장시간 운영되므로 (b) 도 드물다.
5. `runId` 는 run 마다 고유하므로 Map 은 처리한 run 수에 비례해 성장.

## 근본 원인 분석
1. 왜 무제한 성장 가능한가? → cap/TTL 부재, 삭제가 조건부 경로 의존 (host-hook-runtime.ts:38).
   동일 파일의 `closedRunIds` 는 `CLOSED_RUN_IDS_MAX` 상한 보유 — `runContextByRunId` 만 비대칭.
2. 왜 terminal cleanup 이 전부 못 잡는가? → `setRunContext` 와 `agentEventSubscription` 이
   독립 API surface (types.ts:2551 vs 2540), subscription 없는 plugin 의 runId 는 미커버
   (host-hook-runtime.ts:346).
3. 왜 registry teardown 이 안전망이 안 되는가? → swap/disable/restart 시점에만, pluginId/runId
   인자와 함께 호출 (host-hook-cleanup.ts:447). 장시간 단일 registry 환경에서 드묾.

## 영향
runContext API 를 쓰면서 agentEventSubscription 미등록인 plugin, 또는 terminal lifecycle 이벤트
없이 종료되는 run 이 있는 gateway 에서, 단일 registry 장시간 운영 중 `runContextByRunId` 가 처리
run 수에 비례해 성장한다. 정상 종료 run 만 있으면 누수 없음 → 누수 속도는 비정상 run 비율 의존.
당장 OOM 은 아니나 며칠 단위로 관측 가능.

## 반증 탐색
- 이미 cleanup 있는지: `runContextByRunId` 삭제 경로는 `clearPluginRunContext` 내 delete 1건뿐
  (Grep 결과 위 counter_evidence 에 명시). 모두 conditional-edge / shutdown / plugin-자발 경로 —
  unconditional 경로 없음.
- 외부 경계 장치: registry swap 시 `cleanupReplacedPluginHostRegistry` 가 pluginId 별로
  `clearPluginRunContext` 호출 → swap 빈번한 환경에서는 안전망. 단일 registry 장기 운영에서는
  미발동.
- 호출 맥락: `dispatchPluginAgentEventSubscriptions` 는 `onAgentEvent` 브리지가 설치돼야 실행되며
  (runtime.ts:140), terminal 이벤트가 해당 runId 로 emit 되는지는 src/agents/src/gateway 소관
  으로 본 감사 범위 밖. 따라서 메커니즘은 성립하나 발현 빈도는 외부 경로 의존.
- 기존 테스트: `contracts/run-context-lifecycle.contract.test.ts` 는 terminal 이벤트가 정상
  발생하는 happy-path 와 registry-swap 차단만 검증. terminal 이벤트가 끝까지 오지 않는 runId 의
  누적은 검증하지 않음.
탐색 카테고리 4개 적용. 반증 발견되지 않음. 단 production 발현 빈도는 외부 경로 의존 → P2.

## Self-check

### 내가 확실한 근거
- src/plugins/host-hook-runtime.ts:28-50 — `runContextByRunId` 선언, `CLOSED_RUN_IDS_MAX`
  상한이 `closedRunIds`/`terminalEventCleanupExpiredRunIds` 에만 적용됨.
- src/plugins/host-hook-runtime.ts:268 — `runContextByRunId` 의 유일한 delete.
- src/plugins/host-hook-runtime.ts:346-353 — terminal lifecycle 이벤트 조건부 cleanup.
- src/plugins/host-hook-cleanup.ts:442-448 — registry teardown 조건부 cleanup.
- src/plugins/types.ts:2540, 2551 — `registerAgentEventSubscription` 과 `setRunContext` 가
  독립된 API surface.

### 내가 한 가정
- runContext API 를 쓰면서 agentEventSubscription 을 등록하지 않는 plugin, 또는 terminal
  lifecycle 이벤트 없이 끝나는 run 이 production 에 존재한다는 가정. 코드상 가능하나 bundled
  plugin 인벤토리와 run lifecycle emit 보장은 미확인.
- gateway 가 단일 registry 로 장시간 운영된다는 가정 (CLAUDE.md 의 "장시간 gateway 프로세스"
  맥락과 일치).

### 확인 안 한 것 중 영향 가능성
- src/agents, src/gateway 의 agent run lifecycle: 모든 run 이 반드시 phase end/error 를 emit
  하는지, abort/crash 경로에서도 보장되는지 미확인 (감사 범위 밖). 보장된다면 누수는
  "subscription 미등록 plugin" 한정으로 좁아짐.
- bundled plugin 들이 실제로 runContext API 를 어떻게 쓰는지 전수 확인 안 함.
