"""
Result utilities for consistent success/error shapes across commands and steps.

Note: Error details and tracebacks may be included in results. Ensure that
sensitive information (passwords, API keys, tokens) is not passed to error
classes, as it may be logged or returned in API responses.
"""

import traceback
from typing import Any, Optional

from merobox.commands.errors import MeroboxError


def ok(data: Optional[Any] = None, **extras: Any) -> dict[str, Any]:
    """Standard success result shape."""
    result: dict[str, Any] = {"success": True}
    if data is not None:
        result["data"] = data
    if extras:
        result.update(extras)
    return result


def fail(
    message: str, *, error: Optional[Exception] = None, **extras: Any
) -> dict[str, Any]:
    """Standard failure result shape with optional exception details.

    Args:
        message: Human-readable error message
        error: Optional exception that caused the failure
        **extras: Additional fields to include in the result

    Returns:
        Dictionary with success=False and error details.
        For MeroboxError subclasses, includes error_type, error_code, and
        error_details at the top level for convenience.
    """
    result: dict[str, Any] = {"success": False, "error": message}
    if error is not None:
        formatted = format_error(error)
        result["exception"] = formatted
        # Include structured error info at top level for easy access
        # (derived from format_error output to avoid duplication)
        result["error_type"] = formatted["type"]
        if "code" in formatted:
            result["error_code"] = formatted["code"]
        if "details" in formatted:
            result["error_details"] = formatted["details"]
    if extras:
        result.update(extras)
    return result


def format_error(error: Exception) -> dict[str, Any]:
    """Format an exception with type, message, and traceback string.

    For MeroboxError subclasses, includes additional structured information
    (code and details).

    Args:
        error: The exception to format

    Returns:
        Dictionary with error details including type, message, and traceback.

    Note:
        Tracebacks expose internal file paths and code structure. Consider
        filtering or omitting tracebacks when returning errors to external clients.
    """
    result = {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    }
    # Include structured error info for MeroboxError subclasses
    if isinstance(error, MeroboxError):
        if error.code:
            result["code"] = error.code
        if error.details:
            result["details"] = error.details
    return result
