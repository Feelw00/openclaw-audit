---
id: FIND-bash-process-execution-memory-001
cell: bash-process-execution-memory
title: exec abort 리스너가 per-run AbortSignal 에 정상종료 시 제거 안 되어 누적
file: src/agents/bash-tools.exec.ts
line_range: 1656-1660
evidence: "```ts\n    if (signal?.aborted) {\n      onAbortSignal();\n    } else if\
  \ (signal) {\n      signal.addEventListener(\"abort\", onAbortSignal, { once: true\
  \ });\n    }\n```\n"
symptom_type: memory-leak
problem: 하나의 agent run 안에서 exec tool 을 N 번 호출하면 동일한 per-run AbortSignal 에 onAbortSignal
  리스너가 N 개 누적된다. 각 리스너 클로저는 자신의 run/session 객체(출력 버퍼 aggregated/tail, child 참조 포함)를
  abort 이벤트가 실제로 발생하기 전까지 강하게 붙잡아 둔다.
mechanism: '1. runEmbeddedAttempt 가 run 당 AbortController 를 1개 생성 (attempt.ts:712),
  그 signal 을 그 run 의 모든 tool.execute 에 전달 (attempt.ts:3003 `toolParams.signal ?? runAbortController.signal`).

  2. exec tool 의 execute() 가 매 호출마다 `signal.addEventListener("abort", onAbortSignal,
  { once: true })` 로 리스너 등록 (bash-tools.exec.ts:1659).

  3. `{ once: true }` 는 abort 이벤트가 실제로 fire 될 때만 리스너를 자동 제거한다. 정상 종료(run.promise resolve)·실패(reject)·yield(backgrounded)
  경로 어디서도 removeEventListener 를 호출하지 않는다.

  4. 따라서 abort 가 없는 정상 흐름에서는 exec 호출 1건당 리스너 1개가 signal 에 영구 잔존.

  5. onAbortSignal 클로저는 run 을 캡처(run.disableUpdates / run.session / run.kill, exec.ts:1649-1653)하므로,
  완료된 exec 의 session(aggregated/tail 출력 문자열 + child 참조)이 run signal 의 GC 시점까지 해제되지
  않는다.

  6. run signal 은 run 전체 수명 동안 살아있다 → 한 run 에서 exec 를 많이 돌리는 에이전트(예: 빌드/테스트 루프, cron
  반복 작업)는 run 종료 전까지 리스너 + 완료 session 그래프를 선형 누적.

  '
root_cause_chain:
- why: 왜 리스너가 정상 종료 시 남는가
  because: '`{ once: true }` 는 이벤트 발생 시에만 제거를 보장하는데, 정상 종료/yield 경로에는 removeEventListener(onAbortSignal)
    호출이 전혀 없다.'
  evidence_ref: src/agents/bash-tools.exec.ts:1659
- why: 왜 리스너 누적이 같은 signal 위에서 일어나는가 (per-call 이 아니라)
  because: signal 은 run 단위로 1회 생성된 runAbortController.signal 이고, 같은 run 의 모든 tool.execute
    가 이 동일 signal 을 공유한다.
  evidence_ref: src/agents/embedded-agent-runner/run/attempt.ts:712
- why: 왜 메모리가 리스너 개수 이상으로 커지는가
  because: onAbortSignal 클로저가 run 객체(run.session 의 aggregated/tail 출력 버퍼, child 참조
    포함)를 강하게 캡처하여, 완료된 exec 의 session 그래프가 리스너와 함께 retained 된다.
  evidence_ref: src/agents/bash-tools.exec.ts:1649
- why: 왜 abort 경로의 once 자동제거에 의존하면 안 되는가
  because: 정상 종료가 abort 보다 압도적으로 흔한 hot-path 이며, 이 경로에서는 abort 이벤트 자체가 발생하지 않아 once
    가 영영 트리거되지 않는다.
  evidence_ref: src/agents/embedded-agent-runner/run/attempt.ts:3003
