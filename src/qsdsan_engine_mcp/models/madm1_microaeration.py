# -*- coding: utf-8 -*-
'The QSDsan mADM1 twin\'s binding of the OPT-IN digester micro-aeration extension (plan step 2, item F).\n\nRuling PUBLIC-RULE-6dce80fc160d: H2S oxidation by dosed O2 is modelled INSIDE the digester as an\nopt-in, NON-governing bracket model. The kinetics are written once, in\n``process_model_schema.madm1_microaeration.MADM1_MICROAERATION_EXTENSION`` (twin processes 76-85, liquid S_O2, gas\nS_o2 and S_n2, the O2-inhibition factor L2); the WaterTAP S/Fe digester compiles the same object\n(``watertap-engine-mcp chemistry/modified_adm1_microaeration.py``). Nothing here restates a rate law, a coefficient or\na constant: every rate is the extension\'s own ``Expression`` evaluated by the shared arithmetic\n(``process_chemistry_reference.evaluator``) at symbols this module binds from the twin\'s OWN state, speciation,\nheadspace and live parameters. ``tests/test_madm1_microaeration_parity.py`` holds the twin to the reference evaluator.\n\nWhat the variant adds to the twin\n---------------------------------\n* liquid ``S_O2`` (kg O2/m3, COD -1), immediately BEFORE H2O and after X_Sorg, so every core index stays put;\n* two headspace states after the four base gases, at NAMED positions (:data:`MICROAERATION_GAS_STATE_INDEX`), never\n  "the last N": ``S_o2`` (kmol O2/m3) and ``S_n2`` (kmol N2/m3; argon lumped with N2 by moles; N2 has no liquid\n  partner because dissolved N2 is omitted, so it is inert: out = in);\n* rates 76-85 appended after organic sulfur\'s 75 (:data:`MICROAERATION_RATE_SLICE`);\n* the L2 factor on exactly ``O2_INHIBITED_PROCESS_INDICES``;\n* the derived symbols the kernel asks each engine for (``MICROAERATION_DERIVED_SYMBOLS``): ``p_o2_bar`` from the\n  headspace state with the twin\'s own gas constant, ``KH_o2_T`` by the kernel\'s van\'t Hoff form with the kernel\'s\n  R_gas and T_base, ``a_wet_m2_per_m3`` from a wetted area the CALLER states (never defaulted: a rate call without\n  one is refused), ``gas_smoothing`` = 1e-30 (the twin\'s exact law).\n\nRate INPUTS are zero-clamped (the twin\'s convention for every inventory a rate is read from, ``rhos_madm1``): a BDF\nmicro-undershoot of S_O2 or of the O2 partial pressure below zero must not flip a Monod term\'s sign or leave its\ndomain. On any non-negative trajectory the clamp changes nothing.'

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np

from puran_mcp_support.chemistry.evaluator import evaluate
from puran_mcp_support.schema.madm1_microaeration import BASE_GAS_COMPONENTS, DEPOSIT_COMPONENT, G1_REACTION_ENTHALPY_J_MOL, MADM1_MICROAERATION_EXTENSION, OXYGEN_DEMAND, S_O2, _symbols, microaeration_parameter_values
from puran_mcp_support.schema.madm1_organic_sulfur import MADM1_ORGANIC_SULFUR_EXTENSION

__all__ = [
    "EXT",
    "EXT_PROCESS_IDS",
    "MICROAERATION_GAS_STATE_IDS",
    "MICROAERATION_GAS_STATE_INDEX",
    "MICROAERATION_GAS_STATE_SLICE",
    "MICROAERATION_RATE_SLICE",
    "MADM1_BASE_GAS_STATE_SLICE",
    "MicroaerationGeometryError",
    "MicroaerationHeadspace",
    "OXIDANT_KEYS",
    "OXIDANT_REFUSED_SPECIES",
    "OXIDANT_SPECIES",
    "OXIDANT_SPECIES_TO_GAS_STATE",
    "TWIN_BASE_PARAMETER_BINDINGS",
    "TWIN_DERIVED_SYMBOLS",
    "TWIN_GAS_SMOOTHING",
    "bind_headspace_wetted_area",
    "microaeration_components",
    "microaeration_flag",
    "microaeration_headspace",
    "microaeration_headspace_report",
    "microaeration_o2_inhibition_factor",
    "microaeration_processes",
    "microaeration_rate_parameters",
    "microaeration_rates",
    "microaeration_values",
    "resolve_oxidant_inlet",
]

