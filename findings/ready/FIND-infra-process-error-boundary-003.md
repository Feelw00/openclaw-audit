---
id: FIND-infra-process-error-boundary-003
cell: infra-process-error-boundary
title: emitGatewayRestart catch 가 emittedRestartToken 만 롤백 — sigusr1AuthorizedCount
  누수
file: src/infra/restart.ts
line_range: 116-140
evidence: "```ts\nexport function emitGatewayRestart(): boolean {\n  if (hasUnconsumedRestartSignal())\
  \ {\n    clearActiveDeferralPolls();\n    clearPendingScheduledRestart();\n    return\
  \ false;\n  }\n  clearActiveDeferralPolls();\n  clearPendingScheduledRestart();\n\
  \  const cycleToken = ++restartCycleToken;\n  emittedRestartToken = cycleToken;\n\
  \  authorizeGatewaySigusr1Restart();\n  try {\n    if (process.listenerCount(\"\
  SIGUSR1\") > 0) {\n      process.emit(\"SIGUSR1\");\n    } else {\n      process.kill(process.pid,\
  \ \"SIGUSR1\");\n    }\n  } catch {\n    // Roll back the cycle marker so future\
  \ restart requests can still proceed.\n    emittedRestartToken = consumedRestartToken;\n\
  \    return false;\n  }\n  lastRestartEmittedAt = Date.now();\n  return true;\n\
  }\n```\n"
symptom_type: error-boundary-gap
problem: '`emitGatewayRestart` 의 catch 블록이 `emittedRestartToken` 만 롤백하고

  `authorizeGatewaySigusr1Restart()` 에 의해 증가된 `sigusr1AuthorizedCount` 는 그대로

  남겨둔다. 결과적으로 SIGUSR1 emit 실패 직후, 아직 authorized 상태의 restart token 이 잔존

  하여 후속 (의도되지 않은) 외부 SIGUSR1 한 건이 authorized 로 인식될 수 있다.

  '
mechanism: "1. emitGatewayRestart 호출 — line 124-126 에서 restartCycleToken/emittedRestartToken\
  \ 증가 및\n   authorizeGatewaySigusr1Restart() 호출 → sigusr1AuthorizedCount += 1.\n\
  2. try 블록에서 process.emit(\"SIGUSR1\") 또는 process.kill(pid, \"SIGUSR1\") 호출.\n3.\
  \ 드문 경우 process.kill 이 EPERM 이나 ESRCH 로 throw (일부 플랫폼/컨테이너 sandbox).\n4. catch 블록:\
  \ emittedRestartToken = consumedRestartToken (line 135) — cycle 롤백 완료.\n   그러나 sigusr1AuthorizedCount\
  \ 는 감소 없음.\n5. 이후 함수 return false.\n6. sigusr1AuthorizedCount 는 SIGUSR1_AUTH_GRACE_MS\
  \ (5000ms) 동안 유효 (sigusr1AuthorizedUntil).\n7. 이 5초 window 안에 외부에서 들어온 'kill -USR1\
  \ <pid>' 가 consumeGatewaySigusr1RestartAuthorization()\n   를 거치면 authorized 로 처리\
  \ → 정책상 차단되어야 할 외부 재시작 1회가 통과.\n"
root_cause_chain:
- why: 왜 catch 가 emittedRestartToken 만 롤백하고 authorization 은 놔두는가?
  because: catch 주석 (line 134) 이 '미래 restart 요청 가능' 만 의도. authorization 잔존이 초래하는 side-effect
    는 고려 안 됨.
  evidence_ref: src/infra/restart.ts:133-136
- why: 왜 authorization 이 별도 카운터인가?
  because: 외부 프로세스로부터 받은 SIGUSR1 이 authorized 였는지를 구별하기 위해 설계 (emit 도중 Node 이벤트 루프
    진입 전 consume 가능해야 함). 정당한 설계이지만 cleanup 보강이 필요.
  evidence_ref: src/infra/restart.ts:161-180
- why: 왜 이 leak 이 테스트에서 안 잡히나?
  because: process.kill 실패 시나리오는 실제 환경에서 재현이 어렵고, __testing.resetSigusr1State() 가
    모든 상태를 일괄 리셋하기 때문에 pair 어긋남이 드러나지 않음.
  evidence_ref: src/infra/restart.ts:510-523
impact_hypothesis: wrong-output
impact_detail: '정성: 외부 악의적 SIGUSR1 이 인가되지 않은 재시작 1회를 유도 가능 (일반적으로는

  setGatewaySigusr1RestartPolicy({allowExternal: false}) 로 차단되지만, authorization 카운터가

  남아있으면 ''내부가 인가했다'' 로 잘못 판정될 수 있음).

  재현 조건: (1) process.emit("SIGUSR1") 또는 process.kill 이 throw 하는 환경 (예: 특정

  containerized sandbox, permission strip 상황), (2) 5초 내 외부 SIGUSR1 도착.

  빈도: 드물지만, 플랫폼 sandbox 전환 (예: 보안 강화된 launchd 프로필) 시 발생 가능.

  '
