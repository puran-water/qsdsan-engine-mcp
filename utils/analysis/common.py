"""
Common stream analysis utilities shared between anaerobic and aerobic models.

Provides:
- Concentration getters (kg/m³ and mg/L)
- Safe attribute accessors
- Component property helpers
"""

import logging

logger = logging.getLogger(__name__)

# Constants
_HOURS_PER_DAY = 24.0
_STD_MOLAR_VOLUME_M3_PER_KMOL = 22.414  # Ideal molar volume at STP
_SULFUR_MOLAR_MASS_KG_PER_KMOL = 32.065


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

    Use for kinetic calculations (e.g., inhibition functions).

    Parameters
    ----------
    stream : WasteStream
        QSDsan stream object
    component_id : str
        Component ID (e.g., 'S_IS', 'S_NH4')

    Returns
    -------
    float or None
        Concentration in kg/m³, or None if component not found
    """
    try:
        if component_id not in stream.components.IDs:
            return None

        if stream.F_vol > 0:
            return stream.imass[component_id] / stream.F_vol
        else:
            return 0.0
    except:
        return None


def get_component_conc_mg_L(stream, component_id):
    """
    Get component concentration in mg/L.

    Use for reporting and display.

    Parameters
    ----------
    stream : WasteStream
        QSDsan stream object
    component_id : str
        Component ID (e.g., 'S_IS', 'S_NH4')

    Returns
    -------
    float or None
        Concentration in mg/L, or None if component not found
    """
    conc_kg_m3 = get_component_conc_kg_m3(stream, component_id)
    if conc_kg_m3 is not None:
        return conc_kg_m3 * 1000
    return None


def get_component_conc(stream, component_id, units='mg/L'):
    """Legacy function - use get_component_conc_mg_L() or get_component_conc_kg_m3()."""
    if units == 'kg/m3':
        return get_component_conc_kg_m3(stream, component_id)
    else:
        return get_component_conc_mg_L(stream, component_id)


def calculate_stream_ph(stream):
    """
    Get stream pH from stored attribute or default.

    Returns the pH attribute set by simulation, defaulting to 7.0.
    """
    if not hasattr(stream, 'F_vol') or stream.F_vol <= 0:
        return 7.0
    return round(float(getattr(stream, '_pH', 7.0)), 2)


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


def get_component_f_vmass(stream, component_id, biomass_ids=None, precipitate_ids=None):
    """
    Get f_Vmass_Totmass property (volatile fraction) for a component.

    Returns 0.0 for inorganic precipitates, ~0.85 for biomass.
    """
    try:
        if hasattr(stream, 'components'):
            cmps = stream.components
            if hasattr(cmps, component_id):
                cmp = getattr(cmps, component_id)
                if hasattr(cmp, 'f_Vmass_Totmass'):
                    return float(cmp.f_Vmass_Totmass)

        # Fallback logic
        if precipitate_ids and component_id in precipitate_ids:
            return 0.0
        elif biomass_ids and component_id in biomass_ids:
            return 0.85

    except Exception as e:
        logger.debug(f"Could not get f_Vmass for {component_id}: {e}")

    return 0.85  # Default for biomass


def calculate_removal_efficiency(conc_in, conc_out):
    """
    Calculate removal efficiency as percentage.

    Parameters
    ----------
    conc_in : float
        Influent concentration
    conc_out : float
        Effluent concentration

    Returns
    -------
    float
        Removal efficiency in percent (0-100)
    """
    if conc_in is None or conc_out is None:
        return 0.0
    if conc_in <= 0:
        return 0.0
    return max(0.0, (1 - conc_out / conc_in) * 100)


def calculate_mass_flow(concentration_mg_L, flow_m3_d):
    """
    Calculate mass flow from concentration and volumetric flow.

    Parameters
    ----------
    concentration_mg_L : float
        Concentration in mg/L
    flow_m3_d : float
        Volumetric flow in m³/d

    Returns
    -------
    float
        Mass flow in kg/d
    """
    if concentration_mg_L is None or flow_m3_d is None:
        return 0.0
    return concentration_mg_L * flow_m3_d / 1000  # mg/L * m³/d / 1000 = kg/d


def analyze_stream_basics(stream, include_components=False):
    """
    Get basic stream properties common to all models.

    Parameters
    ----------
    stream : WasteStream
        QSDsan stream object
    include_components : bool
        If True, include all component concentrations

    Returns
    -------
    dict
        Basic stream properties: flow, temperature, pH, COD, TSS, VSS, etc.
    """
    try:
        result = {
            "success": True,
            "flow_m3_d": stream.F_vol * _HOURS_PER_DAY if hasattr(stream, 'F_vol') else 0,
            "temperature_K": stream.T if hasattr(stream, 'T') else 293.15,
            "pH": calculate_stream_ph(stream),
            "COD_mg_L": stream.COD if hasattr(stream, 'COD') else 0,
            "TSS_mg_L": stream.get_TSS() if hasattr(stream, 'get_TSS') else 0,
            "VSS_mg_L": stream.get_VSS() if hasattr(stream, 'get_VSS') else 0,
        }

        # Add TKN if available
        if hasattr(stream, 'TKN'):
            result['TKN_mg_L'] = stream.TKN

        # Add TP if available
        tp = safe_composite(stream, 'P')
        if tp is not None:
            result['TP_mg_L'] = tp

        # Add alkalinity if available
        if hasattr(stream, 'SAlk'):
            result['alkalinity_mg_CaCO3_L'] = stream.SAlk * 50

        if include_components:
            result["components"] = {}
            for comp_id in stream.components.IDs:
                result["components"][comp_id] = get_component_conc_mg_L(stream, comp_id)

        return result

    except Exception as e:
        logger.error(f"Error analyzing stream basics: {e}")
        return {"success": False, "error": str(e)}
