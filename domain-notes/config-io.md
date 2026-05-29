# domain-notes: config-io

셀: config-io-data-integrity / 도메인: config-io
작성: data-integrity-auditor, 2026-05-29
upstream: 61c538e2fc

allowed_paths:
- src/config/io.ts (2770 lines)
- src/config/io.write-prepare.ts (942 lines)
- src/config/io.clobber-snapshot.ts (342 lines)
- src/config/mutate.ts (691 lines)

## atomic 기준선 (반증의 핵심)

이 도메인은 atomic-write helper 가 명확히 존재한다. raw write 는 "왜 여기만 raw" warrant 가 성립.

- `replaceFileAtomic` / `replaceFileAtomicSync` 는 `src/infra/replace-file.ts` 에서 re-export.
  실제 구현은 외부 패키지 `@openclaw/fs-safe/atomic` (temp+rename, in-tree 수정 불가).
  → 결함은 항상 "in-tree 소비처가 이 helper 를 안 쓴 것" 으로만 지목. 패키지 내부는 지목 금지.
- `src/infra/json-files.ts:94` 의 `writeJson` 도 동일 `replaceFileAtomic` 사용 (plugin-index store 정상 write 경로가 여길 탐).

## write 경로별 atomic 여부 표

| 위치 | 대상 파일 | 방식 | atomic? | execution condition | 비고 |
|---|---|---|---|---|---|
| io.ts:359 rollbackConfigFileWriteIfUnchanged | 메인 config.json | replaceFileAtomic | YES | recovery/rollback | config 롤백조차 atomic — 반증 기준선 |
| io.ts:1445 replaceConfigFileSync | 메인 config.json | replaceFileAtomicSync | YES | conditional (migration write) | sync config write |
| io.ts:2414 writeConfigFile commit | 메인 config.json | replaceFileAtomic | YES | unconditional (정상 write) | beforeRename backup 포함 |
| mutate.ts:278, 298 | 메인 config.json | replaceFileAtomic | YES | unconditional | mutate 경로 모두 atomic |
| io.ts:1093 persistPrefixedConfigRecovery | 메인 config.json | raw fs.promises.writeFile | NO | recovery-only (doctor cold path) | FIND-001. 복구 경로 자체 비원자 |
| io.ts:1626 rollbackShippedPluginInstallConfigWriteMigration | plugin-index install-records store | raw writeFileSync | NO | compensation (config write fail) | FIND-002 |
| io.ts:1429 restoreFileSnapshotSync | plugin-index install-records store | raw writeFileSync | NO | compensation (replaceConfigFileSync throw) | FIND-002 와 동일 패턴 (cross_ref 후보) |
| io.ts:530/543 writeConfigHealthState(Sync) | config-health.json | raw writeFile + 별도 mkdir | NO | best-effort (catch 삼킴) | derived-cache → FIND 미생성 (R-5) |
| io.ts:2377 rejected payload | config.json.rejected.* | raw writeFile flag:wx | N/A | reject 경로 신규파일 | wx = 배타 신규, truncate 불가 → 안전 |
| io.clobber-snapshot.ts:28/285/324 | clobber snapshot 파일 | writeFile(Sync) flag:wx | N/A | snapshot 보관 신규파일 | wx 신규 + 자체 lock 보유 → 안전 |
| io.write-prepare.ts | (없음) | in-memory transform only | N/A | - | 디스크 write 없음, persistedCandidate 는 객체 |

## 발견 요약

- FIND-config-io-data-integrity-001 (P2, data-integrity-gap):
  io.ts:1093 persistPrefixedConfigRecovery 가 메인 config.json 복구를 raw writeFile 로 수행 (temp+rename 없음).
  손상 config 를 고치려는 복구 동작이 crash 시 추가 truncate 를 만듦 (D 카테고리, 복구 경로 자체 비원자).
  원본은 clobber-snapshot 백업에 남고 호출은 doctor cold path 라 P2. 같은 io.ts 의 config write/rollback 은 전부 atomic (불균일).

- FIND-config-io-data-integrity-002 (P3, data-integrity-gap + cross-store 발산 성격):
  io.ts:1626 (및 동일 패턴 io.ts:1429) plugin-index install-records store 롤백을 raw writeFileSync 로 수행.
  store 정상 write 는 json-files.ts:94 replaceFileAtomic 인데 롤백만 raw — 불균일.
  install records 가 config 에서 strip 후 옮겨진 derived 성격 + 이중 드문 트리거(config write fail + rollback-during-crash) 라 위생 수준 P3.

## 미생성(기각) 메모

- config-health.json (io.ts:530/543): read 실패 시 `{}` 로 graceful fallback (io.ts:507, 518) → 손상 자가복구되는 derived-cache. lastKnownGood fingerprint 도 hash 불일치 시 안전 측 fallback. R-5 derived-cache 분류 → FIND 금지.
- io.ts:2377 rejected payload / clobber-snapshot writes: 전부 `flag: "wx"` 배타 신규 생성이라 기존 파일 truncate 윈도우 없음. clobber-snapshot 은 자체 lock(io.clobber-snapshot.ts:5-7) 보유. 결함 아님.
- io.write-prepare.ts / mutate.ts in-memory transform: 디스크 write 는 mutate.ts:278/298 의 replaceFileAtomic 뿐 (atomic). 결함 아님.

## 탐지 카테고리 적용

- [x] A 비원자 단일파일 쓰기 — FIND-001 (config recovery), FIND-002 (store rollback)
- [x] B 다중 syscall 비원자 — health-state mkdir+write (io.ts:529-530) 검토했으나 derived-cache 라 미생성
- [x] C 부분 multi-write 불일치 — `void ...write` 패턴 Grep 매치 없음 (allowed_paths)
- [x] D 복구/롤백 비원자 — FIND-001, FIND-002 의 주 카테고리
- [x] E 교차 스토어 경계 부재 — config+plugin-index 마이그레이션이 파일 atomic 만 있고 트랜잭션 경계 없음 (FIND-002 mechanism 에 포함). `withWriteTransaction|BEGIN IMMEDIATE|withFileLock` Grep → clobber 자체 lock 외 매치 없음.

### clusterer (2026-05-29)

- CAND-046 (epic, P2): FIND-config-io-data-integrity-001(P2) + -002(P3) 묶음. 공통 원인
  "config/io.ts 의 복구/보상 경로가 같은 모듈의 atomic 헬퍼(replaceFileAtomic, io.ts:359/1445/2414)
  를 우회하고 raw write 로 in-place 덮어쓰기 → crash 시 truncate". FIND-001 root_cause_chain[0]
  (io.ts:1093 raw fs.promises.writeFile) + [1](io.ts:359 atomic sibling 대비 불균일),
  FIND-002 root_cause_chain[0](io.ts:1626 raw writeFileSync) + [1](json-files.ts:94 atomic
  대비 불균일)가 동일 축(정상/롤백 경로는 atomic, 복구/보상 경로만 raw)으로 수렴 → epic.
  severity 최고값 P2 상속.
- 도메인 분리 규율 적용: 타 도메인(task-registry/secrets-apply/infra-state-migrations)과
  root cause 가 "atomic/경계 부재" 로 유사하나 별도 PR scope 이므로 병합 금지, config-io 단독 CAND.
