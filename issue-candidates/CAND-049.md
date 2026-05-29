---
candidate_id: CAND-049
type: epic
finding_ids:
- FIND-infra-state-migrations-cross-store-consistency-001
- FIND-infra-state-migrations-cross-store-consistency-002
cluster_rationale: "공통 근본 원인 (cross-cut within file, clusterer.md Step 2/3): src/infra/state-migrations.ts\n\
  의 legacy→target 마이그레이션이 다수의 영속 스토어를 다루면서 묶는 트랜잭션/체크포인트/\n백업 경계 없이 비가역 fs 연산을 즉시\
  \ 커밋한다. 두 FIND 의 root_cause_chain 이 동일하게\n\"트랜잭션/락/CAS 가 전혀 없다(rg withWriteTransaction|BEGIN\
  \ IMMEDIATE|withFileLock|baseHash|CAS|checkpoint\n→ match 0)\" 라는 같은 grep 증거로 수렴한다.\
  \ 즉 한쪽은 손상-감지 분기의 비대칭 보호 부재\n(FIND-001), 다른 한쪽은 멀티스토어 시퀀스의 트랜잭션 부재(FIND-002)지만,\
  \ 둘 다 \"이\n마이그레이션 모듈에 교차-스토어 경계 프리미티브가 부재\" 라는 단일 인프라 축의 발현이다.\n\n각 FIND root_cause_chain\
  \ 인용:\n- FIND-...-001 root_cause_chain[2] (\"왜 legacy 와 달리 target 손상에는 보호 분기가\n\
  \  없는가\"): \"legacy unreadable 은 :1191 warn + :1239 legacyParsed.ok 가드로 삭제를 막지만,\n\
  \  target unreadable 에 대응하는 targetParsed.ok 검사가 save 경로에 전혀 없어 비대칭 방어\"\n  (evidence_ref:\
  \ src/infra/state-migrations.ts:1239)\n- FIND-...-001 root_cause_chain[3] (\"왜 손상\
  \ 시 백업/abort 같은 안전판이 없는가\"):\n  \"마이그레이션이 다단계 fs 연산을 묶는 트랜잭션/체크포인트 없이 진행되며(grep:\n\
  \  withWriteTransaction|withFileLock|BEGIN|baseHash|CAS 전부 match 없음) 손상 데이터 보존\n\
  \  정책이 부재\" (evidence_ref: src/infra/state-migrations.ts:1209)\n- FIND-...-002 root_cause_chain[0]\
  \ (\"왜 부분 마이그레이션(split state)이 영속되는가\"):\n  \"4개 스토어 이동이 묶는 트랜잭션/2PC/체크포인트 없이 순차\
  \ await 되고, 각 단계가 비가역\n  renameSync/rmSync 를 즉시 커밋하므로 중간 throw 시 앞 단계만 적용된 채 종료\"\
  \n  (evidence_ref: src/infra/state-migrations.ts:1316)\n- FIND-...-002 root_cause_chain[3]\
  \ (\"왜 트랜잭션/락 보호가 아예 없는가\"): \"rg\n  withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|checkpoint\
  \ 가 두 파일에서\n  match 0 — 이 모듈은 트랜잭션 프리미티브를 전혀 쓰지 않음. autoMigrateLegacyStateDir 의\n\
  \  수동 롤백(:996)은 단일 rename 한정이라 멀티스토어 시퀀스엔 적용 안 됨\"\n  (evidence_ref: src/infra/state-migrations.ts:996)\n\
  \n두 FIND 의 counter_evidence 도 같은 사실을 공유한다: saveSessionStore(store.ts:604)는\n락+단일파일\
  \ atomic write 라 단일파일 원자성은 안전하므로, 결함은 단일파일 비원자가 아니라\n교차-스토어/멀티스토어 경계 부재다. 유일한 롤백(:996)은\
  \ 단일 state-dir rename 한정.\n\nepic 으로 묶는 이유: 같은 파일(infra/state-migrations.ts)의 같은\
  \ 마이그레이션 경로\n(runLegacyStateMigrations → migrateLegacySessions 등)가 같은 결함 클래스(트랜잭션/백업\n\
  경계 부재로 인한 비가역 1회성 손상)를 공유하고, 위반된 기준선(트랜잭션 프리미티브 부재,\ngrep match 0)도 동일하다. 마이그레이션\
  \ 트랜잭션/체크포인트/손상-보존 경계라는 공통\nsurface 를 다루므로 GH Issue 1건 + 자식 task 가 적합. severity\
  \ 는 최고값 P1 상속.\n(해결책 자체는 본 CAND 범위 밖.)\n"
