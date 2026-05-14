---
id: FIND-auto-reply-error-boundary-001
cell: auto-reply-error-boundary
title: scheduleFollowupDrain catch+finally 가 max-attempt/backoff 없이 무한 재시도
file: src/auto-reply/reply/queue/drain.ts
line_range: 298-313
evidence: "```ts\n    } catch (err) {\n      queue.lastEnqueuedAt = Date.now();\n\
  \      defaultRuntime.error?.(`followup queue drain failed for ${key}: ${String(err)}`);\n\
  \    } finally {\n      queue.draining = false;\n      if (queue.items.length ===\
  \ 0 && queue.droppedCount === 0) {\n        // Only remove the map entry if it still\
  \ points to this queue instance.\n        // clearSessionQueues can replace the\
  \ entry mid-drain; deleting\n        // unconditionally would orphan the replacement\
  \ queue.\n        if (FOLLOWUP_QUEUES.get(key) === queue) {\n          FOLLOWUP_QUEUES.delete(key);\n\
  \          clearFollowupDrainCallback(key);\n        }\n      } else {\n       \
  \ scheduleFollowupDrain(key, effectiveRunFollowup);\n      }\n```\n"
symptom_type: error-boundary-gap
problem: 'followup queue 의 drain IIFE 가 effectiveRunFollowup 에서 예외를 받으면 catch 가 메시지만
  logger 로 출력하고 finally 가 queue 에 아이템이 남아있는 경우 scheduleFollowupDrain 을 무조건 재호출한다.
  재시도 횟수 상한·exponential backoff·deterministic-failure 감지 회로가 전혀 없어, runFollowup 의
  비-transient 결함 (preflight compaction bug, resolveQueuedReplyExecutionConfig 의 deterministic
  throw 등) 발생 시 같은 키의 같은 아이템에 대해 debounceMs (default 500ms) 간격으로 무한 재시도가 발생한다.

  '
mechanism: '1. drain IIFE (drain.ts:182) 가 while loop 안에서 effectiveRunFollowup 호출.
  drainNextQueueItem (queue-helpers.ts:147-158) 은 `run(next)` 를 await 한 뒤 `items.shift()`
  하므로 throw 시 item 은 queue.items[0] 에 그대로 남는다.

  2. catch (line 298) 가 err 를 잡아 `defaultRuntime.error?.(...)` 로만 출력. stack trace
  는 `String(err)` 로 압축돼 사라진다.

  3. catch 는 `queue.lastEnqueuedAt = Date.now()` 로 lastEnqueuedAt 을 갱신. 다음 drain 의
  waitForQueueDebounce (queue-helpers.ts:111-133) 가 debounceMs 만큼 대기하게 만든다.

  4. finally (line 301-313) 에서 queue.draining=false, items.length>0 이므로 line 312 `scheduleFollowupDrain(key,
  effectiveRunFollowup)` 재호출.

  5. 새 IIFE → beginQueueDrain → 재진입 → waitForQueueDebounce → 다시 effectiveRunFollowup
  → 동일 throw → 같은 catch/finally → 무한 반복.

  6. queue.items 에서 item 이 shift 되지 않으므로 (line 155 의 shift 는 throw 시 unreachable)
  매 시도마다 동일한 페이로드가 그대로 재실행. log 는 매번 같은 error string 으로 출력.

  7. user 메시지 enqueue 가 retry 사이에 들어오면 collect-mode auth-grouping (line 223) 이 매번
  재계산. 이로 인해 batch 구성도 매번 달라질 수 있으나 head 아이템의 throw 가 deterministic 이면 끝없이 재시도.

  '
root_cause_chain:
- why: 왜 drain 의 catch 는 단순 log + finally 재호출 패턴인가?
  because: '현재 설계가 "transient failure 만 가정한 retry" 로, queue.collect.test.ts:716-743
    (''retries collect-mode batches without losing queued items'') 가 보여주는 의도 — 1회
    실패 후 다음 시도에서 성공. 비-transient deterministic 결함은 시나리오에 없으며 코드도 max attempts / backoff
    미배치.

    '
  evidence_ref: src/auto-reply/reply/queue/drain.ts:298-313
