"""Parity contract: Telegram /health must render via the cron formatter.

The periodical health cron (scripts/health_report.py) and the Telegram
/health slash command are two different entry points. They must not
diverge: /health has to call the same format_report the cron calls, so
the card an operator gets on demand is byte-identical in structure to the
one the 6-hourly timer sends.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRON = ROOT / "scripts" / "health_report.py"


def _load_cron():
    """Import the cron script as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("_cron_health_report", CRON)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shared() -> object:
    from fatty_trader.operator import health_report_format as mod

    return mod


def test_shared_formatter_exists_and_is_the_cron_one():
    """The package formatter must be the same function object the cron uses."""
    cron = _load_cron()
    shared = _shared()
    assert shared.format_report is cron.format_report


def test_shared_module_has_no_io_helpers():
    """Loaders that need docker/host auth must NOT be pulled into the shared module.

    /health runs inside the operator-bot container, which has no docker
    socket and no host Codex auth. A shared module that shelled out to
    docker would render blanks there.
    """
    shared = _shared()
    banned_names = (
        "docker_exec_bitget",
        "query_one",
        "query_all",
        "get_codex_usage",
        "send_direct",
    )
    for banned in banned_names:
        assert not hasattr(shared, banned), f"shared formatter must not carry I/O helper {banned}"


def test_slash_command_uses_cron_formatter(monkeypatch):
    """Exercise the injected reader, not source-string name containment."""
    from fatty_trader.operator.health import create_shared_health_reader
    from fatty_trader.operator.live_commands import OperatorCommandService

    shared = _shared()
    snapshot = dict(
        positions=None,
        pending_orders=None,
        sltp={},
        pnl={},
        messages=[],
        metrics={"open_positions": 0},
        account={},
        modes={},
        codex={},
        services={},
    )
    seen = []

    def renderer(**data):
        seen.append(data)
        return "shared snapshot rendered"

    monkeypatch.setattr(shared, "format_report", renderer)
    service = OperatorCommandService(
        object(), operator_id=42, health_reader=create_shared_health_reader(lambda: snapshot)
    )
    assert service._on_health() == "shared snapshot rendered"
    assert seen == [snapshot]
