# Phase 10: Address Known Limitations

**Status:** Complete (2026-01-22)
**Goal:** Resolve 6 known limitations documented in CLAUDE.md.

---

## Summary of Changes

| # | Limitation | Solution | Status |
|---|-----------|----------|--------|
| 1 | QSDsan Import Time | Enhanced pre-warming + process model caching | Complete |
| 2 | mADM1 Kinetic Params | 80+ parameter schema in `core/kinetic_params.py` | Complete |
| 3 | pH/Alkalinity Fallback | Native `pcm()` function in `utils/simulate_madm1.py` | Complete |
| 4 | Junction Property Alignment | Documentation enhancement | Complete |
| 5 | Junction Cycles | Traversal depth limit (20 levels) in `server.py` | Complete |
| 6 | Fan-in Validation | Auto-insert junctions on model mismatch | Complete |

---

## Limitation 1: QSDsan Import Time (~18s cold start)

**Problem:** QSDsan import blocks the MCP event loop for ~18 seconds.

**Solution:** Enhanced `utils/qsdsan_loader.py` with:
- Process model caching for both mADM1 and ASM2d
- `full_warmup()` async function for comprehensive pre-loading
- `get_process_model()` for cached model retrieval
- Documentation in `NOTES_FOR_SKILLS.md`

**Files Modified:**
- `utils/qsdsan_loader.py` - Added `_do_load_asm2d_model()`, `get_process_model()`, `full_warmup()`
- `models/asm2d.py` - Added `create_asm2d_process()`
- `NOTES_FOR_SKILLS.md` - Added warmup documentation section

---

## Limitation 2: mADM1 Kinetic Parameters

**Problem:** `kinetic_params` argument was ignored for mADM1 templates.

**Solution:** Created `core/kinetic_params.py` with:
- `MADM1_KINETIC_SCHEMA` - 80+ parameter definitions with defaults, ranges, units
- `validate_kinetic_params()` - Validation with out-of-range warnings
- `get_kinetic_param_docs()` - Human-readable documentation

**Files Created:**
- `core/kinetic_params.py` (new)

**Files Modified:**
- `templates/anaerobic/cstr.py` - Pass validated kinetic params to simulation
- `utils/simulate_madm1.py` - Forward kinetic params to `ModifiedADM1`
- `server.py` - Updated `simulate_system` docstring

---

## Limitation 3: pH/Alkalinity Fallback

**Problem:** pH calculation fell back to pH=7.0 when external module unavailable.

**Solution:** Replaced external dependency with native `pcm()` function from `models/madm1.py`.

**Files Modified:**
- `utils/simulate_madm1.py` - `update_ph_and_alkalinity()` now uses native `pcm()`

---

## Limitation 4: Junction Property Alignment

**Problem:** Documentation about `X_AUT -> X_h2` mapping was unclear.

**Solution:** Enhanced docstrings in `core/junction_components.py` clarifying:
- `COMPONENT_ALIGNMENT` is for property matching during stoichiometry
- Actual mass conversion uses QSDsan's `_compile_reactions()`
- Upstream junctions only balance COD/TKN/TP (not S/Fe/Al)

**Files Modified:**
- `core/junction_components.py` - Enhanced docstrings
- `CLAUDE.md` - Updated Known Limitations section

---

## Limitation 5: Junction Chains/Cycles Detection

**Problem:** Cycles with junctions only detected at `build_system` time.

**Solution:** Added traversal depth limit to `compute_effective_model_at_unit()`:
- Maximum depth of 20 levels
- Raises `ValueError` if depth exceeded (likely cycle)

**Files Modified:**
- `server.py` - Added `_depth` parameter to `compute_effective_model_at_unit()`

---

## Limitation 6: Fan-in Model Validation

**Problem:** Mixing streams from different models at fan-in produced warning only.

**Solution:** Auto-insert junction units when detecting mismatched models:
- `find_junction_for_conversion()` in `core/unit_registry.py`
- `_auto_insert_junction()` helper in `server.py`
- Auto-inserted units have `auto_inserted=True` flag
- Unit IDs prefixed with `_auto_`

**Files Modified:**
- `server.py` - Added auto-insert junction logic in `create_unit()`
- `core/unit_registry.py` - Added `find_junction_for_conversion()`
- `utils/flowsheet_session.py` - Added `auto_inserted` field to `UnitConfig`

---

## Critical Context: Custom vs Upstream

**Our `ModifiedADM1` (63 components) is a CUSTOM IMPLEMENTATION:**
- Defined in `models/madm1.py` - NOT from upstream QSDsan
- Includes: SRB processes, EBPR, Fe/Al chemistry, mineral precipitation
- **Our `pcm()` function is custom** - upstream uses `solve_pH()`

**Upstream junction limitations:**
- `ASM2dtomADM1` and `mADM1toASM2d` target upstream ADM1p, not our 63-component model
- Upstream balancing: COD/TKN/TP only - does NOT preserve S/Fe/mineral balances
- For our mADM1, S/Fe/Al species require custom handling in `core/junction_units.py`

---

## Tests

All Phase 10 tests in `tests/test_phase10.py`:
- `TestKineticParams` - 5 tests for parameter schema and validation
- `TestNativePHCalculation` - 2 tests for pcm() integration
- `TestAutoInsertJunctions` - 5 tests for auto-insert functionality
- `TestTraversalDepthLimit` - 2 tests for cycle detection
- `TestImportTimeMitigation` - 3 tests for warmup functions
- `TestPhase10Integration` - 3 integration tests

**Test Count:** 20 Phase 10 tests passing

---

## Migration Notes

- **Kinetic params:** Backward compatible (optional argument)
- **pH calculation:** Minor differences possible (more accurate than fallback)
- **Auto-insert junctions:** Silently adds junction units - visible in `get_flowsheet_session` response
- **Auto-inserted units:** Prefixed with `_auto_` and have `auto_inserted=True` for identification
