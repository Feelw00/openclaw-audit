---
candidate_id: CAND-039
type: single
finding_ids:
- FIND-gateway-lifecycle-002
cluster_rationale: 단일 결함 — server-runtime-services.ts:156-190 의 두 recovery 함수 (recoverPendingOutboundDeliveries
  즉시 IIFE, recoverPendingSessionDeliveries setTimeout(1250ms)) 가 stop handle 반환 부재
  + caller (server.impl.ts:1366-1389) 가 cancellation 통로 미보유. timer.unref() 는 event-loop
  alive 만 막을 뿐 callback fire 차단 아님. boot 직후 1250ms 이내 SIGTERM 시 tearing-down state
  에서 recovery 가 dynamic import + dispose 된 deps 사용 시도 → error log 노이즈 + 의도치 않은 외부
  메시지 발사 + partial state 디스크 잔존. 같은 file 의 scheduleGatewayPostReadyMaintenance (L103-154)
  가 isClosing 가드 + clearGatewayMaintenanceHandles 패턴 보유 — 비대칭 누락. 다른 gateway FIND
  와 axis 다름.
proposed_title: 'gateway/runtime-services: cancel startup recovery jobs on shutdown'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
upstream_head_checked: af3d9333aa
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
  - 'f4f98f45c7 fix(gateway): cancel post-ready maintenance on close'
  - '654b70dde8 fix(gateway): keep cron startup after maintenance failure'
  - '0b1fbeabed perf(gateway): defer cron and sentinel startup work'
  - 'a903df02f5 fix(gateway): bound restart continuation recovery'
  - '0ac81d41b6 fix(gateway): durably hand off restart continuations'
  finding: f4f98f45c7 가 post-ready maintenance 에 cancellation 추가하면서 같은 파일의 recovery
    함수에는 동일 패턴 미적용. a903df02f5 / 0ac81d41b6 (restart continuation) 는 recovery 의 다른
    axis (bound semantics, hand-off durability). cancellation handle 부재 fix 0건.
  pr_search: gh pr list --search 'recoverPendingOutboundDeliveries OR recoverPendingSessionDeliveries'
    → 0 매치.
  related_open_pr: null
  related_open_pr_notes: null
  duplicate_decision: not-duplicate
  cross_refs_other_cells: []
