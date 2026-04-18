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
- **skills/openclaw-audit/harness/** — Python 하네스 (validate / gatekeep / publish)
- **findings/** — drafts / ready / rejected
- **issue-candidates/** — clusterer 산출물
- **solutions/** — 수정 접근법 카드
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

## 파이프라인 상태 (Phase 1)

| 셀 | 상태 |
|---|---|
| plugins-memory | planned |
| plugins-lifecycle | planned |
| cron-concurrency | planned |
| agents-registry-memory | planned |
| infra-process-error-boundary | planned |

## MVP 스코프

- ✅ validate.py, gatekeep.py, publish.py, local_state.py
- ✅ memory-leak-hunter, cron-reliability-auditor, clusterer, solution-gatekeeper 페르소나
- ✅ finding/solution 스키마, grid 5셀, operations 가이드
- ⏳ verify.py, draft.py, report.py (지연)
- ⏳ plugin-lifecycle-auditor, error-boundary-auditor, concurrency-auditor, solution-drafter 페르소나 (지연)
