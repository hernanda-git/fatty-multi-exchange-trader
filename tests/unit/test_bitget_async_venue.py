import asyncio
import hashlib
import json
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetApiError
from fatty_trader.exchanges.bitget.metadata import (
    BitgetCatalogError,
    BitgetContractNotFound,
    catalog_audit_context,
    find_contract,
)
from fatty_trader.risk.liquidation import MMTier, select_mmr

# Realistic /market/query-position-lever rows: ordered by size, and note that the
# last tier is NOT marked with an `endUnit == 0` sentinel (BTCUSDT reports
# 1200000000 live), so the widest tier must be forced to the catch-all by code.
POSITION_LEVER_ROWS: list[dict[str, str]] = [
    {
        "symbol": "BTCUSDT",
        "level": "1",
        "startUnit": "0",
        "endUnit": "200000",
        "leverage": "150",
        "keepMarginRate": "0.0050",
    },
    {
        "symbol": "BTCUSDT",
        "level": "2",
        "startUnit": "200000",
        "endUnit": "1200000000",
        "leverage": "100",
        "keepMarginRate": "0.0100",
    },
]


class Client:
    def __init__(
        self,
        *,
        margin_mode: str = "isolated",
        pos_mode: str = "one_way_mode",
        long_leverage: str = "20",
        short_leverage: str = "20",
        position: list[dict[str, str]] | None = None,
        clock_skew_ms: int = 0,
        position_lever: object = None,
    ) -> None:
        self.calls: list[str] = []
        self._account = {
            "available": "100",
            "usdtEquity": "100",
            "accountEquity": "100",
            "marginCoin": "USDT",
            "marginMode": margin_mode,
            "posMode": pos_mode,
            "isolatedLongLever": long_leverage,
            "isolatedShortLever": short_leverage,
        }
        self._position = position or []
        self._clock_skew_ms = clock_skew_ms
        self._position_lever = POSITION_LEVER_ROWS if position_lever is None else position_lever

    async def get_account(self, symbol: str):
        self.calls.append("account")
        return self._account

    async def get_single_position(self, symbol: str):
        self.calls.append("position")
        return self._position

    async def get_contracts(self):
        self.calls.append("contracts")
        return [
            {
                "symbol": "BTCUSDT",
                "pricePlace": "2",
                "priceEndStep": "1",
                "sizeMultiplier": "0.001",
                "minTradeNum": "0.001",
                "maxOrderQty": "100",
                "maxMarketOrderQty": "100",
                "minTradeUSDT": "5",
                "maxLever": "50",
                "contractValue": "1",
            }
        ]

    async def get_position_lever(self, symbol: str):
        self.calls.append("position-lever")
        assert symbol == "BTCUSDT"
        return self._position_lever

    async def get_ticker(self, symbol: str):
        self.calls.append("ticker")
        return {"lastPr": "60000"}

    async def get_clock_skew_ms(self) -> int:
        self.calls.append("clock")
        return self._clock_skew_ms


@pytest.mark.asyncio
async def test_preflight_uses_documented_account_position_contract_and_price_reads() -> None:
    client = Client()
    snapshot = await AsyncBitgetVenue(client).preflight("BTCUSDT")

    assert snapshot.available_balance == Decimal("100")
    assert snapshot.position is None
    assert snapshot.metadata.symbol == "BTCUSDT"
    assert snapshot.current_price == Decimal("60000")
    assert client.calls == ["contracts", "account", "position", "clock", "position-lever", "ticker"]


@pytest.mark.asyncio
async def test_preflight_attaches_resolvable_mmr_tiers() -> None:
    """Tiers must land on the metadata or every LIVE signal dies in the guard.

    Without this assertion, dropping the ``model_copy`` that attaches ``mm_tiers``
    keeps the whole suite green while LIVE goes back to
    "maintenance-margin tiers are required".
    """
    snapshot = await AsyncBitgetVenue(Client()).preflight("BTCUSDT")

    assert snapshot.metadata.mm_tiers == (
        MMTier(upper_bound_notional=Decimal("200000"), mmr=Decimal("0.0050")),
        MMTier(upper_bound_notional=None, mmr=Decimal("0.0100")),
    )
    tiers = snapshot.metadata.mm_tiers
    assert select_mmr(Decimal("10000"), tiers) == Decimal("0.0050")
    assert select_mmr(Decimal("200000"), tiers) == Decimal("0.0050")
    # Above the widest bound: the catch-all has to resolve even though the live
    # payload carries no `endUnit == 0` sentinel tier.
    assert select_mmr(Decimal("200001"), tiers) == Decimal("0.0100")
    assert select_mmr(Decimal("99999999999"), tiers) == Decimal("0.0100")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], "BTCUSDT", 0])