EXT = MADM1_MICROAERATION_EXTENSION

#: The twin's binding of the kernel's ``gas_smoothing`` (bar): 1e-30, numerically its exact law (the kernel's own
#: statement for the twin; a Newton engine binds a value it can differentiate through).
TWIN_GAS_SMOOTHING = 1e-30

# ------------------------------------------------------------------------------------------------ named positions
#: The variant's headspace states, in state order, by the kernel's gas ids: the four base gases, then the extension's.
MICROAERATION_GAS_STATE_IDS = tuple(g.component_id for g in BASE_GAS_COMPONENTS + EXT.gas_components)
#: Position of each gas inside the headspace block ``state[n_cmps : n_cmps + 6]`` (S_o2 4, S_n2 5). NAMED, as the
#: gas-transfer rate positions are (plan step 0): nothing reads "the last two".
MICROAERATION_GAS_STATE_INDEX = {gid: i for i, gid in enumerate(MICROAERATION_GAS_STATE_IDS)}
#: The four base gases inside the headspace block (H2, CH4, CO2, H2S), and the extension's two after them.
MADM1_BASE_GAS_STATE_SLICE = slice(0, len(BASE_GAS_COMPONENTS))
MICROAERATION_GAS_STATE_SLICE = slice(len(BASE_GAS_COMPONENTS), len(MICROAERATION_GAS_STATE_IDS))
#: The extension's rate positions (76-85): after the four gas transfers (71-74) and organic sulfur's 75.
MICROAERATION_RATE_SLICE = slice(EXT.processes[0].index, EXT.processes[-1].index + 1)

#: The extension's process ids in rate order (76-85).
EXT_PROCESS_IDS = tuple(p.process_id for p in EXT.processes)

_GAS = {g.component_id: g for g in BASE_GAS_COMPONENTS + EXT.gas_components}
_G1 = next(p for p in EXT.processes if p.phase == "gas_surface")
_G4 = next(p for p in EXT.processes if p.phase == "gas_liquid_transfer")
_EXT_PARAMETER_NAMES = frozenset(p.name for p in EXT.parameters)

#: The derived symbols the twin binds for the extension's rates (the kernel's ``MICROAERATION_DERIVED_SYMBOLS`` plus
#: the two S/Fe derived symbols its rates read).
TWIN_DERIVED_SYMBOLS = ("H_ion", "p_h2s_bar", *EXT.derived_symbols)

#: The oxidant statement is the ONE the WaterTAP digester takes (watertap-engine-mcp
#: ``chemistry/modified_adm1_microaeration.py``: ``OXIDANT_KEYS``, ``OXIDANT_SPECIES``, ``OXIDANT_REFUSED_SPECIES``; a
#: test reads that file and holds the three equal): ``{"kmol_d": {O2, N2, Ar, CO2}, "temperature_K", "pressure_Pa",
#: "source"}``, a DRY gas by molar flow. The wetted headspace area is geometry with its own argument, never a field of
#: the gas. Species -> the headspace state each enters; ARGON IS LUMPED WITH N2 BY MOLES (the kernel's statement).
OXIDANT_KEYS = frozenset({"kmol_d", "temperature_K", "pressure_Pa", "source"})
OXIDANT_SPECIES_TO_GAS_STATE = {"O2": "S_o2", "N2": "S_n2", "Ar": "S_n2", "CO2": "S_co2"}
OXIDANT_SPECIES = tuple(OXIDANT_SPECIES_TO_GAS_STATE)
OXIDANT_REFUSED_SPECIES = ("H2O", "CH4", "H2", "H2S")


class MicroaerationGeometryError(RuntimeError):
    """A micro-aeration rate was asked for without a stated wetted headspace area."""


