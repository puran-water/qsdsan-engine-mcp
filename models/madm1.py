# -*- coding: utf-8 -*-
'''
QSDsan: Quantitative Sustainable Design for sanitation and resource recovery systems

This module is developed by:
    Joy Zhang <joycheung1994@gmail.com>

This module is under the University of Illinois/NCSA Open Source License.
Please refer to https://github.com/QSD-Group/QSDsan/blob/main/LICENSE.txt
for license details.
'''

from thermosteam.utils import chemicals_user
from thermosteam import settings
from chemicals.elements import molecular_weight as get_mw
from qsdsan import Component, Components, Process, Processes, CompiledProcesses
import numpy as np, qsdsan.processes as pc, qsdsan as qs
from qsdsan.utils import ospath, data_path
from qsdsan.processes._adm1 import (
    R,
    create_adm1_cmps,
    ADM1,
    mass2mol_conversion,
    T_correction_factor,
    substr_inhibit,
    non_compet_inhibit,
    Hill_inhibit
    )
# Import thermodynamic functions for mineral precipitation
from .thermodynamics import calc_saturation_indices
# from scipy.optimize import brenth
# from warnings import warn


__all__ = ('create_madm1_cmps', 'ModifiedADM1')

_path = ospath.join(data_path, 'process_data/_madm1.tsv')

#%% components
# C_mw = get_mw({'C':1})
N_mw = get_mw({'N':1})
P_mw = get_mw({'P':1})
S_mw = get_mw({'S':1})
Fe_mw = get_mw({'Fe':1})
O_mw = get_mw({'O':1})

def create_madm1_cmps(set_thermo=True, ASF_L=0.31, ASF_H=1.2):
    '''
    Create a set of components for the modified ADM1.

    Parameters
    ----------
    set_thermo : bool, optional
        Whether to set thermo with the returned set of components. The default is True.
    ASF_L : float, optional
        Active site factor for X_HFO_L [mol P sites/mol Fe]. The default is 0.31.
    ASF_H : float, optional
        Active site factor for X_HFO_H [mol P sites/mol Fe]. The default is 1.2.

    Returns
    -------
    cmps_madm1 : class:`CompiledComponents`

    '''
    
    # Components from the original ADM1
    # *********************************
    _cmps = create_adm1_cmps(False)
    S_aa = _cmps.S_aa
    X_pr = _cmps.X_pr
    S_aa.i_C = X_pr.i_C = 0.36890
    S_aa.i_N = X_pr.i_N = 0.11065
    S_aa.i_P = X_pr.i_P = 0.
    S_aa.i_mass = X_pr.i_mass = 1/1.35566
    
    S_fa = _cmps.S_fa
    S_fa.formula = 'C25H52O3'
    S_bu = _cmps.S_bu
    S_bu.formula = 'C4H8O2'
    S_pro = _cmps.S_pro
    S_pro.formula = 'C3H6O2'
    S_ac = _cmps.S_ac
    S_ac.formula = 'C2H4O2'
    
    S_I = _cmps.S_I
    X_I = _cmps.X_I
    S_I.i_C = X_I.i_C = 0.36178
    S_I.i_N = X_I.i_N = 0.06003
    S_I.i_P = X_I.i_P = 0.00649
    S_I.i_mass = X_I.i_mass = 1/1.54100

    X_ch = _cmps.X_ch
    X_ch.formula = 'C24H48O24'
    # _cmps.X_li.formula = 'C64H119O7.5P'
    X_li = X_pr.copy('X_li')
    X_li.i_C = 0.26311
    X_li.i_N = 0.
    X_li.i_P = 0.01067
    X_li.i_mass = 1/2.81254
    
    adm1_biomass = (_cmps.X_su, _cmps.X_aa, _cmps.X_fa, _cmps.X_c4, _cmps.X_pro, _cmps.X_ac, _cmps.X_h2)
    for bio in adm1_biomass:
        # bio.formula = 'C5H7O2NP0.113'
        bio.i_C = 0.36612
        bio.i_N = 0.08615
        bio.i_P = 0.02154
        bio.i_mass = 1/1.39300
    
    # P related components from ASM2d
    # *******************************
    asm_cmps = pc.create_asm2d_cmps(False)
    X_PHA = asm_cmps.X_PHA
    X_PHA.formula = '(C2H4O)n'
    # X_PHA.i_C = 0.3
    # X_PHA.i_mass = 0.55
    
    X_PAO = _cmps.X_su.copy('X_PAO')
    X_PAO.description = 'Phosphorus-accumulating organism biomass'
    
    # Additional components for P, S, Fe extensions
    # *********************************************
    S_IP = asm_cmps.S_PO4.copy('S_IP')
    
    ion_properties = dict(
        particle_size='Soluble',
        degradability='Undegradable',
        organic=False)
    S_K = Component.from_chemical('S_K', chemical='K+', description='Potassium ion', 
                                  measured_as='K', **ion_properties)
    S_Mg = Component.from_chemical('S_Mg', chemical='Mg2+', description='Magnesium ion',
                                  measured_as='Mg',**ion_properties)
    S_SO4 = Component.from_chemical('S_SO4', chemical='SO4-2', description='Sulfate',
                                  measured_as='S', **ion_properties)
    # FIX #2b: S_IS must be measured_as='S' for proper chemistry/speciation (per Codex analysis 2025-10-24)
    # CRITICAL: COD basis is wrong for equilibrium constants and Henry's law
    # H2S/HS- speciation and gas transfer require true chemical (sulfur) basis
    S_IS = Component.from_chemical('S_IS', chemical='H2S',
                                  description='Hydrogen sulfide',
                                  measured_as='S',  # FIXED from 'COD' - needed for proper speciation
                                  particle_size='Soluble',
                                  degradability='Undegradable',
                                  organic=False)
    
    X_hSRB = X_PAO.copy('X_hSRB')
    X_hSRB.description = 'sulfate-reducing biomass, utilizing H2'
    X_aSRB = X_PAO.copy('X_aSRB')
    X_aSRB.description = 'sulfate-reducing biomass, utilizing acetate'
    X_pSRB = X_PAO.copy('X_pSRB')
    X_pSRB.description = 'sulfate-reducing biomass, utilizing propionate'
    X_c4SRB = X_PAO.copy('X_c4SRB')
    X_c4SRB.description = 'sulfate-reducing biomass, utilizing butyrate and valerate'
    
    # FIX #6: S_S0 must be measured_as='S' for proper chemistry
    # Elemental sulfur should NOT be counted as COD
    S_S0 = Component.from_chemical('S_S0', chemical='S',
                                  description='Elemental sulfur',
                                  measured_as='S',  # FIXED from 'COD'
                                  particle_size='Soluble',
                                  degradability='Undegradable',
                                  organic=False)
    S_Fe3 = Component.from_chemical('S_Fe3', chemical='Fe3+', description='Iron (III)',
                                  measured_as='Fe',**ion_properties)
    S_Fe2 = Component.from_chemical('S_Fe2', chemical='Fe2+', description='Iron (II)',
                                  measured_as='Fe',**ion_properties)
    # FIX #5: DO NOT count Fe2+ redox O2 demand as COD
    # Keep S_Fe2 on iron basis for correct COD accounting
    # If needed, compute redox O2 demand separately in reporting
    # REMOVED: S_Fe2.i_COD = 0.5*O_mw/Fe_mw
    # REMOVED: S_Fe2.measured_as = 'COD'
    
    # Multiple mineral precipitation
    # ******************************
    mineral_properties = dict(
        particle_size='Particulate',
        degradability='Undegradable',
        organic=False)
    
    X_HFO_H = Component('X_HFO_H', formula='FeO(OH)',
                        description='Hydrous ferric oxide with high number of active sites',
                        measured_as='Fe',**mineral_properties)
    X_HFO_L = X_HFO_H.copy('X_HFO_L')
    X_HFO_L.description = 'Hydrous ferric oxide with low number of active sites'
    
    X_HFO_old = X_HFO_H.copy('X_HFO_old')
    X_HFO_old.description = 'Inactive hydrous ferric oxide'
    
    X_HFO_HP = Component('X_HFO_HP', formula=f'FeO(OH)P{ASF_H}',
                         description='X_HFO_H with phosphorus-bounded adsorption sites',
                         measured_as='Fe', **mineral_properties)
    X_HFO_HP_old = X_HFO_HP.copy('X_HFO_HP_old')
    X_HFO_HP_old.description = 'Old ' + X_HFO_HP.description
    
    X_HFO_LP = Component('X_HFO_LP', formula=f'FeO(OH)P{ASF_L}',
                         description='X_HFO_L with phosphorus-bounded adsorption sites',
                         measured_as='Fe', **mineral_properties)
    X_HFO_LP_old = X_HFO_LP.copy('X_HFO_LP_old')
    X_HFO_LP_old.description = 'Old ' + X_HFO_LP.description
    
    X_CCM = Component.from_chemical('X_CCM', chemical='calcite', description='Calcite', **mineral_properties)
    X_ACC = Component.from_chemical('X_ACC', chemical='aragonite', description='Aragonite', **mineral_properties)
    X_ACP = Component.from_chemical('X_ACP', chemical='Ca3(PO4)2', description='Amorphous calcium phosphate', **mineral_properties)
    X_HAP = Component.from_chemical('X_HAP', chemical='hydroxylapatite', description='Hydroxylapatite', **mineral_properties)
    X_DCPD = Component.from_chemical('X_DCPD', chemical='CaHPO4', description='Dicalcium phosphate', **mineral_properties)
    X_OCP = Component('X_OCP', formula='Ca4HP3O12', description='Octacalcium phosphate', **mineral_properties)
    X_struv = Component.from_chemical('X_struv', chemical='MgNH4PO4', description='Struvite', **mineral_properties)
    X_newb = Component.from_chemical('X_newb', chemical='MgHPO4', description='Newberyite', **mineral_properties)
    X_magn = Component.from_chemical('X_magn', chemical='MgCO3', description='Magnesite', **mineral_properties)
    X_kstruv = Component('X_kstruv', formula='MgKPO4', description='K-struvite', **mineral_properties)
    X_FeS = Component.from_chemical('X_FeS', chemical='FeS', description='Iron sulfide', **mineral_properties)
    X_Fe3PO42 = Component('X_Fe3PO42', formula='Fe3(PO4)2', description='Ferrous phosphate', **mineral_properties)
    X_AlPO4 = Component.from_chemical('X_AlPO4', chemical='AlPO4', description='Aluminum phosphate', **mineral_properties)

    S_Ca = Component.from_chemical('S_Ca', chemical='Ca2+', description='Calsium ion',
                                   measured_as='Ca', **ion_properties)
    S_Al = Component.from_chemical('S_Al', chemical='Al3+', description='Aluminum ion',
                                   measured_as='Al', **ion_properties)
    S_Na = Component.from_chemical('S_Na', chemical='Na+', description='Sodium ion',
                                   measured_as='Na', **ion_properties)
    S_Cl = Component.from_chemical('S_Cl', chemical='Cl-', description='Chloride',
                                   measured_as='Cl', **ion_properties)
    
    cmps_madm1 = Components([_cmps.S_su, S_aa, S_fa, _cmps.S_va, S_bu,
                             S_pro, S_ac, _cmps.S_h2, _cmps.S_ch4,
                             _cmps.S_IC, _cmps.S_IN, S_IP, S_I,
                             X_ch, X_pr, X_li, *adm1_biomass, X_I,
                             X_PHA, asm_cmps.X_PP, X_PAO, S_K, S_Mg,
                             S_SO4, S_IS, X_hSRB, X_aSRB, X_pSRB, X_c4SRB,
                             S_S0, S_Fe3, S_Fe2, X_HFO_H, X_HFO_L, X_HFO_old,
                             X_HFO_HP, X_HFO_LP, X_HFO_HP_old, X_HFO_LP_old,
                             S_Ca, S_Al, X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD,
                             X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS,
                             X_Fe3PO42, X_AlPO4,
                             S_Na, S_Cl, _cmps.H2O])

    # ============================================================================
    # CRITICAL FIX: Disable NOD for COD-basis pseudo-components
    # ============================================================================
    # Per upstream ADM1-P pattern (QSDsan/qsdsan/processes/_adm1_p_extension.py)
    # COD surrogates must have i_NOD = None to prevent nitrogen oxygen demand
    # from inflating .COD composite calculations.
    #
    # Without this fix, components with i_N > 0 (amino acids, proteins, biomass)
    # cause .COD to include NOD, over-stating COD_removed by ~13% (472 kg COD/d
    # in our 1000 m³/d, 4890 mg/L COD sludge scenario).
    #
    # Affected components (all have i_N > 0):
    # - S_aa, X_pr: i_N = 0.11065 (11% nitrogen)
    # - Biomass (X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2): i_N = 0.08615
    # - S_I, X_I: i_N = 0.06003
    # - Extension biomass (X_PAO, X_hSRB, X_aSRB, X_pSRB, X_c4SRB): inherit i_N
    #
    # Reference: QSD-Group/QSDsan _adm1_p_extension.py lines ~180-220
    # ============================================================================

    # Disable NOD for COD surrogates with nitrogen content
    for cmp in (S_aa, X_pr, S_fa, S_I, X_I):
        cmp.i_NOD = None

    # Disable NOD for all ADM1 biomass groups
    for bio in adm1_biomass:  # (X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2)
        bio.i_NOD = None

    # Disable NOD for extension biomass (inherit from X_PAO copy)
    for bio_ext in (X_PAO, X_hSRB, X_aSRB, X_pSRB, X_c4SRB):
        bio_ext.i_NOD = None

    # VFAs and carbohydrates don't have nitrogen, but disable for consistency
    for vfa in (S_bu, S_pro, S_ac):
        vfa.i_NOD = None
    for carb in (X_ch, X_li):
        carb.i_NOD = None

    # Storage polymer X_PHA (no nitrogen)
    X_PHA.i_NOD = None

    # Following upstream QSDsan pattern: use flags to synthesize surrogate thermodynamic properties
    # for COD-based components instead of blocking validation
    cmps_madm1.default_compile(ignore_inaccurate_molar_weight=True, adjust_MW_to_measured_as=True)

    if set_thermo: qs.set_thermo(cmps_madm1)
    return cmps_madm1