async def test_preflight_fails_closed_on_bad_position_lever_payload(payload: object) -> None:
    with pytest.raises(ValueError, match="position-lever"):
        await AsyncBitgetVenue(Client(position_lever=payload)).preflight("BTCUSDT")


@pytest.mark.asyncio
async def test_preflight_fails_closed_when_client_cannot_read_position_lever() -> None:
    class NoLeverClient(Client):
        get_position_lever = None  # type: ignore[assignment]

    with pytest.raises(ValueError, match="cannot read position-lever"):
        await AsyncBitgetVenue(NoLeverClient()).preflight("BTCUSDT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "message"),
    [
        (Client(margin_mode="crossed"), "isolated"),
        (Client(pos_mode="hedge_mode"), "position mode"),
        (Client(long_leverage="20", short_leverage="10"), "leverage"),
        (Client(clock_skew_ms=30_001), "clock skew"),
        (
            Client(
                position=[
                    {
                        "symbol": "BTCUSDT",
                        "holdSide": "long",
                        "total": "0.001",
                        "openPriceAvg": "60000",
                        "marginMode": "isolated",
                        "leverage": "20",
                    }
                ]
            ),
            "active position",
        ),
    ],
)
async def test_preflight_rejects_unsafe_account_state(client: Client, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        await AsyncBitgetVenue(client).preflight("BTCUSDT")


@pytest.mark.asyncio
async def test_all_active_symbols_preserve_hedge_rows_from_one_response():
    class PositionsClient:
        calls = 0

        async def get_all_positions(self):
            self.calls += 1
            return {
                "code": "00000",
                "data": [
                    {"symbol": "BTCUSDT", "total": "1", "holdSide": "long"},
                    {"symbol": "BTCUSDT", "total": "2", "holdSide": "short"},
                    {"symbol": "ETHUSDT", "total": "0"},
                ],
            }

    client = PositionsClient()
    snapshot = await AsyncBitgetVenue(client).active_position_snapshot()
    assert snapshot.symbols == ("BTCUSDT", "BTCUSDT")
    assert snapshot.observed_at.tzinfo is not None
    assert client.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [{"symbol": "BTCUSDT"}],
        [{"symbol": "BTCUSDT", "total": "NaN"}],
        [{"symbol": "BTCUSDT", "total": "Infinity"}],
        [{"symbol": "BTCUSDT", "total": "-1"}],
        [{"symbol": "BTCUSDT", "total": True}],
        [{"total": "1"}],
        [{"symbol": "btcUSDT", "total": "1"}],
        [{"symbol": " BTCUSDT", "total": "1"}],
        [{"symbol": "BTCUSDT", "total": None}],
        [{"symbol": "BTCUSDT", "total": ""}],
        [{"symbol": "BTCUSDT", "total": "junk"}],
        [None],
        {"data": None},
        {"code": "40099", "data": []},
        {"data": {}},
    ],
)
async def test_all_positions_invalid_evidence_refuses(payload):
    class PositionsClient:
        async def get_all_positions(self):
            return payload

    with pytest.raises(ValueError):
        await AsyncBitgetVenue(PositionsClient()).active_position_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "0", "-1"])
async def test_preflight_rejects_nonfinite_or_nonpositive_market_price(price):
    class InvalidTickerClient(Client):
        async def get_ticker(self, symbol):
            return {"lastPr": price}

    with pytest.raises(ValueError, match="finite and positive"):
        await AsyncBitgetVenue(InvalidTickerClient()).preflight("BTCUSDT")


