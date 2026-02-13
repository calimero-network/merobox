"""
Unit tests for the merobox typed error classes.
"""

from merobox.commands.errors import (
    AuthenticationError,
    AuthError,
    ClientError,
    ConfigurationError,
    MeroboxError,
    NodeError,
    NodeResolutionError,
    StepExecutionError,
    StepValidationError,
    TimeoutError,
    ValidationError,
    WorkflowError,
)


class TestMeroboxError:
    """Tests for the base MeroboxError class."""

    def test_basic_error(self):
        """Test basic error creation with message only."""
        error = MeroboxError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.code is None
        assert error.details == {}

    def test_error_with_code(self):
        """Test error with code."""
        error = MeroboxError("Something went wrong", code="ERR_001")
        assert str(error) == "[ERR_001] Something went wrong"
        assert error.code == "ERR_001"

    def test_error_with_details(self):
        """Test error with details."""
        error = MeroboxError(
            "Something went wrong", details={"context": "test", "value": 42}
        )
        assert error.details == {"context": "test", "value": 42}

    def test_to_dict(self):
        """Test error serialization to dictionary."""
        error = MeroboxError("Test error", code="TEST_CODE", details={"key": "value"})
        result = error.to_dict()
        assert result == {
            "type": "MeroboxError",
            "message": "Test error",
            "code": "TEST_CODE",
            "details": {"key": "value"},
        }

    def test_to_dict_minimal(self):
        """Test minimal error serialization."""
        error = MeroboxError("Test error")
        result = error.to_dict()
        assert result == {"type": "MeroboxError", "message": "Test error"}


class TestNodeError:
    """Tests for NodeError and NodeResolutionError."""

    def test_node_error_basic(self):
        """Test basic NodeError."""
        error = NodeError("Node not found")
        assert str(error) == "Node not found"
        assert error.node_ref is None

    def test_node_error_with_ref(self):
        """Test NodeError with node reference."""
        error = NodeError("Node not found", node_ref="my-node")
        assert error.node_ref == "my-node"
        assert error.details["node_ref"] == "my-node"

    def test_node_resolution_error(self):
        """Test NodeResolutionError inherits from NodeError."""
        error = NodeResolutionError("Cannot resolve node", node_ref="test-node")
        assert isinstance(error, NodeError)
        assert isinstance(error, MeroboxError)
        assert error.code == "NODE_RESOLUTION_FAILED"
        assert error.node_ref == "test-node"

    def test_node_error_inheritance(self):
        """Test that NodeError can be caught as MeroboxError."""
        try:
            raise NodeError("Test")
        except MeroboxError as e:
            assert str(e) == "Test"


class TestAuthError:
    """Tests for AuthError and AuthenticationError."""

    def test_auth_error_basic(self):
        """Test basic AuthError."""
        error = AuthError("Invalid credentials")
        assert str(error) == "Invalid credentials"
        assert error.node_url is None

    def test_auth_error_with_url(self):
        """Test AuthError with node URL."""
        error = AuthError("Authentication failed", node_url="http://localhost:2428")
        assert error.node_url == "http://localhost:2428"
        assert error.details["node_url"] == "http://localhost:2428"

    def test_authentication_error(self):
        """Test AuthenticationError inherits from AuthError."""
        error = AuthenticationError("Login failed")
        assert isinstance(error, AuthError)
        assert isinstance(error, MeroboxError)
        assert error.code == "AUTHENTICATION_FAILED"

    def test_auth_error_to_dict(self):
        """Test AuthError serialization."""
        error = AuthenticationError("Token expired", node_url="http://node:2428")
        result = error.to_dict()
        assert result["type"] == "AuthenticationError"
        assert result["code"] == "AUTHENTICATION_FAILED"
        assert result["details"]["node_url"] == "http://node:2428"


