"""
Tests for cloud configuration and storage abstraction.

These tests verify that the cloud module works correctly in local mode
without requiring any cloud dependencies.
"""

import asyncio
import os
import pytest
import tempfile
from pathlib import Path


class TestEnvironmentDetection:
    """Test environment auto-detection."""

    def test_default_is_local_dev(self):
        """Default environment should be LOCAL_DEV."""
        # Clear any existing config
        from cloud.config import reset_config, detect_environment, Environment
        reset_config()

        # Clear environment override
        os.environ.pop("QSDSAN_ENV", None)
        os.environ.pop("K_SERVICE", None)

        env = detect_environment()
        assert env == Environment.LOCAL_DEV

    def test_explicit_override(self):
        """QSDSAN_ENV should override auto-detection."""
        from cloud.config import reset_config, detect_environment, Environment
        reset_config()

        os.environ["QSDSAN_ENV"] = "local_docker"
        try:
            env = detect_environment()
            assert env == Environment.LOCAL_DOCKER
        finally:
            os.environ.pop("QSDSAN_ENV", None)

    def test_cloud_run_detection(self):
        """K_SERVICE indicates Cloud Run."""
        from cloud.config import reset_config, detect_environment, Environment
        reset_config()

        os.environ.pop("QSDSAN_ENV", None)
        os.environ["K_SERVICE"] = "qsdsan-engine-mcp"
        try:
            env = detect_environment()
            assert env == Environment.CLOUD_RUN
        finally:
            os.environ.pop("K_SERVICE", None)


class TestAppConfig:
    """Test application configuration."""

    def test_get_config_singleton(self):
        """get_config should return singleton."""
        from cloud.config import reset_config, get_config
        reset_config()

        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_is_local_true_for_local_dev(self):
        """is_local should return True for LOCAL_DEV."""
        from cloud.config import reset_config, get_config
        reset_config()
        os.environ.pop("QSDSAN_ENV", None)
        os.environ.pop("K_SERVICE", None)

        config = get_config()
        assert config.is_local() is True
        assert config.is_cloud() is False

    def test_jobs_dir_from_env(self, tmp_path):
        """QSDSAN_JOBS_DIR should configure storage path."""
        from cloud.config import reset_config, get_config
        reset_config()

        custom_dir = str(tmp_path / "custom_jobs")
        os.environ["QSDSAN_JOBS_DIR"] = custom_dir
        try:
            config = get_config()
            assert str(config.storage.jobs_dir) == custom_dir
        finally:
            os.environ.pop("QSDSAN_JOBS_DIR", None)
            reset_config()


class TestLocalStorageBackend:
    """Test local filesystem storage."""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        """Create temporary storage backend."""
        from cloud.config import StorageConfig
        from cloud.storage import LocalStorageBackend

        config = StorageConfig(jobs_dir=tmp_path)
        return LocalStorageBackend(config)

    def test_save_and_load_json(self, temp_storage):
        """Test JSON save and load."""
        async def _test():
            test_data = {"key": "value", "number": 42}
            await temp_storage.save_json("test/data.json", test_data)

            loaded = await temp_storage.load_json("test/data.json")
            assert loaded == test_data

        asyncio.run(_test())

    def test_load_nonexistent_returns_none(self, temp_storage):
        """Loading nonexistent file should return None."""
        async def _test():
            result = await temp_storage.load_json("nonexistent.json")
            assert result is None

        asyncio.run(_test())

    def test_save_and_load_bytes(self, temp_storage):
        """Test binary file save and load."""
        async def _test():
            test_data = b"binary content \x00\xff"
            await temp_storage.save_bytes("test/file.bin", test_data)

            loaded = await temp_storage.load_bytes("test/file.bin")
            assert loaded == test_data

        asyncio.run(_test())

    def test_save_and_load_text(self, temp_storage):
        """Test text file save and load."""
        async def _test():
            test_text = "Hello, world!\nLine 2"
            await temp_storage.save_text("test/file.txt", test_text)

            loaded = await temp_storage.load_text("test/file.txt")
            assert loaded == test_text

        asyncio.run(_test())

    def test_exists(self, temp_storage):
        """Test file existence check."""
        async def _test():
            assert await temp_storage.exists("nonexistent.json") is False

            await temp_storage.save_json("exists.json", {"test": True})
            assert await temp_storage.exists("exists.json") is True

        asyncio.run(_test())

    def test_delete(self, temp_storage):
        """Test file deletion."""
        async def _test():
            await temp_storage.save_json("to_delete.json", {"test": True})
            assert await temp_storage.exists("to_delete.json") is True

            result = await temp_storage.delete("to_delete.json")
            assert result is True
            assert await temp_storage.exists("to_delete.json") is False

            # Delete nonexistent returns False
            result = await temp_storage.delete("nonexistent.json")
            assert result is False

        asyncio.run(_test())

    def test_list_files(self, temp_storage):
        """Test file listing."""
        async def _test():
            await temp_storage.save_json("job1/job.json", {"id": "1"})
            await temp_storage.save_json("job1/results.json", {"data": []})
            await temp_storage.save_json("job2/job.json", {"id": "2"})

            files = await temp_storage.list_files("job1")
            assert len(files) == 2
            assert "job1/job.json" in files
            assert "job1/results.json" in files

        asyncio.run(_test())

    def test_get_url_local(self, temp_storage, tmp_path):
        """Test local URL generation."""
        url = temp_storage.get_url("test/file.json")
        assert url.startswith("file://")
        assert "test" in url and "file.json" in url

    def test_get_local_path(self, temp_storage, tmp_path):
        """Test local path retrieval."""
        path = temp_storage.get_local_path("test/file.json")
        assert path is not None
        assert path.parent.name == "test"
        assert path.name == "file.json"

    def test_path_traversal_prevention(self, temp_storage):
        """Test that path traversal is prevented."""
        async def _test():
            with pytest.raises(ValueError, match="traversal"):
                await temp_storage.save_json("../escape.json", {"bad": True})

        asyncio.run(_test())


