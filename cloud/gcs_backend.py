"""
Google Cloud Storage backend for QSDsan Engine MCP.

This module is lazy-loaded only when running in CLOUD_RUN environment.
It requires the google-cloud-storage package to be installed.

Install with: pip install -r requirements-cloud.txt
"""

from datetime import timedelta
from pathlib import Path
from typing import Optional
import json
import logging

# These imports will fail if google-cloud-storage is not installed
# That's intentional - this module should only be imported in CLOUD_RUN mode
from google.cloud import storage as gcs
from google.cloud.exceptions import NotFound

from cloud.config import StorageConfig
from cloud.storage import StorageBackend

logger = logging.getLogger(__name__)


class GCSStorageBackend(StorageBackend):
    """
    Google Cloud Storage backend for production deployment.

    Files are stored in a GCS bucket with optional prefix for organization.
    Signed URLs are generated for artifact access with configurable expiration.
    """

    def __init__(self, config: StorageConfig):
        if not config.gcs_bucket:
            raise ValueError(
                "GCS bucket name is required for cloud storage. "
                "Set QSDSAN_GCS_BUCKET environment variable."
            )

        self.client = gcs.Client()
        self.bucket = self.client.bucket(config.gcs_bucket)
        self.prefix = config.gcs_prefix.rstrip("/") + "/" if config.gcs_prefix else ""
        self.expiry = timedelta(minutes=config.signed_url_expiry_minutes)

        logger.info(
            f"GCSStorageBackend initialized: bucket={config.gcs_bucket}, "
            f"prefix={self.prefix}, expiry={config.signed_url_expiry_minutes}min"
        )

    def _blob_path(self, path: str) -> str:
        """Get the full blob path including prefix."""
        return f"{self.prefix}{path}"

    def _get_blob(self, path: str):
        """Get a blob object for the given path."""
        return self.bucket.blob(self._blob_path(path))

    async def save_json(self, path: str, data: dict) -> None:
        blob = self._get_blob(path)
        content = json.dumps(data, indent=2, default=str)
        blob.upload_from_string(content, content_type="application/json")
        logger.debug(f"GCS: Saved JSON: {path}")

    async def load_json(self, path: str) -> Optional[dict]:
        blob = self._get_blob(path)
        try:
            content = blob.download_as_text()
            return json.loads(content)
        except NotFound:
            return None
        except json.JSONDecodeError as e:
            logger.error(f"GCS: Failed to parse JSON at {path}: {e}")
            return None

    async def save_bytes(self, path: str, data: bytes) -> None:
        blob = self._get_blob(path)
        blob.upload_from_string(data)
        logger.debug(f"GCS: Saved bytes: {path} ({len(data)} bytes)")

    async def load_bytes(self, path: str) -> Optional[bytes]:
        blob = self._get_blob(path)
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None

    async def save_text(self, path: str, text: str) -> None:
        blob = self._get_blob(path)
        blob.upload_from_string(text, content_type="text/plain")
        logger.debug(f"GCS: Saved text: {path}")

    async def load_text(self, path: str) -> Optional[str]:
        blob = self._get_blob(path)
        try:
            return blob.download_as_text()
        except NotFound:
            return None

    async def exists(self, path: str) -> bool:
        blob = self._get_blob(path)
        return blob.exists()

    async def delete(self, path: str) -> bool:
        blob = self._get_blob(path)
        try:
            blob.delete()
            logger.debug(f"GCS: Deleted: {path}")
            return True
        except NotFound:
            return False

    async def list_files(self, prefix: str) -> list[str]:
        full_prefix = self._blob_path(prefix)
        blobs = self.client.list_blobs(self.bucket, prefix=full_prefix)

        # Strip the base prefix from results
        base_len = len(self.prefix)
        return [blob.name[base_len:] for blob in blobs]

    def get_url(self, path: str) -> str:
        """
        Generate a signed URL for accessing the file.

        The URL is valid for the configured expiry duration.
        """
        blob = self._get_blob(path)
        try:
            url = blob.generate_signed_url(
                expiration=self.expiry,
                method="GET",
            )
            return url
        except Exception as e:
            logger.error(f"GCS: Failed to generate signed URL for {path}: {e}")
            # Fall back to public URL (may not work without public access)
            return f"gs://{self.bucket.name}/{self._blob_path(path)}"

    def get_local_path(self, path: str) -> Optional[Path]:
        """GCS storage has no local path."""
        return None
