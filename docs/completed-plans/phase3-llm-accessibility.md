# QSDsan Engine MCP - Phase 3: LLM Accessibility Enhancement Plan

## Executive Summary

This plan addresses 13 validated issues to make the MCP server reliably usable by autonomous agents. The feedback has been vetted against the codebase - all claims are confirmed accurate. The implementation is organized into 4 phases with backwards compatibility preserved.

**Scope:** 11 new MCP tools, 11 modified tools, ~40 new tests, targeting 100% tool surface coverage.

---

## Feedback Validation Summary

| Issue | Feedback Claim | Verified | Notes |
|-------|---------------|----------|-------|
| #1 JSON strings | Tools use `params: str` requiring double JSON encoding | **YES** | `server.py:594-602` confirms `params: str`, `inputs: str`, `outputs: str` |
| #2 No mutation | Cannot update/delete streams/units/connections | **YES** | `FlowsheetSessionManager` has only `add_*` methods, no `update_*` or `delete_*` |
| #3 Shallow introspection | `get_flowsheet_session()` returns only stream IDs | **YES** | `get_session_summary()` returns `list(session.streams.keys())` |
| #4 Unit inconsistency | Anaerobic=kg/m3, Flowsheet=mg/L | **YES** | `simulate_madm1.py:82` says "kg/m3", `flowsheet_builder.py:360` uses "mg/L" |
| #5 Silent warnings | Warnings logged, not returned | **YES** | `flowsheet_builder.py:358` uses `logger.warning()` only |
| #6 Semaphore bug | Released after spawn, not completion | **YES** | `job_manager.py:207-222` - `asyncio.create_task()` outside semaphore hold |
| #7 No flowsheet timeseries | Template saves trajectories, flowsheet doesn't | **YES** | `flowsheet_builder.py` has no time_series extraction |
| #8 No component discovery | Can't get valid component IDs | **YES** | No `get_model_components` tool exists |
| #9 No pre-validation | Must compile to find errors | **YES** | No `validate_flowsheet` tool exists |
| #10 No recycle suggestion | Manual recycle identification | **YES** | `suggest_recycles` not implemented |
| #11 Sparse results | No per-stream summaries or KPIs | **PARTIAL** | Some KPIs exist but mass balance checks limited |
| #12 No artifact retrieval | Returns paths, not content | **YES** | Tools return `diagram_path` but no content getter |
| #13 No deterministic metadata | Missing solver/version info | **YES** | Results lack `qsdsan_version`, solver settings |

---

## Artifacts Produced for Consumption

The system produces artifacts consumable by downstream steps:

### 1. Simulation Results (JSON)
- **Location:** `jobs/{job_id}/results.json`
- **Contents:** Stream compositions, unit states, KPIs, removal efficiencies
- **Consumers:** Analysis agents, dashboard tools, human reviewers

### 2. Quarto Reports (QMD)
- **Location:** `jobs/{job_id}/report.qmd`
- **Templates:** `reports/templates/anaerobic_report.qmd`, `aerobic_report.qmd`
- **Contents:** Professional engineering report with KPI dashboards, diagnostic panels, stream tables
- **Consumers:** Quarto CLI for rendering to HTML/PDF, human reviewers

### 3. Flowsheet Diagrams (SVG)
- **Location:** `jobs/{job_id}/flowsheet.svg`
- **Contents:** Visual flowsheet with unit operations and stream labels
- **Consumers:** Reports, visual verification, human reviewers

### 4. Session State (JSON)
- **Location:** `jobs/flowsheets/{session_id}/session.json`
- **Contents:** Complete flowsheet definition (streams, units, connections, params)
- **Consumers:** Session reconstruction, cloning, export to standalone scripts

### 5. Time-Series Data (JSON/NPY) [After Phase 3]
- **Location:** `jobs/{job_id}/timeseries.json` or `.npy`
- **Contents:** Component trajectories for tracked streams
- **Consumers:** Plotting tools, process control analysis, human reviewers

### 6. Exportable Python Scripts [Potential]
- Users can export session state to standalone Python scripts
- `export_state_to` parameter already exists but underutilized
- **Enhancement:** Add `export_flowsheet_script(session_id)` tool

---

## Implementation Plan

### Phase 3A: Critical API Fixes (Tier 0)

#### 3A.1: Native Type Parameters (BREAKING CHANGE - No Backwards Compat)

