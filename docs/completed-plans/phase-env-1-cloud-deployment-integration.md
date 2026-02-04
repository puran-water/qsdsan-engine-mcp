# Phase ENV-1: Multi-Environment Cloud Deployment Integration

## Overview

This phase integrates a multi-environment configuration system that enables the QSDsan MCP server to run seamlessly across local development, Docker containers, and Google Cloud Run—while keeping cloud dependencies **optional** for users who only need local execution.

**Author:** Claude Code (with Rainer/Roger)
**Date:** 2026-01-28
**Status:** Planning Complete, Implementation In Progress

---

## Goals

1. **Preserve local-first development** - Hersh and other contributors continue using `python server.py` with zero cloud dependencies
2. **Enable cloud deployment** - Support Google Cloud Run with GCS storage for production use
3. **Keep cloud optional** - Google Cloud packages are not required for local/Docker use
4. **Maintain backward compatibility** - All 35 MCP tools continue working identically
5. **Clean abstraction** - Single `StorageManager` API regardless of backend

---

## Architecture

### Environment Detection

```
┌─────────────────────────────────────────────────────────────┐
│                    Environment Detection                     │
├─────────────────────────────────────────────────────────────┤
│  1. Check QSDSAN_ENV environment variable (explicit)        │
│  2. Check K_SERVICE (set by Google Cloud Run)               │
│  3. Check /.dockerenv file (Docker container)               │
│  4. Default: LOCAL_DEV                                       │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────────┐  ┌───────────┐
        │ LOCAL_DEV│   │ LOCAL_DOCKER │  │ CLOUD_RUN │
        └────┬─────┘   └──────┬───────┘  └─────┬─────┘
             │                │                │
             ▼                ▼                ▼
     ┌───────────────────────────────┐  ┌─────────────┐
     │    LocalStorageBackend        │  │ GCSBackend  │
     │    (filesystem: ./jobs/)      │  │ (GCS bucket)│
     └───────────────────────────────┘  └─────────────┘
```

### Storage Abstraction

```python
# Unified API - same calls regardless of backend
storage = get_storage_manager()

# These work identically on local filesystem or GCS
storage.save_job_results(job_id, results_dict)
storage.save_diagram(job_id, svg_bytes)
storage.save_session(session_id, session_dict)
url = storage.get_artifact_url(job_id, "diagram")  # file:// or https://
```

---

## File Structure

### New Files

```
qsdsan-engine-mcp/
├── cloud/                          # NEW: Cloud integration module
│   ├── __init__.py
│   ├── config.py                   # Environment detection, configuration
│   ├── storage.py                  # StorageBackend ABC, LocalStorageBackend
│   └── gcs_backend.py              # GCSStorageBackend (lazy-loaded)
├── requirements-cloud.txt          # NEW: Optional cloud dependencies
├── Dockerfile                      # NEW: Container build
├── docker-compose.yaml             # NEW: Local Docker testing
├── deploy.sh                       # NEW: Cloud Run deployment script
├── .env.example                    # NEW: Environment template
└── .dockerignore                   # NEW: Build exclusions
```

### Modified Files

```
├── server.py                       # Add storage manager integration
├── utils/job_manager.py            # Use storage abstraction for persistence
├── utils/flowsheet_session.py      # Use storage abstraction for sessions
├── requirements.txt                # Add aiofiles (async file I/O)
└── CLAUDE.md                       # Document new phase
```

---

## Configuration

### Environment Variables

| Variable | Purpose | Default | Required For |
|----------|---------|---------|--------------|
| `QSDSAN_ENV` | Force environment mode | Auto-detect | Optional override |
| `QSDSAN_JOBS_DIR` | Job storage directory | `./jobs` | Local/Docker |
| `QSDSAN_GCS_BUCKET` | GCS bucket name | - | Cloud Run only |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | - | Cloud Run only |

### Environment Modes

| Mode | Storage | Cloud Deps | Use Case |
|------|---------|------------|----------|
| `LOCAL_DEV` | Filesystem (`./jobs/`) | None | Development |
| `LOCAL_DOCKER` | Filesystem (volume mount) | None | Local containers |
| `CLOUD_RUN` | Google Cloud Storage | Required | Production |

