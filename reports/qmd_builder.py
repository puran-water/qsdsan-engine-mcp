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
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from copy import deepcopy

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    JINJA2_AVAILABLE = True
except ImportError:
    JINJA2_AVAILABLE = False


__all__ = [
    'build_anaerobic_report',
    'build_aerobic_report',
    'build_report',
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


def _prepare_anaerobic_data(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare anaerobic simulation data for template rendering.

    Transforms raw simulation output into template-friendly structure.
    """
    data = deepcopy(result)

    # Extract nested data
    influent = data.get('influent', {})
    effluent = data.get('effluent', {})
    reactor = data.get('reactor', {})
    performance = data.get('performance', {})
    biogas = data.get('biogas', {})
    inhibition = data.get('inhibition', {})
    sulfur = data.get('sulfur', {})
    flowsheet = data.get('flowsheet', {})

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
        'simulation': {
            'status': data.get('status', 'unknown'),
            'converged_at_days': data.get('converged_at_days', 0),
            'duration_days': data.get('duration_days', 0),
            'tolerance': data.get('tolerance', '1e-3'),
        },
        'time_series': {
            'convergence_plot': '[Convergence plot placeholder]',
            'state_variables_plot': '[State variables plot placeholder]',
        },
    }


def _prepare_aerobic_data(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare aerobic simulation data for template rendering.

    Transforms raw simulation output into template-friendly structure.
    """
    data = deepcopy(result)

    # Extract nested data
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
        'simulation': {
            'status': data.get('status', 'unknown'),
            'converged_at_days': data.get('converged_at_days', 0),
            'duration_days': data.get('duration_days', 0),
            'method': data.get('method', 'RK23'),
        },
        'time_series': {
            'nutrient_plot': '[Nutrient time series placeholder]',
            'reactor_state_plot': '[Reactor state plot placeholder]',
        },
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
        data = _prepare_anaerobic_data(result)
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
        data = _prepare_aerobic_data(result)
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
