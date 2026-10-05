"""Production policy is admission, not a replacement for trade safety gates."""

import pytest

from fatty_trader.service import service_config


@pytest.mark.parametrize(
    "service", ["dispatcher-bitget", "monitor-bitget", "operator-bot", "source-management"]
)
@pytest.mark.parametrize(
    "field,value",
    [("TRADER_MODE", "DEMO"), ("BITGET_MODE", "DEMO"), ("BITGET_EXECUTION_ENABLED", "0")],
)
def test_production_rejects_nonlive_or_disabled_execution(
    service: str, field: str, value: str
) -> None:
    environment = {
        "FATTY_PRODUCTION_LIVE_ONLY": "1",
        "TRADER_MODE": "LIVE",
        "BITGET_MODE": "LIVE",
        "BITGET_EXECUTION_ENABLED": "1",
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_APPROVAL_REFERENCE": "test-approval",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }
    environment[field] = value
    with pytest.raises(ValueError, match="production LIVE-only policy"):
        service_config(service, environment)


def test_rendered_compose_enforces_production_identity() -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    from fatty_trader.production_policy import (
        PRODUCTION_BITGET_SERVICES,
        validate_production_live_policy,
    )

    root = Path(__file__).parents[2]
    environment = {"PATH": os.environ["PATH"], "POSTGRES_PASSWORD": "test-only"}
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=root,
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )
    services = json.loads(rendered.stdout)["services"]
    for name in PRODUCTION_BITGET_SERVICES:
        validate_production_live_policy(services[name]["environment"])


@pytest.mark.parametrize(
    "override",
    [
        {"BITGET_EXECUTION_ENABLED": "0"},
        {"TRADER_MODE": "DEMO"},
        {"BITGET_MODE": "DEMO"},
        {"TRADER_MODE": "DEMO", "BITGET_MODE": "DEMO"},
    ],
)
def test_compose_policy_cli_rejects_disabled_override_before_any_mutation(override) -> None:
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).parents[2]
    result = subprocess.run(
        ["bash", "scripts/check_production_live_policy.sh"],
        cwd=root,
        env={
            "PATH": os.environ["PATH"],
            "POSTGRES_PASSWORD": "test-only",
            **override,
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "production LIVE-only policy" in result.stderr


def test_runtime_verifier_rejects_disabled_container_before_db_or_provider(tmp_path) -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    from fatty_trader.production_policy import PRODUCTION_BITGET_SERVICES, PRODUCTION_VALUES

    root = Path(__file__).parents[2]
    config = json.dumps(
        {
            "services": {
                name: {"environment": PRODUCTION_VALUES} for name in PRODUCTION_BITGET_SERVICES
            }
        }
    )
    fake = tmp_path / "compose"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"config = {config!r}\n"
        "if sys.argv[1] == 'config':\n"
        "    print(config if '--format' in sys.argv else '')\n"
        "elif sys.argv[1:4] == ['exec', '-T', 'dispatcher-bitget']:\n"
        "    print('LIVE|LIVE|0')\n"
        "else:\n"
        "    sys.exit('unexpected DB/provider/service probe before policy check')\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", "scripts/verify_bitget_runtime.sh"],
        cwd=root,
        env={"PATH": os.environ["PATH"], "COMPOSE_BIN": str(fake)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "runtime_blocked=production_live_policy" in result.stderr
    assert "unexpected DB/provider" not in result.stderr


@pytest.mark.parametrize(
    "kill_state,blocker",
    [
        ("true:incident", "kill_switch_active"),
        ("true:stream-only", "kill_switch_active"),
        ("false:none", "source_lineage_mismatch"),
    ],
)
def test_runtime_verifier_rejects_unsafe_runtime_without_provider_probe(
    tmp_path, kill_state, blocker
) -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    from fatty_trader.production_policy import PRODUCTION_BITGET_SERVICES, PRODUCTION_VALUES

    root = Path(__file__).parents[2]
    config = json.dumps(
        {
            "services": {
                name: {"environment": PRODUCTION_VALUES} for name in PRODUCTION_BITGET_SERVICES
            }
        }
    )
    fake = tmp_path / "docker"
    fake.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"config = {config!r}\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'inspect':\n"
        "    print('exited:0')\n"
        "elif args[0] == 'config':\n"
        "    print(config if '--format' in args else '')\n"
        "elif args[0] == 'ps':\n"
        "    print('postgres dispatcher-bitget monitor-bitget')\n"
        "elif args[:2] == ['exec', '-T'] and args[2] != 'postgres':\n"
        "    if 'bitget_api_probe.py' in ' '.join(args):\n"
        "        sys.exit('unexpected provider probe with active kill switch')\n"
        "    print('stale-source' if '-c' in args and 'sh' not in args else 'LIVE|LIVE|1')\n"
        "elif args[:3] == ['exec', '-T', 'postgres']:\n"
        f"    state = {kill_state!r}\n"
        "    if state == 'true:stream-only' and 'bitget-protection-stream' not in args[-1]:\n"
        "        state = 'false:none'\n"
        "    print(state if 'venue_kill_switches' in args[-1] else '1')\n"
        "elif args[0] != 'logs':\n"
        "    sys.exit('unexpected command')\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", "scripts/verify_bitget_runtime.sh"],
        cwd=root,
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}", "COMPOSE_BIN": str(fake)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert f"runtime_blocked={blocker}" in result.stderr
    assert "runtime_check=PASS" not in result.stdout
    assert "unexpected provider probe" not in result.stderr


@pytest.mark.parametrize(
    "service", ["dispatcher-bitget", "monitor-bitget", "operator-bot", "source-management"]
)
def test_policy_does_not_require_optional_mutation_flags(service) -> None:
    from fatty_trader.production_policy import PRODUCTION_VALUES

    environment = {
        **PRODUCTION_VALUES,
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_APPROVAL_REFERENCE": "test-approval",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
        "BITGET_FALLBACK_MUTATIONS_ENABLED": "0",
        "BITGET_OPERATOR_MUTATIONS_ENABLED": "0",
        "BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED": "0",
    }
    assert service_config(service, environment).mode == "LIVE"


@pytest.mark.parametrize(
    "field", ["BITGET_CANARY_MAX_ORDERS", "BITGET_APPROVAL_REFERENCE", "BITGET_MAX_CLOCK_SKEW_MS"]
)
def test_live_policy_does_not_bypass_existing_execution_safety(field) -> None:
    from fatty_trader.production_policy import PRODUCTION_VALUES

    environment = {
        **PRODUCTION_VALUES,
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_APPROVAL_REFERENCE": "test-approval",
        "BITGET_MAX_CLOCK_SKEW_MS": "5000",
    }
    del environment[field]
    with pytest.raises(ValueError):
        service_config("dispatcher-bitget", environment)


def test_web_reports_configured_live_gate_without_claiming_order_readiness() -> None:
    import json
    import os
    import subprocess
    from pathlib import Path

    from fatty_trader.web.health import build_health_report

    root = Path(__file__).parents[2]
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=root,
        env={"PATH": os.environ["PATH"], "POSTGRES_PASSWORD": "test-only"},
        check=True,
        text=True,
        capture_output=True,
    )
    report = build_health_report(json.loads(rendered.stdout)["services"]["web"]["environment"])
    assert report["live_execution_enabled"] is True
    assert report["configuration"]["execution_enabled"] is True
    assert report["orders_enabled"] is None
    assert report["readiness"]["status"] == "unknown"
