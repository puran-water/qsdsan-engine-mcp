"""
Flowsheet Builder - Compile and simulate QSDsan systems from session configurations.

This module provides the actual QSDsan system compilation and simulation logic
that builds WasteStream/SanUnit objects from session configurations.

Usage:
    from utils.flowsheet_builder import (
        compile_system,
        simulate_compiled_system,
    )

    # Compile session into QSDsan System
    system, build_info = compile_system(session)

    # Simulate the system
    results = simulate_compiled_system(system, duration_days=15, ...)
"""

from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path
from dataclasses import dataclass
import datetime
import inspect
import json
import logging
import re

from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc
from qsdsan_engine_mcp.core.conversions.qsdsan_stream import from_qsdsan_stream
from qsdsan_engine_mcp.core.contracts.stream_state import validate_concentration_bounds
from qsdsan_engine_mcp.core.model_registry import ASM1_COMPONENTS, ASM2D_COMPONENTS, MADM1_COMPONENTS, MASM2D_COMPONENTS
from qsdsan_engine_mcp.core.version import get_version_info
from qsdsan_engine_mcp.utils.stream_roles import attach_stream_roles_to_system, derive_session_stream_roles, get_streams_by_role

logger = logging.getLogger(__name__)

ensure_biosteam_tictoc()

_ASM2D_COMPONENT_SET = set(ASM2D_COMPONENTS)
_ASM1_COMPONENT_SET = set(ASM1_COMPONENTS)
_MADM1_COMPONENT_SET = set(MADM1_COMPONENTS)
_MASM2D_COMPONENT_SET = set(MASM2D_COMPONENTS)
_MASM2D_UNIQUE = _MASM2D_COMPONENT_SET - _ASM2D_COMPONENT_SET
_MADM1_UNIQUE = _MADM1_COMPONENT_SET - _MASM2D_COMPONENT_SET
# Two-step nitrification markers — a mASM2d2N stream also carries mASM2d-unique components,
# so this must be tested BEFORE _MASM2D_UNIQUE or two-step streams misclassify as stock mASM2d.
_MASM2D_2N_UNIQUE = frozenset({"S_NO2", "X_AOB", "X_NOB"})
# AOB-N2O markers — a mASM2d2N_N2O stream ALSO carries the two-step markers above, so this must be
# tested BEFORE _MASM2D_2N_UNIQUE or N2O streams misclassify as plain two-step (dropping S_N2O etc.).
_MASM2D_2N_N2O_UNIQUE = frozenset({"S_N2O", "S_NH2OH", "S_NO"})


def _compile_flowsheet_id(session_id: str, system_id: str) -> str:
    """Create a per-build flowsheet namespace to avoid registry collisions."""
    raw = f"qsdsan_engine_{session_id}_{system_id}"
    return re.sub(r"[^A-Za-z0-9_]+", "_", raw)


def _infer_stream_model_type(stream) -> str:
    component_ids = set(getattr(getattr(stream, "components", None), "IDs", ()) or ())
    if component_ids & _MADM1_UNIQUE:
        return "mADM1"
    if component_ids & _MASM2D_2N_N2O_UNIQUE:
        return "mASM2d2N_N2O"
    if component_ids & _MASM2D_2N_UNIQUE:
        return "mASM2d2N"
    if component_ids & _MASM2D_UNIQUE:
        return "mASM2d"
    if component_ids <= _ASM1_COMPONENT_SET and component_ids:
        return "ASM1"
    if component_ids & _ASM2D_COMPONENT_SET:
        return "ASM2d"
    return "ASM2d"


@dataclass
class BuildInfo:
    """Information about a compiled system."""
    system_id: str
    unit_order: List[str]
    recycle_edges: List[Tuple[str, str]]
    streams_created: List[str]
    units_created: List[str]
    warnings: List[str]


def _apply_persisted_unit_initial_conditions(
    session: "FlowsheetSession",
    unit_registry: Dict[str, "SanUnit"],
) -> List[str]:
    """Apply per-unit startup concentrations saved on the session."""
    warnings: List[str] = []
    startup_metadata = getattr(session, "startup_metadata", {}) or {}
    per_unit_init_conc = startup_metadata.get("per_unit_init_conc") or {}
    if not isinstance(per_unit_init_conc, dict):
        return warnings

    for unit_id, init_conc in per_unit_init_conc.items():
        if unit_id not in unit_registry:
            warnings.append(f"Startup state references unknown unit '{unit_id}'")
            continue
        if not isinstance(init_conc, dict) or not init_conc:
            continue

        unit = unit_registry[unit_id]
        setter = getattr(unit, "set_init_conc", None)
        if not callable(setter):
            warnings.append(f"Unit '{unit_id}' does not support set_init_conc")
            continue

        try:
            setter(**init_conc)
        except Exception as exc:
            warnings.append(f"Failed to apply startup state to '{unit_id}': {exc}")

    # mADM1 headspace (gas-phase) seeding (roadmap C1). The WaterTAP -> QSDsan
    # bridge stores the converged gas composition (kmol/m3, keyed by the QSDsan
    # biogas ids) per digester unit; apply it onto the unit's gas slice via the
    # B1 derived slice + biogas-species ordering so the dynamic run starts at the
    # steady headspace instead of re-equilibrating.
    per_unit_gas_init = startup_metadata.get("per_unit_gas_init_kmol_m3") or {}
    if isinstance(per_unit_gas_init, dict) and per_unit_gas_init:
        from qsdsan_engine_mcp.utils.state_checkpoint import apply_madm1_gas_seed

        for unit_id, gas_conc in per_unit_gas_init.items():
            if unit_id not in unit_registry:
                warnings.append(f"Gas startup state references unknown unit '{unit_id}'")
                continue
            if not isinstance(gas_conc, dict) or not gas_conc:
                continue
            try:
                report = apply_madm1_gas_seed(unit_registry[unit_id], gas_conc, strict=False)
                if report.get("warning"):
                    warnings.append(f"Gas startup state for '{unit_id}': {report['warning']}")
            except Exception as exc:
                warnings.append(f"Failed to apply gas startup state to '{unit_id}': {exc}")

    return warnings


def compile_system(
    session: "FlowsheetSession",
    system_id: str,
    unit_order: Optional[List[str]] = None,
    recycle_stream_ids: Optional[Set[str]] = None,
    strict: bool = True,
) -> Tuple["System", BuildInfo]:
    """
    Compile a flowsheet session into a QSDsan System.

    This function:
    1. Creates thermo and components for each model type used
    2. Creates WasteStream objects from session.streams
    3. Creates SanUnit objects from session.units with process models
    4. Wires units together using pipe notation
    5. Creates recycle streams for deferred connections
    6. Builds the System with the specified unit order

    Supports mixed-model flowsheets (e.g., ASM2d + mADM1 with junctions).

    Args:
        session: FlowsheetSession with streams, units, and connections
        system_id: Name for the compiled system
        unit_order: Optional execution order. If None, uses topological sort.
        recycle_stream_ids: Stream IDs known to be recycles
        strict: If True (default), raise errors on connection failures and cycles.
                If False, add warnings but continue with best-effort compilation.

    Returns:
        Tuple of (System, BuildInfo)

    Raises:
        ValueError: If session is invalid or units/streams can't be created
        RuntimeError: If strict=True and connection wiring fails or non-recycle cycle detected
    """
    import qsdsan as qs
    from qsdsan import unit_operations as sanunits, WasteStream, System

    from qsdsan_engine_mcp.core.unit_registry import get_unit_spec
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation, resolve_port, is_tuple_notation, parse_tuple_notation
    from qsdsan_engine_mcp.utils.topo_sort import topological_sort

    warnings = []

    # BioSTEAM/QSDsan use process-wide registries for streams, units, and
    # systems. Give each compile its own flowsheet namespace so repeated MCP
    # session builds do not spam "replaced in registry" warnings.
    qs.main_flowsheet.set_flowsheet(_compile_flowsheet_id(session.session_id, system_id), new=True)
    qs.main_flowsheet.clear()

    # Cache for model-specific components/thermo
    # This enables mixed-model flowsheets (ASM2d + mADM1)
    model_components: Dict[str, Tuple] = {}

    def get_components_for_model(model: str):
        """Get or create components/thermo for a model type."""
        if model not in model_components:
            cmps, thermo = _get_model_components(model)
            model_components[model] = (cmps, thermo)
        return model_components[model]

    # Initialize primary model components
    primary_model = session.primary_model_type
    cmps, thermo = get_components_for_model(primary_model)

    # Set primary model as active thermo
    qs.set_thermo(cmps)

    # Create WasteStream objects
    stream_registry: Dict[str, WasteStream] = {}
    for stream_id, config in session.streams.items():
        try:
            # Use stream's model_type or fall back to primary
            stream_model = config.model_type or primary_model
            stream_cmps, _ = get_components_for_model(stream_model)

            # Set thermo for this stream's model
            qs.set_thermo(stream_cmps)

            stream = _create_waste_stream(stream_id, config, stream_cmps, stream_model)
            stream_registry[stream_id] = stream
            logger.debug(f"Created stream '{stream_id}' (model: {stream_model})")
        except Exception as e:
            raise ValueError(f"Failed to create stream '{stream_id}': {e}")

    # Reset to primary thermo
    qs.set_thermo(cmps)

    # Create SanUnit objects
    unit_registry: Dict[str, "SanUnit"] = {}
    for unit_id, config in session.units.items():
        try:
            # Use unit's model_type or fall back to primary
            unit_model = config.model_type or primary_model
            unit_cmps, _ = get_components_for_model(unit_model)

            # Set thermo for this unit's model
            qs.set_thermo(unit_cmps)

            unit = _create_san_unit(
                unit_id, config, stream_registry, unit_registry, unit_cmps, unit_model
            )
            unit_registry[unit_id] = unit
            logger.debug(f"Created unit '{unit_id}' ({config.unit_type}, model: {unit_model})")
        except Exception as e:
            raise ValueError(f"Failed to create unit '{unit_id}': {e}")

    warnings.extend(_apply_persisted_unit_initial_conditions(session, unit_registry))

    # Wire deferred connections (recycles)
    recycle_streams = []
    for conn in session.connections:
        try:
            _wire_connection(conn, unit_registry, stream_registry, recycle_streams)
            logger.debug(f"Wired connection {conn.from_port} -> {conn.to_port}")
        except Exception as e:
            if strict:
                raise RuntimeError(
                    f"Connection {conn.from_port} -> {conn.to_port} failed: {e}"
                ) from e
            warnings.append(f"Connection {conn.from_port} -> {conn.to_port} failed: {e}")

    # Determine unit order
    if unit_order is None:
        topo_result = topological_sort(
            session.units,
            session.connections,
            recycle_stream_ids=recycle_stream_ids or set(),
            fail_on_cycle=strict,
        )
        unit_order = topo_result.unit_order
        recycle_edges = topo_result.recycle_edges
        warnings.extend(topo_result.warnings)
    else:
        recycle_edges = []

    # Build System
    path = [unit_registry[uid] for uid in unit_order if uid in unit_registry]

    # Identify recycle streams for System
    system_recycles = []
    for stream_id in (recycle_stream_ids or set()):
        if stream_id in stream_registry:
            system_recycles.append(stream_registry[stream_id])
    system_recycles.extend(recycle_streams)

    system = System(
        system_id,
        path=path,
        recycle=system_recycles[0] if len(system_recycles) == 1 else system_recycles if system_recycles else None,
    )

    attach_stream_roles_to_system(
        system,
        derive_session_stream_roles(session),
        extra_streams=stream_registry,
    )

    build_info = BuildInfo(
        system_id=system_id,
        unit_order=unit_order,
        recycle_edges=recycle_edges,
        streams_created=list(stream_registry.keys()),
        units_created=list(unit_registry.keys()),
        warnings=warnings,
    )

    return system, build_info


# =============================================================================
# Sludge/Biomass Stream Detection (Phase 11)
# =============================================================================

# Unit-specific output mappings for clarifiers and MBRs
CLARIFIER_SLUDGE_OUTPUTS = {
    'FlatBottomCircularClarifier': [2, 1],  # WAS (priority), RAS (fallback)
    'IdealClarifier': [1],                   # underflow
    'PrimaryClarifier': [1],                 # underflow
    'PrimaryClarifierBSM2': [1],             # underflow
}

MBR_SLUDGE_OUTPUTS = {
    'CompletelyMixedMBR': [1],               # retentate/pumped
    'AnMBR': [1],                            # productized wrapper: retentate/sludge
}


def _detect_sludge_streams(system: "System") -> List["WasteStream"]:
    """
    Auto-detect sludge streams for biomass convergence tracking.

    Uses unit-specific output mapping (clarifiers, MBRs) and biomass-based
    ranking for accurate sludge stream identification.

    Parameters
    ----------
    system : System
        QSDsan System with units configured

    Returns
    -------
    List[WasteStream]
        Detected sludge streams ranked by biomass mass flow

    Notes
    -----
    Detection priority:
    1. Unit-specific sludge outputs (clarifiers outs[2]/[1], MBR outs[1])
    2. Name-based matching ("was", "sludge", "waste_sludge")
    3. Terminal stream with highest biomass (data-driven fallback)

    Excludes internal recycles (RAS, IR) from results.
    """
    candidates = []

    # Priority 0: Semantic role tags from seed/session compilation.
    role_candidates = get_streams_by_role(
        system,
        "was",
        "sludge",
        exclude_roles=("ras", "internal_recycle", "sludge_return"),
    )
    if role_candidates:
        return _rank_and_dedupe_by_biomass(role_candidates)

    # Priority 1: Unit-specific sludge outputs
    for unit in system.units:
        unit_type = type(unit).__name__

        # Clarifiers
        if unit_type in CLARIFIER_SLUDGE_OUTPUTS:
            for idx in CLARIFIER_SLUDGE_OUTPUTS[unit_type]:
                if len(unit.outs) > idx and unit.outs[idx]:
                    candidates.append(_resolve_sludge_product(unit.outs[idx]))

        # MBRs
        elif unit_type in MBR_SLUDGE_OUTPUTS:
            for idx in MBR_SLUDGE_OUTPUTS[unit_type]:
                if len(unit.outs) > idx and unit.outs[idx]:
                    candidates.append(_resolve_sludge_product(unit.outs[idx]))

        # Unknown clarifier/MBR types: use biomass heuristic
        elif 'Clarifier' in unit_type or 'MBR' in unit_type:
            if len(unit.outs) > 1:
                # Pick outlet with highest biomass (not effluent at outs[0])
                best_sludge = _select_by_biomass(unit.outs[1:])
                if best_sludge:
                    candidates.append(_resolve_sludge_product(best_sludge))

    # Priority 2: Name-based matching (fallback)
    if not candidates:
        sludge_patterns = ('was', 'sludge', 'waste_sludge')
        exclude_patterns = ('ras', 'return', 'recycle', 'internal', '_ir')
        for s in system.streams:
            if not s:
                continue
            sid_lower = s.ID.lower()
            if any(pat in sid_lower for pat in sludge_patterns):
                if not any(ex in sid_lower for ex in exclude_patterns):
                    candidates.append(s)

    # Priority 3: Terminal stream with highest biomass (data-driven fallback)
    if not candidates:
        exclude_patterns = ('ras', 'ir', 'return', 'recycle', 'internal')
        terminal_streams = []
        for s in system.streams:
            if not s:
                continue
            sid_lower = s.ID.lower()
            if any(pat in sid_lower for pat in exclude_patterns):
                continue
            if hasattr(s, 'sink') and s.sink is None:
                terminal_streams.append(s)

        if terminal_streams:
            best = _select_by_biomass(terminal_streams)
            if best:
                candidates.append(best)

    # Rank by biomass and deduplicate
    return _rank_and_dedupe_by_biomass(candidates)


