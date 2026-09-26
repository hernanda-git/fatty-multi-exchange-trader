from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
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
        return self._account

    async def get_single_position(self, symbol: str):
        return self._position

    async def get_contracts(self):
        return [
            {
                "symbol": "BTCUSDT",
                "pricePlace": "2",
                "priceEndStep": "0.01",
                "sizeMultiplier": "0.001",
                "minTradeNum": "0.001",
                "maxTradeNum": "100",
                "minTradeUSDT": "5",
                "maxLever": "50",
                "contractValue": "1",
            }
        ]

    async def get_position_lever(self, symbol: str):
        assert symbol == "BTCUSDT"
        return self._position_lever

    async def get_ticker(self, symbol: str):
        return {"lastPr": "60000"}

    async def get_clock_skew_ms(self) -> int:
        return self._clock_skew_ms


@pytest.mark.asyncio
async def test_preflight_uses_documented_account_position_contract_and_price_reads() -> None:
    snapshot = await AsyncBitgetVenue(Client()).preflight("BTCUSDT")

    assert snapshot.available_balance == Decimal("100")
    assert snapshot.position is None
    assert snapshot.metadata.symbol == "BTCUSDT"
    assert snapshot.current_price == Decimal("60000")


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
