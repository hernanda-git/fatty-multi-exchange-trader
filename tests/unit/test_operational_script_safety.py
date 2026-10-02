"""Operational helpers run only with fakes in a credential-free network namespace."""

import ast
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
UNSAFE = (
    "_place_protection",
    "_register_fallback",
    "_reconcile_dispatch",
    "place_short_btc",
    "_probe_account",
    "_probe_position",
    "check_position_btc",
)


def guarded(name):
    tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
    calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert not calls, f"{name}: top-level operational call"
    assert any(
        isinstance(node, ast.If) and "__main__" in ast.unparse(node.test) for node in tree.body
    )


def load(name, monkeypatch):
    if name in UNSAFE:
        guarded(name)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    fake = types.ModuleType("fatty_trader.exchanges.bitget.client")
    fake.BitgetRestClient = lambda *a, **k: pytest.fail("real client construction forbidden")
    fake.BitgetApiError = RuntimeError
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    for modname, attr, value in (
        ("fatty_trader.execution.bitget_monitor", "BitgetMonitor", fake.BitgetRestClient),
        (
            "fatty_trader.execution.bitget_protection_watchdog",
            "PROTECTION_STREAM_SCOPE",
            "protection",
        ),
        (
            "fatty_trader.execution.bitget_recovery",
            "release_after_clean_monitor",
            fake.BitgetRestClient,
        ),
        (
            "fatty_trader.storage.reconciliation",
            "PostgresReconciliationRepository",
            fake.BitgetRestClient,
        ),
    ):
        stub = types.ModuleType(modname)
        setattr(stub, attr, value)
        monkeypatch.setitem(sys.modules, modname, stub)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", UNSAFE)
def test_helpers_are_import_safe(name, monkeypatch):
    load(name, monkeypatch)


@pytest.mark.parametrize("name", UNSAFE[:4])
def test_mutation_requires_explicit_confirmation(name, monkeypatch):
    module = load(name, monkeypatch)
    result = module.main([])
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    assert result == 2


@pytest.mark.parametrize("name", ("place_short_btc", "_reconcile_dispatch"))
def test_confirmation_cannot_bypass_durable_execution(name, monkeypatch):
    module = load(name, monkeypatch)
    result = module.main(["--confirm"])
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    assert result == 2


class FakeClient:
    def __init__(self, positions=None):
        self.positions = positions
        self.events = []

    async def get_single_position(self, symbol):
        self.events.append(("read", symbol))
        return self.positions

    async def place_position_tpsl(self, **kwargs):
        self.events.append(("tpsl", kwargs))
        return []

    async def aclose(self):
        self.events.append(("close",))


POSITION = {"symbol": "DOGEUSDT", "holdSide": "short", "total": "4", "openPriceAvg": "1"}
ARGS = [
    "--confirm",
    "--approval-reference",
    "review-123",
    "--symbol",
    "DOGEUSDT",
    "--hold-side",
    "short",
    "--quantity",
    "4",
    "--stop-loss",
    "1.1",
    "--take-profit",
    "0.9",
]


@pytest.mark.parametrize("name", ("_place_protection", "_register_fallback"))
@pytest.mark.parametrize(
    "positions",
    [
        None,
        [],
        [dict(POSITION, total="0")],
        [dict(POSITION, total="NaN")],
        [dict(POSITION, symbol="BTCUSDT")],
        [dict(POSITION, holdSide="long")],
        [dict(POSITION, total="5")],
        [POSITION, POSITION],
    ],
)
def test_provider_evidence_required_before_protection(name, positions, monkeypatch):
    module = load(name, monkeypatch)
    client = FakeClient(positions)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")
    monkeypatch.setattr(module, "BitgetRestClient", lambda **kwargs: client, raising=False)
    fallback = types.ModuleType("fatty_trader.execution.bitget_fallback_protection")
    fallback.register_fallback = lambda **kwargs: pytest.fail("mutation before evidence")
    fallback.load_active = lambda: []
    monkeypatch.setitem(sys.modules, fallback.__name__, fallback)
    args = ARGS + (["--position-key", "review-position"] if name == "_register_fallback" else [])
    with pytest.raises(ValueError, match="position"):
        asyncio.run(module.main(args))
    assert client.events == [("read", "DOGEUSDT"), ("close",)]


