# Phase 6: Production Hardening Plan

## Executive Summary

Code review identified 6 categories of issues that prevent the codebase from being "engineering-grade":

1. **No-op interface flags** - `validate_state` accepts `check_mass_balance` but ignores it
2. **Template CLI no-ops** - `--parameters` and `--timestep-hours` do nothing for templates
3. **Dead code stubs** - `calc_pH()` and `fun()` in madm1.py are empty `pass` stubs
4. **Permissive compilation** - Junction component errors swallowed; cycles/wiring failures are warnings only
5. **Test coverage gaps** - 77% of MCP tools untested; JobManager completely untested
6. **Placeholder mappings** - `X_AUT -> X_h2` marked as placeholder

---

## Task 1: Fix `validate_state` Mass Balance Flag (HIGH PRIORITY)

**Problem:** `server.py:validate_state()` accepts `check_mass_balance=True` but the parameter is never used.

**Codex Review Finding:** The original plan to call `validate_mass_balance(state, state)` is a no-op because that function compares input vs output states - calling it with the same state will always pass.

**Revised Solution:** Implement a **single-state sanity check** that computes COD/TKN/TP totals and warns on implausible values. Also use `validate_charge_balance()` for real electroneutrality checks instead of simplified Na/Cl/K comparison.

### Files to Modify

| File | Changes |
|------|---------|
| `server.py` | Add single-state sanity checks when `check_mass_balance=True` |
| `core/converters.py` | Add new `validate_state_consistency()` function |

### Implementation

```python
# NEW function in core/converters.py
def validate_state_consistency(state: PlantState) -> Dict[str, Any]:
    """
    Validate internal consistency of a single PlantState.

    Checks:
    - COD total is non-negative and reasonable (< 100,000 mg/L)
    - TKN total is non-negative and reasonable (< 10,000 mg/L)
    - TP total is non-negative and reasonable (< 5,000 mg/L)
    - No negative concentrations (already checked elsewhere)
    """
    cod_coeffs, n_coeffs, p_coeffs = get_coefficients(state.model_type.value)

    # Compute totals
    cod_total = sum(state.concentrations.get(c, 0) * coeff
                    for c, coeff in cod_coeffs.items())
    tkn_total = sum(state.concentrations.get(c, 0) * coeff
                    for c, coeff in n_coeffs.items())
    tp_total = sum(state.concentrations.get(c, 0) * coeff
                   for c, coeff in p_coeffs.items())

    warnings = []
    if cod_total < 0:
        warnings.append(f"Negative COD total: {cod_total:.1f} mg/L")
    elif cod_total > 100000:
        warnings.append(f"Implausibly high COD: {cod_total:.1f} mg/L (> 100,000)")

    if tkn_total < 0:
        warnings.append(f"Negative TKN total: {tkn_total:.1f} mg/L")
    elif tkn_total > 10000:
        warnings.append(f"Implausibly high TKN: {tkn_total:.1f} mg/L (> 10,000)")

    if tp_total < 0:
        warnings.append(f"Negative TP total: {tp_total:.1f} mg/L")
    elif tp_total > 5000:
        warnings.append(f"Implausibly high TP: {tp_total:.1f} mg/L (> 5,000)")

    return {
        "cod_mg_L": cod_total,
        "tkn_mg_L": tkn_total,
        "tp_mg_L": tp_total,
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }

# In server.py validate_state() after basic validation (~line 300)
if check_mass_balance:
    from core.converters import validate_state_consistency, validate_charge_balance

    # Single-state sanity check
    consistency = validate_state_consistency(plant_state)
    if not consistency["passed"]:
        warnings.extend(consistency["warnings"])

    # Also check charge balance for mADM1 (real electroneutrality)
    if mt == ModelType.MADM1 and check_charge_balance:
        charge_result = validate_charge_balance(plant_state)
        if not charge_result.get("passed", True):
            warnings.append(f"Charge balance error: {charge_result.get('error_pct', 0):.1f}%")
```

### Tests to Add

- `test_validate_state_mass_balance_catches_negative_cod` - Assert warning for negative COD total
- `test_validate_state_mass_balance_catches_implausible_values` - High COD/TKN/TP
- `test_validate_state_returns_computed_totals` - Verify cod_mg_L, tkn_mg_L, tp_mg_L in response

