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
    timestep_hours: Optional[float] = typer.Option(None, "--timestep-hours", help="Output timestep in hours (aerobic templates only)"),
    reactor_config: Optional[str] = typer.Option(None, "--reactor-config", help="Reactor config JSON"),
    parameters: Optional[str] = typer.Option(None, "--parameters", "-p", help="Kinetic parameter overrides as JSON (aerobic templates only). Example: '{\"mu_H\": 6.0}'"),
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
                timestep_hours=timestep_hours,
            )
        elif template == "ao_mbr_asm2d":
            result = _run_ao_mbr_asm2d(
                state=state,
                duration_days=duration_days,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
                timestep_hours=timestep_hours,
            )
        elif template == "a2o_mbr_asm2d":
            result = _run_a2o_mbr_asm2d(
                state=state,
                duration_days=duration_days,
                reactor_config=reactor_cfg,
                parameters=params,
                output_dir=output_dir,
                timestep_hours=timestep_hours,
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
    timestep_hours: Optional[float],
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
    timestep_hours: Optional[float] = None,
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
        timestep_hours=timestep_hours,
    )

    return result


def _run_ao_mbr_asm2d(
    state: PlantState,
    duration_days: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
    timestep_hours: Optional[float] = None,
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
        timestep_hours=timestep_hours,
    )

    return result


