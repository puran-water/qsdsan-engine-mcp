"""
QSDsan Engine MCP Server - Universal Biological Wastewater Treatment Simulation

This is the MCP adapter for the QSDsan simulation engine. It exposes the
dynamic flowsheet, validation, discoverability, and TEA tools backed by the
shared engine core.
"""
import asyncio
import json
import logging
import concurrent.futures as _futures
import functools as _functools
_ENGINE_EXECUTOR = _futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='qsdsan-engine')

async def _locked_to_thread(fn, /, *args, **kwargs):
    """Run a CPU-heavy QSDsan build/solve off the event loop, SERIALIZED through the single-worker engine
    executor (H9). Drop-in for ``asyncio.to_thread`` at every engine dispatch site."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_ENGINE_EXECUTOR, _functools.partial(fn, *args, **kwargs))
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from qsdsan_engine_mcp.utils.timeseries_sampling import SamplingFactor, PointLimit, validate_sampling_controls, bounded_stride
from fastmcp import FastMCP
from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
from qsdsan_engine_mcp.core.model_types import ModelType, model_type_value
from qsdsan_engine_mcp.core.model_registry import get_model_info, get_required_components, validate_components, list_available_models, MODEL_REGISTRY, normalize_model_name
from qsdsan_engine_mcp.core.components.catalog import get_component_ids as get_catalog_component_ids, get_component_specs, get_concentration_units as get_catalog_concentration_units, get_handoff_only_component_specs
from qsdsan_engine_mcp.core.contracts import StateValidationCheck, StateValidationReport, StreamState
from qsdsan_engine_mcp.core.contracts.tool_outputs import TOOL_OUTPUT_SCHEMAS
from qsdsan_engine_mcp.core.errors import public_error
from qsdsan_engine_mcp.core.downstream_handoff import list_handoff_profiles as get_handoff_profiles, load_handoff_state_from_job_dir, stream_state_from_payload, prepare_downstream_handoff as prepare_handoff_payload
from qsdsan_engine_mcp.core.handoff.validators import HandoffValidationError
from qsdsan_engine_mcp.core.results.diagnostics import build_process_diagnostics as extract_process_diagnostics, build_simulation_diagnostics as extract_simulation_diagnostics
from qsdsan_engine_mcp.core.results.normalizer import normalize_job_response, normalize_results_payload, normalized_results_dict, result_validity_blockers
from qsdsan_engine_mcp.core.watertap_seed import build_watertap_seed, seed_has_near_feasible_flag
from qsdsan_engine_mcp.core.version import get_version_info
from qsdsan_engine_mcp.core.unit_registry import list_available_units as get_all_units, get_unit_spec as get_unit_spec_from_registry, serialize_unit_spec, get_public_support_error, compile_engineering_inputs as compile_unit_engineering_inputs, validate_unit_bootstrap, validate_model_compatibility, get_units_by_category, get_junction_output_model, suggest_junction_for_conversion, find_junction_for_conversion
from qsdsan_engine_mcp.utils.job_manager import JobManager
from qsdsan_engine_mcp.utils.path_utils import normalize_path_for_wsl, get_python_executable
from qsdsan_engine_mcp.utils.flowsheet_refs import compute_effective_model as compute_effective_model_at_unit, find_named_output_producer, get_named_output_model
from qsdsan_engine_mcp.utils.srt_preflight import format_missing_biomass_seed_error, session_has_biomass_seed
from qsdsan_engine_mcp.utils.simulation_seed_compiler import build_plan_from_simulation_seeds
from qsdsan_engine_mcp.utils.stream_roles import derive_session_stream_roles, invert_stream_roles
from qsdsan_engine_mcp.utils.session_startup import apply_session_startup
from qsdsan_engine_mcp.utils.flowsheet_session import FlowsheetSession, FlowsheetSessionManager, StreamConfig, UnitConfig, ConnectionConfig
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
mcp = FastMCP('qsdsan-engine')
_original_mcp_tool = mcp.tool

def _tool_with_pydantic_output_schema(name_or_fn=None, **kwargs):
    """Attach strict Pydantic output schemas to every registered MCP tool."""

    def _schema_for(tool_name: str) -> dict[str, Any] | None:
        return TOOL_OUTPUT_SCHEMAS.get(tool_name)

    def _register(fn):
        call_kwargs = dict(kwargs)
        tool_name = call_kwargs.get('name')
        if tool_name is None:
            tool_name = name_or_fn if isinstance(name_or_fn, str) else fn.__name__
        schema = _schema_for(str(tool_name))
        if schema is not None and 'output_schema' not in call_kwargs:
            call_kwargs['output_schema'] = schema
        if isinstance(name_or_fn, str):
            return _original_mcp_tool(name_or_fn, **call_kwargs)(fn)
        return _original_mcp_tool(**call_kwargs)(fn)
    if callable(name_or_fn) and (not isinstance(name_or_fn, str)):
        return _register(name_or_fn)
    return _register
mcp.tool = _tool_with_pydantic_output_schema
apply_runtime_compat()

def _positive_int_env(name: str, default: int) -> int:
    """Read a strictly positive integer from the environment."""
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f'{name} must be a positive integer, got {raw!r}') from exc
    if value < 1:
        raise RuntimeError(f'{name} must be a positive integer, got {raw!r}')
    return value
QSDSAN_STORAGE_DIR = Path(os.environ.get('QSDSAN_ENGINE_STORAGE_DIR') or os.environ.get('QSDSAN_ENGINE_SESSIONS_DIR') or 'jobs')
job_manager = JobManager(max_concurrent_jobs=_positive_int_env('QSDSAN_ENGINE_MAX_CONCURRENT_JOBS', 3), jobs_base_dir=str(QSDSAN_STORAGE_DIR))
_PREDICTED_EFFLUENT_UNITS = {'BOD5': 'mg/L', 'COD': 'mg/L', 'TSS': 'mg/L', 'TN': 'mg/L', 'TP': 'mg/L', 'NH3_N': 'mg/L', 'NO3_N': 'mg/L', 'pH': 'pH'}

def _job_root() -> Path:
    return Path(job_manager.jobs_dir).resolve()

def _job_dir(job_id: str) -> Path:
    return _job_root() / job_id

def _safe_job_dir(job_id: str) -> Path:
    from qsdsan_engine_mcp.utils.path_utils import validate_id, validate_safe_path
    validate_id(job_id, 'job_id')
    return validate_safe_path(_job_root(), job_id, 'job_id')

def _resource_error_json(message: Any, *, code: str, **details: Any) -> str:
    return json.dumps(public_error(message, code=code, **details), indent=2)

def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))

def _session_provenance_path(session_id: str) -> Path:
    return session_manager._get_session_dir(session_id) / 'provenance.json'

def _load_session_provenance(session_id: str) -> dict[str, Any]:
    return _read_json_file(_session_provenance_path(session_id))

def _save_session_provenance(session_id: str, payload: dict[str, Any]) -> None:
    current = _load_session_provenance(session_id)
    current.update(payload)
    _write_json_file(_session_provenance_path(session_id), current)

def _job_persistence_path(job_id: str) -> Path:
    return _job_dir(job_id) / 'artifact_persistence.json'

def _load_job_persistence(job_id: str) -> dict[str, Any]:
    return _read_json_file(_job_persistence_path(job_id))

def _save_job_persistence(job_id: str, payload: dict[str, Any]) -> None:
    current = _load_job_persistence(job_id)
    current.update(payload)
    _write_json_file(_job_persistence_path(job_id), current)

def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def _build_predicted_effluent(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = normalized_results_dict(results)
    raw = results.get('effluent_quality') or results.get('effluent') or {}
    if not isinstance(raw, dict):
        return {}
    mapping = {'BOD5_mg_L': 'BOD5', 'COD_mg_L': 'COD', 'TSS_mg_L': 'TSS', 'TN_mg_N_L': 'TN', 'TKN_mg_N_L': 'TKN', 'TP_mg_P_L': 'TP', 'NH3_N_mg_L': 'NH3_N', 'NO3_N_mg_L': 'NO3_N', 'pH': 'pH'}
    predicted: dict[str, dict[str, Any]] = {}

    def add(code: str, value: Any) -> None:
        numeric = _coerce_float(value)
        if numeric is None:
            return
        predicted[code] = {'predicted_value': numeric, 'predicted_units': _PREDICTED_EFFLUENT_UNITS.get(code, 'mg/L')}

    def walk(obj: dict[str, Any]) -> None:
        for key, value in obj.items():
            if isinstance(value, dict):
                walk(value)
                continue
            code = mapping.get(key)
            if code:
                add(code, value)
    walk(raw)
    return predicted
_ENGINE_MODEL_TO_ENGINE = {'asm1': 'asm1', 'asm2d': 'asm2d', 'masm2d': 'asm2d', 'masm2d_mbr': 'asm2d', 'adm1': 'adm1', 'madm1': 'adm1'}

def _derive_engine_name(results: dict[str, Any]) -> str:
    """Map a QSDsan results dict to a canonical engine name for scope_refs + EngineReport."""
    results = normalized_results_dict(results)
    model_type = results.get('model_type') or ''
    meta = results.get('metadata') or {}
    if not model_type and isinstance(meta, dict):
        model_type = meta.get('model_type') or ''
    key = str(model_type).lower().strip()
    if key in _ENGINE_MODEL_TO_ENGINE:
        return _ENGINE_MODEL_TO_ENGINE[key]
    return f'qsdsan.{key or 'unknown'}'

def _build_recycle_ratios(results: dict[str, Any]) -> dict[str, Any]:
    """Extract recycle-ratio block from reactor_sizing / metadata when present."""
    results = normalized_results_dict(results)
    reactor = results.get('reactor_sizing') or {}
    recycles = reactor.get('recycle_ratios') or reactor.get('recycles') or {}
    if isinstance(recycles, dict) and recycles:
        return dict(recycles)
    return {}

def _extract_steady_dynamic_reconciliation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Find the C2 reconciliation report in a (possibly wrapped) results payload.

    The reconciliation can be attached either INSIDE ``simulation_results`` or at
    the TOP level (a sibling of ``simulation_results``). Plain
    ``normalized_results_dict`` unwraps one ``simulation_results`` layer and
    DISCARDS the wrapper context, which would silently swallow a top-level
    reconciliation (incl. a FAIL verdict). This checks both the unwrapped inner
    data and the discarded wrapper context so a FAIL always survives normalization.
    """
    normalized = normalize_results_payload(payload)
    inner = normalized.data.get('steady_dynamic_reconciliation')
    if isinstance(inner, dict) and inner:
        return inner
    wrapper = normalized.wrapper_context.get('steady_dynamic_reconciliation')
    if isinstance(wrapper, dict) and wrapper:
        return wrapper
    return None

def _build_engine_diagnostics(results: dict[str, Any]) -> dict[str, Any]:
    """Build the EngineDiagnostics payload for a QSDsan ASM/ADM run.

    Populates ``universal`` from convergence / metadata, ``asm_bio`` from
    srt_control + reactor_sizing, and ``process_inhibition`` from the
    per-process health report attached to ``results['process_health']``
    by the result extractor (PR-DD). Keeps the shape dict-based so qsdsan-engine-mcp
    does not have to import the Pydantic models from engineering_utils
    (separate venv, partial install).
    """
    reconciliation = _extract_steady_dynamic_reconciliation(results)
    results = normalized_results_dict(results)
    convergence = results.get('convergence') or {}
    if not isinstance(convergence, dict):
        convergence = {}
    convergence_status = convergence.get('status')
    if not isinstance(convergence_status, str):
        if convergence.get('converged') is True:
            convergence_status = 'converged'
        elif convergence.get('converged') is False:
            convergence_status = 'failed'
        else:
            convergence_status = 'not_run'
    universal = {'warnings': list(results.get('warnings') or []), 'convergence': {'status': convergence_status, 'message': convergence.get('message'), 'residual_norm': convergence.get('final_residual') or convergence.get('residual')}, 'solver_metadata': {'solver_name': (results.get('metadata') or {}).get('solver') or convergence.get('method'), 'solve_time_s': (results.get('metadata') or {}).get('wall_time_s'), 'iterations': convergence.get('iterations')}, 'coverage_status': 'full' if results.get('effluent_quality') else 'partial', 'coverage_notes': []}
    srt_control = results.get('srt_control') or {}
    reactor_sizing = results.get('reactor_sizing') or {}
    raw_asm_bio = results.get('asm_bio') or results.get('diagnostics_asm_bio') or {}
    asm_bio: dict[str, Any] = dict(raw_asm_bio) if isinstance(raw_asm_bio, dict) else {}
    if isinstance(srt_control, dict):
        if srt_control.get('target_srt_days') is not None:
            asm_bio['srt_target_d'] = srt_control['target_srt_days']
        if srt_control.get('achieved_srt_days') is not None:
            asm_bio['srt_achieved_d'] = srt_control['achieved_srt_days']
        if srt_control.get('iterations') is not None:
            asm_bio['srt_iterations'] = srt_control['iterations']
    if isinstance(reactor_sizing, dict):
        mlss = reactor_sizing.get('representative_mlss_mg_l') or reactor_sizing.get('mlss_mg_l')
        if mlss is not None:
            asm_bio['mlss_mg_l'] = mlss
        biomass = reactor_sizing.get('biomass_composition') or reactor_sizing.get('asm_biomass')
        if isinstance(biomass, dict):
            asm_bio['biomass'] = biomass
    diagnostics = {'universal': universal}
    if asm_bio:
        diagnostics['asm_bio'] = asm_bio
    process_health = results.get('process_health')
    if isinstance(process_health, dict) and process_health.get('success'):
        diagnostics['process_inhibition'] = process_health
    dynamic_controls = results.get('dynamic_controls')
    if isinstance(dynamic_controls, dict):
        control_summary = {'enabled': dynamic_controls.get('enabled'), 'sample_time_h': dynamic_controls.get('sample_time_h'), 'reconciliation_note': dynamic_controls.get('reconciliation_note'), 'loops': []}
        for loop in dynamic_controls.get('loops') or []:
            if not isinstance(loop, dict):
                continue
            sensor = loop.get('sensor') if isinstance(loop.get('sensor'), dict) else {}
            actuator = loop.get('actuator') if isinstance(loop.get('actuator'), dict) else {}
            control_summary['loops'].append({'id': loop.get('id'), 'sample_time_h': loop.get('sample_time_h'), 'sensor_variable': sensor.get('variable'), 'sensor_lag_time_h': sensor.get('lag_time_h'), 'actuator_type': actuator.get('type'), 'actuator_target': actuator.get('target'), 'final_actuator_value': actuator.get('held_value'), 'saturation_fraction': actuator.get('saturation_fraction'), 'metrics': loop.get('metrics')})
        diagnostics['dynamic_controls'] = control_summary
    disturbances = results.get('disturbances')
    if isinstance(disturbances, dict) and disturbances.get('enabled'):
        history = disturbances.get('history') or []
        active_events = [row for row in history if isinstance(row, dict) and row.get('active')]
        disturbance_summary = {'enabled': True, 'disturbances': disturbances.get('disturbances'), 'feeds': disturbances.get('feeds'), 'applied_event_count': len(active_events), 'note': disturbances.get('note')}
        diagnostics['disturbances'] = disturbance_summary
    if reconciliation is None:
        reconciliation = results.get('steady_dynamic_reconciliation')
    if isinstance(reconciliation, dict) and reconciliation:
        diagnostics['steady_dynamic_reconciliation'] = reconciliation
    return diagnostics

def _build_engine_report(results: dict[str, Any], *, engine: str) -> dict[str, Any]:
    """Build one EngineReport-shaped dict for a QSDsan simulation.

    Kept as a plain dict (no Pydantic import) so this module stays
    import-light; the consolidator (PR-J) validates the shape upstream.
    """
    reconciliation = _extract_steady_dynamic_reconciliation(results)
    results = normalized_results_dict(results)
    if reconciliation is not None and (not results.get('steady_dynamic_reconciliation')):
        results = {**results, 'steady_dynamic_reconciliation': reconciliation}
    raw_predicted_streams = results.get('predicted_streams') or {}
    predicted_streams: dict[str, Any] = {}
    if isinstance(raw_predicted_streams, dict) and raw_predicted_streams:
        for stream_id, stream_data in raw_predicted_streams.items():
            if isinstance(stream_data, dict):
                predicted_streams[str(stream_id)] = stream_data
    else:
        effluent_quality = results.get('effluent_quality') or {}
        if isinstance(effluent_quality, dict):
            for stream_id, stream_data in effluent_quality.items():
                if isinstance(stream_data, dict):
                    predicted_streams[str(stream_id)] = stream_data
    flowsheet_svg = results.get('flowsheet_svg')
    report: dict[str, Any] = {'engine': engine, 'engine_family': 'bio', 'model_type': results.get('model_type') or ((results.get('metadata') or {}).get('model_type') if isinstance(results.get('metadata'), dict) else None) or '', 'unit_ops': list(results.get('unit_ops') or []), 'predicted_streams': predicted_streams, 'predicted_effluent': _build_predicted_effluent(results), 'diagnostics': _build_engine_diagnostics(results), 'risk_flags': list(results.get('risk_flags') or [])}
    if isinstance(flowsheet_svg, str) and flowsheet_svg.strip():
        report['flowsheet_svg'] = flowsheet_svg
    return report

def _build_simulation_report_payload(*, project_ref: str, plant_slug: str | None, producer_tool: str, job_id: str, job_dir: Path, results: dict[str, Any], session_id: str | None, engine: str, timeseries_full_ref: dict[str, Any] | None=None) -> dict[str, Any]:
    """Build the stage='c' single-engine simulation_report payload.

    Contract: matches ``engineering_utils.models.simulation_report.SimulationReport``
    (plus qsdsan-specific diagnostic annotations in ``metadata``). The
    shape is a stage-c row carrying exactly one entry in ``engine_reports``
    for this engine. The Stage-7b consolidator (PR-J) reads these rows and
    merges them into a plant-level consolidated simulation_report.
    """
    results = normalized_results_dict(results)
    validity_blockers = result_validity_blockers(results)
    if validity_blockers:
        raise RuntimeError(f'cannot build a canonical simulation report from an invalid simulation result: {validity_blockers}')
    engine_report = _build_engine_report(results, engine=engine)
    if timeseries_full_ref:
        engine_report['timeseries'] = timeseries_full_ref
    recycle_ratios = _build_recycle_ratios(results)
    flowsheet_svg = results.get('flowsheet_svg')
    if not isinstance(flowsheet_svg, str) or not flowsheet_svg.strip():
        raise RuntimeError("PR-DD simulation_report persistence requires in-memory flowsheet_svg from system.diagram(format='svg').")
    sfiles_string = results.get('sfiles_string')
    if not isinstance(sfiles_string, str) or not sfiles_string.strip():
        raise RuntimeError('PR-DD simulation_report persistence requires in-memory sfiles_string from system.get_SFILES().')
    report: dict[str, Any] = {'project_ref': project_ref, 'plant_slug': plant_slug, 'stage': 'c', 'generated_at': datetime.now(timezone.utc).isoformat(), 'producer_tool': producer_tool, 'job_id': job_id, 'model_type': engine_report['model_type'], 'predicted_effluent': _build_predicted_effluent(results), 'effluent_quality': results.get('effluent_quality') or {}, 'removal_efficiency': results.get('removal_efficiency') or {}, 'reactor_sizing': results.get('reactor_sizing') or {}, 'performance_summary': results.get('performance_summary') or {}, 'srt_control': results.get('srt_control') or {}, 'recycle_ratios': recycle_ratios, 'heuristic_validation': results.get('heuristic_validation') or {}, 'metadata': results.get('metadata') or {}, 'unit_ops': engine_report['unit_ops'], 'engine_reports': [engine_report], 'flowsheet_svg': flowsheet_svg, 'sfiles_string': sfiles_string}
    state_summary = results.get('state_summary')
    if isinstance(state_summary, dict) and state_summary:
        report['state_summary'] = state_summary
    if session_id:
        report.setdefault('metadata', {})['session_id'] = session_id
    return report

