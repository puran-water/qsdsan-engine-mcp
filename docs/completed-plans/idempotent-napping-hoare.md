# Universal QSDsan Simulation Engine MCP - Refactoring Plan

## Executive Summary

Refactor `anaerobic-design-mcp` into a **universal `qsdsan-engine-mcp`** that supports both anaerobic (mADM1) and aerobic (ASM2d) treatment simulation with ~6-8 stateless tools. All heuristic/sizing logic moves to Skills.

## Feasibility Assessment

| Question | Answer | Evidence |
|----------|--------|----------|
| **Is minimal server possible?** | **YES** | Working prototype exists in anaerobic-design-mcp |
| **Is flowsheet construction possible?** | **YES** | QSDsan provides full API: `SanUnit + WasteStream + System + pipe notation` |

### Codex Validation (via DeepWiki + gh CLI on QSD-Group/QSDsan)

| Question | Verdict | Notes |
|----------|---------|-------|
| Q1: Dynamic API | ✅ YES | Units/streams are Python objects; `create_example_system()` builds at runtime |
| Q2: Pipe Notation | ⚠️ PARSER NEEDED | `A1-0` is operator overloading; MCP must parse string → `unit.outs[idx]` |
| Q3: System Compilation | ⚠️ BUILD-THEN-CREATE | No incremental API; build all units, then `System(path=(...))` |
| Q4: MBR Implementation | ✅ YES | `CompletelyMixedMBR` subclasses `CSTR`, works with `suspended_growth_model` |
| Q5: State Conversion | ✅ YES | `ASM2dtomADM1`, `mADM1toASM2d` exist in `_junction.py` |
| Q6: Background Jobs | ✅ YES | Subprocess + polling valid; consider persistent worker for 18s+ imports |

**Note**: `AnaerobicCSTRmADM1` is a **custom class in this project** (`utils/qsdsan_reactor_madm1.py`) that extends QSDsan's `AnaerobicCSTR` to handle mADM1's 4 biogas species (CH4, CO2, H2, H2S). Port this class to the engine.

---

## Phase 1: Minimal Engine Implementation

### 1.1 Tool Surface (6 core tools)

| Tool | Purpose | Background Job? |
|------|---------|-----------------|
| `simulate_system` | Run any QSDsan system to steady state | **YES** |
| `get_job_status` | Check job progress | No |
| `get_job_results` | Retrieve simulation results | No |
| `list_templates` | List available flowsheet templates | No |
| `validate_state` | Validate PlantState against model | No |
| `convert_state` | ASM2d ↔ mADM1 state conversion | **YES** |

### 1.2 Flowsheet Templates (Pre-built)

**Anaerobic:**
- `anaerobic_cstr_madm1` - Single CSTR with mADM1 (port from current)

**Aerobic (MBR-based, ref: Pune_Nanded_WWTP):**
- `mle_mbr_asm2d` - MLE-MBR (anoxic → aerobic → CompletelyMixedMBR)
- `a2o_mbr_asm2d` - A2O-MBR (anaerobic → anoxic → aerobic → CompletelyMixedMBR)
- `ao_mbr_asm2d` - Simple A/O-MBR configuration

**Reference Implementation:** `/tmp/qsdsan-aerobic-simulation-example/Pune_Nanded_WWTP_updated.py`
- Uses `su.CSTR` with `aeration=None` (anoxic) and `aeration=DO_setpoint` (aerobic)
- Uses `su.CompletelyMixedMBR` for membrane separation
- Uses `pc.ASM2d(**kinetic_params)` for process model
- Dynamic simulation: `sys.simulate(t_span=(0,t), method='RK23')`

### 1.3 Data Model: PlantState

```python
@dataclass
class PlantState:
    model_type: Literal["mADM1", "ASM2d", "mASM2d", "ASM1"]
    flow_m3_d: float
    temperature_K: float
    concentrations: Dict[str, float]  # Component ID → kg/m³
    reactor_config: Dict[str, Any]    # V_liq, HRT/SRT, recycles
    metadata: Optional[Dict[str, Any]] = None
```

### 1.4 Dual Adapter Architecture (CLI + MCP)

Provide two first-class adapters on top of the same engine core:

