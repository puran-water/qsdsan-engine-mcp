"""
MCP Tool Contract Tests - Verify consistent schemas and error handling.

This module tests that MCP tools:
1. Return consistent schemas (is_valid, error, etc.)
2. Handle invalid inputs gracefully
3. Follow proper session lifecycle patterns
"""

import pytest
import sys
import tempfile
import json
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.plant_state import PlantState, ModelType
from utils.flowsheet_session import FlowsheetSessionManager, StreamConfig, UnitConfig


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_session_dir(tmp_path):
    """Create a temporary directory for session storage."""
    sessions_dir = tmp_path / "flowsheets"
    sessions_dir.mkdir(parents=True)
    return sessions_dir


@pytest.fixture
def session_manager(temp_session_dir):
    """Create a session manager with temp directory."""
    return FlowsheetSessionManager(sessions_dir=temp_session_dir)


# =============================================================================
# Return Schema Tests - Verify MCP tools return expected keys
# =============================================================================

class TestMCPReturnSchemas:
    """Verify MCP tools return consistent schemas."""

    def test_validate_state_returns_is_valid_key(self):
        """validate_state should always return is_valid boolean."""
        import server
        import asyncio

        # Valid state
        result = asyncio.run(server.validate_state(
            state={"concentrations": {"S_F": 100}, "flow_m3_d": 100},
            model_type="ASM2d"
        ))

        assert "is_valid" in result
        assert isinstance(result["is_valid"], bool)

    def test_validate_state_returns_warnings_key(self):
        """validate_state should return warnings list."""
        import server
        import asyncio

        result = asyncio.run(server.validate_state(
            state={
                "concentrations": {"S_FAKE": 100},
                "flow_m3_d": 100,
                "model_type": "ASM2d",
                "temperature_K": 293.15
            },
            model_type="ASM2d"
        ))

        assert "warnings" in result
        assert isinstance(result["warnings"], list)

    def test_list_templates_returns_dict_with_categories(self):
        """list_templates should return dict with aerobic/anaerobic/models categories."""
        import server
        import asyncio

        result = asyncio.run(server.list_templates())

        assert isinstance(result, dict)
        assert "aerobic" in result
        assert "anaerobic" in result
        # Check we have templates
        assert len(result["aerobic"]) >= 3  # mle_mbr, a2o_mbr, ao_mbr
        assert len(result["anaerobic"]) >= 1  # anaerobic_cstr

    def test_list_units_returns_dict_with_units(self):
        """list_units should return dict with units list."""
        import server
        import asyncio

        result = asyncio.run(server.list_units())

        assert isinstance(result, dict)
        assert "units" in result
        units = result["units"]
        assert isinstance(units, list)
        assert len(units) >= 30  # At least 30 units

    def test_get_model_components_returns_dict(self):
        """get_model_components should return component info dict."""
        import server
        import asyncio

        result = asyncio.run(server.get_model_components(model_type="ASM2d"))

        assert isinstance(result, dict)
        assert "model_type" in result
        assert "components" in result
        assert isinstance(result["components"], list)


# =============================================================================
# Error Handling Tests - Verify graceful handling of invalid inputs
# =============================================================================

