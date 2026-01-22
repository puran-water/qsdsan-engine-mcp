"""
Phase 9: Mixed-Model Flowsheet Support Tests

Tests for mixed-model flowsheet construction where different units use different
process models (e.g., ASM2d aerobic zones connected to mADM1 anaerobic digesters
via junction units).

Run with: python -m pytest tests/test_mixed_model.py -v
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from utils.flowsheet_session import (
    FlowsheetSessionManager,
    FlowsheetSession,
    StreamConfig,
    UnitConfig,
)
from core.unit_registry import (
    normalize_model_name,
    get_junction_output_model,
    models_compatible,
    suggest_junction_for_conversion,
    validate_model_compatibility,
    JUNCTION_MODEL_TRANSFORMS,
    MODEL_ALIASES,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def temp_session_dir():
    """Create a temporary directory for session storage."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def session_manager(temp_session_dir):
    """Create a session manager with temp storage."""
    manager = FlowsheetSessionManager(sessions_dir=temp_session_dir)
    return manager


# =============================================================================
# Phase 9A: Junction Model Transform Registry Tests
# =============================================================================

class TestJunctionModelTransforms:
    """Test junction model transform registry and helpers."""

    def test_model_aliases_defined(self):
        """MODEL_ALIASES should be defined for known models."""
        assert "mADM1" in MODEL_ALIASES
        assert "ADM1p" in MODEL_ALIASES
        assert "ASM2d" in MODEL_ALIASES
        assert "ASM1" in MODEL_ALIASES

    def test_normalize_model_name_adm1p(self):
        """ADM1p and ADM1_p_extension should normalize to mADM1."""
        assert normalize_model_name("ADM1p") == "mADM1"
        assert normalize_model_name("ADM1_p_extension") == "mADM1"

    def test_normalize_model_name_passthrough(self):
        """Other model names should pass through unchanged."""
        assert normalize_model_name("mADM1") == "mADM1"
        assert normalize_model_name("ASM2d") == "ASM2d"
        assert normalize_model_name("ASM1") == "ASM1"
        assert normalize_model_name("ADM1") == "ADM1"

    def test_junction_transforms_cover_all_concrete_junctions(self):
        """JUNCTION_MODEL_TRANSFORMS should cover all 8 concrete junctions."""
        expected_junctions = {
            "ASM2dtomADM1",
            "mADM1toASM2d",
            "ASM2dtoADM1",
            "ADM1toASM2d",
            "ADM1ptomASM2d",
            "mASM2dtoADM1p",
            "ASMtoADM",
            "ADMtoASM",
        }
        assert set(JUNCTION_MODEL_TRANSFORMS.keys()) == expected_junctions

    def test_get_junction_output_model_asm2d_to_madm1(self):
        """ASM2dtomADM1 should convert ASM2d -> mADM1."""
        result = get_junction_output_model("ASM2dtomADM1")
        assert result == ("ASM2d", "mADM1")

    def test_get_junction_output_model_madm1_to_asm2d(self):
        """mADM1toASM2d should convert mADM1 -> ASM2d."""
        result = get_junction_output_model("mADM1toASM2d")
        assert result == ("mADM1", "ASM2d")

    def test_get_junction_output_model_adm1p_to_masm2d(self):
        """ADM1ptomASM2d should convert mADM1/ADM1p -> mASM2d (Codex fix)."""
        result = get_junction_output_model("ADM1ptomASM2d")
        # FIXED: Input model should be mADM1 (not ADM1)
        assert result == ("mADM1", "mASM2d")

    def test_get_junction_output_model_non_junction(self):
        """Non-junction units should return None."""
        assert get_junction_output_model("CSTR") is None
        assert get_junction_output_model("Mixer") is None
        assert get_junction_output_model("AnaerobicCSTRmADM1") is None

    def test_models_compatible_same(self):
        """Same model names should be compatible."""
        assert models_compatible("ASM2d", "ASM2d") is True
        assert models_compatible("mADM1", "mADM1") is True

    def test_models_compatible_aliases(self):
        """Model aliases should be compatible."""
        assert models_compatible("mADM1", "ADM1p") is True
        assert models_compatible("ADM1p", "mADM1") is True
        assert models_compatible("ADM1_p_extension", "mADM1") is True

    def test_models_compatible_different(self):
        """Different models should not be compatible."""
        assert models_compatible("ASM2d", "mADM1") is False
        assert models_compatible("ASM2d", "ADM1") is False
        assert models_compatible("ASM1", "ASM2d") is False

    def test_suggest_junction_asm2d_to_madm1(self):
        """Should suggest ASM2dtomADM1 for ASM2d -> mADM1 conversion."""
        suggestion = suggest_junction_for_conversion("ASM2d", ["mADM1"])
        assert suggestion is not None
        assert "ASM2dtomADM1" in suggestion

    def test_suggest_junction_asm2d_to_adm1(self):
        """Should suggest ASM2dtoADM1 for ASM2d -> ADM1 conversion."""
        suggestion = suggest_junction_for_conversion("ASM2d", ["ADM1"])
        assert suggestion is not None
        assert "ASM2dtoADM1" in suggestion

    def test_suggest_junction_madm1_to_asm2d(self):
        """Should suggest mADM1toASM2d for mADM1 -> ASM2d conversion."""
        suggestion = suggest_junction_for_conversion("mADM1", ["ASM2d"])
        assert suggestion is not None
        assert "mADM1toASM2d" in suggestion

    def test_suggest_junction_no_path(self):
        """Should return None when no junction path exists."""
        # ASM1 -> mADM1 has no direct junction
        suggestion = suggest_junction_for_conversion("ASM1", ["mADM1"])
        assert suggestion is None

    def test_validate_model_compatibility_with_normalized_names(self):
        """validate_model_compatibility should work with normalized model names."""
        # CSTR is compatible with ASM2d
        is_compat, error = validate_model_compatibility("CSTR", "ASM2d")
        assert is_compat is True

        # AnaerobicCSTRmADM1 is compatible with mADM1 (and aliases)
        is_compat, error = validate_model_compatibility("AnaerobicCSTRmADM1", "mADM1")
        assert is_compat is True

        # Should also work with alias
        is_compat, error = validate_model_compatibility("AnaerobicCSTRmADM1", "ADM1p")
        assert is_compat is True


