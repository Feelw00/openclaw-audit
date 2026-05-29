# infra-state-migrations 도메인 노트

openclaw 의 legacy state 마이그레이션 서브시스템(`src/infra/state-migrations.ts` + `src/infra/state-migrations.fs.ts`)에 대한 영구 관찰 기록. 페르소나/세션별로 append-only 로 추가.

---

### data-integrity-auditor (2026-05-29) — cross-store-consistency 축

셀: `infra-state-migrations-cross-store-consistency`. allowed_paths: `src/infra/state-migrations.ts`, `src/infra/state-migrations.fs.ts`. upstream 61c538e2fc.

#### 대상 파일 현황

| 파일 | LOC | 책임 |
|---|---|---|
| `src/infra/state-migrations.ts` | 1611 | legacy state-dir/sessions/agent-dir/channel-plan 자동 마이그레이션 본체 + 세션 키 canonicalize |
| `src/infra/state-migrations.fs.ts` | 73 | fs 헬퍼(safeReadDir/existsDir/ensureDir/fileExists) + JSON5 세션 store 파서(읽기 실패를 swallow) |

#### 마이그레이션 진입점 & 호출 빈도 (cold-path)

- `autoMigrateLegacyState` (:1512): `autoMigrateChecked` once-flag(:1524) → process 당 1회. caller: `src/commands/doctor-state-migrations.ts`, `autoMigrateLegacyAgentDir`(:1340 wrapper).
- `runLegacyStateMigrations` (:1310): doctor 헬스 경로 `src/flows/doctor-health-contributions.ts:308`.
- `autoMigrateLegacyStateDir` (:870): `autoMigrateStateDirChecked` once-flag(:875). caller: `src/commands/doctor-config-preflight.ts:102`.
- `migrateOrphanedSessionKeys` (:1366): startup 경로 `src/gateway/server-startup-session-migration.ts`.
- 핵심: ordinary CLI startup 은 `runDoctorConfigPreflight({migrateState:false})` (config-guard.ts:58-62)로 전체 state 마이그레이션을 skip. 전 마이그레이션은 cold doctor/startup 전용 → hot-path 아님. 따라서 impact 가 높아도(비가역 1회성) 빈도 낮아 P2 기준이 기본.

#### 다단계 이동 시퀀스 (runLegacyStateMigrations, :1310-1338)

순차 await, 트랜잭션 경계 없음:
1. plugin-state import plans (`runLegacyMigrationPlans` plugin-state-import) — store.register 영속 + legacy source `renameSync` 아카이브(:213, :263).
2. `migrateLegacySessions` — `saveSessionStore` target 갱신(:1209), legacy 파일 `renameSync`(:1232), legacy store `rmSync`(:1242), legacy dir backup rename(:1254).
3. `migrateLegacyAgentDir` — agent 파일 `renameSync`(:1284), legacy dir backup rename(:1300).
4. channel move/copy plans — `renameSync`(:278) / `copyFileSync`(:281).

destructive fs ops 전체: renameSync ×8, copyFileSync ×1, rmSync ×1, rmdirSync ×1, symlinkSync ×2 (:263,278,281,766,966,978,986,996,1232,1242,1254,1284,1300).

#### 롤백 분석

- 트랜잭션/2PC/체크포인트/락/CAS: `rg 'withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|assertBaseHashMatch|checkpoint|transaction'` → 두 파일 모두 match 0. **트랜잭션 프리미티브 전무.**
- 유일한 명시적 롤백: `autoMigrateLegacyStateDir` :996 `renameSync(targetDir, legacyDir)` — symlink 생성 실패 시 *단일 state-dir rename* 만 되돌림. 멀티스토어 시퀀스(sessions/agent/channel)에는 적용 안 됨. 이 수동 롤백의 존재 자체가 트랜잭션 부재의 방증.
- 부분 멱등 보호: `fileExists(targetPath)` skip(:272, :1228, :1280) 가드가 일부 재시도 안전을 주나, legacy 삭제(rmSync) 후 부분상태에선 `detect` 의 hasLegacy 판정이 달라져 완전 회복 미보장.
- swallow 패턴: `readSessionStoreJson5`(.fs.ts:47-58) / `parseSessionStoreJson5`(:60-73) 가 read/parse 예외를 try/catch 로 삼키고 `{store:{}, ok:false}` 반환. 호출부가 `ok` 를 검사하지 않으면 손상 store 가 빈 store 로 둔갑.

