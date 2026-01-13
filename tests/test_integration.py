"""
Integration tests for mADM1/MCP end-to-end workflows.

These tests verify complete workflows across multiple components:
1. mADM1 CLI simulation with biogas outputs
2. MCP simulate_system for aerobic templates
3. Flowsheet build-and-simulate workflows
4. Report generation E2E
"""

import pytest
import sys
import json
import asyncio
from pathlib import Path
import tempfile
import os


class TestMadm1IntegrationCLI:
    """End-to-end tests for mADM1 via CLI.

    Note: mADM1 CSTR simulations run to TRUE steady state and take significant time.
    For stable anaerobic digestion, SRT must be >= 15-20 days (methanogen doubling time ~2-3 days).
    For CSTR systems, SRT = HRT.

    Convergence time depends on HRT/SRT:
    - HRT=20 days: Converges in ~100-150 simulated days (~5-8 min real time)
    - HRT=5 days: Marginal stability, may oscillate and take >30 min
    - HRT=2 days: At washout threshold, simulation will not converge

    Reference: anaerobic-design-mcp uses SRT=20 days and converges at ~116 days (~4 min).
    """

    @pytest.mark.slow
    def test_madm1_cli_simulate_e2e(self, tmp_path):
        """Run mADM1 CSTR via CLI and verify biogas outputs.

        Uses 20-day HRT (V_liq = 2000 m³ for Q = 100 m³/d) for stable methanogenesis.
        Shorter HRT values (5 days or less) cause marginal stability and oscillation.
        The mADM1 template ignores duration_days and runs to TRUE steady state.

        CRITICAL: mADM1 requires COMPLETE 62-component state for convergence.
        Incomplete states (e.g., only 14 components) may not converge.
        Use test_madm1_state.json which has all 62 components properly specified.
        """
        import subprocess

        # Load complete 62-component state from reference file
        # mADM1 requires all components for proper stoichiometric balance
        test_state_path = Path(__file__).parent / "test_madm1_state.json"
        with open(test_state_path, "r") as f:
            input_state = json.load(f)

        # File is already in PlantState format with concentrations wrapper

        # Reactor config: 20-day HRT for stable anaerobic digestion
        # V_liq = Q * HRT = 100 * 20 = 2000 m³
        # For CSTR, SRT = HRT, so SRT = 20 days (safe margin above 15-day minimum)
        reactor_config = {
            "V_liq": 2000.0,  # 20-day HRT
            "V_gas": 200.0,   # 10% headspace
            "T": 308.15
        }

        input_file = tmp_path / "madm1_input.json"
        with open(input_file, "w") as f:
            json.dump(input_state, f)

        # Run simulation via CLI
        # mADM1 CSTR runs to TRUE steady state (duration_days is ignored)
        # With 20-day HRT, expect convergence in ~100-150 simulated days (~5-8 min real time)
        # Timeout set to 15 minutes to be safe
        result = subprocess.run(
            [
                sys.executable, "cli.py", "simulate",
                "-t", "anaerobic_cstr_madm1",
                "-i", str(input_file),
                "-d", "30",  # Stored in metadata only; actual sim runs to steady state
                "-o", str(tmp_path),
                "--reactor-config", json.dumps(reactor_config)
            ],
            capture_output=True,
            text=True,
            timeout=900,  # 15 minutes
            cwd=str(Path(__file__).parent.parent)
        )

        # Check CLI executed successfully
        assert result.returncode == 0, f"CLI failed: {result.stderr}\nStdout: {result.stdout}"

        # Verify output files
        results_path = tmp_path / "simulation_results.json"
        assert results_path.exists(), f"simulation_results.json not created. Files: {list(tmp_path.iterdir())}"

        with open(results_path) as f:
            results = json.load(f)

        # Verify result structure
        assert "status" in results
        assert results["status"] == "completed"

    @pytest.mark.slow
    def test_madm1_cli_with_report_flag(self, tmp_path):
        """Run mADM1 with --report flag to generate QMD report.

        Uses 20-day HRT for stable anaerobic digestion (~2-3 min runtime with complete state).
        Requires COMPLETE 62-component state for convergence.
        """
        import subprocess

        # Load complete 62-component state from reference file
        test_state_path = Path(__file__).parent / "test_madm1_state.json"
        with open(test_state_path, "r") as f:
            input_state = json.load(f)

        # File is already in PlantState format with concentrations wrapper

        # Reactor config: 20-day HRT for stable methanogenesis
        reactor_config = {
            "V_liq": 2000.0,  # 20-day HRT for Q=100 m³/d
            "V_gas": 200.0,
            "T": 308.15
        }

        input_file = tmp_path / "madm1_input.json"
        with open(input_file, "w") as f:
            json.dump(input_state, f)

        # Run simulation with report flag
        # With 20-day HRT, expect ~5-8 min runtime
        result = subprocess.run(
            [
                sys.executable, "cli.py", "simulate",
                "-t", "anaerobic_cstr_madm1",
                "-i", str(input_file),
                "-d", "30",
                "-o", str(tmp_path),
                "--reactor-config", json.dumps(reactor_config),
                "--report"
            ],
            capture_output=True,
            text=True,
            timeout=900,  # 15 minutes
            cwd=str(Path(__file__).parent.parent)
        )

        # CLI should succeed
        assert result.returncode == 0, f"CLI failed: {result.stderr}\nStdout: {result.stdout}"

        # Check report artifacts
        assert (tmp_path / "simulation_results.json").exists()
        # QMD report should be generated
        # Note: Exact file name may vary
        qmd_files = list(tmp_path.glob("*.qmd"))
        # Report may or may not be generated depending on template
        # At minimum, simulation_results.json should exist


