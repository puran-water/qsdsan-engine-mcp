"""
Quarto Markdown Report Builder for QSDsan Simulation Results.

Generates *.qmd reports using Jinja2 templates from simulation results.
Reports are designed to be rendered with Quarto CLI.

Features:
- Professional engineering document styling
- KPI dashboards with status indicators
- Diagnostic panels with threshold warnings
- Stream comparison tables
- Process-specific sections (biogas, inhibition, nutrients)

Templates:
- anaerobic_report.qmd: mADM1 digestion with sulfur cycle
- aerobic_report.qmd: ASM2d MBR with nutrient removal
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from copy import deepcopy

logger = logging.getLogger(__name__)

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False

try:
    from utils.report_plots import (
        generate_convergence_plot,
        generate_nutrient_plot,
        generate_biogas_plot,
        generate_cod_plot,
        MATPLOTLIB_AVAILABLE,
    )
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    generate_convergence_plot = None
    generate_nutrient_plot = None
    generate_biogas_plot = None
    generate_cod_plot = None


__all__ = [
    'build_anaerobic_report',
    'build_aerobic_report',
    'build_report',
    'generate_report',
    'normalize_results_for_report',
    'render_template',
]

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _format_number(value: Any, decimals: int = 2) -> str:
    """Format number for display, handling None/NaN gracefully."""
    if value is None:
        return "N/A"
    try:
        num = float(value)
        if abs(num) < 0.01 and num != 0:
            return f"{num:.2e}"
        return f"{num:.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def _get_status(value: float, thresholds: Dict[str, float], invert: bool = False) -> str:
    """
    Determine status color based on value and thresholds.

    Parameters
    ----------
    value : float
        Current value
    thresholds : dict
        Dict with 'warn' and 'crit' keys
    invert : bool
        If True, lower is better (e.g., effluent concentration)

    Returns
    -------
    str
        Status: 'green', 'yellow', or 'red'
    """
    warn = thresholds.get('warn', float('inf') if not invert else 0)
    crit = thresholds.get('crit', float('inf') if not invert else 0)

    if invert:
        if value <= warn:
            return 'green'
        elif value <= crit:
            return 'yellow'
        else:
            return 'red'
    else:
        if value >= warn:
            return 'green'
        elif value >= crit:
            return 'yellow'
        else:
            return 'red'


def _get_kpi_class(status: str) -> str:
    """Convert status to KPI card CSS class."""
    return {
        'green': 'kpi-ok',
        'yellow': 'kpi-warn',
        'red': 'kpi-crit',
    }.get(status, 'kpi-ok')


def normalize_results_for_report(
    results: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Normalize simulation results to match template expectations.

    This function bridges the gap between various result producers
    (flowsheet_builder, anaerobic/aerobic templates) and the QMD template
    requirements. MUST be idempotent - safe to call multiple times.

    Handles:
    1. diagram_path -> flowsheet.diagram_path (verify file exists)
    2. timeseries_path -> timeseries (load JSON, handle relative paths)
    3. metadata.solver.* -> top-level duration_days, method
    4. effluent_quality -> effluent with expected field names
    5. removal_efficiency -> performance (with nested cod/nitrogen/phosphorus/srt)
    6. effluent_quality.sulfur -> sulfur (top-level for anaerobic)
    7. Default values for all template-required fields
    8. Guard flowsheet = None -> {}

    Parameters
    ----------
    results : dict
        Raw simulation results from any source
    output_dir : Path, optional
        Output directory for resolving relative paths (e.g., timeseries.json)

    Returns
    -------
    dict
        Normalized results ready for template rendering
    """
    # Work on a copy to avoid mutating original
    data = deepcopy(results)

    # 1. Guard against None flowsheet and normalize diagram_path
    if data.get("flowsheet") is None:
        data["flowsheet"] = {}
    flowsheet = data["flowsheet"]

    # Copy diagram_path from top-level to flowsheet if present
    if data.get("diagram_path") and not flowsheet.get("diagram_path"):
        diagram_path = Path(data["diagram_path"])
        # Resolve relative path against output_dir
        if not diagram_path.is_absolute() and output_dir:
            diagram_path = Path(output_dir) / diagram_path.name
        # Only set if file actually exists
        if diagram_path.exists():
            flowsheet["diagram_path"] = str(diagram_path)

    flowsheet["has_diagram"] = flowsheet.get("diagram_path") is not None
    flowsheet.setdefault("streams", [])
    flowsheet.setdefault("units", [])

    # 2. Load timeseries from path if not already loaded
    if data.get("timeseries_path") and not data.get("timeseries"):
        try:
            ts_path = Path(data["timeseries_path"])
            # Resolve relative path against output_dir
            if not ts_path.is_absolute() and output_dir:
                ts_path = Path(output_dir) / ts_path.name
            if ts_path.exists():
                with open(ts_path, "r") as f:
                    data["timeseries"] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load timeseries from {data.get('timeseries_path')}: {e}")
            data["timeseries"] = {}

    # Ensure timeseries exists
    data.setdefault("timeseries", {})

    # 3. Extract solver metadata to top-level
    solver = data.get("metadata", {}).get("solver", {})
    data.setdefault("duration_days", solver.get("duration_days", 0))
    data.setdefault("method", solver.get("method", "RK23"))
    data.setdefault("tolerance", str(solver.get("rtol", 1e-3)))

    # 4. Map effluent_quality to effluent with flattened structure
    effluent = data.setdefault("effluent", {})
    if "effluent_quality" in data:
        eq = data["effluent_quality"]
        # Basic stream properties
        effluent.setdefault("COD_mg_L", eq.get("COD_mg_L", 0))
        effluent.setdefault("TSS_mg_L", eq.get("TSS_mg_L", 0))
        effluent.setdefault("VSS_mg_L", eq.get("VSS_mg_L", 0))

        # Flatten nested nitrogen dict
        if "nitrogen" in eq:
            n = eq["nitrogen"]
            effluent.setdefault("NH4_mg_N_L", n.get("NH4_mg_N_L", 0))
            effluent.setdefault("NO3_mg_N_L", n.get("NO3_mg_N_L", 0))
            effluent.setdefault("N2_mg_N_L", n.get("N2_mg_N_L", 0))

        # Flatten nested phosphorus dict
        if "phosphorus" in eq:
            p = eq["phosphorus"]
            effluent.setdefault("PO4_mg_P_L", p.get("PO4_mg_P_L", 0))

        # 6. Map sulfur to top-level for anaerobic reports
        if "sulfur" in eq and "sulfur" not in data:
            data["sulfur"] = eq["sulfur"]

    # Ensure effluent defaults exist
    effluent.setdefault("NH4_mg_N_L", 0)
    effluent.setdefault("NO3_mg_N_L", 0)
    effluent.setdefault("PO4_mg_P_L", 0)
    effluent.setdefault("N2_mg_N_L", 0)
    effluent.setdefault("COD_mg_L", 0)
    effluent.setdefault("TSS_mg_L", 0)
    effluent.setdefault("VSS_mg_L", 0)

    # 5. Map removal_efficiency to performance with nested structure
    performance = data.setdefault("performance", {})
    if "removal_efficiency" in data:
        re = data["removal_efficiency"]
        # Map to nested structure expected by templates
        if "COD_removal_pct" in re and "cod" not in performance:
            performance["cod"] = {"removal_pct": re.get("COD_removal_pct", 0)}
        if "TN_removal_pct" in re and "nitrogen" not in performance:
            performance["nitrogen"] = {
                "tn_removal_pct": re.get("TN_removal_pct", 0),
                "nh4_removal_pct": re.get("NH4_removal_pct", 0),
                "no3_removal_pct": re.get("NO3_removal_pct", 0),
                "tn_in_mg_L": 0,
                "tn_out_mg_L": 0,
                "nh4_in_mg_L": 0,
                "nh4_out_mg_L": 0,
                "no3_in_mg_L": 0,
                "no3_out_mg_L": 0,
                "nitrification_rate": 0,
                "denitrification_rate": 0,
            }
        if "TP_removal_pct" in re and "phosphorus" not in performance:
            performance["phosphorus"] = {
                "tp_removal_pct": re.get("TP_removal_pct", 0),
                "tp_in_mg_L": 0,
                "tp_out_mg_L": 0,
                "po4_in_mg_L": 0,
                "po4_out_mg_L": 0,
                "po4_removal_pct": 0,
            }

    # Ensure performance nested defaults exist
    performance.setdefault("cod", {"removal_pct": 0})
    performance.setdefault("nitrogen", {
        "tn_removal_pct": 0,
        "nh4_removal_pct": 0,
        "no3_removal_pct": 0,
        "tn_in_mg_L": 0,
        "tn_out_mg_L": 0,
        "nh4_in_mg_L": 0,
        "nh4_out_mg_L": 0,
        "no3_in_mg_L": 0,
        "no3_out_mg_L": 0,
        "nitrification_rate": 0,
        "denitrification_rate": 0,
    })
    performance.setdefault("phosphorus", {
        "tp_removal_pct": 0,
        "tp_in_mg_L": 0,
        "tp_out_mg_L": 0,
        "po4_in_mg_L": 0,
        "po4_out_mg_L": 0,
        "po4_removal_pct": 0,
    })
    performance.setdefault("srt", {"SRT_days": 0})

    # Ensure performance nested keys have defaults
    performance["cod"].setdefault("removal_pct", 0)
    performance["nitrogen"].setdefault("tn_removal_pct", 0)
    performance["nitrogen"].setdefault("nh4_removal_pct", 0)
    performance["nitrogen"].setdefault("no3_removal_pct", 0)
    performance["nitrogen"].setdefault("tn_in_mg_L", 0)
    performance["nitrogen"].setdefault("tn_out_mg_L", 0)
    performance["nitrogen"].setdefault("nh4_in_mg_L", 0)
    performance["nitrogen"].setdefault("nh4_out_mg_L", 0)
    performance["nitrogen"].setdefault("no3_in_mg_L", 0)
    performance["nitrogen"].setdefault("no3_out_mg_L", 0)
    performance["nitrogen"].setdefault("nitrification_rate", 0)
    performance["nitrogen"].setdefault("denitrification_rate", 0)
    performance["phosphorus"].setdefault("tp_removal_pct", 0)
    performance["phosphorus"].setdefault("tp_in_mg_L", 0)
    performance["phosphorus"].setdefault("tp_out_mg_L", 0)
    performance["phosphorus"].setdefault("po4_in_mg_L", 0)
    performance["phosphorus"].setdefault("po4_out_mg_L", 0)
    performance["phosphorus"].setdefault("po4_removal_pct", 0)
    performance["srt"].setdefault("SRT_days", 0)
    # Additional top-level performance keys
    performance.setdefault("HRT_hours", 0)
    performance.setdefault("FM_ratio", 0)
    performance.setdefault("OTR_kg_O2_d", 0)
    performance.setdefault("COD_removal_pct", 0)
    performance.setdefault("specific_CH4_yield_m3_kg_COD", 0)
    performance.setdefault("OLR_kg_COD_m3_d", 0)

    # Ensure other required top-level keys exist
    data.setdefault("influent", {"flow_m3_d": 0})
    data["influent"].setdefault("flow_m3_d", 0)
    data.setdefault("reactor", {
        # Aerobic reactor volumes
        "V_anoxic_m3": 0,
        "V_aerobic_m3": 0,
        "V_mbr_m3": 0,
        "V_total_m3": 0,
        "DO_aerobic_mg_L": 0,
        "DO_mbr_mg_L": 0,
        # Recycle flows
        "Q_ras_m3_d": 0,
        "Q_ir_m3_d": 0,
        "Q_was_m3_d": 0,
        "RAS_ratio": 0,
        "IR_ratio": 0,
        # Anaerobic reactor params
        "V_liq_m3": 0,
        "V_gas_m3": 0,
        "temperature_C": 35,
        "HRT_days": 0,
    })
    # Ensure reactor nested keys have defaults
    reactor = data["reactor"]
    reactor.setdefault("V_anoxic_m3", 0)
    reactor.setdefault("V_aerobic_m3", 0)
    reactor.setdefault("V_mbr_m3", 0)
    reactor.setdefault("V_total_m3", 0)
    reactor.setdefault("DO_aerobic_mg_L", 0)
    reactor.setdefault("DO_mbr_mg_L", 0)
    reactor.setdefault("Q_ras_m3_d", 0)
    reactor.setdefault("Q_ir_m3_d", 0)
    reactor.setdefault("Q_was_m3_d", 0)
    reactor.setdefault("RAS_ratio", 0)
    reactor.setdefault("IR_ratio", 0)
    reactor.setdefault("V_liq_m3", 0)
    reactor.setdefault("V_gas_m3", 0)
    reactor.setdefault("temperature_C", 35)
    reactor.setdefault("HRT_days", 0)
    data.setdefault("biomass", {
        "X_PAO_mg_COD_L": 0,
        "X_PP_mg_P_L": 0,
        "X_PHA_mg_COD_L": 0,
        "X_H_mg_COD_L": 0,
        "X_AUT_mg_COD_L": 0,
        "X_S_mg_COD_L": 0,
        "X_I_mg_COD_L": 0,
        "MLSS_mg_L": 0,
        "MLVSS_mg_L": 0,
        "VSS_TSS_ratio": 0,
        "SVI_mL_g": 0,
    })
    # Ensure biomass nested keys have defaults
    biomass = data["biomass"]
    biomass.setdefault("X_PAO_mg_COD_L", 0)
    biomass.setdefault("X_PP_mg_P_L", 0)
    biomass.setdefault("X_PHA_mg_COD_L", 0)
    biomass.setdefault("X_H_mg_COD_L", 0)
    biomass.setdefault("X_AUT_mg_COD_L", 0)
    biomass.setdefault("X_S_mg_COD_L", 0)
    biomass.setdefault("X_I_mg_COD_L", 0)
    biomass.setdefault("MLSS_mg_L", 0)
    biomass.setdefault("MLVSS_mg_L", 0)
    biomass.setdefault("VSS_TSS_ratio", 0)
    biomass.setdefault("SVI_mL_g", 0)

    data.setdefault("biogas", {})
    data.setdefault("inhibition", {})
    data.setdefault("sulfur", {})
    data.setdefault("unit_analysis", {})
    data.setdefault("thresholds", {})
    data.setdefault("status", "unknown")

    return data


