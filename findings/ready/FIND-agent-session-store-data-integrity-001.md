---
id: FIND-agent-session-store-data-integrity-001
cell: agent-session-store-data-integrity
title: auth.json 자격증명을 lock 하에 raw writeFileSync 로 truncate-rewrite (atomic rename
  부재)
file: src/agents/sessions/auth-storage.ts
line_range: 114-122
evidence: "```ts\n    try {\n      release = this.acquireLockSyncWithRetry(this.authPath);\n\
  \      const current = existsSync(this.authPath) ? readFileSync(this.authPath, \"\
  utf-8\") : undefined;\n      const { result, next } = fn(current);\n      if (next\
  \ !== undefined) {\n        writeFileSync(this.authPath, next, \"utf-8\");\n   \
  \     chmodSync(this.authPath, 0o600);\n      }\n      return result;\n```\n"
symptom_type: data-integrity-gap
problem: auth.json (API 키 / OAuth refresh 토큰 저장소) 갱신이 temp+rename atomic 이 아니라 기존
  파일을 직접 truncate 후 재기록한다. writeFileSync 진행 중(또는 직후 chmodSync 사이) SIGKILL/전원차단 시 auth.json
  이 0바이트 또는 부분 JSON 으로 남아, 다음 기동의 JSON.parse 가 throw → 모든 provider 자격증명이 로드 실패한다.
mechanism: '1. set()/remove()/login() → persistProviderChange() → storage.withLock(fn)

  2. withLock: proper-lockfile 로 advisory lock 획득 (다중 프로세스 동시 refresh 보호)

  3. fn 이 merged JSON 문자열을 next 로 반환

  4. writeFileSync(authPath, next) 가 **기존 파일을 O_TRUNC 로 열어** 내용을 0 으로 만든 뒤 새 바이트를
  기록 (line 119)

  5. crash 주입 시점: writeFileSync 의 truncate 후 ~ 전체 바이트 flush 완료 전, 또는 write 완료 후 chmodSync(120)
  사이. 이 창에서 SIGKILL/패닉 시 파일은 빈/부분 상태로 디스크에 잔존

  6. 다음 프로세스 reload() → parseStorageData(content) → JSON.parse(부분문자열) throw → loadError
  세팅, this.data = {} 로 빈 상태

  7. persistProviderChange 는 loadError 시 early-return (line 287) 하므로 손상된 파일을 덮어쓰지도
  못함 → 사용자가 /login 재인증하기 전까지 silent credential lockout

  advisory lock 은 동시성(다중 프로세스 lost-update)만 막고 단일 write 의 crash-atomicity 는 전혀 보장하지
  않는다.

  '
root_cause_chain:
- why: crash 시 auth.json 이 빈/부분 상태로 남는가
  because: writeFileSync 가 대상 경로를 직접 O_TRUNC 로 열어 in-place 재기록 → truncate 와 flush
    사이에 비원자 창 존재
  evidence_ref: src/agents/sessions/auth-storage.ts:119
- why: lock 이 있는데 왜 보호되지 않는가
  because: proper-lockfile advisory lock 은 프로세스 간 mutual-exclusion 만 제공하며 단일 writeFileSync
    의 부분쓰기 crash 를 막지 못한다 (lock 보유 중 crash 하면 파일은 이미 truncated)
  evidence_ref: src/agents/sessions/auth-storage.ts:115
- why: 왜 temp+rename 패턴을 안 쓰는가 (불균일)
  because: 동일 코드베이스의 세션 transcript repair 경로는 동일한 in-place 재기록을 replaceFileAtomic(temp
    파일 작성 후 atomic rename) 으로 처리하는데, 자격증명이라는 더 critical 한 데이터의 writer 는 raw writeFileSync
    를 쓴다 — atomic 규율이 파일별로 불균일
  evidence_ref: src/agents/session-file-repair.ts:409
- why: 손상 후 자가복구가 안 되는가
  because: reload() 가 parse 실패를 loadError 로 잡고, persistProviderChange() 는 loadError
    시 early-return 하여 손상 파일을 새 값으로 덮어쓰지 못한다. 자격증명은 파일이 유일한 SoT 라 재생성 불가
  evidence_ref: src/agents/sessions/auth-storage.ts:287
