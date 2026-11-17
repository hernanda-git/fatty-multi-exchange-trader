"""Read-only signal ledger and exit-policy replay.

Answers two questions with one command, using only GET requests and a read-only
database transaction:

1. What did the bot actually realise per source signal? (ledger)
2. What would the same signals have produced under four exit policies? (replay)

Counterfactual entries are modelled on the signal's own levels, so the replay
measures the *policy*, not the bot's execution quality. Intrabar ambiguity is
resolved conservatively: when one candle contains both the stop and the target,
the stop is assumed first.

Usage (inside a container that has the package, PG env and network):

    python scripts/signal_replay.py --days 90 [--json]

No POST, no writes, no credential logging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import psycopg

BASE_URL = "https://api.bitget.com"
CANDLES_PATH = "/api/v2/mix/market/candles"
FEE_RATE = Decimal("0.0006")  # taker, per side, measured from live fills
HORIZON = timedelta(hours=48)
ENTRY_WINDOW = timedelta(minutes=30)
GRANULARITY = "5m"
MAX_CANDLES = 200


@dataclass(frozen=True)
class Signal:
    signal_id: str
    created_at: datetime
    received_at: datetime | None
    pair_token: str
    direction: str
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    symbol: str | None = None


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass
class Outcome:
    policy: str
    filled: bool
    r_multiple: Decimal = Decimal("0")
    exit_reason: str = "no-fill"
    exit_ts: datetime | None = None


@dataclass
class Row:
    signal: Signal
    realized_r: Decimal | None = None
    realized_reason: str = "no-dispatch"
    policies: dict[str, Outcome] = field(default_factory=dict)


def _load_signals(conn: psycopg.Connection, since: datetime) -> list[Signal]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.created_at, t.received_at, s.pair_token, s.direction,
                      s.entry_price, s.stop_loss, s.take_profits
               FROM canonical_signals s
               JOIN telegram_messages t ON t.id = s.message_id
               WHERE s.created_at >= %s
               ORDER BY s.created_at""",
            (since,),
        )
        rows = cur.fetchall()
        signals = [
            Signal(
                signal_id=str(row[0]),
                created_at=row[1],
                received_at=row[2],
                pair_token=str(row[3]),
                direction=str(row[4]).upper(),
                entry=Decimal(str(row[5])),
                stop=Decimal(str(row[6])),
                targets=tuple(Decimal(str(v)) for v in (row[7] or [])),
            )
            for row in rows
        ]
        if not signals:
            return []
        cur.execute(
            """SELECT i.dispatch_id, i.symbol
               FROM live_order_intents i
               WHERE i.exchange = 'bitget' AND i.role = 'ENTRY'
               ORDER BY i.created_at""",
        )
        by_dispatch = {str(r[0]): str(r[1]) for r in cur.fetchall() if r[1]}
        cur.execute(
            """SELECT d.source_id, d.id FROM dispatches d
               WHERE d.exchange = 'bitget' AND d.source_type = 'canonical_signal'""",
        )
        symbol_for_signal = {
            str(source_id): by_dispatch.get(str(dispatch_id))
            for source_id, dispatch_id in cur.fetchall()
        }
    return [Signal(**{**s.__dict__, "symbol": symbol_for_signal.get(s.signal_id)}) for s in signals]


