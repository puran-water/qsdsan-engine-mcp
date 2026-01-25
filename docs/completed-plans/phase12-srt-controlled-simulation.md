# Plan: SRT-Controlled Steady-State Simulation

## Problem Statement

Current convergence-based simulation reports "converged at 11 days" with 89.6% NH4 removal, but this is **pre-seeded equilibrium**, not true steady state:
- System is inoculated with 248.5 mg COD/L X_AUT (nitrifiers already established)
- SRT calculated post-hoc is 633 days (unrealistically high)
- True steady state requires 2-3× SRT to reach equilibrium

**User Request:** For flowsheets with MBR or secondary clarifier that decouple HRT and SRT, can we simulate with a **target SRT setpoint** where sludge wasting is controlled to maintain that SRT?

---

## Research Findings

### Current State

| Aspect | Status | Details |
|--------|--------|---------|
| **SRT Control** | None | Q_was is fixed at construction; SRT calculated post-hoc |
| **WAS Flow** | Static | User specifies Q_was_m3_d (e.g., 768 m³/d) |
| **Splitter Ratio** | Fixed | `split_ras = Q_ras / (Q_ras + Q_was)` |
| **BioSTEAM Specs** | Not Used | No `add_specification()` calls in codebase |
| **Feedback Control** | None | Only convergence monitoring, no parameter adjustment |

### BioSTEAM Capability

BioSTEAM supports `add_specification()` for dynamic control:
```python
spec = bst.Specification(ode=control_function, impacted_units=[splitter])
sys.add_specification(spec)
```

But this requires:
- Specs added BEFORE simulation starts
- Mutable system references during simulation
- Not trivially compatible with current compile-then-simulate pattern

### SRT Formula

```
SRT = Total_Biomass_in_System (kg) / Biomass_Leaving_via_WAS (kg/d)
    = (V_total × MLVSS) / (Q_was × TSS_was)
```

To achieve target SRT:
```
Q_was = (V_total × MLVSS) / (SRT_target × TSS_was)
```

**Challenge:** MLVSS and TSS_was depend on steady-state biomass, which we don't know until we simulate.

---

## Proposed Approach: Iterative SRT-Controlled Simulation

### Concept

Instead of fixed Q_was, implement an **outer iteration loop** that:
1. Estimates initial Q_was from target SRT
2. Runs simulation to steady state
3. Calculates achieved SRT from results
4. Adjusts Q_was and repeats if SRT error exceeds tolerance

This achieves true steady state at the target SRT without requiring BioSTEAM's internal specification mechanism.

### Algorithm

```python
def run_to_target_srt(
    system: System,
    target_srt_days: float,
    srt_tolerance: float = 0.1,  # 10% tolerance
    max_iterations: int = 10,
    **convergence_kwargs,
) -> Tuple[float, str, Dict]:
    """
    Run simulation iteratively until target SRT is achieved.

    Returns:
        (achieved_srt, status, metrics)
    """
    # Initial Q_was estimate (heuristic)
    Q_was = estimate_initial_q_was(system, target_srt_days)

    for iteration in range(max_iterations):
        # Update WAS splitter ratio
        update_was_flow(system, Q_was)

        # Run to steady state (existing convergence)
        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            **convergence_kwargs,
        )

        # Calculate achieved SRT
        achieved_srt = calculate_srt(system)
        srt_error = abs(achieved_srt - target_srt_days) / target_srt_days

        if srt_error < srt_tolerance:
            return achieved_srt, 'srt_converged', metrics

        # Adjust Q_was using proportional control
        Q_was = Q_was * (achieved_srt / target_srt_days)

    return achieved_srt, 'srt_max_iterations', metrics
```

### Key Functions Needed

1. **`estimate_initial_q_was()`** - Heuristic initial guess based on target SRT, volumes, typical MLVSS
2. **`update_was_flow()`** - Modify splitter ratio in compiled system
3. **`calculate_srt()`** - Calculate SRT from current biomass inventory and WAS flux
4. **`run_to_target_srt()`** - Outer iteration loop

---

## Implementation Plan

### Task 1: Add SRT Calculation Utility (`utils/srt_control.py`)

