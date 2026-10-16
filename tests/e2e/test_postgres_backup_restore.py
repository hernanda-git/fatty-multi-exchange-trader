"""Real pg_dump/pg_restore against a private, short-lived local server only."""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_TABLES = (
    "dispatches",
    "dispatch_transitions",
    "positions",
    "orders",
    "fills",
    "live_order_intents",
    "console_jobs",
    "console_job_steps",
    "console_snapshots",
    "console_event_outbox",
)


def test_backup_and_online_isolated_restore_with_real_postgres_binaries():
    binaries = ("bash", "initdb", "pg_ctl", "psql", "createdb", "dropdb", "pg_dump", "pg_restore")
    if os.name != "posix" or any(shutil.which(binary) is None for binary in binaries):
        pytest.skip("requires local PostgreSQL server/client binaries and POSIX shell")
    if os.geteuid() == 0:
        pytest.skip("private initdb must run as a non-root user")

    # No external DSN is accepted and TCP is disabled. Short paths also avoid
    # Unix-domain socket length limits under deeply nested pytest directories.
    with tempfile.TemporaryDirectory(prefix="fatty-backup-") as temporary:
        directory = Path(temporary)
        data = directory / "data"
        socket = directory / "socket"
        socket.mkdir(mode=0o700)
        admin = "isolated_backup_admin"
        source = "isolated_backup_source"
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(directory),
            "PGHOST": str(socket),
            "PGPORT": "55499",
            "PGUSER": admin,
            "PGDATABASE": "postgres",
            "POSTGRES_ADMIN_USER": admin,
            "POSTGRES_USER": "worker_must_not_be_used",
            "POSTGRES_DB": source,
            "BACKUP_DIR": str(directory / "backups"),
            "BACKUP_RETAIN_DAYS": "14",
        }

        def command(*args, check=True):
            return subprocess.run(
                list(map(str, args)),
                env=env,
                text=True,
                capture_output=True,
                check=check,
            )

        def query(database, sql):
            return command(
                "psql",
                "--no-psqlrc",
                "--dbname",
                database,
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
                "--command",
                sql,
            ).stdout.strip()

        # The shim only removes Compose's container prefix; every database tool
        # and its archive/SQL processing below is the real installed binary.
        compose = directory / "compose"
        compose.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "assert args[:5] == ['exec', '-T', '--user', 'postgres', 'postgres']\n"
            "os.execvp(args[5], args[5:])\n"
        )
        compose.chmod(0o700)
        env["COMPOSE_BIN"] = str(compose)
        command("initdb", "-D", data, "-U", admin, "-A", "trust", "--no-locale")
        started = False
        try:
            command(
                "pg_ctl",
                "-D",
                data,
                "-l",
                directory / "postgres.log",
                "-w",
                "start",
                "-o",
                f"-F -h '' -k {socket} -p 55499",
            )
            started = True
            command("createdb", "--template=template0", source)
            query(source, "CREATE TABLE schema_migrations(version integer PRIMARY KEY)")
            query(source, "INSERT INTO schema_migrations VALUES (27)")
            for table in CORE_TABLES:
                query(source, f"CREATE TABLE {table}(id integer PRIMARY KEY)")
                query(source, f"INSERT INTO {table} VALUES (1), (2)")

            result = command("bash", ROOT / "scripts" / "backup_postgres.sh")
            assert "backup_created=" in result.stdout
            backups = list(Path(env["BACKUP_DIR"]).iterdir())
            assert len(backups) == 1
            backup = backups[0]
            assert stat.S_IMODE(backup.stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(env["BACKUP_DIR"]).stat().st_mode) == 0o700
            env["CONFIRM_ISOLATED_RESTORE"] = "YES"
            env["RESTORE_EXPECTED_SCHEMA_VERSION"] = "27"
            result = command("bash", ROOT / "scripts" / "verify_restore_postgres.sh", backup)
            assert "restore_verified=" in result.stdout
            assert "restore_verification_cleanup_verified=" in result.stdout
            for table in CORE_TABLES:
                assert f"{table}|2" in result.stdout
                assert query(source, f"SELECT count(*) FROM public.{table}") == "2"
            assert (
                query(
                    "postgres",
                    "SELECT count(*) FROM pg_database WHERE datname LIKE 'fatty_restore_check_%'",
                )
                == "0"
            )

            # A readable archive with the expected version but incomplete core
            # schema must fail an actual restore drill, not pass on TOC alone.
            query(source, "DROP TABLE public.fills")
            incomplete = directory / "incomplete.dump"
            with incomplete.open("wb") as output:
                subprocess.run(
                    ["pg_dump", "--format=custom", "--no-owner", "--dbname", source],
                    env=env,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=True,
                )
            result = command(
                "bash",
                ROOT / "scripts" / "verify_restore_postgres.sh",
                incomplete,
                check=False,
            )
            assert result.returncode != 0
            assert "core_table_queries" in result.stderr
            assert "restore_verified=" not in result.stdout
            assert (
                query(
                    "postgres",
                    "SELECT count(*) FROM pg_database WHERE datname LIKE 'fatty_restore_check_%'",
                )
                == "0"
            )
            assert query(source, "SELECT count(*) FROM public.orders") == "2"
        finally:
            if started:
                command("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")
        # TemporaryDirectory removes all archives, logs, sockets and cluster files.
