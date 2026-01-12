"""
Security tests for path traversal and ID injection prevention.

Tests verify that:
1. validate_safe_path rejects directory traversal attempts
2. validate_id rejects malformed IDs with unsafe characters
3. MCP tools properly reject unsafe job_id and session_id inputs
4. Flowsheet session manager validates session_ids
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import os


class TestPathTraversalPrevention:
    """Tests for validate_safe_path function."""

    def test_rejects_parent_directory_traversal(self):
        """Verify ../../../etc/passwd style attacks fail."""
        from utils.path_utils import validate_safe_path

        with pytest.raises(ValueError, match="path traversal detected"):
            validate_safe_path(Path("jobs"), "../../../etc/passwd", "job_id")

    def test_rejects_double_dot_in_path(self):
        """Verify ../ patterns are rejected."""
        from utils.path_utils import validate_safe_path

        with pytest.raises(ValueError, match="path traversal detected"):
            validate_safe_path(Path("jobs"), "valid/../../../secret", "job_id")

    def test_rejects_absolute_path_injection(self):
        """Verify absolute paths that escape base are rejected."""
        from utils.path_utils import validate_safe_path

        # On Linux, /etc/passwd is an absolute path
        with pytest.raises(ValueError, match="path traversal detected"):
            validate_safe_path(Path("jobs"), "/etc/passwd", "job_id")

    def test_accepts_valid_subdirectory(self):
        """Verify normal subdirectory paths work."""
        from utils.path_utils import validate_safe_path

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            base.mkdir(exist_ok=True)

            # Valid path should work
            result = validate_safe_path(base, "abc123", "job_id")
            assert str(result).startswith(str(base.resolve()))

    def test_accepts_nested_subdirectory(self):
        """Verify nested paths within base directory work."""
        from utils.path_utils import validate_safe_path

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            nested = base / "sub1" / "sub2"
            nested.mkdir(parents=True, exist_ok=True)

            result = validate_safe_path(base, "sub1/sub2", "job_id")
            assert str(result).startswith(str(base.resolve()))


class TestIdValidation:
    """Tests for validate_id function."""

    def test_accepts_alphanumeric_id(self):
        """Verify alphanumeric IDs are accepted."""
        from utils.path_utils import validate_id

        # Should not raise
        validate_id("abc123", "job_id")
        validate_id("ABC123", "session_id")
        validate_id("a1B2c3", "test_id")

    def test_accepts_underscore_and_hyphen(self):
        """Verify underscore and hyphen are accepted."""
        from utils.path_utils import validate_id

        validate_id("my_session", "session_id")
        validate_id("job-v2", "job_id")
        validate_id("test_job-123", "test_id")

    def test_rejects_empty_id(self):
        """Verify empty IDs are rejected."""
        from utils.path_utils import validate_id

        with pytest.raises(ValueError, match="cannot be empty"):
            validate_id("", "job_id")

    def test_rejects_path_separators(self):
        """Verify forward and back slashes are rejected."""
        from utils.path_utils import validate_id

        with pytest.raises(ValueError, match="must contain only"):
            validate_id("job/id", "job_id")

        with pytest.raises(ValueError, match="must contain only"):
            validate_id("job\\id", "job_id")

    def test_rejects_double_dot(self):
        """Verify .. patterns are rejected."""
        from utils.path_utils import validate_id

        with pytest.raises(ValueError, match="must contain only"):
            validate_id("..", "job_id")

        with pytest.raises(ValueError, match="must contain only"):
            validate_id("job/../secret", "job_id")

    def test_rejects_spaces(self):
        """Verify spaces are rejected."""
        from utils.path_utils import validate_id

        with pytest.raises(ValueError, match="must contain only"):
            validate_id("job id", "job_id")

    def test_rejects_special_characters(self):
        """Verify special characters are rejected."""
        from utils.path_utils import validate_id

        for char in ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "=", "+", "[", "]", "{", "}", "|", ";", ":", "'", '"', "<", ">", ",", "?", "`", "~"]:
            with pytest.raises(ValueError, match="must contain only"):
                validate_id(f"job{char}id", "test_id")

    def test_rejects_too_long_id(self):
        """Verify IDs over 64 characters are rejected."""
        from utils.path_utils import validate_id

        long_id = "a" * 65
        with pytest.raises(ValueError, match="max 64 characters"):
            validate_id(long_id, "job_id")

    def test_accepts_max_length_id(self):
        """Verify IDs at exactly 64 characters are accepted."""
        from utils.path_utils import validate_id

        valid_id = "a" * 64
        validate_id(valid_id, "job_id")  # Should not raise


class TestFlowsheetSessionSecurity:
    """Tests for session manager path traversal protection."""

    def test_session_manager_rejects_traversal_id(self):
        """Verify session manager rejects path traversal in session_id."""
        from utils.flowsheet_session import FlowsheetSessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = FlowsheetSessionManager(sessions_dir=Path(tmpdir))

            # Attempt to create session with traversal path
            with pytest.raises(ValueError, match="session_id"):
                manager.create_session(
                    model_type="ASM2d",
                    session_id="../../../etc/passwd"
                )

    def test_session_manager_rejects_slashes_in_id(self):
        """Verify session manager rejects forward slashes in session_id."""
        from utils.flowsheet_session import FlowsheetSessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = FlowsheetSessionManager(sessions_dir=Path(tmpdir))

            with pytest.raises(ValueError, match="session_id"):
                manager.create_session(
                    model_type="ASM2d",
                    session_id="my/session"
                )

    def test_session_manager_accepts_valid_id(self):
        """Verify session manager accepts valid session_ids."""
        from utils.flowsheet_session import FlowsheetSessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = FlowsheetSessionManager(sessions_dir=Path(tmpdir))

            session = manager.create_session(
                model_type="ASM2d",
                session_id="my_valid-session123"
            )
            assert session.session_id == "my_valid-session123"

    def test_get_session_rejects_traversal(self):
        """Verify get_session rejects path traversal."""
        from utils.flowsheet_session import FlowsheetSessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = FlowsheetSessionManager(sessions_dir=Path(tmpdir))

            with pytest.raises(ValueError, match="session_id"):
                manager.get_session("../../../etc/passwd")


class TestMCPToolSecurity:
    """Tests for MCP tool input validation.

    These tests verify that MCP tools properly validate job_id and session_id
    inputs before processing.
    """

    @pytest.mark.asyncio
    async def test_get_flowsheet_timeseries_rejects_traversal(self):
        """Verify get_flowsheet_timeseries rejects path traversal in job_id."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import get_flowsheet_timeseries

        result = await get_flowsheet_timeseries(
            job_id="../../../etc/passwd",
            stream_ids=None,
            components=None,
            downsample_factor=1
        )

        assert "error" in result
        assert "Invalid job_id" in result["error"] or "must contain only" in result["error"]

    @pytest.mark.asyncio
    async def test_get_artifact_rejects_traversal(self):
        """Verify get_artifact rejects path traversal in job_id."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import get_artifact

        result = await get_artifact(
            job_id="../../../etc/passwd",
            artifact_type="diagram"
        )

        assert "error" in result
        assert "Invalid job_id" in result["error"] or "must contain only" in result["error"]

    @pytest.mark.asyncio
    async def test_create_flowsheet_session_rejects_traversal(self):
        """Verify create_flowsheet_session rejects path traversal."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import create_flowsheet_session

        result = await create_flowsheet_session(
            model_type="ASM2d",
            session_id="../../../etc/passwd"
        )

        assert "error" in result
        # Should mention session_id validation
        assert "session_id" in result["error"].lower() or "invalid" in result["error"].lower()


class TestArtifactTypeValidation:
    """Tests for artifact_type parameter validation."""

    @pytest.mark.asyncio
    async def test_rejects_unknown_artifact_type(self):
        """Verify get_artifact rejects unknown artifact types."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from server import get_artifact

        # Create a temporary job directory so we reach artifact_type validation
        jobs_dir = Path("jobs")
        jobs_dir.mkdir(exist_ok=True)
        test_job_dir = jobs_dir / "test_artifact_type"
        test_job_dir.mkdir(exist_ok=True)

        try:
            result = await get_artifact(
                job_id="test_artifact_type",
                artifact_type="../../../etc/passwd"
            )

            assert "error" in result
            # Should mention unknown artifact type, not file access
            assert "Unknown artifact_type" in result["error"] or "Valid types" in result["error"]
        finally:
            # Cleanup
            import shutil
            if test_job_dir.exists():
                shutil.rmtree(test_job_dir)
