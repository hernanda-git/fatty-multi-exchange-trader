import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "generate_telegram_session.py"
SPEC = importlib.util.spec_from_file_location("generate_telegram_session", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_credentials_require_numeric_api_id_and_nonempty_hash_and_phone() -> None:
    with pytest.raises(ValueError, match="TG_API_ID"):
        MODULE.TelegramCredentials.from_environment({"TG_API_ID": "x"})

    with pytest.raises(ValueError, match="TG_API_HASH"):
        MODULE.TelegramCredentials.from_environment({"TG_API_ID": "123", "TG_PHONE": "+62"})

    with pytest.raises(ValueError, match="TG_PHONE"):
        MODULE.TelegramCredentials.from_environment({"TG_API_ID": "123", "TG_API_HASH": "a" * 32})


def test_session_file_path_stays_below_gitignored_data_directory() -> None:
    path = MODULE.default_session_file(Path("C:/repo"))

    assert path == Path("C:/repo/data/telegram/telegram.session")


def test_default_env_file_resolves_to_repository_root() -> None:
    assert MODULE.default_env_file() == SCRIPT.parents[1] / ".env"


def test_main_reports_missing_credentials_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("TG_API_ID", "TG_API_HASH", "TG_PHONE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(MODULE, "load_dotenv", lambda _: None)
    monkeypatch.setattr(MODULE, "parse_args", lambda: SimpleNamespace(env_file=Path("ignored")))

    assert MODULE.main() == 2
    assert "TG_API_ID" in capsys.readouterr().err
