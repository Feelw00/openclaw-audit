# domain-notes: secrets-apply

도메인: secrets-apply (cross-store-consistency 축)
최초 감사: 2026-05-29 (data-integrity-auditor)
대상 upstream: openclaw @ 61c538e2fc22d01b8e08549b07a9e530e6eee35c
셀: secrets-apply-cross-store-consistency

## 파일 인벤토리 (allowed_paths)

| 파일 | 역할 | 영속 대상 |
|---|---|---|
| `src/secrets/apply.ts` | secrets plan 의 dry-run/write 실행. plaintext 자격증명을 SecretRef 로 일괄 마이그레이션 + scrub | config.json + auth-profiles.json(N) + legacy auth-json + .env |
| `src/secrets/shared.ts` | `writeTextFileAtomic`(0o600=privateFileStore, 기타 mode=replaceFileAtomicSync) 등 단일파일 헬퍼 | - (헬퍼) |
| `src/secrets/auth-store-paths.ts` | `listAuthProfileStorePaths` — stateDir 하위 에이전트별 auth-profiles.json 경로 열거 (read-only 스캔, fs write 없음) | - |
| `src/secrets/configure.ts` | 대화형 secrets configure. plan 빌드 후 `runSecretsApply(write:false)` preflight 만 호출 (직접 fs write 없음) | - |

## 커밋/롤백 블록 분석 (apply.ts:835-855)

write 모드 커밋은 한 try 블록에서:
1. `replaceConfigFile(...)` — config.json 1회 커밋. **내부에서 `withConfigMutationLock`→`withFileLock(configPath)` + `assertBaseHashMatches`(baseHash CAS) 보호**(mutate.ts:475/499). 단 함수 반환 시 lock 해제.
2. `for (write of writes) writeTextFileAtomic(...)` — auth-profiles.json(N) + legacy auth-json + .env 순차 커밋. **lock/CAS/트랜잭션 경계 전혀 없음**(apply.ts:843-845).
3. catch: snapshots 순회 `restoreFileSnapshot` 으로 best-effort 복원. 각 복원이 try{}catch{} 로 실패를 삼킴(apply.ts:850-852, 주석 "Best effort only").

스냅샷은 커밋 직전 `captureFileSnapshot`(existed/content/mode)로 전 대상 파일에서 채취(apply.ts:816-832). 복원은 `writeTextFileAtomic`(존재했던 파일) 또는 `fs.rmSync`(없던 파일).

## write 경로별 atomic / 락 여부 표

| writer | 파일 | 단일파일 atomic? | 파일간 경계? | 락/CAS? | 비고 |
|---|---|---|---|---|---|
| `apply.ts:836` `replaceConfigFile` | config.json | YES (replaceFileAtomic) | NO | **YES** (withFileLock+baseHash CAS, mutate.ts:475/499) | 한쪽만 보호 |
| `apply.ts:844` `writeTextFileAtomic` (auth-store) | auth-profiles.json | YES (privateFileStore temp+rename) | NO | **NO** (AUTH_STORE_LOCK 미획득) | FIND-001, FIND-002 |
| `apply.ts:844` `writeTextFileAtomic` (auth-json) | legacy auth-json | YES | NO | NO | FIND-001 |
| `apply.ts:844` `writeTextFileAtomic` (.env) | .env | YES (mode≠0o600→replaceFileAtomicSync) | NO | NO | FIND-001 |
| `apply.ts:746` `restoreFileSnapshot` | (전 대상) | YES | NO | NO | recovery-only, best-effort |

반증 기준선 (락 쓰는 정규 auth-store writer):
- `src/agents/auth-profiles/store.ts:705` `updateAuthProfileStoreWithLock` → `withFileLock(authPath, AUTH_STORE_LOCK_OPTIONS)` 안에서 reload→update→save. 주석(706-708): "Locked writers must reload from disk... Otherwise a live gateway can overwrite fresher CLI/config-auth writes with stale in-memory auth state." → 이 락의 목적이 정확히 lost-update 방지.
- `src/agents/auth-profiles/oauth-manager.ts:437` OAuth refresh 도 동일 락 사용.

핵심 헬퍼:
- `writeTextFileAtomic`(shared.ts:57): 단일파일 원자성만. 0o600 은 `privateFileStoreSync.writeText`, 그 외 mode 는 `replaceFileAtomicSync`(@openclaw/fs-safe). **파일 간 경계는 책임 범위 밖.**
- `replaceConfigFile`(config/mutate.ts:467): config 파일 전용 lock+CAS.

## R-3 트랜잭션/락 경계 Grep 결과 (강제)

```
rg -n "withWriteTransaction|BEGIN IMMEDIATE|BEGIN|withFileLock|baseHash|CAS|assertBaseHashMatch|withLock|flock|lockfile|2PC|journal" src/secrets/
→ match 없음 (EXIT 1)
```

즉 src/secrets/ 디렉터리 자체에는 파일 간 트랜잭션/락/CAS/저널이 전무. config 보호는 `src/config/`, auth-store 보호는 `src/agents/auth-profiles/` 에 있고 apply 의 커밋 루프는 그 어느 쪽도 경유하지 않음.

## 다중스토어 일관성 결론

