---
candidate_id: CAND-026
type: epic
finding_ids:
- FIND-mcp-lifecycle-001
- FIND-mcp-lifecycle-002
cluster_rationale: "공통 원인: standalone tools MCP server (plugin-tools / openclaw-tools\
  \ 가\n공유하는 `connectToolsMcpServerToStdio`) 가 **in-flight callTool 의 lifecycle\n처리를\
  \ 양 끝점에서 동시에 결여**. (1) shutdown 시 in-flight handler drain\n보장 부재 (server.close()\
  \ 가 floating void), (2) host cancellation /\ntransport close 시 abort signal 을 handler\
  \ 와 tool.execute 로 전파할 channel\n부재. 두 결함은 같은 lifecycle 계약 (\"host 가 종료/취소를 신호하면\
  \ server\n는 in-flight 작업을 정리하고 응답 또는 abort 한다\") 의 두 측면이며, FIND\n텍스트 자체가 서로를 명시적으로\
  \ cross-reference.\n\n근거 인용:\n- FIND-mcp-lifecycle-001 root_cause_chain[3] (evidence_ref):\n\
  \  \"src/mcp/plugin-tools-handlers.ts:54 (tool.execute 가 await 인데 cancel\n  signal\
  \ 없음 — FIND-mcp-lifecycle-002 와 연결)\" — 001 의 마지막 step 이\n  자체적으로 002 가 동반 원인임을\
  \ 인용.\n- FIND-mcp-lifecycle-001 root_cause_chain[1]:\n  \"SDK 의 `Server.close()`\
  \ 는 transport 와 protocol layer 를 동기적으로\n  invalidate. handler.callTool 의 `await\
  \ tool.execute(...)` 가 동시에 진행\n  중이면 ... transport 가 닫혀 있어 ... response 를 send 시도해도\
  \ stdout\n  가 EOF\" — drain 부재가 1차 발현 경로.\n- FIND-mcp-lifecycle-002 mechanism:\n\
  \  \"host 가 cancel: (a) MCP `notifications/cancelled` 송신 → SDK 가 internal\n  AbortController.abort()\
  \ 호출 → extra.signal 의 listener 가 trigger 되지만\n  handler 가 무시했으므로 tool.execute 는\
  \ 모름. (b) host 가 child stdin\n  close 또는 SIGTERM → tools-stdio-server.ts shutdown\
  \ → server.close()\n  floating (FIND-mcp-lifecycle-001) — 마찬가지로 in-flight 미통보.\"\
  \ — 002\n  가 001 의 transport close 경로와 동일 mechanism 임을 인용.\n- FIND-mcp-lifecycle-002\
  \ root_cause_chain[0]:\n  \"tools-stdio-server.ts:17 의 setRequestHandler callback\
  \ 시그니처가\n  `async (request)` 로 작성됨. SDK 의 setRequestHandler 두 번째 인자\n  `extra: RequestHandlerExtra`\
  \ 를 통째로 무시.\" — 002 의 1차 결함이\n  001 과 동일 file (tools-stdio-server.ts) 의 동일 wiring\
  \ 함수 안에서 발생.\n\nEpic 으로 묶는 이유: fix surface 가 두 file (tools-stdio-server.ts +\nplugin-tools-handlers.ts)\
  \ 에 걸쳐 있으나 **단일 wiring 변경으로 함께\nresolve** 된다. tools-stdio-server.ts:17 의 setRequestHandler\
  \ callback 을\n`async (request, extra) => handlers.callTool(request.params, extra.signal)`\n\
  로 변경하면서 plugin-tools-handlers.ts:45 callTool 시그니처도\n`(params, signal?: AbortSignal)`\
  \ 로 확장, L54 tool.execute 의 3번째 인자에\nsignal 전달 — 이 한 셋의 변경으로 002 가 해결. FIND-001 의\
  \ drain 측은\n같은 tools-stdio-server.ts 의 shutdown (L30-41) 에서 (a) await close 패턴\n\
  도입 또는 (b) in-flight Set 추적 + drain 으로 resolve. **두 fix 가 같은\n파일 (tools-stdio-server.ts)\
  \ 의 인접 함수 본문 + plugin-tools-handlers.ts\ncallTool 시그니처 한 라인** 으로 한 PR XS-S 범위. 분리\
  \ PR 로 처리하면\n같은 file/같은 함수를 두 번 건드리고 두 PR 모두 동일 회귀 테스트\n(실제 SDK Server + 실제 stdio\
  \ transport + SIGTERM/cancel 시나리오) 를\n중복 구축해야 함. one-thing-per-PR 원칙은 \"MCP server\
  \ in-flight tool call\nlifecycle handling\" 한 축으로 본다.\n\n메인테이너 시각도 동일 예상: tools-stdio-server.ts\
  \ 의 shutdown wiring 과\nsetRequestHandler wiring 은 같은 함수 (`connectToolsMcpServerToStdio`,\n\
  L24-48) 안에 있어 결함도 같은 함수 책임 (host lifecycle contract\nhonoring) 에 귀속된다.\n"