proposed_title: 'infra/state-migrations.ts: legacy→target 마이그레이션이 트랜잭션/백업 경계 없이 비가역
  fs 연산 커밋 → 손상 target 덮어쓰기 영구 유실 + 멀티스토어 split state'
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-049-pre-20260529-070637.md
  measurements:
    scenario: proof-CAND-049
    trials: 2
    beforeHasSentinel: true
    targetFileExists: true
    overwritten: true
    targetCorruptBytesSurvived: false
    afterParsesAsObject: true
    targetOnlyKeyPresent: false
    legacyKeysPresent: true
    warnedAboutTargetCorruption: false
    afterKeys:
    - agent:main:hooks:legacy-key-1
    - agent:main:hooks:legacy-key-2
    warnings: []
    changes:
    - Merged sessions store → /tmp/claude-501/cand049-state-nWFj8W/agents/main/sessions/sessions.json
    threw: null
  scenario: proof-CAND-049
---

# infra/state-migrations.ts: 마이그레이션 경계 부재 → 손상 덮어쓰기 + 멀티스토어 split state

## 공통 패턴

`runLegacyStateMigrations` / `migrateLegacySessions` 경로는 legacy→target 마이그레이션에서
다수의 영속 스토어(sessions / agent dir / channel / plugin-state)를 다루면서 묶는
트랜잭션/2PC/체크포인트/백업 경계가 전무하다. R-3 grep
(`withWriteTransaction|BEGIN IMMEDIATE|withFileLock|baseHash|CAS|checkpoint`)이 두 파일
(state-migrations.ts / .fs.ts)에서 match 0 — 모듈이 트랜잭션 프리미티브를 전혀 쓰지 않는다.
이 부재가 두 갈래로 발현한다:

- **손상-감지 분기의 비대칭 보호 부재(FIND-001)**: save 게이트가 OR 조건
  `(legacyParsed.ok || targetParsed.ok)`(:1197)이고 `targetParsed.ok` 가 save 경로에서
  검사되지 않는다. legacy 손상은 :1191/:1239 로 보호되나 target 손상은 무방비라, 손상된
  target 이 legacy-only 병합본으로 무조건 덮어써진다.
- **멀티스토어 시퀀스의 트랜잭션 부재(FIND-002)**: 4단계 스토어 이동(:1316-1323)이 비가역
  renameSync/rmSync/copyFileSync 를 즉시 커밋하며 함수 레벨 try/catch·보상 로직이 없다.
  중간 throw(ENOSPC/EACCES/SIGKILL) 시 앞 단계만 적용된 채 split state 로 영속한다.

공통 기준선/반증: `saveSessionStore`(config/sessions/store.ts:604)는 락+단일파일 atomic
write 라 단일파일 원자성은 안전 — 결함은 단일파일 비원자가 아니라 교차-스토어 병합 결정
오류(FIND-001) 및 멀티스토어 시퀀스 경계 부재(FIND-002)다. 유일한 롤백(:996 단일 state-dir
rename)은 멀티스토어 시퀀스를 보호하지 못한다. sessions.json 등은 SoT 라 재생성 불가, 비가역
1회성이라 자동 복구 없음. 두 결함 모두 cold doctor/startup(process 당 autoMigrateChecked
1회) 경로지만, 손상/crash 가 겹치는 순간 마지막 사본을 비가역 파괴한다.

## 관련 FIND

- FIND-infra-state-migrations-cross-store-consistency-001 (P1): `state-migrations.ts:1197-1212`
  `migrateLegacySessions`. target sessions.json 이 손상(JSON5 파싱 실패)이면
  `readSessionStoreJson5` 가 `{store:{}, ok:false}` 로 swallow → targetStore={}. save
  게이트 OR 조건이 legacyParsed.ok 만으로 통과해(:1197) 손상 target 을 legacy-only 병합본으로
  `saveSessionStore`(:1209) 덮어씀. legacy 미존재 키였던 target 세션 레코드 영구 소실, 손상
  파일 수동 복구 기회도 상실. 비가역. legacy 손상엔 가드 있으나 target 손상엔 비대칭 무방비.

- FIND-infra-state-migrations-cross-store-consistency-002 (P2): `state-migrations.ts:1316-1323`
  `runLegacyStateMigrations`. 4단계(plugin-state import / sessions / agent dir / channel)를
  트랜잭션 없이 순차 await. 임의 단계 throw 시 앞 단계의 renameSync/rmSync(:1232/:1242/:1284)는
  롤백 안 됨 → 일부 스토어 새 레이아웃 / 일부 legacy 레이아웃 split state 영속. 예: sessions
  이동·legacy 삭제 후 agent dir 이동이 EACCES throw → sessions 새 위치 / agent dir legacy
  잔존. autoMigrateChecked once-flag 로 재시도 막히거나 detect 가 부분상태 오판 위험 → P2.
