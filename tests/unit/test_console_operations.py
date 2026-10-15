from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fatty_trader.console.operations import (
    OperationRefused,
    OperationsHandlers,
    SecretStages,
    digest,
    validate_arguments,
)
from fatty_trader.console.runner import HostOperationsRunner


@pytest.mark.parametrize(
    "settings",
    [
        {"BITGET_EXECUTION_ENABLED": "0"},
        {"BITGET_MODE": "DEMO"},
        {"BITGET_MAX_CLOCK_SKEW_MS": 999999},
        {"BITGET_OPERATOR_MUTATIONS_ENABLED": "1"},
        {"BITGET_MONITOR_POLL_SECONDS": 31},
    ],
)
def test_generic_config_cannot_relax_policy(settings):
    with pytest.raises(OperationRefused):
        validate_arguments("config.save_draft", {"base_version": 0, "settings": settings})


@pytest.mark.parametrize(
    "action,args",
    [
        ("service.restart", {"services": ["postgres"], "maintenance_plan_id": str(uuid4())}),
        (
            "service.stop",
            {"services": ["monitor-bitget;touch /tmp/pwn"], "maintenance_plan_id": str(uuid4())},
        ),
        (
            "release.deploy",
            {
                "release_id": "approved",
                "services": ["web"],
                "maintenance_plan_id": str(uuid4()),
                "ref": "main",
            },
        ),
        (
            "backup.restore",
            {
                "backup_id": str(uuid4()),
                "sha256": "a" * 64,
                "maintenance_plan_id": str(uuid4()),
                "path": "/etc/passwd",
            },
        ),
        ("safety.contain", {"latch_scope": "global"}),
        (
            "signal.reanalyze",
            {"message_id": str(uuid4()), "revision": "a" * 64, "analysis_only": False},
        ),
    ],
)
def test_fixed_operation_schema_rejects_injection_and_scope_escape(action, args):
    with pytest.raises(OperationRefused):
        validate_arguments(action, args)


@pytest.fixture
def stages(tmp_path):
    directory = tmp_path / "stages"
    directory.mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = tmp_path / "public.pem"
    private = tmp_path / "private.pem"
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    env = {
        "CONSOLE_SECRET_STAGE_DIR": str(directory),
        "CONSOLE_SECRET_PUBLIC_KEY_FILE": str(public),
    }
    return (
        SecretStages(env),
        SecretStages({**env, "CONSOLE_SECRET_PRIVATE_KEY_FILE": str(private)}),
        directory,
    )


def test_secret_stage_web_has_no_read_key_and_ciphertext_never_contains_plaintext(stages):
    web, host, directory = stages
    result = web.stage("BITGET_API_SECRET", "never-persist-this-plaintext", "alice", "123")
    raw = (directory / (result["stage_id"] + ".stage")).read_text()
    assert "never-persist-this-plaintext" not in raw
    assert "value" not in result
    with pytest.raises(OperationRefused, match="host-runner only"):
        web.read(result["stage_id"], "alice", "123")
    assert host.read(result["stage_id"], "alice", "123")["value"] == "never-persist-this-plaintext"
    for actor, account in (("bob", "123"), ("alice", "456")):
        with pytest.raises(OperationRefused, match="binding mismatch"):
            host.read(result["stage_id"], actor, account)
    host.consume(result["stage_id"])
    with pytest.raises(OperationRefused):
        host.read(result["stage_id"], "alice", "123")


class Store:
    def __init__(self):
        self.statements = []
        self.snapshot = None
        self.row = None
        self.snapshots = {}

    def one(self, sql, params=()):
        return self.row

    def query(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def put_snapshot(self, kind, scope, data, **kwargs):
        self.snapshot = {"kind": kind, "data": data, **kwargs}
        self.snapshots[kind] = self.snapshot

    def latest_snapshot(self, kind, scope):
        return self.snapshot


def runner(tmp_path, store=None):
    state = tmp_path / "state"
    backups = tmp_path / "backups"
    state.mkdir(mode=0o700)
    backups.mkdir(mode=0o700)
    root = tmp_path / "root"
    root.mkdir()
    (root / "docker-compose.yml").write_text("services: {}")
    calls = []

    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        if "show" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    b"LoadState=loaded\nActiveState=active\nTimersMonotonic={ "
                    b"OnUnitActiveUSec=6h ; next_elapse=0 }\n"
                ),
                stderr=b"",
            )
        return SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")

    env = {
        "CONSOLE_OPS_WORKER": "1",
        "CONSOLE_COMPOSE_PROJECT": "fixed-project",
        "CONSOLE_REPOSITORY_ROOT": str(root),
        "CONSOLE_OPS_STATE_DIR": str(state),
        "CONSOLE_BACKUP_DIR": str(backups),
        "CONSOLE_ACCOUNT_ID": "123",
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "PATH": "/usr/bin:/bin",
    }
    return HostOperationsRunner(env, store or Store(), command), calls


