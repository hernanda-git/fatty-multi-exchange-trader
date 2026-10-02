"""Evidence-led close lifecycle. No provider mutation methods exist here."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore

from fatty_trader.exchanges.bitget.live import normalize_fill
from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository


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

    def bind_requested_close(self, client: VerifiedCloseClient, close_oid: str) -> bool:
        """Capture the current canonical owner BEFORE POST, never from historical fills."""
        environment = getattr(client, "environment", None)
        if environment not in {"DEMO", "LIVE"}:
            return False
        c = self._connection_factory()
        try:
            cur = c.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('bitget'))")
            cur.execute(
                """SELECT r.id FROM bitget_margin_reservations r
                JOIN live_order_intents e ON e.exchange=r.exchange
                    AND e.client_order_id=r.client_order_id AND e.margin_reservation_id=r.id
                JOIN dispatches d ON d.id=r.dispatch_id AND d.source_type='canonical_signal'
                JOIN canonical_signals s ON s.id=d.source_id
                JOIN live_order_intents x ON x.exchange=r.exchange
                    AND x.client_order_id=%s AND x.symbol=r.symbol
                WHERE r.environment=%s AND r.state='consumed'
                    AND e.role='ENTRY' AND e.state IN ('filled','reconciled')
                    AND e.symbol=r.symbol AND e.filled_qty>0
                    AND x.role IN ('CLOSE','EMERGENCY_CLOSE','SL','TP')
                    AND x.state='requested' AND x.filled_qty=0
                    AND x.requested_qty=e.filled_qty
                    AND x.side=CASE e.side WHEN 'BUY' THEN 'SELL' ELSE 'BUY' END
                    AND x.created_at>=e.created_at AND x.created_at>=r.created_at
                    AND NOT EXISTS (SELECT 1 FROM fills f WHERE f.exchange=x.exchange
                        AND f.client_order_id=x.client_order_id)
                FOR UPDATE OF r,e,x""",
                (close_oid, environment),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
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
            c.commit()
            return bound
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    async def reconcile(self, client: VerifiedCloseClient) -> None:
        environment = getattr(client, "environment", None)
        if environment not in {"DEMO", "LIVE"}:
            return
        c = self._connection_factory()
        try:
            cur = c.cursor()
            cur.execute(
                """SELECT r.dispatch_id,r.client_order_id,r.symbol,b.close_client_order_id
                FROM bitget_verified_close_bindings b
                JOIN bitget_margin_reservations r ON r.id=b.reservation_id
                WHERE r.environment=%s AND r.state='consumed'""",
                (environment,),
            )
            candidates = cur.fetchall()
        finally:
            c.close()
        for dispatch_id, entry_oid, symbol, close_oid in candidates:
            record = self._store.get(close_oid)
            if record is None or record.state in {"cancelled", "rejected"}:
                continue
            raw = await client.get_fills(symbol)
            if isinstance(raw, dict) and set(raw) <= {"fillList", "endId"}:
                raw = raw.get("fillList")
            if not isinstance(raw, list) or not all(isinstance(f, dict) for f in raw):
                continue
            matches = [f for f in raw if f.get("clientOid") == close_oid]
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
                        or item["trade_side"] != "close"
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
            self._store.update(record)
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
            self._reservations.release_verified_close(
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
