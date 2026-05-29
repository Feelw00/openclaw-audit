---
id: FIND-session-events-ordering-causality-001
cell: session-events-ordering-causality
title: inline transcript emit 가 on-disk seq 토큰을 누락해 소비자가 측면 카운터로 순서 재추정
file: src/sessions/user-turn-transcript.ts
line_range: 407-417
evidence: "```ts\n  switch (params.updateMode ?? \"inline\") {\n    case \"inline\"\
  :\n      if (appended.appended) {\n        emitSessionTranscriptUpdate({\n     \
  \     sessionFile: params.transcriptPath,\n          ...(params.sessionKey ? { sessionKey:\
  \ params.sessionKey } : {}),\n          message: appended.message,\n          messageId:\
  \ appended.messageId,\n        });\n      }\n      break;\n```\n"
symptom_type: ordering-causality-gap
problem: inline 모드 user-turn emit 가 message/messageId 만 싣고 messageSeq(on-disk 위치)를
  싣지 않는다. SSE/세션 이벤트 소비자는 자기 측 측면 카운터(rawTranscriptSeq += 1)나 별도 파일 재스캔으로 순서를 재추정해야
  하고, 그 추정이 실제 파일 인덱스와 어긋나면 메시지가 stale 로 폐기되거나 중복 정렬된다.
mechanism: '1. append 경로(appendSessionTranscriptMessageLocked)가 nonSessionEntryCount
  로 메시지의 실제 on-disk 위치를 이미 계산한다.

  2. 그러나 AppendSessionTranscriptMessageResult 는 {messageId, message, appended} 만 반환
  - seq 를 버린다.

  3. 본 emit(410)은 그 결과만 가지므로 messageSeq 없이 emit 한다.

  4. 소비자 appendInlineMessage 는 carriedSeq===undefined 분기로 rawTranscriptSeq += 1 (측면
  카운터)을 쓴다.

  5. 같은 세션에 다른 producer(server-methods/sessions.ts, session-tool-result-guard.ts)가
  messageSeq 를 실어 보낸 적이 있으면 carriedSeq <= rawTranscriptSeq 검사(247)에서 본 emit 의 +1 추정이
  실제 파일 index 와 어긋나 shouldRefresh(전체 재조회) 또는 잘못된 seq 부여로 귀결된다.

  '
root_cause_chain:
- why: 왜 소비자가 순서를 측면 카운터로 추정하는가
  because: emit payload 에 messageSeq 가 없어 권위 있는 on-disk 위치를 전달받지 못한다
  evidence_ref: src/sessions/user-turn-transcript.ts:410
- why: 왜 emit 이 seq 를 못 싣는가
  because: append 결과 타입이 seq 를 반환하지 않는다 - 계산은 하나 버린다
  evidence_ref: src/config/sessions/transcript-append.ts:240
- why: 왜 append 가 위치를 계산하고도 버리는가
  because: nonSessionEntryCount(=메시지 수)는 leafInfo 추출용으로만 쓰이고 result 로 surface 되지 않는다
  evidence_ref: src/config/sessions/transcript-append.ts:326
- why: 왜 소비자 카운터가 파일과 어긋날 수 있는가
  because: appendInlineMessage 는 messageSeq 부재 시 자체 +1, 존재 시 carriedSeq 채택 - 한 세션에
    두 정책이 섞이면 단조성 가정이 깨진다
  evidence_ref: src/gateway/session-history-state.ts:245
impact_hypothesis: wrong-output
impact_detail: '정성: inline user-turn 은 모든 대화 턴마다 발생하는 hot-path. 측면 카운터 추정이 어긋나면 (a)
  신규 메시지가 carriedSeq<=rawTranscriptSeq 로 판정돼 shouldRefresh(전체 history 재조회) 비용 발생,
  또는 (b) 잘못된 seq 가 부여돼 SSE 클라이언트가 메시지를 잘못 정렬/중복 표시. 데이터 자체는 안 잃지만(파일은 정확) 라이브 뷰 순서가
  틀어진다. 빈도: 한 세션이 seq-bearing producer 와 seq-less producer 를 혼용할 때만 - 즉 user turn
  + tool-result-guard 가 같은 세션에 공존하는 일반 에이전트 세션.'