severity: P2
counter_evidence:
  path: src/infra/restart.ts
  line: 142-151
  reason: 'R-3 Grep 결과:

    - `rg -n "try\s*\{" src/infra/restart.ts` (이 파일에 대한 try 블록) — line 127 의 try 발견,
    catch 는 line 133. 다른 try 는 line 216 (deferGatewayRestartUntilIdle 내부) 로 상이한 경로.

    - `rg -n "process\.on\([''\"](uncaughtException|unhandledRejection)[''\"]" src/`
    → restart.ts 에는 없음 (grep 결과 전체 src 13 hits, restart.ts 무관).

    - `rg -n "AbortController|signal\.abort|AbortSignal" src/infra/abort-signal.ts`
    → helper 2 hits, 본 FIND 와 무관.

    - `rg -n "\.catch\(" src/infra/{unhandled-rejections,process-respawn,restart,abort-signal,approval-handler-runtime}.ts`
    → no matches. 즉 restart.ts 에는 promise catch 기반 방어도 없음.

    방어 경로: resetSigusr1AuthorizationIfExpired (line 142-151) 가 sigusr1AuthorizedUntil
    이 만료된 경우만 count 를 0 으로 복구. 실패 직후 5초 이내에는 ''만료되지 않은 카운터'' 가 유효한 채로 남음 → 방어 경로 ''존재하나
    범위 외''.

    '
status: discovered
discovered_by: error-boundary-auditor
discovered_at: '2026-04-18'
---
# emitGatewayRestart catch 가 emittedRestartToken 만 롤백 — sigusr1AuthorizedCount 누수

## 문제

`emitGatewayRestart` 가 `process.emit` / `process.kill` 호출 중 예외를 catch 할 때, 같은
함수 초입에서 증가시킨 `sigusr1AuthorizedCount` 를 감소시키지 않는다. emit 실패 후 5초간
'인가된 재시작' 1장이 남아있어, 외부에서 도달한 SIGUSR1 이 `consumeGatewaySigusr1RestartAuthorization`
을 거쳐 authorized 로 오판될 수 있다.

## 발현 메커니즘

```
emitGatewayRestart()
  restartCycleToken++
  emittedRestartToken = cycleToken
  authorizeGatewaySigusr1Restart()   → sigusr1AuthorizedCount += 1
                                       sigusr1AuthorizedUntil = now + 5000
  try {
    process.emit("SIGUSR1") or process.kill(pid, "SIGUSR1")
  } catch {
    emittedRestartToken = consumedRestartToken   // cycle 만 롤백
    return false
    // authorization 카운터는 그대로 남음 → 5초간 consume 가능
  }
```

외부 SIGUSR1 이 이 5초 내에 도달 + `consumeGatewaySigusr1RestartAuthorization()` 가 호출되면,
sigusr1AuthorizedCount 가 여전히 > 0 이므로 true 반환 → 정책 외부의 재시작이 authorized 로
처리된다.

## 근본 원인 분석

1. catch 주석 (line 134 "Roll back the cycle marker so future restart requests can still
   proceed.") 은 cycle token 롤백 의도만 언급. authorization 은 '이 함수의 외부 변수' 이므로
   롤백 대상이 아니라고 판단한 흔적.

2. authorize 함수 (line 161-168) 는 count 증가 및 만료 시간 연장만 수행하고, 실패를 되돌릴
   'deauthorize' 공개 API 가 없다. catch 에서 호출할 대칭 함수 부재.

3. __testing.resetSigusr1State (line 510-523) 가 존재하여 모든 상태를 일괄 리셋하는 방식이라
   실패 경로의 부분 누수는 테스트상 감지 안 됨.

## 영향

- impact_hypothesis: wrong-output
- 시나리오: 보안 강화된 sandbox 에서 process.kill(pid, "SIGUSR1") 이 EPERM 으로 throw
  → catch 진입 → authorization 잔존 → 외부 CLI 가 `kill -USR1 <pid>` 로 한 번의 재시작을
  'authorized' 로 통과. 정책 상 allowExternal=false 라도 이 경로는 우회됨.
- 재현 빈도: 낮음. 단, 재시작 정책이 보안 경계(플러그인 격리 등)에 의존한다면 영향 큼.

## 반증 탐색

- **숨은 방어**: resetSigusr1AuthorizationIfExpired (line 142-151) 가 sigusr1AuthorizedUntil
  경과 후 count 를 0 으로 reset → 5초 뒤 자연 해소. 단 5초 window 는 공격/오작동 가능.
- **기존 테스트**: restart 관련 테스트 파일 grep 안 함. process.kill throw 시나리오 재현이
  어려워 테스트 커버리지 낮을 가능성.
- **문서화/주석**: catch 주석 (line 134) 이 의도적으로 authorization 롤백을 생략했다는 명시
  없음 → 의도적 설계보다 누락에 가까움.
- **Result 패턴**: emitGatewayRestart 반환값은 boolean 이라 에러 정보 손실.

## Self-check

### 내가 확실한 근거
- line 126 에서 authorizeGatewaySigusr1Restart 호출 → sigusr1AuthorizedCount 증가 (line 164).
- line 133-136 catch 가 emittedRestartToken 만 롤백, 다른 상태 변경 없음 (직접 Read).
- 만료 시간 전에는 resetSigusr1AuthorizationIfExpired 가 count 를 그대로 둠 (line 146-148).

### 내가 한 가정
- process.kill(pid, "SIGUSR1") 이 실제로 EPERM/ESRCH 로 throw 할 수 있다고 가정. Node 문서상
  same-process 는 일반적으로 성공하지만 sandbox / prctl restriction 에서는 실패 가능.
- consumeGatewaySigusr1RestartAuthorization 호출처가 외부 signal 경로에 연결되어 있다고 가정
  (호출처 grep 안 함).

### 확인 안 한 것 중 영향 가능성
- 실제 외부 SIGUSR1 수신 경로 (process.on("SIGUSR1", ...) 등록 지점) 와 authorization
  consume 의 정확한 연결.
- `setGatewaySigusr1RestartPolicy({allowExternal: true})` 설정이 실제로 이 카운터 대신 다른
  경로로 분기되는지 (line 153-159 의 의미 확인 미완료).