def microaeration_flag(value: Any) -> bool:
    """The micro-aeration opt-in, fail-closed: only ``True`` compiles the extension, ``None``/``False`` leave the
    model as it was; anything else (a string, a 1) is refused rather than guessed."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    raise TypeError(f"microaeration must be True or False, got {value!r}")


# ------------------------------------------------------------------------------------------------------ components
def microaeration_components(contents: Optional[tuple] = None) -> list:
    """The extension's liquid components as QSDsan components, built from the extension's own contents: dissolved
    oxygen, kg O2/m3, no C/N/P/K/Mg, at the kernel's oxygen demand (-1 kg COD per kg O2). A content the twin has no
    QSDsan chemical for is refused (``contents`` defaults to the extension's)."""
    from qsdsan import Component

    built = []
    for content in (EXT.contents if contents is None else contents):
        if content.component_id != S_O2 or content.measured_as != "O2":
            raise RuntimeError(f"the twin builds dissolved O2 only; the extension states {content!r}")
        # the chemical is named: QSDsan resolves the bare ID 'S_O2' to sulfur dioxide (formula O2S)
        cmp = Component.from_chemical(content.component_id, chemical="O2",
                                      description="Dissolved oxygen (madm1_microaeration extension)",
                                      particle_size="Dissolved gas", degradability="Undegradable", organic=False)
        for element in ("C", "N", "P", "K", "Mg"):
            setattr(cmp, f"i_{element}", content.content(element))
        cmp.i_COD = float(OXYGEN_DEMAND[content.component_id])
        built.append(cmp)
    return built


# ------------------------------------------------------------------------------------------------------ parameters
def microaeration_values(overrides: Optional[Mapping[str, float]] = None) -> dict:
    """The S/Fe kernel's and the extension's values with ``overrides`` applied to the EXTENSION's own parameters only
    (a bracket edge: ``k_S0_red``, ``phi_liq`` ...). A base-kernel parameter is the model's own argument (``k_su``,
    ``KS_IN`` ...), so naming one here is refused: the base rates would not have moved with it."""
    overrides = dict(overrides or {})
    foreign = sorted(set(overrides) - _EXT_PARAMETER_NAMES)
    if foreign:
        raise ValueError(f"microaeration_parameters may set only the extension's own parameters; {foreign} are "
                         "not (a base-kernel parameter is ModifiedADM1's own argument)")
    for name, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"microaeration_parameters[{name!r}] must be a finite number, got {value!r}")
    return microaeration_parameter_values(overrides, extension=EXT)


def _kernel_contents() -> dict:
    from puran_mcp_support.schema.madm1_sfe_kinetics import MADM1_SFE_KERNEL

    return {c.component_id: c for c in (MADM1_SFE_KERNEL.components + MADM1_ORGANIC_SULFUR_EXTENSION.contents
                                        + EXT.contents)}


def microaeration_processes(cmps: Any, values: Mapping[str, float], *, processes: Optional[tuple] = None) -> list:
    """The extension's processes as QSDsan processes, in the extension's order: each LIQUID coefficient is the
    extension's own expression evaluated at ``values``; the conserved carriers (S_IC, S_IN, S_IP) are closed by
    QSDsan from the component contents (``conserved_for``), as the core table's are, for exactly the elements a row
    moves (the kernel's validator refuses a row that states a carrier). The gas and deposit coefficients are not
    liquid stoichiometry: :func:`microaeration_headspace` carries them to the reactor's headspace balance.
    ``processes`` defaults to the extension's."""
    from puran_mcp_support.schema.madm1_sfe_kinetics import MADM1_SFE_KERNEL
    from qsdsan import Process

    carriers = dict(MADM1_SFE_KERNEL.conserved_carriers)                 # element -> carrier id
    contents = _kernel_contents()
    built = []
    for p in (EXT.processes if processes is None else processes):
        reaction: dict = {cid: float(evaluate(coef, values)) for cid, coef in p.stoichiometry.items()}
        moved = tuple(element for element in carriers
                      if any(contents[cid].content(element) != 0.0 for cid in reaction))
        reference = p.reference_component
        if reference in reaction:
            # QSDsan normalises a row by |coefficient of its reference|: the kernel's rows are per unit of the
            # reference already, so anything but -1 here would silently rescale the row against its rate
            if reaction[reference] != -1.0:
                raise RuntimeError(f"{p.process_id}: the reference {reference} carries {reaction[reference]!r}, "
                                   "not -1")
            for element in moved:
                reaction[carriers[element]] = "?"
            built.append(Process(p.process_id, reaction=reaction, ref_component=reference,
                                 components=cmps, conserved_for=moved, parameters=()))
            continue
        # The reference is a GAS (G1: per kg S of headspace H2S oxidised), so no liquid coefficient is the unit and
        # QSDsan must not normalise the row. Its liquid products carry no C, N or P, so nothing is left to close: the
        # row is stated directly (the pattern of this model's host-reduction rows), on a liquid display reference.
        if reference in cmps.IDs or moved:
            raise RuntimeError(f"{p.process_id}: a row without its reference must be a gas-referenced row that "
                               f"moves no C/N/P (reference {reference!r}, moves {moved})")
        display = next(iter(reaction))
        process = Process(p.process_id, reaction={display: 1.0}, ref_component=display,
                          components=cmps, conserved_for=(), parameters=())
        row = np.zeros(len(cmps), dtype=float)
        for cid, coefficient in reaction.items():
            row[cmps.index(cid)] = coefficient
        process._stoichiometry = row
        process._reaction = dict(reaction)
        built.append(process)
    return built


