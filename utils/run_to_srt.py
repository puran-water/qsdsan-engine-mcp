"""
SRT-Controlled Steady-State Simulation.

This module provides the main entry point for running simulations
to achieve a target SRT (Sludge Retention Time) at steady state.

Uses bracketed root-finding (brentq) with adaptive bracket expansion
to find the optimal Q_was that achieves the target SRT.

Phase 12: SRT-Controlled Steady-State Simulation
"""

from typing import Dict, List, Optional, Tuple, Any
import logging
import numpy as np

try:
    from scipy.optimize import brentq
except ImportError:
    brentq = None

from .srt_control import (
    calculate_srt,
    compute_q_was_bounds,
    estimate_q_was_for_target_srt,
    get_retained_biomass,
    get_was_biomass_concentration,
    update_wastage_actuator,
    get_influent_flow,
    detect_wastage_streams,
    has_srt_decoupling,
    BIOMASS_IDS,
)
from .run_to_convergence import run_system_to_steady_state

logger = logging.getLogger(__name__)


def run_to_target_srt(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]] = None,
    effluent_streams: Optional[List[Any]] = None,
    convergence_streams: Optional[List[Any]] = None,
    convergence_components: Optional[Dict[str, List[str]]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
    srt_tolerance: float = 0.1,
    max_srt_iterations: int = 10,
    q_was_bounds: Optional[Tuple[float, float]] = None,
    min_time_multiplier: float = 2.0,
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Run simulation until target SRT is achieved at steady state.

    Uses bracketed root-finding (brentq) with adaptive bracket expansion
    to find Q_was that achieves target SRT.

    Outer loop: Adjust Q_was via root-finding until |achieved_SRT - target| < tolerance
    Inner loop: Run to steady state with current Q_was

    Parameters
    ----------
    system : biosteam.System
        The compiled system to simulate.
    target_srt_days : float
        Target SRT in days.
    wastage_streams : List[WasteStream], optional
        WAS streams for SRT calculation. Auto-detected if None.
    effluent_streams : List[WasteStream], optional
        Effluent streams (for clarifier systems with solids loss).
    convergence_streams : List[WasteStream], optional
        Streams to monitor for convergence.
    convergence_components : Dict[str, List[str]], optional
        Components to track per stream for convergence.
    biomass_IDs : List[str], optional
        Biomass component IDs. Auto-selected from model_type if None.
    model_type : str
        Model type for default biomass IDs ('ASM2d', 'ASM1', 'mADM1').
    srt_tolerance : float
        Relative tolerance on achieved SRT (default 0.1 = 10%).
    max_srt_iterations : int
        Maximum Q_was adjustment iterations.
    q_was_bounds : Tuple[float, float], optional
        Min/max Q_was to search (m³/d). Auto-computed from system flows if None.
    min_time_multiplier : float
        Minimum simulation time = multiplier × target_srt_days.
        Default 2.0 ensures SRT dynamics equilibrate.
    **convergence_kwargs
        Additional arguments for run_system_to_steady_state.

    Returns
    -------
    Tuple[float, str, Dict]
        (achieved_srt, status, metrics)
        - achieved_srt: Actual SRT achieved (days)
        - status: 'srt_converged', 'srt_max_iterations', or 'srt_bracket_failed'
        - metrics: Convergence metrics from final simulation
    """
    if brentq is None:
        logger.warning("scipy not available, using iterative search only")
        return _iterative_srt_search(
            system, target_srt_days, wastage_streams, effluent_streams,
            convergence_streams, convergence_components, biomass_IDs,
            model_type, srt_tolerance, max_srt_iterations, q_was_bounds,
            min_time_multiplier, **convergence_kwargs
        )

    min_simulation_time = min_time_multiplier * target_srt_days

    # Auto-detect wastage streams if not provided
    if wastage_streams is None:
        wastage_streams = detect_wastage_streams(system)
        if not wastage_streams:
            logger.warning("No WAS streams detected, SRT calculation may be inaccurate")

    # Get biomass IDs
    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    # Auto-compute bounds using physics-based estimation
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
        logger.info(f"Auto-computed Q_was bounds: ({q_was_bounds[0]:.1f}, {q_was_bounds[1]:.1f}) m³/d")

    # Track evaluation count for efficiency
    eval_count = [0]
    last_metrics = [{}]

    def srt_residual(q_was: float) -> float:
        """Objective function: achieved_SRT - target_SRT."""
        eval_count[0] += 1

        # Handle actuator feasibility failures
        success, msg = update_wastage_actuator(system, q_was, validate=True)
        if not success:
            logger.warning(f"Actuator update failed at Q_was={q_was:.1f}: {msg}")
            # Return large residual to push optimizer away from infeasible region
            mid = (q_was_bounds[0] + q_was_bounds[1]) / 2
            return float('inf') if q_was > mid else float('-inf')

        # Reset system state for fresh simulation
        if hasattr(system, 'reset_cache'):
            system.reset_cache()
        for stream in (convergence_streams or []):
            if hasattr(stream, 'scope') and hasattr(stream.scope, 'reset_cache'):
                stream.scope.reset_cache()

        # Run to steady state with minimum time enforcement
        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            **convergence_kwargs,
        )
        last_metrics[0] = metrics

        # Calculate achieved SRT
        achieved_srt = calculate_srt(
            system, wastage_streams, effluent_streams,
            biomass_IDs=biomass_IDs, model_type=model_type
        )

        logger.debug(f"[Eval {eval_count[0]}] Q_was={q_was:.1f} → SRT={achieved_srt:.1f} (target={target_srt_days})")

        # Handle inf/nan
        if not np.isfinite(achieved_srt):
            mid = (q_was_bounds[0] + q_was_bounds[1]) / 2
            return float('inf') if q_was < mid else float('-inf')

        return achieved_srt - target_srt_days

    # Try bracketed root-finding with adaptive expansion
    q_was_optimal, status = _try_brentq_with_expansion(
        srt_residual, q_was_bounds[0], q_was_bounds[1],
        max_srt_iterations, target_srt_days
    )

    if q_was_optimal is None:
        # Fallback to iterative search
        logger.warning("Root-finding failed, using iterative fallback")
        return _iterative_srt_search(
            system, target_srt_days, wastage_streams, effluent_streams,
            convergence_streams, convergence_components, biomass_IDs,
            model_type, srt_tolerance, max_srt_iterations, q_was_bounds,
            min_simulation_time, **convergence_kwargs
        )

    # Final run with optimal Q_was
    update_wastage_actuator(system, q_was_optimal, validate=False)
    if hasattr(system, 'reset_cache'):
        system.reset_cache()

    converged_at, _, metrics = run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        min_time=min_simulation_time,
        **convergence_kwargs,
    )
    achieved_srt = calculate_srt(
        system, wastage_streams, effluent_streams,
        biomass_IDs=biomass_IDs, model_type=model_type
    )

    # Add SRT info to metrics
    metrics['achieved_srt_days'] = achieved_srt
    metrics['target_srt_days'] = target_srt_days
    metrics['q_was_optimal'] = q_was_optimal
    metrics['srt_iterations'] = eval_count[0]

    # Final tolerance check - verify achieved SRT actually meets tolerance
    srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else float('inf')
    if srt_error > srt_tolerance:
        status = 'srt_tolerance_exceeded'
        logger.warning(f"SRT tolerance not met: achieved={achieved_srt:.1f}, target={target_srt_days}, error={srt_error:.1%}")
    else:
        logger.info(f"SRT converged: {achieved_srt:.1f} days (target={target_srt_days}, Q_was={q_was_optimal:.1f} m³/d)")

    return achieved_srt, status, metrics


def _try_brentq_with_expansion(
    func,
    lo: float,
    hi: float,
    max_iterations: int,
    target_srt: float,
    max_expansions: int = 3,
) -> Tuple[Optional[float], str]:
    """
    Try brentq with progressive bracket expansion.

    Parameters
    ----------
    func : callable
        Objective function f(q_was) = achieved_SRT - target_SRT.
    lo : float
        Lower bound for Q_was.
    hi : float
        Upper bound for Q_was.
    max_iterations : int
        Maximum iterations for root-finding.
    target_srt : float
        Target SRT for logging.
    max_expansions : int
        Maximum bracket expansion attempts.

    Returns
    -------
    Tuple[Optional[float], str]
        (q_was_optimal, status) or (None, 'srt_bracket_failed')
    """
    for expansion in range(max_expansions + 1):
        try:
            # Evaluate at bounds to check if root is bracketed
            f_lo = func(lo)
            f_hi = func(hi)

            logger.debug(f"Bracket check: f({lo:.1f})={f_lo:.1f}, f({hi:.1f})={f_hi:.1f}")

            if not np.isfinite(f_lo) or not np.isfinite(f_hi):
                # Can't evaluate at bounds - try narrower range
                if expansion < max_expansions:
                    lo = lo * 1.5
                    hi = hi * 0.75
                    logger.info(f"Adjusting bounds to ({lo:.1f}, {hi:.1f}) due to inf values")
                    continue
                break

            if np.sign(f_lo) != np.sign(f_hi):
                # Root is bracketed, use brentq
                # Use generous maxiter (at least 20) - brentq is cheap once root is bracketed
                q_optimal = brentq(
                    func, lo, hi,
                    xtol=lo * 0.01,  # 1% tolerance on Q_was
                    maxiter=max(20, max_iterations * 3),
                )
                return q_optimal, 'srt_converged'

            # Root not bracketed - expand bounds
            if expansion < max_expansions:
                if f_lo > 0 and f_hi > 0:
                    # Both SRTs too high → need higher Q_was (more wasting)
                    hi = hi * 2
                    logger.info(f"Both SRTs > target, expanding upper bound to {hi:.1f}")
                elif f_lo < 0 and f_hi < 0:
                    # Both SRTs too low → need lower Q_was (less wasting)
                    lo = max(0.1, lo / 2)
                    logger.info(f"Both SRTs < target, reducing lower bound to {lo:.1f}")

        except ValueError as e:
            logger.warning(f"brentq failed: {e}")
            break

    return None, 'srt_bracket_failed'


def _iterative_srt_search(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]],
    effluent_streams: Optional[List[Any]],
    convergence_streams: Optional[List[Any]],
    convergence_components: Optional[Dict[str, List[str]]],
    biomass_IDs: Optional[List[str]],
    model_type: str,
    srt_tolerance: float,
    max_iterations: int,
    q_was_bounds: Optional[Tuple[float, float]],
    min_simulation_time: float,
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Fallback iterative search when bracketed root-finding fails.

    Uses proportional adjustment with damping.
    """
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )

    if wastage_streams is None:
        wastage_streams = detect_wastage_streams(system)

    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    q_was = (q_was_bounds[0] + q_was_bounds[1]) / 2  # Start at midpoint
    best_srt = float('inf')
    best_q_was = q_was
    best_metrics: Dict[str, Any] = {}

    for iteration in range(max_iterations):
        success, msg = update_wastage_actuator(system, q_was, validate=True)
        if not success:
            q_was = q_was * 0.9  # Reduce if infeasible
            continue

        if hasattr(system, 'reset_cache'):
            system.reset_cache()

        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            **convergence_kwargs,
        )

        achieved_srt = calculate_srt(
            system, wastage_streams, effluent_streams,
            biomass_IDs=biomass_IDs, model_type=model_type
        )
        srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else 1.0

        if srt_error < abs(best_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else True:
            best_srt = achieved_srt
            best_q_was = q_was
            best_metrics = metrics

        if srt_error < srt_tolerance:
            metrics['achieved_srt_days'] = achieved_srt
            metrics['target_srt_days'] = target_srt_days
            metrics['q_was_optimal'] = q_was
            metrics['srt_iterations'] = iteration + 1
            return achieved_srt, 'srt_converged', metrics

        # Damped proportional adjustment
        damping = 0.7
        if achieved_srt > 0 and np.isfinite(achieved_srt):
            q_was = q_was * (1 + damping * (achieved_srt / target_srt_days - 1))
        q_was = max(q_was_bounds[0], min(q_was_bounds[1], q_was))

        logger.info(f"Iteration {iteration+1}: SRT={achieved_srt:.1f} (error={srt_error:.1%}), Q_was→{q_was:.1f}")

    best_metrics['achieved_srt_days'] = best_srt
    best_metrics['target_srt_days'] = target_srt_days
    best_metrics['q_was_optimal'] = best_q_was
    best_metrics['srt_iterations'] = max_iterations

    return best_srt, 'srt_max_iterations', best_metrics
