---
id: FIND-agents-registry-error-boundary-002
cell: agents-registry-error-boundary
title: restoreSubagentRunsOnce silently swallows all restore failures with no retry
file: src/agents/subagent-registry.ts
line_range: 676-714
evidence: "```ts\nfunction restoreSubagentRunsOnce() {\n  if (restoreAttempted) {\n\
  \    return;\n  }\n  restoreAttempted = true;\n  try {\n    const restoredCount\
  \ = subagentRegistryDeps.restoreSubagentRunsFromDisk({\n      runs: subagentRuns,\n\
  \      mergeOnly: true,\n    });\n    if (restoredCount === 0) {\n      return;\n\
  \    }\n    if (\n      reconcileOrphanedRestoredRuns({\n        runs: subagentRuns,\n\
  \        resumedRuns,\n      })\n    ) {\n      persistSubagentRuns();\n    }\n\
  \    if (subagentRuns.size === 0) {\n      return;\n    }\n    // Resume pending\
  \ work.\n    ensureListener();\n    // Always start sweeper — session-mode runs\
  \ (no archiveAtMs) also need TTL cleanup.\n    startSweeper();\n    for (const runId\
  \ of subagentRuns.keys()) {\n      resumeSubagentRun(runId);\n    }\n\n    // Cold-start\
  \ restore path: queue the same recovery pass that restart\n    // startup also uses\
  \ so resumed children are handled through one seam.\n    scheduleSubagentOrphanRecovery();\n\
  \  } catch {\n    // ignore restore failures\n  }\n}\n```\n"
symptom_type: error-boundary-gap
problem: "`restoreSubagentRunsOnce` (registry.ts:676-714) 가 startup 시 디스크에서\nsubagentRuns\
  \ 를 메모리로 복원한다. 함수 전체가 `try { ... } catch { /* ignore\nrestore failures */ }` 로 감싸여\
  \ 있고, `restoreAttempted = true` 는 try 진입\n**이전** (line 680) 에 set 된다. try 본문은 다음과\
  \ 같이 진행:\n1. `restoreSubagentRunsFromDisk(...)` 가 디스크 → subagentRuns Map 직접 mutate\n\
  \   (state.ts:31 `params.runs.set(runId, entry)`).\n2. restoredCount > 0 이면 reconcileOrphanedRestoredRuns\
  \ → persist → ensureListener\n   → startSweeper → for-loop resumeSubagentRun → scheduleSubagentOrphanRecovery.\n\
  \n이 중 reconcileOrphanedRestoredRuns / ensureListener / startSweeper /\nresumeSubagentRun\
  \ / scheduleSubagentOrphanRecovery 어디서든 throw 가 발생하면\ncatch 가 silent swallow 한다.\
  \ 동시에:\n- subagentRuns Map 에는 디스크에서 복원된 entry 들이 **이미 in-memory 로\n  존재**한다 (step\
  \ 1 에서 mutate 완료).\n- `restoreAttempted=true` 가 set 되어 있어 다음 호출은 line 677-679 early\n\
  \  return.\n- listener / sweeper / resume 중 어느 시점에서 throw 되었느냐에 따라 부분\n  초기화 상태로\
  \ process 가 계속 실행된다 (e.g. entries 는 있는데 listener\n  미부착, sweeper 미가동, resume 미실행).\n"
