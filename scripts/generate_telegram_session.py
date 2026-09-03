#!/usr/bin/env python3
"""Interactive, local-only Telethon session generator.

Run from the repository root after setting TG_API_ID, TG_API_HASH, and TG_PHONE
in the ignored .env file or shell environment. It never sends trading requests.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.errors import SessionPasswordNeededError  # type: ignore[import-untyped]
from telethon.sessions import StringSession  # type: ignore[import-untyped]


@dataclass(frozen=True)
class TelegramCredentials:
    api_id: int
    api_hash: str
    phone: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> TelegramCredentials:
        raw_api_id = environment.get("TG_API_ID", "").strip()
        if not raw_api_id.isdigit() or int(raw_api_id) <= 0:
            raise ValueError("TG_API_ID must be a positive numeric value")
        api_hash = environment.get("TG_API_HASH", "").strip()
        if not api_hash:
            raise ValueError("TG_API_HASH is required")
        phone = environment.get("TG_PHONE", "").strip()
        if not phone.startswith("+") or len(phone) < 8:
            raise ValueError("TG_PHONE must use international +<countrycode> format")
        return cls(api_id=int(raw_api_id), api_hash=api_hash, phone=phone)


def default_session_file(repository_root: Path) -> Path:
    return repository_root / "data" / "telegram" / "telegram.session"


def load_dotenv(path: Path) -> None:
    """Load only missing environment values from the gitignored local .env."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def generate_string_session(credentials: TelegramCredentials) -> str:
    client = TelegramClient(StringSession(), credentials.api_id, credentials.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.send_code_request(credentials.phone)
            code = input("Telegram verification code: ").strip()
            try:
                await client.sign_in(credentials.phone, code)
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)
        return cast(str, StringSession.save(client.session))
    finally:
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local Telegram StringSession")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="ignored local environment file; shell environment values take precedence",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.env_file)
    credentials = TelegramCredentials.from_environment(os.environ)
    session = asyncio.run(generate_string_session(credentials))
    print("\nSession generated. Add this value to the ignored .env file:")
    print(f"TELEGRAM_SESSION={session}")
    print("Do not paste this value into chat, logs, source control, or a shell history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
