---
candidate_id: CAND-013
type: single
finding_ids:
  - FIND-auto-reply-concurrency-002
cluster_rationale: |
  단독 FIND. FIND-001 과 cell/file 공유하지만 root cause axis 가 다름.
  FIND-002 는 `isCrossChannel` stale + authorization key 채널 누락 + routing
  chimera 라는 **data dependency / grouping 문제** (FIND-001 은 map ownership
  문제). 세 subcause 가 서로 얽혀있어 하나의 CAND 로 정리.

  FIND-002 root_cause_chain:
    [0] "isCrossChannel 재계산 지점이 없음" (stale snapshot)
    [1] "resolveFollowupAuthorizationKey 에 channel/accountId/to 없음"
    [2] "resolveOriginRoutingMetadata 의 필드별 독립 find 로 chimera routing"

  세 subcause 는 하나의 symptom (cross-channel mixed batch → wrong-channel
  delivery) 로 수렴하므로 개별 FIND 로 분리하지 않고 하나의 single CAND 로
  유지 (해결책은 세 지점 중 하나 이상을 unconditional guard 로 만들면 차단
  가능 — 이는 SOL 단계에서 판단).
proposed_title: "auto-reply queue: stale isCrossChannel + channel-less auth key cause cross-channel batch misrouting"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-19
---

# auto-reply queue: isCrossChannel stale 로 await 중 유입된 교차채널 item 이 batch 에 병합되어 오라우팅

## 공통 패턴

단일 FIND 기반 single CAND. collect-mode drain 의 batch 경로 (drain.ts:159-221) 는
L159 `isCrossChannel` snapshot 과 L161 `await drainCollectQueueStep(...)` 의
microtask yield 사이에 외부 async caller (webhook 등) 가 `enqueueFollowupRun` 으로
다른 채널 item 을 push 할 수 있는 window 가 존재한다.

## 관련 FIND

- FIND-auto-reply-concurrency-002: isCrossChannel 재계산 부재 + authorization key 에
  channel 미포함 + `resolveOriginRoutingMetadata` 의 field-wise 독립 find 세 축이
  결합하여 cross-channel items 가 하나의 combined prompt 로 잘못 배달되는 race.

## 재현 시나리오 요약

1. Q 에 msg_tg (telegram) 2개 → `isCrossChannel=false`.
2. L161 `await drainCollectQueueStep(...)` 의 microtask yield.
3. 그 틈에 Slack webhook 이 msg_slack 을 Q 에 push.
4. D resume: collectDrainResult="skipped" 로 L185 snapshot 경로 진입.
5. `splitCollectItemsByAuthorization` 가 same sender → 한 그룹으로 묶음.
6. `resolveOriginRoutingMetadata` 의 네 필드 (channel/to/accountId/threadId) 가
   각각 독립적으로 `find` 수행 → chimera routing 생성.
7. Slack 메시지 내용이 포함된 combined prompt 가 telegram chat 에 배달, Slack 쪽은
   응답 미수신.

## 영향

- `impact_hypothesis: wrong-output`
- 같은 sender 가 cross-channel 에 bound 된 setup 에서 silent message loss
- thread id 혼합으로 잘못된 thread 배달 가능
- 재현 window 는 microtask 1 tick ~ 수 ms 로 좁으나 webhook fan-in 환경에서
  non-zero

## 근본 원인 subcauses

| # | 위치 | 문제 |
|---|---|---|
| a | drain.ts:159-161 | isCrossChannel 이 await 이전 snapshot, 재계산 없음 |
| b | drain.ts:68-81 (resolveFollowupAuthorizationKey) | auth key 에 channel/accountId/to 미포함 |
| c | drain.ts:50-60 (resolveOriginRoutingMetadata) | 필드별 독립 find → chimera |

세 subcause 중 하나라도 unconditional guard 가 되면 race 차단. 해결책은 SOL 단계.

## 참고

- upstream 최근 fix `712644f0d9` (splice(0) → splice(0, N)) 는 이 경로를 다루지
  않음. 본 race 는 별개.
- `resolveFollowupAuthorizationKey` 가 channel 을 일부러 제외한 설계 의도 확인
  필요 (동일 사용자의 cross-channel dedupe 가 의도적이라면 c 축만 수정).
