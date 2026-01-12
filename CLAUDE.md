# QSDsan Engine MCP - Development Context

## Project Goal
Universal wastewater simulation engine supporting anaerobic (mADM1, 63 components) and aerobic (ASM2d, 19 components) treatment via dual adapters (MCP + CLI).

---

## Development Plans

| Plan | Path | Status |
|------|------|--------|
| Master Plan (Phase 1) | `docs/completed-plans/idempotent-napping-hoare.md` | ✅ Complete |
| Phase 2 Plan | `docs/completed-plans/bright-snacking-prism.md` | ✅ Complete |
| Phase 2B Bug Fixes | `docs/completed-plans/phase2b-bug-fixes.md` | ✅ Complete |
| Phase 3 Plan | `docs/completed-plans/phase3-llm-accessibility.md` | ✅ Complete |
| Phase 4 Plan | `docs/completed-plans/phase4-production-readiness.md` | ✅ Complete |
| Phase 5 Plan | `docs/completed-plans/phase5-report-integration.md` | ✅ Complete |
| Phase 6 Plan | `docs/completed-plans/phase6-production-hardening.md` | ✅ Complete |

**Master Plan** covers Phase 1A-1F: Foundation, mADM1 simulation, aerobic MBR templates, state converters, Quarto reports, and skills extraction.

**Phase 2 Plan** covers flowsheet construction: dynamic unit creation, pipe notation parsing, session management, topological sort with recycle handling, and system compilation.

**Phase 2B Bug Fixes** covers model-aware analysis dispatch, ASM1 support, Unicode arrow replacement, and biogas detection improvements.

**Phase 3 Plan** covers LLM accessibility: native type parameters, session mutation, deep introspection, validation warnings, and discoverability tools.

**Phase 4 Plan** covers production readiness: critical bug fixes, packaging configuration, report time-series plots, per-unit analysis, mass/charge balance validation, and test hygiene.

**Phase 5 Plan** covers report integration: schema normalization between flowsheet outputs and report templates, artifact location documentation, dead code removal, and comprehensive test coverage.

**Phase 6 Plan** covers production hardening: validate_state mass balance implementation, CLI parameter pass-through fixes, fail-fast flowsheet compilation, dead code removal, MCP tool contract tests, and JobManager tests.

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1A | Foundation (PlantState, registries, dual adapters) | ✅ Complete |
| 1B | Anaerobic mADM1 simulation | ✅ Complete |
| 1C | Aerobic MBR templates (MLE, A/O, A2O) | ✅ Complete |
| 1D | State converters (ASM2d <-> mADM1) | ✅ Complete |
| 1E | Quarto reports + flowsheet diagrams | ✅ Complete |
| 1F | Skills extraction | ✅ Complete |
| **2** | **Flowsheet Construction** | ✅ **COMPLETE** |
| **3** | **LLM Accessibility Enhancement** | ✅ **COMPLETE** |
| **4** | **Production Readiness** | ✅ **COMPLETE** |
| **5** | **Report Integration & Schema Normalization** | ✅ **COMPLETE** |
| **6** | **Production Hardening** | ✅ **COMPLETE** |

**Phase 1 Validation:** Codex-verified 2026-01-06 - all files present, 27 tests passing
**Phase 2 Validation:** Codex-verified 2026-01-08 - 118 tests passing (27 Phase 1 + 91 Phase 2), all plan items complete
**Phase 2B Bug Fixes:** Codex-verified 2026-01-08 - 7 bugs fixed, Unicode arrows replaced with ASCII
**Phase 3 Validation:** Codex-verified 2026-01-09 - 163 tests passing (27 Phase 1 + 91 Phase 2 + 45 Phase 3)
**Phase 4 Validation:** Codex-verified 2026-01-11 - 201 tests passing, all 12 tasks complete
**Phase 5 Validation:** Codex-verified 2026-01-11 - 219 tests passing, all 6 tasks complete, schema normalization implemented
**Phase 6 Validation:** Codex-verified 2026-01-12 - 247 tests passing, all 8 tasks complete, fail-fast compilation and test coverage gaps addressed

