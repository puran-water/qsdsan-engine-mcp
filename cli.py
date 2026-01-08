#!/usr/bin/env python3
"""
QSDsan Engine CLI - Command-line interface for biological wastewater simulation.

This is the CLI adapter for the QSDsan simulation engine. It provides the same
functionality as the MCP server (server.py) but via command line.

Usage:
    qsdsan-engine simulate --template anaerobic_cstr_madm1 --influent state.json --json-out
    qsdsan-engine convert --input was.json --from-model ASM2d --to-model mADM1
    qsdsan-engine validate --state state.json --model mADM1
    qsdsan-engine templates

Benefits over MCP:
    - No server restart during development
    - Skills can invoke CLI directly
    - --help provides discoverable API
    - Direct JSON output for scripting

Architecture:
    This CLI calls the same engine core functions as the MCP adapter.
"""

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from core.plant_state import PlantState, ModelType, ValidationResult
from core.model_registry import (
    get_model_info,
    validate_components,
    list_available_models,
    MODEL_REGISTRY,
)
from core.template_registry import (
    list_templates as get_all_templates,
    get_template,
    is_template_available,
)
from core.unit_registry import (
    list_available_units,
    get_unit_spec,
    validate_unit_params,
    get_units_by_category,
    validate_model_compatibility,
)
from utils.flowsheet_session import (
    FlowsheetSessionManager,
    StreamConfig,
    UnitConfig,
    ConnectionConfig,
)

app = typer.Typer(
    name="qsdsan-engine",
    help="Universal QSDsan simulation engine for biological wastewater treatment",
    add_completion=False,
)

console = Console()


# =============================================================================
# simulate command
# =============================================================================
@app.command()
def simulate(
    template: str = typer.Option(..., "--template", "-t", help="Flowsheet template name"),
    influent: Path = typer.Option(..., "--influent", "-i", help="Path to influent PlantState JSON"),
    output_dir: Path = typer.Option(None, "--output-dir", "-o", help="Output directory for results"),
    duration_days: float = typer.Option(1.0, "--duration-days", "-d", help="Simulation duration in days"),
    timestep_hours: float = typer.Option(1.0, "--timestep-hours", help="Output timestep in hours"),
    reactor_config: Optional[str] = typer.Option(None, "--reactor-config", help="Reactor config JSON"),
    parameters: Optional[str] = typer.Option(None, "--parameters", help="Kinetic parameters JSON"),
    report: bool = typer.Option(False, "--report", "-r", help="Generate Quarto report (.qmd)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output results as JSON"),
):
    """
    Run QSDsan dynamic simulation using a flowsheet template.

    Example:
        qsdsan-engine simulate -t anaerobic_cstr_madm1 -i influent.json -d 30 --json-out
    """
    try:
        # Load influent state
        if not influent.exists():
            _error_exit(f"Influent file not found: {influent}", json_out)

        with open(influent) as f:
            influent_data = json.load(f)
        state = PlantState.from_dict(influent_data)

        # Parse optional configs
        reactor_cfg = json.loads(reactor_config) if reactor_config else {}
        params = json.loads(parameters) if parameters else {}

        # Set up output directory
        if output_dir is None:
            output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dispatch to appropriate template
        if template == "anaerobic_cstr_madm1":
            result = _run_anaerobic_cstr_madm1(
                state=state,
                duration_days=duration_days,
                timestep_hours=timestep_hours,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
            )
        elif template == "mle_mbr_asm2d":
            result = _run_mle_mbr_asm2d(
                state=state,
                duration_days=duration_days,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
            )
        elif template == "ao_mbr_asm2d":
            result = _run_ao_mbr_asm2d(
                state=state,
                duration_days=duration_days,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
            )
        elif template == "a2o_mbr_asm2d":
            result = _run_a2o_mbr_asm2d(
                state=state,
                duration_days=duration_days,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
            )
        else:
            result = {"error": f"Unknown template: {template}. Available: anaerobic_cstr_madm1, mle_mbr_asm2d, ao_mbr_asm2d, a2o_mbr_asm2d"}

        # Output results
        if json_out:
            print(json.dumps(result, indent=2, default=str))
        else:
            _display_simulation_result(result)

        # Save results
        with open(output_dir / "simulation_results.json", "w") as f:
            json.dump(result, f, indent=2, default=str)

        # Generate report if requested
        if report:
            from reports.qmd_builder import build_report
            report_path = output_dir / "report.qmd"
            build_report(result, output_path=report_path)
            if not json_out:
                console.print(f"[green]Report saved: {report_path}[/green]")

            # Copy CSS file to output directory
            css_src = Path(__file__).parent / "reports" / "templates" / "report.css"
            if css_src.exists():
                import shutil
                shutil.copy(css_src, output_dir / "report.css")

            # Report diagram status
            flowsheet = result.get("flowsheet", {})
            if flowsheet and flowsheet.get("diagram_path"):
                if not json_out:
                    console.print(f"[green]Flowsheet diagram: {flowsheet['diagram_path']}[/green]")
            elif not json_out:
                console.print("[yellow]Note: Flowsheet diagram not generated (graphviz may not be installed)[/yellow]")

    except Exception as e:
        _error_exit(str(e), json_out)


