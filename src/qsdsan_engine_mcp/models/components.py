"""
mADM1 (Modified ADM1) component loader for QSDsan Engine.

Based on QSD-Group/QSDsan, adm1 branch, commit b5a0757 (2024-11-22)
Licensed under NCSA Open Source License

This module loads the full mADM1 component set (68 components):
- Core ADM1 soluble/particulate components (0-23)
- EBPR extension: X_PHA, X_PP, X_PAO (24-26)
- Metal ions: S_K, S_Mg, S_Ca, S_Al, S_Na, S_Cl (27-28, 45-46, 60-61)
- Sulfur species: S_SO4, S_IS, S_S0 (29-30, 35)
- Disaggregated SRB biomass: X_hSRB, X_aSRB, X_pSRB, X_c4SRB (31-34)
- Iron species: S_Fe3, S_Fe2, X_HFO_* (36-44)
- Mineral precipitates: X_CCM, X_ACC, X_struv, X_FeS, etc. (47-59)
- Trace micronutrient metals (QSDsan-only, optional/conservative): S_Co, S_Ni, S_Se (62-64)
- Water: H2O (65)

Attribution:
- Component definitions from qsdsan/processes/_madm1.py:88-227
- See docs/qsdsan_sulfur_attribution.md for full attribution
"""
import logging
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()
from qsdsan import Component, Components
import qsdsan as qs

logger = logging.getLogger(__name__)

# Global component set (initialized on first import)
ADM1_SULFUR_CMPS = None
SULFUR_COMPONENT_INFO = None


def create_adm1_sulfur_cmps():
    """
    Create the full mADM1 component set (68 components: 67 state variables + H2O).

    This is the FULL mADM1 (Modified ADM1) with phosphorus, sulfur, iron, and the QSDsan-only
    Na/trace-element extensions (the trace metals S_Co/S_Ni/S_Se are optional, conservative tracers).

    Component structure (66 total):
    - Core ADM1 soluble (0-12): S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2, S_ch4, S_IC, S_IN, S_IP, S_I
    - Core ADM1 particulates (13-23): X_ch, X_pr, X_li, X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2, X_I
    - EBPR extension (24-26): X_PHA, X_PP, X_PAO
    - Metal ions (27-28): S_K, S_Mg
    - Sulfur species (29-35): S_SO4, S_IS, X_hSRB, X_aSRB, X_pSRB, X_c4SRB, S_S0
    - Iron species (36-44): S_Fe3, S_Fe2, X_HFO_H, X_HFO_L, X_HFO_old, X_HFO_HP, X_HFO_LP, X_HFO_HP_old, X_HFO_LP_old
    - More metals (45-46): S_Ca, S_Al
    - Mineral precipitates (47-61): X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD, X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS, X_Mackinawite, X_Fe3PO42, X_Siderite, X_AlPO4
    - Final ions (60-61): S_Na, S_Cl
    - Trace micronutrient metals (62-64, QSDsan-only, optional/conservative): S_Co, S_Ni, S_Se
    - Water (65): H2O

    Returns:
        Components object with the full mADM1 component set

    Raises:
        ImportError: If QSDsan mADM1 components cannot be loaded
    """
    logger.info("Creating full mADM1 component set")

    # The local implementation is the only source: qsdsan 1.5.x removed the
    # upstream _madm1 module ("temporarily disable mADM1", 93a15355), so the
    # old qsdsan.processes._madm1 fallback is dead and models.madm1 is canonical.
    try:
        from qsdsan_engine_mcp.models.madm1 import create_madm1_cmps
        logger.info("Using local mADM1 implementation from models.madm1")
    except ImportError:
        logger.error("mADM1 module not available - cannot create full component set")
        raise ImportError(
            "mADM1 components not available: the local implementation "
            "models/madm1.py failed to import (qsdsan>=1.5 ships no upstream "
            "mADM1 to fall back to)."
        )

    # Get full mADM1 components WITHOUT setting thermo yet
    # CRITICAL: Do not modify order - mADM1 kinetics depend on state vector positions
    madm1_cmps = create_madm1_cmps(set_thermo=False)
    logger.info(f"Loaded {len(madm1_cmps)} mADM1 components")

    # Verify mADM1 structure against the canonical order (SSOT in core.components.catalog)
    from qsdsan_engine_mcp.core.components.catalog import get_component_ids
    _canonical = get_component_ids('mADM1')
    if len(madm1_cmps) != len(_canonical):
        raise RuntimeError(f"Expected {len(_canonical)} mADM1 components, got {len(madm1_cmps)}")

    # Verify EVERY position against the canonical order — the SSOT already
    # fetched above. A hand-kept spot-position map here went stale when the
    # micronutrient extension (X_Mackinawite / X_Siderite / S_Co / S_Ni /
    # S_Se) grew the sequence to 68 and shifted the tail, which made this
    # loader raise on a perfectly canonical component set (found 2026-08-25
    # by the codex plan review). Full-sequence equality cannot rot.
    for idx, expected_id in enumerate(_canonical):
        actual_id = madm1_cmps.IDs[idx]
        if actual_id != expected_id:
            raise RuntimeError(
                f"mADM1 component ordering broken: position {idx} is '{actual_id}', "
                f"expected '{expected_id}'"
            )

    logger.info("mADM1 component ordering verified")

    # Components are already compiled by create_madm1_cmps
    # Just set the active thermo on QSDsan
    qs.set_thermo(madm1_cmps)

    logger.info("mADM1 component thermodynamics set successfully")

    # Set global components for validation and info functions
    set_global_components(madm1_cmps)

    return madm1_cmps


