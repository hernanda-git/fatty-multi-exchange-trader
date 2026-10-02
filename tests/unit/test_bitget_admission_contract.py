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
    }
    values[field] = value
    with pytest.raises(ValueError):
        BitgetEntrySubmission(**values)  # type: ignore[arg-type]
