---
id: FIND-config-io-data-integrity-002
cell: config-io-data-integrity
title: plugin-index install-records rollback uses raw writeFileSync (non-atomic)
file: src/config/io.ts
line_range: 1625-1631
evidence: "```ts\nif (migration.previousFile.existed) {\n  deps.fs.writeFileSync(migration.filePath,\
  \ migration.previousFile.raw, {\n    encoding: \"utf-8\",\n    mode: 0o600,\n  });\n\
  \  return true;\n}\n```\n"
symptom_type: data-integrity-gap
problem: shipped plugins.installs 레코드를 config.json 에서 plugin-index install-records
  store 로 마이그레이션한 뒤, 메인 config write 가 실패하면 그 store 파일을 raw writeFileSync 로 이전 내용으로
  되돌린다. 이 롤백 write 도중 crash 시 install-records store 가 부분 기록(truncate) 상태로 영속된다.
mechanism: '1. config write 진입 시 ensureShippedPluginInstallConfigRecordsMigratedForWrite
  가 config 의 plugins.installs 를 plugin-index install-records store 로 옮기며 이전 store
  파일 스냅샷을 캡처 (io.ts:1564-1601).

  2. 메인 config 는 replaceFileAtomic 으로 commit 시도 (io.ts:2414).

  3. config commit 전 throw (io.ts:2449 !configCommitted) 또는 post-commit 후속 단계 실패 (io.ts:2442)
  시 rollbackShippedPluginInstallConfigWriteMigration 호출.

  4. 롤백이 store 파일(migration.filePath)을 raw writeFileSync 로 previousFile.raw 로 in-place
  덮어씀 (io.ts:1626). temp+rename 없음.

  5. crash 주입 시점 = 4 의 writeFileSync 가 일부 바이트만 기록한 순간. store 파일이 잘린 JSON 으로 남음.

  6. 다음 read 에서 install-records store JSON.parse 실패 가능 → migration 캐시 손상.

  '
root_cause_chain:
- why: 왜 롤백이 비원자적인가
  because: store 롤백이 raw deps.fs.writeFileSync 로 migration.filePath 를 in-place 덮어쓴다.
  evidence_ref: src/config/io.ts:1626
- why: 왜 이것이 불균일인가
  because: install-records store 의 정상 write 경로는 writePersistedInstalledPluginIndex(Sync)
    → saveJsonFile/writeJson 으로, writeJson 은 replaceFileAtomic(temp+rename)을 쓴다 (src/infra/json-files.ts:94).
    롤백만 raw write.
  evidence_ref: src/infra/json-files.ts:94
- why: 왜 raw write 가 손상을 유발하나
  because: in-place writeFileSync 는 대상을 즉시 truncate 후 순차 기록하므로 중간 crash 시 부분 바이트가
    영속된다. 같은 io.ts 의 config 경로(io.ts:1445 replaceFileAtomicSync)는 atomic 으로 보호되는데
    store 롤백만 raw.
  evidence_ref: src/config/io.ts:1445
- why: 왜 단일 store 손상이 cross-store 발산이 되나
  because: install records 는 config 에서 strip 되어 store 로만 옮겨진 상태(io.ts:1496-1499).
    store 롤백 write 중 crash 시 store 는 partial, config 는 이미 atomic commit 됐을 수 있어(io.ts:2442
    post-commit 경로) 두 영속 위치가 install records 에 대해 발산.
  evidence_ref: src/config/io.ts:2442
impact_hypothesis: data-loss
impact_detail: '정성: install-records store 가 부분 기록으로 손상되면 다음 read 의 install-records
  파싱 실패 가능. install records 는 config 의 plugins.installs 에서 마이그레이션된 derived 성격이라 일부
  재구축 여지가 있으나, 롤백 시점엔 config 에서 이미 strip 된 후라 (io.ts:1496) 양쪽 모두 누락될 수 있다. 트리거 조건:
  config write 실패(권한/preflight throw) + 롤백 write 중 crash 라는 이중 드문 조건이라 빈도 매우 낮음. 보상(compensation)
  경로 자체의 비원자성 (D 카테고리).

  '
severity: P3
counter_evidence:
  path: src/infra/json-files.ts
  line: '94'
  reason: 'atomic sibling 존재 — install-records store 정상 write 는 writeJson → replaceFileAtomic(temp+rename,
    json-files.ts:94). 롤백만 raw writeFileSync. 같은 io.ts config 경로도 atomic(io.ts:1445).
    불균일 입증.

    derived-cache 부분 반증: install records 는 config.plugins.installs 에서 옮겨진 derived
    성격이라 손상 시 일부 재마이그레이션 가능 → severity 하향 P3. 단 롤백 시점엔 config 에서 strip 된 후라 완전 자가복구는
    아님. 트리거가 이중 드문 조건(config write fail + rollback-during-crash)이라 위생 수준.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
cross_refs: []
---
# plugin-index install-records rollback uses raw writeFileSync (non-atomic)

## 문제
config write 시 shipped `plugins.installs` 레코드를 config.json 에서 plugin-index install-records store 파일로 마이그레이션한다. 메인 config write 가 실패하면 이 store 파일을 raw `fs.writeFileSync` 로 이전 내용으로 되돌리는 보상(rollback)을 수행한다 (io.ts:1626). 이 롤백 write 도중 crash 가 나면 store 파일이 부분 기록(truncate) 상태로 영속된다. 보상 경로 자체가 비원자적이다.