# ------------------------------------------------------------------------- the twin's live base-kernel parameters
def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator)


#: Base-kernel parameters the extension's rate laws read, each bound to the twin's LIVE value (the rate function's own
#: params), so an override or a temperature adjustment of ``k_su`` or ``KS_IN`` reaches the aerobic uptakes as it
#: reaches the anaerobic ones. ``rate_constants`` is (q_ch_hyd, q_pr_hyd, q_li_hyd, k_su, k_aa, k_fa, ...);
#: ``params['KS_IN']`` is KS_IN x MW_N (kg N/m3) and ``pH_limits`` is (lower limits, upper limits) with the acidogens
#: first; ``KIs_h2`` is (fa, c4, c4, pro).
TWIN_BASE_PARAMETER_BINDINGS = {
    "k_su": lambda params, kernel: float(params["rate_constants"][3]),
    "k_aa": lambda params, kernel: float(params["rate_constants"][4]),
    "k_fa": lambda params, kernel: float(params["rate_constants"][5]),
    "KS_IN": lambda params, kernel: _ratio(params["KS_IN"], kernel["MW_N"]),
    "KS_IP": lambda params, kernel: _ratio(params["KS_IP"], kernel["MW_P"]),
    "MW_N": lambda params, kernel: float(kernel["MW_N"]),
    "MW_P": lambda params, kernel: float(kernel["MW_P"]),
    "pH_LL_aa": lambda params, kernel: float(params["pH_limits"][0][0]),
    "pH_UL_aa": lambda params, kernel: float(params["pH_limits"][1][0]),
    "KI_h2_fa": lambda params, kernel: float(params["KIs_h2"][0]),
}


def microaeration_rate_parameters(cmps: Any, values: Mapping[str, float], *, live_params: Mapping[str, Any],
                                  expected_base: Mapping[str, float], preceding_rate_count: int) -> dict:
    """What ``rhos_madm1`` needs to evaluate the extension (stored as ``params['microaeration']``).

    Fail-closed on a kernel the twin does not bind: every symbol a rate or the inhibition reads must be the
    extension's own parameter, a liquid component of this set, a derived symbol in :data:`TWIN_DERIVED_SYMBOLS`, or a
    base parameter in :data:`TWIN_BASE_PARAMETER_BINDINGS`; the first extension rate must follow
    ``preceding_rate_count`` rates (core + organic sulfur) directly; and each live binding must read back the value
    the model was built with (``expected_base``), so a reordered rate-constant tuple cannot hand k_aa to the sugars.
    """
    read = set(_symbols(EXT.inhibition)).union(*(_symbols(p.rate) for p in EXT.processes))
    liquid = set(cmps.IDs)
    base_read = sorted(read - _EXT_PARAMETER_NAMES - liquid - set(TWIN_DERIVED_SYMBOLS))
    unbound = [name for name in base_read if name not in TWIN_BASE_PARAMETER_BINDINGS]
    if unbound:
        raise RuntimeError(f"the micro-aeration extension reads {unbound}, which the twin does not bind")
    for offset, p in enumerate(EXT.processes):
        if p.index != preceding_rate_count + offset:
            raise RuntimeError(f"{p.process_id}: index {p.index} does not follow the {preceding_rate_count} rates "
                               "before it (core + organic sulfur)")
    kernel = {"MW_N": float(values["MW_N"]), "MW_P": float(values["MW_P"])}
    for name in base_read:
        got = TWIN_BASE_PARAMETER_BINDINGS[name](live_params, kernel)
        want = float(expected_base[name])
        if not math.isclose(got, want, rel_tol=1e-12, abs_tol=0.0):
            raise RuntimeError(f"the twin's live binding of {name} reads {got!r}, the model was built with {want!r}")
    inhibited = tuple(int(i) for i in EXT.inhibited_process_indices)
    return {
        "extension_id": EXT.extension_id,
        "sha256": EXT.sha256,
        "values": {name: float(values[name]) for name in _EXT_PARAMETER_NAMES},
        "kernel": {**kernel, "R_gas": float(values["R_gas"])},
        "base_parameters": tuple(base_read),
        "state_symbols": tuple((cid, int(cmps.index(cid))) for cid in sorted(read & liquid)),
        "s_o2_index": int(cmps.index(S_O2)),
        "inhibited_indices": np.asarray(inhibited, dtype=int),
        "rate_offset": int(preceding_rate_count),
        # GEOMETRY, stated by the caller through the reactor (bind_headspace_wetted_area); never defaulted
        "a_wet_m2_per_m3": None,
    }


