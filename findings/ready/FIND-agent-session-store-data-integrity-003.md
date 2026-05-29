---
id: FIND-agent-session-store-data-integrity-003
cell: agent-session-store-data-integrity
title: settings.json raw writeFileSync truncate → crash 시 전체 설정 silent reset
file: src/agents/sessions/settings-manager.ts
line_range: 206-217
evidence: "```ts\n      const current = fileExists ? readFileSync(path, \"utf-8\"\
  ) : undefined;\n      const next = fn(current);\n      if (next !== undefined) {\n\
  \        // Only create directory when we actually need to write\n        if (!existsSync(dir))\
  \ {\n          mkdirSync(dir, { recursive: true });\n        }\n        if (!release)\
  \ {\n          release = this.acquireLockSyncWithRetry(path);\n        }\n     \
  \   writeFileSync(path, next, \"utf-8\");\n      }\n```\n"
symptom_type: data-integrity-gap
problem: FileSettingsStorage.withLock 이 global/project settings.json 갱신을 raw writeFileSync
  로 in-place truncate 후 재기록한다. write 도중 SIGKILL/전원차단 시 settings.json 이 빈/부분 JSON 으로
  남고, 다음 기동에서 tryLoadFromStorage 가 parse 실패를 잡아 {} (빈 settings) 로 fallback → 사용자가
  설정한 모든 값이 조용히 기본값으로 reset 된다.
mechanism: '1. 설정 변경 → SettingsManager 가 storage.withLock(scope, fn) 호출

  2. withLock: 파일 존재 시 proper-lockfile advisory lock 획득(line 204/214)

  3. fn 이 갱신된 settings JSON 문자열을 next 로 반환

  4. writeFileSync(path, next)(line 216) 가 기존 settings.json 을 O_TRUNC 후 재기록

  5. crash 주입 시점: writeFileSync 의 truncate 후 ~ 전량 flush 전. 이 창에서 SIGKILL/패닉 시 파일은
  빈/부분 JSON

  6. 다음 기동 SettingsManager.fromStorage → tryLoadFromStorage → loadFromStorage 의 JSON.parse(부분문자열)
  throw → tryLoadFromStorage 가 catch 하여 { settings: {}, error } 반환(line 330-334)

  7. 결과: globalSettings/projectSettings 가 {} 로 떨어져 deepMergeSettings 결과도 비고, 사용자 커스텀
  설정 전부 default 로 silent reset

  advisory lock 은 동시성만 막고 단일 write 의 crash-atomicity 는 보장하지 않는다.

  '
root_cause_chain:
- why: crash 시 settings.json 이 빈/부분 상태로 남는가
  because: writeFileSync 가 대상 파일을 O_TRUNC 로 열어 in-place 재기록 → truncate 와 flush 사이
    비원자 창
  evidence_ref: src/agents/sessions/settings-manager.ts:216
- why: lock 이 있는데 왜 보호 안 되는가
  because: acquireLockSyncWithRetry 의 advisory lock 은 프로세스 간 mutual-exclusion 만 제공,
    단일 writeFileSync 의 부분쓰기 crash 를 막지 못한다
  evidence_ref: src/agents/sessions/settings-manager.ts:214
- why: 손상이 왜 silent data-loss 로 귀결되는가
  because: tryLoadFromStorage 가 parse 에러를 catch 하여 {} 로 fallback 한다 → 손상이 에러로 표면화되지
    않고 사용자 설정이 조용히 default 로 초기화
  evidence_ref: src/agents/sessions/settings-manager.ts:333
- why: 왜 atomic rename 을 안 쓰는가 (불균일)
  because: 동일 코드베이스의 transcript repair 가 in-place 재기록을 replaceFileAtomic(temp+rename)
    으로 처리하는데(session-file-repair.ts:409), 사용자 설정 writer 는 raw writeFileSync — atomic
    규율 불균일
  evidence_ref: src/agents/session-file-repair.ts:409
impact_hypothesis: data-loss
impact_detail: '정성+조건: settings.json 은 사용자가 명시 설정한 값의 영속 저장소(SoT, derived-cache 아님).
  손상 조건 = writeFileSync(216) truncate~flush 사이 SIGKILL/전원차단. 발생 시 다음 기동에서 tryLoadFromStorage
  가 parse 실패를 {} 로 흡수 → 모든 사용자 설정이 조용히 default 로 reset(에러 표면화도 안 됨). auth.json(FIND-001)과
  달리 fallback 이 있어 부팅/기능정지(lockout)는 없으나, 사용자 입장에선 설정이 알림 없이 사라지는 silent loss. write
  빈도는 설정 변경 시에만이라 auth/transcript 보다 낮음 → severity P2.

  '
severity: P2
counter_evidence:
  path: src/agents/session-file-repair.ts
  line: '409'
  reason: '반증 기준선(atomic sibling): repairSessionFile() 은 in-place 파일 재기록에 replaceFileAtomic(temp+rename)
    을 사용(session-file-repair.ts:4 import, :409 호출). 같은 트리에 atomic 헬퍼가 존재·사용되는데 settings
    writer 만 raw writeFileSync(216) 라 불균일.

    실행조건 분류:

    | 경로 | 조건 |

    | settings writeFileSync (216) | unconditional — 모든 설정 영속 write 가 이 경로 |

    | tryLoadFromStorage catch→{} (333) | recovery-only(읽기측) — 손상을 흡수하나 데이터를 복원하진
    못함 |

    | replaceFileAtomic (file-repair) | recovery-only — 다른 파일 대상 |

    derived-cache 검토: settings.json 은 사용자 명시 설정의 SoT, 재생성 불가(파일 손상 시 값 영구 소실) → 자가복구
    아님. 단 parse 실패 시 빈 {} 로 graceful 하게 기동은 가능(crash 아님).

    트랜잭션/락: advisory lock 은 동시성용이며 crash-atomicity 무관.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# settings.json raw writeFileSync truncate → crash 시 전체 설정 silent reset

