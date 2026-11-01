'Mineral equilibrium registry + aqueous speciation for mADM1 precipitation.'

import warnings
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import numpy as np
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc
from puran_mcp_support.schema import ACID_DISSOCIATION_CONSTANTS, MADM1_FECL3_FE_S_P_KERNEL, MADM1_MINERAL_EXTENSION_KERNEL, PHREEQC_PHASE_GAS_CONSTANT_J_MOL_K, REFERENCE_TEMPERATURE_K, ActivityModelDomain

ensure_biosteam_tictoc()

T_REF_K = REFERENCE_TEMPERATURE_K

_SHARED_FE_S_P_PHASES = {phase.phase_id: phase for phase in MADM1_FECL3_FE_S_P_KERNEL.phases}
_SHARED_MINERAL_PHASES = {phase.phase_id: phase for phase in MADM1_MINERAL_EXTENSION_KERNEL.phases}


def _shared_phase(phase_id: str):
    """Return one immutable phase definition from the shared chemistry registry."""

    try:
        return _SHARED_FE_S_P_PHASES[phase_id]
    except KeyError as error:  # pragma: no cover - import-time registry invariant
        raise RuntimeError(f"shared Fe-S-P kernel is missing required phase {phase_id!r}") from error


def _shared_mineral(phase_id: str):
    """One immutable phase from the shared mineral-extension registry."""

    try:
        return _SHARED_MINERAL_PHASES[phase_id]
    except KeyError as error:  # pragma: no cover - import-time registry invariant
        raise RuntimeError(f"shared mineral-extension kernel is missing phase {phase_id!r}") from error


def _shared_mineral_law(phase_id: str):
    """The shared phase's own log10 Ksp(T) law (analytic / van't Hoff / hold) —
    the SAME curve the PHREEQC identity benchmark renders, so the mADM1
    registry and the benchmark cannot diverge."""

    phase = _shared_mineral(phase_id)
    return lambda T_K, _p=phase: _p.log10_k(float(T_K))


# Davies is retained as the reduced, differentiable activity model for the
# first shared-kernel vertical slice.  The ionic-strength ceiling is a model
# contract, not a warning: recommendation callers must prove that the result
# is inside this envelope.  The temperature interval is the declared
# operational envelope for this implementation; PHREEQC remains the
# independent audit outside it.
DAVIES_ACTIVITY_DOMAIN = ActivityModelDomain(
    activity_model_id="davies_reduced_v1",
    maximum_ionic_strength_molal=0.5,
    minimum_temperature_c=0.0,
    maximum_temperature_c=50.0,
)


class ActivityModelOutOfDomainError(ValueError):
    """Raised when recommendation-mode chemistry exceeds its activity domain."""

    def __init__(self, report: dict[str, object]):
        self.report = dict(report)
        super().__init__(
            "Davies activity model is outside its declared domain: "
            f"I={report['calculated_ionic_strength']!r} molal, "
            f"temperature={report['temperature_c']!r} C, "
            f"species_coverage={report['species_coverage_fraction']!r}"
        )


class InvalidThermodynamicConstantError(ValueError):
    """Raised when a phase constant cannot support an SI calculation."""


def require_positive_finite_ksp(phase: "MineralPhase", T_K: float) -> float:
    """Return a phase Ksp or fail closed; never manufacture ``SI = 1``.

    The former fallback converted a nonpositive/underflowed constant into an
    apparent equilibrium state.  That is maximally misleading for kinetics,
    because it silently zeroes the precipitation/dissolution driving force.
    """

    ksp = float(phase.ksp(float(T_K)))
    if not np.isfinite(ksp) or ksp <= 0.0:
        raise InvalidThermodynamicConstantError(f"phase {phase.name!r} returned invalid Ksp={ksp!r} at T={T_K!r} K")
    return ksp


def davies_activity_domain_report(
    *,
    ionic_strength_molal: float,
    temperature_c: float,
    species_coverage_fraction: float,
) -> dict[str, object]:
    """Return the machine-readable validity contract for a Davies evaluation."""

    return DAVIES_ACTIVITY_DOMAIN.check(
        ionic_strength_molal=float(ionic_strength_molal),
        temperature_c=float(temperature_c),
        species_coverage_fraction=float(species_coverage_fraction),
    )


def require_davies_activity_domain(report: dict[str, object]) -> None:
    """Fail closed when a result is proposed for recommendation use."""

    if not bool(report.get("activity_model_in_domain")):
        raise ActivityModelOutOfDomainError(report)


