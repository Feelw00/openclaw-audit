---
candidate_id: CAND-051
type: epic
finding_ids:
  - FIND-session-events-ordering-causality-001
  - FIND-session-events-ordering-causality-002
cluster_rationale: |
  공통 근본 원인 (clusterer.md Step 3, cross-file within domain): session 이벤트
  emit payload 에 단조 순서 토큰(messageSeq / seq / version)이 없어, 소비자가
  권위 있는 순서를 받지 못하고 측면 카운터로 재추정하거나 인과 stale 판별을 못 한다.
  두 FIND 가 서로 다른 emit 경로(transcript update vs lifecycle event)지만 동일한
  "emit payload 에 ordering 토큰 부재 → 소비자가 인과 순서를 권위적으로 복원 불가"
  라는 단일 축의 발현이다.

  각 FIND root_cause_chain 인용:
  - FIND-...-001 root_cause_chain[0] ("왜 소비자가 순서를 측면 카운터로 추정하는가"):
    "emit payload 에 messageSeq 가 없어 권위 있는 on-disk 위치를 전달받지 못한다"
    (evidence_ref: src/sessions/user-turn-transcript.ts:410)
  - FIND-...-001 root_cause_chain[3] ("왜 소비자 카운터가 파일과 어긋날 수 있는가"):
    "appendInlineMessage 는 messageSeq 부재 시 자체 +1, 존재 시 carriedSeq 채택 -
    한 세션에 두 정책이 섞이면 단조성 가정이 깨진다"
    (evidence_ref: src/gateway/session-history-state.ts:245)
  - FIND-...-002 root_cause_chain[0] ("왜 소비자가 stale lifecycle 이벤트를 거부할 수
    없는가"): "이벤트 payload 에 단조 seq/version 토큰이 전혀 없다"
    (evidence_ref: src/sessions/session-lifecycle-events.ts:1)
  - FIND-...-002 root_cause_chain[2] ("왜 reason 이 stale 해도 브로드캐스트되는가"):
    "emit 은 무조건(unconditional) listener 호출 - 상태/순서 검사 없음"
    (evidence_ref: src/sessions/session-lifecycle-events.ts:21)

  공통 기준선/반증 공유: 두 FIND 모두 happens-before(persist→emit) 자체는 보장됨을
  counter_evidence 로 인정한다 - FIND-001 은 emit(:410)이 await append(:393) 뒤이고
  write lock + FIFO 로 commit 보장(session-history-state.ts:251-253 fallback 도 존재),
  FIND-002 는 핸들러가 loadGatewaySessionRow 로 snapshot 을 fresh 재조회해 권위 상태가
  current(server-session-events.ts:195). 즉 결함은 순서 *역전*이 아니라 *ordering 토큰
  손실로 인한 소비자 재추정/거부불능 fragility* 라는 같은 성격이다. seq enforcement grep
  (rg seq|generation|version src/sessions)도 두 경로 모두 "producer-side seq enforcement
  none_found" 로 수렴한다.

  epic 으로 묶는 이유: 같은 도메인(session-events)의 두 emit 경로가 같은 결함 클래스
  (emit payload 의 ordering 토큰 부재 → 소비자 인과 복원 불가)를 공유하고, 위반된
  기준선(producer-side 단조 토큰 enforcement 부재)도 동일하다. ordering 토큰 부여라는
  공통 surface 를 다루므로 GH Issue 1건 + 자식 task 가 적합. severity 는 최고값 P2 상속.
  (해결책 자체는 본 CAND 범위 밖.)
proposed_title: "session 이벤트 emit payload 의 단조 순서 토큰 부재 → 소비자 측면 카운터 재추정 fragility + 지연 lifecycle reason 인과 역전"
proposed_severity: P2
existing_issue: null
created_at: 2026-05-29
---

# session-events: emit payload ordering 토큰 부재 → 소비자 인과 복원 불가

## 공통 패턴

session 이벤트의 두 emit 경로가 소비자에게 권위 있는 단조 순서 토큰을 싣지 않아, 소비자가
순서/인과를 측면 카운터로 재추정하거나(transcript) stale 여부를 판별하지 못한다(lifecycle).

- **transcript update emit 의 messageSeq 누락(FIND-001)**: `appendUserTurnTranscriptMessage`
  의 inline 분기(user-turn-transcript.ts:407-417)가 append 완료 후 emit 시 `message`/`messageId`
  만 싣고 `messageSeq`(on-disk 위치)는 생략한다. append 결과 타입
  (transcript-append.ts:240)이 계산된 `nonSessionEntryCount`(:326)를 surface 하지 않기
  때문이다. 소비자 `appendInlineMessage`(session-history-state.ts:245)는 seq 부재 시
  `rawTranscriptSeq += 1` 로 측면 추정하는데, 같은 세션의 seq-bearing producer
  (server-methods/sessions.ts, session-tool-result-guard.ts)와 혼용되면 추정값이 파일
  index 와 어긋나 `shouldRefresh`(전체 재조회) 폭발 또는 오정렬을 부른다.
- **lifecycle event 의 seq/version 부재(FIND-002)**: `SessionLifecycleEvent`
  (session-lifecycle-events.ts:1-7)에 단조 토큰이 없고, `emitSessionLifecycleEvent`(:20-28)는
  전역 Set 에 무조건 동기 fan-out, 소비자는 `void getHandler().then()` 으로 defer
  (server-runtime-subscriptions.ts:98). create/ended 가 서로 다른 async 컨텍스트에서
  emit 되어 wall-clock 역순으로 처리되면, 지연 `reason:"create"` 가 이미 ended 된 세션에
  broadcast 되어도 거부할 가드가 없다.

공통 기준선/반증: 두 경로 모두 persist→emit happens-before 는 보장된다(FIND-001 은
write lock + FIFO, FIND-002 는 핸들러의 fresh snapshot 재조회). 따라서 결함은 순서
*역전*이 아니라 producer-side 단조 토큰 부재로 인한 소비자 재추정/거부불능이다.
producer-side seq enforcement 는 `rg seq|generation|version src/sessions` 에서 발견되지
않으며(transcript-events.ts 의 messageSeq 는 optional pass-through 일 뿐), 이 부재가 두
emit 경로의 공통 surface 다.

## 관련 FIND

- FIND-session-events-ordering-causality-001 (P2): `user-turn-transcript.ts:407-417`
  inline user-turn emit 이 messageSeq 를 누락 → 소비자가 측면 카운터(+1)로 순서 재추정.
  seq-bearing/seq-less producer 혼용 세션에서 추정이 파일 index 와 어긋나 shouldRefresh
  (전체 history 재조회) 폭발 또는 SSE 클라이언트 오정렬/중복 표시. 데이터(파일)는 정확하나
  라이브 뷰 순서가 틀어짐(wrong-output). 소비자 fallback + 서버측 readSessionMessageCountAsync
  재계산 덕에 즉시 깨지진 않아 P2.

- FIND-session-events-ordering-causality-002 (P3): `session-lifecycle-events.ts:20-28`
  lifecycle 이벤트에 seq/version 부재 + 무조건 fan-out + 소비자 비동기 defer. 지연된
  `reason:"create"` 가 ended 세션에 broadcast → sessions.changed SSE 의 reason 필드가
  인과 역전, 클라이언트 UI 가 죽은 세션을 잠깐 활성으로 표시(wrong-output). 핸들러가
  snapshot 을 fresh 재조회(server-session-events.ts:195)해 권위 상태는 정확, 영향이
  transient reason 라벨에 국한되어 P3.
