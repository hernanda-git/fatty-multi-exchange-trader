"""X (Twitter) post intake that feeds the existing analysis pipeline.

Access stopped being the hard part: X bills pay-per-usage at $0.005 per post
read, deduplicated per UTC day, so polling a handful of accounts costs cents.
What remains hard is signal quality, latency, and chart-only posts — and those
are the analyzer's job, not this module's.

A post becomes a durable ``telegram_messages`` row on a synthetic negative
channel id, which is exactly what the analyzer, dispatcher and protection lanes
already consume. Nothing downstream changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from fatty_trader.worker_health import owned_worker_health, worker_progress

MAX_MEDIA_BYTES = 10 * 1024 * 1024
RECENT_SECONDS = 300
_IMAGE_SUFFIX = ".jpg"


@dataclass(frozen=True)
class XAccount:
    handle: str
    channel_id: int


@dataclass(frozen=True)
class XPost:
    post_id: int
    text: str
    created_at: datetime
    media_url: str | None


@dataclass(frozen=True)
class XSettings:
    token: str
    accounts: tuple[XAccount, ...]
    poll_seconds: float
    media_root: Path
    api_base: str = "https://api.x.com/2"

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> XSettings | None:
        token = (environ.get("X_BEARER_TOKEN") or "").strip()
        raw = (environ.get("X_ACCOUNTS") or "").strip()
        if not token or not raw:
            return None
        return cls(
            token=token,
            accounts=parse_accounts(raw),
            poll_seconds=max(15.0, float(environ.get("X_POLL_SECONDS") or "45")),
            media_root=Path(environ.get("X_MEDIA_ROOT") or "/app/runtime/media"),
            api_base=(environ.get("X_API_BASE") or "https://api.x.com/2").rstrip("/"),
        )


def parse_accounts(raw: str) -> tuple[XAccount, ...]:
    """``handle:channel_id`` pairs; channel ids must be negative to stay distinct."""
    accounts: list[XAccount] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        handle, separator, channel = part.partition(":")
        handle = handle.strip().lstrip("@").lower()
        channel = channel.strip()
        if not separator or not handle or not channel.lstrip("-").isdigit():
            raise ValueError("X_ACCOUNTS entries must be handle:channel_id")
        channel_id = int(channel)
        if channel_id >= 0:
            raise ValueError("X account channel ids must be negative")
        accounts.append(XAccount(handle=handle, channel_id=channel_id))
    if not accounts:
        raise ValueError("X_ACCOUNTS must contain at least one account")
    return tuple(accounts)


def parse_posts(payload: Mapping[str, Any]) -> tuple[XPost, ...]:
    """Normalise a ``/users/:id/tweets`` response; unknown shapes are skipped."""
    media: dict[str, str] = {}
    for item in (payload.get("includes") or {}).get("media") or []:
        if not isinstance(item, dict) or item.get("type") != "photo":
            continue
        url = item.get("url") or item.get("preview_image_url")
        key = item.get("media_key")
        if isinstance(key, str) and isinstance(url, str) and url:
            media[key] = url
    posts: list[XPost] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("text"):
            continue
        created = _parse_time(item.get("created_at"))
        if created is None:
            continue
        keys = (item.get("attachments") or {}).get("media_keys") or []
        media_url = next((media[key] for key in keys if key in media), None)
        try:
            post_id = int(item["id"])
        except (TypeError, ValueError):
            continue
        posts.append(XPost(post_id, str(item["text"]), created, media_url))
    return tuple(sorted(posts, key=lambda post: post.post_id))


def revision_hash(text: str, media_sha256: str | None = None) -> str:
    """The same text with different media is a different source revision."""
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    if media_sha256:
        digest.update(b"\0")
        digest.update(media_sha256.encode("ascii"))
    return digest.hexdigest()


def message_row(
    account: XAccount,
    post: XPost,
    *,
    media_sha256: str | None = None,
    media_path: str | None = None,
    media_mime_type: str | None = None,
    media_size_bytes: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The durable row shape the analyzer already reads."""
    moment = now or datetime.now(UTC)
    fresh = moment - post.created_at <= timedelta(seconds=RECENT_SECONDS)
    return {
        "channel_id": account.channel_id,
        "message_id": post.post_id,
        "revision_hash": revision_hash(post.text, media_sha256),
        "received_at": post.created_at,
        "raw_text": post.text,
        "has_media": media_path is not None,
        "media_path": media_path,
        "media_sha256": media_sha256,
        "media_mime_type": media_mime_type,
        "media_size_bytes": media_size_bytes,
        # A post read minutes late must age out of the entry window, not look new.
        "ingestion_origin": "realtime" if fresh else "catchup",
    }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_INSERT = """
INSERT INTO telegram_messages (
    id, channel_id, message_id, revision_hash, received_at, raw_text,
    has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
    intake_state, ingestion_origin
) VALUES (
    gen_random_uuid(), %(channel_id)s, %(message_id)s, %(revision_hash)s,
    %(received_at)s, %(raw_text)s, %(has_media)s, %(media_path)s,
    %(media_sha256)s, %(media_mime_type)s, %(media_size_bytes)s,
    'RECEIVED', %(ingestion_origin)s
) ON CONFLICT (channel_id, message_id, revision_hash) DO NOTHING
"""

