import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "autonomous_demo_cycle.py"
SPEC = importlib.util.spec_from_file_location("autonomous_demo_cycle", SCRIPT)
assert SPEC and SPEC.loader
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_state_recovers_from_malformed_json(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json")
    monkeypatch.setattr(worker, "STATE_PATH", state_path)

    assert worker._state() == {"cycle_count": 0, "failure_count": 0}


def test_state_recovers_from_non_object_json(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("[]")
    monkeypatch.setattr(worker, "STATE_PATH", state_path)

    assert worker._state() == {"cycle_count": 0, "failure_count": 0}