**Files:**
- `server.py` (9 tool signatures)
- `cli.py` (matching commands)

**Changes:**
```python
# Before
async def create_stream(..., concentrations: str, ...) -> Dict[str, Any]:
    conc_data = json.loads(concentrations)

# After - Native types only (no Union, no string parsing)
async def create_stream(..., concentrations: Dict[str, float], ...) -> Dict[str, Any]:
    # Direct use, no parsing needed
```

**Benefits of dropping backwards compat:**
- No dual-mode parsing logic needed
- No `parse_json_or_native()` helper required
- Cleaner type signatures
- FastMCP handles native types directly

**Affected Tools (simplified signatures):**
- `simulate_system`: `influent: Dict`, `reactor_config: Optional[Dict]`, `parameters: Optional[Dict]`
- `validate_state`: `state: Dict`
- `convert_state`: `state: Dict`
- `create_stream`: `concentrations: Dict[str, float]`
- `create_unit`: `params: Dict[str, Any]`, `inputs: List[str]`, `outputs: Optional[List[str]]`
- `connect_units`: `connections: List[Dict[str, str]]`
- `build_system`: `unit_order: Optional[List[str]]`, `recycle_streams: Optional[List[str]]`
- `simulate_built_system`: `t_eval: Optional[List[float]]`, `track: Optional[List[str]]`, etc.

---

#### 3A.2: Session Mutation API

**Files:**
- `utils/flowsheet_session.py` - Add 7 methods
- `server.py` - Add 7 MCP tools
- `cli.py` - Add CLI commands

**New Tools:**

| Tool | Purpose |
|------|---------|
| `update_stream(session_id, stream_id, updates)` | Patch stream properties |
| `update_unit(session_id, unit_id, updates)` | Patch unit parameters |
| `delete_stream(session_id, stream_id)` | Remove stream |
| `delete_unit(session_id, unit_id)` | Remove unit and connections |
| `delete_connection(session_id, from_port, to_port)` | Remove specific connection |
| `clone_session(source_session_id, new_session_id)` | Fork session for experimentation |
| `delete_session(session_id)` | Remove entire session (already exists internally, expose via MCP) |

**Session Manager Additions:**
```python
def update_stream(self, session_id: str, stream_id: str, updates: Dict) -> Dict:
    """Patch stream. Resets status to 'building' if was 'compiled'."""
    session = self.get_session(session_id)
    if stream_id not in session.streams:
        raise ValueError(f"Stream '{stream_id}' not found")

    cfg = session.streams[stream_id]
    for key, value in updates.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    # Reset to building if was compiled
    if session.status == "compiled":
        session.status = "building"

    self._save_session(session)
    return {"stream_id": stream_id, "status": "updated", "session_status": session.status}
```

---

#### 3A.3: Deep Session Introspection

**Files:**
- `utils/flowsheet_session.py` - Enhance `get_session_summary()`
- `server.py` - Update `get_flowsheet_session` response

**Enhanced Response:**
```python
{
    "session_id": "abc123",
    "status": "building",
    "streams": {
        "influent": {
            "flow_m3_d": 4000,
            "temperature_K": 293.15,
            "concentrations": {"S_F": 75, "S_A": 20, "S_NH4": 17},
            "concentration_units": "mg/L",
            "stream_type": "influent"
        }
    },
    "units": {
        "A1": {
            "unit_type": "CSTR",
            "params": {"V_max": 1000},
            "inputs": ["influent"],
            "outputs": null,
            "model_type": "ASM2d"
        }
    },
    "connections": [
        {"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}
    ]
}
```

---

#### 3A.4: Concentration Unit Normalization

**Files:**
- `utils/flowsheet_session.py` - Add `concentration_units` field to `StreamConfig`
- `utils/flowsheet_builder.py` - Add conversion logic
- `server.py` - Add parameter to `create_stream`

**Convention:** User-facing default is **mg/L** (practitioner standard). Internal conversion to kg/m3 for mADM1 model.

```python
@dataclass
class StreamConfig:
    stream_id: str
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
    concentration_units: str = "mg/L"  # NEW: "mg/L" (default) or "kg/m3"
    stream_type: str = "influent"
    model_type: Optional[str] = None

# In flowsheet_builder.py
def _create_waste_stream(stream_id, config, cmps):
    if config.concentration_units == "kg/m3":
        # Convert kg/m3 to mg/L for QSDsan
        concentrations = {k: v * 1000 for k, v in config.concentrations.items()}
    else:
        concentrations = config.concentrations
    # ... create WasteStream with mg/L
```

