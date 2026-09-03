from types import SimpleNamespace

import pytest

from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramForwarder, format_forward_html


def test_settings_accept_deployment_names_and_require_target() -> None:
    settings = TelegramSettings.from_mapping(
        {
            "TG_API_ID": "123456",
            "TG_API_HASH": "a" * 32,
            "TELEGRAM_SESSION": "private-session",
            "TELEGRAM_SOURCE_CHANNELS": "@example_source_channel, -100123",
            "TELEGRAM_TARGET_CHAT_ID": "123456789",
        }
    )
    assert settings.channels == ("@example_source_channel", "-100123")
    assert settings.target_chat_id == 123456789
    assert "private-session" not in repr(settings)


def test_settings_fail_closed_without_forwarding_configuration() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_API_ID"):
        TelegramSettings.from_mapping({})


def test_forward_html_escapes_untrusted_signal_text() -> None:
    result = format_forward_html("<script>alert(1)</script>\nBTCUSDT LONG")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "Fatty Signal Relay" in result


@pytest.mark.asyncio
async def test_forwarder_deduplicates_and_sends_media_with_wrapper() -> None:
    sent: list[tuple[str, object, dict[str, object]]] = []

    class FakeClient:
        async def send_file(self, target: int, media: object, **kwargs: object) -> None:
            sent.append(("file", media, {"target": target, **kwargs}))

        async def send_message(self, target: int, text: str, **kwargs: object) -> None:
            sent.append(("message", text, {"target": target, **kwargs}))

    message = SimpleNamespace(id=7, message="BTCUSDT LONG", media=object(), reply_to=None)
    forwarder = TelegramForwarder(
        FakeClient(),
        TelegramSettings(1, "hash", "session", ("@example_source_channel",), 123456789),
        InMemoryRawMessageRepository(),
    )
    await forwarder.handle_message(-1001, message)
    await forwarder.handle_message(-1001, message)

    assert len(sent) == 1
    assert sent[0][0] == "file"
    assert sent[0][2]["target"] == 123456789
    assert sent[0][2]["parse_mode"] == "html"
