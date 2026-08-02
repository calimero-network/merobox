"""
Account-identity workflow step executors: enrol, pair, revoke, inspect.

These replace the shell scripts core's e2e suite was driving through
`type: script`. The scripts worked, but a `script` step cannot export custom
`outputs:` — it only sets `script_output_local`, which the next script step
overwrites — so every value one script minted for the next (device ids, the
account genesis halves) had to be written to a temp file keyed by namespace.
That made the scenarios read as a sequence of side effects rather than a data
flow, and it put the interesting assertions inside shell rather than in
`json_assert` where a reader can see them.

These go through `calimero-client-py` like every other step, not raw HTTP. An
earlier draft called `admin-api/` directly on the grounds that the client had no
account methods — backwards twice over: the fix for a missing binding is to add
it, and core's Rust client already wrapped all five endpoints (meroctl's `account`
subcommands drive them), so only the Python bindings were missing. They landed in
calimero-client-py 0.6.20. Going through the client keeps the token cache, the
error mapping and the connection handling this layer exists to provide.
"""

from typing import Any

from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.result import fail, ok
from merobox.commands.utils import console


class _AccountStepBase(BaseStep):
    """Shared client plumbing for the account steps."""

    def _client(self, node_name: str):
        """A client bound to `node_name`, with its cached token attached."""
        rpc_url, client_node_name = self._resolve_node_for_client(node_name)
        return get_client_for_rpc_url(rpc_url, node_name=client_node_name)

    @staticmethod
    def _data(response: Any) -> dict[str, Any]:
        """The payload, whether or not the response wraps it in `data`.

        The api types are `{ data: { … } }`; unwrapping here keeps the exported
        variable names matching the field names an operator sees in the docs.
        """
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response if isinstance(response, dict) else {}

    def _require_strings(self, fields: tuple[str, ...]) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in fields:
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")

    def _resolved(self, key: str, dynamic_values: dict[str, Any]) -> str:
        """Resolve a placeholder-bearing field to a plain string."""
        return str(self._resolve_dynamic_value(self.config[key], {}, dynamic_values))

    def _finish(
        self,
        node_name: str,
        result_key: str,
        data: dict[str, Any],
        workflow_results: dict[str, Any],
    ) -> bool:
        if self._check_jsonrpc_error(data):
            return False
        workflow_results[f"{result_key}_{node_name}"] = data
        return True