def bind_headspace_wetted_area(params: dict, *, headspace_wetted_area_m2: float, liquid_volume_m3: float) -> float:
    """Bind the kernel's ``a_wet_m2_per_m3`` = A_wet / V_liq (the wetted headspace surface per m3 of liquor: the
    liquid surface plus the wetted wall band, a sizing input a rating never invents). Returns the bound value."""
    block = params.get("microaeration") if isinstance(params, dict) else None
    if block is None:
        raise ValueError("the model does not compile the micro-aeration extension: there is no wetted area to bind")
    area, volume = _finite(headspace_wetted_area_m2, "headspace_wetted_area_m2"), _finite(liquid_volume_m3, "V_liq")
    if area < 0.0 or volume <= 0.0:
        raise ValueError("headspace_wetted_area_m2 must be >= 0 and the liquid volume > 0")
    block["a_wet_m2_per_m3"] = area / volume
    return block["a_wet_m2_per_m3"]


# ----------------------------------------------------------------------------------------------------------- rates
def microaeration_o2_inhibition_factor(state_arr: Any, block: Mapping[str, Any]) -> float:
    """L2: the extension's own inhibition expression at the (zero-clamped) dissolved O2. Exactly 1.0 at S_O2 = 0."""
    s_o2 = max(float(state_arr[block["s_o2_index"]]), 0.0)
    return float(evaluate(EXT.inhibition, {**block["values"], S_O2: s_o2}))


def _henry_o2_kg_m3_bar(block: Mapping[str, Any], T_base: float, T_op: float) -> float:
    """KH_o2_T on the twin's liquid basis (kg O2/m3/bar): KH_o2 x MW_O2 by the kernel's van't Hoff form
    log KH = log KH0 + dH/R (1/T_base - 1/T), with the KERNEL's R_gas (the constant dH_KH_o2 is stated against)."""
    v = block["values"]
    return v["KH_o2"] * v["MW_O2"] * math.exp(v["dH_KH_o2"] / block["kernel"]["R_gas"] * (1.0 / T_base - 1.0 / T_op))


def microaeration_rates(state_arr: Any, params: Mapping[str, Any], T_op: float, pH: float,
                        gas_constant_bar_m3_kmol_K: float):
    """The extension's ten rates (twin 76-85, kg reference component per m3 of LIQUID per day) at the twin's own
    state, and the symbols they were evaluated at (the twin's diagnostics). Refuses without a stated wetted area."""
    block = params["microaeration"]
    a_wet = block["a_wet_m2_per_m3"]
    if a_wet is None:
        raise MicroaerationGeometryError(
            "micro-aeration: no wetted headspace area is bound (a geometry input the caller states: "
            "AnaerobicCSTRmADM1.set_headspace_oxidant(headspace_wetted_area_m2=...)); it is never defaulted")
    cmps = params["components"]
    n_cmps = len(cmps)
    rt = gas_constant_bar_m3_kmol_K * T_op
    symbols = dict(block["values"])
    kernel = block["kernel"]
    for name in block["base_parameters"]:
        symbols[name] = TWIN_BASE_PARAMETER_BINDINGS[name](params, kernel)
    for cid, index in block["state_symbols"]:
        symbols[cid] = max(float(state_arr[index]), 0.0)                 # the zero-clamp on rate inputs
    derived = {
        "H_ion": 10.0 ** (-float(pH)),
        # the H2S partial pressure as the transfer (process 74) reads it; G1's own smoothing takes care of p <= 0
        "p_h2s_bar": rt * float(state_arr[n_cmps + MICROAERATION_GAS_STATE_INDEX["S_h2s"]]),
        "p_o2_bar": max(rt * float(state_arr[n_cmps + MICROAERATION_GAS_STATE_INDEX["S_o2"]]), 0.0),
        "KH_o2_T": _henry_o2_kg_m3_bar(block, float(params["T_base"]), float(T_op)),
        "a_wet_m2_per_m3": float(a_wet),
        "gas_smoothing": TWIN_GAS_SMOOTHING,
    }
    symbols.update(derived)
    rates = np.asarray([evaluate(p.rate, symbols) for p in EXT.processes], dtype=float)
    return rates, derived