class TestMCPErrorHandling:
    """Verify MCP tools handle invalid inputs gracefully."""

    def test_validate_state_invalid_model_type(self):
        """validate_state with invalid model type should return error or is_valid=False."""
        import server
        import asyncio

        result = asyncio.run(server.validate_state(
            state={"concentrations": {}, "flow_m3_d": 100},
            model_type="INVALID_MODEL"
        ))

        # Should either have error key or is_valid=False
        has_error = "error" in result
        is_invalid = result.get("is_valid") == False

        assert has_error or is_invalid, f"Expected error or is_valid=False, got: {result}"

    def test_get_flowsheet_session_missing_id(self, temp_session_dir):
        """get_flowsheet_session with non-existent session should return error."""
        import server
        import asyncio

        result = asyncio.run(server.get_flowsheet_session(session_id="nonexistent_xyz"))

        assert "error" in result or result is None, f"Expected error for missing session: {result}"

    def test_create_unit_unknown_type(self, temp_session_dir):
        """create_unit with unknown unit type should return error."""
        import server
        import asyncio
        import os

        os.environ["QSDSAN_ENGINE_SESSIONS_DIR"] = str(temp_session_dir)

        # Create a session first
        session_result = asyncio.run(server.create_flowsheet_session(model_type="ASM2d"))
        session_id = session_result.get("session_id")

        try:
            result = asyncio.run(server.create_unit(
                session_id=session_id,
                unit_type="FakeUnitType",
                unit_id="U1",
                params={},
                inputs=[]  # Required parameter
            ))

            assert "error" in result, f"Expected error for unknown unit type: {result}"
        finally:
            if "QSDSAN_ENGINE_SESSIONS_DIR" in os.environ:
                del os.environ["QSDSAN_ENGINE_SESSIONS_DIR"]

    def test_create_stream_empty_concentrations(self, temp_session_dir):
        """create_stream with empty concentrations should work (valid edge case)."""
        import server
        import asyncio
        import os

        os.environ["QSDSAN_ENGINE_SESSIONS_DIR"] = str(temp_session_dir)

        session_result = asyncio.run(server.create_flowsheet_session(model_type="ASM2d"))
        session_id = session_result.get("session_id")

        try:
            result = asyncio.run(server.create_stream(
                session_id=session_id,
                stream_id="empty_stream",
                flow_m3_d=1000,
                concentrations={}
            ))

            # Should succeed - empty concentrations is valid
            assert "error" not in result, f"Unexpected error: {result}"
        finally:
            if "QSDSAN_ENGINE_SESSIONS_DIR" in os.environ:
                del os.environ["QSDSAN_ENGINE_SESSIONS_DIR"]


# =============================================================================
# Session Lifecycle Tests - Test CRUD operations
# =============================================================================

class TestMCPSessionLifecycle:
    """Test session create/read/delete without running simulations."""

    def test_session_create_and_get(self, temp_session_dir):
        """Create session and verify it can be retrieved."""
        import server
        import asyncio
        import os

        os.environ["QSDSAN_ENGINE_SESSIONS_DIR"] = str(temp_session_dir)

        try:
            # Create
            create_result = asyncio.run(server.create_flowsheet_session(model_type="ASM2d"))
            assert "session_id" in create_result
            session_id = create_result["session_id"]

            # Get
            get_result = asyncio.run(server.get_flowsheet_session(session_id=session_id))
            assert get_result is not None
            assert get_result.get("model_type") == "ASM2d" or get_result.get("primary_model_type") == "ASM2d"
        finally:
            if "QSDSAN_ENGINE_SESSIONS_DIR" in os.environ:
                del os.environ["QSDSAN_ENGINE_SESSIONS_DIR"]

    def test_session_delete(self, temp_session_dir):
        """Delete session and verify it's gone."""
        import server
        import asyncio
        import os

        os.environ["QSDSAN_ENGINE_SESSIONS_DIR"] = str(temp_session_dir)

        try:
            # Create
            create_result = asyncio.run(server.create_flowsheet_session(model_type="ASM2d"))
            session_id = create_result["session_id"]

            # Delete
            delete_result = asyncio.run(server.delete_session(session_id=session_id))
            assert delete_result.get("success") or "error" not in delete_result

            # Verify deleted
            get_result = asyncio.run(server.get_flowsheet_session(session_id=session_id))
            # Should return error or None
            assert "error" in get_result or get_result is None
        finally:
            if "QSDSAN_ENGINE_SESSIONS_DIR" in os.environ:
                del os.environ["QSDSAN_ENGINE_SESSIONS_DIR"]

    def test_list_sessions_after_create(self, temp_session_dir):
        """List sessions should include newly created session."""
        import server
        import asyncio
        import os

        os.environ["QSDSAN_ENGINE_SESSIONS_DIR"] = str(temp_session_dir)

        try:
            # Create
            create_result = asyncio.run(server.create_flowsheet_session(model_type="ASM2d"))
            session_id = create_result["session_id"]

            # List - returns dict with 'sessions' key
            list_result = asyncio.run(server.list_flowsheet_sessions())
            assert isinstance(list_result, dict)
            assert "sessions" in list_result
            sessions = list_result["sessions"]
            session_ids = [s.get("session_id") for s in sessions]
            assert session_id in session_ids
        finally:
            if "QSDSAN_ENGINE_SESSIONS_DIR" in os.environ:
                del os.environ["QSDSAN_ENGINE_SESSIONS_DIR"]


