"""
Unit tests for the embedded-auth workflow steps: login, refresh, ws_connect.

These exercise the step executors with the network layer (AuthManager /
aiohttp) mocked, so they verify orchestration: token-cache seeding, output
export, and the positive/negative (expected_failure / unauthenticated)
branches.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from merobox.commands.auth import AuthenticationError, AuthToken
from merobox.commands.bootstrap.steps.login import LoginStep
from merobox.commands.bootstrap.steps.refresh import RefreshStep
from merobox.commands.bootstrap.steps.websocket import WebSocketConnectStep


def _run(coro):
    """Run a coroutine on a dedicated loop, leaving a fresh current loop behind.

    `asyncio.run()` closes its loop and unsets the current one, which on
    Python 3.11 makes a later bare `asyncio.get_event_loop()` raise
    "no current event loop". Other unit-test modules in this suite still use
    that legacy pattern, so we restore a usable current loop afterward rather
    than poison shared state for tests that run after us.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _manager():
    """A manager whose RPC URL resolves to a fixed localhost port."""
    manager = MagicMock()
    manager.get_node_rpc_port.return_value = 2528
    return manager


def _token(access="acc.jwt.tok", refresh="ref.jwt.tok"):
    return AuthToken(
        access_token=access,
        node_url="http://localhost:2528",
        auth_method="user_password",
        refresh_token=refresh,
        username="alice",
    )


# =============================================================================
# login
# =============================================================================