def _load_job_context(job_id: str) -> dict[str, Any]:
    job_dir = _job_dir(job_id)
    sim_config = _read_json_file(job_dir / 'sim_config.json')
    if sim_config:
        session_id = sim_config.get('session_id')
        project_ref = sim_config.get('project_ref')
        plant_slug = sim_config.get('plant_slug')
        if not project_ref and isinstance(session_id, str) and session_id:
            session_prov = _load_session_provenance(session_id)
            project_ref = session_prov.get('project_ref')
            if not plant_slug:
                plant_slug = session_prov.get('plant_slug')
        return {'job_id': job_id, 'job_dir': job_dir, 'producer_tool': 'qsdsan-engine-mcp.simulate_built_system', 'project_ref': project_ref, 'plant_slug': plant_slug, 'session_id': session_id}
    return {'job_id': job_id, 'job_dir': job_dir}

def _load_job_timeseries(job_dir: Path, results: dict[str, Any]) -> dict[str, Any] | None:
    """Return the raw timeseries payload produced by the job, or None.

    Checks ``results["time_series"]`` first (in-memory), then falls back to
    on-disk files the CLI writes (``timeseries.json``, ``time_series.json``).
    """
    results = normalized_results_dict(results)
    inline = results.get('time_series')
    if isinstance(inline, dict) and inline.get('time'):
        return inline
    for fname in ('timeseries.json', 'time_series.json'):
        candidate = job_dir / fname
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get('time'):
                return data
    return None

def _build_timeseries_full_payload(*, project_ref: str, plant_slug: str, engine: str, results: dict[str, Any], raw_ts: dict[str, Any]) -> dict[str, Any] | None:
    """Shape the engine timeseries into an ``EngineTimeseriesFullPayload``
    dict. Returns None when the minimum invariants cannot be satisfied.
    """
    time_axis = raw_ts.get('time')
    if not isinstance(time_axis, list) or not time_axis:
        return None
    time_unit = raw_ts.get('time_unit') or raw_ts.get('time_units') or 'd'
    if time_unit not in ('s', 'min', 'h', 'd'):
        time_unit = 'd'
    series = raw_ts.get('series')
    if not isinstance(series, list):
        series = []
    if not series and isinstance(raw_ts.get('streams'), dict):
        stream_series: list[dict[str, Any]] = []
        for stream_id, stream_payload in raw_ts['streams'].items():
            if not isinstance(stream_payload, dict):
                continue
            for component_id, values in stream_payload.items():
                if isinstance(values, list):
                    variable = str(component_id)
                    stream_series.append({'series_id': f'{stream_id}.{variable}', 'source_node': str(stream_id), 'stream_id': str(stream_id), 'variable': variable, 'units': 'mg/L', 'values': values})
        series = stream_series
    clean_series: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []
    for idx, s in enumerate(series):
        if not isinstance(s, dict):
            continue
        values = s.get('values') or s.get('trajectory')
        if not isinstance(values, list) or len(values) != len(time_axis):
            continue
        series_id = str(s.get('series_id') or s.get('id') or f'series_{idx}')
        unit_op = s.get('unit_op') or s.get('unit')
        variable = str(s.get('variable') or s.get('name') or series_id)
        units = s.get('units')
        source_node = s.get('source_node') or s.get('source')
        stream_id = s.get('stream_id')
        clean_series.append({'series_id': series_id, 'values': list(values)})
        variables.append({'series_id': series_id, 'unit_op': unit_op, 'stream_id': stream_id, 'variable': variable, 'units': units, 'source_node': source_node, 'export_completeness': 'full'})
    if not clean_series:
        return None
    model_type = results.get('model_type') or ((results.get('metadata') or {}).get('model_type') if isinstance(results.get('metadata'), dict) else None) or ''
    return {'project_ref': project_ref, 'plant_slug': plant_slug, 'engine': engine, 'engine_family': 'bio', 'model_type': str(model_type), 'generated_at': datetime.now(timezone.utc).isoformat(), 'time': [float(t) for t in time_axis], 'time_unit': time_unit, 'downsample_method': raw_ts.get('downsample_method') or 'uniform', 'source_step_count': raw_ts.get('source_step_count') or len(time_axis), 'variables': variables, 'series': clean_series}

def _maybe_persist_simulation_timeseries(*, project_ref: str, plant_slug: str | None, engine: str, producer_tool: str, job_id: str, job_dir: Path, results: dict[str, Any], persisted: dict[str, Any]) -> dict[str, Any] | None:
    """Persist the simulation_timeseries artifact if possible; return a
    reference dict suitable for ``EngineReport.timeseries`` or None.

    PR-K: plant_slug + engine are required by the artifact_persistence
    helper because the scope is keyed on (plant_slug, engine) and a row
    without either would cross-contaminate sibling simulations.
    """
    if not plant_slug:
        logger.warning('Simulation timeseries persistence skipped for job %s: plant_slug is required. Pass plant_slug to simulate_built_system.', job_id)
        return None
    raw_ts = _load_job_timeseries(job_dir, results)
    if not raw_ts:
        return None
    payload = _build_timeseries_full_payload(project_ref=project_ref, plant_slug=plant_slug, engine=engine, results=results, raw_ts=raw_ts)
    if not payload:
        return None
    artifact_id, content_hash = persist_engineering_artifact(project_ref=project_ref, artifact_type='simulation_timeseries', content=payload, producer_tool=producer_tool, scope_refs={'plant_slug': plant_slug, 'engine': engine, 'job_id': job_id})
    if artifact_id:
        persisted['simulation_timeseries_artifact_id'] = artifact_id
    if content_hash:
        persisted['simulation_timeseries_content_hash'] = content_hash
    if not artifact_id:
        return None
    return {'time': [], 'time_unit': payload['time_unit'], 'downsample_method': payload['downsample_method'], 'series': [], 'timeseries_full_uri': f'engineering_artifact://{artifact_id}', 'timeseries_full_artifact_id': artifact_id, 'timeseries_full_content_hash': content_hash}

def _load_job_stream_states(job_dir: Path) -> dict[str, Any]:
    path = job_dir / 'stream_states.json'
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def _payload_has_topic(payload: Any, tokens: tuple[str, ...]) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if any((token in key_l for token in tokens)):
                return True
            if _payload_has_topic(value, tokens):
                return True
    elif isinstance(payload, list):
        return any((_payload_has_topic(item, tokens) for item in payload))
    return False

def _build_accessibility_contract(*, results: dict[str, Any], stream_states: dict[str, Any], raw_ts: dict[str, Any] | None) -> dict[str, Any]:
    diagnostic_source = {'process_health': results.get('process_health'), 'asm_bio': results.get('asm_bio') or results.get('diagnostics_asm_bio'), 'unit_analysis': results.get('unit_analysis'), 'per_engine_summaries': results.get('per_engine_summaries')}
    return {'intended_reader': 'pe-process', 'human_reporting_ready': True, 'contains_unit_report_text': False, 'contains_unit_analysis': isinstance(results.get('unit_analysis'), dict) and bool(results.get('unit_analysis')), 'contains_state_summary': isinstance(results.get('state_summary'), dict) and bool(results.get('state_summary')), 'contains_stream_states': bool(stream_states), 'contains_timeseries': isinstance(raw_ts, dict) and bool(raw_ts.get('time')), 'contains_biological_rates': _payload_has_topic(diagnostic_source, ('rate', 'reaction', 'rho', 'growth', 'decay', 'hydrolysis', 'uptake', 'methan', 'nitrif', 'denitrif')), 'contains_biological_inhibition': _payload_has_topic(diagnostic_source, ('inhib', 'inhibition', 's_h2', 's_nh3', 's_nh4', 's_o2', 's_no2', 's_no3', 'vfa'))}

