---
_parse_error: "while parsing a block mapping\n  in \"<unicode string>\", line 58,\
  \ column 3:\n    - why: 같은 디렉터리 내 다른 cache 는 versio ... \n      ^\nexpected <block\
  \ end>, but found '<scalar>'\n  in \"<unicode string>\", line 59, column 53:\n \
  \    ... ed-binding-compiler.ts:184-198' 는 'getActivePluginChannelRegistr ... \n\
  \                                         ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: while parsing a block mapping\n  in \"<unicode string>\"\
  , line 58, column 3:\n    - why: 같은 디렉터리 내 다른 cache 는 versio ... \n      ^\nexpected\
  \ <block end>, but found '<scalar>'\n  in \"<unicode string>\", line 59, column\
  \ 53:\n     ... ed-binding-compiler.ts:184-198' 는 'getActivePluginChannelRegistr\
  \ ... \n                                         ^"
---
# bundled channel public-surface 2차 cache 가 plugin registry version 무시로 stale module 유지

## 문제

'src/channels/plugins/thread-binding-api.ts' 의 모듈-스코프 'threadBindingApiCache'
(line 28) 와 'src/channels/plugins/message-tool-api.ts' 의 'messageToolApiCache'
(line 15) 는 채널 id 만을 키로 사용해 bundled plugin public-surface 모듈을
캐시한다. plugin channel registry 는 별도 'version' 을 갖고 re-pin/reload 시
증가하지만 (src/plugins/runtime-channel-state.ts:33-36,
src/plugins/runtime.channel-pin.test.ts:121-125), 이 두 2차 cache 는 version
비교 또는 invalidation 경로를 구현하지 않는다. production 에서 cache 를 비우는
경로는 '__testing.clearThreadBindingApiCache' / '__testing.clearMessageToolApiCache'
export 뿐이다. 결과적으로 프로세스 수명 동안 registry 가 갱신되더라도 이
cache 는 최초 로드 시점의 module 참조를 계속 반환한다.

## 발현 메커니즘

1. gateway 기동, 채널 id 'slack' 첫 inbound.
   'resolveBundledChannelThreadBindingInboundConversation({channelId:"slack", ...})'
   (thread-binding-api.ts:64-78) 호출 →
   'loadBundledChannelThreadBindingApi("slack")' (line 30-49) →
   'loadBundledPluginPublicArtifactModuleSync' 가 bundled 'thread-binding-api.js'
   (v1) 를 로드 → 'threadBindingApiCache.set("slack", apiV1)' (line 40).

2. 동일 프로세스 내 plugin registry 가 re-pin. 예: config watcher 가 plugin
   set 을 교체하거나 수동 test API 가 pin 을 전환하는 시나리오.
   'PLUGIN_REGISTRY_STATE' 심볼 아래 'state.channel.version' 이 +1 증가
   (runtime.channel-pin.test.ts:121-125 가 이 전이를 검증). bundled
   public-surface 디렉토리도 업데이트되어 'loadBundledPluginPublicArtifactModuleSync'
   가 새로 호출되면 apiV2 를 반환할 잠재 상태.

3. 다음 inbound 메시지 처리에서
   'resolveBundledChannelThreadBindingDefaultPlacement("slack")' (line 56-62)
   또는 'resolveBundledChannelThreadBindingInboundConversation' 가 다시 호출.
   'threadBindingApiCache.has("slack")' 는 true 이므로 line 32-34 에서 apiV1
   이 그대로 반환. version 비교 누락.

4. 'apiV1.resolveInboundConversation(params)' 이 과거 plugin 의 placement
   로직으로 inbound 메시지의 conversationId/parentConversationId 를 결정.
   새 plugin 계약과 불일치하면 threading 이 잘못된 parent 로 귀속.

5. 동일 증상이 'message-tool-api.ts' 의 'messageToolApiCache' 에도 적용.
   'resolveBundledChannelMessageToolDiscoveryAdapter' / 'describeBundledChannelMessageTool'
   가 stale 'describeMessageTool' 을 사용 → 메시지 툴 discovery 가 과거
   plugin 버전의 schema contribution 을 보고.