severity: P2
counter_evidence:
  path: src/gateway/session-history-state.ts
  line: 251-253
  reason: 'partial-mitigation: 소비자에 측면 단조 카운터(rawTranscriptSeq += 1) fallback 이 있어
    seq 부재 자체로 즉시 깨지진 않음. 또 server-session-events.ts:124-130 은 seq 부재 시 readSessionMessageCountAsync
    로 파일에서 재계산. 즉 happens-before(persist→emit) 자체는 await(393) 후 emit(410) 이라 보장됨(emit
    시점 파일은 이미 commit). 결함은 순서 역전이 아니라 *ordering 토큰 손실로 인한 소비자 재추정 fragility*. seq/generation
    단조 가드 grep(rg seq|generation|version src/sessions): transcript-events.ts 의 messageSeq
    는 optional pass-through 일 뿐 producer 강제 없음(none_found: producer-side seq enforcement).'
status: discovered
discovered_by: ordering-causality-auditor
discovered_at: '2026-05-29'
---
# inline transcript emit 가 on-disk seq 토큰을 누락해 소비자가 측면 카운터로 순서 재추정

## 문제

`appendUserTurnTranscriptMessage` 의 inline 분기(407-417)는 append 가 완료된 뒤
`emitSessionTranscriptUpdate` 로 `sessionFile`, `sessionKey`, `message`, `messageId`
는 전달하지만 `messageSeq`(메시지의 실제 on-disk 위치)는 전달하지 않는다.
`SessionTranscriptUpdate` 타입은 `messageSeq?: number` 를 *지원*하나(optional),
이 producer 는 채우지 않는다.

결과적으로 라이브 SSE/세션 이벤트 소비자는 권위 있는 순서 토큰 없이 메시지를
받으며, 자기 측 측면 카운터로 순서를 *재추정* 해야 한다. 추정이 파일의 실제
인덱스와 어긋나면 메시지가 stale 로 판정되어 전체 재조회를 유발하거나 잘못된
seq 로 정렬된다.

## 발현 메커니즘

1. `appendSessionTranscriptMessageLocked`(transcript-append.ts:300)가 write lock
   안에서 `readTranscriptLeafInfo` 로 `nonSessionEntryCount`(현재 메시지 수 =
   자연 seq)를 계산한다(:326).
2. 그러나 반환 타입 `AppendSessionTranscriptMessageResult`(:240)는
   `{ messageId, message, appended }` 뿐 - 계산한 위치를 버린다.
3. 본 emit(:410)은 이 result 만 손에 쥐므로 `messageSeq` 없이 emit 한다.
4. 소비자 `appendInlineMessage`(session-history-state.ts:245)는 `carriedSeq ===
   undefined` 분기를 타 `this.rawTranscriptSeq += 1` 로 측면 추정한다(:252).
5. 그런데 같은 세션의 *다른* producer(server-methods/sessions.ts:912,
   session-tool-result-guard.ts:782)는 `messageSeq` 를 실어 보낸다. 그 emit 이
   먼저 도착해 `rawTranscriptSeq` 를 carriedSeq 로 점프시킨 뒤(:250) 본 seq-less
   emit 이 도착하면, `+1` 추정값이 실제 파일 index 와 어긋난다 →
   `carriedSeq <= rawTranscriptSeq`(:247) 류 판정에서 `shouldRefresh` 폭발 또는
   잘못된 seq 부여.

## 근본 원인 분석

- **1단계 (emit 토큰 손실)**: emit 호출(:410)이 `messageSeq` 인자를 생략한다.
  `appended` 에 seq 가 없기 때문이다.
- **2단계 (반환 타입 누락)**: `appendSessionTranscriptMessage` 의 결과 타입
  (transcript-append.ts:240)이 seq/index 필드를 갖지 않는다. 계측 지점이 잘못된
  것이 아니라 *데이터 통로*가 없다.
- **3단계 (계산-후-폐기)**: append 는 lock 안에서 `nonSessionEntryCount` 로
  위치를 이미 안다(:326). 단조 seq 의 ground truth 가 거기 있는데 result 로
  올리지 않는다.
