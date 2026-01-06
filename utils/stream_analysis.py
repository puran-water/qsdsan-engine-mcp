"""
Stream analysis module for ADM1+sulfur simulation.

Native implementation for 30-component ADM1+sulfur system (27 ADM1 + 3 sulfur).
No dependency on parent ADM1 MCP server.

Public API:
- analyze_liquid_stream() - Analyze influent/effluent streams
- analyze_gas_stream() - Analyze biogas with H2S
- analyze_inhibition() - Complete inhibition including H2S effects
- analyze_biomass_yields() - COD removal and biomass production
- calculate_sulfur_metrics() - Comprehensive sulfur analysis with speciation
- calculate_h2s_speciation() - H2S/HS⁻ equilibrium (Henderson-Hasselbalch)
- calculate_h2s_gas_ppm() - H2S concentration in biogas
"""

import logging
from qsdsan.processes._adm1 import non_compet_inhibit
from models.sulfur_kinetics import H2S_INHIBITION

# Native implementations for ADM1+sulfur model (30 components)
# No dependency on ADM1 MCP server - that uses standard 27-component ADM1
# Our model has 3 additional sulfur components: S_SO4, S_IS, X_SRB

logger = logging.getLogger(__name__)

# Constants for gas calculations
_HOURS_PER_DAY = 24.0
# Ideal molar volume at standard conditions (0 °C, 1 atm); aligns with ADM1 stoichiometry.
_STD_MOLAR_VOLUME_M3_PER_KMOL = 22.414
_SULFUR_MOLAR_MASS_KG_PER_KMOL = 32.065  # kg S per kmol


def safe_get(stream, attr, default=None):
    """Safely get attribute from stream."""
    return getattr(stream, attr, default)


def safe_composite(stream, param, **kwargs):
    """Safely get composite property from QSDsan stream."""
    try:
        if hasattr(stream, 'composite'):
            return stream.composite(param, **kwargs)
        return None
    except:
        return None


def get_component_conc_kg_m3(stream, component_id):
    """
    Get component concentration in kg/m³.

    **Use this function for kinetic calculations** (e.g., inhibition functions).

    Parameters
    ----------
    stream : WasteStream
        QSDsan stream object
    component_id : str
        Component ID (e.g., 'S_IS', 'S_SO4', 'X_SRB')

    Returns
    -------
    float or None
        Concentration in kg/m³, or None if component not found

    Notes
    -----
    ADM1 kinetic functions (like `non_compet_inhibit`) expect kg/m³.
    This function ensures correct units for inhibition calculations.
    """
    try:
        if component_id not in stream.components.IDs:
            return None

        if stream.F_vol > 0:
            # Concentration (kg/m3) = mass flow (kg/d) / volumetric flow (m3/d)
            return stream.imass[component_id] / stream.F_vol
        else:
            return 0.0
    except:
        return None


def get_component_conc_mg_L(stream, component_id):
    """
    Get component concentration in mg/L.

    **Use this function for reporting and display**.

    Parameters
    ----------
    stream : WasteStream
        QSDsan stream object
    component_id : str
        Component ID (e.g., 'S_IS', 'S_SO4', 'X_SRB')

    Returns
    -------
    float or None
        Concentration in mg/L, or None if component not found

    Notes
    -----
    This is the standard reporting unit. For kinetic calculations,
    use `get_component_conc_kg_m3()` instead.
    """
    conc_kg_m3 = get_component_conc_kg_m3(stream, component_id)
    if conc_kg_m3 is not None:
        return conc_kg_m3 * 1000  # Convert kg/m³ to mg/L
    return None


# Legacy function for backward compatibility within this file
def get_component_conc(stream, component_id, units='mg/L'):
    """
    Legacy function - use get_component_conc_mg_L() or get_component_conc_kg_m3() instead.

    This function is deprecated but kept for internal compatibility.
    """
    if units == 'kg/m3':
        return get_component_conc_kg_m3(stream, component_id)
    else:
        return get_component_conc_mg_L(stream, component_id)


def _calculate_stream_ph(stream):
    """
    Get stream pH from stored attribute or default.

    Note: Equilibrium pH calculation via PCM solver is handled by the
    Codex validation tooling in the anaerobic-design-skill, not the engine.
    The engine uses the stored pH attribute from simulation.
    """
    if not hasattr(stream, 'F_vol') or stream.F_vol <= 0:
        return 7.0

    # Use stream's stored pH attribute (set by simulation/reactor dynamics)
    # Default to 7.0 if not set
    return round(float(getattr(stream, '_pH', 7.0)), 2)


def _analyze_liquid_stream_core(stream, include_components=False):
    """
    Core liquid stream analysis (private helper).

    Provides base ADM1 metrics without sulfur roll-up.
    Used internally by analyze_liquid_stream().
    """
    try:
        # Calculate pH from charge balance (gas-phase independent)
        # This is correct for influent/effluent that are not in biogas equilibrium
        calculated_ph = _calculate_stream_ph(stream)

        result = {
            "success": True,
            "flow": stream.F_vol * 24 if hasattr(stream, 'F_vol') else 0,  # m3/d
            "temperature": stream.T if hasattr(stream, 'T') else 308.15,
            "pH": calculated_ph,  # Gas-independent charge balance pH
            "COD": stream.COD if hasattr(stream, 'COD') else 0,  # mg/L
            "TSS": stream.get_TSS() if hasattr(stream, 'get_TSS') else 0,
            "VSS": stream.get_VSS() if hasattr(stream, 'get_VSS') else 0,
            "TKN": stream.TKN if hasattr(stream, 'TKN') else 0,
            "TP": safe_composite(stream, 'P') if hasattr(stream, 'composite') else 0,
            "alkalinity": stream.SAlk * 50 if hasattr(stream, 'SAlk') else 0  # meq/L to mg/L as CaCO3
        }

        if include_components:
            # Include all 30 components (27 ADM1 + 3 sulfur)
            result["components"] = {}
            for comp_id in stream.components.IDs:
                result["components"][comp_id] = get_component_conc_mg_L(stream, comp_id)

        return result

    except Exception as e:
        logger.error(f"Error analyzing liquid stream: {e}")
        return {"success": False, "message": f"Error: {e}"}