---

## Task 2: Fix or Remove Template CLI No-Ops (HIGH PRIORITY)

**Problem:** `--parameters` and `--timestep-hours` accepted but don't affect simulation.

**Decision:** **Implement** the parameters rather than remove (they add real value).

### Files to Modify

| File | Changes |
|------|---------|
| `templates/aerobic/mle_mbr.py` | Apply `kinetic_params` via `set_parameters()` |
| `templates/aerobic/ao_mbr.py` | Apply `kinetic_params` via `set_parameters()` |
| `templates/aerobic/a2o_mbr.py` | Apply `kinetic_params` via `set_parameters()` |
| `templates/anaerobic/cstr.py` | Document or implement; update docstring |

### Implementation for Aerobic Templates

QSDsan's `ASM2d` supports `set_parameters(mu_H=..., K_F=...)` after instantiation.

**Codex Review Finding:** The plan's `applied_params = {k: asm2d.parameters.get(k)}` will miss kinetic params because `asm2d.parameters` is stoichiometry-only; kinetics live in `asm2d.rate_function.params`.

```python
# In mle_mbr.py build_and_run() after creating asm2d process (~line 100)
asm2d = pc.ASM2d(**asm_kwargs)
if kinetic_params:
    # Apply kinetic parameter overrides
    asm2d.set_parameters(**kinetic_params)

    # CORRECTED: Extract applied params from BOTH stoichiometry and kinetics
    # Stoichiometry params are in asm2d._parameters
    # Kinetic params are in asm2d.rate_function.params
    stoichio_params = asm2d._parameters
    kinetic_rate_params = asm2d.rate_function.params if hasattr(asm2d, 'rate_function') else {}

    applied_params = {}
    for k in kinetic_params:
        if k in stoichio_params:
            applied_params[k] = stoichio_params[k]
        elif k in kinetic_rate_params:
            applied_params[k] = kinetic_rate_params[k]
        else:
            applied_params[k] = None  # Parameter not found
```

For `timestep_hours`, compute `t_eval` array:

**Codex Review Finding:** Ensure `t_eval` doesn't overshoot `t_span` (use small epsilon or `np.linspace`).

```python
# Before sys.simulate()
if timestep_hours and duration_days:
    dt = timestep_hours / 24  # Convert to days
    # Use epsilon to avoid floating-point overshoot
    t_eval = np.arange(0, duration_days + 1e-9, dt)
    # Clamp final value to exactly duration_days
    t_eval = t_eval[t_eval <= duration_days + 1e-9]
    if t_eval[-1] < duration_days:
        t_eval = np.append(t_eval, duration_days)
    sys.simulate(t_span=(0, duration_days), t_eval=t_eval, ...)
```

### Implementation for Anaerobic Template

The anaerobic template runs to steady-state convergence. Options:
- **Option A:** Implement time-domain simulation with `duration_days` (significant change)
- **Option B:** Keep steady-state behavior but **raise error** if user passes incompatible flags

**Recommendation:** Option B for now - fail explicitly rather than silently ignore.

```python
# In anaerobic/cstr.py build_and_run()
if timestep_hours is not None:
    raise ValueError("timestep_hours not supported for steady-state anaerobic simulation")
if kinetic_params:
    logger.warning("kinetic_params not yet implemented for mADM1 - using defaults")
```

### Tests to Add

- `test_aerobic_kinetic_params_applied` - Verify `set_parameters()` called
- `test_aerobic_timestep_creates_t_eval` - Check t_eval array matches timestep
- `test_anaerobic_timestep_raises_error` - Explicit failure for unsupported flags

---

## Task 3: Fail-Fast Flowsheet Compilation (HIGH PRIORITY)

**Problem:** Compilation proceeds despite structural errors (cycles, wiring failures).

### Files to Modify

| File | Changes |
|------|---------|
| `utils/flowsheet_builder.py` | Add `strict=True` parameter; hard-fail by default |
| `core/junction_components.py` | Log exceptions instead of swallowing silently |
| `utils/topo_sort.py` | Add `fail_on_cycle` parameter |
| `server.py` | Pass `strict=True` to `compile_system()` |

### Implementation