proposed_title: 'fix(mcp): drain in-flight callTool on shutdown and propagate host
  cancellation signal'
proposed_severity: P3
existing_issue: null
created_at: 2026-05-14
state: pending_gatekeeper
cross_review_metric: metrics/cross-review-CAND-026-20260514-081538.jsonl
cross_review_decision: 'scope-down: FIND-mcp-lifecycle-001 (shutdown drain) abandon
  — SDK Protocol._onclose 가 transport.onclose 발화 시 _requestHandlerAbortControllers
  unconditional abort 호출 + protocol.js:369-372 post-handler abort check 가 응답 송신 자체
  skip. drain/await close 추가 효과 미미. FIND-mcp-lifecycle-002 (RequestHandlerExtra.signal
  propagation) 만 단독 PR scope. plugin tool execute 시그니처가 signal 인자 미수신 다수 (memory_recall,
  cron-tool) — fix effective scope 는 signal-aware tool 한정. avg 0.84, critical high
  scope-down override.'
upstream_dup_check:
  upstream_head: af3d9333aa
  six_week_commits:
    tools_stdio_server_ts:
    - 61ab68f5c9
    plugin_tools_handlers_ts:
    - 471489159b
    - 0df90d9b8d
    - e4b09e1bf3
    - 5fa0d282a8
    - 8f3b99c512
    plugin_tools_serve_ts:
    - e75cd46ba6
  finding: '6 주 file 영역 11 commits 모두 policy / serialization / 보안 / 테스트 /

    refactor 축. in-flight drain / abort signal 전파 / await close 추가 0 건.

    '
  pr_search:
  - plugin-tools-serve OR tools-stdio-server in:title,body
  - callTool OR "void server.close" OR abort signal mcp in:title,body
  - AbortSignal mcp plugin in:title,body
  related_open_pr: null
  related_open_pr_notes: "- PR #71648 (CAND-025, mcp-memory) — channel-bridge.ts 한정\
    \ (다른 file).\n  lifecycle 셀의 두 file (tools-stdio-server.ts / plugin-tools-handlers.ts)\n\
    \  과 겹침 없음. axis 직교 (memory vs lifecycle).\n- bundle-mcp 도메인 PR #73536 / #78160\
    \ — client-side callTool timeout 전달\n  (본 셀 server-side 와 정반대). 다른 file (src/agents/pi-bundle-mcp-*.ts).\n"
  duplicate_decision: not-duplicate
  cross_refs_other_cells:
  - CAND-025
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-026-pre-20260515-002424.md
  measurements:
    scenario: proof-CAND-026-e2e
    trials: 1
    executeInvoked: 1
    signalDefined: false
    signalIsAbortSignal: false
    abortObserved: false
    signalAbortedAtEnd: false
    elapsedMs: 501
    handshake: ok
    callOutcome:
      ok: false
      error: 'McpError: MCP error -32001: AbortError: This operation was aborted'
    childStderr: '(node:92433) ExperimentalWarning: Type Stripping is an experimental
      feature and might change at any time

      (Use `node --trace-warnings ...` to show where the warning was created)

      '
  scenario: proof-CAND-026-e2e
