"""No worker readiness evidence means unverified, never online."""

from test_report_execution_health import render


def test_missing_lifecycle_evidence_cannot_be_online():
    assert "DEGRADED" in render()


def test_explicit_ready_running_baseline_can_be_online():
    text = render(
        services={
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "dispatcher_state": "RUNNING",
            "lifecycle_recovery": "READY",
        }
    )
    assert "🟢 ONLINE" in text


def test_missing_dispatcher_evidence_cannot_be_online():
    text = render(
        services={
            "status": "OK",
            "total": 1,
            "running": 1,
            "healthy": 1,
            "lifecycle_recovery": "READY",
        }
    )
    assert "DEGRADED" in text
