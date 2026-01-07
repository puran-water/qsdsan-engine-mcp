"""
Flowsheet Diagram Generation Utilities.

Generates SVG/PNG flowsheet diagrams and mass balance tables from QSDsan System objects.
Designed for integration with Quarto Markdown reports.

Key features:
- Save system diagrams to PNG/SVG files
- Generate mass balance tables for all streams
- Create annotated stream data summaries
- Support for both anaerobic (mADM1) and aerobic (ASM2d) models
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union

logger = logging.getLogger(__name__)


def save_system_diagram(
    system,
    output_path: Union[str, Path],
    kind: str = "thorough",
    format: str = "svg",
    title: Optional[str] = None,
    number: bool = True,
    label: bool = True,
) -> Path:
    """
    Save a flowsheet diagram from a QSDsan System object.

    Parameters
    ----------
    system : qsdsan.System
        The QSDsan System object to diagram
    output_path : str or Path
        Output file path (without extension)
    kind : str
        Diagram type: 'thorough', 'surface', 'minimal', 'cluster', 'stage'
    format : str
        Output format: 'svg', 'png', 'pdf'
    title : str, optional
        Diagram title
    number : bool
        Whether to number unit operations
    label : bool
        Whether to label streams

    Returns
    -------
    Path
        Path to saved diagram file
    """
    output_path = Path(output_path)

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate filename with extension
    file_path = output_path.with_suffix(f".{format}")

    try:
        # Save diagram using QSDsan/biosteam's built-in method
        system.diagram(
            kind=kind,
            file=str(output_path),  # biosteam adds extension
            format=format,
            display=False,
            number=number,
            label=label,
            title=title or system.ID,
        )
        logger.info(f"Diagram saved to: {file_path}")
        return file_path
    except Exception as e:
        logger.warning(f"Failed to generate diagram: {e}")
        return None


def get_stream_data(stream, model_type: str = "mADM1") -> Dict[str, Any]:
    """
    Extract key data from a QSDsan WasteStream.

    Parameters
    ----------
    stream : qsdsan.WasteStream
        The stream to analyze
    model_type : str
        Model type for component interpretation ('mADM1' or 'ASM2d')

    Returns
    -------
    dict
        Stream data including flow, composition, and key parameters
    """
    data = {
        "id": stream.ID,
        "phase": stream.phase,
        "flow_m3_d": stream.F_vol * 24 if hasattr(stream, 'F_vol') else 0,
        "flow_kg_hr": stream.F_mass,
        "temperature_K": stream.T,
        "pressure_Pa": stream.P,
    }

    # Get component concentrations
    try:
        if hasattr(stream, 'iconc'):
            data["concentrations"] = {
                cmp.ID: float(stream.iconc[cmp.ID])
                for cmp in stream.components
                if stream.iconc[cmp.ID] > 1e-10
            }
    except Exception:
        data["concentrations"] = {}

    # Add aggregate parameters if available
    try:
        if hasattr(stream, 'COD'):
            data["COD_mg_L"] = stream.COD
        if hasattr(stream, 'TN'):
            data["TN_mg_L"] = stream.TN
        if hasattr(stream, 'TP'):
            data["TP_mg_L"] = stream.TP
        if hasattr(stream, 'TSS'):
            data["TSS_mg_L"] = stream.TSS
    except Exception:
        pass

    return data


def generate_mass_balance_table(
    system,
    model_type: str = "mADM1",
    key_components: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Generate mass balance data for all streams in a system.

    Parameters
    ----------
    system : qsdsan.System
        The QSDsan System object
    model_type : str
        Model type for component selection
    key_components : list, optional
        List of component IDs to include (defaults based on model_type)

    Returns
    -------
    list
        List of stream data dictionaries
    """
    # Default key components by model type
    if key_components is None:
        if model_type.lower() in ("madm1", "adm1"):
            key_components = [
                "S_su", "S_aa", "S_fa", "S_va", "S_bu", "S_pro", "S_ac",
                "S_h2", "S_ch4", "S_IC", "S_IN", "S_IP", "S_I",
                "X_c", "X_ch", "X_pr", "X_li", "X_su", "X_aa", "X_fa",
                "X_c4", "X_pro", "X_ac", "X_h2", "X_I",
                "S_SO4", "S_H2S", "X_hSRB", "X_aSRB", "X_pSRB", "X_c4SRB",
            ]
        else:  # ASM2d
            key_components = [
                "S_O2", "S_F", "S_A", "S_I", "S_NH4", "S_N2", "S_NO3",
                "S_PO4", "S_ALK", "X_I", "X_S", "X_H", "X_PAO", "X_PP",
                "X_PHA", "X_AUT", "X_MeOH", "X_MeP",
            ]

    streams_data = []

    # Collect all unique streams
    all_streams = set()
    for unit in system.units:
        for s in unit.ins:
            if s and not s.isempty():
                all_streams.add(s)
        for s in unit.outs:
            if s and not s.isempty():
                all_streams.add(s)

    for stream in sorted(all_streams, key=lambda s: s.ID):
        stream_data = get_stream_data(stream, model_type)

        # Filter to key components
        if stream_data.get("concentrations"):
            stream_data["key_concentrations"] = {
                k: v for k, v in stream_data["concentrations"].items()
                if k in key_components
            }

        streams_data.append(stream_data)

    return streams_data


