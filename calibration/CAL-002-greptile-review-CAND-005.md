# CAL-002: Greptile review 로 CAND-005 PR 보강

**날짜**: 2026-04-18
**CAND**: CAND-005
**PR**: openclaw#68531
**리뷰어**: Greptile (confidence 4/5)
**대응**: follow-up commit 34770a1917, 확장된 회귀 테스트

## Greptile 지적

> Gap in rollback coverage: `captureRegistryArraySnapshot` only iterates
> `Array.isArray(value)` fields and therefore silently skips
> `registry.gatewayHandlers` (a plain `{}` object) and
> `registry.gatewayMethodScopes` (an optional dictionary). A plugin that
> calls `api.registerGatewayMethod()` before throwing still leaves an
> orphan gateway handler active.

정확한 지적. 내가 snapshot 대상을 `Array.isArray` 로만 필터 → object-typed registration field 누락.

## 대응

`RegistryRollbackSnapshot` 타입 도입:
```ts
type RegistryRollbackSnapshot = {
  arrays: Map<string, unknown[]>;        // .slice() 로 capture
  objectKeys: Map<string, Set<string>>;  // Object.keys 스냅샷
};
```

capture: 모든 field 순회. Array 면 slice, plain object 면 key set.
restore: array 는 length=0 후 push, object 는 snapshot 에 없는 key 삭제.

추가 회귀 테스트: `registerGatewayMethod("plugin.orphan.ping", handler, { scope })` 호출 후 throw → `registry.gatewayHandlers` 와 `registry.gatewayMethodScopes` 둘 다 rollback.

## 파이프라인 반영

**페르소나 보강 제안** (다음 세션 반영 대상):
- `plugin-lifecycle-auditor.md` 와 `memory-leak-hunter.md` R-3 Grep 단계에 "array 와 object-typed registration 모두 확인" 명시
- Specifically: `registry.<field>\.push(` 뿐 아니라 `registry.<field>\[key\]\s*=` 도 Grep

**regression test 체크리스트 보강**:
- SOL 의 `repro_test_draft` 가 커버하는 데이터 구조 유형 (array vs object) 명시
- fix helper 가 generic 한 경우 object 와 array 둘 다 exercise 하는 테스트 케이스 포함

## 결과

- Follow-up commit: `34770a1917`
- 추가 테스트: 1건 (총 2/2 pass, 기존 test 도 유지)
- 스코프 테스트: 4 files / 96 tests (+1)
- PR 본문 + Greptile 에 답변 comment 추가
- 메인테이너 최종 리뷰 대기
