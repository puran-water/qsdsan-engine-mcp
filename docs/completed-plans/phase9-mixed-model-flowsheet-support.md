# Phase 9: Mixed-Model Flowsheet Support

**Status:** Complete (2026-01-22)
**Target:** Enable creation of mixed-model flowsheets where different units use different process models (e.g., ASM2d aerobic zones connected to mADM1 anaerobic digesters via junction units)

---

## Codex Review Summary (2026-01-22)

**Assessment:** REVISE - Core approach matches QSDsan, but fixes needed

| Finding | Status |
|---------|--------|
| Transform registry covers all 8 concrete junctions | OK |
| `ADM1ptomASM2d` input model wrong (`ADM1` → `ADM1p`) | **FIXED BELOW** |
| "mADM1" vs "ADM1p" naming confusion | **ADDRESSED** |
| Fan-in validation needed | **ADDED** |
| Junction chains/cycles need handling | **NOTED** |

---

## Problem Statement

When a user creates an ASM2d session and tries to add an `AnaerobicCSTRmADM1` (which only supports mADM1), the `create_unit()` tool fails:

```
Error: Unit 'AnaerobicCSTRmADM1' is not compatible with model 'ASM2d'.
Compatible models: ['mADM1']
```

This happens even if the user intends to place a junction unit (`ASM2dtomADM1`) before the anaerobic reactor. The validation check at unit creation time doesn't know about the planned flowsheet topology.

---

## Root Cause Analysis

| Component | Location | Issue |
|-----------|----------|-------|
| Unit validation | `server.py:682-684` | Checks against `effective_model = model_type or session.primary_model_type` |
| Compatibility function | `core/unit_registry.py:891-920` | `validate_model_compatibility()` only knows unit type + requested model |
| No zone tracking | N/A | System doesn't track "model zones" created by junctions |

**Key Insight:** The build system (`flowsheet_builder.py`) already supports per-unit model types and model-aware thermo switching. The problem is only at the MCP `create_unit()` validation layer.

---

## Solution: Model Zone Tracking with Explicit Override

Implement "model zone" awareness by tracing upstream through junctions to determine the effective model at any point in the flowsheet.

### Approach

1. **Add junction model transform registry** - Map junction types to (input_model, output_model) pairs
2. **Compute effective model at unit** - Trace upstream through inputs to find last junction's output model
3. **Update create_unit validation** - Use computed effective model instead of just session.primary_model_type
4. **Add helpful suggestions** - When validation fails, suggest appropriate junction to add
5. **Honor explicit override** - If user provides `model_type` parameter, trust it

---

## Implementation Plan

### Phase 9A: Add Junction Model Transform Registry

**File:** `core/unit_registry.py` (add after line 920)

Add mapping of junction types to their input/output models:

```python
# Model name aliases (QSDsan uses ADM1_p_extension/ADM1p, we use mADM1 internally)
MODEL_ALIASES: Dict[str, Set[str]] = {
    "mADM1": {"mADM1", "ADM1p", "ADM1_p_extension"},
    "ADM1p": {"mADM1", "ADM1p", "ADM1_p_extension"},
    "mASM2d": {"mASM2d"},
    "ASM2d": {"ASM2d"},
    "ASM1": {"ASM1"},
    "ADM1": {"ADM1"},
}

def normalize_model_name(model: str) -> str:
    """Normalize model name to internal convention."""
    if model in ("ADM1p", "ADM1_p_extension"):
        return "mADM1"  # Our internal name for ADM1 with P/S/Fe extensions
    return model

# Junction transforms: (input_model, output_model)
# Note: "mADM1" = "ADM1p/ADM1_p_extension" in upstream QSDsan
JUNCTION_MODEL_TRANSFORMS: Dict[str, Tuple[str, str]] = {
    "ASM2dtomADM1": ("ASM2d", "mADM1"),      # ASM2d → mADM1 (63 components)
    "mADM1toASM2d": ("mADM1", "ASM2d"),      # mADM1 → ASM2d
    "ASM2dtoADM1": ("ASM2d", "ADM1"),        # ASM2d → ADM1 (35 components)
    "ADM1toASM2d": ("ADM1", "ASM2d"),        # ADM1 → ASM2d
    "ADM1ptomASM2d": ("mADM1", "mASM2d"),    # FIXED: was ("ADM1", "mASM2d")
    "mASM2dtoADM1p": ("mASM2d", "mADM1"),    # mASM2d → mADM1
    "ASMtoADM": ("ASM1", "ADM1"),            # Generic ASM1 → ADM1
    "ADMtoASM": ("ADM1", "ASM1"),            # Generic ADM1 → ASM1
}

def get_junction_output_model(unit_type: str) -> Optional[Tuple[str, str]]:
    """Return (input_model, output_model) for junction unit types."""
    return JUNCTION_MODEL_TRANSFORMS.get(unit_type)

def models_compatible(model_a: str, model_b: str) -> bool:
    """Check if two model names refer to the same model (accounting for aliases)."""
    norm_a = normalize_model_name(model_a)
    norm_b = normalize_model_name(model_b)
    return norm_a == norm_b

def suggest_junction_for_conversion(from_model: str, to_models: List[str]) -> Optional[str]:
    """Suggest junction unit type to convert from one model to another."""
    from_norm = normalize_model_name(from_model)
    for to_model in to_models:
        to_norm = normalize_model_name(to_model)
        for junction, (inp, out) in JUNCTION_MODEL_TRANSFORMS.items():
            if normalize_model_name(inp) == from_norm and normalize_model_name(out) == to_norm:
                return f"Add '{junction}' before this unit to convert from {from_model} to {to_model}"
    return None
```

