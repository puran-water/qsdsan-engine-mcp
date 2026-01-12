# Phase 5: Report Integration & Schema Normalization

## Summary

Address critical schema mismatches between flowsheet simulation outputs and report builder expectations. The current implementation produces reports with missing diagrams, empty time-series plots, and incorrect metadata even when artifacts exist.

## Codex Review Findings

**DeepWiki + GitHub CLI Research:**
- QSDsan's dynamic outputs are centered on `Scope` objects (`scope.record` + `scope.time_series`) with export helpers
- No standard "results dict" schema in core QSDsan - normalization in our engine is the right approach
- No existing result-normalization patterns or "timeseries" schema helpers found in QSD-Group/QSDsan

**Key Codex Recommendations:**
1. Normalize in ONE place only (`_prepare_*_data()`) to avoid double-loading timeseries
2. Expand normalization to cover ALL template-required keys (performance/sulfur/defaults)
3. Guard against `flowsheet = None` crashes
4. Add more edge case tests (idempotency, missing files, relative paths)

## Problem Analysis

### Confirmed Issues

| Issue | Severity | Description |
|-------|----------|-------------|
| **Diagram path mismatch** | HIGH | `flowsheet_builder` stores at `results["diagram_path"]`; report expects `results["flowsheet"]["diagram_path"]` |
| **Timeseries not loaded** | HIGH | Stored as `timeseries_path` (file path); report expects `timeseries` (dict with data) |
| **Solver metadata nested** | MEDIUM | Stored at `metadata.solver.duration_days`; report expects top-level `duration_days` |
| **Effluent format mismatch** | MEDIUM | Stored as `effluent_quality`; report expects separate `influent`/`effluent` dicts |
| **Performance dict missing** | HIGH | Templates expect `performance.cod/nitrogen/phosphorus/srt`; flowsheet results lack this structure |
| **Sulfur dict missing** | MEDIUM | Anaerobic templates expect top-level `sulfur`; lives under `effluent_quality["sulfur"]` |
| **Flowsheet None crash** | HIGH | `flowsheet` can be `None`; `_prepare_*` will crash on `.get()` |
| **Dead placeholder code** | LOW | Unused `saturation_index()` function in `madm1.py` |

### Not Issues (Feedback Clarification)

- **Artifact location contract**: MCP→CLI pathway works correctly (`simulate_built_system` passes `--output-dir jobs/{job_id}`)
- **Thermodynamics placeholders**: The real `calc_saturation_indices()` in `thermodynamics.py` IS used; only `activities` return in `solve_pH_with_biogas()` is placeholder

---

## Implementation Plan

### Task 1: Create Result Schema Normalizer

**File:** `reports/qmd_builder.py`

Add `normalize_results_for_report()` function that bridges result producers and template requirements:

```python
def normalize_results_for_report(
    results: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Normalize simulation results to match template expectations.

    MUST be idempotent - safe to call multiple times on same data.

    Handles:
    1. diagram_path -> flowsheet.diagram_path (verify file exists)
    2. timeseries_path -> timeseries (load JSON, handle relative paths)
    3. metadata.solver.* -> top-level duration_days, method
    4. effluent_quality -> effluent with expected field names
    5. removal_efficiency -> performance (with nested cod/nitrogen/phosphorus/srt)
    6. effluent_quality.sulfur -> sulfur (top-level for anaerobic)
    7. Default values for all template-required fields
    8. Guard flowsheet = None -> {}
    """
```

**Normalization Rules (Expanded per Codex):**

| Source | Target | Default |
|--------|--------|---------|
| `results["diagram_path"]` | `results["flowsheet"]["diagram_path"]` | `None` |
| `results["timeseries_path"]` | `results["timeseries"]` (loaded JSON) | `{}` |
| `results["metadata"]["solver"]["duration_days"]` | `results["duration_days"]` | `0` |
| `results["metadata"]["solver"]["method"]` | `results["method"]` | `"RK23"` |
| `results["effluent_quality"]["nitrogen"]` | `results["effluent"]["NH4_mg_N_L"]`, etc. | `0` |
| `results["effluent_quality"]["phosphorus"]` | `results["effluent"]["PO4_mg_P_L"]` | `0` |
| `results["removal_efficiency"]` | `results["performance"]["cod"]`, etc. | `{"removal_pct": 0}` |
| `results["effluent_quality"]["sulfur"]` | `results["sulfur"]` | `{}` |
| `results["flowsheet"]` = None | `results["flowsheet"]` = `{}` | `{}` |

**Critical Implementation Notes:**
1. **Single entrypoint**: Call ONLY in `_prepare_*_data()`, NOT in `generate_report()` (avoid double-loading)
2. **Verify file existence**: Check `diagram_path` exists before setting `has_diagram = True`
3. **Resolve relative paths**: Use `output_dir` to resolve `timeseries_path` if relative
4. **Guard JSON errors**: Wrap timeseries loading in try/except
5. **Seed nested defaults**: Ensure `performance.cod`, `performance.nitrogen`, etc. exist with defaults

### Task 2: Update Prepare Functions

**File:** `reports/qmd_builder.py`

