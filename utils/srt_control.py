"""
SRT (Sludge Retention Time) calculation and control utilities.

This module provides:
- SRT calculation using QSDsan's native get_SRT function
- Unit-specific actuator updates (MBR, Clarifier, Splitter)
- Physical feasibility validation
- Physics-based Q_was estimation (no heuristics)

Phase 12: SRT-Controlled Steady-State Simulation

IMPORTANT: This module requires QSDsan. It will fail loudly if QSDsan is not installed.
"""

from typing import Dict, List, Optional, Tuple, Any
import logging

# Fail loudly if QSDsan is not available - no fallbacks
from qsdsan.utils import get_SRT as qsdsan_get_SRT

logger = logging.getLogger(__name__)

# Biomass component IDs per model type
BIOMASS_IDS = {
    'ASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'ASM1': ['X_B_H', 'X_B_A'],
    'mASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'mADM1': [
        # Standard ADM1 biomass
        'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',
        # ADM1p extension
        'X_PAO',
        # SRB biomass (this repo's extension)
        'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB',
    ],
    'ADM1': ['X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2'],
}

# Units with known SRT control actuators (Phase 12B)
# Only these units have controllable Q_was actuators for SRT control
SRT_ACTUATOR_UNITS = {
    'CompletelyMixedMBR',           # pumped_flow actuator
    'FlatBottomCircularClarifier',  # wastage actuator
}

# Design setpoint MLSS concentrations (mg/L) for Q_was estimation
# These are typical operating values used for initial estimation before simulation
SETPOINT_MLSS = {
    # Aerobic systems (ASM2d, ASM1, mASM2d)
    'aerobic_mbr': 8000.0,        # MBR flowsheets: 8,000 mg/L MLSS
    'aerobic_clarifier': 3500.0,  # Secondary clarifier flowsheets: 3,500 mg/L MLSS
    # Anaerobic systems (mADM1, ADM1)
    'anaerobic_mbr': 12500.0,     # AnMBR flowsheets: 12,500 mg/L MLSS
    'anaerobic_clarifier': 8000.0,  # Anaerobic with clarifier (less common)
}

# Model type classification
AEROBIC_MODELS = {'ASM2d', 'ASM1', 'mASM2d'}
ANAEROBIC_MODELS = {'mADM1', 'ADM1'}


def get_influent_flow(system: Any) -> float:
    """
    Get total influent flow rate (m³/d) from system feeds.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    float
        Total influent flow in m³/d.
    """
    q_in = 0.0
    for stream in getattr(system, 'feeds', []):
        if hasattr(stream, 'F_vol'):
            q_in += stream.F_vol * 24  # m³/hr → m³/d
    return q_in if q_in > 0 else 1000.0  # Default fallback


def validate_flow_feasibility(
    q_was: float,
    q_ras: float,
    q_in: float,
) -> Tuple[bool, str]:
    """
    Check physical feasibility of flow rates.

    Only Q_was (waste flow leaving the system) is constrained by mass balance.
    Q_ras is an internal recycle and can be any multiple of Q_in.

    Parameters
    ----------
    q_was : float
        Waste activated sludge flow (m³/d). Must be 0 ≤ Q_was ≤ Q_in.
    q_ras : float
        Return activated sludge flow (m³/d). Internal recycle, no upper limit.
    q_in : float
        Influent flow (m³/d).

    Returns
    -------
    Tuple[bool, str]
        (is_valid, error_message)
    """
    if q_was < 0:
        return False, f"Q_was ({q_was:.1f}) cannot be negative"
    if q_ras < 0:
        return False, f"Q_ras ({q_ras:.1f}) cannot be negative"
    if q_was > q_in:
        return False, f"Q_was ({q_was:.1f}) cannot exceed influent Q_in ({q_in:.1f})"
    return True, ""


