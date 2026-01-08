# MLE Workflow Bug Fixes (Expanded per Codex Review)

## Summary

During MLE workflow testing, 4 issues were found. Codex review expanded the scope:

| Bug | Type | Scope | Action |
|-----|------|-------|--------|
| #1 Unicode `→` | **CODE BUG** | 25+ locations | Replace with `->` |
| #2 Pipe notation | **DOC + TEST BUG** | 10+ locations | Fix examples |
| #3/#4 Recycle ports | **INTENTIONAL** | N/A | Document only |

---

## Fix 1: Unicode Arrow Replacement (EXPANDED)

**Problem:** Unicode arrows crash on Windows cp1252 console encoding.

### Original scope (8 locations):
- cli.py: 6 locations
- server.py: 2 locations

### Additional locations found by Codex (17+ more):

**core/template_registry.py** (template descriptions - user visible):
- Line 60: `"MLE-MBR (anoxic → aerobic → MBR) with ASM2d"`
- Line 70: `"A2O-MBR (anaerobic → anoxic → aerobic → MBR) with EBPR"`

**core/converters.py** (conversion metadata - returned to caller):
- Line 353: `"conversion": "ASM2d → mADM1"`
- Line 374: log message with arrow
- Line 500: `"conversion": "mADM1 → ASM2d"`
- Line 519: log message with arrow
- Line 574: error message with arrow

**utils/flowsheet_session.py**:
- Line 403: `f"Added connection {config.from_port} → {config.to_port}"`

**utils/flowsheet_builder.py**:
- Line 142: `logger.debug(f"Wired connection {conn.from_port} → {conn.to_port}")`
- Line 144: warning message with arrow

**utils/simulate_madm1.py**:
- Line 745: `logger.info(f"... kg/m³ → ... mg/L")`
- Line 829: warning with arrow
- Line 848: warning with arrow

**utils/inoculum_generator.py**:
- Line 246: `logger.info(f"  S_IC ...: {orig} → {target} kg/m³")`
- Line 247: similar
- Line 250: similar

**core/junction_components.py** (uses ↔):
- Line 186: `logger.debug(f"Aligned {asm_id} ↔ {adm_id}")`
- Line 188: `logger.warning(f"Failed to align {asm_id} ↔ {adm_id}: {e}")`

### Additional Unicode (H₂S subscripts):
**utils/analysis/anaerobic.py** - uses `H₂S`, `HS⁻` subscripts:
- Lines 478, 498, 499, 500, 501

**Fix approach:**
1. Replace `→` with `->` globally in Python files
2. Replace `↔` with `<->` in junction_components.py
3. Replace `H₂S` with `H2S` and `HS⁻` with `HS-` in analysis output

---

## Fix 2: Pipe Notation Examples (EXPANDED)

**Problem:** `"to": "A1-1"` is output notation, but `to` field requires input notation.

### Original scope (server.py):
- Line 700: docstring
- Line 709: docstring example

### Additional locations found by Codex:

**tests/test_phase2.py** (semantic errors in tests):
- Line 302: test uses wrong notation
- Line 384: test uses wrong notation
- Line 415: test uses wrong notation
- Line 609: test uses wrong notation

**utils/flowsheet_session.py**:
- Line 92: docstring example

**docs/completed-plans/** (historical - lower priority):
- bright-snacking-prism.md:214
- idempotent-napping-hoare.md:252

**Fix:** Change `A1-1` to `1-A1` in all active code/tests.

---

## Fix 3: Test Assertions

Tests assert arrow strings that will change:

**tests/test_phase2.py**:
- Line 970: `assert "ASM2d → mADM1" in meta["conversion"]`
- Line 999: `assert "mADM1 → ASM2d" in meta["conversion"]`

**Fix:** Update to `"ASM2d -> mADM1"` after arrow replacement.

---

## Not Bugs: Recycle Port Handling

Codex confirmed: Bugs #3/#4 are **intentional QSDsan design**.
- Fixed-size port arrays
- Deferred connections wire, not create

**Correct pattern documented in CLAUDE.md.**

---

## Implementation Steps

### Phase 1: Unicode arrows (all Python files)
```bash
# Replace → with -> globally
# Replace ↔ with <->
# Replace H₂S with H2S, HS⁻ with HS-
```

Files to modify:
- cli.py
- server.py
- core/template_registry.py
- core/converters.py
- core/junction_components.py
- utils/flowsheet_session.py
- utils/flowsheet_builder.py
- utils/simulate_madm1.py
- utils/inoculum_generator.py
- utils/analysis/anaerobic.py

### Phase 2: Pipe notation fixes
Files to modify:
- server.py (docstring lines 700, 709)
- tests/test_phase2.py (lines 302, 384, 415, 609)
- utils/flowsheet_session.py (line 92)

### Phase 3: Test assertion updates
- tests/test_phase2.py (lines 970, 999)

---

## Verification

### Run tests
```bash
../venv312/Scripts/python.exe -m pytest tests/test_phase1.py tests/test_phase2.py -v
```

### Grep for remaining Unicode
```bash
rg "→|↔|H₂S|HS⁻" --type py
```

### Manual CLI test (Windows)
```bash
python cli.py templates --json-out
python cli.py flowsheet new --model ASM2d
```

Should not crash with encoding errors.
