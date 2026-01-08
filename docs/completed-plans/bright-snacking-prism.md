# Phase 2: Flowsheet Construction - Implementation Plan

## Overview

Enable dynamic flowsheet construction via MCP/CLI tools, allowing users to build custom treatment trains beyond pre-built templates.

**Current State:** Phase 1 complete with 4 pre-built templates (anaerobic_cstr_madm1, mle_mbr_asm2d, ao_mbr_asm2d, a2o_mbr_asm2d)

**Goal:** Add 7 new tools for dynamic unit creation, connection, system compilation, and simulation with comprehensive reporting

---

## Codex Review Findings (2026-01-06)

| Severity | Issue | Resolution |
|----------|-------|------------|
| **HIGH** | Single `model_type` blocks mixed ASM2d↔ADM1 flowsheets | Add per-unit/stream `model_type` + session-level `model_types` set |
| **HIGH** | Pipe parser only supports `A1-0`/`1-M1`, missing `U1-U2`, `U1-0-1-U2`, tuple fan-in/out | Extend parser to full BioSTEAM syntax |
| **MEDIUM** | Unit registry missing thermo/process-model hooks | Add `thermo_registry` per model type |
| **MEDIUM** | `build_system` has `simulate=True` conflict | Remove - use separate `simulate_built_system` tool |
| **MEDIUM** | Topo sort doesn't handle recycle edges | Mark recycles before sorting, exclude from cycle detection |
| **LOW** | Session dir collides with JobManager | Use `jobs/flowsheets/{session_id}/` |

---

## Architecture Decision: Session-Based State Management

### Recommended Approach: Flowsheet Session State

Store flowsheet construction state in dedicated directory (separate from simulation jobs):

```
jobs/flowsheets/{session_id}/
├── session.json       # Session state (units, streams, connections)
├── config.json        # Session configuration
└── system_result.json # Build output (after build_system)
```

**Rationale:**
- Consistent with existing `JobManager` pattern
- Survives MCP reconnections
- JSON-serializable for debugging
- No global state in server

### Session State Schema (Updated for Mixed Model Support)

Per Codex review, support per-unit and per-stream model types for integrated plants (ASM2d+ADM1):

```python
@dataclass
class FlowsheetSession:
    session_id: str
    primary_model_type: ModelType         # Default model for new units/streams
    model_types: Set[ModelType]           # All models used in session (auto-tracked)
    streams: Dict[str, StreamConfig]      # stream_id -> config (includes model_type)
    units: Dict[str, UnitConfig]          # unit_id -> config (includes model_type)
    connections: List[ConnectionConfig]   # deferred connections
    thermo_contexts: Dict[str, Any]       # model_type -> thermo/components cache
    created_at: datetime
    status: Literal["building", "compiled", "failed"]

# StreamConfig and UnitConfig include model_type field:
@dataclass
class StreamConfig:
    stream_id: str
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
    stream_type: str
    model_type: ModelType  # Per-stream model type

@dataclass
class UnitConfig:
    unit_id: str
    unit_type: str
    params: Dict[str, Any]
    inputs: List[str]
    outputs: Optional[List[str]]
    model_type: ModelType  # Per-unit model type (validated against compatible_models)
```

**Mixed Model Example (Integrated Plant):**
```python
# ASM2d activated sludge → ADM1 anaerobic digester
session = create_flowsheet_session(model_type="ASM2d")

# ASM2d section
create_unit(session_id, "CSTR", "A1", ..., model_type="ASM2d")
create_unit(session_id, "MBR", "MBR", ..., model_type="ASM2d")

# Junction (state converter)
create_unit(session_id, "ASM2dtoADM1", "J1", inputs=["WAS"])

# ADM1 section
create_unit(session_id, "AnaerobicCSTR", "AD1", ..., model_type="ADM1", inputs=["J1-0"])
```

---

## New Files to Create

| File | Purpose |
|------|---------|
| `core/unit_registry.py` | Unit type validation, parameter schemas |
| `utils/pipe_parser.py` | Parse `"A1-0"` → port references |
| `utils/flowsheet_session.py` | Session state management |

---

## Tool Specifications

### Tool 1: `create_flowsheet_session`

Creates a new session for flowsheet construction.

```python
@mcp.tool()
async def create_flowsheet_session(
    model_type: str,              # "ASM2d", "mADM1", etc.
    session_id: Optional[str],    # Auto-generate if not provided
) -> Dict[str, Any]:
    """
    Create a new flowsheet construction session.

    Returns:
        session_id, model_type, available_units
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet new --model ASM2d
```

---

### Tool 2: `create_stream`

Creates a WasteStream in the session.

```python
@mcp.tool()
async def create_stream(
    session_id: str,
    stream_id: str,               # e.g., "influent", "RAS"
    flow_m3_d: float,
    concentrations: str,          # JSON dict of component -> mg/L
    temperature_K: float = 293.15,
    stream_type: str = "influent", # "influent", "recycle", "intermediate"
) -> Dict[str, Any]:
    """
    Create a WasteStream in the flowsheet session.

    Returns:
        stream_id, validation status
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet add-stream --session abc123 --id influent \
    --flow 4000 --concentrations '{"S_F": 75, "S_A": 20, ...}'
```

