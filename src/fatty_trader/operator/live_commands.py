from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from fatty_trader.operator.authorization import authorize_sender
from fatty_trader.operator.command_parser import (
    BalanceCommand,
    CancelCommand,
    CloseCommand,
    CommandError,
    OpenCommand,
    OrdersCommand,
    PositionsCommand,
    PriceCommand,
    SetProtectionCommand,
    parse_operator_command,
)


class LiveGateway(Protocol):
    """Venue gateway surface the operator commands depend on."""

    def get_price(self, symbol: str) -> Decimal: ...
    def get_balance(self) -> Decimal: ...
    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def get_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...
    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        quantity: Decimal,
        leverage: int,
        entry: str,
        stop_loss: Decimal | str,
        take_profits: tuple[Decimal | str, ...],
    ) -> dict[str, Any]: ...
    def cancel_order(self, target: str) -> dict[str, Any]: ...
    def cancel_all(self) -> dict[str, Any]: ...
    def close_position(self, target: str) -> dict[str, Any]: ...
    def close_all(self) -> dict[str, Any]: ...
    def set_position_protection(
        self, symbol: str, *, stop_loss: Decimal | None, take_profit: Decimal | None
    ) -> dict[str, Any]: ...


@dataclass
class _PendingConfirmation:
    kind: str  # "cancel_all" or "close_all"
    target: str
    token: str
    expires_at: float


