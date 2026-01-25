"""
Convergence detection utilities for wastewater simulation systems.

Provides model-agnostic convergence checking with abs+rel tolerance criteria
and oscillation detection. Can be used for both anaerobic (mADM1/ADM1) and
aerobic (ASM2d/ASM1) systems.

Key features:
- Absolute + relative tolerance criteria: |slope| < atol + rtol * max(|mean|, floor)
- Oscillation detection via range check over window
- Window specified in days (not N points) for consistency across t_step values
- Returns detailed metrics for debugging

Usage:
    from utils.convergence import check_steady_state

    converged, metrics = check_steady_state(
        tracked_streams=[eff_stream, was_stream],
        components={'effluent': ['S_NH4', 'S_NO3'], 'WAS': ['X_AUT', 'X_H']},
        window_days=5.0,
        atol=0.1,
        rtol=1e-3,
    )

Reference:
    - Codex Review Session: convergence-based-simulation-plan (2026-01-23)
    - Original mADM1 pattern: utils/simulate_madm1.py:check_steady_state()
"""

import logging
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)

# Default components to track for different model types
DEFAULT_CONVERGENCE_COMPONENTS = {
    "ASM2d": {
        "effluent": ["S_NH4", "S_NO3", "S_O2"],
        "sludge": ["X_AUT", "X_H", "X_PAO"],
    },
    "ASM1": {
        "effluent": ["S_NH", "S_NO", "S_O"],
        "sludge": ["X_B_A", "X_B_H"],
    },
    "mASM2d": {
        "effluent": ["S_NH4", "S_NO3", "S_O2", "S_PO4"],
        "sludge": ["X_AUT", "X_H", "X_PAO", "X_PP"],
    },
    "mADM1": {
        "effluent": ["S_ac", "S_IC", "S_IN"],
        "sludge": ["X_ac", "X_h2", "X_hSRB"],
    },
    "ADM1": {
        "effluent": ["S_ac", "S_IC", "S_IN"],
        "sludge": ["X_ac", "X_h2"],
    },
}


