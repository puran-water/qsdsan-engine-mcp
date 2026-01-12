# Phase 7: Production Hardening & Documentation

## Python Environment

**IMPORTANT:** Use the Windows venv Python for all testing:
```bash
../venv312/Scripts/python.exe -m pytest tests/ -v
```

## Feedback Assessment Summary (Codex-Verified)

After thorough codebase review, DeepWiki research, and Codex validation:

| Issue | Warranted? | Severity | Notes |
|-------|-----------|----------|-------|
| Anaerobic timestep_hours blocker | **YES** | **Critical** | MCP always passes 1.0, template always raises |
| Unit system inconsistency | **YES** | **High** | Model-specific: ASM2d uses mg/L, mADM1 uses kg/m³ |
| Path traversal gaps | **YES** | **Critical** | Tests alone don't fix traversal; need actual guardrails |
| convert_state is heuristic | **Partial** | Medium | Intentional design, but needs better docs |
| Test coverage gaps | **Partial** | High | Slow tests exist but lack mADM1/MCP E2E coverage |
| Kinetic params stub | **YES** | Low | Documented stub, logs warning |
| pH/alkalinity fallback | **YES** | Low | Silent degradation to fixed values |

### Codex Findings

1. **Task 1 fix is incomplete**: `server.py:93` defaults `timestep_hours=1.0` and `server.py:162` always passes `--timestep-hours`. Must also skip passing the flag when None.

2. **Task 2 misstates unit usage**: mADM1 paths expect kg/m³ (`utils/simulate_madm1.py:71`), ASM2d uses mg/L. Need model-specific documentation, not "mg/L everywhere".

3. **Task 4 claim of zero slow tests is incorrect**: Slow tests exist at `tests/test_phase1.py:289` and `tests/test_phase2.py:621`. Gap is mADM1/MCP E2E coverage.

4. **Path traversal must include actual guardrails**: Tests alone don't prevent traversal. Need `Path.resolve()` + `is_relative_to(base)` checks.

5. **Time estimate is optimistic**: 2.5 hours is unrealistic; expect 6-10 hours for proper implementation.

---

## Task List

### Critical Fixes (Must Do)

#### Task 1: Fix Anaerobic Template MCP Interface
**Files:** `server.py`

**Problem:** `simulate_system` in server.py has `timestep_hours: float = 1.0` default (line 93), and line 162 always passes `--timestep-hours`. The anaerobic template raises `ValueError` if timestep_hours is not None.

**Solution:** Make timestep_hours optional AND conditionally pass to CLI.

```python
# server.py:93 - Change signature:
timestep_hours: Optional[float] = None  # Changed from float = 1.0

# server.py:162 - Conditionally pass flag:
if timestep_hours is not None:
    cmd.extend(["--timestep-hours", str(timestep_hours)])
```

**Keep anaerobic template strict** - don't silently ignore provided values.

**Verification:**
```bash
../venv312/Scripts/python.exe -c "
import asyncio
from server import simulate_system
result = asyncio.run(simulate_system('anaerobic_cstr_madm1', {'flow_m3_d': 100, 'concentrations': {'S_su': 0.5}}))
print('PASS' if 'error' not in result else 'FAIL:', result.get('error', result.get('job_id')))
"
```

---

#### Task 1B: Add Path Traversal Guardrails (CRITICAL)
**Files:** `server.py`, `utils/flowsheet_session.py`

**Problem:** Path traversal risks in `get_artifact` (line 1901) and `get_flowsheet_timeseries` (line 1390), plus session directories. Tests alone don't fix traversal.

**Solution:** Add centralized path validation with `Path.resolve()` + `is_relative_to()`.

```python
# utils/path_utils.py - Add new function:
def validate_safe_path(base_dir: Path, user_path: str, path_type: str = "path") -> Path:
    """Validate path stays within base directory."""
    base = base_dir.resolve()
    full = (base / user_path).resolve()
    if not full.is_relative_to(base):
        raise ValueError(f"Invalid {path_type}: path traversal detected")
    return full

# server.py get_artifact - Add validation:
job_dir = validate_safe_path(Path("jobs"), job_id, "job_id")

# utils/flowsheet_session.py - Add validation:
session_dir = validate_safe_path(self.sessions_dir, session_id, "session_id")
```

