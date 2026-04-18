# CAL-007: stale upstream 기반 FIND 생성 (CAL-004 재발 변종)

**날짜**: 2026-04-19
**셀**: plugins-error-boundary (Phase 2)
**관련 CAL**: CAL-004 (upstream merge lag)
**영향**: FIND-plugins-error-boundary-001/002/003 3건 전부 stale 코드 기반

## 상황

세션 부팅 시 `git fetch upstream main` 은 실행했으나 **local main 을 fast-forward 하지 않음**. local main 이 upstream/main 대비 **913 commits behind** 인 상태에서 error-boundary-auditor / memory-leak-hunter 페르소나를 투입.

페르소나는 `/Users/lucas/Project/openclaw/` 디렉터리를 Grep/Read — 이 디렉터리가 stale 상태라 이미 upstream 에서 수정된 결함을 false positive 로 재탐지.

## 구체 증상

FIND-plugins-error-boundary-001 (P0 claim):
> `bundled-capability-runtime.ts:301 void register(captured.api)` async Promise escape → process fatal

Upstream 최신 (8879ed153d) 에서 동일 라인:
```ts
const captured = createCapturedPluginRegistration();
register(captured.api);   // ← `void` 키워드 제거됨 (upstream fix 상태)
```

즉 stale main 에는 `void register(...)` 있었으나 upstream 에서 이미 제거된 상태. CAND 생성 → PR 제출했으면 CAL-004 와 동일하게 "이미 upstream 에 merged" 사유로 close 됐을 것.

## 근본 원인

### 1. 부팅 절차가 fetch 만 있고 merge 없음

NEXT.md §1 은 `git fetch upstream main` 만 명시했을 뿐, local main 을 `pull --ff-only` 로 업데이트하는 단계 부재. fetch 는 `upstream/main` ref 만 갱신하고 working tree 는 stale 그대로.

### 2. fork repo 동기화 단계 부재

작업 후 fork (origin) 을 upstream 과 sync 하지 않으면 PR base 가 stale 가능성. CAND-005 계열 Upstream merge lag 와 겹침.

### 3. 페르소나 투입 전 staleness gate 부재

`git rev-list --count HEAD..upstream/main > 0` 여부 확인하는 gate 가 파이프라인 어디에도 없음.

## 보완 조치 (이 커밋 포함)

### A. NEXT.md §1 부팅 절차 개편

- B 섹션: FIND/CAND 작업 시작 전 **반드시** local main fast-forward + fork push.
- BEHIND 카운트 출력 + 0 이 아니면 조건부 pull + push.

### B. 페르소나 호출 프롬프트에 staleness 언급 금지

페르소나가 "이미 upstream 에 fix 있는지" 판단은 R-8 (CAL-004 기반) 로 이미 규율화됨. 추가 부하 X. 대신 페르소나 invoke 전 **오퍼레이터 책임** 으로 repo 최신화.

### C. 이미 생성된 FIND 3건 처리

- ready/ 에서 rejected/ 로 이동
- rejected_reasons: 'CAL-007: stale upstream base at time of audit (local main 913 commits behind upstream/main). evidence 라인 내용 upstream 에서 이미 변경/수정됨.'
- upstream 최신화 후 페르소나 재투입 예정

## 재실수 방지 체크리스트

다음 세션 부팅 시:
- [ ] `git rev-list --count HEAD..upstream/main` 결과 == 0 확인
- [ ] 0 아니면 `git pull upstream main --ff-only` + `git push origin main`
- [ ] 완료 후에만 페르소나 invoke

FIND/CAND 생성 의심될 때:
- [ ] 페르소나가 참조한 `file:line_range` 가 current upstream 에도 동일한가?
- [ ] `git log upstream/main -- <file>` 에서 최근 수정된 경우 특히 주의
