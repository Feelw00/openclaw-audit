---
candidate_id: CAND-025
type: epic
finding_ids:
  - FIND-mcp-memory-001
  - FIND-mcp-memory-002
cluster_rationale: |
  공통 원인: `OpenClawChannelBridge` (src/mcp/channel-bridge.ts) 의 두 pending Map
  (`pendingClaudePermissions` L50, `pendingApprovals` L51) 이 동일한 세 가드
  (TTL/sweeper, close-clear, cap/FIFO) 를 동시에 결여한 비대칭 결함.
  같은 클래스의 다른 컬렉션 — `queue` (L355 `while (queue.length > QUEUE_LIMIT)
  queue.shift()` cap=1000), `pendingWaiters` (L148 close() 에서 clear + L265
  per-waiter setTimeout fallback) — 은 모두 bounded 인데 두 pending Map 만
  누락. close() (L136-152) 는 두 Map 의 clear 를 동시에 누락한다 (한 줄짜리
  추가 위치도 동일). 이 비대칭은 채널 브리지가 long-running stdio process
  (operator hours-units) 로 가동될 때 단조 증가 leak 을 만든다.

  근거 인용:
  - FIND-mcp-memory-001 root_cause_chain[0]
    (channel-bridge.ts:273-295):
    "L279 set 시 createdAtMs / expiresAtMs / setTimeout 어느 것도 등록하지
    않는다 ... 클래스 안에 sweeper interval / setInterval 도 없음."
  - FIND-mcp-memory-002 root_cause_chain[0]
    (channel-bridge.ts:374-383):
    "L374-383 set 에서 expiresAtMs 를 entry 에 복사하지만 그 값을 기반으로
    setTimeout 을 등록하지도, 주기적 sweeper 를 가동하지도 않는다.
    같은 클래스 내 sweeper interval 부재."
  - FIND-mcp-memory-001 root_cause_chain[1] / FIND-mcp-memory-002 root_cause_chain[1]
    (둘 다 channel-bridge.ts:136-152):
    "close() 는 pendingWaiters.clear() 만 호출하고
    pendingClaudePermissions.clear() / pendingApprovals.clear() 는 부재."
    같은 close 본문, 같은 한 줄 누락 패턴.
  - FIND-mcp-memory-001 root_cause_chain[2] / FIND-mcp-memory-002 root_cause_chain[2]
    (channel-bridge.ts:353-357):
    "queue 의 QUEUE_LIMIT 1000 cap 이 두 pending Map 에 미적용."
    같은 클래스 비대칭 결함 인용.

  Epic 으로 묶는 이유: fix surface 가 단일 close() 라인 (clear 두 개 동시 추가)
  과 단일 sweeper interval (둘 다 expiresAtMs / TTL 기반 정리) 로 자연 공유.
  두 Map 을 분리해 별 PR 로 처리하면 close-clear 변경이 두 PR 로 쪼개져 같은
  메서드를 두 번 건드리고 maintainer review 부담 증가. 공통 가드 하나로 closure
  하는 편이 one-thing-per-PR 원칙(에픽 단위 관점)에도 부합.
proposed_title: "fix(mcp): bound pendingClaudePermissions / pendingApprovals via close-clear + TTL sweep + cap"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-25
state: pending_gatekeeper
upstream_dup_check:
  upstream_head: c070509b7f
  six_week_commits:
    - e157c83c65
    - 0f7d9c9570
    - 74e7b8d47b
    - ba02905c4f
    - ec5877346c
    - 71f37a59ca
  finding: 6주 channel-bridge.ts 커밋 모두 refactor/seam 분리/스모크. leak/TTL/cap 추가 0건.
  pr_search:
    - "pendingClaudePermissions OR pendingApprovals OR channel-bridge memory"
    - "channel-bridge TTL OR channel-bridge expiry OR channel-bridge leak"
    - "mcp memory leak OR channel permission"
  related_open_pr: 56420
  related_pr_axis: "sessionKey binding (security spoofing) — leak 축 직교. 오히려 sessionKey reject 가 누수 가속화 가능성."
  duplicate_decision: not-duplicate
---

# fix(mcp): bound pendingClaudePermissions / pendingApprovals via close-clear + TTL sweep + cap

## 공통 패턴

`OpenClawChannelBridge` (src/mcp/channel-bridge.ts, 516 라인) 안에서 instance-bound
컬렉션 4개 중 2개만 bounded:

