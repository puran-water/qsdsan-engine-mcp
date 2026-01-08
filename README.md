# QSDsan Engine MCP

A universal wastewater treatment simulation engine exposing [QSDsan](https://github.com/QSD-Group/QSDsan) capabilities through dual adapters for AI agent integration.

## Vision

Enable AI agents to design, simulate, and optimize wastewater treatment systems using industry-standard biological process models (ASM1, ASM2d, mADM1) without requiring deep domain expertise.

## Architecture: Dual Adapters

The engine exposes identical functionality through two adapters, enabling integration with different agent runtimes:

```
                    ┌─────────────────────────────────────┐
                    │       QSDsan Engine Core            │
                    │  (Templates, Models, Converters)    │
                    └─────────────┬───────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
     ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
     │   MCP Adapter  │  │   CLI Adapter  │  │  Python API    │
     │   (server.py)  │  │   (cli.py)     │  │  (direct use)  │
     └────────────────┘  └────────────────┘  └────────────────┘
              │                   │
              ▼                   ▼
     ┌────────────────┐  ┌────────────────┐
     │  MCP Clients   │  │  Agent Skills  │
     │  (Claude, etc) │  │  (Claude Code) │
     └────────────────┘  └────────────────┘
```

### MCP Adapter (`server.py`)

For MCP-compatible clients (Claude Desktop, Cline, etc.):

```bash
# Start MCP server
python server.py
```

### CLI Adapter (`cli.py`)

For CLI-based agent runtimes and Agent Skills:

```bash
# List available commands
python cli.py --help
```

## Tool Surface

### Simulation Tools

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `list_templates` | `list_templates` | `templates` | List available treatment templates |
| `validate_state` | `validate_state` | `validate` | Validate influent state against model |
| `run_simulation` | `run_simulation` | `simulate` | Run template-based simulation |

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

### State Conversion Tools

| Tool | MCP | CLI | Description |
|------|-----|-----|-------------|
| `convert_state` | `convert_state` | `convert` | Convert between ASM2d and mADM1 |

## Supported Models

| Model | Components | Use Case |
|-------|------------|----------|
| **ASM1** | 13 | Activated sludge (basic) |
| **ASM2d** | 19 | Activated sludge with biological P removal |
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

# Run MLE-MBR simulation
python cli.py simulate \
  --template mle_mbr_asm2d \
  --influent '{"flow_m3_d": 4000, "concentrations": {"S_F": 75, "S_NH4": 35}}' \
  --duration 15 \
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

## Installation

```bash
# Clone repository
git clone https://github.com/puran-water/qsdsan-engine-mcp.git
cd qsdsan-engine-mcp

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

- Python 3.10+
- QSDsan 1.3+
- BioSTEAM 2.40+
- FastMCP (for MCP adapter)
- Typer (for CLI adapter)

## Output

Simulations produce:
- **JSON results** with effluent quality, removal efficiencies, and mass balances
- **SVG flowsheet diagrams** showing unit operations and streams
- **Quarto reports** (optional) with comprehensive analysis

## License

University of Illinois/NCSA Open Source License - see [LICENSE.txt](LICENSE.txt) for details.

This is a derivative work based on [QSDsan](https://github.com/QSD-Group/QSDsan), licensed under the same terms.

## Acknowledgments

Built on [QSDsan](https://github.com/QSD-Group/QSDsan) by the Quantitative Sustainable Design Group.
