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
import json
import logging

from core.plant_state import validate_concentration_bounds
from core.version import __version__ as ENGINE_VERSION

logger = logging.getLogger(__name__)


@dataclass
class BuildInfo:
    """Information about a compiled system."""
    system_id: str
    unit_order: List[str]
    recycle_edges: List[Tuple[str, str]]
    streams_created: List[str]
    units_created: List[str]
    warnings: List[str]


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
    from qsdsan import sanunits, WasteStream, System

    from core.unit_registry import get_unit_spec
    from utils.pipe_parser import parse_port_notation, resolve_port, is_tuple_notation, parse_tuple_notation
    from utils.topo_sort import topological_sort

    warnings = []

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

    build_info = BuildInfo(
        system_id=system_id,
        unit_order=unit_order,
        recycle_edges=recycle_edges,
        streams_created=list(stream_registry.keys()),
        units_created=list(unit_registry.keys()),
        warnings=warnings,
    )

    return system, build_info


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
) -> Dict[str, Any]:
    """
    Simulate a compiled QSDsan System.

    Args:
        system: Compiled QSDsan System
        duration_days: Simulation duration in days
        timestep_hours: Output timestep in hours
        method: ODE solver method (RK23, RK45, BDF)
        t_eval: Custom evaluation times (days). If None, uses timestep.
        state_reset_hook: Method name for state reset
        output_dir: Directory for output files
        model_type: Process model type for result analysis
        effluent_stream_ids: Streams for effluent quality analysis
        biogas_stream_ids: Streams for biogas analysis (mADM1 only)
        include_components: Include full component breakdown in results
        track: Stream IDs to track dynamically during simulation
        export_state_to: Path to export final effluent state as PlantState JSON

    Returns:
        Dict with simulation results including effluent quality, removal efficiency, etc.
    """
    import numpy as np

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
    }

    # Add track streams if provided (for dynamic tracking during simulation)
    if track:
        # Resolve stream IDs to WasteStream objects
        track_streams = []
        for stream_id in track:
            for stream in system.streams:
                if stream and stream.ID == stream_id:
                    track_streams.append(stream)
                    break
        if track_streams:
            sim_kwargs["track"] = track_streams

    # Run dynamic simulation
    system.simulate(**sim_kwargs)

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
            from utils.diagram import save_system_diagram
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

    # Export final state as PlantState JSON if requested
    if export_state_to:
        export_path = Path(export_state_to)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _export_plant_state(system, export_path, model_type, effluent_stream_ids)
            results["exported_state_path"] = str(export_path)
        except Exception as e:
            logger.warning(f"Failed to export plant state: {e}")

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
        "engine_version": ENGINE_VERSION,
        "solver": {
            "method": method,
            "duration_days": duration_days,
            "timestep_hours": timestep_hours,
            "rtol": 1e-3,
            "atol": 1e-6,
        },
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "model_type": model_type,
    }

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
    from qsdsan import processes as pc

    if model_type == "mADM1":
        # Custom 63-component mADM1 with H2S modeling and sulfide precipitation
        from models.madm1 import create_madm1_cmps
        cmps = create_madm1_cmps()
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
        # Modified ASM2d with minerals and ions
        cmps = pc.create_masm2d_cmps()
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
    from qsdsan import WasteStream

    # Get concentration units (default mg/L for backward compatibility)
    conc_units = getattr(config, 'concentration_units', 'mg/L')

    # Validate concentration bounds for likely unit confusion
    bound_warnings = validate_concentration_bounds(
        config.concentrations, model_type, conc_units
    )
    for warning in bound_warnings:
        logger.warning(f"Stream '{stream_id}': {warning}")

    # Build concentration dict (only valid components)
    concentrations = {}
    valid_ids = set(cmps.IDs)

    for comp_id, conc in config.concentrations.items():
        if comp_id in valid_ids:
            # Convert kg/m3 to mg/L if needed (1 kg/m3 = 1000 mg/L)
            if conc_units == "kg/m3":
                concentrations[comp_id] = conc * 1000  # Convert to mg/L
            else:
                concentrations[comp_id] = conc  # Already in mg/L
        else:
            logger.warning(f"Stream '{stream_id}': component '{comp_id}' not in model, skipping")

    # Create stream with temperature
    stream = WasteStream(
        ID=stream_id,
        T=config.temperature_K,
    )

    # Set flow and concentrations using proper QSDsan method
    # Note: concentrations are now always in mg/L (converted above if needed)
    if config.flow_m3_d > 0 and concentrations:
        # Use set_flow_by_concentration for correct unit handling
        try:
            stream.set_flow_by_concentration(
                flow_tot=config.flow_m3_d,
                concentrations=concentrations,
                units=('m3/d', 'mg/L'),
            )
        except Exception as e:
            # Fallback to manual calculation if set_flow_by_concentration fails
            logger.warning(f"Stream '{stream_id}': set_flow_by_concentration failed ({e}), using fallback")
            stream.set_total_flow(config.flow_m3_d, "m3/d")
            # Convert mg/L to kg/hr: (mg/L * m³/d) / (1000 mg/g * 1000 g/kg) / 24 hr/d
            for comp_id, conc in concentrations.items():
                if conc > 0:
                    # mg/L * m³/d = g/d -> /1000 = kg/d -> /24 = kg/hr
                    stream.imass[comp_id] = (conc * config.flow_m3_d) / 1000 / 24  # kg/hr
    elif config.flow_m3_d > 0:
        stream.set_total_flow(config.flow_m3_d, "m3/d")

    return stream


