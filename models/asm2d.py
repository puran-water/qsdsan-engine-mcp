"""
ASM2d (Activated Sludge Model 2d) component loader for QSDsan Engine.

Uses QSDsan's pc.create_asm2d_cmps() directly with BOD5/COD fraction calibration
from the Pune_Nanded reference implementation.

Component Count: 19
- Soluble: S_O2, S_N2, S_NH4, S_NO3, S_PO4, S_F, S_A, S_I, S_ALK
- Particulate: X_I, X_S, X_H, X_PAO, X_PP, X_PHA, X_AUT, X_MeOH, X_MeP
- Water: H2O

Note: QSDsan's create_asm2d_cmps() includes:
- X_MeOH: Metal-hydroxides (chemical phosphorus removal precipitates)
- X_MeP: Metal-phosphates (chemical phosphorus removal precipitates)

Reference: Pune_Nanded_WWTP_updated.py (Gates Foundation MBR project)

Direct QSDsan import: qsdsan.processes.create_asm2d_cmps()
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    'create_asm2d_components',
    'DEFAULT_ASM2D_KWARGS',
    'DEFAULT_DOMESTIC_WW',
]


def create_asm2d_components(set_thermo: bool = True):
    """
    Create ASM2d components with calibrated BOD5/COD fractions.

    Uses QSDsan's upstream pc.create_asm2d_cmps() which includes the
    mASM2d extension components (X_MeOH for methanol-degrading biomass
    and X_MeP for metal hydroxide precipitates).

    Parameters
    ----------
    set_thermo : bool
        If True, set the created components as the global thermo object

    Returns
    -------
    CompiledComponents
        QSDsan component set for ASM2d/mASM2d

    Notes
    -----
    BOD5/COD fractions are calibrated from Pune_Nanded reference:
    - X_S: 0.25 (slowly biodegradable substrate)
    - S_F: 0.6 (fermentable substrate)
    - S_A: 0.4 (volatile fatty acids)
    """
    import qsdsan as qs
    from qsdsan import processes as pc

    logger.info("Creating ASM2d components with Pune_Nanded calibration...")

    cmps = pc.create_asm2d_cmps(set_thermo=False)

    # Apply BOD5/COD calibration from Pune_Nanded reference
    cmps.X_S.f_BOD5_COD = 0.25
    cmps.S_F.f_BOD5_COD = 0.6
    cmps.S_A.f_BOD5_COD = 0.4

    cmps.compile()

    if set_thermo:
        qs.set_thermo(cmps)

    logger.info(f"Created ASM2d components: {len(cmps.IDs)} components")
    return cmps


# Default ASM2d kinetic parameters (from Pune_Nanded reference)
DEFAULT_ASM2D_KWARGS = {
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

# Default domestic wastewater composition (mg/L) - from Pune_Nanded
DEFAULT_DOMESTIC_WW = {
    'S_I': 10,
    'X_I': 120,
    'S_F': 75,
    'S_A': 20,
    'X_S': 100,
    'S_NH4': 17,
    'S_PO4': 9,
    'X_PP': 0,
    'X_PHA': 10,
    'X_H': 5,
    'X_AUT': 5,
    'X_PAO': 5,
    'X_MeOH': 32,  # Metal-hydroxides (chemical P removal precipitates)
    'S_ALK': 36,
}
