#!/usr/bin/env python3
"""One-pass, read-only evidence snapshot for Fatty Bitget operations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}(?:USDT)?$")


def run(command: Sequence[str]) -> dict[str, Any]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def compose(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def compose_exec(service: str, *args: str) -> dict[str, Any]:
    return run(compose("exec", "-T", service, *args))


def parse_json_output(result: dict[str, Any]) -> Any:
    text = result["stdout"]
    if not text:
        return {"error": result["stderr"] or "empty-output"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "stderr": result["stderr"]}


def validated_symbol(raw: str) -> str:
    symbol = raw.upper().strip()
    if not SYMBOL_RE.fullmatch(symbol):
        raise SystemExit("symbol must match the Bitget token shape, for example PONSUSDT")
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def symbol_sql(symbol: str) -> str:
    pair = symbol.removesuffix("USDT")
    return f"""
SELECT jsonb_build_object(
  'source_messages', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', tm.id, 'channel_id', tm.channel_id, 'message_id', tm.message_id,
      'received_at', tm.received_at, 'intake_state', tm.intake_state,
      'raw_text', tm.raw_text
    ) ORDER BY tm.received_at, tm.message_id)
    FROM telegram_messages tm
    WHERE tm.raw_text ILIKE '%{pair}%'
  ), '[]'::jsonb),
  'canonical_signals', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', c.id, 'message_id', c.message_id, 'pair_token', c.pair_token,
      'direction', c.direction, 'entry_price', c.entry_price,
      'stop_loss', c.stop_loss, 'take_profits', c.take_profits,
      'created_at', c.created_at
    ) ORDER BY c.created_at)
    FROM canonical_signals c
    WHERE upper(c.pair_token) LIKE '{pair}%'
  ), '[]'::jsonb),
  'dispatches', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', d.id, 'source_id', d.source_id, 'state', d.state,
      'attempts', d.attempts, 'terminal_reason', d.terminal_reason,
      'created_at', d.created_at, 'updated_at', d.updated_at,
      'transitions', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
          'from', t.from_state, 'to', t.to_state,
          'reason', t.reason, 'created_at', t.created_at
        ) ORDER BY t.created_at)
        FROM dispatch_transitions t WHERE t.dispatch_id = d.id
      ), '[]'::jsonb)
    ) ORDER BY d.created_at)
    FROM dispatches d
    JOIN canonical_signals c ON c.id = d.source_id
    WHERE upper(c.pair_token) LIKE '{pair}%'
  ), '[]'::jsonb),
  'live_order_intents', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', li.id, 'client_order_id', li.client_order_id,
      'provider_order_id', li.provider_order_id, 'symbol', li.symbol,
      'side', li.side, 'role', li.role, 'state', li.state,
      'requested_qty', li.requested_qty, 'filled_qty', li.filled_qty,
      'filled_price', li.filled_price, 'fee', li.fee,
      'provider_fill_ids', li.provider_fill_ids,
      'created_at', li.created_at, 'updated_at', li.updated_at
    ) ORDER BY li.created_at)
    FROM live_order_intents li
    WHERE upper(li.symbol) LIKE '{pair}%'
       OR upper(li.client_order_id) LIKE '%{pair}%'
  ), '[]'::jsonb),
  'fills', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', f.id, 'client_order_id', f.client_order_id,
      'provider_fill_id', f.provider_fill_id, 'symbol', f.symbol,
      'price', f.price, 'quantity', f.quantity, 'fee', f.fee,
      'fee_ccy', f.fee_ccy, 'realized_pnl', f.realized_pnl,
      'filled_at', f.filled_at
    ) ORDER BY f.filled_at)
    FROM fills f WHERE upper(f.symbol) LIKE '{pair}%'
  ), '[]'::jsonb),
  'positions', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', p.id, 'symbol', p.symbol, 'direction', p.direction,
      'quantity', p.quantity, 'protection_state', p.protection_state,
      'opened_at', p.opened_at, 'closed_at', p.closed_at
    ) ORDER BY p.opened_at)
    FROM positions p WHERE upper(p.symbol) LIKE '{pair}%'
  ), '[]'::jsonb),
  'orders', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
      'id', o.id, 'dispatch_id', o.dispatch_id, 'client_order_id', o.client_order_id,
      'venue_order_id', o.venue_order_id, 'role', o.role, 'state', o.state,
      'created_at', o.created_at
    ) ORDER BY o.created_at)
    FROM orders o
    WHERE upper(COALESCE(o.client_order_id, '')) LIKE '%{pair}%'
       OR upper(COALESCE(o.venue_order_id, '')) LIKE '%{pair}%'
  ) , '[]'::jsonb)
)::text;
"""


def database_snapshot(symbol: str | None) -> Any:
    cap_sql = """
