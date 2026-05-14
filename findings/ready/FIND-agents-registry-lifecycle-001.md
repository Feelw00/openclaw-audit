---
id: FIND-agents-registry-lifecycle-001
cell: agents-registry-lifecycle
title: markTerminated drops pendingLifecycleTimeout marker; stale timer overwrites
  kill
file: src/agents/subagent-registry-run-manager.ts
line_range: 503-528
evidence: "```ts\n    for (const runId of runIds) {\n      params.clearPendingLifecycleError(runId);\n\
  \      const entry = params.runs.get(runId);\n      if (!entry) {\n        continue;\n\
  \      }\n      if (typeof entry.endedAt === \"number\") {\n        continue;\n\
  \      }\n      entry.endedAt = now;\n      entry.outcome = withSubagentOutcomeTiming(\n\
  \        { status: \"error\", error: reason },\n        {\n          startedAt:\
  \ entry.startedAt,\n          endedAt: now,\n        },\n      );\n      entry.endedReason\
  \ = SUBAGENT_ENDED_REASON_KILLED;\n      entry.cleanupHandled = true;\n      entry.cleanupCompletedAt\
  \ = now;\n      entry.suppressAnnounceReason = \"killed\";\n      if (!entriesByChildSessionKey.has(entry.childSessionKey))\
  \ {\n        entriesByChildSessionKey.set(entry.childSessionKey, entry);\n     \
  \ }\n      updated += 1;\n    }\n```\n"
symptom_type: lifecycle-gap
problem: '`markSubagentRunTerminated` (run-manager.ts:479-580) 가 entry 를 killed 로
  전환할 때

  L504 `params.clearPendingLifecycleError(runId)` 만 호출하고

  `clearPendingLifecycleTimeout(runId)` 는 호출하지 않는다. listener (registry.ts:937)

  의 `evt.data?.aborted === true` 분기에서 schedulePendingLifecycleTimeout 이 15초

  grace timer 를 걸어 둔 상태로 kill 이 진행되면, `cleanupHandled=true /

  cleanupCompletedAt=now / suppressAnnounceReason="killed"` 가 set 된 entry 옆에

  pending timer 가 잔존한다. 15초 이내 timer 가 fire 하면 callback

  (registry.ts:442-466) 의 모든 guard 를 통과하고 `completeSubagentRun({reason:

  COMPLETE, outcome: {status: "timeout"}, triggerCleanup: true})` 가 추가로 호출되어

  killed entry 의 endedReason / outcome / cleanupCompletedAt 가 모두 덮어쓰인다.

  '
