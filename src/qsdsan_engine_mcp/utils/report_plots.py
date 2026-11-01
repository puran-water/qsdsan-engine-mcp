"""
Time-series plot generation for Quarto reports.

Generates matplotlib plots from simulation timeseries data for embedding
in QMD reports. Uses non-interactive 'Agg' backend for headless operation.

Functions:
- generate_convergence_plot: Key state variables vs time
- generate_nutrient_plot: N/P nutrient trajectories (aerobic)
- generate_biogas_plot: Gas production trajectories (anaerobic)
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Use non-interactive backend for headless operation
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None

__all__ = [
    'generate_convergence_plot',
    'generate_nutrient_plot',
    'generate_biogas_plot',
    'generate_cod_plot',
    'MATPLOTLIB_AVAILABLE',
]


def generate_convergence_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    title: str = "Simulation Convergence",
    components: Optional[List[str]] = None,
) -> Optional[Path]:
    """
    Generate convergence plot showing key state variables over time.

    Parameters
    ----------
    timeseries : dict
        Time-series data with 'time' and 'streams' keys.
        Structure: {
            "time": [0, 0.1, 0.2, ...],
            "streams": {
                "effluent": {"COD_mg_L": [...], "S_NH4": [...], ...},
                ...
            }
        }
    output_path : Path
        Output path for PNG file (extension will be added if missing)
    title : str
        Plot title
    components : list, optional
        Component IDs to plot. If None, uses common defaults.

    Returns
    -------
    Path or None
        Path to generated plot, or None if generation failed
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("matplotlib not available, skipping plot generation")
        return None

    if not timeseries:
        logger.warning("No timeseries data provided")
        return None

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    if not time or not streams:
        logger.warning("Empty time or streams data")
        return None

    # Default components to plot
    if components is None:
        components = ["COD_mg_L", "S_NH4", "S_NO3", "S_O2", "TSS_mg_L"]

    fig, ax = plt.subplots(figsize=(10, 6))

    plotted = False
    for stream_id, stream_data in streams.items():
        for comp in components:
            if comp in stream_data:
                values = stream_data[comp]
                if len(values) == len(time):
                    ax.plot(time, values, label=f"{stream_id}: {comp}")
                    plotted = True

    if not plotted:
        # Try alternate component naming
        for stream_id, stream_data in streams.items():
            for key, values in stream_data.items():
                if isinstance(values, list) and len(values) == len(time):
                    ax.plot(time, values, label=f"{stream_id}: {key}")
                    plotted = True
                    if plotted:
                        break  # Limit to a few traces
            if plotted:
                break

    if not plotted:
        plt.close(fig)
        logger.warning("No plottable data found in timeseries")
        return None

    ax.set_xlabel("Time (days)")
    ax.set_ylabel("Concentration (mg/L)")
    ax.set_title(title)
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)

    output_path = Path(output_path)
    if output_path.suffix.lower() != '.png':
        output_path = output_path.with_suffix('.png')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"Convergence plot saved to: {output_path}")
    return output_path