_STATE = "SELECT user_id, since_id FROM x_poll_state WHERE handle = %s"
_SAVE_STATE = """
INSERT INTO x_poll_state (handle, user_id, since_id, updated_at)
VALUES (%s, %s, %s, now())
ON CONFLICT (handle) DO UPDATE
SET user_id = EXCLUDED.user_id, since_id = EXCLUDED.since_id, updated_at = now()
"""


@owned_worker_health("x-intake")
async def run_x_intake(environ: Mapping[str, str]) -> None:
    """Poll every configured account forever; inert until credentials exist."""
    import httpx
    import psycopg

    settings = XSettings.from_environ(environ)
    if settings is None:
        print(
            "service=x-intake state=disabled reason=missing-X_BEARER_TOKEN-or-X_ACCOUNTS",
            flush=True,
        )
        while True:
            worker_progress()
            await asyncio.sleep(60)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {settings.token}"}
    async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
        while True:
            inserted = 0
            try:
                with psycopg.connect() as connection:
                    with connection.cursor() as cursor:
                        for account in settings.accounts:
                            inserted += await _poll_account(
                                client, cursor, settings, account
                            )
                    connection.commit()
                print(
                    f"service=x-intake mode=LIVE state=ready accounts={len(settings.accounts)} "
                    f"inserted={inserted}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - the loop must survive one bad cycle
                print(
                    f"service=x-intake state=error error={type(exc).__name__}:{exc}",
                    flush=True,
                )
            await asyncio.sleep(settings.poll_seconds)


async def _poll_account(client: Any, cursor: Any, settings: XSettings, account: XAccount) -> int:
    user_id, since_id = _load_state(cursor, account.handle)
    if user_id is None:
        user_id = await _resolve_user_id(client, settings, account.handle)
        if user_id is None:
            return 0
    url = f"{settings.api_base}/users/{user_id}/tweets"
    params = {
        "max_results": "10",
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,attachments",
        "expansions": "attachments.media_keys",
        "media.fields": "url,preview_image_url,type",
    }
    if since_id:
        params["since_id"] = since_id
    response = await client.get(url, params=params)
    if response.status_code == 429:
        print(f"service=x-intake account={account.handle} state=rate-limited", flush=True)
        return 0
    response.raise_for_status()
    posts = parse_posts(response.json())
    inserted = 0
    newest = int(since_id) if since_id else 0
    for post in posts:
        newest = max(newest, post.post_id)
        media = await _download_media(client, settings, post)
        cursor.execute(_INSERT, message_row(account, post, **media))
        inserted += cursor.rowcount or 0
    if newest:
        _save_state(cursor, account.handle, user_id, str(newest))
    return inserted


async def _resolve_user_id(client: Any, settings: XSettings, handle: str) -> str | None:
    response = await client.get(f"{settings.api_base}/users/by/username/{handle}")
    if response.status_code != 200:
        print(
            f"service=x-intake account={handle} state=unresolved status={response.status_code}",
            flush=True,
        )
        return None
    data = response.json().get("data") or {}
    user_id = str(data.get("id") or "")
    return user_id or None


async def _download_media(client: Any, settings: XSettings, post: XPost) -> dict[str, Any]:
    """Content-addressed, bounded, image-only; failure leaves the post text-only."""
    if not post.media_url:
        return {}
    try:
        response = await client.get(post.media_url)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - media is optional, text still counts
        print(
            f"service=x-intake post={post.post_id} media=failed error={type(exc).__name__}",
            flush=True,
        )
        return {}
    content = response.content
    if not content or len(content) > MAX_MEDIA_BYTES:
        return {}
    digest = hashlib.sha256(content).hexdigest()
    path = settings.media_root / f"{digest}{_IMAGE_SUFFIX}"
    if not path.exists():
        path.write_bytes(content)
    return {
        "media_sha256": digest,
        "media_path": str(path),
        "media_mime_type": "image/jpeg",
        "media_size_bytes": len(content),
    }


def _load_state(cursor: Any, handle: str) -> tuple[str | None, str | None]:
    cursor.execute(_STATE, (handle,))
    row = cursor.fetchone()
    if row is None:
        return None, None
    return (str(row[0]) if row[0] else None, str(row[1]) if row[1] else None)


def _save_state(cursor: Any, handle: str, user_id: str, since_id: str) -> None:
    cursor.execute(_SAVE_STATE, (handle, user_id, since_id))
