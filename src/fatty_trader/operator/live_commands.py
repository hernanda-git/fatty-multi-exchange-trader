"""Provider read-only cards. Mutation authority lives only in console.commands."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import Any

from fatty_trader.operator.authorization import authorize_sender
from fatty_trader.operator.command_parser import (
    BalanceCommand,
    CommandError,
    DiagnosticCommand,
    HealthCommand,
    HelpCommand,
    OrdersCommand,
    PositionsCommand,
    PriceCommand,
    parse_operator_command,
)


class OperatorReadService:
    def __init__(
        self,
        gateway: Any,
        operator_id: int,
        *,
        health_reader: Callable[[], str] | None = None,
        diagnostic_reader: Callable[[str], str] | None = None,
    ) -> None:
        self.gateway, self.operator_id = gateway, operator_id
        self.health_reader, self.diagnostic_reader = health_reader, diagnostic_reader

    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str:
        if not authorize_sender(
            sender_id=sender_id,
            expected_operator_id=self.operator_id,
            is_private=is_private,
            is_forwarded=is_forwarded,
        ):
            raise PermissionError("unauthorized operator command")
        command = parse_operator_command(text)
        if isinstance(command, PriceCommand):
            return f"Harga {command.symbol}: {self.gateway.get_price(command.symbol)}"
        if isinstance(command, BalanceCommand):
            return f"Saldo tersedia: {self.gateway.get_balance()}"
        if isinstance(command, HealthCommand):
            if self.health_reader:
                return self.health_reader()
            from fatty_trader.operator.health import (
                create_shared_health_reader,
                load_operator_health_snapshot,
            )

            def unavailable_database() -> Any:
                raise RuntimeError("operator health database reader not configured")

            return create_shared_health_reader(
                lambda: load_operator_health_snapshot(
                    self.gateway,
                    unavailable_database,
                    mode="UNKNOWN",
                    venue_mode="UNKNOWN",
                    execution_enabled=None,
                )
            )()
        if isinstance(command, HelpCommand):
            return (
                "Read-only /health /status /positions /orders /balance /price. "
                "Mutations use durable console previews and exact password-session consent."
            )
        if isinstance(command, DiagnosticCommand) and self.diagnostic_reader:
            return self.diagnostic_reader(command.kind)
        if isinstance(command, PositionsCommand):
            rows = self.gateway.get_positions()
            if not rows:
                return "Tidak ada posisi terbuka"
            return escape(
                "OPEN POSITIONS · PROVIDER\n"
                + "\n".join(
                    f"{p.get('symbol')} {p.get('side')} "
                    f"size={p.get('size')} entry={p.get('entry')} "
                    f"Native SL={p.get('stop_loss') or 'MISSING'} "
                    f"Native TP={p.get('take_profit') or 'MISSING'}"
                    for p in rows
                ),
                quote=False,
            )
        if isinstance(command, OrdersCommand):
            rows = self.gateway.get_orders()
            if not rows:
                return "Tidak ada pending order"
            return escape(
                "Pending order\n"
                + "\n".join(
                    f"{p.get('symbol')} {p.get('side')} {p.get('order_id')} "
                    f"px={p.get('price')} qty={p.get('size')}"
                    for p in rows
                ),
                quote=False,
            )
        raise CommandError("not a read command; use the shared durable console command adapter")