class TestLoginStep:
    def _step(self, **extra):
        config = {
            "type": "login",
            "name": "login",
            "node": "calimero-node-1",
            "username": "alice",
            "password": "password123",
            **extra,
        }
        return LoginStep(config, manager=_manager())

    def test_login_seeds_cache_and_exports_token(self):
        step = self._step(outputs={"access_token": "access_token"})
        fake_auth = MagicMock()
        fake_auth.authenticate = AsyncMock(return_value=_token())
        fake_auth.save_token.return_value = True

        dynamic = {}
        with patch(
            "merobox.commands.bootstrap.steps.login.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, dynamic))

        assert result is True
        fake_auth.authenticate.assert_awaited_once()
        # Token cached under the stable node name.
        fake_auth.save_token.assert_called_once()
        assert fake_auth.save_token.call_args[0][1] == "calimero-node-1"
        # Token material exported for downstream steps.
        assert dynamic["access_token"] == "acc.jwt.tok"

    def test_login_failure_fails_step(self):
        step = self._step()
        fake_auth = MagicMock()
        fake_auth.authenticate = AsyncMock(side_effect=AuthenticationError("bad creds"))

        with patch(
            "merobox.commands.bootstrap.steps.login.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is False
        fake_auth.save_token.assert_not_called()

    def test_login_expected_failure_passes_on_rejection(self):
        step = self._step(expected_failure=True)
        fake_auth = MagicMock()
        fake_auth.authenticate = AsyncMock(side_effect=AuthenticationError("bad creds"))

        with patch(
            "merobox.commands.bootstrap.steps.login.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is True
        fake_auth.save_token.assert_not_called()

    def test_login_expected_failure_but_success_fails(self):
        step = self._step(expected_failure=True)
        fake_auth = MagicMock()
        fake_auth.authenticate = AsyncMock(return_value=_token())
        fake_auth.save_token.return_value = True

        dynamic = {}
        with patch(
            "merobox.commands.bootstrap.steps.login.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, dynamic))

        # Asserted rejection but login succeeded -> step fails AND must not
        # seed the cache or export tokens (no stale credentials left behind).
        assert result is False
        fake_auth.save_token.assert_not_called()
        assert dynamic == {}

    def test_login_expected_failure_ignores_connectivity_error(self):
        # A negative test must assert an auth rejection, not pass because the
        # node was unreachable (AuthManager wraps both as AuthenticationError).
        step = self._step(expected_failure=True)
        fake_auth = MagicMock()
        fake_auth.authenticate = AsyncMock(
            side_effect=AuthenticationError(
                "Network error during authentication: connection refused"
            )
        )

        with patch(
            "merobox.commands.bootstrap.steps.login.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is False


# =============================================================================
# refresh
# =============================================================================


class TestRefreshStep:
    def _step(self, **extra):
        config = {
            "type": "refresh",
            "name": "refresh",
            "node": "calimero-node-1",
            **extra,
        }
        return RefreshStep(config, manager=_manager())

    def test_refresh_swaps_and_reseeds_token(self):
        step = self._step(outputs={"access_token": "access_token"})
        new = _token(access="new.acc.tok", refresh="new.ref.tok")
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = _token()
        fake_auth.refresh = AsyncMock(return_value=new)
        fake_auth.save_token.return_value = True

        dynamic = {}
        with patch(
            "merobox.commands.bootstrap.steps.refresh.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, dynamic))

        assert result is True
        fake_auth.refresh.assert_awaited_once()
        fake_auth.save_token.assert_called_once()
        assert dynamic["access_token"] == "new.acc.tok"

    def test_refresh_without_cached_token_fails(self):
        step = self._step()
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = None

        with patch(
            "merobox.commands.bootstrap.steps.refresh.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is False

    def test_refresh_expected_failure_passes_on_rejection(self):
        step = self._step(expected_failure=True)
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = _token()
        fake_auth.refresh = AsyncMock(
            side_effect=AuthenticationError("expired refresh token")
        )

        with patch(
            "merobox.commands.bootstrap.steps.refresh.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is True

    def test_refresh_expected_failure_but_success_does_not_reseed(self):
        step = self._step(expected_failure=True)
        new = _token(access="new.acc.tok")
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = _token()
        fake_auth.refresh = AsyncMock(return_value=new)
        fake_auth.save_token.return_value = True

        dynamic = {}
        with patch(
            "merobox.commands.bootstrap.steps.refresh.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, dynamic))

        # Asserted rejection but refresh succeeded -> fail and keep prior token.
        assert result is False
        fake_auth.save_token.assert_not_called()
        assert dynamic == {}

    def test_refresh_expected_failure_ignores_connectivity_error(self):
        step = self._step(expected_failure=True)
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = _token()
        fake_auth.refresh = AsyncMock(
            side_effect=AuthenticationError(
                "Network error during token refresh: timed out"
            )
        )

        with patch(
            "merobox.commands.bootstrap.steps.refresh.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is False


# =============================================================================
# ws_connect
# =============================================================================


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_str(self, msg):
        self.sent.append(msg)

    async def close(self):
        pass


class _FakeSession:
    """Async-context-manager session whose ws_connect is configurable."""

    def __init__(self, ws_connect):
        self._ws_connect = ws_connect

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ws_connect(self, url):
        return self._ws_connect(url)


class _FakeHandshakeError(aiohttp.WSServerHandshakeError):
    """A WSServerHandshakeError stand-in that carries a status.

    The real ``ClientResponseError`` constructor and ``__str__`` reach into
    ``request_info``/``message`` internals whose shape varies across aiohttp
    versions. We skip the base ``__init__`` and override ``__str__`` so the
    fixture is version-proof; it still matches ``except
    aiohttp.WSServerHandshakeError`` (the step only reads ``.status``).
    """

    def __init__(self, status):
        self.status = status

    def __str__(self):
        return f"handshake rejected with {self.status}"


def _handshake_error(status):
    return _FakeHandshakeError(status)


class TestWebSocketConnectStep:
    def _step(self, **extra):
        config = {
            "type": "ws_connect",
            "name": "ws",
            "node": "calimero-node-1",
            **extra,
        }
        return WebSocketConnectStep(config, manager=_manager())

    def test_ws_connects_with_cached_token(self):
        step = self._step(message="ping")
        captured = {}

        async def ws_connect(url):
            captured["url"] = url
            return _FakeWS()

        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = _token()

        with (
            patch(
                "merobox.commands.bootstrap.steps.websocket.AuthManager",
                return_value=fake_auth,
            ),
            patch(
                "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
                return_value=_FakeSession(ws_connect),
            ),
        ):
            result = _run(step.execute({}, {}))

        assert result is True
        # JWT rides on the ?token= query param.
        assert captured["url"] == "ws://localhost:2528/ws?token=acc.jwt.tok"

    def test_ws_missing_token_fails(self):
        step = self._step()
        fake_auth = MagicMock()
        fake_auth.get_cached_token.return_value = None

        with patch(
            "merobox.commands.bootstrap.steps.websocket.AuthManager",
            return_value=fake_auth,
        ):
            result = _run(step.execute({}, {}))

        assert result is False

    def test_ws_unauthenticated_rejected_is_expected_failure(self):
        step = self._step(unauthenticated=True, expected_failure=True)
        captured = {}

        async def ws_connect(url):
            captured["url"] = url
            raise _handshake_error(401)

        with patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=_FakeSession(ws_connect),
        ):
            result = _run(step.execute({}, {}))

        assert result is True
        # No token attached on the unauthenticated negative case.
        assert captured["url"] == "ws://localhost:2528/ws"

    def test_ws_non_auth_handshake_status_fails_even_when_expected(self):
        # A 500 (or any non-401/403) handshake error doesn't prove an auth
        # rejection, so it must not satisfy expected_failure.
        step = self._step(unauthenticated=True, expected_failure=True)

        async def ws_connect(url):
            raise _handshake_error(500)

        with patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=_FakeSession(ws_connect),
        ):
            result = _run(step.execute({}, {}))

        assert result is False

    def test_ws_timeout_fails_even_when_expected(self):
        # A connection timeout doesn't prove an auth rejection.
        step = self._step(unauthenticated=True, expected_failure=True)

        async def ws_connect(url):
            raise asyncio.TimeoutError()

        with patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=_FakeSession(ws_connect),
        ):
            result = _run(step.execute({}, {}))

        assert result is False

    def test_ws_null_timeout_falls_back_to_default(self):
        # `timeout: null` in YAML must not blow up on float(None).
        step = self._step(timeout=None, unauthenticated=True)

        async def ws_connect(url):
            return _FakeWS()

        with patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=_FakeSession(ws_connect),
        ):
            result = _run(step.execute({}, {}))

        assert result is True

    def test_ws_unauthenticated_unexpected_success_fails(self):
        step = self._step(unauthenticated=True, expected_failure=True)

        async def ws_connect(url):
            return _FakeWS()

        with patch(
            "merobox.commands.bootstrap.steps.websocket.aiohttp.ClientSession",
            return_value=_FakeSession(ws_connect),
        ):
            result = _run(step.execute({}, {}))

        # Asserted rejection but the connection succeeded -> step fails.
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
