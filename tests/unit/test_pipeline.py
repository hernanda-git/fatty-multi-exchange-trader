from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.domain.enums import DispatchState, Exchange
from fatty_trader.execution.fanout import FanoutPlanner
from fatty_trader.storage.memory import InMemoryDispatchRepository


def test_explicit_signal_fans_out_once_per_exchange() -> None:
    signal = parse_explicit_signal("BTCUSDT LONG MARKET SL 64000 TP 64630", message_id=1842)
    assert signal is not None

    repository = InMemoryDispatchRepository()
    planner = FanoutPlanner(repository)
    first = planner.plan(signal)
    duplicate = planner.plan(signal)

    assert {(item.exchange, item.state) for item in first} == {
        (Exchange.BINANCE, DispatchState.QUEUED),
        (Exchange.BITGET, DispatchState.QUEUED),
    }
    assert duplicate == first
    assert repository.count == 2


def test_ambiguous_text_cannot_create_a_dispatch() -> None:
    assert parse_explicit_signal("BTC looks strong, maybe long soon", message_id=1) is None


def test_venue_failure_does_not_change_other_dispatch() -> None:
    signal = parse_explicit_signal("ETHUSDT SHORT MARKET SL 2200 TP 2100", message_id=7)
    assert signal is not None
    repository = InMemoryDispatchRepository()
    dispatches = FanoutPlanner(repository).plan(signal)

    repository.set_state(dispatches[0].id, DispatchState.REJECTED)

    assert repository.get(dispatches[0].id).state is DispatchState.REJECTED
    assert repository.get(dispatches[1].id).state is DispatchState.QUEUED


def test_parser_rejects_wrong_side_stop() -> None:
    assert parse_explicit_signal("BTCUSDT LONG MARKET SL 65000", message_id=8) is None