---

## Phase 2: Flowsheet Construction (COMPLETE)

### Overview
Dynamic flowsheet construction via MCP/CLI tools, allowing users to build custom treatment trains beyond pre-built templates.

### New MCP Tools (9 tools)

| Tool | Purpose | Status |
|------|---------|--------|
| `create_flowsheet_session` | Create new flowsheet session | ✅ |
| `create_stream` | Create WasteStream with concentrations | ✅ |
| `create_unit` | Instantiate SanUnit with parameters | ✅ |
| `connect_units` | Wire units via pipe notation | ✅ |
| `build_system` | Compile flowsheet into QSDsan System | ✅ |
| `list_units` | Enumerate available SanUnit types | ✅ |
| `simulate_built_system` | Run simulation on compiled flowsheet | ✅ |
| `get_flowsheet_session` | Get session details | ✅ |
| `list_flowsheet_sessions` | List all sessions | ✅ |

### CLI Commands

```bash
# Session management
python cli.py flowsheet new --model ASM2d
python cli.py flowsheet list
python cli.py flowsheet show --session abc123
python cli.py flowsheet delete --session abc123

# Build flowsheet
python cli.py flowsheet add-stream --session abc123 --id influent \
    --flow 4000 --concentrations '{"S_F": 75, "S_A": 20}'
python cli.py flowsheet add-unit --session abc123 --type CSTR --id A1 \
    --params '{"V_max": 1000}' --inputs '["influent"]'
python cli.py flowsheet connect --session abc123 \
    --connections '[{"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}]'
python cli.py flowsheet build --session abc123 --recycles '["RAS"]'
python cli.py flowsheet simulate --session abc123 --duration 15 --report
# Or simulate using system_id from build_system output:
python cli.py flowsheet simulate --system-id custom_mle --duration 15 --report

# Unit discovery
python cli.py flowsheet units --model ASM2d --json-out
python cli.py flowsheet units --category reactor
```

### Unit Registry (49 units)

```python
from core.unit_registry import list_available_units, get_unit_spec

# Categories: reactor, separator, clarifier, sludge, pump, junction, utility, pretreatment
# List units compatible with ASM2d
units = list_available_units(model_type="ASM2d")
# Get unit specification
spec = get_unit_spec("CSTR")
```

Key units by category:
- **Reactors:** CSTR, AnaerobicCSTR, AnaerobicCSTRmADM1, PFR, MixTank, Lagoon, InternalCirculationRx, ActivatedSludgeProcess, AnaerobicDigestion
- **Separators:** CompletelyMixedMBR, AnMBR, PolishingFilter, MembraneDistillation, MembraneGasExtraction
- **Clarifiers:** FlatBottomCircularClarifier, PrimaryClarifier, PrimaryClarifierBSM2, IdealClarifier, Sedimentation
- **Sludge:** Thickener, Centrifuge, BeltThickener, SludgeDigester, Incinerator, SludgePasteurization, DryingBed, LiquidTreatmentBed
- **Junctions:** ASM2dtoADM1, ADM1toASM2d, mADM1toASM2d, ASM2dtomADM1, Junction, ASMtoADM, ADMtoASM, ADM1ptomASM2d, mASM2dtoADM1p
- **Utilities:** Splitter, Mixer, Tank, StorageTank, DynamicInfluent

### Pipe Notation Parser

Full BioSTEAM syntax support:

```python
from utils.pipe_parser import parse_port_notation

# Output notation: "A1-0" -> unit A1, output port 0
# Input notation: "1-M1" -> unit M1, input port 1
# Direct: "U1-U2" -> U1.outs[0] -> U2.ins[0]
# Explicit: "U1-0-1-U2" -> U1.outs[0] -> U2.ins[1]
# Stream: "influent" -> named stream
```

### Flowsheet Session Management

Sessions persist to `jobs/flowsheets/{session_id}/session.json`:

```python
from utils.flowsheet_session import FlowsheetSessionManager, StreamConfig, UnitConfig

manager = FlowsheetSessionManager()
session = manager.create_session(model_type="ASM2d")
manager.add_stream(session.session_id, StreamConfig(...))
manager.add_unit(session.session_id, UnitConfig(...))
```

