---
candidate_id: CAND-045
type: epic
finding_ids:
- FIND-agent-session-store-data-integrity-001
- FIND-agent-session-store-data-integrity-002
- FIND-agent-session-store-data-integrity-003
cluster_rationale: "공통 근본 원인 (cross-file, clusterer.md Step 3): agent-session-store\
  \ 도메인의 세 영속\ncritical 파일 (auth.json / 세션 .jsonl transcript / settings.json) writer\
  \ 가 모두\n기존 파일을 raw writeFileSync 로 in-place O_TRUNC 후 재기록한다. truncate 와 마지막\n바이트\
  \ flush 사이가 비원자라, 그 창에서 SIGKILL/전원차단 시 파일이 빈/부분 상태로\n잔존한다. 동일 코드베이스에 atomic 헬퍼(replaceFileAtomic,\
  \ temp 작성 후 atomic rename)\n가 존재하고 session-file-repair.ts:409 가 동일한 .jsonl 전량 재기록에\
  \ 실제로 이를\n사용하는데, 더 critical 한 자격증명/대화기록/설정 writer 만 raw write 라 atomic 규율이\n파일별로\
  \ 불균일하다. 세 FIND 의 root_cause_chain 이 \"왜 atomic rename 을 안 쓰는가\n(불균일)\" 단계에서 동일하게\
  \ evidence_ref=src/agents/session-file-repair.ts:409 를\n가리키므로 같은 인프라 축(atomic 파일\
  \ 교체 규율)의 단일 원인이다.\n\n각 FIND root_cause_chain 인용:\n- FIND-001 root_cause_chain[0]:\
  \ \"writeFileSync 가 대상 경로를 직접 O_TRUNC 로 열어\n  in-place 재기록 → truncate 와 flush 사이에\
  \ 비원자 창 존재\"\n  (evidence_ref: src/agents/sessions/auth-storage.ts:119)\n- FIND-001\
  \ root_cause_chain[2]: \"동일 코드베이스의 세션 transcript repair 경로는 동일한\n  in-place 재기록을\
  \ replaceFileAtomic(...) 으로 처리하는데 ... 자격증명 writer 는 raw\n  writeFileSync 를 쓴다 —\
  \ atomic 규율이 파일별로 불균일\"\n  (evidence_ref: src/agents/session-file-repair.ts:409)\n\
  - FIND-002 root_cause_chain[0]: \"rewriteFile → writeJsonlEntriesSync → writeFileSync\
  \ 가\n  대상 .jsonl 을 O_TRUNC 로 열어 전량 in-place 재기록하므로 truncate 와 마지막 엔트리\n  flush 사이가\
  \ 비원자\"\n  (evidence_ref: src/config/sessions/transcript-jsonl.ts:27)\n- FIND-002\
  \ root_cause_chain[2]: \"동일 세션 .jsonl 을 in-place 전량 재기록하는 또 다른\n  경로인 repairSessionFile()\
  \ 은 replaceFileAtomic(...) 으로 처리한다. 같은 파일종류의\n  재기록인데 rewriteFile 만 raw writeFileSync\"\
  \n  (evidence_ref: src/agents/session-file-repair.ts:409)\n- FIND-003 root_cause_chain[0]:\
  \ \"writeFileSync 가 대상 파일을 O_TRUNC 로 열어 in-place\n  재기록 → truncate 와 flush 사이 비원자\
  \ 창\"\n  (evidence_ref: src/agents/sessions/settings-manager.ts:216)\n- FIND-003\
  \ root_cause_chain[3]: \"동일 코드베이스의 transcript repair 가 in-place 재기록을\n  replaceFileAtomic(temp+rename)\
  \ 으로 처리하는데(session-file-repair.ts:409), 사용자\n  설정 writer 는 raw writeFileSync — atomic\
  \ 규율 불균일\"\n  (evidence_ref: src/agents/session-file-repair.ts:409)\n\nepic 으로 묶는\
  \ 이유: 세 writer 는 서로 다른 파일/모듈이지만 결함의 본질\n(raw writeFileSync in-place truncate → crash\
  \ 비원자 창)과 이미 트리에 존재하는 동일\natomic 기준선(session-file-repair.ts:409 의 replaceFileAtomic)이\
  \ 단일하다. 공통 인프라\n축(atomic 파일 교체 규율의 일관 적용)을 다루므로 GH Issue 1건 + 자식 task 로 묶는 것이\n\
  N개 분산 발행보다 적합. (해결책 자체는 본 CAND 범위 밖.)\n"
proposed_title: 'agent-session-store: 영속 critical 파일(auth.json / 세션 transcript / settings.json)
  writer 가 atomic 헬퍼 없이 raw writeFileSync 로 in-place truncate → crash 시 데이터 소실'