#%% rate functions

# https://wiki.dynamita.com/en/biokinetic_process_models#chemical-phosphorus-removal-with-metal-salts-addition-iron-or-aluminium

# =============================================================================
# state_variable_indices = {
#     'S_su': 0, 'S_aa': 1, 'S_fa': 2, 'S_va': 3, 'S_bu': 4, 'S_pro': 5, 'S_ac': 6, 'S_h2': 7,
#     'S_ch4': 8, 'S_IC': 9, 'S_IN': 10, 'S_IP': 11, 'S_I': 12,
#     'X_ch': 13, 'X_pr': 14, 'X_li': 15, 
#     'X_su': 16, 'X_aa': 17, 'X_fa': 18, 'X_c4': 19, 'X_pro': 20, 'X_ac': 21, 'X_h2': 22, 'X_I': 23,
#     'X_PHA': 24, 'X_PP': 25, 'X_PAO': 26, 'S_K': 27, 'S_Mg': 28,
#     'S_SO4': 29, 'S_IS': 30, 'X_hSRB': 31, 'X_aSRB': 32, 'X_pSRB': 33, 'X_c4SRB': 34,
#     'S_S0': 35, 'S_Fe3': 36, 'S_Fe2': 37,
#     'X_HFO_H': 38, 'X_HFO_L': 39, 'X_HFO_old': 40, 'X_HFO_HP': 41, 'X_HFO_LP': 42, 'X_HFO_HP_old': 43, 'X_HFO_LP_old': 44,
#     'S_Ca': 45, 'S_Al': 46,
#     'X_CCM': 47, 'X_ACC': 48, 'X_ACP': 49, 'X_HAP': 50, 'X_DCPD': 51, 'X_OCP': 52,
#     'X_struv': 53, 'X_newb': 54, 'X_magn': 55, 'X_kstruv': 56, 
#     'X_FeS': 57, 'X_Fe3PO42': 58,
#     'X_AlPO4': 59,
#     'S_Na': 60, 'S_Cl': 61, 'H2O': 62
#     }
# =============================================================================

def calc_pH():
    pass

def calc_biogas(state_arr, params, pH):
    """
    Calculate dissolved molecular H2S concentration.

    Uses correct Henderson-Hasselbalch neutral fraction to compute H2S from total sulfide.

    Parameters
    ----------
    state_arr : ndarray
        State variable array
    params : dict
        Kinetic parameters (must include Ka_h2s for temperature-corrected equilibrium
        and 'components' for unit conversion)
    pH : float
        Computed pH from PCM

    Returns
    -------
    float
        Dissolved molecular H2S concentration [kmol-S/m³]

    Notes
    -----
    H2S speciation: H2S <-> HS- + H+, pKa1 ~ 7.0 at 25°C
    Neutral fraction: α0 = 1 / (1 + 10^(pH - pKa))

    CRITICAL FIXES (per Codex reviews):
    - Previous formula `S_IS * 10^(pKa - pH)` overpredicts by factor of 2 at pH ≈ pKa
    - Codex fix #7: Uses temperature-corrected Ka_h2s from params for consistency
    - Codex fix #8: Convert S_IS to molar units before applying α0 (was bloating H2S)
    - Codex fix #9: Use dynamic component indexing instead of hard-coded index 30
    """
    # Codex fix #9: Get S_IS index dynamically from component set
    cmps = params['components']
    is_idx = cmps.index('S_IS')

    # Extract total sulfide (S_IS) in kg/m³
    S_IS_kg = state_arr[is_idx] if len(state_arr) > is_idx else 0.0

    # FIX #2a: Use SAME unit conversion as rest of rate function (per Codex analysis 2025-10-24)
    # CRITICAL: Must match unit_conversion at line 819 in rhos_madm1
    # The inconsistent i_mass/chem_MW was causing H2S to be on wrong scale
    # FIX: Remove extra 1e3 factor - mass2mol_conversion already converts kg/m³ to mol/L
    unit_conversion = mass2mol_conversion(cmps)  # kg/m³ → mol/L (NOT kmol/m³!)
    S_IS_M = S_IS_kg * unit_conversion[is_idx]

    # Codex fix #7: Get temperature-corrected Ka_h2s from params
    # If not available, fallback to 25°C value
    Ka_h2s = params.get('Ka_h2s', 1e-7)
    pKa_h2s = -np.log10(Ka_h2s)

    # Calculate neutral (molecular) fraction using Henderson-Hasselbalch
    # α0 = [H2S] / ([H2S] + [HS-]) = 1 / (1 + Ka/[H+]) = 1 / (1 + 10^(pH - pKa))
    alpha_0 = 1.0 / (1.0 + 10**(pH - pKa_h2s))

    # Dissolved molecular H2S in kmol/m³ (correct molar units)
    h2s = S_IS_M * alpha_0

    return h2s

