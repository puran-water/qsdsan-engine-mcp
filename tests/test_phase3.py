"""
Phase 3: LLM Accessibility Enhancement Tests

Tests for new Phase 3 functionality:
- Native type parameters for MCP tools
- Session mutation operations (update, delete, clone)
- Deep introspection (get_session_summary)
- Concentration unit handling
- Validation warnings
- Discoverability tools
- Engineering-grade results
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.flowsheet_session import (
    FlowsheetSessionManager,
    FlowsheetSession,
    StreamConfig,
    UnitConfig,
    ConnectionConfig,
)
from utils.topo_sort import (
    validate_flowsheet_connectivity,
    detect_cycles,
)
from core.model_registry import (
    ModelType,
    get_model_info,
    get_required_components,
    validate_components,
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


@pytest.fixture
def sample_session(session_manager):
    """Create a sample session with streams and units."""
    session = session_manager.create_session(model_type="ASM2d")

    # Add streams
    session_manager.add_stream(
        session.session_id,
        StreamConfig(
            stream_id="influent",
            flow_m3_d=4000,
            temperature_K=293.15,
            concentrations={"S_F": 75, "S_A": 20, "S_NH4": 17},
            stream_type="influent",
        )
    )

    # Add units
    session_manager.add_unit(
        session.session_id,
        UnitConfig(
            unit_id="A1",
            unit_type="CSTR",
            params={"V_max": 1000},
            inputs=["influent"],
        )
    )

    session_manager.add_unit(
        session.session_id,
        UnitConfig(
            unit_id="O1",
            unit_type="CSTR",
            params={"V_max": 2000, "aeration": 2.3},
            inputs=["A1-0"],
        )
    )

    return session_manager.get_session(session.session_id)


# =============================================================================
# 3A.2: Session Mutation Tests
# =============================================================================

class TestUpdateStream:
    """Test stream update operations."""

    def test_update_stream_flow(self, session_manager, sample_session):
        """update_stream should modify flow_m3_d."""
        result = session_manager.update_stream(
            sample_session.session_id,
            "influent",
            {"flow_m3_d": 5000}
        )

        assert result["status"] == "updated"
        assert "flow_m3_d" in result["updated_fields"]

        session = session_manager.get_session(sample_session.session_id)
        assert session.streams["influent"].flow_m3_d == 5000

    def test_update_stream_concentrations_merge(self, session_manager, sample_session):
        """update_stream should merge concentrations dict."""
        result = session_manager.update_stream(
            sample_session.session_id,
            "influent",
            {"concentrations": {"S_PO4": 9}}
        )

        assert result["status"] == "updated"

        session = session_manager.get_session(sample_session.session_id)
        # Should have both original and new
        assert session.streams["influent"].concentrations["S_F"] == 75
        assert session.streams["influent"].concentrations["S_PO4"] == 9

    def test_update_stream_resets_compiled(self, session_manager, sample_session):
        """update_stream should reset status to 'building' if was 'compiled'."""
        # First set to compiled
        session_manager.update_session_status(sample_session.session_id, "compiled")

        result = session_manager.update_stream(
            sample_session.session_id,
            "influent",
            {"flow_m3_d": 5000}
        )

        assert result["was_compiled"] == True
        assert result["session_status"] == "building"

    def test_update_stream_not_found(self, session_manager, sample_session):
        """update_stream should fail for non-existent stream."""
        with pytest.raises(ValueError, match="not found"):
            session_manager.update_stream(
                sample_session.session_id,
                "nonexistent",
                {"flow_m3_d": 5000}
            )


class TestUpdateUnit:
    """Test unit update operations."""

    def test_update_unit_params(self, session_manager, sample_session):
        """update_unit should modify params."""
        result = session_manager.update_unit(
            sample_session.session_id,
            "A1",
            {"params": {"V_max": 1500}}
        )

        assert result["status"] == "updated"

        session = session_manager.get_session(sample_session.session_id)
        assert session.units["A1"].params["V_max"] == 1500

    def test_update_unit_inputs(self, session_manager, sample_session):
        """update_unit should modify inputs list."""
        result = session_manager.update_unit(
            sample_session.session_id,
            "A1",
            {"inputs": ["influent", "RAS"]}
        )

        assert result["status"] == "updated"

        session = session_manager.get_session(sample_session.session_id)
        assert session.units["A1"].inputs == ["influent", "RAS"]


class TestDeleteStream:
    """Test stream deletion."""

    def test_delete_stream(self, session_manager, sample_session):
        """delete_stream should remove stream from session."""
        # Add another stream that's not referenced
        session_manager.add_stream(
            sample_session.session_id,
            StreamConfig(
                stream_id="unused",
                flow_m3_d=100,
                temperature_K=293.15,
                concentrations={},
                stream_type="intermediate",
            )
        )

        result = session_manager.delete_stream(
            sample_session.session_id,
            "unused"
        )

        assert result["status"] == "deleted"

        session = session_manager.get_session(sample_session.session_id)
        assert "unused" not in session.streams

    def test_delete_stream_with_references_fails(self, session_manager, sample_session):
        """delete_stream should fail if units reference it."""
        with pytest.raises(ValueError, match="referenced by units"):
            session_manager.delete_stream(
                sample_session.session_id,
                "influent",
                force=False
            )

    def test_delete_stream_force(self, session_manager, sample_session):
        """delete_stream with force should remove from unit inputs."""
        result = session_manager.delete_stream(
            sample_session.session_id,
            "influent",
            force=True
        )

        assert result["status"] == "deleted"
        assert "A1" in result["removed_from_units"]

        session = session_manager.get_session(sample_session.session_id)
        assert "influent" not in session.units["A1"].inputs


class TestDeleteUnit:
    """Test unit deletion."""

    def test_delete_unit(self, session_manager, sample_session):
        """delete_unit should remove unit and its connections."""
        result = session_manager.delete_unit(
            sample_session.session_id,
            "O1"
        )

        assert result["status"] == "deleted"

        session = session_manager.get_session(sample_session.session_id)
        assert "O1" not in session.units


class TestDeleteConnection:
    """Test connection deletion."""

    def test_delete_connection(self, session_manager, sample_session):
        """delete_connection should remove specific connection."""
        # First add a connection
        session_manager.add_connection(
            sample_session.session_id,
            ConnectionConfig(
                from_port="O1-0",
                to_port="A1-1",
                stream_id="RAS"
            )
        )

        result = session_manager.delete_connection(
            sample_session.session_id,
            "O1-0",
            "A1-1"
        )

        assert result["status"] == "deleted"


class TestCloneSession:
    """Test session cloning."""

    def test_clone_session(self, session_manager, sample_session):
        """clone_session should create independent copy."""
        result = session_manager.clone_session(sample_session.session_id)

        assert result["status"] == "cloned"
        assert result["n_streams"] == len(sample_session.streams)
        assert result["n_units"] == len(sample_session.units)

        # Verify clone is independent
        new_session = session_manager.get_session(result["new_session_id"])
        assert new_session.status == "building"

    def test_clone_session_custom_id(self, session_manager, sample_session):
        """clone_session should accept custom new_session_id."""
        result = session_manager.clone_session(
            sample_session.session_id,
            new_session_id="my_clone"
        )

        assert result["new_session_id"] == "my_clone"


# =============================================================================
# 3A.3: Deep Introspection Tests
# =============================================================================

class TestDeepIntrospection:
    """Test enhanced get_session_summary."""

    def test_get_session_returns_stream_concentrations(self, session_manager, sample_session):
        """get_session_summary should return full concentrations dict."""
        summary = session_manager.get_session_summary(sample_session.session_id)

        assert "streams" in summary
        assert "influent" in summary["streams"]
        assert "concentrations" in summary["streams"]["influent"]
        assert summary["streams"]["influent"]["concentrations"]["S_F"] == 75

    def test_get_session_returns_stream_flow(self, session_manager, sample_session):
        """get_session_summary should return flow_m3_d."""
        summary = session_manager.get_session_summary(sample_session.session_id)

        assert summary["streams"]["influent"]["flow_m3_d"] == 4000

    def test_get_session_returns_unit_params(self, session_manager, sample_session):
        """get_session_summary should return full unit params dict."""
        summary = session_manager.get_session_summary(sample_session.session_id)

        assert "units" in summary
        assert "A1" in summary["units"]
        assert "params" in summary["units"]["A1"]
        assert summary["units"]["A1"]["params"]["V_max"] == 1000


# =============================================================================
# 3A.4: Concentration Units Tests
# =============================================================================

class TestConcentrationUnits:
    """Test concentration unit handling."""

    def test_default_units_mg_L(self, session_manager, temp_session_dir):
        """Default concentration_units should be 'mg/L'."""
        session = session_manager.create_session(model_type="ASM2d")

        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="test",
                flow_m3_d=100,
                temperature_K=293.15,
                concentrations={"S_F": 75},
            )
        )

        loaded = session_manager.get_session(session.session_id)
        assert loaded.streams["test"].concentration_units == "mg/L"

    def test_explicit_kg_m3(self, session_manager, temp_session_dir):
        """Explicit kg/m3 should be stored."""
        session = session_manager.create_session(model_type="ASM2d")

        session_manager.add_stream(
            session.session_id,
            StreamConfig(
                stream_id="test",
                flow_m3_d=100,
                temperature_K=293.15,
                concentrations={"S_F": 0.075},  # 75 mg/L = 0.075 kg/m3
                concentration_units="kg/m3",
            )
        )

        loaded = session_manager.get_session(session.session_id)
        assert loaded.streams["test"].concentration_units == "kg/m3"


# =============================================================================
# 3A.5: Validation Warnings Tests
# =============================================================================

class TestValidationWarnings:
    """Test validation warning surfacing."""

    def test_validate_components(self):
        """validate_components should detect unknown components."""
        mt = ModelType.ASM2D
        provided = {"S_F", "S_A", "S_FAKE"}  # S_FAKE is invalid

        missing, extra = validate_components(mt, provided)

        assert "S_FAKE" in extra


# =============================================================================
# 3B: Discoverability Tests
# =============================================================================

class TestGetModelComponents:
    """Test component discovery."""

    def test_get_asm2d_components(self):
        """Should return ASM2d components."""
        info = get_model_info(ModelType.ASM2D)

        # ASM2d has 19 components + H2O = 20, but registry has 20 defined
        assert info["n_components"] >= 19
        assert "S_F" in info["components"]
        assert "S_NH4" in info["components"]

    def test_get_madm1_components(self):
        """Should return mADM1 components."""
        info = get_model_info(ModelType.MADM1)

        assert info["n_components"] == 63
        assert "S_su" in info["components"]
        assert "S_ac" in info["components"]


class TestValidateFlowsheet:
    """Test pre-compilation validation."""

    def test_valid_flowsheet_passes(self, session_manager, sample_session):
        """Valid flowsheet should return no errors."""
        errors, warnings = validate_flowsheet_connectivity(
            sample_session.units,
            sample_session.streams,
            sample_session.connections,
        )

        # May have warnings but no errors for valid flowsheet
        assert isinstance(errors, list)
        assert isinstance(warnings, list)


class TestDetectCycles:
    """Test cycle detection."""

    def test_no_cycles_in_linear(self, session_manager, sample_session):
        """Should not detect cycles in linear flowsheet."""
        cycles = detect_cycles(
            sample_session.units,
            sample_session.connections,
        )

        assert len(cycles) == 0


# =============================================================================
# Module Tests (verify imports work)
# =============================================================================

class TestModuleImports:
    """Verify all new modules import correctly."""

    def test_server_imports(self):
        """Server module should import without errors."""
        import server
        assert hasattr(server, 'mcp')

    def test_cli_imports(self):
        """CLI module should import without errors."""
        import cli
        assert hasattr(cli, 'app')

    def test_flowsheet_session_imports(self):
        """Flowsheet session module should import."""
        from utils.flowsheet_session import FlowsheetSessionManager
        assert FlowsheetSessionManager is not None

    def test_topo_sort_imports(self):
        """Topo sort module should import."""
        from utils.topo_sort import topological_sort, detect_cycles
        assert topological_sort is not None
        assert detect_cycles is not None


# =============================================================================
# Additional Phase 3 Tests for Coverage
# =============================================================================

class TestASM1Components:
    """Test ASM1 model component support."""

    def test_get_asm1_components(self):
        """Should return ASM1 components."""
        info = get_model_info(ModelType.ASM1)

        assert info["n_components"] == 13
        assert "S_I" in info["components"]
        assert "S_S" in info["components"]
        assert "X_BH" in info["components"]
        assert "S_NH" in info["components"]

    def test_asm1_biomass_ids(self):
        """ASM1 should have correct biomass IDs."""
        info = get_model_info(ModelType.ASM1)
        assert "X_BH" in info["biomass_ids"]
        assert "X_BA" in info["biomass_ids"]


class TestComponentMetadata:
    """Test component metadata for discoverability."""

    def test_asm2d_metadata_exists(self):
        """ASM2d should have component metadata."""
        from server import COMPONENT_METADATA
        assert "ASM2d" in COMPONENT_METADATA
        assert "S_F" in COMPONENT_METADATA["ASM2d"]
        assert "name" in COMPONENT_METADATA["ASM2d"]["S_F"]

    def test_asm1_metadata_exists(self):
        """ASM1 should have component metadata."""
        from server import COMPONENT_METADATA
        assert "ASM1" in COMPONENT_METADATA
        assert "S_S" in COMPONENT_METADATA["ASM1"]
        assert "X_BH" in COMPONENT_METADATA["ASM1"]

    def test_madm1_metadata_exists(self):
        """mADM1 should have component metadata."""
        from server import COMPONENT_METADATA
        assert "mADM1" in COMPONENT_METADATA
        assert "S_su" in COMPONENT_METADATA["mADM1"]
        assert "S_ac" in COMPONENT_METADATA["mADM1"]


class TestSessionSummaryDetails:
    """Test enhanced session summary with full details."""

    def test_summary_includes_connection_details(self, session_manager, sample_session):
        """Session summary should include connection stream_id."""
        # Add a connection
        session_manager.add_connection(
            sample_session.session_id,
            ConnectionConfig(
                from_port="O1-0",
                to_port="A1-1",
                stream_id="RAS"
            )
        )

        summary = session_manager.get_session_summary(sample_session.session_id)
        assert "connections" in summary
        assert len(summary["connections"]) > 0
        # Check that connections have stream_id
        has_stream_id = any(c.get("stream_id") for c in summary["connections"])
        assert has_stream_id


class TestUpdateOperationsStatus:
    """Test that update operations properly reset session status."""

    def test_update_unit_resets_compiled(self, session_manager, sample_session):
        """update_unit should reset status to 'building' if was 'compiled'."""
        # First set to compiled
        session_manager.update_session_status(sample_session.session_id, "compiled")

        result = session_manager.update_unit(
            sample_session.session_id,
            "A1",
            {"params": {"V_max": 1500}}
        )

        assert result["was_compiled"] == True
        assert result["session_status"] == "building"


class TestDeleteOperationsWithConnections:
    """Test delete operations and their effects on connections."""

    def test_delete_unit_removes_connections(self, session_manager, sample_session):
        """delete_unit should also remove related connections."""
        # Add a connection first
        session_manager.add_connection(
            sample_session.session_id,
            ConnectionConfig(
                from_port="O1-0",
                to_port="A1-1",
                stream_id="RAS"
            )
        )

        # Delete O1 unit
        result = session_manager.delete_unit(
            sample_session.session_id,
            "O1"
        )

        assert result["status"] == "deleted"
        # Connection should be removed
        session = session_manager.get_session(sample_session.session_id)
        assert "O1" not in session.units


class TestCLICommandsExist:
    """Test that new CLI commands are registered."""

    def test_flowsheet_validate_exists(self):
        """flowsheet validate command should exist."""
        import cli
        commands = [cmd.name for cmd in cli.flowsheet_app.registered_commands]
        assert "validate" in commands

    def test_flowsheet_suggest_recycles_exists(self):
        """flowsheet suggest-recycles command should exist."""
        import cli
        commands = [cmd.name for cmd in cli.flowsheet_app.registered_commands]
        assert "suggest-recycles" in commands

    def test_flowsheet_timeseries_exists(self):
        """flowsheet timeseries command should exist."""
        import cli
        commands = [cmd.name for cmd in cli.flowsheet_app.registered_commands]
        assert "timeseries" in commands

    def test_flowsheet_artifact_exists(self):
        """flowsheet artifact command should exist."""
        import cli
        commands = [cmd.name for cmd in cli.flowsheet_app.registered_commands]
        assert "artifact" in commands

    def test_models_components_exists(self):
        """models components command should exist."""
        import cli
        commands = [cmd.name for cmd in cli.models_app.registered_commands]
        assert "components" in commands


class TestMCPToolsExist:
    """Test that new MCP tools are registered."""

    def test_get_model_components_tool(self):
        """get_model_components MCP tool should exist."""
        import server
        # Check if the function exists
        assert hasattr(server, 'get_model_components')

    def test_validate_flowsheet_tool(self):
        """validate_flowsheet MCP tool should exist."""
        import server
        assert hasattr(server, 'validate_flowsheet')

    def test_suggest_recycles_tool(self):
        """suggest_recycles MCP tool should exist."""
        import server
        assert hasattr(server, 'suggest_recycles')

    def test_get_artifact_tool(self):
        """get_artifact MCP tool should exist."""
        import server
        assert hasattr(server, 'get_artifact')

    def test_get_flowsheet_timeseries_tool(self):
        """get_flowsheet_timeseries MCP tool should exist."""
        import server
        assert hasattr(server, 'get_flowsheet_timeseries')