def _analyze_gas_stream_core(stream):
    """
    Core gas stream analysis (private helper).

    Provides base biogas metrics without H2S.
    Used internally by analyze_gas_stream().

    FIX #3 (Codex 2025-10-24): Convert to STP Nm³/d for proper comparison
    with theoretical methane yield (0.35 Nm³/kg COD).
    Uses F_mol (kmol/hr) and standard molar volume (22.414 L/mol at STP).
    """
    try:
        # FIX #3: Use molar flow and STP conversion (Codex analysis 2025-10-24)
        # Standard molar volume at STP: 22.414 L/mol = 0.022414 m³/mol
        STP_MOLAR_VOLUME = 22.414  # L/mol at STP (0°C, 1 atm)

        # Get component molar flows (kmol/hr)
        if stream.F_mol > 0:
            ch4_mol = stream.imol['S_ch4'] if 'S_ch4' in stream.components.IDs else 0  # kmol/hr
            co2_mol = stream.imol['S_IC'] if 'S_IC' in stream.components.IDs else 0   # kmol/hr
            h2_mol = stream.imol['S_h2'] if 'S_h2' in stream.components.IDs else 0    # kmol/hr

            # Convert kmol/hr → mol/hr → L/hr → m³/hr → m³/d at STP
            ch4_flow = ch4_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24  # Nm³/d
            co2_flow = co2_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24  # Nm³/d
            h2_flow = h2_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24   # Nm³/d
            flow_total = ch4_flow + co2_flow + h2_flow  # Nm³/d

            # Calculate percentages
            ch4_frac = stream.imol['S_ch4'] / stream.F_mol if 'S_ch4' in stream.components.IDs else 0
            co2_frac = stream.imol['S_IC'] / stream.F_mol if 'S_IC' in stream.components.IDs else 0
            h2_frac = stream.imol['S_h2'] / stream.F_mol if 'S_h2' in stream.components.IDs else 0
        else:
            ch4_flow = co2_flow = h2_flow = flow_total = 0
            ch4_frac = co2_frac = h2_frac = 0

        return {
            "success": True,
            "flow_total": flow_total,  # Nm³/d at STP (FIXED)
            "methane_flow": ch4_flow,  # Nm³/d at STP (FIXED)
            "methane_percent": ch4_frac * 100,
            "co2_flow": co2_flow,      # Nm³/d at STP (FIXED)
            "co2_percent": co2_frac * 100,
            "h2_flow": h2_flow,        # Nm³/d at STP (FIXED)
            "h2_percent": h2_frac * 100
        }
    except Exception as e:
        logger.error(f"Error analyzing gas stream: {e}")
        return {"success": False, "message": f"Error: {e}"}


def analyze_biomass_yields(inf_stream, eff_stream, system=None, diagnostics=None):
    """
    Calculate biomass yields and COD removal for mADM1 model.

    Uses QSDsan methodology based on production rates and stoichiometry, NOT state changes.
    State-based calculation (eff - inf) is incorrect for steady-state CSTR and always yields ~0.

    Parameters
    ----------
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream
    system : qsdsan.System
        QSDsan system containing the anaerobic reactor (REQUIRED)
    diagnostics : dict, optional
        Pre-computed diagnostic data (will be extracted if not provided)

    Returns
    -------
    dict
        Yield data including:
        - VSS_yield, TSS_yield: Overall yields (kg/kg COD)
        - COD_removal_efficiency: Percent
        - detailed: Complete breakdown by functional group and precipitates

    Notes
    -----
    - Requires system object to access reactor and process model
    - Biomass yields calculated from concentrations and HRT
    - Precipitates reported in kg/d (NOT kg COD/d since they have i_COD=0)
    """
    try:
        # COD removal efficiency
        cod_removal = (1 - eff_stream.COD / inf_stream.COD) * 100 if inf_stream.COD > 0 else 0

        # System object is REQUIRED for correct yield calculation
        if system is None:
            logger.error("System object required for biomass yield calculation")
            return {
                "success": False,
                "message": "System object required - cannot calculate yields from state changes for steady-state CSTR",
                "COD_removal_efficiency": cod_removal
            }

        # Calculate detailed yields using QSDsan methodology
        detailed_yields = calculate_net_biomass_yields(
            system, inf_stream, eff_stream, diagnostics
        )

        if not detailed_yields.get('success'):
            logger.error(f"Yield calculation failed: {detailed_yields.get('message')}")
            return {
                "success": False,
                "message": detailed_yields.get('message'),
                "COD_removal_efficiency": cod_removal
            }

        # Extract overall yields for top-level result
        overall = detailed_yields.get('overall', {})

        result = {
            "success": True,
            "VSS_yield": overall.get('VSS_yield_kg_per_kg_COD', 0.0),
            "TSS_yield": overall.get('TSS_yield_kg_per_kg_COD', 0.0),
            "COD_removal_efficiency": cod_removal,
            "detailed": detailed_yields
        }

        logger.info(f"Biomass yields: VSS={result['VSS_yield']:.4f}, TSS={result['TSS_yield']:.4f} kg/kg COD")

        return result

    except Exception as e:
        logger.error(f"Error calculating biomass yields: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Exception during yield calculation: {str(e)}"
        }


def _analyze_inhibition_core(sim_results):
    """
    Core inhibition analysis (private helper).

    Provides base inhibition framework without H2S metrics.
    Used internally by analyze_inhibition().
    """
    try:
        # Extract streams
        if len(sim_results) >= 4:
            _, inf, eff, gas = sim_results[:4]
        else:
            return {"success": False, "message": "Incomplete simulation results"}

        # Basic inhibition framework
        # Extended by analyze_inhibition_sulfur() to add H2S inhibition

        return {
            "success": True,
            "inhibition_factors": [],  # Will be populated by sulfur extension
            "recommendations": []
        }

    except Exception as e:
        logger.error(f"Error in inhibition analysis: {e}")
        return {"success": False, "message": f"Error: {e}"}

def calculate_h2s_speciation(S_IS_total, pH, temperature_K=308.15, input_units='kg/m3'):
    """
    Calculate H2S/HS⁻ speciation using Henderson-Hasselbalch equation.

    The dissolved sulfide equilibrium:
        H2S ⇌ H+ + HS⁻
        pH = pKa + log([HS⁻]/[H2S])

    Parameters
    ----------
    S_IS_total : float
        Total dissolved sulfide concentration
    pH : float
        pH of the solution
    temperature_K : float, optional
        Temperature in K (default 308.15 = 35°C)
    input_units : str, optional
        Units of S_IS_total: 'kg/m3' (default) or 'mg/L'

    Returns
    -------
    dict
        Dictionary containing:
        - 'H2S_dissolved_kg_m3': H2S concentration in kg S/m³ (for inhibition calcs)
        - 'H2S_dissolved_mg_L': H2S concentration in mg S/L (for reporting)
        - 'HS_dissolved_kg_m3': HS⁻ concentration in kg S/m³
        - 'HS_dissolved_mg_L': HS⁻ concentration in mg S/L
        - 'fraction_H2S': Fraction as H2S (0-1)
        - 'pKa': pKa value used

    Notes
    -----
    - pKa(H2S) ≈ 7.0 at 35°C (typical digester temperature)
    - At pH 7.0: 50% H2S, 50% HS⁻
    - At pH 6.0: 91% H2S (highly inhibitory)
    - At pH 8.0: 91% HS⁻ (less inhibitory)

    **CRITICAL** (per Codex review):
    - H2S inhibition intensity depends strongly on pH through this speciation
    - Must use kg/m³ for inhibition calculations (kinetic functions expect these units)
    - Must report both forms to interpret inhibition correctly
    """
    # Convert to kg/m3 if needed
    if input_units == 'mg/L':
        S_IS_total_kg_m3 = S_IS_total / 1000
    else:
        S_IS_total_kg_m3 = S_IS_total

    # pKa as function of temperature (simplified)
    # pKa ≈ 7.0 at 35°C, varies slightly with T
    pKa_H2S = 7.0  # Could refine with temperature correction

    # Henderson-Hasselbalch: pH = pKa + log([HS⁻]/[H2S])
    # Rearranging: [H2S]/[HS⁻] = 10^(pKa - pH)
    # fraction_H2S = [H2S] / ([H2S] + [HS⁻]) = 1 / (1 + 10^(pH - pKa))

    fraction_H2S = 1.0 / (1.0 + 10**(pH - pKa_H2S))
    fraction_HS = 1.0 - fraction_H2S

    H2S_dissolved_kg_m3 = S_IS_total_kg_m3 * fraction_H2S
    HS_dissolved_kg_m3 = S_IS_total_kg_m3 * fraction_HS

    logger.debug(f"H2S speciation at pH={pH:.2f}: {fraction_H2S*100:.1f}% H2S, {fraction_HS*100:.1f}% HS⁻")
    logger.debug(f"H2S concentration: {H2S_dissolved_kg_m3:.6f} kg S/m³ ({H2S_dissolved_kg_m3*1000:.4f} mg S/L)")

    return {
        'H2S_dissolved_kg_m3': H2S_dissolved_kg_m3,  # For inhibition calculations!
        'H2S_dissolved_mg_L': H2S_dissolved_kg_m3 * 1000,  # For reporting
        'HS_dissolved_kg_m3': HS_dissolved_kg_m3,
        'HS_dissolved_mg_L': HS_dissolved_kg_m3 * 1000,
        'fraction_H2S': fraction_H2S,
        'pKa': pKa_H2S
    }


