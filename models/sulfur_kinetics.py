"""
Sulfate reduction kinetics with H2S inhibition.

Adapted from QSDsan mADM1:
- Kinetic parameters from Flores-Alsina et al. (2016) Water Research 95, 370-382
- H2S inhibition coefficients from qsdsan/processes/_madm1.py:640-757
- Stoichiometry from qsdsan/data/process_data/_madm1.tsv

Licensed under NCSA Open Source License.

Attribution:
- QSD-Group/QSDsan, adm1 branch, commit b5a0757 (2024-11-22)
- See docs/qsdsan_sulfur_attribution.md for full details
"""
import logging
import numpy as np
from qsdsan import Process, Processes, CompiledProcesses
from qsdsan.processes._adm1 import substr_inhibit, non_compet_inhibit, ADM1
# BUG #6 FIX: Import ModifiedADM1 to avoid X_c requirement
# QSDsan's ADM1 expects X_c, but mADM1 uses X_ch/X_pr/X_li directly
from models.madm1 import ModifiedADM1

logger = logging.getLogger(__name__)


def _resolve_component_index(cmps, comp_id):
    """Return component index, raise a helpful error if missing."""
    # QSDsan raises UndefinedComponent (not ValueError) when component not found
    from qsdsan._components import UndefinedComponent

    try:
        return cmps.index(comp_id)
    except (ValueError, AttributeError, UndefinedComponent) as err:
        raise KeyError(f"Component '{comp_id}' not found in ADM1_SULFUR_CMPS") from err


def _resolve_srb_component(cmps):
    """Resolve the SRB biomass component in the current component set."""
    # QSDsan raises UndefinedComponent (not ValueError) when component not found
    from qsdsan._components import UndefinedComponent

    for candidate in ('X_SRB', 'X_hSRB'):
        try:
            return candidate, cmps.index(candidate)
        except (ValueError, AttributeError, UndefinedComponent):
            continue
    raise KeyError("No SRB biomass component (X_SRB or X_hSRB) found in ADM1_SULFUR_CMPS")

# ============================================================================
# ADM1_Sulfur - Custom CompiledProcesses subclass with H2S biogas tracking
# ============================================================================

class ADM1_Sulfur(CompiledProcesses):
    """
    ADM1 + Sulfate Reduction with H2S biogas tracking.

    Per mADM1 reference (qsdsan_madm1.py:410-618): Define process model subclass
    that extends ADM1 biogas tracking to include H2S (S_IS component).

    This class will be used with Processes.compile(to_class=ADM1_Sulfur) to create
    an instance with the extended attributes.
    """
    # Will be set dynamically in extend_adm1_with_sulfate_and_inhibition()
    # based on runtime ADM1 instance
    _biogas_IDs = None
    _biomass_IDs = None
    _acid_base_pairs = None
    _stoichio_params = None
    _kinetic_params = None

# ============================================================================
# KINETIC PARAMETERS FROM QSDsan mADM1
# ============================================================================

# H2S inhibition coefficients (kg COD/m³) from _madm1.py lines 690-705
# Applied to methanogens and acetogens to model SRB competition
H2S_INHIBITION = {
    # Methanogens (most sensitive)
    'KI_h2s_ac': 0.460,    # Acetoclastic methanogens
    'KI_h2s_h2': 0.400,    # Hydrogenotrophic methanogens
    'KI_h2s_pro': 0.481,   # Propionate degraders
    'KI_h2s_c4': 0.481,    # Butyrate/valerate degraders

    # SRBs (more tolerant)
    'KI_h2s_aSRB': 0.499,  # Acetate-utilizing SRB
    'KI_h2s_hSRB': 0.499,  # H2-utilizing SRB
}

# SRB kinetic parameters from mADM1
SRB_PARAMETERS = {
    # H2-utilizing SRB
    'k_hSRB': 41.125,         # Max H2 uptake rate (d⁻¹)
    'K_hSRB': 5.96e-6,        # Half-sat for H2 (kg COD/m³)
    'K_so4_hSRB': 1.04e-4 * 32.06,  # Half-sat for SO4 (kg S/m³)
    'Y_hSRB': 0.05,           # Biomass yield (kg COD/kg COD)

    # Acetate-utilizing SRB (approximated from propionate values)
    'k_aSRB': 20.0,           # Max acetate uptake rate (d⁻¹)
    'K_aSRB': 0.15,           # Half-sat for acetate (kg COD/m³)
    'Y_aSRB': 0.05,           # Biomass yield

    # Decay
    'k_dec_SRB': 0.02,        # Decay rate (d⁻¹)
    'f_sI_xb': 0.1,           # Fraction to soluble inerts

    # Inhibition
    **H2S_INHIBITION           # Include all H2S inhibition constants
}


