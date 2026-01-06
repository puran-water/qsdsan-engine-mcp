"""
Thermodynamic functions for mineral precipitation in mADM1.

This module provides:
- Ionic strength calculation
- Activity coefficient calculation (Davies equation)
- Ion activity calculations
- Saturation index calculations for 13 minerals

Design Principles (DRY):
- Reuses temperature correction from qsdsan.processes._adm1
- Reuses unit conversion utilities from QSDsan
- Minimal custom code (~80 LOC)

References:
- Davies equation: Davies C.W. (1962) Ion Association
- Ksp values: Stumm & Morgan (1996) Aquatic Chemistry
- Struvite: Ohlinger et al. (1998) Water Research
"""

import numpy as np
from qsdsan.processes._adm1 import R, T_correction_factor  # DRY: Reuse QSDsan


def ionic_strength(state_arr, cmps, unit_conversion):
    """
    Calculate ionic strength: I = 0.5 × Σ(c_i × z_i²)

    Parameters
    ----------
    state_arr : array
        State vector [kg/m³]
    cmps : CompiledComponents
        QSDsan components object
    unit_conversion : array
        Conversion factors kg/m³ → mol/L

    Returns
    -------
    float
        Ionic strength [mol/L]
    """
    I = 0.0

    # Monovalent ions (z = ±1)
    for ion in ['S_Na', 'S_K', 'S_Cl']:
        try:
            idx = cmps.index(ion)
            conc_M = state_arr[idx] * unit_conversion[idx]
            I += 0.5 * conc_M * 1**2
        except ValueError:
            pass  # Ion not in component set

    # Divalent ions (z = ±2)
    for ion in ['S_Ca', 'S_Mg', 'S_Fe2']:
        try:
            idx = cmps.index(ion)
            conc_M = state_arr[idx] * unit_conversion[idx]
            I += 0.5 * conc_M * 2**2
        except ValueError:
            pass

    # Trivalent ions (z = ±3)
    for ion in ['S_Fe3', 'S_Al']:
        try:
            idx = cmps.index(ion)
            conc_M = state_arr[idx] * unit_conversion[idx]
            I += 0.5 * conc_M * 3**2
        except ValueError:
            pass

    return I


def davies_activity_coeff(charge, I):
    """
    Davies equation for activity coefficient.

    log₁₀(γ) = -A × z² × [√I/(1+√I) - 0.3I]

    Valid for I < 0.5 M (typical anaerobic digester: 0.05-0.2 M)

    Parameters
    ----------
    charge : int
        Ion charge (e.g., +2 for Ca²⁺, -3 for PO₄³⁻)
    I : float
        Ionic strength [mol/L]

    Returns
    -------
    float
        Activity coefficient γ (unitless)
    """
    if I < 1e-10:  # Essentially zero ionic strength
        return 1.0

    A = 0.51  # Davies constant at 25°C (temperature-independent approximation)
    sqrt_I = np.sqrt(I)

    log_gamma = -A * charge**2 * (sqrt_I / (1 + sqrt_I) - 0.3 * I)
    gamma = 10**log_gamma

    return gamma


