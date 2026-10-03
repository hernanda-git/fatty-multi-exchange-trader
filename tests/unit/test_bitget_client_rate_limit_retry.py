"""A transient rate limit must not surface as a kill-switch latch.

Production evidence: Bitget documents 10 req/s/UID for /api/v2/mix/order/fills and
/api/v2/mix/position/all-position, and the monitor plus its protection watchdog call
them concurrently from one process. ``_request()`` retried neither HTTP 429 nor
Bitget's rate-limit business codes, and slept nothing between attempts, so a blip
became a venue kill-switch latch.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from fatty_trader.exchanges.bitget.client import (
    DEFAULT_GET_BACKOFF_BASE,
    DEFAULT_GET_BACKOFF_CAP,
    DEFAULT_MAX_GET_RETRIES,
    BitgetApiError,
    BitgetRestClient,
    BitgetUnknownResultError,
)


def ok_envelope(data: object = None) -> dict[str, object]:
    """Successful envelope. An explicit empty fill page carries endId="",
    which is the only provider shape that proves pagination exhaustion."""
    return {"code": "00000", "msg": "success", "requestTime": 1, "data": data or {}}


def empty_fill_page() -> dict[str, object]:
    return {"fillList": [], "endId": ""}


def make_client(
    handler: Any,
    *,
    sleeps: list[float] | None = None,
    **kwargs: Any,
) -> tuple[BitgetRestClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = BitgetRestClient(
        api_key="my-key",
        api_secret="my-secret",
        passphrase="my-pass",
        transport=httpx.MockTransport(wrapped),
        sleep=_RecordingSleep(sleeps if sleeps is not None else []),
        **kwargs,
    )
    return client, seen


class _RecordingSleep:
    """Deterministic, injectable sleep: records the delay and returns immediately."""

    def __init__(self, record: list[float]) -> None:
        self.record = record

    async def __call__(self, delay: float) -> None:
        self.record.append(delay)


async def test_http_429_is_retried_and_then_succeeds() -> None:
    responses = [
        httpx.Response(429, json={"code": "30006", "msg": "too many requests"}),
        httpx.Response(200, json=ok_envelope(empty_fill_page())),
    ]
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client, seen = make_client(handler, sleeps=sleeps, max_get_retries=3)

    # get_fills walks bounded pagination; an empty page with endId="" is the
    # only shape that proves exhaustion.
    assert await client.get_fills() == {
        "fillList": [],
        "endId": "",
        "pages": [{"fillList": [], "endId": ""}],
    }
    assert len(seen) == 2
    assert len(sleeps) == 1


async def test_bitget_rate_limit_business_code_is_retried_and_then_succeeds() -> None:
    """HTTP 200 with code 30006 is Bitget's rate limit for the mix endpoints."""
    responses = [
        httpx.Response(200, json={"code": "30006", "msg": "request too many"}),
        httpx.Response(200, json=ok_envelope(empty_fill_page())),
    ]
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client, seen = make_client(handler, sleeps=sleeps, max_get_retries=3)

    assert (await client.get_fills())["fillList"] == []
    assert len(seen) == 2


async def test_rate_limit_that_never_clears_raises_with_the_provider_code() -> None:
    """Fail closed: exhausted retries still raise, they never return fabricated data."""
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "30006", "msg": "too many requests"})

    client, seen = make_client(handler, sleeps=sleeps, max_get_retries=2)

    with pytest.raises(BitgetApiError) as excinfo:
        await client.get_all_positions()

    assert excinfo.value.code == "30006"
    # 1 initial attempt + exactly max_get_retries retries: never more.
    assert len(seen) == 3
    assert len(sleeps) == 2


async def test_permanent_business_code_is_not_retried_and_still_raises() -> None:
    """40309 (delisted symbol) and 40008 (timestamp expired) are permanent."""
    for code, message in (
        ("40309", "symbol delisted"),
        ("40008", "timestamp expired"),
        ("40034", "invalid symbol"),
    ):

        def handler(
            _: httpx.Request, provider_code: str = code, provider_msg: str = message
        ) -> httpx.Response:
            return httpx.Response(200, json={"code": provider_code, "msg": provider_msg})

        client, seen = make_client(handler, sleeps=[], max_get_retries=3)

        with pytest.raises(BitgetApiError) as excinfo:
            await client.get_fills()

        assert excinfo.value.code == code
        assert len(seen) == 1, f"{code} must never be retried"


