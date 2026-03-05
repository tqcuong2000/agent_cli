"""Machine-readable tool error taxonomy."""

from __future__ import annotations

from enum import Enum


class ToolErrorCode(str, Enum):
    """Canonical error codes emitted in tool result envelopes."""

    # File operations
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    ENCODING_ERROR = "ENCODING_ERROR"

    # Command execution
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"

    # Validation
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"

    # Content
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def retryable(self) -> bool:
        return self in {
            ToolErrorCode.COMMAND_TIMEOUT,
            ToolErrorCode.APPROVAL_TIMEOUT,
            ToolErrorCode.OUTPUT_TRUNCATED,
        }
