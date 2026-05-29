# session-events 도메인 노트

openclaw 의 `src/sessions/**` 세션 이벤트/transcript 영속 + 전역 listener fan-out
서브시스템에 대한 영구 관찰 기록. 페르소나/세션별로 append-only.

---

### ordering-causality-auditor (2026-05-29)

셀: `session-events-ordering-causality`. allowed_paths: `src/sessions/**`.
upstream: 61c538e2fc.

#### 대상 파일 현황 (이벤트/순서 관련)

| 파일 | LOC | 책임 |
|---|---|---|
| `src/sessions/transcript-events.ts` | 57 | 전역 `SESSION_TRANSCRIPT_LISTENERS` Set + `emitSessionTranscriptUpdate`(동기 fan-out) + `onSessionTranscriptUpdate`(구독) |
| `src/sessions/session-lifecycle-events.ts` | 28 | 전역 `SESSION_LIFECYCLE_LISTENERS` Set + `emitSessionLifecycleEvent` + `onSessionLifecycleEvent` |
| `src/sessions/user-turn-transcript.ts` | 648 | user-turn 메시지 append + persist-then-emit(:393 await → :410 emit), recorder(persistApproved/persistFallback) |

#### emit / persist 순서 아키텍처 (핵심)

- **persist → emit happens-before 는 보장됨**. user-turn-transcript.ts:393 의
  `await appendSessionTranscriptMessage` 가 끝난 *뒤* :410 에서 emit 한다. 그 append 는
  `src/config/sessions/transcript-append.ts:279-280` 의 write lock + per-path FIFO 큐
  (`withTranscriptAppendQueue`) 안에서 commit 되므로, 소비자가 파일에 아직 없는
  메시지를 emit 으로 먼저 보는 *역전*은 없다. → fire-and-forget persist 결함 아님.
