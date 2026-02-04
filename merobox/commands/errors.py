"""
Typed error classes for merobox.

This module provides a proper error hierarchy for better error handling and debugging:
- MeroboxError: Base exception for all merobox errors
- NodeError: Errors related to node resolution and connectivity
- AuthError: Authentication and authorization errors
- WorkflowError: Workflow execution errors
- ValidationError: Input validation errors
- ClientError: Client/API communication errors
"""

from typing import Any, Optional


class MeroboxError(Exception):
    """Base exception class for all merobox errors.

    Attributes:
        message: Human-readable error message
        code: Optional error code for programmatic handling
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to a dictionary for serialization."""
        result: dict[str, Any] = {
            "type": self.__class__.__name__,
            "message": self.message,
        }
        if self.code:
            result["code"] = self.code
        if self.details:
            result["details"] = self.details
        return result

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class NodeError(MeroboxError):
    """Errors related to node resolution and connectivity.

    Raised when:
    - A node reference cannot be resolved
    - A node is not running or unreachable
    - Node configuration is invalid
    """

    def __init__(
        self,
        message: str,
        node_ref: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.node_ref = node_ref
        details = details or {}
        if node_ref:
            details["node_ref"] = node_ref
        super().__init__(message, code=code, details=details)


class NodeResolutionError(NodeError):
    """Raised when a node cannot be resolved.

    This is a specific type of NodeError for backward compatibility.
    """

    def __init__(
        self,
        message: str,
        node_ref: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message, node_ref=node_ref, code="NODE_RESOLUTION_FAILED", details=details
        )


class AuthError(MeroboxError):
    """Authentication and authorization errors.

    Raised when:
    - Authentication credentials are invalid
    - Token refresh fails
    - User lacks required permissions
    """

    def __init__(
        self,
        message: str,
        node_url: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.node_url = node_url
        details = details or {}
        if node_url:
            details["node_url"] = node_url
        super().__init__(message, code=code, details=details)


class AuthenticationError(AuthError):
    """Raised when authentication fails.

    This is a specific type of AuthError for backward compatibility.
    """

    def __init__(
        self,
        message: str,
        node_url: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message, node_url=node_url, code="AUTHENTICATION_FAILED", details=details
        )


class WorkflowError(MeroboxError):
    """Errors related to workflow execution.

    Raised when:
    - A workflow step fails
    - Workflow configuration is invalid
    - Required workflow dependencies are missing
    """

    def __init__(
        self,
        message: str,
        step_name: Optional[str] = None,
        step_type: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.step_name = step_name
        self.step_type = step_type
        details = details or {}
        if step_name:
            details["step_name"] = step_name
        if step_type:
            details["step_type"] = step_type
        super().__init__(message, code=code, details=details)


class StepValidationError(WorkflowError):
    """Raised when a workflow step validation fails.

    This is a specific type of WorkflowError for step validation issues.
    """

    def __init__(
        self,
        message: str,
        step_name: Optional[str] = None,
        step_type: Optional[str] = None,
        field: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.field = field
        details = details or {}
        if field:
            details["field"] = field
        super().__init__(
            message,
            step_name=step_name,
            step_type=step_type,
            code="STEP_VALIDATION_FAILED",
            details=details,
        )


class StepExecutionError(WorkflowError):
    """Raised when a workflow step execution fails.

    This is a specific type of WorkflowError for step execution issues.
    """

    def __init__(
        self,
        message: str,
        step_name: Optional[str] = None,
        step_type: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            step_name=step_name,
            step_type=step_type,
            code="STEP_EXECUTION_FAILED",
            details=details,
        )


class ValidationError(MeroboxError):
    """Input validation errors.

    Raised when:
    - Function arguments are invalid
    - Configuration values are out of range
    - Required fields are missing
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.field = field
        self.value = value
        details = details or {}
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = value
        super().__init__(
            message, code=code or "VALIDATION_FAILED", details=details
        )


class ClientError(MeroboxError):
    """Client/API communication errors.

    Raised when:
    - HTTP request fails
    - API returns unexpected response
    - Network timeout occurs
    """

    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.url = url
        self.status_code = status_code
        details = details or {}
        if url:
            details["url"] = url
        if status_code is not None:
            details["status_code"] = status_code
        super().__init__(message, code=code, details=details)


class TimeoutError(ClientError):
    """Raised when an operation times out.

    This is a specific type of ClientError for timeout issues.
    """

    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.timeout_seconds = timeout_seconds
        details = details or {}
        if timeout_seconds is not None:
            details["timeout_seconds"] = timeout_seconds
        super().__init__(message, url=url, code="TIMEOUT", details=details)


class ConfigurationError(MeroboxError):
    """Configuration-related errors.

    Raised when:
    - Configuration file is missing or malformed
    - Required configuration values are not set
    - Configuration values conflict with each other
    """

    def __init__(
        self,
        message: str,
        config_file: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        self.config_file = config_file
        details = details or {}
        if config_file:
            details["config_file"] = config_file
        super().__init__(
            message, code=code or "CONFIGURATION_ERROR", details=details
        )


# Export all error classes for convenient importing
__all__ = [
    "MeroboxError",
    "NodeError",
    "NodeResolutionError",
    "AuthError",
    "AuthenticationError",
    "WorkflowError",
    "StepValidationError",
    "StepExecutionError",
    "ValidationError",
    "ClientError",
    "TimeoutError",
    "ConfigurationError",
]
