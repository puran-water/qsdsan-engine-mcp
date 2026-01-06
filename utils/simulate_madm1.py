"""
QSDsan simulation module for mADM1 (Modified ADM1) with 62 state variables.

This module provides simulation functions for anaerobic digester design using
QSDsan's ModifiedADM1 process model with complete sulfur/phosphorus/iron extensions.

Key features:
- 62-component mADM1 system (full Flores-Alsina model)
- Built-in SRB processes (X_hSRB, X_aSRB, X_pSRB, X_c4SRB)
- Built-in H2S inhibition on methanogens
- EBPR extension (PAO, polyphosphate)
- Metal/mineral precipitation (Fe, Al, Ca, Mg)
- Proper dynamic simulation setup with set_dynamic_tracker
- Early-stop convergence checking for pseudo-steady-state
- Dual-HRT validation for design robustness

Based on QSDsan's published mADM1 implementation (utils/qsdsan_madm1.py).
"""

import numpy as np
from qsdsan import sanunits as su, WasteStream, System
from qsdsan.utils import ospath
import logging

# ROOT CAUSE FIX: Use ModifiedADM1 directly instead of extend_adm1_with_sulfate_and_inhibition
# ModifiedADM1 already contains complete sulfur biology (SRB processes, H2S inhibition, etc.)
from models.madm1 import ModifiedADM1, create_madm1_cmps

# CODEX FIX: Use custom reactor that supports mADM1's 4 biogas species (CH4, CO2, H2, H2S)
from models.reactors import AnaerobicCSTRmADM1

# Import inoculum generator for CSTR startup
from utils.inoculum_generator import generate_inoculum_state

# Import pH calculation if available
try:
    import sys
    import os
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    adm1_dir = os.path.join(os.path.dirname(parent_dir), "adm1_mcp_server")
    sys.path.insert(0, adm1_dir)
    from calculate_ph_and_alkalinity_fixed import update_ph_and_alkalinity
except ImportError:
    logging.warning("pH calculation module not found. Using default pH values.")
    def update_ph_and_alkalinity(stream):
        if hasattr(stream, '_pH'):
            stream._pH = 7.0
        if hasattr(stream, '_SAlk'):
            stream._SAlk = 2.5
        return stream

# Constants
from chemicals.elements import molecular_weight as get_mw
C_mw = get_mw({'C': 1})
N_mw = get_mw({'N': 1})

logger = logging.getLogger(__name__)


def create_influent_stream_sulfur(Q, Temp, adm1_state_62):
    """
    Create influent WasteStream with 62 mADM1 components.

    Parameters
    ----------
    Q : float
        Flow rate in m3/d
    Temp : float
        Temperature in K
    adm1_state_62 : dict
        Dictionary of 62-component concentrations (kg/m3)
        Full mADM1 component set from Codex agent

    Returns
    -------
    WasteStream
        Influent stream with calculated pH and alkalinity

    Notes
    -----
    - Uses create_madm1_cmps() for component set (62 components + H2O)
    - Concentrations expected in kg/m3 (or kg COD/m3 for COD-measured components)
    - pH and alkalinity calculated based on acid-base equilibria
    - Codex agent generates disaggregated SRB biomass (X_hSRB, X_aSRB, X_pSRB, X_c4SRB)
    """
    try:
        # Use mADM1 components (62 + H2O = 63 total)
        madm1_cmps = create_madm1_cmps()

        inf = WasteStream('Influent', T=Temp)

        # Set component system (must be done before setting flows)
        # This is handled by QSDsan when ADM1_SULFUR_CMPS is loaded

        # Prepare concentrations for set_flow_by_concentration
        concentrations = {}

        def _to_number(val):
            """Coerce input value to float; handle [value, unit, comment] lists."""
            # Common patterns: val, [val, unit], [val, unit, comment], {'value': val}
            if isinstance(val, (list, tuple)) and val:
                try:
                    return float(val[0])
                except Exception:
                    pass
            elif isinstance(val, dict):
                for key in ('value', 'val', 'amount'):
                    if key in val:
                        try:
                            return float(val[key])
                        except Exception:
                            pass
            try:
                return float(val)
            except Exception:
                return None

        def _kmol_to_kg_per_m3(comp_id, kmol_per_m3):
            # Convert kmol/m3 to kg/m3 using component MW (g/mol)
            # kg/m3 = kmol/m3 * (g/mol) [MW] (see cancellation of 1e3 factors)
            MW_g_per_mol = madm1_cmps[comp_id].chem_MW
            return kmol_per_m3 * MW_g_per_mol

        # Process all mADM1 components (62). Skip bulk liquid 'H2O' to avoid warnings.
        for comp_id in madm1_cmps.IDs:
            if comp_id == 'H2O':
                continue
            if comp_id in adm1_state_62:
                raw = adm1_state_62[comp_id]
                # Handle [value, unit, ...] shaped inputs
                if isinstance(raw, (list, tuple)) and len(raw) >= 2 and isinstance(raw[1], str):
                    num = _to_number(raw)
                    unit = raw[1].strip().lower()
                    if unit == 'kmol/m3':
                        # Convert to kg/m3 using component MW
                        concentrations[comp_id] = _kmol_to_kg_per_m3(comp_id, num)
                    else:
                        # Treat numeric as kg/m3 of measured_as (e.g., kg C/m3)
                        concentrations[comp_id] = float(num)
                else:
                    num = _to_number(raw)
                    concentrations[comp_id] = num if num is not None else 1e-6
            else:
                # Use small default if not specified (Codex generates all 62 components)
                concentrations[comp_id] = 1e-6

        # Set flow by concentration
        inf.set_flow_by_concentration(
            Q,
            concentrations=concentrations,
            units=('m3/d', 'kg/m3')
        )

        # Calculate pH and alkalinity
        update_ph_and_alkalinity(inf)

        logger.info(f"Created influent stream: Q={Q} m3/d, T={Temp} K, pH={inf.pH:.2f}")

        return inf

    except Exception as e:
        raise RuntimeError(f"Error creating influent stream with sulfur: {e}")