### Topological Sort with Recycle Handling

```python
from utils.topo_sort import topological_sort

result = topological_sort(
    units=session.units,
    connections=session.connections,
    recycle_stream_ids={"RAS"},  # Exclude from cycle detection
)
# result.unit_order = ["A1", "O1", "MBR", "SP"]
# result.recycle_edges = [("SP", "A1")]
```

### Complete Example: Custom MLE Flowsheet

```python
# Via CLI
python cli.py flowsheet new --model ASM2d --id my_mle
python cli.py flowsheet add-stream --session my_mle --id influent \
    --flow 4000 --concentrations '{"S_F": 75, "S_A": 20, "S_NH4": 17, "S_PO4": 9}'
python cli.py flowsheet add-unit --session my_mle --type CSTR --id A1 \
    --params '{"V_max": 1000}' --inputs '["influent"]'
python cli.py flowsheet add-unit --session my_mle --type CSTR --id O1 \
    --params '{"V_max": 2000, "aeration": 2.3}' --inputs '["A1-0"]'
python cli.py flowsheet add-unit --session my_mle --type CompletelyMixedMBR --id MBR \
    --params '{"V_max": 500}' --inputs '["O1-0"]'
python cli.py flowsheet add-unit --session my_mle --type Splitter --id SP \
    --params '{"split": 0.8}' --inputs '["MBR-1"]'
python cli.py flowsheet connect --session my_mle \
    --connections '[{"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}]'
python cli.py flowsheet build --session my_mle --recycles '["RAS"]'
```

---

## Phase 3: LLM Accessibility Enhancement (COMPLETE)

### Overview
Phase 3 makes the MCP server reliably usable by autonomous agents through:
- Native type parameters (Dict/List instead of JSON strings)
- Session mutation operations (update, delete, clone)
- Deep introspection for full flowsheet visibility
- Validation warnings surfaced in tool responses
- Discoverability tools for component/unit discovery
- Engineering-grade result retrieval

### New MCP Tools (Phase 3 - 11 tools)

| Tool | Purpose |
|------|---------|
| `update_stream` | Patch stream properties (flow, concentrations, etc.) |
| `update_unit` | Patch unit parameters |
| `delete_stream` | Remove stream from session |
| `delete_unit` | Remove unit and its connections |
| `delete_connection` | Remove specific connection |
| `clone_session` | Fork session for experimentation |
| `delete_session` | Remove entire session |
| `get_flowsheet_timeseries` | Retrieve time-series data from simulation |
| `get_model_components` | Discover valid component IDs for a model |
| `validate_flowsheet` | Pre-compilation validation |
| `suggest_recycles` | Detect potential recycle streams |
| `get_artifact` | Retrieve diagram/report content directly |

### CLI Commands (Phase 3 - 7 new)

```bash
# Mutation commands
python cli.py flowsheet update-stream --session abc123 --id influent --flow 5000
python cli.py flowsheet update-unit --session abc123 --id A1 --params '{"V_max": 1500}'
python cli.py flowsheet delete-stream --session abc123 --id RAS --force
python cli.py flowsheet delete-unit --session abc123 --id SP
python cli.py flowsheet delete-connection --session abc123 --from SP-0 --to A1-1
python cli.py flowsheet clone --session abc123 --new-id abc123_v2
```

### Session Mutation API

```python
from utils.flowsheet_session import FlowsheetSessionManager

manager = FlowsheetSessionManager()

# Update stream (patch-style, merges concentrations)
manager.update_stream(session_id, "influent", {"flow_m3_d": 5000})

# Update unit (patch-style, merges params)
manager.update_unit(session_id, "A1", {"params": {"V_max": 1500}})

# Delete with dependency checks
manager.delete_stream(session_id, "RAS", force=True)  # force removes from unit inputs
manager.delete_unit(session_id, "SP")  # also removes connections

# Clone for experimentation
result = manager.clone_session(session_id, new_session_id="experiment")
```

