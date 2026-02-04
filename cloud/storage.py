"""
Storage abstraction for QSDsan Engine MCP.

Provides a unified API for file storage that works across:
- Local filesystem (LOCAL_DEV, LOCAL_DOCKER)
- Google Cloud Storage (CLOUD_RUN)

The storage backend is selected automatically based on the detected
environment. GCS dependencies are lazy-loaded only when needed.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, Union
import json
import logging
import os

from cloud.config import StorageConfig

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def save_json(self, path: str, data: dict) -> None:
        """Save a dictionary as JSON to the given path."""
        ...

    @abstractmethod
    async def load_json(self, path: str) -> Optional[dict]:
        """Load JSON from the given path, returns None if not found."""
        ...

    @abstractmethod
    async def save_bytes(self, path: str, data: bytes) -> None:
        """Save raw bytes to the given path."""
        ...

    @abstractmethod
    async def load_bytes(self, path: str) -> Optional[bytes]:
        """Load raw bytes from the given path, returns None if not found."""
        ...

    @abstractmethod
    async def save_text(self, path: str, text: str) -> None:
        """Save text content to the given path."""
        ...

    @abstractmethod
    async def load_text(self, path: str) -> Optional[str]:
        """Load text from the given path, returns None if not found."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file exists at the given path."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """Delete a file at the given path. Returns True if deleted."""
        ...

    @abstractmethod
    async def list_files(self, prefix: str) -> list[str]:
        """List all files with the given prefix."""
        ...

    @abstractmethod
    def get_url(self, path: str) -> str:
        """Get a URL for accessing the file (file:// or https://)."""
        ...

    @abstractmethod
    def get_local_path(self, path: str) -> Optional[Path]:
        """Get local filesystem path if available, None for cloud storage."""
        ...


class LocalStorageBackend(StorageBackend):
    """
    Filesystem storage backend for local development and Docker.

    Files are stored relative to a base directory (default: ./jobs/).
    """

    def __init__(self, config: StorageConfig):
        self.base_dir = config.jobs_dir.absolute()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"LocalStorageBackend initialized: {self.base_dir}")

    def _resolve_path(self, path: str) -> Path:
        """Resolve a relative path to an absolute path under base_dir."""
        # Security: prevent path traversal
        resolved = (self.base_dir / path).resolve()
        if not str(resolved).startswith(str(self.base_dir)):
            raise ValueError(f"Path traversal detected: {path}")
        return resolved

    async def save_json(self, path: str, data: dict) -> None:
        full_path = self._resolve_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug(f"Saved JSON: {path}")

    async def load_json(self, path: str) -> Optional[dict]:
        full_path = self._resolve_path(path)
        if not full_path.exists():
            return None
        try:
            return json.loads(full_path.read_text())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON at {path}: {e}")
            return None

    async def save_bytes(self, path: str, data: bytes) -> None:
        full_path = self._resolve_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
        logger.debug(f"Saved bytes: {path} ({len(data)} bytes)")

    async def load_bytes(self, path: str) -> Optional[bytes]:
        full_path = self._resolve_path(path)
        if not full_path.exists():
            return None
        return full_path.read_bytes()

    async def save_text(self, path: str, text: str) -> None:
        full_path = self._resolve_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(text)
        logger.debug(f"Saved text: {path}")

    async def load_text(self, path: str) -> Optional[str]:
        full_path = self._resolve_path(path)
        if not full_path.exists():
            return None
        return full_path.read_text()

    async def exists(self, path: str) -> bool:
        return self._resolve_path(path).exists()

    async def delete(self, path: str) -> bool:
        full_path = self._resolve_path(path)
        if full_path.exists():
            full_path.unlink()
            logger.debug(f"Deleted: {path}")
            return True
        return False

    async def list_files(self, prefix: str) -> list[str]:
        prefix_path = self._resolve_path(prefix)
        if not prefix_path.exists():
            return []

        base_len = len(str(self.base_dir)) + 1  # +1 for separator
        files = []
        if prefix_path.is_dir():
            for p in prefix_path.rglob("*"):
                if p.is_file():
                    files.append(str(p)[base_len:].replace("\\", "/"))
        return files

    def get_url(self, path: str) -> str:
        full_path = self._resolve_path(path)
        return f"file://{full_path}"

    def get_local_path(self, path: str) -> Optional[Path]:
        return self._resolve_path(path)