```
┌─────────────────────────────────────────────────────────────────┐
│                     qsdsan-engine-mcp                           │
├─────────────────────────────────────────────────────────────────┤
│  CLI Adapter                    │  MCP Adapter                  │
│  ─────────────                  │  ───────────                  │
│  qsdsan-engine simulate \       │  simulate_system tool         │
│    --template mle_mbr_asm2d \   │  (FastMCP, stdio/HTTP/SSE)    │
│    --influent state.json \      │                               │
│    --json-out result.json       │  Best for:                    │
│                                 │  - Claude Desktop             │
│  Best for:                      │  - goose / MCP hosts          │
│  - Rapid dev/test cycles        │  - Permissioned execution     │
│  - Agent Skill CLI execution    │                               │
│  - --help as discoverable API   │                               │
└─────────────────────────────────┴───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Engine Core                              │
│  - templates/anaerobic/cstr.py                                  │
│  - templates/aerobic/mle_mbr.py                                 │
│  - core/plant_state.py                                          │
│  - models/madm1.py, asm2d.py                                    │
└─────────────────────────────────────────────────────────────────┘
```

**Benefits:**
- **No MCP restart during development**: CLI adapter allows rapid testing-feedback loops
- **Skills flexibility**: Agent Skills can choose CLI execution over MCP tool calls
- **Discoverability**: `--help` provides schema for CLI; MCP tool descriptions for MCP
- **Same engine**: Both adapters call identical engine functions

### 1.5 File Structure

```
~/mcp-servers/qsdsan-engine-mcp/
├── server.py                    # MCP Adapter (FastMCP, 6-8 tools)
├── cli.py                       # CLI Adapter (typer, same engine)
├── core/
│   ├── plant_state.py           # PlantState dataclass
│   ├── model_registry.py        # Model → component mapping
│   └── converters.py            # Junction-based conversions
├── templates/
│   ├── anaerobic/
│   │   └── cstr.py              # AnaerobicCSTR builder (mADM1)
│   ├── aerobic/
│   │   ├── mle_mbr.py           # MLE-MBR flowsheet (Pune reference)
│   │   ├── a2o_mbr.py           # A2O-MBR with EBPR
│   │   └── ao_mbr.py            # Simple A/O-MBR
├── utils/
│   ├── job_manager.py           # Background job pattern (KEEP)
│   ├── simulate_cli.py          # Unified CLI wrapper
│   ├── stream_analysis.py       # Universal result extraction
│   ├── qsdsan_loader.py         # Lazy component loading
│   └── path_utils.py            # WSL compatibility
├── models/
│   ├── madm1.py                 # mADM1 63-component (KEEP)
│   ├── asm2d.py                 # ASM2d wrapper
│   └── reactors.py              # AnaerobicCSTRmADM1 (KEEP)
├── reports/
│   ├── qmd_builder.py           # Quarto Markdown generator
│   └── templates/
├── jobs/                        # Job workspace
└── CLAUDE.md
```

### 1.5 Files to Keep (from anaerobic-design-mcp)

| File | Purpose |
|------|---------|
| `utils/job_manager.py` | Background job pattern (essential for MCP) |
| `utils/qsdsan_madm1.py` | mADM1 model definition |
| `utils/qsdsan_reactor_madm1.py` | Custom 4-species biogas reactor |
| `utils/qsdsan_simulation_sulfur.py` | Convergence-based simulation |
| `utils/stream_analysis_sulfur.py` | Result extraction |
| `utils/inoculum_generator.py` | CSTR startup initialization |
| `utils/path_utils.py` | WSL path normalization |
| `utils/output_formatters.py` | Token-efficient JSON formatting |

### 1.6 Files to Extract to Skills

| File | Target Skill |
|------|--------------|
| `tools/basis_of_design.py` | anaerobic-skill |
| `utils/heuristic_sizing.py` | anaerobic-skill |
| `utils/mixing_calculations.py` | anaerobic-skill |
| `utils/rheology.py` | anaerobic-skill |
| `tools/chemical_dosing.py` | anaerobic-skill |
| `reports/markdown_report.py` | design-report-skill |

---

## Phase 2: Flowsheet Construction (Dynamic)

### 2.1 Additional Tools

