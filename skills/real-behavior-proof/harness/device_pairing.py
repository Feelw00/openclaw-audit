#!/usr/bin/env python3
"""
skills/real-behavior-proof/harness/device_pairing.py

isolated state-dir 에 device pairing seed.

production 의 device-identity / device-auth-store / pairing-files 형식 그대로 작성:
- {state_dir}/identity/device.json         — device identity (ed25519 keypair + deviceId)
- {state_dir}/identity/device-auth.json    — per-role auth tokens
- {state_dir}/devices/paired.json          — paired devices registry
- {state_dir}/devices/pending.json         — pending pairings (empty)

참조: `scripts/e2e/lib/upgrade-survivor/update-restart-auth.sh:140-225`
      `src/infra/device-identity.ts`, `src/infra/device-auth-store.ts`

CAND-038 e2e 의 connect handshake 에서 `NOT_PAIRED: device identity required` 우회용.
audit ws client 는 paired device 의 keypair 로 device payload 서명 → connect frame 에 첨부 →
gateway 가 paired.json 으로 검증 + auth token 인정.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any


def seed_paired_device(
    *,
    state_dir: Path,
    display_name: str = "audit-proof-probe",
    client_id: str = "openclaw-cli",
    client_mode: str = "cli",
    role: str = "operator",
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """state_dir 에 paired device seed. Node script 호출로 ed25519 keypair + PEM 인코딩 위임.

    반환:
      {
        deviceId, publicKeyPem, privateKeyPem, publicKeyRawBase64Url,
        token, role, scopes,
        files: { device, deviceAuth, paired, pending }
      }
    """
    scopes_list = scopes or ["operator.read", "operator.write"]
    state_dir = Path(state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    node_script = textwrap.dedent(
        f"""\
        import crypto from "node:crypto";
        import fs from "node:fs";
        import path from "node:path";

        const stateDir = {json.dumps(str(state_dir))};
        const displayName = {json.dumps(display_name)};
        const clientId = {json.dumps(client_id)};
        const clientMode = {json.dumps(client_mode)};
        const role = {json.dumps(role)};
        const scopes = {json.dumps(scopes_list)};

        const base64UrlEncode = (buf) =>
          buf.toString("base64").replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
        const ed25519SpkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
        const {{ publicKey, privateKey }} = crypto.generateKeyPairSync("ed25519");
        const publicKeyPem = publicKey.export({{ type: "spki", format: "pem" }});
        const privateKeyPem = privateKey.export({{ type: "pkcs8", format: "pem" }});
        const spki = crypto.createPublicKey(publicKeyPem).export({{ type: "spki", format: "der" }});
        const rawPublicKey =
          spki.length === ed25519SpkiPrefix.length + 32 &&
          spki.subarray(0, ed25519SpkiPrefix.length).equals(ed25519SpkiPrefix)
            ? spki.subarray(ed25519SpkiPrefix.length)
            : spki;
        const publicKeyRawBase64Url = base64UrlEncode(rawPublicKey);
        const deviceId = crypto.createHash("sha256").update(rawPublicKey).digest("hex");
        const token = base64UrlEncode(crypto.randomBytes(32));
        const now = Date.now();

        function writeJson(filePath, value) {{
          fs.mkdirSync(path.dirname(filePath), {{ recursive: true, mode: 0o700 }});
          fs.writeFileSync(filePath, `${{JSON.stringify(value, null, 2)}}\\n`, {{ mode: 0o600 }});
          try {{ fs.chmodSync(filePath, 0o600); }} catch {{}}
        }}

        const devicePath = path.join(stateDir, "identity", "device.json");
        const deviceAuthPath = path.join(stateDir, "identity", "device-auth.json");
        const pairedPath = path.join(stateDir, "devices", "paired.json");
        const pendingPath = path.join(stateDir, "devices", "pending.json");

        writeJson(devicePath, {{
          version: 1,
          deviceId,
          publicKeyPem,
          privateKeyPem,
          createdAtMs: now,
        }});
        writeJson(deviceAuthPath, {{
          version: 1,
          deviceId,
          tokens: {{
            [role]: {{ token, role, scopes, updatedAtMs: now }},
          }},
        }});
        writeJson(pairedPath, {{
          [deviceId]: {{
            deviceId,
            publicKey: publicKeyRawBase64Url,
            displayName,
            platform: process.platform,
            clientId,
            clientMode,
            role,
            roles: [role],
            scopes,
            approvedScopes: scopes,
            tokens: {{
              [role]: {{ token, role, scopes, createdAtMs: now }},
            }},
            createdAtMs: now,
            approvedAtMs: now,
          }},
        }});
        writeJson(pendingPath, {{}});

        process.stdout.write(JSON.stringify({{
          deviceId,
          publicKeyPem,
          privateKeyPem,
          publicKeyRawBase64Url,
          token,
          role,
          scopes,
          files: {{
            device: devicePath,
            deviceAuth: deviceAuthPath,
            paired: pairedPath,
            pending: pendingPath,
          }},
        }}));
        """
    )

    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"device pairing seed failed (rc={proc.returncode}): "
            f"stderr={proc.stderr[-500:]} stdout={proc.stdout[-500:]}"
        )
    try:
        result = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"device pairing stdout JSON parse: {e}: {proc.stdout[:500]}") from e
    return result


# CLI for ad-hoc inspection
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="device_pairing.py — seed paired device into state-dir")
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--role", default="operator")
    ap.add_argument("--client-mode", default="cli")
    args = ap.parse_args()

    out = seed_paired_device(
        state_dir=Path(args.state_dir),
        role=args.role,
        client_mode=args.client_mode,
    )
    print(json.dumps(out, indent=2))