class OperatorCommandService:
    """Executes parsed operator commands against a LiveGateway."""

    def __init__(
        self,
        gateway: LiveGateway,
        operator_id: int,
        require_confirmation: bool = True,
        mutations_enabled: bool = False,
        now: float | None = None,
    ) -> None:
        self._gw = gateway
        self._operator_id = operator_id
        self._require_confirmation = require_confirmation
        self._mutations_enabled = mutations_enabled
        self._now = now
        self._confirm_token: str | None = None
        self._pending: _PendingConfirmation | None = None

    @property
    def _confirm_token_prop(self) -> str | None:
        return self._confirm_token

    def _current_time(self) -> float:
        if self._now is not None:
            return self._now
        import time

        return time.time()

    def _require_auth(self, *, sender_id: int, is_private: bool, is_forwarded: bool) -> None:
        if not authorize_sender(
            sender_id=sender_id,
            expected_operator_id=self._operator_id,
            is_private=is_private,
            is_forwarded=is_forwarded,
        ):
            raise PermissionError("unauthorized operator command")

    def _issue_confirmation(self, kind: str, target: str) -> str:
        token = uuid4().hex[:12]
        self._confirm_token = token
        self._pending = _PendingConfirmation(
            kind=kind, target=target, token=token, expires_at=self._current_time() + 300.0
        )
        return token

    def _consume_confirmation(self, token: str, expected_kind: str) -> _PendingConfirmation:
        if self._pending is None or self._pending.token != token:
            raise CommandError("invalid or expired confirmation token")
        if self._current_time() > self._pending.expires_at:
            self._pending = None
            raise CommandError("confirmation token expired")
        pending = self._pending
        if pending.kind != expected_kind:
            raise CommandError("confirmation token does not match requested action")
        self._pending = None
        return pending

    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str:
        self._require_auth(sender_id=sender_id, is_private=is_private, is_forwarded=is_forwarded)
        command = parse_operator_command(text)
        return self._dispatch(command)

    def _dispatch(self, command: object) -> str:
        if isinstance(command, PriceCommand):
            return self._on_price(command)
        if isinstance(command, BalanceCommand):
            return self._on_balance()
        if isinstance(command, PositionsCommand):
            return self._on_positions()
        if isinstance(command, OrdersCommand):
            return self._on_orders()
        if isinstance(command, OpenCommand):
            return self._on_open(command)
        if isinstance(command, CancelCommand):
            self._require_mutations_enabled()
            return self._on_cancel(command)
        if isinstance(command, CloseCommand):
            self._require_mutations_enabled()
            return self._on_close(command)
        if isinstance(command, SetProtectionCommand):
            self._require_mutations_enabled()
            return self._on_set_protection(command)
        raise CommandError("unsupported command")

    def _require_mutations_enabled(self) -> None:
        if not self._mutations_enabled:
            raise CommandError("operator mutations are disabled by the live cutover gate")

    def _on_price(self, command: PriceCommand) -> str:
        price = self._gw.get_price(command.symbol)
        return f"Harga {command.symbol}: {price}"

    def _on_balance(self) -> str:
        balance = self._gw.get_balance()
        return f"Saldo tersedia: {balance}"

    def _on_positions(self) -> str:
        positions = self._gw.get_positions()
        if not positions:
            return "Tidak ada posisi terbuka"
        rows = []
        for p in positions:
            symbol = p.get("symbol")
            side = p.get("side")
            size = p.get("size")
            entry = p.get("entry")
            stop_loss = p.get("stop_loss") or "none"
            take_profit = p.get("take_profit") or "none"
            rows.append(
                f"{symbol} {side} size={size} entry={entry} SL={stop_loss} TP={take_profit}"
            )
        return "Posisi terbuka\n" + "\n".join(rows)

    def _on_orders(self) -> str:
        orders = self._gw.get_orders()
        if not orders:
            return "Tidak ada pending order"
        rows = []
        for o in orders:
            symbol = o.get("symbol")
            side = o.get("side")
            order_id = o.get("order_id")
            price = o.get("price")
            size = o.get("size")
            rows.append(f"{symbol} {side} {order_id} px={price} qty={size}")
        return "Pending order\n" + "\n".join(rows)

    def _on_open(self, command: OpenCommand) -> str:
        margin: Decimal
        if command.margin == "auto":
            margin = self._gw.get_balance() * Decimal("0.20")
        else:
            margin = Decimal(str(command.margin))
        result = self._gw.open_position(
            symbol=command.symbol,
            direction=command.direction,
            quantity=margin,
            leverage=command.leverage,
            entry=command.entry,
            stop_loss=command.stop_loss,
            take_profits=command.take_profits,
        )
        if result.get("error"):
            return f"SKIP {result['symbol']} reason={result['error']}"
        return (
            f"OPEN {result['symbol']} {result['side']} qty={result['qty']} "
            f"lev={result['leverage']} entry={result['entry']} id={result['order_id']} "
            f"state={result['state']}"
        )

    def _on_cancel(self, command: CancelCommand) -> str:
        confirmation_kind = f"cancel:{command.target}"
        if self._require_confirmation and command.confirm_token is None:
            token = self._issue_confirmation(confirmation_kind, command.target)
            return f"CONFIRM cancel {command.target}? Re-send with confirm={token}"
        if command.confirm_token is not None:
            self._consume_confirmation(command.confirm_token, confirmation_kind)
        if command.target == "all":
            result = self._gw.cancel_all()
            return f"CANCEL all count={result['count']}"
        result = self._gw.cancel_order(command.target)
        return f"CANCEL {result['cancelled']}"

    def _on_close(self, command: CloseCommand) -> str:
        confirmation_kind = f"close:{command.target}"
        if self._require_confirmation and command.confirm_token is None:
            token = self._issue_confirmation(confirmation_kind, command.target)
            return f"CONFIRM close {command.target}? Re-send with confirm={token}"
        if command.confirm_token is not None:
            self._consume_confirmation(command.confirm_token, confirmation_kind)
        if command.target == "all":
            result = self._gw.close_all()
            return f"CLOSE all count={result['count']}"
        result = self._gw.close_position(command.target)
        if result.get("state") == "reconciliation-pending":
            return f"CLOSE {result['closed']} state=reconciliation-pending"
        return f"CLOSE {result['closed']}"

    def _on_set_protection(self, command: SetProtectionCommand) -> str:
        confirmation_kind = f"set{command.kind.lower()}:{command.symbol}:{command.price}"
        if self._require_confirmation and command.confirm_token is None:
            token = self._issue_confirmation(confirmation_kind, command.symbol)
            return (
                f"Konfirmasi set {command.kind} {command.symbol} {command.price}. "
                f"Kirim ulang dengan confirm={token}"
            )
        if command.confirm_token is not None:
            self._consume_confirmation(command.confirm_token, confirmation_kind)
        result = self._gw.set_position_protection(
            command.symbol,
            stop_loss=command.price if command.kind == "SL" else None,
            take_profit=command.price if command.kind == "TP" else None,
        )
        state = result.get("state", "reconciliation-pending")
        if state != "reconciled":
            return f"{command.kind} {command.symbol} {command.price} status={state}"
        return f"{command.kind} {command.symbol} {command.price} terverifikasi"

    def _is_confirmed(self, target: str) -> bool:
        if self._pending is None or self._pending.target != target:
            return False
        if self._current_time() > self._pending.expires_at:
            self._pending = None
            return False
        return True