---

### Tool 3: `create_unit`

Creates a SanUnit in the session.

```python
@mcp.tool()
async def create_unit(
    session_id: str,
    unit_type: str,               # "CSTR", "CompletelyMixedMBR", "Splitter"
    unit_id: str,                 # e.g., "A1", "O1", "MBR"
    params: str,                  # JSON dict of unit parameters
    inputs: str,                  # JSON list: ["influent", "RAS", "A1-0"]
    outputs: Optional[str] = None, # JSON list of output names (optional)
) -> Dict[str, Any]:
    """
    Create a SanUnit in the flowsheet session.

    Args:
        unit_type: One of AVAILABLE_UNITS keys
        params: Unit-specific parameters as JSON:
            - CSTR: {"V_max": 1000, "aeration": 2.3, "DO_ID": "S_O2"}
            - Splitter: {"split": 0.8}
            - CompletelyMixedMBR: {"V_max": 500, "solids_capture_rate": 0.999}
        inputs: List of input sources (stream IDs or pipe notation)
        outputs: Optional list of output stream names

    Returns:
        unit_id, validation status, input/output port info
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet add-unit --session abc123 --type CSTR --id A1 \
    --params '{"V_max": 1000, "aeration": null}' --inputs '["influent", "RAS"]'
```

---

### Tool 4: `connect_units`

Adds deferred connections (for recycles created after units).

```python
@mcp.tool()
async def connect_units(
    session_id: str,
    connections: str,  # JSON list of {"from": "C1-1", "to": "A1-1"}
) -> Dict[str, Any]:
    """
    Add connections between units (for recycles).

    Use this after creating units to wire recycle streams that
    couldn't be specified during unit creation.

    Returns:
        connections added, validation status
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet connect --session abc123 \
    --connections '[{"from": "C1-1", "to": "A1-1"}]'
```

---

### Tool 5: `build_system`

Compiles the flowsheet into a QSDsan System (no simulation - use `simulate_built_system` separately).

```python
@mcp.tool()
async def build_system(
    session_id: str,
    system_id: str,
    unit_order: Optional[str] = None,  # JSON list, auto-infer if not provided
    recycle_streams: Optional[str] = None,  # JSON list of recycle stream IDs
) -> Dict[str, Any]:
    """
    Compile flowsheet into QSDsan System.

    This is a synchronous operation that validates and compiles the session.
    Use simulate_built_system() to run the simulation.

    Returns:
        system_id, validation status, unit execution order, recycle info
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet build --session abc123 --system-id custom_mle \
    --recycles '["RAS", "IR"]'
```

**Note:** Per Codex review, simulation is decoupled from build. Use `simulate_built_system` after `build_system` succeeds.

---

### Tool 6: `list_units`

Lists available SanUnit types with parameters.

```python
@mcp.tool()
async def list_units(
    model_type: Optional[str] = None,  # Filter by compatible model
    category: Optional[str] = None,    # "reactor", "separator", "junction", "utility"
) -> Dict[str, Any]:
    """
    List available SanUnit types with their parameters.

    Returns:
        Dict of unit_type -> {description, parameters, compatible_models}
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet units --model ASM2d
```

---

### Tool 7: `simulate_built_system`

Simulates a compiled flowsheet with comprehensive reporting (matching Phase 1 quality).

```python
@mcp.tool()
async def simulate_built_system(
    session_id: Optional[str] = None,
    system_id: Optional[str] = None,
    duration_days: float = 1.0,
    timestep_hours: float = 1.0,
    method: str = "RK23",
    t_eval: Optional[str] = None,  # JSON list of evaluation times
    state_reset_hook: str = "reset_cache",
    track: Optional[str] = None,  # JSON list of stream IDs to track
    effluent_stream_ids: Optional[str] = None,  # JSON list for effluent analysis
    biogas_stream_ids: Optional[str] = None,  # JSON list for biogas analysis
    report: bool = True,
    diagram: bool = True,
    include_components: bool = False,
    export_state_to: Optional[str] = None,  # Export final state as PlantState JSON
) -> Dict[str, Any]:
    """
    Simulate a compiled flowsheet with comprehensive reporting.

    This is a background job that uses JobManager (like simulate_system).

    Args:
        session_id: Flowsheet session ID (mutually exclusive with system_id)
        system_id: Previously built system ID (mutually exclusive with session_id)
        duration_days: Simulation duration
        timestep_hours: Output timestep
        method: ODE solver method ("RK23", "RK45", "BDF")
        t_eval: Custom evaluation times (JSON list)
        state_reset_hook: Hook for state reset ("reset_cache")
        track: Stream IDs to track dynamically (JSON list)
        effluent_stream_ids: Streams for effluent quality analysis
        biogas_stream_ids: Streams for biogas analysis (mADM1 sessions)
        report: Generate Quarto report
        diagram: Generate flowsheet diagram
        include_components: Include full component breakdown in results
        export_state_to: Path to export final effluent state as PlantState JSON

    Returns:
        job_id for tracking via get_job_status/get_job_results

    Reporting Features (matching Phase 1):
        - Effluent quality: COD, TSS, NH4-N, NO3-N, TN, PO4-P, TP
        - Removal efficiencies: % removal for key parameters
        - Biogas (mADM1 only): CH4/CO2/H2S/H2 yields, production rate
        - Inhibition analysis (mADM1 only): pH, ammonia, sulfide, VFA
        - SRT/HRT calculations
        - Time series availability flag (use get_timeseries_data)
        - Flowsheet diagram (SVG)
        - Quarto report with mass balance tables
    """
```

