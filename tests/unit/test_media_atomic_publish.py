from pathlib import Path

import pytest

from fatty_trader.intake.media import persist_media_bytes


def test_failed_publication_never_leaves_a_partial_final_artifact(tmp_path, monkeypatch):
    original = Path.write_bytes

    def partial_write(path, data):
        original(path, data[:2])
        raise OSError("disk fixture failure")

    monkeypatch.setattr(Path, "write_bytes", partial_write)
    with pytest.raises(OSError):
        persist_media_bytes(
            tmp_path,
            channel_id=7,
            message_id=1,
            revision_hash="a" * 64,
            mime_type="image/jpeg",
            data=b"complete",
        )
    assert not list(tmp_path.rglob("*.j*"))
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