---

#### 3A.5: Surface Validation Warnings

**Files:**
- `server.py` - Add `warnings` field to all tool responses
- `utils/flowsheet_builder.py` - Return warnings from `_create_waste_stream`

**Pattern:**
```python
async def create_stream(...) -> Dict[str, Any]:
    warnings = []

    # Validate component IDs
    unknown = validate_components(conc_data.keys(), model_type)
    if unknown:
        warnings.append(f"Unknown components (ignored): {unknown}")

    result = session_manager.add_stream(session_id, config)
    result["warnings"] = warnings
    return result
```

---

#### 3A.6: JobManager Concurrency Fix

**Files:**
- `utils/job_manager.py`

**Problem:** Semaphore released after ~100ms (subprocess spawn), not after job completion.

**Fix:** Use counter + release-on-completion pattern.

```python
class JobManager:
    def __init__(self, max_concurrent_jobs: int = 3, ...):
        self.max_concurrent_jobs = max_concurrent_jobs
        self._running_count = 0
        self._running_lock = asyncio.Lock()

    async def execute(self, cmd, ...) -> dict:
        async with self._running_lock:
            if self._running_count >= self.max_concurrent_jobs:
                return {"error": f"Max concurrent jobs ({self.max_concurrent_jobs}) reached. Use get_job_status to check running jobs."}
            self._running_count += 1

        try:
            proc = await asyncio.create_subprocess_exec(...)
            asyncio.create_task(self._monitor_with_release(job_id, proc))
        except Exception:
            async with self._running_lock:
                self._running_count -= 1
            raise

        return job

    async def _monitor_with_release(self, job_id, proc):
        try:
            await self._monitor_job(job_id, proc)
        finally:
            async with self._running_lock:
                self._running_count -= 1
```

---

#### 3A.7: Time-Series for Flowsheets

**Files:**
- `utils/flowsheet_builder.py` - Extract and persist time-series
- `server.py` - Add `get_flowsheet_timeseries` tool

**Storage:** Save to `jobs/{job_id}/timeseries.json`

**New Tool:**
```python
@mcp.tool()
async def get_flowsheet_timeseries(
    job_id: str,
    stream_ids: Optional[Union[str, List[str]]] = None,
    components: Optional[Union[str, List[str]]] = None,
    downsample_factor: int = 1,
) -> Dict[str, Any]:
    """
    Get time-series data from flowsheet simulation.

    Returns:
        {
            "time": [0, 0.5, 1.0, ...],
            "time_units": "days",
            "streams": {
                "effluent": {
                    "S_F": [75, 60, 45, ...],
                    "COD": [350, 280, 120, ...]
                }
            }
        }
    """
```

---

### Phase 3B: Improved Discoverability (Tier 1)

#### 3B.1: get_model_components Tool

**Files:**
- `server.py` - Add tool
- `core/model_registry.py` - Add `COMPONENT_METADATA` dict

**Tool:**
```python
@mcp.tool()
async def get_model_components(
    model_type: str,
    include_typical_values: bool = True,
) -> Dict[str, Any]:
    """Get component IDs and metadata for a process model."""
```

**Response:**
```python
{
    "model_type": "ASM2d",
    "n_components": 19,
    "concentration_units": "mg/L",
    "components": [
        {"id": "S_F", "name": "Fermentable substrate", "category": "soluble", "typical_domestic": 75},
        {"id": "S_A", "name": "Acetate", "category": "soluble", "typical_domestic": 20},
        {"id": "S_NH4", "name": "Ammonium", "category": "soluble", "typical_domestic": 17},
        ...
    ],
    "categories": {
        "soluble": ["S_O2", "S_F", "S_A", "S_NH4", "S_NO3", "S_PO4", ...],
        "particulate": ["X_I", "X_S", "X_H", ...],
        "biomass": ["X_H", "X_PAO", "X_PP", "X_PHA", ...]
    }
}
```

---

#### 3B.2: validate_flowsheet Tool

**Files:**
- `server.py` - Add tool
- `utils/flowsheet_validator.py` - New file with validation logic

