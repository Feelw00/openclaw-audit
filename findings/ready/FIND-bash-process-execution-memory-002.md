---
id: FIND-bash-process-execution-memory-002
cell: bash-process-execution-memory
title: finishedSessions Map 이 size cap 없이 TTL prune + sweeper 생존에만 의존
file: src/agents/bash-process-registry.ts
line_range: 322-337
evidence: "```ts\nfunction pruneFinishedSessions() {\n  const cutoff = Date.now()\
  \ - jobTtlMs;\n  for (const [id, session] of finishedSessions.entries()) {\n   \
  \ if (session.endedAt < cutoff) {\n      finishedSessions.delete(id);\n    }\n \
  \ }\n}\n\nfunction startSweeper() {\n  if (sweeper) {\n    return;\n  }\n  sweeper\
  \ = setInterval(pruneFinishedSessions, Math.max(30_000, jobTtlMs / 6));\n  sweeper.unref?.();\n\
  }\n```\n"
symptom_type: memory-leak
problem: 모듈 전역 finishedSessions Map 은 backgrounded 세션이 종료될 때마다 set 되며, 자동 제거 경로는 TTL
  기반 pruneFinishedSessions 뿐이다. 개수 상한(size cap)이 없어 TTL(기본 30분, 최대 3시간) 윈도 안에서는 무제한으로
  쌓인다. 게다가 prune 은 setInterval sweeper 가 살아있을 때만 동작하므로, sweeper 가 한 번도 시작되지 않거나 중지된
  상태에서는 TTL 만료 항목도 제거되지 않는다.
mechanism: '1. backgrounded 세션이 종료되면 moveToFinished 가 finishedSessions.set 으로 항목을
  추가한다 (bash-process-registry.ts:225). 각 항목은 aggregated 출력 문자열 + tail(최대 2000) 을 보유.

  2. 자동 제거는 pruneFinishedSessions 의 TTL 비교(endedAt < now - jobTtlMs) 뿐 (322-329).
  개수 기반 cap / LRU eviction 없음.

  3. prune 은 sweeper(setInterval) 콜백으로만 호출된다 (335). sweeper 는 addSession 시 startSweeper
  로 시작되고 unref 된다.

  4. jobTtlMs 가 기본 30분(최대 3시간)이므로, 그 윈도 안에서 background exec 를 다수 띄우는 워크로드는 finishedSessions
  가 그 수만큼 누적된 채 유지된다 — cap 부재이므로 단위 시간당 background-exec 빈도 × TTL 만큼 동시 보유.

  5. sweeper 의존성: prune 은 sweeper 생존 시에만 실행. setJobTtlMs 가 stopSweeper 후 startSweeper
  를 호출하지 않는 경로나, 프로덕션에서 sweeper 가 시작 안 된 상황(addSession 미경유 set 은 없으나, unref 된 interval
  이 다른 활성 핸들 부재로 묶여있다 가정)에 의존.

  '
root_cause_chain:
- why: 왜 finishedSessions 가 무제한으로 커질 수 있는가
  because: set(225) 에 대응하는 자동 제거가 TTL prune(326) 하나뿐이고 개수 상한(cap/LRU)이 없다. TTL 윈도
    안에서는 추가만 일어난다.
  evidence_ref: src/agents/bash-process-registry.ts:225
- why: 왜 TTL prune 만으로 부족한가
  because: jobTtlMs 기본 30분(MAX 3시간, 9-10행)이라, 고빈도 background-exec 워크로드는 만료 전 항목이 TTL
    × 빈도 만큼 동시에 살아있는다. 출력(aggregated)을 포함하므로 항목당 메모리가 작지 않다.
  evidence_ref: src/agents/bash-process-registry.ts:323
- why: 왜 prune 이 항상 실행된다고 보장 못 하는가
  because: prune 은 sweeper(setInterval) 콜백으로만 트리거된다(335). sweeper 가 없으면(stopSweeper
    후 재시작 누락 등) 만료 항목도 영구 잔존한다 — finishedSessions 자체엔 lazy prune(get/list 시 정리) 경로가
    없다.
  evidence_ref: src/agents/bash-process-registry.ts:335