def check_steady_state(
    tracked_streams: List["WasteStream"],
    components: Optional[Dict[str, List[str]]] = None,
    window_days: float = 5.0,
    t_step: float = 0.5,
    atol: float = 0.1,
    rtol: float = 1e-3,
    floor: float = 1.0,
    check_oscillation: bool = True,
    oscillation_threshold: float = 0.1,
    end_time: Optional[float] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Check if system reached steady state using abs+rel criteria.

    Examines the time-series data for tracked streams and calculates dC/dt
    for specified components. Returns True if all components satisfy:
        |slope| < atol + rtol * max(|mean|, floor)

    Also checks for oscillations via range/window check when enabled.

    Parameters
    ----------
    tracked_streams : List[WasteStream]
        Streams with dynamic tracking enabled (must have scope.record data)
    components : Dict[str, List[str]], optional
        Component IDs to check per stream: {stream_id: [comp_ids]}.
        If None, checks all available components.
    window_days : float, optional
        Time window in days for convergence check (default 5.0).
        This is converted to N points based on t_step.
    t_step : float, optional
        Expected time step in days (default 0.5). Used to convert window_days
        to number of points.
    atol : float, optional
        Absolute tolerance for dC/dt (default 0.1 mg/L/d for ASM, kg/m3/d for ADM).
    rtol : float, optional
        Relative tolerance (default 1e-3). Combined criterion uses:
        |slope| < atol + rtol * max(|mean|, floor)
    floor : float, optional
        Minimum value for rtol scaling (default 1.0). Prevents division issues
        when mean is near zero.
    check_oscillation : bool, optional
        Enable oscillation detection via range check (default True).
    oscillation_threshold : float, optional
        Maximum allowed (range/mean) ratio over window (default 0.1 = 10%).
        If range/mean > threshold, system is considered oscillating.
    end_time : float, optional
        If provided, only consider data up to this time point (days).
        Used for post-hoc convergence scanning at different time points.
        If None, uses all available data (default behavior).

    Returns
    -------
    Tuple[bool, Dict]
        (converged, metrics) where:
        - converged: True if all components satisfy convergence criteria
        - metrics: Dict with detailed per-component slopes, status, and diagnostics

    Notes
    -----
    The abs+rel tolerance criterion handles mixed-basis ASM2d systems where:
    - Carbon/biomass components are in mg COD/L (large values ~1000s)
    - Nitrogen components are in mg N/L (smaller values ~10s)
    A single absolute tolerance would be too loose for nitrogen and too tight
    for COD. The combined criterion scales appropriately.

    Example
    -------
    >>> from utils.convergence import check_steady_state
    >>> converged, metrics = check_steady_state(
    ...     tracked_streams=[eff_stream, was_stream],
    ...     components={'effluent': ['S_NH4', 'S_NO3'], 'WAS': ['X_AUT', 'X_H']},
    ...     window_days=5.0,
    ...     atol=0.1,
    ...     rtol=1e-3,
    ... )
    >>> if converged:
    ...     print("System at steady state")
    """
    metrics = {
        "converged": False,
        "streams": {},
        "message": "",
        "window_days": window_days,
        "atol": atol,
        "rtol": rtol,
        "end_time": end_time,  # None means checked at final time
    }

    # Calculate window size in points
    window_points = max(3, int(window_days / t_step) + 1)

    all_converged = True

    for stream in tracked_streams:
        if stream is None:
            continue

        stream_id = stream.ID if hasattr(stream, 'ID') else str(id(stream))
        stream_metrics = {
            "components": {},
            "converged": True,
        }

        # Check if dynamic tracking data is available
        if not hasattr(stream, 'scope') or stream.scope is None:
            logger.warning(f"Stream '{stream_id}' missing dynamic tracking scope")
            stream_metrics["error"] = "No tracking scope"
            stream_metrics["converged"] = False
            all_converged = False
            metrics["streams"][stream_id] = stream_metrics
            continue

        scope = stream.scope
        if not hasattr(scope, 'record') or scope.record is None:
            logger.warning(f"Stream '{stream_id}' has no tracking record")
            stream_metrics["error"] = "No tracking record"
            stream_metrics["converged"] = False
            all_converged = False
            metrics["streams"][stream_id] = stream_metrics
            continue

        # Get time series data
        time_arr = scope.time_series
        record = scope.record

        # Slice data up to end_time if specified (for post-hoc scanning)
        if end_time is not None:
            # Find index where time exceeds end_time
            end_indices = np.where(time_arr <= end_time + 1e-9)[0]
            if len(end_indices) == 0:
                stream_metrics["error"] = f"No data before end_time={end_time}"
                stream_metrics["converged"] = False
                all_converged = False
                metrics["streams"][stream_id] = stream_metrics
                continue
            end_idx = end_indices[-1] + 1  # +1 for slice (exclusive end)
            time_arr = time_arr[:end_idx]
            record = record[:end_idx, :]

        if len(time_arr) < window_points + 1:
            # Not enough data points yet
            stream_metrics["error"] = f"Insufficient data ({len(time_arr)} points, need {window_points + 1})"
            stream_metrics["converged"] = False
            all_converged = False
            metrics["streams"][stream_id] = stream_metrics
            continue

        # Get components to check
        if components and stream_id in components:
            comp_ids = components[stream_id]
        else:
            # Check all components in stream
            comp_ids = list(stream.components.IDs)

        # Filter to valid components
        valid_comp_ids = [c for c in comp_ids if c in stream.components.IDs]

        # CRITICAL: If no valid components, this is an error (not a false convergence)
        if not valid_comp_ids:
            invalid_comps = [c for c in comp_ids if c not in stream.components.IDs]
            stream_metrics["error"] = f"No valid components to check (invalid: {invalid_comps})"
            stream_metrics["converged"] = False
            all_converged = False
            metrics["streams"][stream_id] = stream_metrics
            logger.warning(f"Stream {stream_id}: no valid components - requested {comp_ids}, available {list(stream.components.IDs)[:10]}...")
            continue

        # Check convergence for each component
        for comp_id in valid_comp_ids:
            try:
                comp_idx = stream.components.index(comp_id)
                comp_metrics = _check_component_convergence(
                    time_arr=time_arr,
                    record=record,
                    comp_idx=comp_idx,
                    window_points=window_points,
                    atol=atol,
                    rtol=rtol,
                    floor=floor,
                    check_oscillation=check_oscillation,
                    oscillation_threshold=oscillation_threshold,
                )
                stream_metrics["components"][comp_id] = comp_metrics

                if not comp_metrics["converged"]:
                    stream_metrics["converged"] = False
                    all_converged = False

            except Exception as e:
                logger.warning(f"Error checking {stream_id}/{comp_id}: {e}")
                stream_metrics["components"][comp_id] = {
                    "converged": False,
                    "error": str(e),
                }
                stream_metrics["converged"] = False
                all_converged = False

        metrics["streams"][stream_id] = stream_metrics

    metrics["converged"] = all_converged

    # Build summary message
    if all_converged:
        metrics["message"] = "All components converged"
        logger.info("Convergence check: PASSED - all components at steady state")
    else:
        # Find worst offender for message
        worst_ratio = 0
        worst_comp = None
        for stream_id, stream_data in metrics["streams"].items():
            for comp_id, comp_data in stream_data.get("components", {}).items():
                ratio = comp_data.get("tolerance_ratio", 0)
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    worst_comp = f"{stream_id}/{comp_id}"

        if worst_comp:
            metrics["message"] = f"Not converged - worst: {worst_comp} at {worst_ratio:.1f}x tolerance"
        else:
            metrics["message"] = "Not converged - insufficient data or errors"
        logger.info(f"Convergence check: NOT CONVERGED - {metrics['message']}")

    return all_converged, metrics


def _check_component_convergence(
    time_arr: np.ndarray,
    record: np.ndarray,
    comp_idx: int,
    window_points: int,
    atol: float,
    rtol: float,
    floor: float,
    check_oscillation: bool,
    oscillation_threshold: float,
) -> Dict[str, Any]:
    """
    Check convergence for a single component.

    Returns metrics dict with slope, mean, tolerance, and convergence status.
    """
    # Get last window_points + 1 points for gradient calculation
    recent_indices = slice(-(window_points + 1), None)
    t_recent = time_arr[recent_indices]
    conc_recent = record[recent_indices, comp_idx]

    # Apply rolling average smoothing to reduce solver jitter
    if len(conc_recent) >= 3:
        try:
            from scipy.ndimage import uniform_filter1d
            conc_smoothed = uniform_filter1d(conc_recent, size=3, mode='nearest')
        except ImportError:
            conc_smoothed = conc_recent
    else:
        conc_smoothed = conc_recent

    # Calculate dC/dt using numerical gradient
    dC_dt = np.gradient(conc_smoothed, t_recent)

    # Get statistics
    max_slope = np.max(np.abs(dC_dt))
    mean_conc = np.mean(np.abs(conc_smoothed))
    conc_range = np.max(conc_smoothed) - np.min(conc_smoothed)

    # Calculate combined tolerance
    tolerance = atol + rtol * max(mean_conc, floor)

    # Check slope criterion
    slope_converged = max_slope < tolerance

    # Check oscillation criterion
    oscillation_converged = True
    oscillation_ratio = None
    if check_oscillation and mean_conc > floor:
        oscillation_ratio = conc_range / mean_conc
        oscillation_converged = oscillation_ratio < oscillation_threshold

    converged = slope_converged and oscillation_converged

    return {
        "converged": converged,
        "max_slope": float(max_slope),
        "mean_concentration": float(mean_conc),
        "concentration_range": float(conc_range),
        "tolerance": float(tolerance),
        "tolerance_ratio": float(max_slope / tolerance) if tolerance > 0 else float('inf'),
        "slope_converged": slope_converged,
        "oscillation_converged": oscillation_converged,
        "oscillation_ratio": float(oscillation_ratio) if oscillation_ratio is not None else None,
    }


def get_convergence_components_for_model(
    model_type: str,
    include_phosphorus: bool = False,
) -> Dict[str, List[str]]:
    """
    Get default convergence components for a model type.

    Parameters
    ----------
    model_type : str
        Model type: ASM2d, ASM1, mASM2d, mADM1, ADM1
    include_phosphorus : bool, optional
        Include phosphorus components for EBPR systems (default False).
        Only applies to ASM2d and mASM2d models.

    Returns
    -------
    Dict[str, List[str]]
        Component IDs to check: {"effluent": [...], "sludge": [...]}
    """
    model_upper = model_type.upper() if model_type else "ASM2D"

    # Normalize model name
    if model_upper in ("MADM1", "ADM1"):
        key = "mADM1" if "M" in model_upper else "ADM1"
    elif model_upper == "ASM1":
        key = "ASM1"
    elif model_upper == "MASM2D":
        key = "mASM2d"
    else:
        key = "ASM2d"

    components = {}
    if key in DEFAULT_CONVERGENCE_COMPONENTS:
        # Deep copy to avoid mutating defaults
        for stream_type, comp_list in DEFAULT_CONVERGENCE_COMPONENTS[key].items():
            components[stream_type] = comp_list.copy()
    else:
        components = {"effluent": [], "sludge": []}

    # Add phosphorus components for EBPR systems
    if include_phosphorus and key in ("ASM2d", "mASM2d"):
        if "S_PO4" not in components.get("effluent", []):
            components.setdefault("effluent", []).append("S_PO4")
        if "X_PP" not in components.get("sludge", []):
            components.setdefault("sludge", []).append("X_PP")

    return components


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'check_steady_state',
    'get_convergence_components_for_model',
    'DEFAULT_CONVERGENCE_COMPONENTS',
]