impact_hypothesis: memory-growth
impact_detail: '정량: exec 호출 1건당 AbortSignal 리스너 1개 + run/session 그래프 1개(session.aggregated
  는 maxOutputChars 까지, 기본 수만 char) retained. 한 run 에서 exec 를 K 번 호출하면 리스너 K 개 + 완료
  session K 개가 run 종료까지 누적. 장수명 run(cron 반복, 대화형 에이전트의 다중 빌드/테스트 루프)에서 선형 증가. abort
  가 발생하면 한 번에 정리되지만, abort 없는 정상 종료가 hot-path 이므로 누적이 기본 동작.

  정성: run 단위로만 해제되므로 OOM 보다는 long-running run 의 메모리 baseline 상승 + AbortSignal 의 리스너
  배열 성장(EventTarget 내부 배열).

  '
severity: P2
counter_evidence:
  path: src/agents/bash-tools.exec.ts
  line: '1659'
  reason: 'R-3 Grep 수행:

    - `rg -n "removeEventListener|removeListener|\.off\(|onAbortSignal" src/agents/bash-tools.exec.ts`
    → onAbortSignal 의 add(1659) 와 즉시호출(1657)·정의(1641) 만 match. removeEventListener
    호출 0건 (match 없음).

    실행 조건 분류(R-5):

    | 경로 | 조건 |

    |---|---|

    | `{ once: true }` 자동 제거 (abort fire 시) | conditional-edge — abort 발생 시에만, 정상
    hot-path 에서는 미실행 |

    | 정상 종료 removeEventListener | 부재 — 코드 없음 |

    unconditional eviction 경로 없음 → leak 성립. 단 signal 이 per-tool-call 이면 호출 종료 시 GC
    되어 무해할 것이나, attempt.ts:712/3003 확인 결과 per-run 공유 signal 이라 누적된다.

    '
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-05-29'
cross_refs:
- FIND-bash-process-execution-memory-002
domain_notes_ref: domain-notes/bash-process-execution.md
related_tests:
- src/agents/bash-tools.exec.background-abort.test.ts
---
# exec abort 리스너가 per-run AbortSignal 에 정상종료 시 제거 안 되어 누적

## 문제

`createExecTool` 의 execute() 는 매 호출마다 전달받은 AbortSignal 에
`onAbortSignal` 리스너를 `{ once: true }` 로 등록한다 (bash-tools.exec.ts:1659).
`once` 는 abort 이벤트가 실제로 발생할 때만 리스너를 제거한다. 그러나 exec 의
정상 종료(run.promise resolve), 실패(reject), yield(backgrounded) 경로 어디에도
`removeEventListener(onAbortSignal)` 호출이 없다. 이 signal 은 per-tool-call 이
아니라 agent run 전체에서 공유되는 `runAbortController.signal` 이므로, 한 run
안에서 exec 를 여러 번 호출하면 동일 signal 위에 리스너가 선형 누적된다.

## 발현 메커니즘

1. `runEmbeddedAttempt` 가 run 당 `AbortController` 를 1개 생성 (attempt.ts:712).
2. 같은 run 의 모든 tool 실행에 `toolParams.signal ?? runAbortController.signal`
   을 넘긴다 (attempt.ts:3003). exec tool 도 이 공유 signal 을 받는다.
3. exec execute() 가 호출마다 `signal.addEventListener("abort", onAbortSignal,
   { once: true })` 등록 (1659).
4. exec 가 정상 종료/실패/yield 로 끝나면 `onAbortSignal` 은 호출되지 않으므로
   `{ once: true }` 의 자동 제거도 트리거되지 않는다. 명시적 removeEventListener
   도 없다.
5. `onAbortSignal` 클로저는 `run` 을 캡처한다(`run.disableUpdates()`,
   `run.session.backgrounded`, `run.kill()`, 1649-1653). 따라서 완료된 exec 의
   `run.session`(aggregated/tail 출력 버퍼 + child 참조)이 signal 의 GC 전까지
   retained.
