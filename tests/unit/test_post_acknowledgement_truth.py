"""Mutation acknowledgements must not fabricate a known rejection."""

import httpx
import pytest

from fatty_trader.exchanges.bitget.client import BitgetUnknownResultError
from tests.unit.test_bitget_client import make_client


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, 500, [], {}, False, ""])
async def test_http_error_validates_original_business_code_type(code):
    client, seen = make_client(
        lambda _: httpx.Response(400, json={"code": code, "msg": "untrusted acknowledgement"})
    )
    try:
        with pytest.raises(BitgetUnknownResultError):
            await client.place_entry_order(
                symbol="BTCUSDT", side="BUY", quantity="0.001", client_oid="offline-typed-code"
            )
        assert len(seen) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (500, "gateway"),
        (500, '{"code":"50000","msg":"server failed"}'),
        (200, "not-json"),
        (400, "gateway"),
        (200, "{}"),
        (200, "[]"),
    ],
)
async def test_untrustworthy_post_ack_is_unknown_without_retry(status, body):
    client, seen = make_client(lambda _: httpx.Response(status, text=body), max_get_retries=3)
    try:
        with pytest.raises(BitgetUnknownResultError):
            await client.place_entry_order(
                symbol="BTCUSDT", side="BUY", quantity="0.001", client_oid="offline-ambiguous"
            )
        assert len(seen) == 1
    finally:
        await client.aclose()