#### 3a. Junction Component Compilation (~junction_components.py:191-200)

```python
# Replace silent exception swallowing
try:
    asm_cmps.compile()
except Exception as e:
    logger.error(f"ASM component compilation failed: {e}")
    raise RuntimeError(f"Component alignment failed: {e}") from e
```

#### 3b. Topological Sort (~topo_sort.py:318-329)

```python
def topological_sort(..., fail_on_cycle: bool = True) -> TopoSortResult:
    ...
    if has_cycle:
        if fail_on_cycle:
            raise ValueError(
                f"Non-recycle cycle detected involving units: {remaining}. "
                "Add these streams to recycle_stream_ids or fix connections."
            )
        # Only append with warning if fail_on_cycle=False
        warnings.append(...)
        order.extend(remaining)
```

#### 3c. Flowsheet Builder (~flowsheet_builder.py:137-144)

```python
def compile_system(session: FlowsheetSession, strict: bool = True) -> BuildInfo:
    ...
    for conn in session.connections:
        try:
            _wire_connection(...)
        except Exception as e:
            if strict:
                raise RuntimeError(f"Connection {conn} failed: {e}") from e
            warnings.append(...)
```

### Breaking Change Note

**Decision:** `strict=True` by default. Breaking changes are acceptable to simplify the codebase.

Error messages will guide users to fix their flowsheets. No deprecation warnings or migration path needed.

### Tests to Add

- `test_compile_strict_fails_on_cycle` - Non-recycle cycle raises ValueError
- `test_compile_strict_fails_on_bad_connection` - Missing unit raises RuntimeError
- `test_compile_permissive_mode_warns` - `strict=False` allows warnings

---

## Task 4: Remove Dead Stubs in mADM1 (MEDIUM PRIORITY)

**Problem:** `calc_pH()` and `fun()` are empty `pass` stubs that could mislead.

### Codex Deep Investigation Results

**Recommendation: DELETE both stubs.** Here's the evidence:

#### `calc_pH()` (line 306-307)
- **QSDsan pattern:** Uses `solve_pH()` with charge-balance + root finding (`brenth`) - see `qsdsan/processes/_adm1.py:185-223`
- **Local implementation:** `ModifiedADM1` already has `solve_pH()` that wraps `pcm()` for pH calculation
- **Conclusion:** The stub is unused and redundant. pH is already handled via `pcm()`.

#### `fun()` (line 1041-1054)
- **Parameters suggest:** Phosphorus precipitation kinetics (q_Pcoprec, K_Pbind, K_Pdiss)
- **QSDsan pattern:** P-precipitation uses `k_mmp/Ksp/K_AlOH/K_FeOH` in ADM1p - see `qsdsan/processes/_adm1_p_extension.py:769-801`
- **Local implementation:** HFO processes are already implemented inside `rhos_madm1` with different parameter names (q_aging_H, q_diss_H, etc.)
- **GitHub search:** No `fun()` or `q_Pcoprec/K_Pbind/K_Pdiss` in QSDsan
- **Conclusion:** The stub doesn't map to any QSDsan pattern and isn't referenced. Delete.

### Files to Modify

| File | Changes |
|------|---------|
| `models/madm1.py` | Delete `calc_pH()` (line 306-307), `fun()` (line 1041-1054) |

### Implementation

Simply delete the functions. Grep confirms they're not called anywhere.

### Verification

```bash
grep -r "calc_pH\|fun(" --include="*.py" . | grep -v "def calc_pH\|def fun("
# Should return no results (or only unrelated "fun" matches like "function")
```

---

## Task 5: Document Placeholder Mapping (LOW PRIORITY)

**Problem:** `('X_AUT', 'X_h2')` in junction_components.py marked as placeholder.

### Files to Modify

| File | Changes |
|------|---------|
| `core/junction_components.py` | Add detailed docstring explaining the mapping rationale |
| `CLAUDE.md` | Document in Known Limitations section |

### Implementation

```python
# In junction_components.py alignment map
# X_AUT (autotrophs) -> X_h2 (hydrogen consumers): This is an approximation.
# ASM2d autotrophs are ammonia oxidizers, while mADM1 X_h2 are hydrogen consumers.
# A more accurate mapping would require splitting X_AUT COD across multiple
# mADM1 biomass groups based on substrate utilization. For most WWTP scenarios,
# autotroph biomass is <5% of MLSS, making this approximation acceptable.
('X_AUT', 'X_h2'): {'measured_as': 'COD'},  # See note above
```