def create_sulfate_reduction_processes():
    """
    Create SRB processes with H2S inhibition using dynamic component indexing.

    Extracts from QSDsan mADM1:
    - Stoichiometry from _madm1.tsv
    - Kinetic parameters from _madm1.py
    - H2S inhibition using non_compet_inhibit()

    Returns:
        Processes object with 3 SRB processes
    """
    # Import component info here to avoid module-level dependency
    from models.components import ADM1_SULFUR_CMPS, SULFUR_COMPONENT_INFO

    if ADM1_SULFUR_CMPS is None or SULFUR_COMPONENT_INFO is None:
        raise RuntimeError("Components not initialized. Call get_qsdsan_components() first.")

    params = SRB_PARAMETERS
    cmps = ADM1_SULFUR_CMPS
    srb_biomass_id, idx_SRB = _resolve_srb_component(cmps)
    i_mass_IS = getattr(cmps, 'S_IS').i_mass

    # Get dynamic component indices from the extended component set
    # CRITICAL: Do not hardcode positions - use dynamic lookup
    idx_h2 = _resolve_component_index(cmps, 'S_h2')
    idx_ac = _resolve_component_index(cmps, 'S_ac')
    idx_SO4 = _resolve_component_index(cmps, 'S_SO4')
    idx_IS = _resolve_component_index(cmps, 'S_IS')

    logger.info("Creating sulfate reduction processes")
    logger.debug(
        "Component indices: H2=%s, Ac=%s, SO4=%s, IS=%s, SRB=%s (%s)",
        idx_h2, idx_ac, idx_SO4, idx_IS, idx_SRB, srb_biomass_id
    )

    # ========================================================================
    # PROCESS 1: H2-utilizing sulfate reduction with H2S inhibition
    # ========================================================================

    # Define rate function with closure-captured indices
    def rate_SRB_h2(state_arr, params):
        """
        H2-utilizing SRB rate with dynamic component indexing.

        Reaction: 4 H2 + SO4²⁻ → HS⁻ + 3 H2O + OH⁻

        Implements from mADM1:
        - Dual-substrate Monod (H2, SO4)
        - H2S non-competitive inhibition
        """
        # Use dynamic indices (captured from closure)
        S_h2 = state_arr[idx_h2]
        S_SO4 = state_arr[idx_SO4]
        S_IS = state_arr[idx_IS]
        X_SRB = state_arr[idx_SRB]

        # Dual-substrate limitation
        f_h2 = substr_inhibit(S_h2, params['K_hSRB'])
        f_so4 = substr_inhibit(S_SO4, params['K_so4_hSRB'])

        # H2S non-competitive inhibition (from mADM1)
        I_h2s = non_compet_inhibit(S_IS, params['KI_h2s_hSRB'])

        rate = params['k_hSRB'] * X_SRB * f_h2 * f_so4 * I_h2s
        return rate

    # Create Process WITHOUT rate_equation (avoids symbolic parsing)
    # Per Codex: Use process.kinetics() to attach rate function
    growth_SRB_h2 = Process(
        'growth_SRB_h2',
        reaction={
            'S_h2': -1.0,                                    # H2 consumption
            'S_SO4': -(1 - params['Y_hSRB']) * i_mass_IS,   # SO4 reduction
            'S_IS': (1 - params['Y_hSRB']),                 # Sulfide production
            srb_biomass_id: params['Y_hSRB'],                      # Biomass growth
        },
        ref_component=srb_biomass_id,
        conserved_for=('COD',),  # Don't specify S - components lack i_S attribute
        parameters=('k_hSRB', 'K_hSRB', 'K_so4_hSRB', 'KI_h2s_hSRB')
    )

    # Attach kinetics AFTER Process creation to bypass symbolic parsing
    growth_SRB_h2.kinetics(
        function=rate_SRB_h2,
        parameters={
            'k_hSRB': params['k_hSRB'],
            'K_hSRB': params['K_hSRB'],
            'K_so4_hSRB': params['K_so4_hSRB'],
            'KI_h2s_hSRB': params['KI_h2s_hSRB'],
        }
    )

    logger.debug("Created growth_SRB_h2 process with dynamic indexing")

    # ========================================================================
    # PROCESS 2: Acetate-utilizing sulfate reduction with H2S inhibition
    # ========================================================================

    # Define rate function with closure-captured indices
    def rate_SRB_ac(state_arr, params):
        """
        Acetate-utilizing SRB rate with dynamic component indexing.

        Reaction: CH3COO⁻ + SO4²⁻ → 2 HCO3⁻ + HS⁻
        """
        # Use dynamic indices (captured from closure)
        S_ac = state_arr[idx_ac]
        S_SO4 = state_arr[idx_SO4]
        S_IS = state_arr[idx_IS]
        X_SRB = state_arr[idx_SRB]

        f_ac = substr_inhibit(S_ac, params['K_aSRB'])
        f_so4 = substr_inhibit(S_SO4, params['K_so4_hSRB'])
        I_h2s = non_compet_inhibit(S_IS, params['KI_h2s_aSRB'])

        rate = params['k_aSRB'] * X_SRB * f_ac * f_so4 * I_h2s
        return rate

    # Create Process WITHOUT rate_equation (avoids symbolic parsing)
    growth_SRB_ac = Process(
        'growth_SRB_ac',
        reaction={
            'S_ac': -1.5,                                    # Acetate consumption
            'S_SO4': -(1 - params['Y_aSRB']) * i_mass_IS,  # SO4 reduction
            'S_IS': (1 - params['Y_aSRB']),                 # Sulfide production
            'S_IC': 0.5,                                     # Inorganic carbon production
            srb_biomass_id: params['Y_aSRB'],                      # Biomass growth
        },
        ref_component=srb_biomass_id,
        conserved_for=('COD',),  # Don't specify S - components lack i_S attribute
        parameters=('k_aSRB', 'K_aSRB', 'K_so4_hSRB', 'KI_h2s_aSRB')
    )

    # Attach kinetics AFTER Process creation
    growth_SRB_ac.kinetics(
        function=rate_SRB_ac,
        parameters={
            'k_aSRB': params['k_aSRB'],
            'K_aSRB': params['K_aSRB'],
            'K_so4_hSRB': params['K_so4_hSRB'],
            'KI_h2s_aSRB': params['KI_h2s_aSRB'],
        }
    )

    logger.debug("Created growth_SRB_ac process with dynamic indexing")

    # ========================================================================
    # PROCESS 3: SRB decay
    # ========================================================================

    # Define rate function with closure-captured index
    def rate_SRB_decay(state_arr, params):
        """SRB decay (first-order) with dynamic component indexing."""
        # Use dynamic index (captured from closure)
        X_SRB = state_arr[idx_SRB]
        return params['k_dec_SRB'] * X_SRB

    # Create Process WITHOUT rate_equation (avoids symbolic parsing)
    decay_SRB = Process(
        'decay_SRB',
        reaction={
            srb_biomass_id: -1.0,
            'X_I': 1.0 - params['f_sI_xb'],  # To particulate inerts
            'S_I': params['f_sI_xb']         # To soluble inerts
        },
        ref_component=srb_biomass_id,
        conserved_for=('COD',),
        parameters=('k_dec_SRB',)
    )

    # Attach kinetics AFTER Process creation
    decay_SRB.kinetics(
        function=rate_SRB_decay,
        parameters={'k_dec_SRB': params['k_dec_SRB']}
    )

    logger.debug("Created decay_SRB process with dynamic indexing")

    processes = Processes([growth_SRB_h2, growth_SRB_ac, decay_SRB])
    logger.info(f"Created {len(processes)} sulfate reduction processes using {srb_biomass_id}")

    # Return both processes and the SRB biomass ID for downstream use
    return processes, srb_biomass_id


