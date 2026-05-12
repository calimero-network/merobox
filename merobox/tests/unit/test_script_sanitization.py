"""
Unit tests for environment variable sanitization in script.py.
"""

from merobox.commands.bootstrap.steps.script import (
    MAX_ENV_VAR_NAME_LENGTH,
    MAX_ENV_VAR_VALUE_LENGTH,
    SENSITIVE_ENV_VARS,
    _sanitize_env_var_name,
    _sanitize_env_var_value,
)


class TestSanitizeEnvVarName:
    """Tests for _sanitize_env_var_name function."""

    def test_simple_name(self):
        """Test simple valid name conversion."""
        name, reason = _sanitize_env_var_name("myvar")
        assert name == "MYVAR"
        assert reason is None

    def test_name_with_dashes(self):
        """Test name with dashes is converted to underscores."""
        name, reason = _sanitize_env_var_name("my-var")
        assert name == "MY_VAR"
        assert reason is None

    def test_name_with_dots(self):
        """Test name with dots is converted to underscores."""
        name, reason = _sanitize_env_var_name("my.var")
        assert name == "MY_VAR"
        assert reason is None

    def test_name_starting_with_digit(self):
        """Test name starting with digit gets underscore prefix."""
        name, reason = _sanitize_env_var_name("1variable")
        assert name == "_1VARIABLE"
        assert reason is None

    def test_name_with_special_chars(self):
        """Test special characters are removed."""
        name, reason = _sanitize_env_var_name("my$var@name")
        assert name == "MYVARNAME"
        assert reason is None

    def test_name_with_spaces(self):
        """Test spaces are removed."""
        name, reason = _sanitize_env_var_name("my var")
        assert name == "MYVAR"
        assert reason is None

    def test_empty_name(self):
        """Test empty string returns None with reason."""
        name, reason = _sanitize_env_var_name("")
        assert name is None
        assert reason is not None
        assert "empty" in reason

    def test_none_name(self):
        """Test None returns None with reason."""
        name, reason = _sanitize_env_var_name(None)
        assert name is None
        assert reason is not None

    def test_name_only_special_chars(self):
        """Test name with only special chars returns None with reason."""
        name, reason = _sanitize_env_var_name("$@#!")
        assert name is None
        assert reason is not None
        assert "no valid characters" in reason

    def test_name_only_digit(self):
        """Test name with only digits gets underscore prefix."""
        name, reason = _sanitize_env_var_name("123")
        assert name == "_123"
        assert reason is None

    def test_long_name_truncated(self):
        """Test very long name is truncated."""
        long_name = "A" * (MAX_ENV_VAR_NAME_LENGTH + 100)
        name, reason = _sanitize_env_var_name(long_name)
        assert name is not None
        assert len(name) == MAX_ENV_VAR_NAME_LENGTH
        assert reason is None

    def test_unicode_name(self):
        """Test unicode characters are removed."""
        name, reason = _sanitize_env_var_name("my_vàr_ñame")
        assert name == "MY_VR_AME"
        assert reason is None

    def test_injection_attempt_newline(self):
        """Test newline injection is prevented."""
        name, reason = _sanitize_env_var_name("VAR\nINJECTION")
        assert name == "VARINJECTION"
        assert "\n" not in name
        assert reason is None

    def test_injection_attempt_equals(self):
        """Test equals sign injection is prevented."""
        name, reason = _sanitize_env_var_name("VAR=INJECTION")
        assert name == "VARINJECTION"
        assert "=" not in name
        assert reason is None

    def test_mixed_case_preserved_after_upper(self):
        """Test mixed case is converted to uppercase."""
        name, reason = _sanitize_env_var_name("MyVarName")
        assert name == "MYVARNAME"
        assert reason is None

    def test_already_valid_name(self):
        """Test already valid name passes through."""
        name, reason = _sanitize_env_var_name("MY_VALID_VAR_123")
        assert name == "MY_VALID_VAR_123"
        assert reason is None

    def test_sensitive_var_path_blocked(self):
        """Test PATH is blocked as sensitive variable."""
        name, reason = _sanitize_env_var_name("path")
        assert name is None
        assert reason is not None
        assert "protected" in reason

    def test_sensitive_var_ld_preload_blocked(self):
        """Test LD_PRELOAD is blocked as sensitive variable."""
        name, reason = _sanitize_env_var_name("ld_preload")
        assert name is None
        assert reason is not None
        assert "protected" in reason

    def test_sensitive_var_pythonpath_blocked(self):
        """Test PYTHONPATH is blocked as sensitive variable."""
        name, reason = _sanitize_env_var_name("pythonpath")
        assert name is None
        assert reason is not None
        assert "protected" in reason

    def test_sensitive_var_home_blocked(self):
        """Test HOME is blocked as sensitive variable."""
        name, reason = _sanitize_env_var_name("home")
        assert name is None
        assert reason is not None
        assert "protected" in reason

    def test_sensitive_var_ld_library_path_blocked(self):
        """Test LD_LIBRARY_PATH is blocked as sensitive variable."""
        name, reason = _sanitize_env_var_name("ld-library-path")
        assert name is None
        assert reason is not None
        assert "protected" in reason

    def test_all_sensitive_vars_blocked(self):
        """Test all defined sensitive variables are blocked."""
        for sensitive_var in SENSITIVE_ENV_VARS:
            name, reason = _sanitize_env_var_name(sensitive_var.lower())
            assert name is None, f"{sensitive_var} should be blocked"
            assert (
                "protected" in reason
            ), f"{sensitive_var} should have protected reason"


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

    def test_tabs_allowed(self):
        """Test tabs are allowed in values."""
        value = "col1\tcol2\tcol3"
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

    def test_control_chars_removed(self):
        """Test control characters (except tab and newline) are removed."""
        # Bell, backspace, vertical tab, form feed, carriage return
        value = "hello\x07\x08\x0b\x0c\rworld"
        result = _sanitize_env_var_value(value)
        assert result == "helloworld"
        assert "\x07" not in result
        assert "\x08" not in result
        assert "\x0b" not in result
        assert "\x0c" not in result
        assert "\r" not in result

    def test_carriage_return_removed(self):
        """Test carriage return is removed to prevent log injection."""
        # Carriage return could be used for log injection
        value = "legitimate\rmalicious log entry"
        result = _sanitize_env_var_value(value)
        assert "\r" not in result
        assert result == "legitimatemalicious log entry"

    def test_escape_sequences_removed(self):
        """Test escape character is removed."""
        value = "hello\x1bworld"  # ESC character
        result = _sanitize_env_var_value(value)
        assert "\x1b" not in result
        assert result == "helloworld"


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
            name, reason = _sanitize_env_var_name(input_name)
            assert name == expected, f"Expected {expected} for {input_name}, got {name}"
            assert reason is None

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

    def test_rejection_reason_is_informative(self):
        """Test that rejection reasons are informative for debugging."""
        # Empty input
        _, reason = _sanitize_env_var_name("")
        assert reason is not None
        assert len(reason) > 0

        # Only special chars
        _, reason = _sanitize_env_var_name("$$$")
        assert reason is not None
        assert "valid" in reason.lower() or "character" in reason.lower()

        # Sensitive variable
        _, reason = _sanitize_env_var_name("PATH")
        assert reason is not None
        assert "protected" in reason.lower()
