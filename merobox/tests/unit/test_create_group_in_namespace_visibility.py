"""
Unit tests for CreateGroupInNamespaceStep's optional `visibility` field (#2771).

When `visibility` is set, the step drives the admin-api REST endpoint directly
(the compiled client does not forward the field yet) and the request body must
carry the `visibility` key (camelCase body). When `visibility` is absent, the
step uses the compiled client and no HTTP body is built.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from merobox.commands.bootstrap.steps.namespace import CreateGroupInNamespaceStep


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _base_config(**overrides):
    cfg = {
        "type": "create_group_in_namespace",
        "name": "Create sub",
        "node": "n1",
        "namespace_id": "ns-hex",
        "group_name": "open-sub",
    }
    cfg.update(overrides)
    return cfg


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


class TestVisibilityValidation:
    def test_valid_open_passes(self):
        CreateGroupInNamespaceStep(_base_config(visibility="open"))

    def test_valid_restricted_passes(self):
        CreateGroupInNamespaceStep(_base_config(visibility="restricted"))

    def test_absent_visibility_passes(self):
        CreateGroupInNamespaceStep(_base_config())

    def test_invalid_visibility_value_raises(self):
        with pytest.raises(ValueError, match="visibility"):
            CreateGroupInNamespaceStep(_base_config(visibility="public"))

    def test_non_string_visibility_raises(self):
        with pytest.raises(ValueError, match="visibility"):
            CreateGroupInNamespaceStep(_base_config(visibility=True))


# -----------------------------------------------------------------------------
# Execute: body shape
# -----------------------------------------------------------------------------


class TestVisibilityBody:
    def _exec(self, step):
        with (
            patch.object(
                step,
                "_resolve_node_for_client",
                return_value=("http://localhost:7180", "n1"),
            ),
            patch.object(step, "_resolve_dynamic_value", side_effect=lambda v, *_: v),
            patch(
                "merobox.commands.bootstrap.steps.namespace.requests.post"
            ) as mock_post,
            patch(
                "merobox.commands.bootstrap.steps.namespace.get_client_for_rpc_url"
            ) as mock_get_client,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": {"groupId": "sub-hex"}}
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            mock_client = MagicMock()
            mock_client.create_group_in_namespace.return_value = {
                "data": {"groupId": "sub-hex"}
            }
            mock_get_client.return_value = mock_client

            result = _run(step.execute({}, {}))
            return result, mock_post, mock_client

    def test_visibility_open_posts_with_visibility_in_body(self):
        step = CreateGroupInNamespaceStep(_base_config(visibility="open"))
        result, mock_post, mock_client = self._exec(step)

        assert result is True
        # HTTP path is used, client method is NOT called.
        mock_post.assert_called_once()
        mock_client.create_group_in_namespace.assert_not_called()

        _, kwargs = mock_post.call_args
        body = kwargs["json"]
        assert body["visibility"] == "open"
        assert body["groupName"] == "open-sub"
        url = mock_post.call_args[0][0]
        assert url == "http://localhost:7180/admin-api/namespaces/ns-hex/groups"

    def test_visibility_restricted_posts_with_visibility_in_body(self):
        step = CreateGroupInNamespaceStep(_base_config(visibility="restricted"))
        result, mock_post, _ = self._exec(step)

        assert result is True
        body = mock_post.call_args[1]["json"]
        assert body["visibility"] == "restricted"

    def test_no_visibility_uses_client_and_omits_body(self):
        step = CreateGroupInNamespaceStep(_base_config())
        result, mock_post, mock_client = self._exec(step)

        assert result is True
        # Client path is used; no raw HTTP POST is issued.
        mock_post.assert_not_called()
        mock_client.create_group_in_namespace.assert_called_once()
        _, kwargs = mock_client.create_group_in_namespace.call_args
        assert "visibility" not in kwargs