def extend_adm1_with_sulfate(base_adm1=None):
    """
    Extend ADM1 with sulfate reduction processes following QSDsan patterns.

    Since ADM1() returns a read-only CompiledProcesses object, we need to:
    1. Create base ADM1 with extended 30-component set
    2. Extract processes from the compiled ADM1
    3. Create new Processes object combining ADM1 + SRB processes
    4. Compile the combined process set

    Args:
        base_adm1: Optional base ADM1 process. If None, creates new one with extended components.

    Returns:
        Extended Processes object with ADM1 + sulfur kinetics (22 ADM1 + 3 SRB processes)
    """
    logger.info("Extending ADM1 with sulfate reduction")

    if base_adm1 is None:
        # BUG #6 FIX: Create ModifiedADM1 with 63-component mADM1 set
        logger.debug("Creating new ModifiedADM1 process with 63-component mADM1 set")
        base_adm1 = ModifiedADM1(components=ADM1_SULFUR_CMPS)

    logger.debug(f"Base ADM1 has {len(base_adm1)} processes")

    # Create SRB processes - now returns tuple (processes, srb_biomass_id)
    sulfate_processes, srb_biomass_id = create_sulfate_reduction_processes()

    # Extract processes from compiled ADM1 (it's read-only, so we need to get the tuple)
    # ADM1 returns a CompiledProcesses object, which has a .tuple attribute
    adm1_process_list = list(base_adm1.tuple)
    # sulfate_processes is a regular Processes object, iterate directly
    srb_process_list = list(sulfate_processes)

    # Create new Processes object combining both
    combined_processes = Processes(adm1_process_list + srb_process_list)

    logger.info(f"Extended ADM1 with {len(sulfate_processes)} SRB processes")
    logger.info(f"Total processes: {len(combined_processes)} (22 ADM1 + 3 SRB)")

    # Compile the combined process set
    combined_processes.compile(to_class=Processes)

    logger.info("ADM1 successfully extended with sulfate reduction")

    return combined_processes


