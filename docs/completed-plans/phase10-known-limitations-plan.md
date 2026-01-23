# Phase 10: Address Known Limitations

**Goal:** Resolve 6 known limitations in CLAUDE.md.

---

## Critical Context: Custom vs Upstream

**Our `ModifiedADM1` (63 components) is a CUSTOM IMPLEMENTATION:**
- Defined in `models/madm1.py:1041` - NOT from upstream QSDsan
- Upstream's `ModifiedADM1` is **commented out** in `qsdsan/processes/__init__.py` (planned but inactive)
- Upstream has `ADM1` (35 components) and `ADM1p` (~50 components) only
- Our model adds: SRB processes (X_hSRB, X_aSRB, etc.), EBPR (X_PAO, X_PP, X_PHA), Fe/Al chemistry, mineral precipitation
- **Our `pcm()` function is custom** - upstream uses `solve_pH()` / `acid_base_rxn()`

**Upstream junction limitations:**
- `ASM2dtomADM1` and `mADM1toASM2d` target upstream `ADM1_p_extension`, not our 63-component model
- Upstream balancing: COD/TKN/TP only - does NOT preserve S/Fe/mineral balances
- Auto-inserting upstream junctions for our mADM1 would cause mass imbalance

---

## Summary of Changes

| # | Limitation | Solution | Effort |
|---|-----------|----------|--------|
| 1 | QSDsan Import Time | Enhanced pre-warming + documentation | Low |
| 2 | mADM1 Kinetic Params | Pass through to **our custom** `ModifiedADM1` | Medium |
| 3 | pH/Alkalinity Fallback | Use **our custom** `pcm()` function (already in `models/madm1.py`) | Low |
| 4 | Junction Property Alignment | Documentation enhancement (no code change) | Low |
| 5 | Junction Cycles | Early detection in `create_unit` | Low |
| 6 | Fan-in Validation | **Auto-insert junctions** (custom impl for our mADM1) | Medium |

---

## Limitation 1: QSDsan Import Time (~18s cold start)

**Current:** `utils/qsdsan_loader.py` provides async loading but import remains slow.

**Upstream Finding:** No native lazy loading in QSDsan - inherent to library.

**Resolution:** The current mitigation is adequate. Enhancements:

### Files to Modify
- `utils/qsdsan_loader.py` - Add process model caching

### Implementation
1. Cache compiled `ModifiedADM1` and `ASM2d` process models separately from components
2. Add `functools.lru_cache` to `create_madm1_cmps()` calls
3. Document warm-up usage in NOTES_FOR_SKILLS.md

### Tests
- `tests/test_phase10.py::test_warmup_reduces_latency`

---

## Limitation 2: mADM1 Kinetic Parameters (ignored)

**Current:** `templates/anaerobic/cstr.py:68-72` ignores `kinetic_params` with warning.

**Our Custom `ModifiedADM1.__new__()` (`models/madm1.py:1265`) accepts 80+ kinetic parameters:**
- Rate constants: `k_su`, `k_aa`, `k_fa`, `k_c4`, `k_pro`, `k_ac`, `k_h2`
- Half-saturation: `K_su`, `K_aa`, `K_fa`, `K_c4`, `K_pro`, `K_ac`, `K_h2`
- Inhibition: `KI_h2_fa`, `KI_h2_c4`, `KI_h2_pro`, `KI_nh3`, `KI_h2s_*`
- pH limits: `pH_limits_aa`, `pH_limits_ac`, `pH_limits_h2`
- SRB params: `k_hSRB`, `k_aSRB`, `k_pSRB`, `k_c4SRB`, `K_*SRB` (custom)
- Mineral precipitation: `k_mmp`, `pKsp`, `K_dis` (custom)

**Note:** This is OUR custom model, not upstream. Upstream `ADM1`/`ADM1p` have similar parameter handling but different component sets.

### Files to Modify
- `templates/anaerobic/cstr.py` - Pass `kinetic_params` to model
- `utils/simulate_madm1.py` - Accept and forward kinetic params
- `core/kinetic_params.py` (new) - Parameter schema and validation
- `server.py` - Expose in `simulate_system` tool docs

### Implementation

1. **Create kinetic parameter schema** (`core/kinetic_params.py`):
```python
MADM1_KINETIC_SCHEMA = {
    'k_su': {'default': 30.0, 'range': (5, 100), 'units': 'd^-1'},
    'k_aa': {'default': 50.0, 'range': (10, 150), 'units': 'd^-1'},
    # ... (extract from models/madm1.py lines 1265-1294)
}

def validate_kinetic_params(params: Dict, schema=MADM1_KINETIC_SCHEMA) -> Tuple[Dict, List[str]]:
    """Validate and merge params with defaults, return warnings."""
```

