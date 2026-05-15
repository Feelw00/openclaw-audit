#!/usr/bin/env python3
"""telegram e2e 시나리오용 sut openclaw home + config.

env_isolate.isolated_home() 위에 telegram channel + mock openai provider overlay.
CAND-031/032/033 (auto-reply queue / reply-run-registry / collect drain) e2e 시 sut
openclaw process spawn 환경. driver 는 user account (Telethon) → group → sut bot.

config 골격: scripts/e2e/npm-telegram-rtt-config.mjs (driver bot 패턴) 를 user account
driver 패턴으로 치환 — allowFrom 에 driver bot id 대신 user.id 등록.

사용:
  with telegram_sut_home(sut_bot_token=..., mock_llm_port=..., telegram_group_id=...,
                        driver_user_id=...) as env:
      proc = subprocess.Popen([node, openclaw_mjs], env=env["env"], ...)
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from env_isolate import isolated_home


@contextmanager
def telegram_sut_home(
    *,
    sut_bot_token: str,
    mock_llm_port: int,
    telegram_group_id: int,
    driver_user_id: int,
    require_oauth: bool = False,
    proof_id: str | None = None,
) -> Iterator[dict]:
    """isolated_home + telegram channel + mock openai provider overlay.

    Args:
        sut_bot_token: SUT bot token (env TELEGRAM_BOT_TOKEN 으로 sut process 에 주입).
        mock_llm_port: mock-openai-server 가 bind 한 loopback port (baseUrl 에 반영).
        telegram_group_id: audit group id (signed int, e.g. -5243821808).
        driver_user_id: telethon driver user account id (allowFrom 등록 대상).
        require_oauth: env_isolate 의 oauth profile 복사 여부 (telegram-only 면 False).
        proof_id: 디렉터리 이름에 사용 (디버깅 편의).

    Yields:
        env_isolate.isolated_home 의 yield 와 동일하되 env 에 TELEGRAM_BOT_TOKEN +
        OPENAI_API_KEY 추가. config 는 telegram channel 활성화 + openai provider 가
        mock url 향함.
    """
    with isolated_home(require_oauth=require_oauth, proof_id=proof_id) as base:
        cfg_path = Path(base["home"]) / "openclaw.json"
        cfg = json.loads(cfg_path.read_text())

        # gateway.bind 는 enum (loopback/lan/tailnet/auto/custom).
        cfg.setdefault("gateway", {})["bind"] = "loopback"
        cfg["gateway"]["auth"] = {"mode": "none"}  # local-only proof 환경

        # config schema 엄격 — meta 의 null/unknown 키 제거.
        meta = cfg.get("meta") or {}
        cfg["meta"] = {
            k: v
            for k, v in meta.items()
            if v is not None and k in ("lastTouchedVersion", "lastTouchedAt")
        }

        # openai provider → mock LLM
        cfg.setdefault("models", {}).setdefault("providers", {})["openai"] = {
            "api": "openai-responses",
            "apiKey": {
                "source": "env",
                "provider": "default",
                "id": "OPENAI_API_KEY",
            },
            "baseUrl": f"http://127.0.0.1:{mock_llm_port}/v1",
            "request": {"allowPrivateNetwork": True},
            "models": [
                {
                    "id": "gpt-5.5",
                    "name": "gpt-5.5",
                    "api": "openai-responses",
                    "contextWindow": 128000,
                }
            ],
        }

        # agent defaults → openai/gpt-5.5
        cfg.setdefault("agents", {}).setdefault("defaults", {})["model"] = {
            "primary": "openai/gpt-5.5"
        }
        cfg["agents"]["defaults"]["models"] = {
            "openai/gpt-5.5": {
                "params": {"transport": "sse", "openaiWsWarmup": False},
            }
        }
        cfg["agents"]["defaults"].pop("agentRuntime", None)  # codex runtime 제거
        cfg["agents"]["list"] = [
            {
                "id": "main",
                "default": True,
                "name": "Main",
                "workspace": "~/workspace",
                "model": {"primary": "openai/gpt-5.5"},
            }
        ]

        # plugins → telegram + openai 만
        cfg.setdefault("plugins", {})
        cfg["plugins"]["enabled"] = True
        cfg["plugins"]["allow"] = ["telegram", "openai"]
        cfg["plugins"].setdefault("entries", {})
        cfg["plugins"]["entries"]["telegram"] = {"enabled": True}
        cfg["plugins"]["entries"]["openai"] = {"enabled": True}
        cfg["plugins"]["entries"].pop("codex", None)

        # channels.telegram → user account driver 패턴
        user_id_str = str(driver_user_id)
        group_id_str = str(telegram_group_id)
        cfg["channels"] = {
            "telegram": {
                "enabled": True,
                "botToken": {
                    "source": "env",
                    "provider": "default",
                    "id": "TELEGRAM_BOT_TOKEN",
                },
                "dmPolicy": "allowlist",
                "allowFrom": [user_id_str],
                "defaultTo": user_id_str,
                "groupPolicy": "allowlist",
                "groupAllowFrom": [user_id_str],
                "groups": {
                    group_id_str: {
                        "requireMention": True,
                        "allowFrom": [user_id_str],
                    }
                },
            }
        }

        # group 에 assistant reply 자동 visible — npm-telegram-rtt-config.mjs:103-110 패턴.
        # 2026.4.27+ 에서 지원, 우리 build 는 2026.5.14.
        cfg["messages"] = {
            **cfg.get("messages", {}),
            "groupChat": {
                **(cfg.get("messages", {}) or {}).get("groupChat", {}),
                "visibleReplies": "automatic",
            },
        }

        cfg_path.write_text(json.dumps(cfg, indent=2))

        env = dict(base["env"])
        # config 위치 명시 (src/utils.ts:127 — OPENCLAW_CONFIG_PATH 가 highest priority).
        # OPENCLAW_HOME 은 env_isolate 가 .openclaw 자체로 set 한 그대로 유지 — logs/state 가
        # 그 안에 떨어지면 cleanup 시 함께 삭제됨.
        env["OPENCLAW_CONFIG_PATH"] = str(cfg_path)
        env["TELEGRAM_BOT_TOKEN"] = sut_bot_token
        env["OPENAI_API_KEY"] = "sk-mock-not-validated-by-mock-server"

        yield {
            **base,
            "env": env,
            "mock_llm_port": mock_llm_port,
            "telegram_group_id": telegram_group_id,
            "driver_user_id": driver_user_id,
        }


def main_preview() -> int:
    """CLI sanity — config 생성 후 dump."""
    import argparse
    import os
    import sys

    ap = argparse.ArgumentParser(description="telegram_sut_home — preview generated config")
    ap.add_argument("--hold-seconds", type=int, default=2)
    args = ap.parse_args()

    sut_token = os.environ.get("OPENCLAW_QA_TELEGRAM_SUT_BOT_TOKEN")
    group_id = os.environ.get("OPENCLAW_QA_TELEGRAM_GROUP_ID")
    if not sut_token or not group_id:
        print(
            "set -a && source ~/.openclaw-audit-secrets/telegram.env && set +a 먼저 실행",
            file=sys.stderr,
        )
        return 1

    # driver user id 는 telegram-e2e.md 에 명시된 lucas 본인 계정 (8419869822)
    driver_user_id = int(os.environ.get("OPENCLAW_QA_TELEGRAM_DRIVER_USER_ID", "8419869822"))

    import time

    with telegram_sut_home(
        sut_bot_token=sut_token,
        mock_llm_port=18801,  # placeholder — Task 11 에서 real allocation
        telegram_group_id=int(group_id),
        driver_user_id=driver_user_id,
        proof_id="preview",
    ) as env:
        cfg_path = Path(env["home"]) / "openclaw.json"
        print(json.dumps({"home": str(env["home"]), "gateway_port": env["gateway_port"]}, indent=2))
        print(f"\nconfig at {cfg_path}:")
        print(cfg_path.read_text())
        time.sleep(args.hold_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_preview())
