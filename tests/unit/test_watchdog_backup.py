"""A silently killed backup must page the owner within a day."""

import importlib.util
import os
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "live_watchdog.py"
SPEC = importlib.util.spec_from_file_location("live_watchdog", SCRIPT)
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)

NOW = 1_000_000.0
DUMP = "fatty_trader_20261119T095356Z_Ek07nE.dump"


def _dump(directory, *, name=DUMP, size=1024, age_hours=1.0):
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = NOW - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_fresh_dump_is_ok(tmp_path):
    _dump(tmp_path)
    assert watchdog.backup_health(tmp_path, NOW, 26)["status"] == "ok"


def test_stale_dump_is_flagged_with_its_age(tmp_path):
    _dump(tmp_path, age_hours=27)
    health = watchdog.backup_health(tmp_path, NOW, 26)
    assert health["status"] == "stale"
    assert health["age_hours"] == 27.0


def test_empty_dump_is_not_success(tmp_path):
    _dump(tmp_path, size=0)
    assert watchdog.backup_health(tmp_path, NOW, 26)["status"] == "empty"


def test_missing_directory_or_foreign_files_are_missing(tmp_path):
    assert watchdog.backup_health(tmp_path / "absent", NOW, 26)["status"] == "missing"
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert watchdog.backup_health(tmp_path, NOW, 26)["status"] == "missing"


def test_newest_dump_wins_and_a_stale_one_raises_a_reason(tmp_path, monkeypatch):
    _dump(tmp_path, name="fatty_trader_20261118T095356Z_aaaaaa.dump", age_hours=40)
    _dump(tmp_path, name="fatty_trader_20261119T095356Z_bbbbbb.dump", age_hours=30)
    monkeypatch.setattr(watchdog, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(watchdog, "BACKUP_MAX_AGE_HOURS", 26)
    monkeypatch.setattr(watchdog.time, "time", lambda: NOW)

    reasons: list[dict[str, str]] = []
    health = watchdog.observe_backup(reasons)

    assert health["newest"] == "fatty_trader_20261119T095356Z_bbbbbb.dump"
    assert reasons == [{"code": "backup-stale", "severity": "critical", "detail": "30.0h"}]
