# QSDsan Engine MCP - API Reference

**Version:** 3.0.1

This document lists all 30 MCP tools exposed by the QSDsan Engine server.

---

## Testing the API

### Option 1: MCP Inspector (Recommended)

The official Anthropic MCP Inspector provides a web UI for testing:

```bash
npx @anthropic-ai/mcp-inspector
```

Then connect to the server using STDIO transport.

### Option 2: HTTP Server

Run the server with HTTP transport for REST-like testing:

```bash
# Start HTTP server (default: http://localhost:8000/mcp)
python server_http.py

# Custom port
python server_http.py --port 9000

# Allow external connections
python server_http.py --host 0.0.0.0
```

### Option 3: curl (with HTTP server running)

```bash
# List all available tools
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'

# Call get_version
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_version", "arguments": {}}, "id": 2}'

# Call list_templates
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "list_templates", "arguments": {}}, "id": 3}'

# Call get_model_components
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_model_components", "arguments": {"model_type": "ASM2d"}}, "id": 4}'
```

### Option 4: Python Client

```python
import httpx

async def call_tool(name: str, arguments: dict = None):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
                "id": 1
            }
        )
        return response.json()

# Example usage
import asyncio
result = asyncio.run(call_tool("get_version"))
print(result)
```

---

## Quick Reference

| # | Tool | Category | Description |
|---|------|----------|-------------|
| 0 | `get_version` | Discovery | Get server version info |
| 1 | `simulate_system` | Simulation | Run template-based simulation |
| 2 | `get_job_status` | Jobs | Check job progress |
| 3 | `get_job_results` | Jobs | Get completed job results |
| 4 | `list_templates` | Discovery | List available templates |
| 5 | `validate_state` | Utility | Validate PlantState |
| 6 | `convert_state` | Utility | Convert between models |
| 7 | `list_jobs` | Jobs | List all jobs |
| 8 | `terminate_job` | Jobs | Stop a running job |
| 9 | `get_timeseries_data` | Jobs | Get time series from job |
| 10 | `create_flowsheet_session` | Flowsheet | Start new session |
| 11 | `create_stream` | Flowsheet | Add stream to session |
| 12 | `create_unit` | Flowsheet | Add unit to session |
| 13 | `connect_units` | Flowsheet | Wire units together |
| 14 | `build_system` | Flowsheet | Compile to QSDsan System |
| 15 | `list_units` | Discovery | List available unit types |
| 16 | `simulate_built_system` | Simulation | Run compiled flowsheet |
| 17 | `get_flowsheet_session` | Session | Get session details |
| 18 | `list_flowsheet_sessions` | Session | List all sessions |
| 19 | `update_stream` | Mutation | Modify stream |
| 20 | `update_unit` | Mutation | Modify unit |
| 21 | `delete_stream` | Mutation | Remove stream |
| 22 | `delete_unit` | Mutation | Remove unit |
| 23 | `delete_connection` | Mutation | Remove connection |
| 24 | `clone_session` | Session | Duplicate session |
| 25 | `get_flowsheet_timeseries` | Results | Get stream trajectories |
| 26 | `delete_session` | Session | Delete session |
| 27 | `get_model_components` | Discovery | Get component IDs |
| 28 | `validate_flowsheet` | Flowsheet | Check flowsheet validity |
| 29 | `suggest_recycles` | Flowsheet | Detect recycle streams |
| 30 | `get_artifact` | Results | Get diagrams/reports |

---

## Tool Details

### 0. get_version

Get version information for the QSDsan Engine MCP server.

```python
get_version() -> Dict
```

**Parameters:** None

**Returns:**
```json
{
  "engine_version": "3.0.1",
  "qsdsan_version": "1.3.2",
  "biosteam_version": "2.42.0",
  "python_version": "3.12.0",
  "jobs_dir": "C:/path/to/qsdsan-engine-mcp/jobs"
}
```

---

### 1. simulate_system

