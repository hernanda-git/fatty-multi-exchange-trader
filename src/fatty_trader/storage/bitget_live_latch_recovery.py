"""Approved flat LIVE baseline recovery; never trades or clears general latches.

All account mutation consumers must remain stopped through proof and apply.
Table locks prevent local ledger races, not external account mutations.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from fatty_trader.exchanges.bitget.websocket_v2 import (
    V2_PRIVATE_WS_URL,
    V2_PUBLIC_WS_URL,
)
from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
from fatty_trader.execution.bitget_runtime_readiness import ledger_readiness_issues
from fatty_trader.storage.bitget_baseline import (
    BaselineRefused,
    _fresh,
    collect_flat_evidence,
)

ALLOWED_REASONS = {
    "bitget": "clock-skew-exceeded",
    "bitget-protection-stream": "socket-not-connected",
}
STREAM_PROOF_TIMEOUT = 15.0
PONG_MAX_AGE = 5.0


def require_production_settings(environ: Mapping[str, str]) -> None:
    expected = {
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_PROTECTION_STREAM_ENABLED": "1",
    }
    if any(environ.get(key) != value for key, value in expected.items()):
        raise BaselineRefused("recovery requires production LIVE/LIVE/1 and enabled stream")
    if (
        environ.get("BITGET_PROTECTION_STREAM_MODE", "observe") != "observe"
        or environ.get("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "0") != "0"
    ):
        raise BaselineRefused("recovery requires an observe-only protection stream")


def validate_latches(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise BaselineRefused("no approved old latch is active")
    for row in rows:
        if (
            row.get("active") is not True
            or row.get("scope") not in ALLOWED_REASONS
            or row.get("reason") != ALLOWED_REASONS[row["scope"]]
            or not row.get("latched_at")
        ):
            raise BaselineRefused("unknown/global latch or unsafe reason; refusing release")


def require_healthy_stream(socket: Any, driver: asyncio.Task[Any] | None = None) -> dict[str, Any]:
    if driver is not None and driver.done():
        raise BaselineRefused("stream observation stopped or failed")
    if socket.public_endpoint != V2_PUBLIC_WS_URL or socket.private_endpoint != V2_PRIVATE_WS_URL:
        raise BaselineRefused("stream is not the production V2 public/private pair")
    now = time.monotonic()
    pongs = (socket.last_public_pong_at, socket.last_private_pong_at)
    if (
        not socket.private_authenticated
        or any(pong is None or not 0 <= now - pong <= PONG_MAX_AGE for pong in pongs)
        or not socket.check_freshness()
        or str(socket.state) != "CONNECTED"
        or socket.last_mark_event_age("BTCUSDT") is None
    ):
        raise BaselineRefused(
            "stream lacks authenticated CONNECTED, fresh mark and both-leg pong proof"
        )
    return {
        "state": "CONNECTED",
        "private_authenticated": True,
        "public_pong_age_seconds": now - pongs[0],
        "private_pong_age_seconds": now - pongs[1],
        "btc_mark_age_seconds": socket.last_mark_event_age("BTCUSDT"),
        "public_endpoint": socket.public_endpoint,
        "private_endpoint": socket.private_endpoint,
    }


async def _observe(socket: Any) -> None:
    while True:
        await socket.send_heartbeat_if_due()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(socket.receive_once(), timeout=0.25)


class _LockedReadinessConnection:
    """Reuse admission SQL without allowing its helpers to commit our transaction."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def cursor(self) -> Any:
        return self.connection.cursor()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class PostgresBitgetLiveLatchRecovery:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _latches(cursor: Any) -> list[dict[str, Any]]:
        cursor.execute("SELECT to_jsonb(k) FROM venue_kill_switches k WHERE active ORDER BY scope")
        return [
            row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in cursor.fetchall()
        ]

    @staticmethod
    def _baseline(cursor: Any, baseline_id: UUID, account_id: str) -> dict[str, Any]:
        cursor.execute(
            "SELECT to_jsonb(b) FROM bitget_audited_baselines b WHERE id=%s", (baseline_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise BaselineRefused("approved baseline receipt is missing")
        receipt = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        evidence = receipt.get("provider_evidence", {})
        if (
            receipt.get("exchange") != "bitget"
            or receipt.get("environment") != "LIVE"
            or receipt.get("account_id") != account_id
            or not receipt.get("approval_reference", "").strip()
            or receipt.get("uncertainty_policy") != "unresolved-history-not-verified-closure"
            or evidence.get("account_id") != account_id
            or evidence.get("environment") != "LIVE"
        ):
            raise BaselineRefused("baseline receipt account/environment binding is invalid")
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM bitget_audited_baselines "
            "WHERE account_id IS DISTINCT FROM %s OR environment IS DISTINCT FROM 'LIVE')",
            (account_id,),
        )
        if cursor.fetchone()[0]:
            raise BaselineRefused("database contains a differently bound baseline")
        return receipt

    @staticmethod
    def _ready(connection: Any, cursor: Any) -> None:
        adapter = _LockedReadinessConnection(connection)
        issues = ledger_readiness_issues(PostgresBitgetDispatchRepository(lambda: adapter), "LIVE")
        if issues:
            raise BaselineRefused("ledger is not ready: " + ",".join(issues))
        # Readiness admits known recoverable ownership. This flat-only release must
        # additionally refuse EVERY future/unbaselined unresolved commitment.
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM live_order_intents i WHERE exchange='bitget' "
            "AND (filled_qty>0 OR state NOT IN ('rejected','cancelled','reconciled')) "
            "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
            "WHERE b.record_kind='intent' AND b.record_id=i.id)), "
            "EXISTS(SELECT 1 FROM bitget_margin_reservations m WHERE exchange='bitget' "
            "AND state IN ('reserved','unknown','consumed') "
            "AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b "
            "WHERE b.record_kind='reservation' AND b.record_id=m.id)), "
            "EXISTS(SELECT 1 FROM positions WHERE exchange='bitget' AND closed_at IS NULL), "
            "EXISTS(SELECT 1 FROM dispatches WHERE exchange='bitget' "
            "AND claimed_by IS NOT NULL AND lease_until>clock_timestamp())"
        )
        if any(cursor.fetchone()):
            raise BaselineRefused(
                "future/unbaselined unresolved commitment, open position or worker lease"
            )

    async def run(
        self,
        client: Any,
        socket: Any,
        *,
        environ: Mapping[str, str],
        confirmed: bool,
        account_id: str,
        baseline_id: UUID,
        approval_reference: str,
        apply: bool = False,
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        if not confirmed or not approval_reference.strip() or not account_id.strip():
            raise BaselineRefused(
                "explicit --confirm, approval reference and account ID are required"
            )
        require_production_settings(environ)
        if apply and (expected_digest is None or len(expected_digest) != 64):
            raise BaselineRefused("apply requires the reviewed dry-run recovery_digest")
        approval_reference = approval_reference.strip()
        evidence = await collect_flat_evidence(client, account_id, "LIVE")
        connection = self._connection_factory()
        driver = None
        try:
            cursor = connection.cursor()
            observed = self._latches(cursor)
            validate_latches(observed)
            self._baseline(cursor, baseline_id, account_id)
            self._ready(connection, cursor)
            # End the initial read transaction before bounded network diagnostics.
            connection.rollback()
            await asyncio.wait_for(socket.connect(), timeout=STREAM_PROOF_TIMEOUT)
            driver = asyncio.create_task(_observe(socket))
            deadline = time.monotonic() + STREAM_PROOF_TIMEOUT
            while True:
                try:
                    require_healthy_stream(socket, driver)
                    break
                except BaselineRefused as exc:
                    if driver.done() or time.monotonic() >= deadline:
                        raise BaselineRefused(
                            "bounded V2 stream authentication/liveness proof failed"
                        ) from exc
                    await asyncio.sleep(0.1)
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute(
                "LOCK TABLE venue_kill_switches, live_order_intents, bitget_margin_reservations, "
                "dispatches, positions, orders, bitget_audited_baselines, bitget_baseline_records "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
            locked = self._latches(cursor)
            validate_latches(locked)
            if locked != observed:
                raise BaselineRefused("latch rows changed during evidence collection")
            receipt = self._baseline(cursor, baseline_id, account_id)
            self._ready(connection, cursor)
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "latches": locked,
                        "baseline_id": str(baseline_id),
                        "account_id": account_id,
                        "approval_reference": approval_reference,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if apply and digest != expected_digest:
                raise BaselineRefused("latches/binding differ from reviewed dry-run")
            # Fresh authenticated GET-only inventory under the write locks, even
            # for dry-run. The stream readers remain alive throughout these GETs.
            evidence = await collect_flat_evidence(client, account_id, "LIVE")
            _fresh(evidence)
            stream = require_healthy_stream(socket, driver)
            result = {
                "applied": False,
                "recovery_digest": digest,
                "baseline_id": str(baseline_id),
                "account_id": account_id,
                "approval_reference": approval_reference,
                "latches": locked,
                "stream": stream,
                "ledger_readiness_issues": [],
                "provider_evidence": evidence,
            }
            if not apply:
                connection.rollback()
                return result
            require_production_settings(environ)
            recovery_id = uuid4()
            for latch in locked:
                cursor.execute(
                    "UPDATE venue_kill_switches SET active=FALSE, reason=%s, "
                    "updated_at=clock_timestamp() WHERE scope=%s AND active AND reason=%s",
                    (f"released:{approval_reference}", latch["scope"], latch["reason"]),
                )
                if cursor.rowcount != 1:
                    raise BaselineRefused("exact latch update failed")
                payload = {
                    "kind": "kill-switch-release",
                    "scope": latch["scope"],
                    "approval_reference": approval_reference,
                    "recovery_id": str(recovery_id),
                    "baseline_id": str(baseline_id),
                    "baseline_approval_reference": receipt["approval_reference"],
                    "account_id": account_id,
                    "environment": "LIVE",
                    "prior_latch": latch,
                    "recovery_digest": digest,
                    "provider_evidence": evidence,
                    "stream": stream,
                    "ledger_readiness_issues": [],
                }
                cursor.execute(
                    "INSERT INTO notifications_outbox (id,dedup_key,payload) "
                    "VALUES (%s,%s,%s::jsonb)",
                    (
                        uuid4(),
                        f"kill-switch-release:{latch['scope']}:{recovery_id}",
                        json.dumps(payload),
                    ),
                )
            _fresh(evidence)
            require_healthy_stream(socket, driver)
            connection.commit()
            result.update(applied=True, recovery_id=str(recovery_id))
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            if driver is not None:
                driver.cancel()
                await asyncio.gather(driver, return_exceptions=True)
            await socket.close()
            connection.close()
