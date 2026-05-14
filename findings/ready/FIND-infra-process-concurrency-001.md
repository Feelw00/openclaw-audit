---
id: FIND-infra-process-concurrency-001
cell: infra-process-concurrency
title: deliverTarget 의 activeEntries 갱신 사이 onStopped 가 clear 하면 wrapped binding orphan
file: src/infra/approval-handler-runtime.ts
line_range: 498-537
evidence: "```ts\n      deliverTarget: async ({\n        plannedTarget,\n        preparedTarget,\n\
  \        request,\n        approvalKind,\n        pendingContent,\n      }) => {\n\
  \        const entry = await nativeRuntime.transport.deliverPending({\n        \
  \  ...baseContext,\n          plannedTarget,\n          preparedTarget,\n      \
  \    request,\n          approvalKind,\n          view: pendingContent.view,\n \
  \         pendingPayload: pendingContent.payload,\n        });\n        if (!entry)\
  \ {\n          return null;\n        }\n        const binding = await nativeRuntime.interactions?.bindPending?.({\n\
  \          ...baseContext,\n          entry,\n          request,\n          approvalKind,\n\
  \          view: pendingContent.view,\n          pendingPayload: pendingContent.payload,\n\
  \        });\n        const wrapped: WrappedPendingEntry = {\n          entry,\n\
  \          ...(binding === undefined || binding === null ? {} : { binding }),\n\
  \        };\n        const activeRequest = activeEntries.get(request.id) ?? {\n\
  \          request,\n          approvalKind,\n          entries: [],\n        };\n\
  \        activeRequest.entries.push(wrapped);\n        activeEntries.set(request.id,\
  \ activeRequest);\n        return wrapped;\n      },\n```\n"
symptom_type: concurrency-race
problem: '`deliverTarget` 가 두 개의 await (`deliverPending` 와 `bindPending`) 를 거치는 동안

  같은 핸들러의 `onStopped` (line 656-672) 가 실행되어 `activeEntries.clear()` 를

  호출하면, deliverTarget 가 resume 한 뒤 line 529-535 에서 wrapped entry 를 새 Map

  슬롯에 다시 등록한다. 이 wrapped 는 이미 종료된 핸들러의 어떤 finalize/unbind 경로

  로도 도달하지 않으므로 native 측 binding 이 영원히 unbind 되지 않는다.

  '
mechanism: "1. caller (`approval-native-runtime.ts`) 가 deliverTarget(R1) 호출 — line\
  \ 498.\n2. line 505 `await deliverPending` 진입. native 측 entry 가 생성됨.\n3. (await\
  \ 도중) caller 가 핸들러를 stop. onStopped (line 656-672) 가\n   activeEntries 의 기존 entry\
  \ 들에 대해 unbindWrappedEntries 를 모두 호출하고\n   line 671 `activeEntries.clear()` 수행.\
  \ 핸들러는 이제 stopped 상태로 간주됨.\n4. deliverPending 가 resume. line 517 `await bindPending`\
  \ 진입. native 측\n   binding 객체 생성 (또는 다른 await 진행). 이미 stop 된 native side 임에도\n \
  \  bindPending 자체가 동기 throw 하지 않는 한 binding 객체는 반환됨.\n5. bindPending resume → line\
  \ 525 wrapped 생성, line 529 `activeEntries.get(R1)`\n   은 undefined (3번에서 clear 됨)\
  \ → line 532 fallback `entries: []` 신규 객체.\n6. line 534 push, line 535 `activeEntries.set(R1,\
  \ ...)`. wrapped 는 비어있던\n   Map 에 다시 등록되었지만 onStopped 는 이미 끝났고, finalizeResolved/Expired\n\
  \   는 stopped 핸들러에서 호출되지 않음.\n7. wrapped.binding 은 unbindPending 으로 도달하지 않음 → native\
  \ 측 자원 leak.\n"
root_cause_chain:
- why: 왜 deliverTarget 가 stop 도중 진행될 수 있는가?
  because: 'deliverTarget 함수 자체가 stop 상태를 확인하지 않으며, activeEntries Map 만이

    shared mutable state. await 사이 단계에서 외부가 onStopped 를 통해 Map 을

    clear 해도 deliverTarget 는 그것을 감지할 방법이 없다.

    '
  evidence_ref: src/infra/approval-handler-runtime.ts:517-535