proposed_severity: P1
existing_issue: null
created_at: 2026-05-29
pre_sol_proof:
  status: collected
  proof_record: proofs/PROOF-CAND-045-pre-20260529-070725.md
  measurements:
    scenario: proof-CAND-045
    trials: 2
    trialResults:
    - branch: control
      loadFailed: false
      keyAfter: <present>
      lockout: false
    - branch: crash-truncate
      keyBeforeCrash: <present>
      fullBytes: 80
      truncatedBytes: 40
      loadFailed: true
      keyAfterCrash: <empty>
      keyAfterRecoverAttempt: <empty>
      recoveryNoOp: true
      lockout: true
    controlOk: true
    crashLockout: true
  scenario: proof-CAND-045
---

# agent-session-store: 영속 critical 파일 writer 가 atomic 헬퍼 없이 raw writeFileSync 로 in-place truncate → crash 시 데이터 소실

## 공통 패턴

agent-session-store 도메인의 세 영속 SoT 파일 writer 가 동일한 비원자 쓰기 패턴을 공유한다:

- 기존 파일을 raw `writeFileSync` 로 직접 연다 → OS 레벨에서 `O_TRUNC` (기존 내용 0으로) 후
  새 바이트 기록. truncate 와 마지막 바이트 flush 사이가 비원자.
- 이 창에서 SIGKILL / 전원차단 / 패닉 시 파일은 빈(0바이트) 또는 부분 JSON/JSONL 로
  디스크에 잔존한다.
- 다음 기동/resume 시 parse 실패 또는 entries 0 감지 → 자격증명 lockout(FIND-001) /
  대화기록 폐기(FIND-002) / 설정 silent reset(FIND-003) 으로 귀결. 손상 데이터는 SoT 라
  재생성 불가하며 코드 경로가 자가복구하지 못한다.

핵심: 동일 코드베이스의 `src/agents/session-file-repair.ts:409` `repairSessionFile()` 은
동일 종류의 in-place 전량 재기록을 `replaceFileAtomic`(temp 작성 후 atomic rename, +사전
backup) 으로 처리한다. atomic 헬퍼(`replaceFileAtomic`/`replaceFileAtomicSync`,
`src/infra/replace-file.ts` 재노출)가 트리 안에 존재하고 실제로 쓰이는데, 더 critical 한
자격증명/대화기록/설정 writer 만 raw write 를 택했다. atomic 파일 교체 규율이 같은 도메인
안에서 파일별로 불균일하다는 점이 이 epic 의 단일 축이다.

세 FIND 모두 advisory lock(proper-lockfile)의 보호 범위를 짚는다: lock 은 다중 프로세스
간 mutual-exclusion(lost-update)만 막고, 단일 write 의 crash-atomicity 와는 무관하다
(lock 보유 중 crash 하면 파일은 이미 truncated). FIND-002 의 rewriteFile 은 파일락조차
없다.

## 관련 FIND

- FIND-agent-session-store-data-integrity-001 (P1): `auth-storage.ts:114-122`(+164).
  auth.json(API 키 / OAuth refresh 토큰 SoT) 을 advisory lock 하에 raw `writeFileSync`
  로 truncate-rewrite. crash 시 빈/부분 JSON → 다음 기동 `JSON.parse` throw, `loadError`
  세팅 + `persistProviderChange` early-return(287) 로 손상 파일을 덮어쓰지도 못해 전
  provider 자격증명 lockout. OAuth refresh(164) 가 만료 주기마다 동일 write 를 수행해
  노출이 드물지 않음.

- FIND-agent-session-store-data-integrity-002 (P1): `session-manager.ts:800-805`.
  `rewriteFile()` → `writeJsonlEntriesSync` → raw `writeFileSync`(transcript-jsonl.ts:27)
  로 세션 .jsonl(대화기록 SoT) 전량 truncate-rewrite. 가장 위험한 호출은 구버전 세션
  resume 시 마이그레이션 경로(741) — 이미 valid 한 전체 transcript 를 full overwrite 하다
  crash 시 빈/부분 JSONL, 다음 resume 이 `newSession()` 으로 폐기(728) → 대화 전체 영구
  소실. rewriteFile 에는 파일락도 없음. 스키마 bump 직후 노출면이 광범위.

- FIND-agent-session-store-data-integrity-003 (P2): `settings-manager.ts:206-217`.
  global/project settings.json(사용자 설정 SoT) 을 advisory lock 하에 raw `writeFileSync`
  로 truncate-rewrite. crash 시 빈/부분 JSON → 다음 기동 `tryLoadFromStorage` 가 parse
  실패를 catch 해 `{}` 로 graceful fallback(333) → 모든 사용자 설정이 에러 표면화 없이
  조용히 default 로 reset. 설정 변경 시에만 write 라 노출 빈도는 auth/transcript 보다 낮음
  (P2).
