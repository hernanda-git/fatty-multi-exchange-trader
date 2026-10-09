"""Synchronous Telegram Bot API poller for authenticated operator commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from html.parser import HTMLParser
from typing import Any, Protocol

import httpx

from fatty_trader.operator.command_parser import CommandError


class CommandService(Protocol):
    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str: ...


FetchUpdates = Callable[[int | None], Sequence[dict[str, Any]]]
SendReply = Callable[[int, str], None]
_TELEGRAM_TEXT_LIMIT = 4000  # UTF-16 code units; leave margin below Telegram's 4096 limit.


class _PlainReplyParser(HTMLParser):
    """Render trusted legacy HTML cards as plain text for Bot API replies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"pre", "p"}:
            self.parts.append("\n")


def _plain_reply(text: str) -> str:
    parser = _PlainReplyParser()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def _split_telegram_text(text: str) -> list[str]:
    """Split by Telegram's UTF-16 message limit without dropping characters."""
    chunks: list[str] = []
    current: list[str] = []
    units = 0
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        if units + width > _TELEGRAM_TEXT_LIMIT:
            chunks.append("".join(current))
            current = []
            units = 0
        current.append(character)
        units += width
    if current:
        chunks.append("".join(current))
    return chunks


class UpdateReceiptStore(Protocol):
    """Durable update claim boundary; a false claim is never re-executed."""

    def claim(self, update_id: int) -> bool: ...
    def next_offset(self) -> int | None: ...


class TelegramBotApi:
    """Small synchronous Bot API boundary with strict response validation."""

    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        if not token:
            raise ValueError("Telegram bot token is required")
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url="https://api.telegram.org", timeout=30.0)
        self._prefix = f"/bot{token}"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": 25, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise RuntimeError("Telegram getUpdates response is invalid")
        return result

    def send_reply(self, chat_id: int, text: str) -> None:
        for chunk in _split_telegram_text(_plain_reply(text)):
            self._post("sendMessage", {"chat_id": chat_id, "text": chunk})

    def set_my_commands(self) -> None:
        commands = [
            {"command": "start", "description": "show operator commands"},
            {"command": "help", "description": "show operator commands"},
            {"command": "health", "description": "full read-only health"},
            {"command": "status", "description": "concise provider status"},
            {"command": "positions", "description": "open positions"},
            {"command": "orders", "description": "pending orders"},
            {"command": "balance", "description": "available balance"},
            {"command": "price", "description": "ticker for a symbol"},
            {"command": "reconcile", "description": "provider vs DB drift"},
            {"command": "protection", "description": "SL/TP protection state"},
            {"command": "fills", "description": "recent fills"},
            {"command": "intents", "description": "recent durable intents"},
            {"command": "dispatches", "description": "dispatch lifecycle"},
            {"command": "signals", "description": "recent source signals"},
            {"command": "close", "description": "close a position"},
            {"command": "cancel", "description": "cancel pending orders"},
            {"command": "setsl", "description": "set native stop loss"},
            {"command": "settp", "description": "set native take profit"},
            {"command": "open", "description": "open a guarded position"},
            {"command": "trade", "description": "open a Bitget trade"},
        ]
        self._post("setMyCommands", {"commands": commands})

    def _post(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(f"{self._prefix}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            # HTTP exception strings include the Bot API URL and its secret token.
            raise RuntimeError("Telegram Bot API request failed") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("Telegram Bot API rejected request")
        return body.get("result")


class TelegramCommandPoller:
    """Route private commands with durable at-most-once update claims."""

    def __init__(
        self,
        *,
        command_service: CommandService,
        fetch_updates: FetchUpdates,
        send_reply: SendReply,
        receipt_store: UpdateReceiptStore | None = None,
    ) -> None:
        self._command_service = command_service
        self._fetch_updates = fetch_updates
        self._send_reply = send_reply
        self._receipt_store = receipt_store
        self.offset = receipt_store.next_offset() if receipt_store is not None else None

    def run_once(self) -> int:
        updates = self._fetch_updates(self.offset)
        processed = 0
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            claimed = self._receipt_store is None or self._receipt_store.claim(update_id)
            # A transient database failure must not acknowledge an unclaimed update.
            self.offset = update_id + 1
            processed += 1
            if not claimed:
                continue
            self._handle_update(update)
        return processed

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(text, str) or not text.startswith("/"):
            return
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        if not isinstance(chat_id, int) or not isinstance(sender_id, int):
            return
        if chat.get("type") != "private":
            return
        is_forwarded = "forward_origin" in message or "forward_from" in message
        try:
            response = self._command_service.handle(
                text,
                sender_id=sender_id,
                is_private=True,
                is_forwarded=is_forwarded,
            )
        except PermissionError:
            self._send_reply(chat_id, "Command rejected.")
        except CommandError as exc:
            self._send_reply(chat_id, f"Command error: {exc}")
        except Exception:
            self._send_reply(chat_id, "Command failed safely; no action confirmed.")
        else:
            self._send_reply(chat_id, response)
