# QSDsan Engine MCP - Development Context

## Project Overview

Universal wastewater simulation engine supporting anaerobic (mADM1, 63 components) and aerobic (ASM2d, 19 components) treatment via dual adapters (MCP + CLI).

**Architecture:** FastMCP server (`server.py`) + Typer CLI (`cli.py`) sharing core simulation logic.

---

## Development Plans

| Plan | Path | Status |
|------|------|--------|
| Master Plan (Phase 1) | `docs/completed-plans/idempotent-napping-hoare.md` | Complete |
| Phase 2 Plan | `docs/completed-plans/bright-snacking-prism.md` | Complete |
| Phase 2B Bug Fixes | `docs/completed-plans/phase2b-bug-fixes.md` | Complete |
| Phase 3 Plan | `docs/completed-plans/phase3-llm-accessibility.md` | Complete |
| Phase 4 Plan | `docs/completed-plans/phase4-production-readiness.md` | Complete |
| Phase 5 Plan | `docs/completed-plans/phase5-report-integration.md` | Complete |
| Phase 6 Plan | `docs/completed-plans/phase6-production-hardening.md` | Complete |
| Phase 7 Plan | `docs/completed-plans/phase7-production-hardening.md` | Complete |
| Phase 7B Bug Fixes | `docs/completed-plans/phase7b-bug-fixes.md` | Complete |
| Phase 7C CH4 Fix | `docs/completed-plans/phase7c-ch4-calculation-fix.md` | Complete |
| Phase 8 Plan | `docs/completed-plans/phase8-mle-tea-nitrification.md` | Complete |
| Phase 8D CLI Verification | `docs/completed-plans/phase8d-cli-workflow-verification.md` | Complete |
| Phase 9 Plan | `docs/completed-plans/phase9-mixed-model-flowsheet-support.md` | Complete |

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1A-1F | Foundation, mADM1, aerobic MBR, converters, reports, skills | Complete |
| 2 | Flowsheet Construction (9 MCP tools, session management) | Complete |
| 2B | Bug fixes (model-aware dispatch, ASM1, Unicode) | Complete |
| 3 | LLM Accessibility (11 tools, native types, introspection) | Complete |
| 4 | Production Readiness (packaging, per-unit analysis, validation) | Complete |
| 5 | Report Integration (schema normalization, artifact contracts) | Complete |
| 6 | Production Hardening (fail-fast, JobManager tests) | Complete |
| 7 | Final Hardening (path traversal, concentration validation) | Complete |
| 7B | Bug fixes (CAS collision, VFA/sulfur report data) | Complete |
| 7C | CH4 calculation fix (COD-to-mass conversion in performance metrics) | Complete |
| 8 | MLE Bug Fixes, Nitrification Fix, TEA Integration | Complete |
| 8D | CLI Workflow Verification (Mixer ins.append, Splitter array, effluent detection, ASM1 DO_ID) | Complete |
| 9 | Mixed-Model Flowsheet Support (junction transforms, model zones, suggestions) | Complete |

**Test Count:** 322 tests passing (Phase 9 validation: 2026-01-22)

---

## File Structure