WITH active_intents AS (
  SELECT count(*)::int AS n FROM live_order_intents
  WHERE exchange='bitget' AND role='ENTRY'
    AND state NOT IN ('filled','rejected','cancelled','reconciled')
), active_reservations AS (
  SELECT count(*)::int AS n
  FROM canary_entry_reservations r
  JOIN dispatches d ON d.id = r.dispatch_id
  WHERE r.exchange='bitget'
    AND d.state NOT IN ('FILLED','REJECTED','CANCELLED','RECONCILED')
), raw_reservations AS (
  SELECT count(*)::int AS n FROM canary_entry_reservations WHERE exchange='bitget'
)
SELECT jsonb_build_object(
  'active_entry_intents', (SELECT n FROM active_intents),
  'effective_active_reservations', (SELECT n FROM active_reservations),
  'raw_reservations', (SELECT n FROM raw_reservations),
  'queued_dispatches', (SELECT count(*) FROM dispatches WHERE exchange='bitget' AND state='QUEUED'),
  'submitting_dispatches', (
    SELECT count(*) FROM dispatches
    WHERE exchange='bitget' AND state='SUBMITTING'
  ),
  'open_db_positions', (SELECT count(*) FROM positions WHERE closed_at IS NULL)
)::text;
"""
    result = run(
        compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-At",
            "-c",
            cap_sql,
        )
    )
    output: dict[str, Any] = {"capacity": parse_json_output(result)}
    if symbol is not None:
        signal_result = run(
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "fatty_app",
                "-d",
                "fatty_trader",
                "-At",
                "-c",
                symbol_sql(symbol),
            )
        )
        output["signal"] = parse_json_output(signal_result)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="optional signal symbol, e.g. PONSUSDT")
    args = parser.parse_args()
    symbol = validated_symbol(args.symbol) if args.symbol else None

    git_status = run(["git", "status", "--short", "--branch"])
    git_head = run(["git", "rev-parse", "HEAD"])
    git_remote = run(["git", "rev-parse", "origin/main"])
    config = run(compose("config", "--quiet"))
    services = run(compose("ps"))
    env_command = (
        "printf 'TRADER_MODE=%s\\n"
        "BITGET_MODE=%s\\n"
        "BITGET_EXECUTION_ENABLED=%s\\n"
        "BITGET_CANARY_MAX_ORDERS=%s\\n"
        "BITGET_OPERATOR_MUTATIONS_ENABLED=%s\\n"
        "BITGET_FALLBACK_MUTATIONS_ENABLED=%s\\n' "
        '"$TRADER_MODE" "$BITGET_MODE" "$BITGET_EXECUTION_ENABLED" '
        '"$BITGET_CANARY_MAX_ORDERS" "$BITGET_OPERATOR_MUTATIONS_ENABLED" '
        '"${BITGET_FALLBACK_MUTATIONS_ENABLED:-0}"'
    )
    env = compose_exec(
        "dispatcher-bitget",
        "sh",
        "-lc",
        env_command,
    )
    source_check = (
        "from pathlib import Path; import json; "
        "text=Path('src/fatty_trader/execution/bitget_dispatch_repository.py').read_text(); "
        "start=text.index('_RESERVE_CANARY_ENTRY_SQL'); "
        "snippet=text[start:start+1500]; "
        "print(json.dumps({'joins_dispatches': "
        "'JOIN dispatches reserved_dispatch' in snippet, "
        "'filters_terminal_states': 'reserved_dispatch.state NOT IN' in snippet}))"
    )
    deployed_source = compose_exec(
        "dispatcher-bitget",
        "/app/.venv/bin/python",
        "-c",
        source_check,
    )
    provider = compose_exec(
        "dispatcher-bitget",
        "sh",
        "-lc",
        "cd /app && . .venv/bin/activate && python3 scripts/bitget_api_probe.py --json",
    )
    account = compose_exec(
        "dispatcher-bitget",
        "sh",
        "-lc",
        "cd /app && . .venv/bin/activate && python3 scripts/_probe_account.py",
    )
    snapshot: dict[str, Any] = {
        "git": {
            "status": git_status["stdout"],
            "head": git_head["stdout"],
            "origin_main": git_remote["stdout"],
        },
        "compose": {
            "config_returncode": config["returncode"],
            "ps": services["stdout"],
        },
        "runtime_env": env["stdout"],
        "deployed_canary_source": parse_json_output(deployed_source),
        "database": database_snapshot(symbol),
        "provider_probe": parse_json_output(provider),
        "account_probe": account["stdout"],
    }
    if symbol is not None:
        position = compose_exec(
            "dispatcher-bitget",
            "sh",
            "-lc",
            f"cd /app && . .venv/bin/activate && python3 scripts/_probe_position.py {symbol}",
        )
        snapshot["provider_position"] = parse_json_output(position)
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0 if config["returncode"] == 0 and provider["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