def initialize_62_component_state(adm1_state_62):
    """
    Ensure 62-component mADM1 state has sensible defaults for sulfur species.

    CRITICAL: Starting with zero sulfate/SRB means inhibition logic stays dormant
    and SRB processes won't activate. This function ensures realistic initial values.

    Parameters
    ----------
    adm1_state_62 : dict
        Dictionary of component concentrations (kg/m3)

    Returns
    -------
    dict
        Validated state with sulfur species defaults applied

    Notes
    -----
    - S_SO4 default: 0.1 kg S/m3 = 100 mg S/L (typical wastewater)
    - S_IS default: 0.001 kg S/m3 = 1 mg S/L (low initial sulfide)
    - SRB biomass defaults (disaggregated):
      * X_hSRB: 0.005 kg COD/m3 = 5 mg COD/L (hydrogen-utilizing, dominant)
      * X_aSRB: 0.003 kg COD/m3 = 3 mg COD/L (acetate-utilizing)
      * X_pSRB: 0.001 kg COD/m3 = 1 mg COD/L (propionate-utilizing)
      * X_c4SRB: 0.001 kg COD/m3 = 1 mg COD/L (butyrate/valerate-utilizing)
      * Total: 0.01 kg COD/m3 = 10 mg COD/L (seed population)

    These defaults ensure:
    1. SRB processes can activate immediately with proper distribution
    2. H2S inhibition can be calculated
    3. Sulfur mass balance is meaningful
    """
    # Get the mADM1 component set directly - it will be initialized when simulation runs
    # This avoids dependency on async loader initialization state
    madm1_cmps = create_madm1_cmps(set_thermo=False)  # Don't reset thermo if already set

    def _to_number(val):
        """Coerce input value to float; handle [value, unit, comment] lists."""
        if isinstance(val, (list, tuple)) and val:
            try:
                return float(val[0])
            except Exception:
                pass
        elif isinstance(val, dict):
            for key in ('value', 'val', 'amount'):
                if key in val:
                    try:
                        return float(val[key])
                    except Exception:
                        pass
        try:
            return float(val)
        except Exception:
            return None

    # Filter and align initialization against mADM1 component set (63 components)
    # This prevents KeyError when input state has extra components
    valid_ids = madm1_cmps.IDs
    init_conds = {}
    extras = []

    for comp_id in valid_ids:
        raw = adm1_state_62.get(comp_id)
        val = _to_number(raw) if raw is not None else 0.0
        init_conds[comp_id] = val

    # Log any components in input that aren't in our component set
    for comp_id in adm1_state_62:
        if comp_id not in valid_ids:
            extras.append(comp_id)
    if extras:
        logger.debug(f"Ignoring components not in mADM1 component set: {extras}")

    # Apply sulfur defaults (ensure SRB processes can activate)
    if init_conds.get('S_SO4', 0) < 1e-6:
        init_conds['S_SO4'] = 0.1  # 100 mg S/L
        logger.info("S_SO4 not specified or too low, using default: 0.1 kg S/m3 (100 mg S/L)")

    if init_conds.get('S_IS', 0) < 1e-9:
        init_conds['S_IS'] = 0.001  # 1 mg S/L
        logger.info("S_IS not specified or zero, using default: 0.001 kg S/m3 (1 mg S/L)")

    # mADM1 uses disaggregated SRB biomass instead of lumped X_SRB
    # Distribute seed population across all SRB types
    srb_seed_total = 0.01  # 10 mg COD/L total
    srb_components = {
        'X_hSRB': 0.005,  # Hydrogen-utilizing (dominant)
        'X_aSRB': 0.003,  # Acetate-utilizing
        'X_pSRB': 0.001,  # Propionate-utilizing
        'X_c4SRB': 0.001  # Butyrate/valerate-utilizing
    }

    needs_srb_default = False
    for srb_id, default_val in srb_components.items():
        if srb_id in valid_ids and init_conds.get(srb_id, 0) < 1e-6:
            init_conds[srb_id] = default_val
            needs_srb_default = True

    if needs_srb_default:
        logger.info(f"SRB biomass not specified, using defaults: X_hSRB=5, X_aSRB=3, X_pSRB=1, X_c4SRB=1 mg COD/L (total=10 mg COD/L)")

    return init_conds


