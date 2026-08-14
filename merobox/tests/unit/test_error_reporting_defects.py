"""Regression tests for two error-reporting defects fixed on this branch.

Defect 1: step failure handlers folded a fixed string like "X failed" into
fail(), discarding the real exception text that fail() had already captured
in result["exception"]. Fixed by folding the exception message into the
fail() call itself, so it reaches console output instead of a generic label.

Defect 2: raw node/log output and error-message interpolations reached
console.print() calls wrapped in Rich markup tags without escaping. Text
containing square brackets (a multiaddr list is the common case) made Rich
raise MarkupError instead of printing the diagnosis. Fixed by markup=False
at raw-output sites and rich.markup.escape() at interpolation sites.
"""

import asyncio
from unittest.mock import MagicMock, patch

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.bootstrap.steps.group_create import CreateNamespaceStep

MULTIADDR_LIST = "[/ip4/127.0.0.1/tcp/2428, /ip4/10.0.0.5/tcp/2428]"


def _unwrapped(captured_out: str) -> str:
    """Undo Rich's terminal-width line wrapping so substring checks don't
    depend on the console width the test happens to run under."""
    return captured_out.replace("\n", "")


def _run(coro):
    # See test_assert_log_step.py: asyncio.run() clears the event loop on
    # exit, which breaks other test files' asyncio.get_event_loop() usage.
    try:
        return asyncio.run(coro)
    finally:
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass


def _exec_create_namespace(exc):
    """Drive CreateNamespaceStep through a failing create_namespace call."""
    step = CreateNamespaceStep(
        {
            "type": "create_namespace",
            "name": "t",
            "node": "n1",
            "application_id": "app",
        }
    )
    mock_client = MagicMock()
    mock_client.create_namespace.side_effect = exc
    with (
        patch.object(
            step,
            "_resolve_node_for_client",
            return_value=("http://localhost:1234", "n1"),
        ),
        patch(
            "merobox.commands.bootstrap.steps.group_create.get_client_for_rpc_url",
            return_value=mock_client,
        ),
        patch.object(step, "_print_node_logs_on_failure"),
    ):
        return _run(step.execute({}, {}))


class TestExceptionSurfacing:
    """A step's failure handler must print the real exception message, not
    a fixed label. Reverting the fix (fail("create_namespace failed", ...)
    instead of fail(f"create_namespace failed: {e}", ...)) drops the
    distinctive message from result["error"] and from the printed line."""

    def test_real_exception_message_reaches_output(self, capsys):
        result = _exec_create_namespace(RuntimeError("distinctive_boom_xyz_789"))
        assert result is False
        combined = _unwrapped(capsys.readouterr().out)
        assert "distinctive_boom_xyz_789" in combined


class TestMarkupSafety:
    """Text containing square brackets must reach the terminal unmangled
    instead of crashing Rich's markup parser."""

    def test_exception_message_with_multiaddr_list_does_not_crash(self, capsys):
        """Covers the escape()-wrapped interpolation site in
        CreateNamespaceStep's failure branch (defect 2, escaped-interpolation
        shape)."""
        result = _exec_create_namespace(
            RuntimeError(f"connection failed: {MULTIADDR_LIST}")
        )
        assert result is False
        combined = _unwrapped(capsys.readouterr().out)
        assert MULTIADDR_LIST in combined

    def test_raw_node_log_output_with_brackets_does_not_crash(self, capsys):
        """Covers the raw console.print(..., markup=False) site in
        BaseStep._print_node_logs_on_failure (defect 2, raw-output shape)."""
        manager = MagicMock()
        manager.binary_path = "/usr/local/bin/merod"
        manager.get_node_logs.return_value = f"listening on {MULTIADDR_LIST}\n"
        step = BaseStep({"name": "t"}, manager=manager)

        step._print_node_logs_on_failure(node_name="node-1", lines=10)

        combined = _unwrapped(capsys.readouterr().out)
        assert MULTIADDR_LIST in combined


class TestReportExpectedFailureEscaping:
    """base.py's _report_expected_failure() is the single choke point every
    step inherits its markup-safety from on the expected-failure path - the
    widest-surface fix on this branch."""

    def test_message_with_brackets_prints_intact(self, capsys):
        step = BaseStep({"name": "t"})
        step._report_expected_failure(f"context does not belong: {MULTIADDR_LIST}")

        combined = _unwrapped(capsys.readouterr().out)
        assert MULTIADDR_LIST in combined
