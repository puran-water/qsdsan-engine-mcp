# QSDsan Engine MCP

> **⚠️ DEVELOPMENT STATUS: This project is under active development and is not yet production-ready. APIs, interfaces, and functionality may change without notice. Use at your own risk for evaluation and testing purposes only. Not recommended for production deployments.**


A universal wastewater treatment simulation engine exposing [QSDsan](https://github.com/QSD-Group/QSDsan) capabilities through dual adapters for AI agent integration.

## Motivation

Commercial wastewater simulation platforms offer sophisticated biological models but impose a significant bottleneck: **GUI-driven workflows** that limit iteration speed, parallelization, and reproducibility. Process engineers spend substantial time navigating interfaces rather than exploring designs.

QSDsan Engine MCP inverts this paradigm by making **natural language the primary interface**. Instead of clicking through dialogs, engineers describe what they want:

> "Compare anaerobic-aerobic vs aerobic-only treatment for this high-strength industrial waste: evaluate supplemental alkalinity and nutrient costs, digester heating load, and biogas energy credit to identify the lowest life cycle cost flowsheet."

This enables:

- **Collapsed iteration cycles**: Build -> run -> diagnose -> patch -> rerun without GUI navigation
- **Massive scenario enumeration**: DOE, Monte Carlo, and optimization workflows become natural since everything is code
- **Reproducible, diffable runs**: Version-controlled session specs with deterministic metadata
- **Structured diagnostics**: Validation warnings, model compatibility checks, and actionable error messages surfaced directly to agents

The goal is not to replace domain expertise, but to **remove friction** so engineers can focus on design decisions rather than tool mechanics.

## Architecture: Dual Adapters

The engine exposes identical functionality through two adapters:

```
                    +-------------------------------------+
                    |       QSDsan Engine Core            |
                    |  (Templates, Models, Converters)    |
                    +-----------------+-------------------+
                                      |
              +-----------------------+---------------------+
              |                       |                     |
              v                       v                     v
     +----------------+      +----------------+     +----------------+
     |   MCP Adapter  |      |   CLI Adapter  |     |  Python API    |
     |   (server.py)  |      |   (cli.py)     |     |  (direct use)  |
     +----------------+      +----------------+     +----------------+
              |                       |
              v                       v
     +----------------+      +----------------+
     |  MCP Clients   |      |  Agent Skills  |
     |  (Claude, etc) |      |  (Claude Code) |
     +----------------+      +----------------+
```

### MCP Adapter (`server.py`)

For MCP-compatible clients (Claude Desktop, Cline, etc.):

```bash
python server.py
```

### CLI Adapter (`cli.py`)

For CLI-based agent runtimes and Agent Skills:

```bash
python cli.py --help
```

## Tool Surface

### Core Simulation Tools

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `list_templates` | `list_templates` | `templates` | List available treatment templates |
| `validate_state` | `validate_state` | `validate` | Validate influent state against model |
| `simulate_system` | `simulate_system` | `simulate` | Run template-based simulation |
| `convert_state` | `convert_state` | `convert` | Convert between ASM2d and mADM1 |

### Flowsheet Construction Tools

Build custom treatment trains dynamically:

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `create_flowsheet_session` | `create_flowsheet_session` | `flowsheet new` | Create new flowsheet session |
| `create_stream` | `create_stream` | `flowsheet add-stream` | Add influent/recycle stream |
| `create_unit` | `create_unit` | `flowsheet add-unit` | Add unit operation |
| `connect_units` | `connect_units` | `flowsheet connect` | Wire units together |
| `build_system` | `build_system` | `flowsheet build` | Compile to QSDsan System |
| `simulate_built_system` | `simulate_built_system` | `flowsheet simulate` | Run simulation |
| `list_units` | `list_units` | `flowsheet units` | List available unit types |

### Session Management Tools

Modify flowsheets without starting over:

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `update_stream` | `update_stream` | `flowsheet update-stream` | Modify stream properties |
| `update_unit` | `update_unit` | `flowsheet update-unit` | Modify unit parameters |
| `delete_stream` | `delete_stream` | `flowsheet delete-stream` | Remove stream |
| `delete_unit` | `delete_unit` | `flowsheet delete-unit` | Remove unit and connections |
| `delete_connection` | `delete_connection` | `flowsheet delete-connection` | Remove specific connection |
| `clone_session` | `clone_session` | `flowsheet clone` | Fork session for experimentation |

### Discoverability Tools

Explore models and validate configurations before simulation:

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `get_model_components` | `get_model_components` | `models components` | Get component IDs and metadata |
| `validate_flowsheet` | `validate_flowsheet` | `flowsheet validate` | Pre-compilation validation |
| `suggest_recycles` | `suggest_recycles` | `flowsheet suggest-recycles` | Detect potential recycle streams |

### Artifact Retrieval Tools

Access simulation outputs programmatically:

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `get_artifact` | `get_artifact` | `flowsheet artifact` | Get diagram/report content |
| `get_flowsheet_timeseries` | `get_flowsheet_timeseries` | `flowsheet timeseries` | Get time-series trajectories |

## Supported Models

| Model | Components | Use Case |
|-------|------------|----------|
| **ASM1** | 13 | Activated sludge (basic nitrification/denitrification) |
| **ASM2d** | 19 | Activated sludge with biological phosphorus removal |
| **mADM1** | 63 | Anaerobic digestion with sulfur-reducing bacteria |

## Pre-built Templates

| Template | Model | Description |
|----------|-------|-------------|
| `anaerobic_cstr_madm1` | mADM1 | Anaerobic CSTR digester |
| `mle_mbr_asm2d` | ASM2d | MLE process with MBR |
| `ao_mbr_asm2d` | ASM2d | A/O process with MBR |
| `a2o_mbr_asm2d` | ASM2d | A2O process with EBPR and MBR |

## Quick Start

### Using CLI

```bash
# List templates
python cli.py templates --json-out

# Create influent file
cat > influent.json << 'EOF'
{
  "flow_m3_d": 4000,
  "temperature_K": 293.15,
  "concentrations": {"S_F": 75, "S_A": 20, "S_NH4": 35, "S_PO4": 9}
}
EOF

# Run MLE-MBR simulation (use file path for --influent, --duration-days not --duration)
python cli.py simulate \
  --template mle_mbr_asm2d \
  --influent influent.json \
  --duration-days 15 \
  --report

# Build custom flowsheet
python cli.py flowsheet new --model ASM2d --id my_plant
python cli.py flowsheet add-stream --session my_plant --id influent \
  --flow 4000 --concentrations '{"S_F": 75, "S_A": 20, "S_NH4": 35}'
python cli.py flowsheet add-unit --session my_plant --type CSTR --id anoxic \
  --params '{"V_max": 1000}' --inputs '["influent"]'
python cli.py flowsheet add-unit --session my_plant --type CSTR --id aerobic \
  --params '{"V_max": 2000, "aeration": 2.0}' --inputs '["anoxic-0"]'
python cli.py flowsheet build --session my_plant
python cli.py flowsheet simulate --session my_plant --duration 15
```

### Using MCP

Configure in your MCP client (e.g., Claude Desktop `config.json`):

```json
{
  "mcpServers": {
    "qsdsan-engine": {
      "command": "python",
      "args": ["/path/to/qsdsan-engine-mcp/server.py"]
    }
  }
}
```

Then use natural language:
> "Create an MLE process treating 4000 m3/d of municipal wastewater and simulate for 15 days"

## Unit Registry

49 unit operations available across categories:

- **Reactors:** CSTR, AnaerobicCSTR, PFR, ActivatedSludgeProcess, AnaerobicDigestion
- **Separators:** CompletelyMixedMBR, AnMBR, PolishingFilter, MembraneDistillation
- **Clarifiers:** FlatBottomCircularClarifier, PrimaryClarifier, IdealClarifier
- **Sludge:** Thickener, Centrifuge, SludgeDigester, DryingBed
- **Junctions:** ASM2dtoADM1, ADM1toASM2d, mADM1toASM2d (model converters)
- **Utilities:** Splitter, Mixer, Tank, StorageTank, DynamicInfluent

```bash
# List all units
python cli.py flowsheet units --json-out

# Filter by model compatibility
python cli.py flowsheet units --model mADM1

# Filter by category
python cli.py flowsheet units --category reactor
```

## Pipe Notation

Connect units using BioSTEAM pipe notation:

```python
# Output notation: "A1-0" -> unit A1, output port 0
# Input notation: "1-M1" -> unit M1, input port 1
# Direct: "U1-U2" -> U1.outs[0] -> U2.ins[0]
# Explicit: "U1-0-1-U2" -> U1.outs[0] -> U2.ins[1]
```

## Output

Simulations produce:

- **JSON results** with effluent quality, removal efficiencies, and deterministic metadata (solver settings, library versions, timestamps)
- **SVG flowsheet diagrams** showing unit operations and streams
- **Quarto reports** (optional) with comprehensive analysis
- **Time-series data** for tracked streams

## Installation

```bash
# Clone repository
git clone https://github.com/puran-water/qsdsan-engine-mcp.git
cd qsdsan-engine-mcp

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies (either method works)
pip install -r requirements.txt
# OR
pip install -e .
```

### Dependencies

**Python packages** (installed automatically):
- Python 3.10+
- QSDsan 1.3+
- BioSTEAM 2.40+
- FastMCP (for MCP adapter)
- Typer + Rich (for CLI adapter)
- Jinja2 (for report generation)
- Matplotlib (for time-series plots)

**External tools** (install separately):
- **Graphviz**: Required for flowsheet diagrams
  - Linux: `sudo apt install graphviz`
  - macOS: `brew install graphviz`
  - Windows: https://graphviz.org/download/
- **Quarto CLI** (optional): For rendering `.qmd` reports to HTML/PDF
  - https://quarto.org/docs/get-started/

## License

University of Illinois/NCSA Open Source License - see [LICENSE.txt](LICENSE.txt) for details.

This is a derivative work based on [QSDsan](https://github.com/QSD-Group/QSDsan), licensed under the same terms.

## Acknowledgments

Built on [QSDsan](https://github.com/QSD-Group/QSDsan) by the Quantitative Sustainable Design Group.