| Tool | Purpose |
|------|---------|
| `create_stream` | Create WasteStream with concentrations |
| `create_unit` | Instantiate SanUnit with parameters |
| `connect_units` | Wire units via pipe notation |
| `build_system` | Compile flowsheet into System |
| `list_units` | List available SanUnit types |

### 2.2 Unit Registry

```python
# VALIDATED by Codex + local codebase review
AVAILABLE_UNITS = {
    # Reactors
    "CSTR": {"models": ["ASM2d", "ASM1", "mASM2d"]},
    "AnaerobicCSTR": {"models": ["ADM1"]},           # Upstream QSDsan
    "AnaerobicCSTRmADM1": {"models": ["mADM1"]},     # Custom class (port from anaerobic-design-mcp)

    # Separators (MBR primary focus - Pune_Nanded pattern)
    "CompletelyMixedMBR": {"models": ["ASM2d", "mASM2d"]},  # Subclasses CSTR
    "FlatBottomCircularClarifier": {},
    "PrimaryClarifier": {},
    "IdealClarifier": {},

    # Junctions (state converters) - from qsdsan/sanunits/_junction.py
    "ASM2dtoADM1": {},      # For standard ADM1
    "ADM1toASM2d": {},      # For standard ADM1
    "ASM2dtomADM1": {},     # For mADM1 (63-component)
    "mADM1toASM2d": {},     # For mADM1 (63-component)

    # Utilities
    "Mixer": {},
    "MixTank": {},
    "Splitter": {},
}
```

### 2.4 Pipe Notation Parser (CRITICAL)

QSDsan's `M1-0` / `1-M1` syntax is **Python operator overloading**, not a string literal.
The MCP must implement a parser in `connect_units`:

```python
def resolve_port(port_str: str, unit_registry: dict) -> Stream:
    """
    Parse pipe notation string to QSDsan stream object.

    Examples:
        "A1-0" → unit_registry["A1"].outs[0]  # First output of A1
        "1-M1" → unit_registry["M1"].ins[1]   # Second input of M1
    """
    if "-" in port_str:
        parts = port_str.split("-")
        if parts[0].isdigit():  # Input: "1-M1"
            idx, unit_id = int(parts[0]), parts[1]
            return unit_registry[unit_id].ins[idx]
        else:  # Output: "A1-0"
            unit_id, idx = parts[0], int(parts[1])
            return unit_registry[unit_id].outs[idx]
    raise ValueError(f"Invalid port notation: {port_str}")
```

### 2.3 Flowsheet Construction API

```python
# 1. Create units
create_unit(unit_type="CSTR", unit_id="A1",
            params={"V_max": 1000, "aeration": None},
            model="ASM2d", inputs=["influent"])

create_unit(unit_type="CSTR", unit_id="O1",
            params={"V_max": 2000, "aeration": 2.0},
            model="ASM2d", inputs=["A1-0"])

create_unit(unit_type="FlatBottomCircularClarifier", unit_id="C1",
            params={"surface_area": 500, "height": 4},
            inputs=["O1-0"])

# 2. Add recycle connections
connect_units(connections=[
    {"from": "C1-1", "to": "A1-1"}  # RAS to anoxic
])

# 3. Compile system
build_system(system_id="mle_custom",
             unit_ids=["A1", "O1", "C1"],
             recycle_streams=["C1-1"])
```

---

## Migration Path

### Step 1: Create qsdsan-engine-mcp skeleton (Day 1)
- [ ] Copy `utils/job_manager.py`, `utils/path_utils.py`
- [ ] Create `core/plant_state.py` with PlantState dataclass
- [ ] Create minimal `server.py` with 6 tools (stubs)

### Step 2: Port anaerobic simulation (Day 2-3)
- [ ] Copy `utils/qsdsan_*.py` model files
- [ ] Create `templates/anaerobic/cstr.py` builder
- [ ] Create unified `utils/simulate_cli.py`
- [ ] Test with existing anaerobic workflow

### Step 3: Add aerobic MBR templates (Day 4-6)
- [ ] Create ASM2d component loader
- [ ] Create `templates/aerobic/mle_mbr.py` using Pune_Nanded as reference
- [ ] Add CompletelyMixedMBR integration
- [ ] Create aerobic stream analysis functions
- [ ] Add A2O-MBR and A/O-MBR templates

