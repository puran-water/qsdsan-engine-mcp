"""
Multi-environment configuration for QSDsan Engine MCP.

Supports three deployment environments:
- LOCAL_DEV: Development on host machine (default)
- LOCAL_DOCKER: Docker container with volume mounts
- CLOUD_RUN: Google Cloud Run with GCS storage

Environment is auto-detected but can be overridden via QSDSAN_ENV.
"""

from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)


class Environment(Enum):
    """Runtime environment modes."""
    LOCAL_DEV = "local_dev"
    LOCAL_DOCKER = "local_docker"
    CLOUD_RUN = "cloud_run"


def detect_environment() -> Environment:
    """
    Auto-detect the runtime environment.

    Detection order:
    1. QSDSAN_ENV environment variable (explicit override)
    2. K_SERVICE (set automatically by Google Cloud Run)
    3. /.dockerenv file (created by Docker)
    4. Default: LOCAL_DEV

    Returns:
        Detected Environment enum value
    """
    # Explicit override via environment variable
    if env_var := os.getenv("QSDSAN_ENV"):
        try:
            env = Environment(env_var.lower())
            logger.info(f"Environment set via QSDSAN_ENV: {env.value}")
            return env
        except ValueError:
            valid = [e.value for e in Environment]
            logger.warning(
                f"Invalid QSDSAN_ENV value '{env_var}', valid options: {valid}. "
                f"Falling back to auto-detection."
            )

    # Cloud Run detection (K_SERVICE is set by GCP)
    if os.getenv("K_SERVICE"):
        logger.info("Cloud Run detected (K_SERVICE present)")
        return Environment.CLOUD_RUN

    # Docker detection
    if os.path.exists("/.dockerenv"):
        logger.info("Docker container detected (/.dockerenv present)")
        return Environment.LOCAL_DOCKER

    # Default: local development
    logger.debug("Defaulting to LOCAL_DEV environment")
    return Environment.LOCAL_DEV


@dataclass
class StorageConfig:
    """Storage backend configuration."""

    # Local filesystem path for jobs/sessions
    jobs_dir: Path = field(default_factory=lambda: Path("./jobs"))

    # GCS bucket name (required for CLOUD_RUN)
    gcs_bucket: Optional[str] = None

    # Signed URL expiration for GCS artifacts
    signed_url_expiry_minutes: int = 60

    # GCS prefix for organizing files
    gcs_prefix: str = ""


@dataclass
class ServerConfig:
    """MCP server configuration."""

    # Server name for FastMCP
    name: str = "qsdsan-engine"

    # Maximum concurrent background jobs
    max_concurrent_jobs: int = 3

    # Default job timeout in seconds (None = no timeout)
    default_timeout_seconds: Optional[int] = None

    # Host/port for HTTP health endpoint (if enabled)
    http_host: str = "0.0.0.0"
    http_port: int = 8080


@dataclass
class QSDsanConfig:
    """QSDsan simulation configuration."""

    # Default simulation duration
    default_duration_days: float = 30.0

    # Convergence detection defaults
    convergence_atol: float = 0.1
    convergence_rtol: float = 1e-3
    max_duration_days: float = 100.0

    # SRT control defaults
    srt_tolerance: float = 0.1
    max_srt_iterations: int = 10


@dataclass
class AppConfig:
    """Main application configuration."""

    environment: Environment
    storage: StorageConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    qsdsan: QSDsanConfig = field(default_factory=QSDsanConfig)
    debug: bool = False

    def is_cloud(self) -> bool:
        """Check if running in cloud environment."""
        return self.environment == Environment.CLOUD_RUN

    def is_local(self) -> bool:
        """Check if running in local environment (dev or docker)."""
        return self.environment in (Environment.LOCAL_DEV, Environment.LOCAL_DOCKER)

    def is_docker(self) -> bool:
        """Check if running in any Docker environment."""
        return self.environment in (Environment.LOCAL_DOCKER, Environment.CLOUD_RUN)


# Singleton instance
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """
    Get the application configuration singleton.

    Configuration is built from environment variables on first call
    and cached for subsequent calls.

    Environment variables:
        QSDSAN_ENV: Override environment detection
        QSDSAN_JOBS_DIR: Local jobs directory path
        QSDSAN_GCS_BUCKET: GCS bucket name (cloud only)
        QSDSAN_GCS_PREFIX: GCS path prefix (cloud only)
        QSDSAN_DEBUG: Enable debug mode
        QSDSAN_MAX_JOBS: Max concurrent jobs
        QSDSAN_DEFAULT_TIMEOUT: Default job timeout in seconds

    Returns:
        AppConfig instance
    """
    global _config

    if _config is None:
        env = detect_environment()

        # Build storage config
        storage = StorageConfig(
            jobs_dir=Path(os.getenv("QSDSAN_JOBS_DIR", "./jobs")),
            gcs_bucket=os.getenv("QSDSAN_GCS_BUCKET"),
            gcs_prefix=os.getenv("QSDSAN_GCS_PREFIX", ""),
            signed_url_expiry_minutes=int(
                os.getenv("QSDSAN_SIGNED_URL_EXPIRY", "60")
            ),
        )

        # Build server config
        server = ServerConfig(
            max_concurrent_jobs=int(os.getenv("QSDSAN_MAX_JOBS", "3")),
            default_timeout_seconds=(
                int(os.getenv("QSDSAN_DEFAULT_TIMEOUT"))
                if os.getenv("QSDSAN_DEFAULT_TIMEOUT")
                else None
            ),
        )

        # Validate cloud config
        if env == Environment.CLOUD_RUN and not storage.gcs_bucket:
            logger.warning(
                "CLOUD_RUN environment detected but QSDSAN_GCS_BUCKET not set. "
                "Storage operations may fail."
            )

        _config = AppConfig(
            environment=env,
            storage=storage,
            server=server,
            debug=os.getenv("QSDSAN_DEBUG", "").lower() in ("true", "1", "yes"),
        )

        logger.info(f"Configuration loaded: environment={env.value}, debug={_config.debug}")

    return _config


def reset_config() -> None:
    """Reset the configuration singleton (useful for testing)."""
    global _config
    _config = None