def calc_activities(state_arr, cmps, pH, I, unit_conversion):
    """
    Calculate ion activities from state vector.

    Converts concentrations [kg/m³] to activities [mol/L] using:
    - Unit conversion
    - pH-dependent speciation
    - Activity coefficients (Davies equation)

    Parameters
    ----------
    state_arr : array
        State vector [kg/m³]
    cmps : CompiledComponents
        QSDsan components
    pH : float
        pH value
    I : float
        Ionic strength [mol/L]
    unit_conversion : array
        Conversion factors

    Returns
    -------
    dict
        Ion activities {ion_name: activity [mol/L]}
    """
    activities = {}

    # Helper function for ion extraction
    def get_ion(name, charge):
        try:
            idx = cmps.index(name)
            conc_M = state_arr[idx] * unit_conversion[idx]
            gamma = davies_activity_coeff(charge, I)
            return conc_M * gamma
        except (ValueError, IndexError):
            return 0.0

    # Extract major cations
    activities['Ca2+'] = get_ion('S_Ca', +2)
    activities['Mg2+'] = get_ion('S_Mg', +2)
    activities['Fe2+'] = get_ion('S_Fe2', +2)
    activities['Fe3+'] = get_ion('S_Fe3', +3)
    activities['Al3+'] = get_ion('S_Al', +3)
    activities['K+'] = get_ion('S_K', +1)
    activities['Na+'] = get_ion('S_Na', +1)

    # pH-dependent species
    H = 10**(-pH)
    Kw = 1e-14  # Water dissociation constant
    OH = Kw / H
    activities['OH-'] = OH * davies_activity_coeff(-1, I)

    # Phosphate speciation (simplified - full speciation in PCM solver)
    # For now, assume total P is available as PO4³⁻
    # TODO: Use proper phosphate speciation from PCM
    try:
        idx_P = cmps.index('S_IP')
        P_total_M = state_arr[idx_P] * unit_conversion[idx_P]

        # pH-dependent PO₄³⁻ fraction (pKa3 ≈ 12.35)
        pKa3 = 12.35
        alpha_PO4 = 1.0 / (1.0 + 10**(pKa3 - pH))
        activities['PO43-'] = P_total_M * alpha_PO4 * davies_activity_coeff(-3, I)
    except (ValueError, IndexError):
        activities['PO43-'] = 0.0

    # Ammonium speciation (pKa ≈ 9.25)
    try:
        idx_N = cmps.index('S_IN')
        N_total_M = state_arr[idx_N] * unit_conversion[idx_N]

        pKa_NH4 = 9.25
        alpha_NH4 = 1.0 / (1.0 + 10**(pH - pKa_NH4))
        activities['NH4+'] = N_total_M * alpha_NH4 * davies_activity_coeff(+1, I)
    except (ValueError, IndexError):
        activities['NH4+'] = 0.0

    # Carbonate speciation (pKa2 ≈ 10.33 for HCO₃⁻/CO₃²⁻)
    try:
        idx_C = cmps.index('S_IC')
        C_total_M = state_arr[idx_C] * unit_conversion[idx_C]

        pKa1 = 6.35  # H₂CO₃/HCO₃⁻
        pKa2 = 10.33  # HCO₃⁻/CO₃²⁻
        Ka1 = 10**(-pKa1)
        Ka2 = 10**(-pKa2)

        denom = H**2 + Ka1*H + Ka1*Ka2
        alpha_CO3 = (Ka1 * Ka2) / denom
        activities['CO32-'] = C_total_M * alpha_CO3 * davies_activity_coeff(-2, I)
    except (ValueError, IndexError):
        activities['CO32-'] = 0.0

    # Sulfide speciation (pKa ≈ 7.0 for H₂S/HS⁻)
    try:
        idx_S = cmps.index('S_IS')
        S_total_M = state_arr[idx_S] * unit_conversion[idx_S]

        pKa_H2S = 7.0  # Approximate at 35°C
        alpha_HS = 1.0 / (1.0 + 10**(pKa_H2S - pH))
        activities['HS-'] = S_total_M * alpha_HS * davies_activity_coeff(-1, I)
    except (ValueError, IndexError):
        activities['HS-'] = 0.0

    return activities


# ============================================================================
# Ksp Functions with Temperature Correction
# ============================================================================

