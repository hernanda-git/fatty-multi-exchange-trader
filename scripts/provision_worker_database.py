"""Provision the isolated runtime DB role before Compose service cutover.

Run with the approved checkout's Python environment and --confirm. The existing
bootstrap/migration role stays fatty_app. No password is emitted or placed in a
process argument. Re-running preserves the private WORKER_DB_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from provision_console import atomic_private

from fatty_trader.storage.worker_privileges import WORKER_PRIVILEGES_SQL


def prepare_worker_secret(env_path: Path) -> str:
    if env_path.is_symlink() or not env_path.is_file():
        raise ValueError("approved existing deployment environment required")
    content = env_path.read_text()
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = re.fullmatch(
            r"\s*(WORKER_DB_PASSWORD|POSTGRES_PASSWORD|CONSOLE_DB_PASSWORD)=(.*)", line
        )
        if match:
            name, value = match.groups()
            if name in values:
                raise ValueError("duplicate database secret configuration")
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if not isinstance(value, str):
                raise ValueError("invalid database secret configuration")
            values[name] = value
    password = values.get("WORKER_DB_PASSWORD") or secrets.token_urlsafe(48)
    if (
        len(password) < 32
        or any(character in password for character in "\r\n\x00")
        or password in {values.get("POSTGRES_PASSWORD"), values.get("CONSOLE_DB_PASSWORD")}
    ):
        raise ValueError("a separate strong runtime database credential is required")
    lines = [
        line for line in content.splitlines() if not re.match(r"^\s*WORKER_DB_PASSWORD\s*=", line)
    ]
    # Single quoting prevents Compose dollar interpolation of an existing secret.
    if "'" in password:
        raise ValueError("runtime database secret must support private dotenv quoting")
    lines.append("WORKER_DB_PASSWORD='" + password + "'")
    atomic_private(env_path, ("\n".join(lines) + "\n").encode())
    return password


def provision_worker_role(root: Path, password: str) -> None:
    literal = password.replace("'", "''")
    sql = (
        "BEGIN;\nSET LOCAL standard_conforming_strings = on;\n"
        + WORKER_PRIVILEGES_SQL
        + "\nALTER ROLE fatty_worker LOGIN PASSWORD '"
        + literal
        + "';\nCOMMIT;\n"
    )
    env = dict(os.environ)
    env["WORKER_DB_PASSWORD"] = password
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
        ],
        input=sql.encode(),
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("worker database provisioning failed; runtime cutover forbidden")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("explicit confirmation required")
    root = Path(__file__).resolve().parent.parent
    password = prepare_worker_secret(root / ".env")
    provision_worker_role(root, password)
    print(json.dumps({"worker_database_provisioning": "complete", "secrets_printed": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Worker database provisioning refused; runtime cutover forbidden.")
        raise SystemExit(1) from None
