import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunResult
from fatty_trader.analyzer.image_analysis import (
    analyze_image_json,
    management_from_image_json,
    signal_from_image_json,
)
from fatty_trader.intake.media import persist_media_bytes
from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


def test_media_persistence_is_revision_stable_and_hashed(tmp_path: Path) -> None:
    artifact = persist_media_bytes(
        tmp_path,
        channel_id=123,
        message_id=456,
        revision_hash="a" * 64,
        mime_type="image/jpeg",
        data=b"chart-bytes",
    )
    assert artifact.path.exists()
    assert artifact.path.read_bytes() == b"chart-bytes"
    assert artifact.sha256 == "8e3d354aeef3c80d6dfbbbfbafbc24969c125526550668f7a496ec6117e62a50"


def test_image_analysis_returns_only_message_and_setup(tmp_path: Path) -> None:
    output = '{"message":"BTC setup","setup":{"asset":"BTC","entry_zone":[74020.9,74390.0]}}'
    image_path = tmp_path / "chart.jpg"
    image_path.write_bytes(b"chart")

    result = analyze_image_json(
        text="$BTC todays move\nBids placed around 74k area",
        message_id=16099,
        image_path=str(image_path),
        runner=lambda prompt, image_paths: CodexRunResult(True, False, False, 0, None, output, ""),
    )

    assert result == {
        "message": "BTC setup",
        "setup": {"asset": "BTC", "entry_zone": [74020.9, 74390.0]},
    }


def test_codex_runner_builds_native_image_argv() -> None:
    runner = CodexRunner()
    argv = runner.build_argv("analyze", image_paths=("/data/chart.jpg",))
    image_index = argv.index("--image")
    assert argv[image_index - 1] == "analyze"
    assert argv[image_index : image_index + 2] == ["--image", "/data/chart.jpg"]


def test_telegram_intake_downloads_and_persists_media(tmp_path: Path) -> None:
    class File:
        mime_type = "image/jpeg"

    class Message:
        id = 9
        message = "BTC chart"
        date = datetime.now(UTC)
        media = object()
        file = File()

        async def download_media(self, *, file: str) -> str:
            path = Path(file)
            path.write_bytes(b"chart-bytes")
            return str(path)

    repository = InMemoryRawMessageRepository()
    item = asyncio.run(
        TelegramIntake(repository).ingest_async(
            channel_id=8, message=Message(), media_root=str(tmp_path)
        )
    )
    assert item.media_path is not None
    assert Path(item.media_path).read_bytes() == b"chart-bytes"
    assert item.media_sha256 == "8e3d354aeef3c80d6dfbbbfbafbc24969c125526550668f7a496ec6117e62a50"


def test_complete_image_setup_becomes_canonical_signal() -> None:
    signal = signal_from_image_json(
        {
            "message": "BTC long",
            "setup": {
                "action": "ENTER",
                "asset": "BTC",
                "side": "LONG",
                "entry": 74050,
                "stop_loss": 73800,
                "take_profits": [75400, 76217.8],
            },
        },
        message_id=42,
        source_revision="a" * 64,
    )
    assert signal is not None
    assert signal.pair_token == "BTC"
    assert signal.entry_price == 74050


def test_image_close_setup_becomes_manual_close_instruction() -> None:
    management = management_from_image_json(
        {"message": "close BTC", "setup": {"action": "CLOSE", "asset": "BTC"}}
    )
    assert management is not None
    assert management.symbol == "BTCUSDT"