def _run_anaerobic_cstr_madm1(
    state: PlantState,
    duration_days: float,
    timestep_hours: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
) -> dict:
    """Run mADM1 anaerobic CSTR simulation."""
    from templates.anaerobic.cstr import build_and_run

    # Convert PlantState to dict for template
    influent_state = {
        "flow_m3_d": state.flow_m3_d,
        "temperature_K": state.temperature_K,
        "concentrations": state.concentrations,
    }

    # Run simulation
    result = build_and_run(
        influent_state=influent_state,
        reactor_config=reactor_config if reactor_config else None,
        kinetic_params=parameters if parameters else None,
        duration_days=duration_days,
        timestep_hours=timestep_hours,
        output_dir=output_dir,
    )

    return result


def _run_mle_mbr_asm2d(
    state: PlantState,
    duration_days: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
) -> dict:
    """Run MLE-MBR simulation with ASM2d."""
    from templates.aerobic.mle_mbr import build_and_run

    # Convert PlantState to dict for template
    influent_state = {
        "flow_m3_d": state.flow_m3_d,
        "temperature_K": state.temperature_K,
        "concentrations": state.concentrations,
    }

    # Run simulation
    result = build_and_run(
        influent_state=influent_state,
        reactor_config=reactor_config if reactor_config else None,
        kinetic_params=parameters if parameters else None,
        duration_days=duration_days,
        output_dir=output_dir,
    )

    return result


def _run_ao_mbr_asm2d(
    state: PlantState,
    duration_days: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
) -> dict:
    """Run A/O-MBR simulation with ASM2d."""
    from templates.aerobic.ao_mbr import build_and_run

    # Convert PlantState to dict for template
    influent_state = {
        "flow_m3_d": state.flow_m3_d,
        "temperature_K": state.temperature_K,
        "concentrations": state.concentrations,
    }

    # Run simulation
    result = build_and_run(
        influent_state=influent_state,
        reactor_config=reactor_config if reactor_config else None,
        kinetic_params=parameters if parameters else None,
        duration_days=duration_days,
        output_dir=output_dir,
    )

    return result


def _run_a2o_mbr_asm2d(
    state: PlantState,
    duration_days: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
) -> dict:
    """Run A2O-MBR simulation with ASM2d (EBPR)."""
    from templates.aerobic.a2o_mbr import build_and_run

    # Convert PlantState to dict for template
    influent_state = {
        "flow_m3_d": state.flow_m3_d,
        "temperature_K": state.temperature_K,
        "concentrations": state.concentrations,
    }

    # Run simulation
    result = build_and_run(
        influent_state=influent_state,
        reactor_config=reactor_config if reactor_config else None,
        kinetic_params=parameters if parameters else None,
        duration_days=duration_days,
        output_dir=output_dir,
    )

    return result