Run QSDsan dynamic simulation using a flowsheet template. Returns immediately with job_id.

```python
simulate_system(
    template: str,                           # Required
    influent: Dict[str, Any],                # Required
    duration_days: float = 1.0,
    timestep_hours: Optional[float] = None,  # Aerobic only
    reactor_config: Optional[Dict] = None,
    parameters: Optional[Dict] = None        # ASM2d kinetics only
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `template` | str | Yes | Template name (see `list_templates`) |
| `influent` | Dict | Yes | PlantState dict with `model_type`, `flow_m3_d`, `temperature_K`, `concentrations` |
| `duration_days` | float | No | Simulation duration (default: 1.0) |
| `timestep_hours` | float | No | Output timestep (aerobic templates only) |
| `reactor_config` | Dict | No | Override reactor parameters |
| `parameters` | Dict | No | Kinetic parameter overrides (ASM2d only) |

**Example:**
```json
{
  "template": "anaerobic_cstr_madm1",
  "influent": {
    "model_type": "mADM1",
    "flow_m3_d": 1000,
    "temperature_K": 308.15,
    "concentrations": {
      "S_su": 0.5,
      "S_aa": 0.3,
      "X_ch": 2.0,
      "X_pr": 3.0,
      "X_li": 1.5
    }
  },
  "duration_days": 30
}
```

**Returns:**
```json
{
  "job_id": "a1b2c3d4",
  "status": "running",
  "template": "anaerobic_cstr_madm1",
  "duration_days": 30,
  "message": "Simulation started. Use get_job_status('a1b2c3d4') to monitor progress."
}
```

---

### 2. get_job_status

Check status of a background simulation job.

```python
get_job_status(job_id: str) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | str | Yes | Job identifier from simulate_system or convert_state |

**Returns:**
```json
{
  "job_id": "a1b2c3d4",
  "status": "running",
  "elapsed_time_seconds": 45.2,
  "started_at": 1705123456.789,
  "progress": {"percent": 65, "message": "Day 15/30"}
}
```

**Status values:** `starting`, `running`, `completed`, `failed`, `terminated`, `rejected`

---

### 3. get_job_results

Retrieve results from a completed simulation job.