- why: 왜 activeEntries 접근에 lock/CAS 가 없는가?
  because: '파일 전체에 Mutex/Semaphore/AsyncLock 매치 0 (R-3 grep). Map operation 이 sync

    라 atomic 이지만, deliverPending/bindPending 의 await 가 sync 블록을 분리하는

    한 read-modify-write (line 529-535) 가 외부 mutation 으로부터 보호되지 않는다.

    '
  evidence_ref: src/infra/approval-handler-runtime.ts:444
- why: 왜 onStopped 가 deliverTarget 진행 여부를 확인하지 않는가?
  because: 'onStopped (line 656-672) 는 activeEntries 의 현재 스냅샷에 대해서만

    unbindWrappedEntries 를 호출하고 진행 중인 deliverTarget 가 활성 entry 를

    추가할 가능성을 고려하지 않음. activeEntries.size 확인 후 모두 unbind 그리고

    clear 하는 패턴은 deliverTarget 의 미완료 await 를 차단하지 못한다.

    '
  evidence_ref: src/infra/approval-handler-runtime.ts:656-672
- why: 왜 결국 native binding 이 leak 되는가?
  because: 'stopped 핸들러로는 finalizeResolved 또는 finalizeExpired 가 호출되지 않으므로

    (caller 가 stop 한 핸들러를 dispatch 대상에서 제외), wrapped 가 든 새

    activeEntries 슬롯은 다음 createChannelApprovalHandlerFromCapability 호출까지

    아무도 건드리지 않는다. activeEntries 는 closure 변수이고 핸들러 객체가

    참조 해제되면 GC 되지만, native 측 binding 은 명시적 unbind 가 필요한 외부

    자원 - 예 native UI listener, 통신 채널 register - 이라 GC 만으로는 정리 안 됨.

    '
  evidence_ref: src/infra/approval-handler-runtime.ts:118-148
impact_hypothesis: resource-exhaustion
impact_detail: '정성: 핸들러 stop 도중 들어온 deliverTarget 의 native binding 한 건이 unbind 되지

  않음. native side 가 stop 후에도 listener 또는 통신 채널을 유지하면 자원이 누적.

  exec/plugin approval 핸들러 lifecycle 이 빈번하지 않지만 (config 재로드 / 채널

  재바인딩 시 stop), 재로드를 반복하면 누적 가능. 정량 추정 불가 — 재현 빈도가

  caller (approval-native-runtime.ts) 의 deliver+stop interleaving 패턴에 의존.

  '
severity: P3
counter_evidence:
  path: src/infra/approval-handler-runtime.ts
  line: 656-672
  reason: "R-3 grep 5종 모두 hit 0 (Mutex/AsyncLock 0, AbortSignal 은 abort-signal.ts\
    \ 에만\n국한, Promise.race 0, once/prependListener 0, microtask 0). 즉 file 내 동기화\n\
    원시 없음.\n\nR-5 execution-condition 분류 (해당 race 무력화 후보):\n| 가드 | 위치 | 분류 |\n|---|---|---|\n\
    | activeEntries 접근 lock | 없음 | `none` |\n| onStopped 의 in-flight deliverTarget\
    \ 대기 | 없음 (Map snapshot 만) | `none` |\n| deliverTarget 의 stopped 상태 체크 | 없음 |\
    \ `none` |\n| finalizeResolved/Expired 의 consumeActiveWrappedEntries (line 89-97)\
    \ | finalize\n  경로 진입 시 | `unconditional` 단, stopped 핸들러에서 호출 안 됨 |\n\nunconditional\
    \ guard 부재. 단, **R-7 hot-path 검증 한계**: caller\napproval-native-runtime.ts (범위\
    \ 밖) 가 stop 호출 전 in-flight deliverTarget\n완료를 await 하면 race 미발생. 본 셀 범위에서 caller\
    \ contract 확인 불가하여\nconfidence 낮음. CAND-007 abandoned 가이드와 유사한 borderline P3 로\
    \ 기록.\n\nupstream 6주 commit 검토 (R-8): approval-handler-runtime.ts 는 type/export\n\
    refactor (566cbb24aa, ce73e6647c, 194c516957) 만 진행됨. race/concurrent/lock\n키워드\
    \ commit 없음.\n"