### Deep Introspection

`get_flowsheet_session` now returns full configuration details:

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
            "model_type": null
        }
    },
    "connections": [
        {"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}
    ]
}
```

### Concentration Units

Streams now support explicit concentration units:

```python
# Default is mg/L (practitioner standard)
config = StreamConfig(
    stream_id="influent",
    concentrations={"S_F": 75},  # mg/L
    concentration_units="mg/L",  # or "kg/m3"
)
```

### Validation Warnings

`create_stream` now returns warnings for unknown components:

```python
result = await create_stream(
    session_id="abc123",
    stream_id="influent",
    concentrations={"S_F": 75, "S_FAKE": 100},  # S_FAKE is invalid
)
# result["warnings"] = ["Unknown component IDs (ignored by QSDsan): ['S_FAKE']"]
```

### Discoverability Tools

```python
# Get valid component IDs for a model
result = await get_model_components(model_type="ASM2d")
# Returns list of components with names, categories, typical values

# Pre-validate flowsheet before building
result = await validate_flowsheet(session_id="abc123")
# Returns errors, warnings, detected recycles

# Suggest recycle streams
result = await suggest_recycles(session_id="abc123")
# Returns detected cycles with suggested recycle points
```

### Time-Series Retrieval

```python
# Track streams during simulation
result = await simulate_built_system(
    session_id="abc123",
    track=["effluent"],  # Track these streams
)

# Later, retrieve time-series
ts = await get_flowsheet_timeseries(
    job_id=result["job_id"],
    stream_ids=["effluent"],
    components=["S_NH4", "S_NO3"],
    downsample_factor=2,
)
# ts["time"] = [0, 0.5, 1.0, ...]
# ts["streams"]["effluent"]["S_NH4"] = [17.0, 15.2, ...]
```

---

## Artifact Locations

| Invocation | Output Directory | Artifact Access |
|------------|------------------|-----------------|
| MCP `simulate_built_system` | `jobs/{job_id}/` | `get_artifact(job_id, ...)` |
| CLI `flowsheet simulate` (default) | `output/{session_id}/` | Direct file path |
| CLI `flowsheet simulate --output-dir X` | `X/` | Direct file path |

### Report Artifacts
- `flowsheet.svg`: System diagram (generated by `utils.diagram.save_system_diagram()`)
- `report.qmd`: Quarto Markdown report
- `timeseries.json`: Time-series data with schema `{time: [], streams: {}, time_units: "days"}`

### Result Normalization

Reports use `normalize_results_for_report()` to map flowsheet simulation outputs to template expectations:

| Source | Target | Default |
|--------|--------|---------|
| `results["diagram_path"]` | `results["flowsheet"]["diagram_path"]` | `None` |
| `results["timeseries_path"]` | `results["timeseries"]` (loaded JSON) | `{}` |
| `results["metadata"]["solver"]["duration_days"]` | `results["duration_days"]` | `0` |
| `results["metadata"]["solver"]["method"]` | `results["method"]` | `"RK23"` |
| `results["effluent_quality"]` | `results["effluent"]` (flattened) | `{}` |
| `results["removal_efficiency"]` | `results["performance"]` | `{}` |
| `results["flowsheet"]` = None | `results["flowsheet"]` = `{}` | `{}` |

The normalizer is called in `_prepare_anaerobic_data()` and `_prepare_aerobic_data()` at the start of report generation.

---

## Key Technical Notes

### Component IDs
| Model | Count | Key IDs |
|-------|-------|---------|
| mADM1 | 63 | SRB: `X_hSRB`, `X_aSRB`, `X_pSRB`, `X_c4SRB` (NOT `X_SRB`); Sulfide: `S_IS` (NOT `S_H2S`); Iron: `S_Fe3` (NOT `S_Fe`) |
| ASM2d | 19 | `X_MeOH` = Metal-hydroxides, `X_MeP` = Metal-phosphates (chemical P removal) |
| ASM1 | 13 | `S_I`, `S_S`, `X_I`, `X_S`, `X_BH`, `X_BA`, `X_P`, `S_O`, `S_NO`, `S_NH`, `S_ND`, `X_ND`, `S_ALK` |

### Template API (Phase 1)
```python
# Anaerobic
from templates.anaerobic.cstr import build_and_run
result = build_and_run(influent_state={...}, reactor_config={...})