# =============================================================================
# Phase 9B/9C: Model Zone Computation Tests
# =============================================================================

class TestModelZoneComputation:
    """Test model zone computation for mixed-model flowsheets."""

    def test_session_creation(self, session_manager):
        """Should be able to create a session."""
        session = session_manager.create_session(model_type="ASM2d")
        assert session is not None
        assert session.primary_model_type == "ASM2d"

    def test_add_asm2d_unit_to_asm2d_session(self, session_manager):
        """Adding ASM2d-compatible unit to ASM2d session should work."""
        session = session_manager.create_session(model_type="ASM2d")

        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100},
            )
        )

        result = session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="A1",
                unit_type="CSTR",
                params={"V_max": 100},
                inputs=["inf"],
            )
        )

        assert "unit_id" in result
        assert result["unit_id"] == "A1"

    def test_add_junction_to_asm2d_session(self, session_manager):
        """Adding junction unit to ASM2d session should work."""
        session = session_manager.create_session(model_type="ASM2d")

        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100},
            )
        )

        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="A1",
                unit_type="CSTR",
                params={"V_max": 100},
                inputs=["inf"],
            )
        )

        # Add junction (ASM2d -> mADM1)
        result = session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="J1",
                unit_type="ASM2dtomADM1",
                params={},
                inputs=["A1-0"],
            )
        )

        assert "unit_id" in result
        assert result["unit_id"] == "J1"


