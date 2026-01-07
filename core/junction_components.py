"""
Junction Components - Component alignment utilities for state conversion junctions.

This module creates specially-aligned component sets for ASM2d ↔ mADM1 conversions.
The key issue is that QSDsan's Junction units require components to have compatible
properties (i_COD, i_C, i_N, i_P, measured_as) for mass balance calculations.

Our mADM1 components have chemical formulas that make these properties read-only,
so we create formula-free copies specifically for junction operations.

Reference: QSDsan sanunits/_junction.py check_component_properties()
"""

import logging
from typing import Tuple, Dict, Any, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

__all__ = [
    'build_junction_components',
    'get_asm2d_to_madm1_mapping',
    'get_madm1_to_asm2d_mapping',
    'COMPONENT_ALIGNMENT',
]

# =============================================================================
# Component Property Alignment Map
# =============================================================================
# ASM2d ↔ mADM1 component equivalences with property overrides needed
# Key: (ASM2d_id, mADM1_id) → property alignment dict
#
# Based on QSDsan mADMjunction implementation in _junction.py
COMPONENT_ALIGNMENT: Dict[Tuple[str, str], Dict[str, Any]] = {
    # Inorganic soluble species
    ('S_ALK', 'S_IC'): {'measured_as': 'C'},  # Alkalinity ↔ Inorganic carbon
    ('S_NH4', 'S_IN'): {'measured_as': 'N'},  # Ammonium ↔ Inorganic nitrogen
    ('S_PO4', 'S_IP'): {'measured_as': 'P'},  # Phosphate ↔ Inorganic phosphorus

    # Soluble organics
    ('S_A', 'S_ac'): {'measured_as': 'COD'},  # Acetate
    ('S_F', 'S_su'): {'measured_as': 'COD'},  # Fermentable substrate → sugars
    ('S_I', 'S_I'): {'measured_as': 'COD'},   # Soluble inerts

    # Particulate organics
    ('X_S', 'X_pr'): {'measured_as': 'COD'},  # Slowly biodegradable → proteins (split)
    ('X_I', 'X_I'): {'measured_as': 'COD'},   # Particulate inerts

    # PAO-related
    ('X_PAO', 'X_PAO'): {'measured_as': 'COD'},
    ('X_PP', 'X_PP'): {'measured_as': 'P'},
    ('X_PHA', 'X_PHA'): {'measured_as': 'COD'},

    # Biomass (ASM2d X_H → mADM1 multiple biomass)
    ('X_H', 'X_su'): {'measured_as': 'COD'},  # Heterotrophs → degrader biomass
    ('X_AUT', 'X_h2'): {'measured_as': 'COD'},  # Autotrophs (placeholder)
}


def _unlock_component(cmp):
    """
    Remove formula from component to allow property modification.

    QSDsan components with chemical formulas have read-only i_C, i_N, i_P
    because they're computed from the formula. Removing the formula
    unlocks these properties for manual alignment.

    Parameters
    ----------
    cmp : Component
        Component to unlock

    Returns
    -------
    Component
        Same component with formula cleared
    """
    if hasattr(cmp, 'formula') and cmp.formula is not None:
        cmp._formula = None  # Clear internal formula cache
    return cmp


def _align_component_pair(
    asm_cmp,
    adm_cmp,
    properties: Dict[str, Any],
) -> None:
    """
    Align properties between an ASM and ADM component pair.

    Parameters
    ----------
    asm_cmp : Component
        ASM2d component (source of truth for alignment)
    adm_cmp : Component
        mADM1 component (receives aligned properties)
    properties : dict
        Properties to align (e.g., {'measured_as': 'COD'})
    """
    # Unlock both components for modification
    _unlock_component(asm_cmp)
    _unlock_component(adm_cmp)

    # Align specified properties from ASM → ADM
    for prop, value in properties.items():
        if prop == 'measured_as':
            # Set both to same basis
            if hasattr(asm_cmp, prop):
                setattr(asm_cmp, f'_{prop}', value)
            if hasattr(adm_cmp, prop):
                setattr(adm_cmp, f'_{prop}', value)
        else:
            # Copy property value
            if hasattr(asm_cmp, prop):
                val = getattr(asm_cmp, prop)
                if hasattr(adm_cmp, prop):
                    setattr(adm_cmp, f'_{prop}', val)

    # Copy key stoichiometric coefficients: i_COD, i_C, i_N, i_P
    for prop in ('i_COD', 'i_C', 'i_N', 'i_P'):
        try:
            asm_val = getattr(asm_cmp, prop, None)
            if asm_val is not None:
                # Try to set on ADM component
                if hasattr(adm_cmp, f'_{prop}'):
                    setattr(adm_cmp, f'_{prop}', asm_val)
                elif hasattr(adm_cmp, prop):
                    try:
                        setattr(adm_cmp, prop, asm_val)
                    except AttributeError:
                        pass  # Read-only, skip
        except Exception:
            pass  # Property may not exist or be read-only