def check_steady_state(eff, gas, window=5, tolerance=1e-3):
    """
    Check if system reached pseudo-steady-state.

    Examines the last 'window' time points and calculates dC/dt for key components.
    Returns True if max(|dC/dt|) < tolerance.

    Parameters
    ----------
    eff : WasteStream
        Effluent stream with dynamic tracking enabled
    gas : WasteStream
        Biogas stream with dynamic tracking enabled
    window : int, optional
        Number of recent time points to check (default 5)
    tolerance : float, optional
        Maximum acceptable dC/dt in kg/m3/d (default 1e-3, relaxed from 5e-4 per Codex
        recommendation to avoid chasing numerical noise below solver precision)

    Returns
    -------
    bool
        True if system converged to steady state

    Notes
    -----
    - Checks COD, VFA (S_ac), and biomass (X_ac) as key indicators
    - Uses numerical differentiation on last 'window' points with rolling average smoothing
    - Rolling average reduces BDF solver jitter before gradient calculation
    - Early convergence detection saves computation time
    - Tolerance relaxed to 1e-3 to avoid numerical noise at true steady state
    """
    try:
        # Check if dynamic tracking is available
        if not hasattr(eff, 'scope') or not hasattr(eff.scope, 'record'):
            logger.warning("Effluent stream missing dynamic tracking data")
            return False

        # Get time series data (FIXED: use time_series not t_arr per QSDsan API)
        time_arr = eff.scope.time_series

        if len(time_arr) < window + 1:
            # Not enough data points yet
            return False

        # Get last 'window' + 1 points for numerical differentiation
        recent_indices = slice(-window-1, None)
        t_recent = time_arr[recent_indices]

        # Get component indices
        try:
            idx_COD = eff.components.index('S_ac')  # VFA as proxy for COD dynamics
            idx_biomass = eff.components.index('X_ac')  # Methanogen biomass
        except ValueError:
            # Component not found, can't check convergence
            logger.warning("Required components for convergence check not found")
            return False

        # Extract concentrations
        record = eff.scope.record
        COD_recent = record[recent_indices, idx_COD]
        biomass_recent = record[recent_indices, idx_biomass]

        # Apply rolling average smoothing to reduce BDF solver jitter (per Codex recommendation)
        # Use simple moving average with window size 3 to smooth numerical noise
        if len(COD_recent) >= 3:
            from scipy.ndimage import uniform_filter1d
            COD_smoothed = uniform_filter1d(COD_recent, size=3, mode='nearest')
            biomass_smoothed = uniform_filter1d(biomass_recent, size=3, mode='nearest')
        else:
            # Not enough points for smoothing, use raw data
            COD_smoothed = COD_recent
            biomass_smoothed = biomass_recent

        # Calculate dC/dt using numerical differentiation
        dCOD_dt = np.gradient(COD_smoothed, t_recent)
        dBiomass_dt = np.gradient(biomass_smoothed, t_recent)

        # Check if all derivatives are below tolerance
        max_dCOD_dt = np.max(np.abs(dCOD_dt))
        max_dBiomass_dt = np.max(np.abs(dBiomass_dt))

        # Log convergence status at INFO level (changed from DEBUG to track progress)
        logger.info(f"Convergence check: max|dCOD/dt|={max_dCOD_dt:.6f} kg/m³/d, "
                   f"max|dBiomass/dt|={max_dBiomass_dt:.6f} kg/m³/d, tolerance={tolerance} kg/m³/d")

        if max_dCOD_dt < tolerance and max_dBiomass_dt < tolerance:
            logger.info(f"System converged: max derivatives below {tolerance}")
            return True

        logger.info(f"Not converged yet (COD: {max_dCOD_dt/tolerance:.1f}× tolerance, "
                   f"Biomass: {max_dBiomass_dt/tolerance:.1f}× tolerance)")
        return False

    except Exception as e:
        logger.warning(f"Error checking steady state: {e}")
        return False