@pytest.mark.asyncio
async def test_absent_contract_rejected_before_account_reads_or_preparation() -> None:
    class PreparingClient(Client):
        async def set_margin_mode(self, symbol: str, margin_mode: str):
            self.calls.append("set-margin-mode")
            self._account["marginMode"] = margin_mode
            return {"marginMode": margin_mode}

    client = PreparingClient(margin_mode="crossed")
    with pytest.raises(KeyError, match="unknown Bitget contract: FLOWUSDT"):
        await AsyncBitgetVenue(client).preflight("FLOWUSDT")
    assert client.calls == ["contracts"]
    assert client._account["marginMode"] == "crossed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contracts",
    [
        None,
        {},
        {"code": "40099", "data": []},
        "unavailable",
        [None],
        [{}],
        [{"symbol": None}],
        [{"symbol": " BTCUSDT"}],
        [{"symbol": "BTCUSDT", "priceEndStep": "untrusted-metadata"}],
    ],
)
async def test_malformed_contract_lookup_is_unavailable_before_account_reads(contracts) -> None:
    class MalformedClient(Client):
        async def get_contracts(self):
            self.calls.append("contracts")
            return contracts

    client = MalformedClient(margin_mode="crossed")
    with pytest.raises(BitgetApiError) as error:
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts"]
    assert client._account["marginMode"] == "crossed"
    assert "unknown Bitget contract" not in str(error.value)
    assert "untrusted-metadata" not in str(error.value)


@pytest.mark.asyncio
async def test_contract_provider_failure_is_not_relabelled_unsupported() -> None:
    failure = BitgetApiError("Bitget contracts unavailable", http_status=503)

    class UnavailableClient(Client):
        async def get_contracts(self):
            self.calls.append("contracts")
            raise failure

    client = UnavailableClient(margin_mode="crossed")
    with pytest.raises(BitgetApiError) as error:
        await AsyncBitgetVenue(client).preflight("FLOWUSDT")
    assert error.value is failure
    assert client.calls == ["contracts"]


@pytest.mark.asyncio
async def test_empty_valid_contract_lookup_proves_absence_without_account_reads() -> None:
    class EmptyClient(Client):
        async def get_contracts(self):
            self.calls.append("contracts")
            return []

    client = EmptyClient(margin_mode="crossed")
    with pytest.raises(KeyError, match="unknown Bitget contract"):
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts"]


@pytest.mark.asyncio
async def test_magic_preflight_accepts_public_unicode_catalog_neighbors() -> None:
    class MagicClient(Client):
        async def get_contracts(self):
            rows = await super().get_contracts()
            target = dict(rows[0], symbol="MAGICUSDT", pricePlace="5")
            return [
                {"symbol": "龙虾USDT"},
                target,
                {"symbol": "牛来USDT"},
                {"symbol": "哈基米USDT"},
            ]

        async def get_position_lever(self, symbol: str):
            self.calls.append("position-lever")
            assert symbol == "MAGICUSDT"
            return [dict(row, symbol=symbol) for row in POSITION_LEVER_ROWS]

    client = MagicClient()
    observation = {}
    with catalog_audit_context(observation):
        snapshot = await AsyncBitgetVenue(client).preflight("MAGICUSDT", read_only=True)

    assert snapshot.metadata.symbol == "MAGICUSDT"
    assert snapshot.metadata.price_tick == Decimal("0.00001")
    assert snapshot.metadata.mm_tiers
    assert client.calls == ["contracts", "account", "position", "clock", "position-lever", "ticker"]
    evidence = observation["contract_catalog"]
    assert evidence["code"] == "target_validated"
    assert evidence["row_count"] == 4
    assert evidence["matching_row_indexes"] == [1]
    assert evidence["unicode_symbol_count"] == 3
    assert evidence["unicode_symbols"] == ["龙虾USDT", "牛来USDT", "哈基米USDT"]
    assert len(evidence["payload_sha256"]) == 64


