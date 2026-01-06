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
        elif template.startswith("mle_mbr") or template.startswith("a2o_mbr") or template.startswith("ao_mbr"):
            result = {"error": f"Template {template} not yet implemented. Coming in Phase 1C."}
        else:
            result = {"error": f"Unknown template: {template}"}

        # Output results
        if json_out:
            print(json.dumps(result, indent=2, default=str))
        else:
            _display_simulation_result(result)

        # Save results
        with open(output_dir / "simulation_results.json", "w") as f:
            json.dump(result, f, indent=2, default=str)

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
    # TODO: Import and call actual simulation engine
    # For now, return placeholder indicating the template is being set up
    return {
        "status": "template_pending",
        "template": "anaerobic_cstr_madm1",
        "message": "Anaerobic CSTR template will be ported from anaerobic-design-mcp in Phase 1B",
        "influent": {
            "model_type": state.model_type.value,
            "flow_m3_d": state.flow_m3_d,
            "temperature_K": state.temperature_K,
            "n_components": len(state.concentrations),
        },
        "config": {
            "duration_days": duration_days,
            "timestep_hours": timestep_hours,
            "reactor_config": reactor_config,
        },
    }


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
            # TODO: Call actual conversion engine (using QSDsan Junction units)
            result = {
                "status": "conversion_pending",
                "message": f"Conversion {from_model} → {to_model} will be implemented in Phase 1D",
                "from_model": from_model,
                "to_model": to_model,
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
    check_charge: bool = typer.Option(True, "--check-charge/--no-check-charge", help="Check charge balance"),
    check_mass: bool = typer.Option(True, "--check-mass/--no-check-mass", help="Check mass balance"),
    json_out: bool = typer.Option(False, "--json-out", "-j", help="Output as JSON"),
):
    """
    Validate PlantState against model requirements.

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
