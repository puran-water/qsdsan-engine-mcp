from pathlib import Path
from typing import Any, Dict
try:
    from qsdsan_engine_mcp.utils.report_plots import MATPLOTLIB_AVAILABLE
except ImportError:
    MATPLOTLIB_AVAILABLE = False

def _path_exists(path_value: Any) -> bool:
    """Return True when a serialized artifact path exists on disk."""
    if not path_value:
        return False
    try:
        return Path(path_value).exists()
    except (TypeError, OSError, ValueError):
        return False

def compute_artifact_completeness(results: Dict[str, Any]) -> Dict[str, bool]:
    """
    Summarize which downstream artifacts are actually available.

    The booleans are intended for agent-facing contract checks rather than for
    styling, so they intentionally stay coarse and stable.
    """
    flowsheet = results.get('flowsheet') or {}
    timeseries_data = bool(results.get('timeseries')) or bool(results.get('timeseries_available')) or _path_exists(results.get('timeseries_path'))
    return {'simulation_results': True, 'qmd_report': _path_exists(results.get('report_path')), 'svg_diagram': _path_exists(results.get('diagram_path')) or _path_exists(flowsheet.get('diagram_path')), 'convergence_plot': bool(timeseries_data and MATPLOTLIB_AVAILABLE), 'timeseries_data': timeseries_data}