```
qsdsan-engine-mcp/
├── server.py              # MCP Adapter (FastMCP) - 29 tools
├── cli.py                 # CLI Adapter (Typer)
├── core/
│   ├── plant_state.py     # PlantState dataclass + validation
│   ├── model_registry.py  # Component definitions (mADM1: 63, ASM2d: 19)
│   ├── template_registry.py
│   ├── converters.py      # State conversion + mass/charge validation
│   ├── junction_components.py  # Component alignment for junctions
│   ├── junction_units.py  # Custom junction classes
│   └── unit_registry.py   # 49 SanUnit specs
├── templates/
│   ├── anaerobic/cstr.py  # mADM1 CSTR template
│   └── aerobic/           # mle_mbr.py, ao_mbr.py, a2o_mbr.py
├── models/
│   ├── madm1.py           # 63-component process model
│   ├── asm2d.py           # 19-component wrapper
│   ├── reactors.py        # AnaerobicCSTRmADM1
│   └── sulfur_kinetics.py # SRB processes
├── utils/
│   ├── simulate_madm1.py  # Simulation wrapper
│   ├── flowsheet_builder.py # System compilation
│   ├── flowsheet_session.py # Session state management
│   ├── path_utils.py      # Path traversal guards
│   ├── pipe_parser.py     # BioSTEAM notation parser
│   ├── topo_sort.py       # Topological sort with recycles
│   ├── diagram.py         # Flowsheet diagrams
│   ├── aerobic_inoculum_generator.py  # Reactor inoculation (Phase 8B)
│   └── tea_wrapper.py     # TEA calculations (Phase 8C)
├── reports/
│   ├── qmd_builder.py     # Quarto report generator
│   └── templates/         # QMD templates
├── tests/
│   ├── test_phase1.py     # 27 tests
│   ├── test_phase2.py     # 121 tests
│   ├── test_phase3.py     # 53 tests
│   ├── test_integration.py # E2E tests
│   ├── test_security.py   # Path traversal tests
│   ├── test_converters.py # Validation tests
│   ├── test_flowsheet_mixer_splitter.py # Phase 8A Mixer/Splitter tests
│   ├── test_nitrification.py # Phase 8B nitrification tests
│   ├── test_mixed_model.py   # Phase 9 mixed-model flowsheet tests (30 tests)
│   ├── test_madm1_state.json  # Complete 62-component mADM1 state
│   ├── test_asm2d_state.json  # Complete ASM2d state
│   └── run_slow_tests.py  # Integration tests for all templates
├── NOTES_FOR_SKILLS.md    # Guidance for companion agent skills
└── docs/completed-plans/  # Development plan archives
```

---

## Key Implementation Notes

### Concentration Units (Model-Specific)
- **ASM2d/ASM1/mASM2d:** mg/L
- **mADM1/ADM1:** kg/m³

`validate_concentration_bounds()` in `core/plant_state.py` warns on likely unit confusion. Called from `utils/flowsheet_builder.py:_create_waste_stream()`.

### State Conversion
Two approaches exist:
1. **Heuristic** (`core/converters.py:convert_state`): Fast, ~95% COD balance, for CLI/MCP standalone use
2. **Junction units** (`core/junction_units.py`): Biochemically accurate, auto-inserted by `build_system` for mixed-model flowsheets

### Path Security
`utils/path_utils.py` provides:
- `validate_safe_path()`: Prevents directory traversal
- `validate_id()`: Ensures IDs contain only safe characters

Used in `server.py` for job_id/session_id validation.

### Report Schema Normalization
`reports/qmd_builder.py:normalize_results_for_report()` bridges flowsheet outputs to template expectations. Called in `_prepare_anaerobic_data()` and `_prepare_aerobic_data()`.

### Session Storage
Default: `jobs/flowsheets/{session_id}/`
Override: `QSDSAN_ENGINE_SESSIONS_DIR` environment variable

### CH4 Flow Calculation (Phase 7C Fix)
`templates/anaerobic/cstr.py:_calculate_performance_metrics()` uses **molar basis** for CH4 volume:
```python
ch4_mol = gas.imol['S_ch4']  # kmol/hr
ch4_flow = ch4_mol * 22.414 * 24  # Nm3/d at STP
```
**Critical:** `gas.imass['S_ch4']` returns COD-equivalent mass (not actual CH4 mass). The `i_mass` factor is 0.25067 g CH4/g COD. Using `imass` directly without conversion causes 4x overestimate.

### Aerobic Reactor Inoculation (Phase 8B Fix)
`templates/aerobic/mle_mbr.py` uses `utils/aerobic_inoculum_generator.py` to initialize reactors with established biomass:
```python
from utils.aerobic_inoculum_generator import generate_aerobic_inoculum
inoculum = generate_aerobic_inoculum(target_mlvss_mg_L=3500)
for reactor in [A1, A2, O1, O2, MBR]:
    reactor.set_init_conc(**inoculum)
```
**Critical:** CSTR does NOT accept `initial_state` parameter - must use `set_init_conc()` method. Default inoculum provides ~249 mg COD/L X_AUT (nitrifiers), enabling >80% NH4 removal.

