"""Provision console-only deployment secrets; never trade or release safety gates.

Run from the approved production checkout after backup/migration, with --confirm.
No secret value is printed. Existing bot credentials and risk settings are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def atomic_private(path: Path, content: bytes, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise ValueError("provision target must not be a symlink")
    temporary = path.with_name(path.name + ".provision-" + secrets.token_hex(8))
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Replace the console login password using one line from standard input",
    )
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--origin", default="http://localhost:18081")
    parser.add_argument("--project", default="fatty-multi-exchange-trader")
    args = parser.parse_args()
    if not args.confirm or not re.fullmatch(r"[0-9]{5,30}", args.account_id):
        parser.error("explicit confirmation and verified account identity required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
        parser.error("invalid approved project")
    from urllib.parse import urlsplit

    origin = urlsplit(args.origin)
    if origin.path or origin.query or origin.fragment or origin.username or not origin.hostname:
        parser.error("origin must have no path, credentials or query")
    if origin.scheme != "https" and not (
        origin.scheme == "http" and origin.hostname == "localhost"
    ):
        parser.error("public access requires HTTPS; private access requires localhost")
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        raise ValueError("approved existing deployment environment required")
    encoded_password: str | None = None
    if args.password_stdin:
        login_password = sys.stdin.buffer.readline(4098).removesuffix(b"\n").removesuffix(b"\r")
        if not login_password or len(login_password) > 4096:
            raise ValueError("a bounded nonempty login password is required")
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(login_password, salt=salt, n=16384, r=8, p=1, dklen=32)
        encoded_password = f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"
    content = env_path.read_text()
    existing: dict[str, str] = {}
    for line in content.splitlines():
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line.strip())
        if match and match.group(1) == "CONSOLE_DB_PASSWORD":
            raw = match.group(2)
            if raw.startswith('"'):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise ValueError("ambiguous deployment quoting") from None
            elif raw.startswith("'") and raw.endswith("'"):
                raw = raw[1:-1]
            existing[match.group(1)] = raw
    password = existing.get("CONSOLE_DB_PASSWORD") or secrets.token_urlsafe(48)
    updates = {
        "CONSOLE_DB_PASSWORD": password,
        "CONSOLE_ACCOUNT_ID": args.account_id,
        "CONSOLE_ORIGIN": args.origin,
        "CONSOLE_ALLOW_LOCALHOST_HTTP": "1" if origin.scheme == "http" else "0",
        "CONSOLE_HOST_GID": str(os.getgid()),
        "CONSOLE_COMPOSE_PROJECT": args.project,
        "CONSOLE_COMPOSE_NETWORK": args.project + "_default",
    }
    lines = [
        line
        for line in content.splitlines()
        if not any(re.match(r"^\s*" + re.escape(key) + r"\s*=", line) for key in updates)
        and not re.match(r"^\s*CONSOLE_RP_ID\s*=", line)
    ]
    lines.extend(key + "=" + json.dumps(value) for key, value in updates.items())
    atomic_private(env_path, ("\n".join(lines) + "\n").encode())
    if encoded_password is not None:
        login_file = root / "runtime" / "console-password.hash"
        login_file.parent.mkdir(exist_ok=True)
        atomic_private(login_file, (encoded_password + "\n").encode(), 0o640)
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    stages = runtime / "console-stages"
    state = runtime / "console-ops"
    backups = runtime / "console-backups"
    for directory, mode in ((stages, 0o2770), (state, 0o700), (backups, 0o700)):
        if directory.is_symlink():
            raise ValueError("private directory must not be a symlink")
        directory.mkdir(exist_ok=True)
        os.chmod(directory, mode)
    private = runtime / "console-secret-private.pem"
    public = runtime / "console-public.pem"
    if private.is_symlink() or public.is_symlink():
        raise ValueError("key path must not be a symlink")
    if private.exists():
        if stat.S_IMODE(private.stat().st_mode) != 0o600:
            raise ValueError("existing private key permissions are unsafe")
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 3072:
            raise ValueError("existing private key is not an approved RSA key")
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        atomic_private(
            private,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    atomic_private(
        public,
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
        0o644,
    )
    ops_env = {
        "CONSOLE_OPS_WORKER": "1",
        "CONSOLE_ENABLED": "1",
        "CONSOLE_REPOSITORY_ROOT": str(root),
        "CONSOLE_OPS_STATE_DIR": str(state),
        "CONSOLE_BACKUP_DIR": str(backups),
        "CONSOLE_SECRET_STAGE_DIR": str(stages),
        "CONSOLE_SECRET_STAGE_GID": str(os.getgid()),
        "CONSOLE_SECRET_PUBLIC_KEY_FILE": str(public),
        "CONSOLE_SECRET_PRIVATE_KEY_FILE": str(private),
        "CONSOLE_COMPOSE_PROJECT": args.project,
        "CONSOLE_COMPOSE_NETWORK": args.project + "_default",
        "CONSOLE_HEARTBEAT_FILE": str(state / "heartbeat"),
    }
    atomic_private(
        runtime / "console-ops.env",
        (
            "\n".join(key + "=" + json.dumps(value) for key, value in ops_env.items()) + "\n"
        ).encode(),
    )
    if args.prepare_only:
        print(
            json.dumps(
                {"console_provisioning": "prepared", "role_pending": True, "secrets_printed": False}
            )
        )
        return 0
    # libpq/psql handles credentials inside the existing database container. The
    # role name is fixed; generated password is a quoted SQL literal on stdin,
    # never a process argument or captured output.
    literal = password.replace("'", "''")
    sql = "ALTER ROLE fatty_console_web LOGIN PASSWORD '" + literal + "';\n"
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("console role provisioning failed; apply approved migration first")
    print(
        json.dumps(
            {
                "console_provisioning": "complete",
                "private_access": True,
                "account_bound": True,
                "secrets_printed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Console provisioning refused; inspect non-secret prerequisites.")
        raise SystemExit(1) from None