mechanism: "1. process 시작 시 (또는 첫 query/snapshot 시) `restoreSubagentRunsOnce`\n  \
  \ 호출. line 680 `restoreAttempted = true` 즉시 set.\n2. line 682-685 `restoreSubagentRunsFromDisk({runs:\
  \ subagentRuns, mergeOnly:\n   true})` 호출. state.ts:15-35 의 구현에서 `params.runs.set(runId,\
  \ entry)`\n   로 각 entry 를 in-memory Map 에 push (line 31). throw 안 함 (자체 catch\n\
  \   없으나 호출하는 loadSubagentRegistryFromDisk 가 store.ts:197-208 에서\n   `try { ... }\
  \ catch (error) { ... throw error }` 패턴으로 disk read 실패는\n   re-throw 가능).\n3. 만약\
  \ disk read 실패해 throw 되면 → outer catch silent swallow 발생. 단\n   이 시나리오에서는 `subagentRuns.set(...)`\
  \ 미실행이라 메모리 일관성 손상은\n   없음. 진단 누락만 발생.\n4. **위험한 시나리오**: disk read 성공 → restoredCount\
  \ > 0 → subagentRuns\n   Map 에 N 개 entry 적재 → reconcileOrphanedRestoredRuns 진행 중\
  \ throw\n   (e.g. session store 파일 corrupt 로 resolveSubagentRunOrphanReason 안의\n\
  \   loadSessionStore throw — registry-helpers.ts:157 `loadSessionStore(storePath)`).\n\
  \   이 경우 outer catch 가 swallow 하지만 메모리에는 entry 가 그대로 남는다.\n5. 또는 `ensureListener()`\
  \ 가 onAgentEvent / 의존성 dispatch 중 throw →\n   `listenerStarted = true` 가 line 896\
  \ 에서 *throw 전에* set 되어 있을 수\n   있고, 외곽 catch 가 swallow → listener 미설치 + listenerStarted=true\
  \ →\n   이후 어떤 lifecycle event 도 처리 불가.\n6. 또는 `startSweeper()` 가 throw → sweeper\
  \ 미가동 → TTL cleanup 영원히\n   중단 (FIND-agents-registry-memory-002 의 sweeper self-stop\
  \ 이슈와 더\n   심각한 변종).\n7. 또는 `resumeSubagentRun(runId)` for-loop 중 throw → 일부 runs\
  \ 만 resumed,\n   나머지는 resumed flag set 안 됨 → 일부 runs 가 deferred announce retry\n\
  \   루프를 못 타고 entries 가 stuck.\n8. `restoreAttempted=true` 가 line 680 에서 이미 set 되었으므로\
  \ 후속 호출은\n   line 677 early return. 재시도 path 없음. process 재시작이 유일한 복구.\n9. log /\
  \ metric / counter 어디에도 흔적 없음 (catch 본문이 빈 줄 // ignore).\n"
root_cause_chain:
- why: 왜 부분 초기화 상태로 process 가 진행되는가
  because: 'restoreAttempted=true 가 try 진입 *이전* 에 set 되어 있어 catch 후

    재시도 불가능. catch 본문이 empty 라 진단 신호도 없음.

    '
  evidence_ref: src/agents/subagent-registry.ts:680-712
- why: 왜 try 본문이 atomic 초기화를 가정하는데 부분 실패에 취약한가
  because: 'step 1 (restoreSubagentRunsFromDisk) 이 subagentRuns Map 을 *직접

    mutate* (state.ts:31). 후속 step 들이 실패해도 in-memory state 는

    롤백되지 않는다.

    '
  evidence_ref: src/agents/subagent-registry-state.ts:31
- why: 왜 catch 가 비어 있는가
  because: '코멘트 `// ignore restore failures` (line 712) 가 의도임을 명시. startup

    에서 disk read 실패는 ''best-effort, continue with empty memory'' 로

    취급하려는 design intent. 그러나 partial-write 시나리오를 고려하지 못함.

    '
  evidence_ref: src/agents/subagent-registry.ts:711-713
- why: 왜 후속 health check 가 없는가
  because: '`restoreAttempted` flag 가 단순 boolean 이며, ''restored but not resumed''

    vs ''fully resumed'' 를 구분하지 못함. snapshot 함수 (state.ts:37-58

    getSubagentRunsSnapshotForRead) 도 메모리 상태만 노출.

    '
  evidence_ref: src/agents/subagent-registry-state.ts:37-58
impact_hypothesis: data-loss
impact_detail: "정성: 디스크에 persisted 된 subagent run 이 process 재시작 후 in-memory 로\n복원\
  \ 시작했으나 (subagentRuns.set 진행), 후속 wire-up 실패 (listener,\nsweeper, resume) 가 silent\
  \ swallow 되면 runs 가 *유령 entries* 로 잔존한다.\n외부 관점:\n- subagentRuns.has(runId) ===\
  \ true, get/list 응답에 포함.\n- 그러나 어떤 lifecycle event 도 처리되지 않음 (listener 미설치) /\n\
  \  sweeper 미가동 / resume 미실행.\n- 사용자가 spawn 한 subagent 가 끝나면 announce 가 발사되어도 listener\
  \ 가\n  없으므로 cleanupCompletedAt set 안 됨 → 사용자는 \"completed but no\n  announcement\"\
  \ 상태 영구.\n- 디스크 상 entries 는 sweeper 가 없으므로 archiveAtMs 도달해도 cleanup\n  안 됨 → disk\
  \ 잔류.\n\n재현 시나리오 (정성):\n1. 어제 session 에서 subagentRuns persisted (N=20 entries).\n\
  2. 오늘 시작 → restoreSubagentRunsOnce 진입.\n3. restoreSubagentRunsFromDisk 가 N=20 entry\
  \ 를 메모리에 push.\n4. reconcileOrphanedRestoredRuns 중 session store 가 disk corruption\
  \ 으로\n   loadSessionStore 실패 → throw → outer catch swallow.\n5. listener / sweeper\
  \ / resume 미실행.\n6. 사용자가 새 subagent spawn → registerSubagentRun 이 ensureListener\
  \ 호출\n   → `if (listenerStarted) return` 으로 skip (listenerStarted=true 가 throw\n\
  \   전에 set 되었을 가능성) OR ensureListener 가 정상 진입해 listener 시작.\n   후자 케이스는 OK. 전자 케이스는\
  \ lifecycle event 전혀 처리 안 됨.\n\nCAL-001 정신 — production hot-path 의 활성도: process\
  \ 재시작 시 매번 호출되는\nstartup 경로. 빈도 = process restart 횟수.\n"
