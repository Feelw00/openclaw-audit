---
id: FIND-agent-session-store-data-integrity-002
cell: agent-session-store-data-integrity
title: rewriteFile() 세션 transcript 전체를 raw writeFileSync 로 truncate (atomic 미사용)
file: src/agents/sessions/session-manager.ts
line_range: 800-805
evidence: "```ts\n  private rewriteFile(): void {\n    if (!this.shouldPersist ||\
  \ !this.sessionFile) {\n      return;\n    }\n    writeJsonlEntriesSync(this.sessionFile,\
  \ this.fileEntries);\n  }\n```\n"
symptom_type: data-integrity-gap
problem: 'rewriteFile() 가 기존 세션 transcript(.jsonl, 사용자 대화 전체 기록) 를 writeJsonlEntriesSync
  → raw writeFileSync 로 in-place truncate 후 전량 재기록한다. 가장 위험한 호출 지점은 resume 시 버전 마이그레이션
  경로(line 741): 이미 valid 했던 전체 transcript 를 truncate 하는 도중 SIGKILL/전원차단 시 파일이 빈/부분
  JSONL 로 남아 사용자의 전체 대화 기록이 손상된다.'
mechanism: '1. 사용자가 구버전(version < 3) 세션을 resume → setSessionFile(file) (line 721)

  2. loadEntriesFromFile 로 기존 valid transcript 전체를 메모리(fileEntries)에 로드 (line 724)

  3. migrateToCurrentVersion(this.fileEntries) 가 헤더 version<CURRENT 면 true 반환 (line
  740, 274-285)

  4. rewriteFile() 호출 (line 741) → writeJsonlEntriesSync(sessionFile, fileEntries)
  (line 804)

  5. writeJsonlEntriesSync 가 writeFileSync(filePath, serializeJsonlEntries(entries))
  로 **기존 파일을 O_TRUNC 후 전체 재기록** (transcript-jsonl.ts:26-27)

  6. crash 주입 시점: writeFileSync 의 truncate 후 ~ N 개 엔트리 전량 flush 완료 전. 이 창에서 SIGKILL/패닉
  시 디스크의 .jsonl 은 빈 파일 또는 일부 줄만 남은 부분 JSONL

  7. 다음 resume 시 loadEntriesFromFile 가 부분/빈 파일 파싱 → header 없거나 entries 비면 newSession()
  으로 fresh 시작(line 728-735) → 기존 대화 기록 영구 소실

  마이그레이션 전 파일은 이미 정상이었으므로(이번 write 가 손상을 새로 만든다) data-integrity 결함.

  '
root_cause_chain:
- why: crash 시 transcript 가 빈/부분 상태로 남는가
  because: rewriteFile → writeJsonlEntriesSync → writeFileSync 가 대상 .jsonl 을 O_TRUNC
    로 열어 전량 in-place 재기록하므로 truncate 와 마지막 엔트리 flush 사이가 비원자
  evidence_ref: src/config/sessions/transcript-jsonl.ts:27
- why: 왜 마이그레이션 경로가 위험한가
  because: setSessionFile 의 migrate 분기(line 740-742)는 이미 valid 한 기존 transcript 전체를
    truncate-rewrite 한다 — append 가 아닌 full overwrite 라 손상 시 일부가 아닌 전체가 위험
  evidence_ref: src/agents/sessions/session-manager.ts:741
- why: 왜 atomic rename 을 안 쓰는가 (불균일)
  because: 동일 세션 .jsonl 을 in-place 전량 재기록하는 또 다른 경로인 repairSessionFile() 은 replaceFileAtomic(temp
    작성 후 atomic rename + backup) 으로 처리한다. 같은 파일종류의 재기록인데 rewriteFile 만 raw writeFileSync
  evidence_ref: src/agents/session-file-repair.ts:409
- why: 손상 후 어떻게 데이터가 소실되는가
  because: 다음 resume 의 setSessionFile 가 빈/header-없는 파일을 감지하면 newSession() 으로 fresh
    시작하여 기존 entries 를 버린다(self-heal 이 아니라 데이터 폐기)
  evidence_ref: src/agents/sessions/session-manager.ts:728