# Aerobic (mle_mbr, ao_mbr, a2o_mbr)
from templates.aerobic.mle_mbr import build_and_run
result = build_and_run(influent_state={...}, reactor_config={...}, duration_days=15.0)
```

### CLI Commands (Phase 1)
```bash
python cli.py templates --json-out          # List 4 templates
python cli.py validate -s state.json -m ASM2d
python cli.py simulate -t mle_mbr_asm2d -i state.json -d 15
python cli.py simulate -t anaerobic_cstr_madm1 -i state.json -d 30 --report
```

### Flowsheet Diagrams
Templates automatically generate flowsheet diagrams when `output_dir` is provided:
- Uses QSDsan/biosteam's `System.diagram()` method
- Outputs SVG format with unit operations and stream labels
- Mass balance tables with COD/TN/TP for all streams
- Integrated into QMD reports via `utils/diagram.py`

```python
from utils.diagram import save_system_diagram, generate_mass_balance_table
diagram_path = save_system_diagram(system, output_path="flowsheet", format="svg")
streams_data = generate_mass_balance_table(system, model_type="mADM1")
```

---

## File Structure

```
qsdsan-engine-mcp/
├── server.py              # MCP Adapter (FastMCP) - 29 tools total
├── cli.py                 # CLI Adapter (typer) - flowsheet command group
├── requirements.txt       # [Phase 4] Runtime dependencies
├── core/
│   ├── plant_state.py     # PlantState dataclass
│   ├── model_registry.py  # Component definitions (mADM1: 63, ASM2d: 19)
│   ├── template_registry.py
│   ├── converters.py      # State conversion + mass/charge validation [Phase 4]
│   ├── junction_components.py  # Component alignment for junctions
│   ├── junction_units.py  # Custom junction classes with _compile_reactions override
│   └── unit_registry.py   # [Phase 2] 49 SanUnit specs with validation
├── templates/
│   ├── anaerobic/cstr.py  # mADM1 CSTR with diagram generation
│   └── aerobic/           # mle_mbr.py, ao_mbr.py, a2o_mbr.py (all with diagrams)
├── models/
│   ├── madm1.py           # 63-component process model
│   ├── asm2d.py           # 19-component (wraps QSDsan pc.create_asm2d_cmps)
│   ├── reactors.py        # AnaerobicCSTRmADM1
│   └── sulfur_kinetics.py # SRB processes
├── utils/
│   ├── simulate_madm1.py  # Simulation wrapper
│   ├── qsdsan_loader.py   # Async component loading
│   ├── diagram.py         # Flowsheet diagram & mass balance
│   ├── pipe_parser.py     # [Phase 2] BioSTEAM notation parser
│   ├── flowsheet_session.py # [Phase 2] Session state management
│   ├── flowsheet_builder.py # System compilation + per-unit analysis [Phase 4]
│   ├── topo_sort.py       # [Phase 2] Topological sort with recycle handling
│   ├── report_plots.py    # [Phase 4] Time-series plot generation
│   └── stream_analysis.py # Result extraction
├── reports/
│   ├── qmd_builder.py     # Quarto Markdown generator (Jinja2) + generate_report()
│   └── templates/         # anaerobic_report.qmd, aerobic_report.qmd (with per-unit sections)
├── jobs/
│   └── flowsheets/        # [Phase 2] Session storage directory
└── tests/
    ├── test_phase1.py     # 27 tests
    ├── test_phase2.py     # 121 tests (flowsheet + report + per-unit)
    ├── test_phase3.py     # 53 tests (LLM accessibility + MCP behavior)
    └── test_converters.py # [Phase 4] 12 validation tests
