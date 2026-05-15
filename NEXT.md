# 세션 플레이북

새 세션 시작 시 이 파일 하나만 보고 다음 액션 결정.

## 1. 부팅 절차 (CAL-004, CAL-007 반영)

### A. audit repo sync
```bash
cd /Users/lucas/Project/openclaw-audit
git pull --ff-only
/tmp/openclaw-audit-venv/bin/python skills/openclaw-audit/harness/local_state.py show | head -40
ls findings/drafts/ findings/ready/ issue-candidates/ solutions/ 2>/dev/null
```

### B. openclaw repo **반드시** upstream 최신화 + fork 동기화 (CAL-007 원천)

**FIND / CAND 작업 시작 전 필수**. stale 코드로 감사하면 이미 fixed 된 결함을 false positive 로 재탐지하게 됨.

```bash
cd /Users/lucas/Project/openclaw
git remote | grep -q upstream || git remote add upstream https://github.com/openclaw/openclaw.git
git fetch upstream main
BEHIND=$(git rev-list --count HEAD..upstream/main)
echo "behind upstream: $BEHIND commits"

# main 이 upstream/main 뒤면 fast-forward + fork 도 push
if [ "$BEHIND" -gt 0 ]; then
  git pull upstream main --ff-only
  git push origin main  # fork 도 동기화 (Feelw00/openclaw main)
fi

# 관심 영역 최근 변경 확인 (upstream 발견 fix 와 중복 방지)
git log upstream/main --since="2 weeks ago" --oneline -- src/plugins/ src/cron/ src/infra/ src/agents/ src/context-engine/ | head -30
```

### C. PR 작업 중 worktree 는 별도

**worktree 위치 규칙**: 모든 PR worktree 는 `/Users/lucas/Project/openclaw-worktrees/pr-NNN/` 에 생성.
`Project/` 루트 직접 생성 금지 (지저분함).

```bash
# 신규 worktree 생성 예
cd /Users/lucas/Project/openclaw
git worktree add /Users/lucas/Project/openclaw-worktrees/pr-NNN -b fix/<branch-name> upstream/main
```

open PR worktree 는 `fix/*` 브랜치라 main 업데이트와 독립. rebase 필요 시 `git rebase upstream/main` 로 개별 처리.

(venv 없으면: `python3 -m venv /tmp/openclaw-audit-venv && /tmp/openclaw-audit-venv/bin/pip install pyyaml`)

## 2. 결정 트리 (위에서 아래 순으로 체크)

| 조건 | 액션 |
|---|---|
| **메인테이너 (CODEOWNERS / maintainers.md 인물) CHANGES_REQUESTED / COMMENT** | **R-10 필수** — 답변 쓰기 전 3 agent cross-review (positive/critical/neutral) 먼저. 특히 critical agent 에 "메인테이너가 놓친 edge case 도 함께 탐색" 프롬프트. pushback 톤 금지. `calibration/CAL-006-maintainer-review-tone.md` 참조 |
| PR 에 follow-up commit push 완료 | **Greptile 자동 재리뷰 없음** — PR 에 `@greptile review and provide confidence score` 코멘트로 수동 트리거 |
| **PR 에 Codex / Greptile bot P1+ 지적** | **CAL-009 프로토콜** — 병렬 2-agent (positive + critical) 로 지적 검증 → 반박 가능 시 공손 reply + `gh api graphql resolveReviewThread` / 반영 필요 시 worktree 에서 수정 → push → Greptile 재트리거 |
| PR 에 bot 리뷰/코멘트 있음 (P2+) | 해당 worktree 에서 대응 → push → Greptile 재트리거 |
| `findings/drafts/` 에 파일 있음 | `validate.py --all --move` |
| `findings/ready/` ≥ 2 건 (같은 도메인 누적) | clusterer 페르소나 호출 |
| `issue-candidates/` 에 gatekeeper 미평가 CAND 있음 (`state: pending_gatekeeper`) | gatekeep 3-step (sanitize → agent → apply --shadow) |
| gatekeeper 판정 끝난 CAND + 사용자 허락 | **R-11 post-harness cross-review** — `skills/cross-review/` 스킬 사용. `harness/run.py --target CAND-NNN --mode post-harness` 로 프롬프트 렌더 → Agent tool 로 병렬 실행 → `aggregate.py` 로 집계. 허락 없이 자동 실행 금지. 아래 §7.1 참조 |
| gatekeeper `approve` + cross-review 2/3 이상 real | **R-12 pre-sol real behavior proof** — `skills/real-behavior-proof/` 스킬 (mode: pre-sol). without-fix 빌드 단독으로 production-like 환경 결함 재현. 모든 severity 적용. 아래 §7.5 참조 |
| pre-sol `collected` / `blocked-external-dep` / `blocked-env` | 사람 최종 검토 → SOL 작성 착수 (blocked 사유는 SOL frontmatter `pre_sol_proof.status` 에 trace) |
| pre-sol `unreproducible` | CAND abandon (false-positive-by-reproduction). cross-review 가 놓친 false positive 의 마지막 안전망 |
| gatekeeper `uncertain` / `needs-human-review` | cross-review 결과로 approve/scope-축소/abandon 결정 |
| `solutions/` 에 `status: drafted` SOL + `chosen_fix` 결정 + 사용자 허락 | **R-13 post-sol real behavior proof** — `skills/real-behavior-proof/` 스킬 (mode: post-sol). with-fix vs without-fix 비교 + 6 필드 PR body evidence 산출. §7.5 참조 |
| post-sol `collected` | pre-pr cross-review (§7.2) → PR 발행 (PR body 에 `pr_body_section` paste, `proof: supplied` 자동 부여) |
| post-sol `blocked-external-dep` / `blocked-env` | 회귀 테스트만으로 PR 발행 + PR body 에 blocked 사유 명시 (`proof: sufficient` 미부여 수용) |
| post-sol `unreproducible` | fix 효과 없음 → SOL abandon 또는 `chosen_fix` 재선택 |
| 위 전부 없음 | 새 셀 선택 (아래 §3) |