impact_hypothesis: data-loss
impact_detail: '정성+조건: 세션 .jsonl 은 해당 대화의 유일한 영속 기록(SoT, derived-cache 아님). 손상 조건
  = rewriteFile 의 writeFileSync(transcript-jsonl.ts:27) truncate~전량 flush 사이 SIGKILL/전원차단.
  트리거 빈도: (a) line 741 — 구버전(v1/v2) 세션 resume 시마다(스키마 업그레이드 후 기존 세션 첫 재개에서 광범위 발생),
  (b) line 732 — 빈/손상 파일 fresh 재기록, (c) line 1271 — branch 생성(새 파일 대상이라 기존 파일 손상 위험
  낮음). 큰 transcript 일수록 write 시간이 길어 crash 창이 넓다. 손상 시 다음 resume 에서 fresh 시작되어 전체
  대화 기록 영구 소실.

  '
severity: P1
counter_evidence:
  path: src/agents/session-file-repair.ts
  line: '409'
  reason: '반증 기준선(atomic sibling): repairSessionFile() 은 동일한 세션 transcript(.jsonl)
    를 in-place 전량 재기록할 때 replaceFileAtomic({ filePath, content, preserveExistingMode,
    tempPrefix }) + 사전 backup 작성(line 405) 으로 atomic 하게 교체한다. 같은 파일종류의 같은 종류 작업(full
    rewrite)인데 rewriteFile() 만 raw writeFileSync(transcript-jsonl.ts:27) 라 불균일이 명확.

    실행조건 분류:

    | 경로 | 조건 |

    | rewriteFile via migrate (741) | conditional-edge → 단 구버전 세션 resume 시 광범위 (스키마
    bump 후 흔함) |

    | rewriteFile empty-file (732) | recovery-only — 이미 빈/손상 파일 대상이라 추가 손실 작음 |

    | rewriteFile branch (1271) | conditional-edge — newSessionFile 대상이라 기존 파일 보존
    |

    | repairSessionFile replaceFileAtomic | recovery-only — 손상 복구 시 atomic |

    derived-cache 검토: transcript 는 해당 대화의 유일 기록, 재생성 불가 → 자가복구 아님(손상 시 폐기됨).

    트랜잭션/락: rewriteFile 에는 파일락도 없음(auth/settings 와 달리). append 경로(persist)는 appendFileSync
    라 별개.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# rewriteFile() 세션 transcript 전체를 raw writeFileSync 로 truncate (atomic 미사용)

## 문제
`SessionManager.rewriteFile()` 은 메모리상의 `fileEntries`(세션 전체 엔트리) 를 `writeJsonlEntriesSync(this.sessionFile, this.fileEntries)` 로 디스크에 다시 쓴다. 이 헬퍼는 `writeFileSync` 로 대상 `.jsonl` 을 `O_TRUNC` 후 전량 재기록한다. 가장 위험한 호출 지점은 resume 시 버전 마이그레이션(line 741): 이미 정상이던 사용자 대화 기록 전체를 truncate 하는 도중 crash 하면 파일이 빈/부분 JSONL 로 남는다.

## 발현 메커니즘
1. 사용자가 구버전(`version < CURRENT_SESSION_VERSION`, 현재 3) 세션을 resume → `setSessionFile(file)`(line 721).
2. `loadEntriesFromFile` 로 기존 valid transcript 전체를 `fileEntries` 로 로드(line 724).
3. `migrateToCurrentVersion(this.fileEntries)` 가 헤더 version 이 낮으면 entries 를 변형하고 `true` 반환(line 740; 함수 270-286).
4. `if (migrateToCurrentVersion(...)) this.rewriteFile();`(line 741) → `writeJsonlEntriesSync(sessionFile, fileEntries)`(line 804).
5. `writeJsonlEntriesSync` 가 `writeFileSync(filePath, serializeJsonlEntries(entries))`(transcript-jsonl.ts:26-27) 로 파일을 truncate 후 전체 재기록.
6. **crash 주입 시점**: truncate 후~마지막 엔트리 flush 전. 이 창에서 SIGKILL/전원차단 시 디스크의 `.jsonl` 은 빈 파일 또는 일부 줄만.
7. 다음 resume 의 `setSessionFile` 가 entries 0 또는 header 부재를 감지 → `newSession()` 으로 fresh 시작(line 728-735) → 기존 대화 영구 소실.

