---
id: FIND-mcp-memory-001
cell: mcp-memory
title: '`pendingClaudePermissions` Map: TTL/cap 부재로 응답 없는 permission request 영구 누적'
file: src/mcp/channel-bridge.ts
line_range: 273-295
evidence: "```ts\n  async handleClaudePermissionRequest(params: {\n    requestId:\
  \ string;\n    toolName: string;\n    description: string;\n    inputPreview: string;\n\
  \  }): Promise<void> {\n    this.pendingClaudePermissions.set(params.requestId,\
  \ {\n      toolName: params.toolName,\n      description: params.description,\n\
  \      inputPreview: params.inputPreview,\n    });\n    this.enqueue({\n      cursor:\
  \ this.nextCursor(),\n      type: \"claude_permission_request\",\n      requestId:\
  \ params.requestId,\n      toolName: params.toolName,\n      description: params.description,\n\
  \      inputPreview: params.inputPreview,\n    });\n    if (this.verbose) {\n  \
  \    process.stderr.write(`openclaw mcp: pending Claude permission ${params.requestId}\\\
  n`);\n    }\n  }\n```\n"
symptom_type: memory-leak
problem: '`OpenClawChannelBridge.pendingClaudePermissions` 는 Claude 가 보내는 permission
  request notification 마다 entry 를 추가하지만, 매칭하는 사용자 응답(`yes/no <id>` 정규식) 이 도착해야만 entry
  가 제거된다. 미응답 / 정규식 불일치 / 다른 sessionKey 응답 시 entry 가 영구 잔존하고, TTL·cap·expiry 가 모두
  부재. 장기 가동 채널 MCP 브리지에서 Map 크기가 단조 증가한다.'
mechanism: "1. Claude SDK 가 tool 사용 직전 `notifications/claude/channel/permission_request`\n\
  \   notification 을 MCP server 에 보냄. (channel-server.ts L42-49)\n2. server 의 setNotificationHandler\
  \ 가 `bridge.handleClaudePermissionRequest`\n   호출 → L279 에서 `pendingClaudePermissions.set(requestId,\
  \ {...})`.\n3. 동시에 enqueue 로 `claude_permission_request` queue event 발행 → operator\
  \ UI/CLI\n   가 channel 을 통해 `yes <id>` / `no <id>` 메시지를 보내야 함 (사람 in-the-loop).\n\
  4. 응답이 오면 `handleSessionMessageEvent` (L440-) 가 텍스트를 추출하고\n   정규식 `/^(yes|no)\\\
  s+([a-km-z]{5})$/i` (L41) 으로 파싱 → 매칭되면 L459 delete.\n5. 그러나 다음 모든 경우 entry 가 영구\
  \ 잔존:\n   - 사용자가 응답하지 않고 다른 메시지/대화로 넘어감 (가장 흔함)\n   - 응답이 정규식에 안 맞음 (대소문자/공백/typo,\
  \ 또는 5자 ID 가 아님)\n   - 응답이 도착했는데 sessionKey 가 다른 채널에서 옴 (PR #56420 가 sessionKey\n\
  \     binding 추가하면 더 많은 응답이 reject 됨)\n   - 사용자가 채널 UI 가 아닌 다른 경로로 결정 (timeout /\
  \ refresh 후)\n6. requestId 는 매 request 마다 새 값 (Claude SDK 가 부여) → 같은 키 overwrite\n\
  \   없음. 누적량 = (Claude tool-call 수) - (정확히 매칭된 응답 수).\n"
root_cause_chain:
- why: 왜 미응답 entry 가 자동 삭제되지 않는가?
  because: L279 set 시 createdAtMs / expiresAtMs / setTimeout 어느 것도 등록하지 않는다. handleClaudePermissionRequest
    시그니처에 expiry 파라미터 없음. bridge 안에 sweeper interval / setInterval 도 없음.
  evidence_ref: src/mcp/channel-bridge.ts:273-295
- why: 왜 close() 가 Map 을 비우지 않는가?
  because: close() (L136-152) 는 `pendingWaiters.clear()` 만 호출하고 `pendingClaudePermissions.clear()`
    / `pendingApprovals.clear()` 는 부재. 코멘트나 의도 표시도 없음. 같은 클래스 내 다른 자료구조(`pendingWaiters`)
    는 cleanup 대상이지만 이 Map 은 누락된 일관성 결함.
  evidence_ref: src/mcp/channel-bridge.ts:136-152
