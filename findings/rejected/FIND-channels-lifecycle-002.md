---
_parse_error: "while parsing a block mapping\n  in \"<unicode string>\", line 81,\
  \ column 3:\n    - why: plugin reload 후에도 왜 과거 에러 k ... \n      ^\nexpected <block\
  \ end>, but found '<scalar>'\n  in \"<unicode string>\", line 82, column 40:\n \
  \    ... se: 'loggedMessageActionErrors' 는 module-load 시 한 번 생성되는 모듈-스코프  ... \n\
  \                                         ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: while parsing a block mapping\n  in \"<unicode string>\"\
  , line 81, column 3:\n    - why: plugin reload 후에도 왜 과거 에러 k ... \n      ^\nexpected\
  \ <block end>, but found '<scalar>'\n  in \"<unicode string>\", line 82, column\
  \ 40:\n     ... se: 'loggedMessageActionErrors' 는 module-load 시 한 번 생성되는 모듈-스코프\
  \  ... \n                                         ^"
---
# loggedMessageActionErrors Set dedup 이 plugin reload 후 stale key 로 새 에러를 silence

## 문제

'src/channels/plugins/message-action-discovery.ts:43' 에서 선언된 모듈-스코프
Set 'loggedMessageActionErrors' 는 channel plugin 의 describeMessageTool
throw 를 dedup 하여 로그 spam 을 방지한다. 그러나 dedup key 가
'${pluginId}:${operation}:${message}' 로 구성되고 Set 은 프로세스 수명
동안 clear 되지 않는다 ('__testing.resetLoggedMessageActionErrors' 만 공개).
plugin registry 가 re-pin 되어 동일 pluginId 로 새 plugin 인스턴스가 로드
되더라도 이전 plugin 이 남긴 key 가 잔존하여 '이미 보고된 에러' 로 간주.
새 plugin 의 regression 에러가 사일런트 drop 된다.

## 발현 메커니즘

1. gateway 기동. channel plugin 'slack' v1 이 로드됨.
2. inbound 메시지 처리 중 listChannelMessageActions / resolveChannelMessageToolSchemaProperties
   등이 'resolveMessageActionDiscoveryForPlugin' → 'describeMessageToolSafely'
   (line 87-102) 호출. v1 의 describeMessageTool 이 throw
   ('invalid schema: foo'). catch 블록이 'logMessageActionError' 호출
   (line 70-85). key='slack:describeMessageTool:invalid schema: foo' 가
   'loggedMessageActionErrors.add(key)' 에 기록되고 defaultRuntime.error 로
   log 1회 출력.
3. 이후 동일 key 인 모든 throw 는 'has(key)' 체크 (line 77-79) 로 silent drop.
   이 동작은 log spam 방지 의도.
4. plugin registry 가 re-pin 되어 v2 로 교체 (runtime.channel-pin.test.ts:
   121-125 전이 참고). v2 는 v1 의 버그 수정을 포함한다고 가정했으나 동일
   문자열 'invalid schema: foo' 를 던지는 regression 이 실제 존재한다고
   가정. 'loggedMessageActionErrors' 는 module-scope 라 여전히 v1 의 key 를
   보유. v2 의 describeMessageTool throw → key 동일 → has(key) true →
   log 출력 안 됨.
5. 상위 호출자 'resolveMessageActionDiscoveryForPlugin' (line 178-217) 는
   null 반환을 '기능 미지원' 으로 해석하여 빈 actions/capabilities/schema
   반환. 사용자는 tool 발견 실패를 체험하지만 운영자의 log 에는 regression
   이 나타나지 않음.

## 근본 원인 분석

1. **module-scope Set 의 lifecycle 비연동** (line 43): 'loggedMessageActionErrors'
   는 모듈 import 시 한 번 생성되며 channel plugin registry 의 버전
   전이와 독립. 동일 디렉터리 'configured-binding-compiler.ts:184-198' 는
   'registryVersion' 을 invalidation 조건에 포함하지만 이 파일은 그렇지
   않음.

2. **production clear 경로 부재** (line 360-364): '__testing' 네임스페이스만
   clear 를 노출. plugin reload / re-pin 시 downstream 캐시에 invalidation
   을 전파하는 hook 이 channels 측에 없음.

3. **dedup key 설계의 시간 경계 부재**: key 는 content (pluginId + operation
   + message) 만 식별. 시간 경계 (registry version, session id, restart
   epoch) 가 없어 '같은 에러' 의 판정이 영원. plugin 교체 후에도 '이전과
   같음' 으로 취급.

4. **관측성 체인 break**: 'describeMessageToolSafely' 가 catch 후 null
   반환하여 상위 (line 195-217) 가 빈 discovery 로 degrade. log 가 침묵하면
   이 degrade 는 잡음 없는 정상 상태로 위장됨. plugin lifecycle 전이
   이후의 regression 이 사일런트.

## 영향

- **impact_hypothesis**: wrong-output (관측성 손실로 regression 은닉 →
  기능 degrade silent).

