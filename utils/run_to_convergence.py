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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np

from utils.convergence import check_steady_state

if TYPE_CHECKING:
    from biosteam import System
    from qsdsan import WasteStream

logger = logging.getLogger(__name__)


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
    max_time: Optional[float] = None,
    min_time: Optional[float] = None,
    window_days: float = 5.0,
    check_oscillation: bool = True,
    oscillation_threshold: float = 0.1,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Run simulation until true steady state (or max_time).

    Uses single-run simulation with post-hoc convergence detection.
    This approach works for systems with internal recycles (MLE, A2O)
    where chunked simulation would fail due to recycle convergence issues.

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
    # Set default max_time if not provided (4-5x typical SRT)
    if max_time is None:
        max_time = _estimate_default_max_time(system)
        logger.info(f"Using default max_time: {max_time:.0f} days (estimated from system)")

    logger.info(
        f"Starting run-to-convergence: method={method}, max_time={max_time:.0f}d, "
        f"check_interval={check_interval}d"
    )

    # Reset tracking scopes before simulation
    logger.debug("Resetting stream tracking scopes before simulation")
    for stream in convergence_streams:
        if stream and hasattr(stream, 'scope') and stream.scope is not None:
            stream.scope.reset_cache()

    # Build t_eval for the full simulation
    t_eval = np.arange(0, max_time + t_step, t_step)
    # Ensure we don't overshoot max_time
    t_eval = t_eval[t_eval <= max_time + 1e-9]

    logger.info(f"Running simulation for {max_time:.0f} days with {len(t_eval)} evaluation points")

    # Run simulation
    actual_method = method
    try:
        system.simulate(
            state_reset_hook='reset_cache',
            t_span=(0, max_time),
            t_eval=t_eval,
            method=method,
        )
    except Exception as e:
        error_str = str(e).lower()
        # If BDF fails, try RK23 as fallback
        if str(method).upper() == "BDF" and (
            "invalid value" in error_str
            or "could not converge" in error_str
            or isinstance(e, FloatingPointError)
        ):
            logger.warning(f"BDF failed ({e}), retrying with RK23")
            for stream in convergence_streams:
                if stream and hasattr(stream, 'scope') and stream.scope is not None:
                    stream.scope.reset_cache()
            try:
                system.simulate(
                    state_reset_hook='reset_cache',
                    t_span=(0, max_time),
                    t_eval=t_eval,
                    method='RK23',
                )
                actual_method = 'RK23'
            except Exception as e2:
                logger.error(f"Simulation failed with RK23: {e2}")
                return 0.0, 'error', {"error": str(e2), "method": "RK23"}
        else:
            logger.error(f"Simulation failed: {e}")
            return 0.0, 'error', {"error": str(e), "method": method}

    logger.info(f"Simulation completed with {actual_method}, analyzing timeseries for convergence...")

    # ==========================================================================
    # POST-HOC CONVERGENCE DETECTION: Scan timeseries to find convergence point
    # ==========================================================================
    # Check convergence at each check_interval to find earliest convergence time.
    # Start from window_days (need at least that much data for slope calculation).
    # If min_time is set, don't report convergence until that time is reached.
    # ==========================================================================

    converged_at = None
    final_metrics = None

    # Determine earliest time to start checking
    # Need at least window_days for slope calculation
    # If min_time is set, respect it for SRT equilibration
    earliest_check = window_days
    if min_time is not None:
        earliest_check = max(window_days, min_time)
        logger.info(f"min_time={min_time:.0f}d set, convergence check starts at t={earliest_check:.0f}d")

    # We need to check convergence at different time points using the recorded data
    # The check_steady_state function looks at the last window_days of data
    check_times = np.arange(earliest_check, max_time + check_interval, check_interval)

    for t_check in check_times:
        # Check convergence at this specific time point using data up to t_check
        # CRITICAL: Pass end_time to slice data, otherwise always checks final window
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

        if converged and converged_at is None:
            converged_at = t_check
            logger.info(f"Detected convergence at t={t_check:.1f} days")
            # Don't break - continue to verify it stays converged
        elif not converged:
            # Lost convergence, reset
            if converged_at is not None:
                logger.debug(f"Lost convergence at t={t_check:.1f} days, continuing...")
            converged_at = None

    # Add method used to metrics
    if final_metrics:
        final_metrics["method"] = actual_method

    # Report final status
    if converged_at is not None:
        logger.info(f"Converged to TRUE steady state at t={converged_at:.1f} days")
        return converged_at, 'converged', final_metrics
    else:
        logger.warning(
            f"Reached max_time={max_time:.1f} days without sustained convergence. "
            f"Last metrics: {final_metrics.get('message', 'unknown') if final_metrics else 'unknown'}"
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
    from utils.convergence import get_convergence_components_for_model

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
    from utils.convergence import get_convergence_components_for_model

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
