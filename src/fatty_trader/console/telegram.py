"""Private Telegram transport for the same durable console command authority.

Telegram never authenticates a console identity or creates consent. The configured
operator can reuse active password-session authority and exact browser consent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from html import escape
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from fatty_trader.operator.authorization import authorize_sender
from fatty_trader.operator.command_parser import (
    CancelCommand,
    CloseCommand,
    CommandError,
    OpenCommand,
    SetProtectionCommand,
    TradeCommand,
    parse_operator_command,
)


class TelegramCommandAdapter:
    def __init__(
        self,
        store: Any,
        commands: Any,
        *,
        operator_id: int,
        scope: dict[str, Any],
        read_service: Any = None,
        health_reader: Callable[[], str] | None = None,
        diagnostic_reader: Callable[[str], str] | None = None,
    ) -> None:
        self.store, self.commands, self.operator_id, self.scope = (
            store,
            commands,
            operator_id,
            scope,
        )
        self.read_service = read_service
        self.health_reader, self.diagnostic_reader = health_reader, diagnostic_reader

    def _principal(self, sender_id: int) -> Any:
        from fatty_trader.console.auth import OWNER_ID

        if sender_id != self.operator_id:
            raise CommandError("unauthorized operator command")
        row = self.store.one(
            """SELECT p.*,s.id AS session_id,s.csrf_token,s.authenticated_at,s.step_up_at
            FROM console_principals p JOIN console_sessions s ON s.principal_id=p.id
            WHERE p.id=%s AND p.enabled AND s.revoked_at IS NULL
              AND s.expires_at>now() AND s.absolute_expires_at>now()
              AND s.last_seen_at>now()-interval '1800 seconds'
              AND s.principal_revision=p.revision ORDER BY s.created_at DESC LIMIT 1""",
            (OWNER_ID,),
        )
        if not row:
            raise CommandError("Sign in to the console with the shared password first")
        return row

    def _root(self, symbol: str) -> str:
        owners = self.store.query(
            """SELECT i.client_order_id FROM live_order_intents i
            JOIN bitget_margin_reservations r ON r.client_order_id=i.client_order_id
              AND r.id=i.margin_reservation_id
            JOIN dispatches d ON d.id=i.dispatch_id AND d.id=r.dispatch_id
            WHERE i.exchange='bitget' AND i.symbol=%s
              AND i.role='ENTRY' AND i.entry_leg IS DISTINCT FROM 'limit' AND r.environment='LIVE'
              AND r.state='consumed' AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
                  WHERE (b.record_kind='intent' AND b.record_id=i.id)
                     OR (b.record_kind='reservation' AND b.record_id=r.id)
                     OR (b.record_kind='dispatch' AND b.record_id=d.id))""",
            (symbol,),
        )
        if len(owners) != 1:
            raise CommandError(
                "symbol must resolve to one exact owned ENTRY; "
                "external/baseline position prohibited"
            )
        return str(owners[0]["client_order_id"])

    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str:
        if not authorize_sender(
            sender_id=sender_id,
            expected_operator_id=self.operator_id,
            is_private=is_private,
            is_forwarded=is_forwarded,
        ):
            raise PermissionError("unauthorized operator command")
        try:
            return asyncio.run(self._handle(text, sender_id))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(
                getattr(exc, "message", "console command failed; inspect durable job evidence")
            ) from None

    async def _handle(self, text: str, sender_id: int) -> str:
        head, *parts = text.split()
        head = head.split("@", 1)[0].lower()
        if head in {"/confirm", "/approve", "/reject", "/job", "/jobcancel"}:
            principal = self._principal(sender_id)
            if head == "/confirm":
                if len(parts) < 3:
                    raise CommandError(
                        "/confirm <preview_id> <preview_digest> <full confirmation phrase>"
                    )
                preview = self.store.one(
                    "SELECT * FROM console_previews WHERE id=%s AND actor_id=%s",
                    (parts[0], principal["id"]),
                )
                if not preview:
                    raise CommandError("preview is absent or owned by another operator")
                request = {
                    "preview_id": parts[0],
                    "preview_digest": parts[1],
                    "confirmation_phrase": " ".join(parts[2:]),
                    "reason": "Private Telegram confirmation",
                    "policy_version": preview["policy_version"],
                    "resource_version": preview["resource_version"],
                    "channel": "telegram",
                }
                key = str(uuid5(NAMESPACE_URL, f"telegram:{sender_id}:{parts[0]}:{parts[1]}"))
                job = self.commands.enqueue(request, principal, key)
            elif head == "/job":
                if len(parts) != 1:
                    raise CommandError("/job <job_id>")
                job = self.commands.get_job(parts[0], principal)
            else:
                if len(parts) < 4:
                    raise CommandError(
                        f"{head} <job_id> <preview_digest> <resource_version> <reason>"
                    )
                request = {
                    "preview_digest": parts[1],
                    "resource_version": parts[2],
                    "reason": " ".join(parts[3:]),
                }
                method = {
                    "/approve": self.commands.approve,
                    "/reject": self.commands.reject,
                    "/jobcancel": self.commands.cancel,
                }[head]
                job = method(parts[0], request, principal)
            return escape(json.dumps(job, default=str), quote=False)
        if head in {"/help", "/start"}:
            return (
                "Read-only /health /positions /orders /balance /price SYMBOL. "
                "Mutations /open /trade /cancel /close "
                "/setsl /settp create the same durable preview as the console. "
                "/confirm PREVIEW DIGEST PHRASE "
                "enqueues only after exact preview-bound consent in the password console. "
                "/job ID tracks evidence. "
                "Account-wide/manual/baseline mutations are prohibited. "
                "No legacy confirmation tokens."
            )
        if head == "/health" and self.health_reader:
            return self.health_reader()
        command = parse_operator_command(text)
        if not isinstance(
            command, (OpenCommand, TradeCommand, CancelCommand, CloseCommand, SetProtectionCommand)
        ):
            if self.diagnostic_reader and head in {
                "/status",
                "/reconcile",
                "/protection",
                "/fills",
                "/intents",
                "/dispatches",
                "/signals",
            }:
                return self.diagnostic_reader(head[1:])
            section = {"/positions": "positions", "/orders": "orders", "/balance": "account"}.get(
                head
            )
            if section and self.read_service:
                return escape(json.dumps(self.read_service.read(section), default=str), quote=False)
            if head == "/price" and len(parts) == 1:
                snapshot = self.store.latest_snapshot(f"ticker:{parts[0].upper()}", self.scope)
                return escape(json.dumps(snapshot, default=str), quote=False)
            raise CommandError("read command is unavailable")
        principal = self._principal(sender_id)
        if getattr(command, "confirm_token", None):
            raise CommandError(
                "legacy tokens are obsolete; use /confirm "
                "with durable preview ID, digest and phrase"
            )
        if isinstance(command, (OpenCommand, TradeCommand)):
            if isinstance(command, TradeCommand) and command.exchanges != ("bitget",):
                raise CommandError("console trade is scoped to Bitget LIVE only")
            if command.stop_loss == "auto" or any(p == "auto" for p in command.take_profits):
                raise CommandError("manual entry needs explicit stop loss and first target")
            action = "trade.open"
            args = {
                "symbol": getattr(command, "symbol", getattr(command, "pair", None)),
                "direction": command.direction,
                "order_type": "LIMIT" if command.entry.startswith("limit:") else "MARKET",
                "stop_loss": str(command.stop_loss),
                "take_profit": str(command.take_profits[0]),
            }
            if args["order_type"] == "LIMIT":
                args["entry_price"] = command.entry.split(":", 1)[1]
            if command.margin != "auto":
                args["margin"] = str(command.margin)
            if command.leverage is not None:
                args["leverage"] = command.leverage
        elif isinstance(command, CancelCommand):
            if command.target == "all":
                raise CommandError("account-wide cancellation is prohibited until exact ownership")
            if command.target.startswith("order_id="):
                row = self.store.one(
                    """SELECT client_order_id FROM live_order_intents WHERE
                    provider_order_id=%s AND exchange='bitget'
                    AND role='ENTRY' AND order_type='limit'""",
                    (command.target.split("=", 1)[1],),
                )
                if not row:
                    raise CommandError("provider order is not an exact owned LIMIT ENTRY")
                action, args = "order.cancel_exact", {"client_oid": row["client_order_id"]}
            else:
                action, args = "entry.cancel_owned", {"symbol": command.target}
        elif isinstance(command, CloseCommand):
            if command.target == "all" or command.target.startswith("position_id="):
                raise CommandError(
                    "close requires one exact owned position; account-wide/external prohibited"
                )
            action, args = "position.close", {"client_oid": self._root(command.target)}
        else:
            action, args = (
                ("protection.set_sl" if command.kind == "SL" else "protection.set_tp"),
                {"client_oid": self._root(command.symbol), "price": str(command.price)},
            )
        preview = await self.commands.preview(
            {
                "action": action,
                "arguments": args,
                "target_refs": [],
                "reason": "Private Telegram operator command",
            },
            principal,
            self.scope,
        )
        return escape(json.dumps(preview, default=str), quote=False)
