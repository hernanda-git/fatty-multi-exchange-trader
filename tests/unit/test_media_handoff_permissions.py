"""Dedicated media tree: intake owns writes, its shared group gets reads only."""

import os
import stat
from pathlib import Path

import pytest

from fatty_trader.intake import media
from fatty_trader.intake.media import persist_media_bytes


def test_complete_media_is_group_readable_without_world_access_even_with_private_umask(tmp_path):
    root = tmp_path / "dedicated-media"
    previous = os.umask(0o077)
    try:
        artifact = persist_media_bytes(
            root,
            channel_id=7,
            message_id=1,
            revision_hash="a" * 64,
            mime_type="image/png",
            data=b"synthetic-image",
        )
    finally:
        os.umask(previous)
    assert stat.S_IMODE(artifact.path.stat().st_mode) == 0o640
    for directory in (root, root / "telegram", root / "telegram/7", artifact.path.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750
    assert artifact.path.stat().st_gid == os.getegid()


def test_existing_private_root_permissions_are_not_broadened(tmp_path):
    root = tmp_path / "operator-owned-root"
    root.mkdir(mode=0o700)
    persist_media_bytes(
        root,
        channel_id=7,
        message_id=1,
        revision_hash="a" * 64,
        mime_type="image/png",
        data=b"synthetic-image",
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_incomplete_bytes_remain_private_until_atomic_complete_publication(tmp_path, monkeypatch):
    original_write = Path.write_bytes
    original_link = os.link
    observed = []

    def staged_write(path, data):
        original_write(path, data[:2])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert not list(tmp_path.rglob("*.png"))
        return original_write(path, data)

    def publish(source, destination):
        assert Path(source).read_bytes() == b"complete-image"
        assert stat.S_IMODE(Path(source).stat().st_mode) == 0o640
        assert not Path(destination).exists()
        observed.append(True)
        return original_link(source, destination)

    monkeypatch.setattr(Path, "write_bytes", staged_write)
    monkeypatch.setattr(os, "link", publish)
    artifact = persist_media_bytes(
        tmp_path,
        channel_id=7,
        message_id=1,
        revision_hash="b" * 64,
        mime_type="image/png",
        data=b"complete-image",
    )
    assert observed == [True]
    assert artifact.path.read_bytes() == b"complete-image"
    assert not list(tmp_path.rglob("*.tmp"))


def test_collision_preserves_original_inode_hash_and_permissions(tmp_path):
    arguments = dict(channel_id=7, message_id=1, revision_hash="c" * 64, mime_type="image/png")
    original = persist_media_bytes(tmp_path, data=b"original-image", **arguments)
    inode = original.path.stat().st_ino
    assert persist_media_bytes(tmp_path, data=b"original-image", **arguments) == original
    with pytest.raises(ValueError, match="collision"):
        persist_media_bytes(tmp_path, data=b"different-image", **arguments)
    assert original.path.stat().st_ino == inode
    assert original.path.read_bytes() == b"original-image"
    assert stat.S_IMODE(original.path.stat().st_mode) == 0o640
    assert not list(tmp_path.rglob("*.tmp"))


def test_oversized_publication_creates_no_files(tmp_path):
    with pytest.raises(ValueError, match="exceeds"):
        persist_media_bytes(
            tmp_path,
            channel_id=7,
            message_id=1,
            revision_hash="d" * 64,
            mime_type="image/png",
            data=b"x" * (media.MAX_MEDIA_BYTES + 1),
        )
    assert not list(tmp_path.rglob("*"))