---

# fix(mcp): drain in-flight callTool on shutdown and propagate host cancellation signal

## 공통 패턴

`connectToolsMcpServerToStdio` (src/mcp/tools-stdio-server.ts:24-48) 는
plugin-tools 와 openclaw-tools 두 standalone MCP 프로세스의 유일한 wiring
함수이며, MCP host (Claude Code SDK / Codex / ACPX bridge) 와의 lifecycle
계약을 honor 해야 하는 지점이다. 그런데 같은 함수 본문에서 **두 개의
독립적 lifecycle hook 이 동시에 누락**:

| hook | 위치 | 현재 동작 | 누락된 동작 |
|---|---|---|---|
| Shutdown drain | L30-41 (`shutdown` 함수 / L39 `void server.close()`) | floating promise — 핸들러 동기 return, close() 비동기 진행 | in-flight handler drain (await close 또는 in-flight Set 추적) |
| Host cancel 전파 | L17-19 (`setRequestHandler(CallToolRequestSchema, async (request) => ...)`) | 두 번째 인자 `extra: RequestHandlerExtra` 무시 (extra.signal 손실) | extra.signal 을 `handlers.callTool` 로 전달, callTool 이 `tool.execute(id, params, signal)` 로 forward |

두 hook 모두 **MCP host 가 "in-flight tool call 을 정리해라" 라고 신호하는
정규 경로** (SIGTERM/SIGINT/stdin close / `notifications/cancelled`) 에 대한
대응이며, 코드 자체에 alternative 경로가 부재 — primary-path inversion 함정
없이 실재 결함이다.

대비 케이스 (같은 repo 내 정상 패턴):
- `channel-server.ts:93` 은 `close().then(resolveClosed, resolveClosed)` +
  L102-109 finally `await closed` 로 drain 보장 (FIND-001 의 비대칭 대상).
- channel-server.ts 의 `serveOpenClawChannelMcp` 는 try/finally 가 caller
  wrapper 에 있어 unconditional cleanup. plugin-tools-serve.ts /
  openclaw-tools-serve.ts 는 단일 await 후 return — finally 부재.

## 관련 FIND

- **FIND-mcp-lifecycle-001** (P2, file=src/mcp/tools-stdio-server.ts:30-40):
  shutdown 핸들러의 `void server.close()` (L39) 가 promise 를 버려 (a)
  SIGTERM/SIGINT/stdin close 시 in-flight `tool.execute` 의 응답이 transport
  가 닫힌 뒤 EPIPE/silent drop, (b) `Server.close()` reject 시
  unhandledRejection. caller wrapper 측 finally drain 도 부재.

- **FIND-mcp-lifecycle-002** (P2, file=src/mcp/plugin-tools-handlers.ts:45-70):
  `callTool` 이 `tool.execute(id, params)` 만 호출하여 `AnyAgentTool.execute`
  의 4번째 인자 `signal?: AbortSignal` 자리를 영구히 undefined. 상위
  `tools-stdio-server.ts:17` 의 setRequestHandler callback 도 두 번째 인자
  `extra` 를 무시 → SDK 가 `notifications/cancelled` 도착 또는 transport
  close 시 abort 를 trigger 해도 handler 가 listener 등록 안 함.

## 공통 fix surface (epic 정당화)

`tools-stdio-server.ts:17-19` 의 wiring 변경 1 곳 + `tools-stdio-server.ts:30-41`
의 shutdown 변경 1 곳 + `plugin-tools-handlers.ts:45-70` 의 callTool 시그니처
및 tool.execute 호출 1 곳 — 총 3 hunk, 2 files.

