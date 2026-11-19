"""X intake: mappings must be exact, and a late post must not look fresh."""

from datetime import UTC, datetime, timedelta

import pytest

from fatty_trader.intake.x_poll import (
    XAccount,
    XSettings,
    XPost,
    _poll_account,
    message_row,
    parse_accounts,
    parse_posts,
    revision_hash,
)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Client:
    def __init__(self, tweets):
        self._tweets = tweets
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params or {}))
        if url.endswith("/users/by/username/alpha"):
            return _Response(200, {"data": {"id": "42"}})
        if "/users/42/tweets" in url:
            return _Response(200, self._tweets)
        return _Response(404, {})


class _Cursor:
    def __init__(self):
        self.executed = []
        self.rowcount = 0

    def execute(self, statement, params=None):
        self.executed.append((statement, params))
        if statement.strip().startswith("INSERT INTO telegram_messages"):
            self.rowcount = 1
        else:
            self.rowcount = 1

    def fetchone(self):
        return None


def _settings():
    return XSettings(
        token="t", accounts=(XAccount("alpha", -2000000001),), poll_seconds=45,
        media_root=__import__("pathlib").Path("."),
    )


def test_accounts_require_negative_channels():
    accounts = parse_accounts("@Alpha:-2000000001, beta: -2000000002")

    assert [account.handle for account in accounts] == ["alpha", "beta"]
    with pytest.raises(ValueError):
        parse_accounts("alpha:123")
    with pytest.raises(ValueError):
        parse_accounts("alpha")


def test_posts_use_photo_media_only_and_skip_unusable_rows():
    payload = {
        "data": [
            {
                "id": "1002",
                "text": "$WLD LONG",
                "created_at": "2026-11-19T12:00:00Z",
                "attachments": {"media_keys": ["m1"]},
            },
            {"id": "1001", "text": "no timestamp"},
            {
                "id": "1003",
                "text": "chart attached",
                "created_at": "2026-11-19T12:01:00Z",
                "attachments": {"media_keys": ["m2"]},
            },
        ],
        "includes": {
            "media": [
                {"media_key": "m1", "type": "photo", "url": "https://img/1.jpg"},
                {
                    "media_key": "m2",
                    "type": "video",
                    "preview_image_url": "https://img/2.jpg",
                },
            ]
        },
    }

    posts = parse_posts(payload)

    assert [post.post_id for post in posts] == [1002, 1003]
    assert posts[0].media_url == "https://img/1.jpg"
    assert posts[1].media_url is None


def test_revision_changes_when_the_media_changes():
    assert revision_hash("same text") != revision_hash("same text", "abc")


def test_late_posts_age_out_of_the_entry_window():
    account = XAccount("alpha", -2000000001)
    post = XPost(1002, "$WLD LONG ENTRY 1", datetime(2026, 11, 19, 12, 0, tzinfo=UTC), None)

    fresh = message_row(account, post, now=post.created_at + timedelta(seconds=30))
    late = message_row(account, post, now=post.created_at + timedelta(minutes=30))

    assert fresh["ingestion_origin"] == "realtime"
    assert late["ingestion_origin"] == "catchup"
    assert late["received_at"] == post.created_at
    assert late["channel_id"] == account.channel_id
    assert late["message_id"] == post.post_id


@pytest.mark.asyncio
async def test_poll_inserts_new_posts_and_advances_the_cursor():
    cursor = _Cursor()
    client = _Client(
        {
            "data": [
                {
                    "id": "5001",
                    "text": "$BLESS LONG ENTRY 0.0114 STOP 0.0109",
                    "created_at": "2026-11-19T12:00:00Z",
                },
                {
                    "id": "5002",
                    "text": "GM",
                    "created_at": "2026-11-19T12:01:00Z",
                },
            ]
        }
    )

    inserted = await _poll_account(client, cursor, _settings(), XAccount("alpha", -2000000001))

    assert inserted == 2
    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "INSERT INTO telegram_messages" in statements
    assert "x_poll_state" in statements
    saved = [params for statement, params in cursor.executed if "x_poll_state" in statement]
    assert saved[-1][2] == "5002"
    tweets_call = next(call for call in client.calls if "/tweets" in call[0])
    # First poll for this handle: no cursor yet, so the query must not send one.
    assert "since_id" not in tweets_call[1]
