"""Shared numerical-physicality diagnostics for biological model variants."""

from __future__ import annotations

from collections import Counter

_PH_SOLVE_FAILURES: Counter[str] = Counter()
_STATE_PROJECTION_FAILURES: Counter[str] = Counter()
_STATE_PROJECTION_MINIMA: dict[str, float] = {}
_STATE_PROJECTION_NONFINITE: Counter[str] = Counter()


def record_ph_solve_failure(model_variant: str) -> None:
    _PH_SOLVE_FAILURES[model_variant] += 1


def ph_solve_failures(model_variant: str | None = None) -> int:
    if model_variant is None:
        return sum(_PH_SOLVE_FAILURES.values())
    return _PH_SOLVE_FAILURES[model_variant]


def ph_solve_failure_breakdown() -> dict[str, int]:
    return dict(_PH_SOLVE_FAILURES)


def reset_ph_solve_failures(model_variant: str | None = None) -> None:
    if model_variant is None:
        _PH_SOLVE_FAILURES.clear()
    else:
        _PH_SOLVE_FAILURES.pop(model_variant, None)


def record_state_projection_failure(
    context: str,
    *,
    minimum_value: float | None = None,
    nonfinite_count: int = 0,
) -> None:
    """Record a state that required a numerical nonnegative projection."""

    key = str(context or "unknown")
    _STATE_PROJECTION_FAILURES[key] += 1
    if minimum_value is not None:
        current = _STATE_PROJECTION_MINIMA.get(key)
        value = float(minimum_value)
        if current is None or value < current:
            _STATE_PROJECTION_MINIMA[key] = value
    if nonfinite_count:
        _STATE_PROJECTION_NONFINITE[key] += int(nonfinite_count)


def state_projection_failures() -> int:
    return sum(_STATE_PROJECTION_FAILURES.values())


def state_projection_failure_report() -> dict[str, object]:
    return {
        "event_count": state_projection_failures(),
        "events_by_context": dict(_STATE_PROJECTION_FAILURES),
        "minimum_value_by_context": dict(_STATE_PROJECTION_MINIMA),
        "nonfinite_count_by_context": dict(_STATE_PROJECTION_NONFINITE),
    }


def reset_state_projection_failures() -> None:
    _STATE_PROJECTION_FAILURES.clear()
    _STATE_PROJECTION_MINIMA.clear()
    _STATE_PROJECTION_NONFINITE.clear()


__all__ = [
    "ph_solve_failure_breakdown",
    "ph_solve_failures",
    "record_ph_solve_failure",
    "record_state_projection_failure",
    "reset_ph_solve_failures",
    "reset_state_projection_failures",
    "state_projection_failure_report",
    "state_projection_failures",
]
