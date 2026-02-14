"""Unit tests for workflow schema validation.

This test module uses a conftest fixture to load the config module
while avoiding import chain issues with external dependencies.
"""

import importlib.util
import os
import sys
import tempfile
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="module")
def config_module():
    """Load the config module with mocked dependencies."""
    # Create a simple mock for the console
    console_mock = MagicMock()
    console_mock.print = lambda *args, **kwargs: None

    # Create a utils module mock
    utils_mock = ModuleType("merobox.commands.utils")
    utils_mock.console = console_mock

    # Store original modules
    original_modules = {}
    modules_to_mock = ["merobox.commands.utils"]
    for mod_name in modules_to_mock:
        original_modules[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = utils_mock

    try:
        # Load the config module directly
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "commands",
            "bootstrap",
            "config.py",
        )
        spec = importlib.util.spec_from_file_location("config", config_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        # Restore original modules
        for mod_name, original in original_modules.items():
            if original is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = original


class TestValidateWorkflowStep:
    """Tests for validate_workflow_step function."""

    def test_valid_install_application_step(self, config_module):
        """Test validation of a valid install_application step."""
        step = {
            "name": "Install App",
            "type": "install_application",
            "node": "calimero-node-1",
            "path": "./app.wasm",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_create_context_step(self, config_module):
        """Test validation of a valid create_context step."""
        step = {
            "name": "Create Context",
            "type": "create_context",
            "node": "calimero-node-1",
            "application_id": "{{app_id}}",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_call_step(self, config_module):
        """Test validation of a valid call step."""
        step = {
            "name": "Execute Call",
            "type": "call",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "method": "set",
            "args": {"key": "value"},
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_wait_step(self, config_module):
        """Test validation of a valid wait step."""
        step = {
            "name": "Wait",
            "type": "wait",
            "seconds": 5,
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_wait_for_sync_step(self, config_module):
        """Test validation of a valid wait_for_sync step."""
        step = {
            "name": "Wait for Sync",
            "type": "wait_for_sync",
            "context_id": "{{context_id}}",
            "nodes": ["calimero-node-1", "calimero-node-2"],
            "timeout": 60,
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_repeat_step(self, config_module):
        """Test validation of a valid repeat step."""
        step = {
            "name": "Repeat",
            "type": "repeat",
            "count": 3,
            "steps": [
                {
                    "name": "Wait",
                    "type": "wait",
                    "seconds": 1,
                }
            ],
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_valid_script_step(self, config_module):
        """Test validation of a valid script step."""
        step = {
            "name": "Run Script",
            "type": "script",
            "script": "./test.sh",
            "target": "nodes",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_missing_type_field(self, config_module):
        """Test that missing type field is detected."""
        step = {
            "name": "Missing Type",
            "node": "calimero-node-1",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 1
        assert "Missing required field 'type'" in errors[0]

    def test_invalid_step_type(self, config_module):
        """Test that invalid step type is detected."""
        step = {
            "name": "Invalid Type",
            "type": "invalid_step_type",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 1
        assert "Invalid step type" in errors[0]
        assert "invalid_step_type" in errors[0]

    def test_missing_required_field_node(self, config_module):
        """Test that missing required 'node' field is detected."""
        step = {
            "name": "Missing Node",
            "type": "install_application",
            "path": "./app.wasm",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
        assert any("node" in err.lower() for err in errors)

    def test_missing_required_field_path(self, config_module):
        """Test that missing required 'path' field is detected."""
        step = {
            "name": "Missing Path",
            "type": "install_application",
            "node": "calimero-node-1",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
        assert any("path" in err.lower() for err in errors)

    def test_invalid_wait_seconds_negative(self, config_module):
        """Test that negative seconds in wait step is detected."""
        step = {
            "name": "Invalid Wait",
            "type": "wait",
            "seconds": -5,
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0

    def test_nested_repeat_step_validation(self, config_module):
        """Test validation of nested steps in repeat."""
        step = {
            "name": "Repeat with Invalid Nested",
            "type": "repeat",
            "count": 2,
            "steps": [
                {
                    "name": "Invalid Step",
                    "type": "invalid_type",
                }
            ],
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
        assert any("invalid_type" in err.lower() for err in errors)

    def test_nested_parallel_step_validation(self, config_module):
        """Test validation of nested steps in parallel groups."""
        step = {
            "name": "Parallel with Invalid Nested",
            "type": "parallel",
            "groups": [
                {
                    "name": "Group 1",
                    "steps": [
                        {
                            "name": "Invalid Step",
                            "type": "invalid_type",
                        }
                    ],
                }
            ],
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
        assert any("invalid_type" in err.lower() for err in errors)


class TestValidateWorkflowConfig:
    """Tests for validate_workflow_config function."""

    def test_valid_minimal_config(self, config_module):
        """Test validation of a minimal valid config."""
        config = {
            "name": "Test Workflow",
            "nodes": {
                "count": 2,
                "prefix": "calimero-node",
            },
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) == 0

    def test_valid_config_with_remote_nodes(self, config_module):
        """Test validation of config with remote nodes."""
        config = {
            "name": "Remote Workflow",
            "remote_nodes": {
                "node1": {
                    "url": "http://example.com:2428",
                }
            },
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) == 0

    def test_valid_full_config(self, config_module):
        """Test validation of a full config with all options."""
        config = {
            "name": "Full Workflow",
            "description": "A comprehensive test workflow",
            "nodes": {
                "count": 3,
                "prefix": "test-node",
                "chain_id": "testnet-1",
                "image": "ghcr.io/test/image:latest",
            },
            "steps": [
                {
                    "name": "Install App",
                    "type": "install_application",
                    "node": "test-node-1",
                    "path": "./app.wasm",
                },
                {
                    "name": "Wait",
                    "type": "wait",
                    "seconds": 5,
                },
            ],
            "stop_all_nodes": True,
            "wait_timeout": 120,
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) == 0

    def test_missing_name_field(self, config_module):
        """Test that missing name field is detected."""
        config = {
            "nodes": {
                "count": 2,
            },
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) > 0
        assert any("name" in err.lower() for err in errors)

    def test_missing_nodes_and_remote_nodes(self, config_module):
        """Test that missing both nodes and remote_nodes is detected."""
        config = {
            "name": "No Nodes Workflow",
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) > 0
        assert any(
            "nodes" in err.lower() or "remote_nodes" in err.lower() for err in errors
        )

    def test_invalid_step_in_config(self, config_module):
        """Test that invalid steps are detected in full config."""
        config = {
            "name": "Workflow with Invalid Step",
            "nodes": {
                "count": 1,
            },
            "steps": [
                {
                    "name": "Invalid",
                    "type": "nonexistent_type",
                }
            ],
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) > 0
        assert any("nonexistent_type" in err.lower() for err in errors)

    def test_invalid_wait_timeout(self, config_module):
        """Test that invalid wait_timeout is detected."""
        config = {
            "name": "Invalid Timeout Workflow",
            "nodes": {
                "count": 1,
            },
            "wait_timeout": 0,  # Invalid: must be >= 1
        }
        errors = config_module.validate_workflow_config(config)
        assert len(errors) > 0


class TestFormatValidationErrors:
    """Tests for format_validation_errors function."""

    def test_empty_errors(self, config_module):
        """Test formatting with no errors."""
        result = config_module.format_validation_errors([])
        assert result == ""

    def test_single_error(self, config_module):
        """Test formatting with a single error."""
        errors = ["Missing required field: name"]
        result = config_module.format_validation_errors(errors)
        assert "Workflow configuration validation failed" in result
        assert "Missing required field: name" in result

    def test_multiple_errors(self, config_module):
        """Test formatting with multiple errors."""
        errors = [
            "Missing required field: name",
            "Invalid step type: unknown",
            "Step 'test' missing required field: node",
        ]
        result = config_module.format_validation_errors(errors)
        assert "Workflow configuration validation failed" in result
        for err in errors:
            assert err in result


class TestLoadWorkflowConfig:
    """Tests for load_workflow_config function with schema validation."""

    def test_load_valid_config(self, config_module):
        """Test loading a valid workflow config."""
        config_content = """
name: Test Workflow
nodes:
  count: 2
  prefix: calimero-node
steps:
  - name: Wait
    type: wait
    seconds: 5
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(config_content)
            f.flush()
            try:
                config = config_module.load_workflow_config(f.name)
                assert config["name"] == "Test Workflow"
                assert config["nodes"]["count"] == 2
                assert len(config["steps"]) == 1
            finally:
                os.unlink(f.name)

    def test_load_invalid_config_raises_error(self, config_module):
        """Test that loading an invalid config raises ValueError."""
        config_content = """
name: Invalid Workflow
nodes:
  count: 2
steps:
  - name: Invalid Step
    type: nonexistent_type
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(config_content)
            f.flush()
            try:
                with pytest.raises(ValueError) as exc_info:
                    config_module.load_workflow_config(f.name)
                assert "nonexistent_type" in str(exc_info.value).lower()
            finally:
                os.unlink(f.name)

    def test_load_config_skip_schema_validation(self, config_module):
        """Test loading config with schema validation skipped."""
        config_content = """
name: Workflow with Issues
nodes:
  count: 2
steps:
  - name: Invalid Step
    type: nonexistent_type
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(config_content)
            f.flush()
            try:
                # Should not raise with skip_schema_validation=True
                config = config_module.load_workflow_config(
                    f.name, skip_schema_validation=True
                )
                assert config["name"] == "Workflow with Issues"
            finally:
                os.unlink(f.name)

    def test_load_missing_file(self, config_module):
        """Test loading a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            config_module.load_workflow_config("/nonexistent/path/workflow.yml")

    def test_load_invalid_yaml(self, config_module):
        """Test loading invalid YAML raises ValueError."""
        config_content = """
name: Test
nodes: [
  invalid yaml here
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(config_content)
            f.flush()
            try:
                with pytest.raises(ValueError) as exc_info:
                    config_module.load_workflow_config(f.name)
                assert "yaml" in str(exc_info.value).lower()
            finally:
                os.unlink(f.name)


class TestValidStepTypes:
    """Tests for VALID_STEP_TYPES constant."""

    def test_all_expected_types_present(self, config_module):
        """Test that all expected step types are in VALID_STEP_TYPES."""
        expected_types = [
            "install_application",
            "create_context",
            "create_identity",
            "invite_identity",
            "join_context",
            "invite_open",
            "join_open",
            "call",
            "wait",
            "wait_for_sync",
            "repeat",
            "parallel",
            "script",
            "assert",
            "json_assert",
            "get_proposal",
            "list_proposals",
            "get_proposal_approvers",
            "upload_blob",
            "create_mesh",
            "fuzzy_test",
        ]
        for step_type in expected_types:
            assert (
                step_type in config_module.VALID_STEP_TYPES
            ), f"Missing step type: {step_type}"

    def test_no_duplicate_types(self, config_module):
        """Test that there are no duplicate step types."""
        assert len(config_module.VALID_STEP_TYPES) == len(
            set(config_module.VALID_STEP_TYPES)
        )


class TestStepSpecificValidation:
    """Tests for step-specific validation rules."""

    def test_invite_identity_all_required_fields(self, config_module):
        """Test invite_identity step requires all fields."""
        step = {
            "name": "Invite",
            "type": "invite_identity",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "grantee_id": "{{public_key}}",
            "granter_id": "{{member_key}}",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_join_context_all_required_fields(self, config_module):
        """Test join_context step requires all fields."""
        step = {
            "name": "Join",
            "type": "join_context",
            "node": "calimero-node-2",
            "context_id": "{{context_id}}",
            "invitee_id": "{{public_key}}",
            "invitation": "{{invitation}}",
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) == 0

    def test_wait_for_sync_requires_nodes_list(self, config_module):
        """Test wait_for_sync requires nodes list."""
        step = {
            "name": "Sync",
            "type": "wait_for_sync",
            "context_id": "{{context_id}}",
            # Missing 'nodes' field
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
        assert any("nodes" in err.lower() for err in errors)

    def test_repeat_requires_count_and_steps(self, config_module):
        """Test repeat step requires count and steps."""
        step = {
            "name": "Repeat",
            "type": "repeat",
            # Missing 'count' and 'steps'
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0

    def test_repeat_count_must_be_positive(self, config_module):
        """Test repeat count must be positive."""
        step = {
            "name": "Repeat",
            "type": "repeat",
            "count": 0,  # Invalid: must be >= 1
            "steps": [],
        }
        errors = config_module.validate_workflow_step(step, 0)
        assert len(errors) > 0
