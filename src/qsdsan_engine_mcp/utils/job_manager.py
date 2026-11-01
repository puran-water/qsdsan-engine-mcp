"""
Background Job Manager for Long-Running Computational Tasks

Implements the Background Job Pattern to avoid MCP STDIO blocking issues
with heavy Python imports (scipy, fluids, QSDsan).

Key Features:
- Async subprocess execution with immediate job_id return
- Crash recovery via disk-based job metadata
- Concurrency control with semaphore (max 3 concurrent jobs)
- Per-job output isolation to prevent file conflicts
- Progress tracking via stdout parsing
- Automatic cleanup of orphaned processes
- Configurable timeout to prevent runaway simulations (Phase 8)

Architecture:
    User -> MCP Tool -> JobManager.execute() -> Returns job_id immediately
                              ↓
                    Background subprocess runs heavy computation
                              ↓
    User -> get_job_status(job_id) -> "running, 65% complete"
                              ↓
    User -> get_job_results(job_id) -> Full structured results

Note: This is a stateless version for qsdsan-engine-mcp.
State reconciliation is not needed since the engine passes state explicitly.
"""

import asyncio
import inspect
import json
import logging
import os
import psutil
import re
import signal
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Dict, List

from qsdsan_engine_mcp.core.errors import public_error, sanitize_error_message, sanitize_public_payload
from qsdsan_engine_mcp.utils.path_utils import normalize_path_for_wsl

logger = logging.getLogger(__name__)