def create_rate_function_with_h2s_inhibition(srb_rate_functions, base_cmps, base_params, base_unit_conv=None):
    """
    Create custom rate function that applies H2S inhibition to methanogens.

    Following Codex recommendation:
    - Keep base ADM1 components (27) separate for _rhos_adm1 calls
    - Use extended state (30) for full model and SRB processes

    Args:
        srb_rate_functions: Tuple of (rate_SRB_h2, rate_SRB_ac, rate_SRB_decay)
        base_cmps: Base ADM1 components (27 components, compiled)
        base_params: Base ADM1 parameters dictionary
        base_unit_conv: Optional cached unit conversion array

    Returns:
        Custom rate function for use with set_rate_function()
    """
    # Import component info here to avoid module-level dependency
    from models.components import ADM1_SULFUR_CMPS

    if ADM1_SULFUR_CMPS is None:
        raise RuntimeError("ADM1_SULFUR_CMPS not initialized. Call get_qsdsan_components() first.")

    # Get component index for S_IS
    idx_IS = _resolve_component_index(ADM1_SULFUR_CMPS, 'S_IS')

    # Methanogen process indices in ADM1 (from inspection)
    IDX_UPTAKE_ACETATE = 10  # Acetoclastic methanogen
    IDX_UPTAKE_H2 = 11       # Hydrogenotrophic methanogen

    # Get H2S inhibition constants
    KI_h2s_ac = H2S_INHIBITION['KI_h2s_ac']
    KI_h2s_h2 = H2S_INHIBITION['KI_h2s_h2']

    # Capture ADM1 component count for state slicing
    adm1_count = len(base_cmps)  # 27

    logger.info(f"Creating custom rate function with H2S inhibition on methanogens")
    logger.debug(f"S_IS index: {idx_IS}, base ADM1 components: {adm1_count}")

    def rhos_adm1_with_h2s_inhibition(state_arr, params):
        """
        Custom rate function: ADM1 (22) + H2S inhibition + SRB (3).

        Following Codex's approach:
        1. Slice state to 27 components for _rhos_adm1 call
        2. Use captured base_cmps and base_params (not the extended 30-component set)
        3. Apply H2S inhibition to methanogens
        4. Append SRB rates using full 30-component state

        Args:
            state_arr: State variable array (30 components)
            params: Parameter dictionary from compiled processes (has 30 components)

        Returns:
            Rate vector (25 processes: 22 ADM1 + 3 SRB)
        """
        # Import here to avoid circular dependency
        from qsdsan.processes._adm1 import _rhos_adm1

        # 1. Get base ADM1 rates (22 processes)
        # Slice state to only ADM1 components (first 27)
        state_base = state_arr[:adm1_count]

        # Use the captured base ADM1 parameters (27 components)
        # CRITICAL: Pass a copy because _rhos_adm1 mutates the dict
        base_params_local = base_params.copy()
        if base_unit_conv is not None:
            base_params_local['unit_conv'] = base_unit_conv

        rhos_base = _rhos_adm1(state_base, base_params_local)

        # 2. Apply H2S inhibition to methanogens
        # Extract S_IS from state
        S_IS = state_arr[idx_IS]

        # Calculate H2S inhibition factors (non-competitive)
        I_h2s_ac = non_compet_inhibit(S_IS, KI_h2s_ac)
        I_h2s_h2 = non_compet_inhibit(S_IS, KI_h2s_h2)

        # Apply inhibition to methanogen rates
        rhos_base[IDX_UPTAKE_ACETATE] *= I_h2s_ac   # Acetoclastic
        rhos_base[IDX_UPTAKE_H2] *= I_h2s_h2         # Hydrogenotrophic

        # 3. Append SRB rates (3 processes)
        rate_h2, rate_ac, rate_decay = srb_rate_functions
        rhos_srb = np.array([
            rate_h2(state_arr, params),
            rate_ac(state_arr, params),
            rate_decay(state_arr, params)
        ])

        # Combine: 22 ADM1 + 3 SRB = 25 total
        return np.concatenate([rhos_base, rhos_srb])

    return rhos_adm1_with_h2s_inhibition


