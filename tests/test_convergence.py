"""
Tests for convergence detection and run-to-convergence functionality.

Unit tests using lightweight test objects instead of mocks.
"""

import numpy as np
import pytest


# =============================================================================
# Lightweight test doubles (not mocks)
# Names use "Fake" prefix instead of "Test" to avoid pytest collection warnings
# =============================================================================

class FakeScope:
    """Lightweight scope object for testing convergence detection."""

    def __init__(self, time_series: np.ndarray, record: np.ndarray):
        self.time_series = time_series
        self.record = record

    def reset_cache(self):
        pass


class FakeComponents:
    """Lightweight components object for testing."""

    def __init__(self, ids: list):
        self._ids = ids

    @property
    def IDs(self):
        return self._ids

    def index(self, comp_id: str) -> int:
        return self._ids.index(comp_id)


class FakeStream:
    """Lightweight stream object for testing convergence detection."""

    def __init__(self, stream_id: str, component_ids: list, scope: "FakeScope" = None):
        self.ID = stream_id
        self.components = FakeComponents(component_ids)
        self.scope = scope
        self.sink = None  # Terminal stream

    def get_TSS(self):
        return 10.0  # Default TSS


def create_test_unit(unit_type: str, outs: list = None, v_max: float = None, v_liq: float = None):
    """Create a test unit with correct type name for type(unit).__name__ to work.

    This creates a dynamic class with the proper __name__ so production code
    using type(unit).__name__ will see the expected unit type.
    """
    # Create a class dynamically with the correct name
    UnitClass = type(unit_type, (), {
        '__init__': lambda self: None,  # Empty init
    })

    unit = UnitClass()
    unit.outs = outs or []
    unit.V_max = v_max
    unit.V_liq = v_liq
    return unit


# Legacy class for backwards compatibility (deprecated - use create_test_unit)
class FakeUnit:
    """Lightweight unit object for testing - use create_test_unit() for new tests."""

    def __init__(self, unit_type: str, outs: list = None, v_max: float = None, v_liq: float = None):
        self._unit_type = unit_type
        self.outs = outs or []
        self.V_max = v_max
        self.V_liq = v_liq


class FakeSystem:
    """Lightweight system object for testing."""

    def __init__(self, units: list = None, streams: list = None, products: list = None):
        self.units = units or []
        self.streams = streams or []
        self.products = products or []


# =============================================================================
# Convergence Detection Tests
# =============================================================================

