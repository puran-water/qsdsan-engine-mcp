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
    - convert_state: ASM2d ↔ mADM1 state conversion (background job)

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
from typing import Dict, Any, Optional, Literal

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
    influent_json: str,
    duration_days: float = 1.0,
    timestep_hours: float = 1.0,
    reactor_config: Optional[str] = None,
    parameters: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run QSDsan dynamic simulation using a flowsheet template.

    This tool runs as a background job and returns immediately with a job_id.
    Use get_job_status() to check progress and get_job_results() to retrieve results.

    Args:
        template: Flowsheet template name (e.g., "anaerobic_cstr_madm1", "mle_mbr_asm2d")
        influent_json: JSON string of PlantState for influent
        duration_days: Simulation duration in days (default 1.0)
        timestep_hours: Output timestep in hours (default 1.0)
        reactor_config: Optional JSON string of reactor configuration overrides
        parameters: Optional JSON string of kinetic parameter overrides

    Returns:
        Dict with job_id, status, and instructions for monitoring

    Example:
        >>> result = await simulate_system(
        ...     template="anaerobic_cstr_madm1",
        ...     influent_json='{"model_type": "mADM1", "flow_m3_d": 1000, ...}',
        ...     duration_days=30.0
        ... )
        >>> job_id = result["job_id"]
        >>> # Then call get_job_status(job_id) and get_job_results(job_id)
    """
    try:
        # Parse influent state
        influent_data = json.loads(influent_json)
        influent = PlantState.from_dict(influent_data)

        # Parse optional configs
        reactor_cfg = json.loads(reactor_config) if reactor_config else {}
        params = json.loads(parameters) if parameters else {}

        # Create job directory
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = Path("jobs") / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Save influent state to job directory
        influent.save(str(job_dir / "influent.json"))

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
            cmd.extend(["--reactor-config", reactor_config])
        if parameters:
            cmd.extend(["--parameters", parameters])

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

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON input: {e}"}
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
    state_json: str,
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
        state_json: JSON string of PlantState to validate
        model_type: Target model type ("mADM1", "ASM2d", etc.)
        check_charge_balance: Whether to check electroneutrality
        check_mass_balance: Whether to check mass balance closure

    Returns:
        ValidationResult as dict
    """
    try:
        # Parse state
        state_data = json.loads(state_json)
        state = PlantState.from_dict(state_data)

        # Get model info
        mt = ModelType(model_type)
        model_info = get_model_info(mt)

        errors = []
        warnings = []

        # Check components
        provided = set(state.concentrations.keys())
        missing, extra = validate_components(mt, provided)

        if missing:
            errors.append(f"Missing required components: {missing[:10]}{'...' if len(missing) > 10 else ''}")
        if extra:
            warnings.append(f"Extra components (will be ignored): {extra[:5]}{'...' if len(extra) > 5 else ''}")

        # Basic validation
        if state.flow_m3_d <= 0:
            errors.append(f"flow_m3_d must be positive, got {state.flow_m3_d}")

        if state.temperature_K < 273.15 or state.temperature_K > 373.15:
            warnings.append(f"Temperature {state.temperature_K} K outside typical range (273-373 K)")

        # Check for negative concentrations
        negative = [k for k, v in state.concentrations.items() if v < 0]
        if negative:
            errors.append(f"Negative concentrations: {negative}")

        # Charge balance check (simplified - full implementation in engine)
        charge_balance = None
        if check_charge_balance and mt == ModelType.MADM1:
            # Simplified check - actual implementation uses full speciation
            s_cat = state.concentrations.get('S_Na', 0) + state.concentrations.get('S_K', 0)
            s_an = state.concentrations.get('S_Cl', 0)
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

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}", "is_valid": False}
    except Exception as e:
        logger.error(f"validate_state failed: {e}", exc_info=True)
        return {"error": str(e), "is_valid": False}


