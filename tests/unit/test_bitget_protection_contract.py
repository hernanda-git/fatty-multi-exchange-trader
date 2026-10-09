"""Provider-contract tests for Bitget Classic V2 native position protection."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fatty_trader.exchanges.bitget.protection_contract import (
    ProtectionContractError,
    build_position_tpsl_payload,
    normalize_pending_plan_response,
    normalize_position_tpsl_response,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "bitget"


def test_position_level_payload_uses_classic_v2_fields_and_market_execution() -> None:
    payload = build_position_tpsl_payload(
        symbol="wldusdt",
        hold_side="long",
        quantity=Decimal("91"),
        stop_loss=Decimal("0.388"),
        take_profit=Decimal("0.427"),
        stop_loss_client_oid="entry-1-sl",
        take_profit_client_oid="entry-1-tp",
    )

    assert payload == {
        "marginCoin": "USDT",
        "productType": "USDT-FUTURES",
        "symbol": "WLDUSDT",
        "holdSide": "buy",
        "stopLossTriggerPrice": "0.388",
        "stopLossTriggerType": "mark_price",
        "stopLossExecutePrice": "0",
        "stopLossClientOid": "entry-1-sl",
        "stopSurplusTriggerPrice": "0.427",
        "stopSurplusTriggerType": "mark_price",
        "stopSurplusExecutePrice": "0",
        "stopSurplusClientOid": "entry-1-tp",
    }
    assert "size" not in payload
    assert "stopLossSize" not in payload
    assert "stopSurplusSize" not in payload
    assert "delegateType" not in payload


@pytest.mark.parametrize("legs", ["sl", "tp", "both"])
@pytest.mark.parametrize("explicit_oids", [False, True])
@pytest.mark.parametrize("partial_sizes", [False, True])
@pytest.mark.parametrize("delegate_type", [False, True])
@pytest.mark.parametrize("execute_price", [None, "0", Decimal("0.00"), "0E-8", ""])
def test_omission_changes_only_market_execute_fields(
    legs: str,
    explicit_oids: bool,
    partial_sizes: bool,
    delegate_type: bool,
    execute_price: Decimal | str | None,
) -> None:
    kwargs = {
        "symbol": "PUMPUSDT",
        "hold_side": "long",
        "quantity": "1880",
        "stop_loss": "0.005428" if legs != "tp" else None,
        "take_profit": "0.00712" if legs != "sl" else None,
        "stop_loss_execute_price": execute_price,
        "take_profit_execute_price": execute_price,
        "stop_loss_client_oid": "entry-pump-sl" if explicit_oids else None,
        "take_profit_client_oid": "entry-pump-tp" if explicit_oids else None,
        "stop_loss_size": "940" if partial_sizes and legs != "tp" else None,
        "take_profit_size": "940" if partial_sizes and legs != "sl" else None,
        "include_delegate_type": delegate_type,
        "product_type": "USDT-FUTURES",
        "margin_coin": "USDT",
    }
    default = build_position_tpsl_payload(**kwargs)
    omitted = build_position_tpsl_payload(**kwargs, omit_market_execute_prices=True)
    expected = {
        key: value
        for key, value in default.items()
        if key not in {"stopLossExecutePrice", "stopSurplusExecutePrice"}
    }
    assert omitted == expected
    assert omitted == build_position_tpsl_payload(**kwargs, omit_market_execute_prices=True)
    assert default == build_position_tpsl_payload(**kwargs, omit_market_execute_prices=False)


def test_short_payload_maps_hold_side_to_sell() -> None:
    payload = build_position_tpsl_payload(
        symbol="BTCUSDT",
        hold_side="short",
        quantity="0.01",
        stop_loss="61000",
        take_profit="59000",
        stop_loss_client_oid="short-sl",
        take_profit_client_oid="short-tp",
    )

    assert payload["holdSide"] == "sell"
    assert payload["stopLossExecutePrice"] == "0"
    assert payload["stopSurplusExecutePrice"] == "0"


def test_partial_plan_sizes_use_documented_field_names_only_when_explicit() -> None:
    payload = build_position_tpsl_payload(
        symbol="BTCUSDT",
        hold_side="buy",
        quantity="0.01",
        stop_loss="49000",
        take_profit="51000",
        stop_loss_client_oid="partial-sl",
        take_profit_client_oid="partial-tp",
        stop_loss_size="0.005",
        take_profit_size="0.005",
    )

    assert payload["stopLossSize"] == "0.005"
    assert payload["stopSurplusSize"] == "0.005"
    assert "size" not in payload


def test_positive_execute_price_is_rejected_by_default() -> None:
    with pytest.raises(ProtectionContractError, match="market execution"):
        build_position_tpsl_payload(
            symbol="BTCUSDT",
            hold_side="buy",
            quantity="0.01",
            stop_loss="49000",
            take_profit=None,
            stop_loss_client_oid="sl",
            take_profit_client_oid=None,
            stop_loss_execute_price="48999",
        )


def test_contract_normalizers_accept_official_fixture_shapes() -> None:
    placement = json.loads((_FIXTURES / "place_pos_tpsl_response.json").read_text())
    pending = json.loads((_FIXTURES / "pending_profit_loss_response.json").read_text())

    assert normalize_position_tpsl_response(placement["data"]) == placement["data"]
    assert normalize_pending_plan_response(pending["data"]) == pending["data"]["entrustedList"]


def test_contract_normalizers_reject_wrong_shapes() -> None:
    with pytest.raises(ProtectionContractError, match="placement response"):
        normalize_position_tpsl_response({"orderId": "not-an-array"})
    with pytest.raises(ProtectionContractError, match="pending plan response"):
        normalize_pending_plan_response({"entrustedList": ["not-an-object"]})


def test_empty_pending_plan_list_is_a_known_empty_read() -> None:
    assert normalize_pending_plan_response({"entrustedList": None}) == []
    assert normalize_pending_plan_response({"entrustedList": []}) == []


def test_missing_pending_list_is_not_a_successful_empty_inventory() -> None:
    with pytest.raises(ProtectionContractError, match="missing entrustedList"):
        normalize_pending_plan_response({"endId": "123"})
