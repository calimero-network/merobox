"""
Unit tests for BaseStep field validation helper methods.
"""

import pytest

from merobox.commands.bootstrap.steps.base import BaseStep


class TestBaseStepValidation:
    """Tests for BaseStep validation helper methods."""

    def _create_step(self, config: dict) -> BaseStep:
        """Create a BaseStep instance with the given config."""
        return BaseStep(config)

    # =========================================================================
    # String Field Validation Tests
    # =========================================================================

    def test_validate_string_field_valid(self):
        """Test string validation with valid input."""
        step = self._create_step({"name": "test", "node": "calimero-node-1"})
        # Should not raise
        step._validate_string_field("node")

    def test_validate_string_field_missing_required(self):
        """Test string validation fails for missing required field."""
        step = self._create_step({"name": "test"})
        with pytest.raises(ValueError, match="'node' is required"):
            step._validate_string_field("node", required=True)

    def test_validate_string_field_missing_optional(self):
        """Test string validation passes for missing optional field."""
        step = self._create_step({"name": "test"})
        # Should not raise
        step._validate_string_field("node", required=False)

    def test_validate_string_field_empty_string_rejected(self):
        """Test string validation fails for empty string when not allowed."""
        step = self._create_step({"name": "test", "node": ""})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_string_field("node", allow_empty=False)

    def test_validate_string_field_whitespace_only_rejected(self):
        """Test string validation fails for whitespace-only string."""
        step = self._create_step({"name": "test", "node": "   "})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_string_field("node", allow_empty=False)

    def test_validate_string_field_empty_allowed(self):
        """Test string validation passes for empty string when allowed."""
        step = self._create_step({"name": "test", "node": ""})
        # Should not raise
        step._validate_string_field("node", allow_empty=True)

    def test_validate_string_field_wrong_type(self):
        """Test string validation fails for non-string type."""
        step = self._create_step({"name": "test", "node": 123})
        with pytest.raises(ValueError, match="must be a string"):
            step._validate_string_field("node")

    def test_validate_string_field_min_length(self):
        """Test string validation with minimum length constraint."""
        step = self._create_step({"name": "test", "context_id": "ab"})
        with pytest.raises(ValueError, match="at least 3 characters"):
            step._validate_string_field("context_id", min_length=3)

    def test_validate_string_field_max_length(self):
        """Test string validation with maximum length constraint."""
        step = self._create_step({"name": "test", "node": "a" * 100})
        with pytest.raises(ValueError, match="at most 50 characters"):
            step._validate_string_field("node", max_length=50)

    def test_validate_string_field_pattern_match(self):
        """Test string validation with pattern matching."""
        step = self._create_step({"name": "test", "context_id": "abc123"})
        # Should not raise - matches pattern
        step._validate_string_field("context_id", pattern=r"^[a-z0-9]+$")

    def test_validate_string_field_pattern_no_match(self):
        """Test string validation fails when pattern doesn't match."""
        step = self._create_step({"name": "test", "context_id": "ABC-123"})
        with pytest.raises(ValueError, match="must match"):
            step._validate_string_field(
                "context_id",
                pattern=r"^[a-z0-9]+$",
                pattern_description="lowercase alphanumeric",
            )

    # =========================================================================
    # Integer Field Validation Tests
    # =========================================================================

    def test_validate_integer_field_valid(self):
        """Test integer validation with valid input."""
        step = self._create_step({"name": "test", "count": 5})
        # Should not raise
        step._validate_integer_field("count")

    def test_validate_integer_field_missing_required(self):
        """Test integer validation fails for missing required field."""
        step = self._create_step({"name": "test"})
        with pytest.raises(ValueError, match="'count' is required"):
            step._validate_integer_field("count", required=True)

    def test_validate_integer_field_wrong_type(self):
        """Test integer validation fails for non-integer type."""
        step = self._create_step({"name": "test", "count": "five"})
        with pytest.raises(ValueError, match="must be an integer"):
            step._validate_integer_field("count")

    def test_validate_integer_field_boolean_rejected(self):
        """Test integer validation fails for boolean (subclass of int)."""
        step = self._create_step({"name": "test", "count": True})
        with pytest.raises(ValueError, match="must be an integer"):
            step._validate_integer_field("count")

    def test_validate_integer_field_float_whole_accepted(self):
        """Test integer validation accepts whole number floats."""
        step = self._create_step({"name": "test", "count": 5.0})
        # Should not raise
        step._validate_integer_field("count")

    def test_validate_integer_field_float_decimal_rejected(self):
        """Test integer validation rejects decimal floats."""
        step = self._create_step({"name": "test", "count": 5.5})
        with pytest.raises(ValueError, match="must be an integer"):
            step._validate_integer_field("count")

    def test_validate_integer_field_positive_valid(self):
        """Test integer validation with positive constraint."""
        step = self._create_step({"name": "test", "count": 1})
        # Should not raise
        step._validate_integer_field("count", positive=True)

    def test_validate_integer_field_positive_zero_rejected(self):
        """Test integer validation fails for zero when positive required."""
        step = self._create_step({"name": "test", "count": 0})
        with pytest.raises(ValueError, match="must be a positive integer"):
            step._validate_integer_field("count", positive=True)

    def test_validate_integer_field_positive_negative_rejected(self):
        """Test integer validation fails for negative when positive required."""
        step = self._create_step({"name": "test", "count": -1})
        with pytest.raises(ValueError, match="must be a positive integer"):
            step._validate_integer_field("count", positive=True)

    def test_validate_integer_field_non_negative_valid(self):
        """Test integer validation with non-negative constraint."""
        step = self._create_step({"name": "test", "count": 0})
        # Should not raise
        step._validate_integer_field("count", non_negative=True)

    def test_validate_integer_field_non_negative_rejected(self):
        """Test integer validation fails for negative when non-negative required."""
        step = self._create_step({"name": "test", "count": -1})
        with pytest.raises(ValueError, match="must be a non-negative integer"):
            step._validate_integer_field("count", non_negative=True)

    def test_validate_integer_field_min_value(self):
        """Test integer validation with minimum value constraint."""
        step = self._create_step({"name": "test", "count": 2})
        with pytest.raises(ValueError, match="must be at least 5"):
            step._validate_integer_field("count", min_value=5)

    def test_validate_integer_field_max_value(self):
        """Test integer validation with maximum value constraint."""
        step = self._create_step({"name": "test", "count": 10})
        with pytest.raises(ValueError, match="must be at most 5"):
            step._validate_integer_field("count", max_value=5)

    # =========================================================================
    # Port Field Validation Tests
    # =========================================================================

    def test_validate_port_field_valid(self):
        """Test port validation with valid port number."""
        step = self._create_step({"name": "test", "port": 8080})
        # Should not raise
        step._validate_port_field("port")

    def test_validate_port_field_negative_rejected(self):
        """Test port validation fails for negative port."""
        step = self._create_step({"name": "test", "port": -1})
        with pytest.raises(ValueError):
            step._validate_port_field("port")

    def test_validate_port_field_zero_rejected(self):
        """Test port validation fails for port 0."""
        step = self._create_step({"name": "test", "port": 0})
        with pytest.raises(ValueError):
            step._validate_port_field("port")

    def test_validate_port_field_too_high_rejected(self):
        """Test port validation fails for port > 65535."""
        step = self._create_step({"name": "test", "port": 65536})
        with pytest.raises(ValueError):
            step._validate_port_field("port")

    def test_validate_port_field_privileged_allowed(self):
        """Test port validation allows privileged ports by default."""
        step = self._create_step({"name": "test", "port": 80})
        # Should not raise
        step._validate_port_field("port", allow_privileged=True)

    def test_validate_port_field_privileged_rejected(self):
        """Test port validation rejects privileged ports when disabled."""
        step = self._create_step({"name": "test", "port": 80})
        with pytest.raises(ValueError):
            step._validate_port_field("port", allow_privileged=False)

    def test_validate_port_field_boundary_valid(self):
        """Test port validation at valid boundaries."""
        # Test minimum valid port
        step = self._create_step({"name": "test", "port": 1})
        step._validate_port_field("port")

        # Test maximum valid port
        step = self._create_step({"name": "test", "port": 65535})
        step._validate_port_field("port")

    def test_validate_port_field_whole_number_float_accepted(self):
        """Test port validation accepts whole-number floats (e.g., 8080.0)."""
        step = self._create_step({"name": "test", "port": 8080.0})
        # Should not raise - whole-number floats are accepted for consistency
        step._validate_port_field("port")

    def test_validate_port_field_decimal_float_rejected(self):
        """Test port validation rejects decimal floats (e.g., 8080.5)."""
        step = self._create_step({"name": "test", "port": 8080.5})
        with pytest.raises(ValueError, match="integer port number"):
            step._validate_port_field("port")

    # =========================================================================
    # Number Field Validation Tests
    # =========================================================================

    def test_validate_number_field_integer(self):
        """Test number validation with integer."""
        step = self._create_step({"name": "test", "timeout": 30})
        # Should not raise
        step._validate_number_field("timeout")

    def test_validate_number_field_float(self):
        """Test number validation with float."""
        step = self._create_step({"name": "test", "interval": 1.5})
        # Should not raise
        step._validate_number_field("interval")

    def test_validate_number_field_boolean_rejected(self):
        """Test number validation rejects booleans."""
        step = self._create_step({"name": "test", "timeout": True})
        with pytest.raises(ValueError, match="must be a number"):
            step._validate_number_field("timeout")

    def test_validate_number_field_positive(self):
        """Test number validation with positive constraint."""
        step = self._create_step({"name": "test", "interval": 0.5})
        # Should not raise
        step._validate_number_field("interval", positive=True)

    def test_validate_number_field_positive_zero_rejected(self):
        """Test number validation rejects zero when positive required."""
        step = self._create_step({"name": "test", "interval": 0})
        with pytest.raises(ValueError, match="must be a positive number"):
            step._validate_number_field("interval", positive=True)

    # =========================================================================
    # Boolean Field Validation Tests
    # =========================================================================

    def test_validate_boolean_field_true(self):
        """Test boolean validation with True."""
        step = self._create_step({"name": "test", "dev": True})
        # Should not raise
        step._validate_boolean_field("dev")

    def test_validate_boolean_field_false(self):
        """Test boolean validation with False."""
        step = self._create_step({"name": "test", "dev": False})
        # Should not raise
        step._validate_boolean_field("dev")

    def test_validate_boolean_field_wrong_type(self):
        """Test boolean validation fails for non-boolean type."""
        step = self._create_step({"name": "test", "dev": "true"})
        with pytest.raises(ValueError, match="must be a boolean"):
            step._validate_boolean_field("dev")

    def test_validate_boolean_field_integer_rejected(self):
        """Test boolean validation fails for integer 1/0."""
        step = self._create_step({"name": "test", "dev": 1})
        with pytest.raises(ValueError, match="must be a boolean"):
            step._validate_boolean_field("dev")

    # =========================================================================
    # List Field Validation Tests
    # =========================================================================

    def test_validate_list_field_valid(self):
        """Test list validation with valid list."""
        step = self._create_step({"name": "test", "nodes": ["node1", "node2"]})
        # Should not raise
        step._validate_list_field("nodes")

    def test_validate_list_field_empty_rejected(self):
        """Test list validation fails for empty list when not allowed."""
        step = self._create_step({"name": "test", "nodes": []})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_list_field("nodes", allow_empty=False)

    def test_validate_list_field_empty_allowed(self):
        """Test list validation passes for empty list when allowed."""
        step = self._create_step({"name": "test", "nodes": []})
        # Should not raise
        step._validate_list_field("nodes", allow_empty=True)

    def test_validate_list_field_wrong_type(self):
        """Test list validation fails for non-list type."""
        step = self._create_step({"name": "test", "nodes": "node1"})
        with pytest.raises(ValueError, match="must be a list"):
            step._validate_list_field("nodes")

    def test_validate_list_field_min_length(self):
        """Test list validation with minimum length constraint."""
        step = self._create_step({"name": "test", "nodes": ["node1"]})
        with pytest.raises(ValueError, match="at least 2 elements"):
            step._validate_list_field("nodes", min_length=2)

    def test_validate_list_field_element_type(self):
        """Test list validation with element type constraint."""
        step = self._create_step({"name": "test", "nodes": ["node1", 123]})
        with pytest.raises(ValueError, match="nodes\\[1\\].*must be a str"):
            step._validate_list_field("nodes", element_type=str)

    def test_validate_list_field_unique_elements(self):
        """Test list validation with uniqueness constraint."""
        step = self._create_step({"name": "test", "nodes": ["node1", "node1"]})
        with pytest.raises(ValueError, match="unique elements"):
            step._validate_list_field("nodes", unique_elements=True)

    def test_validate_list_field_unique_elements_valid(self):
        """Test list validation passes with unique elements."""
        step = self._create_step({"name": "test", "nodes": ["node1", "node2"]})
        # Should not raise
        step._validate_list_field("nodes", unique_elements=True)

    # =========================================================================
    # Dict Field Validation Tests
    # =========================================================================

    def test_validate_dict_field_valid(self):
        """Test dict validation with valid dict."""
        step = self._create_step({"name": "test", "args": {"key": "value"}})
        # Should not raise
        step._validate_dict_field("args")

    def test_validate_dict_field_wrong_type(self):
        """Test dict validation fails for non-dict type."""
        step = self._create_step({"name": "test", "args": ["key", "value"]})
        with pytest.raises(ValueError, match="must be a dictionary"):
            step._validate_dict_field("args")

    def test_validate_dict_field_empty_rejected(self):
        """Test dict validation fails for empty dict when not allowed."""
        step = self._create_step({"name": "test", "args": {}})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_dict_field("args", allow_empty=False)

    def test_validate_dict_field_required_keys(self):
        """Test dict validation with required keys constraint."""
        step = self._create_step({"name": "test", "args": {"a": 1}})
        with pytest.raises(ValueError, match="missing required keys.*b"):
            step._validate_dict_field("args", required_keys=["a", "b"])

    def test_validate_dict_field_allowed_keys(self):
        """Test dict validation with allowed keys constraint."""
        step = self._create_step({"name": "test", "args": {"a": 1, "c": 3}})
        with pytest.raises(ValueError, match="invalid keys.*c"):
            step._validate_dict_field("args", allowed_keys=["a", "b"])

    # =========================================================================
    # JSON String Field Validation Tests
    # =========================================================================

    def test_validate_json_string_field_valid(self):
        """Test JSON string validation with valid JSON."""
        step = self._create_step({"name": "test", "params": '{"key": "value"}'})
        # Should not raise
        step._validate_json_string_field("params")

    def test_validate_json_string_field_invalid_json(self):
        """Test JSON string validation fails for invalid JSON."""
        step = self._create_step({"name": "test", "params": "{invalid}"})
        with pytest.raises(ValueError, match="must be valid JSON"):
            step._validate_json_string_field("params")

    def test_validate_json_string_field_not_string(self):
        """Test JSON string validation fails for non-string type."""
        step = self._create_step({"name": "test", "params": {"key": "value"}})
        with pytest.raises(ValueError, match="must be a JSON string"):
            step._validate_json_string_field("params")

    # =========================================================================
    # Enum Field Validation Tests
    # =========================================================================

    def test_validate_enum_field_valid(self):
        """Test enum validation with valid value."""
        step = self._create_step({"name": "test", "target": "image"})
        # Should not raise
        step._validate_enum_field("target", ["image", "nodes", "local"])

    def test_validate_enum_field_invalid(self):
        """Test enum validation fails for invalid value."""
        step = self._create_step({"name": "test", "target": "unknown"})
        with pytest.raises(ValueError, match="must be one of"):
            step._validate_enum_field("target", ["image", "nodes", "local"])

    def test_validate_enum_field_case_insensitive(self):
        """Test enum validation with case-insensitive matching."""
        step = self._create_step({"name": "test", "target": "IMAGE"})
        # Should not raise
        step._validate_enum_field("target", ["image", "nodes"], case_sensitive=False)

    def test_validate_enum_field_case_sensitive_rejected(self):
        """Test enum validation fails for case mismatch when case-sensitive."""
        step = self._create_step({"name": "test", "target": "IMAGE"})
        with pytest.raises(ValueError, match="must be one of"):
            step._validate_enum_field("target", ["image", "nodes"], case_sensitive=True)


