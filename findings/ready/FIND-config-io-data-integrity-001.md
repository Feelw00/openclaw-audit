---
id: FIND-config-io-data-integrity-001
cell: config-io-data-integrity
title: config.json prefix-recovery rewrites main config via raw writeFile (non-atomic)
file: src/config/io.ts
line_range: 1093-1097
evidence: "```ts\nawait params.deps.fs.promises.writeFile(params.configPath, params.recoveredRaw,\
  \ {\n  encoding: \"utf-8\",\n  mode: 0o600,\n});\nawait params.deps.fs.promises.chmod?.(params.configPath,\
  \ 0o600).catch(() => {});\n```\n"
symptom_type: data-integrity-gap
problem: config.json 의 non-JSON prefix 자동 복구가 메인 config 파일을 temp+rename 없는 raw fs.promises.writeFile
  로 in-place 덮어쓴다. 이 복구 write 도중 SIGKILL/전원차단 시 config.json 이 partial-byte 상태로 truncate
  되어 다음 부팅에서 JSON.parse 실패.
mechanism: '1. config.json 이 손상되어 invalid (앞에 비-JSON prefix 가 붙은 상태) 로 read 됨.

  2. recoverConfigFromJsonRootSuffixWithDeps 가 suffix 에서 valid JSON root 를 찾아 validate
  통과 (io.ts:1112-1133).

  3. persistPrefixedConfigRecovery 호출 → 먼저 원본을 clobber-snapshot 으로 백업 (io.ts:1087,
  이건 flag wx 신규파일이라 안전).

  4. 이어서 메인 configPath 에 raw writeFile 로 recoveredRaw 를 in-place 덮어씀 (io.ts:1093)
  — temp 파일 + rename 없음.

  5. crash 주입 시점 = 4 의 writeFile 가 일부 바이트만 flush 한 순간. config.json 은 잘린 부분 JSON 으로
  남음.

  6. 다음 기동 read 에서 suffix recovery 가 못 살리면(잘린 데이터) JSON.parse 실패 → config 로드 불가.

  '
root_cause_chain:
- why: 왜 복구가 비원자적인가
  because: 복구 write 가 replaceFileAtomic 이 아닌 fs.promises.writeFile 로 메인 configPath
    를 직접 덮어쓴다.
  evidence_ref: src/config/io.ts:1093
- why: 왜 이것이 결함인가 (불균일)
  because: 동일 io.ts 의 정상 config write (io.ts:2414) 와 롤백 (io.ts:359 rollbackConfigFileWriteIfUnchanged)
    은 모두 replaceFileAtomic 을 쓰는데 이 복구 경로만 raw write 를 쓴다.
  evidence_ref: src/config/io.ts:359
- why: 왜 raw write 가 truncate 를 유발하나
  because: in-place writeFile 은 대상 파일을 즉시 열어(또는 truncate 후) 새 바이트를 순차 기록하므로, 중간 crash
    시 부분 기록 상태가 그대로 영속된다. atomic 경로(io.ts:1445 replaceFileAtomicSync)는 temp+rename
    으로 이 윈도우를 제거한다.
  evidence_ref: src/config/io.ts:1445
- why: 왜 자가복구가 안 되나
  because: config.json 은 사용자 authored SoT 라 파일시스템 다른 곳에서 재생성되는 derived-cache 가 아니다.
    손상 시 원본은 clobber-snapshot 백업 디렉터리에만 남고 메인 경로는 잘린 채 유지된다.
  evidence_ref: src/config/io.ts:1087
impact_hypothesis: data-loss
impact_detail: '정량: 트리거 빈도는 낮음 (recovery-only, doctor-config-preflight.ts:123 의 openclaw
  doctor 명령 경로에서만 호출, loadConfig read hot-path 자동 실행 아님). 단 발현 시 영향은 메인 config.json
  의 truncate 영속 — 원본은 별도 clobber-snapshot 디렉터리에 백업되어 있으므로 완전 영구손실은 아니나, 사용자가 백업 위치를
  알고 수동 복원해야 부팅 가능. 즉 자동 복구를 시도하다가 오히려 추가 손상을 만드는 D 카테고리(복구 경로 자체 비원자) 결함.

  '
