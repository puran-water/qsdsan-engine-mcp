import json
import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from qsdsan_engine_mcp.core.runtime_compat import apply_runtime_compat
from qsdsan_engine_mcp.core.contracts.stream_state import StreamState
from qsdsan_engine_mcp.core.model_types import ModelType, model_type_value
from qsdsan_engine_mcp.core.model_registry import get_model_info, validate_components, list_available_models, MODEL_REGISTRY, normalize_model_name
from qsdsan_engine_mcp.utils.flowsheet_session import FlowsheetSessionManager, StreamConfig, UnitConfig, ConnectionConfig
app = typer.Typer(name='qsdsan-engine', help='Universal QSDsan simulation engine for biological wastewater treatment', add_completion=False)
console = Console()

@app.command()
def convert(input: Path=typer.Option(..., '--input', '-i', help='Input StreamState JSON file'), output: Path=typer.Option(None, '--output', '-o', help='Output file (default: stdout)'), from_model: str=typer.Option(..., '--from-model', '-f', help='Source model type'), to_model: str=typer.Option(..., '--to-model', '-t', help='Target model type'), json_out: bool=typer.Option(False, '--json-out', '-j', help='Output as JSON')):
    """
    Convert StreamState between model types using the upstream ADM1p <-> mASM2d
    junction path.

    Example:
        qsdsan-engine convert -i was.json -f ASM2d -t mADM1 -o digester_feed.json
    """
    try:
        if not input.exists():
            _error_exit(f'Input file not found: {input}', json_out)
        with open(input) as f:
            state_data = json.load(f)
        input_state = StreamState.model_validate(state_data)
        from_mt = ModelType(normalize_model_name(from_model))
        to_mt = ModelType(normalize_model_name(to_model))
        if input_state.model_type != from_mt.value:
            raise ValueError(f'state.model_type must match --from-model ({input_state.model_type!r} != {from_mt.value!r})')
        if from_mt == to_mt:
            result = {'status': 'no_conversion_needed', 'message': f'Source and target are both {from_mt.value}', 'output_state': input_state.model_dump(mode='json')}
        else:
            from qsdsan_engine_mcp.core.converters import convert_state
            output_state, metadata = convert_state(input_state, to_mt)
            result = {'status': 'completed', 'message': f'Converted {from_mt.value} -> {to_mt.value}', 'output_state': output_state.model_dump(mode='json'), 'metadata': metadata}
        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f'[bold]Conversion: {from_mt.value} -> {to_mt.value}[/bold]')
            console.print(f'Status: {result.get('status', 'unknown')}')
            console.print(f'Message: {result.get('message', '')}')
        if output and 'output_state' in result:
            with open(output, 'w') as f:
                json.dump(result['output_state'], f, indent=2)
            if not json_out:
                console.print(f'[green]Saved to: {output}[/green]')
    except Exception as e:
        _error_exit(str(e), json_out)
flowsheet_app = typer.Typer(name='flowsheet', help='Dynamic flowsheet construction tools', add_completion=False)
session_manager = FlowsheetSessionManager()

