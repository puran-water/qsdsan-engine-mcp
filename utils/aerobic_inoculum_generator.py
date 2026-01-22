"""
Aerobic Inoculum Generator for ASM2d systems.

Generates initial concentrations for reactor seeding via set_init_conc().
Solves the problem where reactors initialized with influent composition
(~5 mg/L X_AUT) fail to achieve nitrification due to insufficient biomass.

CRITICAL: ASM2d biomass components (X_H, X_PAO, X_AUT) are in mg COD/L.
Use COD_VSS_RATIO = 1.42 g COD/g VSS for conversion.

Usage:
    from utils.aerobic_inoculum_generator import generate_aerobic_inoculum

    # Generate inoculum for typical MLE system
    inoculum = generate_aerobic_inoculum(target_mlvss_mg_L=3500)

    # Apply to reactor using QSDsan's set_init_conc method
    for reactor in [A1, A2, O1, O2, MBR]:
        reactor.set_init_conc(**inoculum)

Reference:
    - Henze et al. (2000) - Activated Sludge Models ASM1, ASM2, ASM2d and ASM3
    - Rieger et al. (2012) - Guidelines for Using Activated Sludge Models
    - Codex Review Session: 019be6ae-8935-70d2-b72d-07763804f933
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# COD to VSS conversion ratio (typical for activated sludge biomass)
# Reference: Henze et al. (2000), typically 1.42-1.48 g COD/g VSS
COD_VSS_RATIO = 1.42  # g COD / g VSS

# ASM2d biomass component IDs
AEROBIC_BIOMASS_COMPONENTS = ['X_H', 'X_PAO', 'X_AUT']

# Typical biomass fractions in activated sludge (as fraction of total MLVSS)
# Reference: Metcalf & Eddy (2014), Henze et al. (2000)
DEFAULT_BIOMASS_FRACTIONS = {
    'X_H': 0.80,    # Heterotrophs: 80% of MLVSS
    'X_PAO': 0.10,  # PAOs: 10% of MLVSS (for EBPR systems)
    'X_AUT': 0.05,  # Autotrophs (nitrifiers): 5% of MLVSS
    # Remaining 5% is inert biomass (X_I from decay)
}


def generate_aerobic_inoculum(
    target_mlvss_mg_L: float = 3500.0,
    x_aut_fraction: Optional[float] = None,
    x_pao_fraction: Optional[float] = None,
    x_h_fraction: Optional[float] = None,
    include_substrate: bool = True,
    include_nutrients: bool = True,
) -> Dict[str, float]:
    """
    Generate reactor initial concentrations for set_init_conc().

    This function generates inoculum concentrations representative of
    acclimated activated sludge, solving the problem where reactors
    initialized with influent composition fail to achieve nitrification.

    Args:
        target_mlvss_mg_L: Target MLVSS in reactor (default 3500 mg/L as VSS).
                          Typical range: 2000-6000 mg/L for MBR systems.
        x_aut_fraction: Fraction of MLVSS as X_AUT (autotrophs/nitrifiers).
                       Default 5% for nitrifying activated sludge.
                       Use 0.03-0.05 for conventional systems.
                       Use 0.05-0.10 for nitrification-focused systems.
        x_pao_fraction: Fraction of MLVSS as X_PAO (phosphate accumulating organisms).
                       Default 10% for EBPR systems. Use 0 for non-EBPR.
        x_h_fraction: Fraction of MLVSS as X_H (heterotrophs).
                     Default calculated to balance to ~95% of MLVSS.
        include_substrate: Include low background substrate concentrations.
        include_nutrients: Include low background nutrient concentrations.

    Returns:
        Dict of component concentrations in mg COD/L (or mg/L for non-COD components)
        suitable for passing to reactor.set_init_conc(**inoculum).

    Example:
        >>> inoculum = generate_aerobic_inoculum(target_mlvss_mg_L=3500)
        >>> print(f"X_AUT: {inoculum['X_AUT']:.0f} mg COD/L")
        X_AUT: 249 mg COD/L

        >>> # For MBR with high MLSS
        >>> inoculum = generate_aerobic_inoculum(target_mlvss_mg_L=8000, x_aut_fraction=0.03)
        >>> reactor.set_init_conc(**inoculum)

    Notes:
        - CSTR does NOT accept `initial_state` parameter - must use set_init_conc()
        - X_AUT units are mg COD/L, not mg VSS/L
        - K_NH4_AUT=1.0 is IWA standard - don't change, fix via inoculation
        - The MLE-MBR template has SRT control via clarifier + RAS/WAS recycle
    """
    # Use provided fractions or defaults
    f_aut = x_aut_fraction if x_aut_fraction is not None else DEFAULT_BIOMASS_FRACTIONS['X_AUT']
    f_pao = x_pao_fraction if x_pao_fraction is not None else DEFAULT_BIOMASS_FRACTIONS['X_PAO']
    f_h = x_h_fraction if x_h_fraction is not None else DEFAULT_BIOMASS_FRACTIONS['X_H']

    # Validate fractions don't exceed 1.0 (allow small tolerance)
    total_fraction = f_aut + f_pao + f_h
    if total_fraction > 1.05:
        logger.warning(
            f"Biomass fractions sum to {total_fraction:.2f} > 1.0. "
            f"Normalizing to 1.0."
        )
        f_aut /= total_fraction
        f_pao /= total_fraction
        f_h /= total_fraction

    # Convert target MLVSS (VSS basis) to COD basis
    target_mlvss_cod = target_mlvss_mg_L * COD_VSS_RATIO  # mg COD/L

    # Calculate biomass component concentrations in mg COD/L
    inoculum: Dict[str, float] = {}

    # Biomass (all in mg COD/L)
    inoculum['X_H'] = target_mlvss_cod * f_h
    inoculum['X_PAO'] = target_mlvss_cod * f_pao
    inoculum['X_AUT'] = target_mlvss_cod * f_aut

    # Inerts from decay (typically ~5% of MLVSS converted to X_I over time)
    inoculum['X_I'] = target_mlvss_cod * 0.05

    # Slowly biodegradable substrate (low background in acclimated sludge)
    if include_substrate:
        inoculum['X_S'] = 50.0   # mg COD/L - low background
        inoculum['S_F'] = 5.0   # mg COD/L - very low in acclimated reactor
        inoculum['S_A'] = 5.0   # mg COD/L - very low
        inoculum['S_I'] = 10.0  # mg/L - soluble inerts

    # Internal storage polymers (for EBPR)
    if f_pao > 0:
        inoculum['X_PP'] = f_pao * target_mlvss_cod * 0.3   # Polyphosphate storage
        inoculum['X_PHA'] = f_pao * target_mlvss_cod * 0.1  # PHA storage

    # Nutrients (background concentrations in acclimated reactor)
    if include_nutrients:
        inoculum['S_NH4'] = 2.0     # mg N/L - low residual after nitrification
        inoculum['S_NO3'] = 5.0     # mg N/L - typical effluent NO3
        inoculum['S_PO4'] = 2.0     # mg P/L - residual after EBPR
        inoculum['S_ALK'] = 200.0   # mg/L as CaCO3 - typical alkalinity
        inoculum['S_N2'] = 15.0     # mg N/L - dissolved N2 from denitrification

    # Dissolved oxygen (set based on typical aerobic reactor)
    # Note: This will be overridden by aeration in dynamic simulation
    inoculum['S_O2'] = 2.0  # mg O2/L - typical DO setpoint

    logger.info(
        f"Generated aerobic inoculum: MLVSS={target_mlvss_mg_L:.0f} mg VSS/L, "
        f"X_H={inoculum['X_H']:.0f}, X_PAO={inoculum['X_PAO']:.0f}, "
        f"X_AUT={inoculum['X_AUT']:.0f} mg COD/L"
    )

    return inoculum


def get_recommended_inoculum_for_process(
    process_type: str,
    flow_m3_d: float = 4000.0,
) -> Dict[str, Any]:
    """
    Get recommended inoculum parameters for common process configurations.

    Args:
        process_type: One of "MLE", "A2O", "SBR", "conventional_AS", "MBR"
        flow_m3_d: Design flow rate (affects recommended MLSS)

    Returns:
        Dict with recommended inoculum parameters and rationale

    Example:
        >>> rec = get_recommended_inoculum_for_process("MLE")
        >>> inoculum = generate_aerobic_inoculum(**rec["params"])
    """
    recommendations = {
        "MLE": {
            "params": {
                "target_mlvss_mg_L": 3500,
                "x_aut_fraction": 0.05,
                "x_pao_fraction": 0.02,  # Minimal PAO for MLE
                "x_h_fraction": 0.85,
            },
            "rationale": (
                "MLE (Modified Ludzack-Ettinger) is designed for nitrogen removal. "
                "5% nitrifiers (X_AUT) ensures robust nitrification. "
                "Low PAO fraction since MLE doesn't target EBPR."
            ),
            "expected_removal": {
                "COD": ">90%",
                "NH4": ">85%",
                "TN": "60-80%",
                "TP": "20-30% (biological uptake only)",
            },
        },
        "A2O": {
            "params": {
                "target_mlvss_mg_L": 4000,
                "x_aut_fraction": 0.04,
                "x_pao_fraction": 0.15,  # Higher PAO for EBPR
                "x_h_fraction": 0.75,
            },
            "rationale": (
                "A2O (Anaerobic-Anoxic-Oxic) targets both N and P removal. "
                "15% PAO fraction supports EBPR. "
                "Slightly lower nitrifiers due to competition for oxygen."
            ),
            "expected_removal": {
                "COD": ">90%",
                "NH4": ">80%",
                "TN": "60-75%",
                "TP": ">80% (with EBPR)",
            },
        },
        "MBR": {
            "params": {
                "target_mlvss_mg_L": 8000,  # Higher MLSS in MBR
                "x_aut_fraction": 0.05,
                "x_pao_fraction": 0.10,
                "x_h_fraction": 0.80,
            },
            "rationale": (
                "MBR operates at higher MLSS (8000-12000 mg/L) due to membrane retention. "
                "Higher biomass concentrations improve nutrient removal. "
                "Membrane prevents solids washout, supporting longer SRT."
            ),
            "expected_removal": {
                "COD": ">95%",
                "NH4": ">95%",
                "TN": "70-85%",
                "TP": "40-60% (higher with chemical P removal)",
            },
        },
        "conventional_AS": {
            "params": {
                "target_mlvss_mg_L": 2500,
                "x_aut_fraction": 0.03,
                "x_pao_fraction": 0.05,
                "x_h_fraction": 0.87,
            },
            "rationale": (
                "Conventional activated sludge focuses on COD/BOD removal. "
                "Lower nitrifier fraction - nitrification may be partial. "
                "Adequate for secondary treatment without nutrient removal targets."
            ),
            "expected_removal": {
                "COD": ">85%",
                "NH4": "50-70%",
                "TN": "20-40%",
                "TP": "15-25%",
            },
        },
        "SBR": {
            "params": {
                "target_mlvss_mg_L": 4000,
                "x_aut_fraction": 0.06,
                "x_pao_fraction": 0.12,
                "x_h_fraction": 0.77,
            },
            "rationale": (
                "SBR (Sequencing Batch Reactor) can achieve good N and P removal. "
                "Fill-react-settle cycle allows anaerobic/anoxic/aerobic phases. "
                "Higher X_AUT fraction due to complete settling preventing washout."
            ),
            "expected_removal": {
                "COD": ">90%",
                "NH4": ">90%",
                "TN": "70-85%",
                "TP": "70-85% (with proper EBPR cycle)",
            },
        },
    }

    process_upper = process_type.upper()
    if process_upper in recommendations:
        return recommendations[process_upper]

    # Default fallback
    logger.warning(f"Unknown process type '{process_type}', using MLE defaults")
    return recommendations["MLE"]


def estimate_equilibration_time(
    target_mlvss_mg_L: float,
    x_aut_fraction: float = 0.05,
    srt_days: float = 15.0,
) -> Dict[str, Any]:
    """
    Estimate simulation duration needed for biomass equilibration.

    Nitrifiers (X_AUT) have slow growth rates (μ_AUT ~1.0 d⁻¹ at 20°C).
    Even with proper inoculation, the system needs time to reach steady state.

    Args:
        target_mlvss_mg_L: Target MLVSS
        x_aut_fraction: Nitrifier fraction
        srt_days: Expected solids retention time

    Returns:
        Dict with recommended simulation duration and rationale

    Example:
        >>> est = estimate_equilibration_time(target_mlvss_mg_L=3500)
        >>> print(f"Recommended simulation: {est['recommended_days']} days")
    """
    # Rule of thumb: 3-5 SRTs for steady state
    # Nitrifiers need longer due to slow growth
    min_days = srt_days * 3
    recommended_days = srt_days * 4

    # If nitrifier fraction is low, need more time for buildup
    if x_aut_fraction < 0.03:
        recommended_days = srt_days * 5
        warning = "Low nitrifier fraction - extended equilibration needed"
    elif x_aut_fraction > 0.08:
        recommended_days = srt_days * 3
        warning = None
    else:
        warning = None

    return {
        "minimum_days": min_days,
        "recommended_days": recommended_days,
        "srt_days": srt_days,
        "rationale": (
            f"Nitrifiers grow slowly (μ_AUT ~1.0 d⁻¹). "
            f"For SRT={srt_days:.0f}d, recommend {recommended_days:.0f} days simulation "
            f"(~{recommended_days/srt_days:.1f} SRTs) for steady state."
        ),
        "warning": warning,
    }


# =============================================================================
# Module exports
# =============================================================================
__all__ = [
    'generate_aerobic_inoculum',
    'get_recommended_inoculum_for_process',
    'estimate_equilibration_time',
    'COD_VSS_RATIO',
    'AEROBIC_BIOMASS_COMPONENTS',
    'DEFAULT_BIOMASS_FRACTIONS',
]