2. **Update template** (`templates/anaerobic/cstr.py:68-76`):
```python
# REMOVE the warning block, REPLACE with:
from core.kinetic_params import validate_kinetic_params, MADM1_KINETIC_SCHEMA

validated_params, kinetic_warnings = validate_kinetic_params(kinetic_params or {})
for w in kinetic_warnings:
    logger.warning(w)
```

3. **Pass to simulation** (`utils/simulate_madm1.py`):
```python
def run_simulation_sulfur(..., kinetic_params: Optional[Dict] = None):
    # In _create_madm1_model():
    madm1_model = ModifiedADM1(components=cmps, **kinetic_params)
```

### Tests
- `tests/test_phase10.py::test_kinetic_params_modify_acetate_uptake`
- `tests/test_phase10.py::test_invalid_kinetic_param_rejected`
- `tests/test_phase10.py::test_srb_kinetic_params_affect_sulfate_reduction`

---

## Limitation 3: pH/Alkalinity Fallback

**Current:** `utils/simulate_madm1.py:36-56` tries to import external `calculate_ph_and_alkalinity_fixed` module, falls back to pH=7.0.

**Our Custom Solution:** `models/madm1.py:433` contains **our custom `pcm()` function** that provides complete pH calculation using Brent's method with:
- Temperature-corrected Ka values (Van't Hoff)
- Full acid-base equilibrium (VFAs, ammonia, carbonate, sulfide)
- Charge balance iteration including Fe/Al trivalents (custom extension)

**Upstream Comparison:** Upstream QSDsan uses `solve_pH()` and `acid_base_rxn()` in `_adm1.py`. There is NO `pcm` function upstream. Our `pcm()` is purpose-built for our 63-component model.

### Files to Modify
- `utils/simulate_madm1.py` - Use **our custom** `pcm()` instead of external module

### Implementation

Replace lines 35-56 with:
```python
def update_ph_and_alkalinity(stream, params=None):
    """Calculate pH using our custom mADM1 pcm() function."""
    from models.madm1 import pcm  # Our custom function, NOT upstream

    # Build state array from stream concentrations
    cmps = stream.components
    concs = np.array([stream.imass[cmp.ID] / stream.F_vol for cmp in cmps])

    # Get default params if not provided
    if params is None:
        params = _get_default_pcm_params()

    # Calculate pH
    pH, free_nh3, free_co2, activities = pcm(concs, params)

    stream._pH = pH
    # Calculate alkalinity from carbonate equilibrium
    S_IC = stream.imass['S_IC'] / stream.F_vol  # kg/m3
    Ka_co2 = 10**(-6.35)  # First dissociation at 25C
    HCO3_fraction = Ka_co2 / (10**(-pH) + Ka_co2)
    stream._SAlk = S_IC * HCO3_fraction * 1000 / 12  # meq/L

    return stream
```

### Tests
- `tests/test_phase10.py::test_ph_calculation_matches_reference`
- `tests/test_phase10.py::test_ph_varies_with_temperature`
- `tests/test_phase10.py::test_alkalinity_from_carbonate`

---

## Limitation 4: Junction Property Alignment (X_AUT -> X_h2)

**Current:** Documentation states this is "for property matching only; actual conversion uses QSDsan's `_compile_reactions()`."

**Upstream Finding:** QSDsan's junction `_compile_reactions()` handles:
- Biomass splitting to proteins/lipids/carbs via `frac_deg` parameter
- COD/TKN/TP balancing via `balance_cod_tkn_tp()`
- Component property validation via `check_component_properties()`

**IMPORTANT LIMITATION:** Upstream junctions only balance **COD/TKN/TP**. They do NOT preserve:
- Sulfur balance (S_SO4, S_IS, SRB biomass)
- Iron balance (S_Fe2, S_Fe3, X_FeS, X_Fe3PO42)
- Aluminum balance (S_Al, X_AlPO4)
- Mineral precipitation species

For our 63-component mADM1, we need custom junction implementations in `core/junction_units.py` that extend upstream balancing.

### Files to Modify
- `core/junction_components.py` - Enhanced docstrings clarifying custom vs upstream
- `CLAUDE.md` - Clarify this is a design note, document upstream limitations

### Implementation

Add clarifying comment to `core/junction_components.py`:
```python
# NOTE: COMPONENT_ALIGNMENT is for property matching during stoichiometry
# calculation. Actual mass conversion is handled by _compile_reactions().
#
# UPSTREAM LIMITATION: QSDsan junctions only balance COD/TKN/TP.
# For our 63-component mADM1 with SRB/Fe/Al/minerals, we implement
# custom junction logic in core/junction_units.py that extends upstream
# to preserve S/Fe/Al/mineral balances.
```

Update CLAUDE.md to reclassify as "Design Note" and document upstream junction limitations.

### Tests
- Existing junction tests in `tests/test_mixed_model.py` cover this

---

## Limitation 5: Junction Chains/Cycles Detection

**Current:** Complex chains traverse correctly, but cycles with junctions detected only at `build_system` time.

**Upstream Clarification:** QSDsan `System` supports **multiple recycles** via list:
```python
sys = qs.System('MLE', path=(...), recycle=[RAS, IR])  # Multiple recycles!
```
Our MLE template already uses this correctly. The limitation is only about *early detection* during `create_unit`.

### Files to Modify
- `server.py` - Add traversal depth limit to prevent infinite loops

### Implementation

Add traversal depth limit to `compute_effective_model_at_unit()`:
```python
def compute_effective_model_at_unit(
    session, unit_inputs, explicit_model, _depth: int = 0
) -> Tuple[str, List[str]]:
    if _depth > 20:
        raise ValueError("Junction chain too deep (>20), possible cycle")
    # ... recursive calls pass _depth + 1
```

### Tests
- `tests/test_phase10.py::test_traversal_depth_limit_prevents_infinite_loop`
- `tests/test_phase10.py::test_complex_junction_chain_succeeds`

---

## Limitation 6: Fan-in Model Validation → Auto-Insert Junctions

**Current:** `server.py:715-721` produces warning but allows mismatched models, leading to runtime failures.

**Solution:** Automatically insert junction units when detecting mismatched models at fan-in points.

**IMPORTANT:** Upstream junctions (`ASM2dtomADM1`, `mADM1toASM2d`) target upstream `ADM1_p_extension`, NOT our 63-component mADM1. Auto-inserting upstream junctions would cause:
- Component mismatch (our SRB/Fe/Al species not mapped)
- Mass imbalance (S/Fe/Al not conserved)

**For our custom mADM1, we must use our custom junction implementations in `core/junction_units.py`.**

### Files to Modify
- `server.py` - Add `_auto_insert_junction()` helper, modify `create_unit()`
- `core/unit_registry.py` - Add `find_junction_for_conversion()` function
- `utils/flowsheet_session.py` - Track auto-inserted units

### Implementation

1. **Add junction finder** (`core/unit_registry.py`):
```python
def find_junction_for_conversion(from_model: str, to_model: str) -> Optional[str]:
    """
    Find junction unit type that converts from_model to to_model.

    NOTE: For our custom mADM1 (63 components), we use our custom junction
    implementations in core/junction_units.py, NOT upstream QSDsan junctions.
    JUNCTION_MODEL_TRANSFORMS maps to our custom implementations.
    """
    from_norm = normalize_model_name(from_model)
    to_norm = normalize_model_name(to_model)
    for junction, (inp, out) in JUNCTION_MODEL_TRANSFORMS.items():
        if normalize_model_name(inp) == from_norm and normalize_model_name(out) == to_norm:
            return junction
    return None
```

2. **Add auto-insert helper** (`server.py`):
```python
def _auto_insert_junction(
    session: FlowsheetSession,
    source_unit_id: str,
    source_port: int,
    source_model: str,
    target_model: str,
) -> Tuple[str, str]:
    """
    Auto-insert a junction unit to convert source_model to target_model.

    Returns (junction_unit_id, junction_output_port).
    """
    junction_type = find_junction_for_conversion(source_model, target_model)
    if not junction_type:
        raise ValueError(f"No junction available for {source_model} -> {target_model}")

    # Generate unique ID
    junction_id = f"_auto_{junction_type}_{source_unit_id}"
    counter = 1
    while junction_id in session.units:
        junction_id = f"_auto_{junction_type}_{source_unit_id}_{counter}"
        counter += 1

    # Create junction unit config
    junction_config = UnitConfig(
        unit_type=junction_type,
        params={},
        inputs=[f"{source_unit_id}-{source_port}"],
        outputs=[f"{junction_id}-0"],
        model_type=target_model,  # Output model
        auto_inserted=True,  # Track for debugging
    )
    session.units[junction_id] = junction_config

    logger.info(f"Auto-inserted {junction_type} junction '{junction_id}' to convert {source_model} -> {target_model}")
    return junction_id, f"{junction_id}-0"
```

3. **Modify create_unit fan-in logic** (`server.py:715-726`):
```python
# Fan-in: auto-insert junctions for mismatched models
unique_models = set(input_models)
if len(unique_models) > 1:
    # Determine target model (session primary or majority)
    target_model = normalize_model_name(session.primary_model_type)

    # Rewrite inputs to insert junctions where needed
    new_inputs = []
    for i, (inp, inp_model) in enumerate(zip(unit_inputs, input_models)):
        if inp_model != target_model:
            # Auto-insert junction
            source_info = parse_port_notation(inp)
            junction_id, junction_port = _auto_insert_junction(
                session, source_info.unit_id, source_info.port_index,
                inp_model, target_model
            )
            new_inputs.append(junction_port)
            warnings.append(f"Auto-inserted junction to convert {inp_model} -> {target_model} for input '{inp}'")
        else:
            new_inputs.append(inp)

    # Use rewritten inputs
    inputs = new_inputs
```

4. **Track auto-inserted units** (`utils/flowsheet_session.py`):
```python
@dataclass
class UnitConfig:
    unit_type: str
    params: Dict[str, Any]
    inputs: List[str]
    outputs: List[str]
    model_type: Optional[str] = None
    auto_inserted: bool = False  # NEW: Track auto-inserted junctions
```

### Example Behavior

Before (warning only):
```
create_unit(Mixer, inputs=["ASM2d_unit-0", "mADM1_unit-0"])
# Warning: Multiple input models detected: ['ASM2d', 'mADM1']
# Later: RuntimeError at build_system due to component mismatch
```

After (auto-insert):
```
create_unit(Mixer, inputs=["ASM2d_unit-0", "mADM1_unit-0"])
# Auto-inserts: _auto_mADM1toASM2d_mADM1_unit junction
# Rewrites inputs to: ["ASM2d_unit-0", "_auto_mADM1toASM2d_mADM1_unit-0"]
# Returns success with warning: "Auto-inserted junction to convert mADM1 -> ASM2d"
```

### Tests
- `tests/test_phase10.py::test_auto_insert_junction_on_model_mismatch`
- `tests/test_phase10.py::test_auto_insert_multiple_junctions`
- `tests/test_phase10.py::test_auto_insert_fails_gracefully_when_no_junction`
- `tests/test_phase10.py::test_auto_inserted_units_tracked`

---

## Critical Files Summary

| File | Changes |
|------|---------|
| `templates/anaerobic/cstr.py` | Pass kinetic params to model |
| `utils/simulate_madm1.py` | Native pH calculation, forward kinetic params |
| `core/kinetic_params.py` (new) | Parameter schema and validation |
| `core/junction_components.py` | Documentation enhancement |
| `server.py` | Auto-insert junctions, traversal depth limit |
| `utils/flowsheet_session.py` | Add `auto_inserted` field to UnitConfig |
| `core/unit_registry.py` | Add `find_junction_for_conversion()` |
| `CLAUDE.md` | Update limitations section |
| `tests/test_phase10.py` (new) | All new tests |

---

## Verification Plan

1. **Unit Tests:** Run `pytest tests/test_phase10.py -v`
2. **Regression:** Run `pytest tests/ -v` (all 322+ tests pass)
3. **Integration - Kinetic Params:**
   ```bash
   python cli.py simulate --template anaerobic_cstr \
     --state tests/test_madm1_state.json \
     --kinetic-params '{"k_ac": 8.0, "K_ac": 0.15}'
   ```
4. **pH Validation:** Compare pH output against reference case with known state
5. **Auto-Junction Test:** Create mixed-model flowsheet via MCP, verify junctions auto-inserted

---

## Migration Notes

- **Kinetic params:** Backward compatible (optional argument)
- **pH calculation:** Minor differences possible (more accurate)
- **Auto-insert junctions:** Silently adds junction units - visible in `get_flowsheet_session` response
- **Auto-inserted units:** Prefixed with `_auto_` and have `auto_inserted=True` for identification

---

## Implementation Order

1. Limitation #2 (Kinetic Params) - High impact, unblocks user functionality
2. Limitation #3 (pH/Alkalinity) - Quick win, removes external dependency
3. Limitation #6 (Auto-Insert Junctions) - Medium effort, major UX improvement
4. Limitation #5 (Traversal Depth) - Low effort, safety guard
5. Limitation #4 (Documentation) - Minimal code change
6. Limitation #1 (Import Time) - Enhancement to existing solution
