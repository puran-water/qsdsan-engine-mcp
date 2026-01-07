"""
Aerobic stream analysis for ASM2d (Activated Sludge Model 2d) simulation.

Provides:
- analyze_aerobic_stream() - Analyze influent/effluent for ASM2d
- analyze_aerobic_performance() - Calculate removal efficiencies
- calculate_nitrogen_removal() - TN, NH4, NO3 removal metrics
- calculate_phosphorus_removal() - TP, PO4 removal metrics
- calculate_srt() - Solids Retention Time
- calculate_hrt() - Hydraulic Retention Time
"""

import logging
from typing import Dict, Any, Optional, List

from .common import (
    get_component_conc_kg_m3,
    get_component_conc_mg_L,
    calculate_removal_efficiency,
    calculate_mass_flow,
    analyze_stream_basics,
    _HOURS_PER_DAY,
)

logger = logging.getLogger(__name__)

# ASM2d Component IDs
ASM2D_COMPONENTS = (
    'S_O2', 'S_F', 'S_A', 'S_I', 'S_NH4', 'S_N2', 'S_NO3', 'S_PO4', 'S_ALK',
    'X_I', 'X_S', 'X_H', 'X_PAO', 'X_PP', 'X_PHA', 'X_AUT', 'H2O',
)

ASM2D_BIOMASS_IDS = ('X_H', 'X_PAO', 'X_AUT')

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
# Uses QSDsan's ASM2d which includes X_MeOH/X_MeP for chemical P removal
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


def analyze_aerobic_stream(stream, include_components: bool = False) -> Dict[str, Any]:
    """
    Analyze liquid stream for ASM2d model.

    Parameters
    ----------
    stream : WasteStream
        Liquid stream (influent or effluent)
    include_components : bool
        Include all component concentrations

    Returns
    -------
    dict
        Stream metrics including nitrogen, phosphorus, and biomass
    """
    result = analyze_stream_basics(stream, include_components=include_components)

    if not result.get('success', False):
        return result

    try:
        # Nitrogen species
        result['nitrogen'] = {
            'NH4_mg_N_L': get_component_conc_mg_L(stream, 'S_NH4') or 0.0,
            'NO3_mg_N_L': get_component_conc_mg_L(stream, 'S_NO3') or 0.0,
            'N2_mg_N_L': get_component_conc_mg_L(stream, 'S_N2') or 0.0,
        }
        # TKN from stream if available
        if hasattr(stream, 'TKN'):
            result['nitrogen']['TKN_mg_N_L'] = stream.TKN

        # Phosphorus species
        result['phosphorus'] = {
            'PO4_mg_P_L': get_component_conc_mg_L(stream, 'S_PO4') or 0.0,
            'PP_mg_P_L': get_component_conc_mg_L(stream, 'X_PP') or 0.0,
            'PHA_mg_COD_L': get_component_conc_mg_L(stream, 'X_PHA') or 0.0,
        }

        # Biomass
        result['biomass'] = {
            'X_H_mg_COD_L': get_component_conc_mg_L(stream, 'X_H') or 0.0,
            'X_PAO_mg_COD_L': get_component_conc_mg_L(stream, 'X_PAO') or 0.0,
            'X_AUT_mg_COD_L': get_component_conc_mg_L(stream, 'X_AUT') or 0.0,
        }
        result['biomass']['total_mg_COD_L'] = sum(result['biomass'].values())

        # Substrate
        result['substrate'] = {
            'S_F_mg_COD_L': get_component_conc_mg_L(stream, 'S_F') or 0.0,
            'S_A_mg_COD_L': get_component_conc_mg_L(stream, 'S_A') or 0.0,
            'X_S_mg_COD_L': get_component_conc_mg_L(stream, 'X_S') or 0.0,
        }

        # DO if available
        do = get_component_conc_mg_L(stream, 'S_O2')
        if do is not None:
            result['DO_mg_L'] = do

    except Exception as e:
        logger.warning(f"Error adding ASM2d metrics: {e}")

    return result


