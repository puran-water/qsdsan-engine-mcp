"""
Run-to-convergence simulation wrapper for QSDsan systems.

Provides a model-agnostic wrapper that runs dynamic simulation until steady state
is reached (or max_time is exceeded).

APPROACH: Single-run simulation with post-hoc convergence detection.
For systems with recycles (MLE, A2O with IR), chunked simulation fails because
each simulate() call tries to re-converge the recycle loop. Solution: Run one
long simulation and analyze the timeseries afterward to find convergence point.

Usage:
    from utils.run_to_convergence import run_system_to_steady_state

    converged_at, status, metrics = run_system_to_steady_state(
        system=sys,
        convergence_streams=[eff_stream, was_stream],
        convergence_components={'effluent': ['S_NH4', 'S_NO3'], 'WAS': ['X_AUT', 'X_H']},
        check_interval=2.0,
        atol=0.1,
        rtol=1e-3,
        method='BDF',
        max_time=100.0,
    )
"""

import logging
import inspect
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np

from qsdsan_engine_mcp.utils.convergence import check_steady_state
from qsdsan_engine_mcp.utils.dynamic_hydraulics import hydraulics_are_stabilized, prefers_explicit_recycle_solver, stabilize_dynamic_hydraulics
from qsdsan_engine_mcp.utils.progress import ProgressCallback, emit_progress, run_with_dynamic_progress

if TYPE_CHECKING:
    from biosteam import System
    from qsdsan import WasteStream

logger = logging.getLogger(__name__)


def _simulate_with_optional_max_step(
    system: "System",
    *,
    state_reset_hook: Optional[Any],
    t_span: tuple[float, float],
    t_eval: np.ndarray,
    method: str,
    max_step: float,
) -> None:
    kwargs = {
        "state_reset_hook": state_reset_hook,
        "t_span": t_span,
        "t_eval": t_eval,
        "method": method,
    }
    try:
        signature = inspect.signature(system.simulate)
        accepts_max_step = (
            "max_step" in signature.parameters
            or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        )
    except (TypeError, ValueError):
        accepts_max_step = True
    if accepts_max_step:
        kwargs["max_step"] = max_step
    system.simulate(**kwargs)


def _reset_tracking_scopes(system: "System", convergence_streams: List["WasteStream"]) -> None:
    """Reset tracked histories without clearing the current dynamic state."""
    system_scope = getattr(system, 'scope', None)
    if system_scope is not None and hasattr(system_scope, 'reset_cache'):
        system_scope.reset_cache()
        return

    for stream in convergence_streams:
        if stream and hasattr(stream, 'scope') and stream.scope is not None:
            stream.scope.reset_cache()


def _build_t_eval(duration: float, t_step: float) -> np.ndarray:
    """Return a monotonic evaluation grid that always includes the final time."""
    if duration <= 0:
        return np.array([0.0])

    if t_step <= 0:
        return np.array([0.0, float(duration)])

    t_eval = np.arange(0.0, duration + t_step, t_step)
    t_eval = t_eval[t_eval <= duration + 1e-9]
    if len(t_eval) == 0 or not np.isclose(t_eval[-1], duration):
        t_eval = np.append(t_eval, duration)
    return t_eval


