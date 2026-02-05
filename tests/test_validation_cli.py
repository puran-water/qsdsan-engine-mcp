"""
Tests for validate-composites, validate-ion-balance, and validate-finalize CLI subcommands.

These commands enable the anaerobic-skill to perform mADM1 state validation
via the CLI without importing the anaerobic-design-mcp server.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Test data paths
TEST_DIR = Path(__file__).parent
TEST_MADM1_STATE = TEST_DIR / "test_madm1_state.json"
CLI_PATH = TEST_DIR.parent / "cli.py"

# Use the same Python interpreter as the current process
PYTHON = sys.executable


def run_cli(args: list[str], timeout: int = 120) -> dict:
    """Run CLI command and return parsed JSON result."""
    cmd = [PYTHON, str(CLI_PATH)] + args + ["--json-out"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(TEST_DIR.parent),
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Failed to parse JSON output: {result.stdout}\nstderr: {result.stderr}")


class TestValidateComposites:
    """Tests for validate-composites CLI command."""

    def test_composites_without_targets(self):
        """Should return calculated composites without validation."""
        result = run_cli([
            "validate-composites",
            "--state", str(TEST_MADM1_STATE),
        ])

        assert result["status"] == "success"
        assert "calculated" in result
        calc = result["calculated"]

        # Check all expected keys are present
        assert "cod_mg_l" in calc
        assert "tss_mg_l" in calc
        assert "vss_mg_l" in calc
        assert "tkn_mg_l" in calc
        assert "tp_mg_l" in calc

        # Sanity checks for values (should be positive)
        assert calc["cod_mg_l"] > 0
        assert calc["tss_mg_l"] > 0
        assert calc["vss_mg_l"] > 0
        assert calc["tkn_mg_l"] > 0
        assert calc["tp_mg_l"] > 0

        # Should not have validation fields
        assert "valid" not in result
        assert "targets" not in result

    def test_composites_with_passing_targets(self):
        """Should validate and pass when targets are met."""
        # Get actual calculated values first
        result1 = run_cli([
            "validate-composites",
            "--state", str(TEST_MADM1_STATE),
        ])
        cod = result1["calculated"]["cod_mg_l"]

        # Use actual COD as target (should pass exactly)
        result = run_cli([
            "validate-composites",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": cod}),
            "--tolerance", "0.01",
        ])

        assert result["status"] == "success"
        assert result["valid"] is True
        assert result["deviations"]["cod_mg_l"] == 0.0

    def test_composites_with_failing_targets(self):
        """Should fail when targets are not met within tolerance."""
        result = run_cli([
            "validate-composites",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": 100000}),  # Way too high
            "--tolerance", "0.10",
        ])

        assert result["status"] == "success"
        assert result["valid"] is False
        assert "cod_mg_l" in result["deviations"]
        assert result["deviations"]["cod_mg_l"] > 0.10

    def test_composites_multiple_targets(self):
        """Should validate multiple targets simultaneously."""
        result = run_cli([
            "validate-composites",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({
                "cod_mg_l": 7000,
                "tss_mg_l": 1700,
            }),
            "--tolerance", "0.10",
        ])

        assert result["status"] == "success"
        assert "deviations" in result
        assert result["deviations"]["cod_mg_l"] is not None
        assert result["deviations"]["tss_mg_l"] is not None
        assert result["deviations"]["vss_mg_l"] is None  # Not in targets


class TestValidateIonBalance:
    """Tests for validate-ion-balance CLI command."""

    def test_ion_balance_basic(self):
        """Should compute equilibrium pH from state."""
        result = run_cli([
            "validate-ion-balance",
            "--state", str(TEST_MADM1_STATE),
            "--target-ph", "7.0",
            "--max-ph-deviation", "1.0",
        ])

        assert result["status"] == "success"
        assert "equilibrium_ph" in result
        assert "target_ph" in result
        assert "ph_deviation" in result
        assert "balanced" in result
        assert "nh3_kmol_m3" in result
        assert "co2_kmol_m3" in result

        # pH should be in reasonable range
        assert 5.0 < result["equilibrium_ph"] < 9.0

    def test_ion_balance_passes_with_wide_tolerance(self):
        """Should pass with wide pH tolerance."""
        result = run_cli([
            "validate-ion-balance",
            "--state", str(TEST_MADM1_STATE),
            "--target-ph", "7.0",
            "--max-ph-deviation", "2.0",
        ])

        assert result["status"] == "success"
        assert result["balanced"] is True

    def test_ion_balance_fails_with_strict_tolerance(self):
        """Should fail when pH deviation exceeds tolerance."""
        # First get equilibrium pH
        result1 = run_cli([
            "validate-ion-balance",
            "--state", str(TEST_MADM1_STATE),
            "--target-ph", "7.0",
            "--max-ph-deviation", "2.0",
        ])
        eq_ph = result1["equilibrium_ph"]

        # Now target a pH far from equilibrium with strict tolerance
        result = run_cli([
            "validate-ion-balance",
            "--state", str(TEST_MADM1_STATE),
            "--target-ph", str(eq_ph + 2.0),  # 2 units away
            "--max-ph-deviation", "0.5",
        ])

        assert result["status"] == "success"
        assert result["balanced"] is False
        assert result["ph_deviation"] > 0.5


class TestValidateFinalize:
    """Tests for validate-finalize CLI command."""

    def test_finalize_both_pass(self):
        """Should return overall_valid=True when both checks pass."""
        result = run_cli([
            "validate-finalize",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": 7000, "ph": 7.0}),
            "--tolerance", "0.20",
            "--max-ph-deviation", "2.0",
        ])

        assert result["status"] == "success"
        assert result["composites"]["valid"] is True
        assert result["ion_balance"]["valid"] is True
        assert result["overall_valid"] is True
        assert "passed" in result["message"].lower()

    def test_finalize_composites_fail(self):
        """Should return composites failure when targets not met."""
        result = run_cli([
            "validate-finalize",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": 100000}),  # Way too high
            "--tolerance", "0.10",
            "--max-ph-deviation", "2.0",
        ])

        assert result["status"] == "success"
        assert result["composites"]["valid"] is False
        assert result["ion_balance"]["valid"] is True
        assert result["overall_valid"] is False
        assert "composite" in result["message"].lower()

    def test_finalize_ion_balance_fail(self):
        """Should return ion-balance failure when pH target not met."""
        result = run_cli([
            "validate-finalize",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": 7000, "ph": 14.0}),  # Impossible pH
            "--tolerance", "0.20",
            "--max-ph-deviation", "0.1",  # Very strict
        ])

        assert result["status"] == "success"
        assert result["composites"]["valid"] is True
        assert result["ion_balance"]["valid"] is False
        assert result["overall_valid"] is False
        assert "ion-balance" in result["message"].lower()

    def test_finalize_both_fail(self):
        """Should return both failures when neither check passes."""
        result = run_cli([
            "validate-finalize",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"cod_mg_l": 100000, "ph": 14.0}),
            "--tolerance", "0.01",
            "--max-ph-deviation", "0.01",
        ])

        assert result["status"] == "success"
        assert result["composites"]["valid"] is False
        assert result["ion_balance"]["valid"] is False
        assert result["overall_valid"] is False
        assert "both" in result["message"].lower()

    def test_finalize_ph_in_targets(self):
        """Should extract pH from targets and use for ion balance."""
        result = run_cli([
            "validate-finalize",
            "--state", str(TEST_MADM1_STATE),
            "--targets", json.dumps({"ph": 7.5}),
            "--max-ph-deviation", "2.0",
        ])

        assert result["status"] == "success"
        assert result["ion_balance"]["target_ph"] == 7.5


class TestFileNotFound:
    """Tests for proper error handling when file not found."""

    def test_composites_file_not_found(self):
        """Should error when state file not found."""
        result = run_cli([
            "validate-composites",
            "--state", "/nonexistent/path.json",
        ])
        assert "error" in result

    def test_ion_balance_file_not_found(self):
        """Should error when state file not found."""
        result = run_cli([
            "validate-ion-balance",
            "--state", "/nonexistent/path.json",
        ])
        assert "error" in result

    def test_finalize_file_not_found(self):
        """Should error when state file not found."""
        result = run_cli([
            "validate-finalize",
            "--state", "/nonexistent/path.json",
        ])
        assert "error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