**Tool:**
```python
@mcp.tool()
async def validate_flowsheet(session_id: str) -> Dict[str, Any]:
    """
    Validate flowsheet without compiling.

    Checks:
    - All unit inputs resolve to streams or other units
    - No orphan units (not connected to anything)
    - Recycle streams can be detected
    - Model compatibility across junctions
    """
```

**Response:**
```python
{
    "is_valid": True,
    "errors": [],
    "warnings": ["Unit 'SP' has unused output port 1"],
    "detected_recycles": ["RAS"],
    "connectivity": {
        "orphan_units": [],
        "missing_inputs": {},
        "unreachable_units": []
    }
}
```

---

#### 3B.3: suggest_recycles Tool

**Files:**
- `server.py` - Add tool
- `utils/topo_sort.py` - Add cycle detection function

**Tool:**
```python
@mcp.tool()
async def suggest_recycles(session_id: str) -> Dict[str, Any]:
    """Detect potential recycle streams in flowsheet."""
```

**Response:**
```python
{
    "detected_cycles": [
        {
            "cycle_path": ["A1", "O1", "MBR", "SP", "A1"],
            "suggested_recycle": {"from": "SP-0", "to": "A1-1", "stream_id": "RAS"},
            "recycle_type": "return_activated_sludge",
            "confidence": "high"
        }
    ],
    "topology": {
        "sources": ["influent"],
        "sinks": ["effluent", "WAS"]
    }
}
```

---

### Phase 3C: Engineering-Grade Results (Tier 2)

#### 3C.1: Richer Simulation Results

**Files:**
- `utils/flowsheet_builder.py` - Enhance `_extract_simulation_results`
- `utils/stream_analysis.py` - Add mass balance function

**Enhanced Results:**
```python
{
    "streams": {
        "influent": {"flow_m3_d": 4000, "COD_mg_L": 350, "TN_mg_L": 45, "TP_mg_L": 12},
        "effluent": {"flow_m3_d": 3900, "COD_mg_L": 25, "TN_mg_L": 8, "TP_mg_L": 1.5}
    },
    "units": {
        "A1": {"unit_type": "CSTR", "HRT_hours": 6, "MLSS_mg_L": 3500}
    },
    "mass_balance": {
        "COD": {"in_kg_d": 1400, "out_kg_d": 97.5, "removed_kg_d": 1302.5, "error_pct": 0.0},
        "TN": {"in_kg_d": 180, "out_kg_d": 31.2, "removed_kg_d": 148.8, "error_pct": 0.0}
    },
    "kpis": {
        "COD_removal_pct": 92.9,
        "TN_removal_pct": 82.2,
        "effluent_NH4_mg_L": 2.1
    }
}
```

---

#### 3C.2: Artifact Retrieval Tool

**Files:**
- `server.py` - Add tool

**Tool:**
```python
@mcp.tool()
async def get_artifact(
    job_id: str,
    artifact_type: Literal["diagram", "report", "timeseries"],
    format: Optional[str] = None,
) -> Dict[str, Any]:
    """Get simulation artifact content directly."""
```

**Response:**
```python
{
    "artifact_type": "diagram",
    "format": "svg",
    "content": "<svg xmlns=...>...</svg>",
    "path": "jobs/abc123/flowsheet.svg"
}
```

---

#### 3C.3: Deterministic Metadata

**Files:**
- `utils/flowsheet_builder.py`
- All template `build_and_run` functions

**Added to all results:**
```python
result["metadata"] = {
    "qsdsan_version": qs.__version__,
    "biosteam_version": bst.__version__,
    "engine_version": "3.0.0",
    "solver": {"method": "RK23", "rtol": 1e-3, "atol": 1e-6},
    "timestamp": "2026-01-08T15:30:00Z",
    "duration_seconds": 45.2
}
```

---

### Phase 3D: Test Suite for 100% Tool Coverage

**Files:**
- `tests/test_phase3.py` - New test file (~40 tests)

#### Test Categories