# ---------------------------------------------------------------------------
# Aqueous speciation (shared, temperature-aware)
# ---------------------------------------------------------------------------
# Local aliases map the historical QSDsan names to the immutable shared
# constants used by both numeric and Pyomo adapters.  This keeps the public
# helpers stable while eliminating a second, drifting acid-base table.
_SHARED_ACID_CONSTANT_IDS = {
    "w": "water",
    "nh4": "ammonium",
    "co2": "carbonic_1",
    "hco3": "carbonic_2",
    "h2s": "sulfide_1",
    "s2": "sulfide_2",
    "h3po4": "phosphoric_1",
    "h2po4": "phosphoric_2",
    "hpo4": "phosphoric_3",
}


def _ka(name: str, T_K: float) -> float:
    return ACID_DISSOCIATION_CONSTANTS[_SHARED_ACID_CONSTANT_IDS[name]].ka(T_K)


def phosphate_alphas(pH: float, T_K: float = T_REF_K):
    """(alpha_H3PO4, alpha_H2PO4, alpha_HPO4, alpha_PO4) — sum to 1."""
    H = 10.0 ** (-pH)
    k1, k2, k3 = _ka("h3po4", T_K), _ka("h2po4", T_K), _ka("hpo4", T_K)
    denom = H**3 + k1 * H**2 + k1 * k2 * H + k1 * k2 * k3
    return (H**3 / denom, k1 * H**2 / denom, k1 * k2 * H / denom, k1 * k2 * k3 / denom)


def carbonate_alpha_co3(pH: float, T_K: float = T_REF_K) -> float:
    H = 10.0 ** (-pH)
    k1, k2 = _ka("co2", T_K), _ka("hco3", T_K)
    return (k1 * k2) / (H**2 + k1 * H + k1 * k2)


def ammonium_alpha_nh4(pH: float, T_K: float = T_REF_K) -> float:
    return 1.0 / (1.0 + 10.0 ** (pH + np.log10(_ka("nh4", T_K))))


def sulfide_alpha_hs(pH: float, T_K: float = T_REF_K) -> float:
    """HS- fraction of analytical S(-II), including the small S2- fraction."""
    k1, k2 = _ka("h2s", T_K), _ka("s2", T_K)
    H = 10.0 ** (-pH)
    return k1 * H / (H**2 + k1 * H + k1 * k2)


# ---------------------------------------------------------------------------
# Ionic strength and activity coefficients
# ---------------------------------------------------------------------------
def _index_or_none(cmps, name):
    """Component index, or None when the set does not carry that component.

    ``CompiledComponents.index`` raises ``UndefinedComponent``, which is neither
    ``ValueError`` nor ``IndexError`` -- so a narrow except never caught it and the
    scaling screen raised outright on any component set without iron and aluminium.
    That is every ASM set, including ``mASM2d2N``: the screen was reachable only from
    mADM1, and from anywhere else it was an exception rather than a result.
    """
    try:
        return cmps.index(name)
    except Exception:
        return None


def _first_index(cmps, *names):
    """Index of the first component present, or None.

    The inorganic pools are named differently in the two model families -- ADM1 calls
    them ``S_IN``/``S_IP``, ASM calls them ``S_NH4``/``S_PO4``. Looking only for the
    ADM1 names returned an activity of ZERO for ammonium and phosphate on an ASM
    state, so every phosphate mineral came back undersaturated by construction. The
    ADM1 name is tried first, so mADM1 behaviour is unchanged.
    """
    for name in names:
        idx = _index_or_none(cmps, name)
        if idx is not None:
            return idx
    return None


#: (component aliases, charge, speciation) triples for ionic strength. Weak-acid
#: totals enter at their dominant near-neutral species charge (HCO3-, HPO4^2-
#: approximated at z=2 only via its alpha, HS-, NH4+); a full speciated I is
#: overkill for a Davies-level model but omitting the majors biased I low.
_IONIC_SPECIES = (
    (("S_Na",), 1, None),
    (("S_K",), 1, None),
    (("S_Cl",), 1, None),
    (("S_Ca",), 2, None),
    (("S_Mg",), 2, None),
    (("S_Fe2",), 2, None),
    (("S_Fe3",), 3, None),
    (("S_Al",), 3, None),
    (("S_SO4",), 2, None),
    (("S_IN", "S_NH4"), 1, "nh4"),
    (("S_IC",), 1, "hco3_frac"),
    (("S_IS",), 1, "hs"),
    (("S_IP", "S_PO4"), 2, "hpo4_frac"),
)


