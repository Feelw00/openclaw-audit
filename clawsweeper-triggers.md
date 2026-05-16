# clawsweeper 트리거 방식

openclaw 의 in-house review bot `clawsweeper` (= Codex 기반) 의 트리거 메커니즘.
원천: `openclaw/.github/workflows/clawsweeper-dispatch.yml`.

bot 무반응 / 재리뷰 필요 시 우선 참조.

## 1. 자동 트리거 (이벤트 → dispatch step)

| 이벤트 | action | dispatch step | 비고 |
|---|---|---|---|
| `issues` | opened, reopened, edited, labeled, unlabeled | "Dispatch exact ClawSweeper review" (L147) | 첫 이슈 평가 |
| `pull_request_target` | opened, reopened, **synchronize**, ready_for_review, edited, labeled, unlabeled | "Dispatch exact ClawSweeper review" (L147) | **force-push 시 synchronize 자동 발화** — PR 재평가 |
| `push` to main | — | "Dispatch ClawSweeper commit review" (L258) | 메인 머지 후 commit-level |
| `pull_request_review` | submitted, edited, dismissed | (activity log only) | 사람 리뷰 발화 시 활동 로그 |
| `pull_request_review_comment` | created, edited | (activity log only) | line-level 코멘트 |
| `issue_comment` | created, edited | "Acknowledge and dispatch ClawSweeper comment" (L177) — **regex 매치 시만** | PR conversation 코멘트 |

추가 동작:
- 라벨 이벤트는 20s sleep debounce (L33-35).
- `edited / synchronize / ready_for_review` 는 `cancel-in-progress: true` (L22) — 연속 이벤트는 직전 run 취소.

## 2. 명시 트리거 (코멘트 키워드)

`issue_comment` 이벤트의 line 196 regex:

```regex
(^|[[:space:]])@(clawsweeper|openclaw-clawsweeper)\b(\[bot\])?
| (^|[[:space:]])/(clawsweeper|review|automerge|autoclose)\b
```

매치되는 코멘트 시작 키워드:

- `@clawsweeper ...` — 가장 명시적, 권장
- `@openclaw-clawsweeper ...`
- `/clawsweeper`
- `/review`
- `/automerge` — 자동 머지 명령 (별개 경로)
- `/autoclose` — 자동 close 명령 (별개 경로)

매치 안 되면 log `No ClawSweeper command found in comment` 출력 후 step exit.
**본문에 키워드 없으면 router dispatch 안 됨**.

## 3. dispatch 경로 (요약)

```
이벤트 → workflow run
       ├─ "Dispatch GitHub activity to ClawSweeper" (모든 이벤트, 활동 로그성, 평가 트리거 아님)
       └─ 이벤트별 분기:
            ├─ issues / pull_request_target → "Dispatch exact ClawSweeper review" (PR/issue 평가)
            ├─ issue_comment (regex 매치) → "Acknowledge and dispatch ClawSweeper comment" (라우터 + comment_id 전달)
            └─ push to main → "Dispatch ClawSweeper commit review" (commit-level)
```

실제 평가/코멘트 작성은 openclaw 본 repo 가 아닌 별도 `openclaw/clawsweeper` repo 에서 처리.
workflow run = dispatch 자체만 success/fail. 실제 review 코멘트 도착까지는 별개 시간선.

## 4. 권한 / 가시성

- `AUTHOR_ASSOCIATION ∈ {OWNER, MEMBER, COLLABORATOR}` 만 acknowledge status 코멘트
  (`🦞👀 ClawSweeper picked this up`) 를 받음 (L219-240).
- 외부 contributor (Feelw00 등) 도 dispatch 자체는 됨. status 코멘트만 안 옴.
- 코멘트 명령에 👀 (eyes) reaction 자동 부여 (L204).

## 5. 라벨 시스템

`clawsweeper-verdict:*` / `clawsweeper-action:*` HTML comment 메타데이터로 verdict 기록.
라벨은 별개 자동 토글:

- `proof: supplied` — PR body 에 6 필드 evidence 있음
- `proof: sufficient` — clawsweeper 가 evidence 를 convincing 으로 판정
- `clawsweeper-action: fix-required` — needs-changes verdict 시 머지 차단 트리거
- `triage: refactor-only` — refactor only 판정

force-push 또는 fix commit 후 clawsweeper 가 새 평가를 시작하면
`proof: sufficient` 같은 평가성 라벨은 일단 reset 됐다가 재평가 후 다시 부여되는 패턴.

## 6. 운영 사례

### Case 1: PR #82482 force-push 후 reply (2026-05-16)

| 시점 | 시도 | 결과 | 원인 |
|---|---|---|---|
| 06:31 opened | pull_request_target.opened 자동 | ✅ 4분 만에 Codex review | opened 트리거 정상 |
| 07:24 reply 게시 | issue_comment.created (본문 키워드 없음) | ❌ router dispatch 안 됨 | regex 매치 실패 → step exit |
| 07:35 force-push | pull_request_target.synchronize 자동 | ✅ workflow SUCCESS at 07:45, `proof: sufficient` 라벨 reset | sha 기반 재리뷰 |
| 07:58 reply edit + `@clawsweeper review` | issue_comment.edited + regex 매치 | ✅ router dispatch | comment_id 와 함께 명시 dispatch |

**교훈**:
- force-push 만으로 sha-based 재리뷰 트리거 OK. 그러나 사용자 본문 (반박 논거) 은 컨텍스트에 안 들어감.
- bot 에게 사용자 reply 를 컨텍스트로 전달하려면 reply 본문에 명시 키워드 필수.
- 가장 간결한 방법: 기존 reply 를 edit 해서 끝에 `@clawsweeper review` 추가 → 단일 코멘트로 끝 + edited 이벤트로 dispatch.

## 7. 운영 권장 (CAL-009 보완)

CAL-009 의 bot review 대응 프로토콜 보완:

1. **반박 reply 작성 시** — 본문 끝에 `@clawsweeper review` 한 줄 포함 (또는 게시 후 edit 으로 추가).
   reply 본문이 라우터에 전달되어야 bot 이 우리 반박 논거를 새 평가에 반영.
2. **반영 commit + push 시** — force-push 의 synchronize 가 자동 트리거. 추가 코멘트 불필요. 단 평가 코멘트 무반응 30분+ 면 명시 `@clawsweeper review` 로 보강.
3. **bot 무반응 진단 순서**:
   1. `gh pr view N --repo openclaw/openclaw --json labels` — 라벨 변화 (sufficient reset 등) 가 있으면 평가 진행 중
   2. `gh run list --repo openclaw/openclaw --workflow=clawsweeper-dispatch.yml` — workflow run SUCCESS 여부
   3. 둘 다 없거나 오래되면 명시 트리거 코멘트

## 8. 다른 bot 트리거

- **Greptile**: `openclaw-pr-tracker.md` §"Greptile 재리뷰 수동 트리거 절차" 참조.
  `@greptile review and provide confidence score` 등. force-push 자동 재리뷰 **없음** — 항상 수동.
- **barnacle-auto-response**: `scripts/github/barnacle-auto-response.mjs` — 별개 시스템.
  auto-response 코멘트 자동 생성. clawsweeper 와 분리.