## 문제
`FileSettingsStorage.withLock` 이 global/project `settings.json` 갱신을 `writeFileSync(path, next)`(line 216) 로 in-place truncate 후 재기록한다. write 도중 프로세스가 죽으면 파일이 빈/부분 JSON 으로 남고, 다음 기동의 로더가 parse 실패를 `{}` 로 흡수하여 사용자가 설정한 모든 값이 조용히 기본값으로 초기화된다.

## 발현 메커니즘
1. 설정 변경 → `SettingsManager` 가 `storage.withLock(scope, fn)` 호출.
2. `withLock` 이 파일 존재 시 proper-lockfile advisory lock 획득(line 204/214) — 동시성 보호.
3. `fn` 이 갱신된 settings JSON 문자열을 `next` 로 반환.
4. `writeFileSync(path, next)`(line 216) 가 기존 `settings.json` 을 O_TRUNC 후 재기록.
5. **crash 주입 시점**: truncate 후~전량 flush 전. SIGKILL/전원차단 시 파일은 빈/부분 JSON.
6. 다음 기동 `SettingsManager.fromStorage` → `tryLoadFromStorage` → `loadFromStorage` 의 `JSON.parse`(line 322) throw → `tryLoadFromStorage` 가 catch 하여 `{ settings: {}, error }` 반환(line 330-334).
7. `globalSettings`/`projectSettings` 가 `{}` 로 떨어지고 `deepMergeSettings` 결과도 비어 사용자 커스텀 설정 전부 default 로 silent reset.

## 근본 원인 분석
- truncate 비원자 창: `writeFileSync` 의 O_TRUNC in-place 재기록(line 216).
- lock 의 보호 범위 오인: advisory lock 은 프로세스 간 mutual-exclusion 만 제공, 단일 write 의 crash-atomicity 와 무관(line 214).
- silent data-loss 로 귀결: `tryLoadFromStorage` 가 parse 에러를 catch 하여 `{}` 로 graceful fallback(line 333) → 손상이 사용자에게 에러로 표면화되지 않고 설정이 조용히 초기화.
- 파일별 atomic 규율 불균일: 동일 코드베이스 transcript repair 는 `replaceFileAtomic`(temp+rename) 을 쓰는데(session-file-repair.ts:409) 설정 writer 는 raw write.

## 영향
impact: data-loss. `settings.json` 은 사용자가 명시 설정한 값의 유일한 영속 저장소다. truncate-rewrite 도중 crash 시 다음 기동에서 모든 설정이 알림 없이 default 로 reset 된다. `auth.json`(FIND-001) 과 달리 로더에 `{}` fallback 이 있어 부팅 실패/lockout 은 없으나, 사용자 입장에선 설정이 사라진다. 설정 변경 시에만 write 가 일어나 노출 빈도는 auth refresh/transcript migrate 보다 낮으므로 P2.

재현 시나리오: (1) 커스텀 settings.json 준비, (2) 설정 변경 트리거로 `withLock` 의 `writeFileSync`(216) 진입, (3) 그 시점에 프로세스 SIGKILL, (4) 파일이 빈/부분 상태인지 확인, (5) 재기동 시 `tryLoadFromStorage` 가 `{}` 로 fallback 하여 설정이 사라지는지 확인.

## 반증 탐색
- atomic sibling 존재(불균일 입증): `session-file-repair.ts:409` 가 in-place 재기록에 `replaceFileAtomic`(temp+rename) 사용. 같은 트리에 atomic 헬퍼가 있고 쓰이는데 설정 writer 만 raw write.
- derived-cache 여부: settings.json 은 사용자 명시 설정의 SoT, 재생성 불가 → 자가복구 아님. 단 parse 실패 시 graceful 빈 기동은 가능(crash 는 아니므로 impact 는 data-loss 로 한정, crash 로 분류 안 함).
- 호출 빈도: 설정 변경 시에만 write → 상대적으로 드문 경로(P2 근거).
- 트랜잭션/락: advisory lock 은 동시성용이며 crash-atomicity 무관.
- 기존 테스트: settings-manager 의 동시성/마이그레이션 테스트는 있으나 crash-during-write 재현은 확인되지 않음.

## Self-check
### 내가 확실한 근거
- line 216 이 raw `writeFileSync` in-place truncate 이고 atomic rename 없음 — 직접 Read 확인.
- `tryLoadFromStorage` 가 parse 에러를 catch 하여 `{}` 로 fallback — line 330-334 직접 확인.
- `replaceFileAtomic` 이 같은 코드베이스에 존재·사용 — session-file-repair.ts:409 직접 확인.

### 내가 한 가정
- `writeFileSync` 가 O_TRUNC open+write 로 분해되어 truncate 직후 crash 만으로 빈 파일이 된다는 표준 동작.
- 사용자가 비기본 설정을 가진다는 점(설정 reset 의 영향이 의미를 가지려면). 기본만 쓰는 사용자에겐 영향 미미하나 손상 메커니즘 자체는 불변.

### 확인 안 한 것 중 영향 가능성
- global 과 project settings 가 별도 파일이고 한쪽만 손상될 수 있는데, deepMerge 시 한쪽만 비면 부분 reset 이 됨(영향 범위는 손상된 scope 에 한정).
- 손상된 settings.json 이 이후 정상 write 로 자동 복구되는지(다음 설정 변경 시 withLock 이 truncated current 를 읽어 fn 에 넘기므로, fn 이 빈 current 를 base 로 새로 쓰면 복구되나 그 전까지 설정은 소실 상태).