## 근본 원인 분석

1. **cache 키 설계 불완전** (thread-binding-api.ts:28-34): 키가 'channelId.trim()'
   만 사용. 'getActivePluginChannelRegistryVersionFromState()' 또는
   'registryRef' 를 키 조합에 포함하지 않음. 동일 디렉터리의
   'configured-binding-compiler.ts:184-198' 는 'cached?.registryVersion === registryVersion && cached.registryRef === activeRegistry'
   로 invalidation 을 구현하여 참조 패턴이 존재함에도 적용이 일관되지 않음.

2. **production clear 경로 미노출** (thread-binding-api.ts:80-82,
   message-tool-api.ts:61-63): 'clearThreadBindingApiCache' /
   'clearMessageToolApiCache' 는 '__testing' 네임스페이스로만 export 되어
   테스트 전용. Grep 결과 production 호출 지점 0건. 상위 plugin-lifecycle
   hook (plugin reload 완료 시 downstream 캐시에 invalidation 전파) 이
   channels 도메인에는 설계되지 않음.

3. **상위 1차 캐시와의 동기화 부재** (src/plugins/public-surface-loader.ts:20):
   'loadedPublicSurfaceModules = new Map<string, unknown>()' 가 plugin 측
   1차 캐시로 존재하나 channels 측 2차 캐시 (thread-binding-api / message-tool-api)
   와 상이한 생애 주기를 가진다. 1차 가 무효화되더라도 2차는 독립적으로 유지.

4. **plugin 인터페이스 계약과 경로 간 mismatch**:
   'resolveInboundConversation', 'defaultTopLevelPlacement',
   'describeMessageTool' 는 plugin 이 버전에 따라 바꿀 수 있는 동작 계약.
   channels 측은 이 계약의 stability 를 가정한 채 1회 로드 후 영구 보존.
   plugin registry version 이라는 explicit한 re-pin 메커니즘이 있음에도
   downstream 에 전파되지 않음 → drift.

## 영향

- **impact_hypothesis**: wrong-output — threading 결정과 tool discovery
  schema 가 과거 plugin 버전 로직으로 계산됨.

- **재현 시나리오**:
  1. gateway 기동, slack inbound 1건 처리 →
     'threadBindingApiCache.has("slack") === true', apiV1 저장.
  2. 'pinPluginChannelRegistry' 또는 'setActivePluginChannelRegistry' 로
     plugin registry re-pin (runtime.channel-pin.test.ts 가 검증하는 전이).
  3. bundled public-surface 아티팩트가 새 'defaultTopLevelPlacement' 를
     "current" → "child" 로 변경한 apiV2 로 교체됨 (artifact 갱신은 test
     시나리오에서 파일 내용 교체로 재현 가능).
  4. 다음 slack inbound →
     'resolveBundledChannelThreadBindingDefaultPlacement("slack")' 가 apiV1
     결과 "current" 반환. 실제 plugin 은 "child" 를 요구했지만 cache stale.
  5. inbound conversation 이 child thread 가 아닌 current placement 로
     resolve → 대화 연결이 잘못된 conversation 에 귀속.

- **빈도**: re-pin 빈도 × public-surface 아티팩트 변경 조건. 일반 프로덕션
  gateway 수명에서 plugin set 이 re-pin 되는 상황은 드물다 (config
  hot-reload 시 plugin set 이 바뀌는 특정 flow 한정). 따라서 severity P2.

- **정정 가능성**: registry version 비교 하나만 추가하면 소멸되는 결함. 동
  디렉터리에 이미 cmake 패턴 있음. 구조적 재설계 불필요.

## 반증 탐색

**R-3 Grep 결과:**

1. 'rg -n "clearThreadBindingApiCache|clearMessageToolApiCache" src/'
   → 매치: thread-binding-api.ts:81, message-tool-api.ts:62 (export);
   thread-binding-api.test.ts:45, message-tool-api.test.ts:40 (test caller).
   production 경로 0건.

