# Phase N8N-1: Dynamic JSON Input & Study Management for n8n Workflow

**Status:** Implemented

**Created:** 2026-02-03

**Updated:** 2026-02-04

**Author:** Claude Code

**Target Version:** v9.0 (builds on v8.0)

---

## Executive Summary

This plan proposes adapting the n8n workflow (currently v8.0) to support **two input modes**:

1. **Study Mode** - Pass a `study_id` to fetch a predefined configuration from Supabase
2. **Direct Mode** - Pass a full simulation JSON configuration directly

Both modes support optional **overrides** to customize specific parameters. The existing **Env Parameters node is retained** for infrastructure settings. This combines the original dynamic JSON input plan with the Study Configuration Management feature into a single workflow version.

---

## Current State Analysis

### Current Workflow Structure (v8.0)

The v8.0 workflow uses **two hardcoded parameter nodes** plus a **Generate Session ID** node:

**1. Env Parameters Node** (Infrastructure + Session Management)

```json
{
  "server_ip": "34.28.104.162",
  "server_port": "8080",
  "gotenberg_url": "http://34.28.104.162:3000",
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "YOUR_SUPABASE_JWT_KEY_HERE",
  "supabase_bucket": "panicleDevelop_1",
  "ai_model": "gpt-4o",
  "override_prompt": "",
  "session_id": "",
  "analysis_type": "WasteWater"
}
```

**2. WW Parameters Node** (Simulation - Hardcoded)

```json
{
  "template": "mle_mbr_asm2d",
  "timeout_seconds": 300,
  "check_interval_seconds": 60,
  "hard_cancel_buffer": 120,
  "flow_m3_d": 4000,
  "temperature_C": 20,
  "COD_mg_L": 350,
  "NH4_mg_L": 25,
  "TP_mg_L": 8,
  "TSS_mg_L": 220
}
```

**3. Generate Session ID Node** (v8.0 addition)

Auto-generates session_id if not provided, sanitizes for path safety.

### v8.0 Key Features (Already Implemented)

- Hierarchical Supabase folder structure: `{session_id}/{analysis_type}/{filename}`
- Server-side AI analysis via `/api/analyze_results` endpoint
- Session ID auto-generation with timestamp formatting
- Four parallel upload paths: PDF, CSV, JSON, AI Analysis markdown

### Supabase Studies Table (Already Created)

The `studies` table exists in Supabase with 9 default studies:
- 2 templates (aerobic MBR, anaerobic CSTR)
- 7 food & beverage industry studies (dairy, brewery, winery, soft drink, meat processing, fruit/vegetable, dairy anaerobic)

### Current Limitations

1. **Hardcoded parameters** - Must edit workflow to change simulation settings
2. **No study reuse** - Cannot run predefined configurations
3. **No external triggering** - Cannot receive configuration from upstream systems
4. **Limited model support** - ASM2d only; no mADM1 support in workflow

---

## Proposed Solution

### Design Principles

1. **Dual input modes** - Study ID or direct JSON configuration
2. **Override capability** - Customize any parameter in either mode
3. **Separation of concerns** - Infrastructure stays in Env Parameters
4. **Backward compatible** - Support v8-style inputs via legacy mode
5. **External triggerable** - Webhook support for upstream systems

### Input Modes

#### Mode 1: Study Mode (Recommended for Predefined Scenarios)

Pass a `study_id` to fetch configuration from Supabase:

```json
{
  "study_id": "dairy_baseline"
}
```

With optional overrides:

```json
{
  "study_id": "dairy_baseline",
  "overrides": {
    "influent": {
      "flow_m3_d": 1200,
      "simplified": {
        "COD_mg_L": 5500
      }
    }
  }
}
```

#### Mode 2: Direct Mode (Full Configuration)

Pass complete simulation configuration directly:

```json
{
  "simulation": {
    "template": "mle_mbr_asm2d",
    "model_type": "ASM2d",
    "timeout_seconds": 600
  },
  "influent": {
    "flow_m3_d": 4000,
    "simplified": {
      "COD_mg_L": 350,
      "NH4_mg_L": 25,
      "TP_mg_L": 8,
      "TSS_mg_L": 220,
      "temperature_C": 20
    }
  },
  "convergence": {
    "run_to_convergence": true,
    "convergence_atol": 0.1
  }
}
```

#### Mode 3: Legacy Mode (v8 Compatibility)

Support existing v8 parameter format:

```json
{
  "legacy_mode": true,
  "template": "mle_mbr_asm2d",
  "flow_m3_d": 4000,
  "COD_mg_L": 350,
  "NH4_mg_L": 25,
  "TP_mg_L": 8,
  "TSS_mg_L": 220,
  "temperature_C": 20
}
```

### Environment Parameters (Retained - Hardcoded in Workflow)