class TestBaseStepValidationIntegration:
    """Integration tests for BaseStep validation with real step configs."""

    def test_context_id_empty_string_rejected(self):
        """Test that empty context_id is properly rejected."""
        step = BaseStep({"name": "test", "context_id": ""})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_string_field("context_id")

    def test_context_id_whitespace_rejected(self):
        """Test that whitespace-only context_id is properly rejected."""
        step = BaseStep({"name": "test", "context_id": "   \t\n  "})
        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_string_field("context_id")

    def test_port_negative_rejected(self):
        """Test that negative port is properly rejected."""
        step = BaseStep({"name": "test", "port": -8080})
        with pytest.raises(ValueError):
            step._validate_port_field("port")

    def test_count_zero_rejected_when_positive(self):
        """Test that zero count is rejected when positive required."""
        step = BaseStep({"name": "test", "count": 0})
        with pytest.raises(ValueError, match="positive"):
            step._validate_integer_field("count", positive=True)

    def test_seconds_negative_rejected(self):
        """Test that negative seconds is properly rejected."""
        step = BaseStep({"name": "test", "seconds": -5})
        with pytest.raises(ValueError, match="positive"):
            step._validate_integer_field("seconds", positive=True)

    def test_nodes_list_empty_string_elements_detected(self):
        """Test that empty string elements in nodes list are detected."""
        step = BaseStep({"name": "test", "nodes": ["node1", ""]})

        def validate_non_empty_string(elem, idx, field_name):
            if not isinstance(elem, str):
                raise ValueError("Element must be a string")
            if not elem.strip():
                raise ValueError("Element cannot be empty")

        with pytest.raises(ValueError, match="cannot be empty"):
            step._validate_list_field(
                "nodes", element_type=str, element_validator=validate_non_empty_string
            )


