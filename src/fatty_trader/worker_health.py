"""Local worker progress evidence, never a configuration or provider probe."""

from __future__ import annotations

import json
import math
import os
import socket
import time
import uuid
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any


class WorkerHealth:
    """Owned by the worker; no timer may refresh evidence on its behalf."""

    def __init__(self, name: str, environ: Mapping[str, str]) -> None:
        self.name = name
        self.path = health_path(name, environ)
        self.pid = os.getpid()
        self.identity = _process_identity(self.pid)
        self._lock = Lock()
        self._closed = False
        self.path.unlink(missing_ok=True)  # Restart needs its first completed cycle.
        self.owner_token = uuid.uuid4().hex
        self._stop = Event()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        endpoint = environ.get("WORKER_HEALTH_SOCKET_PATH")
        self._endpoint = Path(endpoint) if endpoint else None
        if self._endpoint is not None:
            # Only this worker mounts this dedicated volume read/write. Web gets
            # metadata-only access; no host PID namespace, Docker socket or secrets.
            self._endpoint.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(self._endpoint))
                self._endpoint.chmod(0o666)  # Different container UIDs may query.
                listener.listen(8)
                listener.settimeout(0.1)
            except BaseException:
                listener.close()
                self._endpoint.unlink(missing_ok=True)
                raise
            self._socket = listener
            self._thread = Thread(target=self._attest_owner, daemon=True)
            self._thread.start()

    def _attest_owner(self) -> None:
        """Validate identity locally; this thread NEVER publishes progress."""
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(0.2)
                try:
                    with connection.makefile("rb") as stream:
                        request = json.loads(stream.readline(4096))
                    with self._lock:
                        valid = (
                            not self._closed
                            and self.path.exists()
                            and request["service"] == self.name
                            and request["pid"] == self.pid
                            and request["identity"] == self.identity
                            and request["owner_token"] == self.owner_token
                            and _process_identity(self.pid) == self.identity
                        )
                    connection.sendall(
                        json.dumps(
                            {
                                "valid": valid,
                                "challenge": request["challenge"],
                            }
                        ).encode()
                        + b"\n"
                    )
                except (OSError, ValueError, KeyError, TypeError, IndexError):
                    pass

    def progress(self) -> None:
        with self._lock:
            if not self._closed:
                self._write_progress()

    def _write_progress(self) -> None:
        payload = {
            "service": self.name,
            "state": "ready",
            "pid": self.pid,
            "identity": self.identity,
            "owner_token": self.owner_token,
            "progress": time.monotonic(),
        }
        temporary = self.path.with_name(f"{self.path.name}.{self.pid}.tmp")
        try:
            temporary.write_text(json.dumps(payload))
            temporary.chmod(0o644)  # Safe metadata, readable by the web container UID.
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.path.unlink(missing_ok=True)
            self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._endpoint is not None:
            self._endpoint.unlink(missing_ok=True)


_current_health: ContextVar[WorkerHealth | None] = ContextVar("worker_health", default=None)


def worker_progress() -> None:
    health = _current_health.get()
    if health is not None:
        health.progress()


def owned_worker_health(
    name: str,
) -> Callable[[Callable[..., Coroutine[Any, Any, None]]], Callable[..., Coroutine[Any, Any, None]]]:
    def decorate(
        worker: Callable[..., Coroutine[Any, Any, None]],
    ) -> Callable[..., Coroutine[Any, Any, None]]:
        @wraps(worker)
        async def owned(environ: Mapping[str, str], **kwargs: Any) -> None:
            health = WorkerHealth(name, environ)
            token = _current_health.set(health)
            try:
                await worker(environ, **kwargs)
            finally:
                health.close()
                _current_health.reset(token)

        return owned

    return decorate


def health_path(name: str, environ: Mapping[str, str]) -> Path:
    return Path(environ.get("WORKER_HEALTH_PATH", f"/tmp/fatty-{name}-health.json"))


def _process_identity(pid: int) -> str:
    # Start ticks disambiguate a dead worker's PID from a later recycled PID.
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rindex(")") + 2 :].split()
    if fields[0] == "Z":
        raise ValueError("worker is a zombie")
    return fields[19]


def check_worker_health(name: str, environ: Mapping[str, str]) -> int:
    try:
        max_age = float(environ.get("WORKER_HEALTH_MAX_AGE_SECONDS", "180"))
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("invalid health age")
        evidence = json.loads(health_path(name, environ).read_text())
        age = time.monotonic() - float(evidence["progress"])
        healthy = (
            evidence["service"] == name
            and evidence["state"] == "ready"
            and evidence["identity"] == _process_identity(int(evidence["pid"]))
            and math.isfinite(age)
            and 0 <= age <= max_age
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        healthy = False
    print(f"service={name} check={'ok' if healthy else 'failed'} runtime-progress", flush=True)
    return 0 if healthy else 1
