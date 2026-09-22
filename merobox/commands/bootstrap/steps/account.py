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

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import requests
from rich.markup import escape

from merobox.commands.bootstrap.steps import body_assert
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.client import get_client_for_rpc_url
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from merobox.commands.result import fail, ok
from merobox.commands.utils import console

_CLIENT_ERROR_PREFIX = "Client error: "  # calimero-client-py wraps every failed call
_HTTP_STATUS = re.compile(r"HTTP (\d{3})\b")  # what it puts first for a non-2xx answer
_AUTO = "auto"  # `account_namespace: auto` reads the id off the holder's identity
_AWAIT_SELF_READS = 45  # listings await_self reads, as core's account scenarios wait
_AWAIT_SELF_INTERVAL = 2.0  # seconds between those reads
_SCOPE_ALL = "all"  # `account_rescope: scope` for every application
_OFFER_KEYS = {  # account_pair_complete field -> the pair-init answer it carries
    "device_id": "deviceId",
    "kem_public_key": "kemPublicKey",
    "sign_public_key": "signPublicKey",
    "statement": "statement",
    "confirmation_code": "confirmationCode",
}


class _AccountStepBase(BaseStep):
    """Shared client plumbing for the account steps."""

    # A refusal or an absence asserted against a placeholder's own text passes.
    strict_placeholders = True

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

    def _require_string_lists(self, fields: tuple[str, ...]) -> None:
        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        for field in fields:
            value = self.config.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValueError(
                    f"Step '{step_name}': '{field}' must be a list of strings"
                )

    def _resolved_list(self, key: str, dynamic_values: dict[str, Any]) -> list[str]:
        """Resolve each entry of a list field. Absent is an empty list."""
        return [
            str(self._resolve_dynamic_value(item, {}, dynamic_values))
            for item in self.config.get(key, [])
        ]

    def _resolved_args(self, dynamic_values: dict[str, Any]) -> str:
        """The step's `args:` mapping as the JSON string the client takes.

        A JSON *string* rather than a dict because that is `calimero-client-py`'s
        signature, and because the warrant commits to `H(method, args)`: both this
        side and the node parse and re-serialize, so the bytes agree regardless of
        how a scenario spelled the mapping. Passing the text through untouched
        would make a re-indented but identical `args:` mint a warrant that
        verifies nowhere.
        """
        args = self.config.get("args", {})
        resolved = self._resolve_args_recursively(args, dynamic_values)
        return json.dumps(resolved)

    def _resolve_args_recursively(self, value: Any, dynamic_values: dict[str, Any]):
        """Resolve `{{placeholders}}` anywhere inside a nested args structure."""
        if isinstance(value, dict):
            return {
                key: self._resolve_args_recursively(item, dynamic_values)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_args_recursively(item, dynamic_values) for item in value
            ]
        if isinstance(value, str):
            return self._resolve_dynamic_value(value, {}, dynamic_values)
        return value

    def _require_args_mapping(self) -> None:
        """`args:` is optional, but when present it must be a mapping.

        Checked here rather than left to the client: a list or a bare string would
        reach `sign_warrant` as valid JSON of the wrong shape, mint a warrant
        committing to it, and be refused by the node as an intent mismatch — a
        long way from the line that caused it.
        """
        if "args" in self.config and not isinstance(self.config["args"], dict):
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            raise ValueError(f"Step '{step_name}': 'args' must be a dictionary")

    def _expect_status(self) -> int | None:
        """`expect_status:` as an int, or None when the step asserts no refusal.

        Validated here rather than at use, so a bad value stops the run before
        any node is contacted.
        """
        value = self.config.get("expect_status")
        if value is None:
            return None
        # bool is an int in Python, so `expect_status: true` would pass as 1.
        if isinstance(value, bool) or not isinstance(value, int):
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            raise ValueError(
                f"Step '{step_name}': 'expect_status' must be an integer HTTP status"
            )
        return value

    @staticmethod
    def _failure_status(result: dict[str, Any]) -> int | None:
        """The status out of `Client error: HTTP <status>: <body>`, else None.

        Anchored on that shape rather than searched for, so a refusal quoting a
        status in its body cannot stand in for the one the node actually sent.
        """
        exception = result.get("exception")
        message = exception.get("message") if isinstance(exception, dict) else None
        if not isinstance(message, str):
            return None
        match = _HTTP_STATUS.match(message.removeprefix(_CLIENT_ERROR_PREFIX))
        return int(match.group(1)) if match else None

    def _report_expect_status(self, expected: int, result: dict[str, Any]) -> bool:
        """Pass only if the call was refused with exactly `expected`.

        A status merobox cannot read fails closed: typed refusals exist so that a
        500, or an unreachable node, cannot satisfy the assertion under test.
        """
        detail = self._failure_detail(result)
        actual = self._failure_status(result)
        if actual != expected:
            got = f"HTTP {actual}" if actual is not None else "no recoverable status"
            # markup=False so an error body containing brackets survives Rich.
            console.print(
                f"✗ expected the call to fail with HTTP {expected}, "
                f"but it failed with {got}: {detail}",
                style="red",
                markup=False,
                highlight=False,
            )
            return False
        console.print(
            f"[yellow]✓ Refused with HTTP {expected} as expected: "
            f"{escape(detail)}[/yellow]"
        )
        return True

    def _report_unexpected_status_success(self, expected: int) -> bool:
        """A refusal test whose call succeeds has been disproven, so it fails."""
        console.print(
            f"[red]✗ expect_status: {expected} was set but the call succeeded[/red]"
        )
        return False

    def _refusal_verdict(self, result: dict[str, Any], failure: str) -> bool | None:
        """The verdict when the call failed or `expect_status` is set, else None."""
        expect_status = self._expect_status()
        if not result["success"]:
            if expect_status is not None:
                return self._report_expect_status(expect_status, result)
            console.print(f"[red]{failure}: {escape(str(result.get('error')))}[/red]")
            return False
        if expect_status is not None:
            return self._report_unexpected_status_success(expect_status)
        return None

    def _assert_body(
        self,
        node_name: str,
        data: dict[str, Any],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> bool:
        """Apply this step's `where`/`match` to what the read returned.

        Here rather than in a second `assert_api_response` on the same path, so a
        listing is fetched once and checked in place.
        """
        resolve = lambda value: self._resolve_dynamic_value(  # noqa: E731
            value, workflow_results, dynamic_values
        )
        where = self.config.get("where")
        selected = body_assert.select(data, where, resolve)
        if self.config.get("expect_no_match"):
            misses = body_assert.unexpected_match(selected, where)
        elif selected is body_assert.MISSING:
            console.print(
                f"[red]✗ {node_name}: no element matching "
                f"{where!r} in {json.dumps(data, sort_keys=True)}[/red]"
            )
            return False
        else:
            misses = body_assert.failures(selected, self.config, resolve)
        for miss in misses:
            console.print(f"[red]    {miss}[/red]")
        return not misses

    def _read_budget(self) -> tuple[int, float]:
        """Attempts and spacing. One attempt unless the scenario asks for more."""
        return int(self.config.get("retries") or 1), float(
            self.config.get("interval") or 1
        )

    def _finish(
        self,
        node_name: str,
        result_key: str,
        data: dict[str, Any],
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> bool:
        if self._check_jsonrpc_error(data):
            return False
        workflow_results[f"{result_key}_{node_name}"] = data
        # Recording the result is NOT the same as exporting it: `outputs:` only
        # does anything if the step calls this. Without it the placeholders a
        # scenario writes stay literal, and the failure surfaces wherever they are
        # consumed — a `{{root_key}}` reaching an api as a 12-character string —
        # rather than here.
        self._export_variables(data, node_name, dynamic_values)
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
        self._expect_status()

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
            result = fail(f"account create failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to enrol an account on {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        if not data.get("accountId"):
            console.print(f"[red]account create returned no accountId: {data}[/red]")
            return False
        console.print(
            f"[green]✓[/green] {node_name} enrolled account {data['accountId']} "
            f"with device {data.get('deviceId')}"
        )
        return self._finish(
            node_name, "account", data, workflow_results, dynamic_values
        )


class _PairStepBase(_AccountStepBase):
    """The two halves of pairing, shared by the composite and the split steps."""

    def _validate_init_fields(self) -> None:
        """Core refuses a pair-init naming neither namespace field; so does this."""
        if "namespaces" in self.config:
            self._require_string_lists(("namespaces",))
        if "account_namespace" in self.config:
            self._require_strings(("account_namespace",))
        elif not self.config.get("namespaces"):
            raise ValueError(
                f"Step '{self._get_step_name()}': needs 'account_namespace' or a "
                "non-empty 'namespaces' - the node refuses a pairing naming neither"
            )

    def _account_namespace(self, holder: str | None, dynamic_values: dict[str, Any]):
        """The configured account namespace, read off the holder for `auto`."""
        if "account_namespace" not in self.config:
            return None
        value = self._resolved("account_namespace", dynamic_values)
        if value != _AUTO:
            return value
        identity = self._data(self._client(holder).get_node_identity())
        if not identity.get("accountNamespaceId"):
            raise RuntimeError(f"{holder} names no account namespace: {identity}")
        return identity["accountNamespaceId"]

    def _pair_init(
        self,
        node_name: str,
        root_key: str,
        namespaces: list[str],
        account_namespace: str | None,
    ) -> dict[str, Any]:
        """Mint a device on `node_name`, failing on an offer missing any part."""
        init = self._data(
            self._client(node_name).pair_device_init(
                root_key, namespaces, account_namespace=account_namespace
            )
        )
        missing = [
            field
            for field in ("accountId", *_OFFER_KEYS.values())
            if not init.get(field)
        ]
        if missing:
            raise RuntimeError(f"pair-init omitted {', '.join(missing)}: {init}")
        return init

    def _pair_complete(
        self, holder: str, offer: dict[str, Any], applications: list[str]
    ) -> dict[str, Any]:
        """Certify `offer` on `holder`, failing unless that offer is what it certified."""
        complete = self._data(
            self._client(holder).pair_device_complete(
                *(offer[field] for field in _OFFER_KEYS.values()),
                applications or None,
            )
        )
        # The check a human is supposed to make. Both sides derive it over exactly
        # what gets certified, so a mismatch means the payload was altered in transit.
        if complete.get("confirmationCode") != offer["confirmationCode"]:
            raise RuntimeError(
                "confirmation codes differ between the offer and pair-complete "
                f"({offer['confirmationCode']} vs {complete.get('confirmationCode')})"
                " - the payload did not arrive as it was minted"
            )
        # Taking the offer's ids over complete's would hide a link to another
        # device or account.
        for field in ("accountId", "deviceId"):
            if field in offer and complete.get(field) != offer[field]:
                raise RuntimeError(
                    f"pair-complete certified a different {field} than the offer "
                    f"named ({offer[field]} vs {complete.get(field)}) - the device "
                    "now linked is not the one that asked"
                )
        return complete


class AccountPairStep(_PairStepBase):
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

    `account_namespace` makes the device follow the account's own namespace, from
    which it learns every project namespace on its own, so `namespaces` may then
    be empty. `await_self` returns only once the new device has folded what the
    holder wrote there.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "holder", "root_key"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "holder", "root_key"))
        self._validate_init_fields()
        if "applications" in self.config:
            self._require_string_lists(("applications",))
        expect_status = self._expect_status()
        if self.config.get("await_self"):
            if "account_namespace" not in self.config:
                raise ValueError(
                    f"Step '{self._get_step_name()}': 'await_self' needs "
                    "'account_namespace' - a device that does not follow it never "
                    "reads its own pairing back"
                )
            if expect_status is not None:
                raise ValueError(
                    f"Step '{self._get_step_name()}': 'await_self' cannot wait on "
                    "a pairing 'expect_status' says is refused"
                )

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

    async def _await_self(
        self,
        node_name: str,
        device_id: str,
        account_namespace: str,
        applications: list[str],
    ) -> bool:
        """Poll the new node's own listing until it reads back what was certified;
        a scope arrives only by the registry, so it proves the holder's op folded."""
        own_row = {"deviceId": device_id, "isSelf": True}
        seen: Any = None
        for attempt in range(_AWAIT_SELF_READS):
            if attempt:
                await asyncio.sleep(_AWAIT_SELF_INTERVAL)
            try:
                seen = self._data(self._client(node_name).list_account_devices())
            except Exception as e:  # noqa: BLE001 - the last one is reported below
                seen = e
                continue
            row = body_assert.select(seen, own_row, lambda value: value)
            if row is body_assert.MISSING:
                continue
            if applications:
                if set(row.get("applications") or []) == set(applications):
                    return True
            elif account_namespace in (row.get("namespaces") or []):
                return True
        console.print(
            f"✗ {node_name} did not read back its pairing within "
            f"{_AWAIT_SELF_READS} reads; last saw: {seen}",
            style="red",
            markup=False,
        )
        return False

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        holder = self._resolved("holder", dynamic_values)
        namespaces = self._resolved_list("namespaces", dynamic_values)
        root_key = self._resolved("root_key", dynamic_values)
        applications = self._resolved_list("applications", dynamic_values)

        # Outside the try: `expect_status` asserts the pairing, not this lookup.
        try:
            account_namespace = self._account_namespace(holder, dynamic_values)
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            console.print(
                f"[red]Could not read {holder}'s account namespace: "
                f"{escape(str(e))}[/red]"
            )
            return False

        try:
            init = self._pair_init(node_name, root_key, namespaces, account_namespace)
            complete = self._pair_complete(holder, init, applications)
            if account_namespace is not None:
                complete = {**complete, "accountNamespace": account_namespace}
            result = ok(complete)
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account pair failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to pair {node_name} onto the account held by {holder}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} paired onto account "
            f"{data.get('accountId')} as device {data.get('deviceId')} "
            f"(key delivered: {data.get('keyDelivered')})"
        )
        if self.config.get("await_self") and not await self._await_self(
            node_name, data["deviceId"], data["accountNamespace"], applications
        ):
            return False
        return self._finish(
            node_name, "paired_account", data, workflow_results, dynamic_values
        )