## 근본 원인 분석
- truncate 비원자 창: `writeFileSync` 의 O_TRUNC in-place 재기록(transcript-jsonl.ts:27). 전체 transcript 가 한 번에 truncate 되므로 손상 단위가 "전부".
- 마이그레이션 경로가 valid 데이터를 위험에 노출: line 741 은 이미 정상이던 파일을 full overwrite 한다(append 아님). crash 시 새 손상이 생성됨.
- 파일별 atomic 규율 불균일: 동일 `.jsonl` full rewrite 인 `repairSessionFile()` 은 `replaceFileAtomic`+backup(session-file-repair.ts:405,409) 을 쓰는데 `rewriteFile()` 은 raw write.
- 손상이 데이터 폐기로 귀결: 다음 resume 이 부분/빈 파일을 자가복구가 아니라 newSession 으로 폐기(line 728).

## 영향
impact: data-loss. 세션 `.jsonl` 은 해당 대화의 유일 영속 기록이다. 마이그레이션 truncate-rewrite 도중 crash 시 다음 resume 에서 fresh 세션으로 시작되어 전체 대화 기록이 사라진다. 특히 스키마 버전 bump 직후 사용자가 기존 세션들을 처음 재개할 때 line 741 경로가 광범위하게 실행되므로 노출면이 넓다. transcript 가 클수록 write 시간이 길어 crash 창이 커진다.

재현 시나리오: (1) `version: 2` 헤더를 가진 `.jsonl` 세션 파일을 준비, (2) 이를 resume → `setSessionFile` 가 migrate 분기 진입, (3) `writeJsonlEntriesSync` 의 `writeFileSync` 호출 시점에 프로세스 SIGKILL, (4) 파일이 빈/부분 상태인지 확인, (5) 재 resume 시 newSession 으로 빠져 기존 entries 가 사라지는지 확인.

## 반증 탐색
- atomic sibling 존재(불균일 입증): `session-file-repair.ts:409` 의 `repairSessionFile()` 이 동일 `.jsonl` 전량 재기록을 `replaceFileAtomic`(temp+rename) 으로 하고 line 405 에서 backup 까지 만든다. 같은 종류 작업인데 rewriteFile 만 raw write.
- derived-cache 여부: transcript 는 대화 내용 그 자체로 재생성 불가 → 자가복구 아님(손상 시 폐기됨, line 728).
- 호출 빈도/경로 활성: line 741(migrate) 은 구버전 세션 resume 마다, line 1271(branch) 은 새 파일 대상이라 위험 낮음. 즉 위험 핵심은 migrate.
- 트랜잭션/락: rewriteFile 자체에 파일락 없음. append 경로 `persist()` 는 `appendFileSync`(별개, truncate 아님).
- 기존 테스트: session-file-repair.test.ts 는 repair 의 atomic 경로를 다루나 rewriteFile 의 crash-during-migrate 재현은 확인되지 않음.

## Self-check
### 내가 확실한 근거
- `rewriteFile()` 가 `writeJsonlEntriesSync` 를 호출하고(line 804), 그 헬퍼가 raw `writeFileSync` in-place 재기록임(transcript-jsonl.ts:26-27) — 직접 Read 확인.
- migrate 분기(line 740-741)가 이미 로드된 valid 전체 entries 를 truncate-rewrite — 직접 Read 확인.
- repairSessionFile 이 같은 `.jsonl` 을 replaceFileAtomic 으로 처리 — session-file-repair.ts:409 직접 확인.
- 손상 후 다음 resume 이 newSession 으로 폐기 — line 728-735 직접 확인.

### 내가 한 가정
- 구버전 세션 파일이 실제 사용자 환경에 존재하고 resume 될 수 있다는 점(CURRENT_SESSION_VERSION=3 이고 migrateV1ToV2/V2ToV3 가 존재 → 과거 버전 세션 호환 의도가 명백하므로 합리적 가정).
- `writeFileSync` 가 O_TRUNC open+write 로 분해된다는 표준 동작.

### 확인 안 한 것 중 영향 가능성
- loadEntriesFromFile 가 부분 JSONL 의 앞부분 valid 줄들을 일부 보존하는지(부분 복구 가능성). 보존하더라도 마이그레이션 결과가 일관되지 않게 잘릴 수 있어 손상 성격은 유지.
- 동일 세션을 두 프로세스가 동시에 열 때의 상호작용(rewriteFile 에 락이 없어 별도 concurrency 우려가 있으나 본 FIND 는 crash-atomicity 에 한정).