def _build_simulation_diagnostics_payload(*, project_ref: str | None, plant_slug: str | None, producer_tool: str, job_id: str, job_dir: Path, results: dict[str, Any], session_id: str | None, engine: str) -> dict[str, Any]:
    """Build the standalone diagnostics payload for a completed QSDsan run."""
    results = normalized_results_dict(results)
    stream_states = _load_job_stream_states(job_dir)
    raw_ts = _load_job_timeseries(job_dir, results)
    session_payload = _read_json_file(job_dir / 'session.json')
    build_config = _read_json_file(job_dir / 'build_config.json')
    sim_config = _read_json_file(job_dir / 'sim_config.json')
    raw_results = {key: value for key, value in results.items() if key not in {'time_series'}}
    process_diagnostics = extract_process_diagnostics(results)
    simulation_diagnostics = extract_simulation_diagnostics(results, sim_config=sim_config, raw_ts=raw_ts, stream_states=stream_states, job_dir=job_dir)
    validity_blockers = result_validity_blockers(results)
    return {'schema_version': 'qsdsan_simulation_diagnostics/v1', 'project_ref': project_ref, 'plant_slug': plant_slug, 'engine': 'qsdsan-engine-mcp', 'engine_name': engine, 'engine_run_id': job_id, 'job_id': job_id, 'session_id': session_id, 'producer_tool': producer_tool, 'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'invalid_result' if validity_blockers else 'completed', 'result_valid': not validity_blockers, 'result_validity_blockers': validity_blockers, 'model_type': results.get('model_type') or (results.get('metadata') or {}).get('model_type'), 'unit_ops': list(results.get('unit_ops') or session_payload.get('units', {}).keys()), 'session': {'primary_model_type': session_payload.get('primary_model_type'), 'model_types': session_payload.get('model_types', []), 'stream_roles': session_payload.get('stream_roles', {}), 'startup_metadata': session_payload.get('startup_metadata', {}), 'build_config': build_config, 'simulation_config': sim_config}, 'simulation_diagnostics': simulation_diagnostics, 'process_diagnostics': process_diagnostics, 'stream_states': stream_states, 'raw_results': raw_results, 'accessibility_contract': _build_accessibility_contract(results=results, stream_states=stream_states, raw_ts=raw_ts)}

def _safe_filename(value: str) -> str:
    return re.sub('[^A-Za-z0-9_.-]+', '_', value).strip('_') or 'stream'

def _resolve_mcp_outputs_dir(*, job_dir: Path, session_id: str | None, mcp_outputs_dir: str | None=None) -> Path | None:
    if mcp_outputs_dir:
        return Path(mcp_outputs_dir)
    sim_config = _read_json_file(job_dir / 'sim_config.json')
    if sim_config.get('mcp_outputs_dir'):
        return Path(str(sim_config['mcp_outputs_dir']))
    if sim_config.get('project_dir'):
        return Path(str(sim_config['project_dir'])) / 'mcp-outputs'
    if session_id:
        provenance = _load_session_provenance(session_id)
        if provenance.get('mcp_outputs_dir'):
            return Path(str(provenance['mcp_outputs_dir']))
        if provenance.get('project_dir'):
            return Path(str(provenance['project_dir'])) / 'mcp-outputs'
    if os.environ.get('QSDSAN_ENGINE_MCP_OUTPUTS_DIR'):
        return Path(os.environ['QSDSAN_ENGINE_MCP_OUTPUTS_DIR'])
    return None

def _mirror_simulation_outputs(*, job_id: str, job_dir: Path, session_id: str | None, report_payload: dict[str, Any], diagnostics_payload: dict[str, Any], project_ref: str, plant_slug: str | None, mcp_outputs_dir: str | None=None) -> dict[str, Any]:
    output_dir = _resolve_mcp_outputs_dir(job_dir=job_dir, session_id=session_id, mcp_outputs_dir=mcp_outputs_dir)
    if output_dir is None:
        return {'skipped': 'No mcp_outputs_dir, project_dir, or QSDSAN_ENGINE_MCP_OUTPUTS_DIR configured'}
    streams_dir = output_dir / 'streams'
    diagnostics_dir = output_dir / 'diagnostics'
    figures_dir = output_dir / 'figures'
    timeseries_dir = output_dir / 'timeseries'
    for directory in (output_dir, streams_dir, diagnostics_dir, figures_dir, timeseries_dir):
        directory.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / 'simulation_report.json'
    report_path.write_text(json.dumps(report_payload, indent=2, default=str))
    diagnostics_path = diagnostics_dir / f'{_safe_filename(job_id)}.json'
    diagnostics_path.write_text(json.dumps(diagnostics_payload, indent=2, default=str))
    flowsheet_path = figures_dir / 'flowsheet.svg'
    flowsheet_svg = report_payload.get('flowsheet_svg')
    if isinstance(flowsheet_svg, str) and flowsheet_svg.strip():
        flowsheet_path.write_text(flowsheet_svg)
    elif (job_dir / 'flowsheet.svg').exists():
        shutil.copyfile(job_dir / 'flowsheet.svg', flowsheet_path)
    stream_paths: dict[str, str] = {}
    for stream_id, payload in _load_job_stream_states(job_dir).items():
        if not isinstance(payload, dict):
            continue
        stream_path = streams_dir / f'{_safe_filename(stream_id)}.json'
        stream_doc = {'project_ref': project_ref, 'plant_slug': plant_slug, 'stream_id': stream_id, **payload}
        stream_path.write_text(json.dumps(stream_doc, indent=2, default=str))
        stream_paths[stream_id] = str(stream_path)
    timeseries_path = None
    raw_ts = _load_job_timeseries(job_dir, {})
    if raw_ts:
        timeseries_path = timeseries_dir / f'{_safe_filename(job_id)}.json'
        timeseries_path.write_text(json.dumps(raw_ts, indent=2, default=str))
    return {'mcp_outputs_dir': str(output_dir), 'simulation_report': str(report_path), 'diagnostics': str(diagnostics_path), 'flowsheet_svg': str(flowsheet_path) if flowsheet_path.exists() else None, 'streams': stream_paths, 'timeseries': str(timeseries_path) if timeseries_path else None}

def _sanitize_boundary_token(value: str) -> str:
    cleaned = re.sub('[^A-Za-z0-9_.-]+', '_', value.strip())
    return cleaned or 'unknown'

def _source_boundary_tag(stream_id: str, roles: list[str]) -> str:
    role_set = {str(role).lower() for role in roles}
    if role_set & {'influent', 'feed', 'raw_influent'}:
        return 'source.raw_influent'
    return f'source.{_sanitize_boundary_token(stream_id)}'

def _sink_boundary_tag(stream_id: str, roles: list[str]) -> str:
    role_set = {str(role).lower() for role in roles}
    if 'was' in role_set:
        return 'sink.was_disposal'
    if role_set & {'effluent', 'permeate', 'product'}:
        return 'sink.final_effluent'
    if 'supernatant' in role_set:
        return 'sink.supernatant_return'
    if 'centrate' in role_set:
        return 'sink.centrate_return'
    if role_set & {'cake', 'sludge_cake'}:
        return 'sink.dewatered_cake'
    return f'sink.{_sanitize_boundary_token(stream_id)}'

def _stream_snapshot_components(stream_state: dict[str, Any], *, analyzed: dict[str, Any] | None=None) -> dict[str, Any]:
    components: dict[str, Any] = {}
    raw_components = stream_state.get('concentrations') or stream_state.get('components') or {}
    if isinstance(raw_components, dict):
        components.update(raw_components)
    if isinstance(analyzed, dict):
        components.update(analyzed)
    return components

def _collect_engine_boundary_snapshots(*, session: FlowsheetSession, results: dict[str, Any], stream_states: dict[str, Any], engine: str, project_ref: str, plant_slug: str | None, model_run_id: str) -> list[dict[str, Any]]:
    role_map = derive_session_stream_roles(session)
    produced_by: dict[str, str] = {}
    consumers_by_stream: dict[str, list[str]] = {}
    for unit_id, config in session.units.items():
        for output_id in list(config.outputs or []):
            if output_id:
                produced_by[str(output_id)] = unit_id
        for input_id in list(config.inputs or []):
            if not input_id or not isinstance(input_id, str) or ('-' in input_id and input_id not in session.streams and (input_id not in produced_by)):
                continue
            consumers_by_stream.setdefault(str(input_id), []).append(unit_id)
    ordered_unit_ids = [unit_id for unit_id in results.get('unit_ops') or [] if unit_id in session.units]
    ordered_unit_ids.extend((unit_id for unit_id in session.units if unit_id not in ordered_unit_ids))
    effluent_quality = results.get('effluent_quality') or {}
    seen_pairs: set[tuple[str, str]] = set()
    seen_stream_ids: set[str] = set()
    snapshots: list[dict[str, Any]] = []

    def _append_snapshot(snapshot: dict[str, Any]) -> None:
        snapshot_stream_id = str(snapshot.get('stream_id') or '')
        if snapshot_stream_id in seen_stream_ids:
            raise ValueError(f'Duplicate canonical stream_snapshot stream_id emitted for {snapshot_stream_id!r}; Stage 7b requires one row per seed stream_id.')
        seen_stream_ids.add(snapshot_stream_id)
        snapshots.append(snapshot)
    for unit_id in ordered_unit_ids:
        config = session.units[unit_id]
        for input_id in list(config.inputs or []):
            stream_id = str(input_id)
            if stream_id in produced_by:
                continue
            stream_state = stream_states.get(stream_id)
            if not isinstance(stream_state, dict):
                continue
            upstream_tag = _source_boundary_tag(stream_id, role_map.get(stream_id, []))
            pair = (upstream_tag, unit_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            _append_snapshot({'stream_id': stream_id, 'upstream_unit_tag': upstream_tag, 'downstream_unit_tag': unit_id, 'flow_m3_d': _coerce_float(stream_state.get('flow_m3_d')), 'temperature_k': _coerce_float(stream_state.get('temperature_K') or stream_state.get('temperature_k')), 'components': _stream_snapshot_components(stream_state), 'status_code': 'completed', 'scope_refs': {'project_ref': project_ref, 'plant_slug': plant_slug, 'engine': engine, 'model_run_id': model_run_id}})
        for output_id in list(config.outputs or []):
            stream_id = str(output_id)
            stream_state = stream_states.get(stream_id)
            if not isinstance(stream_state, dict):
                continue
            downstream_tags = consumers_by_stream.get(stream_id) or [_sink_boundary_tag(stream_id, role_map.get(stream_id, []))]
            analyzed = effluent_quality.get(stream_id) if isinstance(effluent_quality, dict) else None
            if analyzed is None and role_map.get(stream_id) and {str(r).lower() for r in role_map.get(stream_id, [])} & {'effluent', 'permeate', 'product'}:
                analyzed = effluent_quality.get('final_effluent') if isinstance(effluent_quality, dict) else None
            for downstream_tag in downstream_tags:
                pair = (unit_id, downstream_tag)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                _append_snapshot({'stream_id': stream_id, 'upstream_unit_tag': unit_id, 'downstream_unit_tag': downstream_tag, 'flow_m3_d': _coerce_float(stream_state.get('flow_m3_d')), 'temperature_k': _coerce_float(stream_state.get('temperature_K') or stream_state.get('temperature_k')), 'components': _stream_snapshot_components(stream_state, analyzed=analyzed if isinstance(analyzed, dict) else None), 'status_code': 'completed', 'scope_refs': {'project_ref': project_ref, 'plant_slug': plant_slug, 'engine': engine, 'model_run_id': model_run_id}})
    return snapshots
session_manager = FlowsheetSessionManager(QSDSAN_STORAGE_DIR)

@mcp.tool()
async def get_version() -> Dict[str, Any]:
    """Return engine and dependency versions without importing heavy flowsheet code."""
    return get_version_info()

@mcp.tool()
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Check status of a background simulation job.

    Args:
        job_id: Job identifier from simulate_built_system or convert_state

    Returns:
        Dict with job_id, status, elapsed_time_seconds, progress hints
    """
    return await job_manager.get_status(job_id)

@mcp.tool(annotations={'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False})
async def read_job_results(job_id: str) -> Dict[str, Any]:
    """Read normalized simulation results and validity diagnostics without persistence.

    Does not write engineering artifacts or stream snapshots, generate a report,
    or approve results. Time series are excluded for compact retrieval; request
    get_timeseries_data or get_flowsheet_timeseries for the required series.
    A completed job may still carry result_valid=False and validity blockers.

    Args:
        job_id: Identifier of the simulation job to read.

    Returns:
        Job lifecycle, normalized engineering results, validity and log references,
        or the existing missing/incomplete-job error.
    """
    return normalize_job_response(await job_manager.get_results(job_id))

@mcp.tool()
async def build_simulation_diagnostics_payload(job_id: str, project_ref: Optional[str]=None, plant_slug: Optional[str]=None) -> Dict[str, Any]:
    """Build the full QSDsan diagnostics payload for pe-process reporting."""
    response = await job_manager.get_results(job_id)
    response = normalize_job_response(response, drop_time_series=False)
    results = response.get('results')
    if not isinstance(results, dict) or response.get('status') != 'completed':
        return public_error(response.get('message') or f'Job {job_id} has no completed results', code=response.get('code') or 'job_not_completed', job_id=job_id)
    context = _load_job_context(job_id)
    job_dir = context.get('job_dir') or _job_dir(job_id)
    resolved_project_ref = project_ref or context.get('project_ref')
    resolved_plant_slug = plant_slug or context.get('plant_slug')
    return _build_simulation_diagnostics_payload(project_ref=resolved_project_ref if isinstance(resolved_project_ref, str) else None, plant_slug=resolved_plant_slug if isinstance(resolved_plant_slug, str) else None, producer_tool=context.get('producer_tool') or 'qsdsan-engine-mcp.simulate_built_system', job_id=job_id, job_dir=job_dir, results=results, session_id=context.get('session_id') if isinstance(context.get('session_id'), str) else None, engine=_derive_engine_name(results))

@mcp.tool()
async def get_process_diagnostics(job_id: str, project_ref: Optional[str]=None, plant_slug: Optional[str]=None) -> Dict[str, Any]:
    """Return persisted-ready QSDsan diagnostics for pe-process human reporting."""
    return await build_simulation_diagnostics_payload(job_id=job_id, project_ref=project_ref, plant_slug=plant_slug)

@mcp.tool()
async def validate_state(state: Dict[str, Any], model_type: str, check_charge_balance: bool=True, check_mass_balance: bool=True) -> Dict[str, Any]:
    """
    Validate StreamState against model requirements.

    Checks:
    - Required components present
    - Charge balance (electroneutrality)
    - Mass balance (COD, TSS, TKN, TP)
    - Concentration bounds

    Args:
        state: StreamState dict with explicit concentration_units and component_basis
        model_type: Target model type ("mADM1", "ASM2d", etc.)
        check_charge_balance: Whether to check electroneutrality
        check_mass_balance: Whether to check mass balance closure

    Returns:
        StateValidationReport as dict
    """
    try:
        stream_state = StreamState.model_validate(state)
        mt = ModelType(normalize_model_name(model_type))
        model_info = get_model_info(mt)
        checks: List[StateValidationCheck] = []
        if stream_state.model_type != mt.value:
            checks.append(StateValidationCheck(name='model_type.match', passed=False, severity='error', message=f'state.model_type must match requested model_type ({stream_state.model_type!r} != {mt.value!r})'))
        provided = set(stream_state.concentrations.keys())
        missing, extra = validate_components(mt, provided)
        if missing:
            checks.append(StateValidationCheck(name='components.missing', passed=False, severity='error', message=f'Missing required components: {missing[:10]}{('...' if len(missing) > 10 else '')}', details={'missing_components': missing}))
        if extra:
            checks.append(StateValidationCheck(name='components.extra', passed=False, severity='error', message=f'Extra components are not allowed: {extra[:5]}{('...' if len(extra) > 5 else '')}', details={'extra_components': extra}))
        checks.append(StateValidationCheck(name='flow.nonnegative', passed=stream_state.flow_m3_d >= 0, severity='error', message=f'flow_m3_d must be nonnegative, got {stream_state.flow_m3_d}'))
        if stream_state.temperature_K < 273.15 or stream_state.temperature_K > 373.15:
            checks.append(StateValidationCheck(name='temperature.range', passed=False, severity='warning', message=f'Temperature {stream_state.temperature_K} K outside typical range (273-373 K)'))
        negative = [k for k, v in stream_state.concentrations.items() if v < 0]
        if negative:
            checks.append(StateValidationCheck(name='concentrations.nonnegative', passed=False, severity='error', message=f'Negative concentrations: {negative}', details={'negative_components': negative}))
        from qsdsan_engine_mcp.core.contracts.stream_state import validate_concentration_bounds
        for warning in validate_concentration_bounds(stream_state.concentrations, stream_state.model_type, stream_state.concentration_units):
            checks.append(StateValidationCheck(name='concentrations.bounds', passed=True, severity='warning', message=warning))
        consistency_result = None
        if check_mass_balance:
            from qsdsan_engine_mcp.core.converters import validate_state_consistency
            consistency_result = validate_state_consistency(stream_state)
            if not consistency_result.get('passed', True):
                checks.append(StateValidationCheck(name='mass_balance.consistency', passed=False, severity='error', message='Mass balance consistency failed', details={'warnings': consistency_result.get('warnings', [])}))
        charge_balance = None
        if check_charge_balance and mt == ModelType.MADM1:
            from qsdsan_engine_mcp.core.converters import validate_charge_balance
            charge_result = validate_charge_balance(stream_state)
            charge_balance = {'cation_meq_L': charge_result.get('cation_meq_L', 0), 'anion_meq_L': charge_result.get('anion_meq_L', 0), 'imbalance_meq_L': charge_result.get('imbalance_meq_L', 0), 'passed': charge_result.get('passed', True)}
            if not charge_balance['passed']:
                checks.append(StateValidationCheck(name='charge_balance.electroneutrality', passed=False, severity='warning', message=f'Charge balance error: imbalance {charge_balance['imbalance_meq_L']:.2f} meq/L', details=charge_balance))
        report = StateValidationReport(model_type=mt.value, checks=checks, charge_balance=charge_balance, mass_balance_payload=consistency_result, missing_components=missing, extra_components=extra)
        return report.model_dump(exclude={'checks', 'mass_balance_payload'})
    except Exception as e:
        logger.error(f'validate_state failed: {e}', exc_info=True)
        return public_error(e, code='VALIDATION_FAILED', is_valid=False)

@mcp.tool()
async def convert_state(state: Dict[str, Any], from_model: str, to_model: str) -> Dict[str, Any]:
    """
    Convert StreamState between model types using the shared upstream
    ADM1p <-> mASM2d junction-backed converter path.

    This tool runs as a background job for complex conversions.
    Supports ASM2d <-> mADM1 and mASM2d <-> mADM1 conversions.
    The exact design-grade path is mASM2d <-> mADM1; plain ASM2d is projected
    through mASM2d because it lacks the mineral/ion basis required by A1.

    Args:
        state: StreamState dict with explicit concentration_units and component_basis
        from_model: Source model type ("ASM2d", "mADM1", etc.)
        to_model: Target model type

    Returns:
        Dict with job_id for tracking.

    Example:
        # Convert WAS from activated sludge to anaerobic digester
        >>> result = await convert_state(
        ...     state={"model_type": "ASM2d", "flow_m3_d": 100, "concentrations": {...}},
        ...     from_model="ASM2d",
        ...     to_model="mADM1"
        ... )
    """
    try:
        from_mt = ModelType(normalize_model_name(from_model))
        to_mt = ModelType(normalize_model_name(to_model))
        stream_state = StreamState.model_validate(state)
        if stream_state.model_type != from_mt.value:
            return public_error('state.model_type must match from_model', code='MODEL_TYPE_MISMATCH', state_model_type=stream_state.model_type, from_model=from_mt.value)
        state_data = stream_state.model_dump(mode='json')
        if from_mt == to_mt:
            return {'status': 'no_conversion_needed', 'message': f'Source and target model are both {from_mt.value}', 'state': stream_state.model_dump()}
        supported_conversions = [(ModelType.ASM2D, ModelType.MADM1), (ModelType.MADM1, ModelType.ASM2D), (ModelType.MASM2D, ModelType.MADM1), (ModelType.MADM1, ModelType.MASM2D)]
        if (from_mt, to_mt) not in supported_conversions:
            return public_error(f'Conversion from {from_mt.value} to {to_mt.value} not supported', code='UNSUPPORTED_CONVERSION', supported=[f'{f.value} -> {t.value}' for f, t in supported_conversions])
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = _job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        with open(job_dir / 'input_state.json', 'w') as f:
            json.dump(state_data, f, indent=2)
        python_exe = get_python_executable()
        cmd = [python_exe, '-m', 'qsdsan_engine_mcp.job_cli', 'convert', '--input', str(job_dir / 'input_state.json'), '--output', str(job_dir / 'output_state.json'), '--from-model', from_mt.value, '--to-model', to_mt.value]
        cwd = str(Path(__file__).parent.absolute())
        job = await job_manager.execute(cmd=cmd, cwd=cwd, job_id=job_id)
        return {'job_id': job['id'], 'status': job['status'], 'conversion': f'{from_mt.value} -> {to_mt.value}', 'conversion_method': 'junction', 'message': f"Conversion started. Use get_job_status('{job['id']}') to monitor."}
    except Exception as e:
        logger.error(f'convert_state failed: {e}', exc_info=True)
        return public_error(e, code='INVALID_INPUT')

@mcp.tool()
async def list_jobs(status_filter: Optional[str]=None, limit: int=20) -> Dict[str, Any]:
    """
    List all background jobs with optional status filter.

    Args:
        status_filter: Filter by status ("running", "completed", "failed", or None)
        limit: Maximum number of jobs to return

    Returns:
        Dict with jobs list and summary
    """
    return await job_manager.list_jobs(status_filter=status_filter, limit=limit)

@mcp.tool()
async def terminate_job(job_id: str) -> Dict[str, Any]:
    """
    Terminate a running background job.

    Args:
        job_id: Job identifier to terminate

    Returns:
        Dict with termination status
    """
    return await job_manager.terminate_job(job_id)

@mcp.tool()
async def get_timeseries_data(job_id: str) -> Dict[str, Any]:
    """
    Get time series data from a completed simulation job.

    Time series data is excluded from get_job_results() to avoid token limits.
    Use this tool only when you specifically need the time series for plotting.

    Args:
        job_id: Job identifier

    Returns:
        Dict with time series data or error
    """
    return await job_manager.get_timeseries_data(job_id)

@mcp.tool()
async def create_flowsheet_session(model_type: str, session_id: Optional[str]=None) -> Dict[str, Any]:
    """
    Create a new flowsheet construction session.

    Sessions persist to disk in the configured flowsheet session directory
    (default ``jobs/flowsheets/{session_id}/``, overrideable via
    ``QSDSAN_ENGINE_SESSIONS_DIR``) and survive MCP reconnections.
    Use this to start building a custom flowsheet.

    Args:
        model_type: Primary process model (e.g., "ASM2d", "mADM1")
        session_id: Optional custom session ID. Auto-generates if not provided.

    Returns:
        Dict with session_id, model_type, and available units for that model

    Example:
        >>> result = await create_flowsheet_session(model_type="ASM2d")
        >>> session_id = result["session_id"]
        >>> # Now use create_stream(), create_unit(), etc.
    """
    try:
        canonical_model = normalize_model_name(model_type)
        session = session_manager.create_session(model_type=canonical_model, session_id=session_id)
        compatible_units = get_all_units(model_type=canonical_model)
        return {'session_id': session.session_id, 'model_type': canonical_model, 'status': 'created', 'compatible_units': [u['unit_type'] for u in compatible_units], 'message': f"Session created. Add streams with create_stream('{session.session_id}', ...)"}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', model_type=model_type, session_id=session_id)
    except Exception as e:
        logger.error(f'create_flowsheet_session failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_CREATE_FAILED', model_type=model_type, session_id=session_id)

@mcp.tool()
async def create_session(model_type: str, session_id: Optional[str]=None) -> Dict[str, Any]:
    """Dynamic-session alias for create_flowsheet_session."""
    return await create_flowsheet_session(model_type=model_type, session_id=session_id)

@mcp.tool()
async def create_stream(session_id: str, stream_id: Optional[str]=None, flow_m3_d: Optional[float]=None, concentrations: Optional[Dict[str, float]]=None, temperature_K: Optional[float]=293.15, concentration_units: Optional[str]=None, stream_type: str='influent', model_type: Optional[str]=None, stream_state: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """
    Create a WasteStream in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        stream_id: Unique stream identifier (e.g., "influent", "RAS")
        flow_m3_d: Flow rate in m3/day
        concentrations: Dict of component ID -> concentration value
        temperature_K: Temperature in Kelvin (default 293.15 = 20C)
        concentration_units: Required units for concentration values: "mg/L" or "kg/m3"
        stream_type: One of "influent", "recycle", "intermediate"
        model_type: Process model override (defaults to session's primary model)
        stream_state: Optional canonical StreamState payload; rejects extras

    Returns:
        Dict with stream_id and validation status

    Example:
        >>> await create_stream(
        ...     session_id="abc123",
        ...     stream_id="influent",
        ...     flow_m3_d=4000,
        ...     concentrations={"S_F": 75, "S_A": 20, "S_NH4": 17},
        ...     concentration_units="mg/L",
        ... )
    """
    try:
        session = session_manager.get_session(session_id)
        if stream_state is not None:
            state = StreamState.model_validate(stream_state)
            if state.stream_id is None:
                return public_error('stream_state.stream_id is required for create_stream', code='INVALID_INPUT', session_id=session_id)
            if stream_id is not None and stream_id != state.stream_id:
                return public_error('stream_id does not match stream_state.stream_id', code='INVALID_INPUT', stream_id=stream_id, state_stream_id=state.stream_id)
            if model_type is not None and normalize_model_name(model_type) != state.model_type:
                return public_error('model_type does not match stream_state.model_type', code='MODEL_TYPE_MISMATCH', model_type=model_type, state_model_type=state.model_type)
        else:
            effective_model = normalize_model_name(model_type or session.primary_model_type)
            state = StreamState.from_concentrations(stream_id=stream_id, model_type=effective_model, flow_m3_d=flow_m3_d, temperature_K=temperature_K, concentration_units=concentration_units, concentrations=concentrations or {})
            if state.stream_id is None:
                return public_error('stream_id is required', code='INVALID_INPUT')
        config = StreamConfig(stream_id=state.stream_id, flow_m3_d=state.flow_m3_d, temperature_K=state.temperature_K, concentrations=dict(state.concentrations), concentration_units=state.concentration_units, stream_type=stream_type, model_type=state.model_type)
        result = session_manager.add_stream(session_id, config)
        return result
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, stream_id=stream_id)
    except Exception as e:
        logger.error(f'create_stream failed: {e}', exc_info=True)
        return public_error(e, code='STREAM_CREATE_FAILED', session_id=session_id, stream_id=stream_id)

def _auto_insert_junction(session: 'FlowsheetSessionManager', source_unit_id: str, source_port: int, source_model: str, target_model: str) -> tuple[Optional[str], str, Optional[str]]:
    """
    Phase 10: Auto-insert a junction unit to convert source_model to target_model.

    Creates a junction unit in the session to bridge model mismatches at fan-in points.

    NOTE: For our catalog-driven extended mADM1, the canonical mixed-model
    junctions come from the dedicated A1 adapter module.

    Args:
        session: FlowsheetSession instance
        source_unit_id: ID of the upstream unit
        source_port: Output port index of the upstream unit
        source_model: Model type of the source (e.g., "mADM1")
        target_model: Target model type (e.g., "ASM2d")

    Returns:
        Tuple of (junction_unit_id, junction_output_port, warning_message)
        Returns (None, original_port, None) if no junction available
    """
    junction_type = find_junction_for_conversion(source_model, target_model)
    if not junction_type:
        return (None, f'{source_unit_id}-{source_port}', None)
    junction_id = f'_auto_{junction_type}_{source_unit_id}'
    counter = 1
    while junction_id in session.units:
        junction_id = f'_auto_{junction_type}_{source_unit_id}_{counter}'
        counter += 1
    junction_config = UnitConfig(unit_id=junction_id, unit_type=junction_type, params={}, inputs=[f'{source_unit_id}-{source_port}'], outputs=[f'{junction_id}-0'], model_type=target_model, auto_inserted=True)
    session.units[junction_id] = junction_config
    logger.info(f"Phase 10: Auto-inserted {junction_type} junction '{junction_id}' to convert {source_model} -> {target_model}")
    warning = f"Auto-inserted junction '{junction_id}' to convert {source_model} -> {target_model} for input '{source_unit_id}-{source_port}'"
    return (junction_id, f'{junction_id}-0', warning)

def _rewrite_inputs_with_junctions(session: 'FlowsheetSessionManager', inputs: List[str], target_model: str) -> tuple[List[str], List[str]]:
    """
    Phase 10: Rewrite inputs to auto-insert junctions where needed.

    Scans inputs for model mismatches and auto-inserts junction units to unify
    component sets at fan-in points.

    Args:
        session: FlowsheetSession instance
        inputs: Original list of input port notations or stream IDs
        target_model: Target model type for the unit being created

    Returns:
        Tuple of (rewritten_inputs, auto_insert_warnings)
    """
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation
    rewritten_inputs = []
    warnings = []
    target_norm = normalize_model_name(target_model)
    for inp in inputs:
        if is_tuple_notation(inp):
            port_strs = parse_tuple_notation(inp)
            rewritten_ports = []
            for port_str in port_strs:
                try:
                    ref = parse_port_notation(port_str)
                    inp_model = _get_model_for_input(session, ref)
                    if inp_model and normalize_model_name(inp_model) != target_norm:
                        junction_id, new_port, warning = _auto_insert_junction(session, ref.unit_id, ref.port_index, inp_model, target_model)
                        if junction_id:
                            rewritten_ports.append(new_port)
                            if warning:
                                warnings.append(warning)
                        else:
                            rewritten_ports.append(port_str)
                            warnings.append(f"No junction available to convert {inp_model} -> {target_model} for input '{port_str}'. Component mismatch may cause build failure.")
                    else:
                        rewritten_ports.append(port_str)
                except ValueError:
                    rewritten_ports.append(port_str)
            rewritten_inputs.append('(' + ', '.join(rewritten_ports) + ')')
        else:
            try:
                ref = parse_port_notation(inp)
                inp_model = _get_model_for_input(session, ref)
                if inp_model and normalize_model_name(inp_model) != target_norm:
                    junction_id, new_port, warning = _auto_insert_junction(session, ref.unit_id, ref.port_index, inp_model, target_model)
                    if junction_id:
                        rewritten_inputs.append(new_port)
                        if warning:
                            warnings.append(warning)
                    else:
                        rewritten_inputs.append(inp)
                        warnings.append(f"No junction available to convert {inp_model} -> {target_model} for input '{inp}'. Component mismatch may cause build failure.")
                else:
                    rewritten_inputs.append(inp)
            except ValueError:
                rewritten_inputs.append(inp)
    return (rewritten_inputs, warnings)

def _get_model_for_input(session, ref) -> Optional[str]:
    """
    Get the effective model for an input reference.

    Args:
        session: FlowsheetSession instance
        ref: Parsed port reference

    Returns:
        Model type string or None if unknown
    """
    upstream_unit_id = ref.unit_id
    if upstream_unit_id in session.units:
        upstream_config = session.units[upstream_unit_id]
        junction_transform = get_junction_output_model(upstream_config.unit_type)
        if junction_transform:
            return junction_transform[1]
        elif upstream_config.model_type:
            return upstream_config.model_type
        else:
            return session.primary_model_type
    elif upstream_unit_id in session.streams:
        stream_config = session.streams[upstream_unit_id]
        return stream_config.model_type or session.primary_model_type
    else:
        named_output_model = get_named_output_model(session.units, upstream_unit_id)
        if named_output_model:
            return named_output_model
        return session.primary_model_type

@mcp.tool()
async def create_unit(session_id: str, unit_type: str, unit_id: str, inputs: List[str], outputs: Optional[List[str]]=None, model_type: Optional[str]=None, kinetic_model: Optional[str]=None, engineering_profile: str='default', engineering_inputs: Optional[Dict[str, Any]]=None, stream_topology: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """
    Create a SanUnit in the flowsheet session.

    Args:
        session_id: Session identifier from create_flowsheet_session
        unit_type: Unit type from registry (e.g., "CSTR", "Splitter", "CompletelyMixedMBR")
        unit_id: Unique unit identifier (e.g., "A1", "O1", "MBR")
        inputs: List of input sources (stream IDs or pipe notation like "A1-0")
        outputs: Optional list of output stream names
        model_type: Process model override (defaults to session's primary model)
        engineering_profile: Public engineering profile identifier
        engineering_inputs: Process-engineering inputs for sizing/operation assumptions

    Returns:
        Dict with unit_id, validation status, and port info

    Example:
        >>> await create_unit(
        ...     session_id="abc123",
        ...     unit_type="CSTR",
        ...     unit_id="A1",
        ...     inputs=["influent", "RAS"],
        ...     engineering_inputs={"V_max": 1000},
        ... )
    """
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation, is_tuple_notation, parse_tuple_notation
    try:
        try:
            spec = get_unit_spec_from_registry(unit_type)
        except ValueError as e:
            return public_error(e, code='UNIT_TYPE_UNKNOWN', unit_type=unit_type)
        support_error = get_public_support_error(unit_type)
        if support_error:
            return public_error(support_error, code='UNIT_NOT_PUBLICLY_SUPPORTED', unit_type=unit_type)
        compiled_inputs = compile_unit_engineering_inputs(unit_type, engineering_inputs)
        if compiled_inputs['errors']:
            return public_error('Engineering input validation failed', code='ENGINEERING_INPUT_INVALID', errors=compiled_inputs['errors'], unit_type=unit_type)
        session = session_manager.get_session(session_id)
        explicit_model = kinetic_model or model_type
        effective_model, zone_warnings = compute_effective_model_at_unit(session, inputs or [], explicit_model)
        is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
        if not is_compatible:
            suggestion = suggest_junction_for_conversion(session.primary_model_type, spec.compatible_models)
            error_msg = compat_error
            if suggestion:
                error_msg += f' {suggestion}'
            return public_error(error_msg, code='MODEL_TYPE_INCOMPATIBLE', unit_type=unit_type, model_type=effective_model)
        auto_insert_warnings = []
        if any(('Multiple input models detected' in w for w in zone_warnings)):
            target_model = normalize_model_name(session.primary_model_type)
            inputs, auto_insert_warnings = _rewrite_inputs_with_junctions(session, inputs or [], target_model)
            effective_model, zone_warnings = compute_effective_model_at_unit(session, inputs, explicit_model)
        input_warnings = []
        for inp in inputs:
            if is_tuple_notation(inp):
                port_strs = parse_tuple_notation(inp)
            else:
                port_strs = [inp]
            for port_str in port_strs:
                try:
                    ref = parse_port_notation(port_str)
                    if ref.port_type == 'stream':
                        if ref.unit_id not in session.streams and ref.unit_id not in session.units and (not find_named_output_producer(session.units, ref.unit_id)):
                            input_warnings.append(f"Input '{port_str}' not found in session. Will be treated as deferred connection (recycle).")
                    elif ref.port_type == 'output':
                        if ref.unit_id not in session.units:
                            input_warnings.append(f"Input '{port_str}' references unit '{ref.unit_id}' which doesn't exist yet. Will be treated as deferred connection.")
                    elif ref.port_type == 'input':
                        return public_error(f"Invalid input '{port_str}': input port notation (e.g., '1-M1') cannot be used as an input source. Use output notation (e.g., 'M1-0') or stream ID instead.", code='INVALID_PORT_NOTATION', input=port_str)
                except ValueError as parse_error:
                    return public_error(f"Invalid input notation '{port_str}': {parse_error}", code='INVALID_PORT_NOTATION', input=port_str)
        config = UnitConfig(unit_id=unit_id, unit_type=unit_type, params=compiled_inputs['params'], inputs=inputs, outputs=outputs, model_type=effective_model, engineering_profile=engineering_profile, engineering_inputs=engineering_inputs or {}, aux_config=compiled_inputs['aux_config'], source_spec=compiled_inputs['source_spec'])
        result = session_manager.add_unit(session_id, config)
        all_warnings = compiled_inputs['warnings'] + input_warnings + zone_warnings + auto_insert_warnings
        if all_warnings:
            result['warnings'] = all_warnings
        result['n_ins'] = spec.n_ins
        result['n_outs'] = spec.n_outs
        result['effective_model'] = effective_model
        result['kinetic_model'] = effective_model
        result['engineering_profile'] = engineering_profile
        result['engineering_requirements'] = serialize_unit_spec(spec).get('engineering_inputs', {})
        result['applied_engineering_defaults'] = compiled_inputs['applied_defaults']
        if stream_topology is not None:
            result['stream_topology'] = stream_topology
        return result
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, unit_type=unit_type, unit_id=unit_id)
    except Exception as e:
        logger.error(f'create_unit failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_CREATE_FAILED', session_id=session_id, unit_type=unit_type, unit_id=unit_id)

@mcp.tool()
async def connect_units(session_id: str, connections: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Add deferred connections between units (for recycles).

    Use this after creating units to wire recycle streams that
    couldn't be specified during unit creation.

    Args:
        session_id: Session identifier
        connections: List of connection dicts in canonical form:
            - {"from": "SP-0", "to": "1-A1"}  # "to" uses input notation

    Returns:
        Dict with connections added and validation status

    Example:
        >>> await connect_units(
        ...     session_id="abc123",
        ...     connections=[{"from": "SP-0", "to": "1-A1"}],
        ... )
    """
    from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation
    try:
        results = []
        for conn in connections:
            if not isinstance(conn, dict):
                results.append(public_error('Invalid connection format', code='INVALID_CONNECTION', connection=conn))
                continue
            if 'from' not in conn or 'to' not in conn:
                results.append(public_error("Connections must include both 'from' and 'to' fields in canonical form", code='INVALID_CONNECTION', connection=conn))
                continue
            from_port = conn['from']
            to_port = conn['to']
            try:
                from_ref = parse_port_notation(from_port)
            except ValueError as exc:
                results.append(public_error(f"Invalid 'from' port '{from_port}': {exc}", code='INVALID_PORT_NOTATION', from_port=from_port))
                continue
            if from_ref.port_type == 'direct':
                results.append(public_error("Direct connection notation is no longer supported. Use canonical {'from': 'A1-0', 'to': '1-B1'} form instead.", code='INVALID_PORT_NOTATION', connection=conn))
                continue
            try:
                to_ref = parse_port_notation(to_port)
            except ValueError as exc:
                results.append(public_error(f"Invalid 'to' port '{to_port}': {exc}", code='INVALID_PORT_NOTATION', to_port=to_port))
                continue
            if to_ref.port_type == 'direct':
                results.append(public_error("Direct destination notation is no longer supported. Use canonical input notation like '1-B1' for the 'to' field.", code='INVALID_PORT_NOTATION', connection=conn))
                continue
            if to_ref.port_type == 'output':
                results.append(public_error(f"Destination ports must use input notation like '1-B1' or a unit ID, not output notation '{to_port}'.", code='INVALID_PORT_NOTATION', to_port=to_port))
                continue
            config = ConnectionConfig(from_port=from_port, to_port=to_port, stream_id=conn.get('stream_id'))
            result = session_manager.add_connection(session_id, config)
            results.append(result)
        successful_count = len([r for r in results if 'code' not in r])
        rejected_count = len(results) - successful_count
        return {'session_id': session_id, 'connections_added': successful_count, 'results': results, 'status': 'connected' if rejected_count == 0 else 'partial', 'message': f'Added {successful_count} connection(s).' if rejected_count == 0 else f'Added {successful_count} connection(s); {rejected_count} rejected.'}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'connect_units failed: {e}', exc_info=True)
        return public_error(e, code='CONNECTION_CREATE_FAILED', session_id=session_id)

@mcp.tool()
async def generate_session_inoculum(session_id: str, influent_basis: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """
    Persist startup biomass and per-unit initial concentrations for a session.

    Call this after creating and wiring units, then run build_system() so the
    compiled QSDsan units receive the saved initialization state.
    """
    try:
        return apply_session_startup(session_id, inoculum_policy={'influent_basis': influent_basis or {}}, session_manager=session_manager)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'generate_session_inoculum failed: {e}', exc_info=True)
        return public_error(e, code='INOCULUM_CREATE_FAILED', session_id=session_id)

@mcp.tool()
async def build_from_simulation_seeds(simulation_seeds: List[Dict[str, Any]], bfd_edges: Optional[List[Dict[str, Any]]]=None, session_id: Optional[str]=None, model_type: Optional[str]=None, external_streams: Optional[Dict[str, Dict[str, Any]]]=None, project_ref: Optional[str]=None) -> Dict[str, Any]:
    """
    Create a flowsheet session directly from shared ``SimulationSeed`` payloads.

    The compiler validates the incoming seed schema, creates placeholder
    external feed streams for inlet ``stream_id`` values not produced by another
    seed, instantiates each unit with canonical output stream IDs, and persists
    semantic stream-role tags on the session for downstream SRT and convergence
    lookup.
    """
    try:
        plan = build_plan_from_simulation_seeds(simulation_seeds, bfd_edges=bfd_edges, model_type=model_type)
        session_result = await create_flowsheet_session(model_type=plan.primary_model_type, session_id=session_id)
        if 'code' in session_result:
            return session_result
        compiled_session_id = session_result['session_id']
        if project_ref:
            _save_session_provenance(compiled_session_id, {'project_ref': project_ref, 'origin_tool': 'qsdsan-engine-mcp.build_from_simulation_seeds'})
        warnings: List[str] = []
        for external in plan.external_streams:
            external_override = (external_streams or {}).get(external.source_name, (external_streams or {}).get(external.stream_id, {}))
            stream_model_type = external_override.get('model_type', external.model_type)
            stream_result = await create_stream(session_id=compiled_session_id, stream_id=external.stream_id, flow_m3_d=float(external_override.get('flow_m3_d', external.flow_m3_d)), concentrations=dict(external_override.get('concentrations', external.concentrations)), temperature_K=float(external_override.get('temperature_K', external.temperature_K)), concentration_units=str(external_override.get('concentration_units', 'mg/L')), stream_type='influent' if 'influent' in external.roles else 'intermediate', model_type=stream_model_type)
            if 'code' in stream_result:
                return public_error(f"Failed to create external stream '{external.source_name}'", code='STREAM_CREATE_FAILED', session_id=compiled_session_id, upstream_error=stream_result)
            session_manager.set_stream_roles(compiled_session_id, external.stream_id, external.roles)
        unit_manifest = []
        for unit in plan.units:
            unit_result = await create_unit(session_id=compiled_session_id, unit_type=unit.unit_type, unit_id=unit.unit_id, inputs=unit.inputs, outputs=unit.outputs, kinetic_model=unit.model_type, engineering_inputs=unit.engineering_inputs, stream_topology=unit.stream_topology)
            if 'code' in unit_result:
                return public_error(f"Failed to create unit '{unit.seed_id}'", code='UNIT_CREATE_FAILED', session_id=compiled_session_id, created_units=unit_manifest, upstream_error=unit_result)
            if unit_result.get('warnings'):
                warnings.extend(unit_result['warnings'])
            for output_id in unit.outputs:
                roles = plan.stream_roles.get(output_id, [])
                if roles:
                    session_manager.set_stream_roles(compiled_session_id, output_id, roles)
            unit_manifest.append({'seed_id': unit.seed_id, 'unit_id': unit.unit_id, 'unit_type': unit.unit_type, 'kinetic_model': unit.model_type, 'inputs': unit.inputs, 'outputs': unit.outputs})
        connection_manifest = []
        for connection in plan.connections:
            session_manager.add_connection(compiled_session_id, connection)
            connection_manifest.append({'from': connection.from_port, 'to': connection.to_port, 'stream_id': connection.stream_id})
        session_summary = session_manager.get_session_summary(compiled_session_id)
        role_map = session_summary.get('stream_roles', {})
        return {'session_id': compiled_session_id, 'status': 'created', 'model_type': plan.primary_model_type, 'created_units': unit_manifest, 'external_streams': [{'source_name': external.source_name, 'stream_id': external.stream_id, 'roles': external.roles, 'model_type': external.model_type} for external in plan.external_streams], 'connections': connection_manifest, 'recycle_stream_ids': plan.recycle_stream_ids, 'role_map': role_map, 'roles_by_name': invert_stream_roles(role_map), 'warnings': warnings, 'message': f'Session created from {len(unit_manifest)} simulation seeds. Run generate_session_inoculum() next, then build_system().'}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'build_from_simulation_seeds failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_BUILD_FROM_SEEDS_FAILED', session_id=session_id)

@mcp.tool()
async def build_system(session_id: str, system_id: str, unit_order: Optional[List[str]]=None, recycle_streams: Optional[List[str]]=None) -> Dict[str, Any]:
    """
    Compile flowsheet session into a QSDsan System.

    This validates the flowsheet and compiles it into QSDsan objects.
    Use simulate_built_system() after this to run the simulation.

    Args:
        session_id: Session identifier
        system_id: Name for the compiled system
        unit_order: Optional list of unit IDs for execution order.
                    If not provided, topological sort is used.
        recycle_streams: Optional list of recycle stream IDs

    Returns:
        Dict with system_id, validation status, unit execution order, and build info

    Example:
        >>> await build_system(
        ...     session_id="abc123",
        ...     system_id="custom_mle",
        ...     recycle_streams=["RAS"],
        ... )
    """
    try:
        from qsdsan_engine_mcp.utils.topo_sort import detect_recycle_streams, validate_flowsheet_connectivity
        from qsdsan_engine_mcp.utils.flowsheet_builder import compile_system
        from qsdsan_engine_mcp.utils.stream_roles import derive_session_stream_roles
        from dataclasses import asdict
        session = session_manager.get_session(session_id)
        manual_order = unit_order
        recycles = set(recycle_streams) if recycle_streams else set()
        auto_detected_recycles = False
        if not recycles:
            role_map = derive_session_stream_roles(session)
            recycles = {stream_id for stream_id, roles in role_map.items() if {'ras', 'internal_recycle', 'sludge_return'} & set(roles)}
            auto_detected_recycles = bool(recycles)
        if not recycles:
            recycles = detect_recycle_streams(session.units, session.connections)
            auto_detected_recycles = bool(recycles)
        build_config = {'system_id': system_id, 'unit_order': manual_order, 'recycle_streams': list(recycles), 'status': 'building'}
        session_manager.save_build_metadata(session_id, build_config=build_config)
        errors, warnings = validate_flowsheet_connectivity(session.units, session.streams, session.connections)
        if errors:
            session_manager.update_session_status(session_id, 'failed')
            session_manager.save_build_metadata(session_id, build_config={**build_config, 'status': 'failed'}, build_error={'status': 'failed', 'stage': 'connectivity_validation', 'system_id': system_id, 'errors': errors, 'warnings': warnings})
            return public_error('Flowsheet validation failed', code='FLOWSHEET_VALIDATION_FAILED', session_id=session_id, errors=errors, warnings=warnings)
        try:
            system, build_info = await _locked_to_thread(compile_system, session=session, system_id=system_id, unit_order=manual_order, recycle_stream_ids=recycles)
        except Exception as compile_error:
            session_manager.update_session_status(session_id, 'failed')
            session_manager.save_build_metadata(session_id, build_config={**build_config, 'status': 'failed'}, build_error={'status': 'failed', 'stage': 'compile_system', 'system_id': system_id, 'error': str(compile_error), 'warnings': warnings})
            return public_error(compile_error, code='SYSTEM_COMPILE_FAILED', session_id=session_id, warnings=warnings)
        session_manager.update_session_status(session_id, 'compiled')
        build_config = {'system_id': system_id, 'unit_order': build_info.unit_order, 'recycle_streams': list(recycles), 'status': 'compiled'}
        build_result = {'system_id': build_info.system_id, 'unit_order': build_info.unit_order, 'recycle_streams': list(recycles), 'recycle_edges': build_info.recycle_edges, 'streams_created': build_info.streams_created, 'units_created': build_info.units_created, 'build_warnings': build_info.warnings}
        session_manager.save_build_metadata(session_id, build_config=build_config, build_result=build_result)
        return {'session_id': session_id, 'system_id': system_id, 'status': 'compiled', 'unit_order': build_info.unit_order, 'recycle_streams': list(recycles), 'recycle_edges': build_info.recycle_edges, 'streams_created': build_info.streams_created, 'units_created': build_info.units_created, 'warnings': warnings + build_info.warnings, 'errors': [], 'auto_detected_recycles': auto_detected_recycles, 'message': f"System compiled successfully. Use simulate_built_system(session_id='{session_id}', ...) to simulate."}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, system_id=system_id)
    except Exception as e:
        logger.error(f'build_system failed: {e}', exc_info=True)
        return public_error(e, code='SYSTEM_BUILD_FAILED', session_id=session_id, system_id=system_id)

@mcp.tool()
async def list_units(model_type: Optional[str]=None, category: Optional[str]=None) -> Dict[str, Any]:
    """
    List available SanUnit types with their parameters.

    Args:
        model_type: Filter by compatible process model (e.g., "ASM2d", "mADM1")
        category: Filter by unit category (e.g., "reactor", "separator", "junction")

    Returns:
        Dict with units list and categories

    Example:
        >>> result = await list_units(model_type="ASM2d")
        >>> print(result["units"])  # List of ASM2d-compatible units
    """
    try:
        units = get_all_units(model_type=model_type, category=category, public_only=True)
        categories = get_units_by_category(public_only=True)
        return {'units': units, 'categories': categories, 'total': len(units), 'filter': {'model_type': model_type, 'category': category}}
    except Exception as e:
        logger.error(f'list_units failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_LIST_FAILED', model_type=model_type, category=category)

@mcp.tool()
async def get_unit_spec(unit_type: str) -> Dict[str, Any]:
    """Get the full public contract for one supported unit type."""
    try:
        spec = serialize_unit_spec(get_unit_spec_from_registry(unit_type))
        spec['public_support_error'] = get_public_support_error(unit_type)
        return spec
    except Exception as e:
        logger.error(f'get_unit_spec failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_TYPE_UNKNOWN', unit_type=unit_type)

@mcp.tool()
async def list_downstream_handoff_profiles() -> Dict[str, Any]:
    """List curated downstream WaterTAP handoff profiles for QSDsan states."""
    try:
        profiles = get_handoff_profiles()
        return {'profiles': profiles, 'total': len(profiles)}
    except Exception as e:
        logger.error(f'list_downstream_handoff_profiles failed: {e}', exc_info=True)
        return public_error(e, code='HANDOFF_PROFILE_LIST_FAILED')

def _load_handoff_state_from_job(job_id: str, stream_id: Optional[str]) -> StreamState:
    return load_handoff_state_from_job_dir(_job_dir(job_id), stream_id)

@mcp.tool()
async def prepare_downstream_handoff(target_profile: str, state: Optional[Dict[str, Any]]=None, job_id: Optional[str]=None, stream_id: Optional[str]=None) -> Dict[str, Any]:
    """Prepare a QSDsan state or stream artifact for downstream WaterTAP use."""
    try:
        if state is not None:
            stream_state = stream_state_from_payload(state)
        elif job_id:
            stream_state = _load_handoff_state_from_job(job_id, stream_id)
        else:
            return public_error('Provide either `state` or `job_id` for downstream handoff preparation.', code='INVALID_INPUT')
        payload = prepare_handoff_payload(stream_state, target_profile)
        payload['source'] = {'job_id': job_id, 'stream_id': stream_id, 'model_type': model_type_value(stream_state.model_type)}
        return payload
    except HandoffValidationError as e:
        logger.error(f'prepare_downstream_handoff failed: {e}', exc_info=True)
        return public_error(e, code='HANDOFF_VALIDATION_FAILED', missing_analytes=e.missing_analytes, type_mismatches=e.type_mismatches, unit_mismatches=e.unit_mismatches, value_violations=e.value_violations)
    except Exception as e:
        logger.error(f'prepare_downstream_handoff failed: {e}', exc_info=True)
        return public_error(e, code='HANDOFF_PREPARE_FAILED', job_id=job_id, stream_id=stream_id)
_ANAEROBIC_REACTOR_UNIT_TYPES = {'AnaerobicCSTRmADM1', 'AnaerobicCSTR'}

def _resolve_gas_seed_targets(session: Any, gas_phase: Dict[str, Any], warnings: List[str]) -> List[str]:
    """Pick the QSDsan digester unit(s) to receive a WaterTAP mADM1 gas seed.

    Prefers an explicit unit hint (already unit-mapped to a session unit by the
    bridge); otherwise falls back to the sole anaerobic reactor in the session.
    With multiple anaerobic reactors and no hint the seed is skipped (and warned)
    rather than smeared across digesters.
    """
    hint = gas_phase.get('qsdsan_unit_hint')
    if hint and hint in session.units:
        return [hint]
    anaerobic_units = [unit_id for unit_id, cfg in session.units.items() if getattr(cfg, 'unit_type', None) in _ANAEROBIC_REACTOR_UNIT_TYPES]
    if len(anaerobic_units) == 1:
        return anaerobic_units
    if not anaerobic_units:
        warnings.append('WaterTAP payload carries a gas phase but the session has no anaerobic (AnaerobicCSTRmADM1 / AnaerobicCSTR) reactor to seed; gas seed skipped.')
        return []
    warnings.append(f'WaterTAP payload carries a gas phase and the session has multiple anaerobic reactors ({sorted(anaerobic_units)}); provide a unit_map hint to target one. Gas seed skipped.')
    return []

@mcp.tool()
async def seed_from_watertap_solution(session_id: str, watertap_payload: Optional[Dict[str, Any]]=None, watertap_payload_path: Optional[str]=None, stream_map: Optional[Dict[str, str]]=None, unit_map: Optional[Dict[str, str]]=None, model_type: Optional[str]=None, apply_to: str='both', strict: bool=True, allow_near_feasible: bool=False) -> Dict[str, Any]:
    """Seed a QSDsan dynamic session from a solved WaterTAP steady-state payload.

    The WaterTAP solution is used only as an initializer: stream states update
    matching QSDsan streams, and unit states merge into startup_metadata
    per_unit_init_conc. Rebuild the QSDsan system after applying seeds.
    """
    try:
        session = session_manager.get_session(session_id)
        if watertap_payload is None:
            if not watertap_payload_path:
                return public_error('Provide watertap_payload or watertap_payload_path', code='INVALID_INPUT', session_id=session_id)
            payload_path = Path(normalize_path_for_wsl(watertap_payload_path))
            if not payload_path.exists():
                return public_error('WaterTAP payload path not found', code='MISSING_FILE', session_id=session_id, path=watertap_payload_path)
            watertap_payload = json.loads(payload_path.read_text())
        if not isinstance(watertap_payload, dict):
            return public_error('WaterTAP payload must be a dictionary', code='INVALID_INPUT', session_id=session_id)
        if seed_has_near_feasible_flag(watertap_payload) and (not allow_near_feasible):
            return public_error('WaterTAP payload is marked near_feasible_accepted. Pass allow_near_feasible=True only when this is acceptable for a dynamic initializer.', code='NEAR_FEASIBLE_REQUIRES_ACK', session_id=session_id)
        target_model = model_type or session.primary_model_type
        seed = build_watertap_seed(watertap_payload, model_type=target_model, stream_map=stream_map, unit_map=unit_map)
        apply_scope = apply_to.lower().strip()
        if apply_scope not in {'both', 'streams', 'units'}:
            return public_error('apply_to must be one of: both, streams, units', code='INVALID_INPUT', session_id=session_id, apply_to=apply_to)
        stream_seeds = seed.get('stream_seeds') or {}
        unit_seeds = seed.get('per_unit_init_conc') or {}
        missing_streams = [stream_id for stream_id in stream_seeds if stream_id not in session.streams] if apply_scope in {'both', 'streams'} else []
        missing_units = [unit_id for unit_id in unit_seeds if unit_id not in session.units] if apply_scope in {'both', 'units'} else []
        if strict and (missing_streams or missing_units):
            return public_error('WaterTAP seed does not match the QSDsan session', code='SEED_SESSION_MISMATCH', session_id=session_id, missing_streams=missing_streams, missing_units=missing_units, available_streams=sorted(session.streams), available_units=sorted(session.units), hint='Provide stream_map/unit_map or set strict=False to skip unmatched seeds.')
        applied_streams: list[str] = []
        if apply_scope in {'both', 'streams'}:
            for stream_id, state in stream_seeds.items():
                if stream_id not in session.streams:
                    continue
                session_manager.update_stream(session_id, stream_id, {'flow_m3_d': state['flow_m3_d'], 'temperature_K': state['temperature_K'], 'concentrations': state['concentrations'], 'model_type': state['model_type']})
                applied_streams.append(stream_id)
        applied_units: list[str] = []
        applied_gas_units: list[str] = []
        gas_seed_warnings: list[str] = []
        if apply_scope in {'both', 'units'}:
            startup_metadata = session_manager.get_startup_state(session_id)
            per_unit_init_conc = dict(startup_metadata.get('per_unit_init_conc') or {})
            liquid_to_mg_l = 1000.0 if str(target_model).lower().endswith('adm1') else 1.0
            for unit_id, concentrations in unit_seeds.items():
                if unit_id not in session.units:
                    continue
                existing = dict(per_unit_init_conc.get(unit_id) or {})
                existing.update({cid: float(v) * liquid_to_mg_l for cid, v in concentrations.items()})
                per_unit_init_conc[unit_id] = existing
                applied_units.append(unit_id)
            gas_phase = seed.get('gas_phase')
            per_unit_gas_init = dict(startup_metadata.get('per_unit_gas_init_kmol_m3') or {})
            if gas_phase and gas_phase.get('concentrations_kmol_m3'):
                gas_targets = _resolve_gas_seed_targets(session, gas_phase, gas_seed_warnings)
                gas_conc = gas_phase['concentrations_kmol_m3']
                for unit_id in gas_targets:
                    existing_gas = dict(per_unit_gas_init.get(unit_id) or {})
                    existing_gas.update(gas_conc)
                    per_unit_gas_init[unit_id] = existing_gas
                    applied_gas_units.append(unit_id)
            startup_metadata.update({'engine_family': 'adm' if str(target_model).lower().endswith('adm1') else 'asm', 'per_unit_init_conc': per_unit_init_conc, 'per_unit_gas_init_kmol_m3': per_unit_gas_init, 'watertap_seed': seed.get('provenance') or {}, 'seed_source': 'watertap-engine-mcp', 'seed_applied_at': datetime.now(timezone.utc).isoformat()})
            session_manager.set_startup_state(session_id, startup_metadata)
        _save_session_provenance(session_id, {'watertap_seed_source_run_id': (seed.get('provenance') or {}).get('source_run_id'), 'watertap_seed_applied_at': datetime.now(timezone.utc).isoformat()})
        return {'session_id': session_id, 'status': 'seeded', 'model_type': target_model, 'applied_streams': applied_streams, 'applied_units': applied_units, 'applied_gas_units': applied_gas_units, 'skipped_streams': missing_streams, 'skipped_units': missing_units, 'gas_seed_warnings': gas_seed_warnings, 'seed_report': seed, 'next_step': 'Run build_system(session_id=...) before simulate_built_system; seeding invalidates any prior compiled system.'}
    except Exception as e:
        logger.error(f'seed_from_watertap_solution failed: {e}', exc_info=True)
        return public_error(e, code='WATERTAP_SEED_FAILED', session_id=session_id, path=watertap_payload_path)

@mcp.tool()
async def simulate_built_system(session_id: Optional[str]=None, system_id: Optional[str]=None, duration_days: float=1.0, timestep_hours: float=1.0, project_ref: Optional[str]=None, plant_slug: Optional[str]=None, method: str='RK23', t_eval: Optional[List[float]]=None, track: Optional[List[str]]=None, effluent_stream_ids: Optional[List[str]]=None, biogas_stream_ids: Optional[List[str]]=None, report: bool=False, diagram: bool=True, include_components: bool=False, export_state_to: Optional[str]=None, mcp_outputs_dir: Optional[str]=None, run_to_convergence: bool=False, convergence_atol: float=0.1, convergence_rtol: float=0.001, check_interval_days: float=2.0, max_duration_days: Optional[float]=None, dynamic_controls: Optional[Dict[str, Any]]=None, disturbance_schedule: Optional[Dict[str, Any]]=None, target_srt_days: Optional[float]=None, srt_tolerance: float=0.1, max_srt_iterations: int=10, resume_from_checkpoint: Optional[Dict[str, Any]]=None, resume_from_session_checkpoint: bool=False, reconciliation_measurements: Optional[Dict[str, Any]]=None, reconciliation_options: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """Simulate a compiled flowsheet with comprehensive reporting."""
    try:
        import uuid
        if session_id and system_id:
            return public_error('Provide either session_id or system_id, not both', code='INVALID_INPUT', session_id=session_id, system_id=system_id)
        if not session_id and (not system_id):
            return public_error('Must provide either session_id or system_id', code='INVALID_INPUT')
        if system_id:
            found_session_id = session_manager.find_session_by_system_id(system_id)
            if not found_session_id:
                return public_error(f"No compiled session found with system_id '{system_id}'. Run build_system() first to create a system.", code='SESSION_NOT_FOUND', system_id=system_id)
            session_id = found_session_id
        session = session_manager.get_session(session_id)
        if session.status != 'compiled':
            return public_error(f"Session status is '{session.status}', must be 'compiled'. Run build_system(session_id='{session_id}', ...) first.", code='SESSION_NOT_COMPILED', session_id=session_id, status=session.status)
        if target_srt_days is not None:
            has_seed, biomass_ids, _ = session_has_biomass_seed(session)
            if not has_seed:
                return public_error(format_missing_biomass_seed_error(session.primary_model_type, biomass_ids), code='MISSING_BIOMASS_SEED', session_id=session_id, biomass_ids=biomass_ids)
        job_id = str(uuid.uuid4())[:8]
        job_dir = _job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        session_dir = session_manager._get_session_dir(session_id)
        import shutil
        shutil.copy(session_dir / 'session.json', job_dir / 'session.json')
        if (session_dir / 'build_config.json').exists():
            shutil.copy(session_dir / 'build_config.json', job_dir / 'build_config.json')
        resume_checkpoint_path = None
        resume_requested = bool(resume_from_checkpoint) or resume_from_session_checkpoint
        if (reconciliation_measurements or reconciliation_options) and (not resume_requested):
            return public_error('reconciliation_measurements/reconciliation_options require a resume (set resume_from_checkpoint or resume_from_session_checkpoint=True); reconciliation only applies to a restored checkpoint', code='INVALID_INPUT', session_id=session_id)
        if resume_requested:
            if target_srt_days is not None or run_to_convergence:
                return public_error('resume_from_checkpoint is incompatible with target_srt_days or run_to_convergence; resume runs a fixed-duration transient', code='INVALID_INPUT', session_id=session_id)
            checkpoint_payload = resume_from_checkpoint
            if checkpoint_payload is None and resume_from_session_checkpoint:
                checkpoint_payload = session_manager.get_dynamic_checkpoint(session_id)
            if not checkpoint_payload:
                return public_error('No checkpoint available to resume from: pass resume_from_checkpoint or persist one to the session (set_dynamic_checkpoint).', code='MISSING_CHECKPOINT', session_id=session_id)
            resume_checkpoint_path = job_dir / 'resume_checkpoint.json'
            with open(resume_checkpoint_path, 'w') as f:
                json.dump(checkpoint_payload, f)
        session_project_ref = project_ref
        if not session_project_ref:
            session_project_ref = _load_session_provenance(session_id).get('project_ref')
        if session_project_ref or mcp_outputs_dir:
            provenance_update = {'origin_tool': 'qsdsan-engine-mcp.simulate_built_system'}
            if session_project_ref:
                provenance_update['project_ref'] = session_project_ref
            if mcp_outputs_dir:
                provenance_update['mcp_outputs_dir'] = mcp_outputs_dir
            _save_session_provenance(session_id, provenance_update)
        sim_config = {'session_id': session_id, 'project_ref': session_project_ref, 'plant_slug': plant_slug, 'duration_days': duration_days, 'timestep_hours': timestep_hours, 'method': method, 't_eval': t_eval, 'track': track, 'effluent_stream_ids': effluent_stream_ids, 'biogas_stream_ids': biogas_stream_ids, 'report': report, 'diagram': diagram, 'include_components': include_components, 'export_state_to': export_state_to, 'mcp_outputs_dir': mcp_outputs_dir, 'run_to_convergence': run_to_convergence, 'convergence_atol': convergence_atol, 'convergence_rtol': convergence_rtol, 'check_interval_days': check_interval_days, 'max_duration_days': max_duration_days, 'dynamic_controls': dynamic_controls, 'disturbance_schedule': disturbance_schedule, 'target_srt_days': target_srt_days, 'srt_tolerance': srt_tolerance, 'max_srt_iterations': max_srt_iterations, 'resume_from_checkpoint': resume_checkpoint_path is not None, 'reconciliation_measurements': reconciliation_measurements, 'reconciliation_options': reconciliation_options}
        with open(job_dir / 'sim_config.json', 'w') as f:
            json.dump(sim_config, f, indent=2)
        python_exe = get_python_executable()
        cmd = [python_exe, '-m', 'qsdsan_engine_mcp.job_cli', 'flowsheet', 'simulate', '--session', session_id, '--output-dir', str(job_dir), '--duration-days', str(duration_days), '--timestep-hours', str(timestep_hours), '--method', method]
        if t_eval:
            cmd.extend(['--t-eval', json.dumps(t_eval)])
        if track:
            cmd.extend(['--track', json.dumps(track)])
        if effluent_stream_ids:
            cmd.extend(['--effluent-streams', json.dumps(effluent_stream_ids)])
        if biogas_stream_ids:
            cmd.extend(['--biogas-streams', json.dumps(biogas_stream_ids)])
        if report:
            cmd.append('--report')
        if diagram:
            cmd.append('--diagram')
        if include_components:
            cmd.append('--include-components')
        if export_state_to:
            cmd.extend(['--export-state-to', export_state_to])
        if run_to_convergence:
            cmd.append('--run-to-convergence')
            cmd.extend(['--convergence-atol', str(convergence_atol)])
            cmd.extend(['--convergence-rtol', str(convergence_rtol)])
            cmd.extend(['--check-interval', str(check_interval_days)])
            if max_duration_days is not None:
                cmd.extend(['--max-duration', str(max_duration_days)])
        if dynamic_controls:
            cmd.extend(['--dynamic-controls', json.dumps(dynamic_controls)])
        if disturbance_schedule:
            cmd.extend(['--disturbance-schedule', json.dumps(disturbance_schedule)])
        if target_srt_days is not None:
            cmd.extend(['--target-srt', str(target_srt_days)])
        if srt_tolerance != 0.1:
            cmd.extend(['--srt-tolerance', str(srt_tolerance)])
        if max_srt_iterations != 10:
            cmd.extend(['--max-srt-iterations', str(max_srt_iterations)])
        if resume_checkpoint_path is not None:
            cmd.extend(['--resume-checkpoint-file', str(resume_checkpoint_path)])
        if reconciliation_measurements:
            cmd.extend(['--reconciliation-measurements', json.dumps(reconciliation_measurements)])
        if reconciliation_options:
            cmd.extend(['--reconciliation-options', json.dumps(reconciliation_options)])
        cwd = str(Path(__file__).parent.absolute())
        job = await job_manager.execute(cmd=cmd, cwd=cwd, job_id=job_id, on_complete=None, env={'QSDSAN_ENGINE_SESSIONS_DIR': str(session_manager.sessions_dir)})
        session_manager.record_last_job(session_id, job['id'])
        return {'job_id': job['id'], 'status': job['status'], 'session_id': session_id, 'duration_days': duration_days, 'message': f"Simulation started. Use get_job_status('{job['id']}') to monitor progress.", 'credibility': {'model_status': 'preliminary', 'decision_grade': 'budgetary', 'validation_basis': 'literature', 'notes': 'QSDsan/BioSTEAM dynamic process model; proposal-stage outputs are preliminary unless calibrated against plant data.'}}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, system_id=system_id)
    except Exception as e:
        logger.error(f'simulate_built_system failed: {e}', exc_info=True)
        return public_error(e, code='SIMULATION_START_FAILED', session_id=session_id, system_id=system_id)

@mcp.tool()
async def reconcile_state(session_id: str, measurements: Dict[str, Any], observability_map: Optional[Dict[str, Any]]=None, checkpoint: Optional[Dict[str, Any]]=None, reconciliation_options: Optional[Dict[str, Any]]=None, effluent_stream_ids: Optional[List[str]]=None, dynamic_controls: Optional[Dict[str, Any]]=None, disturbance_schedule: Optional[Dict[str, Any]]=None, persist_checkpoint: bool=False, dry_run: bool=False) -> Dict[str, Any]:
    """Structured engineering calculation interface."""
    try:
        session = session_manager.get_session(session_id)
        if session.status != 'compiled':
            return public_error(f"Session status is '{session.status}', must be 'compiled'. Run build_system(session_id='{session_id}', ...) first.", code='SESSION_NOT_COMPILED', session_id=session_id, status=session.status)
        if not isinstance(measurements, dict) or not measurements:
            return public_error('measurements must be a non-empty object', code='INVALID_INPUT', session_id=session_id)
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = _job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        cmd = [get_python_executable(), '-m', 'qsdsan_engine_mcp.job_cli', 'flowsheet', 'reconcile', '--session', session_id, '--measurements', json.dumps(measurements), '--output-file', str(job_dir / 'reconciliation.json'), '--json-out']
        if observability_map is not None:
            omap_path = job_dir / 'observability_map.json'
            with open(omap_path, 'w') as f:
                json.dump(observability_map, f)
            cmd.extend(['--observability-map-file', str(omap_path)])
        if checkpoint is not None:
            ckpt_path = job_dir / 'reconcile_checkpoint.json'
            with open(ckpt_path, 'w') as f:
                json.dump(checkpoint, f)
            cmd.extend(['--checkpoint-file', str(ckpt_path)])
        if reconciliation_options:
            cmd.extend(['--options', json.dumps(reconciliation_options)])
        if effluent_stream_ids:
            cmd.extend(['--effluent-streams', json.dumps(effluent_stream_ids)])
        if dynamic_controls:
            cmd.extend(['--dynamic-controls', json.dumps(dynamic_controls)])
        if disturbance_schedule:
            cmd.extend(['--disturbance-schedule', json.dumps(disturbance_schedule)])
        if dry_run:
            cmd.append('--dry-run')
        if persist_checkpoint:
            cmd.append('--persist-checkpoint')
        cwd = str(Path(__file__).parent.absolute())
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, 'QSDSAN_ENGINE_SESSIONS_DIR': str(session_manager.sessions_dir)})
        stdout, stderr = await proc.communicate()
        result_path = job_dir / 'reconciliation.json'
        if result_path.exists():
            with open(result_path) as f:
                payload = json.load(f)
        else:
            try:
                payload = json.loads((stdout or b'').decode('utf-8').strip().splitlines()[-1])
            except Exception:
                payload = None
        if proc.returncode != 0 or payload is None:
            return public_error(f'reconcile_state failed: {(stderr or b'').decode('utf-8')[:2000]}', code='RECONCILIATION_FAILED', session_id=session_id, returncode=proc.returncode)
        quality = payload.get('quality') or {}
        conservation = payload.get('conservation_report') or {}
        payload.setdefault('credibility', {'model_status': 'preliminary', 'decision_grade': 'budgetary', 'validation_basis': 'plant_data', 'notes': f'{payload.get('method', 'bounded_nudge')} projection, NOT state estimation — no covariance or innovation statistics. This run: {quality.get('applied', 0)} correction(s) applied, {quality.get('rejected', 0)} rejected, overall confidence {quality.get('overall_confidence', 0.0):.2f} (grade {quality.get('grade', 'n/a')}); conservation report {conservation.get('status', 'unavailable')}. Declared measurement confidence scales the nudge and sigma is a dead-band. Not a calibrated state.'})
        return payload
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'reconcile_state failed: {e}', exc_info=True)
        return public_error(e, code='RECONCILIATION_FAILED', session_id=session_id)