**CLI equivalent:**
```bash
python cli.py flowsheet simulate --session abc123 --duration 15 --report
# Or with system ID from previous build:
python cli.py flowsheet simulate --system-id custom_mle --duration 15
```

**Background Job Pattern:**
```python
# In server.py implementation
async def simulate_built_system(...):
    # 1. Load session or system from disk
    # 2. Create job directory
    # 3. Build CLI command for cli.py flowsheet simulate
    # 4. Execute via JobManager
    # 5. Return job_id for tracking

    cmd = [
        python_exe, "cli.py", "flowsheet", "simulate",
        "--session", session_id,
        "--duration-days", str(duration_days),
        "--method", method,
        "--output-dir", str(job_dir),
    ]
    if report:
        cmd.append("--report")
    if diagram:
        cmd.append("--diagram")
    # ... etc
```

---

## Core Implementation

### `core/unit_registry.py`

```python
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum

class UnitCategory(str, Enum):
    REACTOR = "reactor"
    SEPARATOR = "separator"
    CLARIFIER = "clarifier"
    SLUDGE = "sludge"
    PUMP = "pump"
    JUNCTION = "junction"
    UTILITY = "utility"
    PRETREATMENT = "pretreatment"

@dataclass
class UnitSpec:
    unit_type: str
    category: UnitCategory
    description: str
    compatible_models: List[str]  # Empty = model-agnostic
    required_params: Dict[str, type]
    optional_params: Dict[str, Any]
    qsdsan_class: str
    is_dynamic: bool = True

# COMPREHENSIVE UNIT REGISTRY (from QSDsan sanunits module)
UNIT_REGISTRY: Dict[str, UnitSpec] = {
    # ==================== REACTORS ====================
    "CSTR": UnitSpec(
        unit_type="CSTR",
        category=UnitCategory.REACTOR,
        description="Continuous stirred-tank reactor with optional aeration",
        compatible_models=["ASM2d", "ASM1", "mASM2d"],
        required_params={"V_max": float},
        optional_params={"aeration": None, "DO_ID": "S_O2"},
        qsdsan_class="sanunits.CSTR",
        is_dynamic=True,
    ),
    "AnaerobicCSTR": UnitSpec(
        unit_type="AnaerobicCSTR",
        category=UnitCategory.REACTOR,
        description="Anaerobic CSTR with biogas headspace (ADM1)",
        compatible_models=["ADM1"],
        required_params={"V_liq": float},
        optional_params={"V_gas": 300.0, "T": 308.15, "headspace_P": 1.013},
        qsdsan_class="sanunits.AnaerobicCSTR",
        is_dynamic=True,
    ),
    "AnaerobicCSTRmADM1": UnitSpec(
        unit_type="AnaerobicCSTRmADM1",
        category=UnitCategory.REACTOR,
        description="Anaerobic CSTR with mADM1 (63 components, 4 biogas species)",
        compatible_models=["mADM1"],
        required_params={"V_liq": float},
        optional_params={"V_gas": 300.0, "T": 308.15, "headspace_P": 1.013},
        qsdsan_class="models.reactors.AnaerobicCSTRmADM1",  # Custom class
        is_dynamic=True,
    ),
    "SBR": UnitSpec(
        unit_type="SBR",
        category=UnitCategory.REACTOR,
        description="Sequential batch reactor",
        compatible_models=["ASM2d", "ASM1"],
        required_params={"V_max": float},
        optional_params={"cycle_time": 4.0},
        qsdsan_class="sanunits.SBR",
        is_dynamic=False,  # Under development
    ),
    "AnaerobicBaffledReactor": UnitSpec(
        unit_type="AnaerobicBaffledReactor",
        category=UnitCategory.REACTOR,
        description="Anaerobic baffled reactor",
        compatible_models=["ADM1"],
        required_params={"V_liq": float},
        optional_params={"n_compartments": 4},
        qsdsan_class="sanunits.AnaerobicBaffledReactor",
        is_dynamic=True,
    ),
    "InternalCirculationRx": UnitSpec(
        unit_type="InternalCirculationRx",
        category=UnitCategory.REACTOR,
        description="Two-stage anaerobic reactor with internal circulation",
        compatible_models=["ADM1"],
        required_params={"V_liq": float},
        optional_params={},
        qsdsan_class="sanunits.InternalCirculationRx",
        is_dynamic=True,
    ),
    "MixTank": UnitSpec(
        unit_type="MixTank",
        category=UnitCategory.REACTOR,
        description="Mixing tank with retention time",
        compatible_models=[],
        required_params={"tau": float},
        optional_params={"V_wf": 0.8},
        qsdsan_class="sanunits.MixTank",
        is_dynamic=True,
    ),

    # ==================== MEMBRANE BIOREACTORS ====================
    "CompletelyMixedMBR": UnitSpec(
        unit_type="CompletelyMixedMBR",
        category=UnitCategory.SEPARATOR,
        description="MBR with ideal membrane separation",
        compatible_models=["ASM2d", "mASM2d"],
        required_params={"V_max": float},
        optional_params={"solids_capture_rate": 0.999, "pumped_flow": None, "aeration": None, "DO_ID": "S_O2"},
        qsdsan_class="sanunits.CompletelyMixedMBR",
        is_dynamic=True,
    ),
    "AnMBR": UnitSpec(
        unit_type="AnMBR",
        category=UnitCategory.SEPARATOR,
        description="Anaerobic membrane bioreactor",
        compatible_models=["ADM1"],
        required_params={"V_max": float},
        optional_params={"solids_capture_rate": 0.999},
        qsdsan_class="sanunits.AnMBR",
        is_dynamic=True,
    ),

    # ==================== CLARIFIERS ====================
    "FlatBottomCircularClarifier": UnitSpec(
        unit_type="FlatBottomCircularClarifier",
        category=UnitCategory.CLARIFIER,
        description="Flat-bottom circular clarifier with layered settling",
        compatible_models=[],
        required_params={"surface_area": float, "height": float},
        optional_params={"N_layer": 10},
        qsdsan_class="sanunits.FlatBottomCircularClarifier",
        is_dynamic=True,
    ),
    "PrimaryClarifier": UnitSpec(
        unit_type="PrimaryClarifier",
        category=UnitCategory.CLARIFIER,
        description="Primary clarifier optimized for primary treatment",
        compatible_models=[],
        required_params={"surface_area": float},
        optional_params={"height": 4.0},
        qsdsan_class="sanunits.PrimaryClarifier",
        is_dynamic=True,
    ),
    "IdealClarifier": UnitSpec(
        unit_type="IdealClarifier",
        category=UnitCategory.CLARIFIER,
        description="Simplified clarifier with specified removal efficiency",
        compatible_models=[],
        required_params={},
        optional_params={"sludge_flow_rate": 2000, "solids_removal_efficiency": 0.995},
        qsdsan_class="sanunits.IdealClarifier",
        is_dynamic=False,
    ),
    "Sedimentation": UnitSpec(
        unit_type="Sedimentation",
        category=UnitCategory.CLARIFIER,
        description="General sedimentation unit",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.Sedimentation",
        is_dynamic=False,
    ),

    # ==================== SLUDGE TREATMENT ====================
    "Thickener": UnitSpec(
        unit_type="Thickener",
        category=UnitCategory.SLUDGE,
        description="Sludge thickening unit (BSM2 layout)",
        compatible_models=[],
        required_params={},
        optional_params={"thickener_perc": 7.0, "TSS_removal_perc": 98.0},
        qsdsan_class="sanunits.Thickener",
        is_dynamic=False,
    ),
    "Centrifuge": UnitSpec(
        unit_type="Centrifuge",
        category=UnitCategory.SLUDGE,
        description="Mechanical sludge dewatering",
        compatible_models=[],
        required_params={},
        optional_params={"thickener_perc": 20.0, "TSS_removal_perc": 98.0},
        qsdsan_class="sanunits.Centrifuge",
        is_dynamic=False,
    ),
    "BeltThickener": UnitSpec(
        unit_type="BeltThickener",
        category=UnitCategory.SLUDGE,
        description="Belt thickener for sludge",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.BeltThickener",
        is_dynamic=False,
    ),
    "SludgeDigester": UnitSpec(
        unit_type="SludgeDigester",
        category=UnitCategory.SLUDGE,
        description="Sludge digestion unit",
        compatible_models=["ADM1"],
        required_params={"V_liq": float},
        optional_params={},
        qsdsan_class="sanunits.SludgeDigester",
        is_dynamic=True,
    ),
    "Incinerator": UnitSpec(
        unit_type="Incinerator",
        category=UnitCategory.SLUDGE,
        description="Thermal sludge treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.Incinerator",
        is_dynamic=False,
    ),

    # ==================== PRETREATMENT ====================
    "Screening": UnitSpec(
        unit_type="Screening",
        category=UnitCategory.PRETREATMENT,
        description="Screening for preliminary treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.Screening",
        is_dynamic=False,
    ),
    "SepticTank": UnitSpec(
        unit_type="SepticTank",
        category=UnitCategory.PRETREATMENT,
        description="Septic tank for primary treatment",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.SepticTank",
        is_dynamic=False,
    ),

    # ==================== PUMPING & HYDRAULICS ====================
    "Pump": UnitSpec(
        unit_type="Pump",
        category=UnitCategory.PUMP,
        description="Generic pump for fluid transport",
        compatible_models=[],
        required_params={},
        optional_params={"pump_type": "centrifugal"},
        qsdsan_class="sanunits.Pump",
        is_dynamic=True,
    ),
    "WWTpump": UnitSpec(
        unit_type="WWTpump",
        category=UnitCategory.PUMP,
        description="Wastewater treatment pump with design correlations",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.WWTpump",
        is_dynamic=True,
    ),
    "SludgePump": UnitSpec(
        unit_type="SludgePump",
        category=UnitCategory.PUMP,
        description="Pump optimized for high solids content",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.SludgePump",
        is_dynamic=True,
    ),
    "HydraulicDelay": UnitSpec(
        unit_type="HydraulicDelay",
        category=UnitCategory.PUMP,
        description="First-order hydraulic residence time delay",
        compatible_models=[],
        required_params={"tau": float},
        optional_params={},
        qsdsan_class="sanunits.HydraulicDelay",
        is_dynamic=True,
    ),

    # ==================== JUNCTIONS (STATE CONVERTERS) ====================
    "ASM2dtoADM1": UnitSpec(
        unit_type="ASM2dtoADM1",
        category=UnitCategory.JUNCTION,
        description="Convert ASM2d state to ADM1 for digester feed",
        compatible_models=["ASM2d", "ADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.ASM2dtoADM1",
        is_dynamic=True,
    ),
    "ADM1toASM2d": UnitSpec(
        unit_type="ADM1toASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert ADM1 state to ASM2d for sidestream return",
        compatible_models=["ADM1", "ASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.ADM1toASM2d",
        is_dynamic=True,
    ),
    "mADM1toASM2d": UnitSpec(
        unit_type="mADM1toASM2d",
        category=UnitCategory.JUNCTION,
        description="Convert mADM1 state to ASM2d",
        compatible_models=["mADM1", "ASM2d"],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.mADM1toASM2d",
        is_dynamic=True,
    ),
    "ASM2dtomADM1": UnitSpec(
        unit_type="ASM2dtomADM1",
        category=UnitCategory.JUNCTION,
        description="Convert ASM2d state to mADM1",
        compatible_models=["ASM2d", "mADM1"],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.ASM2dtomADM1",
        is_dynamic=True,
    ),

    # ==================== UTILITY UNITS ====================
    "Splitter": UnitSpec(
        unit_type="Splitter",
        category=UnitCategory.UTILITY,
        description="Flow splitter with configurable split ratio",
        compatible_models=[],
        required_params={"split": float},
        optional_params={},
        qsdsan_class="sanunits.Splitter",
        is_dynamic=True,
    ),
    "Mixer": UnitSpec(
        unit_type="Mixer",
        category=UnitCategory.UTILITY,
        description="Stream mixer combining multiple inputs",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.Mixer",
        is_dynamic=True,
    ),
    "ComponentSplitter": UnitSpec(
        unit_type="ComponentSplitter",
        category=UnitCategory.UTILITY,
        description="Split streams based on specific components",
        compatible_models=[],
        required_params={"split_keys": list},
        optional_params={},
        qsdsan_class="sanunits.ComponentSplitter",
        is_dynamic=False,
    ),
    "Tank": UnitSpec(
        unit_type="Tank",
        category=UnitCategory.UTILITY,
        description="General storage/equalization tank",
        compatible_models=[],
        required_params={"V_max": float},
        optional_params={},
        qsdsan_class="sanunits.Tank",
        is_dynamic=True,
    ),
    "StorageTank": UnitSpec(
        unit_type="StorageTank",
        category=UnitCategory.UTILITY,
        description="Storage tank for holding streams",
        compatible_models=[],
        required_params={"V_max": float},
        optional_params={},
        qsdsan_class="sanunits.StorageTank",
        is_dynamic=False,
    ),

    # ==================== TERTIARY TREATMENT ====================
    "PolishingFilter": UnitSpec(
        unit_type="PolishingFilter",
        category=UnitCategory.SEPARATOR,
        description="Remove residual contaminants from treated effluent",
        compatible_models=[],
        required_params={},
        optional_params={},
        qsdsan_class="sanunits.PolishingFilter",
        is_dynamic=False,
    ),
}

def get_unit_spec(unit_type: str) -> UnitSpec:
    """Get unit specification or raise ValueError."""

def validate_unit_params(unit_type: str, params: dict) -> tuple[list, list]:
    """Validate params, return (errors, warnings)."""

def list_available_units(model_type: Optional[str] = None) -> List[Dict]:
    """List units, optionally filtered by model compatibility."""
```