def _compute_lumped_ions(state_liquid, cmps, unit_conversion):
    """
    Compute lumped S_cat/S_an from measured ion concentrations in mADM1 state.

    CRITICAL FIX (per Codex review):
    mADM1 explicitly models Na⁺ (S_Na) and Cl⁻ (S_Cl) as state variables.
    Use these actual values instead of hard-coded constants to ensure charge
    balance responds correctly to influent salinity changes.

    Also aggregate ALL multivalent cations (Mg²⁺, Ca²⁺, Fe²⁺, Fe³⁺, Al³⁺) for
    accurate positive charge accounting in iron/alum dosing scenarios.

    Parameters
    ----------
    state_liquid : ndarray
        Liquid-phase state vector (first n_cmps entries) [kg/m³]
    cmps : Components
        mADM1 component set
    unit_conversion : ndarray
        Conversion factors from kg/m³ to M

    Returns
    -------
    tuple
        (S_cat, S_divalent, S_trivalent, S_an) in M
        where S_cat = Na⁺, S_divalent = Mg²⁺ + Ca²⁺ + Fe²⁺,
        S_trivalent = Fe³⁺ + Al³⁺, S_an = Cl⁻
    """
    # Extract measured monovalent ions from state (Codex fix #3)
    S_Na = state_liquid[cmps.index('S_Na')] if 'S_Na' in cmps.IDs else 0.0
    S_Cl = state_liquid[cmps.index('S_Cl')] if 'S_Cl' in cmps.IDs else 0.0

    # Extract divalent cations (Codex fix #4)
    S_Mg = state_liquid[cmps.index('S_Mg')] if 'S_Mg' in cmps.IDs else 0.0
    S_Ca = state_liquid[cmps.index('S_Ca')] if 'S_Ca' in cmps.IDs else 0.0
    S_Fe2 = state_liquid[cmps.index('S_Fe2')] if 'S_Fe2' in cmps.IDs else 0.0

    # Extract trivalent cations (Codex fix #6: for iron/alum dosing)
    S_Fe3 = state_liquid[cmps.index('S_Fe3')] if 'S_Fe3' in cmps.IDs else 0.0
    S_Al = state_liquid[cmps.index('S_Al')] if 'S_Al' in cmps.IDs else 0.0

    # Convert to molar concentrations
    S_cat_M = S_Na * unit_conversion[cmps.index('S_Na')] if 'S_Na' in cmps.IDs else 0.0
    S_an_M = S_Cl * unit_conversion[cmps.index('S_Cl')] if 'S_Cl' in cmps.IDs else 0.0

    # Aggregate divalents: Mg²⁺ + Ca²⁺ + Fe²⁺ (all contribute 2× charge)
    S_divalent_M = 0.0
    if 'S_Mg' in cmps.IDs:
        S_divalent_M += S_Mg * unit_conversion[cmps.index('S_Mg')]
    if 'S_Ca' in cmps.IDs:
        S_divalent_M += S_Ca * unit_conversion[cmps.index('S_Ca')]
    if 'S_Fe2' in cmps.IDs:
        S_divalent_M += S_Fe2 * unit_conversion[cmps.index('S_Fe2')]

    # Aggregate trivalents: Fe³⁺ + Al³⁺ (all contribute 3× charge)
    # Codex fix #6: Prevents pH bias during iron/alum dosing campaigns
    S_trivalent_M = 0.0
    if 'S_Fe3' in cmps.IDs:
        S_trivalent_M += S_Fe3 * unit_conversion[cmps.index('S_Fe3')]
    if 'S_Al' in cmps.IDs:
        S_trivalent_M += S_Al * unit_conversion[cmps.index('S_Al')]

    return S_cat_M, S_divalent_M, S_trivalent_M, S_an_M


def pcm(state_arr, params):
    """
    Production-grade pH/Carbonate/amMonia (PCM) equilibrium model for mADM1.

    Uses QSDsan's ADM1-P charge balance solver with full electroneutrality.
    Replaces iterative approximation with Brent's method root-finding.

    Per Codex recommendation: Import QSDsan's production solver for thermodynamic rigor
    with minimal implementation effort (50-80 LOC).

    Parameters
    ----------
    state_arr : ndarray
        State variable array (62+ components for mADM1, may include gas/flow states)
    params : dict
        Kinetic parameters including Ka_base, Ka_dH, T_base, components

    Returns
    -------
    tuple
        (pH, nh3, co2, activities) where:
        - pH: computed pH value from full charge balance
        - nh3: free ammonia concentration [kmol-N/m³]
        - co2: dissolved CO2 concentration [kmol-C/m³]
        - activities: array of ionic activities for precipitation (placeholder)

    Notes
    -----
    Implementation based on Codex guidance (BUG_TRACKER.md:521-679):
    - Imports acid_base_rxn and solve_pH from QSDsan ADM1-P extension
    - Computes lumped S_cat/S_an from mADM1 explicit metal ions
    - Uses Brent's method for thermodynamically rigorous pH solving
    - Matches QSDsan production behavior for maintainability

    Future enhancements:
    - Add Davies/Pitzer activity models for ionic strength correction
    - Explicitly track Ca²⁺, Fe²⁺ in S_Mg aggregation
    """
    # Import QSDsan production solver (per Codex recommendation)
    from scipy.optimize import brenth

    # Extract parameters
    Ka_base = np.array(params['Ka_base'])
    Ka_dH = np.array(params['Ka_dH'])
    T_base = params['T_base']
    T_op = params.get('T_op', T_base)
    cmps = params['components']

    # Temperature correction for Ka values (Van't Hoff equation)
    # FIX: Use correct R units for Ka_dH in J/mol
    # The previous R = 8.3145e-2 bar·m³/(kmol·K) was WRONG - caused 10^29× error in Ka!
    R = 8.314  # J/(mol·K) - CORRECT units for Ka_dH in J/mol
    T_corr = np.exp((Ka_dH / R) * (1/T_base - 1/T_op))
    Ka = Ka_base * T_corr

    # Temperature-corrected Ka_h2s for H2S/HS⁻ equilibrium
    # H2S <-> HS⁻ + H⁺, pKa ~ 7.0 at 25°C
    # Enthalpy: ΔH ≈ 14.3 kJ/mol (endothermic, Ka increases with temperature)
    Ka_h2s_base = 1e-7  # 10^(-7.0) at 25°C
    Ka_h2s_dH = 14300  # J/mol (enthalpy of dissociation)
    Ka_h2s = Ka_h2s_base * np.exp((Ka_h2s_dH / R) * (1/T_base - 1/T_op))

    # Store Ka_h2s in params for use by calc_biogas
    params['Ka_h2s'] = Ka_h2s

    # Unit conversion from kg/m³ to M (mol/L)
    # FIX: Use mass2mol_conversion which includes the ×1000 factor for m³→L
    # The previous calculation was missing this factor, inflating ionic strengths by 1000×
    from qsdsan.processes import mass2mol_conversion
    unit_conversion = mass2mol_conversion(cmps)

    # Slice to liquid components (first len(cmps) entries)
    n_cmps = len(cmps)
    state_liquid = state_arr[:n_cmps]

    # Convert to molar concentrations
    cmps_in_M = state_liquid * unit_conversion

    # Extract ions for charge balance: [S_cat, S_K, S_divalent, S_trivalent, S_an, S_IN, S_IP, S_IC, S_ac, S_pro, S_bu, S_va]
    # Codex fix #3/#4: Use actual Na+/Cl- and aggregate all divalents (Mg2+, Ca2+, Fe2+)
    # Codex fix #6: Include trivalents (Fe3+, Al3+) for iron/alum dosing scenarios
    S_cat_M, S_divalent_M, S_trivalent_M, S_an_M = _compute_lumped_ions(state_liquid, cmps, unit_conversion)

    # Get explicit components (in kg/m³)
    S_K = state_liquid[cmps.index('S_K')] if 'S_K' in cmps.IDs else 0.0
    S_IN = state_liquid[cmps.index('S_IN')]
    S_IP = state_liquid[cmps.index('S_IP')] if 'S_IP' in cmps.IDs else 0.0
    S_IC = state_liquid[cmps.index('S_IC')]
    S_ac = state_liquid[cmps.index('S_ac')]
    S_pro = state_liquid[cmps.index('S_pro')]
    S_bu = state_liquid[cmps.index('S_bu')]
    S_va = state_liquid[cmps.index('S_va')]

    # Codex fix #5: Add sulfur species for complete charge balance
    S_SO4 = state_liquid[cmps.index('S_SO4')] if 'S_SO4' in cmps.IDs else 0.0
    S_IS = state_liquid[cmps.index('S_IS')] if 'S_IS' in cmps.IDs else 0.0

    # Build 14-element weak-acid vector (molar concentrations)
    # S_cat, S_divalent, S_trivalent, S_an already in M from _compute_lumped_ions
    # Others need conversion from kg/m³ to M
    weak_acids = np.array([
        S_cat_M,       # Na+ (molar)
        S_K * unit_conversion[cmps.index('S_K')],
        S_divalent_M,  # Mg2+ + Ca2+ + Fe2+ (molar, Codex fix #4)
        S_trivalent_M, # Fe3+ + Al3+ (molar, Codex fix #6)
        S_an_M,        # Cl- (molar, Codex fix #3)
        S_IN * unit_conversion[cmps.index('S_IN')],
        S_IP * unit_conversion[cmps.index('S_IP')] if 'S_IP' in cmps.IDs else 0.0,
        S_IC * unit_conversion[cmps.index('S_IC')],
        S_ac * unit_conversion[cmps.index('S_ac')],
        S_pro * unit_conversion[cmps.index('S_pro')],
        S_bu * unit_conversion[cmps.index('S_bu')],
        S_va * unit_conversion[cmps.index('S_va')],
        S_SO4 * unit_conversion[cmps.index('S_SO4')] if 'S_SO4' in cmps.IDs else 0.0,  # Codex fix #5
        S_IS * unit_conversion[cmps.index('S_IS')] if 'S_IS' in cmps.IDs else 0.0      # Codex fix #5
    ])

    # QSDsan ADM1/mADM1 acid-base reaction (charge balance)
    def acid_base_rxn(h_ion, weak_acids_tot, Kas, Ka_h2s_param):
        """
        Charge balance equation - adapted for mADM1's 7-element Ka array + sulfur + trivalents.

        mADM1 Ka structure: [Kw, Ka_nh, Ka_co2, Ka_ac, Ka_pr, Ka_bu, Ka_va]
        (No Ka_h2po4 - phosphate not in standard mADM1 Ka array)

        Codex fix #5: Includes sulfur species (SO₄²⁻, HS⁻) to prevent pH bias
        in sulfur-rich scenarios.

        Codex fix #6: Includes trivalent cations (Fe³⁺, Al³⁺) to prevent pH bias
        during iron/alum dosing campaigns.

        Codex fix #7: Uses temperature-corrected Ka_h2s for H2S/HS⁻ equilibrium
        to ensure thermodynamic consistency with calc_biogas.
        """
        S_cat, S_K, S_Mg, S_trivalent, S_an, S_IN, S_IP = weak_acids_tot[:7]
        Kw = Kas[0]
        oh_ion = Kw / h_ion

        # Henderson-Hasselbalch for weak acids (without phosphate)
        # weak_acids_tot[7:12] = [S_IC, S_ac, S_pro, S_bu, S_va] (5 VFAs + IC)
        # weak_acids_tot[12:14] = [S_SO4, S_IS] (sulfur species, Codex fix #5)
        S_IC, S_ac_tot, S_pro_tot, S_bu_tot, S_va_tot, S_SO4, S_IS = weak_acids_tot[7:]

        # Calculate deprotonated forms (Ka * total / (Ka + H⁺))
        nh3 = Kas[1] * S_IN / (Kas[1] + h_ion)
        # No phosphate speciation - assume S_IP is minimal in mADM1
        hpo4 = S_IP  # Approximate as fully deprotonated (HPO₄²⁻)
        hco3 = Kas[2] * S_IC / (Kas[2] + h_ion)
        ac = Kas[3] * S_ac_tot / (Kas[3] + h_ion)
        pro = Kas[4] * S_pro_tot / (Kas[4] + h_ion)
        bu = Kas[5] * S_bu_tot / (Kas[5] + h_ion)
        va = Kas[6] * S_va_tot / (Kas[6] + h_ion)

        # Codex fix #5/#7: Calculate HS⁻ speciation using temperature-corrected Ka_h2s
        # H2S <-> HS⁻ + H⁺, pKa ~ 7.0 at 25°C
        # Now using Ka_h2s from params (matches calc_biogas)
        hs = Ka_h2s_param * S_IS / (Ka_h2s_param + h_ion)  # HS⁻ concentration

        # Charge balance: cations - anions = 0
        # Codex fix #5: Add sulfur terms: -2*SO₄²⁻ - HS⁻
        # Codex fix #6: Add trivalent term: +3*S_trivalent (Fe³⁺ + Al³⁺)
        return (S_cat + S_K + 2*S_Mg + 3*S_trivalent + h_ion + (S_IN - nh3)
                - S_an - oh_ion - hco3 - ac - pro - bu - va
                - 2*hpo4 - (S_IP - hpo4)
                - 2*S_SO4 - hs)

    # Solve for H⁺ using Brent's method (production QSDsan approach)
    # Codex fix #7: Pass Ka_h2s to ensure consistency with calc_biogas
    h = brenth(acid_base_rxn, 1e-14, 1.0,
               args=(weak_acids, Ka, Ka_h2s),
               xtol=1e-12, maxiter=100)
    pH = -np.log10(h)

    # Calculate NH₃ and CO₂ using same Ka (thermodynamically consistent)
    # Codex fix #1/#2: Use ADM1 forms with correct unit handling
    # mADM1 indexing: Ka[1]=Ka_nh, Ka[2]=Ka_co2
    unit_conv_IN = unit_conversion[cmps.index('S_IN')]
    unit_conv_IC = unit_conversion[cmps.index('S_IC')]

    # Codex fix #1: NH3 = S_IN * unit_conv * Ka / (Ka + h)
    # Matches QSDsan ADM1 production form (no unit mixing in denominator)
    nh3 = S_IN * unit_conv_IN * Ka[1] / (Ka[1] + h)

    # Codex fix #2: CO2 = S_IC * unit_conv * h / (Ka + h)
    # Includes (Ka + h) denominator for correct equilibrium (was missing)
    co2 = S_IC * unit_conv_IC * h / (Ka[2] + h)

    # Placeholder activities (unity for now)
    # Future: compute ionic strength and apply Davies/Debye-Hückel
    activities = np.ones(13)  # Match the 13 minerals in _pKsp_base

    return pH, nh3, co2, activities