Modify `_prepare_anaerobic_data()` and `_prepare_aerobic_data()`:

```python
def _prepare_anaerobic_data(result, output_path=None):
    # Normalize results first
    data = normalize_results_for_report(
        result,
        output_dir=output_path.parent if output_path else None
    )
    # ... rest of existing code uses normalized `data`
```

### Task 3: Add Integration Tests (Expanded per Codex)

**File:** `tests/test_phase2.py` (new test class)

**Test Hygiene Requirements (STRICT):**
- NO `MagicMock` or `Mock()` - use real fixtures and data structures
- NO broad `except: pass` - all exceptions must be explicit and logged
- NO `pytest.skip()` for core functionality - only for optional deps (matplotlib)
- NO `filterwarnings('ignore')` - warnings must be visible
- All assertions must be explicit - no truthy/falsy shortcuts

```python
class TestReportSchemaIntegration:
    """Integration tests for report generation pipeline.

    All tests use real data structures (dicts, paths) - no mocking.
    Exceptions are asserted explicitly, not swallowed.
    """

    # Core normalization tests (must never skip)
    def test_flowsheet_results_normalize_diagram_path(self, tmp_path):
        """Diagram at top-level is copied to flowsheet.diagram_path."""
        # Use real file paths, verify actual file existence check

    def test_flowsheet_results_load_timeseries(self, tmp_path):
        """Timeseries loaded from timeseries_path into timeseries dict."""
        # Write actual JSON file, verify loaded content

    def test_solver_metadata_extracted_to_simulation(self):
        """duration_days/method from metadata.solver reach simulation dict."""
        # Use real nested dict, verify exact key paths

    def test_effluent_quality_mapped_to_effluent(self):
        """effluent_quality nested fields flatten to effluent dict."""
        # Explicit assertions for each expected key

    def test_full_report_generation_with_artifacts(self, tmp_path):
        """Complete report generation includes diagram and plots."""
        # Create real files in tmp_path, verify QMD contains references

    # Idempotency and edge case tests (must never skip)
    def test_normalization_is_idempotent(self):
        """Normalized data stays stable if normalized twice."""
        from copy import deepcopy
        # Compare deepcopy before/after second normalization

    def test_missing_timeseries_path_handled(self, tmp_path):
        """Missing/invalid timeseries_path doesn't raise exception."""
        # Assert no exception raised, assert timeseries == {}

    def test_invalid_timeseries_json_handled(self, tmp_path):
        """Invalid JSON in timeseries file doesn't raise exception."""
        # Write invalid JSON, verify graceful fallback to {}

    def test_flowsheet_none_safely_coerced(self):
        """flowsheet=None is coerced to empty dict without crash."""
        # Explicit None in input, verify output["flowsheet"] == {}

    def test_removal_efficiency_maps_to_performance(self):
        """removal_efficiency populates performance.cod/nitrogen/etc."""
        # Verify nested structure exists with defaults

    def test_effluent_quality_flattens_all_species(self):
        """effluent_quality flattens to NH4/NO3/PO4/COD/TSS/VSS."""
        # Explicit assertions for each of 6 keys

    def test_template_render_minimal_flowsheet_result(self, tmp_path):
        """Minimal flowsheet result renders template without crash."""
        # Real Jinja2 render call, no mocking

    def test_relative_timeseries_path_resolved(self, tmp_path):
        """Relative timeseries_path resolved via output_dir."""
        # Use relative path string, verify file found

    def test_sulfur_mapped_for_anaerobic(self):
        """effluent_quality.sulfur maps to top-level sulfur dict."""
        # Verify sulfur key extracted correctly
```

### Task 4: Add Artifact Contract Test

**File:** `tests/test_phase3.py` (extend existing)

**Test Hygiene Requirements (STRICT):**
- Use `tmp_path` and `monkeypatch.chdir()` to isolate from real filesystem
- All file I/O uses real files, not mocks
- Explicit assertions on returned content types

```python
class TestArtifactContract:
    """Verify artifact retrieval matches production locations.

    Uses tmp_path isolation - no writes to real jobs/ directory.
    All assertions are explicit on content and types.
    """

    def test_get_artifact_finds_diagram_in_job_dir(self, tmp_path, monkeypatch):
        """get_artifact returns diagram from jobs/{job_id}/flowsheet.svg.

        Setup:
            1. Create tmp_path/jobs/{job_id}/flowsheet.svg with real SVG content
            2. monkeypatch.chdir(tmp_path)
        Assert:
            - Returns success
            - content contains SVG header
            - content_type == 'image/svg+xml'
        """

    def test_get_artifact_finds_timeseries_in_job_dir(self, tmp_path, monkeypatch):
        """get_artifact returns parsed JSON from jobs/{job_id}/timeseries.json.

        Setup:
            1. Create tmp_path/jobs/{job_id}/timeseries.json with real JSON
            2. monkeypatch.chdir(tmp_path)
        Assert:
            - Returns success
            - content is dict (parsed JSON)
            - Has expected keys (time, streams)
        """

    def test_get_artifact_returns_svg_as_text(self, tmp_path, monkeypatch):
        """SVG content returned as text, not base64.

        Assert:
            - isinstance(content, str)
            - content.startswith('<svg') or '<?xml'
            - NOT base64 encoded
        """

    def test_get_artifact_missing_file_returns_error(self, tmp_path, monkeypatch):
        """Missing artifact returns error, doesn't raise exception.

        Assert:
            - success == False
            - error message mentions "not found"
        """
```

