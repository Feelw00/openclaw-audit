# CLAUDE.md

openclaw 프로젝트의 신뢰성 감사 파이프라인 홈.

## 작업 원칙

- 답변과 문서는 **한국어**로 작성.
- openclaw 는 남의 OSS 저장소 — openclaw/ 디렉터리 내부에는 최종 PR 시에만 손댄다.
- 모든 파이프라인 산출물은 이 디렉터리 (openclaw-audit/) 안에 보관.
- openclaw repo 의 라벨/워크플로우/CI 수정 금지.
- FIND/CAND/SOL 파일의 본문은 append-only. `status`, `cross_refs`, `solution_refs`, `rejected_reasons` 만 mutable.

## 디렉터리 매핑

| 경로 | 역할 |
|---|---|
| `agents/` | 페르소나 (.md) — authoritative |
| `grid.yaml` | 셀 인벤토리 |
| `schema/` | finding / solution YAML 스키마 |
| `findings/drafts/` | 에이전트 초안 (ephemeral) |
| `findings/ready/` | validate 통과 (ephemeral) |
| `findings/rejected/` | 반려 (6개월 보존) |
| `issue-candidates/` | clusterer 출력 (ephemeral) |
| `solutions/` | Solution Card (영구) |
| `test-drafts/` | 재현 테스트 드래프트 (openclaw/ 이식 대기) |
| `proofs/` | real behavior proof evidence (PROOF-{target}-{pre\|post}-{ts}.md, 영구) |
| `local-state/` | FSM 상태 (state.yaml + history.jsonl) |
| `metrics/` | shadow/human/consistency JSONL |
| `domain-notes/` | 영구 도메인 지식 |
| `skills/openclaw-audit/` | 파이프라인 skill + harness |
| `skills/cross-review/` | 다중 agent cross-review skill (post-harness/pre-pr/maintainer-response) |
| `skills/real-behavior-proof/` | SOL 전/후 production env 결함 재현/검증 skill (pre-sol/post-sol) |

## Bot 리뷰 트리거 (openclaw)

`clawsweeper-triggers.md` 에 정리. clawsweeper 자동/명시 트리거 + regex + 라벨
시스템 + Greptile 비교. **PR push 후 bot 무반응 / 우리 reply 가 평가에 반영
안 되는 듯할 때 우선 참조**.

핵심: reply 본문에 `@clawsweeper review` 키워드 없으면 router dispatch 안 됨.
force-push 의 synchronize 는 sha 기반 재리뷰 자동 트리거 OK.

## openclaw 기여 규칙 필독

`openclaw-contribution.md` 에 정리됨 (CONTRIBUTING.md + pull_request_template.md + CODEOWNERS 통합). PR 준비 전 반드시 스캔.

핵심:
- one thing per PR (관련 없는 수정 섞지 말 것)
- 저자당 열린 PR 10개 한계
- 리팩터만 또는 CI-만 PR 금지
- `pnpm build && pnpm check && pnpm test` 전부 green 필수
- CODEOWNERS 제약: `/src/cron/service/jobs.ts`, `/src/cron/stagger.ts`, `/src/agents/*auth*`, `/src/agents/sandbox*` 등 → 소유자 동의 없이 수정 금지
- AI-assisted 표시 필수
- PR 본문 12섹션 (Root Cause / Regression Test Plan / Security Impact 등) 채움

## 메인테이너 가이드 (openclaw)

인용:
> We cant GUARNTEE we will merge / review every PR. Even if its XS and Greptile 5/5.
> Our focus right now is bugs, critical issues, reliability and stability.
> We are frozen on feature work / changes.
>
> Where we need the most help: Core issues like memory, plugin loading, cron, reliability.

즉 — 이 파이프라인은 **core 의 memory / plugin / cron / reliability** 에만 집중한다.
feature 제안, 리팩터만 하는 PR, CI 수정 PR 은 금지 (CONTRIBUTING.md 참조).

## openclaw 개발 규칙 (PR 시 준수)

- 저자당 열린 PR 10개 하드 한계.
- 사전 검사: `pnpm build && pnpm check && pnpm test` 전부 green.
- PR 크기 XS 를 목표 (M 이상 분할).
- `@ts-nocheck` 같은 린트 억제 금지.
- `Result<T, E>` 결과, 닫힌 에러 코드 사용 (freeform string 금지).
- 같은 모듈에 `await import()` + 정적 `import` 혼합 금지 (`*.runtime.ts` 경계 사용).
- 테스트: per-instance 스텁, 프로토타입 변경 금지.
- 보안 민감 경로 수정 시 `.github/CODEOWNERS` 확인.

## 세션 시작 — `NEXT.md` 를 먼저 열어라

**새 세션에서 가장 먼저**: `NEXT.md` 의 결정 트리에 따라 다음 액션 선택.
이 CLAUDE.md 는 규칙 참조용, NEXT.md 는 실행 가이드.

### NEXT.md 작성 규율 (강제)

- **NEXT.md 는 부팅용 파일** — 다음 액션 결정 트리 + 현재 활성 상태 (OPEN PR /
  진행 중 사이클 목록 + 다음 우선순위) 만.
- **히스토리성 이력 작성 금지**: 완료된 사이클의 진행 디테일 / 폐기 사유 /
  과거 시도 / 버전별 outcome 변천사는 NEXT.md 에 *절대* 적지 않는다.
  부팅용 파일을 무겁게 만들지 말 것.
- 히스토리는 git log + commit 메시지 + `solutions/SOL-*.md` 본문 +
  `proofs/PROOF-*.md` + `openclaw-pr-tracker.md` + `calibration/CAL-*.md` 에
  보관한다. NEXT.md 가 참조 링크만 두면 충분.
- 사이클이 종료되면 NEXT.md 의 해당 항목은 *삭제* 한다 (몇 줄로 압축 X,
  외부 파일 위임). active queue + 다음 결정에 필요한 정보만 남긴다.

## 세션 시작 체크리스트

```bash
cd /Users/lucas/Project/openclaw-audit

# 1. 상태 확인
python skills/openclaw-audit/harness/local_state.py show

# 2. 진행 중 셀
grep -E "state:\s*in_progress" grid.yaml

# 3. drafts 미검증
ls findings/drafts/

# 4. ready 쌓임
ls findings/ready/

# 5. CAND 대기열
ls issue-candidates/
```

그 다음 `OPERATIONS.md` 의 워크플로 참고.
