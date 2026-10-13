#!/usr/bin/env python3
# ruff: noqa: E501
"""Periodic health report for Fatty Bitget.

Immutable six-hourly Telegram HTML snapshots, with host-only service/Codex probes.
Core account/ledger meanings and rendering are shared with on-demand /health.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc  # noqa: UP017
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The renderer and core ledger loader are shared; host-only probes are not.
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from fatty_trader.operator.health import (  # noqa: E402
    ACCOUNT_SOURCE,
    load_health_ledger,
    load_health_protection,
)
from fatty_trader.operator.health_report_format import (  # noqa: E402
    fmt_price,
    format_report,
)

# ── Read-only Docker helpers ────────────────────────────────────────────


def query_all(sql: str) -> list[tuple[Any, ...]]:
    # JSON preserves newlines, pipes, nulls and numeric types in source messages/rows.
    sql = sql.strip().rstrip(";")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"SELECT coalesce(json_agg(r), '[]'::json) FROM ({sql}) r",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("ledger read failed")
    rows = json.loads(result.stdout, parse_float=Decimal)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid ledger response")
    return [tuple(row.values()) for row in rows]


def query_one(sql: str) -> tuple[Any, ...] | None:
    rows = query_all(sql)
    return rows[0] if rows else None


def docker_exec_bitget(cmd: str) -> str:
    """Run a command inside the dispatcher-bitget container."""
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "dispatcher-bitget", "sh", "-lc", cmd],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


# ── Runtime ──────────────────────────────────────────────────────────────


def get_runtime_modes() -> dict:
    raw = docker_exec_bitget(
        'printf "%s|%s|%s" "$TRADER_MODE" "$BITGET_MODE" "$BITGET_EXECUTION_ENABLED"'
    )
    parts = [value.strip() or "UNKNOWN" for value in raw.split("|")] if raw else ["UNKNOWN"] * 3
    return {
        "mode": parts[0] if len(parts) > 0 else "UNKNOWN",
        "venue_mode": parts[1] if len(parts) > 1 else "UNKNOWN",
        "execution_enabled": parts[2] if len(parts) > 2 else "UNKNOWN",
    }


def get_account() -> dict:
    try:
        raw = docker_exec_bitget(
            "cd /app && . .venv/bin/activate && python3 scripts/_probe_account.py"
        )
        account = json.loads(raw)
        if isinstance(account, dict) and account.get("read_status") == "OK":
            return account
    except (OSError, TypeError, ValueError):
        pass
    return {
        "read_status": "UNKNOWN",
        "error": "provider account read failed",
        "source": ACCOUNT_SOURCE,
        "observed_at": None,
        "product_type": "USDT-FUTURES",
        "margin_coin": "USDT",
    }


def load_provider_state() -> dict[str, Any] | None:
    """Read provider positions/orders once; None means provider state is unknown."""
    raw = docker_exec_bitget(
        "cd /app && . .venv/bin/activate && python3 scripts/_probe_open_state.py"
    )
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    for name in ("positions", "open_orders"):
        if not isinstance(state.get(name), list):
            state[name] = None
    return state


def _provider_quantity(row: dict[str, Any]) -> Decimal | None:
    for key in ("total", "size", "quantity"):
        if key not in row:
            continue
        try:
            quantity = Decimal(str(row[key]))
            return quantity if quantity.is_finite() and quantity >= 0 else None
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


# ── Data ─────────────────────────────────────────────────────────────────

_NOT_LOADED = object()


def load_positions(provider_state: Any = _NOT_LOADED) -> list[dict] | None:
    """Build the open-position view from Bitget, enriching it with local metadata."""
    state = load_provider_state() if provider_state is _NOT_LOADED else provider_state
    if state is None or not isinstance(state.get("positions"), list):
        return None
    provider_rows = state["positions"]
    try:
        local_rows = query_all(
            "SELECT p.exchange, p.symbol, p.direction, p.quantity::text, "
            "p.protection_state, p.opened_at FROM positions p "
            "WHERE p.exchange='bitget' AND p.closed_at IS NULL ORDER BY p.opened_at DESC"
        )
    except Exception:
        local_rows = []
    local_by_symbol = {row[1].upper(): row for row in local_rows if len(row) > 1}
    positions: list[dict] = []
    for row in provider_rows:
        if not isinstance(row, dict):
            return None
        quantity = _provider_quantity(row)
        if quantity is None:
            return None
        if quantity == 0:
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or str(row.get("holdSide", "")).lower() not in {"short", "long"}:
            return None
        local = local_by_symbol.get(symbol)
        positions.append(
            {
                "exchange": "bitget",
                "symbol": symbol,
                "direction": "SHORT" if str(row.get("holdSide", "")).lower() == "short" else "LONG",
                "qty": str(row.get("total", row.get("size", quantity))),
                "protection_state": local[4] if local and len(local) > 4 else "provider",
                "opened_at": local[5] if local and len(local) > 5 else row.get("cTime"),
                "current_price": fmt_price(row.get("markPrice")),
                "entry_price": fmt_price(row.get("openPriceAvg")),
                "mark_price": fmt_price(row.get("markPrice")),
                "unrealized_pl": row.get("unrealizedPL", "N/A"),
                # Bitget's position margin is the amount actually committed by
                # the open position; account.available alone does not expose it.
                "margin_used": row.get("marginSize", row.get("margin", "N/A")),
                "leverage": str(row.get("leverage", "?")),
                "margin_mode": str(row.get("marginMode", "N/A")).lower(),
                "liquidation_price": fmt_price(row.get("liquidationPrice")),
            }
        )
    return positions


def load_pending_orders(provider_state: Any = _NOT_LOADED) -> list[dict] | None:
    """Return provider pending orders; None means the provider read failed."""
    state = load_provider_state() if provider_state is _NOT_LOADED else provider_state
    if state is None or not isinstance(state.get("open_orders"), list):
        return None
    orders = state["open_orders"]
    for row in orders:
        if (
            not isinstance(row, dict)
            or not row.get("symbol")
            or str(row.get("side", "")).upper() not in {"BUY", "SELL"}
            or _provider_quantity(row) is None
        ):
            return None
    return [
        {
            "exchange": "bitget",
            "symbol": str(row.get("symbol", "?")),
            "side": str(row.get("side", "?")).upper(),
            "role": str(row.get("role", row.get("orderType", "ORDER"))).upper(),
            "qty": str(row.get("size", row.get("quantity", "?"))),
            "price": str(row.get("price", row.get("executePrice", "N/A"))),
            "state": str(row.get("status", row.get("state", "?"))).upper(),
        }
        for row in orders
    ]


def load_sltp_status(
    provider_state: dict[str, Any] | None,
    fallback_rows: Any,
) -> dict[str, dict[str, Any]]:
    """Use shared registration semantics, without claiming fallback enforcement."""
    positions = [
        {
            "symbol": row["symbol"],
            "stop_loss": row.get("stopLossTriggerPrice")
            or row.get("presetStopLossPrice")
            or row.get("stopLoss")
            or row.get("stopLossPrice"),
            "take_profit": row.get("stopSurplusTriggerPrice")
            or row.get("presetStopSurplusPrice")
            or row.get("takeProfit")
            or row.get("takeProfitPrice"),
            "stop_loss_id": row.get("stopLossId"),
            "take_profit_id": row.get("takeProfitId"),
        }
        for row in ((provider_state or {}).get("positions") or [])
        if isinstance(row, dict)
        and row.get("symbol")
        and (_provider_quantity(row) or Decimal("0")) > 0
    ]
    return load_health_protection(positions, fallback_rows)


def get_service_status() -> dict[str, int | str]:
    """Read Compose service state without changing containers."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "ps",
            "--all",
            "--format",
            "{{.Service}}|{{.State}}|{{.Health}}",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        return {"status": "UNKNOWN", "total": 0, "running": 0, "healthy": 0}
    config = subprocess.run(
        [
            "docker",
            "compose",
            "config",
            "--no-interpolate",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    try:
        if config.returncode != 0:
            raise ValueError("Compose config unavailable")
        configured = json.loads(config.stdout)["services"]
        # One-shot setup jobs and disabled lanes are not LIVE operational health.
        excluded = {"init", "migrate", "paper-kaka", "dispatcher-binance", "monitor-binance"}
        expected = {
            name
            for name, service in configured.items()
            if name not in excluded
            and not name.startswith("paper-")
            and "demo" not in name.lower()
            and not service.get("profiles")
        }
    except (ValueError, KeyError, TypeError):
        return {"status": "UNKNOWN", "total": 0, "running": 0, "healthy": 0}
    rows = [line.split("|", 2) for line in result.stdout.splitlines() if line]
    rows = [
        row
        for row in rows
        if row[0] in expected
        or (row[0] not in {"init", "migrate"} and len(row) > 1 and row[1].lower() == "running")
    ]
    unexpected = sum(row[0] not in expected for row in rows)
    present = {row[0] for row in rows}
    rows.extend([name, "missing", ""] for name in sorted(expected - present))
    running = sum(len(row) > 1 and row[1].lower() == "running" for row in rows)
    healthy = sum(
        len(row) > 2 and row[1].lower() == "running" and row[2].lower() == "healthy" for row in rows
    )
    starting = sum(len(row) > 2 and row[2].lower() == "starting" for row in rows)
    unhealthy = sum(
        len(row) < 2
        or row[1].lower() != "running"
        or len(row) < 3
        or row[2].lower() not in {"healthy", "starting"}
        for row in rows
    )
    dispatcher_states = [row[1].upper() for row in rows if row[0] == "dispatcher-bitget"]
    dispatcher_state = ", ".join(dispatcher_states) or "UNKNOWN"
    lifecycle = "UNKNOWN"
    if "RESTARTING" in dispatcher_states:
        # Only retain a fixed startup-failure marker, never arbitrary log payloads.
        # Bound the read to this incident window: historical failures do not prove
        # that an otherwise running worker is currently blocked.
        try:
            logs = subprocess.run(
                ["docker", "compose", "logs", "--no-color", "--tail", "50", "dispatcher-bitget"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                timeout=10,
            )
            if logs.returncode == 0 and "Bitget lifecycle recovery is not ready" in logs.stdout:
                lifecycle = "BLOCKED"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "status": "OK",
        "dispatcher_state": dispatcher_state,
        "lifecycle_recovery": lifecycle,
        "total": len(rows),
        "running": running,
        "healthy": healthy,
        "starting": starting,
        "unhealthy": unhealthy,
        "unexpected_runtime": unexpected,
    }


# ── Helpers ──────────────────────────────────────────────────────────────


# ── Codex Usage ──────────────────────────────────────────────────────────


def get_codex_usage() -> dict:
    """Fetch the host Codex quota; authentication failure is independent of Bitget."""
    cache_path = Path.home() / ".cache" / "fatty" / "codex_usage.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    def fmt_reset(seconds):
        if seconds is None:
            return "N/A"
        seconds = max(0, int(seconds))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def auth_candidates():
        for path in (
            Path.home() / ".pi" / "agent" / "auth.json",
            Path.home() / ".codex" / "auth.json",
        ):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            def walk(value):
                if isinstance(value, dict):
                    token = value.get("access_token") or value.get("accessToken")
                    account = value.get("account_id") or value.get("accountId")
                    if isinstance(token, str) and token and not token.startswith("sk-"):
                        yield token, account if isinstance(account, str) else None
                    for child in value.values():
                        yield from walk(child)

            yield from walk(data)

    try:
        token, account_id = next(auth_candidates())
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if account_id:
            headers["chatgpt-account-id"] = account_id
        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.load(response)
        rate = data["rate_limit"]
        primary = rate["primary_window"]
        secondary = rate["secondary_window"]
        now = datetime.now(UTC).astimezone(timezone(timedelta(hours=7))).strftime("%m-%d %H:%M WIB")
        plan = str(data.get("plan_type") or "N/A")
        fresh = {
            "5h": f"{primary['used_percent']}% used / {100 - primary['used_percent']}% left",
            "7d": f"{secondary['used_percent']}% used / {100 - secondary['used_percent']}% left",
            "reset": (
                f"5h {fmt_reset(primary.get('reset_after_seconds'))}; "
                f"7d {fmt_reset(secondary.get('reset_after_seconds'))}"
            ),
            "plan": plan,
            "refreshed": now,
            "saved_at": int(datetime.now().timestamp()),
        }
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(fresh), encoding="utf-8")
        tmp.rename(cache_path)
        return {"status": "LIVE", **fresh}
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return {
                "status": "AUTH_FAILED",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "auth failed",
                "error": f"Codex usage API HTTP {exc.code}; re-authentication required",
            }
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return {"status": "STALE", **cached, "error": f"Codex usage API HTTP {exc.code}"}
        except Exception:
            return {
                "status": "N/A",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "never",
                "error": f"Codex usage API HTTP {exc.code}",
            }
    except Exception as exc:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            error = f"Codex usage unavailable: {type(exc).__name__}"
            return {"status": "STALE", **cached, "error": error}
        except Exception:
            return {
                "status": "N/A",
                "plan": "N/A",
                "5h": "N/A",
                "7d": "N/A",
                "reset": "N/A",
                "refreshed": "never",
                "error": f"Codex usage unavailable: {type(exc).__name__}",
            }


# ── Format ────────────────────────────────────────────────────────────────


def send_direct(html: str) -> bool:
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_TARGET_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("ok", False)
    except Exception as e:
        print(f"Direct send failed: {e}")
        return False


def load_report_data() -> dict[str, Any]:
    """Collect an immutable read-only card without Telegram delivery."""
    modes = get_runtime_modes()
    ledger = load_health_ledger(query_one, query_all, venue_mode=modes["venue_mode"])
    provider_state = load_provider_state()
    positions = load_positions(provider_state)
    pending = load_pending_orders(provider_state)
    try:
        codex = get_codex_usage()
    except Exception as exc:
        codex = {"status": "UNKNOWN", "error": type(exc).__name__}
    try:
        services = get_service_status()
    except Exception as exc:
        services = {"status": "UNKNOWN", "error": type(exc).__name__}
    return {
        "positions": positions,
        "pending_orders": pending,
        "sltp": load_sltp_status(provider_state, ledger["fallback_rows"]),
        "pnl": ledger["pnl"],
        "messages": ledger["messages"],
        "metrics": ledger["metrics"],
        "account": get_account(),
        "modes": modes,
        "codex": codex,
        "services": services,
    }


if __name__ == "__main__":
    report = format_report(**load_report_data())
    print(report)
    print("\n---")

    if send_direct(report):
        print("SENT")
    else:
        print("FAILED")
        sys.exit(1)