- **재현 시나리오**:
  1. plugin A 첫 로드 후 describeMessageTool 이 'E1' 로 throw → log 1회.
  2. 'loggedMessageActionErrors.has("A:describeMessageTool:E1") === true'.
  3. plugin registry re-pin. 새 plugin A' 가 동일 pluginId, 동일 문자열 'E1'
     로 throw (regression).
  4. log 침묵. 상위 호출자 null 반환 → empty discovery.
  5. 사용자 인터랙션에서 message tool 기능 부재처럼 보이나 운영 대시보드엔
     증상 없음.

- **빈도**: re-pin × regression 확률. 일반 gateway 수명에서는 드문 경로.
  그러나 'src/plugins/runtime.channel-pin.test.ts' 가 re-pin 을 공식 지원
  경로로 검증하므로 이론적 시나리오가 아닌 설계 의도 내 경로.

- **secondary**: error message 가 동적 payload 포함 시 key 공간 무한. long-
  running 프로세스에서 Set 크기 unbounded 성장 (memory secondary concern).

- **P2 근거**: 관측성 손실은 사용자 체감 즉시는 아니지만 regression
  inspection 경로 차단. data loss 는 없음. re-pin 빈도 미확정이라 P1 승급
  근거 부족.

## 반증 탐색

**R-3 Grep 결과:**

1. 'rg -n "resetLoggedMessageActionErrors|loggedMessageActionErrors" src/'
   → 정의 라인 (43, 77, 80, 362). 테스트 caller 소수. production 호출 0건.

2. 'rg -n "loggedMessageActionErrors\\.(clear|delete)" src/'
   → 매치 1건 ('__testing.resetLoggedMessageActionErrors' line 362 '.clear()').
   production 경로 없음.

3. 'rg -n "registryVersion|getActivePluginChannelRegistryVersion"
   src/channels/plugins/message-action-discovery.ts' → 0건. lifecycle 신호
   연동 없음.

**R-5 분류:** 'resetLoggedMessageActionErrors' → 'test-only'.

**추가 반증 탐색:**

- **상위 defensive**: 'describeMessageToolSafely' (line 87-102) 가 catch 후
  null 반환하는 것은 방어지만 log 침묵 자체는 해결하지 않음. 오히려 상위
  경로가 null 을 "기능 미지원" 으로 해석하여 사용자 degrade 를 정상으로
  분류.

- **기존 테스트 커버리지**: 'message-actions.test.ts',
  'message-actions.security.test.ts' 는 특정 action 실행 경로를 검증.
  plugin re-pin 후 dedup 침묵 시나리오를 검증하는 테스트는 파악된 범위 내
  부재.

- **settings / feature flag 경로**: log level, verbose flag 로 dedup 을
  override 하는 경로 grep 결과 0건.

- **재기동 시 자연 복구**: 프로세스 재시작 시 module 재로드로 Set 리셋.
  장기 gateway 프로세스 (weeks) 에서만 영향 누적.

## Self-check

### 내가 확실한 근거

- 'loggedMessageActionErrors' 가 모듈-스코프 Set 이며 키가 pluginId+operation+message
  조합임을 line 43, 76-80 에서 직접 확인.
- 'resetLoggedMessageActionErrors' 가 '__testing' 네임스페이스로만 export
  됨을 line 360-364 에서 확인.
- plugin channel registry version 이 re-pin 시 증가하는 경로가 src/plugins/
  runtime.channel-pin.test.ts:121-125 에서 공식 지원됨을 확인.
- 상위 'describeMessageToolSafely' 가 null 반환으로 기능 degrade 를 정상
  흐름처럼 처리 (line 87-102, 178-217).

### 내가 한 가정

- re-pin 이후 동일 pluginId 로 새 plugin 인스턴스가 교체되는 production
  경로가 실제 존재한다고 가정. runtime.channel-pin.test.ts 는 설계 지원을
  확증하나 실제 production caller 빈도는 scope 외 (src/plugins/runtime.ts
  allowed_paths 외).
- v1 과 v2 가 동일 error message 로 throw 하는 regression 이 현실적으로
  발생 가능하다고 가정. Error 문자열 변경이 잦은 개발 환경에서는 빈도
  낮을 수 있음 — 그 경우 severity 는 P2 → P3.

### 확인 안 한 것 중 영향 가능성

- 'src/plugins/runtime.ts' 의 'pinPluginChannelRegistry' 등 실제 re-pin
  호출자 (scope 외). production 에서 호출되지 않으면 영향 최소.
- error message 에 동적 payload 가 포함되는 빈도 (Set 크기 unbounded
  성장 secondary concern 의 실현 빈도).
- 다른 lifecycle-gap FIND (FIND-channels-lifecycle-001) 와의 공통 원인:
  둘 다 'registryVersion 을 downstream cache 에 전파하지 않음' 이라는
  동일 축이므로 clusterer 가 CAND 로 epic 묶을 가능성 있음.
- plugin manifest 상 'slack' 의 describeMessageTool 이 실제 throw 하는
  조건 (schema 검증 실패 경로 실존 여부) 은 channel plugin 구현체 scope
  외. 이 throw 경로가 theoretic only 라면 FIND severity 하향.
