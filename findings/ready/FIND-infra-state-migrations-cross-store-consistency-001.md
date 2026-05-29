---
id: FIND-infra-state-migrations-cross-store-consistency-001
cell: infra-state-migrations-cross-store-consistency
title: 손상된 target sessions.json 이 legacy-only 병합본으로 무조건 덮어써져 영구 유실
file: src/infra/state-migrations.ts
line_range: 1197-1212
evidence: "```ts\n  if (\n    (legacyParsed.ok || targetParsed.ok) &&\n    (Object.keys(legacyStore).length\
  \ > 0 || Object.keys(targetStore).length > 0)\n  ) {\n    const normalized: Record<string,\
  \ SessionEntry> = {};\n    for (const [key, entry] of Object.entries(merged)) {\n\
  \      const normalizedEntry = normalizeSessionEntry(entry);\n      if (!normalizedEntry)\
  \ {\n        continue;\n      }\n      normalized[key] = normalizedEntry;\n    }\n\
  \    await saveSessionStore(detected.sessions.targetStorePath, normalized, {\n \
  \     skipMaintenance: true,\n    });\n    changes.push(`Merged sessions store →\
  \ ${detected.sessions.targetStorePath}`);\n```\n"
symptom_type: cross-store-gap
problem: 타깃 sessions.json 이 손상되어 파싱 실패하면 그 파일이 legacy 측 키만 담은 병합본으로 무조건 덮어써진다. 손상
  파일이 디스크에 그대로 남아 수동 복구할 수 있었던 사용자 세션 레코드 전체가 영구 소실된다.
mechanism: '1. autoMigrateLegacyState (cold doctor/startup) → migrateLegacySessions
  진입.

  2. targetParsed = readSessionStoreJson5(targetStorePath). 타깃 파일이 손상(JSON5 파싱 실패)이면
  readSessionStoreJson5 가 try/catch-swallow 로 {store:{}, ok:false} 반환 (:47-58, :60-73).

  3. targetStore = {} (빈 객체). 손상 파일의 실제 바이트는 디스크에 그대로 있으나 메모리상으로는 0 entries 로 취급.

  4. legacy 측이 정상(legacyParsed.ok=true)이고 비어있지 않으면 :1197-1199 의 `(legacyParsed.ok
  || targetParsed.ok)` 가 true → merge = {...빈 target, ...legacy} = legacy-only.

  5. :1209 saveSessionStore(targetStorePath, legacy-only) 가 손상 타깃 파일을 legacy-only
  병합본으로 원자적으로 덮어씀.

  6. 손상되었지만 부분 복구 가능했던 target 세션 레코드(legacy 에 없는 키들)는 영구 소실. ok 플래그가 분기 어디에도 영향을 주지
  않음.

  '
root_cause_chain:
- why: 왜 손상 타깃이 legacy-only 데이터로 덮어써지는가
  because: save 게이트 조건이 (legacyParsed.ok || targetParsed.ok) 라서 target 이 unreadable
    이어도 legacy 만 ok 면 통과하고, save 입력 merged 는 빈 target(={}) + legacy 로 계산됨
  evidence_ref: src/infra/state-migrations.ts:1197
- why: 왜 target 파싱 실패가 빈 store 로 둔갑하는가
  because: readSessionStoreJson5 가 read/parse 실패를 try/catch 로 삼키고 {store:{}, ok:false}
    를 반환 — 호출부가 store 만 쓰고 ok 를 검사하지 않으면 손상과 빈 파일이 구분 불가
  evidence_ref: src/infra/state-migrations.fs.ts:47
- why: 왜 legacy 와 달리 target 손상에는 보호 분기가 없는가
  because: legacy unreadable 은 :1191 에서 warn + :1239 의 legacyParsed.ok 가드로 삭제를 막지만,
    target unreadable 에 대응하는 targetParsed.ok 검사가 save 경로에 전혀 없어 비대칭 방어
  evidence_ref: src/infra/state-migrations.ts:1239
- why: 왜 손상 시 백업/abort 같은 안전판이 없는가
  because: '마이그레이션이 다단계 fs 연산을 묶는 트랜잭션/체크포인트 없이 진행되며(grep: withWriteTransaction|withFileLock|BEGIN|baseHash|CAS
    전부 match 없음) 손상 데이터 보존 정책이 부재'
  evidence_ref: src/infra/state-migrations.ts:1209
