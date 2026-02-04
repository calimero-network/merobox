"""
Unit tests for ScriptStep path traversal validation.
"""

import os

from merobox.commands.bootstrap.steps.script import ScriptStep


class TestScriptStepPathValidation:
    """Test cases for path traversal validation in ScriptStep."""

    def setup_method(self):
        """Set up test fixtures."""
        # Basic config for ScriptStep
        self.base_config = {
            "type": "script",
            "name": "Test Script Step",
            "script": "test_script.sh",
        }

    def create_step(self, script_path: str) -> ScriptStep:
        """Helper to create a ScriptStep with a given script path."""
        config = self.base_config.copy()
        config["script"] = script_path
        return ScriptStep(config)

    def test_valid_relative_path(self, tmp_path):
        """Test that valid relative paths within cwd are accepted."""
        # Change to temp directory
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Create a test script file
            script_file = tmp_path / "test_script.sh"
            script_file.write_text("#!/bin/sh\necho hello")

            step = self.create_step("test_script.sh")
            is_valid, error = step._validate_script_path("test_script.sh")

            assert is_valid is True
            assert error == ""
        finally:
            os.chdir(original_cwd)

    def test_valid_nested_relative_path(self, tmp_path):
        """Test that valid nested relative paths are accepted."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Create nested directory structure
            scripts_dir = tmp_path / "scripts"
            scripts_dir.mkdir()
            script_file = scripts_dir / "test_script.sh"
            script_file.write_text("#!/bin/sh\necho hello")

            step = self.create_step("scripts/test_script.sh")
            is_valid, error = step._validate_script_path("scripts/test_script.sh")

            assert is_valid is True
            assert error == ""
        finally:
            os.chdir(original_cwd)

    def test_path_traversal_with_double_dots(self, tmp_path):
        """Test that paths containing '..' are rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("../etc/passwd")
            is_valid, error = step._validate_script_path("../etc/passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
            assert ".." in error
        finally:
            os.chdir(original_cwd)

    def test_path_traversal_with_nested_double_dots(self, tmp_path):
        """Test that paths with nested '..' are rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("scripts/../../../etc/passwd")
            is_valid, error = step._validate_script_path("scripts/../../../etc/passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
        finally:
            os.chdir(original_cwd)

    def test_path_traversal_middle_double_dots(self, tmp_path):
        """Test that paths with '..' in the middle are rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("valid/../../../etc/passwd")
            is_valid, error = step._validate_script_path("valid/../../../etc/passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
        finally:
            os.chdir(original_cwd)

    def test_absolute_path_outside_cwd(self, tmp_path):
        """Test that absolute paths outside cwd are rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("/etc/passwd")
            is_valid, error = step._validate_script_path("/etc/passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
            assert "outside" in error.lower()
        finally:
            os.chdir(original_cwd)

    def test_absolute_path_inside_cwd(self, tmp_path):
        """Test that absolute paths inside cwd are accepted."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Create a test script file
            script_file = tmp_path / "test_script.sh"
            script_file.write_text("#!/bin/sh\necho hello")
            absolute_path = str(script_file)

            step = self.create_step(absolute_path)
            is_valid, error = step._validate_script_path(absolute_path)

            assert is_valid is True
            assert error == ""
        finally:
            os.chdir(original_cwd)

    def test_empty_path(self):
        """Test that empty paths are rejected."""
        step = self.create_step("")
        is_valid, error = step._validate_script_path("")

        assert is_valid is False
        assert "empty" in error.lower()

    def test_none_path(self):
        """Test that None paths are rejected."""
        step = self.create_step("")
        is_valid, error = step._validate_script_path(None)

        assert is_valid is False
        assert "empty" in error.lower()

    def test_path_with_dot_prefix(self, tmp_path):
        """Test that paths with ./ prefix are accepted if valid."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Create a test script file
            script_file = tmp_path / "test_script.sh"
            script_file.write_text("#!/bin/sh\necho hello")

            step = self.create_step("./test_script.sh")
            is_valid, error = step._validate_script_path("./test_script.sh")

            assert is_valid is True
            assert error == ""
        finally:
            os.chdir(original_cwd)

    def test_path_with_only_double_dots(self):
        """Test that a path of just '..' is rejected."""
        step = self.create_step("..")
        is_valid, error = step._validate_script_path("..")

        assert is_valid is False
        assert "Path traversal detected" in error

    def test_windows_style_path_traversal(self, tmp_path):
        """Test that Windows-style path traversal is rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("..\\..\\etc\\passwd")
            is_valid, error = step._validate_script_path("..\\..\\etc\\passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
        finally:
            os.chdir(original_cwd)

    def test_mixed_path_separators(self, tmp_path):
        """Test that mixed path separators with traversal are rejected."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("scripts\\..\\../etc/passwd")
            is_valid, error = step._validate_script_path("scripts\\..\\../etc/passwd")

            assert is_valid is False
            assert "Path traversal detected" in error
        finally:
            os.chdir(original_cwd)

    def test_url_encoded_path_traversal_not_decoded(self, tmp_path):
        """Test that URL-encoded paths are not automatically decoded."""
        # Note: This tests that %2e%2e is treated literally, not as ..
        # The actual path would need to exist with this literal name
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            step = self.create_step("%2e%2e/etc/passwd")
            # %2e%2e is the URL-encoded form of .., but we treat paths literally
            # so this should not trigger the '..' check but will fail on
            # path resolution since the literal path doesn't exist
            is_valid, error = step._validate_script_path("%2e%2e/etc/passwd")

            # The path is valid from a traversal perspective (no literal ..)
            # It will fail later when checking if the file exists
            # Our validation should accept it since there's no literal ..
            assert is_valid is True
        finally:
            os.chdir(original_cwd)

    def test_deeply_nested_valid_path(self, tmp_path):
        """Test that deeply nested valid paths are accepted."""
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Create deeply nested directory structure
            nested_dir = tmp_path / "a" / "b" / "c" / "d"
            nested_dir.mkdir(parents=True)
            script_file = nested_dir / "script.sh"
            script_file.write_text("#!/bin/sh\necho hello")

            step = self.create_step("a/b/c/d/script.sh")
            is_valid, error = step._validate_script_path("a/b/c/d/script.sh")

            assert is_valid is True
            assert error == ""
        finally:
            os.chdir(original_cwd)
