#!/usr/bin/env python
"""
Slow integration tests for all flowsheet templates.

Runs complete simulations for each template type and outputs results
to subdirectories in output_report/ for manual review.

Following NOTES_FOR_SKILLS.md guidance:
- Uses complete 62-component state for mADM1
- Uses complete ASM2d state for aerobic templates
"""

import json
import os
import sys
from pathlib import Path

# Path setup
TESTS_DIR = Path(__file__).parent
PROJECT_ROOT = TESTS_DIR.parent
OUTPUT_ROOT = PROJECT_ROOT / "output_report"

# Add project root to path
sys.path.insert(0, str(PROJECT_ROOT))

# Change to project root for consistent relative paths
os.chdir(PROJECT_ROOT)


def run_anaerobic_cstr_madm1():
    """Run mADM1 anaerobic CSTR simulation with complete 62-component state."""
    print("\n" + "=" * 60)
    print("RUNNING: anaerobic_cstr_madm1")
    print("=" * 60)

    from templates.anaerobic.cstr import build_and_run

    # Load complete 62-component state (per NOTES_FOR_SKILLS.md)
    with open(TESTS_DIR / "test_madm1_state.json") as f:
        state = json.load(f)

    output_dir = OUTPUT_ROOT / "anaerobic_cstr_madm1"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = build_and_run(
        influent_state=state,
        output_dir=output_dir
    )

    if result.get("status") == "completed":
        print(f"  [OK] Completed successfully")
        print(f"  COD removal: {result['performance']['COD_removal_pct']:.1f}%")
        print(f"  CH4 yield: {result['performance']['specific_CH4_yield_m3_kg_COD']:.4f} Nm3/kg COD")
        print(f"  Output: {output_dir}")
    else:
        print(f"  [FAIL] Failed: {result.get('error', 'Unknown error')}")

    return result


def run_mle_mbr_asm2d():
    """Run MLE-MBR simulation with ASM2d model."""
    print("\n" + "=" * 60)
    print("RUNNING: mle_mbr_asm2d")
    print("=" * 60)

    from templates.aerobic.mle_mbr import build_and_run

    # Load ASM2d state
    with open(TESTS_DIR / "test_asm2d_state.json") as f:
        state = json.load(f)

    output_dir = OUTPUT_ROOT / "mle_mbr_asm2d"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = build_and_run(
        influent_state=state,
        reactor_config=state.get("reactor_config"),
        duration_days=10.0,
        timestep_hours=1.0,
        output_dir=output_dir
    )

    if result.get("status") == "completed":
        print(f"  [OK] Completed successfully")
        perf = result.get("performance", {})
        print(f"  COD removal: {perf.get('COD_removal_pct', 'N/A')}%")
        print(f"  TN removal: {perf.get('TN_removal_pct', 'N/A')}%")
        print(f"  Output: {output_dir}")
    else:
        print(f"  [FAIL] Failed: {result.get('error', 'Unknown error')}")

    return result


def run_ao_mbr_asm2d():
    """Run A/O-MBR simulation with ASM2d model."""
    print("\n" + "=" * 60)
    print("RUNNING: ao_mbr_asm2d")
    print("=" * 60)

    from templates.aerobic.ao_mbr import build_and_run

    # Load ASM2d state
    with open(TESTS_DIR / "test_asm2d_state.json") as f:
        state = json.load(f)

    output_dir = OUTPUT_ROOT / "ao_mbr_asm2d"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = build_and_run(
        influent_state=state,
        reactor_config=state.get("reactor_config"),
        duration_days=10.0,
        timestep_hours=1.0,
        output_dir=output_dir
    )

    if result.get("status") == "completed":
        print(f"  [OK] Completed successfully")
        perf = result.get("performance", {})
        print(f"  COD removal: {perf.get('COD_removal_pct', 'N/A')}%")
        print(f"  TN removal: {perf.get('TN_removal_pct', 'N/A')}%")
        print(f"  Output: {output_dir}")
    else:
        print(f"  [FAIL] Failed: {result.get('error', 'Unknown error')}")

    return result


def run_a2o_mbr_asm2d():
    """Run A2O-MBR simulation with ASM2d model (includes EBPR)."""
    print("\n" + "=" * 60)
    print("RUNNING: a2o_mbr_asm2d")
    print("=" * 60)

    from templates.aerobic.a2o_mbr import build_and_run

    # Load ASM2d state
    with open(TESTS_DIR / "test_asm2d_state.json") as f:
        state = json.load(f)

    output_dir = OUTPUT_ROOT / "a2o_mbr_asm2d"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = build_and_run(
        influent_state=state,
        reactor_config=state.get("reactor_config"),
        duration_days=10.0,
        timestep_hours=1.0,
        output_dir=output_dir
    )

    if result.get("status") == "completed":
        print(f"  [OK] Completed successfully")
        perf = result.get("performance", {})
        print(f"  COD removal: {perf.get('COD_removal_pct', 'N/A')}%")
        print(f"  TN removal: {perf.get('TN_removal_pct', 'N/A')}%")
        print(f"  TP removal: {perf.get('TP_removal_pct', 'N/A')}%")
        print(f"  Output: {output_dir}")
    else:
        print(f"  [FAIL] Failed: {result.get('error', 'Unknown error')}")

    return result


def main():
    """Run all slow tests."""
    print("=" * 60)
    print("SLOW INTEGRATION TESTS FOR ALL FLOWSHEET TEMPLATES")
    print("=" * 60)
    print("\nFollowing NOTES_FOR_SKILLS.md guidance:")
    print("  - mADM1: Complete 62-component state from test_madm1_state.json")
    print("  - ASM2d: Complete state from test_asm2d_state.json")

    results = {}

    # Run all simulations
    try:
        results["anaerobic_cstr_madm1"] = run_anaerobic_cstr_madm1()
    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
        results["anaerobic_cstr_madm1"] = {"status": "error", "error": str(e)}

    try:
        results["mle_mbr_asm2d"] = run_mle_mbr_asm2d()
    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
        results["mle_mbr_asm2d"] = {"status": "error", "error": str(e)}

    try:
        results["ao_mbr_asm2d"] = run_ao_mbr_asm2d()
    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
        results["ao_mbr_asm2d"] = {"status": "error", "error": str(e)}

    try:
        results["a2o_mbr_asm2d"] = run_a2o_mbr_asm2d()
    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
        results["a2o_mbr_asm2d"] = {"status": "error", "error": str(e)}

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for template, result in results.items():
        status = result.get("status", "unknown")
        status_icon = "[OK]" if status == "completed" else "[FAIL]"
        print(f"  {status_icon} {template}: {status}")

    # Write summary to output_report
    summary_path = OUTPUT_ROOT / "test_summary.json"
    summary = {
        template: {
            "status": r.get("status"),
            "error": r.get("error") if r.get("status") != "completed" else None
        }
        for template, r in results.items()
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSummary written to: {summary_path}")
    print(f"Results in subdirectories of: output_report/")


if __name__ == "__main__":
    main()
