---
candidate_id: CAND-046
type: epic
finding_ids:
  - FIND-config-io-data-integrity-001
  - FIND-config-io-data-integrity-002
cluster_rationale: |
  공통 근본 원인 (cross-cut within file, clusterer.md Step 2/3): config/io.ts 의 두
  복구/보상(compensation) 경로가 atomic 헬퍼를 우회하고 raw fs write 로 대상 파일을
  in-place 덮어쓴다. 같은 모듈에 atomic sibling(replaceFileAtomic / replaceFileAtomicSync,
  io.ts:359/1445/2414)이 명백히 존재하고 정상 write·롤백 경로는 모두 그것을 쓰는데, 이
  두 경로만 raw write 를 택해 crash 시 truncate 윈도우를 남긴다. atomic 규율이 같은
  파일 안에서 "정상 경로 vs 복구 경로" 로 불균일하다는 단일 축이다.

  각 FIND root_cause_chain 인용:
  - FIND-config-io-data-integrity-001 root_cause_chain[0] ("왜 복구가 비원자적인가"):
    "복구 write 가 replaceFileAtomic 이 아닌 fs.promises.writeFile 로 메인 configPath
    를 직접 덮어쓴다" (evidence_ref: src/config/io.ts:1093)
  - FIND-config-io-data-integrity-001 root_cause_chain[1] ("왜 이것이 결함인가, 불균일"):
    "동일 io.ts 의 정상 config write(io.ts:2414)와 롤백(io.ts:359)은 모두 replaceFileAtomic
    을 쓰는데 이 복구 경로만 raw write 를 쓴다" (evidence_ref: src/config/io.ts:359)
  - FIND-config-io-data-integrity-002 root_cause_chain[0] ("왜 롤백이 비원자적인가"):
    "store 롤백이 raw deps.fs.writeFileSync 로 migration.filePath 를 in-place 덮어쓴다"
    (evidence_ref: src/config/io.ts:1626)
  - FIND-config-io-data-integrity-002 root_cause_chain[1] ("왜 이것이 불균일인가"):
    "install-records store 의 정상 write 경로는 writeJson → replaceFileAtomic(temp+rename)
    을 쓰는데 롤백만 raw write" (evidence_ref: src/infra/json-files.ts:94)

  epic 으로 묶는 이유: 두 결함은 같은 파일(config/io.ts)의 복구/보상 경로이고, 결함의
  본질(raw in-place write → truncate 비원자 창)과 위반된 기준선(같은 트리의
  replaceFileAtomic 규율)이 동일하다. 공통 인프라 축(복구/롤백 경로에도 atomic 파일
  교체 규율을 적용)을 다루므로 GH Issue 1건 + 자식 task 로 묶는 것이 두 건 분산 발행보다
  자연스럽다. (해결책 자체는 본 CAND 범위 밖.) severity 는 두 FIND 중 최고값 P2 상속.
proposed_title: "config/io.ts: prefix-recovery 와 install-records rollback 의 복구/보상 경로가 atomic 헬퍼 없이 raw write 로 in-place 덮어쓰기 → crash 시 truncate"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-29
---

# config/io.ts: 복구/보상 경로가 atomic 헬퍼 없이 raw write 로 in-place 덮어쓰기 → crash 시 truncate

## 공통 패턴

config/io.ts 의 두 복구/보상(compensation) 경로가 atomic 파일 교체를 우회한다:

- 정상 config write(io.ts:2414), config 롤백(io.ts:359), `replaceConfigFileSync`(io.ts:1445)
  는 모두 `replaceFileAtomic`/`replaceFileAtomicSync`(temp 작성 후 atomic rename,
  src/infra/replace-file.ts·src/infra/json-files.ts:94 재노출)를 쓴다.
- 그러나 두 복구/보상 경로만 raw `fs.promises.writeFile`(FIND-001) / raw `writeFileSync`(FIND-002)
  로 대상 파일을 in-place 덮어쓴다. in-place write 는 대상을 즉시 열어(또는 O_TRUNC 후)
  순차 기록하므로, 그 중간에 SIGKILL/전원차단/IO-fault 가 들어가면 부분 기록(truncate)
  상태가 그대로 영속된다.
- 결과적으로 손상을 고치려는 복구(FIND-001) 또는 실패를 되돌리려는 보상 롤백(FIND-002)
  자체가 추가 손상을 만들 수 있다 — atomic 규율이 같은 파일 안에서 "정상 경로 vs 복구
  경로" 로 불균일하다.

핵심: atomic 헬퍼가 같은 모듈에 존재하고 정상/롤백 경로는 실제로 그것을 쓰는데, 더
취약한 시점(이미 손상되었거나 write 가 실패한 직후)에 도는 이 두 경로만 raw write 를
택했다. "왜 여기만 raw" warrant 가 두 FIND 모두에서 같은 atomic sibling 을 기준으로
성립한다.

## 관련 FIND

- FIND-config-io-data-integrity-001 (P2): `io.ts:1093-1097`. config.json 의 non-JSON
  prefix 자동 복구가 메인 config 를 raw `fs.promises.writeFile` 로 in-place 덮어쓴다.
  복구 write 도중 SIGKILL/전원차단 시 config.json 이 partial-byte truncate 상태로 영속,
  다음 부팅에서 JSON.parse 실패로 config 로드 불가. 원본은 clobber-snapshot(io.ts:1087)
  에만 남아 수동 복원 필요. config.json 은 사용자 authored SoT 라 자가복구 불가. 호출은
  openclaw doctor cold path(doctor-config-preflight.ts:123)라 빈도 낮음 → P2.

- FIND-config-io-data-integrity-002 (P3): `io.ts:1625-1631`. shipped plugins.installs 를
  config→plugin-index install-records store 로 마이그레이션한 뒤 config write 실패 시
  store 파일을 raw `writeFileSync` 로 이전 내용으로 되돌린다. 롤백 write 도중 crash 시
  store 가 partial-byte truncate → 다음 read 의 install-records 파싱 실패. 롤백 시점엔
  config 에서 install records 가 이미 strip(io.ts:1496)된 후라 cross-store 발산(config
  atomic-commit / store partial) 가능. 트리거가 "config write 실패 + 롤백 중 crash" 이중
  드문 조건이라 위생 수준 → P3. (FIND 본문은 io.ts:1429 restoreFileSnapshotSync 도 동일
  패턴임을 mechanism 으로 포괄.)