class TestWorkflowError:
    """Tests for WorkflowError and subclasses."""

    def test_workflow_error_basic(self):
        """Test basic WorkflowError."""
        error = WorkflowError("Step failed")
        assert str(error) == "Step failed"
        assert error.step_name is None
        assert error.step_type is None

    def test_workflow_error_with_step_info(self):
        """Test WorkflowError with step information."""
        error = WorkflowError(
            "Execution failed", step_name="create_context", step_type="context"
        )
        assert error.step_name == "create_context"
        assert error.step_type == "context"
        assert error.details["step_name"] == "create_context"
        assert error.details["step_type"] == "context"

    def test_step_validation_error(self):
        """Test StepValidationError."""
        error = StepValidationError(
            "Invalid field",
            step_name="test_step",
            step_type="execute",
            field="context_id",
        )
        assert isinstance(error, WorkflowError)
        assert error.code == "STEP_VALIDATION_FAILED"
        assert error.field == "context_id"
        assert error.details["field"] == "context_id"

    def test_step_execution_error(self):
        """Test StepExecutionError."""
        error = StepExecutionError(
            "Failed to execute", step_name="call_function", step_type="execute"
        )
        assert isinstance(error, WorkflowError)
        assert error.code == "STEP_EXECUTION_FAILED"


class TestValidationError:
    """Tests for ValidationError."""

    def test_validation_error_basic(self):
        """Test basic ValidationError."""
        error = ValidationError("Invalid input")
        assert str(error) == "[VALIDATION_FAILED] Invalid input"
        assert error.code == "VALIDATION_FAILED"

    def test_validation_error_with_field(self):
        """Test ValidationError with field information."""
        error = ValidationError(
            "Port must be between 1 and 65535", field="port", value=70000
        )
        assert error.field == "port"
        assert error.value == 70000
        assert error.details["field"] == "port"
        assert error.details["value"] == 70000


class TestClientError:
    """Tests for ClientError and TimeoutError."""

    def test_client_error_basic(self):
        """Test basic ClientError."""
        error = ClientError("Request failed")
        assert str(error) == "Request failed"

    def test_client_error_with_status(self):
        """Test ClientError with status code."""
        error = ClientError(
            "Server error", url="http://api.example.com/endpoint", status_code=500
        )
        assert error.url == "http://api.example.com/endpoint"
        assert error.status_code == 500
        assert error.details["url"] == "http://api.example.com/endpoint"
        assert error.details["status_code"] == 500

    def test_timeout_error(self):
        """Test TimeoutError."""
        error = TimeoutError(
            "Request timed out", url="http://slow-server.com", timeout_seconds=30.0
        )
        assert isinstance(error, ClientError)
        assert error.code == "TIMEOUT"
        assert error.timeout_seconds == 30.0
        assert error.details["timeout_seconds"] == 30.0


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_configuration_error_basic(self):
        """Test basic ConfigurationError."""
        error = ConfigurationError("Invalid configuration")
        assert str(error) == "[CONFIGURATION_ERROR] Invalid configuration"
        assert error.code == "CONFIGURATION_ERROR"

    def test_configuration_error_with_file(self):
        """Test ConfigurationError with config file."""
        error = ConfigurationError(
            "Missing required field", config_file="/path/to/config.yml"
        )
        assert error.config_file == "/path/to/config.yml"
        assert error.details["config_file"] == "/path/to/config.yml"


class TestErrorHierarchy:
    """Tests for the error hierarchy and inheritance."""

    def test_all_errors_inherit_from_merobox_error(self):
        """Test that all custom errors inherit from MeroboxError."""
        errors = [
            NodeError("test"),
            NodeResolutionError("test"),
            AuthError("test"),
            AuthenticationError("test"),
            WorkflowError("test"),
            StepValidationError("test"),
            StepExecutionError("test"),
            ValidationError("test"),
            ClientError("test"),
            TimeoutError("test"),
            ConfigurationError("test"),
        ]
        for error in errors:
            assert isinstance(error, MeroboxError)
            assert isinstance(error, Exception)

    def test_error_catching_hierarchy(self):
        """Test that errors can be caught at different levels."""
        # Test catching NodeResolutionError as NodeError
        try:
            raise NodeResolutionError("test")
        except NodeError:
            pass  # Should be caught

        # Test catching AuthenticationError as AuthError
        try:
            raise AuthenticationError("test")
        except AuthError:
            pass  # Should be caught

        # Test catching StepValidationError as WorkflowError
        try:
            raise StepValidationError("test")
        except WorkflowError:
            pass  # Should be caught

        # Test catching TimeoutError as ClientError
        try:
            raise TimeoutError("test")
        except ClientError:
            pass  # Should be caught

    def test_error_is_exception(self):
        """Test that MeroboxError is a proper Exception subclass."""
        error = MeroboxError("test")
        assert isinstance(error, Exception)
        assert isinstance(error, BaseException)