### TEA Estimation (Phase 8C)
TEA tools (`create_tea`, `get_capex_breakdown`, etc.) provide cost estimates. However, many QSDsan units (CSTR, Mixer, Splitter) lack `_cost()` methods. The tools use heuristic estimation:
- CAPEX: ~$1000/m³ reactor volume
- OPEX: Aeration power ~0.03 kW/m³, maintenance ~3% TCI

### Mixed-Model Flowsheet Support (Phase 9)
`core/unit_registry.py` now includes junction model transforms that enable mixed-model flowsheets:
```python
JUNCTION_MODEL_TRANSFORMS = {
    "ASM2dtomADM1": ("ASM2d", "mADM1"),
    "mADM1toASM2d": ("mADM1", "ASM2d"),
    "ADM1ptomASM2d": ("mADM1", "mASM2d"),  # Note: mADM1 = ADM1p
    ...
}
```

`server.py:compute_effective_model_at_unit()` traces upstream through junctions to determine the effective model at any unit. This enables:
- Creating mADM1 units after `ASM2dtomADM1` junction in ASM2d sessions
- Helpful error messages suggesting appropriate junctions
- Fan-in warnings when mixing streams from different models

**Key functions:** `normalize_model_name()`, `get_junction_output_model()`, `suggest_junction_for_conversion()`

---

## Known Limitations

1. **QSDsan Import Time:** ~18s cold start. Use `utils/qsdsan_loader.py` for async loading.

2. **Kinetic Parameters:** `parameters` argument ignored for mADM1 templates; ASM2d only.

3. **pH/Alkalinity:** Falls back to pH=7.0, SAlk=2.5 if `calculate_ph_and_alkalinity_fixed` module unavailable.

4. **Junction Property Alignment:** `X_AUT -> X_h2` in `COMPONENT_ALIGNMENT` is for property matching only; actual conversion uses QSDsan's `_compile_reactions()`.

5. **Junction Chains/Cycles:** Complex junction chains (A→J1→J2→B) traverse correctly, but cycles containing junctions are only detected at `build_system` time, not during `create_unit`.

6. **Fan-in Model Validation:** Mixing streams from different models at fan-in points (e.g., ASM2d + mADM1 into Mixer) produces a warning but not an error. Users must add appropriate junctions to unify component sets.

---

## Testing

```bash
# Windows venv (recommended for this project)
../venv312/Scripts/python.exe -m pytest tests/ -v

# All tests
python -m pytest tests/ -v

# Skip slow tests
python -m pytest tests/ -v -m "not slow"

# Specific test files
python -m pytest tests/test_integration.py -v
python -m pytest tests/test_security.py -v
```

---

## MCP Tools Summary

| Category | Tools |
|----------|-------|
| Simulation | `simulate_system`, `get_job_status`, `get_job_results` |
| Flowsheet | `create_flowsheet_session`, `create_stream`, `create_unit`, `connect_units`, `build_system`, `simulate_built_system` |
| Mutation | `update_stream`, `update_unit`, `delete_stream`, `delete_unit`, `delete_connection`, `clone_session`, `delete_session` |
| Discovery | `list_units`, `get_model_components`, `validate_flowsheet`, `suggest_recycles` |
| Retrieval | `get_flowsheet_session`, `list_flowsheet_sessions`, `get_flowsheet_timeseries`, `get_artifact` |
| TEA | `create_tea`, `get_capex_breakdown`, `get_opex_summary`, `get_utility_costs` |
| Utility | `list_templates`, `validate_state`, `convert_state` |

**Total:** 33 MCP tools

---

## External References

| Reference | Path |
|-----------|------|
| Aerobic MBR Example | `/tmp/qsdsan-aerobic-simulation-example/` |
| Anaerobic Skill | `/home/hvksh/skills/anaerobic-skill/` |
| Aerobic Skill | `/home/hvksh/skills/aerobic-design-skill/` |