def calculate_nitrogen_removal(inf_stream, eff_stream) -> Dict[str, Any]:
    """
    Calculate nitrogen removal metrics.

    Parameters
    ----------
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream

    Returns
    -------
    dict
        Nitrogen removal metrics including TN, NH4, nitrification efficiency
    """
    try:
        # Get concentrations
        nh4_in = get_component_conc_mg_L(inf_stream, 'S_NH4') or 0.0
        nh4_out = get_component_conc_mg_L(eff_stream, 'S_NH4') or 0.0
        no3_in = get_component_conc_mg_L(inf_stream, 'S_NO3') or 0.0
        no3_out = get_component_conc_mg_L(eff_stream, 'S_NO3') or 0.0
        n2_out = get_component_conc_mg_L(eff_stream, 'S_N2') or 0.0

        # TKN if available
        tkn_in = inf_stream.TKN if hasattr(inf_stream, 'TKN') else nh4_in
        tkn_out = eff_stream.TKN if hasattr(eff_stream, 'TKN') else nh4_out

        # Calculate TN (TKN + NO3)
        tn_in = tkn_in + no3_in
        tn_out = tkn_out + no3_out

        # Removal efficiencies
        nh4_removal = calculate_removal_efficiency(nh4_in, nh4_out)
        tn_removal = calculate_removal_efficiency(tn_in, tn_out)

        # Nitrification efficiency: NH4 converted to NO3
        nh4_nitrified = nh4_in - nh4_out
        nitrification_eff = (nh4_nitrified / nh4_in * 100) if nh4_in > 0 else 0.0

        # Denitrification: NO3 converted to N2
        # NO3 produced = NH4 nitrified, NO3 remaining in effluent
        no3_produced = max(0, nh4_nitrified)
        no3_denitrified = max(0, no3_produced - (no3_out - no3_in))
        denitrification_eff = (no3_denitrified / no3_produced * 100) if no3_produced > 0 else 0.0

        return {
            "success": True,
            "influent": {
                "NH4_mg_N_L": nh4_in,
                "NO3_mg_N_L": no3_in,
                "TKN_mg_N_L": tkn_in,
                "TN_mg_N_L": tn_in,
            },
            "effluent": {
                "NH4_mg_N_L": nh4_out,
                "NO3_mg_N_L": no3_out,
                "TKN_mg_N_L": tkn_out,
                "TN_mg_N_L": tn_out,
                "N2_mg_N_L": n2_out,
            },
            "removal": {
                "NH4_removal_pct": nh4_removal,
                "TN_removal_pct": tn_removal,
                "nitrification_efficiency_pct": nitrification_eff,
                "denitrification_efficiency_pct": denitrification_eff,
            },
        }

    except Exception as e:
        logger.error(f"Error calculating nitrogen removal: {e}")
        return {"success": False, "error": str(e)}