mechanism: "1. user/agent runtime 가 `markSubagentRunTerminated({runId, reason: \"\
  killed\"})` 호출.\n   run-manager.ts:504 `params.clearPendingLifecycleError(runId)`\
  \ 만 실행되고\n   pendingLifecycleTimeoutByRunId 의 entry 는 그대로 유지.\n2. entry 는 line 512-523\
  \ 에서 `endedAt=now, outcome={error}, endedReason=KILLED,\n   cleanupHandled=true,\
  \ cleanupCompletedAt=now, suppressAnnounceReason=\"killed\"`\n   로 set. line 553\
  \ `completeCleanupBookkeeping` 가 정상 killed cleanup 수행\n   (run-manager.ts:531-577).\n\
  3. 동일 runId 에 대해 schedulePendingLifecycleTimeout (registry.ts:439-472) 가\n   이전에\
  \ 15초 timer 를 걸어 두었으면 (listener phase===\"end\" + aborted===true 시),\n   timer 가\
  \ fire. callback (registry.ts:442-466):\n   - L444 `if (!pending || pending.timer\
  \ !== timer) return;` — pending 가 그대로\n     존재하므로 통과.\n   - L447 `pendingLifecycleTimeoutByRunId.delete(runId)`.\n\
  \   - L448 `subagentRuns.get(runId)` — entry 는 sweeper TTL 5분 (SESSION_RUN_TTL_MS)\n\
  \     까지 잔존하므로 5분 이내 fire 면 entry 존재.\n   - L452 `if (entry.outcome?.status ===\
  \ \"ok\") return;` — killed 의 outcome 은\n     `error` 라 통과.\n   - L455-465 `completeSubagentRun({reason:\
  \ SUBAGENT_ENDED_REASON_COMPLETE,\n     outcome: {status: \"timeout\"}, triggerCleanup:\
  \ true})` 호출.\n4. completeSubagentRun (lifecycle.ts:765-886):\n   - L780-791 분기:\
  \ `reason === COMPLETE && suppressAnnounceReason === \"killed\"\n     && (cleanupHandled\
  \ || cleanupCompletedAt)` 가 정확히 markTerminated 의 set\n     상태에 fire → `suppressAnnounceReason=undefined,\
  \ cleanupHandled=false,\n     cleanupCompletedAt=undefined, completionAnnouncedAt=undefined`\
  \ 로 reset.\n   - L795-810 `entry.endedAt`, `entry.outcome`, `entry.endedReason`\
  \ 모두 덮어씀\n     (endedReason: KILLED → COMPLETE, outcome: error → timeout).\n   -\
  \ L823 safeFinalizeSubagentTaskRun(`outcome.status === \"timeout\"`) → detached\n\
  \     task runtime 의 deliveryStatus 가 'timed_out' 으로 변경.\n   - L858-865 `emitSubagentEndedHookForRun(reason=COMPLETE)`\
  \ — endedHookEmittedAt\n     이 이미 markTerminated 의 emitSubagentEndedHookOnce 호출\
  \ (run-manager.ts:559)\n     로 set 되어 있으면 line 527-529 의 early-return 으로 skip; 아니면\
  \ 두 번째\n     hook 발사.\n   - L871-877 `cleanupBrowserSessionsForLifecycleEnd` 호출\
  \ — PR #68669 의\n     browserCleanupDispatchedAt guard 가 막아주는 별 axis.\n   - L885\
  \ startSubagentAnnounceCleanupFlow → beginSubagentCleanup\n     (lifecycle.ts:409-420)\
  \ 의 guard 가 reset 된 cleanupHandled/cleanupCompletedAt\n     으로 인해 *통과* (둘 다 falsy)\
  \ → cleanup re-entry 가능.\n5. 결과: 외부 관측 endedReason 이 KILLED → COMPLETE 로 잘못 보고되고\
  \ outcome 이\n   timeout 으로 잘못 분류된다. cleanup re-entry 로 인한 idempotence 부담은 PR\n \
  \  #68669 의 dispatch flag 와 endedHookEmittedAt 가 일부 흡수하지만, context-engine\n   onSubagentEnded\
  \ (lifecycle.ts:486-491 / 497-502) 는 wrapper 가 없는 별 dispatch\n   경로라 동일 childSessionKey\
  \ 에 reason=\"deleted\"/\"completed\" 가 한 번 더 도착.\n"
root_cause_chain:
- why: 왜 markTerminated 가 timeout marker 를 clear 하지 않는가?
  because: 'run-manager.ts:105-147 의 deps interface 에 ''clearPendingLifecycleTimeout''

    자체가 노출되지 않았다. ''clearPendingLifecycleError'' 만 line 123 에 등록.

    registry.ts:386 의 함수가 module-local 이고 controller (createSubagentRunManager)

    에 전달되지 않아 markTerminated 는 access 가 없다. 동일 의도의

    finalizeInterruptedSubagentRun (registry.ts:1100-1102) 은 module-local 호출이라

    두 marker 모두 clear — interface gap 으로 인한 incidental 누락.

    '
  evidence_ref: src/agents/subagent-registry-run-manager.ts:123 (deps interface)
- why: 왜 timer fire 시 stale entry 가 reset 되는가?
  because: 'lifecycle.ts:780-791 의 분기는 ''killed 후 진짜 COMPLETE event 가 늦게

    도착하는'' 정상 시나리오 (예: gateway sigterm 직전 child 가 normal exit) 를

    처리하기 위해 도입됐다. 그러나 동일 분기가 stale grace timer 의 fire 도

    ''COMPLETE event 도착''으로 받아들여 reset 한다. timer callback 의 phase 표시

    (registry.ts:461 reason=COMPLETE, outcome.status=timeout) 와 listener phase=end

    가 lifecycle.ts:780-791 입장에서 구별되지 않는다.

    '
  evidence_ref: src/agents/subagent-registry-lifecycle.ts:780-791
