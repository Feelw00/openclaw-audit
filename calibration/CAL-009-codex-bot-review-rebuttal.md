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

## 추가 사례 (2026-04-22, PR #68669 browser cleanup dedup)

**Codex 재반박 → 번들 실측으로 재반박 성공**. 1라운드 반박 후 Codex 가 `src/agents/subagent-registry.ts:114-115` 의 production wiring 을 fresh evidence 로 제시하며 "dynamic `import()` reject 가 `runBestEffortCleanup` 실행 전에 throw 가능" 이라고 재지적. 5-agent post-harness cross-review 결과:

| agent | 판정 | 핵심 |
|---|---|---|
| positive-advocate | proceed (option c) | sibling 동형 수정 권고 |
| critical-devil | scope-down-to-option-a | Set 중복, 1줄 이동 충분 |
| reproduction-realist | reject-as-synthetic (test ii) | dist 번들에 lazy import 부재, CAL-003 parallel |
| hot-path-tracer | low-impact-abandon | `dist/subagent-registry-Zhtu8A2W.js` inline 확인 |
| upstream-dup-checker | proceed | upstream 중복 없음 |

**결정적 증거**: repro-realist + hot-path-tracer 가 **독립적으로** `dist/subagent-registry-Zhtu8A2W.js` 를 열어 확인 — tsdown/rollup 이 `browser-lifecycle-cleanup.ts` 를 동일 청크에 inline. line 835 에 wrapper 함수 직접 정의, line 2183/2495/2666 에 정적 참조. `browserCleanupPromise` / `loadCleanupBrowserSessionsForLifecycleEnd` / `await import()` identifier 전부 번들에 부재. Codex 지적한 reject 경로가 production runtime 에 존재하지 않음.

**판단**: 코드 변경 없이 반박. reply 에 dist 파일 경로 + 번들 line 번호 인용 → thread resolved.

## 프로토콜 확장 (2라운드 대응)

1. **1라운드 반박 후 bot 재반박** 이 오면 CAL-009 프로토콜 **다시** 실행 (role 확장):
   - 기본 2-agent (positive + critical) 대신 5-agent post-harness 권장
   - **reproduction-realist + hot-path-tracer 를 필수 포함** — bot 이 지적한 경로가 번들/build 이후에도 실재하는지 실측
2. **dist / build artifact 검증**을 증거 축으로 추가:
   - 소스 레벨 경로만 믿지 말고 실제 배포 번들에 해당 코드가 남아있는지 `grep` 으로 확인
   - bundler (tsdown, rollup, webpack, esbuild) 가 inline / tree-shake 할 가능성 상시 고려
   - dist 증거는 source 증거보다 훨씬 강력 — bot 이 source 만 보고 작성한 경우 대부분 무력화됨
3. **내부 판단 편향 경계**:
   - "sibling consistency" 같은 명분은 scope 확장을 정당화하기 쉬움 — 사용자가 편향 경고 보내도 무시하지 말 것
   - 사용자가 "왜 로직을 변경하는지" 물으면 production 효용 0 라면 솔직히 코드 변경 없음으로 회귀
   - 3번째 라운드까지 가지 않도록 2라운드 반박은 반드시 객관 증거 (번들 실측, Grep 결과, 전체 시나리오 통과 로그) 로만 구성

## 재사용 명령 (확장)

```bash
# bundle inline 증거 수집
grep -n "<symbol>" dist/*.js | head -10
grep -c "await import\|browserCleanupPromise" dist/<chunk>.js  # 0 이면 tree-shake 확인

# tsdown / rollup config 확인 (chunk split 변경 시 재검증 필요)
cat tsdown.config.ts 2>/dev/null || cat rollup.config.mjs 2>/dev/null
```