```python
# =============================================================================
# 3A: Critical API Fixes
# =============================================================================

class TestNativeTypeParameters:
    """Test dual-mode parameter acceptance (JSON string + native type)."""

    def test_create_stream_with_dict_concentrations(self):
        """create_stream should accept Dict[str, float] directly."""

    def test_create_stream_with_json_string_concentrations(self):
        """create_stream should still accept JSON string for backwards compat."""

    def test_create_unit_with_dict_params(self):
        """create_unit should accept params as dict."""

    def test_create_unit_with_list_inputs(self):
        """create_unit should accept inputs as list."""

    def test_connect_units_with_list_connections(self):
        """connect_units should accept connections as list of dicts."""

    def test_build_system_with_list_recycles(self):
        """build_system should accept recycle_streams as list."""

    def test_simulate_built_system_with_list_track(self):
        """simulate_built_system should accept track as list."""


class TestSessionMutation:
    """Test update/delete operations."""

    def test_update_stream_flow(self):
        """update_stream should modify flow_m3_d."""

    def test_update_stream_concentrations(self):
        """update_stream should modify concentrations dict."""

    def test_update_stream_resets_compiled(self):
        """update_stream should reset status to 'building' if was 'compiled'."""

    def test_update_unit_params(self):
        """update_unit should modify params."""

    def test_update_unit_inputs(self):
        """update_unit should modify inputs list."""

    def test_delete_stream(self):
        """delete_stream should remove stream from session."""

    def test_delete_stream_with_references_fails(self):
        """delete_stream should fail if units reference it."""

    def test_delete_unit(self):
        """delete_unit should remove unit and its connections."""

    def test_delete_connection(self):
        """delete_connection should remove specific connection."""

    def test_clone_session(self):
        """clone_session should create independent copy."""

    def test_clone_session_custom_id(self):
        """clone_session should accept custom new_session_id."""

    def test_delete_session(self):
        """delete_session should remove session directory."""


class TestDeepIntrospection:
    """Test full session details in get_flowsheet_session."""

    def test_get_session_returns_stream_concentrations(self):
        """get_flowsheet_session should return full concentrations dict."""

    def test_get_session_returns_stream_flow(self):
        """get_flowsheet_session should return flow_m3_d."""

    def test_get_session_returns_unit_params(self):
        """get_flowsheet_session should return full unit params dict."""

    def test_get_session_returns_connection_stream_id(self):
        """get_flowsheet_session should return connection stream_id."""


class TestConcentrationUnits:
    """Test concentration unit handling."""

    def test_default_units_mg_L(self):
        """Default concentration_units should be 'mg/L'."""

    def test_explicit_mg_L(self):
        """Explicit 'mg/L' should pass through unchanged."""

    def test_kg_m3_converted_to_mg_L(self):
        """kg/m3 should be converted to mg/L internally."""

    def test_invalid_units_rejected(self):
        """Invalid concentration_units should raise error."""


class TestValidationWarnings:
    """Test warning surfacing in tool responses."""

    def test_unknown_component_warned(self):
        """Unknown component IDs should generate warning."""

    def test_unknown_unit_params_warned(self):
        """Unknown unit parameters should generate warning."""

    def test_model_incompatibility_warned(self):
        """Model-incompatible units should generate warning."""

    def test_warnings_in_create_stream_response(self):
        """create_stream response should include warnings array."""

    def test_warnings_in_create_unit_response(self):
        """create_unit response should include warnings array."""


class TestJobManagerConcurrency:
    """Test fixed semaphore behavior."""

    def test_max_concurrent_respected(self):
        """Should reject new jobs when max_concurrent reached."""

    def test_semaphore_released_after_completion(self):
        """Running count should decrement after job completes."""

    def test_concurrent_job_count(self):
        """Should accurately track running job count."""


class TestFlowsheetTimeseries:
    """Test time-series persistence and retrieval."""

    def test_timeseries_saved_when_track_provided(self):
        """Time-series should be saved when track streams specified."""

    def test_get_flowsheet_timeseries_returns_data(self):
        """get_flowsheet_timeseries should return trajectory data."""

    def test_timeseries_filter_by_stream(self):
        """Should filter by stream_ids parameter."""

    def test_timeseries_filter_by_component(self):
        """Should filter by components parameter."""

    def test_timeseries_downsample(self):
        """downsample_factor should reduce data points."""


# =============================================================================
# 3B: Discoverability
# =============================================================================

class TestGetModelComponents:
    """Test component discovery tool."""

    def test_get_asm2d_components(self):
        """Should return 19 ASM2d components."""

    def test_get_madm1_components(self):
        """Should return 63 mADM1 components."""

    def test_get_asm1_components(self):
        """Should return 13 ASM1 components."""

    def test_component_metadata_included(self):
        """Should include name, category, typical_range."""

    def test_categories_organized(self):
        """Should organize components by category."""


class TestValidateFlowsheet:
    """Test pre-compilation validation."""

    def test_valid_flowsheet_passes(self):
        """Valid flowsheet should return is_valid=True."""

    def test_missing_input_detected(self):
        """Should detect unit with unresolved input."""

    def test_orphan_unit_detected(self):
        """Should detect unit not connected to anything."""

    def test_cycle_detected_without_recycle(self):
        """Should detect cycles not marked as recycles."""

    def test_model_incompatibility_detected(self):
        """Should detect model incompatibility at junctions."""


class TestSuggestRecycles:
    """Test automatic recycle detection."""

    def test_ras_recycle_detected(self):
        """Should detect RAS recycle in MLE configuration."""

    def test_internal_recycle_detected(self):
        """Should detect internal recycle streams."""

    def test_no_false_positives(self):
        """Should not suggest recycles for linear flowsheet."""

    def test_recycle_confidence(self):
        """Should assign confidence based on unit types."""


# =============================================================================
# 3C: Engineering Results
# =============================================================================

class TestRicherResults:
    """Test enhanced simulation results."""

    def test_per_stream_summaries(self):
        """Results should include per-stream COD/TN/TP summaries."""

    def test_mass_balance_check(self):
        """Results should include mass balance verification."""

    def test_unit_kpis(self):
        """Results should include unit-specific KPIs."""


class TestGetArtifact:
    """Test artifact retrieval tool."""

    def test_get_diagram_svg(self):
        """Should return SVG diagram content."""

    def test_get_report_qmd(self):
        """Should return QMD report content."""

    def test_get_timeseries_json(self):
        """Should return time-series as JSON."""

    def test_missing_artifact_error(self):
        """Should error gracefully for missing artifacts."""


class TestDeterministicMetadata:
    """Test solver/version metadata."""

    def test_qsdsan_version_included(self):
        """Results should include qsdsan version."""

    def test_solver_settings_included(self):
        """Results should include solver method and tolerances."""

    def test_timestamp_included(self):
        """Results should include execution timestamp."""
```