impact_hypothesis: data-loss
impact_detail: '정성+조건부 정량: target sessions.json 이 손상된 상태에서 legacy sessions 디렉터리가 동시에
  존재하는 사용자가 doctor/cold-startup 마이그레이션을 1회 수행할 때 발생. 비가역 1회성 연산이라 손상되었지만 부분 복구 가능했던
  모든 target 세션 레코드(legacy 에 대응 키 없는 것)가 영구 소실. 호출 빈도는 낮음(cold doctor/startup, process
  당 autoMigrateChecked 1회) 이나, 손상 직후 사용자가 가장 복구를 원하는 시점에 정확히 마지막 사본을 파괴한다는 점에서 impact
  가 큼. 복구 불가(원본 덮어쓰기).

  '
severity: P1
counter_evidence:
  path: src/infra/state-migrations.ts
  line: '1191'
  reason: '방어 경로 실행조건 분류:

    | 경로 | 조건 |

    |---|---|

    | legacyParsed.ok 가드(:1191 warn, :1239 삭제 차단) | recovery-only — legacy 손상에만 작동,
    target 손상엔 무반응 |

    | targetParsed.ok 검사 | 부재(none) — save 분기 :1197-1209 어디에도 없음 |

    | 트랜잭션/락/CAS | none_found: rg ''withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|checkpoint''
    state-migrations.ts/.fs.ts → match 0 |

    | atomic sibling | saveSessionStore(:604) 는 runExclusiveSessionStoreWrite 락 +
    단일 파일 atomic write 로 그 자체는 안전. 결함은 그 atomic write 의 *입력 데이터*가 손상 원본을 무시한 채 legacy-only
    라는 점(cross-store 병합 결정 오류) — 단일파일 원자성 문제 아님 |

    | derived-cache | 아님 — sessions.json 은 세션 SoT(파생 캐시 아님). 재생성 불가 |

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# 손상된 target sessions.json 이 legacy-only 병합본으로 무조건 덮어써져 영구 유실

## 문제
`migrateLegacySessions` 는 마이그레이션 대상 위치인 target `sessions.json` 을 읽어 legacy 측과 병합한 뒤 다시 target 경로에 저장한다. 그런데 target 파일이 손상되어 JSON5 파싱에 실패하면 `readSessionStoreJson5` 가 빈 store `{store:{}, ok:false}` 를 반환하고, 호출부는 `ok` 를 검사하지 않은 채 `targetStore = {}` 로 진행한다. legacy 측이 정상이고 비어있지 않으면 save 게이트 `(legacyParsed.ok || targetParsed.ok)` 가 통과되어, 손상되었지만 디스크에 남아있던 원본 target 파일이 legacy 측 키만 담은 병합본으로 덮어써진다. legacy 에 대응 키가 없던 target 세션 레코드는 영구 소실되며, 손상 파일을 수동 검사·복구할 마지막 기회도 사라진다.

## 발현 메커니즘
1. cold doctor 또는 startup 경로에서 `autoMigrateLegacyState` → `migrateLegacySessions` 진입 (production caller: `src/flows/doctor-health-contributions.ts:308` `runLegacyStateMigrations`, `src/commands/doctor-state-migrations.ts` `autoMigrateLegacyState`).
2. `targetParsed = fileExists(targetStorePath) ? readSessionStoreJson5(targetStorePath) : {store:{}, ok:true}` (:1151-1153). target 파일이 손상이면 `readSessionStoreJson5` 가 `fs.readFileSync`/`JSON5.parse` 예외를 try/catch 로 삼키고 `{store:{}, ok:false}` 반환 (state-migrations.fs.ts:47-58, 60-73).
3. `targetStore = targetParsed.store` → 빈 객체. 손상 파일 바이트는 디스크에 그대로지만 메모리상 0 entries.
4. `merged = {...canonicalizedTarget.store(=빈), ...canonicalizedLegacy.store}` → legacy-only (:1170-1177).
5. save 게이트 `(legacyParsed.ok || targetParsed.ok) && (legacy 또는 target 비어있지 않음)` 에서 `legacyParsed.ok=true` 이므로 통과 (:1197-1199).
6. `saveSessionStore(targetStorePath, normalized=legacy-only, {skipMaintenance:true})` 가 손상 타깃을 legacy-only 로 덮어씀 (:1209). 비가역.