severity: P2
counter_evidence:
  path: src/config/io.ts
  line: '359'
  reason: 'atomic sibling 명백 존재 — 같은 io.ts 의 rollbackConfigFileWriteIfUnchanged(io.ts:359)
    와 replaceConfigFileSync(io.ts:1445) 와 정상 write(io.ts:2414) 가 모두 replaceFileAtomic/replaceFileAtomicSync
    를 사용. replace-file 은 @openclaw/fs-safe/atomic 의 temp+rename 구현(src/infra/replace-file.ts:21,
    src/infra/json-files.ts:94 동일 사용). 따라서 "왜 여기만 raw" warrant 성립.

    derived-cache 반증: 대상 config.json 은 사용자 authored SoT 로 재생성 불가 (io.ts:1093 의 configPath
    는 메인 config). 다만 호출 빈도가 recovery-only + doctor cold path 라 P2 로 조정.

    '
status: discovered
discovered_by: data-integrity-auditor
discovered_at: '2026-05-29'
---
# config.json prefix-recovery rewrites main config via raw writeFile (non-atomic)

## 문제
config.json 에 비-JSON prefix 가 붙어 손상된 경우, openclaw 는 suffix 에서 valid JSON 을 찾아 자동 복구한다. 이 복구 단계가 메인 config.json 파일을 temp 파일 + rename 없이 raw `fs.promises.writeFile` 로 in-place 덮어쓴다 (io.ts:1093). 복구 write 도중 프로세스가 SIGKILL/전원차단으로 죽으면 config.json 은 부분 기록(truncate) 상태로 영속되고, 다음 기동에서 JSON.parse 가 실패할 수 있다. 손상된 파일을 고치려는 복구 동작이 오히려 추가 손상을 만들 수 있는 구조다.

## 발현 메커니즘
1. config.json 이 손상(앞에 비-JSON prefix) 되어 read 시 invalid snapshot 으로 분류.
2. `recoverConfigFromJsonRootSuffixWithDeps` 가 `findJsonRootSuffix` 로 valid JSON root 를 찾고 validate 통과 (io.ts:1112-1133).
3. `persistPrefixedConfigRecovery` 진입. 먼저 원본 raw 를 `persistBoundedClobberedConfigSnapshot` 으로 백업 (io.ts:1087). 이 백업은 별도 스냅샷 디렉터리에 `flag: "wx"` 신규 파일로 쓰므로 메인 config 를 건드리지 않음.
4. 이어서 메인 `configPath` 에 `recoveredRaw` 를 raw `fs.promises.writeFile` 로 in-place 덮어씀 (io.ts:1093). temp+rename 없음.
5. crash 주입 시점은 4의 writeFile 가 일부 바이트만 디스크에 flush 한 순간 — config.json 은 잘린 부분 JSON 으로 남는다.
6. 다음 기동 read 에서 suffix recovery 가 잘린 데이터를 못 살리면 JSON.parse 실패 → config 로드 실패. (원본은 3 의 clobber-snapshot 에만 존재.)

## 근본 원인 분석
- 복구 경로가 atomic helper 를 쓰지 않는다. io.ts:1093 의 `fs.promises.writeFile` 은 in-place 덮어쓰기로, 정상 config write 경로(io.ts:2414 의 `replaceFileAtomic`)나 config 롤백 경로(io.ts:359 의 `rollbackConfigFileWriteIfUnchanged` → `replaceFileAtomic`)와 달리 temp+rename 보호가 없다.
- 동일 모듈에 atomic sibling 이 분명히 있다 (io.ts:1445 `replaceConfigFileSync` → `replaceFileAtomicSync`). 즉 이 복구 함수만 불균일하게 raw 를 쓴다 — "왜 여기만 raw" warrant 성립.
- in-place raw write 는 대상을 즉시 열어 순차 기록하므로 중간 crash 시 부분 기록 상태가 영속된다. atomic 경로는 새 temp 에 완전히 쓰고 fsync 후 rename 으로 교체하여 이 truncate 윈도우를 제거한다 (src/infra/replace-file.ts:21, src/infra/json-files.ts:94).
- 대상 데이터는 derived-cache 가 아니다. config.json 은 사용자 authored SoT 라서 손상 시 다른 곳에서 자동 재생성되지 않는다 (원본은 clobber-snapshot 백업에만 남음).

