"""Two-pathway AOB nitrous-oxide (N2O) extension of the two-step nitrification model.

`mASM2d2N_N2O` — extends the custom `mASM2d2N` (two-step nitrification, AOB/NOB + FA/FNA) with
the two-pathway AOB N2O model of Pocquet et al. (2016), resolving the AOB electron-transport chain
through explicit intermediates:

    NH4 --AMO--> NH2OH --HAO(+growth)--> NO --HAO--> NO2

plus dissolved N2O produced by two competing pathways:
  * NN (hydroxylamine / nitrifier-nitrification): NO reduced to N2O coupled to NH2OH oxidation,
  * ND (nitrifier-denitrification): free nitrous acid (FNA) reduced to N2O, with a Haldane DO term
    (N2O production rises as DO falls to a maximum, then falls toward zero DO).

The single lumped AOB nitritation of mASM2d2N is replaced by five AOB processes; NOB, heterotrophs,
PAO, denitrification, decay and mineral precipitation are inherited unchanged. NOTE this is a
DESIGN/ops model of *dissolved* N2O; gas stripping to the off-gas (the emission the twin reports) is a
reactor-side concern (see the reporting helper), not modelled in the liquid Petersen matrix.

Reference: Pocquet, M., Wu, Z., Queinnec, I. & Sperandio, M. (2016). A two pathway model for N2O
emissions by ammonium oxidizing bacteria supported by the NO/N2O variation. Water Research 88, 948-959.
"""

from __future__ import annotations

import os as _os
from typing import Dict

# Reuse the two-step machinery verbatim (no logic drift): shared numerical helpers and the
# inheritance/compile pattern all come from the mASM2d2N implementation.
import numpy as _np
from qsdsan import Processes as _Processes, CompiledProcesses as _CompiledProcesses
from qsdsan.process_models import ion_speciation as _ion_speciation
from qsdsan.process_models._asm2d import acid_base_rxn as _acid_base_rxn
from qsdsan.utils import ospath as _ospath, data_path as _qs_data_path, load_data as _load_data
from scipy.optimize import brenth as _brenth
from .physicality import record_ph_solve_failure as _record_ph_solve_failure
from qsdsan_engine_mcp.models.masm2d import _M, _inhib

_MMP_TSV = _ospath.join(_qs_data_path, 'process_data/_mmp.tsv')

_MASM2D_2N_N2O_TSV = _os.path.join(_os.path.dirname(__file__), 'process_data', '_masm2d_2n_n2o.tsv')

# NH4-referenced electron count (from ammonium, N -III) for each N-oxide state — the basis for the
# explicit electron-COD QSDsan cannot auto-derive for the neutral N gases.
_NOXIDE_ELECTRONS = {'S_NH2OH': 2, 'S_NO': 5, 'S_N2O': 4}  # NH2OH(-I), NO(+II), N2O(+I) vs NH4(-III)


