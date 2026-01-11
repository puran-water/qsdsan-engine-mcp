# Phase 4: Production Readiness Fixes

## Assessment Summary

The feedback is **warranted**. The codebase is a functional prototype with real plumbing but has several issues preventing production readiness.

### Codex Review Findings (Additional Issues)

Codex performed a thorough review using DeepWiki and GitHub CLI inspection of QSD-Group/QSDsan. Key additional findings:

1. **Missing runtime dependencies**: `psutil` (job_manager.py:32), `jinja2` (qmd_builder.py:26), and Graphviz (external) are used but not declared
2. **Report templates not packaged**: `reports/templates/*.qmd` and `report.css` won't be included in wheel builds
3. **JobManager operational issues**: No streaming stdout (progress never updates until completion), `_running_count` doesn't account for recovered jobs after restart
4. **Upstream compatibility confirmed**: BioSTEAM `System.diagram()` supports `kind`, `file`, `format`, `display`, `number`, `label`, `title` - our `save_system_diagram` is compatible
5. **mADM1 is custom**: QSDsan main branch doesn't ship 63-component mADM1 (imports commented) - our implementation is custom and shouldn't assume upstream provides it

---

### CRITICAL (Broken Functionality)
| Issue | Location | Impact |
|-------|----------|--------|
| `generate_report` import bug | cli.py:1031 | `flowsheet simulate --report` crashes with ImportError |
| README CLI examples wrong | README.md:146-150 | Users can't run documented examples |
| Tests hardcode Windows paths | test_phase1.py, test_phase2.py | Tests fail on non-Windows/CI |

### HIGH (Major Gaps)
| Issue | Location | Impact |
|-------|----------|--------|
| Aerobic templates lack diagrams | mle_mbr.py, ao_mbr.py, a2o_mbr.py | Inconsistent with anaerobic template |
| Packaging broken | pyproject.toml:27-28 | cli.py/server.py not in wheel |
| Report templates not packaged | reports/templates/ | Reports fail in wheel installs |
| Version mismatch | pyproject.toml vs templates | 0.1.0 vs 3.0.0 confuses reproducibility |
| No requirements.txt | README:236 | Installation instructions fail |
| Missing runtime deps | pyproject.toml | psutil, jinja2 not declared |
| **Report placeholders** | qmd_builder.py:196-198, 296-299 | Time-series plots show "[placeholder]" |

### MEDIUM (Quality Gaps)
| Issue | Location | Impact |
|-------|----------|--------|
| Shallow MCP tests | test_phase3.py | Only check tool existence |
| No artifact tests | test_phase3.py | Artifact retrieval untested |

---

## Implementation Plan

### Task 1: Fix `generate_report` Import Bug [CRITICAL]

**File:** `cli.py:1031`

**Current (broken):**
```python
from reports.qmd_builder import generate_report
report_path = generate_report(
    session_id=session_id,
    model_type=session.primary_model_type,
    results=sim_results,
    output_dir=output_dir,
)
```

**Fix:** Create `generate_report` wrapper in `reports/qmd_builder.py`:
```python
def generate_report(
    session_id: str,
    model_type: str,
    results: Dict[str, Any],
    output_dir: Path,
) -> Path:
    """Generate report for flowsheet simulation results."""
    output_path = output_dir / "report.qmd"
    # Determine template based on model_type
    if model_type.lower() in ("madm1", "adm1"):
        build_anaerobic_report(results, output_path=output_path)
    else:
        build_aerobic_report(results, output_path=output_path)
    return output_path
```

**Also add to `__all__`.**

---

### Task 2: Fix README CLI Examples [CRITICAL]

**File:** `README.md:146-150`

**Current (broken):**
```bash
python cli.py simulate \
  --template mle_mbr_asm2d \
  --influent '{"flow_m3_d": 4000, ...}' \  # Inline JSON not supported
  --duration 15 \                           # Should be --duration-days
  --report
```