---

### `utils/pipe_parser.py`

Extended pipe notation parser supporting full BioSTEAM syntax (per Codex review).

```python
from typing import Union, Tuple, List, Optional
from dataclasses import dataclass
from qsdsan import WasteStream

@dataclass
class PortReference:
    """Parsed port reference."""
    unit_id: str
    port_type: str  # "input", "output", "stream", "direct"
    index: int  # -1 for streams, port index otherwise
    target_unit_id: Optional[str] = None  # For direct U1-U2 connections

def parse_port_notation(port_str: str) -> PortReference:
    """
    Parse pipe notation string with full BioSTEAM syntax support.

    Supported notations:
        "A1-0"      -> Output port 0 of unit A1
        "1-M1"      -> Input port 1 of unit M1
        "U1-U2"     -> Direct connection: U1.outs[0] -> U2.ins[0]
        "U1-0-1-U2" -> Explicit: U1.outs[0] -> U2.ins[1]
        "influent"  -> Named stream

    Args:
        port_str: Port notation string

    Returns:
        PortReference with parsed details

    Examples:
        parse_port_notation("A1-0")
        # -> PortReference("A1", "output", 0)

        parse_port_notation("1-M1")
        # -> PortReference("M1", "input", 1)

        parse_port_notation("U1-U2")
        # -> PortReference("U1", "direct", 0, target_unit_id="U2")

        parse_port_notation("U1-0-1-U2")
        # -> PortReference("U1", "direct", 0, target_unit_id="U2")
        # (with explicit output 0 -> input 1)

        parse_port_notation("influent")
        # -> PortReference("influent", "stream", -1)
    """
    if "-" not in port_str:
        # Plain stream name
        return PortReference(port_str, "stream", -1)

    parts = port_str.split("-")

    if len(parts) == 2:
        # Could be: "A1-0", "1-M1", or "U1-U2"
        if parts[0].isdigit() and not parts[1].isdigit():
            # Input notation: "1-M1"
            return PortReference(parts[1], "input", int(parts[0]))
        elif parts[1].isdigit() and not parts[0].isdigit():
            # Output notation: "A1-0"
            return PortReference(parts[0], "output", int(parts[1]))
        elif not parts[0].isdigit() and not parts[1].isdigit():
            # Direct unit-to-unit: "U1-U2" (default ports 0->0)
            return PortReference(parts[0], "direct", 0, target_unit_id=parts[1])
        else:
            raise ValueError(f"Ambiguous notation: {port_str}")

    elif len(parts) == 4:
        # Explicit port mapping: "U1-0-1-U2"
        # U1.outs[0] -> U2.ins[1]
        src_unit, out_idx, in_idx, dst_unit = parts
        if not out_idx.isdigit() or not in_idx.isdigit():
            raise ValueError(f"Invalid explicit port notation: {port_str}")
        return PortReference(
            src_unit, "direct", int(out_idx),
            target_unit_id=dst_unit
        )
        # Note: in_idx is stored separately if needed for connection

    else:
        raise ValueError(f"Unsupported pipe notation: {port_str}")


def resolve_port(
    port_str: str,
    unit_registry: dict,
    stream_registry: dict,
) -> Union[WasteStream, "Port"]:
    """
    Resolve port notation to actual QSDsan object.

    Args:
        port_str: Port notation or stream ID
        unit_registry: Dict of unit_id -> SanUnit object
        stream_registry: Dict of stream_id -> WasteStream object

    Returns:
        WasteStream for streams, or unit.ins[i]/unit.outs[i] for ports
    """
    ref = parse_port_notation(port_str)

    if ref.port_type == "stream":
        if ref.unit_id not in stream_registry:
            raise ValueError(f"Stream '{ref.unit_id}' not found")
        return stream_registry[ref.unit_id]

    if ref.unit_id not in unit_registry:
        raise ValueError(f"Unit '{ref.unit_id}' not found")

    unit = unit_registry[ref.unit_id]

    if ref.port_type == "output":
        if ref.index >= len(unit.outs):
            raise ValueError(f"Unit '{ref.unit_id}' has no output port {ref.index}")
        return unit.outs[ref.index]
    elif ref.port_type == "input":
        if ref.index >= len(unit.ins):
            raise ValueError(f"Unit '{ref.unit_id}' has no input port {ref.index}")
        return unit.ins[ref.index]
    elif ref.port_type == "direct":
        # For direct connections, return the output port
        # The caller handles wiring to target unit
        if ref.index >= len(unit.outs):
            raise ValueError(f"Unit '{ref.unit_id}' has no output port {ref.index}")
        return unit.outs[ref.index]

    raise ValueError(f"Unknown port type: {ref.port_type}")


def parse_tuple_notation(tuple_str: str) -> List[str]:
    """
    Parse tuple fan-in/fan-out notation.

    Examples:
        "(A1-0, B1-0)" -> ["A1-0", "B1-0"]  # Fan-in
        "(effluent, WAS)" -> ["effluent", "WAS"]  # Fan-out
    """
    if tuple_str.startswith("(") and tuple_str.endswith(")"):
        inner = tuple_str[1:-1]
        return [p.strip() for p in inner.split(",")]
    return [tuple_str]
```

