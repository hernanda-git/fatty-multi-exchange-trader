from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.domain.models import MAX_VENUE_LEVERAGE_CEILING
from fatty_trader.exchanges.bitget.client import BitgetApiError
from fatty_trader.risk.liquidation import MMTier
from fatty_trader.risk.sizing import SymbolMetadata

_CATALOG_AUDIT: ContextVar[dict[str, Any] | None] = ContextVar("bitget_catalog_audit", default=None)
_AUDIT_SAMPLE_LIMIT = 8
_METADATA_FIELDS = (
    "symbol", "pricePlace", "priceEndStep", "sizeMultiplier", "minTradeNum",
    "maxOrderQty", "maxMarketOrderQty", "minTradeUSDT", "maxLever",
    "contractValue", "contractSize",
)


@contextmanager
def catalog_audit_context(observation: dict[str, Any]) -> Iterator[None]:
    """Collect this task's public catalog observation without sharing venue state."""
    token = _CATALOG_AUDIT.set(observation)
    try:
        yield
    finally:
        _CATALOG_AUDIT.reset(token)


def record_catalog_observation(evidence: dict[str, Any], code: str) -> None:
    evidence["code"] = code
    observation = _CATALOG_AUDIT.get()
    if observation is not None:
        observation["contract_catalog"] = dict(evidence)


class BitgetCatalogError(BitgetApiError):
    """Malformed or ambiguous public catalog; never proves symbol ineligibility."""

    def __init__(self, message: str, evidence: dict[str, Any], code: str) -> None:
        super().__init__(message)
        record_catalog_observation(evidence, code)
        self.audit_evidence = dict(evidence)


class BitgetContractNotFound(KeyError):
    """A structurally valid catalog contains no exact requested contract."""

    def __init__(self, requested: str, evidence: dict[str, Any]) -> None:
        super().__init__(f"unknown Bitget contract: {requested}")
        record_catalog_observation(evidence, "target_missing")
        self.audit_evidence = dict(evidence)


def _safe_type(value: Any) -> str:
    return type(value).__name__ if type(value) in (str, dict, list, int, float, bool) else (
        "null" if value is None else "other"
    )


def _public_symbol(value: Any) -> bool:
    # The public venue catalog includes CJK names. Whitespace, punctuation,
    # controls, missing identities and unbounded names are still malformed.
    return isinstance(value, str) and 2 <= len(value) <= 32 and value.isalnum()


def _catalog_evidence(contracts: Any, symbol: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "stage": "contract_catalog",
        "requested_symbol": symbol.upper() if isinstance(symbol, str)
        and re.fullmatch(r"[A-Za-z0-9]{2,20}", symbol) else None,
        "payload_type": _safe_type(contracts),
        "row_count": len(contracts) if isinstance(contracts, list) else None,
        "hash_encoding": "canonical-json-data-sha256",
    }
    # Hash the decoded public data, not an account response or transport headers.
    # Stream the encoding so evidence never duplicates the full catalog in memory.
    digest = hashlib.sha256()
    try:
        for chunk in json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).iterencode(contracts):
            digest.update(chunk.encode("ascii"))
        evidence["payload_sha256"] = digest.hexdigest()
    except (TypeError, ValueError, OverflowError):
        evidence["payload_sha256"] = None
    return evidence


def _integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    number = _decimal(value, field, positive=False)
    if number != number.to_integral_value() or not minimum <= number <= maximum:
        raise ValueError(f"invalid {field}")
    return int(number)


def _selected_metadata(contract: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {"symbol": contract["symbol"]}
    for field in _METADATA_FIELDS[1:]:
        value = contract.get(field)
        if type(value) not in (str, int, float):
            continue
        text = str(value)
        if len(text) <= 32 and re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text
        ):
            selected[field] = text
    return selected


def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def metadata_from_contract(contract: dict[str, Any]) -> SymbolMetadata:
    """Normalize one Bitget V2 contract record; never selects another symbol."""
    raw_symbol = contract.get("symbol")
    if not _public_symbol(raw_symbol):
        raise ValueError("contract symbol is invalid")
    symbol = raw_symbol.upper()
    price_precision = _integer(
        contract.get("pricePlace"), "price precision", minimum=0, maximum=12
    )
    price_tick = _decimal(contract.get("priceEndStep"), "price end step") * Decimal(1).scaleb(
        -price_precision
    )
    size_step = _decimal(contract.get("sizeMultiplier"), "size multiplier")
    min_qty = _decimal(contract.get("minTradeNum"), "minimum order quantity")
    # V2 distinguishes single limit/market order quantities. maxPositionNum is
    # a count of held positions, never a base-coin quantity.
    raw_max_qty = contract.get("maxOrderQty")
    max_qty = (
        _decimal(raw_max_qty, "maximum limit order quantity")
        if raw_max_qty not in (None, "")
        else None
    )
    raw_max_market_qty = contract.get("maxMarketOrderQty")
    max_market_qty = (
        _decimal(raw_max_market_qty, "maximum market order quantity")
        if raw_max_market_qty not in (None, "")
        else None
    )
    min_notional = _decimal(contract.get("minTradeUSDT", "0"), "minimum notional", positive=False)
    max_leverage = _integer(
        contract.get("maxLever"), "maximum leverage",
        minimum=1, maximum=MAX_VENUE_LEVERAGE_CEILING,
    )
    contract_value = _decimal(
        contract.get("contractValue", contract.get("contractSize", "1")), "contract value"
    )
    return SymbolMetadata(
        symbol=symbol,
        price_precision=price_precision,
        price_tick=price_tick,
        size_step=size_step,
        min_order_qty=min_qty,
        max_order_qty=max_qty,
        max_market_order_qty=max_market_qty,
        contract_value=contract_value,
        max_leverage=max_leverage,
        min_notional=min_notional,
    )


