# Report generation (Quarto Markdown)
"""
Quarto Markdown report generation for QSDsan simulation results.

Usage:
    from reports import build_report

    # Auto-detect report type
    qmd_content = build_report(simulation_result, output_path="report.qmd")

    # Or use specific builders
    from reports import build_anaerobic_report, build_aerobic_report
"""

from reports.qmd_builder import (
    build_report,
    build_anaerobic_report,
    build_aerobic_report,
)

__all__ = [
    'build_report',
    'build_anaerobic_report',
    'build_aerobic_report',
]