```python
get_job_results(job_id: str) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `job_id` | str | Yes | Job identifier |

**Returns:** Full simulation results (time series excluded - use `get_timeseries_data`)

---

### 4. list_templates

List available flowsheet templates for simulation.

```python
list_templates() -> Dict
```

**Returns:**
```json
{
  "anaerobic": ["anaerobic_cstr_madm1"],
  "aerobic": ["mle_mbr_asm2d", "ao_mbr_asm2d", "a2o_mbr_asm2d"],
  "models": ["mADM1", "ASM2d", "ASM1", "mASM2d"]
}
```

---

### 5. validate_state

Validate PlantState against model requirements.

```python
validate_state(
    state: Dict[str, Any],           # Required
    model_type: str,                 # Required
    check_charge_balance: bool = True,
    check_mass_balance: bool = True
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `state` | Dict | Yes | PlantState dict |
| `model_type` | str | Yes | Target model ("mADM1", "ASM2d", etc.) |
| `check_charge_balance` | bool | No | Check electroneutrality |
| `check_mass_balance` | bool | No | Check COD/TKN/TP closure |

**Returns:**
```json
{
  "is_valid": true,
  "model_type": "mADM1",
  "errors": [],
  "warnings": ["Extra components (will be ignored): ['X_extra']"],
  "missing_components": [],
  "extra_components": ["X_extra"],
  "charge_balance": {"cation_meq_L": 10.5, "anion_meq_L": 10.3, "imbalance_meq_L": 0.2, "passed": true}
}
```

---

### 6. convert_state

Convert PlantState between model types (background job).

```python
convert_state(
    state: Dict[str, Any],  # Required
    from_model: str,        # Required
    to_model: str           # Required
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `state` | Dict | Yes | PlantState dict |
| `from_model` | str | Yes | Source model type |
| `to_model` | str | Yes | Target model type |

**Supported conversions:**
- ASM2d <-> mADM1
- mASM2d <-> mADM1

**Returns:**
```json
{
  "job_id": "b2c3d4e5",
  "status": "running",
  "conversion": "ASM2d -> mADM1",
  "message": "Conversion started. Use get_job_status('b2c3d4e5') to monitor."
}
```

---

### 7. list_jobs

List all background jobs with optional status filter.

```python
list_jobs(
    status_filter: Optional[str] = None,
    limit: int = 20
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `status_filter` | str | No | Filter by status ("running", "completed", "failed") |
| `limit` | int | No | Max jobs to return (default: 20) |

---

### 8. terminate_job

Terminate a running background job.

```python
terminate_job(job_id: str) -> Dict
```

---

### 9. get_timeseries_data

Get time series data from a completed simulation job.

```python
get_timeseries_data(job_id: str) -> Dict
```

---

### 10. create_flowsheet_session

Create a new flowsheet construction session.

```python
create_flowsheet_session(
    model_type: str,                    # Required
    session_id: Optional[str] = None
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `model_type` | str | Yes | Primary process model ("ASM2d", "mADM1") |
| `session_id` | str | No | Custom session ID (auto-generated if not provided) |

**Returns:**
```json
{
  "session_id": "abc123",
  "model_type": "ASM2d",
  "status": "created",
  "compatible_units": ["CSTR", "Splitter", "CompletelyMixedMBR", ...],
  "message": "Session created. Add streams with create_stream('abc123', ...)"
}
```

---

### 11. create_stream

Create a WasteStream in the flowsheet session.

```python
create_stream(
    session_id: str,                      # Required
    stream_id: str,                       # Required
    flow_m3_d: float,                     # Required
    concentrations: Dict[str, float],     # Required
    temperature_K: float = 293.15,
    concentration_units: str = "mg/L",    # or "kg/m3"
    stream_type: str = "influent",        # or "recycle", "intermediate"
    model_type: Optional[str] = None
) -> Dict
```

**Example:**
```json
{
  "session_id": "abc123",
  "stream_id": "influent",
  "flow_m3_d": 4000,
  "concentrations": {
    "S_F": 75,
    "S_A": 20,
    "S_NH4": 17,
    "S_PO4": 9,
    "X_S": 125,
    "X_H": 30
  },
  "concentration_units": "mg/L"
}
```

---

### 12. create_unit

Create a SanUnit in the flowsheet session.

```python
create_unit(
    session_id: str,                  # Required
    unit_type: str,                   # Required
    unit_id: str,                     # Required
    params: Dict[str, Any],           # Required
    inputs: List[str],                # Required
    outputs: Optional[List[str]] = None,
    model_type: Optional[str] = None
) -> Dict
```

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `session_id` | str | Yes | Session identifier |
| `unit_type` | str | Yes | Unit type from registry (see `list_units`) |
| `unit_id` | str | Yes | Unique unit identifier (e.g., "A1", "MBR") |
| `params` | Dict | Yes | Unit-specific parameters |
| `inputs` | List[str] | Yes | Input sources (stream IDs or pipe notation "A1-0") |
| `outputs` | List[str] | No | Output stream names |

**Example:**
```json
{
  "session_id": "abc123",
  "unit_type": "CSTR",
  "unit_id": "A1",
  "params": {"V_max": 1000, "aeration": 0},
  "inputs": ["influent", "RAS"]
}
```

---

### 13. connect_units

Add deferred connections between units (for recycles).

```python
connect_units(
    session_id: str,              # Required
    connections: List[Dict]       # Required
) -> Dict
```

**Connection formats:**
```json
{"from": "SP-0", "to": "1-A1"}
{"from": "SP-0-1-A1"}
```

---

### 14. build_system

Compile flowsheet session into a QSDsan System.

```python
build_system(
    session_id: str,                        # Required
    system_id: str,                         # Required
    unit_order: Optional[List[str]] = None,
    recycle_streams: Optional[List[str]] = None
) -> Dict
```

**Returns:**
```json
{
  "session_id": "abc123",
  "system_id": "custom_mle",
  "status": "compiled",
  "unit_order": ["A1", "A2", "O1", "O2", "MBR"],
  "recycle_edges": [["SP", "A1"]],
  "streams_created": ["influent", "RAS", "effluent", "WAS"],
  "units_created": ["A1", "A2", "O1", "O2", "SP", "MBR"],
  "warnings": [],
  "message": "System compiled successfully. Use simulate_built_system(session_id='abc123', ...) to simulate."
}
```

---

### 15. list_units

List available SanUnit types with their parameters.

```python
list_units(
    model_type: Optional[str] = None,
    category: Optional[str] = None
) -> Dict
```

**Categories:** `reactor`, `separator`, `junction`, `mixer`, `splitter`, `membrane`

---

### 16. simulate_built_system

Simulate a compiled flowsheet with comprehensive reporting.

```python
simulate_built_system(
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
    export_state_to: Optional[str] = None
) -> Dict
```

**Note:** Provide either `session_id` OR `system_id`, not both.

---

### 17. get_flowsheet_session

Get details of a flowsheet session.

```python
get_flowsheet_session(session_id: str) -> Dict
```

---

### 18. list_flowsheet_sessions

List all flowsheet sessions.

```python
list_flowsheet_sessions(
    status_filter: Optional[str] = None
) -> Dict
```

**Status values:** `building`, `compiled`, `failed`

---

### 19. update_stream

Update a stream in the flowsheet session (patch-style).

```python
update_stream(
    session_id: str,         # Required
    stream_id: str,          # Required
    updates: Dict[str, Any]  # Required
) -> Dict
```

**Valid update fields:** `flow_m3_d`, `temperature_K`, `concentrations` (merged), `stream_type`, `model_type`

---

### 20. update_unit

Update a unit in the flowsheet session (patch-style).

```python
update_unit(
    session_id: str,         # Required
    unit_id: str,            # Required
    updates: Dict[str, Any]  # Required
) -> Dict
```

**Valid update fields:** `params` (merged), `inputs`, `outputs`, `model_type`

---

### 21. delete_stream

Delete a stream from the flowsheet session.

```python
delete_stream(
    session_id: str,       # Required
    stream_id: str,        # Required
    force: bool = False
) -> Dict
```

---

### 22. delete_unit

Delete a unit from the flowsheet session.

```python
delete_unit(
    session_id: str,  # Required
    unit_id: str      # Required
) -> Dict
```

---

### 23. delete_connection

Delete a specific connection from the flowsheet session.

```python
delete_connection(
    session_id: str,               # Required
    from_port: str,                # Required
    to_port: Optional[str] = None
) -> Dict
```

---

### 24. clone_session

Clone a flowsheet session for experimentation.

```python
clone_session(
    source_session_id: str,              # Required
    new_session_id: Optional[str] = None
) -> Dict
```

---

### 25. get_flowsheet_timeseries

Get time-series data from a completed flowsheet simulation.

```python
get_flowsheet_timeseries(
    job_id: str,                            # Required
    stream_ids: Optional[List[str]] = None,
    components: Optional[List[str]] = None,
    downsample_factor: int = 1
) -> Dict
```

---

### 26. delete_session

Delete a flowsheet session and all its files.

```python
delete_session(session_id: str) -> Dict
```

---

### 27. get_model_components

Get component IDs and metadata for a process model.

```python
get_model_components(
    model_type: str,                   # Required
    include_typical_values: bool = True
) -> Dict
```

**Returns:**
```json
{
  "model_type": "ASM2d",
  "n_components": 19,
  "concentration_units": "mg/L",
  "components": [
    {"id": "S_O2", "name": "Dissolved oxygen", "category": "soluble", "typical_domestic_mg_L": 0},
    {"id": "S_F", "name": "Fermentable substrate", "category": "soluble", "typical_domestic_mg_L": 75},
    ...
  ],
  "categories": {
    "soluble": ["S_O2", "S_F", "S_A", ...],
    "particulate": ["X_I", "X_S", ...],
    "biomass": ["X_H", "X_PAO", "X_AUT"]
  }
}
```

---

### 28. validate_flowsheet

Validate a flowsheet without compiling it.

```python
validate_flowsheet(session_id: str) -> Dict
```

**Returns:**
```json
{
  "session_id": "abc123",
  "is_valid": true,
  "errors": [],
  "warnings": [],
  "detected_recycles": [{"cycle_path": ["A1", "SP", "A1"]}],
  "n_units": 5,
  "n_streams": 2,
  "n_connections": 3
}
```

---

### 29. suggest_recycles

Detect potential recycle streams in a flowsheet.

```python
suggest_recycles(session_id: str) -> Dict
```

**Returns:**
```json
{
  "session_id": "abc123",
  "n_cycles_detected": 1,
  "detected_cycles": [["A1", "O1", "SP", "A1"]],
  "suggestions": [
    {
      "cycle_path": ["A1", "O1", "SP", "A1"],
      "suggested_recycle": {"from": "SP-0", "to": "A1-1", "stream_id": "recycle_SP_A1"},
      "recycle_type": "return_activated_sludge",
      "confidence": "medium"
    }
  ],
  "topology": {
    "sources": ["influent"],
    "sinks": ["MBR"]
  }
}
```

---

### 30. get_artifact

Get simulation artifact content directly.

```python
get_artifact(
    job_id: str,                   # Required
    artifact_type: str,            # Required
    format: Optional[str] = None
) -> Dict
```

**Artifact types:** `diagram`, `report`, `timeseries`, `results`

**Returns:**
```json
{
  "job_id": "a1b2c3d4",
  "artifact_type": "diagram",
  "format": "svg",
  "content": "<?xml version=\"1.0\"?><svg>...</svg>",
  "path": "C:/path/to/jobs/a1b2c3d4/flowsheet.svg",
  "size_bytes": 12345
}
```

---

## PlantState Schema

All tools that accept `state` or `influent` parameters expect this structure:

```json
{
  "model_type": "ASM2d",
  "flow_m3_d": 4000,
  "temperature_K": 293.15,
  "concentrations": {
    "S_F": 75,
    "S_A": 20,
    "S_I": 30,
    "S_NH4": 17,
    "S_NO3": 0,
    "S_PO4": 9,
    "S_ALK": 300,
    "X_I": 50,
    "X_S": 125,
    "X_H": 30,
    "X_PAO": 0,
    "X_PP": 0,
    "X_PHA": 0,
    "X_AUT": 0,
    "X_MeOH": 0,
    "X_MeP": 0
  }
}
```

**Concentration units:**
- ASM2d, ASM1, mASM2d: mg/L
- mADM1, ADM1: kg/m³

---

## Typical Workflow

### 1. Template-Based Simulation (Simple)

```
list_templates()
  -> simulate_system(template, influent, duration_days)
  -> get_job_status(job_id)  [poll until completed]
  -> get_job_results(job_id)
```

### 2. Custom Flowsheet (Advanced)

```
create_flowsheet_session(model_type)
  -> create_stream(session_id, ...)       [repeat for each stream]
  -> create_unit(session_id, ...)         [repeat for each unit]
  -> connect_units(session_id, ...)       [for recycles]
  -> validate_flowsheet(session_id)       [optional check]
  -> build_system(session_id, system_id)
  -> simulate_built_system(session_id, ...)
  -> get_job_status(job_id)               [poll until completed]
  -> get_job_results(job_id)
  -> get_artifact(job_id, "diagram")      [get flowsheet SVG]
```

---

## Error Handling

All tools return errors in a consistent format:

```json
{
  "error": "Description of what went wrong"
}
```

Check for the presence of an `error` key to detect failures.
