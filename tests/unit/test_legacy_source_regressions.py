from __future__ import annotations

from decimal import Decimal

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.integration import AnalysisStatus, analyze_with_fallback
from fatty_trader.analyzer.trade_management import ManagementAction, parse_source_management


def test_explicit_entry_range_and_plural_targets_are_fallback_parseable() -> None:
    signal = parse_explicit_signal(
        "#XPL $XPL LONG TRADE ENTRY: 0.098 - 0.096 TARGET: 0.117 Stoploss: 0.09475",
        message_id=16108,
    )

    assert signal is not None
    assert signal.pair_token == "XPL"
    assert signal.entry_price == Decimal("0.098")
    assert signal.stop_loss == Decimal("0.09475")
    assert signal.take_profits == (Decimal("0.117"),)


def test_plural_targets_preserve_every_target() -> None:
    signal = parse_explicit_signal(
        "#PUMP $PUMP LONG TRADE ENTRY: 0.00427 TARGETS: 0.004438 - 0.004915 STOPLOSS: 0.00416",
        message_id=16090,
    )

    assert signal is not None
    assert signal.take_profits == (Decimal("0.004438"), Decimal("0.004915"))


def test_management_symbol_prefers_explicit_ethfi_token() -> None:
    result = parse_source_management("sl to entry $ETHFI")

    assert result is not None
    assert result.action is ManagementAction.SL_TO_ENTRY
    assert result.symbol == "ETHFIUSDT"


def test_management_symbol_supports_both_word_orders() -> None:
    before = parse_source_management("$WLD TP1 booked here")
    after = parse_source_management("book TP1 on $WLD")

    assert before is not None and before.symbol == "WLDUSDT"
    assert after is not None and after.symbol == "WLDUSDT"
    assert before.action is ManagementAction.TP1_BOOKED
    assert after.action is ManagementAction.TP1_BOOKED


def test_management_without_position_is_not_reported_as_reconciled() -> None:
    from fatty_trader.execution.source_management import (
        InMemorySourceManagementStore,
        SourceManagementExecutor,
        SourceManagementUpdate,
    )

    class Gateway:
        def get_positions(self, symbol: str) -> list[dict[str, object]]:
            return []

        def close_reduce_only(self, **_: object) -> None:
            raise AssertionError("no provider POST expected")

        def replace_stop_loss(self, **_: object) -> None:
            raise AssertionError("no provider POST expected")

    update = SourceManagementUpdate.new("a" * 64, "ETHFIUSDT", ManagementAction.SL_TO_ENTRY)
    store = InMemorySourceManagementStore([update])

    assert SourceManagementExecutor(store, Gateway()).run_once("worker") == "failed"
    assert store.get(update.id).state == "failed"


def test_codex_no_signal_falls_back_to_explicit_xpl_setup() -> None:
    result = analyze_with_fallback(
        text=("#XPL $XPL LONG TRADE ENTRY: 0.098 - 0.096 TARGET: 0.117 Stoploss: 0.09475"),
        message_id=16108,
        codex_runner=lambda _: CodexRunResult(
            succeeded=True,
            terminal_failure=False,
            timed_out=False,
            exit_code=0,
            failure_reason=None,
            stdout='{"actionable": false}',
            stderr="",
        ),
    )

    assert result.status is AnalysisStatus.FALLBACK_ACCEPTED
    assert result.signal is not None
    assert result.signal.pair_token == "XPL"


def test_management_symbol_rejects_reserved_prefix_candidates() -> None:
    for text in ("$SL to entry $ETHFI", "#SL to entry #ETHFI"):
        result = parse_source_management(text)
        assert result is not None
        assert result.symbol == "ETHFIUSDT"
    assert parse_source_management("SLUSDT to entry") is None