def test_host_runner_requires_explicit_project(tmp_path):
    host, _ = runner(tmp_path)
    del host.environ["CONSOLE_COMPOSE_PROJECT"]
    with pytest.raises(KeyError):
        host._environment()


def test_fixed_commands_use_no_shell_and_snapshot_has_actual_observed_timestamp(tmp_path):
    host, calls = runner(tmp_path)
    host.observe()
    assert calls[0][0] == ["/usr/bin/docker", "compose", "ps", "--all", "--format", "json"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["env"]["COMPOSE_PROJECT_NAME"] == "fixed-project"
    assert host.store.snapshot["observed_at"] is not None


def test_restart_unknown_is_get_only_and_running_is_not_restart_proof(tmp_path):
    host, calls = runner(tmp_path)
    job = {
        "id": str(uuid4()),
        "action": "service.restart",
        "arguments": {"services": ["web"], "maintenance_plan_id": str(uuid4())},
    }
    host._write_atomic(
        host.state_dir / (job["id"] + ".receipt.json"),
        {"state": "claimed", "arguments_digest": digest(job["arguments"])},
    )
    result = host.reconcile(job)
    assert result["state"] == "reconciliation_pending"
    assert result["result"]["effect_replayed"] is False
    assert all("restart" not in argv for argv, _ in calls)


def test_backup_digest_mismatch_refuses_before_any_command(tmp_path):
    host, calls = runner(tmp_path)
    path = host.backup_dir / "fatty_trader_20261014T120000Z.dump"
    path.write_bytes(b"actual archive")
    with pytest.raises(OperationRefused, match="integrity mismatch"):
        host._verify_backup(
            {"filename": path.name, "size_bytes": path.stat().st_size, "sha256": "a" * 64}
        )
    assert calls == []


def test_backup_catalog_cannot_escape_directory(tmp_path):
    host, _ = runner(tmp_path)
    with pytest.raises(OperationRefused):
        host._backup_path({"filename": "../../etc/passwd"})


def test_nonflat_maintenance_refuses_without_effect_claim(tmp_path):
    host, calls = runner(tmp_path)
    host._lock = lambda: __import__("contextlib").nullcontext()
    host._effect_guard = lambda job: None
    host._policy = lambda: None

    def nonflat():
        raise OperationRefused("position exposure is positive or unknown")

    host._flat = nonflat
    job = {
        "id": str(uuid4()),
        "scope": host.scope,
        "action": "service.stop",
        "arguments": {"services": ["monitor-bitget"], "maintenance_plan_id": str(uuid4())},
    }
    with pytest.raises(OperationRefused, match="position exposure"):
        host.execute(job, {})
    assert not (host.state_dir / (job["id"] + ".receipt.json")).exists()
    assert calls == []


def test_operations_resource_version_matches_preview_and_stale_host_gate():
    store = Store()
    handlers = OperationsHandlers({"CONSOLE_ACCOUNT_ID": "123"}, store)
    scope = {"account_id": "123", "venue": "bitget", "environment": "LIVE"}
    args = {"services": ["web"], "no_replay": True}
    preview = asyncio.run(handlers.preview("maintenance.prepare", args, [], scope))
    assert preview["resource_version"] == handlers.current_resource_version(
        "maintenance.prepare", args, [], scope
    )
    store.snapshot = {"observed_at": datetime.now(UTC) - timedelta(seconds=60), "error": None}
    assert (
        handlers.capability("backup.create", scope)["reasons"][0]["code"] == "host_evidence_stale"
    )


def test_export_csv_escapes_formula_and_uses_real_audit_table():
    store = Store()
    captured = []

    def query(sql, params=()):
        captured.append((sql, params))
        return [{"id": "=CMD()", "actor_id": "@evil"}]

    store.query = query
    handlers = OperationsHandlers({}, store)
    result = handlers._export(
        {"report_kind": "audit", "format": "csv"},
        {"scope": {"account_id": "123", "environment": "LIVE"}},
    )
    assert "console_audit_events" in captured[0][0]
    assert "'=CMD()" in result["content"]
    assert "'@evil" in result["content"]
    assert result["sha256"] == hashlib.sha256(result["content"].encode()).hexdigest()


def test_secret_stages_bound_capacity_and_expire_old_entries(stages):
    web, host, directory = stages
    for index in range(4):
        web.stage("TG_BOT_TOKEN", f"secret-{index}", "actor", "123")
    with pytest.raises(OperationRefused, match="capacity"):
        web.stage("TG_BOT_TOKEN", "fifth", "actor", "123")
    for path in directory.glob("*.stage"):
        os.utime(path, (0, 0))
    assert web.stage("TG_BOT_TOKEN", "replacement", "actor", "123")["kind"] == "TG_BOT_TOKEN"
    assert len(list(directory.glob("*.stage"))) == 1


def test_restore_claim_survives_database_receipt_loss_and_never_reexecutes(tmp_path):
    host, calls = runner(tmp_path)
    job = {
        "id": str(uuid4()),
        "action": "backup.restore",
        "arguments": {
            "backup_id": str(uuid4()),
            "sha256": "a" * 64,
            "maintenance_plan_id": str(uuid4()),
        },
    }
    host._write_atomic(
        host.state_dir / (job["id"] + ".receipt.json"),
        {"state": "claimed", "arguments_digest": digest(job["arguments"])},
    )
    result = host.reconcile(job)
    assert result["state"] == "reconciliation_pending"
    assert result["result"]["effect_replayed"] is False
    assert not any("restore_postgres.sh" in argv for argv, _ in calls)


def test_explicit_schedule_is_fixed_unit_only_and_has_observed_evidence(tmp_path):
    host, calls = runner(tmp_path)
    host.environ["HOME"] = str(tmp_path)
    host._effect_guard = lambda job: None
    job = {
        "id": str(uuid4()),
        "action": "notification.configure_schedule",
        "arguments": {"interval_hours": 6},
    }
    result = host._schedule(job)
    assert result["effective_interval_hours"] == 6
    assert result["fields"]["TimersMonotonic"]
    override = tmp_path / ".config/systemd/user/fatty-health-report.timer.d/console.conf"
    assert override.read_text() == "[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=6h\n"
    assert any(
        argv == ["/usr/bin/systemctl", "--user", "restart", "fatty-health-report.timer"]
        for argv, _ in calls
    )
    with pytest.raises(OperationRefused):
        validate_arguments(
            "notification.configure_schedule", {"interval_hours": 6, "unit": "other.timer"}
        )


def test_health_delivery_uses_shared_renderer_not_arbitrary_html():
    from fatty_trader.notifications import NotificationPayloadError, format_notification_html
    from fatty_trader.operator.health_report_format import format_report
    from fatty_trader.telegram_html import bounded_html

    snapshot = {
        "positions": None,
        "pending_orders": None,
        "sltp": {},
        "pnl": {},
        "messages": [],
        "metrics": {},
        "account": {},
        "modes": {},
        "codex": {},
        "services": {},
    }
    assert format_notification_html(
        {"kind": "console-health-report", "snapshot": snapshot}
    ) == bounded_html(format_report(**snapshot))
    with pytest.raises(NotificationPayloadError):
        format_notification_html({"kind": "console-health-report", "html": "<script>bad</script>"})


def test_expired_secret_spool_is_cleaned_without_new_submission(stages):
    web, host, directory = stages
    result = web.stage("TG_BOT_TOKEN", "private-token", "actor", "123")
    path = directory / (result["stage_id"] + ".stage")
    os.utime(path, (0, 0))
    host.cleanup()
    assert not path.exists()


def test_timer_readback_does_not_confuse_sixteen_hours_with_six(tmp_path):
    host, calls = runner(tmp_path)
    host.environ["HOME"] = str(tmp_path)
    host._effect_guard = lambda job: None

    def command(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                b"LoadState=loaded\nActiveState=active\nTimersMonotonic={ "
                b"OnUnitActiveUSec=16h ; next_elapse=0 }\n"
            ),
            stderr=b"",
        )

    host.command_runner = command
    with pytest.raises(OperationRefused, match="interval readback"):
        host._schedule({"id": str(uuid4()), "arguments": {"interval_hours": 6}})