cross_refs:
- CAND-038
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-039-pre-20260515-061713.md
  measurements:
    scenario: proof-CAND-039-e2e
    trials: 5
    fire_trials: 5
    not_ready_trials: 0
    fire_rate: 1.0
    boot_ms_samples:
    - 2143
    - 2040
    - 2139
    - 2062
    - 2152
    close_prelude_ms_samples:
    - 2306
    - 43
    - 2280
    - 43
    - 2275
    fire_keyword: Found N pending delivery entries — starting recovery
    subsystem: delivery-recovery
    trial_results:
    - trial_idx: 0
      entry_id: 9fe2c243f9384c139abdc33766b0bf09
      port_listen: true
      port_listen_ms: 1500
      ready: true
      ready_marker: '[gateway] ready'
      boot_ms: 2143
      status: ok
      rc: 0
      close_prelude_ms: 2306
      fire_found: true
      fire_line: Found 1 pending delivery entries — starting recovery
      complete_found: true
      complete_line: 'Delivery recovery complete: 1 recovered'
      subsystem_seen: true
      stderr_len: 360
      stdout_len: 2971
      stderr_tail: "\e[90m2026-05-15T15:16:53.563+09:00\e[39m \e[36m[gateway]\e[39m\
        \ \e[33mauth mode=none explicitly configured; all gateway connections are\
        \ unauthenticated.\e[39m\n2026-05-15T15:16:57.465+09:00 (node:31968) ExperimentalWarning:\
        \ Type Stripping is an experimental feature and might change at any time\n\
        (Use `node --trace-warnings ...` to show where the warning was created)\n"
      stdout_tail: "\e[39m \e[36m[heartbeat]\e[39m \e[36mstarted\e[39m\n\e[90m2026-05-15T15:16:55.380+09:00\e\
        [39m \e[33m[delivery-recovery]\e[39m \e[36mFound 1 pending delivery entries\
        \ — starting recovery\e[39m\n\e[90m2026-05-15T15:16:57.469+09:00\e[39m \e\
        [36m[gateway]\e[39m \e[36msignal SIGTERM received\e[39m\n\e[90m2026-05-15T15:16:57.517+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mreceived SIGTERM; shutting down\e[39m\n\e\
        [90m2026-05-15T15:16:57.627+09:00\e[39m \e[33m[delivery-recovery]\e[39m \e\
        [36mRecovered delivery 9fe2c243f9384c139abdc33766b0bf09 on telegram\e[39m\n\
        \e[90m2026-05-15T15:16:57.628+09:00\e[39m \e[33m[delivery-recovery]\e[39m\
        \ \e[36mDelivery recovery complete: 1 recovered, 0 failed, 0 skipped (max\
        \ retries), 0 deferred (backoff)\e[39m\n\e[90m2026-05-15T15:16:57.645+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mstarted: gateway stopping\e[39m\n\e[90m2026-05-15T15:16:57.646+09:00\e\
        [39m \e[35m[plugins]\e[39m \e[36mbonjour: advertised gateway fqdn=MacBook Pro\
        \ (OpenClaw) (2)._openclaw-gw._tcp.local. host=MacBook-Pro-(2).local. port=17000\
        \ state=unannounced\e[39m\n\e[90m2026-05-15T15:16:57.682+09:00\e[39m \e[34m[gmail-watcher]\e\
        [39m \e[36mgmail watcher stopped\e[39m\n\e[90m2026-05-15T15:16:57.684+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mcompleted cleanly in 40ms\e[39m\n"
    - trial_idx: 1
      entry_id: 7da28754906141dcb7e4cbbed4eabe91
      port_listen: true
      port_listen_ms: 1623
      ready: true
      ready_marker: '[gateway] ready'
      boot_ms: 2040
      status: ok
      rc: 0
      close_prelude_ms: 43
      fire_found: true
      fire_line: Found 1 pending delivery entries — starting recovery
      complete_found: false
      complete_line: null
      subsystem_seen: true
      stderr_len: 153
      stdout_len: 1984
      stderr_tail: "\e[90m2026-05-15T15:16:58.640+09:00\e[39m \e[36m[gateway]\e[39m\
        \ \e[33mauth mode=none explicitly configured; all gateway connections are\
        \ unauthenticated.\e[39m\n"
      stdout_tail: ", file-transfer, memory-core, phone-control, talk-voice; 1.3s)\e\
        [39m\n\e[90m2026-05-15T15:16:59.980+09:00\e[39m \e[36m[gateway]\e[39m \e[36mlog\
        \ file: /tmp/openclaw/openclaw-2026-05-15.log\e[39m\n\e[90m2026-05-15T15:16:59.982+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mstarting channels and sidecars...\e[39m\n\
        \e[90m2026-05-15T15:16:59.994+09:00\e[39m \e[35m[plugins]\e[39m \e[36membedded\
        \ acpx runtime backend registered (cwd: /tmp/proof-c039-e2e-persist/.openclaw/.openclaw/workspace)\e\
        [39m\n\e[90m2026-05-15T15:17:00.180+09:00\e[39m \e[35m[plugins]\e[39m \e[36membedded\
        \ acpx runtime backend ready\e[39m\n\e[90m2026-05-15T15:17:00.259+09:00\e\
        [39m \e[36m[browser/server]\e[39m \e[36mBrowser control listening on http://127.0.0.1:17002/\
        \ (auth=token)\e[39m\n\e[90m2026-05-15T15:17:00.262+09:00\e[39m \e[36m[gateway]\e\
        [39m \e[36mready\e[39m\n\e[90m2026-05-15T15:17:00.265+09:00\e[39m \e[36m[heartbeat]\e\
        [39m \e[36mstarted\e[39m\n\e[90m2026-05-15T15:17:00.276+09:00\e[39m \e[36m[gateway]\e\
        [39m \e[36msignal SIGTERM received\e[39m\n\e[90m2026-05-15T15:17:00.285+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mreceived SIGTERM; shutting down\e[39m\n\e\
        [90m2026-05-15T15:17:00.287+09:00\e[39m \e[33m[delivery-recovery]\e[39m \e\
        [36mFound 1 pending delivery entries — starting recovery\e[39m\n"
    - trial_idx: 2
      entry_id: dfa2f54867024835bd1844503e8c4e42
      port_listen: true
      port_listen_ms: 1606
      ready: true
      ready_marker: '[gateway] ready'
      boot_ms: 2139
      status: ok
      rc: 0
      close_prelude_ms: 2280
      fire_found: true
      fire_line: Found 1 pending delivery entries — starting recovery
      complete_found: true
      complete_line: 'Delivery recovery complete: 1 recovered'
      subsystem_seen: true
      stderr_len: 360
      stdout_len: 2809
      stderr_tail: "\e[90m2026-05-15T15:17:01.258+09:00\e[39m \e[36m[gateway]\e[39m\
        \ \e[33mauth mode=none explicitly configured; all gateway connections are\
        \ unauthenticated.\e[39m\n2026-05-15T15:17:05.009+09:00 (node:32044) ExperimentalWarning:\
        \ Type Stripping is an experimental feature and might change at any time\n\
        (Use `node --trace-warnings ...` to show where the warning was created)\n"
      stdout_tail: "\e[39m \e[36m[heartbeat]\e[39m \e[36mstarted\e[39m\n\e[90m2026-05-15T15:17:02.918+09:00\e\
        [39m \e[33m[delivery-recovery]\e[39m \e[36mFound 1 pending delivery entries\
        \ — starting recovery\e[39m\n\e[90m2026-05-15T15:17:05.014+09:00\e[39m \e\
        [36m[gateway]\e[39m \e[36msignal SIGTERM received\e[39m\n\e[90m2026-05-15T15:17:05.058+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mreceived SIGTERM; shutting down\e[39m\n\e\
        [90m2026-05-15T15:17:05.161+09:00\e[39m \e[33m[delivery-recovery]\e[39m \e\
        [36mRecovered delivery dfa2f54867024835bd1844503e8c4e42 on telegram\e[39m\n\
        \e[90m2026-05-15T15:17:05.161+09:00\e[39m \e[33m[delivery-recovery]\e[39m\
        \ \e[36mDelivery recovery complete: 1 recovered, 0 failed, 0 skipped (max\
        \ retries), 0 deferred (backoff)\e[39m\n\e[90m2026-05-15T15:17:05.176+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mstarted: gateway stopping\e[39m\n\e[90m2026-05-15T15:17:05.177+09:00\e\
        [39m \e[35m[plugins]\e[39m \e[36mbonjour: advertised gateway fqdn=MacBook Pro\
        \ (OpenClaw) (2)._openclaw-gw._tcp.local. host=MacBook-Pro-(2).local. port=17000\
        \ state=unannounced\e[39m\n\e[90m2026-05-15T15:17:05.212+09:00\e[39m \e[34m[gmail-watcher]\e\
        [39m \e[36mgmail watcher stopped\e[39m\n\e[90m2026-05-15T15:17:05.214+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mcompleted cleanly in 38ms\e[39m\n"
    - trial_idx: 3
      entry_id: d5ea660dd1344c07adf1b2c9c3b02810
      port_listen: true
      port_listen_ms: 1630
      ready: true
      ready_marker: '[gateway] ready'
      boot_ms: 2062
      status: ok
      rc: 0
      close_prelude_ms: 43
      fire_found: true
      fire_line: Found 1 pending delivery entries — starting recovery
      complete_found: false
      complete_line: null
      subsystem_seen: true
      stderr_len: 153
      stdout_len: 1984
      stderr_tail: "\e[90m2026-05-15T15:17:06.175+09:00\e[39m \e[36m[gateway]\e[39m\
        \ \e[33mauth mode=none explicitly configured; all gateway connections are\
        \ unauthenticated.\e[39m\n"
      stdout_tail: ", file-transfer, memory-core, phone-control, talk-voice; 1.3s)\e\
        [39m\n\e[90m2026-05-15T15:17:07.518+09:00\e[39m \e[36m[gateway]\e[39m \e[36mlog\
        \ file: /tmp/openclaw/openclaw-2026-05-15.log\e[39m\n\e[90m2026-05-15T15:17:07.520+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mstarting channels and sidecars...\e[39m\n\
        \e[90m2026-05-15T15:17:07.532+09:00\e[39m \e[35m[plugins]\e[39m \e[36membedded\
        \ acpx runtime backend registered (cwd: /tmp/proof-c039-e2e-persist/.openclaw/.openclaw/workspace)\e\
        [39m\n\e[90m2026-05-15T15:17:07.717+09:00\e[39m \e[35m[plugins]\e[39m \e[36membedded\
        \ acpx runtime backend ready\e[39m\n\e[90m2026-05-15T15:17:07.798+09:00\e\
        [39m \e[36m[browser/server]\e[39m \e[36mBrowser control listening on http://127.0.0.1:17002/\
        \ (auth=token)\e[39m\n\e[90m2026-05-15T15:17:07.805+09:00\e[39m \e[36m[gateway]\e\
        [39m \e[36mready\e[39m\n\e[90m2026-05-15T15:17:07.808+09:00\e[39m \e[36m[heartbeat]\e\
        [39m \e[36mstarted\e[39m\n\e[90m2026-05-15T15:17:07.822+09:00\e[39m \e[36m[gateway]\e\
        [39m \e[36msignal SIGTERM received\e[39m\n\e[90m2026-05-15T15:17:07.827+09:00\e\
        [39m \e[33m[delivery-recovery]\e[39m \e[36mFound 1 pending delivery entries\
        \ — starting recovery\e[39m\n\e[90m2026-05-15T15:17:07.828+09:00\e[39m \e\
        [36m[gateway]\e[39m \e[36mreceived SIGTERM; shutting down\e[39m\n"
    - trial_idx: 4
      entry_id: 7ba7f6442a514cf79756beeb388d314f
      port_listen: true
      port_listen_ms: 1617
      ready: true
      ready_marker: '[gateway] ready'
      boot_ms: 2152
      status: ok
      rc: 0
      close_prelude_ms: 2275
      fire_found: true
      fire_line: Found 1 pending delivery entries — starting recovery
      complete_found: true
      complete_line: 'Delivery recovery complete: 1 recovered'
      subsystem_seen: true
      stderr_len: 360
      stdout_len: 2809
      stderr_tail: "\e[90m2026-05-15T15:17:08.805+09:00\e[39m \e[36m[gateway]\e[39m\
        \ \e[33mauth mode=none explicitly configured; all gateway connections are\
        \ unauthenticated.\e[39m\n2026-05-15T15:17:12.573+09:00 (node:32097) ExperimentalWarning:\
        \ Type Stripping is an experimental feature and might change at any time\n\
        (Use `node --trace-warnings ...` to show where the warning was created)\n"
      stdout_tail: "\e[39m \e[36m[heartbeat]\e[39m \e[36mstarted\e[39m\n\e[90m2026-05-15T15:17:10.473+09:00\e\
        [39m \e[33m[delivery-recovery]\e[39m \e[36mFound 1 pending delivery entries\
        \ — starting recovery\e[39m\n\e[90m2026-05-15T15:17:12.579+09:00\e[39m \e\
        [36m[gateway]\e[39m \e[36msignal SIGTERM received\e[39m\n\e[90m2026-05-15T15:17:12.622+09:00\e\
        [39m \e[36m[gateway]\e[39m \e[36mreceived SIGTERM; shutting down\e[39m\n\e\
        [90m2026-05-15T15:17:12.729+09:00\e[39m \e[33m[delivery-recovery]\e[39m \e\
        [36mRecovered delivery 7ba7f6442a514cf79756beeb388d314f on telegram\e[39m\n\
        \e[90m2026-05-15T15:17:12.729+09:00\e[39m \e[33m[delivery-recovery]\e[39m\
        \ \e[36mDelivery recovery complete: 1 recovered, 0 failed, 0 skipped (max\
        \ retries), 0 deferred (backoff)\e[39m\n\e[90m2026-05-15T15:17:12.740+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mstarted: gateway stopping\e[39m\n\e[90m2026-05-15T15:17:12.741+09:00\e\
        [39m \e[35m[plugins]\e[39m \e[36mbonjour: advertised gateway fqdn=MacBook Pro\
        \ (OpenClaw) (2)._openclaw-gw._tcp.local. host=MacBook-Pro-(2).local. port=17000\
        \ state=unannounced\e[39m\n\e[90m2026-05-15T15:17:12.777+09:00\e[39m \e[34m[gmail-watcher]\e\
        [39m \e[36mgmail watcher stopped\e[39m\n\e[90m2026-05-15T15:17:12.778+09:00\e\
        [39m \e[33m[shutdown]\e[39m \e[36mcompleted cleanly in 39ms\e[39m\n"
  scenario: proof-CAND-039-e2e
