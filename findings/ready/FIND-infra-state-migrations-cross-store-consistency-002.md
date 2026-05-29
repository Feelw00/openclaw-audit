---
id: FIND-infra-state-migrations-cross-store-consistency-002
cell: infra-state-migrations-cross-store-consistency
title: 다단계 스토어 이동(sessions+agentDir+channel)이 트랜잭션 없이 순차 실행 - 중간 throw 시 split state
file: src/infra/state-migrations.ts
line_range: 1316-1323
evidence: "```ts\n  const preSessionChannelPlans = await runLegacyMigrationPlans(\n\
  \    detected.channelPlans.plans.filter((plan) => plan.kind === \"plugin-state-import\"\
  ),\n  );\n  const sessions = await migrateLegacySessions(detected, now);\n  const\
  \ agentDir = await migrateLegacyAgentDir(detected, now);\n  const channelPlans =\
  \ await runLegacyMigrationPlans(\n    detected.channelPlans.plans.filter((plan)\
  \ => plan.kind !== \"plugin-state-import\"),\n  );\n```\n"
symptom_type: cross-store-gap
problem: 여러 영속 스토어(plugin-state import, sessions store, agent dir, channel 파일 이동)를
  순차 await 로 마이그레이션하는데 묶는 트랜잭션/체크포인트/롤백이 없다. 앞 스토어가 이미 renameSync/rmSync 로 비가역 변경된
  뒤 뒤 단계에서 throw/crash 하면 일부 스토어만 새 레이아웃, 일부는 legacy 레이아웃으로 발산(split state)한 채 영속된다.
mechanism: '1. runLegacyStateMigrations 가 4단계를 트랜잭션 경계 없이 순차 await.

  2. 단계1 plugin-state import 가 일부 entry 를 store.register 로 영속 + 일부 legacy source 를
  renameSync 로 .migrated 아카이브 (:213, :263).

  3. 단계2 migrateLegacySessions 가 saveSessionStore 로 target 갱신 후 legacy store 를 rmSync,
  legacy 파일을 renameSync 로 target 이동 (:1209, :1232, :1242, :1254).

  4. 단계3 migrateLegacyAgentDir 가 agent 파일을 renameSync 로 target 이동 (:1284, :1300).

  5. 단계4 채널 move/copy plan 이 renameSync/copyFileSync (:278, :281).

  6. 임의 단계의 await 가 throw(디스크 풀/권한/SIGKILL)하면 함수가 즉시 전파 종료. 이미 끝난 앞 단계의 renameSync/rmSync
  는 비가역이므로 롤백 안 됨 → 스토어 간 발산 영속.

  7. autoMigrateChecked once-flag 때문에 다음 기동에서 재시도 안 될 수도, detect 가 부분상태를 새 legacy
  로 오인할 수도 있음.

  '
root_cause_chain:
- why: 왜 부분 마이그레이션(split state)이 영속되는가
  because: 4개 스토어 이동이 묶는 트랜잭션/2PC/체크포인트 없이 순차 await 되고, 각 단계가 비가역 renameSync/rmSync
    를 즉시 커밋하므로 중간 throw 시 앞 단계만 적용된 채 종료
  evidence_ref: src/infra/state-migrations.ts:1316
- why: 왜 throw 가 롤백되지 않는가
  because: runLegacyStateMigrations 에 try/catch 나 단계별 보상(compensating) 로직이 전혀 없어 임의
    단계 reject 가 그대로 전파되고 앞 단계 결과는 그대로 디스크에 남음
  evidence_ref: src/infra/state-migrations.ts:1319
- why: 왜 fs 연산이 비가역인가
  because: renameSync/rmSync/copyFileSync 가 직접 영구 스토어를 이동·삭제하며 원본 백업이 보장되지 않음(legacy
    삭제는 rmSync force, agent/session 이동은 renameSync 직접)
  evidence_ref: src/infra/state-migrations.ts:1242
- why: 왜 트랜잭션/락 보호가 아예 없는가
  because: rg withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|checkpoint
    가 두 파일에서 match 0 — 이 모듈은 트랜잭션 프리미티브를 전혀 쓰지 않음. autoMigrateLegacyStateDir 의 수동
    롤백(:996 renameSync 되돌림)은 단일 rename 한정이라 멀티스토어 시퀀스엔 적용 안 됨
  evidence_ref: src/infra/state-migrations.ts:996
impact_hypothesis: data-loss
impact_detail: '정성+조건부: 4단계 중 임의 지점에서 throw(ENOSPC 디스크풀, EACCES 권한, SIGKILL crash)하면
  일부 스토어는 새 경로, 일부는 legacy 경로로 발산한 채 영속. 비가역 1회성이라 자동 복구 없음. 예: 단계2 가 sessions 를 새
  위치로 이동·legacy 삭제한 뒤 단계3 agent dir 이동이 EACCES 로 throw → sessions 는 새 레이아웃, agent
  dir 은 legacy 레이아웃으로 split. once-flag(autoMigrateChecked) 로 다음 기동 재시도가 막히거나, detect
  가 부분상태를 오판할 위험. 호출 빈도 낮음(cold doctor/startup, process 당 1회) → P2.

  '