class TestBaseStepJsonParsing:
    """Tests for consolidated JSON parsing methods (_parse_json and _get_value)."""

    def _create_step(self, config: dict = None) -> BaseStep:
        """Create a BaseStep instance with the given config."""
        return BaseStep(config or {"name": "test"})

    # =========================================================================
    # _parse_json Tests - Strategy 1: Standard JSON
    # =========================================================================

    def test_parse_json_standard_object(self):
        """Test _parse_json with standard JSON object."""
        step = self._create_step()
        result = step._parse_json('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_parse_json_standard_array(self):
        """Test _parse_json with standard JSON array."""
        step = self._create_step()
        result = step._parse_json('[1, 2, "three"]')
        assert result == [1, 2, "three"]

    def test_parse_json_non_string_passthrough(self):
        """Test _parse_json returns non-strings unchanged."""
        step = self._create_step()
        assert step._parse_json(42) == 42
        assert step._parse_json({"already": "dict"}) == {"already": "dict"}
        assert step._parse_json([1, 2, 3]) == [1, 2, 3]
        assert step._parse_json(None) is None

    def test_parse_json_empty_string(self):
        """Test _parse_json with empty string."""
        step = self._create_step()
        assert step._parse_json("") == ""
        assert step._parse_json("   ") == "   "

    # =========================================================================
    # _parse_json Tests - Strategy 2: Double-encoded JSON
    # =========================================================================

    def test_parse_json_double_encoded(self):
        """Test _parse_json with double-encoded JSON string."""
        step = self._create_step()
        # JSON string containing an escaped JSON object
        double_encoded = '"{\\"key\\": \\"value\\"}"'
        result = step._parse_json(double_encoded)
        assert result == {"key": "value"}

    # =========================================================================
    # _parse_json Tests - Strategy 3: Python literals
    # =========================================================================

    def test_parse_json_python_dict_single_quotes(self):
        """Test _parse_json with Python-style dict using single quotes."""
        step = self._create_step()
        result = step._parse_json("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_parse_json_python_bool(self):
        """Test _parse_json with Python-style booleans."""
        step = self._create_step()
        result = step._parse_json("{'enabled': True, 'disabled': False}")
        assert result == {"enabled": True, "disabled": False}

    def test_parse_json_python_none(self):
        """Test _parse_json with Python None."""
        step = self._create_step()
        result = step._parse_json("{'value': None}")
        assert result == {"value": None}

    # =========================================================================
    # _parse_json Tests - Strategy 4: Trailing commas
    # =========================================================================

    def test_parse_json_trailing_comma_object(self):
        """Test _parse_json with trailing comma in object."""
        step = self._create_step()
        result = step._parse_json('{"key": "value",}')
        assert result == {"key": "value"}

    def test_parse_json_trailing_comma_array(self):
        """Test _parse_json with trailing comma in array."""
        step = self._create_step()
        result = step._parse_json("[1, 2, 3,]")
        assert result == [1, 2, 3]

    # =========================================================================
    # _parse_json Tests - Strategy 5: Substring extraction
    # =========================================================================

    def test_parse_json_extract_from_noisy_input(self):
        """Test _parse_json extracts JSON from text with surrounding content."""
        step = self._create_step()
        result = step._parse_json('Some prefix text {"key": "value"} and suffix')
        assert result == {"key": "value"}

    def test_parse_json_extract_array_from_noisy_input(self):
        """Test _parse_json extracts array from text with surrounding content."""
        step = self._create_step()
        result = step._parse_json("prefix [1, 2, 3] suffix")
        assert result == [1, 2, 3]

    # =========================================================================
    # _parse_json Tests - Fallback behavior
    # =========================================================================

    def test_parse_json_unparseable_returns_original(self):
        """Test _parse_json returns original string when parsing fails."""
        step = self._create_step()
        result = step._parse_json("not json at all")
        assert result == "not json at all"

    # =========================================================================
    # _get_value Tests - Simple key access
    # =========================================================================

    def test_get_value_simple_key(self):
        """Test _get_value with simple key access."""
        step = self._create_step()
        obj = {"name": "test", "count": 42}
        assert step._get_value(obj, "name") == "test"
        assert step._get_value(obj, "count") == 42

    def test_get_value_missing_key(self):
        """Test _get_value returns None for missing key."""
        step = self._create_step()
        obj = {"name": "test"}
        assert step._get_value(obj, "missing") is None

    def test_get_value_simple_key_with_json_parsing(self):
        """Test _get_value parses JSON string values."""
        step = self._create_step()
        obj = {"data": '{"nested": "value"}'}
        result = step._get_value(obj, "data")
        assert result == {"nested": "value"}

    # =========================================================================
    # _get_value Tests - Dotted path access
    # =========================================================================

    def test_get_value_nested_path(self):
        """Test _get_value with nested path."""
        step = self._create_step()
        obj = {"result": {"data": {"value": 42}}}
        assert step._get_value(obj, "result.data.value") == 42

    def test_get_value_path_with_json_string(self):
        """Test _get_value parses JSON strings in path traversal."""
        step = self._create_step()
        obj = {"result": '{"data": {"value": 42}}'}
        assert step._get_value(obj, "result.data.value") == 42

    def test_get_value_array_index(self):
        """Test _get_value with array index."""
        step = self._create_step()
        obj = {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}
        assert step._get_value(obj, "items.0.id") == 1
        assert step._get_value(obj, "items.2.id") == 3

    def test_get_value_array_index_out_of_bounds(self):
        """Test _get_value returns None for out of bounds array index."""
        step = self._create_step()
        obj = {"items": [1, 2]}
        assert step._get_value(obj, "items.5") is None

    def test_get_value_mixed_path(self):
        """Test _get_value with mixed dict keys and array indices."""
        step = self._create_step()
        obj = {"result": {"items": [{"name": "first"}, {"name": "second"}]}}
        assert step._get_value(obj, "result.items.1.name") == "second"

    # =========================================================================
    # _get_value Tests - Edge cases
    # =========================================================================

    def test_get_value_empty_path(self):
        """Test _get_value returns None for empty path."""
        step = self._create_step()
        obj = {"key": "value"}
        assert step._get_value(obj, "") is None
        assert step._get_value(obj, None) is None

    def test_get_value_non_dict_object(self):
        """Test _get_value handles non-dict starting object."""
        step = self._create_step()
        # JSON string as starting object
        json_str = '{"key": "value"}'
        assert step._get_value(json_str, "key") == "value"

    def test_get_value_broken_path(self):
        """Test _get_value returns None when path segment doesn't exist."""
        step = self._create_step()
        obj = {"a": {"b": 1}}
        assert step._get_value(obj, "a.c.d") is None

    # =========================================================================
    # Backward compatibility alias tests
    # =========================================================================

    def test_try_parse_json_alias(self):
        """Test _try_parse_json is an alias for _parse_json."""
        step = self._create_step()
        json_str = '{"key": "value"}'
        assert step._try_parse_json(json_str) == step._parse_json(json_str)
        assert step._try_parse_json(json_str) == {"key": "value"}

    def test_extract_path_alias(self):
        """Test _extract_path is an alias for _get_value."""
        step = self._create_step()
        obj = {"result": {"data": 42}}
        assert step._extract_path(obj, "result.data") == step._get_value(
            obj, "result.data"
        )
        assert step._extract_path(obj, "result.data") == 42