- why: 왜 drainNextQueueItem 은 run 이 throw 해도 item 을 pop 하지 않는가?
  because: '"preserve pending items during drains" 정책 (upstream 712644f0d9, splice(0)→splice(0,N)
    패치) 과 동일 논리 — 처리 완료된 item 만 제거. 그러나 이 정책은 "처리 = resolve" 만 다루며 throw 시 escape
    valve (poison-pill counter, dead-letter, drop-after-N) 가 없다.

    '
  evidence_ref: src/utils/queue-helpers.ts:147-158
- why: 왜 effectiveRunFollowup (createFollowupRunner 의 반환) 가 비-transient throw 를 외부로
    흘릴 수 있는가?
  because: 'followup-runner.ts 의 outer try (line 220) 는 finally 만 가지고 catch 가 없다.
    runEmbeddedPiAgent 호출은 inner try (line 258-363) 로 catch+log+return 하지만, 그 앞의 resolveQueuedReplyExecutionConfig
    (line 201), runPreflightCompactionIfNeeded (line 241) 가 throw 하면 outer try 의 finally
    에서 typing.markRunComplete()/markDispatchIdle() 후 그대로 propagate 되어 drain 의 catch
    에 도달한다.

    '
  evidence_ref: src/auto-reply/reply/followup-runner.ts:198-220
- why: 왜 drain 은 error string 만 log 하고 stack trace 를 보존하지 않는가?
  because: '`defaultRuntime.error?.(\`...${String(err)}\`)` 는 Error 인스턴스를 toString
    으로 압축. stack frame, cause, sub-properties 모두 버려져 동일 재시도 N회의 로그가 구분 불가. 재시도 폭주
    diagnostics 가 어렵다.

    '
  evidence_ref: src/auto-reply/reply/queue/drain.ts:300
impact_hypothesis: resource-exhaustion
impact_detail: '정량: debounceMs default 500ms (state.ts:19 DEFAULT_QUEUE_DEBOUNCE_MS)
  이므로 deterministic-fail 키 1개당 약 2 retry/sec. effectiveRunFollowup 진입은 createReplyOperation
  등록·resolveQueuedReplyExecutionConfig 평가·typing 컨트롤러 시작 등 nontrivial side-effect
  동반. 다수 키 동시 실패 시 CPU + log 채널 + 메모리 (replyRunState 의 active set) 누적. log 측면: 24시간
  동안 같은 string 의 error 가 약 17만 회 기록 (= 2 * 60 * 60 * 24). 사용자 visible side-effect
  는 typing indicator 가 들어왔다 나갔다 깜빡거리고 (followup-runner finally 의 markDispatchIdle
  가 매 시도 실행), client adapter (Telegram/Slack) 의 typing 상태가 불안정. 또한 retry 마다 typing
  TTL refresh 가 일어나 typing keepalive 자원이 만성 점유. 비-transient 결함 사례: (a) preflight compaction
  의 unfixable config (e.g. agentDir 경로 권한 문제), (b) execOverrides JSON 직렬화 에러.

  '