---

### `utils/flowsheet_session.py`

```python
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Any, Optional
import json
from datetime import datetime

@dataclass
class StreamConfig:
    stream_id: str
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]
    stream_type: str  # "influent", "recycle", "intermediate"

@dataclass
class UnitConfig:
    unit_id: str
    unit_type: str
    params: Dict[str, Any]
    inputs: List[str]  # Port notations or stream IDs
    outputs: Optional[List[str]] = None

@dataclass
class ConnectionConfig:
    from_port: str  # e.g., "C1-1"
    to_port: str    # e.g., "A1-1"

@dataclass
class FlowsheetSession:
    session_id: str
    model_type: str
    streams: Dict[str, StreamConfig] = field(default_factory=dict)
    units: Dict[str, UnitConfig] = field(default_factory=dict)
    connections: List[ConnectionConfig] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "building"

    def save(self, path: Path):
        """Save session to JSON file."""
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "FlowsheetSession":
        """Load session from JSON file."""
        with open(path) as f:
            data = json.load(f)
        # Reconstruct nested dataclasses
        data["streams"] = {
            k: StreamConfig(**v) for k, v in data["streams"].items()
        }
        data["units"] = {
            k: UnitConfig(**v) for k, v in data["units"].items()
        }
        data["connections"] = [
            ConnectionConfig(**c) for c in data["connections"]
        ]
        return cls(**data)


class FlowsheetSessionManager:
    """Manage flowsheet construction sessions."""

    def __init__(self, sessions_dir: Path = Path("jobs")):
        self.sessions_dir = sessions_dir

    def create_session(
        self,
        model_type: str,
        session_id: Optional[str] = None,
    ) -> FlowsheetSession:
        """Create new session."""

    def get_session(self, session_id: str) -> FlowsheetSession:
        """Load existing session."""

    def add_stream(self, session_id: str, config: StreamConfig):
        """Add stream to session."""

    def add_unit(self, session_id: str, config: UnitConfig):
        """Add unit to session."""

    def add_connection(self, session_id: str, config: ConnectionConfig):
        """Add deferred connection."""

    def build_system(self, session_id: str, **kwargs) -> dict:
        """Compile session into QSDsan System."""
```

