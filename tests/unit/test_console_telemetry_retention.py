"""Bounded telemetry maintenance never shares financial-worker failure semantics."""

import asyncio
from unittest.mock import MagicMock

import pytest

from fatty_trader.console import worker
from fatty_trader.console.store import ConsoleStore, TelemetryRetention


def test_explicit_retention_configuration_is_parsed():
    assert TelemetryRetention.from_environ(
        {
            "CONSOLE_SNAPSHOT_RETENTION_DAYS": "14",
            "CONSOLE_EVENT_RETENTION_HOURS": "24",
            "CONSOLE_RETENTION_BATCH_SIZE": "25",
            "CONSOLE_RETENTION_INTERVAL_SECONDS": "120",
        }
    ) == TelemetryRetention(14, 24, 25, 120)


@pytest.mark.parametrize(
    "name,value",
    [
        ("CONSOLE_SNAPSHOT_RETENTION_DAYS", "0"),
        ("CONSOLE_SNAPSHOT_RETENTION_DAYS", "-7"),
        ("CONSOLE_EVENT_RETENTION_HOURS", "23"),
        ("CONSOLE_EVENT_RETENTION_HOURS", "NaN"),
        ("CONSOLE_EVENT_RETENTION_HOURS", "inf"),
        ("CONSOLE_EVENT_RETENTION_HOURS", "24.0"),
        ("CONSOLE_RETENTION_BATCH_SIZE", "0"),
        ("CONSOLE_RETENTION_BATCH_SIZE", "5001"),
        ("CONSOLE_RETENTION_INTERVAL_SECONDS", "9"),
        ("CONSOLE_RETENTION_INTERVAL_SECONDS", "3601"),
        ("CONSOLE_SNAPSHOT_RETENTION_DAYS", ""),
    ],
)
def test_invalid_retention_cannot_enable_deletion(name, value):
    with pytest.raises(ValueError):
        TelemetryRetention.from_environ({name: value})


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_direct_configuration_cannot_bypass_validation(value):
    with pytest.raises(ValueError):
        TelemetryRetention(batch_size=value)


def retention_store(acquired=True):
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = {"acquired": acquired}
    cursor.rowcount = 3
    return ConsoleStore(lambda: connection), connection, cursor


def test_delete_failure_rolls_back_both_tables_and_propagates():
    store, connection, cursor = retention_store()
    cursor.execute.side_effect = [None, None, None, None, RuntimeError("private payload")]
    with pytest.raises(RuntimeError):
        store.prune_telemetry(TelemetryRetention())
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.asyncio
async def test_unavailable_cleanup_is_reported_without_payload_or_worker_failure(
    monkeypatch, caplog
):
    async def direct(function, *args):
        return function(*args)

    monkeypatch.setattr(worker, "_run_sync_storage", direct)
    store = MagicMock()
    store.prune_telemetry.side_effect = RuntimeError("secret source payload must not log")
    await worker.maintain_telemetry(store, TelemetryRetention())
    assert "retention unavailable exception_type=RuntimeError elapsed_ms=" in caplog.text
    assert "secret source payload" not in caplog.text
    assert "completed" not in caplog.text


@pytest.mark.asyncio
async def test_worker_cancellation_is_not_swallowed_by_cleanup(monkeypatch):
    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "_run_sync_storage", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await worker.maintain_telemetry(MagicMock(), TelemetryRetention())


@pytest.mark.asyncio
async def test_invalid_config_disables_cleanup_but_polls_queue_and_reports_collection_failure(
    monkeypatch, caplog
):
    from fatty_trader.console import reads

    async def direct(function, *args):
        return function(*args)

    async def unavailable():
        raise RuntimeError("private collection payload")

    async def stop_after_one_poll(*args):
        raise asyncio.CancelledError

    collector = MagicMock()
    collector.collect_once = unavailable
    store = MagicMock()
    store.claim_job.return_value = None
    monkeypatch.setattr(worker, "ConsoleStore", lambda: store)
    monkeypatch.setattr(worker, "build_handlers", lambda *args, **kwargs: {})
    monkeypatch.setattr(reads, "SnapshotCollector", lambda *args: collector)
    monkeypatch.setattr(worker, "_run_sync_storage", direct)
    monkeypatch.setattr(worker, "Path", MagicMock())
    monkeypatch.setattr(worker.asyncio, "sleep", stop_after_one_poll)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_console_worker({"CONSOLE_EVENT_RETENTION_HOURS": "23"})
    store.claim_job.assert_called_once()
    store.prune_telemetry.assert_not_called()
    assert "retention disabled: invalid retention configuration" in caplog.text
    assert "Console observation unavailable (RuntimeError)" in caplog.text
    assert "private collection payload" not in caplog.text
