"""
Anaerobic stream analysis for mADM1 (Modified ADM1) simulation.

Provides:
- analyze_liquid_stream() - Analyze influent/effluent with sulfur metrics
- analyze_gas_stream() - Analyze biogas with H2S
- analyze_inhibition() - Complete inhibition including H2S effects
- analyze_biomass_yields() - COD removal and biomass production
- calculate_sulfur_metrics() - Comprehensive sulfur analysis
- calculate_h2s_speciation() - H2S/HS- equilibrium
- calculate_h2s_gas_ppm() - H2S concentration in biogas
- extract_diagnostics() - Extract mADM1 diagnostic data
"""

import logging
from qsdsan.processes._adm1 import non_compet_inhibit

from .common import (
    get_component_conc_kg_m3,
    get_component_conc_mg_L,
    calculate_stream_ph,
    safe_composite,
    get_component_i_mass,
    get_component_f_vmass,
    analyze_stream_basics,
    _HOURS_PER_DAY,
    _STD_MOLAR_VOLUME_M3_PER_KMOL,
    _SULFUR_MOLAR_MASS_KG_PER_KMOL,
)

logger = logging.getLogger(__name__)

# Import H2S inhibition constants
try:
    from models.sulfur_kinetics import H2S_INHIBITION
except ImportError:
    # Fallback if module not available
    H2S_INHIBITION = {
        'KI_h2s_ac': 0.0016,  # kg S/m3 for acetoclastic
        'KI_h2s_h2': 0.0064,  # kg S/m3 for hydrogenotrophic
    }

# Component classification constants for mADM1
MADM1_BIOMASS_IDS = (
    'X_su', 'X_aa', 'X_fa', 'X_c4', 'X_pro', 'X_ac', 'X_h2',  # Core degraders & methanogens
    'X_PAO',  # Polyphosphate accumulators
    'X_PHA',  # Storage polymer
    'X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB',  # Sulfate reducers
)


def _empty_sulfur_metrics(reason: str = "not computed") -> dict:
    """Return empty sulfur metrics dict with success=False and reason."""
    return {
        "success": False,
        "reason": reason,
        "sulfate_in_mg_L": 0.0,
        "sulfate_out_mg_L": 0.0,
        "sulfate_removal_pct": 0.0,
        "sulfate_in_kg_S_d": 0.0,
        "sulfate_out_kg_S_d": 0.0,
        "sulfide_total_mg_L": 0.0,
        "sulfide_out_kg_S_d": 0.0,
        "H2S_dissolved_mg_L": 0.0,
        "H2S_dissolved_kg_m3": 0.0,
        "HS_dissolved_mg_L": 0.0,
        "fraction_H2S": 0.0,
        "pH": 7.0,
        "h2s_biogas_ppm": 0.0,
        "h2s_biogas_percent": 0.0,
        "h2s_biogas_kg_S_d": 0.0,
        "srb_biomass_mg_COD_L": 0.0,
        "inhibition_acetoclastic_pct": 0.0,
        "inhibition_acetoclastic_factor": 1.0,
        "inhibition_hydrogenotrophic_pct": 0.0,
        "inhibition_hydrogenotrophic_factor": 1.0,
        "KI_h2s_acetoclastic": H2S_INHIBITION['KI_h2s_ac'],
        "KI_h2s_hydrogenotrophic": H2S_INHIBITION['KI_h2s_h2'],
        "speciation": {},
    }

MADM1_PRECIPITATE_IDS = (
    'X_struv', 'X_newb', 'X_kstruv',  # Struvites
    'X_HAP', 'X_ACP', 'X_DCPD', 'X_OCP',  # Calcium phosphates
    'X_Fe3PO42', 'X_AlPO4',  # Iron/aluminum phosphates
    'X_ACC', 'X_CCM', 'X_magn',  # Carbonates
    'X_FeS',  # Sulfides
    'X_HFO_H', 'X_HFO_L', 'X_HFO_old',
    'X_HFO_HP', 'X_HFO_LP', 'X_HFO_HP_old', 'X_HFO_LP_old',
)