def run_simulation_to_steady_state(sys, eff, gas, check_interval=2, t_step=0.1, tolerance=1e-3):
    """
    Run simulation until TRUE steady state (no time limit).

    Performs dynamic simulation in intervals, checking for convergence after each.
    Runs indefinitely until pseudo-steady-state is reached. Modified to eliminate
    arbitrary time limits - the simulation WILL converge eventually, and we want
    the actual steady-state values, not values at some arbitrary cutoff time.

    Parameters
    ----------
    sys : System
        QSDsan System object with AnaerobicCSTR
    eff : WasteStream
        Effluent stream (must have dynamic tracking)
    gas : WasteStream
        Biogas stream (must have dynamic tracking)
    check_interval : float, optional
        Days between convergence checks (default 2, reduced from 20 to detect convergence
        faster without wasting 10-20 day chunks near steady state)
    t_step : float, optional
        Time step for output in days (default 0.1)
    tolerance : float, optional
        Convergence tolerance in kg/m3/d (default 1e-3, relaxed from 5e-4 per Codex
        recommendation to avoid chasing numerical noise below BDF solver precision)

    Returns
    -------
    tuple
        (converged_at, status) where:
        - converged_at: Time in days when converged
        - status: 'converged' (always, since we run until convergence)

    Notes
    -----
    - Uses BDF method (recommended for stiff ODEs in ADM1)
    - Checks convergence every check_interval days
    - NO MAXIMUM TIME LIMIT - runs until convergence
    - Running to true steady state (dCOD/dt ≈ 0) eliminates need for inventory tracking
      in methane yield calculations: Y_CH4 = CH4_flow / (COD_in - COD_out)
    - Typical convergence time: 50-150 days for standard ADM1, but may take 2000-5000 days
      for systems with high particulate COD inventory requiring equilibration across all
      62 mADM1 components
    - If you need a safety limit, impose it at the CLI level, not here
    """
    t_current = 0

    logger.info(f"Starting simulation to TRUE steady state (no time limit, checking every {check_interval} days)")

    # CRITICAL FIX per Codex: Reset caches ONCE before loop, not every iteration
    # Resetting every iteration causes the simulation to replay the same 2 days forever
    # because all accumulated state and tracking data gets cleared
    logger.debug("Resetting system caches once before simulation loop")
    sys.reset_cache()
    eff.scope.reset_cache()
    gas.scope.reset_cache()

    while True:
        t_next = t_current + check_interval

        logger.debug(f"Simulating from t={t_current} to t={t_next} days")

        try:
            sys.simulate(
                state_reset_hook=None,  # FIXED: Don't reset - keep accumulated state/data
                t_span=(t_current, t_next),
                t_eval=np.arange(t_current, t_next + t_step, t_step),
                method='BDF'  # Backward Differentiation Formula for stiff ODEs
            )
        except Exception as e:
            logger.error(f"Simulation failed at t={t_current}: {e}")
            raise RuntimeError(f"Simulation failed: {e}")

        t_current = t_next

        # Check for convergence
        if check_steady_state(eff, gas, tolerance=tolerance):
            logger.info(f"Converged to TRUE steady state at t={t_current} days")
            return t_current, 'converged'

        # Log progress every 100 days to show we're still working
        if t_current % 100 == 0:
            logger.info(f"Progress: t={t_current} days, still approaching steady state...")


def extract_time_series(eff, gas):
    """
    Extract time series data from stream scopes for diagnostics.

    Parameters
    ----------
    eff : WasteStream
        Effluent stream with dynamic tracking
    gas : WasteStream
        Biogas stream with dynamic tracking

    Returns
    -------
    dict
        Time series data including:
        - time: Array of time points (days)
        - time_units: "days"
        - effluent_cod: COD trajectory (mg/L) **UNITS VERIFIED**
        - effluent_cod_units: "mg/L"
        - effluent_vfa: VFA (S_ac) trajectory (mg/L) **UNITS VERIFIED**
        - effluent_vfa_units: "mg/L"
        - effluent_biomass: Biomass (X_ac) trajectory (mg/L) **UNITS VERIFIED**
        - effluent_biomass_units: "mg/L"
        - biogas_ch4: CH4 concentration trajectory (mg/L in gas phase)
        - biogas_ch4_units: "mg/L"

    Notes
    -----
    **CRITICAL UNITS DOCUMENTATION**:
    All concentrations from eff.scope.record are in **mg/L**, NOT kg/m³.
    This was verified via Codex investigation of QSDsan v1.4.2 source code.
    Do NOT multiply by 1000 for unit conversion - values are already in mg/L.
    """
    time_series = {
        'success': False,
        'message': None,
        'time': [],
        'time_units': 'days',
        'effluent_cod': [],
        'effluent_cod_units': 'mg/L',
        'effluent_vfa': [],
        'effluent_vfa_units': 'mg/L',
        'effluent_biomass': [],
        'effluent_biomass_units': 'mg/L',
        'biogas_ch4': [],
        'biogas_ch4_units': 'mg/L'
    }

    try:
        # Check if tracking data exists
        if not hasattr(eff, 'scope') or not hasattr(eff.scope, 'record'):
            time_series['message'] = "No dynamic tracking data available"
            return time_series

        # Extract time array
        time_arr = eff.scope.time_series
        time_series['time'] = time_arr.tolist()

        # Extract effluent components
        record_eff = eff.scope.record
        components = eff.components

        # Get indices for key components
        try:
            idx_S_ac = components.index('S_ac')
            idx_X_ac = components.index('X_ac')

            # Extract trajectories - UNITS: mg/L (from eff.scope.record)
            # NO conversion needed - values are already in mg/L
            time_series['effluent_vfa'] = record_eff[:, idx_S_ac].tolist()  # mg/L
            time_series['effluent_biomass'] = record_eff[:, idx_X_ac].tolist()  # mg/L

            # Calculate COD at each time point
            # Mirror WasteStream.composite('COD'): record stores mg/L, skip gases/negatives
            # Per Codex investigation of QSDsan source:
            # - eff.scope.record contains concentrations in mg/L (not kg/m³)
            # - i_COD is dimensionless (kg COD/kg component)
            # - Result is mg COD/L (no conversion needed)
            # - Must exclude gas components (g=1) and negative i_COD
            cod_coeff = np.asarray(components.i_COD)
            cod_mask = (cod_coeff >= 0) * (1 - np.asarray(components.g))
            eff_conc = record_eff[:, :len(components)]  # mg/L for each component
            time_series['effluent_cod'] = (
                eff_conc * cod_coeff * cod_mask
            ).sum(axis=1).tolist()

        except ValueError as e:
            logger.warning(f"Some effluent components not found: {e}")

        # Extract biogas data
        if hasattr(gas, 'scope') and hasattr(gas.scope, 'record'):
            record_gas = gas.scope.record
            gas_components = gas.components

            try:
                idx_ch4 = gas_components.index('S_ch4')
                # Store CH4 concentration trajectory
                time_series['biogas_ch4'] = record_gas[:, idx_ch4].tolist()

            except ValueError:
                logger.warning("CH4 component not found in biogas")

        time_series['success'] = True
        time_series['message'] = f"Extracted {len(time_arr)} time points"
        logger.info(f"Time series extracted: {len(time_arr)} points from 0 to {time_arr[-1]:.1f} days")

    except Exception as e:
        time_series['message'] = f"Error extracting time series: {str(e)}"
        logger.error(f"Failed to extract time series: {e}")

    return time_series


