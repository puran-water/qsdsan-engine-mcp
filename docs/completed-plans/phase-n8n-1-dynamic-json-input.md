# Phase N8N-1: Dynamic JSON Input Structure for n8n Workflow

**Status:** Planning
**Created:** 2026-02-03
**Author:** Claude Code

---

## Executive Summary

This plan proposes adapting the n8n workflow (v8) to accept a dynamic JSON input structure for **simulation parameters only**, provided by an upstream process. The existing **Env Parameters node is retained** as a hardcoded configuration (server endpoints, credentials, AI settings), maintaining the clean separation of concerns from v7. Only the WW Parameters node is replaced with a flexible, schema-validated JSON input that matches the full capabilities of the QSDsan engine.

---

## Current State Analysis

### Current Workflow Structure (v7)

The v7 workflow uses **two hardcoded parameter nodes**:

**1. Env Parameters Node** (Infrastructure)
```json
{
  "server_ip": "34.28.104.162",
  "server_port": "8080",
  "gotenberg_url": "http://34.28.104.162:3000",
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "eyJ...",
  "supabase_bucket": "panicleDevelop_1",
  "ai_model": "gpt-4o",
  "override_prompt": ""
}
```

**2. WW Parameters Node** (Simulation)
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

### Current Limitations

1. **Hardcoded COD/TSS distribution** - Fixed percentages (50%/15%/35%) don't allow for different wastewater characteristics
2. **Limited to ASM2d** - Model type is hardcoded; no mADM1 support
3. **No kinetic parameter control** - Cannot tune biological kinetics
4. **No reactor configuration** - Fixed reactor sizing
5. **No convergence control** - Fixed duration, no steady-state detection
6. **No SRT control** - Cannot target specific SRT values
7. **Limited influent specification** - Only 6 aggregate parameters vs 19+ ASM2d or 63 mADM1 components

---

## Proposed Solution

### Design Principles

1. **No extraneous parameters** - Every field must map to a QSDsan engine parameter
2. **Separation of concerns** - Environment/infrastructure config remains hardcoded in workflow; only simulation parameters come from upstream
3. **Backward compatible** - Support simplified inputs with sensible defaults
4. **Schema-validated** - Clear structure with type enforcement
5. **Model-aware** - Different schemas for ASM2d vs mADM1
6. **Upstream-providable** - Single JSON object from prior workflow step

### Environment Parameters (Retained from v7 - Hardcoded in Workflow)

The following parameters remain as a hardcoded "Env Parameters" Set node in the workflow. These are infrastructure/deployment settings that don't change per simulation:

```json
{
  "server_ip": "34.28.104.162",
  "server_port": "8080",
  "gotenberg_url": "http://34.28.104.162:3000",
  "supabase_url": "https://xxx.supabase.co",
  "supabase_key": "eyJ...",
  "supabase_bucket": "panicleDevelop_1",
  "ai_model": "gpt-4o",
  "override_prompt": ""
}
```

### Proposed Simulation Input JSON Schema (Replaces WW Parameters)

This JSON structure is provided by an upstream process and contains only simulation-specific parameters:

```json
{
  "$schema": "qsdsan-simulation-input-v1",

  "simulation": {
    "template": "string (required)",
    "model_type": "string (required: ASM2d | mADM1 | ASM1)",
    "duration_days": "number (optional, default: 1.0)",
    "timestep_hours": "number (optional)",
    "timeout_seconds": "number (optional, default: 300)",
    "check_interval_seconds": "number (optional, default: 60)"
  },

  "influent": {
    "flow_m3_d": "number (required)",
    "temperature_K": "number (optional, default: 293.15)",
    "concentrations": {
      "// ASM2d components (mg/L)": "",
      "S_O2": "number (optional, default: 0)",
      "S_F": "number (optional)",
      "S_A": "number (optional)",
      "S_I": "number (optional, default: 10)",
      "S_NH4": "number (optional)",
      "S_N2": "number (optional, default: 0)",
      "S_NO3": "number (optional, default: 0)",
      "S_PO4": "number (optional)",
      "S_ALK": "number (optional, default: 300)",
      "X_I": "number (optional)",
      "X_S": "number (optional)",
      "X_H": "number (optional, default: 30)",
      "X_PAO": "number (optional, default: 0)",
      "X_PP": "number (optional, default: 0)",
      "X_PHA": "number (optional, default: 0)",
      "X_AUT": "number (optional, default: 0)",
      "X_MeOH": "number (optional, default: 0)",
      "X_MeP": "number (optional, default: 0)",

      "// mADM1 components (kg/m³) - alternative": "",
      "S_su": "number", "S_aa": "number", "S_fa": "number",
      "// ... (63 components total)"
    },

    "// Simplified input (alternative to concentrations)": "",
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
    "// Anaerobic CSTR (mADM1)": "",
    "V_liq": "number (optional, default: flow × HRT)",
    "V_gas": "number (optional, default: V_liq × 0.1)",
    "T": "number (optional, default: 308.15 K)",
    "headspace_P": "number (optional, default: 101325 Pa)",

    "// Aerobic MBR systems": "",
    "V_anoxic_m3": "number (optional)",
    "V_aerobic_m3": "number (optional)",
    "V_mbr_m3": "number (optional)",
    "V_anaerobic_m3": "number (optional, A2O only)",
    "DO_aerobic_mg_L": "number (optional, default: 2.3)",
    "DO_mbr_mg_L": "number (optional, default: 2.2)",
    "SCR": "number (optional, default: 0.999)",
    "Q_ras_multiplier": "number (optional, default: 4.0)",
    "Q_was_m3_d": "number (optional)"
  },

  "kinetic_params": {
    "// Only include parameters you want to override": "",
    "// Hydrolysis rates (d⁻¹)": "",
    "q_ch_hyd": "number (optional)",
    "q_pr_hyd": "number (optional)",
    "q_li_hyd": "number (optional)",

    "// Uptake rates (d⁻¹)": "",
    "k_su": "number (optional)",
    "k_aa": "number (optional)",
    "k_fa": "number (optional)",
    "k_c4": "number (optional)",
    "k_pro": "number (optional)",
    "k_ac": "number (optional)",
    "k_h2": "number (optional)",

    "// Half-saturation (kg COD/m³)": "",
    "K_su": "number (optional)",
    "K_ac": "number (optional)",

    "// Yields (kg COD/kg COD)": "",
    "Y_su": "number (optional)",
    "Y_ac": "number (optional)",

    "// Decay rates (d⁻¹)": "",
    "b_su": "number (optional)",
    "b_ac": "number (optional)",

    "// Inhibition (various units)": "",
    "KI_nh3": "number (optional)",
    "KI_h2s_ac": "number (optional)"
  },

  "convergence": {
    "run_to_convergence": "boolean (optional, default: false)",
    "convergence_atol": "number (optional, default: 0.1 mg/L/d)",
    "convergence_rtol": "number (optional, default: 1e-3)",
    "check_interval_days": "number (optional, default: 2.0)",
    "max_duration_days": "number (optional)"
  },

  "srt_control": {
    "target_srt_days": "number (optional)",
    "srt_tolerance": "number (optional, default: 0.1)",
    "max_srt_iterations": "number (optional, default: 10)"
  },

  "output": {
    "report": "boolean (optional, default: true)",
    "diagram": "boolean (optional, default: true)",
    "include_components": "boolean (optional, default: false)",
    "track_streams": ["string array (optional)"],
    "export_state_to": "string (optional)"
  }
}
```

---

## Implementation Plan

### Phase 1: Input Schema Definition

**Task 1.1: Create JSON Schema File**
- Create `n8n/schemas/simulation-input-v1.schema.json`
- Define all fields with types, defaults, and descriptions
- Include validation rules (required fields, ranges, enums)

**Task 1.2: Document Schema**
- Create `n8n/docs/input-schema-reference.md`
- Provide examples for common scenarios
- Document model-specific requirements

### Phase 2: Workflow Modification

**Task 2.1: Create v8 Workflow Base**
- Copy v7 as starting point
- Add webhook/manual trigger with JSON input

**Task 2.2: Replace WW Parameter Node**
- Retain hardcoded "Env Parameters" node (unchanged)
- Remove hardcoded "WW Parameters" node
- Add "Parse Input JSON" code node for simulation parameters

**Task 2.3: Input Validation Node**
- Add JavaScript validation against schema
- Provide clear error messages for invalid input
- Apply defaults for missing optional fields

**Task 2.4: Update Prepare Simulation Node**
- Accept either `concentrations` (direct) or `simplified` input
- Perform COD/TSS distribution only when using simplified mode
- Pass through direct concentrations unchanged

**Task 2.5: Add Model-Aware Logic**
- Route to correct template based on `model_type`
- Validate concentrations match model requirements
- Handle unit conversion (mg/L vs kg/m³) appropriately

### Phase 3: API Request Construction

