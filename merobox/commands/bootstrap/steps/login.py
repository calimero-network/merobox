"""
Login step executor for embedded-auth nodes.

Performs the bootstrap/login against a node's embedded auth router
(`POST /auth/token`, `auth_method = user_password`) and seeds the on-disk
token cache so that every downstream `execute`/`call`/`ws_connect` step on the
same node is authenticated automatically.

On a fresh node the first `POST /auth/token` with `user_password` auto-creates a
root key with `["admin"]` permission — so the very first login doubles as setup,
with no chicken-and-egg.
"""

from typing import Any

from merobox.commands.auth import AuthenticationError, AuthManager
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class LoginStep(BaseStep):
    """Authenticate against a node's embedded auth router and seed the token cache.

    Required fields: ``node``, ``username``, ``password``.

    Set ``expected_failure: true`` to assert that authentication is *rejected*
    (e.g. bad credentials) — the step then passes only when the login fails.

    Exports (via ``outputs``): the response carries ``access_token`` and
    ``refresh_token``, so e.g. ``outputs: { token: access_token }`` captures the
    JWT for later steps.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node", "username", "password"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("username")
        # Password may be any non-empty string (allow symbols/whitespace inside).
        self._validate_string_field("password", allow_empty=False)
        self._validate_boolean_field("expected_failure", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        username = self._resolve_dynamic_value(
            self.config["username"], workflow_results, dynamic_values
        )
        password = self._resolve_dynamic_value(
            self.config["password"], workflow_results, dynamic_values
        )
        # First-root-key bootstrap secret: an explicit step field wins;
        # otherwise AuthManager defaults it from MERO_AUTH_BOOTSTRAP_SECRET.
        bootstrap_secret = self.config.get("bootstrap_secret")
        if bootstrap_secret is not None:
            bootstrap_secret = self._resolve_dynamic_value(
                bootstrap_secret, workflow_results, dynamic_values
            )
        expected_failure = self._is_expected_failure()

        # Resolve the node URL and the stable name used as the token-cache key.
        # We always seed under a stable name (independent of auth_mode) so that
        # downstream steps find the token via the same key.
        try:
            rpc_url, cache_node_name = self._resolve_login_target(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        auth_manager = AuthManager()

        try:
            token = await auth_manager.authenticate(
                rpc_url, username, password, bootstrap_secret=bootstrap_secret
            )
        except AuthenticationError as e:
            if expected_failure:
                # A negative test must assert an *auth* rejection, not pass just
                # because the node was unreachable (AuthManager wraps transport
                # faults in the same exception type as a 401).
                if self._is_connectivity_error(str(e)):
                    console.print(
                        f"[red]❌ Expected an auth rejection but hit a "
                        f"connectivity error for {node_name}: {e}[/red]"
                    )
                    return False
                self._report_expected_failure(str(e))
                return True
            console.print(f"[red]❌ Login failed for {node_name}: {e}[/red]")
            self._print_node_logs_on_failure(node_name=node_name, lines=50)
            return False
        except Exception as e:
            # An unexpected exception is never proof of an auth rejection.
            console.print(f"[red]❌ Login error for {node_name}: {e}[/red]")
            return False

        if expected_failure:
            # Login succeeded but the workflow asserted it should be rejected.
            # Do NOT seed the cache or export tokens — a mis-asserted negative
            # test must not leave stale credentials behind for later steps/runs.
            self._report_unexpected_success()
            return False

        # Seed the on-disk cache so downstream calls auto-attach this token.
        if not auth_manager.save_token(token, cache_node_name):
            console.print(
                f"[yellow]⚠️  Authenticated with {node_name} but failed to "
                f"write the token cache[/yellow]"
            )

        # Store and export the token material for later steps.
        step_key = f"login_{node_name}"
        workflow_results[step_key] = token.to_dict()
        self._export_variables(token.to_dict(), node_name, dynamic_values)

        console.print(f"[green]✓ Logged in to {node_name} as {username}[/green]")
        return True

    def _resolve_login_target(self, node_name: str) -> tuple[str, str]:
        """Return ``(rpc_url, cache_node_name)`` for the login target.

        The cache node name is the stable identifier used as the token-cache
        key. For resolver-backed nodes it comes from the ResolvedNode; otherwise
        it falls back to the raw node name from the workflow.
        """
        resolved = self._resolve_node(node_name)
        if resolved:
            return resolved.url, resolved.node_name
        return self._get_node_rpc_url(node_name), node_name
