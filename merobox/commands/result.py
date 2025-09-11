"""
Result utilities for consistent success/error shapes across commands and steps.
"""

import traceback
from typing import Any, Dict, Optional


def ok(data: Optional[Any] = None, **extras: Any) -> Dict[str, Any]:
    """Standard success result shape."""
    result: Dict[str, Any] = {"success": True}
    if data is not None:
        result["data"] = data
    if extras:
        result.update(extras)
    return result


def fail(
    message: str, *, error: Optional[Exception] = None, **extras: Any
) -> Dict[str, Any]:
    """Standard failure result shape with optional exception details."""
    result: Dict[str, Any] = {"success": False, "error": message}
    if error is not None:
        result["exception"] = format_error(error)
    if extras:
        result.update(extras)
    return result


def format_error(error: Exception) -> Dict[str, Any]:
    """Format an exception with type, message, and traceback string."""
    return {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
    }
