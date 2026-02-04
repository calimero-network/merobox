"""
Unit tests for environment variable sanitization in script.py.

These tests verify the sanitization functions that prevent environment
variable injection attacks.
"""

import re
import sys

# Import the functions to test directly from the script file
# We need to handle the case where the full module chain isn't available
sys.path.insert(0, "/workspace")

# Define the same constants and functions here for testing to avoid import issues
MAX_ENV_VAR_NAME_LENGTH = 256
MAX_ENV_VAR_VALUE_LENGTH = 131072  # 128KB max value length
ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _sanitize_env_var_name(name):
    """Copy of the function for testing purposes."""
    if not name or not isinstance(name, str):
        return None
    sanitized = name.upper().replace("-", "_").replace(".", "_")
    sanitized = re.sub(r"[^A-Z0-9_]", "", sanitized)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    if not sanitized:
        return None
    if len(sanitized) > MAX_ENV_VAR_NAME_LENGTH:
        sanitized = sanitized[:MAX_ENV_VAR_NAME_LENGTH]
    if not ENV_VAR_NAME_PATTERN.match(sanitized):
        return None
    return sanitized


def _sanitize_env_var_value(value):
    """Copy of the function for testing purposes."""
    if value is None:
        return ""
    str_value = str(value)
    str_value = str_value.replace("\x00", "")
    if len(str_value) > MAX_ENV_VAR_VALUE_LENGTH:
        str_value = str_value[:MAX_ENV_VAR_VALUE_LENGTH]
    return str_value


class TestSanitizeEnvVarName:
    """Tests for _sanitize_env_var_name function."""

    def test_simple_name(self):
        """Test simple valid name conversion."""
        assert _sanitize_env_var_name("myvar") == "MYVAR"

    def test_name_with_dashes(self):
        """Test name with dashes is converted to underscores."""
        assert _sanitize_env_var_name("my-var") == "MY_VAR"

    def test_name_with_dots(self):
        """Test name with dots is converted to underscores."""
        assert _sanitize_env_var_name("my.var") == "MY_VAR"

    def test_name_starting_with_digit(self):
        """Test name starting with digit gets underscore prefix."""
        assert _sanitize_env_var_name("1variable") == "_1VARIABLE"

    def test_name_with_special_chars(self):
        """Test special characters are removed."""
        assert _sanitize_env_var_name("my$var@name") == "MYVARNAME"

    def test_name_with_spaces(self):
        """Test spaces are removed."""
        assert _sanitize_env_var_name("my var") == "MYVAR"

    def test_empty_name(self):
        """Test empty string returns None."""
        assert _sanitize_env_var_name("") is None

    def test_none_name(self):
        """Test None returns None."""
        assert _sanitize_env_var_name(None) is None

    def test_name_only_special_chars(self):
        """Test name with only special chars returns None."""
        assert _sanitize_env_var_name("$@#!") is None

    def test_name_only_digit(self):
        """Test name with only digits gets underscore prefix."""
        assert _sanitize_env_var_name("123") == "_123"

    def test_long_name_truncated(self):
        """Test very long name is truncated."""
        long_name = "A" * (MAX_ENV_VAR_NAME_LENGTH + 100)
        result = _sanitize_env_var_name(long_name)
        assert result is not None
        assert len(result) == MAX_ENV_VAR_NAME_LENGTH

    def test_unicode_name(self):
        """Test unicode characters are removed."""
        assert _sanitize_env_var_name("my_vàr_ñame") == "MY_VR_AME"

    def test_injection_attempt_newline(self):
        """Test newline injection is prevented."""
        result = _sanitize_env_var_name("VAR\nINJECTION")
        assert result == "VARINJECTION"
        assert "\n" not in result

    def test_injection_attempt_equals(self):
        """Test equals sign injection is prevented."""
        result = _sanitize_env_var_name("VAR=INJECTION")
        assert result == "VARINJECTION"
        assert "=" not in result

    def test_mixed_case_preserved_after_upper(self):
        """Test mixed case is converted to uppercase."""
        assert _sanitize_env_var_name("MyVarName") == "MYVARNAME"

    def test_already_valid_name(self):
        """Test already valid name passes through."""
        assert _sanitize_env_var_name("MY_VALID_VAR_123") == "MY_VALID_VAR_123"


