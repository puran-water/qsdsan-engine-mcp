# Investigation: mADM1 Nonsensical CH4 Production Results

## Summary

**Root Cause: COD-to-Mass Conversion Factor Missing**

The bug is in `templates/anaerobic/cstr.py:290` in the `_calculate_performance_metrics()` function. The code incorrectly treats `gas.imass['S_ch4']` as **actual CH4 mass** when it's actually **COD-equivalent mass**.

This causes a **~4x overestimate** in methane flow and yield calculations in the `performance` section of reports.

---

## Evidence

### Output File Analysis (`output_report/simulation_results.json`)

| Metric | `biogas` section (CORRECT) | `performance` section (WRONG) | Ratio |
|--------|---------------------------|------------------------------|-------|
| CH4 flow | 193.91 Nm³/d | 772.18 m³/d | **3.98x** |
| CH4 yield | 0.347 Nm³/kg COD | 1.38 m³/kg COD | **3.98x** |
| Yield efficiency | 99% | **394%** (impossible) | - |

The `biogas` section shows plausible results (99% of theoretical 0.35 Nm³/kg COD), while `performance` shows physically impossible values exceeding theory by 4x.

---

## Root Cause Analysis

### The Bug (templates/anaerobic/cstr.py:290)

```python
ch4_flow = gas.imass['S_ch4'] * 24 / 0.717  # kg/d to m3/d (0.717 kg/m3 at STP)
```

**Problems:**
1. `gas.imass['S_ch4']` returns mass in **kg COD-equivalent/hr**, not actual CH4 mass
2. Per QSDsan docs (confirmed via DeepWiki): S_ch4 has `i_mass = 0.25067` (g CH4 / g COD)
3. The code divides by CH4 density (0.717 kg/m³) but the numerator is COD mass, not CH4 mass
4. Result: Missing conversion factor causes **1/0.25067 ≈ 4x** overestimate

### The Correct Implementation (utils/analysis/anaerobic.py:385-392)

```python
STP_MOLAR_VOLUME = 22.414  # L/mol at STP
ch4_mol = _safe_get_imol(stream, 'S_ch4')  # kmol/hr (molar basis, no COD confusion)
ch4_flow = ch4_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24  # Nm³/d
```

This uses **molar flow** (`stream.imol[]`) and ideal gas law conversion, avoiding the COD-mass confusion entirely.

### Gold Standard Comparison (anaerobic-design-mcp)

The gold standard implementation:
1. Uses molar flow (`stream.F_mol`, `stream.imol[]`) consistently
2. Converts using STP molar volume (22.414 L/mol)
3. Explicitly documents "Nm³ at STP" throughout
4. Has hardcoded theoretical max validation: `theoretical_yield = 0.35  # Nm³ CH4/kg COD at STP`

---

## DeepWiki Confirmation

From QSD-Group/QSDsan documentation:
> - `imass['S_ch4']` returns mass flow rate in **kg/hr**
> - S_ch4 is measured_as='COD', so `imass` gives **COD-equivalent mass**
> - The `i_mass` factor for S_ch4 is **0.25067 g CH4 / g COD**
> - To convert: multiply COD-eq mass by i_mass factor first

---

## Fix Required

### Recommended Fix: Molar Basis with Enhanced Fallback

**Vetted by Codex CLI** - confirmed diagnosis and approach via QSDsan source code inspection.

```python
# templates/anaerobic/cstr.py:288-292
# BEFORE:
try:
    ch4_flow = gas.imass['S_ch4'] * 24 / 0.717  # WRONG - treats COD mass as CH4 mass
except:
    ch4_flow = biogas_m3_d * 0.6

# AFTER (fail-fast approach):
STP_MOLAR_VOLUME = 22.414  # L/mol at STP
CH4_DENSITY_STP = 0.717    # kg/m³ at STP
try:
    # Primary: molar basis (correct for ADM1 with adjust_MW_to_measured_as=True)
    ch4_mol = gas.imol['S_ch4']  # kmol/hr
    ch4_flow = ch4_mol * STP_MOLAR_VOLUME * 24  # Nm³/d
except Exception as e1:
    try:
        # Fallback: mass basis with i_mass correction
        i_mass_ch4 = gas.components.S_ch4.i_mass  # ~0.25067 g CH4/g COD
        ch4_flow = gas.imass['S_ch4'] * i_mass_ch4 * 24 / CH4_DENSITY_STP
    except Exception as e2:
        # FAIL LOUDLY - do not silently produce wrong results
        raise RuntimeError(
            f"Cannot calculate CH4 flow: molar method failed ({e1}), "
            f"mass method failed ({e2}). Check gas stream configuration."
        )
```

**Why this approach:**
- Primary method matches `utils/analysis/anaerobic.py` (proven correct)
- First fallback handles edge case where `imol` might be unreliable
- Dynamically retrieves `i_mass` from component instead of hardcoding
- **Fail-fast**: Raises exception rather than silently producing wrong results

---

## Verification Plan

After fix:
1. Re-run test simulation with same input (`output_test/input.json`)
2. Verify `biogas.methane_flow_Nm3_d` ≈ `performance.methane_m3_d`
3. Verify `specific_CH4_yield_m3_kg_COD` ≤ 0.35 (theoretical max)
4. Run existing test suite: `pytest tests/ -v`

---

## Files to Modify

| File | Lines | Change |
|------|-------|--------|
| `templates/anaerobic/cstr.py` | 290 | Fix CH4 flow calculation |
| `templates/anaerobic/cstr.py` | 296 | Update yield calculation to use corrected flow |

---

## Impact Assessment

- **Severity:** HIGH - Produces physically impossible results that violate thermodynamic limits
- **Scope:** All mADM1 simulations using `build_and_run()` template
- **Data affected:** `performance.methane_m3_d` and `performance.specific_CH4_yield_m3_kg_COD`
- **Workaround:** Use `biogas.methane_flow_Nm3_d` and `biogas.methane_yield_Nm3_kg_COD` (already correct)

---

## Codex Verification Summary

**Codex CLI** independently verified the diagnosis by:
1. Inspecting QSDsan source: `qsdsan/_component.py:333`, `tests/test_junctions.py:205-206`
2. Confirming `i_mass` calculation: `chem_MW/cod` = 16.04/64 ≈ 0.25067
3. Finding related issue: `QSD-Group/QSDsan#100` (molar flow inaccuracy for measured_as components)
4. Validating math: 1/0.25067 ≈ 3.99 matches observed 4x error

**Codex recommendation:** Use molar basis with tiered fallback (incorporated in fix above).

---

## Conclusion

This is a **reporting bug** in the performance metrics calculation, not a simulation bug. The underlying QSDsan mADM1 simulation produces correct results - the error is only in how `_calculate_performance_metrics()` interprets the gas stream data. The `analyze_gas_stream()` function already implements the correct calculation, so the fix is to align the performance metrics with that approach.
