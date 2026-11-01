'Declarative aeration for aerobic reactors, resolved into QSDsan Processes.'

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

#: Keys accepted in an aeration spec dict. Anything else is an error rather than
#: a silent no-op -- a misspelled ``alpha`` must not leave the default in place.
AERATION_SPEC_KEYS = frozenset(
    {
        "mode",
        # driving quantity (exactly one of these three)
        "Q_air_scfm",
        "Q_air_std_m3_d",
        "KLa",
        "KLa_20",
        # field-condition DO saturation, if pinning it directly
        "DOsat",
        # gas basis
        "air_basis",
        # ASCE correction chain
        "alpha",
        "beta",
        "F",
        "theta",
        "SOTE",
        "d_submergence",
        "fine_pore",
        "elevation_m",
        "T_air_K",
        "T_water_K",
        "DOsat_s20",
        "CF1",
        # component
        "DO_ID",
        # intermittent duty cycle
        "period_min",
        "on_min",
        "on_fraction",
        "phase_min",
        "ramp_min",
    }
)

VALID_MODES = frozenset({"fixed_kla", "fixed_airflow", "do_control", "intermittent"})

#: Attribute stamped on a unit to carry an intermittent duty cycle into the ODE.
DUTY_ATTR = "_codex_aeration_duty"

#: 1 scfm at the US blower basis, in standard m3/d. Fallback only; the typed
#: boundary in engineering_utils.units is preferred and used when importable.
_SCFM_TO_STD_M3_D_FALLBACK = 0.3048**3 * 60.0 * 24.0


class AerationSpecError(ValueError):
    """Raised for a malformed aeration spec. Never downgraded to a warning."""


def is_aeration_spec(value: Any) -> bool:
    """True if ``value`` looks like a declarative aeration spec rather than a
    DO setpoint float or an already-built Process."""
    return isinstance(value, Mapping) and bool(value)


def _scfm_to_std_m3_d(scfm: float, air_basis: str | None) -> float:
    """Convert scfm using the typed gas-volume boundary.

    "scfm" is a unit plus a convention, and the conventions disagree by up to
    7.6% in oxygen content. The boundary refuses to guess; so do we.
    """
    try:
        from qsdsan_engine_mcp.core.engineering_utils_bridge import ensure_engineering_utils_importable

        ensure_engineering_utils_importable()
        from puran_mcp_support.engineering.units import GAS_VOLUME_BASES, SCFM_BLOWER, scfm_to_std_m3_per_day

        basis = SCFM_BLOWER
        if air_basis:
            if air_basis not in GAS_VOLUME_BASES:
                raise AerationSpecError(
                    f"unknown air_basis {air_basis!r}; expected one of "
                    f"{sorted(GAS_VOLUME_BASES)}"
                )
            basis = GAS_VOLUME_BASES[air_basis]
        return scfm_to_std_m3_per_day(float(scfm), basis)
    except AerationSpecError:
        raise
    except Exception:  # pragma: no cover - only if engineering_utils is absent
        logger.warning(
            "engineering_utils.units unavailable; converting scfm geometrically and "
            "assuming the US blower basis (68 F, 14.696 psia, 36%% RH)."
        )
        return float(scfm) * _SCFM_TO_STD_M3_D_FALLBACK


