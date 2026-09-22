"""
WebSocket step executor: handshake, subscribe, and event assertions.

Opens a WebSocket against a node's ``/ws`` endpoint. WebSocket clients cannot
set custom headers, so the JWT is passed via the ``?token=<jwt>`` query param
(mirroring ``core/scripts/test-websocket-auth.sh``). Note the asymmetry with
SSE, which authenticates with an ``Authorization: Bearer`` header at ``/sse``.

Use it three ways:
- Positive handshake: with a valid cached token (seeded by a prior ``login``
  step) the connect must succeed.
- Negative handshake: ``unauthenticated: true`` forces a no-token connect;
  combine with ``expected_failure: true`` to assert the server rejects it
  (HTTP 401 on the upgrade handshake).
- Event assertion: ``subscribe:`` sends a subscribe frame, then
  ``expect_event:`` (or ``expect_no_event:``) reads frames until one matches or
  ``await_seconds`` elapses. This is the only way a workflow can assert that a
  node actually *delivered* an event, as opposed to that a write returned 200.

Wire shape (core ``calimero-server-primitives::ws``):
- Requests are ``{"id": <u64>, "method": "<name>", "params": {...}}`` — there
  is **no** ``jsonrpc`` member; this is not JSON-RPC 2.0 and a node rejects a
  frame carrying one (``deny_unknown_fields``).
- ``subscribe`` params are camelCase: ``{"contextIds": [...], "groupIds": [...]}``.
- Replies and pushed events are ``{"id": <u64>|null, "result": {...}}`` (or
  ``{"id": ..., "error": {...}}``). Pushed events always carry ``id: null``.
- A context event is untagged-flattened, so a state mutation reads
  ``{"result": {"contextId": "<hex>", "type": "StateMutation",
  "data": {"newRoot": "<hex>", "events": [...]}}}``.

Event ordering is not guaranteed relative to the subscribe ack: core documents
that events for a context may precede it, so the matcher considers every frame
received after the subscribe frame is sent, ack included.
"""

import asyncio
import json
from typing import Any, Optional

import aiohttp

from merobox.commands.auth import AuthManager
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import DEFAULT_CONNECTION_TIMEOUT
from merobox.commands.utils import console

WS_ENDPOINT = "/ws"

# Default window to wait for a matching event once subscribed.
DEFAULT_AWAIT_SECONDS = 30.0

