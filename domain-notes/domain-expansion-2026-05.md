# 감사 도메인(x축) 확장 정찰 (2026-05-29)

5-agent 병렬 정찰로 미커버 core 영역에서 신규 감사 도메인 후보 발굴. upstream HEAD 61c538e2fc.

배경: 기존 10 도메인은 각 코드 영역의 좁은 슬라이스만 커버 (특히 src/agents/ 977파일 중
subagent-registry*/mcp-* 만, src/infra/ 387파일 중 ~7파일만). src/config/·tasks/·secrets/·
sessions/·logging/·acp/·hooks/·process/·daemon/ 등은 도메인 0. y축 신규 3축(data-integrity/
cross-store-consistency/ordering-causality) 의 핵심 증거지가 이 미커버 영역에 집중.

7축 = memory / lifecycle / concurrency / error-boundary / data-integrity / cross-store-consistency / ordering-causality.

## grid.yaml 에 등록한 STRONG 10 도메인 (1차 add)

| 도메인 | 첫 셀 (축) | 핵심 seed evidence |
|---|---|---|
| agent-session-store | data-integrity | auth-storage.ts:119,164 / settings-manager.ts:216 raw writeFileSync (crash 시 자격증명 lockout); session-manager.ts:800 rewriteFile sync 전체쓰기 |
| session-events | ordering-causality | user-turn-transcript.ts:407-417 영속-후-emit; transcript-events.ts:50-56 전역 Set 동기 fan-out + listener throw swallow; session-lifecycle-events.ts:24 |
| config-io | data-integrity | io.ts:1093 persistPrefixedConfigRecovery raw write (주 config truncate); io.ts:1429 restoreFileSnapshotSync 롤백 raw write; 반증 기준선 io.ts:1445 replaceFileAtomicSync |
| task-registry-store | cross-store-consistency | task-registry.ts:261-326 persist 함수 try/catch 전무 → 인메모리/sqlite 발산; sqlite:542/561/574 비트랜잭션 변형 vs :547/567 withWriteTransaction |
| secrets-apply | cross-store-consistency | apply.ts:835-855 config+N auth-store+.env 순차쓰기, 각 파일 atomic 이나 파일간 경계無 + best-effort 롤백(:846-853) |
| infra-state-migrations | cross-store-consistency | state-migrations.ts:966,996,1232,1254 다단계 renameSync, :996 수동 롤백=트랜잭션 부재 방증; state-migrations.fs.ts try/catch-swallow |
| infra-delivery-queue | ordering-causality | delivery-queue-recovery.ts:411-417 "refusing blind replay"; storage:73,199 send_attempt_started/unknown_after_send; 평행 2큐 패턴 복제(divergence) |
| diagnostic-recovery | ordering-causality | coordinator.ts:118-141 generation 갱신 인과; 중복 in-flight Set 2개 키 비대칭 (coordinator.ts:21 ref:generation vs runtime.ts:27 ref) |
| acp-control-plane | concurrency | manager.core.ts:1093/1136-1145 turn timeout 후 turnPromise detach + late emit; cleanupTimedOutTurn:1162; runtime-cache idle eviction:1952. 데몬 hot-path 싱글톤 |
| bash-process-execution | memory | bash-process-registry.ts:98-99 runningSessions/finishedSessions 무경계 Map + :335 sweeper 의존; moveToFinished:188-219 FD 정리 |

## 등록 보류 (STRONG 이나 wave-2 / 게이트 있음)

- **acp-gateway-agent** [STRONG] — src/acp/translator.ts + event-ledger.ts. memory(pendingPrompts/approvalRelays Map) + lifecycle(disconnect/reconcile) + ordering(event ledger seq) + data-integrity(ledger atomic write/16MB eviction). acp-control-plane 과 파일 분리. wave-2 add 권장. (src/acp/client.ts 는 인터랙티브 REPL → 제외)
- **embedded-agent-run-lifecycle** [STRONG] — src/agents/embedded-agent-runner/. memory(active/abandoned 다중 Map) + lifecycle(dispose 순서 finally 비대칭, attempt.subscription-cleanup.ts:101-120) + cross-store(ownedSessionFileWrites). run.ts 3603줄로 커서 셀은 attempt.* 서브로 좁혀 시작. wave-2.
- **process-command-lanes** [STRONG] — src/process/command-queue.ts + lanes.ts. concurrency(generation/draining/active-set 상태기계) + ordering(fire-and-forget reject). cron 병렬 레인 직결. wave-2.
- **infra-gateway-single-instance** [STRONG] — src/infra/gateway-lock.ts, stale-lock-file.ts, restart-stale-pids.ts. concurrency(stale 회수→재획득 TOCTOU) + lifecycle(PID 재사용 방어) + data-integrity(lock payload non-atomic). 단 covered infra-process(restart/respawn)와 의미 인접 → 셀은 lock/reclaim 한정. wave-2.
- **llm-provider-stream** [MEDIUM→STRONG(scope 축소 시)] — src/llm/providers/openai-codex-responses.ts. memory(websocketDebugStats:798/sseFallbackSessions:799 cleanup 누락 → 세션당 무한 성장, closeOpenAICodexWebSocketSessions:838 은 websocketSessionCache 만 정리). codex websocket 으로 scope 좁히면 STRONG. wave-2.