```text
src/mcp/tools-stdio-server.ts
  - L17-19: setRequestHandler(CallToolRequestSchema, async (request, extra) =>
      handlers.callTool(request.params, extra.signal));
  - L30-41: in-flight Set 또는 closing barrier 추가 → shutdown 에서 await
      Promise.all([...in-flight]) 후 server.close() 를 await (또는
      channel-server.ts:93 의 `close().then(resolveClosed, resolveClosed)` 패턴
      + caller finally drain 도입).
  - 추가 옵션: caller (plugin-tools-serve.ts / openclaw-tools-serve.ts) 에
      try/finally 도입해 unconditional drain.

src/mcp/plugin-tools-handlers.ts
  - L45 callTool 시그니처에 `signal?: AbortSignal` 추가.
  - L54 `tool.execute(\`mcp-${Date.now()}\`, params.arguments ?? {}, signal)`.
```

세 변경 모두:
- 같은 도메인 (mcp), 같은 lifecycle 축, 같은 wiring 함수 책임.
- CODEOWNERS 검사: `src/mcp/tools-stdio-server.ts` / `src/mcp/plugin-tools-handlers.ts`
  둘 다 `*auth*` / `sandbox*` / `cron/service/jobs.ts` / `cron/stagger.ts`
  매치 안 함 — 일반 ownership.
- 회귀 테스트도 같은 시나리오 (실 SDK Server + 실 stdio transport + 100ms
  이상 deferred tool.execute + SIGTERM 또는 `Protocol.cancelRequest`) 로
  공통 구축 가능 — 분리 PR 시 테스트 인프라 중복.

## upstream-dup 검사 결과

- `git log upstream/main --since="6 weeks ago" -- src/mcp/tools-stdio-server.ts`
  → 1 commit (`61ab68f5c9 refactor: share MCP tools stdio server`, 본 패턴
  도입 commit). 후속 in-flight / signal 처리 fix 0 건.
- `git log upstream/main --since="6 weeks ago" -- src/mcp/plugin-tools-handlers.ts`
  → 5 commits (policy / observability / serialization 2 / 보안). signal / cancel
  축 0.
- `gh pr list --repo openclaw/openclaw --state open --search
  "plugin-tools-serve OR tools-stdio-server in:title,body"` → 본 file 영역
  OPEN PR 없음 (PR #71648 은 channel-bridge.ts 한정).
- `gh pr list --search "callTool OR \"void server.close\" OR abort signal mcp
  in:title,body"` → bundle-mcp PR #73536 / #78160 은 agents 도메인의
  **client-side** timeout 축 (server-side 와 반대) — duplicate 아님.
- 결론: **not-duplicate**. 본 epic 발행 진행.

## CAND-025 와의 관계

- 같은 도메인 (mcp) **다른 axis**: CAND-025 는 channel-bridge.ts 의 메모리
  서피스 (unbounded pending Maps), 본 CAND 는 tools-stdio-server.ts /
  plugin-tools-handlers.ts 의 lifecycle hook 부재.
- 두 CAND 의 fix file 영역 **겹침 없음** (channel-bridge.ts ↔
  tools-stdio-server.ts + plugin-tools-handlers.ts).
- 양방향 cross-reference 만 추가 (CAND-026 의 본 섹션 + CAND-025
  frontmatter cross_refs).
- 같은 PR 으로 통합하면 안 됨 — fix surface 다르고 PR XS 원칙 위반.

## next steps (gatekeeper / publisher 입력)

- one-thing-per-PR 검토: "MCP server in-flight tool call lifecycle handling"
  한 축 → XS-S 단일 PR 가능 (3 hunk / 2 files).
- 회귀 테스트:
  1. plugin-tools-serve.test.ts (또는 신규 in-flight 시나리오 파일) — 실 SDK
     Server + 실 stdio transport + 100ms 이상 deferred tool.execute + SIGTERM
     → 응답 도달 또는 graceful abort 확인.
  2. cancel 시나리오 — `Protocol.cancelRequest` (또는 `notifications/cancelled`)
     → tool.execute 의 signal.aborted=true 확인.
  3. unhandledRejection 시나리오 — close() reject 강제 후 process listener
     로 unhandledRejection 미발생 확인.
- 보안/CODEOWNERS: 두 file 모두 일반 ownership. PR #71648 (mcp-memory) 와
  file 영역 겹침 없음 → rebase 충돌 위험 없음.
- AI-assisted 표시: PR 본문 12섹션에 포함 (openclaw-contribution.md 요건).
