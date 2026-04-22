---
candidate_id: CAND-019
type: single
finding_ids:
  - FIND-channels-error-boundary-001
cluster_rationale: |
  단독 FIND. 같은 셀(channels-error-boundary)의 FIND-002 와 같은 "floating
  promise" 상위 테마를 공유하지만 R-5 execution-condition / root cause axis /
  fix 축이 다르다.

  FIND-001 (draft-stream-loop.ts) R-5 분류:
    "conditional-edge + primary hot-path" — line 60 (setTimeout void flush) /
    line 75 (throttle 통과 void flush) 에서 `.catch` 자체 부재. 추가로 flush
    내부 `pendingText = ""` 가 send 이전에 수행되어 reject 경로에서 복구
    불가 → error-boundary 뿐 아니라 **데이터 유실** 성격 동반.

  FIND-002 (ack-reactions.ts) R-5 분류:
    "conditional-edge + secondary visual" — `.then(onFulfilled)` 만 있고
    onRejected 부재. onError 파라미터는 remove() 전용으로 오배선 → stale
    emoji + unhandled rejection. 데이터 유실 없음.

  공통은 "fire-and-forget 에 onRejected 누락" 라는 상위 관찰뿐. fix 축은:
    FIND-001 → `void flush()` → `void flush().catch(onError)` + `pendingText`
               복구 전략 재설계 (retry-safe). multi-step 변경.
    FIND-002 → `.then(onFulfilled)` → `.then(onFulfilled, onRejected)` 또는
               `ackReactionPromise.catch(...)` 선-부착 계약화. single-line 변경.

  severity 도 P2 vs P3 로 다름. CONTRIBUTING.md "one thing per PR" 관점에서
  개별 PR 이 적합. → 각각 single CAND.
proposed_title: "channels/draft-stream-loop: void flush() swallows send throw — draft chunk loss + unhandled rejection"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-22
---

# channels/draft-stream-loop: `void flush()` 가 send throw 를 swallow 해 draft 조각 유실 + unhandled rejection

## 공통 패턴

단일 FIND 기반 single CAND. `src/channels/draft-stream-loop.ts:60, 75` 두
지점에서 `void flush()` 만 호출하고 `.catch()` 가 없다. `flush()` 내부
(line 30-35) 는 send 호출 이전에 `pendingText = ""` 로 reset 하므로 send
throw 경로에서 복구 불가 — "best-effort + pendingText 복구" 설계가 양립
불가.

## 관련 FIND

- FIND-channels-error-boundary-001 (P2): agent streaming reply 의 chunk 단위
  `loop.update(text)` 가 throttle 조건 만족 시 `void flush()` 를 발사. channel
  adapter (telegram/discord/slack editMessage) 가 rate-limit 429 / ECONNRESET /
  TypeError 등으로 throw → flush reject → caller swallow → infra/unhandled-
  rejections.ts:345 process handler 로 propagate (transient warn, non-transient
  `process.exit(1)`).

## 근거 위치

- 문제 호출: `src/channels/draft-stream-loop.ts:60` (setTimeout 내 `void flush()`),
  `src/channels/draft-stream-loop.ts:75` (throttle 통과 즉시 `void flush()`)
- pendingText 초기화 (reject 복구 불가): `src/channels/draft-stream-loop.ts:30-35`
- resolve-only 복구 경로: `src/channels/draft-stream-loop.ts:42-46` (`sent === false`)
- 대조 unconditional 방어 (stop/seal): `src/channels/draft-stream-controls.ts:53-68`
  — `await loop.flush()` 로 reject propagate, caller 에서 catch 가능.
- process-level 수신: `src/infra/unhandled-rejections.ts:345` (transient warn,
  non-transient `exitWithTerminalRestore`)

## 영향

- `impact_hypothesis: wrong-output` — streaming reply 중 throttle window 의
  draft chunk 영구 유실. pendingText 가 이미 "" 로 초기화된 상태.
- 추가: transient network 는 warn-only 이지만 non-transient (TypeError,
  RangeError, runtime payload 오류) 는 process.exit(1) 유발 가능.
- 재현: test harness 에서 `sendOrEditStreamMessage: () => { throw new Error(); }`
  stub 주입 + `loop.update("...")` 호출 → 즉시 unhandledRejection + text 유실.
- severity P2 — streaming reply 는 hot-path. adapter rate-limit/네트워크 오류는
  real production class.

## 대응 방향 (제안만)

- `void flush().catch(onFlushError)` 형태로 `.catch` 부착. 구체적 error handler
  는 caller 주입 (params 에 onFlushError 추가) 혹은 내장 log 로.
- pendingText 복구 전략 재설계: reject 경로에서 `pendingText = text + pendingText`
  로 merge 하거나 unsent text buffer 분리. idempotent retry 고려.
- stop/seal 경로는 이미 `await loop.flush()` 로 방어되어 그대로 유지.

구체 구현은 SOL 단계.

## 중복 검사 (upstream)

`git log upstream/main --since="3 weeks ago" -- src/channels/draft-stream-loop.ts`
→ 0 commits. 동일 영역 race fix 없음. CAL-004 상황 아님.

## 반증 메모

- plugin-sdk 측 reply-payload.ts 또는 상위 adapter wrapper 에서 throw 를 미리
  swallow 할 가능성 — FIND self-check 에 확인 안 함으로 적시. 있다면 P3 로 하향
  가능.
- `draft-stream-controls.test.ts` 에 reject stub 경로가 이미 있는지 미확인 —
  있을 경우 "known" 문제일 수 있음.
- CAL-003 synthetic-only 경고: channel adapter 의 실 throw 빈도를 프로덕션
  telemetry 로 확인 못함. PR 제출 시 최소 production 적 path (e.g. telegram
  rate-limit 시뮬레이션) 로 재현 테스트 포함 필요.
