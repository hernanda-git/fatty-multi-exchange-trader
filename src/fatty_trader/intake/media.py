"""Bounded, content-addressed Telegram media persistence."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_MEDIA_BYTES = 10 * 1024 * 1024


class MediaTooLarge(ValueError):
    """The stream exceeded its bound before the offending bytes were retained."""


class BoundedMediaBuffer(BytesIO):
    """A private Telethon file-like sink; never creates unrestricted temp files."""

    def write(self, data: Any) -> int:
        if self.tell() + len(data) > MAX_MEDIA_BYTES:
            raise MediaTooLarge("media exceeds 10 MiB")
        return super().write(data)

    def truncate(self, size: int | None = None) -> int:
        if (self.tell() if size is None else size) > MAX_MEDIA_BYTES:
            raise MediaTooLarge("media exceeds 10 MiB")
        return super().truncate(size)


@dataclass(frozen=True)
class MediaArtifact:
    path: Path
    sha256: str
    mime_type: str
    size_bytes: int


def persist_media_bytes(
    root: str | Path,
    *,
    channel_id: int,
    message_id: int,
    revision_hash: str,
    mime_type: str,
    data: bytes,
) -> MediaArtifact:
    if mime_type not in _ALLOWED_MIME:
        raise ValueError(f"unsupported media type: {mime_type}")
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise ValueError("media size is empty or exceeds 10 MiB")
    digest = hashlib.sha256(data).hexdigest()
    suffix = mimetypes.guess_extension(mime_type) or ".bin"
    path = Path(root) / "telegram" / str(channel_id) / str(message_id) / f"{revision_hash}{suffix}"
    # Dedicated image-only root: intake's primary group is shared with the RO
    # analyzer. Never broaden existing operator-owned directory permissions.
    root_path = Path(root)
    for directory in (root_path, root_path / "telegram", path.parent.parent, path.parent):
        try:
            directory.mkdir(mode=0o750, parents=True)
        except FileExistsError:
            if not directory.is_dir():
                raise
        else:
            directory.chmod(0o750)  # independent of the intake process's umask
    # Keep incomplete bytes private; atomically publish complete group-readable
    # files without replacing an existing revision (no world access or writes).
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".media-", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(data)
        temporary.chmod(0o640)
        try:
            os.link(temporary, path)
        except FileExistsError:
            with path.open("rb") as existing:
                if existing.read(MAX_MEDIA_BYTES + 1) != data:
                    raise ValueError("media path collision with different content") from None
    finally:
        temporary.unlink(missing_ok=True)
    return MediaArtifact(path, digest, mime_type, len(data))
