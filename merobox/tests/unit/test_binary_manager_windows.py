"""Platform branches in BinaryManager.

These run on every platform: the point is that the Windows path is exercised on
the Linux runners that actually gate this repository, since a branch only
compiled on Windows is a branch nobody sees until a Windows user finds it.
"""

import subprocess
from unittest import mock

from merobox.commands import binary_manager as bm


class _FakeStdin:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, with_stdin=True):
        self.stdin = _FakeStdin() if with_stdin else None
        self.terminated = False

    def terminate(self):
        self.terminated = True


def test_windows_stop_closes_stdin_rather_than_terminating():
    """`terminate()` on Windows is TerminateProcess: it runs no code, so the
    node never flushes. Closing stdin is the request merod listens for."""
    proc = _FakeProcess()
    with mock.patch.object(bm, "IS_WINDOWS", True):
        bm._request_stop(proc)

    assert proc.stdin.closed, "the stop request is the pipe closing"
    assert not proc.terminated, "TerminateProcess would skip the drain entirely"


def test_unix_stop_still_signals():
    proc = _FakeProcess()
    with mock.patch.object(bm, "IS_WINDOWS", False):
        bm._request_stop(proc)

    assert proc.terminated
    assert not proc.stdin.closed, "unix stops with a signal, not by closing stdin"


def test_windows_stop_tolerates_a_node_without_a_pipe():
    """A node adopted from a previous run has no stdin we own. It must not raise."""
    proc = _FakeProcess(with_stdin=False)
    with mock.patch.object(bm, "IS_WINDOWS", True):
        bm._request_stop(proc)


def test_windows_liveness_reads_tasklist_output_not_the_exit_code():
    """tasklist exits 0 even when nothing matches, so only the row means alive."""
    absent = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="INFO: No tasks are running which match.\n"
    )
    present = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='"merod.exe","4321","Console","1","10 K"\n'
    )
    with mock.patch.object(bm, "IS_WINDOWS", True):
        with mock.patch.object(bm.subprocess, "run", return_value=absent):
            assert bm._is_pid_alive(4321) is False
        with mock.patch.object(bm.subprocess, "run", return_value=present):
            assert bm._is_pid_alive(4321) is True


def test_windows_force_kill_uses_taskkill_because_sigkill_does_not_exist():
    with mock.patch.object(bm, "IS_WINDOWS", True):
        with mock.patch.object(bm.subprocess, "run") as run:
            bm._force_kill_pid(4321)

    argv = run.call_args[0][0]
    assert argv[:2] == ["taskkill", "/PID"]
    assert "/F" in argv, "without /F taskkill cannot stop a windowless console process"
