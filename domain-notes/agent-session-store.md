# domain-notes: agent-session-store

도메인: agent-session-store (data-integrity 축 신규)
최초 감사: 2026-05-29 (data-integrity-auditor)
대상 upstream: openclaw @ 61c538e2fc22d01b8e08549b07a9e530e6eee35c

## 파일 인벤토리 (allowed_paths)

| 파일 | 역할 | 영속 대상 |
|---|---|---|
| `src/agents/sessions/session-manager.ts` | 세션 transcript(.jsonl) 라이프사이클 (load/append/rewrite/branch/migrate) | 대화 기록 SoT |
| `src/agents/sessions/auth-storage.ts` | API 키 / OAuth refresh 토큰 저장소 (auth.json) | 자격증명 SoT |
| `src/agents/sessions/settings-manager.ts` | global/project settings.json 관리 | 사용자 설정 SoT |
| `src/agents/session-write-lock.ts` | 세션 파일 write 직렬화 락 (raw fs write 없음) | - |
| `src/agents/session-write-lock-error.ts` | write-lock 에러 타입 | - |
| `src/agents/session-file-repair.ts` | 손상 세션 파일 복구 (replaceFileAtomic 사용) | 대화 기록 (복구) |
| `src/agents/session-transcript-repair.ts` | transcript 엔트리 구조 복구 (in-memory, 직접 fs write 없음) | - |
| `src/agents/queued-file-writer.ts` | append-only 라인 writer (infra/fs-safe appendRegularFile 경유) | append 로그 |

## write 경로별 atomic 여부 표

| writer | 파일 | 메커니즘 | atomic? | 비고 |
|---|---|---|---|---|
| `auth-storage.ts:119` (withLock sync) | auth.json | raw `writeFileSync` + `chmodSync` (advisory lock 보유) | NO | FIND-001. lock 은 동시성만, crash-atomicity 없음 |
| `auth-storage.ts:164` (withLockAsync) | auth.json | raw `writeFileSync` + `chmodSync` (OAuth refresh) | NO | FIND-001 (동일 패턴, async) |
| `auth-storage.ts:77` (ensureFileExists) | auth.json | `writeFileSync("{}")` 신규 생성 | N/A | 빈 파일 시드, 기존 데이터 위험 없음 |
| `session-manager.ts:800` `rewriteFile` → `writeJsonlEntriesSync` | *.jsonl | raw `writeFileSync` (O_TRUNC 전량 재기록), 락 없음 | NO | FIND-002. migrate(741)/empty(732)/branch(1271) 호출 |
| `session-manager.ts:842/845` `persist` → `appendJsonlEntr(y|ies)Sync` | *.jsonl | `appendFileSync` (truncate 아님) | N/A | append-only, 부분손상 위험 낮음 (truncate 결함 아님) |
| `settings-manager.ts:216` (withLock) | settings.json | raw `writeFileSync` (advisory lock 보유) | NO | FIND-003. parse 실패 시 {} fallback → silent reset |
| `session-file-repair.ts:409` `repairSessionFile` | *.jsonl | `replaceFileAtomic` (temp+rename) + 사전 backup(405) | YES | **반증 기준선(atomic sibling)**. 동일 .jsonl 재기록을 atomic 처리 |
| `queued-file-writer.ts:37` `safeAppendFile` → `appendRegularFile` | append 로그 | infra/fs-safe `appendRegularFile` (append, symlink 거부) | N/A | append-only, infra 책임. 결함 아님 |

핵심 헬퍼:
- `src/infra/replace-file.ts` → `@openclaw/fs-safe/atomic` 의 `replaceFileAtomic`/`replaceFileAtomicSync` 재노출. in-tree consumer 가 선택 사용.
- `src/config/sessions/transcript-jsonl.ts` → `writeJsonlEntriesSync`(raw writeFileSync, :26-27), `appendJsonlEntr*Sync`(appendFileSync). atomic 변형 없음.

## 발견 요약

| FIND | 대상 | symptom | severity | 한 줄 |
|---|---|---|---|---|
| FIND-001 | auth-storage.ts:114-122 (+164) | data-integrity-gap | P1 | auth.json raw writeFileSync truncate, crash 시 자격증명 lockout. atomic sibling=file-repair:409 |
| FIND-002 | session-manager.ts:800-805 | data-integrity-gap | P1 | rewriteFile 마이그레이션 경로(741)가 transcript 전량 truncate-rewrite, crash 시 대화 전체 소실 |
| FIND-003 | settings-manager.ts:206-217 | data-integrity-gap | P2 | settings.json raw write, crash+{} fallback 으로 설정 silent reset |

## 카테고리 적용 결과 (data-integrity-auditor 체크리스트)

- [x] A 비원자 단일파일 쓰기 — FIND-001/002/003 (3건)
- [x] B 다중 syscall 비원자 — auth-storage write(119)+chmod(120) 창을 FIND-001 mechanism 에 포함. settings/session-manager 는 별도 chmod 분리 없음
- [x] C 부분 multi-write 불일치 — skipped. allowed_paths 내 `void`/fire-and-forget 영속 write 없음 (session-manager:621 `.catch` 는 영속 multi-write 아님)
- [x] D 복구/롤백 비원자 — skipped(결함 없음). session-file-repair 의 복구 경로는 replaceFileAtomic+backup 으로 이미 atomic
- [x] E 교차 스토어 경계 부재 — skipped. allowed_paths 내 다중 영속 스토어 동시갱신(Promise.all write / multi-store) 없음. auth/settings/transcript 는 각각 단일 파일 단일 writer

## 후속 감사 단서 (다음 셀용)

- `session-manager.ts:800 rewriteFile` 에 파일락이 없다 — 동일 세션을 두 프로세스가 열 때 concurrency-race 가능성(본 축 밖, concurrency 페르소나 검토 후보).
- `auth-storage` 의 proper-lockfile stale lock 디렉터리가 crash 후 다음 기동 lock 획득을 막는지(lifecycle 검토 후보).
- `appendJsonlEntr*Sync`(appendFileSync) 의 partial-line append 시 다음 read 가 마지막 깨진 줄을 어떻게 처리하는지(transcript-jsonl + session-file-repair 연계, append 손상 별도 셀).
- atomic 변형(`writeJsonlEntriesAtomic`)이 transcript-jsonl.ts 에 부재 — rewriteFile 의 atomic 화는 헬퍼 추가가 필요(SOL 단계 참조).