def _resolve_single_input(
    input_ref: str,
    stream_registry: Dict[str, "WasteStream"],
    unit_registry: Dict[str, "SanUnit"],
) -> Optional[Any]:
    """Resolve a single input reference to a stream/port or None.

    Args:
        input_ref: Port notation string (not tuple notation)
        stream_registry: Dict of stream_id -> WasteStream
        unit_registry: Dict of unit_id -> SanUnit

    Returns:
        WasteStream, output port, or None if not found
    """
    from utils.pipe_parser import parse_port_notation

    ref = parse_port_notation(input_ref)

    if ref.port_type == "stream":
        # Direct stream reference
        if ref.unit_id in stream_registry:
            return stream_registry[ref.unit_id]
        else:
            # Create empty stream as placeholder
            return None
    elif ref.port_type == "output":
        # Output port of another unit (e.g., "A1-0")
        if ref.unit_id in unit_registry:
            src_unit = unit_registry[ref.unit_id]
            if ref.index < len(src_unit.outs):
                return src_unit.outs[ref.index]
        return None
    elif ref.port_type == "direct":
        # Direct unit-to-unit connection (e.g., "U1-U2" or "U1-0-1-U2")
        # For inputs, we only care about the source unit's output
        if ref.unit_id in unit_registry:
            src_unit = unit_registry[ref.unit_id]
            if ref.index < len(src_unit.outs):
                return src_unit.outs[ref.index]
        return None
    elif ref.port_type == "input":
        # Input port reference (e.g., "1-M1") - shouldn't be used as input source
        # This is unusual - typically inputs reference outputs or streams
        return None
    else:
        return None


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
    from qsdsan import sanunits
    from core.unit_registry import get_unit_spec
    from utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation

    spec = get_unit_spec(config.unit_type)

    # Resolve input streams/ports
    # Supports tuple fan-in notation like "(A1-0, B1-0)" for Mixer inputs
    ins = []
    for input_ref in config.inputs:
        # Check for tuple notation (fan-in)
        if is_tuple_notation(input_ref):
            # Parse tuple and resolve each port
            port_strs = parse_tuple_notation(input_ref)
            for port_str in port_strs:
                resolved = _resolve_single_input(port_str, stream_registry, unit_registry)
                ins.append(resolved)
        else:
            resolved = _resolve_single_input(input_ref, stream_registry, unit_registry)
            ins.append(resolved)

    # Get the QSDsan class
    unit_class = _get_unit_class(spec.qsdsan_class)

    # Build kwargs
    kwargs = {"ID": unit_id}
    if ins:
        kwargs["ins"] = ins if len(ins) > 1 else ins[0]

    # Add unit-specific parameters
    for param, value in config.params.items():
        if value is not None:
            kwargs[param] = value

    # Auto-instantiate process models for reactors
    # Aerobic reactors (CSTR, MBR) need suspended_growth_model
    if config.unit_type in ("CSTR", "CompletelyMixedMBR"):
        from qsdsan import processes as pc
        if model_type == "ASM2d":
            kwargs.setdefault("suspended_growth_model", pc.ASM2d())
        elif model_type == "mASM2d":
            kwargs.setdefault("suspended_growth_model", pc.mASM2d())
        elif model_type == "ASM1":
            kwargs.setdefault("suspended_growth_model", pc.ASM1())
        else:
            logger.warning(f"Unit '{unit_id}': No suspended_growth_model for model_type '{model_type}'")

    # Anaerobic reactors need anaerobic_digestion_model
    elif config.unit_type == "AnaerobicCSTR":
        from qsdsan import processes as pc
        if model_type == "ADM1":
            kwargs.setdefault("model", pc.ADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnaerobicCSTR typically uses ADM1, got '{model_type}'")

    elif config.unit_type == "AnaerobicCSTRmADM1":
        # Custom mADM1 reactor uses ModifiedADM1 process model
        if model_type == "mADM1":
            from models.madm1 import ModifiedADM1
            kwargs.setdefault("model", ModifiedADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnaerobicCSTRmADM1 requires mADM1, got '{model_type}'")

    # AnMBR (anaerobic MBR) also needs anaerobic model
    elif config.unit_type == "AnMBR":
        from qsdsan import processes as pc
        if model_type == "ADM1":
            kwargs.setdefault("model", pc.ADM1())
        elif model_type == "mADM1":
            from models.madm1 import ModifiedADM1
            kwargs.setdefault("model", ModifiedADM1())
        else:
            logger.warning(f"Unit '{unit_id}': AnMBR typically uses ADM1/mADM1, got '{model_type}'")

    # Junction units - use custom classes for mADM1, standard QSDsan for ADM1 (Phase 2B Fix 2)
    elif config.unit_type == "ASM2dtomADM1":
        # Custom junction for mADM1 (63 components) that accepts CompiledProcesses
        from core.junction_units import ASM2dtomADM1_custom
        from qsdsan import processes as pc
        from models.madm1 import ModifiedADM1

        unit = ASM2dtomADM1_custom(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            auto_align_components=True,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.asm2d_model = pc.ASM2d()
        unit.adm1_model = ModifiedADM1()  # Custom 63-component model

        prep_status = unit.prepare_for_simulation()
        logger.debug(f"Junction {unit_id} prep status: {prep_status}")
        return unit

    elif config.unit_type == "ASM2dtoADM1":
        # Standard QSDsan junction for ADM1 (35 components)
        from qsdsan import sanunits, processes as pc

        unit = sanunits.ASM2dtoADM1(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.asm2d_model = pc.ASM2d()
        unit.adm1_model = pc.ADM1()  # Standard 35-component model
        return unit

    elif config.unit_type == "mADM1toASM2d":
        # Custom junction for mADM1 (63 components) that accepts CompiledProcesses
        from core.junction_units import mADM1toASM2d_custom
        from qsdsan import processes as pc
        from models.madm1 import ModifiedADM1

        unit = mADM1toASM2d_custom(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            auto_align_components=True,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.adm1_model = ModifiedADM1()  # Custom 63-component model
        unit.asm2d_model = pc.ASM2d()

        prep_status = unit.prepare_for_simulation()
        logger.debug(f"Junction {unit_id} prep status: {prep_status}")
        return unit

    elif config.unit_type == "ADM1toASM2d":
        # Standard QSDsan junction for ADM1 (35 components)
        from qsdsan import sanunits, processes as pc

        unit = sanunits.ADM1toASM2d(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.adm1_model = pc.ADM1()  # Standard 35-component model
        unit.asm2d_model = pc.ASM2d()
        return unit

    # Additional junction types with model injection (Phase 2B Fix 3)
    elif config.unit_type == "ASMtoADM":
        # Generic ASM to ADM junction - use model-aware ASM selection
        from qsdsan import sanunits, processes as pc

        unit = sanunits.ASMtoADM(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        # Select ASM model based on model_type
        if model_type == "ASM1":
            unit.asm_model = pc.ASM1()
        else:
            unit.asm_model = pc.ASM2d()  # Default for ASM2d, mASM2d
        unit.adm_model = pc.ADM1()
        return unit

    elif config.unit_type == "ADMtoASM":
        # Generic ADM to ASM junction - use model-aware ASM selection
        from qsdsan import sanunits, processes as pc

        unit = sanunits.ADMtoASM(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.adm_model = pc.ADM1()
        # Select ASM model based on model_type
        if model_type == "ASM1":
            unit.asm_model = pc.ASM1()
        else:
            unit.asm_model = pc.ASM2d()  # Default for ASM2d, mASM2d
        return unit

    elif config.unit_type == "ADM1ptomASM2d":
        # ADM1 with P extension to modified ASM2d junction
        from qsdsan import sanunits, processes as pc

        unit = sanunits.ADM1ptomASM2d(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.adm1_model = pc.ADM1_p_extension()
        unit.asm2d_model = pc.mASM2d()
        return unit

    elif config.unit_type == "mASM2dtoADM1p":
        # Modified ASM2d to ADM1 with P extension junction
        from qsdsan import sanunits, processes as pc

        unit = sanunits.mASM2dtoADM1p(
            ID=unit_id,
            upstream=ins[0] if ins else None,
            **{k: v for k, v in config.params.items() if v is not None}
        )
        unit.asm2d_model = pc.mASM2d()
        unit.adm1_model = pc.ADM1_p_extension()
        return unit

    # SBR also needs suspended_growth_model
    elif config.unit_type == "SBR":
        from qsdsan import processes as pc
        if model_type == "ASM2d":
            kwargs.setdefault("suspended_growth_model", pc.ASM2d())
        elif model_type == "ASM1":
            kwargs.setdefault("suspended_growth_model", pc.ASM1())

    # Create unit
    unit = unit_class(**kwargs)

    # Handle named outputs - assign IDs to output streams and register them
    if config.outputs:
        for i, out_name in enumerate(config.outputs):
            if i < len(unit.outs) and out_name:
                # Set the stream ID
                if unit.outs[i]:
                    unit.outs[i].ID = out_name
                    # Register in stream_registry for downstream reference
                    stream_registry[out_name] = unit.outs[i]

    return unit


def _get_unit_class(qsdsan_class: str):
    """Import and return a unit class from its path."""
    from qsdsan import sanunits

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
    """
    from qsdsan import WasteStream
    from utils.pipe_parser import parse_port_notation

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
        if dst_in_idx >= len(dst_unit.ins):
            raise ValueError(f"Unit '{from_ref.target_unit_id}' has no input port {dst_in_idx}")

        # Wire the connection
        dst_unit.ins[dst_in_idx] = src_port

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
        # Destination is a unit ID (e.g., "B1") - use input port 0
        if to_ref.unit_id not in unit_registry:
            raise ValueError(f"Destination unit '{to_ref.unit_id}' not found")
        dst_unit = unit_registry[to_ref.unit_id]
        dst_in_idx = 0
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

    if dst_in_idx >= len(dst_unit.ins):
        raise ValueError(f"Unit '{to_ref.unit_id}' has no input port {dst_in_idx}")

    # Wire the connection
    dst_unit.ins[dst_in_idx] = src_port

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
    }

    try:
        # Find tracked streams
        tracked_streams = []
        for stream_id in track:
            for stream in system.streams:
                if stream and stream.ID == stream_id:
                    tracked_streams.append(stream)
                    break

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
                                    # Only include non-zero components
                                    if np.any(values > 0):
                                        if isinstance(values, np.ndarray):
                                            stream_data[comp_id] = values.tolist()
                                        else:
                                            stream_data[comp_id] = list(values)
            except Exception as e:
                logger.warning(f"Error extracting time-series for {stream.ID}: {e}")

            if stream_data:
                result["streams"][stream.ID] = stream_data

        result["success"] = len(result["streams"]) > 0
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
    from utils.stream_analysis import (
        analyze_aerobic_stream,
        analyze_liquid_stream,
        calculate_removal_efficiency,
    )

    units_data = {}

    # Normalize model_type
    mt = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
    mt = str(mt).upper()
    is_anaerobic = mt in ("MADM1", "ADM1")

    for unit in system.units:
        try:
            unit_id = unit.ID
            unit_type = type(unit).__name__

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
                params['split_ratio'] = float(unit.split) if unit.split else None

            unit_data['parameters'] = params

            # Analyze inlet streams
            inlet_analysis = []
            for inlet in unit.ins:
                if inlet:
                    try:
                        if is_anaerobic:
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
                        if is_gas and is_anaerobic:
                            from utils.stream_analysis import analyze_gas_stream
                            analysis = analyze_gas_stream(outlet)
                            outlet_analysis.append({
                                "stream_id": outlet.ID,
                                "stream_type": "gas",
                                "flow_m3_d": float(outlet.F_vol * 24) if hasattr(outlet, 'F_vol') else None,
                                **{k: v for k, v in analysis.items() if k not in ['success', 'stream_id']}
                            })
                        else:
                            if is_anaerobic:
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
    from utils.stream_analysis import (
        analyze_aerobic_stream,
        analyze_liquid_stream,
        analyze_gas_stream,
        calculate_removal_efficiency,
    )

    results = {
        "system_id": system.ID,
        "simulation_completed": True,
        "units": [u.ID for u in system.units],
        "streams": [s.ID for s in system.streams if s],
    }

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
        # Auto-detect effluent (streams with "effluent" in name or last stream)
        effluent_streams = [s for s in system.streams if s and "effluent" in s.ID.lower()]
        if not effluent_streams and system.streams:
            effluent_streams = [system.streams[-1]]

    # Calculate effluent quality - use model-aware analysis function
    if effluent_streams:
        eff = effluent_streams[0]
        try:
            # Normalize model_type for comparison (handle both strings and ModelType enums)
            mt = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
            mt = str(mt).upper()
            if mt in ("MADM1", "ADM1"):
                # Anaerobic models use liquid stream analysis with mADM1 components
                results["effluent_quality"] = analyze_liquid_stream(eff)
            elif mt == "ASM1":
                # ASM1 has different components - use basic stream analysis
                from utils.analysis.common import analyze_stream_basics
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

    # Biogas analysis - analyze if biogas_stream_ids explicitly provided OR model is anaerobic
    # This allows mixed-model flowsheets (ASM2d + mADM1) to get biogas analysis
    # when the user explicitly specifies biogas streams
    biogas_streams = []
    # Normalize model_type for comparison (handle both strings and ModelType enums)
    mt_biogas = getattr(model_type, 'value', model_type) if model_type else "ASM2d"
    mt_biogas = str(mt_biogas).upper()

    if biogas_stream_ids:
        # Explicit biogas streams provided - always analyze regardless of model_type
        biogas_streams = [s for s in system.streams if s and s.ID in biogas_stream_ids]
    elif mt_biogas in ("MADM1", "ADM1"):
        # Auto-detect biogas streams for anaerobic models
        # First check for streams with "biogas" or "gas" in name
        biogas_streams = [s for s in system.streams if s and (
            "biogas" in s.ID.lower() or "gas" in s.ID.lower()
        )]
        # If not found, check for gas-phase streams from digester second outputs
        if not biogas_streams:
            for unit in system.units:
                # Check if unit is an anaerobic digester with 2 outputs
                unit_type = type(unit).__name__
                if any(x in unit_type for x in ["Anaerobic", "ADM1", "Digester"]):
                    if len(unit.outs) >= 2 and unit.outs[1]:
                        # Second output is typically biogas
                        gas_stream = unit.outs[1]
                        # Verify it has gas-phase components (CH4, CO2, H2, H2S, IC)
                        try:
                            cmps = gas_stream.components
                            # Check for common biogas components in various naming conventions
                            gas_comp_ids = [
                                'S_ch4', 'S_CH4', 'S_co2', 'S_CO2',  # Methane, CO2
                                'S_h2s', 'S_H2S', 'S_IS',            # Sulfide
                                'S_h2', 'S_H2', 'S_IC',              # Hydrogen, inorganic carbon
                            ]
                            has_gas = any(hasattr(cmps, c) for c in gas_comp_ids)
                            if has_gas:
                                biogas_streams.append(gas_stream)
                        except Exception:
                            pass

    if biogas_streams:
        try:
            results["biogas"] = analyze_gas_stream(biogas_streams[0])
        except Exception as e:
            logger.warning(f"Failed to calculate biogas composition: {e}")

    return results


def _export_plant_state(
    system: "System",
    export_path: Path,
    model_type: str,
    effluent_stream_ids: Optional[List[str]] = None,
) -> None:
    """Export final effluent state as PlantState JSON.

    Args:
        system: Completed QSDsan System
        export_path: Path to write JSON file
        model_type: Process model type for component definitions
        effluent_stream_ids: Specific effluent stream IDs to export
    """
    from core.plant_state import PlantState

    # Find effluent stream
    if effluent_stream_ids:
        effluent_streams = [s for s in system.streams if s and s.ID in effluent_stream_ids]
    else:
        effluent_streams = [s for s in system.streams if s and "effluent" in s.ID.lower()]

    if not effluent_streams:
        raise ValueError("No effluent stream found to export")

    stream = effluent_streams[0]

    # Build concentrations dict
    concentrations = {}
    for comp_id in stream.components.IDs:
        try:
            conc = stream.iconc[comp_id]
            if conc > 0:
                concentrations[comp_id] = float(conc)
        except Exception:
            pass

    # Get flow in m³/d
    try:
        flow_m3_d = float(stream.F_vol * 24)  # m³/hr -> m³/d
    except Exception:
        flow_m3_d = 0.0

    # Create PlantState
    plant_state = PlantState(
        model_type=model_type,
        flow_m3_d=flow_m3_d,
        temperature_K=float(stream.T) if hasattr(stream, 'T') else 293.15,
        concentrations=concentrations,
    )

    # Write to JSON
    with open(export_path, "w") as f:
        json.dump(plant_state.__dict__, f, indent=2)


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'compile_system',
    'simulate_compiled_system',
    'BuildInfo',
]