# ------------------------------------------------------------------------------------------------------- headspace
@dataclass(frozen=True)
class MicroaerationHeadspace:
    """The gas side of the extension for the reactor's headspace balance.

    ``gas_state_ids`` are the six headspace states in state order; ``rate_indices`` the extension rates with a gas
    row (76 G1, 77 G4); ``molar_matrix[i, j]`` kmol of gas state ``i`` formed per kg reference component of rate
    ``rate_indices[j]`` (the extension's gas coefficient over the gas's molar mass); ``molar_mass`` kg per kmol on
    each gas's measured basis."""

    gas_state_ids: tuple
    base_gas_state_count: int
    rate_indices: tuple
    molar_matrix: Any
    molar_mass: Mapping[str, float]
    g1_rate_index: int
    o2_transfer_rate_index: int
    deposit_kg_s_per_kg_s: float
    liquid_s0_kg_s_per_kg_s: float
    sulfate_kg_s_per_kg_s: float
    o2_kg_per_kg_s: float
    f_so4: float
    extension_id: str
    sha256: str

    def index(self, gas_id: str) -> int:
        return MICROAERATION_GAS_STATE_INDEX[gas_id]


def microaeration_headspace(values: Mapping[str, float], *, biogas_ids: tuple,
                            base_molar_conversion: Mapping[str, float]) -> MicroaerationHeadspace:
    """Build the headspace description from the extension's gas stoichiometry.

    ``biogas_ids`` are the twin's four base gas states by liquid partner (``ModifiedADM1._biogas_IDs``) and must be
    the kernel's base gases in order; ``base_molar_conversion`` is the reactor's exact kmol-per-kg map for them
    (``models.reactors.madm1_gas_mass_to_molar_conversion``): a gas row on a BASE gas (G1 consumes H2S) converts at
    the reactor's own value, which must be the kernel's molar mass."""
    partners = tuple(g.liquid_partner for g in BASE_GAS_COMPONENTS)
    if tuple(biogas_ids) != partners:
        raise RuntimeError(f"the twin's base gas states {tuple(biogas_ids)} are not the kernel's {partners}")
    molar_mass = {}
    for g in BASE_GAS_COMPONENTS:
        own = 1.0 / float(base_molar_conversion[g.liquid_partner])
        molar_mass[g.component_id] = own
    kernel_h2s = float(_GAS["S_h2s"].molar_mass)
    if not math.isclose(molar_mass["S_h2s"], kernel_h2s, rel_tol=1e-12, abs_tol=0.0):
        raise RuntimeError(f"H2S converts at {molar_mass['S_h2s']!r} kg S/kmol in the twin, {kernel_h2s!r} in the "
                           "kernel: G1's sulfur would not close")
    for g in EXT.gas_components:
        molar_mass[g.component_id] = float(values[{"S_o2": "MW_O2", "S_n2": "MW_N2"}[g.component_id]])
        if not math.isclose(molar_mass[g.component_id], float(g.molar_mass), rel_tol=1e-12, abs_tol=0.0):
            raise RuntimeError(f"{g.component_id}: molar mass parameter and gas component disagree")
    gas_processes = [p for p in EXT.processes if p.gas_stoichiometry]
    matrix = np.zeros((len(MICROAERATION_GAS_STATE_IDS), len(gas_processes)), dtype=float)
    for column, p in enumerate(gas_processes):
        for gas_id, coefficient in p.gas_stoichiometry.items():
            matrix[MICROAERATION_GAS_STATE_INDEX[gas_id], column] = (
                float(evaluate(coefficient, values)) / molar_mass[gas_id])

    def coefficient(mapping, key):
        return float(evaluate(mapping[key], values)) if key in mapping else 0.0

    return MicroaerationHeadspace(
        gas_state_ids=MICROAERATION_GAS_STATE_IDS,
        base_gas_state_count=len(BASE_GAS_COMPONENTS),
        rate_indices=tuple(int(p.index) for p in gas_processes),
        molar_matrix=matrix,
        molar_mass=molar_mass,
        g1_rate_index=int(_G1.index),
        o2_transfer_rate_index=int(_G4.index),
        deposit_kg_s_per_kg_s=coefficient(_G1.deposit_stoichiometry, DEPOSIT_COMPONENT),
        liquid_s0_kg_s_per_kg_s=coefficient(_G1.stoichiometry, "S_S0"),
        sulfate_kg_s_per_kg_s=coefficient(_G1.stoichiometry, "S_SO4"),
        o2_kg_per_kg_s=-coefficient(_G1.gas_stoichiometry, "S_o2"),
        f_so4=float(values["f_SO4"]),
        extension_id=EXT.extension_id,
        sha256=EXT.sha256,
    )


