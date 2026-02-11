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
        "calimero_client_py",
        "ed25519",
        "base58",
        "py_near",
        "merobox.commands.near",
        "merobox.commands.near.client",
        "merobox.commands.near.sandbox",
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

        with patch("merobox.commands.auth.aiohttp.TCPConnector") as mock_connector:
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_connector_instance = MagicMock()
                mock_connector.return_value = mock_connector_instance

                mock_session_instance = MagicMock()
                mock_session_instance.closed = False
                mock_session.return_value = mock_session_instance

                session = await manager.get_session()

                # Verify connector was created with correct parameters
                mock_connector.assert_called_once_with(
                    limit=DEFAULT_POOL_CONNECTIONS,
                    limit_per_host=DEFAULT_POOL_CONNECTIONS_PER_HOST,
                    keepalive_timeout=DEFAULT_POOL_KEEPALIVE_TIMEOUT,
                    enable_cleanup_closed=True,
                )

                # Verify session was created with the connector
                mock_session.assert_called_once_with(connector=mock_connector_instance)

                assert session == mock_session_instance
                assert manager._session == mock_session_instance
                assert manager._connector == mock_connector_instance

    @pytest.mark.asyncio
    async def test_get_session_reuses_existing_session(self):
        """Test get_session returns existing session if not closed."""
        manager = SessionManager()

        mock_session = MagicMock()
        mock_session.closed = False
        manager._session = mock_session

        session = await manager.get_session()

        assert session == mock_session

    @pytest.mark.asyncio
    async def test_get_session_recreates_closed_session(self):
        """Test get_session creates new session if existing one is closed."""
        manager = SessionManager()

        # Set up a closed session
        old_session = MagicMock()
        old_session.closed = True
        manager._session = old_session

        with patch("merobox.commands.auth.aiohttp.TCPConnector") as mock_connector:
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_connector_instance = MagicMock()
                mock_connector.return_value = mock_connector_instance

                new_session = MagicMock()
                new_session.closed = False
                mock_session.return_value = new_session

                session = await manager.get_session()

                # Should create a new session
                assert session == new_session
                mock_connector.assert_called_once()
                mock_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_thread_safe(self):
        """Test get_session is thread-safe with concurrent calls."""
        manager = SessionManager()
        call_count = 0

        original_create_connector = manager._create_connector

        def counting_create_connector():
            nonlocal call_count
            call_count += 1
            return original_create_connector()

        with patch.object(manager, "_create_connector", counting_create_connector):
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_session_instance = MagicMock()
                mock_session_instance.closed = False
                mock_session.return_value = mock_session_instance

                # Call get_session concurrently multiple times
                tasks = [manager.get_session() for _ in range(10)]
                results = await asyncio.gather(*tasks)

                # All results should be the same session
                for result in results:
                    assert result == mock_session_instance

                # Connector should only be created once due to lock
                assert call_count == 1

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

        # Should not raise
        await manager.close()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test SessionManager works as async context manager."""
        with patch("merobox.commands.auth.aiohttp.TCPConnector") as mock_connector:
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_connector_instance = MagicMock()
                mock_connector.return_value = mock_connector_instance

                mock_session_instance = AsyncMock()
                mock_session_instance.closed = False
                mock_session.return_value = mock_session_instance

                async with SessionManager() as session:
                    assert session == mock_session_instance

                # After exiting context, close should be called
                mock_session_instance.close.assert_awaited_once()

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
        with patch("merobox.commands.auth.aiohttp.TCPConnector") as mock_connector:
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_connector_instance = MagicMock()
                mock_connector.return_value = mock_connector_instance

                mock_session_instance = MagicMock()
                mock_session_instance.closed = False
                mock_session.return_value = mock_session_instance

                session = await get_shared_session()

                assert session == mock_session_instance

    @pytest.mark.asyncio
    async def test_close_shared_session(self):
        """Test close_shared_session cleans up shared instance."""
        # Create instance
        instance = SessionManager.get_shared_instance()
        instance.close = AsyncMock()

        await close_shared_session()

        instance.close.assert_awaited_once()
        assert SessionManager._instance is None
