# Telegram E2E 인프라

CAND-031/032/033 (auto-reply / channel 의존 결함) 의 production end-to-end 검증을 위한
telegram bot + user account driver 인프라. **NEXT.md 의 telegram 관련 결정을 따를 때만 읽어라**.

## 결정 배경 (2026-05-15)

### 왜 telegram 이 필요한가

CAND-031/032/033 의 production execution path 가 channel adapter 를 trigger 로 함.
unit-level 시나리오는 backend mock 으로 결함 발현했지만, production e2e 는 실 channel
message → openclaw 가 받아 처리 → 결함 발현 path 통과를 요구. openclaw 의 channel
adapter 중 telegram extension 이 가장 성숙 + maintainer infra 와 동일 (bot-to-bot
QA lane 또는 user-driver 패턴).

### 왜 Telethon 인가 (TDLib 폐기 사유)

처음 시도: openclaw 의 `scripts/e2e/telegram-user-driver.py` 재사용 (TDLib 1.8.0 via
homebrew). 결과: `UPDATE_APP_TO_LOGIN` 에러 — TDLib 1.8.0 (2022년) 이 2026년 telegram
server 의 신 layer 요구 못 맞춤. TDLib master 빌드는 30-60분 + ABI 호환성 risk
(openclaw user-driver 가 1.8.0 ABI 기준).

전환: **Telethon (pure python MTProto, 1.43.2)**. pip 한 줄 + 자체 layer maintenance.
driver 라이브러리 차이는 **production code 검증과 무관** — driver 는 단순 메시지
send/receive 만, e2e 결과 (production bundle 결함 발현) 에 영향 0. openclaw 일관성
손실은 cosmetic (PR 시 maintainer 친숙도) 만.

### 왜 user account driver 인가 (driver bot 폐기 사유)

처음 시도: driver bot + sut bot 두 개 + bot-to-bot QA lane. 결과: telegram 의 hard
restriction — bot 은 다른 bot 의 메시지를 받을 수 없음 (privacy / mention 무관).
driver bot send → sut bot getUpdates count=0 검증.

전환: **user account driver** (Telethon, 사용자 telegram 계정). user → bot 통신은
telegram 정책 100% 통과. 사용자 본인의 telegram 자동화. driver bot 은 잔존하지만
미사용 (잔존 이유: openclaw rtt-config 가 driver token 받는 패턴 호환).

## 인프라 인벤토리

### Bots (BotFather 발급, 2026-05-15)
- **SUT bot**: `@openclaw_audit_sut_bot` (id 8758244120) — openclaw cli 가 listen
- **Driver bot**: `@openclaw_audit_driver_bot` (id 8804962742) — 미사용 (잔존)
- 두 bot 다 group privacy OFF 권장 (실제 sut 는 ON 유지 — mention 으로 충분)
- 두 bot 다 audit group 의 administrator

### Group
- ID `-5243821808` (basic group, type="group")
- 멤버: lucas (admin) + sut bot (admin) + driver bot (admin)
- supergroup 변환 필요 시: telegram client → group 정보 → "Convert to supergroup"

### User account
- lucas 본인 telegram 계정 (id 8419869822)
- driver 자동화 주체 — group 에 메시지 send + sut bot 응답 관찰

### Telegram API credentials
- https://my.telegram.org → API development tools 에서 발급 (api_id 8자리, api_hash 32자 hex)
- App title: `OpenClawAudit`, Short name: `openclawaudit` (특수문자 거부 주의)

### Secret 파일 (`~/.openclaw-audit-secrets/telegram.env`, 600 권한)
- `OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN` (46자)
- `OPENCLAW_QA_TELEGRAM_DRIVER_BOT_TOKEN` (46자, 잔존, 미사용)
- `OPENCLAW_QA_TELEGRAM_GROUP_ID` (-5243821808)
- `OPENCLAW_QA_TELEGRAM_API_ID` (8자리 정수)
- `OPENCLAW_QA_TELEGRAM_API_HASH` (32자 hex)
- `OPENCLAW_QA_TELEGRAM_USER_PHONE` (+국가코드 형태)

### Session (`~/.openclaw-audit-secrets/telethon.session`, 600 권한)
- Telethon SQLite session (28KB)
- 첫 인증 후 영구 — 재인증 불필요 (telegram 정책 변경 또는 session 만료 시 재실행)
- 재인증 명령:
  ```bash
  set -a && source ~/.openclaw-audit-secrets/telegram.env && set +a
  /tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/telegram_login.py
  ```

### Audit venv (`/tmp/openclaw-audit-venv/`, 진짜 venv)
- python 3.14 venv (`pyvenv.cfg` 존재)
- 설치 패키지: telethon 1.43.2, pyyaml 6.0.3
- 재생성 명령 (필요 시):
  ```bash
  rm -rf /tmp/openclaw-audit-venv && python3.14 -m venv /tmp/openclaw-audit-venv \
    && /tmp/openclaw-audit-venv/bin/pip install --quiet --upgrade pip \
    && /tmp/openclaw-audit-venv/bin/pip install --quiet telethon pyyaml
  ```

### Audit driver code
- `skills/real-behavior-proof/harness/telegram_login.py` — 첫 phone 인증
- `skills/real-behavior-proof/harness/telegram_driver.py` — TelegramDriver context
  manager (send / get_recent_after / get_recent)

### TDLib 잔존 (미사용)
- `/opt/homebrew/lib/libtdjson.1.8.0.dylib` (homebrew install, 1.8.0)
- `~/.openclaw-audit-secrets/tdlib-state/` (빈 디렉터리)
- 제거 권장: `brew uninstall tdlib && rm -rf ~/.openclaw-audit-secrets/tdlib-state`

