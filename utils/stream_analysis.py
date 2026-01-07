"""
Stream analysis module - Backward compatibility re-exports.

This module re-exports functions from the restructured utils.analysis package.
For new code, import directly from utils.analysis submodules.

Submodules:
- utils.analysis.common: Shared helper functions
- utils.analysis.anaerobic: mADM1/sulfur-specific analysis
- utils.analysis.aerobic: ASM2d-specific analysis
"""

# Re-export everything from the new package structure
from utils.analysis import (
    # Common utilities
    get_component_conc_kg_m3,
    get_component_conc_mg_L,
    get_component_conc,
    calculate_stream_ph,
    calculate_removal_efficiency,
    calculate_mass_flow,
    analyze_stream_basics,
    # Anaerobic (mADM1)
    analyze_liquid_stream,
    analyze_gas_stream,
    analyze_inhibition,
    analyze_biomass_yields,
    calculate_sulfur_metrics,
    calculate_h2s_speciation,
    calculate_h2s_gas_ppm,
    extract_diagnostics,
    MADM1_BIOMASS_IDS,
    MADM1_PRECIPITATE_IDS,
    # Aerobic (ASM2d)
    analyze_aerobic_stream,
    analyze_aerobic_performance,
    calculate_nitrogen_removal,
    calculate_phosphorus_removal,
    calculate_srt,
    calculate_hrt,
    ASM2D_COMPONENTS,
    ASM2D_BIOMASS_IDS,
    DEFAULT_ASM2D_KWARGS,
    DEFAULT_DOMESTIC_WW,
)

# Legacy aliases for backward compatibility
BIOMASS_COMPONENTS = MADM1_BIOMASS_IDS
PRECIPITATE_COMPONENTS = MADM1_PRECIPITATE_IDS

# Alias the pH function name
_calculate_stream_ph = calculate_stream_ph

__all__ = [
    # Common
    'get_component_conc_kg_m3',
    'get_component_conc_mg_L',
    'get_component_conc',
    'calculate_stream_ph',
    '_calculate_stream_ph',
    'calculate_removal_efficiency',
    'calculate_mass_flow',
    'analyze_stream_basics',
    # Anaerobic
    'analyze_liquid_stream',
    'analyze_gas_stream',
    'analyze_inhibition',
    'analyze_biomass_yields',
    'calculate_sulfur_metrics',
    'calculate_h2s_speciation',
    'calculate_h2s_gas_ppm',
    'extract_diagnostics',
    'MADM1_BIOMASS_IDS',
    'MADM1_PRECIPITATE_IDS',
    'BIOMASS_COMPONENTS',  # Legacy alias
    'PRECIPITATE_COMPONENTS',  # Legacy alias
    # Aerobic
    'analyze_aerobic_stream',
    'analyze_aerobic_performance',
    'calculate_nitrogen_removal',
    'calculate_phosphorus_removal',
    'calculate_srt',
    'calculate_hrt',
    'ASM2D_COMPONENTS',
    'ASM2D_BIOMASS_IDS',
    'DEFAULT_ASM2D_KWARGS',
    'DEFAULT_DOMESTIC_WW',
]