## 발현 메커니즘
1. config write 진입 시 `ensureShippedPluginInstallConfigRecordsMigratedForWrite` 가 config 의 `plugins.installs` 를 plugin-index install-records store 로 옮기고 이전 store 파일 스냅샷을 캡처한다 (io.ts:1564-1601). 이 과정에서 config 쪽 install records 는 strip 된다 (io.ts:1496).
2. 메인 config 는 `replaceFileAtomic` 으로 commit 시도 (io.ts:2414).
3. config commit 전 throw 시 `!configCommitted` 분기(io.ts:2449), 또는 post-commit 후속 rollback 콜백(io.ts:2442) 에서 `rollbackShippedPluginInstallConfigWriteMigration` 이 호출된다.
4. 롤백은 hash 일치 확인 후 store 파일(`migration.filePath`)을 raw `writeFileSync` 로 `previousFile.raw` 로 in-place 덮어쓴다 (io.ts:1626). temp+rename 보호 없음.
5. crash 주입 시점은 4의 writeFileSync 가 일부 바이트만 기록한 순간 — store 파일이 잘린 JSON 으로 남는다.
6. 다음 read 에서 install-records store 의 JSON.parse 가 실패할 수 있어 migration 캐시가 손상된다.

## 근본 원인 분석
- 롤백 write 가 atomic helper 를 안 쓴다. io.ts:1626 의 raw `writeFileSync` 는 store 정상 write 경로(`writeJson` → `replaceFileAtomic`, json-files.ts:94)와 달리 temp+rename 이 없다.
- 같은 io.ts 의 config 경로(io.ts:1445 `replaceFileAtomicSync`)는 sync 컨텍스트에서도 atomic 을 쓰는데, 이 store 롤백만 raw 다 — 불균일.
- in-place `writeFileSync` 는 대상을 즉시 truncate 후 순차 기록하므로 중간 crash 시 부분 바이트가 영속된다.
- 단일 store 손상이 cross-store 발산으로 번진다: install records 는 롤백 시점에 config 에서 이미 strip 된 상태(io.ts:1496)이고 메인 config 가 atomic commit 된 post-commit 경로(io.ts:2442)에서도 이 롤백이 돌 수 있어, config 와 store 가 install records 에 대해 서로 다른 상태로 발산할 수 있다.

## 영향
impact_hypothesis: data-loss.
재현 시나리오: shipped plugins.installs 가 있는 config + config write 가 권한/preflight 로 실패 → 롤백(io.ts:1626) 진행 중 SIGKILL → install-records store partial-byte truncate → 다음 read 의 store 파싱 실패.
install records 는 config 의 plugins.installs 에서 마이그레이션된 derived 성격이라 일부 재구축 여지가 있으나, 롤백 시점엔 config 에서 strip 된 후라 완전 자가복구는 아니다. 트리거가 "config write 실패 + 롤백 중 crash" 라는 이중 드문 조건이라 빈도는 매우 낮아 위생 수준(P3).

## 반증 탐색
- atomic helper 존재 (확인): store 정상 write 는 `writeJson`/`saveJsonFile` → `replaceFileAtomic`(temp+rename, src/infra/json-files.ts:94). 같은 io.ts config 경로도 atomic(io.ts:1445). 롤백만 raw → 불균일 입증.
- derived-cache 여부 (확인): install records 는 config.plugins.installs 에서 옮겨진 derived 성격 → 일부 재마이그레이션 가능 → severity 하향. 단 롤백 시점에 config 에서 strip 된 후(io.ts:1496)라 완전 자가복구 아님.
- 트랜잭션/락 (확인): 마이그레이션-commit-롤백이 한 논리 단위인데 파일 단위 atomic 만 있고 두 스토어를 묶는 트랜잭션 경계는 없음. `rg -n "withWriteTransaction|BEGIN IMMEDIATE|withFileLock" src/config` → 매치 없음(clobber-snapshot 의 자체 lock 제외).
- 호출 빈도 (확인): config write + shipped plugins.installs 존재 + write 실패라는 조건부 경로. cold path.
- 기존 테스트 (확인): io.write-config.test.ts 에 migration/rollback 동작 테스트는 있으나 rollback-write-during-crash 원자성 테스트는 없음.

## Self-check
### 내가 확실한 근거
- io.ts:1626 이 raw `writeFileSync` 로 store filePath 를 in-place 덮어쓴다 (Read 1625-1630 확인).
- store 정상 write 는 atomic (src/infra/json-files.ts:94 replaceFileAtomic, Grep 확인).
- 같은 패턴이 io.ts:1429 restoreFileSnapshotSync 에도 존재 (동일 store 롤백, cross_ref 후보).
- config 경로는 atomic (io.ts:1445).

### 내가 한 가정
- in-place writeFileSync 중 crash 시 store 파일이 부분 기록으로 truncate 된다는 POSIX 일반 가정.
- migration.filePath 가 install-records store 라는 것은 io.ts:1564 resolveInstalledPluginIndexRecordsStorePath 호출 경로 추적에 근거 (store-path 구현 세부는 allowed_paths 밖이라 미열람).

### 확인 안 한 것 중 영향 가능성
- install records 가 손상돼도 다음 read 에서 config 재마이그레이션으로 완전 복구되면 영향이 P3 보다 더 낮을 수 있음 (단 롤백 시점 config strip 으로 완전 복구는 불확실).
- io.ts:1429 restoreFileSnapshotSync 도 동일 결함 패턴 — 별도 FIND 분리 대신 동일 store 동일 증상이라 본 카드에 mechanism 으로 포괄, 필요 시 clusterer 가 cross_ref 처리.