def normalize_aeration_spec(
    spec: Mapping[str, Any],
    *,
    V_m3: float,
    T_water_K: float | None = None,
    DO_ID: str = "S_O2",
) -> dict[str, Any]:
    """Validate a spec and resolve it to explicit QSDsan constructor arguments.

    Enforces four things QSDsan does not, each of which fails obscurely rather
    than loudly if left to the library:

    1. Exactly one driving quantity. With none, ``DiffusedAeration.KLa_20``
       returns ``None`` and the first rate evaluation raises ``TypeError``.
    2. ``KLa`` of exactly zero is rejected. It is falsy, so the getter falls
       through to ``self._KLa`` before assignment and raises ``AttributeError``
       -- a blower-off command crashes the run. Use ``intermittent`` mode.
    3. Unknown keys are an error, so a misspelling cannot silently leave a
       default in force.
    4. ``DOsat`` and ``DOsat_s20`` may not both be given; the ``DOsat`` setter
       silently back-solves and overwrites ``DOsat_s20``.
    """
    unknown = set(spec) - AERATION_SPEC_KEYS
    if unknown:
        raise AerationSpecError(
            f"unknown aeration spec key(s): {sorted(unknown)}. "
            f"Accepted: {sorted(AERATION_SPEC_KEYS)}"
        )

    mode = str(spec.get("mode", "fixed_airflow")).lower()
    if mode not in VALID_MODES:
        raise AerationSpecError(
            f"aeration mode {mode!r} is not one of {sorted(VALID_MODES)}"
        )

    if float(V_m3) <= 0:
        raise AerationSpecError(f"reactor volume must be positive, got {V_m3}")

    drivers = [k for k in ("Q_air_scfm", "Q_air_std_m3_d", "KLa", "KLa_20") if k in spec]
    if len(drivers) != 1:
        raise AerationSpecError(
            f"aeration spec needs exactly one of Q_air_scfm / Q_air_std_m3_d / KLa / "
            f"KLa_20; got {drivers or 'none'}. Without one, KLa_20 resolves to None and "
            f"the first rate evaluation raises TypeError deep inside QSDsan."
        )

    if "DOsat" in spec and "DOsat_s20" in spec:
        raise AerationSpecError(
            "give DOsat or DOsat_s20, not both: QSDsan's DOsat setter back-solves and "
            "silently overwrites DOsat_s20, so the pair is contradictory."
        )

    kwargs: dict[str, Any] = {"DO_ID": DO_ID, "V": float(V_m3)}

    # QSDsan's Q_air is documented at FIELD conditions and `_calc_KLa_20` divides by
    # the air-density factor to recover the standard flow that SOTE and CF1 apply to.
    # Handing it a standard flow therefore under-reports SOTR by that factor. It is
    # unity at 20 C and sea level -- which is why this hides -- but not otherwise.
    _T_air = float(spec.get("T_air_K", 293.15))
    _elev = float(spec.get("elevation_m", 0.0))

    def _std_to_field(std_m3_d: float) -> float:
        try:
            from qsdsan_engine_mcp.core.engineering_utils_bridge import ensure_engineering_utils_importable

            ensure_engineering_utils_importable()
            from puran_mcp_support.engineering.units import GAS_VOLUME_BASES, SCFM_BLOWER, site_pressure_pa, std_to_actual_m3_per_day

            # Use the basis the caller declared. Hardcoding SCFM_BLOWER silently
            # converts an ISO-normal flow against the wrong reference temperature --
            # a ~7% error that the typed boundary exists to prevent.
            basis = GAS_VOLUME_BASES.get(spec.get("air_basis"), SCFM_BLOWER)
            return std_to_actual_m3_per_day(
                std_m3_d, basis, _T_air, site_pressure_pa(_elev)
            )
        except Exception:  # pragma: no cover - only if engineering_utils is absent
            import math

            factor = (_T_air / 293.15) * math.exp(
                9.81 * 28.964 * _elev / (8.314 * _T_air * 1e3)
            )
            return std_m3_d * factor

    driver = drivers[0]
    if driver in ("Q_air_scfm", "Q_air_std_m3_d"):
        # Zero air flow is falsy inside QSDsan exactly as KLa=0 is: KLa_20 resolves to
        # None and the first evaluation dies with AttributeError on `_KLa`. Guard it
        # here rather than letting a blower-off command crash mid-integration.
        if float(spec[driver]) <= 0:
            raise AerationSpecError(
                f"{driver} must be > 0. QSDsan cannot represent a stopped blower as a "
                f"zero air flow -- the derived KLa is falsy and the rate evaluation "
                f"raises. To model a blower that stops, use mode='intermittent'."
            )
    if driver == "Q_air_scfm":
        std = _scfm_to_std_m3_d(spec["Q_air_scfm"], spec.get("air_basis"))
        kwargs["Q_air"] = _std_to_field(std)
    elif driver == "Q_air_std_m3_d":
        kwargs["Q_air"] = _std_to_field(float(spec["Q_air_std_m3_d"]))
    elif driver == "KLa_20":
        kwargs["KLa_20"] = float(spec["KLa_20"])
    else:
        kla = float(spec["KLa"])
        if kla <= 0:
            raise AerationSpecError(
                "KLa must be > 0. QSDsan treats 0 as falsy and the getter then reads "
                "an unassigned attribute. To model a blower that stops, use "
                "mode='intermittent'."
            )
        kwargs["KLa"] = kla

    for key, target in (
        ("alpha", "alpha"),
        ("beta", "beta"),
        ("F", "F"),
        ("theta", "theta"),
        ("SOTE", "SOTE"),
        ("d_submergence", "d_submergence"),
        ("fine_pore", "fine_pore"),
        ("elevation_m", "elevation"),
        ("T_air_K", "T_air"),
        ("DOsat_s20", "DOsat_s20"),
        ("DOsat", "DOsat"),
        ("CF1", "CF1"),
    ):
        if key in spec:
            kwargs[target] = spec[key]

    # An explicitly-passed T_water_K is the reactor's ACTUAL operating temperature and
    # must win over the spec's declared value. The old precedence had it backwards, and
    # the failure was silent in exactly the case that matters: a replay stepping 28 -> 35
    # C against a spec carrying one design temperature kept that temperature for every
    # segment, pinning DO saturation and biasing both KLa and CO2 stripping all the way
    # through. `flowsheet_builder` passes the unit's own T here for the same reason.
    #
    # The spec value remains the fallback, so a caller that states a design temperature
    # and passes nothing still gets it.
    spec_T = spec.get("T_water_K")
    if T_water_K is not None and spec_T is not None and abs(float(spec_T) - float(T_water_K)) > 0.05:
        logger.warning(
            "aeration spec declares T_water_K=%.2f K but the reactor is operating at "
            "%.2f K; using the operating temperature. Carry water temperature as reactor "
            "state, not in the equipment spec.",
            float(spec_T), float(T_water_K),
        )
    water_T = T_water_K if T_water_K is not None else spec_T
    if water_T is not None:
        kwargs["T_water"] = float(water_T)

    if "alpha" not in kwargs:
        logger.warning(
            "aeration spec for a %.0f m3 reactor did not state alpha; QSDsan will use "
            "0.6. alpha is wastewater-specific and spans 0.3-0.85 in practice, a 2.8x "
            "swing in delivered oxygen. State it explicitly, with a band.",
            float(V_m3),
        )

    duty = _normalize_duty(spec, mode)
    return {"mode": mode, "kwargs": kwargs, "duty": duty}


