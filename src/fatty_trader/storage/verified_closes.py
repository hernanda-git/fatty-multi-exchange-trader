"""Evidence-led close lifecycle. No provider mutation methods exist here."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

from fatty_trader.exchanges.bitget.live import normalize_fill
from fatty_trader.execution.async_storage import _run_sync_storage
from fatty_trader.storage.balance_reservations import (
    CLOSING_TRADE_SIDES,
    PostgresBitgetMarginReservationRepository,
)


class VerifiedCloseClient(Protocol):
    @property
    def environment(self) -> str: ...

    async def get_fills(self, symbol: str | None = None) -> Any: ...

    async def get_all_positions(self) -> Any: ...


class PostgresVerifiedCloseLifecycle:
    def __init__(
        self, connection_factory: Callable[[], Any], store: "PostgresLiveIntentStore"
    ) -> None:
        self._connection_factory = connection_factory
        self._store = store
        self._reservations = PostgresBitgetMarginReservationRepository(connection_factory)

    def bind_requested_close(
        self,
        client: VerifiedCloseClient,
        close_oid: str,
        *,
        expected_remaining: Decimal | None = None,
        connection: Any = None,
    ) -> bool:
        """Capture the current canonical owner BEFORE POST, never from historical fills."""
        environment = getattr(client, "environment", None)
        if environment not in {"DEMO", "LIVE"}:
            return False
        owns_connection = connection is None
        c = self._connection_factory() if owns_connection else connection
        try:
            if owns_connection:
                cur = c.cursor()
            else:
                from psycopg.rows import tuple_row

                cur = c.cursor(row_factory=tuple_row)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            owner_join = (
                """JOIN dispatches d ON d.id=r.dispatch_id
                LEFT JOIN canonical_signals s ON s.id=d.source_id
                LEFT JOIN console_manual_entries m ON m.id=d.source_id
                AND d.source_type='console_manual' AND m.environment=r.environment"""
                if self._store.console_enabled
                else """JOIN dispatches d ON d.id=r.dispatch_id
                AND d.source_type='canonical_signal'
                JOIN canonical_signals s ON s.id=d.source_id"""
            )
            cur.execute(
                """SELECT r.id,r.client_order_id,x.requested_qty FROM bitget_margin_reservations r
                JOIN live_order_intents e ON e.exchange=r.exchange
                    AND e.client_order_id=r.client_order_id AND e.margin_reservation_id=r.id
                """
                + owner_join
                + """
                JOIN live_order_intents x ON x.exchange=r.exchange
                    AND x.client_order_id=%s AND x.symbol=r.symbol
                -- A protective close can race the fill, so the reservation may still be
                -- 'reserved' here; the entry intent below already proves filled_qty>0, so
                -- ownership evidence is complete without waiting for 'consumed'.
                WHERE r.environment=%s AND r.state IN ('consumed','reserved')
                    AND (s.id IS NOT NULL OR d.source_type='console_manual')
                    AND e.role='ENTRY' AND e.state IN ('filled','reconciled')
                    AND e.symbol=r.symbol AND e.filled_qty>0
                    AND x.role IN ('CLOSE','EMERGENCY_CLOSE','SL','TP')
                    AND x.state='requested' AND x.filled_qty=0
                    AND x.requested_qty>0
                    AND NOT EXISTS (
                        SELECT 1 FROM bitget_verified_close_bindings prior
                        JOIN live_order_intents pending ON pending.exchange=prior.exchange
                          AND pending.client_order_id=prior.close_client_order_id
                        WHERE prior.reservation_id=r.id
                          AND prior.close_client_order_id<>x.client_order_id
                          AND pending.state NOT IN ('filled','reconciled','cancelled','rejected')
                    )
                    AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records b
                        WHERE (b.record_kind='intent' AND b.record_id=e.id)
                           OR (b.record_kind='reservation' AND b.record_id=r.id)
                           OR (b.record_kind='dispatch' AND b.record_id=d.id))
                    AND NOT EXISTS (SELECT 1 FROM live_order_intents waiting
                        WHERE waiting.exchange=e.exchange AND waiting.margin_reservation_id=r.id
                          AND waiting.entry_leg='limit' AND NOT waiting.provider_terminal)
                    AND x.side=CASE e.side WHEN 'BUY' THEN 'SELL' ELSE 'BUY' END
                    AND x.created_at>=e.created_at AND x.created_at>=r.created_at
                    AND NOT EXISTS (SELECT 1 FROM fills f WHERE f.exchange=x.exchange
                        AND f.client_order_id=x.client_order_id)
                FOR UPDATE OF r,e,x""",
                (close_oid, environment),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
                if owns_connection:
                    c.commit()
                return False
            from fatty_trader.storage.live_intents import OwnedQuantityEvidenceError

            try:
                remaining = self._store.remaining_owned_quantity(rows[0][1], cursor=cur)
            except OwnedQuantityEvidenceError:
                if owns_connection:
                    c.commit()
                return False
            if rows[0][2] > remaining or (
                expected_remaining is not None and remaining != expected_remaining
            ):
                if owns_connection:
                    c.commit()
                return False
            cur.execute(
                """INSERT INTO bitget_verified_close_bindings
                (close_client_order_id,reservation_id) VALUES (%s,%s)
                ON CONFLICT (close_client_order_id) DO NOTHING""",
                (close_oid, rows[0][0]),
            )
            cur.execute(
                """SELECT reservation_id FROM bitget_verified_close_bindings
                WHERE close_client_order_id=%s""",
                (close_oid,),
            )
            bound = bool(cur.fetchone()[0] == rows[0][0])
            if owns_connection:
                c.commit()
            return bound
        except Exception:
            if owns_connection:
                c.rollback()
            raise
        finally:
            if owns_connection:
                c.close()

    async def reconcile(self, client: VerifiedCloseClient) -> None:
        environment = getattr(client, "environment", None)
        if environment not in {"DEMO", "LIVE"}:
            return
        candidates = await _run_sync_storage(self._read_close_candidates, environment)
        for dispatch_id, entry_oid, symbol, close_oid in candidates:
            record = await _run_sync_storage(self._store.get, close_oid)
            if record is None or record.state in {"cancelled", "rejected"}:
                continue
            raw = await client.get_fills(symbol)
            if isinstance(raw, dict) and set(raw) <= {"fillList", "endId"}:
                raw = raw.get("fillList")
            if not isinstance(raw, list) or not all(isinstance(f, dict) for f in raw):
                continue
            # A venue-executed TP/SL plan closes through the venue's own order, and
            # that fill carries no client order id at all: the executed order id is
            # the only link, and the close intent records it.  A fill that does name
            # a client order id must name ours, so a fill belonging to another
            # client order is never treated as our close.
            provider_order_id = str(record.provider_order_id or "")
            matches: list[dict[str, Any]] = []
            for fill in raw:
                client_oid = fill.get("clientOid")
                if client_oid is not None:
                    if client_oid == close_oid:
                        matches.append(fill)
                    continue
                if provider_order_id and str(fill.get("orderId", "")) == provider_order_id:
                    matches.append(fill)
            if not matches:
                continue
            evidence = []
            try:
                for raw_fill in matches:
                    # Validate economics BEFORE normalization, which otherwise defaults to zero.
                    detail = raw_fill.get("feeDetail")
                    if detail is not None:
                        if isinstance(detail, str):
                            detail = json.loads(detail)
                        if isinstance(detail, dict):
                            detail = [detail]
                        if not isinstance(detail, list) or not detail:
                            raise ValueError("missing fee detail")
                        for part in detail:
                            fee = Decimal(str(part["totalFee"]))
                            if not fee.is_finite():
                                raise ValueError("invalid fee detail")
                    else:
                        fee = Decimal(str(raw_fill["fee"]))
                        if not fee.is_finite():
                            raise ValueError("invalid fee")
                    f = normalize_fill(raw_fill)
                    item = dict(
                        provider_fill_id=f.get("fillId", f.get("tradeId")),
                        provider_order_id=f["orderId"],
                        symbol=f["symbol"],
                        side=f["side"].upper(),
                        trade_side=f["tradeSide"],
                        quantity=Decimal(
                            str(
                                f.get(
                                    "quantity", f.get("size", f.get("fillQty", f.get("baseVolume")))
                                )
                            )
                        ),
                        price=Decimal(str(f.get("price", f.get("fillPrice", f.get("priceAvg"))))),
                        fee=Decimal(str(f["fee"])),
                        realized_pnl=Decimal(
                            str(f.get("realizedPnl", f.get("profit", f.get("totalProfits"))))
                        ),
                    )
                    if (
                        item["symbol"] != symbol
                        or item["side"] != record.side
                        or item["trade_side"] not in CLOSING_TRADE_SIDES
                        or not item["provider_fill_id"]
                        or str(item["provider_fill_id"]).startswith("status-derived:")
                        or not item["provider_order_id"]
                        or any(
                            not item[k].is_finite()
                            for k in ("quantity", "price", "fee", "realized_pnl")
                        )
                        or item["quantity"] <= 0
                        or item["price"] <= 0
                        or item["fee"] < 0
                    ):
                        raise ValueError("unverified fill")
                    evidence.append(item)
                if (
                    len({f["provider_fill_id"] for f in evidence}) != len(evidence)
                    or len({f["provider_order_id"] for f in evidence}) != 1
                    or sum((f["quantity"] for f in evidence), Decimal(0)) != record.requested_qty
                    or (
                        record.provider_order_id
                        and record.provider_order_id != evidence[0]["provider_order_id"]
                    )
                ):
                    continue
            except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
                continue
            # Persist authenticated fill identity before asking for the fresh flat proof.
            record.state = "filled"
            record.provider_order_id = evidence[0]["provider_order_id"]
            record.filled_qty = record.requested_qty
            record.avg_price = (
                sum((f["price"] * f["quantity"] for f in evidence), Decimal(0)) / record.filled_qty
            )
            record.fee = sum((f["fee"] for f in evidence), Decimal(0))
            record.provider_fill_ids = tuple(f["provider_fill_id"] for f in evidence)
            record.provider_fills = tuple(matches)
            await _run_sync_storage(self._store.update, record)
            # Full authenticated position read includes BOTH hold sides; malformed is not flat.
            positions = await client.get_all_positions()
            if not isinstance(positions, list):
                continue
            try:
                for p in positions:
                    if (
                        not isinstance(p, dict)
                        or not isinstance(p["symbol"], str)
                        or not p["symbol"]
                    ):
                        raise ValueError("invalid positions")
                    qty = Decimal(str(p["total"]))
                    if not qty.is_finite() or qty < 0 or (p["symbol"] == symbol and qty != 0):
                        raise ValueError("not verified flat")
            except (KeyError, ValueError, TypeError, ArithmeticError):
                continue
            observed_at = datetime.now(UTC)
            await _run_sync_storage(
                self._reservations.release_verified_close,
                environment=environment,
                symbol=symbol,
                dispatch_id=dispatch_id,
                entry_client_order_id=entry_oid,
                close_client_order_id=close_oid,
                authenticated=True,
                observed_at=observed_at,
                flat_quantity=Decimal(0),
                fills=tuple(evidence),
            )

    def _read_close_candidates(self, environment: str) -> list[Any]:
        c = self._connection_factory()
        try:
            cur = c.cursor()
            cur.execute(
                """SELECT r.dispatch_id,r.client_order_id,r.symbol,b.close_client_order_id
                FROM bitget_verified_close_bindings b
                JOIN bitget_margin_reservations r ON r.id=b.reservation_id
                -- Bound closes whose fill raced the reservation transition must still
                -- reconcile; the binding itself proves ownership was established.
                WHERE r.environment=%s AND r.state IN ('consumed','reserved')""",
                (environment,),
            )
            return list(cur.fetchall())
        finally:
            c.close()