---

# gateway/runtime-services: cancel startup recovery jobs on shutdown

## 문제 요약

`src/gateway/server-runtime-services.ts:156-190` 의 두 recovery 함수:

- `recoverPendingOutboundDeliveries` (L156-170): 즉시 fire-and-forget async IIFE, 동적 `await import("../infra/outbound/delivery-queue.js")` → `recoverPendingDeliveries(...)`. handle 또는 stop 함수 반환 없음.
- `recoverPendingSessionDeliveries` (L172-190): `setTimeout(callback, 1250)`. timer.unref?.() 만 호출. handle 미반환.

caller (`server.impl.ts:1366-1389` `activateScheduledServicesWhenReady`) 는 startup guard (`closePreludeStarted`) 로 activate 진입만 보호, 진입한 후 background work 의 cancellation 통로 없음. `activateGatewayScheduledServices` 의 return 타입 (L255-258 `{heartbeatRunner, stopModelPricingRefresh}`) 에 recovery stop 핸들 부재.

`timer.unref()` 는 event-loop alive 카운터에서 timer 를 제외할 뿐, queued callback 자체는 process 가 다른 작업으로 살아있는 동안 fire. shutdown (`runGatewayClosePrelude` + close handler) 이 startup 직후 1250ms 이내 시작되면 recovery 가 tearing-down state 에서 진행:
- `deliverOutboundPayloadsInternal` 이 dispose 된 channelManager 호출 → error log
- 외부 채널 (Slack/Telegram/SMS) 에 의도치 않은 message 발사
- partial state 디스크 잔존

