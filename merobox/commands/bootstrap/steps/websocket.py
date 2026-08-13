"""
WebSocket step executors: handshake assertion and event assertion.

Both talk to a node's ``/ws`` endpoint. WebSocket clients cannot set custom
headers, so the JWT is passed via the ``?token=<jwt>`` query param (mirroring
``core/scripts/test-websocket-auth.sh``).

``ws_connect`` asserts only the handshake outcome. ``assert_ws_event``
subscribes to a group's event stream and asserts what does (or does not) arrive
on it, which is the only way to gate on an event whose absence writes no log
line to grep for.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

import aiohttp

from merobox.commands.auth import AuthManager
from merobox.commands.bootstrap.steps.base import BaseStep
from merobox.commands.constants import DEFAULT_CONNECTION_TIMEOUT
from merobox.commands.utils import console

WS_ENDPOINT = "/ws"

# Correlates the subscribe request with its acknowledgement. Event pushes carry
# a null id, so the ack is the only frame that echoes this back.
_SUBSCRIBE_REQUEST_ID = 1

# Cap on frames replayed in a failure report, so a long window of progress
# frames cannot bury the assertion that failed.
_MAX_REPORTED_FRAMES = 20

# Stands in for the JWT wherever a URL reaches the console.
_REDACTED = "***"


class _WebSocketStepBase(BaseStep):
    """Shared node/token/URL resolution for the WebSocket steps."""

    def _resolve_ws_target(self, node_name: str) -> tuple[str, str]:
        """Resolve a node to its RPC URL and the name its token is cached under."""
        resolved = self._resolve_node(node_name)
        if resolved:
            return resolved.url, resolved.node_name
        return self._get_node_rpc_url(node_name), node_name

    def _resolve_token(
        self,
        cache_node_name: str,
        workflow_results: dict[str, Any],
        dynamic_values: dict[str, Any],
    ) -> str | None:
        """Resolve the JWT to attach: explicit ``token`` field wins, else cache."""
        explicit = self.config.get("token")
        if explicit:
            return self._resolve_dynamic_value(
                explicit, workflow_results, dynamic_values
            )
        cached = AuthManager().get_cached_token(cache_node_name)
        return cached.access_token if cached else None

    def _build_ws_url(self, rpc_url: str, token: str | None) -> str:
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

    def _redact(self, text: str, token: str | None) -> str:
        """Mask a token anywhere in text bound for the console.

        aiohttp errors stringify the request URL, and the JWT rides in that
        URL's query string, so every error path can otherwise print the token
        into a CI log that outlives the node.
        """
        return text.replace(token, _REDACTED) if token else text


class WebSocketConnectStep(_WebSocketStepBase):
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
            rpc_url, cache_node_name = self._resolve_ws_target(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        token: str | None = None
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
        console.print(
            f"[cyan]Opening WebSocket to {self._redact(ws_url, token)}[/cyan]"
        )

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
            detail = self._redact(str(e), token)
            if expected_failure:
                if e.status in (401, 403):
                    self._report_expected_failure(detail)
                    return True
                console.print(
                    f"[red]❌ Expected a 401/403 auth rejection but the "
                    f"{node_name} handshake returned HTTP {e.status}[/red]"
                )
                return False
            console.print(
                f"[red]❌ WebSocket connect to {node_name} failed: {detail}[/red]"
            )
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # Connection refused / reset / timeout doesn't prove an auth
            # rejection, so it never satisfies expected_failure.
            console.print(
                f"[red]❌ WebSocket connect to {node_name} error: "
                f"{self._redact(str(e), token)}[/red]"
            )
            return False

        if expected_failure:
            self._report_unexpected_success()
            return False

        console.print(f"[green]✓ WebSocket connected to {node_name}[/green]")
        return True


class WebSocketEventAssertStep(_WebSocketStepBase):
    """Subscribe to a group's event stream and assert what arrives on it.

    Watches the stream for ``timeout_seconds``. The default form passes as soon
    as an event of type ``event`` satisfying ``match`` arrives. ``absent: true``
    inverts the verdict, passing only if the window elapses without one, and
    ``count: n`` demands exactly ``n``.

    Required fields: ``node``, ``group_id``, ``event``.

    Optional fields:
    - ``match`` (dict): dotted paths into the delivered frame mapped to expected
      values. A subset test, so unlisted fields are ignored. Paths address the
      wire frame, which is camelCase where the Rust is snake_case:
      ``groupId``, ``data.toVersion``, ``data.toStateVersion``.
    - ``absent`` (bool): assert the event must NOT arrive.
    - ``count`` (int): assert exactly this many arrive. Mutually exclusive with
      ``absent``, which is the same assertion for ``count: 0``.
    - ``timeout_seconds`` (number): window length, default ``30``.
    - ``token`` (str): explicit JWT (supports ``{{placeholders}}``); otherwise
      the token a prior ``login`` cached for the node, or none at all on a node
      running without auth.

    ``count`` is counted over the bounded window, so it proves the event did not
    fire more than ``count`` times within ``timeout_seconds``, not that it never
    fires again afterwards. Establishing the count also costs the whole window,
    since a further arrival can only be ruled out by watching for one.

    Two properties of the transport a workflow author has to plan around:

    A subscription observes only events emitted after it lands, and the stream
    has no replay, so an event the triggering step already emitted on the same
    node cannot be caught by a later step. Assert against a node that learns of
    the change through sync.

    ``CascadeProgress`` is admin-gated at subscribe time while the other
    migration variants are member-visible, so a non-admin subscriber never
    receives it and ``absent: true`` on it would pass for the wrong reason.
    """

    _DEFAULT_TIMEOUT_SECONDS = 30.0

    def _get_required_fields(self) -> list[str]:
        return ["node", "group_id", "event"]

    def _validate_field_types(self) -> None:
        self._validate_string_field("node")
        self._validate_string_field("group_id")
        self._validate_string_field("event")
        self._validate_string_field("token", required=False)
        self._validate_boolean_field("absent", required=False)
        self._validate_integer_field("count", required=False, non_negative=True)
        # Both express an expected number of arrivals, so a config carrying two
        # of them has no single answer to honour.
        if self.config.get("count") is not None and self.config.get("absent"):
            raise ValueError(
                f"Step '{self._get_step_name()}': 'count' and 'absent' are "
                f"mutually exclusive ('absent: true' is 'count: 0')"
            )
        self._validate_number_field("timeout_seconds", required=False, positive=True)
        timeout = self.config.get("timeout_seconds")
        # NaN/inf survive the positive check but leave the deadline non-finite,
        # so the watch loop would never end.
        if timeout is not None and not math.isfinite(timeout):
            raise ValueError(
                f"Step '{self._get_step_name()}': 'timeout_seconds' must be a "
                f"finite number"
            )
        self._validate_dict_field("match", required=False)
        for path in self.config.get("match") or {}:
            if not isinstance(path, str) or not path:
                raise ValueError(
                    f"Step '{self._get_step_name()}': 'match' keys must be "
                    f"non-empty dotted paths into the event frame"
                )

    async def execute(
        self, workflow_results: dict[str, Any], dynamic_values: dict[str, Any]
    ) -> bool:
        node_name = self.config["node"]
        group_id = self._resolve_dynamic_value(
            self.config["group_id"], workflow_results, dynamic_values
        )
        event_type = self._resolve_dynamic_value(
            self.config["event"], workflow_results, dynamic_values
        )
        # How many matches the window must hold. `None` means "at least one",
        # the default, which is also the only form that can stop at the first
        # arrival; an exact count is only knowable at the end of the window.
        required: int | None = None
        if self.config.get("absent"):
            required = 0
        elif self.config.get("count") is not None:
            required = int(self.config["count"])
        # `.get(key, default)` returns the default only when the key is ABSENT;
        # an explicit `timeout_seconds: null` yields a present key with value
        # None, which float() would crash on.
        timeout_raw = self.config.get("timeout_seconds")
        timeout_seconds = float(
            self._DEFAULT_TIMEOUT_SECONDS if timeout_raw is None else timeout_raw
        )
        match_spec = {
            path: (
                self._resolve_dynamic_value(expected, workflow_results, dynamic_values)
                if isinstance(expected, str)
                else expected
            )
            for path, expected in (self.config.get("match") or {}).items()
        }

        try:
            rpc_url, cache_node_name = self._resolve_ws_target(node_name)
        except Exception as e:
            console.print(f"[red]Failed to resolve node {node_name}: {str(e)}[/red]")
            return False

        # A node running without auth issues no token, and its WS treats every
        # connection as node owner, so a missing token is not an error here.
        # The subscribe acknowledgement below is the real authorization signal.
        token = self._resolve_token(cache_node_name, workflow_results, dynamic_values)
        ws_url = self._build_ws_url(rpc_url, token)

        wanted = (
            f"at least one '{event_type}'"
            if required is None
            else f"exactly {required} '{event_type}'"
        )
        console.print(
            f"[blue]⏳ Watching {node_name} for {wanted} on group {group_id} "
            f"(timeout {timeout_seconds:g}s)[/blue]"
        )

        matches, received, blocker = await self._watch(
            ws_url, group_id, event_type, match_spec, timeout_seconds, required
        )

        if blocker is not None:
            # The window was never observed, so no verdict was tested. An
            # unobserved window is not an absent event, so the `absent` and
            # `count` forms fail here too rather than passing vacuously.
            # A transport blocker quotes the exception, which quotes the URL the
            # token rides in, so redact here rather than at each raising site.
            console.print(
                f"✗ assert_ws_event on {node_name}: {self._redact(blocker, token)}",
                style="red",
                markup=False,
            )
            self._report_received(received, event_type, match_spec, matches)
            return False

        satisfied = len(matches) >= 1 if required is None else len(matches) == required
        if satisfied:
            if matches:
                workflow_results[f"ws_event_{node_name}"] = matches[0]
            if required == 0:
                console.print(
                    f"[green]✓ assert_ws_event: no '{event_type}' reached "
                    f"{node_name} for group {group_id} in {timeout_seconds:g}s "
                    f"({len(received)} other event(s) observed)[/green]"
                )
            elif required is None:
                console.print(
                    f"[green]✓ assert_ws_event: '{event_type}' arrived on "
                    f"{node_name} for group {group_id}[/green]"
                )
            else:
                console.print(
                    f"[green]✓ assert_ws_event: exactly {required} "
                    f"'{event_type}' arrived on {node_name} for group "
                    f"{group_id} in {timeout_seconds:g}s[/green]"
                )
            return True

        console.print(
            f"✗ assert_ws_event on {node_name}: expected {wanted} on group "
            f"{group_id} within {timeout_seconds:g}s, but {len(matches)} arrived",
            style="red",
            markup=False,
        )
        self._report_received(received, event_type, match_spec, matches)
        return False

    async def _watch(
        self,
        ws_url: str,
        group_id: str,
        event_type: str,
        match_spec: dict[str, Any],
        timeout_seconds: float,
        required: int | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        """Subscribe and collect frames until the verdict is settled or the deadline.

        Returns ``(matching_frames, received_frames, blocker)``. ``blocker`` is
        set when the window could not be observed at all (a refused handshake, a
        denied subscription, an early close) and invalidates every verdict.
        The received frames come back on every path so a caller can report what
        actually arrived instead of only that the expectation went unmet.

        One arrival past the ceiling settles the run early: for the default
        "at least one" that is the success, and for an exact ``required`` it is
        a failure no further watching can undo. An exact count that is still
        reachable has to watch the whole window.
        """
        received: list[dict[str, Any]] = []
        matches: list[dict[str, Any]] = []
        stop_after = required if required is not None else 0
        deadline = time.monotonic() + timeout_seconds

        try:
            async with aiohttp.ClientSession() as session:
                # asyncio.wait_for rather than ws_connect's own timeout kwarg,
                # whose shape changed in aiohttp 3.11.
                try:
                    ws = await asyncio.wait_for(
                        session.ws_connect(ws_url), timeout_seconds
                    )
                except asyncio.TimeoutError:
                    return (
                        matches,
                        received,
                        f"the handshake did not complete within {timeout_seconds:g}s",
                    )

                try:
                    await ws.send_str(
                        json.dumps(
                            {
                                "id": _SUBSCRIBE_REQUEST_ID,
                                "method": "subscribe",
                                # Hex ids, the representation the group and
                                # namespace admin APIs hand out.
                                "params": {"groupIds": [group_id]},
                            }
                        )
                    )
                    subscribed = False

                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            msg = await asyncio.wait_for(ws.receive(), remaining)
                        except asyncio.TimeoutError:
                            break  # the window elapsed, the normal end
                        if msg.type is not aiohttp.WSMsgType.TEXT:
                            return (
                                matches,
                                received,
                                f"the connection ended after "
                                f"{timeout_seconds - max(remaining, 0.0):.1f}s "
                                f"({msg.type.name}), so the window was not observed",
                            )

                        frame = json.loads(msg.data)
                        if not isinstance(frame, dict):
                            continue

                        if frame.get("id") == _SUBSCRIBE_REQUEST_ID:
                            result = frame.get("result")
                            # The server answers with the ids it actually
                            # subscribed, dropping any the caller may not
                            # observe, so the requested id missing from the ack
                            # is a denial and not something to wait out. A
                            # non-empty list is not enough: an ack naming only
                            # other groups leaves this one unwatched, and
                            # `absent` would then pass without observing
                            # anything.
                            acked = (
                                result.get("groupIds")
                                if isinstance(result, dict)
                                else None
                            )
                            if not isinstance(acked, list) or group_id not in acked:
                                return (
                                    matches,
                                    received,
                                    f"the node refused a subscription to group "
                                    f"{group_id} (not a member of it, or the id "
                                    f"is wrong): {msg.data}",
                                )
                            subscribed = True
                            continue

                        result = frame.get("result")
                        if not isinstance(result, dict):
                            continue
                        received.append(result)
                        if result.get("type") != event_type:
                            continue
                        if self._match_failures(result, match_spec):
                            continue
                        matches.append(result)
                        if len(matches) > stop_after:
                            return matches, received, None

                    if not subscribed:
                        return (
                            matches,
                            received,
                            "the subscription was never acknowledged, so the "
                            "window was not observed",
                        )
                finally:
                    await ws.close()
        except (aiohttp.ClientError, OSError) as e:
            return matches, received, f"WebSocket error: {e}"
        except json.JSONDecodeError as e:
            return matches, received, f"the node sent a frame that is not JSON: {e}"

        return matches, received, None

    def _match_failures(
        self, frame: dict[str, Any], match_spec: dict[str, Any]
    ) -> list[str]:
        """Per-path mismatches; empty means the frame satisfies ``match``."""
        failures = []
        for path, expected in match_spec.items():
            actual = self._get_value(frame, path)
            if actual != expected:
                failures.append(f"{path}: expected {expected!r}, got {actual!r}")
        return failures

    def _report_received(
        self,
        received: list[dict[str, Any]],
        event_type: str,
        match_spec: dict[str, Any],
        matches: list[dict[str, Any]],
    ) -> None:
        """Replay what actually arrived, marking matches and near misses.

        Matching frames are marked rather than replayed separately, so a count
        mismatch shows which arrivals were counted without printing the stream
        twice. markup=False so event payloads containing ``[..]`` are not eaten
        as Rich console tags.
        """
        if not received:
            console.print(
                "  no events arrived on the subscription", style="red", markup=False
            )
            return
        console.print(
            f"  {len(received)} event(s) arrived on the subscription, "
            f"{len(matches)} of them matching:",
            style="red",
            markup=False,
        )
        # Identity, not equality: two matches can be equal frames, and each
        # arrival counted separately toward `count`.
        matched_ids = {id(frame) for frame in matches}
        for frame in received[:_MAX_REPORTED_FRAMES]:
            marker = "match >" if id(frame) in matched_ids else "      "
            console.print(
                f"    {marker} {json.dumps(frame, sort_keys=True)}",
                style="red",
                markup=False,
            )
            if match_spec and frame.get("type") == event_type:
                for failure in self._match_failures(frame, match_spec):
                    console.print(f"      {failure}", style="red", markup=False)
        if len(received) > _MAX_REPORTED_FRAMES:
            console.print(
                f"    ... and {len(received) - _MAX_REPORTED_FRAMES} more",
                style="red",
                markup=False,
            )
