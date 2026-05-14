---
candidate_id: CAND-034
type: single
finding_ids:
  - FIND-auto-reply-lifecycle-002
cluster_rationale: "단일 결함 — inbound-debounce.ts:177-180 의 setTimeout flush timer 가 buffer.timeout.unref() 호출. unref 는 event-loop alive 카운터에서 timer 제외 → SIGTERM / 정상 exit 시 timer callback 미발화. 반환 객체 (L265 `{enqueue, flushKey}`) 에 flushAll/dispose/keys() 부재. shutdown handler 가 buffered key 를 enumerate 할 채널 없음. inbound webhook 이미 200 OK 응답한 메시지가 platform 측 'delivered' 로 기록되지만 silent drop. 다른 auto-reply FIND 와 file/axis 모두 다름."
proposed_title: "auto-reply/inbound-debounce: drain buffered inbound on shutdown"
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: abandoned
gatekeeper_verdict: reject_suspected/high
gatekeeper_metric: metrics/shadow-runs.jsonl
retracted_reason: 'gatekeeper CAL-008 upstream-dup: PR #46303 "fix: drain inbound debounce buffer and followup queues before SIGUSR1 reload" (2026-03-14 OPEN, 2026-05-13 updated) 가 동일 file (inbound-debounce.ts) + 동일 root cause (setTimeout.unref + flushAll API 부재) + 동일 fix surface (flushAll method, flushAllInboundDebouncers, run-loop.ts restart hook) 제안 중. 정확한 중복.'
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    - "f5ebe63ecd fix(auto-reply): preserve debounce ordering"
    - "df27091f5f fix(auto-reply): avoid leaking inbound debounce cleanup"
  finding: "6주 inbound-debounce.ts commit 2건 모두 in-process ordering / leak fix 축. shutdown drain axis 0건."
  pr_search: "gh pr list --search 'inbound-debounce graceful shutdown' → 0 매치."
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs: []
---

# auto-reply/inbound-debounce: drain buffered inbound on shutdown

## 문제 요약

`src/auto-reply/inbound-debounce.ts:177-180` `scheduleFlush` 가 `setTimeout(..., debounceMs)` 후 즉시 `buffer.timeout.unref?.()` 호출. Node 의 `Timeout.unref()` 는 event-loop alive 카운터에서 timer 를 제외 — process 가 graceful exit (SIGTERM / 정상 종료 / gateway restart) 으로 event-loop 가 비기 시작하면 unref timer 의 callback 발화 안 함. buffered items 메모리에서 사라짐.

반환 객체 (L265 `return { enqueue, flushKey }`) 에 `flushAll` / `dispose` / `keys()` 부재. `buffers: Map<string, DebounceBuffer<T>>` (L59) 가 closure private 라 caller 가 enumerate 불가. SIGTERM hook (`src/cli/gateway-cli/run-loop.ts:512` 등) 도 debouncer flush 미호출 (`rg "flushKey|drainDebouncer" src/cli src/gateway src/channels` → 0 매치).

inbound webhook (Telegram/Slack/Discord) 은 200 OK 응답을 server 가 보내면 platform 이 정상 처리로 간주 → 재전송 없음. 결과: debounce window 안에 SIGTERM 도착 시 메시지 silent drop. 사용자 측 platform UI 는 '보냄' 상태이지만 실제 처리 없음.

## fix surface

- inbound-debounce.ts:265 에 `flushAll` / `dispose` API 추가. flushAll() 은 buffers.keys() 를 enumerate → 각 key 에 flushKey 호출 (또는 직접 await flushBuffer). dispose() 는 buffers.values() 의 모든 timer clearTimeout + buffers.clear().
- 또는 createInboundDebouncer 반환 시그니처를 `{ enqueue, flushKey, flushAll, dispose }` 로 확장 + `params.signal?: AbortSignal` 옵션 추가 (graceful drain on abort).
- gateway / channel SIGTERM hook 에서 debouncer instance 들을 모아 flushAll 호출.

XS-S 추정.

## upstream-dup 검사 결과

- 6주 inbound-debounce.ts commit 2건 (f5ebe63ecd, df27091f5f) 모두 in-process scope. shutdown drain axis 0건.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — flushAll/dispose 추가 + caller SIGTERM hook 에서 호출.
2. 회귀 테스트 — child process spawn + SIGTERM 송신 + buffered inbound silent drop 미발생 검증 (또는 graceful drain 호출 시점 fake timer 시뮬레이션).
3. CAL-008 검사 — Slack/Telegram inbound retry 정책 별 platform 별 비대칭 점검.

## 관련 FIND

- FIND-auto-reply-lifecycle-002: inbound debouncer 의 unref timer + flushAll 부재 → SIGTERM 시 buffered 메시지 silent drop.
