"""Scoped database projections and worker-only, GET-only provider observations.

A successful ledger read is not a provider read, admission, ownership proof or
runtime-health proof. Missing observations are unknown, never synthetic empty
successful screens. Financial values remain decimal strings.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import set_json_loads

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    _plan_client_oid,
    _plan_hold_side,
    _plan_order_id,
    _plan_type,
    confirm_native_protection,
)
from fatty_trader.operator.health import normalize_account_snapshot
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


class ReadError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def configured_scope(environ: Mapping[str, str]) -> dict[str, str]:
    mode = environ.get("BITGET_MODE", "LIVE")
    if mode not in {"LIVE", "DEMO"}:
        raise ReadError("invalid_scope", "Configured environment is invalid")
    return {
        "account_id": environ.get("CONSOLE_ACCOUNT_ID")
        or environ.get("BITGET_ACCOUNT_ID")
        or "configured-bitget",
        "venue": "bitget",
        "environment": mode,
    }


def exact_position_owner(
    position: Mapping[str, Any],
    registrations: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    environment: str,
) -> dict[str, Any] | None:
    """Reuse the durable fallback ownership contract, never a symbol-only join."""
    try:
        quantity = Decimal(str(position["total"]))
        if (
            not quantity.is_finite()
            or quantity <= 0
            or position.get("posMode") != "one_way_mode"
            or position.get("marginMode") != "isolated"
        ):
            return None
        candidates = []
        for registration in registrations:
            if (
                registration.get("environment") != environment
                or not registration.get("provider_position_epoch")
                or str(registration["provider_position_epoch"]) != str(position.get("cTime"))
                or registration.get("symbol") != position.get("symbol")
                or str(registration.get("direction", "")).lower()
                != str(position.get("holdSide", "")).lower()
                or Decimal(str(registration.get("quantity"))) != quantity
            ):
                continue
            owners = [
                intent
                for intent in intents
                if intent.get("client_order_id") == registration.get("position_key")
                and intent.get("symbol") == position.get("symbol")
                and intent.get("side") == ("BUY" if position.get("holdSide") == "long" else "SELL")
                and intent.get("role") == "ENTRY"
                and intent.get("state") in {"filled", "partially_filled", "reconciled"}
                and Decimal(str(intent.get("filled_qty"))) == quantity
                and not intent.get("baseline_id")
            ]
            if len(owners) == 1:
                candidates.append(
                    {"registration": registration, "intent": owners[0], "quantity": str(quantity)}
                )
        return candidates[0] if len(candidates) == 1 else None
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None


_OWNERSHIP_ROOT_SQL = """
SELECT i.*,r.environment,br.baseline_id,e.provider_position_epoch AS recorded_position_epoch,
 e.environment AS epoch_environment,e.account_id AS epoch_account_id FROM live_order_intents i
JOIN bitget_margin_reservations r ON r.id=i.margin_reservation_id
 AND r.client_order_id=i.client_order_id AND r.binding_symbol=i.symbol
 AND r.balance_snapshot_id=i.balance_snapshot_id
JOIN dispatches d ON d.id=r.dispatch_id AND d.id=i.dispatch_id
LEFT JOIN bitget_baseline_records br ON br.record_kind='intent' AND br.record_id=i.id
LEFT JOIN console_position_epochs e ON e.root_client_order_id=i.client_order_id
WHERE i.exchange='bitget' AND i.role='ENTRY' AND i.entry_leg IS DISTINCT FROM 'limit'
 AND r.environment=%s AND i.filled_qty>0
 AND NOT EXISTS(SELECT 1 FROM bitget_baseline_records b
 WHERE (b.record_kind='reservation' AND b.record_id=r.id)
 OR (b.record_kind='dispatch' AND b.record_id=d.id))
