# 운용 가이드 — openclaw 신뢰성 감사

원본(pethroomcare perf-audit) 에서 포팅. 라벨 파이프라인 제거, 로컬 FSM 채택.

## 1. 5분 오리엔테이션

### 이 파이프라인이 하는 일
openclaw 코드베이스 (TS/Node.js) 의 신뢰성 결함 (memory leak / plugin lifecycle gap /
cron concurrency / error boundary gap) 을 LLM 에이전트로 탐지 → 게이트키핑 → GH Issue 발행 +
재현 테스트 드래프트 → PR 까지 진행.

**결정적으로 다른 점** (pethroom 대비):
- openclaw 는 남의 OSS → 커스텀 라벨 없음, FSM 은 local-state/state.yaml 에만.
- Issue 발행 전에 **반드시 재현 테스트** 를 준비 (SECURITY.md 기준 "재현 + 영향 + 해결책").
- 1 PR = 1 bug 원칙. XS 크기 우선.

### 현재 지점
```bash
python skills/openclaw-audit/harness/local_state.py show
ls findings/ issue-candidates/ solutions/
```

### 핵심 파일
| 영역 | 위치 |
|---|---|
| 페르소나 | `agents/*.md` |
| 스키마 | `schema/{finding,solution}.schema.yaml` |
| 셀 | `grid.yaml` |
| 하네스 | `skills/openclaw-audit/harness/*.py` |
| 상태 | `local-state/state.yaml` |

## 2. 자동화 수준

### 결정적 (언제든 실행 OK)
- `validate.py` (B-1 게이트)
- `gatekeep.py apply` 후처리 (verdict JSON 검증)
- `gatekeep.py drafter-gate` (drift 체크)
- `publish.py` dedup (fingerprint)