### Step 4: Add state converters (Day 7)
- [ ] Implement `convert_state` using Junction units
- [ ] Test ASM2d → mADM1 conversion (WAS to digester)
- [ ] Test mADM1 → ASM2d conversion (digester effluent to sidestream)

### Step 5: Create Quarto report output (Day 8)
- [ ] Create `reports/qmd_builder.py`
- [ ] Port Jinja2 templates to *.qmd format
- [ ] Add Obsidian frontmatter

### Step 6: Extract Skills (Day 9-10)
- [ ] Create `anaerobic-skill` with heuristic scripts
- [ ] Create `aerobic-design-skill` with sizing scripts
- [ ] Update CLAUDE.md for new architecture

### Step 7 (Phase 2): Flowsheet construction (Day 11-14)
- [ ] Implement unit registry with validation
- [ ] Create `create_stream`, `create_unit`, `connect_units`, `build_system` tools
- [ ] Create `list_units` for SanUnit enumeration
- [ ] Test arbitrary flowsheet construction (custom MLE variant)
- [ ] Document API in CLAUDE.md

---

## Key Design Decisions

### Q1: Tool Surface
**Decision**: 6 core tools (stateless, explicit state passing)
**Rationale**: Matches puran-water-agent vision, enables parallel design exploration

### Q2: Aerobic Implementation
**Decision**: Template-based MBR in Phase 1, dynamic construction in Phase 2
**Rationale**: Templates are safer to validate; dynamic construction enables custom flowsheets

### Q3: PlantState Model
**Decision**: Single dataclass with `model_type` discriminator
**Rationale**: Template builders validate required keys per model type

### Q4: Background Jobs
**Decision**: REQUIRED for all heavy operations (simulate, convert)
**Rationale**: Prevents MCP STDIO blocking during 18+ second QSDsan loads

---

## Critical Files to Modify

### anaerobic-design-mcp → qsdsan-engine-mcp
- `server.py` - Rewrite with 6-8 stateless tools
- `utils/simulate_cli.py` - Unify anaerobic + aerobic CLI

### New Files to Create
- `core/plant_state.py` - PlantState dataclass
- `core/model_registry.py` - Model type → components
- `templates/aerobic/mle_mbr.py` - MLE-MBR flowsheet builder (Pune reference)
- `templates/aerobic/a2o_mbr.py` - A2O-MBR flowsheet builder
- `templates/aerobic/ao_mbr.py` - A/O-MBR flowsheet builder
- `reports/qmd_builder.py` - Quarto Markdown generator

### Reference Files
- `/tmp/qsdsan-aerobic-simulation-example/Pune_Nanded_WWTP_updated.py` - Primary MBR reference
- `qsdsan.sanunits.CompletelyMixedMBR` - MBR separation unit
- `qsdsan.sanunits.CSTR` - Reactor with ASM2d model
- `qsdsan.processes.ASM2d` - Aerobic process model

---

## User Decisions (Confirmed)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Repository strategy | **New repo** | Create `qsdsan-engine-mcp`, keep `anaerobic-design-mcp` until migration complete |
| Output format | ***.qmd only** | Quarto Markdown with Obsidian frontmatter |
| Report location | **In engine** | Single call returns simulation + report |
| Aerobic templates | **MLE-MBR + A2O-MBR + A/O-MBR** | Three most common MBR configurations (Pune reference) |

---

## Implementation Checklist

### Phase 1A: Foundation (Days 1-2)
- [ ] Create new `qsdsan-engine-mcp` repository in `~/mcp-servers/`
- [ ] Copy core utilities from `anaerobic-design-mcp`:
  - `utils/job_manager.py`
  - `utils/path_utils.py`
  - `utils/qsdsan_loader.py`
- [ ] Create `core/plant_state.py` with PlantState dataclass
- [ ] Create `core/model_registry.py` for component management
- [ ] Create **dual adapters**:
  - `server.py` - MCP adapter (FastMCP, 6-8 tools)
  - `cli.py` - CLI adapter (typer, `--json-out`, `--help` discoverability)
- [ ] Ensure both adapters call same engine core functions