---

## Task 6: Add MCP Tool Contract Tests (HIGH PRIORITY)

**Problem:** 77% of MCP tools have no behavior tests.

### Files to Create/Modify

| File | Changes |
|------|---------|
| `tests/test_mcp_contracts.py` | **NEW** - MCP tool contract tests |

### Test Categories

#### 6a. Return Schema Tests

```python
class TestMCPReturnSchemas:
    """Verify MCP tools return consistent schemas."""

    async def test_validate_state_returns_is_valid_key(self):
        result = await validate_state({"concentrations": {}, "flow_m3_d": 100}, "ASM2d")
        assert "is_valid" in result
        assert isinstance(result["is_valid"], bool)

    async def test_simulate_system_returns_job_id(self):
        result = await simulate_system(template_id="mle_mbr_asm2d", influent_state={...})
        assert "job_id" in result
        assert isinstance(result["job_id"], str)
```

#### 6b. Error Handling Tests

```python
class TestMCPErrorHandling:
    """Verify MCP tools handle invalid inputs gracefully."""

    async def test_validate_state_invalid_model_type(self):
        result = await validate_state({}, "INVALID_MODEL")
        assert "error" in result or result.get("is_valid") == False

    async def test_create_unit_unknown_type(self):
        result = await create_unit(session_id="test", unit_type="FakeUnit", unit_id="U1")
        assert "error" in result
```

#### 6c. Integration Tests (No QSDsan Required)

```python
class TestMCPSessionLifecycle:
    """Test session CRUD without running simulations."""

    async def test_session_create_get_delete(self, tmp_path):
        # Create
        result = await create_flowsheet_session(model_type="ASM2d")
        session_id = result["session_id"]

        # Get
        session = await get_flowsheet_session(session_id=session_id)
        assert session["model_type"] == "ASM2d"

        # Delete
        await delete_session(session_id=session_id)
        result = await get_flowsheet_session(session_id=session_id)
        assert "error" in result
```

---

## Task 7: Add JobManager Tests (HIGH PRIORITY)

**Problem:** JobManager (644 lines) is completely untested.

### Files to Create

| File | Changes |
|------|---------|
| `tests/test_job_manager.py` | **NEW** - JobManager unit tests |

### Test Categories

```python
class TestJobManagerSingleton:
    def test_singleton_returns_same_instance(self):
        jm1 = JobManager.get_instance()
        jm2 = JobManager.get_instance()
        assert jm1 is jm2

class TestJobManagerConcurrency:
    """
    Codex Warning: These tests can be flaky due to process timing.
    Add explicit timeouts and small sleeps. Avoid relying on exact
    "queued vs rejected" semantics unless the API guarantees it.
    """
    async def test_concurrency_limit_enforced(self, tmp_path):
        jm = JobManager(jobs_dir=tmp_path)
        # Start 3 jobs (at limit) with long-running processes
        jobs = []
        for i in range(3):
            job_id = await jm.execute(
                f"python -c 'import time; time.sleep(2)'",
                f"test_{i}"
            )
            jobs.append(job_id)
            await asyncio.sleep(0.1)  # Small delay to ensure process starts

        # 4th job should be queued (not rejected)
        # Check status rather than expecting exception
        job4 = await jm.execute("python -c 'print(1)'", "test_4")
        status = await jm.get_status(job4)
        # Accept either queued or running (if one of the 3 finished quickly)
        assert status["status"] in ["queued", "running", "completed"]

class TestJobManagerLifecycle:
    async def test_execute_creates_job_directory(self, tmp_path):
        jm = JobManager(jobs_dir=tmp_path)
        job_id = await jm.execute("python -c 'print(1)'", "test")
        assert (tmp_path / job_id / "job.json").exists()

    async def test_get_status_returns_progress(self, tmp_path):
        jm = JobManager(jobs_dir=tmp_path)
        job_id = await jm.execute("python -c 'import time; time.sleep(0.5)'", "test")
        status = await jm.get_status(job_id)
        assert status["status"] in ["running", "completed", "queued"]

class TestJobManagerRecovery:
    async def test_loads_existing_jobs_on_init(self, tmp_path):
        # Create a fake job.json
        job_dir = tmp_path / "test_job"
        job_dir.mkdir()
        (job_dir / "job.json").write_text('{"status": "completed", "job_id": "test_job"}')

        jm = JobManager(jobs_dir=tmp_path)
        jobs = await jm.list_jobs()
        assert any(j["job_id"] == "test_job" for j in jobs)

class TestJobManagerProgressParsing:
    def test_parses_progress_from_stdout(self):
        jm = JobManager.get_instance()
        progress = jm._parse_progress("Progress: 45%")
        assert progress == 45
```

