"""Synchronous Telegram Bot API poller for authenticated operator commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx

from fatty_trader.operator.command_parser import CommandError


class CommandService(Protocol):
    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str: ...


FetchUpdates = Callable[[int | None], Sequence[dict[str, Any]]]
SendReply = Callable[[int, str], None]


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
        self._post("sendMessage", {"chat_id": chat_id, "text": text[:4000]})

    def _post(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(f"{self._prefix}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Telegram Bot API request failed") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("Telegram Bot API rejected request")
        return body.get("result")


class TelegramCommandPoller:
    """Advance Bot API updates exactly once and route only private slash commands."""

    def __init__(
        self,
        *,
        command_service: CommandService,
        fetch_updates: FetchUpdates,
        send_reply: SendReply,
    ) -> None:
        self._command_service = command_service
        self._fetch_updates = fetch_updates
        self._send_reply = send_reply
        self.offset: int | None = None

    def run_once(self) -> int:
        updates = self._fetch_updates(self.offset)
        processed = 0
        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            self.offset = update_id + 1
            processed += 1
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
