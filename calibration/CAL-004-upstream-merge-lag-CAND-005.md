# CAL-004: upstream merge lag — PR #68531 (CAND-005) obsolete 으로 self-close

**날짜**: 2026-04-18
**CAND**: CAND-005
**PR**: openclaw#68531 (self-closed, not merged)
**트리거**: 사용자 "main 과 conflict 발생 → fetch + 변경점 재분석"
**결과**: upstream main 이 **동일 문제를 하루 먼저 merge** — 내 PR 완전 중복

## 사실

| 시점 | 이벤트 |
|---|---|
| 2026-04-17 | openclaw maintainer @obviyus (Ayaan Zaidi) 가 `59d07f0ab4 fix(plugins): roll back failed register globals` merge |
| 2026-04-17 | `e8fd148437 fix(plugins): roll back failed register side effects` merge |
| 2026-04-17 | `2a283e87a7 fix(plugins): enforce synchronous registration` merge (snapshotPluginRegistry / restorePluginRegistry 도입) |
| 2026-04-18 | 나는 PR #68531 발행 (내 브랜치 base 는 4월 14일 `d7cc6f7643`) |
| 2026-04-18 | cross-review 4 rounds (Greptile, Codex P2 × 3) 전부 대응 |
| 2026-04-18 | 사용자 지시로 upstream fetch — conflict 확인 중 upstream 에 같은 fix 이미 merged 발견 |
| 2026-04-18 | PR + issue self-close |

upstream 변경 요약 (내 PR 과 동일 범위):
- `clearContextEnginesForOwner(owner)` in `src/context-engine/registry.ts:399` (내 `releaseContextEngineOwner` 와 구현 identical)
- `snapshotPluginRegistry` / `restorePluginRegistry` in `src/plugins/` (내 `captureRegistryRollbackSnapshot` / `restoreRegistryRollbackSnapshot` 과 동일 역할)
- `rollbackPluginGlobalSideEffects(record.id)` — 내 `releaseContextEngineOwner(...)` + record 복원 통합한 helper
- register catch 블록에서 위 3개 호출

즉 내 PR 의 **모든 scope** (registry arrays, gatewayHandlers, record counters, context-engine global) 가 upstream 에 이미 들어감.

## 내가 검증에 실패한 지점

### 1. 파이프라인 dedup 단계가 "이미 merged" 를 못 잡음

`dedup.py` 는 issue + PR 의 **title/body 키워드 검색** 기반:
- `captureRegistryRollbackSnapshot` — 내가 만든 이름이라 upstream 에 없음 → 0 hit
- `plugin register partial rollback httpRoutes` — #68529 (내 issue) 만 hit
- `plugin error status orphan route` — 가까운 것은 gateway self-restart (#47142) 으로 무관

키워드 선택이 달랐다면 `rollback failed register globals` 로 검색했으면 `59d07f0ab4` 를 찾았을 것. 하지만 이건 상류 commit → `gh issue list` / `gh pr list` 의 merged PR body 검색으로 커버해야 정상.

### 2. 브랜치 base 노후화

내 worktree 는 `main` 에서 2026-04-14 이후 업데이트 안 함. PR 작업 시간 (4월 18일) 과 **4일 차이**. 그 사이 807 커밋 merged. `main` 에 같은 영역 수정이 들어왔는지 매 세션 시작 시 확인해야 함.

### 3. cross-review 가 "이미 upstream 에 있나" 는 질문 안 함

3 에이전트 (positive/critical/neutral) 는 "버그 실재성", "scope 적절성" 은 검증. 하지만 "upstream main 이 이미 같은 fix 를 merge 했는가" 라는 시간 축 확인은 누락.

## 파이프라인 보강 (이 커밋)

### A. dedup.py 에 upstream merged-PR 검색 추가

기존: `gh issue/pr list --search <keyword>` (state=all) 만 사용.

추가:
- **`gh pr list --state merged --search <symbol>`** 로 이미 merged 된 PR 중 심볼 건드린 것 탐색
- 또는 `gh search commits --owner openclaw --repo openclaw <keyword>` 로 commit 메시지 검색
- 특히 최근 commit (기본 30일) 을 우선 검색

```bash
# dedup.py 에 추가할 쿼리
gh pr list --repo openclaw/openclaw --state merged --search "{keyword}" --limit 20
gh search commits --owner openclaw --repo openclaw "{keyword}" --limit 10 --sort author-date
```

### B. NEXT.md 에 "base refresh" 체크 추가

세션 시작 시:
```bash
cd /Users/lucas/Project/openclaw
git fetch upstream main  # upstream 이 openclaw/openclaw 리모트 있는지 먼저 확인
git log upstream/main --since="1 week ago" -- src/{target-dir}/
```

worktree 가 `upstream/main` 대비 7일 이상 떨어지면 rebase 또는 새 worktree 를 base 로.

### C. Cross-review 페르소나에 "upstream 중복 merge" 질문 추가

solution-gatekeeper / critical-agent 프롬프트에:
> "Upstream `openclaw/main` 이 최근 30일 내 같은 영역 (파일·심볼·동작) 을 수정했는가? 그 commit 이 내 fix 와 중복되는가? 동등한 API 를 이미 export 했는가?"

→ `explored_categories` 에 **"upstream-merge-lag"** 카테고리 필수 포함.

### D. 페르소나 R-8 추가

**R-8. upstream/main 최신성 확인 (필수)**
- 세션 시작 시 `git fetch upstream main` 후 `git log upstream/main --since="2 weeks ago" -- {allowed_paths}` 로 관련 영역 변경 확인
- 이미 merged 된 fix 가 있으면 FIND/PR 불필요
- worktree base 가 upstream/main 대비 1주 이상 오래됐으면 rebase 또는 새 worktree

## 결과

- CAND-005 state → abandoned-upstream-superseded (신규 상태)
- human-verdicts.jsonl 에 기록 (match_verdict=None, reason="upstream superseded")
- `src/context-engine/registry.ts` releaseContextEngineOwner 는 upstream 의 `clearContextEnginesForOwner` 로 대체 (동일 로직)
- Greptile/Codex 가 지적한 context-engine leak 문제 자체는 real 이었음 (cross-review 3/3 판정 정확) — 단지 이미 merged

## 교훈 정리

1. **파이프라인 첫 step 이 "base 최신성 확인"** 이어야 함. dedup 이 그 다음.
2. **upstream merge 속도가 빠른 프로젝트** (openclaw 는 일 1-수십 commit) 에선 base 가 1주만 돼도 위험.
3. **Bot review 가 모든 지적을 해결해도** upstream 이 같은 걸 이미 고쳤으면 PR 은 무용.
4. **CAL-001 (메인테이너 rejection), CAL-003 (self-cross-review), CAL-004 (upstream lag)** 3번의 실패 각각이 파이프라인 다른 지점을 drain. 이번은 "base 관리".

## 재실수 방지 체크리스트

- [ ] 세션 시작 시 `git fetch upstream main` + 최근 2주 log 확인
- [ ] worktree base 가 upstream/main 대비 1주 이상 오래되면 rebase
- [ ] PR 발행 전 dedup.py 가 `--state merged` 도 포함
- [ ] Cross-review 페르소나가 "upstream-merge-lag" 카테고리 탐색
