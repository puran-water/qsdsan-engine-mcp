# QSDsan Engine MCP - Universal Biological Wastewater Simulation

## Overview

Universal simulation engine for biological wastewater treatment using QSDsan.
Supports both anaerobic (mADM1) and aerobic (ASM2d) models with explicit state passing.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     qsdsan-engine-mcp                           │
├─────────────────────────────────────────────────────────────────┤
│  CLI Adapter (cli.py)           │  MCP Adapter (server.py)      │
│  ─────────────                  │  ───────────                  │
│  qsdsan-engine simulate \       │  simulate_system tool         │
│    --template mle_mbr_asm2d \   │  (FastMCP, stdio/HTTP/SSE)    │
│    --influent state.json \      │                               │
│    --json-out result.json       │                               │
└─────────────────────────────────┴───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Engine Core                              │
│  - core/plant_state.py: PlantState dataclass                    │
│  - core/model_registry.py: Component management                 │
│  - templates/anaerobic/: mADM1 flowsheet builders               │
│  - templates/aerobic/: ASM2d MBR flowsheet builders             │
└─────────────────────────────────────────────────────────────────┘
```

## Tools (6 Core + 3 Utility)

| Tool | Purpose | Background Job? |
|------|---------|-----------------|
| `simulate_system` | Run QSDsan simulation to steady state | **YES** |
| `get_job_status` | Check job progress | No |
| `get_job_results` | Retrieve simulation results | No |
| `list_templates` | List available flowsheet templates | No |
| `validate_state` | Validate PlantState against model | No |
| `convert_state` | ASM2d ↔ mADM1 state conversion | **YES** |
| `list_jobs` | List all background jobs | No |
| `terminate_job` | Stop a running job | No |
| `get_timeseries_data` | Get time series (large, separate call) | No |

## CLI Usage

```bash
# List available templates
python cli.py templates --json-out

# Validate a state file
python cli.py validate -s influent.json -m mADM1 --json-out

# Run simulation
python cli.py simulate -t anaerobic_cstr_madm1 -i influent.json -d 30 --json-out

# Convert between models
python cli.py convert -i was.json -f ASM2d -t mADM1 -o digester_feed.json
```

## PlantState Schema

```python
@dataclass
class PlantState:
    model_type: ModelType        # "mADM1", "ASM2d", etc.
    flow_m3_d: float             # m³/day
    temperature_K: float         # Kelvin
    concentrations: Dict[str, float]  # Component → kg/m³
    reactor_config: Dict[str, Any]    # V_liq, HRT, etc.
    metadata: Optional[Dict]          # Provenance
```

## Supported Models

| Model | Components | Description |
|-------|------------|-------------|
| mADM1 | 63 | Modified ADM1 with P/S/Fe extensions, 4 biogas species |
| ADM1 | 27 | Standard ADM1 (upstream QSDsan) |
| ASM2d | 17 | Activated Sludge Model 2d |
| mASM2d | ~20 | Modified ASM2d with extensions |
| ASM1 | 13 | Activated Sludge Model 1 |

## Available Templates

### Anaerobic
- `anaerobic_cstr_madm1` - Single CSTR with mADM1 (✅ available)

### Aerobic (MBR-based)
- `mle_mbr_asm2d` - MLE-MBR (⏳ planned)
- `a2o_mbr_asm2d` - A2O-MBR with EBPR (⏳ planned)
- `ao_mbr_asm2d` - Simple A/O-MBR (⏳ planned)

## Background Job Pattern

Heavy operations run as background jobs:

```python
# Start job
result = await simulate_system(template="...", influent_json="...")
job_id = result["job_id"]

# Monitor
status = await get_job_status(job_id)
# {"status": "running", "elapsed_time_seconds": 45}

# Get results when complete
results = await get_job_results(job_id)
```

## Development

```bash
# Install dependencies
pip install -e ".[cli,dev]"

# Run CLI
python cli.py --help

# Run MCP server
python server.py
```

## Files to Port (from anaerobic-design-mcp)

Phase 1B tasks:
- `utils/qsdsan_madm1.py` → `models/madm1.py`
- `utils/qsdsan_reactor_madm1.py` → `models/reactors.py`
- `utils/qsdsan_simulation_sulfur.py` → simulation engine
- `utils/stream_analysis_sulfur.py` → result extraction
- `utils/inoculum_generator.py` → startup helpers