class AccountPairInitStep(_PairStepBase):
    """The new device's half of `account_pair`: mint a device, export the offer,
    so a scenario can hand the holder an offer with one field changed."""

    def _get_required_fields(self) -> list[str]:
        return ["node", "root_key"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "root_key"))
        self._validate_init_fields()
        if self.config.get("account_namespace") == _AUTO:
            raise ValueError(
                f"Step '{self._get_step_name()}': 'account_namespace: auto' needs a "
                "holder to read it from; pass the id from node_identity"
            )
        self._expect_status()

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        try:
            result = ok(
                self._pair_init(
                    node_name,
                    self._resolved("root_key", dynamic_values),
                    self._resolved_list("namespaces", dynamic_values),
                    self._account_namespace(None, dynamic_values),
                )
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account pair-init failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to mint a device on {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} minted device {data['deviceId']} for "
            f"account {data['accountId']}"
        )
        return self._finish(
            node_name, "pair_init", data, workflow_results, dynamic_values
        )


class AccountPairCompleteStep(_PairStepBase):
    """The holder's half of `account_pair`: certify an offer given field by field."""

    def _get_required_fields(self) -> list[str]:
        return ["node", *_OFFER_KEYS]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", *_OFFER_KEYS))
        if "applications" in self.config:
            self._require_string_lists(("applications",))
        self._expect_status()

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        offer = {
            field: self._resolved(key, dynamic_values)
            for key, field in _OFFER_KEYS.items()
        }
        try:
            result = ok(
                self._pair_complete(
                    node_name,
                    offer,
                    self._resolved_list("applications", dynamic_values),
                )
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account pair-complete failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"{node_name} did not certify device {offer['deviceId']}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} certified device {data.get('deviceId')} "
            f"(key delivered: {data.get('keyDelivered')})"
        )
        return self._finish(
            node_name, "pair_complete", data, workflow_results, dynamic_values
        )


