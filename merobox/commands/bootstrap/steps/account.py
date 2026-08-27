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

import json
from typing import Any

from rich.markup import escape

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

        if not result["success"]:
            console.print(
                f"[red]Failed to enrol an account on {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
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
        return self._finish(
            node_name, "account", data, workflow_results, dynamic_values
        )


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
            result = fail(f"account pair failed: {e}", error=e)

        if not result["success"]:
            console.print(
                f"[red]Failed to pair {node_name} onto the account held by "
                f"{holder}: {escape(str(result.get('error')))}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} paired onto account "
            f"{data.get('accountId')} as device {data.get('deviceId')} "
            f"(key delivered: {data.get('keyDelivered')})"
        )
        return self._finish(
            node_name, "paired_account", data, workflow_results, dynamic_values
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

        if not result["success"]:
            console.print(
                f"[red]Failed to revoke {device_id} via {node_name}: "
                f"{escape(str(result.get('error')))}[/red]"
            )
            return False

        data = result["data"]
        console.print(
            f"[green]✓[/green] {node_name} revoked device {device_id} "
            f"(key rotated: {data.get('keyRotated')}"
            f"{', via supplied proof' if proof else ''})"
        )
        return self._finish(node_name, "revoke", data, workflow_results, dynamic_values)


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