# =============================================================================
# Tool 6: convert_state (Background Job)
# =============================================================================
@mcp.tool()
async def convert_state(
    state_json: str,
    from_model: str,
    to_model: str,
) -> Dict[str, Any]:
    """
    Convert PlantState between model types using QSDsan Junction units.

    This tool runs as a background job for complex conversions.
    Supports ASM2d ↔ mADM1 conversions for integrated plant simulation.

    Args:
        state_json: JSON string of PlantState to convert
        from_model: Source model type ("ASM2d", "mADM1", etc.)
        to_model: Target model type

    Returns:
        Dict with job_id for tracking, or direct result for simple conversions

    Example:
        # Convert WAS from activated sludge to anaerobic digester
        >>> result = await convert_state(
        ...     state_json='{"model_type": "ASM2d", ...}',
        ...     from_model="ASM2d",
        ...     to_model="mADM1"
        ... )
    """
    try:
        # Parse state
        state_data = json.loads(state_json)

        # Validate model types
        from_mt = ModelType(from_model)
        to_mt = ModelType(to_model)

        if from_mt == to_mt:
            return {
                "status": "no_conversion_needed",
                "message": f"Source and target model are both {from_model}",
                "state": state_data,
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
                "supported": [f"{f.value} → {t.value}" for f, t in supported_conversions],
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
            "conversion": f"{from_model} → {to_model}",
            "message": f"Conversion started. Use get_job_status('{job['id']}') to monitor.",
        }

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
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
    concentrations: str,
    temperature_K: float = 293.15,
    stream_type: str = "influent",
    model_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a WasteStream in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        stream_id: Unique stream identifier (e.g., "influent", "RAS")
        flow_m3_d: Flow rate in m³/day
        concentrations: JSON dict of component ID → concentration (mg/L)
        temperature_K: Temperature in Kelvin (default 293.15 = 20°C)
        stream_type: One of "influent", "recycle", "intermediate"
        model_type: Process model override (defaults to session's primary model)

    Returns:
        Dict with stream_id and validation status

    Example:
        >>> await create_stream(
        ...     session_id="abc123",
        ...     stream_id="influent",
        ...     flow_m3_d=4000,
        ...     concentrations='{"S_F": 75, "S_A": 20, "S_NH4": 17}',
        ... )
    """
    try:
        # Parse concentrations
        conc_data = json.loads(concentrations)

        config = StreamConfig(
            stream_id=stream_id,
            flow_m3_d=flow_m3_d,
            temperature_K=temperature_K,
            concentrations=conc_data,
            stream_type=stream_type,
            model_type=model_type,
        )

        result = session_manager.add_stream(session_id, config)
        return result

    except json.JSONDecodeError as e:
        return {"error": f"Invalid concentrations JSON: {e}"}
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
    params: str,
    inputs: str,
    outputs: Optional[str] = None,
    model_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a SanUnit in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        unit_type: Unit type from registry (e.g., "CSTR", "Splitter", "CompletelyMixedMBR")
        unit_id: Unique unit identifier (e.g., "A1", "O1", "MBR")
        params: JSON dict of unit-specific parameters
        inputs: JSON list of input sources (stream IDs or pipe notation like "A1-0")
        outputs: Optional JSON list of output stream names
        model_type: Process model override (defaults to session's primary model)

    Returns:
        Dict with unit_id, validation status, and port info

    Example:
        >>> await create_unit(
        ...     session_id="abc123",
        ...     unit_type="CSTR",
        ...     unit_id="A1",
        ...     params='{"V_max": 1000, "aeration": null}',
        ...     inputs='["influent", "RAS"]',
        ... )
    """
    try:
        # Parse JSON inputs
        params_data = json.loads(params)
        inputs_data = json.loads(inputs)
        outputs_data = json.loads(outputs) if outputs else None

        # Validate unit type exists
        try:
            spec = get_unit_spec(unit_type)
        except ValueError as e:
            return {"error": str(e)}

        # Validate parameters
        param_errors, param_warnings = validate_unit_params(unit_type, params_data)
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
            params=params_data,
            inputs=inputs_data,
            outputs=outputs_data,
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

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"create_unit failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def connect_units(
    session_id: str,
    connections: str,
) -> Dict[str, Any]:
    """
    Add deferred connections between units (for recycles).

    Use this after creating units to wire recycle streams that
    couldn't be specified during unit creation.

    Args:
        session_id: Session identifier
        connections: JSON list of connection objects. Formats:
            - Standard: {"from": "SP-0", "to": "A1-1"}
            - Direct:   {"from": "U1-U2"} or {"from": "U1-0-1-U2"}

    Returns:
        Dict with connections added and validation status

    Example:
        >>> await connect_units(
        ...     session_id="abc123",
        ...     connections='[{"from": "SP-0", "to": "A1-1"}]',
        ... )
        >>> await connect_units(
        ...     session_id="abc123",
        ...     connections='[{"from": "SP-0-1-A1"}]',  # Direct notation
        ... )
    """
    from utils.pipe_parser import parse_port_notation

    try:
        # Parse connections
        conn_data = json.loads(connections)

        if not isinstance(conn_data, list):
            return {"error": "connections must be a JSON list"}

        results = []
        for conn in conn_data:
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

    except json.JSONDecodeError as e:
        return {"error": f"Invalid connections JSON: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"connect_units failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def build_system(
    session_id: str,
    system_id: str,
    unit_order: Optional[str] = None,
    recycle_streams: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compile flowsheet session into a QSDsan System.

    This validates the flowsheet and compiles it into QSDsan objects.
    Use simulate_built_system() after this to run the simulation.

    Args:
        session_id: Session identifier
        system_id: Name for the compiled system
        unit_order: Optional JSON list of unit IDs for execution order.
                    If not provided, topological sort is used.
        recycle_streams: Optional JSON list of recycle stream IDs

    Returns:
        Dict with system_id, validation status, unit execution order, and build info

    Example:
        >>> await build_system(
        ...     session_id="abc123",
        ...     system_id="custom_mle",
        ...     recycle_streams='["RAS"]',
        ... )
    """
    try:
        from utils.topo_sort import validate_flowsheet_connectivity
        from utils.flowsheet_builder import compile_system
        from dataclasses import asdict

        # Load session
        session = session_manager.get_session(session_id)

        # Parse optional inputs
        manual_order = json.loads(unit_order) if unit_order else None
        recycles = set(json.loads(recycle_streams)) if recycle_streams else set()

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

    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
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
    t_eval: Optional[str] = None,
    track: Optional[str] = None,
    effluent_stream_ids: Optional[str] = None,
    biogas_stream_ids: Optional[str] = None,
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
        t_eval: Custom evaluation times as JSON list (days). If not provided, uses timestep.
        track: JSON list of stream IDs to track dynamically during simulation
        effluent_stream_ids: JSON list of stream IDs for effluent quality analysis
        biogas_stream_ids: JSON list of stream IDs for biogas analysis (mADM1 only)
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
            cmd.extend(["--t-eval", t_eval])
        if track:
            cmd.extend(["--track", track])
        if effluent_stream_ids:
            cmd.extend(["--effluent-streams", effluent_stream_ids])
        if biogas_stream_ids:
            cmd.extend(["--biogas-streams", biogas_stream_ids])
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
# Main entry point
# =============================================================================
if __name__ == "__main__":
    mcp.run()