class AccountCreateStep(_AccountStepBase):
    """Enrol a device for a fresh account in a namespace.

    The first thing in the account plane that publishes an op, so it is also the
    first evidence that `AccountDeviceLinked` survives the real wire. Must run
    AFTER the node holds the namespace scope key: a device link travels as an
    *encrypted* group op, so a node that has not joined yet cannot publish one.

    Exports `accountId`, `deviceId`, and the genesis halves (`accountRootKey`,
    `accountNonce`) a second device needs to mint its own id — pass those to
    `account_pair`.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "namespace_id"))

    def _get_exportable_variables(self):
        return [
            ("accountId", "account_id_{node_name}", "The account this node enrolled"),
            ("deviceId", "device_id_{node_name}", "The device id it minted"),
            (
                "accountRootKey",
                "account_root_key_{node_name}",
                "Epoch-0 root key of the account's genesis (public data)",
            ),
            (
                "accountNonce",
                "account_nonce_{node_name}",
                "Genesis nonce, needed to mint a paired device's id",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        namespace_id = self._resolved("namespace_id", dynamic_values)
        try:
            client = self._client(node_name)
            result = ok(self._data(client.create_account(namespace_id)))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("account create failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to enrol an account on {node_name}: "
                f"{result.get('error')}[/red]"
            )
            return False

        data = result["data"]
        if not data.get("accountId"):
            console.print(f"[red]account create returned no accountId: {data}[/red]")
            return False
        console.print(
            f"[green]✓[/green] {node_name} enrolled account {data['accountId']} "
            f"with device {data.get('deviceId')}"
        )
        return self._finish(node_name, "account", data, workflow_results)


class AccountPairStep(_AccountStepBase):
    """Pair a second node onto an account that already exists elsewhere.

    Both halves of the exchange in one step, because the ordering between them is
    forced rather than a choice: the new device cannot mint its `DeviceId` until
    it knows the account (the id is `H(account ‖ nonce)`), and the holder cannot
    certify that device until it knows the id and both of its keys. So this runs
    `pair-init` on the NEW node, then hands what it minted — including the signed
    statement and the confirmation code — to `pair-complete` on the HOLDER.

    Modelling merobox as the operator in the middle is the point: it is the
    channel a human would be, and passing the confirmation code through is what
    a human comparing it out loud would do.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "holder", "namespace_id", "root_key", "nonce"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "holder", "namespace_id", "root_key", "nonce"))

    def _get_exportable_variables(self):
        return [
            (
                "accountId",
                "paired_account_id_{node_name}",
                "The account the new device now speaks for",
            ),
            (
                "deviceId",
                "paired_device_id_{node_name}",
                "The device id the new node minted",
            ),
            (
                "keyDelivered",
                "paired_key_delivered_{node_name}",
                "Whether the holder wrapped the current scope key for it",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        holder = self._resolved("holder", dynamic_values)
        namespace_id = self._resolved("namespace_id", dynamic_values)
        root_key = self._resolved("root_key", dynamic_values)
        nonce = self._resolved("nonce", dynamic_values)

        try:
            init = self._data(
                self._client(node_name).pair_device_init(namespace_id, root_key, nonce)
            )
            missing = [
                field
                for field in (
                    "deviceId",
                    "kemPublicKey",
                    "signPublicKey",
                    "statement",
                    "confirmationCode",
                )
                if not init.get(field)
            ]
            if missing:
                raise RuntimeError(f"pair-init omitted {', '.join(missing)}: {init}")

            complete = self._data(
                self._client(holder).pair_device_complete(
                    namespace_id,
                    init["deviceId"],
                    init["kemPublicKey"],
                    init["signPublicKey"],
                    init["statement"],
                    init["confirmationCode"],
                )
            )
            # The check a human is supposed to make. Both sides derive it over
            # exactly what gets certified, so a mismatch means the payload was
            # altered in transit and the device must not be trusted — asserting it
            # here keeps the scenario honest about what pairing actually promises.
            if complete.get("confirmationCode") != init["confirmationCode"]:
                raise RuntimeError(
                    "confirmation codes differ between pair-init and pair-complete "
                    f"({init['confirmationCode']} vs {complete.get('confirmationCode')})"
                    " — the payload did not arrive as it was minted"
                )
            result = ok({**complete, "deviceId": init["deviceId"]})
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("account pair failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to pair {node_name} onto the account held by "
                f"{holder}: {result.get('error')}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} paired onto account "
            f"{data.get('accountId')} as device {data.get('deviceId')} "
            f"(key delivered: {data.get('keyDelivered')})"
        )
        return self._finish(node_name, "paired_account", data, workflow_results)


class AccountRevokeStep(_AccountStepBase):
    """Withdraw a device from an account, rotating the scope key.

    Run on a node with the authority to do it (an admin, or the account itself).
    Exports `keyRotated` so a scenario can assert the rotation happened rather
    than inferring it from a later read.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id", "device_id"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "namespace_id", "device_id"))

    def _get_exportable_variables(self):
        return [
            (
                "keyRotated",
                "revoke_key_rotated_{node_name}",
                "Whether the revocation rotated the scope key",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        namespace_id = self._resolved("namespace_id", dynamic_values)
        device_id = self._resolved("device_id", dynamic_values)

        try:
            client = self._client(node_name)
            result = ok(self._data(client.revoke_device(namespace_id, device_id)))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("account revoke failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to revoke {device_id} via {node_name}: "
                f"{result.get('error')}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} revoked device {device_id} "
            f"(key rotated: {data.get('keyRotated')})"
        )
        return self._finish(node_name, "revoke", data, workflow_results)


class AccountShowStep(_AccountStepBase):
    """Report which account a node speaks for in a namespace, and its device.

    A read, so it mints nothing: the account id is derived from the node's root
    and the namespace, and exists whether or not a device has been enrolled. A
    `deviceId` of `null` is a real answer — "this node holds no device here" —
    which is what makes it usable to assert that a revocation stuck.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "namespace_id"))

    def _get_exportable_variables(self):
        return [
            ("accountId", "shown_account_id_{node_name}", "Account this node owns"),
            (
                "deviceId",
                "shown_device_id_{node_name}",
                "Device it holds there, or null if none",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        namespace_id = self._resolved("namespace_id", dynamic_values)

        try:
            client = self._client(node_name)
            result = ok(self._data(client.get_namespace_account(namespace_id)))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("account show failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to read {node_name}'s account: "
                f"{result.get('error')}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} owns account {data.get('accountId')} "
            f"(device: {data.get('deviceId')})"
        )
        return self._finish(node_name, "shown_account", data, workflow_results)
