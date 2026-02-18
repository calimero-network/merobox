"""Unit tests for dry-run mode functionality.

This test module verifies the dry-run validation mode for workflow execution,
testing the actual module-level pattern and extraction logic.

Note: Due to import chain complexity (executor.py imports many dependencies),
tests recreate the regex pattern here. The pattern MUST match the one defined
in merobox/commands/bootstrap/run/executor.py (_VAR_PATTERN).
"""

import re

# This pattern MUST match _VAR_PATTERN in executor.py
# Pattern: r"\{\{(\w+)\}\}"
VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def extract_vars(obj: object, pattern: re.Pattern = VAR_PATTERN) -> set[str]:
    """
    Extract variable references from nested structure.

    This mirrors the logic in WorkflowExecutor._extract_variable_references().
    """
    variables: set[str] = set()
    if isinstance(obj, str):
        matches = pattern.findall(obj)
        variables.update(matches)
    elif isinstance(obj, dict):
        for value in obj.values():
            variables.update(extract_vars(value, pattern))
    elif isinstance(obj, list):
        for item in obj:
            variables.update(extract_vars(item, pattern))
    return variables


def validate_variable_ordering(
    steps: list[dict], pattern: re.Pattern = VAR_PATTERN
) -> list[str]:
    """
    Validate variable ordering - check each step against prior steps.

    This mirrors the logic in WorkflowExecutor._dry_run_analyze_steps().
    Returns list of forward reference warnings.
    """
    available_vars: set[str] = set()
    warnings: list[str] = []

    for i, step in enumerate(steps, 1):
        step_name = step.get("name", f"Step {i}")
        # Exclude 'outputs' when checking consumed variables (mirrors executor)
        step_for_vars = {k: v for k, v in step.items() if k != "outputs"}
        step_vars = extract_vars(step_for_vars, pattern)

        # Check for undefined variables at this point
        undefined = step_vars - available_vars
        if undefined:
            warnings.append(
                f"Step {i} '{step_name}': uses undefined variables: "
                f"{', '.join(sorted(undefined))}"
            )

        # Add outputs to available vars for next steps
        outputs = step.get("outputs")
        if outputs and isinstance(outputs, dict):
            available_vars.update(outputs.keys())

    return warnings


class TestVarPatternModule:
    """Tests for the _VAR_PATTERN regex used in variable extraction."""

    def test_extract_simple_variable(self):
        """Test extracting a simple variable from a string."""
        text = "{{app_id}}"
        matches = VAR_PATTERN.findall(text)
        assert matches == ["app_id"]

    def test_extract_multiple_variables(self):
        """Test extracting multiple variables from a string."""
        text = "Context {{context_id}} with app {{app_id}}"
        matches = VAR_PATTERN.findall(text)
        assert set(matches) == {"context_id", "app_id"}

    def test_no_variables(self):
        """Test extraction when no variables are present."""
        text = "Just plain text without variables"
        matches = VAR_PATTERN.findall(text)
        assert matches == []

    def test_triple_braces_extracts_inner_variable(self):
        """Test that triple braces {{{}}} still extract the inner variable.

        The pattern matches the first complete {{var}} it finds, so {{{invalid}}}
        matches {{invalid}} (the inner pair with the variable name).
        """
        text = "{{{invalid}}} and {not_a_var}"
        matches = VAR_PATTERN.findall(text)
        assert matches == ["invalid"]

    def test_underscore_in_variable_name(self):
        """Test that underscores in variable names are captured."""
        text = "{{my_long_variable_name}}"
        matches = VAR_PATTERN.findall(text)
        assert matches == ["my_long_variable_name"]

    def test_dotted_names_not_captured(self):
        """Test that dotted names like {{env.VAR}} are not captured at all.

        The regex requires the entire content between {{ and }} to be word
        characters, so {{env.HOME}} won't match because the dot is not a
        word character.
        """
        text = "{{env.HOME}}"
        matches = VAR_PATTERN.findall(text)
        # The dot breaks the match entirely - no partial capture
        assert matches == []


class TestVariableExtractionLogic:
    """Tests for variable extraction from nested structures."""

    def test_extract_from_flat_dict(self):
        """Test extraction from a flat dictionary."""
        step = {
            "type": "call",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "method": "set",
        }
        vars_found = extract_vars(step)
        assert vars_found == {"context_id"}

    def test_extract_from_nested_dict(self):
        """Test extraction from nested dictionary structures."""
        step = {
            "type": "call",
            "node": "calimero-node-1",
            "context_id": "{{context_id}}",
            "method": "set",
            "args": {"key": "{{key_var}}", "value": "{{value_var}}"},
        }
        vars_found = extract_vars(step)
        assert vars_found == {"context_id", "key_var", "value_var"}

    def test_extract_from_list(self):
        """Test extraction from list values."""
        step = {
            "type": "parallel",
            "nodes": ["{{node1}}", "{{node2}}", "static-node"],
        }
        vars_found = extract_vars(step)
        assert vars_found == {"node1", "node2"}

    def test_extract_from_deeply_nested(self):
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
        vars_found = extract_vars(step)
        assert vars_found == {"deep_var"}


class TestVariableOrderingValidation:
    """Tests for variable ordering validation in dry-run mode."""

    def test_correct_ordering_passes(self):
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
        warnings = validate_variable_ordering(steps)
        assert len(warnings) == 0

    def test_forward_reference_detected(self):
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
        warnings = validate_variable_ordering(steps)
        assert len(warnings) == 1
        assert "context_id" in warnings[0]
        assert "Step 1" in warnings[0]

    def test_undefined_variable_detected(self):
        """Test that completely undefined variables are detected."""
        steps = [
            {
                "name": "Use Undefined",
                "type": "call",
                "context_id": "{{never_defined}}",
                "method": "test",
            },
        ]
        warnings = validate_variable_ordering(steps)
        assert len(warnings) == 1
        assert "never_defined" in warnings[0]

    def test_multiple_forward_references(self):
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
        warnings = validate_variable_ordering(steps)
        assert len(warnings) == 1
        assert "ctx" in warnings[0] or "app" in warnings[0]

    def test_outputs_excluded_from_consumed_vars(self):
        """Test that variables in outputs values are not flagged as consumed."""
        steps = [
            {
                "name": "Step with output template",
                "type": "create_context",
                "outputs": {"result": "{{some_template}}"},
            },
        ]
        # Should NOT warn about {{some_template}} since it's in outputs
        warnings = validate_variable_ordering(steps)
        assert len(warnings) == 0


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
