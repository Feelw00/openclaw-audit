---
id: FIND-mcp-memory-002
cell: mcp-memory
title: '`pendingApprovals` Map: expiresAtMs 추적만 하고 자동 expiry 부재 + close cleanup 누락'
file: src/mcp/channel-bridge.ts
line_range: 369-391
evidence: "```ts\n  private trackApproval(kind: ApprovalKind, payload: Record<string,\
  \ unknown>): void {\n    const id = normalizeApprovalId(payload.id);\n    if (!id)\
  \ {\n      return;\n    }\n    this.pendingApprovals.set(id, {\n      kind,\n  \
  \    id,\n      request:\n        payload.request && typeof payload.request ===\
  \ \"object\"\n          ? (payload.request as Record<string, unknown>)\n       \
  \   : undefined,\n      createdAtMs: typeof payload.createdAtMs === \"number\" ?\
  \ payload.createdAtMs : undefined,\n      expiresAtMs: typeof payload.expiresAtMs\
  \ === \"number\" ? payload.expiresAtMs : undefined,\n    });\n  }\n\n  private resolveTrackedApproval(payload:\
  \ Record<string, unknown>): void {\n    const id = normalizeApprovalId(payload.id);\n\
  \    if (id) {\n      this.pendingApprovals.delete(id);\n    }\n  }\n```\n"
symptom_type: memory-leak
problem: '`OpenClawChannelBridge.pendingApprovals` 는 gateway 의 `*.approval.requested`
  이벤트마다 entry 를 추가하고, `*.approval.resolved` 이벤트가 도착해야만 entry 를 제거한다. payload 의 `expiresAtMs`
  를 entry 에 저장은 하지만 자동 expiry 로직이 없고, gateway 가 resolved 이벤트를 누락(WS 끊김 / 패킷 유실 / gateway
  측 expiry-only) 하면 entry 가 영구 잔존. close() 에서 clear 도 부재.'
mechanism: "1. gateway 가 exec.approval.requested / plugin.approval.requested EventFrame\
  \ 을\n   bridge 로 송신. handleGatewayEvent (L393-) 가 `trackApproval(kind, raw)` 호출.\n\
  2. `trackApproval` 이 L374 `pendingApprovals.set(id, {kind, id, request,\n   createdAtMs,\
  \ expiresAtMs})`. expiresAtMs 는 저장되지만 setTimeout 등록 안 함.\n3. operator 가 MCP tool\
  \ `permissions_respond` 호출 → bridge 가 gateway 에\n   `*.approval.resolve` request\
  \ → gateway 가 처리 후 `*.approval.resolved` 이벤트\n   송신 → handleGatewayEvent (L408,\
  \ L428) 가 `resolveTrackedApproval(raw)` 호출\n   → L389 delete.\n4. 누락 케이스 (entry\
  \ 영구 잔존):\n   - WS 연결 끊김 직후 gateway 측에서 expiry 처리되어 resolved 이벤트가 분실\n     (재연결\
  \ 후 missed events delivery 보장 부재 — 본 코드에서 확인 가능).\n   - gateway 가 자체 timeout 으로\
  \ server-side-only resolve 했을 때 (event 송신 누락).\n   - resolved 이벤트의 payload.id 가\
  \ normalize 후 다른 형태 (mismatch).\n   - bridge 가 close 후 재연결 안 함 (L136-152 close 도\
  \ Map clear 부재 → bridge\n     객체가 GC 될 때까지 entry 보유).\n5. `expiresAtMs` 가 entry\
  \ 에 저장되어 있어도 read 만 가능 (listPendingApprovals\n   의 노출용) — 이를 이용한 sweep / filter\
  \ / setTimeout 등록 없음.\n"
root_cause_chain:
- why: 왜 expiresAtMs 가 저장되는데 expiry 로 동작하지 않는가?
  because: L374-383 set 에서 expiresAtMs 를 entry 에 복사하지만 그 값을 기반으로 setTimeout 을 등록하지도,
    주기적 sweeper 를 가동하지도 않는다. expiresAtMs 는 MCP tool `permissions_list_open` 응답에 노출하기
    위한 read-only 메타로만 사용. 같은 클래스 내 sweeper interval 부재 (R-3 Grep 0건).
  evidence_ref: src/mcp/channel-bridge.ts:374-383