severity: P3
counter_evidence:
  path: src/auto-reply/reply/queue.collect.test.ts
  line: 716-806
  reason: 'R-3 Grep:

    - `rg -n "try\\s*\\{|catch\\s*\\(|\\.catch\\(" src/auto-reply/reply/queue/drain.ts`
    — line 183, 298, 301 (try/catch/finally). 외 catch 없음.

    - `rg -n "throw " src/auto-reply/reply/queue/drain.ts` — 0 hits (drain 자체는 throw
    없음, 외부 callback 만 throw).

    - `rg -n "maxAttempts|backoff|circuit|retry" src/auto-reply/reply/queue/` — 0
    hits.

    - `rg -n "scheduleFollowupDrain" src/auto-reply/` — call sites: line 43 (kickFollowupDrainIfIdle),
    line 312 (재시도 self-call), agent-runner.ts:1197/1287 (정상 enqueue 경로). 어떤 caller
    에도 max-attempts 보호 없음.

    - `rg -n "process\\.on\\([''\"](uncaughtException|unhandledRejection)[''\"]" src/`
    — infra/unhandled-rejections.ts 한 군데. drain catch 는 reject 를 swallow 하므로 process-level
    handler 도 fire 안 됨.


    방어 경로 매핑:

    - queue.collect.test.ts:716 ''retries collect-mode batches without losing queued
    items'' — 1회 fail/그 다음 success 만 확인. deterministic-fail 시나리오는 cover 안 됨.

    - queue.collect.test.ts:745 ''retries only the remaining collect auth groups after
    a partial failure'' — 2번째 시도에서 통과, 무한 retry 시나리오 없음.

    - createFollowupRunner inner try/catch (followup-runner.ts:358) 가 runWithModelFallback
    의 throw 만 흡수 — outer try 영역 (resolveQueuedReplyExecutionConfig, runPreflightCompactionIfNeeded)
    는 catch 없음.

    - clearSessionQueues (cleanup.ts) 는 명시적 user reset 으로만 호출, retry storm 중에는 trigger
    없음.


    R-5 execution condition:


    | 경로 | 조건 | 비고 |

    |---|---|---|

    | line 298 catch (err) | conditional-edge | callback throw 시에만 실행 |

    | line 299 lastEnqueuedAt=Date.now() | conditional-edge | 동상 — debounce 재출발 강제
    |

    | line 311-313 scheduleFollowupDrain 재호출 | conditional-edge | items.length>0 또는
    droppedCount>0 시 |

    | line 307 FOLLOWUP_QUEUES.delete | conditional-edge | items empty + droppedCount=0
    |

    | (재시도 상한/poison-pill) | 없음 | unconditional 방어 없음 |


    unconditional 방어 부재 — primary path inversion 없음. transient-failure 만 가정한 retry
    정책은 자주 트리거되지 않을 수 있으나 (그래서 P3), deterministic-fail 셀에서는 영구 폭주 보장.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-05-14'
domain_notes_ref: domain-notes/auto-reply.md
related_tests:
- src/auto-reply/reply/queue.collect.test.ts
- src/auto-reply/reply/queue.drain-restart.test.ts
---
# scheduleFollowupDrain catch + finally 가 max-attempt/backoff 없이 무한 재시도 (debounceMs 간격)

## 문제

`scheduleFollowupDrain` 의 try/catch/finally (drain.ts:182-314) 는 effectiveRunFollowup callback 의 throw 를 catch 에서 string 으로만 log 하고, finally 에서 `queue.items.length > 0` 이면 무조건 `scheduleFollowupDrain` 를 재호출한다. 재시도 상한, exponential backoff, dead-letter, deterministic-failure 감지 회로가 전혀 없다. callback 이 비-transient 결함으로 매번 throw 하면 동일 key, 동일 head item 에 대해 debounceMs (default 500ms) 간격의 무한 재시도가 발생한다.

## 발현 메커니즘

1. drain IIFE 의 while loop 안에서 `effectiveRunFollowup` 가 호출된다 (collect-mode auth group loop line 252, summary path line 276, default path line 294 — 셋 다 await 함).
2. drainNextQueueItem (queue-helpers.ts:151-156) 은 `await run(next)` 한 뒤 `items.shift()` 한다. throw 발생 시 shift 가 unreachable → queue.items[0] 에 원본 아이템 그대로.
3. catch (line 298) 가 err 를 잡아 `queue.lastEnqueuedAt = Date.now()` 로 debounce 시계를 재출발하고 `defaultRuntime.error?.(\`followup queue drain failed for ${key}: ${String(err)}\`)` 로 한 줄 log.
4. finally (line 301-313): `queue.draining = false` → items.length>0 이므로 line 312 `scheduleFollowupDrain(key, effectiveRunFollowup)` 재호출.
5. 재호출이 beginQueueDrain 로 재진입 → waitForQueueDebounce 가 debounceMs 만큼 대기 → 같은 callback 재시도 → 같은 throw → 같은 catch → 같은 finally → 같은 재호출. 무한 microtask loop.
6. Stack 은 IIFE 마다 새로 시작되므로 stack overflow 는 없음. 그러나 CPU/IO/log 채널 부하는 누적.
7. 사용자 visible side-effect: createFollowupRunner 의 finally (followup-runner.ts:469-480) 가 매 시도마다 `typing.markRunComplete()` + `typing.markDispatchIdle()` 호출 — typing keepalive loop 가 매번 reset → adapter (Telegram/Slack) 에 typing ping 폭주.

