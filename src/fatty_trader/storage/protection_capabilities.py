"""Persistence for symbol-local Bitget protection capability observations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)


class Cursor(Protocol):
    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> object: ...
    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ProtectionCapabilityRepository(Protocol):
    def get(
        self, exchange: str, environment: str, symbol: str
    ) -> BitgetProtectionCapability | None: ...
    def upsert(self, capability: BitgetProtectionCapability) -> None: ...
    def update_stream(
        self,
        exchange: str,
        environment: str,
        symbol: str,
        *,
        state: StreamState,
        last_stream_at: datetime | None,
        last_error: str | None = None,
    ) -> None: ...


def _key(exchange: str, environment: str, symbol: str) -> tuple[str, str, str]:
    return exchange.strip().lower(), environment.strip().upper(), symbol.strip().upper()


class InMemoryProtectionCapabilityRepository:
    """Deterministic repository used by unit tests and offline simulations."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], BitgetProtectionCapability] = {}

    def get(
        self, exchange: str, environment: str, symbol: str
    ) -> BitgetProtectionCapability | None:
        return self._values.get(_key(exchange, environment, symbol))

    def upsert(self, capability: BitgetProtectionCapability) -> None:
        self._values[_key(capability.exchange, capability.environment, capability.symbol)] = (
            capability
        )

    def update_stream(
        self,
        exchange: str,
        environment: str,
        symbol: str,
        *,
        state: StreamState,
        last_stream_at: datetime | None,
        last_error: str | None = None,
    ) -> None:
        key = _key(exchange, environment, symbol)
        current = self._values.get(key)
        if current is None:
            raise LookupError(f"protection capability not found: {key[0]}:{key[1]}:{key[2]}")
        self._values[key] = BitgetProtectionCapability(
            exchange=current.exchange,
            environment=current.environment,
            symbol=current.symbol,
            position_mode=current.position_mode,
            margin_mode=current.margin_mode,
            native_state=current.native_state,
            fallback_allowed=current.fallback_allowed,
            payload_profile=current.payload_profile,
            last_verified_at=current.last_verified_at,
            last_error=last_error,
            stream_state=state,
            last_stream_at=last_stream_at,
        )


class PostgresProtectionCapabilityRepository:
    """PostgreSQL-backed capability observations with environment isolation."""

    def __init__(self, connection_factory: Callable[[], Connection]) -> None:
        self._connection_factory = connection_factory

    def get(
        self, exchange: str, environment: str, symbol: str
    ) -> BitgetProtectionCapability | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT exchange, environment, symbol, position_mode, margin_mode,
                   native_state, fallback_allowed, payload_profile, last_verified_at,
                   last_error, stream_state, last_stream_at
            FROM bitget_protection_capabilities
            WHERE exchange = %s AND environment = %s AND symbol = %s
            """,
            _key(exchange, environment, symbol),
        )
        row = cursor.fetchone()
        return _capability_from_row(row) if row is not None else None

    def upsert(self, capability: BitgetProtectionCapability) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO bitget_protection_capabilities
                    (exchange, environment, symbol, position_mode, margin_mode,
                     native_state, fallback_allowed, payload_profile, last_verified_at,
                     last_error, stream_state, last_stream_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (exchange, environment, symbol) DO UPDATE SET
                    position_mode = EXCLUDED.position_mode,
                    margin_mode = EXCLUDED.margin_mode,
                    native_state = EXCLUDED.native_state,
                    fallback_allowed = EXCLUDED.fallback_allowed,
                    payload_profile = EXCLUDED.payload_profile,
                    last_verified_at = EXCLUDED.last_verified_at,
                    last_error = EXCLUDED.last_error,
                    stream_state = EXCLUDED.stream_state,
                    last_stream_at = EXCLUDED.last_stream_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    capability.exchange.strip().lower(),
                    capability.environment.strip().upper(),
                    capability.symbol.strip().upper(),
                    capability.position_mode,
                    capability.margin_mode,
                    capability.native_state.value,
                    capability.fallback_allowed,
                    capability.payload_profile,
                    capability.last_verified_at,
                    capability.last_error,
                    capability.stream_state.value,
                    capability.last_stream_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def update_stream(
        self,
        exchange: str,
        environment: str,
        symbol: str,
        *,
        state: StreamState,
        last_stream_at: datetime | None,
        last_error: str | None = None,
    ) -> None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE bitget_protection_capabilities
                SET stream_state = %s, last_stream_at = %s, last_error = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE exchange = %s AND environment = %s AND symbol = %s
                """,
                (
                    state.value,
                    last_stream_at,
                    last_error,
                    *_key(exchange, environment, symbol),
                ),
            )
            if getattr(cursor, "rowcount", 1) == 0:
                raise LookupError("protection capability does not exist")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _row_value(row: Any, values: list[Any], field: str, index: int) -> Any:
    return row.get(field) if isinstance(row, Mapping) else values[index]


def _capability_from_row(row: Any) -> BitgetProtectionCapability:
    values = list(row.values()) if isinstance(row, Mapping) else list(row)
    raw_native = str(_row_value(row, values, "native_state", 5)).upper()
    raw_stream = str(_row_value(row, values, "stream_state", 10)).upper()
    try:
        native_state = NativeProtectionState(raw_native)
    except ValueError:
        native_state = NativeProtectionState.UNKNOWN
    try:
        stream_state = StreamState(raw_stream)
    except ValueError:
        stream_state = StreamState.FAILED
    return BitgetProtectionCapability(
        exchange=str(_row_value(row, values, "exchange", 0)),
        environment=str(_row_value(row, values, "environment", 1)),
        symbol=str(_row_value(row, values, "symbol", 2)),
        position_mode=str(_row_value(row, values, "position_mode", 3)),
        margin_mode=str(_row_value(row, values, "margin_mode", 4)),
        native_state=native_state,
        fallback_allowed=bool(_row_value(row, values, "fallback_allowed", 6)),
        payload_profile=str(_row_value(row, values, "payload_profile", 7)),
        last_verified_at=_row_value(row, values, "last_verified_at", 8),
        last_error=_row_value(row, values, "last_error", 9),
        stream_state=stream_state,
        last_stream_at=_row_value(row, values, "last_stream_at", 11),
    )