def Ksp_struvite(T_K):
    """Struvite: MgNH₄PO₄·6H₂O - Ksp(25°C) = 10^-13.26"""
    Ksp_298 = 10**(-13.26)
    dH_rxn = -50000  # J/mol (exothermic, Ohlinger 1998)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_HAP(T_K):
    """Hydroxylapatite: Ca₅(PO₄)₃OH - Ksp(25°C) = 10^-58.5"""
    Ksp_298 = 10**(-58.5)
    dH_rxn = -100000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_FeS(T_K):
    """Iron sulfide: FeS - Ksp(25°C) = 10^-18.1"""
    Ksp_298 = 10**(-18.1)
    dH_rxn = 20000  # J/mol (endothermic, Davison 1991)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_Fe3PO42(T_K):
    """Ferrous phosphate: Fe₃(PO₄)₂ - Ksp(25°C) = 10^-36"""
    Ksp_298 = 10**(-36)
    dH_rxn = -80000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_AlPO4(T_K):
    """Aluminum phosphate: AlPO₄ - Ksp(25°C) = 10^-20.5"""
    Ksp_298 = 10**(-20.5)
    dH_rxn = -60000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_calcite(T_K):
    """Calcite: CaCO₃ - Ksp(25°C) = 10^-8.48"""
    Ksp_298 = 10**(-8.48)
    dH_rxn = -12000  # J/mol
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_aragonite(T_K):
    """Aragonite: CaCO₃ (polymorph) - Ksp(25°C) = 10^-8.34"""
    Ksp_298 = 10**(-8.34)
    dH_rxn = -12000  # J/mol
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_ACP(T_K):
    """Amorphous calcium phosphate: Ca₃(PO₄)₂ - Ksp(25°C) = 10^-28.9"""
    Ksp_298 = 10**(-28.9)
    dH_rxn = -70000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_DCPD(T_K):
    """Dicalcium phosphate dihydrate: CaHPO₄·2H₂O - Ksp(25°C) = 10^-6.6"""
    Ksp_298 = 10**(-6.6)
    dH_rxn = -30000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_OCP(T_K):
    """Octacalcium phosphate: Ca₄H(PO₄)₃·2.5H₂O - Ksp(25°C) = 10^-47"""
    Ksp_298 = 10**(-47)
    dH_rxn = -90000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_newberyite(T_K):
    """Newberyite: MgHPO₄·3H₂O - Ksp(25°C) = 10^-5.8"""
    Ksp_298 = 10**(-5.8)
    dH_rxn = -25000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_magnesite(T_K):
    """Magnesite: MgCO₃ - Ksp(25°C) = 10^-7.46"""
    Ksp_298 = 10**(-7.46)
    dH_rxn = -15000  # J/mol (approximate)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


def Ksp_kstruvite(T_K):
    """K-struvite: MgKPO₄·6H₂O - Ksp(25°C) = 10^-11"""
    Ksp_298 = 10**(-11)
    dH_rxn = -45000  # J/mol (approximate, similar to struvite)
    return Ksp_298 * T_correction_factor(298.15, T_K, dH_rxn)


# ============================================================================
# IAP (Ion Activity Product) Calculations
# ============================================================================

def calc_iap_struvite(activities):
    """IAP = {Mg²⁺}{NH₄⁺}{PO₄³⁻}"""
    return activities.get('Mg2+', 0) * activities.get('NH4+', 0) * activities.get('PO43-', 0)


def calc_iap_HAP(activities):
    """IAP = {Ca²⁺}⁵{PO₄³⁻}³{OH⁻}"""
    return (activities.get('Ca2+', 0)**5 *
            activities.get('PO43-', 0)**3 *
            activities.get('OH-', 0))


def calc_iap_FeS(activities):
    """IAP = {Fe²⁺}{HS⁻}"""
    return activities.get('Fe2+', 0) * activities.get('HS-', 0)


def calc_iap_Fe3PO42(activities):
    """IAP = {Fe³⁺}³{PO₄³⁻}²"""
    return activities.get('Fe3+', 0)**3 * activities.get('PO43-', 0)**2


def calc_iap_AlPO4(activities):
    """IAP = {Al³⁺}{PO₄³⁻}"""
    return activities.get('Al3+', 0) * activities.get('PO43-', 0)


def calc_iap_calcite(activities):
    """IAP = {Ca²⁺}{CO₃²⁻}"""
    return activities.get('Ca2+', 0) * activities.get('CO32-', 0)


def calc_iap_aragonite(activities):
    """IAP = {Ca²⁺}{CO₃²⁻} (same as calcite, different Ksp)"""
    return activities.get('Ca2+', 0) * activities.get('CO32-', 0)


def calc_iap_ACP(activities):
    """IAP = {Ca²⁺}³{PO₄³⁻}²"""
    return activities.get('Ca2+', 0)**3 * activities.get('PO43-', 0)**2


def calc_iap_DCPD(activities):
    """IAP = {Ca²⁺}{HPO₄²⁻} (TODO: Add HPO₄²⁻ speciation)"""
    # Simplified: Use PO43- as proxy
    return activities.get('Ca2+', 0) * activities.get('PO43-', 0)


def calc_iap_OCP(activities):
    """IAP = {Ca²⁺}⁴{HPO₄²⁻}³ (TODO: Add HPO₄²⁻ speciation)"""
    # Simplified: Use PO43- as proxy
    return activities.get('Ca2+', 0)**4 * activities.get('PO43-', 0)**3


def calc_iap_newberyite(activities):
    """IAP = {Mg²⁺}{HPO₄²⁻} (TODO: Add HPO₄²⁻ speciation)"""
    # Simplified: Use PO43- as proxy
    return activities.get('Mg2+', 0) * activities.get('PO43-', 0)


