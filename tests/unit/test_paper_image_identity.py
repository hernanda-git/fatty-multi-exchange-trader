import hashlib
import json

import pytest

from fatty_trader.analyzer import image_analysis


def test_image_revision_hashes_source_and_actual_media_when_canonical_missing(tmp_path):
    image = tmp_path / "chart.png"
    image.write_bytes(b"fixture image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    identity = {"channel_id": -123, "message_id": 7, "raw_text": "original", "media_sha256": digest}
    expected = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert (
        image_analysis.image_source_revision(
            image_path=str(image),
            channel_id=-123,
            message_id=7,
            text="original",
        )
        == expected
    )
    canonical = hashlib.sha256(b"intake canonical identity").hexdigest()
    assert (
        image_analysis.image_source_revision(
            image_path=str(image),
            channel_id=-123,
            message_id=7,
            text="original",
            canonical_revision=canonical,
            media_sha256=digest,
        )
        == canonical
    )
    image.write_bytes(b"different image")
    with pytest.raises(ValueError, match="media"):
        image_analysis.image_source_revision(
            image_path=str(image),
            channel_id=-123,
            message_id=7,
            text="original",
            canonical_revision=canonical,
            media_sha256=digest,
        )


def test_original_caption_veto_cannot_be_overridden_by_model():
    data = {
        "message": "Valid entry",
        "setup": {
            "action": "ENTER",
            "asset": "ETH",
            "side": "SHORT",
            "entry": 2400,
            "stop_loss": 2450,
            "take_profits": [2300],
        },
    }
    assert (
        image_analysis.signal_from_image_json(
            data,
            message_id=7,
            source_revision=hashlib.sha256(b"source").hexdigest(),
            original_text="Do not enter. Wait for confirmation.",
        )
        is None
    )