def create_masm2d_2n_n2o_components(set_thermo: bool = True):
    """Return the 36-component two-step-nitrification + AOB-N2O (``mASM2d2N_N2O``) package.

    Extends the 33-component ``mASM2d2N`` set by inserting the three AOB electron-transport
    intermediates right after ``S_NO2``: hydroxylamine ``S_NH2OH``, nitric oxide ``S_NO`` and
    dissolved nitrous oxide ``S_N2O`` (all measured as N, electrically neutral). Component order:

        S_O2 S_N2 S_NH4 S_NO3 S_NO2 S_NH2OH S_NO S_N2O S_PO4 S_F S_A S_I S_IC S_K S_Mg
        X_I X_S X_H X_PAO X_PP X_PHA X_AOB X_NOB
        S_Ca X_CaCO3 X_struv X_newb X_ACP X_MgCO3 X_AlOH X_AlPO4 X_FeOH X_FePO4 S_Na S_Cl H2O

    QSDsan auto-derives i_COD = 0 for the neutral N-oxide gases, so their NH4-referenced
    electron-COD is set explicitly on the SAME per-electron basis as the model's own ``S_NO2``
    (NH2OH 2 e-, NO 5 e-, N2O 4 e- from ammonium) — so the Petersen COD-conservation ``?`` solver
    closes to machine precision. ``i_charge`` stays 0 (neutral), so the pH charge balance is
    unchanged from mASM2d2N (NOx = S_NO3 + S_NO2 only).
    """
    import qsdsan as qs
    from qsdsan import Component, Components, process_models as pc

    base = pc.create_masm2d_cmps(set_thermo=False)   # stock 31-comp mASM2d set
    ion_kwargs = dict(particle_size='Soluble', degradability='Undegradable', organic=False)

    S_NO2 = Component.from_chemical('S_NO2', chemical='NO2-', measured_as='N',
                                    description='Nitrite nitrogen', **ion_kwargs)
    S_NO2.i_NOD = None
    per_e = float(S_NO2.i_COD) / 6.0   # S_NO2 is 6 e- from NH4 -> per-electron electron-COD

    def _noxide(ID, chem, desc):
        c = Component.from_chemical(ID, chemical=chem, measured_as='N',
                                    description=desc, **ion_kwargs)
        c.i_NOD = None
        c.i_COD = _NOXIDE_ELECTRONS[ID] * per_e   # signed like S_NO2 (negative: oxidized N)
        return c

    S_NH2OH = _noxide('S_NH2OH', 'hydroxylamine', 'Hydroxylamine (AOB intermediate)')
    S_NO = _noxide('S_NO', 'nitric oxide', 'Nitric oxide (AOB intermediate)')
    S_N2O = _noxide('S_N2O', 'nitrous oxide', 'Nitrous oxide (dissolved)')

    X_AUT = base.X_AUT
    X_AOB = X_AUT.copy('X_AOB'); X_AOB.description = 'Ammonia-oxidizing bacteria (NH4->NO2 via NH2OH/NO)'
    X_NOB = X_AUT.copy('X_NOB'); X_NOB.description = 'Nitrite-oxidizing bacteria (NO2->NO3)'

    new = []
    for c in base:
        if c.ID == 'X_AUT':
            new.extend([X_AOB, X_NOB])
            continue
        new.append(c)
        if c.ID == 'S_NO3':
            new.extend([S_NO2, S_NH2OH, S_NO, S_N2O])   # nitrite + AOB electron-chain intermediates

    cmps = Components(new)
    cmps.default_compile(ignore_inaccurate_molar_weight=True, adjust_MW_to_measured_as=True)
    if set_thermo:
        qs.set_thermo(cmps)
    return cmps


# state_arr indices — 36-component mASM2d2N_N2O order (S_NH2OH/S_NO/S_N2O inserted after S_NO2,
# so everything from S_PO4 onward shifts +3 relative to mASM2d2N).
(_S_O2, _S_N2, _S_NH4, _S_NO3, _S_NO2, _S_NH2OH, _S_NO, _S_N2O, _S_PO4, _S_F, _S_A, _S_I,
 _S_IC, _S_K, _S_Mg, _X_I, _X_S, _X_H, _X_PAO, _X_PP, _X_PHA, _X_AOB, _X_NOB,
 _S_Ca, _X_CaCO3, _X_struv, _X_newb, _X_ACP, _X_MgCO3, _X_AlOH, _X_AlPO4, _X_FeOH, _X_FePO4,
 _S_Na, _S_Cl) = range(35)

_N_BIO = 29          # biological processes (mASM2d2N 25 - 1 nitritation + 5 AOB)
_N_PRECIP = 7        # mineral precipitation/dissolution