```

---

## Reference Files

| Reference | Path |
|-----------|------|
| Aerobic MBR Example | `/tmp/qsdsan-aerobic-simulation-example/Pune_Nanded_WWTP_updated.py` |
| Anaerobic Skill | `/home/hvksh/skills/anaerobic-skill/` |
| Aerobic Skill | `/home/hvksh/skills/aerobic-design-skill/` |

> **Note:** Completed development plans are stored in `docs/completed-plans/` and documented in the [Development Plans](#development-plans) section.

---

## Known Limitations

1. **QSDsan Import Time:** ~18s cold start. Use `utils/qsdsan_loader.py` for async loading.

2. **Session Storage:** Sessions are stored in `jobs/flowsheets/` by default. Set `QSDSAN_ENGINE_SESSIONS_DIR` environment variable to customize the storage location.

3. **X_AUT -> X_h2 Property Alignment:** In `core/junction_components.py`, the `COMPONENT_ALIGNMENT` map pairs `X_AUT` with `X_h2` for **property alignment only** (matching i_N, i_P, measured_as). The actual mass conversion uses QSDsan's inherited `ASM2dtomADM1._compile_reactions()` which properly converts X_AUT → X_pr/X_li/X_ch (proteins/lipids/carbs) based on `frac_deg` and N/P balance. Our custom junction classes (`junction_units.py`) inherit from QSDsan's standard junctions to preserve this biochemically accurate conversion logic.

---

## State Conversion (ASM2d <-> mADM1)

Junction units and state converters are now fully implemented, enabling mixed-model flowsheets.

### PlantState Conversion

```python
from core.plant_state import PlantState, ModelType
from core.converters import convert_state, convert_asm2d_to_madm1, convert_madm1_to_asm2d

# Convert WAS from MBR (ASM2d) to digester feed (mADM1)
asm_state = PlantState(
    model_type=ModelType.ASM2D,
    concentrations={'X_H': 5000, 'X_S': 2000, 'S_NH4': 30, 'S_PO4': 10},
    flow_m3_d=100,
    temperature_K=293.15,
)
adm_state, metadata = convert_asm2d_to_madm1(asm_state)
print(f"COD balance: {metadata['balance']['cod_error']:.2%}")

# Convert digestate back to ASM2d for sidestream treatment
asm_return, meta = convert_madm1_to_asm2d(adm_state)
```

### Custom Junction Units

Custom junction classes that work with our 63-component ModifiedADM1:

```python
from core.junction_units import ASM2dtomADM1_custom, mADM1toASM2d_custom
from core.converters import create_junction_unit

# Factory function
junction = create_junction_unit('asm2d_to_madm1', unit_id='J1')
junction.adm1_model = modified_adm1_model  # Accepts ModifiedADM1

# Direct instantiation
j2 = mADM1toASM2d_custom(ID='J2')
```

### Component Mapping

```python
from core.junction_components import get_asm2d_to_madm1_mapping, get_madm1_to_asm2d_mapping

# ASM2d -> mADM1
# S_ALK -> S_IC, S_NH4 -> S_IN, S_PO4 -> S_IP
# S_A -> S_ac, S_F -> S_su, X_S -> X_pr/X_li/X_ch (split)
# X_H -> biomass groups (degradable fraction)

# mADM1 -> ASM2d
# S_IC -> S_ALK, S_IN -> S_NH4, S_IP -> S_PO4
# S_ac/S_va/S_bu/S_pro -> S_A
# X_ch/X_pr/X_li -> X_S
# All biomass (X_su, X_aa, etc.) -> X_H
```

---

## Testing

```bash
# Run all tests (201 total)
python -m pytest tests/ -v

# Phase 1 tests only (27)
python -m pytest tests/test_phase1.py -v

# Phase 2 tests only (121 - includes report/per-unit tests)
python -m pytest tests/test_phase2.py -v

# Phase 3 tests only (53 - includes MCP behavior tests)
python -m pytest tests/test_phase3.py -v

# Converter validation tests (12)
python -m pytest tests/test_converters.py -v