---

### Phase 9B: Add Model Zone Computation

**File:** `server.py` (add helper function near line 670)

```python
def compute_effective_model_at_unit(
    session: "FlowsheetSession",
    unit_inputs: List[str],
    explicit_model: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Compute effective model for a unit based on upstream junctions.

    Priority:
    1. Explicit model_type if provided
    2. Output model of upstream junction (if any)
    3. Upstream unit's explicit model_type
    4. Session primary_model_type

    Returns:
        Tuple of (effective_model, list of warnings)
    """
    warnings = []

    if explicit_model:
        return normalize_model_name(explicit_model), warnings

    # Collect models from all inputs for fan-in validation
    input_models = []

    for inp in unit_inputs:
        ref = parse_port_notation(inp)
        upstream_unit_id = ref.unit_id

        if upstream_unit_id in session.units:
            upstream_config = session.units[upstream_unit_id]
            junction_transform = get_junction_output_model(upstream_config.unit_type)

            if junction_transform:
                # This is a junction - use its output model
                input_models.append(normalize_model_name(junction_transform[1]))
            elif upstream_config.model_type:
                input_models.append(normalize_model_name(upstream_config.model_type))
            else:
                # Recursively trace upstream (for junction chains)
                upstream_model, _ = compute_effective_model_at_unit(
                    session, upstream_config.inputs, upstream_config.model_type
                )
                input_models.append(upstream_model)
        else:
            # Stream input - use session primary
            input_models.append(normalize_model_name(session.primary_model_type))

    # Fan-in validation: all inputs must have same model
    unique_models = set(input_models)
    if len(unique_models) > 1:
        warnings.append(
            f"Multiple input models detected: {unique_models}. "
            f"Consider adding junctions to unify component sets before mixing."
        )

    # Return first input's model (or session primary if no inputs)
    if input_models:
        return input_models[0], warnings
    return normalize_model_name(session.primary_model_type), warnings
```

---

### Phase 9C: Update create_unit() Validation

**File:** `server.py` (modify lines 677-684)

**Before:**
```python
session = session_manager.get_session(session_id)
effective_model = model_type or session.primary_model_type

is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
if not is_compatible:
    return {"error": compat_error}
```

**After:**
```python
session = session_manager.get_session(session_id)

# Compute effective model considering upstream junctions
effective_model, zone_warnings = compute_effective_model_at_unit(
    session, inputs or [], model_type
)

# Check compatibility with computed effective model
is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
if not is_compatible:
    # Provide helpful error with junction suggestion
    from core.unit_registry import get_unit_spec, suggest_junction_for_conversion
    spec = get_unit_spec(unit_type)
    current_model = session.primary_model_type

    suggestion = suggest_junction_for_conversion(current_model, spec.compatible_models)

    error_msg = compat_error
    if suggestion:
        error_msg += f" Suggestion: {suggestion}"

    return {"error": error_msg}

# Return warnings about fan-in model mismatches (non-blocking)
result = {"unit_id": unit_id, "session_id": session_id, "effective_model": effective_model}
if zone_warnings:
    result["warnings"] = zone_warnings
```

---

### Phase 9D: Add Tests

**File:** `tests/test_mixed_model.py` (new file)

