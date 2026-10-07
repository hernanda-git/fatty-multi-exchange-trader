"""Monitor-owned release evidence. No provider connections or mutation authority."""

from __future__ import annotations

import hashlib
import json
import math
import socket
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fatty_trader.worker_health import WorkerHealth, _process_identity

SCHEMA = "bitget-monitor-release-readiness/v1"
DEFAULT_PATH = "/run/fatty-health/service/release.json"


class ReadinessUnavailable(ValueError):
    """Safe fixed diagnostic; never include provider or credential data."""


def _fresh(value: Any, now: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= now - value <= maximum
    )


class MonitorReadinessPublisher(WorkerHealth):
    """Reuse worker identity/challenge lifecycle; only actual cycles publish."""

    def __init__(
        self,
        environ: Mapping[str, str],
        *,
        socket: Any,
        stream: Any = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._owned_socket = socket
        self._owned_stream = stream
        self._environment = environ.get("BITGET_MODE", "").upper()
        key = environ.get("BITGET_API_KEY", "")
        self._credential_binding = hashlib.sha256(key.encode()).hexdigest() if key else None
        self._monitor: dict[str, Any] | None = None
        self._watchdog: dict[str, Any] | None = None
        self._last_payload: dict[str, Any] | None = None
        self._failed = False
        progress_path = environ.get("WORKER_HEALTH_PATH")
        self._progress_path = Path(progress_path) if progress_path else None
        if self._progress_path is not None:
            self._progress_path.unlink(missing_ok=True)
        path = environ.get("BITGET_MONITOR_READINESS_PATH", DEFAULT_PATH)
        endpoint = environ.get(
            "BITGET_MONITOR_READINESS_SOCKET_PATH",
            environ.get("WORKER_HEALTH_SOCKET_PATH", path + ".sock"),
        )
        super().__init__(
            "monitor-bitget", {"WORKER_HEALTH_PATH": path, "WORKER_HEALTH_SOCKET_PATH": endpoint}
        )
        self.publish()

    def monitor_completed(self, report: Any) -> None:
        self._monitor = {
            "clean": (
                getattr(report, "status", None) in {"ok", "kill-switch-latched"}
                and getattr(report, "reasons", None) == ()
            ),
            "observed_at": getattr(report, "observed_at", None),
            "observed_monotonic": getattr(report, "observed_monotonic", None),
            "clock": getattr(report, "clock_observation", None),
        }
        self.publish()

    def watchdog_completed(
        self, report: Any, *, observed_at: float, observed_monotonic: float
    ) -> None:
        self._watchdog = {
            "clean": (
                getattr(report, "status", None) == "HEALTHY"
                and getattr(report, "reasons", None) == ()
            ),
            "observed_at": observed_at,
            "observed_monotonic": observed_monotonic,
        }
        self.publish()

    def _snapshot(self) -> dict[str, Any]:
        now = self._clock()
        get_private = getattr(self._owned_socket, "release_readiness", None)
        private = get_private() if callable(get_private) else {"ready": False}
        clock = (self._monitor or {}).get("clock")
        monitor = {k: v for k, v in (self._monitor or {}).items() if k != "clock"}
        payload = {
            "schema": SCHEMA,
            "service": self.name,
            "environment": self._environment,
            "pid": self.pid,
            "identity": self.identity,
            "owner_token": self.owner_token,
            "credential_binding_sha256": self._credential_binding,
            "progress": now,
            "observed_at": self._wall_clock(),
            "monitor": monitor,
            "watchdog": self._watchdog,
            "private": private,
            "clock": clock,
        }
        reasons = proof_reasons(
            payload,
            monotonic_now=now,
            wall_now=self._wall_clock(),
            max_age_seconds=90,
            pong_max_age_seconds=65,
        )
        if self._failed:
            reasons.append("worker-cycle-failed")
        if getattr(self._owned_stream, "symbol_source_ready", True) is not True:
            reasons.append("symbol-source-unavailable")
        payload.update(ready=not reasons, reasons=reasons)
        return payload

    def publish(self) -> None:
        with self._lock:
            if self._closed:
                return
            payload = self._snapshot()
            temporary = self.path.with_name(f"{self.path.name}.{self.pid}.tmp")
            try:
                temporary.write_text(json.dumps(payload, allow_nan=False))
                temporary.chmod(0o600)
                temporary.replace(self.path)
                self._last_payload = json.loads(json.dumps(payload, allow_nan=False))
                if self._progress_path is not None:
                    if payload["ready"]:
                        health = {
                            k: payload[k] for k in ("service", "pid", "identity", "owner_token")
                        }
                        health.update(
                            state="ready", progress=payload["monitor"]["observed_monotonic"]
                        )
                        progress_tmp = self._progress_path.with_suffix(".tmp")
                        try:
                            progress_tmp.write_text(json.dumps(health, allow_nan=False))
                            progress_tmp.chmod(0o644)
                            progress_tmp.replace(self._progress_path)
                        finally:
                            progress_tmp.unlink(missing_ok=True)
                    else:
                        self._progress_path.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)

    def invalidate(self, reason: str) -> None:
        """Permanently fail this worker generation; no delayed task may revive it."""
        self._failed = True
        self.publish()

    def close(self) -> None:
        super().close()
        if self._progress_path is not None:
            self._progress_path.unlink(missing_ok=True)

    def _attest_owner(self) -> None:
        """Echo fresh challenge with IN-MEMORY proof, never an operator-supplied file.

        The thread cannot rejuvenate cycle or pong times. A reconnect or current
        transport failure vetoes even a still-young formerly ready publication.
        """
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
                        proof = self._last_payload
                        current = self._owned_socket.release_readiness()
                        valid = (
                            not self._closed
                            and proof is not None
                            and not self._failed
                            and getattr(self._owned_stream, "symbol_source_ready", True) is True
                            and request["service"] == self.name
                            and request["pid"] == self.pid
                            and request["identity"] == self.identity
                            and request["owner_token"] == self.owner_token
                            and _process_identity(self.pid) == self.identity
                            and current.get("ready") is True
                            and current.get("connection_generation")
                            == proof["private"].get("connection_generation")
                        )
                        response = {
                            "valid": valid,
                            "challenge": request["challenge"],
                            "proof": proof if valid else None,
                        }
                    connection.sendall(json.dumps(response, allow_nan=False).encode() + b"\n")
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass


def proof_reasons(
    payload: dict[str, Any],
    *,
    monotonic_now: float,
    wall_now: float,
    max_age_seconds: float,
    pong_max_age_seconds: float,
) -> list[str]:
    """Re-age independently observed facts, never just the file publication."""
    reasons = []
    if (
        payload.get("schema") != SCHEMA
        or payload.get("service") != "monitor-bitget"
        or payload.get("environment") != "LIVE"
        or not payload.get("credential_binding_sha256")
    ):
        reasons.append("worker-identity-invalid")
    if not (
        _fresh(payload.get("progress"), monotonic_now, max_age_seconds)
        and _fresh(payload.get("observed_at"), wall_now, max_age_seconds)
    ):
        reasons.append("publication-stale")
    for name in ("monitor", "watchdog"):
        report = payload.get(name) or {}
        if (
            report.get("clean") is not True
            or not _fresh(report.get("observed_monotonic"), monotonic_now, max_age_seconds)
            or not _fresh(report.get("observed_at"), wall_now, max_age_seconds)
        ):
            reasons.append(name + "-cycle-unavailable")
    private = payload.get("private") or {}
    login_at = private.get("login_ack_at")
    if isinstance(login_at, (int, float)):
        for name in ("monitor", "watchdog"):
            observed = (payload.get(name) or {}).get("observed_monotonic")
            if not isinstance(observed, (int, float)) or observed < login_at:
                reasons.append(name + "-cycle-before-current-login")
    from fatty_trader.exchanges.bitget.ws_v2_models import V2_PRIVATE_CHANNELS

    if (
        private.get("ready") is not True
        or not private.get("connection_generation")
        or not _fresh(private.get("login_ack_at"), monotonic_now, float("inf"))
        or private.get("subscription_acks") != sorted(V2_PRIVATE_CHANNELS)
        or not _fresh(private.get("pong_at"), monotonic_now, pong_max_age_seconds)
    ):
        reasons.append("private-stream-unavailable")
    clock = payload.get("clock") or {}
    lower: Any = clock.get("lower_ms")
    upper: Any = clock.get("upper_ms")
    bound: Any = clock.get("bound_ms")
    finite = all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in (lower, upper, bound)
    )
    if (
        not finite
        or clock.get("clean") is not True
        or not (bound >= 0 and -bound <= lower <= upper <= bound)
        or not _fresh(clock.get("observed_monotonic"), monotonic_now, max_age_seconds)
        or not _fresh(clock.get("observed_at"), wall_now, max_age_seconds)
    ):
        reasons.append("clock-interval-unavailable")
    return reasons