@mcp.tool()
async def get_flowsheet_session(session_id: str) -> Dict[str, Any]:
    """
    Get details of a flowsheet session.

    Args:
        session_id: Session identifier

    Returns:
        Dict with session summary including streams, units, and connections
    """
    try:
        session = session_manager.get_session(session_id)
        summary = session_manager.get_session_summary(session_id)
        summary['model_type'] = summary.get('primary_model_type', session.primary_model_type)
        summary['session'] = session._to_dict()
        return summary
    except ValueError as e:
        return public_error(e, code='SESSION_NOT_FOUND', session_id=session_id)
    except Exception as e:
        logger.error(f'get_flowsheet_session failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_READ_FAILED', session_id=session_id)

@mcp.tool()
async def list_flowsheet_sessions(status_filter: Optional[str]=None) -> Dict[str, Any]:
    """
    List all flowsheet sessions.

    Args:
        status_filter: Optional filter by status ("building", "compiled", "failed")

    Returns:
        Dict with sessions list
    """
    try:
        sessions = session_manager.list_sessions(status_filter=status_filter)
        return {'sessions': sessions, 'total': len(sessions), 'filter': status_filter}
    except Exception as e:
        logger.error(f'list_flowsheet_sessions failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_LIST_FAILED', status_filter=status_filter)