class TestCheckSteadyState:
    """Tests for utils/convergence.py:check_steady_state()."""

    def test_converged_stream_returns_true(self):
        """Test that stable stream is detected as converged."""
        from utils.convergence import check_steady_state

        # Create stream with steady-state data (constant concentration)
        t = np.arange(0, 20, 0.5)  # 40 points over 20 days
        record = np.column_stack([
            np.ones_like(t) * 5.0,   # S_NH4 = 5 mg/L constant
            np.ones_like(t) * 10.0,  # S_NO3 = 10 mg/L constant
        ])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4", "S_NO3"], scope)

        components = {"effluent": ["S_NH4", "S_NO3"]}

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components=components,
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
        )

        assert converged is True
        assert metrics["converged"] is True
        assert "effluent" in metrics["streams"]

    def test_not_converged_stream_returns_false(self):
        """Test that changing stream is detected as not converged."""
        from utils.convergence import check_steady_state

        t = np.arange(0, 20, 0.5)
        # Linearly increasing concentration (not converged)
        # dC/dt = 1 mg/L/day which exceeds atol=0.1
        record = np.column_stack([t * 1.0])  # Slope = 1 mg/L/day
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
        )

        assert converged is False
        assert metrics["converged"] is False

    def test_oscillating_stream_detected(self):
        """Test that oscillating stream is detected as not converged."""
        from utils.convergence import check_steady_state

        t = np.arange(0, 20, 0.5)
        # Oscillating with zero mean slope but high range
        # This should trigger oscillation detection
        record = np.column_stack([
            10.0 + 5.0 * np.sin(2 * np.pi * t / 5)  # +/- 5 around 10
        ])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
            check_oscillation=True,
            oscillation_threshold=0.1,  # 10% of mean
        )

        assert converged is False
        assert metrics["converged"] is False

    def test_insufficient_data_not_converged(self):
        """Test that insufficient data is handled gracefully."""
        from utils.convergence import check_steady_state

        # Only 3 points - not enough for convergence check
        t = np.array([0, 0.5, 1.0])
        record = np.array([[1], [2], [3]])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,  # Needs ~11 points at 0.5 step
            t_step=0.5,
        )

        assert converged is False
        assert "error" in metrics["streams"]["effluent"]

    def test_abs_rel_tolerance(self):
        """Test combined abs+rel tolerance criterion."""
        from utils.convergence import check_steady_state

        t = np.arange(0, 20, 0.5)

        # Concentration with small slope relative to high mean value
        # Mean ~1000, slope 0.5 mg/L/day
        # Tolerance = 0.1 + 1e-3 * 1000 = 1.1 mg/L/d
        # Slope 0.5 < 1.1, should converge
        base = 1000 + np.arange(len(t)) * 0.5 * 0.5  # 0.5 per day * 0.5 step
        record = np.column_stack([base])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
            floor=1.0,
        )

        assert converged is True

    def test_no_scope_returns_error(self):
        """Test that stream without scope returns error."""
        from utils.convergence import check_steady_state

        stream = FakeStream("effluent", ["S_NH4"], scope=None)

        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
        )

        assert converged is False
        assert "error" in metrics["streams"]["effluent"]

    def test_invalid_components_returns_error_not_converged(self):
        """Test that invalid component IDs cause error, NOT false convergence.

        This is a critical production-safety test: if the caller passes only
        invalid component IDs, the system should report an error and NOT claim
        to be converged (which could lead to dangerous false positives).
        """
        from utils.convergence import check_steady_state

        # Create valid stream with constant data (would converge if components were valid)
        t = np.arange(0, 20, 0.5)
        record = np.column_stack([np.ones_like(t) * 5.0])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        # Ask to check an invalid component that doesn't exist in the stream
        converged, metrics = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["NOT_A_VALID_COMPONENT"]},
            window_days=5.0,
            t_step=0.5,
        )

        # CRITICAL: Should NOT converge with invalid components
        assert converged is False, "Should NOT report convergence with invalid component IDs"
        assert "error" in metrics["streams"]["effluent"], "Should have error for invalid components"
        assert "No valid components" in metrics["streams"]["effluent"]["error"]

    def test_end_time_slices_data_correctly(self):
        """Test that end_time parameter slices data for post-hoc scanning.

        This is critical for the single-run + post-hoc convergence detection.
        Without end_time, the convergence check always uses the final window,
        leading to incorrect converged_at times.
        """
        from utils.convergence import check_steady_state

        # Create stream that starts unstable then stabilizes:
        # Days 0-10: ramping up (not converged)
        # Days 10-20: stable (converged)
        t = np.arange(0, 20, 0.5)  # 40 points
        concentrations = np.where(t < 10, t * 1.0, 10.0)  # Ramp then flat
        record = np.column_stack([concentrations])
        scope = FakeScope(time_series=t, record=record)
        stream = FakeStream("effluent", ["S_NH4"], scope)

        # Check at t=8 (during ramp-up) - should NOT be converged
        converged_t8, metrics_t8 = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
            end_time=8.0,
        )
        assert converged_t8 is False, "Should NOT be converged at t=8 (during ramp-up)"
        assert metrics_t8["end_time"] == 8.0

        # Check at t=18 (stable region) - should be converged
        converged_t18, metrics_t18 = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
            end_time=18.0,
        )
        assert converged_t18 is True, "Should be converged at t=18 (stable region)"
        assert metrics_t18["end_time"] == 18.0

        # Without end_time (default) - checks final window, should be converged
        converged_final, metrics_final = check_steady_state(
            tracked_streams=[stream],
            components={"effluent": ["S_NH4"]},
            window_days=5.0,
            t_step=0.5,
            atol=0.1,
            rtol=1e-3,
        )
        assert converged_final is True
        assert metrics_final["end_time"] is None


class TestGetConvergenceComponentsForModel:
    """Tests for component selection per model type."""

    def test_asm2d_components(self):
        """Test default ASM2d convergence components."""
        from utils.convergence import get_convergence_components_for_model

        comps = get_convergence_components_for_model("ASM2d")
        assert "S_NH4" in comps["effluent"]
        assert "S_NO3" in comps["effluent"]
        assert "X_AUT" in comps["sludge"]
        assert "X_H" in comps["sludge"]

    def test_asm2d_with_phosphorus(self):
        """Test ASM2d components with phosphorus for EBPR."""
        from utils.convergence import get_convergence_components_for_model

        comps = get_convergence_components_for_model("ASM2d", include_phosphorus=True)
        assert "S_PO4" in comps["effluent"]
        assert "X_PP" in comps["sludge"]

    def test_madm1_components(self):
        """Test mADM1 convergence components."""
        from utils.convergence import get_convergence_components_for_model

        comps = get_convergence_components_for_model("mADM1")
        assert "S_ac" in comps["effluent"]
        assert "X_ac" in comps["sludge"]

    def test_asm1_components(self):
        """Test ASM1 convergence components."""
        from utils.convergence import get_convergence_components_for_model

        comps = get_convergence_components_for_model("ASM1")
        assert "S_NH" in comps["effluent"]
        assert "X_B_A" in comps["sludge"]

    def test_unknown_model_defaults_to_asm2d(self):
        """Test unknown model type falls back to ASM2d."""
        from utils.convergence import get_convergence_components_for_model

        comps = get_convergence_components_for_model("UnknownModel")
        assert "S_NH4" in comps["effluent"]