**Codex Correction (Round 2):**
1. `get_retained_mass()` expects an **iterable** of IDs, not a single string - call once per unit
2. Add physical feasibility checks (Q_ras + Q_was <= Q_in)
3. Use flow-scaled bounds instead of fixed (10, 5000) m³/d

```python
from qsdsan.utils.wwt_design import get_SRT  # If available
import numpy as np

# Biomass IDs per model type
BIOMASS_IDS = {
    'ASM2d': ['X_H', 'X_AUT', 'X_PAO', 'X_PHA', 'X_PP'],
    'ASM1': ['X_B_H', 'X_B_A'],
    'mADM1': ['X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2'],
}


def calculate_srt(
    system: System,
    wastage_streams: List[WasteStream],
    effluent_streams: Optional[List[WasteStream]] = None,
    biomass_IDs: Optional[List[str]] = None,
    model_type: str = 'ASM2d',
) -> float:
    """
    Calculate SRT using QSDsan conventions.

    SRT = Total Retained Biomass (kg) / Biomass Leaving Rate (kg/d)

    Uses unit.get_retained_mass(biomass_IDs) where available, falls back to
    reactor volume × state concentration.

    For clarifier systems, includes effluent solids in denominator.

    CODEX FIX: get_retained_mass expects iterable of IDs, not single string.
    Call ONCE per unit with tuple of all biomass IDs.
    """
    if biomass_IDs is None:
        biomass_IDs = BIOMASS_IDS.get(model_type, BIOMASS_IDS['ASM2d'])

    # Convert to tuple for get_retained_mass (expects iterable)
    biomass_IDs_tuple = tuple(biomass_IDs)

    # Calculate retained biomass inventory
    total_biomass = 0.0
    for unit in system.units:
        if hasattr(unit, 'get_retained_mass'):
            try:
                # CODEX FIX: Call once with tuple of all biomass IDs
                # Returns dict {ID: mass_in_kg} or total mass depending on unit
                mass_result = unit.get_retained_mass(biomass_IDs_tuple)
                if isinstance(mass_result, dict):
                    total_biomass += sum(mass_result.values())
                else:
                    total_biomass += float(mass_result)
            except Exception:
                # Fallback if get_retained_mass fails for this unit
                pass
        elif hasattr(unit, 'V_max') and hasattr(unit, 'state'):
            # Fallback for units without get_retained_mass
            for comp in biomass_IDs:
                try:
                    idx = unit.components.index(comp)
                    # state is in kg/m³, V_max in m³ → result in kg
                    total_biomass += unit.state[idx] * unit.V_max
                except (ValueError, IndexError, AttributeError):
                    pass

    # Calculate biomass leaving rate (WAS + optional effluent)
    biomass_rate = 0.0
    for stream in wastage_streams:
        for comp in biomass_IDs:
            try:
                # imass is in kg/hr, convert to kg/d
                biomass_rate += stream.imass[comp] * 24
            except (KeyError, AttributeError):
                pass

    # Include effluent solids for clarifier systems
    if effluent_streams:
        for stream in effluent_streams:
            for comp in biomass_IDs:
                try:
                    biomass_rate += stream.imass[comp] * 24  # kg/d
                except (KeyError, AttributeError):
                    pass

    return total_biomass / biomass_rate if biomass_rate > 0 else float('inf')


def get_influent_flow(system: System) -> float:
    """
    Get total influent flow rate (m³/d) for bounds calculation.

    CODEX FIX: Needed for flow-scaled Q_was bounds.
    """
    q_in = 0.0
    for stream in system.feeds:
        if hasattr(stream, 'F_vol'):
            q_in += stream.F_vol * 24  # m³/hr → m³/d
    return q_in if q_in > 0 else 1000.0  # Default fallback


def validate_flow_feasibility(
    q_was: float,
    q_ras: float,
    q_in: float,
) -> Tuple[bool, str]:
    """
    Check physical feasibility of flow rates.

    Only Q_was is constrained by mass balance: Q_in = Q_was + Q_effluent.
    Q_ras is an internal recycle and can be any multiple of Q_in.

    Returns (is_valid, error_message)
    """
    if q_was < 0:
        return False, f"Q_was ({q_was:.1f}) cannot be negative"
    if q_ras < 0:
        return False, f"Q_ras ({q_ras:.1f}) cannot be negative"
    if q_was > q_in:  # Mass balance: Q_in = Q_was + Q_effluent
        return False, f"Q_was ({q_was:.1f}) cannot exceed influent Q_in ({q_in:.1f})"
    return True, ""


def update_wastage_actuator(
    system: System,
    q_was: float,
    q_ras: Optional[float] = None,
    validate: bool = True,
) -> Tuple[bool, str]:
    """
    Update WAS flow using unit-specific actuators.

    Codex: Different units have different control knobs:
    - CompletelyMixedMBR: pumped_flow = Q_ras + Q_was
    - FlatBottomCircularClarifier: wastage = Q_was (direct)
    - Splitter: split = Q_ras / (Q_ras + Q_was) (if downstream of MBR)

    CODEX FIX (Round 2):
    - Improved q_ras inference: use typical RAS ratio (3-4× Q_in) instead of 0.9*pumped_flow
    - Added feasibility validation before applying changes
    - Returns (success, error_message) for caller to handle

    Returns:
        (success: bool, error_message: str)
    """
    q_in = get_influent_flow(system)

    for unit in system.units:
        unit_type = type(unit).__name__

        if unit_type == 'CompletelyMixedMBR':
            # MBR: Control via pumped_flow (total retentate)
            if q_ras is None:
                # CODEX FIX: Use typical RAS ratio (3-4× influent) instead of heuristic
                # Most activated sludge systems operate with RAS ratio 0.5-1.5× Q_in
                # For MBR, retentate = RAS (to anoxic) + WAS
                q_ras = q_in * 1.0  # Default RAS = 1× influent

            # Validate before applying
            if validate:
                valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                if not valid:
                    return False, msg

            unit.pumped_flow = q_ras + q_was
            # Also update downstream splitter if present
            _update_downstream_splitter(unit, q_ras, q_was)
            return True, ""

        elif 'Clarifier' in unit_type:
            # Clarifier: Direct wastage control
            if hasattr(unit, 'wastage'):
                # Get underflow (RAS) for feasibility check
                if q_ras is None:
                    q_ras = getattr(unit, 'underflow', q_in * 0.5)

                if validate:
                    valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                    if not valid:
                        return False, msg

                unit.wastage = q_was
                return True, ""

    # Fallback: Find and update WAS splitter
    for unit in system.units:
        if type(unit).__name__ == 'Splitter':
            if any('WAS' in str(out) or 'was' in str(out).lower() for out in unit.outs):
                if q_ras is None:
                    q_ras = unit.outs[0].F_vol * 24  # m³/d from RAS stream

                if validate:
                    valid, msg = validate_flow_feasibility(q_was, q_ras, q_in)
                    if not valid:
                        return False, msg

                unit.split = q_ras / (q_ras + q_was) if (q_ras + q_was) > 0 else 0.5
                return True, ""

    return False, "No WAS actuator (MBR, Clarifier, or Splitter) found in system"


def _update_downstream_splitter(mbr_unit, q_ras: float, q_was: float) -> None:
    """Update splitter downstream of MBR (splits retentate to RAS/WAS)."""
    if hasattr(mbr_unit, 'outs') and len(mbr_unit.outs) > 1:
        retentate = mbr_unit.outs[1]  # pumped/retentate outlet
        if retentate and retentate.sink:
            splitter = retentate.sink
            if type(splitter).__name__ == 'Splitter':
                splitter.split = q_ras / (q_ras + q_was) if (q_ras + q_was) > 0 else 0.5
```