- why: 왜 close() 가 Map 을 비우지 않는가?
  because: close() (L136-152) 는 `pendingWaiters.clear()` 만 호출. pendingApprovals /
    pendingClaudePermissions 는 cleanup 누락. 같은 close 본문 내 다른 자료구조 처리 일관성과 비대칭. (FIND-mcp-memory-001
    과 같은 누락 패턴.)
  evidence_ref: src/mcp/channel-bridge.ts:136-152
- why: 왜 cap/FIFO eviction 도 없는가?
  because: R-3 Grep `(cap|max|limit|size).*pendingApprovals`, `while.*pendingApprovals.size`
    매치 0건. 같은 클래스의 queue 는 QUEUE_LIMIT 1000 cap (L355) 가 있으나 pendingApprovals 에 동일
    가드 미적용. 한 채널 브리지가 다수의 long-running 세션을 위탁받아 누적 가능.
  evidence_ref: src/mcp/channel-bridge.ts:353-357
- why: 왜 누락 시나리오가 현실적인가?
  because: 'gateway 와 bridge 사이는 WS 기반. WS drop / reconnect 시 missed event delivery
    보장 코드 본 파일에 부재 (`onClose` L122 가 단순 reject ready). gateway 측에서 expiry-only resolve
    하면 resolved 이벤트가 발송되어도 bridge 가 재연결 전이라면 미수신. 또한 `permissions_respond` 의 `decision:
    "deny"` flow 도 동일 — bridge 는 응답 후 resolved 이벤트를 기다림.'
  evidence_ref: src/mcp/channel-bridge.ts:122-127
impact_hypothesis: memory-growth
impact_detail: "정량 (모델 기반):\n- entry 1개 = `PendingApproval` { kind, id, request?,\
  \ createdAtMs?, expiresAtMs? }\n  ≈ 200-1000 bytes (request payload 의존).\n- exec.approval.requested\
  \ 는 plugin 호출 / 명령 실행 / sandbox 우회 등\n  operator 동의가 필요한 모든 경로 trigger. Claude/Codex\
  \ 자동화 세션에서\n  분당 수회 ~ 수십 회 가능.\n- WS 끊김 비율 / gateway expiry timeout 정책 의존. 1주 가동에서\
  \ 수백~수천 entry\n  가능 → 수백 KB ~ MB.\n- OOM 즉시 위험 P3 미만이나 단조 증가 (감소 경로 = resolved 이벤트\
  \ 수신만) +\n  의도된 expiry 메타가 코드에서 무시됨이 결함의 본질.\n"