def _init_component_info():
    """Initialize component info dictionary after component set is created."""
    global SULFUR_COMPONENT_INFO

    if ADM1_SULFUR_CMPS is None:
        raise RuntimeError("Component set not initialized. Call create_adm1_sulfur_cmps() first.")

    # For mADM1, we document key extension components beyond base ADM1
    # Use dynamic indexing to get positions
    SULFUR_COMPONENT_INFO = {
        'total_components': len(ADM1_SULFUR_CMPS),
        'description': 'Full mADM1 with P/S/Fe + Na/trace-element extensions',
        'key_components': {
            'S_SO4': {
                'index': ADM1_SULFUR_CMPS.index('S_SO4'),
                'description': 'Sulfate (SO4 2-)',
                'units': 'kg S/m3',
                'typical_range_mg_l': (10, 500)
            },
            'S_IS': {
                'index': ADM1_SULFUR_CMPS.index('S_IS'),
                'description': 'Total dissolved sulfide (H2S + HS- + S2-)',
                'units': 'kg S/m3',
                'typical_range_mg_l': (0.1, 100),
                'inhibition_threshold_mg_l': 50
            },
            'X_hSRB': {
                'index': ADM1_SULFUR_CMPS.index('X_hSRB'),
                'description': 'Hydrogenotrophic sulfate-reducing bacteria',
                'units': 'kg/m3',
                'typical_range_mg_l': (1, 50)
            },
            'S_Fe3': {
                'index': ADM1_SULFUR_CMPS.index('S_Fe3'),
                'description': 'Ferric iron (Fe 3+)',
                'units': 'kg Fe/m3',
                'typical_range_mg_l': (0, 50)
            },
            'S_IP': {
                'index': ADM1_SULFUR_CMPS.index('S_IP'),
                'description': 'Inorganic phosphate',
                'units': 'kg P/m3',
                'typical_range_mg_l': (5, 50)
            },
            'X_PHA': {
                'index': ADM1_SULFUR_CMPS.index('X_PHA'),
                'description': 'Polyhydroxyalkanoates (PAO storage)',
                'units': 'kg/m3',
                'typical_range_mg_l': (0, 100)
            },
            'S_Na': {
                'index': ADM1_SULFUR_CMPS.index('S_Na'),
                'description': 'Sodium ion',
                'units': 'kg Na/m3',
                'typical_range_mg_l': (50, 300)
            }
        }
    }

    logger.debug(f"Component info initialized for {len(ADM1_SULFUR_CMPS)} mADM1 components")


# Component set will be initialized via async loader (utils/qsdsan_loader.py)
# This prevents blocking the MCP event loop during module import
ADM1_SULFUR_CMPS = None
SULFUR_COMPONENT_INFO = None

logger.info("Component set initialization deferred to async loader")


