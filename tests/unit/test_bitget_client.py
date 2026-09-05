"""Unit tests for the authenticated Bitget V2 REST transport (TDD)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from fatty_trader.exchanges.bitget.client import (
    BitgetApiError,
    BitgetRestClient,
    BitgetUnknownResultError,
    build_signature,
    canonical_query_string,
    compact_body,
)


def expected_signature(
    secret: str, timestamp: str, method: str, path: str, query: str, body: str
) -> str:
    prehash = f"{timestamp}{method.upper()}{path}"
    if query:
        prehash += f"?{query}"
    prehash += body
    digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_signature_vector_with_known_secret() -> None:
    sig = build_signature(
        "test-secret", "1693830000000", "GET", "/api/v2/mix/account/account", "", ""
    )
    assert sig == expected_signature(
        "test-secret", "1693830000000", "GET", "/api/v2/mix/account/account", "", ""
    )
    assert isinstance(sig, str)
    assert len(sig) == 44  # base64-encoded 32-byte HMAC-SHA256


def test_signature_vector_post_with_body() -> None:
    body = compact_body({"leverage": "10", "symbol": "BTCUSDT"})
    sig = build_signature(
        "s3cr3t", "1693830000000", "post", "/api/v2/mix/account/set-leverage", "", body
    )
    assert sig == expected_signature(
        "s3cr3t", "1693830000000", "POST", "/api/v2/mix/account/set-leverage", "", body
    )


def test_query_canonicalization_sorts_keys() -> None:
    assert canonical_query_string({"z": "1", "a": "2", "m": "3"}) == "a=2&m=3&z=1"
    assert canonical_query_string({}) == ""
    assert canonical_query_string(None) == ""


def test_body_serialization_compact_sorted() -> None:
    assert compact_body(None) == ""
    assert compact_body({}) == "{}"
    assert compact_body({"b": 1, "a": 2}) == json.dumps(
        {"a": 2, "b": 1}, separators=(",", ":"), sort_keys=True
    )


def test_timeout_defaults_to_ten_seconds() -> None:
    client = BitgetRestClient(api_key="k", api_secret="s", passphrase="p")
    assert client.timeout == 10.0


def ok_envelope(data: object = None) -> dict[str, object]:
    return {"code": "00000", "msg": "success", "requestTime": 1, "data": data or {}}


def make_client(handler: object, **kwargs: object) -> tuple[BitgetRestClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert isinstance(handler, object)
        fn = handler  # type: ignore[operator]
        return fn(request)  # type: ignore[operator]

    transport = httpx.MockTransport(wrapped)  # type: ignore[arg-type]
    client = BitgetRestClient(
        api_key="my-key",
        api_secret="my-secret",
        passphrase="my-pass",
        transport=transport,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )
    return client, seen


async def test_public_ticker_hits_expected_path_with_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/ticker"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json=ok_envelope({"symbol": "BTCUSDT"}))

    client, _ = make_client(handler)
    data = await client.get_ticker("BTCUSDT")
    assert data["symbol"] == "BTCUSDT"
    await client.aclose()


async def test_auth_headers_include_passphrase_and_signature() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ACCESS-KEY"] == "my-key"
        assert request.headers["ACCESS-PASSPHRASE"] == "my-pass"
        assert request.headers["ACCESS-SIGN"]
        assert request.headers["ACCESS-TIMESTAMP"]
        return httpx.Response(200, json=ok_envelope({}))

    client, _ = make_client(handler)
    await client.get_account(symbol="BTCUSDT")
    await client.aclose()


async def test_get_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"code": "500", "msg": "boom", "data": None})
        return httpx.Response(200, json=ok_envelope({"ok": True}))

    client, _ = make_client(handler, max_get_retries=2)
    data = await client.get_ticker("BTCUSDT")
    assert data == {"ok": True}
    assert calls["n"] == 3
    await client.aclose()


async def test_post_never_retried_on_transport_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("down", request=request)

    client, _ = make_client(handler)
    with pytest.raises(BitgetUnknownResultError):
        await client.set_leverage("BTCUSDT", product_type="USDT-FUTURES", leverage="10")
    assert calls["n"] == 1
    await client.aclose()


async def test_error_code_raises_without_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "40001", "msg": "bad margin coin", "requestTime": 1, "data": None}
        )

    client, _ = make_client(handler)
    with pytest.raises(BitgetApiError) as exc_info:
        await client.get_ticker("BTCUSDT")
    assert exc_info.value.code == "40001"
    assert "bad margin coin" in str(exc_info.value)
    blob = f"{exc_info.value!s}{exc_info.value!r}"
    for secret in ("my-key", "my-secret", "my-pass"):
        assert secret not in blob
    await client.aclose()


async def test_transport_error_message_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    client, _ = make_client(handler)
    with pytest.raises(BitgetApiError) as exc_info:
        await client.get_ticker("BTCUSDT")
    blob = str(exc_info.value)
    for secret in ("my-key", "my-secret", "my-pass"):
        assert secret not in blob
    assert "ACCESS-SIGN" not in blob
    await client.aclose()


async def test_http_error_preserves_provider_code_and_message_without_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "40012", "msg": "apikey/password is incorrect"})

    client, _ = make_client(handler)
    with pytest.raises(BitgetApiError) as exc_info:
        await client.get_account(symbol="BTCUSDT")
    assert exc_info.value.code == "40012"
    assert "apikey/password is incorrect" in str(exc_info.value)
    for secret in ("my-key", "my-secret", "my-pass"):
        assert secret not in str(exc_info.value)
    await client.aclose()


async def test_explicit_params_and_all_methods() -> None:
    seen: list[httpx.Request] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        paths.append(request.url.path)
        return httpx.Response(200, json=ok_envelope({"ok": True}))

    client, _ = make_client(handler)
    await client.get_contracts()
    await client.get_account_bills()
    await client.get_account(symbol="BTCUSDT")
    await client.set_leverage("BTCUSDT", leverage="10")
    await client.set_margin_mode("BTCUSDT", margin_mode="crossed")
    await client.get_pending_orders()
    await client.get_order_detail("BTCUSDT", order_id="123")
    await client.get_fills()
    await client.get_single_position("BTCUSDT")
    await client.get_all_positions()
    await client.aclose()

    assert paths == [
        "/api/v2/mix/market/contracts",
        "/api/v2/mix/account/bills",
        "/api/v2/mix/account/account",
        "/api/v2/mix/account/set-leverage",
        "/api/v2/mix/account/set-margin-mode",
        "/api/v2/mix/order/orders-pending",
        "/api/v2/mix/order/detail",
        "/api/v2/mix/order/fills",
        "/api/v2/mix/position/single-position",
        "/api/v2/mix/position/all-position",
    ]
    # set-leverage body carries explicit symbol param
    leverage_req = seen[3]
    assert json.loads(leverage_req.content.decode())["symbol"] == "BTCUSDT"