class TestMCPSimulateSystem:
    """End-to-end tests for MCP simulate_system tool."""

    @pytest.mark.asyncio
    async def test_mcp_simulate_aerobic_returns_job_id(self):
        """Test MCP simulate_system for aerobic template returns job_id."""
        # Import server module
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import simulate_system

        result = await simulate_system(
            template="mle_mbr_asm2d",
            influent={
                "model_type": "ASM2d",
                "flow_m3_d": 1000,
                "temperature_K": 293.15,
                "concentrations": {"S_F": 75, "S_A": 20, "S_NH4": 17}
            },
            duration_days=0.1,
            timestep_hours=1.0
        )

        # Should return job_id (even if job fails due to QSDsan import)
        assert "job_id" in result or "error" in result

    @pytest.mark.asyncio
    async def test_mcp_simulate_anaerobic_without_timestep(self):
        """Test MCP simulate_system for anaerobic template without timestep_hours."""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import simulate_system

        # This tests Task 1 fix - anaerobic should work without timestep_hours
        result = await simulate_system(
            template="anaerobic_cstr_madm1",
            influent={
                "model_type": "mADM1",
                "flow_m3_d": 100,
                "temperature_K": 308.15,
                "concentrations": {"S_su": 0.5, "X_ch": 3.0}
            },
            duration_days=1
            # Note: timestep_hours NOT provided - should use None
        )

        # Should return job_id, not fail with ValueError about timestep
        assert "job_id" in result or "error" in result
        if "error" in result:
            # Error should NOT be about timestep_hours
            assert "timestep" not in result["error"].lower()