impact_hypothesis: data-loss
impact_detail: '정성+조건: auth.json 은 API 키와 OAuth refresh 토큰의 유일한 영속 저장소(SoT, derived-cache
  아님). 손상 조건 = writeFileSync(119) truncate~flush 사이 또는 write~chmod(120) 사이 프로세스 SIGKILL/전원차단.
  발생 시 다음 기동에서 전 provider 자격증명 로드 실패 → 사용자가 /login 으로 OAuth 재인증하거나 키를 다시 입력하기 전까지
  모든 LLM 호출 불가(부팅 후 사실상 사용 불가). write 빈도: OAuth 토큰은 만료 시마다 refreshOAuthTokenWithLock
  가 writeFileSync(164) 로 갱신하므로 장기 세션에서 반복 발생(드물지 않음). 동일 truncate 비원자 패턴이 withLock(sync,
  line 119) 과 withLockAsync(async, line 164) 양쪽에 존재.

  '
severity: P1
counter_evidence:
  path: src/agents/session-file-repair.ts
  line: '409'
  reason: '반증 기준선(atomic sibling): repairSessionFile() 은 세션 transcript(.jsonl) 를 in-place
    재기록할 때 replaceFileAtomic({ filePath, content, preserveExistingMode, tempPrefix
    }) 을 호출해 temp 파일 작성 후 atomic rename 한다(session-file-repair.ts:4 에서 ../infra/replace-file.js
    import). 같은 코드베이스가 atomic 헬퍼를 보유·사용하는데 auth-storage 의 자격증명 writer 만 raw writeFileSync(119,164)
    라 "왜 여기만 raw?" 불균일이 명확.

    실행조건 분류:

    | 경로 | 조건 |

    | auth.json writeFileSync (119/164) | unconditional — set/remove/login/refresh
    의 모든 영속 write 가 이 경로 |

    | replaceFileAtomic (file-repair) | conditional-edge — transcript 손상 복구 시에만, 다른
    파일 대상 |

    derived-cache 검토: auth.json 은 env var/CLI override 와 별개로 OAuth 토큰의 유일 영속처라 재생성
    불가 → 자가복구 아님(결함 성립).

    외부패키지 경계: replaceFileAtomic 자체는 @openclaw/fs-safe 이나, 이를 소비하지 않고 raw write 를 택한
    것은 in-tree 결정이므로 in-tree 수정 가능.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# auth.json 자격증명을 lock 하에 raw writeFileSync 로 truncate-rewrite (atomic rename 부재)

## 문제
`FileAuthStorageBackend.withLock`(sync) 과 `withLockAsync`(async) 이 auth.json 갱신을 `writeFileSync(this.authPath, next)` 로 처리한다. 이는 기존 파일을 `O_TRUNC` 로 열어 내용을 비운 뒤 새 바이트를 다시 쓰는 in-place 재기록이라, write 도중 프로세스가 죽으면 auth.json 이 빈 파일 또는 잘린 JSON 으로 남는다. auth.json 은 API 키와 OAuth refresh 토큰의 유일한 영속 저장소다.

## 발현 메커니즘
1. `set()` / `remove()` / `login()` → `persistProviderChange()` → `storage.withLock(fn)`.
2. `withLock` 이 proper-lockfile 로 advisory lock 을 잡는다(line 115). 이건 다중 프로세스 동시 refresh 의 lost-update 만 막는다.
3. `fn` 이 병합된 JSON 문자열을 `next` 로 반환.
4. `writeFileSync(this.authPath, next)`(line 119) 가 대상 파일을 truncate 후 재기록.
5. **crash 주입 시점**: truncate 직후~전체 flush 전, 혹은 write 완료~`chmodSync`(line 120) 사이. 이 창에서 SIGKILL/전원차단 시 파일은 0바이트 또는 부분 JSON.
6. 다음 기동 `reload()` → `parseStorageData(content)` → `JSON.parse` throw → `loadError` 세팅, `this.data = {}`.
7. `persistProviderChange()` 는 `if (this.loadError) return;`(line 287) 으로 손상 파일을 덮어쓰지 못한다 → 사용자가 명시적으로 재인증하기 전까지 자격증명 lockout.

async 경로(`withLockAsync`)도 동일하게 line 164 의 `writeFileSync` 로 OAuth 토큰 refresh 를 영속화하므로 같은 비원자 창을 가진다.