**Fix:** Update to match actual CLI:
```bash
# Create influent file first
echo '{"flow_m3_d": 4000, "concentrations": {"S_F": 75, "S_NH4": 35}}' > influent.json

python cli.py simulate \
  --template mle_mbr_asm2d \
  --influent influent.json \
  --duration-days 15 \
  --report
```

---

### Task 3: Make Tests Portable [CRITICAL]

**Files:** `tests/test_phase1.py`, `tests/test_phase2.py`

**Current (broken):**
```python
result = subprocess.run(
    ['../venv312/Scripts/python.exe', 'cli.py', ...],
    ...
)
```

**Fix:** Use `sys.executable`:
```python
import sys

result = subprocess.run(
    [sys.executable, 'cli.py', ...],
    ...
)
```

Replace all 23+ occurrences across both test files.

---

### Task 4: Add Diagram Generation to Aerobic Templates [HIGH]

**Files:** `templates/aerobic/mle_mbr.py`, `ao_mbr.py`, `a2o_mbr.py`

**Reference:** Copy pattern from `templates/anaerobic/cstr.py:184-223`

**Add before result save section:**
```python
# Generate diagram and mass balance data
try:
    from utils.diagram import (
        save_system_diagram,
        generate_mass_balance_table,
        generate_unit_summary,
    )

    streams_data = generate_mass_balance_table(sys, model_type="ASM2d")
    units_data = generate_unit_summary(sys)

    result["flowsheet"] = {
        "streams": streams_data,
        "units": units_data,
    }

    if output_dir:
        diagram_path = save_system_diagram(
            sys,
            output_path=output_dir / "flowsheet",
            kind="thorough",
            format="svg",
            title=f"MLE-MBR - {flow_m3_d:.0f} m3/d",
        )
        if diagram_path:
            result["flowsheet"]["diagram_path"] = str(diagram_path)
except Exception as e:
    logger.warning(f"Could not generate flowsheet data: {e}")
    result["flowsheet"] = None
```

**Codex validation:** BioSTEAM `System.diagram()` API confirmed compatible with these parameters.

---

### Task 5: Fix Packaging Configuration [HIGH]

**File:** `pyproject.toml`

**Issues:**
1. cli.py/server.py not in wheel
2. Report templates not packaged (Codex finding)
3. Missing runtime dependencies

**Fix:**
```toml
[project]
name = "qsdsan-engine-mcp"
version = "3.0.0"
dependencies = [
    "qsdsan>=1.3.0",
    "biosteam>=2.38",
    "pydantic>=2.0",
    "fastmcp>=0.5",
    "numpy>=1.24",
    "scipy>=1.10",
    "typer>=0.9",
    "rich>=13.0",
    "jinja2>=3.0",
    "psutil>=5.9",
]

[tool.hatch.build.targets.wheel]
include = [
    "core/**",
    "templates/**",
    "utils/**",
    "models/**",
    "reports/**",
    "cli.py",
    "server.py",
]

[tool.hatch.build.targets.wheel.force-include]
"reports/templates" = "reports/templates"
```

**Note:** `typer` and `rich` moved from optional to required (CLI is always used).

---

### Task 6: Fix Version Inconsistency [HIGH]

**Files:**
- `pyproject.toml:3` - Change to `version = "3.0.0"`

All templates already output `"engine_version": "3.0.0"`, so align pyproject.toml.

---

### Task 7: Create requirements.txt [HIGH]

**File:** Create `requirements.txt`

```
qsdsan>=1.3.0
biosteam>=2.38
pydantic>=2.0
fastmcp>=0.5
numpy>=1.24
scipy>=1.10
typer>=0.9
rich>=13.0
jinja2>=3.0
psutil>=5.9
```

**Also add note about external dependencies:**
```
# External dependencies (not pip-installable):
# - Graphviz: Required for diagram generation
#   Install via: apt install graphviz (Linux) or brew install graphviz (macOS)
#   or: https://graphviz.org/download/
# - Quarto CLI: Required for rendering .qmd reports to HTML/PDF
#   Install from: https://quarto.org/docs/get-started/
```

---