## CODEOWNERS 게이트 (감사 OK, SOL/PR 비용 높음)

- **auth-profiles-store** [STRONG] — src/agents/auth-profiles/. concurrency(refresh_contention + withFileLock) + cross-store(external-cli-sync → main store 병합) + data-integrity(store.ts:231 수동 open(wx)+writeFileSync+rmSync). CODEOWNERS `src/agents/*auth*` → 소유자 동의 필요.
- **infra-device-auth-pairing** [STRONG] — src/infra/device-pairing.ts:191-200 pending+paired 두 파일 Promise.all 비원자 동시쓰기 → split-brain. cross-store. 보안경로 → PR 게이트.

## SKIP (도메인 미가치)

- **gmail-watcher** — 최근 6주 upstream 3건 집중 fix(listener/respawn/leak 축) 진행 중 → CAL-008 upstream-competing 위험 최고. 외부 바이너리(gog) 의존으로 real-behavior-proof 난도 높음. feature-frozen 경계. **진입 금지** (진입 시 gatekeeper 단계 gh pr list 필수).
- **flows** (src/flows/) — doctor/health-check/카탈로그 순수 함수형. setInterval/child_process/fire-and-forget 0건. 상태성 없음.
- **cli / commands / shared** — 일회성/인터랙티브 프로세스 + 정적 상수 lookup Set/Map(literal-bounded). 데몬 장기 상태 없음. global-singleton.ts callers 는 전부 타 도메인 소유.
- **process-supervisor** [WEAK-MID] — 상태기계 복잡하나 영속/cross-store surface 약함(메모리 내). 3 lifecycle 타이머 경합만. STRONG 소진 후 보조.
- **infra-heartbeat** — 전부 in-process setTimeout + 인메모리 cooldown. 영속 surface 약. setTimeout overflow clamp 정도가 단일 FIND 거리.
- **subagent-recovery-reconciliation** [WEAK] — surface 실재하나 파일 작고 store helper 가 src/config/sessions/ 밖 → 경계 흐릿. agent-session-store 에 흡수 검토.
- **session-memory-hook** [STRONG 이나 보류] — hooks/bundled/session-memory. ordering(reset vs 스냅샷 read 인과) + data-integrity(memoryRoot.write atomic 미확인). hook 영역이라 wave-2 또는 별도 검토.

## data-integrity 축 공통 주의 (전 도메인 적용)
atomic-write 원시 연산(temp+fsync+rename)은 외부 패키지 `@openclaw/fs-safe@0.3.0` 의
`replaceFileAtomic`/`replaceFileAtomicSync` 에 있음. in-tree 셀은 그 **소비/조합 결함**
(어떤 writer 가 atomic helper 를 안 쓰고 raw writeFileSync 하는가) 만 다룬다. 외부 패키지
default fsync 동작 수정은 PR 범위 밖.

## 첫 사이클 권고 (재현/수정 최소 + maintainer-fit 순)
1. agent-session-store × data-integrity — auth.json raw write → 자격증명 lockout. 재현 쉽고 fix 1줄(writeFileSync→replaceFileAtomicSync), maintainer reliability 직격.
2. config-io × data-integrity — config 손상 = 부팅 실패. 동일 파일 atomic 반증 기준선 존재.
3. task-registry-store × cross-store-consistency — 인메모리/sqlite 발산(lost-delete).
4. session-events × ordering-causality — 영속-후-emit + 전역 listener 누적.

착수 전 필수: persona 작성 (data-integrity-auditor / ordering-causality-auditor), PR 큐 ≤7 확인.