def _load_realized(conn: psycopg.Connection, since: datetime) -> dict[str, tuple[Decimal, str]]:
    """Realised R per signal over that signal's position epoch.

    Fills carry no dispatch id, so the epoch is bounded by the next signal on the
    same symbol (or 48h). This keeps a native stop/TP close attached to the entry
    that opened it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.created_at, s.pair_token, s.entry_price, s.stop_loss,
                      coalesce((SELECT min(i.symbol) FROM dispatches d
                                JOIN live_order_intents i ON i.dispatch_id = d.id
                                WHERE d.source_id = s.id AND i.role = 'ENTRY'), NULL)
               FROM canonical_signals s
               WHERE s.created_at >= %s
               ORDER BY s.created_at""",
            (since,),
        )
        signals = [
            (str(sid), created, str(pair), Decimal(str(entry)), Decimal(str(stop)), sym)
            for sid, created, pair, entry, stop, sym in cur.fetchall()
        ]
        cur.execute(
            """SELECT symbol, client_order_id, price, quantity, realized_pnl, fee, filled_at
               FROM fills WHERE exchange = 'bitget' AND filled_at >= %s
               ORDER BY filled_at""",
            (since,),
        )
        fills = cur.fetchall()

    by_symbol_rows: dict[str, list[tuple]] = {}
    for row in fills:
        by_symbol_rows.setdefault(str(row[0]), []).append(row)

    result: dict[str, tuple[Decimal, str]] = {}
    # Epoch end: the next signal on the same symbol, else 48 hours.
    windows: list[tuple[str, str, datetime, datetime, Decimal, Decimal, Decimal]] = []
    for index, (sid, created, pair, entry, stop, sym) in enumerate(signals):
        symbol = sym or f"{pair}USDT"
        end = created + HORIZON
        for later in signals[index + 1 :]:
            later_symbol = later[5] or f"{later[2]}USDT"
            if later_symbol == symbol:
                end = min(end, later[1])
                break
        windows.append((sid, symbol, created, end, entry, stop, Decimal("0")))

    for sid, symbol, start, end, entry, stop, _unused in windows:
        rows = [r for r in by_symbol_rows.get(symbol, []) if start <= r[6] <= end]
        if not rows:
            continue
        entry_qty = sum((Decimal(str(r[3])) for r in rows if (r[4] or 0) == 0), Decimal("0"))
        realized = sum((Decimal(str(r[4] or 0)) for r in rows), Decimal("0"))
        fees = sum((Decimal(str(r[5] or 0)) for r in rows), Decimal("0"))
        risk_per_unit = abs(entry - stop)
        if entry_qty <= 0 or risk_per_unit <= 0 or realized == 0:
            continue
        result[sid] = (
            ((realized - fees) / (risk_per_unit * entry_qty)).quantize(Decimal("0.0001")),
            "realized",
        )
    return result