# =============================================================================
# convert command
# =============================================================================
@app.command()
def convert(
    input: Path = typer.Option(..., "--input", "-i", help="Input PlantState JSON file"),
    output: Path = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    from_model: str = typer.Option(..., "--from-model", "-f", help="Source model type"),
    to_model: str = typer.Option(..., "--to-model", "-t", help="Target model type"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Convert PlantState between model types using QSDsan Junction units.

    Example:
        qsdsan-engine convert -i was.json -f ASM2d -t mADM1 -o digester_feed.json
    """
    try:
        # Load input state
        if not input.exists():
            _error_exit(f"Input file not found: {input}", json_out)

        with open(input) as f:
            state_data = json.load(f)

        # Validate model types
        from_mt = ModelType(from_model)
        to_mt = ModelType(to_model)

        if from_mt == to_mt:
            result = {
                "status": "no_conversion_needed",
                "message": f"Source and target are both {from_model}",
                "output_state": state_data,
            }
        else:
            # Use Junction-based conversion from core/converters.py
            from core.converters import convert_state
            from core.plant_state import PlantState

            input_ps = PlantState.from_dict(state_data)
            output_ps, metadata = convert_state(input_ps, to_mt)

            result = {
                "status": "completed",
                "message": f"Converted {from_model} → {to_model}",
                "output_state": output_ps.to_dict(),
                "metadata": metadata,
            }

        # Output
        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Conversion: {from_model} → {to_model}[/bold]")
            console.print(f"Status: {result.get('status', 'unknown')}")
            console.print(f"Message: {result.get('message', '')}")

        # Save to output file if specified
        if output and "output_state" in result:
            with open(output, "w") as f:
                json.dump(result["output_state"], f, indent=2)
            console.print(f"[green]Saved to: {output}[/green]")

    except Exception as e:
        _error_exit(str(e), json_out)


# =============================================================================
# validate command
# =============================================================================
@app.command()
def validate(
    state: Path = typer.Option(..., "--state", "-s", help="PlantState JSON file to validate"),
    model: str = typer.Option(..., "--model", "-m", help="Target model type"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Validate PlantState against model requirements.

    Checks:
    - Required components present
    - Flow is positive
    - No negative concentrations

    Example:
        qsdsan-engine validate -s influent.json -m mADM1 --json-out
    """
    try:
        # Load state
        if not state.exists():
            _error_exit(f"State file not found: {state}", json_out)

        with open(state) as f:
            state_data = json.load(f)
        plant_state = PlantState.from_dict(state_data)

        # Validate
        mt = ModelType(model)
        model_info = get_model_info(mt)

        errors = []
        warnings = []

        # Check components
        provided = set(plant_state.concentrations.keys())
        missing, extra = validate_components(mt, provided)

        if missing:
            errors.append(f"Missing {len(missing)} required components")
        if extra:
            warnings.append(f"{len(extra)} extra components (will be ignored)")

        # Basic checks
        if plant_state.flow_m3_d <= 0:
            errors.append(f"Invalid flow: {plant_state.flow_m3_d}")

        negative = [k for k, v in plant_state.concentrations.items() if v < 0]
        if negative:
            errors.append(f"Negative concentrations: {negative[:5]}")

        result = {
            "is_valid": len(errors) == 0,
            "model_type": model,
            "errors": errors,
            "warnings": warnings,
            "missing_components": missing[:10] if missing else [],
            "extra_components": extra[:5] if extra else [],
            "n_components_provided": len(provided),
            "n_components_required": model_info.get("n_components"),
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            _display_validation_result(result)

    except Exception as e:
        _error_exit(str(e), json_out)


# =============================================================================
# templates command
# =============================================================================
@app.command()
def templates(
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    List available flowsheet templates.

    Example:
        qsdsan-engine templates --json-out
    """
    # Use shared template registry (same as MCP server)
    result = get_all_templates()
    result["models"] = list_available_models()

    if json_out:
        print(json.dumps(result, indent=2))
    else:
        console.print("\n[bold]Anaerobic Templates[/bold]")
        for t in result["anaerobic"]:
            status_color = "green" if t["status"] == "available" else "yellow"
            console.print(f"  {t['name']}: {t['description']} [{status_color}]{t['status']}[/{status_color}]")

        console.print("\n[bold]Aerobic Templates[/bold]")
        for t in result["aerobic"]:
            status_color = "green" if t["status"] == "available" else "yellow"
            console.print(f"  {t['name']}: {t['description']} [{status_color}]{t['status']}[/{status_color}]")

        console.print("\n[bold]Supported Models[/bold]")
        for m in result["models"]:
            console.print(f"  {m['model_type']}: {m['description']}")


# =============================================================================
# models command
# =============================================================================
@app.command()
def models(
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    List available biological process models.

    Example:
        qsdsan-engine models
    """
    result = list_available_models()

    if json_out:
        print(json.dumps(result, indent=2))
    else:
        table = Table(title="Available Models")
        table.add_column("Model", style="cyan")
        table.add_column("Components", justify="right")
        table.add_column("Temp (K)", justify="right")
        table.add_column("Description")

        for m in result:
            table.add_row(
                m["model_type"],
                str(m["n_components"] or "N/A"),
                str(m["default_temperature_K"]),
                m["description"][:40],
            )

        console.print(table)


# =============================================================================
# flowsheet command group (Phase 2)
# =============================================================================
flowsheet_app = typer.Typer(
    name="flowsheet",
    help="Dynamic flowsheet construction tools",
    add_completion=False,
)
app.add_typer(flowsheet_app, name="flowsheet")

# Session manager for flowsheet operations
session_manager = FlowsheetSessionManager()


@flowsheet_app.command("new")
def flowsheet_new(
    model: str = typer.Option("ASM2d", "--model", "-m", help="Primary process model (ASM2d, mADM1)"),
    session_id: Optional[str] = typer.Option(None, "--id", help="Custom session ID (auto-generated if not provided)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Create a new flowsheet construction session.

    Example:
        qsdsan-engine flowsheet new --model ASM2d
        qsdsan-engine flowsheet new --model mADM1 --id my_digester
    """
    try:
        session = session_manager.create_session(
            model_type=model,
            session_id=session_id,
        )

        result = {
            "session_id": session.session_id,
            "model_type": session.primary_model_type,
            "status": session.status,
            "available_units": [
                u["unit_type"] for u in list_available_units(model_type=model)
            ],
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold green]Created flowsheet session: {session.session_id}[/bold green]")
            console.print(f"Model: {session.primary_model_type}")
            console.print(f"Status: {session.status}")
            console.print(f"\nNext steps:")
            console.print(f"  1. Add streams: flowsheet add-stream --session {session.session_id} ...")
            console.print(f"  2. Add units: flowsheet add-unit --session {session.session_id} ...")
            console.print(f"  3. Connect: flowsheet connect --session {session.session_id} ...")
            console.print(f"  4. Build: flowsheet build --session {session.session_id}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("add-stream")
def flowsheet_add_stream(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    stream_id: str = typer.Option(..., "--id", help="Stream identifier (e.g., 'influent', 'RAS')"),
    flow: float = typer.Option(..., "--flow", "-f", help="Flow rate in m³/day"),
    concentrations: str = typer.Option(..., "--concentrations", "-c", help="Component concentrations as JSON dict (mg/L)"),
    temperature: float = typer.Option(293.15, "--temperature", "-t", help="Temperature in K"),
    stream_type: str = typer.Option("influent", "--type", help="Stream type: influent, recycle, intermediate"),
    model_type: Optional[str] = typer.Option(None, "--model", "-m", help="Model type (default: session model)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Add a stream to the flowsheet session.

    Example:
        qsdsan-engine flowsheet add-stream --session abc123 --id influent \\
            --flow 4000 --concentrations '{"S_F": 75, "S_A": 20, "S_NH4": 17}'
    """
    try:
        conc_dict = json.loads(concentrations)

        config = StreamConfig(
            stream_id=stream_id,
            flow_m3_d=flow,
            temperature_K=temperature,
            concentrations=conc_dict,
            stream_type=stream_type,
            model_type=model_type,
        )

        result = session_manager.add_stream(session_id, config)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Added stream '{stream_id}' to session {session_id}[/green]")
            console.print(f"  Flow: {flow} m³/day")
            console.print(f"  Temperature: {temperature} K")
            console.print(f"  Components: {len(conc_dict)}")
            console.print(f"  Type: {stream_type}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("add-unit")
def flowsheet_add_unit(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    unit_type: str = typer.Option(..., "--type", "-t", help="Unit type (e.g., 'CSTR', 'CompletelyMixedMBR')"),
    unit_id: str = typer.Option(..., "--id", help="Unit identifier (e.g., 'A1', 'O1', 'MBR')"),
    params: str = typer.Option(..., "--params", "-p", help="Unit parameters as JSON dict"),
    inputs: str = typer.Option(..., "--inputs", "-i", help="Input sources as JSON list (stream IDs or pipe notation)"),
    outputs: Optional[str] = typer.Option(None, "--outputs", "-o", help="Output stream names as JSON list"),
    model_type: Optional[str] = typer.Option(None, "--model", "-m", help="Model type (default: session model)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Add a unit to the flowsheet session.

    Example:
        qsdsan-engine flowsheet add-unit --session abc123 --type CSTR --id A1 \\
            --params '{"V_max": 1000, "aeration": null}' --inputs '["influent", "RAS"]'
    """
    try:
        params_dict = json.loads(params)
        inputs_list = json.loads(inputs)
        outputs_list = json.loads(outputs) if outputs else None

        # Validate unit type
        spec = get_unit_spec(unit_type)

        # Validate parameters
        errors, warnings = validate_unit_params(unit_type, params_dict)
        if errors:
            _error_exit(f"Parameter validation failed: {errors}", json_out)

        # Load session to check model compatibility (same as MCP create_unit)
        session = session_manager.get_session(session_id)
        effective_model = model_type or session.primary_model_type

        # Validate model compatibility
        is_compatible, compat_error = validate_model_compatibility(unit_type, effective_model)
        if not is_compatible:
            _error_exit(compat_error, json_out)

        # Junction units now supported via core/junction_units.py custom classes
        # which work with our 63-component ModifiedADM1 model

        config = UnitConfig(
            unit_id=unit_id,
            unit_type=unit_type,
            params=params_dict,
            inputs=inputs_list,
            outputs=outputs_list,
            model_type=model_type,
        )

        result = session_manager.add_unit(session_id, config)

        # Add warnings to result
        if warnings:
            result["warnings"] = warnings

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Added unit '{unit_id}' ({unit_type}) to session {session_id}[/green]")
            console.print(f"  Category: {spec.category.value}")
            console.print(f"  Inputs: {inputs_list}")
            if outputs_list:
                console.print(f"  Outputs: {outputs_list}")
            if warnings:
                for w in warnings:
                    console.print(f"  [yellow]Warning: {w}[/yellow]")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("connect")
def flowsheet_connect(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    connections: str = typer.Option(..., "--connections", "-c", help="Connections as JSON list of {from, to, stream_id?}"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Add deferred connections between units (for recycles).

    Use this after creating units to wire recycle streams that couldn't be
    specified during unit creation.

    Example:
        qsdsan-engine flowsheet connect --session abc123 \\
            --connections '[{"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}]'
    """
    try:
        conn_list = json.loads(connections)
        results = []

        for conn in conn_list:
            config = ConnectionConfig(
                from_port=conn["from"],
                to_port=conn["to"],
                stream_id=conn.get("stream_id"),
            )
            result = session_manager.add_connection(session_id, config)
            results.append(result)

        output = {
            "session_id": session_id,
            "connections_added": len(results),
            "connections": results,
        }

        if json_out:
            print(json.dumps(output, indent=2))
        else:
            console.print(f"[green]Added {len(results)} connection(s) to session {session_id}[/green]")
            for r in results:
                stream_info = f" ({r.get('stream_id')})" if r.get('stream_id') else ""
                console.print(f"  {r['from']} → {r['to']}{stream_info}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("build")
def flowsheet_build(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    system_id: Optional[str] = typer.Option(None, "--system-id", help="System identifier (default: session_id)"),
    unit_order: Optional[str] = typer.Option(None, "--unit-order", help="Unit execution order as JSON list (auto-inferred if not provided)"),
    recycles: Optional[str] = typer.Option(None, "--recycles", "-r", help="Recycle stream IDs as JSON list"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Compile flowsheet session into a QSDsan System.

    This validates, compiles, and builds the QSDsan System objects.
    Use 'flowsheet simulate' to run the simulation.

    Example:
        qsdsan-engine flowsheet build --session abc123 --recycles '["RAS"]'
    """
    try:
        from utils.topo_sort import validate_flowsheet_connectivity
        from utils.flowsheet_builder import compile_system

        session = session_manager.get_session(session_id)
        actual_system_id = system_id or session_id

        # Parse optional parameters
        manual_order = json.loads(unit_order) if unit_order else None
        recycle_ids = set(json.loads(recycles)) if recycles else set()

        # Validate connectivity first
        errors, warnings = validate_flowsheet_connectivity(
            session.units, session.streams, session.connections
        )
        if errors:
            session_manager.update_session_status(session_id, "failed")
            _error_exit(f"Connectivity validation failed: {errors}", json_out)

        # Actually compile the QSDsan System
        try:
            system, build_info = compile_system(
                session=session,
                system_id=actual_system_id,
                unit_order=manual_order,
                recycle_stream_ids=recycle_ids,
            )
        except Exception as compile_error:
            session_manager.update_session_status(session_id, "failed")
            _error_exit(f"System compilation failed: {compile_error}", json_out)

        # Update session status
        session_manager.update_session_status(session_id, "compiled")

        # Save build result and config
        session_dir = session_manager._get_session_dir(session_id)

        # Save build_config.json (used by simulate to restore build parameters)
        build_config = {
            "system_id": actual_system_id,
            "unit_order": build_info.unit_order,
            "recycle_streams": list(recycle_ids),
        }
        with open(session_dir / "build_config.json", "w") as f:
            json.dump(build_config, f, indent=2)

        # Save system_result.json (detailed build info)
        build_result = {
            "system_id": build_info.system_id,
            "unit_order": build_info.unit_order,
            "recycle_streams": list(recycle_ids),
            "recycle_edges": build_info.recycle_edges,
            "streams_created": build_info.streams_created,
            "units_created": build_info.units_created,
            "build_warnings": build_info.warnings,
        }
        with open(session_dir / "system_result.json", "w") as f:
            json.dump(build_result, f, indent=2)

        result = {
            "session_id": session_id,
            "system_id": actual_system_id,
            "status": "compiled",
            "model_types": list(session.model_types),
            "unit_order": build_info.unit_order,
            "recycle_edges": build_info.recycle_edges,
            "streams_created": build_info.streams_created,
            "units_created": build_info.units_created,
            "n_units": len(session.units),
            "n_streams": len(session.streams),
            "n_connections": len(session.connections),
            "warnings": warnings + build_info.warnings,
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold green]Compiled flowsheet session {session_id}[/bold green]")
            console.print(f"System ID: {result['system_id']}")
            console.print(f"Models: {result['model_types']}")
            console.print(f"Unit order: {' → '.join(result['unit_order'])}")
            console.print(f"Streams created: {len(result['streams_created'])}")
            console.print(f"Units created: {len(result['units_created'])}")
            if result['recycle_edges']:
                console.print(f"Recycle edges: {result['recycle_edges']}")
            console.print(f"\nReady for simulation:")
            console.print(f"  flowsheet simulate --session {session_id} --duration 15")

            if result['warnings']:
                console.print(f"\n[yellow]Warnings:[/yellow]")
                for w in result['warnings']:
                    console.print(f"  - {w}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("simulate")
def flowsheet_simulate(
    session_id: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID (mutually exclusive with --system-id)"),
    system_id_opt: Optional[str] = typer.Option(None, "--system-id", help="System ID from build_system (mutually exclusive with --session)"),
    duration: float = typer.Option(1.0, "--duration", "-d", help="Simulation duration in days"),
    duration_days: float = typer.Option(None, "--duration-days", help="Alias for --duration (MCP compatibility)"),
    timestep: float = typer.Option(1.0, "--timestep", help="Output timestep in hours"),
    timestep_hours: float = typer.Option(None, "--timestep-hours", help="Alias for --timestep (MCP compatibility)"),
    method: str = typer.Option("RK23", "--method", help="ODE solver method (RK23, RK45, BDF)"),
    t_eval: Optional[str] = typer.Option(None, "--t-eval", help="Custom evaluation times as JSON list (days)"),
    track: Optional[str] = typer.Option(None, "--track", help="Stream IDs to track dynamically (JSON list)"),
    effluent_streams: Optional[str] = typer.Option(None, "--effluent-streams", help="Stream IDs for effluent analysis (JSON list)"),
    biogas_streams: Optional[str] = typer.Option(None, "--biogas-streams", help="Stream IDs for biogas analysis (JSON list)"),
    output_dir: Path = typer.Option(None, "--output-dir", "-o", help="Output directory"),
    report: bool = typer.Option(False, "--report", "-r", help="Generate Quarto report"),
    diagram: bool = typer.Option(False, "--diagram", help="Generate flowsheet diagram"),
    include_components: bool = typer.Option(False, "--include-components", help="Include full component breakdown"),
    export_state_to: Optional[Path] = typer.Option(None, "--export-state-to", help="Export final effluent state as PlantState JSON"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Simulate a compiled flowsheet session.

    Example:
        qsdsan-engine flowsheet simulate --session abc123 --duration 15 --report
        qsdsan-engine flowsheet simulate --system-id custom_mle --duration 15 --report
    """
    # Validate arguments: exactly one of session_id or system_id must be provided
    if session_id and system_id_opt:
        _error_exit("Provide either --session or --system-id, not both", json_out)
    if not session_id and not system_id_opt:
        _error_exit("Must provide either --session or --system-id", json_out)

    # Handle MCP compatibility aliases
    if duration_days is not None:
        duration = duration_days
    if timestep_hours is not None:
        timestep = timestep_hours

    # Parse JSON list arguments
    effluent_stream_ids = json.loads(effluent_streams) if effluent_streams else None
    biogas_stream_ids = json.loads(biogas_streams) if biogas_streams else None
    custom_t_eval = json.loads(t_eval) if t_eval else None
    track_stream_ids = json.loads(track) if track else None

    try:
        from utils.flowsheet_builder import compile_system, simulate_compiled_system

        # If system_id is provided, find the session with that system_id
        if system_id_opt:
            found_session_id = None
            sessions_dir = Path("jobs") / "flowsheets"
            if sessions_dir.exists():
                for sess_dir in sessions_dir.iterdir():
                    if sess_dir.is_dir():
                        build_config_path = sess_dir / "build_config.json"
                        if build_config_path.exists():
                            try:
                                with open(build_config_path) as f:
                                    build_config = json.load(f)
                                if build_config.get("system_id") == system_id_opt:
                                    found_session_id = sess_dir.name
                                    break
                            except Exception:
                                continue
            if not found_session_id:
                _error_exit(
                    f"No compiled session found with system_id '{system_id_opt}'. "
                    "Run 'flowsheet build' first.",
                    json_out
                )
            session_id = found_session_id

        session = session_manager.get_session(session_id)

        if session.status != "compiled":
            _error_exit(
                f"Session '{session_id}' is not compiled (status: {session.status}). "
                "Run 'flowsheet build' first.",
                json_out
            )

        # Set up output directory
        if output_dir is None:
            output_dir = Path("output") / session_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load build config to get recycle streams
        session_dir = session_manager._get_session_dir(session_id)
        build_config_path = session_dir / "build_config.json"
        if build_config_path.exists():
            with open(build_config_path) as f:
                build_config = json.load(f)
            recycle_stream_ids = set(build_config.get("recycle_streams", []))
            unit_order = build_config.get("unit_order")
            system_id = build_config.get("system_id", session_id)
        else:
            recycle_stream_ids = set()
            unit_order = None
            system_id = session_id

        # Compile system
        console.print(f"[bold]Compiling flowsheet session {session_id}...[/bold]") if not json_out else None
        system, build_info = compile_system(
            session,
            system_id=system_id,
            unit_order=unit_order,
            recycle_stream_ids=recycle_stream_ids,
        )

        # Run simulation
        console.print(f"[bold]Simulating for {duration} days (method: {method})...[/bold]") if not json_out else None
        sim_results = simulate_compiled_system(
            system,
            duration_days=duration,
            timestep_hours=timestep,
            method=method,
            t_eval=custom_t_eval,
            track=track_stream_ids,
            output_dir=output_dir,
            model_type=session.primary_model_type,
            effluent_stream_ids=effluent_stream_ids,
            biogas_stream_ids=biogas_stream_ids,
            export_state_to=export_state_to,
        )

        # Generate diagram if requested
        if diagram:
            try:
                from utils.diagram import save_system_diagram
                diagram_path = save_system_diagram(system, output_dir / "flowsheet")
                sim_results["diagram_path"] = str(diagram_path)
            except Exception as e:
                sim_results["diagram_warning"] = f"Failed to generate diagram: {e}"

        # Generate report if requested
        if report:
            try:
                from reports.qmd_builder import generate_report
                report_path = generate_report(
                    session_id=session_id,
                    model_type=session.primary_model_type,
                    results=sim_results,
                    output_dir=output_dir,
                )
                sim_results["report_path"] = str(report_path)
            except Exception as e:
                sim_results["report_warning"] = f"Failed to generate report: {e}"

        # Build result
        result = {
            "session_id": session_id,
            "system_id": system_id,
            "status": "completed",
            "config": {
                "duration_days": duration,
                "timestep_hours": timestep,
                "method": method,
                "output_dir": str(output_dir),
            },
            "build_info": {
                "unit_order": build_info.unit_order,
                "streams_created": build_info.streams_created,
                "units_created": build_info.units_created,
                "warnings": build_info.warnings,
            },
            "simulation_results": sim_results,
        }

        # Save results
        with open(output_dir / "results.json", "w") as f:
            json.dump(result, f, indent=2, default=str)

        if json_out:
            print(json.dumps(result, indent=2, default=str))
        else:
            console.print(f"\n[bold green]Simulation completed![/bold green]")
            console.print(f"System: {system_id}")
            console.print(f"Units: {' → '.join(build_info.unit_order)}")
            console.print(f"Duration: {duration} days")
            console.print(f"Output: {output_dir}")

            if "effluent_quality" in sim_results:
                console.print("\n[bold]Effluent Quality:[/bold]")
                eq = sim_results["effluent_quality"]
                for key, val in eq.items():
                    if isinstance(val, (int, float)):
                        console.print(f"  {key}: {val:.2f}")

            if "removal_efficiency" in sim_results:
                console.print("\n[bold]Removal Efficiency:[/bold]")
                re = sim_results["removal_efficiency"]
                for key, val in re.items():
                    if isinstance(val, (int, float)):
                        console.print(f"  {key}: {val:.1f}%")

            if build_info.warnings:
                console.print(f"\n[yellow]Warnings:[/yellow]")
                for w in build_info.warnings:
                    console.print(f"  - {w}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("units")
def flowsheet_units(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter by compatible model (ASM2d, mADM1)"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    List available SanUnit types with parameters.

    Example:
        qsdsan-engine flowsheet units --model ASM2d
        qsdsan-engine flowsheet units --category reactor
    """
    try:
        # Use list_available_units with optional model and category filters
        units = list_available_units(model_type=model, category=category)

        if json_out:
            print(json.dumps(units, indent=2))
        else:
            table = Table(title=f"Available Units{' (' + model + ')' if model else ''}")
            table.add_column("Type", style="cyan")
            table.add_column("Category")
            table.add_column("Models")
            table.add_column("Required Params")
            table.add_column("Description")

            for u in units:
                models = ", ".join(u["compatible_models"]) if u["compatible_models"] else "any"
                req_params = ", ".join(u["required_params"]) if u["required_params"] else "-"
                table.add_row(
                    u["unit_type"],
                    u["category"],
                    models[:20],
                    req_params[:20],
                    u["description"][:30] + "..." if len(u["description"]) > 30 else u["description"],
                )

            console.print(table)
            console.print(f"\nTotal: {len(units)} units")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("show")
def flowsheet_show(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Show details of a flowsheet session.

    Example:
        qsdsan-engine flowsheet show --session abc123
    """
    try:
        summary = session_manager.get_session_summary(session_id)

        if json_out:
            print(json.dumps(summary, indent=2))
        else:
            console.print(f"\n[bold]Flowsheet Session: {summary['session_id']}[/bold]")
            console.print(f"Status: {summary['status']}")
            console.print(f"Primary Model: {summary['primary_model_type']}")
            console.print(f"Models Used: {summary['model_types']}")
            console.print(f"Created: {summary['created_at']}")
            console.print(f"Updated: {summary['updated_at']}")

            console.print(f"\n[bold]Streams ({len(summary['streams'])}):[/bold]")
            for stream_id in summary['streams']:
                console.print(f"  - {stream_id}")

            console.print(f"\n[bold]Units ({len(summary['units'])}):[/bold]")
            for unit_id, info in summary['units'].items():
                inputs_str = ", ".join(info['inputs']) if info['inputs'] else "none"
                console.print(f"  - {unit_id} ({info['type']}): inputs=[{inputs_str}]")

            if summary['connections']:
                console.print(f"\n[bold]Connections ({len(summary['connections'])}):[/bold]")
                for conn in summary['connections']:
                    console.print(f"  - {conn['from']} → {conn['to']}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("list")
def flowsheet_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status (building, compiled, failed)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    List all flowsheet sessions.

    Example:
        qsdsan-engine flowsheet list
        qsdsan-engine flowsheet list --status compiled
    """
    try:
        sessions = session_manager.list_sessions(status_filter=status)

        if json_out:
            print(json.dumps(sessions, indent=2))
        else:
            if not sessions:
                console.print("[yellow]No flowsheet sessions found.[/yellow]")
                console.print("Create one with: flowsheet new --model ASM2d")
                return

            table = Table(title="Flowsheet Sessions")
            table.add_column("Session ID", style="cyan")
            table.add_column("Model")
            table.add_column("Units")
            table.add_column("Streams")
            table.add_column("Status")
            table.add_column("Updated")

            for s in sessions:
                status_color = {
                    "building": "yellow",
                    "compiled": "green",
                    "failed": "red",
                }.get(s["status"], "white")

                table.add_row(
                    s["session_id"],
                    s["primary_model_type"],
                    str(s["n_units"]),
                    str(s["n_streams"]),
                    f"[{status_color}]{s['status']}[/{status_color}]",
                    s["updated_at"][:19],
                )

            console.print(table)
            console.print(f"\nTotal: {len(sessions)} session(s)")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("delete")
def flowsheet_delete(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Delete a flowsheet session.

    Example:
        qsdsan-engine flowsheet delete --session abc123 --force
    """
    try:
        if not force and not json_out:
            confirm = typer.confirm(f"Delete session '{session_id}'?")
            if not confirm:
                console.print("[yellow]Cancelled[/yellow]")
                return

        deleted = session_manager.delete_session(session_id)

        result = {
            "session_id": session_id,
            "deleted": deleted,
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            if deleted:
                console.print(f"[green]Deleted session '{session_id}'[/green]")
            else:
                console.print(f"[yellow]Session '{session_id}' not found[/yellow]")

    except Exception as e:
        _error_exit(str(e), json_out)


# =============================================================================
# Helper functions
# =============================================================================
def _error_exit(message: str, json_out: bool):
    """Exit with error message."""
    if json_out:
        print(json.dumps({"error": message}))
    else:
        console.print(f"[red]Error: {message}[/red]")
    sys.exit(1)


def _display_simulation_result(result: dict):
    """Display simulation result in human-readable format."""
    console.print("\n[bold]Simulation Result[/bold]")
    console.print(f"Status: {result.get('status', 'unknown')}")
    console.print(f"Template: {result.get('template', 'unknown')}")
    if "message" in result:
        console.print(f"Message: {result['message']}")
    if "error" in result:
        console.print(f"[red]Error: {result['error']}[/red]")


def _display_validation_result(result: dict):
    """Display validation result in human-readable format."""
    valid = result.get("is_valid", False)
    status_color = "green" if valid else "red"
    status_text = "VALID" if valid else "INVALID"

    console.print(f"\n[bold]Validation: [{status_color}]{status_text}[/{status_color}][/bold]")
    console.print(f"Model: {result.get('model_type')}")
    console.print(f"Components: {result.get('n_components_provided')} provided / {result.get('n_components_required')} required")

    if result.get("errors"):
        console.print("\n[red]Errors:[/red]")
        for e in result["errors"]:
            console.print(f"  - {e}")

    if result.get("warnings"):
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in result["warnings"]:
            console.print(f"  - {w}")

    if result.get("missing_components"):
        console.print(f"\n[red]Missing components:[/red] {result['missing_components']}")


# =============================================================================
# Entry point
# =============================================================================
if __name__ == "__main__":
    app()