class JobManager:
    """
    Singleton job manager with crash recovery and concurrency control.

    Usage:
        manager = JobManager()

        # Start job (e.g., via CLI adapter)
        job = await manager.execute(
            cmd=["python", "cli.py", "flowsheet", "simulate", "--session", "example_session"],
            cwd="/path/to/project"
        )

        # Check status
        status = await manager.get_status(job["id"])

        # Get results
        results = await manager.get_results(job["id"])
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_concurrent_jobs: int = 3, jobs_base_dir: str = "jobs"):
        """
        Initialize job manager.

        Args:
            max_concurrent_jobs: Maximum number of simultaneous background jobs
            jobs_base_dir: Base directory for job workspaces
        """
        # Only initialize once
        if hasattr(self, '_initialized'):
            return

        self.jobs: Dict[str, dict] = {}
        self.jobs_dir = Path(jobs_base_dir)
        self.jobs_dir.mkdir(exist_ok=True)
        self.max_concurrent_jobs = max_concurrent_jobs
        # Counter-based concurrency control (replaces semaphore which released too early)
        self._running_count = 0
        self._running_lock = asyncio.Lock()
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._monitor_tasks: Dict[str, asyncio.Task] = {}

        # Load existing jobs from disk (crash recovery)
        self._load_existing_jobs()

        # Register signal handlers for cleanup
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self._initialized = True
        logger.info(f"JobManager initialized: max_concurrent={max_concurrent_jobs}, jobs_dir={self.jobs_dir}")

    def _load_existing_jobs(self):
        """Recover job metadata from disk, detect stale PIDs."""
        logger.info("Loading existing jobs from disk...")
        recovered = 0
        stale = 0
        preserved = 0
        terminal_statuses = {"completed", "failed", "timeout", "terminated", "rejected", "cancelled"}

        for job_file in self.jobs_dir.glob("*/job.json"):
            try:
                with open(job_file) as f:
                    job = json.load(f)

                job_id = job.get("id")
                if not job_id:
                    continue

                # Check if process is still running
                pid = job.get("pid")
                stored_status = job.get("status")
                if stored_status in terminal_statuses:
                    preserved += 1
                elif pid and self._is_process_alive(pid):
                    job["status"] = "running"
                    recovered += 1
                    logger.info(f"Recovered running job {job_id} (PID: {pid})")
                else:
                    job["status"] = "failed"
                    job["error"] = "Process terminated (server restart or crash)"
                    job["recovered_at"] = time.time()
                    stale += 1
                    logger.debug(f"Marked stale job {job_id} as failed")

                self.jobs[job_id] = job

            except Exception as e:
                logger.error(f"Failed to load job from {job_file}: {e}")

        logger.info(f"Job recovery complete: {recovered} running, {preserved} preserved, {stale} stale")

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process with given PID is still running."""
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals by terminating all running jobs."""
        logger.warning(f"Received signal {signum}, terminating running jobs...")
        for job_id, job in self.jobs.items():
            if job["status"] == "running" and "pid" in job:
                try:
                    process = psutil.Process(job["pid"])
                    process.terminate()
                    logger.info(f"Terminated job {job_id} (PID: {job['pid']})")
                except Exception as e:
                    logger.error(f"Failed to terminate job {job_id}: {e}")

    async def execute(
        self,
        cmd: List[str],
        cwd: str = ".",
        env: Optional[Dict[str, str]] = None,
        job_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        on_complete: Optional[Callable[[dict], Any | Awaitable[Any]]] = None,
    ) -> dict:
        """
        Execute command in background subprocess.

        Args:
            cmd: Command as list (e.g., ["python", "script.py", "--arg", "value"])
            cwd: Working directory for subprocess
            env: Optional environment variables
            job_id: Optional pre-determined job ID (for pre-created directories)
            timeout_seconds: Maximum runtime in seconds. If exceeded, job is terminated
                           with status="timeout". Default None (no timeout).
            on_complete: Optional callback invoked once a job reaches a terminal
                          state. Callback errors are recorded on job metadata.

        Returns:
            Job metadata dict with job_id, status, command, etc.
        """
        # Generate or validate job ID
        if job_id is None:
            job_id = str(uuid.uuid4())[:8]
            job_dir = self.jobs_dir / job_id
            job_dir.mkdir(exist_ok=True)
        else:
            # Guardrail: Check for collision (caller must have created directory already)
            job_dir = self.jobs_dir / job_id
            if job_id in self.jobs:
                raise ValueError(f"Job ID {job_id} already exists in active jobs")
            if not job_dir.exists():
                raise ValueError("Pre-created job directory is required when providing custom job_id")
            # Caller already created directory - don't create again

        # Replace {job_id} placeholder in command
        cmd_with_id = [arg.replace("{job_id}", job_id) for arg in cmd]

        # Prepare job metadata
        job = {
            "id": job_id,
            "command": cmd_with_id,
            "cwd": str(Path(cwd).absolute()),
            "status": "starting",
            "started_at": time.time(),
            "job_dir": str(job_dir.absolute()),
            "env": env or {},
            "timeout_seconds": timeout_seconds,
        }

        logger.info(f"Starting job {job_id}: {' '.join(cmd_with_id)}")

        # Save initial metadata
        self._save_job_metadata(job)

        # Check concurrency limit before starting
        async with self._running_lock:
            if self._running_count >= self.max_concurrent_jobs:
                job["status"] = "rejected"
                job["error"] = f"Max concurrent jobs ({self.max_concurrent_jobs}) reached. Use get_job_status to check running jobs."
                job["completed_at"] = time.time()
                self.jobs[job_id] = job
                self._save_job_metadata(job)
                return sanitize_public_payload(job)
            self._running_count += 1

        # Prepare log file paths
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"

        try:
            # Prepare environment with PYTHONUNBUFFERED for immediate output flushing
            proc_env = os.environ.copy()
            proc_env["PYTHONUNBUFFERED"] = "1"
            if env:
                proc_env.update(env)

            # Normalize paths for WSL/Windows interoperability
            cmd_normalized = [normalize_path_for_wsl(arg) for arg in cmd_with_id]
            cwd_normalized = normalize_path_for_wsl(cwd)

            # Start subprocess with PIPE for stdout/stderr
            # We'll use async stream readers to capture output line-by-line to files
            # This ensures real-time capture even on Windows where file handles don't flush properly
            proc = await asyncio.create_subprocess_exec(
                *cmd_normalized,
                cwd=cwd_normalized,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env
            )

            job["pid"] = proc.pid
            job["status"] = "running"
            self.jobs[job_id] = job
            self._processes[job_id] = proc

            # Update metadata with PID
            self._save_job_metadata(job)

            logger.info(f"Job {job_id} started with PID {proc.pid}")

            # Monitor in background with release-on-completion
            # Pass paths for log file writing
            monitor_task = asyncio.create_task(self._monitor_job_with_release(
                job_id, proc, timeout_seconds,
                stdout_path, stderr_path, on_complete
            ))
            self._monitor_tasks[job_id] = monitor_task

        except Exception as e:
            # Decrement counter on failure
            async with self._running_lock:
                self._running_count = max(0, self._running_count - 1)

            job["status"] = "failed"
            job["error"] = sanitize_error_message(e)
            job["completed_at"] = time.time()
            self._save_job_metadata(job)
            logger.error(f"Failed to start job {job_id}: {e}")

        return sanitize_public_payload(job)

    async def _monitor_job_with_release(
        self,
        job_id: str,
        proc: asyncio.subprocess.Process,
        timeout_seconds: Optional[float] = None,
        stdout_path: Optional[Path] = None,
        stderr_path: Optional[Path] = None,
        on_complete: Optional[Callable[[dict], Any | Awaitable[Any]]] = None,
    ):
        """
        Monitor job completion, capture output, and release counter.

        This runs in the background and properly decrements _running_count when complete.
        """
        try:
            await self._monitor_job(
                job_id, proc, timeout_seconds,
                stdout_path, stderr_path
            )
            if on_complete is not None:
                await self._run_completion_callback(job_id, on_complete)
        finally:
            self._processes.pop(job_id, None)
            self._monitor_tasks.pop(job_id, None)
            # Always release counter when job completes (success, failure, or exception)
            async with self._running_lock:
                self._running_count = max(0, self._running_count - 1)
            logger.debug(f"Job {job_id}: released slot, running={self._running_count}/{self.max_concurrent_jobs}")

    async def _run_completion_callback(
        self,
        job_id: str,
        callback: Callable[[dict], Any | Awaitable[Any]],
    ) -> None:
        """Invoke a terminal-job callback without letting it change job status."""
        job = self.jobs.get(job_id)
        if not job:
            return
        if job.get("status") not in {"completed", "failed", "timeout", "terminated", "rejected", "cancelled"}:
            return
        try:
            result = callback(dict(job))
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.error("Job %s completion callback failed: %s", job_id, exc, exc_info=True)
            job["completion_callback_error"] = sanitize_error_message(exc)
            self._save_job_metadata(job)

    async def _stream_to_file(
        self,
        stream: asyncio.StreamReader,
        file_path: Path,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Read from async stream and write to file line by line.

        This ensures output is captured in real-time, even on Windows.
        """
        try:
            with open(file_path, "wb") as f:
                while not stop_event.is_set():
                    try:
                        # Read with short timeout to check stop_event periodically
                        line = await asyncio.wait_for(stream.readline(), timeout=0.5)
                        if line:
                            f.write(line)
                            f.flush()  # Flush immediately for real-time capture
                        elif stream.at_eof():
                            break
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

                # Drain any remaining data after stop signal
                while True:
                    try:
                        line = await asyncio.wait_for(stream.readline(), timeout=0.1)
                        if line:
                            f.write(line)
                        else:
                            break
                    except (asyncio.TimeoutError, Exception):
                        break
        except Exception as e:
            logger.debug(f"Stream reader error for {file_path}: {e}")

    async def _monitor_job(
        self,
        job_id: str,
        proc: asyncio.subprocess.Process,
        timeout_seconds: Optional[float] = None,
        stdout_path: Optional[Path] = None,
        stderr_path: Optional[Path] = None,
    ):
        """
        Monitor job completion and capture output using async stream readers.

        This runs in the background and updates job status when complete.
        Output is captured line-by-line and written to files in real-time,
        ensuring output is available even on timeout or crash.

        If timeout_seconds is specified, the job will be terminated if it exceeds
        the timeout limit.
        """
        job = self.jobs[job_id]
        job_dir = Path(job["job_dir"])

        # Use passed paths or derive from job_dir
        if stdout_path is None:
            stdout_path = job_dir / "stdout.log"
        if stderr_path is None:
            stderr_path = job_dir / "stderr.log"

        # Create stop event for stream readers
        stop_event = asyncio.Event()

        # Start stream reader tasks
        stdout_task = None
        stderr_task = None
        if proc.stdout:
            stdout_task = asyncio.create_task(
                self._stream_to_file(proc.stdout, stdout_path, stop_event)
            )
        if proc.stderr:
            stderr_task = asyncio.create_task(
                self._stream_to_file(proc.stderr, stderr_path, stop_event)
            )

        try:
            if timeout_seconds and timeout_seconds > 0:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    # Timeout exceeded - terminate the process
                    logger.warning(f"Job {job_id} exceeded timeout of {timeout_seconds}s, terminating...")

                    # Signal stream readers to stop
                    stop_event.set()

                    # Try graceful termination first
                    try:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            logger.warning(f"Job {job_id} did not terminate gracefully, killing...")
                            proc.kill()
                            await proc.wait()
                    except ProcessLookupError:
                        pass

                    # Wait for stream readers to finish capturing
                    if stdout_task:
                        try:
                            await asyncio.wait_for(stdout_task, timeout=1.0)
                        except (asyncio.TimeoutError, Exception):
                            stdout_task.cancel()
                    if stderr_task:
                        try:
                            await asyncio.wait_for(stderr_task, timeout=1.0)
                        except (asyncio.TimeoutError, Exception):
                            stderr_task.cancel()

                    # Calculate elapsed time
                    elapsed = time.time() - job["started_at"]

                    # Read last progress message from stdout
                    last_progress = None
                    try:
                        with open(stdout_path, "r", errors="replace") as f:
                            lines = f.readlines()
                            for line in reversed(lines):
                                if "[PROGRESS]" in line:
                                    last_progress = line.strip()
                                    break
                    except Exception as e:
                        logger.debug(f"Could not read last progress: {e}")

                    # Append enhanced timeout context to stderr
                    try:
                        with open(stderr_path, "a") as f:
                            f.write("\n\n")
                            f.write("=" * 60 + "\n")
                            f.write(f"[TIMEOUT] Job exceeded {timeout_seconds}s limit and was terminated.\n")
                            f.write(f"Elapsed: {elapsed:.1f}s\n")
                            if last_progress:
                                f.write(f"Last progress: {last_progress}\n")
                            f.write("=" * 60 + "\n")
                    except Exception as e:
                        logger.debug(f"Could not write timeout context to stderr: {e}")

                    # Update job status
                    job["status"] = "timeout"
                    job["error"] = f"Simulation exceeded {timeout_seconds}s timeout limit"
                    job["exit_code"] = -1
                    job["completed_at"] = time.time()

                    logger.info(f"Job {job_id} terminated due to timeout after {timeout_seconds}s")
                    return
            else:
                # No timeout - wait indefinitely
                await proc.wait()

            # Signal stream readers to stop and wait for completion
            stop_event.set()
            if stdout_task:
                try:
                    await asyncio.wait_for(stdout_task, timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    stdout_task.cancel()
            if stderr_task:
                try:
                    await asyncio.wait_for(stderr_task, timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    stderr_task.cancel()

            # Get exit code
            exit_code = proc.returncode

            # Update job status
            if job.get("status") == "terminating":
                job["status"] = "terminated"
                job["message"] = f"Job {job_id} terminated successfully"
            else:
                job["status"] = "completed" if exit_code == 0 else "failed"
            job["exit_code"] = exit_code
            job["completed_at"] = time.time()

            if exit_code != 0 and job["status"] != "terminated":
                try:
                    with open(stderr_path, "r", errors="replace") as f:
                        job["error"] = sanitize_error_message(f.read(500))
                except Exception:
                    job["error"] = f"Process exited with code {exit_code}"

            logger.info(f"Job {job_id} {job['status']} with exit code {exit_code}")

        except Exception as e:
            stop_event.set()
            job["status"] = "failed"
            job["error"] = sanitize_error_message(f"Monitoring error: {str(e)}")
            job["completed_at"] = time.time()
            logger.error(f"Job {job_id} monitoring failed: {e}")

        finally:
            # Cancel any remaining tasks
            if stdout_task and not stdout_task.done():
                stdout_task.cancel()
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            reader_tasks = [task for task in (stdout_task, stderr_task) if task is not None]
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            self._close_process_transport(proc)
            # Save final metadata
            self._save_job_metadata(job)

    def _save_job_metadata(self, job: dict):
        """Save job metadata to disk."""
        job_dir = Path(job["job_dir"])
        metadata_file = job_dir / "job.json"

        try:
            with open(metadata_file, "w") as f:
                json.dump(job, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata for job {job['id']}: {e}")

    async def get_status(self, job_id: str) -> dict:
        """
        Get job status with progress hints.

        Args:
            job_id: Job identifier

        Returns:
            Dict with status, progress, elapsed_time, etc.
        """
        if job_id not in self.jobs:
            return public_error(f"Job {job_id} not found", code="job_not_found")

        job = self.jobs[job_id]

        # Check for dead process (subprocess crashed without proper cleanup)
        if job["status"] in {"running", "terminating"}:
            pid = job.get("pid")
            if pid and not self._is_process_alive(pid):
                logger.warning(f"Job {job_id}: Process {pid} terminated unexpectedly")
                if job["status"] == "terminating":
                    job["status"] = "terminated"
                    job["message"] = f"Job {job_id} terminated successfully"
                else:
                    job["status"] = "failed"
                    job["error"] = "Process terminated unexpectedly (subprocess crash)"
                job["completed_at"] = time.time()
                self._save_job_metadata(job)

        # Calculate elapsed time
        elapsed = time.time() - job["started_at"]

        # Parse progress from stdout if available
        progress = self._parse_progress(job["job_dir"])

        # Build status response
        status_response = {
            "job_id": job_id,
            "status": job["status"],
            "elapsed_time_seconds": round(elapsed, 1),
            "started_at": job["started_at"]
        }

        if progress:
            status_response["progress"] = progress

        if job["status"] == "completed":
            status_response["completed_at"] = job.get("completed_at")
            status_response["total_time_seconds"] = round(job.get("completed_at", time.time()) - job["started_at"], 1)

        if job["status"] == "failed":
            status_response["message"] = sanitize_error_message(job.get("error", "Unknown error"))
            status_response["exit_code"] = job.get("exit_code")

        if job["status"] == "timeout":
            status_response["message"] = sanitize_error_message(job.get("error", "Timeout exceeded"))
            status_response["timeout_seconds"] = job.get("timeout_seconds")

        if job["status"] == "terminated":
            status_response["completed_at"] = job.get("completed_at")
            status_response["message"] = job.get("message", f"Job {job_id} terminated successfully")

        # Show timeout info for running jobs
        if job["status"] in {"running", "terminating"} and job.get("timeout_seconds"):
            timeout = job["timeout_seconds"]
            status_response["timeout_seconds"] = timeout
            status_response["time_remaining_seconds"] = round(max(0, timeout - elapsed), 1)

        return sanitize_public_payload(status_response)

    def _parse_progress(self, job_dir: str) -> Optional[dict]:
        """
        Parse progress hints from stdout.

        Looks for patterns like:
        - "[PROGRESS] Starting MLE-MBR simulation..."
        - "[PROGRESS] Day 100 - still converging..."
        - "Progress: 45%"
        - "Day 15/20"
        """
        stdout_file = Path(job_dir) / "stdout.log"
        if not stdout_file.exists():
            return None

        try:
            with open(stdout_file, "r") as f:
                lines = f.readlines()

            # Look for progress patterns in last 20 lines
            for line in reversed(lines[-20:]):
                # Pattern: "[PROGRESS] ..." (our structured format)
                if "[PROGRESS]" in line:
                    msg = line.replace("[PROGRESS]", "").strip()
                    percent_match = re.search(r"(\d+(?:\.\d+)?)%", msg)
                    if percent_match:
                        percent = max(0.0, min(100.0, float(percent_match.group(1))))
                        return {"percent": round(percent, 1), "message": msg}
                    if "complete" in line.lower():
                        return {"percent": 100, "message": msg}
                    return {"message": msg}

                # Pattern: "Progress: 45%"
                if "progress:" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        try:
                            percent = int(''.join(filter(str.isdigit, parts[1])))
                            return {"percent": percent, "message": line.strip()}
                        except ValueError:
                            pass

                # Pattern: "Day 15/20"
                if "/" in line and "day" in line.lower():
                    return {"message": line.strip()}

            # If no progress found, return last non-empty line as status
            for line in reversed(lines):
                if line.strip():
                    return {"message": line.strip()[:100]}

        except Exception as e:
            logger.debug(f"Failed to parse progress: {e}")

        return None

    def _close_process_transport(self, proc: asyncio.subprocess.Process) -> None:
        """Close asyncio's private subprocess transport after the process exits."""
        if proc.returncode is None:
            return
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()

    async def get_results(self, job_id: str) -> dict:
        """
        Get results from completed job.

        Args:
            job_id: Job identifier

        Returns:
            Dict with job_id, status, results (parsed JSON), and log file paths
        """
        if job_id not in self.jobs:
            return public_error(f"Job {job_id} not found", code="job_not_found")

        job = self.jobs[job_id]
        job_dir = Path(job["job_dir"])

        if job["status"] != "completed":
            return public_error(
                f"Job {job_id} not completed (status: {job['status']})",
                code="job_not_completed",
                job_id=job_id,
                status=job["status"],
            )

        # Look for common result file patterns
        result_files = [
            "results.json",
            "output_state.json",
            "output.json",
            "simulation_results.json",
            "heuristic_sizing_results.json",
            "validation_results.json",
        ]

        results = None
        result_file_found = None

        for filename in result_files:
            result_path = job_dir / filename
            if result_path.exists():
                try:
                    with open(result_path) as f:
                        results = json.load(f)
                    result_file_found = str(result_path)
                    break
                except Exception as e:
                    logger.error(f"Failed to parse {result_path}: {e}")

        response = {
            "job_id": job_id,
            "status": "completed",
            "total_time_seconds": round(job.get("completed_at", time.time()) - job["started_at"], 1),
            "stdout_file": str(job_dir / "stdout.log"),
            "stderr_file": str(job_dir / "stderr.log")
        }

        if results:
            # Exclude time_series to avoid token limit (use get_timeseries_data tool instead)
            if "time_series" in results:
                response["time_series_available"] = True
                response["time_series_note"] = "Time series data excluded from response. Use get_timeseries_data(job_id) to retrieve."
                # Create filtered copy without time_series
                results_filtered = {k: v for k, v in results.items() if k != "time_series"}
                response["results"] = results_filtered
            else:
                response["results"] = results
            response["result_file"] = result_file_found
        else:
            response["warning"] = "No result JSON file found. Check stdout/stderr logs."

        return sanitize_public_payload(response)

    async def get_timeseries_data(self, job_id: str) -> dict:
        """
        Get time series data for a completed simulation job.

        Args:
            job_id: Job identifier

        Returns:
            Dict with time series data or error
        """
        if job_id not in self.jobs:
            return public_error(f"Job {job_id} not found", code="job_not_found")

        job = self.jobs[job_id]
        job_dir = Path(job["job_dir"])

        if job["status"] != "completed":
            return public_error(
                f"Job {job_id} not completed (status: {job['status']})",
                code="job_not_completed",
                job_id=job_id,
                status=job["status"],
            )

        # Look for simulation results with time_series
        result_path = job_dir / "simulation_results.json"
        if not result_path.exists():
            return public_error(
                "simulation_results.json not found",
                code="timeseries_not_found",
                job_id=job_id,
                note="Time series data only available for simulation jobs",
            )

        try:
            with open(result_path) as f:
                results = json.load(f)

            if "time_series" in results:
                return sanitize_public_payload({
                    "job_id": job_id,
                    "status": "completed",
                    "time_series": results["time_series"],
                    "result_file": str(result_path)
                })
            else:
                return public_error(
                    "No time_series data in results",
                    code="timeseries_not_found",
                    job_id=job_id,
                )
        except Exception as e:
            logger.error(f"Failed to load time series from {result_path}: {e}")
            return public_error(
                f"Failed to parse results: {e}",
                code="timeseries_parse_failed",
                job_id=job_id,
            )

    async def list_jobs(self, status_filter: Optional[str] = None, limit: int = 20) -> dict:
        """
        List all jobs with optional status filter.

        Args:
            status_filter: Filter by status ("running", "completed", "failed", or None for all)
            limit: Maximum number of jobs to return

        Returns:
            Dict with jobs list
        """
        jobs_list = []

        for job_id, job in sorted(self.jobs.items(), key=lambda x: x[1].get("started_at", 0), reverse=True):
            if status_filter and job["status"] != status_filter:
                continue

            jobs_list.append({
                "id": job_id,
                "status": job["status"],
                "command": " ".join(job["command"][:3]) + ("..." if len(job["command"]) > 3 else ""),
                "started_at": job["started_at"],
                "elapsed_time_seconds": round(time.time() - job["started_at"], 1) if job["status"] == "running" else None
            })

            if len(jobs_list) >= limit:
                break

        return sanitize_public_payload({
            "jobs": jobs_list,
            "total": len(jobs_list),
            "filter": status_filter,
            "running_jobs": self._running_count,  # Use counter for accurate tracking
            "max_concurrent": self.max_concurrent_jobs
        })

    async def shutdown(self, timeout_seconds: float = 5.0) -> None:
        """Terminate live subprocesses and drain monitor tasks before event-loop shutdown."""
        live_statuses = {"running", "terminating"}
        for job_id, job in list(self.jobs.items()):
            if job.get("status") in live_statuses:
                try:
                    await self.terminate_job(job_id)
                except Exception as exc:
                    logger.warning("Failed to terminate job %s during shutdown: %s", job_id, exc)

        tasks = list(self._monitor_tasks.values())
        if not tasks:
            return
        await asyncio.gather(
            *(
                asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
                for task in tasks
            ),
            return_exceptions=True,
        )

    async def terminate_job(self, job_id: str) -> dict:
        """
        Terminate a running job.

        Args:
            job_id: Job identifier

        Returns:
            Dict with termination status
        """
        if job_id not in self.jobs:
            return public_error(f"Job {job_id} not found", code="job_not_found")

        job = self.jobs[job_id]

        if job["status"] in {"completed", "failed", "timeout", "terminated", "rejected", "cancelled"}:
            return public_error(
                sanitize_error_message(
                    job.get("message") or job.get("error") or f"Job {job_id} is already terminal"
                ),
                code="already_terminal",
                job_id=job_id,
                status=job["status"],
            )

        if job["status"] not in {"running", "terminating"}:
            return public_error(
                f"Job {job_id} is not running (status: {job['status']})",
                code="job_not_running",
                job_id=job_id,
                status=job["status"],
            )

        pid = job.get("pid")
        if not pid:
            return public_error(
                f"Job {job_id} has no PID recorded",
                code="job_missing_pid",
                job_id=job_id,
                status=job["status"],
            )

        job["status"] = "terminating"
        job["terminating_at"] = time.time()
        self._save_job_metadata(job)

        proc = self._processes.get(job_id)
        monitor_task = self._monitor_tasks.get(job_id)

        try:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    if monitor_task is not None:
                        await asyncio.wait_for(asyncio.shield(monitor_task), timeout=5.0)
                    else:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Job {job_id} did not terminate gracefully, killing...")
                    proc.kill()
                    if monitor_task is not None:
                        await asyncio.wait_for(asyncio.shield(monitor_task), timeout=5.0)
                    else:
                        await proc.wait()
                if monitor_task is None:
                    self._close_process_transport(proc)
            else:
                process = psutil.Process(pid)
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5.0)
                except psutil.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5.0)

                job["status"] = "terminated"
                job["message"] = f"Job {job_id} terminated successfully"
                job["completed_at"] = time.time()
                self._save_job_metadata(job)
                async with self._running_lock:
                    self._running_count = max(0, self._running_count - 1)

            job = self.jobs.get(job_id, job)
            if job.get("status") == "terminating":
                job["status"] = "terminated"
                job["message"] = f"Job {job_id} terminated successfully"
                job["completed_at"] = time.time()
                self._save_job_metadata(job)

            logger.info(f"Terminated job {job_id} (PID: {pid})")
            return {
                "job_id": job_id,
                "status": "terminated",
                "message": job.get("message", f"Job {job_id} terminated successfully"),
            }

        except psutil.NoSuchProcess:
            job["status"] = "terminated"
            job["message"] = "Process already exited before termination request completed"
            job["completed_at"] = time.time()
            self._save_job_metadata(job)
            async with self._running_lock:
                self._running_count = max(0, self._running_count - 1)
            return {
                "job_id": job_id,
                "status": "terminated",
                "message": job["message"],
            }

        except Exception as e:
            logger.error(f"Failed to terminate job {job_id}: {e}")
            return public_error(
                f"Failed to terminate: {e}",
                code="job_termination_failed",
                job_id=job_id,
                status=job.get("status"),
            )
