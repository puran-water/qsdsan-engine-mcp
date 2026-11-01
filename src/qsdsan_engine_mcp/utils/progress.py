"""
Progress reporting helpers for long-running simulations.

These utilities keep progress production transport-agnostic:
- CLI/background jobs can print structured `[PROGRESS]` lines to stdout.
- MCP tools can translate the same events to `ctx.report_progress(...)`.

The dynamic ODE solve helper relies on BioSTEAM's `SystemScope`, which tracks
time during dynamic simulation. A lightweight monitor thread polls the tracked
time and emits coarse-grained progress updates while `system.simulate()` runs.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

ProgressEvent = dict[str, Any]
ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(
    callback: Optional[ProgressCallback],
    *,
    stage: str,
    message: str,
    progress: float | None = None,
    total: float | None = None,
    **metadata: Any,
) -> None:
    """Emit a structured progress event if a callback is configured."""
    if callback is None:
        return

    event: ProgressEvent = {
        "stage": stage,
        "message": message,
    }
    if progress is not None:
        event["progress"] = progress
    if total is not None:
        event["total"] = total
    if metadata:
        event["metadata"] = metadata
    callback(event)


def prefix_progress(
    callback: Optional[ProgressCallback],
    *,
    prefix: str,
    stage_prefix: str | None = None,
) -> Optional[ProgressCallback]:
    """Return a child callback that prefixes messages and stage names."""
    if callback is None:
        return None

    def wrapped(event: ProgressEvent) -> None:
        child = dict(event)
        child["message"] = f"{prefix}: {child.get('message', '')}"
        stage = child.get("stage")
        if stage_prefix and stage:
            child["stage"] = f"{stage_prefix}.{stage}"
        callback(child)

    return wrapped


def stdout_progress(event: ProgressEvent) -> None:
    """Print a structured progress event to stdout for subprocess polling."""
    stage = event.get("stage", "progress")
    message = event.get("message", "").strip()
    progress = event.get("progress")
    total = event.get("total")

    if progress is not None and total not in (None, 0):
        percent = max(0.0, min(100.0, (float(progress) / float(total)) * 100.0))
        suffix = f" ({percent:.0f}% | {float(progress):.1f}/{float(total):.1f})"
    elif progress is not None:
        suffix = f" ({float(progress):.1f})"
    else:
        suffix = ""

    print(f"[PROGRESS] {stage}: {message}{suffix}", flush=True)


def run_with_dynamic_progress(
    *,
    system: Any,
    total_days: float,
    callback: Optional[ProgressCallback],
    run: Callable[[], Any],
    stage: str = "ode_solve",
    poll_interval_seconds: float = 2.0,
    message_template: str = "ODE solve in progress",
) -> Any:
    """Run a blocking dynamic simulation while polling BioSTEAM tracking state.

    The monitor reads `system.scope.time_series`, which BioSTEAM updates from
    inside the DAE right-hand side. This is not a formal SciPy callback, but it
    provides a practical signal for long-running solves without patching SciPy.
    """
    if callback is None or total_days <= 0:
        return run()

    stop_event = threading.Event()
    last_reported = {"value": -1.0}

    def _read_latest_time() -> float | None:
        try:
            ts = system.scope.time_series
        except Exception:
            return None
        if ts is None or len(ts) == 0:
            return 0.0
        return float(ts[-1])

    def _monitor() -> None:
        while not stop_event.wait(poll_interval_seconds):
            latest = _read_latest_time()
            if latest is None:
                continue
            latest = max(0.0, min(float(total_days), latest))
            if latest <= last_reported["value"] + 1e-9:
                continue
            last_reported["value"] = latest
            emit_progress(
                callback,
                stage=stage,
                message=message_template,
                progress=latest,
                total=total_days,
            )

    emit_progress(
        callback,
        stage=stage,
        message="Starting ODE solve",
        progress=0.0,
        total=total_days,
    )

    monitor = threading.Thread(
        target=_monitor,
        name=f"{stage}-progress-monitor",
        daemon=True,
    )
    monitor.start()
    try:
        result = run()
    finally:
        stop_event.set()
        monitor.join(timeout=max(1.0, poll_interval_seconds))

    emit_progress(
        callback,
        stage=stage,
        message="ODE solve completed",
        progress=total_days,
        total=total_days,
    )
    return result
