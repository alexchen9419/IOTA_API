#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.3 - Raspberry Pi Gateway 首次本地 provision 工具。

用途：在 Gateway 本機第一次部署時執行，建立長期 P-256 identity key、
gateway_id、公鑰 fingerprint，以及一次性的 initialization token。

注意：
- private key 只寫入本機 gateway_runtime，不會寫入 MySQL。
- initialization token 明文只輸出這一次，本機只保存 SHA-256。
- 若已 provision 但遺失 token，可用 --rotate-token 產生新的短效 token。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gateway_init_common import (
    INITIALIZATION_TOKEN_TTL_SECONDS,
    atomic_write_json,
    ensure_state_dir,
    expected_gateway_id,
    iso_utc,
    load_and_validate_identity,
    load_json_file,
    public_key_fingerprint_from_pem,
    sha256_text,
    state_paths,
    utc_now,
)


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UC1.3 Gateway identity provision")
    parser.add_argument(
        "--state-dir",
        default=os.getenv("UC1_3_GATEWAY_STATE_DIR"),
        help="Gateway runtime 狀態目錄；預設使用 .env 的 UC1_3_GATEWAY_STATE_DIR",
    )
    parser.add_argument(
        "--hardware-model",
        default=os.getenv("UC1_3_HARDWARE_MODEL", "RASPBERRY_PI"),
    )
    parser.add_argument(
        "--firmware-version",
        default=os.getenv("UC1_3_FIRMWARE_VERSION", "1.0.0"),
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="保留既有 Gateway identity，但作廢舊 initialization token 並重發",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else None
    base = ensure_state_dir(state_dir)
    private_key_path, identity_path, bootstrap_path = state_paths(base)

    if identity_path.exists() and private_key_path.exists():
        identity = load_and_validate_identity(base)
        if bootstrap_path.exists():
            bootstrap = load_json_file(bootstrap_path, "gateway_bootstrap.json")
            if bool(bootstrap.get("consumed")):
                print_json(
                    {
                        "status": "Error",
                        "msg": "此 Gateway 已完成 UC1.3 初始化，禁止重新 provision identity",
                        "data": {
                            "gateway_id": identity.get("gateway_id"),
                            "state_dir": str(base),
                        },
                    }
                )
                return 2
            if not args.rotate_token:
                print_json(
                    {
                        "status": "Error",
                        "msg": "Gateway 已 provision；token 明文不會保存。若遺失 token，請使用 --rotate-token",
                        "data": {
                            "gateway_id": identity.get("gateway_id"),
                            "state_dir": str(base),
                        },
                    }
                )
                return 2
    elif identity_path.exists() or private_key_path.exists():
        print_json(
            {
                "status": "Error",
                "msg": "Gateway runtime 狀態不完整：private key 與 identity 必須成對存在",
                "data": {"state_dir": str(base)},
            }
        )
        return 2
    else:
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        private_key_path.write_bytes(private_key_pem)
        try:
            os.chmod(private_key_path, 0o600)
        except OSError:
            pass

        public_key_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        fingerprint = public_key_fingerprint_from_pem(public_key_pem)
        gateway_id = expected_gateway_id(fingerprint)
        identity = {
            "schema_version": "1.0",
            "gateway_id": gateway_id,
            "curve": "SECP256R1",
            "public_key_pem": public_key_pem,
            "public_key_fingerprint": fingerprint,
            "hardware_model": str(args.hardware_model).strip() or "RASPBERRY_PI",
            "firmware_version": str(args.firmware_version).strip() or "1.0.0",
            "provisioned_at": iso_utc(),
        }
        atomic_write_json(identity_path, identity, mode=0o644)

    initialization_token = f"GWINIT_{secrets.token_urlsafe(32)}"
    now = utc_now()
    expires_at = now.timestamp() + INITIALIZATION_TOKEN_TTL_SECONDS
    from datetime import datetime, timezone

    bootstrap = {
        "schema_version": "1.0",
        "gateway_id": identity["gateway_id"],
        "initialization_token_hash": sha256_text(initialization_token),
        "issued_at": iso_utc(now),
        "token_expires_at": iso_utc(datetime.fromtimestamp(expires_at, timezone.utc)),
        "consumed": False,
        "consumed_at": None,
        "bound_family_id": None,
    }
    atomic_write_json(bootstrap_path, bootstrap, mode=0o600)

    print_json(
        {
            "status": "Success",
            "msg": "UC1.3 Gateway identity provision 完成；initialization_token 僅顯示本次",
            "data": {
                "gateway_id": identity["gateway_id"],
                "curve": identity["curve"],
                "public_key_fingerprint": identity["public_key_fingerprint"],
                "hardware_model": identity["hardware_model"],
                "firmware_version": identity["firmware_version"],
                "initialization_token": initialization_token,
                "expires_at": bootstrap["token_expires_at"],
                "state_dir": str(base),
                "private_key_path": str(private_key_path),
                "identity_path": str(identity_path),
                "bootstrap_path": str(bootstrap_path),
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