def calculate_phosphorus_removal(inf_stream, eff_stream) -> Dict[str, Any]:
    """
    Calculate phosphorus removal metrics.

    Parameters
    ----------
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream

    Returns
    -------
    dict
        Phosphorus removal metrics including TP, PO4, EBPR indicators
    """
    try:
        # Get concentrations
        po4_in = get_component_conc_mg_L(inf_stream, 'S_PO4') or 0.0
        po4_out = get_component_conc_mg_L(eff_stream, 'S_PO4') or 0.0
        pp_in = get_component_conc_mg_L(inf_stream, 'X_PP') or 0.0
        pp_out = get_component_conc_mg_L(eff_stream, 'X_PP') or 0.0
        pao_out = get_component_conc_mg_L(eff_stream, 'X_PAO') or 0.0
        pha_out = get_component_conc_mg_L(eff_stream, 'X_PHA') or 0.0

        # Estimate TP (simplified: PO4 + organic P in biomass)
        # More accurate would use stream.composite('P')
        tp_in = po4_in + pp_in * 0.3  # Approximate P content of PP
        tp_out = po4_out + pp_out * 0.3

        # Removal efficiencies
        po4_removal = calculate_removal_efficiency(po4_in, po4_out)
        tp_removal = calculate_removal_efficiency(tp_in, tp_out)

        # EBPR indicators
        ebpr_active = pao_out > 50 and pp_out > 10  # Significant PAO and PP in effluent

        return {
            "success": True,
            "influent": {
                "PO4_mg_P_L": po4_in,
                "TP_mg_P_L": tp_in,
            },
            "effluent": {
                "PO4_mg_P_L": po4_out,
                "TP_mg_P_L": tp_out,
                "PP_mg_P_L": pp_out,
                "PAO_mg_COD_L": pao_out,
                "PHA_mg_COD_L": pha_out,
            },
            "removal": {
                "PO4_removal_pct": po4_removal,
                "TP_removal_pct": tp_removal,
            },
            "ebpr": {
                "active": ebpr_active,
                "PAO_biomass_mg_COD_L": pao_out,
                "PP_storage_mg_P_L": pp_out,
            },
        }

    except Exception as e:
        logger.error(f"Error calculating phosphorus removal: {e}")
        return {"success": False, "error": str(e)}


def calculate_srt(system, was_stream) -> Dict[str, Any]:
    """
    Calculate Solids Retention Time (SRT) for aerobic system.

    SRT = Total biomass in system / Biomass leaving in WAS

    Parameters
    ----------
    system : qsdsan.System
        QSDsan system
    was_stream : WasteStream
        Waste Activated Sludge stream

    Returns
    -------
    dict
        SRT calculation with breakdown
    """
    try:
        # Sum biomass in all reactors
        total_biomass_kg = 0.0
        reactor_volumes = {}

        for unit in system.units:
            if hasattr(unit, 'V_max') and hasattr(unit, '_state'):
                V = unit.V_max
                reactor_volumes[unit.ID] = V

                # Get biomass concentration from unit state
                for biomass_id in ASM2D_BIOMASS_IDS:
                    try:
                        idx = list(unit.components.IDs).index(biomass_id)
                        conc_kg_m3 = unit._state[idx]
                        total_biomass_kg += conc_kg_m3 * V
                    except (ValueError, IndexError):
                        pass

        # Biomass leaving in WAS (kg/d)
        was_flow_m3_d = was_stream.F_vol * _HOURS_PER_DAY if hasattr(was_stream, 'F_vol') else 0
        was_tss = was_stream.get_TSS() if hasattr(was_stream, 'get_TSS') else 0
        was_biomass_kg_d = was_tss * was_flow_m3_d / 1000  # mg/L * m3/d / 1000 = kg/d

        # SRT = biomass in system / biomass leaving
        srt_days = total_biomass_kg / was_biomass_kg_d if was_biomass_kg_d > 0 else float('inf')

        return {
            "success": True,
            "SRT_days": srt_days,
            "total_biomass_kg": total_biomass_kg,
            "was_biomass_kg_d": was_biomass_kg_d,
            "reactor_volumes_m3": reactor_volumes,
        }

    except Exception as e:
        logger.error(f"Error calculating SRT: {e}")
        return {"success": False, "error": str(e)}


