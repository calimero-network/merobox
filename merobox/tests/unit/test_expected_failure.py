"""
Unit tests for the ``expected_failure`` flag on non-``call`` step types.

Until now only ``execute.py`` (the ``call`` step) honored ``expected_failure``;
the flag was silently dropped on every other step type. These tests cover the
shared BaseStep helpers and the per-step behavior for the step types that were
updated: join_context, join_namespace, create_context, create_namespace,
install_application, create_namespace_invitation, create_group_in_namespace,
add_group_members.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.context import CreateContextStep
from merobox.commands.bootstrap.steps.group_create import CreateNamespaceStep
from merobox.commands.bootstrap.steps.group_invite import CreateNamespaceInvitationStep
from merobox.commands.bootstrap.steps.group_join import JoinNamespaceStep
from merobox.commands.bootstrap.steps.install import InstallApplicationStep
from merobox.commands.bootstrap.steps.join_context import JoinContextStep
from merobox.commands.bootstrap.steps.namespace import CreateGroupInNamespaceStep
from merobox.commands.bootstrap.steps.subgroup import AddGroupMembersStep


def _run(coro):
    """Run a coroutine inside pytest without relying on pytest-asyncio."""
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# BaseStep helpers
# =============================================================================


class TestExpectedFailureHelpers:
    """Verify the shared helpers on BaseStep."""

    def _make_step(self, **extra):
        config = {
            "type": "join_context",
            "name": "helper test",
            "node": "calimero-node-1",
            "context_id": "ctx1",
            **extra,
        }
        return JoinContextStep(config)

    def test_default_is_false(self):
        assert self._make_step()._is_expected_failure() is False

    def test_true_is_true(self):
        assert self._make_step(expected_failure=True)._is_expected_failure() is True

    def test_false_is_false(self):
        assert self._make_step(expected_failure=False)._is_expected_failure() is False

    def test_non_bool_raises(self):
        step = self._make_step(expected_failure="yes")
        with pytest.raises(ValueError, match="expected_failure.*boolean"):
            step._is_expected_failure()


# =============================================================================
# JoinContextStep — the user-visible bug that motivated this change
# =============================================================================


class TestJoinContextExpectedFailure:
    """Exercise the two failure branches in JoinContextStep.execute()."""

    def _setup(self, expected_failure=True):
        config = {
            "type": "join_context",
            "name": "t",
            "node": "node-2",
            "context_id": "ctx",
            "expected_failure": expected_failure,
        }
        return JoinContextStep(config)

    def _run_with_mock_client(self, step, mock_client):
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", "node-2"),
            ),
            patch(
                "merobox.commands.bootstrap.steps.join_context.get_client_for_rpc_url",
                return_value=mock_client,
            ),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
        ):
            return _run(step.execute({}, {}))

    def test_exception_path_passes_when_expected(self):
        """Client raises → step should return True when expected_failure=True."""
        mock_client = MagicMock()
        mock_client.join_context.side_effect = RuntimeError(
            "context does not belong to any group"
        )
        step = self._setup(expected_failure=True)
        assert self._run_with_mock_client(step, mock_client) is True

    def test_exception_path_fails_when_not_expected(self):
        """Without the flag the step must still surface the failure."""
        mock_client = MagicMock()
        mock_client.join_context.side_effect = RuntimeError("boom")
        step = self._setup(expected_failure=False)
        # Also patch _print_node_logs_on_failure since it needs self.manager
        with patch.object(step, "_print_node_logs_on_failure"):
            assert self._run_with_mock_client(step, mock_client) is False

    def test_jsonrpc_error_path_passes_when_expected(self):
        """HTTP-200 response carrying a JSON-RPC error envelope is the
        real-world shape merod returns for "context does not belong to any
        group" — this is the branch we regressed on before the fix."""
        mock_client = MagicMock()
        mock_client.join_context.return_value = {
            "data": {"error": {"type": "ApiError", "data": "context does not belong"}}
        }
        step = self._setup(expected_failure=True)
        assert self._run_with_mock_client(step, mock_client) is True

    def test_success_with_expected_failure_warns_but_still_passes(self):
        """Matches the existing `call` semantic: an over-eager
        expected_failure flag should not convert a passing workflow into a
        failing one during refactor."""
        mock_client = MagicMock()
        mock_client.join_context.return_value = {
            "data": {"contextId": "ctx", "memberPublicKey": "pk"}
        }
        step = self._setup(expected_failure=True)
        assert self._run_with_mock_client(step, mock_client) is True


