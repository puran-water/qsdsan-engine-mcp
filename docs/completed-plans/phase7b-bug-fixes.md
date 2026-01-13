# Phase 7B: mADM1 Simulation & Report Bug Fixes

## Completed: 2026-01-13

### Overview

Bug fixes discovered during mADM1 integration testing. Resolved CAS alias collision in component registration and missing data fields in report template rendering.

---

## Bugs Fixed

### Bug 1: CAS Alias Collision (X_CCM/X_ACC)

**File:** `models/madm1.py:199-202`

**Problem:** Both X_CCM (calcite) and X_ACC (aragonite) are CaCO3 polymorphs with the same CAS number 471-34-1. Using `Component.from_chemical()` for both caused "alias already in use" error during component registration.

**Error:**
```
RuntimeError: alias '471-34-1' already in use by Component('X_CCM')
```

**Root Cause:** thermosteam's `CompiledChemicals` registers CAS numbers as aliases. Both minerals share CAS 471-34-1 since they're crystalline forms of calcium carbonate.

**Fix:** Changed X_ACC creation from `Component.from_chemical()` to direct `Component()` with formula:
```python
# Before (caused collision):
X_ACC = Component.from_chemical('X_ACC', chemical='aragonite', ...)

# After (avoids CAS registration):
X_ACC = Component('X_ACC', formula='CaCO3', description='Aragonite', **mineral_properties)
```

---

### Bug 2: Missing VFA Data in Report

**File:** `reports/qmd_builder.py:581-607`

**Problem:** Jinja2 template `anaerobic_report.qmd` expected `data.inhibition.VFA.VFA_ALK_ratio` but the inhibition dict from `analyze_inhibition()` didn't include VFA key.

**Error:**
```
jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'VFA'
```

**Fix:** Added VFA calculation from effluent concentrations in `_prepare_anaerobic_data()`:
```python
# Calculate VFA from individual species
acetate = eff_concs.get('S_ac', 0) or 0
propionate = eff_concs.get('S_pro', 0) or 0
butyrate = eff_concs.get('S_bu', 0) or 0
valerate = eff_concs.get('S_va', 0) or 0
total_vfa = acetate + propionate + butyrate + valerate

# Calculate alkalinity from S_IC
s_ic = eff_concs.get('S_IC', 0) or 0
alkalinity = s_ic * 4.17  # Approximate mg CaCO3/L

# Populate inhibition['VFA'] dict
inhibition['VFA'] = {
    'acetate_mg_COD_L': acetate,
    'total_VFA_mg_COD_L': total_vfa,
    'VFA_ALK_ratio': total_vfa / alkalinity if alkalinity > 0 else 0,
    ...
}
```

---

### Bug 3: Missing Sulfur SRB Data in Report

**File:** `reports/qmd_builder.py:609-655`

**Problem:** Template expected `data.sulfur.X_hSRB_mg_COD_L` and other SRB biomass fields but sulfur dict was missing these keys.

**Error:**
```
jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'X_hSRB_mg_COD_L'
```

**Fix:** Added sulfur data calculation from effluent concentrations in `_prepare_anaerobic_data()`:
```python
# SRB biomass from effluent
X_hSRB = eff_concs.get('X_hSRB', 0) or 0  # H2-oxidizing SRB
X_aSRB = eff_concs.get('X_aSRB', 0) or 0  # Acetate-utilizing SRB
X_pSRB = eff_concs.get('X_pSRB', 0) or 0  # Propionate-utilizing SRB
X_c4SRB = eff_concs.get('X_c4SRB', 0) or 0  # Butyrate/valerate SRB
total_srb = X_hSRB + X_aSRB + X_pSRB + X_c4SRB

# Sulfate removal
sulfate_in = inf_concs.get('S_SO4', 0) or 0
sulfate_out = eff_concs.get('S_SO4', 0) or 0

# Update sulfur dict with all required fields
sulfur['X_hSRB_mg_COD_L'] = X_hSRB
sulfur['srb_biomass_mg_COD_L'] = total_srb
sulfur['sulfate_in_mg_L'] = sulfate_in
...
```

---

## Files Modified

| File | Changes |
|------|---------|
| `models/madm1.py` | X_ACC creation changed to avoid CAS collision |
| `reports/qmd_builder.py` | Added VFA calculation (~25 lines), sulfur data calculation (~45 lines) |

---

## Verification

```bash
# Run all tests (280 total)
../venv312/Scripts/python.exe -m pytest tests/ -v

# Run slow mADM1 E2E tests specifically
../venv312/Scripts/python.exe -m pytest tests/test_integration.py -v -m slow
```

**Results:**
- 277 fast tests: PASSED
- 3 slow tests: PASSED (mADM1 CLI E2E, report generation)
- Total: **280 tests passing**
- Slow test time: 3:42 (resolved from >10 minute timeout)

---

## Test Count History

| Phase | Tests Added | Total |
|-------|-------------|-------|
| Phase 1 | 27 | 27 |
| Phase 2 | 91 | 118 |
| Phase 2B | 0 | 118 |
| Phase 3 | 45 | 163 |
| Phase 4 | 38 | 201 |
| Phase 5 | 18 | 219 |
| Phase 6 | 28 | 247 |
| Phase 7 | 22 | 269 |
| **Phase 7B** | 11 | **280** |
