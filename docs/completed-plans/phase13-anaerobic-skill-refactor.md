# Phase 13: Anaerobic Skill Refactor

**Status:** Complete (2026-02-05)

## Goal

Eliminate the anaerobic-design-mcp server dependency from the anaerobic-skill. The refactored skill orchestrates the full anaerobic design workflow using:
- **qsdsan-engine-mcp CLI** for simulation, validation, and ion-balance (pure CLI consumer)
- **Skill-scoped scripts** for heuristic sizing, mixing, rheology, and chemical dosing
- **The agent itself** for mADM1 state variable estimation (replacing the Codex GPT-5 agent)

## Changes to qsdsan-engine-mcp

### 1. Added `validate-composites` CLI subcommand

**File:** `cli.py`

New top-level command that computes bulk composites (COD, TSS, VSS, TKN, TP) from an mADM1 PlantState file and compares against user targets.

```bash
python cli.py validate-composites \
  --state plant_state.json \
  --targets '{"cod_mg_l": 7682, "tss_mg_l": 5500, "tkn_mg_l": 450, "tp_mg_l": 80}' \
  --tolerance 0.10 \
  --json-out
```

### 2. Added `validate-ion-balance` CLI subcommand

**File:** `cli.py`

New top-level command that solves for equilibrium pH using the engine's existing `pcm()` solver and compares against target pH.

```bash
python cli.py validate-ion-balance \
  --state plant_state.json \
  --target-ph 7.0 \
  --max-ph-deviation 0.5 \
  --json-out
```

### 3. Added `validate-finalize` CLI subcommand

Runs both composites + ion-balance in a single invocation:

```bash
python cli.py validate-finalize \
  --state plant_state.json \
  --targets '{"cod_mg_l": 7682, "ph": 7.0}' \
  --tolerance 0.10 \
  --max-ph-deviation 0.5 \
  --json-out
```

Exit codes: 0=pass, 1=composites fail, 2=ion-balance fail, 3=both fail.

### 4. Added tests

**File:** `tests/test_validation_cli.py`

15 tests covering all three commands.

## Changes to anaerobic-skill

### 5. Created `scripts/convert_to_plantstate.py`

Small utility for converting legacy annotated format to PlantState JSON:

```bash
python convert_to_plantstate.py \
  --adm1-state ./adm1_state.json \
  --flow-m3-d 100 \
  --temperature-c 35 \
  --output ./plant_state.json
```

### 6. Updated `references/madm1-state-estimation.md`

- Removed broken references to `utils.codex_validator` and `finalize_state.py`
- Updated validation CLI commands to use engine CLI
- Added scaffold approach starting from `test_madm1_state.json`
- Updated output format to PlantState JSON

### 7. Rewrote `SKILL.md`

New 10-step workflow with engine CLI commands:
1. Receive plant_state_in
2. Characterize feed - estimate mADM1 state variables
3. Validate composites via engine CLI
4. Validate ion-balance via engine CLI
5. Iterate (max 3 attempts)
6. Run heuristic sizing
7. Simulate via engine CLI
8. Evaluate results
9. Calculate chemical dosing
10. Generate outputs and register artifacts

### 8. Deleted `scripts/validate_cli.py`

No longer needed - replaced by engine CLI subcommands.

## Tool Mapping

| anaerobic-design-mcp Tool | Refactored Approach |
|---------------------------|---------------------|
| `elicit_basis_of_design()` | Agent collects params conversationally |
| `load_adm1_state()` | Agent writes PlantState JSON directly |
| `validate_adm1_state()` | `python cli.py validate-composites` |
| `compute_bulk_composites()` | `python cli.py validate-composites` (without targets) |
| `check_strong_ion_balance()` | `python cli.py validate-ion-balance` |
| `heuristic_sizing_ad()` | `scripts/heuristic_sizing.py` |
| `simulate_ad_system_tool()` | `python cli.py simulate -t anaerobic_cstr_madm1 --json-out` |
| `estimate_chemical_dosing()` | `scripts/chemical_dosing.py` |
| `generate_design_report()` | `python cli.py simulate ... --report` |
| `get_design_state()` | Agent tracks state in conversation + artifact directory |
| `reset_design()` | Agent starts fresh |
| Codex agent (state estimation) | Agent itself, guided by madm1-state-estimation.md |

## Test Results

- All 15 new validation CLI tests pass
- All 413 existing tests pass (21 slow tests skipped)
- Total: 428+ tests passing