6. run signal 은 run 전체 수명 동안 살아있으므로, exec 를 K 번 호출하는 run 은
   리스너 K 개 + 완료 session K 개를 run 종료까지 보유한다.

## 근본 원인 분석

- 1단계(표면): `{ once: true }` 는 abort 발생 시점에만 제거를 보장하고, 정상
  종료 경로에는 cleanup 코드가 없다 (bash-tools.exec.ts:1659).
- 2단계: 누적이 발생하는 이유는 signal 이 호출 단위가 아니라 run 단위로 공유되기
  때문 (attempt.ts:712 에서 run 당 1회 생성, 3003 에서 모든 tool.execute 에 전달).
- 3단계: 메모리가 리스너 카운트 이상으로 커지는 이유는 클로저가 run/session
  그래프(출력 문자열 포함)를 강하게 잡기 때문 (exec.ts:1649).
- 4단계: abort 경로의 once 자동제거에 의존이 안전하지 않은 이유는 정상 종료가
  hot-path 이고 그 경로에서는 abort 이벤트가 발생하지 않기 때문 (attempt.ts:3003).

## 영향

impact_hypothesis: memory-growth.

재현 시나리오: 한 agent run 안에서 exec tool 을 반복 호출(예: 빌드 → 테스트 →
린트 루프, 또는 cron 으로 반복되는 다단계 명령). 각 호출이 공유 run signal 에
리스너 1개 + 완료된 run/session 그래프 1개를 남긴다. abort 가 한 번도 발생하지
않으면(정상 흐름) run 이 끝날 때까지 해제되지 않는다. session.aggregated 는
maxOutputChars 까지 커질 수 있어, 출력이 많은 명령을 다수 실행하는 long-running
run 의 메모리 baseline 이 누적 상승한다. abort 발생 시 once 들이 한꺼번에 정리
되긴 하지만, 그것은 비정상 경로다.

## 반증 탐색

탐색 카테고리:
1. 이미 cleanup 있는지: `rg -n "removeEventListener|removeListener|\.off\(|
   onAbortSignal" src/agents/bash-tools.exec.ts` → removeEventListener 호출
   match 없음. 정상 종료 경로(1710-1734)에는 yieldTimer 의 clearTimeout 만 있고
   abort 리스너 정리는 없음.
2. 외부 경계 장치(signal 수명): attempt.ts:712 에서 signal 은 run 당 1개,
   3003 에서 모든 tool.execute 가 공유 → tool-call 단위 GC 로 자연 해제되지
   않음. 이것이 누수의 핵심 조건.
3. 호출 빈도: exec 는 에이전트 핵심 도구로 빈번히 호출됨. 단 single-exec run 은
   영향이 미미(리스너 1개)하여 severity 를 P2 로 책정.

## Self-check

### 내가 확실한 근거
- src/agents/bash-tools.exec.ts:1659 — `{ once: true }` 등록, removeEventListener 부재.
- src/agents/bash-tools.exec.ts:1649-1653 — onAbortSignal 클로저가 run 캡처.
- src/agents/embedded-agent-runner/run/attempt.ts:712 — run 당 AbortController 1개.
- src/agents/embedded-agent-runner/run/attempt.ts:3003 — 공유 signal 전달.

### 내가 한 가정
- 한 run 이 exec 를 여러 번 호출하는 워크로드가 실제로 존재한다고 가정(빌드/
  테스트 루프, cron 다단계). single-exec run 이라면 영향 미미.
- AbortSignal(EventTarget) 은 리스너를 내부 배열로 보유하며 signal GC 전까지
  유지된다는 Node 동작 가정.

### 확인 안 한 것 중 영향 가능성
- 일부 tool dispatch 경로가 `toolParams.signal`(per-call 일 수 있음)을 넘기는지
  여부 — 3003 의 `?? runAbortController.signal` fallback 기준으로는 공유 signal
  이지만, toolParams.signal 이 항상 채워지는 경로가 있다면 그 경로는 누수 아님.
  hot-path(exec 일반 호출)에서 toolParams.signal 출처는 본 셀 범위 밖이라 미확정.