### Shadow mode (LLM 판정은 있되 자동 전이 안 함)
- memory-leak-hunter, cron-reliability-auditor, clusterer, solution-gatekeeper
- 이유: shadow 50건 + human 10건 + self-consistency 10건 누적 전까지.
- 메인 세션이 사람처럼 수행 + metrics/*.jsonl 에 기록 축적.

### GitHub Actions (의도적 없음)
- openclaw repo 에 워크플로우 추가 금지.
- 모든 검증은 이 머신에서 로컬 실행.

## 3. 한 셀 → PR 표준 워크플로

예: `cron-concurrency` 셀.

### Step 1. 셀 선택
```
grid.yaml 에서 state 를 'in_progress' 로 편집
```

### Step 2. 페르소나 호출 (탐색)
```
Agent 도구, subagent_type=Explore:

너는 cron-reliability-auditor 페르소나다.
필수 선행: /Users/lucas/Project/openclaw-audit/agents/cron-reliability-auditor.md 완전히 읽고 엄수.

openclaw repo: /Users/lucas/Project/openclaw
셀          : cron-concurrency
allowed_paths: src/cron/**

산출물: findings/drafts/FIND-cron-concurrency-NNN.md 최대 5건
```

### Step 3. 게이트 통과
```bash
python skills/openclaw-audit/harness/validate.py --all --move
```
통과 → `findings/ready/`, 반려 → `findings/rejected/`.

흔한 반려:
- `B-1-2c evidence mismatch` — 공백 차이. 파일 직접 Read 후 evidence 블록 교체.
- `B-1-3 title > 80자` — 제목 축소 (본문 # 헤더도 동기화).
- `B-1-5 root_cause_chain < 3` — 깊이 보강.
- `B-1-7 counter_evidence 비어있음` — 반증 탐색 섹션 보강.

### Step 4. 반려 수정 (필요 시)
에이전트 재호출 (rejected finding fixer) → drafts/ 로 복귀 → validate 재실행.

### Step 5. 클러스터링 (ready 가 누적되면)
```
Agent 도구, clusterer 페르소나
```
→ `issue-candidates/CAND-NNN.md` 생성.

### Step 6. Gatekeeper (shadow 수집용)
```bash
# 1. sanitize
python skills/openclaw-audit/harness/gatekeep.py sanitize CAND-001 > /tmp/gk-CAND-001.json

# 2. agent 호출
#    Agent 도구, solution-gatekeeper 페르소나
#    입력: /tmp/gk-CAND-001.json
#    출력: /tmp/gk-CAND-001-verdict.json (순수 JSON)

# 3. apply (shadow — FSM 전이 안 함, 판정만 기록)
python skills/openclaw-audit/harness/gatekeep.py apply CAND-001 \
  --verdict-json /tmp/gk-CAND-001-verdict.json --shadow

# 또는 실전 (gatekeeper-approved or needs-human-review 전이)
python skills/openclaw-audit/harness/gatekeep.py apply CAND-001 \
  --verdict-json /tmp/gk-CAND-001-verdict.json
```

### Step 7. 재현 테스트 드래프트 (사람 + Claude)
MVP 에서는 `solution-drafter` 페르소나를 쓰지 않고, 사람이 SOL 카드를 수동 작성:

1. `solutions/SOL-NNNN.md` 파일 신규 작성 (schema/solution.schema.yaml 참고)
2. `repro_test_draft` 에 vitest 테스트 코드 블록 — 수정 전에는 실패, 수정 후에는 성공해야 함
3. `fix_approach_candidates` 최대 3개 + tradeoff
4. `chosen_fix` 선택

### Step 8. Drafter-gate (drift 체크)
```bash
python skills/openclaw-audit/harness/gatekeep.py drafter-gate CAND-001
```
통과 → 안전하게 PR 작성 단계로. 실패 → 파일이 바뀌었으니 gatekeeper 재실행.

### Step 9. openclaw 에 재현 테스트 이식 + 수정
```bash
# 재현 테스트를 openclaw 내부로 복사
cp test-drafts/SOL-NNNN.test.ts /Users/lucas/Project/openclaw/<repro_test_target_file>

# 실패 확인
cd /Users/lucas/Project/openclaw
pnpm test <path>      # 반드시 실패 (재현 증명)

# 수정 구현 (소스 편집)
# ...

# 성공 확인
pnpm test <path>      # 성공
pnpm check            # lint/type OK
pnpm build            # build OK
```

### Step 10. Issue 발행
```bash
# dry-run
python skills/openclaw-audit/harness/publish.py CAND-001

# 실제 (사람 승인 후)
python skills/openclaw-audit/harness/publish.py CAND-001 --apply
```

### Step 11. PR
```bash
cd /Users/lucas/Project/openclaw
gh pr create --title "..." --body "Closes #<issue>. ..."
```

## 4. 자주 쓰는 명령

```bash
# 상태 덤프
python skills/openclaw-audit/harness/local_state.py show

# 특정 item 조회
python skills/openclaw-audit/harness/local_state.py get FIND-plugins-memory-001

# 수동 상태 전이 (디버그용)
python skills/openclaw-audit/harness/local_state.py set FIND-xxx --to discovered --actor manual
```

## 5. 흔한 실패·대처

| 증상 | 원인 | 대처 |
|---|---|---|
| B-1-2c evidence mismatch | 공백·줄바꿈 차이 | 파일 직접 Read 후 evidence 블록 교체 |
| B-1-5 concrete < 2 | root_cause_chain 의 evidence_ref 가 N/A 만 | 파일:라인 최소 2개 채움 |
| B-1-1 path scope | file 이 cell 의 allowed_paths 밖 | 올바른 셀 사용 또는 grid.yaml 업데이트 |
| B-1-7 counter_evidence | 반증 탐색 누락 | 최소 2 카테고리 탐색 결과 기록 |
| gatekeeper grounding fail | rationale 에 whitelist 외 파일 참조 | gatekeeper 재호출 (자동 needs-human-review) |
| drafter-gate G-5 drift | 증거 파일 수정됨 | gatekeeper 재실행 (새 fingerprint) |
| publish dedup | 유사 issue 이미 있음 | 해당 issue 에 댓글 추가 or --force |

## 6. 데이터 수집 졸업 조건 (Shadow → 자동화)

`report.py` 가 아직 포팅되지 않았으므로 임시로:
```bash
wc -l metrics/*.jsonl
```

원본 파이프라인 졸업 조건:
- [ ] `shadow-runs.jsonl` ≥ 50
- [ ] `human-verdicts.jsonl` ≥ 10
- [ ] `self-consistency.jsonl` ≥ 10

졸업 후 활성화 순서: auto-cluster → auto-gatekeep → auto-drafter.

## 7. 범위 밖 (명시)

- openclaw repo 에 커스텀 라벨 생성
- openclaw CI / workflows 수정
- openclaw CLAUDE.md / AGENTS.md / CONTRIBUTING.md 수정
- `@ts-nocheck` 또는 린트 억제
- 리팩터만 하는 PR
- CI 수정 PR
- feature 제안

## 8. 지연된 항목 (MVP 이후 추가)

MVP 에서는 제외:
- `verify.py` (drafter-gate 가 drift 체크 대체)
- `draft.py` + `solution-drafter` 페르소나 (사람이 수동으로 SOL 작성)
- `report.py` (임시로 `wc -l metrics/*.jsonl`)
- 추가 페르소나: `plugin-lifecycle-auditor`, `error-boundary-auditor`, `concurrency-auditor`
  (memory-leak-hunter + cron-reliability-auditor 로 우선 4셀 수행)

첫 셀이 PR 까지 성공하면 단계적 추가.

## 9. 세션 시작 3 명령

```bash
cd /Users/lucas/Project/openclaw-audit
python skills/openclaw-audit/harness/local_state.py show | head -40
ls findings/drafts/ findings/ready/ issue-candidates/ solutions/ 2>/dev/null | head
grep -E "state:\s*in_progress" grid.yaml
```