def _resolve_sludge_product(stream, _depth: int = 0):
    """Resolve a detected sludge outlet to its terminal product wastage stream.

    A ``CompletelyMixedMBR`` retentate (or clarifier underflow) is frequently
    split downstream into WAS (leaves the system) + RAS (returns to the
    bioreactor). The retentate itself is a non-product intermediate whose
    ``.state`` is proxied through its sink's ``_ins_QC``. BioSTEAM builds the
    dynamic ``SystemScope`` inside ``reset_cache`` *before* the per-unit
    ``_init_dynamic`` loop, so a tracked intermediate has no resolvable
    ``.state`` at scope-creation time and its scope records nothing, crashing
    the post-hoc convergence scan with "need at least one array to
    concatenate". The actual wastage is the splitter's product (non-recycle)
    output, which carries the same solids composition and tracks robustly.
    Returns the stream unchanged when it already leaves the system as a product
    or is not fed into a splitter.
    """
    if stream is None or _depth > 4:
        return stream
    # Already a system product (no sink) → robust to track as-is.
    if getattr(stream, "sink", None) is None:
        return stream
    sink = stream.sink
    if type(sink).__name__ != "Splitter":
        return stream

    outs = [o for o in getattr(sink, "outs", []) if o is not None]
    recycle_pat = ("ras", "return", "recycle", "internal", "_ir", "mlr")
    # Terminal product outputs of the splitter (leave the system).
    products = [o for o in outs if getattr(o, "sink", None) is None]
    wastage = [o for o in products if not any(p in o.ID.lower() for p in recycle_pat)]
    chosen = wastage or products
    if chosen:
        named = [o for o in chosen if any(w in o.ID.lower() for w in ("was", "sludge", "waste"))]
        return (named or chosen)[0]
    # No terminal product yet (splitter feeds further units) → follow the
    # wastage-named / highest-biomass branch through the next splitter.
    named = [o for o in outs if any(w in o.ID.lower() for w in ("was", "sludge", "waste"))]
    nxt = _select_by_biomass(named or outs)
    return _resolve_sludge_product(nxt, _depth + 1)


def _select_by_biomass(streams) -> "WasteStream":
    """Select stream with highest ASM biomass mass flow."""
    def biomass_score(s):
        if not s or not hasattr(s, 'imass'):
            return 0
        score = 0
        for comp in ['X_AUT', 'X_H', 'X_PAO', 'X_ac', 'X_h2']:  # ASM + ADM biomass
            try:
                score += s.imass[comp]
            except Exception:
                pass
        return score

    valid = [s for s in streams if s]
    return max(valid, key=biomass_score, default=None)


def _biomass_score(s) -> float:
    """Calculate biomass score for a stream (numeric value for sorting)."""
    if not s or not hasattr(s, 'imass'):
        return 0.0
    score = 0.0
    for comp in ['X_AUT', 'X_H', 'X_PAO', 'X_ac', 'X_h2']:  # ASM + ADM biomass
        try:
            score += float(s.imass[comp])
        except Exception:
            pass
    return score


def _rank_and_dedupe_by_biomass(candidates) -> List["WasteStream"]:
    """Rank candidates by biomass and remove duplicates."""
    seen = set()
    unique = []
    for s in candidates:
        if s and id(s) not in seen:
            seen.add(id(s))
            unique.append(s)

    # Sort by biomass score descending (highest first)
    # Uses _biomass_score which returns a float, not a stream object
    return sorted(unique, key=_biomass_score, reverse=True)


def _detect_effluent_streams(system: "System") -> List["WasteStream"]:
    """
    Auto-detect effluent streams for convergence tracking.

    Uses multiple strategies with priority:
    1. Streams with "effluent" in name
    2. First output of clarifier units (outs[0] is clarified effluent)
    3. Terminal streams with lowest TSS (clear water = effluent)

    Parameters
    ----------
    system : System
        QSDsan System

    Returns
    -------
    List[WasteStream]
        Detected effluent streams
    """
    candidates = []

    # Priority 0: Semantic role tags from seed/session compilation.
    role_candidates = get_streams_by_role(system, "effluent", "permeate")
    if role_candidates:
        return role_candidates

    # Priority 1: Name-based detection
    for s in system.streams:
        if s and "effluent" in s.ID.lower():
            candidates.append(s)

    if candidates:
        return candidates

    # Priority 2: First output of clarifier units
    clarifier_types = ('Clarifier', 'MBR', 'FlatBottom', 'IdealClarifier')
    for unit in system.units:
        unit_type = type(unit).__name__
        if any(ct in unit_type for ct in clarifier_types):
            if len(unit.outs) > 0 and unit.outs[0]:
                candidates.append(unit.outs[0])

    if candidates:
        return candidates

    # Priority 3: Terminal stream with lowest TSS (excluding sludge)
    exclude_patterns = ('was', 'sludge', 'ras', 'return', 'recycle', 'biogas', 'gas')
    terminal_streams = []
    for s in system.streams:
        if not s:
            continue
        sid_lower = s.ID.lower()
        if any(pat in sid_lower for pat in exclude_patterns):
            continue
        if hasattr(s, 'sink') and s.sink is None:
            terminal_streams.append(s)

    if terminal_streams:
        def tss_score(s):
            """Lower TSS = more likely to be effluent."""
            if not hasattr(s, 'get_TSS'):
                return float('inf')
            try:
                return s.get_TSS()
            except Exception:
                return float('inf')

        # Return stream with lowest TSS
        best = min(terminal_streams, key=tss_score, default=None)
        if best:
            return [best]

    # Fallback: return first product that's not a gas
    for s in system.products:
        if s and 'gas' not in s.ID.lower() and 'biogas' not in s.ID.lower():
            return [s]

    return []