- **emit 자체는 전역 Set 동기 fan-out**. `emitSessionTranscriptUpdate`(:50-56)와
  `emitSessionLifecycleEvent`(:21-27) 둘 다 `for (listener of SET) { try listener()
    catch {/*swallow*/} }`. listener throw 는 의도적으로 swallow -
  transcript-events.test.ts:67-78 ("continues notifying other listeners when one
  throws")가 이를 invariant 로 lock. → swallow 단독은 결함 아님(by-design best-effort).
- **소비자는 비동기 defer**. gateway 의 단일 구독자
  (`src/gateway/server-runtime-subscriptions.ts:94-100`)가
  `void getHandler().then(h => h(evt))` 로 transcript/lifecycle 둘 다 microtask 로
  defer. 핸들러는 lazy `import()` 캐싱. single-process 에서는 `.then` 콜백이 등록
  순서(FIFO)대로 실행되어 동일-emit-source 순서는 보존됨.

#### messageSeq 순서 토큰 분포 (FIND-001 근거)

- `SessionTranscriptUpdate.messageSeq` 는 *optional*. transcript-events.ts:38 에서
  `asPositiveSafeInteger` 로 검증만 하고 producer 강제 없음.
- emit 호출부(9곳) 중 **messageSeq 를 싣는 곳은 단 하나**:
  `src/agents/session-tool-result-guard.ts:782`(조건부). 나머지(user-turn :410,
  config/sessions/transcript.ts:342, attempt-execution.ts:294, compaction-hooks.ts:91,
  tool-result-truncation.ts, transcript-rewrite.ts:402, session-transcript-files.fs.ts:141,
  chat-transcript-inject.ts:119)는 seq 없이 emit.
- 권위 seq(=nonSessionEntryCount, 메시지 수)는 append 가 lock 안에서 *계산*하지만
  (`transcript-append.ts:326`) 반환 타입 `AppendSessionTranscriptMessageResult`(:240)가
  `{messageId, message, appended}` 만 surface 해 *버린다*. → emit 이 seq 를 못 싣는
  구조적 원인.
- 소비자 fallback: `session-history-state.ts:appendInlineMessage`(:245-253)는 seq 부재
  시 측면 카운터 `rawTranscriptSeq += 1`, 존재 시 carriedSeq 채택(:247-250).
  `server-session-events.ts:124-130`은 seq 부재 시 `readSessionMessageCountAsync` 파일
  재계산. 한 세션에 seq-bearing/seq-less producer 가 혼용되면 측면 카운터의 단조성
  가정이 깨진다.

#### lifecycle 이벤트 순서 토큰 (FIND-002 근거)

- `SessionLifecycleEvent`(:1-7)에 seq/version/timestamp 전무. `reason`("create"/
  "ended"/"spawn-failed"/"completed"/"deleted"/...)은 순서 토큰이 아니라 라벨.
- create emit: `subagent-spawn.ts:1350`. ended 류 emit:
  `subagent-registry-lifecycle.ts:1106`. 서로 다른 async 컨텍스트.
- 인과 역전 시 영향이 제한적인 이유: 소비자 핸들러
  (`server-session-events.ts:180-204`)가 `loadGatewaySessionRow`(:195)로 snapshot 을
  fresh 재조회 → 권위 상태는 항상 current. `reason` 라벨만 인과 역전(transient UI).

#### 적용 카테고리 (agents/ordering-causality-auditor.md §탐지 카테고리)

- [x] A. fire-and-forget 영속 + 직렬화 부재 - skip
  - 사유: persist 는 await + write lock + FIFO 큐로 직렬화됨(transcript-append.ts:279).
    `void persistX` 패턴 sessions 스코프에 없음(`handlePersistenceError` 의 `void
    import().then()` 은 로깅이지 영속 아님). → persist 측 ordering 결함 없음.
- [x] B. 무조건 상태 갱신 / terminal reactivate - skip (직접 결함 아님 / FIND-002 부분)
  - 사유: emit 들은 알림 채널일 뿐 세션 상태를 직접 mutate 하지 않음
    (`rg terminal|reactivat|status.*running src/sessions` → none_found). 실제 상태
    mutate 는 gateway/store(스코프 밖). lifecycle 의 무조건 fan-out 은 FIND-002 로
    기록하되 영향이 reason 라벨로 국한(snapshot fresh).
- [x] C. persist/emit 순서 - applied (FIND-001)
  - persist→emit happens-before 는 보장되나 emit payload 가 순서 토큰(messageSeq)을
    누락 → 소비자 재추정 fragility. FIND-001.
- [x] D. deferred async handler 적용 순서 - applied (FIND-002 맥락)
  - server-runtime-subscriptions.ts:94-100 의 `void getHandler().then()` defer. 동일
    source FIFO 보존되나 lifecycle 의 토큰 부재가 cross-source 역전 거부를 불가능하게
    함. FIND-002.
- [x] E. in-flight 키 비대칭 - skip
  - 사유: sessions 스코프에 in-flight 추적 Set(`inFlight|recoveriesInFlight`) 없음
    (`rg inFlight|InFlight src/sessions` → 0건). 해당 패턴은 diagnostic-recovery 셀 소관.

#### 발견 요약

| FIND | severity | symptom | 핵심 |
|---|---|---|---|
| FIND-session-events-ordering-causality-001 | P2 | ordering-causality-gap | inline transcript emit(user-turn-transcript.ts:410)이 messageSeq(on-disk 위치) 누락 → 소비자 측면 카운터 재추정 fragility. seq-bearing/seq-less producer 혼용 시 shouldRefresh 폭발 또는 오정렬. 소비자 fallback 덕에 즉시 깨지진 않음(P2). |
| FIND-session-events-ordering-causality-002 | P3 | ordering-causality-gap | lifecycle 이벤트(session-lifecycle-events.ts:20-28)에 seq/version 부재 + 무조건 fan-out → 지연 reason:"create" 가 ended 세션에 broadcast. 단 핸들러 snapshot fresh 재조회로 영향이 reason 라벨에 국한(P3). |

방어로 인해 FIND 안 된 것:
- listener throw swallow(transcript-events.ts:53, session-lifecycle-events.ts:24): 의도된
  best-effort, 테스트로 lock(transcript-events.test.ts:67).
- persist→emit 역전: write lock + FIFO 로 happens-before 보장.
- 전역 Set 누적(memory-leak 가능성): onX 가 unsubscribe closure 반환하고 gateway
  구독자가 1개(server-runtime-subscriptions.ts) + plugins/SSE 구독자가 unsubscribe
  관리(sessions-history-http.ts:309 unsubscribe). 무제한 누적 증거 없음 → 이 셀(ordering)
  스코프 밖. memory 셀이 본다면 cross_refs 권장.

### clusterer (2026-05-29)

- CAND-051 (epic): 공통 원인 "session 이벤트 emit payload 의 단조 순서 토큰(messageSeq /
  seq / version) 부재 → 소비자가 인과 순서를 권위적으로 복원 불가" 로 2 FIND 묶음.
  - FIND-001 root_cause_chain[0]: "emit payload 에 messageSeq 가 없어 권위 있는 on-disk
    위치를 전달받지 못한다" (user-turn-transcript.ts:410)
  - FIND-002 root_cause_chain[0]: "이벤트 payload 에 단조 seq/version 토큰이 전혀 없다"
    (session-lifecycle-events.ts:1)
  - 두 emit 경로 모두 happens-before(persist→emit)는 보장(FIND-001 write lock+FIFO /
    FIND-002 핸들러 fresh snapshot 재조회)이라, 결함은 순서 역전이 아니라 토큰 손실로 인한
    소비자 재추정/거부불능 fragility 라는 같은 성격 → epic. severity 최고값 P2 상속.