status: discovered
discovered_by: concurrency-auditor
discovered_at: '2026-05-14'
domain_notes_ref: domain-notes/infra-process.md
related_tests:
- src/infra/approval-handler-runtime.test.ts
---
# deliverTarget 의 activeEntries 갱신 사이 onStopped 가 clear 하면 wrapped binding orphan

## 문제

`createChannelApprovalHandlerFromCapability` 내부 closure 의 `activeEntries` Map 은
deliverTarget / finalizeResolved / finalizeExpired / onStopped 네 경로가 공유하는
shared mutable state 이다. deliverTarget (line 498-537) 는 `deliverPending` 과
`bindPending` 두 차례 await 를 거친 뒤에야 `activeEntries.set` 으로 등록한다. 이
await 사이 onStopped (line 656-672) 가 실행되면 wrapped 가 stopped 핸들러에 묶이지
못한 채 Map 에 다시 들어가 native binding 의 unbind 경로가 사라진다.

## 발현 메커니즘

```
T0: deliverTarget(R1) 진입 (line 498).
T1: await deliverPending (line 505) 진입 — pending.
    │
    ├─ caller 가 stop 결정.
T2: onStopped 실행 (line 656-672):
    - 현재 activeEntries 의 모든 request 에 unbindWrappedEntries 호출.
    - 그 때까지 deliverTarget(R1) 의 wrapped 는 아직 Map 에 없으므로
      onStopped 의 unbind 대상에서 누락.
    - activeEntries.clear() (line 671) 호출.
T3: deliverPending resolve → entry 반환.
T4: await bindPending (line 517) 진입 — pending.
T5: bindPending resolve → binding 반환.
T6: wrapped = {entry, binding} 생성 (line 525).
T7: activeEntries.get(R1) → undefined (T2 에서 clear).
    → fallback {request, approvalKind, entries: []}.
T8: entries.push(wrapped), activeEntries.set(R1, activeRequest) (line 534-535).
T9: deliverTarget 가 wrapped 반환. 그러나 핸들러는 이미 stopped.
T10: caller 가 stopped 핸들러에서 finalizeResolved/Expired 를 호출하지 않으므로
     wrapped 의 binding 은 unbindPending 으로 도달하지 못함.
```

## 근본 원인 분석

1. **shared mutable state 접근에 동기화 부재**. activeEntries Map (line 444) 은
   순수 closure 변수로 lock/CAS/AbortSignal 어떤 가드도 없다 (R-3 grep 0 hits).
   deliverTarget 의 read-modify-write (line 529-535) 는 sync 블록 안에서 atomic
   이지만, 그 sync 블록 진입 전 두 차례 await 가 stop 의 진입을 허용한다.

2. **onStopped 가 in-flight deliverTarget 을 기다리지 않는다**. line 657 `if
   (activeEntries.size === 0)` 체크 후 line 661-670 으로 현재 entry 들을 unbind
   하고 line 671 `clear()`. 진행 중인 deliverTarget 의 await 가 완료될 때까지
   blocking 하는 메커니즘 없음. inflight 카운터, AbortController, Mutex 모두
   부재.

3. **deliverTarget 자체가 stopped 상태를 인식하지 못함**. line 514 `if (!entry)`
   조기 반환만 있고 `핸들러가 stop 되었는지` 를 확인하는 분기 없음. 만약 stop
   상태를 알 수 있다면 line 517 bindPending 호출을 건너뛰고 line 535 의 set
   대신 native cleanup 으로 진행해야 정상.

4. **native binding 은 GC 로 회수되지 않음**. wrapped 가 활성 Map 슬롯이든 새
   슬롯이든 결국 GC 되더라도 native 측 listener/통신 채널 등록은 명시적
   unbindPending 호출이 필요함 (line 118-148 unbindWrappedEntries 의 존재가
   계약). 따라서 orphan 은 메모리가 아닌 외부 자원 누수로 이어진다.

## 영향

- 카테고리: resource-exhaustion (native side 자원).
- 빈도: 채널/플러그인 reload 또는 핸들러 교체 시. 정상 운영에서는 드물지만, config
  watcher 가 channel approval 설정을 자주 갱신하는 환경에서 누적 가능.
