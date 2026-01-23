# Notes for Companion Agent Skills

This document captures lessons learned during development and testing of the qsdsan-engine-mcp, intended to inform the design of companion agent skills.

---

## mADM1 Anaerobic Simulation Requirements

### CRITICAL: Complete 62-Component State Required

**Problem:** mADM1 simulations will NOT converge with incomplete state specifications.

**Root Cause:** The 63-component mADM1 model (62 state variables + H2O) has tightly coupled stoichiometry. Missing components cause mass balance errors that manifest as oscillating derivatives that never converge.

**Symptoms of incomplete state:**
- COD derivatives oscillate wildly (1000x-10000x tolerance) instead of decreasing
- Biomass may appear to converge while COD does not
- Simulation runs indefinitely without reaching steady state
- Timeout after hours of computation

**Solution:** Always provide ALL 62 components with reasonable initial values. Use `tests/test_madm1_state.json` as a reference template.

**Minimum required components by category:**

| Category | Components | Notes |
|----------|------------|-------|
| Core soluble (13) | S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2, S_ch4, S_IC, S_IN, S_IP, S_I | All required |
| Core particulate (11) | X_ch, X_pr, X_li, X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2, X_I | All required |
| EBPR (3) | X_PHA, X_PP, X_PAO | Can be 0 if no EBPR |
| Metal ions (2) | S_K, S_Mg | Required for ion balance |
| Sulfur (7) | S_SO4, S_IS, X_hSRB, X_aSRB, X_pSRB, X_c4SRB, S_S0 | Required even if 0 |
| Iron (9) | S_Fe3, S_Fe2, X_HFO_* (7 variants) | Required even if 0 |
| Minerals (13) | X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD, X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS, X_Fe3PO42, X_AlPO4 | Required even if 0 |
| Final ions (2) | S_Na, S_Cl | Required for electroneutrality |

---

### Component Ordering is Critical

**Problem:** mADM1 kinetics depend on specific state vector positions. If components are in wrong order, reactions will use wrong concentrations.

**Solution:** Always validate component ordering before simulation:

```python
from utils.simulate_madm1 import get_validated_components

# This validates ordering and sets thermo ONCE
cmps = get_validated_components()
```

**Expected order (first 15):**
```
S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2, S_ch4, S_IC, S_IN, S_IP, S_I, X_ch, X_pr, ...
```

---

### Thermo Initialization

**Problem:** Multiple calls to `qs.set_thermo()` with different component sets cause state variable misalignment.

**Solution:** Use `get_validated_components()` which:
1. Creates components ONCE
2. Validates ordering
3. Sets thermo ONCE
4. Returns cached singleton on subsequent calls

**Anti-pattern (causes convergence failures):**
```python
# BAD - creates new component set each time
cmps1 = create_madm1_cmps()  # sets thermo
cmps2 = create_madm1_cmps()  # sets thermo AGAIN - may differ!
```

**Correct pattern:**
```python
# GOOD - uses validated singleton
cmps = get_validated_components()  # validates + sets thermo once
```

---

## Steady-State Convergence

### SRT Determines Convergence, NOT HRT

**Key insight from user:** "Steady state is a function of SRT, not HRT."

For CSTR systems: **SRT = HRT** (no biomass retention)

**Minimum SRT requirements:**
- Acetoclastic methanogens: doubling time ~2-3 days, minimum SRT ~15 days
- Safe operating SRT: 20+ days for stable methanogenesis
- SRT < 15 days: Risk of methanogen washout

**Convergence time estimates (with complete state):**

| HRT/SRT | Convergence Time | Wall-Clock Time |
|---------|------------------|-----------------|
| 20 days | ~100-120 simulated days | ~2-3 minutes |
| 15 days | ~80-100 simulated days | ~2 minutes |
| 5 days | May not converge (washout risk) | N/A |

---

### Convergence Tolerance

**Default tolerance:** 1e-3 kg/m³/d

**What "converged" means:**
- max|dCOD/dt| < tolerance
- max|dBiomass/dt| < tolerance

**Typical convergence progression (complete state, 20-day HRT):**
```
t=2d:   COD: 560x tolerance, Biomass: 60535x tolerance
t=20d:  COD: 2000x tolerance, Biomass: 45000x tolerance
t=60d:  COD: 230x tolerance, Biomass: 623x tolerance
t=100d: COD: 3.4x tolerance, Biomass: 4.2x tolerance
t=110d: COD: 0.87x tolerance, Biomass: 0.88x tolerance -> CONVERGED
```

---

## Inoculum Generation

**Problem:** Raw feedstock has insufficient biomass for CSTR startup, causing F/M overload.

**Solution:** The `generate_inoculum_state()` function scales biomass to ~20% of feed COD:

```python
from utils.inoculum_generator import generate_inoculum_state

reactor_init_state = generate_inoculum_state(
    feedstock_state=adm1_state_62,
    target_biomass_cod_ratio=0.20  # 20% of COD as biomass
)
```

**What it does:**
1. Calculates total feedstock COD
2. Scales all biomass groups proportionally to reach target ratio
3. Boosts methanogens (X_ac, X_h2) by additional 6x factor
4. Adds alkalinity (S_IC boost to ~200 meq/L)
5. Balances S_Na for electroneutrality