### Task 8: Implement Report Time-Series Plots [HIGH - NOT DEFERRED]

**Files:** `reports/qmd_builder.py`, `reports/templates/anaerobic_report.qmd`, `reports/templates/aerobic_report.qmd`

**Current (placeholders):**
```python
'time_series': {
    'convergence_plot': '[Convergence plot placeholder]',
    'state_variables_plot': '[State variables plot placeholder]',
}
```

**Fix:** Generate actual matplotlib plots from timeseries.json

**Implementation:**

1. **Add plot generation utility** (`utils/report_plots.py`):
```python
"""Generate time-series plots for Quarto reports."""
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from pathlib import Path
from typing import Dict, Any, List, Optional

def generate_convergence_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    title: str = "Simulation Convergence",
) -> Path:
    """Generate convergence plot showing key state variables over time."""
    fig, ax = plt.subplots(figsize=(10, 6))

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    # Plot effluent COD/TSS if available
    for stream_id, stream_data in streams.items():
        if "COD_mg_L" in stream_data:
            ax.plot(time, stream_data["COD_mg_L"], label=f"{stream_id} COD")
        if "S_NH4" in stream_data:
            ax.plot(time, stream_data["S_NH4"], label=f"{stream_id} NH4-N")

    ax.set_xlabel("Time (days)")
    ax.set_ylabel("Concentration (mg/L)")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)

    output_path = Path(output_path).with_suffix('.png')
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    return output_path

def generate_nutrient_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    components: List[str] = ["S_NH4", "S_NO3", "S_PO4"],
) -> Path:
    """Generate nutrient time-series plot for aerobic systems."""
    fig, ax = plt.subplots(figsize=(10, 6))

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    for stream_id, stream_data in streams.items():
        for comp in components:
            if comp in stream_data:
                ax.plot(time, stream_data[comp], label=f"{stream_id} {comp}")

    ax.set_xlabel("Time (days)")
    ax.set_ylabel("Concentration (mg/L)")
    ax.set_title("Nutrient Trajectories")
    ax.legend()
    ax.grid(True, alpha=0.3)

    output_path = Path(output_path).with_suffix('.png')
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    return output_path
```

2. **Update qmd_builder.py** to call plot generators:
```python
from utils.report_plots import generate_convergence_plot, generate_nutrient_plot

# In build_anaerobic_report / build_aerobic_report:
if timeseries_data and output_path:
    plot_dir = output_path.parent / "plots"
    plot_dir.mkdir(exist_ok=True)

    convergence_path = generate_convergence_plot(
        timeseries_data,
        plot_dir / "convergence.png"
    )
    data['time_series']['convergence_plot'] = f"![Convergence]({convergence_path.name})"
```

3. **Update QMD templates** to embed images:
```jinja2
<div class="ts-plot">
{{ data.time_series.convergence_plot }}
</div>
```

4. **Add matplotlib to dependencies** (pyproject.toml):
```toml
"matplotlib>=3.7",
```

---

### Task 9: Add Report Generation Test [MEDIUM]

**File:** `tests/test_phase2.py`

**Add test:**
```python
def test_flowsheet_simulate_report_generation(self, tmp_path):
    """flowsheet simulate --report should generate QMD report."""
    # Create session + minimal flowsheet
    # Run simulate with --report
    # Assert report.qmd exists in output
    # Assert plots directory exists with PNG files
```

---

### Task 10: Add MCP Tool Behavior Tests [MEDIUM]

**File:** `tests/test_phase3.py`

**Add tests:**
- `test_simulate_built_system_creates_job()` - Verify job_id returned
- `test_get_artifact_returns_content()` - Verify artifact content
- `test_get_flowsheet_timeseries_returns_data()` - Verify time-series structure

---

### Task 11: Add Mass/Charge Balance Validation [HIGH]

**Files:** `core/converters.py`, `tests/test_converters.py` (NEW)

**Issue (from Codex):** Conversion logic in `core/converters.py` uses heuristic mappings that are NOT equivalent to QSDsan's junction units. This can produce incorrect results that don't conserve mass or charge.

