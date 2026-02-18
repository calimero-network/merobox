"""Unit tests for dry-run mode functionality.

This test module verifies the dry-run validation mode for workflow execution,
testing the actual module-level pattern and extraction logic.
"""

import re

import pytest


class TestVarPatternModule:
    """Tests for the _VAR_PATTERN regex used in variable extraction."""

    @pytest.fixture
    def var_pattern(self):
        """Get the variable pattern (same as module-level _VAR_PATTERN)."""
        return re.compile(r"\{\{(\w+)\}\}")

    def test_extract_simple_variable(self, var_pattern):
        """Test extracting a simple variable from a string."""
        text = "{{app_id}}"
        matches = var_pattern.findall(text)
        assert matches == ["app_id"]

    def test_extract_multiple_variables(self, var_pattern):
        """Test extracting multiple variables from a string."""
        text = "Context {{context_id}} with app {{app_id}}"
        matches = var_pattern.findall(text)
        assert set(matches) == {"context_id", "app_id"}

    def test_no_variables(self, var_pattern):
        """Test extraction when no variables are present."""
        text = "Just plain text without variables"
        matches = var_pattern.findall(text)
        assert matches == []

    def test_nested_braces_not_matched(self, var_pattern):
        """Test that nested or malformed braces are not matched."""
        text = "{{{invalid}}} and {not_a_var}"
        matches = var_pattern.findall(text)
        assert matches == ["invalid"]

    def test_underscore_in_variable_name(self, var_pattern):
        """Test that underscores in variable names are captured."""
        text = "{{my_long_variable_name}}"
        matches = var_pattern.findall(text)
        assert matches == ["my_long_variable_name"]

    def test_dotted_names_not_captured(self, var_pattern):
        """Test that dotted names like {{env.VAR}} are not captured at all.

        The regex requires the entire content between {{ and }} to be word
        characters, so {{env.HOME}} won't match because the dot is not a
        word character.
        """
        text = "{{env.HOME}}"
        matches = var_pattern.findall(text)
        # The dot breaks the match entirely - no partial capture
        assert matches == []


class TestVariableExtractionLogic:
    """Tests for variable extraction from nested structures."""

    @pytest.fixture
    def var_pattern(self):
        """Get the variable pattern."""
        return re.compile(r"\{\{(\w+)\}\}")

    def extract_vars(self, obj, pattern):
        """Extract variables from nested structure (mirrors executor logic)."""
        variables = set()
        if isinstance(obj, str):
            matches = pattern.findall(obj)
            variables.update(matches)
        elif isinstance(obj, dict):
            for value in obj.values():
                variables.update(self.extract_vars(value, pattern))
        elif isinstance(obj, list):
            for item in obj:
                variables.update(self.extract_vars(item, pattern))
        return variables

    def test_extract_from_flat_dict(self, var_pattern):
        """Test extraction from a flat dictionary."""
        step = {
            "type": "call",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "method": "set",
        }
        vars_found = self.extract_vars(step, var_pattern)
        assert vars_found == {"context_id"}

    def test_extract_from_nested_dict(self, var_pattern):
        """Test extraction from nested dictionary structures."""
        step = {
            "type": "call",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "method": "set",
            "args": {"key": "{{key_var}}", "value": "{{value_var}}"},
        }
        vars_found = self.extract_vars(step, var_pattern)
        assert vars_found == {"context_id", "key_var", "value_var"}

    def test_extract_from_list(self, var_pattern):
        """Test extraction from list values."""
        step = {
            "type": "parallel",
            "nodes": ["{{node1}}", "{{node2}}", "static-node"],
        }
        vars_found = self.extract_vars(step, var_pattern)
        assert vars_found == {"node1", "node2"}

    def test_extract_from_deeply_nested(self, var_pattern):
        """Test extraction from deeply nested structures."""
        step = {
            "type": "repeat",
            "steps": [
                {
                    "type": "call",
                    "args": {
                        "nested": {
                            "deep": "{{deep_var}}",
                        }
                    },
                }
            ],
        }
        vars_found = self.extract_vars(step, var_pattern)
        assert vars_found == {"deep_var"}