def set_global_components(components):
    """
    Set the global component set (called by async loader).

    Args:
        components: The compiled QSDsan Components object
    """
    global ADM1_SULFUR_CMPS
    ADM1_SULFUR_CMPS = components
    _init_component_info()
    logger.info("Global ADM1_SULFUR_CMPS set by async loader")


def get_component_info(component_id: str = None):
    """
    Get information about mADM1 key components.

    Args:
        component_id: Optional component ID (e.g., 'S_SO4', 'S_IS', 'X_hSRB').
                     If None, returns info for all components.

    Returns:
        Dictionary with component information

    Example:
        >>> info = get_component_info('S_SO4')
        >>> print(f"Sulfate index: {info['index']}")
    """
    if SULFUR_COMPONENT_INFO is None:
        raise RuntimeError("Component info not initialized")

    if component_id is None:
        return SULFUR_COMPONENT_INFO
    elif component_id in SULFUR_COMPONENT_INFO.get('key_components', {}):
        return SULFUR_COMPONENT_INFO['key_components'][component_id]
    else:
        valid_ids = ', '.join(SULFUR_COMPONENT_INFO.get('key_components', {}).keys())
        raise ValueError(f"Unknown component ID: {component_id}. Valid IDs: {valid_ids}")


def verify_component_ordering():
    """
    Verify that mADM1 component ordering is correct.

    This is critical - mADM1 kinetics depend on specific state vector positions.
    Checks ALL canonical component positions against the expected order.

    Returns:
        Boolean indicating if ordering is correct

    Raises:
        AssertionError: If component ordering is incorrect
    """
    if ADM1_SULFUR_CMPS is None:
        raise RuntimeError("Component set not initialized")

    # Canonical order is the SINGLE SOURCE OF TRUTH in core.components.catalog.
    from qsdsan_engine_mcp.core.components.catalog import get_component_ids
    expected_full_order = get_component_ids('mADM1')

    # Check total count
    assert len(ADM1_SULFUR_CMPS) == len(expected_full_order), \
        f"Expected {len(expected_full_order)} components, got {len(ADM1_SULFUR_CMPS)}"

    # Verify every canonical position
    for idx, expected_id in enumerate(expected_full_order):
        actual_id = ADM1_SULFUR_CMPS.IDs[idx]
        assert actual_id == expected_id, \
            f"Component ordering broken at position {idx}: found '{actual_id}', expected '{expected_id}'"

    logger.info(f"Component ordering verified: all {len(expected_full_order)} mADM1 components in correct positions")
    return True


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)

    print("=== mADM1 Component Loader Test ===\n")

    # Initialize component set first
    print("0. Initializing mADM1 components...")
    try:
        cmps = create_adm1_sulfur_cmps()
        print(f"   [OK] Loaded {len(cmps)} components\n")
    except Exception as e:
        print(f"   [FAIL] Component initialization failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

    # 1. Verify component set
    print("1. Component Set:")
    print(f"   Total components: {len(ADM1_SULFUR_CMPS)}")
    print(f"   First 5: {ADM1_SULFUR_CMPS.IDs[:5]}")
    print(f"   Last 5: {ADM1_SULFUR_CMPS.IDs[-5:]}")
    print()

    # 2. Verify ordering (checks all canonical component positions)
    print("2. Component Ordering Verification:")
    try:
        verify_component_ordering()
        print(f"   [OK] All {len(ADM1_SULFUR_CMPS)} component positions verified")
    except AssertionError as e:
        print(f"   [FAIL] Component ordering error: {e}")
        exit(1)
    print()

    # 3. Key component info
    print("3. Key mADM1 Component Details:")
    for cid in ['S_SO4', 'S_IS', 'X_hSRB', 'S_Fe3', 'S_IP', 'X_PHA', 'S_Na']:
        try:
            info = get_component_info(cid)
            print(f"   {cid} (position {info['index']}):")
            print(f"      Description: {info['description']}")
            print(f"      Units: {info['units']}")
            if 'typical_range_mg_l' in info:
                print(f"      Typical range: {info['typical_range_mg_l'][0]}-{info['typical_range_mg_l'][1]} mg/L")
            print()
        except ValueError as e:
            print(f"   [SKIP] {cid}: {e}\n")
