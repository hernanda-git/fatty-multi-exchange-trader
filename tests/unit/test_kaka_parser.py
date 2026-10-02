"""Parser tests pinned to the real `Kaka trades` message shapes (2026-09-01 → 2026-09-28)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.kaka.parser import KakaEventType, parse_kaka_event


def test_structured_setup_block_parses_entry_stop_and_dca() -> None:
    event = parse_kaka_event(
        "🧨BTC LONG  SETUP\n🎯 Just follow the plan.\n📍 Entry: $76,150\n"
        "💰 Margin: $280\n➕ DCA: $76,450\n🛑 SL: $75,210",
        message_id=157,
    )

    assert event.type is KakaEventType.OPEN
    assert event.symbol == "BTCUSDT"
    assert event.side == "LONG"
    assert event.entry == Decimal("76150")
    assert event.stop_loss == Decimal("75210")
    assert event.dca == Decimal("76450")


def test_leading_dash_is_a_separator_and_commas_are_stripped() -> None:
    event = parse_kaka_event("Short $ETH | SL -2401", message_id=145)

    assert event.type is KakaEventType.OPEN
    assert event.symbol == "ETHUSDT"
    assert event.side == "SHORT"
    assert event.stop_loss == Decimal("2401")  # not -2401


def test_entry_with_second_dca_and_stop() -> None:
    event = parse_kaka_event(
        "Short -$ETH AGAIN | ENTRY -2739 | 2ND DCA -2775 | SL -2790", message_id=298
    )

    assert event.type is KakaEventType.OPEN
    assert (event.entry, event.dca, event.stop_loss) == (
        Decimal("2739"),
        Decimal("2775"),
        Decimal("2790"),
    )


def test_dca_level_alone_is_an_open_without_entry() -> None:
    event = parse_kaka_event("Long -$SOL | 2ND DCA -118.50 | SL 117.50", message_id=308)

    assert event.type is KakaEventType.OPEN
    assert event.symbol == "SOLUSDT"
    assert event.entry is None
    assert event.dca == Decimal("118.50")
    assert event.stop_loss == Decimal("117.50")


def test_lowercase_side_word_after_the_symbol() -> None:
    event = parse_kaka_event(
        "Eth long  | Entry: $2,517 | 💰 Margin: $210 | ➕ DCA: $2,495 | 🛑 SL: $2,482",
        message_id=153,
    )

    assert event.type is KakaEventType.OPEN
    assert event.symbol == "ETHUSDT"
    assert event.side == "LONG"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Set sl at be", KakaEventType.BREAKEVEN),
        (
            "Set your SL at breakeven. If the market moves up, we'll avoid taking a loss.",
            KakaEventType.BREAKEVEN,
        ),
        ("Remove tp", KakaEventType.TP_REMOVED),
        ("Tp -2540", KakaEventType.TP),
        ("Tp hit | Profit+32$", KakaEventType.TP),
        ("Set your SL at 92.38.", KakaEventType.STOP_MOVE),
        ("Move sl -74,980", KakaEventType.STOP_MOVE),
        ("SL 76,200", KakaEventType.STOP_MOVE),
        (
            "Set your SL at 86,200 so that even if the market moves up, "
            "we can still stay in profit.",
            KakaEventType.STOP_MOVE,
        ),
        ("Take 2nd entry 2475", KakaEventType.ADD),
        ("Take entry now", KakaEventType.ADD),
        ("We'll take our second entry at $2,498. 📈", KakaEventType.ADD),
        (
            "We'll use the remaining balance for our 2nd entry at $81,500. SL: $81,980 🔒📉",
            KakaEventType.ADD,
        ),
        ("Closed", KakaEventType.CLOSE),
        ("Close | Profit +95$", KakaEventType.CLOSE),
        ("Trade Closed 🔴 | Loss: -$10", KakaEventType.CLOSE),
        ("SL hit | Loss -46$", KakaEventType.STOP_HIT),
        ("SL hit again  | Loss -44$", KakaEventType.STOP_HIT),
        ("❌ SL HIT | Loss: -$30 | Wallet: $282", KakaEventType.STOP_HIT),
    ],
)
def test_management_message_classification(text: str, expected: KakaEventType) -> None:
    assert parse_kaka_event(text, message_id=1).type is expected


def test_numbers_are_extracted_from_management_messages() -> None:
    assert parse_kaka_event("Move sl -74,980", message_id=192).stop_loss == Decimal("74980")
    assert parse_kaka_event("Tp -2540", message_id=149).take_profit == Decimal("2540")
    add = parse_kaka_event(
        "We'll use the remaining balance for our 2nd entry at $81,500. SL: $81,980 🔒📉",
        message_id=230,
    )
    assert add.entry == Decimal("81500")
    assert add.stop_loss == Decimal("81980")


def test_stop_hit_in_profit_is_a_win_not_a_loss() -> None:
    event = parse_kaka_event("SL hit  |  Profit+50$  |  Wallet -300$", message_id=256)

    assert event.type is KakaEventType.STOP_HIT
    assert event.reported_pnl == Decimal("50")


def test_losses_are_negative_and_profits_positive() -> None:
    assert parse_kaka_event("SL hit | Loss -46$", message_id=156).reported_pnl == Decimal("-46")
    assert parse_kaka_event("Close | Profit +95$", message_id=197).reported_pnl == Decimal("95")


def test_cancels_are_never_trades() -> None:
    for text in (
        "Cancelling the entry for now. The market is highly volatile and fluctuating sharply",
        "We're cancelling the second entry. Our SL will remain at 2485.",
        "Forget it,. Let's cancel the trade for today. See you tomorrow. 📈🔥",
        "Don't take this LONG entry yet. The market may dip a little further.",
    ):
        assert parse_kaka_event(text, message_id=1).type is KakaEventType.CANCEL


def test_noise_never_opens_a_trade() -> None:
    for text in (
        "Wallet -444$",
        "See you tomorrow. The market is too volatile today",
        "👍👍👍",
        "https://partner.blofin.com/d/KakaTrades",
        "Should I start a 5X Copy Trading Challenge? 🚀 💰 $100 → $500 (5X)",
        "Just as I expected, the market dropped — it made a sharp move to the downside. 📉🔥",
        "Next, we'll take this SHORT entry.",
        "I'm sorry for the losses, everyone. From now on, I'll focus on playing it much safer",
    ):
        event = parse_kaka_event(text, message_id=1)
        assert event.type is KakaEventType.NOISE, text


def test_channel_typo_enrty_still_parses_the_entry() -> None:
    # Verbatim from the channel (2026-09-13): "👊ENRTY: $76,150".
    event = parse_kaka_event(
        "BTC LONG SETUP | 🎯 Just follow the plan. | 👊ENRTY: $76,150 | "
        "💰 Margin: $280$ | 🛑 SL: $75,210",
        message_id=157,
    )

    assert event.type is KakaEventType.OPEN
    assert event.entry == Decimal("76150")
    assert event.stop_loss == Decimal("75210")


def test_preamble_before_the_entry_still_parses() -> None:
    # Also verbatim: "Let's take a small risk… | 🔴 SHORT $ETH | 🛑 SL: 2,720"
    event = parse_kaka_event(
        "Let's take a small risk and see how it plays out. | 🔴 SHORT $ETH | 🛑 SL: 2,720",
        message_id=272,
    )

    assert event.type is KakaEventType.OPEN
    assert event.symbol == "ETHUSDT"
    assert event.side == "SHORT"
    assert event.entry is None  # market entry
    assert event.stop_loss == Decimal("2720")


def test_only_a_real_entry_shape_becomes_an_open() -> None:
    # Mentions a direction and a stop, but no symbol: not tradable.
    assert (
        parse_kaka_event("Short again | SL -76,350", message_id=204).type is not KakaEventType.OPEN
    )
