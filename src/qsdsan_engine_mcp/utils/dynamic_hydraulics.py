"""Hydraulic stabilization for dynamic QSDsan recycle simulations."""

from __future__ import annotations

import inspect
import logging
from types import MethodType
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Gas states that are TOTALS over an acid-base pair, so that only part of the state is
#: actually volatile. Used for models that do not declare their own
#: ``_SPECIATING_GASES`` -- stock qsdsan mASM2d carries ``S_IC`` in ``gas_IDs`` and has
#: no ``volatile_fractions``, and is exactly the case BUGS #2 describes.
_SPECIATING_GASES_DEFAULT = ("S_IC", "S_NH4")
_MATERIAL_NEGATIVE_TOLERANCE = -1e-6


def _record_projected_state(unit: Any, values: np.ndarray, location: str) -> None:
    arr = np.asarray(values, dtype=float)
    finite = np.isfinite(arr)
    nonfinite_count = int(arr.size - np.count_nonzero(finite))
    minimum = float(np.min(arr[finite])) if np.any(finite) else None
    if nonfinite_count or (
        minimum is not None and minimum < _MATERIAL_NEGATIVE_TOLERANCE
    ):
        from qsdsan_engine_mcp.models.physicality import record_state_projection_failure

        unit_id = str(getattr(unit, "ID", type(unit).__name__))
        record_state_projection_failure(
            f"{unit_id}:{location}",
            minimum_value=minimum,
            nonfinite_count=nonfinite_count,
        )


def _flow_m3_d(stream: Any) -> float:
    try:
        return float(getattr(stream, "F_vol", 0.0) or 0.0) * 24.0
    except Exception:
        return 0.0


def _finite_nonnegative(values: list[float]) -> bool:
    return all(np.isfinite(value) and value >= 0.0 for value in values)


def _guarded_cstr_update_state(self: Any) -> None:
    arr = self._state
    _record_projected_state(self, arr[:-1], "outlet_projection")
    conc = arr[:-1].copy()
    conc[conc < 1e-16] = 0.0

    fixed_outflows = self._codex_fixed_hydraulic_outflows_m3_d
    if self.split is None:
        y = arr.copy()
        y[:-1] = conc
        y[-1] = fixed_outflows[0] if fixed_outflows else arr[-1]
        self._outs[0].state = y
        return

    for ws, fixed_q in zip(self._outs, fixed_outflows):
        y = arr.copy()
        y[:-1] = conc
        y[-1] = fixed_q
        ws.state = y


def _guarded_cstr_update_dstate(self: Any) -> None:
    arr = self._dstate
    arr[-1] = 0.0

    if self.split is None:
        y = arr.copy()
        y[-1] = 0.0
        self._outs[0].dstate = y
        return

    for ws in self._outs:
        y = arr.copy()
        y[-1] = 0.0
        ws.dstate = y


def _guarded_mbr_update_state(self: Any) -> None:
    arr = self._state
    _record_projected_state(self, arr[:-1], "outlet_projection")
    conc = arr[:-1].copy()
    conc[conc < 1e-16] = 0.0

    fixed_outflows = self._codex_fixed_hydraulic_outflows_m3_d
    flt, rtn = self.outs

    y_flt = arr.copy()
    y_flt[:-1] = conc * self._flt2in_conc_ratio
    y_flt[-1] = fixed_outflows[0]
    flt.state = y_flt

    y_rtn = arr.copy()
    y_rtn[:-1] = conc
    y_rtn[-1] = fixed_outflows[1]
    rtn.state = y_rtn


def _guarded_mbr_update_dstate(self: Any) -> None:
    arr = self._dstate
    arr[-1] = 0.0
    flt, rtn = self.outs

    y_flt = arr.copy()
    y_flt[:-1] = arr[:-1] * self._flt2in_conc_ratio
    y_flt[-1] = 0.0
    flt.dstate = y_flt

    y_rtn = arr.copy()
    y_rtn[-1] = 0.0
    rtn.dstate = y_rtn


_ZERO_FLOW_WRITEBACK_PATCHED = False

# Single source: requirements.txt pins qsdsan==1.5.3 and biosteam>=2.53.11,<2.54.
# The guard replicates `_state2flows` internals, so it is only validated against that
# exact stack; update these together with the pins. Re-validated 2026-08-24 for the
# Wave-5b bump: `_state2flows` and `set_flow_by_concentration` are byte-identical
# between qsdsan 1.4.2 and 1.5.3.
_VALIDATED_QSDSAN_VERSION = "1.5.3"
_VALIDATED_BIOSTEAM_FLOOR = (2, 53, 11)
_VALIDATED_BIOSTEAM_CEILING = (2, 54, 0)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version).split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def _install_zero_flow_writeback_guard() -> None:
    'Let a structurally zero-flow outlet survive the end-of-run state writeback.'
    global _ZERO_FLOW_WRITEBACK_PATCHED
    if _ZERO_FLOW_WRITEBACK_PATCHED:
        return
    import biosteam
    import qsdsan
    from qsdsan import WasteStream

    qsdsan_version = str(getattr(qsdsan, "__version__", ""))
    biosteam_version = str(getattr(biosteam, "__version__", ""))
    if qsdsan_version != _VALIDATED_QSDSAN_VERSION or not (
        _VALIDATED_BIOSTEAM_FLOOR
        <= _version_tuple(biosteam_version)
        < _VALIDATED_BIOSTEAM_CEILING
    ):
        floor = ".".join(str(v) for v in _VALIDATED_BIOSTEAM_FLOOR)
        ceiling = ".".join(str(v) for v in _VALIDATED_BIOSTEAM_CEILING)
        raise RuntimeError(
            "zero-flow writeback guard is unvalidated for qsdsan "
            f"{qsdsan_version!r} / biosteam {biosteam_version!r} (validated: "
            f"qsdsan=={_VALIDATED_QSDSAN_VERSION}, biosteam>={floor},<{ceiling} per "
            "requirements.txt) -- dynamic hydraulics must not run unguarded"
        )

    original = WasteStream._state2flows
    if getattr(original, "__qsdsan_engine_guard__", False):
        # The class is already guarded but the module global was lost to a re-import;
        # re-wrapping would stack guards, so just resynchronise the short-circuit.
        _ZERO_FLOW_WRITEBACK_PATCHED = True
        return

    parameters = list(inspect.signature(original).parameters)
    if parameters != ["self"]:
        raise RuntimeError(
            "zero-flow writeback guard expected WasteStream._state2flows(self) but "
            f"found parameters {parameters} -- the installed qsdsan does not match "
            "the validated implementation and dynamic hydraulics must not run "
            "unguarded"
        )

    def _state2flows(self):
        state = getattr(self, "state", None)
        if state is not None and float(state[-1]) <= 0.0:
            self.empty()
            return None
        return original(self)

    _state2flows.__qsdsan_engine_guard__ = True  # type: ignore[attr-defined]
    _state2flows.__wrapped__ = original  # type: ignore[attr-defined]
    WasteStream._state2flows = _state2flows
    _ZERO_FLOW_WRITEBACK_PATCHED = True


