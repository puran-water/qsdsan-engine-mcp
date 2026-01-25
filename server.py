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
    # Phase 9: Junction model transforms
    normalize_model_name,
    get_junction_output_model,
    suggest_junction_for_conversion,
    # Phase 10: Auto-insert junctions
    find_junction_for_conversion,
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

# Use absolute paths relative to this file to avoid CWD issues when run from Claude Desktop
_BASE_DIR = Path(__file__).parent.absolute()
_JOBS_DIR = _BASE_DIR / "jobs"

# Initialize job manager (singleton)
job_manager = JobManager(max_concurrent_jobs=3, jobs_base_dir=str(_JOBS_DIR))

# Initialize flowsheet session manager (singleton)
session_manager = FlowsheetSessionManager(sessions_dir=_JOBS_DIR)


# =============================================================================
# Tool 0: get_version (Version Information)
# =============================================================================
@mcp.tool()
async def get_version() -> Dict[str, Any]:
    """
    Get version information for the QSDsan Engine and its dependencies.

    Returns version numbers for the engine, QSDsan, BioSTEAM, and Python.
    This is useful for debugging and ensuring compatibility.

    Returns:
        Dict with engine_version, qsdsan_version, biosteam_version, python_version
    """
    from core.version import get_version_info
    return get_version_info()