@flowsheet_app.command('simulate')
def flowsheet_simulate(session_id: Optional[str]=typer.Option(None, '--session', '-s', help='Session ID (mutually exclusive with --system-id)'), system_id_opt: Optional[str]=typer.Option(None, '--system-id', help='System ID from build_system (mutually exclusive with --session)'), duration: float=typer.Option(1.0, '--duration', '-d', help='Simulation duration in days'), duration_days: float=typer.Option(None, '--duration-days', help='Alias for --duration (MCP compatibility)'), timestep: float=typer.Option(1.0, '--timestep', help='Output timestep in hours'), timestep_hours: float=typer.Option(None, '--timestep-hours', help='Alias for --timestep (MCP compatibility)'), method: str=typer.Option('RK23', '--method', help='ODE solver method (RK23, RK45, BDF)'), t_eval: Optional[str]=typer.Option(None, '--t-eval', help='Custom evaluation times as JSON list (days)'), track: Optional[str]=typer.Option(None, '--track', help='Stream IDs to track dynamically (JSON list)'), effluent_streams: Optional[str]=typer.Option(None, '--effluent-streams', help='Stream IDs for effluent analysis (JSON list)'), biogas_streams: Optional[str]=typer.Option(None, '--biogas-streams', help='Stream IDs for biogas analysis (JSON list)'), output_dir: Path=typer.Option(None, '--output-dir', '-o', help='Output directory'), report: bool=typer.Option(False, '--report', '-r', help='Legacy flag: narrative reports are supplied by the calling client'), diagram: bool=typer.Option(False, '--diagram', help='Generate flowsheet diagram'), include_components: bool=typer.Option(False, '--include-components', help='Include full component breakdown'), export_state_to: Optional[Path]=typer.Option(None, '--export-state-to', help='Export final effluent state as StreamState JSON'), run_to_convergence: bool=typer.Option(False, '--run-to-convergence', help='Run until steady state is reached'), convergence_atol: float=typer.Option(0.1, '--convergence-atol', help='Absolute tolerance for convergence (mg/L/d)'), convergence_rtol: float=typer.Option(0.001, '--convergence-rtol', help='Relative tolerance for convergence'), check_interval: float=typer.Option(2.0, '--check-interval', help='Days between convergence checks'), max_duration: Optional[float]=typer.Option(None, '--max-duration', help='Maximum simulation time for convergence mode'), dynamic_controls: Optional[str]=typer.Option(None, '--dynamic-controls', help='Sampled-data dynamic controls as a JSON object'), disturbance_schedule: Optional[str]=typer.Option(None, '--disturbance-schedule', help='Declarative feed-disturbance schedule (load shock, pH/CIP excursion) as a JSON object'), target_srt: Optional[float]=typer.Option(None, '--target-srt', help='Target SRT in days for MBR/clarifier systems'), srt_tolerance: float=typer.Option(0.1, '--srt-tolerance', help='Relative tolerance for achieved SRT'), max_srt_iterations: int=typer.Option(10, '--max-srt-iterations', help='Maximum Q_was adjustment iterations'), resume_checkpoint_file: Optional[Path]=typer.Option(None, '--resume-checkpoint-file', help='Path to a B1 full-state checkpoint JSON to restore and resume a fixed-duration transient from'), reconciliation_measurements: Optional[str]=typer.Option(None, '--reconciliation-measurements', help='JSON SCADA/lab measurement dict to project the restored state toward before the first resumed segment (requires --resume-checkpoint-file)'), reconciliation_options: Optional[str]=typer.Option(None, '--reconciliation-options', help='JSON bounded-projection options (nudge_fraction, max_rel_change, positivity_floor, dry_run)'), json_out: bool=typer.Option(False, '--json-out', '-j', help='Output as JSON')):
    """
    Simulate a compiled flowsheet session.

    Example:
        qsdsan-engine flowsheet simulate --session abc123 --duration 15 --report
        qsdsan-engine flowsheet simulate --system-id custom_mle --duration 15 --report
        qsdsan-engine flowsheet simulate --session abc123 --run-to-convergence --max-duration 100
    """
    if session_id and system_id_opt:
        _error_exit('Provide either --session or --system-id, not both', json_out)
    if not session_id and (not system_id_opt):
        _error_exit('Must provide either --session or --system-id', json_out)
    if duration_days is not None:
        duration = duration_days
    if timestep_hours is not None:
        timestep = timestep_hours
    effluent_stream_ids = json.loads(effluent_streams) if effluent_streams else None
    biogas_stream_ids = json.loads(biogas_streams) if biogas_streams else None
    custom_t_eval = json.loads(t_eval) if t_eval else None
    track_stream_ids = json.loads(track) if track else None
    dynamic_controls_spec = json.loads(dynamic_controls) if dynamic_controls else None
    disturbance_schedule_spec = json.loads(disturbance_schedule) if disturbance_schedule else None
    reconciliation_measurements_spec = json.loads(reconciliation_measurements) if reconciliation_measurements else None
    reconciliation_options_spec = json.loads(reconciliation_options) if reconciliation_options else None
    resume_checkpoint_payload = None
    if resume_checkpoint_file:
        with open(resume_checkpoint_file) as f:
            resume_checkpoint_payload = json.load(f)
    if (reconciliation_measurements_spec or reconciliation_options_spec) and (not resume_checkpoint_payload):
        _error_exit('--reconciliation-measurements/--reconciliation-options require --resume-checkpoint-file (reconciliation only applies to a restored checkpoint before the first resumed segment)', json_out)
    try:
        from qsdsan_engine_mcp.utils.flowsheet_builder import compile_system, simulate_compiled_system
        from qsdsan_engine_mcp.utils.srt_preflight import format_missing_biomass_seed_error, session_has_biomass_seed
        if system_id_opt:
            found_session_id = session_manager.find_session_by_system_id(system_id_opt)
            if not found_session_id:
                _error_exit(f"No compiled session found with system_id '{system_id_opt}'. Run 'flowsheet build' first.", json_out)
            session_id = found_session_id
        session = session_manager.get_session(session_id)
        if session.status != 'compiled':
            _error_exit(f"Session '{session_id}' is not compiled (status: {session.status}). Run 'flowsheet build' first.", json_out)
        if target_srt is not None:
            has_seed, biomass_ids, _ = session_has_biomass_seed(session)
            if not has_seed:
                _error_exit(format_missing_biomass_seed_error(session.primary_model_type, biomass_ids), json_out)
        if output_dir is None:
            output_dir = Path('output') / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        session_dir = session_manager._get_session_dir(session_id)
        build_config_path = session_dir / 'build_config.json'
        if build_config_path.exists():
            with open(build_config_path) as f:
                build_config = json.load(f)
            recycle_stream_ids = set(build_config.get('recycle_streams', []))
            unit_order = build_config.get('unit_order')
            system_id = build_config.get('system_id', session_id)
        else:
            recycle_stream_ids = set()
            unit_order = None
            system_id = session_id
        console.print(f'[bold]Compiling flowsheet session {session_id}...[/bold]') if not json_out else None
        system, build_info = compile_system(session, system_id=system_id, unit_order=unit_order, recycle_stream_ids=recycle_stream_ids)
        if run_to_convergence:
            console.print(f'[bold]Running to convergence (max: {max_duration or 'auto'} days, method: BDF)...[/bold]') if not json_out else None
        else:
            console.print(f'[bold]Simulating for {duration} days (method: {method})...[/bold]') if not json_out else None
        sim_results = simulate_compiled_system(system, duration_days=duration, timestep_hours=timestep, method=method, t_eval=custom_t_eval, track=track_stream_ids, output_dir=output_dir, model_type=session.primary_model_type, effluent_stream_ids=effluent_stream_ids, biogas_stream_ids=biogas_stream_ids, export_state_to=export_state_to, run_to_convergence=run_to_convergence, convergence_atol=convergence_atol, convergence_rtol=convergence_rtol, check_interval_days=check_interval, max_duration_days=max_duration, dynamic_controls=dynamic_controls_spec, disturbance_schedule=disturbance_schedule_spec, target_srt_days=target_srt, srt_tolerance=srt_tolerance, max_srt_iterations=max_srt_iterations, resume_from_checkpoint=resume_checkpoint_payload, reconciliation_measurements=reconciliation_measurements_spec, reconciliation_options=reconciliation_options_spec)
        if diagram:
            try:
                from qsdsan_engine_mcp.utils.diagram import save_system_diagram
                diagram_path = save_system_diagram(system, output_dir / 'flowsheet')
                sim_results['diagram_path'] = str(diagram_path)
            except Exception as e:
                sim_results['diagram_warning'] = f'Failed to generate diagram: {e}'
        sim_results['report_warning'] = 'Narrative reporting is provided by the calling client; no report generated.'
        costing_report = None
        if session.costing.enabled:
            from qsdsan_engine_mcp.utils.costing_service import build_costing_report
            costing_report = build_costing_report(system, session, costed_unit_ids=session.costing.costed_units, economic_assumptions=session.costing.economic_assumptions, tea_profile=session.costing.tea_profile)
            with open(output_dir / 'costing.json', 'w') as f:
                json.dump(costing_report, f, indent=2, default=str)
        from qsdsan_engine_mcp.utils.artifact_availability import compute_artifact_completeness
        sim_results['artifact_completeness'] = compute_artifact_completeness(sim_results)
        result = {'session_id': session_id, 'system_id': system_id, 'status': 'completed' if sim_results.get('result_valid', True) else 'invalid_result', 'config': {'duration_days': duration, 'timestep_hours': timestep, 'method': method, 'output_dir': str(output_dir), 'run_to_convergence': run_to_convergence, 'convergence_atol': convergence_atol if run_to_convergence else None, 'convergence_rtol': convergence_rtol if run_to_convergence else None, 'dynamic_controls': dynamic_controls_spec, 'target_srt_days': target_srt, 'srt_tolerance': srt_tolerance if target_srt is not None else None, 'max_srt_iterations': max_srt_iterations if target_srt is not None else None}, 'build_info': {'unit_order': build_info.unit_order, 'streams_created': build_info.streams_created, 'units_created': build_info.units_created, 'warnings': build_info.warnings}, 'simulation_results': sim_results, 'artifact_completeness': sim_results['artifact_completeness']}
        if costing_report is not None:
            result['costing'] = costing_report
        with open(output_dir / 'results.json', 'w') as f:
            json.dump(result, f, indent=2, default=str)
        if json_out:
            print(json.dumps(result, indent=2, default=str))
        else:
            console.print(f'\n[bold green]Simulation completed![/bold green]')
            console.print(f'System: {system_id}')
            console.print(f'Units: {' -> '.join(build_info.unit_order)}')
            if run_to_convergence and 'simulation' in sim_results:
                sim_info = sim_results['simulation']
                conv_status = sim_info.get('convergence_status', 'unknown')
                conv_days = sim_info.get('converged_at_days', sim_info.get('duration_days'))
                if conv_status == 'converged':
                    console.print(f'[green]Converged at: {conv_days:.1f} days[/green]')
                else:
                    console.print(f'[yellow]Status: {conv_status} at {conv_days:.1f} days[/yellow]')
            else:
                console.print(f'Duration: {duration} days')
            console.print(f'Output: {output_dir}')
            if 'effluent_quality' in sim_results:
                console.print('\n[bold]Effluent Quality:[/bold]')
                eq = sim_results['effluent_quality']
                for key, val in eq.items():
                    if isinstance(val, (int, float)):
                        console.print(f'  {key}: {val:.2f}')
            if 'removal_efficiency' in sim_results:
                console.print('\n[bold]Removal Efficiency:[/bold]')
                re = sim_results['removal_efficiency']
                for key, val in re.items():
                    if isinstance(val, (int, float)):
                        console.print(f'  {key}: {val:.1f}%')
            if build_info.warnings:
                console.print(f'\n[yellow]Warnings:[/yellow]')
                for w in build_info.warnings:
                    console.print(f'  - {w}')
    except Exception as e:
        _error_exit(str(e), json_out)

