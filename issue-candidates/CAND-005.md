---
candidate_id: CAND-005
type: single
finding_ids:
  - FIND-plugins-lifecycle-001
cluster_rationale: |
  단일 FIND 로 구성. register() throw 시 loader catch 블록이 process-global
  runtime state 4종 (agentHarnesses / compactionProviders / memoryEmbedding /
  memoryPluginState) 만 복구하고 registry 객체의 배열 필드
  (httpRoutes/services/commands/hooks/gatewayMethods/providers) 는 rollback
  하지 않아 부분 등록이 잔존하는 결함.

  타 도메인 FIND-infra-process-error-boundary-003 과 "catch 블록의 부분 상태
  롤백" 이라는 상위 추상 패턴은 공유하나, 해결 축이 서로 다르다:
    - 본 건: registry 배열 snapshot/restore 메커니즘 부재 (데이터 구조 축)
    - FIND-003: sigusr1AuthorizedCount 대칭 감소 함수 부재 (카운터 축)
  또한 도메인/CODEOWNERS/테스트 대상이 상이하여 clusterer.md Step 3 의
  "같은 인프라 축 (같은 registry, 같은 lock, 같은 error boundary)" 기준을
  충족하지 않음 → epic 근거 불충분, single 처리.

  FIND 내부 근거 인용:
  - root_cause_chain[0]: "catch 블록에서 restoreRegisteredAgentHarnesses 등
    process-global runtime state 만 복구하고, registry 배열들은 복구하지 않는다"
    (src/plugins/loader.ts:1839-1849).
  - root_cause_chain[1]: "register() 콜백이 registry.ts 의
    registerHttpRoute/Service/Command 를 직접 호출하여 registry.httpRoutes.push
    에 바로 append 하는 설계이고, loader 는 registry 중간 상태 복원 책임을
    agentHarnesses 계열에만 할당했다".
  - domain-notes/plugins.md lifecycle-auditor 테이블: 6종 register* 함수가
    전부 "대칭 결여", agentHarness/compaction/memory* 4종만 "대칭 존재".
proposed_title: "plugin register throw 시 httpRoutes/services/commands/hooks 부분 등록 rollback 부재"
proposed_severity: P1
existing_issue: null
created_at: 2026-04-18
---

# plugin register throw 시 httpRoutes/services/commands/hooks 부분 등록 rollback 부재

## 공통 패턴

`src/plugins/loader.ts:1839-1862` 의 register phase catch 블록은 아래 4종만
snapshot/restore 한다.

1. `restoreRegisteredAgentHarnesses(previousAgentHarnesses)`
2. `restoreRegisteredCompactionProviders(previousCompactionProviders)`
3. `restoreRegisteredMemoryEmbeddingProviders(previousMemoryEmbeddingProviders)`
4. `restoreMemoryPluginState({ corpusSupplements, promptBuilder, ... })`

반면 플러그인이 `register(api)` 중 호출하는 아래 6종은 registry 객체의
배열 필드에 직접 push 되며 catch 블록에서 복구되지 않는다.

- `api.registerHttpRoute` → `registry.httpRoutes.push` (registry.ts:388)
- `api.registerService` → `registry.services.push` (registry.ts:962)
- `api.registerCommand` → registry.commands / command-registry-state
- `api.registerHook` → `registry.hooks.push`
- `api.registerGatewayMethod` → `registry.gatewayMethods`
- `api.registerProvider` → `registry.providers`

register 본체가 N 회의 register* 호출을 마친 뒤 throw 하면, 이 N 개의
엔트리는 registry 에 잔존한 채 record.status='error' 로만 표시된다.
`registry.httpRoutes` 소비자 (http-registry.ts:28 `registry.httpRoutes = routes`)
는 status 로 필터링하지 않으므로 실패 플러그인의 route/service 가 정상과
구분 없이 공개된다.

## 관련 FIND

- **FIND-plugins-lifecycle-001** (P1, lifecycle-gap): register phase catch
  블록이 agentHarness/compaction/memory* 4종만 복구, registry 배열 필드 6종은
  rollback 경로 부재. 결과: 부분 등록된 HTTP route / service 가 gateway 에
  그대로 expose 되어 초기화 실패 handler 호출 → wrong-output 가능.
  근거: src/plugins/loader.ts:1839-1862, src/plugins/registry.ts:388-411, 961-968.

## Cross-refs

- CAND-006 (FIND-infra-process-error-boundary-003): catch 블록 부분 롤백
  패턴을 공유하나 다른 도메인/다른 자료구조. 해결이 독립적이므로 CAND 는
  분리하되 개념 유사성은 publisher / solution-miner 단계에서 참고 가능.