---

## Requirements Split

### requirements.txt (Core - Always Required)

```
# Existing dependencies (unchanged)
qsdsan>=1.3.0
biosteam>=2.38
pydantic>=2.0
fastmcp>=0.5
numpy>=1.24
scipy>=1.10
typer>=0.9
rich>=13.0
jinja2>=3.0
matplotlib>=3.7
psutil>=5.9

# NEW: Async file I/O (lightweight, useful for local too)
aiofiles>=23.0.0
```

### requirements-cloud.txt (Optional - Cloud Deployment Only)

```
# Google Cloud Storage
google-cloud-storage>=2.10.0
google-auth>=2.20.0

# Cloud logging/monitoring
google-cloud-logging>=3.5.0

# HTTP server for health checks
starlette>=0.27.0
uvicorn>=0.23.0
```

### Installation Commands

```bash
# Local development (Hersh, contributors)
pip install -r requirements.txt

# Cloud deployment (Rainer, production)
pip install -r requirements.txt -r requirements-cloud.txt
```

---

## Implementation Details

### 1. cloud/config.py

```python
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
import os

class Environment(Enum):
    LOCAL_DEV = "local_dev"
    LOCAL_DOCKER = "local_docker"
    CLOUD_RUN = "cloud_run"

def detect_environment() -> Environment:
    """Auto-detect runtime environment."""
    # Explicit override
    if env_var := os.getenv("QSDSAN_ENV"):
        return Environment(env_var)

    # Cloud Run sets K_SERVICE
    if os.getenv("K_SERVICE"):
        return Environment.CLOUD_RUN

    # Docker creates /.dockerenv
    if os.path.exists("/.dockerenv"):
        return Environment.LOCAL_DOCKER

    return Environment.LOCAL_DEV

@dataclass
class StorageConfig:
    jobs_dir: Path
    gcs_bucket: str | None = None
    signed_url_expiry_minutes: int = 60

@dataclass
class AppConfig:
    environment: Environment
    storage: StorageConfig
    debug: bool = False

    def is_cloud(self) -> bool:
        return self.environment == Environment.CLOUD_RUN

    def is_local(self) -> bool:
        return self.environment in (Environment.LOCAL_DEV, Environment.LOCAL_DOCKER)

# Singleton
_config: AppConfig | None = None

def get_config() -> AppConfig:
    global _config
    if _config is None:
        env = detect_environment()
        _config = AppConfig(
            environment=env,
            storage=StorageConfig(
                jobs_dir=Path(os.getenv("QSDSAN_JOBS_DIR", "./jobs")),
                gcs_bucket=os.getenv("QSDSAN_GCS_BUCKET"),
            ),
            debug=os.getenv("QSDSAN_DEBUG", "").lower() == "true",
        )
    return _config
```

### 2. cloud/storage.py

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
import json

class StorageBackend(ABC):
    """Abstract base for storage backends."""

    @abstractmethod
    async def save_json(self, path: str, data: dict) -> None: ...

    @abstractmethod
    async def load_json(self, path: str) -> dict | None: ...

    @abstractmethod
    async def save_bytes(self, path: str, data: bytes) -> None: ...

    @abstractmethod
    async def exists(self, path: str) -> bool: ...

    @abstractmethod
    def get_url(self, path: str) -> str: ...

class LocalStorageBackend(StorageBackend):
    """Filesystem storage for local development."""

    def __init__(self, config: StorageConfig):
        self.base_dir = config.jobs_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save_json(self, path: str, data: dict) -> None:
        full_path = self.base_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(json.dumps(data, indent=2, default=str))

    async def load_json(self, path: str) -> dict | None:
        full_path = self.base_dir / path
        if not full_path.exists():
            return None
        return json.loads(full_path.read_text())

    async def save_bytes(self, path: str, data: bytes) -> None:
        full_path = self.base_dir / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    async def exists(self, path: str) -> bool:
        return (self.base_dir / path).exists()

    def get_url(self, path: str) -> str:
        return f"file://{(self.base_dir / path).absolute()}"