```python
"""Tests for mixed-model flowsheet construction."""
import pytest

class TestMixedModelFlowsheets:
    """Test mixed-model flowsheet construction."""

    def test_asm2d_session_with_junction_then_madm1_unit(self, session_manager):
        """Should allow mADM1 unit after ASM2dtomADM1 junction in ASM2d session."""
        # Create ASM2d session
        result = create_flowsheet_session(session_manager, "ASM2d", "test_mixed")
        session_id = result["session_id"]

        # Add influent stream
        create_stream(session_manager, session_id, "INF", 1000, {})

        # Add aerobic CSTR (ASM2d)
        create_unit(session_manager, session_id, "CSTR", "A1", {"V_max": 100}, ["INF"])

        # Add junction (ASM2d -> mADM1)
        create_unit(session_manager, session_id, "ASM2dtomADM1", "J1", {}, ["A1-0"])

        # Add mADM1 reactor - THIS SHOULD NOW WORK
        result = create_unit(session_manager, session_id, "AnaerobicCSTRmADM1", "AD1",
                           {"V_max": 500}, ["J1-0"])
        assert "error" not in result
        assert result["unit_id"] == "AD1"

    def test_error_without_junction_suggests_solution(self, session_manager):
        """Should suggest adding junction when incompatible unit added."""
        result = create_flowsheet_session(session_manager, "ASM2d", "test_suggest")
        session_id = result["session_id"]

        create_stream(session_manager, session_id, "INF", 1000, {})

        # Try to add mADM1 reactor without junction
        result = create_unit(session_manager, session_id, "AnaerobicCSTRmADM1", "AD1",
                           {"V_max": 500}, ["INF"])

        assert "error" in result
        assert "ASM2dtomADM1" in result["error"]  # Suggestion included

    def test_explicit_model_override(self, session_manager):
        """Explicit model_type should bypass zone check."""
        result = create_flowsheet_session(session_manager, "ASM2d", "test_override")
        session_id = result["session_id"]

        create_stream(session_manager, session_id, "INF", 1000, {}, model_type="mADM1")

        # Add mADM1 reactor with explicit model override
        result = create_unit(session_manager, session_id, "AnaerobicCSTRmADM1", "AD1",
                           {"V_max": 500}, ["INF"], model_type="mADM1")
        assert "error" not in result

    def test_fan_in_warns_on_model_mismatch(self, session_manager):
        """Fan-in with different models should warn but not error."""
        result = create_flowsheet_session(session_manager, "ASM2d", "test_fanin")
        session_id = result["session_id"]

        create_stream(session_manager, session_id, "INF1", 500, {})
        create_stream(session_manager, session_id, "INF2", 500, {}, model_type="mADM1")

        # Mixer receiving streams from different models should warn
        result = create_unit(session_manager, session_id, "Mixer", "M1",
                           {}, ["INF1", "INF2"])
        assert "error" not in result  # Mixer is model-agnostic
        assert "warnings" in result
        assert "Multiple input models" in result["warnings"][0]

    def test_junction_chain_traversal(self, session_manager):
        """Junction chains should compute final output model correctly."""
        result = create_flowsheet_session(session_manager, "ASM2d", "test_chain")
        session_id = result["session_id"]

        create_stream(session_manager, session_id, "INF", 1000, {})
        create_unit(session_manager, session_id, "CSTR", "A1", {"V_max": 100}, ["INF"])
        create_unit(session_manager, session_id, "ASM2dtomADM1", "J1", {}, ["A1-0"])
        # After J1, effective model is mADM1

        result = create_unit(session_manager, session_id, "AnaerobicCSTRmADM1", "AD1",
                           {"V_max": 500}, ["J1-0"])
        assert "error" not in result
        assert result["effective_model"] == "mADM1"
```

---

## Edge Cases

| Case | Handling |
|------|----------|
| Multiple inputs from different models | **Fan-in validation**: Error unless model-agnostic unit OR all inputs same model |
| Deferred connections (recycles) | Use session primary until connection exists; validate at build time |
| Nested junctions | Use closest upstream junction's output model |
| Junction-to-junction | Validate junction's expected input matches upstream output |
| Explicit override provided | Trust user, skip zone computation |
| Junction chains (A→J1→J2→B) | Traverse full chain; compute final output model |
| Cycles with junctions | Detect cycles in `build_system`; warn if junction in cycle |
| Component property alignment | Surface QSDsan warnings about `measured_as` mismatches |

---

## Files to Modify

| File | Changes |
|------|---------|
| `core/unit_registry.py` | Add `JUNCTION_MODEL_TRANSFORMS`, `get_junction_output_model()`, `suggest_junction_for_conversion()` |
| `server.py` | Add `compute_effective_model_at_unit()`, update `create_unit()` validation |
| `tests/test_mixed_model.py` | New file with mixed-model flowsheet tests |

---

## Verification Plan

### Unit Tests
```bash
python -m pytest tests/test_mixed_model.py -v
```

### Manual CLI Verification
```bash
# Create ASM2d session
python cli.py flowsheet new --model ASM2d --id mixed_test

# Add influent
python cli.py flowsheet add-stream --session mixed_test --id inf --flow 1000 \
  --concentrations '{"S_F": 100, "S_NH4": 30}'

# Add aerobic reactor
python cli.py flowsheet add-unit --session mixed_test --type CSTR --id A1 \
  --params '{"V_max": 200, "aeration": 2.0}' --inputs '["inf"]'

# Add junction (this should work)
python cli.py flowsheet add-unit --session mixed_test --type ASM2dtomADM1 --id J1 \
  --inputs '["A1-0"]'

# Add mADM1 digester (THIS SHOULD NOW WORK)
python cli.py flowsheet add-unit --session mixed_test --type AnaerobicCSTRmADM1 --id AD1 \
  --params '{"V_max": 500}' --inputs '["J1-0"]'

# Build and verify
python cli.py flowsheet build --session mixed_test
```

### Regression
```bash
python -m pytest tests/ -v  # All existing tests should pass
```

---

## Success Criteria

1. Creating `AnaerobicCSTRmADM1` after `ASM2dtomADM1` junction in ASM2d session succeeds
2. Error message suggests appropriate junction when incompatible unit added without junction
3. Explicit `model_type` override works as escape hatch
4. All 292+ existing tests continue to pass
5. Build and simulate completes for mixed-model flowsheet