---

### `utils/topo_sort.py`

Topological sort with recycle edge handling (per Codex review).

```python
from typing import List, Set, Dict, Tuple
from dataclasses import dataclass

@dataclass
class TopoSortResult:
    """Result of topological sort."""
    unit_order: List[str]          # Execution order
    recycle_edges: List[Tuple[str, str]]  # Edges excluded from sort
    has_non_recycle_cycle: bool    # True if invalid cycle detected

def topological_sort(
    units: Dict[str, "UnitConfig"],
    connections: List["ConnectionConfig"],
    recycle_stream_ids: Set[str],
) -> TopoSortResult:
    """
    Topological sort of units with recycle handling.

    Algorithm:
    1. Build dependency graph from units and connections
    2. Mark edges involving recycle_stream_ids as "recycle edges"
    3. Exclude recycle edges from cycle detection
    4. Perform Kahn's algorithm on remaining DAG
    5. Return execution order

    Args:
        units: Dict of unit_id -> UnitConfig
        connections: List of ConnectionConfig
        recycle_stream_ids: Stream IDs known to be recycles

    Returns:
        TopoSortResult with unit order and detected recycle edges

    Example:
        # MLE flowsheet: A1 -> O1 -> MBR -> SP
        #                ^___________RAS__|
        units = {"A1": ..., "O1": ..., "MBR": ..., "SP": ...}
        connections = [
            {"from": "A1-0", "to": "O1"},
            {"from": "O1-0", "to": "MBR"},
            {"from": "MBR-1", "to": "SP"},
            {"from": "SP-0", "to": "A1-1"},  # RAS recycle
        ]
        recycle_stream_ids = {"RAS"}

        result = topological_sort(units, connections, recycle_stream_ids)
        # result.unit_order = ["A1", "O1", "MBR", "SP"]
        # result.recycle_edges = [("SP", "A1")]
    """
    # Implementation:
    # 1. Build adjacency list (excluding recycle edges)
    # 2. Compute in-degrees
    # 3. Kahn's algorithm with queue
    # 4. Detect cycles in non-recycle subgraph

def detect_recycle_streams(
    units: Dict[str, "UnitConfig"],
    connections: List["ConnectionConfig"],
) -> Set[str]:
    """
    Auto-detect potential recycle streams by finding back-edges.

    Uses DFS to find edges that point to already-visited nodes.
    These are candidates for recycle streams.
    """

def validate_flowsheet_connectivity(
    units: Dict[str, "UnitConfig"],
    streams: Dict[str, "StreamConfig"],
    connections: List["ConnectionConfig"],
) -> Tuple[List[str], List[str]]:
    """
    Validate flowsheet connectivity.

    Returns:
        (errors, warnings) - e.g., disconnected units, unused streams
    """
```

