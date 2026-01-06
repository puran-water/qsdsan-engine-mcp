# QSDsan Engine MCP - Development Context

## Plan Document
**Path:** `/home/hvksh/.claude/plans/idempotent-napping-hoare.md`

## Project Goal
Refactor `anaerobic-design-mcp` into a universal `qsdsan-engine-mcp` supporting both anaerobic (mADM1) and aerobic (ASM2d) treatment simulation with ~6-8 stateless tools.

---

## Completed Work

### Phase 1A: Foundation ✅
- Created `qsdsan-engine-mcp` repository
- Copied core utilities from `anaerobic-design-mcp`
- Created `core/plant_state.py` with PlantState dataclass
- Created `core/model_registry.py` for component management
- Created dual adapters: `server.py` (MCP) and `cli.py` (typer)

### Phase 1B: Port Anaerobic Simulation ✅
- Ported mADM1 model files to `models/`:
  - `models/madm1.py` - mADM1 63-component process model
  - `models/reactors.py` - AnaerobicCSTRmADM1 (4 biogas species)
  - `models/sulfur_kinetics.py` - SRB processes and H2S inhibition
  - `models/components.py` - mADM1 component creation
  - `models/thermodynamics.py` - Thermodynamic calculations
- Created `templates/anaerobic/cstr.py` - Template builder for mADM1 CSTR
- Created `utils/simulate_madm1.py` - Simulation wrapper with convergence
- Created `utils/stream_analysis.py` - Stream analysis functions
- Created `utils/inoculum_generator.py` - CSTR startup inoculum scaling
- **Codex gate review: APPROVED**

### Phase 1B Cleanup (per Codex review) ✅
- Fixed SRB biomass reporting to use disaggregated components (X_hSRB, X_aSRB, X_pSRB, X_c4SRB)
- Fixed FeCl3 dosing to use S_Fe3 instead of S_Fe
- Updated outdated X_SRB comments in components.py and stream_analysis.py
- Fixed undefined X_SRB variable bug in stream_analysis.py:646

---

## Next Phase: 1C - Add Aerobic MBR Templates

### Tasks
1. Create `models/asm2d.py` component loader
2. Create `templates/aerobic/mle_mbr.py` (MLE-MBR flowsheet)
3. Create `templates/aerobic/a2o_mbr.py` (A2O-MBR with EBPR)
4. Create `templates/aerobic/ao_mbr.py` (Simple A/O-MBR)
5. Register aerobic templates in registry

---

## Reference Documents

### Aerobic MBR Reference Implementation
**Path:** `/tmp/qsdsan-aerobic-simulation-example/Pune_Nanded_WWTP_updated.py`

Key patterns from Pune_Nanded:
```python
from qsdsan import processes as pc, sanunits as su

# Components
cmps = pc.create_asm2d_cmps(set_thermo=False)
qs.set_thermo(cmps)

# Process model
asm2d = pc.ASM2d(**default_asm2d_kwargs)

# Anoxic zone (no aeration)
A1 = su.CSTR('A1', ins=[...], V_max=V_an, aeration=None, DO_ID=None, suspended_growth_model=asm2d)

# Aerobic zone (with aeration)
O1 = su.CSTR('O1', ins=[A1-0], V_max=V_ae, aeration=DO_ae, DO_ID='S_O2', suspended_growth_model=asm2d)

# MBR separation
MBR = su.CompletelyMixedMBR('MBR', ins=O2-0, outs=('effluent', 'retain'),
                             V_max=V_mbr, solids_capture_rate=0.999,
                             pumped_flow=Q_was + Q_ras,
                             aeration=DO_mbr, DO_ID='S_O2',
                             suspended_growth_model=asm2d)

# System
sys = qs.System('Pune_MBR', path=(SP1, TA, A1, A2, O1, O2, MBR, SP2, CF), recycle=[RAS, recycle])
sys.simulate(t_span=(0,t), method='RK23')
```

### ASM2d Default Kinetic Parameters
Located in Pune_Nanded lines 124-146 (`default_asm2d_kwargs`)

### Domestic Wastewater Composition (ASM2d)
Located in Pune_Nanded lines 107-122 (`domestic_ww`)

---

## Key Technical Notes

### mADM1 Component IDs
- Disaggregated SRB: `X_hSRB`, `X_aSRB`, `X_pSRB`, `X_c4SRB` (NOT lumped `X_SRB`)
- Ferric iron: `S_Fe3` (NOT generic `S_Fe`)
- Total: 63 components (62 state variables + H2O)

### Simulation API (Anaerobic)
```python
from utils.simulate_madm1 import run_simulation_sulfur

sys, inf, eff, gas, converged_at, status, time_series = run_simulation_sulfur(
    basis={"Q": flow_m3_d, "Temp": temperature_K},
    adm1_state_62=concentrations,
    HRT=HRT_days,
    check_interval=2,
    tolerance=1e-3,
)
```

### Template API
```python
from templates.anaerobic.cstr import build_and_run

result = build_and_run(
    influent_state={"flow_m3_d": 1000, "temperature_K": 308.15, "concentrations": {...}},
    reactor_config={"V_liq": 20000},
)
```

---

## File Structure

```
qsdsan-engine-mcp/
├── server.py                    # MCP Adapter (FastMCP)
├── cli.py                       # CLI Adapter (typer)
├── core/
│   ├── plant_state.py           # PlantState dataclass
│   └── model_registry.py        # Model → component mapping
├── templates/
│   ├── anaerobic/
│   │   └── cstr.py              # AnaerobicCSTR builder (mADM1) ✅
│   └── aerobic/
│       ├── __init__.py          # (empty)
│       ├── mle_mbr.py           # MLE-MBR flowsheet (TODO)
│       ├── a2o_mbr.py           # A2O-MBR flowsheet (TODO)
│       └── ao_mbr.py            # A/O-MBR flowsheet (TODO)
├── utils/
│   ├── simulate_madm1.py        # mADM1 simulation wrapper ✅
│   ├── stream_analysis.py       # Stream analysis functions ✅
│   ├── inoculum_generator.py    # CSTR startup initialization ✅
│   ├── qsdsan_loader.py         # Async component loading ✅
│   └── path_utils.py            # WSL compatibility
├── models/
│   ├── madm1.py                 # mADM1 63-component ✅
│   ├── asm2d.py                 # ASM2d wrapper (TODO)
│   ├── reactors.py              # AnaerobicCSTRmADM1 ✅
│   ├── sulfur_kinetics.py       # SRB processes ✅
│   ├── components.py            # Component loader ✅
│   └── thermodynamics.py        # Thermo calculations ✅
└── CLAUDE.md                    # This file
```

---

## Git Log (Recent)
```
9d0c2fd fix(phase1b): Clean up outdated X_SRB comments and fix undefined variable
[prior]  fix(phase1b): Fix template API and mADM1 component IDs
[prior]  feat(phase1b): Port anaerobic mADM1 simulation from anaerobic-design-mcp
```