def read_monitor_readiness(
    path: str | Path = DEFAULT_PATH,
    *,
    max_age_seconds: float = 90,
    pong_max_age_seconds: float = 65,
    socket_path: str | Path | None = None,
    expected_credential_binding: str | None = None,
    max_clock_skew_ms: float = 10000,
) -> dict[str, Any]:
    """Read authenticated owner response; re-call after final admission-lock waits.

    `expected_credential_binding` is SHA256(API key), INTERNAL ONLY. Consumers
    must compare it to their authenticated provider collector's key binding.
    Never log the returned dictionary or its binding/identity fields.
    """
    try:
        if not all(math.isfinite(v) and v > 0 for v in (max_age_seconds, pong_max_age_seconds)):
            raise ReadinessUnavailable("invalid-readiness-age-policy")
        location = Path(path)
        identity = json.loads(location.read_bytes()[:16384])
        challenge = uuid.uuid4().hex
        request = {k: identity[k] for k in ("service", "pid", "identity", "owner_token")}
        request["challenge"] = challenge
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(socket_path or (str(location) + ".sock")))
            client.sendall(json.dumps(request).encode() + b"\n")
            with client.makefile("rb") as stream:
                response = json.loads(stream.readline(16384))
        if response.get("valid") is not True or response.get("challenge") != challenge:
            raise ReadinessUnavailable("worker-owner-unavailable")
        proof: dict[str, Any] = response["proof"]
        if any(proof[k] != request[k] for k in ("service", "pid", "identity", "owner_token")):
            raise ReadinessUnavailable("worker-owner-mismatch")
        if proof.get("ready") is not True or proof.get("reasons") != []:
            raise ReadinessUnavailable("worker-proof-not-ready")
        if (
            expected_credential_binding is not None
            and proof.get("credential_binding_sha256") != expected_credential_binding
        ):
            raise ReadinessUnavailable("worker-credential-binding-mismatch")
        reasons = proof_reasons(
            proof,
            monotonic_now=time.monotonic(),
            wall_now=time.time(),
            max_age_seconds=max_age_seconds,
            pong_max_age_seconds=pong_max_age_seconds,
        )
        clock = proof.get("clock") or {}
        if (
            not math.isfinite(max_clock_skew_ms)
            or max_clock_skew_ms < 0
            or abs(clock.get("lower_ms", float("inf"))) > max_clock_skew_ms
            or abs(clock.get("upper_ms", float("inf"))) > max_clock_skew_ms
        ):
            reasons.append("clock-policy-exceeded")
        if reasons:
            raise ReadinessUnavailable(reasons[0])
        return proof
    except ReadinessUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ReadinessUnavailable("worker-proof-unavailable") from None