severity: P2
counter_evidence:
  path: src/infra/state-migrations.ts
  line: '996'
  reason: '방어 경로 실행조건 분류:

    | 경로 | 조건 |

    |---|---|

    | 단계별 try/catch (migrateLegacySessions/AgentDir 내부 per-file) | conditional-edge
    — 개별 renameSync 실패는 warn 으로 흡수하나, saveSessionStore throw(:1209) 등은 흡수 안 되고 전파.
    단계간 보상 없음 |

    | runLegacyStateMigrations 레벨 트랜잭션/롤백 | 부재(none) — :1316-1323 에 try/catch·체크포인트·2PC
    전무 |

    | autoMigrateLegacyStateDir 수동 롤백 | recovery-only & 범위제한 — :996 renameSync 되돌림은
    *단일 state-dir rename* 한정. 멀티스토어 시퀀스엔 미적용 |

    | 트랜잭션/락/CAS | none_found: rg ''withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|checkpoint''
    state-migrations.ts/.fs.ts → match 0 |

    | 호출 빈도 | cold doctor/startup only, process 당 autoMigrateChecked 1회. ordinary
    CLI 는 migrateState:false 로 skip(config-guard.ts:58). hot-path 아님 |

    | idempotency | 부분 재시도 안전성 불완전: fileExists(targetPath) skip(:272) 가드는 일부 보호하나,
    legacy 삭제(rmSync)/dir rename 후 부분상태에선 detect 가 hasLegacy 를 다르게 평가할 수 있음 |

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# 다단계 스토어 이동(sessions+agentDir+channel)이 트랜잭션 없이 순차 실행 - 중간 throw 시 split state

## 문제
`runLegacyStateMigrations` 는 4개의 독립 영속 스토어 그룹 — (1) plugin-state import, (2) sessions store, (3) agent dir, (4) channel 파일 이동 — 을 순차 `await` 로 마이그레이션한다. 이들을 하나의 논리 단위로 묶는 트랜잭션/2PC/체크포인트/롤백이 전혀 없고, 각 단계는 `renameSync`/`rmSync`/`copyFileSync` 로 영구 스토어를 비가역 이동·삭제한 뒤 즉시 다음 단계로 넘어간다. 임의 단계의 await 가 throw(디스크 풀, 권한, SIGKILL)하면 함수가 즉시 종료되면서 이미 커밋된 앞 단계는 롤백되지 않는다. 결과적으로 일부 스토어는 새 레이아웃, 일부는 legacy 레이아웃으로 발산(split state)한 채 영속된다.

## 발현 메커니즘
1. production caller(doctor: `src/flows/doctor-health-contributions.ts:308`, startup/autoMigrate: `src/commands/doctor-state-migrations.ts`)가 `runLegacyStateMigrations`/`autoMigrateLegacyState` 호출.
2. 단계1 `runLegacyMigrationPlans(plugin-state-import)` 가 entry 를 `store.register` 영속하고 legacy source 를 `fs.renameSync(plan.sourcePath, archivedPath)` 로 아카이브 (:213, :263).
3. 단계2 `migrateLegacySessions` 가 `saveSessionStore` 로 target 갱신(:1209), legacy 파일 `fs.renameSync`(:1232), legacy store `fs.rmSync`(:1242), legacy dir backup rename(:1254).
4. 단계3 `migrateLegacyAgentDir` 가 agent 파일 `fs.renameSync`(:1284), legacy dir backup rename(:1300).
5. 단계4 채널 `move`/`copy` plan 이 `fs.renameSync`(:278)/`fs.copyFileSync`(:281).
6. 임의 단계의 await 가 throw → 함수 즉시 전파 종료. 이미 끝난 앞 단계의 renameSync/rmSync 는 비가역이라 그대로 디스크에 남음 → 스토어 간 발산 영속.
7. `autoMigrateChecked` once-flag(:1524-1527) 때문에 같은 프로세스 재시도 불가, 다음 기동 시 `detect` 가 부분상태를 오판할 위험.

## 근본 원인 분석
1. 4개 스토어 이동이 트랜잭션/2PC/체크포인트 없이 순차 await 되며 각 단계가 비가역 renameSync/rmSync 를 즉시 커밋한다. 중간 throw 시 앞 단계만 적용된 채 종료된다 (:1316).
2. `runLegacyStateMigrations` 함수 자체에 try/catch 나 단계별 보상(compensating) 로직이 없어 임의 단계 reject 가 그대로 전파되고 앞 단계 결과가 롤백되지 않는다 (:1319).
3. fs 연산이 직접 영구 스토어를 이동·삭제하며 원본 백업이 보장되지 않는다(legacy 삭제 rmSync force, agent/session 이동 renameSync 직접) (:1242).
4. 이 모듈은 트랜잭션 프리미티브를 전혀 쓰지 않는다(grep match 0). 유일한 롤백인 `autoMigrateLegacyStateDir` 의 :996 `renameSync(targetDir, legacyDir)` 되돌림은 *단일 state-dir rename* 한정이라 멀티스토어 시퀀스에는 적용되지 않는다 — 트랜잭션 부재의 방증이자 보호 범위의 한계 (:996).

