# openclaw 진행중 PR 트래커

파이프라인 외부에서 이미 진행중인 openclaw PR + 파이프라인이 발행한 PR 의 현재 상태.

## 내 active PR (Feelw00)

### #63105 — feat(cron): split jobs.json into config and runtime state files

- **유형**: 파이프라인 이전 본인 feature PR
- **상태**: OPEN, mergeable CLEAN, 체크 43/43 green
- **Greptile**: **5/5 safe-to-merge** (2026-04-15 재리뷰 기준, P1/P2 모두 해결)
- **커뮤니티**: @Daanvdplas (기능 필요 사용자) + @gumadeiras 메인테이너 리뷰 요청
- **마지막 활동**: 2026-04-15 — 내가 직접 `@greptile review` 수동 트리거
- **대기**: 메인테이너 수동 리뷰
- **브랜치**: `feat/split-cron-store-state` (내 로컬 기본 작업 브랜치이기도 함)
- **관련 이슈**: Closes #53581

### #68531 — fix(plugins): roll back partial registry array contributions when register() throws

- **유형**: 파이프라인 CAND-005, cross-review 3/3 real
- **상태**: OPEN, 체크 43/43 green
- **Greptile**: 4/5 → gatewayHandlers/gatewayMethodScopes 지적 → follow-up commit 34770a1917 로 해결 (CAL-002)
- **대기**: Greptile 재리뷰 (수동 트리거 필요) + 메인테이너 리뷰
- **관련**: issue #68529

### #68543 — fix(infra): keep retryAsync delays above server-supplied Retry-After

- **유형**: 파이프라인 CAND-009, cross-review 3/3 real
- **상태**: OPEN, 체크 45/45 green
- **Greptile**: **5/5 safe-to-merge**
- **대기**: 메인테이너 리뷰
- **관련**: issue #68541

## 종결된 PR

### #68489 (CAND-004, maintainer closed)
- **결과**: false positive — CAL-001 참조
- **사유**: schedulePendingLifecycleError 의 line 249 unconditional delete 가 primary cleanup path. sweeper cleanup 은 fallback.
- **교훈**: R-5 (execution condition 분류) 추가

### #68511 (CAND-006, self-closed)
- **결과**: false positive — CAL-003 참조
- **사유**: test 가 process.kill branch throw 를 강제하지만 production 은 process.emit branch (listener 항상 등록) 만 탐
- **교훈**: R-7 (hot-path vs test-path 일관성) + PR 발행 전 cross-review 3 에이전트 의무화

---

## Greptile 재리뷰 수동 트리거 절차

Greptile 은 commit push 후 **자동으로 재리뷰하지 않음**. 수동 요청 필요.

### 방법 1: 코멘트 트리거 (권장)

PR 에 다음 코멘트 작성:
```
@greptile review and provide confidence score
```

변형:
- `@greptile review` — 기본 재리뷰
- `@greptile review and provide confidence score` — confidence 점수 포함 재리뷰 (권장)

### 방법 2: Greptile 웹 UI

`app.greptile.com/api/retrigger?id=<review_id>` 링크 — 각 Greptile 코멘트 하단에 포함됨.

### 언제 재요청

- Follow-up commit 으로 지적 해결 후
- 초기 리뷰가 비어있거나 오래된 경우
- confidence 점수 다시 받아 메인테이너에게 시그널 주고 싶을 때

### 주의

- **너무 자주 트리거 금지** — rate limit 걸릴 수 있음
- commit push 후 최소 수 분 대기 (CI 완료 후)
- 같은 PR 에 연속 2-3회 이상 코멘트 자제

### 파이프라인 flow 에 포함

```
1. 커밋 + 푸시
2. CI 완료 대기 (gh pr checks <N> --watch)
3. 지적사항 있으면 follow-up 커밋
4. 재-push 후 `@greptile review and provide confidence score` 코멘트
5. Greptile 5/5 확보 후 메인테이너 리뷰 대기
```

## 체크 빈도

주 1회 또는 세션 시작 시 `gh pr list --author Feelw00 --repo openclaw/openclaw --state open` 로 상태 확인.

## R-10: 메인테이너 리뷰 대응 (CAL-006)

메인테이너 review 가 오면 **답변 전 필수 절차**:

1. 답변 draft 금지 — cross-review (3 agent: positive/critical/neutral) 먼저
2. Critical agent 에 "메인테이너가 말한 불변식 + 주변 edge case 동시 탐색" 프롬프트
3. 답변 톤: 사과 + 재검토 결과 + 새 fix commit SHA + 선택지 열기
4. 상세 프로토콜: `maintainer-review-protocol.md`

Anti-pattern (금지):
- "이미 구현됐다" 로 단정 시작
- "file:line 알려달라" 로 책임 전가
- cross-review 없이 답변
- code 변경 없이 comment 만

메인테이너 목록: CONTRIBUTING.md §Maintainers (steipete, obviyus, tyler6204, gumadeiras 등).
