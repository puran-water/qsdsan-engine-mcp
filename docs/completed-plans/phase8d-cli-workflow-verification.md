# Phase 8D: CLI Workflow Verification and Bug Fixes

**Status:** Complete (2026-01-22)
**Test Count:** 292 tests passing

## Executive Summary

Following Phase 8 completion, CLI workflow verification revealed four additional bugs when attempting to reproduce the MLE flowsheet workflow from the PDF test document.

---

## Bugs Found and Fixed

| # | Bug | Location | Fix |
|---|-----|----------|-----|
| 1 | WasteStream.sink is read-only | `flowsheet_builder.py:873` | Changed to `dst_unit.ins.append(src_port)` - BioSTEAM Mixer has `_ins_size_is_fixed=False` |
| 2 | Splitter `split` array causes "truth value of array is ambiguous" | `flowsheet_builder.py:1124` | Added numpy array handling in unit_analysis extraction |
| 3 | Effluent detection picks WAS instead of clarifier effluent | `flowsheet_builder.py:1311-1350` | Added priority detection: (1) named "effluent", (2) clarifier outs[0], (3) lowest TSS terminal stream |
| 4 | ASM1 uses `S_O` for DO, not `S_O2` | `flowsheet_builder.py:624-634` | Added model-aware DO_ID mapping for ASM1 |

---

## Bug 1: Mixer Dynamic Input Connection

### Problem
When connecting recycle streams to a Mixer unit, the code tried to set `src_port.sink = dst_unit`, but WasteStream's `sink` property is read-only.

### Root Cause
BioSTEAM's `sink` property is a computed property, not a setter.

### Fix
Use `dst_unit.ins.append(src_port)` instead. BioSTEAM Mixer has `_ins_size_is_fixed = False`, allowing dynamic additions to the `ins` list.

```python
# Before (broken):
src_port.sink = dst_unit

# After (working):
dst_unit.ins.append(src_port)
```

---

## Bug 2: Splitter Array Split Handling

### Problem
When extracting unit analysis for Splitter units, the code checked `if unit.split:` which fails when `split` is a numpy array (per-component splits).

### Root Cause
The truthiness check `if unit.split:` on a numpy array raises "the truth value of an array with more than one element is ambiguous".

### Fix
Added explicit numpy array handling:

```python
if hasattr(unit, 'split'):
    split_val = unit.split
    if split_val is not None:
        import numpy as np
        if isinstance(split_val, np.ndarray):
            params['split_ratio'] = float(split_val[0]) if len(split_val) > 0 else None
            params['split_is_array'] = True
        else:
            params['split_ratio'] = float(split_val)
```

---

## Bug 3: Effluent Detection Logic

### Problem
When no stream has "effluent" in its name, the system fell back to `system.streams[-1]` which was the WAS stream (100 m³/d) instead of the clarifier effluent (565 m³/d).

### Root Cause
No logic to identify clarifier effluent or exclude recycle/waste streams.

### Fix
Implemented priority-based effluent detection:
1. Streams with "effluent" in name
2. First output of clarifier units (`outs[0]`)
3. Terminal streams with lowest TSS (clarified water)
4. Fallback to last stream

```python
# Priority 2: First output of clarifier units (outs[0] is effluent)
clarifier_types = ('Clarifier', 'FlatBottomCircularClarifier', 'IdealClarifier', 'PrimaryClarifier')
for unit in system.units:
    if any(ct in type(unit).__name__ for ct in clarifier_types):
        if unit.outs and unit.outs[0] and unit.outs[0].F_vol > 0:
            effluent_streams = [unit.outs[0]]
            break
```

---

## Bug 4: ASM1 DO Component ID

### Problem
ASM1 model uses `S_O` for dissolved oxygen, but the CSTR unit defaults to `DO_ID='S_O2'` (ASM2d convention).

### Root Cause
Unit registry hardcodes `DO_ID: "S_O2"` for CSTR, incompatible with ASM1.

### Fix
Added model-aware DO_ID mapping:

```python
elif model_type == "ASM1":
    kwargs.setdefault("suspended_growth_model", pc.ASM1())
    # ASM1 uses S_O for dissolved oxygen, not S_O2
    if kwargs.get("DO_ID") == "S_O2":
        kwargs["DO_ID"] = "S_O"
    elif "aeration" in kwargs and kwargs["aeration"] and "DO_ID" not in kwargs:
        kwargs["DO_ID"] = "S_O"
```

---

## Workflows Tested

| Workflow | Model | Units | Result |
|----------|-------|-------|--------|
| MLE with recycles | ASM2d | Mixer, CSTR, Splitter, Clarifier | ✅ Fixed (bugs 1-3) |
| Anaerobic CSTR | mADM1 | AnaerobicCSTRmADM1 | ✅ Passed |
| MBR system | ASM2d | CSTR, CompletelyMixedMBR | ✅ Passed |
| Parallel trains | ASM2d | Splitter, CSTR, Mixer | ✅ Passed |
| Single CSTR | ASM1 | CSTR | ✅ Fixed (bug 4) |

---

## MLE Workflow Results (Post-Fix)

**Configuration:** MLE process (4000 m³/d, 400 m³ anoxic, 1200 m³ aerobic, IR=50%, RAS=95%)

### CLI Commands (All Successful)
```bash
flowsheet new --model ASM2d --id mle_test_v3
flowsheet add-stream --session mle_test_v3 --id influent --flow 4000 ...
flowsheet add-unit --session mle_test_v3 --type Mixer --id M1 ...
flowsheet add-unit --session mle_test_v3 --type CSTR --id A1 ...
flowsheet add-unit --session mle_test_v3 --type CSTR --id O1 ...
flowsheet add-unit --session mle_test_v3 --type Splitter --id SP_IR ...
flowsheet add-unit --session mle_test_v3 --type FlatBottomCircularClarifier --id SC ...
flowsheet add-unit --session mle_test_v3 --type Splitter --id SP_RAS ...
flowsheet connect --session mle_test_v3 --connections '[{"from": "SP_IR-1", "to": "1-M1"}, {"from": "SP_RAS-0", "to": "2-M1"}]'
flowsheet build --session mle_test_v3 --recycles '["IR", "RAS"]'
flowsheet simulate --session mle_test_v3 --duration 30
```

### Final Effluent Quality
| Parameter | Value | Removal |
|-----------|-------|---------|
| Flow | 565 m³/d | - |
| COD | 35.7 mg/L | 89.4% |
| TSS | 2.7 mg/L | 98.3% |
| TN | 22.1 mg/L | 38.6% |
| TP | 5.5 mg/L | 39.8% |

---

## Known Limitation

**Mixed-model flowsheets with junctions**: The unit compatibility check doesn't account for model transitions via junction units. Adding an `AnaerobicCSTRmADM1` after an `ASM2dtomADM1` junction in an ASM2d session fails validation. Workaround: use templates for mixed-model systems.

---

## Files Modified

| File | Changes |
|------|---------|
| `utils/flowsheet_builder.py` | `ins.append()` for Mixer, array split handling, improved effluent detection, ASM1 DO_ID mapping |

---

## Verification

### Test Results
```
292 passed, 14 deselected (slow tests)
```

All existing tests continue to pass with the bug fixes.