def calculate_h2s_gas_ppm(gas_stream):
    """
    Calculate H2S concentration in biogas.

    Parameters
    ----------
    gas_stream : WasteStream
        Biogas stream from simulation

    Returns
    -------
    float
        H2S concentration in ppmv (parts per million by volume)

    Notes
    -----
    - H2S in biogas typically 100-10,000 ppm
    - >2000 ppm: Highly corrosive, requires treatment
    - <1000 ppm: Acceptable for some applications
    - H2S partitions to gas phase based on Henry's law
    """
    try:
        components = getattr(gas_stream, 'components', None)
        component_ids = components.IDs if components else ()

        if 'S_IS' not in component_ids:
            return 0.0

        total_mol_hr = getattr(gas_stream, 'F_mol', 0.0)
        if total_mol_hr <= 0:
            return 0.0

        try:
            h2s_mol_hr = gas_stream.imol['S_IS']
        except Exception:
            # If the component is present but mol flow is inaccessible, assume zero
            h2s_mol_hr = 0.0

        if h2s_mol_hr <= 0:
            return 0.0

        mol_fraction = h2s_mol_hr / total_mol_hr
        ppmv = mol_fraction * 1e6
        return ppmv

    except Exception as e:
        logger.warning(f"Error calculating H2S in biogas: {e}")
        return 0.0