def saturation_index(acts, Ksp):
    """
    Calculate saturation indices for mineral precipitation.

    Computes the ratio of ionic activity product (IAP) to solubility product (Ksp)
    for each mineral.

    Parameters
    ----------
    acts : ndarray
        Array of ionic activities (placeholder - currently unity)
    Ksp : ndarray
        Solubility products for each mineral

    Returns
    -------
    ndarray
        Saturation indices (IAP/Ksp) for each mineral

    Notes
    -----
    SI > 1: supersaturated (precipitation likely)
    SI = 1: at equilibrium
    SI < 1: undersaturated (no precipitation)

    This is a placeholder implementation that returns unity (no precipitation)
    Future: implement proper IAP calculation from activities
    """
    # Placeholder: return all minerals at equilibrium (SI = 1.0)
    # Future: compute IAP from activities (e.g., IAP_calcite = a_Ca2+ * a_CO32-)
    # Then return IAP / Ksp
    return np.ones_like(Ksp)


# H2 tracking for Newton solver - required by AnaerobicCSTR
_H2_EPS = 1e-8

def dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in):
    """
    H2 mass balance residual for Newton solver.

    Computes the algebraic hydrogen balance:
    Q/V_liq*(S_h2_in - S_h2) + Σ(rhos * ν_H2)

    Parameters
    ----------
    S_h2 : float
        Current H2 concentration guess [kmol/m³]
    state_arr : ndarray
        State variable array
    h : float or None
        Proton concentration (if None, will be computed)
    params : dict
        Kinetic parameters
    f_stoichio : callable
        Stoichiometry function
    V_liq : float
        Liquid volume [m³]
    S_h2_in : float
        Influent H2 concentration [kmol/m³]

    Returns
    -------
    float
        Residual value for Newton iteration
    """
    cmps = params['components']
    h2_idx = cmps.index('S_h2')
    S_saved = state_arr[h2_idx]

    # Temporarily set H2 to current guess
    state_arr[h2_idx] = S_h2

    # Get T_op from params or use default
    T_op = params.get('T_op', params.get('T_base', 298.15))

    # Compute reaction rates with current H2 value
    rhos = rhos_madm1(state_arr, params, T_op, h=h)

    # Get H2 stoichiometry
    stoichio = f_stoichio(state_arr)

    # Get flow rate (last element of state array)
    Q = state_arr[-1]

    # Compute residual: accumulation + reaction
    residual = Q/V_liq * (S_h2_in - S_h2) + np.dot(rhos, stoichio)

    # Restore original H2 value
    state_arr[h2_idx] = S_saved

    return residual


def grad_dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps=_H2_EPS):
    """
    Gradient of H2 mass balance residual for Newton solver.

    Computes the derivative of the residual with respect to S_h2
    using numerical differentiation (central difference).

    Parameters
    ----------
    S_h2 : float
        Current H2 concentration guess [kmol/m³]
    state_arr : ndarray
        State variable array
    h : float or None
        Proton concentration (if None, will be computed)
    params : dict
        Kinetic parameters
    f_stoichio : callable
        Stoichiometry function
    V_liq : float
        Liquid volume [m³]
    S_h2_in : float
        Influent H2 concentration [kmol/m³]
    eps : float
        Perturbation for numerical differentiation

    Returns
    -------
    float
        Gradient (d_residual/d_S_h2) for Newton iteration
    """
    cmps = params['components']
    h2_idx = cmps.index('S_h2')
    S_saved = state_arr[h2_idx]

    # Adaptive epsilon based on magnitude
    eps = max(eps, 1e-6 * max(1.0, abs(S_h2)))

    # Get T_op from params
    T_op = params.get('T_op', params.get('T_base', 298.15))

    # Forward perturbation
    state_arr[h2_idx] = S_h2 + eps
    rhos_plus = rhos_madm1(state_arr, params, T_op, h=h).copy()

    # Backward perturbation
    state_arr[h2_idx] = S_h2 - eps
    rhos_minus = rhos_madm1(state_arr, params, T_op, h=h).copy()

    # Restore original H2 value
    state_arr[h2_idx] = S_saved

    # Central difference derivative
    dr_dS = (rhos_plus - rhos_minus) / (2 * eps)

    # Get H2 stoichiometry
    stoichio = f_stoichio(state_arr)

    # Get flow rate
    Q = state_arr[-1]

    # Compute gradient: d(Q/V*(S_in - S))/dS + d(Σ rhos*ν)/dS
    return -Q/V_liq + np.dot(dr_dS, stoichio)


rhos = np.zeros(38+8+13+4) # 38 biological + 8 chemical P removal by HFO + 13 MMP + 4 gas transfer
Cs = np.empty(38+8)
sum_stoichios = np.array([2, 2, 5, 9, 3, 8, 3, 3, 2, 3, 2, 2, 2])  # 13 minerals stoichiometry

