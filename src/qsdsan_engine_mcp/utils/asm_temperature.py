'C-4: temperature propagation into ASM kinetics.'

from __future__ import annotations

import logging
from typing import Dict, Mapping, Optional

logger = logging.getLogger(__name__)


class TemperatureRangeError(ValueError):
    """A theta correction was requested outside the range its theta was fitted over.

    Raised rather than warned. A theta of 1.07 applied 30 K past its range is a factor
    of 7.6, which is not a correction -- it is a different model wearing the same name.
    """


#: Per-process theta values with the range each is defensible over.
#:
#: These are the conventional wastewater-engineering values (Metcalf & Eddy; the ASM
#: reports). They are EMPIRICAL fits over the stated range, not activation energies, and
#: they are not interchangeable between processes: nitrifiers are far more
#: temperature-sensitive than heterotrophs, which is precisely the effect that matters
#: when a hot recycle lands in a nitrifying basin.
#:
#: Stamped ``not_identifiable`` for any specific plant until fitted against site data.
THETA_DEFAULTS: Dict[str, Dict[str, float]] = {
    # heterotrophic growth / hydrolysis
    "mu_H":  {"theta": 1.072, "T_min_C": 10.0, "T_max_C": 30.0},
    "k_h":   {"theta": 1.116, "T_min_C": 10.0, "T_max_C": 30.0},
    "b_H":   {"theta": 1.072, "T_min_C": 10.0, "T_max_C": 30.0},
    # autotrophs -- steeper, and the reason this module exists
    "mu_AOB": {"theta": 1.072, "T_min_C": 10.0, "T_max_C": 35.0},
    "mu_NOB": {"theta": 1.060, "T_min_C": 10.0, "T_max_C": 35.0},
    "b_AOB":  {"theta": 1.029, "T_min_C": 10.0, "T_max_C": 35.0},
    "b_NOB":  {"theta": 1.029, "T_min_C": 10.0, "T_max_C": 35.0},
    # phosphorus-accumulating organisms
    "mu_PAO": {"theta": 1.041, "T_min_C": 10.0, "T_max_C": 30.0},
    "q_PHA":  {"theta": 1.041, "T_min_C": 10.0, "T_max_C": 30.0},
    "q_PP":   {"theta": 1.041, "T_min_C": 10.0, "T_max_C": 30.0},
}

_T_REF_C = 20.0


def theta_correct(
    name: str,
    value_at_20C: float,
    T_C: float,
    *,
    thetas: Optional[Mapping[str, Mapping[str, float]]] = None,
    allow_extrapolation: bool = False,
) -> float:
    """``k(T) = k_20 * theta^(T - 20)``, refusing outside the fitted range.

    ``allow_extrapolation`` exists because a caller may knowingly want it -- but it has
    to be typed out, and it logs, so an extrapolated rate is never indistinguishable
    from a fitted one.
    """
    table = thetas or THETA_DEFAULTS
    if name not in table:
        raise KeyError(
            f"no theta for {name!r}. Known: {sorted(table)}. Guessing one is how a "
            "correction becomes a fabrication -- supply it explicitly instead."
        )
    spec = table[name]
    lo, hi = float(spec["T_min_C"]), float(spec["T_max_C"])
    if not lo <= T_C <= hi:
        if not allow_extrapolation:
            raise TemperatureRangeError(
                f"{name}: {T_C:.1f} C is outside the {lo:.0f}-{hi:.0f} C range its "
                f"theta={spec['theta']} was fitted over. theta^(T-20) is an empirical "
                "fit, not an activation energy, and it compounds fast -- 1.072 over 30 K "
                "of extrapolation is a factor of 8. Pass allow_extrapolation=True if you "
                "mean it, and say so wherever the result is quoted. For nitrifier rates "
                "ABOVE the range, extrapolation is not merely unfitted, it points the "
                "wrong direction (measured optima: AOB 35 C, NOB 38 C, declining above "
                "-- Grunditz & Dalhammar 2001): use "
                "nitrifier_high_temperature_screen() for the literature-bounded "
                "statement instead."
            )
        logger.warning(
            "%s: theta-correcting to %.1f C, outside the fitted %.0f-%.0f C range. "
            "The rate is an extrapolation.", name, T_C, lo, hi,
        )
    return float(value_at_20C) * float(spec["theta"]) ** (T_C - _T_REF_C)