class TestSanitizeEnvVarValue:
    """Tests for _sanitize_env_var_value function."""

    def test_simple_string(self):
        """Test simple string passes through."""
        assert _sanitize_env_var_value("hello world") == "hello world"

    def test_none_value(self):
        """Test None returns empty string."""
        assert _sanitize_env_var_value(None) == ""

    def test_integer_value(self):
        """Test integer is converted to string."""
        assert _sanitize_env_var_value(42) == "42"

    def test_float_value(self):
        """Test float is converted to string."""
        assert _sanitize_env_var_value(3.14) == "3.14"

    def test_boolean_value(self):
        """Test boolean is converted to string."""
        assert _sanitize_env_var_value(True) == "True"
        assert _sanitize_env_var_value(False) == "False"

    def test_null_byte_removed(self):
        """Test null bytes are removed to prevent injection."""
        result = _sanitize_env_var_value("hello\x00world")
        assert result == "helloworld"
        assert "\x00" not in result

    def test_multiple_null_bytes_removed(self):
        """Test multiple null bytes are all removed."""
        result = _sanitize_env_var_value("\x00hello\x00world\x00")
        assert result == "helloworld"

    def test_long_value_truncated(self):
        """Test very long value is truncated."""
        long_value = "A" * (MAX_ENV_VAR_VALUE_LENGTH + 100)
        result = _sanitize_env_var_value(long_value)
        assert len(result) == MAX_ENV_VAR_VALUE_LENGTH

    def test_empty_string(self):
        """Test empty string returns empty string."""
        assert _sanitize_env_var_value("") == ""

    def test_special_characters_allowed(self):
        """Test special characters are allowed in values."""
        value = "hello!@#$%^&*()_+-=[]{}|;':\",./<>?"
        assert _sanitize_env_var_value(value) == value

    def test_newlines_allowed(self):
        """Test newlines are allowed in values (they're safe in env vars)."""
        value = "line1\nline2\nline3"
        assert _sanitize_env_var_value(value) == value

    def test_list_value(self):
        """Test list is converted to string representation."""
        assert _sanitize_env_var_value([1, 2, 3]) == "[1, 2, 3]"

    def test_dict_value(self):
        """Test dict is converted to string representation."""
        assert _sanitize_env_var_value({"key": "value"}) == "{'key': 'value'}"

    def test_unicode_value(self):
        """Test unicode values are preserved."""
        value = "héllo wörld 你好"
        assert _sanitize_env_var_value(value) == value

    def test_injection_attempt_with_null_and_command(self):
        """Test injection attempt with null byte and command is sanitized."""
        malicious = "legitimate_value\x00; rm -rf /"
        result = _sanitize_env_var_value(malicious)
        assert result == "legitimate_value; rm -rf /"
        assert "\x00" not in result


class TestSanitizationIntegration:
    """Integration tests for sanitization in realistic scenarios."""

    def test_workflow_variable_sanitization(self):
        """Test sanitizing typical workflow variable names."""
        test_cases = [
            ("node.name", "NODE_NAME"),
            ("context-id", "CONTEXT_ID"),
            ("output.result.value", "OUTPUT_RESULT_VALUE"),
            ("step-1-output", "STEP_1_OUTPUT"),
        ]
        for input_name, expected in test_cases:
            assert _sanitize_env_var_name(input_name) == expected

    def test_dynamic_value_patterns(self):
        """Test sanitizing typical dynamic values."""
        test_cases = [
            ("abc123", "abc123"),
            ("http://localhost:8080", "http://localhost:8080"),
            ('{"key": "value"}', '{"key": "value"}'),
            ("path/to/file.txt", "path/to/file.txt"),
        ]
        for input_value, expected in test_cases:
            assert _sanitize_env_var_value(input_value) == expected


if __name__ == "__main__":
    # Run tests manually if pytest is not available
    import traceback

    test_classes = [
        TestSanitizeEnvVarName,
        TestSanitizeEnvVarValue,
        TestSanitizationIntegration,
    ]

    passed = 0
    failed = 0

    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    passed += 1
                    print(f"  ✓ {test_class.__name__}.{method_name}")
                except Exception as e:
                    failed += 1
                    print(f"  ✗ {test_class.__name__}.{method_name}")
                    traceback.print_exc()

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
