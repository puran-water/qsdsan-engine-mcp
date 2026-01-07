"""
Aerobic MBR templates for ASM2d-based simulation.

Templates:
- mle_mbr: MLE-MBR (Modified Ludzack-Ettinger with MBR)
- ao_mbr: A/O-MBR (Simple Anoxic-Oxic with MBR)
- a2o_mbr: A2O-MBR (Anaerobic-Anoxic-Oxic with EBPR)

Usage:
    from templates.aerobic.mle_mbr import build_and_run
    result = build_and_run(influent_state={...}, reactor_config={...})
"""

from templates.aerobic.mle_mbr import (
    build_and_run as build_mle_mbr,
    get_default_reactor_config as get_mle_mbr_config,
    get_default_influent as get_mle_mbr_influent,
)

from templates.aerobic.ao_mbr import (
    build_and_run as build_ao_mbr,
    get_default_reactor_config as get_ao_mbr_config,
    get_default_influent as get_ao_mbr_influent,
)

from templates.aerobic.a2o_mbr import (
    build_and_run as build_a2o_mbr,
    get_default_reactor_config as get_a2o_mbr_config,
    get_default_influent as get_a2o_mbr_influent,
)

__all__ = [
    # MLE-MBR
    'build_mle_mbr',
    'get_mle_mbr_config',
    'get_mle_mbr_influent',
    # A/O-MBR
    'build_ao_mbr',
    'get_ao_mbr_config',
    'get_ao_mbr_influent',
    # A2O-MBR
    'build_a2o_mbr',
    'get_a2o_mbr_config',
    'get_a2o_mbr_influent',
]