async def _candles(
    client: httpx.AsyncClient, symbol: str, start: datetime, end: datetime
) -> list[Candle]:
    collected: dict[int, Candle] = {}
    cursor = int(end.timestamp() * 1000)
    stop_at = int(start.timestamp() * 1000)
    for _ in range(24):  # 24 * 200 * 5m ≈ 40h of coverage headroom
        try:
            response = await client.get(
                BASE_URL + CANDLES_PATH,
                params={
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    "granularity": GRANULARITY,
                    "limit": MAX_CANDLES,
                    "endTime": cursor,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            break
        rows = payload.get("data") or []
        if not rows:
            break
        oldest = cursor
        for raw in rows:
            ts = int(raw[0]) // 1000
            if ts < oldest:
                oldest = ts
            collected[ts] = Candle(
                ts=datetime.fromtimestamp(ts, UTC),
                open=Decimal(str(raw[1])),
                high=Decimal(str(raw[2])),
                low=Decimal(str(raw[3])),
                close=Decimal(str(raw[4])),
            )
        if oldest <= stop_at:
            break
        cursor = oldest - 1
    return [c for ts, c in sorted(collected.items()) if start.timestamp() <= ts <= end.timestamp()]


async def _contract_symbols(client: httpx.AsyncClient) -> dict[str, str]:
    """Map a channel pair token to a real USDT-FUTURES instrument.

    The channel writes "$BONK" while the instrument is 1000BONKUSDT, so a plain
    PAIR+USDT fallback silently drops most signals.
    """
    try:
        response = await client.get(
            BASE_URL + "/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES"},
        )
        response.raise_for_status()
        rows = response.json().get("data") or []
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        base = str(row.get("baseCoin", "")).upper()
        if not symbol or not base:
            continue
        for token in (base, base.lstrip("0123456789"), base.removeprefix("1M")):
            if token:
                mapping.setdefault(token, symbol)
        if symbol.endswith("USDT"):
            mapping.setdefault(symbol.removesuffix("USDT"), symbol)
    return mapping


def _simulate(signal: Signal, candles: list[Candle], policy: str) -> Outcome:
    if not candles:
        return Outcome(policy=policy, filled=False, exit_reason="no-candles")

    long = signal.direction == "LONG"
    entry, stop = signal.entry, signal.stop
    risk = (entry - stop) if long else (stop - entry)
    if risk <= 0:
        return Outcome(policy=policy, filled=False, exit_reason="invalid-risk")

    first_target = signal.targets[0] if signal.targets else None
    fill_deadline = signal.created_at + ENTRY_WINDOW
    first_candle = candles[0]
    # Mirrors route_entry: a signal whose price already passed its entry is taken
    # at market; a pullback signal waits for a limit at the signal entry.
    passed = (first_candle.open > entry) if long else (first_candle.open < entry)
    fill_price = entry
    filled_at: Candle | None = None
    if passed:
        fill_price = first_candle.open
        filled_at = first_candle
    else:
        for candle in candles:
            if candle.ts > fill_deadline:
                break
            if (long and candle.low <= entry) or (not long and candle.high >= entry):
                filled_at = candle
                break
    if filled_at is None:
        return Outcome(policy=policy, filled=False, exit_reason="limit-not-filled")
    if fill_price != entry:
        risk = (fill_price - stop) if long else (stop - fill_price)
        if risk <= 0:
            return Outcome(policy=policy, filled=False, exit_reason="fill-beyond-stop")

    if policy in {"source_faithful", "full_at_first_target"} and first_target is None:
        policy = "hold_3r"
    target = {
        "source_faithful": first_target,
        "full_at_first_target": first_target,
        "half_tp1_trail": first_target,
        "hold_3r": fill_price + 3 * risk if long else fill_price - 3 * risk,
        "full_at_1r": fill_price + risk if long else fill_price - risk,
    }[policy]

    remaining = Decimal("1")
    realised = Decimal("0")
    stop_now = stop
    breakeven = fill_price

    def r_of(price: Decimal) -> Decimal:
        move = (price - fill_price) if long else (fill_price - price)
        return move / risk

    for candle in candles:
        if candle.ts < filled_at.ts:
            continue
        hit_stop = candle.low <= stop_now if long else candle.high >= stop_now
        hit_target = target is not None and (
            (candle.high >= target) if long else (candle.low <= target)
        )
        # Conservative: a candle containing both is treated as a stop first.
        if hit_stop:
            realised += remaining * r_of(stop_now)
            return Outcome(policy=policy, filled=True, r_multiple=realised, exit_reason="stop")
        if hit_target:
            if policy == "half_tp1_trail":
                share = Decimal("0.5") if remaining == Decimal("1") else remaining
                realised += share * r_of(target)
                remaining -= share
                if remaining <= 0:
                    return Outcome(
                        policy=policy, filled=True, r_multiple=realised, exit_reason="tp1"
                    )
                stop_now = breakeven
                target = None
                continue
            realised += remaining * r_of(target)
            return Outcome(policy=policy, filled=True, r_multiple=realised, exit_reason="target")
        if candle.ts > filled_at.ts + HORIZON:
            realised += remaining * r_of(candle.close)
            return Outcome(policy=policy, filled=True, r_multiple=realised, exit_reason="horizon")
    realised += remaining * r_of(candles[-1].close)
    return Outcome(policy=policy, filled=True, r_multiple=realised, exit_reason="end-of-data")


def _stats(values: Iterable[Decimal]) -> dict[str, float | int]:
    rows = list(values)
    if not rows:
        return {"n": 0}
    wins = [v for v in rows if v > 0]
    losses = [v for v in rows if v <= 0]
    total = sum(rows, Decimal("0"))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    return {
        "n": len(rows),
        "win_rate": round(len(wins) / len(rows), 3),
        "avg_win_r": round(float(sum(wins, Decimal("0")) / len(wins)), 3) if wins else 0.0,
        "avg_loss_r": round(float(sum(losses, Decimal("0")) / len(losses)), 3) if losses else 0.0,
        "expectancy_r": round(float(total / len(rows)), 3),
        "total_r": round(float(total), 2),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    since = datetime.now(UTC) - timedelta(days=args.days)
    conninfo = (
        f"host={os.environ.get('PGHOST', 'postgres')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'fatty_trader')} "
        f"user={os.environ.get('PGUSER', 'fatty_app')} "
        f"password={os.environ.get('PGPASSWORD', '')}"
    )
    with psycopg.connect(conninfo) as conn:
        conn.read_only = True
        signals = _load_signals(conn, since)
        realized = _load_realized(conn, since)

    if not signals:
        print("no signals in window")
        return 1

    rows: list[Row] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        instruments = await _contract_symbols(client)
        cache: dict[str, list[Candle]] = {}
        for signal in signals:
            symbol = (
                signal.symbol
                or instruments.get(signal.pair_token.upper())
                or f"{signal.pair_token}USDT"
            )
            key = f"{symbol}:{signal.created_at:%Y%m%d%H}"
            candles = cache.get(key)
            if candles is None:
                candles = await _candles(
                    client,
                    symbol,
                    signal.created_at - timedelta(minutes=10),
                    signal.created_at + HORIZON,
                )
                cache[key] = candles
            row = Row(signal=signal, policies={})
            if signal.signal_id in realized:
                row.realized_r, row.realized_reason = realized[signal.signal_id]
            elif not candles:
                row.realized_reason = "no-market-data"
            for policy in (
                "source_faithful",
                "half_tp1_trail",
                "hold_3r",
                "full_at_1r",
            ):
                row.policies[policy] = _simulate(signal, candles, policy)
            rows.append(row)

    report = {
        "window_days": args.days,
        "signals": len(rows),
        "coverage": {
            "with_intent_symbol": sum(1 for r in rows if r.signal.symbol),
            "with_candles": sum(
                1 for r in rows if r.policies["hold_3r"].exit_reason != "no-candles"
            ),
            "not_filled_reasons": {
                reason: sum(
                    1
                    for r in rows
                    for policy, outcome in r.policies.items()
                    if policy == "hold_3r" and not outcome.filled and outcome.exit_reason == reason
                )
                for reason in ("no-candles", "limit-not-filled", "invalid-risk", "fill-beyond-stop")
            },
        },
        "filled_by_policy": {
            policy: sum(1 for r in rows if r.policies[policy].filled) for policy in rows[0].policies
        },
        "policies": {
            policy: _stats(r.policies[policy].r_multiple for r in rows if r.policies[policy].filled)
            for policy in rows[0].policies
        },
        "realized": _stats(r.realized_r for r in rows if r.realized_r is not None),
        "worst": [
            {
                "signal": r.signal.pair_token,
                "at": r.signal.created_at.isoformat(),
                "realized_r": float(r.realized_r) if r.realized_r is not None else None,
            }
            for r in sorted(
                (r for r in rows if r.realized_r is not None),
                key=lambda r: r.realized_r,
            )[:5]
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"window={args.days}d signals={report['signals']} filled={report['filled_by_policy']}")
    print(
        f"{'policy':<20}{'n':>5}{'win%':>8}{'avgW':>8}{'avgL':>8}{'expR':>8}{'totalR':>9}{'PF':>7}"
    )
    for policy, stats in report["policies"].items():
        if not stats.get("n"):
            continue
        print(
            f"{policy:<20}{stats['n']:>5}{stats['win_rate'] * 100:>7.1f}%"
            f"{stats['avg_win_r']:>8.2f}{stats['avg_loss_r']:>8.2f}"
            f"{stats['expectancy_r']:>8.3f}{stats['total_r']:>9.1f}"
            f"{(stats['profit_factor'] or 0):>7.2f}"
        )
    stats = report["realized"]
    if stats.get("n"):
        print(
            f"{'realized (bot)':<20}{stats['n']:>5}{stats['win_rate'] * 100:>7.1f}%"
            f"{stats['avg_win_r']:>8.2f}{stats['avg_loss_r']:>8.2f}"
            f"{stats['expectancy_r']:>8.3f}{stats['total_r']:>9.1f}"
            f"{(stats['profit_factor'] or 0):>7.2f}"
        )
    print("worst:", ", ".join(f"{w['signal']} {w['realized_r']}" for w in report["worst"]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
