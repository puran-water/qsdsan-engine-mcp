"""Diagnostics extraction from normalized QSDsan result payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .gas import extract_gas_results
from .normalizer import normalized_results_dict


def build_process_diagnostics(results: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract process-facing diagnostics without exposing wrapper details."""

    normalized = normalized_results_dict(results)
    return {
        "process_health": normalized.get("process_health") or {},
        "asm_bio": normalized.get("asm_bio") or normalized.get("diagnostics_asm_bio") or {},
        "unit_analysis": normalized.get("unit_analysis") or {},
        "state_summary": normalized.get("state_summary") or {},
        "per_engine_summaries": normalized.get("per_engine_summaries") or {},
        "predicted_streams": normalized.get("predicted_streams") or {},
        "effluent_quality": normalized.get("effluent_quality") or {},
        "biogas": extract_gas_results(normalized),
    }


def build_simulation_diagnostics(
    results: Mapping[str, Any] | None,
    *,
    sim_config: Mapping[str, Any] | None = None,
    raw_ts: Mapping[str, Any] | None = None,
    stream_states: Mapping[str, Any] | None = None,
    job_dir: Path | None = None,
) -> dict[str, Any]:
    """Extract simulation diagnostics from normalized result data."""

    normalized = normalized_results_dict(results)
    metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
    convergence = normalized.get("convergence") if isinstance(normalized.get("convergence"), dict) else {}
    config = dict(sim_config or {})
    return {
        "convergence": convergence,
        "srt_control": normalized.get("srt_control") or {},
        "solver_metadata": {
            "solver_name": metadata.get("solver"),
            "method": config.get("method"),
            "duration_days": config.get("duration_days"),
            "run_to_convergence": config.get("run_to_convergence"),
            "wall_time_s": metadata.get("wall_time_s"),
        },
        "warnings": list(normalized.get("warnings") or []),
        "risk_flags": list(normalized.get("risk_flags") or []),
        "timeseries_available": raw_ts is not None,
        "timeseries_path": normalized.get("timeseries_path"),
        "stream_states_path": str(job_dir / "stream_states.json") if job_dir and stream_states else None,
    }


__all__ = ["build_process_diagnostics", "build_simulation_diagnostics"]