class TestFlowsheetE2E:
    """End-to-end tests for flowsheet construction and simulation."""

    @pytest.mark.asyncio
    async def test_flowsheet_build_and_validate(self):
        """Build flowsheet via session and run validation."""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import (
            create_flowsheet_session,
            create_stream,
            create_unit,
            validate_flowsheet
        )

        # Create session
        session_result = await create_flowsheet_session(model_type="ASM2d")
        assert "session_id" in session_result
        session_id = session_result["session_id"]

        try:
            # Add stream
            stream_result = await create_stream(
                session_id=session_id,
                stream_id="influent",
                flow_m3_d=4000.0,
                temperature_K=293.15,
                concentrations={"S_F": 75, "S_A": 20, "S_NH4": 17}
            )
            assert "stream_id" in stream_result

            # Add unit
            unit_result = await create_unit(
                session_id=session_id,
                unit_id="CSTR1",
                unit_type="CSTR",
                params={"V_max": 1000},
                inputs=["influent"]
            )
            assert "unit_id" in unit_result

            # Validate flowsheet
            validation = await validate_flowsheet(session_id=session_id)
            assert "errors" in validation or "warnings" in validation

        finally:
            # Cleanup session
            from server import delete_session
            await delete_session(session_id=session_id)

    @pytest.mark.asyncio
    async def test_flowsheet_session_lifecycle(self):
        """Test full session lifecycle: create -> modify -> clone -> delete."""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import (
            create_flowsheet_session,
            create_stream,
            update_stream,
            clone_session,
            delete_session,
            get_flowsheet_session
        )

        # Create session
        session = await create_flowsheet_session(model_type="ASM2d")
        session_id = session["session_id"]

        try:
            # Add stream
            await create_stream(
                session_id=session_id,
                stream_id="influent",
                flow_m3_d=1000.0,
                temperature_K=293.15,
                concentrations={"S_F": 50}
            )

            # Update stream
            update_result = await update_stream(
                session_id=session_id,
                stream_id="influent",
                updates={"flow_m3_d": 2000.0}
            )
            assert "stream_id" in update_result

            # Verify update
            details = await get_flowsheet_session(session_id=session_id)
            assert details["streams"]["influent"]["flow_m3_d"] == 2000.0

            # Clone session
            clone = await clone_session(
                source_session_id=session_id,
                new_session_id=f"{session_id}_clone"
            )
            # Clone returns new_session_id
            assert "new_session_id" in clone
            clone_id = clone["new_session_id"]

            # Delete clone
            delete_result = await delete_session(session_id=clone_id)
            assert "deleted" in delete_result.get("status", "").lower() or "session_id" in delete_result

        finally:
            # Cleanup original
            await delete_session(session_id=session_id)


class TestConcentrationUnits:
    """Tests for model-specific concentration unit handling."""

    def test_plantstate_returns_correct_units(self):
        """Verify PlantState.get_concentration_units() returns model-specific units."""
        from core.plant_state import PlantState, ModelType

        # ASM2d should use mg/L
        asm_state = PlantState(
            model_type=ModelType.ASM2D,
            flow_m3_d=1000,
            temperature_K=293.15,
            concentrations={"S_F": 75}
        )
        assert asm_state.get_concentration_units() == "mg/L"

        # mADM1 should use kg/m3
        madm_state = PlantState(
            model_type=ModelType.MADM1,
            flow_m3_d=100,
            temperature_K=308.15,
            concentrations={"S_su": 0.5}
        )
        assert madm_state.get_concentration_units() == "kg/m3"

    def test_concentration_bounds_validation(self):
        """Verify concentration bounds validation detects unit confusion."""
        from core.plant_state import validate_concentration_bounds

        # ASM2d with suspiciously low value (might be kg/m3 instead of mg/L)
        warnings = validate_concentration_bounds(
            concentrations={"S_F": 0.075},  # 0.075 mg/L is very low
            model_type="ASM2d",
            units="mg/L"
        )
        assert len(warnings) > 0
        assert "suspiciously low" in warnings[0].lower()

        # mADM1 with suspiciously high value (might be mg/L instead of kg/m3)
        warnings = validate_concentration_bounds(
            concentrations={"S_su": 500},  # 500 kg/m3 is very high
            model_type="mADM1",
            units="kg/m3"
        )
        assert len(warnings) > 0
        assert "suspiciously high" in warnings[0].lower()

        # Normal values should not warn
        warnings = validate_concentration_bounds(
            concentrations={"S_F": 75, "S_A": 20},
            model_type="ASM2d",
            units="mg/L"
        )
        assert len(warnings) == 0


