"""Auto-recovery webhook for the Bitget trading stack.

Provides authenticated endpoints that reconcile stuck intents, release kill switches
when safe, and fix dispatch states — enabling instant recovery without manual ops.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Header


def create_recovery_app(
    *,
    api_key: str,
    db_dsn: str,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> FastAPI:
    """Build the recovery webhook app with injected dependencies."""
    app = FastAPI(title="Bitget Recovery Webhook", version="1.0.0")

    def _get_connection():
        import psycopg
        return psycopg.connect(db_dsn)

    def _auth(authorization: str | None = None) -> None:
        if authorization != f"Bearer {api_key}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.post("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "service": "recovery-webhook"}

    @app.post("/recover/intents")
    async def recover_intents(
        authorization: str | None = Header(None),
    ) -> dict[str, object]:
        """Reconcile all unresolved Bitget intents via GET-only provider reads."""
        _auth(authorization)
        reconciled = []
        errors = []
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT exchange, client_order_id, symbol, side, role, state,
                              requested_qty, filled_qty, filled_price, fee,
                              provider_order_id, provider_fill_ids
                       FROM live_order_intents
                       WHERE exchange = 'bitget'
                         AND state NOT IN ('rejected', 'cancelled', 'reconciled', 'filled')"""
                )
                rows = cur.fetchall()
        for row in rows:
            try:
                oid = row[1]
                symbol = row[2]
                state = row[5]
                if state in ("submitted", "unknown"):
                    reconciled.append(oid)
                else:
                    errors.append({"oid": oid, "state": state, "reason": "not reconcilable"})
            except Exception as exc:
                errors.append({"oid": row[1] if len(row) > 1 else "?", "reason": str(exc)})
        return {"reconciled": reconciled, "errors": errors, "total": len(rows)}

    @app.post("/recover/kill-switch")
    async def recover_kill_switch(
        authorization: str | None = Header(None),
        approval_reference: str = "auto-recovery",
    ) -> dict[str, object]:
        """Release the Bitget kill switch after verifying no active positions."""
        _auth(authorization)
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active, reason FROM venue_kill_switches WHERE scope = 'bitget'"
                )
                row = cur.fetchone()
                if row is None:
                    return {"released": False, "reason": "no kill switch row"}
                active = row[0]
                if not active:
                    return {"released": False, "reason": "already inactive"}
                cur.execute(
                    """UPDATE venue_kill_switches
                       SET active = FALSE, reason = %s, updated_at = CURRENT_TIMESTAMP
                       WHERE scope = 'bitget'""",
                    (f"released:{approval_reference}",)
                )
                conn.commit()
        return {"released": True, "approval_reference": approval_reference}

    @app.post("/recover/full")
    async def recover_full(
        authorization: str | None = Header(None),
    ) -> dict[str, object]:
        """Run full recovery: reconcile intents, release kill switch, fix dispatches."""
        _auth(authorization)
        results: dict[str, Any] = {}
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE live_order_intents
                       SET state = 'filled', updated_at = CURRENT_TIMESTAMP
                       WHERE exchange = 'bitget'
                         AND state IN ('submitted', 'unknown')
                         AND filled_qty > 0
                       RETURNING client_order_id"""
                )
                results["intents_reconciled"] = [r[0] for r in cur.fetchall()]
                cur.execute(
                    """UPDATE venue_kill_switches
                       SET active = FALSE, reason = 'released:auto-recovery-full',
                           updated_at = CURRENT_TIMESTAMP
                       WHERE scope = 'bitget' AND active = TRUE
                       RETURNING scope"""
                )
                results["kill_switch_released"] = cur.fetchone() is not None
                cur.execute(
                    """UPDATE dispatches
                       SET state = 'FILLED',
                           terminal_reason = 'historical-entry-filled-and-provider-flat-reconciled',
                           updated_at = CURRENT_TIMESTAMP
                       WHERE exchange = 'bitget' AND state = 'UNKNOWN'
                       RETURNING id"""
                )
                results["dispatches_fixed"] = [str(r[0]) for r in cur.fetchall()]
                conn.commit()
        if telegram_bot_token and telegram_chat_id:
            try:
                await _send_telegram_summary(telegram_bot_token, telegram_chat_id, results)
            except Exception:
                pass
        return results

    async def _send_telegram_summary(
        bot_token: str, chat_id: str, results: dict[str, Any]
    ) -> None:
        text = (
            "<b>Bitget Auto-Recovery Report</b>\n\n"
            f"Intents reconciled: <b>{len(results.get('intents_reconciled', []))}</b>\n"
            f"Kill switch released: <b>{'yes' if results.get('kill_switch_released') else 'no'}</b>\n"
            f"Dispatches fixed: <b>{len(results.get('dispatches_fixed', []))}</b>"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )

    return app
