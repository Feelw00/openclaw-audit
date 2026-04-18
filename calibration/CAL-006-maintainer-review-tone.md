# CAL-006: 메인테이너 리뷰 톤 — PR #68543

**날짜**: 2026-04-19
**PR**: openclaw#68543
**reviewer**: @steipete (Peter Steinberger, Benevolent Dictator)

## 실수

메인테이너 `CHANGES_REQUESTED` 리뷰에 대해 **cross-review 없이 "이미 구현됐다" 로 단정적 답변**.

내 첫 comment 의 문제 문구:
> "If I'm misreading, a quick pointer to the specific file:line that still violates..."

책임 전가성 톤 + 메인테이너에게 "내가 놓친 게 있으면 네가 찾아와라" 식. Benevolent Dictator 급 인물에게 이런 톤은 신뢰도 직격.

실제로 cross-review 돌렸더니 critical agent 가 **별개의 real violation** (non-integer `retryAfterMs` + `Math.round` → 하방 위반) 을 찾아냄. 즉 내 "이미 구현됐다" 주장 자체가 부분적으로 **틀렸음**.

## 결과

사용자가 "치명적 문제" 라고 즉시 지적. 옳음.
- 메인테이너의 직접 review 는 bot review 보다 훨씬 비싼 자원
- 신뢰도 추락 한 번이면 이후 모든 PR 이 영향받음
- "이미 구현됐다" 라고 답하기 전 deeper 검증 먼저 필수

## 조치

1. **comment 편집** (issue-comment 4274083105):
   - pushback 톤 제거, 사과로 시작
   - 실제 찾은 edge case (non-integer round-down) 보고
   - commit 11430f641c 에 `Math.ceil` 로 fix
   - 기존 contract 경계 (`<=`) 는 그의 의견과 일치 확인
   - steipete 의 simplification 선택권 명시적으로 열어둠

2. **notification comment 추가** (4274098120): 편집만으론 steipete 에게 알림 안 가므로 짧은 cue

3. **실제 코드 수정** (commit 11430f641c):
   - `applyJitter` positive mode → `Math.ceil` (symmetric 은 그대로 `Math.round`)
   - 회귀 테스트: `retryAfterMs = 1.4` → `delays[0] >= 2`
   - 31/31 tests green

## 파이프라인 규율 추가 (R-10)

### R-10. 메인테이너 review 전엔 반드시 cross-review 선 실행
메인테이너 (특히 maintainers.md 에 올라있는 인물) CHANGES_REQUESTED / COMMENT 받으면:
1. 답변 쓰기 전에 3 agent cross-review 필수 (positive/critical/neutral)
2. 특히 critical agent 가 steipete 의 요구 외에도 **별개 edge case** 를 탐색하게 프롬프트
3. cross-review 결과 없이 "이미 구현됐다" 류 반박 금지
4. 답변 톤: 사과 + learning + 재검증 결과 보고 + 남은 simplification 선택권 열기

**이유**: bot review (Greptile/Codex) 는 rate limit 내 저비용 반복 가능하지만, 메인테이너 시간은 비싸고 신뢰도는 복구 어려움.

## 교훈 요약

- CAL-001: 메인테이너 post-merge reject (primary-path 오독)
- CAL-002: Greptile bot partial gap
- CAL-003: Cross-review self-catch
- CAL-004: Upstream merge lag
- CAL-005: Bot reviewer contradiction
- **CAL-006: 메인테이너 review 에 cross-review 없이 단정 반박** ← 가장 위험한 실수 (신뢰도 직격)

## 재발 방지 체크리스트

- [ ] 메인테이너 리뷰 받으면 → 즉시 답 금지, cross-review 먼저
- [ ] Critical agent 에게 "메인테이너가 놓친 edge case 도 함께 찾아라" 프롬프트
- [ ] 답변 초안 작성 후 톤 체크: 사과/학습 > 단정/반박
- [ ] 코드 수정이 필요하면 답변 전에 commit (pushback + 수정 커밋 분리하지 말기)