secrets apply 는 **단일 논리 트랜잭션(plaintext→ref 마이그레이션 + scrub)에서 4종 스토어(config / auth-profiles.json×N / legacy auth-json / .env)를 비원자로 순차 커밋**한다. 각 파일은 개별 atomic 이나:
1. 파일 간 경계 없음 → config 커밋 후 satellite throw 시 부분 마이그레이션 (FIND-001).
2. config 만 lock+CAS, satellite 는 무락 → "lock/CAS 한쪽에만" 비대칭.
3. auth-profiles.json 은 정규 writer 가 쓰는 AUTH_STORE_LOCK 을 apply 만 우회 → 동시 OAuth refresh 와 lost-update (FIND-002).
4. 롤백 best-effort → throw 유발 조건(디스크/권한)이 복원도 실패시키면 발산 영속.

auth-profiles.json / auth-json / .env 는 자격증명 영속 SoT(derived-cache 아님) → 손상 시 재인증 외 복구 불가.

## 발견 요약

| FIND | 대상 | symptom | severity | 한 줄 |
|---|---|---|---|---|
| FIND-secrets-apply-cross-store-consistency-001 | apply.ts:835-855 | cross-store-gap | P1 | config(lock+CAS) 커밋 후 satellite store 무경계 순차 커밋, 중간 throw + best-effort 롤백 실패 시 부분 마이그레이션 영속 |
| FIND-secrets-apply-cross-store-consistency-002 | apply.ts:818-821 | cross-store-gap | P1 | auth-profiles.json 을 정규 AUTH_STORE_LOCK 없이 read-modify-write 커밋 → 동시 OAuth refresh 신토큰 lost-update |

두 FIND 는 cross_refs 로 상호 연결(둘 다 동일 커밋 블록의 무경계 satellite write 가 근원, 한쪽은 crash/IO-fault 타이밍, 한쪽은 동시성 타이밍).

## 탐지 카테고리 적용 (체크리스트)

- [x] A. 비원자 단일파일 쓰기 — skipped: 모든 write 가 `writeTextFileAtomic`(temp+rename) 경유. 단일파일 원자성은 충족 (결함 아님).
- [x] B. 다중 syscall 비원자 (write+chmod) — skipped: mode 가 writeTextFileAtomic 인자로 atomic 처리됨, 분리 chmod 없음.
- [x] C. 부분 multi-write 불일치 (void vs await) — skipped: 커밋 루프는 전부 동기 writeTextFileAtomic, fire-and-forget void write 없음.
- [x] D. 복구/롤백 비원자 — 부분 적용: 롤백 자체는 writeTextFileAtomic(원자)이나 best-effort 로 실패를 삼킴 → FIND-001 의 일부로 흡수(단독 FIND 아님, 복원 write 자체는 원자라 D 단독 결함 미성립).
- [x] E. 교차 스토어 경계 부재 — **적용, 발견 2 (FIND-001, FIND-002)**.

## 추가 관찰 (FIND 미승격)

- `configure.ts` 는 plan 빌드 + dry-run preflight 만 하고 직접 fs write 없음 → write 경로는 전적으로 apply.ts. 결함 없음.
- `auth-store-paths.ts` 는 read-only 스캔(readdirSync). 결함 없음.
- dry-run 모드(write:false)는 커밋 블록을 타지 않음(apply.ts:777-790) → 부분커밋/lost-update 위험은 write 모드 한정.
- 보안 민감 경로 주의: `/src/agents/*auth*` CODEOWNERS 제약 인접. apply.ts 자체는 src/secrets/ 라 직접 제약은 아니나, SOL 단계에서 auth-store 락 도입 시 auth-profiles 모듈 표면을 건드릴 수 있어 PR 비용 큼. FIND 산출에는 영향 없음(읽기 전용 감사).

## clusterer (2026-05-29)

- CAND-048 (epic, P1): FIND-...-001(P1) + -002(P1) 묶음 (frontmatter 상호 cross_refs, 의도 존중).
  공통 원인 "secrets/apply.ts 의 config↔auth-store 다중스토어 커밋(write 모드)이 같은 store 집합에
  대해 파일 간 트랜잭션 경계와 AUTH_STORE_LOCK 획득을 모두 결여". FIND-001 root_cause_chain[0]
  (apply.ts:843 파일 간 2PC/저널/단일 lock 부재) + [2](apply.ts:850 best-effort 롤백),
  FIND-002 root_cause_chain[0](apply.ts:818 AUTH_STORE_LOCK 미획득) + [1](store.ts:705 정규
  writer 의 락 규약 존재)가 동일 stem 에서 갈라짐. 공통 비대칭: config 경로는 withFileLock+CAS
  보호(mutate.ts:475/499)인데 satellite auth-store write 만 무보호(lock/경계 한쪽에만, E 카테고리).
  R-3 grep 결과 src/secrets/ 전체 트랜잭션/락 match 0 (두 FIND 공통 증거) → epic. severity 둘 다 P1.
- fix 표면 분리 가능성: 파일 간 트랜잭션 경계 도입(FIND-001) vs AUTH_STORE_LOCK 획득(FIND-002)이
  구별되므로 gatekeeper/SOL 단계에서 자식 task 로 나뉠 수 있음(epic 본문에 명시).
- 도메인 분리 규율 적용: 타 도메인과 "교차-스토어 경계 부재" root cause 유사하나 별도 PR scope 이라
  병합 금지, secrets-apply 단독 CAND.