def _safe_process_state(unit: Any, QC: np.ndarray) -> np.ndarray:
    _record_projected_state(unit, QC[:-1], "rate_state")
    state = QC.copy()
    state[:-1] = np.maximum(state[:-1], 0.0)
    return state


def _safe_inlet_state(unit: Any, QC_ins: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    _record_projected_state(unit, QC_ins, "inlet_state")
    return np.maximum(QC_ins[:, -1], 0.0), np.maximum(QC_ins[:, :-1], 0.0)


# Multi-recycle (IR+RAS A2O/MLE) static-tear closure policy.
#
# biosteam's default conditional-Aitken accelerator OVERSHOOTS on two coupled
# mass-balance recycles: the internal nitrate recycle (IR, aerobic -> anoxic)
# feeds the SAME reactors as the return activated sludge (RAS), so the secant
# step throws the tear guess into a region where the map is not contractive. It
# then oscillates to a 30-40% flow residual that trips ``strict_convergence`` ->
# RuntimeError. The failure is segment-by-segment and nondeterministic because
# biosteam re-converges the thermo tear at the start of EVERY ``System.simulate``
# (``dynamic_run`` calls ``self.converge()`` before each integration window), and
# the Aitken secant amplifies floating-point noise differently from each guess.
#
# Pure successive substitution ('fixedpoint') is a guaranteed monotone
# contraction for a physical recycle (loop gain < 1). It converges
# deterministically (bounded, ~100-320 iterations for an internal recycle up to
# ~8Q) with no acceleration to amplify noise. Wegstein's bounded acceleration was
# measured to divide-by-zero on a near-zero tear component and to need an erratic
# 2600+ iterations, so undamped SS is the robust choice. Single-recycle trains
# (e.g. single-RAS MLE/MBR) keep biosteam's Aitken -- one tear already converges
# deterministically and Aitken is faster there.
_MULTI_RECYCLE_METHOD = "fixedpoint"
_MULTI_RECYCLE_MAXITER = 1000

# High-recycle MBR static-tear policy (pumped_flow >= plant feed flow).
#
# For a membrane loop the static (non-reactive) tear map retains particulates
# almost perfectly: solids leave only through the WAS purge and the ~0.1%
# membrane leak, so successive substitution contracts at the PURGE FRACTION of
# the loop -- (Q_was + (1-f_rtn)*Q_perm)/Q_loop. When an SRT controller actuates
# Q_was toward a small value on a ~5x-Q_in loop that is ~1e-4..1e-3 per pass:
# the measured per-pass molar change decays by factor 0.9998, needing ~1e4
# iterations against the 1000-iteration budget, and the guarded stabilize then
# fails the whole run (HydraulicGuardError) even though the DYNAMIC solve it
# protects is perfectly well posed (the seeded unit states, not the static tear
# composition, are the dynamic initial condition). Measured on the seeded
# MLE-MBR train (Q_in 1800, pumped 9000 m3/d): Q_was=1 -> >1e4 iterations;
# Q_was=100 -> 529; even the design point (~22 m3/d, cold) sits at ~100% of
# the 1000-iteration budget.
#
# _conditional_extrapolated_fixed_point keeps pure SS as the base iteration and
# adds a dominant-eigenmode extrapolation: once successive tear deltas show
# STABLE geometric decay (Rayleigh-quotient ratio r agreeing across passes), it
# jumps x -> x + delta*r/(1-r) -- the exact remaining geometric sum for the
# slow retained-solids mode -- and only ADOPTS the jump if a genuine map
# evaluation shows a smaller residual. Convergence is still declared solely by
# biosteam's own criterion inside ``_iter_run_conditional``, and the strict
# maxiter RuntimeError still fires, so the fail-closed guard semantics are
# untouched: a genuinely non-contracting tear (e.g. zero purge) still fails
# loud. Unlike the #19 Aitken/Wegstein failures this never divides
# componentwise (no FP blowups on near-zero components) and never accelerates
# a non-geometric sequence (the r-stability precondition), and a jump that
# does not shrink the residual is discarded, leaving plain SS. Measured on the
# failing seeded MLE-MBR actuation: 10 iterations (1 jump) where plain SS
# needed >1e4.
#
# Scope: only >=2-recycle systems with a CompletelyMixedMBR whose pumped_flow
# is at least the total plant feed flow (the same high-recycle family the
# seeded SRT controller special-cases). The #19-hardened IR+RAS A2O/MLE trains
# (sub-unity MBR ratios included) keep the pinned pure 'fixedpoint' above.
_HIGH_RECYCLE_MBR_METHOD = "extrapolatedfixedpoint"
_HIGH_RECYCLE_MBR_MAXITER = 5000
# Successive-substitution passes that must show stable geometric decay before a
# jump is attempted (lets post-actuation transients and subdominant modes damp).
_EXTRAPOLATION_SETTLE_PASSES = 8
# Two consecutive dominant-ratio estimates must agree within this fraction of
# the remaining contraction (1 - r) before the geometric sum is trusted.
_EXTRAPOLATION_RATIO_STABILITY = 0.25
# Cap on the geometric-sum boost r/(1-r); above this the tear is treated as
# effectively non-contracting and left to plain SS (which then fails loud).
_EXTRAPOLATION_MAX_BOOST = 1e6


def _conditional_extrapolated_fixed_point(f, x):
    """Successive substitution with guarded dominant-eigenmode extrapolation.

    ``f`` is biosteam's ``System._iter_run_conditional``: it returns
    ``(x_new, not_converged)``, evaluates the convergence criterion itself, and
    raises the strict ``RuntimeError`` when the iteration budget is exhausted.
    This solver therefore cannot declare convergence on its own and cannot
    swallow a failure -- it only chooses better iterates. See the policy
    comment above for the physics and the #19 safety analysis.
    """
    x = np.array(x, dtype=float)
    x1, not_converged = f(x)
    delta_prev = x1 - x
    x = x1
    r_prev = None
    since_jump = 0
    while not_converged:
        x1, not_converged = f(x)
        if not not_converged:
            return x1
        delta = x1 - x
        den = float(np.dot(delta_prev, delta_prev))
        r = float(np.dot(delta, delta_prev)) / den if den > 0.0 else 0.0
        since_jump += 1
        jump_adopted = False
        if (
            since_jump >= _EXTRAPOLATION_SETTLE_PASSES
            and r_prev is not None
            and 0.0 < r < 1.0
            and 0.0 < r_prev < 1.0
            and abs(r - r_prev) <= _EXTRAPOLATION_RATIO_STABILITY * (1.0 - r)
            and r / (1.0 - r) <= _EXTRAPOLATION_MAX_BOOST
        ):
            x_jump = x1 + delta * (r / (1.0 - r))
            # Match the projection biosteam itself applies to every tear guess.
            x_jump[x_jump < 0.0] = 0.0
            x2, not_converged = f(x_jump)
            if not not_converged:
                return x2
            delta2 = x2 - x_jump
            # A fresh settle window either way: a rejected jump means the delta
            # sequence is not geometric here, so re-attempting every pass would
            # only burn the strict iteration budget at two evaluations per pass.
            since_jump = 0
            if float(np.dot(delta2, delta2)) < float(np.dot(delta, delta)):
                x = x2
                delta_prev = delta2
                r_prev = None
                jump_adopted = True
        if not jump_adopted:
            x = x1
            delta_prev = delta
            r_prev = r
    return x


def _register_extrapolated_fixed_point() -> bool:
    """Idempotently register the extrapolated solver with biosteam."""
    try:
        from biosteam import System
    except Exception:  # pragma: no cover - biosteam always present in practice
        logger.debug("biosteam unavailable; extrapolated tear solver not registered")
        return False
    if _HIGH_RECYCLE_MBR_METHOD not in System.available_methods:
        System.register_method(
            _HIGH_RECYCLE_MBR_METHOD,
            _conditional_extrapolated_fixed_point,
            conditional=True,
        )
    return True


def _has_high_recycle_mbr_tear(system: Any) -> bool:
    """True when a CompletelyMixedMBR pumps at least the total plant feed flow.

    This is the static-tear analog of the seeded SRT controller's
    high-recycle predicate: at pumped_flow >= sum(feeds) the non-reactive tear
    retains particulates at >=50% per pass even before the RAS split, and the
    purge-fraction contraction analysis in the policy comment above applies.
    """
    feed_flow = 0.0
    try:
        feed_flow = sum(_flow_m3_d(stream) for stream in getattr(system, "feeds", ()) or ())
    except Exception:
        feed_flow = 0.0
    if not np.isfinite(feed_flow) or feed_flow <= 0.0:
        return False
    for unit in getattr(system, "units", []) or []:
        if type(unit).__name__ != "CompletelyMixedMBR":
            continue
        try:
            q_pumped = float(getattr(unit, "pumped_flow", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if q_pumped >= feed_flow:
            return True
    return False


def _recycle_count(system: Any) -> int:
    try:
        return len(system.get_all_recycles() or [])
    except Exception:
        recycle = getattr(system, "recycle", None)
        if recycle is None:
            return 0
        if isinstance(recycle, (list, tuple, set)):
            return len([r for r in recycle if r is not None])
        return 1


def configure_recycle_solver(system: Any) -> bool:
    """Pin a robust, deterministic static-tear solver for >=2-recycle systems.

    biosteam's ``System.converge`` reads ``converge_method`` / ``maxiter`` off the
    system, and ``dynamic_run`` re-converges every segment, so configuring the
    system here fixes BOTH the one-shot :func:`stabilize_dynamic_hydraulics`
    converge and the per-segment re-converge. Returns True when the multi-recycle
    policy was applied. No-op (returns False) for 0/1-recycle systems, which keep
    biosteam's default accelerator.

    Multi-recycle systems get pure successive substitution (the #19 IR+RAS
    determinism fix). High-recycle MBR loops additionally get the
    dominant-eigenmode extrapolation on top of SS, because their static tear
    contracts at the WAS purge fraction (~1e-4 per pass) and plain SS cannot
    close it inside any sane iteration budget -- see the policy comment at
    :data:`_HIGH_RECYCLE_MBR_METHOD`.
    """
    if _recycle_count(system) < 2:
        return False
    method = _MULTI_RECYCLE_METHOD
    maxiter = _MULTI_RECYCLE_MAXITER
    if _has_high_recycle_mbr_tear(system) and _register_extrapolated_fixed_point():
        method = _HIGH_RECYCLE_MBR_METHOD
        maxiter = _HIGH_RECYCLE_MBR_MAXITER
    try:
        system.converge_method = method
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("could not set multi-recycle converge method: %s", exc)
        return False
    # Only raise maxiter (never lower a caller's higher budget). Undamped SS on a
    # strongly-coupled IR+RAS tear needs more than biosteam's default 200.
    try:
        if int(getattr(system, "maxiter", 0) or 0) < maxiter:
            system.maxiter = maxiter
    except Exception:  # pragma: no cover - defensive
        pass
    return True


def hydraulics_are_stabilized(system: Any) -> bool:
    """True when the frozen-hydraulics guard is already installed on this system.

    ``restore_dynamic_checkpoint`` calls :func:`stabilize_dynamic_hydraulics` as
    the single owner of the resume-path stabilization (it converges the recycle
    ONCE and overlays the exact captured frozen flows). The sampled-data resume
    driver reads this to AVOID a second converge that would re-solve the recycle
    from the restored fixed point and drift it (~0.4%), breaking bit-identical
    resume (#17). Detect the guard by the per-unit frozen-outflow attribute the
    stabilization installs on every dynamic CSTR/MBR.
    """
    for unit in getattr(system, "units", []):
        if not getattr(unit, "isdynamic", False):
            continue
        if type(unit).__name__ not in {"CSTR", "CompletelyMixedMBR"}:
            continue
        if hasattr(unit, "_codex_fixed_hydraulic_outflows_m3_d"):
            return True
    return False


def _component_index(cmps: Any, component_id: str | None) -> int | None:
    if not component_id:
        return None
    try:
        return int(cmps.index(component_id))
    except Exception:
        ids = getattr(cmps, "IDs", ()) or ()
        try:
            return list(ids).index(component_id)
        except ValueError:
            return None


def _dynamic_kla(unit: Any) -> float | None:
    value = getattr(unit, "_codex_dynamic_kLa_d", None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    return max(0.0, value)


def _dynamic_dosat(unit: Any, aer: Any) -> float:
    value = getattr(unit, "_codex_do_control_DO_sat_mg_L", None)
    if value is None:
        value = getattr(aer, "DOsat", 8.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 8.0
    return value if np.isfinite(value) else 8.0


def _process_aeration_terms(aer: Any) -> tuple[np.ndarray, Any] | None:
    """Return QSDsan Process aeration terms without importing QSDsan."""
    stoichiometry = getattr(aer, "_stoichiometry", None)
    rate_function = getattr(aer, "rate_function", None)
    if stoichiometry is None or rate_function is None:
        return None
    try:
        return np.asarray(stoichiometry, dtype=float), rate_function
    except Exception:
        return None


def _gas_stripping_terms(unit: Any) -> tuple | None:
    'Resolve speciated gas-stripping terms for the guarded ODE.'
    if not getattr(unit, "gas_stripping", False):
        return None
    uid = getattr(unit, "ID", "?")
    gas_ids = getattr(unit, "gas_IDs", None)
    if not gas_ids:
        raise GasStrippingPhysicsError(
            f"unit {uid} has gas_stripping=True but its process model declares no "
            "gas_IDs, so there is nothing to strip. This used to return silently, "
            "leaving a unit that reports stripping enabled and transfers nothing."
        )
    cmps = unit.components
    try:
        idx = np.array([cmps.index(i) for i in gas_ids], dtype=int)
        k_henry = np.asarray(unit.K_Henry, dtype=float)
        p_gas = np.asarray(unit.p_gas_atm, dtype=float)
        d_gas = np.asarray(unit.D_gas, dtype=float)
        kmin = np.asarray(unit.stripping_kLa_min, dtype=float)
    except Exception as exc:
        raise GasStrippingPhysicsError(
            f"unit {uid} has gas_stripping=True but incomplete gas parameters "
            f"({exc}). Stripping used to be skipped silently here, which reports a "
            "hard-aerated reactor that never loses CO2 -- the inorganic carbon the "
            "ammonia oxidisers depend on is then overstated for the whole run."
        ) from exc

    sat = k_henry * p_gas
    d_o2 = float(getattr(unit, "_D_O2", 2.10e-9) or 2.10e-9)

    model = getattr(unit, "_model", None)
    volatile_fn = getattr(model, "volatile_fractions", None)
    speciating = tuple(
        g for g in getattr(model, "_SPECIATING_GASES", _SPECIATING_GASES_DEFAULT)
        if g in gas_ids
    )
    if speciating and volatile_fn is None:
        raise GasStrippingPhysicsError(
            f"unit {uid} strips {list(speciating)}, which are TOTALS over acid-base "
            f"pairs, but its model ({type(model).__name__}) exposes no "
            "volatile_fractions(). The driving force would fall back to the total "
            "state -- BUGS #2, over-stripping carbon 2-6x at pH 5.9-6.9 and ~15x at "
            "pH 7.5. Use a model that speciates (models.masm2d.ModifiedASM2d) or "
            "drop these species from gas_IDs."
        )

    h_cc = getattr(model, "H_cc", None)
    aer = getattr(unit, "aeration", None)
    q_air = float(getattr(aer, "Q_air", 0.0) or 0.0) if aer is not None else 0.0
    vol = float(getattr(aer, "V", 0.0) or getattr(unit, "V_max", 0.0) or 0.0)
    if h_cc is None or q_air <= 0 or vol <= 0:
        raise GasStrippingPhysicsError(
            f'unit {uid}: cannot bound stripping by gas carrying capacity (H_cc={('present' if h_cc is not None else 'MISSING')}, Q_air={q_air}, V={vol} Physical consistency failed; check the model inputs and diagnostics.'
        )
    h_cc_arr = np.asarray(h_cc, dtype=float)

    def _kla_strip(kla_o2: float) -> np.ndarray:
        'Stripping kLa per gas, bounded by what the air can physically carry.\n\n        Two-film theory with C* fixed at the ambient partial pressure assumes the gas\n        phase is never enriched by what it picks up. That holds for a sparingly soluble\n        gas -- an air bubble is 21% oxygen and leaves barely changed. It fails badly for\n        a soluble one: an ammonia bubble reaches Henry equilibrium within centimetres of\n        rise, after which there is no driving force left however large kLa is. Past that\n        point the flux is set by how much gas is flowing, not by how fast it transfers.\n\n        Modelling the bubble as approaching equilibrium exponentially over its rise,\n\n            x   = kLa.V / (Q_air.H_cc)          contact time in units of saturation\n            eta = (1 - exp(-x)) / x             fraction of the two-film flux realised'
        kla = np.maximum(kla_o2 * np.sqrt(d_gas / d_o2), kmin)
        if h_cc_arr is None:
            return kla
        x = kla * vol / (q_air * h_cc_arr)
        # expm1 keeps the small-x limit accurate, where eta -> 1 and the naive form
        # is 0/0.
        return kla * np.where(x > 1e-8, -np.expm1(-x) / np.where(x > 0, x, 1.0), 1.0)

    return idx, _kla_strip, sat, volatile_fn


def _aeration_duty_fn(unit: Any) -> Any:
    """Return a callable ``t_days -> multiplier`` for intermittent aeration.

    Returns ``None`` when the unit has no duty cycle, so the hot path pays
    nothing for the common continuous case. The duty is evaluated on ``t``
    inside the ODE rather than at controller sample boundaries, so an aerate/rest
    cycle is not quantised to the segment cadence -- a 6-on/4-off minute cycle
    would otherwise need 240 segments per simulated day to resolve.
    """
    from qsdsan_engine_mcp.core.aeration_spec import DUTY_ATTR, duty_at

    duty = getattr(unit, DUTY_ATTR, None)
    if not duty:
        return None
    return lambda t: duty_at(duty, t)


def _compile_guarded_cstr_ode(self: Any) -> None:
    cmps = self.components
    aer = getattr(self, "_aeration", None)
    model = getattr(self, "_model", None)
    rate = (lambda state_arr: np.zeros(cmps.size)) if model is None else model.production_rates_eval
    update_dstate = self._update_dstate
    dstate = self._dstate
    V = self._V_max
    hasexo = bool(len(getattr(self, "_exovars", ()) or ()))
    f_exovars = self.eval_exo_dynamic_vars

    control_do_id = getattr(self, "_codex_do_control_DO_ID", getattr(self, "_DO_ID", None))
    control_do_index = _component_index(cmps, control_do_id) if _dynamic_kla(self) is not None else None
    ideal_do_index = (
        _component_index(cmps, getattr(self, "_DO_ID", None))
        if control_do_index is None and isinstance(aer, (float, int))
        else None
    )
    process_aeration = None if control_do_index is not None else _process_aeration_terms(aer)
    duty_fn = _aeration_duty_fn(self) if process_aeration is not None else None
    strip_terms = _gas_stripping_terms(self)

    def dy_dt(t, QC_ins, QC, dQC_ins):
        safe = _safe_process_state(self, QC)
        if ideal_do_index is not None:
            QC[ideal_do_index] = aer
            safe[ideal_do_index] = aer
        q_ins, c_ins = _safe_inlet_state(self, QC_ins)
        dstate[-1] = 0.0
        dstate[:-1] = (q_ins @ c_ins - q_ins.sum() * safe[:-1]) / V
        rate_state = np.append(safe, f_exovars(t)) if hasexo else safe
        dstate[:-1] += rate(rate_state)
        if process_aeration is not None:
            aer_stoi, aer_frho = process_aeration
            aer_term = aer_stoi * aer_frho(rate_state)
            if duty_fn is not None:
                aer_term = aer_term * duty_fn(t)
            dstate[:-1] += aer_term
        if control_do_index is not None:
            kla = _dynamic_kla(self) or 0.0
            dstate[control_do_index] += kla * (_dynamic_dosat(self, aer) - safe[control_do_index])
        if strip_terms is not None:
            g_idx, kla_strip_fn, g_sat, vol_fn = strip_terms
            kla_o2 = _dynamic_kla(self)
            if kla_o2 is None:
                kla_o2 = float(getattr(aer, "KLa", 0.0) or 0.0) if not isinstance(
                    aer, (int, float)) else 0.0
            # Stripping is driven by the SAME air. Under an intermittent duty the
            # blower is off for part of the cycle, and gas transfer must stop with
            # it -- otherwise CO2 strips at full rate through the rest phase and the
            # inorganic-carbon balance is wrong in exactly the mode designed to
            # protect it.
            if duty_fn is not None:
                kla_o2 = kla_o2 * duty_fn(t)
            if kla_o2 > 0:
                # Drive on the VOLATILE fraction, not the total state: S_IC and S_NH4 are
                # sums over acid-base pairs and only the neutral member leaves.
                frac = vol_fn(safe) if vol_fn is not None else 1.0
                dstate[g_idx] -= kla_strip_fn(kla_o2) * (frac * safe[g_idx] - g_sat)
        if ideal_do_index is not None:
            dstate[ideal_do_index] = 0.0
        update_dstate()

    self._ODE = dy_dt


def _compile_guarded_mbr_ode(self: Any) -> None:
    cmps = self.components
    aer = getattr(self, "_aeration", None)
    model = getattr(self, "_model", None)
    rate = (lambda state_arr: np.zeros(cmps.size)) if model is None else model.production_rates_eval
    update_dstate = self._update_dstate
    dstate = self._dstate
    V = self._V_max
    Qp = self.pumped_flow
    f_rtn = self.solids_capture_rate
    xarr = cmps.x
    hasexo = bool(len(getattr(self, "_exovars", ()) or ()))
    f_exovars = self.eval_exo_dynamic_vars

    control_do_id = getattr(self, "_codex_do_control_DO_ID", getattr(self, "_DO_ID", None))
    control_do_index = _component_index(cmps, control_do_id) if _dynamic_kla(self) is not None else None
    ideal_do_index = (
        _component_index(cmps, getattr(self, "_DO_ID", None))
        if control_do_index is None and isinstance(aer, (float, int))
        else None
    )
    process_aeration = None if control_do_index is not None else _process_aeration_terms(aer)
    duty_fn = _aeration_duty_fn(self) if process_aeration is not None else None
    strip_terms = _gas_stripping_terms(self)

    # Filling or draining reactor. `dV_dt` defaults to zero, so a fixed-volume unit
    # integrates exactly as before; setting it makes the volume follow the level.
    #
    # The full variable-volume CSTR balance is d(V.C)/dt = Q_in.C_in - Q_out.C_out, i.e.
    # dC/dt = [Q_in.C_in - Q_out.C_out - C.dV/dt] / V(t), closed by the water balance
    # Q_out = Q_in - dV/dt. For SOLUBLES the two dV/dt terms cancel and only the growing
    # V is felt; for RETAINED PARTICULATES they do not, and what survives is dilution by
    # the rising level -- about 4.8% across this window, which is not negligible against
    # a solids trajectory the twin is identified on.
    dV_dt = float(getattr(self, "_codex_dV_dt_m3_d", 0.0) or 0.0)

    def dy_dt(t, QC_ins, QC, dQC_ins):
        safe = _safe_process_state(self, QC)
        if ideal_do_index is not None:
            QC[ideal_do_index] = aer
            safe[ideal_do_index] = aer
        q_ins, c_ins = _safe_inlet_state(self, QC_ins)
        q_total = q_ins.sum()
        V_t = V + dV_dt * t
        q_out = q_total - dV_dt
        removal = q_out * (1.0 - xarr) + (Qp + (q_out - Qp) * (1.0 - f_rtn)) * xarr
        dstate[-1] = 0.0
        dstate[:-1] = (q_ins @ c_ins - removal * safe[:-1] - safe[:-1] * dV_dt) / V_t
        rate_state = np.append(safe, f_exovars(t)) if hasexo else safe
        dstate[:-1] += rate(rate_state)
        if process_aeration is not None:
            aer_stoi, aer_frho = process_aeration
            aer_term = aer_stoi * aer_frho(rate_state)
            if duty_fn is not None:
                aer_term = aer_term * duty_fn(t)
            dstate[:-1] += aer_term
        if control_do_index is not None:
            kla = _dynamic_kla(self) or 0.0
            dstate[control_do_index] += kla * (_dynamic_dosat(self, aer) - safe[control_do_index])
        if strip_terms is not None:
            g_idx, kla_strip_fn, g_sat, vol_fn = strip_terms
            kla_o2 = _dynamic_kla(self)
            if kla_o2 is None:
                kla_o2 = float(getattr(aer, "KLa", 0.0) or 0.0) if not isinstance(
                    aer, (int, float)) else 0.0
            # Stripping is driven by the SAME air. Under an intermittent duty the
            # blower is off for part of the cycle, and gas transfer must stop with
            # it -- otherwise CO2 strips at full rate through the rest phase and the
            # inorganic-carbon balance is wrong in exactly the mode designed to
            # protect it.
            if duty_fn is not None:
                kla_o2 = kla_o2 * duty_fn(t)
            if kla_o2 > 0:
                # Drive on the VOLATILE fraction, not the total state: S_IC and S_NH4 are
                # sums over acid-base pairs and only the neutral member leaves.
                frac = vol_fn(safe) if vol_fn is not None else 1.0
                dstate[g_idx] -= kla_strip_fn(kla_o2) * (frac * safe[g_idx] - g_sat)
        if ideal_do_index is not None:
            dstate[ideal_do_index] = 0.0
        update_dstate()

    self._ODE = dy_dt


class HydraulicGuardError(RuntimeError):
    """The guarded hydraulics could not be installed, so the run must not proceed.

    Proceeding without the guard is not a degraded run, it is a DIFFERENT MODEL. Stock
    QSDsan re-walks the process path once per ODE evaluation, so high-RAS recycles
    multiply hydraulic states instead of re-solving; and the guarded overlay is also
    where the carrying-capacity-bounded gas stripping lives. A silent fallback therefore
    yields finite, plausible-looking output computed with recycle physics we know to be
    wrong and stripping we know to be ~6,700x over the thermodynamic bound for ammonia --
    with no fatal status anywhere to show for it.
    """


class GasStrippingPhysicsError(HydraulicGuardError):
    """``gas_stripping=True`` was requested where the correct physics is unavailable.

    Subclasses :class:`HydraulicGuardError` because it is the same failure in a
    narrower place: the run would proceed on stripping physics we know to be wrong.
    """


def install_speciated_stripping_guard() -> bool:
    """Make total-state gas stripping UNREACHABLE, not merely avoidable (BUGS #2).

    QSDsan's compiled ODE applies the stripping driving force to the named state::

        if gstrip: _dstate[gas_idx] -= kLa_stripping * (QC[gas_idx] - S_gas_air)

    For ``S_N2`` that is right -- the state is dissolved N2. For ``S_IC`` and ``S_NH4``
    it is wrong: those are TOTALS over CO2/HCO3-/CO3-- and NH3/NH4+, and only the
    neutral member volatilises. Using the total over-strips carbon by 2-6x at
    pH 5.9-6.9 and ~15x at pH 7.5, draining the inorganic carbon in minutes while the
    biology carries on nitrifying against carbon that is gone.

    The correct speciated term lives in the guarded ODEs, so until now the right
    physics was a property of the CODE PATH: anything that skipped
    :func:`stabilize_dynamic_hydraulics` -- manual session assembly via
    ``create_unit`` / ``create_flowsheet_session``, a bare ``system.simulate()``, or an
    early return -- silently got the wrong one.

    This patches ``_compile_ODE`` on the dynamic reactor CLASSES so the stock
    implementation refuses to compile a stripping ODE for a model that speciates.
    Guarded units are unaffected: :func:`stabilize_dynamic_hydraulics` installs its
    ``_compile_ODE`` as an INSTANCE attribute, which shadows the class, so this wrapper
    only ever fires on the stock path it exists to close.

    Idempotent, and a no-op when qsdsan is unavailable. Returns True if the guard is
    installed.
    """
    try:
        from qsdsan.unit_operations import CSTR, CompletelyMixedMBR
    except Exception:  # pragma: no cover - qsdsan always present in practice
        logger.debug("qsdsan reactor classes unavailable; speciation guard not installed")
        return False

    for cls in {CSTR, CompletelyMixedMBR}:
        # `in cls.__dict__` deliberately, not hasattr: CompletelyMixedMBR inherits
        # CSTR's _compile_ODE, and wrapping an already-wrapped inherited attribute
        # would double-check (harmless) or, worse, wrap the subclass's own override
        # twice on a second call.
        existing = cls.__dict__.get("_compile_ODE")
        if existing is None or getattr(existing, "_codex_speciation_guard", False):
            continue

        def _guarded(self, _stock=existing):
            _assert_speciated_stripping_available(self)
            return _stock(self)

        _guarded._codex_speciation_guard = True
        _guarded.__name__ = getattr(existing, "__name__", "_compile_ODE")
        _guarded.__doc__ = getattr(existing, "__doc__", None)
        cls._compile_ODE = _guarded

    return True


def _assert_speciated_stripping_available(unit: Any) -> None:
    """Refuse the stock stripping ODE for a model whose gas states are totals."""
    if not getattr(unit, "gas_stripping", False):
        return
    if getattr(unit, "_codex_hydraulic_guard", None):
        return  # the guarded ODE is installed; it strips on the volatile fraction
    model = getattr(unit, "_model", None)
    gas_ids = tuple(getattr(unit, "gas_IDs", ()) or ())
    speciating = tuple(
        g for g in getattr(model, "_SPECIATING_GASES", _SPECIATING_GASES_DEFAULT)
        if g in gas_ids
    )
    if not speciating:
        return
    raise GasStrippingPhysicsError(
        f"unit {getattr(unit, 'ID', '?')} has gas_stripping=True and strips "
        f"{list(speciating)} -- totals over acid-base pairs, of which only the neutral "
        "member volatilises -- but it is about to compile the STOCK QSDsan ODE, which "
        "applies the driving force to the total state. That over-strips carbon 2-6x at "
        "pH 5.9-6.9 and ~15x at pH 7.5, and the run reports no failure. Call "
        "utils.dynamic_hydraulics.stabilize_dynamic_hydraulics(system) before "
        "simulating so the speciated ODE is installed, or set gas_stripping=False."
    )


def stabilize_dynamic_hydraulics(system: Any, *, require_guard: bool = True) -> dict:
    """Freeze converged liquid flows during dynamic concentration integration.

    QSDsan's dynamic CSTR/MBR implementations update recycle flows by walking
    the process path once per ODE evaluation. High-RAS MBR and MLE recycle loops
    therefore multiply hydraulic states instead of re-solving the static recycle
    equations. For constant-flow ASM simulations, hydraulics should be solved by
    the static system convergence and held fixed while concentrations evolve.
    """
    # The record lives on the UNITS, not on the system: qsdsan's System defines
    # __slots__ and is not weakref-able, so neither an attribute nor a WeakKeyDictionary
    # is available. Marking each guarded unit is better anyway -- the evidence sits on
    # the object that was actually modified.
    record: dict = {"converged": False, "guarded": [], "skipped": [], "eligible": []}

    # Close the stock-ODE stripping hole for every unit on this system, including any
    # this call ends up SKIPPING. A unit that is skipped here is precisely a unit that
    # would otherwise reach stock QSDsan's total-state stripping term.
    install_speciated_stripping_guard()

    if not getattr(system, "isdynamic", False):
        record["note"] = "system is not dynamic; nothing to guard"
        return record

    # Pin the robust multi-recycle tear solver BEFORE converging. This persists on
    # the system so biosteam's per-segment ``dynamic_run`` re-converge uses it too
    # (the IR+RAS A2O/MLE convergence fix -- see configure_recycle_solver / #19).
    configure_recycle_solver(system)

    try:
        system.converge()
    except Exception as exc:
        record["error"] = f"static convergence failed: {exc}"
        if require_guard:
            raise HydraulicGuardError(
                "static hydraulic convergence failed before the dynamic solve, so the "
                "flows this guard exists to FREEZE were never solved. Continuing would "
                "run stock QSDsan recycle physics and unbounded gas stripping and "
                f"report no failure. Original error: {exc}"
            ) from exc
        logger.warning("Static hydraulic convergence failed, UNGUARDED: %s", exc)
        return record
    record["converged"] = True

    for unit in getattr(system, "units", []):
        if not getattr(unit, "isdynamic", False):
            continue

        unit_type = type(unit).__name__
        if unit_type not in {"CSTR", "CompletelyMixedMBR"}:
            continue

        record["eligible"].append(unit.ID)
        fixed_inflow = sum(_flow_m3_d(stream) for stream in getattr(unit, "ins", ()) or ())
        fixed_outflows = [_flow_m3_d(stream) for stream in getattr(unit, "outs", ()) or ()]
        if not fixed_outflows or not _finite_nonnegative([fixed_inflow, *fixed_outflows]):
            why = f"non-finite static flows in={fixed_inflow!r} out={fixed_outflows!r}"
            record["skipped"].append({"unit": unit.ID, "why": why})
            if require_guard:
                raise HydraulicGuardError(
                    f"cannot guard {unit.ID}: {why}. This unit is a dynamic "
                    f"{unit_type} and would otherwise integrate with stock recycle "
                    "physics and unbounded stripping."
                )
            logger.warning("Skipping hydraulic guard for %s: %s", unit.ID, why)
            continue

        unit._codex_fixed_hydraulic_inflow_m3_d = fixed_inflow
        unit._codex_fixed_hydraulic_outflows_m3_d = fixed_outflows

        if unit_type == "CompletelyMixedMBR":
            if not hasattr(unit, "_codex_original_update_state"):
                unit._codex_original_update_state = unit._update_state
                unit._codex_original_update_dstate = unit._update_dstate
                unit._codex_original_compile_ODE = unit._compile_ODE
                unit._update_state = MethodType(_guarded_mbr_update_state, unit)
                unit._update_dstate = MethodType(_guarded_mbr_update_dstate, unit)
                unit._compile_ODE = MethodType(_compile_guarded_mbr_ode, unit)
        else:
            if not hasattr(unit, "_codex_original_update_state"):
                unit._codex_original_update_state = unit._update_state
                unit._codex_original_update_dstate = unit._update_dstate
                unit._codex_original_compile_ODE = unit._compile_ODE
                unit._update_state = MethodType(_guarded_cstr_update_state, unit)
                unit._update_dstate = MethodType(_guarded_cstr_update_dstate, unit)
                unit._compile_ODE = MethodType(_compile_guarded_cstr_ode, unit)

        # A structurally zero outlet (no wasting -> pumped_flow = 0) would otherwise kill
        # the run in the final state-writeback, after the integration has fully succeeded.
        if any(q == 0.0 for q in fixed_outflows):
            _install_zero_flow_writeback_guard()

        record["guarded"].append(unit.ID)
        unit._codex_hydraulic_guard = {"guarded": True, "converged": True,
                                       "inflow_m3_d": fixed_inflow,
                                       "outflows_m3_d": list(fixed_outflows)}
        state = getattr(unit, "_state", None)
        if state is not None:
            state[-1] = fixed_inflow

    return record


def assert_hydraulics_guarded(system: Any) -> dict:
    """Positively confirm the guard is installed on every eligible unit.

    A caller that merely avoided an exception has established nothing: the old failure
    mode was a quiet ``return``. This asserts the POSITIVE record instead, so a run can
    say which units it guarded rather than that nothing complained.
    """
    eligible, guarded = [], []
    for unit in getattr(system, "units", []) or ():
        if not getattr(unit, "isdynamic", False):
            continue
        if type(unit).__name__ not in {"CSTR", "CompletelyMixedMBR"}:
            continue
        eligible.append(unit.ID)
        if getattr(unit, "_codex_hydraulic_guard", None):
            guarded.append(unit.ID)
    missing = sorted(set(eligible) - set(guarded))
    if missing:
        raise HydraulicGuardError(
            f"dynamic units running UNGUARDED: {missing}. Either "
            "stabilize_dynamic_hydraulics was never called, or it returned early -- "
            "which it used to do silently. Stock recycle physics and unbounded gas "
            "stripping would be in force with nothing above debug level to say so."
        )
    return {"eligible": eligible, "guarded": guarded}


def prefers_explicit_recycle_solver(system: Any) -> bool:
    """Return True when dense-output BDF is unsafe for guarded MBR recycles."""
    for unit in getattr(system, "units", []):
        if type(unit).__name__ != "CompletelyMixedMBR":
            continue
        q_pumped = float(getattr(unit, "pumped_flow", 0.0) or 0.0)
        q_in = float(
            getattr(unit, "_codex_fixed_hydraulic_inflow_m3_d", 0.0)
            or sum(_flow_m3_d(stream) for stream in getattr(unit, "ins", ()) or ())
        )
        if q_in > 0.0 and q_pumped / q_in > 0.2:
            return True
    return False
