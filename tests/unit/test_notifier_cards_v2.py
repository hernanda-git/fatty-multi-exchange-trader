"""Premium execution cards: English, no raw enums, realised results."""

from __future__ import annotations

from fatty_trader.notifications import format_notification_html
from fatty_trader.notifier_cards import human_reason

REJECTED = {
    "kind": "execution-event",
    "to_state": "REJECTED",
    "reason": "protection-stream-latched",
    "dispatch_id": "b545d68b-3d4f-46f4-9958-75042b067515",
    "evidence": {
        "symbol": "ZECUSDT",
        "direction": "LONG",
        "source_message_id": 16444,
        "source_received_at": "2026-11-03T21:13:19+00:00",
    },
}

CLOSED = {
    "kind": "execution-event",
    "to_state": "CLOSED",
    "reason": "recovery-closed-close-fill-verified",
    "dispatch_id": "187e63cd-ff06-46e7-807f-b3d51f8b9001",
    "evidence": {"symbol": "SNDKUSDT", "direction": "SHORT", "source_message_id": 16442},
    "result": {
        "entry": 1121.69,
        "exit": 1145.74,
        "gross": -0.4569,
        "fees": 0.0258,
        "net": -0.4828,
        "net_pct": -2.15,
        "r": -1.08,
        "held": "2h 07m",
    },
}


def test_rejection_card_is_human_readable() -> None:
    card = format_notification_html(REJECTED)
    assert "ZEC/USDT" in card
    assert "not traded" in card
    assert "protection stream down" in card
    assert "no order was sent" in card
    assert "protection-stream-latched" not in card


def test_closed_card_shows_realised_pnl_and_r() -> None:
    card = format_notification_html(CLOSED)
    assert "SNDK/USDT" in card
    assert "1,121.69" in card
    assert "1,145.74" in card
    assert "0.483" in card
    assert "1.08R" in card
    assert "2h 07m" in card


def test_closed_without_result_still_renders() -> None:
    payload = {key: value for key, value in CLOSED.items() if key != "result"}
    card = format_notification_html(payload)
    assert "SNDK/USDT" in card
    assert "verified against venue fill" in card


def test_enrichment_is_applied_when_supplied() -> None:
    payload = {key: value for key, value in CLOSED.items() if key != "result"}

    def enrich(_: object) -> dict[str, object]:
        return {"result": CLOSED["result"]}

    card = format_notification_html(payload, enrich=enrich)
    assert "0.483" in card
    assert "1.08R" in card


def test_enrichment_failure_never_blocks_delivery() -> None:
    payload = {key: value for key, value in CLOSED.items() if key != "result"}

    def broken(_: object) -> dict[str, object]:
        raise RuntimeError("db down")

    card = format_notification_html(payload, enrich=broken)
    assert "SNDK/USDT" in card


def test_unknown_reason_is_humanised_not_raw() -> None:
    assert human_reason("some-new-reason-code") == "some new reason code"
    assert human_reason("") == "no reason recorded"
    assert human_reason("none") == "no reason recorded"