class TestStorageManager:
    """Test high-level storage manager API."""

    @pytest.fixture
    def storage_manager(self, tmp_path):
        """Create storage manager with temp directory."""
        from cloud.config import StorageConfig
        from cloud.storage import LocalStorageBackend, StorageManager

        config = StorageConfig(jobs_dir=tmp_path)
        backend = LocalStorageBackend(config)
        return StorageManager(backend)

    def test_job_metadata_round_trip(self, storage_manager):
        """Test saving and loading job metadata."""
        async def _test():
            metadata = {
                "id": "abc123",
                "status": "running",
                "started_at": 1234567890.0,
            }
            await storage_manager.save_job_metadata("abc123", metadata)

            loaded = await storage_manager.load_job_metadata("abc123")
            assert loaded == metadata

        asyncio.run(_test())

    def test_session_round_trip(self, storage_manager):
        """Test saving and loading session data."""
        async def _test():
            session = {
                "session_id": "test-session",
                "primary_model_type": "ASM2d",
                "streams": {},
                "units": {},
            }
            await storage_manager.save_session("test-session", session)

            loaded = await storage_manager.load_session("test-session")
            assert loaded == session

        asyncio.run(_test())

    def test_diagram_save(self, storage_manager):
        """Test saving diagram SVG."""
        async def _test():
            svg_content = b"<svg>test</svg>"
            await storage_manager.save_diagram("job123", svg_content)

            # Verify file exists
            assert await storage_manager.backend.exists("job123/flowsheet.svg")

        asyncio.run(_test())

    def test_artifact_url(self, storage_manager):
        """Test artifact URL generation."""
        url = storage_manager.get_artifact_url("job123", "diagram")
        assert "job123" in url
        assert "flowsheet.svg" in url

        url = storage_manager.get_artifact_url("job123", "results")
        assert "simulation_results.json" in url


class TestGetStorageManager:
    """Test storage manager factory."""

    def test_returns_local_backend_in_local_mode(self, tmp_path):
        """In LOCAL_DEV, should return LocalStorageBackend."""
        from cloud.config import reset_config
        from cloud.storage import reset_storage_manager, get_storage_manager, LocalStorageBackend

        reset_config()
        reset_storage_manager()

        os.environ.pop("QSDSAN_ENV", None)
        os.environ.pop("K_SERVICE", None)
        os.environ["QSDSAN_JOBS_DIR"] = str(tmp_path)

        try:
            manager = get_storage_manager()
            assert isinstance(manager.backend, LocalStorageBackend)
        finally:
            os.environ.pop("QSDSAN_JOBS_DIR", None)
            reset_config()
            reset_storage_manager()