def _generate_anaerobic_plots(
    data: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Dict[str, str]:
    """
    Generate time-series plots for anaerobic reports.

    Parameters
    ----------
    data : dict
        Simulation result data (may include 'timeseries' key)
    output_path : Path, optional
        Report output path. Plots saved to plots/ subdirectory.

    Returns
    -------
    dict
        Dictionary with markdown image references or placeholders
    """
    default_plots = {
        'convergence_plot': '[No timeseries data available]',
        'state_variables_plot': '[No timeseries data available]',
    }

    if not MATPLOTLIB_AVAILABLE:
        default_plots['convergence_plot'] = '[matplotlib not available]'
        default_plots['state_variables_plot'] = '[matplotlib not available]'
        return default_plots

    timeseries = data.get('timeseries', {})
    if not timeseries or not output_path:
        return default_plots

    output_path = Path(output_path)
    plots_dir = output_path.parent / "plots"

    plots = {}

    # Generate convergence plot
    try:
        convergence_path = generate_convergence_plot(
            timeseries,
            plots_dir / "convergence.png",
            title="Simulation Convergence",
            components=["COD_mg_L", "S_ac", "S_pro", "S_ch4"],
        )
        if convergence_path:
            plots['convergence_plot'] = f"![Convergence](plots/{convergence_path.name})"
        else:
            plots['convergence_plot'] = default_plots['convergence_plot']
    except Exception as e:
        logger.warning(f"Could not generate convergence plot: {e}")
        plots['convergence_plot'] = f"[Plot generation failed: {e}]"

    # Generate biogas plot as state variables
    try:
        biogas_path = generate_biogas_plot(
            timeseries,
            plots_dir / "biogas.png",
            title="Biogas Production",
        )
        if biogas_path:
            plots['state_variables_plot'] = f"![Biogas Production](plots/{biogas_path.name})"
        else:
            # Fall back to COD plot
            cod_path = generate_cod_plot(
                timeseries,
                plots_dir / "cod.png",
                title="COD Trajectory",
            )
            if cod_path:
                plots['state_variables_plot'] = f"![COD Trajectory](plots/{cod_path.name})"
            else:
                plots['state_variables_plot'] = default_plots['state_variables_plot']
    except Exception as e:
        logger.warning(f"Could not generate state variables plot: {e}")
        plots['state_variables_plot'] = f"[Plot generation failed: {e}]"

    return plots


def _generate_aerobic_plots(
    data: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Dict[str, str]:
    """
    Generate time-series plots for aerobic reports.

    Parameters
    ----------
    data : dict
        Simulation result data (may include 'timeseries' key)
    output_path : Path, optional
        Report output path. Plots saved to plots/ subdirectory.

    Returns
    -------
    dict
        Dictionary with markdown image references or placeholders
    """
    default_plots = {
        'nutrient_plot': '[No timeseries data available]',
        'reactor_state_plot': '[No timeseries data available]',
    }

    if not MATPLOTLIB_AVAILABLE:
        default_plots['nutrient_plot'] = '[matplotlib not available]'
        default_plots['reactor_state_plot'] = '[matplotlib not available]'
        return default_plots

    timeseries = data.get('timeseries', {})
    if not timeseries or not output_path:
        return default_plots

    output_path = Path(output_path)
    plots_dir = output_path.parent / "plots"

    plots = {}

    # Generate nutrient plot
    try:
        nutrient_path = generate_nutrient_plot(
            timeseries,
            plots_dir / "nutrients.png",
            title="Nutrient Trajectories",
        )
        if nutrient_path:
            plots['nutrient_plot'] = f"![Nutrients](plots/{nutrient_path.name})"
        else:
            plots['nutrient_plot'] = default_plots['nutrient_plot']
    except Exception as e:
        logger.warning(f"Could not generate nutrient plot: {e}")
        plots['nutrient_plot'] = f"[Plot generation failed: {e}]"

    # Generate reactor state plot (convergence)
    try:
        convergence_path = generate_convergence_plot(
            timeseries,
            plots_dir / "convergence.png",
            title="Reactor State Convergence",
            components=["S_NH4", "S_NO3", "S_O2", "COD_mg_L", "TSS_mg_L"],
        )
        if convergence_path:
            plots['reactor_state_plot'] = f"![Reactor State](plots/{convergence_path.name})"
        else:
            plots['reactor_state_plot'] = default_plots['reactor_state_plot']
    except Exception as e:
        logger.warning(f"Could not generate reactor state plot: {e}")
        plots['reactor_state_plot'] = f"[Plot generation failed: {e}]"

    return plots


def _prepare_anaerobic_data(
    result: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Prepare anaerobic simulation data for template rendering.

    Transforms raw simulation output into template-friendly structure.
    Generates time-series plots if output_path provided and timeseries data available.

    Parameters
    ----------
    result : dict
        Simulation result dictionary
    output_path : Path, optional
        If provided, generate plots in plots/ subdirectory
    """
    # Normalize results first to ensure all required keys exist
    data = normalize_results_for_report(
        result,
        output_dir=output_path.parent if output_path else None
    )

    # Extract nested data (now guaranteed to exist after normalization)
    influent = data.get('influent', {})
    effluent = data.get('effluent', {})
    reactor = data.get('reactor', {})
    performance = data.get('performance', {})
    biogas = data.get('biogas', {})
    inhibition = data.get('inhibition', {})
    sulfur = data.get('sulfur', {})
    flowsheet = data.get('flowsheet', {})

    # Calculate VFA data from effluent if not present in inhibition
    # mADM1 tracks individual VFA species: S_va (valerate), S_bu (butyrate), S_pro (propionate), S_ac (acetate)
    if 'VFA' not in inhibition or inhibition.get('VFA') is None:
        eff_concs = effluent.get('concentrations', effluent)
        # VFA concentrations are already in mg/L in effluent (for mADM1, or mg COD/L)
        acetate = eff_concs.get('S_ac', 0) or 0
        propionate = eff_concs.get('S_pro', 0) or 0
        butyrate = eff_concs.get('S_bu', 0) or 0
        valerate = eff_concs.get('S_va', 0) or 0
        total_vfa = acetate + propionate + butyrate + valerate

        # Alkalinity from S_IC (inorganic carbon) - approximate as 50 mg CaCO3/L per mg C/L
        s_ic = eff_concs.get('S_IC', 0) or 0
        alkalinity = s_ic * 4.17  # Approximate alkalinity in mg CaCO3/L from S_IC in mg/L

        # VFA/Alkalinity ratio (dimensionless)
        vfa_alk_ratio = (total_vfa / alkalinity) if alkalinity > 0 else 0

        inhibition['VFA'] = {
            'acetate_mg_COD_L': acetate,
            'propionate_mg_COD_L': propionate,
            'butyrate_mg_COD_L': butyrate,
            'valerate_mg_COD_L': valerate,
            'total_VFA_mg_COD_L': total_vfa,
            'alkalinity_mg_CaCO3_L': alkalinity,
            'VFA_ALK_ratio': vfa_alk_ratio,
        }

    # Calculate sulfur data from effluent if not present
    # mADM1 tracks SRB (sulfate-reducing bacteria) biomass: X_hSRB, X_aSRB, X_pSRB, X_c4SRB
    eff_concs = effluent.get('concentrations', effluent)
    inf_concs = influent.get('concentrations', influent)

    # SRB biomass (from effluent)
    X_hSRB = eff_concs.get('X_hSRB', 0) or 0  # H2-oxidizing SRB
    X_aSRB = eff_concs.get('X_aSRB', 0) or 0  # Acetate-utilizing SRB
    X_pSRB = eff_concs.get('X_pSRB', 0) or 0  # Propionate-utilizing SRB
    X_c4SRB = eff_concs.get('X_c4SRB', 0) or 0  # Butyrate/valerate-utilizing SRB
    total_srb = X_hSRB + X_aSRB + X_pSRB + X_c4SRB

    # Sulfate in/out (S_SO4 is in mg S/L)
    sulfate_in = inf_concs.get('S_SO4', 0) or 0
    sulfate_out = eff_concs.get('S_SO4', 0) or 0
    sulfate_removal = ((sulfate_in - sulfate_out) / sulfate_in * 100) if sulfate_in > 0 else 0

    # Sulfide species (S_IS is total dissolved sulfide in mADM1)
    s_is = eff_concs.get('S_IS', 0) or 0  # Total inorganic sulfide

    # Update sulfur dict with calculated values if not already present
    if 'X_hSRB_mg_COD_L' not in sulfur:
        sulfur['X_hSRB_mg_COD_L'] = X_hSRB
    if 'X_aSRB_mg_COD_L' not in sulfur:
        sulfur['X_aSRB_mg_COD_L'] = X_aSRB
    if 'X_pSRB_mg_COD_L' not in sulfur:
        sulfur['X_pSRB_mg_COD_L'] = X_pSRB
    if 'X_c4SRB_mg_COD_L' not in sulfur:
        sulfur['X_c4SRB_mg_COD_L'] = X_c4SRB
    if 'srb_biomass_mg_COD_L' not in sulfur:
        sulfur['srb_biomass_mg_COD_L'] = total_srb
    if 'sulfate_in_mg_L' not in sulfur:
        sulfur['sulfate_in_mg_L'] = sulfate_in
    if 'sulfate_out_mg_L' not in sulfur:
        sulfur['sulfate_out_mg_L'] = sulfate_out
    if 'sulfate_removal_pct' not in sulfur:
        sulfur['sulfate_removal_pct'] = sulfate_removal
    if 'sulfide_total_mg_L' not in sulfur:
        sulfur['sulfide_total_mg_L'] = s_is
    if 'H2S_dissolved_mg_L' not in sulfur:
        # At neutral pH ~7, H2S is roughly 50% of total dissolved sulfide
        sulfur['H2S_dissolved_mg_L'] = s_is * 0.5
    if 'HS_dissolved_mg_L' not in sulfur:
        sulfur['HS_dissolved_mg_L'] = s_is * 0.5
    if 'h2s_biogas_ppm' not in sulfur:
        # Default to biogas value if available, otherwise estimate from dissolved
        sulfur['h2s_biogas_ppm'] = biogas.get('h2s_ppm', 0) or 0

    # Default thresholds
    thresholds = {
        'h2s_ppm': 500,
        'inhibition_pct': 20,
        'cod_removal_pct': {'warn': 80, 'crit': 60},
        'ch4_yield': {'warn': 0.25, 'crit': 0.15},
        'vfa_alk': {'warn': 0.3, 'crit': 0.4},
    }

    # Calculate diagnostics
    cod_removal = performance.get('COD_removal_pct', 0)
    h2s_ppm = biogas.get('h2s_ppm', 0)
    ch4_yield = performance.get('specific_CH4_yield_m3_kg_COD', 0)
    vfa_alk = inhibition.get('VFA', {}).get('VFA_ALK_ratio', 0)

    # Determine max inhibition
    inhib_factors = inhibition.get('inhibition_factors', [])
    max_inhib = max([f.get('inhibition_pct', 0) for f in inhib_factors], default=0)

    diagnostics = {
        'cod_status': _get_status(cod_removal, thresholds['cod_removal_pct']),
        'cod_class': _get_kpi_class(_get_status(cod_removal, thresholds['cod_removal_pct'])),
        'h2s_status': 'green' if h2s_ppm < 200 else ('yellow' if h2s_ppm < 500 else 'red'),
        'ch4_class': _get_kpi_class(_get_status(ch4_yield, thresholds['ch4_yield'])),
        'inhibition_status': 'green' if max_inhib < 10 else ('yellow' if max_inhib < 20 else 'red'),
        'max_inhibition_pct': max_inhib,
        'vfa_status': 'green' if vfa_alk < 0.3 else ('yellow' if vfa_alk < 0.4 else 'red'),
    }

    # Build stream comparison
    stream_comparison = []
    key_params = [
        ('COD', 'COD_mg_L', 'mg/L'),
        ('TSS', 'TSS_mg_L', 'mg/L'),
        ('VSS', 'VSS_mg_L', 'mg/L'),
        ('Sulfate', 'S_SO4_mg_L', 'mg S/L'),
    ]
    for name, key, unit in key_params:
        inf_val = influent.get(key, influent.get('concentrations', {}).get(key, 0)) or 0
        eff_val = effluent.get(key, effluent.get('concentrations', {}).get(key, 0)) or 0
        removal = ((inf_val - eff_val) / inf_val * 100) if inf_val > 0 else 0
        stream_comparison.append({
            'parameter': name,
            'influent': _format_number(inf_val),
            'effluent': _format_number(eff_val),
            'unit': unit,
            'removal_pct': _format_number(removal, 1),
        })

    # Prepare flowsheet data for diagram section
    flowsheet_data = {
        'streams': flowsheet.get('streams', []),
        'units': flowsheet.get('units', []),
        'diagram_path': flowsheet.get('diagram_path'),
        'has_diagram': flowsheet.get('diagram_path') is not None,
    }

    # Extract per-unit analysis data
    unit_analysis = data.get('unit_analysis', {})

    return {
        'influent': influent,
        'effluent': effluent,
        'reactor': reactor,
        'performance': performance,
        'biogas': biogas,
        'inhibition': inhibition,
        'sulfur': sulfur,
        'diagnostics': diagnostics,
        'thresholds': thresholds,
        'stream_comparison': stream_comparison,
        'flowsheet': flowsheet_data,
        'unit_analysis': unit_analysis,
        'simulation': {
            'status': data.get('status', 'unknown'),
            'converged_at_days': data.get('converged_at_days', 0),
            'duration_days': data.get('duration_days', 0),
            'tolerance': data.get('tolerance', '1e-3'),
        },
        'time_series': _generate_anaerobic_plots(data, output_path),
    }


def _prepare_aerobic_data(
    result: Dict[str, Any],
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Prepare aerobic simulation data for template rendering.

    Transforms raw simulation output into template-friendly structure.
    Generates time-series plots if output_path provided and timeseries data available.

    Parameters
    ----------
    result : dict
        Simulation result dictionary
    output_path : Path, optional
        If provided, generate plots in plots/ subdirectory
    """
    # Normalize results first to ensure all required keys exist
    data = normalize_results_for_report(
        result,
        output_dir=output_path.parent if output_path else None
    )

    # Extract nested data (now guaranteed to exist after normalization)
    influent = data.get('influent', {})
    effluent = data.get('effluent', {})
    reactor = data.get('reactor', {})
    performance = data.get('performance', {})
    biomass = data.get('biomass', {})
    flowsheet = data.get('flowsheet', {})

    # Default thresholds
    thresholds = {
        'nh4_mg_L': 5.0,
        'no3_mg_L': 10.0,
        'po4_mg_L': 1.0,
        'cod_removal_pct': {'warn': 90, 'crit': 80},
        'tn_removal_pct': {'warn': 80, 'crit': 60},
        'tp_removal_pct': {'warn': 80, 'crit': 60},
    }

    # Get nested performance data
    cod_perf = performance.get('cod', {})
    n_perf = performance.get('nitrogen', {})
    p_perf = performance.get('phosphorus', {})

    cod_removal = cod_perf.get('removal_pct', performance.get('COD_removal_pct', 0))
    tn_removal = n_perf.get('tn_removal_pct', performance.get('TN_removal_pct', 0))
    tp_removal = p_perf.get('tp_removal_pct', performance.get('TP_removal_pct', 0))

    nh4_eff = effluent.get('NH4_mg_N_L', 0)
    no3_eff = effluent.get('NO3_mg_N_L', 0)
    po4_eff = effluent.get('PO4_mg_P_L', 0)

    diagnostics = {
        'cod_status': _get_status(cod_removal, thresholds['cod_removal_pct']),
        'cod_class': _get_kpi_class(_get_status(cod_removal, thresholds['cod_removal_pct'])),
        'tn_class': _get_kpi_class(_get_status(tn_removal, thresholds['tn_removal_pct'])),
        'tp_class': _get_kpi_class(_get_status(tp_removal, thresholds['tp_removal_pct'])),
        'nh4_status': 'green' if nh4_eff < 2 else ('yellow' if nh4_eff < 5 else 'red'),
        'no3_status': 'green' if no3_eff < 5 else ('yellow' if no3_eff < 10 else 'red'),
        'po4_status': 'green' if po4_eff < 0.5 else ('yellow' if po4_eff < 1.0 else 'red'),
    }

    # Build stream comparison
    stream_comparison = []
    key_params = [
        ('COD', 'COD_mg_L', 'mg/L'),
        ('TSS', 'TSS_mg_L', 'mg/L'),
        ('NH4-N', 'NH4_mg_N_L', 'mg N/L'),
        ('NO3-N', 'NO3_mg_N_L', 'mg N/L'),
        ('PO4-P', 'PO4_mg_P_L', 'mg P/L'),
    ]
    for name, key, unit in key_params:
        inf_val = influent.get(key, influent.get('concentrations', {}).get(key, 0)) or 0
        eff_val = effluent.get(key, 0) or 0
        removal = ((inf_val - eff_val) / inf_val * 100) if inf_val > 0 else 0
        stream_comparison.append({
            'parameter': name,
            'influent': _format_number(inf_val),
            'effluent': _format_number(eff_val),
            'unit': unit,
            'removal_pct': _format_number(removal, 1),
        })

    # Prepare flowsheet data for diagram section
    flowsheet_data = {
        'streams': flowsheet.get('streams', []),
        'units': flowsheet.get('units', []),
        'diagram_path': flowsheet.get('diagram_path'),
        'has_diagram': flowsheet.get('diagram_path') is not None,
    }

    # Extract per-unit analysis data
    unit_analysis = data.get('unit_analysis', {})

    return {
        'influent': influent,
        'effluent': effluent,
        'reactor': reactor,
        'performance': performance,
        'biomass': biomass,
        'diagnostics': diagnostics,
        'thresholds': thresholds,
        'stream_comparison': stream_comparison,
        'flowsheet': flowsheet_data,
        'unit_analysis': unit_analysis,
        'simulation': {
            'status': data.get('status', 'unknown'),
            'converged_at_days': data.get('converged_at_days', 0),
            'duration_days': data.get('duration_days', 0),
            'method': data.get('method', 'RK23'),
        },
        'time_series': _generate_aerobic_plots(data, output_path),
    }


def render_template(
    template_name: str,
    data: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Render a Jinja2 template with provided data.

    Parameters
    ----------
    template_name : str
        Name of template file (e.g., 'anaerobic_report.qmd')
    data : dict
        Prepared data for template rendering
    meta : dict, optional
        Metadata for report header

    Returns
    -------
    str
        Rendered template content
    """
    if not JINJA2_AVAILABLE:
        raise ImportError("Jinja2 is required for template rendering. Install with: pip install jinja2")

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(['html', 'xml']),
    )

    # Add custom filters
    env.filters['round'] = lambda x, d=2: round(float(x), d) if x is not None else 0

    template = env.get_template(template_name)

    # Default metadata
    if meta is None:
        meta = {}
    meta.setdefault('report_date', datetime.now().strftime("%Y-%m-%d"))
    meta.setdefault('simulation_id', str(uuid.uuid4())[:8])
    meta.setdefault('template_name', template_name.replace('_report.qmd', ''))

    return template.render(data=data, meta=meta)


def build_anaerobic_report(
    result: Dict[str, Any],
    output_path: Optional[Path] = None,
    use_template: bool = True,
) -> str:
    """
    Build Quarto report for anaerobic digestion simulation.

    Parameters
    ----------
    result : dict
        Simulation result from templates/anaerobic/cstr.py
    output_path : Path, optional
        If provided, write report to file
    use_template : bool
        If True, use Jinja2 template; otherwise use legacy builder

    Returns
    -------
    str
        Complete Quarto Markdown content
    """
    if use_template and JINJA2_AVAILABLE:
        data = _prepare_anaerobic_data(result, output_path)
        meta = {
            'template_name': result.get('template', 'anaerobic_cstr_madm1'),
        }
        report = render_template('anaerobic_report.qmd', data, meta)
    else:
        # Legacy programmatic builder
        report = _build_anaerobic_legacy(result)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)

    return report


def build_aerobic_report(
    result: Dict[str, Any],
    output_path: Optional[Path] = None,
    use_template: bool = True,
) -> str:
    """
    Build Quarto report for aerobic MBR simulation.

    Parameters
    ----------
    result : dict
        Simulation result from templates/aerobic/*.py
    output_path : Path, optional
        If provided, write report to file
    use_template : bool
        If True, use Jinja2 template; otherwise use legacy builder

    Returns
    -------
    str
        Complete Quarto Markdown content
    """
    if use_template and JINJA2_AVAILABLE:
        data = _prepare_aerobic_data(result, output_path)
        meta = {
            'template_name': result.get('template', 'mle_mbr_asm2d'),
        }
        report = render_template('aerobic_report.qmd', data, meta)
    else:
        # Legacy programmatic builder
        report = _build_aerobic_legacy(result)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)

    return report


def build_report(
    result: Dict[str, Any],
    output_path: Optional[Path] = None,
    use_template: bool = True,
) -> str:
    """
    Build appropriate Quarto report based on simulation result type.

    Dispatcher function that routes to anaerobic or aerobic report builder.

    Parameters
    ----------
    result : dict
        Simulation result from any template
    output_path : Path, optional
        If provided, write report to file
    use_template : bool
        If True, use Jinja2 templates

    Returns
    -------
    str
        Complete Quarto Markdown content
    """
    template = result.get("template", "").lower()

    if "anaerobic" in template or "madm1" in template or "adm1" in template:
        return build_anaerobic_report(result, output_path, use_template)
    elif "mbr" in template or "asm2d" in template or "aerobic" in template:
        return build_aerobic_report(result, output_path, use_template)
    else:
        return build_aerobic_report(result, output_path, use_template)


def generate_report(
    session_id: str,
    model_type: str,
    results: Dict[str, Any],
    output_dir: Path,
) -> Path:
    """
    Generate report for flowsheet simulation results.

    This function is called by cli.py flowsheet simulate --report to generate
    Quarto Markdown reports from simulation results.

    Parameters
    ----------
    session_id : str
        Flowsheet session identifier
    model_type : str
        Process model type (e.g., "ASM2d", "mADM1", "ASM1")
    results : dict
        Simulation results dictionary
    output_dir : Path
        Directory to write report file

    Returns
    -------
    Path
        Path to the generated report.qmd file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "report.qmd"

    # Normalize model_type for comparison
    model_type_lower = model_type.lower() if model_type else ""

    # Determine template based on model_type
    if model_type_lower in ("madm1", "adm1", "modified_adm1"):
        build_anaerobic_report(results, output_path=output_path)
    else:
        # ASM2d, ASM1, and others use aerobic report format
        build_aerobic_report(results, output_path=output_path)

    return output_path


# =============================================================================
# LEGACY BUILDERS (fallback when Jinja2 not available)
# =============================================================================

def _build_anaerobic_legacy(result: Dict[str, Any]) -> str:
    """Legacy programmatic anaerobic report builder."""
    template = result.get("template", "anaerobic_cstr_madm1")
    influent = result.get("influent", {})
    effluent = result.get("effluent", {})
    reactor = result.get("reactor", {})
    performance = result.get("performance", {})
    biogas = result.get("biogas", {})

    flow = influent.get("flow_m3_d", 0)
    temp_c = influent.get("temperature_K", 308.15) - 273.15

    return f"""---
title: "Anaerobic Digester Simulation - {flow:.0f} m³/d"
date: {datetime.now().strftime("%Y-%m-%d")}
template: {template}
model: mADM1
flow_m3_d: {flow}
tags: [qsdsan, anaerobic, mADM1, simulation]
format:
  html:
    toc: true
    css: report.css
---

## Executive Summary

Anaerobic digester treating **{flow:.0f} m³/d** at **{temp_c:.1f}°C** using mADM1.

**Key Results:**
- COD Removal: {_format_number(performance.get('COD_removal_pct', 0))}%
- Methane Production: {_format_number(biogas.get('methane_flow_Nm3_d', 0))} Nm³/d
- HRT: {_format_number(reactor.get('HRT_days', 0))} days
- Status: {result.get('status', 'unknown')}

## Performance

| Metric | Value |
|--------|-------|
| COD Removal | {_format_number(performance.get('COD_removal_pct', 0))}% |
| OLR | {_format_number(performance.get('OLR_kg_COD_m3_d', 0))} kg COD/m³/d |
| CH4 Yield | {_format_number(performance.get('specific_CH4_yield_m3_kg_COD', 0))} Nm³/kg COD |

## Biogas

| Component | Value |
|-----------|-------|
| Total Flow | {_format_number(biogas.get('flow_total_Nm3_d', 0))} Nm³/d |
| Methane | {_format_number(biogas.get('methane_percent', 0))}% |
| CO2 | {_format_number(biogas.get('co2_percent', 0))}% |
| H2S | {_format_number(biogas.get('h2s_ppm', 0))} ppm |
"""


def _build_aerobic_legacy(result: Dict[str, Any]) -> str:
    """Legacy programmatic aerobic report builder."""
    template = result.get("template", "mle_mbr_asm2d")
    influent = result.get("influent", {})
    effluent = result.get("effluent", {})
    reactor = result.get("reactor", {})
    performance = result.get("performance", {})

    flow = influent.get("flow_m3_d", 0)
    temp_c = influent.get("temperature_K", 293.15) - 273.15

    cod_perf = performance.get('cod', {})
    n_perf = performance.get('nitrogen', {})
    p_perf = performance.get('phosphorus', {})

    return f"""---
title: "Aerobic MBR Simulation - {flow:.0f} m³/d"
date: {datetime.now().strftime("%Y-%m-%d")}
template: {template}
model: ASM2d
flow_m3_d: {flow}
tags: [qsdsan, aerobic, ASM2d, MBR, simulation]
format:
  html:
    toc: true
    css: report.css
---

## Executive Summary

MBR system treating **{flow:.0f} m³/d** at **{temp_c:.1f}°C** using ASM2d.

**Key Results:**
- COD Removal: {_format_number(cod_perf.get('removal_pct', 0))}%
- TN Removal: {_format_number(n_perf.get('tn_removal_pct', 0))}%
- TP Removal: {_format_number(p_perf.get('tp_removal_pct', 0))}%
- Status: {result.get('status', 'unknown')}

## Performance

| Metric | Value |
|--------|-------|
| COD Removal | {_format_number(cod_perf.get('removal_pct', 0))}% |
| TN Removal | {_format_number(n_perf.get('tn_removal_pct', 0))}% |
| TP Removal | {_format_number(p_perf.get('tp_removal_pct', 0))}% |
| Effluent NH4-N | {_format_number(effluent.get('NH4_mg_N_L', 0))} mg/L |
| Effluent NO3-N | {_format_number(effluent.get('NO3_mg_N_L', 0))} mg/L |
| Effluent PO4-P | {_format_number(effluent.get('PO4_mg_P_L', 0))} mg/L |
"""