def calculate_sulfur_metrics(inf, eff, gas):
    """
    Comprehensive sulfur analysis with mass balance and speciation.

    Parameters
    ----------
    inf : WasteStream
        Influent stream
    eff : WasteStream
        Effluent stream
    gas : WasteStream
        Biogas stream

    Returns
    -------
    dict
        Flat dictionary with all sulfur metrics at top level:
        - Sulfate: sulfate_in_mg_L, sulfate_out_mg_L, sulfate_removal_pct,
                   sulfate_in_kg_S_d, sulfate_out_kg_S_d
        - Sulfide: sulfide_total_mg_L, sulfide_out_kg_S_d,
                   H2S_dissolved_mg_L, H2S_dissolved_kg_m3, HS_dissolved_mg_L,
                   fraction_H2S, pH
        - Biogas: h2s_biogas_ppm, h2s_biogas_percent, h2s_biogas_kg_S_d
        - SRB: srb_biomass_mg_COD_L, srb_yield_kg_VSS_per_kg_COD
        - Inhibition: inhibition_acetoclastic_pct, inhibition_acetoclastic_factor,
                      inhibition_hydrogenotrophic_pct, inhibition_hydrogenotrophic_factor,
                      KI_h2s_acetoclastic, KI_h2s_hydrogenotrophic
        - speciation: Full speciation dict for reuse by other functions

    Raises
    ------
    ValueError
        If streams are missing required sulfur components (S_SO4, S_IS)
        or have zero flow when mass balance is expected

    Notes
    -----
    **Clean flat structure** - No nested dicts. All keys at top level for easy access.

    This function provides comprehensive sulfur analysis:
    1. Sulfate mass balance (concentrations and flows)
    2. Dissolved sulfide with H2S/HS⁻ speciation
    3. H2S in biogas (ppm, %, mass flow)
    4. SRB biomass and yield
    5. H2S inhibition on methanogens (acetoclastic and hydrogenotrophic)

    **Fail-fast validation**: This function validates inputs strictly for design-grade tools.
    Missing sulfur components indicate the stream is not from an ADM1+sulfur simulation.
    """
    # Fail-fast validation for design-grade tool
    # Check influent has required sulfur components
    if 'S_SO4' not in inf.components.IDs:
        raise ValueError(
            "Influent stream missing S_SO4 component - not a valid ADM1+sulfur stream. "
            "This function requires a 30-component ADM1+sulfur simulation (not 27-component ADM1)."
        )

    # Check effluent has all sulfur components
    if 'S_SO4' not in eff.components.IDs:
        raise ValueError(
            "Effluent stream missing S_SO4 component - not a valid ADM1+sulfur stream. "
            "This function requires a 30-component ADM1+sulfur simulation (not 27-component ADM1)."
        )
    if 'S_IS' not in eff.components.IDs:
        raise ValueError(
            "Effluent stream missing S_IS component - not a valid ADM1+sulfur stream. "
            "This function requires a 30-component ADM1+sulfur simulation (not 27-component ADM1)."
        )

    # Check for zero flows when mass balance is expected
    if inf.F_vol <= 0:
        raise ValueError(
            f"Influent stream has zero or negative flow (F_vol={inf.F_vol}). "
            "Cannot perform sulfur mass balance without flow information."
        )
    if eff.F_vol <= 0:
        raise ValueError(
            f"Effluent stream has zero or negative flow (F_vol={eff.F_vol}). "
            "Cannot perform sulfur mass balance without flow information."
        )
    if gas.F_vol <= 0:
        raise ValueError(
            f"Biogas stream has zero or negative flow (F_vol={gas.F_vol}). "
            "Cannot perform sulfur mass balance without biogas flow information."
        )

    try:
        # Get sulfur component concentrations using explicit unit functions
        S_SO4_in_mg_L = get_component_conc_mg_L(inf, 'S_SO4')
        S_SO4_out_mg_L = get_component_conc_mg_L(eff, 'S_SO4')
        S_IS_total_mg_L = get_component_conc_mg_L(eff, 'S_IS')
        S_IS_total_kg_m3 = get_component_conc_kg_m3(eff, 'S_IS')  # For inhibition!
        X_SRB = get_component_conc_mg_L(eff, 'X_SRB')  # mg COD/L

        pH = getattr(eff, 'pH', 7.0)

        # 1. Mass balance (both concentrations and mass flows)
        sulfate_removal = 0.0
        if S_SO4_in_mg_L and S_SO4_in_mg_L > 1e-6:
            sulfate_removal = (1 - S_SO4_out_mg_L/S_SO4_in_mg_L) * 100

        # Calculate mass flows (kg S/d) from concentrations and flow rates
        Q_inf_m3_d = inf.F_vol * _HOURS_PER_DAY  # m3/d
        Q_eff_m3_d = eff.F_vol * _HOURS_PER_DAY  # m3/d
        gas_mol_hr = getattr(gas, 'F_mol', 0.0)
        Q_gas_m3_d = gas_mol_hr * _STD_MOLAR_VOLUME_M3_PER_KMOL * _HOURS_PER_DAY  # Nm3/d

        # Mass flows: concentration (mg S/L) * flow (m3/d) * (1 kg / 1e6 mg) * (1000 L / m3)
        # Simplifies to: concentration (mg S/L) * flow (m3/d) / 1000 = kg S/d
        sulfate_in_kg_S_d = (S_SO4_in_mg_L * Q_inf_m3_d / 1000) if S_SO4_in_mg_L else 0.0
        sulfate_out_kg_S_d = (S_SO4_out_mg_L * Q_eff_m3_d / 1000) if S_SO4_out_mg_L else 0.0
        sulfide_out_kg_S_d = (S_IS_total_mg_L * Q_eff_m3_d / 1000) if S_IS_total_mg_L else 0.0

        # H2S in biogas: use gas stream S_IS mass flow directly
        # gas.imass['S_IS'] is already in kg/hr, convert to kg/d
        if hasattr(gas, 'imol') and 'S_IS' in gas.components.IDs:
            try:
                h2s_mol_hr = gas.imol['S_IS']
            except Exception:
                h2s_mol_hr = 0.0
            h2s_biogas_kg_S_d = h2s_mol_hr * _SULFUR_MOLAR_MASS_KG_PER_KMOL * _HOURS_PER_DAY
        else:
            h2s_biogas_kg_S_d = 0.0

        mass_balance = {
            # Concentrations (for reporting)
            "sulfate_in": S_SO4_in_mg_L if S_SO4_in_mg_L else 0.0,  # mg S/L
            "sulfate_out": S_SO4_out_mg_L if S_SO4_out_mg_L else 0.0,  # mg S/L
            "sulfate_removal_pct": sulfate_removal,  # %

            # Mass flows (for balance calculations)
            "sulfate_in_kg_S_d": sulfate_in_kg_S_d,
            "sulfate_out_kg_S_d": sulfate_out_kg_S_d,
            "sulfide_out_kg_S_d": sulfide_out_kg_S_d,
            "h2s_biogas_kg_S_d": h2s_biogas_kg_S_d
        }

        # 2. Dissolved sulfide with speciation
        # CRITICAL: Pass kg/m3 to speciation (which then returns both units)
        speciation = calculate_h2s_speciation(
            S_IS_total_kg_m3 if S_IS_total_kg_m3 else 0.0,
            pH,
            input_units='kg/m3'
        )

        dissolved_sulfide = {
            "total": S_IS_total_mg_L if S_IS_total_mg_L else 0.0,  # mg S/L for reporting
            "H2S": speciation['H2S_dissolved_mg_L'],  # mg S/L for reporting
            "HS_minus": speciation['HS_dissolved_mg_L'],  # mg S/L for reporting
            "pH": pH,
            "fraction_H2S": speciation['fraction_H2S']
        }

        # 3. Biogas H2S
        h2s_ppm = calculate_h2s_gas_ppm(gas)

        # Calculate H2S concentration in biogas (vol%)
        # H2S ppm / 10000 = vol%
        h2s_percent = h2s_ppm / 10000.0

        biogas_h2s = {
            "h2s_ppm": h2s_ppm,
            "concentration_ppm": h2s_ppm,  # Alias for sulfur_balance.py compatibility
            "concentration_percent": h2s_percent,  # vol%
            "h2s_mg_per_L": speciation['H2S_dissolved_mg_L']  # Use speciation result
        }

        # 4. SRB performance
        srb_performance = {
            "biomass_conc": X_SRB if X_SRB else 0.0,  # mg COD/L
            "yield": calculate_srb_yield(inf, eff)
        }

        # 5. H2S inhibition on methanogens
        # CRITICAL: Use kg/m3 for inhibition calculations!
        H2S_dissolved_kg_m3 = speciation['H2S_dissolved_kg_m3']

        # Get inhibition constants from kinetics module (these are in kg/m3)
        KI_h2s_ac = H2S_INHIBITION['KI_h2s_ac']  # kg S/m3 for acetoclastic
        KI_h2s_h2 = H2S_INHIBITION['KI_h2s_h2']  # kg S/m3 for hydrogenotrophic

        # Calculate inhibition factors (0-1, where 1 = no inhibition)
        # CRITICAL: Both concentrations must be in kg/m3!
        I_ac = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_ac)
        I_h2 = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_h2)

        # Convert to inhibition percentage (0-100, where 0 = no inhibition)
        inhibition_pct_ac = (1 - I_ac) * 100
        inhibition_pct_h2 = (1 - I_h2) * 100

        # Use mg/L for reporting
        H2S_dissolved_mg_L = speciation['H2S_dissolved_mg_L']

        h2s_inhibition = {
            "acetoclastic": {
                "inhibition_pct": inhibition_pct_ac,
                "activity_factor": I_ac,  # 1 = full activity
                "KI": KI_h2s_ac
            },
            "hydrogenotrophic": {
                "inhibition_pct": inhibition_pct_h2,
                "activity_factor": I_h2,
                "KI": KI_h2s_h2
            },
            "H2S_concentration_mg_L": H2S_dissolved_mg_L,  # mg S/L for reporting
            "H2S_concentration_kg_m3": H2S_dissolved_kg_m3  # kg S/m3 (used for inhibition calcs)
        }

        logger.info(f"Sulfur metrics: SO4 removal={sulfate_removal:.1f}%, "
                   f"H2S={H2S_dissolved_mg_L:.4f} mg S/L, "
                   f"Inhibition: ac={inhibition_pct_ac:.1f}%, h2={inhibition_pct_h2:.1f}%")

        # Return flat structure (no nested dicts) - cleaner API
        return {
            "success": True,
            # Sulfate mass balance
            "sulfate_in_mg_L": S_SO4_in_mg_L if S_SO4_in_mg_L else 0.0,
            "sulfate_out_mg_L": S_SO4_out_mg_L if S_SO4_out_mg_L else 0.0,
            "sulfate_removal_pct": sulfate_removal,
            "sulfate_in_kg_S_d": sulfate_in_kg_S_d,
            "sulfate_out_kg_S_d": sulfate_out_kg_S_d,

            # Dissolved sulfide (effluent)
            "sulfide_total_mg_L": S_IS_total_mg_L if S_IS_total_mg_L else 0.0,
            "sulfide_out_kg_S_d": sulfide_out_kg_S_d,
            "H2S_dissolved_mg_L": H2S_dissolved_mg_L,
            "H2S_dissolved_kg_m3": H2S_dissolved_kg_m3,
            "HS_dissolved_mg_L": speciation['HS_dissolved_mg_L'],
            "fraction_H2S": speciation['fraction_H2S'],
            "pH": pH,

            # Biogas H2S
            "h2s_biogas_ppm": h2s_ppm,
            "h2s_biogas_percent": h2s_percent,
            "h2s_biogas_kg_S_d": h2s_biogas_kg_S_d,

            # SRB performance
            "srb_biomass_mg_COD_L": X_SRB if X_SRB else 0.0,
            "srb_yield_kg_VSS_per_kg_COD": calculate_srb_yield(inf, eff),

            # H2S inhibition on methanogens
            "inhibition_acetoclastic_pct": inhibition_pct_ac,
            "inhibition_acetoclastic_factor": I_ac,
            "inhibition_hydrogenotrophic_pct": inhibition_pct_h2,
            "inhibition_hydrogenotrophic_factor": I_h2,
            "KI_h2s_acetoclastic": KI_h2s_ac,
            "KI_h2s_hydrogenotrophic": KI_h2s_h2,

            # Speciation object for reuse
            "speciation": speciation
        }

    except Exception as e:
        logger.error(f"Error calculating sulfur metrics: {e}")
        return {
            "success": False,
            "message": f"Error calculating sulfur metrics: {e}"
        }