#: Literature bands for nitrifier activity ABOVE the fitted theta range. The theta
#: refusal above 35 C is not a shrug -- measurement says what happens up there, and
#: the answer is worse than extrapolation: theta^(T-20) keeps RISING while the real
#: rate peaks and falls. Optima measured in pure culture: Nitrosomonas (AOB) 35 C,
#: Nitrobacter (NOB) 38 C -- Grunditz & Dalhammar (2001), Water Research 35:433,
#: doi 10.1016/s0043-1354(00)00312-2 (registry-verified).
NITRIFIER_HIGH_T_BANDS = (
    (35.0, 38.0, "near_optimum",
     "at/near the measured optima (AOB 35 C, NOB 38 C -- Grunditz & Dalhammar "
     "2001): rates plateau, so theta^(T-20) OVER-predicts; use the 35 C value "
     "as a ceiling, not the extrapolation"),
    (38.0, 42.0, "declining",
     "above both optima: measured rates DECLINE with temperature while "
     "theta^(T-20) rises -- the correction points the wrong direction. Treat "
     "rates as degraded and unquantified without site data"),
    (42.0, None, "no_design_basis",
     "no mesophilic design basis: beyond the measured activity range of both "
     "genera. Treat nitrification as infeasible at this temperature unless "
     "thermophilic-specific evidence is produced"),
)


def nitrifier_high_temperature_screen(T_C: float) -> dict:
    """Literature-bounded statement for basin temperatures above the theta range.

    Exists so a refusal is never the last word: ``theta_correct`` correctly will not
    extrapolate ``mu_AOB``/``mu_NOB`` past 35 C, and THIS says what measurement
    knows about the region instead (doctrine: degrade to a cited bound, not to
    silence). Returns ``{"band", "T_C", "statement", "source"}``.
    """
    src = ("Grunditz & Dalhammar (2001), Water Res. 35:433, "
           "doi 10.1016/s0043-1354(00)00312-2")
    if T_C <= 35.0:
        return {"band": "within_theta_range", "T_C": float(T_C),
                "statement": "inside the fitted theta range; use theta_correct",
                "source": src}
    for lo, hi, band, statement in NITRIFIER_HIGH_T_BANDS:
        if T_C > lo and (hi is None or T_C <= hi):
            return {"band": band, "T_C": float(T_C),
                    "statement": statement, "source": src}
    raise AssertionError("unreachable")


def kinetics_at(
    T_C: float,
    base_kinetics_20C: Mapping[str, float],
    *,
    thetas: Optional[Mapping[str, Mapping[str, float]]] = None,
    allow_extrapolation: bool = False,
) -> Dict[str, float]:
    """Theta-correct a whole kinetic set, passing through anything with no theta.

    Pass-through is deliberate and is the safe direction: half-saturation coefficients,
    yields and inhibition constants are NOT theta-corrected in standard practice, and
    silently scaling one would be worse than leaving it. Returns a new dict.
    """
    out = dict(base_kinetics_20C)
    table = thetas or THETA_DEFAULTS
    for name, value in base_kinetics_20C.items():
        if name in table:
            out[name] = theta_correct(
                name, value, T_C, thetas=table,
                allow_extrapolation=allow_extrapolation,
            )
    return out


def basin_temperature_C(
    *,
    forward_flow_m3_d: float,
    forward_T_C: float,
    recycle_flow_m3_d: float,
    recycle_T_C: float,
    heat_loss_C: float = 0.0,
) -> float:
    """Mixed basin temperature from a forward stream and a hot recycle.

    Deliberately a flow-weighted mix and nothing more: this is the term that is missing
    entirely today, and an order-of-magnitude-correct mix is worth far more than a
    detailed basin heat balance that nobody has the surface areas for. ``heat_loss_C``
    is the one knob, and it is an input rather than a correlation so that whoever sets
    it owns it.
    """
    total = float(forward_flow_m3_d) + float(recycle_flow_m3_d)
    if total <= 0:
        raise ValueError("no flow into the basin; temperature is undefined")
    mixed = (forward_flow_m3_d * forward_T_C + recycle_flow_m3_d * recycle_T_C) / total
    return mixed - float(heat_loss_C)