## 영향
impact_hypothesis: data-loss(스토어 간 발산 → 일부 데이터 접근 불가/일관성 손실). 4단계 중 임의 지점에서 throw 하면 일부 스토어는 새 경로, 일부는 legacy 경로로 split 된 채 영속한다. 비가역 1회성이라 자동 복구가 없다. 예: 단계2 가 sessions 를 새 위치로 옮기고 legacy 를 rmSync 한 직후 단계3 agent dir 이동이 EACCES 로 throw → sessions 는 새 레이아웃, agent dir 은 legacy 레이아웃으로 발산. 이후 런타임이 새 레이아웃을 기대하면 agent dir 데이터가 보이지 않게 된다.

호출 빈도는 낮다: cold doctor/startup 전용이고, ordinary CLI startup 은 `migrateState:false`(config-guard.ts:58)로 이 경로를 건너뛰며, process 당 `autoMigrateChecked` 1회로 제한된다. 따라서 hot-path 가 아니며 트리거에는 (a) legacy 레이아웃 동시 존재 (b) 마이그레이션 도중 fs throw/crash 라는 조건이 겹쳐야 한다 → P2.

재현 시나리오: legacy sessions + legacy agent dir 둘 다 배치 → agent dir target 경로를 read-only 로 만들어 단계3 의 ensureDir/renameSync 가 throw 하게 함 → `runLegacyStateMigrations` 실행 → sessions 는 이동 완료(legacy 삭제됨), agent dir 은 legacy 에 잔존, 함수는 throw 로 종료. R-7: production caller(doctor/startup)와 동일한 `runLegacyStateMigrations` 진입점·동일 단계 순서를 exercise.

## 반증 탐색
- 단계별 try/catch: `migrateLegacySessions`/`migrateLegacyAgentDir` 내부의 per-file `renameSync` 는 try/catch 로 warn 흡수하나(:1234, :1287), `saveSessionStore`(:1209)·`ensureDir`(:1146, :1274) 같은 단계 본체 throw 는 흡수되지 않고 전파된다. 단계간 보상 로직은 없음.
- runLegacyStateMigrations 레벨: :1316-1323 에 트랜잭션/체크포인트/롤백 전무.
- 트랜잭션/락/CAS: `rg 'withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|assertBaseHashMatch|checkpoint|transaction' src/infra/state-migrations.ts src/infra/state-migrations.fs.ts` → match 0.
- 유일 롤백의 범위: `autoMigrateLegacyStateDir` :996 `renameSync(targetDir, legacyDir)` 는 단일 state-dir rename 실패에만 작동(recovery-only, 범위제한). 멀티스토어 시퀀스를 보호하지 않음.
- 호출 빈도: cold doctor/startup only, ordinary CLI skip, process 당 1회 → hot-path 아님, severity 하향(P2).
- idempotency 부분보호: `fileExists(plan.targetPath)` skip(:272)·`fileExists(to)` skip(:1228, :1280) 가드가 일부 재시도 안전을 주지만, legacy 삭제(rmSync) 이후 부분상태에선 다음 `detect` 의 `hasLegacy` 판정이 달라져 완전한 멱등 회복을 보장하지 못함.

## Self-check
### 내가 확실한 근거
- `runLegacyStateMigrations` 가 4단계를 순차 await 하며 함수 레벨 try/catch 가 없음 (:1310-1338, Read 확인).
- 각 단계가 비가역 renameSync/rmSync/copyFileSync 사용 (:213, :263, :278, :281, :1232, :1242, :1284, :1300).
- 트랜잭션/락/CAS grep match 0.
- 유일 롤백 :996 은 단일 state-dir rename 한정 (Read 확인).

### 내가 한 가정
- 마이그레이션 도중 fs throw(ENOSPC/EACCES) 또는 SIGKILL 가 실제 발생 가능하다고 가정 — 디스크 풀/권한/전원차단은 production 에서 드물지만 가능.
- legacy 와 새 레이아웃이 동시에 마이그레이션 대상으로 존재하는 상태를 가정(마이그레이션 정의상 성립).

### 확인 안 한 것 중 영향 가능성
- 부분 split state 후 런타임이 실제로 어떻게 동작(빈 store 로 시작? 에러?)하는지는 allowed_paths 밖(런타임 로더)이라 미확인. 발산의 사용자 체감 심각도는 로더 동작에 의존.
- `detect` 가 부분상태를 정확히 재감지해 다음 기동에서 회복하는지는 detect 로직 분기(:1046-1134)의 부분상태 입력 테스트를 안 해 미확정. 회복 가능하면 P2→P3 하향 여지, 불가하면 P2 유지.