## 근본 원인 분석

drain.ts 는 "queue 처리 도중 throw 가 발생해도 사용자 메시지를 잃지 않는다" 는 정책에 충실하게 구현됐다 (queue.collect.test.ts:716 'retries collect-mode batches without losing queued items' 가 그 의도를 명시). 그러나 이 정책은 "다음 시도에 성공한다" 는 transient assumption 위에 서 있다. deterministic-fail 시나리오 (e.g. preflight compaction 의 fs 권한 문제, sessionFile 경로의 ENOENT, runtime config 의 invalid JSON schema 가 매번 throw) 에서는 다음 시도에서도 똑같이 throw 하므로 retry 정책이 무한 재실행으로 폭주한다.

effectiveRunFollowup (createFollowupRunner 의 반환값) 는 inner try/catch (followup-runner.ts:258-363) 로 runWithModelFallback 결과만 보호한다. 그 앞 단계 (resolveQueuedReplyExecutionConfig: line 201, runPreflightCompactionIfNeeded: line 241) 는 outer try 의 finally 에 둘러싸여 있지만 catch 가 없어 throw 가 그대로 propagate 한다. 그 결과 drain.ts 의 catch 에 도달하고, 위의 재시도 loop 가 작동한다.

추가로 catch 가 `String(err)` 로 stack 을 압축 — 동일 재시도 N회의 로그가 시간 stamp 외에는 분간 불가. retry storm 의 diagnostics 가 어렵다.

## 영향

- 영향 유형: resource-exhaustion (log 채널 폭주, CPU, replyRunState/typing keepalive 의 활성 자원 누적).
- 정량:
  - DEFAULT_QUEUE_DEBOUNCE_MS = 500 (queue/state.ts:19) → 키당 약 2 retry/sec.
  - 24 시간 = 약 172,800 회 동일 error log.
  - effectiveRunFollowup 진입 1회당 createReplyOperation 등록 (reply-run-registry.ts), typing.markRunComplete (typing.ts), markDispatchIdle 호출. typing keepalive 의 setTimeout 이 매 retry 마다 reschedule → adapter typing 표시가 무한히 깜빡임 (Telegram/Slack hot path).
- 재현 조건:
  - preflight compaction 의 fs.read 가 ENOENT/EACCES 로 매번 throw (e.g. sessionFile 가 외부 process 에 의해 잠긴 상태).
  - resolveQueuedReplyExecutionConfig 가 invalid configuration 로 throw (e.g. provider plugin 의 throwing resolver).
  - runtimeConfig 직렬화 단계에서 JSON cycle 등으로 RangeError.
- 심각도: P3 (borderline). 비-transient 결함이 트리거되어야만 발현하므로 평상시 드물지만, 한 번 트리거되면 운영 노이즈는 매우 큼.

## 반증 탐색

R-3 Grep:
- `rg -n "try\s*\{|catch\s*\(|\.catch\(" src/auto-reply/reply/queue/drain.ts` — line 183 (try), 298 (catch), 301 (finally), 그 외 0. user callback 결과를 catch 하는 곳은 line 298 한 곳뿐.
- `rg -n "throw " src/auto-reply/reply/queue/drain.ts` — drain 자신은 throw 없음.
- `rg -n "maxAttempts|backoff|circuit|retry" src/auto-reply/reply/queue/` — 0 hits. retry 상한 / backoff 변수 없음.
- `rg -n "scheduleFollowupDrain" src/auto-reply/` — call sites: drain.ts:43 (kickFollowupDrainIfIdle), drain.ts:312 (self re-schedule), agent-runner.ts:1197/1287 (정상 enqueue). 어떤 caller 에도 attempt counter 없음.
- `rg -n "JSON\.parse" src/auto-reply` — drain.ts 영역엔 JSON.parse 없음 (관련 없음).
- `rg -n "process\.on\(['\"](uncaughtException|unhandledRejection)['\"]" src/` — infra/unhandled-rejections.ts:345. drain catch 가 reject 를 swallow 하므로 process handler 까지 도달하지 못함.

