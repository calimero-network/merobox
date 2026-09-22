"""
Unit tests for the WebSocket subscribe/event-assertion support.

These run against a REAL WebSocket server (aiohttp) bound to loopback rather
than a mock, because what is under test is frame I/O: that the step sends the
node's frame shape (``{id, method, params}`` with NO ``jsonrpc`` member and
camelCase params) and that it actually reads pushed frames back. A mocked
socket would assert the test's own idea of the protocol.

The fake server mimics core's ws endpoint closely enough for that: it decodes
the subscribe frame, answers an ack with the same ``id``, then pushes a
StateMutation-shaped event with ``id: null``.
"""

import asyncio
import importlib
import json
import sys
from unittest.mock import MagicMock

import pytest

from merobox.commands.bootstrap.steps.websocket import WebSocketConnectStep

# Resolved through the class rather than `import ...websocket as m`: a sibling
# test module replaces entries under `merobox.commands` in `sys.modules`, which
# breaks the dotted-import form but not the already-imported module object.
websocket_module = sys.modules[WebSocketConnectStep.__module__]


def _real_aiohttp():
    """Get the genuine aiohttp, even when a sibling test module stubbed it.

    ``test_node_resolver`` installs ``sys.modules["aiohttp"] = MagicMock()`` at
    import time and never restores it, so by the time this module runs under
    the full suite the name may be a mock. These tests need real sockets, so
    re-import the actual package (and hand it back to the step module, which
    may have bound the mock at ITS import time).
    """
    module = sys.modules.get("aiohttp")
    if isinstance(module, MagicMock) or not hasattr(module, "ClientSession"):
        sys.modules.pop("aiohttp", None)
        module = importlib.import_module("aiohttp")
    return module


aiohttp = _real_aiohttp()
web = importlib.import_module("aiohttp.web")


@pytest.fixture(autouse=True)
def _real_aiohttp_in_step_module():
    """Point the step module at the real aiohttp for the duration of a test.

    Restores whatever was there afterwards so the sibling module's stub keeps
    working if it runs later.
    """
    previous_module = websocket_module.aiohttp
    previous_sys = sys.modules.get("aiohttp")
    websocket_module.aiohttp = aiohttp
    sys.modules["aiohttp"] = aiohttp
    try:
        yield
    finally:
        websocket_module.aiohttp = previous_module
        if previous_sys is not None:
            sys.modules["aiohttp"] = previous_sys


CONTEXT_ID = "ab" * 32
NEW_ROOT = "cd" * 32


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _state_mutation_frame(context_id=CONTEXT_ID):
    # Untagged-flattened ContextEvent: `type`/`data` sit next to `contextId`.
    return {
        "id": None,
        "result": {
            "contextId": context_id,
            "type": "StateMutation",
            "data": {"newRoot": NEW_ROOT, "events": []},
        },
    }


