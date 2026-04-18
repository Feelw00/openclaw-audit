---
name: openclaw-audit
description: openclaw (TypeScript/Node.js) 프로젝트의 신뢰성 결함(메모리·플러그인 lifecycle·cron concurrency·error boundary) 을 탐지·검증·이슈화하는 로컬 파이프라인. 페르소나 호출, FIND 카드 게이트키핑, gatekeeper 판정, GH 이슈 발행까지. 작업 디렉터리는 /Users/lucas/Project/openclaw-audit/.
---

# openclaw-audit Skill

openclaw 레포는 외부 OSS 이므로 **모든 작업 산출물은 openclaw-audit/ 안에** 둔다.
최종 PR (코드 수정 + 재현 테스트) 만 openclaw/ 로 들어간다.

## 스킬 위치

```
/Users/lucas/Project/openclaw-audit/
├── agents/             페르소나 (.md, authoritative)
├── grid.yaml           셀 인벤토리
├── schema/             finding / solution 스키마
├── findings/           drafts/ ready/ rejected/
├── issue-candidates/   CAND-NNN.md
├── solutions/          SOL-NNNN.md
├── local-state/        FSM (state.yaml + history.jsonl)
├── metrics/            shadow / human / consistency JSONL
├── domain-notes/       영구 도메인 지식
└── skills/openclaw-audit/
    ├── SKILL.md        (이 파일)
    └── harness/
        ├── local_state.py
        ├── validate.py
        ├── gatekeep.py
        └── publish.py
```

## 호출 모드

### 모드 1: 상태 확인 — "status" / "진행도"
```bash
python skills/openclaw-audit/harness/local_state.py show
ls findings/drafts findings/ready findings/rejected issue-candidates solutions 2>/dev/null
```

### 모드 2: 셀 실행 — "cell {id}" / "{id} 실행"
1. grid.yaml 에서 셀 state 를 `in_progress` 로 표시 (수동 편집)
2. 해당 페르소나 호출 (agents/{agent}.md)
3. 산출물: findings/drafts/FIND-{cell-id}-NNN.md

### 모드 3: 검증 — "validate"
```bash
python skills/openclaw-audit/harness/validate.py --all --move
```

### 모드 4: 클러스터링 — "cluster"
Agent 도구, clusterer 페르소나 호출.

### 모드 5: 게이트키핑 — "gatekeep {cand-id}"
```bash
python skills/openclaw-audit/harness/gatekeep.py sanitize CAND-001 > /tmp/gk-input.json
# Agent 도구 solution-gatekeeper → /tmp/gk-verdict.json
python skills/openclaw-audit/harness/gatekeep.py apply CAND-001 --verdict-json /tmp/gk-verdict.json --shadow
```

### 모드 6: 발행 — "publish {cand-id}"
```bash
python skills/openclaw-audit/harness/publish.py CAND-001               # dry-run
python skills/openclaw-audit/harness/publish.py CAND-001 --apply       # 실제 발행
```

## 호출 예시 (에이전트 프롬프트 템플릿)

### Phase 1 탐색 (memory-leak-hunter)
```
Agent 도구, subagent_type=Explore:

너는 memory-leak-hunter 페르소나 역할을 수행한다.
필수 선행: /Users/lucas/Project/openclaw-audit/agents/memory-leak-hunter.md 완전히 읽고 엄수.

openclaw repo: /Users/lucas/Project/openclaw
audit repo   : /Users/lucas/Project/openclaw-audit
셀          : plugins-memory
allowed_paths: src/plugins/**, src/plugin-sdk/**

산출물:
- findings/drafts/FIND-plugins-memory-{NNN}.md  최대 5건
- domain-notes/plugins.md 에 요약 섹션 추가

반환: 페르소나 Definition of Done 에 따른 한국어 요약
```

### Clusterer (ready/ 누적 시)
```
Agent 도구:

너는 clusterer 페르소나다. agents/clusterer.md 엄수.

대상: findings/ready/ 중 issue-candidates/ 에 없는 FIND
참고: domain-notes/*.md, issue-candidates/index.yaml

작업: 클러스터링 Step 1~4 적용, CAND 생성, index.yaml + domain-notes 갱신.

반환: CAND 수 (single/epic), 공통 원인 Top 3, 중복 경고.
```

## 빠른 참조 (자주 쓰는 명령)

```bash
# 진행 상태
python skills/openclaw-audit/harness/local_state.py show

# validate (dry-run)
python skills/openclaw-audit/harness/validate.py --all

# validate + 이동
python skills/openclaw-audit/harness/validate.py --all --move

# 특정 CAND 게이트키퍼 입력 생성
python skills/openclaw-audit/harness/gatekeep.py sanitize CAND-001

# drafter-gate (drift 체크)
python skills/openclaw-audit/harness/gatekeep.py drafter-gate CAND-001

# 발행 dry-run
python skills/openclaw-audit/harness/publish.py CAND-001

# 수동 상태 전이
python skills/openclaw-audit/harness/local_state.py set FIND-xxx --to discovered --actor manual
```

## 환경

```bash
# venv
python3 -m venv /tmp/openclaw-audit-venv
/tmp/openclaw-audit-venv/bin/pip install pyyaml

# openclaw repo 위치 (환경변수로 override 가능)
export OPENCLAW_ROOT=/Users/lucas/Project/openclaw
```

## 주의사항

- openclaw repo 에 커스텀 라벨 생성 금지
- GH Issue 발행은 사람 승인 후에만 (publish.py 기본 dry-run)
- FIND 본문은 append-only (mutable: status, cross_refs, solution_refs, rejected_reasons)
- 메인테이너 가이드: bug/critical/reliability/stability 외 의 발견은 낮은 우선순위
