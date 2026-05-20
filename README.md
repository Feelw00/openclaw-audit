# openclaw-audit

openclaw(TypeScript/Node.js OSS) 의 신뢰성 결함 탐지·이슈화 파이프라인.
원본 아이디어: pethroomcare perf-audit (Spring). 이 포팅판은 **openclaw 외부** 에 둔다 — openclaw repo 오염 방지.

## 왜 외부에?

openclaw 메인테이너는 feature freeze + bug/critical/reliability 만 받는 상태.
파이프라인 자체를 openclaw repo 에 넣을 수도, 커스텀 라벨을 만들 수도 없다.
→ 파이프라인은 이 디렉터리에서만 돌고, openclaw 에는 **최종 PR(수정 + 재현 테스트)** 만 제출.

## 구성

- **agents/** — 페르소나 프롬프트 (.md, authoritative)
- **grid.yaml** — 셀(도메인 × 타입) 인벤토리
- **schema/** — finding / solution YAML 스키마
- **skills/openclaw-audit/harness/** — Python 하네스 (validate / gatekeep / publish / dedup / local_state)
- **skills/cross-review/** — 다중 agent cross-review skill
- **skills/real-behavior-proof/** — SOL 전/후 production env 결함 재현·검증 skill
- **findings/** — drafts / ready / rejected
- **issue-candidates/** — clusterer 산출물
- **solutions/** — 수정 접근법 카드
- **proofs/** — real behavior proof evidence (PROOF-*.md)
- **test-drafts/** — 재현 테스트 드래프트 (openclaw 이식 대기)
- **calibration/** — CAL-* 캘리브레이션 기록
- **adr/** — 아키텍처 결정 기록
- **local-state/** — FSM 상태 (GH 라벨 대체)
- **domain-notes/** — 영구 도메인 지식
- **metrics/** — shadow / human / consistency 판정 JSONL

## 빠른 시작

```bash
# 의존성
python3 -m venv /tmp/openclaw-audit-venv
/tmp/openclaw-audit-venv/bin/pip install pyyaml
export PATH=/tmp/openclaw-audit-venv/bin:$PATH

# 스모크
python skills/openclaw-audit/harness/local_state.py show
python skills/openclaw-audit/harness/validate.py --all
python -c "import yaml; yaml.safe_load(open('grid.yaml'))"
```

상세: `OPERATIONS.md`.

## 파이프라인 상태

grid.yaml 셀 인벤토리 35개 (도메인 × 결함 타입). 초기 5셀 Phase 1 이후 전 도메인으로 확장 완료.

| 상태 | 셀 수 | 의미 |
|---|---|---|
| done | 28 | 감사 완료 |
| integrated | 6 | 후속 사이클에 흡수 |
| zero_find | 1 | 감사 후 유효 결함 없음 |

도메인: plugins / cron / agents-registry / infra-process / infra-retry / gateway / channels / auto-reply / context-engine / mcp.

산출: Solution Card 13건 (SOL-0001~0013) + openclaw PR 발행. 머지·credit 내역은 `openclaw-pr-tracker.md` 참조.

## 하네스·페르소나 현황

- ✅ 하네스: validate.py / gatekeep.py / publish.py / dedup.py / local_state.py
- ✅ 페르소나 7종: memory-leak-hunter, cron-reliability-auditor, concurrency-auditor, plugin-lifecycle-auditor, error-boundary-auditor, clusterer, solution-gatekeeper
- ✅ finding/solution 스키마, grid 35셀, operations 가이드
- ✅ cross-review / real-behavior-proof skill
- verify.py / draft.py / report.py, solution-drafter 페르소나는 미구현 — 현 워크플로에서 미사용