#### 발견 요약

- **FIND-001 (P1, cross-store-gap)**: 손상된 target `sessions.json` 이 legacy-only 병합본으로 무조건 덮어써져 영구 유실. save 게이트가 OR 조건 `(legacyParsed.ok || targetParsed.ok)`(:1197)이고 `targetParsed.ok` 가 save 경로에서 검사되지 않음. legacy 손상은 :1191/:1239 로 보호되나 target 손상은 비대칭으로 무방비. 손상 직후 마지막 사본을 파괴 → 복구 불가. cold-path라 P1.
- **FIND-002 (P2, cross-store-gap)**: 4단계 스토어 이동(plugin-state/sessions/agent-dir/channel)이 트랜잭션 없이 순차 await(:1316-1323). 중간 throw(ENOSPC/EACCES/SIGKILL) 시 앞 단계 비가역 renameSync/rmSync 가 롤백 안 되어 일부 스토어는 새 레이아웃·일부는 legacy 레이아웃으로 split state 영속. 함수 레벨 try/catch·보상 로직 없음. 빈도 낮음 → P2.

#### 탐지 카테고리 적용 (agents/data-integrity-auditor.md)

- [x] A. 비원자 단일파일 쓰기 — applied/skip: `saveSessionStore`(config/sessions/store.ts:604)가 락+원자 write 라 단일파일 원자성은 안전. 마이그레이션은 직접 raw writeFileSync 안 씀(검색 결과 0). 단일파일 결함 아님.
- [x] B. 다중 syscall 비원자 (write+chmod/sidecar) — skip: 마이그레이션에 chmod/sidecar 결합 없음. symlink fallback(:978-1011)은 롤백 분기 있음.
- [x] C. 부분 multi-write 불일치 — skip: `void`/fire-and-forget write 없음(전부 await). `rg 'void .*write|void .*persist'` 마이그레이션 내 0.
- [x] D. 복구/롤백 비원자 — applied(부분): :996 단일 rename 롤백은 멀티스토어 미보호(FIND-002 근거에 반영). 그 자체 raw write 아님이라 별도 FIND 아님.
- [x] E. 교차 스토어 경계 부재 — applied: FIND-001(병합 결정), FIND-002(시퀀스 트랜잭션 부재).

---

### clusterer (2026-05-29)

- CAND-049 (epic, P1): FIND-...-001(P1) + -002(P2) 묶음. 공통 원인 "infra/state-migrations.ts 의
  legacy→target 마이그레이션이 다수 영속 스토어를 다루면서 묶는 트랜잭션/체크포인트/백업 경계 없이
  비가역 fs 연산을 즉시 커밋(grep withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|
  checkpoint → match 0)". FIND-001 root_cause_chain[2](:1239 target 손상 비대칭 무방비) +
  [3](:1209 손상 데이터 보존 정책 부재), FIND-002 root_cause_chain[0](:1316 멀티스토어 비가역
  순차 커밋) + [3](:996 단일 rename 롤백은 멀티스토어 미적용)가 동일 축(마이그레이션 모듈 교차-스토어
  경계 프리미티브 부재)으로 수렴. saveSessionStore(store.ts:604)는 단일파일 원자성 안전 → 결함은
  단일파일 비원자가 아니라 교차-스토어/멀티스토어 경계 부재(두 FIND 공통 반증) → epic, severity
  최고값 P1 상속.
- 도메인 분리 규율 적용: 타 도메인과 "트랜잭션/경계 부재" root cause 유사하나 별도 PR scope 이라
  병합 금지, infra-state-migrations 단독 CAND.
