"""
mASM2d - Modified Activated Sludge Model 2d defaults and component creation.

mASM2d extends ASM2d (19 components) with:
  - S_IC (inorganic carbon, replaces S_ALK)
  - S_K, S_Mg, S_Ca, S_Na, S_Cl (major ions)
  - S_N2 (dissolved nitrogen from denitrification)
  - 9 precipitate species (CaCO3, struvite, newberyite, ACP, MgCO3,
    AlOH, AlPO4, FeOH, FePO4)

Total: 31 components.

The underlying QSDsan process model is still ASM2d — mASM2d is an extended
component set that tracks ions and precipitates alongside the biological
kinetics. The extra species are inert w.r.t. ASM2d kinetics but participate
in chemical precipitation equilibria (handled externally).

Usage:
    from models.masm2d import DEFAULT_MASM2D_KWARGS, DEFAULT_MASM2D_DOMESTIC_WW
    from models.masm2d import create_masm2d_components
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ASM2d kinetic parameters (same as ASM2d — biological kinetics unchanged)
# Source: Pune_Nanded calibration (models/asm2d.py)
DEFAULT_MASM2D_KWARGS: Dict[str, float] = {
    'iN_SI': 0.01, 'iN_SF': 0.03, 'iN_XI': 0.02, 'iN_XS': 0.04, 'iN_BM': 0.07,
    'iP_SI': 0.0, 'iP_SF': 0.01, 'iP_XI': 0.01, 'iP_XS': 0.01, 'iP_BM': 0.02,
    'iTSS_XI': 0.75, 'iTSS_XS': 0.75, 'iTSS_BM': 0.9,
    'f_SI': 0.0, 'Y_H': 0.625, 'f_XI_H': 0.1,
    'Y_PAO': 0.625, 'Y_PO4': 0.4, 'Y_PHA': 0.2, 'f_XI_PAO': 0.1,
    'Y_A': 0.24, 'f_XI_AUT': 0.1,
    'K_h': 3.0, 'eta_fe': 0.4, 'K_O2': 0.2, 'K_X': 0.1,
    'mu_H': 6.0, 'q_fe': 3.0, 'b_H': 0.4, 'K_O2_H': 0.05, 'K_F': 4.0,
    'K_fe': 4.0, 'K_A_H': 4.0, 'K_NH4_H': 0.05, 'K_P_H': 0.01, 'K_ALK_H': 0.1,
    'q_PHA': 3.0, 'q_PP': 1.5, 'mu_PAO': 1.0, 'b_PAO': 0.2, 'b_PP': 0.2,
    'b_PHA': 0.2, 'K_O2_PAO': 0.2, 'K_A_PAO': 4.0, 'K_NH4_PAO': 0.05,
    'K_PS': 0.2, 'K_P_PAO': 0.01, 'K_ALK_PAO': 0.1,
    'K_PP': 0.01, 'K_MAX': 0.34, 'K_IPP': 0.02, 'K_PHA': 0.01,
    'mu_AUT': 1.0, 'b_AUT': 0.15, 'K_O2_AUT': 0.5, 'K_NH4_AUT': 1.0,
    'K_ALK_AUT': 0.5, 'K_P_AUT': 0.01,
    'k_PRE': 1.0, 'k_RED': 0.6, 'K_ALK_PRE': 0.5,
    'eta_NO3': 0.5, 'eta_NO3_H': 0.5, 'eta_NO3_PAO': 0.5,
    'K_NO3_H': 0.8, 'K_NO3': 0.8, 'K_NO3_PAO': 0.8,
}

# Default domestic wastewater composition (mg/L) for mASM2d
# Extends ASM2d defaults with ion and precipitate fields.
# COD fractionation from Pune_Nanded; ion concentrations from
# typical municipal wastewater (Metcalf & Eddy, 2014).
DEFAULT_MASM2D_DOMESTIC_WW: Dict[str, float] = {
    # ASM2d biological components (19 - 3 implicit = 16 explicit ASM2d)
    'S_O2': 0.0,     # dissolved oxygen (set by aeration)
    'S_I': 10,
    'X_I': 120,
    'S_F': 75,
    'S_A': 20,
    'S_NH4': 17,
    'S_NO3': 0.5,    # trace nitrate in influent
    'S_PO4': 9,
    'X_S': 100,
    'X_H': 5,
    'X_PAO': 5,
    'X_PP': 0,
    'X_PHA': 10,
    'X_AUT': 5,
    'H2O': 1e6,      # water (solvent, ~1e6 mg/L)

    # mASM2d-specific: inorganic carbon (replaces S_ALK)
    # 200 mg/L as CaCO3 ~ 48 mg-C/L (200 * 12/50)
    'S_IC': 48,

    # Major ions (typical municipal)
    'S_K': 15,      # mg/L
    'S_Mg': 10,     # mg/L
    'S_Ca': 50,     # mg/L
    'S_Na': 80,     # mg/L (charge balance adjusted)
    'S_Cl': 100,    # mg/L (charge balance adjusted)

    # Dissolved gases
    'S_N2': 0,      # produced by denitrification

    # Precipitates (zero in influent)
    'X_CaCO3': 0,
    'X_struv': 0,
    'X_newb': 0,
    'X_ACP': 0,
    'X_MgCO3': 0,
    'X_AlOH': 0,
    'X_AlPO4': 0,
    'X_FeOH': 0,
    'X_FePO4': 0,
}


def create_masm2d_components(set_thermo: bool = True):
    """Return upstream QSDsan's native 31-component ``mASM2d`` package."""
    from qsdsan import process_models as pc

    return pc.create_masm2d_cmps(set_thermo=set_thermo)


def create_masm2d_2n_components(set_thermo: bool = True):
    """Return the 33-component TWO-STEP nitrification (``mASM2d2N``) package.

    Extends the stock 31-component mASM2d set by (a) inserting a distinct nitrite state ``S_NO2``
    right after ``S_NO3`` and (b) splitting the lumped autotroph ``X_AUT`` into ammonia-oxidising
    ``X_AOB`` (NH4->NO2) and nitrite-oxidising ``X_NOB`` (NO2->NO3). Component order (state_arr):

        S_O2 S_N2 S_NH4 S_NO3 S_NO2 S_PO4 S_F S_A S_I S_IC S_K S_Mg
        X_I X_S X_H X_PAO X_PP X_PHA X_AOB X_NOB
        S_Ca X_CaCO3 X_struv X_newb X_ACP X_MgCO3 X_AlOH X_AlPO4 X_FeOH X_FePO4 S_Na S_Cl H2O

    QSDsan auto-derives ``S_NO2`` i_COD = -48/14 (so the Petersen COD-conservation ``?`` solver
    yields the correct per-step O2) and i_charge = -1/14 (so nitrite enters ``solve_pH`` correctly).
    ``X_AOB``/``X_NOB`` inherit the mASM2d ``X_AUT`` composition (i_N 0.08615, i_P 0.02154, i_mass 0.9).
    This is additive: it does NOT touch the stock ``create_masm2d_components`` / ``mASM2d`` model.
    """
    import qsdsan as qs
    from qsdsan import Component, Components, process_models as pc

    base = pc.create_masm2d_cmps(set_thermo=False)   # stock 31-comp mASM2d set
    ion_kwargs = dict(particle_size='Soluble', degradability='Undegradable', organic=False)

    S_NO2 = Component.from_chemical('S_NO2', chemical='NO2-', measured_as='N',
                                    description='Nitrite nitrogen', **ion_kwargs)
    S_NO2.i_NOD = None

    X_AUT = base.X_AUT
    X_AOB = X_AUT.copy('X_AOB'); X_AOB.description = 'Ammonia-oxidizing bacteria (NH4->NO2)'
    X_NOB = X_AUT.copy('X_NOB'); X_NOB.description = 'Nitrite-oxidizing bacteria (NO2->NO3)'

    new = []
    for c in base:
        if c.ID == 'X_AUT':
            new.extend([X_AOB, X_NOB])          # replace single autotroph with AOB + NOB
            continue
        new.append(c)
        if c.ID == 'S_NO3':
            new.append(S_NO2)                    # nitrite intermediate right after nitrate

    cmps = Components(new)
    cmps.default_compile(ignore_inaccurate_molar_weight=True, adjust_MW_to_measured_as=True)
    if set_thermo:
        qs.set_thermo(cmps)
    return cmps