severity: P3
counter_evidence:
  path: src/agents/subagent-registry-state.ts
  line: 7-13
  reason: "R-3 Grep 결과:\n1. `rg -n \"try\\s*\\{|catch\\s*\\(|\\.catch\\(\" src/agents/subagent-registry*.ts`:\n\
    \   restoreSubagentRunsOnce 의 outer try (line 681) 외에 본 함수 내 다른\n   catch 없음.\
    \ 부분 실패 복구 경로 없음.\n2. `rg -n \"restoreAttempted\" src/agents/subagent-registry*.ts`:\n\
    \   registry.ts:677, 680 만 사용. flag reset path 없음 (test reset 제외).\n   → 한 번 실패하면\
    \ 재시도 불가.\n3. `rg -n \"// ignore\" src/agents/subagent-registry*.ts`:\n   4개 silent\
    \ catch:\n   - subagent-registry.store.ts:169 (`// ignore migration write failures`)\n\
    \   - subagent-registry-state.ts:11 (`// ignore persistence failures`)\n   - subagent-registry-run-manager.ts:243\
    \ (`// ignore` — 이미 PR #75462 가\n     수정 중, CAL-008 회피)\n   - subagent-registry.ts:712\
    \ (`// ignore restore failures` — 본 FIND)\n4. R-5 (CAL-001) silent catch 의 4-caller\
    \ 검사:\n   restoreSubagentRunsOnce 의 호출자: `rg -n \"restoreSubagentRunsOnce\"\n\
    \   src/agents/`:\n   - registry.ts:676 (정의)\n   - registry.ts:1024 함수 호출 (또는\
    \ export init path) — 확인 필요\n   단일 호출자 (process startup). primary-path inversion\
    \ 없음.\n5. defense-in-depth path:\n   - persistSubagentRunsToDisk (state.ts:7-13)\
    \ 도 silent catch 라\n     \"어차피 다음에 persist 못 했어도 모를 수 있는\" 부분이 있으나, 본 FIND 는\n\
    \     restore 의 부분 초기화에 관한 것으로 독립적.\n   - scheduleSubagentOrphanRecovery (registry.ts:710)\
    \ 가 listener\n     설치되었음을 가정 — listener 미설치 시 orphan recovery 도 부분 효과.\n6. CAL-008\
    \ upstream 검사:\n   - `gh pr list --search subagent restore`: PR #54765 \"sessions:\
    \ fix\n     durable restore after recovery\" (CyberSpencer, OPEN) — durable\n\
    \     restore 관련. 본 FIND 와 같은 axis 인지 PR diff 확인 필요. PR #54765\n     body 가 본\
    \ FIND 의 메커니즘과 정확히 일치하지는 않음 (해당 PR 은\n     recovery hook 의 restore 경로). 그러나 인접\
    \ 영역이므로\n     cross_refs 후보.\n   - PR #75462 (waitForSubagentCompletion silent\
    \ catch) 는 다른 silent\n     catch 경로. 본 FIND 의 restore silent catch 는 미커버.\n"
status: discovered
discovered_by: error-boundary-auditor
discovered_at: 2026-05-14
cross_refs: []
related_tests:
- src/agents/subagent-registry.persistence.test.ts
- src/agents/subagent-registry.persistence.resume.test.ts
---
# restoreSubagentRunsOnce silently swallows all restore failures with no retry

## 문제

`restoreSubagentRunsOnce` (registry.ts:676-714) 는 process startup 시 디스크에서
subagent runs 를 in-memory Map 으로 복원하는 단일 호출 함수다. 함수 전체가
`try { ... } catch { /* ignore restore failures */ }` 로 wrap 되어 있고,
재시도 차단 flag (`restoreAttempted = true`) 는 try 진입 *이전* line 680 에 set
된다.

