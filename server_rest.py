#!/usr/bin/env python3
"""
QSDsan Engine - Complete REST API for Testing

This provides a REST wrapper around ALL 31 MCP tools for easy testing
with curl, Postman, or any HTTP client. No MCP session management required.

Usage:
    python server_rest.py                    # Default: http://localhost:8000
    python server_rest.py --port 9000        # Custom port

Testing:
    # Get version
    curl http://localhost:8000/api/get_version

    # List templates
    curl http://localhost:8000/api/list_templates

    # Get model components
    curl "http://localhost:8000/api/get_model_components?model_type=ASM2d"

    # List units
    curl "http://localhost:8000/api/list_units?model_type=ASM2d"

    # POST with JSON body (for complex tools)
    curl -X POST http://localhost:8000/api/simulate_system \
        -H "Content-Type: application/json" \
        -d '{"template": "anaerobic_cstr_madm1", "influent": {...}, "duration_days": 30}'

Note: This is for development/testing only. For production MCP usage, use server.py with Claude Desktop.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import uvicorn

app = FastAPI(
    title="QSDsan Engine REST API",
    description="Complete REST wrapper for all 31 QSDsan Engine MCP tools",
    version="3.0.1",
)


# Import after app creation to avoid circular imports
def get_tools():
    """Lazy import of server module to get tool functions."""
    from server import (
        # Discovery (4)
        get_version,
        list_templates,
        list_units,
        get_model_components,
        # Simulation (2)
        simulate_system,
        simulate_built_system,
        # Jobs (5)
        list_jobs,
        get_job_status,
        get_job_results,
        terminate_job,
        get_timeseries_data,
        # Utility (2)
        validate_state,
        convert_state,
        # Flowsheet Construction (5)
        create_flowsheet_session,
        create_stream,
        create_unit,
        connect_units,
        build_system,
        # Session Management (4)
        get_flowsheet_session,
        list_flowsheet_sessions,
        clone_session,
        delete_session,
        # Mutation (5)
        update_stream,
        update_unit,
        delete_stream,
        delete_unit,
        delete_connection,
        # Flowsheet Analysis (2)
        validate_flowsheet,
        suggest_recycles,
        # Results (2)
        get_flowsheet_timeseries,
        get_artifact,
    )
    return {
        # Discovery
        "get_version": get_version,
        "list_templates": list_templates,
        "list_units": list_units,
        "get_model_components": get_model_components,
        # Simulation
        "simulate_system": simulate_system,
        "simulate_built_system": simulate_built_system,
        # Jobs
        "list_jobs": list_jobs,
        "get_job_status": get_job_status,
        "get_job_results": get_job_results,
        "terminate_job": terminate_job,
        "get_timeseries_data": get_timeseries_data,
        # Utility
        "validate_state": validate_state,
        "convert_state": convert_state,
        # Flowsheet Construction
        "create_flowsheet_session": create_flowsheet_session,
        "create_stream": create_stream,
        "create_unit": create_unit,
        "connect_units": connect_units,
        "build_system": build_system,
        # Session Management
        "get_flowsheet_session": get_flowsheet_session,
        "list_flowsheet_sessions": list_flowsheet_sessions,
        "clone_session": clone_session,
        "delete_session": delete_session,
        # Mutation
        "update_stream": update_stream,
        "update_unit": update_unit,
        "delete_stream": delete_stream,
        "delete_unit": delete_unit,
        "delete_connection": delete_connection,
        # Flowsheet Analysis
        "validate_flowsheet": validate_flowsheet,
        "suggest_recycles": suggest_recycles,
        # Results
        "get_flowsheet_timeseries": get_flowsheet_timeseries,
        "get_artifact": get_artifact,
    }


# Cache for tools
_tools = None

def tools():
    global _tools
    if _tools is None:
        _tools = get_tools()
    return _tools


# =============================================================================
# Root endpoint
# =============================================================================

@app.get("/")
async def root():
    """API root - list available endpoints."""
    return {
        "name": "QSDsan Engine REST API",
        "version": "3.0.1",
        "total_endpoints": 31,
        "categories": {
            "discovery": ["get_version", "list_templates", "list_units", "get_model_components"],
            "simulation": ["simulate_system", "simulate_built_system"],
            "jobs": ["list_jobs", "get_job_status", "get_job_results", "terminate_job", "get_timeseries_data"],
            "utility": ["validate_state", "convert_state"],
            "flowsheet": ["create_flowsheet_session", "create_stream", "create_unit", "connect_units", "build_system"],
            "session": ["get_flowsheet_session", "list_flowsheet_sessions", "clone_session", "delete_session"],
            "mutation": ["update_stream", "update_unit", "delete_stream", "delete_unit", "delete_connection"],
            "analysis": ["validate_flowsheet", "suggest_recycles"],
            "results": ["get_flowsheet_timeseries", "get_artifact"],
        },
        "docs": "/docs",
    }


# =============================================================================
# Discovery Endpoints (GET)
# =============================================================================

@app.get("/api/get_version", tags=["Discovery"])
async def api_get_version():
    """Get server version information."""
    return await tools()["get_version"]()


@app.get("/api/list_templates", tags=["Discovery"])
async def api_list_templates():
    """List available flowsheet templates."""
    return await tools()["list_templates"]()


@app.get("/api/list_units", tags=["Discovery"])
async def api_list_units(
    model_type: Optional[str] = Query(None, description="Filter by model type (ASM2d, mADM1, etc.)"),
    category: Optional[str] = Query(None, description="Filter by category (reactor, separator, etc.)"),
):
    """List available SanUnit types."""
    return await tools()["list_units"](model_type=model_type, category=category)


@app.get("/api/get_model_components", tags=["Discovery"])
async def api_get_model_components(
    model_type: str = Query(..., description="Model type (ASM2d, mADM1, ASM1, etc.)"),
    include_typical_values: bool = Query(True, description="Include typical domestic wastewater values"),
):
    """Get component IDs and metadata for a process model."""
    return await tools()["get_model_components"](
        model_type=model_type,
        include_typical_values=include_typical_values,
    )


# =============================================================================
# Jobs Endpoints (GET)
# =============================================================================

@app.get("/api/list_jobs", tags=["Jobs"])
async def api_list_jobs(
    status_filter: Optional[str] = Query(None, description="Filter by status (running, completed, failed)"),
    limit: int = Query(20, description="Max jobs to return"),
):
    """List all background jobs."""
    return await tools()["list_jobs"](status_filter=status_filter, limit=limit)


@app.get("/api/get_job_status", tags=["Jobs"])
async def api_get_job_status(
    job_id: str = Query(..., description="Job identifier"),
):
    """Get status of a background job."""
    return await tools()["get_job_status"](job_id=job_id)


@app.get("/api/get_job_results", tags=["Jobs"])
async def api_get_job_results(
    job_id: str = Query(..., description="Job identifier"),
):
    """Get results from a completed job."""
    return await tools()["get_job_results"](job_id=job_id)


@app.get("/api/get_timeseries_data", tags=["Jobs"])
async def api_get_timeseries_data(
    job_id: str = Query(..., description="Job identifier"),
):
    """Get time series data from a completed simulation job."""
    return await tools()["get_timeseries_data"](job_id=job_id)


@app.post("/api/terminate_job", tags=["Jobs"])
async def api_terminate_job(
    job_id: str = Query(..., description="Job identifier"),
):
    """Terminate a running background job."""
    return await tools()["terminate_job"](job_id=job_id)


# =============================================================================
# Session Endpoints (GET)
# =============================================================================

@app.get("/api/list_flowsheet_sessions", tags=["Session"])
async def api_list_flowsheet_sessions(
    status_filter: Optional[str] = Query(None, description="Filter by status (building, compiled, failed)"),
):
    """List all flowsheet sessions."""
    return await tools()["list_flowsheet_sessions"](status_filter=status_filter)


@app.get("/api/get_flowsheet_session", tags=["Session"])
async def api_get_flowsheet_session(
    session_id: str = Query(..., description="Session identifier"),
):
    """Get details of a flowsheet session."""
    return await tools()["get_flowsheet_session"](session_id=session_id)


@app.post("/api/delete_session", tags=["Session"])
async def api_delete_session(
    session_id: str = Query(..., description="Session identifier"),
):
    """Delete a flowsheet session and all its files."""
    return await tools()["delete_session"](session_id=session_id)


# =============================================================================
# Flowsheet Analysis Endpoints (GET)
# =============================================================================

@app.get("/api/validate_flowsheet", tags=["Analysis"])
async def api_validate_flowsheet(
    session_id: str = Query(..., description="Session identifier"),
):
    """Validate a flowsheet without compiling it."""
    return await tools()["validate_flowsheet"](session_id=session_id)


@app.get("/api/suggest_recycles", tags=["Analysis"])
async def api_suggest_recycles(
    session_id: str = Query(..., description="Session identifier"),
):
    """Detect potential recycle streams in a flowsheet."""
    return await tools()["suggest_recycles"](session_id=session_id)


# =============================================================================
# Results Endpoints (GET)
# =============================================================================

@app.get("/api/get_flowsheet_timeseries", tags=["Results"])
async def api_get_flowsheet_timeseries(
    job_id: str = Query(..., description="Job identifier"),
    stream_ids: Optional[str] = Query(None, description="Comma-separated stream IDs to include"),
    components: Optional[str] = Query(None, description="Comma-separated component IDs to include"),
    downsample_factor: int = Query(1, description="Downsample factor (1 = no downsampling)"),
):
    """Get time-series data from a completed flowsheet simulation."""
    stream_list = stream_ids.split(",") if stream_ids else None
    comp_list = components.split(",") if components else None
    return await tools()["get_flowsheet_timeseries"](
        job_id=job_id,
        stream_ids=stream_list,
        components=comp_list,
        downsample_factor=downsample_factor,
    )


@app.get("/api/get_artifact", tags=["Results"])
async def api_get_artifact(
    job_id: str = Query(..., description="Job identifier"),
    artifact_type: str = Query(..., description="Artifact type (diagram, report, timeseries, results)"),
    format: Optional[str] = Query(None, description="Output format"),
):
    """Get simulation artifact content directly."""
    return await tools()["get_artifact"](
        job_id=job_id,
        artifact_type=artifact_type,
        format=format,
    )


# =============================================================================
# POST Endpoints - Utility
# =============================================================================

class ValidateStateRequest(BaseModel):
    state: Dict[str, Any]
    model_type: str
    check_charge_balance: bool = True
    check_mass_balance: bool = True


@app.post("/api/validate_state", tags=["Utility"])
async def api_validate_state(request: ValidateStateRequest):
    """Validate PlantState against model requirements."""
    return await tools()["validate_state"](
        state=request.state,
        model_type=request.model_type,
        check_charge_balance=request.check_charge_balance,
        check_mass_balance=request.check_mass_balance,
    )


class ConvertStateRequest(BaseModel):
    state: Dict[str, Any]
    from_model: str
    to_model: str


@app.post("/api/convert_state", tags=["Utility"])
async def api_convert_state(request: ConvertStateRequest):
    """Convert PlantState between model types (background job)."""
    return await tools()["convert_state"](
        state=request.state,
        from_model=request.from_model,
        to_model=request.to_model,
    )


# =============================================================================
# POST Endpoints - Simulation
# =============================================================================

class SimulateSystemRequest(BaseModel):
    template: str
    influent: Dict[str, Any]
    duration_days: float = 1.0
    timestep_hours: Optional[float] = None
    reactor_config: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None


@app.post("/api/simulate_system", tags=["Simulation"])
async def api_simulate_system(request: SimulateSystemRequest):
    """Run QSDsan dynamic simulation using a flowsheet template (background job)."""
    return await tools()["simulate_system"](
        template=request.template,
        influent=request.influent,
        duration_days=request.duration_days,
        timestep_hours=request.timestep_hours,
        reactor_config=request.reactor_config,
        parameters=request.parameters,
    )


class SimulateBuiltSystemRequest(BaseModel):
    session_id: Optional[str] = None
    system_id: Optional[str] = None
    duration_days: float = 1.0
    timestep_hours: float = 1.0
    method: str = "RK23"
    t_eval: Optional[List[float]] = None
    track: Optional[List[str]] = None
    effluent_stream_ids: Optional[List[str]] = None
    biogas_stream_ids: Optional[List[str]] = None
    report: bool = True
    diagram: bool = True
    include_components: bool = False
    export_state_to: Optional[str] = None


@app.post("/api/simulate_built_system", tags=["Simulation"])
async def api_simulate_built_system(request: SimulateBuiltSystemRequest):
    """Simulate a compiled flowsheet with comprehensive reporting (background job)."""
    return await tools()["simulate_built_system"](
        session_id=request.session_id,
        system_id=request.system_id,
        duration_days=request.duration_days,
        timestep_hours=request.timestep_hours,
        method=request.method,
        t_eval=request.t_eval,
        track=request.track,
        effluent_stream_ids=request.effluent_stream_ids,
        biogas_stream_ids=request.biogas_stream_ids,
        report=request.report,
        diagram=request.diagram,
        include_components=request.include_components,
        export_state_to=request.export_state_to,
    )


# =============================================================================
# POST Endpoints - Flowsheet Construction
# =============================================================================

class CreateSessionRequest(BaseModel):
    model_type: str
    session_id: Optional[str] = None


@app.post("/api/create_flowsheet_session", tags=["Flowsheet"])
async def api_create_flowsheet_session(request: CreateSessionRequest):
    """Create a new flowsheet construction session."""
    return await tools()["create_flowsheet_session"](
        model_type=request.model_type,
        session_id=request.session_id,
    )


class CreateStreamRequest(BaseModel):
    session_id: str
    stream_id: str
    flow_m3_d: float
    concentrations: Dict[str, float]
    temperature_K: float = 293.15
    concentration_units: str = "mg/L"
    stream_type: str = "influent"
    model_type: Optional[str] = None


@app.post("/api/create_stream", tags=["Flowsheet"])
async def api_create_stream(request: CreateStreamRequest):
    """Create a WasteStream in the flowsheet session."""
    return await tools()["create_stream"](
        session_id=request.session_id,
        stream_id=request.stream_id,
        flow_m3_d=request.flow_m3_d,
        concentrations=request.concentrations,
        temperature_K=request.temperature_K,
        concentration_units=request.concentration_units,
        stream_type=request.stream_type,
        model_type=request.model_type,
    )


class CreateUnitRequest(BaseModel):
    session_id: str
    unit_type: str
    unit_id: str
    params: Dict[str, Any]
    inputs: List[str]
    outputs: Optional[List[str]] = None
    model_type: Optional[str] = None


@app.post("/api/create_unit", tags=["Flowsheet"])
async def api_create_unit(request: CreateUnitRequest):
    """Create a SanUnit in the flowsheet session."""
    return await tools()["create_unit"](
        session_id=request.session_id,
        unit_type=request.unit_type,
        unit_id=request.unit_id,
        params=request.params,
        inputs=request.inputs,
        outputs=request.outputs,
        model_type=request.model_type,
    )


class ConnectUnitsRequest(BaseModel):
    session_id: str
    connections: List[Dict[str, str]]


@app.post("/api/connect_units", tags=["Flowsheet"])
async def api_connect_units(request: ConnectUnitsRequest):
    """Add deferred connections between units (for recycles)."""
    return await tools()["connect_units"](
        session_id=request.session_id,
        connections=request.connections,
    )


class BuildSystemRequest(BaseModel):
    session_id: str
    system_id: str
    unit_order: Optional[List[str]] = None
    recycle_streams: Optional[List[str]] = None


@app.post("/api/build_system", tags=["Flowsheet"])
async def api_build_system(request: BuildSystemRequest):
    """Compile flowsheet session into a QSDsan System."""
    return await tools()["build_system"](
        session_id=request.session_id,
        system_id=request.system_id,
        unit_order=request.unit_order,
        recycle_streams=request.recycle_streams,
    )


# =============================================================================
# POST Endpoints - Session Management
# =============================================================================

class CloneSessionRequest(BaseModel):
    source_session_id: str
    new_session_id: Optional[str] = None


@app.post("/api/clone_session", tags=["Session"])
async def api_clone_session(request: CloneSessionRequest):
    """Clone a flowsheet session for experimentation."""
    return await tools()["clone_session"](
        source_session_id=request.source_session_id,
        new_session_id=request.new_session_id,
    )


# =============================================================================
# POST Endpoints - Mutation
# =============================================================================

class UpdateStreamRequest(BaseModel):
    session_id: str
    stream_id: str
    updates: Dict[str, Any]


@app.post("/api/update_stream", tags=["Mutation"])
async def api_update_stream(request: UpdateStreamRequest):
    """Update a stream in the flowsheet session (patch-style)."""
    return await tools()["update_stream"](
        session_id=request.session_id,
        stream_id=request.stream_id,
        updates=request.updates,
    )


class UpdateUnitRequest(BaseModel):
    session_id: str
    unit_id: str
    updates: Dict[str, Any]


@app.post("/api/update_unit", tags=["Mutation"])
async def api_update_unit(request: UpdateUnitRequest):
    """Update a unit in the flowsheet session (patch-style)."""
    return await tools()["update_unit"](
        session_id=request.session_id,
        unit_id=request.unit_id,
        updates=request.updates,
    )


class DeleteStreamRequest(BaseModel):
    session_id: str
    stream_id: str
    force: bool = False


@app.post("/api/delete_stream", tags=["Mutation"])
async def api_delete_stream(request: DeleteStreamRequest):
    """Delete a stream from the flowsheet session."""
    return await tools()["delete_stream"](
        session_id=request.session_id,
        stream_id=request.stream_id,
        force=request.force,
    )


class DeleteUnitRequest(BaseModel):
    session_id: str
    unit_id: str


@app.post("/api/delete_unit", tags=["Mutation"])
async def api_delete_unit(request: DeleteUnitRequest):
    """Delete a unit from the flowsheet session."""
    return await tools()["delete_unit"](
        session_id=request.session_id,
        unit_id=request.unit_id,
    )


class DeleteConnectionRequest(BaseModel):
    session_id: str
    from_port: str
    to_port: Optional[str] = None


@app.post("/api/delete_connection", tags=["Mutation"])
async def api_delete_connection(request: DeleteConnectionRequest):
    """Delete a specific connection from the flowsheet session."""
    return await tools()["delete_connection"](
        session_id=request.session_id,
        from_port=request.from_port,
        to_port=request.to_port,
    )


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Run QSDsan Engine REST API server (all 31 endpoints)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)"
    )

    args = parser.parse_args()

    print(f"Starting QSDsan Engine REST API Server")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"  Docs: http://{args.host}:{args.port}/docs")
    print(f"  Endpoints: 31 (all MCP tools)")
    print(f"  Press Ctrl+C to stop")
    print()

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
