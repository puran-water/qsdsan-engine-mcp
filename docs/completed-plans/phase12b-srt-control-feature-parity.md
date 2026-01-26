# Plan: Extend SRT Control to All Flowsheets with HRT/SRT Decoupling

## Problem Statement

Phase 12 implemented SRT control for `mle_mbr.py`, but:
1. Other aerobic templates (`ao_mbr.py`, `a2o_mbr.py`) are missing key features
2. mADM1 biomass IDs are incomplete (missing X_PAO and SRB biomasses)
3. Need to ensure dynamic flowsheets work correctly with supported units

**Goal:** Extend SRT-controlled steady-state simulation to ALL flowsheets using units with known actuators.

---

## How SRT Control Works in Aerobic MBR Templates

The aerobic MBR templates (`mle_mbr.py`, `ao_mbr.py`, `a2o_mbr.py`) use a **Q_was actuator on the MBR retentate** for SRT control:

```
Influent -> Anoxic -> Aerobic -> MBR -> Permeate (effluent)
               ^                  |
               |             Retentate
               |                  |
               |             Splitter -> WAS (wastage) [Q_was actuator]
               |                  |
               +------<-- RAS <---+
```

**Key mechanism:**
- `CompletelyMixedMBR.pumped_flow` controls total retentate flow (Q_ras + Q_was)
- The Splitter divides retentate into RAS (return) and WAS (waste)
- Adjusting `pumped_flow` while holding RAS constant changes Q_was
- SRT = Total Biomass Inventory / Q_was × Biomass Concentration

This architecture enables:
- Proper biomass inventory tracking (reactors accumulate biomass)
- Explicit Q_was actuator for brentq root-finding
- Unified SRT calculation using model-specific biomass IDs

---

## Codex Review Findings (Incorporated)

### Supported Units (Have Actuators)

| Unit | Actuator | Outlets | How Q_was is Controlled |
|------|----------|---------|------------------------|
| `CompletelyMixedMBR` | `pumped_flow` | 2 (permeate, retentate) | Retentate split via downstream Splitter |
| `FlatBottomCircularClarifier` | `wastage` | 3 (effluent, RAS, WAS) | Direct `wastage` property sets Q_was |

### Unsupported Units (No Actuators - Excluded from Detection)

| Unit | Issue | Status |
|------|-------|--------|
| `AnMBR` | Yield-based (not dynamic), uses `solids_conc`/`split`, no biomass inventory | Out of scope |
| `IdealClarifier` | Uses `sludge_flow_rate`, 2 outlets | Out of scope |
| `PrimaryClarifier` | Uses `sludge_flow_rate`, 2 outlets | Out of scope |
| `Sedimentation` | No Q_was actuator | Out of scope |
| `Thickener`/`Centrifuge` | Sludge treatment, not HRT/SRT decoupling | Out of scope |
| `DAF` | Does not exist in QSDsan | N/A |

### Why AnMBR Cannot Be Used for mADM1 + SRT Control

QSDsan's `AnMBR` sanunit is **yield-based**, not mechanistic:
- Uses `Y_biogas`, `Y_biomass`, `biodegradability` parameters
- Does NOT accept `suspended_growth_model` or mADM1/ADM1 `model`
- No retained biomass inventory → SRT formula undefined
- No explicit Q_was actuator (`solids_conc` adjusts water partitioning, not sludge flow)

**For anaerobic MBR with mADM1 dynamics**, the recommended approach is to build a flowsheet template using the same architecture as aerobic MBR:

```
Influent -> AnaerobicCSTRmADM1 -> MembraneSeparator -> Permeate
                ^                        |
                |                   Retentate
                |                        |
                |                   Splitter -> WAS [Q_was actuator]
                |                        |
                +--------<-- RAS <-------+
```

This is **future work** and not in scope for Phase 12B.

### mADM1 Biomass IDs (Corrected)

This repo's mADM1 extends standard ADM1 with additional biomass:

```python
BIOMASS_IDS = {
    'mADM1': [
        # Standard ADM1 biomass
        'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',
        # ADM1p extension
        'X_PAO',
        # SRB biomass (this repo's extension)
        'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB',
    ],
}
```

---

## Current State Analysis

### Aerobic Templates Feature Parity

| Feature | mle_mbr.py | ao_mbr.py | a2o_mbr.py |
|---------|-----------|-----------|-----------|
| SRT control parameters | ✓ | ✓ | ✓ |
| run_to_target_srt() integration | ✓ | ✓ | ✓ |
| Biomass inoculation | ✓ | ✗ Missing | ✗ Missing |
| Equilibration time estimate | ✓ | ✗ Missing | ✗ Missing |
| Post-sim SRT calculation | ✓ | ✗ Missing | ✗ Missing |
| Result structure (nested) | `simulation.srt_control` | `srt_control` (wrong) | `srt_control` (wrong) |
| SRT_days in reactor output | ✓ | ✗ Missing | ✗ Missing |