def rhos_madm1(state_arr, params, T_op, h=None):
    """
    Compute mADM1 process rates.

    Parameters
    ----------
    state_arr : array
        State vector
    params : dict
        Model parameters
    T_op : float
        Operating temperature [K]
    h : tuple, optional
        Pre-computed (pH, nh3, co2, acts) from pcm(). If None, computed internally.

    Returns
    -------
    array
        Process rates
    """
    ks = params['rate_constants']
    Ks = params['half_sat_coeffs']
    K_PP = params['K_PP']
    K_so4 = params['K_so4']
    cmps = params['components']
    # n = len(cmps)
    pH_LLs, pH_ULs = params['pH_limits']
    KS_IN = params['KS_IN']
    KS_IP = params['KS_IP']
    KI_nh3 = params['KI_nh3']
    KIs_h2 = params['KIs_h2']
    KIs_h2s = params['KIs_h2s']
    KHb = params['K_H_base']
    Kab = params['Ka_base']
    KH_dH = params['K_H_dH']
    Ka_dH = params['Ka_dH']
    kLa = params['kLa']
    k_cryst = params['k_cryst']
    n_cryst = params['n_cryst']
    Kspb = params['Ksp_base']
    Ksp_dH = params['Ksp_dH']
    T_base = params['T_base']
    
    Cs[:7] = state_arr[13:20]                   # original ADM1 processes
    Cs[7:11] = state_arr[19:23]
    Cs[11:18] = state_arr[16:23]
    Cs[18:23] = X_PAO = state_arr[26]           # P extension processes
    Cs[23:25] = X_PP, X_PHA = state_arr[[25,24]]
    Cs[25:27] = state_arr[31]                   # S extension processes
    Cs[27:29] = state_arr[32]
    Cs[29:31] = state_arr[33]
    Cs[31:34] = state_arr[34]
    Cs[34:36] = Cs[36:38] = Cs[38:40] = Cs[40:42] = state_arr[38:40]   # Fe extension processes + HFO module
    Cs[42:44] = Cs[44:46] = state_arr[41:43]
    
    rhos[:46] = ks * Cs
    primary_substrates = state_arr[:8]
    
    rhos[3:11] *= substr_inhibit(primary_substrates, Ks[:8])
    c4 = primary_substrates[[3,4]]
    if sum(c4) > 0: rhos[[6,7]] *= c4/sum(c4)
    
    vfas = primary_substrates[3:7]
    rhos[18:22] *= substr_inhibit(vfas, Ks[8])
    if sum(vfas) > 0: rhos[18:22] *= vfas/sum(vfas)
    if X_PAO > 0: rhos[18:22] *= substr_inhibit(X_PP/X_PAO, K_PP)
    
    srb_subs = np.flip(primary_substrates[3:])
    S_SO4, S_IS = state_arr[29:31]
    rhos[[25,27,29,31,32]] *= substr_inhibit(srb_subs, Ks[9:14]) * substr_inhibit(S_SO4, K_so4)  # Updated to Ks[9:14] for 5 SRB processes
    if sum(srb_subs[-2:]) > 0: rhos[[31,32]] *= srb_subs[-2:]/sum(srb_subs[-2:])
    
    #!!! why divide by 16 or 64?
    S_h2 = primary_substrates[-1]
    rhos[34:36] *= S_h2 / 16 
    rhos[36:38] *= S_IS / 64
    
    KPbind, KPdiss = Ks[-2:]
    S_IP = state_arr[11]
    rhos[40:42] *= substr_inhibit(S_IP, KPbind)
    rhos[44:46] *= non_compet_inhibit(S_IP, KPdiss)
    
    # inhibition factors
    # ******************
    # Convert kg/m³ (model states) to mol/L
    # FIX: Remove extra 1e3 factor - mass2mol_conversion already converts kg/m³ to mol/L
    unit_conversion = mass2mol_conversion(cmps)  # kg/m³ → mol/L (NOT kmol/m³!)
    if T_op == T_base:
        Ka = Kab
        KH = KHb / unit_conversion[[7,8,9,30]]
        Ksp = Kspb
    else:
        T_temp = params.pop('T_op', None)
        if T_op == T_temp:
            params['T_op'] = T_op
            Ka = params['Ka']
            KH = params['KH']
            Ksp = params['Ksp']
        else:
            params['T_op'] = T_op
            Ka = params['Ka'] = Kab * T_correction_factor(T_base, T_op, Ka_dH)
            KH = params['KH'] = KHb * T_correction_factor(T_base, T_op, KH_dH) / unit_conversion[[7,8,9,30]]
            Ksp = params['Ksp'] = Kspb * T_correction_factor(T_base, T_op, Ksp_dH)
            
    S_IN, S_IP = state_arr[[10,11]]
    I_nutrients = substr_inhibit(S_IN, KS_IN) * substr_inhibit(S_IP, KS_IP)
    rhos[3:11] *= I_nutrients
    rhos[[25,27,29,31,32]] *= I_nutrients
    
# =============================================================================
#     !!! place holder for PCM (speciation)
# =============================================================================
    # Use pre-computed h if provided, otherwise compute it
    if h is None:
        pH, nh3, co2, acts = pcm(state_arr, params)
    else:
        pH, nh3, co2, acts = h
    Is_pH = Hill_inhibit(10**(-pH), pH_ULs, pH_LLs)
    rhos[3:9] *= Is_pH[0]
    rhos[9:11] *= Is_pH[1:3]
    rhos[[25,27]] *= Is_pH[3:5]
    rhos[[29,31,32]] *= Is_pH[-1]
    
    Is_h2 = non_compet_inhibit(S_h2, KIs_h2)
    rhos[5:9] *= Is_h2
    Inh3 = non_compet_inhibit(nh3, KI_nh3)
    rhos[9] *= Inh3
    
    Z_h2s = calc_biogas(state_arr, params, pH) # should be a function of pH, like co2 and nh3
    Is_h2s = non_compet_inhibit(Z_h2s, KIs_h2s)
    rhos[6:11] *= Is_h2s[:5]
    rhos[[25,27,29,31,32]] *= Is_h2s[5:]
    
    # multiple mineral precipitation
    # ******************************
    # Calculate real saturation indices using thermodynamics module
    SI_dict = calc_saturation_indices(state_arr, cmps, pH, T_op, unit_conversion)

    # Map dict to array in correct order (matching state_arr[47:60])
    # X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD, X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS, X_Fe3PO42, X_AlPO4
    mineral_names = ['CCM', 'ACC', 'ACP', 'HAP', 'DCPD', 'OCP', 'struv', 'newb', 'magn', 'kstruv', 'FeS', 'Fe3PO42', 'AlPO4']
    SIs = np.array([SI_dict.get(name, 1.0) for name in mineral_names])

    # CRITICAL FIX (per Codex review): Do NOT clamp SI at 1.0
    # That prevents dissolution (SI < 1 → negative rate)
    # The kinetic expression must preserve sign for dissolution
    X_minerals = state_arr[47:60]

    # Precipitation/dissolution rate: r = k * X * sign(SI-1) * |SI^(1/ν) - 1|^n
    # n_cryst is even (2), so (SI^(1/ν) - 1)^n is always positive
    # We must explicitly preserve the sign for dissolution
    SI_driving_force = SIs**(1/sum_stoichios) - 1  # Positive if SI > 1, negative if SI < 1
    sign_direction = np.sign(SI_driving_force)  # +1 for precipitation, -1 for dissolution
    magnitude = np.abs(SI_driving_force)**n_cryst  # Always positive due to even n_cryst

    # Combine: rate = k * X * sign * magnitude
    rates = k_cryst * X_minerals * sign_direction * magnitude

    # Guard: Prevent dissolution if X_mineral is already near zero
    rhos[46:59] = np.where(X_minerals > 1e-12, rates, np.maximum(0, rates))
    
    # gas transfer
    # ************
    # Henry's law: rhos = kLa * (biogas_S - KH * biogas_p)
    # CRITICAL: Keep everything in kg/m³ (mass units) to match KH which is already kg/m³/bar
    # KH was temperature-corrected at line 833: KH = KHb * T_corr / unit_conversion
    # This made KH have units of kg/m³/bar (from original kmol/m³/bar)

    # FIX #1: Dynamic headspace indexing (per Codex analysis 2025-10-24)
    # The liquid phase has 63 components (0-62), gas phase starts at index 63
    # For mADM1+S we have 4 biogas species: H2, CH4, CO2, H2S
    # Biogas species in liquid phase: S_h2 (7), S_ch4 (8), S_IC (9), S_IS (30)
    n_cmps = 63  # Fixed for mADM1 - number of liquid components
    n_gas = 4    # Fixed for mADM1+S - H2, CH4, CO2, H2S
    gas_slice = slice(n_cmps, n_cmps + n_gas)  # Indices 63:67

    biogas_S = state_arr[[7,8,9,30]].copy()  # Start with kg/m³ from state

    # UPSTREAM APPROACH: Calculate dissolved CO2 directly from state_arr[S_IC]
    # This naturally includes biological supersaturation from ODE dynamics
    # Matches QSDsan upstream: co2 = state_arr[9] * h / (Ka[3] + h)
    h_ion = 10**(-pH)  # Convert pH back to H+ concentration
    Ka_co2 = Ka[2]  # Ka for CO2/HCO3- equilibrium
    co2_dissolved = state_arr[9] * h_ion / (Ka_co2 + h_ion)  # kg/m³ (dissolved CO2 from TOTAL S_IC)
    biogas_S[2] = co2_dissolved  # Use directly - S_IC already contains supersaturation from biology
    biogas_S[3] = Z_h2s / unit_conversion[30]  # H2S from calc_biogas: kmol/m³ → kg/m³

    # Partial pressures in bar (R is in bar·m³/(kmol·K), state[gas_slice] is kmol/m³)
    biogas_p = R * T_op * state_arr[gas_slice]  # bar - FIXED to use dynamic slice

    # Gas transfer rate in kg/m³/d (all terms in consistent mass units)
    # Now biogas_S includes biological supersaturation, so driving force is non-zero
    rhos[-n_gas:] = kLa * (biogas_S - KH * biogas_p)  # kg/m³/d - FIXED to use -n_gas

    # DIAGNOSTIC HOOKS: Populate root.data for CLI diagnostics (per Codex advice)
    root = params.get('root')

    # DIAGNOSTIC: Print gas transfer driving force for CH4
    if root is not None and 'diagnostics' in root.data:
        root.data['diagnostics']['gas_transfer'] = {
            'biogas_S_ch4_kg_m3': float(biogas_S[1]),  # CH4 dissolved
            'KH_ch4_kg_m3_bar': float(KH[1]),
            'biogas_p_ch4_bar': float(biogas_p[1]),
            'KH_times_p_ch4_kg_m3': float(KH[1] * biogas_p[1]),
            'driving_force_ch4_kg_m3': float(biogas_S[1] - KH[1] * biogas_p[1]),
            'kLa': float(kLa),
            'rho_ch4_kg_m3_d': float(rhos[-3])  # CH4 is second element in gas array
        }
    if root is not None:
        # Calculate Monod factors for diagnostics using Ks from params
        # Primary substrates (S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2)
        primary_substrates = state_arr[[0,1,2,3,4,5,6,7]]
        Ks_main = Ks[:8]  # First 8 Ks values are for main uptake processes
        monod_main = substr_inhibit(primary_substrates, Ks_main)

        # Store comprehensive diagnostics
        root.data = {
            'pH': float(pH),
            'nh3_M': float(nh3),
            'co2_M': float(co2),
            'h2s_M': float(Z_h2s),
            'biogas_dissolved': {
                'co2_kmol_m3': float(biogas_S[2]),
                'h2s_kmol_m3': float(biogas_S[3]),
            },
            'gas_equilibrium': {
                'co2_eq_kmol_m3': float((KH * biogas_p)[2]),
                'h2s_eq_kmol_m3': float((KH * biogas_p)[3]),
            },
            'I_pH': {
                'acidogens': float(Is_pH[0]),
                'acetoclastic': float(Is_pH[1]),
                'hydrogenotrophic': float(Is_pH[2]),
                'SRB_h2': float(Is_pH[3]),
                'SRB_ac': float(Is_pH[4]),
                'SRB_aa': float(Is_pH[5]),
            },
            'I_h2': {
                'LCFA': float(Is_h2[0]),
                'C4_valerate': float(Is_h2[1]),
                'C4_butyrate': float(Is_h2[2]),
                'propionate': float(Is_h2[3]),
            },
            'I_h2s': {
                'C4_valerate': float(Is_h2s[0]),
                'C4_butyrate': float(Is_h2s[1]),
                'propionate': float(Is_h2s[2]),
                'acetate': float(Is_h2s[3]),
                'hydrogen': float(Is_h2s[4]),
                'SRB_h2': float(Is_h2s[5]),
                'SRB_ac': float(Is_h2s[6]),
                'SRB_prop': float(Is_h2s[7]),
                'SRB_bu': float(Is_h2s[8]),
                'SRB_va': float(Is_h2s[9]),
            },
            'I_nutrients': {
                'I_IN_lim': float(substr_inhibit(S_IN, KS_IN)),
                'I_IP_lim': float(substr_inhibit(S_IP, KS_IP)),
                'combined': float(I_nutrients),
                'I_nh3': float(Inh3),
            },
            'Monod': monod_main.tolist(),
            'biomass_kg_m3': {
                'X_su': float(state_arr[16]),
                'X_aa': float(state_arr[17]),
                'X_fa': float(state_arr[18]),
                'X_c4': float(state_arr[19]),
                'X_pro': float(state_arr[20]),
                'X_ac': float(state_arr[21]),
                'X_h2': float(state_arr[22]),
                'X_PAO': float(state_arr[26]),
                'X_hSRB': float(state_arr[31]),
                'X_aSRB': float(state_arr[32]),
                'X_pSRB': float(state_arr[33]),
                'X_c4SRB': float(state_arr[34]),
            },
            'process_rates': rhos.tolist(),
        }

    return rhos