## 근본 원인 분석
- truncate 비원자 창: `writeFileSync` 가 in-place O_TRUNC 재기록이라 truncate 와 flush 사이가 비원자(line 119).
- lock 의 보호 범위 오인: advisory lock 은 프로세스 간 mutual-exclusion 만 제공, 단일 write 의 crash-atomicity 와는 무관(line 115). lock 보유 중 crash 하면 파일은 이미 truncated.
- 파일별 atomic 규율 불균일: 동일 코드베이스의 transcript repair 경로는 `replaceFileAtomic`(temp+rename) 으로 같은 in-place 재기록 문제를 해결했는데(session-file-repair.ts:409), 더 critical 한 자격증명 writer 는 raw write 를 쓴다.
- 자가복구 부재: parse 실패를 `loadError` 로 잡지만 손상 파일을 덮어쓰는 경로가 차단됨(line 287). 자격증명은 SoT 라 재생성 불가.

## 영향
impact: data-loss. auth.json 손상 시 다음 기동에서 모든 provider 자격증명 로드가 실패하고, 코드가 손상 파일을 자동 복구·덮어쓰지 못해 사용자가 `/login` 재인증 또는 키 재입력을 하기 전까지 LLM 호출이 전면 불가하다. OAuth refresh 가 만료 주기마다 line 164 의 동일 비원자 write 를 수행하므로 손상 노출이 드물지 않다.

재현 시나리오: (1) OAuth provider 로 로그인한 상태에서 (2) 장기 세션 중 토큰 만료 → `refreshOAuthTokenWithLock` 이 `writeFileSync`(164) 실행, (3) 그 write 진행 중 프로세스를 SIGKILL, (4) 재기동 시 `reload()` 의 `JSON.parse` 가 throw → 빈 data + lockout 확인.

## 반증 탐색
- atomic sibling 존재(불균일 입증): `session-file-repair.ts:409` 의 `repairSessionFile()` 이 동일 세션 파일 재기록에 `replaceFileAtomic`(temp+rename) 을 사용. 같은 트리 안에 atomic 헬퍼가 있고 실제로 쓰이는데 자격증명 writer 만 raw write → 의도적 불균일이 아니라 누락으로 판단.
- derived-cache 여부: auth.json 은 OAuth refresh 토큰의 유일 영속처. env var/CLI `--api-key` 는 별개 source 이고 OAuth 토큰을 대체하지 못함 → 재생성 불가, 자가복구 아님.
- 외부 패키지 경계: `replaceFileAtomic` 구현은 `@openclaw/fs-safe/atomic` 이지만, in-tree `src/infra/replace-file.js` 가 이를 재노출하고 file-repair 가 소비 중이다. auth-storage 가 이를 소비하지 않은 것은 in-tree 결정이므로 결함 지목 대상은 in-tree.
- 기존 테스트: auth-storage 에 crash-during-write 재현 테스트는 확인되지 않음(lock 동시성 테스트 위주).

## Self-check
### 내가 확실한 근거
- line 119/164 가 raw `writeFileSync`(in-place truncate) 이고 atomic rename 이 없음 (직접 Read 확인).
- `replaceFileAtomic` 이 같은 코드베이스에 존재하고 transcript repair 가 사용 (session-file-repair.ts:4,409).
- parse 실패 시 `persistProviderChange` 가 early-return 하여 손상 파일을 덮어쓰지 못함 (line 287).

### 내가 한 가정
- `writeFileSync` 가 OS 레벨에서 단일 atomic syscall 이 아니라 O_TRUNC open + write 로 분해된다는 점(POSIX/Node 표준 동작). 작은 JSON 이라 보통 한 번의 write 로 끝나지만, O_TRUNC 시점에 이미 기존 내용이 사라지므로 truncate 직후 crash 만으로도 빈 파일이 된다.
- OAuth refresh 가 production 에서 실제로 주기적으로 일어난다(만료 기반)는 점.

### 확인 안 한 것 중 영향 가능성
- proper-lockfile 이 남긴 stale lock 디렉터리가 crash 후 다음 기동의 lock 획득을 방해하는지(별개 lifecycle 이슈, 본 FIND 범위 밖).
- 파일시스템별(ext4 data=ordered vs APFS) truncate 가시성 차이 — 비원자 창의 폭에 영향은 주지만 존재 자체는 불변.
