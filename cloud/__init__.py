"""
Cloud deployment module for QSDsan Engine MCP.

This module provides multi-environment support for local development,
Docker containers, and Google Cloud Run deployment.

Usage:
    from cloud.config import get_config
    from cloud.storage import get_storage_manager

    config = get_config()
    if config.is_cloud():
        storage = get_storage_manager()
        await storage.save_job_results(job_id, results)

The module auto-detects the runtime environment:
- LOCAL_DEV: Default, filesystem storage in ./jobs/
- LOCAL_DOCKER: Docker container, filesystem storage
- CLOUD_RUN: Google Cloud Run, GCS storage

Cloud dependencies (google-cloud-storage) are lazy-loaded and only
required when running in CLOUD_RUN mode.
"""

from cloud.config import Environment, get_config, detect_environment

__all__ = ["Environment", "get_config", "detect_environment"]
