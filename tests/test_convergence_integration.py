"""
Integration tests for convergence-based simulation.

These tests run actual simulations and are marked as slow.
Run with: pytest tests/test_convergence_integration.py -v -m slow
"""

import json
import pytest
from pathlib import Path


@pytest.mark.slow
class TestMleMbrConvergence:
    """Integration tests for MLE-MBR convergence mode."""

    def test_fixed_duration_mode_works(self):
        """Test that fixed-duration mode still works correctly."""
        from templates.aerobic.mle_mbr import build_and_run

        # Load test state
        test_state_path = Path(__file__).parent / "test_asm2d_state.json"
        if not test_state_path.exists():
            pytest.skip("Test state file not found")

        with open(test_state_path) as f:
            state = json.load(f)

        influent_state = {
            "flow_m3_d": state.get("flow_m3_d", 4000),
            "temperature_K": state.get("temperature_K", 293.15),
            "concentrations": state.get("concentrations", {}),
        }

        # Run with fixed duration (original behavior)
        result = build_and_run(
            influent_state=influent_state,
            duration_days=5,  # Short for test
            run_to_convergence=False,
        )

        assert result["status"] == "completed"
        assert result["simulation"]["method"] == "RK23"
        assert result["simulation"]["run_to_convergence"] is False
        assert result["simulation"]["duration_days"] == 5

    def test_convergence_mode_parameters(self):
        """Test that convergence mode accepts all parameters."""
        from templates.aerobic.mle_mbr import build_and_run

        test_state_path = Path(__file__).parent / "test_asm2d_state.json"
        if not test_state_path.exists():
            pytest.skip("Test state file not found")

        with open(test_state_path) as f:
            state = json.load(f)

        influent_state = {
            "flow_m3_d": state.get("flow_m3_d", 4000),
            "temperature_K": state.get("temperature_K", 293.15),
            "concentrations": state.get("concentrations", {}),
        }

        # Run with convergence mode but short max_duration for testing
        result = build_and_run(
            influent_state=influent_state,
            run_to_convergence=True,
            convergence_atol=0.1,
            convergence_rtol=1e-3,
            check_interval_days=2.0,
            max_duration_days=10,  # Short for test
        )

        assert result["status"] == "completed"
        assert result["simulation"]["method"] == "BDF"
        assert result["simulation"]["run_to_convergence"] is True
        assert "converged_at_days" in result["simulation"]
        assert "convergence_status" in result["simulation"]

        # CRITICAL: Verify convergence status is valid (not 'error')
        # This catches silent simulation failures that would otherwise be masked
        conv_status = result["simulation"]["convergence_status"]
        assert conv_status in ("converged", "max_time_reached"), \
            f"Expected valid convergence status, got '{conv_status}'"

        # Verify convergence metrics don't have errors
        if "convergence_metrics" in result["simulation"]:
            metrics = result["simulation"]["convergence_metrics"]
            for stream_id, stream_data in metrics.get("streams", {}).items():
                assert "error" not in stream_data, \
                    f"Stream {stream_id} has error: {stream_data.get('error')}"


@pytest.mark.slow
class TestAoMbrConvergence:
    """Integration tests for A/O-MBR convergence mode."""

    def test_ao_mbr_convergence_mode(self):
        """Test A/O-MBR with convergence mode."""
        from templates.aerobic.ao_mbr import build_and_run

        test_state_path = Path(__file__).parent / "test_asm2d_state.json"
        if not test_state_path.exists():
            pytest.skip("Test state file not found")

        with open(test_state_path) as f:
            state = json.load(f)

        influent_state = {
            "flow_m3_d": state.get("flow_m3_d", 4000),
            "temperature_K": state.get("temperature_K", 293.15),
            "concentrations": state.get("concentrations", {}),
        }

        result = build_and_run(
            influent_state=influent_state,
            run_to_convergence=True,
            max_duration_days=10,  # Short for test
        )

        assert result["status"] == "completed"
        assert result["simulation"]["run_to_convergence"] is True

        # CRITICAL: Verify convergence status is valid (not 'error')
        conv_status = result["simulation"]["convergence_status"]
        assert conv_status in ("converged", "max_time_reached"), \
            f"Expected valid convergence status, got '{conv_status}'"


@pytest.mark.slow
class TestA2oMbrConvergence:
    """Integration tests for A2O-MBR convergence mode."""

    def test_a2o_mbr_convergence_with_phosphorus(self):
        """Test A2O-MBR includes phosphorus in convergence tracking."""
        from templates.aerobic.a2o_mbr import build_and_run

        test_state_path = Path(__file__).parent / "test_asm2d_state.json"
        if not test_state_path.exists():
            pytest.skip("Test state file not found")

        with open(test_state_path) as f:
            state = json.load(f)

        influent_state = {
            "flow_m3_d": state.get("flow_m3_d", 4000),
            "temperature_K": state.get("temperature_K", 293.15),
            "concentrations": state.get("concentrations", {}),
        }

        result = build_and_run(
            influent_state=influent_state,
            run_to_convergence=True,
            max_duration_days=10,  # Short for test
        )

        assert result["status"] == "completed"
        assert result["simulation"]["run_to_convergence"] is True
        # A2O should have EBPR info
        assert "ebpr" in result

        # CRITICAL: Verify convergence status is valid (not 'error')
        conv_status = result["simulation"]["convergence_status"]
        assert conv_status in ("converged", "max_time_reached"), \
            f"Expected valid convergence status, got '{conv_status}'"


@pytest.mark.slow
class TestFlowsheetBuilderConvergence:
    """Integration tests for flowsheet builder convergence mode."""

    def test_flowsheet_builder_convergence_imports(self):
        """Test that convergence imports work in flowsheet builder."""
        # This tests that the module structure is correct
        from utils.flowsheet_builder import simulate_compiled_system
        from utils.convergence import check_steady_state
        from utils.run_to_convergence import run_system_to_steady_state

        # Just verify imports work
        assert callable(simulate_compiled_system)
        assert callable(check_steady_state)
        assert callable(run_system_to_steady_state)


class TestConvergenceUtilitiesUnit:
    """Unit tests for convergence utilities that don't need QSDsan."""

    def test_default_components_asm2d(self):
        """Test default ASM2d component selection."""
        from utils.convergence import DEFAULT_CONVERGENCE_COMPONENTS

        asm2d = DEFAULT_CONVERGENCE_COMPONENTS["ASM2d"]
        assert "effluent" in asm2d
        assert "sludge" in asm2d
        assert "S_NH4" in asm2d["effluent"]
        assert "X_AUT" in asm2d["sludge"]

    def test_default_components_madm1(self):
        """Test default mADM1 component selection."""
        from utils.convergence import DEFAULT_CONVERGENCE_COMPONENTS

        madm1 = DEFAULT_CONVERGENCE_COMPONENTS["mADM1"]
        assert "effluent" in madm1
        assert "sludge" in madm1
        assert "S_ac" in madm1["effluent"]
        assert "X_ac" in madm1["sludge"]


# =============================================================================
# Module exports for pytest
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "slow"])