같은 파일 `scheduleGatewayPostReadyMaintenance` (L103-154) 가 `isClosing()` 가드 + `clearGatewayMaintenanceHandles` 패턴을 보유 — 동일 패턴이 recovery 에 적용 가능했음을 시사.

## fix surface

옵션 A (minimal): 두 recovery 함수 에 isClosing 가드 추가 + setTimeout 의 handle 을 host (server.impl.ts) 에 반환해 close 시 clearTimeout. recoverPendingOutboundDeliveries 의 IIFE 내부에는 (a) 각 await 사이 isClosing 체크 또는 (b) AbortSignal 파라미터 전달 — 후자가 더 안전.
옵션 B (정밀): recovery 함수 시그니처에 `signal: AbortSignal` 추가. `recoverPendingDeliveries` / `recoverPendingRestartContinuationDeliveries` (셀 밖) 에도 signal 전파 — 별도 PR.

XS-S 추정 (옵션 A).

## upstream-dup 검사 결과

- f4f98f45c7 가 post-ready maintenance 에 cancellation 추가 시점에 recovery 함수에는 동일 패턴 미적용.
- duplicate_decision: not-duplicate

## next steps

1. fix branch — isClosing 가드 + timer handle 반환.
2. 회귀 테스트 — fake timer + immediate close trigger + recovery callback 미fire 검증.
3. CAL-003 회피 — boot→shutdown 1250ms 이내 window 가 CI/dev/smoke test 환경 hot-path 임을 명시.

## 관련 FIND

- FIND-gateway-lifecycle-002: recoverPendingOutboundDeliveries / recoverPendingSessionDeliveries 가 cancellation handle 미반환 + caller 측 cancellation 통로 부재.