def extend_adm1_with_sulfate_and_inhibition(base_adm1=None):
    """
    Extend ADM1 with sulfate reduction AND apply H2S inhibition to methanogens.

    This is the complete solution that:
    1. Combines ADM1 + SRB processes (structure/stoichiometry)
    2. Sets custom rate function with H2S inhibition (kinetics)

    Following Codex recommendation: Keep base ADM1 components (27) separate for _rhos_adm1 calls,
    while using extended components (30) for the full model.

    Args:
        base_adm1: Optional base ADM1 process. If None, creates new one.

    Returns:
        Extended CompiledProcesses object with custom rate function
    """
    from qsdsan import CompiledProcesses

    logger.info("Extending ADM1 with sulfate reduction and H2S inhibition")

    # CRITICAL: Capture base ADM1 assets for _rhos_adm1 calls
    # _rhos_adm1 expects the original 27-component system
    # Since global thermo is already set to 30 components, we need to extract
    # just the first 27 components and their unit conversion
    from qsdsan import Components
    from qsdsan.processes import mass2mol_conversion
    import numpy as np

    # Import components here to avoid circular dependency and ensure they're loaded
    from models.components import ADM1_SULFUR_CMPS

    if ADM1_SULFUR_CMPS is None:
        raise RuntimeError("ADM1_SULFUR_CMPS not initialized. Call get_qsdsan_components() first.")

    # Get first 27 components from our extended 30-component set
    # Use tuple for slicing, then extract i_mass and chem_MW arrays
    base_cmps_tuple = ADM1_SULFUR_CMPS.tuple[:27]

    # Pre-calculate unit_conversion for 27 components
    # mass2mol_conversion needs i_mass and chem_MW arrays
    base_i_mass = np.array([c.i_mass for c in base_cmps_tuple])
    base_chem_MW = np.array([c.chem_MW for c in base_cmps_tuple])
    base_unit_conv = base_i_mass / base_chem_MW
    logger.debug(f"Pre-calculated unit conversion for {len(base_unit_conv)} base ADM1 components")

    # BUG #6 FIX: Use ModifiedADM1 instead of ADM1 to avoid X_c requirement
    # ModifiedADM1 supports the full 63-component mADM1 set with X_ch/X_pr/X_li
    if base_adm1 is None:
        temp_adm1 = ModifiedADM1(components=ADM1_SULFUR_CMPS)
    else:
        temp_adm1 = base_adm1
    base_params = temp_adm1.rate_function.params.copy()
    base_stoich_params = temp_adm1.parameters.copy()

    # Create a minimal Components-like object for base 27 components
    # _rhos_adm1 needs this to access component properties
    base_cmps = Components(base_cmps_tuple)
    base_params['components'] = base_cmps
    base_params['unit_conv'] = base_unit_conv
    logger.debug(f"Captured base ADM1 parameters with {len(base_cmps)} components")

    if base_adm1 is None:
        # BUG #6 FIX: Create ModifiedADM1 with 63-component mADM1 set
        logger.debug("Creating new ModifiedADM1 process with 63-component mADM1 set")
        base_adm1 = ModifiedADM1(components=ADM1_SULFUR_CMPS)

    # Create SRB processes with their rate functions
    # Returns tuple: (processes, srb_biomass_id)
    sulfate_processes, srb_biomass_id = create_sulfate_reduction_processes()

    # BUG #7 FIX (Part 4): Check if ModifiedADM1 already contains SRB processes
    # ModifiedADM1 ships with sulfur biology (growth_SRB_h2, X_hSRB, etc.)
    # Only add our custom SRB processes if they're not already present
    existing_process_ids = {p.ID for p in base_adm1.tuple}
    logger.debug(f"Existing process IDs in base_adm1: {existing_process_ids}")

    # Filter out SRB processes that already exist
    srb_process_list = [p for p in sulfate_processes if p.ID not in existing_process_ids]

    if len(srb_process_list) < len(sulfate_processes):
        logger.info(f"ModifiedADM1 already contains {len(sulfate_processes) - len(srb_process_list)} SRB processes")
        logger.info(f"Skipping duplicate SRB processes to avoid ID conflicts")
        # If all SRB processes already exist, use base_adm1 as-is
        if len(srb_process_list) == 0:
            logger.info("All SRB processes already in base model - using ModifiedADM1 SRB kinetics")
            processes = base_adm1
            # Don't need custom rate functions if using built-in SRB kinetics
            rate_SRB_h2_func = None
            rate_SRB_ac_func = None
            rate_SRB_decay_func = None
        else:
            # Extract rate functions only for processes we're adding
            rate_SRB_h2_func = sulfate_processes['growth_SRB_h2'].rate_function if 'growth_SRB_h2' not in existing_process_ids else None
            rate_SRB_ac_func = sulfate_processes['growth_SRB_ac'].rate_function if 'growth_SRB_ac' not in existing_process_ids else None
            rate_SRB_decay_func = sulfate_processes['decay_SRB'].rate_function if 'decay_SRB' not in existing_process_ids else None
    else:
        # Extract SRB rate functions for custom wrapper
        # These were created in create_sulfate_reduction_processes() with closures
        # Need to access by ID, not index
        rate_SRB_h2_func = sulfate_processes['growth_SRB_h2'].rate_function
        rate_SRB_ac_func = sulfate_processes['growth_SRB_ac'].rate_function
        rate_SRB_decay_func = sulfate_processes['decay_SRB'].rate_function

    # Per mADM1 reference (qsdsan_madm1.py:618): Set class attributes BEFORE compilation
    # Extend biogas tracking to include H2S (S_IS component) - only if not already present
    if 'S_IS' not in base_adm1._biogas_IDs:
        ADM1_Sulfur._biogas_IDs = (*base_adm1._biogas_IDs, 'S_IS')
    else:
        ADM1_Sulfur._biogas_IDs = base_adm1._biogas_IDs

    # BUG #7 FIX (Part 4): Only append srb_biomass_id if not already present
    if srb_biomass_id not in base_adm1._biomass_IDs:
        ADM1_Sulfur._biomass_IDs = (*base_adm1._biomass_IDs, srb_biomass_id)
        logger.debug(f"Appended {srb_biomass_id} to biomass IDs")
    else:
        ADM1_Sulfur._biomass_IDs = base_adm1._biomass_IDs
        logger.debug(f"{srb_biomass_id} already in biomass IDs - using existing")

    ADM1_Sulfur._acid_base_pairs = base_adm1._acid_base_pairs
    ADM1_Sulfur._stoichio_params = (*base_adm1._stoichio_params,)
    # Kinetic params: inherit ALL from ADM1 (including 'root') + add 'KIs_h2s'
    # Insert KIs_h2s AFTER KIs_h2 (index 6) but BEFORE the rest
    ADM1_Sulfur._kinetic_params = (*base_adm1._kinetic_params[:7],
                                    'KIs_h2s',
                                    *base_adm1._kinetic_params[7:])

    logger.info(f"Set ADM1_Sulfur class attributes:")
    logger.info(f"  Biogas IDs (includes H2S): {ADM1_Sulfur._biogas_IDs}")
    logger.info(f"  Biomass IDs: {ADM1_Sulfur._biomass_IDs}")

    # Combine ADM1 + SRB processes (create Processes object without compiling yet)
    # Per mADM1 reference (qsdsan_madm1.py:668-728): Modify BEFORE calling compile()
    # BUG #7 FIX (Part 4): Only add non-duplicate SRB processes
    if len(srb_process_list) == 0:
        # All SRB processes already exist - use base_adm1 as-is
        processes = base_adm1
        logger.info("Using base ModifiedADM1 processes (no new SRB processes to add)")
        # No need to compile - already compiled
        compile_needed = False
    else:
        adm1_process_list = list(base_adm1.tuple)
        processes = Processes(adm1_process_list + srb_process_list)
        logger.info(f"Combined {len(adm1_process_list)} ADM1 + {len(srb_process_list)} new SRB processes")
        compile_needed = True

    # Now compile to ADM1_Sulfur class (only if we created a new Processes object)
    # Per mADM1 reference: Use Processes.compile(to_class=cls) pattern
    if compile_needed:
        processes.compile(to_class=ADM1_Sulfur)
        logger.debug(f"Compiled to {type(processes).__name__}")
    else:
        logger.debug(f"Using pre-compiled {type(processes).__name__}")

    logger.debug(f"Processes object _biogas_IDs: {processes._biogas_IDs}")

    # Create custom rate function with H2S inhibition (only if we have custom SRB processes)
    # Pass the captured base ADM1 components and parameters for _rhos_adm1 calls
    # BUG #7 FIX (Part 4): Skip custom rate function if using built-in SRB kinetics
    if rate_SRB_h2_func is not None:
        custom_rate_func = create_rate_function_with_h2s_inhibition(
            srb_rate_functions=(rate_SRB_h2_func, rate_SRB_ac_func, rate_SRB_decay_func),
            base_cmps=base_cmps,
            base_params=base_params,
            base_unit_conv=base_unit_conv
        )

        # Set the custom rate function on the compiled process
        processes.set_rate_function(custom_rate_func)
        logger.info("Applied custom rate function with H2S inhibition")
    else:
        logger.info("Using ModifiedADM1 built-in SRB kinetics (no custom rate function)")

    # Per mADM1 reference (qsdsan_madm1.py:776-786): Set parameters using the proper pattern
    # 1. Extract stoichiometric parameter values from base_params
    try:
        stoichio_vals = tuple(base_stoich_params[p] for p in ADM1_Sulfur._stoichio_params)
    except KeyError as err:
        missing_key = err.args[0]
        raise KeyError(f"Missing stoichiometric parameter '{missing_key}' in base ADM1 parameters.") from err

    # 2. Prepare kinetic parameter values (matching the order in _kinetic_params)
    # ADM1_Sulfur._kinetic_params includes 'root' at the end (inherited from ADM1)
    # We need to provide all ADM1 params + KIs_h2s in the right order
    kinetic_vals = [
        base_params.get('rate_constants'),
        base_params.get('half_sat_coeffs'),
        base_params.get('pH_ULs'),
        base_params.get('pH_LLs'),
        base_params.get('KS_IN'),
        base_params.get('KI_nh3'),
        base_params.get('KIs_h2'),
        # H2S inhibition coefficient (from our SRB_PARAMETERS)
        H2S_INHIBITION['KI_h2s_ac'],  # Use acetoclastic methanogen KI as representative
        # Remaining ADM1 kinetic params (Ka through components)
        base_params.get('Ka_base'),
        base_params.get('Ka_dH'),
        base_params.get('K_H_base'),
        base_params.get('K_H_dH'),
        base_params.get('kLa'),
        base_params.get('T_base'),
        ADM1_SULFUR_CMPS,  # components
        base_params.get('root')  # TempState for intermediate calculations
    ]

    # 3. Set _parameters on the CompiledProcesses object (stoichiometric)
    processes.__dict__['_parameters'] = dict(zip(ADM1_Sulfur._stoichio_params, stoichio_vals))

    # 4. Set _params on the rate function (kinetic)
    processes.rate_function._params = dict(zip(ADM1_Sulfur._kinetic_params, kinetic_vals))

    logger.debug(f"Set {len(stoichio_vals)} stoichiometric parameters on processes object")
    logger.debug(f"Set {len(kinetic_vals)} kinetic parameters on rate function")

    logger.info("Custom rate function set with H2S inhibition on methanogens")
    logger.info(f"Final model: 25 processes (22 ADM1 + 3 SRB) with H2S inhibition")

    return processes


