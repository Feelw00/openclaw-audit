---
id: FIND-secrets-apply-cross-store-consistency-002
cell: secrets-apply-cross-store-consistency
title: secrets apply auth-store 커밋이 AUTH_STORE_LOCK 미획득 → 동시 OAuth refresh lost-update
file: src/secrets/apply.ts
line_range: 818-821
evidence: "```ts\nfor (const [pathname, value] of projected.authStoreByPath.entries())\
  \ {\n  capture(pathname);\n  writes.push(toJsonWrite(pathname, value));\n}\n```\n"
symptom_type: cross-store-gap
problem: secrets apply 가 auth-profiles.json 을 read(projectPlanState 시점) → scrub →
  writeTextFileAtomic(커밋 시점) 의 read-modify-write 로 갱신하면서, 같은 파일의 정규 writer 가 쓰는 AUTH_STORE_LOCK_OPTIONS
  파일락을 전혀 잡지 않는다. apply 의 read 와 write 사이에 OAuth refresh(updateAuthProfileStoreWithLock)
  가 같은 auth-profiles.json 을 갱신하면, apply 의 writeTextFileAtomic 가 stale 스냅샷 전체를 덮어써
  갱신된 토큰을 lost-update 로 날린다.
mechanism: '1. runSecretsApply(write:true) → projectPlanState → scrubAuthStoresForProviderTargets
  가 readJsonObjectIfExists 로 디스크의 auth-profiles.json 을 읽어 메모리 nextStore 로 scrub(apply.ts:391,
  395). 이 read 는 어떤 락도 잡지 않는다.

  2. projectPlanState 가 반환되고, 커밋 단계 진입 전까지 시간 경과(다중 ref resolve / prepareSecretsRuntimeSnapshot
  preflight 가 exec/file provider 를 spawn 하면 수십~수백 ms).

  3. **경합 주입 시점**: 이 read↔write 창에서 라이브 게이트웨이의 OAuth 만료 → refreshOAuthTokenWithLock
  / updateAuthProfileStoreWithLock 가 withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS)
  를 잡고 같은 auth-profiles.json 에 새 access/refresh 토큰을 atomic 으로 기록(store.ts:705).

  4. apply 의 커밋 루프가 authStoreByPath 의 (stale) 메모리 스냅샷을 writeTextFileAtomic 로 전체 덮어쓴다(apply.ts:818-820
  에서 toJsonWrite 로 적재 → apply.ts:844 에서 write). 이 write 는 AUTH_STORE_LOCK 을 잡지 않으므로
  정규 writer 와 serialize 되지 않는다.

  5. 결과: 3단계에서 refresh 된 새 토큰이 apply 의 stale 스냅샷으로 덮여 사라진다(lost-update). auth-profiles.json
  은 apply 가 read 했던 옛 토큰 + scrub 결과만 남는다.

  6. 다음 LLM 호출이 만료된(또는 회전돼 무효화된) refresh 토큰으로 OAuth refresh 를 재시도 → 실패 → 사용자 재인증 필요.

  역방향도 성립: apply write(락 없음) 직후 정규 writer 가 stale-from-its-own-load 로 apply 의 ref
  마이그레이션을 덮을 수 있다. 정규 writer 끼리는 락으로 직렬화되지만 apply 는 그 직렬화에 참여하지 않는다.

  '
root_cause_chain:
- why: apply 의 auth-store write 가 동시 writer 와 충돌하는가
  because: apply 가 auth-profiles.json 을 writeTextFileAtomic(toJsonWrite 결과)로 커밋할 때
    AUTH_STORE_LOCK_OPTIONS 파일락을 잡지 않아, 같은 파일을 락으로 직렬화하는 정규 writer 와 mutual-exclusion
    되지 않는다.
  evidence_ref: src/secrets/apply.ts:818
