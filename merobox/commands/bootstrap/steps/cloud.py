"""
Cloud (mdma) workflow step executors.

Two halves of the same handshake:

- ``issue_ownership_proof`` — ask a node's merod admin API for a signed
  ownership proof over a namespace
  (``POST /admin-api/groups/{id}/issue-namespace-ownership-proof``). This is
  the credential the cloud verifies to accept that the caller owns the
  namespace.
- ``cloud_request`` — a single authenticated HTTP call to an mdma deployment
  (claim, enable-ha, disable-ha, relay/authorship reads). Before this step
  existed, every cloud hop in a workflow was a ``script`` step shelling out to
  ``curl``, which is where the ``set -eu`` pipe-swallowing hazard bites: a
  failing ``curl`` on the left of a pipe does not trip ``set -eu``, so the
  script exits 0 after a 401 and the workflow proceeds against empty state.
  This step asserts the status code in Python, so a 401 fails the step.

Wire-shape notes, all of them load-bearing and all of them learned the hard way:

- merod's admin API is camelCase (serde ``rename_all = "camelCase"``): the
  proof request body is ``{audience, subject, nonce, expiresAtMs}``.
- The **namespace** proof variant takes NO ``contextId``. Sending one is a 400.
- mdma's cloud API is snake_case at the top level
  (``{namespace_id, ownership_proof}``) but the proof object itself forwards
  **verbatim**, keeping merod's camelCase inner keys — mdma's ``OwnershipProof``
  model sets ``populate_by_name`` and accepts both spellings, so no re-keying
  step is needed (and a re-keying step is three chances to get it wrong).

Secrets (the MDMA session JWT) are never written into workflow YAML: config
values may reference the environment as ``${MDMA_SESSION}``, resolved by
``steps/_env.py``, which fails loudly when the variable is unset.
"""

import secrets
import time
from typing import Any, Optional
from urllib.parse import quote

import requests

from merobox.commands.bootstrap.steps._env import EnvRefError, resolve_env_refs
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
)
from merobox.commands.utils import console

DEFAULT_MDMA_URL = "https://manager.cloud.calimero.network"
DEFAULT_TOKEN_REF = "${MDMA_SESSION}"

# mdma's `manager/app/ownership_proof.py` pins this exact audience for the
# namespace-scoped proof (OWNERSHIP_PROOF_AUDIENCE_NAMESPACE) and rejects
# anything else, for both `claim` and `enable-ha`.
NAMESPACE_PROOF_AUDIENCE = "mdma:enable-ha-namespace"