## 사용 패턴

### Driver send + 응답 관찰

```python
from skills.real_behavior_proof.harness.telegram_driver import TelegramDriver
import time

with TelegramDriver() as d:
    marker = f"trial-{int(time.time())}"
    msg_id = d.send(f"@openclaw_audit_sut_bot {marker}")
    # ... openclaw process 가 받아 처리 (별도 spawn)
    # ... audit 가 process internal 측정 (stdout/log/sqlite)
    # 선택: sut bot 응답 본문 관찰
    replies = d.get_recent_after(msg_id, timeout=5)
    for r in replies:
        if r["sender_id"] != d.me_id:  # sut bot 의 응답 (또는 다른 bot)
            print(f"reply: {r['text']}")
```

### CLI sanity check

```bash
set -a && source ~/.openclaw-audit-secrets/telegram.env && set +a
/tmp/openclaw-audit-venv/bin/python skills/real-behavior-proof/harness/telegram_driver.py
# 출력: driver_ready / sent / recent_after JSON
```

### Sut bot 측 응답 관찰 (driver wrapper 외)

bot API getUpdates 직접 호출:
```bash
curl -s "https://api.telegram.org/bot${OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN}/getUpdates" \
  | python3 -m json.tool
```

## 결함 trigger 의 어려움 (CAND 시나리오 작성 시 주의)

CAND-031/032/033 의 결함은 **backend reject** 또는 **race timing** — production 에서
자연스럽게 reproduce 어려움. unit-level 은 backend mock 으로 깨끗 trigger, e2e 는 mock 사용 못 함.

옵션:
1. **Mock LLM 의 invalid response** → activeSession.steer 가 처리 도중 throw → backend
   reject 로 propagate. production code 0 변경, mock LLM 서버만 controlled. 결함
   propagate path 검증 필요 (LLM client 의 throw 가 backend reject 로 어떻게 연결되는지).
2. **Production race condition** — session 종료 후 메시지 send 등. timing 정확 어려움.
3. **Plugin 으로 backend wrap** — production code 변경 (production-faithful 깨짐, 폐기).

옵션 1 이 가장 깨끗. 시나리오 별로 mock LLM 의 fault injection 형태 결정.

## 다음 단계 (Task 10~13)

이 인프라 위에 다음 작업 (이번 세션 또는 후속):
- **Task 10**: isolated openclaw home + sut config 자동 생성 (`/tmp/openclaw-audit-proof-{uuid}/.openclaw/`).
  npm-telegram-rtt-config.mjs 패턴 (gateway local mode + telegram channel adapter
  + mock OpenAI provider) 재사용. allowFrom 에 user_id (8419869822) 등록.
- **Task 11**: mock LLM 서버 spawn — `scripts/e2e/mock-openai-server.mjs` 재사용 (HTTP
  server, port assign, SUCCESS_MARKER env). fault injection 가능하도록 환경 변수 또는
  endpoint 추가.
- **Task 12**: openclaw cli (`openclaw.mjs`) 부팅 + sut bot listen 검증. driver 가 group
  에 mention 메시지 send → sut bot 받음 + mock LLM 응답 → driver 가 sut bot 응답 관찰.
- **Task 13**: CAND-032 e2e 시나리오 — backend reject trigger 방법 결정 + 측정 방법
  (process stdout 의 unhandledRejection 패턴 grep, 또는 openclaw 자체 로그) + transition 기록.

## 트러블슈팅

### `UPDATE_APP_TO_LOGIN` (TDLib)
TDLib 너무 오래됨. Telethon 으로 전환 (위 결정 배경 참조).

### Telethon `FloodWaitError`
자주 시도 시 telegram 자동탐지. 안내된 시간 (분/시간) 만큼 대기 후 재시도. low traffic
(CAND 당 1-3 메시지) 라 정상 사용 시 발생 거의 없음.

### `is_user_authorized() == False`
session 만료 또는 telegram 정책 변경. `telegram_login.py` 재실행.

### Sut bot 이 driver 메시지 못 받음
- driver 가 user account 인지 확인 (TelegramDriver.me_id == 사용자 telegram id)
- driver 가 bot 이면 telegram bot-to-bot 차단 — user account 로 전환 필수
- mention 형식 정확 (`@openclaw_audit_sut_bot text`, username 오타 주의)

### Group 가 supergroup 으로 변환된 경우
group ID 가 변경됨 (`-100xxxxxxxxx` 형태). secret 파일의 GROUP_ID 갱신 + getUpdates
로 새 ID 재확인.

### Personal account ban risk
low traffic 이라 자동탐지 위험 매우 낮음. 그래도 우려 시:
- audit 끝나면 my.telegram.org 의 application 삭제 + telethon.session 파기
- burner phone (Google Voice / TextNow) 으로 별도 telegram 계정 생성 후 재인증

## 보안 노트

- secret 파일 (`~/.openclaw-audit-secrets/`) audit repo 외부 — git 추적 0
- session 파일 + bot tokens **commit/push 절대 금지**
- Claude Code 출력 시 token 값 redact (length / 형식만 표시)
- gateway token (`~/.openclaw/openclaw.json` 의 `gateway.auth.token`) 도 평문 — production
  config 직접 출력 금지
- isolated openclaw home (`/tmp/openclaw-audit-proof-{uuid}/.openclaw/`) 의 sut config
  도 sut bot token 평문 포함 — temp 디렉터리 cleanup 후 잔존물 검사