**Verification:**
```bash
../venv312/Scripts/python.exe -c "
from utils.path_utils import validate_safe_path
from pathlib import Path
try:
    validate_safe_path(Path('jobs'), '../../../etc/passwd', 'job_id')
    print('FAIL: Should have raised')
except ValueError as e:
    print('PASS:', e)
"
```

---

#### Task 2: Fix PlantState Unit Documentation (Model-Specific)
**Files:** `core/plant_state.py`, `CLAUDE.md`

**Problem:** Units are actually model-specific:
- ASM2d uses mg/L (`tests/test_asm2d_state.json`)
- mADM1 uses kg/m³ (`utils/simulate_madm1.py:71`)

PlantState docstring incorrectly claims kg/m³ for all.

**Solution:** Update docstring to be model-specific. Auto-infer units from model_type (user preference).

```python
@dataclass
class PlantState:
    """
    Plant state representation for wastewater treatment simulation.

    Concentration units are MODEL-SPECIFIC (auto-inferred):
    - ASM2d/ASM1: mg/L (standard practitioner units)
    - mADM1: kg/m³ (QSDsan convention for anaerobic models)

    Flow is in m³/day. Temperature is in Kelvin.
    """
    model_type: ModelType
    concentrations: Dict[str, float]
    flow_m3_d: float
    temperature_K: float

    def get_concentration_units(self) -> str:
        """Return expected units based on model type."""
        if self.model_type.value in ("ASM2d", "ASM1", "mASM2d"):
            return "mg/L"
        elif self.model_type.value == "mADM1":
            return "kg/m3"
        return "mg/L"  # Default
```

**Verification:**
- Review test files match documented conventions
- `../venv312/Scripts/python.exe -m pytest tests/test_phase1.py -v -k "state"`

---

#### Task 3: Add Concentration Bounds Validation (Model-Specific)
**Files:** `core/plant_state.py`, `utils/flowsheet_builder.py`

**Problem:** No runtime detection of likely unit confusion (values 1000x off). Current validation only checks high-end totals.

**Solution:** Add model-specific validation warnings.

**Typical influent ranges (from Codex research):**
- **ASM2d (mg/L):** COD 200-1000, BOD5 100-300, TSS 100-350, TKN 20-85, NH4-N 15-40, TP 4-15, alkalinity 50-300
- **mADM1 (kg/m³):** Digester feeds 1-20 kg/m³ (biomass can be higher)

```python
def validate_concentration_bounds(
    concentrations: Dict[str, float],
    model_type: str,
    units: str
) -> List[str]:
    """Warn if concentrations suggest unit confusion."""
    warnings = []

    if model_type in ("ASM2d", "ASM1") and units == "mg/L":
        for comp, val in concentrations.items():
            if 0 < val < 0.1:
                warnings.append(f"{comp}={val} mg/L suspiciously low (did you mean kg/m³?)")
            if val > 50000:
                warnings.append(f"{comp}={val} mg/L suspiciously high")

    elif model_type == "mADM1" and units == "kg/m3":
        for comp, val in concentrations.items():
            if val > 100:
                warnings.append(f"{comp}={val} kg/m³ suspiciously high (did you mean mg/L?)")

    return warnings
```

**Verification:**
```bash
../venv312/Scripts/python.exe -c "
from core.plant_state import validate_concentration_bounds
# Test ASM2d with kg/m3-scale values (wrong units)
warns = validate_concentration_bounds({'S_F': 0.075}, 'ASM2d', 'mg/L')
print('PASS' if warns else 'FAIL: Should warn about low value')
"
```

---

### High Priority Fixes

#### Task 4: Add Missing Integration Tests (mADM1/MCP E2E)
**Files:** `tests/test_integration.py` (new)

**Problem:** Slow tests exist (test_phase1.py:289, test_phase2.py:621) but lack mADM1 E2E and MCP endpoint coverage.

**Solution:** Add missing integration tests to dedicated module.

