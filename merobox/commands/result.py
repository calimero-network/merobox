"""
Result utilities for consistent success/error shapes across commands and steps.
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
        Dictionary with success=False and error details
    """
    result: dict[str, Any] = {"success": False, "error": message}
    if error is not None:
        result["exception"] = format_error(error)
        # Include structured error info for MeroboxError subclasses
        if isinstance(error, MeroboxError):
            result["error_type"] = type(error).__name__
            if error.code:
                result["error_code"] = error.code
            if error.details:
                result["error_details"] = error.details
    if extras:
        result.update(extras)
    return result


def format_error(error: Exception) -> dict[str, Any]:
    """Format an exception with type, message, and traceback string.

    For MeroboxError subclasses, includes additional structured information.

    Args:
        error: The exception to format

    Returns:
        Dictionary with error details
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
