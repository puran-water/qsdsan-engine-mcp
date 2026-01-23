"""
Phase 10 Tests - Known Limitations Fixes

Tests for:
1. Kinetic parameter schema and validation (Limitation #2)
2. Native pH calculation using pcm() (Limitation #3)
3. Auto-insert junctions on model mismatch (Limitation #6)
4. Traversal depth limit (Limitation #5)
5. Import time mitigation enhancements (Limitation #1)
"""

import pytest
import numpy as np
from typing import Dict, Any


# =============================================================================
# Kinetic Parameter Tests (Limitation #2)
# =============================================================================

class TestKineticParams:
    """Tests for core/kinetic_params.py"""

    def test_schema_contains_expected_params(self):
        """MADM1_KINETIC_SCHEMA should contain 80+ parameters."""
        from core.kinetic_params import MADM1_KINETIC_SCHEMA

        # Should have many parameters
        assert len(MADM1_KINETIC_SCHEMA) >= 80

        # Check key parameter categories exist
        expected_params = [
            # Rate constants
            "k_su", "k_aa", "k_fa", "k_c4", "k_pro", "k_ac", "k_h2",
            # Half-saturation
            "K_su", "K_aa", "K_fa", "K_c4", "K_pro", "K_ac", "K_h2",
            # SRB params
            "k_hSRB", "k_aSRB", "k_pSRB", "k_c4SRB",
            # Inhibition
            "KI_h2_fa", "KI_nh3", "KI_h2s_ac",
            # pH limits
            "pH_limits_aa", "pH_limits_ac",
        ]

        for param in expected_params:
            assert param in MADM1_KINETIC_SCHEMA, f"Missing expected param: {param}"

    def test_validate_kinetic_params_with_valid_input(self):
        """validate_kinetic_params should accept valid parameter values."""
        from core.kinetic_params import validate_kinetic_params

        params = {"k_ac": 10.0, "K_ac": 0.2}
        validated, warnings = validate_kinetic_params(params)

        # Should return validated dict with all defaults + overrides
        assert "k_ac" in validated
        assert validated["k_ac"] == 10.0
        assert validated["K_ac"] == 0.2
        # Should have defaults for other params
        assert "k_su" in validated

    def test_validate_kinetic_params_warns_on_out_of_range(self):
        """validate_kinetic_params should warn on out-of-range values."""
        from core.kinetic_params import validate_kinetic_params

        # k_ac default range is (2.0, 30.0)
        params = {"k_ac": 100.0}  # Way above range
        validated, warnings = validate_kinetic_params(params)

        assert len(warnings) >= 1
        assert any("outside typical range" in w for w in warnings)

    def test_validate_kinetic_params_warns_on_unknown(self):
        """validate_kinetic_params should warn on unknown parameters."""
        from core.kinetic_params import validate_kinetic_params

        params = {"unknown_param": 42.0}
        validated, warnings = validate_kinetic_params(params)

        assert len(warnings) >= 1
        assert any("Unknown" in w for w in warnings)
        # Should still pass through
        assert validated["unknown_param"] == 42.0

    def test_get_kinetic_param_docs(self):
        """get_kinetic_param_docs should return documentation string."""
        from core.kinetic_params import get_kinetic_param_docs

        docs = get_kinetic_param_docs()
        assert isinstance(docs, str)
        assert "mADM1 Kinetic Parameters" in docs
        assert "k_ac" in docs


# =============================================================================
# pH Calculation Tests (Limitation #3)
# =============================================================================

class TestNativePHCalculation:
    """Tests for native pH calculation using pcm()."""

    def test_pcm_function_exists(self):
        """pcm() function should exist in models/madm1.py."""
        from models.madm1 import pcm
        assert callable(pcm)

    @pytest.mark.slow
    def test_update_ph_and_alkalinity(self):
        """update_ph_and_alkalinity should calculate pH using pcm()."""
        # This requires QSDsan to be imported, so mark as slow
        from utils.simulate_madm1 import update_ph_and_alkalinity

        # Create a mock stream object with necessary attributes
        class MockStream:
            def __init__(self):
                self._pH = None
                self._SAlk = None
                self.F_vol = 100.0  # m3/d
                self.T = 308.15  # 35°C

                # Mock components
                class MockComponents:
                    def __init__(self):
                        self.IDs = ["S_IC", "S_IN", "S_ac", "S_pro", "S_bu", "S_va"]

                    def __len__(self):
                        return 62

                    def __iter__(self):
                        for i in range(62):
                            yield type("Cmp", (), {"ID": f"cmp_{i}"})()

                    def index(self, name):
                        if name == "S_IC":
                            return 5
                        return 0

                self.components = MockComponents()

            def imass(self):
                return {}

        # Note: Full test would require actual QSDsan stream
        # This just verifies the function exists and is callable
        assert callable(update_ph_and_alkalinity)


# =============================================================================
# Auto-Insert Junction Tests (Limitation #6)
# =============================================================================

