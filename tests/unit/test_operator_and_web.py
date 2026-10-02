import httpx
import pytest

from fatty_trader.operator.authorization import authorize_sender
from fatty_trader.operator.command_parser import (
    CancelCommand,
    CloseCommand,
    CommandError,
    parse_operator_command,
    parse_trade,
)
from fatty_trader.web.app import create_app


def test_operator_authorization_requires_exact_private_user_id() -> None:
    assert authorize_sender(
        sender_id=42, expected_operator_id=42, is_private=True, is_forwarded=False
    )
    assert not authorize_sender(
        sender_id=43, expected_operator_id=42, is_private=True, is_forwarded=False
    )
    assert not authorize_sender(
        sender_id=42, expected_operator_id=42, is_private=False, is_forwarded=False
    )
    assert not authorize_sender(
        sender_id=42, expected_operator_id=42, is_private=True, is_forwarded=True
    )


def test_trade_grammar_requires_explicit_side_and_stop() -> None:
    command = parse_trade(
        "/trade all LONG BTCUSDT margin=2 leverage=auto entry=market sl=64000 tp=64630"
    )
    assert command.exchanges == ("binance", "bitget")
    assert command.stop_loss == 64000

    try:
        parse_trade("/trade all BTCUSDT margin=2 entry=market")
    except CommandError:
        pass
    else:
        raise AssertionError("invalid trade syntax must be rejected")


@pytest.mark.parametrize(
    "text",
    [
        "/trade bitget LONG BTCUSDT margin=NaN leverage=20 entry=market sl=90 tp=110",
        "/trade bitget LONG BTCUSDT margin=Infinity leverage=20 entry=market sl=90 tp=110",
        "/trade bitget LONG BTCUSDT margin=1 leverage=20 entry=market sl=90 tp=110 junk=x",
        "/trade bitget LONG BTCUSDT margin=1 margin=2 leverage=20 entry=market sl=90 tp=110",
        "/open BTCUSDT LONG margin=Infinity leverage=20 entry=market sl=90 tp=110",
        "/open BTCUSDT LONG margin=1 leverage=20 entry=limit:NaN sl=90 tp=110",
        "/open BTCUSDT LONG margin=1 leverage=20 entry=market sl=NaN tp=110",
        "/open BTCUSDT LONG margin=1 leverage=20 entry=market sl=90 tp=Infinity",
        "/close all confirm=one confirm=two",
        "/cancel order_id=",
        "/close position_id=",
        "/price BTC/USDT",
        "/setsl BTC/USDT 100",
    ],
)
def test_trade_commands_reject_nonfinite_numbers_unknown_and_duplicate_arguments(text: str) -> None:
    with pytest.raises(CommandError):
        parse_operator_command(text)


def test_bot_mentioned_slash_commands_are_parsed() -> None:
    assert parse_operator_command("/balance@FattyTestBot").__class__.__name__ == "BalanceCommand"
    assert (
        parse_operator_command(
            "/trade@FattyTestBot bitget LONG BTCUSDT margin=1 leverage=20 entry=market sl=90 tp=110"
        ).__class__.__name__
        == "TradeCommand"
    )


@pytest.mark.parametrize("command", ["/close ALL", "/cancel ALL"])
def test_all_target_is_case_insensitive(command: str) -> None:
    parsed = parse_operator_command(command)
    assert isinstance(parsed, (CloseCommand, CancelCommand))
    assert parsed.target == "all"


async def test_dashboard_never_reports_live_execution_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADER_MODE", "DEMO")
    monkeypatch.setenv("BITGET_MODE", "DEMO")
    monkeypatch.setenv("BITGET_EXECUTION_ENABLED", "0")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "DEMO"
    assert response.json()["live_execution_enabled"] is False
