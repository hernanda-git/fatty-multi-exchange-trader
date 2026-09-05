"""Authenticated Bitget V2 REST transport (async, httpx-based)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

BASE_URL = "https://api.bitget.com"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_GET_RETRIES = 2

_SENSITIVE_TOKENS = ("ACCESS-KEY", "ACCESS-SIGN", "ACCESS-PASSPHRASE", "ACCESS-TIMESTAMP")


class BitgetApiError(Exception):
    """Provider-side or transport error with credentials redacted."""

    def __init__(self, message: str, code: str = "", provider_msg: str = "") -> None:
        super().__init__(redact(message))
        self.code = code
        self.provider_msg = provider_msg


class BitgetUnknownResultError(BitgetApiError):
    """POST failed with unknown server-side result (never retried blindly)."""


def redact(text: str) -> str:
    redacted = text
    for token in _SENSITIVE_TOKENS:
        redacted = redacted.replace(token, "[REDACTED]")
    return redacted


def canonical_query_string(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    return urlencode(sorted(params.items()), doseq=True)


def compact_body(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return ""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def build_signature(
    secret: str, timestamp: str, method: str, path: str, query: str, body: str
) -> str:
    prehash = f"{timestamp}{method.upper()}{path}"
    if query:
        prehash += f"?{query}"
    prehash += body
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _envelope_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise BitgetApiError("Unexpected Bitget response shape")
    if payload.get("code") != "00000":
        code = str(payload.get("code", ""))
        msg = str(payload.get("msg", ""))
        raise BitgetApiError(f"Bitget error {code}: {msg}", code=code, provider_msg=msg)
    return payload.get("data")


class BitgetRestClient:
    """Async Bitget V2 REST client with V2 HMAC signing."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_get_retries: int = DEFAULT_MAX_GET_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_get_retries = max_get_retries
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    self._timeout,
                    connect=self._timeout,
                    read=self._timeout,
                    write=self._timeout,
                    pool=self._timeout,
                ),
                transport=transport,
            )
            self._owns_client = True

    @property
    def timeout(self) -> float:
        return self._timeout

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _signed_headers(self, timestamp: str, signature: str) -> dict[str, str]:
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        query = canonical_query_string(params)
        body = compact_body(payload) if method.upper() != "GET" else ""
        timestamp = str(int(time.time() * 1000))
        signature = build_signature(self._api_secret, timestamp, method, path, query, body)
        headers = self._signed_headers(timestamp, signature)
        url = path if not query else f"{path}?{query}"
        content = body.encode("utf-8") if body else None
        retryable = method.upper() == "GET"

        attempts = 1 + (self._max_get_retries if retryable else 0)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method.upper(), url, headers=headers, content=content
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if retryable and attempt < attempts - 1:
                    continue
                if retryable:
                    raise BitgetApiError(f"Bitget GET {path} transport failure") from None
                raise BitgetUnknownResultError(
                    f"Bitget POST {path} result unknown: transport failure"
                ) from None
            if response.status_code >= 500 and retryable and attempt < attempts - 1:
                last_error = None
                continue
            if response.status_code >= 400:
                try:
                    error_payload = response.json()
                except ValueError:
                    error_payload = None
                if isinstance(error_payload, dict):
                    code = str(error_payload.get("code", ""))
                    msg = str(error_payload.get("msg", ""))
                    if code or msg:
                        raise BitgetApiError(
                            f"Bitget {method.upper()} {path} HTTP {response.status_code}: "
                            f"{code} {msg}".strip(),
                            code=code,
                            provider_msg=msg,
                        )
                raise BitgetApiError(f"Bitget {method.upper()} {path} HTTP {response.status_code}")
            try:
                envelope = response.json()
            except ValueError as exc:
                raise BitgetApiError(f"Bitget {path} invalid JSON") from exc
            return _envelope_data(envelope)
        raise BitgetApiError(f"Bitget GET {path} transport failure") from last_error

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("POST", path, params=params, payload=payload)

    async def get_ticker(self, symbol: str, product_type: str = "USDT-FUTURES") -> dict[str, Any]:
        data = await self._get(
            "/api/v2/mix/market/ticker", {"symbol": symbol, "productType": product_type}
        )
        return data if isinstance(data, dict) else {"data": data}

    async def get_server_time_ms(self) -> int:
        """Return Bitget server time in milliseconds for clock-skew checks."""
        data = await self._get("/api/v2/public/time")
        raw = data.get("serverTime") if isinstance(data, dict) else data
        try:
            return int(str(raw))
        except (TypeError, ValueError) as exc:
            raise BitgetApiError("Bitget server time response is invalid") from exc

    async def get_clock_skew_ms(self) -> int:
        """Return local-minus-server clock skew in milliseconds."""
        return int(time.time() * 1000) - await self.get_server_time_ms()

    async def get_contracts(self, product_type: str = "USDT-FUTURES") -> Any:
        return await self._get("/api/v2/mix/market/contracts", {"productType": product_type})

    async def get_account_bills(
        self,
        symbol: str = "BTCUSDT",
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> Any:
        return await self._get(
            "/api/v2/mix/account/bills",
            {"symbol": symbol, "productType": product_type, "marginCoin": margin_coin},
        )

    async def get_account(
        self,
        symbol: str = "BTCUSDT",
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> Any:
        return await self._get(
            "/api/v2/mix/account/account",
            {"symbol": symbol, "productType": product_type, "marginCoin": margin_coin},
        )

    async def set_leverage(
        self,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        leverage: str = "1",
        hold_side: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "leverage": leverage,
        }
        if hold_side is not None:
            payload["holdSide"] = hold_side
        return await self._post("/api/v2/mix/account/set-leverage", payload)

    async def set_margin_mode(
        self,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        margin_mode: str = "crossed",
    ) -> Any:
        return await self._post(
            "/api/v2/mix/account/set-margin-mode",
            {
                "symbol": symbol,
                "productType": product_type,
                "marginCoin": margin_coin,
                "marginMode": margin_mode,
            },
        )

    async def get_pending_orders(
        self,
        symbol: str | None = None,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> Any:
        params: dict[str, Any] = {"productType": product_type, "marginCoin": margin_coin}
        if symbol is not None:
            params["symbol"] = symbol
        return await self._get("/api/v2/mix/order/orders-pending", params)

    async def get_order_detail(
        self,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        order_id: str | None = None,
        client_oid: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
        }
        if order_id is not None:
            params["orderId"] = order_id
        if client_oid is not None:
            params["clientOid"] = client_oid
        return await self._get("/api/v2/mix/order/detail", params)

    async def get_fills(
        self,
        symbol: str | None = None,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> Any:
        params: dict[str, Any] = {"productType": product_type, "marginCoin": margin_coin}
        if symbol is not None:
            params["symbol"] = symbol
        return await self._get("/api/v2/mix/order/fills", params)

    async def get_single_position(
        self,
        symbol: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> Any:
        return await self._get(
            "/api/v2/mix/position/single-position",
            {"symbol": symbol, "productType": product_type, "marginCoin": margin_coin},
        )

    async def get_all_positions(
        self, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT"
    ) -> Any:
        return await self._get(
            "/api/v2/mix/position/all-position",
            {"productType": product_type, "marginCoin": margin_coin},
        )

    async def place_entry_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: str,
        client_oid: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        margin_mode: str = "isolated",
        order_type: str = "market",
        price: str | None = None,
        trade_side: str = "open",
    ) -> dict[str, Any]:
        """Place one opening order; callers must persist intent before invoking."""
        normalized_side = side.lower()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("Bitget order side must be BUY or SELL")
        if order_type not in {"market", "limit"}:
            raise ValueError("Bitget order type must be market or limit")
        payload: dict[str, Any] = {
            "symbol": symbol.upper(),
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": quantity,
            "side": normalized_side,
            "tradeSide": trade_side,
            "orderType": order_type,
            "reduceOnly": "NO",
            "clientOid": client_oid,
        }
        if order_type == "limit":
            if price is None:
                raise ValueError("limit entry price is required")
            payload["price"] = price
            payload["force"] = "gtc"
        data = await self._post("/api/v2/mix/order/place-order", payload)
        if not isinstance(data, dict):
            raise BitgetApiError("Bitget entry response is invalid")
        return data

    async def place_market_close(
        self,
        *,
        symbol: str,
        side: str,
        quantity: str,
        client_oid: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        margin_mode: str = "isolated",
        trade_side: str = "close",
    ) -> dict[str, Any]:
        """Place one reduce-only market close; never retries an ambiguous POST."""
        normalized_side = side.lower()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("Bitget close side must be BUY or SELL")
        payload = {
            "symbol": symbol.upper(),
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": quantity,
            "side": normalized_side,
            "tradeSide": trade_side,
            "orderType": "market",
            "reduceOnly": "YES",
            "clientOid": client_oid,
        }
        data = await self._post("/api/v2/mix/order/place-order", payload)
        if not isinstance(data, dict):
            raise BitgetApiError("Bitget close response is invalid")
        return data

    async def place_position_tpsl(
        self,
        *,
        symbol: str,
        hold_side: str,
        quantity: str,
        stop_loss: str,
        stop_loss_execute_price: str,
        take_profit: str,
        take_profit_execute_price: str,
        stop_loss_client_oid: str,
        take_profit_client_oid: str,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
    ) -> dict[str, Any]:
        """Place venue-native mark-price SL/TP for the confirmed position size."""
        if hold_side not in {"long", "short"}:
            raise ValueError("Bitget hold side must be long or short")
        data = await self._post(
            "/api/v2/mix/order/place-pos-tpsl",
            {
                "symbol": symbol.upper(),
                "productType": product_type,
                "marginCoin": margin_coin,
                "size": quantity,
                "holdSide": hold_side,
                "stopLossTriggerPrice": stop_loss,
                "stopLossTriggerType": "mark_price",
                "stopLossExecutePrice": stop_loss_execute_price,
                "stopLossClientOid": stop_loss_client_oid,
                "stopSurplusTriggerPrice": take_profit,
                "stopSurplusTriggerType": "mark_price",
                "stopSurplusExecutePrice": take_profit_execute_price,
                "stopSurplusClientOid": take_profit_client_oid,
            },
        )
        if not isinstance(data, dict):
            raise BitgetApiError("Bitget protection response is invalid")
        return data