def _run_resume_segment(
    system: "System",
    *,
    dynamic_controls: Optional[Dict[str, Any]],
    disturbance_schedule: Optional[Dict[str, Any]],
    duration_days: float,
    timestep_hours: float,
    method: str,
    t_eval: Optional[List[float]],
    start_time_days: float,
    resume_state_hook: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """Resume a B1-restored twin for ``duration_days`` from ``start_time_days``.

    The reconciliation hook runs exactly once on the restored state, before the
    first integrated segment (see ``run_sampled_data_system``'s resume branch).
    With a controller/disturbance schedule the resume goes through the
    sampled-data driver; a bare resume integrates a single absolute-time window
    with ``state_reset_hook=None`` so the restored (and reconciled) state is
    never cleared. The hook is invoked here for the bare path because that path
    does not go through the sampled-data driver.
    """
    import numpy as np
    from qsdsan_engine_mcp.utils.run_to_convergence import run_sampled_data_system

    timestep_days = timestep_hours / 24.0
    if dynamic_controls or disturbance_schedule:
        return run_sampled_data_system(
            system,
            dynamic_controls=dynamic_controls,
            disturbance_schedule=disturbance_schedule,
            duration_days=duration_days,
            timestep_days=timestep_days,
            method=method,
            state_reset_hook=None,
            max_step_days=timestep_days,
            start_time_days=start_time_days,
            resume=True,
            resume_state_hook=resume_state_hook,
        )

    # Bare resume (no controls/disturbances): reconcile once, then integrate the
    # absolute-time window. Hydraulics were already stabilized by the caller.
    if resume_state_hook is not None:
        resume_state_hook(system)
    end_days = start_time_days + duration_days
    resume_eval = np.arange(
        start_time_days, end_days + timestep_days, timestep_days
    )
    resume_eval = resume_eval[resume_eval <= end_days + 1e-9].tolist()
    if not resume_eval or abs(resume_eval[-1] - end_days) > 1e-9:
        resume_eval.append(end_days)
    system.simulate(
        state_reset_hook=None,
        t_span=(start_time_days, end_days),
        t_eval=resume_eval,
        method=method,
        max_step=timestep_days,
    )
    return None


def _apply_ph_solver_physicality_gate(
    results: Dict[str, Any],
    system: Any = None,
) -> Dict[str, Any]:
    """Attach fail-closed charge-solve and state-projection diagnostics."""
    import numpy as np

    from qsdsan_engine_mcp.models.physicality import ph_solve_failure_breakdown, ph_solve_failures, state_projection_failure_report

    failure_count = ph_solve_failures()
    failure_breakdown = ph_solve_failure_breakdown()
    failures = []
    if failure_count:
        failures.append(
            "charge-balance pH solve fell back to neutral pH "
            f"{failure_count} time(s); affected variants={failure_breakdown}"
        )
    projection_report = state_projection_failure_report()
    projection_failures = int(projection_report["event_count"])
    if projection_failures:
        failures.append(
            "material negative/nonfinite native states required numerical projection "
            f"{projection_failures} time(s); contexts="
            f"{projection_report['events_by_context']}"
        )

    final_state_minimum = None
    final_state_nonfinite_count = 0
    final_state = getattr(system, "_state", None) if system is not None else None
    if final_state is not None:
        try:
            state_array = np.asarray(final_state, dtype=float)
            finite = np.isfinite(state_array)
            final_state_nonfinite_count = int(
                state_array.size - np.count_nonzero(finite)
            )
            if np.any(finite):
                final_state_minimum = float(np.min(state_array[finite]))
        except (TypeError, ValueError):
            final_state_nonfinite_count = 1
        if final_state_nonfinite_count or (
            final_state_minimum is not None and final_state_minimum < -1e-6
        ):
            failures.append(
                "final native system state is nonphysical: "
                f"minimum={final_state_minimum}, "
                f"nonfinite_count={final_state_nonfinite_count}"
            )
    physicality_ok = not failures
    prior_result_valid = results.get("result_valid") is not False
    results["physicality"] = {
        "physicality_ok": physicality_ok,
        "ph_solve_failure_count": failure_count,
        "ph_solve_failure_breakdown": failure_breakdown,
        "state_projection": projection_report,
        "final_state_minimum": final_state_minimum,
        "final_state_nonfinite_count": final_state_nonfinite_count,
        "failures": failures,
    }
    results["physicality_ok"] = physicality_ok
    results["physicality_failures"] = failures
    results["result_valid"] = physicality_ok and prior_result_valid
    if not physicality_ok:
        # The numerical integration returned, but its chemistry is not fit for
        # interpretation.  Keep the diagnostic payload while preventing generic
        # consumers from treating it as a completed usable simulation.
        results["simulation_completed"] = False
        results["simulation"]["status"] = (
            "invalid_ph_charge_solve"
            if failure_count and not projection_failures and len(failures) == 1
            else "invalid_physicality"
        )
        results["recommendation_eligible"] = False
    elif not prior_result_valid:
        # A physicality pass cannot revive a result invalidated by another
        # solver, balance, or contract gate.
        results["recommendation_eligible"] = False
    return results


def simulate_compiled_system(
    system: "System",
    duration_days: float = 1.0,
    timestep_hours: float = 1.0,
    method: str = "RK23",
    t_eval: Optional[List[float]] = None,
    state_reset_hook: str = "reset_cache",
    output_dir: Optional[Path] = None,
    model_type: str = "ASM2d",
    effluent_stream_ids: Optional[List[str]] = None,
    biogas_stream_ids: Optional[List[str]] = None,
    include_components: bool = False,
    track: Optional[List[str]] = None,
    export_state_to: Optional[Path] = None,
    run_to_convergence: bool = False,
    convergence_atol: float = 0.1,
    convergence_rtol: float = 1e-3,
    check_interval_days: float = 2.0,
    max_duration_days: Optional[float] = None,
    convergence_stream_ids: Optional[Dict[str, List[str]]] = None,
    convergence_components: Optional[Dict[str, List[str]]] = None,
    dynamic_controls: Optional[Dict[str, Any]] = None,
    disturbance_schedule: Optional[Dict[str, Any]] = None,
    # SRT Control Parameters (Phase 12)
    target_srt_days: Optional[float] = None,
    srt_tolerance: float = 0.1,
    max_srt_iterations: int = 10,
    # B3: checkpoint-restored resume + SCADA/lab state reconciliation
    resume_from_checkpoint: Optional[Dict[str, Any]] = None,
    reconciliation_measurements: Optional[Dict[str, Any]] = None,
    reconciliation_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Simulate a compiled QSDsan System.

    Args:
        system: Compiled QSDsan System
        duration_days: Simulation duration in days (used when run_to_convergence=False)
        timestep_hours: Output timestep in hours
        method: ODE solver method (RK23, RK45, BDF). Auto-set to BDF when run_to_convergence=True.
        t_eval: Custom evaluation times (days). If None, uses timestep.
        state_reset_hook: Method name for state reset
        output_dir: Directory for output files
        model_type: Process model type for result analysis
        effluent_stream_ids: Streams for effluent quality analysis
        biogas_stream_ids: Streams for biogas analysis (mADM1 only)
        include_components: Include full component breakdown in results
        track: Stream IDs to track dynamically during simulation
        export_state_to: Path to export final effluent state as StreamState JSON
        run_to_convergence: If True, run until steady state (default False)
        convergence_atol: Absolute tolerance for convergence (default 0.1 mg/L/d)
        convergence_rtol: Relative tolerance for convergence (default 1e-3)
        check_interval_days: Days between convergence checks (default 2.0)
        max_duration_days: Maximum simulation time for convergence mode
        convergence_stream_ids: Explicit streams for convergence:
            {"effluent": ["eff1"], "sludge": ["was1"]}
        convergence_components: Explicit components to check:
            {"effluent": ["S_NH4", "S_NO3"], "sludge": ["X_AUT", "X_H"]}
        dynamic_controls: Optional sampled-data Sensor/PID/Actuator loop specs.
            Fast DO/pH controls run in fixed-duration or convergence mode. SRT
            remains handled by the existing supervisory target_srt_days search.
        disturbance_schedule: Optional declarative feed-disturbance schedule
            (load shocks, pH/alkalinity excursions, CIP slugs) applied at
            controller-sample boundaries. Runs open-loop (no controller) or
            closed-loop (with dynamic_controls). Not applied in target_srt_days
            mode (a supervisory steady-state search).
        target_srt_days: Target SRT in days. If set, Q_was is iteratively adjusted
            to achieve the target SRT at steady state. Implies run_to_convergence=True. (Phase 12)
        srt_tolerance: Relative tolerance on achieved SRT (default 0.1 = 10%).
        max_srt_iterations: Maximum Q_was adjustment iterations for SRT control (default 10).

    Returns:
        Dict with simulation results including effluent quality, removal efficiency, etc.
        When run_to_convergence=True, also includes:
        - simulation.converged_at_days: Time when steady state was reached
        - simulation.convergence_status: 'converged' or 'max_time_reached'
        - simulation.convergence_metrics: Detailed convergence diagnostics
        When target_srt_days is set, also includes:
        - srt_control: Dict with achieved_srt_days, q_was_optimal, srt_iterations
    """
    import numpy as np

    # The modified ASM charge-balance solvers deliberately return neutral pH
    # from the rate function when an internal nonlinear solve fails.  Raising
    # inside the ODE right-hand side makes recovery impossible, but allowing the
    # completed trajectory to look valid is fail-open.  Audit the whole public
    # simulation interval and classify the result after integration instead.
    from qsdsan_engine_mcp.models.physicality import reset_ph_solve_failures, reset_state_projection_failures

    reset_ph_solve_failures()
    reset_state_projection_failures()

    # Initialize convergence tracking variables
    converged_at = None
    conv_status = None
    conv_metrics = None
    actual_duration = duration_days
    simulation_method = method
    srt_days = None  # Phase 12: Track achieved SRT
    control_results = None

    # B3: checkpoint-restored resume + reconciliation. Restore the full B1 state
    # onto the freshly-compiled system, then (optionally) build a one-shot
    # ``resume_state_hook`` that projects the restored state toward the supplied
    # SCADA/lab measurements before the first resumed segment. Resume is a
    # fixed-duration transient: it is incompatible with the supervisory SRT
    # search and the steady-state convergence scan.
    resume_state_hook = None
    checkpoint_restore_summary = None
    reconciliation_summary = None
    resume_start_days = 0.0
    # Live control/disturbance objects restored from the checkpoint; threaded into
    # the resumed segment so PID integrals, disturbance baselines/targets, and the
    # held actuator side effects carry forward (NOT fresh-build defaults).
    resume_control_manager = None
    resume_disturbance_scheduler = None
    # B3: reconciliation inputs are only meaningful while resuming a restored
    # checkpoint (they project the RESTORED state before the first resumed
    # segment). Reject them outside a resume so they can never be silently ignored.
    if (reconciliation_measurements or reconciliation_options) and not resume_from_checkpoint:
        raise ValueError(
            "reconciliation_measurements/reconciliation_options require "
            "resume_from_checkpoint; state reconciliation only applies to a "
            "restored checkpoint before the first resumed segment"
        )
    if resume_from_checkpoint:
        if not isinstance(resume_from_checkpoint, dict):
            raise ValueError("resume_from_checkpoint must be a checkpoint payload dict")
        if target_srt_days is not None or run_to_convergence:
            raise ValueError(
                "resume_from_checkpoint cannot be combined with target_srt_days or "
                "run_to_convergence; resume runs a fixed-duration transient from the "
                "restored (and optionally reconciled) state"
            )
        if not getattr(system, "isdynamic", False):
            raise ValueError("resume_from_checkpoint requires a dynamic system")
        from qsdsan_engine_mcp.utils.state_checkpoint import restore_dynamic_checkpoint
        from qsdsan_engine_mcp.core.control import build_control_manager
        from qsdsan_engine_mcp.core.disturbance import build_disturbance_scheduler

        # Build the control manager + disturbance scheduler BEFORE restore so the
        # checkpoint's controller/disturbance/actuator state is overlaid onto LIVE
        # objects (restore_dynamic_checkpoint calls restore_control_state,
        # restore_disturbance_state, and apply_held_value_on_restore). These exact
        # objects are then handed to the resumed segment.
        resume_control_manager = build_control_manager(dynamic_controls)
        resume_disturbance_scheduler = build_disturbance_scheduler(disturbance_schedule)

        checkpoint_restore_summary = restore_dynamic_checkpoint(
            system,
            resume_from_checkpoint,
            control_manager=resume_control_manager,
            disturbance_scheduler=resume_disturbance_scheduler,
        )
        resume_start_days = float(resume_from_checkpoint.get("time_days", 0.0) or 0.0)
        if reconciliation_measurements:
            from qsdsan_engine_mcp.utils.state_reconciliation import build_observability_map, reconcile_restored_state

            _omap = build_observability_map(
                system,
                model_type=model_type,
                effluent_stream_ids=effluent_stream_ids,
            )

            def resume_state_hook(_sys, _meas=reconciliation_measurements,
                                  _opts=reconciliation_options, _map=_omap):
                nonlocal reconciliation_summary
                reconciliation_summary = reconcile_restored_state(_sys, _meas, _map, _opts)
                return reconciliation_summary

    # Phase 12: SRT control takes precedence if target_srt_days is set
    if target_srt_days is not None:
        # =====================================================================
        # SRT-controlled simulation (Phase 12)
        # =====================================================================
        from qsdsan_engine_mcp.utils.run_to_srt import run_to_target_srt
        from qsdsan_engine_mcp.utils.srt_control import detect_wastage_streams, has_srt_decoupling
        from qsdsan_engine_mcp.utils.convergence import get_convergence_components_for_model

        # Check if system has MBR/Clarifier (SRT decoupling)
        if not has_srt_decoupling(system):
            logger.warning(
                "No MBR or clarifier detected in system. "
                "SRT control is only effective for systems that decouple HRT and SRT."
            )

        # Step 1: Detect or use explicit effluent streams
        if convergence_stream_ids and 'effluent' in convergence_stream_ids:
            eff_ids = convergence_stream_ids['effluent']
            eff_streams = [s for s in system.streams if s and s.ID in eff_ids]
        else:
            if effluent_stream_ids:
                eff_streams = [s for s in system.streams if s and s.ID in effluent_stream_ids]
            else:
                eff_streams = _detect_effluent_streams(system)

        # Step 2: Detect wastage streams for SRT calculation
        if convergence_stream_ids and 'sludge' in convergence_stream_ids:
            sludge_ids = convergence_stream_ids['sludge']
            sludge_streams = [s for s in system.streams if s and s.ID in sludge_ids]
        else:
            sludge_streams = _detect_sludge_streams(system)

        explicit_was_streams = detect_wastage_streams(system)
        has_clarifier_actuator = any(
            type(unit).__name__ == 'FlatBottomCircularClarifier'
            for unit in getattr(system, 'units', [])
        )
        if explicit_was_streams:
            was_streams = explicit_was_streams
        elif has_clarifier_actuator and sludge_streams:
            was_streams = sludge_streams
        else:
            raise ValueError(
                "SRT control requires a detectable wastage stream. "
                "For MBR systems, add a Splitter output named 'WAS' or 'waste'. "
                "For clarifier systems, expose a sludge/wastage outlet."
            )

        # Step 3: Combine all convergence streams
        all_convergence_streams = eff_streams + [
            stream for stream in was_streams if stream not in eff_streams
        ]
        if not all_convergence_streams:
            logger.warning("No streams detected for convergence tracking, using system.products")
            all_convergence_streams = [s for s in system.products if s]

        trackable_streams = [stream for stream in all_convergence_streams if hasattr(stream, 'state')]
        if not trackable_streams:
            raise ValueError(
                "SRT control requires trackable effluent/wastage streams with dynamic state."
            )
        system.set_dynamic_tracker(*trackable_streams)

        # Step 4: Determine components to check
        if convergence_components is None:
            default_comps = get_convergence_components_for_model(model_type)
            conv_comps_map = {}
            for s in eff_streams:
                if s:
                    conv_comps_map[s.ID] = default_comps.get("effluent", ['S_NH4', 'S_NO3', 'S_O2'])
            for s in was_streams:
                if s:
                    conv_comps_map[s.ID] = default_comps.get("sludge", ['X_AUT', 'X_H', 'X_PAO'])
        else:
            conv_comps_map = {}
            for s in eff_streams:
                if s:
                    conv_comps_map[s.ID] = convergence_components.get("effluent", ['S_NH4', 'S_NO3'])
            for s in was_streams:
                if s:
                    conv_comps_map[s.ID] = convergence_components.get("sludge", ['X_AUT', 'X_H'])

        # Step 5: Set default max_time from target SRT (4x for margin)
        if max_duration_days is None:
            max_duration_days = max(100.0, target_srt_days * 4)

        logger.info(
            f"Running SRT-controlled simulation: target_srt={target_srt_days}d, "
            f"tolerance={srt_tolerance:.0%}, max_time={max_duration_days:.0f}d"
        )
        if dynamic_controls or disturbance_schedule:
            logger.warning(
                "dynamic_controls / disturbance_schedule were not applied inside "
                "target_srt_days mode; SRT remains a supervisory search in this "
                "engine path"
            )
            control_results = {
                "enabled": False,
                "reason": (
                    "dynamic_controls and disturbance_schedule are not applied in "
                    "target_srt_days mode; SRT remains the existing supervisory "
                    "run_to_srt search. Run a fixed-duration or run_to_convergence "
                    "transient for disturbance scenarios."
                ),
            }

        # Step 6: Run SRT-controlled simulation
        achieved_srt, srt_status, srt_metrics = run_to_target_srt(
            system=system,
            target_srt_days=target_srt_days,
            wastage_streams=was_streams,
            effluent_streams=None,  # MBR permeate typically has no solids
            convergence_streams=all_convergence_streams,
            convergence_components=conv_comps_map,
            model_type=model_type,
            srt_tolerance=srt_tolerance,
            max_srt_iterations=max_srt_iterations,
            min_time_multiplier=2.0,
            check_interval=check_interval_days,
            atol=convergence_atol,
            rtol=convergence_rtol,
            max_time=max_duration_days,
        )

        # Store results
        actual_duration = (
            srt_metrics.get('converged_at_days', srt_metrics.get('converged_at', max_duration_days))
            if srt_metrics else max_duration_days
        )
        simulation_method = 'BDF'
        converged_at = actual_duration
        conv_status = srt_status
        conv_metrics = srt_metrics
        srt_days = achieved_srt

        logger.info(
            f"SRT control complete: achieved_srt={achieved_srt:.1f}d "
            f"(target={target_srt_days}d), status={srt_status}"
        )

    elif run_to_convergence:
        # =====================================================================
        # Convergence-based simulation (Phase 11)
        # =====================================================================
        from qsdsan_engine_mcp.utils.run_to_convergence import run_system_to_steady_state
        from qsdsan_engine_mcp.utils.convergence import get_convergence_components_for_model

        # Step 1: Detect or use explicit effluent streams
        if convergence_stream_ids and 'effluent' in convergence_stream_ids:
            eff_ids = convergence_stream_ids['effluent']
            eff_streams = [s for s in system.streams if s and s.ID in eff_ids]
        else:
            # Use effluent_stream_ids parameter or auto-detect
            if effluent_stream_ids:
                eff_streams = [s for s in system.streams if s and s.ID in effluent_stream_ids]
            else:
                # Auto-detect using existing logic
                eff_streams = _detect_effluent_streams(system)

        # Step 2: Detect or use explicit sludge streams
        if convergence_stream_ids and 'sludge' in convergence_stream_ids:
            sludge_ids = convergence_stream_ids['sludge']
            sludge_streams = [s for s in system.streams if s and s.ID in sludge_ids]
        else:
            sludge_streams = _detect_sludge_streams(system)

        # Step 3: Combine all convergence streams
        all_convergence_streams = eff_streams + sludge_streams
        if not all_convergence_streams:
            logger.warning("No streams detected for convergence tracking, using system.products")
            all_convergence_streams = [s for s in system.products if s]

        # Set up dynamic tracking
        system.set_dynamic_tracker(*all_convergence_streams)

        # Step 4: Determine components to check
        if convergence_components is None:
            # Use defaults based on model type
            default_comps = get_convergence_components_for_model(model_type)
            conv_comps_map = {}
            for s in eff_streams:
                if s:
                    conv_comps_map[s.ID] = default_comps.get("effluent", ['S_NH4', 'S_NO3', 'S_O2'])
            for s in sludge_streams:
                if s:
                    conv_comps_map[s.ID] = default_comps.get("sludge", ['X_AUT', 'X_H', 'X_PAO'])
        else:
            # Map explicit components to stream IDs
            conv_comps_map = {}
            for s in eff_streams:
                if s:
                    conv_comps_map[s.ID] = convergence_components.get("effluent", ['S_NH4', 'S_NO3'])
            for s in sludge_streams:
                if s:
                    conv_comps_map[s.ID] = convergence_components.get("sludge", ['X_AUT', 'X_H'])

        # Step 5: Set default max_time
        if max_duration_days is None:
            # Default based on model type
            if model_type.upper() in ('MADM1', 'ADM1'):
                max_duration_days = 500.0
            else:
                max_duration_days = 100.0

        logger.info(
            f"Running to convergence: max_time={max_duration_days:.0f}d, "
            f"effluent_streams={[s.ID for s in eff_streams]}, "
            f"sludge_streams={[s.ID for s in sludge_streams]}"
        )

        # Step 6: Run to convergence
        converged_at, conv_status, conv_metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=all_convergence_streams,
            convergence_components=conv_comps_map,
            check_interval=check_interval_days,
            t_step=timestep_hours / 24,
            atol=convergence_atol,
            rtol=convergence_rtol,
            method='BDF',  # Always BDF for convergence
            max_time=max_duration_days,
            dynamic_controls=dynamic_controls,
            disturbance_schedule=disturbance_schedule,
        )
        control_results = (conv_metrics or {}).get("dynamic_controls") if conv_metrics else None

        actual_duration = converged_at
        simulation_method = 'BDF'

    else:
        # =====================================================================
        # Fixed-duration simulation (original behavior)
        # =====================================================================
        if getattr(system, "isdynamic", False):
            from qsdsan_engine_mcp.utils.dynamic_hydraulics import prefers_explicit_recycle_solver, stabilize_dynamic_hydraulics

            # Generate t_eval if not provided
            if t_eval is None:
                t_eval = np.arange(0, duration_days + timestep_hours / 24, timestep_hours / 24).tolist()

            t_span = (0, duration_days)

            # Build simulate kwargs
            sim_kwargs = {
                "t_span": t_span,
                "t_eval": t_eval,
                "method": method,
                "state_reset_hook": state_reset_hook,
                "max_step": timestep_hours / 24,
            }

            # Add track streams if provided (for dynamic tracking during simulation)
            if track:
                # QSDsan/BioSTEAM tracking is driven by ``set_dynamic_tracker``.
                # Passing ``track=...`` through to ``system.simulate`` is ignored by
                # the SciPy IVP layer.
                track_streams = []
                for stream_id in track:
                    for stream in system.streams:
                        if stream and stream.ID == stream_id:
                            track_streams.append(stream)
                            break
                if track_streams:
                    system.set_dynamic_tracker(*track_streams)

            # Run dynamic simulation. A cold fixed-duration start stabilizes the
            # hydraulics here (converges + freezes the recycle). A resume has
            # ALREADY been stabilized by restore_dynamic_checkpoint (the single
            # owner); re-stabilizing would re-converge the recycle and drift the
            # restored state (#17). prefers_explicit_recycle_solver still works on
            # the resumed system because restore installed the frozen-hydraulics
            # guards.
            if not resume_from_checkpoint:
                stabilize_dynamic_hydraulics(system)
            simulation_method = method
            if str(sim_kwargs["method"]).upper() == "BDF" and prefers_explicit_recycle_solver(system):
                logger.warning(
                    "Using RK23 for guarded high-recycle MBR hydraulics; dense-output BDF "
                    "is nonphysical for this QSDsan recycle topology"
                )
                sim_kwargs["method"] = "RK23"
                simulation_method = "RK23"
            try:
                if resume_from_checkpoint:
                    # Pass the LIVE restored manager/scheduler (not the raw specs)
                    # so the resumed segment continues from the checkpoint's PID
                    # integrals / disturbance baselines / held actuator state.
                    control_results = _run_resume_segment(
                        system,
                        dynamic_controls=resume_control_manager,
                        disturbance_schedule=resume_disturbance_scheduler,
                        duration_days=duration_days,
                        timestep_hours=timestep_hours,
                        method=sim_kwargs["method"],
                        t_eval=t_eval,
                        start_time_days=resume_start_days,
                        resume_state_hook=resume_state_hook,
                    )
                    simulation_method = f"{sim_kwargs['method']}+resume"
                elif dynamic_controls or disturbance_schedule:
                    from qsdsan_engine_mcp.utils.run_to_convergence import run_sampled_data_system

                    control_results = run_sampled_data_system(
                        system,
                        dynamic_controls=dynamic_controls,
                        disturbance_schedule=disturbance_schedule,
                        duration_days=duration_days,
                        timestep_days=timestep_hours / 24,
                        method=sim_kwargs["method"],
                        state_reset_hook=state_reset_hook,
                        t_eval=t_eval,
                        max_step_days=timestep_hours / 24,
                    )
                    _tags = []
                    if dynamic_controls:
                        _tags.append("controls")
                    if disturbance_schedule:
                        _tags.append("disturbances")
                    simulation_method = f"{sim_kwargs['method']}+sampled_{'_'.join(_tags)}"
                else:
                    system.simulate(**sim_kwargs)
            except Exception as exc:
                # A resume must never silently cold-restart (it would clobber the
                # restored/reconciled state); surface the failure instead.
                if resume_from_checkpoint:
                    raise
                error_str = str(exc).lower()
                if str(method).upper() == "BDF" and (
                    "invalid value" in error_str
                    or "could not converge" in error_str
                    or "array must not contain infs or nans" in error_str
                ):
                    logger.warning("BDF failed (%s), retrying fixed-duration solve with RK23", exc)
                    stabilize_dynamic_hydraulics(system)
                    sim_kwargs["method"] = "RK23"
                    if dynamic_controls or disturbance_schedule:
                        from qsdsan_engine_mcp.utils.run_to_convergence import run_sampled_data_system

                        control_results = run_sampled_data_system(
                            system,
                            dynamic_controls=dynamic_controls,
                            disturbance_schedule=disturbance_schedule,
                            duration_days=duration_days,
                            timestep_days=timestep_hours / 24,
                            method="RK23",
                            state_reset_hook=state_reset_hook,
                            t_eval=t_eval,
                            max_step_days=timestep_hours / 24,
                        )
                        _tags = []
                        if dynamic_controls:
                            _tags.append("controls")
                        if disturbance_schedule:
                            _tags.append("disturbances")
                        simulation_method = f"RK23+sampled_{'_'.join(_tags)}"
                    else:
                        system.simulate(**sim_kwargs)
                        simulation_method = "RK23"
                else:
                    raise

            actual_duration = duration_days
        else:
            # Static/non-dynamic systems should converge to a steady solution
            # rather than being pushed through the dynamic IVP API.
            system.converge()
            actual_duration = 0.0
            simulation_method = "converge"

    # Extract results
    results = _extract_simulation_results(
        system,
        model_type=model_type,
        effluent_stream_ids=effluent_stream_ids,
        biogas_stream_ids=biogas_stream_ids,
        include_components=include_components,
    )

    # Save diagram if output_dir provided
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            from qsdsan_engine_mcp.utils.diagram import save_system_diagram
            diagram_path = save_system_diagram(system, output_dir / "flowsheet")
            results["diagram_path"] = str(diagram_path)
        except Exception as e:
            logger.warning(f"Failed to generate diagram: {e}")

        # Extract and persist time-series data for tracked streams
        if track:
            try:
                time_series = _extract_flowsheet_timeseries(system, track)
                if time_series and time_series.get("success"):
                    # Save to output_dir
                    ts_path = output_dir / "timeseries.json"
                    with open(ts_path, "w") as f:
                        json.dump(time_series, f, indent=2)
                    results["timeseries_path"] = str(ts_path)
                    results["timeseries_available"] = True
            except Exception as e:
                logger.warning(f"Failed to extract time-series: {e}")

        try:
            stream_states = _extract_stream_states(system)
            if stream_states:
                stream_state_path = output_dir / "stream_states.json"
                with open(stream_state_path, "w") as f:
                    json.dump(stream_states, f, indent=2)
                results["stream_states_path"] = str(stream_state_path)
                results["stream_states_available"] = True
        except Exception as e:
            logger.warning(f"Failed to extract stream-state artifacts: {e}")

    # Export final state as StreamState JSON if requested
    if export_state_to:
        export_path = Path(export_state_to)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _export_stream_state(system, export_path, model_type, effluent_stream_ids)
            results["exported_state_path"] = str(export_path)
        except Exception as e:
            logger.warning(f"Failed to export stream state: {e}")

    # Add deterministic metadata (Phase 3C)
    import datetime
    try:
        import qsdsan as qs
        qsdsan_version = getattr(qs, "__version__", "unknown")
    except Exception:
        qsdsan_version = "unknown"

    try:
        import biosteam as bst
        biosteam_version = getattr(bst, "__version__", "unknown")
    except Exception:
        biosteam_version = "unknown"

    results["metadata"] = {
        "qsdsan_version": qsdsan_version,
        "biosteam_version": biosteam_version,
        "engine_version": get_version_info()["engine_version"],
        "solver": {
            "method": simulation_method,
            "duration_days": actual_duration,
            "timestep_hours": timestep_hours,
            "rtol": 1e-3,
            "atol": 1e-6,
            "run_to_convergence": run_to_convergence,
            "dynamic_controls": bool(dynamic_controls),
            "disturbance_schedule": bool(disturbance_schedule),
        },
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "model_type": model_type,
    }

    # Add simulation info
    results["simulation"] = {
        "duration_days": actual_duration,
        "method": simulation_method,
        "status": "completed",
        "run_to_convergence": run_to_convergence or target_srt_days is not None,
        "dynamic_controls": bool(dynamic_controls),
        "disturbance_schedule": bool(disturbance_schedule),
    }

    _apply_ph_solver_physicality_gate(results, system)

    # Add convergence info if applicable
    if run_to_convergence or target_srt_days is not None:
        results["simulation"]["converged_at_days"] = converged_at
        results["simulation"]["convergence_status"] = conv_status
        results["simulation"]["convergence_metrics"] = conv_metrics
        results["metadata"]["solver"]["convergence_atol"] = convergence_atol
        results["metadata"]["solver"]["convergence_rtol"] = convergence_rtol
        asm2d_summary = (results.get("per_engine_summaries") or {}).get("asm2d")
        if isinstance(asm2d_summary, dict) and isinstance(asm2d_summary.get("srt"), dict):
            asm2d_summary["srt"]["steady_state_status"] = conv_status
            asm2d_summary["srt"]["is_steady_state_srt"] = conv_status in {
                "converged",
                "srt_converged",
            }
            if not asm2d_summary["srt"]["is_steady_state_srt"]:
                asm2d_summary["srt"]["warning"] = (
                    "SRT was calculated on the final transient state because steady-state "
                    f"status was {conv_status!r}."
                )

    # Add SRT control info if applicable (Phase 12)
    if target_srt_days is not None:
        results["srt_control"] = {
            "target_srt_days": target_srt_days,
            "achieved_srt_days": srt_days,
            "srt_tolerance": srt_tolerance,
            "status": conv_status,
            "q_was_optimal": conv_metrics.get('q_was_optimal') if conv_metrics else None,
            "srt_iterations": conv_metrics.get('srt_iterations') if conv_metrics else None,
            "split_locked": conv_metrics.get('split_locked') if conv_metrics else None,
            "split_lock": conv_metrics.get('split_lock') if conv_metrics else None,
            "warnings": conv_metrics.get('warnings', []) if conv_metrics else [],
        }

    if control_results is not None:
        results["dynamic_controls"] = control_results
        if isinstance(control_results, dict) and "disturbances" in control_results:
            results["disturbances"] = control_results["disturbances"]

    # B3: surface the checkpoint-restore + reconciliation provenance.
    if checkpoint_restore_summary is not None:
        results["checkpoint_restore"] = checkpoint_restore_summary
        results["simulation"]["resumed_from_checkpoint"] = True
        results["simulation"]["resume_start_days"] = resume_start_days
        results["metadata"]["solver"]["resumed_from_checkpoint"] = True
    if reconciliation_summary is not None:
        results["state_reconciliation"] = reconciliation_summary

    return results


def _get_model_components(model_type: str) -> Tuple["CompiledComponents", "Thermo"]:
    """Get components and thermo for a model type.

    Model type mapping:
        - mADM1: Custom 63-component ModifiedADM1 with H2S/sulfide support
        - ADM1: Standard QSDsan ADM1 (35 components)
        - ASM2d: Standard QSDsan ASM2d (19 components)
        - ASM1: Standard QSDsan ASM1 (13 components)
        - mASM2d: Modified ASM2d with minerals/ions
    """
    import qsdsan as qs
    from qsdsan import process_models as pc

    if model_type == "mADM1":
        # Custom 63-component mADM1 with H2S modeling and sulfide precipitation
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps
        cmps = create_madm1_cmps()
    elif model_type in ("ADM1p", "ADM1_p_extension"):
        # Canonical upstream ADM1p chemistry package used by A1 junctions.
        from qsdsan_engine_mcp.core.a1_junction_units import get_canonical_adm1p_cmps
        cmps = get_canonical_adm1p_cmps()
        qs.set_thermo(cmps)
    elif model_type == "ADM1":
        # Standard QSDsan ADM1 (35 components)
        cmps = pc.create_adm1_cmps()
    elif model_type == "ASM2d":
        # Standard QSDsan ASM2d (19 components)
        cmps = pc.create_asm2d_cmps()
    elif model_type == "ASM1":
        # Standard QSDsan ASM1 (13 components)
        cmps = pc.create_asm1_cmps()
    elif model_type == "mASM2d":
        # Upstream modified ASM2d with ions and mineral species used by the A1
        # ADM1p <-> mASM2d junction path.
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_components
        cmps = create_masm2d_components()
    elif model_type == "mASM2d2N":
        # Two-step nitrification variant (+S_NO2, X_AOB, X_NOB).
        from qsdsan_engine_mcp.models.masm2d import create_masm2d_2n_components
        cmps = create_masm2d_2n_components()
    elif model_type == "mASM2d2N_N2O":
        # Two-step nitrification + AOB N2O variant (+S_NH2OH, S_NO, S_N2O).
        from qsdsan_engine_mcp.models.masm2d_n2o import create_masm2d_2n_n2o_components
        cmps = create_masm2d_2n_n2o_components()
    else:
        raise ValueError(f"Unknown model type: {model_type}")

    thermo = qs.get_thermo()
    return cmps, thermo


def _create_waste_stream(
    stream_id: str,
    config: "StreamConfig",
    cmps: "CompiledComponents",
    model_type: str = "ASM2d",
) -> "WasteStream":
    """Create a WasteStream from StreamConfig.

    Concentrations can be specified in either mg/L (default) or kg/m3,
    controlled by config.concentration_units. Internal conversion to mg/L
    is performed for kg/m3 inputs since QSDsan uses mg/L.

    Validates concentration bounds and warns if values suggest unit confusion
    (e.g., providing kg/m3 values when mg/L is expected or vice versa).
    """
    from qsdsan_engine_mcp.core.contracts import StreamState
    from qsdsan_engine_mcp.core.conversions.qsdsan_stream import to_qsdsan_stream

    conc_units = getattr(config, 'concentration_units', None)

    # Validate concentration bounds for likely unit confusion
    bound_warnings = validate_concentration_bounds(
        config.concentrations, model_type, conc_units
    )
    for warning in bound_warnings:
        logger.warning(f"Stream '{stream_id}': {warning}")

    stream_state = StreamState.from_concentrations(
        stream_id=stream_id,
        model_type=model_type,
        flow_m3_d=config.flow_m3_d,
        temperature_K=config.temperature_K,
        concentration_units=conc_units,
        concentrations=dict(config.concentrations),
    )
    return to_qsdsan_stream(stream_state)


def _resolve_single_input(
    input_ref: str,
    stream_registry: Dict[str, "WasteStream"],
    unit_registry: Dict[str, "SanUnit"],
    allow_missing: bool = False,
) -> Optional[Any]:
    """Resolve a single input reference to a stream/port or None.

    Args:
        input_ref: Port notation string (not tuple notation)
        stream_registry: Dict of stream_id -> WasteStream
        unit_registry: Dict of unit_id -> SanUnit
        allow_missing: If True, return None for missing refs (for deferred connections).
                      If False, raise ValueError for missing refs.

    Returns:
        WasteStream, output port, or None if allow_missing=True and not found

    Raises:
        ValueError: If allow_missing=False and the reference cannot be resolved
    """
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation

    ref = parse_port_notation(input_ref)

    if ref.port_type == "stream":
        # Direct stream reference
        if ref.unit_id in stream_registry:
            return stream_registry[ref.unit_id]
        else:
            # Stream not found - check if it's actually a unit ID (common mistake)
            if ref.unit_id in unit_registry:
                # User likely meant unit-0 (first output of the unit)
                src_unit = unit_registry[ref.unit_id]
                if len(src_unit.outs) > 0:
                    logger.debug(f"Input '{input_ref}' resolved as unit output (implicit -0)")
                    return src_unit.outs[0]
            if allow_missing:
                return None
            raise ValueError(
                f"Cannot resolve input '{input_ref}': stream not found. "
                f"Available streams: {list(stream_registry.keys())[:5]}{'...' if len(stream_registry) > 5 else ''}"
            )
    elif ref.port_type == "output":
        # Output port of another unit (e.g., "A1-0")
        if ref.unit_id in unit_registry:
            src_unit = unit_registry[ref.unit_id]
            if ref.index < len(src_unit.outs):
                return src_unit.outs[ref.index]
            if allow_missing:
                return None
            raise ValueError(
                f"Cannot resolve input '{input_ref}': unit '{ref.unit_id}' has no output port {ref.index}. "
                f"Unit has {len(src_unit.outs)} outputs (0-{len(src_unit.outs)-1})"
            )
        if allow_missing:
            return None
        raise ValueError(
            f"Cannot resolve input '{input_ref}': unit '{ref.unit_id}' not found. "
            f"Available units: {list(unit_registry.keys())[:5]}{'...' if len(unit_registry) > 5 else ''}"
        )
    elif ref.port_type == "direct":
        # Direct unit-to-unit connection (e.g., "U1-U2" or "U1-0-1-U2")
        # For inputs, we only care about the source unit's output
        if ref.unit_id in unit_registry:
            src_unit = unit_registry[ref.unit_id]
            if ref.index < len(src_unit.outs):
                return src_unit.outs[ref.index]
            if allow_missing:
                return None
            raise ValueError(
                f"Cannot resolve input '{input_ref}': unit '{ref.unit_id}' has no output port {ref.index}"
            )
        if allow_missing:
            return None
        raise ValueError(
            f"Cannot resolve input '{input_ref}': source unit '{ref.unit_id}' not found"
        )
    elif ref.port_type == "input":
        # Input port reference (e.g., "1-M1") - shouldn't be used as input source
        # This is unusual - typically inputs reference outputs or streams
        if allow_missing:
            return None
        raise ValueError(
            f"Cannot resolve input '{input_ref}': input port notation (e.g., '1-M1') "
            f"cannot be used as an input source. Use output notation (e.g., 'M1-0') instead."
        )
    else:
        if allow_missing:
            return None
        raise ValueError(f"Cannot resolve input '{input_ref}': unknown port type '{ref.port_type}'")


#: Models whose whole purpose is to resolve oxygen-limited nitrification. A bare
#: DO setpoint pins DO and zeroes its derivative, so oxygen limitation is
#: structurally unrepresentable -- for these models that is a configuration
#: error, not a default worth honouring silently.
_AERATION_FAIL_CLOSED_MODELS = frozenset({"mASM2d2N", "mASM2d2N_N2O"})

#: Models whose equilibrium constants are set at CONSTRUCTION from a temperature that
#: the rate function never sees again. Building one without a temperature pins the
#: whole acid-base basis at 25 C.
_TEMPERATURE_AT_CONSTRUCTION_MODELS = frozenset({"mASM2d2N", "mASM2d2N_N2O"})


def _resolve_model_temperature_K(
    kwargs: Dict[str, Any], *, unit_id: str, model_type: str | None
) -> float | None:
    """Operating temperature (K) for a construction-time-speciated process model.

    ``ModifiedASM2d`` and ``ModifiedASM2d_N2O`` derive ``Ka`` -- and therefore free
    ammonia and free nitrous acid -- from ``pKa`` AT CONSTRUCTION, because the rate
    function takes no temperature argument. Building one with no temperature leaves
    ammonium pinned at the 25 C value pKa 9.25 when a 35 C reactor's is 8.94, which
    UNDERSTATES free ammonia by a factor of 2.00 and understates NOB inhibition with
    it. The model's own docstring says so; until now only the twin path passed
    ``T_op_K``, so every flowsheet built through this function got the 25 C basis
    silently.

    ``T_op_K`` is POPPED: it configures the process model, not the reactor, and
    forwarding it would land it in the QSDsan constructor's ``**kwargs`` as a stray
    attribute. ``T`` is only read -- the aeration resolver needs it in place.

    Returns None when no temperature is available, having said plainly what that costs.
    """
    raw = kwargs.pop("T_op_K", None)
    if raw is None:
        raw = kwargs.get("T")
    if raw is None:
        if model_type in _TEMPERATURE_AT_CONSTRUCTION_MODELS:
            logger.warning(
                "Unit '%s': %s built with no operating temperature, so its acid-base "
                "constants stay at 25 C. At 35 C that understates free ammonia ~2x "
                "(pKa 9.25 vs 8.94) and free nitrous acid with it, which biases "
                "AOB/NOB inhibition towards a healthier plant than the real one. Set "
                "T_op_K (K) on the unit to fix it.",
                unit_id, model_type,
            )
        return None
    try:
        temperature = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"Unit '{unit_id}': T_op_K={raw!r} is not a temperature in kelvin."
        ) from None
    if not (273.15 <= temperature <= 373.15):
        raise ValueError(
            f"Unit '{unit_id}': T_op_K={temperature} K is outside 273.15-373.15 K. "
            "The Emerson/Harned correlations behind the speciation are fitted over "
            "0-50 C; extrapolating them is how a units slip (C for K) becomes a "
            "plausible-looking pH."
        )
    return temperature


def _resolve_aeration_kwarg(
    kwargs: Dict[str, Any], *, unit_id: str, model_type: str | None
) -> Dict[str, Any]:
    """Turn a declarative ``aeration`` spec dict into a real QSDsan Process.

    A float stays a float (the legacy ideal-DO behaviour, still valid for
    biology-only studies) except under a two-step nitrification model, where it
    is refused: those runs exist to answer oxygen questions that a pinned DO
    cannot express.

    Returns any intermittent duty cycles keyed by kwarg name, for the caller to
    stamp onto the unit once it is constructed. They cannot ride in ``kwargs``
    because everything there is forwarded to the QSDsan constructor.
    """
    duties: Dict[str, Any] = {}
    from qsdsan_engine_mcp.core.aeration_spec import AerationSpecError, build_diffused_aeration, is_aeration_spec, normalize_aeration_spec

    for key in ("aeration", "crossflow_air"):
        value = kwargs.get(key)

        if is_aeration_spec(value):
            volume = float(kwargs.get("V_max", 0.0) or 0.0)
            do_id = kwargs.get("DO_ID", "S_O2")
            resolved = normalize_aeration_spec(
                value, V_m3=volume, T_water_K=kwargs.get("T"), DO_ID=do_id
            )
            kwargs[key] = build_diffused_aeration(
                value, unit_id=unit_id, V_m3=volume, DO_ID=do_id,
                T_water_K=kwargs.get("T"),
            )
            if resolved["duty"] is not None:
                duties[key] = resolved["duty"]
            logger.info(
                "Unit '%s': built %s from a %s aeration spec (KLa=%.1f /d, DOsat=%.2f mg/L)",
                unit_id, key, resolved["mode"],
                float(kwargs[key].KLa), float(kwargs[key].DOsat),
            )

        elif key == "aeration" and isinstance(value, (int, float)) and value:
            if model_type in _AERATION_FAIL_CLOSED_MODELS:
                raise AerationSpecError(
                    f"Unit '{unit_id}': aeration={value} is a DO setpoint, which pins "
                    f"dissolved oxygen and forces its derivative to zero -- oxygen "
                    f"supply becomes infinite and oxygen limitation cannot be "
                    f"represented. Model '{model_type}' exists to resolve exactly that. "
                    f"Pass an aeration spec dict instead, e.g. "
                    f"{{'mode': 'fixed_airflow', 'Q_air_scfm': 1600, 'alpha': 0.6, "
                    f"'SOTE': 0.30, 'd_submergence': 5.0}}."
                )

    return duties


def _create_san_unit(
    unit_id: str,
    config: "UnitConfig",
    stream_registry: Dict[str, "WasteStream"],
    unit_registry: Dict[str, "SanUnit"],
    cmps: "CompiledComponents",
    model_type: str = "ASM2d",
) -> "SanUnit":
    """Create a SanUnit from UnitConfig with automatic process model instantiation.

    Args:
        unit_id: Unit identifier
        config: UnitConfig with unit_type, params, inputs, outputs
        stream_registry: Registry of created WasteStreams
        unit_registry: Registry of created SanUnits
        cmps: Components for this unit's model type
        model_type: Process model type (ASM2d, ASM1, mASM2d, ADM1, mADM1)

    Returns:
        Configured SanUnit instance
    """
    from qsdsan import unit_operations as sanunits
    from qsdsan_engine_mcp.core.unit_registry import get_unit_spec
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation

    spec = get_unit_spec(config.unit_type)

    # Populated only for aerated suspended-growth reactors carrying an
    # intermittent aeration spec; stamped onto the unit after construction.
    _pending_aeration_duties: Dict[str, Any] = {}

    # Resolve input streams/ports
    # Supports tuple fan-in notation like "(A1-0, B1-0)" for Mixer inputs
    # allow_missing=True for deferred connections (recycles wired later)
    ins = []
    for input_ref in config.inputs:
        # Check for tuple notation (fan-in)
        if is_tuple_notation(input_ref):
            # Parse tuple and resolve each port
            port_strs = parse_tuple_notation(input_ref)
            for port_str in port_strs:
                resolved = _resolve_single_input(
                    port_str, stream_registry, unit_registry, allow_missing=True
                )
                ins.append(resolved)
        else:
            resolved = _resolve_single_input(
                input_ref, stream_registry, unit_registry, allow_missing=True
            )
            ins.append(resolved)

    # Get the QSDsan class
    unit_class = _get_unit_class(spec.qsdsan_class)

    # Build kwargs
    kwargs = {"ID": unit_id}

    # Dynamic-capable utility units (Splitter, Mixer) default to isdynamic=False
    # in QSDsan even though they implement full dynamic state machinery
    # (_init_state/_update_state/_compile_AE). In a dynamic ASM/ADM system this
    # leaves them without _ins_QC, so any inlet whose sink is such a unit (e.g. an
    # MBR retentate feeding a WAS splitter) has no resolvable `.state`, breaking
    # both dynamic state propagation through recycles and convergence tracking.
    # Honor the registry's is_dynamic flag by forcing isdynamic=True when the
    # constructor accepts it (reactors already default to True, so this is a no-op
    # for them).
    if getattr(spec, "is_dynamic", False):
        try:
            if "isdynamic" in inspect.signature(unit_class.__init__).parameters:
                kwargs.setdefault("isdynamic", True)
        except (TypeError, ValueError):
            pass

    # Materialize documented registry defaults before applying user overrides.
    # This keeps the flowsheet surface aligned with UNIT_REGISTRY even when
    # QSDsan constructors do not expose the same Python defaults.
    for param, default in spec.optional_params.items():
        if default is not None:
            kwargs[param] = default

    # Ensure all units create WasteStreams (not base Streams) for dynamic simulation.
    # WasteStream has _state, iconc, and other attributes required by ODE integration.
    kwargs.setdefault("init_with", "WasteStream")

    def _register_named_outputs(unit: "SanUnit") -> "SanUnit":
        if config.outputs:
            for i, out_name in enumerate(config.outputs):
                if i < len(unit.outs) and out_name and unit.outs[i]:
                    unit.outs[i].ID = out_name
                    stream_registry[out_name] = unit.outs[i]
        return unit

    # Handle inputs based on unit type
    # Mixer uses ins=(...) tuple pattern for variable inputs
    # Splitter uses ins=single_stream
    if ins:
        # Filter out None values (deferred connections)
        valid_ins = [s for s in ins if s is not None]
        if config.unit_type == "Mixer":
            # Mixer: always pass as tuple even for single input
            # This enables adding more inputs later via deferred connections
            kwargs["ins"] = tuple(valid_ins) if valid_ins else ()
        elif len(valid_ins) == 1:
            kwargs["ins"] = valid_ins[0]
        elif len(valid_ins) > 1:
            kwargs["ins"] = valid_ins

    # Add unit-specific parameters
    for param, value in config.params.items():
        if value is not None:
            kwargs[param] = value

    if getattr(config, "aux_config", None):
        kwargs["aux_config"] = config.aux_config
    if getattr(config, "source_spec", None) is not None:
        kwargs["source_spec"] = config.source_spec

    # Registry exposes HydraulicDelay as tau for user consistency, but the
    # underlying QSDsan constructor expects t_delay.
    if config.unit_type == "HydraulicDelay" and "tau" in kwargs and "t_delay" not in kwargs:
        kwargs["t_delay"] = kwargs.pop("tau")

    # Auto-instantiate process models for reactors
    # Aerobic reactors (CSTR, MBR) need suspended_growth_model
    if config.unit_type in ("CSTR", "CompletelyMixedMBR"):
        from qsdsan import process_models as pc

        # Close the stock-ODE stripping hole (BUGS #2) at CONSTRUCTION, which is the
        # earliest point that always precedes a simulate(). stabilize_dynamic_hydraulics
        # installs it too, but a manually assembled session that never calls it -- the
        # documented create_unit / create_flowsheet_session route -- would otherwise
        # reach stock QSDsan's total-state stripping term with nothing to say so.
        from qsdsan_engine_mcp.utils.dynamic_hydraulics import install_speciated_stripping_guard

        install_speciated_stripping_guard()

        _T_op_K = _resolve_model_temperature_K(
            kwargs, unit_id=unit_id, model_type=model_type
        )
        if model_type == "ASM2d":
            kwargs.setdefault("suspended_growth_model", pc.ASM2d())
        elif model_type == "mASM2d":
            kwargs.setdefault("suspended_growth_model", pc.mASM2d())
        elif model_type == "mASM2d2N":
            from qsdsan_engine_mcp.models.masm2d import ModifiedASM2d
            kwargs.setdefault("suspended_growth_model", ModifiedASM2d(T_op_K=_T_op_K))
        elif model_type == "mASM2d2N_N2O":
            from qsdsan_engine_mcp.models.masm2d_n2o import ModifiedASM2d_N2O
            kwargs.setdefault("suspended_growth_model", ModifiedASM2d_N2O(T_op_K=_T_op_K))
        elif model_type == "ASM1":
            kwargs.setdefault("suspended_growth_model", pc.ASM1())
            # ASM1 uses S_O for dissolved oxygen, not S_O2
            # Check both kwargs and ensure correct DO component for ASM1
            if kwargs.get("DO_ID") == "S_O2":
                kwargs["DO_ID"] = "S_O"
                logger.debug(f"Unit '{unit_id}': Changed DO_ID from S_O2 to S_O for ASM1")
            elif "aeration" in kwargs and kwargs["aeration"] and "DO_ID" not in kwargs:
                # If aeration is set but DO_ID is missing, set it for ASM1
                kwargs["DO_ID"] = "S_O"
                logger.debug(f"Unit '{unit_id}': Set DO_ID to S_O for ASM1 aerated reactor")
        else:
            logger.warning(f"Unit '{unit_id}': No suspended_growth_model for model_type '{model_type}'")

        _pending_aeration_duties = _resolve_aeration_kwarg(
            kwargs, unit_id=unit_id, model_type=model_type
        )

    # Anaerobic reactors need anaerobic_digestion_model
    elif config.unit_type == "AnaerobicCSTR":
        from qsdsan import process_models as pc
        if model_type == "ADM1":
            kwargs.setdefault("model", pc.ADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnaerobicCSTR typically uses ADM1, got '{model_type}'")

    elif config.unit_type == "AnaerobicCSTRmADM1":
        # Custom mADM1 reactor uses ModifiedADM1 process model
        if model_type == "mADM1":
            import qsdsan as qs
            from qsdsan_engine_mcp.models.madm1 import ModifiedADM1
            kwargs.setdefault("thermo", qs.get_thermo())
            kwargs.setdefault("model", ModifiedADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnaerobicCSTRmADM1 requires mADM1, got '{model_type}'")

    # AnMBR (anaerobic MBR) also needs anaerobic model
    elif config.unit_type == "AnMBR":
        from qsdsan import process_models as pc
        if model_type == "ADM1":
            kwargs.setdefault("model", pc.ADM1())
        elif model_type == "mADM1":
            from qsdsan_engine_mcp.models.madm1 import ModifiedADM1
            kwargs.setdefault("model", ModifiedADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnMBR typically uses ADM1/mADM1, got '{model_type}'")

    elif config.unit_type == "ADM1ptomASM2d":
        # Canonical A1 path with full mADM1 input projection -> mASM2d.
        import qsdsan as qs
        from qsdsan_engine_mcp.core.a1_junction_units import ADM1ptomASM2d_full_custom, get_canonical_adm1p_model, get_canonical_masm2d_cmps, get_canonical_masm2d_model

        qs.set_thermo(get_canonical_masm2d_cmps())
        unit = ADM1ptomASM2d_full_custom(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            thermo=qs.get_thermo(),
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.adm1_model = get_canonical_adm1p_model()
        unit.asm2d_model = get_canonical_masm2d_model()
        return _register_named_outputs(unit)

    elif config.unit_type == "mASM2dtoADM1p":
        # Canonical A1 path with full mADM1 reconstruction.
        import qsdsan as qs
        from qsdsan_engine_mcp.core.a1_junction_units import get_canonical_adm1p_model, get_canonical_masm2d_model, mASM2dtoMADM1_custom
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps

        qs.set_thermo(create_madm1_cmps())
        unit = mASM2dtoMADM1_custom(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            thermo=qs.get_thermo(),
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.asm2d_model = get_canonical_masm2d_model()
        unit.adm1_model = get_canonical_adm1p_model()
        return _register_named_outputs(unit)

    # SBR also needs suspended_growth_model
    elif config.unit_type == "SBR":
        from qsdsan import process_models as pc
        _T_op_K = _resolve_model_temperature_K(
            kwargs, unit_id=unit_id, model_type=model_type
        )
        if model_type == "ASM2d":
            kwargs.setdefault("suspended_growth_model", pc.ASM2d())
        elif model_type == "mASM2d":
            kwargs.setdefault("suspended_growth_model", pc.mASM2d())
        elif model_type == "mASM2d2N":
            from qsdsan_engine_mcp.models.masm2d import ModifiedASM2d
            kwargs.setdefault("suspended_growth_model", ModifiedASM2d(T_op_K=_T_op_K))
        elif model_type == "mASM2d2N_N2O":
            from qsdsan_engine_mcp.models.masm2d_n2o import ModifiedASM2d_N2O
            kwargs.setdefault("suspended_growth_model", ModifiedASM2d_N2O(T_op_K=_T_op_K))
        elif model_type == "ASM1":
            kwargs.setdefault("suspended_growth_model", pc.ASM1())

    # Create unit
    unit = unit_class(**kwargs)

    # Intermittent aeration duty cycles cannot ride in kwargs (everything there is
    # forwarded to the QSDsan constructor), so stamp them now. The guarded ODE
    # reads this attribute and evaluates the cycle on t.
    if _pending_aeration_duties:
        from qsdsan_engine_mcp.core.aeration_spec import DUTY_ATTR

        for _kwarg_name, _duty in _pending_aeration_duties.items():
            if _kwarg_name == "aeration":
                setattr(unit, DUTY_ATTR, _duty)
            else:
                setattr(unit, f"{DUTY_ATTR}_{_kwarg_name}", _duty)
        logger.info(
            "Unit '%s': intermittent aeration attached (%s)",
            unit_id,
            ", ".join(
                f"{k}: {d['on_d'] * 1440:.1f} on / {(d['period_d'] - d['on_d']) * 1440:.1f} off min"
                for k, d in _pending_aeration_duties.items()
            ),
        )

    if config.unit_type == "Splitter":
        engineering_inputs = getattr(config, "engineering_inputs", {}) or {}
        aux_config = getattr(config, "aux_config", {}) or {}
        explicit_lock = (
            engineering_inputs.get("split_locked")
            if "split_locked" in engineering_inputs
            else engineering_inputs.get("lock_split")
            if "lock_split" in engineering_inputs
            else aux_config.get("split_locked")
            if "split_locked" in aux_config
            else (aux_config.get("srt_control") or {}).get("split_locked")
            if isinstance(aux_config.get("srt_control"), dict)
            else None
        )
        srt_actuator_seed = (
            engineering_inputs.get("q_was_m3_d") is not None
            and engineering_inputs.get("target_srt_days") is not None
        )
        split_is_prescribed = bool(explicit_lock) if explicit_lock is not None else (
            engineering_inputs.get("split") is not None and not srt_actuator_seed
        )
        unit._srt_control_split_locked = split_is_prescribed
        if split_is_prescribed:
            unit._srt_control_prescribed_split = (
                engineering_inputs.get("split")
                if engineering_inputs.get("split") is not None
                else config.params.get("split", getattr(unit, "split", None))
            )

    return _register_named_outputs(unit)


def _get_unit_class(qsdsan_class: str):
    """Import and return a unit class from its path."""
    from qsdsan import unit_operations as sanunits

    # Handle qsdsan.sanunits.ClassName format
    if qsdsan_class.startswith("qsdsan.sanunits."):
        class_name = qsdsan_class.split(".")[-1]
        if hasattr(sanunits, class_name):
            return getattr(sanunits, class_name)
        raise ValueError(f"Unit class '{class_name}' not found in qsdsan.sanunits")

    # Handle custom classes (e.g., models.reactors.AnaerobicCSTRmADM1)
    parts = qsdsan_class.rsplit(".", 1)
    if len(parts) == 2:
        module_path, class_name = parts
        import importlib
        try:
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Failed to import '{qsdsan_class}': {e}")

    raise ValueError(f"Invalid qsdsan_class path: {qsdsan_class}")


def _wire_connection(
    conn: "ConnectionConfig",
    unit_registry: Dict[str, "SanUnit"],
    stream_registry: Dict[str, "WasteStream"],
    recycle_streams: List["WasteStream"],
) -> None:
    """Wire a deferred connection between units.

    Handles all pipe notation formats:
    - Output notation: {"from": "A1-0", "to": "B1"} or {"from": "A1-0", "to": "1-B1"}
    - Direct notation: {"from": "A1-B1", "to": None} (U1-U2 format)
    - Explicit notation: {"from": "A1-0-1-B1", "to": None} (U1-0-1-U2 format)

    Special handling for Mixer units:
    - Mixers have variable inputs via ins=(...) tuple
    - If target input index exceeds current ins length, the connection is appended
    """
    from qsdsan import WasteStream
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation

    def _assign_to_input_port(dst_unit: "SanUnit", dst_in_idx: int, src_port: "WasteStream"):
        """Assign source port to destination unit's input, handling Mixer specially."""
        unit_type = type(dst_unit).__name__

        def _refresh_dynamic_input_cache() -> None:
            # QSDsan sizes _ins_QC/_ins_dQC when a dynamic unit is constructed.
            # Deferred recycle fan-in mutates ``ins`` after construction, so the
            # dynamic input cache must be rebuilt before System.dynamic_run.
            if getattr(dst_unit, "isdynamic", False) and hasattr(dst_unit, "_init_dynamic"):
                dst_unit._init_dynamic()

        def _append_deferred_placeholder(index: int) -> None:
            dst_unit.ins.append(
                WasteStream(
                    ID=f"{dst_unit.ID}_deferred_in{index}",
                    thermo=getattr(dst_unit, "thermo", None),
                )
            )

        # Check if port exists
        if dst_in_idx < len(dst_unit.ins):
            dst_unit.ins[dst_in_idx] = src_port
            _refresh_dynamic_input_cache()
            return

        # Port doesn't exist - check if unit supports variable inputs.
        # Public CSTR/Tank wrappers and BioSTEAM mixers are expected to accept
        # recycled fan-in even when the initial constructor only saw one input.
        if unit_type in ("Mixer", "MixTank", "Tank", "CSTR"):
            # Find next available empty slot or append
            for i, existing_in in enumerate(dst_unit.ins):
                if existing_in is None or (hasattr(existing_in, 'F_mass') and existing_in.F_mass == 0):
                    dst_unit.ins[i] = src_port
                    logger.debug(f"Assigned to empty slot {i} on {dst_unit.ID}")
                    return

            # No empty slot - append for units whose public contract allows fan-in.
            if unit_type in ("Mixer", "MixTank", "Tank", "CSTR"):
                while len(dst_unit.ins) < dst_in_idx:
                    _append_deferred_placeholder(len(dst_unit.ins))
                dst_unit.ins.append(src_port)
                _refresh_dynamic_input_cache()
                logger.debug(f"Appended {src_port.ID} to {dst_unit.ID}.ins")
            else:
                raise ValueError(
                    f"Unit '{dst_unit.ID}' has {len(dst_unit.ins)} inputs, "
                    f"cannot wire to port {dst_in_idx}"
                )
        else:
            raise ValueError(
                f"Unit '{dst_unit.ID}' ({unit_type}) has no input port {dst_in_idx}. "
                f"Unit has {len(dst_unit.ins)} inputs (0-{len(dst_unit.ins)-1})"
            )

    from_ref = parse_port_notation(conn.from_port)

    # Handle direct notation (U1-U2 or U1-0-1-U2) where source and target are in from_port
    if from_ref.port_type == "direct":
        # Source unit and port
        if from_ref.unit_id not in unit_registry:
            raise ValueError(f"Source unit '{from_ref.unit_id}' not found")
        src_unit = unit_registry[from_ref.unit_id]

        src_out_idx = from_ref.index  # Default 0 for U1-U2, explicit for U1-0-1-U2
        if src_out_idx >= len(src_unit.outs):
            raise ValueError(f"Unit '{from_ref.unit_id}' has no output port {src_out_idx}")
        src_port = src_unit.outs[src_out_idx]

        # Target unit and port (embedded in from_ref)
        if from_ref.target_unit_id not in unit_registry:
            raise ValueError(f"Target unit '{from_ref.target_unit_id}' not found")
        dst_unit = unit_registry[from_ref.target_unit_id]

        dst_in_idx = from_ref.target_index if from_ref.target_index is not None else 0

        # Wire the connection using helper
        _assign_to_input_port(dst_unit, dst_in_idx, src_port)

        # Track as recycle stream
        if src_port not in recycle_streams:
            recycle_streams.append(src_port)
        return

    # Standard notation: from_port is output, to_port is input destination
    to_ref = parse_port_notation(conn.to_port)

    # Get source port (from output notation like "A1-0")
    if from_ref.unit_id not in unit_registry:
        raise ValueError(f"Source unit '{from_ref.unit_id}' not found")

    src_unit = unit_registry[from_ref.unit_id]
    src_out_idx = from_ref.index if from_ref.index >= 0 else 0
    if src_out_idx >= len(src_unit.outs):
        raise ValueError(f"Unit '{from_ref.unit_id}' has no output port {src_out_idx}")

    src_port = src_unit.outs[src_out_idx]

    # Get destination port
    if to_ref.port_type == "stream":
        # Destination is a unit ID (e.g., "B1"). If a variable-input unit has
        # no empty slots, use the next index so the helper appends instead of
        # overwriting an occupied influent port.
        if to_ref.unit_id not in unit_registry:
            raise ValueError(f"Destination unit '{to_ref.unit_id}' not found")
        dst_unit = unit_registry[to_ref.unit_id]
        dst_in_idx = len(dst_unit.ins)
        for i, existing_in in enumerate(dst_unit.ins):
            if existing_in is None or (hasattr(existing_in, 'F_mass') and existing_in.F_mass == 0):
                dst_in_idx = i
                break
    elif to_ref.port_type == "input":
        # Explicit input notation (e.g., "1-B1")
        if to_ref.unit_id not in unit_registry:
            raise ValueError(f"Destination unit '{to_ref.unit_id}' not found")
        dst_unit = unit_registry[to_ref.unit_id]
        dst_in_idx = to_ref.index
    else:
        raise ValueError(
            f"Invalid to_port '{conn.to_port}': expected input notation or unit ID"
        )

    # Wire the connection using helper
    _assign_to_input_port(dst_unit, dst_in_idx, src_port)

    # Track as recycle stream
    if src_port not in recycle_streams:
        recycle_streams.append(src_port)


def _extract_flowsheet_timeseries(
    system: "System",
    track: List[str],
) -> Dict[str, Any]:
    """
    Extract time-series data from tracked streams after simulation.

    Args:
        system: Completed QSDsan System
        track: List of stream IDs that were tracked

    Returns:
        Dict with time and component trajectories for each tracked stream
    """
    import numpy as np

    result = {
        "success": False,
        "time": [],
        "time_units": "days",
        "streams": {},
        "component_evidence_version": 1,
        "omitted_zero_components": {},
        "unavailable_streams": [],
        "unavailable_components": {},
        "component_findings": [],
    }

    try:
        # Find tracked streams
        tracked_streams = []
        for stream_id in track:
            for stream in system.streams:
                if stream and stream.ID == stream_id:
                    tracked_streams.append(stream)
                    break

        result["unavailable_streams"] = sorted(set(track) - {s.ID for s in tracked_streams})

        if not tracked_streams:
            result["message"] = "No tracked streams found"
            return result

        # Get time array from first tracked stream's scope
        first_stream = tracked_streams[0]
        if not hasattr(first_stream, 'scope') or first_stream.scope is None:
            result["message"] = "No tracking scope available (simulation may not have tracked streams)"
            return result

        scope = first_stream.scope
        if not hasattr(scope, 'time_series') or scope.time_series is None:
            result["message"] = "No time_series attribute on scope"
            return result

        time_arr = scope.time_series
        if isinstance(time_arr, np.ndarray):
            result["time"] = time_arr.tolist()
        else:
            result["time"] = list(time_arr)

        # Extract component trajectories for each tracked stream
        for stream in tracked_streams:
            stream_data = {}
            zero_components = []
            unavailable_components = set(stream.components.IDs)
            try:
                if hasattr(stream, 'scope') and stream.scope is not None:
                    scope = stream.scope
                    if hasattr(scope, 'record'):
                        record = scope.record
                        if record is not None and len(record) > 0:
                            # Get component IDs
                            comp_ids = stream.components.IDs
                            for i, comp_id in enumerate(comp_ids):
                                if i < record.shape[1]:
                                    values = record[:, i]
                                    negative = values < 0
                                    # Retain signed evidence; absence is not a zero.
                                    if np.any(values > 0) or np.any(negative):
                                        if isinstance(values, np.ndarray):
                                            stream_data[comp_id] = values.tolist()
                                        else:
                                            stream_data[comp_id] = list(values)
                                        unavailable_components.discard(comp_id)
                                        if np.any(negative):
                                            result["component_findings"].append({
                                                "code": "NEGATIVE_COMPONENT_VALUES",
                                                "stream_id": stream.ID,
                                                "component_id": comp_id,
                                                "negative_count": int(np.count_nonzero(negative)),
                                                "minimum_negative_value": float(np.min(values[negative])),
                                                "standing": "requires_numerical_or_model_review",
                                            })
                                    elif np.all(values == 0):
                                        zero_components.append(comp_id)
                                        unavailable_components.discard(comp_id)
            except Exception as e:
                logger.warning(f"Error extracting time-series for {stream.ID}: {e}")

            if stream_data:
                result["streams"][stream.ID] = stream_data
            if zero_components:
                result["omitted_zero_components"][stream.ID] = sorted(zero_components)
            if unavailable_components:
                result["unavailable_components"][stream.ID] = sorted(unavailable_components)

        result["success"] = bool(result["streams"] or result["omitted_zero_components"])
        result["message"] = f"Extracted {len(result['streams'])} streams with {len(result['time'])} time points"

    except Exception as e:
        result["message"] = f"Error extracting time-series: {str(e)}"
        logger.error(f"Time-series extraction failed: {e}", exc_info=True)

    return result



def _extract_unit_analysis(
    system: "System",
    model_type: str = "ASM2d",
) -> Dict[str, Any]:
    """
    Extract per-unit performance data for every SanUnit in the system.

    This provides detailed analysis for each unit including:
    - Inlet/outlet stream characteristics
    - Unit-specific parameters (volume, HRT, etc.)
    - Removal/conversion efficiencies across the unit
    - Unit type and configuration

    Args:
        system: Completed QSDsan System
        model_type: Process model type for component interpretation

    Returns:
        Dict mapping unit_id -> unit analysis data
    """
    from qsdsan_engine_mcp.utils.stream_analysis import analyze_aerobic_stream, analyze_liquid_stream, calculate_removal_efficiency

    units_data = {}

    def unit_is_anaerobic(unit_obj) -> bool:
        unit_name = type(unit_obj).__name__
        if is_anaerobic:
            return True
        return any(
            token in unit_name
            for token in (
                "Anaerobic",
                "ADM1",
                "AnMBR",
                "InternalCirculationRx",
                "AnaerobicBaffledReactor",
            )
        )

    # Normalize model_type
    mt = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
    mt = str(mt).upper()
    is_anaerobic = mt in ("MADM1", "ADM1")

    for unit in system.units:
        try:
            unit_id = unit.ID
            unit_type = type(unit).__name__
            unit_anaerobic = unit_is_anaerobic(unit)

            # Basic unit info
            unit_data = {
                "unit_id": unit_id,
                "unit_type": unit_type,
                "inlet_ids": [s.ID for s in unit.ins if s],
                "outlet_ids": [s.ID for s in unit.outs if s],
            }

            # Extract unit-specific parameters
            params = {}

            # Volume parameters
            if hasattr(unit, 'V_max'):
                params['V_max_m3'] = float(unit.V_max) if unit.V_max else None
            if hasattr(unit, 'V_liq'):
                params['V_liq_m3'] = float(unit.V_liq) if unit.V_liq else None
            if hasattr(unit, 'V_gas'):
                params['V_gas_m3'] = float(unit.V_gas) if unit.V_gas else None

            # HRT calculation
            if hasattr(unit, 'HRT'):
                params['HRT_days'] = float(unit.HRT) * 24 if unit.HRT else None  # hr -> days
            elif hasattr(unit, 'tau'):
                params['HRT_days'] = float(unit.tau) * 24 if unit.tau else None  # hr -> days

            # Temperature
            if hasattr(unit, 'T'):
                params['temperature_K'] = float(unit.T) if unit.T else None

            # Aeration/DO (for aerobic units)
            if hasattr(unit, 'aeration'):
                params['aeration_kLa'] = float(unit.aeration) if unit.aeration else None
            if hasattr(unit, 'DO_ID'):
                params['DO_component'] = unit.DO_ID

            # Split ratio (for splitters)
            if hasattr(unit, 'split'):
                split_val = unit.split
                # Handle array splits (per-component) vs scalar splits
                if split_val is not None:
                    import numpy as np
                    if isinstance(split_val, np.ndarray):
                        # For array splits, report the mean or first value
                        params['split_ratio'] = float(split_val[0]) if len(split_val) > 0 else None
                        params['split_is_array'] = True
                    elif hasattr(split_val, '__len__') and not isinstance(split_val, str):
                        params['split_ratio'] = float(split_val[0]) if len(split_val) > 0 else None
                        params['split_is_array'] = True
                    else:
                        params['split_ratio'] = float(split_val)
                else:
                    params['split_ratio'] = None

            unit_data['parameters'] = params

            # Analyze inlet streams
            inlet_analysis = []
            for inlet in unit.ins:
                if inlet:
                    try:
                        if unit_anaerobic:
                            analysis = analyze_liquid_stream(inlet)
                        else:
                            analysis = analyze_aerobic_stream(inlet)
                        inlet_analysis.append({
                            "stream_id": inlet.ID,
                            "flow_m3_d": float(inlet.F_vol * 24) if hasattr(inlet, 'F_vol') else None,
                            "COD_mg_L": float(inlet.COD) if hasattr(inlet, 'COD') and inlet.COD else None,
                            "TSS_mg_L": float(inlet.get_TSS()) if hasattr(inlet, 'get_TSS') else None,
                            "temperature_K": float(inlet.T) if hasattr(inlet, 'T') else None,
                            **{k: v for k, v in analysis.items() if k not in ['success', 'stream_id']}
                        })
                    except Exception as e:
                        inlet_analysis.append({
                            "stream_id": inlet.ID,
                            "error": str(e),
                        })

            unit_data['inlets'] = inlet_analysis

            # Analyze outlet streams
            outlet_analysis = []
            for outlet in unit.outs:
                if outlet:
                    try:
                        # Check if this is a gas stream (biogas)
                        is_gas = any(x in outlet.ID.lower() for x in ['biogas', 'gas', 'off_gas'])
                        if is_gas and unit_anaerobic:
                            from qsdsan_engine_mcp.utils.stream_analysis import analyze_gas_stream
                            analysis = analyze_gas_stream(outlet)
                            outlet_analysis.append({
                                "stream_id": outlet.ID,
                                "stream_type": "gas",
                                "flow_m3_d": float(outlet.F_vol * 24) if hasattr(outlet, 'F_vol') else None,
                                **{k: v for k, v in analysis.items() if k not in ['success', 'stream_id']}
                            })
                        else:
                            if unit_anaerobic:
                                analysis = analyze_liquid_stream(outlet)
                            else:
                                analysis = analyze_aerobic_stream(outlet)
                            outlet_analysis.append({
                                "stream_id": outlet.ID,
                                "stream_type": "liquid",
                                "flow_m3_d": float(outlet.F_vol * 24) if hasattr(outlet, 'F_vol') else None,
                                "COD_mg_L": float(outlet.COD) if hasattr(outlet, 'COD') and outlet.COD else None,
                                "TSS_mg_L": float(outlet.get_TSS()) if hasattr(outlet, 'get_TSS') else None,
                                "temperature_K": float(outlet.T) if hasattr(outlet, 'T') else None,
                                **{k: v for k, v in analysis.items() if k not in ['success', 'stream_id']}
                            })
                    except Exception as e:
                        outlet_analysis.append({
                            "stream_id": outlet.ID,
                            "error": str(e),
                        })

            unit_data['outlets'] = outlet_analysis

            # Calculate removal efficiency across the unit (if applicable)
            if inlet_analysis and outlet_analysis:
                # Use first inlet and first liquid outlet
                first_inlet = inlet_analysis[0] if inlet_analysis else None
                first_outlet = next(
                    (o for o in outlet_analysis if o.get('stream_type') != 'gas'),
                    outlet_analysis[0] if outlet_analysis else None
                )

                if first_inlet and first_outlet:
                    removal = {}
                    inlet_cod = first_inlet.get('COD_mg_L')
                    outlet_cod = first_outlet.get('COD_mg_L')
                    if inlet_cod and outlet_cod and inlet_cod > 0:
                        removal['COD_removal_pct'] = calculate_removal_efficiency(inlet_cod, outlet_cod)

                    inlet_tss = first_inlet.get('TSS_mg_L')
                    outlet_tss = first_outlet.get('TSS_mg_L')
                    if inlet_tss and outlet_tss and inlet_tss > 0:
                        removal['TSS_removal_pct'] = calculate_removal_efficiency(inlet_tss, outlet_tss)

                    # TN removal (for aerobic)
                    inlet_tn = first_inlet.get('TN_mg_L') or first_inlet.get('TKN_mg_L')
                    outlet_tn = first_outlet.get('TN_mg_L') or first_outlet.get('TKN_mg_L')
                    if inlet_tn and outlet_tn and inlet_tn > 0:
                        removal['TN_removal_pct'] = calculate_removal_efficiency(inlet_tn, outlet_tn)

                    if removal:
                        unit_data['removal_efficiency'] = removal

            units_data[unit_id] = unit_data

        except Exception as e:
            logger.warning(f"Failed to analyze unit {unit.ID}: {e}")
            units_data[unit.ID] = {
                "unit_id": unit.ID,
                "error": str(e),
            }

    return units_data


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    return str(value)


def _stream_component_concentrations(stream: Any) -> Dict[str, float]:
    concentrations: Dict[str, float] = {}
    components = getattr(stream, "components", None)
    iconc = getattr(stream, "iconc", None)
    for comp_id in getattr(components, "IDs", ()) or ():
        try:
            value = float(iconc[comp_id])
        except Exception:
            continue
        if value > 0:
            concentrations[str(comp_id)] = value
    return concentrations


def _stream_summary(stream: Any) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "stream_id": str(getattr(stream, "ID", "")),
        "phase": getattr(stream, "phase", None),
    }
    flow = _to_float(getattr(stream, "F_vol", None))
    if flow is not None:
        summary["flow_m3_d"] = flow * 24.0
    temperature = _to_float(getattr(stream, "T", None))
    if temperature is not None:
        summary["temperature_K"] = temperature
    for attr, key in {
        "COD": "COD_mg_L",
        "TN": "TN_mg_L",
        "TP": "TP_mg_L",
        "TDS": "TDS_mg_L",
    }.items():
        value = _to_float(getattr(stream, attr, None))
        if value is not None:
            summary[key] = value
    get_tss = getattr(stream, "get_TSS", None)
    if callable(get_tss):
        tss = _to_float(get_tss())
        if tss is not None:
            summary["TSS_mg_L"] = tss
    components = _stream_component_concentrations(stream)
    if components:
        summary["components"] = components
    return summary


def _extract_predicted_streams_from_system(system: "System") -> Dict[str, Dict[str, Any]]:
    predicted: Dict[str, Dict[str, Any]] = {}
    for stream in getattr(system, "outs", ()) or ():
        if not stream:
            continue
        stream_id = str(getattr(stream, "ID", ""))
        if stream_id:
            predicted[stream_id] = _stream_summary(stream)
    return predicted


def _component_ids_for_unit(unit: Any) -> List[str]:
    candidates: list[Any] = []
    candidates.extend(getattr(unit, "outs", ()) or ())
    candidates.extend(getattr(unit, "ins", ()) or ())
    for stream in candidates:
        components = getattr(stream, "components", None)
        ids = [str(c) for c in (getattr(components, "IDs", ()) or ())]
        if ids:
            return ids
    components = getattr(unit, "components", None)
    return [str(c) for c in (getattr(components, "IDs", ()) or ())]


def _flatten_state_vector(state: Any) -> List[Any]:
    values = _jsonable(state)
    if isinstance(values, list) and values and isinstance(values[-1], list):
        values = values[-1]
    if isinstance(values, list):
        return values
    return []


def _state_component_map(unit: Any) -> Dict[str, float]:
    component_ids = _component_ids_for_unit(unit)
    values = _flatten_state_vector(getattr(unit, "state", None))
    if not component_ids or not values:
        return {}
    out: Dict[str, float] = {}
    for comp_id, value in zip(component_ids, values):
        numeric = _to_float(value)
        if numeric is not None:
            out[comp_id] = numeric
    return out


def _looks_biological_unit(unit: Any) -> bool:
    label = f"{getattr(unit, 'ID', '')} {type(unit).__name__}".lower()
    return any(
        token in label
        for token in (
            "aerobic",
            "anaerobic",
            "anoxic",
            "oxic",
            "activated",
            "asm",
            "adm",
            "cstr",
            "reactor",
            "mbr",
        )
    )


def _extract_unit_mlss(unit: Any, state_map: Dict[str, float]) -> Optional[float]:
    for stream in getattr(unit, "outs", ()) or ():
        get_tss = getattr(stream, "get_TSS", None)
        if callable(get_tss):
            value = _to_float(get_tss())
            if value is not None:
                return value
    solids = [
        value
        for key, value in state_map.items()
        if key.startswith("X_") or key in {"X_H", "X_AUT", "X_I", "X_BH", "X_BA"}
    ]
    if solids:
        return float(sum(solids))
    return None


def _extract_asm_bio_from_system(system: "System") -> Dict[str, Any]:
    biological_units: Dict[str, Dict[str, Any]] = {}
    top_level_values: Dict[str, list[float]] = {"X_H": [], "X_AUT": [], "X_I": []}
    for unit in getattr(system, "units", ()) or ():
        if not _looks_biological_unit(unit):
            continue
        state_map = _state_component_map(unit)
        unit_payload: Dict[str, Any] = {
            key: state_map[key]
            for key in ("X_H", "X_AUT", "X_I")
            if key in state_map
        }
        for key in top_level_values:
            if key in state_map:
                top_level_values[key].append(state_map[key])
        mlss = _extract_unit_mlss(unit, state_map)
        if mlss is not None:
            unit_payload["MLSS"] = mlss
        srt = _to_float(getattr(unit, "SRT", None))
        if srt is not None:
            unit_payload["SRT"] = srt
        if unit_payload:
            biological_units[str(getattr(unit, "ID", type(unit).__name__))] = unit_payload

    asm_bio: Dict[str, Any] = {}
    for key, values in top_level_values.items():
        if values:
            asm_bio[key] = sum(values) / len(values)
    if biological_units:
        asm_bio["biological_unit_states"] = biological_units
    return asm_bio


def _capture_system_svg(system: "System") -> str:
    diagram = getattr(system, "diagram", None)
    if not callable(diagram):
        raise RuntimeError("QSDsan system has no diagram() method")
    graph = diagram(kind="thorough", format="svg", display=False, number=True, label=True)
    if isinstance(graph, bytes):
        return graph.decode("utf-8").strip()
    if isinstance(graph, str):
        return graph.strip()
    pipe = getattr(graph, "pipe", None)
    if callable(pipe):
        svg = pipe(format="svg")
        if isinstance(svg, bytes):
            return svg.decode("utf-8").strip()
        if isinstance(svg, str):
            return svg.strip()
    repr_svg = getattr(graph, "_repr_svg_", None)
    if callable(repr_svg):
        svg = repr_svg()
        if isinstance(svg, str):
            return svg.strip()
    raise RuntimeError("QSDsan system.diagram(format='svg') did not return SVG content")


def _capture_sfiles_string(system: "System") -> str:
    get_sfiles = getattr(system, "get_SFILES", None)
    if callable(get_sfiles):
        rendered = get_sfiles()
        if rendered is not None:
            return str(rendered).strip()
    units = [str(getattr(unit, "ID", type(unit).__name__)) for unit in getattr(system, "units", ()) or ()]
    streams = [str(getattr(stream, "ID", "")) for stream in getattr(system, "streams", ()) or () if stream]
    return "\n".join(
        [
            f"System: {getattr(system, 'ID', '')}",
            f"Units: {', '.join(units)}",
            f"Streams: {', '.join(streams)}",
        ]
    ).strip()


def _capture_state_summary(system: "System") -> Dict[str, Any]:
    get_state_summary = getattr(system, "get_state_summary", None)
    if callable(get_state_summary):
        summary = get_state_summary()
        if isinstance(summary, dict):
            return _jsonable(summary)
        return {"summary": _jsonable(summary)}
    return {
        str(getattr(unit, "ID", type(unit).__name__)): state_map
        for unit in getattr(system, "units", ()) or ()
        if (state_map := _state_component_map(unit))
    }


def _capture_in_memory_diagnostics(system: "System") -> Dict[str, Any]:
    captured: Dict[str, Any] = {}
    errors: list[str] = []
    try:
        svg = _capture_system_svg(system)
        if svg:
            captured["flowsheet_svg"] = svg
    except Exception as exc:
        errors.append(f"flowsheet_svg: {exc}")
    try:
        sfiles = _capture_sfiles_string(system)
        if sfiles:
            captured["sfiles_string"] = sfiles
    except Exception as exc:
        errors.append(f"sfiles_string: {exc}")
    try:
        state_summary = _capture_state_summary(system)
        if state_summary:
            captured["state_summary"] = state_summary
    except Exception as exc:
        errors.append(f"state_summary: {exc}")
    try:
        asm_bio = _extract_asm_bio_from_system(system)
        if asm_bio:
            captured["asm_bio"] = asm_bio
    except Exception as exc:
        errors.append(f"asm_bio: {exc}")
    try:
        predicted_streams = _extract_predicted_streams_from_system(system)
        if predicted_streams:
            captured["predicted_streams"] = predicted_streams
    except Exception as exc:
        errors.append(f"predicted_streams: {exc}")
    if errors:
        captured["diagnostic_capture_errors"] = errors
    return captured


def _extract_simulation_results(
    system: "System",
    model_type: str = "ASM2d",
    effluent_stream_ids: Optional[List[str]] = None,
    biogas_stream_ids: Optional[List[str]] = None,
    include_components: bool = False,
) -> Dict[str, Any]:
    """Extract simulation results from a completed system.

    Args:
        system: Completed QSDsan System
        model_type: Process model type for component interpretation
        effluent_stream_ids: Streams for effluent analysis
        biogas_stream_ids: Streams for biogas analysis
        include_components: Include full component breakdown for each stream
    """
    from qsdsan_engine_mcp.utils.stream_analysis import analyze_aerobic_stream, analyze_liquid_stream, analyze_gas_stream, calculate_removal_efficiency

    results = {
        "system_id": system.ID,
        "simulation_completed": True,
        "units": [u.ID for u in system.units],
        "streams": [s.ID for s in system.streams if s],
    }
    results.update(_capture_in_memory_diagnostics(system))

    def unit_is_anaerobic(unit_obj) -> bool:
        unit_name = type(unit_obj).__name__
        return any(
            token in unit_name
            for token in (
                "Anaerobic",
                "ADM1",
                "AnMBR",
                "InternalCirculationRx",
                "AnaerobicBaffledReactor",
            )
        )

    def stream_from_anaerobic_unit(stream_obj) -> bool:
        for unit in system.units:
            if any(outlet is stream_obj for outlet in getattr(unit, "outs", ())):
                return unit_is_anaerobic(unit)
        return False

    def stream_looks_like_biogas(stream_obj) -> bool:
        if not stream_obj:
            return False
        stream_id = getattr(stream_obj, "ID", "").lower()
        phase = getattr(stream_obj, "phase", None)
        if phase == "g":
            return True
        if any(token in stream_id for token in ("biogas", "off_gas")):
            return True
        if "gas" in stream_id and phase != "l":
            return True
        if phase == "l":
            return False
        components = getattr(stream_obj, "components", None)
        component_ids = set(getattr(components, "IDs", ()) or ())
        gas_component_ids = {
            "S_ch4",
            "S_CH4",
            "S_co2",
            "S_CO2",
            "S_h2",
            "S_H2",
            "S_h2s",
            "S_H2S",
            "S_IS",
            "S_IC",
        }
        return bool(component_ids & gas_component_ids)

    # Extract per-unit analysis (detailed data for every SanUnit)
    try:
        results["unit_analysis"] = _extract_unit_analysis(system, model_type)
    except Exception as e:
        logger.warning(f"Failed to extract per-unit analysis: {e}")
        results["unit_analysis"] = {"error": str(e)}

    # Include full component breakdown if requested
    if include_components:
        components_data = {}
        for stream in system.streams:
            if stream:
                try:
                    # Get component concentrations in mg/L
                    stream_components = {}
                    for comp_id in stream.components.IDs:
                        try:
                            conc = stream.iconc[comp_id]
                            if conc > 0:
                                stream_components[comp_id] = float(conc)
                        except Exception:
                            pass
                    if stream_components:
                        components_data[stream.ID] = stream_components
                except Exception as e:
                    logger.warning(f"Failed to get components for stream {stream.ID}: {e}")
        if components_data:
            results["components"] = components_data

    # Find effluent streams
    if effluent_stream_ids:
        effluent_streams = [s for s in system.streams if s and s.ID in effluent_stream_ids]
    else:
        # Auto-detect effluent using multiple strategies
        # Priority 1: Streams with "effluent" in name
        effluent_streams = [s for s in system.streams if s and "effluent" in s.ID.lower()]

        # Priority 2: First output of clarifier units (outs[0] is effluent)
        if not effluent_streams:
            clarifier_types = ('Clarifier', 'FlatBottomCircularClarifier', 'IdealClarifier', 'PrimaryClarifier')
            for unit in system.units:
                if any(ct in type(unit).__name__ for ct in clarifier_types):
                    if unit.outs and unit.outs[0] and hasattr(unit.outs[0], 'F_vol') and unit.outs[0].F_vol > 0:
                        effluent_streams = [unit.outs[0]]
                        break

        # Priority 3: Terminal streams with lowest TSS (clarified water)
        if not effluent_streams and system.streams:
            exclude_patterns = ('ras', 'ir', 'was', 'recycle', 'return', 'internal')
            terminal_streams = []
            for s in system.streams:
                if not s:
                    continue
                sid_lower = s.ID.lower()
                if any(pat in sid_lower for pat in exclude_patterns):
                    continue
                if hasattr(s, 'sink') and s.sink is not None:
                    continue
                if getattr(s, 'phase', 'l') != 'l':
                    continue
                if hasattr(s, 'F_vol') and s.F_vol > 0:
                    terminal_streams.append(s)

            if terminal_streams:
                # Prefer lowest TSS (clarified) with reasonable flow
                def effluent_score(s):
                    tss = s.get_TSS() if hasattr(s, 'get_TSS') else 1000
                    flow = s.F_vol if hasattr(s, 'F_vol') else 0
                    # Lower TSS is better; reasonable flow (>100 m3/d) is required
                    if flow < 100 / 24:  # < 100 m3/d, unlikely to be main effluent
                        return float('inf')
                    return tss
                effluent_streams = [min(terminal_streams, key=effluent_score)]
            elif system.streams:
                effluent_streams = [system.streams[-1]]

    # Calculate effluent quality - use model-aware analysis function
    if effluent_streams:
        eff = effluent_streams[0]
        try:
            # Normalize model_type for comparison (handle both strings and ModelType enums)
            mt = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
            mt = str(mt).upper()
            if mt in ("MADM1", "ADM1") or stream_from_anaerobic_unit(eff):
                # Anaerobic models use liquid stream analysis with mADM1 components
                results["effluent_quality"] = analyze_liquid_stream(eff)
            elif mt == "ASM1":
                # ASM1 has different components - use basic stream analysis
                from qsdsan_engine_mcp.utils.analysis.common import analyze_stream_basics
                results["effluent_quality"] = analyze_stream_basics(eff)
            else:
                # ASM2d, mASM2d and others use aerobic analysis
                results["effluent_quality"] = analyze_aerobic_stream(eff)
        except Exception as e:
            logger.warning(f"Failed to calculate effluent quality: {e}")
            results["effluent_quality"] = {"error": str(e)}

    # Find influent for removal efficiency
    influent_streams = [s for s in system.streams if s and "influent" in s.ID.lower()]
    if influent_streams and effluent_streams:
        try:
            inf = influent_streams[0]
            eff = effluent_streams[0]
            # Calculate removal efficiency for key parameters
            cod_in = inf.COD if hasattr(inf, 'COD') else 0
            cod_out = eff.COD if hasattr(eff, 'COD') else 0
            tss_in = inf.get_TSS() if hasattr(inf, 'get_TSS') else 0
            tss_out = eff.get_TSS() if hasattr(eff, 'get_TSS') else 0

            results["removal_efficiency"] = {
                "COD_removal_pct": calculate_removal_efficiency(cod_in, cod_out),
                "TSS_removal_pct": calculate_removal_efficiency(tss_in, tss_out),
            }
            # Add TN removal if available
            if hasattr(inf, 'TN') and hasattr(eff, 'TN'):
                tn_in = inf.TN if inf.TN else 0
                tn_out = eff.TN if eff.TN else 0
                results["removal_efficiency"]["TN_removal_pct"] = calculate_removal_efficiency(tn_in, tn_out)
            # Add TP removal if available
            if hasattr(inf, 'TP') and hasattr(eff, 'TP'):
                tp_in = inf.TP if inf.TP else 0
                tp_out = eff.TP if eff.TP else 0
                results["removal_efficiency"]["TP_removal_pct"] = calculate_removal_efficiency(tp_in, tp_out)
        except Exception as e:
            logger.warning(f"Failed to calculate removal efficiency: {e}")

    # Sync each mADM1 digester's vapor-outlet thermo flow from its live headspace
    # gas-slice + gas-transfer rate before reading biogas results. The guarded
    # dynamic path only writes the headspace gas-slice onto the dynamic state, so
    # without this the biogas outlet F_vol reads the stale steady-state 0
    # (roadmap follow-up #20). Defensive: never raises into result extraction.
    try:
        from qsdsan_engine_mcp.models.reactors import sync_madm1_biogas_outlets

        sync_madm1_biogas_outlets(system)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("biogas outlet sync skipped before result extraction: %s", exc)

    # Biogas analysis - auto-detect for anaerobic or mixed-model flowsheets.
    biogas_streams = []
    # Normalize model_type for comparison (handle both strings and ModelType enums)
    mt_biogas = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
    mt_biogas = str(mt_biogas).upper()
    has_anaerobic_units = any(unit_is_anaerobic(unit) for unit in system.units)

    if biogas_stream_ids:
        # Explicit biogas streams provided - always analyze regardless of model_type
        biogas_streams = [s for s in system.streams if s and s.ID in biogas_stream_ids]
    elif mt_biogas in ("MADM1", "ADM1") or has_anaerobic_units:
        for unit in system.units:
            if not unit_is_anaerobic(unit):
                continue
            for candidate in getattr(unit, "outs", ()):
                if candidate and stream_looks_like_biogas(candidate):
                    biogas_streams.append(candidate)
                    break
        if not biogas_streams:
            biogas_streams = [s for s in system.streams if s and stream_looks_like_biogas(s)]

    if biogas_streams:
        try:
            results["biogas"] = analyze_gas_stream(biogas_streams[0])
        except Exception as e:
            logger.warning(f"Failed to calculate biogas composition: {e}")

    # Attach engine-specific summaries on the same dynamic result surface.
    try:
        from qsdsan_engine_mcp.utils.system_summaries import detect_system_engine_families, extract_asm2d_performance, extract_madm1_performance, extract_reactor_geometry

        engine_families = detect_system_engine_families(system)
        summary_payload: Dict[str, Any] = {
            "geometry": extract_reactor_geometry(system),
        }
        influent_id = influent_streams[0].ID if influent_streams else None
        effluent_id = effluent_streams[0].ID if effluent_streams else None
        biogas_id = biogas_streams[0].ID if biogas_streams else None

        if engine_families.get("has_asm2d") and influent_id and effluent_id:
            summary_payload["asm2d"] = extract_asm2d_performance(
                system,
                influent_id=influent_id,
                effluent_id=effluent_id,
            )
        if engine_families.get("has_madm1") and influent_id and effluent_id and biogas_id:
            summary_payload["madm1"] = extract_madm1_performance(
                system,
                influent_id=influent_id,
                effluent_id=effluent_id,
                biogas_id=biogas_id,
            )
        if summary_payload:
            results["per_engine_summaries"] = summary_payload
    except Exception as e:
        logger.warning(f"Failed to attach system summaries: {e}")

    # PR-DD: per-process inhibition & activity matrix.
    # Captured here (not in any template) so dynamically-constructed
    # flowsheets get proposal-grade diagnostics without template-side
    # plumbing. The capture hook auto-dispatches by engine family via
    # model introspection; templates that build their own results dict
    # can call the same hook directly if they want early access.
    try:
        from qsdsan_engine_mcp.utils.analysis.process_diagnostics import capture_system_process_health
        process_health = capture_system_process_health(system)
        if process_health is not None:
            results["process_health"] = process_health
    except Exception as e:
        logger.debug("process_diagnostics capture skipped: %s", e)

    return results


def _export_stream_state(
    system: "System",
    export_path: Path,
    model_type: str,
    effluent_stream_ids: Optional[List[str]] = None,
) -> None:
    """Export final effluent state as StreamState JSON.

    Args:
        system: Completed QSDsan System
        export_path: Path to write JSON file
        model_type: Process model type for component definitions
        effluent_stream_ids: Specific effluent stream IDs to export
    """
    # Find effluent stream
    if effluent_stream_ids:
        effluent_streams = [s for s in system.streams if s and s.ID in effluent_stream_ids]
    else:
        effluent_streams = [s for s in system.streams if s and "effluent" in s.ID.lower()]

    if not effluent_streams:
        raise ValueError("No effluent stream found to export")

    stream = effluent_streams[0]

    stream_state = from_qsdsan_stream(stream, model_type)

    # Write to JSON
    with open(export_path, "w") as f:
        json.dump(stream_state.model_dump(exclude_none=True), f, indent=2)


def _extract_stream_states(system: "System") -> Dict[str, Dict[str, Any]]:
    """Extract final canonical stream-state payloads for downstream handoff.

    QSDsan reports component concentrations through a mg/L API. The public
    export contract is catalog-defined, so mADM1/ADM1 stream artifacts are
    persisted as kg/m3 with values divided by 1000 before serialization.
    """

    result: Dict[str, Dict[str, Any]] = {}
    for stream in system.streams:
        if not stream:
            continue

        try:
            flow_m3_d = float(stream.F_vol * 24.0)
        except Exception:
            flow_m3_d = 0.0

        if flow_m3_d <= 0:
            continue

        model_type = _infer_stream_model_type(stream)
        try:
            state = from_qsdsan_stream(stream, model_type)
            result[stream.ID] = state.model_dump(exclude_none=True)
        except Exception as exc:
            logger.warning("Skipped stream-state export for %s: %s", getattr(stream, "ID", "<unknown>"), exc)

    return result


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'compile_system',
    'simulate_compiled_system',
    'BuildInfo',
]