# merod clamps proof expiry to issued_at + 5min; 4min leaves headroom for the
# rest of the chain (claim → enable-ha) without bumping into the clamp.
DEFAULT_PROOF_TTL_MS = 240_000

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _http(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", (DEFAULT_CONNECTION_TIMEOUT, DEFAULT_READ_TIMEOUT))
    return requests.request(method, url, **kwargs)


class IssueOwnershipProofStep(BaseStep):
    """Issue a merod-signed namespace ownership proof for the cloud handshake.

    Required fields: ``node``, ``group_id`` (the namespace id), ``subject``
    (the cloud identity the proof is issued to — mdma checks it equals the
    caller's email, so this is normally ``${MDMA_EMAIL}``).

    Optional fields:
    - ``audience`` (str): defaults to ``mdma:enable-ha-namespace``.
    - ``nonce`` (str): defaults to a fresh 16-byte hex nonce. mdma enforces
      global nonce uniqueness (replay guard), so leave it unset.
    - ``expires_in_ms`` (int): defaults to 240000 (merod clamps to 5 minutes).
    - ``outputs`` (dict): exports from the step result, which is shaped
      ``{proof, audience, subject, nonce, expires_at_ms}`` where ``proof`` is
      merod's response object **verbatim** (camelCase keys), ready to forward
      to mdma as ``ownership_proof``.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "subject"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        for field in ("node", "group_id", "subject"):
            if not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        for field in ("audience", "nonce"):
            if field in self.config and not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if "expires_in_ms" in self.config and not isinstance(
            self.config.get("expires_in_ms"), int
        ):
            raise ValueError(f"Step '{step_name}': 'expires_in_ms' must be an integer")

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        expected_failure = self._is_expected_failure()

        try:
            group_id = self._resolved(
                self.config["group_id"], workflow_results, dynamic_values
            )
            subject = self._resolved(
                self.config["subject"], workflow_results, dynamic_values
            )
            audience = self._resolved(
                self.config.get("audience", NAMESPACE_PROOF_AUDIENCE),
                workflow_results,
                dynamic_values,
            )
            nonce = self._resolved(
                self.config.get("nonce") or secrets.token_hex(16),
                workflow_results,
                dynamic_values,
            )
        except EnvRefError as e:
            console.print(f"[red]issue_ownership_proof on {node_name}: {e}[/red]")
            return False

        ttl_ms = int(self.config.get("expires_in_ms", DEFAULT_PROOF_TTL_MS))
        expires_at_ms = int(time.time() * 1000) + ttl_ms

        # camelCase per merod's serde rename_all, and NO contextId: the
        # namespace variant rejects a context-scoped body with a 400.
        body = {
            "audience": audience,
            "subject": subject,
            "nonce": nonce,
            "expiresAtMs": expires_at_ms,
        }

        try:
            admin_url = self._get_node_rpc_url(node_name)
            url = (
                f"{admin_url}/admin-api/groups/{quote(str(group_id), safe='')}"
                f"/issue-namespace-ownership-proof"
            )
            response = _http("POST", url, json=body)
        except Exception as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            console.print(
                f"[red]issue_ownership_proof failed on {node_name}: {str(e)}[/red]"
            )
            return False

        if response.status_code != 200:
            message = (
                f"issue_ownership_proof returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )
            if expected_failure:
                self._report_expected_failure(message)
                return True
            console.print(f"[red]{message}[/red]")
            return False

        payload = self._parse_json(response.text)
        proof = payload.get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(proof, dict):
            console.print(
                f"[red]issue_ownership_proof: unexpected response shape: "
                f"{response.text[:200]}[/red]"
            )
            return False

        data = {
            "proof": proof,
            "audience": audience,
            "subject": subject,
            "nonce": nonce,
            "expires_at_ms": expires_at_ms,
        }
        workflow_results[f"issue_ownership_proof_{node_name}"] = data
        self._export_variables(data, node_name, dynamic_values)

        console.print(
            f"[green]✓ Issued ownership proof for namespace {group_id} "
            f"(audience={audience}, subject={subject}) on {node_name}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
            return False
        return True

    def _resolved(
        self,
        value: Any,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Any:
        return resolve_env_refs(
            self._resolve_dynamic_value(value, workflow_results, dynamic_values)
        )


class CloudRequestStep(BaseStep):
    """Make one authenticated HTTP call to an mdma (Calimero Cloud) deployment.

    Required fields: ``method`` (GET/POST/PUT/PATCH/DELETE), ``path``
    (e.g. ``/api/cloud/namespaces/claim``; supports ``{{placeholders}}``).

    Optional fields:
    - ``base_url`` (str): defaults to ``${MDMA_URL}`` if set in the
      environment, else the prod manager URL.
    - ``body`` (dict): JSON body; every string inside is placeholder-resolved
      and ``${ENV}``-resolved, and a whole-value placeholder may resolve to a
      dict (that is how an ownership proof is forwarded verbatim).
    - ``token`` (str): bearer token; defaults to ``${MDMA_SESSION}``. Set
      ``token: null`` for an unauthenticated call.
    - ``expect_status`` (int | list[int]): defaults to ``200``.
    - ``non_blocking`` (bool): report a failure but let the workflow continue
      (for best-effort cleanup hops such as ``disable-ha``).
    - ``outputs`` (dict): exports from the decoded JSON response body.

    The step FAILS on an unexpected status. That is the whole point: the shell
    equivalent silently exited 0 after a 401 and let the workflow assert
    against state that was never written.
    """

    def _get_required_fields(self) -> list[str]:
        return ["method", "path"]

    def _validate_field_types(self) -> None:
        step_name = self._get_step_name()
        method = self.config.get("method")
        if not isinstance(method, str):
            raise ValueError(f"Step '{step_name}': 'method' must be a string")
        if method.upper() not in _HTTP_METHODS:
            raise ValueError(
                f"Step '{step_name}': 'method' must be one of {_HTTP_METHODS}, "
                f"got '{method}'"
            )
        for field in ("path", "base_url"):
            if field in self.config and not isinstance(self.config.get(field), str):
                raise ValueError(f"Step '{step_name}': '{field}' must be a string")
        if not isinstance(self.config.get("path"), str):
            raise ValueError(f"Step '{step_name}': 'path' must be a string")
        if "body" in self.config and not isinstance(self.config.get("body"), dict):
            raise ValueError(f"Step '{step_name}': 'body' must be a mapping")
        if "token" in self.config and self.config.get("token") is not None:
            if not isinstance(self.config.get("token"), str):
                raise ValueError(
                    f"Step '{step_name}': 'token' must be a string or null"
                )
        if "non_blocking" in self.config and not isinstance(
            self.config.get("non_blocking"), bool
        ):
            raise ValueError(f"Step '{step_name}': 'non_blocking' must be a boolean")
        expect = self.config.get("expect_status")
        if expect is not None:
            values = expect if isinstance(expect, list) else [expect]
            if not values or not all(isinstance(v, int) for v in values):
                raise ValueError(
                    f"Step '{step_name}': 'expect_status' must be an int or a "
                    f"non-empty list of ints"
                )

    def _expected_statuses(self) -> list[int]:
        expect = self.config.get("expect_status", 200)
        return expect if isinstance(expect, list) else [expect]

    def _resolve_deep(
        self,
        value: Any,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Any:
        """Resolve ``{{placeholders}}`` then ``${ENV}`` through a JSON value.

        A whole-string placeholder resolves to whatever the dynamic value
        holds — including a dict — so ``ownership_proof: '{{proof}}'`` forwards
        the proof object verbatim rather than stringifying it.
        """
        if isinstance(value, str):
            resolved = self._resolve_dynamic_value(
                value, workflow_results, dynamic_values
            )
            return (
                resolve_env_refs(resolved)
                if isinstance(resolved, str)
                else self._resolve_deep(resolved, workflow_results, dynamic_values)
            )
        if isinstance(value, dict):
            return {
                k: self._resolve_deep(v, workflow_results, dynamic_values)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_deep(v, workflow_results, dynamic_values) for v in value
            ]
        return value

    def _resolve_token(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> Optional[str]:
        if "token" in self.config and self.config.get("token") is None:
            return None
        raw = self.config.get("token", DEFAULT_TOKEN_REF)
        resolved = self._resolve_dynamic_value(raw, workflow_results, dynamic_values)
        return resolve_env_refs(resolved) if isinstance(resolved, str) else resolved

    def _base_url(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> str:
        configured = self.config.get("base_url")
        if configured:
            resolved = self._resolve_dynamic_value(
                configured, workflow_results, dynamic_values
            )
            return str(resolve_env_refs(resolved)).rstrip("/")
        try:
            return str(resolve_env_refs("${MDMA_URL}")).rstrip("/")
        except EnvRefError:
            return DEFAULT_MDMA_URL

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        step_name = self._get_step_name()
        method = str(self.config["method"]).upper()
        non_blocking = bool(self.config.get("non_blocking", False))
        expected_failure = self._is_expected_failure()

        try:
            base_url = self._base_url(workflow_results, dynamic_values)
            path = str(
                resolve_env_refs(
                    self._resolve_dynamic_value(
                        self.config["path"], workflow_results, dynamic_values
                    )
                )
            )
            token = self._resolve_token(workflow_results, dynamic_values)
            body = (
                self._resolve_deep(
                    self.config["body"], workflow_results, dynamic_values
                )
                if "body" in self.config
                else None
            )
        except EnvRefError as e:
            return self._fail(f"cloud_request '{step_name}': {e}", non_blocking)

        url = f"{base_url}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        console.print(f"[cyan]{method} {url}[/cyan]")

        try:
            response = _http(
                method,
                url,
                headers=headers,
                **({"json": body} if body is not None else {}),
            )
        except Exception as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            return self._fail(
                f"cloud_request '{step_name}' {method} {url} errored: {str(e)}",
                non_blocking,
            )

        expected = self._expected_statuses()
        if response.status_code not in expected:
            message = (
                f"cloud_request '{step_name}' {method} {url} returned HTTP "
                f"{response.status_code} (expected {expected}): "
                f"{response.text[:400]}"
            )
            if expected_failure:
                self._report_expected_failure(message)
                return True
            return self._fail(message, non_blocking)

        payload = self._parse_json(response.text)
        data = payload if isinstance(payload, dict) else {"response": payload}
        data.setdefault("status_code", response.status_code)

        workflow_results[f"cloud_request_{self._result_key()}"] = data
        self._export_variables(data, self._result_key(), dynamic_values)

        console.print(
            f"[green]✓ cloud_request '{step_name}': HTTP {response.status_code}[/green]"
        )
        if expected_failure:
            self._report_unexpected_success()
            return False
        return True

    def _result_key(self) -> str:
        """Stable key for workflow_results / export naming.

        ``cloud_request`` is not node-scoped, so the step name stands in for
        the node name other steps use.
        """
        name = self._get_step_name()
        return "_".join(str(name).lower().split())

    def _fail(self, message: str, non_blocking: bool) -> bool:
        if non_blocking:
            console.print(f"[yellow]⚠️  {message} (non_blocking)[/yellow]")
            return True
        console.print(f"[red]{message}[/red]")
        return False
