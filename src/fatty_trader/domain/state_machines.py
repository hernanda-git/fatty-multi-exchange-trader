from fatty_trader.domain.enums import DispatchState


class TransitionError(ValueError):
    """Raised when a durable dispatch state change skips required reconciliation."""


_ALLOWED: dict[DispatchState, set[DispatchState]] = {
    DispatchState.QUEUED: {DispatchState.PREFLIGHT, DispatchState.EXPIRED, DispatchState.REJECTED},
    DispatchState.PREFLIGHT: {
        DispatchState.SIZED,
        DispatchState.REJECTED,
        DispatchState.RETRY_WAIT,
    },
    DispatchState.SIZED: {DispatchState.VALIDATED, DispatchState.REJECTED},
    DispatchState.VALIDATED: {DispatchState.SUBMITTING, DispatchState.REJECTED},
    DispatchState.SUBMITTING: {
        DispatchState.ACKNOWLEDGED,
        DispatchState.UNKNOWN,
        DispatchState.FAILED,
    },
    DispatchState.ACKNOWLEDGED: {
        DispatchState.PENDING_FILL,
        DispatchState.PARTIALLY_FILLED,
        DispatchState.FILLED,
    },
    DispatchState.PENDING_FILL: {
        DispatchState.PARTIALLY_FILLED,
        DispatchState.FILLED,
        DispatchState.CLOSING,
    },
    DispatchState.PARTIALLY_FILLED: {DispatchState.PROTECTING, DispatchState.CLOSING},
    DispatchState.FILLED: {DispatchState.PROTECTING, DispatchState.CLOSING},
    DispatchState.PROTECTING: {
        DispatchState.ACTIVE,
        DispatchState.MANUAL_REVIEW,
        DispatchState.FAILED,
    },
    DispatchState.ACTIVE: {DispatchState.CLOSING, DispatchState.MANUAL_REVIEW},
    DispatchState.CLOSING: {DispatchState.CLOSED, DispatchState.UNKNOWN, DispatchState.FAILED},
    DispatchState.RETRY_WAIT: {DispatchState.PREFLIGHT, DispatchState.FAILED},
    DispatchState.UNKNOWN: {
        DispatchState.ACKNOWLEDGED,
        DispatchState.RETRY_WAIT,
        DispatchState.MANUAL_REVIEW,
    },
}


def transition_dispatch(current: DispatchState, target: DispatchState) -> DispatchState:
    if target not in _ALLOWED.get(current, set()):
        raise TransitionError(f"cannot transition dispatch from {current} to {target}")
    return target