def get_h2s_inhibition_factors(S_IS_kg_m3: float) -> dict:
    """
    Calculate H2S inhibition factors for reporting.

    Useful for validation and diagnostics.

    Args:
        S_IS_kg_m3: Sulfide concentration (kg S/m³)

    Returns:
        Dictionary with inhibition factors (0-1, where 1=no inhibition)

    Example:
        >>> factors = get_h2s_inhibition_factors(0.05)  # 50 mg S/L
        >>> print(f"Methanogens: {factors['acetoclastic_methanogens']*100:.0f}% activity")
    """
    return {
        'acetoclastic_methanogens': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_ac']
        ),
        'hydrogenotrophic_methanogens': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_h2']
        ),
        'propionate_degraders': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_pro']
        ),
        'butyrate_degraders': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_c4']
        ),
        'acetate_utilizing_SRB': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_aSRB']
        ),
        'h2_utilizing_SRB': non_compet_inhibit(
            S_IS_kg_m3, H2S_INHIBITION['KI_h2s_hSRB']
        ),
    }


def get_kinetic_parameters():
    """
    Get all SRB kinetic parameters for reference.

    Returns:
        Dictionary with all kinetic parameters
    """
    return SRB_PARAMETERS.copy()


def get_h2s_inhibition_constants():
    """
    Get H2S inhibition constants for reference.

    Returns:
        Dictionary with inhibition constants (kg COD/m³)
    """
    return H2S_INHIBITION.copy()


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)

    print("=== QSDsan Sulfur Kinetics Module Test ===\n")

    # 1. Create processes
    print("1. Creating sulfate reduction processes:")
    try:
        processes, srb_id = create_sulfate_reduction_processes()
        print(f"   [OK] Created {len(processes)} processes using {srb_id}")
        for p in processes:
            print(f"      - {p.ID}")
    except Exception as e:
        print(f"   [ERROR] {e}")
    print()

    # 2. H2S inhibition factors
    print("2. H2S Inhibition Factors at Different Sulfide Concentrations:")
    print(f"   {'S_IS (mg/L)':<15} {'Acetoclastic':<15} {'Hydrogenotrophic':<18} {'Status'}")
    print("   " + "-"*65)

    for S_IS_mg_l in [10, 25, 50, 75, 100]:
        S_IS_kg_m3 = S_IS_mg_l / 1000
        factors = get_h2s_inhibition_factors(S_IS_kg_m3)
        status = "OK" if factors['acetoclastic_methanogens'] > 0.7 else \
                 "Moderate" if factors['acetoclastic_methanogens'] > 0.5 else "Severe"
        print(f"   {S_IS_mg_l:<15} {factors['acetoclastic_methanogens']:<15.2f} "
              f"{factors['hydrogenotrophic_methanogens']:<18.2f} {status}")
    print()

    # 3. Kinetic parameters
    print("3. SRB Kinetic Parameters:")
    params = get_kinetic_parameters()
    print(f"   H2-utilizing SRB:")
    print(f"      k_max = {params['k_hSRB']:.2f} d^-1")
    print(f"      K_H2 = {params['K_hSRB']:.2e} kg COD/m^3")
    print(f"      Y = {params['Y_hSRB']:.3f}")
    print(f"   Acetate-utilizing SRB:")
    print(f"      k_max = {params['k_aSRB']:.2f} d^-1")
    print(f"      K_ac = {params['K_aSRB']:.3f} kg COD/m^3")
    print(f"      Y = {params['Y_aSRB']:.3f}")