---

## File Change Summary

| File | Changes | LOC Est. |
|------|---------|----------|
| `server.py` | Add 11 tools, modify 9 for native types | +400 |
| `utils/flowsheet_session.py` | Add mutation methods, enhance introspection | +200 |
| `utils/job_manager.py` | Fix concurrency, add counter pattern | +50 |
| `utils/flowsheet_builder.py` | Time-series extraction, unit conversion, enhanced results | +150 |
| `utils/flowsheet_validator.py` | NEW: Pre-compilation validation | +150 |
| `core/model_registry.py` | Add COMPONENT_METADATA | +100 |
| `utils/topo_sort.py` | Add cycle detection for suggest_recycles | +50 |
| `cli.py` | Add CLI commands for new tools | +100 |
| `tests/test_phase3.py` | NEW: ~40 tests | +600 |
| `CLAUDE.md` | Update documentation | +100 |

**Total:** ~1900 lines

---

## Tool Surface Summary (After Phase 3)

### Existing Tools (18)
1. `simulate_system` - Template simulation
2. `get_job_status` - Job polling
3. `get_job_results` - Result retrieval
4. `list_templates` - Template discovery
5. `validate_state` - PlantState validation
6. `convert_state` - Model conversion
7. `list_jobs` - Job listing
8. `terminate_job` - Job cancellation
9. `get_timeseries_data` - Template time-series
10. `create_flowsheet_session` - Session creation
11. `create_stream` - Stream creation
12. `create_unit` - Unit creation
13. `connect_units` - Connection creation
14. `build_system` - System compilation
15. `list_units` - Unit discovery
16. `simulate_built_system` - Flowsheet simulation
17. `get_flowsheet_session` - Session retrieval
18. `list_flowsheet_sessions` - Session listing

### New Tools (11)
19. `update_stream` - Stream mutation (3A.2)
20. `update_unit` - Unit mutation (3A.2)
21. `delete_stream` - Stream deletion (3A.2)
22. `delete_unit` - Unit deletion (3A.2)
23. `delete_connection` - Connection deletion (3A.2)
24. `clone_session` - Session cloning (3A.2)
25. `delete_session` - Session deletion (3A.2)
26. `get_flowsheet_timeseries` - Flowsheet trajectories (3A.7)
27. `get_model_components` - Component discovery (3B.1)
28. `validate_flowsheet` - Pre-compilation check (3B.2)
29. `suggest_recycles` - Cycle detection (3B.3)
30. `get_artifact` - Artifact content (3C.2)

