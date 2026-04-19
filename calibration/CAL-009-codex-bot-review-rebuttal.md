# CAL-009: Codex bot P2 지적 — 병렬 에이전트로 검증 후 sibling consistency 근거로 반박

**날짜**: 2026-04-19
**PR**: #68842 (CAND-014 → SOL-0004, costUsageCache FIFO eviction)
**결과**: 코드 수정 없이 공손한 반박 코멘트 작성 + review thread resolve

## Codex 지적 원문

> **Avoid evicting in-flight cost usage entries** (P2)
> The new FIFO eviction in `setCostUsageCache` deletes the oldest key without checking whether that entry is still `inFlight`. When more than 256 distinct ranges are requested before earlier loads resolve, an in-flight key can be evicted; a follow-up request for that same range then misses `cached?.inFlight` and starts a second `loadCostUsageSummary` call, defeating deduplication and increasing expensive usage scans under high-cardinality or abusive traffic.
>
> path: src/gateway/server-methods/usage.ts:76

## 병렬 에이전트 검증 결과

| 역할 | 권고 | 핵심 논리 |
|---|---|---|
| positive-advocate | debate-but-reflect @ medium | race 실재 (코드상 가능), 수정 XS (5-8줄), P2 응답 안 하면 수용성 저하 |
| critical-devil | defend-skip @ high | 256 동시 in-flight 실제 trigger 불가 (macOS 단일 range + UI bounded), sibling 3건 동일 unconditional-FIFO — divergent fix 는 narrative 파괴, evict 된 promise self-contained → rare duplicate scan 뿐 (P3) |

## 판단

**반박 + 코멘트 작성**. 핵심 근거 3가지:

1. **Sibling consistency**: 3개 sibling cache (`resolvedSessionKeyByRunId`, `TRANSCRIPT_SESSION_KEY_CACHE`, `sessionTitleFieldsCache`) 모두 동일 unconditional FIFO. 이 PR 만 divergent 하게 in-flight skip 넣으면 "sibling 과 동일 패턴" narrative 깨짐. 일관된 fix 는 4 cache 전체에 적용하는 별도 PR scope.
2. **실제 caller rate**: macOS menu poll (45s 단일 range) + Control UI (bounded state) — 256 distinct in-flight 경로 부재. abusive client 가 필요한 synthetic scenario.
3. **영향 경미**: evict 된 promise 는 closure 내 self-contained → rare duplicate disk scan 뿐. P3 수준, P2 아님.

## 프로토콜: bot review 대응

1. bot 지적을 **병렬 에이전트로 검증** (positive + critical 최소 2 agent)
2. race/leak 주장의 **production trigger 경로** 확인
3. sibling/인접 코드와의 **consistency** 체크
4. 반박 가능하면:
   - 공손한 reply (사과 없이 근거 나열)
   - follow-up PR 가능성 언급 ("broader change" 로 열어두기)
   - review thread resolve (GraphQL `resolveReviewThread`)
5. 반영 필요하면:
   - 추가 commit + push
   - thread resolve 는 재리뷰 후

## 재사용 명령

```bash
# 1. bot comment 조회
gh api repos/<owner>/<repo>/pulls/<N>/comments

# 2. 특정 comment 에 reply
gh api -X POST repos/<owner>/<repo>/pulls/<N>/comments/<COMMENT_ID>/replies -f body="..."

# 3. review thread id 찾기
gh api graphql -f query='query { repository(owner:"owner", name:"repo") { pullRequest(number: N) { reviewThreads(first: 10) { nodes { id isResolved comments(first: 1) { nodes { databaseId } } } } } } }'

# 4. resolve
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<ID>"}) { thread { id isResolved } } }'
```

## CAL-003 재발 방지 확인

Codex 지적을 그대로 반영했다면 "synthetic race 방어 추가" → CAL-003 패턴 재발. 병렬 에이전트로 trigger 경로 실재성 확인 후 반박 결정은 CAL-003 교훈과 일치.