### Phase 1B: Anaerobic Port (Days 3-4)
- [ ] Copy mADM1 model files:
  - `utils/qsdsan_madm1.py`
  - `utils/qsdsan_reactor_madm1.py`
  - `utils/qsdsan_simulation_sulfur.py`
  - `utils/stream_analysis_sulfur.py`
  - `utils/inoculum_generator.py`
- [ ] Create `templates/anaerobic/cstr.py` builder
- [ ] Create unified `utils/simulate_cli.py`
- [ ] Test with existing anaerobic workflow
- [ ] Validate output matches current MCP

### Phase 1C: Aerobic MBR Templates (Days 5-8)
- [ ] Create `models/asm2d.py` component loader
- [ ] Create `templates/aerobic/mle_mbr.py` (ref: Pune_Nanded_WWTP):
  - Pre-anoxic zone (CSTR, aeration=None, suspended_growth_model=asm2d)
  - Aerobic zone (CSTR, aeration=DO_setpoint, DO_ID='S_O2')
  - CompletelyMixedMBR (solids_capture_rate, pumped_flow)
  - Internal recycle (IR) + RAS streams via Splitter
- [ ] Create `templates/aerobic/a2o_mbr.py`:
  - Anaerobic zone (CSTR, aeration=None) + MLE-MBR configuration
  - EBPR support via mASM2d kinetics
- [ ] Create `templates/aerobic/ao_mbr.py`:
  - Simple anoxic-oxic-MBR (no anaerobic zone)
- [ ] Create aerobic stream analysis functions
- [ ] Add test cases for each template

### Phase 1D: State Converters (Day 9)
- [ ] Implement `convert_state` tool using QSDsan Junction units
- [ ] Test ASM2d → mADM1 conversion (WAS to digester)
- [ ] Test mADM1 → ASM2d conversion (digester effluent to sidestream)

### Phase 1E: Quarto Reports (Day 10)
- [ ] Create `reports/qmd_builder.py`:
  - Obsidian frontmatter generation
  - Performance metrics table
  - Inhibition analysis section
  - Stream comparison table
- [ ] Port Jinja2 templates to *.qmd format
- [ ] Test report rendering with Quarto CLI

### Phase 1F: Skills Extraction (Days 11-12)
- [ ] Create `~/skills/anaerobic-skill/`:
  - Extract heuristic_sizing.py
  - Extract mixing_calculations.py
  - Extract basis_of_design.py
  - Extract chemical_dosing.py
  - Create SKILL.md from CLAUDE.md
- [ ] Create `~/skills/aerobic-design-skill/`:
  - Extract sizing calculations
  - Extract aeration requirements
  - Create SKILL.md for aerobic workflows
- [ ] Update `~/puran-water-agent/README.md`

### Phase 2: Flowsheet Construction (Days 13-16)
- [ ] Implement unit registry with validation
- [ ] Create `create_stream` tool for WasteStream creation
- [ ] Create `create_unit` tool for SanUnit instantiation
- [ ] Create `connect_units` tool for pipe notation wiring
- [ ] Create `build_system` tool to compile flowsheet into System
- [ ] Create `list_units` tool to enumerate available SanUnit types
- [ ] Test arbitrary flowsheet construction (custom MLE variant)
- [ ] Document flowsheet construction API in CLAUDE.md

---

## Validation Checklist

- [ ] mADM1 simulation matches existing anaerobic-design-mcp (±1% biogas)
- [ ] MLE-MBR template produces valid effluent (TN < 10 mg/L at proper SRT)
- [ ] A2O-MBR template achieves EBPR (TP < 1 mg/L with proper configuration)
- [ ] MBR solids capture rate > 99.9% (membrane separation)
- [ ] State conversion preserves mass balance (±0.1%)
- [ ] *.qmd reports render correctly with Quarto
- [ ] Background jobs complete without MCP timeout
- [ ] Phase 2 flowsheet construction creates valid System objects

### Dual Adapter Validation
- [ ] CLI `qsdsan-engine simulate --help` shows all options
- [ ] CLI `qsdsan-engine simulate --template mle_mbr_asm2d --json-out result.json` works
- [ ] MCP `simulate_system` tool returns identical results to CLI
- [ ] Skills can invoke CLI directly without MCP server restart
- [ ] Both adapters share same engine core (no code duplication)
