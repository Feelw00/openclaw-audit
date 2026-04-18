# 메인테이너 리뷰 대응 프로토콜 (R-10)

**근거**: `calibration/CAL-006-maintainer-review-tone.md`

openclaw 유지자 (CONTRIBUTING.md Maintainers 섹션 인물 또는 CODEOWNERS) 이 PR 에 `CHANGES_REQUESTED` / `COMMENT` 를 남기면 아래 절차를 엄수. bot 리뷰와 다르게 메인테이너 시간은 비싸고 **한 번의 신뢰도 훼손이 이후 모든 PR 에 영향**.

## 규율 (R-10)

### Step 1. 즉시 답하지 말 것

사용자가 "reply" 를 요청해도, 답변 **초안 작성 + cross-review** 먼저. 답변은 그 다음.

### Step 2. 3 agent cross-review 실행

`subagent_type: general-purpose` 세 개 병렬:

#### Agent 1: Positive
"메인테이너 지적이 현재 코드에 이미 반영된 상태인가? file:line 단위 검증."

#### Agent 2: Critical (가장 중요)
"메인테이너가 명시한 불변식을 **현재 코드가 실제로 충족하는가**? 반례 / edge case 를 적극 탐색. 메인테이너가 말하지 **않은** 관련 violation 이 같이 숨어있는지도 찾아라."

Critical 프롬프트에 반드시 포함:
- "The maintainer said: `<원문>`. Find edge cases that violate this contract in the current code."
- "Also check for *adjacent* violations of the same invariant the maintainer is protecting."
- 타입 시그니처 (Number, string, optional 등) 의 극단값을 명시적으로 다뤄라 — NaN, Infinity, negative, non-integer, 0, 경계값
- 의존된 helper (Math.round, Math.floor, clamp, typecast) 의 hidden 동작

#### Agent 3: Neutral
"메인테이너 리뷰를 여러 각도로 해석. 오해 / 부분 오해 / 정확한 지적 / stale review 가능성 각각 평가. 가장 높은 확률은?"

### Step 3. 결과 분기

| cross-review 결과 | 답변 톤 | 코드 조치 |
|---|---|---|
| 3/3 already-satisfied (stale review) | 사과 + 재검토 보고 + 현재 상태 요약 + 단순화 선택권 열기 | 추가 fix 없음 |
| critical 이 edge case 찾음 | 사과 + 메인테이너 지적 덕에 재검토 → 별개 violation 발견 + fix commit 보고 | 새 commit + 회귀 테스트 |
| 3/3 실제 위반 | 순수 사과 + 즉시 fix | fix + 테스트 |
| Neutral 이 "거절 of exception" 해석 | 순수 사과 + "simplify 반영하겠다" + 단순화 commit | simplification |

### Step 4. 답변 톤 체크리스트

- [ ] **첫 문장이 사과** ("Sorry — ...", "Apologies for ...", "Thanks for catching ...") 인가?
- [ ] "I think", "I believe", "actually" 같은 **주장 동사 없는가**?
- [ ] "If I'm misreading" / "if you could point to" 같은 **책임 전가 문구 없는가**?
- [ ] 코드 변경 필요하면 **commit 먼저 push 하고** 답변에 SHA 인용?
- [ ] 본문 끝에 **메인테이너가 선택할 여지** (simplification 옵션) 열어두기?
- [ ] Cross-review 에서 찾은 추가 edge case 를 **메인테이너 덕분** 으로 frame 했는가?

### Step 5. 편집 vs 새 comment

- 이미 단정/반박 톤 comment 가 나갔다면: **PATCH** (edit) 로 수정 + 짧은 follow-up comment 추가 (편집은 알림 안 감)
- 새 답변이면: 직접 새 comment

```bash
# 편집
gh api repos/openclaw/openclaw/issues/comments/<comment_id> \
  -X PATCH \
  -f body="$(cat /tmp/reply.md)"

# 알림용 짧은 follow-up
gh pr comment <pr> --repo openclaw/openclaw \
  --body "@<reviewer> — revised my earlier reply above with follow-up commit <sha>. ..."
```

## 안티 패턴 (금지)

1. ❌ "이미 구현됐다" 로 단정 시작
2. ❌ 메인테이너에게 "file:line 알려달라" 요청 (pushback)
3. ❌ cross-review 없이 답변
4. ❌ code 변경 없이 comment 만
5. ❌ 메인테이너 지적을 "오해" 로 단정
6. ❌ 여러 위협 trade-off 시 내 선호를 강하게 주장 (선택지 열어두기)

## 성공 패턴 (지향)

1. ✅ 첫 문장 사과
2. ✅ 메인테이너 지적이 이미 반영됐어도 감사 표현
3. ✅ Critical agent 가 찾은 추가 edge case 를 "덕분에 발견" 으로 frame
4. ✅ Fix commit 먼저 + comment 에 SHA + diff 링크
5. ✅ 선택지 명시 ("simplify 원하시면 바로 반영하겠다")
6. ✅ 테스트 pass matrix 를 표로 제시

## 예시 (CAL-006 에서 수정 후 comment)

> Sorry — my earlier reply landed as pushback when it should have been "let me re-check more carefully." That's on me, and I appreciate you spelling out the contract shape explicitly.
>
> After re-reading the review and running a deeper cross-check, the high-level `canHonorRetryAfter <=` / boundary test assertions are aligned with what you asked for — but the deeper pass surfaced a **separate real violation** of the same contract that my earlier comment missed:
>
> [edge case 설명]
>
> Fixed in `11430f641c`: [commit 요약]
>
> [테스트 매트릭스]
>
> If you'd still prefer we drop the `> maxDelayMs` symmetric exception entirely... say the word and I'll simplify. Otherwise this should match the contract you asked for. Thanks for the catch — the invariant is tighter now than before.

## 메인테이너 목록 참조

PR review 가 다음 인물에서 오면 R-10 자동 적용:
- `@steipete` (Peter Steinberger, Benevolent Dictator)
- `@thewilloftheshadow` (Shadow, Discord / ClawHub)
- `@tyler6204` (Tyler Yust, Agents/subagents, cron)
- `@obviyus` (Ayaan Zaidi, Telegram / Android — CAL-004 에서 upstream 먼저 merge 한 인물)
- `@gumadeiras` (Gustavo, Multi-agents / CLI / Performance / Plugins)
- `@cpojer` (Christoph Nakazawa, JS Infra)
- `@vincentkoc` (Vincent Koc, Agents / Telemetry)
- 전체: `openclaw/CONTRIBUTING.md` §Maintainers

CODEOWNERS (`/.github/CODEOWNERS`) 에 나오는 `@openclaw/secops`, `@openclaw/openclaw-release-managers` 팀도 메인테이너 권위로 취급.