@flowsheet_app.command('reconcile')
def flowsheet_reconcile(session_id: str=typer.Option(..., '--session', '-s', help='Session ID'), measurements: str=typer.Option(..., '--measurements', help='JSON measurement dict {quantity_id: value | {value, ...}}'), checkpoint_file: Optional[Path]=typer.Option(None, '--checkpoint-file', help="B1 checkpoint JSON; defaults to the session's stored dynamic_checkpoint"), observability_map_file: Optional[Path]=typer.Option(None, '--observability-map-file', help='Optional precomputed observability map JSON (built from the system if omitted)'), options: Optional[str]=typer.Option(None, '--options', help='JSON bounded-projection options (nudge_fraction, max_rel_change, positivity_floor)'), effluent_streams: Optional[str]=typer.Option(None, '--effluent-streams', help='Effluent stream IDs (JSON list) for nutrient mapping'), dynamic_controls: Optional[str]=typer.Option(None, '--dynamic-controls', help='Sampled-data dynamic controls JSON; rebuilds + restores the controller state so a re-captured checkpoint keeps it'), disturbance_schedule: Optional[str]=typer.Option(None, '--disturbance-schedule', help='Feed-disturbance schedule JSON; rebuilds + restores the disturbance state so a re-captured checkpoint keeps it'), dry_run: bool=typer.Option(False, '--dry-run', help='Report residuals without mutating state'), persist_checkpoint: bool=typer.Option(False, '--persist-checkpoint', help='Re-capture + persist the reconciled checkpoint to the session'), output_file: Optional[Path]=typer.Option(None, '--output-file', help='Write the reconciliation payload JSON here'), json_out: bool=typer.Option(False, '--json-out', '-j', help='Output as JSON')):
    """Reconcile a checkpoint-restored twin toward current SCADA/lab observations (B3).

    Compiles the session system, restores the B1 checkpoint, projects the
    restored state a *bounded* amount toward the supplied measurements (ratio-
    preserving for DERIVED quantities so COD/TSS closure holds), and prints a
    ``qsdsan_state_reconciliation/v1`` payload. ``--dry-run`` reports residuals
    without mutating; ``--persist-checkpoint`` writes the reconciled state back.
    """
    try:
        from qsdsan_engine_mcp.utils.flowsheet_builder import compile_system
        from qsdsan_engine_mcp.utils.state_checkpoint import capture_dynamic_checkpoint, restore_dynamic_checkpoint
        from qsdsan_engine_mcp.utils.state_reconciliation import build_observability_map, read_observations, reconcile_restored_state
        from qsdsan_engine_mcp.core.control import build_control_manager
        from qsdsan_engine_mcp.core.disturbance import build_disturbance_scheduler
        measurements_spec = json.loads(measurements)
        if not isinstance(measurements_spec, dict) or not measurements_spec:
            _error_exit('--measurements must be a non-empty JSON object', json_out)
        options_spec = json.loads(options) if options else {}
        options_spec = dict(options_spec or {})
        options_spec['dry_run'] = bool(dry_run or options_spec.get('dry_run', False))
        effluent_stream_ids = json.loads(effluent_streams) if effluent_streams else None
        dynamic_controls_spec = json.loads(dynamic_controls) if dynamic_controls else None
        disturbance_schedule_spec = json.loads(disturbance_schedule) if disturbance_schedule else None
        session = session_manager.get_session(session_id)
        if session.status != 'compiled':
            _error_exit(f"Session '{session_id}' is not compiled (status: {session.status}). Run 'flowsheet build' first.", json_out)
        if checkpoint_file:
            with open(checkpoint_file) as f:
                checkpoint = json.load(f)
        else:
            checkpoint = session_manager.get_dynamic_checkpoint(session_id)
        if not checkpoint:
            _error_exit('No checkpoint available: pass --checkpoint-file or persist one to the session first (set_dynamic_checkpoint).', json_out)
        session_dir = session_manager._get_session_dir(session_id)
        build_config_path = session_dir / 'build_config.json'
        if build_config_path.exists():
            with open(build_config_path) as f:
                build_config = json.load(f)
            recycle_stream_ids = set(build_config.get('recycle_streams', []))
            unit_order = build_config.get('unit_order')
            system_id = build_config.get('system_id', session_id)
        else:
            recycle_stream_ids = set()
            unit_order = None
            system_id = session_id
        system, _ = compile_system(session, system_id=system_id, unit_order=unit_order, recycle_stream_ids=recycle_stream_ids)
        control_manager = build_control_manager(dynamic_controls_spec)
        disturbance_scheduler = build_disturbance_scheduler(disturbance_schedule_spec)
        restore_summary = restore_dynamic_checkpoint(system, checkpoint, control_manager=control_manager, disturbance_scheduler=disturbance_scheduler)
        if observability_map_file:
            with open(observability_map_file) as f:
                omap = json.load(f)
        else:
            omap = build_observability_map(system, model_type=session.primary_model_type, effluent_stream_ids=effluent_stream_ids)
        pre_observations = read_observations(system, omap)
        reconciliation = reconcile_restored_state(system, measurements_spec, omap, options_spec)
        persisted = False
        if persist_checkpoint and (not options_spec['dry_run']):
            new_checkpoint = capture_dynamic_checkpoint(system, time_days=float(checkpoint.get('time_days', 0.0) or 0.0), system_id=checkpoint.get('system_id') or system_id, model_type=checkpoint.get('model_type') or session.primary_model_type, control_manager=control_manager, disturbance_scheduler=disturbance_scheduler)
            if new_checkpoint.get('dynamic_controls') is None and checkpoint.get('dynamic_controls'):
                new_checkpoint['dynamic_controls'] = checkpoint.get('dynamic_controls')
            if new_checkpoint.get('disturbance_scheduler') is None and checkpoint.get('disturbance_scheduler'):
                new_checkpoint['disturbance_scheduler'] = checkpoint.get('disturbance_scheduler')
            session_manager.set_dynamic_checkpoint(session_id, new_checkpoint)
            persisted = True
        payload = dict(reconciliation)
        payload['session_id'] = session_id
        payload['checkpoint_time_days'] = checkpoint.get('time_days')
        payload['checkpoint_restore'] = restore_summary
        payload['observability_map'] = omap
        payload['pre_observations'] = pre_observations
        payload['checkpoint_persisted'] = persisted
        if output_file:
            output_file = Path(output_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w') as f:
                json.dump(payload, f, indent=2, default=str)
        if json_out:
            print(json.dumps(payload, indent=2, default=str))
        else:
            console.print(f'[bold green]Reconciliation {payload['status']}[/bold green] (grade: {payload['quality']['grade']}, confidence: {payload['quality']['overall_confidence']:.2f})')
            for r in payload['quantities']:
                console.print(f'  {r['quantity_id']}: pre={r['pre_twin']} meas={r['measurement']} post={r['post_twin']} applied={r['nudge_applied']} flags={r['flags']}')
            su = payload['state_update_summary']
            console.print(f'  touched_units={su['touched_units']} max_rel_change={su['max_relative_component_change']:.3f} positivity_clips={su['positivity_clips']}')
    except Exception as e:
        _error_exit(str(e), json_out)

def _error_exit(message: str, json_out: bool):
    """Exit with error message."""
    if json_out:
        print(json.dumps({'error': message}))
    else:
        console.print(f'[red]Error: {message}[/red]')
    sys.exit(1)
apply_runtime_compat()
app.add_typer(flowsheet_app, name='flowsheet')
if __name__ == '__main__':
    app()