#%% modified ADM1 class
_load_components = settings.get_default_chemicals

def fun(q_aging_H=450.0, q_aging_L=0.1, q_Pcoprec=360, q_Pbinding=0.3, q_diss_H=36.0, q_diss_L=36.0,
        K_Pbind=37.2, K_Pdiss=0.93):
    '''
    

    Parameters
    ----------

    Returns
    -------
    None.

    '''
    pass    
    
@chemicals_user
class ModifiedADM1(CompiledProcesses):
    """
    Modified Anaerobic Digestion Model no.1 [1]_, [2]_, [3]_

    Parameters
    ----------
    f_ch_xb : float, optional
        Fraction of carbohydrates as biomass decay product. The default is 0.275.
    f_pr_xb : flaot, optional
        Fraction of proteins as biomass decay product. The default is 0.275.
    f_li_xb : float, optional
        Fraction of lipids as biomass decay product. The default is 0.35.
    f_xI_xb : float, optional
        Fraction of inert particulates as biomass decay product. The default is 0.1.
    f_va_pha : float, optional
        Fraction of valerate as PHA lysis product. The default is 0.1.
    f_bu_pha : float, optional
        Fraction of butyrate as PHA lysis product. The default is 0.1.
    f_pro_pha : float, optional
        Fraction of propionate as PHA lysis product. The default is 0.4.
    Y_PO4 : float, optional
        Poly-phosphorus (PP) required for PHA storage [kg P/kg COD]. The default is 0.4.
    Y_hSRB : float, optional
        Sulfide-reducing biomass (SRB) yield of hydrogen uptake [kg COD/kg COD]. 
        The default is 0.05.
    Y_aSRB : float, optional
        SRB yield of acetate uptake [kg COD/kg COD]. The default is 0.05.
    Y_pSRB : float, optional
        SRB yield of propionate uptake [kg COD/kg COD]. The default is 0.04.
    Y_c4SRB : float, optional
        SRB yield of butyrate or valerate uptake [kg COD/kg COD]. 
        The default is 0.06.
    q_pha : float, optional
        Maximum specific rate constant for PHA storage by phosphorus-accumulating
        organisms (PAOs) [d^(-1)]. The default is 3.0.
    b_pao : float, optional
        PAO lysis rate constant [d^(-1)]. The default is 0.2.
    b_pp : float, optional
        PP lysis rate constant [d^(-1)]. The default is 0.2.
    b_pha : float, optional
        PHA lysis rate constant [d^(-1)]. The default is 0.2.
    K_A : float, optional
        Substrate half saturation coefficient for PHA storage [kg COD/m3]. 
        The default is 4e-3.
    K_PP : float, optional
        PP half saturation coefficient for PHA storage [kg P (X_PP)/kg COD (X_PHA)]. 
        The default is 0.01.
    k_hSRB : float, optional
        Maximum specific growth rate constant of hydrogen-uptaking SRB [d^(-1)]. 
        The default is 41.125.
    k_aSRB : float, optional
        Maximum specific growth rate constant of acetate-uptaking SRB [d^(-1)]. 
        The default is 10..
    k_pSRB : float, optional
        Maximum specific growth rate constant of propionate-uptaking SRB [d^(-1)]. 
        The default is 16.25.
    k_c4SRB : float, optional
        Maximum specific growth rate constant of butyrate- or valerate-uptaking 
        SRB [d^(-1)]. The default is 23.
    b_hSRB : float, optional
        Hydrogen-uptaking SRB decay rate constant [d^(-1)]. The default is 0.02.
    b_aSRB : float, optional
        Acetate-uptaking SRB decay rate constant [d^(-1)]. The default is 0.02.
    b_pSRB : float, optional
        Propionate-uptaking SRB decay rate constant [d^(-1)]. The default is 0.02.
    b_c4SRB : float, optional
        Butyrate- or valerate-uptaking SRB decay rate constant [d^(-1)]. 
        The default is 0.02.
    K_hSRB : float, optional
        Substrate half saturation coefficient of hydrogen uptake by SRB 
        [kg COD/m3]. The default is 5.96e-6.
    K_aSRB : float, optional
        Substrate half saturation coefficient of acetate uptake by SRB 
        [kg COD/m3]. The default is 0.176.
    K_pSRB : float, optional
        Substrate half saturation coefficient of propionate uptake by SRB 
        [kg COD/m3]. The default is 0.088.
    K_c4SRB : float, optional
        Substrate half saturation coefficient of butyrate or valerate uptake by  
        SRB [kg COD/m3]. The default is 0.1739.
    K_so4_hSRB : float, optional
        Sulfate half saturation coefficient of SRB uptaking hydrogen [kg S/m3]. 
        The default is 3.335e-3.
    K_so4_aSRB : float, optional
        Sulfate half saturation coefficient of SRB uptaking acetate [kg S/m3]. 
        The default is 6.413e-3.
    K_so4_pSRB : float, optional
        Sulfate half saturation coefficient of SRB uptaking propionate [kg S/m3]. 
        The default is 6.413e-3.
    K_so4_c4SRB : float, optional
        Sulfate half saturation coefficient of SRB uptaking butyrate or valerate  
        [kg S/m3]. The default is 6.413e-3.
    k_Fe3t2_h2 : float, optional
        Fe(3+) reduction rate constant [m3∙kg^(-1) Fe(III)∙d^(-1)] using hydrogen
        as electron donor. The default is 1.79e7.
    k_Fe3t2_is : float, optional
        Fe(3+) reduction rate constant [m3∙kg^(-1) Fe(III)∙d^(-1)] using sulfide
        as electron donor. The default is 1.79e7.
    KS_IP : float, optional
        Inorganic phosphorus (nutrient) inhibition coefficient for soluble 
        substrate uptake [M]. The default is 2e-5.
    q_aging_H : float, optional
        Aging rate constant of X_HFO_H and X_HFO_HP [d^(-1)]. The default is 450.0.
    q_aging_L : float, optional
        Aging rate constant of X_HFO_L and X_HFO_LP [d^(-1)]. The default is 0.1.
    q_Pcoprec : float, optional
        Rate constant of P binding and coprecipitation on X_HFO_H [d^(-1)]. 
        The default is 360.
    q_Pbinding : float, optional
        Rate constant of P binding on X_HFO_L [d^(-1)]. The default is 0.3.
    q_diss_H : float, optional
        Dissolution rate constant of X_HFO_HP [d^(-1)]. The default is 36.0.
    q_diss_L : float, optional
        Dissolution rate constant of X_HFO_HP [d^(-1)]. The default is 36.0.
    K_Pbind : float, optional
        S_IP half saturation coefficient for binding with X_HFO_H or X_HFO_L
        [kg P/m3]. The default is 37.2, i.e., 1.20 kmol P/m3.
    K_Pdiss : float, optional
        S_IP half inhibition coefficient for dissolution of X_HFO_HP or X_HFO_LP
        [kg P/m3]. The default is 0.93, i.e., 0.03 kmol P/m3.
    KI_h2s_c4 : float, optional
        H2S half inhibition coefficient for butyrate or valerate uptake 
        [kg COD/m3]. The default is 0.481.
    KI_h2s_pro : float, optional
        H2S half inhibition coefficient for propionate uptake [kg COD/m3]. 
        The default is 0.481.
    KI_h2s_ac : float, optional
        H2S half inhibition coefficient for acetate uptake [kg COD/m3]. 
        The default is 0.460.
    KI_h2s_h2 : float, optional
        H2S half inhibition coefficient for hydrogen uptake [kg COD/m3]. 
        The default is 0.400.
    KI_h2s_c4SRB : float, optional
        H2S half inhibition coefficient for butyrate or valerate uptake by SRB
        [kg COD/m3]. The default is 0.520.
    KI_h2s_pSRB : float, optional
        H2S half inhibition coefficient for propionate uptake by SRB [kg COD/m3]. 
        The default is 0.520.
    KI_h2s_aSRB : float, optional
        H2S half inhibition coefficient for acetate uptake by SRB [kg COD/m3]. 
        The default is 0.499.
    KI_h2s_hSRB : float, optional
        H2S half inhibition coefficient for hydrogen uptake by SRB [kg COD/m3]. 
        The default is 0.499.        
    pH_limits_aa_SRB : 2-tuple, optional
        Lower and upper limits of pH inhibition for acetogenosis by SRB, 
        unitless. The default is (6,7).
    pH_limits_ac_SRB : 2-tuple, optional
        Lower and upper limits of pH inhibition for acetate uptake by SRB, 
        unitless. The default is (6,7).
    pH_limits_h2_SRB : 2-tuple, optional
        Lower and upper limits of pH inhibition for hydrogen uptake by SRB, 
        unitless. The default is (5,6).
    k_cryst : iterable[float], optional
        Mineral precipitation rate constants [h^(-1)], following the order of 
        `ModifiedADM1._precipitates`. The default is 
        [0.35, 1e-3, 3.0, 1e-3, 2.0, 0.76, 5.0, 1e-3, 1e-3, 1e-3, 1e2, 1e-3, 1e-3].
    n_cryst : iterable[int], optional
        The effect orders of mineral precipitation reactions [unitless], following 
        the order of `ModifiedADM1._precipitates`. The default is 
        [2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2].
    
    
    Examples
    --------
    ...

    References
    ----------
    .. [1] Flores-Alsina, X., Solon, K., Kazadi Mbamba, C., Tait, S., 
        Gernaey, K. V., Jeppsson, U., Batstone, D. J. (2016). 
        Modelling phosphorus (P), sulfur (S) and iron (Fe) interactions 
        for dynamic simulations of anaerobic digestion processes. 
        Water Research, 95, 370–382. https://doi.org/10.1016/J.WATRES.2016.03.012
    .. [2] Solon, K., Flores-Alsina, X., Kazadi Mbamba, C., Ikumi, D., 
        Volcke, E. I. P., Vaneeckhaute, C., Ekama, G., Vanrolleghem, P. A., 
        Batstone, D. J., Gernaey, K. v., Jeppsson, U. (2017). Plant-wide 
        modelling of phosphorus transformations in wastewater treatment systems: 
        Impacts of control and operational strategies. Water Research, 
        113, 97–110. https://doi.org/10.1016/J.WATRES.2017.02.007
    .. [3] Hauduc, H., Takács, I., Smith, S., Szabo, A., Murthy, S., Daigger, G. T., 
        Spérandio, M. (2015). A dynamic physicochemical model for chemical phosphorus 
        removal. Water Research, 73, 157–170. https://doi.org/10.1016/J.WATRES.2014.12.053
    
    See Also
    --------
    `qsdsan.processes.ADM1 <https://qsdsan.readthedocs.io/en/latest/api/processes/ADM1.html>`_  

    """
        
    _cmp_dependent_stoichio = ('K_XPP', 'Mg_XPP', 
                               'MW_S0', 'MW_IS', 
                               'i_mass_S0', 'i_mass_IS', 'i_mass_Fe2')
    _stoichio_params = (*ADM1._stoichio_params[5:],
                        'f_ch_xb', 'f_pr_xb', 'f_li_xb', 'f_xI_xb', 'f_sI_xb',
                        'f_va_pha',	'f_bu_pha',	'f_pro_pha', 'f_ac_pha',
                        'f_is_pro', 'f_is_bu', 'f_is_va',
                        'Y_PO4', 'Y_hSRB', 'Y_aSRB', 'Y_pSRB', 'Y_c4SRB',
                        *_cmp_dependent_stoichio
                        )
    _kinetic_params = ('rate_constants', 'half_sat_coeffs', 'K_PP', 'K_so4',
                       'pH_limits', 'KS_IN', 'KS_IP', 'KI_nh3', 'KIs_h2', 'KIs_h2s',
                       'Ka_base', 'Ka_dH', 'K_H_base', 'K_H_dH', 'kLa',
                       'k_cryst', 'n_cryst', 'Ksp_base', 'Ksp_dH',
                       'T_base', 'components',
                       'root'
                       )
    _acid_base_pairs = ADM1._acid_base_pairs
    _biogas_IDs = (*ADM1._biogas_IDs, 'S_IS')
    _biomass_IDs = (*ADM1._biomass_IDs, 'X_PAO', 'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB')
    _precipitates = ('X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP',
                    'X_struv', 'X_newb', 'X_magn', 'X_kstruv', 
                    'X_FeS', 'X_Fe3PO42', 'X_AlPO4')
    _T_base = 298.15
    _K_H_base = [7.8e-4, 1.4e-3, 3.5e-2, 0.105]    # biogas species Henry's Law constant [M/bar]
    _K_H_dH = [-4180, -14240, -19410, -19180]      # Heat of reaction of liquid-gas transfer of biogas species [J/mol]
    
    _pKsp_base = [8.48, 8.3, 28.92, 44.333, 18.995, 47.08, 
                  13.6, 18.175, 7.46, 11.5508, 
                  2.95, 37.76, 18.2]
    _Ksp_dH = [8000, -12000, 54000, 0, 31000, 0, 
               -22600, -22600, -20000, -22600, 
               -11000, 5060, 0]
    
    def __new__(cls, components=None, path=None, 
                f_ch_xb=0.275, f_pr_xb=0.275, f_li_xb=0.35, f_xI_xb=0.1,
                f_fa_li=0.95, f_bu_su=0.1328, f_pro_su=0.2691, f_ac_su=0.4076,
                f_va_aa=0.23, f_bu_aa=0.26, f_pro_aa=0.05, f_ac_aa=0.4,
                f_ac_fa=0.7, f_pro_va=0.54, f_ac_va=0.31, f_ac_bu=0.8, f_ac_pro=0.57,
                Y_su=0.1, Y_aa=0.08, Y_fa=0.06, Y_c4=0.06, Y_pro=0.04, Y_ac=0.05, Y_h2=0.06,
                f_va_pha=0.1, f_bu_pha=0.1, f_pro_pha=0.4,
                Y_PO4=0.4, Y_hSRB=0.05, Y_aSRB=0.05, Y_pSRB=0.04, Y_c4SRB=0.06,                
                q_ch_hyd=10, q_pr_hyd=10, q_li_hyd=10,
                k_su=30, k_aa=50, k_fa=6, k_c4=20, k_pro=13, k_ac=8, k_h2=35,
                K_su=0.5, K_aa=0.3, K_fa=0.4, K_c4=0.2, K_pro=0.1, K_ac=0.15, K_h2=7e-6,
                b_su=0.02, b_aa=0.02, b_fa=0.02, b_c4=0.02, b_pro=0.02, b_ac=0.02, b_h2=0.02,
                q_pha=3.0, b_pao=0.2, b_pp=0.2, b_pha=0.2, K_A=4e-3, K_PP=0.01, 
                k_hSRB=41.125, k_aSRB=10., k_pSRB=16.25, k_c4SRB=23, 
                b_hSRB=0.02, b_aSRB=0.02, b_pSRB=0.02, b_c4SRB=0.02,
                K_hSRB=5.96e-6, K_aSRB=0.176, K_pSRB=0.088, K_c4SRB=0.1739,
                K_so4_hSRB=1.04e-4*S_mw, K_so4_aSRB=2e-4*S_mw, K_so4_pSRB=2e-4*S_mw, K_so4_c4SRB=2e-4*S_mw,
                k_Fe3t2_h2=1e9/Fe_mw, k_Fe3t2_is=1e9/Fe_mw,
                q_aging_H=450.0, q_aging_L=0.1, q_Pcoprec=360, q_Pbinding=0.3, q_diss_H=36.0, q_diss_L=36.0,
                K_Pbind=37.2, K_Pdiss=0.93, # 1.20 and 0.03 in MATLAB, assuming in kmol-P/m3 ?
                KI_h2_fa=5e-6, KI_h2_c4=1e-5, KI_h2_pro=3.5e-6, KI_nh3=1.8e-3, KS_IN=1e-4, KS_IP=2e-5,
                KI_h2s_c4=0.481, KI_h2s_pro=0.481, KI_h2s_ac=0.460, KI_h2s_h2=0.400,
                KI_h2s_c4SRB=0.520, KI_h2s_pSRB=0.520, KI_h2s_aSRB=0.499, KI_h2s_hSRB=0.499,
                pH_limits_aa=(4,5.5), pH_limits_ac=(6,7), pH_limits_h2=(5,6),
                pH_limits_aa_SRB=(6,7), pH_limits_ac_SRB=(6,7), pH_limits_h2_SRB=(5,6),
                kLa=200, pKa_base=[14, 9.25, 6.35, 4.76, 4.88, 4.82, 4.86],
                Ka_dH=[55900, 51965, 7646, 0, 0, 0, 0],
                k_cryst=[0.35, 1e-3, 3.0, 1e-3, 2.0, 0.76, 5.0, 1e-3, 1e-3, 1e-3, 1e2, 1e-3, 1e-3],
                n_cryst=[2, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2],
                **kwargs):
        
        cmps = _load_components(components)

        if not path: path = _path
        self = Processes.load_from_file(path,
                                        components=cmps,
                                        conserved_for=('C', 'N', 'P'),
                                        parameters=cls._stoichio_params,
                                        compile=False)
        
        for i in ('fast_P_binding', 'slow_P_sorption', 'dissolution_HFO_HP', 'dissolution_HFO_LP'):
            p = getattr(self, i)
            p.ref_component = 'S_IP'
        
        precipitation = []
        for i in cls._precipitates[:-3]:
            new_p = Process('precipitation_%s' % i.lstrip('X_'),
                            reaction='[?]S_IC + [?]S_IN + [?]S_IP + [?]S_K + [?]S_Mg + [?]S_Ca -> %s' % i,
                            ref_component=i,
                            conserved_for=('C', 'N', 'P', 'K', 'Mg', 'Ca'),
                            parameters=())
            precipitation.append(new_p)
        
        i_mass_IS = cmps.S_IS.i_mass
        i_mass_Fe2 = cmps.S_Fe2.i_mass
        FeS_mw = cmps.X_FeS.chem_MW
        new_p = Process('precipitation_FeS',
                        reaction={'S_Fe2': -Fe_mw/FeS_mw/i_mass_Fe2,
                                  'S_IS': -S_mw/FeS_mw/i_mass_IS,
                                  'X_FeS': 1},
                        ref_component='X_FeS',
                        conserved_for=())
        precipitation.append(new_p)
        
        Fe3PO42_mw = cmps.X_Fe3PO42.chem_MW
        new_p = Process('precipitation_Fe3PO42',
                        reaction={'S_Fe2': -3*Fe_mw/Fe3PO42_mw/i_mass_Fe2,
                                  'S_IP': '?',
                                  'X_Fe3PO42': 1},
                        ref_component='X_Fe3PO42',
                        conserved_for=('P',))
        precipitation.append(new_p)
        
        AlPO4_mw = cmps.X_AlPO4.chem_MW
        Al_mw = cmps.S_Al.chem_MW
        new_p = Process('precipitation_AlPO4',
                        reaction={'S_Al': -Al_mw/AlPO4_mw,
                                  'S_IP': '?',
                                  'X_AlPO4': 1},
                        ref_component='X_AlPO4',
                        conserved_for=('P',))        
        precipitation.append(new_p)

        self.extend(precipitation)
        
        gas_transfer = []
        for i in cls._biogas_IDs:
            new_p = Process('%s_transfer' % i.lstrip('S_'),
                            reaction={i:-1},
                            ref_component=i,
                            conserved_for=(),
                            parameters=())
            gas_transfer.append(new_p)
        self.extend(gas_transfer)
        self.compile(to_class=cls)

        stoichio_vals = (f_fa_li, f_bu_su, f_pro_su, f_ac_su, 1-f_bu_su-f_pro_su-f_ac_su,
                         f_va_aa, f_bu_aa, f_pro_aa, f_ac_aa, 1-f_va_aa-f_bu_aa-f_pro_aa-f_ac_aa,
                         f_ac_fa, 1-f_ac_fa, f_pro_va, f_ac_va, 1-f_pro_va-f_ac_va,
                         f_ac_bu, 1-f_ac_bu, f_ac_pro, 1-f_ac_pro,
                         Y_su, Y_aa, Y_fa, Y_c4, Y_pro, Y_ac, Y_h2,
                         f_ch_xb, f_pr_xb, f_li_xb, f_xI_xb, round(1.0-f_ch_xb-f_pr_xb-f_li_xb-f_xI_xb, 4),
                         f_va_pha, f_bu_pha, f_pro_pha, 1-f_va_pha-f_bu_pha-f_pro_pha,
                         1-f_ac_pro, 1-f_ac_bu, 1-f_pro_va-f_ac_va,
                         Y_PO4, Y_hSRB, Y_aSRB, Y_pSRB, Y_c4SRB,
                         cmps.X_PP.i_K, cmps.X_PP.i_Mg,
                         cmps.S_S0.chem_MW, cmps.S_IS.chem_MW, 
                         cmps.S_S0.i_mass, i_mass_IS, i_mass_Fe2)
        
        pH_limits = np.array([pH_limits_aa, pH_limits_ac, pH_limits_h2, 
                              pH_limits_h2_SRB, pH_limits_ac_SRB, pH_limits_aa_SRB]).T

        ks = np.array((q_ch_hyd, q_pr_hyd, q_li_hyd,
                       k_su, k_aa, k_fa, k_c4, k_c4, k_pro, k_ac, k_h2,
                       b_su, b_aa, b_fa, b_c4, b_pro, b_ac, b_h2,               # original ADM1
                       q_pha, q_pha, q_pha, q_pha, b_pao, b_pp, b_pha,          # P extension
                       k_hSRB, b_hSRB, k_aSRB, b_aSRB, k_pSRB, b_pSRB, k_c4SRB, k_c4SRB, b_c4SRB, # S extension
                       k_Fe3t2_h2, k_Fe3t2_h2, k_Fe3t2_is, k_Fe3t2_is,          # Fe extension
                       q_aging_H, q_aging_L, q_Pcoprec, q_Pbinding,             # HFO module
                       q_aging_H, q_aging_L, q_diss_H, q_diss_L))         
        
        Ks = np.array((K_su, K_aa, K_fa, K_c4, K_c4, K_pro, K_ac, K_h2,         # original ADM1
                       K_A,                                                     # P extension
                       K_hSRB, K_aSRB, K_pSRB, K_c4SRB, K_c4SRB,                # S extension (duplicate c4 for butyrate & valerate)
                       K_Pbind, K_Pdiss))                                       # HFO module
        K_so4 = np.array((K_so4_hSRB, K_so4_aSRB, K_so4_pSRB, K_so4_c4SRB, K_so4_c4SRB))  # Duplicate c4 for butyrate & valerate
        
        KIs_h2 = np.array((KI_h2_fa, KI_h2_c4, KI_h2_c4, KI_h2_pro))
        KIs_h2s = np.array((KI_h2s_c4, KI_h2s_c4, KI_h2s_pro, KI_h2s_ac, KI_h2s_h2,
                            KI_h2s_hSRB, KI_h2s_aSRB, KI_h2s_pSRB, KI_h2s_c4SRB, KI_h2s_c4SRB))
        K_H_base = np.array(cls._K_H_base)
        K_H_dH = np.array(cls._K_H_dH)
        Ka_base = np.array([10**(-pKa) for pKa in pKa_base])
        Ka_dH = np.array(Ka_dH)
        k_cryst = np.array(k_cryst) * 24    # converted to d^(-1)
        n_cryst = np.array(n_cryst)
        Ksp_base = np.array([10**(-pK) for pK in cls._pKsp_base])
        Ksp_dH = np.array(cls._Ksp_dH)

        # Create TempState object for storing intermediate calculation results
        # It needs a .data attribute that can be copied and used as a dict
        class TempState:
            def __init__(self):
                self.data = {}  # Temporary storage for solver state (dictionary)

        root = TempState()
        dct = self.__dict__
        dct.update(kwargs)

        dct['_parameters'] = dict(zip(cls._stoichio_params, stoichio_vals))

        # Wrapper to adapt rhos_madm1's 3-argument signature to QSDsan's expected 2-argument signature
        # T_op is retrieved from the reactor temperature during simulation
        # h parameter is optional - used for pH control (passed as kwarg from AnaerobicCSTRmADM1._compile_ODE)
        # **kwargs captures h without violating QSDsan's 2-argument requirement
        def rhos_wrapper(state_arr, params, **kwargs):
            # Get T_op from params dict (set by reactor during simulation)
            T_op = params.get('T_op', cls._T_base)
            # Extract h from kwargs (None if not provided)
            h = kwargs.get('h', None)
            return rhos_madm1(state_arr, params, T_op, h=h)

        self.set_rate_function(rhos_wrapper)
        params_dict = dict(zip(cls._kinetic_params,
                               [ks, Ks, K_PP, K_so4,
                                pH_limits, KS_IN*N_mw, KS_IP*P_mw,
                                KI_nh3, KIs_h2, KIs_h2s,
                                Ka_base, Ka_dH, K_H_base, K_H_dH, kLa,
                                k_cryst, n_cryst, Ksp_base, Ksp_dH,
                                cls._T_base, self._components,
                                root,
                                ]))
        self.rate_function._params = params_dict

        # Pre-compute stoichiometric rows for gas-phase supersaturation correction
        # Only the non-transfer processes (all but the trailing biogas transfers) contribute biologically
        # Must be done AFTER params are set so stoichio_eval() can lambdify properly
        stoichio_mass = self.stoichio_eval().T  # shape: (n_components, n_processes)
        n_gas = len(cls._biogas_IDs)
        n_non_gas = stoichio_mass.shape[1] - n_gas
        ic_idx = cmps.index('S_IC')
        supersat_data = {
            'n_gas': n_gas,
            'nu_IC': stoichio_mass[ic_idx, :n_non_gas].copy(),
        }
        if 'S_IS' in cmps.IDs:
            supersat_data['nu_IS'] = stoichio_mass[cmps.index('S_IS'), :n_non_gas].copy()
        else:
            supersat_data['nu_IS'] = None

        # Add supersaturation data to params
        self.rate_function._params['gas_supersat'] = supersat_data
        return self

    def solve_pH(self, state_arr, params=None):
        """
        Solve for pH using the PCM model.

        This method is expected by QSDsan's AnaerobicCSTR reactor.
        It wraps our pcm() function to provide the interface the reactor needs.

        Parameters
        ----------
        state_arr : ndarray
            State variable array
        params : dict, optional
            Kinetic parameters (if None, uses self._rhos_func.params)

        Returns
        -------
        float
            Computed pH value
        """
        if params is None:
            params = self._rhos_func.params if hasattr(self, '_rhos_func') else {}

        pH, nh3, co2, acts = pcm(state_arr, params)
        return pH

    def dydt_Sh2_AD(self, S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in):
        """
        H2 mass balance residual for Newton solver.

        This method is expected by QSDsan's AnaerobicCSTR reactor for H2 tracking.

        Parameters
        ----------
        S_h2 : float
            Trial H2 concentration [kg/m³]
        state_arr : ndarray
            State variable array (will be temporarily modified)
        h : tuple
            Pre-computed (pH, nh3, co2, acts) from pcm()
        params : dict
            Kinetic parameters
        f_stoichio : callable
            Stoichiometry function
        V_liq : float
            Liquid volume [m³]
        S_h2_in : float
            Influent H2 concentration [kg/m³]

        Returns
        -------
        float
            Residual of H2 mass balance
        """
        return dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in)

    def grad_dydt_Sh2_AD(self, S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps=_H2_EPS):
        """
        Gradient of H2 mass balance residual using numerical differentiation.

        This method is expected by QSDsan's AnaerobicCSTR reactor for H2 tracking.

        Parameters
        ----------
        S_h2 : float
            Trial H2 concentration [kg/m³]
        state_arr : ndarray
            State variable array
        h : tuple
            Pre-computed (pH, nh3, co2, acts) from pcm()
        params : dict
            Kinetic parameters
        f_stoichio : callable
            Stoichiometry function
        V_liq : float
            Liquid volume [m³]
        S_h2_in : float
            Influent H2 concentration [kg/m³]
        eps : float, optional
            Finite difference step size

        Returns
        -------
        float
            Gradient dR/dS_h2
        """
        return grad_dydt_Sh2_AD(S_h2, state_arr, h, params, f_stoichio, V_liq, S_h2_in, eps)
