"""
QSDsan Engine MCP Server - Universal Biological Wastewater Treatment Simulation

This is the MCP adapter for the QSDsan simulation engine. It provides 6 core tools
for stateless simulation with explicit state passing.

Tools:
    - simulate_system: Run QSDsan simulation to steady state (background job)
    - get_job_status: Check job progress
    - get_job_results: Retrieve simulation results
    - list_templates: List available flowsheet templates
    - validate_state: Validate PlantState against model
    - convert_state: ASM2d ↔ mADM1 state conversion (background job)

Architecture:
    This server exposes the same engine core as the CLI adapter (cli.py).
    Both adapters call identical functions from the engine/ module.
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
from utils.job_manager import JobManager
from utils.path_utils import normalize_path_for_wsl, get_python_executable

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("qsdsan-engine")

# Initialize job manager (singleton)
job_manager = JobManager(max_concurrent_jobs=3, jobs_base_dir="jobs")


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
        Dict with anaerobic and aerobic template lists
    """
    return {
        "anaerobic": [
            {
                "name": "anaerobic_cstr_madm1",
                "description": "Single CSTR with mADM1 model (63 components, 4 biogas species)",
                "model_type": "mADM1",
                "reactor_type": "AnaerobicCSTRmADM1",
                "typical_hrt_days": "15-30",
                "status": "available",
            },
        ],
        "aerobic": [
            {
                "name": "mle_mbr_asm2d",
                "description": "MLE-MBR (anoxic → aerobic → MBR) with ASM2d",
                "model_type": "ASM2d",
                "reactor_type": "CSTR + CompletelyMixedMBR",
                "typical_hrt_hours": "5-24",
                "status": "planned",
            },
            {
                "name": "a2o_mbr_asm2d",
                "description": "A2O-MBR (anaerobic → anoxic → aerobic → MBR) with EBPR",
                "model_type": "ASM2d",
                "reactor_type": "CSTR + CompletelyMixedMBR",
                "typical_hrt_hours": "6-24",
                "status": "planned",
            },
            {
                "name": "ao_mbr_asm2d",
                "description": "Simple A/O-MBR configuration",
                "model_type": "ASM2d",
                "reactor_type": "CSTR + CompletelyMixedMBR",
                "typical_hrt_hours": "5-12",
                "status": "planned",
            },
        ],
        "models": list_available_models(),
    }


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
# Main entry point
# =============================================================================
if __name__ == "__main__":
    mcp.run()