def ionic_strength(state_arr, cmps, unit_conversion, pH: float = 7.0, T_K: float = T_REF_K) -> float:
    """I = 0.5 * sum(c_i z_i^2) over the major dissolved species [mol/L]."""
    ionic_strength_molal = 0.0
    for names, z, spec in _IONIC_SPECIES:
        idx = _first_index(cmps, *names)
        if idx is None:
            continue
        conc_M = max(0.0, float(state_arr[idx]) * float(unit_conversion[idx]))
        if spec == "nh4":
            conc_M *= ammonium_alpha_nh4(pH, T_K)
        elif spec == "hs":
            conc_M *= sulfide_alpha_hs(pH, T_K)
        elif spec == "hco3_frac":
            H = 10.0 ** (-pH)
            k1, k2 = _ka("co2", T_K), _ka("hco3", T_K)
            conc_M *= (k1 * H) / (H**2 + k1 * H + k1 * k2)
        elif spec == "hpo4_frac":
            conc_M *= phosphate_alphas(pH, T_K)[2]
        ionic_strength_molal += 0.5 * conc_M * z * z
    return ionic_strength_molal


#: Verified literature validity of the two correlations behind A(T): the Kell
#: (1975) density fit is stated 0-150 C and the Malmberg & Maryott (1956)
#: dielectric fit is measured 0-100 C, so their intersection 0-100 C is the
#: verified domain. Diagnostic callers a few kelvin past it (a column bottom
#: fractionally above the boil) get the extrapolated value with a loud warning
#: -- the module contract keeps diagnostics non-fatal -- and beyond the grace
#: band the correlation basis is gone, so the function fails closed for
#: everyone. The stricter 0-50 C Davies operational envelope for
#: recommendation-mode results is enforced separately and fatally via
#: ``DAVIES_ACTIVITY_DOMAIN`` / ``require_davies_activity_domain``.
DEBYE_HUCKEL_A_MIN_C = 0.0
DEBYE_HUCKEL_A_MAX_C = 100.0
_DEBYE_HUCKEL_A_GRACE_C = 10.0


def debye_huckel_a_water(T_K: float = T_REF_K) -> float:
    """Debye-Huckel limiting-law A for pure water, verified over 0-100 C.

    ``A = 1.82483e6 sqrt(rho_kg_L) / (eps * T_K)**1.5`` (Manov et al. 1943,
    JACS 65:1765; the form in Stumm & Morgan 1996).

    * Density: Kell (1975), J. Chem. Eng. Data 20(1):97-105
      (doi 10.1021/je60064a005), rational fit stated 0-150 C at 1 atm. This
      replaces the Bigg (1967)/UNESCO EOS-80 pure-water polynomial, whose
      stated range is -2 to 40 C and which runs +0.5% high at 90 C and +1.1%
      high at 100 C (0.96921 vs 0.95835 kg/L). Below 50 C the two agree to
      <3e-5 kg/L, so nothing referenced to the old envelope moves.
    * Dielectric: Malmberg & Maryott (1956), J. Res. NBS 56(1):1-8 (RP2641),
      static dielectric constant of water, measured and fitted 0-100 C --
      already valid across the widened domain and retained unchanged.

    Anchors: A = 0.5108 at 25 C and 0.5959 at 100 C, against 0.5092 / 0.5998
    in the Helgeson & Kirkham (1974) tabulation -- within ~0.7% throughout.

    Domain policy (module contract: diagnostics degrade loudly, only
    recommendation mode is fatal): inside 0-100 C compute silently; within
    ``_DEBYE_HUCKEL_A_GRACE_C`` beyond either end compute and emit a loud
    ``UserWarning``; farther out raise ``ValueError`` -- there is no
    literature basis left to extrapolate.
    """

    temperature_c = float(T_K) - 273.15
    if not DEBYE_HUCKEL_A_MIN_C <= temperature_c <= DEBYE_HUCKEL_A_MAX_C:
        in_grace_band = (
            DEBYE_HUCKEL_A_MIN_C - _DEBYE_HUCKEL_A_GRACE_C
            <= temperature_c
            <= DEBYE_HUCKEL_A_MAX_C + _DEBYE_HUCKEL_A_GRACE_C
        )
        if not in_grace_band:
            raise ValueError(
                f"Debye-Huckel A(T) at {temperature_c:.2f} C is outside the verified "
                f"{DEBYE_HUCKEL_A_MIN_C:.0f}-{DEBYE_HUCKEL_A_MAX_C:.0f} C domain "
                f"(Kell 1975 density, 0-150 C; Malmberg & Maryott 1956 dielectric, "
                f"0-100 C) by more than the {_DEBYE_HUCKEL_A_GRACE_C:.0f} K "
                "diagnostic grace band"
            )
        warnings.warn(
            f"Debye-Huckel A(T) evaluated at {temperature_c:.2f} C, outside the "
            f"verified {DEBYE_HUCKEL_A_MIN_C:.0f}-{DEBYE_HUCKEL_A_MAX_C:.0f} C "
            "domain but within the diagnostic grace band: the value is an "
            "extrapolation of the Kell (1975) density and Malmberg & Maryott "
            "(1956) dielectric fits -- treat it as indicative, not quantitative",
            UserWarning,
            stacklevel=2,
        )
    density_kg_l = (
        999.83952
        + 16.945176 * temperature_c
        - 7.9870401e-3 * temperature_c**2
        - 46.170461e-6 * temperature_c**3
        + 105.56302e-9 * temperature_c**4
        - 280.54253e-12 * temperature_c**5
    ) / (1.0 + 16.879850e-3 * temperature_c) / 1000.0
    dielectric = 87.740 - 0.40008 * temperature_c + 9.398e-4 * temperature_c**2 - 1.410e-6 * temperature_c**3
    return 1.82483e6 * np.sqrt(density_kg_l) / (dielectric * float(T_K)) ** 1.5