# Component classification constants for mADM1
BIOMASS_COMPONENTS = [
    'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',  # Core degraders & methanogens
    'X_PAO',  # Polyphosphate accumulators
    'X_PHA',  # Storage polymer (polyhydroxyalkanoates)
    'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB'  # Sulfate reducers
]

PRECIPITATE_COMPONENTS = [
    # Phosphate minerals
    'X_struv', 'X_newb', 'X_kstruv',  # Struvites
    'X_HAP', 'X_ACP', 'X_DCPD', 'X_OCP',  # Calcium phosphates
    'X_Fe3PO42', 'X_AlPO4',  # Iron/aluminum phosphates
    # Carbonates
    'X_ACC', 'X_CCM', 'X_magn',
    # Sulfides
    'X_FeS',
    # Iron oxides
    'X_HFO_H', 'X_HFO_L', 'X_HFO_old',
    'X_HFO_HP', 'X_HFO_LP', 'X_HFO_HP_old', 'X_HFO_LP_old'
]


def is_biomass_component(component_id):
    """Check if component is an active biomass group."""
    return component_id in BIOMASS_COMPONENTS


def is_precipitate_component(component_id):
    """Check if component is an inorganic precipitate."""
    return component_id in PRECIPITATE_COMPONENTS


def get_component_i_mass(stream, component_id):
    """
    Get i_mass property (kg TSS per kg COD) for a component.

    Returns 0.0 if component not found or property not available.
    """
    try:
        if hasattr(stream, 'components'):
            cmps = stream.components
            if hasattr(cmps, component_id):
                cmp = getattr(cmps, component_id)
                if hasattr(cmp, 'i_mass'):
                    return float(cmp.i_mass)
    except Exception as e:
        logger.debug(f"Could not get i_mass for {component_id}: {e}")
    return 0.0


def get_component_f_vmass(stream, component_id):
    """
    Get f_Vmass_Totmass property (volatile fraction) for a component.

    Returns 0.0 for inorganic precipitates, typical value (0.85) for biomass if not available.
    """
    try:
        if hasattr(stream, 'components'):
            cmps = stream.components
            if hasattr(cmps, component_id):
                cmp = getattr(cmps, component_id)
                if hasattr(cmp, 'f_Vmass_Totmass'):
                    return float(cmp.f_Vmass_Totmass)

        # Fallback: inorganic precipitates have f_V = 0, biomass typically ~0.85
        if is_precipitate_component(component_id):
            return 0.0
        elif is_biomass_component(component_id):
            return 0.85  # Typical biomass volatile fraction

    except Exception as e:
        logger.debug(f"Could not get f_Vmass for {component_id}: {e}")

    return 0.85 if is_biomass_component(component_id) else 0.0