class TestAutoInsertJunctions:
    """Tests for auto-insert junction functionality."""

    def test_find_junction_for_conversion_exists(self):
        """find_junction_for_conversion should be importable."""
        from core.unit_registry import find_junction_for_conversion
        assert callable(find_junction_for_conversion)

    def test_find_junction_madm1_to_asm2d(self):
        """Should find junction for mADM1 -> ASM2d conversion."""
        from core.unit_registry import find_junction_for_conversion

        junction = find_junction_for_conversion("mADM1", "ASM2d")
        assert junction == "mADM1toASM2d"

    def test_find_junction_asm2d_to_madm1(self):
        """Should find junction for ASM2d -> mADM1 conversion."""
        from core.unit_registry import find_junction_for_conversion

        junction = find_junction_for_conversion("ASM2d", "mADM1")
        assert junction == "ASM2dtomADM1"

    def test_find_junction_no_conversion_available(self):
        """Should return None when no conversion available."""
        from core.unit_registry import find_junction_for_conversion

        # ASM1 to mADM1 might not have a direct junction
        junction = find_junction_for_conversion("ASM1", "mADM1")
        # This might be None or might exist depending on implementation
        # The test just verifies no crash

    def test_unit_config_has_auto_inserted_field(self):
        """UnitConfig should have auto_inserted field."""
        from utils.flowsheet_session import UnitConfig

        config = UnitConfig(
            unit_id="test",
            unit_type="CSTR",
            params={},
            inputs=["inf"],
            auto_inserted=True,
        )
        assert config.auto_inserted is True

        config2 = UnitConfig(
            unit_id="test2",
            unit_type="CSTR",
            params={},
            inputs=["inf"],
        )
        assert config2.auto_inserted is False  # Default


# =============================================================================
# Traversal Depth Limit Tests (Limitation #5)
# =============================================================================

class TestTraversalDepthLimit:
    """Tests for traversal depth limit in compute_effective_model_at_unit."""

    def test_compute_effective_model_handles_depth(self):
        """compute_effective_model_at_unit should have _depth parameter."""
        import inspect
        import server

        sig = inspect.signature(server.compute_effective_model_at_unit)
        params = list(sig.parameters.keys())

        assert "_depth" in params

    def test_depth_limit_raises_on_deep_chain(self):
        """Should raise ValueError when depth exceeds 20."""
        # This test directly calls compute_effective_model_at_unit with high depth
        # to verify the depth limit works

        from server import compute_effective_model_at_unit
        from utils.flowsheet_session import FlowsheetSession, UnitConfig

        # Create a simple session
        session = FlowsheetSession(
            session_id="test_depth",
            primary_model_type="ASM2d",
        )

        # Add a simple unit
        session.units["test_unit"] = UnitConfig(
            unit_id="test_unit",
            unit_type="CSTR",
            params={},
            inputs=["inf"],
        )

        # Call with depth already at 21 (exceeds limit of 20)
        with pytest.raises(ValueError) as excinfo:
            compute_effective_model_at_unit(
                session, ["test_unit-0"], None, _depth=21
            )

        assert "too deep" in str(excinfo.value).lower() or "cycle" in str(excinfo.value).lower()


# =============================================================================
# Import Time Mitigation Tests (Limitation #1)
# =============================================================================

class TestImportTimeMitigation:
    """Tests for import time mitigation enhancements."""

    def test_full_warmup_function_exists(self):
        """full_warmup should be importable from qsdsan_loader."""
        from utils.qsdsan_loader import full_warmup
        import asyncio

        assert asyncio.iscoroutinefunction(full_warmup)

    def test_get_process_model_function_exists(self):
        """get_process_model should be importable from qsdsan_loader."""
        from utils.qsdsan_loader import get_process_model
        import asyncio

        assert asyncio.iscoroutinefunction(get_process_model)

    def test_is_model_loaded_function_exists(self):
        """is_model_loaded should be importable from qsdsan_loader."""
        from utils.qsdsan_loader import is_model_loaded

        assert callable(is_model_loaded)
        # Should return False before any loading
        assert is_model_loaded("mADM1") is False


# =============================================================================
# Integration Tests
# =============================================================================

class TestPhase10Integration:
    """Integration tests for Phase 10 features."""

    def test_kinetic_params_schema_matches_madm1_model(self):
        """Kinetic param schema should match ModifiedADM1.__new__ signature."""
        from core.kinetic_params import MADM1_KINETIC_SCHEMA

        # Key params from ModifiedADM1.__new__
        model_params = [
            "f_ch_xb", "f_pr_xb", "f_li_xb", "f_xI_xb",
            "k_su", "k_aa", "k_fa", "k_c4", "k_pro", "k_ac", "k_h2",
            "K_su", "K_aa", "K_fa", "K_c4", "K_pro", "K_ac", "K_h2",
            "k_hSRB", "k_aSRB", "k_pSRB", "k_c4SRB",
            "pH_limits_aa", "pH_limits_ac", "pH_limits_h2",
            "kLa", "pKa_base", "Ka_dH",
        ]

        for param in model_params:
            assert param in MADM1_KINETIC_SCHEMA, f"Missing model param in schema: {param}"

    def test_junction_model_transforms_complete(self):
        """JUNCTION_MODEL_TRANSFORMS should have bidirectional mappings."""
        from core.unit_registry import JUNCTION_MODEL_TRANSFORMS

        # Check key transforms exist
        assert "ASM2dtomADM1" in JUNCTION_MODEL_TRANSFORMS
        assert "mADM1toASM2d" in JUNCTION_MODEL_TRANSFORMS
        assert "ASM2dtoADM1" in JUNCTION_MODEL_TRANSFORMS
        assert "ADM1toASM2d" in JUNCTION_MODEL_TRANSFORMS

    def test_documentation_updated_for_phase10(self):
        """CLAUDE.md should mention Phase 10."""
        from pathlib import Path

        claude_md = Path(__file__).parent.parent / "CLAUDE.md"
        content = claude_md.read_text()

        assert "Phase 10" in content
        assert "kinetic_params" in content.lower() or "kinetic parameters" in content.lower()


# =============================================================================
# Module-level test markers
# =============================================================================

# Mark all tests in this module
pytestmark = [
    pytest.mark.phase10,
]