# ==============================================================================
# Two-step nitrification model (mASM2d2N) — custom QSDsan process, mirrors ModifiedADM1.
# Splits single-step nitrification into AOB (NH4->NO2) + NOB (NO2->NO3) with a distinct S_NO2
# state and Anthonisen free-ammonia / free-nitrous-acid inhibition coupled to the algebraic pH
# solve. Two-step denitrification (NO3->NO2->N2) keeps N mass-balance correct once S_NO2 exists.
# ==============================================================================

import os as _os
import numpy as _np
from qsdsan import Processes as _Processes, CompiledProcesses as _CompiledProcesses
from math import exp as _exp
from qsdsan.process_models import Monod as _Monod, ion_speciation as _ion_speciation
from qsdsan.process_models._asm2d import acid_base_rxn as _acid_base_rxn
from qsdsan.utils import ospath as _ospath, data_path as _qs_data_path, load_data as _load_data
from scipy.optimize import brenth as _brenth
from thermosteam import settings as _tmo_settings
from .physicality import (
    ph_solve_failures as _shared_ph_solve_failures,
    record_ph_solve_failure as _record_ph_solve_failure,
    reset_ph_solve_failures as _shared_reset_ph_solve_failures,
)

# TSV lives in the monorepo (version-controlled), NOT the qsdsan venv data dir.
_MASM2D_2N_TSV = _os.path.join(_os.path.dirname(__file__), 'process_data', '_masm2d_2n.tsv')
_MMP_TSV = _ospath.join(_qs_data_path, 'process_data/_mmp.tsv')

# state_arr indices (33-component mASM2d2N order)
(_S_O2, _S_N2, _S_NH4, _S_NO3, _S_NO2, _S_PO4, _S_F, _S_A, _S_I, _S_IC, _S_K, _S_Mg,
 _X_I, _X_S, _X_H, _X_PAO, _X_PP, _X_PHA, _X_AOB, _X_NOB,
 _S_Ca, _X_CaCO3, _X_struv, _X_newb, _X_ACP, _X_MgCO3, _X_AlOH, _X_AlPO4, _X_FeOH, _X_FePO4,
 _S_Na, _S_Cl) = range(32)

_N_BIO = 25          # biological processes
_N_PRECIP = 7        # mineral precipitation/dissolution


def solve_pH_2n(state_arr, Ka, unit_conversion):
    """pH solve for the 33-component two-step set; NOx charge = S_NO3 + S_NO2."""
    m = state_arr[:32] * unit_conversion[:32] * 1e-3   # mol/L
    ions = _np.array([
        m[_S_K], m[_S_Mg], m[_S_Ca], m[_S_Na], m[_S_Cl],
        m[_S_NO3] + m[_S_NO2],           # NOx (both anions)
        m[_S_NH4], m[_S_IC], m[_S_PO4], m[_S_A],
    ])
    try:
        return _brenth(_acid_base_rxn, 1e-14, 1.0, args=(ions, Ka), xtol=1e-12, maxiter=100)
    except (ValueError, RuntimeError, FloatingPointError):
        # No sign change in the bracket -- usually a transient non-physical state inside a
        # stiff step. Returning neutral keeps the RHS finite so the solver can back up and
        # retry, but a pH of 7.0 on a reactor running at 6.0 silently rewrites FA, FNA and
        # the carbonate speciation. Count it so the twin can surface repeated failures as a
        # physicality fault instead of quietly integrating fabricated chemistry.
        _record_ph_solve_failure("masm2d_2n")
        return 1e-7


#: Count of pH-solve fallbacks since the last reset. A handful during a stiff transient is
#: normal; a sustained count means the charge balance is not closing and the run's pH --
#: and therefore its FA/FNA and carbonate chemistry -- is not trustworthy.
def ph_solve_failures() -> int:
    """Number of pH-solve fallbacks recorded since :func:`reset_ph_solve_failures`."""
    return _shared_ph_solve_failures("masm2d_2n")


def reset_ph_solve_failures() -> None:
    """Zero the pH-solve failure counter, typically at the start of a replay segment."""
    _shared_reset_ph_solve_failures("masm2d_2n")


def _M(S, K):
    S = S if S > 0 else 0.0
    d = K + S
    return S / d if d > 0 else 0.0


def _inhib(S_free, KI):
    """Non-competitive Anthonisen inhibition KI/(KI+S_free), bounded [0,1]."""
    if KI is None or KI <= 0:
        return 1.0
    v = KI / (KI + (S_free if S_free > 0 else 0.0))
    return 1.0 if v > 1.0 else (0.0 if v < 0.0 else v)