---

## Implementation Sequence

### Step 1: Core Infrastructure
- [ ] Create `core/unit_registry.py` with comprehensive UNIT_REGISTRY (35+ units)
- [ ] Create `utils/pipe_parser.py` with parse/resolve functions
- [ ] Create `utils/flowsheet_session.py` with session management
- [ ] Add `utils/topo_sort.py` for auto-inferring unit execution order
- [ ] Add unit tests for pipe parser and topo sort

### Step 2: MCP Tools
- [ ] Add `create_flowsheet_session` to server.py
- [ ] Add `create_stream` to server.py
- [ ] Add `create_unit` to server.py
- [ ] Add `connect_units` to server.py
- [ ] Add `list_units` to server.py
- [ ] Add `build_system` to server.py (synchronous compilation)
- [ ] Add `simulate_built_system` to server.py (background job via JobManager)

### Step 3: CLI Commands
- [ ] Add `flowsheet` command group to cli.py
- [ ] Add subcommands: new, add-stream, add-unit, connect, build, units, show, simulate
- [ ] Ensure CLI/MCP parity

### Step 4: build_system Implementation
- [ ] Implement topological sort for unit ordering (with override)
- [ ] Implement system compilation logic
- [ ] Handle process model assignment (ASM2d, mADM1, ADM1)
- [ ] Handle recycle stream wiring and detection
- [ ] Integrate with JobManager for background execution
- [ ] Add flowsheet diagram generation