**Task 3.1: Build Simulation Request**
- Construct `/api/simulate_system` payload from parsed input
- Include all optional parameters when provided
- Map `kinetic_params` directly to request

**Task 3.2: Handle Convergence/SRT Options**
- Add `run_to_convergence` and related params
- Add `target_srt_days` and SRT control params
- Set appropriate timeouts based on convergence mode

### Phase 4: Testing & Documentation

**Task 4.1: Create Test Inputs**
- Minimal ASM2d input (simplified)
- Full ASM2d input (concentrations)
- mADM1 input with kinetic overrides
- Convergence mode input
- SRT control input

**Task 4.2: Update README**
- Document new v8 input format
- Provide migration guide from v7
- Add troubleshooting section

---

## Example Input Scenarios

> **Note:** All examples below show only the simulation input JSON. Environment parameters (server_ip, server_port, etc.) are configured separately in the workflow's Env Parameters node.

### Scenario 1: Simple ASM2d (Current Behavior)

```json
{
  "simulation": {
    "template": "mle_mbr_asm2d",
    "model_type": "ASM2d"
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
  }
}
```

### Scenario 2: Full ASM2d with Convergence

```json
{
  "simulation": {
    "template": "a2o_mbr_asm2d",
    "model_type": "ASM2d",
    "timeout_seconds": 600
  },
  "influent": {
    "flow_m3_d": 5000,
    "temperature_K": 298.15,
    "concentrations": {
      "S_O2": 0,
      "S_F": 100,
      "S_A": 30,
      "S_I": 15,
      "S_NH4": 35,
      "S_NO3": 0,
      "S_PO4": 10,
      "S_ALK": 350,
      "X_I": 55,
      "X_S": 180,
      "X_H": 30
    }
  },
  "reactor_config": {
    "V_anaerobic_m3": 150,
    "V_anoxic_m3": 200,
    "V_aerobic_m3": 300,
    "V_mbr_m3": 400,
    "DO_aerobic_mg_L": 2.5
  },
  "convergence": {
    "run_to_convergence": true,
    "convergence_atol": 0.1,
    "max_duration_days": 100
  },
  "output": {
    "report": true,
    "diagram": true
  }
}
```

### Scenario 3: Anaerobic mADM1 with Kinetic Tuning

```json
{
  "simulation": {
    "template": "anaerobic_cstr_madm1",
    "model_type": "mADM1",
    "timeout_seconds": 900
  },
  "influent": {
    "flow_m3_d": 100,
    "temperature_K": 308.15,
    "concentrations": {
      "S_su": 1.011,
      "S_aa": 1.522,
      "S_fa": 1.739,
      "S_ac": 0.038,
      "S_IC": 0.1896,
      "S_IN": 0.311,
      "S_IP": 0.028,
      "X_ch": 0.318,
      "X_pr": 1.061,
      "X_li": 0.743,
      "X_su": 0.011,
      "X_ac": 0.009,
      "S_SO4": 0.1,
      "X_hSRB": 0.006,
      "X_aSRB": 0.006
    }
  },
  "reactor_config": {
    "V_liq": 2000,
    "V_gas": 200,
    "T": 308.15
  },
  "kinetic_params": {
    "k_ac": 10.0,
    "K_ac": 0.12,
    "k_hSRB": 45.0,
    "KI_h2s_ac": 0.5
  },
  "convergence": {
    "run_to_convergence": true,
    "convergence_atol": 0.05,
    "max_duration_days": 60
  }
}
```

### Scenario 4: SRT-Controlled MLE-MBR

```json
{
  "simulation": {
    "template": "mle_mbr_asm2d",
    "model_type": "ASM2d",
    "timeout_seconds": 1200
  },
  "influent": {
    "flow_m3_d": 4000,
    "simplified": {
      "COD_mg_L": 400,
      "NH4_mg_L": 30,
      "TP_mg_L": 10,
      "TSS_mg_L": 250,
      "temperature_C": 18
    }
  },
  "srt_control": {
    "target_srt_days": 15,
    "srt_tolerance": 0.1,
    "max_srt_iterations": 10
  },
  "convergence": {
    "run_to_convergence": true,
    "convergence_atol": 0.1
  }
}
```

---

## Workflow Node Changes

### Nodes to Retain (Unchanged)
- `Env Parameters` (Set node) - Hardcoded infrastructure/deployment settings

### Nodes to Remove
- `WW Parameters` (Set node) - Replaced by dynamic JSON input