class AccountRevokeStep(_AccountStepBase):
    """Withdraw a device from an account, rotating the scope key.

    Run on a node with the authority to do it — an admin, or the account itself.
    Exports `keyRotated` so a scenario can assert the rotation happened rather
    than inferring it from a later read.

    `proof:` supplies a revocation signed **elsewhere** (`merod account
    revoke-proof`), which is the lost-device case: the account root never reaches a
    node, and the node running this step needs no authority of its own — it only
    publishes. Without it, the node must be an admin or hold the account itself.

    Only an admin can rotate the scope key, so a proof-published revocation stops
    the device writing immediately and leaves it able to read until an admin
    rotates. `keyRotated` reports which happened rather than hiding the difference.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "namespace_id", "device_id"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "namespace_id", "device_id"))
        # Optional, so absence is fine — but a present non-string is a scenario
        # bug, and an empty string is one too: it would reach the node as "no
        # proof" while the author clearly meant to pass one.
        proof = self.config.get("proof")
        if proof is not None:
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            if not isinstance(proof, str):
                raise ValueError(f"Step '{step_name}': 'proof' must be a string")
            if not proof.strip():
                raise ValueError(
                    f"Step '{step_name}': 'proof' is empty — omit the field entirely "
                    "if this node revokes on its own authority"
                )
        self._expect_status()

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
        # Resolved through the same path as every other field, so `{{proof}}` from
        # a `node_exec` capture works without the scenario copying the hex inline.
        proof = (
            self._resolved("proof", dynamic_values)
            if self.config.get("proof") is not None
            else None
        )

        try:
            client = self._client(node_name)
            result = ok(
                self._data(client.revoke_device(namespace_id, device_id, proof))
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account revoke failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to revoke {device_id} via {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} revoked device {device_id} "
            f"(key rotated: {data.get('keyRotated')}"
            f"{', via supplied proof' if proof else ''})"
        )
        return self._finish(node_name, "revoke", data, workflow_results, dynamic_values)


class AccountRelinkStep(_AccountStepBase):
    """Repair or widen a device this account already certified.

    Re-runs pairing's fan-out against the namespaces this node takes part in
    now, so a namespace gained after pairing binds the device without a second
    ceremony. Naming no application repairs WITHOUT widening: unlike
    `account_pair`, an empty list here is not "every application".
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "device_id"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "device_id"))
        if "applications" in self.config:
            self._require_string_lists(("applications",))
        self._expect_status()

    def _get_exportable_variables(self):
        return [
            (
                "applications",
                "relink_applications_{node_name}",
                "The device's scope after the repair",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        device_id = self._resolved("device_id", dynamic_values)
        applications = self._resolved_list("applications", dynamic_values)

        try:
            client = self._client(node_name)
            result = ok(
                self._data(client.relink_device(device_id, applications or None))
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account relink failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to relink {device_id} via {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        outcomes = data.get("outcomes") or []
        console.print(
            f"[green]✓[/green] {node_name} relinked device {device_id} "
            f"across {len(outcomes)} namespace(s)"
        )
        return self._finish(node_name, "relink", data, workflow_results, dynamic_values)


class AccountRescopeStep(_AccountStepBase):
    """Replace the applications a device this account certified may speak in.

    Where `account_relink` only ever widens, this REPLACES: `only: [app]` drops
    every other application the device held. Driven over the admin API rather
    than a binding, because calimero-client-py has none for this route yet.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "device_id", "scope"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "device_id"))
        self._validate_scope()
        self._expect_status()

    def _validate_scope(self) -> None:
        """`all`, or `only:` naming at least one application.

        An empty `only` is refused here rather than sent: on the wire it reads
        as every application, the opposite of what such a scenario asked for.
        """
        scope = self.config["scope"]
        if scope == _SCOPE_ALL:
            return
        if (
            not isinstance(scope, dict)
            or set(scope) != {"only"}
            or not isinstance(scope.get("only"), list)
            or not scope["only"]
            or not all(isinstance(app, str) for app in scope["only"])
        ):
            raise ValueError(
                f"Step '{self._get_step_name()}': 'scope' must be '{_SCOPE_ALL}' or "
                "a mapping 'only:' naming at least one application"
            )

    def _get_exportable_variables(self):
        return [
            (
                "applications",
                "rescope_applications_{node_name}",
                "The device's scope after the replacement",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        device_id = self._resolved("device_id", dynamic_values)
        scope = self._resolve_args_recursively(self.config["scope"], dynamic_values)

        try:
            rpc_url, cache_node_name = self._resolve_node_target(node_name)
            token = self._resolve_token(
                cache_node_name, workflow_results, dynamic_values
            )
            # Off the loop: `requests` is synchronous, so a `parallel:` sibling
            # keeps running while this one waits.
            response = await asyncio.to_thread(
                requests.put,
                f"{rpc_url.rstrip('/')}/admin-api/account/devices/{device_id}/scope",
                json={"scope": scope},
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=(DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
            if response.status_code != 200:
                # Raised, and shaped as a client error, so a refusal reaches
                # `expect_status` by the same path the bindings' refusals do.
                raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
            result = ok(self._data(json.loads(response.content)))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"account rescope failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to rescope {device_id} via {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} rescoped device {device_id} to "
            f"{'every application' if scope == _SCOPE_ALL else scope['only']}"
        )
        return self._finish(
            node_name, "rescope", data, workflow_results, dynamic_values
        )


class AccountDevicesStep(_AccountStepBase):
    """List every device of this node's account.

    Joined from the node-local certificate cache and the live bindings of every
    namespace this node takes part in, so it reports devices this node never
    certified as well as the ones it did.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node",))
        body_assert.validate(self.config, self._get_step_name())

    def _get_exportable_variables(self):
        return [
            (
                "devices",
                "account_devices_{node_name}",
                "Every device of this account, with scope and bindings",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)

        attempts, interval = self._read_budget()
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                result = ok(self._data(self._client(node_name).list_account_devices()))
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                result = fail(f"account devices failed: {e}", error=e)
            if result["success"] and self._assert_body(
                node_name, result["data"], workflow_results, dynamic_values
            ):
                break
            if last:
                if result["success"]:
                    console.print(
                        f"[red]✗ {node_name}'s devices did not satisfy this step[/red]"
                    )
                    return False
                break
            await asyncio.sleep(interval)

        if not result["success"]:
            console.print(
                f"[red]Failed to list devices on {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} reports "
            f"{len(data.get('devices') or [])} device(s) on its account"
        )
        return self._finish(
            node_name, "devices", data, workflow_results, dynamic_values
        )


class AccountApplicationsStep(_AccountStepBase):
    """List the applications this node's account speaks in.

    The only route by which a paired device can learn them: it is a member of
    nothing, and a namespace summary is withheld from non-members.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node",))
        body_assert.validate(self.config, self._get_step_name())

    def _get_exportable_variables(self):
        return [
            (
                "applications",
                "account_applications_{node_name}",
                "The applications this account speaks in",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)

        attempts, interval = self._read_budget()
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                result = ok(
                    self._data(self._client(node_name).list_account_applications())
                )
            except Exception as e:  # noqa: BLE001 - reported, not swallowed
                result = fail(f"account applications failed: {e}", error=e)
            if result["success"] and self._assert_body(
                node_name, result["data"], workflow_results, dynamic_values
            ):
                break
            if last:
                if result["success"]:
                    console.print(
                        f"[red]✗ {node_name}'s applications did not satisfy this step[/red]"
                    )
                    return False
                break
            await asyncio.sleep(interval)

        if not result["success"]:
            console.print(
                f"[red]Failed to list applications on {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} speaks in "
            f"{len(data.get('applications') or [])} application(s)"
        )
        return self._finish(
            node_name, "applications", data, workflow_results, dynamic_values
        )


class NodeIdentityStep(_AccountStepBase):
    """Report who a node is — account, device, signing key, account root.

    A read; it mints nothing. Enrolment is implicit on every join path, so by
    the time a node has joined anything it already has an account and a device,
    and this reports them.

    Takes NO namespace, because none of what it reports varies by one: a node
    has one root key, therefore one account, and one device per installation.
    It replaces `account_show`, which asked per namespace and could not answer
    `accountRootPublicKey` at all.

    Why this exists rather than reusing `account_create`: scenarios were calling
    that step purely for its outputs, after the join had already enrolled them —
    a mutation used as a getter, because there was no getter. `deviceId` and the
    account root had no other source.

    Requires calimero-client-py >= 0.6.27 (the `get_node_identity` binding) and
    a node exposing `GET /admin-api/identity`.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node",))

    def _get_exportable_variables(self):
        return [
            (
                "accountId",
                "identity_account_id_{node_name}",
                "Account this node writes as",
            ),
            (
                "deviceId",
                "identity_device_id_{node_name}",
                "This node's device — its replica id within the account",
            ),
            (
                "publicKey",
                "identity_public_key_{node_name}",
                "The DEVICE's signing key, which is what op signatures verify against",
            ),
            (
                "accountRootPublicKey",
                "identity_account_root_{node_name}",
                "Public half of the account root — what a second device pairs against",
            ),
            (
                "deviceAgreementKey",
                "identity_agreement_key_{node_name}",
                "The device's X25519 key, the third input `merod account sign-cert` "
                "needs alongside the device id and the signing key",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)

        try:
            client = self._client(node_name)
            result = ok(self._data(client.get_node_identity()))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail("node identity read failed", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to read {node_name}'s identity: "
                f"{result.get('error')}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} is account {data.get('accountId')} "
            f"(device: {data.get('deviceId')})"
        )
        return self._finish(
            node_name, "identity", data, workflow_results, dynamic_values
        )


class SignWarrantStep(_AccountStepBase):
    """Mint a warrant: a member's signed consent for one delegated write.

    Delegated authorship exists for a holder that has **no node** — a device with
    only a signing key, which can neither run the application (the runtime is a
    JIT) nor decrypt the state (it never received a scope key). What makes such a
    member the author of its own writes is a warrant it signs, which travels with
    the change so every peer can check it consented.

    This step contacts nothing. It signs with `device_secret` and returns bytes,
    and that is the whole point rather than a convenience: a node that held the
    signing key could forge writes in the member's name, so the key must never
    reach the node that runs the request. No `node:` field, for the same reason —
    there is nothing for one to do here.

    **The key material comes from the scenario, deliberately.** merobox cannot
    mint it: `account_pair` binds a device to a *node* and never hands the secret
    out, which is correct. A scenario supplies either a fixed test credential (a
    fixture, as core's own delegated-authorship scenario does) or one minted
    out-of-band by `merod account sign-cert`. Providing the step without
    providing the keys is the right split — merobox is the channel, not the
    holder.

    Note the encodings, which are core's and are not interchangeable:
    `context_id` is base58 and `executor` is hex. The author's account is read
    out of `credential` rather than configured, because a scenario that states it
    separately is one that can state it inconsistently.

    Requires calimero-client-py with the `sign_warrant` binding, and core with
    the warrant types.
    """

    def _get_required_fields(self) -> list[str]:
        return ["context_id", "executor", "method", "device_secret", "credential"]

    def _validate_field_types(self) -> None:
        self._require_strings(
            ("context_id", "executor", "method", "device_secret", "credential")
        )
        self._require_args_mapping()

    def _get_exportable_variables(self):
        return [
            (
                "warrant",
                "warrant_{node_name}",
                "Hex-encoded warrant, ready for a perform_intent step",
            ),
            (
                "authorAccount",
                "warrant_author_account_{node_name}",
                "Account the write will be attributed to — add it as a member first",
            ),
            (
                "authorDeviceKey",
                "warrant_author_device_{node_name}",
                "The author's device key: the CRDT replica the change lands under",
            ),
            (
                "intentHash",
                "warrant_intent_hash_{node_name}",
                "H(method, args) — what the signature commits to, not the plaintext",
            ),
            (
                "notAfter",
                "warrant_not_after_{node_name}",
                "Unix seconds after which a relay refuses to spend it",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        context_id = self._resolved("context_id", dynamic_values)
        executor = self._resolved("executor", dynamic_values)
        method = self._resolved("method", dynamic_values)
        device_secret = self._resolved("device_secret", dynamic_values)
        credential = self._resolved("credential", dynamic_values)
        args = self._resolved_args(dynamic_values)
        nonce = int(self.config.get("nonce", 1))
        valid_for = int(self.config.get("valid_for", 300))

        try:
            from calimero_client_py import sign_warrant

            data = self._data(
                sign_warrant(
                    context_id=context_id,
                    executor=executor,
                    method=method,
                    args=args,
                    nonce=nonce,
                    device_secret=device_secret,
                    credential=credential,
                    valid_for=valid_for,
                )
            )
            result = ok(data)
        except ImportError as e:
            result = fail(
                "this calimero-client-py has no sign_warrant binding; "
                f"upgrade it to mint warrants: {e}",
                error=e,
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"signing the warrant failed: {e}", error=e)

        # Minting refuses too — a credential that certifies a different key than
        # `device_secret` holds, most usefully — and that refusal is worth
        # asserting rather than only surviving.
        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            console.print(
                f"[red]Failed to sign a warrant for {method} in {context_id}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        if expected_failure:
            return self._report_unexpected_success()

        data = result["data"]
        console.print(
            f"[green]✓[/green] warrant signed for {method} in {context_id} "
            f"by {data.get('authorAccount')} (nonce {nonce}), "
            f"spendable by {executor}"
        )
        # Keyed on the executor rather than a node: this step has no node, and
        # two warrants in one scenario are told apart by who may spend them.
        return self._finish(
            executor, "signed_warrant", data, workflow_results, dynamic_values
        )


class PerformIntentStep(_AccountStepBase):
    """Have a node run one method on a member's behalf, under their warrant.

    The relay executes and signs the envelope with its own key; the change is
    attributed to the **author**. Both halves are on the wire, so every peer
    re-checks that the member consented rather than taking the relay's word.

    Only the author's half is sent — the warrant and the proof its signing key is
    a device of the account it names. The node attaches its own credential, so a
    scenario never has to learn which of the node's processes runs the intent,
    and the node re-keying does not void a warrant already minted.

    Two things a scenario has to get right first, because neither is implied:

    * the author's **account** must be a member of the group owning the context
      (`add_group_members` takes an account, and the author's device joins
      nothing — it is in no group's binding rows and never will be);
    * the relay must hold `CAN_AUTHOR_ON_BEHALF` on that group. It is not implied
      by membership and not implied by admin, and without it this is refused
      before anything executes.

    A warrant is single-use. Presenting a spent one is refused, which is the
    point of the nonce ledger: the signature stays valid forever, so replay is
    not forgery and the envelope check cannot be what stops it.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "method", "warrant", "author_proof"]

    def _validate_field_types(self) -> None:
        self._require_strings(
            ("node", "context_id", "method", "warrant", "author_proof")
        )
        self._require_args_mapping()

    def _get_exportable_variables(self):
        return [
            (
                "rootHash",
                "intent_root_hash_{node_name}",
                "The context's scope root after the run — how a scenario sees it wrote",
            ),
            (
                "returns",
                "intent_returns_{node_name}",
                "The method's own return value",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        context_id = self._resolved("context_id", dynamic_values)
        method = self._resolved("method", dynamic_values)
        warrant = self._resolved("warrant", dynamic_values)
        author_proof = self._resolved("author_proof", dynamic_values)
        args = self._resolved_args(dynamic_values)

        try:
            data = self._data(
                self._client(node_name).perform_intent(
                    context_id, method, args, warrant, author_proof
                )
            )
            result = ok(data)
        except AttributeError as e:
            result = fail(
                "this calimero-client-py has no perform_intent binding; "
                f"upgrade it to run delegated intents: {e}",
                error=e,
            )
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"performing the intent failed: {e}", error=e)

        # A refusal is a first-class outcome here, not just an error. The three
        # things this endpoint refuses — a relay holding no authorship grant, a
        # warrant that does not cover the intent, and a warrant already spent —
        # are each worth asserting positively, and a scenario that can only
        # assert acceptance cannot show that the grant is load-bearing.
        #
        # Pair it with `expected_error` in anything that matters: without one,
        # an unreachable node satisfies the same assertion as the refusal under
        # test.
        expected_failure = self._is_expected_failure()

        if not result["success"]:
            if expected_failure:
                return self._report_expected_failure(self._failure_detail(result))
            console.print(
                f"[red]{node_name} could not perform {method} in {context_id}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        if expected_failure:
            return self._report_unexpected_success()

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} performed {method} in {context_id} "
            f"on a member's behalf (root {data.get('rootHash')})"
        )
        return self._finish(
            node_name, "performed_intent", data, workflow_results, dynamic_values
        )


class WarrantNonceStep(_AccountStepBase):
    """Read where an author device stands in its warrant-nonce sequence.

    `GET /admin-api/contexts/{id}/warrant-nonce/{author_device_key}`. The nonce
    ledger is what makes a warrant single-use: the signature stays valid forever,
    so a replay is not a forgery and the envelope check cannot be what stops it.

    The key is the device's **signing key** — not its account and not its
    `DeviceId`. Two devices of one account hold independent sequences, and a
    re-keyed device starts a fresh one. `sign_warrant` exports it as
    `authorDeviceKey` for exactly this read.

    `seen: false` is the ordinary state of a device that has not written in this
    context yet, reported as a fact rather than as a 404 — "nonce 0 was spent"
    and "nothing has been spent" are different things a scenario must not have to
    guess apart. Asserting it advances is the cheapest way to show a delegated
    write was really *admitted* rather than merely answered 2xx: an intent that
    produces no state change spends no nonce, because the spend sits inside the
    causal-delta branch of core's execute handler, after the delta row is
    persisted. So two identical writes leave this reading unchanged, and a
    scenario that re-writes the same value and then asserts progress here is
    asserting something false.

    Unlike the intent pair next door, this route is on merod's **protected**
    router, so a token is attached where the scenario has one.

    **Availability.** The route is newer than the two intent endpoints it sits
    beside; a merod that predates it answers 404 with no way to tell that apart
    from a bad context at the status alone. `optional: true` downgrades a 404 to
    a warning so a scenario can run against both builds — use it, or pin an image
    known to carry the route.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "context_id", "author_device_key"]

    def _validate_field_types(self) -> None:
        self._require_strings(("node", "context_id", "author_device_key"))
        if "optional" in self.config and not isinstance(
            self.config.get("optional"), bool
        ):
            step_name = self.config.get(
                "name", f'Unnamed {self.config.get("type", "Unknown")} step'
            )
            raise ValueError(f"Step '{step_name}': 'optional' must be a boolean")

    def _get_exportable_variables(self):
        return [
            (
                "seen",
                "warrant_nonce_seen_{node_name}",
                "Whether any warrant from this device has been admitted here",
            ),
            (
                "highWaterNonce",
                "warrant_nonce_high_{node_name}",
                "Highest nonce accepted from this device in this context",
            ),
            (
                "nextNonce",
                "warrant_nonce_next_{node_name}",
                "The nonce to put in the next warrant — the field to act on",
            ),
        ]

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self._resolved("node", dynamic_values)
        context_id = self._resolved("context_id", dynamic_values)
        device_key = self._resolved("author_device_key", dynamic_values)

        try:
            rpc_url, cache_node_name = self._resolve_node_target(node_name)
            token = self._resolve_token(
                cache_node_name, workflow_results, dynamic_values
            )
            # Off the loop, as the other `requests` step here: it is synchronous,
            # so a `parallel:` sibling keeps running while this one waits.
            response = await asyncio.to_thread(
                requests.get,
                f"{rpc_url.rstrip('/')}/admin-api/contexts/{context_id}"
                f"/warrant-nonce/{device_key}",
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=(DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT),
            )
            if response.status_code == 404 and self.config.get("optional"):
                console.print(
                    f"[yellow]⚠ {node_name} has no warrant-nonce route (404); "
                    "this merod predates it. Skipping, because optional: true — "
                    "nothing about the ledger has been checked.[/yellow]"
                )
                return True
            if response.status_code != 200:
                # Shaped as a client error so a refusal reaches `expect_status`
                # by the same path the bindings' refusals do.
                raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
            result = ok(self._data(json.loads(response.content)))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            result = fail(f"warrant nonce read failed: {e}", error=e)

        verdict = self._refusal_verdict(
            result, f"Failed to read {device_key}'s warrant nonce on {node_name}"
        )
        if verdict is not None:
            return verdict

        data = result["data"]
        if not self._assert_body(node_name, data, workflow_results, dynamic_values):
            return False
        console.print(
            f"[green]✓[/green] {node_name}: device {device_key} is "
            + (
                f"at nonce {data.get('highWaterNonce')}, next "
                f"{data.get('nextNonce')}"
                if data.get("seen")
                else "unseen in this context"
            )
        )
        return self._finish(
            node_name, "warrant_nonce", data, workflow_results, dynamic_values
        )