def generate_nutrient_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    components: Optional[List[str]] = None,
    title: str = "Nutrient Trajectories",
) -> Optional[Path]:
    """
    Generate nutrient time-series plot for aerobic systems.

    Plots nitrogen and phosphorus species trajectories.

    Parameters
    ----------
    timeseries : dict
        Time-series data with 'time' and 'streams' keys
    output_path : Path
        Output path for PNG file
    components : list, optional
        Nutrient component IDs. Defaults to N/P species.
    title : str
        Plot title

    Returns
    -------
    Path or None
        Path to generated plot, or None if generation failed
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("matplotlib not available, skipping plot generation")
        return None

    if not timeseries:
        return None

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    if not time or not streams:
        return None

    # Default nutrient components
    if components is None:
        components = ["S_NH4", "S_NO3", "S_NO2", "S_PO4", "NH4_mg_N_L", "NO3_mg_N_L", "PO4_mg_P_L"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    # Nitrogen subplot
    n_components = ["S_NH4", "S_NO3", "S_NO2", "NH4_mg_N_L", "NO3_mg_N_L"]
    n_plotted = False
    for stream_id, stream_data in streams.items():
        for comp in n_components:
            if comp in stream_data:
                values = stream_data[comp]
                if len(values) == len(time):
                    label_name = comp.replace("_mg_N_L", "").replace("S_", "")
                    ax1.plot(time, values, label=f"{stream_id}: {label_name}")
                    n_plotted = True

    ax1.set_ylabel("N Concentration (mg N/L)")
    ax1.set_title("Nitrogen Species")
    ax1.legend(loc='best', fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Phosphorus subplot
    p_components = ["S_PO4", "PO4_mg_P_L", "S_IP", "X_PP"]
    p_plotted = False
    for stream_id, stream_data in streams.items():
        for comp in p_components:
            if comp in stream_data:
                values = stream_data[comp]
                if len(values) == len(time):
                    label_name = comp.replace("_mg_P_L", "").replace("S_", "")
                    ax2.plot(time, values, label=f"{stream_id}: {label_name}")
                    p_plotted = True

    ax2.set_xlabel("Time (days)")
    ax2.set_ylabel("P Concentration (mg P/L)")
    ax2.set_title("Phosphorus Species")
    ax2.legend(loc='best', fontsize=8)
    ax2.grid(True, alpha=0.3)

    if not n_plotted and not p_plotted:
        plt.close(fig)
        logger.warning("No nutrient data found in timeseries")
        return None

    fig.suptitle(title, fontsize=12, fontweight='bold')
    plt.tight_layout()

    output_path = Path(output_path)
    if output_path.suffix.lower() != '.png':
        output_path = output_path.with_suffix('.png')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"Nutrient plot saved to: {output_path}")
    return output_path


def generate_biogas_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    title: str = "Biogas Production",
) -> Optional[Path]:
    """
    Generate biogas production plot for anaerobic systems.

    Plots CH4, CO2, and H2S trajectories if available.

    Parameters
    ----------
    timeseries : dict
        Time-series data with 'time' and 'streams' keys
    output_path : Path
        Output path for PNG file
    title : str
        Plot title

    Returns
    -------
    Path or None
        Path to generated plot, or None if generation failed
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("matplotlib not available, skipping plot generation")
        return None

    if not timeseries:
        return None

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    if not time or not streams:
        return None

    # Biogas components (various naming conventions)
    gas_components = [
        "S_ch4", "S_CH4", "S_co2", "S_CO2", "S_h2s", "S_H2S", "S_IS",
        "S_h2", "S_H2", "S_IC", "biogas_CH4", "biogas_CO2",
        "methane_flow_Nm3_d", "co2_flow_Nm3_d",
    ]

    fig, ax = plt.subplots(figsize=(10, 6))

    plotted = False
    for stream_id, stream_data in streams.items():
        for comp in gas_components:
            if comp in stream_data:
                values = stream_data[comp]
                if len(values) == len(time):
                    ax.plot(time, values, label=f"{stream_id}: {comp}")
                    plotted = True

    if not plotted:
        plt.close(fig)
        logger.warning("No biogas data found in timeseries")
        return None

    ax.set_xlabel("Time (days)")
    ax.set_ylabel("Gas Concentration / Flow")
    ax.set_title(title)
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)

    output_path = Path(output_path)
    if output_path.suffix.lower() != '.png':
        output_path = output_path.with_suffix('.png')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"Biogas plot saved to: {output_path}")
    return output_path


def generate_cod_plot(
    timeseries: Dict[str, Any],
    output_path: Path,
    title: str = "COD Trajectory",
) -> Optional[Path]:
    """
    Generate COD concentration plot.

    Parameters
    ----------
    timeseries : dict
        Time-series data with 'time' and 'streams' keys
    output_path : Path
        Output path for PNG file
    title : str
        Plot title

    Returns
    -------
    Path or None
        Path to generated plot, or None if generation failed
    """
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("matplotlib not available, skipping plot generation")
        return None

    if not timeseries:
        return None

    time = timeseries.get("time", [])
    streams = timeseries.get("streams", {})

    if not time or not streams:
        return None

    # COD-related components
    cod_components = ["COD_mg_L", "S_S", "S_F", "X_S", "total_COD"]

    fig, ax = plt.subplots(figsize=(10, 6))

    plotted = False
    for stream_id, stream_data in streams.items():
        for comp in cod_components:
            if comp in stream_data:
                values = stream_data[comp]
                if len(values) == len(time):
                    ax.plot(time, values, label=f"{stream_id}: {comp}")
                    plotted = True

    if not plotted:
        plt.close(fig)
        logger.warning("No COD data found in timeseries")
        return None

    ax.set_xlabel("Time (days)")
    ax.set_ylabel("COD (mg/L)")
    ax.set_title(title)
    ax.legend(loc='best', fontsize=8)
    ax.grid(True, alpha=0.3)

    output_path = Path(output_path)
    if output_path.suffix.lower() != '.png':
        output_path = output_path.with_suffix('.png')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info(f"COD plot saved to: {output_path}")
    return output_path