```json
{
  "server_ip": "34.28.104.162",
  "server_port": "8080",
  "gotenberg_url": "http://34.28.104.162:3000",
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "YOUR_SUPABASE_JWT_KEY_HERE",
  "supabase_bucket": "panicleDevelop_1",
  "ai_model": "gpt-4o",
  "override_prompt": "",
  "session_id": "",
  "analysis_type": "WasteWater"
}
```

---

## Simulation Input JSON Schema

Full schema for Direct Mode (also stored in `studies.config` for Study Mode):

```json
{
  "$schema": "qsdsan-simulation-input-v1",

  "simulation": {
    "template": "string (required)",
    "model_type": "string (required: ASM2d | mADM1 | ASM1)",
    "duration_days": "number (optional, default: 1.0)",
    "timestep_hours": "number (optional)",
    "timeout_seconds": "number (optional, default: 300)",
    "check_interval_seconds": "number (optional, default: 60)",
    "hard_cancel_buffer": "number (optional, default: 120)"
  },

  "influent": {
    "flow_m3_d": "number (required)",
    "temperature_K": "number (optional, default: 293.15)",
    "concentrations": {
      "// ASM2d components (mg/L) or mADM1 components (kg/m³)": ""
    },
    "simplified": {
      "COD_mg_L": "number",
      "NH4_mg_L": "number",
      "TP_mg_L": "number",
      "TSS_mg_L": "number",
      "temperature_C": "number",
      "cod_distribution": {
        "f_soluble": "number (default: 0.5)",
        "f_fermentable": "number (default: 0.5)",
        "f_acetate": "number (default: 0.15)"
      },
      "tss_distribution": {
        "f_inert": "number (default: 0.25)",
        "f_biodegradable_factor": "number (default: 0.5)"
      }
    }
  },

  "reactor_config": {
    "V_liq": "number (optional)",
    "V_gas": "number (optional)",
    "T": "number (optional)",
    "V_anoxic_m3": "number (optional)",
    "V_aerobic_m3": "number (optional)",
    "V_mbr_m3": "number (optional)",
    "DO_aerobic_mg_L": "number (optional, default: 2.3)"
  },

  "kinetic_params": {
    "// Only include parameters to override": "",
    "k_ac": "number (optional)",
    "K_ac": "number (optional)"
  },

  "convergence": {
    "run_to_convergence": "boolean (optional, default: false)",
    "convergence_atol": "number (optional, default: 0.1)",
    "convergence_rtol": "number (optional, default: 1e-3)",
    "max_duration_days": "number (optional)"
  },

  "srt_control": {
    "target_srt_days": "number (optional)",
    "srt_tolerance": "number (optional, default: 0.1)",
    "max_srt_iterations": "number (optional, default: 10)"
  },

  "output": {
    "report": "boolean (optional, default: true)",
    "diagram": "boolean (optional, default: true)"
  }
}
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         n8n Workflow (v9)                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    INPUT (Webhook or Manual)                       │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │  Option A: { "study_id": "dairy_baseline", "overrides": {...} }    │ │
│  │  Option B: { "simulation": {...}, "influent": {...}, ... }         │ │
│  │  Option C: { "legacy_mode": true, "template": "...", ... }         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                    │                                    │
│                                    ▼                                    │
│  ┌──────────────────┐    ┌────────────────────┐                        │
│  │  Env Parameters  │    │  Determine Input   │                        │
│  │  (Hardcoded)     │    │  Mode              │                        │
│  └────────┬─────────┘    └─────────┬──────────┘                        │
│           │                        │                                    │
│           │         ┌──────────────┼──────────────┐                    │
│           │         ▼              ▼              ▼                    │
│           │  ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│           │  │ Study Mode │ │Direct Mode │ │Legacy Mode │              │
│           │  │            │ │            │ │            │              │
│           │  │ Fetch from │ │ Use config │ │ Transform  │              │
│           │  │ Supabase   │ │ directly   │ │ to new     │              │
│           │  │            │ │            │ │ schema     │              │
│           │  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘              │
│           │        │              │              │                      │
│           │        └──────────────┼──────────────┘                      │
│           │                       ▼                                     │
│           │              ┌────────────────┐                             │
│           │              │ Apply Overrides│                             │
│           │              │ (if provided)  │                             │
│           │              └───────┬────────┘                             │
│           │                      │                                      │
│           ▼                      ▼                                      │
│    ┌──────────────────────────────────────┐                            │
│    │         Generate Session ID          │                            │
│    └──────────────────┬───────────────────┘                            │
│                       ▼                                                 │
│    ┌──────────────────────────────────────┐                            │
│    │         Prepare Simulation           │                            │
│    │  (Build API request from config)     │                            │
│    └──────────────────┬───────────────────┘                            │
│                       ▼                                                 │
│    ┌──────────────────────────────────────┐                            │
│    │         Submit to QSDsan API         │                            │
│    └──────────────────────────────────────┘                            │
│                       │                                                 │
│                       ▼                                                 │
│    ... (polling, results, AI analysis, uploads - same as v8) ...       │
│                                                                         │
│    ┌──────────────────────────────────────────────────────────────────┐│
│    │  Hierarchical Storage: {session_id}/{analysis_type}/...          ││
│    │  • PDF report  • CSV data  • JSON results  • AI analysis         ││
│    └──────────────────────────────────────────────────────────────────┘│
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Summary

### Files Created

| File | Description |
|------|-------------|
| `n8n/n8n-qsd-test/qsdsan-simulation-v9.json` | v9 workflow with dynamic input |
| `n8n/n8n-qsd-test/webhook-test-examples.ps1` | PowerShell test script with interactive menu |
| `n8n/n8n-qsd-test/webhook-test-examples.sh` | Bash/curl test script with interactive menu |
| `n8n/n8n-qsd-test/webhook-payloads.json` | JSON reference for all payload examples |

### Workflow Node Changes (v8 → v9)

#### Nodes Removed

| Node | Reason |
|------|--------|
| `WW Parameters` | Replaced by dynamic input |

#### Nodes Added

| Node | Purpose |
|------|---------|
| `Webhook Trigger` | Receive input from external systems |
| `Determine Input Mode` | Route based on input type (study/direct/legacy) |
| `Is Study Mode?` | Conditional branch for study mode |
| `Fetch Study Config` | Get config from Supabase (study mode) |
| `Is Legacy Mode?` | Conditional branch for legacy mode |
| `Transform Legacy` | Convert v8 format (legacy mode) |
| `Pass Direct Mode` | Handle direct JSON configuration |
| `Merge Input Modes` | Combine all three input paths |
| `Apply Overrides` | Deep merge overrides into config |

#### Nodes Modified

| Node | Changes |
|------|---------|
| `Generate Session ID` | Include study_id in auto-generated names |
| `Prepare Simulation` | Accept config from any mode; handle concentrations OR simplified |
| `Submit Simulation` | Include kinetic_params, reactor_config, convergence, srt_control |
| `Evaluate Status` | Get timeout from config instead of hardcoded WW Parameters |
| `Wait 60s` | Get check_interval from config |
| `Process Results` | Include study metadata (study_id, study_name, category) |
| `Generate Report` | New study banner, mode badge, v9 branding |
| `Generate CSV Data` | Include study metadata columns |
| `Upload AI Analysis` | Include v9 study context |

---

## Test Examples

### Available Studies (in Supabase)

| Study ID | Description |
|----------|-------------|
| `template_aerobic_mbr` | Template - Aerobic MBR (ASM2d) |
| `template_anaerobic_cstr` | Template - Anaerobic CSTR (mADM1) |
| `dairy_baseline` | Dairy Processing - Baseline |
| `brewery_baseline` | Brewery - Baseline |
| `winery_baseline` | Winery - Baseline |
| `soft_drink_baseline` | Soft Drink Manufacturing |
| `meat_processing_baseline` | Meat Processing |
| `fruit_vegetable_baseline` | Fruit & Vegetable Processing |
| `dairy_anaerobic` | Dairy - Anaerobic Treatment (mADM1) |

### Test Script Examples

| # | Mode | Description |
|---|------|-------------|
| 1 | Study | Dairy Baseline |
| 2 | Study | Dairy with Overrides |
| 3 | Study | Brewery Baseline |
| 4 | Study | Dairy Anaerobic (mADM1) |
| 5 | Direct | ASM2d Basic |
| 6 | Direct | With Convergence |
| 7 | Direct | With SRT Control |
| 8 | Legacy | v8 Compatible |
| 9 | Legacy | Explicit Flag (A/O MBR) |
| 10 | Direct | High-Strength Industrial |

### Quick Start

1. Update the webhook URL in the test script
2. In n8n, click on **Webhook Trigger** → **"Listen for Test Event"**
3. Run the PowerShell script: `.\webhook-test-examples.ps1`
4. Select an example from the menu
5. Watch the workflow execute in n8n

---

## Success Criteria

1. ✅ Study mode: Fetch and run any study from Supabase
2. ✅ Direct mode: Accept full JSON configuration
3. ✅ Legacy mode: Run v8-style inputs unchanged
4. ✅ Overrides: Apply partial overrides in any mode
5. ⏳ All 9 default studies execute successfully (requires testing)
6. ✅ External webhook triggering works
7. ✅ Hierarchical storage paths preserved from v8

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-03 | Initial planning (Dynamic JSON only) |
| 1.1 | 2026-02-04 | Updated baseline to v8.0 |
| 1.2 | 2026-02-04 | Merged Study Management (N8N-3) into this plan |
| 1.3 | 2026-02-04 | **Implementation complete** - v9 workflow created |