2. 'rg -n "registryVersion|getActivePluginChannelRegistryVersion" src/channels/plugins/'
   → configured-binding-compiler.ts:184/195/208, registry-loaded.ts:59/91,
   setup-registry.ts:57/59/73, session-conversation.ts:162 에서는 사용.
   thread-binding-api.ts / message-tool-api.ts 에서는 import 0건.

3. 'rg -n "threadBindingApiCache\\.|messageToolApiCache\\." src/'
   → .set/.get/.has/.clear 만 매치. 엔트리 단위 filter/splice/delete 경로 없음.

**R-5 분류:** 'clearThreadBindingApiCache', 'clearMessageToolApiCache' 모두
'test-only'. production invalidation 경로 없음 → lifecycle-gap 성립.

**추가 반증 탐색:**

- **상위 1차 캐시의 보호**: 'src/plugins/public-surface-loader.ts:20-30' 의
  'loadedPublicSurfaceModules' 가 bundled public-surface 를 process 수명
  동안 보관. 이 계층이 stale 되더라도 channels 측 2차 캐시는 독립적으로
  동기화 안 됨.

- **기존 테스트 커버리지**: 'thread-binding-api.test.ts',
  'message-tool-api.test.ts' 는 각각 clear helper 를 beforeEach 에서
  사용하여 테스트 격리를 달성. 이는 re-pin drift 를 검증하지 않는다.

- **plugin lifecycle re-pin 경로의 현실성**: runtime.channel-pin.test.ts:121-125
  가 'channelVersionBeforeRepin + 1' 증가를 검증하므로 re-pin 은
  설계상 지원. 프로덕션 빈도는 불명.

- **registry-loaded/setup-registry/configured-binding-compiler 의 패턴**:
  모두 version+ref 조합으로 invalidation. 본 파일들만 적용 누락 —
  consistency gap.

## Self-check

### 내가 확실한 근거

- 'threadBindingApiCache' (line 28), 'messageToolApiCache' (line 15) 가
  채널 id 만을 키로 사용하는 Map. 직접 읽어 확인.
- 'clearThreadBindingApiCache' / 'clearMessageToolApiCache' 는 '__testing'
  export 에만 노출 (line 80-82, 61-63).
- 같은 디렉터리 'configured-binding-compiler.ts:184-212' 은
  registryVersion 비교 기반 invalidation 을 이미 구현.
- 'src/plugins/runtime.channel-pin.test.ts:121-125' 가 channel registry
  version 증가 전이를 검증하여 re-pin 이 설계상 지원됨을 확인.

### 내가 한 가정

- re-pin 이후 bundled public-surface 아티팩트가 실제로 변경되는 시나리오가
  production 에 존재한다고 가정. bundled 디렉터리는 원자적 패키징이
  일반적이라 빈도는 낮을 수 있음. 이 가정이 약하면 severity 는 P2 → P3.
- 'apiV1.defaultTopLevelPlacement' 와 'apiV2.defaultTopLevelPlacement' 가
  다를 수 있는 조건 (plugin upgrade 경계) 을 가정. 실제 plugin 계약
  stability policy (SDK versioning) 가 bundled plugin 을 immutable 로 간주
  한다면 영향 최소.

### 확인 안 한 것 중 영향 가능성

- **src/plugins/runtime.ts:173 및 runtime.channel-pin.test.ts** 의 실제
  re-pin production caller 가 gateway 실행 중 언제 호출되는지 추적 못 함
  (allowed_paths 외). 호출이 shutdown-only 또는 test-only 라면 production
  영향 미미.
- bundled public-surface 디렉터리가 프로덕션에서 덮어써지는 빈도
  (deployment reload 등) 정량 데이터 없음.
- 'loadBundledPluginPublicArtifactModuleSync' 의 상위 캐시
  ('loadedPublicSurfaceModules') 가 stale 해소 역할을 일부 수행하는지
  ('src/plugins/public-surface-loader.ts') 완전한 흐름 추적 못 함 — 해당
  파일이 scope 외.
- 'message-tool-api.ts' 와 'thread-binding-api.ts' 중 어느 쪽이 더 높은
  빈도로 호출되는지 (hot path) 측정 못 함. severity 는 둘 중 더 활성
  경로 기준으로 평가해야 함.
