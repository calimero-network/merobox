"""
Unit tests for namespace/group workflow step classes.

Covers: CreateNamespaceStep, CreateNamespaceInvitationStep, JoinNamespaceStep,
        JoinContextStep (join_context module) — validation logic and fallback capture.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.group_create import CreateNamespaceStep
from merobox.commands.bootstrap.steps.group_invite import CreateNamespaceInvitationStep
from merobox.commands.bootstrap.steps.group_join import JoinNamespaceStep
from merobox.commands.bootstrap.steps.join_context import JoinContextStep

# =============================================================================
# CreateNamespaceStep
# =============================================================================


class TestCreateNamespaceStep:
    """Tests for CreateNamespaceStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "create_namespace",
            "name": "Test Create Namespace",
            "node": "calimero-node-1",
            "application_id": "app123",
        }

    def _make_step(self, config: dict) -> CreateNamespaceStep:
        return CreateNamespaceStep(config)

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
# CreateNamespaceInvitationStep
# =============================================================================


class TestCreateNamespaceInvitationStep:
    """Tests for CreateNamespaceInvitationStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "create_namespace_invitation",
            "name": "Test Create Invitation",
            "node": "calimero-node-1",
            "namespace_id": "namespace-abc",
        }

    def _make_step(self, config: dict) -> CreateNamespaceInvitationStep:
        return CreateNamespaceInvitationStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_namespace_and_group_id_raises(self):
        config = {**self.base_config}
        del config["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": 99}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_namespace_id_not_string_raises(self):
        config = {**self.base_config, "namespace_id": ["bad"]}
        with pytest.raises(ValueError, match="'namespace_id'"):
            self._make_step(config)

    def test_deprecated_group_id_alias_still_works(self):
        config = {
            "type": "create_namespace_invitation",
            "name": "Alias test",
            "node": "calimero-node-1",
            "group_id": "legacy-group-id",
        }
        self._make_step(config)

    def test_node_none_raises(self):
        config = {**self.base_config, "node": None}
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)


# =============================================================================
# JoinNamespaceStep
# =============================================================================


class TestJoinNamespaceStep:
    """Tests for JoinNamespaceStep validation."""

    def setup_method(self):
        self.base_config = {
            "type": "join_namespace",
            "name": "Test Join Namespace",
            "node": "calimero-node-2",
            "namespace_id": "namespace-xyz",
            "invitation": '{"type": "SignedGroupOpenInvitation"}',
        }

    def _make_step(self, config: dict) -> JoinNamespaceStep:
        return JoinNamespaceStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_namespace_and_group_id_raises(self):
        config = {**self.base_config}
        del config["namespace_id"]
        with pytest.raises(ValueError, match="namespace_id"):
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

    def test_deprecated_group_id_alias_still_works(self):
        config = {
            "type": "join_namespace",
            "name": "Alias test",
            "node": "calimero-node-2",
            "group_id": "legacy-group-id",
            "invitation": '{"type": "SignedGroupOpenInvitation"}',
        }
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


# =============================================================================
# ReparentGroupStep
#
# Strict-tree refactor (calimero-network/core PR #2200) replaces the
# nest_group + unnest_group two-step pattern with a single atomic
# reparent_group primitive. Orphan group state is no longer expressible.
# =============================================================================


class TestReparentGroupStep:
    """Validation tests for ReparentGroupStep."""

    def setup_method(self):
        self.base_config = {
            "type": "reparent_group",
            "name": "Test Reparent",
            "node": "calimero-node-1",
            "child_group_id": "abcd1234",
            "new_parent_id": "ef567890",
        }

    def _make_step(self, config: dict):
        from merobox.commands.bootstrap.steps.subgroup import ReparentGroupStep

        return ReparentGroupStep(config)

    def test_valid_config_passes_validation(self):
        self._make_step(self.base_config)

    def test_missing_node_raises(self):
        config = {**self.base_config}
        del config["node"]
        with pytest.raises(ValueError, match="node"):
            self._make_step(config)

    def test_missing_child_group_id_raises(self):
        config = {**self.base_config}
        del config["child_group_id"]
        with pytest.raises(ValueError, match="child_group_id"):
            self._make_step(config)

    def test_missing_new_parent_id_raises(self):
        config = {**self.base_config}
        del config["new_parent_id"]
        with pytest.raises(ValueError, match="new_parent_id"):
            self._make_step(config)

    def test_node_not_string_raises(self):
        config = {**self.base_config, "node": 123}
        with pytest.raises(ValueError, match="'node' must be a string"):
            self._make_step(config)

    def test_child_group_id_not_string_raises(self):
        config = {**self.base_config, "child_group_id": 123}
        with pytest.raises(ValueError, match="'child_group_id' must be a string"):
            self._make_step(config)

    def test_new_parent_id_not_string_raises(self):
        config = {**self.base_config, "new_parent_id": 456}
        with pytest.raises(ValueError, match="'new_parent_id' must be a string"):
            self._make_step(config)


class TestNestUnnestRemoved:
    """The old NestGroupStep / UnnestGroupStep classes must not exist."""

    def test_nest_group_step_removed(self):
        from merobox.commands.bootstrap.steps import subgroup

        assert not hasattr(
            subgroup, "NestGroupStep"
        ), "NestGroupStep should be removed in the strict-tree refactor"

    def test_unnest_group_step_removed(self):
        from merobox.commands.bootstrap.steps import subgroup

        assert not hasattr(
            subgroup, "UnnestGroupStep"
        ), "UnnestGroupStep should be removed in the strict-tree refactor"

    def test_nest_group_config_removed(self):
        from merobox.commands.bootstrap import config

        assert not hasattr(config, "NestGroupStepConfig")

    def test_unnest_group_config_removed(self):
        from merobox.commands.bootstrap import config

        assert not hasattr(config, "UnnestGroupStepConfig")


class TestReparentGroupStepConfigSchema:
    """Pydantic schema validation for the new step type."""

    def test_pydantic_schema_accepts_valid(self):
        from merobox.commands.bootstrap.config import ReparentGroupStepConfig

        cfg = ReparentGroupStepConfig(
            name="test",
            node="n1",
            child_group_id="abc",
            new_parent_id="def",
        )
        assert cfg.type == "reparent_group"
        assert cfg.child_group_id == "abc"
        assert cfg.new_parent_id == "def"

    def test_pydantic_schema_rejects_missing_new_parent_id(self):
        from pydantic import ValidationError

        from merobox.commands.bootstrap.config import ReparentGroupStepConfig

        with pytest.raises(ValidationError):
            ReparentGroupStepConfig(
                name="test",
                node="n1",
                child_group_id="abc",
            )

    def test_pydantic_schema_rejects_missing_child_group_id(self):
        from pydantic import ValidationError

        from merobox.commands.bootstrap.config import ReparentGroupStepConfig

        with pytest.raises(ValidationError):
            ReparentGroupStepConfig(
                name="test",
                node="n1",
                new_parent_id="def",
            )
