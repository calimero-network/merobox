"""Which shell a local `type: script` step runs under.

Runs on every platform by patching `os.name`, so the Windows branch is
exercised on the Linux runners that gate this repository.
"""

from unittest import mock

import pytest

from merobox.commands.bootstrap.steps.script import posix_shell


def test_posix_hosts_use_bin_sh():
    with mock.patch("merobox.commands.bootstrap.steps.script.os.name", "posix"):
        assert posix_shell() == "/bin/sh"


def test_windows_resolves_sh_from_path():
    """`/bin/sh` is a path, not a name, and does not exist on Windows."""
    with mock.patch("merobox.commands.bootstrap.steps.script.os.name", "nt"):
        with mock.patch(
            "merobox.commands.bootstrap.steps.script.shutil.which",
            side_effect=lambda n: (
                r"C:\Program Files\Git\usr\bin\sh.exe" if n == "sh" else None
            ),
        ):
            assert posix_shell().endswith("sh.exe")


def test_windows_falls_back_to_bash():
    def which(name):
        return r"C:\Program Files\Git\bin\bash.exe" if name == "bash" else None

    with mock.patch("merobox.commands.bootstrap.steps.script.os.name", "nt"):
        with mock.patch(
            "merobox.commands.bootstrap.steps.script.shutil.which", side_effect=which
        ):
            assert posix_shell().endswith("bash.exe")


def test_windows_without_a_shell_says_so():
    """Otherwise the only symptom is a script step failing for no stated reason."""
    with mock.patch("merobox.commands.bootstrap.steps.script.os.name", "nt"):
        with mock.patch(
            "merobox.commands.bootstrap.steps.script.shutil.which", return_value=None
        ):
            with pytest.raises(FileNotFoundError, match="POSIX shell"):
                posix_shell()