def calculate_h2s_speciation(S_IS_total, pH, temperature_K=308.15, input_units='kg/m3'):
    """
    Calculate H2S/HS- speciation using Henderson-Hasselbalch equation.

    H2S <=> H+ + HS-

    Parameters
    ----------
    S_IS_total : float
        Total dissolved sulfide concentration
    pH : float
        pH of the solution
    temperature_K : float
        Temperature in K (default 308.15 = 35°C)
    input_units : str
        Units of S_IS_total: 'kg/m3' or 'mg/L'

    Returns
    -------
    dict
        H2S_dissolved_kg_m3, H2S_dissolved_mg_L, HS_dissolved_kg_m3,
        HS_dissolved_mg_L, fraction_H2S, pKa
    """
    if input_units == 'mg/L':
        S_IS_total_kg_m3 = S_IS_total / 1000
    else:
        S_IS_total_kg_m3 = S_IS_total

    pKa_H2S = 7.0  # ~7.0 at 35°C
    fraction_H2S = 1.0 / (1.0 + 10**(pH - pKa_H2S))
    fraction_HS = 1.0 - fraction_H2S

    H2S_dissolved_kg_m3 = S_IS_total_kg_m3 * fraction_H2S
    HS_dissolved_kg_m3 = S_IS_total_kg_m3 * fraction_HS

    return {
        'H2S_dissolved_kg_m3': H2S_dissolved_kg_m3,
        'H2S_dissolved_mg_L': H2S_dissolved_kg_m3 * 1000,
        'HS_dissolved_kg_m3': HS_dissolved_kg_m3,
        'HS_dissolved_mg_L': HS_dissolved_kg_m3 * 1000,
        'fraction_H2S': fraction_H2S,
        'pKa': pKa_H2S
    }


def calculate_h2s_gas_ppm(gas_stream):
    """
    Calculate H2S concentration in biogas (ppmv).

    Parameters
    ----------
    gas_stream : WasteStream
        Biogas stream from simulation

    Returns
    -------
    float
        H2S concentration in ppmv
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
            h2s_mol_hr = 0.0

        if h2s_mol_hr <= 0:
            return 0.0

        mol_fraction = h2s_mol_hr / total_mol_hr
        return mol_fraction * 1e6

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
        Flat dictionary with sulfur metrics including:
        - Sulfate mass balance
        - H2S/HS- speciation
        - Biogas H2S content
        - SRB performance
        - H2S inhibition factors
    """
    # Validate streams have sulfur components
    if 'S_SO4' not in inf.components.IDs:
        logger.warning("Influent missing S_SO4 - returning empty sulfur metrics")
        return _empty_sulfur_metrics("missing S_SO4 in influent")
    if 'S_IS' not in eff.components.IDs:
        logger.warning("Effluent missing S_IS - returning empty sulfur metrics")
        return _empty_sulfur_metrics("missing S_IS in effluent")

    # Handle zero flow gracefully
    if inf.F_vol <= 0 or eff.F_vol <= 0:
        logger.warning("Liquid streams have zero flow - returning empty sulfur metrics")
        return _empty_sulfur_metrics("zero liquid flow")

    try:
        # Get concentrations
        S_SO4_in_mg_L = get_component_conc_mg_L(inf, 'S_SO4') or 0.0
        S_SO4_out_mg_L = get_component_conc_mg_L(eff, 'S_SO4') or 0.0
        S_IS_total_mg_L = get_component_conc_mg_L(eff, 'S_IS') or 0.0
        S_IS_total_kg_m3 = get_component_conc_kg_m3(eff, 'S_IS') or 0.0

        # Sum disaggregated SRB biomass
        X_SRB_total = 0.0
        for srb_id in ['X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB']:
            srb_conc = get_component_conc_mg_L(eff, srb_id)
            if srb_conc:
                X_SRB_total += srb_conc
        if X_SRB_total < 1e-6:
            X_SRB_lumped = get_component_conc_mg_L(eff, 'X_SRB')
            X_SRB_total = X_SRB_lumped if X_SRB_lumped else 0.0

        pH = getattr(eff, 'pH', 7.0)

        # Sulfate removal
        sulfate_removal = 0.0
        if S_SO4_in_mg_L > 1e-6:
            sulfate_removal = (1 - S_SO4_out_mg_L / S_SO4_in_mg_L) * 100

        # Mass flows
        Q_inf_m3_d = inf.F_vol * _HOURS_PER_DAY
        Q_eff_m3_d = eff.F_vol * _HOURS_PER_DAY

        sulfate_in_kg_S_d = S_SO4_in_mg_L * Q_inf_m3_d / 1000
        sulfate_out_kg_S_d = S_SO4_out_mg_L * Q_eff_m3_d / 1000
        sulfide_out_kg_S_d = S_IS_total_mg_L * Q_eff_m3_d / 1000

        # H2S in biogas
        if hasattr(gas, 'imol') and 'S_IS' in gas.components.IDs:
            try:
                h2s_mol_hr = gas.imol['S_IS']
            except Exception:
                h2s_mol_hr = 0.0
            h2s_biogas_kg_S_d = h2s_mol_hr * _SULFUR_MOLAR_MASS_KG_PER_KMOL * _HOURS_PER_DAY
        else:
            h2s_biogas_kg_S_d = 0.0

        # Speciation
        speciation = calculate_h2s_speciation(S_IS_total_kg_m3, pH, input_units='kg/m3')
        H2S_dissolved_kg_m3 = speciation['H2S_dissolved_kg_m3']
        H2S_dissolved_mg_L = speciation['H2S_dissolved_mg_L']

        # Biogas H2S ppm
        h2s_ppm = calculate_h2s_gas_ppm(gas)
        h2s_percent = h2s_ppm / 10000.0

        # H2S inhibition
        KI_h2s_ac = H2S_INHIBITION['KI_h2s_ac']
        KI_h2s_h2 = H2S_INHIBITION['KI_h2s_h2']

        I_ac = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_ac)
        I_h2 = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_h2)

        inhibition_pct_ac = (1 - I_ac) * 100
        inhibition_pct_h2 = (1 - I_h2) * 100

        return {
            "success": True,
            "sulfate_in_mg_L": S_SO4_in_mg_L,
            "sulfate_out_mg_L": S_SO4_out_mg_L,
            "sulfate_removal_pct": sulfate_removal,
            "sulfate_in_kg_S_d": sulfate_in_kg_S_d,
            "sulfate_out_kg_S_d": sulfate_out_kg_S_d,
            "sulfide_total_mg_L": S_IS_total_mg_L,
            "sulfide_out_kg_S_d": sulfide_out_kg_S_d,
            "H2S_dissolved_mg_L": H2S_dissolved_mg_L,
            "H2S_dissolved_kg_m3": H2S_dissolved_kg_m3,
            "HS_dissolved_mg_L": speciation['HS_dissolved_mg_L'],
            "fraction_H2S": speciation['fraction_H2S'],
            "pH": pH,
            "h2s_biogas_ppm": h2s_ppm,
            "h2s_biogas_percent": h2s_percent,
            "h2s_biogas_kg_S_d": h2s_biogas_kg_S_d,
            "srb_biomass_mg_COD_L": X_SRB_total,
            "inhibition_acetoclastic_pct": inhibition_pct_ac,
            "inhibition_acetoclastic_factor": I_ac,
            "inhibition_hydrogenotrophic_pct": inhibition_pct_h2,
            "inhibition_hydrogenotrophic_factor": I_h2,
            "KI_h2s_acetoclastic": KI_h2s_ac,
            "KI_h2s_hydrogenotrophic": KI_h2s_h2,
            "speciation": speciation,
        }

    except Exception as e:
        logger.error(f"Error calculating sulfur metrics: {e}")
        return {"success": False, "error": str(e)}