@pytest.mark.asyncio
async def test_successful_catalog_observation_survives_later_read_only_rejection() -> None:
    client = Client(margin_mode="crossed")
    observation = {}
    with catalog_audit_context(observation), pytest.raises(ValueError):
        await AsyncBitgetVenue(client).preflight("BTCUSDT", read_only=True)
    assert client.calls == ["contracts", "account", "position"]
    assert client._account["marginMode"] == "crossed"
    assert observation["contract_catalog"]["code"] == "target_validated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", " BTCUSDT"),
        ("pricePlace", None),
        ("pricePlace", "2.5"),
        ("pricePlace", -1),
        ("pricePlace", 13),
        ("priceEndStep", "NaN"),
        ("priceEndStep", "1e999999999"),
        ("sizeMultiplier", 0),
        ("minTradeNum", -1),
        ("maxOrderQty", "Infinity"),
        ("maxMarketOrderQty", False),
        ("minTradeUSDT", -1),
        ("maxLever", "20.5"),
        ("maxLever", True),
        ("contractValue", 0),
    ],
)
async def test_invalid_target_contract_is_never_admitted(field, value) -> None:
    class InvalidTargetClient(Client):
        async def get_contracts(self):
            rows = await super().get_contracts()
            rows[0][field] = value
            return rows

    client = InvalidTargetClient(margin_mode="crossed")
    with pytest.raises(BitgetCatalogError) as error:
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts"]
    evidence = error.value.audit_evidence
    assert evidence["stage"] == "contract_catalog"
    assert evidence["code"] == (
        "catalog_rows_invalid" if field == "symbol" else "target_metadata_invalid"
    )
    assert evidence["requested_symbol"] == "BTCUSDT"
    assert evidence["row_count"] == 1
    assert len(evidence["payload_sha256"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_symbol", ["BTCUSDT", "btcusdt"])
async def test_duplicate_requested_contract_is_ambiguous_before_account_reads(
    duplicate_symbol: str,
) -> None:
    class DuplicateClient(Client):
        async def get_contracts(self):
            rows = await super().get_contracts()
            # A bad duplicate must not be hidden by a valid first record.
            return rows + [{"symbol": duplicate_symbol, "priceEndStep": "invalid"}]

    client = DuplicateClient()
    with pytest.raises(BitgetCatalogError) as error:
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts"]
    assert error.value.audit_evidence["code"] == "target_ambiguous"
    assert error.value.audit_evidence["matching_row_count"] == 2
    assert error.value.audit_evidence["matching_row_indexes"] == [0, 1]


def test_absent_target_is_distinct_from_malformed_or_ambiguous_catalog() -> None:
    with pytest.raises(BitgetContractNotFound) as error:
        find_contract([{"symbol": "龙虾USDT"}, {"symbol": "BTCUSDT"}], "MAGICUSDT")
    assert error.value.audit_evidence["code"] == "target_missing"
    assert error.value.audit_evidence["matching_row_count"] == 0


def test_unicode_case_mapping_cannot_impersonate_ascii_requested_target() -> None:
    with pytest.raises(BitgetContractNotFound) as error:
        find_contract([{"symbol": "ſUSDT"}], "SUSDT")
    assert error.value.audit_evidence["code"] == "target_missing"


def test_catalog_diagnostics_are_bounded_and_do_not_dump_untrusted_fields() -> None:
    secret = "Bearer private-provider-secret"
    payload = [
        {"symbol": secret, "Authorization": secret, "private_prompt": secret} for _ in range(300)
    ]
    observation = {}
    with catalog_audit_context(observation), pytest.raises(BitgetCatalogError) as error:
        find_contract(payload, "MAGICUSDT")
    evidence = error.value.audit_evidence
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert evidence["code"] == "catalog_rows_invalid"
    assert evidence["invalid_row_count"] == 300
    assert len(evidence["invalid_rows"]) == 8
    assert [row["index"] for row in evidence["invalid_rows"]] == list(range(8))
    assert all(row["symbol_type"] == "str" for row in evidence["invalid_rows"])
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
    ).hexdigest()
    assert evidence["payload_sha256"] == expected_hash
    assert secret not in serialized
    assert "Authorization" not in serialized
    assert "private_prompt" not in serialized
    assert len(serialized) < 4096
    assert observation["contract_catalog"] == evidence


@pytest.mark.asyncio
async def test_malformed_neighbor_cannot_hide_behind_valid_requested_target() -> None:
    class InvalidNeighborClient(Client):
        async def get_contracts(self):
            return await super().get_contracts() + [{"symbol": None}]

    client = InvalidNeighborClient()
    with pytest.raises(BitgetCatalogError) as error:
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts"]
    assert error.value.audit_evidence["code"] == "catalog_rows_invalid"
    assert error.value.audit_evidence["invalid_rows"] == [
        {"index": 1, "row_type": "dict", "symbol_type": "null"}
    ]


def test_catalog_audit_context_restores_outer_and_does_not_collect_outside_scope() -> None:
    outer = {}
    inner = {}
    with catalog_audit_context(outer):
        find_contract([{"symbol": "BTCUSDT"}], "BTCUSDT")
        with catalog_audit_context(inner):
            find_contract([{"symbol": "MAGICUSDT"}], "MAGICUSDT")
    find_contract([{"symbol": "ETHUSDT"}], "ETHUSDT")
    assert outer["contract_catalog"]["requested_symbol"] == "BTCUSDT"
    assert inner["contract_catalog"]["requested_symbol"] == "MAGICUSDT"


@pytest.mark.asyncio
async def test_catalog_observations_are_isolated_between_concurrent_tasks() -> None:
    async def observe(symbol):
        observation = {}
        with catalog_audit_context(observation):
            await asyncio.sleep(0)
            find_contract([{"symbol": symbol}], symbol)
            await asyncio.sleep(0)
        return observation["contract_catalog"]

    btc, magic = await asyncio.gather(observe("BTCUSDT"), observe("MAGICUSDT"))
    assert btc["requested_symbol"] == "BTCUSDT"
    assert magic["requested_symbol"] == "MAGICUSDT"


@pytest.mark.asyncio
async def test_invalid_selected_metadata_exposes_types_not_untrusted_values() -> None:
    secret = "Bearer private-provider-secret"

    class InvalidMetadataClient(Client):
        async def get_contracts(self):
            rows = await super().get_contracts()
            rows[0]["priceEndStep"] = secret
            rows[0]["Authorization"] = secret
            return rows

    client = InvalidMetadataClient()
    with pytest.raises(BitgetCatalogError) as error:
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    evidence = error.value.audit_evidence
    assert evidence["code"] == "target_metadata_invalid"
    assert evidence["field_types"]["priceEndStep"] == "str"
    assert evidence["selected_metadata"]["symbol"] == "BTCUSDT"
    assert evidence["selected_metadata"]["sizeMultiplier"] == "0.001"
    assert "priceEndStep" not in evidence["selected_metadata"]
    assert secret not in json.dumps(evidence)
    assert "Authorization" not in json.dumps(evidence)
    assert client.calls == ["contracts"]


@pytest.mark.asyncio
async def test_supported_contract_still_prepares_and_verifies_isolated_margin_mode() -> None:
    class PreparingClient(Client):
        async def set_margin_mode(self, symbol: str, margin_mode: str):
            self.calls.append("set-margin-mode")
            self._account["marginMode"] = margin_mode
            return {"marginMode": margin_mode}

    client = PreparingClient(margin_mode="crossed")
    snapshot = await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert snapshot.account.margin_mode == "isolated"
    assert snapshot.metadata.mm_tiers
    assert client.calls == [
        "contracts",
        "account",
        "position",
        "set-margin-mode",
        "account",
        "clock",
        "position-lever",
        "ticker",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("skew_ms", [-30_000, 30_000])
async def test_preflight_preserves_inclusive_clock_safety_boundary(skew_ms: int) -> None:
    snapshot = await AsyncBitgetVenue(Client(clock_skew_ms=skew_ms)).preflight("BTCUSDT")
    assert snapshot.metadata.symbol == "BTCUSDT"


@pytest.mark.asyncio
@pytest.mark.parametrize("skew_ms", [-30_001, 30_001])
async def test_preflight_blocks_real_clock_offset_past_boundary(skew_ms: int) -> None:
    with pytest.raises(ValueError, match="clock skew exceeds"):
        await AsyncBitgetVenue(Client(clock_skew_ms=skew_ms)).preflight("BTCUSDT")


@pytest.mark.asyncio
async def test_preflight_does_not_turn_unavailable_clock_into_zero() -> None:
    class UnavailableClockClient(Client):
        async def get_clock_skew_ms(self):
            self.calls.append("clock")
            raise BitgetApiError("Bitget clock sample unavailable")

    client = UnavailableClockClient()
    with pytest.raises(BitgetApiError, match="clock sample unavailable"):
        await AsyncBitgetVenue(client).preflight("BTCUSDT")
    assert client.calls == ["contracts", "account", "position", "clock"]