def mm_tiers_from_position_lever(rows: list[dict[str, Any]], symbol: str) -> tuple[MMTier, ...]:
    """Build MMR tiers from ``/api/v2/mix/market/query-position-lever`` rows.

    Bitget does not publish maintenance-margin rates in ``/market/contracts``;
    they live in the position-lever table, a flat list of
    ``{symbol, level, startUnit, endUnit, keepMarginRate}`` rows ordered by size.
    ``keepMarginRate`` IS the maintenance-margin rate (it is not a
    complement, and ``keepMarginRate * tier leverage`` matches Bitget's published
    liquidation rate).

    Tiers are sorted by ``endUnit`` ascending and the widest is then forced to be
    the catch-all (``None`` bound) so ``select_mmr`` can never fall through a gap.
    Do not rely on a sentinel row: live payloads do not reliably include an
    ``endUnit == 0`` tier (BTCUSDT's last tier reports ``1200000000``), so the
    forced catch-all is the load-bearing part, not the sentinel check.
    Fails closed on a bad payload rather than returning an empty tuple, which
    would abort live sizing.
    """
    requested = symbol.upper()
    matching = [row for row in rows if str(row.get("symbol", "")).upper() == requested]
    if not matching:
        raise ValueError(f"unknown Bitget position-lever symbol: {requested}")

    tiers: list[MMTier] = []
    for row in matching:
        raw_bound = row.get("endUnit")
        # Bitget marks the unbounded final tier with endUnit == 0.
        bound = (
            None
            if raw_bound in (None, "", 0, "0")
            else _decimal(raw_bound, "position-lever endUnit", positive=False)
        )
        mmr = _decimal(row.get("keepMarginRate"), "keepMarginRate", positive=False)
        if mmr <= 0 or mmr > 1:
            raise ValueError("keepMarginRate must be in (0, 1]")
        tiers.append(MMTier(upper_bound_notional=bound, mmr=mmr))

    ordered = sorted(
        tiers,
        key=lambda tier: (
            tier.upper_bound_notional is None,
            tier.upper_bound_notional or 0,
        ),
    )
    # The widest tier becomes the catch-all so any notional resolves an MMR.
    ordered[-1] = MMTier(upper_bound_notional=None, mmr=ordered[-1].mmr)
    return tuple(ordered)


def find_contract(
    contracts: Any, symbol: str, *, audit_evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Validate catalog identities, then select one unambiguous exact target."""
    evidence = _catalog_evidence(contracts, symbol)
    if audit_evidence is not None:
        audit_evidence.update(evidence)
        evidence = audit_evidence
    requested = evidence["requested_symbol"]
    if requested is None:
        raise BitgetCatalogError(
            "Bitget requested contract symbol is invalid", evidence, "requested_symbol_invalid"
        )
    if not isinstance(contracts, list):
        raise BitgetCatalogError(
            "Bitget contracts response is invalid", evidence, "catalog_shape_invalid"
        )
    invalid_rows: list[dict[str, Any]] = []
    invalid_count = 0
    matching: list[tuple[int, dict[str, Any]]] = []
    unicode_symbols: list[str] = []
    unicode_count = 0
    for index, row in enumerate(contracts):
        row_symbol = row.get("symbol") if isinstance(row, dict) else None
        if not isinstance(row, dict) or not _public_symbol(row_symbol):
            invalid_count += 1
            if len(invalid_rows) < _AUDIT_SAMPLE_LIMIT:
                invalid_rows.append({
                    "index": index, "row_type": _safe_type(row),
                    "symbol_type": _safe_type(row_symbol),
                })
            continue
        if not row_symbol.isascii():
            unicode_count += 1
            if len(unicode_symbols) < _AUDIT_SAMPLE_LIMIT:
                unicode_symbols.append(row_symbol)
        # Never let Unicode case folding impersonate an ASCII requested target.
        elif row_symbol.upper() == requested:
            matching.append((index, row))
    evidence.update({
        "invalid_row_count": invalid_count,
        "invalid_rows": invalid_rows,
        "unicode_symbol_count": unicode_count,
        "unicode_symbols": unicode_symbols,
        "matching_row_count": len(matching),
        "matching_row_indexes": [index for index, _ in matching[:_AUDIT_SAMPLE_LIMIT]],
    })
    if invalid_count:
        raise BitgetCatalogError(
            "Bitget contracts response contains invalid rows", evidence, "catalog_rows_invalid"
        )
    if not matching:
        raise BitgetContractNotFound(requested, evidence)
    if len(matching) != 1:
        raise BitgetCatalogError(
            "Bitget requested contract is ambiguous", evidence, "target_ambiguous"
        )
    contract = matching[0][1]
    evidence["field_types"] = {
        field: _safe_type(contract.get(field)) for field in _METADATA_FIELDS
    }
    evidence["selected_metadata"] = _selected_metadata(contract)
    record_catalog_observation(evidence, "target_selected")
    return contract