class TestComputeEffectiveModelAtUnit:
    """Test compute_effective_model_at_unit helper function."""

    def test_explicit_model_override(self, session_manager):
        """Explicit model_type should bypass zone check."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        # With explicit override
        effective, warnings = compute_effective_model_at_unit(
            session, [], explicit_model="mADM1"
        )
        assert effective == "mADM1"
        assert len(warnings) == 0

    def test_no_inputs_uses_session_primary(self, session_manager):
        """No inputs should use session primary model."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        effective, warnings = compute_effective_model_at_unit(session, [])
        assert effective == "ASM2d"

    def test_stream_input_uses_stream_model(self, session_manager):
        """Stream input should use stream's model if set."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        # Add stream with explicit model
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="madm1_stream",
                flow_m3_d=500,
                temperature_K=308.15,
                concentrations={"S_su": 10},
                model_type="mADM1",
            )
        )

        # Reload session to get updated state
        session = session_manager.get_session(session.session_id)

        effective, warnings = compute_effective_model_at_unit(
            session, ["madm1_stream"]
        )
        assert effective == "mADM1"

    def test_junction_output_model(self, session_manager):
        """Unit after junction should use junction's output model."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        # Add stream
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100},
            )
        )

        # Add CSTR
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="A1",
                unit_type="CSTR",
                params={"V_max": 100},
                inputs=["inf"],
            )
        )

        # Add junction
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="J1",
                unit_type="ASM2dtomADM1",
                params={},
                inputs=["A1-0"],
            )
        )

        # Reload session
        session = session_manager.get_session(session.session_id)

        # Unit after junction should see mADM1
        effective, warnings = compute_effective_model_at_unit(
            session, ["J1-0"]
        )
        assert effective == "mADM1"

    def test_fan_in_different_models_warns(self, session_manager):
        """Fan-in with different models should warn."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        # Add two streams with different models
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="asm2d_stream",
                flow_m3_d=500,
                temperature_K=293.15,
                concentrations={"S_F": 100},
                model_type="ASM2d",
            )
        )

        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="madm1_stream",
                flow_m3_d=500,
                temperature_K=308.15,
                concentrations={"S_su": 10},
                model_type="mADM1",
            )
        )

        # Reload session
        session = session_manager.get_session(session.session_id)

        # Mixer receiving streams from different models
        effective, warnings = compute_effective_model_at_unit(
            session, ["asm2d_stream", "madm1_stream"]
        )

        # Should warn about multiple models
        assert len(warnings) > 0
        assert "Multiple input models" in warnings[0]


# =============================================================================
# Integration Tests: Mixed-Model Flowsheet Construction
# =============================================================================

class TestMixedModelFlowsheets:
    """Test mixed-model flowsheet construction end-to-end."""

    def test_asm2d_session_with_junction_then_madm1_unit(self, session_manager):
        """Should allow mADM1 unit after ASM2dtomADM1 junction in ASM2d session."""
        from server import compute_effective_model_at_unit

        # Create ASM2d session
        session = session_manager.create_session(
            model_type="ASM2d",
            session_id="test_mixed"
        )

        # Add influent stream
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="INF",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100, "S_NH4": 30},
            )
        )

        # Add aerobic CSTR (ASM2d)
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="A1",
                unit_type="CSTR",
                params={"V_max": 100},
                inputs=["INF"],
            )
        )

        # Add junction (ASM2d -> mADM1)
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="J1",
                unit_type="ASM2dtomADM1",
                params={},
                inputs=["A1-0"],
            )
        )

        # Reload session
        session = session_manager.get_session(session.session_id)

        # Verify effective model after junction is mADM1
        effective, warnings = compute_effective_model_at_unit(
            session, ["J1-0"]
        )
        assert effective == "mADM1"

        # Add mADM1 reactor - should work because junction converts to mADM1
        # Note: This tests the validation logic, not actual unit creation
        # The full MCP tool test would require async execution

    def test_junction_chain_traversal(self, session_manager):
        """Junction chains should compute final output model correctly."""
        from server import compute_effective_model_at_unit

        session = session_manager.create_session(model_type="ASM2d")

        # Add stream
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="INF",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100},
            )
        )

        # Add unit
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="A1",
                unit_type="CSTR",
                params={"V_max": 100},
                inputs=["INF"],
            )
        )

        # Add first junction (ASM2d -> mADM1)
        session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="J1",
                unit_type="ASM2dtomADM1",
                params={},
                inputs=["A1-0"],
            )
        )

        # Reload session
        session = session_manager.get_session(session.session_id)

        # After J1, effective model is mADM1
        effective, _ = compute_effective_model_at_unit(session, ["J1-0"])
        assert effective == "mADM1"

    def test_model_agnostic_unit_in_mixed_flowsheet(self, session_manager):
        """Model-agnostic units (Mixer, Splitter) should work in any model zone."""
        session = session_manager.create_session(model_type="ASM2d")

        # Add stream
        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="INF",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 100},
            )
        )

        # Mixer is model-agnostic
        result = session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="M1",
                unit_type="Mixer",
                params={},
                inputs=["INF"],
            )
        )

        assert "unit_id" in result
        assert result["unit_id"] == "M1"

        # Splitter is also model-agnostic
        result = session_manager.add_unit(
            session.session_id,
            UnitConfig(
                unit_id="SP1",
                unit_type="Splitter",
                params={"split": 0.5},
                inputs=["M1-0"],
            )
        )

        assert "unit_id" in result
        assert result["unit_id"] == "SP1"


# =============================================================================
# Suggestion Tests
# =============================================================================

class TestJunctionSuggestions:
    """Test junction suggestion functionality."""

    def test_suggest_asm2d_to_madm1_junction(self):
        """Should suggest correct junction for ASM2d -> mADM1."""
        suggestion = suggest_junction_for_conversion("ASM2d", ["mADM1"])
        assert suggestion is not None
        assert "ASM2dtomADM1" in suggestion
        assert "ASM2d" in suggestion
        assert "mADM1" in suggestion

    def test_suggest_for_multiple_target_models(self):
        """Should find first matching junction for list of targets."""
        # AnaerobicCSTRmADM1 is compatible with mADM1
        suggestion = suggest_junction_for_conversion("ASM2d", ["mADM1", "ADM1"])
        assert suggestion is not None
        # Should suggest ASM2dtomADM1 (first match)
        assert "ASM2dtomADM1" in suggestion

    def test_no_suggestion_for_same_model(self):
        """No junction needed when models are the same."""
        suggestion = suggest_junction_for_conversion("ASM2d", ["ASM2d"])
        # ASM2d -> ASM2d doesn't need a junction
        assert suggestion is None
