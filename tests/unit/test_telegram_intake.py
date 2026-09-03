from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.integration import AnalysisStatus, analyze_with_fallback
from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake
from fatty_trader.intake.telethon_client import build_telethon_client


def test_telegram_settings_require_session_and_configured_channels() -> None:
    settings = TelegramSettings.from_mapping(
        {
            "TELEGRAM_API_ID": "123456",
            "TELEGRAM_API_HASH": "a" * 32,
            "TELEGRAM_SESSION": "session-value",
            "TELEGRAM_CHANNELS": "@example_source_channel, -100123",
            "TELEGRAM_TARGET_CHAT_ID": "123456789",
        }
    )
    assert settings.api_id == 123456
    assert settings.channels == ("@example_source_channel", "-100123")


def test_intake_persists_raw_message_idempotently_by_revision() -> None:
    repository = InMemoryRawMessageRepository()
    intake = TelegramIntake(repository)
    message = SimpleNamespace(
        id=42,
        message="BTCUSDT LONG MARKET SL 64000 TP 64630",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        reply_to=SimpleNamespace(reply_to_msg_id=7),
        media=None,
    )

    first = intake.ingest(channel_id=-1001, message=message)
    duplicate = intake.ingest(channel_id=-1001, message=message)

    assert first == duplicate
    assert repository.count == 1
    assert first.raw_text.startswith("BTCUSDT LONG")
    assert first.reply_to_message_id == 7


def test_fallback_accepts_explicit_signal_when_codex_fails() -> None:
    result = analyze_with_fallback(
        text="BTCUSDT LONG MARKET SL 64000 TP 64630",
        message_id=42,
        codex_runner=lambda _: CodexRunResult(
            succeeded=False,
            terminal_failure=True,
            timed_out=True,
            exit_code=None,
            failure_reason="codex timed out",
            stdout="",
            stderr="",
        ),
    )
    assert result.status is AnalysisStatus.FALLBACK_ACCEPTED
    assert result.signal is not None
    assert result.failure_class == "codex timed out"


def test_fallback_sends_ambiguous_content_to_manual_review() -> None:
    result = analyze_with_fallback(
        text="BTC looks strong, maybe long soon",
        message_id=42,
        codex_runner=lambda _: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert result.signal is None
    assert result.failure_class == "codex unavailable"


def test_session_client_wraps_string_session_without_leaking_value() -> None:
    calls: list[tuple[object, int, str]] = []

    class FakeClient:
        def __init__(self, session: object, api_id: int, api_hash: str) -> None:
            calls.append((session, api_id, api_hash))

    client = build_telethon_client(
        TelegramSettings(123456, "a" * 32, "private-session", ("@example_source_channel",)),
        client_factory=FakeClient,
        session_factory=lambda value: ("wrapped", value),
    )
    assert isinstance(client, FakeClient)
    assert calls == [(("wrapped", "private-session"), 123456, "a" * 32)]
    assert "private-session" not in repr(client)


@pytest.mark.asyncio
async def test_intake_attaches_new_message_handler_for_configured_channels() -> None:
    repository = InMemoryRawMessageRepository()
    intake = TelegramIntake(repository)
    registrations: list[tuple[object, object]] = []

    class FakeClient:
        def add_event_handler(self, callback: object, event: object) -> None:
            registrations.append((callback, event))

    await intake.attach(FakeClient(), ("@example_source_channel",))

    assert len(registrations) == 1