def _normalize_duty(spec: Mapping[str, Any], mode: str) -> dict[str, float] | None:
    """Resolve the intermittent duty cycle, in days."""
    duty_keys = {"period_min", "on_min", "on_fraction", "phase_min", "ramp_min"}
    present = duty_keys & set(spec)

    if mode != "intermittent":
        if present:
            raise AerationSpecError(
                f"duty-cycle key(s) {sorted(present)} given but mode is {mode!r}; "
                f"use mode='intermittent'."
            )
        return None

    if "period_min" not in spec:
        raise AerationSpecError("intermittent aeration requires 'period_min'")
    period = float(spec["period_min"])
    if period <= 0:
        raise AerationSpecError("period_min must be > 0")

    if ("on_min" in spec) == ("on_fraction" in spec):
        raise AerationSpecError(
            "intermittent aeration requires exactly one of 'on_min' or 'on_fraction'"
        )
    on = (
        float(spec["on_min"])
        if "on_min" in spec
        else float(spec["on_fraction"]) * period
    )
    if not 0.0 <= on <= period:
        raise AerationSpecError(
            f"on time {on:.3f} min must lie within the {period:.3f} min period"
        )

    ramp = float(spec.get("ramp_min", 0.5))
    if ramp < 0:
        raise AerationSpecError("ramp_min must be >= 0")
    # Ramps are centred on the switch points, so each consumes ramp/2 either side.
    # Requiring ramp <= min(on, off) keeps the up- and down-ramps from overlapping,
    # which would make the duty non-monotonic and the cycle integral wrong.
    off = period - on
    if 0.0 < on < period and ramp > min(on, off):
        raise AerationSpecError(
            f"ramp_min {ramp} exceeds the shorter of the on ({on:.3f} min) and off "
            f"({off:.3f} min) phases; the ramps would overlap. Shorten ramp_min or "
            f"lengthen the cycle."
        )

    return {
        "period_d": period / 1440.0,
        "on_d": on / 1440.0,
        "phase_d": float(spec.get("phase_min", 0.0)) / 1440.0,
        "ramp_d": ramp / 1440.0,
    }