**Total: 29 MCP tools**

---

## Verification Plan

### Unit Tests
- Run `pytest tests/test_phase3.py -v` after each sub-phase
- Target: All 40+ new tests passing

### Integration Tests
1. **Full MLE Flowsheet Workflow:**
   ```
   create_session -> create_stream -> create_unit (x4) ->
   connect_units -> validate_flowsheet -> build_system ->
   simulate_built_system -> get_flowsheet_timeseries -> get_artifact
   ```

2. **Mutation Workflow:**
   ```
   create_session -> create_stream -> update_stream ->
   create_unit -> update_unit -> delete_unit -> build_system
   ```

3. **Clone & Experiment:**
   ```
   create_session -> (build flowsheet) -> clone_session ->
   update_unit -> build_system (both) -> compare results
   ```

### Manual Verification
- Test with Claude Code as client
- Verify error messages are actionable
- Verify warnings surface correctly
- Verify artifacts are retrievable

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| ~~Breaking backwards compatibility~~ | ~~Dual-mode parameters (string + native)~~ **REMOVED - breaking changes accepted** |
| JobManager race conditions | Use asyncio.Lock for counter operations |
| Large time-series responses | Downsample option + separate tool |
| Session file corruption | Atomic write with temp file + rename |
| Test flakiness | Isolated test sessions with cleanup |

---

## Success Criteria

1. All 118 existing tests still pass
2. All ~40 new Phase 3 tests pass
3. 29 MCP tools with documented schemas
4. Agent can build, modify, and simulate flowsheets without starting over
5. Warnings visible in tool responses
6. Time-series data retrievable for flowsheet simulations
7. Component IDs discoverable via tool
8. Artifacts retrievable as content (not just paths)

---

## Codex Review Findings (2026-01-08)

### Critical Gaps Identified

| Severity | Issue | Location | Required Fix |
|----------|-------|----------|--------------|
| **HIGH** | No shared core methods for update/delete/clone | `utils/flowsheet_session.py:290-471` | Add methods to `FlowsheetSessionManager` before MCP/CLI tools |
| **HIGH** | Connection notation divergence | `server.py:686` vs `cli.py:686` | MCP supports `"U1-U2"` direct notation, CLI requires `{"from","to"}`. Fix CLI to support same notation. |
| **MEDIUM** | No time-series persistence | `utils/flowsheet_builder.py:240` | `simulate_compiled_system` returns in-memory only. Must persist to `time_series.json`. |
| **MEDIUM** | Tool count mismatch | Plan says 11, lists 12 | `get_artifact` is tool #12. Update plan counts. |
| **LOW** | `delete_session` already in CLI | `cli.py:1193` | CLI has `flowsheet delete`, MCP tool missing. Add MCP wrapper. |

### Revised CLI Command Mapping (Dual Adapter)

| MCP Tool | CLI Command | Shared Core Function |
|----------|-------------|---------------------|
| `update_stream` | `flowsheet update-stream` | `FlowsheetSessionManager.update_stream()` |
| `update_unit` | `flowsheet update-unit` | `FlowsheetSessionManager.update_unit()` |
| `delete_stream` | `flowsheet delete-stream` | `FlowsheetSessionManager.delete_stream()` |
| `delete_unit` | `flowsheet delete-unit` | `FlowsheetSessionManager.delete_unit()` |
| `delete_connection` | `flowsheet delete-connection` | `FlowsheetSessionManager.delete_connection()` |
| `clone_session` | `flowsheet clone` | `FlowsheetSessionManager.clone_session()` |
| `delete_session` | `flowsheet delete` (EXISTS) | `FlowsheetSessionManager.delete_session()` (EXISTS) |
| `get_flowsheet_timeseries` | `flowsheet timeseries` | `utils/flowsheet_builder.load_timeseries()` |
| `validate_flowsheet` | `flowsheet validate` | `utils/flowsheet_validator.validate_flowsheet()` |
| `suggest_recycles` | `flowsheet suggest-recycles` | `utils/topo_sort.detect_recycle_streams()` |
| `get_artifact` | `flowsheet artifact` | `utils/flowsheet_builder.get_artifact_path()` |
| `get_model_components` | `models components` | `core/model_registry.get_component_metadata()` |