def _run_a2o_mbr_asm2d(
    state: PlantState,
    duration_days: float,
    reactor_config: dict,
    parameters: dict,
    output_dir: Path,
    timestep_hours: Optional[float] = None,
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
        timestep_hours=timestep_hours,
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
                "message": f"Converted {from_model} -> {to_model}",
                "output_state": output_ps.to_dict(),
                "metadata": metadata,
            }

        # Output
        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Conversion: {from_model} -> {to_model}[/bold]")
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
    concentrations: str = typer.Option(..., "--concentrations", "-c", help="Component concentrations as JSON dict"),
    temperature: float = typer.Option(293.15, "--temperature", "-t", help="Temperature in K"),
    concentration_units: str = typer.Option("mg/L", "--conc-units", "-u", help="Concentration units: mg/L (default) or kg/m3"),
    stream_type: str = typer.Option("influent", "--type", help="Stream type: influent, recycle, intermediate"),
    model_type: Optional[str] = typer.Option(None, "--model", "-m", help="Model type (default: session model)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Add a stream to the flowsheet session.

    Example:
        qsdsan-engine flowsheet add-stream --session abc123 --id influent \\
            --flow 4000 --concentrations '{"S_F": 75, "S_A": 20, "S_NH4": 17}'
        qsdsan-engine flowsheet add-stream --session abc123 --id influent \\
            --flow 4000 --concentrations '{"S_F": 0.075}' --conc-units kg/m3
    """
    try:
        # Validate concentration_units
        if concentration_units not in ("mg/L", "kg/m3"):
            _error_exit(f"Invalid concentration_units '{concentration_units}'. Must be 'mg/L' or 'kg/m3'.", json_out)

        conc_dict = json.loads(concentrations)

        config = StreamConfig(
            stream_id=stream_id,
            flow_m3_d=flow,
            temperature_K=temperature,
            concentrations=conc_dict,
            concentration_units=concentration_units,
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
            console.print(f"  Components: {len(conc_dict)} ({concentration_units})")
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
    connections: str = typer.Option(..., "--connections", "-c", help="Connections as JSON list of {from, to?, stream_id?}"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Add deferred connections between units (for recycles).

    Use this after creating units to wire recycle streams that couldn't be
    specified during unit creation.

    Supports both standard and direct notation:
    - Standard: {"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}
    - Direct:   {"from": "SP-0-1-A1"} or {"from": "U1-U2"}

    Example:
        qsdsan-engine flowsheet connect --session abc123 \\
            --connections '[{"from": "SP-0", "to": "A1-1", "stream_id": "RAS"}]'
        qsdsan-engine flowsheet connect --session abc123 \\
            --connections '[{"from": "SP-0-1-A1"}]'
    """
    from utils.pipe_parser import parse_port_notation

    try:
        conn_list = json.loads(connections)
        results = []

        for conn in conn_list:
            if not isinstance(conn, dict) or "from" not in conn:
                results.append({"error": f"Invalid connection format (missing 'from'): {conn}"})
                continue

            from_port = conn["from"]

            # Check if direct notation (U1-U2 or U1-0-1-U2) - target embedded in from
            try:
                from_ref = parse_port_notation(from_port)
                if from_ref.port_type == "direct":
                    # Direct notation: to_port is optional/ignored
                    to_port = conn.get("to")  # May be None
                else:
                    # Standard notation: requires to field
                    if "to" not in conn:
                        results.append({"error": f"Standard notation requires 'to' field: {conn}"})
                        continue
                    to_port = conn["to"]
            except ValueError:
                # If parsing fails, require to field for backward compatibility
                if "to" not in conn:
                    results.append({"error": f"Invalid connection format (missing 'to'): {conn}"})
                    continue
                to_port = conn["to"]

            config = ConnectionConfig(
                from_port=from_port,
                to_port=to_port,
                stream_id=conn.get("stream_id"),
            )
            result = session_manager.add_connection(session_id, config)
            results.append(result)

        successful = [r for r in results if "error" not in r]
        errors = [r for r in results if "error" in r]

        output = {
            "session_id": session_id,
            "connections_added": len(successful),
            "results": results,
        }

        if json_out:
            print(json.dumps(output, indent=2))
        else:
            console.print(f"[green]Added {len(successful)} connection(s) to session {session_id}[/green]")
            for r in successful:
                stream_info = f" ({r.get('stream_id')})" if r.get('stream_id') else ""
                console.print(f"  {r['from']} -> {r['to']}{stream_info}")
            for r in errors:
                console.print(f"  [red]{r['error']}[/red]")

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
            console.print(f"Unit order: {' -> '.join(result['unit_order'])}")
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
            console.print(f"Units: {' -> '.join(build_info.unit_order)}")
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
            for stream_id, sinfo in summary['streams'].items():
                n_comps = len(sinfo.get('concentrations', {}))
                console.print(f"  - {stream_id}: flow={sinfo['flow_m3_d']} m3/d, T={sinfo['temperature_K']} K, {n_comps} components")

            console.print(f"\n[bold]Units ({len(summary['units'])}):[/bold]")
            for unit_id, info in summary['units'].items():
                inputs_str = ", ".join(info['inputs']) if info['inputs'] else "none"
                params_str = ", ".join(f"{k}={v}" for k, v in (info.get('params') or {}).items())
                console.print(f"  - {unit_id} ({info['unit_type']}): inputs=[{inputs_str}]")
                if params_str:
                    console.print(f"      params: {params_str}")

            if summary['connections']:
                console.print(f"\n[bold]Connections ({len(summary['connections'])}):[/bold]")
                for conn in summary['connections']:
                    stream_info = f" ({conn['stream_id']})" if conn.get('stream_id') else ""
                    console.print(f"  - {conn['from']} -> {conn['to']}{stream_info}")

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


@flowsheet_app.command("update-stream")
def flowsheet_update_stream(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    stream_id: str = typer.Option(..., "--id", help="Stream identifier to update"),
    flow: Optional[float] = typer.Option(None, "--flow", "-f", help="New flow rate in m3/day"),
    concentrations: Optional[str] = typer.Option(None, "--concentrations", "-c", help="Concentrations to update/merge as JSON dict"),
    temperature: Optional[float] = typer.Option(None, "--temperature", "-t", help="New temperature in K"),
    stream_type: Optional[str] = typer.Option(None, "--type", help="New stream type"),
    model_type: Optional[str] = typer.Option(None, "--model", "-m", help="New model type"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Update a stream in the flowsheet session (patch-style).

    Only specified fields are updated. Concentrations are merged, not replaced.

    Example:
        qsdsan-engine flowsheet update-stream --session abc123 --id influent \\
            --flow 5000 --concentrations '{"S_F": 100}'
    """
    try:
        updates = {}
        if flow is not None:
            updates["flow_m3_d"] = flow
        if temperature is not None:
            updates["temperature_K"] = temperature
        if stream_type is not None:
            updates["stream_type"] = stream_type
        if model_type is not None:
            updates["model_type"] = model_type
        if concentrations is not None:
            updates["concentrations"] = json.loads(concentrations)

        if not updates:
            _error_exit("No updates provided", json_out)

        result = session_manager.update_stream(session_id, stream_id, updates)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Updated stream '{stream_id}'[/green]")
            console.print(f"  Updated fields: {result['updated_fields']}")
            if result.get("was_compiled"):
                console.print(f"  [yellow]Session reset to 'building' - rebuild required[/yellow]")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("update-unit")
def flowsheet_update_unit(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    unit_id: str = typer.Option(..., "--id", help="Unit identifier to update"),
    params: Optional[str] = typer.Option(None, "--params", "-p", help="Parameters to update/merge as JSON dict"),
    inputs: Optional[str] = typer.Option(None, "--inputs", "-i", help="New inputs as JSON list"),
    outputs: Optional[str] = typer.Option(None, "--outputs", "-o", help="New outputs as JSON list"),
    model_type: Optional[str] = typer.Option(None, "--model", "-m", help="New model type"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Update a unit in the flowsheet session (patch-style).

    Only specified fields are updated. Params are merged, not replaced.

    Example:
        qsdsan-engine flowsheet update-unit --session abc123 --id A1 \\
            --params '{"V_max": 1500}'
    """
    try:
        updates = {}
        if params is not None:
            updates["params"] = json.loads(params)
        if inputs is not None:
            updates["inputs"] = json.loads(inputs)
        if outputs is not None:
            updates["outputs"] = json.loads(outputs)
        if model_type is not None:
            updates["model_type"] = model_type

        if not updates:
            _error_exit("No updates provided", json_out)

        result = session_manager.update_unit(session_id, unit_id, updates)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Updated unit '{unit_id}'[/green]")
            console.print(f"  Updated fields: {result['updated_fields']}")
            if result.get("was_compiled"):
                console.print(f"  [yellow]Session reset to 'building' - rebuild required[/yellow]")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("delete-stream")
def flowsheet_delete_stream(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    stream_id: str = typer.Option(..., "--id", help="Stream identifier to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Delete even if referenced by units"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Delete a stream from the flowsheet session.

    By default fails if units reference this stream. Use --force to delete anyway.

    Example:
        qsdsan-engine flowsheet delete-stream --session abc123 --id RAS --force
    """
    try:
        result = session_manager.delete_stream(session_id, stream_id, force)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Deleted stream '{stream_id}'[/green]")
            if result.get("removed_from_units"):
                console.print(f"  Removed from units: {result['removed_from_units']}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("delete-unit")
def flowsheet_delete_unit(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    unit_id: str = typer.Option(..., "--id", help="Unit identifier to delete"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Delete a unit from the flowsheet session.

    Also removes any connections referencing this unit.

    Example:
        qsdsan-engine flowsheet delete-unit --session abc123 --id SP
    """
    try:
        result = session_manager.delete_unit(session_id, unit_id)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Deleted unit '{unit_id}'[/green]")
            if result.get("removed_connections"):
                console.print(f"  Removed connections: {result['removed_connections']}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("delete-connection")
def flowsheet_delete_connection(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    from_port: str = typer.Option(..., "--from", help="Source port (e.g., 'SP-0')"),
    to_port: Optional[str] = typer.Option(None, "--to", help="Destination port (optional for direct notation)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Delete a specific connection from the flowsheet session.

    Example:
        qsdsan-engine flowsheet delete-connection --session abc123 --from SP-0 --to A1-1
    """
    try:
        result = session_manager.delete_connection(session_id, from_port, to_port)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Deleted connection {from_port} -> {to_port}[/green]")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("clone")
def flowsheet_clone(
    session_id: str = typer.Option(..., "--session", "-s", help="Source session ID to clone"),
    new_session_id: Optional[str] = typer.Option(None, "--new-id", help="New session ID (auto-generated if not provided)"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Clone a flowsheet session for experimentation.

    Creates a copy reset to 'building' status for modifications.

    Example:
        qsdsan-engine flowsheet clone --session abc123 --new-id abc123_v2
    """
    try:
        result = session_manager.clone_session(session_id, new_session_id)

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]Cloned session '{session_id}' to '{result['new_session_id']}'[/green]")
            console.print(f"  Streams: {result['n_streams']}")
            console.print(f"  Units: {result['n_units']}")
            console.print(f"  Connections: {result['n_connections']}")

    except Exception as e:
        _error_exit(str(e), json_out)


# =============================================================================
# Phase 3: Discoverability and Engineering Tools
# =============================================================================

@flowsheet_app.command("validate")
def flowsheet_validate(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Validate a flowsheet without compiling it.

    Performs pre-compilation validation checks including:
    - Unit inputs resolve to streams or other units
    - No orphan units
    - Recycle streams detection

    Example:
        qsdsan-engine flowsheet validate --session abc123
    """
    try:
        from utils.topo_sort import validate_flowsheet_connectivity, detect_cycles
        from core.unit_registry import get_unit_spec

        session = session_manager.get_session(session_id)
        errors, warnings = validate_flowsheet_connectivity(
            session.units,
            session.streams,
            session.connections,
        )
        cycles = detect_cycles(
            session.units,
            session.connections,
        )

        # Model compatibility check across junctions (Phase 3B.2)
        junction_types = {"ASM2dtoADM1", "ADM1toASM2d", "mADM1toASM2d", "ASM2dtomADM1",
                         "ASMtoADM", "ADMtoASM", "ADM1ptomASM2d", "mASM2dtoADM1p"}
        model_compat_warnings = []

        for unit_id, config in session.units.items():
            unit_type = config.unit_type
            if unit_type in junction_types:
                try:
                    spec = get_unit_spec(unit_type)
                    junction_models = set(spec.compatible_models) if spec.compatible_models else set()
                except Exception:
                    continue

                for input_ref in config.inputs:
                    upstream_unit_id = None
                    if "-" in input_ref and not input_ref.startswith("-"):
                        upstream_unit_id = input_ref.split("-")[0]
                    elif input_ref in session.units:
                        upstream_unit_id = input_ref

                    if upstream_unit_id and upstream_unit_id in session.units:
                        upstream_config = session.units[upstream_unit_id]
                        upstream_type = upstream_config.unit_type
                        try:
                            upstream_spec = get_unit_spec(upstream_type)
                            upstream_models = set(upstream_spec.compatible_models) if upstream_spec.compatible_models else set()
                            if upstream_models and junction_models:
                                common = upstream_models & junction_models
                                if not common:
                                    model_compat_warnings.append(
                                        f"Junction '{unit_id}' ({unit_type}) may be incompatible with upstream "
                                        f"'{upstream_unit_id}' ({upstream_type})"
                                    )
                        except Exception:
                            pass

        warnings.extend(model_compat_warnings)

        is_valid = len(errors) == 0
        result = {
            "session_id": session_id,
            "is_valid": is_valid,
            "errors": errors,
            "warnings": warnings,
            "detected_cycles": [c["cycle_path"] for c in cycles] if cycles else [],
            "n_units": len(session.units),
            "n_streams": len(session.streams),
            "n_connections": len(session.connections),
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            status_color = "green" if is_valid else "red"
            status_text = "VALID" if is_valid else "INVALID"
            console.print(f"\n[bold]Flowsheet Validation: [{status_color}]{status_text}[/{status_color}][/bold]")
            console.print(f"Session: {session_id}")
            console.print(f"Units: {len(session.units)}, Streams: {len(session.streams)}, Connections: {len(session.connections)}")

            if errors:
                console.print("\n[red]Errors:[/red]")
                for e in errors:
                    console.print(f"  - {e}")

            if warnings:
                console.print("\n[yellow]Warnings:[/yellow]")
                for w in warnings:
                    console.print(f"  - {w}")

            if cycles:
                console.print("\n[cyan]Detected cycles (need recycle_streams):[/cyan]")
                for c in cycles:
                    console.print(f"  - {' -> '.join(c['cycle_path'])}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("suggest-recycles")
def flowsheet_suggest_recycles(
    session_id: str = typer.Option(..., "--session", "-s", help="Session ID"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Detect potential recycle streams in a flowsheet.

    Analyzes the flowsheet topology to identify cycles that likely
    represent recycle streams (e.g., RAS, internal recycles).

    Example:
        qsdsan-engine flowsheet suggest-recycles --session abc123
    """
    try:
        from utils.topo_sort import detect_cycles

        session = session_manager.get_session(session_id)
        cycles = detect_cycles(
            session.units,
            session.connections,
        )

        # Identify sources (influent streams) and sinks (units with no downstream)
        sources = [sid for sid, s in session.streams.items() if s.stream_type == "influent"]

        # Find sinks: units that are not in any connection's from_unit
        all_from_units = set()
        for conn in session.connections:
            try:
                from utils.pipe_parser import parse_port_notation
                from_ref = parse_port_notation(conn.from_port)
                all_from_units.add(from_ref.unit_id)
            except Exception:
                pass
        sinks = [uid for uid in session.units.keys() if uid not in all_from_units]

        suggestions = []
        for c in cycles:
            # Suggest the last edge in the cycle as the recycle
            cycle_path = c["cycle_path"]
            if len(cycle_path) >= 2:
                from_unit = cycle_path[-2]
                to_unit = cycle_path[-1]
                suggestions.append({
                    "cycle_path": cycle_path,
                    "suggested_recycle": {
                        "from": f"{from_unit}-0",
                        "to": f"{to_unit}-1",
                        "stream_id": f"recycle_{from_unit}_{to_unit}",
                    },
                    "recycle_type": "detected",
                })

        result = {
            "session_id": session_id,
            "n_cycles_detected": len(cycles),
            "suggestions": suggestions,
            "topology": {
                "sources": sources,
                "sinks": sinks,
            },
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"\n[bold]Recycle Detection: {session_id}[/bold]")
            console.print(f"Cycles detected: {len(cycles)}")

            if suggestions:
                console.print("\n[cyan]Suggested recycles:[/cyan]")
                for s in suggestions:
                    console.print(f"  Cycle: {' -> '.join(s['cycle_path'])}")
                    sr = s["suggested_recycle"]
                    console.print(f"    Suggestion: {sr['from']} -> {sr['to']} (stream_id: {sr['stream_id']})")
            else:
                console.print("[green]No cycles detected - flowsheet is acyclic[/green]")

            console.print(f"\nSources: {sources}")
            console.print(f"Sinks: {sinks}")

    except Exception as e:
        _error_exit(str(e), json_out)


@flowsheet_app.command("timeseries")
def flowsheet_timeseries(
    job_id: str = typer.Option(..., "--job", "-j", help="Job ID from simulation"),
    stream_ids: Optional[str] = typer.Option(None, "--streams", help="Comma-separated stream IDs to filter"),
    components: Optional[str] = typer.Option(None, "--components", help="Comma-separated component IDs to filter"),
    downsample: int = typer.Option(1, "--downsample", "-d", help="Downsample factor (1 = no downsample)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (JSON)"),
    json_out: bool = typer.Option(False, "--json-out", help="Output as JSON to stdout"),
):
    """
    Get time-series data from a flowsheet simulation.

    Requires a simulation run with --track streams specified.

    Example:
        qsdsan-engine flowsheet timeseries --job abc123 --streams effluent
        qsdsan-engine flowsheet timeseries --job abc123 --components S_NH4,S_PO4 --output ts.json
    """
    try:
        from pathlib import Path as P
        job_dir = P("jobs") / job_id
        ts_path = job_dir / "timeseries.json"

        if not ts_path.exists():
            raise ValueError(f"Time-series data not found for job '{job_id}'. "
                           "Ensure simulation was run with --track parameter.")

        with open(ts_path) as f:
            ts_data = json.load(f)

        # Filter by streams if specified
        if stream_ids:
            filter_streams = set(s.strip() for s in stream_ids.split(","))
            if "streams" in ts_data:
                ts_data["streams"] = {
                    k: v for k, v in ts_data["streams"].items()
                    if k in filter_streams
                }

        # Filter by components if specified
        if components:
            filter_comps = set(c.strip() for c in components.split(","))
            if "streams" in ts_data:
                for stream_id, stream_data in ts_data["streams"].items():
                    ts_data["streams"][stream_id] = {
                        k: v for k, v in stream_data.items()
                        if k in filter_comps
                    }

        # Downsample
        if downsample > 1 and "time" in ts_data:
            ts_data["time"] = ts_data["time"][::downsample]
            if "streams" in ts_data:
                for stream_id, stream_data in ts_data["streams"].items():
                    for comp_id, values in stream_data.items():
                        if isinstance(values, list):
                            ts_data["streams"][stream_id][comp_id] = values[::downsample]

        # Output
        if output:
            with open(output, "w") as f:
                json.dump(ts_data, f, indent=2)
            console.print(f"[green]Time-series saved to {output}[/green]")
        elif json_out:
            print(json.dumps(ts_data, indent=2))
        else:
            # Summary display
            console.print(f"\n[bold]Time-series Data: {job_id}[/bold]")
            if "time" in ts_data:
                console.print(f"Time points: {len(ts_data['time'])}")
                console.print(f"Time range: {ts_data['time'][0]:.2f} - {ts_data['time'][-1]:.2f} {ts_data.get('time_units', 'days')}")
            if "streams" in ts_data:
                console.print(f"\n[bold]Streams:[/bold]")
                for stream_id, stream_data in ts_data["streams"].items():
                    comps = list(stream_data.keys())
                    console.print(f"  - {stream_id}: {len(comps)} components ({', '.join(comps[:5])}{'...' if len(comps) > 5 else ''})")
            console.print("\nUse --json-out or --output to get full data")

    except Exception as e:
        _error_exit(str(e), json_out if 'json_out' in dir() else False)


@flowsheet_app.command("artifact")
def flowsheet_artifact(
    job_id: str = typer.Option(..., "--job", "-j", help="Job ID"),
    artifact_type: str = typer.Option(..., "--type", "-t", help="Artifact type: diagram, report, timeseries"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (required for binary artifacts)"),
    json_out: bool = typer.Option(False, "--json-out", help="Output metadata as JSON"),
):
    """
    Get simulation artifact content.

    Artifact types:
    - diagram: SVG flowsheet diagram
    - report: QMD Quarto report
    - timeseries: JSON time-series data

    Example:
        qsdsan-engine flowsheet artifact --job abc123 --type diagram --output flowsheet.svg
        qsdsan-engine flowsheet artifact --job abc123 --type report --json-out
    """
    try:
        from pathlib import Path as P
        job_dir = P("jobs") / job_id

        if not job_dir.exists():
            raise ValueError(f"Job directory not found: {job_id}")

        # Map artifact types to file patterns
        artifact_files = {
            "diagram": ["flowsheet.svg", "diagram.svg", "system.svg"],
            "report": ["report.qmd", "simulation_report.qmd"],
            "timeseries": ["timeseries.json", "time_series.json"],
        }

        if artifact_type not in artifact_files:
            raise ValueError(f"Unknown artifact type: {artifact_type}. "
                           f"Valid types: {list(artifact_files.keys())}")

        # Find the artifact file
        artifact_path = None
        for filename in artifact_files[artifact_type]:
            candidate = job_dir / filename
            if candidate.exists():
                artifact_path = candidate
                break

        if not artifact_path:
            raise ValueError(f"Artifact '{artifact_type}' not found in job '{job_id}'")

        # Read content
        is_binary = artifact_path.suffix in [".png", ".pdf"]
        if is_binary:
            if not output:
                raise ValueError(f"Binary artifact requires --output parameter")
            import shutil
            shutil.copy(artifact_path, output)
            console.print(f"[green]Artifact saved to {output}[/green]")
            return

        content = artifact_path.read_text(encoding="utf-8")

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(content)
            console.print(f"[green]Artifact saved to {output}[/green]")
        elif json_out:
            result = {
                "job_id": job_id,
                "artifact_type": artifact_type,
                "format": artifact_path.suffix[1:] if artifact_path.suffix else "text",
                "path": str(artifact_path),
                "size_bytes": len(content.encode("utf-8")),
            }
            # Include content for text artifacts in JSON mode
            if artifact_type in ["report", "timeseries"]:
                if artifact_type == "timeseries":
                    result["content"] = json.loads(content)
                else:
                    result["content"] = content
            print(json.dumps(result, indent=2))
        else:
            # Display content directly for text artifacts
            console.print(f"\n[bold]Artifact: {artifact_type} ({artifact_path.name})[/bold]")
            console.print(f"Size: {len(content)} bytes\n")
            if artifact_type == "timeseries":
                data = json.loads(content)
                console.print(json.dumps(data, indent=2)[:2000])
                if len(content) > 2000:
                    console.print("\n... (truncated, use --output to save full content)")
            else:
                console.print(content[:3000])
                if len(content) > 3000:
                    console.print("\n... (truncated, use --output to save full content)")

    except Exception as e:
        _error_exit(str(e), json_out if 'json_out' in dir() else False)


# =============================================================================
# Models command group (Phase 3 discoverability)
# =============================================================================

models_app = typer.Typer(
    name="models",
    help="Model discovery and component information",
)
app.add_typer(models_app, name="models")


@models_app.command("components")
def models_components(
    model_type: str = typer.Argument(..., help="Model type: ASM1, ASM2d, mADM1"),
    include_typical: bool = typer.Option(True, "--typical/--no-typical", help="Include typical domestic values"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Get component IDs and metadata for a process model.

    Example:
        qsdsan-engine models components ASM2d
        qsdsan-engine models components mADM1 --json-out
    """
    try:
        from core.model_registry import ModelType, get_model_info, MODEL_REGISTRY

        # Parse model type
        model_type_upper = model_type.upper()
        if model_type_upper == "MADM1":
            model_type_upper = "MADM1"  # Keep original case

        try:
            mt = ModelType(model_type_upper)
        except ValueError:
            # Try alternate names
            mt_map = {"ADM1": "MADM1", "ASM2D": "ASM2D"}
            if model_type_upper in mt_map:
                mt = ModelType(mt_map[model_type_upper])
            else:
                raise ValueError(f"Unknown model type: {model_type}. Valid: ASM1, ASM2d, mADM1")

        info = get_model_info(mt)
        components = info.get("components", [])

        # Component metadata from server.py COMPONENT_METADATA
        from server import COMPONENT_METADATA
        metadata = COMPONENT_METADATA.get(model_type_upper, {})

        # Build component list
        component_list = []
        for comp_id in components:
            comp_info = {"id": comp_id}
            if comp_id in metadata:
                meta = metadata[comp_id]
                comp_info.update(meta)
            component_list.append(comp_info)

        # Organize by category
        categories = {}
        for comp in component_list:
            cat = comp.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(comp["id"])

        result = {
            "model_type": str(mt.value),
            "n_components": len(components),
            "concentration_units": "mg/L",
            "components": component_list,
            "categories": categories,
            "description": info.get("description", ""),
        }

        if json_out:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"\n[bold]{mt.value} Components ({len(components)} total)[/bold]")
            console.print(f"Units: mg/L (default)\n")

            # Display by category
            for cat, comp_ids in categories.items():
                console.print(f"[cyan]{cat.title()}:[/cyan]")
                for cid in comp_ids:
                    meta = metadata.get(cid, {})
                    name = meta.get("name", "")
                    typical = meta.get("typical_domestic", "")
                    typical_str = f" (typical: {typical})" if typical and include_typical else ""
                    name_str = f" - {name}" if name else ""
                    console.print(f"  {cid}{name_str}{typical_str}")
                console.print()

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