- why: 왜 process clear/remove 로 정리되는 것에 의존 못 하는가
  because: deleteSession 호출은 에이전트가 명시적으로 process clear/remove 액션을 발행할 때만 일어나는 conditional
    경로다. 정상 흐름에서 자동 호출되지 않으며, clearFinished() 는 export 됐지만 프로덕션 호출자가 없다.
  evidence_ref: src/agents/bash-tools.process.ts:664
impact_hypothesis: memory-growth
impact_detail: '정량: finishedSessions 항목 1개당 FinishedSession(aggregated 출력 문자열 + tail
  최대 2000자 + 메타) 보유. cap 부재이므로 보유량 ≈ (단위시간당 background-exec 종료 빈도) × (jobTtlMs, 기본
  30분). 예: 분당 background exec 10개 종료 시 TTL 30분 윈도에 약 300개 항목 상주. sweeper 가 중지/미시작이면
  TTL 만료분도 제거되지 않아 run 수명 내내 단조 증가 → 사실상 무제한.

  정성: 일반적으로는 TTL+sweeper 가 bound 를 제공하나, 고빈도 background exec + 큰 출력 조합, 또는 sweeper
  부재 시 memory baseline 상승.

  '
severity: P2
counter_evidence:
  path: src/agents/bash-process-registry.ts
  line: '322'
  reason: 'R-3 Grep 수행:

    - `rg -n "finishedSessions\.(delete|clear)" registry.ts` → delete: 126(deleteSession),
    326(prune); clear: 304(clearFinished), 309(resetProcessRegistryForTests).

    - `rg -n "(cap|max|limit|size)" registry.ts | rg finishedSessions` → match 없음
    (size cap / LRU 부재 확인).

    - `rg -n "while" registry.ts` → finishedSessions.size 기반 eviction 루프 없음.

    - `rg -rn "clearFinished" src/ | grep -v test` → 정의(304)만, 프로덕션 호출자 없음.

    실행 조건 분류(R-5):

    | 경로 | 조건 |

    |---|---|

    | pruneFinishedSessions TTL delete (326) | conditional-edge — sweeper 생존 + endedAt
    < cutoff 일 때만 |

    | deleteSession (126) | conditional-edge — process clear/remove 액션 발행 시에만 (bash-tools.process.ts:664/687/696/716)
    |

    | clearFinished (304) | dead — 프로덕션 호출자 없음 |

    | resetProcessRegistryForTests (309) | test-only |

    unconditional eviction 경로 없음. TTL prune 이 부분적 bound 를 주지만 (a) size cap 부재로 TTL
    윈도 내 무제한, (b) sweeper 의존이라 sweeper 부재 시 무력화. → leak 성립(단 TTL+sweeper 정상 시 bound
    존재하므로 P2).

    '
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-05-29'
cross_refs:
- FIND-bash-process-execution-memory-001
domain_notes_ref: domain-notes/bash-process-execution.md
related_tests:
- src/agents/bash-process-registry.test.ts
---
# finishedSessions Map 이 size cap 없이 TTL prune + sweeper 생존에만 의존

## 문제

모듈 전역 `finishedSessions` Map (bash-process-registry.ts:99) 은 backgrounded
세션이 종료될 때마다 `moveToFinished` 에서 set 된다(225). 항목 자동 제거는
`pruneFinishedSessions` 의 TTL 비교(endedAt < now - jobTtlMs) 단 하나이며,
개수 상한(size cap)이나 LRU eviction 이 없다. 그래서 TTL(기본 30분, 최대 3시간)
윈도 안에서는 background exec 종료가 일어날 때마다 항목이 무제한으로 쌓인다.
더해서 prune 은 `setInterval` sweeper 콜백으로만 실행되므로(335), sweeper 가
중지/미시작이면 TTL 만료 항목조차 제거되지 않는다.

## 발현 메커니즘

1. backgrounded 세션 종료 → `moveToFinished` 가 `finishedSessions.set(...)`
   (225). 각 항목은 aggregated 출력 + tail(최대 2000) + 메타 보유.
2. 자동 제거는 `pruneFinishedSessions` 의 TTL delete(326) 뿐. 개수 cap / LRU 없음.
3. prune 은 sweeper(`setInterval(pruneFinishedSessions, max(30s, ttl/6))`, 335)
   콜백으로만 호출. sweeper 는 addSession 의 startSweeper 로 시작되고 unref 됨.
4. jobTtlMs 기본 30분(MIN 1분 / MAX 3시간, 8-10행) → 그 윈도 안에서 background
   exec 를 다수 띄우면 finishedSessions 가 그 수만큼 동시 상주.
