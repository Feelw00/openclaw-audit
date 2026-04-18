---
_parse_error: "while scanning for the next token\nfound character '`' that cannot\
  \ start any token\n  in \"<unicode string>\", line 48, column 12:\n      because:\
  \ `??=` (nullish assignment) 는 lef ... \n               ^"
status: rejected
rejected_reasons:
- "B-1-3: frontmatter YAML error: while scanning for the next token\nfound character\
  \ '`' that cannot start any token\n  in \"<unicode string>\", line 48, column 12:\n\
  \      because: `??=` (nullish assignment) 는 lef ... \n               ^"
---
# plugins 도메인 runtime 로더 4곳이 rejected dynamic import Promise 를 영구 캐시

## 문제

`src/plugins/install.ts:20-25` 등 4개 모듈이 runtime code 를 cold path 에서 lazy-load 하기 위해
dynamic import 를 module-scoped Promise 변수에 캐시한다:

```
let xxxRuntimePromise: Promise<...> | undefined;
function loadXxxRuntime() {
  xxxRuntimePromise ??= import("...runtime.js");
  return xxxRuntimePromise;
}
```

문제는 **첫 호출에서 import reject 시** rejected Promise 가 `xxxRuntimePromise` 에 남고
`??=` 는 null/undefined 가 아닌 값에 대해 덮어쓰지 않으므로 **영구 재시도 불가 상태** 가 된다.
프로세스 재시작이 유일한 recovery.

네 곳 모두 재시도/reset 경로가 없음 — 문제는 공통 idiom 설계에 embedded.

## 발현 메커니즘

1. OpenClaw 프로세스 시작, `src/plugins/install.ts` static-import 로 load 됨.
2. `pluginInstallRuntimePromise` 는 `undefined` (line 20).
3. 어느 경로 (e.g., plugin install CLI 실행, gateway 설치 sync) 가 `loadPluginInstallRuntime()` 호출.
4. line 23: `pluginInstallRuntimePromise ??= import("./install.runtime.js");`
   - 첫 호출: left-hand side undefined → `import(...)` 실행 → Promise 반환 → 변수에 저장.
5. Dynamic import 가 transient 실패 (ENOENT, EACCES, symlink resolution 실패, jiti alias 해석 중 오류 등):
   → Promise rejected.
6. caller `await loadPluginInstallRuntime()` → throw.
7. 문제의 원인이 해소되어도 (git pull 완료, 권한 복원 등) 후속 호출:
   - line 23 `??=` 는 rejected Promise 를 non-null 로 간주 → 덮어쓰지 않음.
   - 같은 rejected Promise 반환.
8. 모든 install/provider-runtime/provider-discovery/provider-auth-ref 관련 호출 stuck.
9. 프로세스 재시작 전에는 module-scoped 변수 reset 할 API 없음.

## 근본 원인 분석

1. **`??=` 의 semantic** (`install.ts:23`): nullish 할당 연산자는 current value 가 null/undefined
   일 때만 right-hand side 평가. rejected Promise 객체는 non-nullish 이므로 단락평가되어
   재할당 안 됨. dynamic import 재시도 경로 원천 봉쇄.

2. **rejection handler 부재** (`install.ts:23-24`): Promise 를 즉시 반환. `.catch` 를 체이닝하여
   실패 시 `pluginInstallRuntimePromise = undefined` 로 reset 하는 패턴이 적용되지 않음.

3. **Idiom 복제** (`provider-runtime.runtime.ts:15-22`, `provider-discovery.ts:7-12`,
   `provider-auth-ref.ts:18-22`): 4곳에 동일 패턴 반복. 공통 base helper 도 없어서 각각 수정 필요.

4. **Node ES spec 의 module registry 와의 불일치**: Node 는 특정 module path 에 대한 dynamic
   import 가 실패하면 다음 `import()` 호출 시 재평가를 허용 (module 이 registry 에 cached 되지
   않음). 그러나 본 코드는 **JS 레벨 Promise 캐시** 로 Node 의 재시도를 차단. "lazy-load"
   idiom 이 "once-failed, always-failed" 를 의도치 않게 구현.

## 영향

- **impact_hypothesis**: hang (무한 stuck 상태로 전환)
- **재현 시나리오**:
  1. OpenClaw 프로세스 실행 중에 `git pull` 또는 file system 작업이 install.runtime.ts 를
     원자적이지 않게 업데이트.
  2. 사용자가 `openclaw plugin install ...` 실행 → `loadPluginInstallRuntime()` 첫 호출 →
     dynamic import 가 ENOENT 리턴.
  3. `pluginInstallRuntimePromise` 에 rejected Promise 저장.
  4. git pull 완료 후 사용자 재시도 `openclaw plugin install ...`.
  5. `??=` short-circuit → 같은 rejected Promise 반환. 설치 실패.
  6. 재시도 루프 (CLI 수동 재시도) 계속 실패. 프로세스 재시작 전에는 복구 불가.
- **영향 범위**:
  - install.ts: 모든 install/uninstall/update CLI 명령.
  - provider-runtime.runtime.ts: model catalog 조회, auth hint, provider runtime auth.
  - provider-discovery.ts: provider list 반환.
  - provider-auth-ref.ts: secret resolve. 모든 secret lookup 실패 → 모든 provider 가 auth 실패.