def davies_activity_coeff(
    charge: int,
    ionic_strength_molal: float,
    T_K: float = T_REF_K,
) -> float:
    """Davies coefficient with temperature-dependent Debye-Huckel A."""

    if ionic_strength_molal < 1e-10:
        return 1.0
    A = debye_huckel_a_water(T_K)
    sqrt_I = np.sqrt(ionic_strength_molal)
    log_gamma = -A * charge**2 * (sqrt_I / (1 + sqrt_I) - 0.3 * ionic_strength_molal)
    return 10.0**log_gamma


def calc_activities(
    state_arr, cmps, pH, ionic_strength_molal, unit_conversion, T_K: float = T_REF_K
) -> Dict[str, float]:
    """Ion activities [mol/L] from the state vector. Concentrations are
    clamped non-negative before use — a tiny BDF undershoot must not produce
    a negative activity (the integrated state's negativity is judged
    separately by the physicality gate, never hidden here)."""
    activities: Dict[str, float] = {}

    def total_M(*names: str) -> float:
        idx = _first_index(cmps, *names)
        if idx is None:
            return 0.0
        return max(0.0, float(state_arr[idx]) * float(unit_conversion[idx]))

    def ion(name: str, charge: int) -> float:
        return total_M(name) * davies_activity_coeff(
            charge,
            ionic_strength_molal,
            T_K,
        )

    activities["Ca2+"] = ion("S_Ca", +2)
    activities["Mg2+"] = ion("S_Mg", +2)
    activities["Fe2+"] = ion("S_Fe2", +2)
    activities["Fe3+"] = ion("S_Fe3", +3)
    activities["Al3+"] = ion("S_Al", +3)
    activities["K+"] = ion("S_K", +1)
    activities["Na+"] = ion("S_Na", +1)

    H = 10.0 ** (-pH)
    activities["H+"] = H  # pH is already an activity scale
    activities["OH-"] = (_ka("w", T_K) / H) * davies_activity_coeff(-1, ionic_strength_molal, T_K)

    a1, a2, a3, a4 = phosphate_alphas(pH, T_K)
    P_total = total_M("S_IP", "S_PO4")
    activities["H2PO4-"] = P_total * a2 * davies_activity_coeff(-1, ionic_strength_molal, T_K)
    activities["HPO42-"] = P_total * a3 * davies_activity_coeff(-2, ionic_strength_molal, T_K)
    activities["PO43-"] = P_total * a4 * davies_activity_coeff(-3, ionic_strength_molal, T_K)

    activities["NH4+"] = (
        total_M("S_IN", "S_NH4") * ammonium_alpha_nh4(pH, T_K) * davies_activity_coeff(+1, ionic_strength_molal, T_K)
    )
    activities["CO32-"] = (
        total_M("S_IC") * carbonate_alpha_co3(pH, T_K) * davies_activity_coeff(-2, ionic_strength_molal, T_K)
    )
    activities["HS-"] = (
        total_M("S_IS") * sulfide_alpha_hs(pH, T_K) * davies_activity_coeff(-1, ionic_strength_molal, T_K)
    )
    return activities