`subagentRegistryDeps.restoreSubagentRunsFromDisk` (state.ts:15-35) 는
`params.runs.set(runId, entry)` 로 in-memory Map 을 **직접 mutate** 한다. 따라서
이 step 이 성공한 후 후속 step (reconcileOrphanedRestoredRuns / ensureListener /
startSweeper / resumeSubagentRun) 중 어느 것이 throw 해도 memory 에는 entries 가
잔존하지만 wire-up 은 부분 상태로 끝난다. outer catch 는 *완전 silent* 이며
log/metric/counter 어디에도 흔적이 없다.

`restoreAttempted=true` 가 try 진입 *이전* 에 set 되어 있으므로 재호출은 line
677-679 early return 으로 차단 → process 재시작이 유일한 복구 경로.

## 발현 메커니즘

1. process 시작 시 (혹은 첫 query/snapshot 시) `restoreSubagentRunsOnce` 진입.
   line 680 `restoreAttempted = true` set.
2. line 682-685 `restoreSubagentRunsFromDisk({runs: subagentRuns, mergeOnly:
   true})` 호출. state.ts:15-35 의 구현이 in-memory Map 에 N 개 entry 적재.
3. line 689-696 `reconcileOrphanedRestoredRuns(...)` 가 session store 를
   읽어 orphan 분류. registry-helpers.ts:157 `loadSessionStore(storePath)`
   가 disk read 실패로 throw 가능 (corrupt JSON, FS error 등).
4. line 701 `ensureListener()` 가 `listenerStarted = true` 를 line 896 에서
   먼저 set 한 후 `subagentRegistryDeps.onAgentEvent(...)` 호출. DI seam 에서
   throw 가 발생하면 listenerStarted=true 인 상태로 outer catch 까지 escalate.
5. line 703 `startSweeper()` 는 `setInterval(...)` 호출 — 환경 의존성 throw
   가능성 낮으나 0 아님.
6. line 704-706 `resumeSubagentRun(runId)` for-loop. 안에서 `void
   subagentRunManager.waitForSubagentCompletion(...)` 가 sync 단계에서 어떤
   resolve/config 실패로 throw 하면 for-loop 중간에 break + outer catch.
7. 어느 step 에서 throw 됐든 outer catch (line 711-713) silent swallow.
8. 다음 호출자는 line 677-679 early return. 재시도 불가능. process 재시작이
   유일.

핵심 메모리 일관성 문제:
- step 2 가 성공한 후 step 3-7 중 어느 것이 throw 해도 subagentRuns Map 은
  **N 개 entry 보유 상태**.
- listener 미설치 / sweeper 미가동 / resume 미실행.
- 외부 query (state.ts:37-58 getSubagentRunsSnapshotForRead) 는 entries 노출 →
  사용자/디버그 도구는 "runs 존재" 로 보임.
- 실제로는 lifecycle event 처리 불능 / cleanup 불능 / sweeper TTL 적용 불능.

## 근본 원인 분석

1. **restoreAttempted=true 의 위치 (line 680)**: try 진입 이전 set 이라 catch
   후 retry 차단. retry 가능했으면 transient 결함 (e.g. disk 일시적 read 오류)
   에서 회복 가능했을 것.

2. **restoreSubagentRunsFromDisk 가 in-place mutation**: state.ts:31
   `params.runs.set(runId, entry)` 가 호출되는 순간 메모리 일관성이 *부분
   적재* 상태. transactional 형태가 아니다 (e.g. local Map 빌드 후 마지막에
   교체 패턴 아님).

3. **catch 본문 empty + 코멘트 의도**: line 712 `// ignore restore failures`
   가 design intent 를 명시. 'best-effort, continue with whatever loaded' 로
   가정. 그러나 'loaded but not wired up' 시나리오를 분리 인식하지 못함.

4. **state machine 단순성**: restoreAttempted 가 boolean 1 개로
   'idle / attempted-success / attempted-failure / fully-wired' 4 상태를
   구분하지 못함. 'attempted-failure' 와 'attempted-success-with-wireup-failure'
   분리 검출 불가.

5. **logging 부재**: catch 본문 empty + persistSubagentRunsToDisk 도 silent
   (state.ts:7-13). startup 의 진단 신호 누락 stack 전체가 silent.

## 영향

`impact_hypothesis: data-loss` — strict 한 data 손실은 아니지만 *operational
data inconsistency*: persisted entries 가 메모리에 적재됐으나 wire-up 미완.