### Task 2: Create SRT-Controlled Wrapper (`utils/run_to_srt.py`)

**Codex Correction (Round 2):**
1. Use **flow-scaled bounds** instead of fixed (10, 5000) m³/d
2. Add **adaptive bracket expansion** when initial bounds don't bracket the root
3. Handle **feasibility failures** from actuator updates gracefully

```python
from scipy.optimize import brentq
import numpy as np
import logging

logger = logging.getLogger(__name__)


def compute_q_was_bounds(
    system: System,
    target_srt_days: float,
) -> Tuple[float, float]:
    """
    Compute flow-scaled Q_was bounds based on system characteristics.

    CODEX FIX: Use flow-scaled bounds instead of fixed (10, 5000) m³/d.

    Heuristic:
    - Q_was_min: Very long SRT (~5× target) → low wasting
    - Q_was_max: Very short SRT (~0.2× target) → high wasting

    Both scaled to influent flow for different plant sizes.
    """
    q_in = get_influent_flow(system)

    # Estimate total reactor volume (rough)
    total_volume = 0.0
    for unit in system.units:
        if hasattr(unit, 'V_max'):
            total_volume += unit.V_max
        elif hasattr(unit, 'V'):
            total_volume += unit.V

    if total_volume <= 0:
        total_volume = q_in * 0.5  # Assume HRT ~ 12 hrs

    # Assume typical MLVSS ~3000 mg/L = 3 kg/m³
    typical_mlvss = 3.0  # kg/m³

    # Q_was = V × MLVSS / (SRT × TSS_was)
    # Assume TSS_was ≈ 1.2 × MLVSS for thickened sludge
    tss_was = typical_mlvss * 1.2

    # Q_was for target SRT
    q_was_target = (total_volume * typical_mlvss) / (target_srt_days * tss_was)

    # Bounds: 0.1× to 10× of estimated Q_was
    q_was_min = max(1.0, q_was_target * 0.1)  # At least 1 m³/d
    q_was_max = min(q_in * 0.5, q_was_target * 10)  # At most 50% of Q_in

    return q_was_min, q_was_max


def run_to_target_srt(
    system: System,
    target_srt_days: float,
    wastage_streams: List[WasteStream],
    effluent_streams: Optional[List[WasteStream]] = None,
    convergence_streams: List[WasteStream] = None,
    convergence_components: Dict[str, List[str]] = None,
    srt_tolerance: float = 0.1,
    max_srt_iterations: int = 10,
    q_was_bounds: Optional[Tuple[float, float]] = None,  # Auto-computed if None
    min_time_multiplier: float = 2.0,
    **convergence_kwargs,
) -> Tuple[float, str, Dict]:
    """
    Run simulation until target SRT is achieved at steady state.

    Uses bracketed root-finding (brentq) with adaptive bracket expansion.

    CODEX FIX (Round 2):
    - Auto-compute flow-scaled bounds if not provided
    - Expand brackets adaptively if root not initially bracketed
    - Handle actuator feasibility failures gracefully

    Parameters
    ----------
    q_was_bounds : Tuple[float, float], optional
        Min/max Q_was to search (m³/d). Auto-computed from system flows if None.
    min_time_multiplier : float
        Minimum simulation time = multiplier × target_srt_days.
        Default 2.0 ensures SRT dynamics equilibrate.
    """
    min_simulation_time = min_time_multiplier * target_srt_days

    # CODEX FIX: Auto-compute bounds if not provided
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(system, target_srt_days)
        logger.info(f"Auto-computed Q_was bounds: ({q_was_bounds[0]:.1f}, {q_was_bounds[1]:.1f}) m³/d")

    def srt_residual(q_was: float) -> float:
        """Objective function: achieved_SRT - target_SRT."""
        # CODEX FIX: Handle actuator feasibility failures
        success, msg = update_wastage_actuator(system, q_was, validate=True)
        if not success:
            logger.warning(f"Actuator update failed at Q_was={q_was:.1f}: {msg}")
            # Return large residual to push optimizer away from infeasible region
            return float('inf') if q_was > target_srt_days else float('-inf')

        # Run to steady state
        system.reset_cache()
        for stream in convergence_streams or []:
            if hasattr(stream, 'scope'):
                stream.scope.reset_cache()

        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            **convergence_kwargs,
        )

        # Calculate achieved SRT
        achieved_srt = calculate_srt(system, wastage_streams, effluent_streams)

        logger.debug(f"Q_was={q_was:.1f} → SRT={achieved_srt:.1f} (target={target_srt_days})")

        # Handle inf/nan
        if not np.isfinite(achieved_srt):
            mid = (q_was_bounds[0] + q_was_bounds[1]) / 2
            return float('inf') if q_was < mid else float('-inf')

        return achieved_srt - target_srt_days

    # CODEX FIX: Adaptive bracket expansion if root not initially bracketed
    def try_brentq_with_expansion(
        lo: float, hi: float, max_expansions: int = 3
    ) -> Tuple[Optional[float], str]:
        """Try brentq with progressive bracket expansion."""
        for expansion in range(max_expansions + 1):
            try:
                # Evaluate at bounds to check if root is bracketed
                f_lo = srt_residual(lo)
                f_hi = srt_residual(hi)

                if np.sign(f_lo) != np.sign(f_hi):
                    # Root is bracketed, use brentq
                    q_optimal = brentq(
                        srt_residual, lo, hi,
                        xtol=lo * 0.01,  # 1% tolerance
                        maxiter=max_srt_iterations - expansion * 2,
                    )
                    return q_optimal, 'srt_converged'

                # Root not bracketed - expand bounds
                if expansion < max_expansions:
                    if f_lo > 0 and f_hi > 0:
                        # Both SRTs too high → need higher Q_was
                        hi = hi * 2
                    elif f_lo < 0 and f_hi < 0:
                        # Both SRTs too low → need lower Q_was
                        lo = lo / 2
                    logger.info(f"Expanding bounds to ({lo:.1f}, {hi:.1f})")

            except ValueError as e:
                logger.warning(f"brentq failed: {e}")
                break

        return None, 'srt_bracket_failed'

    q_was_optimal, status = try_brentq_with_expansion(q_was_bounds[0], q_was_bounds[1])

    if q_was_optimal is None:
        # Fallback: use best value found during bracket search
        logger.warning("Root-finding failed, using iterative fallback")
        return _iterative_srt_search(
            system, target_srt_days, wastage_streams, effluent_streams,
            convergence_streams, convergence_components,
            srt_tolerance, max_srt_iterations, q_was_bounds,
            min_simulation_time, **convergence_kwargs
        )

    # Final run with optimal Q_was
    update_wastage_actuator(system, q_was_optimal, validate=False)
    converged_at, _, metrics = run_system_to_steady_state(
        system=system,
        convergence_streams=convergence_streams,
        convergence_components=convergence_components,
        min_time=min_simulation_time,
        **convergence_kwargs,
    )
    achieved_srt = calculate_srt(system, wastage_streams, effluent_streams)

    return achieved_srt, status, metrics


def _iterative_srt_search(
    system: System,
    target_srt_days: float,
    wastage_streams: List[WasteStream],
    effluent_streams: Optional[List[WasteStream]],
    convergence_streams: List[WasteStream],
    convergence_components: Dict[str, List[str]],
    srt_tolerance: float,
    max_iterations: int,
    q_was_bounds: Tuple[float, float],
    min_simulation_time: float,
    **convergence_kwargs,
) -> Tuple[float, str, Dict]:
    """
    Fallback iterative search when bracketed root-finding fails.

    Uses proportional adjustment with damping.
    """
    q_was = (q_was_bounds[0] + q_was_bounds[1]) / 2  # Start at midpoint
    best_srt = float('inf')
    best_q_was = q_was
    best_metrics = {}

    for iteration in range(max_iterations):
        success, msg = update_wastage_actuator(system, q_was, validate=True)
        if not success:
            q_was = q_was * 0.9  # Reduce if infeasible
            continue

        system.reset_cache()
        converged_at, status, metrics = run_system_to_steady_state(
            system=system,
            convergence_streams=convergence_streams,
            convergence_components=convergence_components,
            min_time=min_simulation_time,
            **convergence_kwargs,
        )

        achieved_srt = calculate_srt(system, wastage_streams, effluent_streams)
        srt_error = abs(achieved_srt - target_srt_days) / target_srt_days

        if srt_error < abs(best_srt - target_srt_days) / target_srt_days:
            best_srt = achieved_srt
            best_q_was = q_was
            best_metrics = metrics

        if srt_error < srt_tolerance:
            return achieved_srt, 'srt_converged', metrics

        # Damped proportional adjustment
        damping = 0.7
        q_was = q_was * (1 + damping * (achieved_srt / target_srt_days - 1))
        q_was = max(q_was_bounds[0], min(q_was_bounds[1], q_was))

        logger.info(f"Iteration {iteration+1}: SRT={achieved_srt:.1f} (error={srt_error:.1%}), Q_was→{q_was:.1f}")

    return best_srt, 'srt_max_iterations', best_metrics
```

