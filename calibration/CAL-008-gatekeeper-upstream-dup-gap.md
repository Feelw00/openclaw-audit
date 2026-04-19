# CAL-008: Gatekeeper 단독으로 upstream-dup 미탐지 (CAL-004 재발 예방)

**날짜**: 2026-04-19
**관련 CAND**: CAND-016 (agentRunStarts asymmetry)
**관련 CAL**: CAL-004 (upstream merge lag)
**영향**: cross-review 가 최종 단계에서 탐지 했으나, gatekeeper 단독 파이프라인이면 CAL-004 재발할 뻔함

## 상황

2026-04-19 Phase 3 감사에서 CAND-016 (gateway agentRunStarts Map TTL/cap 부재) 이 gatekeeper 단독 판정 `approve@medium` 받음.

이후 cross-review 5-agent 실행 중 **upstream-dup-checker 가 PR #68801 (2026-04-19 03:25Z OPEN) 발견** — 동일한 agentRunStarts TTL sweep fix 를 다른 기여자가 이미 진행 중.

## 근본 원인

Gatekeeper 의 필수 탐색 카테고리 중 **upstream-dup check 가 누락**. 카테고리 목록 (`숨은 방어, 호출 빈도, 테스트 커버, 설정, 주변 맥락, primary-path inversion, hot-path consistency`) 중 "이미 누가 고치고 있는가" 확인 단계 부재.

CAL-004 (CAND-005 upstream superseded day before PR) 는 **부팅 시 upstream fetch + 영역 변경 확인** 수준까지만 반영됐고, **CAND 별 gatekeeper 단계에서 per-CAND 로 git log / gh pr list 검색** 을 강제하지 않음. 2주간 upstream 전체 변경은 확인해도, 특정 심볼/파일 수준의 open PR 검색은 cross-review 에서만 수행됐다.

## 보완 조치 (Option C 하이브리드 — 이 커밋 포함)

### A. Gatekeeper 페르소나 개조 — upstream-dup check 필수 카테고리

`agents/solution-gatekeeper.md` 의 `Counter-evidence 탐색` 표에 항목 추가:

```
| upstream-dup check (필수, CAL-004/CAL-008) | 동일/유사 fix 가 upstream merged 또는 open PR 에 있는가? |
```

새 섹션 "Upstream dup 검사" 에 실행 명령 명시:
```bash
git log upstream/main --since="4 weeks ago" -- <file_path>
git show upstream/main:<file_path> | sed -n '<range>'
gh pr list --repo openclaw/openclaw --search "<symbol_or_file>" --state all
```

탐지 규칙:
- upstream merged fix 존재 → `reject_suspected`
- open PR 존재 → `reject_suspected` (CAL-008 원천)
- closed PR 의도적 wontfix → `reject_suspected`

`explored_categories` 에 `"upstream-dup check"` 반드시 포함. 미포함 시 gatekeep.py apply 가 `needs-human-review` 로 라우팅.

### B. Cross-review 트리거에 severity gate

`NEXT.md §7.1` 업데이트:
- P0/P1/P2: cross-review 필수 (메인테이너 visibility + false positive 비용 큼)
- P3 approve: skip cross-review (gatekeeper 의 primary-path + upstream-dup 으로 충분)
- P3 uncertain: 기본 abandon (과거 CAND-007/008 패턴), cross-review 는 요청 시만

근거:
- Gatekeeper 가 upstream-dup + primary-path 둘 다 돌리면 P3 단순 leak 은 단독 판단 신뢰 가능
- Cross-review 5 agent 는 자원 비용. P3 에 투입은 오버엔지니어링
- P2+ 는 PR 되면 메인테이너 관계 비용 발생 → 추가 필터 가치 존재

### C. 메트릭 추가

`metrics/gatekeeper-upstream-dup-hits.jsonl` (신규) — gatekeeper 가 upstream-dup 을 직접 탐지한 건수 추적. cross-review 없이도 이 규칙으로 차단된 case 누적. 졸업 조건 새 지표.

## 예상 효과 (다음 10 CAND 시뮬레이션)

| 시나리오 | 현행 | Option C |
|---|---|---|
| gatekeeper 호출 | 10 | 10 (+ upstream check 1회씩) |
| cross-review 호출 | 10 × 5 = 50 | P2+ 만 ≈ 3~4 × 5 = 15~20 |
| 총 agent 호출 | 60 | 25~30 |
| CAL-004 재발 가능성 | 중간 (cross-review 가 catch) | 낮음 (gatekeeper 가 catch + cross-review 2중 안전장치) |

**비용 50% 절감 + CAL-004 차단력 강화**.

## 재실수 방지 체크리스트

다음 CAND gatekeeper 호출 시:
- [ ] `explored_categories` 에 `"upstream-dup check"` 포함됐는가?
- [ ] counter_evidence 에 `git log` / `gh pr list` 실행 결과 기록됐는가?
- [ ] upstream HEAD 에서 주장 코드 여전히 존재 확인?

Cross-review 트리거 시:
- [ ] 대상 CAND severity 가 P0/P1/P2 인가? (P3 면 기본 skip)
- [ ] gatekeeper 의 upstream-dup 이미 수행 여부?
