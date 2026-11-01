"""
SRT-Controlled Steady-State Simulation.

Efficient two-pass approach:
  1. Cold-start convergence with initial Q_was estimate
  2. Analytical Q_was correction from converged biomass inventory
  3. Warm-start confirmation run (reuses system state)

Replaces the previous brentq root-finding loop which required N cold-start
convergence runs (very slow for stiff mASM2d systems).
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
    get_influent_flow,
    update_wastage_actuator,
    detect_wastage_streams,
    get_wastage_split_lock_info,
    get_terminal_streams_by_role,
    BIOMASS_IDS,
    get_biomass_ids,
)
from .run_to_convergence import run_system_to_steady_state
from .progress import ProgressCallback, emit_progress, prefix_progress
from .stream_roles import get_streams_by_role

logger = logging.getLogger(__name__)


def _pre_steady_state_result(
    *,
    target_srt_days: float,
    q_was: Optional[float],
    passes_completed: int,
    converged_at: Optional[float],
    steady_state_status: str,
    metrics: Optional[Dict[str, Any]],
) -> Tuple[float, str, Dict[str, Any]]:
    """Package a controller result when steady state was not reached."""
    result_metrics = metrics or {}
    result_metrics["target_srt_days"] = target_srt_days
    result_metrics["achieved_srt_days"] = None
    result_metrics["q_was_optimal"] = q_was
    result_metrics["srt_iterations"] = passes_completed
    result_metrics["converged_at_days"] = converged_at
    result_metrics["converged_at"] = converged_at
    result_metrics["steady_state_status"] = steady_state_status
    warnings = list(result_metrics.get("warnings") or [])
    warning = (
        "SRT was not calculated because the dynamic simulation did not reach "
        f"steady state (status={steady_state_status!r})."
    )
    if warning not in warnings:
        warnings.append(warning)
    result_metrics["warnings"] = warnings
    return np.nan, "srt_pre_steady_state", result_metrics


def _invalidate_compiled_dynamics(system: Any) -> None:
    """Force warm starts to rebuild dynamic equations after actuator changes.

    QSDsan's dynamic MBR ODE caches ``pumped_flow`` inside the compiled closure.
    When a warm-start pass updates ``Q_was`` with ``state_reset_hook=None``, the
    current unit states should be preserved, but the compiled unit/system
    equations must be invalidated so the next solve picks up the new actuator
    setpoint.
    """
    if getattr(system, 'isdynamic', False) is not True:
        return

    if hasattr(system, '_DAE'):
        system._DAE = None

    for unit in getattr(system, 'units', []):
        if getattr(unit, 'isdynamic', False) is not True:
            continue
        if hasattr(unit, '_ODE'):
            unit._ODE = None
        if hasattr(unit, '_AE'):
            unit._AE = None


def _apply_q_was(system: Any, q_was: float) -> Tuple[bool, str]:
    """Apply the requested WAS flow and surface actuator failures."""
    success, msg = update_wastage_actuator(system, q_was, validate=True)
    if success:
        _invalidate_compiled_dynamics(system)
    else:
        logger.error(f"Failed to apply Q_was={q_was:.2f} m³/d: {msg}")
    return success, msg


def _high_recycle_mbr_ratio(system: Any) -> float:
    """Return the largest MBR retentate recycle ratio relative to influent."""
    q_in = get_influent_flow(system)
    if q_in <= 0:
        return 0.0

    ratio = 0.0
    for unit in getattr(system, "units", []) or []:
        if type(unit).__name__ != "CompletelyMixedMBR":
            continue
        try:
            q_pumped = float(getattr(unit, "pumped_flow", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        ratio = max(ratio, q_pumped / q_in)
    return ratio


def _run_seeded_high_recycle_mbr_control(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]],
    effluent_streams: Optional[List[Any]],
    biomass_IDs: List[str],
    model_type: str,
    srt_tolerance: float,
    max_srt_iterations: int,
    q_was: float,
    progress_callback: Optional[ProgressCallback],
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """Fast SRT controller for seeded high-recycle MBR systems.

    QSDsan's explicit RK path is the only physically stable solver for the
    guarded high-recycle MBR topology, but a full 40-60 d cold solve is too slow
    for controller iteration. The Danone-style seed already contains the design
    biomass inventory; a very short dynamic materialization pass is enough to
    populate unit retained-mass states and the MBR/RAS/WAS boundary streams.
    The controller then solves SRT from the same retained-biomass and WAS
    concentration formula used by the full steady-state path.
    """
    if not hasattr(system, "simulate"):
        return np.nan, "error", {"error": "System does not expose simulate()"}

    from .dynamic_hydraulics import stabilize_dynamic_hydraulics

    max_time = convergence_kwargs.get("max_time")
    init_days = float(convergence_kwargs.get("seeded_initialization_days") or 0.01)
    init_days = min(max(init_days, 0.001), 0.05)
    passes_completed = 0
    last_metrics: Dict[str, Any] = {}

    emit_progress(
        progress_callback,
        stage="srt_seeded_mbr_setup",
        message=(
            "Using seeded high-recycle MBR SRT initialization "
            f"(dynamic materialization {init_days:.3f} d)"
        ),
    )

    for iteration in range(max_srt_iterations):
        passes_completed = iteration + 1
        success, msg = _apply_q_was(system, q_was)
        if not success:
            last_metrics = {"error": msg, "steady_state_status": "error"}
            return np.nan, "error", last_metrics

        try:
            stabilize_dynamic_hydraulics(system)
            system.simulate(
                state_reset_hook="reset_cache",
                t_span=(0.0, init_days),
                t_eval=[0.0, init_days],
                method="RK23",
                max_step=init_days,
            )
        except Exception as exc:
            logger.warning("Seeded high-recycle MBR initialization failed: %s", exc)
            last_metrics = {"error": str(exc), "steady_state_status": "error"}
            return np.nan, "error", last_metrics

        achieved_srt = calculate_srt(
            system,
            wastage_streams,
            effluent_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
        srt_error = (
            abs(achieved_srt - target_srt_days) / target_srt_days
            if target_srt_days > 0
            else float("inf")
        )
        last_metrics = {
            "achieved_srt_days": achieved_srt,
            "target_srt_days": target_srt_days,
            "q_was_optimal": q_was,
            "srt_iterations": passes_completed,
            "steady_state_status": "converged",
            "converged_at_days": init_days,
            "converged_at": init_days,
            "checked_until_days": max_time,
            "method": "seeded_high_recycle_mbr_initialization",
            "srt_controller_mode": "seeded_high_recycle_mbr",
            "initialization_dynamic_days": init_days,
            "high_recycle_mbr_ratio": _high_recycle_mbr_ratio(system),
            "warnings": [
                "High-recycle MBR SRT was evaluated from materialized seeded "
                "dynamic inventory rather than a full RK23 cold solve; the "
                "full explicit 60 d solve is prohibitively slow for controller iteration."
            ],
        }

        emit_progress(
            progress_callback,
            stage="srt_seeded_mbr_result",
            message=(
                f"Seeded MBR SRT pass {passes_completed}: "
                f"{achieved_srt:.2f} d (error {srt_error:.1%})"
            ),
            pass_index=passes_completed,
            achieved_srt_days=achieved_srt,
            srt_error=srt_error,
        )

        if srt_error <= srt_tolerance:
            return achieved_srt, "srt_converged", last_metrics

        q_was_candidate = _analytical_q_was(
            system,
            target_srt_days,
            wastage_streams or [],
            effluent_streams,
            biomass_IDs,
            model_type,
        )
        q_in = get_influent_flow(system)
        if not np.isfinite(q_was_candidate) or q_was_candidate <= 0 or q_was_candidate > q_in:
            last_metrics["warnings"].append(
                f"Analytical seeded-MBR correction produced infeasible Q_was={q_was_candidate:.3f} m3/d."
            )
            return achieved_srt, "srt_tolerance_exceeded", last_metrics
        q_was = q_was_candidate

    return last_metrics.get("achieved_srt_days", np.nan), "srt_max_iterations", last_metrics


def _annotate_split_locked_result(
    *,
    achieved_srt: float,
    status: str,
    metrics: Optional[Dict[str, Any]],
    split_lock_info: Optional[Dict[str, Any]],
    target_srt_days: float,
    srt_tolerance: float,
) -> Tuple[str, Dict[str, Any]]:
    """Surface a clear controller status when a prescribed split limits SRT control."""
    metrics = metrics or {}
    if not split_lock_info:
        return status, metrics

    metrics["split_locked"] = True
    metrics["split_lock"] = split_lock_info

    srt_error = (
        abs(achieved_srt - target_srt_days) / target_srt_days
        if target_srt_days > 0 and np.isfinite(achieved_srt)
        else float("inf")
    )
    if status not in {"error", "srt_pre_steady_state"} and srt_error > srt_tolerance:
        warning = (
            f"SRT target {target_srt_days:.2f} d was not reached because wastage splitter "
            f"{split_lock_info.get('unit_id')} has a prescribed split. The controller preserved "
            "the split and adjusted upstream flow only."
        )
        warnings = list(metrics.get("warnings") or [])
        if warning not in warnings:
            warnings.append(warning)
        metrics["warnings"] = warnings
        status = "srt_split_locked_target_unreachable"

    return status, metrics


def _analytical_q_was(
    system: Any,
    target_srt_days: float,
    wastage_streams: List[Any],
    effluent_streams: Optional[List[Any]],
    biomass_IDs: List[str],
    model_type: str,
) -> float:
    """Compute Q_was from converged biomass inventory.

    SRT = M_retained / (Q_was * C_was + Q_eff * C_eff)

    For MBR (no solids in permeate): Q_was = M_retained / (SRT * C_was)
    """
    try:
        m_retained = get_retained_biomass(system, biomass_IDs, model_type)
        c_was = get_was_biomass_concentration(system, wastage_streams, biomass_IDs, model_type)
    except ValueError as e:
        logger.warning(f"Analytical Q_was unavailable ({e}); falling back to estimate")
        return estimate_q_was_for_target_srt(system, target_srt_days, model_type=model_type)
    if not np.isfinite(m_retained) or m_retained <= 0:
        logger.warning("Analytical Q_was unavailable (non-finite retained biomass); falling back to estimate")
        return estimate_q_was_for_target_srt(system, target_srt_days, model_type=model_type)
    if not np.isfinite(c_was) or c_was <= 1e-9:
        logger.warning(
            "Analytical Q_was unavailable (WAS biomass concentration too small: %.3e kg/m³); "
            "falling back to estimate",
            c_was,
        )
        return estimate_q_was_for_target_srt(system, target_srt_days, model_type=model_type)

    # Account for effluent solids loss if applicable (clarifier systems)
    w_eff = 0.0
    biomass_IDs_tuple = tuple(biomass_IDs)
    if effluent_streams:
        for eff in effluent_streams:
            if eff is None:
                continue
            f_vol = getattr(eff, 'F_vol', 0)  # m³/hr
            if f_vol <= 0:
                continue
            if hasattr(eff, 'composite'):
                c_eff = eff.composite('solids', subgroup=biomass_IDs_tuple)
            else:
                c_eff = sum(
                    eff.imass.get(comp, 0) for comp in biomass_IDs
                ) / f_vol if hasattr(eff, 'imass') else 0.0
            w_eff += f_vol * 24 * max(c_eff, 0.0)  # kg/d lost via effluent

    q_was = max(0.1, (m_retained / target_srt_days - w_eff) / c_was)

    logger.info(
        f"Analytical Q_was: {q_was:.2f} m³/d "
        f"(M_retained={m_retained:.1f} kg, C_was={c_was:.3f} kg/m³, "
        f"W_eff={w_eff:.3f} kg/d, target_SRT={target_srt_days} d)"
    )
    return q_was


def _try_brentq_with_expansion(
    func: Any,
    lo: float,
    hi: float,
    max_iterations: int,
    max_expansions: int = 3,
) -> Tuple[Optional[float], str]:
    """Find a feasible Q_was root by expanding the validated bracket when needed."""
    for expansion in range(max_expansions + 1):
        try:
            f_lo = func(lo)
            f_hi = func(hi)
            logger.debug(f"Bracket check: f({lo:.1f})={f_lo:.1f}, f({hi:.1f})={f_hi:.1f}")

            if not np.isfinite(f_lo) or not np.isfinite(f_hi):
                if expansion < max_expansions:
                    lo = max(0.1, lo / 2)
                    hi = hi * 2
                    continue
                break

            if np.sign(f_lo) != np.sign(f_hi):
                q_optimal = brentq(
                    func,
                    lo,
                    hi,
                    xtol=max(lo * 0.01, 1e-3),
                    maxiter=max(20, max_iterations * 3),
                )
                return q_optimal, 'srt_converged'

            if expansion < max_expansions:
                if f_lo > 0 and f_hi > 0:
                    hi = hi * 2
                elif f_lo < 0 and f_hi < 0:
                    lo = max(0.1, lo / 2)
        except ValueError as e:
            logger.warning(f"brentq failed: {e}")
            break

    return None, 'srt_bracket_failed'


def _prepare_search_run_kwargs(
    convergence_kwargs: Dict[str, Any],
    *,
    min_simulation_time: float,
) -> Tuple[Dict[str, Any], Optional[float], Optional[float]]:
    """Split search kwargs into full-run args plus optional warm-search cap."""
    full_kwargs = dict(convergence_kwargs)
    warm_start_hint = full_kwargs.pop('warm_start_max_time', None)
    validated_search_max_time = full_kwargs.pop('validated_search_max_time', None)
    full_max_time = full_kwargs.get('max_time')
    search_max_time_hint = validated_search_max_time or warm_start_hint
    if (
        search_max_time_hint is not None
        and full_max_time is not None
        and float(search_max_time_hint) > float(full_max_time)
    ):
        search_max_time_hint = float(full_max_time)
    if (
        search_max_time_hint is not None
        and min_simulation_time is not None
        and float(search_max_time_hint) < float(min_simulation_time)
    ):
        search_max_time_hint = float(min_simulation_time)
    return full_kwargs, search_max_time_hint, full_max_time


def _build_validated_search_run_kwargs(
    full_run_kwargs: Dict[str, Any],
    *,
    full_max_time: Optional[float],
    search_max_time_hint: Optional[float],
    previous_convergence_time: Optional[float],
    warm_start: bool,
) -> Tuple[Dict[str, Any], Optional[float]]:
    """Build run kwargs for a validated-search evaluation.

    Warm-start evaluations use a shortened horizon derived from the previous
    observed convergence time. An explicit ``validated_search_max_time`` or
    ``warm_start_max_time`` remains a hard upper bound when provided.
    """
    run_kwargs = dict(full_run_kwargs)
    if warm_start:
        run_kwargs['state_reset_hook'] = None

        dynamic_max_time = None
        if full_max_time is not None and previous_convergence_time is not None and np.isfinite(previous_convergence_time):
            dynamic_max_time = min(float(full_max_time), max(0.0, 2.0 * float(previous_convergence_time)))
        if search_max_time_hint is not None:
            dynamic_max_time = (
                float(search_max_time_hint)
                if dynamic_max_time is None
                else min(float(dynamic_max_time), float(search_max_time_hint))
            )
        if dynamic_max_time is not None:
            run_kwargs['max_time'] = dynamic_max_time
    current_max_time = run_kwargs.get('max_time')
    return run_kwargs, current_max_time


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
    progress_callback: Optional[ProgressCallback] = None,
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """Fallback validated search when the analytical shortcut is infeasible."""
    convergence_kwargs, _, _ = _prepare_search_run_kwargs(
        convergence_kwargs,
        min_simulation_time=min_simulation_time,
    )

    q_in = get_influent_flow(system)
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
    q_was_bounds = (q_was_bounds[0], max(q_was_bounds[0], q_in))

    if wastage_streams is None:
        wastage_streams = detect_wastage_streams(system)

    if biomass_IDs is None:
        biomass_IDs = get_biomass_ids(model_type)

    q_was = (q_was_bounds[0] + q_was_bounds[1]) / 2
    best_srt = float('inf')
    best_q_was = q_was
    best_metrics: Dict[str, Any] = {}

    for iteration in range(max_iterations):
        success, msg = _apply_q_was(system, q_was)
        if not success:
            logger.warning("Validated search rejected Q_was=%.2f m³/d: %s", q_was, msg)
            q_was = max(q_was_bounds[0], q_was * 0.9)
            continue

        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            progress_callback=prefix_progress(
                progress_callback,
                prefix=f"Fallback {iteration + 1}",
                stage_prefix=f"srt.fallback{iteration + 1}",
            ),
            **convergence_kwargs,
        )
        metrics = metrics or {}
        metrics['steady_state_status'] = status

        if status == 'error':
            best_metrics = metrics
            continue
        if status != 'converged':
            best_metrics = metrics
            continue

        achieved_srt = calculate_srt(
            system,
            wastage_streams,
            effluent_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
        srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else 1.0

        if srt_error < (abs(best_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else float('inf')):
            best_srt = achieved_srt
            best_q_was = q_was
            best_metrics = metrics

        if srt_error <= srt_tolerance:
            metrics['achieved_srt_days'] = achieved_srt
            metrics['target_srt_days'] = target_srt_days
            metrics['q_was_optimal'] = q_was
            metrics['srt_iterations'] = iteration + 1
            metrics['converged_at_days'] = converged_at
            metrics['converged_at'] = converged_at
            return achieved_srt, 'srt_converged', metrics

        damping = 0.7
        if achieved_srt > 0 and np.isfinite(achieved_srt):
            q_was = q_was * (1 + damping * (achieved_srt / target_srt_days - 1))
        q_was = max(q_was_bounds[0], min(q_was_bounds[1], q_was))

    best_metrics['achieved_srt_days'] = best_srt
    best_metrics['target_srt_days'] = target_srt_days
    best_metrics['q_was_optimal'] = best_q_was
    best_metrics['srt_iterations'] = max_iterations
    if not np.isfinite(best_srt):
        return _pre_steady_state_result(
            target_srt_days=target_srt_days,
            q_was=best_q_was,
            passes_completed=max_iterations,
            converged_at=best_metrics.get('converged_at_days', best_metrics.get('converged_at')),
            steady_state_status=best_metrics.get('steady_state_status', 'max_time_reached'),
            metrics=best_metrics,
        )
    return best_srt, 'srt_max_iterations', best_metrics


def _run_validated_search(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]],
    effluent_streams: Optional[List[Any]],
    convergence_streams: Optional[List[Any]],
    convergence_components: Optional[Dict[str, List[str]]],
    biomass_IDs: Optional[List[str]],
    model_type: str,
    srt_tolerance: float,
    max_srt_iterations: int,
    q_was_bounds: Optional[Tuple[float, float]],
    min_simulation_time: float,
    progress_callback: Optional[ProgressCallback] = None,
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """Use the pre-analytical validated search when the shortcut leaves the feasible region."""
    full_run_kwargs, search_max_time_hint, full_max_time = _prepare_search_run_kwargs(
        convergence_kwargs,
        min_simulation_time=min_simulation_time,
    )
    q_in = get_influent_flow(system)
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
    q_was_bounds = (q_was_bounds[0], max(q_was_bounds[0], q_in))

    if brentq is None:
        logger.warning("scipy not available; using iterative validated search")
        return _iterative_srt_search(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            effluent_streams=effluent_streams,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
            srt_tolerance=srt_tolerance,
            max_iterations=max_srt_iterations,
            q_was_bounds=q_was_bounds,
            min_simulation_time=min_simulation_time,
            progress_callback=progress_callback,
            **full_run_kwargs,
        )

    eval_count = [0]
    previous_convergence_time = [None]

    def srt_residual(q_was: float) -> float:
        eval_count[0] += 1
        success, msg = _apply_q_was(system, q_was)
        if not success:
            logger.warning(f"Actuator update failed at Q_was={q_was:.1f}: {msg}")
            mid = (q_was_bounds[0] + q_was_bounds[1]) / 2
            return float('inf') if q_was > mid else float('-inf')

        run_kwargs, run_max_time = _build_validated_search_run_kwargs(
            full_run_kwargs,
            full_max_time=full_max_time,
            search_max_time_hint=search_max_time_hint,
            previous_convergence_time=previous_convergence_time[0],
            warm_start=eval_count[0] > 1,
        )

        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            progress_callback=prefix_progress(
                progress_callback,
                prefix=f"Search {eval_count[0]}",
                stage_prefix=f"srt.search{eval_count[0]}",
            ),
            **run_kwargs,
        )
        if status == 'converged' and np.isfinite(converged_at):
            previous_convergence_time[0] = float(converged_at)
        if (
            status == 'max_time_reached'
            and eval_count[0] > 1
            and full_max_time is not None
            and run_max_time is not None
            and float(run_max_time) < float(full_max_time)
        ):
            logger.info(
                "Validated search evaluation %d did not converge within %.1f d; retrying with %.1f d",
                eval_count[0],
                float(run_max_time),
                full_max_time,
            )
            retry_kwargs = dict(full_run_kwargs)
            retry_kwargs['state_reset_hook'] = None
            converged_at, status, metrics = run_system_to_steady_state(
                system=system,
                convergence_streams=convergence_streams,
                convergence_components=convergence_components,
                min_time=min_simulation_time,
                progress_callback=prefix_progress(
                    progress_callback,
                    prefix=f"Search {eval_count[0]} retry",
                    stage_prefix=f"srt.search{eval_count[0]}.retry",
                ),
                **retry_kwargs,
            )
            if status == 'converged' and np.isfinite(converged_at):
                previous_convergence_time[0] = float(converged_at)
        if status == 'error':
            logger.warning("Validated search run failed at Q_was=%.2f m³/d: %s", q_was, metrics.get('error'))
            return np.nan
        if status != 'converged':
            logger.warning(
                "Validated search run at Q_was=%.2f m³/d did not reach steady state "
                "(status=%s); skipping SRT residual",
                q_was,
                status,
            )
            return np.nan

        achieved_srt = calculate_srt(
            system,
            wastage_streams,
            effluent_streams,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
        )
        return achieved_srt - target_srt_days

    q_was_optimal, status = _try_brentq_with_expansion(
        srt_residual,
        q_was_bounds[0],
        q_was_bounds[1],
        max_srt_iterations,
    )
    if q_was_optimal is None:
        logger.warning("Validated root-finding failed; using iterative fallback")
        return _iterative_srt_search(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=wastage_streams,
            effluent_streams=effluent_streams,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            biomass_IDs=biomass_IDs,
            model_type=model_type,
            srt_tolerance=srt_tolerance,
            max_iterations=max_srt_iterations,
            q_was_bounds=q_was_bounds,
            min_simulation_time=min_simulation_time,
            progress_callback=progress_callback,
            **full_run_kwargs,
        )

    success, msg = _apply_q_was(system, q_was_optimal)
    if not success:
        return np.nan, 'error', {'error': msg, 'steady_state_status': 'error'}

    final_run_kwargs, final_run_max_time = _build_validated_search_run_kwargs(
        full_run_kwargs,
        full_max_time=full_max_time,
        search_max_time_hint=search_max_time_hint,
        previous_convergence_time=previous_convergence_time[0],
        warm_start=eval_count[0] > 0,
    )

    converged_at, steady_state_status, metrics = run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        min_time=min_simulation_time,
        progress_callback=prefix_progress(
            progress_callback,
            prefix="Search final",
            stage_prefix="srt.search.final",
        ),
        **final_run_kwargs,
    )
    if (
        steady_state_status == 'max_time_reached'
        and eval_count[0] > 0
        and full_max_time is not None
        and final_run_max_time is not None
        and float(final_run_max_time) < float(full_max_time)
    ):
        retry_kwargs = dict(full_run_kwargs)
        retry_kwargs['state_reset_hook'] = None
        converged_at, steady_state_status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            progress_callback=prefix_progress(
                progress_callback,
                prefix="Search final retry",
                stage_prefix="srt.search.final.retry",
            ),
            **retry_kwargs,
        )
    metrics = metrics or {}
    if steady_state_status != 'converged':
        return _pre_steady_state_result(
            target_srt_days=target_srt_days,
            q_was=q_was_optimal,
            passes_completed=eval_count[0],
            converged_at=converged_at,
            steady_state_status=steady_state_status,
            metrics=metrics,
        )
    achieved_srt = calculate_srt(
        system,
        wastage_streams,
        effluent_streams,
        biomass_IDs=biomass_IDs,
        model_type=model_type,
    )
    metrics['achieved_srt_days'] = achieved_srt
    metrics['target_srt_days'] = target_srt_days
    metrics['q_was_optimal'] = q_was_optimal
    metrics['srt_iterations'] = eval_count[0]
    metrics['converged_at_days'] = converged_at
    metrics['converged_at'] = converged_at
    metrics['steady_state_status'] = steady_state_status
    srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else float('inf')
    return achieved_srt, ('srt_converged' if srt_error <= srt_tolerance else status), metrics


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
    progress_callback: Optional[ProgressCallback] = None,
    **convergence_kwargs,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Run simulation until target SRT is achieved at steady state.

    Efficient two-pass approach:
      Pass 1: Cold-start convergence with heuristic Q_was estimate
      Pass 2: Analytical Q_was correction → warm-start confirmation
      Pass 3 (if needed): One more correction cycle

    Parameters
    ----------
    system : biosteam.System
        The compiled system to simulate.
    target_srt_days : float
        Target SRT in days.
    wastage_streams : list, optional
        WAS streams. Auto-detected if None.
    effluent_streams : list, optional
        Effluent streams (for clarifier systems with solids loss).
    convergence_streams : list, optional
        Streams to monitor for convergence.
    convergence_components : dict, optional
        Components to track per stream.
    biomass_IDs : list, optional
        Biomass component IDs. Auto-selected from model_type if None.
    model_type : str
        Model type for default biomass IDs.
    srt_tolerance : float
        Relative tolerance on achieved SRT (default 0.1 = 10%).
    max_srt_iterations : int
        Maximum correction passes (default 10, but typically converges in 2-3).
    min_time_multiplier : float
        Minimum simulation time = multiplier × target_srt_days.
    **convergence_kwargs
        Passed to ``run_system_to_steady_state``. When provided,
        ``warm_start_max_time`` overrides ``max_time`` only for warm-start
        confirmation passes.

    Returns
    -------
    tuple
        (achieved_srt, status, metrics)
    """
    if not convergence_streams:
        raise ValueError("convergence_streams are required for SRT-controlled runs")

    if convergence_components is None:
        convergence_components = {}

    min_simulation_time = min_time_multiplier * target_srt_days

    if wastage_streams is None:
        wastage_streams = get_streams_by_role(system, "was")
    if effluent_streams is None:
        effluent_streams = get_terminal_streams_by_role(
            system,
            "effluent",
            "permeate",
            exclude_roles=("internal_recycle", "ras", "was", "sludge_return", "sludge"),
        )

    if wastage_streams is None:
        wastage_streams = detect_wastage_streams(system)
        if not wastage_streams:
            logger.warning("No WAS streams detected")

    if biomass_IDs is None:
        biomass_IDs = get_biomass_ids(model_type)

    split_lock_info = get_wastage_split_lock_info(system)

    def finalize(
        achieved: float,
        controller_status: str,
        controller_metrics: Optional[Dict[str, Any]],
    ) -> Tuple[float, str, Dict[str, Any]]:
        controller_status, controller_metrics = _annotate_split_locked_result(
            achieved_srt=achieved,
            status=controller_status,
            metrics=controller_metrics,
            split_lock_info=split_lock_info,
            target_srt_days=target_srt_days,
            srt_tolerance=srt_tolerance,
        )
        return achieved, controller_status, controller_metrics

    def run_validated_search() -> Tuple[float, str, Dict[str, Any]]:
        return finalize(
            *_run_validated_search(
                system=system,
                target_srt_days=target_srt_days,
                wastage_streams=wastage_streams,
                effluent_streams=effluent_streams,
                convergence_streams=convergence_streams,
                convergence_components=convergence_components,
                biomass_IDs=biomass_IDs,
                model_type=model_type,
                srt_tolerance=srt_tolerance,
                max_srt_iterations=max_srt_iterations,
                q_was_bounds=q_was_bounds,
                min_simulation_time=min_simulation_time,
                progress_callback=prefix_progress(
                    progress_callback,
                    prefix="Validated search",
                    stage_prefix="srt.search",
                ),
                **convergence_kwargs,
            )
        )

    warm_start_kwargs = dict(convergence_kwargs)
    warm_start_max_time = warm_start_kwargs.pop('warm_start_max_time', None)
    if warm_start_max_time is not None:
        warm_start_kwargs['max_time'] = warm_start_max_time

    metrics: Dict[str, Any] = {}
    converged_at = None
    conv_status = 'error'
    passes_completed = 0
    last_applied_q_was = None
    emit_progress(
        progress_callback,
        stage="srt_setup",
        message=f"Starting SRT control toward {target_srt_days:.1f} d",
    )

    # --- Pass 1: Cold-start with heuristic Q_was ---
    q_was = estimate_q_was_for_target_srt(
        system, target_srt_days, model_type=model_type,
    )
    logger.info(f"Pass 1 (cold-start): Q_was={q_was:.2f} m³/d")
    emit_progress(
        progress_callback,
        stage="srt_pass_start",
        message=f"Pass 1 cold start with Q_was={q_was:.2f} m3/d",
        pass_index=1,
        q_was_m3_d=q_was,
    )

    success, msg = _apply_q_was(system, q_was)
    if not success:
        logger.warning("Initial Q_was estimate was not directly applicable; using validated search")
        return run_validated_search()
    last_applied_q_was = q_was

    if _high_recycle_mbr_ratio(system) >= 1.0:
        logger.info(
            "Using seeded high-recycle MBR SRT controller path "
            "(retentate recycle ratio %.2f x Q_in)",
            _high_recycle_mbr_ratio(system),
        )
        return finalize(
            *_run_seeded_high_recycle_mbr_control(
                system=system,
                target_srt_days=target_srt_days,
                wastage_streams=wastage_streams,
                effluent_streams=effluent_streams,
                biomass_IDs=biomass_IDs,
                model_type=model_type,
                srt_tolerance=srt_tolerance,
                max_srt_iterations=max_srt_iterations,
                q_was=q_was,
                progress_callback=progress_callback,
                **convergence_kwargs,
            )
        )

    converged_at, conv_status, metrics = run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        min_time=min_simulation_time,
        progress_callback=prefix_progress(
            progress_callback,
            prefix="Pass 1",
            stage_prefix="srt.pass1",
        ),
        **convergence_kwargs,
    )
    metrics = metrics or {}
    passes_completed = 1

    if conv_status == 'error':
        metrics['error'] = metrics.get('error', 'Initial steady-state simulation failed')
        metrics['steady_state_status'] = conv_status
        return finalize(np.nan, 'error', metrics)
    if conv_status != 'converged':
        return finalize(
            *_pre_steady_state_result(
                target_srt_days=target_srt_days,
                q_was=last_applied_q_was,
                passes_completed=passes_completed,
                converged_at=converged_at,
                steady_state_status=conv_status,
                metrics=metrics,
            )
        )

    achieved_srt = calculate_srt(
        system, wastage_streams, effluent_streams,
        biomass_IDs=biomass_IDs, model_type=model_type,
    )
    srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else 1.0
    logger.info(f"Pass 1 result: SRT={achieved_srt:.1f}d (target={target_srt_days}d, error={srt_error:.1%})")
    emit_progress(
        progress_callback,
        stage="srt_pass_result",
        message=f"Pass 1 result: achieved SRT {achieved_srt:.1f} d (error {srt_error:.1%})",
        pass_index=1,
        achieved_srt_days=achieved_srt,
        srt_error=srt_error,
    )

    # --- Passes 2+: Analytical correction with warm-start ---
    for iteration in range(1, max_srt_iterations):
        if srt_error <= srt_tolerance:
            break

        # Analytical Q_was from converged biomass
        q_was_candidate = _analytical_q_was(
            system, target_srt_days, wastage_streams,
            effluent_streams, biomass_IDs, model_type,
        )
        if (
            last_applied_q_was is not None
            and np.isclose(q_was_candidate, last_applied_q_was, rtol=1e-3, atol=0.1)
            and srt_error > srt_tolerance
        ):
            logger.warning(
                "Analytical SRT correction stalled at Q_was=%.2f m³/d after pass %d "
                "(previous %.2f m³/d, achieved SRT %.2f d); switching to validated search",
                q_was_candidate,
                iteration + 1,
                last_applied_q_was,
                achieved_srt,
            )
            return run_validated_search()

        q_was = q_was_candidate
        logger.info(f"Pass {iteration + 1} (warm-start): Q_was={q_was:.2f} m³/d")
        q_in = get_influent_flow(system)
        q_lower_bound = q_was_bounds[0] if q_was_bounds is not None else 0.1
        q_upper_hint = q_was_bounds[1] if q_was_bounds is not None else q_in
        if not np.isfinite(q_was) or q_was < q_lower_bound or q_was > q_in:
            logger.warning(
                "Analytical correction produced infeasible Q_was=%.2f m³/d "
                "(bounds %.2f..%.2f, q_in=%.2f); falling back to validated search",
                q_was,
                q_lower_bound,
                q_upper_hint,
                q_in,
            )
            return run_validated_search()
        emit_progress(
            progress_callback,
            stage="srt_pass_start",
            message=f"Pass {iteration + 1} warm start with Q_was={q_was:.2f} m3/d",
            pass_index=iteration + 1,
            q_was_m3_d=q_was,
        )

        success, msg = _apply_q_was(system, q_was)
        if not success:
            logger.warning("Warm-start Q_was application failed; falling back to validated search")
            return run_validated_search()

        converged_at, conv_status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            state_reset_hook=None,
            progress_callback=prefix_progress(
                progress_callback,
                prefix=f"Pass {iteration + 1}",
                stage_prefix=f"srt.pass{iteration + 1}",
            ),
            **warm_start_kwargs,
        )
        metrics = metrics or {}
        passes_completed = iteration + 1

        if conv_status == 'error':
            logger.warning("Warm-start simulation failed, retrying with cold-start reset")
            converged_at, conv_status, metrics = run_system_to_steady_state(
                system=system,
                convergence_streams=convergence_streams,
                convergence_components=convergence_components,
                min_time=min_simulation_time,
                progress_callback=prefix_progress(
                    progress_callback,
                    prefix=f"Pass {iteration + 1} cold retry",
                    stage_prefix=f"srt.pass{iteration + 1}.retry",
                ),
                **convergence_kwargs,
            )
            metrics = metrics or {}
            if conv_status == 'error':
                metrics['error'] = metrics.get('error', 'Warm-start and cold-start confirmation failed')
                metrics['steady_state_status'] = conv_status
                return finalize(np.nan, 'error', metrics)
        if conv_status != 'converged':
            return finalize(
                *_pre_steady_state_result(
                    target_srt_days=target_srt_days,
                    q_was=q_was,
                    passes_completed=passes_completed,
                    converged_at=converged_at,
                    steady_state_status=conv_status,
                    metrics=metrics,
                )
            )
        last_applied_q_was = q_was

        achieved_srt = calculate_srt(
            system, wastage_streams, effluent_streams,
            biomass_IDs=biomass_IDs, model_type=model_type,
        )
        srt_error = abs(achieved_srt - target_srt_days) / target_srt_days if target_srt_days > 0 else 1.0
        logger.info(f"Pass {iteration + 1} result: SRT={achieved_srt:.1f}d (error={srt_error:.1%})")
        emit_progress(
            progress_callback,
            stage="srt_pass_result",
            message=(
                f"Pass {iteration + 1} result: achieved SRT {achieved_srt:.1f} d "
                f"(error {srt_error:.1%})"
            ),
            pass_index=iteration + 1,
            achieved_srt_days=achieved_srt,
            srt_error=srt_error,
        )

    # --- Package results ---
    status = 'srt_converged' if srt_error <= srt_tolerance else 'srt_tolerance_exceeded'
    if srt_error > srt_tolerance:
        logger.warning(
            f"SRT tolerance not met after {passes_completed} passes: "
            f"achieved={achieved_srt:.1f}d, target={target_srt_days}d, error={srt_error:.1%}"
        )
    else:
        logger.info(
            f"SRT converged in {passes_completed} passes: "
            f"{achieved_srt:.1f}d (target={target_srt_days}d, Q_was={q_was:.1f} m³/d)"
        )

    metrics['achieved_srt_days'] = achieved_srt
    metrics['target_srt_days'] = target_srt_days
    metrics['q_was_optimal'] = q_was
    metrics['srt_iterations'] = passes_completed
    metrics['converged_at_days'] = converged_at
    metrics['converged_at'] = converged_at
    metrics['steady_state_status'] = conv_status
    achieved_srt, status, metrics = finalize(achieved_srt, status, metrics)
    emit_progress(
        progress_callback,
        stage="srt_complete",
        message=(
            f"SRT control finished after {passes_completed} pass(es): "
            f"{achieved_srt:.1f} d ({status})"
        ),
        passes_completed=passes_completed,
        achieved_srt_days=achieved_srt,
        target_srt_days=target_srt_days,
        status=status,
    )

    return achieved_srt, status, metrics