- why: auth-profiles.json 에 정규 락 규약이 존재하는가
  because: auth-store 정규 writer updateAuthProfileStoreWithLock 은 withFileLock(authPath,
    AUTH_STORE_LOCK_OPTIONS) 안에서 reload→update→save 한다. 주석이 명시적으로 "락 보유 writer 는 디스크에서
    reload 해야 라이브 게이트웨이가 더 신선한 CLI/config-auth write 를 stale in-memory 로 덮지 않는다" 고
    적어, 이 락이 정확히 이런 lost-update 를 막기 위한 것임을 보여준다.
  evidence_ref: src/agents/auth-profiles/store.ts:705
- why: apply 가 read 한 스냅샷이 stale 이 되는가
  because: apply 는 projectPlanState 의 readJsonObjectIfExists 로 락 없이 디스크를 읽고(apply.ts:391),
    그 후 ref resolve/runtime preflight 동안 시간이 흐른 뒤 그 메모리 스냅샷을 커밋한다. read 와 write 사이에
    다른 writer 가 디스크를 바꾸면 apply 의 스냅샷은 stale 이 된다.
  evidence_ref: src/secrets/apply.ts:391
- why: lost-update 가 자격증명 손상으로 이어지는가
  because: auth-profiles.json 은 OAuth access/refresh 토큰의 영속 SoT(derived-cache 아님).
    refresh 된 신토큰이 apply 의 stale 스냅샷으로 덮이면 회전된 refresh 토큰이 무효화돼 다음 refresh 가 실패, 사용자
    재인증 전까지 해당 provider 사용 불가.
  evidence_ref: src/secrets/apply.ts:844
impact_hypothesis: data-loss
impact_detail: '정량 가능 조건: lost-update 창 = projectPlanState 의 auth-store read(apply.ts:391)
  ~ 커밋 루프의 writeTextFileAtomic(apply.ts:844) 사이. 이 창은 ref resolve + prepareSecretsRuntimeSnapshot
  preflight(exec/file provider spawn 시 외부 프로세스 대기)로 수십~수백 ms 이상 벌어질 수 있다. 경합자 = 라이브
  게이트웨이의 OAuth 자동 refresh(만료 주기마다 발생, 장기 세션에서 반복) 또는 동시 CLI auth 변경. 충돌 시 그 창에서 디스크에
  기록된 신규 토큰/auth 변경이 apply 의 stale 전체 스냅샷 overwrite 로 소실(lost-update). auth-profiles.json
  은 토큰 SoT 라 회전된 refresh 토큰 소실 시 재인증 외 복구 불가. 영향 범위 = apply plan 이 건드리는 모든 auth-store(에이전트별
  + main). 빈도: apply 는 cold 명령이나 게이트웨이가 동시에 떠 있는 운영 환경에서 OAuth refresh 와 겹칠 현실적 창이
  있어 P1. 정규 writer 끼리는 락으로 보호되는데 apply 만 락 밖이라 "lock 한쪽에만" 의 cross-store-gap.'
severity: P1
counter_evidence:
  path: src/agents/auth-profiles/store.ts
  line: '705'
  reason: '트랜잭션/락 Grep(R-3): `rg -n "withFileLock|AUTH_STORE_LOCK|baseHash|CAS" src/secrets/`
    → secrets 디렉터리에서 match 없음(EXIT 1). apply 의 auth-store write 경로(apply.ts:818-821,
    844)는 어떤 락도 잡지 않음을 직접 확인.

    반증 기준선(같은 파일의 락 쓰는 정규 writer): updateAuthProfileStoreWithLock(store.ts:696-719)과
    oauth-manager.ts:437 의 OAuth refresh 는 동일 auth-profiles.json 을 withFileLock(authPath,
    AUTH_STORE_LOCK_OPTIONS) 안에서 reload→update→save 한다. store.ts:706-708 주석이 "Otherwise
    a live gateway can overwrite fresher CLI/config-auth writes with stale in-memory
    auth state" 라고 적어 이 락의 목적이 정확히 본 FIND 의 lost-update 방지임을 입증. apply 만 이 락을 우회 →
    "락이 한쪽에만" 비대칭(E 카테고리).


    실행조건 분류:

    | 경로 | 조건 |

    | apply auth-store writeTextFileAtomic (apply.ts:818-821/844) | unconditional(auth-store
    변경 시) — 락 없음 |

    | 정규 updateAuthProfileStoreWithLock (store.ts:705) | unconditional — 항상 AUTH_STORE_LOCK
    |

    | OAuth refresh withFileLock (oauth-manager.ts:437) | conditional-edge — 토큰 만료
    시 |


    derived-cache 검토: auth-profiles.json 은 OAuth access/refresh 토큰의 영속 SoT 라 재생성 불가
    → 자가복구 아님(결함 성립).

    외부 패키지 경계: 락 자체는 in-tree src/infra/file-lock.js(withFileLock)다. apply 가 이를 소비하지
    않은 것은 in-tree 결정 → in-tree 결함.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-secrets-apply-cross-store-consistency-001
