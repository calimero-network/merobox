"""Unit tests for SessionManager connection pooling."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Store original modules to restore later
_original_modules = {}


def _mock_modules():
    """Mock modules that are problematic to import in test environment."""
    modules_to_mock = [
        # External/runtime dependencies
        "calimero_client_py",
        "ed25519",
        "base58",
        "py_near",
        # near package modules (mock as package to prevent import errors)
        "merobox.commands.near",
        "merobox.commands.near.client",
        "merobox.commands.near.sandbox",
        "merobox.commands.near.contracts",
    ]

    for mod_name in modules_to_mock:
        if mod_name in sys.modules:
            _original_modules[mod_name] = sys.modules[mod_name]
        sys.modules[mod_name] = MagicMock()

    # Mock the constants module with actual values
    mock_constants = MagicMock()
    mock_constants.DEFAULT_CONNECTION_TIMEOUT = 10.0
    mock_constants.DEFAULT_READ_TIMEOUT = 30.0
    if "merobox.commands.constants" in sys.modules:
        _original_modules["merobox.commands.constants"] = sys.modules[
            "merobox.commands.constants"
        ]
    sys.modules["merobox.commands.constants"] = mock_constants


def _restore_modules():
    """Restore original modules."""
    modules_to_restore = [
        "calimero_client_py",
        "ed25519",
        "base58",
        "py_near",
        "merobox.commands.near",
        "merobox.commands.near.client",
        "merobox.commands.near.sandbox",
        "merobox.commands.near.contracts",
        "merobox.commands.constants",
    ]

    for mod_name in modules_to_restore:
        if mod_name in _original_modules:
            sys.modules[mod_name] = _original_modules[mod_name]
        elif mod_name in sys.modules:
            del sys.modules[mod_name]


# Setup mocks before importing auth module
_mock_modules()

# Now import just the auth module directly
import importlib  # noqa: E402

import merobox.commands.auth  # noqa: E402

importlib.reload(merobox.commands.auth)

from merobox.commands.auth import (  # noqa: E402
    DEFAULT_POOL_CONNECTIONS,
    DEFAULT_POOL_CONNECTIONS_PER_HOST,
    DEFAULT_POOL_KEEPALIVE_TIMEOUT,
    SessionManager,
    close_shared_session,
    get_shared_session,
    run_with_shared_session_cleanup,
)


@pytest.fixture(autouse=True)
def cleanup_session_manager():
    """Reset SessionManager singleton state before and after each test."""
    # Reset before test
    SessionManager._instance = None

    yield

    # Reset after test
    SessionManager._instance = None


@pytest.fixture(scope="module", autouse=True)
def cleanup_modules():
    """Restore original modules after test module completes."""
    yield
    _restore_modules()


class TestSessionManager:
    """Tests for the SessionManager class."""

    def test_init_with_defaults(self):
        """Test SessionManager initializes with default pooling configuration."""
        manager = SessionManager()
        assert manager._pool_connections == DEFAULT_POOL_CONNECTIONS
        assert manager._pool_connections_per_host == DEFAULT_POOL_CONNECTIONS_PER_HOST
        assert manager._keepalive_timeout == DEFAULT_POOL_KEEPALIVE_TIMEOUT
        assert manager._session is None
        assert manager._connector is None

    def test_init_with_custom_values(self):
        """Test SessionManager accepts custom pooling configuration."""
        manager = SessionManager(
            pool_connections=50,
            pool_connections_per_host=5,
            keepalive_timeout=60,
        )
        assert manager._pool_connections == 50
        assert manager._pool_connections_per_host == 5
        assert manager._keepalive_timeout == 60

    @pytest.mark.asyncio
    async def test_get_session_creates_new_session(self):
        """Test get_session creates a new session when none exists."""
        manager = SessionManager()

        mock_connector = MagicMock()
        mock_session = MagicMock()
        mock_session.closed = False

        with patch.object(
            manager, "_create_connector", return_value=mock_connector
        ) as mock_create_conn:
            with patch.object(
                manager, "_create_session", return_value=mock_session
            ) as mock_create_sess:
                session = await manager.get_session()

                # Verify connector and session were created
                mock_create_conn.assert_called_once()
                mock_create_sess.assert_called_once_with(mock_connector)

                assert session == mock_session
                assert manager._session == mock_session
                assert manager._connector == mock_connector

    @pytest.mark.asyncio
    async def test_get_session_reuses_existing_session(self):
        """Test get_session returns existing session if not closed."""
        manager = SessionManager()

        mock_session = MagicMock()
        mock_session.closed = False
        manager._session = mock_session
        # Set the session_loop_id to current loop so session is recognized as valid
        manager._session_loop_id = manager._get_current_loop_id()

        session = await manager.get_session()

        assert session == mock_session

    @pytest.mark.asyncio
    async def test_get_session_recreates_closed_session(self):
        """Test get_session creates new session if existing one is closed."""
        manager = SessionManager()

        # Set up a closed session with same session_loop_id
        old_session = MagicMock()
        old_session.closed = True
        manager._session = old_session
        manager._session_loop_id = manager._get_current_loop_id()

        mock_connector = MagicMock()
        mock_connector.closed = False
        new_session = MagicMock()
        new_session.closed = False

        with patch.object(manager, "_create_connector", return_value=mock_connector):
            with patch.object(manager, "_create_session", return_value=new_session):
                session = await manager.get_session()

                # Should create a new session
                assert session == new_session

    @pytest.mark.asyncio
    async def test_get_session_thread_safe(self):
        """Test get_session is thread-safe with concurrent calls."""
        manager = SessionManager()
        call_count = 0

        mock_connector = MagicMock()
        mock_session = MagicMock()
        mock_session.closed = False

        def counting_create_connector():
            nonlocal call_count
            call_count += 1
            return mock_connector

        with patch.object(manager, "_create_connector", counting_create_connector):
            with patch.object(manager, "_create_session", return_value=mock_session):
                # Call get_session concurrently multiple times
                tasks = [manager.get_session() for _ in range(10)]
                results = await asyncio.gather(*tasks)

                # All results should be the same session
                for result in results:
                    assert result == mock_session

                # Connector should only be created once due to lock
                assert call_count == 1

    @pytest.mark.asyncio
    async def test_get_session_handles_loop_change(self):
        """Test get_session detects event loop change and recreates resources."""
        manager = SessionManager()

        # Simulate having a session from a different event loop
        old_session = MagicMock()
        old_session.closed = False
        manager._session = old_session
        manager._session_loop_id = 12345  # Different from current loop

        mock_connector = MagicMock()
        new_session = MagicMock()
        new_session.closed = False

        with patch.object(
            manager, "_create_connector", return_value=mock_connector
        ) as mock_create_conn:
            with patch.object(
                manager, "_create_session", return_value=new_session
            ) as mock_create_sess:
                session = await manager.get_session()

                # Should create a new session since loop changed
                assert session == new_session
                mock_create_conn.assert_called_once()
                mock_create_sess.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_releases_resources(self):
        """Test close properly releases session and connector."""
        manager = SessionManager()

        mock_session = AsyncMock()
        mock_session.closed = False
        manager._session = mock_session

        mock_connector = AsyncMock()
        mock_connector.closed = False
        manager._connector = mock_connector

        # Set session_loop_id so close() recognizes we're in the same loop
        manager._session_loop_id = manager._get_current_loop_id()

        await manager.close()

        mock_session.close.assert_awaited_once()
        mock_connector.close.assert_awaited_once()
        assert manager._session is None
        assert manager._connector is None

    @pytest.mark.asyncio
    async def test_close_handles_already_closed(self):
        """Test close handles already closed session gracefully."""
        manager = SessionManager()

        mock_session = MagicMock()
        mock_session.closed = True
        manager._session = mock_session

        mock_connector = MagicMock()
        mock_connector.closed = True
        manager._connector = mock_connector

        # Set session_loop_id so close() recognizes we're in the same loop
        manager._session_loop_id = manager._get_current_loop_id()

        # Should not raise
        await manager.close()

    @pytest.mark.asyncio
    async def test_close_handles_loop_change(self):
        """Test close handles event loop change by clearing resources without closing."""
        manager = SessionManager()

        mock_session = AsyncMock()
        mock_session.closed = False
        manager._session = mock_session

        mock_connector = AsyncMock()
        mock_connector.closed = False
        manager._connector = mock_connector

        # Set session_loop_id to a different loop
        manager._session_loop_id = 12345  # Different from current loop

        await manager.close()

        # Should NOT call close on the session/connector (they belong to different loop)
        mock_session.close.assert_not_awaited()
        mock_connector.close.assert_not_awaited()

        # But resources should be cleared
        assert manager._session is None
        assert manager._connector is None
        assert manager._session_loop_id is None

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test SessionManager works as async context manager."""
        mock_connector = MagicMock()
        mock_session = AsyncMock()
        mock_session.closed = False

        manager = SessionManager()
        with patch.object(manager, "_create_connector", return_value=mock_connector):
            with patch.object(manager, "_create_session", return_value=mock_session):
                async with manager as session:
                    assert session == mock_session

                # After exiting context, close should be called
                mock_session.close.assert_awaited_once()

    def test_get_shared_instance_singleton(self):
        """Test get_shared_instance returns singleton."""
        instance1 = SessionManager.get_shared_instance()
        instance2 = SessionManager.get_shared_instance()

        assert instance1 is instance2

    @pytest.mark.asyncio
    async def test_close_shared_instance(self):
        """Test close_shared_instance cleans up singleton."""
        instance = SessionManager.get_shared_instance()
        assert SessionManager._instance is not None

        # Mock the close method
        instance.close = AsyncMock()

        await SessionManager.close_shared_instance()

        instance.close.assert_awaited_once()
        assert SessionManager._instance is None


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_get_shared_session(self):
        """Test get_shared_session returns session from shared instance."""
        mock_connector = MagicMock()
        mock_session = MagicMock()
        mock_session.closed = False

        # Get the shared instance and patch its methods
        instance = SessionManager.get_shared_instance()
        with patch.object(instance, "_create_connector", return_value=mock_connector):
            with patch.object(instance, "_create_session", return_value=mock_session):
                session = await get_shared_session()
                assert session == mock_session

    @pytest.mark.asyncio
    async def test_close_shared_session(self):
        """Test close_shared_session cleans up shared instance."""
        # Create instance
        instance = SessionManager.get_shared_instance()
        instance.close = AsyncMock()

        await close_shared_session()

        instance.close.assert_awaited_once()
        assert SessionManager._instance is None

    @pytest.mark.asyncio
    async def test_run_with_shared_session_cleanup_closes_on_success(self):
        """Test run_with_shared_session_cleanup closes session after success."""

        async def sample_coro():
            return "result"

        # Patch at the SessionManager class level to avoid module reload issues
        with patch.object(
            SessionManager, "close_shared_instance", new_callable=AsyncMock
        ) as mock_close:
            result = await run_with_shared_session_cleanup(sample_coro())
            assert result == "result"
            mock_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_with_shared_session_cleanup_closes_on_error(self):
        """Test run_with_shared_session_cleanup closes session even on error."""

        async def failing_coro():
            raise ValueError("test error")

        # Patch at the SessionManager class level to avoid module reload issues
        with patch.object(
            SessionManager, "close_shared_instance", new_callable=AsyncMock
        ) as mock_close:
            with pytest.raises(ValueError, match="test error"):
                await run_with_shared_session_cleanup(failing_coro())
            mock_close.assert_awaited_once()
