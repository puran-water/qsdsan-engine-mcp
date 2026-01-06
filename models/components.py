"""
mADM1 (Modified ADM1) component loader for QSDsan Engine.

Based on QSD-Group/QSDsan, adm1 branch, commit b5a0757 (2024-11-22)
Licensed under NCSA Open Source License

This module loads the full mADM1 component set (63 components):
- Core ADM1 soluble/particulate components (0-23)
- EBPR extension: X_PHA, X_PP, X_PAO (24-26)
- Metal ions: S_K, S_Mg, S_Ca, S_Al, S_Na, S_Cl (27-28, 45-46, 60-61)
- Sulfur species: S_SO4, S_IS, S_S0 (29-30, 35)
- Disaggregated SRB biomass: X_hSRB, X_aSRB, X_pSRB, X_c4SRB (31-34)
- Iron species: S_Fe3, S_Fe2, X_HFO_* (36-44)
- Mineral precipitates: X_CCM, X_ACC, X_struv, X_FeS, etc. (47-59)

Attribution:
- Component definitions from qsdsan/processes/_madm1.py:88-227
- See docs/qsdsan_sulfur_attribution.md for full attribution
"""
import logging
from qsdsan import Component, Components
import qsdsan as qs

logger = logging.getLogger(__name__)

# Global component set (initialized on first import)
ADM1_SULFUR_CMPS = None
SULFUR_COMPONENT_INFO = None


def create_adm1_sulfur_cmps():
    """
    Create mADM1 component set: 63 components (62 state variables + H2O).

    This is the FULL mADM1 (Modified ADM1) with phosphorus, sulfur, and iron extensions.
    Includes all 62 state variables from qsdsan_madm1.py plus H2O.

    Component structure (63 total):
    - Core ADM1 soluble (0-12): S_su, S_aa, S_fa, S_va, S_bu, S_pro, S_ac, S_h2, S_ch4, S_IC, S_IN, S_IP, S_I
    - Core ADM1 particulates (13-23): X_ch, X_pr, X_li, X_su, X_aa, X_fa, X_c4, X_pro, X_ac, X_h2, X_I
    - EBPR extension (24-26): X_PHA, X_PP, X_PAO
    - Metal ions (27-28): S_K, S_Mg
    - Sulfur species (29-35): S_SO4, S_IS, X_hSRB, X_aSRB, X_pSRB, X_c4SRB, S_S0
    - Iron species (36-44): S_Fe3, S_Fe2, X_HFO_H, X_HFO_L, X_HFO_old, X_HFO_HP, X_HFO_LP, X_HFO_HP_old, X_HFO_LP_old
    - More metals (45-46): S_Ca, S_Al
    - Mineral precipitates (47-59): X_CCM, X_ACC, X_ACP, X_HAP, X_DCPD, X_OCP, X_struv, X_newb, X_magn, X_kstruv, X_FeS, X_Fe3PO42, X_AlPO4
    - Final ions (60-61): S_Na, S_Cl
    - Water (62): H2O

    Returns:
        Components object with 63 components

    Raises:
        ImportError: If QSDsan mADM1 components cannot be loaded
    """
    logger.info("Creating full mADM1 component set (63 components)")

    # Try to import mADM1 (only available in adm1 branch OR local implementation)
    try:
        from models.madm1 import create_madm1_cmps
        madm1_available = True
        logger.info("Using local mADM1 implementation from models.madm1")
    except ImportError:
        try:
            from qsdsan.processes._madm1 import create_madm1_cmps
            madm1_available = True
            logger.info("Using QSDsan upstream mADM1 implementation")
        except ImportError:
            madm1_available = False
            logger.error("mADM1 module not available - cannot create full component set")
            raise ImportError(
                "mADM1 components not available. Ensure either:\n"
                "1. Local implementation exists in utils/qsdsan_madm1.py, OR\n"
                "2. QSDsan adm1 branch is installed"
            )

    # Get full mADM1 components (63 components) WITHOUT setting thermo yet
    # CRITICAL: Do not modify order - mADM1 kinetics depend on state vector positions
    madm1_cmps = create_madm1_cmps(set_thermo=False)
    logger.info(f"Loaded {len(madm1_cmps)} mADM1 components")

    # Verify mADM1 structure
    if len(madm1_cmps) != 63:
        raise RuntimeError(f"Expected 63 mADM1 components, got {len(madm1_cmps)}")

    # Verify key components are in correct positions
    expected_positions = {
        0: 'S_su',
        10: 'S_IN',
        11: 'S_IP',
        27: 'S_K',
        29: 'S_SO4',
        30: 'S_IS',
        36: 'S_Fe3',
        45: 'S_Ca',
        60: 'S_Na',
        61: 'S_Cl',
        62: 'H2O'
    }

    for idx, expected_id in expected_positions.items():
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
        'description': 'Full mADM1 with P/S/Fe extensions (63 components)',
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
    Checks ALL 63 component positions against the expected order.

    Returns:
        Boolean indicating if ordering is correct

    Raises:
        AssertionError: If component ordering is incorrect
    """
    if ADM1_SULFUR_CMPS is None:
        raise RuntimeError("Component set not initialized")

    # Check total count
    assert len(ADM1_SULFUR_CMPS) == 63, f"Expected 63 components, got {len(ADM1_SULFUR_CMPS)}"

    # Complete expected order for ALL 63 components
    # Based on qsdsan_madm1.py:212-223 and .codex/AGENTS.md:385-398
    expected_full_order = [
        'S_su', 'S_aa', 'S_fa', 'S_va', 'S_bu', 'S_pro', 'S_ac', 'S_h2', 'S_ch4',
        'S_IC', 'S_IN', 'S_IP', 'S_I',
        'X_ch', 'X_pr', 'X_li', 'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2', 'X_I',
        'X_PHA', 'X_PP', 'X_PAO',
        'S_K', 'S_Mg',
        'S_SO4', 'S_IS', 'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB', 'S_S0',
        'S_Fe3', 'S_Fe2',
        'X_HFO_H', 'X_HFO_L', 'X_HFO_old', 'X_HFO_HP', 'X_HFO_LP', 'X_HFO_HP_old', 'X_HFO_LP_old',
        'S_Ca', 'S_Al',
        'X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP',
        'X_struv', 'X_newb', 'X_magn', 'X_kstruv',
        'X_FeS', 'X_Fe3PO42', 'X_AlPO4',
        'S_Na', 'S_Cl',
        'H2O'
    ]

    # Verify all 63 positions
    for idx, expected_id in enumerate(expected_full_order):
        actual_id = ADM1_SULFUR_CMPS.IDs[idx]
        assert actual_id == expected_id, \
            f"Component ordering broken at position {idx}: found '{actual_id}', expected '{expected_id}'"

    logger.info("Component ordering verified: ALL 63 mADM1 components in correct positions")
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

    # 2. Verify ordering (now checks all 63 components)
    print("2. Component Ordering Verification:")
    try:
        verify_component_ordering()
        print("   [OK] All 63 component positions verified")
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