class StorageManager:
    """High-level storage API used by job_manager and flowsheet_session."""

    def __init__(self, backend: StorageBackend):
        self.backend = backend

    async def save_job_metadata(self, job_id: str, metadata: dict) -> None:
        await self.backend.save_json(f"{job_id}/job.json", metadata)

    async def load_job_metadata(self, job_id: str) -> dict | None:
        return await self.backend.load_json(f"{job_id}/job.json")

    async def save_job_results(self, job_id: str, results: dict) -> None:
        await self.backend.save_json(f"{job_id}/simulation_results.json", results)

    async def save_diagram(self, job_id: str, svg_data: bytes) -> None:
        await self.backend.save_bytes(f"{job_id}/flowsheet.svg", svg_data)

    async def save_session(self, session_id: str, session: dict) -> None:
        await self.backend.save_json(f"flowsheets/{session_id}/session.json", session)

    async def load_session(self, session_id: str) -> dict | None:
        return await self.backend.load_json(f"flowsheets/{session_id}/session.json")

    def get_artifact_url(self, job_id: str, artifact: str) -> str:
        filename = {
            "results": "simulation_results.json",
            "diagram": "flowsheet.svg",
            "stdout": "stdout.log",
            "stderr": "stderr.log",
        }.get(artifact, artifact)
        return self.backend.get_url(f"{job_id}/{filename}")

# Factory with lazy GCS import
_storage_manager: StorageManager | None = None

def get_storage_manager() -> StorageManager:
    global _storage_manager
    if _storage_manager is None:
        from cloud.config import get_config, Environment
        config = get_config()

        if config.environment == Environment.CLOUD_RUN:
            # Lazy import - only when actually needed
            from cloud.gcs_backend import GCSStorageBackend
            backend = GCSStorageBackend(config.storage)
        else:
            backend = LocalStorageBackend(config.storage)

        _storage_manager = StorageManager(backend)
    return _storage_manager
```

### 3. cloud/gcs_backend.py (Lazy-loaded)

```python
"""GCS backend - only imported when CLOUD_RUN environment detected."""

from google.cloud import storage as gcs
from google.cloud.storage import Blob
from datetime import timedelta
import json

from cloud.storage import StorageBackend, StorageConfig

class GCSStorageBackend(StorageBackend):
    """Google Cloud Storage backend for production."""

    def __init__(self, config: StorageConfig):
        self.client = gcs.Client()
        self.bucket = self.client.bucket(config.gcs_bucket)
        self.expiry = timedelta(minutes=config.signed_url_expiry_minutes)

    async def save_json(self, path: str, data: dict) -> None:
        blob = self.bucket.blob(path)
        blob.upload_from_string(
            json.dumps(data, indent=2, default=str),
            content_type="application/json"
        )

    async def load_json(self, path: str) -> dict | None:
        blob = self.bucket.blob(path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())

    async def save_bytes(self, path: str, data: bytes) -> None:
        blob = self.bucket.blob(path)
        blob.upload_from_string(data)

    async def exists(self, path: str) -> bool:
        return self.bucket.blob(path).exists()

    def get_url(self, path: str) -> str:
        blob = self.bucket.blob(path)
        return blob.generate_signed_url(expiration=self.expiry)
```

### 4. Integration with job_manager.py

The existing `JobManager` class will be updated to optionally use the storage abstraction while maintaining full backward compatibility:

```python
# In utils/job_manager.py

class JobManager:
    def __init__(self, max_concurrent_jobs=3, jobs_base_dir=None):
        # Existing initialization...
        self.jobs_base_dir = Path(jobs_base_dir) if jobs_base_dir else _JOBS_DIR

        # NEW: Optional storage manager (None = use direct filesystem)
        self._storage_manager = None

    def _get_storage_manager(self):
        """Lazy-load storage manager."""
        if self._storage_manager is None:
            try:
                from cloud.storage import get_storage_manager
                self._storage_manager = get_storage_manager()
            except ImportError:
                # Cloud module not available - use direct filesystem
                pass
        return self._storage_manager

    def _save_job_metadata(self, job: dict):
        """Save job metadata - uses storage manager if available."""
        storage = self._get_storage_manager()
        if storage:
            # Use async storage (run in executor for sync context)
            import asyncio
            asyncio.create_task(storage.save_job_metadata(job["id"], job))
        else:
            # Direct filesystem (existing behavior)
            job_path = Path(job["job_dir"]) / "job.json"
            job_path.write_text(json.dumps(job, indent=2, default=str))
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    graphviz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt

# Copy application
COPY . .

# Create jobs directory
RUN mkdir -p /app/jobs

# Environment
ENV PYTHONUNBUFFERED=1
ENV QSDSAN_JOBS_DIR=/app/jobs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s \
    CMD python -c "import server; print('OK')" || exit 1

# Run MCP server
CMD ["python", "server.py"]
```

---

## docker-compose.yaml

```yaml
version: '3.8'

services:
  qsdsan-mcp:
    build: .
    environment:
      - QSDSAN_ENV=local_docker
      - QSDSAN_JOBS_DIR=/app/jobs
      - PYTHONUNBUFFERED=1
    volumes:
      - ./jobs:/app/jobs  # Persist jobs locally
    ports:
      - "8080:8080"  # If HTTP health endpoint added
```

---

## Testing Plan

### 1. Local Development (No Changes Required)

```bash
# Existing workflow continues to work
python server.py

# Run tests
python -m pytest tests/ -v
```

### 2. Local Docker

```bash
docker-compose up --build
# Verify jobs persist to ./jobs/ on host
```

### 3. Cloud Run (Future)

```bash
# Requires GCP setup
./deploy.sh
```

---

## Migration Path

### For Existing Users (Hersh, Contributors)

**No action required.** The server continues to work exactly as before:

```bash
python server.py  # Works identically
```

The `cloud/` module is only loaded when `QSDSAN_ENV=cloud_run` or when running on Cloud Run (K_SERVICE detected).

### For Cloud Deployment

1. Install cloud dependencies: `pip install -r requirements-cloud.txt`
2. Set environment variables (GCS bucket, etc.)
3. Deploy via Cloud Run or Docker

---

## Backward Compatibility Guarantees

| Aspect | Guarantee |
|--------|-----------|
| MCP tool signatures | Unchanged - all 35 tools work identically |
| Job directory structure | Same `jobs/{job_id}/` layout |
| Session directory structure | Same `jobs/flowsheets/{session_id}/` layout |
| CLI interface | Unchanged - `python cli.py` works as before |
| Environment variables | New variables are additive, not replacing |
| Import time | No impact for local dev (cloud modules lazy-loaded) |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Cloud import fails locally | Lazy import with try/except fallback |
| GCS permissions issues | Clear error messages with setup instructions |
| Async/sync mixing | Storage operations run in executor when needed |
| Breaking existing tests | All tests run in LOCAL_DEV mode by default |

---

## Success Criteria

1. ✅ `python server.py` works without any cloud packages installed
2. ✅ All 430+ existing tests pass without modification
3. ✅ Docker build and local container execution work
4. ✅ Cloud Run deployment works with GCS storage
5. ✅ Artifact URLs return `file://` locally, `https://` in cloud

---

## Timeline

| Step | Description |
|------|-------------|
| 1 | Create `cloud/` module with config.py and storage.py |
| 2 | Add gcs_backend.py (lazy-loaded) |
| 3 | Create requirements-cloud.txt |
| 4 | Add Dockerfile and docker-compose.yaml |
| 5 | Integrate storage manager into job_manager.py |
| 6 | Integrate storage manager into flowsheet_session.py |
| 7 | Test local execution (no cloud deps) |
| 8 | Test Docker execution |
| 9 | Update CLAUDE.md documentation |

---

## References

- Source deployment package: `C:\Users\gaierr\Documents\qsdsan-mcp-cloudrun\`
- Existing MCP server: `c:\Users\gaierr\Energy_Projects\projects\WasteWater\qsdsan-engine-mcp\`
- Google Cloud Run docs: https://cloud.google.com/run/docs
- FastMCP framework: https://github.com/jlowin/fastmcp
