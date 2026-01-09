"""
QSDsan Engine MCP Server - Universal Biological Wastewater Treatment Simulation

This is the MCP adapter for the QSDsan simulation engine. It provides 18 tools
for stateless simulation with explicit state passing.

Core Tools (Phase 1):
    - simulate_system: Run QSDsan simulation to steady state (background job)
    - get_job_status: Check job progress
    - get_job_results: Retrieve simulation results
    - list_templates: List available flowsheet templates
    - validate_state: Validate PlantState against model
    - convert_state: ASM2d <-> mADM1 state conversion (background job)

Utility Tools:
    - list_jobs: List all background jobs
    - terminate_job: Terminate a running job
    - get_timeseries_data: Retrieve time series from completed simulation

Flowsheet Construction Tools (Phase 2):
    - create_flowsheet_session: Create a new flowsheet construction session
    - create_stream: Create a WasteStream in the session
    - create_unit: Create a SanUnit in the session
    - connect_units: Add deferred connections (for recycles)
    - build_system: Compile flowsheet into QSDsan System
    - list_units: List available SanUnit types
    - simulate_built_system: Simulate compiled flowsheet with reporting

Session Management Tools:
    - get_flowsheet_session: Get details of an existing session
    - list_flowsheet_sessions: List all flowsheet sessions

Architecture:
    This server exposes the same engine core as the CLI adapter (cli.py).
    Both adapters use shared modules from core/ (model_registry, template_registry, plant_state).
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Literal, List

from mcp.server.fastmcp import FastMCP

from core.plant_state import PlantState, ModelType, ValidationResult
from core.model_registry import (
    get_model_info,
    get_required_components,
    validate_components,
    list_available_models,
    MODEL_REGISTRY,
)
from core.template_registry import list_templates as get_all_templates
from core.unit_registry import (
    list_available_units as get_all_units,
    get_unit_spec,
    validate_unit_params,
    validate_model_compatibility,
    get_units_by_category,
)
from utils.job_manager import JobManager
from utils.path_utils import normalize_path_for_wsl, get_python_executable
from utils.flowsheet_session import (
    FlowsheetSessionManager,
    StreamConfig,
    UnitConfig,
    ConnectionConfig,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("qsdsan-engine")

# Initialize job manager (singleton)
job_manager = JobManager(max_concurrent_jobs=3, jobs_base_dir="jobs")

# Initialize flowsheet session manager (singleton)
session_manager = FlowsheetSessionManager(sessions_dir=Path("jobs"))


# =============================================================================
# Tool 1: simulate_system (Background Job)
# =============================================================================
@mcp.tool()
async def simulate_system(
    template: str,
    influent: Dict[str, Any],
    duration_days: float = 1.0,
    timestep_hours: float = 1.0,
    reactor_config: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run QSDsan dynamic simulation using a flowsheet template.

    This tool runs as a background job and returns immediately with a job_id.
    Use get_job_status() to check progress and get_job_results() to retrieve results.

    Args:
        template: Flowsheet template name (e.g., "anaerobic_cstr_madm1", "mle_mbr_asm2d")
        influent: PlantState dict for influent with keys: model_type, flow_m3_d,
                  temperature_K, concentrations
        duration_days: Simulation duration in days (default 1.0)
        timestep_hours: Output timestep in hours (default 1.0)
        reactor_config: Optional reactor configuration overrides
        parameters: Optional kinetic parameter overrides

    Returns:
        Dict with job_id, status, and instructions for monitoring

    Example:
        >>> result = await simulate_system(
        ...     template="anaerobic_cstr_madm1",
        ...     influent={"model_type": "mADM1", "flow_m3_d": 1000, "concentrations": {...}},
        ...     duration_days=30.0
        ... )
        >>> job_id = result["job_id"]
        >>> # Then call get_job_status(job_id) and get_job_results(job_id)
    """
    try:
        # Use influent directly (native dict)
        influent_state = PlantState.from_dict(influent)

        # Use configs directly (native dicts)
        reactor_cfg = reactor_config or {}
        params = parameters or {}

        # Create job directory
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = Path("jobs") / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Save influent state to job directory
        influent_state.save(str(job_dir / "influent.json"))

        # Save config
        config = {
            "template": template,
            "duration_days": duration_days,
            "timestep_hours": timestep_hours,
            "reactor_config": reactor_cfg,
            "parameters": params,
        }
        with open(job_dir / "config.json", "w") as f:
            json.dump(config, f, indent=2)

        # Build CLI command
        python_exe = get_python_executable()
        cmd = [
            python_exe,
            "cli.py",
            "simulate",
            "--template", template,
            "--influent", str(job_dir / "influent.json"),
            "--output-dir", str(job_dir),
            "--duration-days", str(duration_days),
            "--timestep-hours", str(timestep_hours),
        ]

        if reactor_config:
            cmd.extend(["--reactor-config", json.dumps(reactor_config)])
        if parameters:
            cmd.extend(["--parameters", json.dumps(parameters)])

        # Execute as background job
        cwd = str(Path(__file__).parent.absolute())
        job = await job_manager.execute(cmd=cmd, cwd=cwd, job_id=job_id)

        return {
            "job_id": job["id"],
            "status": job["status"],
            "template": template,
            "duration_days": duration_days,
            "message": f"Simulation started. Use get_job_status('{job['id']}') to monitor progress.",
        }
    except Exception as e:
        logger.error(f"simulate_system failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Tool 2: get_job_status
# =============================================================================
@mcp.tool()
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Check status of a background simulation job.

    Args:
        job_id: Job identifier from simulate_system or convert_state

    Returns:
        Dict with job_id, status, elapsed_time_seconds, progress hints
    """
    return await job_manager.get_status(job_id)


# =============================================================================
# Tool 3: get_job_results
# =============================================================================
@mcp.tool()
async def get_job_results(job_id: str) -> Dict[str, Any]:
    """
    Retrieve results from a completed simulation job.

    Time series data is excluded by default to avoid token limits.
    Use get_timeseries_data(job_id) if you need the full time series.

    Args:
        job_id: Job identifier

    Returns:
        Dict with job_id, status, results (parsed JSON), and log file paths
    """
    return await job_manager.get_results(job_id)


# =============================================================================
# Tool 4: list_templates
# =============================================================================
@mcp.tool()
async def list_templates() -> Dict[str, Any]:
    """
    List available flowsheet templates for simulation.

    Returns:
        Dict with anaerobic and aerobic template lists, plus supported models
    """
    templates = get_all_templates()
    templates["models"] = list_available_models()
    return templates


# =============================================================================
# Tool 5: validate_state
# =============================================================================
@mcp.tool()
async def validate_state(
    state: Dict[str, Any],
    model_type: str,
    check_charge_balance: bool = True,
    check_mass_balance: bool = True,
) -> Dict[str, Any]:
    """
    Validate PlantState against model requirements.

    Checks:
    - Required components present
    - Charge balance (electroneutrality)
    - Mass balance (COD, TSS, TKN, TP)
    - Concentration bounds

    Args:
        state: PlantState dict with keys: model_type, flow_m3_d, temperature_K, concentrations
        model_type: Target model type ("mADM1", "ASM2d", etc.)
        check_charge_balance: Whether to check electroneutrality
        check_mass_balance: Whether to check mass balance closure

    Returns:
        ValidationResult as dict
    """
    try:
        # Use state directly (native dict)
        plant_state = PlantState.from_dict(state)

        # Get model info
        mt = ModelType(model_type)
        model_info = get_model_info(mt)

        errors = []
        warnings = []

        # Check components
        provided = set(plant_state.concentrations.keys())
        missing, extra = validate_components(mt, provided)

        if missing:
            errors.append(f"Missing required components: {missing[:10]}{'...' if len(missing) > 10 else ''}")
        if extra:
            warnings.append(f"Extra components (will be ignored): {extra[:5]}{'...' if len(extra) > 5 else ''}")

        # Basic validation
        if plant_state.flow_m3_d <= 0:
            errors.append(f"flow_m3_d must be positive, got {plant_state.flow_m3_d}")

        if plant_state.temperature_K < 273.15 or plant_state.temperature_K > 373.15:
            warnings.append(f"Temperature {plant_state.temperature_K} K outside typical range (273-373 K)")

        # Check for negative concentrations
        negative = [k for k, v in plant_state.concentrations.items() if v < 0]
        if negative:
            errors.append(f"Negative concentrations: {negative}")

        # Charge balance check (simplified - full implementation in engine)
        charge_balance = None
        if check_charge_balance and mt == ModelType.MADM1:
            # Simplified check - actual implementation uses full speciation
            s_cat = plant_state.concentrations.get('S_Na', 0) + plant_state.concentrations.get('S_K', 0)
            s_an = plant_state.concentrations.get('S_Cl', 0)
            if s_cat == 0 and s_an == 0:
                warnings.append("Charge balance species (S_Na, S_Cl, S_K) not set")
            charge_balance = {"S_cat_approx": s_cat, "S_an_approx": s_an}

        result = ValidationResult(
            is_valid=len(errors) == 0,
            model_type=mt,
            errors=errors,
            warnings=warnings,
            charge_balance=charge_balance,
            missing_components=missing,
            extra_components=extra,
        )

        return result.to_dict()

    except Exception as e:
        logger.error(f"validate_state failed: {e}", exc_info=True)
        return {"error": str(e), "is_valid": False}


# =============================================================================
# Tool 6: convert_state (Background Job)
# =============================================================================
@mcp.tool()
async def convert_state(
    state: Dict[str, Any],
    from_model: str,
    to_model: str,
) -> Dict[str, Any]:
    """
    Convert PlantState between model types using QSDsan Junction units.

    This tool runs as a background job for complex conversions.
    Supports ASM2d <-> mADM1 conversions for integrated plant simulation.

    Args:
        state: PlantState dict with keys: model_type, flow_m3_d, temperature_K, concentrations
        from_model: Source model type ("ASM2d", "mADM1", etc.)
        to_model: Target model type

    Returns:
        Dict with job_id for tracking, or direct result for simple conversions

    Example:
        # Convert WAS from activated sludge to anaerobic digester
        >>> result = await convert_state(
        ...     state={"model_type": "ASM2d", "flow_m3_d": 100, "concentrations": {...}},
        ...     from_model="ASM2d",
        ...     to_model="mADM1"
        ... )
    """
    try:
        # Use state directly (native dict)
        state_data = state

        # Validate model types
        from_mt = ModelType(from_model)
        to_mt = ModelType(to_model)

        if from_mt == to_mt:
            return {
                "status": "no_conversion_needed",
                "message": f"Source and target model are both {from_model}",
                "state": state,
            }

        # Check for supported conversions
        supported_conversions = [
            (ModelType.ASM2D, ModelType.MADM1),
            (ModelType.MADM1, ModelType.ASM2D),
            (ModelType.MASM2D, ModelType.MADM1),
            (ModelType.MADM1, ModelType.MASM2D),
        ]

        if (from_mt, to_mt) not in supported_conversions:
            return {
                "error": f"Conversion from {from_model} to {to_model} not supported",
                "supported": [f"{f.value} -> {t.value}" for f, t in supported_conversions],
            }

        # Create job directory
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = Path("jobs") / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Save input state
        with open(job_dir / "input_state.json", "w") as f:
            json.dump(state_data, f, indent=2)

        # Build CLI command
        python_exe = get_python_executable()
        cmd = [
            python_exe,
            "cli.py",
            "convert",
            "--input", str(job_dir / "input_state.json"),
            "--output", str(job_dir / "output_state.json"),
            "--from-model", from_model,
            "--to-model", to_model,
        ]

        # Execute as background job
        cwd = str(Path(__file__).parent.absolute())
        job = await job_manager.execute(cmd=cmd, cwd=cwd, job_id=job_id)

        return {
            "job_id": job["id"],
            "status": job["status"],
            "conversion": f"{from_model} -> {to_model}",
            "message": f"Conversion started. Use get_job_status('{job['id']}') to monitor.",
        }

    except Exception as e:
        logger.error(f"convert_state failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Additional utility tools
# =============================================================================
@mcp.tool()
async def list_jobs(
    status_filter: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    List all background jobs with optional status filter.

    Args:
        status_filter: Filter by status ("running", "completed", "failed", or None)
        limit: Maximum number of jobs to return

    Returns:
        Dict with jobs list and summary
    """
    return await job_manager.list_jobs(status_filter=status_filter, limit=limit)


@mcp.tool()
async def terminate_job(job_id: str) -> Dict[str, Any]:
    """
    Terminate a running background job.

    Args:
        job_id: Job identifier to terminate

    Returns:
        Dict with termination status
    """
    return await job_manager.terminate_job(job_id)


@mcp.tool()
async def get_timeseries_data(job_id: str) -> Dict[str, Any]:
    """
    Get time series data from a completed simulation job.

    Time series data is excluded from get_job_results() to avoid token limits.
    Use this tool only when you specifically need the time series for plotting.

    Args:
        job_id: Job identifier

    Returns:
        Dict with time series data or error
    """
    return await job_manager.get_timeseries_data(job_id)


# =============================================================================
# Phase 2: Flowsheet Construction Tools
# =============================================================================

@mcp.tool()
async def create_flowsheet_session(
    model_type: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new flowsheet construction session.

    Sessions persist to disk in jobs/flowsheets/{session_id}/ and survive
    MCP reconnections. Use this to start building a custom flowsheet.

    Args:
        model_type: Primary process model (e.g., "ASM2d", "mADM1")
        session_id: Optional custom session ID. Auto-generates if not provided.

    Returns:
        Dict with session_id, model_type, and available units for that model

    Example:
        >>> result = await create_flowsheet_session(model_type="ASM2d")
        >>> session_id = result["session_id"]
        >>> # Now use create_stream(), create_unit(), etc.
    """
    try:
        session = session_manager.create_session(
            model_type=model_type,
            session_id=session_id,
        )

        # Get units compatible with this model
        compatible_units = get_all_units(model_type=model_type)

        return {
            "session_id": session.session_id,
            "model_type": model_type,
            "status": "created",
            "compatible_units": [u["unit_type"] for u in compatible_units],
            "message": f"Session created. Add streams with create_stream('{session.session_id}', ...)",
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"create_flowsheet_session failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def create_stream(
    session_id: str,
    stream_id: str,
    flow_m3_d: float,
    concentrations: Dict[str, float],
    temperature_K: float = 293.15,
    concentration_units: str = "mg/L",
    stream_type: str = "influent",
    model_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a WasteStream in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        stream_id: Unique stream identifier (e.g., "influent", "RAS")
        flow_m3_d: Flow rate in m3/day
        concentrations: Dict of component ID -> concentration value
        temperature_K: Temperature in Kelvin (default 293.15 = 20C)
        concentration_units: Units for concentration values: "mg/L" (default) or "kg/m3"
        stream_type: One of "influent", "recycle", "intermediate"
        model_type: Process model override (defaults to session's primary model)

    Returns:
        Dict with stream_id and validation status

    Example:
        >>> await create_stream(
        ...     session_id="abc123",
        ...     stream_id="influent",
        ...     flow_m3_d=4000,
        ...     concentrations={"S_F": 75, "S_A": 20, "S_NH4": 17},
        ...     concentration_units="mg/L",
        ... )
    """
    try:
        # Validate concentration_units
        if concentration_units not in ("mg/L", "kg/m3"):
            return {"error": f"Invalid concentration_units '{concentration_units}'. Must be 'mg/L' or 'kg/m3'."}

        warnings = []

        # Validate component IDs against model
        session = session_manager.get_session(session_id)
        effective_model = model_type or session.primary_model_type
        try:
            mt = ModelType(effective_model)
            missing, extra = validate_components(mt, set(concentrations.keys()))
            if extra:
                extra_list = extra[:5]
                suffix = f"... and {len(extra) - 5} more" if len(extra) > 5 else ""
                warnings.append(f"Unknown component IDs (ignored by QSDsan): {extra_list}{suffix}")
        except (ValueError, KeyError):
            # Model not in registry - skip component validation
            pass

        config = StreamConfig(
            stream_id=stream_id,
            flow_m3_d=flow_m3_d,
            temperature_K=temperature_K,
            concentrations=concentrations,
            concentration_units=concentration_units,
            stream_type=stream_type,
            model_type=model_type,
        )

        result = session_manager.add_stream(session_id, config)

        # Add warnings to result
        if warnings:
            result["warnings"] = warnings

        return result

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"create_stream failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def create_unit(
    session_id: str,
    unit_type: str,
    unit_id: str,
    params: Dict[str, Any],
    inputs: List[str],
    outputs: Optional[List[str]] = None,
    model_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a SanUnit in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        unit_type: Unit type from registry (e.g., "CSTR", "Splitter", "CompletelyMixedMBR")
        unit_id: Unique unit identifier (e.g., "A1", "O1", "MBR")
        params: Dict of unit-specific parameters
        inputs: List of input sources (stream IDs or pipe notation like "A1-0")
        outputs: Optional list of output stream names
        model_type: Process model override (defaults to session's primary model)

    Returns:
        Dict with unit_id, validation status, and port info

    Example:
        >>> await create_unit(
        ...     session_id="abc123",
        ...     unit_type="CSTR",
        ...     unit_id="A1",
        ...     params={"V_max": 1000, "aeration": None},
        ...     inputs=["influent", "RAS"],
        ... )
    """
    try:
        # Validate unit type exists
        try:
            spec = get_unit_spec(unit_type)
        except ValueError as e:
            return {"error": str(e)}

        # Validate parameters
        param_errors, param_warnings = validate_unit_params(unit_type, params)
        if param_errors:
            return {"error": f"Parameter validation failed: {param_errors}"}

        # Load session to check model compatibility
        session = session_manager.get_session(session_id)
        effective_model = model_type or session.primary_model_type

        # Validate model compatibility
        is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
        if not is_compatible:
            return {"error": compat_error}

        # Junction units now supported via core/junction_units.py custom classes
        # which work with our 63-component ModifiedADM1 model

        config = UnitConfig(
            unit_id=unit_id,
            unit_type=unit_type,
            params=params,
            inputs=inputs,
            outputs=outputs,
            model_type=model_type,
        )

        result = session_manager.add_unit(session_id, config)

        # Add warnings to result
        if param_warnings:
            result["warnings"] = param_warnings

        # Add port info
        result["n_ins"] = spec.n_ins
        result["n_outs"] = spec.n_outs

        return result

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"create_unit failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def connect_units(
    session_id: str,
    connections: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Add deferred connections between units (for recycles).

    Use this after creating units to wire recycle streams that
    couldn't be specified during unit creation.

    Args:
        session_id: Session identifier
        connections: List of connection dicts. Formats:
            - Standard: {"from": "SP-0", "to": "1-A1"}  # Note: input notation for "to"
            - Direct:   {"from": "U1-U2"} or {"from": "U1-0-1-U2"}

    Returns:
        Dict with connections added and validation status

    Example:
        >>> await connect_units(
        ...     session_id="abc123",
        ...     connections=[{"from": "SP-0", "to": "1-A1"}],
        ... )
        >>> await connect_units(
        ...     session_id="abc123",
        ...     connections=[{"from": "SP-0-1-A1"}],  # Direct notation
        ... )
    """
    from utils.pipe_parser import parse_port_notation

    try:
        results = []
        for conn in connections:
            if not isinstance(conn, dict) or "from" not in conn:
                results.append({"error": f"Invalid connection format (missing 'from'): {conn}"})
                continue

            from_port = conn["from"]

            # Check if direct notation (U1-U2 or U1-0-1-U2) - target embedded in from
            try:
                from_ref = parse_port_notation(from_port)
                if from_ref.port_type == "direct":
                    # Direct notation: to_port is optional/ignored
                    to_port = conn.get("to")  # May be None
                else:
                    # Standard notation: requires to field
                    if "to" not in conn:
                        results.append({"error": f"Standard notation requires 'to' field: {conn}"})
                        continue
                    to_port = conn["to"]
            except ValueError:
                # If parsing fails, require to field for backward compatibility
                if "to" not in conn:
                    results.append({"error": f"Invalid connection format (missing 'to'): {conn}"})
                    continue
                to_port = conn["to"]

            config = ConnectionConfig(
                from_port=from_port,
                to_port=to_port,
                stream_id=conn.get("stream_id"),
            )

            result = session_manager.add_connection(session_id, config)
            results.append(result)

        return {
            "session_id": session_id,
            "connections_added": len([r for r in results if "error" not in r]),
            "results": results,
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"connect_units failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def build_system(
    session_id: str,
    system_id: str,
    unit_order: Optional[List[str]] = None,
    recycle_streams: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Compile flowsheet session into a QSDsan System.

    This validates the flowsheet and compiles it into QSDsan objects.
    Use simulate_built_system() after this to run the simulation.

    Args:
        session_id: Session identifier
        system_id: Name for the compiled system
        unit_order: Optional list of unit IDs for execution order.
                    If not provided, topological sort is used.
        recycle_streams: Optional list of recycle stream IDs

    Returns:
        Dict with system_id, validation status, unit execution order, and build info

    Example:
        >>> await build_system(
        ...     session_id="abc123",
        ...     system_id="custom_mle",
        ...     recycle_streams=["RAS"],
        ... )
    """
    try:
        from utils.topo_sort import validate_flowsheet_connectivity
        from utils.flowsheet_builder import compile_system
        from dataclasses import asdict

        # Load session
        session = session_manager.get_session(session_id)

        # Use native types directly
        manual_order = unit_order
        recycles = set(recycle_streams) if recycle_streams else set()

        # Validate connectivity first
        errors, warnings = validate_flowsheet_connectivity(
            session.units,
            session.streams,
            session.connections,
        )

        if errors:
            session_manager.update_session_status(session_id, "failed")
            return {
                "error": "Flowsheet validation failed",
                "errors": errors,
                "warnings": warnings,
            }

        # Actually compile the QSDsan System
        try:
            system, build_info = compile_system(
                session=session,
                system_id=system_id,
                unit_order=manual_order,
                recycle_stream_ids=recycles,
            )
        except Exception as compile_error:
            session_manager.update_session_status(session_id, "failed")
            return {
                "error": f"System compilation failed: {compile_error}",
                "warnings": warnings,
            }

        # Update session status
        session_manager.update_session_status(session_id, "compiled")

        # Save build config and result
        session_dir = session_manager._get_session_dir(session_id)

        # Save build_config.json (used by simulate to restore build parameters)
        build_config = {
            "system_id": system_id,
            "unit_order": build_info.unit_order,
            "recycle_streams": list(recycles),
        }
        with open(session_dir / "build_config.json", "w") as f:
            json.dump(build_config, f, indent=2)

        # Save system_result.json (detailed build info)
        build_result = {
            "system_id": build_info.system_id,
            "unit_order": build_info.unit_order,
            "recycle_streams": list(recycles),
            "recycle_edges": build_info.recycle_edges,
            "streams_created": build_info.streams_created,
            "units_created": build_info.units_created,
            "build_warnings": build_info.warnings,
        }
        with open(session_dir / "system_result.json", "w") as f:
            json.dump(build_result, f, indent=2)

        return {
            "session_id": session_id,
            "system_id": system_id,
            "status": "compiled",
            "unit_order": build_info.unit_order,
            "recycle_edges": build_info.recycle_edges,
            "streams_created": build_info.streams_created,
            "units_created": build_info.units_created,
            "warnings": warnings + build_info.warnings,
            "message": f"System compiled successfully. Use simulate_built_system(session_id='{session_id}', ...) to simulate.",
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"build_system failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def list_units(
    model_type: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List available SanUnit types with their parameters.

    Args:
        model_type: Filter by compatible process model (e.g., "ASM2d", "mADM1")
        category: Filter by unit category (e.g., "reactor", "separator", "junction")

    Returns:
        Dict with units list and categories

    Example:
        >>> result = await list_units(model_type="ASM2d")
        >>> print(result["units"])  # List of ASM2d-compatible units
    """
    try:
        units = get_all_units(model_type=model_type, category=category)
        categories = get_units_by_category()

        return {
            "units": units,
            "categories": categories,
            "total": len(units),
            "filter": {
                "model_type": model_type,
                "category": category,
            },
        }

    except Exception as e:
        logger.error(f"list_units failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def simulate_built_system(
    session_id: Optional[str] = None,
    system_id: Optional[str] = None,
    duration_days: float = 1.0,
    timestep_hours: float = 1.0,
    method: str = "RK23",
    t_eval: Optional[List[float]] = None,
    track: Optional[List[str]] = None,
    effluent_stream_ids: Optional[List[str]] = None,
    biogas_stream_ids: Optional[List[str]] = None,
    report: bool = True,
    diagram: bool = True,
    include_components: bool = False,
    export_state_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Simulate a compiled flowsheet with comprehensive reporting.

    This is a background job that uses JobManager (like simulate_system).
    The session must be compiled first with build_system().

    Args:
        session_id: Flowsheet session ID (mutually exclusive with system_id)
        system_id: Previously built system ID (mutually exclusive with session_id).
            The system_id is the value returned in build_system() output.
        duration_days: Simulation duration in days
        timestep_hours: Output timestep in hours
        method: ODE solver method ("RK23", "RK45", "BDF")
        t_eval: Optional list of evaluation times (days). If not provided, uses timestep.
        track: Optional list of stream IDs to track dynamically during simulation
        effluent_stream_ids: Optional list of stream IDs for effluent quality analysis
        biogas_stream_ids: Optional list of stream IDs for biogas analysis (mADM1 only)
        report: Generate Quarto report
        diagram: Generate flowsheet diagram
        include_components: Include full component breakdown in results
        export_state_to: Path to export final effluent state as PlantState JSON

    Returns:
        Dict with job_id for tracking via get_job_status/get_job_results

    Reporting Features:
        - Effluent quality: COD, TSS, NH4-N, NO3-N, TN, PO4-P, TP
        - Removal efficiencies: % removal for key parameters
        - Biogas (mADM1 only): CH4/CO2/H2S/H2 yields, production rate
        - Flowsheet diagram (SVG)
        - Quarto report with mass balance tables

    Example:
        >>> # Using session_id
        >>> result = await simulate_built_system(
        ...     session_id="abc123",
        ...     duration_days=15,
        ...     report=True,
        ... )
        >>> # Or using system_id from build_system output
        >>> result = await simulate_built_system(
        ...     system_id="custom_mle",
        ...     duration_days=15,
        ...     report=True,
        ... )
        >>> job_id = result["job_id"]
        >>> # Use get_job_status(job_id) and get_job_results(job_id)
    """
    try:
        import uuid

        # Validate arguments: exactly one of session_id or system_id must be provided
        if session_id and system_id:
            return {
                "error": "Provide either session_id or system_id, not both"
            }
        if not session_id and not system_id:
            return {
                "error": "Must provide either session_id or system_id"
            }

        # If system_id is provided, find the session that has this system_id
        if system_id:
            # Search for session with matching system_id in build_config
            found_session_id = None
            sessions_dir = Path("jobs") / "flowsheets"
            if sessions_dir.exists():
                for session_dir in sessions_dir.iterdir():
                    if session_dir.is_dir():
                        build_config_path = session_dir / "build_config.json"
                        if build_config_path.exists():
                            try:
                                with open(build_config_path) as f:
                                    build_config = json.load(f)
                                if build_config.get("system_id") == system_id:
                                    found_session_id = session_dir.name
                                    break
                            except Exception:
                                continue
            if not found_session_id:
                return {
                    "error": f"No compiled session found with system_id '{system_id}'. "
                    f"Run build_system() first to create a system."
                }
            session_id = found_session_id

        # Load and validate session
        session = session_manager.get_session(session_id)

        if session.status != "compiled":
            return {
                "error": f"Session status is '{session.status}', must be 'compiled'. "
                f"Run build_system(session_id='{session_id}', ...) first."
            }

        # Create job directory
        job_id = str(uuid.uuid4())[:8]
        job_dir = Path("jobs") / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Copy session info to job dir
        session_dir = session_manager._get_session_dir(session_id)
        import shutil
        shutil.copy(session_dir / "session.json", job_dir / "session.json")
        if (session_dir / "build_config.json").exists():
            shutil.copy(session_dir / "build_config.json", job_dir / "build_config.json")

        # Save simulation config
        sim_config = {
            "session_id": session_id,
            "duration_days": duration_days,
            "timestep_hours": timestep_hours,
            "method": method,
            "t_eval": t_eval,
            "track": track,
            "effluent_stream_ids": effluent_stream_ids,
            "biogas_stream_ids": biogas_stream_ids,
            "report": report,
            "diagram": diagram,
            "include_components": include_components,
            "export_state_to": export_state_to,
        }
        with open(job_dir / "sim_config.json", "w") as f:
            json.dump(sim_config, f, indent=2)

        # Build CLI command
        python_exe = get_python_executable()
        cmd = [
            python_exe,
            "cli.py",
            "flowsheet", "simulate",
            "--session", session_id,
            "--output-dir", str(job_dir),
            "--duration-days", str(duration_days),
            "--timestep-hours", str(timestep_hours),
            "--method", method,
        ]

        if t_eval:
            cmd.extend(["--t-eval", json.dumps(t_eval)])
        if track:
            cmd.extend(["--track", json.dumps(track)])
        if effluent_stream_ids:
            cmd.extend(["--effluent-streams", json.dumps(effluent_stream_ids)])
        if biogas_stream_ids:
            cmd.extend(["--biogas-streams", json.dumps(biogas_stream_ids)])
        if report:
            cmd.append("--report")
        if diagram:
            cmd.append("--diagram")
        if include_components:
            cmd.append("--include-components")
        if export_state_to:
            cmd.extend(["--export-state-to", export_state_to])

        # Execute as background job
        cwd = str(Path(__file__).parent.absolute())
        job = await job_manager.execute(cmd=cmd, cwd=cwd, job_id=job_id)

        return {
            "job_id": job["id"],
            "status": job["status"],
            "session_id": session_id,
            "duration_days": duration_days,
            "message": f"Simulation started. Use get_job_status('{job['id']}') to monitor progress.",
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"simulate_built_system failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Session Management Utilities
# =============================================================================

@mcp.tool()
async def get_flowsheet_session(session_id: str) -> Dict[str, Any]:
    """
    Get details of a flowsheet session.

    Args:
        session_id: Session identifier

    Returns:
        Dict with session summary including streams, units, and connections
    """
    try:
        return session_manager.get_session_summary(session_id)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"get_flowsheet_session failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def list_flowsheet_sessions(
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List all flowsheet sessions.

    Args:
        status_filter: Optional filter by status ("building", "compiled", "failed")

    Returns:
        Dict with sessions list
    """
    try:
        sessions = session_manager.list_sessions(status_filter=status_filter)
        return {
            "sessions": sessions,
            "total": len(sessions),
            "filter": status_filter,
        }
    except Exception as e:
        logger.error(f"list_flowsheet_sessions failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Session Mutation Tools (Phase 3)
# =============================================================================

@mcp.tool()
async def update_stream(
    session_id: str,
    stream_id: str,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update a stream in the flowsheet session (patch-style).

    Modifying a stream resets session status to 'building' if it was 'compiled'.
    Requires re-running build_system() before simulating.

    Args:
        session_id: Session identifier
        stream_id: Stream to update
        updates: Dict of fields to update. Valid fields:
                 flow_m3_d, temperature_K, concentrations (merged), stream_type, model_type

    Returns:
        Dict with stream_id, updated fields, and session status

    Example:
        >>> await update_stream(
        ...     session_id="abc123",
        ...     stream_id="influent",
        ...     updates={"flow_m3_d": 5000, "concentrations": {"S_F": 100}},
        ... )
    """
    try:
        return session_manager.update_stream(session_id, stream_id, updates)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"update_stream failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def update_unit(
    session_id: str,
    unit_id: str,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update a unit in the flowsheet session (patch-style).

    Modifying a unit resets session status to 'building' if it was 'compiled'.
    Requires re-running build_system() before simulating.

    Args:
        session_id: Session identifier
        unit_id: Unit to update
        updates: Dict of fields to update. Valid fields:
                 params (merged), inputs, outputs, model_type

    Returns:
        Dict with unit_id, updated fields, and session status

    Example:
        >>> await update_unit(
        ...     session_id="abc123",
        ...     unit_id="A1",
        ...     updates={"params": {"V_max": 1500}},
        ... )
    """
    try:
        return session_manager.update_unit(session_id, unit_id, updates)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"update_unit failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def delete_stream(
    session_id: str,
    stream_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Delete a stream from the flowsheet session.

    By default, fails if any units reference this stream in their inputs.
    Use force=True to delete anyway (also removes from unit inputs).

    Args:
        session_id: Session identifier
        stream_id: Stream to delete
        force: If True, also remove from unit inputs that reference this stream

    Returns:
        Dict with deletion status and any units that had their inputs modified

    Example:
        >>> await delete_stream(session_id="abc123", stream_id="RAS", force=True)
    """
    try:
        return session_manager.delete_stream(session_id, stream_id, force)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"delete_stream failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def delete_unit(
    session_id: str,
    unit_id: str,
) -> Dict[str, Any]:
    """
    Delete a unit from the flowsheet session.

    Also removes any connections that reference this unit.

    Args:
        session_id: Session identifier
        unit_id: Unit to delete

    Returns:
        Dict with deletion status and list of removed connections

    Example:
        >>> await delete_unit(session_id="abc123", unit_id="SP")
    """
    try:
        return session_manager.delete_unit(session_id, unit_id)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"delete_unit failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def delete_connection(
    session_id: str,
    from_port: str,
    to_port: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delete a specific connection from the flowsheet session.

    Args:
        session_id: Session identifier
        from_port: Source port of connection to delete (e.g., "SP-0")
        to_port: Destination port (e.g., "A1-1"). Optional for direct notation.

    Returns:
        Dict with deletion status

    Example:
        >>> await delete_connection(session_id="abc123", from_port="SP-0", to_port="A1-1")
    """
    try:
        return session_manager.delete_connection(session_id, from_port, to_port)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"delete_connection failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def clone_session(
    source_session_id: str,
    new_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Clone a flowsheet session for experimentation.

    Creates a copy of the session with a new ID, reset to 'building' status.
    Useful for testing variations without modifying the original.

    Args:
        source_session_id: Session to clone
        new_session_id: Optional custom ID for new session. Auto-generates if not provided.

    Returns:
        Dict with new session info

    Example:
        >>> result = await clone_session(source_session_id="abc123")
        >>> new_id = result["new_session_id"]
        >>> # Now modify the clone without affecting original
        >>> await update_unit(session_id=new_id, unit_id="A1", updates={"params": {"V_max": 2000}})
    """
    try:
        return session_manager.clone_session(source_session_id, new_session_id)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"clone_session failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def get_flowsheet_timeseries(
    job_id: str,
    stream_ids: Optional[List[str]] = None,
    components: Optional[List[str]] = None,
    downsample_factor: int = 1,
) -> Dict[str, Any]:
    """
    Get time-series data from a completed flowsheet simulation.

    Use this tool to retrieve dynamic component trajectories for streams
    that were tracked during simulation (via the 'track' parameter).

    Args:
        job_id: Job identifier from simulate_built_system
        stream_ids: Optional list to filter which streams to include
        components: Optional list to filter which component IDs to include
        downsample_factor: Reduce data points by this factor (default 1 = no downsampling)

    Returns:
        Dict with time array and component trajectories per stream

    Example:
        >>> result = await get_flowsheet_timeseries(
        ...     job_id="abc123",
        ...     stream_ids=["effluent"],
        ...     components=["S_NH4", "S_NO3"],
        ...     downsample_factor=2,
        ... )
        >>> # result["time"] = [0, 0.5, 1.0, ...]
        >>> # result["streams"]["effluent"]["S_NH4"] = [17.0, 15.2, ...]
    """
    try:
        job_dir = Path("jobs") / job_id
        ts_path = job_dir / "timeseries.json"

        if not ts_path.exists():
            # Also check results.json for time_series key
            results_path = job_dir / "results.json"
            if results_path.exists():
                with open(results_path) as f:
                    results = json.load(f)
                if "time_series" in results:
                    ts_data = results["time_series"]
                else:
                    return {
                        "error": f"No time-series data found for job {job_id}. "
                        f"Ensure you used 'track' parameter during simulation."
                    }
            else:
                return {"error": f"Job {job_id} not found or no time-series available"}
        else:
            with open(ts_path) as f:
                ts_data = json.load(f)

        if not ts_data.get("success"):
            return {
                "error": ts_data.get("message", "Time-series extraction failed"),
                "job_id": job_id,
            }

        # Filter streams if specified
        result_streams = ts_data.get("streams", {})
        if stream_ids:
            result_streams = {k: v for k, v in result_streams.items() if k in stream_ids}

        # Filter components if specified
        if components:
            filtered_streams = {}
            for stream_id, stream_data in result_streams.items():
                filtered = {k: v for k, v in stream_data.items() if k in components}
                if filtered:
                    filtered_streams[stream_id] = filtered
            result_streams = filtered_streams

        # Downsample if requested
        time_arr = ts_data.get("time", [])
        if downsample_factor > 1:
            time_arr = time_arr[::downsample_factor]
            downsampled_streams = {}
            for stream_id, stream_data in result_streams.items():
                downsampled_streams[stream_id] = {
                    comp_id: values[::downsample_factor]
                    for comp_id, values in stream_data.items()
                }
            result_streams = downsampled_streams

        return {
            "job_id": job_id,
            "time": time_arr,
            "time_units": ts_data.get("time_units", "days"),
            "streams": result_streams,
            "n_timepoints": len(time_arr),
            "n_streams": len(result_streams),
        }

    except Exception as e:
        logger.error(f"get_flowsheet_timeseries failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def delete_session(session_id: str) -> Dict[str, Any]:
    """
    Delete a flowsheet session and all its files.

    Args:
        session_id: Session identifier

    Returns:
        Dict with deletion status

    Example:
        >>> await delete_session(session_id="abc123")
    """
    try:
        deleted = session_manager.delete_session(session_id)
        if deleted:
            return {"session_id": session_id, "status": "deleted"}
        else:
            return {"error": f"Session '{session_id}' not found"}
    except Exception as e:
        logger.error(f"delete_session failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Phase 3B: Discoverability Tools
# =============================================================================

# Component metadata for discoverability (typical domestic wastewater values)
COMPONENT_METADATA = {
    "ASM2d": {
        "S_O2": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_F": {"name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75},
        "S_A": {"name": "Acetate (VFA)", "category": "soluble", "typical_domestic_mg_L": 20},
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 30},
        "S_NH4": {"name": "Ammonium-N", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_N2": {"name": "Dinitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO3": {"name": "Nitrate-N", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_PO4": {"name": "Phosphate-P", "category": "soluble", "typical_domestic_mg_L": 9},
        "S_ALK": {"name": "Alkalinity", "category": "soluble", "typical_domestic_mg_L": 300},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 50},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 125},
        "X_H": {"name": "Heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 30},
        "X_PAO": {"name": "PAO biomass", "category": "biomass", "typical_domestic_mg_L": 0},
        "X_PP": {"name": "Poly-phosphate", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_PHA": {"name": "Poly-hydroxy-alkanoates", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_AUT": {"name": "Autotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 0},
        "X_MeOH": {"name": "Metal-hydroxides", "category": "particulate", "typical_domestic_mg_L": 0},
        "X_MeP": {"name": "Metal-phosphates", "category": "particulate", "typical_domestic_mg_L": 0},
        "H2O": {"name": "Water", "category": "solvent", "typical_domestic_mg_L": None},
    },
    "mADM1": {
        "S_su": {"name": "Sugars", "category": "soluble", "typical_domestic_mg_L": 50},
        "S_aa": {"name": "Amino acids", "category": "soluble", "typical_domestic_mg_L": 50},
        "S_fa": {"name": "Fatty acids", "category": "soluble", "typical_domestic_mg_L": 100},
        "S_ac": {"name": "Acetate", "category": "soluble", "typical_domestic_mg_L": 200},
        "S_IC": {"name": "Inorganic carbon", "category": "soluble", "typical_domestic_mg_L": 50},
        "S_IN": {"name": "Inorganic nitrogen", "category": "soluble", "typical_domestic_mg_L": 200},
        "S_IP": {"name": "Inorganic phosphorus", "category": "soluble", "typical_domestic_mg_L": 50},
        "X_ch": {"name": "Carbohydrates", "category": "particulate", "typical_domestic_mg_L": 1000},
        "X_pr": {"name": "Proteins", "category": "particulate", "typical_domestic_mg_L": 2000},
        "X_li": {"name": "Lipids", "category": "particulate", "typical_domestic_mg_L": 1000},
        "S_SO4": {"name": "Sulfate", "category": "soluble", "typical_domestic_mg_L": 100},
        "S_IS": {"name": "Dissolved sulfide", "category": "soluble", "typical_domestic_mg_L": 0},
    },
    "ASM1": {
        "S_I": {"name": "Soluble inerts", "category": "soluble", "typical_domestic_mg_L": 30},
        "S_S": {"name": "Readily biodegradable substrate", "category": "soluble", "typical_domestic_mg_L": 70},
        "X_I": {"name": "Particulate inerts", "category": "particulate", "typical_domestic_mg_L": 50},
        "X_S": {"name": "Slowly biodegradable substrate", "category": "particulate", "typical_domestic_mg_L": 125},
        "X_BH": {"name": "Active heterotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 30},
        "X_BA": {"name": "Active autotrophic biomass", "category": "biomass", "typical_domestic_mg_L": 0},
        "X_P": {"name": "Particulate products from decay", "category": "particulate", "typical_domestic_mg_L": 0},
        "S_O": {"name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NO": {"name": "Nitrate+nitrite nitrogen", "category": "soluble", "typical_domestic_mg_L": 0},
        "S_NH": {"name": "Ammonia nitrogen", "category": "soluble", "typical_domestic_mg_L": 17},
        "S_ND": {"name": "Soluble biodegradable organic N", "category": "soluble", "typical_domestic_mg_L": 8},
        "X_ND": {"name": "Particulate biodegradable organic N", "category": "particulate", "typical_domestic_mg_L": 10},
        "S_ALK": {"name": "Alkalinity", "category": "soluble", "typical_domestic_mg_L": 300},
    },
}


@mcp.tool()
async def get_model_components(
    model_type: str,
    include_typical_values: bool = True,
) -> Dict[str, Any]:
    """
    Get component IDs and metadata for a process model.

    Use this tool to discover valid component IDs before creating streams.

    Args:
        model_type: Process model ("ASM2d", "mADM1", "ASM1", etc.)
        include_typical_values: Include typical domestic wastewater concentrations

    Returns:
        Dict with component IDs, names, categories, and typical values

    Example:
        >>> result = await get_model_components(model_type="ASM2d")
        >>> print(result["components"])  # List of component IDs with metadata
    """
    try:
        mt = ModelType(model_type)
        model_info = get_model_info(mt)

        components = model_info.get("components")
        if components is None:
            # For models without predefined components, use QSDsan directly
            return {
                "model_type": model_type,
                "n_components": model_info.get("n_components"),
                "note": f"Component list for {model_type} uses upstream QSDsan. "
                        "Import qsdsan.processes and use the create_*_cmps() functions.",
            }

        # Build component list with metadata
        component_list = []
        metadata = COMPONENT_METADATA.get(model_type, {})

        for comp_id in components:
            comp_info = {"id": comp_id}
            if comp_id in metadata:
                meta = metadata[comp_id]
                comp_info["name"] = meta.get("name", comp_id)
                comp_info["category"] = meta.get("category", "unknown")
                if include_typical_values and meta.get("typical_domestic_mg_L") is not None:
                    comp_info["typical_domestic_mg_L"] = meta["typical_domestic_mg_L"]
            component_list.append(comp_info)

        # Group by category
        categories = {}
        for comp in component_list:
            cat = comp.get("category", "unknown")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(comp["id"])

        return {
            "model_type": model_type,
            "n_components": len(components),
            "concentration_units": "mg/L",
            "components": component_list,
            "categories": categories,
            "description": model_info.get("description", ""),
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"get_model_components failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def validate_flowsheet(session_id: str) -> Dict[str, Any]:
    """
    Validate a flowsheet without compiling it.

    Performs pre-compilation validation checks including:
    - Unit inputs resolve to streams or other units
    - No orphan units (not connected to anything)
    - Model compatibility across junctions
    - Potential recycle detection

    Args:
        session_id: Session identifier

    Returns:
        Dict with is_valid, errors, warnings, and detected recycles

    Example:
        >>> result = await validate_flowsheet(session_id="abc123")
        >>> if not result["is_valid"]:
        ...     print(result["errors"])
    """
    try:
        from utils.topo_sort import validate_flowsheet_connectivity, detect_cycles
        from core.unit_registry import get_unit_spec

        session = session_manager.get_session(session_id)

        # Run connectivity validation
        errors, warnings = validate_flowsheet_connectivity(
            session.units,
            session.streams,
            session.connections,
        )

        # Detect potential recycles (cycles in the graph)
        detected_recycles = []
        try:
            cycle_info = detect_cycles(
                session.units,
                session.connections,
                existing_recycles=set(),
            )
            if cycle_info:
                detected_recycles = cycle_info
        except Exception:
            pass  # Cycle detection is optional

        # Check for orphan units (no inputs and no outputs connected)
        orphan_units = []
        for unit_id, config in session.units.items():
            has_input = bool(config.inputs)
            has_output = any(
                conn.from_port.startswith(unit_id)
                for conn in session.connections
            )
            has_downstream = any(
                unit_id in other_config.inputs
                for other_id, other_config in session.units.items()
                if other_id != unit_id
            )
            if not has_input and not has_output and not has_downstream:
                orphan_units.append(unit_id)

        if orphan_units:
            warnings.append(f"Orphan units (not connected): {orphan_units}")

        # Model compatibility check across junctions (Phase 3B.2)
        # Junction units must be compatible with models of connected units
        junction_types = {"ASM2dtoADM1", "ADM1toASM2d", "mADM1toASM2d", "ASM2dtomADM1",
                         "ASMtoADM", "ADMtoASM", "ADM1ptomASM2d", "mASM2dtoADM1p"}
        model_compat_warnings = []

        for unit_id, config in session.units.items():
            unit_type = config.unit_type
            if unit_type in junction_types:
                # Get the junction's compatible models
                try:
                    spec = get_unit_spec(unit_type)
                    junction_models = set(spec.compatible_models) if spec.compatible_models else set()
                except Exception:
                    continue  # Can't check if spec not found

                # Check upstream units (feeding into this junction)
                for input_ref in config.inputs:
                    # input_ref could be stream_id or "UnitID-port"
                    upstream_unit_id = None
                    if "-" in input_ref and not input_ref.startswith("-"):
                        # Likely unit port notation
                        upstream_unit_id = input_ref.split("-")[0]
                    elif input_ref in session.units:
                        upstream_unit_id = input_ref

                    if upstream_unit_id and upstream_unit_id in session.units:
                        upstream_config = session.units[upstream_unit_id]
                        upstream_type = upstream_config.unit_type
                        try:
                            upstream_spec = get_unit_spec(upstream_type)
                            upstream_models = set(upstream_spec.compatible_models) if upstream_spec.compatible_models else set()
                            # Check if there's any overlap
                            if upstream_models and junction_models:
                                common = upstream_models & junction_models
                                if not common:
                                    model_compat_warnings.append(
                                        f"Junction '{unit_id}' ({unit_type}) may be incompatible with upstream unit "
                                        f"'{upstream_unit_id}' ({upstream_type}): junction supports {list(junction_models)}, "
                                        f"upstream supports {list(upstream_models)}"
                                    )
                        except Exception:
                            pass  # Can't check if spec not found

        warnings.extend(model_compat_warnings)

        return {
            "session_id": session_id,
            "is_valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "detected_recycles": detected_recycles,
            "n_units": len(session.units),
            "n_streams": len(session.streams),
            "n_connections": len(session.connections),
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"validate_flowsheet failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def suggest_recycles(session_id: str) -> Dict[str, Any]:
    """
    Detect potential recycle streams in a flowsheet.

    Analyzes the flowsheet topology to identify cycles that likely
    represent recycle streams (e.g., RAS, internal recycles).

    Args:
        session_id: Session identifier

    Returns:
        Dict with detected cycles and suggested recycle configurations

    Example:
        >>> result = await suggest_recycles(session_id="abc123")
        >>> for suggestion in result["suggestions"]:
        ...     print(f"Recycle: {suggestion['suggested_recycle']}")
    """
    try:
        from utils.topo_sort import detect_cycles

        session = session_manager.get_session(session_id)

        # Use topo_sort helper to detect cycles
        cycles = detect_cycles(
            session.units,
            session.connections,
        )

        # Generate suggestions from detected cycles
        suggestions = []
        for cycle_info in cycles:
            cycle_path = cycle_info.get("cycle_path", [])
            if len(cycle_path) >= 2:
                # Suggest the last edge as recycle
                from_unit = cycle_path[-2]
                to_unit = cycle_path[-1]

                # Determine recycle type based on unit types
                from_type = session.units[from_unit].unit_type if from_unit in session.units else None

                recycle_type = "internal_recycle"
                if from_type and "Splitter" in from_type:
                    recycle_type = "return_activated_sludge"
                elif from_type and "Clarifier" in from_type:
                    recycle_type = "return_activated_sludge"

                suggestions.append({
                    "cycle_path": cycle_path,
                    "suggested_recycle": {
                        "from": f"{from_unit}-0",
                        "to": f"{to_unit}-1",
                        "stream_id": f"recycle_{from_unit}_{to_unit}",
                    },
                    "recycle_type": recycle_type,
                    "confidence": "medium",
                })

        # Identify sources and sinks
        sources = [
            sid for sid, s in session.streams.items()
            if s.stream_type == "influent"
        ]

        # Find sinks: units that are not in any connection's from_unit
        all_from_units = set()
        for conn in session.connections:
            try:
                from utils.pipe_parser import parse_port_notation
                from_ref = parse_port_notation(conn.from_port)
                all_from_units.add(from_ref.unit_id)
            except Exception:
                pass
        sinks = [uid for uid in session.units.keys() if uid not in all_from_units]

        return {
            "session_id": session_id,
            "n_cycles_detected": len(cycles),
            "detected_cycles": [c.get("cycle_path", []) for c in cycles],
            "suggestions": suggestions,
            "topology": {
                "sources": sources,
                "sinks": sinks,
            },
        }

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"suggest_recycles failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Phase 3C: Engineering-Grade Results
# =============================================================================

@mcp.tool()
async def get_artifact(
    job_id: str,
    artifact_type: str,
    format: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get simulation artifact content directly.

    Use this tool to retrieve diagram SVGs, QMD reports, or other artifacts
    as content rather than just file paths.

    Args:
        job_id: Job identifier from simulation
        artifact_type: Type of artifact: "diagram", "report", "timeseries"
        format: Optional format hint (e.g., "svg", "qmd", "json")

    Returns:
        Dict with artifact content, path, and metadata

    Example:
        >>> result = await get_artifact(job_id="abc123", artifact_type="diagram")
        >>> svg_content = result["content"]
    """
    try:
        job_dir = Path("jobs") / job_id

        if not job_dir.exists():
            return {"error": f"Job {job_id} not found"}

        # Map artifact types to file patterns
        artifact_patterns = {
            "diagram": ["flowsheet.svg", "flowsheet.png", "diagram.svg"],
            "report": ["report.qmd", "anaerobic_report.qmd", "aerobic_report.qmd"],
            "timeseries": ["timeseries.json", "time_series.json"],
            "results": ["results.json", "simulation_results.json"],
        }

        if artifact_type not in artifact_patterns:
            return {
                "error": f"Unknown artifact_type '{artifact_type}'. "
                f"Valid types: {list(artifact_patterns.keys())}"
            }

        # Find the artifact file
        artifact_path = None
        for pattern in artifact_patterns[artifact_type]:
            candidate = job_dir / pattern
            if candidate.exists():
                artifact_path = candidate
                break

        if artifact_path is None:
            return {
                "error": f"No {artifact_type} artifact found in job {job_id}",
                "searched": artifact_patterns[artifact_type],
                "job_dir": str(job_dir),
            }

        # Determine if binary or text
        binary_extensions = {".svg", ".png", ".pdf"}
        is_binary = artifact_path.suffix in binary_extensions

        # Read content
        if is_binary and artifact_path.suffix == ".svg":
            # SVG is actually text/xml, safe to read as text
            with open(artifact_path, "r", encoding="utf-8") as f:
                content = f.read()
            is_binary = False
        elif is_binary:
            # For true binary files, return base64 or just path
            import base64
            with open(artifact_path, "rb") as f:
                content = base64.b64encode(f.read()).decode("ascii")
            return {
                "job_id": job_id,
                "artifact_type": artifact_type,
                "format": artifact_path.suffix[1:],
                "encoding": "base64",
                "content": content,
                "path": str(artifact_path),
                "size_bytes": artifact_path.stat().st_size,
            }
        else:
            # Text file
            with open(artifact_path, "r", encoding="utf-8") as f:
                content = f.read()

        # Parse JSON if applicable
        if artifact_path.suffix == ".json":
            try:
                parsed = json.loads(content)
                return {
                    "job_id": job_id,
                    "artifact_type": artifact_type,
                    "format": "json",
                    "content": parsed,
                    "path": str(artifact_path),
                }
            except json.JSONDecodeError:
                pass  # Return as raw text

        return {
            "job_id": job_id,
            "artifact_type": artifact_type,
            "format": artifact_path.suffix[1:] if artifact_path.suffix else "text",
            "content": content,
            "path": str(artifact_path),
            "size_bytes": len(content),
        }

    except Exception as e:
        logger.error(f"get_artifact failed: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# Main entry point
# =============================================================================
if __name__ == "__main__":
    mcp.run()