def calculate_srt(
    system: Any,
    wastage_streams: Optional[List[Any]] = None,
    effluent_streams: Optional[List[Any]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> float:
    """
    Calculate SRT following QSDsan's methodology.

    SRT = Total Retained Biomass (kg) / Biomass Leaving Rate (kg/d)

    This implementation follows QSDsan's get_SRT logic but handles edge cases
    where get_retained_mass() returns a dict instead of a float.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    wastage_streams : List[WasteStream], optional
        WAS streams leaving the system. If None, uses all liquid/solid products.
    effluent_streams : List[WasteStream], optional
        Effluent streams (for clarifier systems with solids loss).
        Combined with wastage_streams for accurate SRT calculation.
    biomass_IDs : List[str], optional
        Component IDs for biomass. Auto-detected from model_type if None.
    model_type : str
        Model type for default biomass IDs ('ASM2d', 'ASM1', 'mADM1', etc.)

    Returns
    -------
    float
        SRT in days.

    Raises
    ------
    ValueError
        If wasted biomass is zero (system not wasting properly).
    """
    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    # Convert to tuple for QSDsan API compatibility
    biomass_IDs_tuple = tuple(biomass_IDs)

    # Combine wastage and effluent streams for total biomass leaving
    all_wastage = []
    if wastage_streams:
        all_wastage.extend(wastage_streams)
    if effluent_streams:
        all_wastage.extend(effluent_streams)

    # If no wastage streams specified, use all liquid/solid products (QSDsan default)
    if not all_wastage:
        all_wastage = [ws for ws in system.products if ws.phase in ('l', 's')]

    # Calculate wasted biomass rate (kg/d) using QSDsan's composite method
    # Formula: sum(ws.composite('solids', subgroup=biomass_IDs) * ws.F_vol * 24)
    waste = 0.0
    for ws in all_wastage:
        try:
            # composite returns concentration in kg/m³ for given subgroup
            conc = ws.composite('solids', subgroup=biomass_IDs_tuple)
            # F_vol is m³/hr, multiply by 24 for m³/d
            waste += conc * ws.F_vol * 24
        except (KeyError, AttributeError, ValueError):
            # Skip streams that don't have the biomass components
            pass

    if waste == 0:
        raise ValueError(
            f"Wasted biomass is zero. Check that wastage streams contain biomass "
            f"({biomass_IDs_tuple}) and that the system has been simulated."
        )

    # Calculate retained biomass (kg) from all dynamic units
    # Handle both float and dict returns from get_retained_mass
    retain = 0.0
    for unit in system.units:
        if not getattr(unit, 'isdynamic', False):
            continue
        if not hasattr(unit, 'get_retained_mass'):
            continue

        try:
            mass_result = unit.get_retained_mass(biomass_IDs_tuple)
            if isinstance(mass_result, dict):
                # Sum all biomass component masses
                retain += sum(mass_result.values())
            elif mass_result is not None:
                retain += float(mass_result)
        except (KeyError, AttributeError, ValueError, TypeError):
            # Skip units that fail - likely don't have these components
            pass

    if retain == 0:
        raise ValueError(
            f"Retained biomass is zero. Check that system units are dynamic "
            f"and have been simulated."
        )

    srt = retain / waste
    logger.info(f"Calculated SRT: {srt:.2f} days (retain={retain:.2f} kg, waste={waste:.2f} kg/d)")
    return srt


def get_retained_biomass(
    system: Any,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> float:
    """
    Get total retained biomass inventory from all dynamic units.

    Uses QSDsan's get_retained_mass() method on each unit.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    biomass_IDs : List[str], optional
        Component IDs for biomass. Auto-detected from model_type if None.
    model_type : str
        Model type for default biomass IDs.

    Returns
    -------
    float
        Total retained biomass in kg.

    Raises
    ------
    ValueError
        If no biomass found in system (units not initialized).
    """
    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    biomass_IDs_tuple = tuple(biomass_IDs)
    total_biomass = 0.0

    for unit in getattr(system, 'units', []):
        if not getattr(unit, 'isdynamic', False):
            continue
        if not hasattr(unit, 'get_retained_mass'):
            continue

        mass = unit.get_retained_mass(biomass_IDs_tuple)
        if mass is not None:
            total_biomass += float(mass)

    if total_biomass <= 0:
        raise ValueError(
            f"No retained biomass found in system. Ensure units are initialized "
            f"with inoculum and system has been simulated. Biomass IDs: {biomass_IDs_tuple}"
        )

    return total_biomass


def get_was_biomass_concentration(
    system: Any,
    wastage_streams: Optional[List[Any]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> float:
    """
    Get biomass concentration in WAS stream(s).

    Uses QSDsan's composite() method for accurate TSS calculation.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    wastage_streams : List[WasteStream], optional
        WAS streams. Auto-detected if None.
    biomass_IDs : List[str], optional
        Component IDs for biomass.
    model_type : str
        Model type for default biomass IDs.

    Returns
    -------
    float
        Biomass concentration in WAS in kg/m³.

    Raises
    ------
    ValueError
        If WAS concentration cannot be determined.
    """
    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    if wastage_streams is None:
        wastage_streams = detect_wastage_streams(system)

    if not wastage_streams:
        raise ValueError("No WAS streams found in system")

    biomass_IDs_tuple = tuple(biomass_IDs)

    # Calculate flow-weighted average biomass concentration
    total_biomass_flow = 0.0  # kg/hr
    total_vol_flow = 0.0  # m³/hr

    for stream in wastage_streams:
        if not hasattr(stream, 'F_vol') or stream.F_vol <= 0:
            continue

        vol_flow = stream.F_vol  # m³/hr

        # Use QSDsan's composite method for accurate solids
        if hasattr(stream, 'composite'):
            # composite('solids', subgroup=...) returns kg/m³
            conc = stream.composite('solids', subgroup=biomass_IDs_tuple)
        else:
            # Fallback: sum imass for biomass components
            conc = sum(
                stream.imass.get(comp, 0) for comp in biomass_IDs
            ) / vol_flow if vol_flow > 0 else 0

        total_biomass_flow += conc * vol_flow
        total_vol_flow += vol_flow

    if total_vol_flow <= 0:
        raise ValueError(
            f"WAS streams have zero flow. Ensure system has been simulated "
            f"and WAS is being produced."
        )

    c_was = total_biomass_flow / total_vol_flow  # kg/m³

    if c_was <= 0:
        raise ValueError(
            f"WAS biomass concentration is zero. Check that WAS stream contains "
            f"biomass ({biomass_IDs_tuple}), not just permeate/filtrate."
        )

    return c_was


def get_setpoint_mlss(system: Any, model_type: str = 'ASM2d') -> float:
    """
    Get design setpoint MLSS concentration based on system configuration.

    Returns typical operating MLSS values:
    - Aerobic MBR: 8,000 mg/L
    - Aerobic Clarifier: 3,500 mg/L
    - Anaerobic MBR: 12,500 mg/L
    - Anaerobic Clarifier: 8,000 mg/L

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    model_type : str
        Model type ('ASM2d', 'ASM1', 'mASM2d', 'mADM1', 'ADM1').

    Returns
    -------
    float
        Setpoint MLSS in mg/L.
    """
    is_aerobic = model_type in AEROBIC_MODELS
    has_mbr = has_srt_decoupling(system) and any(
        'MBR' in type(u).__name__ for u in getattr(system, 'units', [])
    )

    if is_aerobic:
        if has_mbr:
            return SETPOINT_MLSS['aerobic_mbr']
        else:
            return SETPOINT_MLSS['aerobic_clarifier']
    else:
        if has_mbr:
            return SETPOINT_MLSS['anaerobic_mbr']
        else:
            return SETPOINT_MLSS['anaerobic_clarifier']


def get_total_reactor_volume(system: Any) -> float:
    """
    Get total reactor volume from all dynamic units.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    float
        Total reactor volume in m³.

    Raises
    ------
    ValueError
        If no reactor volumes found.
    """
    total_volume = 0.0

    for unit in getattr(system, 'units', []):
        # Try common volume attributes
        if hasattr(unit, 'V_max') and unit.V_max:
            total_volume += unit.V_max
        elif hasattr(unit, 'V') and unit.V:
            total_volume += unit.V
        elif hasattr(unit, 'volume') and unit.volume:
            total_volume += unit.volume

    if total_volume <= 0:
        raise ValueError("No reactor volumes found in system")

    return total_volume


def estimate_q_was_for_target_srt(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> float:
    """
    Estimate Q_was needed to achieve target SRT using design setpoint values.

    Uses the SRT formula inverted with design MLSS setpoints:
        Q_was = (V_total × MLSS) / (SRT_target × TSS_was)

    Where:
    - V_total = total reactor volume (m³)
    - MLSS = design setpoint MLSS concentration (kg/m³)
    - SRT_target = target SRT (days)
    - TSS_was = WAS solids concentration ≈ MLSS × thickening_factor

    This approach uses typical design values rather than reading from
    potentially uninitialized reactor states.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    target_srt_days : float
        Target SRT in days.
    wastage_streams : List[WasteStream], optional
        Not used (kept for API compatibility).
    biomass_IDs : List[str], optional
        Not used (kept for API compatibility).
    model_type : str
        Model type for MLSS setpoint selection.

    Returns
    -------
    float
        Estimated Q_was in m³/d.
    """
    # Get system characteristics
    v_total = get_total_reactor_volume(system)
    mlss_mg_L = get_setpoint_mlss(system, model_type)
    mlss_kg_m3 = mlss_mg_L / 1000.0  # Convert mg/L to kg/m³

    # Assume WAS is thickened to ~1.2× MLSS concentration
    thickening_factor = 1.2
    tss_was_kg_m3 = mlss_kg_m3 * thickening_factor

    # Total biomass inventory (kg) = Volume (m³) × MLSS (kg/m³)
    m_bio = v_total * mlss_kg_m3

    # Invert SRT formula: Q_was = M_bio / (SRT × TSS_was)
    q_was_est = m_bio / (target_srt_days * tss_was_kg_m3)

    logger.info(
        f"Q_was estimate: {q_was_est:.2f} m³/d "
        f"(V={v_total:.0f} m³, MLSS={mlss_mg_L:.0f} mg/L, SRT_target={target_srt_days} d)"
    )

    return q_was_est


def compute_q_was_bounds(
    system: Any,
    target_srt_days: float,
    wastage_streams: Optional[List[Any]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> Tuple[float, float]:
    """
    Compute Q_was bounds using design setpoint MLSS values.

    Uses the SRT formula inverted with design MLSS to get an initial estimate,
    then sets bounds around that estimate to allow root-finding to converge.

    This approach uses typical operating MLSS setpoints rather than reading
    from reactor states, which may not be initialized before simulation.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    target_srt_days : float
        Target SRT in days.
    wastage_streams : List[WasteStream], optional
        Kept for API compatibility (not used).
    biomass_IDs : List[str], optional
        Kept for API compatibility (not used).
    model_type : str
        Model type for MLSS setpoint selection.

    Returns
    -------
    Tuple[float, float]
        (q_was_min, q_was_max) in m³/d.
    """
    q_in = get_influent_flow(system)

    # Get design-based Q_was estimate using setpoint MLSS values
    q_was_est = estimate_q_was_for_target_srt(
        system=system,
        target_srt_days=target_srt_days,
        model_type=model_type,
    )

    # Set bounds: 0.2× to 5× of physics-based estimate
    # This is much tighter than the old 0.1× to 10× heuristic
    q_was_min = max(0.1, q_was_est * 0.2)  # At least 0.1 m³/d
    q_was_max = min(q_in * 0.5, q_was_est * 5.0)  # At most 50% of Q_in

    # Ensure valid bounds
    if q_was_max <= q_was_min:
        q_was_max = q_was_min * 5.0

    logger.info(
        f"Q_was bounds: [{q_was_min:.2f}, {q_was_max:.2f}] m³/d "
        f"(estimate={q_was_est:.2f} m³/d)"
    )

    return q_was_min, q_was_max


def update_wastage_actuator(
    system: Any,
    q_was: float,
    q_ras: Optional[float] = None,
    validate: bool = True,
) -> Tuple[bool, str]:
    """
    Update WAS flow using unit-specific actuators.

    Different units have different control knobs:
    - CompletelyMixedMBR: pumped_flow = Q_ras + Q_was
    - FlatBottomCircularClarifier: wastage = Q_was (direct)
    - Splitter: split = Q_ras / (Q_ras + Q_was) (if downstream of MBR)

    Parameters
    ----------
    system : biosteam.System
        The compiled system.
    q_was : float
        Target WAS flow in m³/d.
    q_ras : float, optional
        RAS flow in m³/d. Auto-inferred if None.
    validate : bool
        Whether to validate feasibility before applying.

    Returns
    -------
    Tuple[bool, str]
        (success, error_message)
    """
    q_in = get_influent_flow(system)

    for unit in getattr(system, 'units', []):
        unit_type = type(unit).__name__

        if unit_type == 'CompletelyMixedMBR':
            # MBR: Control via pumped_flow (total retentate)
            if q_ras is None:
                # Use typical RAS ratio (1× influent) instead of heuristic
                q_ras = q_in * 1.0

            # Validate before applying
            if validate:
                valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                if not valid:
                    return False, msg

            unit.pumped_flow = q_ras + q_was
            # Also update downstream splitter if present
            _update_downstream_splitter(unit, q_ras, q_was)
            return True, ""

        elif 'Clarifier' in unit_type:
            # Clarifier: Direct wastage control
            if hasattr(unit, 'wastage'):
                # Get underflow (RAS) for feasibility check
                if q_ras is None:
                    q_ras = getattr(unit, 'underflow', q_in * 0.5)

                if validate:
                    valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                    if not valid:
                        return False, msg

                unit.wastage = q_was
                return True, ""

    # Fallback: Find and update WAS splitter
    for unit in getattr(system, 'units', []):
        if type(unit).__name__ == 'Splitter':
            if any('WAS' in str(out) or 'was' in str(out).lower()
                   for out in getattr(unit, 'outs', [])):
                if q_ras is None:
                    q_ras = unit.outs[0].F_vol * 24  # m³/d from RAS stream

                if validate:
                    valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                    if not valid:
                        return False, msg

                unit.split = q_ras / (q_ras + q_was) if (q_ras + q_was) > 0 else 0.5
                return True, ""

    return False, "No WAS actuator (MBR, Clarifier, or Splitter) found in system"


def _update_downstream_splitter(mbr_unit: Any, q_ras: float, q_was: float) -> None:
    """Update splitter downstream of MBR (splits retentate to RAS/WAS)."""
    if hasattr(mbr_unit, 'outs') and len(mbr_unit.outs) > 1:
        retentate = mbr_unit.outs[1]  # pumped/retentate outlet
        if retentate and hasattr(retentate, 'sink') and retentate.sink:
            splitter = retentate.sink
            if type(splitter).__name__ == 'Splitter':
                splitter.split = q_ras / (q_ras + q_was) if (q_ras + q_was) > 0 else 0.5


def has_srt_decoupling(system: Any) -> bool:
    """
    Check if system has HRT/SRT decoupling with a controllable actuator.

    Only returns True for units with known Q_was actuators:
    - CompletelyMixedMBR: pumped_flow property
    - FlatBottomCircularClarifier: wastage property

    Other separation units (AnMBR, IdealClarifier, Sedimentation, etc.)
    are excluded because they lack controllable waste flow actuators.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    bool
        True if system has a unit with known SRT control actuator.
    """
    for unit in getattr(system, 'units', []):
        unit_type = type(unit).__name__
        if unit_type in SRT_ACTUATOR_UNITS:
            return True
    return False


def detect_wastage_streams(system: Any) -> List[Any]:
    """
    Auto-detect WAS (wastage) streams in the system.

    Priority:
    1. Clarifier WAS outlet (outs[2])
    2. MBR retentate via WAS splitter
    3. Stream with 'WAS' or 'waste' in name

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    List[WasteStream]
        Detected WAS streams.
    """
    wastage_streams = []

    for unit in getattr(system, 'units', []):
        unit_type = type(unit).__name__

        # Clarifier WAS outlet
        if 'Clarifier' in unit_type and hasattr(unit, 'outs') and len(unit.outs) > 2:
            wastage_streams.append(unit.outs[2])

        # Splitter with WAS output
        if unit_type == 'Splitter':
            for out in getattr(unit, 'outs', []):
                name = str(out).lower() if out else ''
                if 'was' in name or 'waste' in name:
                    wastage_streams.append(out)

    # Fallback: search all streams
    if not wastage_streams:
        for stream in getattr(system, 'streams', []):
            name = str(stream).lower() if stream else ''
            if 'was' in name or 'waste' in name:
                wastage_streams.append(stream)

    return wastage_streams