방어 경로 / 기존 테스트:
- queue.collect.test.ts:716 'retries collect-mode batches without losing queued items' — `attempt === 1` 일 때만 throw, 다음에 성공. 무한 retry 없음.
- queue.collect.test.ts:745 'retries only the remaining collect auth groups after a partial failure' — 동일하게 1회 throw 후 회복.
- queue.dedupe.test.ts — dedupe 시나리오만, retry 폭주 미검증.
- followup-runner.ts:358-363 inner catch — runWithModelFallback 결과만 보호. preflight/config 단계 미보호.
- clearSessionQueues (cleanup.ts) — user 가 /reset 등으로 명시 cleanup 해야 발동. 자동 escape valve 아님.

R-5 execution condition:

| 경로 | 조건 | 비고 |
|---|---|---|
| line 298 catch (err) | conditional-edge | effectiveRunFollowup throw 시 |
| line 299 lastEnqueuedAt 갱신 | conditional-edge | catch 진입 시 |
| line 312 scheduleFollowupDrain self-recurse | conditional-edge | items.length>0 || droppedCount>0 시 |
| line 307-309 delete + clearFollowupDrainCallback | conditional-edge | items empty + droppedCount=0 시 |
| (max-attempts / backoff / dead-letter) | 없음 | unconditional 방어 부재 |

unconditional 방어 부재 — primary-path inversion 없음. 따라서 P3 (borderline) 유지.

## Self-check

### 내가 확실한 근거
- drain.ts:298-313 의 catch + finally 가 max-attempts/backoff 없이 self re-schedule 함은 Read 로 확인.
- drainNextQueueItem (queue-helpers.ts:151-156) 의 `await run(next)` → `items.shift()` 순서로 throw 시 item 잔존 — Read 확인.
- DEFAULT_QUEUE_DEBOUNCE_MS = 500ms (state.ts:19) — Read 확인.
- queue.collect.test.ts:716 의 transient-only retry assumption — Read 확인.
- followup-runner.ts:201, 241 의 outer try 영역에 catch 부재 — Read 확인.
- followup-runner.ts:469-480 의 finally 가 typing keepalive reset — Read 확인.

### 내가 한 가정
- 비-transient 결함 (preflight compaction 의 fs 권한 etc.) 가 실제로 production 에서 deterministic 하게 발생할 수 있는 빈도. retry storm 시나리오 자체는 시연 가능하지만, 운영 빈도는 정량 없음. 따라서 severity 를 P2 가 아닌 P3 로 책정.
- typing keepalive reset 이 visible 한 사용자 영향을 주는 정도. Telegram bot API 는 5 초당 1회 typing request rate-limit 가 있으므로 hard fail 가능성 — 정확한 adapter 동작은 채널마다 다를 수 있음.

### 확인 안 한 것 중 영향 가능성
- replyRunState 의 active set (reply-run-registry.ts) 가 retry 마다 register/clear 를 반복하면서 race 가 발생하는지 — concurrency 축 audit 대상.
- 동일 session 의 여러 key 가 동시에 retry storm 에 빠질 때 FOLLOWUP_RUN_CALLBACKS 의 Map 크기 자체는 증가하지 않음 (key 당 1 entry) — 정상.
- `defaultRuntime.error?.` 의 sink 가 disk file 인 경우 disk 폭주 가능성. logger sink configurable.
- queue.drain-restart.test.ts 가 deterministic-fail retry 를 covered 하는지 — filename 기반으로는 drain restart 정상 경로 확인 위주로 추정. 미검증.
