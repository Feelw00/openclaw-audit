---
candidate_id: CAND-006
type: single
finding_ids:
  - FIND-infra-process-error-boundary-003
cluster_rationale: |
  단일 FIND 로 구성. emitGatewayRestart 의 try 블록에서 process.emit /
  process.kill 이 throw 할 때 catch 블록이 emittedRestartToken 만
  consumedRestartToken 값으로 복원할 뿐, 같은 함수가 앞서 증가시킨
  sigusr1AuthorizedCount 는 감소시키지 않는다. 결과: 최대
  SIGUSR1_AUTH_GRACE_MS (5000ms) 동안 "인가된 restart" 카운터 1 장이
  외부 SIGUSR1 에 소비될 수 있는 authorization leak.

  CAND-005 (plugin register 부분 등록) 와 "catch 블록의 부분 상태 롤백"
  상위 패턴을 공유하나, 아래 이유로 별도 CAND 로 분리:
    - 다른 도메인 (infra-process vs plugins)
    - 다른 자료구조 (카운터 vs 배열 필드 집합)
    - 다른 실패 경로 (process.kill EPERM vs 플러그인 내부 throw)
    - domain-notes/infra-process.md 의 에러 경계 매핑 테이블과
      domain-notes/plugins.md 의 대칭 결여 테이블은 각각 자기 도메인
      안에서 독립 관찰로 기술됨.
  clusterer.md Step 3 의 "같은 인프라 축 / 같은 lock / 같은 error
  boundary" 기준 미충족 → epic 불가, single 처리.

  FIND 내부 근거 인용:
  - root_cause_chain[0]: "catch 주석 (line 134) 이 '미래 restart 요청
    가능' 만 의도. authorization 잔존이 초래하는 side-effect 는
    고려 안 됨" (src/infra/restart.ts:133-136).
  - root_cause_chain[1]: "authorize 함수는 count 증가 및 만료 시간 연장만
    수행하고, 실패를 되돌릴 'deauthorize' 공개 API 가 없다. catch 에서
    호출할 대칭 함수 부재" (src/infra/restart.ts:161-180).
  - domain-notes/infra-process.md 관찰 3: "restartCycleToken 설계는
    emit/consume 쌍으로 cycle 을 구분하지만 부분 실패 시 롤백이 비대칭".
proposed_title: "emitGatewayRestart catch — sigusr1AuthorizedCount deauthorize 누락"
proposed_severity: P2
existing_issue: null
created_at: 2026-04-18
---

# emitGatewayRestart catch — sigusr1AuthorizedCount deauthorize 누락

## 공통 패턴

`src/infra/restart.ts:116-140` `emitGatewayRestart()` 는 다음 순서로 상태를
변경한다.

```
restartCycleToken++
emittedRestartToken = cycleToken
authorizeGatewaySigusr1Restart()     // sigusr1AuthorizedCount += 1,
                                     //  sigusr1AuthorizedUntil = now + 5000
try {
  process.emit("SIGUSR1") or process.kill(pid, "SIGUSR1")
} catch {
  emittedRestartToken = consumedRestartToken   // cycle 만 롤백
  return false
  // sigusr1AuthorizedCount, sigusr1AuthorizedUntil 은 그대로 남음
}
```

catch 블록이 cycle token 만 되돌리고 authorization 카운터는 그대로 두기
때문에, emit 실패 후 최대 5 초간 "인가된 restart" 1장이 잔존한다. 이
window 내부에 외부 `kill -USR1 <pid>` 가 도달 +
`consumeGatewaySigusr1RestartAuthorization()` 경로를 타면
`setGatewaySigusr1RestartPolicy({allowExternal: false})` 정책을 우회한
재시작 1회가 "authorized" 로 처리된다.

방어 경로 `resetSigusr1AuthorizationIfExpired` (line 142-151) 는
`sigusr1AuthorizedUntil` 만료 후에만 count 를 0 으로 복구하므로 실패 직후
5초 window 는 보호되지 않는다.

## 관련 FIND

- **FIND-infra-process-error-boundary-003** (P2, error-boundary-gap):
  emitGatewayRestart catch 가 `emittedRestartToken` 만 롤백, 동일 함수 초입
  에서 증가한 `sigusr1AuthorizedCount` 는 감소시키지 않음. 결과: 최대 5 초
  authorization leak → 정책상 차단될 외부 SIGUSR1 1회가 wrongly authorized.
  impact_hypothesis: wrong-output (security-adjacent).
  근거: src/infra/restart.ts:116-140, 161-180, 142-151.

## Cross-refs

- CAND-005 (FIND-plugins-lifecycle-001): "try 블록에서 변경된 N 개 상태 중
  일부만 catch 에서 복구" 상위 패턴 공유. 해결 축은 독립 (카운터 감소
  함수 도입 vs registry 배열 snapshot/restore).
