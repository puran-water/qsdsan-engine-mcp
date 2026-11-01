"""
Anaerobic stream analysis for mADM1 (Modified ADM1) simulation.

Provides:
- analyze_liquid_stream() - Analyze influent/effluent with sulfur metrics
- analyze_gas_stream() - Analyze biogas with H2S
- analyze_biomass_yields() - COD removal and biomass production
- calculate_sulfur_metrics() - Comprehensive sulfur analysis
- calculate_h2s_speciation() - H2S/HS- equilibrium
- calculate_h2s_gas_ppm() - H2S concentration in biogas

Process-health / per-process inhibition analysis is now provided by the
``utils.analysis.process_diagnostics`` module (PR-DD). This module no
longer exposes ``analyze_inhibition`` or ``extract_diagnostics``.
"""

import logging
from qsdsan_engine_mcp.core.results.gas import analyze_gas_stream, calculate_h2s_gas_ppm
from qsdsan_engine_mcp.core.qsdsan_compat import ensure_biosteam_tictoc

ensure_biosteam_tictoc()
from qsdsan.process_models._adm1 import non_compet_inhibit

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
    from qsdsan_engine_mcp.models.sulfur_kinetics import H2S_INHIBITION
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
    'X_Siderite',  # Iron carbonate
    'X_FeS', 'X_Mackinawite',  # Distinct iron-sulfide histories
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

    # the house definition both digester engines read (process_model_schema.speciation "sulfide_1", Millero's
    # evaluated curve; HOUSE-MADM1-ACID-CONSTANTS-EVALUATED-LITERATURE-20261008), at the given temperature (was 7.0
    # held flat)
    from puran_mcp_support.schema.speciation import ACID_DISSOCIATION_CONSTANTS

    pKa_H2S = ACID_DISSOCIATION_CONSTANTS["sulfide_1"].pka(float(temperature_K))
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
        # at the effluent's own temperature where the stream carries one (the digester's), else the 35 C default
        t_eff = getattr(eff, 'T', None)
        t_eff = float(t_eff) if isinstance(t_eff, (int, float)) and t_eff > 0 else 308.15
        speciation = calculate_h2s_speciation(S_IS_total_kg_m3, pH, temperature_K=t_eff, input_units='kg/m3')
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

        if system is None:
            result["note"] = "System object required for detailed yield calculation"

        return result

    except Exception as e:
        logger.error(f"Error calculating biomass yields: {e}")
        return {"success": False, "error": str(e)}