def calculate_net_biomass_yields(system, inf_stream, eff_stream, diagnostics=None):
    """
    Calculate net biomass yields and precipitate formation following QSDsan methodology.

    Uses production rates from process model (not state changes) to properly account
    for steady-state CSTR operation where biomass in ≈ biomass out.

    Parameters
    ----------
    system : qsdsan.System
        QSDsan system containing the anaerobic reactor
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream
    diagnostics : dict, optional
        Pre-computed diagnostic data (from extract_diagnostics)

    Returns
    -------
    dict
        Comprehensive yield and precipitate data with structure:
        {
            "success": bool,
            "overall": {
                "VSS_yield_kg_per_kg_COD": float,
                "TSS_yield_kg_per_kg_COD": float,
                "biomass_TSS_yield": float,
                "precipitate_TSS_yield": float
            },
            "per_functional_group": {
                "X_su": {"yield_kg_VSS_per_kg_COD": float, "net_production_kg_d": float},
                ...
            },
            "precipitates": {
                "X_struv": {"formation_kg_d": float, "formation_kg_TSS_d": float},
                ...
            },
            "total_precipitate_formation_kg_d": float,
            "total_precipitate_formation_kg_TSS_d": float
        }

    Notes
    -----
    - Biomass yields calculated from production rates using stoichiometry
    - Precipitates reported in kg/d (NOT kg COD/d since they have i_COD=0)
    - Uses component i_mass and f_Vmass_Totmass for unit conversions
    - Inorganic precipitates contribute to TSS but NOT VSS
    """
    try:
        # Extract diagnostics if not provided
        if diagnostics is None:
            diagnostics = extract_diagnostics(system)

        if not diagnostics.get('success'):
            return {
                "success": False,
                "message": f"Diagnostic extraction failed: {diagnostics.get('message')}"
            }

        # Get biomass concentrations from diagnostics (kg/m³)
        biomass_conc = diagnostics.get('biomass_kg_m3', {})

        # Get reactor volume from system
        ad_reactor = None
        for unit in system.units:
            if hasattr(unit, 'ID') and unit.ID == 'AD':
                ad_reactor = unit
                break

        if ad_reactor is None:
            return {
                "success": False,
                "message": "Anaerobic digester reactor not found in system"
            }

        V_liq = ad_reactor.V_liq if hasattr(ad_reactor, 'V_liq') else 10000.0  # m³

        # Calculate COD removed
        COD_in = inf_stream.COD if hasattr(inf_stream, 'COD') else 0.0
        COD_out = eff_stream.COD if hasattr(eff_stream, 'COD') else 0.0
        COD_removed_mg_L = COD_in - COD_out

        Q = inf_stream.F_vol * 24 if hasattr(inf_stream, 'F_vol') else 1000.0  # m³/d
        COD_removed_kg_d = COD_removed_mg_L * Q / 1000.0  # mg/L × m³/d → kg/d

        if COD_removed_kg_d < 1e-6:
            return {
                "success": False,
                "message": "No COD removal - cannot calculate yields"
            }

        # Initialize results
        per_group_yields = {}
        total_biomass_VSS_kg_d = 0.0
        total_biomass_TSS_kg_d = 0.0

        # Get model and stoichiometry matrix for M@r calculation (upstream QSDsan method)
        model = ad_reactor.model if hasattr(ad_reactor, 'model') else None
        process_rates = diagnostics.get('process_rates', [])

        # Use stoichiometric matrix approach: prod_rates = M.T @ process_rates
        # This is the standard QSDsan method, NOT washout
        component_production_rates = None
        if model is not None and len(process_rates) > 0:
            try:
                # Get stoichiometry matrix (processes × components)
                M = model.stoichio_eval()
                # Compute component production rates: M.T @ rho (kg/m³/d)
                component_production_rates = M.T @ process_rates
            except Exception as e:
                logger.warning(f"Could not compute M@r for biomass yields: {e}")

        # Calculate yields for each biomass functional group
        for biomass_id in BIOMASS_COMPONENTS:
            # Get biomass concentration (kg COD/m³)
            biomass_cod_kg_m3 = biomass_conc.get(biomass_id, 0.0)

            # Net production (kg COD/d) using M@r approach (QSDsan methodology)
            if component_production_rates is not None:
                try:
                    # Get component index
                    cmp_idx = list(eff_stream.components.IDs).index(biomass_id)
                    prod_rate_kg_m3_d = component_production_rates[cmp_idx]
                    # Only count positive production (net growth, not decay)
                    net_production_cod_kg_d = max(0.0, prod_rate_kg_m3_d * V_liq)
                except (ValueError, IndexError):
                    # Component not found or index error - fall back to zero
                    net_production_cod_kg_d = 0.0
            else:
                # Fallback: if M@r failed, use zero (better than wrong washout estimate)
                net_production_cod_kg_d = 0.0

            # Convert to VSS using component properties
            i_mass = get_component_i_mass(eff_stream, biomass_id)
            f_vmass = get_component_f_vmass(eff_stream, biomass_id)

            # kg COD/d → kg VSS/d
            net_production_vss_kg_d = net_production_cod_kg_d * i_mass * f_vmass

            # kg COD/d → kg TSS/d
            net_production_tss_kg_d = net_production_cod_kg_d * i_mass

            # Yield (kg VSS per kg COD removed)
            yield_vss = net_production_vss_kg_d / COD_removed_kg_d if COD_removed_kg_d > 0 else 0.0

            per_group_yields[biomass_id] = {
                "yield_kg_VSS_per_kg_COD": yield_vss,
                "net_production_kg_VSS_d": net_production_vss_kg_d,
                "net_production_kg_TSS_d": net_production_tss_kg_d,
                "concentration_kg_COD_m3": biomass_cod_kg_m3
            }

            total_biomass_VSS_kg_d += net_production_vss_kg_d
            total_biomass_TSS_kg_d += net_production_tss_kg_d

        # Calculate precipitate formation from process rates (QSDsan methodology)
        # Precipitation processes are at indices 46-58 (13 processes)
        # Corresponding to ModifiedADM1._precipitates list
        precipitate_data = {}
        total_precip_kg_d = 0.0
        total_precip_tss_kg_d = 0.0

        # Get process rates from diagnostics
        process_rates = diagnostics.get('process_rates', [])

        # Define precipitation process indices and corresponding component IDs
        # From utils/qsdsan_madm1.py:1068-1069:
        # _precipitates = ('X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP',
        #                  'X_struv', 'X_newb', 'X_magn', 'X_kstruv',
        #                  'X_FeS', 'X_Fe3PO42', 'X_AlPO4')
        # Process indices: 46-58 (13 total)
        PRECIP_START_IDX = 46
        PRECIP_END_IDX = 59  # Exclusive

        # Mapping from process index to component ID
        PRECIP_COMPONENTS_ORDERED = [
            'X_CCM', 'X_ACC', 'X_ACP', 'X_HAP', 'X_DCPD', 'X_OCP',
            'X_struv', 'X_newb', 'X_magn', 'X_kstruv',
            'X_FeS', 'X_Fe3PO42', 'X_AlPO4'
        ]

        if len(process_rates) >= PRECIP_END_IDX:
            for i, precip_id in enumerate(PRECIP_COMPONENTS_ORDERED):
                process_idx = PRECIP_START_IDX + i

                # Get precipitation rate (kg/m³/d)
                rate_kg_m3_d = process_rates[process_idx]

                # Convert to kg/d using liquid volume
                formation_kg_d = rate_kg_m3_d * V_liq

                if abs(formation_kg_d) > 1e-6:  # Only report active precipitation
                    # Get effluent concentration for reference
                    precip_conc_out = get_component_conc_kg_m3(eff_stream, precip_id) or 0.0

                    # Precipitates are reported directly in mass (no COD conversion)
                    # TSS contribution equals mass (inorganics have i_COD=0)
                    precipitate_data[precip_id] = {
                        "formation_kg_d": formation_kg_d,
                        "formation_kg_TSS_d": formation_kg_d,  # For inorganics, mass = TSS
                        "rate_kg_m3_d": rate_kg_m3_d,
                        "concentration_out_kg_m3": precip_conc_out
                    }

                    total_precip_kg_d += formation_kg_d
                    total_precip_tss_kg_d += formation_kg_d
        else:
            logger.warning(f"Process rates array too short ({len(process_rates)}), cannot extract precipitation rates")

        # Calculate overall yields
        overall_VSS_yield = total_biomass_VSS_kg_d / COD_removed_kg_d if COD_removed_kg_d > 0 else 0.0
        overall_TSS_yield = (total_biomass_TSS_kg_d + total_precip_tss_kg_d) / COD_removed_kg_d if COD_removed_kg_d > 0 else 0.0
        biomass_TSS_yield = total_biomass_TSS_kg_d / COD_removed_kg_d if COD_removed_kg_d > 0 else 0.0
        precipitate_TSS_yield = total_precip_tss_kg_d / COD_removed_kg_d if COD_removed_kg_d > 0 else 0.0

        logger.info(f"Biomass yields calculated: VSS={overall_VSS_yield:.4f}, TSS={overall_TSS_yield:.4f} kg/kg COD")
        logger.info(f"Precipitate formation: {total_precip_kg_d:.2f} kg/d ({len(precipitate_data)} species active)")

        return {
            "success": True,
            "overall": {
                "VSS_yield_kg_per_kg_COD": overall_VSS_yield,
                "TSS_yield_kg_per_kg_COD": overall_TSS_yield,
                "biomass_TSS_yield": biomass_TSS_yield,
                "precipitate_TSS_yield": precipitate_TSS_yield,
                "COD_removed_kg_d": COD_removed_kg_d,
                "total_biomass_VSS_kg_d": total_biomass_VSS_kg_d,
                "total_biomass_TSS_kg_d": total_biomass_TSS_kg_d
            },
            "per_functional_group": per_group_yields,
            "precipitates": precipitate_data,
            "total_precipitate_formation_kg_d": total_precip_kg_d,
            "total_precipitate_formation_kg_TSS_d": total_precip_tss_kg_d
        }

    except Exception as e:
        logger.error(f"Error calculating net biomass yields: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Exception during yield calculation: {str(e)}"
        }


def calculate_srb_yield(inf, eff):
    """
    Calculate SRB biomass yield (kg VSS/kg COD removed).

    Parameters
    ----------
    inf : WasteStream
        Influent stream
    eff : WasteStream
        Effluent stream

    Returns
    -------
    float
        SRB yield in kg VSS/kg COD

    Notes
    -----
    SRB yield typically 0.05-0.15 kg VSS/kg COD (lower than aerobic bacteria).
    """
    try:
        # Get SRB biomass change
        X_SRB_in = get_component_conc_mg_L(inf, 'X_SRB')
        X_SRB_out = get_component_conc_mg_L(eff, 'X_SRB')

        if not X_SRB_in:
            X_SRB_in = 0.0
        if not X_SRB_out:
            X_SRB_out = 0.0

        # COD removed
        COD_in = inf.COD
        COD_out = eff.COD
        COD_removed = COD_in - COD_out

        if COD_removed > 1e-6:
            # Assume X_SRB is in COD units, need to convert to VSS
            # Typical VSS/COD ratio for biomass ≈ 0.9
            VSS_COD_ratio = 0.9
            SRB_yield = (X_SRB_out - X_SRB_in) * VSS_COD_ratio / COD_removed
            return max(0.0, SRB_yield)  # Only positive growth

        return 0.0

    except Exception as e:
        logger.warning(f"Error calculating SRB yield: {e}")
        return 0.0