**Fix:** Add validation functions to verify conversion correctness:

1. **Add balance validation utilities** (`core/converters.py`):
```python
def validate_mass_balance(
    input_state: PlantState,
    output_state: PlantState,
    rtol: float = 0.01,
) -> Dict[str, Any]:
    """
    Validate mass balance between input and output states.

    Checks:
    - Total COD balance (should be conserved +/- rtol)
    - Total nitrogen balance (TKN + NO3 + NO2)
    - Total phosphorus balance

    Returns:
        Dict with balance errors and pass/fail status
    """
    # Calculate total COD for input
    input_cod = sum(
        conc * get_cod_factor(comp_id)
        for comp_id, conc in input_state.concentrations.items()
    )
    output_cod = sum(
        conc * get_cod_factor(comp_id)
        for comp_id, conc in output_state.concentrations.items()
    )

    cod_error = abs(output_cod - input_cod) / max(input_cod, 1e-6)

    # Similar for N and P...

    return {
        "cod_balance": {
            "input_mg_L": input_cod,
            "output_mg_L": output_cod,
            "error_pct": cod_error * 100,
            "passed": cod_error <= rtol,
        },
        # ... nitrogen, phosphorus
        "all_passed": cod_error <= rtol and n_error <= rtol and p_error <= rtol,
    }

def validate_charge_balance(
    state: PlantState,
    atol: float = 0.1,  # meq/L tolerance
) -> Dict[str, Any]:
    """
    Validate electroneutrality of state.

    Sum of cation charges should equal sum of anion charges.
    """
    cation_charge = sum(
        conc * get_charge(comp_id)
        for comp_id, conc in state.concentrations.items()
        if get_charge(comp_id) > 0
    )
    anion_charge = sum(
        conc * abs(get_charge(comp_id))
        for comp_id, conc in state.concentrations.items()
        if get_charge(comp_id) < 0
    )

    imbalance = abs(cation_charge - anion_charge)

    return {
        "cation_meq_L": cation_charge,
        "anion_meq_L": anion_charge,
        "imbalance_meq_L": imbalance,
        "passed": imbalance <= atol,
    }
```

2. **Integrate validation into convert_state():**
```python
def convert_state(
    input_state: PlantState,
    target_model: ModelType,
    validate: bool = True,
) -> Tuple[PlantState, Dict[str, Any]]:
    """Convert state with optional mass/charge balance validation."""
    output_state = _do_conversion(input_state, target_model)

    metadata = {"conversion": "asm2d_to_madm1"}

    if validate:
        mass_balance = validate_mass_balance(input_state, output_state)
        charge_balance = validate_charge_balance(output_state)

        metadata["mass_balance"] = mass_balance
        metadata["charge_balance"] = charge_balance

        if not mass_balance["all_passed"]:
            logger.warning(
                f"Mass balance error: COD {mass_balance['cod_balance']['error_pct']:.1f}%"
            )
        if not charge_balance["passed"]:
            logger.warning(
                f"Charge imbalance: {charge_balance['imbalance_meq_L']:.2f} meq/L"
            )

    return output_state, metadata
```

3. **Add validation tests** (`tests/test_converters.py`):
```python
class TestConverterValidation:
    """Test mass and charge balance validation for state conversions."""

    def test_asm2d_to_madm1_conserves_cod(self):
        """ASM2d -> mADM1 conversion should conserve COD within 1%."""
        input_state = PlantState(
            model_type=ModelType.ASM2D,
            concentrations={"S_F": 100, "X_S": 200, "X_H": 1000},
            flow_m3_d=1000,
        )
        output_state, metadata = convert_state(input_state, ModelType.MADM1)

        assert metadata["mass_balance"]["cod_balance"]["passed"], \
            f"COD error: {metadata['mass_balance']['cod_balance']['error_pct']:.1f}%"

    def test_madm1_to_asm2d_conserves_nitrogen(self):
        """mADM1 -> ASM2d should conserve total nitrogen."""
        # ...

    def test_conversion_preserves_charge_neutrality(self):
        """Converted state should remain electroneutral."""
        # ...
```