| 컬렉션 | 위치 | TTL | close-clear | cap |
|---|---|---|---|---|
| `queue: QueueEvent[]` | L48 | — | — | **L355 QUEUE_LIMIT=1000** ✓ |
| `pendingWaiters: Set<PendingWaiter>` | L49 | **L265 setTimeout fallback** ✓ | **L148 clear()** ✓ | — |
| `pendingClaudePermissions: Map<…>` | L50 | **부재** ✗ | **부재** ✗ | **부재** ✗ |
| `pendingApprovals: Map<…>` | L51 | **부재** ✗ (expiresAtMs 저장만) | **부재** ✗ | **부재** ✗ |

두 누락 Map 모두 같은 close() 본문 (L136-152) 에서 clear 호출이 빠져 있고, 같은
클래스에 setInterval / sweeper 가 0건이라 자동 expiry 도 동작 불가. 인입 trigger
는 외부 (Claude SDK / gateway WS) — 외부가 미응답 / event 누락 / WS drop 시
entry 가 영구 잔존. 채널 브리지가 long-running stdio process (operator hours-units
가동) 인 환경에서 단조 증가 leak.

## 관련 FIND

- **FIND-mcp-memory-001** (P2):
  `pendingClaudePermissions` 가 Claude SDK 의 모든 tool-use permission_request
  notification 마다 set, operator 정규식 응답 (`/^(yes|no)\s+([a-km-z]{5})$/i`)
  이 정확히 매칭 + sessionKey 통과해야만 delete. 미응답 / 형식 불일치 / sessionKey
  불일치 / `--dangerously-skip-permissions` 모두 entry 잔존. close() 도 비우지 않음.

- **FIND-mcp-memory-002** (P2):
  `pendingApprovals` 가 gateway 의 exec/plugin approval requested 이벤트마다 set,
  resolved 이벤트가 도착해야만 delete. payload 의 `expiresAtMs` 를 entry 에
  저장하지만 setTimeout / sweeper 등록 없이 read-only 메타로만 사용. WS drop /
  gateway expiry-only / event 누락 시 entry 잔존. close() 도 비우지 않음.

## 공통 fix surface (epic 정당화)

근거 없는 epic 회피 위해 fix surface 가 실제 공유됨을 표시:

1. **close() (L136-152)** — 한 메서드에서 두 Map clear 동시 추가:
   ```ts
   close(): void {
     // ... 기존 ...
     this.pendingWaiters.clear();
     this.pendingClaudePermissions.clear();  // FIND-001
     this.pendingApprovals.clear();          // FIND-002
     // 등록된 sweeper 도 clearInterval
   }
   ```

2. **공통 sweeper / TTL** — 클래스에 setInterval 자체가 0건. 단일 sweeper 가
   두 Map 에 createdAtMs 또는 expiresAtMs 기준 GC. FIND-002 는 expiresAtMs 가
   이미 payload 에 있고, FIND-001 은 set 시점에 createdAtMs 추가 가능. 한 곳에서
   resolve.

3. **cap/FIFO (선택적 defense-in-depth)** — `queue` 의 QUEUE_LIMIT 패턴을 두
   Map 에 동시 적용. 한 helper 로 통일.

세 변경 모두 단일 클래스 internal — CODEOWNERS 검사: `src/mcp/channel-bridge.ts`
는 `*auth*` / `sandbox*` / `cron/service/jobs.ts` / `cron/stagger.ts` 매치 안
함. 일반 ownership.

## upstream-dup 검사 결과

- `git log upstream/main --since="8 weeks ago" -- src/mcp/channel-bridge.ts`
  6 commits (e157c83c65 / 0f7d9c9570 / 74e7b8d47b / ba02905c4f / ec5877346c
  / 71f37a59ca) — 모두 refactor / seam 분리. expiry / cap / close-clear 추가 0건.
- `gh pr list --search "pendingClaudePermissions OR pendingApprovals OR
  channel-bridge memory"` — leak 축 직접 fix PR 없음.
- PR #56420 (OPEN, sessionKey binding) — 보안 spoofing 축. leak 직교.
  본 fix 와 충돌은 close() 한 곳에서 발생 가능 (rebase merge); 의미 충돌 아님.
- 결론: **not-duplicate**. 본 epic 발행 진행.

## next steps (gatekeeper / publisher 입력)

- one-thing-per-PR 검토: 세 fix surface 가 한 클래스 내 메모리 안전 한 축 →
  XS-S 단일 PR 가능. 본 셀에서 P2 두 건이라 size 면에서 분할 압박 없음.
- 회귀 테스트: `channel-server.test.ts` 에 (a) 미응답 후 size cap 도달 시
  shift, (b) close() 호출 후 두 Map size 0, (c) expiresAtMs 경과 후 sweeper
  delete 세 시나리오 추가 후보.
- 보안 검토: PR #56420 와 같은 메서드 close() 를 건드리므로 충돌 가능성 monitor.
