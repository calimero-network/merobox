"""
WebSocket connect step executor for embedded-auth nodes.

Opens a WebSocket subscription against a node's ``/ws`` endpoint and asserts the
handshake outcome. WebSocket clients cannot set custom headers, so the JWT is
passed via the ``?token=<jwt>`` query param (mirroring
``core/scripts/test-websocket-auth.sh``).

Use it two ways:
- Positive: with a valid cached token (seeded by a prior ``login`` step) the
  connect must succeed.
- Negative: ``unauthenticated: true`` forces a no-token connect; combine with
  ``expected_failure: true`` to assert the server rejects it (HTTP 401 on the
  upgrade handshake).
"""

import asyncio
from typing import Any, Optional

import aiohttp

from merobox.commands.auth import AuthManager
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import DEFAULT_CONNECTION_TIMEOUT
from merobox.commands.utils import console

WS_ENDPOINT = "/ws"


class WebSocketConnectStep(BaseStep):
    """Open a WebSocket subscription and assert the handshake outcome.

    Required fields: ``node``.

    Optional fields:
    - ``unauthenticated`` (bool): connect without attaching a token.
    - ``expected_failure`` (bool): the step passes only if the connect is
      rejected (use with ``unauthenticated: true`` for the negative case).
    - ``token`` (str): explicit JWT to attach (supports ``{{placeholders}}``);
      overrides the cached token.
    - ``message`` (str): optional text frame to send once connected.
    - ``timeout`` (number): handshake timeout in seconds.
    """

    def _get_required_fields(self) -> list[str]:
        return ["node"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_boolean_field("unauthenticated", required=False)
        self._validate_boolean_field("expected_failure", required=False)
        self._validate_string_field("token", required=False)
        self._validate_string_field("message", required=False)
        self._validate_number_field("timeout", required=False, positive=True)

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        unauthenticated = bool(self.config.get("unauthenticated", False))
        expected_failure = self._is_expected_failure()
        message = self.config.get("message")
        # `timeout` is optional and may be explicitly null in YAML — fall back to
        # the default rather than letting float(None) raise.
        timeout_cfg = self.config.get("timeout")
        timeout = (
            float(timeout_cfg)
            if timeout_cfg is not None
            else float(DEFAULT_CONNECTION_TIMEOUT)
        )

        try:
            resolved = self._resolve_node(node_name)
            if resolved:
                rpc_url, cache_node_name = resolved.url, resolved.node_name
            else:
                rpc_url, cache_node_name = self._get_node_rpc_url(node_name), node_name
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        token = None
        if not unauthenticated:
            token = self._resolve_token(
                cache_node_name, workflow_results, dynamic_values
            )
            if token is None:
                console.print(
                    f"[red]❌ No token available for {node_name}; run a 'login' "
                    f"step first or set 'unauthenticated: true'[/red]"
                )
                return False

        ws_url = self._build_ws_url(rpc_url, token)
        display_url = self._build_ws_url(rpc_url, "***" if token else None)
        console.print(f"[cyan]Opening WebSocket to {display_url}[/cyan]")

        try:
            async with aiohttp.ClientSession() as session:
                # Bound the upgrade handshake with asyncio.wait_for so the timeout
                # works across aiohttp versions (ws_connect's timeout kwarg changed
                # shape in 3.11).
                ws = await asyncio.wait_for(session.ws_connect(ws_url), timeout)
                try:
                    if message is not None:
                        resolved_message = self._resolve_dynamic_value(
                            message, workflow_results, dynamic_values
                        )
                        await ws.send_str(resolved_message)
                finally:
                    await ws.close()
        except aiohttp.WSServerHandshakeError as e:
            # The server rejected the upgrade handshake — the genuine auth signal.
            # Only an auth status (401/403) proves a rejected-without-token test;
            # other statuses are a real failure even under expected_failure.
            if expected_failure:
                if e.status in (401, 403):
                    self._report_expected_failure(str(e))
                    return True
                console.print(
                    f"[red]❌ Expected a 401/403 auth rejection but the "
                    f"{node_name} handshake returned HTTP {e.status}[/red]"
                )
                return False
            console.print(f"[red]❌ WebSocket connect to {node_name} failed: {e}[/red]")
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # Connection refused / reset / timeout doesn't prove an auth
            # rejection, so it never satisfies expected_failure.
            console.print(f"[red]❌ WebSocket connect to {node_name} error: {e}[/red]")
            return False

        if expected_failure:
            self._report_unexpected_success()
            return False

        console.print(f"[green]✓ WebSocket connected to {node_name}[/green]")
        return True

    def _resolve_token(
        self,
        cache_node_name: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Optional[str]:
        """Resolve the JWT to attach: explicit ``token`` field wins, else cache."""
        explicit = self.config.get("token")
        if explicit:
            return self._resolve_dynamic_value(
                explicit, workflow_results, dynamic_values
            )
        cached = AuthManager().get_cached_token(cache_node_name)
        return cached.access_token if cached else None

    def _build_ws_url(self, rpc_url: str, token: Optional[str]) -> str:
        """Build the ``ws(s)://host:port/ws[?token=...]`` URL from the RPC URL."""
        base = rpc_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        url = f"{base}{WS_ENDPOINT}"
        if token:
            url = f"{url}?token={token}"
        return url