# Skip slow simulation tests
python -m pytest tests/ -v -m "not slow"
```

---

## Phase 2B Bug Fixes (2026-01-08)

Seven bugs were identified during mADM1 CSTR workflow testing and fixed:

| Bug | Issue | Fix |
|-----|-------|-----|
| #2 | Wrong analysis function for mADM1 | Model-aware dispatch: `analyze_liquid_stream` for anaerobic, `analyze_aerobic_stream` for aerobic |
| #3 | Biogas stream auto-detection fails | Check digester outputs + expanded gas component IDs |
| #4 | `calculate_removal_efficiency` wrong args | Extract COD/TSS from streams first, then pass concentrations |
| #5 | ASM1 uses ASM2d in junctions | Model-type check: use `pc.ASM1()` when `model_type == "ASM1"` |
| #6 | Converters treat ASM1 as ASM2d | Separate ASM1 component loading + fallback coefficients |
| #7 | mADM1 mass-balance wrong IDs | Correct IDs: `S_IS` not `S_H2S`, `X_ch/X_pr/X_li` not `X_c` |

### Additional Fixes (Codex Review)
- **Enum normalization**: `getattr(model_type, 'value', model_type)` handles both strings and `ModelType` enums
- **Unicode arrows replaced**: All `->` and `↔` replaced with `->` and `<->` for Windows cp1252 compatibility
- **Biogas component IDs expanded**: `S_ch4`, `S_CH4`, `S_co2`, `S_CO2`, `S_h2s`, `S_H2S`, `S_IS`, `S_h2`, `S_H2`, `S_IC`

### Files Modified
- `utils/flowsheet_builder.py` - Model-aware analysis, biogas detection, removal efficiency
- `core/converters.py` - ASM1 component loading and coefficients
- `utils/diagram.py` - Correct mADM1 component IDs
- Multiple files - Unicode arrow replacement (cli.py, server.py, templates, utils)

---

## Phase 4: Production Readiness (2026-01-11)

Phase 4 addressed critical bugs and quality gaps to make the codebase production-ready.

### Critical Fixes (Tasks 1-3)

| Task | Issue | Fix |
|------|-------|-----|
| 1 | `generate_report` import bug | Added `generate_report()` wrapper function to `reports/qmd_builder.py` |
| 2 | README CLI examples wrong | Fixed `--duration-days` flag, file-based `--influent` |
| 3 | Tests hardcode Windows paths | Replaced all hardcoded paths with `sys.executable` |

### Feature Additions (Tasks 4, 8)

| Task | Feature | Implementation |
|------|---------|----------------|
| 4 | Aerobic diagram generation | Added `save_system_diagram()` calls to `mle_mbr.py`, `ao_mbr.py`, `a2o_mbr.py` |
| 8 | Report time-series plots | Created `utils/report_plots.py` with matplotlib plot generators |

### Packaging & Dependencies (Tasks 5-7)

- **pyproject.toml**: Added `psutil`, `jinja2`, `matplotlib` to dependencies; fixed wheel packaging for `cli.py`, `server.py`, and report templates
- **requirements.txt**: Created with all runtime deps + Graphviz/Quarto external dependency notes
- **Version**: Aligned to `3.0.0` across all files

### Per-Unit Analysis (NEW)

Reports now include detailed analysis for **every SanUnit** in the flowsheet:

```python
# Extracted by _extract_unit_analysis() in utils/flowsheet_builder.py
{
    "CSTR1": {
        "unit_id": "CSTR1",
        "unit_type": "CSTR",
        "parameters": {"V_max_m3": 1000, "HRT_days": 0.5},
        "inlets": [{"stream_id": "influent", "COD_mg_L": 500, ...}],
        "outlets": [{"stream_id": "eff1", "COD_mg_L": 150, ...}],
        "removal_efficiency": {"COD_removal_pct": 70.0}
    }
}
```

Templates (`anaerobic_report.qmd`, `aerobic_report.qmd`) display per-unit tables with parameters, inlet/outlet streams, and removal efficiencies.

### Validation Enhancements (Task 11)

Mass and charge balance validation added to `core/converters.py`:

```python
from core.converters import convert_state, validate_mass_balance, validate_charge_balance