def _f_DO_ND(SO2, K_ND, KI_O2):
    """Haldane-type DO dependence of the ND-pathway N2O production (Pocquet 2016 / Boiocchi 2016).

    A NORMALISED substrate-inhibition factor that rises as DO falls to a maximum, then falls toward
    zero at DO->0:
        SO2 / (K_ND + (1 - 2*sqrt(K_ND/KI))*SO2 + SO2^2/KI)
    The ``sqrt(K_ND/KI)`` term makes the factor peak at **exactly 1.0** at SO2* = sqrt(K_ND*KI) — a
    proper modulating factor in [0, 1]. (The aquakin ``asm3_2step_n2o.yaml`` uses the inverted
    ``sqrt(KI/K_ND)``, which amplifies the peak ~10x and over-produces N2O; we follow the reference
    Boiocchi PhD thesis / Pocquet 2016 form instead.)
    """
    SO2 = SO2 if SO2 > 0 else 0.0
    denom = K_ND + (1.0 - 2.0 * (K_ND / KI_O2) ** 0.5) * SO2 + SO2 * SO2 / KI_O2
    return SO2 / denom if denom > 0 else 0.0


def solve_pH_2n_n2o(state_arr, Ka, unit_conversion):
    """pH solve for the 36-component set; NOx charge = S_NO3 + S_NO2 (the N-oxide gases are neutral)."""
    m = state_arr[:35] * unit_conversion[:35] * 1e-3   # mol/L
    ions = _np.array([
        m[_S_K], m[_S_Mg], m[_S_Ca], m[_S_Na], m[_S_Cl],
        m[_S_NO3] + m[_S_NO2],           # NOx (both anions); NH2OH/NO/N2O carry no charge
        m[_S_NH4], m[_S_IC], m[_S_PO4], m[_S_A],
    ])
    try:
        return _brenth(_acid_base_rxn, 1e-14, 1.0, args=(ions, Ka), xtol=1e-12, maxiter=100)
    except (ValueError, RuntimeError, FloatingPointError):
        _record_ph_solve_failure("masm2d_2n_n2o")
        return 1e-7


