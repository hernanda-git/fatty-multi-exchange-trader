"""Execute complete maintenance scripts; fake Compose never contacts a database."""

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux backup shell/mode contract")

ROOT = Path(__file__).resolve().parents[2]
FAKE_COMPOSE = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import signal
import sys

args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
mode = os.environ.get('FAKE_MODE', '')
if args[:1] == ['ps']:
    if mode == 'unknown_services':
        sys.exit(1)
    print(os.environ.get('RUNNING_SERVICES', 'postgres'))
    sys.exit(0)
assert args[:5] == ['exec', '-T', '--user', 'postgres', 'postgres'], args
command, options = args[5], args[6:]
state = pathlib.Path(os.environ['DB_STATE'])
if command == 'pg_dump':
    assert '--username=maintenance_admin' in options, options
    print('credential-must-not-escape', file=sys.stderr)
    if mode != 'empty_dump':
        sys.stdout.buffer.write(b'fixture-custom-archive')
    if mode == 'signal_dump':
        os.kill(os.getppid(), signal.SIGTERM)
    sys.exit(1 if mode == 'dump_fail' else 0)
if command == 'pg_restore':
    data = sys.stdin.buffer.read()
    assert data == b'fixture-custom-archive', data
    if '--list' in options:
        # Publication must not precede the integrity gate.
        backup_dir = pathlib.Path(os.environ['BACKUP_DIR'])
        for partial in backup_dir.glob('.fatty_trader_*.??????.partial'):
            if partial.read_bytes() == b'fixture-custom-archive':
                stem, suffix, _ = partial.name[1:].rsplit('.', 2)
                assert not (backup_dir / f'{stem}_{suffix}.dump').exists()
        sys.exit(1 if mode == 'list_fail' else 0)
    assert '--username=maintenance_admin' in options, options
    assert '--exit-on-error' in options and '--single-transaction' in options
    target = next(arg.split('=', 1)[1] for arg in options if arg.startswith('--dbname='))
    if target != os.environ['POSTGRES_DB']:
        assert target == state.read_text()
        assert '--no-acl' in options
    print('credential-must-not-escape', file=sys.stderr)
    sys.exit(1 if mode == 'restore_fail' else 0)
if command == 'createdb':
    assert options[-1].startswith('fatty_restore_check_')
    assert options[-1] != os.environ['POSTGRES_DB']
    if mode == 'create_fail':
        sys.exit(1)
    assert not state.exists()
    state.write_text(options[-1])
    sys.exit(0)
if command == 'dropdb':
    assert options[-1] == state.read_text()
    if mode == 'drop_fail':
        sys.exit(1)
    state.unlink()
    sys.exit(0)
if command == 'psql':
    sql = next(arg.split('=', 1)[1] for arg in options if arg.startswith('--command='))
    if sql.startswith('REVOKE CONNECT'):
        assert state.read_text() in sql
        assert 'FROM PUBLIC' in sql
        sys.exit(1 if mode == 'isolate_fail' else 0)
    if 'FROM pg_database' in sql:
        assert not state.exists()
        print('1' if mode == 'cleanup_unconfirmed' else '0')
    elif 'max(version)' in sql:
        print('26' if mode == 'wrong_version' else '27')
    else:
        assert 'public.dispatches' in sql and 'public.console_jobs' in sql
        assert 'public.fills' in sql and 'public.console_event_outbox' in sql
        if mode == 'missing_core_table':
            sys.exit(1)
        print('dispatches|2\nfills|1\nconsole_jobs|1')
    sys.exit(0)
