---
candidate_id: CAND-055
type: epic
finding_ids:
  - FIND-bash-process-execution-memory-001
  - FIND-bash-process-execution-memory-002
cluster_rationale: |
  공통 근본 원인 (clusterer.md Step 3, FIND 본문 cross_refs 존중): bash-process-execution
  의 세션 수명 관리에서, set/add 에 대응하는 unconditional eviction 경로가 없어
  (정상 흐름에서 자동 호출되는 무조건 제거가 부재) 컨테이너가 선형/무제한 성장한다는
  동일 memory-leak 축의 발현이다. 두 FIND 는 frontmatter 에서 서로를 명시 cross_ref 한다
  (FIND-001.cross_refs=[FIND-002], FIND-002.cross_refs=[FIND-001]).

  각 FIND root_cause_chain 인용:
  - FIND-...-001 root_cause_chain[0] ("왜 리스너가 정상 종료 시 남는가"): "`{ once: true }`
    는 이벤트 발생 시에만 제거를 보장하는데, 정상 종료/yield 경로에는
    removeEventListener(onAbortSignal) 호출이 전혀 없다"
    (evidence_ref: src/agents/bash-tools.exec.ts:1659)
  - FIND-...-001 root_cause_chain[3] ("왜 abort 경로의 once 자동제거에 의존하면 안
    되는가"): "정상 종료가 abort 보다 압도적으로 흔한 hot-path 이며, 이 경로에서는 abort
    이벤트 자체가 발생하지 않아 once 가 영영 트리거되지 않는다"
    (evidence_ref: src/agents/embedded-agent-runner/run/attempt.ts:3003)
  - FIND-...-002 root_cause_chain[0] ("왜 finishedSessions 가 무제한으로 커질 수
    있는가"): "set(225)에 대응하는 자동 제거가 TTL prune(326) 하나뿐이고 개수
    상한(cap/LRU)이 없다. TTL 윈도 안에서는 추가만 일어난다"
    (evidence_ref: src/agents/bash-process-registry.ts:225)
  - FIND-...-002 root_cause_chain[2] ("왜 prune 이 항상 실행된다고 보장 못 하는가"):
    "prune 은 sweeper(setInterval) 콜백으로만 트리거된다(335). sweeper 가
    없으면(stopSweeper 후 재시작 누락 등) 만료 항목도 영구 잔존한다 — finishedSessions
    자체엔 lazy prune(get/list 시 정리) 경로가 없다"
    (evidence_ref: src/agents/bash-process-registry.ts:335)

  공통 surface: 두 FIND 모두 "set/add 는 hot-path 에서 무조건 일어나는데, 대응하는
  제거는 conditional-edge(abort fire / sweeper 생존+TTL)이거나 dead(clearFinished
  프로덕션 호출자 없음)뿐이라 unconditional eviction 경로 부재" 라는 동일 결함 클래스다.
  두 FIND 의 counter_evidence(R-5 실행 조건 분류표)도 같은 형식으로 "unconditional eviction
  경로 없음 → leak 성립" 으로 수렴한다. 또 둘 다 retained 데이터가 exec 출력 버퍼(session
  aggregated/tail)라 항목당 메모리가 작지 않다는 점을 공유한다.

  epic 으로 묶는 이유: 같은 도메인(bash-process-execution)의 exec/session 수명 cleanup
  누락이라는 같은 결함 클래스 + 같은 surface(set 대비 unconditional eviction 경로 부재로
  인한 보유 컨테이너 선형 성장)를 공유하고, FIND 본문이 서로를 cross_ref 한다. severity 는
  둘 다 P2. (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "bash-process-execution 세션 수명 cleanup 누락: exec abort 리스너가 per-run signal 에 정상종료 시 미제거 + finishedSessions Map size cap 부재(TTL+sweeper 의존)"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-29
---

# bash-process-execution: set 대비 unconditional eviction 부재 → 리스너/세션 컨테이너 성장

## 공통 패턴

bash-process-execution 의 세션 수명 관리에서 set/add(hot-path 무조건 실행)에 대응하는
unconditional eviction 경로가 없어, 보유 컨테이너가 run/process 수명 동안 선형 또는 무제한
성장한다. 자동 제거 경로는 모두 conditional-edge(특정 이벤트 발생 시) 이거나 dead(프로덕션
호출자 없음)뿐이다.

- **per-run AbortSignal 리스너 누적(FIND-001)**: exec tool 의 execute()가 매 호출마다
  공유 `runAbortController.signal`(attempt.ts:712, 모든 tool.execute 가 :3003 으로 공유)에
  `onAbortSignal` 리스너를 `{ once: true }` 로 등록한다(bash-tools.exec.ts:1659). `once` 는
  abort 이벤트가 실제 fire 될 때만 제거하고, 정상 종료/실패/yield 경로엔 removeEventListener
  가 전혀 없다. abort 가 hot-path 가 아니므로(정상 종료가 압도적) 한 run 의 exec K 호출은
  리스너 K 개 + 클로저가 캡처한 run/session 그래프(aggregated/tail 출력 버퍼 + child 참조)
  K 개를 run 종료까지 보유 → 선형 성장(memory-growth).
- **finishedSessions Map size cap 부재(FIND-002)**: 모듈 전역 `finishedSessions` Map
  (bash-process-registry.ts:99)이 backgrounded 세션 종료마다 set 되고(moveToFinished:225),
  자동 제거는 TTL prune(endedAt < now-jobTtlMs, :326) 하나뿐 — size cap/LRU 없음. prune 은
  sweeper(setInterval) 콜백에만 묶여(:335) sweeper 중지/미시작 시 만료분도 정리 안 됨. lazy
  prune(get/list 시 정리) 경로 부재. jobTtlMs 기본 30분(최대 3시간) 윈도 안에서 고빈도
  background-exec 는 보유량 ≈ 종료 빈도 × TTL 로 무제한 누적, 각 항목이 aggregated 출력
  보유(memory-growth).

공통 기준선/반증: 두 FIND 모두 R-3 grep 으로 "unconditional eviction 경로 없음" 을 확정한다
(FIND-001: removeEventListener match 0; FIND-002: cap/LRU/while-size eviction match 0,
clearFinished 프로덕션 호출자 없음 dead). 대응 제거가 conditional-edge(abort fire / sweeper
생존+TTL / process clear 액션)뿐이라 정상 hot-path 에서 무조건 정리되지 않는다. retained
데이터가 exec 출력 버퍼라 항목당 메모리가 비무시. 단 abort 발생/TTL+sweeper 정상 시 bound 가
존재하므로 둘 다 P2(OOM 즉발 아닌 baseline 상승).

## 관련 FIND

- FIND-bash-process-execution-memory-001 (P2, memory-leak): `bash-tools.exec.ts:1656-1660`.
  exec execute()가 per-run 공유 AbortSignal 에 `{ once: true }` 리스너 등록하나 정상 종료/
  yield 경로에 removeEventListener 부재 → 한 run 의 exec 호출마다 리스너 + run/session
  그래프(출력 버퍼 + child 참조)가 run 종료까지 누적. signal 이 per-run 공유(attempt.ts:712/3003)
  인 점이 핵심 조건. 빌드/테스트 루프·cron 다단계 등 한 run 다수 exec 워크로드에서 선형 성장.

- FIND-bash-process-execution-memory-002 (P2, memory-leak): `bash-process-registry.ts:322-337`.
  finishedSessions Map 에 size cap/LRU 부재, 자동 제거가 TTL prune(sweeper 의존) 하나뿐.
  TTL(기본 30분, 최대 3시간) 윈도 안에서 고빈도 background-exec 는 보유량 ≈ 종료빈도×TTL 로
  누적(예: 분당 10개 종료, TTL 30분 → 약 300개 상주), sweeper 중지/미시작 시 만료분도 정리
  안 됨 → 사실상 무제한. 각 항목이 aggregated 출력 + tail(최대 2000) 보유.
