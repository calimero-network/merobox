"""A node that panics must fail its workflow.

Assertions read RPC responses, so a node can panic, restart, and leave every
step green. Exactly one scenario in calimero-network/core greps its logs for
this; the other 89 jobs never look.
"""

from unittest.mock import MagicMock

from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.manager import panic_messages

STRUCTURED = (
    "ERROR panic.message=Mailbox has closed panic.thread=tokio-runtime-worker "
    'panic.file=crates/node/src/x.rs "Application panic occurred"'
)
RUST = "thread 'tokio-runtime-worker' panicked at crates/node/src/x.rs:42:9:"
ANSI = (
    "\x1b[31mERROR\x1b[0m \x1b[2mpanic.message\x1b[0m=boom happened "
    'panic.thread=main "Application panic occurred"'
)


class TestPanicMessages:
    def test_structured_hook_line_yields_the_whole_message(self):
        assert panic_messages(STRUCTURED) == ["Mailbox has closed"]

    def test_default_rust_line_is_matched_too(self):
        # A panic raised before the hook is installed leaves only this shape.
        assert panic_messages(RUST) == [RUST]

    def test_ansi_escapes_do_not_hide_a_panic(self):
        # Escapes sit between the field name and its `=` in node logs.
        assert panic_messages(ANSI) == ["boom happened"]

    def test_prose_containing_the_word_is_not_a_panic(self):
        assert panic_messages("INFO the word panicked appears here") == []

    def test_clean_log_is_clean(self):
        assert panic_messages("INFO started\nINFO ready") == []

    def test_every_panic_in_a_log_is_reported(self):
        assert len(panic_messages("\n".join([STRUCTURED, "INFO fine", RUST]))) == 2


def _executor(panics, config=None):
    ex = WorkflowExecutor.__new__(WorkflowExecutor)
    ex.config = config if config is not None else {}
    ex.manager = MagicMock()
    ex.manager.client = object()
    ex.manager.scan_nodes_for_panics.return_value = panics
    return ex


class TestExecutorGate:
    def test_a_panic_fails_the_workflow(self):
        assert (
            _executor({"node-1": ["Mailbox has closed"]})._assert_no_node_panicked()
            is False
        )

    def test_no_panic_passes(self):
        assert _executor({})._assert_no_node_panicked() is True

    def test_a_workflow_may_opt_out(self):
        ex = _executor({"node-1": ["deliberate"]}, {"fail_on_panic": False})
        assert ex._assert_no_node_panicked() is True

    def test_a_scan_that_errors_does_not_fail_the_run(self):
        # Best-effort: an unreadable container must not invent a failure.
        ex = _executor({})
        ex.manager.scan_nodes_for_panics.side_effect = RuntimeError("docker gone")
        assert ex._assert_no_node_panicked() is True

    def test_no_manager_is_not_a_panic(self):
        ex = _executor({})
        ex.manager = None
        assert ex._assert_no_node_panicked() is True

    def test_a_scan_returning_no_messages_does_not_fail_the_run(self):
        # The gate must fail only on a panic it can name, never on a shape it
        # did not expect back from the scan.
        assert _executor({"node-1": []})._assert_no_node_panicked() is True
        assert _executor(MagicMock())._assert_no_node_panicked() is True