@pytest.mark.parametrize(
    "name,entry",
    (("_probe_account", "main"), ("check_position_btc", "main"), ("bitget_demo_telemetry", "_run")),
)
def test_account_probes_pass_symbol_not_margin_coin(name, entry, monkeypatch):
    module = load(name, monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")
    monkeypatch.setenv("BITGET_MODE", "DEMO")

    class AccountClient(FakeClient):
        async def get_account(self, *, symbol):
            self.events.append(("account", symbol))
            return {}

        async def get_all_positions(self):
            return []

        async def get_pending_orders(self):
            return []

        async def get_fills(self):
            return []

    client = AccountClient()
    monkeypatch.setattr(module, "BitgetRestClient", lambda *a, **k: client)
    asyncio.run(getattr(module, entry)(symbol="ETHUSDT"))
    assert client.events == [("account", "ETHUSDT"), ("close",)]


@pytest.mark.parametrize(
    "name", ("reconcile_historical_bitget_intent", "recover_bitget_demo_kill_switch")
)
def test_recovery_requires_explicit_confirmation(name, monkeypatch):
    module = load(name, monkeypatch)
    result = module.main([])
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    assert result == 2


@pytest.mark.parametrize("name", ("_place_protection", "_register_fallback"))
@pytest.mark.parametrize(
    "flag,value",
    [
        ("--approval-reference", " "),
        ("--symbol", "dogeUSDT"),
        ("--quantity", "0"),
        ("--quantity", "NaN"),
        ("--stop-loss", "-1"),
        ("--take-profit", "Infinity"),
    ],
)
def test_invalid_repair_arguments_refuse_before_client(name, flag, value, monkeypatch):
    module = load(name, monkeypatch)
    args = list(ARGS)
    args[args.index(flag) + 1] = value
    if name == "_register_fallback":
        args += ["--position-key", "review-position"]
    with pytest.raises(ValueError):
        asyncio.run(module.main(args))


@pytest.mark.parametrize(
    "positions,orders",
    [({}, []), ([], {}), ([{}], []), ([{"total": "NaN"}], []), ([{"total": "-1"}], [])],
)
def test_historical_reconciliation_rejects_unknown_provider_state(positions, orders, monkeypatch):
    module = load("reconcile_historical_bitget_intent", monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")
    monkeypatch.setenv("BITGET_MODE", "DEMO")

    class HistoricalClient(FakeClient):
        async def get_all_positions(self):
            return positions

        async def get_pending_orders(self):
            return orders

    client = HistoricalClient()
    monkeypatch.setattr(module, "BitgetRestClient", lambda *a, **k: client)
    with pytest.raises(ValueError):
        asyncio.run(module._provider_snapshot())
    assert client.events == [("close",)]


def test_btc_probe_closes_client_on_read_failure(monkeypatch):
    module = load("check_position_btc", monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")

    class BrokenClient(FakeClient):
        async def get_all_positions(self):
            raise RuntimeError("provider unavailable")

    client = BrokenClient()
    monkeypatch.setattr(module, "BitgetRestClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(module.main())
    assert client.events == [("close",)]


def test_historical_flat_snapshot_cannot_prove_rejection(monkeypatch):
    module = load("reconcile_historical_bitget_intent", monkeypatch)

    async def unexpected_provider():
        pytest.fail("flat provider snapshot cannot prove historical rejection")

    monkeypatch.setattr(module, "_provider_snapshot", unexpected_provider)
    assert (
        module.main(
            [
                "--confirm",
                "--approval-reference",
                "review-123",
                "--client-oid",
                "test-oid",
                "--resolved-state",
                "rejected",
            ]
        )
        == 2
    )


def test_demo_recovery_refuses_blank_approval_before_provider(monkeypatch):
    module = load("recover_bitget_demo_kill_switch", monkeypatch)
    monkeypatch.setenv("BITGET_MODE", "DEMO")
    with pytest.raises(ValueError, match="approval"):
        asyncio.run(module.main(["--confirm", "--approval-reference", " "]))


@pytest.mark.parametrize("name", ("_place_protection", "_register_fallback"))
def test_valid_repair_uses_parameterized_provider_identity(name, monkeypatch):
    module = load(name, monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")
    client = FakeClient([POSITION])
    monkeypatch.setattr(module, "BitgetRestClient", lambda **kwargs: client)
    registrations = []
    fallback = types.ModuleType("fatty_trader.execution.bitget_fallback_protection")
    fallback.register_fallback = lambda **kwargs: registrations.append(kwargs) or "test-key"
    fallback.load_active = lambda: []
    monkeypatch.setitem(sys.modules, fallback.__name__, fallback)
    args = ARGS + (["--position-key", "review-position"] if name == "_register_fallback" else [])
    assert asyncio.run(module.main(args)) == 0
    assert client.events[0] == ("read", "DOGEUSDT")
    assert client.events[-1] == ("close",)
    if name == "_place_protection":
        assert client.events[1] == (
            "tpsl",
            {
                "symbol": "DOGEUSDT",
                "hold_side": "short",
                "quantity": "4",
                "stop_loss": "1.1",
                "take_profit": "0.9",
            },
        )
        assert client.events[2] == ("read", "DOGEUSDT")
    else:
        assert len(registrations) == 1
        assert registrations[0]["symbol"] == "DOGEUSDT"
        assert registrations[0]["direction"] == "SHORT"
        assert registrations[0]["position_key"] == "review-position"
        assert str(registrations[0]["entry_price"]) == "1"
        assert str(registrations[0]["quantity"]) == "4"


@pytest.mark.parametrize("name", ("_place_protection", "_register_fallback"))
def test_provider_failure_never_mutates_and_closes_client(name, monkeypatch):
    module = load(name, monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")

    class BrokenClient(FakeClient):
        async def get_single_position(self, symbol):
            raise RuntimeError("provider unavailable")

    client = BrokenClient()
    monkeypatch.setattr(module, "BitgetRestClient", lambda **kwargs: client)
    args = ARGS + (["--position-key", "review-position"] if name == "_register_fallback" else [])
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(module.main(args))
    assert client.events == [("close",)]


# Execute only extracted decision blocks with shell functions, never an auth or
# restore script. No host commands, credentials, containers or databases run.
def shell_decision(source, prelude):
    import subprocess

    return subprocess.run(
        ["/usr/bin/bash", "-c", "set -euo pipefail\n" + prelude + "\n" + source],
        env={"PATH": "/nonexistent", "HOME": "/tmp"},
        capture_output=True,
        text=True,
        timeout=5,
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("analyzer healthy running", 0),
        ("analyzer unhealthy running", 1),
        ("analyzer starting running", 1),
        ("analyzer healthy exited", 1),
        ("other healthy running", 1),
        ("", 1),
        ("analyzer unhealthy running\nanalyzer healthy running", 1),
    ],
)
def test_auth_health_requires_exact_running_healthy_analyzer(status, expected):
    import shlex

    text = (SCRIPTS / "refresh_codex_auth.sh").read_text()
    decision = text[text.index("for _ in {1..30}; do") :]
    prelude = (
        "docker() { if [[ $* == *' ps '* ]]; then printf '%s\\n' "
        + shlex.quote(status)
        + "; fi; }; sleep() { :; }"
    )
    result = shell_decision(decision, prelude)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize(
    "services",
    [
        "intake",
        "analyzer",
        "dispatcher-bitget",
        "monitor-bitget",
        "future-worker",
        "postgres\nanalyzer",
    ],
)
def test_restore_refuses_any_running_non_postgres_service_before_clean(services):
    import shlex

    text = (SCRIPTS / "restore_postgres.sh").read_text()
    start = text.index("# Refuse") if "# Refuse" in text else text.index("running=")
    decision = text[start:]
    prelude = (
        "compose_bin=fake_compose; backup_file=/dev/null; "
        "fake_compose() { if [[ $1 == ps ]]; then printf '%s\\n' "
        + shlex.quote(services)
        + "; else printf 'UNSAFE_CLEAN\\n'; fi; }; "
        "grep() { local row; while IFS= read -r row; do "
        "[[ $row == postgres ]] || printf '%s\\n' \"$row\"; done; }; tr() { :; }"
    )
    result = shell_decision(decision, prelude)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNSAFE_CLEAN" not in result.stdout


def test_restore_refuses_unknown_running_service_state():
    text = (SCRIPTS / "restore_postgres.sh").read_text()
    start = text.index("# Refuse") if "# Refuse" in text else text.index("running=")
    result = shell_decision(
        text[start:],
        "compose_bin=fake_compose; backup_file=/dev/null; "
        "fake_compose() { if [[ $1 == ps ]]; then return 1; "
        "else printf 'UNSAFE_CLEAN\\n'; fi; }; grep() { return 1; }",
    )
    assert result.returncode == 2
    assert "UNSAFE_CLEAN" not in result.stdout


def test_fallback_refuses_blank_position_key_before_client(monkeypatch):
    module = load("_register_fallback", monkeypatch)
    with pytest.raises(ValueError, match="position key"):
        asyncio.run(module.main(ARGS + ["--position-key", " "]))


@pytest.mark.parametrize("name", ("place_short_btc", "_reconcile_dispatch"))
def test_retired_bypasses_have_no_mutation_or_50x_calls(name):
    tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
    calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert set(calls) <= {"print", "SystemExit", "main"}
    assert not any(isinstance(node, ast.Constant) and node.value == 50 for node in ast.walk(tree))


@pytest.mark.parametrize(
    "name", ("reconcile_historical_bitget_intent", "recover_bitget_demo_kill_switch")
)
def test_recovery_import_has_no_operational_call(name, monkeypatch):
    guarded(name)
    load(name, monkeypatch)


@pytest.mark.parametrize("services", ["", "postgres"])
def test_restore_stopped_workers_allow_only_mocked_clean(services):
    import shlex

    text = (SCRIPTS / "restore_postgres.sh").read_text()
    decision = text[text.index("# Refuse") :]
    result = shell_decision(
        decision,
        "compose_bin=fake_compose; backup_file=/dev/null; "
        "fake_compose() { if [[ $1 == ps ]]; then printf '%s\\n' "
        + shlex.quote(services)
        + "; else printf 'MOCK_CLEAN:%s\\n' \"$*\"; fi; }",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("MOCK_CLEAN:") == 1
    assert "--clean --if-exists --no-owner" in result.stdout


def test_restore_confirmation_guard_precedes_any_compose_call():
    text = (SCRIPTS / "restore_postgres.sh").read_text()
    guard = text.index('if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]')
    assert guard < text.index("$compose_bin exec") < text.index("--clean")


def test_native_protection_error_does_not_enable_fallback_or_entry(monkeypatch):
    module = load("_place_protection", monkeypatch)
    for key in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"):
        monkeypatch.setenv(key, "fake-only")

    class RejectedProtection(FakeClient):
        async def place_position_tpsl(self, **kwargs):
            self.events.append(("rejected-tpsl",))
            raise RuntimeError("43011 fake refusal")

    client = RejectedProtection([POSITION])
    monkeypatch.setattr(module, "BitgetRestClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="43011"):
        asyncio.run(module.main(ARGS))
    assert client.events == [("read", "DOGEUSDT"), ("rejected-tpsl",), ("close",)]


def test_auth_health_failed_discovery_cannot_claim_healthy():
    text = (SCRIPTS / "refresh_codex_auth.sh").read_text()
    decision = text[text.index("for _ in {1..30}; do") :]
    result = shell_decision(
        decision,
        "docker() { printf 'analyzer healthy running\\n'; return 1; }; sleep() { :; }",
    )
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "snapshot",
    [{"open_positions": 1, "pending_orders": 0}, {"open_positions": 0, "pending_orders": 1}],
)
def test_historical_reconcile_requires_flat_provider_before_db(snapshot, monkeypatch):
    module = load("reconcile_historical_bitget_intent", monkeypatch)

    async def provider():
        return snapshot

    monkeypatch.setattr(module, "_provider_snapshot", provider)
    monkeypatch.setattr(module.psycopg, "connect", lambda: pytest.fail("DB access forbidden"))
    with pytest.raises(RuntimeError, match="provider is not flat"):
        module.main(
            [
                "--confirm",
                "--approval-reference",
                "review-123",
                "--client-oid",
                "test-oid",
                "--resolved-state",
                "reconciled",
            ]
        )