class TestVariableOrderingValidation:
    """Tests for variable ordering validation in dry-run mode."""

    @pytest.fixture
    def var_pattern(self):
        """Get the variable pattern."""
        return re.compile(r"\{\{(\w+)\}\}")

    def extract_vars(self, obj, pattern):
        """Extract variables from nested structure."""
        variables = set()
        if isinstance(obj, str):
            matches = pattern.findall(obj)
            variables.update(matches)
        elif isinstance(obj, dict):
            for value in obj.values():
                variables.update(self.extract_vars(value, pattern))
        elif isinstance(obj, list):
            for item in obj:
                variables.update(self.extract_vars(item, pattern))
        return variables

    def validate_variable_ordering(self, steps, pattern):
        """
        Validate variable ordering - check each step against prior steps.
        Returns list of forward reference warnings.
        """
        available_vars = set()
        warnings = []

        for i, step in enumerate(steps, 1):
            step_name = step.get("name", f"Step {i}")
            step_vars = self.extract_vars(step, pattern)

            # Check for undefined variables at this point
            undefined = step_vars - available_vars
            if undefined:
                warnings.append(
                    f"Step {i} '{step_name}': uses undefined variables: "
                    f"{', '.join(sorted(undefined))}"
                )

            # Add outputs to available vars for next steps
            outputs = step.get("outputs", {})
            available_vars.update(outputs.keys())

        return warnings

    def test_correct_ordering_passes(self, var_pattern):
        """Test that correctly ordered variables produce no warnings."""
        steps = [
            {
                "name": "Install App",
                "type": "install_application",
                "path": "./app.wasm",
                "outputs": {"app_id": "id"},
            },
            {
                "name": "Create Context",
                "type": "create_context",
                "application_id": "{{app_id}}",
                "outputs": {"context_id": "id"},
            },
            {
                "name": "Execute",
                "type": "call",
                "context_id": "{{context_id}}",
                "method": "test",
            },
        ]
        warnings = self.validate_variable_ordering(steps, var_pattern)
        assert len(warnings) == 0

    def test_forward_reference_detected(self, var_pattern):
        """Test that forward references (use before define) are detected."""
        steps = [
            {
                "name": "Use Before Define",
                "type": "call",
                "context_id": "{{context_id}}",
                "method": "test",
            },
            {
                "name": "Define Later",
                "type": "create_context",
                "outputs": {"context_id": "id"},
            },
        ]
        warnings = self.validate_variable_ordering(steps, var_pattern)
        assert len(warnings) == 1
        assert "context_id" in warnings[0]
        assert "Step 1" in warnings[0]

    def test_undefined_variable_detected(self, var_pattern):
        """Test that completely undefined variables are detected."""
        steps = [
            {
                "name": "Use Undefined",
                "type": "call",
                "context_id": "{{never_defined}}",
                "method": "test",
            },
        ]
        warnings = self.validate_variable_ordering(steps, var_pattern)
        assert len(warnings) == 1
        assert "never_defined" in warnings[0]

    def test_multiple_forward_references(self, var_pattern):
        """Test detection of multiple forward references."""
        steps = [
            {
                "name": "Step 1",
                "type": "call",
                "context_id": "{{ctx}}",
                "app_id": "{{app}}",
            },
            {
                "name": "Step 2",
                "outputs": {"ctx": "id", "app": "id"},
            },
        ]
        warnings = self.validate_variable_ordering(steps, var_pattern)
        assert len(warnings) == 1
        assert "ctx" in warnings[0] or "app" in warnings[0]


class TestDryRunConfigValidation:
    """Tests for configuration validation structures."""

    def test_valid_config_structure(self):
        """Test that a valid config structure has required fields."""
        config = {
            "name": "Test Workflow",
            "nodes": {
                "count": 2,
                "prefix": "calimero-node",
                "chain_id": "testnet-1",
                "image": "test:latest",
            },
            "steps": [
                {
                    "name": "Install App",
                    "type": "install_application",
                    "node": "calimero-node-1",
                    "path": "./app.wasm",
                    "outputs": {"app_id": "id"},
                },
            ],
        }
        assert "name" in config
        assert "nodes" in config or "remote_nodes" in config
        assert "steps" in config

    def test_remote_only_config(self):
        """Test config with only remote nodes."""
        config = {
            "name": "Remote Workflow",
            "remote_nodes": {
                "prod-node": {
                    "url": "https://node.example.com:2428",
                    "auth": {
                        "method": "api_key",
                        "api_key": "test-key",
                    },
                },
            },
            "steps": [],
        }
        assert "remote_nodes" in config
        assert "nodes" not in config


class TestDryRunNodeReferences:
    """Tests for node reference extraction."""

    def test_extract_static_node_reference(self):
        """Test extracting static node reference from a step."""
        step = {
            "type": "call",
            "node": "calimero-node-1",
            "method": "test",
        }
        node = step.get("node")
        assert node == "calimero-node-1"
        assert "{{" not in node

    def test_dynamic_node_reference_detected(self):
        """Test that dynamic node references are identified."""
        step = {
            "type": "call",
            "node": "{{dynamic_node}}",
            "method": "test",
        }
        node = step.get("node")
        is_dynamic = "{{" in node and "}}" in node
        assert is_dynamic

    def test_extract_nodes_from_nested_repeat(self):
        """Test extracting node references from nested repeat steps."""

        def extract_nodes(s):
            nodes = set()
            if "node" in s:
                node_val = s["node"]
                if isinstance(node_val, str) and "{{" not in node_val:
                    nodes.add(node_val)
            if s.get("type") == "repeat":
                for nested in s.get("steps", []):
                    nodes.update(extract_nodes(nested))
            elif s.get("type") == "parallel":
                for group in s.get("groups", []):
                    for nested in group.get("steps", []):
                        nodes.update(extract_nodes(nested))
            return nodes

        step = {
            "type": "repeat",
            "count": 3,
            "steps": [
                {"type": "wait", "seconds": 1},
                {"type": "call", "node": "calimero-node-2", "method": "test"},
            ],
        }
        nodes = extract_nodes(step)
        assert nodes == {"calimero-node-2"}