# Run conversion with validation
output_state, metadata = convert_state(input_state, target_model, validate=True)
# metadata["mass_balance"]["cod_balance"]["passed"] -> True/False
# metadata["charge_balance"]["passed"] -> True/False
```

### Test Improvements (Tasks 9, 10, 12)

| Test Class | Tests | Purpose |
|------------|-------|---------|
| `TestReportIntegration` | 4 | CLI `--report` option, report routing |
| `TestPerUnitAnalysis` | 4 | Per-unit extraction and template support |
| `TestMCPToolBehavior` | 8 | Tool behavior validation (not just existence) |
| `TestWarningHandling` | 2 | Explicit warning assertions |
| `TestConverterValidation` | 12 | Mass/charge balance validation |

### Files Modified

| File | Changes |
|------|---------|
| `reports/qmd_builder.py` | Added `generate_report()`, plot integration, `unit_analysis` pass-through |
| `utils/report_plots.py` | **NEW** - Time-series plot generation with matplotlib |
| `utils/flowsheet_builder.py` | Added `_extract_unit_analysis()` for per-unit data |
| `reports/templates/*.qmd` | Added Per-Unit Analysis section |
| `core/converters.py` | Added `validate_mass_balance()`, `validate_charge_balance()`, `validate` param |
| `tests/test_phase2.py` | Added 30 new tests (report, per-unit, warning handling) |
| `tests/test_phase3.py` | Added 8 behavior tests |
| `tests/test_converters.py` | **NEW** - 12 validation tests |
| `pyproject.toml` | Fixed packaging, added dependencies |
| `requirements.txt` | **NEW** - All runtime dependencies |
| `README.md` | Fixed CLI examples |

---

## Phase 5: Report Integration & Schema Normalization (2026-01-11)

Phase 5 addressed critical schema mismatches between flowsheet simulation outputs and report builder expectations.

### Schema Normalization

Created `normalize_results_for_report()` function in `reports/qmd_builder.py` that bridges result producers and template requirements:

| Source | Target | Default |
|--------|--------|---------|
| `results["diagram_path"]` | `results["flowsheet"]["diagram_path"]` | `None` |
| `results["timeseries_path"]` | `results["timeseries"]` (loaded JSON) | `{}` |
| `results["metadata"]["solver"]["duration_days"]` | `results["duration_days"]` | `0` |
| `results["metadata"]["solver"]["method"]` | `results["method"]` | `"RK23"` |
| `results["effluent_quality"]` | `results["effluent"]` (flattened) | `{}` |
| `results["removal_efficiency"]` | `results["performance"]` | `{}` |
| `results["flowsheet"]` = None | `results["flowsheet"]` = `{}` | `{}` |

### Key Features

- **Single normalization entrypoint**: Called in `_prepare_anaerobic_data()` and `_prepare_aerobic_data()` only
- **Idempotent**: Safe to call multiple times on same data
- **Comprehensive defaults**: All template-required keys populated (performance, biomass, reactor, influent)
- **File handling**: Resolves relative paths, verifies file existence, loads JSON timeseries
- **Null safety**: Guards against `flowsheet = None` crashes

### Dead Code Removal

Removed unused `saturation_index()` function from `models/madm1.py` (lines 627-658). The real saturation index calculation uses `calc_saturation_indices()` from `thermodynamics.py`.

### New Tests

| Test Class | Tests | Purpose |
|------------|-------|---------|
| `TestReportSchemaIntegration` | 14 | Normalization, idempotency, edge cases, template rendering |
| `TestArtifactContract` | 4 | Artifact retrieval from jobs/, format assertions |

### Files Modified

| File | Changes |
|------|---------|
| `reports/qmd_builder.py` | Added `normalize_results_for_report()` (~220 lines), updated `_prepare_*_data()` |
| `models/madm1.py` | Removed dead `saturation_index()` function |
| `tests/test_phase2.py` | Added `TestReportSchemaIntegration` class (14 tests) |
| `tests/test_phase3.py` | Added `TestArtifactContract` class (4 tests) |
| `CLAUDE.md` | Added Artifact Locations and Result Normalization sections |