async def test_permanent_http_403_is_not_retried() -> None:
    seen: list[httpx.Request] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "30015", "msg": "permission denied"})

    client, seen = make_client(handler, sleeps=[], max_get_retries=3)

    with pytest.raises(BitgetApiError):
        await client.get_fills()

    assert len(seen) == 1


async def test_backoff_delays_are_monotonic_and_bounded() -> None:
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "30006", "msg": "too many requests"})

    client, _ = make_client(handler, sleeps=sleeps, max_get_retries=6)

    with pytest.raises(BitgetApiError):
        await client.get_fills()

    assert len(sleeps) == 6
    assert sleeps == sorted(sleeps), "backoff must be non-decreasing"
    assert sleeps[0] == DEFAULT_GET_BACKOFF_BASE
    assert all(0 < delay <= DEFAULT_GET_BACKOFF_CAP for delay in sleeps), sleeps
    assert sleeps[-1] == DEFAULT_GET_BACKOFF_CAP, "backoff must saturate at the cap"
    # Deterministic: the same failure produces the same schedule every time.
    replay: list[float] = []
    other, _ = make_client(handler, sleeps=replay, max_get_retries=6)
    with pytest.raises(BitgetApiError):
        await other.get_fills()
    assert replay == sleeps


async def test_post_is_never_retried_on_a_429() -> None:
    """Retrying an order POST after a rate limit could double-fill a position."""
    seen: list[httpx.Request] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "30006", "msg": "too many requests"})

    client, seen = make_client(handler, sleeps=[], max_get_retries=5)

    with pytest.raises((BitgetUnknownResultError, BitgetApiError)):
        await client.place_market_close(
            symbol="BTCUSDT",
            side="SELL",
            quantity="0.001",
            client_oid="live-bitget-BTCUSDT-postnever1",
        )

    assert len(seen) == 1, "a POST must never be retried"


async def test_post_is_never_retried_on_a_rate_limit_business_code() -> None:
    seen: list[httpx.Request] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "30006", "msg": "request too many"})

    client, seen = make_client(handler, sleeps=[], max_get_retries=5)

    with pytest.raises(BitgetApiError):
        await client.set_leverage("BTCUSDT", leverage="5")

    assert len(seen) == 1, "a POST must never be retried"


async def test_max_get_retries_zero_disables_retries_entirely() -> None:
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "30006", "msg": "too many requests"})

    client, seen = make_client(handler, sleeps=sleeps, max_get_retries=0)

    with pytest.raises(BitgetApiError):
        await client.get_fills()

    assert len(seen) == 1
    assert sleeps == []


async def test_successful_first_get_never_sleeps() -> None:
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok_envelope(empty_fill_page()))

    client, seen = make_client(handler, sleeps=sleeps)

    assert (await client.get_fills())["fillList"] == []
    assert len(seen) == 1
    assert sleeps == []


async def test_transport_failure_still_retries_and_raises_after_exhaustion() -> None:
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")

    client, seen = make_client(handler, sleeps=sleeps, max_get_retries=2)

    with pytest.raises(BitgetApiError):
        await client.get_all_positions()

    assert len(seen) == 3
    assert len(sleeps) == 2


async def test_rate_limit_retry_re_issues_the_same_signed_request() -> None:
    """A retried GET must not reuse a stale signature/timestamp pair."""
    bodies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.headers.get("ACCESS-SIGN"))
        if len(bodies) == 1:
            return httpx.Response(429, json={"code": "30006", "msg": "too many requests"})
        return httpx.Response(200, json=ok_envelope(empty_fill_page()))

    client, seen = make_client(handler, sleeps=[], max_get_retries=2)
    assert (await client.get_fills())["fillList"] == []
    assert len(bodies) == 2
    assert all(signature for signature in bodies), bodies
    assert all(json.loads(request.content.decode()) == {} for request in seen if request.content)


def test_default_retry_budget_is_unchanged_and_configurable() -> None:
    assert DEFAULT_MAX_GET_RETRIES == 2
    assert 0 < DEFAULT_GET_BACKOFF_BASE <= DEFAULT_GET_BACKOFF_CAP
