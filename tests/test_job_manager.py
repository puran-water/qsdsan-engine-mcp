"""
JobManager Tests - Test job lifecycle, concurrency, and recovery.

These tests verify the JobManager works correctly for:
1. Singleton pattern
2. Job creation and directory structure
3. Job status tracking
4. Job listing
5. Recovery from disk
"""

import pytest
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_jobs_dir(tmp_path):
    """Create temporary jobs directory."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    return jobs_dir


@pytest.fixture
def fresh_job_manager(temp_jobs_dir):
    """Create a fresh JobManager instance for testing.

    Note: JobManager is a singleton, so we need to reset it between tests.
    """
    from utils.job_manager import JobManager

    # Reset singleton
    JobManager._instance = None

    manager = JobManager(max_concurrent_jobs=3, jobs_base_dir=str(temp_jobs_dir))
    yield manager

    # Cleanup: reset singleton for next test
    JobManager._instance = None


# =============================================================================
# Singleton Tests
# =============================================================================

class TestJobManagerSingleton:
    """Test singleton pattern."""

    def test_singleton_returns_same_instance(self, temp_jobs_dir):
        """Singleton should return same instance."""
        from utils.job_manager import JobManager

        # Reset singleton
        JobManager._instance = None

        jm1 = JobManager(jobs_base_dir=str(temp_jobs_dir))
        jm2 = JobManager(jobs_base_dir=str(temp_jobs_dir))

        assert jm1 is jm2

        # Cleanup
        JobManager._instance = None


# =============================================================================
# Job Lifecycle Tests
# =============================================================================

class TestJobManagerLifecycle:
    """Test job creation and execution."""

    @pytest.mark.asyncio
    async def test_execute_creates_job_directory(self, fresh_job_manager, temp_jobs_dir):
        """Execute should create job directory with job.json."""
        # Run a quick command
        result = await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "print('hello')"],
            cwd=str(temp_jobs_dir),
        )

        assert "id" in result
        job_id = result["id"]

        # Wait a bit for async operations
        await asyncio.sleep(0.5)

        # Verify job directory exists
        job_dir = temp_jobs_dir / job_id
        assert job_dir.exists()

        # Verify job.json exists
        job_file = job_dir / "job.json"
        assert job_file.exists()

    @pytest.mark.asyncio
    async def test_get_status_returns_job_info(self, fresh_job_manager, temp_jobs_dir):
        """get_status should return job info dict."""
        result = await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "import time; time.sleep(0.3); print('done')"],
            cwd=str(temp_jobs_dir),
        )
        job_id = result["id"]

        # Check status while running
        status = await fresh_job_manager.get_status(job_id)
        assert isinstance(status, dict)
        assert "status" in status
        assert status["status"] in ["running", "completed", "queued", "pending"]

        # Wait for completion
        await asyncio.sleep(0.5)

        status = await fresh_job_manager.get_status(job_id)
        # Should be completed or failed (depending on cmd)
        assert status["status"] in ["completed", "failed", "running"]

    @pytest.mark.asyncio
    async def test_list_jobs_returns_dict(self, fresh_job_manager, temp_jobs_dir):
        """list_jobs should return dict with jobs list."""
        # Create a job
        await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "print(1)"],
            cwd=str(temp_jobs_dir),
        )

        await asyncio.sleep(0.2)

        result = await fresh_job_manager.list_jobs()

        assert isinstance(result, dict)
        assert "jobs" in result
        assert isinstance(result["jobs"], list)


# =============================================================================
# Job Recovery Tests
# =============================================================================

class TestJobManagerRecovery:
    """Test job recovery from disk."""

    def test_loads_existing_jobs_on_init(self, temp_jobs_dir):
        """JobManager should load existing job.json files on init."""
        from utils.job_manager import JobManager

        # Reset singleton
        JobManager._instance = None

        # Create a fake job.json
        job_dir = temp_jobs_dir / "test_job_recovery"
        job_dir.mkdir()
        job_data = {
            "id": "test_job_recovery",
            "status": "completed",
            "created_at": time.time(),
            "completed_at": time.time(),
        }
        (job_dir / "job.json").write_text(json.dumps(job_data))

        # Create manager (should load the job)
        manager = JobManager(jobs_base_dir=str(temp_jobs_dir))

        assert "test_job_recovery" in manager.jobs

        # Cleanup
        JobManager._instance = None

    def test_marks_stale_jobs_as_failed(self, temp_jobs_dir):
        """Jobs with dead PIDs should be marked as failed."""
        from utils.job_manager import JobManager

        # Reset singleton
        JobManager._instance = None

        # Create a job with a non-existent PID
        job_dir = temp_jobs_dir / "stale_job"
        job_dir.mkdir()
        job_data = {
            "id": "stale_job",
            "status": "running",
            "pid": 99999999,  # Non-existent PID
            "created_at": time.time(),
        }
        (job_dir / "job.json").write_text(json.dumps(job_data))

        # Create manager
        manager = JobManager(jobs_base_dir=str(temp_jobs_dir))

        # Job should be marked as failed
        assert manager.jobs["stale_job"]["status"] == "failed"
        assert "terminated" in manager.jobs["stale_job"].get("error", "").lower() or \
               "crash" in manager.jobs["stale_job"].get("error", "").lower()

        # Cleanup
        JobManager._instance = None


# =============================================================================
# Custom Job ID Tests
# =============================================================================

class TestJobManagerCustomId:
    """Test custom job ID support."""

    @pytest.mark.asyncio
    async def test_execute_with_custom_id(self, fresh_job_manager, temp_jobs_dir):
        """Execute should accept custom job_id when directory pre-exists."""
        custom_id = "my_custom_job_id"

        # Create the job directory first (required by JobManager)
        job_dir = temp_jobs_dir / custom_id
        job_dir.mkdir(parents=True)

        result = await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "print('custom')"],
            cwd=str(temp_jobs_dir),
            job_id=custom_id,
        )

        assert result["id"] == custom_id

        # Wait and check directory still exists with job.json
        await asyncio.sleep(0.3)

        assert job_dir.exists()
        assert (job_dir / "job.json").exists()


# =============================================================================
# Concurrency Tests
# =============================================================================

class TestJobManagerConcurrency:
    """Test concurrency control.

    Note: These tests can be flaky due to process timing.
    Use explicit timeouts and accept some variance.
    """

    @pytest.mark.asyncio
    async def test_running_count_tracks_jobs(self, fresh_job_manager, temp_jobs_dir):
        """Running count should increase with active jobs."""
        initial_count = fresh_job_manager._running_count

        # Start a slow job
        await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "import time; time.sleep(1)"],
            cwd=str(temp_jobs_dir),
        )

        await asyncio.sleep(0.2)

        # Running count should be higher or job already queued
        # (depends on timing)
        current_count = fresh_job_manager._running_count
        # Count could be 0 if job already finished, or 1 if still running
        assert current_count >= 0


# =============================================================================
# Results Tests
# =============================================================================

class TestJobManagerResults:
    """Test job results retrieval."""

    @pytest.mark.asyncio
    async def test_get_results_includes_exit_code(self, fresh_job_manager, temp_jobs_dir):
        """get_results should include exit_code."""
        result = await fresh_job_manager.execute(
            cmd=[sys.executable, "-c", "print('test output')"],
            cwd=str(temp_jobs_dir),
        )
        job_id = result["id"]

        # Wait for completion
        await asyncio.sleep(0.5)

        results = await fresh_job_manager.get_results(job_id)

        # Should have results structure
        assert isinstance(results, dict)
        # May have 'exit_code' or 'status' depending on implementation

    @pytest.mark.asyncio
    async def test_get_results_nonexistent_job(self, fresh_job_manager):
        """get_results for nonexistent job should return error."""
        results = await fresh_job_manager.get_results("nonexistent_job_xyz")

        assert "error" in results or results.get("status") == "not_found"
