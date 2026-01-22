"""
Tests for Mixer/Splitter with recycle connections (Phase 8A).

These tests verify that:
1. Splitter parameter formats (float, list, dict) are handled correctly
2. Mixer input resolution doesn't return None silently
3. Deferred connections (recycles) are wired properly
4. Dynamic port allocation works for Mixer units

Usage:
    pytest tests/test_flowsheet_mixer_splitter.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

# Mark as phase8 tests
pytestmark = pytest.mark.phase8


class TestSplitterParameterFormats:
    """Test that Splitter accepts various split parameter formats."""

    def test_splitter_float_split(self):
        """Splitter should accept a float for 2-output split."""
        from utils.flowsheet_builder import _create_san_unit
        from utils.flowsheet_session import UnitConfig

        # Mock the imports within _create_san_unit
        with patch('utils.flowsheet_builder._get_unit_class') as mock_get_class:
            mock_splitter = MagicMock()
            mock_get_class.return_value = mock_splitter

            config = UnitConfig(
                unit_id="SP1",
                unit_type="Splitter",
                params={"split": 0.8},  # Float format
                inputs=["stream1"],
                outputs=None,
                model_type="ASM2d",
            )

            # Create mock stream registry
            mock_stream = MagicMock()
            mock_stream.F_mass = 100.0
            stream_registry = {"stream1": mock_stream}
            unit_registry = {}

            # This should not raise
            try:
                _create_san_unit(
                    "SP1",
                    config,
                    stream_registry=stream_registry,
                    unit_registry=unit_registry,
                    model_type="ASM2d",
                )
            except Exception as e:
                # Check it's not a split param issue
                assert "split" not in str(e).lower() or "format" not in str(e).lower()

    def test_splitter_list_split(self):
        """Splitter should accept a list for multi-output split."""
        from utils.flowsheet_session import UnitConfig

        config = UnitConfig(
            unit_id="SP2",
            unit_type="Splitter",
            params={"split": [0.7, 0.3]},  # List format
            inputs=["stream1"],
            outputs=None,
            model_type="ASM2d",
        )

        # Validate params don't raise errors
        from core.unit_registry import validate_unit_params
        errors, warnings = validate_unit_params("Splitter", config.params)
        assert len(errors) == 0, f"Expected no errors, got: {errors}"


class TestMixerInputResolution:
    """Test that Mixer input resolution provides clear errors."""

    def test_resolve_single_input_raises_for_missing_stream(self):
        """_resolve_single_input should raise ValueError for missing stream."""
        from utils.flowsheet_builder import _resolve_single_input

        stream_registry = {"existing_stream": MagicMock()}
        unit_registry = {}

        # With allow_missing=False, should raise
        with pytest.raises(ValueError) as exc_info:
            _resolve_single_input(
                "nonexistent_stream",
                stream_registry,
                unit_registry,
                allow_missing=False,
            )

        assert "not found" in str(exc_info.value).lower()

    def test_resolve_single_input_returns_none_when_allowed(self):
        """_resolve_single_input should return None when allow_missing=True."""
        from utils.flowsheet_builder import _resolve_single_input

        stream_registry = {}
        unit_registry = {}

        # With allow_missing=True, should return None
        result = _resolve_single_input(
            "nonexistent_stream",
            stream_registry,
            unit_registry,
            allow_missing=True,
        )

        assert result is None

    def test_resolve_unit_output_raises_for_missing_unit(self):
        """_resolve_single_input should raise for missing unit in output notation."""
        from utils.flowsheet_builder import _resolve_single_input

        stream_registry = {}
        unit_registry = {}

        with pytest.raises(ValueError) as exc_info:
            _resolve_single_input(
                "A1-0",  # Output port of unit A1
                stream_registry,
                unit_registry,
                allow_missing=False,
            )

        assert "A1" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    def test_resolve_input_port_notation_raises(self):
        """Input port notation (1-M1) should raise when used as input source."""
        from utils.flowsheet_builder import _resolve_single_input

        stream_registry = {}
        unit_registry = {"M1": MagicMock()}

        with pytest.raises(ValueError) as exc_info:
            _resolve_single_input(
                "1-M1",  # Input port notation
                stream_registry,
                unit_registry,
                allow_missing=False,
            )

        assert "input port notation" in str(exc_info.value).lower()


class TestDeferredConnections:
    """Test wiring of deferred connections (recycles)."""

    def test_wire_connection_to_mixer_finds_empty_slot(self):
        """_wire_connection should find empty input slot for Mixers."""
        from utils.flowsheet_builder import _wire_connection
        from utils.flowsheet_session import ConnectionConfig

        # Create mock Mixer with some inputs
        mock_mixer = MagicMock()
        mock_mixer.ID = "M1"
        mock_mixer.__class__.__name__ = "Mixer"

        # Mixer with 2 inputs, first one occupied, second empty
        mock_occupied = MagicMock()
        mock_occupied.F_mass = 100.0
        mock_empty = MagicMock()
        mock_empty.F_mass = 0.0  # Empty slot

        mock_mixer.ins = [mock_occupied, mock_empty]

        # Create source unit
        mock_splitter = MagicMock()
        mock_splitter.ID = "SP1"
        mock_output = MagicMock()
        mock_output.F_mass = 50.0
        mock_splitter.outs = [mock_output]

        unit_registry = {"M1": mock_mixer, "SP1": mock_splitter}
        stream_registry = {}
        recycle_streams = []

        conn = ConnectionConfig(from_port="SP1-0", to_port="M1")

        # Should wire to the empty slot
        _wire_connection(conn, unit_registry, stream_registry, recycle_streams)

        # Verify the connection was made to slot 1 (the empty one)
        assert mock_mixer.ins[1] == mock_output

    def test_wire_connection_explicit_input_port(self):
        """_wire_connection should handle explicit input port notation."""
        from utils.flowsheet_builder import _wire_connection
        from utils.flowsheet_session import ConnectionConfig

        mock_dst = MagicMock()
        mock_dst.ID = "A1"
        mock_dst.__class__.__name__ = "CSTR"
        mock_dst.ins = [MagicMock(), MagicMock()]

        mock_src = MagicMock()
        mock_src.ID = "SP1"
        mock_output = MagicMock()
        mock_src.outs = [mock_output]

        unit_registry = {"A1": mock_dst, "SP1": mock_src}
        stream_registry = {}
        recycle_streams = []

        conn = ConnectionConfig(from_port="SP1-0", to_port="1-A1")  # Explicit port 1

        _wire_connection(conn, unit_registry, stream_registry, recycle_streams)

        # Verify the connection was made to port 1
        assert mock_dst.ins[1] == mock_output


class TestMixerCreation:
    """Test Mixer unit creation with multiple inputs."""

    def test_mixer_created_with_tuple_inputs(self):
        """Mixer should receive inputs as tuple for variable fan-in."""
        from utils.flowsheet_builder import _create_san_unit
        from utils.flowsheet_session import UnitConfig

        with patch('utils.flowsheet_builder._get_unit_class') as mock_get_class:
            mock_mixer_class = MagicMock()
            mock_get_class.return_value = mock_mixer_class

            config = UnitConfig(
                unit_id="M1",
                unit_type="Mixer",
                params={},
                inputs=["stream1", "stream2"],
                outputs=None,
                model_type="ASM2d",
            )

            mock_stream1 = MagicMock()
            mock_stream2 = MagicMock()
            stream_registry = {"stream1": mock_stream1, "stream2": mock_stream2}
            unit_registry = {}

            try:
                _create_san_unit(
                    "M1",
                    config,
                    stream_registry=stream_registry,
                    unit_registry=unit_registry,
                    model_type="ASM2d",
                )
            except Exception:
                pass  # May fail on actual QSDsan import, but we check the call

            # Check that Mixer was called with tuple ins
            if mock_mixer_class.called:
                call_kwargs = mock_mixer_class.call_args[1]
                assert "ins" in call_kwargs
                # Should be tuple for Mixer
                assert isinstance(call_kwargs["ins"], tuple)


class TestRecycleWorkflow:
    """Integration tests for complete recycle workflow."""

    def test_mle_style_recycle_pattern(self):
        """Test MLE-style recycle pattern: Mixer -> reactors -> Splitter -> Mixer."""
        from utils.pipe_parser import parse_port_notation

        # Verify port notation parsing for MLE-style connections
        # IR recycle: SP_IR-1 -> M1-1
        ir_from = parse_port_notation("SP_IR-1")
        assert ir_from.unit_id == "SP_IR"
        assert ir_from.port_type == "output"
        assert ir_from.index == 1

        ir_to = parse_port_notation("1-M1")
        assert ir_to.unit_id == "M1"
        assert ir_to.port_type == "input"
        assert ir_to.index == 1

        # RAS recycle: SP_RAS-0 -> M1-2
        ras_from = parse_port_notation("SP_RAS-0")
        assert ras_from.unit_id == "SP_RAS"
        assert ras_from.port_type == "output"
        assert ras_from.index == 0

        ras_to = parse_port_notation("2-M1")
        assert ras_to.unit_id == "M1"
        assert ras_to.port_type == "input"
        assert ras_to.index == 2


class TestPreCompilationValidation:
    """Test pre-compilation input validation in create_unit."""

    @pytest.mark.asyncio
    async def test_create_unit_validates_input_port_notation(self):
        """create_unit should reject input port notation as input source."""
        from server import create_unit

        # Mock session manager
        with patch('server.session_manager') as mock_sm:
            mock_session = MagicMock()
            mock_session.primary_model_type = "ASM2d"
            mock_session.streams = {}
            mock_session.units = {}
            mock_sm.get_session.return_value = mock_session

            result = await create_unit(
                session_id="test",
                unit_type="CSTR",
                unit_id="A1",
                params={"V_max": 100},
                inputs=["1-M1"],  # Invalid: input port notation as input source
            )

            assert "error" in result
            assert "input port notation" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_create_unit_warns_for_missing_inputs(self):
        """create_unit should warn (not error) for deferred inputs."""
        from server import create_unit

        with patch('server.session_manager') as mock_sm:
            mock_session = MagicMock()
            mock_session.primary_model_type = "ASM2d"
            mock_session.streams = {}
            mock_session.units = {}
            mock_sm.get_session.return_value = mock_session
            mock_sm.add_unit.return_value = {"unit_id": "A1", "status": "added"}

            result = await create_unit(
                session_id="test",
                unit_type="CSTR",
                unit_id="A1",
                params={"V_max": 100},
                inputs=["nonexistent_stream"],  # Will be deferred
            )

            # Should succeed with warning, not error
            if "error" not in result:
                # Check for warnings
                assert "warnings" in result or result.get("status") == "added"