severity: P2
counter_evidence:
  path: src/mcp/channel-bridge.ts
  line: 369-391
  reason: "R-3 Grep (upstream HEAD c070509b7f, 2026-04-25):\n```\nrg -n \"pendingApprovals\\\
    .(delete|clear|evict|splice|shift|pop)\" src/mcp/ src/agents/ src/cli/ src/config/\n\
    \  → src/mcp/channel-bridge.ts:389 delete (resolveTrackedApproval, conditional-edge)\n\
    \  # 그 외 production cleanup 경로 없음.\n\nrg -n \"(cap|max|limit|size).*pendingApprovals\"\
    \ src/mcp/ src/agents/ src/cli/ src/config/\n  → match 없음.\n\nrg -n \"while.*pendingApprovals\\\
    .size\" src/mcp/ src/agents/ src/cli/ src/config/\n  → match 없음.\n\nrg -n \"expiresAtMs|expirat\"\
    \ src/mcp/channel-bridge.ts src/mcp/channel-shared.ts\n  → channel-shared.ts:73\
    \ (PendingApproval 타입 필드 정의)\n  → channel-bridge.ts:382 (set 에서 entry 에 복사)\n\
    \  # setTimeout / clearTimeout / sweeper 가 expiresAtMs 를 사용하는 매치 0건.\n\nrg -n\
    \ \"pendingApprovals\\.(set|delete|clear|get|has|size|keys|values|entries)\" src/mcp/\n\
    \  → channel-bridge.ts:374 set (trackApproval)\n  → channel-bridge.ts:389 delete\
    \ (resolveTrackedApproval)\n  → channel-bridge.ts:222 listPendingApprovals 는 [...this.pendingApprovals.values()]\n\
    \    (제거 아닌 enumerate)\n```\n\nR-5 execution condition 분류:\n| 경로 | 조건 | 비고 |\n\
    |---|---|---|\n| L389 `delete(id)` | conditional-edge | gateway 가 `*.approval.resolved`\
    \ 송신 + bridge 수신 시에만 |\n| L374 `set(id, …)` | unconditional on input | gateway\
    \ requested 이벤트마다 |\n| close() clear | 부재 | L136-152 본문에 pendingApprovals 언급 없음\
    \ |\n| sweeper / setInterval | 부재 | 클래스 내 setInterval 0건 |\n| expiresAtMs 기반 expiry\
    \ | 부재 | 저장만 됨, 시간 기반 정리 코드 없음 |\n| cap/FIFO | 부재 | queue 의 QUEUE_LIMIT (L355)\
    \ 미적용 |\n\nPrimary-path inversion: \"resolved 이벤트가 항상 도달한다\" 가 참이려면\ngateway WS\
    \ 가 영원히 끊기지 않거나, 끊김 시 미수신 이벤트 재전송 보장이\n있어야 한다. `onClose` (L122) 는 단순 reject ready,\
    \ 재연결 시 missed events\ncatchup 코드 부재.\n\nDefense-in-depth: queue 의 QUEUE_LIMIT\
    \ 은 다른 자료구조에 적용 안 됨.\n`permissions_list_open` 은 enumerate-only 라 정리 효과 없음.\n\n\
    Test coverage: PR #56420 가 channel-server.test.ts 확장하지만 approval map\nleak 시나리오\
    \ 테스트 부재.\n\nComment / 의도: \"intentional\" / \"TTL\" / \"expiry handled by gateway\"\
    \ 주석 없음.\n\nCAL-008 dup 검사:\n- `git log upstream/main --since=\"6 weeks ago\"\
    \ -- src/mcp/channel-bridge.ts`:\n  6 commit 모두 리팩터/seam 분리. expiresAtMs 활용 추가\
    \ 없음.\n- `gh pr list --search \"pendingApprovals OR pending approval\"`: leak/expiry\n\
    \  축 PR 없음. PR #56420 는 sessionKey-binding 보안 축으로 직교.\n"
status: discovered
discovered_by: memory-leak-hunter
discovered_at: '2026-04-25'
---
# `pendingApprovals` Map: expiresAtMs 추적만 하고 자동 expiry 부재 + close cleanup 누락

## 문제

`OpenClawChannelBridge.pendingApprovals` 는 gateway 의 exec/plugin approval requested 이벤트마다 entry 를 추가하고 resolved 이벤트가 도착할 때만 제거한다. payload 의 `expiresAtMs` 를 entry 에 복사 저장하지만, 그 값을 기반으로 한 자동 만료 / sweeper / setTimeout 이 부재. WS drop / gateway expiry-only / event 누락 시 entry 가 영구 잔존하고, close() 에서도 clear 가 호출되지 않아 비정상 종료 시까지 GC 안 된다.

## 발현 메커니즘

1. operator 가 Claude tool 사용 → gateway 가 `exec.approval.requested` / `plugin.approval.requested` EventFrame 을 채널 MCP 브리지에 송신.
2. `handleGatewayEvent` (channel-bridge.ts:393-) 가 `trackApproval(kind, raw)` 호출 → L374 `pendingApprovals.set(id, {kind, id, request, createdAtMs, expiresAtMs})`. setTimeout 등록 없음.
3. operator 가 MCP tool `permissions_respond` 호출 → bridge 가 gateway 에 RPC → gateway 가 `*.approval.resolved` 이벤트 송신 → L389 delete.
4. 다음 케이스에서 entry 영구 잔존:
   - WS 끊김 (`onClose` L122 단순 reject) → 재연결 후 missed resolved 이벤트 catchup 보장 코드 없음.
   - gateway 측 server-side timeout 으로 resolve (event 송신 안 한 경우).
   - bridge 가 close 후 재시작되지 않은 채 메모리에 잔존.
5. expiresAtMs 는 read-only 메타. listPendingApprovals 응답에만 노출.

## 근본 원인 분석

