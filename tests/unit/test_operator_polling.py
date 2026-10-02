from __future__ import annotations

import json
from typing import Any, cast

import httpx

from fatty_trader.operator.live_commands import OperatorCommandService
from fatty_trader.operator.telegram_polling import TelegramBotApi, TelegramCommandPoller


class InMemoryUpdateReceiptStore:
    def __init__(self) -> None:
        self.claimed: set[int] = set()

    def claim(self, update_id: int) -> bool:
        if update_id in self.claimed:
            return False
        self.claimed.add(update_id)
        return True

    def next_offset(self) -> int | None:
        return max(self.claimed) + 1 if self.claimed else None


class FakeCommandService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def handle(self, text: str, *, sender_id: int, is_private: bool, is_forwarded: bool) -> str:
        if sender_id != 1 or not is_private or is_forwarded:
            raise PermissionError("unauthorized")
        self.calls.append(
            {
                "text": text,
                "sender_id": sender_id,
                "is_private": is_private,
                "is_forwarded": is_forwarded,
            }
        )
        return f"handled {text}"


def _update(
    *,
    update_id: int = 1,
    text: str = "/balance",
    chat_id: int = 1,
    chat_type: str = "private",
    sender_id: int = 1,
    forwarded: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 10,
        "text": text,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": sender_id},
    }
    if forwarded:
        message["forward_origin"] = {"type": "user"}
    return {"update_id": update_id, "message": message}


def test_bot_api_long_polls_and_replies_without_parse_mode() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getUpdates"):
            assert json.loads(request.content.decode()) == {
                "allowed_updates": ["message"],
                "offset": 8,
                "timeout": 25,
            }
            return httpx.Response(200, json={"ok": True, "result": [_update(update_id=8)]})
        assert request.url.path.endswith("/sendMessage")
        assert json.loads(request.content.decode()) == {
            "chat_id": 1,
            "text": "BALANCE available=10",
        }
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    api = TelegramBotApi("123456:token", client=client)

    assert api.fetch_updates(8) == [_update(update_id=8)]
    api.send_reply(1, "BALANCE available=10")
    assert len(seen) == 2
    client.close()


def test_bot_api_splits_long_reply_without_dropping_tail() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    api = TelegramBotApi("123456:token", client=client)
    text = "x" * 4100

    api.send_reply(1, text)

    assert "".join(part["text"] for part in sent) == text
    assert len(sent) == 2
    assert all(len(part["text"]) <= 4096 for part in sent)
    client.close()


def test_bot_api_renders_operator_html_as_plain_text() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    api = TelegramBotApi("123456:token", client=client)

    api.send_reply(1, "<b>Health</b>\n<pre>safe &amp; sound</pre>")

    assert sent == [{"chat_id": 1, "text": "Health\nsafe & sound\n"}]
    client.close()


def test_poller_routes_authorized_private_slash_command_and_replies() -> None:
    service = FakeCommandService()
    sent: list[tuple[int, str]] = []
    poller = TelegramCommandPoller(
        command_service=service,
        fetch_updates=lambda offset: [_update()],
        send_reply=lambda chat_id, text: sent.append((chat_id, text)),
    )

    assert poller.run_once() == 1
    assert service.calls == [
        {"text": "/balance", "sender_id": 1, "is_private": True, "is_forwarded": False}
    ]
    assert sent == [(1, "handled /balance")]
    assert poller.offset == 2


def test_poller_replies_with_sanitized_rejection_for_unauthorized_sender() -> None:
    service = FakeCommandService()
    sent: list[tuple[int, str]] = []
    poller = TelegramCommandPoller(
        command_service=service,
        fetch_updates=lambda offset: [_update(sender_id=999)],
        send_reply=lambda chat_id, text: sent.append((chat_id, text)),
    )

    assert poller.run_once() == 1
    assert service.calls == []
    assert sent == [(1, "Command rejected.")]


def test_poller_does_not_route_non_command_or_group_message() -> None:
    service = FakeCommandService()
    sent: list[tuple[int, str]] = []
    poller = TelegramCommandPoller(
        command_service=service,
        fetch_updates=lambda offset: [
            _update(text="hello"),
            _update(update_id=2, chat_type="group"),
        ],
        send_reply=lambda chat_id, text: sent.append((chat_id, text)),
    )

    assert poller.run_once() == 2
    assert service.calls == []
    assert sent == []
    assert poller.offset == 3


def test_poller_advances_offset_after_malformed_update() -> None:
    service = FakeCommandService()
    poller = TelegramCommandPoller(
        command_service=service,
        fetch_updates=lambda offset: [{"update_id": 4, "message": "invalid"}],
        send_reply=lambda chat_id, text: None,
    )

    assert poller.run_once() == 1
    assert poller.offset == 5


def test_poller_does_not_reexecute_claimed_mutation_after_restart() -> None:
    store = InMemoryUpdateReceiptStore()
    first_service = FakeCommandService()
    first = TelegramCommandPoller(
        command_service=first_service,
        fetch_updates=lambda offset: [_update(update_id=42, text="/setsl WLDUSDT 0.47")],
        send_reply=lambda chat_id, text: None,
        receipt_store=store,
    )
    assert first.run_once() == 1
    assert len(first_service.calls) == 1

    restarted_service = FakeCommandService()
    restarted = TelegramCommandPoller(
        command_service=restarted_service,
        fetch_updates=lambda offset: [_update(update_id=42, text="/setsl WLDUSDT 0.47")],
        send_reply=lambda chat_id, text: None,
        receipt_store=store,
    )
    assert restarted.offset == 43
    assert restarted.run_once() == 1
    assert restarted_service.calls == []


def test_start_with_bot_mention_reaches_service_and_returns_readable_reply() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    api = TelegramBotApi("123456:token", client=client)
    service = OperatorCommandService(gateway=cast(Any, object()), operator_id=1)
    poller = TelegramCommandPoller(
        command_service=service,
        fetch_updates=lambda offset: [_update(text="/start@FattyTestBot")],
        send_reply=api.send_reply,
    )

    assert poller.run_once() == 1

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 1
    assert sent[0]["text"].startswith("FATTY OPERATOR COMMANDS\n")
    assert "/trade bitget LONG SYMBOL" in sent[0]["text"]
    assert "<b>" not in sent[0]["text"]
    client.close()


def test_bot_command_menu_exposes_start_alias() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org"
    )
    TelegramBotApi("123456:token", client=client).set_my_commands()

    assert requests[0]["commands"][0] == {
        "command": "start",
        "description": "show operator commands",
    }
    client.close()