def rhos_masm2d_2n_n2o(state_arr, params, acceptor_dependent_decay=True, h=None):
    """Process rates for two-step nitrification + two-pathway AOB N2O (Pocquet 2016) mASM2d.

    Layout: 29 biological processes then 7 precipitation. The lumped AOB nitritation of mASM2d2N is
    replaced by the five-process AOB electron chain (AMO / HAO-growth / HAO / NN / ND); NOB,
    heterotrophs, PAO, denitrification, decay and precipitation are the mASM2d2N processes at shifted
    indices. pH is solved before the rates so FNA drives the ND N2O pathway (and NOB FA/FNA inhibition).
    """
    p = params
    cmps = p['cmps']
    if 'mass2mol' not in p:
        p['mass2mol'] = cmps.i_mass / cmps.chem_MW
    m2m = p['mass2mol']

    s = _np.maximum(state_arr, 0.0)   # clip transient negatives (stiff-step overshoot)
    S_O2, S_NH4, S_NO3, S_NO2, S_PO4, S_F, S_A = (
        s[_S_O2], s[_S_NH4], s[_S_NO3], s[_S_NO2], s[_S_PO4], s[_S_F], s[_S_A])
    S_NH2OH, S_NO, S_N2O = s[_S_NH2OH], s[_S_NO], s[_S_N2O]
    S_IC = s[_S_IC]
    X_H, X_PAO, X_PP, X_PHA, X_AOB, X_NOB, X_S = (
        s[_X_H], s[_X_PAO], s[_X_PP], s[_X_PHA], s[_X_AOB], s[_X_NOB], s[_X_S])

    rhos = _np.zeros(_N_BIO + _N_PRECIP)

    # ---- pH + free-species speciation (early, for FNA/FA coupling) ----
    Ka = p['Ka']
    Kw, Knh, Kc1, Kc2, Kp1, Kp2, Kp3, Kac = Ka
    if h is None:
        h = solve_pH_2n_n2o(s, Ka, m2m)
    FA = S_NH4 * Knh / (Knh + h)                      # free ammonia (mg N/L)
    Ka_hno2 = p['Ka_hno2']
    FNA = S_NO2 * h / (Ka_hno2 + h)                   # free nitrous acid (mg N/L) = aquakin S_HNO2

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

    # ---- heterotrophic growth on S_F / S_A (aerobic 3,4; denite 5-8; ferment 9; lysis 10) ----
    if (S_F + S_A) > 0 and X_H > 0:
        nut_H = _M(S_NH4, p['K_NH4_H']) * _M(S_PO4, p['K_P_H'])
        base_F = p['mu_H'] * nut_H * _M(S_F, p['K_F']) * S_F / (S_F + S_A) * X_H
        base_A = p['mu_H'] * nut_H * _M(S_A, p['K_A_H']) * S_A / (S_F + S_A) * X_H
        o2_H = _M(S_O2, p['K_O2_H'])
        anox_H = (1 - o2_H)
        rhos[3] = base_F * o2_H
        rhos[4] = base_A * o2_H
        rhos[5] = base_F * p['eta_NO3_H'] * anox_H * _M(S_NO3, p['K_NO3_H'])
        rhos[6] = base_F * p['eta_NO2_H'] * anox_H * _M(S_NO2, p['K_NO2_H'])
        rhos[7] = base_A * p['eta_NO3_H'] * anox_H * _M(S_NO3, p['K_NO3_H'])
        rhos[8] = base_A * p['eta_NO2_H'] * anox_H * _M(S_NO2, p['K_NO2_H'])
        rhos[9] = p['q_fe'] * (1 - o2_H) * (1 - _M(NOx, p['K_NO3_H'])) * _M(S_F, p['K_fe']) * X_H
    hlys = p['b_H'] * X_H
    if acceptor_dependent_decay:
        o2_H = _M(S_O2, p['K_O2_H'])
        hlys *= (o2_H + p['eta_NO3_Hl'] * (1 - o2_H) * _M(NOx, p['K_NO3_H']))
    rhos[10] = hlys

    # ---- PAO storage / growth (11-20) ----
    if X_PAO > 0:
        pp_pao = X_PP / X_PAO
        pha_pao = X_PHA / X_PAO
        rhos[11] = (p['q_PHA'] * _M(S_A, p['K_A_PAO']) * _M(pp_pao, p['K_PP']) * X_PAO)
        stor = p['q_PP'] * _M(pha_pao, p['K_PHA']) * _M(p['K_MAX'] - pp_pao, p['K_IPP']) \
            * _M(S_PO4, p['K_P_S']) * X_PAO
        o2_PAO = _M(S_O2, p['K_O2_PAO'])
        rhos[12] = stor * o2_PAO
        rhos[13] = stor * p['eta_NO3_PAO'] * (1 - o2_PAO) * _M(S_NO3, p['K_NO3_PAO'])
        rhos[14] = stor * p['eta_NO2_PAO'] * (1 - o2_PAO) * _M(S_NO2, p['K_NO2_PAO'])
        grow = p['mu_PAO'] * _M(S_NH4, p['K_NH4_PAO']) * _M(S_PO4, p['K_P_PAO']) * _M(pha_pao, p['K_PHA']) * X_PAO
        rhos[15] = grow * o2_PAO
        rhos[16] = grow * p['eta_NO3_PAO'] * (1 - o2_PAO) * _M(S_NO3, p['K_NO3_PAO'])
        rhos[17] = grow * p['eta_NO2_PAO'] * (1 - o2_PAO) * _M(S_NO2, p['K_NO2_PAO'])
        plys = p['b_PAO'] * X_PAO
        pplys = p['b_PP'] * X_PP
        phalys = p['b_PHA'] * X_PHA
        if acceptor_dependent_decay:
            sw = o2_PAO + p['eta_NO3_PAOl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
            plys *= sw
            pplys *= o2_PAO + p['eta_NO3_PPl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
            phalys *= o2_PAO + p['eta_NO3_PHAl'] * (1 - o2_PAO) * _M(NOx, p['K_NO3_PAO'])
        rhos[18] = plys
        rhos[19] = pplys
        rhos[20] = phalys

    # ---- AOB electron-transport chain + N2O (21-25), Pocquet 2016 (replaces lumped nitritation) ----
    # AMO: NH4 -> NH2OH (O2 consumed)
    rhos[21] = p['q_AOB_AMO'] * _M(S_O2, p['K_O2_AOB_1']) * _M(S_NH4, p['K_NH3_AOB']) * X_AOB
    # HAO + growth: NH2OH -> NO, AOB grows; the near-step NH4 switch stops growth only at S_NH4=0
    rhos[22] = (p['q_AOB_HAO'] * _M(S_O2, p['K_O2_AOB_2']) * _M(S_NH2OH, p['K_NH2OH_AOB'])
                * _M(S_NH4, p['K_NH3_limit']) * X_AOB)
    # HAO: NO -> NO2 (O2 consumed)
    rhos[23] = p['q_AOB_HAO'] * _M(S_O2, p['K_O2_AOB_2']) * _M(S_NO, p['K_NO_AOB_HAO']) * X_AOB
    # NN pathway: NO -> N2O coupled to NH2OH oxidation
    rhos[24] = p['q_AOB_N2O_NN'] * _M(S_NH2OH, p['K_NH2OH_AOB']) * _M(S_NO, p['K_NO_AOB_NN']) * X_AOB
    # ND pathway: free nitrous acid -> N2O, with Haldane DO dependence
    rhos[25] = (p['q_AOB_N2O_ND'] * _M(S_NH2OH, p['K_NH2OH_AOB'])
                * _M(FNA, p['K_HNO2_AOB']) * _f_DO_ND(S_O2, p['K_O2_AOB_ND'], p['KI_O2_AOB']) * X_AOB)

    # ---- nitratation (26 NOB) with FA/FNA inhibition (inherited from mASM2d2N) ----
    nut_P = _M(S_PO4, p['K_P_AUT'])
    rhos[26] = (p['mu_NOB'] * X_NOB * _M(S_O2, p['K_O2_NOB']) * _M(S_NO2, p['K_NO2_NOB'])
                * nut_P * _inhib(FA, p['KI_FA_NOB']) * _inhib(FNA, p['KI_FNA_NOB']))
    # nitrifier decay (27 AOB, 28 NOB)
    aob_dec = p['b_AOB'] * X_AOB
    nob_dec = p['b_NOB'] * X_NOB
    if acceptor_dependent_decay:
        o2_A = _M(S_O2, p['K_O2_AOB'])
        o2_N = _M(S_O2, p['K_O2_NOB'])
        aob_dec *= o2_A + p['eta_NO3_AUTl'] * (1 - o2_A) * _M(NOx, p['K_NO3_AUT'])
        nob_dec *= o2_N + p['eta_NO3_AUTl'] * (1 - o2_N) * _M(NOx, p['K_NO3_AUT'])
    rhos[27] = aob_dec
    rhos[28] = nob_dec

    # ---- precipitation / dissolution (29-35), same minerals as mASM2d2N ----
    S_Ca, S_Mg = s[_S_Ca], s[_S_Mg]
    X_CaCO3, X_struv, X_newb, X_ACP, X_MgCO3, X_AlOH, X_FeOH = (
        s[_X_CaCO3], s[_X_struv], s[_X_newb], s[_X_ACP], s[_X_MgCO3], s[_X_AlOH], s[_X_FeOH])
    nh4 = S_NH4 * h / (Knh + h)
    co2, hco3, co3 = S_IC * _ion_speciation(h, Kc1, Kc2)
    h3po4, h2po4, hpo4, po4 = S_PO4 * _ion_speciation(h, Kp1, Kp2, Kp3)
    # TODO(shared-kernel): Ksp/pKsp here follow the legacy mASM2d table (see the
    # pKsp TODO in masm2d.py) — newberyite is the retired Mg+HPO4 convention and
    # struvite differs from MADM1_MINERAL_EXTENSION_KERNEL; do not migrate piecemeal.
    Ksp = p['Ksp']
    SI = (S_Ca * co3 / Ksp[0]) ** (1 / 2)
    if SI > 1: rhos[29] = X_CaCO3 * (SI - 1) ** 2
    SI = (S_Mg * nh4 * po4 / Ksp[1]) ** (1 / 3)
    if SI > 1: rhos[30] = X_struv * (SI - 1) ** 3
    SI = (S_Mg * hpo4 / Ksp[2]) ** (1 / 2)
    if SI > 1: rhos[31] = X_newb * (SI - 1) ** 2
    SI = (S_Ca ** 3 * po4 ** 2 / Ksp[3]) ** (1 / 5)
    if SI > 1: rhos[32] = X_ACP * (SI - 1) ** 2
    SI = (S_Mg * co3 / Ksp[4]) ** (1 / 2)
    if SI > 1: rhos[33] = X_MgCO3 * (SI - 1) ** 2
    rhos[34] = X_AlOH * po4 * _M(X_AlOH, p['K_AlOH'])
    rhos[35] = s[_X_FeOH] * po4 * _M(s[_X_FeOH], p['K_FeOH'])
    rhos[29:36] *= p['k_mmp']
    return rhos


class ModifiedASM2d_N2O(_CompiledProcesses):
    """Two-step nitrification + two-pathway AOB N2O (``mASM2d2N_N2O``), Pocquet et al. (2016).

    Extends :class:`~models.masm2d.ModifiedASM2d` (mASM2d2N) by resolving the AOB electron-transport
    chain (NH4->NH2OH->NO->NO2) and dissolved N2O via the NN (hydroxylamine) and ND (nitrifier-
    denitrification, free-nitrous-acid) pathways. NOB, heterotrophs, PAO, denitrification, decay and
    mineral precipitation are the mASM2d2N processes unchanged. Reuses stock CSTR/CompletelyMixedMBR
    reactors (2-argument rate signature). ``pH_ctrl=None`` solves pH each step (FNA drives the ND
    pathway); pass a float to fix pH.
    """

    _stoichio_params = ('f_SI', 'Y_H', 'Y_PAO', 'Y_PO4', 'Y_PHA', 'Y_AOB', 'Y_NOB',
                        'f_XI_H', 'f_XI_PAO', 'f_XI_AUT', 'COD_deN1', 'COD_deN2', 'K_XPP', 'Mg_XPP')
    _kinetic_params = ('k_h', 'mu_H', 'mu_PAO', 'mu_NOB',
                       'q_fe', 'q_PHA', 'q_PP', 'b_H', 'b_PAO', 'b_PP', 'b_PHA', 'b_AOB', 'b_NOB',
                       'eta_NO3', 'eta_fe', 'eta_NO3_H', 'eta_NO2_H', 'eta_NO3_PAO', 'eta_NO2_PAO',
                       'eta_NO3_Hl', 'eta_NO3_PAOl', 'eta_NO3_PPl', 'eta_NO3_PHAl', 'eta_NO3_AUTl',
                       'K_O2', 'K_O2_H', 'K_O2_PAO', 'K_O2_AOB', 'K_O2_NOB',
                       'K_NO3', 'K_NO3_H', 'K_NO2_H', 'K_NO3_PAO', 'K_NO2_PAO', 'K_NO3_AUT',
                       'K_X', 'K_F', 'K_fe', 'K_A_H', 'K_A_PAO', 'K_NH4_H', 'K_NH4_PAO', 'K_NO2_NOB',
                       'K_P_H', 'K_P_PAO', 'K_P_AUT', 'K_P_S', 'K_PP', 'K_MAX', 'K_IPP', 'K_PHA',
                       'KI_FA_NOB', 'KI_FNA_NOB',
                       # Pocquet 2016 AOB electron-chain + N2O kinetics
                       'q_AOB_AMO', 'q_AOB_HAO', 'q_AOB_N2O_NN', 'q_AOB_N2O_ND',
                       'K_O2_AOB_1', 'K_O2_AOB_2', 'K_NH3_AOB', 'K_NH2OH_AOB',
                       'K_NO_AOB_HAO', 'K_NO_AOB_NN', 'K_HNO2_AOB', 'K_O2_AOB_ND', 'KI_O2_AOB',
                       'K_NH3_limit',
                       'k_mmp', 'Ksp', 'K_dis', 'K_AlOH', 'K_FeOH', 'Ka', 'Ka_hno2', 'cmps')
    _acid_base_pairs = (('H+', 'OH-'), ('NH4+', 'NH3'),
                        ('CO2', 'HCO3-'), ('HCO3-', 'CO3-2'),
                        ('H3PO4', 'H2PO4-'), ('H2PO4-', 'HPO4-2'), ('HPO4-2', 'PO4-3'),
                        ('HAc', 'Ac-'),)
    _precipitates = ('X_CaCO3', 'X_struv', 'X_newb', 'X_ACP', 'X_MgCO3', 'X_AlPO4', 'X_FePO4')

    def __new__(cls, components=None, path=None,
                electron_acceptor_dependent_decay=True, pH_ctrl=None,
                f_SI=0.0, Y_H=0.625, Y_PAO=0.625, Y_PO4=0.4, Y_PHA=0.2, Y_AOB=0.15, Y_NOB=0.06,
                f_XI_H=0.1, f_XI_PAO=0.1, f_XI_AUT=0.1,
                k_h=3.0, mu_H=6.0, mu_PAO=1.0, mu_NOB=0.65,
                q_fe=3.0, q_PHA=3.0, q_PP=1.5,
                b_H=0.4, b_PAO=0.2, b_PP=0.2, b_PHA=0.2, b_AOB=0.15, b_NOB=0.22,
                eta_NO3=0.6, eta_fe=0.4, eta_NO3_H=0.8, eta_NO2_H=0.8, eta_NO3_PAO=0.6, eta_NO2_PAO=0.6,
                eta_NO3_Hl=0.5, eta_NO3_PAOl=0.33, eta_NO3_PPl=0.33, eta_NO3_PHAl=0.33, eta_NO3_AUTl=0.33,
                K_O2=0.2, K_O2_H=0.2, K_O2_PAO=0.2, K_O2_AOB=0.8, K_O2_NOB=0.8,
                K_NO3=0.5, K_NO3_H=0.5, K_NO2_H=0.5, K_NO3_PAO=0.5, K_NO2_PAO=0.5, K_NO3_AUT=0.5,
                K_X=0.1, K_F=4.0, K_fe=4.0, K_A_H=4.0, K_A_PAO=4.0,
                K_NH4_H=0.05, K_NH4_PAO=0.05, K_NO2_NOB=0.28,
                K_P_H=0.01, K_P_PAO=0.01, K_P_AUT=0.01, K_P_S=0.2,
                K_PP=0.01, K_MAX=0.34, K_IPP=0.02, K_PHA=0.01,
                KI_FA_NOB=0.5, KI_FNA_NOB=0.026,
                # --- Pocquet et al. (2016) AOB electron-chain + N2O (rates already per-day) ---
                q_AOB_AMO=5.20, q_AOB_HAO=5.20, q_AOB_N2O_NN=0.0078, q_AOB_N2O_ND=1.30,
                K_O2_AOB_1=1.0, K_O2_AOB_2=0.6, K_NH3_AOB=0.20, K_NH2OH_AOB=0.90,
                K_NO_AOB_HAO=3.0e-4, K_NO_AOB_NN=0.008, K_HNO2_AOB=0.004,
                K_O2_AOB_ND=0.5, KI_O2_AOB=0.8, K_NH3_limit=1.0e-9,
                k_mmp=(8.4, 240, 1.0, 72, 1.0, 1.0e-5, 1.0e-5),
                pKsp=(8.45, 13.5, 5.7, 29.1, 7.4, 18.2, 26.4),
                K_dis=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
                K_AlOH=0.001, K_FeOH=0.001,
                pKa=(14, 9.25, 6.37, 10.32, 2.12, 7.21, 12.32, 4.76), pKa_hno2=3.25,
                T_op_K=None,
                **kwargs):
        # Same construction-boundary temperature correction as ModifiedASM2d, for the
        # same reason: the defaults above are 25 C values and the rate function has no
        # temperature argument, so without this a plant at 35 C computes free ammonia
        # against pKa 9.25 when the true value is 8.94 -- understating FA by a factor of
        # 2.00, and with it the AOB/NOB inhibition. This model carries an explicit
        # ammonia term (K_NH3_limit) on top of FA/FNA, so the error propagates into the
        # N2O pathway as well. Omitting T_op_K reproduces the 25 C behaviour
        # bit-identically.
        if T_op_K is not None:
            from math import log
            _T = float(T_op_K)
            pKa = list(pKa)
            # Emerson et al. 1975 / Anthonisen 1976
            pKa[1] = 0.09018 + 2729.92 / _T
            # van 't Hoff on HNO2, dH = 2300 J/mol (Anthonisen 1976 form)
            pKa_hno2 = 2300.0 / _T / log(10)
            pKa = tuple(pKa)

        if not path:
            path = _MASM2D_2N_N2O_TSV
        if components is None:
            components = create_masm2d_2n_n2o_components(set_thermo=True)
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
        COD_deN1 = float(cmps.S_NO2.i_COD - cmps.S_NO3.i_COD)   # NO3->NO2
        COD_deN2 = float(cmps.S_N2.i_COD - cmps.S_NO2.i_COD)    # NO2->N2
        stoichio_vals = (f_SI, Y_H, Y_PAO, Y_PO4, Y_PHA, Y_AOB, Y_NOB,
                         f_XI_H, f_XI_PAO, f_XI_AUT, COD_deN1, COD_deN2,
                         cmps.X_PP.i_K, cmps.X_PP.i_Mg)
        dct['_parameters'] = dict(zip(cls._stoichio_params, stoichio_vals))
        dct['_edecay'] = bool(electron_acceptor_dependent_decay)
        dct['pH_ctrl'] = pH_ctrl
        h = 10 ** (-pH_ctrl) if pH_ctrl else None
        edecay = bool(electron_acceptor_dependent_decay)
        self.set_rate_function(
            lambda state_arr, params: rhos_masm2d_2n_n2o(state_arr, params, edecay, h))
        Ka = _np.array([10 ** (-p) for p in pKa])
        self.rate_function._params = dict(
            k_h=k_h, mu_H=mu_H, mu_PAO=mu_PAO, mu_NOB=mu_NOB,
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
            K_NH4_H=K_NH4_H, K_NH4_PAO=K_NH4_PAO, K_NO2_NOB=K_NO2_NOB,
            K_P_H=K_P_H, K_P_PAO=K_P_PAO, K_P_AUT=K_P_AUT, K_P_S=K_P_S,
            K_PP=K_PP, K_MAX=K_MAX, K_IPP=K_IPP, K_PHA=K_PHA,
            KI_FA_NOB=KI_FA_NOB, KI_FNA_NOB=KI_FNA_NOB,
            q_AOB_AMO=q_AOB_AMO, q_AOB_HAO=q_AOB_HAO,
            q_AOB_N2O_NN=q_AOB_N2O_NN, q_AOB_N2O_ND=q_AOB_N2O_ND,
            K_O2_AOB_1=K_O2_AOB_1, K_O2_AOB_2=K_O2_AOB_2, K_NH3_AOB=K_NH3_AOB, K_NH2OH_AOB=K_NH2OH_AOB,
            K_NO_AOB_HAO=K_NO_AOB_HAO, K_NO_AOB_NN=K_NO_AOB_NN, K_HNO2_AOB=K_HNO2_AOB,
            K_O2_AOB_ND=K_O2_AOB_ND, KI_O2_AOB=KI_O2_AOB, K_NH3_limit=K_NH3_limit,
            k_mmp=_np.array(k_mmp), Ksp=Ksp_mass, K_dis=_np.array(K_dis),
            K_AlOH=K_AlOH, K_FeOH=K_FeOH,
            Ka=Ka, Ka_hno2=10 ** (-pKa_hno2), cmps=cmps,
        )
        dct['solve_pH'] = solve_pH_2n_n2o
        return self