- **빈도**: transient dynamic import 실패 자체가 흔하지 않음 (유저 기기에서 파일 재배치는
  설치/업데이트 때만 발생). 그러나 "한 번 실패로 영구 stuck" 이라는 성질이 reliability gap
  핵심.
- **P2 근거**: transient 실패 조건이 드물지만 (P3 hygiene 수준 아님), 영향이 install/provider/auth
  subsystem 전체 stuck (P1 수준) 이어서 균형 P2.

## 반증 탐색

**R-3 Grep 명령 + 결과:**

1. `rg -n "let \w+Promise.*Promise.*import|\?\?=\s*import\(" src/plugins/`
   → 4곳에서 동일 패턴:
   - src/plugins/install.ts:20-23
   - src/plugins/provider-auth-ref.ts:18-21
   - src/plugins/provider-discovery.ts:7-10
   - src/plugins/provider-runtime.runtime.ts:15-20
2. `rg -n "pluginInstallRuntimePromise|providerRuntimePromise|secretResolvePromise" src/plugins/`
   → 각 변수 선언 + 단일 함수 내부 사용. **reset/clear/reassign-undefined 경로 없음**.
3. `rg -n "Promise\.resolve.*catch|\.catch.*undefined" src/plugins/install.ts src/plugins/provider-runtime.runtime.ts src/plugins/provider-discovery.ts src/plugins/provider-auth-ref.ts`
   → 0건. rejection 시 캐시 invalidate 하는 패턴 없음.

**R-5 primary-path inversion:**

- **"이 stuck 이 성립하려면 어떤 방어가 없어야 하는가?":**
  (1) module-scoped 변수 reset API 없음 — grep 확인.
  (2) Node 자동 import module registry 재시도 — `??=` JS 캐시로 우회됨.
  (3) caller 재시도 로직 — install.ts 의 모든 public export 가 `await loadPluginInstallRuntime()`
      만 수행. 재시도 루프 미구현.
- **Test-only reset**: `vi.resetModules()` + `await import(...)` 로 module graph 전체를 다시
  평가하는 방식은 가능하지만 production 경로에선 사용 불가.
- **처리 중인 설계 의도**: lazy-load idiom 이 "cold path 에서만 import" 을 달성하는 것이 의도.
  transient failure recovery 는 명시적 요구사항이 아님. 그러나 "reliability/stability" 개선
  관점에서 dynamic import 재시도 안전성은 필요.
- **기존 테스트**: install.test.ts 존재하지만 rejection 재시도 시나리오 grep 결과 없음
  (rejected promise cache invalidation 검증 없음).
- **다른 lazy-load 패턴과의 비교**: src/plugins/install-security-scan.ts:37-39 의
  `loadInstallSecurityScanRuntime` 은 다음 형태:
  ```
  async function loadInstallSecurityScanRuntime() {
    return await import("./install-security-scan.runtime.js");
  }
  ```
  **매 호출마다 재 import** — 본 FIND 패턴보다 안전 (Node module cache 에 의존). cold path
  최적화는 잃지만 rejection 영구 캐시 문제 없음. 본 FIND 는 "optimization 이 안전성을 희생" 한 케이스.

## Self-check

### 내가 확실한 근거

- 4개 파일의 lazy-load 패턴 소스 모두 직접 Read/Grep 으로 확인.
- reset/clear 경로 grep 결과 0건.
- 대안 패턴 (install-security-scan.ts) 이 같은 도메인 내 존재 — 쉬운 비교.
- `??=` semantic: spec 상 left-hand side null/undefined 시에만 evaluate right-hand side.
  rejected Promise 는 truthy 객체이므로 재할당 안 됨.

### 내가 한 가정

- Node dynamic import 가 transient FS 에러 시 어느 정도 재시도 가능하다는 전제 — Node 내부
  구현 디테일 미검증. Node 의 구체적 동작에 따라 "재시도 가능" 이 "매번 실패" 에 가까울 수도
  있음. 그러면 이 FIND 의 severity 하향.
- transient import 실패가 실제 production 에서 발생한다는 가정 — 정량 근거 없음. file 재배치
  중 호출 등의 시나리오는 이론적.
- 4개 lazy-loader 경로 모두 동일 결함이지만 단일 FIND 로 합쳐 기술. clusterer 가 같은
  축으로 판단하길 기대.

### 확인 안 한 것 중 영향 가능성

- Node 22 의 정확한 dynamic import 재시도 동작. spec 상 "evaluation failure" 는 module registry
  에 cached 되지 않지만 구현 세부가 다를 수 있음.
- install.test.ts 내부 테스트 케이스 내용 — "dynamic import reject 후 재시도" 시나리오 미검증.
  기존 테스트가 이 결함을 이미 간접적으로 lock-in 했을 가능성.
- src/plugin-sdk/** 내부에도 유사 패턴 있을 수 있으나 본 세션 grep 대상에 포함되지 않음.
- async register 관련 FIND 001/002 와는 root cause axis 가 다름 (Promise escape vs Promise cache).
  clusterer 에서 별도 CAND 로 분리 예상.
