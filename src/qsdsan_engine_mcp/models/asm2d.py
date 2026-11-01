'ASM2d (Activated Sludge Model 2d) component loader for QSDsan Engine.'

import logging
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    'create_asm2d_components',
    'create_asm2d_process',
    'DEFAULT_ASM2D_KWARGS',
    'DEFAULT_DOMESTIC_WW',
]


def create_asm2d_components(set_thermo: bool = True):
    'Create ASM2d components with calibrated BOD5/COD fractions.'
    import qsdsan as qs
    from qsdsan import process_models as pc

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


def create_asm2d_process(cmps=None, **kwargs):
    """
    Create ASM2d process model with calibrated kinetics.

    Parameters
    ----------
    cmps : CompiledComponents, optional
        Component set to use. If None, creates new components.
    **kwargs : dict
        Override default kinetic parameters (see DEFAULT_ASM2D_KWARGS).

    Returns
    -------
    ASM2d
        QSDsan ASM2d process model instance

    Example
    -------
    >>> cmps = create_asm2d_components()
    >>> asm2d = create_asm2d_process(cmps, mu_H=7.0)
    """
    from qsdsan import process_models as pc

    if cmps is None:
        cmps = create_asm2d_components(set_thermo=True)

    # Merge default kwargs with overrides
    process_kwargs = DEFAULT_ASM2D_KWARGS.copy()
    process_kwargs.update(kwargs)

    logger.info("Creating ASM2d process model...")
    asm2d = pc.ASM2d(**process_kwargs)

    logger.info(f"Created ASM2d process model with {len(asm2d.IDs)} reactions")
    return asm2d


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