- why: 왜 cap/FIFO eviction 도 없는가?
  because: 같은 클래스의 `queue` 는 L355 `while (this.queue.length > QUEUE_LIMIT) this.queue.shift()`
    로 1000 한도가 있다. 그러나 `pendingClaudePermissions` 에는 동일한 cap 패턴이 적용되지 않았다. R-3 Grep
    `(cap|max|limit|size).*pendingClaudePermissions` 및 `while.*pendingClaudePermissions.size`
    결과 0건.
  evidence_ref: src/mcp/channel-bridge.ts:353-357
- why: 왜 누적 속도가 무시할 수 없는가?
  because: 'Claude permission request 는 Claude 가 사용하는 모든 tool 호출 (read, bash, write,
    ...) 마다 발생할 수 있는 high-frequency notification. operator 가 응답을 놓치거나 채널 UI 외 경로(예:
    --dangerously-skip-permissions)로 처리할 가능성이 있음. PR #56420 는 보안축에서 sessionKey binding
    을 추가하지만, 미매칭 응답이 더 많아져 leak 가속화 가능.'
  evidence_ref: src/mcp/channel-bridge.ts:455-470
impact_hypothesis: memory-growth
impact_detail: "정량 (모델 기반, 프로덕션 관측치 없음):\n- 엔트리 1개 = `ClaudePermissionRequest` { toolName,\
  \ description, inputPreview }\n  ≈ 200-2000 bytes (description/inputPreview 길이 의존,\
  \ MCP 1KB 가정).\n- Claude SDK heavy session 1회당 50-200 tool-call → 10-50 미응답 entry\
  \ 가능.\n- 채널 MCP 브리지는 stdio long-running process (Claude Code/Codex 가 launch),\n\
  \  operator 세션 길이 = 시간 단위.\n- 1주 가동시 수천 entry → 수 MB 급. 단일 프로세스 OOM 즉시 위험은 P3 미만이나\n\
  \  누적이 단조 증가 (감소 경로 0) + 인입은 외부 trigger 라는 점에서 reliability 결함.\n실제 영향은 가동 패턴 의존.\
  \ P2 (조건부 누적, 외부 trigger 의존).\n"
severity: P2
counter_evidence:
  path: src/mcp/channel-bridge.ts
  line: 41-470
  reason: "R-3 Grep (upstream HEAD c070509b7f, 2026-04-25):\n```\nrg -n \"pendingClaudePermissions\\\
    .(delete|clear|evict|splice|shift|pop)\" src/mcp/ src/agents/ src/cli/ src/config/\n\
    \  → src/mcp/channel-bridge.ts:459 delete (handleSessionMessageEvent 내부, conditional)\n\
    \  # 그 외 production delete 경로 없음.\n\nrg -n \"(cap|max|limit|size).*pendingClaudePermissions\"\
    \ src/mcp/ src/agents/ src/cli/ src/config/\n  → match 없음.\n\nrg -n \"while.*pendingClaudePermissions\\\
    .size\" src/mcp/ src/agents/ src/cli/ src/config/\n  → match 없음.\n\nrg -n \"pendingClaudePermissions\\\
    .(set|delete|clear|get|has)\" src/mcp/\n  → channel-bridge.ts:279 set (handleClaudePermissionRequest)\n\
    \  → channel-bridge.ts:458 has  (정규식 매칭 후 검증)\n  → channel-bridge.ts:459 delete\
    \ (응답 매칭 시)\n  → channel-server.test.ts:366 set (테스트 fixture)\n```\n\nR-5 execution\
    \ condition 분류:\n| 경로 | 조건 | 비고 |\n|---|---|---|\n| L459 `delete(requestId)` |\
    \ conditional-edge | `/^(yes|no)\\s+([a-km-z]{5})$/i` 매칭 + has(requestId) 양쪽 통과\
    \ 시에만 |\n| L279 `set(requestId, …)` | unconditional on input | notification 인입\
    \ 시 매번 |\n| close() 의 clear | 부재 | L136-152 close 본문에 pendingClaudePermissions\
    \ 언급 없음 |\n| sweeper / setInterval | 부재 | 클래스 전체에 setInterval 없음 |\n| cap/FIFO\
    \ | 부재 | queue 의 QUEUE_LIMIT (L355) 같은 가드 미적용 |\n\nPrimary-path inversion: \"\
    응답이 항상 매칭된다\"가 참이려면 모든 operator 가\nClaude tool 마다 5자 정확 ID + sessionKey 매칭 응답을\
    \ 즉시 보내야 한다.\nClaude SDK 의 `--dangerously-skip-permissions` 또는 timeout 처리 경로가\n\
    정규식 외 결정을 만들면 entry 영구 잔존.\n\nDefense-in-depth: queue 의 QUEUE_LIMIT 1000 cap 은\
    \ 별도. 두 자료구조는\n독립이라 queue cap 이 pendingClaudePermissions 를 보호하지 못한다.\n\nTest coverage:\
    \ channel-server.test.ts 에 set fixture 만 있고 누적 시나리오\n테스트 없음 (timeout / 미응답 케이스\
    \ 부재).\n\nComment / 의도: 파일 내 \"intentional\" / \"ephemeral\" / \"TTL\" 주석 없음.\n\
    \nCAL-008 dup 검사:\n- `git log upstream/main --since=\"6 weeks ago\" -- src/mcp/channel-bridge.ts`:\n\
    \  e157c83c65, 0f7d9c9570, 74e7b8d47b, ba02905c4f, ec5877346c, 71f37a59ca\n  →\
    \ 모두 리팩터/seam 분리/스모크 강화. leak/TTL/cap 추가 없음.\n- `gh pr list --search \"pendingClaudePermissions\
    \ OR claude permission\"`:\n  PR #56420 OPEN (2026-03-28) \"fix: bind Claude permission\
    \ replies to session\" —\n  sessionKey binding (security spoofing) 만 다룸. leak\
    \ 축 fix 없음.\n  diff 1줄 추가 (`expiresAtMs?: number` 같은 류 아님). 본 FIND 와 직교.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-25'