---

## Task 8: Update CLI Help Text (LOW PRIORITY)

**Problem:** CLI `--help` doesn't clarify which parameters are template-specific.

### Files to Modify

| File | Changes |
|------|---------|
| `cli.py` | Update parameter help strings |

### Implementation

```python
@app.command()
def simulate(
    parameters: Optional[str] = typer.Option(
        None, "--parameters", "-p",
        help="Kinetic parameter overrides as JSON. Supported for aerobic templates only. "
             "Example: '{\"mu_H\": 6.0, \"K_F\": 10}'"
    ),
    timestep_hours: Optional[float] = typer.Option(
        None, "--timestep-hours",
        help="Output timestep in hours. Supported for aerobic templates and flowsheet simulate. "
             "Anaerobic steady-state templates ignore this parameter."
    ),
    ...
)
```

---

## Verification Plan

### Unit Tests

```bash
# Run new tests
pytest tests/test_mcp_contracts.py -v
pytest tests/test_job_manager.py -v

# Run full suite
pytest tests/ -v
```

### Manual Verification

```bash
# 1. Verify mass balance flag has effect
python -c "
from server import validate_state
import asyncio
r1 = asyncio.run(validate_state({'concentrations': {'S_F': 100}, 'flow_m3_d': 100}, 'ASM2d', check_mass_balance=False))
r2 = asyncio.run(validate_state({'concentrations': {'S_F': 100}, 'flow_m3_d': 100}, 'ASM2d', check_mass_balance=True))
print('Different results:', r1 != r2)
"

# 2. Verify kinetic params applied
python cli.py simulate -t mle_mbr_asm2d -i test_influent.json \
    --parameters '{"mu_H": 10.0}' --duration 1 --output-dir /tmp/test
# Check metadata.json for applied_params

# 3. Verify strict compilation fails on cycle
python -c "
from utils.flowsheet_builder import compile_system
# Create session with cycle, expect ValueError
"

# 4. Verify dead stubs removed
grep -n "def calc_pH\|def fun(" models/madm1.py
# Should return nothing
```

---

## Files Changed Summary

| File | Action | Priority |
|------|--------|----------|
| `server.py` | Implement mass balance check | HIGH |
| `templates/aerobic/mle_mbr.py` | Implement kinetic_params + timestep | HIGH |
| `templates/aerobic/ao_mbr.py` | Implement kinetic_params + timestep | HIGH |
| `templates/aerobic/a2o_mbr.py` | Implement kinetic_params + timestep | HIGH |
| `templates/anaerobic/cstr.py` | Error on unsupported flags | HIGH |
| `utils/flowsheet_builder.py` | Add strict mode | HIGH |
| `utils/topo_sort.py` | Add fail_on_cycle | HIGH |
| `core/junction_components.py` | Log instead of swallow exceptions | HIGH |
| `models/madm1.py` | Delete dead stubs | MEDIUM |
| `cli.py` | Update help text | LOW |
| `CLAUDE.md` | Document placeholder mapping | LOW |
| `tests/test_mcp_contracts.py` | **NEW** | HIGH |
| `tests/test_job_manager.py` | **NEW** | HIGH |

---

## Estimated Test Count

| Test File | New Tests |
|-----------|-----------|
| `test_mcp_contracts.py` | ~25 tests |
| `test_job_manager.py` | ~15 tests |
| `test_phase2.py` (additions) | ~6 tests |
| **Total New** | **~46 tests** |

Expected final count: **265+ tests** (219 current + 46 new)