def calculate_hrt(system, influent_stream) -> Dict[str, Any]:
    """
    Calculate Hydraulic Retention Time (HRT) for aerobic system.

    HRT = Total volume / Influent flow

    Parameters
    ----------
    system : qsdsan.System
        QSDsan system
    influent_stream : WasteStream
        Influent stream

    Returns
    -------
    dict
        HRT calculation with breakdown by zone
    """
    try:
        total_volume = 0.0
        zone_volumes = {'anoxic': 0.0, 'aerobic': 0.0, 'mbr': 0.0}

        for unit in system.units:
            if hasattr(unit, 'V_max'):
                V = unit.V_max
                total_volume += V

                # Classify zone by aeration
                if hasattr(unit, 'aeration'):
                    if unit.aeration is None or unit.aeration == 0:
                        zone_volumes['anoxic'] += V
                    else:
                        if 'MBR' in unit.ID.upper():
                            zone_volumes['mbr'] += V
                        else:
                            zone_volumes['aerobic'] += V
                elif 'MBR' in unit.ID.upper():
                    zone_volumes['mbr'] += V

        # Influent flow
        flow_m3_d = influent_stream.F_vol * _HOURS_PER_DAY if hasattr(influent_stream, 'F_vol') else 0

        # HRT in hours
        hrt_hours = (total_volume / flow_m3_d * 24) if flow_m3_d > 0 else 0

        return {
            "success": True,
            "HRT_hours": hrt_hours,
            "HRT_days": hrt_hours / 24,
            "total_volume_m3": total_volume,
            "zone_volumes_m3": zone_volumes,
            "flow_m3_d": flow_m3_d,
        }

    except Exception as e:
        logger.error(f"Error calculating HRT: {e}")
        return {"success": False, "error": str(e)}


def analyze_aerobic_performance(
    inf_stream,
    eff_stream,
    system=None,
    was_stream=None,
) -> Dict[str, Any]:
    """
    Comprehensive aerobic MBR performance analysis.

    Parameters
    ----------
    inf_stream : WasteStream
        Influent stream
    eff_stream : WasteStream
        Effluent stream
    system : qsdsan.System, optional
        QSDsan system (for SRT/HRT)
    was_stream : WasteStream, optional
        Waste activated sludge stream (for SRT)

    Returns
    -------
    dict
        Comprehensive performance metrics
    """
    try:
        # COD removal
        cod_in = inf_stream.COD if hasattr(inf_stream, 'COD') else 0
        cod_out = eff_stream.COD if hasattr(eff_stream, 'COD') else 0
        cod_removal = calculate_removal_efficiency(cod_in, cod_out)

        # BOD removal (if available)
        bod_in = inf_stream.BOD if hasattr(inf_stream, 'BOD') else None
        bod_out = eff_stream.BOD if hasattr(eff_stream, 'BOD') else None
        bod_removal = calculate_removal_efficiency(bod_in, bod_out) if bod_in else None

        # Nitrogen removal
        n_metrics = calculate_nitrogen_removal(inf_stream, eff_stream)

        # Phosphorus removal
        p_metrics = calculate_phosphorus_removal(inf_stream, eff_stream)

        # TSS/VSS
        tss_in = inf_stream.get_TSS() if hasattr(inf_stream, 'get_TSS') else 0
        tss_out = eff_stream.get_TSS() if hasattr(eff_stream, 'get_TSS') else 0
        tss_removal = calculate_removal_efficiency(tss_in, tss_out)

        result = {
            "success": True,
            "cod": {
                "influent_mg_L": cod_in,
                "effluent_mg_L": cod_out,
                "removal_pct": cod_removal,
            },
            "tss": {
                "influent_mg_L": tss_in,
                "effluent_mg_L": tss_out,
                "removal_pct": tss_removal,
            },
            "nitrogen": n_metrics if n_metrics.get('success') else {"error": n_metrics.get('error')},
            "phosphorus": p_metrics if p_metrics.get('success') else {"error": p_metrics.get('error')},
        }

        if bod_removal is not None:
            result['bod'] = {
                "influent_mg_L": bod_in,
                "effluent_mg_L": bod_out,
                "removal_pct": bod_removal,
            }

        # Add SRT/HRT if system provided
        if system is not None:
            hrt = calculate_hrt(system, inf_stream)
            result['hrt'] = hrt if hrt.get('success') else {"error": hrt.get('error')}

            if was_stream is not None:
                srt = calculate_srt(system, was_stream)
                result['srt'] = srt if srt.get('success') else {"error": srt.get('error')}

        return result

    except Exception as e:
        logger.error(f"Error in aerobic performance analysis: {e}")
        return {"success": False, "error": str(e)}