def run_sampled_data_system(
    system: "System",
    *,
    dynamic_controls: Any = None,
    disturbance_schedule: Any = None,
    duration_days: float,
    timestep_days: float,
    method: str = "RK23",
    state_reset_hook: Optional[Any] = "reset_cache",
    t_eval: Optional[List[float] | np.ndarray] = None,
    max_step_days: Optional[float] = None,
    disturbance_step_h: Optional[float] = None,
    start_time_days: float = 0.0,
    resume: bool = False,
    resume_state_hook: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run a dynamic system with sampled-data control and/or feed disturbances.

    The control layer and disturbance schedule both use hours for sample times,
    while BioSTEAM/QSDsan uses days for ``t_span``.  Each segment runs with held
    actuator values and a held disturbed feed, and ends exactly at a sample
    boundary.  At each boundary the controllers re-sample and the disturbance
    schedule re-writes the feed state for the next segment.

    Either ``dynamic_controls`` or ``disturbance_schedule`` (or both) may be
    provided; an open-loop transient (disturbances, no controller) is valid.

    Absolute-time resume
    --------------------
    ``start_time_days`` shifts the integration window to
    ``(start_time_days, start_time_days + duration_days)`` so a checkpointed
    twin continues at its real simulated time (controllers/disturbances are
    evaluated at the absolute hour). When ``resume=True`` the manager/scheduler
    are assumed to already carry restored state (built objects passed in via
    ``dynamic_controls`` / ``disturbance_schedule``): the driver does NOT
    re-initialize the controller (which would re-apply a t=0 boundary action --
    the double-application trap). It re-stabilizes hydraulics and re-applies the
    feed disturbance for the resumed segment (deterministic ``level_at``; the
    checkpoint was taken *before* the boundary apply for the resumed segment),
    and forces ``state_reset_hook=None`` so the restored state is never cleared.

    State reconciliation (resume only)
    ----------------------------------
    ``resume_state_hook`` (B3) is a ``callable(system) -> Any`` invoked ONCE on
    the restored state, inside the ``resume=True`` branch, AFTER hydraulics are
    re-stabilized and the feed disturbance is applied, and BEFORE the first
    segment. That ordering means reconciliation projects the B1-restored state
    toward current SCADA/lab observations exactly once, and no later hydraulic
    pass perturbs it. It is never called when ``resume=False`` -- a cold start's
    behavior is unchanged.
    """
    from qsdsan_engine_mcp.core.control import build_control_manager
    from qsdsan_engine_mcp.core.disturbance import build_disturbance_scheduler

    manager = build_control_manager(dynamic_controls)
    scheduler = build_disturbance_scheduler(disturbance_schedule)
    if manager is None and scheduler is None:
        return {
            "enabled": False,
            "reason": "no dynamic control loops or feed disturbances configured",
        }

    duration_days = float(duration_days)
    timestep_days = float(timestep_days)
    start_time_days = float(start_time_days)
    if duration_days < 0:
        raise ValueError("duration_days cannot be negative")
    if start_time_days < 0:
        raise ValueError("start_time_days cannot be negative")
    if timestep_days <= 0:
        timestep_days = duration_days if duration_days > 0 else 1.0

    end_time_days = start_time_days + duration_days

    if t_eval is None:
        eval_grid = _build_t_eval(duration_days, timestep_days) + start_time_days
    else:
        eval_grid = np.asarray(t_eval, dtype=float)
        eval_grid = eval_grid[
            (eval_grid >= start_time_days - 1e-12) & (eval_grid <= end_time_days + 1e-12)
        ]
        eval_grid = np.unique(np.clip(eval_grid, start_time_days, end_time_days))
        if len(eval_grid) == 0 or not np.isclose(eval_grid[-1], end_time_days):
            eval_grid = np.append(eval_grid, end_time_days)

    # The segment cadence is the finest of the controller sample time and the
    # disturbance resolution. Disturbances default to the output timestep.
    candidate_steps_days: List[float] = []
    if manager is not None:
        control_step = manager.sample_time_h / 24.0
        if control_step <= 0:
            raise ValueError("dynamic control sample time must be positive")
        candidate_steps_days.append(control_step)
    if scheduler is not None:
        if disturbance_step_h is not None and disturbance_step_h > 0:
            candidate_steps_days.append(float(disturbance_step_h) / 24.0)
        else:
            candidate_steps_days.append(timestep_days)
    segment_step_days = min(candidate_steps_days)
    if segment_step_days <= 0:
        raise ValueError("sample-and-hold segment step must be positive")
    segment_max_step = min(
        segment_step_days,
        float(max_step_days) if max_step_days is not None and max_step_days > 0 else segment_step_days,
    )

    start_h = start_time_days * 24.0
    if resume:
        # The manager/scheduler already carry restored state (built objects
        # passed in). Do NOT re-initialize the controller -- a fresh
        # initialize() would re-sample and re-apply a boundary action at the
        # resume time, double-counting the action the checkpointed run already
        # performed.
        #
        # #17: restore_dynamic_checkpoint is the SINGLE owner of the resume-path
        # hydraulic stabilization -- it converges the recycle ONCE and overlays
        # the exact captured frozen flows + restored Q. Re-stabilizing here would
        # re-converge the recycle from the restored fixed point and DRIFT it
        # (~0.4%), so the MCP/CLI resume would no longer reproduce the in-process
        # bit-identical resume. Trust the restored stabilization; stabilize only
        # as a fallback when the guards were never installed (a resume on a
        # system that was not checkpoint-restored -- not the production path).
        if not hydraulics_are_stabilized(system):
            stabilize_dynamic_hydraulics(system)
        if scheduler is not None:
            scheduler.apply(start_h, system)
        # B3: project the restored state toward current observations ONCE, after
        # hydraulics/disturbance are settled and before any segment integrates.
        if resume_state_hook is not None:
            resume_state_hook(system)
    else:
        # Establish baselines and apply any t=0 disturbances before the first segment.
        if scheduler is not None:
            scheduler.initialize(system)
        if manager is not None:
            manager.initialize(system, t_h=start_h)
        stabilize_dynamic_hydraulics(system)
        if scheduler is not None:
            scheduler.apply(start_h, system)

    def _assemble(segments: int) -> Dict[str, Any]:
        # Sync each mADM1 digester's vapor-outlet thermo flow from its live
        # headspace gas-slice so the biogas RATE is reported live, not the stale
        # steady-state 0 the guarded dynamic path otherwise leaves on the outlet
        # (roadmap follow-up #20). Defensive: never raises into a completed run.
        try:
            from qsdsan_engine_mcp.models.reactors import sync_madm1_biogas_outlets

            sync_madm1_biogas_outlets(system)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("biogas outlet sync skipped after sampled run: %s", exc)

        results: Dict[str, Any] = {"enabled": True}
        if manager is not None:
            results.update(manager.as_results())
        if scheduler is not None:
            results["disturbances"] = scheduler.as_results()
        results["driver"] = {
            "mode": "sample_and_hold",
            "method": method,
            "duration_days": duration_days,
            "start_time_days": start_time_days,
            "end_time_days": end_time_days,
            "resume": resume,
            "sample_time_h": segment_step_days * 24.0,
            "controls_enabled": manager is not None,
            "disturbances_enabled": scheduler is not None,
            "max_step_days": segment_max_step,
            "segments": segments,
        }
        return results

    if duration_days == 0:
        if manager is not None:
            manager.finalize(end_time_days * 24.0)
        return _assemble(0)

    t0 = start_time_days
    first_segment = True
    segments = 0
    while t0 < end_time_days - 1e-12:
        t1 = min(end_time_days, t0 + segment_step_days)
        segment_eval = eval_grid[(eval_grid >= t0 - 1e-12) & (eval_grid <= t1 + 1e-12)]
        if len(segment_eval) == 0 or not np.isclose(segment_eval[-1], t1):
            segment_eval = np.append(segment_eval, t1)
        segment_eval = np.unique(np.clip(segment_eval, t0, t1))

        # On resume the restored state must never be cleared: force None.
        segment_reset = None if (resume or not first_segment) else state_reset_hook
        _simulate_with_optional_max_step(
            system,
            state_reset_hook=segment_reset,
            t_span=(t0, t1),
            t_eval=segment_eval,
            method=method,
            max_step=segment_max_step,
        )
        t0 = t1
        first_segment = False
        segments += 1
        if manager is not None:
            manager.update(t0 * 24.0, system)
        if scheduler is not None and t0 < end_time_days - 1e-12:
            scheduler.apply(t0 * 24.0, system)

    if manager is not None:
        manager.finalize(end_time_days * 24.0)
    return _assemble(segments)


def run_system_to_steady_state(
    system: "System",
    convergence_streams: List["WasteStream"],
    convergence_components: Dict[str, List[str]],
    check_interval: float = 2.0,
    t_step: float = 0.5,
    atol: float = 0.1,
    rtol: float = 1e-3,
    floor: float = 1.0,
    method: str = 'BDF',
    state_reset_hook: Optional[Any] = 'reset_cache',
    max_time: Optional[float] = None,
    min_time: Optional[float] = None,
    window_days: float = 5.0,
    check_oscillation: bool = True,
    oscillation_threshold: float = 0.1,
    progress_callback: Optional[ProgressCallback] = None,
    dynamic_controls: Optional[Any] = None,
    disturbance_schedule: Optional[Any] = None,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Run simulation until true steady state (or max_time).

    Uses a single dynamic simulation with post-hoc convergence detection.
    This avoids recycle re-convergence churn between short chunks while still
    reporting the earliest convergence time detectable from the recorded history.

    Parameters
    ----------
    system : System
        QSDsan/BioSTEAM System object (must have dynamic tracking set up)
    convergence_streams : List[WasteStream]
        Streams to check for convergence (effluent, WAS, etc.)
    convergence_components : Dict[str, List[str]]
        Components to check per stream {stream_id: [comp_ids]}
    check_interval : float
        How often to check convergence when scanning timeseries (days)
    t_step : float
        Simulation timestep (days)
    atol : float
        Absolute tolerance for convergence (mg/L/d for ASM, kg/m³/d for ADM)
    rtol : float
        Relative tolerance for convergence
    floor : float
        Minimum value for rtol scaling
    method : str
        ODE solver method ('BDF' recommended for steady-state, 'RK23' for transient)
    state_reset_hook : Optional[Any]
        Hook passed to ``system.simulate`` before the dynamic run. Use
        ``'reset_cache'`` for cold starts or ``None`` to preserve the current
        dynamic state for warm starts while still resetting tracking scopes.
    max_time : Optional[float]
        Maximum simulation time (days). Default: estimated from system (4-5x SRT)
    min_time : Optional[float]
        Minimum simulation time before convergence can be reported (days).
        Used for SRT control to ensure sufficient equilibration time.
    window_days : float
        Time window for slope calculation (days)
    check_oscillation : bool
        Whether to check for oscillations
    oscillation_threshold : float
        Max range/mean ratio before flagging oscillation

    Returns
    -------
    Tuple[float, str, Dict]
        (converged_at_days, status, convergence_metrics) where:
        - converged_at_days: Time in days when converged (or stopped)
        - status: 'converged', 'max_time_reached', or 'error'
        - convergence_metrics: Detailed metrics from final convergence check
    """
    if max_time is None:
        max_time = _estimate_default_max_time(system)
        logger.info(f"Using default max_time: {max_time:.0f} days (estimated from system)")

    logger.info(
        f"Starting run-to-convergence: method={method}, max_time={max_time:.0f}d, "
        f"check_interval={check_interval}d"
    )
    emit_progress(
        progress_callback,
        stage="steady_state_setup",
        message=(
            f"Preparing steady-state run with {len(convergence_streams)} tracked streams "
            f"(method={method}, max_time={max_time:.1f} d)"
        ),
        progress=0.0,
        total=max_time,
    )

    _reset_tracking_scopes(system, convergence_streams)
    stabilize_dynamic_hydraulics(system)
    if str(method).upper() == "BDF" and prefers_explicit_recycle_solver(system):
        logger.warning(
            "Using RK23 for guarded high-recycle MBR hydraulics; dense-output BDF "
            "is nonphysical for this QSDsan recycle topology"
        )
        method = "RK23"
    t_eval = _build_t_eval(max_time, t_step)
    logger.info(f"Running simulation for {max_time:.0f} days with {len(t_eval)} evaluation points")

    actual_method = method
    control_results = None
    try:
        if dynamic_controls or disturbance_schedule:
            control_results = run_sampled_data_system(
                system,
                dynamic_controls=dynamic_controls,
                disturbance_schedule=disturbance_schedule,
                duration_days=max_time,
                timestep_days=t_step,
                method=method,
                state_reset_hook=state_reset_hook,
                t_eval=t_eval,
                max_step_days=t_step,
            )
        else:
            run_with_dynamic_progress(
                system=system,
                total_days=max_time,
                callback=progress_callback,
                stage="ode_solve",
                message_template="Steady-state ODE solve in progress",
                run=lambda: _simulate_with_optional_max_step(
                    system,
                    state_reset_hook=state_reset_hook,
                    t_span=(0, max_time),
                    t_eval=t_eval,
                    method=method,
                    max_step=t_step,
                ),
            )
    except Exception as e:
        error_str = str(e).lower()
        if str(method).upper() == "BDF" and (
            "invalid value" in error_str
            or "could not converge" in error_str
            or isinstance(e, FloatingPointError)
        ):
            logger.warning(f"BDF failed ({e}), retrying with RK23")
            _reset_tracking_scopes(system, convergence_streams)
            try:
                emit_progress(
                    progress_callback,
                    stage="ode_solve_fallback",
                    message=f"BDF failed, retrying with RK23 ({e})",
                    progress=0.0,
                    total=max_time,
                )
                if dynamic_controls or disturbance_schedule:
                    control_results = run_sampled_data_system(
                        system,
                        dynamic_controls=dynamic_controls,
                        disturbance_schedule=disturbance_schedule,
                        duration_days=max_time,
                        timestep_days=t_step,
                        method="RK23",
                        state_reset_hook=state_reset_hook,
                        t_eval=t_eval,
                        max_step_days=t_step,
                    )
                else:
                    run_with_dynamic_progress(
                        system=system,
                        total_days=max_time,
                        callback=progress_callback,
                        stage="ode_solve_fallback",
                        message_template="Fallback RK23 ODE solve in progress",
                        run=lambda: _simulate_with_optional_max_step(
                            system,
                            state_reset_hook=state_reset_hook,
                            t_span=(0, max_time),
                            t_eval=t_eval,
                            method='RK23',
                            max_step=t_step,
                        ),
                    )
                actual_method = 'RK23'
            except Exception as e2:
                logger.error(f"Simulation failed with RK23: {e2}")
                emit_progress(
                    progress_callback,
                    stage="steady_state_failed",
                    message=f"Simulation failed with RK23: {e2}",
                )
                return 0.0, 'error', {"error": str(e2), "method": "RK23"}
        else:
            logger.error(f"Simulation failed: {e}")
            emit_progress(
                progress_callback,
                stage="steady_state_failed",
                message=f"Simulation failed: {e}",
            )
            return 0.0, 'error', {"error": str(e), "method": method}

    logger.info(f"Simulation completed with {actual_method}, analyzing timeseries for convergence...")
    emit_progress(
        progress_callback,
        stage="convergence_scan",
        message="ODE solve completed, scanning recorded histories for convergence",
        progress=max_time,
        total=max_time,
    )

    earliest_check = window_days
    if min_time is not None:
        earliest_check = max(window_days, min_time)
        logger.info(f"min_time={min_time:.0f}d set, convergence check starts at t={earliest_check:.0f}d")

    converged_at = None
    final_metrics = None
    check_times = np.arange(earliest_check, max_time + check_interval, check_interval)

    for t_check in check_times:
        converged, metrics = check_steady_state(
            tracked_streams=convergence_streams,
            components=convergence_components,
            window_days=window_days,
            t_step=t_step,
            atol=atol,
            rtol=rtol,
            floor=floor,
            check_oscillation=check_oscillation,
            oscillation_threshold=oscillation_threshold,
            end_time=t_check,
        )
        final_metrics = metrics

        emit_progress(
            progress_callback,
            stage="convergence_scan",
            message=(
                f"Checked convergence at t={t_check:.1f} d: "
                f"{'converged' if converged else 'not converged'}"
            ),
            progress=min(float(max_time), float(t_check)),
            total=max_time,
        )

        if converged and converged_at is None:
            converged_at = float(t_check)
            logger.info(f"Detected convergence at t={t_check:.1f} days")
        elif not converged and converged_at is not None:
            logger.debug(f"Lost convergence at t={t_check:.1f} days, continuing...")
            converged_at = None

    if final_metrics is not None:
        final_metrics["method"] = actual_method
        final_metrics["convergence_detected_at_days"] = converged_at
        final_metrics["checked_until_days"] = max_time
        if control_results is not None:
            final_metrics["dynamic_controls"] = control_results
            if isinstance(control_results, dict) and "disturbances" in control_results:
                final_metrics["disturbances"] = control_results["disturbances"]

    if converged_at is not None:
        logger.info(f"Converged to steady state at t={converged_at:.1f} days")
        emit_progress(
            progress_callback,
            stage="steady_state_complete",
            message=f"System converged at t={converged_at:.1f} d",
            progress=max_time,
            total=max_time,
        )
        return converged_at, 'converged', final_metrics

    logger.warning(
        f"Reached max_time={max_time:.1f} days without sustained convergence. "
        f"Last metrics: {final_metrics.get('message', 'unknown') if final_metrics else 'unknown'}"
    )
    emit_progress(
        progress_callback,
        stage="steady_state_complete",
        message=f"Reached max_time={max_time:.1f} d without sustained convergence",
        progress=max_time,
        total=max_time,
    )
    return max_time, 'max_time_reached', final_metrics


def _estimate_default_max_time(system: "System") -> float:
    """
    Estimate default max_time based on system characteristics.

    Uses heuristics:
    - If system has MBR/clarifier, estimate SRT and use 4-5x SRT
    - Anaerobic systems need longer (100-500+ days)
    - Aerobic systems typically 50-100 days

    Parameters
    ----------
    system : System
        QSDsan/BioSTEAM System

    Returns
    -------
    float
        Estimated max_time in days
    """
    # Check for system characteristics
    is_anaerobic = False
    has_mbr = False
    total_volume = 0.0

    for unit in system.units:
        unit_type = type(unit).__name__

        if 'Anaerobic' in unit_type or 'ADM' in unit_type or 'mADM' in unit_type:
            is_anaerobic = True

        if 'MBR' in unit_type:
            has_mbr = True

        # Sum up reactor volumes for SRT estimation
        if hasattr(unit, 'V_max') and unit.V_max:
            total_volume += unit.V_max
        elif hasattr(unit, 'V_liq') and unit.V_liq:
            total_volume += unit.V_liq

    if is_anaerobic:
        # Anaerobic systems need longer - default to 500 days
        # Can take 100-5000 days for complex substrates
        return 500.0

    # Aerobic systems
    if has_mbr:
        # MBR systems typically have SRT 15-25 days, use 5x = 75-125 days
        return 100.0
    else:
        # Conventional AS, estimate SRT ~10-15 days, use 5x = 50-75 days
        return 80.0


def run_aerobic_to_steady_state(
    system: "System",
    convergence_streams: List["WasteStream"],
    model_type: str = "ASM2d",
    include_phosphorus: bool = False,
    **kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Convenience wrapper for aerobic systems with model-aware components.

    Parameters
    ----------
    system : System
        QSDsan/BioSTEAM System
    convergence_streams : List[WasteStream]
        Streams to track (typically effluent + WAS)
    model_type : str
        Model type for component selection ('ASM2d', 'ASM1', 'mASM2d')
    include_phosphorus : bool
        Whether to include phosphorus components (for EBPR)
    **kwargs
        Additional arguments passed to run_system_to_steady_state

    Returns
    -------
    Tuple[float, str, Dict]
        Same as run_system_to_steady_state
    """
    from qsdsan_engine_mcp.utils.convergence import get_convergence_components_for_model

    # Get model-specific components
    comp_config = get_convergence_components_for_model(model_type, include_phosphorus)

    # Build convergence_components dict based on stream IDs
    convergence_components = {}
    for stream in convergence_streams:
        stream_id = stream.ID.lower()
        if 'effluent' in stream_id or 'eff' in stream_id or 'permeate' in stream_id:
            convergence_components[stream.ID] = comp_config.get('effluent', [])
        elif 'was' in stream_id or 'sludge' in stream_id or 'retain' in stream_id:
            convergence_components[stream.ID] = comp_config.get('sludge', [])
        else:
            # Default to effluent components
            convergence_components[stream.ID] = comp_config.get('effluent', [])

    # Set aerobic defaults
    kwargs.setdefault('method', 'BDF')
    kwargs.setdefault('max_time', 100.0)  # Aerobic default

    return run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        **kwargs,
    )


def run_anaerobic_to_steady_state(
    system: "System",
    convergence_streams: List["WasteStream"],
    model_type: str = "mADM1",
    **kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Convenience wrapper for anaerobic systems with model-aware components.

    Parameters
    ----------
    system : System
        QSDsan/BioSTEAM System
    convergence_streams : List[WasteStream]
        Streams to track (typically effluent + biogas)
    model_type : str
        Model type for component selection ('mADM1', 'ADM1')
    **kwargs
        Additional arguments passed to run_system_to_steady_state

    Returns
    -------
    Tuple[float, str, Dict]
        Same as run_system_to_steady_state
    """
    from qsdsan_engine_mcp.utils.convergence import get_convergence_components_for_model

    # Get model-specific components
    comp_config = get_convergence_components_for_model(model_type)

    # Build convergence_components dict
    convergence_components = {}
    for stream in convergence_streams:
        stream_id = stream.ID.lower()
        if 'gas' in stream_id or 'biogas' in stream_id:
            # Gas stream - check methane production
            convergence_components[stream.ID] = ['S_ch4', 'S_co2'] if 'S_ch4' in comp_config.get('effluent', []) else comp_config.get('effluent', [])
        elif 'sludge' in stream_id or 'waste' in stream_id:
            convergence_components[stream.ID] = comp_config.get('sludge', [])
        else:
            convergence_components[stream.ID] = comp_config.get('effluent', [])

    # Set anaerobic defaults
    kwargs.setdefault('method', 'BDF')
    kwargs.setdefault('max_time', 500.0)  # Anaerobic default - longer

    return run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        **kwargs,
    )