4. **Document heuristic limitations in docstrings:**
```python
def convert_asm2d_to_madm1(state: PlantState) -> PlantState:
    """
    Convert ASM2d state to mADM1.

    WARNING: This uses heuristic component mappings that approximate
    QSDsan junction unit behavior but may not be stoichiometrically
    exact. For critical applications, validate mass/charge balance
    using validate_mass_balance() and validate_charge_balance().

    Key approximations:
    - X_S split evenly to X_ch, X_pr, X_li
    - X_H mapped to proportional biomass groups
    - S_ALK <-> S_IC (bicarbonate assumption)
    """
```

---

### Task 12: Test Script Hygiene Audit [HIGH]

**Files:** `tests/test_phase1.py`, `tests/test_phase2.py`, `tests/test_phase3.py`

**Issues to fix:**

1. **Remove all MagicMock usage** - Tests should use real objects or proper test fixtures, not mock objects that hide real behavior

2. **Remove silent exception swallowing** - Find and fix patterns like:
   ```python
   # BAD - swallows exceptions
   try:
       do_something()
   except Exception:
       pass  # or just log

   # GOOD - let exceptions propagate or explicitly assert
   do_something()  # Will fail test if exception
   # OR
   with pytest.raises(ExpectedError):
       do_something_that_should_fail()
   ```

3. **Remove "warning passes" anti-pattern** - Tests should FAIL on warnings, not PASS:
   ```python
   # BAD - warning that passes
   if result.get("warning"):
       print(f"Warning: {result['warning']}")  # Test still passes!

   # GOOD - fail on unexpected warnings
   assert "warning" not in result, f"Unexpected warning: {result.get('warning')}"
   # OR if warning is expected:
   assert result.get("warning") == "Expected specific warning text"
   ```

4. **Add strict assertion mode** - At top of test files:
   ```python
   import warnings
   warnings.filterwarnings("error")  # Convert warnings to errors

   # Or use pytest.ini:
   # filterwarnings = error
   ```

**Audit process:**
1. Grep for `MagicMock`, `Mock`, `patch` - remove or replace with real fixtures
2. Grep for `except.*:.*pass` and `except.*:.*continue` - fix each instance
3. Grep for `warning` assertions that don't fail on unexpected warnings
4. Add `filterwarnings = error` to pytest.ini or conftest.py

**Example fixes:**

```python
# BEFORE (bad)
def test_something():
    result = some_function()
    if result.get("diagram_warning"):
        pass  # Silently ignores failures
    assert result["status"] == "completed"

# AFTER (good)
def test_something():
    result = some_function()
    assert "diagram_warning" not in result, f"Diagram failed: {result.get('diagram_warning')}"
    assert result["status"] == "completed"
```

```python
# BEFORE (bad - using MagicMock)
def test_mcp_tool():
    mock_session = MagicMock()
    mock_session.primary_model_type = "ASM2d"
    result = some_tool(mock_session)  # Doesn't test real behavior

# AFTER (good - using real fixture)
@pytest.fixture
def test_session(tmp_path):
    """Create a real session for testing."""
    from utils.flowsheet_session import FlowsheetSessionManager
    manager = FlowsheetSessionManager(sessions_dir=tmp_path)
    session = manager.create_session(model_type="ASM2d")
    return session

def test_mcp_tool(test_session):
    result = some_tool(test_session)  # Tests real behavior
```

---

## File Modification Summary