### Task 3: Update Aerobic Templates

Add new parameters to `build_and_run()`:

```python
def build_and_run(
    influent_state: Dict[str, Any],
    reactor_config: Optional[Dict[str, Any]] = None,
    # Existing params...
    run_to_convergence: bool = False,
    # NEW: SRT control (implies run_to_convergence=True)
    target_srt_days: Optional[float] = None,  # If set, enables SRT control
    srt_tolerance: float = 0.1,               # 10% tolerance on achieved SRT
    max_srt_iterations: int = 10,             # Max Q_was adjustment iterations
    ...
)
```

When `target_srt_days` is set:
1. Calculate initial Q_was from target SRT using heuristic
2. Keep inoculation enabled (prevents VFA accumulation/failure)
3. Run SRT-controlled iteration loop
4. Enforce minimum simulation time of 2× target SRT
5. Report achieved SRT and total simulation time

### Task 4: Update Flowsheet Builder

Add SRT control to `simulate_compiled_system()`:

```python
def simulate_compiled_system(
    ...
    target_srt_days: Optional[float] = None,
    srt_tolerance: float = 0.1,
    ...
)
```

Auto-detect WAS splitter and apply SRT control loop.

### Task 5: Update CLI/MCP Interface

**Codex Correction:** Make `target_srt_days` optional with warnings instead of hard errors. Don't break existing usage.