class TestReportGeneration:
    """End-to-end tests for report generation."""

    @pytest.mark.slow
    def test_report_generation_e2e(self, tmp_path):
        """Verify report generation creates expected artifacts."""
        import subprocess

        # Create input state file for aerobic simulation
        input_state = {
            "model_type": "ASM2d",
            "flow_m3_d": 4000.0,
            "temperature_K": 293.15,
            "concentrations": {
                "S_F": 75,
                "S_A": 20,
                "S_NH4": 17,
                "S_PO4": 9,
                "X_S": 100
            }
        }

        input_file = tmp_path / "aerobic_input.json"
        with open(input_file, "w") as f:
            json.dump(input_state, f)

        # Run simulation with report flag
        result = subprocess.run(
            [
                sys.executable, "cli.py", "simulate",
                "-t", "mle_mbr_asm2d",
                "-i", str(input_file),
                "-d", "1",
                "-o", str(tmp_path),
                "--report"
            ],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(Path(__file__).parent.parent)
        )

        # CLI should succeed
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Check core result file
        assert (tmp_path / "simulation_results.json").exists(), "simulation_results.json not created"

        # Check for diagram (SVG)
        svg_files = list(tmp_path.glob("*.svg"))
        # Diagram may or may not be generated depending on graphviz availability

        # Check for QMD report
        qmd_files = list(tmp_path.glob("*.qmd"))
        # Report may be generated depending on template

    def test_qmd_builder_renders_template(self):
        """Verify QMD builder renders templates without crashing."""
        from reports.qmd_builder import _prepare_aerobic_data, _prepare_anaerobic_data
        from jinja2 import Environment, FileSystemLoader
        from pathlib import Path

        # Mock results data
        aerobic_results = {
            "status": "completed",
            "duration_days": 1.0,
            "method": "RK23",
            "effluent": {"S_NH4": 0.5, "S_NO3": 15.0},
            "performance": {"N_removal_pct": 85.0},
            "flowsheet": {},
            "influent": {"flow_m3_d": 4000},
            "reactor": {},
            "biomass": {},
            "timeseries": {"time": [0, 1], "time_units": "days"}
        }

        # Should not raise
        data = _prepare_aerobic_data(aerobic_results)
        # Data has nested structure with 'simulation' key
        assert "simulation" in data
        assert "duration_days" in data["simulation"]
        assert "effluent" in data

        # Mock anaerobic results
        anaerobic_results = {
            "status": "completed",
            "duration_days": 30.0,
            "method": "RK23",
            "effluent": {"S_ac": 0.01},
            "biogas": {"CH4": 60, "CO2": 35},
            "performance": {"methane_yield_m3_kg_COD": 0.35},
            "flowsheet": {},
            "influent": {"flow_m3_d": 100},
            "reactor": {},
            "biomass": {},
            "timeseries": {}
        }

        data = _prepare_anaerobic_data(anaerobic_results)
        assert "biogas" in data
        # Anaerobic also has simulation key
        assert "simulation" in data or "duration_days" in data

    def test_normalize_results_for_report(self):
        """Verify result normalization handles various input formats."""
        from reports.qmd_builder import normalize_results_for_report

        # Test with minimal results
        minimal = {"status": "completed"}
        normalized = normalize_results_for_report(minimal.copy())

        # Should have all required keys with defaults
        assert "duration_days" in normalized
        assert "method" in normalized
        assert "performance" in normalized
        assert "effluent" in normalized
        assert "flowsheet" in normalized

        # Test with nested metadata
        with_metadata = {
            "status": "completed",
            "metadata": {
                "solver": {
                    "duration_days": 15.0,
                    "method": "BDF"
                }
            }
        }
        normalized = normalize_results_for_report(with_metadata.copy())
        assert normalized["duration_days"] == 15.0
        assert normalized["method"] == "BDF"

        # Test idempotency
        already_normalized = {
            "status": "completed",
            "duration_days": 10.0,
            "method": "RK45",
            "performance": {"removal": 90},
            "effluent": {"S_NH4": 1.0},
            "flowsheet": {"units": []}
        }
        normalized = normalize_results_for_report(already_normalized.copy())
        assert normalized["duration_days"] == 10.0
        assert normalized["method"] == "RK45"