# ---------------------------------------------------------------------------------------------------- oxidant inlet
def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def resolve_oxidant_inlet(oxidant_inlet: Any, *, headspace_wetted_area_m2: Any) -> dict:
    """The dosed oxidant as the reactor takes it: ``{"headspace_wetted_area_m2": m2, "gas_inflow_kmol_d": {gas state:
    kmol/d}, "argon_per_nitrogen_state_mol_mol": share}``.

    ``oxidant_inlet`` is the statement the WaterTAP digester takes (:data:`OXIDANT_KEYS`): ``kmol_d`` over
    O2 / N2 / Ar / CO2 (molar flows of a DRY gas; water, a fuel or H2S is refused by name), ``temperature_K``,
    ``pressure_Pa`` and ``source``. The twin's headspace balance is molar, so the gas's own temperature and pressure
    do not enter its arithmetic; they are part of the one statement and are checked, not used. Argon is lumped into
    the N2 state by moles. ``headspace_wetted_area_m2`` is REQUIRED and never defaulted: the digester's own geometry
    (liquid surface + wetted wall band).

    ONE difference from the WaterTAP statement, on purpose: the twin accepts a dose of ZERO (every molar flow 0), a
    stated dose of nothing. WaterTAP refuses it because its solve ramps the dose in from zero itself; the twin is
    integrated at whatever dose it is given, and the zero-dose identity with the organic-sulfur model needs it."""
    if headspace_wetted_area_m2 is None:
        raise ValueError("a micro-aeration run states no wetted headspace area: headspace_wetted_area_m2 is the "
                         "digester's own geometry (liquid surface + wetted wall band) and is never defaulted")
    area = _finite(headspace_wetted_area_m2, "headspace_wetted_area_m2")
    if area < 0.0:
        raise ValueError("headspace_wetted_area_m2 must be >= 0")
    if not isinstance(oxidant_inlet, Mapping):
        raise ValueError(f"a micro-aeration run states its oxidant inlet as a mapping of {sorted(OXIDANT_KEYS)}; "
                         f"got {oxidant_inlet!r}")
    unknown, absent = sorted(set(oxidant_inlet) - OXIDANT_KEYS), sorted(OXIDANT_KEYS - set(oxidant_inlet))
    if unknown or absent:
        raise ValueError(f"an oxidant inlet states exactly {sorted(OXIDANT_KEYS)}; unknown {unknown}, absent {absent} "
                         "(the headspace wetted area is geometry with its own argument, never a field of the gas)")
    if not str(oxidant_inlet["source"] or "").strip():
        raise ValueError("an oxidant inlet names its source (the stream or the sizing that states the dose)")
    flows = oxidant_inlet["kmol_d"]
    if not isinstance(flows, Mapping) or not flows:
        raise ValueError("oxidant_inlet['kmol_d'] is a mapping of species to molar flow (kmol/d)")
    refused = sorted(set(flows) & set(OXIDANT_REFUSED_SPECIES))
    if refused:
        raise ValueError(f"an oxidant inlet is a dry O2/N2/Ar(/CO2) gas; it carries {refused}, refused by name")
    stray = sorted(set(flows) - set(OXIDANT_SPECIES))
    if stray:
        raise ValueError(f"oxidant species {stray} are not {list(OXIDANT_SPECIES)}")
    kmol = {species: _finite(flows.get(species, 0.0), f"oxidant_inlet['kmol_d'][{species!r}]")
            for species in OXIDANT_SPECIES}
    if any(value < 0.0 for value in kmol.values()):
        raise ValueError(f"oxidant molar flows are non-negative, not {kmol}")
    temperature = _finite(oxidant_inlet["temperature_K"], "oxidant_inlet['temperature_K']")
    pressure = _finite(oxidant_inlet["pressure_Pa"], "oxidant_inlet['pressure_Pa']")
    if temperature <= 0.0 or pressure <= 0.0:
        raise ValueError("oxidant_inlet temperature_K and pressure_Pa must be positive")
    inflow = {gid: 0.0 for gid in MICROAERATION_GAS_STATE_IDS}
    for species, value in kmol.items():
        inflow[OXIDANT_SPECIES_TO_GAS_STATE[species]] += value
    lumped = kmol["N2"] + kmol["Ar"]
    return {"headspace_wetted_area_m2": area, "gas_inflow_kmol_d": inflow,
            "argon_per_nitrogen_state_mol_mol": kmol["Ar"] / lumped if lumped > 0.0 else 0.0}