approval 라이프사이클은 의도상 gateway 가 권한을 갖고, bridge 는 캐시 유사 역할. expiresAtMs 의 존재 자체가 "시간 제한" 개념을 코드에 도입했지만 정작 client-side enforcement 가 누락. 즉, 메타데이터 의도와 동작 사이의 gap. 또한 같은 클래스의 `queue` 는 cap (L355), `pendingWaiters` 는 close-clear (L148) 로 모두 bounded 인데 두 pending Map (`pendingApprovals`, `pendingClaudePermissions`) 만 bounded 가드 부재 — 추가 누락 패턴.

WS-based sync 구조에서 missed event 는 가능한 시나리오. server 도 client 도 cleanup 책임을 떠넘기는 형태가 되어 어느 한쪽이 빠지면 leak.

## 영향

- 영향 유형: memory-growth (slow leak, gateway-event-miss 의존).
- 트리거: approval-heavy 운영 + WS instability 또는 gateway expiry-only.
- 누적률: approval frequency 의존. 자동화 세션 분당 수회.
- 엔트리 크기: ~200-1000 bytes.
- OOM 즉시 위험 P3 미만이나 expiresAtMs 무시는 명백한 의도-구현 gap.

severity P2: 누적 속도 운영 패턴 의존, 즉시 OOM 아닌 slow leak. 외부 trigger (WS drop) 빈도 상승 시 P1 가능.

## 반증 탐색

**카테고리 1 (이미 cleanup 있는지)**: R-3 Grep 결과 production delete 경로 단 1개 (L389, conditional-edge). cap / sweeper / close-clear / expiresAtMs 기반 expiry 모두 부재.

**카테고리 2 (외부 경계 장치)**: gateway 측 expiry 정책이 resolved 이벤트를 항상 송신한다고 가정해야 leak 차단. WS drop 시 catchup 코드 부재 (L122 onClose 는 reject ready 만).

**카테고리 3 (호출 빈도 / 경로 활성)**: exec.approval.requested 는 plugin 호출 / 명령 실행 / sandbox 우회 등 operator 동의 모든 경로. 자동화 세션에서 빈도 높음.

**카테고리 4 (기존 테스트)**: pendingApprovals leak 시나리오 테스트 부재. PR #56420 는 sessionKey 보안 축이라 직교.

**카테고리 5 (주석/의도)**: "TTL" / "expiry by gateway" / "intentional" 주석 없음. expiresAtMs 필드의 사용처 grep 결과 read-only enumerate 만.

**Primary-path inversion**: "resolved 이벤트가 항상 도달한다" 는 WS 및 gateway 모두 신뢰해야 성립 — 본 파일 onClose 처리 부실(catchup 부재) 로 인해 무너짐.

**CAL-008 upstream-dup**: 6주 채널 브리지 커밋에 expiresAtMs 기반 expiry / cleanup 추가 없음. PR #56420 는 보안축 직교.

## Self-check

### 내가 확실한 근거
- `src/mcp/channel-bridge.ts:48-51, 369-391, 393-438` Read 로 set/delete/handle 경로 직접 확인.
- R-3 Grep 4종 모두 production cleanup / cap / expiry 0건.
- `expiresAtMs` 필드 사용처 grep: set 시 1회 + 타입 정의 1회 = read-only 메타.
- 같은 클래스 비대칭: queue cap, pendingWaiters close-clear 는 정상이나 두 pending Map 만 누락.
- CAL-008: 6주 브리지 커밋에 expiry 추가 없음, PR #56420 는 보안축 직교.

### 내가 한 가정
- gateway 가 항상 resolved 이벤트를 송신한다는 보장 부재 가정 — gateway 코드 직접 트레이스 안 함 (allowed_paths 외).
- WS drop 빈도가 production 에서 무시할 수 없다는 가정 — 운영 metrics 부재.
- approval frequency 추정 (분당 수회) — 자동화 세션 패턴 추정.

### 확인 안 한 것 중 영향 가능성
- gateway 의 `sessions.subscribe` 가 missed events catchup 정책을 갖는지 — handleHelloOk (L322) 가 subscribe 만 하고 missed delivery 표시 없음.
- bridge 가 reconnect 시 새 instance 가 생성되는지 — 그렇다면 이전 Map 은 GC. 그러나 같은 process 내 long-running CLI 라면 instance 재생성 보장 없음.
- listPendingApprovals 호출 빈도 — 자주 호출되어 size 상한이 모니터링되면 운영적 cap 가능 (그러나 코드 가드 아님).
