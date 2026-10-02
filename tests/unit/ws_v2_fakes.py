"""Scripted doubles for the v2 dual-socket transport tests.

The fakes here are deliberately capable of expressing the failure modes that
matter for fail-closed review:

- ``repeat`` makes the leg replay its ENTIRE script once exhausted, so a leg
  can be modelled as "keeps talking" or "goes silent" independently of the
  other leg. Replaying the whole script (rather than latching one frame as a
  permanent default) matters: a leg that only ever repeats ``pong`` would
  starve the ticker frames the freshness check depends on.
- ``marks=N`` injects N ticker frames, letting a test feed a FRESH public mark
  before breaking the private leg. Without that, a test passes because no marks
  existed rather than because the interlock fired.
- ``_fail_send`` / ``_fail_recv`` model a half-open socket.
"""

import asyncio

from fatty_trader.exchanges.bitget.websocket_v2 import (
    V2_PRIVATE_WS_URL as V2_PRIVATE_URL,
)
from fatty_trader.exchanges.bitget.websocket_v2 import (
    V2_PUBLIC_WS_URL as V2_PUBLIC_URL,
)


class FakeConnection:
    """Minimal scripted WebSocketConnection with a programmable failure mode."""

    def __init__(self, script: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self._orig: list[str] = list(script or [])
        self._script: list[str] = list(self._orig)
        self._loop = False
        self.closed = False
        self._fail_send = False
        self._fail_recv = False
        # A real socket parks in recv() and RETURNS the moment data arrives.
        # A parked reader must therefore wake when a test appends a frame,
        # otherwise the test silently asserts on a leg that can never speak.
        self._data = asyncio.Event()

    def _wake(self) -> None:
        if self._script or self._loop:
            self._data.set()
        else:
            self._data.clear()

    def add(self, frame: str, *, marks: int = 1, repeat: bool = False) -> "FakeConnection":
        """Append frames; ``repeat`` replays the whole script when exhausted."""
        self._script.extend([frame] * marks)
        self._orig.extend([frame] * marks)
        if repeat:
            self._loop = True
        self._wake()
        return self

    async def send(self, value: str) -> None:
        if self._fail_send:
            raise ConnectionResetError("fake send failed")
        self.sent.append(value)

    async def recv(self) -> str:
        if self._fail_recv:
            raise ConnectionResetError("fake recv failed")
        if not self._script:
            if not self._loop:
                # Silent leg: block like a real quiet socket, but wake if the
                # test later appends a frame.
                self._data.clear()
                await self._data.wait()
                if not self._script:
                    await asyncio.sleep(3600)
            else:
                self._script = list(self._orig)
                if not self._script:
                    self._data.clear()
                    await self._data.wait()
        # A real socket always yields to the event loop between frames. Without
        # this the repeat leg busy-loops and starves every other task, which
        # looks exactly like a production hang.
        await asyncio.sleep(0)
        return self._script.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, public_script=None, private_script=None) -> None:
        self.urls: list[str] = []
        self.public = FakeConnection(public_script)
        self.private = FakeConnection(private_script)

    def add_public(self, frame: str, *, marks: int = 1, repeat: bool = False) -> "FakeTransport":
        self.public.add(frame, marks=marks, repeat=repeat)
        return self

    def add_private(self, frame: str, *, marks: int = 1, repeat: bool = False) -> "FakeTransport":
        self.private.add(frame, marks=marks, repeat=repeat)
        return self

    async def connect(self, url: str):
        self.urls.append(url)
        if url == V2_PUBLIC_URL:
            return self.public
        if url == V2_PRIVATE_URL:
            return self.private
        raise AssertionError(f"unexpected url {url}")


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