## 3. 새 셀 선택

```bash
# 아직 안 돌린 Phase 1 셀 확인
grep -A3 "phase: 1" grid.yaml | grep -E "^  - id:|state:"

# 감사 완료 셀 인벤토리 (axis 중복 회피용 — 새 셀 진입 시 참조).
# 상세 outcome / PR / 종결 사유는 issue-candidates/index.yaml + solutions/SOL-*.md + openclaw-pr-tracker.md.
#
# Phase 1 (5/5):
#   ✓ plugins-memory / plugins-lifecycle / cron-concurrency / agents-registry-memory / infra-process-error-boundary
#
# Phase 2 (5/5):
#   ✓ plugins-error-boundary / cron-memory / infra-retry-concurrency / infra-process-memory / agents-registry-concurrency
#
# Phase 3 (6/6):
#   ✓ auto-reply-concurrency / gateway-memory / gateway-error-boundary / gateway-concurrency
#   ✓ channels-error-boundary / channels-lifecycle (2 FIND validate REJECT — YAML frontmatter 복구 작업 대기)
#
# Phase 4 (4/4):
#   ✓ plugins-concurrency / context-engine-memory / cron-error-boundary / cron-lifecycle
#
# Phase 5 (1/N — "plugin loading" 영역):
#   ✓ mcp-memory  (cap/FIFO 후속 v2 셀 보류 — PR #71648 머지 후 착수)
#
# OPEN openclaw PR (다음 세션에서 상태 확인 우선):
#   • #68669 (CAND-011, agents-registry-concurrency) — `proof: supplied+sufficient` + `triage: refactor-only`. 무대응 유지.
#   • #71648 (CAND-025→SOL-0008, mcp channel-bridge TTL sweeper) — `proof: supplied` 만. 메인테이너 리뷰 대기.
# 상세 + 종결된 PR / merged history → openclaw-pr-tracker.md.
#
# 다음 세션 액션 우선순위 (잔여):
#
#   ## 0. 최우선 — 9 CAND production end-to-end re-verification (사용자 결정 2026-05-14)
#
#   ### 진행 상태 (2026-05-15)
#
#   - **CAND-026 ✅ wire-level e2e collected** (PROOF-CAND-026-pre-20260515-002424.md).
#     production bundle `dist/mcp/plugin-tools-serve.js` + 실 stdio MCP transport +
#     `createPluginToolsMcpServer({tools:[probe]})` factory inject + 실 SDK Client 가
#     `notifications/cancelled` 송신 → tool.execute 의 4번째 인자 signal=undefined 확인.
#     unit-level 결과와 일치 + client side AbortError 정상 + server side 끝까지 실행
#     (elapsedMs=501) 분리 관측으로 결함 위치 wiring 안에 정확히 박혀있음 확정.
#
#   - **나머지 8 CAND (030/031/032/033/037/038/039/040): production e2e 보류** — 사유는
#     아래 §"e2e 실행 환경 분류" 참조.
#
#   - **2026-05-15 후속 진행 (CAND-033/030/037 blocked + CAND-038 skeleton)**:
#       • CAND-033 blocked-external-dep: resolveFollowupAuthorizationKey 가 sender/exec 필드만
#         보는데 single telegram user account 메시지는 모두 동일 → authGroups ≥2 production-faithful
#         생성 불가. burner phone 으로 두 번째 telegram 계정 추가 set-up 시에만 e2e 가능.
#       • CAND-030/037 blocked-module-level: 사용자 결정 (2026-05-15) 으로 건너뜀.
#         module-level only + instrumentation 의존 → 외부 환경 set-up 가치 0.
#       • CAND-038/039/040 (gateway 3건): 외부 환경 set-up 진행 결정.
#         skeleton 작성 완료 (`skills/real-behavior-proof/harness/proof_CAND_038_e2e.py`).
#         인프라 ready: isolated_home + gateway loopback + auth=none + mock_llm + audit ws probe.
#         1차 실행은 SUT spawn 명령 정정 (`gateway start --auth none`) 까지. 디버깅 다음 세션.
#
#   - **2026-05-15 (다음 세션) CAND-039 e2e collected** (PROOF-CAND-039-pre-20260515-061713.md):
#     `proof-CAND-039-e2e` 시나리오 5 trial 전부 fire (fire_rate=1.0). production-faithful
#     결함 발현 직접 관측 — recovery IIFE 가 `[gateway] ready` 직후 ~25ms 만에 fire,
#     일부 trial 은 SIGTERM 후에도 `Recovered delivery ... on telegram` + `Delivery recovery
#     complete: 1 recovered` 출력. close_prelude_ms 가 43ms↔2.3s 두 패턴 — 후자는 recovery
#     in-flight 인 상태에서 shutdown 이 background 완료를 기다리는 결함 직접 측정. 축약된
#     인프라 (build worktree 우회 + 메인 repo bundle + IIFE only 측정) 로 multi-session 추정
#     4.5h → 실 ~2h. state transition `proof-blocked-pre → proof-collected-pre`. **다음**:
#     SOL-CAND-039 작성 진입 또는 post-sol 단계. fix surface 옵션 A (isClosing 가드 + timer
#     handle) / 옵션 B (AbortSignal 전파) 결정 필요.
#
#   - **2026-05-15 (이 세션) CAND-038 ws handshake + chat.send ack 작동** —
#     `skills/real-behavior-proof/harness/device_pairing.py` ✅ (ed25519 keypair seed) +
#     `harness/mock_openai_cand038.mjs` ✅ (hold-then-complete + req.on("close") detection) +
#     `scenarios/proof-CAND-038-e2e.py` ✅ (audit ws probe TS: connect.challenge → device
#     payload v2 signed → connect → hello-ok → chat.send → ws.close).
#     **1차 e2e 시도 결과** (`proofs/PROOF-CAND-038-pre-20260515-082544.md`):
#       • probe_connected=true, helloOk=true, chatSendAck={runId, status: "started"} ✅
#       • probe_wsCloseCode=1000 ✅
#       • mock_request_started_count=0 ❌ — chain reach LLM 부재.
#     원인: env_isolate 의 minimal cfg 가 새 schema 와 불일치 → SUT default cfg 사용 →
#     agentRuntime.id="codex" + codex CLI binary 부재 → LLM 호출 전 chain 막힘.
#     **다음 세션 (~1.5h)**: state_dir/openclaw.json 에 새 schema 와 일치하는 cfg 직접 작성
#     (agentRuntime.id="openai-responses" + models.providers.openai.baseUrl=mock_port).
#     상세 옵션 비교: gateway-e2e.md §CAND-038 의 4번 항목.
#
#   - **2026-05-15 (1차 세션) CAND-038/039/040 1차 e2e 시도 결과 — 모두 `blocked-external-dep`**:
#     SUT spawn 명령 정정 완료 (`gateway run --auth none --bind loopback --port <p> --allow-unconfigured`,
#     1.6s ready). audit ws connect.challenge 수신 + connect frame schema (PROTOCOL_VERSION=4 +
#     ConnectParamsSchema 정확형) 까지 진행. 그 후 세 CAND 모두 audit-side infrastructure 신규
#     작업 필요 — multi-session size (telegram-e2e.md 패턴 동등).
#       • CAND-038: device pairing helper (ed25519 keypair + state dir 3 file, 참고
#         `scripts/e2e/lib/upgrade-survivor/update-restart-auth.sh:140-225`) + audit ws client
#         + mock-openai disconnect detection + chat workflow chain 검증. cli mode connect 시
#         `NOT_PAIRED: device identity required` (auth.mode=none 인데도 device 필수). 작업량 ~5h.
#       • CAND-039: pending state pre-injection (delivery-queue + restart-sentinel) + close
#         prelude 지연 trigger (현 graceful 46ms 라 setTimeout 1250 fire window 미발생) +
#         observable 강화 (recovery subsystem log). 작업량 ~4.5h.
#       • CAND-040: native runtime stub + capability 등록 path + approval trigger +
#         activeEntries 측정 sideband. 작업량 ~5.5h.
#     영속화: `proofs/PROOF-CAND-{038,039,040}-pre-20260515-052108-e2e-blocked.md` 3건 + CAND
#     frontmatter `pre_sol_proof.status=blocked-external-dep` 갱신 + state transition
#     `proof-blocked-pre` 기록. 상세 다음 세션 작업 분할 → **`gateway-e2e.md`**
#     (telegram-e2e.md 패턴). 이 파일은 CAND-038/039/040 e2e 작업 시에만 읽어라.
#
#   - **2026-05-15 후속 결정**: 사용자가 "외부 환경 set-up 후 재시도" 선택 → telegram
#     인프라 구축 진행. CAND-031/032/033 (channel 의존) 의 driver 자동화 인프라 완료:
#     telegram bot 2개 + group + Telethon user account driver + audit-side wrapper.
#     상세 (인프라 인벤토리 / 결정 배경 / 사용 패턴 / 트러블슈팅) → **`telegram-e2e.md`**.
#     이 파일은 CAND-031/032/033 e2e 작업 시에만 읽어라.
#
#   - **CAND-032 ❌ e2e unreproducible (2026-05-15)**: Task 10-13 인프라 완성 후 3 attempt
#     모두 reply-run-registry path 미활성화. ACTIVE_EMBEDDED_RUNS cleanup 과 reply-run-registry
#     operation cleanup 이 사실상 동시에 일어나 race window 가 좁고 production 에서
#     자연스럽게 노출 안 됨. CAL-003 안전망 발동 → CAND-032 abandoned. 상세:
#     `proofs/PROOF-CAND-032-pre-20260515-024753-e2e.md`.
#
#     교훈: telegram e2e 인프라가 wire-level 작동 확인 됐고 다른 CAND 에도 재사용 가능.
#     같은 패턴 (좁은 race window) 의 다른 CAND 도 e2e unreproducible 가능성 시사.
#
#   - **CAND-031 ❌ dropped (2026-05-15)**: production sequence 재분석 결과 self-recurse
#     trigger 는 **LLM throw 가 아닌 LLM 외부 throw** (resolveQueuedReplyExecutionConfig /
#     runPreflightCompactionIfNeeded / sendFollowupPayloads) — followup-runner.ts:358 의
#     inner catch 가 LLM throw 를 잡아 drain self-recurse 발동 안 함. 외부 throw 자체도
#     비정상 환경 한정 + enqueue-followup activation timing 좁음 (CAND-032 와 동일 종류
#     reproducibility risk). effective severity 낮고 SOL 작성 가치 의문 — CAND 자체 drop.
#     상세: `proofs/PROOF-CAND-031-pre-20260515-031046-e2e.md`.
#
#     교훈: drain.ts:303-313 catch+finally self-recurse 코드 로직은 결함이지만 production
#     hot-path 발현 가능성 낮음. 같은 유형 (deterministic-fail 가정 + 비정상 환경 한정) CAND
#     는 unit-level reproducibility 강해도 e2e + SOL 가치가 낮을 수 있음.
#
#   ### e2e 실행 환경 분류 (2026-05-15 결정)
#
#   | CAND | e2e 가능성 | 사유 | 권고 |
#   |---|---|---|---|
#   | CAND-026 | ✅ wire-level | standalone MCP server entry (`dist/mcp/plugin-tools-serve.js`) + named export → 외부 spawn + 실 wire 가능 | 완료 |
#   | CAND-030 | ❌ blocked-module-level (2026-05-15) | subagent-registry 가 cli inline module + production bundle named export 없음. marker Map size 측정에 instrumentation 패치 필수 (proof-CAND-030.py 의 `__test = {...}` 주입). 외부 환경 (LLM/OAuth/메신저) 과 무관 → 외부 환경 set-up 으로 e2e 가치 추가 0. 사용자 결정 (2026-05-15): 건너뜀 | unit-level final 후보 |
#   | CAND-031 | ❌ dropped (2026-05-15) | LLM throw 는 inner catch 가 잡음. 외부 throw triggers 는 비정상 환경 한정 + enqueue-followup timing window 좁음. effective severity 낮음 + SOL 가치 의문. | abandoned |
#   | CAND-032 | ❌ e2e unreproducible (2026-05-15) | reply-run-registry path 가 production sequence 에서 미활성화 (ACTIVE_EMBEDDED_RUNS 가 dominate). 3 attempt 시도 후 abandon. | abandoned |
#   | CAND-033 | ❌ multi-account 필수 (2026-05-15) | drain.ts:89 `resolveFollowupAuthorizationKey` 가 senderId/senderE164/senderIsOwner/execOverrides/bashElevated 만 봄. single telegram user account 메시지는 sender 필드 모두 동일 → authGroups 1개 → 결함 미발현. execOverrides/bashElevated 도 메시지별 변화 path 없음. burner phone 으로 두 번째 telegram 계정 추가 set-up 시에만 e2e 가능 | blocked-external-dep (multi-account 인프라 부재) |
#   | CAND-037 | ❌ blocked-module-level (2026-05-15) | context-engine plugin loader. factory inject + dispose 측정에 instrumentation 의존. production binary 실행 path 없음 + 외부 환경 무관. 사용자 결정 (2026-05-15): 건너뜀 | unit-level final 후보 |
#   | CAND-038 | ⏳ in-progress (2026-05-15 2차) | ws handshake + chat.send ack 성공 ✅. chain reach LLM 부재 (cfg/agentRuntime 디버깅 ~1.5h 남음). | gateway-e2e.md §CAND-038 |
#   | CAND-039 | ✅ **collected** (2026-05-15 다음 세션) | 5/5 trial fire. recovery IIFE production-faithful 발현 직접 관측. close prelude 43ms↔2.3s 두 패턴. SOL 작성 진입 가능. | proofs/PROOF-CAND-039-pre-20260515-061713.md |
#   | CAND-040 | ❌ blocked-external-dep (2026-05-15) | gateway 부팅 ✅. native runtime stub + capability 등록 path + approval trigger + activeEntries 측정 sideband 필요. 작업량 ~5.5h. | gateway-e2e.md §CAND-040 |
#
#   **분류 키**:
#   - ✅ wire-level: 외부 의존 0 + production binary spawn + 실 wire 통과 (CAND-026 만 해당)
#   - ❌ module-level only: cli 부팅 없이 production module 호출 가능. production bundle 빌드는
#     하지만 wire-level 가치 unit-level 과 거의 동일 (CAND-030, CAND-037)
#   - ❌ 외부 환경 필요: cli/gateway 부팅 + 외부 메신저/LLM/OAuth/multi-account user 환경
#
#   ### 결정 사항 (사용자 결정 완료 — 2026-05-15)
#
#   사용자 결정: 옵션 2 (외부 환경 set-up 후 재시도). 진행 상태:
#     • CAND-031/032/033 (channel): telegram 인프라 완성 → 031 dropped, 032 unreproducible, 033 blocked-external-dep
#     • CAND-030/037 (module-level): 사용자 결정 (2026-05-15) 건너뜀, unit-level final 채택 가능
#     • CAND-038/039/040 (gateway): 1차 시도 (2026-05-15) → blocked-external-dep. 다음 세션 audit-side
#       인프라 작업 (gateway-e2e.md 참조). multi-session 진행.
#
#   ### 배경: 2026-05-14 세션의 pre-sol 9/9 collected 는 unit-level isolation test 이지
#   ### production 실제 실행 검증이 아님 (CAL-003 정직한 인정).
#
#   2026-05-14 세션의 9 CAND pre-sol 결과 (`proofs/PROOF-CAND-*-pre-20260514-*.md`) 는 모두
#   `collected` 였으나, 다음 한계가 있다:
#
#   - **production bundle (`openclaw.mjs`) 빌드 우회** (`--skip-build`). tsx + src ts 직접 import.
#   - 시나리오가 production code 의 **일부 함수만 단위로 호출** (e.g. `createPluginToolsMcpHandlers`,
#     `scheduleFollowupDrain`, `createChannelApprovalHandlerFromCapability` 등 entry function 만).
#   - **나머지 의존성은 in-script mock** — gateway 전체 부팅 / cron / channel transport / OAuth / LLM /
#     sqlite / WebSocketServer 등 production 실행 path 전혀 안 거침.
#   - 모든 시나리오 `REQUIRES_EXTERNAL_DEP=False` + `isolated_home(require_oauth=False)`.
#
#   즉 "결함 메커니즘이 production code 그 함수 안에 그대로 존재함" 을 unit 수준으로 보였지,
#   "production 실행 시 그 경로가 실제로 taken 되는가" 는 별도 미검증. CAL-003 위험 그대로 현재진행형.
#
#   ### 다음 세션 목표: 9 CAND production end-to-end re-verification
#
#   각 CAND 별 production execution path 식별 + `REQUIRES_EXTERNAL_DEP=True` 시나리오 작성 또는
#   기존 시나리오를 production-faithful 로 재작성. `openclaw.mjs` 또는 dist 빌드 실행 +
#   실 transport / SDK / sqlite / lifecycle 호출 + 실 시간 흐름 시뮬.
#
#   #### 9 CAND production execution path (재검증 설계)
#
#   | CAND | production entry | 필요 실행 환경 | 결함 trigger 방법 |
#   |---|---|---|---|
#   | CAND-026 | plugin-tools MCP server (`openclaw plugin-tools serve` 등 standalone) | 실 stdio MCP transport + SDK Server | host (probe-client) 가 callTool 발사 후 `notifications/cancelled` 송신 → tool.execute 가 signal 받는가 측정 |
#   | CAND-030 | subagent registry + 실제 subagent lifecycle | 실 cli 부팅 + 실 subagent 생성 (또는 deps stub 하지만 production registry 모듈은 그대로) | listener 가 aborted timer schedule + 15s 이내 user kill → entry 의 marker map 측정 |
#   | CAND-031 | auto-reply queue runner | 실 cli 부팅 + 실 channel adapter (no LLM) | enqueue + deterministic-fail backend (contextEngine.compact throw mock 만, 다른 stack production) → drain self-recurse 관측 |
#   | CAND-032 | reply-run-registry + pi-embedded-runner | 실 cli 부팅 + 실 backend.queueMessage path | activeSession.steer reject 시 unhandledRejection escape + infra/unhandled-rejections classifier 동작 |
#   | CAND-033 | collect-mode drain | 실 multi-account user 시뮬 + clearSessionQueues trigger | inner-for race window 에 clear 발사 후 Y group fire 여부 |
#   | CAND-037 | plugin slot config 로 3rd-party engine 등록 | 실 plugin loader + factory 가 fake-but-instantiated engine 반환 | resolveContextEngine 호출 시 contract error fallback → dispose 호출 여부 |
#   | CAND-038 | 실 gateway server + WebSocketServer | gateway 부팅 + 실 ws 클라이언트 connect + chatAbortController register | ws.close 후 controller.signal.aborted 측정 |
#   | CAND-039 | 실 gateway bootstrap | gateway 부팅 + 즉시 SIGTERM | recover 함수의 dynamic import 실제 fire 됐는지 (log 또는 외부 observable) |
#   | CAND-040 | 실 approval handler + capability nativeRuntime | gateway 부팅 + 실 approval flow + handler.stop() | deliverTarget 도중 stop → activeEntries 잔존 여부 측정 |
#
#   #### 단계별 진행 권고
#
#   1. **인프라 준비**: build.py `skip_build=False` 로 다시 (실 production bundle 필요).
#      pnpm build 30분 / per worktree. base+head 두 빌드 → 60분.
#   2. **시나리오 재작성 또는 신규**: CAND 별로 `proof-CAND-NNN-e2e.py` 신규 작성 권장
#      (기존 unit-level `proof-CAND-NNN.py` 보존). `REQUIRES_EXTERNAL_DEP=True`,
#      `isolated_home(require_oauth=True if 필요)`.
#   3. **실행 cost 큼**: CAND 당 5-30분 (cli 부팅 + setup + trial + cleanup). 9 CAND 전체
#      예상 4-6시간 + 빌드 30분 × N. 다중 세션 진행 필요.
#   4. **진행 순서** (2026-05-15 시점 outcome — 위 §"e2e 실행 환경 분류" 결정 반영):
#        세션 M+1 (완료): CAND-026 wire-level e2e collected ✅
#        세션 M+2~ (보류): CAND-030/031/032/033/037/038/039/040 모두 외부 환경 또는
#                        module-level only — 사용자 결정 대기. 외부 환경 set-up 안 하면
#                        unit-level final 채택 또는 보류 유지.
#   5. **상태 enum**:
#        - `collected` (e2e) — production 실행에서도 결함 재현 → SOL 작성 진입
#        - `unreproducible` — unit-level 결함이 production path 에 실제로 안 나타남 → CAND abandon
#                              (가장 가치 있는 결과 — false positive 의 마지막 안전망)
#        - `blocked-external-dep` — OAuth / LLM / channel 의존이라 실 실행 못 함. unit-level 결과를
#                                    final 로 채택할지 사람 판단.
#   6. **각 CAND e2e collected → SOL 작성 진입 (decision tree 와 동일)**.
#
#   #### 각 세션 진입 첫 액션
#
#   1. 사용자 허락 (real-behavior-proof skill §Step 1 gate)
#   2. `upstream/main` fetch + behind 확인 (§1.B)
#   3. 해당 CAND 의 production execution path 재검토 (위 표)
#   4. 시나리오 새로 작성 또는 기존 e2e 시나리오 수정
#   5. **production bundle 빌드** (`--skip-build` 옵션 제거)
#   6. `harness/run.py --target CAND-NNN --mode pre-sol --scenario proof-CAND-NNN-e2e` 실행
#   7. status 평가 + transition 기록 (proofs/PROOF-CAND-NNN-pre-{ts}-e2e.md 같은 별도 파일명 권장)
#   8. unit-level 결과 (`PROOF-CAND-NNN-pre-20260514-*.md`) 와 비교 — 일치 / 불일치 보고
#
#   #### CAND-038/039/040 다음 세션 진행 (2026-05-15 1차 시도 후 — 상세는 gateway-e2e.md)
#
#   1차 시도 결과 (2026-05-15 이 세션): SUT spawn 명령 정정 (`gateway run --auth none --bind
#   loopback --port <p> --allow-unconfigured`, 1.6s ready) + connect handshake schema 정확형
#   (PROTOCOL_VERSION=4 + ConnectParamsSchema) 까지 확인. 그 후 audit-side infrastructure
#   신규 작업 필요 — 세 CAND 각각 multi-session size (~4.5-5.5h/CAND). 영속화:
#   `proofs/PROOF-CAND-{038,039,040}-pre-20260515-052108-e2e-blocked.md` 3건 + CAND
#   frontmatter `blocked-external-dep` + state transition `proof-blocked-pre`.
#
#   다음 세션 진행 순서 권고 (가성비 순) — 2026-05-15 갱신:
#   1. ~~CAND-039~~ ✅ collected (2026-05-15). SOL 작성 진입 가능.
#   2. CAND-038 (~3.5h 남음) — device pairing ✅. 남은: audit ws client + mock LLM + 시나리오
#   3. CAND-040 (~5.5h) — native runtime stub + capability + approval trigger
#
#   각 CAND 진행 시작 시 `gateway-e2e.md` §CAND-NNN starting points 부터 읽어라.
#
#   #### SOL 작성은 e2e collected 결과를 받은 후 (보류)
#
#   2026-05-14 unit-level 9/9 collected 만으로 SOL 작성 진입 안 함. 사용자 결정:
#   "실제 실행으로 다시 검증할거야" — e2e re-verification 완료 후 SOL 작성 단계.
#
#   ## 1-5. 기존 잔여 액션 (위 0번 이후)
#
#   1. PR #68669 무대응 유지 — close 트리거 시 race fix 논거 제시 + reopen 또는 CAL-010 credit-only.
#   2. PR #71648 메인테이너 리뷰 대기 — sufficient 자동평가 미부여 (fake timer 추정). real wall-clock 재시도는 mcp-pending-ttl
#      scenario + TTL env override hook 도입에 의존.
#   3. CAL-011 calibration 정식 문서 작성 — alternative-axis acceptance 패턴 (PR #71040 사례).
#   4. real-behavior-proof skill end-to-end 검증 — 2026-05-14 9 CAND pre-sol 일괄 통과로 검증 완료.
#      build.py skip_build + tsx 패턴이 신규 표준. SOL-0004 는 2026-04-21 PR #68842 로 머지 완료
#      (proof skill 도입 전이라 미적용).
#   5. Phase 5 후속 셀 — mcp-lifecycle / mcp-concurrency / mcp-memory v2 / agents-registry-lifecycle / 신규 도메인 (event-bus / channel-bridge-concurrency).
#
# CLOSED (이전 액션, 완료):
#   • PR #68341 (CAL-008 upstream-competing) — 2026-05-11 MERGED. CAND-021/022 retract 결정 사후 검증.
#     CAL-008 outcome 섹션 참조.
#
# 신규 셀 정의 시 grid.yaml §types 에 id 추가 후 §cells 확장.
```

셀 실행 프롬프트 템플릿 (Agent 도구, `subagent_type=general-purpose`):
```
너는 {페르소나 이름} 페르소나다.
/Users/lucas/Project/openclaw-audit/agents/{페르소나}.md 완전히 읽고 R-1~R-4 엄수.