| File | Changes |
|------|---------|
| `reports/qmd_builder.py` | Add `generate_report()` function + plot integration |
| `utils/report_plots.py` | **NEW** - Time-series plot generation |
| `reports/templates/anaerobic_report.qmd` | Update to embed plot images |
| `reports/templates/aerobic_report.qmd` | Update to embed plot images |
| `README.md` | Fix CLI example (lines 146-162) |
| `tests/test_phase1.py` | Replace hardcoded paths (8 locations), remove mocks/swallowed exceptions |
| `tests/test_phase2.py` | Replace hardcoded paths (15 locations), add report test, hygiene fixes |
| `tests/test_phase3.py` | Add behavior tests for MCP tools, remove mock objects, strict warnings |
| `tests/conftest.py` | **NEW or UPDATE** - Add shared fixtures, strict warning config |
| `tests/test_converters.py` | **NEW** - Mass/charge balance validation tests |
| `core/converters.py` | Add validate_mass_balance(), validate_charge_balance() |
| `pyproject.toml` | Fix version, packaging, dependencies, add `filterwarnings = error` |
| `templates/aerobic/mle_mbr.py` | Add diagram generation (~30 lines) |
| `templates/aerobic/ao_mbr.py` | Add diagram generation (~30 lines) |
| `templates/aerobic/a2o_mbr.py` | Add diagram generation (~30 lines) |
| `requirements.txt` | **NEW** - All runtime deps + external notes |

---

## Verification Plan

After implementation:

1. **Test the fixed `--report` path:**
   ```bash
   python cli.py flowsheet new --model ASM2d --id test
   python cli.py flowsheet add-stream --session test --id inf --flow 1000
   python cli.py flowsheet add-unit --session test --type CSTR --id R1 --inputs '["inf"]'
   python cli.py flowsheet build --session test
   python cli.py flowsheet simulate --session test --duration 1 --report
   # Verify report.qmd exists AND plots/ directory has PNG files
   ```

2. **Test README example:**
   ```bash
   echo '{"flow_m3_d": 4000, "concentrations": {"S_F": 75, "S_NH4": 35}}' > /tmp/influent.json
   python cli.py simulate -t mle_mbr_asm2d -i /tmp/influent.json -d 1
   ```

3. **Run portable tests:**
   ```bash
   python -m pytest tests/test_phase1.py tests/test_phase2.py -v -k "cli"
   ```

4. **Verify aerobic diagrams:**
   ```bash
   python cli.py simulate -t mle_mbr_asm2d -i /tmp/influent.json -d 1 -o /tmp/mle_out
   ls /tmp/mle_out/flowsheet.svg
   ```

5. **Verify report plots:**
   ```bash
   python cli.py simulate -t mle_mbr_asm2d -i /tmp/influent.json -d 1 -o /tmp/mle_out --report
   ls /tmp/mle_out/plots/*.png
   ```

6. **Run full test suite:**
   ```bash
   python -m pytest tests/ -v
   ```

---

## Priority Order

1. Task 1: Fix `generate_report` - **Unblocks `--report` feature**
2. Task 3: Make tests portable - **Enables CI/CD**
3. Task 12: Test hygiene audit - **Remove MagicMock, exception swallowing, warning-passes**
4. Task 2: Fix README - **First-run experience**
5. Task 5: Fix packaging + deps - **Installation experience** (includes jinja2, psutil, matplotlib)
6. Task 7: Create requirements.txt - **Installation experience**
7. Task 6: Fix version - **Reproducibility**
8. Task 4: Add aerobic diagrams - **Feature parity**
9. Task 8: Implement report plots - **Complete report feature**
10. Task 11: Add mass/charge balance validation - **Conversion correctness**
11. Task 9-10: Add tests - **Prevent regressions**

---

## Codex Review Summary

**Validated:**
- Diagram API compatible with upstream BioSTEAM
- Unit class paths in registry exist in current QSDsan
- ASM2d component IDs (18 + H2O = 19) consistent with `create_asm2d_cmps`

**New findings incorporated:**
- Added `psutil`, `jinja2`, `matplotlib` to dependencies
- Added report templates to wheel packaging
- Made report plots a required task (not deferred)
- Added Graphviz/Quarto documentation to requirements.txt
- **Added mass/charge balance validation** (Task 11) to address heuristic converter limitations
- **Added test hygiene audit** (Task 12) - no MagicMock, no silent exceptions, warnings must fail

**Notes:**
- 63-component mADM1 is custom (QSDsan doesn't ship it) - document this in code
- Conversion validation now included with clear warnings when balances exceed tolerances