def duty_at(duty: Mapping[str, float] | None, t_days: float) -> float:
    """Aeration multiplier in [0, 1] at time ``t_days``.

    Smoothed at the transitions by ``ramp_d`` using a cubic smoothstep. A hard
    square wave is what the blower does, but it makes the ODE right-hand side
    discontinuous, which forces adaptive solvers to crawl or to step straight
    over a transition. A 30 s default ramp is far shorter than any biological
    timescale here and keeps the system integrable.

    Callers should still cap ``max_step`` at or below ``ramp_d``.
    """
    if not duty:
        return 1.0

    period = duty["period_d"]
    on = duty["on_d"]
    ramp = duty["ramp_d"]

    if on <= 0.0:
        return 0.0
    if on >= period:
        return 1.0

    pos = (float(t_days) - duty["phase_d"]) % period

    if ramp <= 0.0:
        return 1.0 if pos < on else 0.0

    def _smoothstep(x: float) -> float:
        x = min(max(x, 0.0), 1.0)
        return x * x * (3.0 - 2.0 * x)

    # Ramps are CENTRED on the switch points: duty passes through 0.5 exactly at
    # t=0 and t=on. Ramping entirely inside the on-window instead would lose
    # `ramp` of air every cycle (a 0.5 min ramp on a 6 min on-time under-delivers
    # by 8%), quietly biasing every intermittent-aeration result low.
    #
    # Integral over one period = ramp/2 + (on - ramp) + ramp/2 = on, exactly.
    half = 0.5 * ramp

    # The leading edge begins before the nominal start, i.e. at the tail of the
    # previous period; fold that back to a small negative position.
    if pos >= period - half:
        pos -= period

    if pos < -half:
        return 0.0
    if pos < half:
        return _smoothstep((pos + half) / ramp)
    if pos < on - half:
        return 1.0
    if pos < on + half:
        return _smoothstep((on + half - pos) / ramp)
    return 0.0


def build_diffused_aeration(
    spec: Mapping[str, Any],
    *,
    unit_id: str,
    V_m3: float,
    DO_ID: str = "S_O2",
    T_water_K: float | None = None,
) -> Any:
    """Build a ``qsdsan.processes.DiffusedAeration`` from a declarative spec."""
    from qsdsan.process_models import DiffusedAeration  # local: keep import cost off the path

    resolved = normalize_aeration_spec(
        spec, V_m3=V_m3, T_water_K=T_water_K, DO_ID=DO_ID
    )
    kwargs = dict(resolved["kwargs"])
    do_id = kwargs.pop("DO_ID")
    volume = kwargs.pop("V")
    return DiffusedAeration(f"{unit_id}_aeration", do_id, V=volume, **kwargs)