def calc_iap_magnesite(activities):
    """IAP = {Mg²⁺}{CO₃²⁻}"""
    return activities.get('Mg2+', 0) * activities.get('CO32-', 0)


def calc_iap_kstruvite(activities):
    """IAP = {Mg²⁺}{K⁺}{PO₄³⁻}"""
    return activities.get('Mg2+', 0) * activities.get('K+', 0) * activities.get('PO43-', 0)


# ============================================================================
# Main Saturation Index Calculator
# ============================================================================

def calc_saturation_indices(state_arr, cmps, pH, T_K, unit_conversion):
    """
    Calculate saturation indices for all 13 minerals.

    SI = IAP / Ksp
    - SI > 1: Supersaturated (precipitation favored)
    - SI = 1: Equilibrium
    - SI < 1: Undersaturated (dissolution favored)

    Parameters
    ----------
    state_arr : array
        State vector [kg/m³]
    cmps : CompiledComponents
        QSDsan components
    pH : float
        pH value
    T_K : float
        Temperature [K]
    unit_conversion : array
        Conversion factors kg/m³ → mol/L

    Returns
    -------
    dict
        Saturation indices {mineral_name: SI_value}
    """
    # Calculate ionic strength
    I = ionic_strength(state_arr, cmps, unit_conversion)

    # Calculate activities
    activities = calc_activities(state_arr, cmps, pH, I, unit_conversion)

    # Calculate saturation indices
    SI = {}

    # Struvite
    IAP = calc_iap_struvite(activities)
    Ksp = Ksp_struvite(T_K)
    SI['struv'] = IAP / Ksp if Ksp > 0 else 1.0

    # Hydroxylapatite
    IAP = calc_iap_HAP(activities)
    Ksp = Ksp_HAP(T_K)
    SI['HAP'] = IAP / Ksp if Ksp > 0 else 1.0

    # Iron sulfide
    IAP = calc_iap_FeS(activities)
    Ksp = Ksp_FeS(T_K)
    SI['FeS'] = IAP / Ksp if Ksp > 0 else 1.0

    # Ferrous phosphate
    IAP = calc_iap_Fe3PO42(activities)
    Ksp = Ksp_Fe3PO42(T_K)
    SI['Fe3PO42'] = IAP / Ksp if Ksp > 0 else 1.0

    # Aluminum phosphate
    IAP = calc_iap_AlPO4(activities)
    Ksp = Ksp_AlPO4(T_K)
    SI['AlPO4'] = IAP / Ksp if Ksp > 0 else 1.0

    # Calcite
    IAP = calc_iap_calcite(activities)
    Ksp = Ksp_calcite(T_K)
    SI['CCM'] = IAP / Ksp if Ksp > 0 else 1.0

    # Aragonite
    IAP = calc_iap_aragonite(activities)
    Ksp = Ksp_aragonite(T_K)
    SI['ACC'] = IAP / Ksp if Ksp > 0 else 1.0

    # Amorphous calcium phosphate
    IAP = calc_iap_ACP(activities)
    Ksp = Ksp_ACP(T_K)
    SI['ACP'] = IAP / Ksp if Ksp > 0 else 1.0

    # Dicalcium phosphate
    IAP = calc_iap_DCPD(activities)
    Ksp = Ksp_DCPD(T_K)
    SI['DCPD'] = IAP / Ksp if Ksp > 0 else 1.0

    # Octacalcium phosphate
    IAP = calc_iap_OCP(activities)
    Ksp = Ksp_OCP(T_K)
    SI['OCP'] = IAP / Ksp if Ksp > 0 else 1.0

    # Newberyite
    IAP = calc_iap_newberyite(activities)
    Ksp = Ksp_newberyite(T_K)
    SI['newb'] = IAP / Ksp if Ksp > 0 else 1.0

    # Magnesite
    IAP = calc_iap_magnesite(activities)
    Ksp = Ksp_magnesite(T_K)
    SI['magn'] = IAP / Ksp if Ksp > 0 else 1.0

    # K-struvite
    IAP = calc_iap_kstruvite(activities)
    Ksp = Ksp_kstruvite(T_K)
    SI['kstruv'] = IAP / Ksp if Ksp > 0 else 1.0

    return SI