class StorageManager:
    """
    High-level storage API for job and session management.

    Provides domain-specific methods that map to the underlying
    storage backend (local or cloud).
    """

    def __init__(self, backend: StorageBackend):
        self.backend = backend

    # -------------------------------------------------------------------------
    # Job storage methods
    # -------------------------------------------------------------------------

    async def save_job_metadata(self, job_id: str, metadata: dict) -> None:
        """Save job metadata (job.json)."""
        await self.backend.save_json(f"{job_id}/job.json", metadata)

    async def load_job_metadata(self, job_id: str) -> Optional[dict]:
        """Load job metadata."""
        return await self.backend.load_json(f"{job_id}/job.json")

    async def save_job_results(self, job_id: str, results: dict) -> None:
        """Save simulation results."""
        await self.backend.save_json(f"{job_id}/simulation_results.json", results)

    async def load_job_results(self, job_id: str) -> Optional[dict]:
        """Load simulation results."""
        return await self.backend.load_json(f"{job_id}/simulation_results.json")

    async def save_job_config(self, job_id: str, config: dict) -> None:
        """Save job configuration."""
        await self.backend.save_json(f"{job_id}/config.json", config)

    async def save_influent(self, job_id: str, influent: dict) -> None:
        """Save influent state."""
        await self.backend.save_json(f"{job_id}/influent.json", influent)

    async def save_diagram(self, job_id: str, svg_data: Union[bytes, str]) -> None:
        """Save flowsheet diagram."""
        if isinstance(svg_data, str):
            svg_data = svg_data.encode("utf-8")
        await self.backend.save_bytes(f"{job_id}/flowsheet.svg", svg_data)

    async def save_report(self, job_id: str, report_name: str, content: str) -> None:
        """Save a report file."""
        await self.backend.save_text(f"{job_id}/{report_name}", content)

    async def append_log(self, job_id: str, log_type: str, line: str) -> None:
        """Append a line to a log file (stdout.log or stderr.log)."""
        path = f"{job_id}/{log_type}.log"
        existing = await self.backend.load_text(path)
        new_content = (existing or "") + line + "\n"
        await self.backend.save_text(path, new_content)

    async def save_log(self, job_id: str, log_type: str, content: str) -> None:
        """Save complete log content."""
        await self.backend.save_text(f"{job_id}/{log_type}.log", content)

    async def load_log(self, job_id: str, log_type: str) -> Optional[str]:
        """Load log content."""
        return await self.backend.load_text(f"{job_id}/{log_type}.log")

    async def job_exists(self, job_id: str) -> bool:
        """Check if a job directory exists."""
        return await self.backend.exists(f"{job_id}/job.json")

    async def list_jobs(self) -> list[str]:
        """List all job IDs."""
        files = await self.backend.list_files("")
        job_ids = set()
        for f in files:
            parts = f.split("/")
            if len(parts) >= 2 and parts[1] == "job.json":
                job_ids.add(parts[0])
        return sorted(job_ids)

    # -------------------------------------------------------------------------
    # Session storage methods
    # -------------------------------------------------------------------------

    async def save_session(self, session_id: str, session: dict) -> None:
        """Save flowsheet session."""
        await self.backend.save_json(
            f"flowsheets/{session_id}/session.json", session
        )

    async def load_session(self, session_id: str) -> Optional[dict]:
        """Load flowsheet session."""
        return await self.backend.load_json(
            f"flowsheets/{session_id}/session.json"
        )

    async def session_exists(self, session_id: str) -> bool:
        """Check if a session exists."""
        return await self.backend.exists(f"flowsheets/{session_id}/session.json")

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        return await self.backend.delete(f"flowsheets/{session_id}/session.json")

    async def list_sessions(self) -> list[str]:
        """List all session IDs."""
        files = await self.backend.list_files("flowsheets")
        session_ids = set()
        for f in files:
            parts = f.split("/")
            if len(parts) >= 3 and parts[2] == "session.json":
                session_ids.add(parts[1])
        return sorted(session_ids)

    # -------------------------------------------------------------------------
    # Artifact URL methods
    # -------------------------------------------------------------------------

    def get_artifact_url(self, job_id: str, artifact: str) -> str:
        """
        Get URL for accessing a job artifact.

        Args:
            job_id: Job identifier
            artifact: Artifact type ('results', 'diagram', 'stdout', 'stderr')
                      or filename

        Returns:
            URL string (file:// for local, https:// for cloud)
        """
        filename_map = {
            "results": "simulation_results.json",
            "diagram": "flowsheet.svg",
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "config": "config.json",
            "influent": "influent.json",
        }
        filename = filename_map.get(artifact, artifact)
        return self.backend.get_url(f"{job_id}/{filename}")

    def get_local_path(self, job_id: str, filename: str) -> Optional[Path]:
        """Get local filesystem path for a job file (None for cloud storage)."""
        return self.backend.get_local_path(f"{job_id}/{filename}")


# Singleton instance
_storage_manager: Optional[StorageManager] = None


def get_storage_manager() -> StorageManager:
    """
    Get the storage manager singleton.

    The backend is selected automatically based on the detected environment:
    - LOCAL_DEV, LOCAL_DOCKER: LocalStorageBackend
    - CLOUD_RUN: GCSStorageBackend (lazy-loaded)

    Returns:
        StorageManager instance
    """
    global _storage_manager

    if _storage_manager is None:
        from cloud.config import get_config, Environment

        config = get_config()

        if config.environment == Environment.CLOUD_RUN:
            # Lazy import GCS backend only when needed
            try:
                from cloud.gcs_backend import GCSStorageBackend
                backend = GCSStorageBackend(config.storage)
                logger.info("Using GCS storage backend")
            except ImportError as e:
                logger.error(
                    f"Failed to import GCS backend: {e}. "
                    f"Install with: pip install -r requirements-cloud.txt"
                )
                raise
        else:
            backend = LocalStorageBackend(config.storage)
            logger.info(f"Using local storage backend: {config.storage.jobs_dir}")

        _storage_manager = StorageManager(backend)

    return _storage_manager


def reset_storage_manager() -> None:
    """Reset the storage manager singleton (useful for testing)."""
    global _storage_manager
    _storage_manager = None