def attach_aeration(
    unit: Any,
    spec: Mapping[str, Any],
    *,
    T_water_K: float | None = None,
    volume_m3: float | None = None,
) -> Any:
    """Attach a spec-built aeration Process to ``unit`` and stamp its duty cycle.

    Nulls the compiled ODE so the new Process is picked up: both QSDsan and the
    guarded overlay capture the aeration terms by value at compile time.

    ``volume_m3`` overrides ``unit.V_max`` as the transfer reference volume. These are
    the SAME number in a constant-volume reactor and DIFFERENT in a filling or draining
    one: ``V_max`` is the hydraulic INITIAL condition the ODE integrates from, while KLa
    should be referenced to the volume the aeration actually sees over the interval.
    Conflating them silently offsets the whole volume trajectory -- which is exactly the
    defect this parameter exists to make impossible.
    """
    V_m3 = float(volume_m3) if volume_m3 else float(
        getattr(unit, "V_max", getattr(unit, "_V_max", 0.0)) or 0.0)
    DO_ID = getattr(unit, "_DO_ID", "S_O2") or "S_O2"
    resolved = normalize_aeration_spec(
        spec, V_m3=V_m3, T_water_K=T_water_K, DO_ID=DO_ID
    )

    aer = build_diffused_aeration(
        spec, unit_id=getattr(unit, "ID", "unit"), V_m3=V_m3, DO_ID=DO_ID,
        T_water_K=T_water_K,
    )
    unit.aeration = aer
    setattr(unit, DUTY_ATTR, resolved["duty"])

    # A spec-built Process supersedes any stale PID stamp; leaving
    # _codex_dynamic_kLa_d set would force the guarded ODE down the two-scalar
    # path and discard the corrections we just configured.
    if resolved["mode"] != "do_control":
        for attr in ("_codex_dynamic_kLa_d", "_codex_dynamic_Q_air_m3_d"):
            if hasattr(unit, attr):
                setattr(unit, attr, None)

    if hasattr(unit, "_ODE"):
        unit._ODE = None
    return aer


def aeration_spec_digest(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical, order-independent summary of a spec, for twin fingerprinting.

    A checkpoint restored against a different aeration configuration is a silently
    wrong twin, so the fingerprint has to cover the aeration basis. Returns only
    the keys actually present, so an unset parameter is distinguishable from one
    explicitly set to its default.
    """
    unknown = set(spec) - AERATION_SPEC_KEYS
    if unknown:
        raise AerationSpecError(f"unknown aeration spec key(s): {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key in sorted(spec):
        value = spec[key]
        out[key] = round(float(value), 9) if isinstance(value, (int, float)) and not isinstance(
            value, bool
        ) else value
    return out


def describe_aeration(unit: Any) -> dict[str, Any]:
    """Report the resolved aeration state of a unit, for diagnostics."""
    aer = getattr(unit, "aeration", None)
    out: dict[str, Any] = {"unit": getattr(unit, "ID", None)}

    if aer is None:
        out["mode"] = "anoxic"
        return out
    if isinstance(aer, (int, float)):
        out.update(
            mode="ideal_do_setpoint",
            do_setpoint_mg_L=float(aer),
            warning=(
                "DO is pinned and its derivative forced to zero: oxygen supply is "
                "effectively infinite and oxygen limitation cannot be represented."
            ),
        )
        return out

    duty = getattr(unit, DUTY_ATTR, None)
    out.update(
        mode="intermittent" if duty else "process",
        kla_field_per_day=_safe(aer, "KLa"),
        kla_20_per_day=_safe(aer, "KLa_20"),
        dosat_field_mg_L=_safe(aer, "DOsat"),
        sotr_g_per_day=_safe(aer, "SOTR"),
        q_air_field_m3_per_day=_safe(aer, "Q_air"),
        alpha=_safe(aer, "alpha"),
        beta=_safe(aer, "beta"),
        F=_safe(aer, "F"),
        theta=_safe(aer, "theta"),
        SOTE=_safe(aer, "SOTE"),
        d_submergence_m=_safe(aer, "d_submergence"),
        T_water_K=_safe(aer, "T_water"),
        tau=_safe(aer, "tau"),
        Omega=_safe(aer, "Omega"),
        delta=_safe(aer, "delta"),
    )
    if duty:
        out["duty_cycle"] = {
            "period_min": duty["period_d"] * 1440.0,
            "on_min": duty["on_d"] * 1440.0,
            "duty_fraction": duty["on_d"] / duty["period_d"] if duty["period_d"] else None,
            "ramp_min": duty["ramp_d"] * 1440.0,
        }
    return out


def _safe(obj: Any, attr: str) -> Any:
    try:
        value = getattr(obj, attr, None)
        return float(value) if isinstance(value, (int, float)) else value
    except Exception:
        return None


__all__ = [
    "AERATION_SPEC_KEYS",
    "VALID_MODES",
    "DUTY_ATTR",
    "AerationSpecError",
    "is_aeration_spec",
    "normalize_aeration_spec",
    "build_diffused_aeration",
    "attach_aeration",
    "describe_aeration",
    "duty_at",
]
