# QSDsan Engine MCP - Development Context

## Project Goal
Universal wastewater simulation engine supporting anaerobic (mADM1, 63 components) and aerobic (ASM2d, 19 components) treatment via dual adapters (MCP + CLI).

**Master Plan:** `/home/hvksh/.claude/plans/idempotent-napping-hoare.md`

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1A | Foundation (PlantState, registries, dual adapters) | ✅ Complete |
| 1B | Anaerobic mADM1 simulation | ✅ Complete |
| 1C | Aerobic MBR templates (MLE, A/O, A2O) | ✅ Complete |
| 1D | State converters (ASM2d ↔ mADM1) | ✅ Complete |
| 1E | Quarto reports + flowsheet diagrams | ✅ Complete |
| 1F | Skills extraction | ✅ Complete |
| **2** | **Flowsheet Construction** | ✅ **COMPLETE** |

**Phase 1 Validation:** Codex-verified 2026-01-06 - all files present, 27 tests passing
**Phase 2 Validation:** 87 tests passing as of 2026-01-07 (including junction unit tests)

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

# Unit discovery
python cli.py flowsheet units --model ASM2d --json-out
python cli.py flowsheet units --category reactor
```

### Unit Registry (38 units)

```python
from core.unit_registry import list_available_units, get_unit_spec

# Categories: reactor, separator, clarifier, sludge, pump, junction, utility, pretreatment
# List units compatible with ASM2d
units = list_available_units(model_type="ASM2d")
# Get unit specification
spec = get_unit_spec("CSTR")
```

Key units by category:
- **Reactors:** CSTR, AnaerobicCSTR, AnaerobicCSTRmADM1, PFR, MixTank, Lagoon, InternalCirculationRx
- **Separators:** CompletelyMixedMBR, AnMBR, PolishingFilter
- **Clarifiers:** FlatBottomCircularClarifier, PrimaryClarifier, IdealClarifier, Sedimentation
- **Junctions:** ASM2dtoADM1, ADM1toASM2d, mADM1toASM2d, ASM2dtomADM1, Junction
- **Utilities:** Splitter, Mixer, Tank, StorageTank, DynamicInfluent

### Pipe Notation Parser

Full BioSTEAM syntax support:

```python
from utils.pipe_parser import parse_port_notation

# Output notation: "A1-0" → unit A1, output port 0
# Input notation: "1-M1" → unit M1, input port 1
# Direct: "U1-U2" → U1.outs[0] → U2.ins[0]
# Explicit: "U1-0-1-U2" → U1.outs[0] → U2.ins[1]
# Stream: "influent" → named stream
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

## Key Technical Notes

### Component IDs
| Model | Count | Key IDs |
|-------|-------|---------|
| mADM1 | 63 | SRB: `X_hSRB`, `X_aSRB`, `X_pSRB`, `X_c4SRB` (NOT `X_SRB`); Iron: `S_Fe3` (NOT `S_Fe`) |
| ASM2d | 19 | `X_MeOH` = Metal-hydroxides, `X_MeP` = Metal-phosphates (chemical P removal) |

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
├── server.py              # MCP Adapter (FastMCP) - 9 Phase 2 tools added
├── cli.py                 # CLI Adapter (typer) - flowsheet command group
├── core/
│   ├── plant_state.py     # PlantState dataclass
│   ├── model_registry.py  # Component definitions (mADM1: 63, ASM2d: 19)
│   ├── template_registry.py
│   ├── converters.py      # State conversion (ASM2d ↔ mADM1)
│   ├── junction_components.py  # Component alignment for junctions
│   ├── junction_units.py  # Custom junction classes for ModifiedADM1
│   └── unit_registry.py   # [Phase 2] 37 SanUnit specs with validation
├── templates/
│   ├── anaerobic/cstr.py  # mADM1 CSTR
│   └── aerobic/           # mle_mbr.py, ao_mbr.py, a2o_mbr.py
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
│   ├── topo_sort.py       # [Phase 2] Topological sort with recycle handling
│   └── stream_analysis.py # Result extraction
├── reports/
│   ├── qmd_builder.py     # Quarto Markdown generator (Jinja2)
│   └── templates/         # anaerobic_report.qmd, aerobic_report.qmd, report.css
├── jobs/
│   └── flowsheets/        # [Phase 2] Session storage directory
└── tests/
    ├── test_phase1.py     # 27 tests
    └── test_phase2.py     # [Phase 2] 48 flowsheet construction tests
```

---

## Reference Files

| Reference | Path |
|-----------|------|
| Master Plan | `/home/hvksh/.claude/plans/idempotent-napping-hoare.md` |
| Phase 2 Plan | `/home/hvksh/.claude/plans/bright-snacking-prism.md` |
| Aerobic MBR Example | `/tmp/qsdsan-aerobic-simulation-example/Pune_Nanded_WWTP_updated.py` |
| Anaerobic Skill | `/home/hvksh/skills/anaerobic-skill/` |
| Aerobic Skill | `/home/hvksh/skills/aerobic-design-skill/` |

---

## Known Limitations

1. **QSDsan Import Time:** ~18s cold start. Use `utils/qsdsan_loader.py` for async loading.

2. **Session Storage:** Sessions are stored in `jobs/flowsheets/` by default. Set `QSDSAN_ENGINE_SESSIONS_DIR` environment variable to customize the storage location.

---

## State Conversion (ASM2d ↔ mADM1)

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

# ASM2d → mADM1
# S_ALK → S_IC, S_NH4 → S_IN, S_PO4 → S_IP
# S_A → S_ac, S_F → S_su, X_S → X_pr/X_li/X_ch (split)
# X_H → biomass groups (degradable fraction)

# mADM1 → ASM2d
# S_IC → S_ALK, S_IN → S_NH4, S_IP → S_PO4
# S_ac/S_va/S_bu/S_pro → S_A
# X_ch/X_pr/X_li → X_S
# All biomass (X_su, X_aa, etc.) → X_H
```

---

## Testing

```bash
# Run all tests
../venv312/Scripts/python.exe -m pytest tests/ -v

# Phase 1 tests only
../venv312/Scripts/python.exe -m pytest tests/test_phase1.py -v

# Phase 2 tests only
../venv312/Scripts/python.exe -m pytest tests/test_phase2.py -v

# Skip slow simulation tests
../venv312/Scripts/python.exe -m pytest tests/ -v -m "not slow"
```