def activities_from_shared_speciation(
    state_arr, cmps, unit_conversion, speciation: Dict[str, object], T_K: float
) -> Dict[str, float]:
    """Ion activities (mol/kg water) on THE shared Davies speciation the pH solve uses.

    Owner ruling 2026-10-08 (Hersh, AskUserQuestion), "Wait for two more steps", step 2: the twin's mineral saturation
    reads the speciation both engines share (``process_chemistry_reference.madm1_speciation``, pcm()) instead of this
    module's own ionic strength. Everything comes from ``speciation`` (the solve's result at ``T_K``): its ionic
    strength and Davies gammas, its acid constants, its molal basis (``molar_to_molal``) and its pH. Ca2+, Mg2+ and
    Fe2+ are the solve's FREE ions after its ion pairs (HOUSE-MADM1-SPECIATION-ION-PAIRS-FIRST-20261008); the other
    strong ions (K+, Na+, Fe3+, Al3+) pair with nothing and are free at their totals; phosphate is
    speciated by the shared H2PO4-/HPO4-2 equilibrium on the state's own S_IP whether or not the solve charged it (the
    reduced observer's exclude_sip_from_charge); PO4-3 follows HPO4-2 by the registry's third dissociation, which the
    shared solve does not carry (named: it is only the struvite/HAP/vivianite IAPs' PO4-3).
    """
    m = float(speciation["molar_to_molal"])
    log_gamma = speciation["log_gamma"]
    gamma = {z: 10.0 ** float(log_gamma[z]) for z in (1, 2, 3)}
    species = speciation["species_M"]
    pk = speciation["pKa"]
    pH = float(speciation["pH"])

    def total_M(*names: str) -> float:
        idx = _first_index(cmps, *names)
        if idx is None:
            return 0.0
        return max(0.0, float(state_arr[idx]) * float(unit_conversion[idx]))

    def free_M(key: str, *names: str) -> float:
        # the solve's FREE metal (after its ion pairs, HOUSE-MADM1-SPECIATION-ION-PAIRS-FIRST-20261008) where it
        # carries one, else the total
        return float(species[key]) if key in species else total_M(*names)

    activities: Dict[str, float] = {
        "Ca2+": free_M("Ca", "S_Ca") * m * gamma[2],
        "Mg2+": free_M("Mg", "S_Mg") * m * gamma[2],
        "Fe2+": free_M("Fe2", "S_Fe2") * m * gamma[2],
        "Fe3+": total_M("S_Fe3") * m * gamma[3],
        "Al3+": total_M("S_Al") * m * gamma[3],
        "K+": total_M("S_K") * m * gamma[1],
        "Na+": total_M("S_Na") * m * gamma[1],
        "H+": 10.0 ** (-pH),
        "OH-": float(species["OH"]) * m * gamma[1],
        "NH4+": float(species["NH4"]) * m * gamma[1],
        "CO32-": float(species["CO3"]) * m * gamma[2],
        "HS-": float(species["HS"]) * m * gamma[1],
    }
    r_hpo4 = 10.0 ** (pH - float(pk["h2po4"]) + float(log_gamma[1]) - float(log_gamma[2]))   # HPO4-2 / H2PO4-
    phosphate_from_solve = float(species.get("H2PO4", 0.0)) + float(species.get("HPO4", 0.0)) > 0.0
    if phosphate_from_solve:
        # the solve speciated the phosphate (with its ion pairs): its FREE H2PO4- / HPO4-2
        h2po4, hpo4 = float(species["H2PO4"]), float(species["HPO4"])
    else:
        # the solve left S_IP out of the charge (the reduced observer): the state's own S_IP on the same equilibrium
        h2po4 = total_M("S_IP", "S_PO4") / (1.0 + r_hpo4)
        hpo4 = h2po4 * r_hpo4
    activities["H2PO4-"] = h2po4 * m * gamma[1]
    activities["HPO42-"] = hpo4 * m * gamma[2]
    if phosphate_from_solve and "PO4" in species:
        # the shared solve's PO4-3 (the ONE Ka3 definition, process_model_schema.speciation "phosphoric_3")
        activities["PO43-"] = float(species["PO4"]) * m * gamma[3]
    else:
        # the reconstructed HPO4-2 (reduced observer: the solve carried NO phosphate, so its PO4 entry is 0) on the
        # same Ka3 definition; never the solve's empty PO4 (Codex review 2026-10-08, defect 1)
        activities["PO43-"] = activities["HPO42-"] * _ka("hpo4", T_K) / activities["H+"]
    return activities