class FakeNodeWs:
    """A minimal stand-in for merod's ``/ws`` endpoint."""

    def __init__(self, push_frames, require_token=False):
        self.push_frames = push_frames
        self.require_token = require_token
        self.received = []
        self.runner = None
        self.url = None

    async def start(self):
        app = web.Application()
        app.router.add_get("/ws", self._handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def _handler(self, request):
        if self.require_token and "token" not in request.query:
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        msg = await ws.receive()
        if msg.type is web.WSMsgType.TEXT:
            frame = json.loads(msg.data)
            self.received.append(frame)
            # Subscribe ack carries the request's id.
            await ws.send_str(
                json.dumps(
                    {
                        "id": frame.get("id"),
                        "result": {
                            "contextIds": (frame.get("params") or {}).get(
                                "contextIds", []
                            )
                        },
                    }
                )
            )
        for pushed in self.push_frames:
            await ws.send_str(json.dumps(pushed))
        # Hold the socket open briefly so a negative test has to wait out its
        # window rather than seeing an early close.
        await asyncio.sleep(0.3)
        await ws.close()
        return ws


def _make_step(url, **overrides):
    cfg = {"type": "ws_subscribe", "node": "ws-node-1", "unauthenticated": True}
    cfg.update(overrides)
    step = WebSocketConnectStep(cfg, manager=MagicMock())
    step._resolve_node = lambda _n: None
    step._get_node_rpc_url = lambda _n: url
    return step


async def _with_server(server, make_step_kwargs, dynamic=None):
    url = await server.start()
    try:
        step = _make_step(url, **make_step_kwargs)
        return await step.execute({}, dynamic if dynamic is not None else {})
    finally:
        await server.stop()


# =============================================================================
# Validation
# =============================================================================


class TestEventFieldValidation:
    def test_empty_subscribe_raises(self):
        with pytest.raises(ValueError, match="subscribe"):
            WebSocketConnectStep(
                {"type": "ws_subscribe", "node": "n", "subscribe": {}},
                manager=MagicMock(),
            )

    def test_matcher_needs_path_or_contains(self):
        with pytest.raises(ValueError, match="expect_event"):
            WebSocketConnectStep(
                {"type": "ws_subscribe", "node": "n", "expect_event": {"equals": "x"}},
                manager=MagicMock(),
            )

    def test_path_requires_equals(self):
        with pytest.raises(ValueError, match="equals"):
            WebSocketConnectStep(
                {
                    "type": "ws_subscribe",
                    "node": "n",
                    "expect_event": {"path": "result.type"},
                },
                manager=MagicMock(),
            )

    def test_expect_no_event_requires_a_matcher(self):
        with pytest.raises(ValueError, match="expect_no_event"):
            WebSocketConnectStep(
                {"type": "ws_subscribe", "node": "n", "expect_no_event": True},
                manager=MagicMock(),
            )


# =============================================================================
# Frame shape + event reading
# =============================================================================


class TestSubscribeFrameShape:
    def test_sends_node_frame_shape_without_jsonrpc(self):
        server = FakeNodeWs([_state_mutation_frame()])
        result = _run(
            _with_server(
                server,
                {
                    "subscribe": {"context_ids": ["{{context_id}}"]},
                    "expect_event": {"path": "result.type", "equals": "StateMutation"},
                    "await_seconds": 5,
                },
                {"context_id": CONTEXT_ID},
            )
        )
        assert result is True
        sent = server.received[0]
        assert sent["method"] == "subscribe"
        # No `jsonrpc` member: core's RequestPayload is deny_unknown_fields.
        assert "jsonrpc" not in sent
        assert sent["params"] == {"contextIds": [CONTEXT_ID]}
        assert isinstance(sent["id"], int)

    def test_group_ids_are_camelcased(self):
        server = FakeNodeWs([_state_mutation_frame()])
        _run(
            _with_server(
                server,
                {
                    "subscribe": {"group_ids": ["ff" * 32]},
                    "expect_event": {"contains": "StateMutation"},
                    "await_seconds": 5,
                },
            )
        )
        assert server.received[0]["params"] == {"groupIds": ["ff" * 32]}


class TestEventAssertion:
    def test_matches_by_path_and_exports_from_the_frame(self):
        server = FakeNodeWs([_state_mutation_frame()])
        dynamic = {"context_id": CONTEXT_ID}
        result = _run(
            _with_server(
                server,
                {
                    "subscribe": {"context_ids": ["{{context_id}}"]},
                    "expect_event": {"path": "result.type", "equals": "StateMutation"},
                    "await_seconds": 5,
                    "outputs": {
                        "mutated_context": "result.contextId",
                        "new_root": "result.data.newRoot",
                    },
                },
                dynamic,
            )
        )
        assert result is True
        assert dynamic["mutated_context"] == CONTEXT_ID
        assert dynamic["new_root"] == NEW_ROOT

    def test_matches_by_contains(self):
        server = FakeNodeWs([_state_mutation_frame()])
        assert (
            _run(
                _with_server(
                    server,
                    {
                        "subscribe": {"context_ids": [CONTEXT_ID]},
                        "expect_event": {"contains": "StateMutation"},
                        "await_seconds": 5,
                    },
                )
            )
            is True
        )

    def test_skips_non_matching_frames_until_the_match(self):
        noise = {"id": None, "result": {"contextId": CONTEXT_ID, "type": "SyncStatus"}}
        server = FakeNodeWs([noise, noise, _state_mutation_frame()])
        assert (
            _run(
                _with_server(
                    server,
                    {
                        "subscribe": {"context_ids": [CONTEXT_ID]},
                        "expect_event": {
                            "path": "result.type",
                            "equals": "StateMutation",
                        },
                        "await_seconds": 5,
                    },
                )
            )
            is True
        )

    def test_no_matching_event_fails_the_step(self):
        noise = {"id": None, "result": {"contextId": CONTEXT_ID, "type": "SyncStatus"}}
        server = FakeNodeWs([noise])
        assert (
            _run(
                _with_server(
                    server,
                    {
                        "subscribe": {"context_ids": [CONTEXT_ID]},
                        "expect_event": {
                            "path": "result.type",
                            "equals": "StateMutation",
                        },
                        "await_seconds": 2,
                    },
                )
            )
            is False
        )

    def test_expect_no_event_passes_when_nothing_matches(self):
        noise = {"id": None, "result": {"contextId": CONTEXT_ID, "type": "SyncStatus"}}
        server = FakeNodeWs([noise])
        assert (
            _run(
                _with_server(
                    server,
                    {
                        "subscribe": {"context_ids": [CONTEXT_ID]},
                        "expect_event": {
                            "path": "result.type",
                            "equals": "StateMutation",
                        },
                        "expect_no_event": True,
                        "await_seconds": 2,
                    },
                )
            )
            is True
        )

    def test_expect_no_event_fails_when_the_event_arrives(self):
        server = FakeNodeWs([_state_mutation_frame()])
        assert (
            _run(
                _with_server(
                    server,
                    {
                        "subscribe": {"context_ids": [CONTEXT_ID]},
                        "expect_event": {
                            "path": "result.type",
                            "equals": "StateMutation",
                        },
                        "expect_no_event": True,
                        "await_seconds": 2,
                    },
                )
            )
            is False
        )

    def test_handshake_only_still_works(self):
        """No subscribe/expect_event: unchanged connect-assert behaviour."""
        server = FakeNodeWs([])
        assert _run(_with_server(server, {})) is True