---

## Unit Systems

### Model-Specific Concentration Units

| Model | Concentration Units | Flow Units |
|-------|---------------------|------------|
| ASM2d | mg/L | m³/d |
| ASM1 | mg/L | m³/d |
| mADM1 | kg/m³ | m³/d |

**Common error:** Providing mADM1 concentrations in mg/L (1000x too high) or ASM2d in kg/m³ (1000x too low).

**Validation function:**
```python
from core.plant_state import validate_concentration_bounds

warnings = validate_concentration_bounds(
    concentrations={"S_su": 0.5},
    model_type="mADM1",
    units="kg/m3"
)
```

---

## Test Fixtures

### Complete mADM1 State File

Location: `tests/test_madm1_state.json`

Format:
```json
{
  "S_su": [1.011, "kg/m3"],
  "S_aa": [1.522, "kg/m3"],
  ...
}
```

**Usage in tests:**
```python
test_state_path = Path(__file__).parent / "test_madm1_state.json"
with open(test_state_path, "r") as f:
    state_raw = json.load(f)

# Convert [value, unit] to plain floats
concentrations = {k: v[0] if isinstance(v, list) else v
                  for k, v in state_raw.items()}
```

---

## Debugging Tips

### COD Oscillation Diagnosis

If COD derivatives oscillate while biomass converges:
1. Check component ordering with `verify_component_ordering()`
2. Verify thermo was set only once
3. Check for missing components in input state
4. Try `pH_ctrl=7.0` to rule out alkalinity issues

### Timeout Diagnosis

If simulation times out (>10 minutes):
1. **First check:** Is state complete? (62 components)
2. **Second check:** Is HRT/SRT adequate? (>= 15 days)
3. **Third check:** Is inoculum properly scaled? (biomass ~20% of COD)
4. **Fourth check:** Component ordering validated?

### Quick Validation Test

```python
from utils.simulate_madm1 import get_validated_components, verify_component_ordering

cmps = get_validated_components()
print(f"Components: {len(cmps)}")  # Should be 63
verify_component_ordering(cmps)    # Should not raise
```

---

## Performance Benchmarks

### Expected Runtime (complete state, 20-day HRT)

| Phase | Simulated Days | Wall-Clock Time |
|-------|----------------|-----------------|
| QSDsan import | - | ~18s |
| Model creation | - | ~19s |
| Simulation | 110 days | ~90s |
| **Total** | - | **~2-3 minutes** |

### Memory Usage

- QSDsan + thermosteam: ~500 MB
- Single mADM1 simulation: +200-300 MB
- Peak during BDF solve: ~1 GB

---

## Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| "timestep_hours not supported" | Passing timestep to anaerobic template | Don't pass timestep_hours for mADM1 |
| Simulation never converges | Incomplete state | Use complete 62-component state |
| COD oscillates wildly | Component ordering mismatch | Use `get_validated_components()` |
| "Expected 63 components, got N" | Wrong component set | Regenerate with `create_madm1_cmps()` |
| Methanogen washout | SRT too short | Use HRT >= 15 days |
| F/M overload | No inoculum scaling | Use `generate_inoculum_state()` |

---

## QSDsan Import Time Mitigation (Phase 10)

### Cold Start Issue

QSDsan takes ~18 seconds to import due to thermosteam/thermo dependencies. This blocks the MCP event loop during initialization.

### Background Warmup Solution

Use `utils/qsdsan_loader.py` for async loading that doesn't block the event loop:

```python
from utils.qsdsan_loader import start_background_warmup, full_warmup, is_loaded

# Option 1: Start warmup immediately after server startup
start_background_warmup("mADM1")  # Non-blocking, fires and forgets

# Option 2: Comprehensive warmup (both models + process models)
import asyncio
asyncio.create_task(full_warmup(["mADM1", "ASM2d"]))

# Check if models are ready
if is_loaded("mADM1"):
    # Fast path - components already cached
    cmps = await get_components("mADM1")
```

### Available Functions

| Function | Description |
|----------|-------------|
| `start_background_warmup(model)` | Fire-and-forget warmup for single model |
| `full_warmup(models)` | Async warmup for multiple models + process models |
| `get_components(model)` | Get cached components (loads if not cached) |
| `get_process_model(model)` | Get cached process model (loads if not cached) |
| `is_loaded(model)` | Check if components are cached |
| `is_model_loaded(model)` | Check if process model is cached |
| `wait_for_load(model, timeout)` | Wait for ongoing load with timeout |

### Best Practices

1. Call `full_warmup()` during server initialization
2. Use `is_loaded()` before simulations to provide user feedback
3. All loading is thread-safe - concurrent calls await the same load task

---

## References

- Complete mADM1 state template: `tests/test_madm1_state.json`
- Component ordering validation: `utils/simulate_madm1.py:verify_component_ordering()`
- Inoculum generator: `utils/inoculum_generator.py`
- Reference implementation: `anaerobic-design-mcp/utils/qsdsan_simulation_sulfur.py`