### Step 5: Testing
- [ ] Create `tests/test_phase2.py`
- [ ] Test pipe parser edge cases (A1-0, 1-M1, stream names)
- [ ] Test session persistence across restarts
- [ ] Test topological sort with cycles (recycles)
- [ ] Test custom MLE variant construction
- [ ] Test integrated plant with junctions (ASM2d → ADM1)
- [ ] Integration test: build and simulate custom flowsheet

### Step 6: Documentation
- [ ] Update CLAUDE.md with Phase 2 tools and unit registry
- [ ] Add flowsheet construction examples
- [ ] Document unit parameter schemas and categories

---

## Files to Modify

| File | Changes |
|------|---------|
| `server.py` | Add 7 new tools (create_flowsheet_session, create_stream, create_unit, connect_units, build_system, list_units, simulate_built_system) |
| `cli.py` | Add `flowsheet` command group with 8 subcommands (new, add-stream, add-unit, connect, build, units, show, simulate) |
| `CLAUDE.md` | Update with Phase 2 documentation, unit registry reference |

## Files to Create

| File | Purpose |
|------|---------|
| `core/unit_registry.py` | Comprehensive unit type definitions (35+ units), validation |
| `utils/pipe_parser.py` | Parse `"A1-0"` → port references, full BioSTEAM syntax |
| `utils/flowsheet_session.py` | Session state management with disk persistence, mixed model support |
| `utils/topo_sort.py` | Topological sort with recycle edge handling |
| `tests/test_phase2.py` | Phase 2 unit and integration tests |

---

## Example: Custom MLE Variant

```python
# Create session
session = create_flowsheet_session(model_type="ASM2d")
# session_id = "abc123"

# Create influent stream
create_stream(
    session_id="abc123",
    stream_id="influent",
    flow_m3_d=4000,
    concentrations='{"S_F": 75, "S_A": 20, "S_NH4": 17, "S_PO4": 9, ...}',
)

# Create recycle streams (empty initially)
create_stream(session_id="abc123", stream_id="RAS", flow_m3_d=0,
              concentrations='{}', stream_type="recycle")

# Create units
create_unit(session_id="abc123", unit_type="CSTR", unit_id="A1",
            params='{"V_max": 1000, "aeration": null}',
            inputs='["influent", "RAS"]')

create_unit(session_id="abc123", unit_type="CSTR", unit_id="O1",
            params='{"V_max": 2000, "aeration": 2.3}',
            inputs='["A1-0"]')

create_unit(session_id="abc123", unit_type="CompletelyMixedMBR", unit_id="MBR",
            params='{"V_max": 500, "solids_capture_rate": 0.999}',
            inputs='["O1-0"]',
            outputs='["effluent", "retain"]')

create_unit(session_id="abc123", unit_type="Splitter", unit_id="SP",
            params='{"split": 0.8}',
            inputs='["MBR-1"]',
            outputs='["RAS", "WAS"]')

# Wire RAS back to A1 (deferred connection)
connect_units(session_id="abc123",
              connections='[{"from": "SP-0", "to": "A1-1"}]')

# Build system (compile only, no simulation)
build_system(session_id="abc123", system_id="custom_mle",
             recycle_streams='["RAS"]')

# Simulate with comprehensive reporting (separate tool per Codex review)
simulate_built_system(session_id="abc123", duration_days=15,
                      report=True, diagram=True)
```

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| QSDsan import time (~18s) | Use existing JobManager background job pattern |
| Complex recycle resolution | Require explicit recycle_streams in build_system |
| Unit compatibility errors | Validate model_type consistency in session |
| Port index out of bounds | Validate in pipe_parser before build |

---

## Validation Checklist

- [ ] Pipe parser handles all BioSTEAM notation variants (A1-0, 1-M1, U1-U2, U1-0-1-U2, tuple)
- [ ] Session persists across MCP reconnections
- [ ] Mixed model sessions work (ASM2d + ADM1 with junctions)
- [ ] Custom MLE variant produces valid simulation
- [ ] CLI and MCP tools produce identical results
- [ ] simulate_built_system reporting matches Phase 1 quality
- [ ] Error messages are actionable
- [ ] CLAUDE.md is updated with Phase 2 documentation