class TestRunToConvergenceWrapper:
    """Tests for utils/run_to_convergence.py."""

    def test_estimate_default_max_time_aerobic(self):
        """Test default max_time estimation for aerobic systems."""
        from utils.run_to_convergence import _estimate_default_max_time

        # System with MBR - use create_test_unit for proper type(unit).__name__
        mbr_unit = create_test_unit("CompletelyMixedMBR", v_max=400)
        cstr_unit = create_test_unit("CSTR", v_max=500)
        system = FakeSystem(units=[cstr_unit, mbr_unit])

        max_time = _estimate_default_max_time(system)
        assert max_time == 100.0  # MBR default

    def test_estimate_default_max_time_anaerobic(self):
        """Test default max_time estimation for anaerobic systems."""
        from utils.run_to_convergence import _estimate_default_max_time

        # Anaerobic system - use create_test_unit for proper type(unit).__name__
        unit = create_test_unit("AnaerobicCSTRmADM1", v_liq=1000)
        system = FakeSystem(units=[unit])

        max_time = _estimate_default_max_time(system)
        assert max_time == 500.0  # Anaerobic default

    def test_estimate_default_max_time_conventional(self):
        """Test default max_time for conventional AS (no MBR)."""
        from utils.run_to_convergence import _estimate_default_max_time

        # Conventional AS with clarifier - use create_test_unit for proper type(unit).__name__
        cstr = create_test_unit("CSTR", v_max=500)
        clarifier = create_test_unit("FlatBottomCircularClarifier", v_max=200)
        system = FakeSystem(units=[cstr, clarifier])

        max_time = _estimate_default_max_time(system)
        assert max_time == 80.0  # Conventional default


class TestDetectEffluentStreams:
    """Tests for flowsheet builder effluent stream detection."""

    def test_name_based_detection(self):
        """Test effluent detection by stream name."""
        from utils.flowsheet_builder import _detect_effluent_streams

        eff = FakeStream("effluent", ["S_NH4"])
        was = FakeStream("WAS", ["X_H"])
        system = FakeSystem(streams=[eff, was], products=[eff, was])

        detected = _detect_effluent_streams(system)
        assert len(detected) == 1
        assert detected[0].ID == "effluent"

    def test_clarifier_output_detection(self):
        """Test effluent detection from clarifier first output."""
        from utils.flowsheet_builder import _detect_effluent_streams

        eff = FakeStream("clr_out1", ["S_NH4"])
        und = FakeStream("clr_out2", ["X_H"])
        # Use create_test_unit for proper type(unit).__name__ matching
        clarifier = create_test_unit("FlatBottomCircularClarifier", outs=[eff, und])
        system = FakeSystem(units=[clarifier], streams=[eff, und])

        detected = _detect_effluent_streams(system)
        assert len(detected) >= 1
        assert detected[0].ID == "clr_out1"


class TestDetectSludgeStreams:
    """Tests for flowsheet builder sludge stream detection."""

    def test_clarifier_sludge_detection(self):
        """Test sludge detection from clarifier outputs."""
        from utils.flowsheet_builder import _detect_sludge_streams

        # Clarifier with 3 outputs - use create_test_unit for proper type matching
        eff_out = FakeStream("clarifier_eff", ["S_NH4"])
        ras_out = FakeStream("RAS", ["X_H"])
        was_out = FakeStream("WAS", ["X_H"])
        clarifier = create_test_unit("FlatBottomCircularClarifier", outs=[eff_out, ras_out, was_out])
        system = FakeSystem(units=[clarifier], streams=[eff_out, ras_out, was_out])

        detected = _detect_sludge_streams(system)

        # Should detect WAS (index 2) first
        assert len(detected) >= 1
        detected_ids = [s.ID for s in detected]
        assert "WAS" in detected_ids

    def test_mbr_sludge_detection(self):
        """Test sludge detection from MBR outputs."""
        from utils.flowsheet_builder import _detect_sludge_streams

        # MBR with 2 outputs - use create_test_unit for proper type matching
        permeate = FakeStream("permeate", ["S_NH4"])
        retentate = FakeStream("retain", ["X_H"])
        mbr = create_test_unit("CompletelyMixedMBR", outs=[permeate, retentate])
        system = FakeSystem(units=[mbr], streams=[permeate, retentate])

        detected = _detect_sludge_streams(system)

        # Should detect retentate (index 1)
        assert len(detected) >= 1
        detected_ids = [s.ID for s in detected]
        assert "retain" in detected_ids

    def test_name_based_sludge_detection(self):
        """Test sludge detection by stream name when no clarifier/MBR."""
        from utils.flowsheet_builder import _detect_sludge_streams

        # No clarifiers/MBRs, just named streams - use create_test_unit
        cstr = create_test_unit("CSTR")
        was = FakeStream("waste_sludge", ["X_H"])
        eff = FakeStream("effluent", ["S_NH4"])
        system = FakeSystem(units=[cstr], streams=[was, eff])

        detected = _detect_sludge_streams(system)

        assert len(detected) >= 1
        detected_ids = [s.ID for s in detected]
        assert "waste_sludge" in detected_ids