# ---------------------------------------------------------------------------
# The mineral registry — one entry per phase, mADM1 order
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MineralPhase:
    name: str  # SI-dict key, matches _MADM1_MMP_MINERAL_NAMES
    formula: str
    reaction: str  # dissolution reaction the constants describe
    log_ksp_25: float
    dh_j_mol: Optional[float]  # None => held at 25 C value (no invented dH)
    nu: int  # ion count for the SI^(1/nu) driving force. CONVENTION: nu counts
    #   PRODUCT-side ions of the dissolution reaction; protons on the reactant
    #   (denominator) side are excluded — FeS/Mackinawite (=2) and HAP (=8, not
    #   9: the consumed H+ is not a product ion) set the precedent.
    iap: Callable[[Dict[str, float]], float]
    source: str
    #: Optional fitted log10-Ksp(T_K) expression. When a phase has a published
    #: analytic fit, it beats a single-dH van't Hoff -- use it and cite it.
    log_ksp_fn: Optional[Callable[[float], float]] = None

    def ksp(self, T_K: float) -> float:
        if self.log_ksp_fn is not None:
            return 10.0 ** self.log_ksp_fn(float(T_K))
        k25 = 10.0**self.log_ksp_25
        if self.dh_j_mol is None or T_K == T_REF_K:
            return k25
        return k25 * np.exp(
            self.dh_j_mol
            / PHREEQC_PHASE_GAS_CONSTANT_J_MOL_K
            * (1.0 / T_REF_K - 1.0 / float(T_K))
        )


def _a(act, *names_pows):
    out = 1.0
    for name, power in names_pows:
        out *= act.get(name, 0.0) ** power
    return out