---
# secrets apply auth-store 커밋이 AUTH_STORE_LOCK 미획득 → 동시 OAuth refresh lost-update

## 문제
secrets apply 는 `auth-profiles.json` 을 read(projectPlanState 시점) → scrub → `writeTextFileAtomic`(커밋 시점)의 read-modify-write 로 갱신하면서, 같은 파일의 정규 writer 가 사용하는 `AUTH_STORE_LOCK_OPTIONS` 파일락을 전혀 잡지 않는다(apply.ts:818-821 에서 스냅샷을 write 큐에 적재, apply.ts:844 에서 락 없이 커밋). apply 의 read 와 write 사이에 OAuth refresh(`updateAuthProfileStoreWithLock`)가 같은 파일을 갱신하면, apply 의 write 가 stale 메모리 스냅샷 전체를 덮어써 신규 토큰을 lost-update 로 소실시킨다.

## 발현 메커니즘
1. `projectPlanState` → `scrubAuthStoresForProviderTargets` 가 `readJsonObjectIfExists` 로 디스크의 auth-profiles.json 을 **락 없이** 읽어 메모리 nextStore 로 scrub(apply.ts:391, 395).
2. projectPlanState 반환 후 커밋 진입까지 ref resolve + `prepareSecretsRuntimeSnapshot` preflight(exec/file provider spawn 시 외부 프로세스 대기)로 시간 경과.
3. **경합 주입 시점**: 이 read↔write 창에서 라이브 게이트웨이 OAuth 만료 → `updateAuthProfileStoreWithLock` 가 `withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS)` 안에서 같은 파일에 새 토큰을 atomic 기록(store.ts:705).
4. apply 커밋 루프가 authStoreByPath 의 stale 스냅샷을 `writeTextFileAtomic` 로 전체 덮어쓴다(apply.ts:818-821 → 844). **AUTH_STORE_LOCK 미획득**이라 정규 writer 와 직렬화 안 됨.
5. 3단계의 신규 토큰이 apply 의 stale 스냅샷으로 덮여 소실(lost-update). 파일엔 apply 가 read 했던 옛 토큰 + scrub 결과만 남음.
6. 다음 호출이 무효화된 refresh 토큰으로 refresh 재시도 → 실패 → 재인증 필요.

역방향: apply write(락 없음) 직후 정규 writer 가 자기 load 기준 stale 로 apply 의 ref 마이그레이션을 덮을 수 있다. 정규 writer 끼리는 락으로 직렬화되나 apply 는 그 직렬화에 불참.

## 근본 원인 분석
- apply 의 auth-store write 가 무락: writeTextFileAtomic 커밋(apply.ts:818/844)이 AUTH_STORE_LOCK_OPTIONS 를 안 잡아 정규 writer 와 mutual-exclusion 안 됨.
- 정규 락 규약 존재: `updateAuthProfileStoreWithLock`(store.ts:705) 과 oauth-manager.ts:437 은 동일 파일을 `withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS)` 로 직렬화하며, store.ts 주석이 이 락의 목적이 "live gateway 가 fresher write 를 stale in-memory 로 덮는 것 방지" 임을 명시.
- apply 스냅샷의 staleness: apply 는 락 없이 read(apply.ts:391)한 뒤 시간이 흐른 메모리 스냅샷을 커밋 → read↔write 창에서 디스크 변경이 반영 안 됨.
- SoT 라 손상 영속: auth-profiles.json 은 토큰 SoT 라 회전된 refresh 토큰 소실 시 재인증 외 복구 불가.