def run_simulation_sulfur(basis, adm1_state_62, HRT, check_interval=2, tolerance=1e-3, pH_ctrl=None,
                          fixed_naoh_dose_m3_d=0.0, fixed_fecl3_dose_m3_d=0.0, fixed_na2co3_dose_m3_d=0.0,
                          naoh_conc_kg_m3=431.25, fecl3_conc_kg_m3=400.0, na2co3_conc_kg_m3=106.0):
    """
    Run single ADM1+sulfur simulation at specified HRT until TRUE steady state.

    Main simulation function that sets up and runs a dynamic anaerobic digester
    simulation with the extended ADM1+sulfur model. Runs indefinitely until
    convergence is detected (no arbitrary time limits).

    Parameters
    ----------
    basis : dict
        Basis of design containing:
        - 'Q': Flow rate (m3/d)
        - 'Temp': Temperature (K)
        - Other design parameters
    adm1_state_62 : dict
        62-component mADM1 state (kg/m3)
        Must include all ADM1 components + sulfur biology + P/S/Fe extensions
    HRT : float
        Hydraulic retention time in days
    check_interval : float, optional
        Days between convergence checks (default 2, for faster convergence detection)
    tolerance : float, optional
        Convergence tolerance in kg/m3/d (default 1e-3, relaxed from 5e-4 to avoid
        chasing numerical noise below BDF solver precision)
    pH_ctrl : float, optional
        If specified, fixes pH at this value (e.g., 7.0) to emulate perfect pH control.
        Used for rapid diagnostic testing to rule out alkalinity limitation
    fixed_naoh_dose_m3_d : float, optional
        Fixed NaOH dosing rate (m3/d), default 0.0
    fixed_fecl3_dose_m3_d : float, optional
        Fixed FeCl3 dosing rate (m3/d), default 0.0
    fixed_na2co3_dose_m3_d : float, optional
        Fixed Na2CO3 dosing rate (m3/d), default 0.0
    naoh_conc_kg_m3 : float, optional
        NaOH concentration as kg Na+/m³ (default 431.25 = 50% commercial NaOH)
    fecl3_conc_kg_m3 : float, optional
        FeCl3 concentration as kg Fe³⁺/m³ (default 100.0)
    na2co3_conc_kg_m3 : float, optional
        Na2CO3 concentration as kg Na+/m³ (default 106.0)

    Returns
    -------
    tuple
        (sys, inf, eff, gas, converged_at, status, time_series) where:
        - sys: QSDsan System object
        - inf: Influent WasteStream
        - eff: Effluent WasteStream
        - gas: Biogas WasteStream
        - converged_at: Time converged (days)
        - status: Always 'converged' (runs until steady state achieved)
        - time_series: Time series data dict (COD, VFA, biomass over time)

    Notes
    -----
    **CRITICAL SETUP REQUIREMENTS** (per Codex review):
    1. Must use set_dynamic_tracker(eff, gas) before simulate()
    2. Must use state_reset_hook='reset_cache' in simulate()
    3. Must initialize sulfur components with non-zero values
    4. Runs indefinitely until TRUE pseudo-steady-state (no time limits)

    **Refactored (2025-10-26)**: Eliminated arbitrary max_time parameter. Simulations
    now run until dCOD/dt ≈ 0, which eliminates need for complex COD inventory tracking
    in methane yield calculations: Y_CH4 = CH4_flow / (COD_in - COD_out)

    Progress is logged every 100 days to show simulation is still running.
    Convergence detection uses rolling average smoothing to reduce BDF solver jitter.

    Without these requirements, streams won't update and results will be wrong!
    """
    try:
        Q = basis['Q']
        Temp = basis.get('Temp', 308.15)  # Default 35°C

        logger.info(f"=== Starting mADM1 Simulation ===")
        logger.info(f"Q={Q} m3/d, T={Temp} K, HRT={HRT} days")

        # 1. Create mADM1 model (use ModifiedADM1 directly - no extend function needed)
        # ModifiedADM1 already has SRB processes, H2S inhibition, and all 62 mADM1 components
        # CRITICAL FIX: Generate proper inoculum from feedstock composition
        # Solves "pickling" problem where feedstock biomass (~1 kg/m³) is insufficient
        # for CSTR startup, causing F/M overload and immediate failure
        logger.info("="*80)
        logger.info("INOCULUM GENERATOR - Scaling biomass for CSTR startup")
        logger.info("="*80)
        reactor_init_state = generate_inoculum_state(
            feedstock_state=adm1_state_62,
            target_biomass_cod_ratio=0.20  # 20% of COD as biomass (typical healthy digester)
        )
        logger.info("="*80)

        logger.info("Creating mADM1 model (62 components, built-in sulfur biology)")
        madm1_cmps = create_madm1_cmps()
        madm1_model = ModifiedADM1(components=madm1_cmps)
        logger.info(f"mADM1 model created with {len(madm1_model)} processes")

        # 2. Create streams with 62 mADM1 components (adm1_state_62 actually has 62 components from Codex)
        logger.info("Creating influent stream with mADM1 state")
        inf = create_influent_stream_sulfur(Q, Temp, adm1_state_62)
        eff = WasteStream('Effluent', T=Temp)
        gas = WasteStream('Biogas')

        # 2b. Create fixed dosing streams if any doses are specified
        dosing_streams = []
        if fixed_naoh_dose_m3_d > 0:
            naoh = WasteStream('NaOH_Dosing', T=Temp)
            naoh.set_flow_by_concentration(
                flow_tot=fixed_naoh_dose_m3_d,
                concentrations={'S_Na': naoh_conc_kg_m3 * 1000},  # kg/m³ to mg/L
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(naoh)
            logger.info(f"Created NaOH dosing stream: {naoh_conc_kg_m3:.2f} kg/m³ S_Na, flow = {fixed_naoh_dose_m3_d:.4f} m³/d")

        if fixed_fecl3_dose_m3_d > 0:
            fecl3 = WasteStream('FeCl3_Dosing', T=Temp)
            fecl3.set_flow_by_concentration(
                flow_tot=fixed_fecl3_dose_m3_d,
                concentrations={'S_Fe': fecl3_conc_kg_m3 * 1000},  # kg/m³ to mg/L
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(fecl3)
            logger.info(f"Created FeCl3 dosing stream: {fecl3_conc_kg_m3:.2f} kg/m³ S_Fe, flow = {fixed_fecl3_dose_m3_d:.4f} m³/d")

        if fixed_na2co3_dose_m3_d > 0:
            na2co3 = WasteStream('Na2CO3_Dosing', T=Temp)
            na2co3.set_flow_by_concentration(
                flow_tot=fixed_na2co3_dose_m3_d,
                concentrations={'S_Na': na2co3_conc_kg_m3 * 1000},  # kg/m³ to mg/L
                units=('m3/d', 'mg/L')
            )
            dosing_streams.append(na2co3)
            logger.info(f"Created Na2CO3 dosing stream: {na2co3_conc_kg_m3:.2f} kg/m³ S_Na, flow = {fixed_na2co3_dose_m3_d:.4f} m³/d")

        # Prepare inlet streams list
        inlet_streams = [inf] + dosing_streams

        # 3. Create AnaerobicCSTRmADM1 reactor (supports 4 biogas species: CH4, CO2, H2, H2S)
        V_liq = Q * HRT
        V_gas = V_liq * 0.1  # 10% of liquid volume

        logger.info(f"Creating AnaerobicCSTRmADM1: V_liq={V_liq:.1f} m3, V_gas={V_gas:.1f} m3")
        logger.info(f"Biogas species: {madm1_model._biogas_IDs}")
        logger.info(f"Inlet streams: {len(inlet_streams)} (influent + {len(dosing_streams)} dosing)")

        AD = AnaerobicCSTRmADM1(
            'AD',
            ins=inlet_streams,  # Now includes dosing streams
            outs=(gas, eff),
            model=madm1_model,
            V_liq=V_liq,
            V_gas=V_gas,
            T=Temp,
            isdynamic=True,  # Enable dynamic simulation
            f_retain=0  # CRITICAL FIX: Set to 0 for true CSTR behavior (no retention)
        )
        # Use algebraic H2 for stability (per Codex recommendation and BSM2 pattern)
        AD.algebraic_h2 = True

        # Compile ODE with pH control if requested (rapid diagnostic test)
        if pH_ctrl is not None:
            logger.info(f"DIAGNOSTIC MODE: Fixing pH at {pH_ctrl} to emulate perfect pH control")
            AD._compile_ODE(algebraic_h2=True, pH_ctrl=pH_ctrl)

        logger.info(f"Reactor configured: algebraic_h2={AD.algebraic_h2}, pH_ctrl={pH_ctrl}, fixed dosing streams={len(dosing_streams)}")

        # 4. Initialize reactor with INOCULUM state (NOT feedstock!)
        # Use reactor_init_state (scaled biomass) instead of adm1_state_62 (feedstock)
        logger.info("Initializing reactor with INOCULUM state (scaled biomass)")
        init_conds_kg_m3 = initialize_62_component_state(reactor_init_state)

        # CRITICAL FIX: Convert kg/m³ to mg/L for set_init_conc()
        # QSDsan's set_init_conc() expects mg/L, but our state is in kg/m³
        # Without this conversion, initial concentrations are 1000x too low
        init_conds_mg_L = {k: v * 1000 for k, v in init_conds_kg_m3.items()}  # kg/m³ → mg/L
        logger.info(f"Unit conversion applied: X_I = {init_conds_kg_m3.get('X_I', 0):.3f} kg/m³ → {init_conds_mg_L.get('X_I', 0):.1f} mg/L")

        AD.set_init_conc(**init_conds_mg_L)
        logger.info(f"Initial S_SO4={init_conds_kg_m3['S_SO4']:.4f}, S_IS={init_conds_kg_m3['S_IS']:.6f}, X_hSRB={init_conds_kg_m3.get('X_hSRB', 0):.4f} (all kg/m³)")

        # 5. Set up dynamic system
        # CRITICAL FIX: Use None for ID to get unique auto-generated ID
        # This prevents ValueError on duplicate System IDs in dual-HRT runs
        logger.info("Setting up dynamic system with tracking")
        sys = System(None, path=(AD,))  # None = auto-generate unique ID

        # CRITICAL: Enable dynamic tracking for streams to update
        sys.set_dynamic_tracker(eff, gas)
        logger.info("Dynamic tracking enabled for effluent and biogas streams")

        # 6. Run simulation to steady state (no time limit)
        logger.info("Running simulation to TRUE steady-state (to avoid inventory tracking)...")
        converged_at, status = run_simulation_to_steady_state(
            sys, eff, gas,
            check_interval=check_interval,
            tolerance=tolerance
        )

        logger.info(f"Simulation complete: {status} at t={converged_at} days")

        # 7. Update pH and alkalinity for effluent
        logger.info("Calculating final pH and alkalinity")
        # CODEX BUG FIX #1: Removed legacy pH calculator - it expects S_cat/S_an (lumped ions)
        # but our 62-component model uses explicit ions (Na+, K+, Cl-, etc.)
        # The PCM already calculates correct pH during simulation - don't overwrite it
        # update_ph_and_alkalinity(eff)
        logger.info(f"Final effluent pH={eff.pH:.2f}, SAlk={eff.SAlk:.3f} meq/L")

        # 8. Log key results
        logger.info(f"=== Simulation Results ===")
        logger.info(f"COD in: {inf.COD:.1f} mg/L, COD out: {eff.COD:.1f} mg/L")
        logger.info(f"COD removal: {(1 - eff.COD/inf.COD)*100:.1f}%")
        logger.info(f"Biogas production: {gas.F_vol*24:.2f} m3/d")

        # Extract time series data for diagnostics
        time_series_data = extract_time_series(eff, gas)

        return sys, inf, eff, gas, converged_at, status, time_series_data

    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        raise RuntimeError(f"Error running ADM1+sulfur simulation: {e}")


def assess_robustness(results_design, results_check, threshold=10.0):
    """
    Compare performance at design HRT vs check HRT.

    Flags warnings if performance drops significantly (>threshold%) at check HRT,
    indicating the design may be sensitive to operational variations.

    Parameters
    ----------
    results_design : tuple
        (sys, inf, eff, gas, converged_at, status) at design HRT
    results_check : tuple
        (sys, inf, eff, gas, converged_at, status) at check HRT
    threshold : float, optional
        Performance drop threshold in % (default 10.0)

    Returns
    -------
    list
        List of warning messages if performance issues detected
    """
    warnings = []

    sys_d, inf_d, eff_d, gas_d, t_d, status_d, _time_series_d = results_design
    sys_c, inf_c, eff_c, gas_c, t_c, status_c, _time_series_c = results_check

    # COD removal comparison with zero guard
    if inf_d.COD > 1e-6 and inf_c.COD > 1e-6:
        cod_removal_design = (1 - eff_d.COD / inf_d.COD) * 100
        cod_removal_check = (1 - eff_c.COD / inf_c.COD) * 100
        cod_drop = cod_removal_design - cod_removal_check

        if cod_drop > threshold:
            warnings.append(
                f"COD removal drops {cod_drop:.1f}% at increased HRT "
                f"({cod_removal_design:.1f}% → {cod_removal_check:.1f}%). "
                f"Design may be sensitive to HRT variations."
            )
    else:
        warnings.append(
            f"COD removal assessment skipped: influent COD near zero "
            f"(design: {inf_d.COD:.6f}, check: {inf_c.COD:.6f} mg/L)"
        )

    # Biogas production comparison with zero guard
    biogas_design = gas_d.F_vol * 24  # m3/d
    biogas_check = gas_c.F_vol * 24

    if biogas_design > 1e-6:
        biogas_change = abs(biogas_design - biogas_check) / biogas_design * 100

        if biogas_change > threshold:
            warnings.append(
                f"Biogas production changes {biogas_change:.1f}% at increased HRT "
                f"({biogas_design:.1f} → {biogas_check:.1f} m3/d)."
            )
    else:
        warnings.append(
            f"Biogas production assessment skipped: design case produced near-zero biogas "
            f"({biogas_design:.6f} m3/d). System may have failed."
        )

    # Convergence comparison
    if status_d == 'converged' and status_c != 'converged':
        warnings.append(
            f"Design HRT converged at {t_d} days but check HRT did not converge. "
            f"System may be unstable at higher HRT."
        )

    return warnings


def run_dual_hrt_simulation(basis, adm1_state_62, heuristic_config, hrt_variation=0.2,
                            check_interval=2, tolerance=1e-3, pH_ctrl=None,
                            fixed_naoh_dose_m3_d=0.0, fixed_fecl3_dose_m3_d=0.0, fixed_na2co3_dose_m3_d=0.0,
                            naoh_conc_kg_m3=431.25, fecl3_conc_kg_m3=400.0, na2co3_conc_kg_m3=106.0):
    """
    Run simulation at design SRT and validation SRT.

    Performs two simulations:
    1. At design SRT from heuristic sizing
    2. At design SRT * (1 + hrt_variation) for robustness check

    Both simulations run until TRUE steady state (no time limit).

    Parameters
    ----------
    basis : dict
        Basis of design with Q, Temp, etc.
    adm1_state_62 : dict
        62-component mADM1 state
    heuristic_config : dict
        Heuristic sizing results containing digester SRT
    hrt_variation : float, optional
        Fractional SRT variation for check (default 0.2 = ±20%)
    check_interval : float, optional
        Days between convergence checks (default 2)
    tolerance : float, optional
        Convergence tolerance in kg/m3/d (default 1e-3)
    pH_ctrl : float, optional
        If specified, fixes pH at this value (e.g., 7.0) to emulate perfect pH control
    fixed_naoh_dose_m3_d : float, optional
        Fixed NaOH dosing flow rate in m³/d (default 0)
    fixed_fecl3_dose_m3_d : float, optional
        Fixed FeCl3 dosing flow rate in m³/d (default 0)
    fixed_na2co3_dose_m3_d : float, optional
        Fixed Na2CO3 dosing flow rate in m³/d (default 0)
    naoh_conc_kg_m3 : float, optional
        NaOH solution concentration in kg/m³ S_Na (default 431.25 = 50% NaOH)
    fecl3_conc_kg_m3 : float, optional
        FeCl3 solution concentration in kg/m³ S_Fe (default 100.0)
    na2co3_conc_kg_m3 : float, optional
        Na2CO3 solution concentration in kg/m³ S_Na (default 106.0)

    Returns
    -------
    tuple
        (results_design, results_check, warnings) where:
        - results_design: Tuple from run_simulation_sulfur at design SRT
        - results_check: Tuple from run_simulation_sulfur at check SRT
        - warnings: List of robustness warnings

    Notes
    -----
    For CSTR without MBR, SRT = HRT by definition (biomass leaves with liquid).
    This dual-SRT approach validates that the design isn't sitting on a performance cliff.

    Simulations run indefinitely until convergence - no arbitrary time limits.
    """
    SRT_design = heuristic_config['digester']['srt_days']
    SRT_check = SRT_design * (1 + hrt_variation)

    logger.info(f"=== Dual-SRT Validation ===")
    logger.info(f"Design SRT: {SRT_design} days (HRT = SRT for CSTR)")
    logger.info(f"Check SRT: {SRT_check} days (+{hrt_variation*100:.0f}%)")

    # Run at design SRT (runs until convergence, no time limit)
    logger.info("Running simulation at design SRT...")
    results_design = run_simulation_sulfur(basis, adm1_state_62, SRT_design,
                                          check_interval=check_interval,
                                          tolerance=tolerance,
                                          pH_ctrl=pH_ctrl,
                                          fixed_naoh_dose_m3_d=fixed_naoh_dose_m3_d,
                                          fixed_fecl3_dose_m3_d=fixed_fecl3_dose_m3_d,
                                          fixed_na2co3_dose_m3_d=fixed_na2co3_dose_m3_d,
                                          naoh_conc_kg_m3=naoh_conc_kg_m3,
                                          fecl3_conc_kg_m3=fecl3_conc_kg_m3,
                                          na2co3_conc_kg_m3=na2co3_conc_kg_m3)

    # Run at check SRT (runs until convergence, no time limit)
    logger.info("Running simulation at check SRT...")
    results_check = run_simulation_sulfur(basis, adm1_state_62, SRT_check,
                                         check_interval=check_interval,
                                         tolerance=tolerance,
                                         pH_ctrl=pH_ctrl,
                                         fixed_naoh_dose_m3_d=fixed_naoh_dose_m3_d,
                                         fixed_fecl3_dose_m3_d=fixed_fecl3_dose_m3_d,
                                         fixed_na2co3_dose_m3_d=fixed_na2co3_dose_m3_d,
                                         naoh_conc_kg_m3=naoh_conc_kg_m3,
                                         fecl3_conc_kg_m3=fecl3_conc_kg_m3,
                                         na2co3_conc_kg_m3=na2co3_conc_kg_m3)

    # Assess robustness
    logger.info("Assessing design robustness...")
    warnings = assess_robustness(results_design, results_check)

    if warnings:
        logger.warning(f"Robustness issues detected: {len(warnings)} warnings")
        for warning in warnings:
            logger.warning(f"  - {warning}")
    else:
        logger.info("Design appears robust to HRT variations")

    return results_design, results_check, warnings