## 영향
impact_hypothesis: data-loss.
재현 시나리오: prefix 손상된 config.json + `openclaw doctor` 실행 → 복구 write(io.ts:1093) 진행 중 SIGKILL → config.json truncate 영속 → 재기동 시 JSON.parse 실패로 config 로드 불가. 사용자는 clobber-snapshot 백업(io.ts:1087)에서 수동 복원해야 부팅 가능.
빈도: 낮음 (recovery-only, openclaw doctor 명령에서만; io.ts:1093 은 loadConfig read 자동 경로가 아님). 영향 심각도는 부팅 config 손상이지만 백업 존재 + cold path 이므로 P2.

## 반증 탐색
- atomic helper 존재 (확인): 같은 io.ts 의 정상 write(io.ts:2414), config 롤백(io.ts:359), `replaceConfigFileSync`(io.ts:1445) 가 전부 `replaceFileAtomic`/`replaceFileAtomicSync` 사용. `rg -n "replaceFileAtomic|replaceFileAtomicSync" src/config/io.ts` → 13, 359, 1445, 2414 매치. 불균일 입증됨.
- derived-cache 여부 (확인): config.json 은 사용자 authored SoT, 재생성 불가. 손상 자가복구 안 됨 → 결함 유효.
- 외부 패키지 경계 (확인): atomic 구현은 `@openclaw/fs-safe/atomic` (src/infra/replace-file.ts:8) 으로 in-tree 수정 불가지만, 결함은 "in-tree 소비처가 그 helper 를 안 쓴 것"이라 PR 범위 내.
- 호출 빈도 (확인): `rg -n "recoverConfigFromJsonRootSuffix" src/` → 소비처는 doctor-config-preflight.ts:123 (openclaw doctor) + config.ts 재노출. loadConfig 자동 read 경로 아님 → cold path → severity 하향.
- 기존 테스트 (확인): io.write-config.test.ts 에 recover 동작 테스트는 있으나 crash-during-recovery 원자성 테스트는 없음.

## Self-check
### 내가 확실한 근거
- io.ts:1093 이 raw `fs.promises.writeFile` 로 메인 configPath 를 in-place 덮어쓴다 (Read 로 확인, 1093-1097).
- 같은 모듈 다른 config write/rollback 경로는 atomic (io.ts:359, 1445, 2414 Read 로 확인).
- atomic 구현은 temp+rename (src/infra/replace-file.ts, src/infra/json-files.ts:94 의 replaceFileAtomic).
- 호출은 doctor cold path (doctor-config-preflight.ts:123 Grep 확인).

### 내가 한 가정
- in-place raw writeFile 중 crash 시 config 가 부분 기록으로 truncate 된다는 것은 POSIX write 의 일반 동작 가정 (해당 FS 가 저널링으로 보호하지 않는 한). 동일 가정이 atomic helper 존재 자체의 정당성이기도 함.
- clobber-snapshot 백업이 항상 성공한다는 보장은 별도 (io.ts:1087 이 throw 하면 더 이른 단계 실패).

### 확인 안 한 것 중 영향 가능성
- recoveredRaw 가 항상 originalRaw 보다 짧거나 같다는 보장은 없음 — 더 길면 truncate 윈도우가 더 큼.
- 동일 함수가 향후 loadConfig 자동 read 경로에 연결되면 hot-path 가 되어 severity 가 P1 로 올라갈 수 있음 (현재는 doctor only).