### Task 5: Remove Dead Placeholder Code

**File:** `models/madm1.py`

Remove unused `saturation_index(acts, Ksp)` function (lines 627-658). This is dead code - the real saturation index calculation uses `calc_saturation_indices()` from `thermodynamics.py`.

### Task 6: Document Artifact Location Contract (Expanded per Codex)

**File:** `CLAUDE.md`

Add section clarifying artifact locations and normalization:

```markdown
## Artifact Locations

| Invocation | Output Directory | Artifact Access |
|------------|------------------|-----------------|
| MCP `simulate_built_system` | `jobs/{job_id}/` | `get_artifact(job_id, ...)` |
| CLI `flowsheet simulate` (default) | `output/{session_id}/` | Direct file path |
| CLI `flowsheet simulate --output-dir X` | `X/` | Direct file path |

### Report Artifacts
- `flowsheet.svg`: System diagram (generated by `utils.diagram.save_system_diagram()`)
- `report.qmd`: Quarto Markdown report
- `timeseries.json`: Time-series data with schema `{time: [], streams: {}, time_units: "days"}`

### Result Normalization
Reports use `normalize_results_for_report()` to map flowsheet simulation outputs to template expectations:
- Flowsheet builder stores: `diagram_path`, `timeseries_path`, `metadata.solver.*`, `effluent_quality`
- Templates expect: `flowsheet.diagram_path`, `timeseries`, `duration_days`, `performance.*`, `effluent`
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `reports/qmd_builder.py` | Add `normalize_results_for_report()`, update `_prepare_*_data()` |
| `tests/test_phase2.py` | Add `TestReportSchemaIntegration` class (13 tests) |
| `tests/test_phase3.py` | Add `TestArtifactContract` class (3 tests) |
| `models/madm1.py` | Remove dead `saturation_index()` function (lines 627-658) |
| `CLAUDE.md` | Add artifact location + normalization documentation |

---

## Verification

### Unit Tests
```bash
python -m pytest tests/test_phase2.py::TestReportSchemaIntegration -v
python -m pytest tests/test_phase3.py::TestArtifactContract -v
```

### Integration Test (Manual)
```bash
# Create and simulate a flowsheet
python cli.py flowsheet new --model ASM2d --id test_report
python cli.py flowsheet add-stream --session test_report --id influent \
    --flow 4000 --concentrations '{"S_F": 75, "S_A": 20}'
python cli.py flowsheet add-unit --session test_report --type CSTR --id R1 \
    --params '{"V_max": 1000}' --inputs '["influent"]'
python cli.py flowsheet build --session test_report
python cli.py flowsheet simulate --session test_report --duration 5 --report

# Verify report contains diagram reference
grep -q "flowsheet.svg" output/test_report/report.qmd && echo "PASS: Diagram included"

# Verify duration is not 0
grep -q "duration_days.*[1-9]" output/test_report/report.qmd && echo "PASS: Duration correct"
```

### Full Test Suite
```bash
python -m pytest tests/ -v
# Expected: All 201+ tests pass
```

---

## Test Hygiene Standards (ENFORCED)

All new tests MUST follow these rules:

| Rule | Enforcement |
|------|-------------|
| NO `MagicMock` / `Mock()` | Use real dicts, files, fixtures |
| NO broad `except: pass` | Explicit exception types, log or re-raise |
| NO `pytest.skip()` for core functionality | Only for optional deps like matplotlib |
| NO `filterwarnings('ignore')` | Warnings must be visible |
| NO truthy/falsy assertions | Use `assert x == expected`, not `assert x` |
| Explicit assertions | Test specific values, not just types |
| Filesystem isolation | Use `tmp_path` + `monkeypatch.chdir()` |

**Verification:** After implementation, run:
```bash
# Check for banned patterns
grep -rn "MagicMock\|Mock(" tests/test_phase*.py
grep -rn "except.*:.*pass" tests/test_phase*.py
grep -rn "filterwarnings.*ignore" tests/test_phase*.py
```

---

## Acceptance Criteria

1. Reports generated from flowsheet simulations include diagram references when `flowsheet.svg` exists
2. Reports show correct simulation duration/method from solver metadata
3. Time-series plots generate when `timeseries.json` exists
4. Templates render without crash on minimal flowsheet results (no undefined errors)
5. All 17 new tests pass (14 integration + 4 artifact contract) - NO skips for core functionality
6. No regressions in existing 201 tests
7. Zero banned patterns in new test code (MagicMock, except:pass, filterwarnings)

---

## Estimated Scope

- **New code:** ~200 lines (normalize function + tests)
- **Modified code:** ~30 lines (_prepare_* functions)
- **Deleted code:** ~30 lines (dead saturation_index function)
- **New tests:** 17 (14 integration + 4 artifact contract)