class TestBiomassRanking:
    """Tests for biomass score calculation and ranking."""

    def test_biomass_score_returns_numeric(self):
        """Test that _biomass_score returns a float, not a stream object."""
        from utils.flowsheet_builder import _biomass_score

        # Create stream with mock imass
        class StreamWithImass:
            def __init__(self, name, imass):
                self.ID = name
                self.imass = imass

        stream = StreamWithImass("test", {"X_AUT": 10.0, "X_H": 5.0})
        score = _biomass_score(stream)

        assert isinstance(score, (int, float)), f"Score should be numeric, got {type(score)}"
        assert score == 15.0, f"Expected 15.0, got {score}"

    def test_rank_and_dedupe_sorts_by_biomass(self):
        """Test that _rank_and_dedupe_by_biomass sorts by biomass score descending."""
        from utils.flowsheet_builder import _rank_and_dedupe_by_biomass

        # Create streams with different biomass scores
        class StreamWithImass:
            def __init__(self, name, imass):
                self.ID = name
                self.imass = imass

        s1 = StreamWithImass("low_biomass", {"X_AUT": 1.0})
        s2 = StreamWithImass("high_biomass", {"X_AUT": 100.0})
        s3 = StreamWithImass("medium_biomass", {"X_AUT": 10.0})

        result = _rank_and_dedupe_by_biomass([s1, s2, s3])

        # Should be sorted by biomass descending: high, medium, low
        assert len(result) == 3
        assert result[0].ID == "high_biomass", f"First should be highest biomass, got {result[0].ID}"
        assert result[1].ID == "medium_biomass", f"Second should be medium biomass, got {result[1].ID}"
        assert result[2].ID == "low_biomass", f"Third should be lowest biomass, got {result[2].ID}"

    def test_rank_and_dedupe_removes_duplicates(self):
        """Test that _rank_and_dedupe_by_biomass removes duplicate streams."""
        from utils.flowsheet_builder import _rank_and_dedupe_by_biomass

        class StreamWithImass:
            def __init__(self, name, imass):
                self.ID = name
                self.imass = imass

        s1 = StreamWithImass("stream1", {"X_AUT": 1.0})

        # Pass same stream multiple times
        result = _rank_and_dedupe_by_biomass([s1, s1, s1])

        assert len(result) == 1
        assert result[0].ID == "stream1"


class TestSimulateCompiledSystemConvergence:
    """Tests for simulate_compiled_system with convergence mode."""

    def test_convergence_parameters_in_signature(self):
        """Test that convergence parameters are in function signature."""
        from utils.flowsheet_builder import simulate_compiled_system
        import inspect

        sig = inspect.signature(simulate_compiled_system)
        params = sig.parameters

        assert "run_to_convergence" in params
        assert "convergence_atol" in params
        assert "convergence_rtol" in params
        assert "check_interval_days" in params
        assert "max_duration_days" in params

    def test_convergence_defaults(self):
        """Test default values for convergence parameters."""
        from utils.flowsheet_builder import simulate_compiled_system
        import inspect

        sig = inspect.signature(simulate_compiled_system)
        params = sig.parameters

        assert params["run_to_convergence"].default is False
        assert params["convergence_atol"].default == 0.1
        assert params["convergence_rtol"].default == 1e-3
        assert params["check_interval_days"].default == 2.0


class TestDefaultConvergenceComponents:
    """Tests for DEFAULT_CONVERGENCE_COMPONENTS constant."""

    def test_all_models_have_effluent_and_sludge(self):
        """Test all model types have effluent and sludge components."""
        from utils.convergence import DEFAULT_CONVERGENCE_COMPONENTS

        for model_type, comps in DEFAULT_CONVERGENCE_COMPONENTS.items():
            assert "effluent" in comps, f"{model_type} missing effluent"
            assert "sludge" in comps, f"{model_type} missing sludge"
            assert len(comps["effluent"]) > 0, f"{model_type} has empty effluent list"
            assert len(comps["sludge"]) > 0, f"{model_type} has empty sludge list"


# =============================================================================
# Module exports for pytest
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
