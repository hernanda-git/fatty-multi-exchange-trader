"""Exercise the exact deployed source probe without credentials or providers."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ops_snapshot", ROOT / "scripts/agentic_ops_snapshot.py"
)
assert spec is not None and spec.loader is not None
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def source_probe(monkeypatch, capsys, tmp_path, source):
    target = tmp_path / "src/fatty_trader/execution/bitget_dispatch_repository.py"
    target.parent.mkdir(parents=True)
    target.write_text(source)
    monkeypatch.setattr(sys, "argv", ["agentic_ops_snapshot.py"])
    monkeypatch.setattr(
        snapshot, "run", lambda command: {"returncode": 0, "stdout": "{}", "stderr": ""}
    )
    monkeypatch.setattr(snapshot, "database_snapshot", lambda symbol: {})

    def offline_exec(service, *args):
        if "-c" in args:
            result = subprocess.run(
                [sys.executable, "-c", args[-1]],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        return {"returncode": 0, "stdout": "{}", "stderr": ""}

    monkeypatch.setattr(snapshot, "compose_exec", offline_exec)
    assert snapshot.main() == 0
    return json.loads(capsys.readouterr().out)["deployed_canary_source"]


def test_actual_canary_sql_is_not_reported_as_missing_guards(monkeypatch, capsys, tmp_path):
    source = (ROOT / "src/fatty_trader/execution/bitget_dispatch_repository.py").read_text()
    assert source_probe(monkeypatch, capsys, tmp_path, source) == {
        "joins_dispatches": True,
        "filters_terminal_states": True,
    }


@pytest.mark.parametrize("alias", ["d", "reserved_dispatch", "owner_dispatch"])
def test_alias_and_long_literal_do_not_change_guard_evidence(monkeypatch, capsys, tmp_path, alias):
    sql = (
        " " * 2000 + f"JOIN dispatches AS {alias} ON {alias}.id = r.dispatch_id "
        f"WHERE {alias}.state NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')"
    )
    source = "_RESERVE_CANARY_ENTRY_SQL = " + repr(sql)
    assert source_probe(monkeypatch, capsys, tmp_path, source) == {
        "joins_dispatches": True,
        "filters_terminal_states": True,
    }


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("no_join", {"joins_dispatches": False, "filters_terminal_states": False}),
        ("no_filter", {"joins_dispatches": True, "filters_terminal_states": False}),
        ("wrong_alias", {"joins_dispatches": True, "filters_terminal_states": False}),
        ("missing_terminal", {"joins_dispatches": True, "filters_terminal_states": False}),
    ],
)
def test_missing_guards_are_not_supplied_by_other_constants(
    monkeypatch,
    capsys,
    tmp_path,
    mutation,
    expected,
):
    sql = (
        "JOIN dispatches d ON d.id = r.dispatch_id "
        "WHERE d.state NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')"
    )
    decoy = "OTHER_SQL = " + repr(sql) + "\n"
    if mutation == "no_join":
        sql = "SELECT 1"
    elif mutation == "no_filter":
        sql = sql.split("WHERE")[0]
    elif mutation == "wrong_alias":
        sql = sql.replace("d.state", "other.state")
    else:
        sql = sql.replace("'RECONCILED'", "'UNKNOWN'")
    source = decoy + "_RESERVE_CANARY_ENTRY_SQL = " + repr(sql)
    assert source_probe(monkeypatch, capsys, tmp_path, source) == expected
