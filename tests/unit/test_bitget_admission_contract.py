from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from fatty_trader.execution.bitget_admission import BitgetEntrySubmission


def test_entry_submission_is_immutable_and_carries_all_sizing_evidence() -> None:
    submission = BitgetEntrySubmission(
        quantity=Decimal("0.004"),
        effective_leverage=20,
        planned_margin_usdt=Decimal("10"),
        planned_notional_usdt=Decimal("256"),
        margin_mode="isolated",
        balance_snapshot_id=UUID("12345678-1234-5678-1234-567812345678"),
        margin_reservation_id=UUID("87654321-4321-8765-4321-876543218765"),
        observed_at=datetime(2026, 9, 23, tzinfo=UTC),
        planned_stop_loss=Decimal("63000"),
        planned_take_profits=(Decimal("65000"),),
    )

    assert submission.quantity == Decimal("0.004")
    assert submission.effective_leverage == 20
    assert submission.planned_margin_usdt == Decimal("10")
    assert submission.planned_notional_usdt == Decimal("256")
    assert submission.margin_mode == "ISOLATED"
    assert submission.balance_snapshot_id is not None
    assert submission.margin_reservation_id is not None
    with pytest.raises((AttributeError, TypeError)):
        submission.effective_leverage = 50  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", Decimal("0")),
        ("effective_leverage", 0),
        ("planned_margin_usdt", Decimal("0")),
        ("planned_notional_usdt", Decimal("0")),
        ("margin_mode", "crossed"),
        ("observed_at", datetime(2026, 9, 23)),
        ("planned_stop_loss", Decimal("NaN")),
        ("planned_take_profits", ()),
        ("planned_take_profits", (Decimal("Infinity"),)),
    ],
)
def test_entry_submission_rejects_invalid_admission_evidence(field: str, value: object) -> None:
    values: dict[str, object] = {
        "quantity": Decimal("0.004"),
        "effective_leverage": 20,
        "planned_margin_usdt": Decimal("10"),
        "planned_notional_usdt": Decimal("256"),
        "margin_mode": "ISOLATED",
        "balance_snapshot_id": UUID("12345678-1234-5678-1234-567812345678"),
        "margin_reservation_id": UUID("87654321-4321-8765-4321-876543218765"),
        "observed_at": datetime(2026, 9, 23, tzinfo=UTC),
        "planned_stop_loss": Decimal("63000"),
        "planned_take_profits": (Decimal("65000"),),
    }
    values[field] = value
    with pytest.raises(ValueError):
        BitgetEntrySubmission(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "direction,stop,targets,expected",
    [
        ("LONG", "99.94", ("100.15", "100.26"), ("99.9", ("100.2", "100.3"))),
        ("SHORT", "100.06", ("99.85", "99.74"), ("100.1", ("99.9", "99.7"))),
    ],
)
def test_price_normalization_uses_half_up_ticks_for_both_sides(direction, stop, targets, expected):
    from fatty_trader.domain.enums import Direction
    from fatty_trader.execution.bitget_admission import normalized_protection_prices

    normalized = normalized_protection_prices(
        direction=Direction(direction),
        entry=Decimal("100"),
        canonical_entry=Decimal("100"),
        stop_loss=Decimal(stop),
        take_profits=tuple(Decimal(target) for target in targets),
        price_tick=Decimal("0.1"),
    )
    assert normalized == (Decimal(expected[0]), tuple(Decimal(target) for target in expected[1]))


def test_price_normalization_preserves_canonical_geometry_when_market_has_moved():
    from fatty_trader.domain.enums import Direction
    from fatty_trader.execution.bitget_admission import normalized_protection_prices

    with pytest.raises(ValueError, match="rounded stop loss"):
        normalized_protection_prices(
            direction=Direction.LONG,
            entry=Decimal("101"),
            canonical_entry=Decimal("100"),
            stop_loss=Decimal("99.96"),
            take_profits=(Decimal("102"),),
            price_tick=Decimal("0.1"),
        )