ORDER BY i.updated_at DESC,i.id
"""


def native_position_owner(
    position: Mapping[str, Any],
    plans: list[dict[str, Any]],
    roots: list[dict[str, Any]],
    environment: str,
    account_id: str | None = None,
) -> dict[str, Any] | None:
    """Bind exact durable residual to immutable epoch or current root-owned plans."""
    try:
        quantity = Decimal(str(position["total"]))
        epoch = str(position.get("cTime", ""))
        if (
            not quantity.is_finite()
            or quantity <= 0
            or not re.fullmatch(r"[1-9][0-9]*", epoch)
            or position.get("posMode") != "one_way_mode"
            or position.get("marginMode") != "isolated"
        ):
            return None
        candidates = []
        for root in roots:
            if (
                root.get("baseline_id")
                or root.get("environment") != environment
                or root.get("role") != "ENTRY"
                or root.get("entry_leg") == "limit"
                or root.get("symbol") != position.get("symbol")
                or root.get("side") != ("BUY" if position.get("holdSide") == "long" else "SELL")
                or Decimal(str(root.get("remaining_owned_quantity"))) != quantity
            ):
                continue
            recorded_epoch = root.get("recorded_position_epoch")
            if recorded_epoch:
                if (
                    str(recorded_epoch) != epoch
                    or root.get("epoch_environment") != environment
                    or (account_id is not None and root.get("epoch_account_id") != account_id)
                ):
                    continue
                candidates.append(
                    {
                        "intent": root,
                        "quantity": str(quantity),
                        "provider_position_epoch": epoch,
                        "proof": "immutable-scoped-epoch-and-durable-residual",
                    }
                )
                continue
            protection_root = root.get("protection_root_oid") or root["client_order_id"]
            sl_oid = (root.get("stop_mutation_root_oid") or protection_root) + "-sl"
            tp_oid = protection_root + "-tp"
            identities = []
            for identifier, oid, plan_type, leg in (
                (position.get("stopLossId"), sl_oid, "pos_loss", "stopLoss"),
                (position.get("takeProfitId"), tp_oid, "pos_profit", "stopSurplus"),
            ):
                matches = [
                    plan
                    for plan in plans
                    if _plan_order_id(plan)
                    and (not identifier or _plan_order_id(plan) == str(identifier))
                    and _plan_client_oid(plan, leg) == oid
                    and plan.get("symbol") == position.get("symbol")
                    and _plan_type(plan) == plan_type
                    and _plan_hold_side(plan) == ("buy" if root["side"] == "BUY" else "sell")
                ]
                identities.append(len(matches) == 1)
            if all(identities):
                candidates.append(
                    {
                        "intent": root,
                        "quantity": str(quantity),
                        "provider_position_epoch": epoch,
                        "proof": "exact-native-identities-and-durable-residual",
                    }
                )
        return candidates[0] if len(candidates) == 1 else None
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None


def add_owned_residuals(
    store: Any, roots: list[dict[str, Any]], connection: Any = None
) -> list[dict[str, Any]]:
    """Read the canonical split/verified-close residual, never duplicate its math."""
    if not roots:
        return []
    if connection is None:
        with store.connection() as owned:
            return add_owned_residuals(store, roots, owned)
    ledger = PostgresLiveIntentStore(connection_factory=lambda: connection)
    result = []
    for root in roots:
        try:
            with connection.cursor(row_factory=tuple_row) as cursor:
                residual = ledger.remaining_owned_quantity(root["client_order_id"], cursor=cursor)
            result.append({**root, "remaining_owned_quantity": residual})
        except (ValueError, ArithmeticError):
            continue
    return result


_SECRET_KEY = re.compile(
    r"secret|password|passphrase|credential|authorization|cookie|csrf|private_key|public_key|"
    r"token|challenge|assertion|signature|session_hash|hash_value|filename|file_path|"
    r"storage_ref|media_path|bootstrap|confirmation_phrase|lease_token",
    re.I,
)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?(?:key|secret)|password|passphrase|token|authorization|"
    r"cookie)\s*[:=]\s*[^\s,;]+|postgres(?:ql)?://[^\s]+)"
)


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): sanitize(v)
            for k, v in value.items()
            if str(k) == "pair_token" or not _SECRET_KEY.search(str(k))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, str):
        return _SECRET_TEXT.sub("[redacted]", value)
    if isinstance(value, (int, bool, float)) or value is None:
        return value
    return str(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(sanitize(value), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            return None
    return None


def _evidence(
    identifier: str,
    source: str,
    observed: Any,
    *,
    complete: bool,
    error: str | None = None,
    updated: Any = None,
    stale_after: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _time(observed)
    now = now or datetime.now(UTC)
    age = max(0.0, (now - timestamp).total_seconds()) if timestamp else None
    return {
        "id": identifier,
        "source": source,
        "read_status": "unknown"
        if timestamp is None or error
        else "complete"
        if complete
        else "partial",
        "observed_at": timestamp.isoformat() if timestamp else None,
        "record_updated_at": sanitize(updated),
        "age_seconds": age,
        "stale_after_seconds": stale_after,
        "freshness": "unknown"
        if age is None
        else "stale"
        if stale_after is not None and age > stale_after
        else "fresh",
        "complete": bool(complete and timestamp and not error),
        "error_code": error,
    }


# Fixed table/column expressions only; neither resource names nor SQL come from a client.
_TABLES: dict[str, tuple[str, str, str]] = {
    "signals": ("telegram_messages", "id", "received_at"),
    "dispatches": ("dispatches", "id", "updated_at"),
    "orders": ("live_order_intents", "id", "updated_at"),
    "fills": ("fills", "id", "filled_at"),
    "management": ("source_management_updates", "id", "updated_at"),
    "jobs": ("console_jobs", "id", "created_at"),
    "approvals": ("console_approvals", "id", "created_at"),
    "audit": ("console_audit_events", "id", "created_at"),
    "notifications": ("notifications_outbox", "id", "created_at"),
    "backups": ("console_backups", "id", "created_at"),
    "releases": ("console_artifacts", "id", "created_at"),
    "config/history": ("console_config_versions", "id", "created_at"),
    "config/drafts": ("console_config_versions", "id", "created_at"),
    "recovery/baselines": ("bitget_audited_baselines", "id", "created_at"),
    "account/admission-snapshots": ("balance_snapshots", "id", "captured_at"),
    "account/reservations": ("bitget_margin_reservations", "id", "created_at"),
    "access/principals": ("console_principals", "id", "created_at"),
    "access/sessions": ("console_sessions", "id", "created_at"),
}
_COMMON_FILTERS = {"search", "from", "to", "sort", "account_id", "environment", "venue"}
_FILTERS = {
    "signals": {
        "source",
        "channel_id",
        "message_id",
        "state",
        "intake_state",
        "symbol",
        "direction",
        "revision",
        "dispatch_state",
        "terminal_reason",
        "has_media",
        "classification",
    },
    "dispatches": {
        "source_id",
        "source",
        "symbol",
        "direction",
        "revision",
        "state",
        "terminal_reason",
        "exchange",
    },
    "orders": {
        "symbol",
        "role",
        "state",
        "client_order_id",
        "provider_order_id",
        "dispatch_id",
        "entry_leg",
        "order_type",
        "ownership",
    },
    "fills": {
        "symbol",
        "client_order_id",
        "provider_fill_id",
        "intent_id",
        "ownership",
        "source",
        "fee_ccy",
    },
    "management": {"symbol", "state", "action", "source_message_id", "revision"},
    "jobs": {"state", "action", "actor_id", "resource_id", "channel", "approval_reference"},
    "approvals": {"job_id", "actor_id", "approver_id", "state"},
    "audit": {"action", "actor_id", "job_id", "resource_id", "request_id", "channel", "outcome"},
    "notifications": {"state", "event_kind", "dedup_key", "job_id", "source_id", "intent_id"},
    "positions": {"symbol", "direction", "ownership", "protection_state", "state"},
    "protection": {
        "symbol",
        "direction",
        "ownership",
        "native_state",
        "fallback_state",
        "stream_state",
        "missing_sl",
        "missing_tp",
    },
    "services": {"state", "service_id"},
    "services/logs": {"service_id"},
    "backups": {"state", "release_id", "verified"},
    "releases": {"state", "approved"},
    "config": {"kind", "state"},
    "config/history": {"kind", "state"},
    "config/drafts": {"kind", "state"},
    "recovery/baselines": {"approval_reference", "record_kind", "record_id"},
    "account/reservations": {"symbol", "state", "client_order_id", "dispatch_id"},
    "account/admission-snapshots": {"margin_coin"},
    "account/pnl": {"symbol", "client_order_id", "ownership", "fee_ccy", "source", "intent_id"},
    "access/principals": {"enabled", "role"},
    "access/sessions": {"principal_id", "revoked"},
    "media": {"message_id"},
    "artifacts": set(),
    "overview": set(),
    "account": {"symbol", "ownership"},
    "recovery": set(),
    "jobs/steps": {"job_id"},
    "access": set(),
    "access/policy": set(),
}


def _filters(
    section: str, filters: Mapping[str, Any] | None, scope: dict[str, str]
) -> dict[str, str]:
    supplied = dict(filters or {})
    unknown = set(supplied) - _COMMON_FILTERS - _FILTERS.get(section, set())
    if unknown:
        raise ReadError("invalid_filter", "Unsupported filter")
    output: dict[str, str] = {}
    for key, value in supplied.items():
        if value is None or value == "":
            continue
        if not isinstance(value, (str, int, bool)) or len(str(value)) > 500:
            raise ReadError("invalid_filter", "Invalid filter value")
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if key in {"account_id", "environment", "venue"} and text != scope[key]:
            raise ReadError("scope_mismatch", "Requested scope is unavailable", 403)
        if key in {"from", "to"}:
            parsed = _time(text)
            if parsed is None or not re.search(r"(?:Z|[+-]\d\d:\d\d)$", text):
                raise ReadError(
                    "invalid_filter", "Time filters must be timezone-qualified ISO timestamps"
                )
            text = parsed.isoformat()
        if key == "sort" and text not in {"newest", "oldest"}:
            raise ReadError("invalid_sort", "Unsupported sort")
        output[key] = text
    if output.get("from") and output.get("to") and output["from"] > output["to"]:
        raise ReadError("invalid_filter", "Invalid time interval")
    return output


class ReadService:
    def __init__(
        self,
        store: Any,
        scope: Mapping[str, str] | None = None,
        media_root: str | Path | None = None,
    ) -> None:
        self.store = store
        self.scope = dict(scope) if scope is not None else configured_scope(os.environ)
        if (
            self.scope.get("environment") not in {"LIVE", "DEMO"}
            or self.scope.get("venue") != "bitget"
            or not self.scope.get("account_id")
        ):
            raise ReadError("invalid_scope", "Configured scope is invalid")
        self.media_root = Path(
            media_root
            or os.environ.get("CONSOLE_MEDIA_ROOT")
            or os.environ.get("TELEGRAM_MEDIA_ROOT", "/data/media")
        )
        self._cursor_sql: Any = None
        self._evidence: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._now = datetime.now(UTC)

    def _query(
        self, sql: str, params: tuple[Any, ...] = (), name: str = "ledger"
    ) -> list[dict[str, Any]] | None:
        try:
            if self._cursor_sql is None:
                rows = self.store.query(sql, params)
            else:
                # A failed independent source must not abort the remaining read projections.
                self._cursor_sql.execute("SAVEPOINT console_read_source")
                try:
                    self._cursor_sql.execute(sql, params)
                    rows = list(self._cursor_sql.fetchall())
                except Exception:
                    self._cursor_sql.execute("ROLLBACK TO SAVEPOINT console_read_source")
                    raise
                finally:
                    self._cursor_sql.execute("RELEASE SAVEPOINT console_read_source")
            records = [dict(row) for row in rows]
            timestamps: list[datetime | None] = []
            for row in records:
                record = row.get("record", row)
                if isinstance(record, dict):
                    timestamps.extend(
                        _time(record.get(field))
                        for field in (
                            "updated_at",
                            "created_at",
                            "captured_at",
                            "received_at",
                            "filled_at",
                            "collected_at",
                        )
                    )
            updated = max((time for time in timestamps if time is not None), default=None)
            self._evidence.append(
                _evidence(name, "ledger", self._now, complete=True, updated=updated, now=self._now)
            )
            return records
        except Exception:
            self._evidence.append(
                _evidence(
                    name,
                    "ledger",
                    None,
                    complete=False,
                    error="database_read_unavailable",
                    now=self._now,
                )
            )
            self._warnings.append(f"{name}: database observation unavailable")
            return None

    def _snapshot(self, kind: str) -> Any:
        def load(latest_good: bool = False) -> dict[str, Any] | None:
            if not latest_good and self._cursor_sql is None:
                return cast(dict[str, Any] | None, self.store.latest_snapshot(kind, self.scope))
            rows = self._query(
                "SELECT * FROM console_snapshots WHERE kind=%s AND scope=%s::jsonb"
                + (
                    " AND data IS NOT NULL AND error IS NULL AND observed_at IS NOT NULL"
                    if latest_good
                    else ""
                )
                + " ORDER BY collected_at DESC,id DESC LIMIT 1",
                (kind, json.dumps(self.scope)),
                f"persisted:{kind}",
            )
            return rows[0] if rows else None

        try:
            loaded = load()
            row = dict(loaded) if loaded else None
        except Exception:
            row = None
        data = row.get("data") if row else None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = None
        error = row.get("error") if row else "observation_unavailable"
        if data is None and not error:
            error = "observation_unavailable"
        last_known = bool(error)
        if error:
            try:
                previous = load(latest_good=True)
            except Exception:
                previous = None
            if previous:
                row = dict(previous)
                data = row.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        data = None
        complete = isinstance(data, dict) and data.get("complete", False)
        source = (
            "runner"
            if kind
            in {
                "services",
                "host",
                "service_logs",
                "host_effective_config",
                "notification_schedule",
            }
            else "configuration"
            if kind == "effective_config"
            else "worker"
            if kind in {"stream", "protection_proofs"}
            else "provider"
        )
        self._evidence.append(
            _evidence(
                f"snapshot:{kind}:{row.get('id') if row else 'unknown'}",
                source,
                row.get("observed_at") if row else None,
                complete=complete,
                error="provider_read_unavailable"
                if error and source == "provider"
                else "observation_unavailable"
                if error
                else None,
                stale_after=120 if source in {"runner", "worker", "configuration"} else 15,
                now=self._now,
            )
        )
        if error:
            self._warnings.append(f"{kind}: observation unavailable")
            if not isinstance(data, dict):
                return None
            return sanitize(
                {
                    **data,
                    "complete": False,
                    "last_known": True,
                    "refresh_error": "observation_unavailable",
                }
            )
        if not complete:
            self._warnings.append(f"{kind}: observation coverage is partial")
        return sanitize({**data, "last_known": last_known}) if isinstance(data, dict) else None

    def read(
        self,
        section: str,
        resource_id: str | None = None,
        filters: Mapping[str, Any] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        # API uses one shared service instance; each request owns its mutable
        # transaction/evidence state rather than racing other threadpool reads.
        request = ReadService(self.store, self.scope, self.media_root)
        return request._response(section, resource_id, filters, limit, cursor)

    def _response(
        self,
        section: str,
        resource_id: str | None,
        filters: Mapping[str, Any] | None,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        section = section.strip("/")
        if section not in _FILTERS and section not in _TABLES:
            raise ReadError("unknown_section", "Unknown read section", 404)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ReadError("invalid_limit", "Limit must be between 1 and 200")
        if resource_id is not None and (not isinstance(resource_id, str) or len(resource_id) > 200):
            raise ReadError("invalid_resource", "Invalid resource identifier")
        selected = _filters(section, filters, self.scope)
        self._now, self._evidence, self._warnings = datetime.now(UTC), [], []
        if section == "media":
            return self._media(resource_id, selected)
        if section == "artifacts":
            return self._artifact(resource_id)
        # One transaction binds ledger projections and the event high-water mark.
        try:
            with self.store.connection() as connection:
                set_json_loads(
                    lambda value: json.loads(value, parse_float=Decimal), context=connection
                )
                with connection.cursor(row_factory=dict_row) as sql_cursor:
                    self._cursor_sql = sql_cursor
                    sql_cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    data = self._read(section, resource_id, selected, limit, cursor)
                    events = self._query(
                        "SELECT coalesce(max(id),0) AS event_cursor FROM console_event_outbox "
                        "WHERE scope=%s::jsonb",
                        (json.dumps(self.scope),),
                        name="event_cursor",
                    )
                    event_cursor = events[0]["event_cursor"] if events else None
        except ReadError:
            raise
        except Exception:
            # Connection failure has explicit unknown data; provider snapshots cannot
            # be claimed fresh if their persisted source could not be read.
            data, event_cursor = None, None
            self._evidence.append(
                _evidence(
                    "database", "ledger", None, complete=False, error="database_read_unavailable"
                )
            )
            self._warnings.append("Database snapshot unavailable")
        finally:
            self._cursor_sql = None
        return {
            "api_version": "v1",
            "server_time": self._now.isoformat(),
            "snapshot_id": _digest(
                {"section": section, "data": data, "event_cursor": event_cursor}
            ),
            "data": sanitize(data),
            "evidence": list(
                {
                    json.dumps(item, sort_keys=True, default=str): item for item in self._evidence
                }.values()
            ),
            "warnings": self._warnings,
            "event_cursor": event_cursor,
            "scope": self.scope,
        }

    def _read(
        self,
        section: str,
        resource: str | None,
        filters: dict[str, str],
        limit: int,
        cursor: str | None,
    ) -> Any:
        if section in _TABLES:
            return self._table(section, resource, filters, limit, cursor)
        if section in {"positions", "protection"}:
            return self._positions(section, resource, filters, limit, cursor)
        if section == "overview":
            return {
                "provider": {
                    kind: self._snapshot(kind)
                    for kind in (
                        "account",
                        "positions",
                        "orders",
                        "plan_orders",
                        "fills",
                        "contracts",
                    )
                },
                "ledger": self._query(
                    "SELECT (SELECT count(*) FROM live_order_intents WHERE exchange='bitget') "
                    "AS intents, (SELECT count(*) FROM fills WHERE exchange='bitget') AS fills, "
                    "(SELECT count(*) FROM positions WHERE exchange='bitget' "
                    "AND closed_at IS NULL) AS open_positions",
                    name="ledger_summary",
                ),
                "latches": self._query(
                    "SELECT * FROM venue_kill_switches WHERE scope IN ('global','bitget') "
                    "ORDER BY scope",
                    name="latches",
                ),
                "admission_snapshots": self._query(
                    "SELECT * FROM balance_snapshots WHERE exchange='bitget' "
                    "ORDER BY captured_at DESC,id DESC LIMIT 1",
                    name="admission_snapshots",
                ),
                "services": self._snapshot("services"),
                "effective_config": self._snapshot("effective_config"),
                "clock": self._snapshot("clock"),
                "stream": self._snapshot("stream"),
                "recent_signals": self._table("signals", None, {}, 10, None),
                "recent_dispatches": self._table("dispatches", None, {}, 10, None),
                "jobs": self._query(
                    "SELECT state,count(*) AS count FROM console_jobs WHERE scope=%s::jsonb "
                    "GROUP BY state ORDER BY state",
                    (json.dumps(self.scope),),
                    "job_summary",
                ),
                "admission_verdict": None,
            }
        if section == "account":
            return {
                "provider": self._snapshot("account"),
                "provider_identity": self._snapshot("account_identity"),
                "provider_positions": self._snapshot("positions"),
                "ledger_pnl": self._pnl(filters),
                "admission_snapshots": self._table(
                    "account/admission-snapshots", None, {}, limit, None
                ),
                "reservations": self._table("account/reservations", None, {}, limit, None),
                "baseline_exclusions": self._table("recovery/baselines", None, {}, limit, None),
                "coverage": "ledger-recorded-fills-only; provider GET does not refresh admission",
            }
        if section == "account/pnl":
            return self._pnl(filters)
        if section == "recovery":
            return {
                "latches": self._query(
                    "SELECT * FROM venue_kill_switches WHERE scope IN "
                    "('global','bitget','bitget-protection-stream') ORDER BY scope",
                    name="latches",
                ),
                "unresolved_intents": self._query(
                    "SELECT * FROM live_order_intents WHERE exchange='bitget' AND state IN "
                    "('staged','requested','acknowledged','submitted',"
                    "'partially_filled','unknown') "
                    "ORDER BY updated_at DESC,id DESC LIMIT 200",
                    name="unresolved_intents",
                ),
                "reservations": self._table("account/reservations", None, {}, limit, cursor),
                "baselines": self._table("recovery/baselines", None, {}, limit, None),
                "provider": {
                    kind: self._snapshot(kind)
                    for kind in ("positions", "orders", "plan_orders", "clock")
                },
                "reconciliation": self._query(
                    "SELECT * FROM reconciliation_state WHERE scope IN ('global','bitget')",
                    name="reconciliation",
                ),
                "acknowledgments": self._query(
                    "SELECT id,actor_id,arguments,reason,created_at FROM console_jobs "
                    "WHERE action='incident.acknowledge' AND state='succeeded' "
                    "AND scope=%s::jsonb ORDER BY created_at DESC LIMIT 100",
                    (json.dumps(self.scope),),
                    "incident_acknowledgments",
                ),
                "proof_status": "unknown",
                "proof_note": "Inventory is not an audited flat-recovery apply proof",
            }
        if section == "services":
            observation = self._snapshot("services")
            rows = observation.get("items") if isinstance(observation, dict) else None
            return self._list_memory(section, rows, resource, filters, limit, cursor, observation)
        if section == "services/logs":
            observation = self._snapshot("service_logs")
            rows = observation.get("items") if isinstance(observation, dict) else None
            log_filters = {**filters, **({"service_id": resource} if resource else {})}
            return self._list_memory(section, rows, None, log_filters, limit, cursor, observation)
        if section == "config":
            return {
                "requested": self._table("config/history", resource, filters, limit, cursor),
                "effective": self._snapshot("effective_config"),
                "host": self._snapshot("host_effective_config"),
                "requested_is_effective": None,
            }
        if section == "jobs/steps":
            job_id = resource or filters.get("job_id")
            if not job_id:
                raise ReadError("invalid_resource", "Job identifier is required")
            return self._query(
                "SELECT s.* FROM console_job_steps s JOIN console_jobs j ON j.id=s.job_id "
                "WHERE s.job_id::text=%s AND j.scope=%s::jsonb ORDER BY s.created_at,s.id",
                (job_id, json.dumps(self.scope)),
                "job_steps",
            )
        if section in {"access", "access/policy"}:
            return {
                "principals": self._table("access/principals", resource, filters, limit, cursor),
                "sessions": self._table("access/sessions", None, {}, limit, None),
                "policy": self._query(
                    "SELECT * FROM console_policy_versions ORDER BY created_at DESC LIMIT 1",
                    name="policy",
                ),
            }
        if section == "artifacts":
            raise ReadError(
                "artifact_download_prohibited",
                "Use the reviewed operations artifact delivery policy",
                403,
            )
        raise ReadError("unknown_section", "Unknown read section", 404)

    def _base(self, section: str) -> tuple[str, list[Any]]:
        table, key, stamp = _TABLES[section]
        projection = "to_jsonb(t)"
        if section == "signals":
            projection += (
                " || jsonb_build_object('canonical_signals',(SELECT "
                "coalesce(jsonb_agg(to_jsonb(cs)),'[]'::jsonb) FROM canonical_signals cs "
                "WHERE cs.message_id=t.id AND cs.revision=t.revision_hash),'dispatches',"
                "(SELECT coalesce(jsonb_agg(to_jsonb(d)),'[]'::jsonb) FROM dispatches d "
                "JOIN canonical_signals cs ON cs.id=d.source_id WHERE cs.message_id=t.id "
                "AND cs.revision=t.revision_hash),'management_ids',(SELECT "
                "coalesce(jsonb_agg(m.id),'[]'::jsonb) FROM source_management_updates m "
                "WHERE m.source_message_id=t.id AND m.revision=t.revision_hash))"
            )
        if section in {"orders", "fills"}:
            projection += (
                " || jsonb_build_object('ownership'," + self._expression(section, "ownership") + ")"
            )
        sql = (
            f"SELECT {projection} AS record, t.{key}::text AS row_id, "
            f"t.{stamp} AS sort_time FROM {table} t"
        )
        predicates: list[str] = []
        args: list[Any] = []
        if section in {
            "dispatches",
            "orders",
            "fills",
            "account/admission-snapshots",
            "account/reservations",
        }:
            predicates.append("t.exchange='bitget'")
        if section == "dispatches":
            predicates.append(
                "(t.source_type<>'console_manual' OR EXISTS(SELECT 1 FROM "
                "console_manual_entries m WHERE m.id=t.source_id AND m.account_id=%s "
                "AND m.environment=%s))"
            )
            args.extend([self.scope["account_id"], self.scope["environment"]])
        if section in {"jobs", "audit"}:
            predicates.append("(to_jsonb(t)->'scope')=%s::jsonb")
            args.append(json.dumps(self.scope))
        if section == "approvals":
            predicates.append(
                "EXISTS(SELECT 1 FROM console_jobs j WHERE j.id=t.job_id AND j.scope=%s::jsonb)"
            )
            args.append(json.dumps(self.scope))
        if section in {"config/history", "config/drafts", "backups", "releases"}:
            predicates.extend(["t.account_id=%s", "t.environment=%s"])
            args.extend([self.scope["account_id"], self.scope["environment"]])
        if section == "releases":
            predicates.append("t.kind='release'")
        if section == "config/drafts":
            predicates.append("t.state='draft'")
        if section == "recovery/baselines":
            predicates.extend(["t.environment=%s", "t.account_id=%s"])
            args.extend([self.scope["environment"], self.scope["account_id"]])
        if section == "account/reservations":
            predicates.append("(t.environment=%s OR t.environment IS NULL)")
            args.append(self.scope["environment"])
        return sql + " WHERE " + (" AND ".join(predicates) if predicates else "TRUE"), args

    def _expression(self, section: str, field: str) -> str:
        aliases = {"state": "intake_state"} if section == "signals" else {}
        field = aliases.get(field, field)
        if section == "signals" and field in {
            "symbol",
            "direction",
            "classification",
            "dispatch_state",
            "terminal_reason",
        }:
            if field == "classification":
                return (
                    "CASE WHEN EXISTS (SELECT 1 FROM canonical_signals cs WHERE "
                    "cs.message_id=t.id AND cs.revision=t.revision_hash) THEN 'canonical' "
                    "WHEN EXISTS(SELECT 1 FROM source_management_updates m WHERE "
                    "m.source_message_id=t.id AND m.revision=t.revision_hash) "
                    "THEN 'management' ELSE 'unknown' END"
                )
            if field in {"dispatch_state", "terminal_reason"}:
                name = "state" if field == "dispatch_state" else "terminal_reason"
                return (
                    f"(SELECT d.{name} FROM dispatches d JOIN canonical_signals cs "
                    "ON cs.id=d.source_id WHERE cs.message_id=t.id AND "
                    "cs.revision=t.revision_hash AND d.exchange='bitget' "
                    "ORDER BY d.updated_at DESC,d.id DESC LIMIT 1)"
                )
            name = "pair_token" if field == "symbol" else field
            return (
                f"(SELECT cs.{name} FROM canonical_signals cs WHERE cs.message_id=t.id "
                "AND cs.revision=t.revision_hash LIMIT 1)"
            )
        if field == "revision" and section == "signals":
            field = "revision_hash"
        if field == "source" and section == "signals":
            field = "channel_id"
        if section == "dispatches" and field in {"symbol", "direction"}:
            name = "pair_token" if field == "symbol" else field
            return (
                f"coalesce((SELECT cs.{name} FROM canonical_signals cs WHERE "
                f"cs.id=t.source_id LIMIT 1),(SELECT m.{field} FROM console_manual_entries m "
                "WHERE m.id=t.source_id LIMIT 1))"
            )
        if section == "notifications":
            if field == "state":
                return (
                    "CASE WHEN t.sent_at IS NOT NULL THEN 'sent' WHEN t.failed_at IS NOT NULL "
                    "THEN 'terminal-failed' WHEN t.lease_until>now() THEN 'leased' "
                    "WHEN t.attempts>0 THEN 'retry' ELSE 'pending' END"
                )
            if field in {"event_kind", "job_id", "source_id", "intent_id"}:
                return f"t.payload->>'{field}'"
        if section == "fills" and field == "intent_id":
            return (
                "(SELECT i.id::text FROM live_order_intents i WHERE i.exchange=t.exchange "
                "AND i.client_order_id=t.client_order_id)"
            )
        if section == "fills" and field == "source":
            return (
                "coalesce((SELECT e.source FROM provider_reconciliation_events e WHERE "
                "e.exchange=t.exchange AND e.provider_fill_id=t.provider_fill_id),'ledger')"
            )
        if section == "dispatches" and field == "source":
            return (
                "(SELECT tm.channel_id::text FROM canonical_signals cs JOIN telegram_messages "
                "tm ON tm.id=cs.message_id WHERE cs.id=t.source_id)"
            )
        if section == "approvals" and field in {"actor_id", "approver_id"}:
            return "t.principal_id"
        if section == "approvals" and field == "state":
            return "t.decision"
        if section == "jobs" and field == "resource_id":
            return (
                "(SELECT ref->>'id' FROM jsonb_array_elements(t.target_refs) ref "
                "WHERE ref->>'id' IS NOT NULL LIMIT 1)"
            )
        if section == "backups" and field == "verified":
            return "(t.verified_at IS NOT NULL)::text"
        if section == "access/principals" and field == "role":
            return "(SELECT role FROM jsonb_array_elements_text(t.roles) role LIMIT 1)"
        if section == "access/sessions" and field == "revoked":
            return "(t.revoked_at IS NOT NULL)::text"
        if section == "recovery/baselines" and field in {"record_id", "record_kind"}:
            return (
                f"(SELECT br.{field}::text FROM bitget_baseline_records br WHERE "
                "br.baseline_id=t.id ORDER BY br.record_kind,br.record_id LIMIT 1)"
            )
        if section in {"fills", "orders"} and field == "ownership":
            intent = (
                "t.id"
                if section == "orders"
                else "(SELECT i.id FROM live_order_intents i WHERE i.exchange=t.exchange "
                "AND i.client_order_id=t.client_order_id)"
            )
            environment = self.scope["environment"]
            return (
                "CASE WHEN EXISTS(SELECT 1 FROM bitget_baseline_records br WHERE "
                f"br.record_kind='intent' AND br.record_id={intent}) THEN 'account_baseline' "
                "WHEN EXISTS(SELECT 1 FROM live_order_intents oi JOIN bitget_margin_reservations "
                f"r ON r.id=oi.margin_reservation_id WHERE oi.id={intent} "
                f"AND r.environment='{environment}' AND r.binding_symbol=oi.symbol "
                "AND r.client_order_id=oi.client_order_id "
                "AND r.balance_snapshot_id=oi.balance_snapshot_id) OR EXISTS(SELECT 1 FROM "
                "live_order_intents oi JOIN bitget_verified_close_bindings c "
                "ON c.close_client_order_id=oi.client_order_id JOIN bitget_margin_reservations "
                f"r ON r.id=c.reservation_id WHERE oi.id={intent} "
                f"AND r.environment='{environment}') THEN 'bot' ELSE 'unresolved' END"
            )
        return f"to_jsonb(t)->>'{field}'"

    def _table(
        self,
        section: str,
        resource: str | None,
        filters: dict[str, str],
        limit: int,
        cursor: str | None,
    ) -> Any:
        sql, args = self._base(section)
        _, key, stamp = _TABLES[section]
        observations = {
            kind: self._snapshot(kind)
            for kind in (
                ("orders", "plan_orders")
                if section == "orders"
                else ("fills",)
                if section == "fills"
                else ()
            )
        }
        schedule = self._snapshot("notification_schedule") if section == "notifications" else None
        for name, value in filters.items():
            if name in {"account_id", "environment", "venue", "sort"}:
                continue
            if name in {"from", "to"}:
                sql += f" AND t.{stamp} {'>=' if name == 'from' else '<='} %s::timestamptz"
            elif section == "signals" and name in {"dispatch_state", "terminal_reason"}:
                column = "state" if name == "dispatch_state" else "terminal_reason"
                sql += (
                    " AND EXISTS(SELECT 1 FROM dispatches d JOIN canonical_signals cs "
                    "ON cs.id=d.source_id WHERE cs.message_id=t.id "
                    "AND cs.revision=t.revision_hash AND d.exchange='bitget' "
                    f"AND d.{column}=%s)"
                )
            elif section == "access/principals" and name == "role":
                sql += " AND t.roles ? %s"
            elif section == "recovery/baselines" and name in {"record_kind", "record_id"}:
                sql += (
                    " AND EXISTS(SELECT 1 FROM bitget_baseline_records br "
                    f"WHERE br.baseline_id=t.id AND br.{name}::text=%s)"
                )
            elif name == "search":
                if section == "signals":
                    sql += (
                        " AND (t.raw_text ILIKE %s OR t.message_id::text ILIKE %s "
                        "OR t.channel_id::text ILIKE %s)"
                    )
                    args.extend(["%" + value.replace("%", "\\%").replace("_", "\\_") + "%"] * 3)
                    continue
                sql += (
                    " AND jsonb_build_object('id',t."
                    + key
                    + "::text,'symbol',to_jsonb(t)->>'symbol','state',to_jsonb(t)->>'state',"
                    "'action',to_jsonb(t)->>'action','client_order_id',"
                    "to_jsonb(t)->>'client_order_id','reason',to_jsonb(t)->>'reason',"
                    "'display_name',to_jsonb(t)->>'display_name','dedup_key',"
                    "to_jsonb(t)->>'dedup_key)::text ILIKE %s"
                )
                value = "%" + value.replace("%", "\\%").replace("_", "\\_") + "%"
            else:
                sql += " AND (" + self._expression(section, name) + ")=%s"
            args.append(value)
        if resource:
            sql += f" AND t.{key}::text=%s"
            args.append(resource)
        revision_sql, revision_args = self._base(section)
        revision = self._query(
            "SELECT count(*) AS record_count,max(scoped.sort_time) AS latest_update FROM ("
            + revision_sql
            + ") scoped",
            tuple(revision_args),
            name=f"{section}:revision",
        )
        binding = _digest(
            {"section": section, "scope": self.scope, "filters": filters, "revision": revision}
        )
        ascending = filters.get("sort") == "oldest"
        if cursor:
            saved = self._decode_cursor(cursor, binding)
            sql += (
                f" AND (t.{stamp},t.{key}::text) {'>' if ascending else '<'} (%s::timestamptz,%s)"
            )
            args.extend([saved["time"], saved["id"]])
        sql += (
            f" ORDER BY t.{stamp} {'ASC' if ascending else 'DESC'},"
            f"t.{key}::text {'ASC' if ascending else 'DESC'} LIMIT %s"
        )
        args.append(limit + 1)
        rows = self._query(sql, tuple(args), section)
        if rows is None:
            return (
                None
                if resource
                else {
                    "items": None,
                    "next_cursor": None,
                    "has_more": False,
                    "total": None,
                    "provider_observations": observations,
                }
            )
        more = len(rows) > limit
        selected = rows[:limit]
        output = [self._record(section, row["record"]) for row in selected]
        if resource:
            if not output:
                raise ReadError("not_found", "Resource not found", 404)
            return self._detail(section, output[0])
        next_cursor = (
            self._encode_cursor(
                {
                    "binding": binding,
                    "time": sanitize(selected[-1]["sort_time"]),
                    "id": selected[-1]["row_id"],
                }
            )
            if more
            else None
        )
        return {
            "items": output,
            "next_cursor": next_cursor,
            "has_more": more,
            "total": None,
            **({"provider_observations": observations} if observations else {}),
            **({"schedule": schedule} if section == "notifications" else {}),
        }

    def _record(self, section: str, record: dict[str, Any]) -> dict[str, Any]:
        result = cast(dict[str, Any], sanitize(record))
        result["resource_version"] = _digest(record)
        result["links"] = [{"section": section, "id": str(record.get("id", ""))}]
        for field, linked_section in {
            "dispatch_id": "dispatches",
            "source_message_id": "signals",
            "balance_snapshot_id": "account/admission-snapshots",
            "margin_reservation_id": "account/reservations",
            "job_id": "jobs",
        }.items():
            if record.get(field):
                result["links"].append({"section": linked_section, "id": str(record[field])})
        if (
            section == "signals"
            and isinstance(record.get("channel_id"), int)
            and isinstance(record.get("message_id"), int)
        ):
            channel = str(record["channel_id"])
            if channel.startswith("-100") and channel[4:].isdigit():
                result["source_message_url"] = (
                    f"https://t.me/c/{channel[4:]}/{record['message_id']}"
                )
        if section == "signals":
            result["media"] = self._media_reference(record)
            result["classification"] = (
                "canonical"
                if record.get("canonical_signals")
                else "management"
                if record.get("management_ids")
                else "unknown"
            )
            result["links"].extend(
                {"section": "dispatches", "id": str(item["id"])}
                for item in record.get("dispatches", [])
            )
            result["links"].extend(
                {"section": "management", "id": str(item)}
                for item in record.get("management_ids", [])
            )
        if section == "notifications":
            lease_until = _time(record.get("lease_until"))
            result["state"] = (
                "sent"
                if record.get("sent_at")
                else "terminal-failed"
                if record.get("failed_at")
                else "leased"
                if lease_until is not None and lease_until > self._now
                else "retry"
                if record.get("attempts", 0)
                else "pending"
            )
            result["trade_outcome"] = "not-inferred-from-notification"
        if section in {"orders", "fills", "account/reservations"}:
            result["ownership"] = record.get("ownership", "unresolved")
            result["account_provenance"] = (
                "legacy-unscoped" if not record.get("account_id") else "recorded"
            )
        return result

    def _detail(self, section: str, record: dict[str, Any]) -> dict[str, Any]:
        identifier = str(record["id"])
        linked: dict[str, Any] = {}
        if section == "signals":
            linked["revisions"] = self._query(
                "SELECT id,revision_hash,received_at,intake_state FROM telegram_messages "
                "WHERE channel_id=%s AND message_id=%s ORDER BY received_at,id",
                (record["channel_id"], record["message_id"]),
                "signal_revisions",
            )
            linked["canonical_signals"] = self._query(
                "SELECT * FROM canonical_signals WHERE message_id=%s::uuid AND revision=%s "
                "ORDER BY created_at,id",
                (identifier, record["revision_hash"]),
                "canonical_signals",
            )
            linked["dispatches"] = self._query(
                "SELECT d.* FROM dispatches d JOIN canonical_signals cs ON cs.id=d.source_id "
                "WHERE cs.message_id=%s::uuid AND cs.revision=%s ORDER BY d.created_at,d.id",
                (identifier, record["revision_hash"]),
                "signal_dispatches",
            )
            linked["management"] = self._query(
                "SELECT * FROM source_management_updates WHERE source_message_id=%s::uuid "
                "AND revision=%s ORDER BY created_at,id",
                (identifier, record["revision_hash"]),
                "signal_management",
            )
            linked["analysis_runs"] = self._query(
                "SELECT * FROM console_analysis_runs WHERE source_message_id=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "signal_analysis_runs",
            )
            linked["annotations"] = self._query(
                "SELECT id,actor_id,arguments,created_at FROM console_jobs "
                "WHERE action='signal.annotate' AND state='succeeded' AND scope=%s::jsonb "
                "AND arguments->>'message_id'=%s AND arguments->>'revision'=%s "
                "ORDER BY created_at,id",
                (json.dumps(self.scope), identifier, record["revision_hash"].strip()),
                "signal_annotations",
            )
            record["classification"] = (
                "canonical"
                if linked["canonical_signals"]
                else "management"
                if linked["management"]
                else "unknown"
            )
        elif section == "dispatches":
            linked["transitions"] = self._query(
                "SELECT * FROM dispatch_transitions WHERE dispatch_id=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "dispatch_transitions",
            )
            linked["intents"] = self._query(
                "SELECT * FROM live_order_intents WHERE dispatch_id=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "dispatch_intents",
            )
            linked["reservations"] = self._query(
                "SELECT * FROM bitget_margin_reservations WHERE dispatch_id=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "dispatch_reservations",
            )
            linked["canonical_signal"] = self._query(
                "SELECT cs.*,tm.channel_id,tm.message_id AS telegram_message_id,tm.received_at "
                "FROM canonical_signals cs JOIN telegram_messages tm ON tm.id=cs.message_id "
                "WHERE cs.id=%s::uuid",
                (record["source_id"],),
                "dispatch_source",
            )
            if record.get("source_type") == "console_manual":
                linked["manual_entry"] = self._query(
                    "SELECT * FROM console_manual_entries WHERE id=%s::uuid "
                    "AND account_id=%s AND environment=%s",
                    (record["source_id"], self.scope["account_id"], self.scope["environment"]),
                    "manual_entry_source",
                )
        elif section in {"orders", "fills"}:
            oid = record["client_order_id"]
            for kind in ("orders", "plan_orders", "fills"):
                observation = self._snapshot(kind)
                linked[f"provider_{kind}"] = (
                    [
                        row
                        for row in observation.get("items", [])
                        if row.get("clientOid") == oid
                        or (
                            record.get("provider_order_id")
                            and row.get("orderId") == record["provider_order_id"]
                        )
                    ]
                    if observation is not None
                    else None
                )
            linked["intent"] = self._query(
                "SELECT * FROM live_order_intents WHERE exchange='bitget' AND client_order_id=%s",
                (oid,),
                "intent",
            )
            linked["fills"] = self._query(
                "SELECT * FROM fills WHERE exchange='bitget' AND client_order_id=%s "
                "ORDER BY filled_at,id",
                (oid,),
                "intent_fills",
            )
            linked["reconciliation"] = self._query(
                "SELECT * FROM intent_reconciliation_audit WHERE exchange='bitget' "
                "AND client_order_id=%s ORDER BY reconciled_at,id",
                (oid,),
                "intent_reconciliation",
            )
            linked["close_bindings"] = self._query(
                "SELECT * FROM bitget_verified_close_bindings WHERE close_client_order_id=%s",
                (oid,),
                "close_bindings",
            )
            linked["entry_reservation"] = self._query(
                "SELECT r.*,b.captured_at AS admission_captured_at,b.total_balance,"
                "b.available_balance,b.equity FROM bitget_margin_reservations r "
                "JOIN balance_snapshots b ON b.id=r.balance_snapshot_id "
                "JOIN live_order_intents i ON i.margin_reservation_id=r.id "
                "WHERE i.exchange='bitget' AND i.client_order_id=%s",
                (oid,),
                "entry_admission",
            )
            linked["baseline"] = self._query(
                "SELECT br.*,b.environment,b.account_id,b.uncertainty_policy "
                "FROM bitget_baseline_records br JOIN bitget_audited_baselines b "
                "ON b.id=br.baseline_id JOIN live_order_intents i ON i.id=br.record_id "
                "WHERE br.record_kind='intent' AND i.exchange='bitget' AND i.client_order_id=%s",
                (oid,),
                "intent_baseline",
            )
            if linked["baseline"]:
                record["ownership"] = "account_baseline"
        elif section == "management":
            linked["provider_intents"] = self._query(
                "SELECT i.* FROM source_management_provider_intents m JOIN live_order_intents "
                "i ON i.exchange='bitget' AND i.client_order_id=m.client_order_id "
                "WHERE m.management_update_id=%s::uuid ORDER BY i.created_at,i.id",
                (identifier,),
                "management_intents",
            )
            linked["cancelled_entries"] = self._query(
                "SELECT * FROM live_order_intents WHERE cancel_requested_by=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "management_cancellation_claims",
            )
        elif section == "jobs":
            linked["steps"] = self._read("jobs/steps", identifier, {}, 200, None)
            linked["approvals"] = self._query(
                "SELECT a.* FROM console_approvals a JOIN console_jobs j ON j.id=a.job_id "
                "WHERE a.job_id=%s AND j.scope=%s::jsonb ORDER BY a.created_at,a.id",
                (identifier, json.dumps(self.scope)),
                "job_approvals",
            )
            linked["audit"] = self._query(
                "SELECT * FROM console_audit_events WHERE job_id=%s AND scope=%s::jsonb "
                "ORDER BY created_at,id",
                (identifier, json.dumps(self.scope)),
                "job_audit",
            )
        elif section == "recovery/baselines":
            linked["retired_records"] = self._query(
                "SELECT * FROM bitget_baseline_records WHERE baseline_id=%s::uuid "
                "ORDER BY record_kind,record_id",
                (identifier,),
                "baseline_records",
            )
            record["verified_closure"] = False
        elif section == "account/reservations":
            linked["admission_snapshot"] = self._query(
                "SELECT b.* FROM balance_snapshots b JOIN bitget_margin_reservations r "
                "ON r.balance_snapshot_id=b.id WHERE r.id=%s::uuid",
                (identifier,),
                "reservation_snapshot",
            )
            linked["intents"] = self._query(
                "SELECT * FROM live_order_intents WHERE margin_reservation_id=%s::uuid "
                "ORDER BY created_at,id",
                (identifier,),
                "reservation_intents",
            )
        record["linked"] = sanitize(linked)
        for name, rows in linked.items():
            if isinstance(rows, list):
                for row in rows:
                    if row.get("id"):
                        record["links"].append(
                            {
                                "section": {
                                    "canonical_signals": "signals",
                                    "canonical_signal": "signals",
                                    "transitions": "dispatches",
                                    "intents": "orders",
                                    "provider_intents": "orders",
                                    "cancelled_entries": "orders",
                                    "reservations": "account/reservations",
                                    "entry_reservation": "account/reservations",
                                }.get(name, name),
                                "id": str(
                                    row.get("message_id")
                                    if name in {"canonical_signals", "canonical_signal"}
                                    else row["id"]
                                ),
                            }
                        )
        return record

    @staticmethod
    def _encode_cursor(value: dict[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    @staticmethod
    def _decode_cursor(cursor: str, binding: str) -> dict[str, Any]:
        try:
            if len(cursor) > 4096:
                raise ValueError
            saved = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if saved["binding"] != binding:
                raise ValueError
            if "offset" not in saved and (
                not isinstance(saved.get("id"), str) or _time(saved.get("time")) is None
            ):
                raise ValueError
            return cast(dict[str, Any], saved)
        except (ValueError, KeyError, TypeError):
            raise ReadError(
                "invalid_cursor", "Cursor does not match this scope, filter or observation"
            ) from None

    def _list_memory(
        self,
        section: str,
        rows: list[dict[str, Any]] | None,
        resource: str | None,
        filters: dict[str, str],
        limit: int,
        cursor: str | None,
        observation: Any,
    ) -> Any:
        if rows is None:
            return (
                None
                if resource
                else {"items": None, "next_cursor": None, "has_more": False, "total": None}
            )
        binding = _digest(
            {"section": section, "scope": self.scope, "filters": filters, "snapshot": observation}
        )
        selected = []
        for row in rows:
            identifier = str(
                row.get("id") or row.get("service_id") or row.get("position_key") or _digest(row)
            )
            value = {
                **row,
                "id": identifier,
                "resource_version": _digest(row),
                "links": [{"section": section, "id": identifier}] + row.get("links", []),
            }
            if resource and identifier != resource:
                continue
            if any(
                str(value.get(k, "")).lower() != v.lower()
                for k, v in filters.items()
                if k not in _COMMON_FILTERS
            ):
                continue
            if (
                filters.get("search")
                and filters["search"].casefold()
                not in json.dumps(sanitize(value), default=str).casefold()
            ):
                continue
            stamp = _time(
                value.get("observed_at") or value.get("updated_at") or value.get("created_at")
            )
            lower = _time(filters.get("from"))
            upper = _time(filters.get("to"))
            if lower is not None and (stamp is None or stamp < lower):
                continue
            if upper is not None and (stamp is None or stamp > upper):
                continue
            selected.append(value)
        selected.sort(
            key=lambda row: (
                str(row.get("observed_at") or row.get("updated_at") or row.get("created_at") or ""),
                row["id"],
            ),
            reverse=filters.get("sort") != "oldest",
        )
        if resource:
            if not selected:
                raise ReadError("not_found", "Resource not found", 404)
            return selected[0]
        offset = 0
        if cursor:
            saved = self._decode_cursor(cursor, binding)
            offset = saved.get("offset", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ReadError("invalid_cursor", "Invalid cursor offset")
        page, more = selected[offset : offset + limit], offset + limit < len(selected)
        complete = (
            isinstance(observation, dict)
            and observation.get("complete") is True
            and not observation.get("last_known")
        )
        return {
            "items": page,
            "next_cursor": self._encode_cursor({"binding": binding, "offset": offset + limit})
            if more
            else None,
            "has_more": more,
            "total": len(selected) if complete else None,
        }

    def _positions(
        self,
        section: str,
        resource: str | None,
        filters: dict[str, str],
        limit: int,
        cursor: str | None,
    ) -> Any:
        provider = self._snapshot("positions")
        plans = self._snapshot("plan_orders")
        orders = self._snapshot("orders")
        stream = self._snapshot("stream")
        proofs = self._snapshot("protection_proofs")
        capabilities = self._query(
            "SELECT * FROM bitget_protection_capabilities WHERE exchange='bitget' "
            "AND environment=%s ORDER BY symbol",
            (self.scope["environment"],),
            "capabilities",
        )
        fallbacks = self._query(
            "SELECT * FROM fallback_protection WHERE exchange='bitget' "
            "AND state IN ('active','closing') AND (environment=%s OR environment IS NULL) "
            "ORDER BY updated_at DESC,id",
            (self.scope["environment"],),
            "fallback_registrations",
        )
        intents = self._query(
            "SELECT i.*,br.baseline_id FROM live_order_intents i "
            "LEFT JOIN bitget_baseline_records br ON br.record_kind='intent' "
            "AND br.record_id=i.id WHERE i.exchange='bitget' AND i.role='ENTRY' "
            "AND EXISTS(SELECT 1 FROM fallback_protection f "
            "WHERE f.position_key=i.client_order_id AND f.exchange=i.exchange "
            "AND f.environment=%s AND f.state IN ('active','closing')) "
            "ORDER BY i.updated_at DESC,i.id",
            (self.scope["environment"],),
            "ownership_intents",
        )
        native_roots = self._query(
            _OWNERSHIP_ROOT_SQL, (self.scope["environment"],), "exact_ownership_roots"
        )
        if native_roots and self._cursor_sql is not None:
            native_roots = add_owned_residuals(
                self.store, native_roots, self._cursor_sql.connection
            )
        rows: list[dict[str, Any]] | None = None
        if provider is not None:
            rows = []
            for raw in provider["items"]:
                if Decimal(str(raw["total"])) == 0:
                    continue
                symbol, side, epoch = (
                    raw.get("symbol"),
                    raw.get("holdSide"),
                    str(raw.get("cTime") or ""),
                )
                owner = native_position_owner(
                    raw,
                    (plans or {}).get("items", []),
                    native_roots or [],
                    self.scope["environment"],
                    self.scope["account_id"],
                ) or exact_position_owner(
                    raw, fallbacks or [], intents or [], self.scope["environment"]
                )
                root = owner["intent"]["client_order_id"] if owner else None
                position_key = (
                    root
                    if root
                    else f"bitget:{self.scope['environment']}:{symbol}:{side}:{epoch}"
                    if epoch
                    else None
                )
                exact = [
                    f
                    for f in fallbacks or []
                    if f.get("environment") == self.scope["environment"]
                    and str(f.get("provider_position_epoch")) == epoch
                    and f.get("symbol") == symbol
                    and str(f.get("direction", "")).lower() == str(side).lower()
                ]
                matches = [
                    p
                    for p in (plans or {}).get("items", [])
                    if p.get("symbol") == symbol
                    and str(p.get("holdSide", p.get("posSide", ""))).lower()
                    in {str(side).lower(), "buy" if side == "long" else "sell"}
                ]
                proof = next(
                    (
                        p
                        for p in (proofs or {}).get("items", [])
                        if p.get("root") == root and p.get("provider_position_epoch") == epoch
                    ),
                    None,
                )
                if (
                    (proofs or {}).get("last_known")
                    or (provider or {}).get("last_known")
                    or (plans or {}).get("last_known")
                ):
                    proof = None
                capability = next(
                    (c for c in capabilities or [] if c.get("symbol") == symbol), None
                )
                row: dict[str, Any] = {
                    "id": position_key or "unresolved:" + _digest(raw),
                    "position_key": position_key,
                    "symbol": symbol,
                    "direction": str(side).upper() if side else None,
                    "provider_position_epoch": epoch or None,
                    "provider": raw,
                    "ownership": "bot" if owner else "unresolved",
                    "ownership_root": root,
                    "owned_residual": owner["quantity"] if owner else None,
                    "ledger_intent": owner["intent"] if owner else None,
                    "native_plans": matches if plans is not None else None,
                    "native_protection_proof": proof,
                    "native_state": capability.get("native_state") if capability else None,
                    "protection_state": proof.get("state") if proof else "unknown",
                    "fallback_state": exact[0].get("state") if len(exact) == 1 else None,
                    "fallback_registrations": exact,
                    "fallback_enforcement": "unknown",
                    "capability": capability,
                    "stream": stream,
                    "stream_state": capability.get("stream_state") if capability else None,
                    "orders_observation": orders,
                    "observed_at": provider.get("observed_at"),
                    "missing_sl": proof.get("missing_sl") if proof else None,
                    "missing_tp": proof.get("missing_tp") if proof else None,
                }
                baseline_intents = [intent for intent in intents or [] if intent.get("baseline_id")]
                baseline_owner = exact_position_owner(
                    raw,
                    fallbacks or [],
                    [{**intent, "baseline_id": None} for intent in baseline_intents],
                    self.scope["environment"],
                )
                if owner is None and baseline_owner is not None:
                    row["ownership"] = "account_baseline"
                    row["baseline_refs"] = [
                        {"intent_id": intent["id"], "baseline_id": intent["baseline_id"]}
                        for intent in baseline_intents
                        if intent["id"] == baseline_owner["intent"]["id"]
                    ]
                    row["verified_closure"] = False
                row["links"] = (
                    [{"section": "orders", "id": str(owner["intent"]["id"])}] if owner else []
                )
                row["state"] = "open"
                row["last_known"] = bool(provider.get("last_known"))
                row["observation_complete"] = bool(provider.get("complete"))
                rows.append(row)
        return self._list_memory(
            section,
            rows,
            resource,
            filters,
            limit,
            cursor,
            {
                "provider": provider,
                "plans": plans,
                "fallbacks": fallbacks,
                "capabilities": capabilities,
                "proofs": proofs,
                "complete": bool(
                    provider and provider.get("complete") and not provider.get("last_known")
                ),
            },
        )

    def _pnl(self, filters: dict[str, str]) -> Any:
        where = "f.exchange='bitget'"
        args: list[Any] = []
        for key in ("symbol", "client_order_id", "fee_ccy"):
            if key in filters:
                where += f" AND f.{key}=%s"
                args.append(filters[key])
        if "intent_id" in filters:
            where += " AND i.id::text=%s"
            args.append(filters["intent_id"])
        if "source" in filters:
            where += (
                " AND coalesce((SELECT e.source FROM provider_reconciliation_events e "
                "WHERE e.exchange=f.exchange AND e.provider_fill_id=f.provider_fill_id),"
                "'ledger')=%s"
            )
            args.append(filters["source"])
        if "ownership" in filters:
            where += (
                " AND (" + self._expression("orders", "ownership").replace("t.id", "i.id") + ")=%s"
            )
            args.append(filters["ownership"])
        if "search" in filters:
            where += " AND (f.symbol ILIKE %s OR f.client_order_id ILIKE %s)"
            args.extend(["%" + filters["search"].replace("%", "\\%").replace("_", "\\_") + "%"] * 2)
        for key in ("from", "to"):
            if key in filters:
                where += (
                    " AND f.filled_at " + (">=" if key == "from" else "<=") + " %s::timestamptz"
                )
                args.append(filters[key])
        rows = self._query(
            "SELECT f.fee_ccy,count(*) AS fill_count,sum(f.realized_pnl) AS gross_realized_pnl,"
            "sum(f.fee) AS fees,min(f.filled_at) AS first_fill_at,"
            "max(f.filled_at) AS last_fill_at FROM fills f JOIN live_order_intents i "
            "ON i.exchange=f.exchange AND i.client_order_id=f.client_order_id WHERE "
            + where
            + " AND NOT EXISTS (SELECT 1 FROM bitget_baseline_records br WHERE "
            "br.record_kind='intent' AND br.record_id=i.id) GROUP BY f.fee_ccy "
            "ORDER BY f.fee_ccy NULLS LAST",
            tuple(args),
            "ledger_pnl",
        )
        excluded = self._query(
            "SELECT br.record_id,b.id AS baseline_id,b.uncertainty_policy,b.historical_before "
            "FROM bitget_baseline_records br JOIN bitget_audited_baselines b "
            "ON b.id=br.baseline_id WHERE br.record_kind='intent' AND b.environment=%s "
            "AND b.account_id=%s ORDER BY b.created_at,br.record_id",
            (self.scope["environment"], self.scope["account_id"]),
            "pnl_baseline_exclusions",
        )
        return {
            "by_fee_currency": sanitize(rows),
            "excluded_history": sanitize(excluded),
            "coverage": "recorded-ledger-fills-excluding-audited-intents",
            "complete_account_history": False,
            "net_pnl": None,
            "note": (
                "Gross realized PnL and fees remain separate; unknown fee conversion and legacy "
                "account provenance prevent an account-wide net claim"
            ),
        }

    def _artifact(self, resource: str | None) -> dict[str, Any]:
        if not resource:
            raise ReadError("invalid_resource", "Report artifact identifier is required")
        rows = self.store.query(
            "SELECT id,result FROM console_jobs WHERE id=%s AND action='report.export' "
            "AND state='succeeded' AND scope=%s::jsonb",
            (resource, json.dumps(self.scope)),
        )
        report = rows[0].get("result") if rows else None
        if isinstance(report, dict) and isinstance(report.get("result"), dict):
            report = report["result"]
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("content"), str)
            or report.get("format") not in {"csv", "json"}
        ):
            raise ReadError("not_found", "Report artifact not found", 404)
        expires = _time(report.get("expires_at"))
        if expires is None or expires <= self._now:
            raise ReadError("artifact_expired", "Report artifact expired", 410)
        content = sanitize(report["content"]).encode("utf-8")
        if len(content) > 10 * 1024 * 1024:
            raise ReadError("artifact_unavailable", "Report artifact exceeds delivery limit", 413)
        return {
            "data": {
                "content": content,
                "mime_type": "text/csv" if report["format"] == "csv" else "application/json",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            "evidence": [_evidence(resource, "ledger", self._now, complete=True)],
            "warnings": [],
            "event_cursor": None,
            "scope": self.scope,
        }

    @staticmethod
    def _media_reference(record: Mapping[str, Any]) -> list[dict[str, Any]]:
        if not record.get("has_media") or not record.get("media_sha256"):
            return []
        opaque = _digest(
            {
                "message": str(record.get("id")),
                "sha256": record["media_sha256"],
                "revision": record.get("revision_hash"),
            }
        )
        return [
            {
                "id": opaque,
                "mime_type": record.get("media_mime_type"),
                "size_bytes": record.get("media_size_bytes"),
            }
        ]

    def _media(self, resource: str | None, filters: dict[str, str]) -> dict[str, Any]:
        if not resource or not filters.get("message_id"):
            raise ReadError("invalid_resource", "Message and opaque media identifiers are required")
        rows = self.store.query(
            "SELECT id,revision_hash,has_media,media_path,media_sha256,media_mime_type,"
            "media_size_bytes FROM telegram_messages WHERE id::text=%s",
            (filters["message_id"],),
        )
        record = rows[0] if rows else None
        reference = self._media_reference(record) if record else []
        if not reference or reference[0]["id"] != resource:
            raise ReadError("not_found", "Media not found", 404)
        record = cast(dict[str, Any], record)
        allowed = {
            "image/png": b"\x89PNG\r\n\x1a\n",
            "image/jpeg": b"\xff\xd8\xff",
            "image/webp": b"RIFF",
        }
        mime = record.get("media_mime_type")
        size = record.get("media_size_bytes")
        if mime not in allowed or not isinstance(size, int) or not 0 < size <= 20 * 1024 * 1024:
            raise ReadError("unsafe_media", "Media format or size is not permitted", 415)
        mime = cast(str, mime)
        try:
            root = self.media_root.resolve(strict=True)
            file = Path(record["media_path"])
            file = (file if file.is_absolute() else root / file).resolve(strict=True)
            file.relative_to(root)
            if not file.is_file() or file.stat().st_size != size:
                raise ValueError
            with file.open("rb") as stream:
                content = stream.read(size + 1)
            if (
                len(content) != size
                or hashlib.sha256(content).hexdigest() != record["media_sha256"]
                or not content.startswith(allowed[mime])
                or (mime == "image/webp" and content[8:12] != b"WEBP")
            ):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise ReadError("media_unavailable", "Media evidence is unavailable", 404) from None
        return {
            "data": {"content": content, "mime_type": mime, "size_bytes": size},
            "evidence": [_evidence(resource, "ledger", self._now, complete=True)],
            "warnings": [],
            "event_cursor": None,
            "scope": self.scope,
        }


_PROVIDER_FIELDS = {
    "positions": {
        "symbol",
        "holdSide",
        "total",
        "available",
        "locked",
        "averageOpenPrice",
        "openPriceAvg",
        "markPrice",
        "liquidationPrice",
        "leverage",
        "marginMode",
        "marginCoin",
        "unrealizedPL",
        "cTime",
        "uTime",
        "posMode",
        "achievedProfits",
        "keepMarginRate",
        "marginSize",
        "stopLoss",
        "takeProfit",
        "stopLossId",
        "takeProfitId",
        "breakEvenPrice",
    },
    "orders": {
        "symbol",
        "orderId",
        "clientOid",
        "side",
        "tradeSide",
        "posSide",
        "holdSide",
        "size",
        "baseVolume",
        "price",
        "priceAvg",
        "status",
        "orderType",
        "reduceOnly",
        "cTime",
        "uTime",
        "marginMode",
        "marginCoin",
        "presetStopSurplusPrice",
        "presetStopLossPrice",
    },
    "plan_orders": {
        "symbol",
        "orderId",
        "planOrderId",
        "id",
        "clientOid",
        "clientOrderId",
        "holdSide",
        "posSide",
        "side",
        "planType",
        "type",
        "triggerPrice",
        "triggerType",
        "size",
        "status",
        "planStatus",
        "cTime",
        "uTime",
        "stopLossTriggerPrice",
        "stopSurplusTriggerPrice",
        "executePrice",
        "stopLossClientOid",
        "stopSurplusClientOid",
        "stopLossOrderId",
        "stopSurplusOrderId",
        "stopLossSize",
        "stopSurplusSize",
        "stopLossTriggerType",
        "stopSurplusTriggerType",
        "stopLossExecutePrice",
        "stopSurplusExecutePrice",
    },
    "fills": {
        "symbol",
        "orderId",
        "clientOid",
        "tradeId",
        "fillId",
        "side",
        "tradeSide",
        "price",
        "size",
        "baseVolume",
        "quoteVolume",
        "profit",
        "feeDetail",
        "fillAmount",
        "cTime",
        "uTime",
    },
    "contracts": {
        "symbol",
        "baseCoin",
        "quoteCoin",
        "buyLimitPriceRatio",
        "sellLimitPriceRatio",
        "feeRateUpRatio",
        "makerFeeRate",
        "takerFeeRate",
        "openCostUpRatio",
        "supportMarginCoins",
        "minTradeNum",
        "priceEndStep",
        "volumePlace",
        "pricePlace",
        "sizeMultiplier",
        "symbolType",
        "minTradeUSDT",
        "maxSymbolOrderNum",
        "maxProductOrderNum",
        "maxPositionNum",
        "maxOrderQty",
        "maxMarketOrderQty",
        "contractValue",
        "contractSize",
        "symbolStatus",
        "maxLever",
        "minLever",
    },
}


def provider_rows(payload: Any, kind: str) -> tuple[list[dict[str, Any]], bool]:
    """Validate containers before counting; paginated fills are never full history."""
    if isinstance(payload, dict) and "code" in payload:
        if str(payload["code"]) != "00000":
            raise ValueError("provider rejected read")
        payload = payload.get("data")
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    complete = kind != "fills"
    if isinstance(payload, dict):
        keys = {
            "orders": ("entrustedList", "orderList"),
            "plan_orders": ("entrustedList", "orderList"),
            "fills": ("fillList", "list"),
            "positions": ("positionList", "list"),
            "contracts": ("list",),
        }[kind]
        selected = next((payload[key] for key in keys if key in payload), None)
        if selected is None:
            raise ValueError("missing provider collection")
        if payload.get("endId") or payload.get("nextCursor") or payload.get("hasMore"):
            complete = False
        declared_total = payload.get("total", payload.get("totalCount"))
        if declared_total is not None:
            if isinstance(declared_total, bool) or not str(declared_total).isdigit():
                raise ValueError("invalid provider collection total")
            if isinstance(selected, list) and int(declared_total) > len(selected):
                complete = False
        payload = selected
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("invalid provider collection")
    for row in payload:
        if kind in {"positions", "contracts"} and (
            not isinstance(row.get("symbol"), str)
            or not re.fullmatch(r"[A-Z0-9]{3,30}", row["symbol"])
        ):
            raise ValueError("provider symbol missing")
        if kind == "positions":
            quantity = Decimal(str(row.get("total")))
            if (
                not quantity.is_finite()
                or quantity < 0
                or row.get("holdSide") not in {"long", "short"}
            ):
                raise ValueError("invalid provider position")
    return [
        {key: sanitize(value) for key, value in row.items() if key in _PROVIDER_FIELDS[kind]}
        for row in payload
    ], complete


_EFFECTIVE_KEYS = (
    "TRADER_MODE",
    "BITGET_MODE",
    "BITGET_EXECUTION_ENABLED",
    "FATTY_PRODUCTION_LIVE_ONLY",
    "BITGET_OPERATOR_MUTATIONS_ENABLED",
    "SOURCE_MANAGEMENT_MUTATIONS_ENABLED",
    "BITGET_FALLBACK_MUTATIONS_ENABLED",
    "BITGET_PROTECTION_STREAM_ENABLED",
    "BITGET_PROTECTION_STREAM_MODE",
    "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED",
    "BITGET_APPROVAL_REFERENCE",
    "BITGET_CANARY_MAX_ORDERS",
    "BITGET_CANARY_SYMBOL",
    "BITGET_MIN_LEVERAGE",
    "BITGET_MAX_LEVERAGE",
    "BITGET_ALLOCATION_PCT",
    "BITGET_MAX_MARGIN_PER_TRADE_USDT",
    "BITGET_MAX_NORMAL_POSITIONS",
    "BITGET_LIQUIDATION_BUFFER",
    "BITGET_BALANCE_MAX_AGE_SECONDS",
    "BITGET_BALANCE_RESERVATION_TTL_SECONDS",
    "BITGET_BALANCE_MAX_FUTURE_SKEW_SECONDS",
    "BITGET_MAX_CLOCK_SKEW_MS",
)


class SnapshotCollector:
    """Worker-owned observations: no preflight, margin-mode or leverage writes."""

    def __init__(self, environ: Mapping[str, str], store: Any, client: Any = None) -> None:
        self.environ, self.store, self.client = environ, store, client
        self.scope = configured_scope(environ)
        self._tier_offset = 0

    async def _observe(self, kind: str, operation: Any) -> Any:
        try:
            data = await asyncio.wait_for(operation(), timeout=20)
            observed = datetime.now(UTC)
            normalized: dict[str, Any]
            if kind in _PROVIDER_FIELDS:
                rows, complete = provider_rows(data, kind)
                normalized = {
                    "items": rows,
                    "complete": complete,
                    "observed_at": observed.isoformat(),
                }
            elif kind == "account":
                if isinstance(data, dict) and "code" in data and str(data["code"]) != "00000":
                    raise ValueError("provider rejected account read")
                data = data.get("data", data) if isinstance(data, dict) else data
                if not isinstance(data, dict):
                    raise ValueError("invalid account read")
                account = normalize_account_snapshot(
                    data, environment=self.scope["environment"], observed_at=observed
                )
                normalized = {
                    "values": account,
                    "complete": all(
                        account[key] is not None for key in ("equity", "available", "unrealized_pl")
                    ),
                    "observed_at": observed.isoformat(),
                }
            elif kind == "account_identity":
                data = data.get("data", data) if isinstance(data, dict) else data
                uid = data.get("userId") if isinstance(data, dict) else None
                if (
                    not isinstance(uid, str)
                    or not uid.isascii()
                    or not uid.isdecimal()
                    or int(uid) <= 0
                ):
                    raise ValueError("invalid authenticated account identity")
                if (
                    self.scope["account_id"] != "configured-bitget"
                    and uid != self.scope["account_id"]
                ):
                    raise ValueError("authenticated account scope mismatch")
                normalized = {
                    "values": {"provider_account_id": uid},
                    "complete": True,
                    "observed_at": observed.isoformat(),
                }
            elif kind == "clock":
                if isinstance(data, bool) or not isinstance(data, int):
                    raise ValueError("invalid clock read")
                normalized = {
                    "values": {"local_minus_server_ms": data},
                    "complete": True,
                    "observed_at": observed.isoformat(),
                }
            else:
                raise ValueError("invalid observation kind")
            self.store.put_snapshot(kind, self.scope, sanitize(normalized), observed_at=observed)
            return normalized
        except Exception:
            self.store.put_snapshot(
                kind, self.scope, None, error="provider_read_unavailable", observed_at=None
            )
            return None

    async def collect_once(self) -> None:
        self.store.put_snapshot(
            "effective_config",
            self.scope,
            {
                "values": {key: self.environ.get(key) for key in _EFFECTIVE_KEYS},
                "worker": "console-worker",
                "orders_enabled": None,
                "complete": False,
            },
            observed_at=datetime.now(UTC),
        )
        # Stream is a separately attributed durable worker observation, not REST
        # success or the collector's own configuration.
        try:
            rows = self.store.query(
                "SELECT symbol,environment,stream_state,last_stream_at,updated_at "
                "FROM bitget_protection_capabilities WHERE exchange='bitget' "
                "AND environment=%s ORDER BY symbol",
                (self.scope["environment"],),
            )
            times = [_time(row.get("last_stream_at")) for row in rows]
            observed: datetime | None = max(
                (time for time in times if time is not None), default=None
            )
            self.store.put_snapshot(
                "stream",
                self.scope,
                {
                    "items": sanitize(rows),
                    "complete": False,
                    "source": (
                        "persisted-protection-worker-capability; not live socket attestation"
                    ),
                },
                error=None if observed else "stream_observation_unavailable",
                observed_at=observed,
            )
        except Exception:
            self.store.put_snapshot(
                "stream",
                self.scope,
                None,
                error="stream_observation_unavailable",
                observed_at=None,
            )
        own = self.client is None
        client = self.client
        if own:
            credentials = [
                self.environ.get(key, "").strip()
                for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")
            ]
            if not all(credentials):
                for kind in (*_PROVIDER_FIELDS, "account", "account_identity", "clock"):
                    self.store.put_snapshot(
                        kind,
                        self.scope,
                        None,
                        error="provider_credentials_unavailable",
                        observed_at=None,
                    )
                return
            client = BitgetRestClient(
                credentials[0], credentials[1], credentials[2], mode=self.scope["environment"]
            )
        try:

            async def identity() -> Any:
                return await client._get("/api/v2/spot/account/info")

            identity_snapshot = await self._observe("account_identity", identity)
            if identity_snapshot is None:
                for kind in (*_PROVIDER_FIELDS, "account", "clock", "protection_proofs"):
                    self.store.put_snapshot(
                        kind,
                        self.scope,
                        None,
                        error="authenticated_account_binding_unavailable",
                        observed_at=None,
                    )
                return
            positions, ordinary, contracts, _, _, _ = await asyncio.gather(
                self._observe("positions", client.get_all_positions),
                self._observe("orders", client.get_pending_orders),
                self._observe("contracts", client.get_contracts),
                self._observe("fills", client.get_fills),
                self._observe("account", client.get_account),
                self._observe("clock", client.get_clock_skew_ms),
            )
            # Plans are per-symbol. Include all authenticated open symbols and
            # exact durable intent symbols. Contract inventory alone is metadata,
            # not evidence that protection plans were read for that symbol.
            symbols = {
                row["symbol"] for row in (positions or {}).get("items", []) if row.get("symbol")
            }
            symbols.update(
                row["symbol"] for row in (ordinary or {}).get("items", []) if row.get("symbol")
            )
            try:
                rows = self.store.query(
                    "SELECT DISTINCT symbol FROM live_order_intents WHERE exchange='bitget' "
                    "AND state IN ('staged','requested','acknowledged','submitted',"
                    "'partially_filled','unknown') UNION SELECT DISTINCT symbol "
                    "FROM fallback_protection WHERE exchange='bitget' AND environment=%s "
                    "AND state IN ('active','closing')",
                    (self.scope["environment"],),
                )
                symbols.update(row["symbol"] for row in rows if row.get("symbol"))
            except Exception:
                rows = None
            plans, errors = [], []
            for symbol in sorted(symbols):
                if not re.fullmatch(r"[A-Z0-9]{3,30}", symbol):
                    errors.append("invalid_symbol")
                    continue
                try:
                    payload = await asyncio.wait_for(
                        client.get_pending_plan_orders(symbol), timeout=20
                    )
                    normalized, complete = provider_rows(payload, "plan_orders")
                    for row in normalized:
                        row.setdefault("symbol", symbol)
                    plans.extend(normalized)
                    if not complete:
                        errors.append("partial_plan_inventory")
                except Exception:
                    errors.append("plan_read_unavailable")
            complete = positions is not None and rows is not None and not errors
            self.store.put_snapshot(
                "plan_orders",
                self.scope,
                {
                    "items": plans,
                    "symbols": sorted(symbols),
                    "complete": False,
                    "scoped_symbols_complete": complete,
                    "coverage": (
                        "observed-open-position-and-owned-active-entry-symbols; "
                        "not account-wide-plan-inventory"
                    ),
                    "errors": errors,
                    "observed_at": datetime.now(UTC).isoformat(),
                },
                error="plan_inventory_unavailable" if positions is None and not plans else None,
                observed_at=datetime.now(UTC) if positions is not None or plans else None,
            )
            await self._collect_market(client, contracts, symbols)
            await self._collect_protection(positions, plans, complete)
        finally:
            if own:
                await client.aclose()

    async def _collect_market(self, client: Any, contracts: Any, priority: set[str]) -> None:
        listed = sorted(
            {
                row["symbol"]
                for row in (contracts or {}).get("items", [])
                if re.fullmatch(r"[A-Z0-9]{3,30}", str(row.get("symbol", "")))
            }
        )
        watched = {
            symbol.strip().upper()
            for symbol in self.environ.get("CONSOLE_WATCH_SYMBOLS", "").split(",")
            if symbol.strip()
        }
        batch = set(listed[self._tier_offset : self._tier_offset + 20]) | priority | watched
        self._tier_offset = (self._tier_offset + 20) % max(1, len(listed))
        semaphore = asyncio.Semaphore(4)

        async def collect_symbol(symbol: str) -> None:
            if not re.fullmatch(r"[A-Z0-9]{3,30}", symbol):
                return
            async with semaphore:
                for name, operation in (
                    ("account_admission", client.get_account),
                    ("ticker", client.get_ticker),
                    ("position_lever", client.get_position_lever),
                ):
                    try:
                        payload = await asyncio.wait_for(operation(symbol), timeout=20)
                        if name == "account_admission":
                            payload = (
                                payload.get("data", payload)
                                if isinstance(payload, dict)
                                else payload
                            )
                            if not isinstance(payload, dict):
                                raise ValueError
                            payload = {
                                key: value
                                for key, value in payload.items()
                                if key
                                in {
                                    "marginCoin",
                                    "available",
                                    "accountEquity",
                                    "usdtEquity",
                                    "marginMode",
                                    "posMode",
                                    "isolatedLongLever",
                                    "isolatedShortLever",
                                    "crossedMaxAvailable",
                                    "isolatedMaxAvailable",
                                    "unrealizedPL",
                                }
                            }
                            data: dict[str, Any] = {
                                "values": payload,
                                "complete": all(
                                    payload.get(key) is not None
                                    for key in ("available", "marginCoin", "marginMode", "posMode")
                                ),
                            }
                        elif name == "ticker":
                            if not isinstance(payload, dict):
                                raise ValueError
                            payload = {
                                key: value
                                for key, value in payload.items()
                                if key
                                in {
                                    "symbol",
                                    "lastPr",
                                    "markPrice",
                                    "indexPrice",
                                    "bidPr",
                                    "askPr",
                                    "ts",
                                }
                            }
                            if not payload.get("markPrice") and not payload.get("lastPr"):
                                raise ValueError
                            data = {"values": payload, "complete": True}
                        else:
                            if (
                                not isinstance(payload, list)
                                or not payload
                                or any(not isinstance(row, dict) for row in payload)
                            ):
                                raise ValueError
                            data = {
                                "items": [
                                    {
                                        key: value
                                        for key, value in row.items()
                                        if key
                                        in {
                                            "symbol",
                                            "level",
                                            "startUnit",
                                            "endUnit",
                                            "leverage",
                                            "keepMarginRate",
                                        }
                                    }
                                    for row in payload
                                ],
                                "complete": True,
                            }
                        self.store.put_snapshot(
                            f"{name}:{symbol}",
                            self.scope,
                            sanitize(data),
                            observed_at=datetime.now(UTC),
                        )
                    except Exception:
                        self.store.put_snapshot(
                            f"{name}:{symbol}",
                            self.scope,
                            None,
                            error="market_observation_unavailable",
                            observed_at=None,
                        )

        await asyncio.gather(*(collect_symbol(symbol) for symbol in sorted(batch)))

    async def _collect_protection(
        self, positions: Any, plans: list[dict[str, Any]], plan_complete: bool
    ) -> None:
        try:
            registrations = self.store.query(
                "SELECT * FROM fallback_protection WHERE exchange='bitget' "
                "AND environment=%s AND state IN ('active','closing')",
                (self.scope["environment"],),
            )
            intents = self.store.query(
                "SELECT i.*,br.baseline_id FROM live_order_intents i "
                "LEFT JOIN bitget_baseline_records br ON br.record_kind='intent' "
                "AND br.record_id=i.id WHERE i.exchange='bitget' AND i.role='ENTRY'"
            )
            native_roots = add_owned_residuals(
                self.store, self.store.query(_OWNERSHIP_ROOT_SQL, (self.scope["environment"],))
            )
            proofs = []
            for position in (positions or {}).get("items", []):
                owner = native_position_owner(
                    position,
                    plans,
                    native_roots,
                    self.scope["environment"],
                    self.scope["account_id"],
                ) or exact_position_owner(
                    position, registrations, intents, self.scope["environment"]
                )
                if owner is None:
                    continue
                intent = owner["intent"]
                root = intent["client_order_id"]
                epoch = str(position.get("cTime", ""))
                if re.fullmatch(r"[1-9][0-9]*", epoch):
                    self.store.execute(
                        "INSERT INTO console_position_epochs(root_client_order_id,account_id,"
                        "environment,provider_position_epoch,observed_at) "
                        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(root_client_order_id) DO NOTHING",
                        (
                            root,
                            self.scope["account_id"],
                            self.scope["environment"],
                            epoch,
                            _time(positions.get("observed_at")),
                        ),
                    )
                    binding = self.store.one(
                        "SELECT account_id,environment,provider_position_epoch "
                        "FROM console_position_epochs WHERE root_client_order_id=%s",
                        (root,),
                    )
                    if (
                        not binding
                        or binding["account_id"] != self.scope["account_id"]
                        or binding["environment"] != self.scope["environment"]
                        or str(binding["provider_position_epoch"]) != epoch
                    ):
                        continue
                targets = intent.get("planned_take_profits") or []
                if isinstance(targets, str):
                    targets = json.loads(targets)
                if intent.get("active_take_profit_price"):
                    targets = [intent["active_take_profit_price"]]
                stop = intent.get("active_stop_loss_price") or intent.get("planned_stop_loss")
                if (
                    not stop
                    or not targets
                    or not intent.get("provider_order_id")
                    or not intent.get("provider_fill_ids")
                    or any(
                        str(fill).startswith("status-derived:")
                        for fill in intent["provider_fill_ids"]
                    )
                ):
                    continue

                async def read_position(position: Any = position) -> Any:
                    return [position]

                async def read_plans(position: Any = position) -> Any:
                    if not plan_complete:
                        raise ValueError("incomplete plan inventory")
                    return [plan for plan in plans if plan.get("symbol") == position["symbol"]]

                protection_root = intent.get("protection_root_oid") or root
                expectation = NativeProtectionExpectation(
                    symbol=position["symbol"],
                    hold_side="buy" if position["holdSide"] == "long" else "sell",
                    quantity=Decimal(owner["quantity"]),
                    stop_loss=Decimal(str(stop)),
                    take_profit=Decimal(str(targets[0])),
                    stop_loss_client_oid=(intent.get("stop_mutation_root_oid") or protection_root)
                    + "-sl",
                    take_profit_client_oid=protection_root + "-tp",
                )
                report = await confirm_native_protection(
                    read_position, read_plans, expectation=expectation
                )
                verified = str(report.state) == "VENUE_PROTECTED"
                proofs.append(
                    {
                        "root": root,
                        "provider_position_epoch": str(position["cTime"]),
                        "state": str(report.state),
                        "reason": report.reason,
                        "observed_quantity": str(report.observed_quantity)
                        if report.observed_quantity is not None
                        else None,
                        "missing_sl": False
                        if verified
                        else True
                        if report.reason == "missing-stop-loss"
                        else None,
                        "missing_tp": False
                        if verified
                        else True
                        if report.reason == "missing-take-profit"
                        else None,
                        "observed_at": positions.get("observed_at"),
                        "source": "strict-native-protection-readback",
                    }
                )
            self.store.put_snapshot(
                "protection_proofs",
                self.scope,
                {"items": proofs, "complete": positions is not None and plan_complete},
                error=None if positions is not None else "protection_observation_unavailable",
                observed_at=datetime.now(UTC) if positions is not None else None,
            )
        except Exception:
            self.store.put_snapshot(
                "protection_proofs",
                self.scope,
                None,
                error="protection_observation_unavailable",
                observed_at=None,
            )
