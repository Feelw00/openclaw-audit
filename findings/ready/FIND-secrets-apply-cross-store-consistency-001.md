---
id: FIND-secrets-apply-cross-store-consistency-001
cell: secrets-apply-cross-store-consistency
title: 'secrets apply: config↔auth-store 다중스토어 비원자 커밋, 중간 throw 시 부분 마이그레이션'
file: src/secrets/apply.ts
line_range: 835-855
evidence: "```ts\n  try {\n    await replaceConfigFile({\n      nextConfig: projected.nextConfig,\n\
  \      snapshot: projected.configSnapshot,\n      writeOptions: projected.configWriteOptions,\n\
  \      io,\n      afterWrite: { mode: \"auto\" },\n    });\n    for (const write\
  \ of writes) {\n      writeTextFileAtomic(write.path, write.content, write.mode);\n\
  \    }\n  } catch (err) {\n    for (const [pathname, snapshot] of snapshots.entries())\
  \ {\n      try {\n        restoreFileSnapshot(pathname, snapshot);\n      } catch\
  \ {\n        // Best effort only; preserve original error.\n      }\n    }\n   \
  \ throw new Error(`Secrets apply failed: ${String(err)}`, { cause: err });\n  }\n\
  ```\n"
symptom_type: cross-store-gap
problem: 'secrets apply 의 write 모드가 한 논리 트랜잭션(plaintext 자격증명을 SecretRef 로 마이그레이션)에서
  config.json, N개 auth-profiles.json, legacy auth-json, .env 를 순차로 커밋한다. 각 파일은 개별
  atomic 이지만 파일 간 경계가 없어, config 커밋 성공 후 auth-store #2/#3 write 가 throw 하면 config
  는 ref 로 마이그레이션됐는데 auth-store 의 plaintext 키는 남아 두 스토어가 발산한 채 영속된다. catch 의 롤백은 best-effort
  라 복원 write 자체가 실패하면 반쪽 상태가 영구화된다.'
mechanism: '1. runSecretsApply(write:true) → projectPlanState 가 메모리에서 nextConfig +
  authStoreByPath + authJsonByPath + envRawByPath 를 계산(apply.ts:770).

  2. 커밋 전 모든 대상 파일을 captureFileSnapshot 으로 스냅샷(apply.ts:816-832).

  3. 커밋 순서: 먼저 replaceConfigFile 로 config.json 을 커밋(apply.ts:836). 이 호출은 자체 withFileLock
  + baseHash CAS 로 보호되지만 호출이 반환되는 순간 그 lock 은 해제된다.

  4. 그 다음 for 루프가 writes(auth-store JSON들 + auth-json + .env)를 순차로 writeTextFileAtomic
  한다(apply.ts:843-845). 이 구간은 lock/CAS/트랜잭션 밖이다.

  5. **throw 주입 시점**: config 커밋(836)이 이미 성공한 뒤, 루프 N번째 writeTextFileAtomic(844)이 ENOSPC
  / EACCES(0o600 디렉터리 생성 실패) / EIO 등으로 throw. config 는 SecretRef 로 전환됐는데 auth-store
  #2 이후는 아직 plaintext.

  6. catch 가 snapshots 를 순회하며 restoreFileSnapshot 으로 모든 파일을 이전 내용으로 되돌리려 한다(apply.ts:847-849).
  그러나 각 restore 가 try/catch 로 감싸여 실패를 삼킨다(apply.ts:850-852, "Best effort only"). 디스크
  가득참/권한 문제로 throw 를 유발한 그 조건이 복원 write 도 똑같이 실패시킬 수 있다.

  7. 복원이 일부만 성공하면: config 는 원복됐는데 auth-store 는 마이그레이션된 상태로 남거나(또는 그 반대), 두 스토어가 영구
  발산. 다음 런타임 로드 시 config 의 ref 가 가리키는 plaintext 가 auth-store 에서 이미 삭제됐거나, 거꾸로 scrub
  됐어야 할 plaintext 키가 auth-store 에 잔존(자격증명 유출 표면 + ref 미해결로 자격증명 로드 실패).

  '
