# Anaerobic treatment templates (mADM1)
"""
Anaerobic treatment templates using mADM1.

Available templates:
- cstr: Single CSTR with mADM1 model (63 components, 4 biogas species)
"""

from templates.anaerobic.cstr import build_and_run, get_default_reactor_config

__all__ = ['build_and_run', 'get_default_reactor_config']