---

## Implementation Plan

### Task 1: Update mADM1 Biomass IDs (`utils/srt_control.py`)

**File:** `utils/srt_control.py`

Update BIOMASS_IDS to include all mADM1 biomass components:

```python
BIOMASS_IDS = {
    'ASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'ASM1': ['X_B_H', 'X_B_A'],
    'mASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'mADM1': [
        # Standard ADM1 biomass
        'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',
        # ADM1p extension
        'X_PAO',
        # SRB biomass (this repo's extension)
        'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB',
    ],
    'ADM1': ['X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2'],
}
```

### Task 2: Simplify SRT Detection (`utils/srt_control.py`)

**File:** `utils/srt_control.py`

Limit `has_srt_decoupling()` to units with known actuators ONLY:

```python
# Units with known SRT control actuators
SRT_ACTUATOR_UNITS = {
    'CompletelyMixedMBR',      # pumped_flow actuator
    'FlatBottomCircularClarifier',  # wastage actuator
}

def has_srt_decoupling(system: Any) -> bool:
    """
    Check if system has HRT/SRT decoupling with a controllable actuator.

    Only returns True for units with known Q_was actuators:
    - CompletelyMixedMBR: pumped_flow property
    - FlatBottomCircularClarifier: wastage property

    Other separation units (AnMBR, IdealClarifier, Sedimentation, etc.)
    are excluded because they lack controllable waste flow actuators.
    """
    for unit in getattr(system, 'units', []):
        unit_type = type(unit).__name__
        if unit_type in SRT_ACTUATOR_UNITS:
            return True
    return False
```

### Task 3: Update ao_mbr.py to Feature Parity

**File:** `templates/aerobic/ao_mbr.py`

1. Add imports for `generate_aerobic_inoculum`, `estimate_equilibration_time`
2. Add reactor inoculation after unit creation
3. Add equilibration time estimation
4. Add post-simulation SRT calculation when `target_srt_days` is None
5. Fix result structure: `result["simulation"]["srt_control"]`
6. Add `SRT_days` to reactor output

### Task 4: Update a2o_mbr.py to Feature Parity

**File:** `templates/aerobic/a2o_mbr.py`

Same changes as ao_mbr.py. Note: Keep default inoculum parameters (don't hardcode X_PAO fraction).

### Task 5: Add Tests for Updated Detection

**File:** `tests/test_srt_control.py`

Add tests verifying:
- `CompletelyMixedMBR` detected
- `FlatBottomCircularClarifier` detected
- `AnMBR` NOT detected (no actuator)
- `IdealClarifier` NOT detected (no actuator)
- `Sedimentation` NOT detected (no actuator)
- mADM1 biomass IDs include X_PAO and SRB biomasses

### Task 6: Update Documentation

**File:** `CLAUDE.md`

Document:
- Supported units for SRT control (with actuators)
- Unsupported units (and why)
- Corrected mADM1 biomass IDs
- How aerobic MBR templates use Q_was actuator on retentate

---

## Files to Modify

| File | Changes |
|------|---------|
| `utils/srt_control.py` | Update BIOMASS_IDS, simplify has_srt_decoupling() |
| `templates/aerobic/ao_mbr.py` | Add inoculation, equilibration, SRT calc, fix result structure |
| `templates/aerobic/a2o_mbr.py` | Same as ao_mbr.py |
| `tests/test_srt_control.py` | Add tests for detection and biomass IDs |
| `CLAUDE.md` | Document supported units and biomass IDs |

---

## What's NOT in Scope

1. **AnMBR support** - Yield-based unit, no biomass inventory, no Q_was actuator
2. **Anaerobic MBR template** - Would require new template with AnaerobicCSTRmADM1 + MembraneSeparator + Splitter (future work)
3. **IdealClarifier/PrimaryClarifier support** - Would require `sludge_flow_rate` actuator
4. **Sedimentation/Thickener/Centrifuge** - Not HRT/SRT decoupling for bio reactors
5. **DAF** - Does not exist in QSDsan

---

## Verification Steps

1. Run fast unit tests:
   ```bash
   python -m pytest tests/test_srt_control.py -v -m "not slow"
   ```

2. Verify detection excludes unsupported units:
   ```python
   from utils.srt_control import has_srt_decoupling
   # Should return True only for CompletelyMixedMBR/FlatBottomCircularClarifier
   ```

3. Full test suite:
   ```bash
   python -m pytest tests/ -v -m "not slow"
   ```