- why: 왜 timing 창 15초 가 production 에서 trigger 되는가?
  because: 'LIFECYCLE_TIMEOUT_RETRY_GRACE_MS = 15_000 (registry.ts:196). listener
    의

    aborted 분기 (registry.ts:936-942) 는 gateway timeout / child sigterm 직후 emit

    되는 ''phase===end + aborted===true'' event 에서 fire. 동일 run 에 대해 user kill

    이나 orphan recovery (PR #54764 영역) 가 15초 이내 진행되면 race 발생.

    production 에서 gateway timeout 후 user 가 manual kill 을 누르는 시퀀스나,

    orphan recovery 가 abort event 직후 dispatch 되는 패턴이 빈번.

    '
  evidence_ref: src/agents/subagent-registry.ts:196 (constant)
impact_hypothesis: wrong-output
impact_detail: "외부 관측 영향 (정성):\n1. subagent_ended hook 의 reason 이 KILLED 가 아닌 COMPLETE\
  \ 로 전달, outcome.status\n   가 'ok'/'timeout' 으로 잘못 분류. upstream CI/monitoring 의 kill\
  \ 통계가\n   'completed' 로 오인.\n2. context-engine.onSubagentEnded 가 reason=\"completed\"\
  \ 로 한 번 더 dispatch —\n   동일 childSessionKey 에 대해 두 번 호출되어 plugin 측 idempotence 가정\n\
  \   (memory plugin 의 session-close handler 등) 에 부담.\n3. cleanupCompletedAt 가 undefined\
  \ 로 재설정되어 sweeper 의 5분 TTL 카운터가\n   reset — entry 가 추가 5분 잔존 (memory 영향 P3 수준).\n\
  4. detached task runtime 의 deliveryStatus 가 'failed' (killed) → 'timed_out'\n  \
  \ 으로 덮여 background task tracker 가 다른 종료 사유로 보고.\n재현 조건: subagent run 이 'phase=end\
  \ + aborted=true' event 를 발사\n(gateway timeout, transport drop 등) → 15초 이내 markSubagentRunTerminated\
  \ 가\n호출 (user kill, orphan recovery, finalizeInterruptedSubagentRun 별 경로). timing\
  \ 창\n15s 로 좁지만, gateway timeout 후 manual kill 시퀀스는 production 에서 충분히\n빈번 (orphan\
  \ recovery + user 개입 동시 발생).\n"
severity: P2
counter_evidence:
  path: src/agents/subagent-registry.ts
  line: 1100-1102
  reason: "R-3 lifecycle 축 Grep + execution condition 분류 (CAL-001 / R-5).\n\nGrep:\n\
    \n  rg -n \"clearPendingLifecycleTimeout\" src/agents/subagent-registry*.ts\n\
    \    subagent-registry.ts:386    function defn\n    subagent-registry.ts:403 \
    \   schedulePendingLifecycleError 진입에서 cross-clear\n    subagent-registry.ts:441\
    \    schedulePendingLifecycleTimeout 진입에서 self-clear\n    subagent-registry.ts:877\
    \    sweeper TTL cleanup (5min)\n    subagent-registry.ts:912    listener phase===\"\
    start\"\n    subagent-registry.ts:957    listener phase===\"end\" 정상 경로\n    subagent-registry.ts:1102\
    \   finalizeInterruptedSubagentRun\n\n  rg -n \"markSubagentRunTerminated\" src/agents/\n\
    \    subagent-registry-run-manager.ts:479  defn (본 finding)\n    subagent-registry-run-manager.ts:585\
    \  export\n\n  rg -n \"clearPendingLifecycleError\" src/agents/subagent-registry-run-manager.ts\n\
    \    run-manager.ts:123  deps interface (timeout 측 노출 부재)\n    run-manager.ts:280\
    \  replaceSubagentRunAfterSteer 내부\n    run-manager.ts:307  replaceSubagentRunAfterSteer\n\
    \    run-manager.ts:457  releaseSubagentRun\n    run-manager.ts:504  markSubagentRunTerminated\n\
    \nExecution condition 분류:\n\n| 경로 | clearError | clearTimeout | 조건 |\n|---|---|---|---|\n\
    | schedulePendingLifecycleError (reg:402-404) | self | cross-clear | unconditional,\
    \ function entry |\n| schedulePendingLifecycleTimeout (reg:439-441) | cross-clear\
    \ | self | unconditional, function entry |\n| listener phase==='start' (reg:911-912)\
    \ | both | both | unconditional, sync |\n| listener phase==='end' (reg:956-957)\
    \ | both | both | unconditional, sync (yielded/aborted 분기 후 도달) |\n| sweeper TTL\
    \ (reg:870-879) | TTL 5분 | TTL 5분 | conditional (TTL 도달 시 cleanup) |\n| finalizeInterruptedSubagentRun\
    \ (reg:1101-1102) | both | both | unconditional, dispose loop |\n| replaceSubagentRunAfterSteer\
    \ (run-mgr:307) | error only | **missing** | unconditional, dispose loop |\n|\
    \ releaseSubagentRun (run-mgr:457) | error only | **missing** | unconditional,\
    \ dispose loop |\n| **markSubagentRunTerminated (run-mgr:504)** | error only |\
    \ **missing** | unconditional, dispose loop |\n\nDispose 경로 3개 (replaceSubagentRunAfterSteer,\
    \ releaseSubagentRun,\nmarkSubagentRunTerminated) 가 모두 timeout marker clearance\
    \ 를 누락. 그러나:\n- replaceSubagentRunAfterSteer 는 line 311 'params.runs.delete(previousRunId)'\n\
    \  로 entry 자체를 즉시 제거 → timer fire 시 line 449 'if (!entry) return;'\n  으로 safe-exit.\
    \ marker map 에 5분 잔존 (P3 memory 만).\n- releaseSubagentRun 도 line 470 'params.runs.delete(runId)'\
    \ → 동일하게 safe.\n- **markSubagentRunTerminated 는 entry 를 *삭제하지 않고* cleanupHandled=true\n\
    \  + cleanupCompletedAt=now 로 maintain** → sweeper SESSION_RUN_TTL_MS 5분까지\n \
    \ entry 잔존 + L452 'outcome.status === \"ok\"' check 도 killed 의 'error'\n  outcome\
    \ 으로 통과 → completeSubagentRun reset 분기 (lifecycle.ts:780-791) 가\n  정확히 fire.\n\
    \nmarkTerminated 만 functional 영향. 본 finding 은 markTerminated 단독.\n\nProduction\
    \ hot-path (R-7):\n- schedulePendingLifecycleTimeout 호출 출처: listener (registry.ts:937)\n\
    \  'evt.data?.aborted === true' — gateway timeout / abort 시 fire.\n- markSubagentRunTerminated\
    \ callsite: user kill, orphan recovery (PR #54764\n  영역, out-of-scope file), gateway\
    \ shutdown handler — production hot-path.\n- timing 창 LIFECYCLE_TIMEOUT_RETRY_GRACE_MS\
    \ = 15_000ms.\n\nPR overlap (CAL-008):\n- PR #68669 (OPEN, head 7067f30ab2): browser\
    \ cleanup wrapper dedup. axis 다름\n  ('cleanupBrowserSessionsForLifecycleEnd' wrapper\
    \ 의 idempotence vs marker map\n  clearance).\n- PR #54764 (OPEN, head 51380f1a94):\
    \ markSubagentRunTerminated 영역 수정.\n  'gh pr diff 54764 | grep clearPendingLifecycleTimeout'\
    \ → 0 매치. axis 무관.\n- PR #77415 (OPEN): listener 의 'first-progress'/'startup-failed'\
    \ 분기에\n  clearPendingLifecycleTimeout 추가. markTerminated 축 미수정.\n- PR #80886,\
    \ #76332, #74131, #75462, #53314, #75786: subagent-announce /\n  completion /\
    \ gateway readiness 다른 축. marker map clearance 미수정.\n- 'git log upstream/main\
    \ --since=\"6 weeks ago\" -- src/agents/subagent-registry-run-manager.ts'\n  범위에서\
    \ markTerminated + clearPendingLifecycleTimeout 키워드 commit 없음.\n"
status: discovered
discovered_by: plugin-lifecycle-auditor
discovered_at: '2026-05-14'
cross_refs: []
solution_refs: []
rejected_reasons: []
---
# markSubagentRunTerminated drops pendingLifecycleTimeout marker

## 문제

`markSubagentRunTerminated` (src/agents/subagent-registry-run-manager.ts:479-580) 는 subagent run 을 외부 사유로 강제 종료한다. dispose loop (L503-528) 에서 `params.clearPendingLifecycleError(runId)` (L504) 만 호출하고 동등한 timeout 측 marker (`pendingLifecycleTimeoutByRunId`, registry.ts:362-368) 는 clear 하지 않는다.

동일 종착점인 `finalizeInterruptedSubagentRun` (registry.ts:1100-1102) 은 두 marker 를 모두 clear 한다 — API contract 비대칭.

```
finalizeInterruptedSubagentRun  →  clearPendingLifecycleError  + clearPendingLifecycleTimeout
markSubagentRunTerminated       →  clearPendingLifecycleError  + (missing)
```

`pendingLifecycleTimeoutByRunId` 의 timer 는 listener (registry.ts:937) 의 `phase==="end" && aborted===true` 분기가 set. gateway timeout / abort event 직후 15초 grace 동안 fire 대기 중. user kill 이나 orphan recovery 가 이 15초 안에 markSubagentRunTerminated 를 호출하면 timer 가 잔존한 채 entry 가 killed 상태로 maintain → 5분 SESSION_RUN_TTL_MS 까지 entry 살아있는 동안 timer 가 fire 가능.

## 발현 메커니즘

```
t=0    listener phase=end + aborted=true
       → schedulePendingLifecycleTimeout(runId, 15s)
       → entry.endedAt = endedAt; pending = {timer, endedAt}

t=5s   user kill → markSubagentRunTerminated(runId)
       → clearPendingLifecycleError(runId)   ← timeout 측 안 건드림
       → entry.endedReason = KILLED
       → entry.cleanupHandled = true
       → entry.cleanupCompletedAt = now
       → entry.suppressAnnounceReason = "killed"
       → completeCleanupBookkeeping(...)
       → emitSubagentEndedHookOnce(reason=KILLED)   ← endedHookEmittedAt set
       → cleanupBrowserSessionsForLifecycleEnd
         (PR #68669 dispatch flag 가 dedup)

t=15s  pendingLifecycleTimeoutByRunId timer fires
       callback (registry.ts:442-466):
         L444 pending.timer === timer       OK
         L447 pendingLifecycleTimeoutByRunId.delete(runId)
         L448 entry = subagentRuns.get(runId)   ← entry 잔존 (TTL 5분)
         L452 entry.outcome?.status === "ok"   ← killed outcome 은 "error"
              → false → 통과
         L455-465 completeSubagentRun({
            reason: SUBAGENT_ENDED_REASON_COMPLETE,
            outcome: { status: "timeout" },
            triggerCleanup: true,
         })

t=15s  completeSubagentRun (lifecycle.ts:765-886):
       L780-791 분기: reason===COMPLETE && suppressAnnounceReason==="killed"
                && (cleanupHandled || cleanupCompletedAt)
                ↓ 정확히 일치 ↓
         entry.suppressAnnounceReason = undefined
         entry.cleanupHandled = false
         entry.cleanupCompletedAt = undefined
         entry.completionAnnouncedAt = undefined

       L795-810 entry.endedAt / outcome / endedReason 덮어씀
                ↓
         endedReason: KILLED   → COMPLETE
         outcome    : error    → timeout

       L823 safeFinalizeSubagentTaskRun(outcome.status==="timeout")
            → detached task runtime: 'failed'(killed) → 'timed_out'

       L858-865 emitSubagentEndedHookForRun(reason=COMPLETE)
                L527-529 'if (params.entry.endedHookEmittedAt) return;'
                ↓ markTerminated 가 이미 set 했으면 skip ↓
                ↓ aborted 후 markTerminated 사이 ordering 따라
                   set 안 된 경로면 두 번째 hook 발사

       L871-877 cleanupBrowserSessionsForLifecycleEnd
                PR #68669 의 browserCleanupDispatchedAt guard 가 dedup

       L885 startSubagentAnnounceCleanupFlow
            beginSubagentCleanup (lifecycle.ts:409-420) 의 guard:
              cleanupCompletedAt || cleanupHandled
              ↓ 모두 undefined/false 로 reset 됐으므로 통과 ↓
            cleanupHandled = true 재진입 → cleanup re-run.
```

## 근본 원인 분석

1. **Interface gap**: run-manager.ts:105-147 의 deps interface 에 `clearPendingLifecycleTimeout` 자체가 노출되지 않았다. line 123 에 `clearPendingLifecycleError` 만 등록되어 controller (`createSubagentRunManager`) 는 timeout 측에 접근할 수 없다. registry.ts 의 controller 생성부 (L975-993) 에서 `clearPendingLifecycleError` 만 전달.

2. **동일 종착점의 API 비대칭**: finalizeInterruptedSubagentRun (registry.ts:1100-1102) 은 module-local 함수라 두 marker 를 직접 clear. markSubagentRunTerminated 는 module 밖 controller 에서 정의되어 deps 의존성에 의해 incidental 누락이 발생.

3. **completeSubagentRun L780-791 reset 분기의 ambiguity**: 'killed 후 진짜 COMPLETE event 가 늦게 도착하는' 정상 시나리오 (예: gateway sigterm 직전 child 가 normal exit) 와 'stale grace timer fire' 시나리오가 lifecycle.ts:780-791 입장에서 구분되지 않는다. 두 경우 모두 `reason === COMPLETE && suppressAnnounceReason === "killed"` 에 매치.

4. **Sweeper TTL 의 entry 보존**: markTerminated 가 entry 를 delete 하지 않고 cleanupCompletedAt 만 set 한 것은 detached task runtime / persistence 가 종료 상태를 query 할 수 있도록 5분 잔존을 보장하는 의도. 이 잔존이 stale timer fire 의 functional impact 를 가능하게 한다.

## 영향

- **외부 hook 관측치 왜곡**: subagent_ended hook 의 reason 이 KILLED 가 아닌 COMPLETE 로 전달, outcome.status 가 'timeout' 으로 분류. upstream CI / monitoring / billing 의 kill 통계가 'completed' 로 오인.
- **plugin idempotence 부담**: context-engine.onSubagentEnded (lifecycle.ts:486-491 'deleted' / 497-502 'completed') 가 동일 childSessionKey 에 대해 두 번 dispatch. memory plugin 의 session-close handler, persistence layer 의 final commit 등이 이중 호출 가정 부담.
- **detached task tracker 오류**: safeFinalizeSubagentTaskRun (lifecycle.ts:823) 가 status 를 'failed' → 'timed_out' 으로 갱신. background task UI / agent.list 결과 변경.
- **Memory P3**: cleanupCompletedAt 가 undefined 로 reset 되어 sweeper TTL 카운터 reset → entry 가 추가 5분 잔존.
- **재현 조건**: gateway timeout / transport drop → listener phase=end + aborted=true → 15초 이내 user kill 또는 orphan recovery 의 markSubagentRunTerminated. timing 창 15s 좁지만 production 에서 충분히 빈번 (gateway timeout 후 user 개입은 정규 시나리오).

## 반증 탐색

### Lifecycle marker dispose 매트릭스 (R-3 Grep 결과)

```
rg -n "clearPendingLifecycleTimeout" src/agents/subagent-registry*.ts
  subagent-registry.ts:386    function defn
  subagent-registry.ts:403    schedulePendingLifecycleError cross-clear
  subagent-registry.ts:441    schedulePendingLifecycleTimeout self-clear
  subagent-registry.ts:877    sweeper TTL cleanup (5min)
  subagent-registry.ts:912    listener phase==="start"
  subagent-registry.ts:957    listener phase==="end" 정상
  subagent-registry.ts:1102   finalizeInterruptedSubagentRun
```

7개 호출 사이트 모두 unconditional. **markSubagentRunTerminated 만 누락**.

### Execution condition 표 (CAL-001 / R-5)

| 경로 | clearError | clearTimeout | 조건 |
|---|---|---|---|
| schedulePendingLifecycleError (reg:402-404) | self | cross-clear | unconditional, function entry |
| schedulePendingLifecycleTimeout (reg:439-441) | cross-clear | self | unconditional, function entry |
| listener phase==='start' (reg:911-912) | both | both | unconditional, sync |
| listener phase==='end' (reg:956-957) | both | both | unconditional, sync |
| sweeper TTL (reg:870-879) | TTL 5분 | TTL 5분 | conditional (TTL 도달) |
| finalizeInterruptedSubagentRun (reg:1101-1102) | both | both | unconditional, dispose loop |
| replaceSubagentRunAfterSteer (run-mgr:307) | error only | **missing** | unconditional, dispose loop |
| releaseSubagentRun (run-mgr:457) | error only | **missing** | unconditional, dispose loop |
| **markSubagentRunTerminated (run-mgr:504)** | error only | **missing** | unconditional, dispose loop |

3개 dispose 경로가 timeout marker 를 누락. 그러나:

- **replaceSubagentRunAfterSteer**: line 311 `params.runs.delete(previousRunId)` 로 entry 즉시 제거 → timer fire 시 callback L449 `if (!entry) return;` 으로 safe-exit. marker map 에 5분 잔존 (P3 memory).
- **releaseSubagentRun**: line 470 `params.runs.delete(runId)` → 동일하게 safe. 또한 production code 호출자 없음 (test only, `rg -n "releaseSubagentRun\b" src/` 확인).
- **markSubagentRunTerminated 만** entry 를 *삭제하지 않고* maintain 하므로 timer callback 의 모든 guard 통과 → functional impact 발생.

### Production hot-path (R-7)

- schedulePendingLifecycleTimeout 호출 출처: registry.ts:937 `phase==="end" && aborted===true` — gateway timeout / abort 정규 경로.
- markSubagentRunTerminated callsite: user kill (gateway-side handler), orphan recovery (PR #54764 영역, out-of-scope file), `agent.kill` 명령 — production hot-path.
- timing 창 LIFECYCLE_TIMEOUT_RETRY_GRACE_MS = 15_000ms (registry.ts:196). 짧지만 gateway timeout → user kill 시퀀스는 정규 운영 패턴.

### Upstream / Open PR 분리 (CAL-008)

- **PR #68669 (OPEN, head 7067f30ab2)**: `cleanupBrowserSessionsForLifecycleEnd` wrapper 의 dispatch flag dedup. 본 finding 의 marker map clearance axis 와 무관.
- **PR #54764 (OPEN, head 51380f1a94)**: markSubagentRunTerminated 영역 수정 중. `gh pr diff 54764 | grep -c "clearPendingLifecycleTimeout\|pendingLifecycleTimeoutByRunId"` → 0. axis 분리.
- **PR #77415 (OPEN)**: listener 의 `first-progress` / `startup-failed` 분기에 `clearPendingLifecycleTimeout` 추가. markTerminated 축 미수정.
- **PR #80886 (OPEN)**: listener completion 분기 보강. markTerminated 미수정.
- **PR #76332, #74131, #75462, #53314, #75786 (OPEN)**: subagent-announce / gateway readiness / completion 다른 축. marker map clearance 미수정.
- `git log upstream/main --since="6 weeks ago" -- src/agents/subagent-registry-run-manager.ts` 범위에서 markTerminated + clearPendingLifecycleTimeout 키워드 commit 없음.

### Primary-path inversion (CAL-001)

defensive 경로로 sweeper TTL 5분 (registry.ts:870-879) 이 stale marker 를 cleanup 하지만, fire 가 15초 grace 안에 발생하면 sweeper 의 5분 TTL 보다 빠르므로 무의미하다. 즉 unconditional dispose 경로 자체가 markTerminated 에서 빠진 형태.

### Hot-path-vs-test-path (CAL-003)

`subagent-registry.steer-restart.test.ts` (PR #54764 의 test 영역), `subagent-registry.lifecycle-retry-grace.e2e.test.ts` 등이 markSubagentRunTerminated 의 happy path 와 idempotence 를 cover 하지만, 'aborted 직후 markTerminated 가 15초 안에 호출' 시나리오는 fixture 부재 (확인: `rg -n "schedulePendingLifecycleTimeout|aborted.*kill" src/agents/subagent-registry*.test.ts` → 0).

## Self-check

### 내가 확실한 근거

- run-manager.ts:504 의 단일 호출 (`params.clearPendingLifecycleError(runId)`) 실재 — Read 로 확인.
- registry.ts:386 의 clearPendingLifecycleTimeout 정의 + 7개 호출 사이트 실재 — Grep 으로 확인.
- finalizeInterruptedSubagentRun (registry.ts:1100-1102) 이 두 marker 를 모두 clear — Read 로 확인.
- run-manager.ts:123 deps interface 에 clearPendingLifecycleTimeout 미노출 — Read 로 확인.
- markTerminated 가 cleanupCompletedAt=now + entry 잔존 (delete 안 함) — Read 로 확인.
- completeSubagentRun L780-791 reset 분기의 조건 — Read 로 확인.
- pendingLifecycleTimeoutByRunId 가 registry.ts:362-368 새로 추가된 marker (PR 845040214e, 2026-04-25) — git log -S 확인.

### 내가 한 가정

- markTerminated 의 production callsite 가 gateway timeout 후 충분한 frequency 로 trigger 된다 — allowed_paths 밖이라 직접 확인 불가. PR #77415 / #80886 이 listener 의 timeout marker 처리를 보강하는 fix 를 진행 중인 점은 timer 가 실제로 production 에서 fire 한다는 *간접 증거*.
- completeSubagentRun L780-791 의 reset 분기 도입 의도가 'killed → COMPLETE late arrival' 이라는 추정 — 코드 주석 부재.
- detached task runtime 의 status enum 매핑 (`'failed'(killed)` → `'timed_out'`) 이 외부 UI 에 visible — safeFinalizeSubagentTaskRun 의 implementation 은 out-of-scope file. 정확한 visibility 미확인.
- aborted event 와 markTerminated 의 ordering 이 race 가 가능하다는 가정 — gateway-side handler 의 emit 순서는 allowed_paths 밖.

### 확인 안 한 것 중 영향 가능성

- markTerminated 가 호출되는 정확한 production 시나리오 (subagent-orphan-recovery.ts 의 prune 호출, gateway-side kill handler 의 path 등) — 실제 빈도 측정 불가.
- emitSubagentEndedHookOnce (completion.ts:66-121) 의 inFlightRunIds guard 가 markTerminated 의 hook 발사 vs 본 race 의 두 번째 hook 발사 사이에 *항상* 동작하는지. emitSubagentEndedHookOnce 의 try/finally 가 inFlightRunIds.delete 를 unconditional 으로 보장하지만 — markTerminated 가 emit 완료 후 endedHookEmittedAt 가 set 됐다면 line 80-82 의 early-return 으로 dedup. 그러나 markTerminated 의 emit (run-manager.ts:559) 이 *async* 라 timer fire 시점에 endedHookEmittedAt 가 아직 set 되지 않았을 가능성 → 두 번째 hook 발사 가능.
- context-engine.onSubagentEnded 의 plugin 측 idempotence 보장 여부 — context-engine 구현은 allowed_paths 밖. memory plugin / persistence plugin 의 session-close 동작이 이중 호출 시 어떻게 반응하는지 미확인.
- PR #77415 의 fix 가 머지되면 listener 가 `first-progress` 받을 때 timer clear → 본 finding 의 trigger 가 약화될 가능성. 그러나 markTerminated 가 first-progress 없이 fire 하는 경로 (orphan recovery 직접 호출) 는 여전히 노출.
- replaceSubagentRunAfterSteer / releaseSubagentRun 의 marker map 5분 잔존이 누적되어 memory pressure 를 만드는지 — 빈도 데이터 부재.