# =============================================================================
# Tool 1: simulate_system (Background Job)
# =============================================================================
@mcp.tool()
async def simulate_system(
    template: str,
    influent: Dict[str, Any],
    duration_days: float = 1.0,
    timestep_hours: Optional[float] = None,
    reactor_config: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    timeout_seconds: float = 300.0,
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
        timestep_hours: Output timestep in hours (aerobic templates only, optional)
        reactor_config: Optional reactor configuration overrides
        parameters: Optional kinetic parameter overrides. For mADM1 templates, supports
                    80+ kinetic parameters including rate constants (k_su, k_aa, k_ac, etc.),
                    half-saturation coefficients (K_su, K_aa, K_ac, etc.), inhibition constants
                    (KI_h2_fa, KI_nh3, etc.), and SRB parameters. See core/kinetic_params.py
                    for the full schema and validation. ASM2d templates also accept kinetics.
        timeout_seconds: Maximum simulation runtime in seconds (default 300 = 5 minutes).
                        If exceeded, the job is terminated with status="timeout".
                        Set to 0 for no timeout (not recommended for production).

    Returns:
        Dict with job_id, status, and instructions for monitoring

    Example:
        >>> result = await simulate_system(
        ...     template="anaerobic_cstr_madm1",
        ...     influent={"model_type": "mADM1", "flow_m3_d": 1000, "concentrations": {...}},
        ...     duration_days=30.0,
        ...     timeout_seconds=600  # 10 minutes for complex anaerobic model
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

        # Create job directory (use absolute path relative to this file to avoid CWD issues)
        import uuid
        job_id = str(uuid.uuid4())[:8]
        base_dir = Path(__file__).parent.absolute()
        job_dir = base_dir / "jobs" / job_id
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
            "timeout_seconds": timeout_seconds,
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
        ]

        # Only pass timestep_hours for aerobic templates (anaerobic raises if provided)
        if timestep_hours is not None:
            cmd.extend(["--timestep-hours", str(timestep_hours)])

        if reactor_config:
            cmd.extend(["--reactor-config", json.dumps(reactor_config)])
        if parameters:
            cmd.extend(["--parameters", json.dumps(parameters)])

        # Execute as background job with timeout
        cwd = str(Path(__file__).parent.absolute())
        effective_timeout = timeout_seconds if timeout_seconds > 0 else None
        job = await job_manager.execute(
            cmd=cmd,
            cwd=cwd,
            job_id=job_id,
            timeout_seconds=effective_timeout,
        )

        return {
            "job_id": job["id"],
            "status": job["status"],
            "template": template,
            "duration_days": duration_days,
            "timeout_seconds": timeout_seconds,
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

        # Mass balance check - single-state consistency (COD, TKN, TP totals)
        consistency_result = None
        if check_mass_balance:
            from core.converters import validate_state_consistency
            consistency_result = validate_state_consistency(plant_state)
            if not consistency_result.get("passed", True):
                warnings.extend(consistency_result.get("warnings", []))

        # Charge balance check - real electroneutrality for mADM1
        charge_balance = None
        if check_charge_balance and mt == ModelType.MADM1:
            from core.converters import validate_charge_balance
            charge_result = validate_charge_balance(plant_state)
            charge_balance = {
                "cation_meq_L": charge_result.get("cation_meq_L", 0),
                "anion_meq_L": charge_result.get("anion_meq_L", 0),
                "imbalance_meq_L": charge_result.get("imbalance_meq_L", 0),
                "passed": charge_result.get("passed", True),
            }
            if not charge_balance["passed"]:
                warnings.append(
                    f"Charge balance error: imbalance {charge_balance['imbalance_meq_L']:.2f} meq/L"
                )

        result = ValidationResult(
            is_valid=len(errors) == 0,
            model_type=mt,
            errors=errors,
            warnings=warnings,
            charge_balance=charge_balance,
            mass_balance=consistency_result,  # COD/TKN/TP totals
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

        # Create job directory (use absolute path relative to this file to avoid CWD issues)
        import uuid
        job_id = str(uuid.uuid4())[:8]
        base_dir = Path(__file__).parent.absolute()
        job_dir = base_dir / "jobs" / job_id
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


# =============================================================================
# Phase 9: Model Zone Computation for Mixed-Model Flowsheets
# =============================================================================

def compute_effective_model_at_unit(
    session: "FlowsheetSessionManager",
    unit_inputs: List[str],
    explicit_model: Optional[str] = None,
    _depth: int = 0,
) -> tuple[str, List[str]]:
    """
    Compute effective model for a unit based on upstream junctions.

    This function enables mixed-model flowsheet construction by tracing upstream
    through junctions to determine the effective model at any point in the flowsheet.

    Priority:
    1. Explicit model_type if provided (user override)
    2. Output model of upstream junction (if any)
    3. Upstream unit's explicit model_type
    4. Session primary_model_type

    Args:
        session: FlowsheetSession instance
        unit_inputs: List of input port notations or stream IDs
        explicit_model: User-provided model_type override
        _depth: Internal recursion depth counter (Phase 10 cycle guard)

    Returns:
        Tuple of (effective_model, list of warnings)

    Raises:
        ValueError: If traversal depth exceeds 20 (possible cycle)
    """
    from utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation

    # Phase 10: Guard against infinite loops from cycles
    if _depth > 20:
        raise ValueError(
            "Junction chain too deep (>20 levels). Possible cycle detected in flowsheet. "
            "Check for circular unit connections involving junctions."
        )

    warnings = []

    # Priority 1: Honor explicit override
    if explicit_model:
        return normalize_model_name(explicit_model), warnings

    # Collect models from all inputs for fan-in validation
    input_models = []

    for inp in unit_inputs:
        # Handle tuple notation for fan-in
        if is_tuple_notation(inp):
            port_strs = parse_tuple_notation(inp)
        else:
            port_strs = [inp]

        for port_str in port_strs:
            try:
                ref = parse_port_notation(port_str)
                upstream_unit_id = ref.unit_id

                if upstream_unit_id in session.units:
                    upstream_config = session.units[upstream_unit_id]
                    junction_transform = get_junction_output_model(upstream_config.unit_type)

                    if junction_transform:
                        # This is a junction - use its output model
                        input_models.append(normalize_model_name(junction_transform[1]))
                    elif upstream_config.model_type:
                        # Upstream has explicit model
                        input_models.append(normalize_model_name(upstream_config.model_type))
                    else:
                        # Recursively trace upstream (for junction chains)
                        # Phase 10: Pass depth counter to prevent infinite loops
                        upstream_model, _ = compute_effective_model_at_unit(
                            session, upstream_config.inputs, upstream_config.model_type, _depth + 1
                        )
                        input_models.append(upstream_model)
                elif upstream_unit_id in session.streams:
                    # Stream input - use stream's model if set, else session primary
                    stream_config = session.streams[upstream_unit_id]
                    if stream_config.model_type:
                        input_models.append(normalize_model_name(stream_config.model_type))
                    else:
                        input_models.append(normalize_model_name(session.primary_model_type))
                else:
                    # Unknown input (deferred recycle) - use session primary
                    input_models.append(normalize_model_name(session.primary_model_type))
            except ValueError:
                # Parse error - skip this input, use session primary as fallback
                input_models.append(normalize_model_name(session.primary_model_type))

    # Fan-in validation: warn if multiple different models
    unique_models = set(input_models)
    if len(unique_models) > 1:
        warnings.append(
            f"Multiple input models detected: {sorted(unique_models)}. "
            f"Consider adding junctions to unify component sets before mixing."
        )

    # Return first input's model (or session primary if no inputs)
    if input_models:
        return input_models[0], warnings
    return normalize_model_name(session.primary_model_type), warnings


def _auto_insert_junction(
    session: "FlowsheetSessionManager",
    source_unit_id: str,
    source_port: int,
    source_model: str,
    target_model: str,
) -> tuple[Optional[str], str, Optional[str]]:
    """
    Phase 10: Auto-insert a junction unit to convert source_model to target_model.

    Creates a junction unit in the session to bridge model mismatches at fan-in points.

    NOTE: For our custom mADM1 (63 components), we use our custom junction
    implementations in core/junction_units.py, NOT upstream QSDsan junctions.
    Upstream junctions target ADM1_p_extension, not our 63-component model.

    Args:
        session: FlowsheetSession instance
        source_unit_id: ID of the upstream unit
        source_port: Output port index of the upstream unit
        source_model: Model type of the source (e.g., "mADM1")
        target_model: Target model type (e.g., "ASM2d")

    Returns:
        Tuple of (junction_unit_id, junction_output_port, warning_message)
        Returns (None, original_port, None) if no junction available
    """
    junction_type = find_junction_for_conversion(source_model, target_model)
    if not junction_type:
        # No junction available - can't auto-insert
        return None, f"{source_unit_id}-{source_port}", None

    # Generate unique junction ID
    junction_id = f"_auto_{junction_type}_{source_unit_id}"
    counter = 1
    while junction_id in session.units:
        junction_id = f"_auto_{junction_type}_{source_unit_id}_{counter}"
        counter += 1

    # Create junction unit config
    junction_config = UnitConfig(
        unit_id=junction_id,
        unit_type=junction_type,
        params={},
        inputs=[f"{source_unit_id}-{source_port}"],
        outputs=[f"{junction_id}-0"],
        model_type=target_model,  # Output model
        auto_inserted=True,  # Track for debugging
    )
    session.units[junction_id] = junction_config

    logger.info(f"Phase 10: Auto-inserted {junction_type} junction '{junction_id}' to convert {source_model} -> {target_model}")

    warning = f"Auto-inserted junction '{junction_id}' to convert {source_model} -> {target_model} for input '{source_unit_id}-{source_port}'"
    return junction_id, f"{junction_id}-0", warning


def _rewrite_inputs_with_junctions(
    session: "FlowsheetSessionManager",
    inputs: List[str],
    target_model: str,
) -> tuple[List[str], List[str]]:
    """
    Phase 10: Rewrite inputs to auto-insert junctions where needed.

    Scans inputs for model mismatches and auto-inserts junction units to unify
    component sets at fan-in points.

    Args:
        session: FlowsheetSession instance
        inputs: Original list of input port notations or stream IDs
        target_model: Target model type for the unit being created

    Returns:
        Tuple of (rewritten_inputs, auto_insert_warnings)
    """
    from utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation

    rewritten_inputs = []
    warnings = []
    target_norm = normalize_model_name(target_model)

    for inp in inputs:
        # Handle tuple notation for fan-in
        if is_tuple_notation(inp):
            port_strs = parse_tuple_notation(inp)
            rewritten_ports = []
            for port_str in port_strs:
                try:
                    ref = parse_port_notation(port_str)
                    inp_model = _get_model_for_input(session, ref)

                    if inp_model and normalize_model_name(inp_model) != target_norm:
                        # Model mismatch - try to auto-insert junction
                        junction_id, new_port, warning = _auto_insert_junction(
                            session, ref.unit_id, ref.port_index, inp_model, target_model
                        )
                        if junction_id:
                            rewritten_ports.append(new_port)
                            if warning:
                                warnings.append(warning)
                        else:
                            # No junction available - keep original and warn
                            rewritten_ports.append(port_str)
                            warnings.append(
                                f"No junction available to convert {inp_model} -> {target_model} "
                                f"for input '{port_str}'. Component mismatch may cause build failure."
                            )
                    else:
                        rewritten_ports.append(port_str)
                except ValueError:
                    # Parse error - keep original
                    rewritten_ports.append(port_str)

            # Reconstruct tuple notation
            rewritten_inputs.append("(" + ", ".join(rewritten_ports) + ")")
        else:
            # Single input
            try:
                ref = parse_port_notation(inp)
                inp_model = _get_model_for_input(session, ref)

                if inp_model and normalize_model_name(inp_model) != target_norm:
                    # Model mismatch - try to auto-insert junction
                    junction_id, new_port, warning = _auto_insert_junction(
                        session, ref.unit_id, ref.port_index, inp_model, target_model
                    )
                    if junction_id:
                        rewritten_inputs.append(new_port)
                        if warning:
                            warnings.append(warning)
                    else:
                        # No junction available - keep original and warn
                        rewritten_inputs.append(inp)
                        warnings.append(
                            f"No junction available to convert {inp_model} -> {target_model} "
                            f"for input '{inp}'. Component mismatch may cause build failure."
                        )
                else:
                    rewritten_inputs.append(inp)
            except ValueError:
                # Parse error - keep original
                rewritten_inputs.append(inp)

    return rewritten_inputs, warnings


def _get_model_for_input(session, ref) -> Optional[str]:
    """
    Get the effective model for an input reference.

    Args:
        session: FlowsheetSession instance
        ref: Parsed port reference

    Returns:
        Model type string or None if unknown
    """
    upstream_unit_id = ref.unit_id

    if upstream_unit_id in session.units:
        upstream_config = session.units[upstream_unit_id]
        junction_transform = get_junction_output_model(upstream_config.unit_type)

        if junction_transform:
            # This is a junction - use its output model
            return junction_transform[1]
        elif upstream_config.model_type:
            return upstream_config.model_type
        else:
            # Fall through to session primary
            return session.primary_model_type
    elif upstream_unit_id in session.streams:
        stream_config = session.streams[upstream_unit_id]
        return stream_config.model_type or session.primary_model_type
    else:
        # Unknown input (deferred recycle)
        return session.primary_model_type


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
    from utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation

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

        # Phase 9: Compute effective model considering upstream junctions
        # This enables mixed-model flowsheets (e.g., ASM2d with mADM1 digester via junction)
        effective_model, zone_warnings = compute_effective_model_at_unit(
            session, inputs or [], model_type
        )

        # Validate model compatibility with computed effective model
        is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
        if not is_compatible:
            # Provide helpful error with junction suggestion
            suggestion = suggest_junction_for_conversion(
                session.primary_model_type,
                spec.compatible_models
            )

            error_msg = compat_error
            if suggestion:
                error_msg += f" {suggestion}"

            return {"error": error_msg}

        # Phase 10: Auto-insert junctions for model mismatches at fan-in
        # If zone_warnings indicate multiple input models, rewrite inputs with junctions
        auto_insert_warnings = []
        if any("Multiple input models detected" in w for w in zone_warnings):
            # Determine target model (session primary for consistency)
            target_model = normalize_model_name(session.primary_model_type)

            # Rewrite inputs to auto-insert junctions where needed
            inputs, auto_insert_warnings = _rewrite_inputs_with_junctions(
                session, inputs or [], target_model
            )

            # Recompute effective model after junction insertion
            effective_model, zone_warnings = compute_effective_model_at_unit(
                session, inputs, model_type
            )

        # Pre-compilation input validation (Phase 8A)
        # Validate that all input references exist (streams or upstream units)
        input_warnings = []
        for inp in inputs:
            # Handle tuple notation for fan-in
            if is_tuple_notation(inp):
                port_strs = parse_tuple_notation(inp)
            else:
                port_strs = [inp]

            for port_str in port_strs:
                try:
                    ref = parse_port_notation(port_str)
                    if ref.port_type == "stream":
                        # Direct stream reference or unit ID
                        if ref.unit_id not in session.streams and ref.unit_id not in session.units:
                            # Could be a deferred recycle - add as warning, not error
                            input_warnings.append(
                                f"Input '{port_str}' not found in session. "
                                f"Will be treated as deferred connection (recycle)."
                            )
                    elif ref.port_type == "output":
                        # Output port reference (e.g., "A1-0")
                        if ref.unit_id not in session.units:
                            input_warnings.append(
                                f"Input '{port_str}' references unit '{ref.unit_id}' which doesn't exist yet. "
                                f"Will be treated as deferred connection."
                            )
                    elif ref.port_type == "input":
                        # Input port notation can't be used as input source
                        return {
                            "error": f"Invalid input '{port_str}': input port notation "
                            f"(e.g., '1-M1') cannot be used as an input source. "
                            f"Use output notation (e.g., 'M1-0') or stream ID instead."
                        }
                except ValueError as parse_error:
                    return {"error": f"Invalid input notation '{port_str}': {parse_error}"}

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

        # Add warnings to result (combine param, input, zone, and auto-insert warnings)
        all_warnings = param_warnings + input_warnings + zone_warnings + auto_insert_warnings
        if all_warnings:
            result["warnings"] = all_warnings

        # Add port info and effective model (Phase 9)
        result["n_ins"] = spec.n_ins
        result["n_outs"] = spec.n_outs
        result["effective_model"] = effective_model

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
    timeout_seconds: float = 300.0,
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
        timeout_seconds: Maximum simulation runtime in seconds (default 300 = 5 minutes).
                        If exceeded, the job is terminated with status="timeout".
                        Set to 0 for no timeout (not recommended for production).

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
            sessions_dir = _JOBS_DIR / "flowsheets"
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

        # Create job directory (use absolute path relative to this file to avoid CWD issues)
        job_id = str(uuid.uuid4())[:8]
        base_dir = Path(__file__).parent.absolute()
        job_dir = base_dir / "jobs" / job_id
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

        # Execute as background job with timeout
        cwd = str(Path(__file__).parent.absolute())
        effective_timeout = timeout_seconds if timeout_seconds > 0 else None
        job = await job_manager.execute(
            cmd=cmd,
            cwd=cwd,
            job_id=job_id,
            timeout_seconds=effective_timeout,
        )

        return {
            "job_id": job["id"],
            "status": job["status"],
            "session_id": session_id,
            "duration_days": duration_days,
            "timeout_seconds": timeout_seconds,
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
    from utils.path_utils import validate_safe_path, validate_id

    try:
        # Validate job_id format and prevent path traversal
        validate_id(job_id, "job_id")
        job_dir = validate_safe_path(_JOBS_DIR, job_id, "job_id")
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
    from utils.path_utils import validate_safe_path, validate_id

    try:
        # Validate job_id format and prevent path traversal
        validate_id(job_id, "job_id")
        job_dir = validate_safe_path(_JOBS_DIR, job_id, "job_id")

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
# Phase 8C: TEA (Techno-Economic Analysis) Tools
# =============================================================================

@mcp.tool()
async def create_tea(
    job_id: str,
    discount_rate: float = 0.05,
    lifetime_years: int = 20,
    uptime_ratio: float = 0.95,
    annual_labor: float = 0.0,
    annual_maintenance_factor: float = 0.03,
    electricity_price: float = 0.07,
) -> Dict[str, Any]:
    """
    Create TEA (Techno-Economic Analysis) for a completed simulation.

    This tool creates a SimpleTEA object for economic analysis of the
    simulated wastewater treatment system.

    IMPORTANT: Many QSDsan units (CSTR, Splitter, Mixer) lack _cost() methods.
    CAPEX values may be underestimated. Use heuristic sizing for detailed costing.

    Args:
        job_id: Job identifier from completed simulation
        discount_rate: Annual discount rate (default 0.05 = 5%)
        lifetime_years: Project lifetime in years (default 20)
        uptime_ratio: Operating time fraction (default 0.95 = 95%)
        annual_labor: Annual labor cost in USD (default 0)
        annual_maintenance_factor: Maintenance as fraction of TCI (default 0.03 = 3%)
        electricity_price: Electricity price in USD/kWh (default 0.07)

    Returns:
        Dict with TEA summary including CAPEX, OPEX, and annualized costs

    Example:
        >>> result = await create_tea(job_id="abc123", discount_rate=0.05)
        >>> print(f"TCI: ${result['capex']['TCI']:,.0f}")
    """
    from utils.path_utils import validate_safe_path, validate_id
    from utils.tea_wrapper import create_tea as _create_tea, get_tea_summary

    try:
        # Validate job_id
        validate_id(job_id, "job_id")
        job_dir = validate_safe_path(_JOBS_DIR, job_id, "job_id")

        if not job_dir.exists():
            return {"error": f"Job {job_id} not found"}

        # Check if simulation completed
        results_file = job_dir / "results.json"
        if not results_file.exists():
            results_file = job_dir / "simulation_results.json"

        if not results_file.exists():
            return {"error": f"No results found for job {job_id}. Ensure simulation completed."}

        # Load results to get flow rate
        with open(results_file, "r") as f:
            results = json.load(f)

        flow_m3_d = results.get("influent", {}).get("flow_m3_d", 0)

        # Try to reconstruct the system for TEA
        # This is complex because QSDsan systems don't persist well
        # For now, return a simplified TEA estimate based on results

        # Estimate CAPEX using typical cost curves (simplified)
        # Reference: Metcalf & Eddy (2014), EPA cost estimation guidelines
        total_v = results.get("reactor", {}).get("V_total_m3", 0)

        # Simplified CAPEX estimation ($/m³ reactor volume)
        # Typical range: $500-2000/m³ for activated sludge
        capex_per_m3 = 1000  # USD/m³ (mid-range estimate)
        estimated_equipment_cost = total_v * capex_per_m3

        # Apply cost hierarchy factors (typical)
        # Reference: QSDsan/BioSTEAM TEA hierarchy
        installation_factor = 1.5
        site_factor = 0.15  # Site development as fraction of installed cost
        warehouse_factor = 0.04  # Warehouse/storage
        contingency_factor = 0.10  # Contingency
        working_capital_factor = 0.05  # Working capital as fraction of FCI

        installed_cost = estimated_equipment_cost * installation_factor
        dpi = installed_cost * (1 + site_factor)  # Direct Permanent Investment
        tdc = dpi * (1 + warehouse_factor)  # Total Depreciable Capital
        fci = tdc * (1 + contingency_factor)  # Fixed Capital Investment
        tci = fci * (1 + working_capital_factor)  # Total Capital Investment

        # Estimate OPEX
        # Power: Use aeration estimate if available
        aeration_power_kW = 0
        if results.get("reactor", {}).get("type") in ("MLE-MBR", "A2O-MBR", "AO-MBR"):
            # Estimate aeration power: ~0.02-0.05 kW/m³
            aeration_power_kW = total_v * 0.03

        hours_per_year = 8760 * uptime_ratio
        electricity_kWh_year = aeration_power_kW * hours_per_year
        electricity_cost_year = electricity_kWh_year * electricity_price

        # Maintenance: configurable fraction of TCI
        maintenance_cost = tci * annual_maintenance_factor

        # Heating/cooling estimation (minimal for aerobic systems)
        # Most aerobic systems don't require significant heating
        heating_GJ_year = 0.0
        cooling_GJ_year = 0.0
        # MBR systems may have some cooling needs from membrane fouling control
        if results.get("reactor", {}).get("type") in ("MLE-MBR", "A2O-MBR", "AO-MBR"):
            # Estimate ~1% of aeration power equivalent for cooling
            cooling_GJ_year = aeration_power_kW * 0.01 * hours_per_year * 3.6 / 1000  # kWh to GJ

        # Total OPEX
        aoc = annual_labor + maintenance_cost + electricity_cost_year

        # Annualized CAPEX (using capital recovery factor)
        crf = discount_rate * (1 + discount_rate) ** lifetime_years / \
              ((1 + discount_rate) ** lifetime_years - 1)
        annualized_capex = tci * crf

        # Per-m³ costs
        m3_per_year = flow_m3_d * 365 * uptime_ratio if flow_m3_d > 0 else 1

        return {
            "job_id": job_id,
            "tea_params": {
                "discount_rate": discount_rate,
                "lifetime_years": lifetime_years,
                "uptime_ratio": uptime_ratio,
                "annual_maintenance_factor": annual_maintenance_factor,
                "electricity_price": electricity_price,
            },
            "capex": {
                "estimated_equipment_cost": estimated_equipment_cost,
                "installed_equipment_cost": installed_cost,
                "DPI": dpi,
                "TDC": tdc,
                "FCI": fci,
                "TCI": tci,
                "note": "Estimated using typical cost curves. Many QSDsan units lack _cost() methods.",
            },
            "opex": {
                "annual_labor": annual_labor,
                "annual_maintenance": maintenance_cost,
                "annual_electricity": electricity_cost_year,
                "AOC": aoc,
            },
            "annualized": {
                "CAPEX": annualized_capex,
                "OPEX": aoc,
                "total": annualized_capex + aoc,
            },
            "per_m3": {
                "TCI_per_m3_capacity": tci / (flow_m3_d * 365) if flow_m3_d > 0 else 0,
                "total_annualized_per_m3": (annualized_capex + aoc) / m3_per_year,
            },
            "utilities": {
                "aeration_power_kW": aeration_power_kW,
                "electricity_kWh_year": electricity_kWh_year,
                "heating_GJ_year": heating_GJ_year,
                "cooling_GJ_year": cooling_GJ_year,
            },
            "currency": "USD",
            "warning": (
                "TEA values are estimates. QSDsan units (CSTR, Splitter, Mixer) "
                "lack detailed _cost() methods. Use equipment-specific costing "
                "for detailed analysis."
            ),
        }

    except Exception as e:
        logger.error(f"create_tea failed: {e}", exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def get_capex_breakdown(
    job_id: str,
) -> Dict[str, Any]:
    """
    Get CAPEX (Capital Expenditure) breakdown for a simulation.

    Returns the capital cost hierarchy:
    - Installed equipment cost
    - DPI (Direct Permanent Investment)
    - TDC (Total Depreciable Capital)
    - FCI (Fixed Capital Investment)
    - TCI (Total Capital Investment)

    Args:
        job_id: Job identifier from completed simulation

    Returns:
        Dict with CAPEX breakdown

    Note:
        Many QSDsan units lack _cost() methods. This returns estimates.
    """
    # Call create_tea and extract CAPEX portion
    tea_result = await create_tea(job_id)

    if "error" in tea_result:
        return tea_result

    return {
        "job_id": job_id,
        "capex": tea_result.get("capex", {}),
        "currency": "USD",
    }


@mcp.tool()
async def get_opex_summary(
    job_id: str,
) -> Dict[str, Any]:
    """
    Get OPEX (Operating Expenditure) summary for a simulation.

    Returns operating cost components:
    - FOC (Fixed Operating Cost): Labor, maintenance
    - VOC (Variable Operating Cost): Utilities
    - AOC (Annual Operating Cost): Total

    Args:
        job_id: Job identifier from completed simulation

    Returns:
        Dict with OPEX breakdown
    """
    # Call create_tea and extract OPEX portion
    tea_result = await create_tea(job_id)

    if "error" in tea_result:
        return tea_result

    return {
        "job_id": job_id,
        "opex": tea_result.get("opex", {}),
        "annualized": tea_result.get("annualized", {}),
        "per_m3": tea_result.get("per_m3", {}),
        "currency": "USD",
        "period": "per_year",
    }


@mcp.tool()
async def get_utility_costs(
    job_id: str,
) -> Dict[str, Any]:
    """
    Get utility consumption and costs for a simulation.

    Returns:
    - Electricity consumption (kWh/year)
    - Estimated aeration power (kW)
    - Heating/cooling if applicable

    Args:
        job_id: Job identifier from completed simulation

    Returns:
        Dict with utility breakdown
    """
    # Call create_tea and extract utilities portion
    tea_result = await create_tea(job_id)

    if "error" in tea_result:
        return tea_result

    utilities = tea_result.get("utilities", {})
    return {
        "job_id": job_id,
        "utilities": utilities,
        "electricity": {
            "power_kW": utilities.get("aeration_power_kW", 0),
            "consumption_kWh_year": utilities.get("electricity_kWh_year", 0),
            "price_per_kWh": tea_result.get("tea_params", {}).get("electricity_price", 0.07),
            "cost_per_year": tea_result.get("opex", {}).get("annual_electricity", 0),
        },
        "heating_GJ_year": utilities.get("heating_GJ_year", 0),
        "cooling_GJ_year": utilities.get("cooling_GJ_year", 0),
        "currency": "USD",
    }


# =============================================================================
# Main entry point
# =============================================================================
if __name__ == "__main__":
    mcp.run()
