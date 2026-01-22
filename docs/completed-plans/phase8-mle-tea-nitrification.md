# Phase 8: MLE Test Bug Fixes and TEA Integration

**Status:** Complete (2026-01-22)
**Test Count:** 292 tests passing (up from 280)

## Executive Summary

Based on the MLE comparison test (2026-01-21), three critical issues were addressed:
1. **Flowsheet construction failure** - Mixer/Splitter dynamic creation fixed
2. **Missing TEA functionality** - TEA tools added for CAPEX/OPEX estimation
3. **Nitrification failure** - Fixed via reactor inoculation (was 4.5%, now >80%)

---

## Phase 8A: Flowsheet Construction Fixes

### Issues Fixed

| Bug | Location | Fix |
|-----|----------|-----|
| Splitter parameter format | `flowsheet_builder.py` | Already supported float/list/dict - no change needed |
| Mixer input resolution | `flowsheet_builder.py:450-540` | Added `allow_missing` parameter for deferred connections |
| Input validation | `server.py:create_unit` | Added pre-compilation validation with clear error messages |
| Mixer port allocation | `flowsheet_builder.py:_wire_connection` | Dynamic port assignment for variable-input units |

### Key Changes

1. **`_resolve_single_input()`** now accepts `allow_missing` parameter:
   - `allow_missing=False`: Raises `ValueError` for missing refs (fail-fast)
   - `allow_missing=True`: Returns `None` for deferred connections (recycles)

2. **`_wire_connection()`** handles Mixer's variable inputs:
   - Finds empty input slots for new connections
   - Uses BioSTEAM's sink mechanism when slots exhausted

3. **`create_unit` tool** validates inputs before adding to session:
   - Rejects invalid input port notation (e.g., "1-M1" as input source)
   - Warns (but allows) deferred connections for recycles

---

## Phase 8B: Nitrification Fixes

### Root Cause Analysis

NH4 removal was only 4.5% instead of expected >85% because:
- Reactors initialized with influent composition (~5 mg/L X_AUT)
- Nitrifiers grow slowly (μ_AUT ~1.0 d⁻¹)
- Insufficient X_AUT biomass for nitrification

### Solution: Reactor Inoculation

**New File:** `utils/aerobic_inoculum_generator.py`

```python
from utils.aerobic_inoculum_generator import generate_aerobic_inoculum

inoculum = generate_aerobic_inoculum(target_mlvss_mg_L=3500)
# Returns: X_AUT=249 mg COD/L, X_H=3976 mg COD/L, X_PAO=99 mg COD/L

for reactor in [A1, A2, O1, O2, MBR]:
    reactor.set_init_conc(**inoculum)
```

**Key Features:**
- Target MLVSS conversion: VSS to COD via 1.42 ratio
- Default fractions: 85% X_H, 5% X_AUT, 2% X_PAO
- Includes background nutrients (S_NH4, S_NO3, S_ALK)

### Template Changes

**File:** `templates/aerobic/mle_mbr.py`

1. Imports aerobic inoculum generator
2. Generates inoculum with established nitrifier population
3. Applies to all reactors via `set_init_conc()`
4. Adds simulation duration warning if <45 days
5. Calculates and reports SRT in results

---

## Phase 8C: TEA Integration

### New MCP Tools

| Tool | Function |
|------|----------|
| `create_tea` | Create TEA for completed simulation |
| `get_capex_breakdown` | Return CAPEX hierarchy (DPI, TDC, FCI, TCI) |
| `get_opex_summary` | Return OPEX components (FOC, VOC, AOC) |
| `get_utility_costs` | Return utility consumption (kWh/yr) |

### TEA Estimation Approach

Since many QSDsan units lack `_cost()` methods, TEA tools use heuristics:

**CAPEX Hierarchy (per QSDsan/BioSTEAM):**
- Equipment: ~$1000/m³ reactor volume
- Installed: Equipment × 1.5
- DPI (Direct Permanent Investment): Installed × 1.15
- TDC (Total Depreciable Capital): DPI × 1.04
- FCI (Fixed Capital Investment): TDC × 1.10
- TCI (Total Capital Investment): FCI × 1.05

**OPEX:**
- Aeration: ~0.03 kW/m³
- Maintenance: configurable fraction of TCI (default 3%)
- Electricity: configurable price (default $0.07/kWh)
- Heating/Cooling: estimated for MBR systems

**New File:** `utils/tea_wrapper.py`
- `create_tea()`: Creates SimpleTEA object
- `get_capex_breakdown()`: CAPEX hierarchy
- `get_opex_summary()`: OPEX components
- `get_utility_costs()`: Utility consumption
- `estimate_aeration_power()`: Aeration power estimate

---

## Files Modified

| File | Changes |
|------|---------|
| `utils/flowsheet_builder.py` | `_resolve_single_input()` with allow_missing, Mixer tuple inputs, `_wire_connection()` dynamic ports |
| `server.py` | Pre-compilation input validation, 4 TEA tools with full CAPEX hierarchy (DPI/TDC/FCI/TCI) and heating/cooling |
| `templates/aerobic/mle_mbr.py` | Reactor inoculation, duration warning, SRT calculation |
| `core/unit_registry.py` | Mixer docs (ins tuple pattern), CSTR costing note |
| `CLAUDE.md` | Updated documentation |

## New Files

| File | Purpose |
|------|---------|
| `utils/aerobic_inoculum_generator.py` | Generate reactor inoculum with established X_AUT |
| `utils/tea_wrapper.py` | TEA creation and query functions |
| `tests/test_flowsheet_mixer_splitter.py` | Test dynamic Mixer/Splitter with recycles |
| `tests/test_nitrification.py` | Verify >80% NH4 removal with inoculum |

---

## Verification

### Test Results

```
292 passed, 14 deselected (slow tests)
```

### Key Validations

1. **Phase 8A:** Mixer/Splitter tests pass (12 tests)
2. **Phase 8B:** Inoculum generator tests pass (10 tests)
3. **Phase 8C:** TEA tools added (functional, heuristic estimates)
4. **Regression:** All existing 280 tests still pass

---

## Codex Review Reference

**Session 1:** `019be6ae-8935-70d2-b72d-07763804f933`

Key corrections from Codex review:
1. Inoculation method: `set_init_conc()` not `initial_state` param
2. Units: COD/VSS ratio = 1.42 for biomass
3. K_NH4_AUT: Keep at 1.0 (IWA default)
4. CSTR costing: Has `_cost()` but empty/pass

**Session 2:** `019be6d6-557a-7343-a6fd-607b79436506`

Additional fixes from second Codex review:
1. TEA outputs: Added DPI/TDC to CAPEX breakdown (server.py:2183-2184)
2. TEA outputs: Added heating/cooling to utilities (server.py:2207-2208)
3. TEA params: Added `annual_maintenance_factor` parameter (server.py:2057)
4. Unit docs: Updated Mixer docs with `ins=(...)` tuple pattern (unit_registry.py:555-557)
5. Unit docs: Added CSTR costing note (unit_registry.py:79-80)
