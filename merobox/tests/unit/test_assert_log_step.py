"""Unit tests for assert_log_absent / assert_log_present step types."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.config import validate_workflow_step
from merobox.commands.bootstrap.steps import assert_log as assert_log_module
from merobox.commands.bootstrap.steps.assert_log import (
    AssertLogAbsentStep,
    AssertLogPresentStep,
)


def _docker_manager_with_logs(node_logs: dict[str, str]) -> MagicMock:
    """Build a MagicMock that imitates DockerManager.

    Each entry in ``node_logs`` becomes a container whose ``logs(...)`` call
    returns the bytes form of the canned log content. Mirrors the access path
    used in BaseStep._print_node_logs_on_failure: manager.nodes[name].logs(...)
    falling back to manager.client.containers.get(name).
    """
    manager = MagicMock()
    # No binary_path => docker mode
    del manager.binary_path

    containers: dict[str, MagicMock] = {}
    for name, content in node_logs.items():
        container = MagicMock()

        def _logs(tail="all", timestamps=False, _content=content):
            text = _content
            if isinstance(tail, int):
                text = "\n".join(text.splitlines()[-tail:])
                if _content.endswith("\n"):
                    text += "\n"
            return text.encode("utf-8")

        container.logs.side_effect = _logs
        containers[name] = container

    manager.nodes = containers
    manager.client.containers.get.side_effect = lambda n: containers[n]
    manager.get_running_nodes.return_value = list(node_logs.keys())
    return manager


def _binary_manager_with_logs(node_logs: dict[str, str]) -> MagicMock:
    """Build a MagicMock that imitates BinaryManager."""
    manager = MagicMock()
    manager.binary_path = "/usr/local/bin/merod"
    manager.get_node_logs.side_effect = lambda name, lines=50: node_logs.get(name)
    manager.list_nodes.return_value = [{"name": n} for n in node_logs.keys()]
    return manager


def _run(coro):
    # asyncio.run() calls set_event_loop(None) in its finally block on
    # Python 3.10+, which leaks across tests: subsequent test files that
    # use the deprecated asyncio.get_event_loop().run_until_complete(...)
    # pattern then crash with "There is no current event loop in thread
    # 'MainThread'". Restore an event loop after each call so our tests
    # don't break other test files in the suite.
    try:
        return asyncio.run(coro)
    finally:
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
        except Exception:
            pass


# ============================================================================
# Schema / validation
# ============================================================================


class TestAssertLogAbsentValidation:
    def test_missing_nodes_field_raises(self):
        with pytest.raises(ValueError, match="nodes"):
            AssertLogAbsentStep(
                {"type": "assert_log_absent", "patterns": ["foo"]},
                manager=MagicMock(),
            )

    def test_missing_patterns_field_raises(self):
        with pytest.raises(ValueError, match="patterns"):
            AssertLogAbsentStep(
                {"type": "assert_log_absent", "nodes": ["node-1"]},
                manager=MagicMock(),
            )

    def test_empty_patterns_rejected(self):
        with pytest.raises(ValueError, match="patterns"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": [],
                },
                manager=MagicMock(),
            )

    def test_non_string_pattern_rejected(self):
        with pytest.raises(ValueError, match="patterns"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": [123],
                },
                manager=MagicMock(),
            )

    def test_regex_must_be_boolean(self):
        with pytest.raises(ValueError, match="regex"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": ["foo"],
                    "regex": "yes",
                },
                manager=MagicMock(),
            )

    def test_tail_lines_must_be_positive(self):
        with pytest.raises(ValueError, match="tail_lines"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": ["foo"],
                    "tail_lines": 0,
                },
                manager=MagicMock(),
            )

    def test_empty_nodes_list_is_allowed_meaning_all_running(self):
        # Empty list semantically means "all running nodes" per the spec
        AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": [],
                "patterns": ["foo"],
            },
            manager=MagicMock(),
        )

    def test_empty_string_pattern_rejected(self):
        # An empty-string pattern matches every line — almost certainly a YAML
        # templating bug, so reject at construction time.
        with pytest.raises(ValueError, match="empty string"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": ["valid", ""],
                },
                manager=MagicMock(),
            )

    def test_invalid_regex_pattern_rejected_at_construction(self):
        # When regex=True, malformed patterns should fail validation rather
        # than crash mid-execute with re.error.
        with pytest.raises(ValueError, match="not a valid regex"):
            AssertLogAbsentStep(
                {
                    "type": "assert_log_absent",
                    "nodes": ["node-1"],
                    "patterns": ["[invalid"],
                    "regex": True,
                },
                manager=MagicMock(),
            )

    def test_invalid_regex_only_validated_when_regex_flag_true(self):
        # Without regex=True the same string is a literal substring and should
        # not trigger a regex validation error.
        AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["[not-a-regex"],
            },
            manager=MagicMock(),
        )


class TestAssertLogPresentValidation:
    def test_min_matches_must_be_positive(self):
        with pytest.raises(ValueError, match="min_matches"):
            AssertLogPresentStep(
                {
                    "type": "assert_log_present",
                    "nodes": ["node-1"],
                    "patterns": ["foo"],
                    "min_matches": 0,
                },
                manager=MagicMock(),
            )

    def test_case_sensitive_must_be_boolean(self):
        with pytest.raises(ValueError, match="case_sensitive"):
            AssertLogPresentStep(
                {
                    "type": "assert_log_present",
                    "nodes": ["node-1"],
                    "patterns": ["foo"],
                    "case_sensitive": "yes",
                },
                manager=MagicMock(),
            )


# ============================================================================
# assert_log_absent behaviour
# ============================================================================


class TestAssertLogAbsentBehaviour:
    def test_passes_when_no_pattern_matches(self):
        manager = _docker_manager_with_logs(
            {"node-1": "boot complete\nready\n", "node-2": "ready\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1", "node-2"],
                "patterns": ["context not materialised", "unknown context"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is True

    def test_fails_when_any_pattern_matches_any_node(self):
        manager = _docker_manager_with_logs(
            {
                "node-1": "boot complete\nready\n",
                "node-2": "WARN inbound stream for unknown context\n",
            }
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1", "node-2"],
                "patterns": [
                    "context not materialised",
                    "inbound stream for unknown context",
                ],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_regex_flag_treats_patterns_as_regex(self):
        manager = _docker_manager_with_logs(
            {"node-1": "ERROR ctx=abc123 not materialised within join race\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": [r"ctx=\w+ not materialised"],
                "regex": True,
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_regex_disabled_treats_patterns_as_literal(self):
        # The regex meta-chars in the pattern would match if regex=True, but
        # with regex disabled the literal substring must match exactly.
        manager = _docker_manager_with_logs(
            {"node-1": "ERROR ctx=abc123 not materialised within join race\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": [r"ctx=\w+ not materialised"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is True

    def test_tail_lines_limits_search_window(self):
        # Pattern only appears early in the log. With tail_lines=2 we shouldn't
        # see it; without tail_lines we should.
        full_log = (
            "no owned identities found for context\n"
            "boot complete\n"
            "step 1 done\n"
            "step 2 done\n"
        )
        manager = _docker_manager_with_logs({"node-1": full_log})
        step_limited = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["no owned identities found"],
                "tail_lines": 2,
            },
            manager=manager,
        )
        assert _run(step_limited.execute({}, {})) is True

        # Confirm the same pattern *would* match without a tail bound.
        manager2 = _docker_manager_with_logs({"node-1": full_log})
        step_unbounded = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["no owned identities found"],
            },
            manager=manager2,
        )
        assert _run(step_unbounded.execute({}, {})) is False

    def test_case_insensitive_match(self):
        manager = _docker_manager_with_logs(
            {"node-1": "WARN INBOUND STREAM FOR UNKNOWN CONTEXT\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["inbound stream for unknown context"],
                "case_sensitive": False,
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_empty_nodes_list_means_all_running_nodes(self):
        manager = _docker_manager_with_logs(
            {
                "node-a": "ok\n",
                "node-b": "context not materialised within join race window\n",
            }
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": [],
                "patterns": ["context not materialised"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False
        manager.get_running_nodes.assert_called()

    def test_fails_when_no_logs_retrievable_from_any_node(self):
        # Regression for Cursor Bugbot: a typo'd nodes list (every fetch
        # returns None) must not silently pass — that turns the gate into
        # a no-op and lets regressions slip through.
        manager = MagicMock()
        del manager.binary_path
        manager.nodes = {}
        manager.client.containers.get.side_effect = Exception("no such container")
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["typoed-node-1", "typoed-node-2"],
                "patterns": ["context not materialised"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_binary_mode_uses_manager_get_node_logs(self):
        manager = _binary_manager_with_logs(
            {"node-1": "ready\nWARN inbound stream for unknown context\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["inbound stream for unknown context"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False
        # Must have gone through the binary path, not docker.
        manager.get_node_logs.assert_called()


# ============================================================================
# assert_log_present behaviour
# ============================================================================


class TestAssertLogPresentBehaviour:
    def test_passes_when_every_pattern_appears_in_any_node(self):
        manager = _docker_manager_with_logs(
            {
                "boot-node": "Sync session complete\nGoodbye\n",
                "follower": "Replayed 3 ops\n",
            }
        )
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["boot-node", "follower"],
                "patterns": ["Sync session complete", "Replayed"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is True

    def test_fails_when_pattern_missing_in_all_nodes(self):
        manager = _docker_manager_with_logs(
            {"boot-node": "Booting...\n", "follower": "follower up\n"}
        )
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["boot-node", "follower"],
                "patterns": ["Sync session complete"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_min_matches_aggregates_across_nodes(self):
        # Pattern appears once in each node => 2 total across the union.
        # Default min_matches=1 should pass; min_matches=3 should fail.
        manager = _docker_manager_with_logs(
            {
                "node-1": "Sync session complete\nmore\n",
                "node-2": "Sync session complete\n",
            }
        )
        pass_step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1", "node-2"],
                "patterns": ["Sync session complete"],
                "min_matches": 2,
            },
            manager=manager,
        )
        assert _run(pass_step.execute({}, {})) is True

        manager2 = _docker_manager_with_logs(
            {
                "node-1": "Sync session complete\nmore\n",
                "node-2": "Sync session complete\n",
            }
        )
        fail_step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1", "node-2"],
                "patterns": ["Sync session complete"],
                "min_matches": 3,
            },
            manager=manager2,
        )
        assert _run(fail_step.execute({}, {})) is False

    def test_regex_flag_in_present(self):
        manager = _docker_manager_with_logs(
            {"node-1": "Sync session complete in 1234ms\n"}
        )
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1"],
                "patterns": [r"Sync session complete in \d+ms"],
                "regex": True,
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is True

    def test_present_fails_when_no_logs_retrievable(self):
        # Symmetric to the absent case: if every fetch returns None,
        # report it explicitly instead of relying on the patterns-missing
        # branch's generic "had 0 hit(s)" message.
        manager = _docker_manager_with_logs({"node-1": "ok\n"})
        # Replace the container.logs to fail for every node
        manager.client.containers.get.side_effect = Exception("no such container")
        manager.nodes = {}
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["ghost"],
                "patterns": ["anything"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_duplicate_nodes_do_not_double_count_hits(self):
        # If a user accidentally lists the same node twice, _fetch_log would
        # be called twice for the same content and each matching line would
        # be counted twice — letting a pattern with hits/2 satisfy
        # min_matches=2. Dedupe target_nodes at resolution time.
        manager = _docker_manager_with_logs({"node-1": "Sync session complete\n"})
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1", "node-1"],
                "patterns": ["Sync session complete"],
                "min_matches": 2,
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_duplicate_patterns_do_not_double_count_hits(self):
        # Regression for meroreviewer critical: a pattern listed twice must
        # not let a single matching line satisfy min_matches=2. The user's
        # intent for duplicates is unambiguous (same pattern = one rule).
        manager = _docker_manager_with_logs({"node-1": "Sync session complete\n"})
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1"],
                "patterns": ["Sync session complete", "Sync session complete"],
                "min_matches": 2,
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is False

    def test_binary_mode_used_for_present(self):
        manager = _binary_manager_with_logs({"node-1": "Sync session complete\n"})
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1"],
                "patterns": ["Sync session complete"],
            },
            manager=manager,
        )
        assert _run(step.execute({}, {})) is True
        manager.get_node_logs.assert_called()


# ============================================================================
# Failure reporting
# ============================================================================


class TestFailureReporting:
    def test_absent_failure_reports_match_details(self, capsys):
        manager = _docker_manager_with_logs(
            {"node-1": "boot complete\nWARN inbound stream for unknown context\n"}
        )
        step = AssertLogAbsentStep(
            {
                "type": "assert_log_absent",
                "nodes": ["node-1"],
                "patterns": ["inbound stream for unknown context"],
            },
            manager=manager,
        )
        result = _run(step.execute({}, {}))
        assert result is False
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Failure output must call out the offending node, pattern, and line
        # so CI doesn't need the log artifact to diagnose the failure.
        assert "node-1" in combined
        assert "inbound stream for unknown context" in combined

    def test_present_failure_lists_missing_patterns(self, capsys):
        manager = _docker_manager_with_logs({"node-1": "boot complete\n"})
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["node-1"],
                "patterns": ["Sync session complete"],
            },
            manager=manager,
        )
        result = _run(step.execute({}, {}))
        assert result is False
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Sync session complete" in combined


def _manager_with_changing_logs(node: str, successive: list[str]) -> MagicMock:
    """A docker-like manager whose log content changes between reads.

    Each `logs(...)` call returns the next entry, holding on the last one. This
    is what a node actually looks like to a polling assertion: the line is not
    there, and then it is.
    """
    manager = MagicMock()
    del manager.binary_path
    state = {"i": 0}

    def _logs(tail="all", timestamps=False):
        content = successive[min(state["i"], len(successive) - 1)]
        state["i"] += 1
        return content.encode("utf-8")

    container = MagicMock()
    container.logs.side_effect = _logs
    manager.nodes = {node: container}
    manager.client.containers.get.side_effect = lambda n: container
    manager.get_running_nodes.return_value = [node]
    return manager


async def _no_sleep(_seconds):
    return None


class TestAssertLogPresentTimeout:
    """Polling replaces a fixed sleep in front of the assertion."""

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValueError):
            AssertLogPresentStep(
                {"type": "assert_log_present", "patterns": ["x"], "timeout": 0},
                MagicMock(),
                MagicMock(),
            )

    def test_check_interval_must_be_positive(self):
        with pytest.raises(ValueError):
            AssertLogPresentStep(
                {
                    "type": "assert_log_present",
                    "patterns": ["x"],
                    "check_interval": -1,
                },
                MagicMock(),
                MagicMock(),
            )

    def test_without_timeout_the_log_is_read_exactly_once(self):
        """The historical behaviour, pinned.

        Adding polling must not change what a workflow that never asked for it
        asserts — nor how long it takes.
        """
        manager = _manager_with_changing_logs("n1", ["nothing yet", "the line"])
        step = AssertLogPresentStep(
            {"type": "assert_log_present", "nodes": ["n1"], "patterns": ["the line"]},
            manager,
            MagicMock(),
        )
        assert _run(step.execute({}, {})) is False
        assert manager.nodes["n1"].logs.call_count == 1

    def test_a_line_that_arrives_late_is_waited_for(self):
        """The flake this exists to remove: the event happened, just not yet."""
        manager = _manager_with_changing_logs(
            "n1", ["booting", "still booting", "prefetching announced blob"]
        )
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["n1"],
                "patterns": ["prefetching announced blob"],
                "timeout": 30,
                "check_interval": 1,
            },
            manager,
            MagicMock(),
        )
        with patch.object(assert_log_module.asyncio, "sleep", _no_sleep):
            assert _run(step.execute({}, {})) is True
        assert manager.nodes["n1"].logs.call_count == 3

    def test_a_line_that_never_arrives_still_fails(self):
        """A timeout must not turn a real regression into a pass."""
        manager = _manager_with_changing_logs("n1", ["booting"])
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["n1"],
                "patterns": ["never emitted"],
                "timeout": 1,
                "check_interval": 1,
            },
            manager,
            MagicMock(),
        )
        with patch.object(assert_log_module.asyncio, "sleep", _no_sleep):
            assert _run(step.execute({}, {})) is False

    def test_hits_are_not_accumulated_across_polls(self):
        """The trap in polling: each poll re-reads the WHOLE log.

        One line that is present from the first read must count once, not once
        per attempt — otherwise any `min_matches` is satisfied by simply waiting
        long enough, and every count-based gate silently stops counting.
        """
        manager = _manager_with_changing_logs("n1", ["synced once"])
        step = AssertLogPresentStep(
            {
                "type": "assert_log_present",
                "nodes": ["n1"],
                "patterns": ["synced once"],
                "min_matches": 3,
                "timeout": 1,
                "check_interval": 1,
            },
            manager,
            MagicMock(),
        )
        with patch.object(assert_log_module.asyncio, "sleep", _no_sleep):
            assert _run(step.execute({}, {})) is False
        assert (
            manager.nodes["n1"].logs.call_count > 1
        ), "the step must actually have polled, or this proves nothing"


class TestAssertLogPresentSchema:
    """Every field the step reads must also be declared to the validator.

    These are two separate gates. `_validate_field_types` runs on a step object
    a test can build directly; `validate_workflow_step` runs over the YAML
    before any step exists, and `extra="forbid"` means a field it does not
    declare is a hard error — "unknown field 'timeout' - the step would ignore
    it".

    0.6.78 shipped `timeout` on the step and not in the schema, so every
    workflow that used it failed validation while the whole unit suite stayed
    green: the behaviour tests construct the step and never pass through the
    gate in front of it. That is the gap this class exists to close, so a field
    added to one and not the other fails here rather than in a consumer's CI.
    """

    def test_timeout_and_check_interval_validate(self):
        assert (
            validate_workflow_step(
                {
                    "type": "assert_log_present",
                    "name": "waits for a line",
                    "nodes": ["n1"],
                    "patterns": ["x"],
                    "timeout": 60,
                    "check_interval": 2,
                },
                0,
            )
            == []
        )

    def test_every_field_the_step_reads_is_declared(self):
        """Catches the next one, not just this one.

        Reads the field names `AssertLogPresentStep` pulls out of its config and
        asserts the schema declares each. A field added to the step alone fails
        here by construction.
        """
        import inspect
        import re

        from merobox.commands.bootstrap.config import AssertLogPresentStepConfig

        source = inspect.getsource(assert_log_module.AssertLogPresentStep)
        source += inspect.getsource(assert_log_module._AssertLogStepBase)
        read = set(re.findall(r"""self\.config(?:\.get\(|\[)["'](\w+)["']""", source))
        declared = set(AssertLogPresentStepConfig.model_fields)
        undeclared = read - declared
        assert not undeclared, (
            f"the step reads {sorted(undeclared)} but the schema does not declare "
            f"them, so a workflow using them fails validation with "
            f"'unknown field'"
        )