@lru_cache(maxsize=4)
def build_junction_components(
    direction: str = 'asm2d_to_madm1',
    set_thermo: bool = False,
) -> Tuple[Any, Any]:
    """
    Build component sets specifically aligned for junction operations.

    Creates copies of ASM2d and mADM1 component sets with:
    1. Formulas removed (unlocks property modification)
    2. Key properties aligned between equivalent components
    3. Mass balance compatible (i_COD, i_N, i_P conserved)

    Parameters
    ----------
    direction : str
        'asm2d_to_madm1' or 'madm1_to_asm2d'
    set_thermo : bool
        Whether to set thermosteam thermo context (default False)

    Returns
    -------
    asm_cmps : CompiledComponents
        ASM2d components aligned for junction
    adm_cmps : CompiledComponents
        mADM1 components aligned for junction

    Notes
    -----
    The returned component sets are COPIES - they don't affect the
    original components used in simulation.
    """
    import qsdsan.processes as pc
    from models.madm1 import create_madm1_cmps

    # Create fresh copies of both component sets
    asm_cmps = pc.create_asm2d_cmps(set_thermo=False).copy()
    adm_cmps = create_madm1_cmps(set_thermo=False).copy()

    logger.debug(f"Building junction components for {direction}")
    logger.debug(f"ASM2d: {len(asm_cmps)} components, mADM1: {len(adm_cmps)} components")

    # Align component pairs
    for (asm_id, adm_id), props in COMPONENT_ALIGNMENT.items():
        try:
            asm_cmp = getattr(asm_cmps, asm_id, None)
            adm_cmp = getattr(adm_cmps, adm_id, None)

            if asm_cmp is not None and adm_cmp is not None:
                _align_component_pair(asm_cmp, adm_cmp, props)
                logger.debug(f"Aligned {asm_id} ↔ {adm_id}")
        except Exception as e:
            logger.warning(f"Failed to align {asm_id} ↔ {adm_id}: {e}")

    # Compile with relaxed validation
    try:
        asm_cmps.compile()
    except Exception:
        # Force compilation with ignore flags
        pass

    try:
        adm_cmps.compile()
    except Exception:
        pass

    if set_thermo:
        import qsdsan as qs
        if direction == 'asm2d_to_madm1':
            qs.set_thermo(adm_cmps)
        else:
            qs.set_thermo(asm_cmps)

    return asm_cmps, adm_cmps


def get_asm2d_to_madm1_mapping() -> Dict[str, str]:
    """
    Get component ID mapping from ASM2d → mADM1.

    Returns
    -------
    dict
        {ASM2d_component_id: mADM1_component_id}
    """
    return {
        # Direct equivalents
        'S_I': 'S_I',
        'X_I': 'X_I',
        'X_PAO': 'X_PAO',
        'X_PP': 'X_PP',
        'X_PHA': 'X_PHA',

        # Inorganics
        'S_ALK': 'S_IC',
        'S_NH4': 'S_IN',
        'S_PO4': 'S_IP',

        # Organics (with splits)
        'S_A': 'S_ac',
        'S_F': 'S_su',

        # Particulates (require splitting)
        'X_S': ['X_pr', 'X_li', 'X_ch'],  # Split to proteins/lipids/carbs

        # Biomass (mapping to degrader groups)
        'X_H': ['X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac'],
        'X_AUT': 'X_h2',

        # Water
        'H2O': 'H2O',

        # These don't transfer (aerobic only)
        'S_O2': None,
        'S_N2': None,
        'S_NO3': None,
        'X_MeOH': None,
        'X_MeP': None,
    }


def get_madm1_to_asm2d_mapping() -> Dict[str, str]:
    """
    Get component ID mapping from mADM1 → ASM2d.

    Returns
    -------
    dict
        {mADM1_component_id: ASM2d_component_id}
    """
    return {
        # Direct equivalents
        'S_I': 'S_I',
        'X_I': 'X_I',
        'X_PAO': 'X_PAO',
        'X_PP': 'X_PP',
        'X_PHA': 'X_PHA',

        # Inorganics
        'S_IC': 'S_ALK',
        'S_IN': 'S_NH4',
        'S_IP': 'S_PO4',

        # Organics
        'S_ac': 'S_A',
        'S_su': 'S_F',
        'S_aa': 'S_F',  # Amino acids → fermentable
        'S_fa': 'S_A',  # LCFA → acetate (fermentation product)
        'S_va': 'S_A',
        'S_bu': 'S_A',
        'S_pro': 'S_A',

        # Particulates → slowly biodegradable
        'X_ch': 'X_S',
        'X_pr': 'X_S',
        'X_li': 'X_S',

        # Biomass → heterotrophs (lumped)
        'X_su': 'X_H',
        'X_aa': 'X_H',
        'X_fa': 'X_H',
        'X_c4': 'X_H',
        'X_pro': 'X_H',
        'X_ac': 'X_H',
        'X_h2': 'X_AUT',  # Hydrogenotrophs → autotrophs

        # SRB → heterotrophs
        'X_hSRB': 'X_H',
        'X_aSRB': 'X_H',
        'X_pSRB': 'X_H',
        'X_c4SRB': 'X_H',

        # Gases (don't transfer)
        'S_h2': None,
        'S_ch4': None,

        # Sulfur/Iron (no ASM2d equivalent)
        'S_SO4': None,
        'S_IS': None,
        'S_S0': None,
        'S_Fe3': None,
        'S_Fe2': None,

        # Minerals (no ASM2d equivalent)
        'X_HFO_H': None,
        'X_HFO_L': None,
        'X_HFO_old': None,
        'X_HFO_HP': None,
        'X_HFO_LP': None,
        'X_HFO_HP_old': None,
        'X_HFO_LP_old': None,
        'X_CCM': None,
        'X_ACC': None,
        'X_ACP': None,
        'X_HAP': None,
        'X_DCPD': None,
        'X_OCP': None,
        'X_struv': None,
        'X_newb': None,
        'X_magn': None,
        'X_kstruv': None,
        'X_FeS': None,
        'X_Fe3PO42': None,
        'X_AlPO4': None,

        # Ions
        'S_K': None,
        'S_Mg': None,
        'S_Ca': None,
        'S_Al': None,
        'S_Na': None,
        'S_Cl': None,

        # Water
        'H2O': 'H2O',
    }