### Nodes to Add
1. **Webhook/Manual Trigger** - Receives simulation JSON input from upstream process
2. **Parse & Validate Input** - JavaScript node for schema validation
3. **Apply Defaults** - Merge defaults for missing optional fields
4. **Route by Model** - Branch based on model_type

### Nodes to Modify
1. **Prepare Simulation** - Accept either `concentrations` (direct) or `simplified` input
2. **Submit Simulation** - Include all optional parameters in request
3. **Check Status** - Adjust polling based on convergence mode timeout

---

## Validation Rules

### Required Fields (Simulation Input JSON)
- `simulation.template`
- `simulation.model_type`
- `influent.flow_m3_d`
- Either `influent.concentrations` OR `influent.simplified`

### Required Fields (Env Parameters - Hardcoded in Workflow)
- `server_ip`
- `server_port`

### Model-Specific Validation
- **ASM2d**: Concentrations in mg/L, warn if values > 50,000
- **mADM1**: Concentrations in kg/m³, warn if values < 0.001
- **Template/Model Match**: Validate template is compatible with model_type

### Range Validation
- `flow_m3_d`: > 0
- `temperature_K`: 273.15 - 373.15
- `convergence_atol`: > 0
- `srt_tolerance`: 0 - 1
- `timeout_seconds`: > 0

---

## Backward Compatibility

The v8 workflow will support a "legacy mode" that accepts the v7 WW parameter structure:

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

When `legacy_mode: true`, the workflow will transform this to the new schema internally. Environment parameters are always taken from the hardcoded Env Parameters node.

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `n8n/schemas/simulation-input-v1.schema.json` | Create | JSON Schema definition |
| `n8n/docs/input-schema-reference.md` | Create | Schema documentation |
| `n8n/n8n-qsd-test/qsdsan-simulation-v8.json` | Create | New workflow version |
| `n8n/n8n-qsd-test/README.md` | Modify | Update with v8 documentation |
| `n8n/examples/` | Create | Example input files |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing integrations | Medium | High | Maintain v7 alongside v8; add legacy mode |
| Schema validation too strict | Low | Medium | Start permissive, tighten based on feedback |
| Timeout issues with convergence | Medium | Medium | Dynamic timeout calculation based on input |
| Complex error messages | Medium | Low | Provide clear, actionable error messages |

---

## Success Criteria

1. v8 workflow accepts all documented QSDsan parameters
2. No extraneous fields that aren't used by the engine
3. Backward compatible with v7 inputs via legacy mode
4. Clear validation errors for invalid inputs
5. All four example scenarios execute successfully
6. Documentation complete and accurate

---

## Approval Checklist

- [ ] Schema covers all QSDsan engine parameters
- [ ] No unused/extraneous parameters included
- [ ] Backward compatibility approach acceptable
- [ ] Example scenarios cover target use cases
- [ ] Implementation phases are appropriately scoped

---

## Questions for Approval

1. Should legacy mode be supported, or require all upstream processes to migrate?
2. Are there any additional parameters from the QSDsan engine that should be exposed?
3. Should the schema include validation ranges (min/max) or leave that to the engine?
4. Is the phased implementation approach acceptable?

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         n8n Workflow (v8)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────┐    ┌───────────────────────────────────┐ │
│  │   Env Parameters     │    │  Simulation Input (from upstream) │ │
│  │   (Hardcoded Node)   │    │  (Webhook/Manual Trigger)         │ │
│  ├──────────────────────┤    ├───────────────────────────────────┤ │
│  │ • server_ip          │    │ • simulation                      │ │
│  │ • server_port        │    │ • influent                        │ │
│  │ • gotenberg_url      │    │ • reactor_config                  │ │
│  │ • supabase_url       │    │ • kinetic_params                  │ │
│  │ • supabase_key       │    │ • convergence                     │ │
│  │ • supabase_bucket    │    │ • srt_control                     │ │
│  │ • ai_model           │    │ • output                          │ │
│  │ • override_prompt    │    │                                   │ │
│  └──────────┬───────────┘    └─────────────┬─────────────────────┘ │
│             │                              │                       │
│             └──────────────┬───────────────┘                       │
│                            ▼                                       │
│                 ┌──────────────────────┐                           │
│                 │  Prepare Simulation  │                           │
│                 │  (Merge & Validate)  │                           │
│                 └──────────┬───────────┘                           │
│                            ▼                                       │
│                 ┌──────────────────────┐                           │
│                 │  Submit to QSDsan    │                           │
│                 │  Engine API          │                           │
│                 └──────────────────────┘                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```