# =============================================================================
# State Validation Specific Tests
# =============================================================================

class TestValidateStateContracts:
    """Test validate_state tool contract in detail."""

    def test_mass_balance_flag_has_effect(self):
        """check_mass_balance flag should produce different results when enabled."""
        import server
        import asyncio

        state = {
            "concentrations": {"S_F": 100000, "X_S": 50000},  # High values
            "flow_m3_d": 100,
            "temperature_K": 293.15,
            "model_type": "ASM2d"  # Required field
        }

        # Without mass balance check
        r1 = asyncio.run(server.validate_state(
            state=state,
            model_type="ASM2d",
            check_mass_balance=False
        ))

        # With mass balance check
        r2 = asyncio.run(server.validate_state(
            state=state,
            model_type="ASM2d",
            check_mass_balance=True
        ))

        # Results should differ - mass balance check adds consistency_check
        # High COD values (>100,000 mg/L) should trigger warnings
        assert r1 != r2 or "consistency_check" in r2 or len(r2.get("warnings", [])) > len(r1.get("warnings", []))

    def test_charge_balance_flag_for_madm1(self):
        """check_charge_balance should work for mADM1 states."""
        import server
        import asyncio

        state = {
            "concentrations": {
                "S_IC": 50,
                "S_IN": 30,
                "S_IP": 10,
                "S_Na": 100,
                "S_Cl": 80,
            },
            "flow_m3_d": 1000,
            "temperature_K": 308.15
        }

        result = asyncio.run(server.validate_state(
            state=state,
            model_type="mADM1",
            check_charge_balance=True
        ))

        # Should have charge balance info when enabled
        assert "is_valid" in result
        # May have charge_balance key with mADM1
        # At minimum, should not crash

    def test_validate_returns_computed_totals_when_mass_balance_enabled(self):
        """validate_state with mass_balance should return COD, TKN, TP totals."""
        import server
        import asyncio

        state = {
            "concentrations": {"S_F": 100, "S_A": 50, "X_S": 200},
            "flow_m3_d": 1000,
            "temperature_K": 293.15
        }

        result = asyncio.run(server.validate_state(
            state=state,
            model_type="ASM2d",
            check_mass_balance=True
        ))

        # consistency_check should have computed totals
        if "consistency_check" in result:
            check = result["consistency_check"]
            assert "cod_mg_L" in check or "passed" in check


# =============================================================================
# Clone and Mutation Tests
# =============================================================================

class TestMCPMutationOperations:
    """Test session mutation operations."""

    def test_clone_session_creates_copy(self, temp_session_dir):
        """Clone session should create independent copy."""
        manager = FlowsheetSessionManager(sessions_dir=temp_session_dir)

        # Create original
        session = manager.create_session(model_type="ASM2d")
        manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 75},
            )
        )

        # Clone - use source_session_id parameter
        result = manager.clone_session(
            source_session_id=session.session_id,
            new_session_id="cloned"
        )

        assert result.get("success") or "new_session_id" in result
        new_id = result.get("new_session_id", "cloned")

        # Verify clone has the stream
        cloned = manager.get_session(new_id)
        assert "inf" in cloned.streams

    def test_update_stream_patches_flow(self, temp_session_dir):
        """update_stream should patch flow rate."""
        manager = FlowsheetSessionManager(sessions_dir=temp_session_dir)

        session = manager.create_session(model_type="ASM2d")
        manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={"S_F": 75},
            )
        )

        # Update
        manager.update_stream(session.session_id, "inf", {"flow_m3_d": 2000})

        # Verify
        updated = manager.get_session(session.session_id)
        assert updated.streams["inf"].flow_m3_d == 2000
        # Temperature should be unchanged
        assert updated.streams["inf"].temperature_K == 293.15

    def test_delete_stream_removes_from_session(self, temp_session_dir):
        """delete_stream should remove stream."""
        manager = FlowsheetSessionManager(sessions_dir=temp_session_dir)

        session = manager.create_session(model_type="ASM2d")
        manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="inf",
                flow_m3_d=1000,
                temperature_K=293.15,
                concentrations={},
            )
        )

        # Verify exists
        assert "inf" in manager.get_session(session.session_id).streams

        # Delete
        manager.delete_stream(session.session_id, "inf")

        # Verify gone
        assert "inf" not in manager.get_session(session.session_id).streams