## 근본 원인 분석
1. save 게이트 조건이 OR (`legacyParsed.ok || targetParsed.ok`) 라서 target 이 unreadable 이어도 legacy 만 정상이면 진입한다. 이때 save 에 들어가는 `merged` 는 빈 target 기준이므로 사실상 legacy-only 가 손상 원본을 대체한다 (:1197).
2. 근본적으로 `readSessionStoreJson5` 가 손상(read/parse 실패)을 빈 store 로 둔갑시키는 try/catch-swallow 설계라서, `ok` 플래그를 호출부가 검사하지 않으면 "손상"과 "빈 파일"이 구분 불가하다 (state-migrations.fs.ts:47).
3. legacy 손상에는 `:1191` warn 과 `:1239` 의 `legacyParsed.ok` 가드(삭제 차단)가 있으나, target 손상에 대응하는 `targetParsed.ok` 검사가 save 경로에 전혀 없어 방어가 비대칭이다 (:1239).
4. 전체 마이그레이션이 다단계 fs 연산을 묶는 트랜잭션/체크포인트/백업 없이 진행되어(grep 결과 트랜잭션·락·CAS match 0) 손상 데이터 보존 정책 자체가 부재하다 (:1209).

## 영향
impact_hypothesis: data-loss. target `sessions.json` 이 손상된 상태에서 legacy sessions 가 동시에 존재하는 사용자가 doctor/cold-startup 마이그레이션을 1회 돌리면, 손상되었지만 부분 복구 가능했던 target 세션 레코드(legacy 미존재 키)가 영구 소실된다. 복구 불가(원본 덮어쓰기). 호출 빈도는 낮으나(cold path, process 당 1회 `autoMigrateChecked`) 손상 직후 사용자가 복구를 가장 원하는 시점에 정확히 마지막 사본을 파괴한다.

재현 시나리오: (a) target `agents/{id}/sessions/sessions.json` 에 trailing garbage 주입해 JSON5 파싱 실패하게 만들고 비-legacy 키 N개 포함. (b) legacy `sessions/sessions.json` 에 정상 키 M개 배치. (c) `runLegacyStateMigrations` 실행. (d) 결과 target 파일에 legacy M개만 남고 target N개 소실, warning 없음(legacy 가 ok 라서). R-7 준수: production caller(doctor/startup)가 타는 동일 branch(`migrateLegacySessions`)를 그대로 exercise 하며 손상 주입 시점이 실제 read 직전이라 합치.

## 반증 탐색
- 트랜잭션/락/CAS 경계: `rg 'withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|assertBaseHashMatch|checkpoint|transaction' src/infra/state-migrations.ts src/infra/state-migrations.fs.ts` → match 0. 다단계 이동을 묶는 경계 없음.
- atomic sibling: `saveSessionStore` (src/config/sessions/store.ts:604) 는 `runExclusiveSessionStoreWrite` 락 + 단일 파일 원자 write 라 그 자체는 안전. 따라서 이 FIND 는 단일파일 원자성 결함이 아니라, atomic write 의 *입력*이 손상 원본을 무시하고 legacy-only 로 계산되는 cross-store 병합 결정 오류다.
- recovery 비대칭: legacy 손상은 `:1191` warn + `:1239` `legacyParsed.ok` 가드로 보존되나, target 손상엔 동등한 보호가 없음. 이 비대칭이 결함의 핵심 warrant.
- derived-cache 여부: sessions.json 은 세션 SoT(파생 캐시 아님). 재생성 불가 → 자가복구 불가.
- 호출 빈도: cold doctor/startup 전용, ordinary CLI 는 `migrateState:false`(config-guard.ts:58-62)로 skip, process 당 `autoMigrateChecked` 1회. hot-path 아님 → severity 를 P0 에서 P1 로 조정.

## Self-check
### 내가 확실한 근거
- `targetParsed.ok` 가 save 분기 :1197-1216 어디에서도 검사되지 않음 (Read 로 확인).
- `readSessionStoreJson5` 가 손상을 `{store:{}, ok:false}` 로 swallow (state-migrations.fs.ts:47-73).
- 트랜잭션/락 grep match 0.
- save 게이트가 OR 조건 `(legacyParsed.ok || targetParsed.ok)` (:1197-1198).

### 내가 한 가정
- legacy 가 정상이고 비어있지 않은 동시-존재 상태가 실제 발생 가능하다고 가정(legacy→target 마이그레이션 정의상 둘 다 존재 가능).
- target 손상이 "trailing garbage / 부분 truncate" 같은 부분 복구 가능 형태라고 가정. 완전 0바이트면 소실량은 작지만 메커니즘은 동일.

### 확인 안 한 것 중 영향 가능성
- target 손상이 발생하는 실제 빈도(다른 writer 의 비원자 쓰기 등)는 이 셀 범위 밖이라 미확인. 빈도가 낮으면 발현은 드묾.
- doctor preview/confirm UI 가 사용자에게 "손상 감지" 를 별도 경고하는지 미확인(allowed_paths 밖). 경고가 있어도 데이터는 이미 덮어써진 뒤일 가능성.