def rhos_masm2d_2n(state_arr, params, acceptor_dependent_decay=True, h=None):
    """Process rates for two-step nitrification/denitrification mASM2d with FA/FNA inhibition.

    Layout: 25 biological processes then 7 precipitation. pH is solved BEFORE the nitrifier rates
    so free NH3 (FA) and free HNO2 (FNA) — derived from the model's own equilibrium constants —
    modulate AOB/NOB via non-competitive inhibition. Temperature enters only through the (fixed)
    equilibrium constants, so no per-RHS temperature argument is needed.
    """
    p = params
    cmps = p['cmps']
    if 'mass2mol' not in p:
        p['mass2mol'] = cmps.i_mass / cmps.chem_MW
    m2m = p['mass2mol']

    # Clip transient negative concentrations (a stiff CSTR/SBR step can briefly overshoot below
    # zero); identity at feasible states. Matches the IWA/BSM max(x,0) convention and prevents
    # NaN from the fractional-power precipitation SI terms and the pH charge balance.
    s = _np.maximum(state_arr, 0.0)
    S_O2, S_NH4, S_NO3, S_NO2, S_PO4, S_F, S_A = (
        s[_S_O2], s[_S_NH4], s[_S_NO3], s[_S_NO2], s[_S_PO4], s[_S_F], s[_S_A])
    S_IC = s[_S_IC]
    X_H, X_PAO, X_PP, X_PHA, X_AOB, X_NOB, X_S = (
        s[_X_H], s[_X_PAO], s[_X_PP], s[_X_PHA], s[_X_AOB], s[_X_NOB], s[_X_S])

    rhos = _np.zeros(_N_BIO + _N_PRECIP)

    # ---- pH + free-species speciation (early, for FA/FNA coupling) ----
    Ka = p['Ka']
    Kw, Knh, Kc1, Kc2, Kp1, Kp2, Kp3, Kac = Ka
    if h is None:
        h = solve_pH_2n(s, Ka, m2m)
    FA = S_NH4 * Knh / (Knh + h)                      # free ammonia (mg N/L)
    Ka_hno2 = p['Ka_hno2']
    FNA = S_NO2 * h / (Ka_hno2 + h)                   # free nitrous acid (mg N/L)

    NOx = S_NO3 + S_NO2
    aero_h = _M(S_O2, p['K_O2'])

    # ---- hydrolysis (0,1,2) ----
    if X_H > 0:
        hyd = p['k_h'] * _M(X_S / X_H, p['K_X']) * X_H
    else:
        hyd = 0.0
    rhos[0] = hyd * aero_h
    rhos[1] = hyd * p['eta_NO3'] * (1 - aero_h) * _M(NOx, p['K_NO3'])
    rhos[2] = hyd * p['eta_fe'] * (1 - aero_h) * (1 - _M(NOx, p['K_NO3']))

    # ---- heterotrophic growth on S_F / S_A (aerobic 3,4; denite 5-8) ----
    if (S_F + S_A) > 0 and X_H > 0:
        nut_H = _M(S_NH4, p['K_NH4_H']) * _M(S_PO4, p['K_P_H'])
        base_F = p['mu_H'] * nut_H * _M(S_F, p['K_F']) * S_F / (S_F + S_A) * X_H
        base_A = p['mu_H'] * nut_H * _M(S_A, p['K_A_H']) * S_A / (S_F + S_A) * X_H
        o2_H = _M(S_O2, p['K_O2_H'])
        anox_H = (1 - o2_H)
        rhos[3] = base_F * o2_H
        rhos[4] = base_A * o2_H
        rhos[5] = base_F * p['eta_NO3_H'] * anox_H * _M(S_NO3, p['K_NO3_H'])   # S_F, NO3->NO2
        rhos[6] = base_F * p['eta_NO2_H'] * anox_H * _M(S_NO2, p['K_NO2_H'])   # S_F, NO2->N2
        rhos[7] = base_A * p['eta_NO3_H'] * anox_H * _M(S_NO3, p['K_NO3_H'])   # S_A, NO3->NO2
        rhos[8] = base_A * p['eta_NO2_H'] * anox_H * _M(S_NO2, p['K_NO2_H'])   # S_A, NO2->N2
        # fermentation (9)
        rhos[9] = p['q_fe'] * (1 - o2_H) * (1 - _M(NOx, p['K_NO3_H'])) * _M(S_F, p['K_fe']) * X_H
    # heterotroph lysis (10)
    hlys = p['b_H'] * X_H
    if acceptor_dependent_decay:
        o2_H = _M(S_O2, p['K_O2_H'])
        hlys *= (o2_H + p['eta_NO3_Hl'] * (1 - o2_H) * _M(NOx, p['K_NO3_H']))
    rhos[10] = hlys

    # ---- PAO storage / growth (11-20) ----
    if X_PAO > 0:
        pp_pao = X_PP / X_PAO
        pha_pao = X_PHA / X_PAO
        rhos[11] = (p['q_PHA'] * _M(S_A, p['K_A_PAO']) * _M(pp_pao, p['K_PP']) * X_PAO)   # storage_PHA
        stor = p['q_PP'] * _M(pha_pao, p['K_PHA']) * _M(p['K_MAX'] - pp_pao, p['K_IPP']) \
            * _M(S_PO4, p['K_P_S']) * X_PAO
        o2_PAO = _M(S_O2, p['K_O2_PAO'])
        rhos[12] = stor * o2_PAO                                                          # aero_storage_PP
        rhos[13] = stor * p['eta_NO3_PAO'] * (1 - o2_PAO) * _M(S_NO3, p['K_NO3_PAO'])     # PP NO3->NO2
        rhos[14] = stor * p['eta_NO2_PAO'] * (1 - o2_PAO) * _M(S_NO2, p['K_NO2_PAO'])     # PP NO2->N2
        grow = p['mu_PAO'] * _M(S_NH4, p['K_NH4_PAO']) * _M(S_PO4, p['K_P_PAO']) * _M(pha_pao, p['K_PHA']) * X_PAO
        rhos[15] = grow * o2_PAO                                                          # PAO_aero_growth
        rhos[16] = grow * p['eta_NO3_PAO'] * (1 - o2_PAO) * _M(S_NO3, p['K_NO3_PAO'])     # anox NO3->NO2
        rhos[17] = grow * p['eta_NO2_PAO'] * (1 - o2_PAO) * _M(S_NO2, p['K_NO2_PAO'])     # anox NO2->N2
        plys = p['b_PAO'] * X_PAO
        pplys = p['b_PP'] * X_PP
        phalys = p['b_PHA'] * X_PHA
        if acceptor_dependent_decay:
            sw = o2_PAO + p['eta_NO3_PAOl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
            plys *= sw
            pplys *= o2_PAO + p['eta_NO3_PPl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
            phalys *= o2_PAO + p['eta_NO3_PHAl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
        rhos[18] = plys      # PAO_lysis
        rhos[19] = pplys     # PP_lysis
        rhos[20] = phalys    # PHA_lysis

    # ---- two-step nitrification (21 AOB, 22 NOB) with FA/FNA inhibition ----
    nut_P = _M(S_PO4, p['K_P_AUT'])

    # Inorganic-carbon limitation of AOB. AOB are autotrophs: CO2/HCO3- is their carbon
    # SOURCE, not merely a pH term, and a heavily-aerated reactor strips it.
    #
    # THIS IS A SENSITIVITY PRIOR, NOT A FITTED AFFINITY -- state it that way anywhere it
    # is reported. K_HCO3_AOB = 6.0 mg C/L is CONVERTED from ASM2d's typical alkalinity
    # placeholder K_ALK = 0.5 mol HCO3-/m3 (Henze et al. 2000, IWA STR No. 9), which is a
    # bookkeeping switch guarding against low-pH nonsense, NOT an experimentally measured
    # bicarbonate half-saturation for AOB. Treat it as `not_identifiable` and
    # sensitivity-test it until DIC excitation data exist. Set 0 to disable.
    # The sensitivity test is BOUNDED by measurement, not open-ended: Guisasola
    # et al. (2007, Water Res. 41:277, doi 10.1016/j.watres.2006.10.030) measured
    # the limitation ONSET near ~36 mg C/L in nitrifying sludge -- so the check is
    # "does basin DIC stay clear of ~36 mg C/L", swept with K_HCO3_AOB over
    # ~2-12 mg C/L. If limitation IS predicted, Wett & Rauch (2003, Water Res.
    # 37:1100, doi 10.1016/S0043-1354(02)00440-2) provide a sigmoidal
    # carbon-limitation kinetic fitted on full-scale rejection water -- measured
    # basis for exactly the high-N, CO2-stripped regime this term guards.
    #
    # An earlier revision claimed this was "ASM2d's own switch" unchanged. That
    # overclaims twice: Henze's S_ALK is an alkalinity state merely *assumed* to be
    # bicarbonate, whereas this term acts on explicitly speciated HCO3- from the pH
    # solve; and no source supplies this value as a biological affinity. Writing it on
    # speciated bicarbonate is a defensible adaptation -- at pH 6 most inorganic carbon
    # is dissolved CO2, so a term on total S_IC would count carbon the organisms cannot
    # use -- but it is an adaptation, not a citation.
    #
    # WHAT THE LITERATURE DOES SUPPORT. Guisasola et al. 2007
    # (doi 10.1016/j.watres.2006.10.030) establish the qualitative asymmetry the term
    # relies on: AOB limited below ~3 mmol C/L TIC, NOB showing no limitation even below
    # 0.1 mmol C/L -- which is why the switch is applied to AOB alone. They fitted Monod,
    # Tessier and sigmoidal kinetics and report the sigmoid best, but their parameters are
    # paywalled and were NOT obtained, and their threshold is stated against TIC rather
    # than bicarbonate. Wett & Rauch 2003 (doi 10.1016/S0043-1354(02)00440-2) conclude
    # "the bicarbonate concentration proved to be crucial", supporting the SPECIES but
    # not this value.
    #
    # Monod ships as the default because it is the form the converted value belongs to.
    # `n_HCO3_AOB` raises it to a Hill shape for sensitivity work ONLY -- no source gives
    # that exponent, and a run with n != 1 must never be described as "Guisasola
    # kinetics". At this K the term is 0.83 at Guisasola's 3 mmol C/L threshold, i.e.
    # limitation is beginning there, which is consistent with their observation without
    # pretending to reproduce their fit.
    #
    # WHAT THIS TERM ALONE GETS WRONG, per Mellbye et al. 2016 (AEM 82(11):3310-3318,
    # doi 10.1128/AEM.00294-16). Their chemostats on N. europaea are the closest thing to
    # a controlled measurement of this mechanism, and they show IC limitation acts on
    # GROWTH, not on the oxidation rate per cell:
    #
    #   * specific NH3 oxidation fell only 21.9 -> 19.4 umol (mg protein)^-1 h^-1, ~11%
    #   * standing biomass fell to 37-66% of replete (a stock ratio, not a growth rate)
    #   * IC-limited cultures washed out at mu = 0.026 /h -- the only IC-limited growth
    #     figure the paper reports. NOTE: 0.126 and 0.063 /h are ln2/5.5 and ln2/11, the
    #     endpoints of the PREVIOUSLY PUBLISHED Td range it cites, not measurements of
    #     theirs; an earlier revision of this comment wrongly read them as a halving.
    #   * maintenance energy rose 6.12 -> 14.0 mmol NH3 gDCW^-1 h^-1, i.e. 2.3x, matching
    #     the abstract. (4.4 and 6.12 are both NH3-LIMITED values -- previously reported
    #     and their own estimate -- and an earlier revision mis-assigned them as the IC
    #     effect.)
    #   * they name the result "uncoupling of NH3 oxidation from growth"
    #
    # In ASM stoichiometry the growth rate rho and the oxidation rate rho/Y are rigidly
    # coupled, so multiplying rho by `ic_lim` suppresses BOTH -- which is precisely what
    # Mellbye says does not happen. Reproducing their result needs Y_AOB scaled by the
    # same factor, so that oxidation = (ic_lim*rho)/(ic_lim*Y) is preserved while growth
    # falls. See `utils.nitrification_twin.ic_limited_yield`, which does that at the
    # segment boundary (Y is stoichiometric and cannot vary inside a segment).
    #
    # Using this term WITHOUT the yield coupling therefore over-predicts the immediate
    # ammonia breakthrough and under-predicts the inventory shortfall -- and those are
    
    # GUISASOLA 2007 SIGMOID, origin-corrected. Their Table 1 best fit, on the SPECIES
    # named by `ic_basis`. Replaces an earlier Monod on bicarbonate whose half-saturation
    # was an unsourced conversion of ASM2d's alkalinity placeholder -- that prior cost the
    # AOB 21-47% on this plant's states, which is a large penalty to levy on a number
    # nobody measured.
    #
    # WHICH SPECIES IS NOT SETTLED, and the choice is worth up to 57 percentage points of
    # AOB activity here, so it is a switch rather than a decision. Guisasola fitted at a
    # FIXED pH 7.50, where CO2 = alpha.TIC exactly, so their data cannot distinguish the
    # two pools -- expressing the parameters "in CO2 units to obtain a non pH-dependent
    # value" is a portability claim, not a species identification. RuBisCO does consume
    # CO2, but N. europaea upregulates carbonic anhydrase under IC limitation and
    # bicarbonate uptake has been shown by radiotracer, so the bulk HCO3 pool can
    # plausibly replenish it. At pH 5.9-6.9 -- nowhere near their 7.5 -- the two readings
    # diverge sharply, and neither is validated there.
    #
    # ORIGIN CORRECTION. The published sigmoid has f(0) = 1/(1+exp(a/k)) = 0.124, i.e. it
    # would license 12.4% AOB activity at ZERO carbon. That offset is Guisasola's own
    # unexplained baseline OUR -- they attribute it to maintenance metabolism, mixotrophy
    # or unlimited NOB -- not to carbon-free autotrophic growth. It is subtracted and the
    # curve renormalised so f(0) = 0.
    ic_basis = p.get('ic_basis', None)
    if ic_basis:
        _co2, _hco3, _co3 = S_IC * _ion_speciation(h, Kc1, Kc2)
        _a = p.get('ic_sigmoid_a', 0.0)
        _k = p.get('ic_sigmoid_k', 1.0)
        _x = max(_co2 if ic_basis == 'co2' else S_IC, 0.0)
        _z = (_x - _a) / _k
        _f = 1.0 / (1.0 + _exp(-_z)) if _z > -700.0 else 0.0
        _f0 = 1.0 / (1.0 + _exp(_a / _k)) if (_a / _k) < 700.0 else 0.0
        ic_lim = min(max((_f - _f0) / (1.0 - _f0), 0.0), 1.0) if _f0 < 1.0 else 1.0
    elif p.get('K_HCO3_AOB', 0.0) > 0:
        # Retained so an older basis still builds, but it is NOT the recommended form.
        K_hco3 = p['K_HCO3_AOB']
        _co2, _hco3, _co3 = S_IC * _ion_speciation(h, Kc1, Kc2)
        n = p.get('n_HCO3_AOB', 1.0)
        if n == 1.0:
            ic_lim = _M(max(_hco3, 0.0), K_hco3)
        else:
            _b = max(_hco3, 0.0) ** n
            ic_lim = _b / (K_hco3 ** n + _b) if _b > 0 else 0.0
    else:
        ic_lim = 1.0

    # Low-pH cessation of AOB. Fumasoli, Morgenroth & Udert 2015
    # (doi 10.1016/j.watres.2015.06.013, Water Res 83:161-170) tested three forms against
    # controlled batch data and concluded that NH3 limitation plus HNO2 inhibition ALREADY
    # explain the rate decrease between pH 7 and 6 -- the FA/FNA terms above -- but fail at
    # the hard limit near pH 5.4. They found a sigmoidal pH term (Antoniou 1990 / Jubany
    # 2007) OVERESTIMATES the effect over a wide pH range and could not be applied
    # successfully. So do NOT add a general pH-activity factor here: it would double-count
    # against FA/FNA. Use only their Model 3 exponential term (Presser 1997; Ratkowsky
    # 2002), which is ~1 above pH 6.5 and drives the rate to zero at pH_min:
    #     f_pH = 1 - 10^(K_pH * (pH_min - pH)),   zero below pH_min
    # Their fitted K_pH = 2.3 with pH_min = 5.4 for Nitrosomonas eutropha. The mechanism is
    # energetic, not enzymatic: below the limit the proton motive force cannot supply the
    # energy for NADH production. Set pH_min_AOB=None to disable.
    ph_min = p.get('pH_min_AOB')
    if ph_min is not None:
        pH_now = -_np.log10(max(h, 1e-14))
        if pH_now <= ph_min:
            ph_lim = 0.0
        else:
            ph_lim = 1.0 - 10.0 ** (p.get('K_pH_AOB', 2.3) * (ph_min - pH_now))
    else:
        ph_lim = 1.0

    rhos[21] = (p['mu_AOB'] * X_AOB * _M(S_O2, p['K_O2_AOB']) * _M(S_NH4, p['K_NH4_AOB'])
                * nut_P * ic_lim * ph_lim
                * _inhib(FA, p['KI_FA_AOB']) * _inhib(FNA, p['KI_FNA_AOB']))
    # NOB substrate is nitrite, NOT ammonia — do not gate the energy reaction on S_NH4, or NOB
    # cannot clear accumulated nitrite once AOB has depleted ammonia (the thread's nitrite-lock
    # recovery). Biomass-N assimilation is handled stoichiometrically by the Petersen `?` solver.
    rhos[22] = (p['mu_NOB'] * X_NOB * _M(S_O2, p['K_O2_NOB']) * _M(S_NO2, p['K_NO2_NOB'])
                * nut_P
                * _inhib(FA, p['KI_FA_NOB']) * _inhib(FNA, p['KI_FNA_NOB']))
    # nitrifier decay (23 AOB, 24 NOB)
    aob_dec = p['b_AOB'] * X_AOB
    nob_dec = p['b_NOB'] * X_NOB
    if acceptor_dependent_decay:
        o2_A = _M(S_O2, p['K_O2_AOB'])
        o2_N = _M(S_O2, p['K_O2_NOB'])
        aob_dec *= o2_A + p['eta_NO3_AUTl'] * (1 - o2_A) * _M(NOx, p['K_NO3_AUT'])
        nob_dec *= o2_N + p['eta_NO3_AUTl'] * (1 - o2_N) * _M(NOx, p['K_NO3_AUT'])
    rhos[23] = aob_dec
    rhos[24] = nob_dec

    # ---- precipitation / dissolution (25-31), same minerals as stock mASM2d ----
    S_Ca, S_Mg = s[_S_Ca], s[_S_Mg]
    X_CaCO3, X_struv, X_newb, X_ACP, X_MgCO3, X_AlOH, X_FeOH = (
        s[_X_CaCO3], s[_X_struv], s[_X_newb], s[_X_ACP], s[_X_MgCO3], s[_X_AlOH], s[_X_FeOH])
    nh4 = S_NH4 * h / (Knh + h)
    co2, hco3, co3 = S_IC * _ion_speciation(h, Kc1, Kc2)
    h3po4, h2po4, hpo4, po4 = S_PO4 * _ion_speciation(h, Kp1, Kp2, Kp3)
    Ksp = p['Ksp']
    SI = (S_Ca * co3 / Ksp[0]) ** (1 / 2)
    if SI > 1: rhos[25] = X_CaCO3 * (SI - 1) ** 2
    SI = (S_Mg * nh4 * po4 / Ksp[1]) ** (1 / 3)
    if SI > 1: rhos[26] = X_struv * (SI - 1) ** 3
    SI = (S_Mg * hpo4 / Ksp[2]) ** (1 / 2)
    if SI > 1: rhos[27] = X_newb * (SI - 1) ** 2
    SI = (S_Ca ** 3 * po4 ** 2 / Ksp[3]) ** (1 / 5)
    if SI > 1: rhos[28] = X_ACP * (SI - 1) ** 2
    SI = (S_Mg * co3 / Ksp[4]) ** (1 / 2)
    if SI > 1: rhos[29] = X_MgCO3 * (SI - 1) ** 2
    rhos[30] = X_AlOH * po4 * _M(X_AlOH, p['K_AlOH'])
    rhos[31] = s[_X_FeOH] * po4 * _M(s[_X_FeOH], p['K_FeOH'])
    rhos[25:32] *= p['k_mmp']
    return rhos


def generate_masm2d_inoculum(
    target_mlvss_mg_L: float = 3500.0,
    x_aut_fraction: float = 0.05,
    x_pao_fraction: float = 0.10,
    x_h_fraction: float = 0.80,
    include_substrate: bool = True,
    include_nutrients: bool = True,
) -> Dict[str, float]:
    """Generate reactor initial concentrations for mASM2d systems.

    Extends the ASM2d inoculum generator with mASM2d-specific fields:
    S_IC (instead of S_ALK), ions, and precipitates.

    Args:
        target_mlvss_mg_L: Target MLVSS (mg VSS/L). Default 3500.
        x_aut_fraction: Fraction of MLVSS as nitrifiers.
        x_pao_fraction: Fraction of MLVSS as PAOs.
        x_h_fraction: Fraction of MLVSS as heterotrophs.
        include_substrate: Include background substrate.
        include_nutrients: Include background nutrients.

    Returns:
        Dict of component concentrations suitable for
        ``reactor.set_init_conc(**inoculum)``.
    """
    from qsdsan_engine_mcp.utils.aerobic_inoculum_generator import generate_aerobic_inoculum

    # Generate base ASM2d inoculum
    base = generate_aerobic_inoculum(
        target_mlvss_mg_L=target_mlvss_mg_L,
        x_aut_fraction=x_aut_fraction,
        x_pao_fraction=x_pao_fraction,
        x_h_fraction=x_h_fraction,
        include_substrate=include_substrate,
        include_nutrients=include_nutrients,
    )

    # Convert S_ALK -> S_IC
    # S_ALK is in mg/L as CaCO3; S_IC is in mg-C/L
    # 200 mg CaCO3/L ~ 48 mg-C/L (factor: 12/50)
    s_alk = base.pop('S_ALK', 200.0)
    base['S_IC'] = s_alk * 12.0 / 50.0

    # Add mASM2d-specific ion defaults (typical reactor background)
    base['S_K'] = 15.0
    base['S_Mg'] = 10.0
    base['S_Ca'] = 50.0
    base['S_Na'] = 80.0
    base['S_Cl'] = 100.0

    # Precipitates start at zero in inoculum
    for precip in ('X_CaCO3', 'X_struv', 'X_newb', 'X_ACP',
                   'X_MgCO3', 'X_AlOH', 'X_AlPO4', 'X_FeOH', 'X_FePO4'):
        base[precip] = 0.0

    logger.info(
        "Generated mASM2d inoculum: MLVSS=%.0f mg/L, S_IC=%.1f mg-C/L, "
        "X_AUT=%.0f mg COD/L",
        target_mlvss_mg_L, base['S_IC'], base.get('X_AUT', 0)
    )

    return base



#: Gas constant, L.atm/(mol.K) -- used to turn a Henry solubility into the
#: dimensionless C_gas/C_liq ratio that bounds stripping flux.
_R_L_ATM = 0.0820573

class ModifiedASM2d(_CompiledProcesses):
    """Two-step nitrification/denitrification mASM2d (``mASM2d2N``) with FA/FNA inhibition.

    Custom QSDsan process mirroring :class:`ModifiedADM1`. Extends the stock single-step mASM2d by
    splitting nitrification into AOB (NH4->NO2) and NOB (NO2->NO3) over a distinct ``S_NO2`` state,
    routing heterotroph/PAO denitrification through NO3->NO2->N2, and coupling Anthonisen free-ammonia
    (FA) and free-nitrous-acid (FNA) non-competitive inhibition to the algebraic pH solve. Bio-P (PAO)
    and mineral precipitation are retained from mASM2d. Reuses stock CSTR/CompletelyMixedMBR reactors
    (2-argument rate signature). Default ``pH_ctrl=None`` solves pH each step so FA/FNA respond to
    alkalinity drawdown (the thread's self-regulation); pass a float to fix pH.
    """

    _stoichio_params = ('f_SI', 'Y_H', 'Y_PAO', 'Y_PO4', 'Y_PHA', 'Y_AOB', 'Y_NOB',
                        'f_XI_H', 'f_XI_PAO', 'f_XI_AUT', 'COD_deN1', 'COD_deN2', 'K_XPP', 'Mg_XPP')
    _kinetic_params = ('k_h', 'mu_H', 'mu_PAO', 'mu_AOB', 'mu_NOB',
                       'q_fe', 'q_PHA', 'q_PP',
                       'b_H', 'b_PAO', 'b_PP', 'b_PHA', 'b_AOB', 'b_NOB',
                       'eta_NO3', 'eta_fe', 'eta_NO3_H', 'eta_NO2_H', 'eta_NO3_PAO', 'eta_NO2_PAO',
                       'eta_NO3_Hl', 'eta_NO3_PAOl', 'eta_NO3_PPl', 'eta_NO3_PHAl', 'eta_NO3_AUTl',
                       'K_O2', 'K_O2_H', 'K_O2_PAO', 'K_O2_AOB', 'K_O2_NOB',
                       'K_NO3', 'K_NO3_H', 'K_NO2_H', 'K_NO3_PAO', 'K_NO2_PAO', 'K_NO3_AUT',
                       'K_X', 'K_F', 'K_fe', 'K_A_H', 'K_A_PAO',
                       'K_NH4_H', 'K_NH4_PAO', 'K_NH4_AOB', 'K_NO2_NOB',
                       'K_P_H', 'K_P_PAO', 'K_P_AUT', 'K_P_S',
                       'K_PP', 'K_MAX', 'K_IPP', 'K_PHA',
                       'KI_FA_AOB', 'KI_FA_NOB', 'KI_FNA_AOB', 'KI_FNA_NOB',
                       'k_mmp', 'Ksp', 'K_dis', 'K_AlOH', 'K_FeOH',
                       'Ka', 'Ka_hno2', 'cmps')
    _acid_base_pairs = (('H+', 'OH-'), ('NH4+', 'NH3'),
                        ('CO2', 'HCO3-'), ('HCO3-', 'CO3-2'),
                        ('H3PO4', 'H2PO4-'), ('H2PO4-', 'HPO4-2'), ('HPO4-2', 'PO4-3'),
                        ('HAc', 'Ac-'),)
    _precipitates = ('X_CaCO3', 'X_struv', 'X_newb', 'X_ACP', 'X_MgCO3', 'X_AlPO4', 'X_FePO4')

    def __new__(cls, components=None, path=None,
                electron_acceptor_dependent_decay=True, pH_ctrl=None,
                f_SI=0.0, Y_H=0.625, Y_PAO=0.625, Y_PO4=0.4, Y_PHA=0.2, Y_AOB=0.18, Y_NOB=0.06,
                f_XI_H=0.1, f_XI_PAO=0.1, f_XI_AUT=0.1,
                k_h=3.0, mu_H=6.0, mu_PAO=1.0, mu_AOB=0.9, mu_NOB=0.65,
                q_fe=3.0, q_PHA=3.0, q_PP=1.5,
                b_H=0.4, b_PAO=0.2, b_PP=0.2, b_PHA=0.2, b_AOB=0.15, b_NOB=0.22,
                eta_NO3=0.6, eta_fe=0.4, eta_NO3_H=0.8, eta_NO2_H=0.8, eta_NO3_PAO=0.6, eta_NO2_PAO=0.6,
                eta_NO3_Hl=0.5, eta_NO3_PAOl=0.33, eta_NO3_PPl=0.33, eta_NO3_PHAl=0.33, eta_NO3_AUTl=0.33,
                # AOB/NOB DO half-saturation = Kaelin 2009 (0.8), NOT the stock X_AUT value (0.5),
                # so the production default matches the reference at all DO (not only saturating).
                K_O2=0.2, K_O2_H=0.2, K_O2_PAO=0.2, K_O2_AOB=0.8, K_O2_NOB=0.8,
                K_NO3=0.5, K_NO3_H=0.5, K_NO2_H=0.5, K_NO3_PAO=0.5, K_NO2_PAO=0.5, K_NO3_AUT=0.5,
                K_X=0.1, K_F=4.0, K_fe=4.0, K_A_H=4.0, K_A_PAO=4.0,
                K_NH4_H=0.05, K_NH4_PAO=0.05, K_NH4_AOB=0.14, K_NO2_NOB=0.28,
                K_P_H=0.01, K_P_PAO=0.01, K_P_AUT=0.01, K_P_S=0.2,
                K_PP=0.01, K_MAX=0.34, K_IPP=0.02, K_PHA=0.01,
                KI_FA_AOB=100.0, KI_FA_NOB=0.5, KI_FNA_AOB=0.5, KI_FNA_NOB=0.026,
                # Inorganic-carbon limitation of AOB, on speciated BICARBONATE (mg C/L).
                # A Monod SENSITIVITY PRIOR: 6.0 mg C/L is converted from ASM2d's typical
                # alkalinity-placeholder K_ALK (0.5 mol/m3, Henze et al. 2000), NOT an
                # experimentally fitted bicarbonate affinity -- treat as not_identifiable.
                # Applied to AOB only, per Guisasola 2007, who saw no NOB limitation even
                # below 0.1 mmol C/L. n=1 is Monod; n>1 is an UNSOURCED sensitivity shape,
                # never "Guisasola kinetics". DEFAULT OFF (0.0) so existing
                # calibrations and the aquakin parity run are bit-identical; switch it on for
                # any plant that strips CO2 hard or runs low alkalinity.
                K_HCO3_AOB=0.0, n_HCO3_AOB=1.0,
                ic_basis=None, ic_sigmoid_a=0.0, ic_sigmoid_k=1.0,
                # Low-pH cessation of AOB (Fumasoli 2015 Model 3, Presser/Ratkowsky form).
                # pH_min_AOB=5.4, K_pH_AOB=2.3 are their fitted values for N. eutropha.
                # DEFAULT OFF (None): above pH ~6.5 the term is ~1 anyway, and FA/FNA already
                # carry the pH 6-7 decrease -- adding a general pH factor would double-count.
                pH_min_AOB=None, K_pH_AOB=2.3,
                k_mmp=(8.4, 240, 1.0, 72, 1.0, 1.0e-5, 1.0e-5),
                # TODO(shared-kernel): this legacy mASM2d MMP table is NOT migrated to
                # process_model_schema.MADM1_MINERAL_EXTENSION_KERNEL (different model
                # family; out of scope 2026-08). Known discrepancies vs the kernel:
                # newberyite here is the retired Mg+HPO4 convention (pKsp 5.7) while the
                # kernel uses minteq.v4's proton-inclusive Mg+H+PO4 form (18.175); struvite
                # 13.5 vs kernel 13.26 (Ohlinger). Reconcile on migration, not piecemeal.
                pKsp=(8.45, 13.5, 5.7, 29.1, 7.4, 18.2, 26.4),
                K_dis=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
                K_AlOH=0.001, K_FeOH=0.001,
                pKa=(14, 9.25, 6.37, 10.32, 2.12, 7.21, 12.32, 4.76), pKa_hno2=3.25,
                T_op_K=None,
                **kwargs):
        # Temperature-correct the two equilibrium constants that set FA and FNA. The
        # defaults above are 25 C values, and the rate function has no temperature
        # argument -- so without this, a plant at 35 C computes free ammonia against
        # pKa 9.25 when the true value is 8.94, understating FA by a factor of 2.00 and
        # therefore understating NOB inhibition. This is a construction-boundary fix: the
        # rate function is untouched, and omitting T_op_K reproduces the 25 C behaviour
        # bit-identically.
        if T_op_K is not None:
            from math import log
            _T = float(T_op_K)
            pKa = list(pKa)
            # Emerson et al. 1975 / Anthonisen 1976, as used by engineering_utils
            # heuristics.nitrification.speciation.pKa_nh4
            pKa[1] = 0.09018 + 2729.92 / _T
            # van 't Hoff on HNO2, dH = 2300 J/mol (Anthonisen 1976 form)
            pKa_hno2 = 2300.0 / _T / log(10)
            pKa = tuple(pKa)

        if not path:
            path = _MASM2D_2N_TSV
        if components is None:
            components = create_masm2d_2n_components(set_thermo=True)
        cmps = components

        self = _Processes.load_from_file(path, components=cmps,
                                         conserved_for=('COD', 'C', 'N', 'P'),
                                         parameters=cls._stoichio_params, compile=False)

        mmp = _Processes.load_from_file(_MMP_TSV, components=cmps, conserved_for=(), compile=False)
        mmp_stoichio = {}
        df = _load_data(_MMP_TSV)
        for _i, _j in df.iterrows():
            _j.dropna(inplace=True)
            key = _j.index[_j == 1][0]
            _j = _j.to_dict()
            _j.pop(key)
            mmp_stoichio[key] = _j
        mol_to_mass = cmps.chem_MW / cmps.i_mass
        Ksp_mass = _np.array([10 ** (-p) for p in pKsp])
        _k = 0
        for _pd, xid in zip(mmp, cls._precipitates):
            for k, v in mmp_stoichio[xid].items():
                m2m = mol_to_mass[cmps.index(k)] * 1e3
                Ksp_mass[_k] *= m2m ** abs(v)
            _k += 1
            _pd._stoichiometry *= mol_to_mass
            _pd.ref_component = xid
        self.extend(mmp)
        self.compile(to_class=cls)

        dct = self.__dict__
        dct.update(kwargs)
        COD_deN1 = float(cmps.S_NO2.i_COD - cmps.S_NO3.i_COD)   # NO3->NO2 (~1.1423)
        COD_deN2 = float(cmps.S_N2.i_COD - cmps.S_NO2.i_COD)    # NO2->N2  (~1.7134)
        stoichio_vals = (f_SI, Y_H, Y_PAO, Y_PO4, Y_PHA, Y_AOB, Y_NOB,
                         f_XI_H, f_XI_PAO, f_XI_AUT, COD_deN1, COD_deN2,
                         cmps.X_PP.i_K, cmps.X_PP.i_Mg)
        dct['_parameters'] = dict(zip(cls._stoichio_params, stoichio_vals))
        dct['_edecay'] = bool(electron_acceptor_dependent_decay)
        dct['pH_ctrl'] = pH_ctrl
        h = 10 ** (-pH_ctrl) if pH_ctrl else None
        edecay = bool(electron_acceptor_dependent_decay)
        self.set_rate_function(
            lambda state_arr, params: rhos_masm2d_2n(state_arr, params, edecay, h))
        Ka = _np.array([10 ** (-p) for p in pKa])
        self.rate_function._params = dict(
            k_h=k_h, mu_H=mu_H, mu_PAO=mu_PAO, mu_AOB=mu_AOB, mu_NOB=mu_NOB,
            q_fe=q_fe, q_PHA=q_PHA, q_PP=q_PP,
            b_H=b_H, b_PAO=b_PAO, b_PP=b_PP, b_PHA=b_PHA, b_AOB=b_AOB, b_NOB=b_NOB,
            eta_NO3=eta_NO3, eta_fe=eta_fe, eta_NO3_H=eta_NO3_H, eta_NO2_H=eta_NO2_H,
            eta_NO3_PAO=eta_NO3_PAO, eta_NO2_PAO=eta_NO2_PAO,
            eta_NO3_Hl=eta_NO3_Hl, eta_NO3_PAOl=eta_NO3_PAOl, eta_NO3_PPl=eta_NO3_PPl,
            eta_NO3_PHAl=eta_NO3_PHAl, eta_NO3_AUTl=eta_NO3_AUTl,
            K_O2=K_O2, K_O2_H=K_O2_H, K_O2_PAO=K_O2_PAO, K_O2_AOB=K_O2_AOB, K_O2_NOB=K_O2_NOB,
            K_NO3=K_NO3, K_NO3_H=K_NO3_H, K_NO2_H=K_NO2_H, K_NO3_PAO=K_NO3_PAO,
            K_NO2_PAO=K_NO2_PAO, K_NO3_AUT=K_NO3_AUT,
            K_X=K_X, K_F=K_F, K_fe=K_fe, K_A_H=K_A_H, K_A_PAO=K_A_PAO,
            K_NH4_H=K_NH4_H, K_NH4_PAO=K_NH4_PAO, K_NH4_AOB=K_NH4_AOB, K_NO2_NOB=K_NO2_NOB,
            K_P_H=K_P_H, K_P_PAO=K_P_PAO, K_P_AUT=K_P_AUT, K_P_S=K_P_S,
            K_PP=K_PP, K_MAX=K_MAX, K_IPP=K_IPP, K_PHA=K_PHA,
            KI_FA_AOB=KI_FA_AOB, KI_FA_NOB=KI_FA_NOB, KI_FNA_AOB=KI_FNA_AOB, KI_FNA_NOB=KI_FNA_NOB,
            K_HCO3_AOB=K_HCO3_AOB, n_HCO3_AOB=n_HCO3_AOB,
            ic_basis=ic_basis, ic_sigmoid_a=ic_sigmoid_a, ic_sigmoid_k=ic_sigmoid_k,
            pH_min_AOB=pH_min_AOB, K_pH_AOB=K_pH_AOB,
            k_mmp=_np.array(k_mmp), Ksp=Ksp_mass, K_dis=_np.array(K_dis),
            K_AlOH=K_AlOH, K_FeOH=K_FeOH,
            Ka=Ka, Ka_hno2=10 ** (-pKa_hno2), cmps=cmps,
        )
        dct['solve_pH'] = solve_pH_2n
        # Henry constants mol/(L.atm) -> (mg/L)/atm, matching stock mASM2d's convention
        # so `gas_stripping=True` sees the units the reactor ODE expects.
        dct['K_Henry'] = [K * mol_to_mass[cmps.index(i)] * 1000
                          for K, i in zip(cls.K_Henry, cls.gas_IDs)]
        dct['H_cc'] = [1.0 / (K * _R_L_ATM * 298.15) for K in cls.K_Henry_molar_298]
        return self

    # ---- gas transfer -------------------------------------------------------
    # Stock mASM2d defines these; the two-step fork did not, so `gas_stripping=True`
    # raised AttributeError and CO2 stripping was structurally impossible. NH3 is added
    # here because a hard-aerated reactor at elevated pH strips ammonia, and if that is
    # not represented it is silently absorbed into apparent biological removal.
    #
    # Henry constants are 20 C, mol/(L.atm); converted to (mg/L)/atm at construction in
    # the same way the stock model does.
    gas_IDs = ['S_N2', 'S_IC', 'S_NH4']
    kLa_min = [3.0, 3.0, 3.0]
    K_Henry = [6.5e-4, 3.5e-2, 5.9e1]      # N2, CO2, NH3

    #: The SAME constants, kept in molar form because the instance attribute above is
    #: overwritten with mass units at construction. Needed to derive the dimensionless
    #: Henry coefficient H_cc = C_gas/C_liq = 1/(K.R.T), which is what bounds how much
    #: of a species a bubble can physically carry away. Note the 1,700x spread between
    #: CO2 and NH3: that spread is the whole reason one strips and the other does not.
    K_Henry_molar_298 = (6.5e-4, 3.5e-2, 5.9e1)
    _K_HENRY_VANT_HOFF = (1300.0, 2400.0, 4200.0)
    D_gas = [1.88e-9, 1.92e-9, 2.00e-9]    # m2/s
    p_gas_atm = [0.78, 3.947e-4, 1.0e-9]   # NH3 in ambient air is effectively zero

    #: Gas states that are only PARTLY volatile, because the state variable is a total
    #: over an acid-base pair. Stripping must act on the volatile fraction alone.
    _SPECIATING_GASES = ('S_IC', 'S_NH4')

    def volatile_fractions(self, state_arr, h=None):
        """Fraction of each ``gas_IDs`` state that is actually in the volatile form.

        QSDsan's generic stripping term is ``kLa * (QC[gas_idx] - S_gas_air)``, i.e. it
        applies the driving force to the **named state**. For ``S_N2`` that is right --
        the state is dissolved N2. For ``S_IC`` and ``S_NH4`` it is wrong: those are
        totals over CO2/HCO3-/CO3-- and NH3/NH4+, and only the neutral member volatilises.

        Using the total over-strips carbon by 2-6x at pH 5.9-6.9 and by ~15x at pH 7.5 --
        which would drain the inorganic carbon the AOB depend on within minutes at high
        airflow, while the biology carried on nitrifying against carbon that was gone.

        Returns an array aligned with ``gas_IDs``.
        """
        p = self.rate_function._params
        s = _np.maximum(_np.asarray(state_arr, dtype=float), 0.0)
        Ka = p['Ka']
        Kw, Knh, Kc1, Kc2 = Ka[0], Ka[1], Ka[2], Ka[3]
        if h is None:
            # Honour pH_ctrl first. Falling straight through to the charge-balance
            # solve would let gas stripping speciate on a PREDICTED pH while the
            # biology speciates FA/FNA on the MEASURED one -- the two would disagree
            # silently, and only in the mode built for measured-pH replay.
            h = self._fixed_h()
        if h is None:
            h = solve_pH_2n(s, Ka, p['mass2mol'] if 'mass2mol' in p
                            else p['cmps'].i_mass / p['cmps'].chem_MW)
        out = _np.ones(len(self.gas_IDs))
        for i, gid in enumerate(self.gas_IDs):
            if gid == 'S_IC':
                # CO2 fraction of total inorganic carbon
                out[i] = float(_ion_speciation(h, Kc1, Kc2)[0])
            elif gid == 'S_NH4':
                # NH3 fraction of total ammoniacal nitrogen
                out[i] = float(Knh / (Knh + h))
        return out

    def set_pH_ctrl(self, pH=None):
        """Re-bind the rate function to a new fixed pH, or to the live pH solve.

        ``pH_ctrl`` is closed over in the ``__new__`` lambda, so assigning to it has no
        effect on the compiled rate function. A daily replay that forces measured pH must
        go through here, or every segment silently keeps the pH the model was built with.

        Pass ``None`` to restore the charge-balance solve.

        .. warning::
           ``Processes.set_rate_function`` constructs a **fresh** ``MultiKinetics``
           object and does **not** carry ``_params`` across. Rebinding without
           re-attaching them leaves an empty parameter dict, and the next rate
           evaluation dies with ``KeyError: 'cmps'``. Capture and restore them.
        """
        params = getattr(self.rate_function, '_params', None)
        self.__dict__['pH_ctrl'] = pH
        h = 10 ** (-pH) if pH else None
        edecay = bool(self.__dict__.get('_edecay', True))
        self.set_rate_function(
            lambda state_arr, params: rhos_masm2d_2n(state_arr, params, edecay, h))
        if params:
            self.rate_function._params = params
        return self

    def _fixed_h(self):
        """Hydrogen-ion activity implied by ``pH_ctrl``, or ``None`` for the live solve.

        Anything that speciates -- FA/FNA in the rate function, CO2/NH3 fractions for
        gas transfer -- must resolve pH the *same* way, or the biology runs on measured
        pH while stripping runs on predicted pH and the two silently disagree.
        """
        pH = self.__dict__.get('pH_ctrl')
        return 10 ** (-pH) if pH else None

    def set_temperature(self, T_op_K):
        'Re-derive every temperature-dependent equilibrium constant in place.'
        from math import exp, log
        _T = float(T_op_K)
        _T25 = 298.15
        params = self.rate_function._params
        Ka = params['Ka'].copy()

        Ka[0] = 10 ** (-(4470.99 / _T - 6.0875 + 0.01706 * _T))          # water
        Ka[1] = 10 ** (-(0.09018 + 2729.92 / _T))                        # NH4+/NH3
        Ka[2] = 10 ** (-(3404.71 / _T + 0.032786 * _T - 14.8435))        # H2CO3*/HCO3-
        Ka[3] = 10 ** (-(2902.39 / _T + 0.02379 * _T - 6.4980))          # HCO3-/CO3=

        # van 't Hoff on the remaining weak acids. dH values are standard 25 C molar
        # ionisation enthalpies (J/mol); acetate's is near zero, which is why acetate
        # barely moves and is included for consistency rather than effect.
        _R = 8.314
        for idx, pKa25, dH in (
            (4, 2.12, -8.0e3),      # H3PO4  / H2PO4-
            (5, 7.21, 4.2e3),       # H2PO4- / HPO4=
            (6, 12.32, 16.0e3),     # HPO4=  / PO4(3-)
            (7, 4.76, -0.41e3),     # acetic acid
        ):
            K25 = 10 ** (-pKa25)
            Ka[idx] = K25 * exp(-dH / _R * (1.0 / _T - 1.0 / _T25))

        params['Ka'] = Ka
        params['Ka_hno2'] = 10 ** (-(2300.0 / _T / log(10)))             # HNO2/NO2-

        # Henry coefficients fall with temperature, so a warm reactor strips harder. This
        # matters directly here: CO2 is the AOB carbon source AND the buffer, so getting
        # its solubility wrong at 35 C biases both the inorganic-carbon limitation term
        # and the alkalinity balance the whole diagnosis rests on.
        base = getattr(self, '_K_Henry_298', None)
        if base is None:
            base = list(self.K_Henry)
            self.__dict__['_K_Henry_298'] = base
        vant_hoff_C = self.__dict__.get('_K_Henry_vant_hoff', (1300.0, 2400.0, 4200.0))
        self.__dict__['K_Henry'] = [
            h * exp(c * (1.0 / _T - 1.0 / _T25))
            for h, c in zip(base, vant_hoff_C)
        ]

        # H_cc = C_gas/C_liq = 1/(K_molar.R.T) -- the dimensionless Henry coefficient,
        # which is what bounds the flux. Must move with T alongside K_Henry.
        self.__dict__['H_cc'] = [
            1.0 / (K * exp(c * (1.0 / _T - 1.0 / _T25)) * _R_L_ATM * _T)
            for K, c in zip(self.K_Henry_molar_298, self._K_HENRY_VANT_HOFF)
        ]

        self.__dict__['T_op_K'] = _T
        return self