openclaw repo: /Users/lucas/Project/openclaw
audit repo   : /Users/lucas/Project/openclaw-audit
셀: {cell-id}
allowed_paths: {grid.yaml 해당 도메인}

산출물 (Write tool 필수):
- findings/drafts/FIND-{cell-id}-{NNN}.md (최대 3~4 건)
- domain-notes/{domain}.md append

R-3 Grep 결과를 counter_evidence.reason 에 명시.
```

## 4. 다음 셀 선택 시 우선순위

Phase 1-5 의 21 셀 모두 1차 audit 완료. 새 셀 착수 시:

1. **메인테이너 공개 우선순위 부합 도메인** 우선 (memory / plugin loading / cron / reliability)
2. PR queue 여유 확인 (`gh pr list --author "@me" --repo openclaw/openclaw --state open` ≤ 7)
3. 기존 abandoned CAND 와 axis 중복 회피 (CAL-004/CAL-008 패턴)
4. 신규 도메인 진입 시 `domain-notes/<name>.md` 신규 작성 의무

현재 후보 (위 §3 마지막 코멘트 참조): mcp-lifecycle / mcp-concurrency / mcp-memory v2 / agents-registry-lifecycle / event-bus.

## 5. 졸업 조건 (shadow → 자동화)

```bash
wc -l metrics/shadow-runs.jsonl metrics/human-verdicts.jsonl metrics/self-consistency.jsonl
# 목표: 50 / 10 / 10
```

현재 (2026-05-14, 9 CAND pre-sol proof 일괄 완료 후): **46 / 23 / 10** —
self-consistency 졸업, human-verdicts 졸업, shadow-runs 4 누적 더 필요 (50 목표).
2026-05-14 세션의 9 proof transition 으로 shadow-runs 28→46 점프. SOL/post-sol 단계 진입 시 50 도달 확실.

## 6. 세션 종료

```bash
# 1. 변경사항 commit
git add -A
git status --short
git commit -m "<action-oriented 요약>"

