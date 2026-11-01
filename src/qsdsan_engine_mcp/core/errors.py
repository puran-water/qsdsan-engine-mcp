"""Public error envelopes with path redaction."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from puran_mcp_support.engineering.safe_paths import redact_mapping, redact_paths


class PublicError(BaseModel):
    """Sanitized error payload returned at public MCP/REST boundaries."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


def sanitize_error_message(message: Any) -> str:
    """Return a public-safe error string with local paths redacted."""

    return redact_paths(str(message))


def sanitize_public_payload(payload: Any) -> Any:
    """Recursively redact local paths in a JSON-like public payload."""

    return redact_mapping(payload)


def public_error(
    message: Any,
    *,
    code: str | None = None,
    correlation_id: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build a sanitized public error envelope."""

    return PublicError(
        code=code or "error",
        message=sanitize_error_message(message),
        details=sanitize_public_payload(details),
        correlation_id=correlation_id or uuid.uuid4().hex,
    ).model_dump(exclude_none=True)


__all__ = [
    "PublicError",
    "public_error",
    "sanitize_error_message",
    "sanitize_public_payload",
]