```python
import pytest
import sys

@pytest.mark.slow
class TestMissingIntegration:
    def test_madm1_cli_simulate_e2e(self, tmp_path):
        """Run mADM1 CSTR via CLI and verify biogas outputs."""
        import subprocess
        result = subprocess.run([
            sys.executable, 'cli.py', 'simulate',
            '-t', 'anaerobic_cstr_madm1',
            '-i', 'tests/test_madm1_state.json',
            '-d', '1',
            '-o', str(tmp_path)
        ], capture_output=True, text=True, timeout=300)
        assert result.returncode == 0
        assert (tmp_path / 'results.json').exists()

    def test_mcp_simulate_system_aerobic(self):
        """Test MCP simulate_system returns valid job_id."""
        import asyncio
        from server import simulate_system
        result = asyncio.run(simulate_system(
            template='mle_mbr_asm2d',
            influent_state={'flow_m3_d': 1000, 'concentrations': {'S_F': 75}},
            duration_days=0.1,
            timestep_hours=1.0
        ))
        assert 'job_id' in result or 'error' not in result

    def test_flowsheet_build_and_simulate_e2e(self):
        """Build flowsheet via session, compile, and simulate."""
        # Session creation -> add units -> build -> simulate
```

**Verification:**
```bash
../venv312/Scripts/python.exe -m pytest tests/test_integration.py -v -m slow --timeout=600
```

---

#### Task 5: Add Path Traversal Security Tests
**Files:** `tests/test_security.py` (new)

**Problem:** No explicit tests for path traversal attacks on job_id/session_id.

**Solution:** Add security tests that verify crafted IDs are rejected or handled safely.

```python
class TestPathTraversalSecurity:
    def test_get_artifact_rejects_traversal_job_id(self):
        """Verify ../../../etc/passwd style attacks fail safely."""

    def test_session_id_with_path_separators_handled(self):
        """Verify session_id with / or \\ is rejected."""

    def test_artifact_filename_traversal_blocked(self):
        """Verify artifact_type cannot traverse directories."""
```

**Verification:**
- Tests should pass without exposing files outside job directories

---

#### Task 6: Document Heuristic vs Junction Conversion
**Files:** `CLAUDE.md`, `core/converters.py`

**Problem:** Feedback correctly notes convert_state is heuristic, not junction-based.

**Solution:** Add clear documentation distinguishing the two approaches.

Add to CLAUDE.md:
```markdown
## State Conversion: Two Approaches

| Use Case | Method | Function/Class | Accuracy |
|----------|--------|----------------|----------|
| Standalone state conversion (CLI/MCP) | Heuristic mapping | `convert_state()` | ~95% COD balance |
| System simulation with junctions | Biochemical stoichiometry | `ASM2dtomADM1_custom` | Exact per QSDsan |

**When to use which:**
- `convert_state()`: Quick CLI conversions, state file transformations, no reactor context
- Junction units: Full flowsheet simulations requiring biochemical accuracy
```

**Verification:**
- Documentation review

---

### Medium Priority Fixes

#### Task 7: Add Input Validation for Job/Session IDs
**Files:** `server.py`, `utils/flowsheet_session.py`

**Problem:** No validation that job_id/session_id contain only safe characters.

**Solution:** Add ID validation regex.

```python
import re

ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

def validate_id(id_value: str, id_type: str = "ID") -> None:
    """Validate ID contains only safe characters."""
    if not ID_PATTERN.match(id_value):
        raise ValueError(f"Invalid {id_type}: must contain only alphanumeric, underscore, hyphen")
    if len(id_value) > 64:
        raise ValueError(f"Invalid {id_type}: max 64 characters")
```

**Verification:**
- Attempting `job_id="../../../etc/passwd"` should raise ValueError

---

#### Task 8: Make pH/Alkalinity Fallback Explicit
**Files:** `utils/simulate_madm1.py`

**Problem:** Silent fallback to pH=7.0 if external module not found.

**Solution:** Log at WARNING level with explicit message about degraded functionality.

```python
except ImportError:
    logging.warning(
        "pH/alkalinity calculation module not available. "
        "Using fixed defaults (pH=7.0, SAlk=2.5). "
        "For accurate pH prediction, install calculate_ph_and_alkalinity_fixed module."
    )
```

**Verification:**
- Run simulation without external module, verify warning is logged

---

#### Task 9: Document Kinetic Parameters Stub
**Files:** `CLAUDE.md`, `server.py` (tool docstring)