@pytest.mark.parametrize(
    "action,args,row",
    [
        (
            "config.apply",
            {"draft_id": str(uuid4())},
            {"state": "draft", "account_id": "other", "environment": "LIVE"},
        ),
        (
            "release.deploy",
            {"release_id": "approved"},
            {"account_id": "123", "environment": "DEMO"},
        ),
        (
            "backup.delete",
            {"backup_id": str(uuid4()), "sha256": "a" * 64},
            {"sha256": "a" * 64, "account_id": "other", "environment": "LIVE"},
        ),
    ],
)
def test_operations_reject_cross_account_and_environment_resources(action, args, row):
    store = Store()
    store.row = row
    handlers = OperationsHandlers({"CONSOLE_ACCOUNT_ID": "123"}, store)
    with pytest.raises(OperationRefused, match="scope"):
        handlers._resource(
            action, args, {"account_id": "123", "venue": "bitget", "environment": "LIVE"}
        )


def test_host_quiescence_refuses_unknown_running_compose_service(tmp_path):
    host, calls = runner(tmp_path)
    host.observe = lambda: {
        "items": [],
        "unreviewed_running_services": ["unexpected-provider-worker"],
    }
    with pytest.raises(OperationRefused, match="unreviewed running"):
        host._running()
    assert calls == []


def test_report_redacts_before_payload_and_digest_are_persisted():
    store = Store()
    store.query = lambda sql, params: [{"actor_id": "token=private-value", "api_secret": "hidden"}]
    handlers = OperationsHandlers({}, store)
    result = handlers._export(
        {"report_kind": "audit", "format": "json"},
        {"scope": {"account_id": "123", "environment": "LIVE"}},
    )
    assert "private-value" not in result["content"]
    assert "hidden" not in result["content"]
    assert result["sha256"] == hashlib.sha256(result["content"].encode()).hexdigest()