MINERAL_PHASES = (
    # The Ca/Mg/K/Al set pulls constants + temperature laws from the shared
    # mineral-extension kernel (process_model_schema.mineral_extension), the
    # same definitions the water-chemistry PHREEQC identity benchmark renders
    # — 15/15 phases benchmarked at 25 and 35 C. The Ca/Mg phosphates use
    # minteq.v4's PROTON-inclusive conventions (H+ in the IAP, not HPO4/OH-),
    # with a_H2O taken as 1 in this dilute-digester registry (the benchmark
    # side uses PHREEQC's own a_H2O).
    MineralPhase(
        "CCM",
        "CaCO3 (calcite)",
        "CaCO3 = Ca2+ + CO3 2-",
        _shared_mineral("Calcite").log_k_25c,
        None,
        2,
        lambda a: _a(a, ("Ca2+", 1), ("CO32-", 1)),
        _shared_mineral("Calcite").source,
        log_ksp_fn=_shared_mineral_law("Calcite"),
    ),
    MineralPhase(
        "ACC",
        "CaCO3 (aragonite)",
        "CaCO3 = Ca2+ + CO3 2-",
        _shared_mineral("Aragonite").log_k_25c,
        None,
        2,
        lambda a: _a(a, ("Ca2+", 1), ("CO32-", 1)),
        _shared_mineral("Aragonite").source,
        log_ksp_fn=_shared_mineral_law("Aragonite"),
    ),
    MineralPhase(
        "ACP",
        "Ca3(PO4)2 (beta-TCP as ACP surrogate)",
        "Ca3(PO4)2 = 3Ca2+ + 2PO4 3-",
        _shared_mineral("TCP_beta").log_k_25c,
        _shared_mineral("TCP_beta").delta_h_j_mol,
        5,
        lambda a: _a(a, ("Ca2+", 3), ("PO43-", 2)),
        _shared_mineral("TCP_beta").source,
        log_ksp_fn=_shared_mineral_law("TCP_beta"),
    ),
    MineralPhase(
        "HAP",
        "Ca5(PO4)3OH",
        "HAP + H+ = 5Ca2+ + 3PO4 3- + H2O  (minteq.v4 convention)",
        _shared_mineral("Hydroxylapatite").log_k_25c,
        _shared_mineral("Hydroxylapatite").delta_h_j_mol,
        8,  # 5 Ca2+ + 3 PO43- product ions; the CONSUMED H+ is excluded (see nu doc)
        lambda a: (
            _a(a, ("Ca2+", 5), ("PO43-", 3)) / a["H+"] if a.get("H+", 0.0) > 0 else 0.0
        ),
        _shared_mineral("Hydroxylapatite").source,
        log_ksp_fn=_shared_mineral_law("Hydroxylapatite"),
    ),
    MineralPhase(
        "DCPD",
        "CaHPO4.2H2O (brushite)",
        "DCPD = Ca2+ + H+ + PO4 3-  (minteq.v4 convention)",
        _shared_mineral("Brushite").log_k_25c,
        _shared_mineral("Brushite").delta_h_j_mol,
        3,
        lambda a: _a(a, ("Ca2+", 1), ("H+", 1), ("PO43-", 1)),
        _shared_mineral("Brushite").source,
        log_ksp_fn=_shared_mineral_law("Brushite"),
    ),
    MineralPhase(
        "OCP",
        "Ca4H(PO4)3.3H2O",
        "OCP = 4Ca2+ + H+ + 3PO4 3-  (minteq.v4 convention)",
        _shared_mineral("OCP").log_k_25c,
        _shared_mineral("OCP").delta_h_j_mol,
        8,
        lambda a: _a(a, ("Ca2+", 4), ("H+", 1), ("PO43-", 3)),
        _shared_mineral("OCP").source,
        log_ksp_fn=_shared_mineral_law("OCP"),
    ),
    MineralPhase(
        "struv",
        "MgNH4PO4.6H2O",
        "struvite = Mg2+ + NH4+ + PO4 3-",
        _shared_mineral("Struvite").log_k_25c,
        _shared_mineral("Struvite").delta_h_j_mol,
        3,
        lambda a: _a(a, ("Mg2+", 1), ("NH4+", 1), ("PO43-", 1)),
        _shared_mineral("Struvite").source,
        log_ksp_fn=_shared_mineral_law("Struvite"),
    ),
    MineralPhase(
        "newb",
        "MgHPO4.3H2O (newberyite)",
        "newb = Mg2+ + H+ + PO4 3-  (minteq.v4 convention)",
        _shared_mineral("Newberyite").log_k_25c,
        _shared_mineral("Newberyite").delta_h_j_mol,
        3,
        lambda a: _a(a, ("Mg2+", 1), ("H+", 1), ("PO43-", 1)),
        _shared_mineral("Newberyite").source,
        log_ksp_fn=_shared_mineral_law("Newberyite"),
    ),
    MineralPhase(
        "magn",
        "MgCO3 (magnesite)",
        "MgCO3 = Mg2+ + CO3 2-",
        _shared_mineral("Magnesite").log_k_25c,
        _shared_mineral("Magnesite").delta_h_j_mol,
        2,
        lambda a: _a(a, ("Mg2+", 1), ("CO32-", 1)),
        _shared_mineral("Magnesite").source,
        log_ksp_fn=_shared_mineral_law("Magnesite"),
    ),
    MineralPhase(
        "kstruv",
        "MgKPO4.6H2O",
        "K-struvite = Mg2+ + K+ + PO4 3-",
        _shared_mineral("K_struvite").log_k_25c,
        _shared_mineral("K_struvite").delta_h_j_mol,
        3,
        lambda a: _a(a, ("Mg2+", 1), ("K+", 1), ("PO43-", 1)),
        _shared_mineral("K_struvite").source,
        log_ksp_fn=_shared_mineral_law("K_struvite"),
    ),
    MineralPhase(
        "FeS",
        "FeS (amorphous)",
        "FeS + H+ = Fe2+ + HS-  (proton convention)",
        _shared_phase("FeS_amorphous").log_k_25c,
        _shared_phase("FeS_amorphous").delta_h_j_mol,
        2,
        lambda a: _a(a, ("Fe2+", 1), ("HS-", 1)) / a["H+"] if a.get("H+", 0.0) > 0 else 0.0,
        _shared_phase("FeS_amorphous").source,
    ),
    MineralPhase(
        "Mackinawite",
        "FeS (mackinawite)",
        "FeS + H+ = Fe2+ + HS-  (proton convention)",
        _shared_phase("Mackinawite").log_k_25c,
        _shared_phase("Mackinawite").delta_h_j_mol,
        2,
        lambda a: _a(a, ("Fe2+", 1), ("HS-", 1)) / a["H+"] if a.get("H+", 0.0) > 0 else 0.0,
        _shared_phase("Mackinawite").source,
    ),
    MineralPhase(
        "Fe3PO42",
        "Fe3(PO4)2.8H2O (vivianite)",
        "vivianite = 3Fe2+ + 2PO4 3-",
        _shared_phase("Vivianite").log_k_25c,
        _shared_phase("Vivianite").delta_h_j_mol,
        5,
        lambda a: _a(a, ("Fe2+", 3), ("PO43-", 2)),
        _shared_phase("Vivianite").source,
    ),
    MineralPhase(
        "Siderite",
        "FeCO3 (siderite)",
        "siderite = Fe2+ + CO3 2-",
        _shared_phase("Siderite").log_k_25c,
        _shared_phase("Siderite").delta_h_j_mol,
        2,
        lambda a: _a(a, ("Fe2+", 1), ("CO32-", 1)),
        _shared_phase("Siderite").source,
    ),
    MineralPhase(
        "AlPO4",
        "AlPO4 (amorphous)",
        "AlPO4 = Al3+ + PO4 3-",
        _shared_mineral("AlPO4_am").log_k_25c,
        _shared_mineral("AlPO4_am").delta_h_j_mol,
        2,
        lambda a: _a(a, ("Al3+", 1), ("PO43-", 1)),
        _shared_mineral("AlPO4_am").source,
        log_ksp_fn=_shared_mineral_law("AlPO4_am"),
    ),
)