def analyze_liquid_stream(stream, include_components=False):
    """
    Analyze liquid stream for ADM1+sulfur model.

    Native implementation for 30-component system (27 ADM1 + 3 sulfur).

    Parameters
    ----------
    stream : WasteStream
        Liquid stream (influent or effluent)
    include_components : bool, optional
        Include all 30 component concentrations (default False)

    Returns
    -------
    dict
        Complete ADM1+sulfur metrics including:
        - Standard composites (COD, TSS, VSS, TKN, TP, alkalinity)
        - Sulfur section (sulfate, total_sulfide, srb_biomass)
        - All 30 components (if include_components=True)
    """
    # Get base ADM1 analysis
    result = _analyze_liquid_stream_core(stream, include_components=include_components)

    if not result.get('success', False):
        return result

    # Add sulfur metrics
    try:
        S_SO4 = get_component_conc_mg_L(stream, 'S_SO4')
        S_IS = get_component_conc_mg_L(stream, 'S_IS')
        X_SRB = get_component_conc_mg_L(stream, 'X_SRB')

        result['sulfur'] = {
            "sulfate": S_SO4 if S_SO4 else 0.0,  # mg S/L
            "total_sulfide": S_IS if S_IS else 0.0,  # mg S/L
            "srb_biomass": X_SRB if X_SRB else 0.0  # mg COD/L
        }

    except Exception as e:
        logger.warning(f"Could not add sulfur metrics to liquid stream: {e}")
        result['sulfur'] = {
            "sulfate": 0.0,
            "total_sulfide": 0.0,
            "srb_biomass": 0.0
        }

    return result


def analyze_gas_stream(stream, inf_stream=None, eff_stream=None):
    """
    Analyze biogas stream for ADM1+sulfur model.

    Native implementation for 30-component system (27 ADM1 + 3 sulfur).

    Parameters
    ----------
    stream : WasteStream
        Biogas stream
    inf_stream : WasteStream, optional
        Influent stream (required for methane yield calculation)
    eff_stream : WasteStream, optional
        Effluent stream (required for methane yield calculation)

    Returns
    -------
    dict
        Complete biogas metrics including:
        - Gas flows (total, CH4, CO2, H2)
        - Gas composition (CH4%, CO2%, H2%)
        - H2S content (ppm)
        - methane_yield_m3_kg_cod: Specific methane yield (if influent/effluent provided)
        - methane_yield_efficiency_percent: % of theoretical (0.35 m3/kg COD)
    """
    # Get base ADM1 gas analysis
    result = _analyze_gas_stream_core(stream)

    if not result.get('success', False):
        return result

    # Add H2S
    try:
        h2s_ppm = calculate_h2s_gas_ppm(stream)
        result['h2s_ppm'] = h2s_ppm

        logger.debug(f"Biogas analysis: CH4={result.get('methane_percent', 0):.1f}%, "
                    f"H2S={h2s_ppm:.1f} ppm")

    except Exception as e:
        logger.warning(f"Could not add H2S to gas stream: {e}")
        result['h2s_ppm'] = 0.0

    # Calculate methane yield if influent/effluent provided
    if inf_stream is not None and eff_stream is not None:
        try:
            methane_flow = result.get('methane_flow', 0)  # Nm³/d at STP (FIXED)
            inf_cod = inf_stream.COD  # mg/L
            eff_cod = eff_stream.COD  # mg/L
            flow = inf_stream.F_vol * 24  # m3/d (convert m3/hr to m3/d)

            cod_removed_kg_d = (inf_cod - eff_cod) * flow / 1000  # kg/d

            if cod_removed_kg_d > 0:
                methane_yield = methane_flow / cod_removed_kg_d  # Nm³ CH4/kg COD at STP (FIXED)
                theoretical_yield = 0.35  # Nm³ CH4/kg COD at STP
                efficiency_pct = (methane_yield / theoretical_yield) * 100

                result['methane_yield_m3_kg_cod'] = methane_yield
                result['methane_yield_m3_kg_cod_units'] = 'Nm3 CH4/kg COD removed (STP)'  # FIXED
                result['methane_yield_theoretical'] = theoretical_yield
                result['methane_yield_efficiency_percent'] = efficiency_pct

                logger.info(f"Methane yield: {methane_yield:.4f} m3/kg COD ({efficiency_pct:.1f}% of theoretical)")
            else:
                result['methane_yield_m3_kg_cod'] = None
                result['methane_yield_efficiency_percent'] = None

        except Exception as e:
            logger.warning(f"Could not calculate methane yield: {e}")
            result['methane_yield_m3_kg_cod'] = None
            result['methane_yield_efficiency_percent'] = None

    return result


def analyze_inhibition(sim_results, speciation=None):
    """
    Analyze inhibition for ADM1+sulfur model.

    Native implementation for 30-component system (27 ADM1 + 3 sulfur).

    Parameters
    ----------
    sim_results : tuple
        (sys, inf, eff, gas, ...) from simulation
    speciation : dict, optional
        Pre-calculated H2S speciation from calculate_sulfur_metrics().
        If provided, avoids recalculation. If None, will calculate.

    Returns
    -------
    dict
        Complete inhibition analysis including:
        - H₂S inhibition (acetoclastic and hydrogenotrophic methanogens)
        - Inhibition factors sorted by severity
        - Recommendations for mitigation

    Notes
    -----
    **Performance optimization**: Pass speciation from calculate_sulfur_metrics()
    to avoid recalculating the Henderson-Hasselbalch equilibrium.
    """
    try:
        # Get base ADM1 inhibition analysis
        inhibition = _analyze_inhibition_core(sim_results)

        if not inhibition.get('success', False):
            return inhibition

        # Extract streams from results
        if len(sim_results) >= 4:
            _, inf, eff, gas = sim_results[:4]
        else:
            logger.warning("Simulation results tuple incomplete for inhibition analysis")
            return inhibition

        # Get pH (needed for recommendations regardless of speciation source)
        pH = getattr(eff, 'pH', 7.0)

        # Use provided speciation or calculate if not provided
        if speciation is None:
            # Calculate speciation
            S_IS_total_kg_m3 = get_component_conc_kg_m3(eff, 'S_IS')
            if not S_IS_total_kg_m3:
                S_IS_total_kg_m3 = 0.0

            speciation = calculate_h2s_speciation(S_IS_total_kg_m3, pH, input_units='kg/m3')

        H2S_dissolved_kg_m3 = speciation['H2S_dissolved_kg_m3']

        # Calculate inhibition factors
        KI_h2s_ac = H2S_INHIBITION['KI_h2s_ac']  # kg S/m3
        KI_h2s_h2 = H2S_INHIBITION['KI_h2s_h2']  # kg S/m3

        # CRITICAL: Both concentrations must be in kg/m3!
        I_ac = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_ac)
        I_h2 = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_h2)

        # Convert to inhibition percentage
        inhibition_pct_ac = (1 - I_ac) * 100
        inhibition_pct_h2 = (1 - I_h2) * 100

        # Add to inhibition factors list
        # Use mg/L for reporting
        H2S_dissolved_mg_L = speciation['H2S_dissolved_mg_L']

        inhibition['inhibition_factors'].extend([
            {
                "type": "H₂S Inhibition (Acetoclastic)",
                "value": inhibition_pct_ac,
                "concentration": H2S_dissolved_mg_L,  # mg S/L for reporting
                "concentration_units": "mg S/L",
                "KI": KI_h2s_ac
            },
            {
                "type": "H₂S Inhibition (Hydrogenotrophic)",
                "value": inhibition_pct_h2,
                "concentration": H2S_dissolved_mg_L,  # mg S/L for reporting
                "concentration_units": "mg S/L",
                "KI": KI_h2s_h2
            }
        ])

        # Re-sort by inhibition value
        inhibition['inhibition_factors'].sort(key=lambda x: x.get("value", 0), reverse=True)

        # Add H2S-specific recommendations if significant inhibition
        if inhibition_pct_ac > 10 or inhibition_pct_h2 > 10:
            h2s_recommendations = [
                f"H₂S Inhibition Detected (Acetoclastic: {inhibition_pct_ac:.1f}%, Hydrogenotrophic: {inhibition_pct_h2:.1f}%)",
                f"H₂S concentration: {H2S_dissolved_mg_L:.4f} mg S/L at pH {pH:.2f}",
                "Consider reducing sulfate loading or enhancing H₂S stripping",
                "Monitor sulfate-to-COD ratio (ideally < 0.5 kg SO4/kg COD)",
                "Consider pH adjustment to shift H₂S/HS⁻ equilibrium (higher pH reduces H₂S)"
            ]

            if 'recommendations' in inhibition:
                inhibition['recommendations'].extend(h2s_recommendations)
            else:
                inhibition['recommendations'] = h2s_recommendations

        logger.info(f"H2S inhibition analysis: ac={inhibition_pct_ac:.1f}%, h2={inhibition_pct_h2:.1f}%")

        return inhibition

    except Exception as e:
        logger.error(f"Error in H2S inhibition analysis: {e}")
        # Return base inhibition if extension fails
        try:
            return _analyze_inhibition_core(sim_results)
        except:
            return {
                "success": False,
                "message": f"Error in inhibition analysis: {e}"
            }