---
# `pendingClaudePermissions` Map: TTL/cap 부재로 응답 없는 permission request 영구 누적

## 문제

`OpenClawChannelBridge` 의 `pendingClaudePermissions` (Map<string, ClaudePermissionRequest>) 는 Claude SDK 가 채널 MCP 브리지로 보내는 `notifications/claude/channel/permission_request` notification 마다 entry 를 추가한다. entry 제거 경로는 단 하나 — operator 가 정규식 `/^(yes|no)\s+([a-km-z]{5})$/i` 에 정확히 매칭하는 텍스트 메시지를 같은 채널에서 보냈을 때. 미응답 / 정규식 불일치 / 사용자 응답 누락 / 다른 sessionKey 응답 (PR #56420 적용 후) 모두에서 entry 가 영구 잔존하며, TTL · cap · expiresAtMs 자동 expiry · close() clear 모두 부재.

## 발현 메커니즘

1. Claude SDK / Codex client 가 tool 호출 직전 채널 MCP 브리지로 permission notification 송신.
2. `channel-server.ts:42-49` setNotificationHandler 가 `bridge.handleClaudePermissionRequest({requestId, toolName, description, inputPreview})` 호출.
3. `channel-bridge.ts:279` `pendingClaudePermissions.set(requestId, {...})`. 즉시 `enqueue` 로 queue event 발행.
4. operator 가 보낸 텍스트가 `handleSessionMessageEvent` 에 들어와 정규식(L41) 매칭 + has(requestId) 통과 → L459 delete + notification 송신.
5. 매칭 실패 시(미응답 / 형식 불일치 / sessionKey 불일치) entry 잔존. requestId 는 항상 새로워서 같은 키 overwrite 없음.
6. close() 도 이 Map 을 비우지 않음 — bridge 객체가 GC 될 때까지 유지.

## 근본 원인 분석

`pendingClaudePermissions` 는 사람-in-the-loop 승인 흐름의 임시 상태 보관소로 도입되었다. 그러나 사람 응답 누락 / 잘못된 형식 / 채널 외 경로 결정은 일반적 운영 패턴이다 (예: Claude SDK 의 `--dangerously-skip-permissions` 사용 시, 또는 client 가 timeout 후 다른 흐름으로 진행한 경우). 이 Map 은 그런 경우를 위한 보조 정리 경로 (TTL sweeper / max cap / close-time clear) 를 갖추지 않았다.

같은 클래스의 다른 자료구조와 비교:
- `queue` (Array): L355 에 `while (this.queue.length > QUEUE_LIMIT) this.queue.shift()` cap 1000 ✓
- `pendingWaiters` (Set): close() L148 에서 clear ✓ + 각 waiter 의 setTimeout fallback ✓
- `pendingClaudePermissions` (Map): cap / TTL / close-clear 모두 부재 ✗
- `pendingApprovals` (Map): close-clear 부재 (별도 FIND-mcp-memory-002)

이 비대칭이 의도(intentional unbounded)인지를 판별할 주석은 부재. CONTRIBUTING/upstream PR 본문에도 "Claude permission Map 은 의도적 unbounded" 라는 표시 없음. 누락으로 보임.

## 영향

- 영향 유형: memory-growth (slow leak, high-frequency input).
- 트리거: Claude SDK 사용 + 사람 승인 누락 / 형식 오류.
- 누적률: Claude tool-call frequency 의존. heavy session 50-200 tool-call 중 미응답률 10-30% 가정 시 세션당 10-50 entry. 채널 브리지 1주 가동 시 수천 entry.
- 엔트리 크기: ~200-2000 bytes (toolName / description / inputPreview).
- OOM 위험: 즉시는 아니나 무한 누적 (감소 경로 부재 + 외부 trigger). 메모리는 cumulative 단조증가.
- 재현: Claude SDK launch 후 permission notification 발송 → 응답 안 보내고 다음 tool-call 진행 반복 → Map.size 단조 증가 관측.

severity P2: 누적 속도 운영 패턴 의존 + 즉시 OOM 아닌 slow leak. P1 으로 올리려면 production usage rate 측정 필요.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 결과 production delete 경로 단 1개 (L459, conditional-edge). cap / sweeper / close-clear 모두 부재. test fixture (channel-server.test.ts:366) 외 다른 정리 경로 없음.

**카테고리 2 (외부 경계 장치)**: stdio MCP server 는 process 단위 lifetime — process 종료 시 메모리 회수. 그러나 channel MCP 브리지는 long-running CLI command (`openclaw mcp serve`) 로 hours 단위 가동 의도. process boundary 가 leak 을 hide 하지 못함.

**카테고리 3 (호출 빈도 / 경로 활성)**: `handleClaudePermissionRequest` 는 Claude SDK 의 모든 tool-use 시 trigger. heavy 자동화 세션에서 분당 수십 회. 응답 누락은 일반적 (사람이 즉시 응답 못 함).

**카테고리 4 (기존 테스트)**: channel-server.test.ts:366 에 set fixture 있고 PR #56420 가 sessionKey 검증 테스트 추가하지만, 누적 / TTL / cap 시나리오 테스트 부재.

**카테고리 5 (주석/의도)**: 파일 내 "intentional" / "TTL" / "ephemeral" 주석 없음. PR #71f37a59ca (initial bridge) 의 PR 설명에서도 expiry 정책 언급 없음.

**Primary-path inversion**: "모든 응답이 매칭된다" 는 운영 가정이 깨지는 케이스 — `--dangerously-skip-permissions` 모드, timeout, 형식 오류, sessionKey 불일치 — 모두 일반적. 누수 가능 경로가 정상 경로보다 많을 수 있음.

**CAL-008 upstream-dup**: 6주 channel-bridge 커밋 분석에서 leak 축 fix 부재. PR #56420 는 보안축이라 직교. 본 FIND 는 unique.

## Self-check

### 내가 확실한 근거
- `src/mcp/channel-bridge.ts:48-51, 273-295, 440-470` Read 로 set/delete 경로 직접 확인.
- R-3 Grep 4종 모두 0 production cleanup 경로 (L459 conditional-edge 외).
- 같은 클래스 내 `queue` (L355) 와 `pendingWaiters` (L148) 는 cap/clear 정상 — 비대칭 결함.
- CAL-008 dup 검사: 6주 채널 브리지 커밋에 leak 축 fix 없음, PR #56420 는 보안 축.

### 내가 한 가정
- Claude SDK 가 모든 tool-use 마다 permission_request 보낸다는 가정 (channel-server.ts L42 setNotificationHandler 기반 추론, 실제 SDK 호출 frequency 미측정).
- 미응답률 10-30% 추정 — 운영 데이터 없이 모델 기반.
- entry 크기 200-2000 bytes — description/inputPreview 길이 의존, profiling 안 함.
- 채널 MCP 브리지가 hours-units long-running 이라는 가정 — `openclaw mcp serve` CLI 의 의도와 일치.

### 확인 안 한 것 중 영향 가능성
- Claude SDK 측에서 timeout 후 동일 requestId 로 retry 보내는지 — retry 가 있다면 같은 키 overwrite 로 누적 완화. 미확인.
- gateway WS 재연결 시 bridge close() 가 호출되는지 — 호출되면 Map 도 GC, 다만 close() 가 clear 안 부르는 것은 여전히 결함.
- `--dangerously-skip-permissions` 모드에서 SDK 가 permission notification 자체를 안 보내는지 — 보내지 않으면 leak 인입 자체가 차단. 미확인.