**Problem:** kinetic_params accepted but ignored for mADM1.

**Solution:** Document limitation in CLAUDE.md and tool docstring.

Add to CLAUDE.md Known Limitations:
```markdown
4. **Kinetic Parameter Overrides:** Currently ignored for mADM1 template.
   ASM2d kinetic params are supported. mADM1 uses QSDsan defaults.
```

**Verification:**
- Documentation review

---

### Low Priority / Future

#### Task 10: Add Report Rendering E2E Test
**Files:** `tests/test_integration.py`

**Problem:** No test verifies full report generation (diagram + QMD + optional PDF).

**Solution:** Add test that generates report and verifies artifacts exist.

```python
@pytest.mark.slow
def test_report_generation_e2e(self, tmp_path):
    """Verify report generation creates all expected artifacts."""
    # Run simulation with --report
    # Assert flowsheet.svg exists
    # Assert report.qmd exists
    # Assert timeseries.json exists
```

---

## Implementation Order

### Critical (Must complete first)
1. **Task 1** - Anaerobic MCP fix (15 min)
2. **Task 1B** - Path traversal guardrails (30 min)

### High Priority
3. **Task 2** - PlantState unit documentation (20 min)
4. **Task 3** - Concentration bounds validation (30 min)
5. **Task 7** - ID validation regex + resolve (20 min)
6. **Task 5** - Security tests (30 min)

### Medium Priority
7. **Task 4** - Integration tests (60 min)
8. **Task 6** - Conversion documentation (20 min)
9. **Task 8** - pH fallback logging (10 min)
10. **Task 9** - Kinetic params docs (10 min)

### Low Priority
11. **Task 10** - Report E2E test (20 min, optional if Quarto unavailable)

**Estimated Total:** 6-8 hours (Codex-verified realistic estimate)

---

## Verification Plan

After implementation, run all tests with the correct Python environment:

```bash
# Run all existing tests (should pass)
../venv312/Scripts/python.exe -m pytest tests/ -v

# Run new integration tests (slow)
../venv312/Scripts/python.exe -m pytest tests/test_integration.py -v -m slow --timeout=600

# Run security tests
../venv312/Scripts/python.exe -m pytest tests/test_security.py -v

# Manual verification of Task 1 (anaerobic MCP fix)
../venv312/Scripts/python.exe -c "
import asyncio
from server import simulate_system
result = asyncio.run(simulate_system(
    template='anaerobic_cstr_madm1',
    influent_state={'flow_m3_d': 100, 'concentrations': {'S_su': 0.5}}
))
print('Task 1 - Anaerobic MCP:', 'PASS' if 'job_id' in result else 'FAIL:', result)
"

# Manual verification of Task 1B (path traversal)
../venv312/Scripts/python.exe -c "
from utils.path_utils import validate_safe_path
from pathlib import Path
try:
    validate_safe_path(Path('jobs'), '../../../etc/passwd', 'job_id')
    print('Task 1B - Path traversal: FAIL (should have raised)')
except ValueError as e:
    print('Task 1B - Path traversal: PASS')
"
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `server.py` | Task 1 (timestep_hours:93,162), Task 1B (get_artifact:1901, get_flowsheet_timeseries:1390) |
| `utils/path_utils.py` | Task 1B (add validate_safe_path function) |
| `utils/flowsheet_session.py` | Task 1B (session_id validation:129) |
| `core/plant_state.py` | Task 2 (docstring), Task 3 (bounds validation) |
| `utils/flowsheet_builder.py` | Task 3 (call validation) |
| `utils/simulate_madm1.py` | Task 8 (logging) |
| `core/converters.py` | Task 6 (docstring) |
| `CLAUDE.md` | Tasks 2, 6, 9 (documentation) |
| `tests/test_integration.py` | Tasks 4, 10 (new file) |
| `tests/test_security.py` | Task 5 (new file) |

---

## Not Addressed (Deferred)

1. **Worker pool for warm QSDsan imports** - Performance optimization, not correctness
2. **Parameter metadata exposure** - Nice-to-have for LLM quality
3. **Batch simulation / sensitivity tools** - Future features
4. **TEA/CAPEX primitives** - Future features
