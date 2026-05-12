"""
Generic metadata workflow steps (replaces the former group-alias steps).

Steps for setting and reading metadata records on groups, group members,
and group-registered contexts via the admin API.
"""

import json
from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

# Minimum calimero-client-py release that ships the *_metadata client methods.
_REQUIRED_CLIENT_VERSION = "0.6.10"


def _build_metadata_body(name: Any, data: Any, requester: Any) -> str:
    """Build the JSON body for a set-metadata admin API call.

    The server expects ``{"name": <str|null>, "data": {<str>:<str>...},
    "requester": <pubkey|null>}``. ``requester`` is ``Option<PublicKey>``
    server-side, so sending ``null`` when it's not provided is fine.
    """
    return json.dumps({"name": name, "data": data or {}, "requester": requester})


class _SetMetadataBase(BaseStep):
    """Shared logic for the three set-metadata step variants.

    Subclasses set ``_api_method`` (the ``calimero_client_py`` method name),
    ``_extra_required`` (extra required string id fields beyond ``node`` /
    ``group_id``), ``_result_key_prefix`` and ``_what`` (a label for log
    output).
    """

    _api_method: str = ""
    _extra_required: tuple[str, ...] = ()
    _result_key_prefix: str = ""
    _what: str = "metadata"

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", *self._extra_required]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("group_id", *self._extra_required):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if not isinstance(self.config.get("node"), str):
            raise ValueError(f"Step '{step_name}': 'node' must be a string")
        record_name = self.config.get("record_name")
        if record_name is not None and not isinstance(record_name, str):
            raise ValueError(
                f"Step '{step_name}': 'record_name' must be a string if provided"
            )
        data = self.config.get("data")
        if data is not None:
            if not isinstance(data, dict):
                raise ValueError(
                    f"Step '{step_name}': 'data' must be a dict if provided"
                )
            for k, v in data.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ValueError(
                        f"Step '{step_name}': 'data' must be a dict of string->string"
                    )
        requester = self.config.get("requester")
        if requester is not None and not isinstance(requester, str):
            raise ValueError(
                f"Step '{step_name}': 'requester' must be a string if provided"
            )

    def _log_target(self, group_id: str, extra_args: list[Any]) -> str:
        """Human-readable target description for the success log line.

        The base variant only knows ``group_id``; member/context variants
        also fold in the resolved ``member_id`` / ``context_id``.
        """
        return " / ".join(str(p) for p in (group_id, *extra_args))

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        # Extra id args (member_id / context_id) are passed positionally,
        # in declared order, *before* the JSON body — matching the admin API
        # path order .../{group_id}/members/{member_id}/metadata.
        extra_args = [
            self._resolve_dynamic_value(
                self.config[field], workflow_results, dynamic_values
            )
            for field in self._extra_required
        ]

        # The metadata record's "name" comes from a dedicated `record_name`
        # config key (kept distinct from the step's `name` label). Absent ->
        # null; we never fall back to the step label.
        name_raw = self.config.get("record_name")
        name = (
            self._resolve_dynamic_value(name_raw, workflow_results, dynamic_values)
            if isinstance(name_raw, str)
            else name_raw
        )
        data_raw = self.config.get("data")
        if isinstance(data_raw, dict):
            data = {
                k: (
                    self._resolve_dynamic_value(v, workflow_results, dynamic_values)
                    if isinstance(v, str)
                    else v
                )
                for k, v in data_raw.items()
            }
        else:
            data = data_raw
        requester_raw = self.config.get("requester")
        requester = (
            self._resolve_dynamic_value(requester_raw, workflow_results, dynamic_values)
            if isinstance(requester_raw, str)
            else requester_raw
        )
        body = _build_metadata_body(name, data, requester)

        expected_failure = self._is_expected_failure()

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            if not hasattr(client, self._api_method):
                msg = (
                    f"client.{self._api_method} not found — requires "
                    f"calimero-client-py >= {_REQUIRED_CLIENT_VERSION} (got an older version)"
                )
                if expected_failure:
                    self._report_expected_failure(msg)
                    return True
                console.print(f"[red]{msg}[/red]")
                return False
            method = getattr(client, self._api_method)
            api_result = method(group_id, *extra_args, body)
            result = ok(api_result)
        except Exception as e:
            result = fail(f"{self._api_method} failed", error=e)

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]{self._api_method} failed on {node_name}: {result.get('error')}[/red]"
            )
            return False

        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"{self._result_key_prefix}_{node_name}"] = result["data"]
        console.print(
            f"[green]✓ Set {self._what} for {self._log_target(group_id, extra_args)} "
            f"on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


class _GetMetadataBase(BaseStep):
    """Shared logic for the three get-metadata step variants.

    Mirrors ``GetGroupInfoStep`` in ``group_management.py``: the API result
    (a ``{"data": <MetadataRecord|null>}`` dict) is stored verbatim under
    ``workflow_results[f"{prefix}_{node_name}"]`` and run through
    ``_export_variables`` so ``outputs:`` / ``json_assert`` can reach into it.
    """

    _api_method: str = ""
    _extra_required: tuple[str, ...] = ()
    _result_key_prefix: str = ""
    _export_name: str = "metadata"
    _what: str = "metadata"

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", *self._extra_required]

    def _validate_field_types(self) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in ("node", "group_id", *self._extra_required):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _get_exportable_variables(self):
        # Mirrors GetGroupInfoStep's literal `{node_name}` template; the
        # actual export goes through `_export_variables` (custom `outputs:`).
        return [
            (
                self._export_name,
                f"{self._result_key_prefix}_{{node_name}}",
                f"{self._what} record",
            )
        ]

    def _log_target(self, group_id: str, extra_args: list[Any]) -> str:
        return " / ".join(str(p) for p in (group_id, *extra_args))

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        extra_args = [
            self._resolve_dynamic_value(
                self.config[field], workflow_results, dynamic_values
            )
            for field in self._extra_required
        ]

        expected_failure = self._is_expected_failure()

        try:
            rpc_url, client_node_name = self._resolve_node_for_client(node_name)
            client = get_client_for_rpc_url(rpc_url, node_name=client_node_name)
            if not hasattr(client, self._api_method):
                msg = (
                    f"client.{self._api_method} not found — requires "
                    f"calimero-client-py >= {_REQUIRED_CLIENT_VERSION} (got an older version)"
                )
                if expected_failure:
                    self._report_expected_failure(msg)
                    return True
                console.print(f"[red]{msg}[/red]")
                return False
            method = getattr(client, self._api_method)
            api_result = method(group_id, *extra_args)
            result = ok(api_result)
        except Exception as e:
            result = fail(f"{self._api_method} failed", error=e)

        if not result["success"]:
            if expected_failure:
                self._report_expected_failure(str(result.get("error", "Unknown error")))
                return True
            console.print(
                f"[red]{self._api_method} failed on {node_name}: {result.get('error')}[/red]"
            )
            return False
        if self._check_jsonrpc_error(result["data"]):
            if expected_failure:
                self._report_expected_failure("JSON-RPC error returned")
                return True
            return False

        workflow_results[f"{self._result_key_prefix}_{node_name}"] = result["data"]
        self._export_variables(result["data"], node_name, dynamic_values)
        console.print(
            f"[green]✓ Got {self._what} for {self._log_target(group_id, extra_args)} "
            f"on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
        return True


# =============================================================================
# Group metadata
# =============================================================================


class SetGroupMetadataStep(_SetMetadataBase):
    """Set the metadata record on a group via the admin API."""

    _api_method = "set_group_metadata"
    _extra_required = ()
    _result_key_prefix = "set_group_metadata"
    _what = "group metadata"


class GetGroupMetadataStep(_GetMetadataBase):
    """Get the metadata record of a group via the admin API."""

    _api_method = "get_group_metadata"
    _extra_required = ()
    _result_key_prefix = "get_group_metadata"
    _export_name = "group_metadata"
    _what = "group metadata"


# =============================================================================
# Member metadata
# =============================================================================


class SetMemberMetadataStep(_SetMetadataBase):
    """Set the metadata record on a group member via the admin API."""

    _api_method = "set_member_metadata"
    _extra_required = ("member_id",)
    _result_key_prefix = "set_member_metadata"
    _what = "member metadata"


class GetMemberMetadataStep(_GetMetadataBase):
    """Get the metadata record of a group member via the admin API."""

    _api_method = "get_member_metadata"
    _extra_required = ("member_id",)
    _result_key_prefix = "get_member_metadata"
    _export_name = "member_metadata"
    _what = "member metadata"


# =============================================================================
# Context metadata
# =============================================================================


class SetContextMetadataStep(_SetMetadataBase):
    """Set the metadata record on a group-registered context via the admin API."""

    _api_method = "set_context_metadata"
    _extra_required = ("context_id",)
    _result_key_prefix = "set_context_metadata"
    _what = "context metadata"


class GetContextMetadataStep(_GetMetadataBase):
    """Get the metadata record of a group-registered context via the admin API."""

    _api_method = "get_context_metadata"
    _extra_required = ("context_id",)
    _result_key_prefix = "get_context_metadata"
    _export_name = "context_metadata"
    _what = "context metadata"