def generate_unit_summary(system) -> List[Dict[str, Any]]:
    """
    Generate summary data for all units in a system.

    Parameters
    ----------
    system : qsdsan.System
        The QSDsan System object

    Returns
    -------
    list
        List of unit data dictionaries
    """
    units_data = []

    for i, unit in enumerate(system.units):
        unit_data = {
            "number": i + 1,
            "id": unit.ID,
            "type": type(unit).__name__,
            "n_ins": len(unit.ins),
            "n_outs": len(unit.outs),
        }

        # Add unit-specific parameters
        if hasattr(unit, 'V_max'):
            unit_data["volume_m3"] = unit.V_max
        if hasattr(unit, 'tau'):
            unit_data["HRT_hr"] = unit.tau
        if hasattr(unit, 'aeration'):
            unit_data["DO_mg_L"] = unit.aeration if unit.aeration else 0

        # Input/output stream IDs
        unit_data["inlet_ids"] = [s.ID for s in unit.ins if s]
        unit_data["outlet_ids"] = [s.ID for s in unit.outs if s]

        units_data.append(unit_data)

    return units_data


def format_mass_balance_for_qmd(
    streams_data: List[Dict[str, Any]],
    format_type: str = "table",
) -> str:
    """
    Format mass balance data as Quarto Markdown.

    Parameters
    ----------
    streams_data : list
        Stream data from generate_mass_balance_table()
    format_type : str
        Output format: 'table' or 'detailed'

    Returns
    -------
    str
        Formatted Quarto Markdown content
    """
    lines = []

    if format_type == "table":
        # Summary table
        lines.append("| Stream | Flow (m3/d) | COD (mg/L) | TN (mg/L) | TP (mg/L) |")
        lines.append("|:-------|------------:|-----------:|----------:|----------:|")

        for s in streams_data:
            flow = s.get("flow_m3_d", 0)
            cod = s.get("COD_mg_L", "N/A")
            tn = s.get("TN_mg_L", "N/A")
            tp = s.get("TP_mg_L", "N/A")

            cod_str = f"{cod:.1f}" if isinstance(cod, (int, float)) else cod
            tn_str = f"{tn:.1f}" if isinstance(tn, (int, float)) else tn
            tp_str = f"{tp:.1f}" if isinstance(tp, (int, float)) else tp

            lines.append(f"| {s['id']} | {flow:.1f} | {cod_str} | {tn_str} | {tp_str} |")

    elif format_type == "detailed":
        for s in streams_data:
            lines.append(f"### Stream: {s['id']}")
            lines.append("")
            lines.append(f"- **Flow:** {s.get('flow_m3_d', 0):.2f} m³/d")
            lines.append(f"- **Temperature:** {s.get('temperature_K', 0) - 273.15:.1f} °C")

            if s.get("key_concentrations"):
                lines.append("")
                lines.append("**Key Components (mg/L):**")
                lines.append("")
                lines.append("| Component | Concentration |")
                lines.append("|:----------|-------------:|")
                for comp, conc in sorted(s["key_concentrations"].items()):
                    if conc > 0.01:
                        lines.append(f"| {comp} | {conc:.2f} |")
            lines.append("")

    return "\n".join(lines)


def generate_diagram_section(
    diagram_path: Optional[Path],
    streams_data: List[Dict[str, Any]],
    units_data: List[Dict[str, Any]],
) -> str:
    """
    Generate complete Flowsheet & Mass Balance section for QMD.

    Parameters
    ----------
    diagram_path : Path, optional
        Path to saved diagram file
    streams_data : list
        Stream data from generate_mass_balance_table()
    units_data : list
        Unit data from generate_unit_summary()

    Returns
    -------
    str
        Complete QMD section content
    """
    lines = []

    lines.append("## Flowsheet Diagram")
    lines.append("")

    if diagram_path and diagram_path.exists():
        # Relative path for the QMD file
        lines.append(f"![System Flowsheet]({diagram_path.name})")
        lines.append("")
    else:
        lines.append("<div class=\"ts-placeholder\">")
        lines.append("[Flowsheet diagram not available]")
        lines.append("</div>")
        lines.append("")

    lines.append("## Unit Operations")
    lines.append("")
    lines.append("| # | Unit | Type | Volume (m³) | Inlets | Outlets |")
    lines.append("|--:|:-----|:-----|------------:|:-------|:--------|")

    for u in units_data:
        vol = u.get("volume_m3", "N/A")
        vol_str = f"{vol:.1f}" if isinstance(vol, (int, float)) else vol
        ins = ", ".join(u.get("inlet_ids", []))
        outs = ", ".join(u.get("outlet_ids", []))
        lines.append(f"| {u['number']} | {u['id']} | {u['type']} | {vol_str} | {ins} | {outs} |")

    lines.append("")
    lines.append("## Stream Mass Balance")
    lines.append("")
    lines.append(format_mass_balance_for_qmd(streams_data, "table"))

    return "\n".join(lines)