5. sweeper 가 없는 상태(stopSweeper 후 재시작 누락, 또는 unref interval 이 다른
   활성 핸들 부재로 정리됨)에서는 TTL 만료분도 prune 되지 않아 단조 증가.

## 근본 원인 분석

- 1단계: set(225) 에 대응하는 자동 제거가 TTL prune(326) 하나뿐이고 size cap 이
  없다 → TTL 윈도 내 무제한 추가.
- 2단계: jobTtlMs 기본 30분/최대 3시간(323)이라, 고빈도 background-exec 는 만료
  전 항목이 TTL × 빈도 만큼 동시 생존. aggregated 출력 포함이라 항목당 메모리
  비무시.
- 3단계: prune 이 sweeper 생존에만 의존(335). lazy prune(get/list 시 정리)
  경로가 없어 sweeper 부재 시 만료분도 영구 잔존.
- 4단계: deleteSession(126) 은 process clear/remove 액션 발행 시에만 호출되는
  conditional 경로이고(bash-tools.process.ts:664), clearFinished(304) 는
  프로덕션 호출자가 없다 → 명시적 정리에 의존 불가.

## 영향

impact_hypothesis: memory-growth.

재현 시나리오: agent 가 background 모드 exec(yieldWindow 경과로 backgrounded)를
고빈도로 종료시키는 워크로드. cap 부재이므로 동시 상주량 ≈ (분당 background-exec
종료수) × (jobTtlMs). 예) 분당 10개 종료, TTL 30분 → 약 300개 FinishedSession
상주, 각 항목이 aggregated 출력 보유. sweeper 가 중지/미시작이면 TTL 만료분도
제거되지 않아 run/process 수명 내내 단조 증가 → 사실상 무제한 성장.

## 반증 탐색

탐색 카테고리:
1. 이미 cleanup 있는지: `rg -n "finishedSessions\.(delete|clear)"
   bash-process-registry.ts` → TTL prune(326), deleteSession(126),
   clearFinished(304), test reset(309). TTL prune 외 자동 unconditional 제거
   없음.
2. size cap / LRU: `rg -n "(cap|max|limit|size)" registry.ts | rg
   finishedSessions` → match 없음. while-size eviction 루프도 없음. cap 부재
   확정.
3. 명시 정리 의존성: deleteSession 은 process clear/remove 액션
   (bash-tools.process.ts:664/687/696/716) conditional, clearFinished 는
   `rg -rn "clearFinished" src/ | grep -v test` 결과 정의(304)만 — 프로덕션
   호출자 없음.
4. sweeper 보호: sweeper 는 unref 되어 프로세스 종료를 막진 않지만, 동시에 다른
   활성 핸들이 없으면 interval 자체가 유지되지 않을 수 있어 prune 비실행 가능성.
   결론: TTL+sweeper 정상 시 bound 가 있으나 cap 부재 + sweeper 의존이라 P2.

## Self-check

### 내가 확실한 근거
- src/agents/bash-process-registry.ts:225 — moveToFinished 의 finishedSessions.set.
- src/agents/bash-process-registry.ts:322-329 — TTL prune 만이 자동 제거.
- src/agents/bash-process-registry.ts:335 — prune 이 sweeper 콜백에만 묶임.
- size cap / LRU 부재: `rg "(cap|max|limit|size)" | rg finishedSessions` match 없음.
- src/agents/bash-tools.process.ts:664 — deleteSession 은 clear 액션 conditional.

### 내가 한 가정
- 고빈도 background-exec 워크로드(분 단위 다수 종료)가 실제로 발생한다고 가정.
  저빈도면 TTL prune 으로 충분히 bound 됨.
- sweeper(unref interval)가 특정 상황에서 비활성/미시작일 수 있다는 것은
  방어적 가정이며, 정상 기동(addSession → startSweeper) 시에는 살아있다.

### 확인 안 한 것 중 영향 가능성
- 실제 프로덕션에서 background exec 종료 빈도가 얼마나 높은지 미측정 — 빈도가
  낮으면 P2 도 과대평가일 수 있음.
- runningSessions Map 은 moveToFinished(189)/deleteSession(125) 에서 매 exit 마다
  unconditional delete 되므로 leak 아님(별도 FIND 안 함). finishedSessions 만
  대상.
