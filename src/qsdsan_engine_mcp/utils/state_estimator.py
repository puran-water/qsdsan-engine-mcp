"""
Single-pass mASM2d influent state estimator.

This utility maps plant-level wastewater measurements onto the engine's
31-component mASM2d influent basis without any iterative calls to
``validate_state``.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional
import logging

from qsdsan_engine_mcp.core.contracts.stream_state import StreamState
from qsdsan_engine_mcp.models.masm2d import DEFAULT_MASM2D_DOMESTIC_WW, DEFAULT_MASM2D_KWARGS

from puran_mcp_support.engineering.heuristics.nitrification.state_partition import IonBasisError, build_ion_basis

logger = logging.getLogger(__name__)

__all__ = ["IonBasisError", "estimate_masm2d_state"]


# ``validate_state`` uses these COD-equivalent factors in
# ``core.converters.get_coefficients(..., use_component_props=False)``.
_ENGINE_I_COD = {
    "S_I": 1.0,
    "S_F": 1.0,
    "S_A": 1.0,
    "X_S": 1.0,
    "X_I": 1.5,
    "X_H": 1.42,
    "X_PAO": 1.42,
    "X_AUT": 1.42,
    "X_PHA": 1.67,
    "X_PP": 0.0,
}

# Pune/Nanded N/P/TSS calibration carried by the engine model defaults.
_ENGINE_I_N = {
    "S_I": DEFAULT_MASM2D_KWARGS["iN_SI"],
    "S_F": DEFAULT_MASM2D_KWARGS["iN_SF"],
    "S_A": 0.0,
    "X_I": DEFAULT_MASM2D_KWARGS["iN_XI"],
    "X_S": DEFAULT_MASM2D_KWARGS["iN_XS"],
    "X_H": DEFAULT_MASM2D_KWARGS["iN_BM"],
    "X_PAO": DEFAULT_MASM2D_KWARGS["iN_BM"],
    "X_AUT": DEFAULT_MASM2D_KWARGS["iN_BM"],
    "X_PHA": 0.0,
    "X_PP": 0.0,
    "S_NH4": 1.0,
}
_ENGINE_I_P = {
    "S_I": DEFAULT_MASM2D_KWARGS["iP_SI"],
    "S_F": DEFAULT_MASM2D_KWARGS["iP_SF"],
    "S_A": 0.0,
    "X_I": DEFAULT_MASM2D_KWARGS["iP_XI"],
    "X_S": DEFAULT_MASM2D_KWARGS["iP_XS"],
    "X_H": DEFAULT_MASM2D_KWARGS["iP_BM"],
    "X_PAO": DEFAULT_MASM2D_KWARGS["iP_BM"],
    "X_AUT": DEFAULT_MASM2D_KWARGS["iP_BM"],
    "X_PHA": 0.0,
    "X_PP": 0.31,
    "S_PO4": 1.0,
}
_ENGINE_I_TSS = {
    "X_I": DEFAULT_MASM2D_KWARGS["iTSS_XI"],
    "X_S": DEFAULT_MASM2D_KWARGS["iTSS_XS"],
    "X_H": DEFAULT_MASM2D_KWARGS["iTSS_BM"],
    "X_PAO": DEFAULT_MASM2D_KWARGS["iTSS_BM"],
    "X_AUT": DEFAULT_MASM2D_KWARGS["iTSS_BM"],
    "X_PHA": 0.55,
    "X_PP": 3.2251507977994662,
    "X_CaCO3": 1.0,
    "X_struv": 1.0,
    "X_newb": 1.0,
    "X_ACP": 1.0,
    "X_MgCO3": 1.0,
    "X_AlOH": 1.0,
    "X_AlPO4": 1.0,
    "X_FeOH": 1.0,
    "X_FePO4": 1.0,
}
_ENGINE_F_VMASS = {
    "X_I": 0.85,
    "X_S": 0.85,
    "X_H": 0.85,
    "X_PAO": 0.85,
    "X_AUT": 0.85,
    "X_PHA": 0.92727,
    "X_PP": 0.0,
    "X_CaCO3": 0.0,
    "X_struv": 0.0,
    "X_newb": 0.0,
    "X_ACP": 0.0,
    "X_MgCO3": 0.0,
    "X_AlOH": 0.0,
    "X_AlPO4": 0.0,
    "X_FeOH": 0.0,
    "X_FePO4": 0.0,
}

#: Municipal defaults offered only as an *explicitly assumed* ion basis
#: (``allow_assumed_ions=True``); never applied silently.
_DEFAULT_IONS = {
    "S_K": DEFAULT_MASM2D_DOMESTIC_WW["S_K"],
    "S_Mg": DEFAULT_MASM2D_DOMESTIC_WW["S_Mg"],
    "S_Ca": DEFAULT_MASM2D_DOMESTIC_WW["S_Ca"],
    "S_Na": DEFAULT_MASM2D_DOMESTIC_WW["S_Na"],
    "S_Cl": DEFAULT_MASM2D_DOMESTIC_WW["S_Cl"],
}

#: Species accepted in the ``ions=`` mapping (the charge-balance basis).
_ION_BASIS_SPECIES = ("S_K", "S_Mg", "S_Ca", "S_Na", "S_Cl", "S_IC")

_MIN_BIODEGRADABLE_FRACTION = 0.20
_MAX_BIODEGRADABLE_FRACTION = 0.95
_BOD5_TO_BIOCOD_FACTOR = 0.58
_DEFAULT_VSS_FRACTION = 0.85
_PARTICULATE_TSS_PER_COD = DEFAULT_MASM2D_KWARGS["iTSS_XS"]
_ALKALINITY_TO_IC = 12.0 / 50.0


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _nonnegative(name: str, value: Optional[float]) -> float:
    if value is None:
        raise ValueError(f"{name} is required")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return float(value)


def _estimate_biodegradable_fraction(cod: float, bod5: float) -> float:
    if cod <= 0:
        return _MIN_BIODEGRADABLE_FRACTION
    return _clip(
        (bod5 / cod) / _BOD5_TO_BIOCOD_FACTOR,
        _MIN_BIODEGRADABLE_FRACTION,
        _MAX_BIODEGRADABLE_FRACTION,
    )


def _sum_with_coefficients(
    concentrations: Dict[str, float],
    coefficients: Dict[str, float],
) -> float:
    return sum(concentrations.get(component_id, 0.0) * coefficient
               for component_id, coefficient in coefficients.items())


def estimate_masm2d_state(
    cod: float,
    bod5: float,
    tss: float,
    vss: float,
    tkn: float,
    tn: Optional[float],
    tp: float,
    alkalinity: float,
    temperature_c: float,
    flow_m3d: float,
    *,
    ions: Optional[Mapping[str, float]] = None,
    allow_assumed_ions: bool = False,
) -> Dict[str, object]:
    """
    Estimate a complete mASM2d StreamState dict from bulk wastewater metrics.

    The estimator is closed-form:
    1. Use BOD5/COD to infer total biodegradable COD.
    2. Use TSS/VSS to set the particulate organic envelope.
    3. Allocate residual COD to ``S_I`` and residual TSS to ``X_CaCO3``.
    4. Solve NH4, NO3, and PO4 from the remaining N/P balances.

    Ion basis (fail-closed)
    -----------------------
    The pH solve consumes S_K/S_Mg/S_Ca/S_Na/S_Cl and total inorganic carbon
    (S_IC). ``ions=`` supplies *measured* values for any of those six species.
    Species not measured fall back to municipal defaults (and, for S_IC, the
    alkalinity-derived estimate ``alkalinity * 12/50``) **only when**
    ``allow_assumed_ions=True`` — in which case every substituted species is
    stamped into ``not_identifiable`` and the ion provenance becomes
    ``"partly_assumed"``. With the default ``allow_assumed_ions=False``, any
    unmeasured species raises :class:`IonBasisError` instead of silently
    inventing chemistry. An alkalinity-derived S_IC is always treated as
    assumed (alkalinity is a titration, not DIC), so it can never look
    measured downstream.

    Returns
    -------
    dict with keys:
    - ``"state"``: the mASM2d StreamState dict (as before).
    - ``"ion_provenance"``: ``"measured"`` or ``"partly_assumed"``.
    - ``"not_identifiable"``: list of ion-basis species that were assumed.

    Notes
    -----
    - COD/TKN/TP are matched against the engine's ``validate_state`` basis.
    - When the supplied ``COD``/``BOD5``/``TSS``/``VSS`` tuple is not jointly
      feasible on that basis, the estimator prioritizes COD, TKN, TP, and TSS
      closure and clips volatile solids downward.
    - ``TN`` is optional. If omitted, ``S_NO3`` is set to zero.
    - ``H2O`` is emitted at the canonical solvent mass (1e6 mg/L).
    """
    unknown_ions = sorted(set(ions or {}) - set(_ION_BASIS_SPECIES))
    if unknown_ions:
        raise ValueError(
            f"Unknown ion keys {unknown_ions} in ions=. The mASM2d ion basis accepts "
            f"exactly {sorted(_ION_BASIS_SPECIES)} (mg/L). Rename the offending keys "
            "to those component ids or drop them."
        )

    cod = _nonnegative("cod", cod)
    bod5 = _nonnegative("bod5", bod5)
    tss = _nonnegative("tss", tss)
    vss = _nonnegative("vss", vss)
    tkn = _nonnegative("tkn", tkn)
    tp = _nonnegative("tp", tp)
    alkalinity = _nonnegative("alkalinity", alkalinity)
    flow_m3d = _nonnegative("flow_m3d", flow_m3d)

    if tn is not None:
        tn = _nonnegative("tn", tn)
        if tn < tkn:
            raise ValueError(f"tn must be >= tkn, got tn={tn}, tkn={tkn}")

    # Charge-balance basis: measured ions win; municipal defaults and the
    # alkalinity-derived S_IC (12 g C per 50 g CaCO3) are *assumed* values that
    # require allow_assumed_ions=True and are stamped not_identifiable.
    # Raises IonBasisError naming the missing species otherwise.
    try:
        ion_basis = build_ion_basis(
            measured=dict(ions or {}),
            assumed={**_DEFAULT_IONS, "S_IC": alkalinity * _ALKALINITY_TO_IC},
            allow_assumed=allow_assumed_ions,
        )
    except IonBasisError as exc:
        raise IonBasisError(
            f"{exc} For estimate_masm2d_state: pass measured values via ions= "
            "(mg/L; species S_K/S_Mg/S_Ca/S_Na/S_Cl plus S_IC as measured DIC), "
            "or set allow_assumed_ions=True to accept municipal defaults and the "
            "alkalinity-derived S_IC with an explicit not_identifiable stamp."
        ) from exc

    biodegradable_fraction = _estimate_biodegradable_fraction(cod, bod5)
    biodegradable_cod = cod * biodegradable_fraction

    # Use VSS to infer the maximum organic TSS, then clip it if the COD/BOD5
    # basis cannot support that much particulate organic matter.
    organic_tss_target = min(tss, vss / _DEFAULT_VSS_FRACTION)
    particulate_cod_target = organic_tss_target / _PARTICULATE_TSS_PER_COD
    particulate_cod_max = min(cod, (cod + 0.5 * biodegradable_cod) / 1.5)
    particulate_cod = min(particulate_cod_target, particulate_cod_max)

    x_s = min(particulate_cod, biodegradable_cod)
    x_i = max(0.0, particulate_cod - x_s)
    soluble_biodegradable_cod = max(0.0, biodegradable_cod - x_s)

    tss_ratio = tss / cod if cod > 0 else 0.0
    s_a_fraction = 0.20 if tss_ratio < 0.25 else 0.10
    s_a = soluble_biodegradable_cod * s_a_fraction
    s_f = soluble_biodegradable_cod - s_a
    s_i = cod - (x_s + _ENGINE_I_COD["X_I"] * x_i + soluble_biodegradable_cod)
    s_i = max(0.0, s_i)

    x_caco3 = max(0.0, tss - _PARTICULATE_TSS_PER_COD * (x_s + x_i))

    concentrations = {
        "S_O2": 0.0,
        "S_N2": 0.0,
        "S_NH4": 0.0,  # solved below
        "S_NO3": 0.0 if tn is None else max(0.0, tn - tkn),
        "S_PO4": 0.0,  # solved below
        "S_F": s_f,
        "S_A": s_a,
        "S_I": s_i,
        "X_I": x_i,
        "X_S": x_s,
        "X_H": 0.0,
        "X_PAO": 0.0,
        "X_PP": 0.0,
        "X_PHA": 0.0,
        "X_AUT": 0.0,
        "X_CaCO3": x_caco3,
        "X_struv": 0.0,
        "X_newb": 0.0,
        "X_ACP": 0.0,
        "X_MgCO3": 0.0,
        "X_AlOH": 0.0,
        "X_AlPO4": 0.0,
        "X_FeOH": 0.0,
        "X_FePO4": 0.0,
        "H2O": 1_000_000.0,
    }
    concentrations.update(ion_basis.as_concentrations())

    organic_n = _sum_with_coefficients(concentrations, {
        component_id: coefficient
        for component_id, coefficient in _ENGINE_I_N.items()
        if component_id not in ("S_NH4",)
    })
    concentrations["S_NH4"] = max(0.0, tkn - organic_n)

    organic_p = _sum_with_coefficients(concentrations, {
        component_id: coefficient
        for component_id, coefficient in _ENGINE_I_P.items()
        if component_id not in ("S_PO4",)
    })
    concentrations["S_PO4"] = max(0.0, tp - organic_p)

    achieved_vss = sum(
        concentrations.get(component_id, 0.0)
        * _ENGINE_I_TSS.get(component_id, 0.0)
        * _ENGINE_F_VMASS.get(component_id, 0.0)
        for component_id in concentrations
    )
    if achieved_vss + 1e-9 < vss:
        logger.warning(
            "mASM2d state estimation clipped VSS from %.1f to %.1f mg/L to preserve "
            "COD/TKN/TP/TSS closure on the engine basis",
            vss,
            achieved_vss,
        )

    state = StreamState.from_concentrations(
        model_type="mASM2d",
        flow_m3_d=flow_m3d,
        temperature_K=temperature_c + 273.15,
        concentration_units="mg/L",
        concentrations=concentrations,
    ).model_dump(mode="json")

    return {
        "state": state,
        "ion_provenance": ion_basis.provenance,
        "not_identifiable": list(ion_basis.not_identifiable),
    }