재현 시나리오 (정성):
1. 어제 session 에서 subagentRuns 가 disk 에 persisted (N=20 entry).
2. 오늘 process 시작 → restoreSubagentRunsOnce 진입.
3. restoreSubagentRunsFromDisk 가 N=20 entry 를 메모리에 push (state.ts:31).
4. reconcileOrphanedRestoredRuns 중 session store 가 corruption 으로
   loadSessionStore 실패 → throw → outer catch swallow.
5. listener / sweeper / resume 미가동.
6. 사용자가 새 subagent spawn → registerSubagentRun 이 ensureListener 호출.
   listenerStarted=true 가 step 4 의 ensureListener 호출 중 throw *전에* set
   되었다면 (registry.ts:896) line 893-895 가 skip → listener 미설치 + flag
   true → 사용자가 spawn 한 새 subagent 의 lifecycle event 도 처리 불가.

영향 frequency: process 재시작 시 매번 가능. 단, throw 원인이 disk corruption /
plugin runtime DI 실패 등으로 정상 환경에서는 드묾. P3 가 적정.

## 반증 탐색

R-3 Grep 결과:
1. `rg -n "try\s*\{|catch\s*\(|\.catch\(" src/agents/subagent-registry*.ts`:
   restoreSubagentRunsOnce 함수 내 다른 catch 없음. 부분 실패 복구 path 없음.
2. `rg -n "restoreAttempted" src/agents/subagent-registry*.ts`:
   registry.ts:677, 680 만 사용. test reset 외 flag clear 경로 없음.
3. `rg -n "// ignore" src/agents/subagent-registry*.ts`:
   4 곳 silent catch. PR #75462 가 run-manager.ts:243 의 // ignore 만 수정
   (CAL-008 회피). 본 FIND 의 registry.ts:712 는 미커버.
4. R-5 4-caller 검사 (silent catch):
   `rg -n "restoreSubagentRunsOnce" src/agents/`:
   - registry.ts:676 (정의)
   - registry.ts (init 또는 첫 호출 — 단일 process startup path).
   primary-path inversion 없음.
5. CAL-008 upstream 검사:
   - `gh pr list --repo openclaw/openclaw --state open --search "subagent
     restore"`: PR #54765 (CyberSpencer, "sessions: fix durable restore after
     recovery") OPEN. body 가 'orphan-prune kill cleanup → 같은 bookkeeping
     경유, delete-mode terminated 멱등' 으로 본 FIND 의 startup 부분 wire-up
     실패와 다른 axis. 직접 충돌 아님.
   - PR #75462, #76332, #68669 모두 다른 영역.
6. defense-in-depth:
   - scheduleSubagentOrphanRecovery (line 710) 가 listener 설치 가정으로 동작.
     listener 미설치 시 orphan recovery 도 부분 효과.
   - sweeper 가 미가동이면 archiveAtMs / SESSION_RUN_TTL_MS 미적용. 단,
     subsequent registerSubagentRun (run-manager.ts:374) 이 startSweeper 를
     다시 호출하므로 새 run 적재 시 sweeper 는 시작될 수 있다 — 단,
     listenerStarted=true 라면 listener 는 영구 미설치.

## Self-check

### 내가 확실한 근거

- registry.ts:676-714 함수 전체를 직접 읽음. try 진입 전 line 680 의
  `restoreAttempted = true` set 확인.
- state.ts:15-35 `restoreSubagentRunsFromDisk` 가 `params.runs.set` 으로
  in-memory Map 을 직접 mutate 함을 확인 (line 31).
- catch 본문이 empty + `// ignore restore failures` 코멘트 확인.
- `ensureListener` (registry.ts:892-896) 가 listenerStarted=true 를 호출 *전*
  set 함을 확인.

### 내가 한 가정

- `reconcileOrphanedRestoredRuns` / `ensureListener` / `startSweeper` 의 실제
  throw 빈도 — disk corruption / DI 실패는 "가능한 경로" 로 보았으나 정량 미측정.
- `loadSessionStore` 가 disk corruption 시 throw 함은 일반적 fs.readFileSync
  + JSON.parse 패턴 가정 (helpers.ts:157 호출 경로의 deep-read 미수행).

### 확인 안 한 것 중 영향 가능성

- `restoreSubagentRunsOnce` 의 호출자 위치를 정확히 못 좁힘 (registry.ts:676
  정의는 확인했으나 외부 호출 진입점은 grep 미실행). 만약 init 외에도 retry
  path 가 있다면 부분 실패 복구 가능성 있음.
- PR #54765 의 diff 가 일부 commit 에서 restore wire-up 을 atomic 화 했을
  가능성 — PR body 만 확인. diff 직접 비교 안 함.
- production telemetry 부재로 실제 재현 빈도 미측정.