- **4단계 (혼합 정책 fragility)**: 소비자가 seq 부재/존재를 둘 다 처리하느라
  단조성 가정이 producer 혼용 시 깨진다(session-history-state.ts:245-253).

## 영향

`impact_hypothesis: wrong-output`. inline user-turn 은 *모든* 대화 턴마다 도는
hot-path 다. 파일 자체는 lock + FIFO 로 정확히 직렬화되므로 데이터 유실은
없다(그래서 P0/data-loss 아님). 그러나 라이브 뷰(SSE) 에서:

- (a) seq-bearing producer 와 seq-less producer 가 한 세션에 공존하면, seq-less
  emit 의 `+1` 추정이 carriedSeq 점프와 충돌해 `shouldRefresh` 가 빈발 →
  전체 history 재조회(refreshAsync) 비용.
- (b) 또는 잘못된 seq 가 부여돼 클라이언트가 메시지를 잘못 정렬/중복 표시.

재현 시나리오: 에이전트 세션에서 user turn(seq-less, 본 경로) 직후
tool-result-guard 가 seq 를 실어 emit(session-tool-result-guard.ts:782) → SSE
소비자의 `rawTranscriptSeq` 가 후자에 의해 점프 → 다음 user turn 의 `+1` 추정이
어긋남.

## 반증 탐색

- **소비자 측 fallback (partial-mitigation)**: `appendInlineMessage` 는 seq
  부재 시 측면 카운터(+1)로 동작하고(session-history-state.ts:251-253),
  `server-session-events.ts`(:124-130)는 `readSessionMessageCountAsync` 로 파일
  재계산. 그래서 seq 부재 *단독*으로 즉시 깨지진 않음 - 이것이 P2 로 낮춘 이유.
- **happens-before(persist→emit) 보장됨**: emit(:410)은 `await
  appendSessionTranscriptMessage`(:393) *뒤*에 실행되고, 그 append 는 write lock
  + FIFO 큐(transcript-append.ts:279-280) 안에서 commit 된다. 따라서 소비자가
  파일에 없는 메시지를 emit 으로 먼저 보는 역전은 없음. 본 결함은 순서 *역전*이
  아니라 *순서 토큰 손실에 의한 재추정 fragility*.
- **seq/generation 가드 grep**: `rg -n "seq|generation|version|monotonic"
    src/sessions/` → transcript-events.ts 의 messageSeq 는 optional pass-through
  검증(asPositiveSafeInteger)일 뿐 producer 가 채우도록 강제하는 곳 없음
  (none_found: producer-side seq enforcement).
- **기존 테스트**: transcript-events.test.ts:23-42 는 messageSeq 가 *전달되면*
  pass-through 됨을 확인하나, user-turn-transcript.test.ts:260-286(inline 기본
  모드)은 emit payload 의 seq 부재를 검증하지 않음 - 의도된 누락인지 미확인.

## Self-check

### 내가 확실한 근거
- emit(:410)이 messageSeq 를 안 싣는다는 점은 직접 Read 로 확인.
- append result 타입(transcript-append.ts:240)에 seq 필드 없음 직접 확인.
- 소비자 fallback 카운터(session-history-state.ts:245-253)와 server-side
  재계산(server-session-events.ts:124-130) 직접 확인.

### 내가 한 가정
- 한 세션에 seq-bearing(tool-result-guard) producer 와 seq-less(user-turn)
  producer 가 실제 공존한다 - emit 호출부 분포(rg)로 뒷받침하나, 동일 SSE 스트림
  생애 안에서 둘이 교차 도착하는 정확한 production 타임라인은 trace 안 함.
- `shouldRefresh` 빈발이 체감 비용이라는 가정 - 정량 측정 안 함.

### 확인 안 한 것 중 영향 가능성
- `readSessionMessageCountAsync`(서버 측 재계산)가 매번 정확한 index 를 주면
  server.session-events 경로는 실질 무해할 수 있음. SSE history-state 경로
  (session-history-state.ts)만 측면 카운터 의존 - 이 둘의 상대 사용 빈도 미확인.
- limit/cursor 가 설정된 페이지네이션 모드에서는 appendInlineMessage 가 일찍
  return(null)하므로(session-history-state.ts:242) 영향 범위가 inline-full 모드로
  한정될 가능성.