# =============================================================================
# Per-step smoke tests — each updated step must honor the flag on its
# exception branch. We don't duplicate the JSON-RPC / success-path coverage
# from the JoinContext class above; the shared helpers guarantee those work
# everywhere once the exception branch is wired correctly.
# =============================================================================


def _smoke(step_cls, module_path, client_method, base_config):
    """Assert: client raises → step returns True under expected_failure=True,
    and False without the flag."""
    config_fail = {**base_config, "expected_failure": True}
    config_default = {**base_config}

    step_fail = step_cls(config_fail)
    step_default = step_cls(config_default)

    mock_client = MagicMock()
    getattr(mock_client, client_method).side_effect = RuntimeError("upstream boom")

    def _exec(step):
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", base_config["node"]),
            ),
            patch(
                f"{module_path}.get_client_for_rpc_url",
                return_value=mock_client,
            ),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
            patch.object(step, "_print_node_logs_on_failure"),
        ):
            return _run(step.execute({}, {}))

    assert _exec(step_fail) is True, f"{step_cls.__name__} should pass when expected"
    assert (
        _exec(step_default) is False
    ), f"{step_cls.__name__} should fail without expected_failure"


class TestPerStepSmoke:
    """One lightweight smoke test per updated step type."""

    def test_create_namespace(self):
        _smoke(
            CreateNamespaceStep,
            "merobox.commands.bootstrap.steps.group_create",
            "create_namespace",
            {
                "type": "create_namespace",
                "name": "t",
                "node": "n1",
                "application_id": "app",
            },
        )

    def test_create_namespace_invitation(self):
        _smoke(
            CreateNamespaceInvitationStep,
            "merobox.commands.bootstrap.steps.group_invite",
            "create_namespace_invitation",
            {
                "type": "create_namespace_invitation",
                "name": "t",
                "node": "n1",
                "namespace_id": "ns",
            },
        )

    def test_join_namespace(self):
        _smoke(
            JoinNamespaceStep,
            "merobox.commands.bootstrap.steps.group_join",
            "join_namespace",
            {
                "type": "join_namespace",
                "name": "t",
                "node": "n2",
                "namespace_id": "ns",
                "invitation": '{"type": "SignedGroupOpenInvitation"}',
            },
        )

    def test_create_context(self):
        _smoke(
            CreateContextStep,
            "merobox.commands.bootstrap.steps.context",
            "create_context",
            {
                "type": "create_context",
                "name": "t",
                "node": "n1",
                "application_id": "app",
                "group_id": "ns",
            },
        )

    def test_install_application(self, tmp_path):
        # InstallApplicationStep checks the path exists AND routes through a
        # container-vs-binary branch before reaching the client call. Give it
        # a real file and force the binary-mode branch so the mocked client
        # gets invoked and our expected_failure logic can run.
        fake_wasm = tmp_path / "app.wasm"
        fake_wasm.write_bytes(b"\0asm")
        base_config = {
            "type": "install_application",
            "name": "t",
            "node": "n1",
            "path": str(fake_wasm),
            "dev": True,
        }
        step_fail = InstallApplicationStep({**base_config, "expected_failure": True})
        step_default = InstallApplicationStep(base_config)

        mock_client = MagicMock()
        mock_client.install_dev_application.side_effect = RuntimeError("boom")

        def _exec(step):
            with (
                patch.object(
                    step,
                    "_resolve_node_for_client",
                    return_value=("http://localhost:1234", "n1"),
                ),
                patch(
                    "merobox.commands.bootstrap.steps.install.get_client_for_rpc_url",
                    return_value=mock_client,
                ),
                patch.object(step, "_is_binary_mode", return_value=True),
                patch.object(
                    step, "_resolve_dynamic_value", side_effect=lambda v, *_: v
                ),
                patch.object(step, "_print_node_logs_on_failure"),
            ):
                return _run(step.execute({}, {}))

        assert _exec(step_fail) is True
        assert _exec(step_default) is False

    def test_create_group_in_namespace(self):
        _smoke(
            CreateGroupInNamespaceStep,
            "merobox.commands.bootstrap.steps.namespace",
            "create_group_in_namespace",
            {
                "type": "create_group_in_namespace",
                "name": "t",
                "node": "n1",
                "namespace_id": "ns",
                "group_alias": "child",
            },
        )

    def test_add_group_members(self):
        _smoke(
            AddGroupMembersStep,
            "merobox.commands.bootstrap.steps.subgroup",
            "add_group_members",
            {
                "type": "add_group_members",
                "name": "t",
                "node": "n1",
                "group_id": "g",
                "members": [{"identity": "pk", "role": "Member"}],
            },
        )
