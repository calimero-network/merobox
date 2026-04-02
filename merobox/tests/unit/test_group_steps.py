"""
Unit tests for group workflow step classes.

Covers: CreateGroupStep, CreateGroupInvitationStep, JoinGroupStep,
        JoinContextStep (join_context module) — validation logic and fallback capture.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_create import CreateGroupStep
from merobox.commands.bootstrap.steps.group_invite import CreateGroupInvitationStep
from merobox.commands.bootstrap.steps.group_join import JoinGroupStep
from merobox.commands.bootstrap.steps.join_context import JoinContextStep

# =============================================================================
# CreateGroupStep
# =============================================================================


class TestCreateGroupStep:
    """Tests for CreateGroupStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "create_group",
            "name": "Test Create Group",
            "node": "calimero-node-1",
            "application_id": "app123",
        }

    def _make_step(self, config: dict) -> CreateGroupStep:
        return CreateGroupStep(config)

    def test_valid_config_passes_validation(self):
        """Valid config should not raise."""
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_application_id_raises(self):
        config = {**self.base_config}
        del config["application_id"]
        with pytest.raises(ValueError, match="application_id"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": 123}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_application_id_not_string_raises(self):
        config = {**self.base_config, "application_id": 42}
        with pytest.raises(ValueError, match="'application_id' must be a string"):
            self._make_step(config)

    def test_node_none_raises(self):
        config = {**self.base_config, "node": None}
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_application_id_none_raises(self):
        config = {**self.base_config, "application_id": None}
        with pytest.raises(ValueError, match="application_id"):
            self._make_step(config)


# =============================================================================
# CreateGroupInvitationStep
# =============================================================================


class TestCreateGroupInvitationStep:
    """Tests for CreateGroupInvitationStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "create_group_invitation",
            "name": "Test Create Invitation",
            "node": "calimero-node-1",
            "group_id": "group-abc",
        }

    def _make_step(self, config: dict) -> CreateGroupInvitationStep:
        return CreateGroupInvitationStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_group_id_raises(self):
        config = {**self.base_config}
        del config["group_id"]
        with pytest.raises(ValueError, match="group_id"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": 99}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_group_id_not_string_raises(self):
        config = {**self.base_config, "group_id": ["bad"]}
        with pytest.raises(ValueError, match="'group_id' must be a string"):
            self._make_step(config)

    def test_node_none_raises(self):
        config = {**self.base_config, "node": None}
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)


# =============================================================================
# JoinGroupStep
# =============================================================================


class TestJoinGroupStep:
    """Tests for JoinGroupStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "join_group",
            "name": "Test Join Group",
            "node": "calimero-node-2",
            "invitation": '{"type": "SignedGroupOpenInvitation"}',
        }

    def _make_step(self, config: dict) -> JoinGroupStep:
        return JoinGroupStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_invitation_raises(self):
        config = {**self.base_config}
        del config["invitation"]
        with pytest.raises(ValueError, match="invitation"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": 0}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_node_none_raises(self):
        config = {**self.base_config, "node": None}
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_invitation_can_be_dict(self):
        """invitation field accepts a dict (resolved dynamic value)."""
        config = {**self.base_config, "invitation": {"key": "value"}}
        self._make_step(config)

    def test_invitation_none_raises(self):
        config = {**self.base_config, "invitation": None}
        with pytest.raises(ValueError, match="invitation"):
            self._make_step(config)


# =============================================================================
# JoinContextStep (group membership join_context workflow step)
# =============================================================================


class TestJoinContextStepGroupMembership:
    """Tests for JoinContextStep validation and fallback capture."""

    def setup_method(self):
        self.base_config = {
            "type": "join_context",
            "name": "Test Join Context",
            "node": "calimero-node-2",
            "context_id": "ctx-xyz",
        }

    def _make_step(self, config: dict) -> JoinContextStep:
        return JoinContextStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_context_id_raises(self):
        config = {**self.base_config}
        del config["context_id"]
        with pytest.raises(ValueError, match="context_id"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": True}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_context_id_not_string_raises(self):
        config = {**self.base_config, "context_id": {"bad": "type"}}
        with pytest.raises(ValueError, match="'context_id' must be a string"):
            self._make_step(config)

    def test_member_public_key_fallback_runs_when_context_id_already_captured(self):
        """memberPublicKey fallback must run even if context_id was already in dynamic_values.

        Regression test for the bug where the memberPublicKey capture was
        nested inside the `if context_id not in dynamic_values` block.
        Exercises the actual step execute() method via mocked client.
        """
        node_name = "calimero-node-2"
        step = self._make_step(self.base_config)

        # Pre-populate context_id as if _export_variables already captured it,
        # but leave member public key absent.
        dynamic_values = {f"context_id_{node_name}": "ctx-xyz"}
        workflow_results = {}

        # Mock the client to return an API response with both fields
        mock_client = MagicMock()
        mock_client.join_context.return_value = {
            "data": {
                "contextId": "ctx-xyz",
                "memberPublicKey": "pk-abc123",
            }
        }

        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:1234", node_name),
            ),
            patch(
                "merobox.commands.bootstrap.steps.join_context.get_client_for_rpc_url",
                return_value=mock_client,
            ),
            patch.object(
                step,
                "_resolve_dynamic_value",
                side_effect=lambda val, *_: val,
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                step.execute(workflow_results, dynamic_values)
            )

        assert result is True
        # context_id was already there and should remain unchanged
        assert dynamic_values[f"context_id_{node_name}"] == "ctx-xyz"
        # memberPublicKey MUST have been captured despite context_id already existing
        assert (
            dynamic_values.get(f"context_member_public_key_{node_name}") == "pk-abc123"
        )
