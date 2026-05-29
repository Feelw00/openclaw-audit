---
candidate_id: CAND-048
type: epic
finding_ids:
  - FIND-secrets-apply-cross-store-consistency-001
  - FIND-secrets-apply-cross-store-consistency-002
cluster_rationale: |
  공통 근본 원인 (cross-cut within file, clusterer.md Step 2/3): secrets apply 의 write
  모드 커밋(apply.ts, 한 논리 트랜잭션 = plaintext 자격증명 → SecretRef 마이그레이션)이
  config.json + N개 auth-profiles.json + legacy auth-json + .env 를 다루면서 파일 간
  스토어 경계 보호를 결여한다. 두 FIND 는 같은 커밋 경로(apply.ts:818-855)의 같은 store
  집합(config↔auth-store)에 대한 두 갈래의 경계 부재이고, frontmatter 에서 서로 cross_refs
  되어 있어 동일 축으로 보는 저자 의도가 명시돼 있다(persona: "이미 cross_refs 된 경우 그
  의도 존중"). 두 FIND 의 반증(counter_evidence) 모두 동일한 비대칭을 근거로 든다 —
  config 경로(replaceConfigFile)는 withFileLock+baseHash CAS(mutate.ts:475/499)로 보호되는데
  satellite auth-store write 만 그 보호 밖이라는 "lock/CAS 가 한쪽에만"(E 카테고리) 비대칭.

  각 FIND root_cause_chain 인용:
  - FIND-...-001 root_cause_chain[0] ("config 와 satellite 스토어가 왜 한 트랜잭션으로
    묶이지 않는가"): "커밋 블록이 replaceConfigFile 1회 + for 루프 writeTextFileAtomic N회를
    단순 나열할 뿐 파일 간 2PC/저널/단일 lock 경계가 없다" (evidence_ref: src/secrets/apply.ts:843)
  - FIND-...-001 root_cause_chain[2] ("부분 커밋이 자동 복구되지 않는가"): "롤백이
    best-effort 다. restoreFileSnapshot 호출이 각각 try{}catch{} 로 복원 실패를 무시한다"
    (evidence_ref: src/secrets/apply.ts:850)
  - FIND-...-002 root_cause_chain[0] ("apply 의 auth-store write 가 동시 writer 와
    충돌하는가"): "apply 가 auth-profiles.json 을 writeTextFileAtomic 로 커밋할 때
    AUTH_STORE_LOCK_OPTIONS 파일락을 잡지 않아 정규 writer 와 mutual-exclusion 되지 않는다"
    (evidence_ref: src/secrets/apply.ts:818)
  - FIND-...-002 root_cause_chain[1] ("auth-profiles.json 에 정규 락 규약이 존재하는가"):
    "정규 writer updateAuthProfileStoreWithLock 은 withFileLock(authPath,
    AUTH_STORE_LOCK_OPTIONS) 안에서 reload→update→save 하며, 주석이 이 락의 목적이
    lost-update 방지임을 명시" (evidence_ref: src/agents/auth-profiles/store.ts:705)

  두 FIND 의 root cause 가 동일 stem 에서 갈라진다: apply 의 커밋 블록이 config↔auth-store
  를 다루면서 (1) 파일 간 트랜잭션/저널 경계(FIND-001)와 (2) 동시 writer 직렬화 락
  (FIND-002)을 둘 다 결여한다. 둘 다 같은 store 집합(자격증명 SoT)에 대한 발산을 영속시키며,
  src/secrets/ 전체에 트랜잭션/락 키워드 match 0(R-3 grep, 두 FIND counter_evidence 공통)
  이라는 동일 증거를 공유한다.

  epic 으로 묶는 이유: 같은 파일(secrets/apply.ts)의 같은 커밋 경로가 같은 store 집합
  (config↔auth-store)에 대해 경계 보호를 결여한다는 단일 인프라 축이다. config/auth-store
  교차-스토어 커밋의 경계 도입이라는 공통 surface 를 다루므로 GH Issue 1건 + 자식 task 가
  적합. 단, 두 FIND 의 fix 표면(파일 간 트랜잭션 경계 vs AUTH_STORE_LOCK 획득)이 구별되므로
  gatekeeper/SOL 단계에서 자식 task 로 분리 가능성이 높다. severity 는 두 FIND 모두 P1.
  (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "secrets apply: config↔auth-store 다중스토어 커밋이 파일 간 트랜잭션 경계와 AUTH_STORE_LOCK 을 모두 결여 → 부분 마이그레이션 / 동시 OAuth refresh lost-update"
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
---

# secrets apply: config↔auth-store 다중스토어 커밋의 경계 부재 (트랜잭션 + 락)

## 공통 패턴

`runSecretsApply` 의 write 모드 커밋(apply.ts:818-855)은 한 논리 트랜잭션(plaintext
자격증명 → SecretRef 마이그레이션)에서 config.json 을 먼저 커밋하고 이어서 N개
auth-profiles.json + legacy auth-json + .env 를 for 루프로 순차 커밋한다. 이 커밋 경로가
config↔auth-store 라는 같은 store 집합에 대해 두 갈래의 경계 보호를 결여한다:

- **파일 간 트랜잭션/저널 경계 부재(FIND-001)**: 각 파일은 writeTextFileAtomic 로 개별
  원자적이지만 파일 간 2PC/저널/단일 lock 이 없다. config 커밋 성공 후 satellite write
  루프 중간에서 throw(ENOSPC/EACCES/EIO) 하면 config=ref / 일부 auth-store=plaintext 로
  발산한다. catch 의 롤백은 best-effort 라(restoreFileSnapshot 이 try/catch 로 실패 삼킴)
  throw 를 유발한 동일 조건이 복원 write 도 실패시키면 발산이 영속한다.
- **동시 writer 직렬화 락 부재(FIND-002)**: apply 가 auth-profiles.json 을 read(projectPlanState
  시점)→scrub→write(커밋 시점) 하면서 정규 writer 가 쓰는 AUTH_STORE_LOCK_OPTIONS 파일락을
  잡지 않는다. read↔write 창에서 라이브 게이트웨이의 OAuth refresh 가 같은 파일을 갱신하면
  apply 의 stale 스냅샷 overwrite 로 신규 토큰이 lost-update 로 소실된다.

공통 비대칭(E 카테고리): config 경로(replaceConfigFile)는 withFileLock+baseHash CAS
(mutate.ts:475/499)로 보호되는데, 동일 논리 트랜잭션의 satellite auth-store write 만 어떤
보호도 받지 못한다 — "lock/CAS/트랜잭션 경계가 한쪽에만". R-3 grep 결과 src/secrets/
전체에 트랜잭션/락 키워드 match 0(두 FIND 공통 증거). auth-profiles.json/auth-json/.env 는
자격증명 SoT 라 재생성 불가 → 자가복구 아님.

## 관련 FIND

- FIND-secrets-apply-cross-store-consistency-001 (P1): `apply.ts:835-855`. config 커밋
  성공 후 satellite write 루프 중간 throw + best-effort 롤백 실패 시 config↔auth-store
  영구 발산. (a) config=ref / auth-store=plaintext 잔존 → 평문 자격증명 scrub 실패(유출
  표면), (b) config=원복 / auth-store=scrub 완료 → ref 미반영으로 다음 로드에서 자격증명
  해결 실패. cold 마이그레이션 명령이나 자격증명 critical 데이터라 1회 손상 영향 큼 → P1.

- FIND-secrets-apply-cross-store-consistency-002 (P1): `apply.ts:818-821`(read apply.ts:391,
  write apply.ts:844). apply 의 auth-store write 가 AUTH_STORE_LOCK 미획득이라 정규 writer
  (updateAuthProfileStoreWithLock, store.ts:705 / OAuth refresh oauth-manager.ts:437)와
  직렬화 안 됨. read↔write 창(ref resolve + preflight 로 수십~수백 ms)에서 OAuth refresh 가
  쓴 신규 토큰이 apply 의 stale 전체 스냅샷으로 덮여 소실(lost-update) → 다음 refresh 실패,
  재인증 전까지 provider 사용 불가. 게이트웨이+apply 동시 운영 시나리오라 P1.