# 2. push
git push

# 3. 다음 세션을 위해 상태 간단 메모 (선택)
echo "next: {한 줄}" >> orchestrator-log.md
```

## 7. Cross-review 단계 (R-11 post-harness + CAL-003 PR 직전)

### 7.0 실행 경로 (cross-review 스킬)

모든 cross-review 는 `skills/cross-review/` 스킬 사용. 5단계 프로토콜:

1. **사용자 허락** (gate 필수)
2. `run.py --target <T> --mode <M>` 로 프롬프트 JSON 렌더
3. Agent tool 병렬 dispatch (한 메시지, 여러 tool_use 블록)
4. `aggregate.py --target <T> --mode <M>` 로 집계 + `metrics/cross-review-*.jsonl` 영구 기록
5. `primary_decision.action` 에 따라 다음 단계

상세: `skills/cross-review/SKILL.md`. 역할 카탈로그: `skills/cross-review/ROLES.md`. 모드 프리셋: `skills/cross-review/modes/*.yaml`.

### 7.1 Post-harness cross-review (severity gate + 사용자 허락)

**트리거 조건** (CAL-008 이후, Option C 하이브리드):

| severity | gatekeeper verdict | 행동 |
|---|---|---|
| P0 / P1 / P2 | approve / uncertain | **사용자 허락 후 cross-review 필수** — 메인테이너 visibility 높음, false positive 비용 큼 |
| P3 | approve | **skip cross-review** — gatekeeper 의 upstream-dup + primary-path inversion 만으로 커버. SOL 작성 가치는 사용자 판단 |
| P3 | uncertain | 과거 패턴 (CAND-007/008) 대로 기본 abandon. cross-review 는 사용자 요청 시만 |
| 전체 | reject_suspected | skip cross-review — 이미 reject |

이 gate 의 근거:
- CAL-008: gatekeeper 에 upstream-dup check 가 추가되면 P3 단순 leak 은 단독 판단으로 충분
- cross-review 비용 = 5 agent × CAND. P3 에 5-agent 투입은 오버엔지니어링
- P2+ 는 false positive 리스크가 실제 PR/메인테이너 관계 비용으로 이어져 추가 필터 가치 있음

**사용자 허락** 은 P2+ 에서 여전히 필수 (5 agent 병렬 리소스).

mode: `post-harness`.

기본 역할 세트 (5개 기본, 3 최소):

| 역할 | 프롬프트 초점 |
|---|---|
| **positive-advocate** | "왜 merge 해야 하는가" — 문제 존재 증거, production 영향 경로, 메인테이너 수용 가능성 |
| **critical-devil** | "왜 close 해야 하는가" — primary-path inversion, CAL-001~005 재확인, unconditional guard 재탐색 |
| **reproduction-realist** | 재현 테스트가 production hot-path 와 동일 branch 인가 (CAL-003). synthetic race 위험. fake timer / mock 의존도. |
| **hot-path-tracer** | production caller stack 추적 — 문제 경로가 정상 사용자 시나리오에서 taken 되는가 |
| **upstream-dup-checker** | `git log upstream/main` 에서 동일/유사 fix 이미 있는지 (CAL-004) |

판정 enum (각 에이전트가 반드시 반환):
- `real-problem-real-fix`
- `real-problem-fix-insufficient`
- `synthetic-only` (test path ≠ production hot-path)
- `false-positive` (primary cleanup 이 이미 처리)
- `upstream-duplicate` (이미 upstream 에서 해결)

**결과 해석**:
- 3/3 real → approve → SOL 작성
- 2/3 real + 1 scope 우려 → scope 축소 후 진행
- 긍정 시점마저 real 판정 못 함 → false-positive 가능성 높음 → abandon
- 재현 에이전트가 synthetic-only → test 를 production branch 로 재작성하거나 abandon

**CAL 추가 반영**:
- CAL-001: critical agent 에 primary-path inversion 필수 포함
- CAL-003: reproduction realist 필수 포함
- CAL-004: upstream dup checker 포함 권장

### 7.2 PR 발행 직전 cross-review (CAL-003, mode: pre-pr)

PR 제출 **직전** 3 agent 병렬 재검증. fix 포함 최종 diff 기준.
합의 2/3 미만이면 retract 또는 scope 축소. **긍정 시점마저 real 판정 못 하면** 거의 확실한 false positive.

### 7.3 메인테이너 리뷰 답변 전 cross-review (R-10/CAL-006, mode: maintainer-response)

메인테이너 CHANGES_REQUESTED / COMMENT 받으면 답변 전 `skills/cross-review/harness/run.py --target PR#NNNNN --mode maintainer-response --maintainer-quote "..." --invariant "..." --pr-reference "PR#NNNNN @<sha>"` 실행.
기본 5 agent: critical-devil, maintainer-invariant-hunter, schema-boundary-fuzzer, caller-surface-auditor, reproduction-realist.
톤 체크리스트: `modes/maintainer-response.yaml` 의 `tone_checklist`.

## 7.5 Real behavior proof (R-12 pre-sol / R-13 post-sol)

**스킬**: `skills/real-behavior-proof/` (별도 — cross-review 와 분리. build/run/measure 가 본질).

openclaw 의 외부 PR 라벨 정책 (`triage: needs-real-behavior-proof` / `proof: supplied` / `proof: sufficient`,
출처 `scripts/github/real-behavior-proof-policy.mjs`) 에 맞춰 SOL 작성 전/후 production-like 환경에서
실제 결함 재현/검증을 자동화. 머지된 5 PR 의 V2/V3/V4 사후 evidence 강화 패턴을 SOL 단계로 forward-shift.

**모드 2개**:
- `pre-sol` (R-12): gatekeeper approve + cross-review proceed 직후. without-fix 빌드 단독.
  목적: false positive 사전 차단 + post-sol baseline 확보.
- `post-sol` (R-13): SOL chosen_fix 결정 후. with-fix vs without-fix 두 빌드 비교.
  산출물: openclaw 6 필드 PR body 텍스트 (`pr_body_section`).

**모든 severity 적용 (P3 포함)**. 재현 불가능은 강등 없이 별도 상태 (`blocked-external-dep` 등).

**호출 (사용자 허락 필수)**:
```bash
# pre-sol (single build, scenario 1회)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 --mode pre-sol --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> --trials 100

# post-sol (build pair + scenario × 2)
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/run.py \
  --target SOL-0004 --mode post-sol --scenario gateway-map-size \
  --base-sha <upstream/main HEAD> --head-sha <fix HEAD> --trials 100
```

**산출물 위치**:
- `proofs/PROOF-{target}-{pre|post}-{ts}.md` — 영구 evidence + PR body section
- `solutions/SOL-NNNN.md` frontmatter 의 `pre_sol_proof` / `post_sol_proof` 객체
- `local-state/state.yaml` + `history.jsonl` transition (`proof-{collected|unreproducible|blocked|skipped}-{pre|post}`)

**status enum** (둘 다 동일): `pending | collected | unreproducible | blocked-external-dep | blocked-env | skipped-by-user`

**시나리오 카탈로그** (`skills/real-behavior-proof/scenarios/`):
- `cron-manual-run` — PR #78243 baseline. sqlite task_runs status='lost' 측정. `REQUIRES_EXTERNAL_DEP=True` (OAuth + LLM 호출).
- `gateway-map-size` — SOL-0004 패턴. Map.size 측정. 외부 의존 없음.
- `mcp-pending-ttl` — PR #71648 패턴. real wall clock TTL (fake timer 회피, sufficient 라벨 노림).

**격리**: `env_isolate.isolated_home()` 가 `OPENCLAW_HOME=/tmp/proof-{uuid}/.openclaw` redirect.
production `~/.openclaw/` 손상 0. OAuth profile 만 read-only 복사 (LLM 호출 가능).

**openclaw policy 호환 검증**: `harness/render_proof.py` 가 6 필드 형식 정확 mirror.
검증: `node -e` 로 `evaluateRealBehaviorProof()` 직접 호출 → `status: "passed"` 확인.
guard `_check_no_inline_heading` 가 line-start `# ` 패턴 (policy 가 거기서 break) 자동 raise.

**상세**: `skills/real-behavior-proof/SKILL.md` (5 단계 호출 규약 + 시나리오 작성 규약).

## 8. 긴급 참조

- 운영 상세: `OPERATIONS.md`
- 기여 규칙: `openclaw-contribution.md`
- 페르소나 규율: `agents/memory-leak-hunter.md` §"필수 규율 R-1~R-7"
- **과거 실패 회고 (반드시 읽기)**:
  - `calibration/CAL-001-maintainer-verdict-CAND-004.md` (메인테이너 post-merge reject, R-5 원천)
  - `calibration/CAL-002-greptile-review-CAND-005.md` (Greptile bot partial gap)
  - `calibration/CAL-003-cross-review-retract-CAND-006.md` (self-caught synthetic-only, R-7 원천)
  - `calibration/CAL-004-upstream-merge-lag-CAND-005.md` (upstream superseded, R-8 원천)
  - `calibration/CAL-005-bot-contradiction-boundary.md` (bot contradiction, R-9 원천)
  - `calibration/CAL-006-maintainer-review-tone.md` (메인테이너 톤 실수, **R-10 원천 — 가장 위험**)
  - `calibration/CAL-007-stale-fetch-before-find.md` (stale upstream 기반 FIND, NEXT.md §1 fast-forward 강제 원천)
  - `calibration/CAL-008-gatekeeper-upstream-dup-gap.md` (gatekeeper upstream-dup check 필수 원천)
  - `calibration/CAL-009-codex-bot-review-rebuttal.md` (Codex/Greptile bot 지적 병렬 검증 → 반박/반영 결정 프로토콜)
  - `calibration/CAL-010-indirect-merge-with-credit.md` (PR #70142 — 메인테이너가 우월한 fix 로 직접 commit + 우리 PR closed + changelog credit. atomic helper 반환 contract 활용 미스 + indirect-merge outcome 분류 신설)
  - **CAL-011 (TODO 작성)** — alternative-axis acceptance + cross-review-driven retract. CAL-008 (dup-axis 선제) + CAL-010 (indirect-merge with credit) 의 hybrid 패턴. 메인테이너가 다른 axis 로 동일 문제 해결 시 우리 PR close + 잔여 영역만 follow-up issue 로 좁게 분리.
- **PR 트래커 (모든 내 openclaw PR)**: `openclaw-pr-tracker.md`
  - 파이프라인 외 PR + 종결된 PR + Greptile 재리뷰 수동 트리거 절차
- **Telegram E2E 인프라**: `telegram-e2e.md`
  - CAND-031/032/033 e2e 작업 시에만 읽어라. bot 인벤토리 / Telethon driver / 결정 배경 / 트러블슈팅 포함
- **Gateway E2E 인프라**: `gateway-e2e.md`
  - CAND-038/039/040 e2e 작업 시에만 읽어라. device pairing / pending state injection / native runtime stub / 다음 세션 작업 분할 포함