## 영향
impact: data-loss. lost-update 창(apply.ts:391 read ~ apply.ts:844 write)에서 디스크에 기록된 신규 OAuth 토큰/auth 변경이 apply 의 stale 전체 overwrite 로 소실된다. auth-profiles.json 은 토큰 SoT 라 회전된 refresh 토큰이 사라지면 다음 refresh 가 실패하고 사용자 재인증 전까지 해당 provider 사용 불가. 영향 범위는 plan 이 건드리는 모든 auth-store. 정규 writer 끼리는 락 보호되는데 apply 만 락 밖이라 "lock 한쪽에만" cross-store-gap.

재현 시나리오: (1) OAuth provider 가 설정된 에이전트의 auth-profiles.json 존재, (2) 라이브 게이트웨이가 토큰 refresh 를 막 수행해 디스크에 새 refresh 토큰 기록, (3) 그 직전 secrets apply 가 같은 파일을 read 한 상태로 ref resolve/preflight 중, (4) apply 커밋이 stale 스냅샷으로 파일 overwrite, (5) 파일에서 새 refresh 토큰이 사라지고 옛 토큰만 남아 다음 refresh 실패 확인. (read↔write 사이에 refresh write 가 끼도록 타이밍 제어.)

## 반증 탐색
- 락 Grep(R-3 필수): `rg -n "withFileLock|AUTH_STORE_LOCK|baseHash|CAS" src/secrets/` → match 없음(EXIT 1). apply 의 auth-store write 경로가 무락임을 직접 확인.
- 반증 기준선(같은 파일의 락 쓰는 정규 writer): `updateAuthProfileStoreWithLock`(store.ts:705)/oauth-manager.ts:437 이 `withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS)` 로 동일 파일을 직렬화. store.ts:706-708 주석이 이 락이 정확히 본 lost-update 를 막기 위한 것이라 명시 → apply 만 우회하는 비대칭.
- derived-cache 여부: auth-profiles.json 은 토큰 SoT 라 재생성 불가, 자가복구 아님.
- 외부 패키지 경계: withFileLock 은 in-tree src/infra/file-lock.js. apply 가 미소비한 것은 in-tree 결정 → in-tree 결함.
- 기존 테스트: secrets apply 와 OAuth refresh 의 동시성 lost-update 재현 테스트는 확인되지 않음.

## Self-check
### 내가 확실한 근거
- apply 가 auth-profiles.json 을 락 없이 read(apply.ts:391) 후 락 없이 writeTextFileAtomic 커밋(apply.ts:818-821, 844) (직접 Read).
- 정규 writer updateAuthProfileStoreWithLock / OAuth refresh 가 withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS) 를 잡음 (store.ts:705, oauth-manager.ts:437).
- store.ts:706-708 주석이 이 락의 목적을 "live gateway 가 fresher write 를 stale in-memory 로 덮는 것 방지" 로 명시.
- src/secrets/ 전체에 락/CAS 키워드 match 0 (Grep EXIT 1).

### 내가 한 가정
- apply 의 read(projectPlanState)와 write(커밋) 사이에 실제로 시간 간격이 있다는 점(ref resolve + preflight 의 외부 프로세스/IO 대기로 비자명한 창 형성).
- 운영 환경에서 게이트웨이가 떠 있는 동안 OAuth 자동 refresh 가 만료 주기로 발생한다는 점.

### 확인 안 한 것 중 영향 가능성
- file-lock 의 stale 만료(30s) 가 apply 무락 write 와 무관하게 정규 writer 간에 미치는 영향(별개, 본 FIND 범위 밖).
- 단일 사용자/단일 프로세스 환경에서는 동시 writer 가 없어 창이 닫히는 점 — 본 FIND 는 게이트웨이+apply 동시 운영 시나리오에 한정.