# Request id for the subscribe frame. Any u64 works; a fixed one keeps the ack
# easy to correlate in logs.
SUBSCRIBE_REQUEST_ID = 1


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
    - ``subscribe`` (dict): ``{context_ids: [...], group_ids: [...]}`` — sends
      a ``subscribe`` frame (camelCase on the wire) once connected.
    - ``expect_event`` (dict): frame matcher; the step waits until a received
      frame matches. Keys (all optional, all must hold):
      ``path``+``equals`` (dotted path into the decoded frame, e.g.
      ``result.type``), and ``contains`` (substring of the raw frame text).
    - ``expect_no_event`` (bool): invert ``expect_event`` — pass only if NO
      matching frame arrives within the window.
    - ``await_seconds`` (number): how long to read frames for (default 30).
    - ``outputs`` (dict): exports read out of the matched frame.
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
        self._validate_number_field("await_seconds", required=False, positive=True)
        self._validate_boolean_field("expect_no_event", required=False)

        step_name = self.config.get(
            "name", f'Unnamed {self.config.get("type", "Unknown")} step'
        )
        subscribe = self.config.get("subscribe")
        if subscribe is not None:
            if not isinstance(subscribe, dict):
                raise ValueError(f"Step '{step_name}': 'subscribe' must be a mapping")
            for field in ("context_ids", "group_ids"):
                if field in subscribe and not isinstance(subscribe[field], list):
                    raise ValueError(
                        f"Step '{step_name}': 'subscribe.{field}' must be a list"
                    )
            if not subscribe.get("context_ids") and not subscribe.get("group_ids"):
                raise ValueError(
                    f"Step '{step_name}': 'subscribe' needs at least one of "
                    f"'context_ids' / 'group_ids' — an empty subscribe frame "
                    f"subscribes to nothing and would wait out the window"
                )
        matcher = self.config.get("expect_event")
        if matcher is not None:
            if not isinstance(matcher, dict):
                raise ValueError(
                    f"Step '{step_name}': 'expect_event' must be a mapping"
                )
            if not any(k in matcher for k in ("path", "contains")):
                raise ValueError(
                    f"Step '{step_name}': 'expect_event' needs 'path' (with "
                    f"'equals') and/or 'contains'"
                )
            if "path" in matcher and "equals" not in matcher:
                raise ValueError(
                    f"Step '{step_name}': 'expect_event.path' requires 'equals'"
                )
        if self.config.get("expect_no_event") and matcher is None:
            raise ValueError(
                f"Step '{step_name}': 'expect_no_event' requires 'expect_event' "
                f"to say which frame must not arrive"
            )

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

        matched: Optional[dict[str, Any]] = None
        event_assertion = self.config.get("expect_event") is not None
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
                    if self.config.get("subscribe") is not None:
                        await self._send_subscribe(ws, workflow_results, dynamic_values)
                    if event_assertion:
                        matched = await self._await_event(
                            ws, workflow_results, dynamic_values
                        )
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

        if event_assertion:
            return self._report_event_outcome(matched, node_name, dynamic_values)

        console.print(f"[green]✓ WebSocket connected to {node_name}[/green]")
        return True

    # ------------------------------------------------------------------
    # subscribe / event reading
    # ------------------------------------------------------------------

    async def _send_subscribe(
        self,
        ws: "aiohttp.ClientWebSocketResponse",
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> None:
        """Send the node's subscribe frame.

        ``{"id": 1, "method": "subscribe", "params": {"contextIds": [...]}}`` —
        no ``jsonrpc`` member (core's ``RequestPayload`` is
        ``deny_unknown_fields``), and camelCase params.
        """
        subscribe = self.config.get("subscribe") or {}
        params: dict[str, Any] = {}
        for cfg_key, wire_key in (
            ("context_ids", "contextIds"),
            ("group_ids", "groupIds"),
        ):
            values = subscribe.get(cfg_key) or []
            resolved = [
                self._resolve_dynamic_value(v, workflow_results, dynamic_values)
                for v in values
            ]
            if resolved:
                params[wire_key] = resolved

        frame = {"id": SUBSCRIBE_REQUEST_ID, "method": "subscribe", "params": params}
        console.print(f"[cyan]→ ws subscribe {json.dumps(params)}[/cyan]")
        await ws.send_str(json.dumps(frame))

    async def _await_event(
        self,
        ws: "aiohttp.ClientWebSocketResponse",
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Read frames until one matches ``expect_event`` or the window closes.

        Returns the matching frame (decoded, plus ``_raw``), or ``None``.
        """
        matcher = self.config.get("expect_event") or {}
        path = matcher.get("path")
        expected = (
            self._resolve_dynamic_value(
                matcher.get("equals"), workflow_results, dynamic_values
            )
            if "equals" in matcher
            else None
        )
        contains = (
            self._resolve_dynamic_value(
                matcher.get("contains"), workflow_results, dynamic_values
            )
            if "contains" in matcher
            else None
        )
        window = float(self.config.get("await_seconds", DEFAULT_AWAIT_SECONDS))
        deadline = asyncio.get_event_loop().time() + window
        seen = 0

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                console.print(
                    f"[yellow]• ws: window of {window}s closed after {seen} "
                    f"frame(s) with no match[/yellow]"
                )
                return None
            try:
                msg = await asyncio.wait_for(ws.receive(), remaining)
            except asyncio.TimeoutError:
                continue
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                console.print(
                    f"[yellow]• ws: socket closed after {seen} frame(s) "
                    f"({msg.type.name})[/yellow]"
                )
                return None
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue

            seen += 1
            raw = msg.data
            decoded = self._parse_json(raw)
            if not isinstance(decoded, dict):
                decoded = {}

            if contains is not None and str(contains) not in raw:
                continue
            if path is not None:
                actual = self._get_value(decoded, path)
                if str(actual) != str(expected):
                    continue

            frame = dict(decoded)
            frame["_raw"] = raw
            console.print(f"[cyan]← ws frame #{seen} matched: {raw[:300]}[/cyan]")
            return frame

    def _report_event_outcome(
        self,
        matched: Optional[dict[str, Any]],
        node_name: str,
        dynamic_values: dict[str, Any],
    ) -> bool:
        """Turn the (non-)match into a step verdict, honouring expect_no_event."""
        expect_no_event = bool(self.config.get("expect_no_event", False))
        matcher = json.dumps(self.config.get("expect_event"), default=str)

        if expect_no_event:
            if matched is None:
                console.print(
                    f"[green]✓ No frame matching {matcher} arrived on "
                    f"{node_name} (as expected)[/green]"
                )
                return True
            console.print(
                f"[red]❌ Expected NO frame matching {matcher} on {node_name}, "
                f"but one arrived: {str(matched.get('_raw'))[:300]}[/red]"
            )
            return False

        if matched is None:
            console.print(
                f"[red]❌ No frame matching {matcher} arrived on {node_name} "
                f"within the await window[/red]"
            )
            return False

        self._export_variables(matched, node_name, dynamic_values)
        console.print(
            f"[green]✓ {node_name} delivered an event matching {matcher}[/green]"
        )
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
