"""Dedicated host executor launcher; Docker and secrets never enter the web process."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path


def prepare_host_environment(environ: Mapping[str, str]) -> dict[str, str]:
    env = dict(environ)
    if env.get("CONSOLE_OPS_WORKER") != "1":
        raise ValueError("dedicated host worker marker is required")
    root = Path(env["CONSOLE_REPOSITORY_ROOT"]).resolve(strict=True)
    files = [root / "docker-compose.yml"]
    state_path = Path(env["CONSOLE_OPS_STATE_DIR"])
    if state_path.is_symlink():
        raise ValueError("invalid private host state directory")
    state_dir = state_path.resolve(strict=True)
    for name in ("runtime.compose.json", "release.compose.json"):
        path = state_dir / name
        if path.exists():
            if (
                path.is_symlink()
                or not path.is_file()
                or (os.name != "nt" and path.stat().st_mode & 0o077)
            ):
                raise ValueError("invalid private Compose override")
            files.append(path)
    env["COMPOSE_FILE"] = os.pathsep.join(str(p) for p in files)
    runtime_override = state_dir / "runtime.compose.json"
    if runtime_override.exists():
        if runtime_override.is_symlink():
            raise ValueError("invalid private runtime config")
        data = json.loads(runtime_override.read_text())
        credential_fields = (
            "BITGET_API_KEY",
            "BITGET_API_SECRET",
            "BITGET_API_PASSPHRASE",
            "TG_BOT_TOKEN",
        )
        for service in (
            "console-worker",
            "dispatcher-bitget",
            "operator-bot",
            "notification-sender",
        ):
            for key, value in (
                data.get("services", {}).get(service, {}).get("environment", {}).items()
            ):
                if key in credential_fields and isinstance(value, str):
                    env[key] = value.replace("$$", "$")
    project = env.get("CONSOLE_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise ValueError("exact approved Compose project is required")
    env["COMPOSE_PROJECT_NAME"] = project

    # Postgres is private to Compose; discover exactly its container, not host services.
    def run(args: list[str]) -> str:
        result = subprocess.run(
            args, cwd=root, env=env, capture_output=True, timeout=20, check=False, shell=False
        )
        if result.returncode:
            raise ValueError("private database container discovery failed")
        return result.stdout.decode().strip()

    container_id = run(["/usr/bin/docker", "compose", "ps", "-q", "postgres"])
    if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
        raise ValueError("exact private database container is unavailable")
    networks = json.loads(
        run(
            [
                "/usr/bin/docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                container_id,
            ]
        )
    )
    network = env.get("CONSOLE_COMPOSE_NETWORK", env["COMPOSE_PROJECT_NAME"] + "_default")
    address = networks.get(network, {}).get("IPAddress")
    import ipaddress

    if not address or not ipaddress.ip_address(address).is_private:
        raise ValueError("approved private database network is unavailable")
    env.update(
        {
            "PGHOST": address,
            "PGPORT": "5432",
            "PGUSER": "fatty_app",
            "PGDATABASE": "fatty_trader",
            "PGPASSWORD": env["POSTGRES_PASSWORD"],
        }
    )
    env.pop("DATABASE_URL", None)
    return env


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register-release")
    parser.add_argument("--enroll-owner", action="store_true")
    parser.add_argument("--invite-approver", action="store_true")
    parser.add_argument("--display-name")
    parser.add_argument("--recipient")
    parser.add_argument("--image")
    parser.add_argument("--approval-reference")
    args = parser.parse_args()
    if (
        sum(
            bool(value)
            for value in (args.register_release, args.enroll_owner, args.invite_approver)
        )
        > 1
    ):
        parser.error("choose exactly one administrative operation")
    if args.enroll_owner or args.invite_approver:
        if not args.display_name or args.image:
            parser.error("enrollment requires --display-name and forbids release --image")
        if args.invite_approver and (not args.recipient or not args.approval_reference):
            parser.error(
                "distinct approver invitation requires --recipient and --approval-reference"
            )
        if args.enroll_owner and (args.recipient or args.approval_reference):
            parser.error("initial owner delivery uses only the configured TG_OPERATOR_ID")
    elif args.display_name or args.recipient:
        parser.error("enrollment options require --enroll-owner or --invite-approver")
    env = prepare_host_environment(os.environ)
    os.environ.update(env)
    if args.enroll_owner or args.invite_approver:
        from fatty_trader.console.auth import AuthConfig, AuthService
        from fatty_trader.console.store import ConsoleStore

        service = AuthService(ConsoleStore(), AuthConfig.from_environ(env))
        if service.config.scope != {
            "account_id": env.get("CONSOLE_ACCOUNT_ID")
            or env.get("BITGET_ACCOUNT_ID")
            or "configured-bitget",
            "venue": "bitget",
            "environment": "LIVE",
        }:
            raise ValueError("enrollment requires the exact configured LIVE account")
        metadata = asyncio.run(
            service.issue_invitation(
                env,
                args.display_name,
                ["owner"],
                [service.config.scope],
                approval_reference=args.approval_reference,
                recipient=args.recipient,
            )
        )
        print(json.dumps(metadata, sort_keys=True))
        return
    if args.register_release:
        if not args.image or not args.approval_reference:
            parser.error("immutable --image and --approval-reference required")
        from fatty_trader.console.runner import HostOperationsRunner
        from fatty_trader.console.store import ConsoleStore

        print(
            json.dumps(
                HostOperationsRunner(env, ConsoleStore()).register_release(
                    args.register_release, args.image, args.approval_reference
                ),
                sort_keys=True,
            )
        )
        return
    if args.image or args.approval_reference:
        parser.error("registration options require --register-release")
    from fatty_trader.console.worker import run_console_worker

    asyncio.run(run_console_worker(env, host=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "console-ops-worker failed: private configuration or executor unavailable", flush=True
        )
        raise SystemExit(1) from None