@mcp.tool()
async def update_stream(session_id: str, stream_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update a stream in the flowsheet session (patch-style).

    Modifying a stream resets session status to 'building' if it was 'compiled'.
    Requires re-running build_system() before simulating.

    Args:
        session_id: Session identifier
        stream_id: Stream to update
        updates: Dict of fields to update. Valid fields:
                 flow_m3_d, temperature_K, concentrations (merged), stream_type, model_type

    Returns:
        Dict with stream_id, updated fields, and session status

    Example:
        >>> await update_stream(
        ...     session_id="abc123",
        ...     stream_id="influent",
        ...     updates={"flow_m3_d": 5000, "concentrations": {"S_F": 100}},
        ... )
    """
    try:
        valid_fields = {'flow_m3_d', 'temperature_K', 'concentrations', 'concentration_units', 'stream_type', 'model_type'}
        extra_fields = sorted(set(updates) - valid_fields)
        if extra_fields:
            return public_error(f'Invalid field(s) {extra_fields}. Valid fields: {sorted(valid_fields)}', code='INVALID_INPUT', session_id=session_id, stream_id=stream_id, extra_fields=extra_fields)
        session = session_manager.get_session(session_id)
        if stream_id not in session.streams:
            return public_error(f"Stream '{stream_id}' not found in session", code='STREAM_NOT_FOUND', session_id=session_id, stream_id=stream_id)
        config = session.streams[stream_id]
        proposed_concentrations = dict(config.concentrations)
        if 'concentrations' in updates:
            if not isinstance(updates['concentrations'], dict):
                return public_error('concentrations must be a dict', code='INVALID_INPUT', session_id=session_id, stream_id=stream_id)
            proposed_concentrations.update(updates['concentrations'])
        proposed_model = normalize_model_name(updates.get('model_type') or config.model_type or session.primary_model_type)
        StreamState.from_concentrations(stream_id=stream_id, model_type=proposed_model, flow_m3_d=updates.get('flow_m3_d', config.flow_m3_d), temperature_K=updates.get('temperature_K', config.temperature_K), concentration_units=updates.get('concentration_units', config.concentration_units), concentrations=proposed_concentrations)
        return session_manager.update_stream(session_id, stream_id, updates)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, stream_id=stream_id)
    except Exception as e:
        logger.error(f'update_stream failed: {e}', exc_info=True)
        return public_error(e, code='STREAM_UPDATE_FAILED', session_id=session_id, stream_id=stream_id)

@mcp.tool()
async def update_unit(session_id: str, unit_id: str, inputs: Optional[List[str]]=None, outputs: Optional[List[str]]=None, model_type: Optional[str]=None, engineering_profile: Optional[str]=None, engineering_inputs: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """
    Update a unit in the flowsheet session (patch-style).

    Modifying a unit resets session status to 'building' if it was 'compiled'.
    Requires re-running build_system() before simulating.

    Args:
        session_id: Session identifier
        unit_id: Unit to update
        inputs: Optional new input references
        outputs: Optional new output stream names
        model_type: Optional new effective model override
        engineering_profile: Optional new public engineering profile
        engineering_inputs: Optional engineering-input updates to merge

    Returns:
        Dict with unit_id, updated fields, and session status

    Example:
        >>> await update_unit(
        ...     session_id="abc123",
        ...     unit_id="A1",
        ...     engineering_inputs={"V_max": 1500},
        ... )
    """
    try:
        updates: Dict[str, Any] = {}
        if inputs is not None:
            updates['inputs'] = inputs
        if outputs is not None:
            updates['outputs'] = outputs
        if model_type is not None:
            updates['model_type'] = model_type
        if engineering_profile is not None:
            updates['engineering_profile'] = engineering_profile
        if engineering_inputs is not None:
            updates['engineering_inputs'] = engineering_inputs
        if not updates:
            return public_error('No updates provided', code='INVALID_INPUT', session_id=session_id, unit_id=unit_id)
        return session_manager.update_unit(session_id, unit_id, updates)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, unit_id=unit_id)
    except Exception as e:
        logger.error(f'update_unit failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_UPDATE_FAILED', session_id=session_id, unit_id=unit_id)

@mcp.tool()
async def set_unit_engineering_inputs(session_id: str, unit_id: str, engineering_inputs: Dict[str, Any], engineering_profile: Optional[str]=None) -> Dict[str, Any]:
    """
    Set process-engineering inputs for a unit.

    This is the default public sizing/operating-specification action for units
    after creation.
    """
    try:
        updates: Dict[str, Any] = {'engineering_inputs': engineering_inputs}
        if engineering_profile is not None:
            updates['engineering_profile'] = engineering_profile
        return session_manager.update_unit(session_id, unit_id, updates)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, unit_id=unit_id)
    except Exception as e:
        logger.error(f'set_unit_engineering_inputs failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_UPDATE_FAILED', session_id=session_id, unit_id=unit_id)

@mcp.tool()
async def delete_stream(session_id: str, stream_id: str, force: bool=False) -> Dict[str, Any]:
    """
    Delete a stream from the flowsheet session.

    By default, fails if any units reference this stream in their inputs.
    Use force=True to delete anyway (also removes from unit inputs).

    Args:
        session_id: Session identifier
        stream_id: Stream to delete
        force: If True, also remove from unit inputs that reference this stream

    Returns:
        Dict with deletion status and any units that had their inputs modified

    Example:
        >>> await delete_stream(session_id="abc123", stream_id="RAS", force=True)
    """
    try:
        return session_manager.delete_stream(session_id, stream_id, force)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, stream_id=stream_id)
    except Exception as e:
        logger.error(f'delete_stream failed: {e}', exc_info=True)
        return public_error(e, code='STREAM_DELETE_FAILED', session_id=session_id, stream_id=stream_id)

@mcp.tool()
async def delete_unit(session_id: str, unit_id: str) -> Dict[str, Any]:
    """
    Delete a unit from the flowsheet session.

    Also removes any connections that reference this unit.

    Args:
        session_id: Session identifier
        unit_id: Unit to delete

    Returns:
        Dict with deletion status and list of removed connections

    Example:
        >>> await delete_unit(session_id="abc123", unit_id="SP")
    """
    try:
        return session_manager.delete_unit(session_id, unit_id)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, unit_id=unit_id)
    except Exception as e:
        logger.error(f'delete_unit failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_DELETE_FAILED', session_id=session_id, unit_id=unit_id)

@mcp.tool()
async def delete_connection(session_id: str, from_port: str, to_port: Optional[str]=None) -> Dict[str, Any]:
    """
    Delete a specific connection from the flowsheet session.

    Args:
        session_id: Session identifier
        from_port: Source port of connection to delete (e.g., "SP-0")
        to_port: Destination port (e.g., "1-A1")

    Returns:
        Dict with deletion status

    Example:
        >>> await delete_connection(session_id="abc123", from_port="SP-0", to_port="1-A1")
    """
    try:
        return session_manager.delete_connection(session_id, from_port, to_port)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, from_port=from_port, to_port=to_port)
    except Exception as e:
        logger.error(f'delete_connection failed: {e}', exc_info=True)
        return public_error(e, code='CONNECTION_DELETE_FAILED', session_id=session_id, from_port=from_port, to_port=to_port)

@mcp.tool()
async def clone_session(source_session_id: str, new_session_id: Optional[str]=None) -> Dict[str, Any]:
    """
    Clone a flowsheet session for experimentation.

    Creates a copy of the session with a new ID, reset to 'building' status.
    Useful for testing variations without modifying the original.

    Args:
        source_session_id: Session to clone
        new_session_id: Optional custom ID for new session. Auto-generates if not provided.

    Returns:
        Dict with new session info

    Example:
        >>> result = await clone_session(source_session_id="abc123")
        >>> new_id = result["new_session_id"]
        >>> # Now modify the clone without affecting original
        >>> await set_unit_engineering_inputs(
        ...     session_id=new_id,
        ...     unit_id="A1",
        ...     engineering_inputs={"V_max": 2000},
        ... )
    """
    try:
        return session_manager.clone_session(source_session_id, new_session_id)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', source_session_id=source_session_id, new_session_id=new_session_id)
    except Exception as e:
        logger.error(f'clone_session failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_CLONE_FAILED', source_session_id=source_session_id, new_session_id=new_session_id)

@mcp.tool()
async def get_flowsheet_timeseries(job_id: str, stream_ids: Optional[List[str]]=None, components: Optional[List[str]]=None, downsample_factor: SamplingFactor=1, max_points: Optional[PointLimit]=500) -> Dict[str, Any]:
    """
    Get time-series data from a completed flowsheet simulation.

    Use this tool to retrieve dynamic component trajectories for streams
    that were tracked during simulation (via the 'track' parameter).

    Args:
        job_id: Job identifier from simulate_built_system
        stream_ids: Optional list to filter which streams to include
        components: Optional list to filter which component IDs to include
        downsample_factor: Positive integer stride (default 1). Coarsened by whole
                    multiples when necessary to enforce max_points.
        max_points: Maximum returned points, 1..2000 (default 500), including with
                    an explicit stride. Legacy None selects the hard 2000-point cap.
                    Full retained files remain available to authorized local helpers.

    Returns:
        Dict with time array and component trajectories per stream

        Component evidence distinguishes omitted recorded zeros from unavailable
        selections. Negative-value findings refer to the full recorded trajectory
        before downsampling, not just the returned points. Older artifacts without
        producer evidence are marked component_evidence_available=False; absence
        in those artifacts does not establish a zero or a nonnegative trajectory.

    Example:
        >>> result = await get_flowsheet_timeseries(
        ...     job_id="abc123",
        ...     stream_ids=["effluent"],
        ...     components=["S_NH4", "S_NO3"],
        ...     max_points=200,
        ... )
        >>> # result["time"] = [0, 0.5, 1.0, ...]
        >>> # result["streams"]["effluent"]["S_NH4"] = [17.0, 15.2, ...]
    """
    from qsdsan_engine_mcp.utils.path_utils import validate_safe_path, validate_id
    try:
        downsample_factor, max_points = validate_sampling_controls(downsample_factor, max_points)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', job_id=job_id)
    try:
        validate_id(job_id, 'job_id')
        job_dir = validate_safe_path(_job_root(), job_id, 'job_id')
        ts_path = job_dir / 'timeseries.json'
        if not ts_path.exists():
            results_path = job_dir / 'results.json'
            if results_path.exists():
                with open(results_path) as f:
                    results = json.load(f)
                if 'time_series' in results:
                    ts_data = results['time_series']
                else:
                    return public_error(f"No time-series data found for job {job_id}. Ensure you used 'track' parameter during simulation.", code='TIMESERIES_NOT_FOUND', job_id=job_id)
            else:
                return public_error(f'Job {job_id} not found or no time-series available', code='JOB_NOT_FOUND', job_id=job_id)
        else:
            with open(ts_path) as f:
                ts_data = json.load(f)
        if not ts_data.get('success'):
            return public_error(ts_data.get('message', 'Time-series extraction failed'), code='TIMESERIES_EXTRACT_FAILED', job_id=job_id)
        from qsdsan_engine_mcp.utils.timeseries_evidence import select_component_evidence
        component_evidence = select_component_evidence(ts_data, stream_ids, components)
        result_streams = ts_data.get('streams', {})
        if stream_ids:
            result_streams = {k: v for k, v in result_streams.items() if k in stream_ids}
        if components:
            filtered_streams = {}
            for stream_id, stream_data in result_streams.items():
                filtered = {k: v for k, v in stream_data.items() if k in components}
                if filtered:
                    filtered_streams[stream_id] = filtered
            result_streams = filtered_streams
        time_arr = ts_data.get('time', [])
        downsample_factor = bounded_stride(len(time_arr), downsample_factor, max_points)
        sample_stop = downsample_factor * max_points
        time_arr = time_arr[:sample_stop:downsample_factor]
        result_streams = {stream_id: {comp_id: values[:sample_stop:downsample_factor] for comp_id, values in stream_data.items()} for stream_id, stream_data in result_streams.items()}
        return {'job_id': job_id, 'time': time_arr, 'time_units': ts_data.get('time_units', 'days'), 'streams': result_streams, 'n_timepoints': len(time_arr), 'n_streams': len(result_streams), **component_evidence}
    except Exception as e:
        logger.error(f'get_flowsheet_timeseries failed: {e}', exc_info=True)
        return public_error(e, code='TIMESERIES_READ_FAILED', job_id=job_id)

@mcp.tool()
async def delete_session(session_id: str) -> Dict[str, Any]:
    """
    Delete a flowsheet session and all its files.

    Args:
        session_id: Session identifier

    Returns:
        Dict with deletion status

    Example:
        >>> await delete_session(session_id="abc123")
    """
    try:
        deleted = session_manager.delete_session(session_id)
        if deleted:
            return {'session_id': session_id, 'status': 'deleted', 'success': True, 'message': f"Session '{session_id}' deleted."}
        else:
            return public_error(f"Session '{session_id}' not found", code='SESSION_NOT_FOUND', session_id=session_id)
    except Exception as e:
        logger.error(f'delete_session failed: {e}', exc_info=True)
        return public_error(e, code='SESSION_DELETE_FAILED', session_id=session_id)

@mcp.tool()
async def get_model_components(model_type: str, include_typical_values: bool=True) -> Dict[str, Any]:
    """
    Get component IDs and metadata for a process model.

    Use this tool to discover valid component IDs before creating streams.
    Components listed under ``handoff_only`` are accepted by StreamState for
    downstream conversion contracts (for example mASM2d sulfate into MCAS), but
    they are not part of the native QSDsan kinetic component set.

    Args:
        model_type: Process model ("ASM2d", "mADM1", "ASM1", etc.)
        include_typical_values: Include typical domestic wastewater concentrations

    Returns:
        Dict with component IDs, names, categories, and typical values

    Example:
        >>> result = await get_model_components(model_type="ASM2d")
        >>> print(result["components"])  # List of component IDs with metadata
    """
    try:
        canonical_model = normalize_model_name(model_type)
        mt = ModelType(canonical_model)
        model_info = get_model_info(mt)
        components = model_info.get('components')
        if components is None:
            return {'model_type': canonical_model, 'n_components': model_info.get('n_components'), 'handoff_only': [], 'note': f'Component list for {canonical_model} uses upstream QSDsan. Import qsdsan.processes and use the create_*_cmps() functions.'}
        component_list = [spec.to_tool_dict(include_typical_values=include_typical_values) for spec in get_component_specs(canonical_model)]
        handoff_only = [spec.to_tool_dict(include_typical_values=include_typical_values) for spec in get_handoff_only_component_specs(canonical_model)]
        categories = {}
        for comp in component_list:
            cat = comp.get('category', 'unknown')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(comp['id'])
        concentration_units = get_catalog_concentration_units(canonical_model)
        note = ''
        if handoff_only:
            note = f'handoff_only components are accepted at StreamState boundaries for downstream conversion but are not solved by the native {canonical_model} kinetic model.'
        return {'model_type': canonical_model, 'n_components': len(components), 'concentration_units': concentration_units, 'components': component_list, 'handoff_only': handoff_only, 'categories': categories, 'note': note, 'description': model_info.get('description', ''), 'status': model_info.get('status', 'available')}
    except ValueError as e:
        return public_error(e, code='MODEL_TYPE_UNKNOWN', model_type=model_type)
    except Exception as e:
        logger.error(f'get_model_components failed: {e}', exc_info=True)
        return public_error(e, code='COMPONENT_LIST_FAILED', model_type=model_type)

@mcp.tool()
async def validate_flowsheet(session_id: str) -> Dict[str, Any]:
    """
    Validate a flowsheet without compiling it.

    Performs pre-compilation validation checks including:
    - Unit inputs resolve to streams or other units
    - No orphan units (not connected to anything)
    - Model compatibility across junctions
    - Potential recycle detection

    Args:
        session_id: Session identifier

    Returns:
        Dict with is_valid, errors, warnings, and detected recycles

    Example:
        >>> result = await validate_flowsheet(session_id="abc123")
        >>> if not result["is_valid"]:
        ...     print(result["errors"])
    """
    try:
        from qsdsan_engine_mcp.utils.topo_sort import validate_flowsheet_connectivity, detect_cycles
        from qsdsan_engine_mcp.core.unit_registry import get_unit_spec, JUNCTION_MODEL_TRANSFORMS, compile_engineering_inputs
        session = session_manager.get_session(session_id)
        errors, warnings = validate_flowsheet_connectivity(session.units, session.streams, session.connections)
        detected_recycles = []
        try:
            cycle_info = detect_cycles(session.units, session.connections, existing_recycles=set())
            if cycle_info:
                detected_recycles = cycle_info
        except Exception:
            pass
        orphan_units = []
        for unit_id, config in session.units.items():
            has_input = bool(config.inputs)
            has_output = any((conn.from_port.startswith(unit_id) for conn in session.connections))
            has_downstream = any((unit_id in other_config.inputs for other_id, other_config in session.units.items() if other_id != unit_id))
            if not has_input and (not has_output) and (not has_downstream):
                orphan_units.append(unit_id)
        if orphan_units:
            warnings.append(f'Orphan units (not connected): {orphan_units}')
        missing_engineering_inputs: Dict[str, List[str]] = {}
        applied_engineering_defaults: Dict[str, Dict[str, Any]] = {}
        internal_hygiene_summary = {'auto_inserted_units': sorted((unit_id for unit_id, config in session.units.items() if getattr(config, 'auto_inserted', False))), 'detected_recycles': detected_recycles}
        for unit_id, config in session.units.items():
            compiled = compile_engineering_inputs(unit_type=config.unit_type, engineering_inputs=config.engineering_inputs)
            unit_missing = []
            for error in compiled['errors']:
                if 'Missing required engineering input:' in error:
                    unit_missing.append(error.split(':', 1)[1].strip())
                else:
                    warnings.append(f'{unit_id}: {error}')
            if unit_missing:
                missing_engineering_inputs[unit_id] = unit_missing
            if compiled['applied_defaults']:
                applied_engineering_defaults[unit_id] = compiled['applied_defaults']
            if compiled['warnings']:
                warnings.extend((f'{unit_id}: {warning}' for warning in compiled['warnings']))
        junction_types = set(JUNCTION_MODEL_TRANSFORMS)
        model_compat_warnings = []
        for unit_id, config in session.units.items():
            unit_type = config.unit_type
            if unit_type in junction_types:
                try:
                    spec = get_unit_spec_from_registry(unit_type)
                    junction_models = set(spec.compatible_models) if spec.compatible_models else set()
                except Exception:
                    continue
                for input_ref in config.inputs:
                    upstream_unit_id = None
                    if '-' in input_ref and (not input_ref.startswith('-')):
                        upstream_unit_id = input_ref.split('-')[0]
                    elif input_ref in session.units:
                        upstream_unit_id = input_ref
                    if upstream_unit_id and upstream_unit_id in session.units:
                        upstream_config = session.units[upstream_unit_id]
                        upstream_type = upstream_config.unit_type
                        try:
                            upstream_spec = get_unit_spec_from_registry(upstream_type)
                            upstream_models = set(upstream_spec.compatible_models) if upstream_spec.compatible_models else set()
                            if upstream_models and junction_models:
                                common = upstream_models & junction_models
                                if not common:
                                    model_compat_warnings.append(f"Junction '{unit_id}' ({unit_type}) may be incompatible with upstream unit '{upstream_unit_id}' ({upstream_type}): junction supports {list(junction_models)}, upstream supports {list(upstream_models)}")
                        except Exception:
                            pass
        warnings.extend(model_compat_warnings)
        return {'session_id': session_id, 'is_valid': len(errors) == 0, 'errors': errors, 'warnings': warnings, 'missing_engineering_inputs': missing_engineering_inputs, 'applied_engineering_defaults': applied_engineering_defaults, 'engineering_readiness': len(errors) == 0 and (not missing_engineering_inputs), 'internal_hygiene_summary': internal_hygiene_summary, 'detected_recycles': detected_recycles, 'n_units': len(session.units), 'n_streams': len(session.streams), 'n_connections': len(session.connections)}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'validate_flowsheet failed: {e}', exc_info=True)
        return public_error(e, code='FLOWSHEET_VALIDATION_FAILED', session_id=session_id)

@mcp.tool()
async def suggest_recycles(session_id: str) -> Dict[str, Any]:
    """
    Detect potential recycle streams in a flowsheet.

    Analyzes the flowsheet topology to identify cycles that likely
    represent recycle streams (e.g., RAS, internal recycles).

    Args:
        session_id: Session identifier

    Returns:
        Dict with detected cycles and suggested recycle configurations

    Example:
        >>> result = await suggest_recycles(session_id="abc123")
        >>> for suggestion in result["suggestions"]:
        ...     print(f"Recycle: {suggestion['suggested_recycle']}")
    """
    try:
        from qsdsan_engine_mcp.utils.topo_sort import detect_cycles
        session = session_manager.get_session(session_id)
        cycles = detect_cycles(session.units, session.connections)
        suggestions = []
        for cycle_info in cycles:
            cycle_path = cycle_info.get('cycle_path', [])
            if len(cycle_path) >= 2:
                from_unit = cycle_path[-2]
                to_unit = cycle_path[-1]
                from_type = session.units[from_unit].unit_type if from_unit in session.units else None
                recycle_type = 'internal_recycle'
                if from_type and 'Splitter' in from_type:
                    recycle_type = 'return_activated_sludge'
                elif from_type and 'Clarifier' in from_type:
                    recycle_type = 'return_activated_sludge'
                suggestions.append({'cycle_path': cycle_path, 'suggested_recycle': {'from': f'{from_unit}-0', 'to': f'{to_unit}-1', 'stream_id': f'recycle_{from_unit}_{to_unit}'}, 'recycle_type': recycle_type, 'confidence': 'medium'})
        sources = [sid for sid, s in session.streams.items() if s.stream_type == 'influent']
        all_from_units = set()
        for conn in session.connections:
            try:
                from qsdsan_engine_mcp.utils.pipe_parser import parse_port_notation
                from_ref = parse_port_notation(conn.from_port)
                all_from_units.add(from_ref.unit_id)
            except Exception:
                pass
        sinks = [uid for uid in session.units.keys() if uid not in all_from_units]
        return {'session_id': session_id, 'n_cycles_detected': len(cycles), 'detected_cycles': [c.get('cycle_path', []) for c in cycles], 'suggestions': suggestions, 'topology': {'sources': sources, 'sinks': sinks}}
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'suggest_recycles failed: {e}', exc_info=True)
        return public_error(e, code='RECYCLE_SUGGESTION_FAILED', session_id=session_id)

@mcp.tool()
async def get_artifact(job_id: str, artifact_type: str, format: Optional[str]=None) -> Dict[str, Any]:
    """
    Get simulation artifact content directly.

    Use this tool to retrieve diagram SVGs, QMD reports, or other artifacts
    as content rather than just file paths.

    Args:
        job_id: Job identifier from simulation
        artifact_type: Type of artifact: "diagram", "report", "timeseries"
        format: Optional format hint (e.g., "svg", "qmd", "json")

    Returns:
        Dict with artifact content, path, and metadata

    Example:
        >>> result = await get_artifact(job_id="abc123", artifact_type="diagram")
        >>> svg_content = result["content"]
    """
    from qsdsan_engine_mcp.utils.path_utils import validate_safe_path, validate_id
    try:
        validate_id(job_id, 'job_id')
        job_dir = validate_safe_path(_job_root(), job_id, 'job_id')
        if not job_dir.exists():
            return public_error(f'Job {job_id} not found', code='JOB_NOT_FOUND', job_id=job_id)
        artifact_patterns = {'diagram': ['flowsheet.svg', 'flowsheet.png', 'diagram.svg'], 'report': ['report.qmd', 'anaerobic_report.qmd', 'aerobic_report.qmd'], 'timeseries': ['timeseries.json', 'time_series.json'], 'results': ['results.json', 'simulation_results.json']}
        if artifact_type not in artifact_patterns:
            return public_error(f"Unknown artifact_type '{artifact_type}'. Valid types: {list(artifact_patterns.keys())}", code='INVALID_ARTIFACT_TYPE', artifact_type=artifact_type, valid_types=list(artifact_patterns.keys()))
        artifact_path = None
        for pattern in artifact_patterns[artifact_type]:
            candidate = job_dir / pattern
            if candidate.exists():
                artifact_path = candidate
                break
        if artifact_path is None:
            return public_error(f'No {artifact_type} artifact found in job {job_id}', code='ARTIFACT_NOT_FOUND', job_id=job_id, searched=artifact_patterns[artifact_type], job_dir=str(job_dir))
        binary_extensions = {'.svg', '.png', '.pdf'}
        is_binary = artifact_path.suffix in binary_extensions
        if is_binary and artifact_path.suffix == '.svg':
            with open(artifact_path, 'r', encoding='utf-8') as f:
                content = f.read()
            is_binary = False
        elif is_binary:
            import base64
            with open(artifact_path, 'rb') as f:
                content = base64.b64encode(f.read()).decode('ascii')
            return {'job_id': job_id, 'artifact_type': artifact_type, 'format': artifact_path.suffix[1:], 'encoding': 'base64', 'content': content, 'path': str(artifact_path), 'size_bytes': artifact_path.stat().st_size}
        else:
            with open(artifact_path, 'r', encoding='utf-8') as f:
                content = f.read()
        if artifact_path.suffix == '.json':
            try:
                parsed = json.loads(content)
                return {'job_id': job_id, 'artifact_type': artifact_type, 'format': 'json', 'content': parsed, 'path': str(artifact_path)}
            except json.JSONDecodeError:
                pass
        return {'job_id': job_id, 'artifact_type': artifact_type, 'format': artifact_path.suffix[1:] if artifact_path.suffix else 'text', 'content': content, 'path': str(artifact_path), 'size_bytes': len(content)}
    except Exception as e:
        logger.error(f'get_artifact failed: {e}', exc_info=True)
        return public_error(e, code='ARTIFACT_READ_FAILED', job_id=job_id, artifact_type=artifact_type)

def _load_costing_report(job_id: str) -> Dict[str, Any]:
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise ValueError(f'Job {job_id} not found')
    report_path = job_dir / 'costing.json'
    if not report_path.exists():
        raise ValueError(f'No strict costing report found for job {job_id}. Enable costing on the session and simulate again.')
    with open(report_path) as f:
        return json.load(f)

@mcp.tool()
async def enable_costing(session_id: str, tea_profile: str='qsdsan_simpletea', economic_assumptions: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """Enable strict upstream-backed costing for a flowsheet session."""
    try:
        return session_manager.enable_costing(session_id=session_id, tea_profile=tea_profile, economic_assumptions=economic_assumptions)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, tea_profile=tea_profile)
    except Exception as e:
        logger.error(f'enable_costing failed: {e}', exc_info=True)
        return public_error(e, code='COSTING_ENABLE_FAILED', session_id=session_id, tea_profile=tea_profile)

@mcp.tool()
async def add_unit_costing(session_id: str, unit_id: str) -> Dict[str, Any]:
    """Add one public cost-supported unit to the session costing scope."""
    try:
        return session_manager.add_unit_costing(session_id, unit_id)
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, unit_id=unit_id)
    except Exception as e:
        logger.error(f'add_unit_costing failed: {e}', exc_info=True)
        return public_error(e, code='UNIT_COSTING_ADD_FAILED', session_id=session_id, unit_id=unit_id)

@mcp.tool()
async def list_costed_units(session_id: str) -> Dict[str, Any]:
    """List the current session costing configuration and selected units."""
    try:
        result = session_manager.list_costed_units(session_id)
        result['units'] = result.get('costed_units', [])
        result['total'] = len(result['units'])
        return result
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id)
    except Exception as e:
        logger.error(f'list_costed_units failed: {e}', exc_info=True)
        return public_error(e, code='COSTING_LIST_FAILED', session_id=session_id)

@mcp.tool()
async def get_costing(session_id: Optional[str]=None, job_id: Optional[str]=None) -> Dict[str, Any]:
    """Return the persisted strict costing report for the latest solved job."""
    try:
        if session_id and job_id:
            return public_error('Provide either session_id or job_id, not both', code='INVALID_INPUT', session_id=session_id, job_id=job_id)
        if not session_id and (not job_id):
            return public_error('Provide either session_id or job_id', code='INVALID_INPUT')
        if session_id:
            job_id = session_manager.get_last_job_id(session_id)
            if not job_id:
                return public_error(f"Session '{session_id}' has no recorded simulation job. Simulate after enabling costing first.", code='JOB_NOT_FOUND', session_id=session_id)
        report = _load_costing_report(job_id)
        report['job_id'] = job_id
        return report
    except ValueError as e:
        return public_error(e, code='INVALID_INPUT', session_id=session_id, job_id=job_id)
    except Exception as e:
        logger.error(f'get_costing failed: {e}', exc_info=True)
        return public_error(e, code='COSTING_READ_FAILED', session_id=session_id, job_id=job_id)
_REFERENCE_STATE_DIR = Path(__file__).parent / 'tests'
_REFERENCE_STATE_FILES = {'ASM2d': 'test_asm2d_state.json', 'mADM1': 'test_madm1_state.json', 'mASM2d': 'test_masm2d_state.json'}

@mcp.resource('qsdsan://models/{model_type}/reference-influent')
def get_reference_influent(model_type: str) -> str:
    """Complete reference influent StreamState for a model type (domestic wastewater).

    Returns a JSON StreamState dict ready for create_stream() or other state-driven setup.
    For create_stream(), unpack into separate args (stream_id, flow_m3_d, concentrations, temperature_K).

    Supported model types: ASM2d, mASM2d, mADM1
    """
    canonical = normalize_model_name(model_type)
    if canonical not in _REFERENCE_STATE_FILES:
        return json.dumps({'error': f"No reference state for model '{model_type}'. Available: {list(_REFERENCE_STATE_FILES.keys())}"})
    path = _REFERENCE_STATE_DIR / _REFERENCE_STATE_FILES[canonical]
    if not path.exists():
        return json.dumps({'error': f'Reference state file not found: {path}'})
    return path.read_text(encoding='utf-8')

@mcp.resource('qsdsan://models/{model_type}/components')
def get_model_component_catalog(model_type: str) -> str:
    """Component catalog for a model type with IDs, descriptions, units, typical values, and categories.

    More structured than get_model_components tool output — includes typical ranges
    and required/optional flags for each component.
    """
    canonical = normalize_model_name(model_type)
    info = get_model_info(canonical)
    if not info:
        return json.dumps({'error': f'Unknown model type: {model_type}'})
    if info.get('components') is None:
        return json.dumps({'model_type': canonical, 'n_components': info.get('n_components'), 'note': f'Component list for {canonical} uses upstream QSDsan.'}, indent=2)
    components = get_catalog_component_ids(canonical)
    units = get_catalog_concentration_units(canonical)
    catalog = [spec.to_resource_dict(units=units) for spec in get_component_specs(canonical)]
    handoff_only = [spec.to_resource_dict(units=units) for spec in get_handoff_only_component_specs(canonical)]
    return json.dumps({'model_type': canonical, 'n_components': len(components), 'concentration_units': units, 'components': catalog, 'handoff_only': handoff_only}, indent=2)

@mcp.resource('qsdsan://jobs/{job_id}/streams/{stream_id}')
def get_job_stream_state(job_id: str, stream_id: str) -> str:
    """Full StreamState for a specific stream from a completed simulation job.

    Returns a JSON StreamState dict suitable for convert_state() or prepare_downstream_handoff().
    """
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    if not job_dir.exists():
        return _resource_error_json(f"Job '{job_id}' not found", code='JOB_NOT_FOUND', job_id=job_id)
    stream_states_path = job_dir / 'stream_states.json'
    if not stream_states_path.exists():
        return _resource_error_json(f'No stream_states.json in job {job_id}. Run a simulation that persists stream states before requesting per-stream export.', code='STREAM_STATES_NOT_FOUND', job_id=job_id, available_files=[f.name for f in job_dir.iterdir() if f.is_file()])
    with open(stream_states_path, 'r', encoding='utf-8') as f:
        all_states = json.load(f)
    if stream_id not in all_states:
        return _resource_error_json(f"Stream '{stream_id}' not found in job {job_id}", code='STREAM_NOT_FOUND', job_id=job_id, stream_id=stream_id, available_streams=list(all_states.keys()))
    return json.dumps(all_states[stream_id], indent=2)

@mcp.resource('qsdsan://handoff-profiles')
def get_handoff_profiles_resource() -> str:
    """All downstream handoff profiles with supported source models, required analytes, and target engines.

    Use this to determine which conversion target (mASM2d, mADM1, ASM2d) is required
    before calling prepare_downstream_handoff().
    """
    profiles = get_handoff_profiles(public_only=False)
    return json.dumps({'profiles': profiles}, indent=2)

@mcp.resource('qsdsan://jobs/{job_id}/manifest')
def get_job_manifest(job_id: str) -> str:
    """Index of all simulation outputs for a completed job.

    Single-read discovery: returns artifact URIs, sizes, key metrics,
    and guidance on which resources/tools to use for detailed data.
    """
    import math as _math
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    if not job_dir.exists():
        return _resource_error_json(f"Job '{job_id}' not found", code='JOB_NOT_FOUND', job_id=job_id)
    manifest: Dict[str, Any] = {'job_id': job_id, 'artifacts': {}}
    checks = [('results', ['simulation_results.json', 'results.json', 'output_state.json'], f'qsdsan://jobs/{job_id}/results'), ('stream_states', ['stream_states.json'], None), ('diagram', ['flowsheet.svg', 'diagram.svg'], f'qsdsan://jobs/{job_id}/diagram'), ('report', ['report.qmd', 'anaerobic_report.qmd', 'aerobic_report.qmd'], f'qsdsan://jobs/{job_id}/report'), ('costing', ['costing_report.json', 'costing.json'], f'qsdsan://jobs/{job_id}/costing'), ('timeseries', ['timeseries.json'], None)]
    for name, filenames, uri in checks:
        for fname in filenames:
            path = job_dir / fname
            if path.exists():
                entry: Dict[str, Any] = {'file': fname, 'size_bytes': path.stat().st_size}
                if uri:
                    entry['uri'] = uri
                if name == 'stream_states':
                    try:
                        streams = list(json.load(open(path)).keys())
                        entry['streams'] = streams
                        entry['resource_uris'] = {s: f'qsdsan://jobs/{job_id}/streams/{s}' for s in streams}
                    except Exception:
                        pass
                elif name == 'timeseries':
                    entry['tool'] = 'get_flowsheet_timeseries'
                    entry['note'] = 'Use stream_ids, components, max_points filters'
                    try:
                        ts = json.load(open(path))
                        entry['n_timepoints'] = len(ts.get('time', []))
                        entry['tracked_streams'] = list(ts.get('streams', {}).keys())
                    except Exception:
                        pass
                manifest['artifacts'][name] = entry
                break
    for fname in ['simulation_results.json', 'results.json']:
        path = job_dir / fname
        if path.exists():
            try:
                r = json.load(open(path))
                eff = r.get('effluent') or r.get('effluent_quality') or r.get('simulation_results', {}).get('effluent_quality') or {}
                snap_keys = ('COD_mg_L', 'TSS_mg_L', 'NH4_mg_N_L', 'NO3_mg_N_L', 'PO4_mg_P_L', 'pH', 'flow_m3_d')
                manifest['effluent_snapshot'] = {k: round(v, 2) if isinstance(v, float) else v for k, v in eff.items() if k in snap_keys}
                perf = r.get('performance') or r.get('simulation_results', {}).get('removal_efficiency') or {}
                manifest['performance_snapshot'] = {k: round(v, 1) if isinstance(v, float) else v for k, v in perf.items() if 'removal' in k or 'pct' in k}
                manifest['model_type'] = r.get('model_type') or (r.get('metadata') or {}).get('model_type')
                manifest['system_id'] = r.get('system_id')
                manifest['status'] = r.get('status', 'unknown')
            except Exception:
                pass
            break
    return json.dumps(manifest, indent=2)

@mcp.resource('qsdsan://jobs/{job_id}/results')
def get_job_results_resource(job_id: str) -> str:
    """Simulation results summary (effluent quality, performance, reactor config)."""
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    for fname in ['simulation_results.json', 'results.json', 'output_state.json']:
        path = job_dir / fname
        if path.exists():
            return path.read_text(encoding='utf-8')
    return _resource_error_json(f'No results in job {job_id}', code='RESULTS_NOT_FOUND', job_id=job_id)

@mcp.resource('qsdsan://jobs/{job_id}/diagram')
def get_job_diagram(job_id: str) -> str:
    """SVG flowsheet diagram."""
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    for name in ['flowsheet.svg', 'diagram.svg']:
        path = job_dir / name
        if path.exists():
            return path.read_text(encoding='utf-8')
    return _resource_error_json(f'No diagram in job {job_id}', code='DIAGRAM_NOT_FOUND', job_id=job_id)

@mcp.resource('qsdsan://jobs/{job_id}/report')
def get_job_report(job_id: str) -> str:
    """QMD report."""
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    for name in ['report.qmd', 'anaerobic_report.qmd', 'aerobic_report.qmd']:
        path = job_dir / name
        if path.exists():
            return path.read_text(encoding='utf-8')
    return _resource_error_json(f'No report in job {job_id}', code='REPORT_NOT_FOUND', job_id=job_id)

@mcp.resource('qsdsan://jobs/{job_id}/costing')
def get_job_costing_resource(job_id: str) -> str:
    """Costing report (CAPEX/OPEX)."""
    try:
        job_dir = _safe_job_dir(job_id)
    except ValueError as exc:
        return _resource_error_json(exc, code='INVALID_JOB_ID', job_id=job_id)
    for name in ['costing_report.json', 'costing.json']:
        path = job_dir / name
        if path.exists():
            return path.read_text(encoding='utf-8')
    return _resource_error_json(f'No costing report in job {job_id}', code='COSTING_NOT_FOUND', job_id=job_id)

@mcp.prompt()
def build_flowsheet(topology: str, model_type: str='ASM2d', flow_m3_d: float=4000.0) -> str:
    """Step-by-step flowsheet construction guide with correct outputs, recycles, and costing.

    Args:
        topology: One of "MLE-MBR", "A2O-MBR", "AO-MBR", "CSTR-Anaerobic"
        model_type: Process model — "ASM2d", "mASM2d", or "mADM1"
        flow_m3_d: Design flow in m3/day
    """
    topology_upper = topology.upper().replace(' ', '-')
    if topology_upper == 'MLE-MBR':
        return f'## Flowsheet Construction: MLE-MBR ({model_type}, {flow_m3_d} m3/d)\n\n### Step 1: Create session\n```\ncreate_flowsheet_session(model_type="{model_type}")\n```\nCapture session_id.\n\n### Step 2: Create influent stream\nRead reference influent from resource `qsdsan://models/{model_type}/reference-influent`, then:\n```\ncreate_stream(session_id=SID, stream_id="influent", flow_m3_d={flow_m3_d},\n  concentrations=<from resource>, concentration_units="{('kg/m3' if model_type == 'mADM1' else 'mg/L')}",\n  stream_type="influent", temperature_K=293.15)\n```\n\n### Step 3: Create units (sequential — port dependencies)\n```\ncreate_unit(session_id=SID, unit_type="CSTR", unit_id="A1",\n  inputs=["influent"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.25)}, "aeration": 0, "DO_ID": "S_O2"}})\n\ncreate_unit(session_id=SID, unit_type="CSTR", unit_id="O1",\n  inputs=["A1-0"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.5)}, "aeration": 2.0, "DO_ID": "S_O2"}})\n\ncreate_unit(session_id=SID, unit_type="CompletelyMixedMBR", unit_id="MBR",\n  inputs=["O1-0"],\n  outputs=["effluent", "mbr_retentate"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.375)}, "aeration": 2.0, "DO_ID": "S_O2",\n    "solids_capture_rate": 0.999, "pumped_flow": {int(flow_m3_d * 1.05)}}})\n\ncreate_unit(session_id=SID, unit_type="Splitter", unit_id="SP",\n  inputs=["MBR-1"],\n  outputs=["RAS", "WAS"],\n  engineering_inputs={{"split": 0.84}})\n\ncreate_unit(session_id=SID, unit_type="Pump", unit_id="P1",\n  inputs=["SP-0"],\n  outputs=["RAS_return"])\n```\n\n### Step 4: Connect RAS recycle\n```\nconnect_units(session_id=SID, connections=[\n  {{"from": "P1-0", "to": "1-A1", "stream_id": "RAS_return"}}\n])\n```\nNote: `1-A1` is correct destination input-port notation.\n\n### Step 5: Validate and build\n```\nvalidate_flowsheet(session_id=SID)\nsuggest_recycles(session_id=SID)\ngenerate_session_inoculum(session_id=SID)\nbuild_system(session_id=SID, system_id="mle_sys", recycle_streams=["RAS_return"])\n```\n\n### Step 6: Costing (optional)\nCost-supported units: Pump, WWTpump, SludgePump, StorageTank, MixTank, PrimaryClarifier, BeltThickener, HydraulicDelay, Screening.\nCSTRs and MBR are NOT cost-supported (solve-only).\n```\nenable_costing(session_id=SID, tea_profile="qsdsan_simpletea")\nadd_unit_costing(session_id=SID, unit_id="P1")  # Pump is cost-supported\n```\n\n### Step 7: Simulate\n```\nsimulate_built_system(session_id=SID, duration_days=10, timestep_hours=6,\n  track=["effluent"], effluent_stream_ids=["effluent"],\n  report=true, diagram=true, include_components=true)\n```\n'
    elif topology_upper == 'A2O-MBR':
        return f'## Flowsheet Construction: A2O-MBR ({model_type}, {flow_m3_d} m3/d)\n\n### Step 1: Create session\n```\ncreate_flowsheet_session(model_type="{model_type}")\n```\n\n### Step 2: Create influent stream\nRead from `qsdsan://models/{model_type}/reference-influent`.\n\n### Step 3: Create units\n```\ncreate_unit(session_id=SID, unit_type="CSTR", unit_id="AN",\n  inputs=["influent"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.15)}, "aeration": 0}})\n\ncreate_unit(session_id=SID, unit_type="CSTR", unit_id="AX",\n  inputs=["AN-0"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.2)}, "aeration": 0, "DO_ID": "S_O2"}})\n\ncreate_unit(session_id=SID, unit_type="CSTR", unit_id="OX",\n  inputs=["AX-0"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.4)}, "aeration": 2.0, "DO_ID": "S_O2"}})\n\ncreate_unit(session_id=SID, unit_type="CompletelyMixedMBR", unit_id="MBR",\n  inputs=["OX-0"],\n  outputs=["effluent", "mbr_retentate"],\n  engineering_inputs={{"V_max": {int(flow_m3_d * 0.3)}, "aeration": 2.0, "DO_ID": "S_O2",\n    "solids_capture_rate": 0.999, "pumped_flow": {int(flow_m3_d * 1.05)}}})\n\ncreate_unit(session_id=SID, unit_type="Splitter", unit_id="SP",\n  inputs=["MBR-1"], outputs=["RAS", "WAS"],\n  engineering_inputs={{"split": 0.84}})\n\ncreate_unit(session_id=SID, unit_type="Pump", unit_id="P1",\n  inputs=["SP-0"], outputs=["RAS_return"])\n```\n\n### Step 4: Connect recycle\n```\nconnect_units(session_id=SID, connections=[\n  {{"from": "P1-0", "to": "1-AN", "stream_id": "RAS_return"}}\n])\n```\n\n### Step 5-7: Same as MLE-MBR (including generate_session_inoculum before build)\n'
    elif topology_upper == 'AO-MBR':
        return f'## Flowsheet Construction: AO-MBR ({model_type}, {flow_m3_d} m3/d)\n\nSame as MLE-MBR but with a single anoxic/aerobic zone pair.\nUse MLE-MBR topology with adjusted volumes.\n'
    elif topology_upper == 'CSTR-ANAEROBIC':
        return f'## Flowsheet Construction: CSTR-Anaerobic ({model_type}, {flow_m3_d} m3/d)\n\n### Step 1-4\n```\ncreate_flowsheet_session(model_type="mADM1")\ncreate_stream(session_id=SID, stream_id="influent", flow_m3_d={flow_m3_d}, ...)\ncreate_unit(session_id=SID, unit_type="AnaerobicCSTRmADM1", unit_id="AD",\n  inputs=["influent"], outputs=["biogas", "digestate"],\n  engineering_inputs={{"V_liq": {int(flow_m3_d * 20)}}})\ngenerate_session_inoculum(session_id=SID)\nbuild_system(session_id=SID, system_id="ad_sys")\nsimulate_built_system(session_id=SID, duration_days=120, method="BDF",\n  track=["digestate"], biogas_stream_ids=["biogas"])\n```\n'
    else:
        available = ['MLE-MBR', 'A2O-MBR', 'AO-MBR', 'CSTR-Anaerobic']
        return f"Unknown topology '{topology}'. Available: {available}"

@mcp.prompt()
def analyze_results(model_type: str='ASM2d') -> str:
    """Guide for navigating simulation results and assessing engineering reasonableness.

    Args:
        model_type: Primary process model for the solved system
    """
    return f'## Result Navigation: {model_type}\n\n### Core result blocks\n`get_job_results(job_id=...)` returns a dict with a `results` key. Key paths:\n  - `results.effluent_quality` — predicted effluent metrics\n  - `results.removal_efficiency` — COD/TSS/TN/TP removal rollups\n  - `results.per_engine_summaries.geometry` — per-unit liquid/gas volume and HRT\n  - `results.per_engine_summaries.asm2d` — MLSS, SRT, HRT, N/P removal when ASM-family units are present\n  - `results.per_engine_summaries.madm1` — biogas, biomass yields, sulfur, COD removal when mADM1 units are present\n  - `results.process_health` — per-process inhibition rows from PR-DD\n  - `results.simulation` — solver status and convergence metadata\n\n### Costing\n`get_costing(session_id=...)` returns the separate capital and operating-cost payload.\n\n### Stream states\nUse `qsdsan://jobs/{{job_id}}/streams/{{stream_id}}` for any saved StreamState.\n'

@mcp.tool()
async def simulate_madm1_serial_mineral_history(native_liquid_state: Dict[str, float], train: Dict[str, Any], inlet_ph: float, horizon_days: float, time_step_days: float, native_gas_transfer_completed: bool, ligand_sites_mol_m3: float=0.0, dissolved_oxygen_mol_m3: float=0.0, dose_mol_d: Optional[Dict[str, float]]=None, initial_state_by_zone: Optional[Dict[str, Dict[str, float]]]=None, initial_cell_states_mol_m3: Optional[Dict[str, Dict[str, float]]]=None, resume_checkpoint: Optional[Dict[str, Any]]=None, temperature_k: float=298.15, phase_parameters: Optional[Dict[str, Dict[str, float]]]=None, redox_parameters: Optional[Dict[str, Dict[str, float]]]=None, oxidation_parameters: Optional[Dict[str, Dict[str, float]]]=None, aging_parameters: Optional[Dict[str, Dict[str, float]]]=None, surface_parameters: Optional[Dict[str, Dict[str, float]]]=None, ligand_parameters: Optional[Dict[str, float]]=None, activity_coefficients: Optional[Dict[str, float]]=None, solvent_water_activity: float=1.0, species_coverage_fraction: float=0.0, zone_parameter_overrides: Optional[Dict[str, Dict[str, Any]]]=None, fixed_background_charge_eq_m3: Optional[float]=None, imposed_ph_by_zone: Optional[Dict[str, float]]=None, sample_every_steps: int=1, return_native_dissolved_feedback: bool=False, native_chemistry_delegation: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """Run the shared serial Fe-S-P history sidecar after native mADM1 gas transfer."""
    try:
        from qsdsan_engine_mcp.models.shared_chemistry import DEFAULT_MADM1_FE_S_P_ADAPTER
        from puran_mcp_support.schema import coerce_serial_chemistry_train
        return await _locked_to_thread(DEFAULT_MADM1_FE_S_P_ADAPTER.simulate_serial_madm1_history, native_liquid_state, train=coerce_serial_chemistry_train(train), inlet_ph=inlet_ph, horizon_days=horizon_days, time_step_days=time_step_days, native_gas_transfer_completed=native_gas_transfer_completed, ligand_sites_mol_m3=ligand_sites_mol_m3, dissolved_oxygen_mol_m3=dissolved_oxygen_mol_m3, dose_mol_d=dose_mol_d, initial_state_by_zone=initial_state_by_zone, initial_cell_states_mol_m3=initial_cell_states_mol_m3, resume_checkpoint=resume_checkpoint, temperature_k=temperature_k, phase_parameters=phase_parameters, redox_parameters=redox_parameters, oxidation_parameters=oxidation_parameters, aging_parameters=aging_parameters, surface_parameters=surface_parameters, ligand_parameters=ligand_parameters, activity_coefficients=activity_coefficients, solvent_water_activity=solvent_water_activity, species_coverage_fraction=species_coverage_fraction, zone_parameter_overrides=zone_parameter_overrides, fixed_background_charge_eq_m3=fixed_background_charge_eq_m3, imposed_ph_by_zone=imposed_ph_by_zone, sample_every_steps=sample_every_steps, return_native_dissolved_feedback=return_native_dissolved_feedback, native_chemistry_delegation=native_chemistry_delegation)
    except Exception as exc:
        logger.error('simulate_madm1_serial_mineral_history failed: %s', exc, exc_info=True)
        return public_error(exc, code='SERIAL_MINERAL_HISTORY_FAILED')
EXTERNAL_DISABLED_TOOLS = frozenset()

def _apply_configured_tool_surface() -> None:
    """Restrict the registered MCP tools for dedicated external runtimes."""
    surface = os.getenv('MCP_TOOL_SURFACE', 'all').strip().lower()
    if surface == 'all':
        return
    if surface != 'external':
        raise RuntimeError(f"Unknown MCP_TOOL_SURFACE {surface!r}; expected 'all' or 'external'")
    for tool_name in EXTERNAL_DISABLED_TOOLS:
        mcp.local_provider.remove_tool(tool_name)
_apply_configured_tool_surface()

def main():
    mcp.run(transport='stdio')
if __name__ == '__main__':
    main()