def analyze_liquid_stream(stream, include_components=False):
    """
    Analyze liquid stream for mADM1 model.

    Parameters
    ----------
    stream : WasteStream
        Liquid stream (influent or effluent)
    include_components : bool
        Include all component concentrations

    Returns
    -------
    dict
        Stream metrics including sulfur section
    """
    result = analyze_stream_basics(stream, include_components=include_components)

    if not result.get('success', False):
        return result

    # Add sulfur metrics
    try:
        S_SO4 = get_component_conc_mg_L(stream, 'S_SO4')
        S_IS = get_component_conc_mg_L(stream, 'S_IS')

        X_SRB_total = 0.0
        for srb_id in ['X_hSRB', 'X_aSRB', 'X_pSRB', 'X_c4SRB']:
            srb_conc = get_component_conc_mg_L(stream, srb_id)
            if srb_conc:
                X_SRB_total += srb_conc
        if X_SRB_total < 1e-6:
            X_SRB_lumped = get_component_conc_mg_L(stream, 'X_SRB')
            X_SRB_total = X_SRB_lumped if X_SRB_lumped else 0.0

        result['sulfur'] = {
            "sulfate_mg_S_L": S_SO4 if S_SO4 else 0.0,
            "sulfide_mg_S_L": S_IS if S_IS else 0.0,
            "srb_biomass_mg_COD_L": X_SRB_total,
        }

    except Exception as e:
        logger.warning(f"Could not add sulfur metrics: {e}")
        result['sulfur'] = {"sulfate_mg_S_L": 0.0, "sulfide_mg_S_L": 0.0, "srb_biomass_mg_COD_L": 0.0}

    return result