raise AssertionError(args)
"""


@pytest.fixture
def maintenance(tmp_path):
    fake = tmp_path / "compose"
    fake.write_text(FAKE_COMPOSE)
    fake.chmod(0o755)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o755)
    env = {
        "PATH": os.environ["PATH"],
        "COMPOSE_BIN": str(fake),
        "CALL_LOG": str(tmp_path / "calls.jsonl"),
        "DB_STATE": str(tmp_path / "database"),
        "BACKUP_DIR": str(backup_dir),
        "POSTGRES_USER": "runtime_role_must_not_be_used",
        "POSTGRES_ADMIN_USER": "maintenance_admin",
        "POSTGRES_DB": "live_financial_state",
        "POSTGRES_PASSWORD": "credential-must-not-escape",
        "BACKUP_RETAIN_DAYS": "14",
    }
    return env


def run_script(env, name, *args, **extra):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / name), *map(str, args)],
        cwd=ROOT,
        env={**env, **extra},
        text=True,
        capture_output=True,
        check=False,
    )


def calls(env):
    path = Path(env["CALL_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def archive(env):
    path = Path(env["BACKUP_DIR"]).parent / "input.dump"
    path.write_bytes(b"fixture-custom-archive")
    return path


def test_backup_private_atomic_and_admin_boundary(maintenance):
    result = run_script(maintenance, "backup_postgres.sh")
    assert result.returncode == 0, result.stderr
    directory = Path(maintenance["BACKUP_DIR"])
    files = list(directory.iterdir())
    assert len(files) == 1 and files[0].suffix == ".dump"
    assert files[0].read_bytes() == b"fixture-custom-archive"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert "credential-must-not-escape" not in result.stdout + result.stderr
    assert "isolated_restore_command=" in result.stdout
    assert all(
        "runtime_role_must_not_be_used" not in arg for row in calls(maintenance) for arg in row
    )


def test_repeated_backups_preserve_both_archives(maintenance):
    first = run_script(maintenance, "backup_postgres.sh")
    second = run_script(maintenance, "backup_postgres.sh")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    files = list(Path(maintenance["BACKUP_DIR"]).iterdir())
    assert len(files) == 2
    assert all(path.read_bytes() == b"fixture-custom-archive" for path in files)


def test_backup_refuses_symlink_directory_before_database_call(maintenance, tmp_path):
    link = tmp_path / "backup-link"
    link.symlink_to(Path(maintenance["BACKUP_DIR"]), target_is_directory=True)
    result = run_script(maintenance, "backup_postgres.sh", BACKUP_DIR=str(link))
    assert result.returncode == 2
    assert calls(maintenance) == []


@pytest.mark.parametrize("mode", ["dump_fail", "empty_dump", "list_fail", "signal_dump"])
def test_failed_backup_never_publishes_or_leaves_partial(maintenance, mode):
    result = run_script(maintenance, "backup_postgres.sh", FAKE_MODE=mode)
    assert result.returncode != 0
    assert not list(Path(maintenance["BACKUP_DIR"]).iterdir())
    assert "credential-must-not-escape" not in result.stdout + result.stderr


def test_retention_removes_only_old_exact_backup_names(maintenance):
    directory = Path(maintenance["BACKUP_DIR"])
    old = ["fatty_trader_20260101T000000Z.dump", "fatty_trader_20260101T000000Z_abc123.dump"]
    keep = [
        "fatty_trader_notes.dump",
        "other.dump",
        ".fatty_trader_20260101T000000Z.abc123.partial",
    ]
    for name in old + keep:
        path = directory / name
        path.write_bytes(b"old")
        os.utime(path, (time.time() - 20 * 86400,) * 2)
    nested = directory / "nested"
    nested.mkdir()
    (nested / old[0]).write_bytes(b"untouched")
    recent = directory / "fatty_trader_20260201T000000Z.dump"
    recent.write_bytes(b"recent")
    target = directory.parent / "unrelated-target"
    target.write_bytes(b"untouched")
    link = directory / "fatty_trader_20260102T000000Z.dump"
    link.symlink_to(target)
    result = run_script(maintenance, "backup_postgres.sh")
    assert result.returncode == 0, result.stderr
    assert not any((directory / name).exists() for name in old)
    assert all((directory / name).exists() for name in keep)
    assert recent.exists() and (nested / old[0]).exists()
    assert link.is_symlink() and target.read_bytes() == b"untouched"


def test_retention_zero_keeps_old_backups(maintenance):
    old = Path(maintenance["BACKUP_DIR"]) / "fatty_trader_20260101T000000Z.dump"
    old.write_bytes(b"old")
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    result = run_script(maintenance, "backup_postgres.sh", BACKUP_RETAIN_DAYS="0")
    assert result.returncode == 0, result.stderr
    assert old.exists()


@pytest.mark.parametrize("days", ["-1", "bogus", "1;false"])
def test_invalid_retention_fails_before_database_call(maintenance, days):
    result = run_script(maintenance, "backup_postgres.sh", BACKUP_RETAIN_DAYS=days)
    assert result.returncode == 2
    assert calls(maintenance) == []


@pytest.mark.parametrize(
    "script,confirmation",
    [
        ("restore_postgres.sh", "CONFIRM_RESTORE"),
        ("verify_restore_postgres.sh", "CONFIRM_ISOLATED_RESTORE"),
    ],
)
def test_restore_confirmation_precedes_every_database_call(maintenance, script, confirmation):
    result = run_script(maintenance, script, archive(maintenance), **{confirmation: "NO"})
    assert result.returncode == 2
    assert calls(maintenance) == []


@pytest.mark.parametrize("services", ["intake", "future-worker", "postgres\nanalyzer"])
def test_live_restore_refuses_every_running_app_service(maintenance, services):
    result = run_script(
        maintenance,
        "restore_postgres.sh",
        archive(maintenance),
        CONFIRM_RESTORE="YES",
        RUNNING_SERVICES=services,
    )
    assert result.returncode == 2
    assert not any("--clean" in row for row in calls(maintenance))


def test_live_restore_refuses_failed_service_discovery(maintenance):
    result = run_script(
        maintenance,
        "restore_postgres.sh",
        archive(maintenance),
        CONFIRM_RESTORE="YES",
        FAKE_MODE="unknown_services",
    )
    assert result.returncode == 2
    assert not any("--clean" in row for row in calls(maintenance))


@pytest.mark.parametrize("services", ["", "postgres"])
def test_live_restore_is_admin_single_transaction_with_workers_stopped(maintenance, services):
    result = run_script(
        maintenance,
        "restore_postgres.sh",
        archive(maintenance),
        CONFIRM_RESTORE="YES",
        RUNNING_SERVICES=services,
    )
    assert result.returncode == 0, result.stderr
    assert len([row for row in calls(maintenance) if "--clean" in row]) == 1
    assert "credential-must-not-escape" not in result.stdout + result.stderr


def test_live_restore_failure_is_not_reported_as_success(maintenance):
    result = run_script(
        maintenance,
        "restore_postgres.sh",
        archive(maintenance),
        CONFIRM_RESTORE="YES",
        FAKE_MODE="restore_fail",
    )
    assert result.returncode != 0
    assert "restore_completed=" not in result.stdout
    assert "credential-must-not-escape" not in result.stdout + result.stderr


def test_isolated_drill_restores_queries_and_verifies_cleanup(maintenance):
    result = run_script(
        maintenance,
        "verify_restore_postgres.sh",
        archive(maintenance),
        CONFIRM_ISOLATED_RESTORE="YES",
        RESTORE_EXPECTED_SCHEMA_VERSION="27",
    )
    assert result.returncode == 0, result.stderr
    assert "dispatches|2" in result.stdout
    assert "restore_verification_cleanup_verified=" in result.stdout
    assert "restore_verified=" in result.stdout
    assert not Path(maintenance["DB_STATE"]).exists()
    assert "credential-must-not-escape" not in result.stdout + result.stderr
    restores = [row for row in calls(maintenance) if "pg_restore" in row and "--list" not in row]
    assert len(restores) == 1
    assert "--dbname=live_financial_state" not in restores[0]
    assert not any(row[0] == "ps" for row in calls(maintenance))


@pytest.mark.parametrize(
    "mode", ["isolate_fail", "restore_fail", "wrong_version", "missing_core_table"]
)
def test_isolated_drill_cleans_created_database_on_every_failure(maintenance, mode):
    result = run_script(
        maintenance,
        "verify_restore_postgres.sh",
        archive(maintenance),
        CONFIRM_ISOLATED_RESTORE="YES",
        RESTORE_EXPECTED_SCHEMA_VERSION="27",
        FAKE_MODE=mode,
    )
    assert result.returncode != 0
    assert not Path(maintenance["DB_STATE"]).exists()
    assert "restore_verified=" not in result.stdout
    assert "credential-must-not-escape" not in result.stdout + result.stderr


def test_failed_database_creation_never_drops_existing_database(maintenance):
    Path(maintenance["DB_STATE"]).write_text("existing_database")
    result = run_script(
        maintenance,
        "verify_restore_postgres.sh",
        archive(maintenance),
        CONFIRM_ISOLATED_RESTORE="YES",
        RESTORE_EXPECTED_SCHEMA_VERSION="27",
        FAKE_MODE="create_fail",
    )
    assert result.returncode != 0
    assert Path(maintenance["DB_STATE"]).read_text() == "existing_database"
    assert not any("dropdb" in row for row in calls(maintenance))


@pytest.mark.parametrize("mode", ["drop_fail", "cleanup_unconfirmed"])
def test_unconfirmed_cleanup_prevents_success(maintenance, mode):
    result = run_script(
        maintenance,
        "verify_restore_postgres.sh",
        archive(maintenance),
        CONFIRM_ISOLATED_RESTORE="YES",
        RESTORE_EXPECTED_SCHEMA_VERSION="27",
        FAKE_MODE=mode,
    )
    assert result.returncode != 0
    assert "restore_verified=" not in result.stdout


@pytest.mark.parametrize("version", ["", "0", "latest", "27;DROP DATABASE postgres"])
def test_isolated_drill_requires_numeric_expected_version_before_database_call(
    maintenance, version
):
    result = run_script(
        maintenance,
        "verify_restore_postgres.sh",
        archive(maintenance),
        CONFIRM_ISOLATED_RESTORE="YES",
        RESTORE_EXPECTED_SCHEMA_VERSION=version,
    )
    assert result.returncode == 2
    assert calls(maintenance) == []
