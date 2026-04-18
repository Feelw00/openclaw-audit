# openclaw 기여 규칙 (요약)

출처: `openclaw/CONTRIBUTING.md`, `openclaw/.github/pull_request_template.md`, `openclaw/.github/CODEOWNERS`.
PR 작성 전 이 파일 스캔 필수.

## 1. PR 수용 범위

| 종류 | 수용? |
|---|---|
| Bug fix / 작은 수정 | ✅ PR 바로 |
| 새 feature / 아키텍처 변경 | ❌ 먼저 Discussion 또는 Discord |
| **리팩터만** 하는 PR | ❌ 금지 (메인테이너 요청 시에만) |
| **테스트/CI 만** main 실패 잡으려는 PR | ❌ 금지 (이미 추적 중) |

우리 파이프라인 모든 CAND 는 bug fix 범주 → OK.

## 2. 하드 한계

- **저자당 열린 PR 10개** — 초과 시 `r: too-many-prs` 라벨 + 자동 close.
- 우리는 아직 0 PR, 충분히 여유.

## 3. PR 전 필수 검사

```bash
# openclaw repo 에서
pnpm build && pnpm check && pnpm test

# 플러그인 변경 시
pnpm test:extension <extension-name>

# 공유 플러그인/채널 표면 변경 시
pnpm test:contracts

# Codex 접근 가능하면
codex review --base origin/main
```

## 4. PR 스코프 규칙

- **one thing per PR** — 관련 없는 수정 섞지 말 것.
- 우리 CAND 매핑:
  - CAND-002 (epic, FIND-001 + FIND-003) → **반드시 분리** (2개 파일, 2개 이슈) — locked.ts 와 timer.ts 는 각각 별개 PR.
  - CAND-001 (single, FIND-plugins-memory-003) → 단독 PR
  - CAND-003 → **retract** (gatekeeper reject_suspected@high)
  - CAND-004 (single, FIND-agents-registry-memory-002) → 단독 PR ✅

## 5. CODEOWNERS 제약 (중요)

`.github/CODEOWNERS` 에서 `@openclaw/secops` 소유 파일은 **소유자가 명시적으로 요청한 경우에만** 수정.

### 우리 CAND 와 CODEOWNERS 교차 검증

| CAND | 주요 수정 파일 | CODEOWNERS | 작업 가능? |
|---|---|---|---|
| CAND-001 | `src/plugins/runtime/runtime-web-channel-plugin.ts` | 미포함 | ✅ |
| CAND-002 (FIND-001) | `src/cron/service/locked.ts` | 미포함 | ✅ |
| CAND-002 (FIND-003) | `src/cron/service/timer.ts` | 미포함 | ✅ |
| CAND-003 (FIND-002) | `src/cron/service/jobs.ts` | **@openclaw/secops ⚠️** | ❌ (어차피 reject) |
| CAND-004 | `src/agents/subagent-registry.ts` | 미포함 | ✅ |

secops 소유 (참고):
- `/src/security/`, `/src/secrets/`
- `/src/gateway/*auth*`, `/src/gateway/*secret*`
- `/src/agents/*auth*`, `/src/agents/sandbox*`
- `/src/cron/stagger.ts`, `/src/cron/service/jobs.ts`
- `/SECURITY.md`, `/docs/security/`

## 6. PR 본문 템플릿 (필수 섹션)

openclaw/.github/pull_request_template.md 에서 발췌. 필수:

1. **Summary** (2-5 bullets): Problem / Why it matters / What changed / What did NOT change
2. **Change Type**: Bug fix / Refactor required for the fix 등 체크
3. **Scope** (touched areas 체크)
4. **Linked Issue**: `Closes #N`
5. **Root Cause** (버그 수정 시 필수): 왜 발생했는가, missing guardrail
6. **Regression Test Plan**: 최소 신뢰할 수 있는 테스트 커버리지. 해당 테스트 파일.
7. **Security Impact** (required): 새 permission? secrets 변경? network call? 데이터 범위?
8. **Repro + Verification**: Environment / Steps / Expected / Actual
9. **Evidence**: failing test before + passing after (**필수**)
10. **Human Verification**: 사람이 직접 확인한 것
11. **Review Conversations**: bot comment 처리 체크
12. **Compatibility / Migration**
13. **Risks and Mitigations**

## 7. AI-assisted PR 정책

openclaw 는 AI 지원 PR 환영 (first-class). 단 명시 필수:
- PR title/description 에 AI-assisted 표시
- 테스트 정도 명시 (untested / lightly tested / fully tested)
- 세션 로그/프롬프트 포함 권장
- 코드 이해도 확인 체크
- Codex 있으면 `codex review --base origin/main` 실행 후 결과 반영

## 8. 언어

- **American English** (color, behavior, analyze 등)
- PR 본문도 영어

## 9. 커밋

```bash
# scripts/committer 사용 (staging 스코핑)
scripts/committer "CLI: add verbose flag to send" src/cli/send.ts
# 빠른 로컬 커밋 (equivalent 검증 이미 돌린 경우)
scripts/committer --fast "WIP local" <files>
```

- commit message: 간결, action-oriented
- 관련 없는 변경 분리

## 10. 브랜치 / fork

- openclaw repo 에 push 권한 없으면 fork → feature branch → PR
- 10 PR 한계는 상류 repo 기준

## 11. 보안 취약점 리포트 (vulnerability)

일반 bug PR 이 아니라 **보안 취약점** 이면:
- security@openclaw.ai 로 비공개 보고
- GitHub Security Advisory 사용
- 필수 필드: Title / Severity / Impact / Component / Technical Repro / Demonstrated Impact / Environment / Remediation

우리 CAND 는 reliability/memory 버그 (보안 아님) → 일반 PR 흐름.

## 12. 파이프라인 별 PR 준비 체크리스트

CAND 별 PR 생성 전:

- [ ] gatekeeper 판정이 approve 또는 uncertain→사람 승인 완료
- [ ] 재현 테스트 드래프트 (vitest) 가 openclaw 에서 수정 전 fail, 수정 후 pass 확인
- [ ] 수정 파일이 CODEOWNERS @openclaw/secops 아님 (또는 소유자 동의 있음)
- [ ] `pnpm build && pnpm check && pnpm test` 전부 green
- [ ] 수정 scope 가 single bug (one thing per PR)
- [ ] PR 제목: `fix(<scope>): <summary>` 형식
- [ ] PR 본문: 12개 템플릿 섹션 채움
- [ ] AI-assisted 표시
- [ ] 저자당 열린 PR 수 확인 (10 미만)
- [ ] 관련 Issue 발행 후 `Closes #N` 연결