def _safe_get_imol(stream, component_id, default=0.0):
    """Safely get molar flow for a component, returning default if not found."""
    try:
        return stream.imol[component_id]
    except (KeyError, IndexError):
        return default


def analyze_gas_stream(stream, inf_stream=None, eff_stream=None):
    """
    Analyze biogas stream for mADM1 model.

    Parameters
    ----------
    stream : WasteStream
        Biogas stream
    inf_stream : WasteStream, optional
        Influent stream (for methane yield)
    eff_stream : WasteStream, optional
        Effluent stream (for methane yield)

    Returns
    -------
    dict
        Biogas metrics including H2S and methane yield
    """
    try:
        STP_MOLAR_VOLUME = 22.414  # L/mol at STP

        if stream.F_mol > 0:
            ch4_mol = _safe_get_imol(stream, 'S_ch4')
            co2_mol = _safe_get_imol(stream, 'S_IC')
            h2_mol = _safe_get_imol(stream, 'S_h2')

            ch4_flow = ch4_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24
            co2_flow = co2_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24
            h2_flow = h2_mol * 1000 * STP_MOLAR_VOLUME / 1000 * 24
            flow_total = ch4_flow + co2_flow + h2_flow

            ch4_pct = (_safe_get_imol(stream, 'S_ch4') / stream.F_mol * 100) if stream.F_mol > 0 else 0
            co2_pct = (_safe_get_imol(stream, 'S_IC') / stream.F_mol * 100) if stream.F_mol > 0 else 0
            h2_pct = (_safe_get_imol(stream, 'S_h2') / stream.F_mol * 100) if stream.F_mol > 0 else 0
        else:
            ch4_flow = co2_flow = h2_flow = flow_total = 0
            ch4_pct = co2_pct = h2_pct = 0

        result = {
            "success": True,
            "flow_total_Nm3_d": flow_total,
            "methane_flow_Nm3_d": ch4_flow,
            "methane_percent": ch4_pct,
            "co2_flow_Nm3_d": co2_flow,
            "co2_percent": co2_pct,
            "h2_flow_Nm3_d": h2_flow,
            "h2_percent": h2_pct,
            "h2s_ppm": calculate_h2s_gas_ppm(stream),
        }

        # Calculate methane yield if streams provided
        if inf_stream is not None and eff_stream is not None:
            try:
                inf_cod = inf_stream.COD
                eff_cod = eff_stream.COD
                flow = inf_stream.F_vol * 24
                cod_removed_kg_d = (inf_cod - eff_cod) * flow / 1000

                if cod_removed_kg_d > 0:
                    methane_yield = ch4_flow / cod_removed_kg_d
                    theoretical = 0.35
                    result['methane_yield_Nm3_kg_COD'] = methane_yield
                    result['methane_yield_efficiency_pct'] = (methane_yield / theoretical) * 100
            except Exception as e:
                logger.warning(f"Could not calculate methane yield: {e}")

        return result

    except Exception as e:
        logger.error(f"Error analyzing gas stream: {e}")
        return {"success": False, "error": str(e)}


