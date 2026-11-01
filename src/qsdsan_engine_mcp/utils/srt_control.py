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
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc
from qsdsan_engine_mcp.utils.stream_roles import get_streams_by_role

# Fail loudly if QSDsan is not available - no fallbacks
ensure_biosteam_tictoc()
from qsdsan.utils import get_SRT as qsdsan_get_SRT

logger = logging.getLogger(__name__)

# Biomass component IDs per model type
BIOMASS_IDS = {
    'ASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'ASM1': ['X_B_H', 'X_B_A'],
    'mASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    # Two-step nitrification splits X_AUT into X_AOB + X_NOB. Without these keys the
    # .get(model, ...) fallback asks composite() for X_AUT, which does not exist in the
    # 33-component set. composite() skips unknown IDs silently, so the SRT would be
    # computed with ZERO nitrifier mass -- wrong, and quietly so.
    'mASM2d2N': ['X_H', 'X_AOB', 'X_NOB', 'X_PAO', 'X_PHA', 'X_PP'],
    'mASM2d2N_N2O': ['X_H', 'X_AOB', 'X_NOB', 'X_PAO', 'X_PHA', 'X_PP'],
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


def get_biomass_ids(model_type: Optional[str]) -> List[str]:
    """Resolve biomass component IDs for a model, normalising the model name first.

    A bare ``BIOMASS_IDS.get(model_type, ...)`` accepts any casing or alias and falls
    back to the ASM2d list, whose ``X_AUT`` does not exist in the two-step component
    sets. ``composite()`` skips unknown IDs without complaint, so the failure surfaces
    as an SRT computed with no nitrifier mass rather than as an error. Normalise, and
    make the fallback audible.
    """
    if not model_type:
        return list(BIOMASS_IDS['ASM2d'])
    try:
        from qsdsan_engine_mcp.core.model_registry import normalize_model_name
        canonical = normalize_model_name(model_type)
    except Exception:  # pragma: no cover - registry import is not worth failing over
        canonical = model_type
    ids = BIOMASS_IDS.get(canonical)
    if ids is None:
        logger.warning(
            "No biomass component list registered for model %r (normalised to %r); "
            "falling back to the ASM2d list %s. If this model does not use X_AUT the "
            "resulting SRT will omit its autotrophs entirely.",
            model_type, canonical, BIOMASS_IDS['ASM2d'],
        )
        return list(BIOMASS_IDS['ASM2d'])
    return list(ids)


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


class MissingSemanticRoleError(ValueError):
    """Raised when SRT control is requested without required stream roles."""


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


def get_terminal_streams_by_role(
    system: Any,
    *roles: str,
    exclude_roles: Optional[Tuple[str, ...]] = None,
) -> List[Any]:
    """Resolve role-tagged streams that leave the flowsheet boundary.

    Seed-built aerobic trains tag intermediate zone outlets as ``effluent``
    because they are the effluent of that unit. Those streams are valid
    convergence sentinels, but they are not solids leaving the plant and must
    not enter SRT loss calculations.
    """
    role_streams = get_streams_by_role(
        system,
        *roles,
        exclude_roles=exclude_roles,
    )
    if not role_streams:
        return []

    products = {
        id(stream)
        for stream in getattr(system, "products", ()) or ()
        if stream is not None
    }
    terminal = []
    seen = set()
    for stream in role_streams:
        if stream is None:
            continue
        marker = id(stream)
        if marker in seen:
            continue
        seen.add(marker)
        if marker in products or getattr(stream, "sink", None) is None:
            terminal.append(stream)
    return terminal


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


def _infer_mbr_ras_flow(mbr_unit: Any, q_in: float) -> float:
    """
    Recover the fixed RAS leg from the current MBR recycle configuration.

    The aerobic MBR templates wire the membrane retentate to a downstream
    splitter with:
    - ``pumped_flow = Q_ras + Q_was``
    - ``split = Q_ras / (Q_ras + Q_was)``

    Reading those two values preserves the configured recycle ratio across SRT
    iterations instead of falling back to a generic 1.0 * Q_in assumption.
    """
    pumped_flow = _normalize_mbr_pumped_flow(mbr_unit, q_in)
    if pumped_flow is not None:
        outs = getattr(mbr_unit, 'outs', ())
        if len(outs) > 1:
            retentate = outs[1]
            splitter = getattr(retentate, 'sink', None) if retentate else None
            split = _coerce_split_fraction(getattr(splitter, 'split', None), default=None)
            if type(splitter).__name__ == 'Splitter' and split is not None:
                return float(pumped_flow) * split

    return q_in * 1.0


def _normalize_mbr_pumped_flow(mbr_unit: Any, q_in: float) -> Optional[float]:
    """Return MBR pumped flow in QSDsan's m3/d basis.

    The simulation-seed contract now treats ``pumped_flow`` as m3/d, matching
    QSDsan. Some KLD-scale project payloads express the same retentate flow as
    MLD while still carrying the canonical ``pumped_flow`` key. When that value
    is orders of magnitude below influent but becomes a realistic MBR recycle
    when converted from MLD to m3/d, normalize it at the actuator boundary so
    the SRT controller preserves the intended RAS leg.
    """
    raw = getattr(mbr_unit, 'pumped_flow', None)
    if raw is None:
        return None

    try:
        pumped_flow = float(raw)
    except (TypeError, ValueError):
        return raw

    if pumped_flow <= 0 or q_in <= 0:
        return pumped_flow

    mld_as_m3_d = pumped_flow * 1000.0
    if (
        pumped_flow < 10.0
        and q_in >= 100.0
        and 1.0 <= mld_as_m3_d / q_in <= 20.0
    ):
        logger.info(
            "Normalizing MBR pumped_flow %.3f to %.1f m3/d for %s "
            "(value is consistent with MLD-scale retentate flow for Q_in=%.1f m3/d)",
            pumped_flow,
            mld_as_m3_d,
            getattr(mbr_unit, "ID", type(mbr_unit).__name__),
            q_in,
        )
        mbr_unit.pumped_flow = mld_as_m3_d
        return mld_as_m3_d

    return pumped_flow


def _coerce_split_fraction(split: Any, default: float | None = 0.5) -> float | None:
    if split is None:
        return default
    if isinstance(split, (int, float)):
        return float(split)
    try:
        import numpy as np

        array = np.asarray(split, dtype=float).reshape(-1)
        if array.size:
            return float(array[0])
    except Exception:
        pass
    try:
        return float(split[0])
    except Exception:
        return default


def _splitter_has_locked_split(splitter: Any) -> bool:
    return bool(getattr(splitter, '_srt_control_split_locked', False))


def _stream_matches(left: Any, right: Any) -> bool:
    if left is right:
        return True
    left_id = getattr(left, 'ID', None)
    right_id = getattr(right, 'ID', None)
    return bool(left_id and right_id and left_id == right_id)


def _splitter_outlet_index(splitter: Any, stream: Any) -> Optional[int]:
    for index, outlet in enumerate(getattr(splitter, 'outs', ()) or ()):
        if _stream_matches(outlet, stream):
            return index
    return None


def _splitter_was_fraction(splitter: Any, was_stream: Any) -> Optional[float]:
    """Return the current fraction of splitter inlet routed to the WAS outlet."""
    split_to_first = _coerce_split_fraction(getattr(splitter, 'split', None), default=None)
    if split_to_first is None:
        return None

    was_index = _splitter_outlet_index(splitter, was_stream)
    if was_index == 0:
        return split_to_first
    if was_index == 1:
        return 1.0 - split_to_first
    return None


def _set_splitter_was_fraction(splitter: Any, was_stream: Any, was_fraction: float) -> None:
    """Set a two-outlet splitter so the role-tagged WAS outlet gets ``was_fraction``."""
    was_fraction = max(0.0, min(1.0, float(was_fraction)))
    was_index = _splitter_outlet_index(splitter, was_stream)
    if was_index == 0:
        splitter.split = was_fraction
    else:
        splitter.split = 1.0 - was_fraction


def _splitter_lock_info(splitter: Any, was_stream: Any) -> Optional[Dict[str, Any]]:
    if not _splitter_has_locked_split(splitter):
        return None
    split = _coerce_split_fraction(getattr(splitter, 'split', None), default=None)
    was_fraction = _splitter_was_fraction(splitter, was_stream)
    return {
        "unit_id": getattr(splitter, "ID", type(splitter).__name__),
        "split": split,
        "prescribed_split": getattr(splitter, "_srt_control_prescribed_split", split),
        "was_stream_id": getattr(was_stream, "ID", None),
        "was_fraction": was_fraction,
    }


def get_wastage_split_lock_info(system: Any) -> Optional[Dict[str, Any]]:
    """Return metadata for a role-tagged WAS splitter explicitly locked for SRT control."""
    try:
        actuator = _resolve_wastage_actuator(system)
    except MissingSemanticRoleError:
        return None

    if actuator is None:
        return None

    if actuator.get("kind") == "splitter":
        return _splitter_lock_info(actuator["unit"], actuator.get("was_stream"))

    if actuator.get("kind") == "mbr_pumped_flow":
        splitter = actuator.get("splitter")
        was_stream = actuator.get("was_stream")
        if splitter is not None and was_stream is not None:
            return _splitter_lock_info(splitter, was_stream)

        mbr_unit = actuator["unit"]
        for outlet in getattr(mbr_unit, "outs", ()) or ():
            splitter = getattr(outlet, "sink", None)
            if type(splitter).__name__ != "Splitter":
                continue
            for was_stream in get_streams_by_role(system, "was"):
                if getattr(was_stream, "source", None) is splitter:
                    return _splitter_lock_info(splitter, was_stream)

    return None


def _stream_flow_m3_d(stream: Any) -> float:
    f_vol = getattr(stream, 'F_vol', None)
    if f_vol is None:
        return 0.0
    return float(f_vol) * 24.0


def _find_splitter_partner_stream(splitter: Any, was_stream: Any) -> Any:
    for stream in getattr(splitter, 'outs', ()) or ():
        if stream is None or stream is was_stream:
            continue
        return stream
    return None


def _infer_splitter_ras_flow(splitter: Any, was_stream: Any, q_in: float) -> float:
    partner = _find_splitter_partner_stream(splitter, was_stream)
    if partner is not None:
        partner_flow = _stream_flow_m3_d(partner)
        if partner_flow > 0:
            return partner_flow

    inlet_stream = None
    ins = getattr(splitter, 'ins', None)
    if ins:
        inlet_stream = ins[0]

    upstream_unit = getattr(inlet_stream, 'source', None) if inlet_stream is not None else None
    if upstream_unit is not None and hasattr(upstream_unit, 'pumped_flow'):
        total_flow = float(_normalize_mbr_pumped_flow(upstream_unit, q_in) or 0.0)
        split = _coerce_split_fraction(getattr(splitter, 'split', 0.5))
        return total_flow * split

    if inlet_stream is not None:
        inlet_flow = _stream_flow_m3_d(inlet_stream)
        if inlet_flow > 0:
            split = _coerce_split_fraction(getattr(splitter, 'split', 0.5))
            return inlet_flow * split

    return q_in * 1.0


def _resolve_wastage_actuator_from_roles(system: Any) -> Optional[Dict[str, Any]]:
    """Resolve a controllable wastage actuator from semantic stream roles."""

    for was_stream in get_streams_by_role(system, "was"):
        source_unit = getattr(was_stream, 'source', None)
        if source_unit is None:
            continue

        if hasattr(source_unit, 'wastage'):
            return {
                "kind": "direct_wastage",
                "unit": source_unit,
                "was_stream": was_stream,
            }

        if hasattr(source_unit, 'pumped_flow'):
            return {
                "kind": "mbr_pumped_flow",
                "unit": source_unit,
                "was_stream": was_stream,
            }

        if hasattr(source_unit, 'split'):
            inlet_stream = None
            ins = getattr(source_unit, 'ins', None)
            if ins:
                inlet_stream = ins[0]
            upstream_unit = getattr(inlet_stream, 'source', None) if inlet_stream is not None else None
            if upstream_unit is not None and hasattr(upstream_unit, 'pumped_flow'):
                return {
                    "kind": "mbr_pumped_flow",
                    "unit": upstream_unit,
                    "splitter": source_unit,
                    "was_stream": was_stream,
                }
            return {
                "kind": "splitter",
                "unit": source_unit,
                "upstream_unit": upstream_unit,
                "was_stream": was_stream,
            }

    return None


def _resolve_wastage_actuator(system: Any) -> Optional[Dict[str, Any]]:
    actuator = _resolve_wastage_actuator_from_roles(system)
    if actuator is None:
        raise MissingSemanticRoleError(
            "SRT control requires a stream tagged with semantic role 'was'. "
            "Seed-built systems must carry SimulationSeedPort.role-derived "
            "session stream_roles before build."
        )
    return actuator


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
        biomass_IDs = get_biomass_ids(model_type)

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
        biomass_IDs = get_biomass_ids(model_type)

    biomass_IDs_tuple = tuple(biomass_IDs)
    total_biomass = 0.0

    for unit in getattr(system, 'units', []):
        if not getattr(unit, 'isdynamic', False):
            continue
        if not hasattr(unit, 'get_retained_mass'):
            continue

        try:
            mass = unit.get_retained_mass(biomass_IDs_tuple)
            if isinstance(mass, dict):
                total_biomass += sum(float(v) for v in mass.values())
            elif mass is not None:
                total_biomass += float(mass)
        except (KeyError, AttributeError, TypeError, ValueError):
            continue

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
    'Get biomass concentration in WAS stream(s).'
    if biomass_IDs is None:
        biomass_IDs = get_biomass_ids(model_type)

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
    actuator = _resolve_wastage_actuator(system)
    if actuator is None:
        return False, "No controllable wastage actuator found in system"

    kind = actuator["kind"]

    if kind == "mbr_pumped_flow":
        unit = actuator["unit"]
        splitter = actuator.get("splitter")
        was_stream = actuator.get("was_stream")
        if splitter is not None and was_stream is not None:
            if _splitter_has_locked_split(splitter):
                was_fraction = _splitter_was_fraction(splitter, was_stream)
                if was_fraction is None:
                    return False, "Locked splitter WAS outlet could not be resolved from stream roles"
                if was_fraction <= 0:
                    return False, "Locked splitter sends no flow to the role-tagged WAS outlet"
                total_flow = q_was / was_fraction
                q_ras = total_flow - q_was
                if validate:
                    valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                    if not valid:
                        return False, msg
                unit.pumped_flow = total_flow
                return True, ""

            if q_ras is None:
                q_ras = _infer_splitter_ras_flow(splitter, was_stream, q_in)
            if validate:
                valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                if not valid:
                    return False, msg
            total_flow = q_ras + q_was
            unit.pumped_flow = total_flow
            _set_splitter_was_fraction(
                splitter,
                was_stream,
                q_was / total_flow if total_flow > 0 else 0.5,
            )
            return True, ""

        if q_ras is None:
            q_ras = _infer_mbr_ras_flow(unit, q_in)
        if validate:
            valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
            if not valid:
                return False, msg
        unit.pumped_flow = q_ras + q_was
        _update_downstream_splitter(unit, q_ras, q_was)
        return True, ""

    if kind == "direct_wastage":
        unit = actuator["unit"]
        if q_ras is None:
            q_ras = float(getattr(unit, 'underflow', q_in * 0.5) or (q_in * 0.5))
        if validate:
            valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
            if not valid:
                return False, msg
        unit.wastage = q_was
        return True, ""

    if kind == "splitter":
        splitter = actuator["unit"]
        was_stream = actuator.get("was_stream")
        upstream_unit = actuator.get("upstream_unit")
        if _splitter_has_locked_split(splitter):
            was_fraction = _splitter_was_fraction(splitter, was_stream)
            if was_fraction is None:
                return False, "Locked splitter WAS outlet could not be resolved from stream roles"
            if was_fraction <= 0:
                return False, "Locked splitter sends no flow to the role-tagged WAS outlet"

            total_flow = q_was / was_fraction
            q_ras = total_flow - q_was
            if validate:
                valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                if not valid:
                    return False, msg
            if upstream_unit is None or not hasattr(upstream_unit, 'pumped_flow'):
                return (
                    False,
                    "WAS splitter split is locked, but no upstream pumped_flow actuator is available "
                    "to change Q_was while preserving split",
                )
            upstream_unit.pumped_flow = total_flow
            return True, ""

        if q_ras is None:
            q_ras = _infer_splitter_ras_flow(splitter, was_stream, q_in)
        if validate:
            valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
            if not valid:
                return False, msg

        total_flow = q_ras + q_was
        if upstream_unit is not None and hasattr(upstream_unit, 'pumped_flow'):
            upstream_unit.pumped_flow = total_flow
        _set_splitter_was_fraction(
            splitter,
            was_stream,
            q_was / total_flow if total_flow > 0 else 0.5,
        )
        return True, ""

    return False, f"Unsupported wastage actuator kind: {kind}"


def _update_downstream_splitter(mbr_unit: Any, q_ras: float, q_was: float) -> None:
    """Update splitter downstream of MBR (splits retentate to RAS/WAS)."""
    if hasattr(mbr_unit, 'outs') and len(mbr_unit.outs) > 1:
        retentate = mbr_unit.outs[1]  # pumped/retentate outlet
        if retentate and hasattr(retentate, 'sink') and retentate.sink:
            splitter = retentate.sink
            if type(splitter).__name__ == 'Splitter':
                if _splitter_has_locked_split(splitter):
                    return
                splitter.split = q_ras / (q_ras + q_was) if (q_ras + q_was) > 0 else 0.5


def has_srt_decoupling(system: Any) -> bool:
    """
    Check if system has HRT/SRT decoupling with a controllable actuator.

    The system must carry semantic stream roles; the ``was`` role identifies
    the withdrawal stream and its source unit identifies the actuator.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    bool
        True if system has a unit with known SRT control actuator.
    """
    return _resolve_wastage_actuator(system) is not None


def detect_wastage_streams(system: Any) -> List[Any]:
    """
    Return WAS streams from semantic role tags.

    Parameters
    ----------
    system : biosteam.System
        The compiled system.

    Returns
    -------
    List[WasteStream]
        Role-tagged WAS streams.
    """
    role_streams = get_streams_by_role(system, "was")
    if role_streams:
        return role_streams
    raise MissingSemanticRoleError(
        "Cannot detect wastage streams: no stream is tagged with semantic role 'was'."
    )
