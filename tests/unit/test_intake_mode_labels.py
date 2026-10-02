"""Intake startup labels describe the actual configured service mode."""

import pytest

from fatty_trader import service


@pytest.mark.asyncio
async def test_disabled_live_intake_reports_live_mode(monkeypatch, capsys, tmp_path):
    async def stop(_seconds):
        raise RuntimeError("offline-stop")

    monkeypatch.setattr(service.asyncio, "sleep", stop)
    with pytest.raises(RuntimeError, match="offline-stop"):
        await service.run_intake(
            {"TRADER_MODE": "LIVE", "WORKER_HEALTH_PATH": str(tmp_path / "health")}
        )
    assert "service=intake mode=LIVE state=disabled" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ready_live_intake_reports_live_mode(monkeypatch, capsys, tmp_path):
    class Client:
        async def start(self):
            pass

        def is_connected(self):
            return False

        async def run_until_disconnected(self):
            pass

    class Forwarder:
        def __init__(self, *args):
            pass

        async def attach(self):
            pass

    monkeypatch.setattr(service, "TelegramForwarder", Forwarder)
    await service.run_intake(
        {
            "TRADER_MODE": "LIVE",
            "WORKER_HEALTH_PATH": str(tmp_path / "health"),
            "TG_API_ID": "123",
            "TG_API_HASH": "offline",
            "TELEGRAM_SESSION": "offline",
            "TELEGRAM_SOURCE_CHANNELS": "@offline",
            "TELEGRAM_TARGET_CHAT_ID": "123",
            "TELEGRAM_CATCHUP_SECONDS": "0",
        },
        client_factory=lambda settings: Client(),
        repository=object(),
    )
    assert "service=intake mode=LIVE state=ready" in capsys.readouterr().out