# ----------------------------------------------------------------------------------------------------------- report
def microaeration_headspace_report(AD: Any) -> dict:
    """The micro-aeration readout of a variant reactor at its CURRENT state (rates kg/d, heat W): G1 and its S0
    split (liquid fall-out vs the headspace DEPOSIT, an S outflow with a cleaning duty), the O2 and N2 balances, the
    dry headspace O2 fraction, the liquor DO and the G1 reaction heat. The biogas outlet stream does not carry O2 or
    N2 (there is no dissolved-N2 component to carry it): this readout and the headspace state are their record."""
    headspace = getattr(AD.model, "microaeration_headspace", None)
    if headspace is None:
        raise ValueError("the reactor's model does not compile the micro-aeration extension")
    state = np.asarray(AD._state, dtype=float)
    n_cmps = len(AD.components)
    rates = np.asarray(AD.model.rate_function(state.copy()), dtype=float)
    gas = state[n_cmps:n_cmps + len(headspace.gas_state_ids)]
    v_liq = float(AD.V_liq)
    g1 = v_liq * float(rates[headspace.g1_rate_index])                                   # kg S/d oxidised
    transfer = v_liq * float(rates[headspace.o2_transfer_rate_index])                    # kg O2/d, liquid -> gas
    q_gas = float(AD.f_q_gas_var_P_headspace(rates[AD._gas_rate_slice()], gas, AD.T))
    inflow = np.asarray(AD._headspace_gas_inflow_kmol_d, dtype=float)
    i_o2, i_n2 = headspace.index("S_o2"), headspace.index("S_n2")
    mw_o2, mw_n2, mw_s = headspace.molar_mass["S_o2"], headspace.molar_mass["S_n2"], headspace.molar_mass["S_h2s"]
    o2_in, o2_out = inflow[i_o2] * mw_o2, q_gas * gas[i_o2] * mw_o2
    o2_headspace = g1 * headspace.o2_kg_per_kg_s
    dry = float(np.sum(gas))
    f = headspace.f_so4
    heat_w = -(g1 / mw_s * 1000.0 / 86400.0) * ((1.0 - f) * G1_REACTION_ENTHALPY_J_MOL["S0"]
                                               + f * G1_REACTION_ENTHALPY_J_MOL["SO4"])
    return {
        "kinetics": {"microaeration_extension_id": headspace.extension_id,
                     "microaeration_extension_sha256": headspace.sha256},
        "headspace_h2s_oxidised_kg_s_d": g1,
        "s0_to_liquid_kg_s_d": g1 * headspace.liquid_s0_kg_s_per_kg_s,
        "s0_headspace_deposit_kg_s_d": g1 * headspace.deposit_kg_s_per_kg_s,
        "sulfate_to_liquid_kg_s_d": g1 * headspace.sulfate_kg_s_per_kg_s,
        "o2_in_kg_d": o2_in, "o2_out_kg_d": o2_out, "o2_consumed_headspace_kg_d": o2_headspace,
        "o2_to_liquid_kg_d": -transfer,
        "o2_headspace_accumulation_kg_d": o2_in - o2_out - o2_headspace + transfer,
        "n2_in_kg_d": inflow[i_n2] * mw_n2, "n2_out_kg_d": q_gas * gas[i_n2] * mw_n2,
        "residual_o2_fraction_dry": (float(gas[i_o2]) / dry if dry > 0.0 else None),
        "liquor_do_kg_m3": float(state[AD.components.index(S_O2)]),
        "headspace_oxidation_heat_w": heat_w,
        "headspace_wetted_area_m2": AD.headspace_wetted_area_m2,
        "biogas_flow_m3_d": q_gas,
    }
