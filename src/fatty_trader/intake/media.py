"""Bounded, content-addressed Telegram media persistence."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_MAX_BYTES = 10 * 1024 * 1024


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
    if not data or len(data) > _MAX_BYTES:
        raise ValueError("media size is empty or exceeds 10 MiB")
    digest = hashlib.sha256(data).hexdigest()
    suffix = mimetypes.guess_extension(mime_type) or ".bin"
    path = Path(root) / "telegram" / str(channel_id) / str(message_id) / f"{revision_hash}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise ValueError("media path collision with different content")
    if not path.exists():
        path.write_bytes(data)
    return MediaArtifact(path, digest, mime_type, len(data))