root_cause_chain:
- why: config 와 satellite 스토어가 왜 한 트랜잭션으로 묶이지 않는가
  because: 커밋 블록이 replaceConfigFile 호출 1회 + for 루프 writeTextFileAtomic N회를 단순 나열할
    뿐, 파일 간 2PC/저널/단일 lock 경계가 없다. config 의 lock 은 replaceConfigFile 반환 시 해제되어 후속
    write 를 덮지 않는다.
  evidence_ref: src/secrets/apply.ts:843
- why: config lock 이 satellite write 를 보호하지 못하는가
  because: replaceConfigFile 은 내부에서 withConfigMutationLock → withFileLock(configPath)
    으로 config 파일만 잠그고 함수 반환과 함께 lock 을 푼다. 루프(apply.ts:843)는 그 바깥에서 실행되므로 잠금 범위 밖이다.
  evidence_ref: src/config/mutate.ts:475
- why: 부분 커밋이 자동 복구되지 않는가
  because: 롤백이 best-effort 다. restoreFileSnapshot 호출이 각각 try{}catch{} 로 감싸여 복원 실패를
    무시한다(주석 "Best effort only"). throw 를 유발한 디스크/권한 조건이 복원 write 도 실패시키면 발산 상태가 영속된다.
  evidence_ref: src/secrets/apply.ts:850
- why: 발산이 데이터 손상/유출로 이어지는가
  because: auth-profiles.json 과 legacy auth-json 은 OAuth/api_key 자격증명의 영속 SoT(derived-cache
    아님). config 가 ref 로 전환됐는데 auth-store plaintext 가 잔존하면 scrub 실패로 평문 키가 디스크에 남고,
    거꾸로 auth-store 만 scrub 됐는데 config ref 가 미반영이면 자격증명 미해결로 로드 실패한다.
  evidence_ref: src/secrets/apply.ts:818
impact_hypothesis: data-loss
impact_detail: '정성+조건: secrets apply write 모드는 plaintext 자격증명(config 인라인 키 / auth-profiles.json
  의 api_key·token / legacy auth-json / .env)을 SecretRef 로 일괄 마이그레이션하는 다중스토어 커밋이다.
  손상 조건 = config 커밋(apply.ts:836) 성공 후 satellite write 루프(844)의 임의 지점에서 throw(ENOSPC,
  EACCES, EIO, 또는 부모 디렉터리 0o700 생성 실패) AND best-effort 롤백 중 적어도 한 restore 가 동일 조건으로
  실패. 발생 시 config↔auth-store 가 영구 발산: (a) config=ref / auth-store=plaintext 잔존 → 평문
  자격증명이 scrub 안 된 채 디스크에 남아 유출 표면 확대 + ref 가 동일 값을 중복 참조, (b) config=원복 / auth-store=scrub
  완료 → ref 미반영으로 다음 로드에서 자격증명 해결 실패(로드 불가). 다중 auth-store(에이전트별 auth-profiles.json)
  + .env 까지 한 번에 쓰므로 writes 길이가 클수록 중간 throw 노출 창이 넓다. 빈도: secrets apply 는 핫패스가 아닌
  명시적 마이그레이션 명령(cold)이나, 자격증명이라는 critical 데이터에 1회 손상으로도 영향이 크다(P1 하한). 단일 실행이라 동시성보다
  crash/IO-fault 타이밍 의존이라 P1.'
