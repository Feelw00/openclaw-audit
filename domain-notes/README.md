# 도메인 노트

페르소나가 셀 실행 후 여기에 영구 관찰 기록을 누적한다. 다음 셀의 페르소나가 이 파일을 읽고
중복 탐색을 피할 수 있도록.

## 작성 규칙

- 파일명: `{domain-id}.md` (grid.yaml 도메인 id)
- 각 페르소나 실행마다 "**{페르소나} (YYYY-MM-DD)**" 섹션을 **하단에 append**
- 이전 섹션 수정 금지 (append-only)
- 너무 길어지면 오래된 것을 `domain-notes/archive/{domain}-{YYYY-Q}.md` 로 분리

## 권장 템플릿

```markdown
# {domain-id}

## 공통 지식

(도메인 전체 맥락. 실행자가 계속 업데이트.)

## 실행 이력

### memory-leak-hunter (2026-04-18)

- 셀: plugins-memory
- 탐지 FIND: 3건 (2 passed validate, 1 rejected B-1-2c)
- 주요 발견 패턴:
  - registryCache cap 후 eviction 정책 없음 (loader.ts:196)
  - jitiCache 무제한 누적 (jiti-loader-cache.ts:34)
- 반증 탐색에서 확인된 기존 방어: dispose 메소드는 process-exit 경로에만.

### clusterer (2026-04-18)

- CAND-001 (epic): "Node Map 자료구조 delete/TTL 누락" 으로 2 FIND 묶음.
```