def extract_diagnostics(system):
    """
    Extract comprehensive diagnostic data from mADM1 simulation.

    This extracts ALL available diagnostic metrics from the mADM1 process model's
    root.data dictionary, including:
    - Complete inhibition profile (pH, H2, H2S, nutrients)
    - Biomass concentrations (12 functional groups)
    - Substrate limitation (Monod factors)
    - Process rates (63 processes)
    - Precipitation rates (13 minerals)
    - Speciation data (pH, NH3, CO2, H2S)

    Parameters
    ----------
    system : qsdsan.System
        QSDsan system containing the AnaerobicCSTRmADM1 reactor

    Returns
    -------
    dict
        Comprehensive diagnostic data with the following structure:
        {
            "success": bool,
            "message": str (if error),
            "speciation": {
                "pH": float,
                "nh3_M": float,
                "co2_M": float,
                "h2s_M": float
            },
            "inhibition": {
                "I_pH": {
                    "acidogens": float,
                    "acetoclastic": float,
                    "hydrogenotrophic": float,
                    "SRB_h2": float,
                    "SRB_ac": float,
                    "SRB_aa": float
                },
                "I_h2": {
                    "LCFA": float,
                    "C4_valerate": float,
                    "C4_butyrate": float,
                    "propionate": float
                },
                "I_h2s": {
                    "C4_valerate": float,
                    "C4_butyrate": float,
                    "propionate": float,
                    "acetate": float,
                    "hydrogen": float,
                    "SRB_h2": float,
                    "SRB_ac": float,
                    "SRB_prop": float,
                    "SRB_bu": float,
                    "SRB_va": float
                },
                "I_nutrients": {
                    "I_IN_lim": float,
                    "I_IP_lim": float,
                    "combined": float,
                    "I_nh3": float
                }
            },
            "biomass_kg_m3": {
                "X_su": float,      # Sugar degraders
                "X_aa": float,      # Amino acid degraders
                "X_fa": float,      # LCFA degraders
                "X_c4": float,      # Valerate/butyrate degraders
                "X_pro": float,     # Propionate degraders
                "X_ac": float,      # Acetoclastic methanogens
                "X_h2": float,      # Hydrogenotrophic methanogens
                "X_PAO": float,     # Polyphosphate accumulating organisms
                "X_hSRB": float,    # H2-utilizing SRB
                "X_aSRB": float,    # Acetate-utilizing SRB
                "X_pSRB": float,    # Propionate-utilizing SRB
                "X_c4SRB": float    # C4-utilizing SRB
            },
            "substrate_limitation": {
                "Monod": [...]      # 8 Monod factors for substrate limitation
            },
            "process_rates": [...]  # 63 process rates (kg COD/m³/d)
        }

    Notes
    -----
    This function accesses diagnostic hooks set up in utils/qsdsan_madm1.py
    lines 878-943. The diagnostic data is populated during the rate function
    calculation and stored in params['root'].data.

    If the reactor doesn't have diagnostic data available (e.g., if using
    standard ADM1 instead of mADM1), returns success=False with explanation.

    Examples
    --------
    >>> sys, inf, eff, gas, t, status = run_simulation_sulfur(basis, adm1_state, HRT)
    >>> diagnostics = extract_diagnostics(sys)
    >>> if diagnostics['success']:
    ...     print(f"pH: {diagnostics['speciation']['pH']:.2f}")
    ...     print(f"Methanogen biomass: {diagnostics['biomass_kg_m3']['X_ac']:.3f} kg/m³")
    ...     print(f"NH3 inhibition: {diagnostics['inhibition']['I_nutrients']['I_nh3']:.3f}")
    """
    try:
        # Find the anaerobic digester reactor
        ad_reactor = None
        for unit in system.units:
            if hasattr(unit, 'ID') and unit.ID == 'AD':
                ad_reactor = unit
                break

        if ad_reactor is None:
            return {
                "success": False,
                "message": "Anaerobic digester reactor (ID='AD') not found in system"
            }

        # Access the mADM1 model
        if not hasattr(ad_reactor, 'model'):
            return {
                "success": False,
                "message": "Reactor does not have a 'model' attribute"
            }

        model = ad_reactor.model

        # Access rate function parameters
        if not hasattr(model, 'rate_function'):
            return {
                "success": False,
                "message": "Model does not have a 'rate_function' attribute"
            }

        rate_function = model.rate_function

        # Get params dictionary
        if not hasattr(rate_function, 'params'):
            return {
                "success": False,
                "message": "Rate function does not have 'params' attribute"
            }

        params = rate_function.params

        # Get root object
        root = params.get('root')
        if root is None:
            return {
                "success": False,
                "message": "params['root'] not found - diagnostic hooks may not be set up"
            }

        # Get diagnostic data
        if not hasattr(root, 'data'):
            return {
                "success": False,
                "message": "root.data not found - diagnostic data not populated during simulation"
            }

        # Extract and return diagnostic data
        diagnostic_data = root.data

        # Validate that it has expected structure
        if not isinstance(diagnostic_data, dict):
            return {
                "success": False,
                "message": f"root.data is not a dictionary (type: {type(diagnostic_data)})"
            }

        # Build structured result
        result = {
            "success": True,
            "speciation": {
                "pH": diagnostic_data.get('pH'),
                "nh3_M": diagnostic_data.get('nh3_M'),
                "co2_M": diagnostic_data.get('co2_M'),
                "h2s_M": diagnostic_data.get('h2s_M')
            },
            "inhibition": {
                "I_pH": diagnostic_data.get('I_pH', {}),
                "I_h2": diagnostic_data.get('I_h2', {}),
                "I_h2s": diagnostic_data.get('I_h2s', {}),
                "I_nutrients": diagnostic_data.get('I_nutrients', {})
            },
            "biomass_kg_m3": diagnostic_data.get('biomass_kg_m3', {}),
            "substrate_limitation": {
                "Monod": diagnostic_data.get('Monod', [])
            },
            "process_rates": diagnostic_data.get('process_rates', [])
        }

        logger.info("Successfully extracted diagnostic data from mADM1 simulation")
        logger.info(f"  pH: {result['speciation']['pH']:.2f}")
        logger.info(f"  Biomass groups: {len(result['biomass_kg_m3'])} functional groups")
        logger.info(f"  Process rates: {len(result['process_rates'])} processes tracked")

        return result

    except Exception as e:
        logger.error(f"Error extracting diagnostic data: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Exception during diagnostic extraction: {str(e)}"
        }