severity: P1
counter_evidence:
  path: src/config/mutate.ts
  line: '475'
  reason: '트랜잭션/락 경계 Grep(R-3): `rg -n "withWriteTransaction|BEGIN|withFileLock|baseHash|CAS|2PC|journal"
    src/secrets/` → src/secrets/ 전체에서 match 없음(EXIT 1). 즉 secrets 디렉터리 자체에는 파일 간 트랜잭션/락/저널이
    전혀 없다.

    비대칭 기준선(반증): config 쓰기 경로 replaceConfigFile 은 withConfigMutationLock → withFileLock(configPath,
    ...) + assertBaseHashMatches(baseHash CAS, mutate.ts:499) 로 잠금·CAS 보호된다(mutate.ts:475-478).
    그런데 이 보호는 config 파일에만 적용되고 함수 반환과 함께 해제되어, 동일 논리 트랜잭션의 satellite write(apply.ts:843)는
    어떤 보호도 받지 못한다 → "lock/CAS 가 한쪽에만" 의 전형(E 카테고리).


    실행조건 분류:

    | 경로 | 조건 |

    | config 커밋 withFileLock+CAS (mutate.ts:475/499) | unconditional — config 변경 시
    항상 잠금·CAS |

    | satellite for-loop writeTextFileAtomic (apply.ts:843-845) | unconditional(write모드)
    — 단 lock/경계 없음 |

    | best-effort 롤백 (apply.ts:847-853) | recovery-only — 커밋 throw 시에만, 복원 실패 무시 |


    derived-cache 검토: auth-profiles.json / legacy auth-json / .env 는 자격증명의 영속 SoT
    라 파일시스템에서 재생성 불가 → 자가복구 아님(결함 성립).

    외부패키지 경계: writeTextFileAtomic→replaceFileAtomicSync 는 @openclaw/fs-safe 로 단일파일
    원자성만 책임지며, 파일 간 경계는 그 범위 밖이다. 트랜잭션 경계 부재는 in-tree apply.ts 의 책임이므로 in-tree 결함.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
cross_refs:
- FIND-secrets-apply-cross-store-consistency-002
---
# secrets apply: config↔auth-store 다중스토어 비원자 커밋, 중간 throw 시 부분 마이그레이션

## 문제
`runSecretsApply` 의 write 모드 커밋 블록(apply.ts:835-855)이 한 논리 트랜잭션(plaintext 자격증명 → SecretRef 마이그레이션)에서 config.json 을 먼저 커밋하고, 이어서 N개 `auth-profiles.json` + legacy auth-json + `.env` 를 for 루프로 순차 커밋한다. 각 파일은 `writeTextFileAtomic` 로 개별 원자적이지만 **파일 간 트랜잭션/락/저널 경계가 없다.** config 커밋이 성공한 뒤 satellite write 루프 중간에서 throw 하면 config 는 ref 로 전환됐는데 일부 auth-store 는 아직 plaintext 인 채로 두 스토어가 발산한 상태가 디스크에 남는다.

## 발현 메커니즘
1. `projectPlanState`(apply.ts:770)가 메모리에서 nextConfig + authStoreByPath + authJsonByPath + envRawByPath 를 계산한다.
2. 커밋 전 모든 대상 파일을 `captureFileSnapshot` 으로 스냅샷한다(apply.ts:816-832).
3. 커밋 1단계: `replaceConfigFile`(apply.ts:836)로 config.json 커밋. 이 호출은 자체 `withFileLock`+baseHash CAS 로 보호되지만 **호출이 반환되는 즉시 lock 이 풀린다**.
4. 커밋 2단계: for 루프가 `writes`(auth-store JSON들 + auth-json + .env)를 순차 `writeTextFileAtomic` 한다(apply.ts:843-845). 이 구간은 lock/CAS/트랜잭션 밖.
5. **throw 주입 시점**: config 커밋(836) 성공 후 루프 N번째 `writeTextFileAtomic`(844)이 ENOSPC / EACCES / EIO / 0o700 디렉터리 생성 실패로 throw. config=ref, auth-store #2 이후=plaintext.
6. catch 가 snapshots 순회로 모든 파일을 원복 시도하나(apply.ts:847-849), 각 `restoreFileSnapshot` 이 try/catch 로 실패를 삼킨다(apply.ts:850-852, "Best effort only"). throw 를 유발한 동일 조건(디스크 가득/권한)이 복원 write 도 실패시킬 수 있다.
7. 복원이 일부만 성공 → config 와 auth-store 가 영구 발산.

## 근본 원인 분석
- 파일 간 경계 부재: 커밋 블록이 `replaceConfigFile` 1회 + 루프 N회를 단순 나열할 뿐 2PC/저널/단일 lock 이 없다(apply.ts:843). R-3 Grep 결과 src/secrets/ 전체에 트랜잭션/락 키워드 match 0.
- config lock 의 범위 한정: `replaceConfigFile` 은 `withConfigMutationLock`→`withFileLock(configPath)` 으로 config 파일만 잠그고 반환과 동시에 해제(mutate.ts:475). 후속 satellite write 는 잠금 밖.
- best-effort 롤백: 복원 write 가 try/catch 로 실패를 무시(apply.ts:850). 커밋 throw 를 부른 조건이 복원도 실패시키면 발산 영속.
- SoT 라 자가복구 불가: auth-profiles.json / auth-json / .env 는 자격증명 영속 SoT(파일시스템 재생성 불가).

## 영향
impact: data-loss. config↔auth-store 영구 발산으로 두 갈래 손상이 가능하다 — (a) config=ref / auth-store=plaintext 잔존 → 평문 자격증명이 scrub 안 된 채 디스크에 남아 유출 표면 확대, (b) config=원복 / auth-store=scrub 완료 → ref 미반영으로 다음 로드에서 자격증명 해결 실패. secrets apply 는 cold 마이그레이션 명령이지만 자격증명이라는 critical 데이터라 1회 손상의 영향이 크다.

재현 시나리오: (1) config 인라인 키 + 2개 이상 에이전트의 auth-profiles.json plaintext 키를 동시 마이그레이션하는 plan 구성, (2) 첫 auth-store 디렉터리는 쓰기 가능하나 두 번째 auth-store 경로를 read-only/ENOSPC 로 만들어 둠, (3) write 모드 실행 → config 커밋 성공 후 두 번째 writeTextFileAtomic 가 throw, (4) catch 의 best-effort 복원도 동일 조건으로 실패 유도, (5) config 는 ref / 첫 auth-store 는 scrub / 두 번째 auth-store 는 plaintext 잔존 상태로 발산 확인.

## 반증 탐색
- 트랜잭션/락 경계 Grep(R-3 필수): `rg -n "withWriteTransaction|BEGIN|withFileLock|baseHash|CAS|2PC|journal" src/secrets/` → match 없음(EXIT 1). secrets 디렉터리에는 파일 간 경계가 전혀 없음을 직접 확인.
- 비대칭 기준선(반증): config 경로(`replaceConfigFile`)는 `withFileLock`+`assertBaseHashMatches`(baseHash CAS) 로 보호(mutate.ts:475/499). 동일 트랜잭션의 satellite write 는 무보호 → "lock/CAS 한쪽에만" 의 전형.
- derived-cache 여부: auth-profiles.json / auth-json / .env 는 자격증명 SoT 라 재생성 불가, 자가복구 아님.
- 외부 패키지 경계: `writeTextFileAtomic` 의 단일파일 원자성은 @openclaw/fs-safe 책임이나, 파일 간 트랜잭션 경계 부재는 in-tree apply.ts 의 책임 → in-tree 결함.
- 기존 테스트: secrets apply 의 "커밋 중간 throw + 롤백 실패" 부분커밋 재현 테스트는 확인되지 않음.

## Self-check
### 내가 확실한 근거
- 커밋 블록(apply.ts:836-845)이 config `replaceConfigFile` 1회 후 satellite write 를 for 루프로 순차 실행하며 그 사이에 파일 간 경계가 없음 (직접 Read).
- `replaceConfigFile` 이 `withFileLock`+baseHash CAS 로 config 만 보호하고 반환 시 lock 해제됨 (mutate.ts:475-478, 499).
- 롤백이 best-effort 로 복원 실패를 try/catch 로 삼킴 (apply.ts:850-852, 주석 "Best effort only").
- src/secrets/ 전체에 트랜잭션/락/저널 키워드 match 0 (Grep EXIT 1).

### 내가 한 가정
- writeTextFileAtomic 가 디스크 가득/권한/IO 결함에서 throw 한다는 점(replaceFileAtomicSync/privateFileStoreSync 의 표준 fs 동작).
- throw 를 유발한 조건(ENOSPC/EACCES 등)이 같은 영역에 대한 복원 write 도 실패시킬 개연이 있다는 점(동일 디스크/권한 컨텍스트).

### 확인 안 한 것 중 영향 가능성
- replaceConfigFile 자체가 throw 했을 때(config 커밋 실패)는 satellite write 가 시작도 안 했으니 발산이 없음 — 본 FIND 는 config 성공 후 satellite throw 케이스에 한정.
- afterWrite:{mode:"auto"} 의 런타임 스냅샷 갱신이 부분 커밋 상태에서 어떤 in-memory 캐시 발산을 추가로 유발하는지(config 모듈 내부, 본 FIND 범위 밖).