### Revised LOC Estimates (With Breaking Changes Accepted)

| File | Original Est. | Codex Revised | Final (No Compat) | Reason |
|------|--------------|---------------|-------------------|--------|
| `server.py` | +400 | +400 | **+350** | No dual-mode parsing |
| `cli.py` | +100 | +400 | **+350** | No dual-mode parsing, simpler |
| `utils/flowsheet_session.py` | +200 | +250 | +250 | Add 6 mutation methods + clone |
| `utils/flowsheet_validator.py` | +150 | +150 | +150 | Unchanged |
| `utils/json_helpers.py` | +30 | +30 | **0** | Not needed |
| **Total** | ~1900 | ~2200 | **~2000** | Simplified by dropping compat |

### Open Design Questions

1. **`clone_session` artifact handling:** Copy only `session.json` (reset to "building") or include `build_config.json`/`system_result.json` (preserve "compiled")?
   - **Recommendation:** Copy only `session.json`, reset status to "building"

2. **`update_stream`/`update_unit` semantics:** Patch-style (merge) or replace-style?
   - **Recommendation:** Patch-style (merge only provided fields)

3. **Artifact registry path convention:**
   - Session artifacts: `jobs/flowsheets/{session_id}/`
   - Job artifacts: `jobs/{job_id}/`
   - `get_artifact` should accept both session_id and job_id

### Shared Helper Requirements

**No longer needed:** `utils/json_helpers.py` with `parse_json_or_native()` - since we're dropping backwards compatibility, both MCP and CLI will use native types directly.

### CLI Binary Artifact Pattern

For `flowsheet artifact` command:
```python
@flowsheet_app.command("artifact")
def flowsheet_artifact(
    job_id: str = typer.Option(..., "--job", "-j", help="Job ID"),
    artifact_type: str = typer.Option(..., "--type", "-t", help="diagram|report|timeseries"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (required for binary)"),
    json_out: bool = typer.Option(False, "--json-out", help="Output metadata as JSON"),
):
    # Binary (svg/png/pdf) -> require --output, write to file
    # Text (qmd/json) -> stdout or --output
    # --json-out -> metadata only: {"path": ..., "mime": ..., "size": ...}
```

### Strengthened Dual-Adapter Architecture (Simplified - No Compat Layer)

```
┌─────────────────────────────────────────────────────────────┐
│                    Adapter Layer (Thin)                     │
├─────────────────────────────┬───────────────────────────────┤
│   server.py (MCP Tools)     │    cli.py (Typer Commands)    │
│   - Async wrappers          │    - Sync wrappers            │
│   - Native type params      │    - Native type params       │
│   - JSON response format    │    - Rich console output      │
└─────────────────────────────┴───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Shared Core Layer                        │
├─────────────────────────────────────────────────────────────┤
│ utils/flowsheet_session.py  - Session CRUD + mutations      │
│ utils/flowsheet_builder.py  - Compile, simulate, timeseries │
│ utils/flowsheet_validator.py- Pre-compilation validation    │
│ utils/topo_sort.py          - Recycle detection             │
│ core/model_registry.py      - Component metadata            │
│ core/unit_registry.py       - Unit specs                    │
└─────────────────────────────────────────────────────────────┘
```

**Simplification from dropping backwards compatibility:**
- No `utils/json_helpers.py` needed
- No dual-mode parsing in adapters
- Cleaner type signatures throughout
- ~200 LOC saved

### Revised Test Strategy

```python
# tests/test_phase3_core.py - Unit tests (no adapters)
# Import directly from utils/ and core/
from utils.flowsheet_session import FlowsheetSessionManager
from utils.flowsheet_validator import validate_flowsheet
from core.model_registry import get_component_metadata

# tests/test_phase3_cli.py - CLI integration tests
# Use subprocess like Phase 2 tests
import subprocess
result = subprocess.run(['python', 'cli.py', 'flowsheet', 'validate', ...])

# tests/test_phase3_mcp.py - MCP wrapper tests (minimal)
# Use FastMCP in-memory Client (per FastMCP README)
from mcp import Client
async def test_mcp_wrapper():
    async with Client("qsdsan-engine") as client:
        result = await client.call_tool("validate_flowsheet", {...})
```