```bash
# CLI - SRT control optional but recommended for MBR/clarifier systems
python cli.py simulate \
    --template mle_mbr_asm2d \
    --influent tests/test_asm2d_state.json \
    --target-srt 15 \
    --srt-tolerance 0.1

# Without --target-srt: runs with fixed Q_was, warns about achieved SRT
python cli.py simulate \
    --template mle_mbr_asm2d \
    --influent tests/test_asm2d_state.json
# WARNING: No target SRT specified. Achieved SRT = 633 days (may be unrealistic).
# Consider using --target-srt for accurate steady-state simulation.
```

```python
# MCP - target_srt_days optional with warnings
await simulate_system(
    template="mle_mbr_asm2d",
    influent_state={...},
    target_srt_days=15.0,  # Optional: enables SRT control
    srt_tolerance=0.1,
)
```

**Validation:** If MBR/clarifier detected and `target_srt_days` not provided:
- **Warn** (don't error) about potentially unrealistic SRT
- **Report achieved SRT** in results
- **Suggest default SRT** based on system type (15d aerobic, 30-50d anaerobic)

```python
if has_srt_decoupling(system) and target_srt_days is None:
    logger.warning(
        "No target SRT specified for system with MBR/clarifier. "
        "Simulation will use fixed Q_was; achieved SRT may be unrealistic. "
        "Consider setting target_srt_days for accurate steady-state results."
    )
    # Report achieved SRT post-simulation
    achieved_srt = calculate_srt(system, wastage_streams, effluent_streams)
    result['achieved_srt_days'] = achieved_srt
    result['srt_warning'] = f"Achieved SRT = {achieved_srt:.1f} days (no target specified)"
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `utils/srt_control.py` | **NEW**: SRT calculation (using QSDsan patterns), unit-specific actuator updates, `has_srt_decoupling()` |
| `utils/run_to_srt.py` | **NEW**: SRT-controlled wrapper with bracketed root-finding (brentq) |
| `utils/run_to_convergence.py` | Add `min_time` parameter to enforce minimum simulation time |
| `templates/aerobic/mle_mbr.py` | Add `target_srt_days`, `srt_tolerance` params; warn (not error) if missing |
| `templates/aerobic/ao_mbr.py` | Add SRT control params |
| `templates/aerobic/a2o_mbr.py` | Add SRT control params |
| `utils/flowsheet_builder.py` | Add SRT control to `simulate_compiled_system()`; auto-detect MBR/clarifier actuators |
| `server.py` | Add SRT params to `simulate_system()` and `simulate_built_system()` |
| `cli.py` | Add `--target-srt`, `--srt-tolerance`, `--q-was-bounds` flags |
| `tests/test_srt_control.py` | **NEW**: SRT control tests (unit + integration) |
| `CLAUDE.md` | Document SRT control feature for decoupled systems |

**Note (Codex):** Anaerobic CSTR template removed from scope. Plain CSTR has SRT ≈ HRT (no solids separation). SRT control only applies to systems with MBR or clarifier that decouple HRT and SRT. For anaerobic systems with solids separation (e.g., AnMBR), add SRT control via flowsheet builder.

---

## Expected Behavior

### With Target SRT Control (Aerobic, SRT=15 days)

```
Input: target_srt_days=15, srt_tolerance=0.1

Minimum simulation time: 2 × 15 = 30 days

Iteration 1:
  Initial Q_was: 533 m³/d (estimated from heuristic)
  Simulation: 30 days (minimum enforced)
  Achieved SRT: 22.3 days (error: 48%)

Iteration 2:
  Adjusted Q_was: 792 m³/d
  Simulation: 30 days
  Achieved SRT: 15.8 days (error: 5.3%)

Iteration 3:
  Adjusted Q_was: 837 m³/d
  Simulation: 30 days
  Achieved SRT: 15.1 days (error: 0.7% < 10% tolerance)

Result: SRT converged to 15.1 days after 3 iterations (90 total days)
```

### With Target SRT Control (Anaerobic, SRT=50 days)

```
Input: target_srt_days=50, srt_tolerance=0.1

Minimum simulation time: 2 × 50 = 100 days

Iteration 1:
  Initial Q_was: estimated from volumes
  Simulation: 100 days
  Achieved SRT: 65 days (error: 30%)

Iteration 2:
  Adjusted Q_was
  Simulation: 100 days
  Achieved SRT: 52 days (error: 4%)

Result: SRT converged after 2 iterations (200 total days)
```

### Comparison: Current vs SRT-Controlled

| Mode | Inoculation | Min Time | Achieved SRT | Status |
|------|-------------|----------|--------------|--------|
| Current (fixed Q_was) | Yes (248 mg COD/L X_AUT) | 11 days | 633 days (unrealistic) | Pre-seeded equilibrium |
| SRT-controlled (target=15d) | Yes | 30+ days (2×SRT) | 15.1 days ± 10% | True steady state |

---

## Verification Plan

1. **Unit Tests**: SRT calculation, actuator detection, root-finding
   ```bash
   pytest tests/test_srt_control.py -v
   ```

2. **Integration Test**: MLE-MBR with target SRT=15 days
   ```bash
   python cli.py simulate \
       --template mle_mbr_asm2d \
       --influent tests/test_asm2d_state.json \
       --target-srt 15 \
       --srt-tolerance 0.1
   ```

3. **Verify SRT Achieved**: Check that achieved SRT ≈ target ± 10%

4. **Verify Minimum Time**: Simulation should run at least 2× SRT (30 days for SRT=15)

5. **Warning Test**: Confirm warning (not error) when target_srt_days missing
   ```bash
   python cli.py simulate \
       --template mle_mbr_asm2d \
       --influent tests/test_asm2d_state.json
   # Should warn about unrealistic achieved SRT, not fail
   ```

6. **Actuator Tests**: Verify correct actuator for MBR (`pumped_flow`) vs Clarifier (`wastage`)

---

## Design Decisions (User Confirmed)

### 1. Inoculation: Keep ON

**Rationale:** Without inoculation, unseeded systems (especially anaerobic digesters) will accumulate VFAs and fail. Inoculation provides stability while still achieving true steady state.

**Minimum Simulation Time:** Run until the **LATER** of:
- Convergence detected (dC/dt < tolerance)
- **2× target SRT** days of simulation

This ensures both mathematical steady state AND sufficient time for SRT dynamics to equilibrate.

```python
min_time = 2.0 * target_srt_days
actual_converged_at = max(converged_at, min_time)
```

### 2. Scope: All Systems with HRT/SRT Decoupling

Apply SRT control to:
- **Aerobic MBR** (ASM2d, ASM1, mASM2d): MLE-MBR, A/O-MBR, A2O-MBR
- **Aerobic Clarifier** (secondary clarifiers with RAS/WAS)
- **Anaerobic MBR** (mADM1): Longer SRTs (30-100+ days)
- **Dynamic Flowsheets**: Any compiled system with MBR or clarifier

### 3. Algorithm Update (Codex Corrections Applied - Round 2)

**Key Changes from Original Plan (Round 1):**
1. Use **bracketed root-finding** (scipy.brentq) instead of proportional update
2. Use **unit-specific actuators** (MBR.pumped_flow, Clarifier.wastage)
3. Use **QSDsan's SRT calculation patterns** (get_retained_mass, stream imass)
4. **Warn instead of error** when target_srt not specified

**Additional Fixes from Codex Round 2:**
5. **Fix `get_retained_mass` signature**: Call once per unit with `tuple(biomass_IDs)`, not once per component
6. **Add physical feasibility checks**: `validate_flow_feasibility(q_was, q_ras, q_in)` before actuator updates
7. **Flow-scaled bounds**: Auto-compute `q_was_bounds` from `get_influent_flow()` instead of fixed (10, 5000)
8. **Adaptive bracket expansion**: Expand bounds automatically when root not initially bracketed
9. **Improved q_ras inference**: Use `q_in * 1.0` (typical RAS ratio) instead of `0.9 * pumped_flow` heuristic
10. **Graceful failure handling**: Return `(False, error_msg)` from actuator updates; fallback to iterative search

```python
from scipy.optimize import brentq

def run_to_target_srt(
    system: System,
    target_srt_days: float,
    wastage_streams: List[WasteStream],
    q_was_bounds: Optional[Tuple[float, float]] = None,  # Auto-computed if None
    min_time_multiplier: float = 2.0,
    **convergence_kwargs,
) -> Tuple[float, str, Dict]:
    """
    Run until target SRT achieved AND minimum 2× SRT elapsed.

    Uses Brent's method with adaptive bracket expansion and feasibility checks.
    """
    min_simulation_time = min_time_multiplier * target_srt_days

    # CODEX FIX: Auto-compute flow-scaled bounds
    if q_was_bounds is None:
        q_was_bounds = compute_q_was_bounds(system, target_srt_days)

    def srt_residual(q_was: float) -> float:
        """f(Q_was) = achieved_SRT - target_SRT"""
        # CODEX FIX: Validate feasibility before applying
        success, msg = update_wastage_actuator(system, q_was, validate=True)
        if not success:
            return float('inf')  # Push optimizer away from infeasible region

        run_system_to_steady_state(system, min_time=min_simulation_time, ...)
        achieved_srt = calculate_srt(system, wastage_streams)
        return achieved_srt - target_srt_days

    # CODEX FIX: Try with adaptive expansion
    q_was_optimal, status = try_brentq_with_expansion(
        srt_residual, q_was_bounds[0], q_was_bounds[1]
    )

    if q_was_optimal is None:
        # CODEX FIX: Fallback to iterative search
        return _iterative_srt_search(system, target_srt_days, ...)

    # Final run with optimal Q_was
    update_wastage_actuator(system, q_was_optimal, validate=False)
    converged_at, status, metrics = run_system_to_steady_state(...)
    achieved_srt = calculate_srt(system, wastage_streams)

    return achieved_srt, 'srt_converged', metrics
```

---

## Codex Review Summary

| Round | Issues Found | Status |
|-------|--------------|--------|
| Round 1 | Wrong actuator (Splitter.split vs MBR.pumped_flow), SRT underspecified, fixed bounds | ✅ Fixed |
| Round 2 | Wrong `get_retained_mass` signature, missing feasibility checks, fixed bounds unsafe | ✅ Fixed |

**Ready for Implementation:** YES (after Round 2 fixes applied above)
