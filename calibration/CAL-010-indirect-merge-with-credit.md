# CAL-010: 메인테이너가 우월한 fix 로 직접 commit + 우리 PR closed + changelog credit (indirect-merge 패턴)

**날짜**: 2026-04-26 (close) / 2026-04-27 (회고)
**PR**: #70142 (CAND-023 → SOL-0006, gateway chat.send attachment race)
**관련 issue**: #70139
**메인테이너 commit**: `8bc4d4bcd4` (steipete, "fix: prevent duplicate chat attachment send races")
**자동 sweeper close**: `clawsweeper` 봇이 2026-04-26T12:20Z 에 "Closing this as implemented after Codex automated review" 로 close
**결과**: 직접 merge 는 아니지만 우리 issue/PR 가 메인테이너의 직접 fix 를 유도, changelog 에 `Thanks @Feelw00` credit, **사실상 win**

## 패턴 정의

기존 분류와의 차이:

| 패턴 | 설명 | 사례 | outcome |
|---|---|---|---|
| direct-merge | 우리 PR 이 그대로 merge | #68842 (CAND-014) | merge + credit |
| upstream-superseded (CAL-004) | upstream 이 우리보다 먼저 동일 fix merge — 우리 self-close | #68531 (CAND-005) | self-close, credit 없음 |
| third-party-dup (CAL-008) | 우리 PR 발행 **전** 다른 기여자 PR open — gatekeeper 가 catch → CAND abandon | #68801 / CAND-016 | abandon, no PR |
| **indirect-merge (CAL-010, 신규)** | 우리 PR 발행 **후** 메인테이너가 직접 우월한 fix 로 main commit → 우리 PR closed + credit | **#70142 / CAND-023** | **close + credit (사실상 win)** |
| reject-after-merge (CAL-001) | merge 됐다가 메인테이너 post-merge reject + revert | #68489 (CAND-004) | revert, 신뢰 손상 |

## 두 fix 의 구조 비교

### 우리 PR #70142 (head `39ccb9c4a2`) — chat.ts L2235 부근

**위치**: post-attachment-parse (두 await 이후)
**diff**: 174 prod / 173 test, deleteMediaBuffer import + cleanup 루프 포함

```ts
const duringParseWinner = context.chatAbortControllers.get(clientRunId);
if (duringParseWinner) {
  if (offloadedRefs.length > 0) {
    for (const ref of offloadedRefs) {
      try { await deleteMediaBuffer(ref.id); }
      catch (e) { logGateway.warn(...); }
    }
  }
  respond(true, { runId, status: "in_flight" }, ...);
  return;
}
```

**접근 방식**: race window 존속을 인정 + post-parse 시점에 보정. parseMessageWithAttachments 가 디스크에 쓴 offloaded media 를 cleanup 까지 책임짐.

### 메인테이너 fix `8bc4d4bcd4` — chat.ts L1854 부근

**위치**: pre-attachment-parse (registerChatAbortController 직후)
**diff**: 7 prod / 136 test = 144 lines total

```ts
if (!activeRunAbort.registered) {
  respond(true, { runId, status: "in_flight" }, ...);
  return;
}
```

**접근 방식**: `registerChatAbortController` 의 `.registered` 반환 플래그 활용. atomic register 가 이미 fail 한 경우 attachment parse 자체를 건너뜀 → race window 0 + offloaded media 가 만들어지지도 않음.

## 왜 메인테이너 fix 가 우월한가

| 축 | 우리 PR | main fix |
|---|---|---|
| race window | 존속, 사후 보정 | 제거 (atomic) |
| parse 부작용 | 발생 후 cleanup | 발생하지 않음 |
| import 추가 | deleteMediaBuffer | 없음 |
| LOC | 36 줄 (cleanup 루프 포함) | 7 줄 |
| 새 helper / sibling 영향 | 없음 | 없음 |
| 미래 유지보수 부담 | cleanup 누락 시 leak 재발 | 없음 |

**핵심 차이**: 우리는 `registerChatAbortController` 가 `.registered` 반환한다는 사실을 활용 못 함. `.registered: false` 면 map 에 set 하지 않는다는 contract 가 이미 있는데, 우리는 그 다음 `.set` 으로 덮어쓰는 흐름을 그대로 둔 채 post-parse 에서 다시 `.get` 으로 보정. **atomic helper 의 반환 contract 를 먼저 살피는 습관 부재**.

`src/gateway/chat-abort.ts` (이미 존재):
```ts
if (!params.sessionKey || params.chatAbortControllers.has(params.runId)) {
  // 등록 실패 → registered: false 반환
}
```

## 우리 PR 가 가져온 가치

직접 merge 는 아니지만 다음 4가지 가치 발생:

1. **issue identification**: #70139 으로 race 조건을 정확히 documented (idempotencyKey contract violation, user-abort misdirection 등 4 harm)
2. **메인테이너 trigger**: 우리 PR 본문 + 17-agent cross-review 결과 + Codex 1라운드 evidence 가 메인테이너에게 race 실재성을 설득. issue 작성만으로는 안 됐을 가능성
3. **regression test 확립**: 우리 PR 의 test scenario (duplicate attachment send, in_flight assertion) 가 메인테이너 commit `8bc4d4bcd4` 의 `gateway-server-chat-b.test.ts:138` 으로 거의 그대로 반영
4. **changelog credit**: `Gateway/chat: keep duplicate attachment-backed chat.send retries with the same idempotency key on the documented in-flight path so aborts still target the real active run. Fixes #70139. Thanks @Feelw00.`

## 잔여 follow-up: offloaded-media cleanup

clawsweeper 가 명시적으로 언급:
> "If desired, open or request a small follow-up focused only on offloaded-media cleanup for losing duplicate attachment sends."

**그러나 main 의 새 흐름에서는 거의 의미 없음** — pre-parse 거름 → media 가 디스크에 쓰이지도 않음. 우리가 추가한 cleanup 루프는 main 의 새 fix 하에서는 dead code. 다른 시나리오 (parse 실패, multi-attachment partial fail) 가 별도 leak 경로인지는 별도 audit 영역. **우선순위 낮음**.

## 교훈

### 1. atomic helper 의 반환 contract 를 먼저 살펴라

후속 race fix 를 설계할 때 다음 순서:

1. 관련 helper 함수의 signature 와 반환 contract 확인 (특히 success/fail 플래그)
2. helper 가 이미 atomic guarantee 를 제공한다면 그 반환값 활용 → fix 가 7 줄
3. 그게 안 되면 비로소 외부 보정 (post-await re-check, cleanup 루프 등) 검토

이번 케이스에서는 `registerChatAbortController.registered` 라는 1차 단서를 cross-review (17 agent!) 도 catch 못 함. **모든 agent 가 race window post-parse 보정만을 검토하고, register helper 의 contract refactor 가능성은 탐색 영역 밖**. cross-review 프롬프트에 "기존 helper 반환 contract 활용 가능성" 카테고리 추가 검토.

### 2. indirect-merge 는 합법적 outcome — abandon 으로 분류 금지

이 패턴은 CAL-001 (post-merge reject) 과는 정반대. 메인테이너 신뢰도/관계 측면에서 **긍정 신호**:
- 우리 issue 가 진짜 race 라고 메인테이너가 확인
- changelog credit 명시 → 다음 PR 의 수용성에 + 효과
- 우리 fix 의 핵심 의도 (in_flight 반환 + idempotency key contract 보호) 가 그대로 반영됨

이런 케이스는 metrics 에 별도 카테고리 (`metrics/indirect-merge.jsonl`) 로 카운트. 단순 abandon 과 같이 묶으면 파이프라인 시그널 왜곡.

### 3. clawsweeper 자동 close 의 신뢰성

clawsweeper 봇은 Codex automated review 를 사용해 PR vs main 의 fix 위치를 정밀 비교 후 close 결정. 이 케이스에서는 정확히 동작:
- main commit `8bc4d4bcd4` 에 fix 와 regression test 둘 다 반영됨을 evidence 로 인용
- 우리 PR 의 잔여 가치 (offloaded-media cleanup) 를 narrow follow-up 으로 분리 제안
- 우리 credit 도 명시 (`Thanks @Feelw00`)

자동 close 라고 무시하지 말 것 — Codex evidence + commit hash 인용을 정밀하게 검증하면 수동 close 와 동질의 정보 제공.

## 재실수 방지 체크리스트

PR 발행 전:

- [ ] race fix 설계 시 관련 helper 의 반환 contract 확인 (특히 `.registered`, `.added`, `.replaced` 같은 atomic 플래그)
- [ ] cross-review 프롬프트에 "기존 helper 반환 contract 활용 가능성" 카테고리 포함
- [ ] post-await 보정형 fix 와 atomic-helper 활용형 fix 둘 다 검토 후 LOC + 부작용 비교

PR 발행 후 메인테이너 직접 commit 발견 시:

- [ ] 메인테이너 commit hash + diff 비교 → 우리 fix 의 어느 부분이 반영됐는가 분석
- [ ] changelog credit 확인
- [ ] 우리 PR 의 잔여 부분 (이번 경우 offloaded-media cleanup) 이 main 흐름에서 여전히 의미 있는지 평가
- [ ] CAL 회고 + tracker "종결된 PR" 으로 이동
- [ ] **abandon 이 아닌 indirect-merge 로 분류** — 파이프라인 시그널 왜곡 방지
