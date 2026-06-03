"""
Refresh step executor for embedded-auth nodes.

Exercises ``POST /auth/refresh``: reads the cached token for a node, swaps the
refresh token for a fresh access token, and re-seeds the cache so downstream
steps pick up the new token. Useful for testing that the refresh path issues a
working access token.
"""

from typing import Any

from merobox.commands.auth import AuthenticationError, AuthManager
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.utils import console


class RefreshStep(BaseStep):
    """Refresh the cached access token for a node via ``POST /auth/refresh``.

    Required fields: ``node``. The node must already have a cached token with a
    refresh token (typically seeded by a prior ``login`` step).

    Set ``expected_failure: true`` to assert the refresh is *rejected* (e.g. an
    expired/invalid refresh token) — the step then passes only when refresh
    fails.

    Exports (via ``outputs``): the new ``access_token`` and ``refresh_token``.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_boolean_field("expected_failure", required=False)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        expected_failure = self._is_expected_failure()

        try:
            resolved = self._resolve_node(node_name)
            if resolved:
                rpc_url, cache_node_name = resolved.url, resolved.node_name
            else:
                rpc_url, cache_node_name = self._get_node_rpc_url(node_name), node_name
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        auth_manager = AuthManager()
        cached = auth_manager.get_cached_token(cache_node_name)
        if cached is None:
            console.print(
                f"[red]❌ No cached token for {node_name}; run a 'login' step "
                f"before 'refresh'[/red]"
            )
            return False
        if not cached.refresh_token:
            console.print(
                f"[red]❌ Cached token for {node_name} has no refresh token[/red]"
            )
            return False

        try:
            new_token = await auth_manager.refresh(rpc_url, cached)
        except AuthenticationError as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            console.print(f"[red]❌ Token refresh failed for {node_name}: {e}[/red]")
            return False
        except Exception as e:
            if expected_failure:
                self._report_expected_failure(str(e))
                return True
            console.print(f"[red]❌ Token refresh error for {node_name}: {e}[/red]")
            return False

        if expected_failure:
            self._report_unexpected_success()

        if not auth_manager.save_token(new_token, cache_node_name):
            console.print(
                f"[yellow]⚠️  Refreshed token for {node_name} but failed to "
                f"write the token cache[/yellow]"
            )

        step_key = f"refresh_{node_name}"
        workflow_results[step_key] = new_token.to_dict()
        self._export_variables(new_token.to_dict(), node_name, dynamic_values)

        console.print(f"[green]✓ Refreshed token for {node_name}[/green]")
        return not expected_failure
