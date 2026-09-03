import httpx

from fatty_trader.operator.authorization import authorize_sender
from fatty_trader.operator.command_parser import CommandError, parse_trade
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


async def test_dashboard_never_reports_live_execution_enabled() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"
    assert response.json()["live_execution_enabled"] is False