def analyze_inhibition(sim_results, speciation=None):
    """
    Analyze inhibition for mADM1 model including H2S effects.

    Parameters
    ----------
    sim_results : tuple
        (sys, inf, eff, gas, ...) from simulation
    speciation : dict, optional
        Pre-calculated H2S speciation

    Returns
    -------
    dict
        Inhibition analysis with factors and recommendations
    """
    try:
        if len(sim_results) < 4:
            return {"success": False, "error": "Incomplete simulation results"}

        _, inf, eff, gas = sim_results[:4]
        pH = getattr(eff, 'pH', 7.0)

        if speciation is None:
            S_IS_total_kg_m3 = get_component_conc_kg_m3(eff, 'S_IS') or 0.0
            speciation = calculate_h2s_speciation(S_IS_total_kg_m3, pH, input_units='kg/m3')

        H2S_dissolved_kg_m3 = speciation['H2S_dissolved_kg_m3']
        H2S_dissolved_mg_L = speciation['H2S_dissolved_mg_L']

        KI_h2s_ac = H2S_INHIBITION['KI_h2s_ac']
        KI_h2s_h2 = H2S_INHIBITION['KI_h2s_h2']

        I_ac = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_ac)
        I_h2 = non_compet_inhibit(H2S_dissolved_kg_m3, KI_h2s_h2)

        inhibition_pct_ac = (1 - I_ac) * 100
        inhibition_pct_h2 = (1 - I_h2) * 100

        inhibition_factors = [
            {
                "type": "H2S (Acetoclastic)",
                "inhibition_pct": inhibition_pct_ac,
                "activity_factor": I_ac,
                "concentration_mg_L": H2S_dissolved_mg_L,
                "KI_kg_m3": KI_h2s_ac,
            },
            {
                "type": "H2S (Hydrogenotrophic)",
                "inhibition_pct": inhibition_pct_h2,
                "activity_factor": I_h2,
                "concentration_mg_L": H2S_dissolved_mg_L,
                "KI_kg_m3": KI_h2s_h2,
            },
        ]

        inhibition_factors.sort(key=lambda x: x['inhibition_pct'], reverse=True)

        recommendations = []
        if inhibition_pct_ac > 10 or inhibition_pct_h2 > 10:
            recommendations = [
                f"H2S inhibition detected: Acetoclastic {inhibition_pct_ac:.1f}%, Hydrogenotrophic {inhibition_pct_h2:.1f}%",
                f"H2S concentration: {H2S_dissolved_mg_L:.2f} mg S/L at pH {pH:.2f}",
                "Consider reducing sulfate loading or enhancing H2S stripping",
                "Higher pH shifts H2S/HS- equilibrium toward less toxic HS-",
            ]

        return {
            "success": True,
            "inhibition_factors": inhibition_factors,
            "recommendations": recommendations,
            "pH": pH,
        }

    except Exception as e:
        logger.error(f"Error in inhibition analysis: {e}")
        return {"success": False, "error": str(e)}


def analyze_biomass_yields(inf_stream, eff_stream, system=None):
    """
    Calculate biomass yields for mADM1 model.

    Parameters
    ----------
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream
    system : qsdsan.System, optional
        QSDsan system for detailed yield calculation

    Returns
    -------
    dict
        Yield data including VSS_yield, TSS_yield, COD_removal_efficiency
    """
    try:
        cod_in = inf_stream.COD if hasattr(inf_stream, 'COD') else 0
        cod_out = eff_stream.COD if hasattr(eff_stream, 'COD') else 0
        cod_removal = (1 - cod_out / cod_in) * 100 if cod_in > 0 else 0

        result = {
            "success": True,
            "COD_in_mg_L": cod_in,
            "COD_out_mg_L": cod_out,
            "COD_removal_efficiency_pct": cod_removal,
        }

        # Simplified yield estimate if system not provided
        # More detailed calculation would require extract_diagnostics()
        if system is None:
            result["note"] = "System object required for detailed yield calculation"

        return result

    except Exception as e:
        logger.error(f"Error calculating biomass yields: {e}")
        return {"success": False, "error": str(e)}


def extract_diagnostics(system):
    """
    Extract diagnostic data from mADM1 simulation.

    Parameters
    ----------
    system : qsdsan.System
        QSDsan system with AnaerobicCSTRmADM1 reactor

    Returns
    -------
    dict
        Diagnostic data including speciation, inhibition, biomass, process_rates
    """
    try:
        ad_reactor = None
        for unit in system.units:
            if hasattr(unit, 'ID') and unit.ID == 'AD':
                ad_reactor = unit
                break

        if ad_reactor is None:
            return {"success": False, "error": "Reactor 'AD' not found in system"}

        if not hasattr(ad_reactor, 'model'):
            return {"success": False, "error": "Reactor has no 'model' attribute"}

        model = ad_reactor.model
        if not hasattr(model, 'rate_function'):
            return {"success": False, "error": "Model has no 'rate_function' attribute"}

        params = getattr(model.rate_function, 'params', None)
        if params is None:
            return {"success": False, "error": "rate_function has no 'params'"}

        root = params.get('root')
        if root is None or not hasattr(root, 'data'):
            return {"success": False, "error": "Diagnostic data not available"}

        data = root.data
        return {
            "success": True,
            "speciation": {
                "pH": data.get('pH'),
                "nh3_M": data.get('nh3_M'),
                "co2_M": data.get('co2_M'),
                "h2s_M": data.get('h2s_M'),
            },
            "inhibition": {
                "I_pH": data.get('I_pH', {}),
                "I_h2": data.get('I_h2', {}),
                "I_h2s": data.get('I_h2s', {}),
                "I_nutrients": data.get('I_nutrients', {}),
            },
            "biomass_kg_m3": data.get('biomass_kg_m3', {}),
            "process_rates": data.get('process_rates', []),
        }

    except Exception as e:
        logger.error(f"Error extracting diagnostics: {e}")
        return {"success": False, "error": str(e)}