- 자가 회복: 없음. 다음 새 핸들러 인스턴스는 다른 closure 의 activeEntries 를
  사용하므로 누락된 wrapped 의 binding 정리 책임은 어떤 코드에도 남지 않음.

재현 시나리오:
1. native approval 핸들러 생성 후 첫 deliverTarget 호출이 진행 중인 동안 (예:
   bindPending 안의 native register 통신이 약간 길어진 상태) caller 가 채널 재바
   인딩 (channel reload) 으로 onStopped 를 트리거.
2. deliverTarget 완료 후 wrapped 가 stale activeEntries Map 에 다시 들어감.
3. 다음 cycle 에 새 핸들러가 만들어지지만 이전 wrapped 의 native binding 은 유지됨.

## 반증 탐색

- **R-3 grep 5종**: file 내 매치 0 (Mutex/Semaphore/AsyncLock, AbortController,
  Promise.race, once/prependListener, microtask). 즉 동기화 원시 부재.

- **R-5 execution-condition 분류**: 위 counter_evidence 표 참조. 모든 가드 후보가
  `none`. 단 finalizeResolved/Expired 의 `consumeActiveWrappedEntries` (line 89-97)
  는 unconditional unbind 경로이지만, **stopped 핸들러에서는 호출되지 않음**
  (caller dispatcher 가 stop 후 dispatch 중단). 따라서 race 의 wrapped 는 이 경로
  로 도달 못 함.

- **숨은 방어 / defense-in-depth**: 없음. deliverTarget 의 line 514 `if (!entry)`
  는 entry 자체가 falsy 인 경우만 cover. stopped 상태 체크 부재.

- **기존 테스트 커버리지**: approval-handler-runtime.test.ts 가 있으나 onStopped
  중 deliverTarget interleaving 시나리오는 R-7 의 production hot-path 와 다를
  가능성 (mock 으로 강제하기 쉬운 race) — 추가 검증 필요.

- **upstream 활동 (R-8)**: 6주 commit 5건 (566cbb24aa, ce73e6647c, 194c516957 등)
  모두 export/refactor. race/concurrent 키워드 commit 없음 — 본 race 는 upstream
  이 아직 인지하지 못한 것으로 추정.

- **R-7 hot-path vs test-path**: caller (`approval-native-runtime.ts`, allowed
  paths 밖) 가 deliverTarget 진행 중에 onStopped 를 호출할 수 있는지 본 셀 범위
  내에서 확인 불가. 만약 caller 가 in-flight deliverTarget 의 promise 를 await
  한 후에만 onStopped 를 invoke 한다면 race 미발생. **이 가정이 확인되지 않는
  한 P3 (이론적 race, 미사용 경로 가능성)**. 확인되면 P2 로 격상.

## Self-check

### 내가 확실한 근거
- file 내부에 동기화 원시 0건 (R-3 grep 결과).
- deliverTarget 의 line 529-535 가 read-modify-write 패턴이며, 그 직전에 두
  차례 await (line 505, 517) 가 존재.
- onStopped (line 671) 가 activeEntries.clear() 를 무조건 실행.
- finalizeResolved/Expired 가 stopped 핸들러에서 호출된다는 보장 없음.

### 내가 한 가정
- caller (approval-native-runtime.ts) 가 deliverTarget 와 onStopped 를 독립적으로
  dispatch 한다는 가정. 실제로는 caller 가 await chain 으로 sequence 를 보장할
  수 있음 (R-7 검증 필요).
- native binding 이 GC 로 회수되지 않는다는 가정. binding 객체가 단순 closure
  이면 GC 가능. 그러나 unbindPending 함수 존재 자체가 외부 자원 cleanup 계약을
  시사.

### 확인 안 한 것 중 영향 가능성
- approval-native-runtime.ts 의 stop / deliver dispatcher 코드. 만약 dispatcher
  가 in-flight delivery 를 기다린다면 본 race 는 unreachable.
- native binding 의 실제 구현 (telegram/whatsapp 등 채널별 어댑터). binding 객체
  가 weak ref 만 보유하면 GC 로 자연 해소되지만, native UI/통신 채널은 명시
  unbind 가 표준.
