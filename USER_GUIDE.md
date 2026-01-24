# QSDsan Engine MCP - User Guide

**Version:** 3.0.2
**Last Updated:** January 2026

This guide provides an overview of the QSDsan Engine MCP service, walking through a typical use case from design through simulation to outputs.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Typical Workflow](#typical-workflow)
4. [Use Case: MLE-MBR Wastewater Treatment Plant](#use-case-mle-mbr-wastewater-treatment-plant)
5. [Jobs Folder Structure](#jobs-folder-structure)
6. [Output Files Reference](#output-files-reference)
7. [MCP Tools Quick Reference](#mcp-tools-quick-reference)

---

## Overview

QSDsan Engine MCP is a universal wastewater simulation engine that provides:

- **Anaerobic treatment** using mADM1 (modified Anaerobic Digestion Model No. 1) with 63 components
- **Aerobic treatment** using ASM2d (Activated Sludge Model No. 2d) with 19 components
- **Dual adapters**: MCP (Model Context Protocol) for AI assistants + CLI for command-line use

The service enables AI assistants like Claude to design, simulate, and analyze wastewater treatment plants through natural language interactions.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Client Applications                          │
│         (Claude Desktop, AI Assistants, CLI Tools)               │
└─────────────────────────┬───────────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
            ▼                           ▼
┌───────────────────┐       ┌───────────────────┐
│   MCP Adapter     │       │   CLI Adapter     │
│   (server.py)     │       │   (cli.py)        │
│   29 tools        │       │   Typer commands  │
└─────────┬─────────┘       └─────────┬─────────┘
          │                           │
          └───────────┬───────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Core Engine                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Plant State  │  │ Model        │  │ Template     │          │
│  │ Management   │  │ Registry     │  │ Registry     │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Converters   │  │ Flowsheet    │  │ Report       │          │
│  │              │  │ Builder      │  │ Generator    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                  QSDsan / BioSTEAM                               │
│              (Wastewater Simulation Engine)                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Typical Workflow

The QSDsan Engine supports two main workflows:

### 1. Template-Based Simulation (Simple)

For standard configurations using pre-built templates:

```
Define Influent → Select Template → Run Simulation → Retrieve Results
```

### 2. Custom Flowsheet Construction (Advanced)

For custom plant designs:

```
Create Session → Add Streams → Add Units → Connect Units → Validate → Build → Simulate → Results
```

---

## Use Case: MLE-MBR Wastewater Treatment Plant

This example demonstrates designing and simulating a Modified Ludzack-Ettinger (MLE) process with Membrane Bioreactor (MBR) for biological nutrient removal.

### Step 1: Check Available Templates

First, discover what templates are available:

**MCP Tool:** `list_templates`

```json
{
  "templates": [
    {"id": "cstr_madm1", "description": "Single CSTR anaerobic digester using mADM1"},
    {"id": "mle_mbr_asm2d", "description": "MLE process with MBR using ASM2d"},
    {"id": "ao_mbr_asm2d", "description": "A/O process with MBR using ASM2d"},
    {"id": "a2o_mbr_asm2d", "description": "A2O process with MBR using ASM2d"}
  ]
}
```

### Step 2: Understand the Model Components

Query the component definitions for ASM2d:

**MCP Tool:** `get_model_components`
**Input:** `{"model_type": "ASM2d"}`

```json
{
  "model_type": "ASM2d",
  "component_count": 19,
  "components": {
    "S_O2": {"name": "Dissolved oxygen", "unit": "mg/L", "typical_range": [0, 8]},
    "S_F": {"name": "Fermentable substrate", "unit": "mg COD/L", "typical_range": [0, 100]},
    "S_A": {"name": "Acetate", "unit": "mg COD/L", "typical_range": [0, 50]},
    "S_NH4": {"name": "Ammonium", "unit": "mg N/L", "typical_range": [0, 50]},
    "S_NO3": {"name": "Nitrate", "unit": "mg N/L", "typical_range": [0, 30]},
    "S_PO4": {"name": "Phosphate", "unit": "mg P/L", "typical_range": [0, 15]},
    "X_H": {"name": "Heterotrophic biomass", "unit": "mg COD/L", "typical_range": [0, 5000]},
    "...": "..."
  }
}
```

### Step 3: Define the Influent

Create a plant state representing typical municipal wastewater:

**MCP Tool:** `validate_state`
**Input:**
```json
{
  "state": {
    "model_type": "ASM2d",
    "flow_m3_d": 4000,
    "temperature_K": 293.15,
    "concentrations": {
      "S_F": 75,
      "S_A": 20,
      "S_I": 30,
      "S_NH4": 17,
      "S_NO3": 0,
      "S_PO4": 9,
      "X_S": 125,
      "X_H": 30,
      "X_I": 50,
      "S_ALK": 300
    }
  }
}
```

**Response:**
```json
{
  "valid": true,
  "warnings": [],
  "calculated_metrics": {
    "total_COD_mg_L": 330,
    "total_N_mg_L": 17,
    "total_P_mg_L": 9
  }
}
```

### Step 4: Run the Simulation

Submit the simulation job:

**MCP Tool:** `simulate_system`
**Input:**
```json
{
  "template": "mle_mbr_asm2d",
  "influent_state": {
    "model_type": "ASM2d",
    "flow_m3_d": 4000,
    "temperature_K": 293.15,
    "concentrations": {
      "S_F": 75, "S_A": 20, "S_I": 30,
      "S_NH4": 17, "S_NO3": 0, "S_PO4": 9,
      "X_S": 125, "X_H": 30, "X_I": 50, "S_ALK": 300
    }
  },
  "duration_days": 15
}
```

**Response:**
```json
{
  "job_id": "a1b2c3d4",
  "status": "starting",
  "message": "Simulation job submitted"
}
```

### Step 5: Monitor Job Status

Check the simulation progress:

**MCP Tool:** `get_job_status`
**Input:** `{"job_id": "a1b2c3d4"}`

```json
{
  "job_id": "a1b2c3d4",
  "status": "running",
  "started_at": "2026-01-21T10:30:00Z",
  "elapsed_seconds": 45
}
```

Wait for completion (typically 30-90 seconds):

```json
{
  "job_id": "a1b2c3d4",
  "status": "completed",
  "started_at": "2026-01-21T10:30:00Z",
  "completed_at": "2026-01-21T10:31:25Z",
  "elapsed_seconds": 85,
  "exit_code": 0
}
```

### Step 6: Retrieve Results

Get the simulation results:

**MCP Tool:** `get_job_results`
**Input:** `{"job_id": "a1b2c3d4"}`

```json
{
  "status": "completed",
  "template": "mle_mbr_asm2d",
  "engine_version": "3.0.2",

  "influent": {
    "flow_m3_d": 4000,
    "COD_mg_L": 330,
    "TKN_mg_L": 17,
    "TP_mg_L": 9
  },

  "reactor": {
    "type": "MLE-MBR",
    "V_anoxic_m3": 312,
    "V_aerobic_m3": 504,
    "V_mbr_m3": 382,
    "V_total_m3": 1198,
    "HRT_hours": 7.19,
    "SRT_days": 15,
    "DO_aerobic_mg_L": 2.3,
    "Q_ras_m3_d": 16000,
    "Q_ir_m3_d": 8000,
    "Q_was_m3_d": 768
  },

  "effluent": {
    "COD_mg_L": 31.74,
    "TSS_mg_L": 0.86,
    "NH4_mg_N_L": 16.72,
    "NO3_mg_N_L": 0.0,
    "PO4_mg_P_L": 8.83
  },

  "performance": {
    "success": true,
    "cod": {
      "influent_mg_L": 330,
      "effluent_mg_L": 31.74,
      "removal_pct": 90.38
    },
    "tss": {
      "influent_mg_L": 158.25,
      "effluent_mg_L": 0.86,
      "removal_pct": 99.45
    },
    "nitrogen": {
      "TKN_removal_pct": 1.65,
      "TN_removal_pct": 1.65
    }
  }
}
```

### Step 7: Review Outputs

The job directory now contains all artifacts:

```
jobs/a1b2c3d4/
├── job.json                 # Execution metadata
├── config.json              # Input configuration
├── influent.json            # Influent state
├── simulation_results.json  # Full results
├── flowsheet.svg            # System diagram
├── stdout.log               # Process output
└── stderr.log               # Error output (if any)
```

---

## Jobs Folder Structure

### Overview

```
jobs/
├── {job_id}/                    # Simple simulation jobs
│   ├── job.json
│   ├── config.json
│   ├── influent.json
│   ├── simulation_results.json
│   ├── flowsheet.svg
│   ├── stdout.log
│   └── stderr.log
│
└── flowsheets/                  # Custom flowsheet sessions
    └── {session_id}/
        ├── session.json
        ├── build_config.json
        └── system_result.json
```

### Job IDs

- **Format:** 8 hexadecimal characters (e.g., `a1b2c3d4`)
- **Generated:** Automatically by JobManager using UUID
- **Purpose:** Unique identifier for tracking simulation jobs

### Session IDs

- **Format:** User-defined string (e.g., `my_plant_v1`)
- **Constraints:** Alphanumeric, underscores, hyphens only (validated for security)
- **Purpose:** Named workspace for flowsheet construction

---

## Output Files Reference

### job.json

**Purpose:** Complete execution record for the simulation job

**Created:** When job is submitted (before execution starts)

**Updated:** When job completes with exit code and timestamps

**Contents:**
```json
{
  "id": "a1b2c3d4",
  "command": ["python", "cli.py", "simulate", "--template", "mle_mbr_asm2d", ...],
  "cwd": "C:\\path\\to\\qsdsan-engine-mcp",
  "status": "completed",
  "started_at": 1768912680.29,
  "completed_at": 1768912866.16,
  "job_dir": "C:\\path\\to\\jobs\\a1b2c3d4",
  "pid": 91572,
  "exit_code": 0
}
```

**Status Values:** `starting`, `running`, `completed`, `failed`

---

### config.json

**Purpose:** Stores the simulation configuration parameters

**Created:** When job directory is initialized

**Contents:**
```json
{
  "template": "mle_mbr_asm2d",
  "duration_days": 15.0,
  "timestep_hours": null,
  "reactor_config": {},
  "parameters": {}
}
```

---

### influent.json

**Purpose:** Complete influent stream specification (PlantState)

**Created:** When simulation job is submitted

**Contents:**
```json
{
  "model_type": "ASM2d",
  "flow_m3_d": 4000,
  "temperature_K": 293.15,
  "concentrations": {
    "S_F": 75,
    "S_A": 20,
    "S_NH4": 17,
    "...": "..."
  },
  "reactor_config": {},
  "metadata": {}
}
```

---

### simulation_results.json

**Purpose:** Complete simulation results including performance metrics, effluent quality, and time series data

**Created:** When simulation completes successfully

**Size:** Typically 15-25 KB

**Key Sections:**

| Section | Description |
|---------|-------------|
| `status` | Completion status |
| `template` | Template used |
| `engine_version` | Engine version |
| `influent` | Input summary |
| `reactor` | Reactor configuration and sizing |
| `effluent` | Final effluent concentrations |
| `performance` | Removal efficiencies |
| `inhibition` | Process inhibition indicators (anaerobic) |
| `timeseries` | Time-dependent concentration data |

---

### flowsheet.svg

**Purpose:** Visual diagram of the process flowsheet

**Created:** When simulation completes

**Format:** Scalable Vector Graphics (SVG)

**Contents:** Unit operations (reactors, membranes, etc.) connected by streams showing the treatment train configuration

**Usage:** Can be viewed in any web browser or SVG viewer

---

### stdout.log

**Purpose:** Captures standard output from the simulation subprocess

**Created:** When background job completes

**Contents:** Progress messages, warnings, and informational output from QSDsan

**Typical Size:** 500 bytes - 2 KB

---

### stderr.log

**Purpose:** Captures error output from the simulation subprocess

**Created:** When background job completes

**Contents:** Error messages, stack traces (if simulation failed), deprecation warnings from dependencies

**Note:** May contain warnings even for successful simulations (from QSDsan/BioSTEAM dependencies)

---

### session.json (Flowsheet Sessions)

**Purpose:** Persistent storage of flowsheet construction state

**Created:** When `create_flowsheet_session` is called

**Updated:** Each time streams, units, or connections are modified

**Contents:**
```json
{
  "session_id": "my_plant_v1",
  "primary_model_type": "ASM2d",
  "model_types": ["ASM2d"],
  "streams": {
    "influent": {
      "stream_id": "influent",
      "flow_m3_d": 4000.0,
      "concentrations": {...},
      "stream_type": "influent",
      "model_type": "ASM2d"
    }
  },
  "units": {
    "AX": {
      "unit_id": "AX",
      "unit_type": "CSTR",
      "params": {"V_max": 400, "aeration": 0},
      "model_type": "ASM2d"
    }
  },
  "connections": [
    {"from_unit": "AX", "from_port": 0, "to_unit": "OX", "to_port": 0}
  ],
  "created_at": "2026-01-21T10:00:00Z",
  "updated_at": "2026-01-21T10:15:00Z",
  "status": "building"
}
```

---

### build_config.json (Flowsheet Sessions)

**Purpose:** Records system compilation settings

**Created:** When `build_system` is called

**Contents:**
```json
{
  "system_id": "my_plant_sys",
  "unit_order": ["AX", "OX", "MBR"],
  "recycle_streams": ["RAS"]
}
```

---

### system_result.json (Flowsheet Sessions)

**Purpose:** Output metadata from flowsheet compilation

**Created:** When `build_system` completes

**Contents:**
```json
{
  "system_id": "my_plant_sys",
  "unit_order": ["AX", "OX", "MBR"],
  "recycle_edges": [],
  "recycle_streams": ["RAS"],
  "streams_created": ["influent", "effluent", "WAS", "RAS"],
  "units_created": ["AX", "OX", "MBR"],
  "build_warnings": []
}
```

---

## MCP Tools Quick Reference

### Discovery Tools

| Tool | Purpose |
|------|---------|
| `get_version` | Get engine and dependency versions |
| `list_templates` | List available simulation templates |
| `list_units` | List available unit operation types |
| `get_model_components` | Get component definitions for a model |

### Simulation Tools

| Tool | Purpose |
|------|---------|
| `simulate_system` | Run a template-based simulation |
| `get_job_status` | Check job execution status |
| `get_job_results` | Retrieve completed simulation results |

### Utility Tools

| Tool | Purpose |
|------|---------|
| `validate_state` | Validate a PlantState definition |
| `convert_state` | Convert between model types (ASM2d ↔ mADM1) |

### Flowsheet Construction Tools

| Tool | Purpose |
|------|---------|
| `create_flowsheet_session` | Start a new flowsheet design |
| `create_stream` | Add a stream to the flowsheet |
| `create_unit` | Add a unit operation |
| `connect_units` | Connect units with streams |
| `validate_flowsheet` | Check flowsheet validity |
| `suggest_recycles` | Get recycle stream suggestions |
| `build_system` | Compile the flowsheet |
| `simulate_built_system` | Simulate the compiled system |

### Session Management Tools

| Tool | Purpose |
|------|---------|
| `get_flowsheet_session` | Retrieve session state |
| `list_flowsheet_sessions` | List all sessions |
| `clone_session` | Duplicate a session |
| `delete_session` | Remove a session |

### Mutation Tools

| Tool | Purpose |
|------|---------|
| `update_stream` | Modify stream properties |
| `update_unit` | Modify unit parameters |
| `delete_stream` | Remove a stream |
| `delete_unit` | Remove a unit |
| `delete_connection` | Remove a connection |

### Results Tools

| Tool | Purpose |
|------|---------|
| `get_flowsheet_timeseries` | Get time series data |
| `get_artifact` | Retrieve generated files (SVG, reports) |

---

## Environment Configuration

### Session Storage Location

By default, sessions are stored in `jobs/` relative to the project directory.

Override with environment variable:
```bash
export QSDSAN_ENGINE_SESSIONS_DIR=/path/to/custom/location
```

### Concentration Units

| Model | Units |
|-------|-------|
| ASM2d, ASM1, mASM2d | mg/L |
| mADM1, ADM1 | kg/m³ |

The engine automatically validates and warns if concentrations appear to use incorrect units.

---

## Troubleshooting

### Common Issues

**1. Job stuck in "running" status**
- QSDsan has ~18 second cold start on first import
- Complex simulations may take 60-90 seconds
- Check `stderr.log` for errors

**2. "Influent file not found" error**
- Ensure using engine version 3.0.1+ (path handling fix)
- Check `get_version` to confirm version

**3. Simulation fails with convergence error**
- Try longer `duration_days` (default 15)
- Check influent concentrations are reasonable
- Review `stderr.log` for specific error messages

**4. Session not found**
- Session IDs are case-sensitive
- Use `list_flowsheet_sessions` to see available sessions

---

## Version History

| Version | Changes |
|---------|---------|
| 3.0.2 | Current release |
| 3.0.1 | Path handling fix for Claude Desktop, version exposure |
| 3.0.0 | Phase 7C: CH4 calculation fix |

---

*For API reference details, see [API_REFERENCE.md](API_REFERENCE.md)*