MINERAL_BY_NAME = {p.name: p for p in MINERAL_PHASES}


def mineral_nu_array() -> np.ndarray:
    """The mineral nu vector in mADM1 order (single source for
    the crystallization driving-force exponent — the old hardcoded copy in
    madm1.py carried nu=2 for vivianite and proton-convention counts for
    DCPD/newberyite that did not match the live IAPs). nu counts product-side
    ions of each dissolution reaction; denominator (consumed) protons are
    excluded — so DCPD/newberyite count their RELEASED proton (nu=3) while
    HAP does not count its consumed one (nu=8)."""
    return np.array([p.nu for p in MINERAL_PHASES], dtype=float)


# ---------------------------------------------------------------------------
# Main Saturation Index Calculator (public signature unchanged)
# ---------------------------------------------------------------------------
def calc_saturation_indices(
    state_arr,
    cmps,
    pH,
    T_K,
    unit_conversion,
    *,
    recommendation_mode: bool = False,
    species_coverage_fraction: Optional[float] = None,
    speciation: Optional[Dict[str, object]] = None,
    speciation_temperature_k: Optional[float] = None,
):
    """SI = IAP / Ksp per phase. >1 supersaturated, <1 undersaturated.

    Existing dynamic simulations remain diagnostic by default.  A caller that
    intends to use the calculation in a dose recommendation must opt into
    ``recommendation_mode`` and supply the fraction of material dissolved
    species covered by this reduced activity model.  Missing coverage or an
    out-of-domain result is fatal; it is never reduced to a warning.

    ``speciation`` (the shared Davies solve's result, pcm_speciation(); its temperature ``speciation_temperature_k``):
    the activities, ionic strength and pH are that solve's (``activities_from_shared_speciation``); ``pH`` must be its
    pH. Without it -- a non-mADM1 state (the ammonia stripper's ASM liquor) or a caller-fixed pH diagnostic -- this
    module's own reduced ionic strength and ideal fractions answer, as before.
    """
    if speciation is not None:
        if speciation_temperature_k is None:
            raise ValueError("a shared speciation carries its own temperature: pass speciation_temperature_k")
        if not np.isclose(float(pH), float(speciation["pH"]), rtol=0.0, atol=1e-12):
            raise ValueError(f"pH {pH!r} is not the shared speciation's pH {speciation['pH']!r}")
        ionic_strength_molal = float(speciation["ionic_strength_molal"])
    else:
        ionic_strength_molal = ionic_strength(
            state_arr,
            cmps,
            unit_conversion,
            pH=pH,
            T_K=T_K,
        )
    if recommendation_mode:
        if species_coverage_fraction is None:
            raise ActivityModelOutOfDomainError(
                davies_activity_domain_report(
                    ionic_strength_molal=ionic_strength_molal,
                    temperature_c=float(T_K) - 273.15,
                    species_coverage_fraction=0.0,
                )
            )
        require_davies_activity_domain(
            davies_activity_domain_report(
                ionic_strength_molal=ionic_strength_molal,
                temperature_c=float(T_K) - 273.15,
                species_coverage_fraction=species_coverage_fraction,
            )
        )
    if speciation is not None:
        activities = activities_from_shared_speciation(state_arr, cmps, unit_conversion, speciation,
                                                       float(speciation_temperature_k))
    else:
        activities = calc_activities(state_arr, cmps, pH, ionic_strength_molal, unit_conversion, T_K=T_K)
    SI = {}
    for phase in MINERAL_PHASES:
        ksp = require_positive_finite_ksp(phase, T_K)
        iap = float(phase.iap(activities))
        if not np.isfinite(iap) or iap < 0.0:
            raise InvalidThermodynamicConstantError(f"phase {phase.name!r} returned invalid IAP={iap!r} at T={T_K!r} K")
        SI[phase.name] = iap / ksp
    return SI
