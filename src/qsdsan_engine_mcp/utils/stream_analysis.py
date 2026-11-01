"""Stream analysis re-export layer.

Exposes the symbols callers currently import under the flat
``utils.stream_analysis`` namespace. New code should import directly
from the submodules under ``utils.analysis``.
"""

from qsdsan_engine_mcp.utils.analysis import get_component_conc_kg_m3, get_component_conc_mg_L, calculate_stream_ph, calculate_removal_efficiency, calculate_mass_flow, analyze_stream_basics, analyze_liquid_stream, analyze_gas_stream, analyze_biomass_yields, calculate_sulfur_metrics, calculate_h2s_speciation, calculate_h2s_gas_ppm, MADM1_BIOMASS_IDS, MADM1_PRECIPITATE_IDS, analyze_aerobic_stream, analyze_aerobic_performance, calculate_nitrogen_removal, calculate_phosphorus_removal, calculate_srt, calculate_hrt, ASM2D_COMPONENTS, ASM2D_BIOMASS_IDS, DEFAULT_ASM2D_KWARGS, DEFAULT_DOMESTIC_WW, analyze_madm1_process_health, analyze_asm2d_process_health, capture_system_process_health

__all__ = [
    # Common
    'get_component_conc_kg_m3',
    'get_component_conc_mg_L',
    'calculate_stream_ph',
    'calculate_removal_efficiency',
    'calculate_mass_flow',
    'analyze_stream_basics',
    # Anaerobic
    'analyze_liquid_stream',
    'analyze_gas_stream',
    'analyze_biomass_yields',
    'calculate_sulfur_metrics',
    'calculate_h2s_speciation',
    'calculate_h2s_gas_ppm',
    'MADM1_BIOMASS_IDS',
    'MADM1_PRECIPITATE_IDS',
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
    # Per-process diagnostics (PR-DD)
    'analyze_madm1_process_health',
    'analyze_asm2d_process_health',
    'capture_system_process_health',
]
