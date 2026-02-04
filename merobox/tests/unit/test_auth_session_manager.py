"""Unit tests for SessionManager connection pooling."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock dependencies before importing auth module to avoid import chain issues
sys.modules["calimero_client_py"] = MagicMock()
sys.modules["ed25519"] = MagicMock()
sys.modules["base58"] = MagicMock()
sys.modules["py_near"] = MagicMock()

# Mock the near module imports
sys.modules["merobox.commands.near"] = MagicMock()
sys.modules["merobox.commands.near.client"] = MagicMock()
sys.modules["merobox.commands.near.sandbox"] = MagicMock()

# Import constants used by the auth module
DEFAULT_CONNECTION_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0

# Mock the constants module
mock_constants = MagicMock()
mock_constants.DEFAULT_CONNECTION_TIMEOUT = DEFAULT_CONNECTION_TIMEOUT
mock_constants.DEFAULT_READ_TIMEOUT = DEFAULT_READ_TIMEOUT
sys.modules["merobox.commands.constants"] = mock_constants

# Now import just the auth module directly (not through merobox.commands)
# We need to reload it to pick up the mocks
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
        # Reset singleton
        SessionManager._instance = None

        instance1 = SessionManager.get_shared_instance()
        instance2 = SessionManager.get_shared_instance()

        assert instance1 is instance2

        # Clean up
        SessionManager._instance = None

    @pytest.mark.asyncio
    async def test_close_shared_instance(self):
        """Test close_shared_instance cleans up singleton."""
        # Reset singleton
        SessionManager._instance = None

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
        # Reset singleton
        SessionManager._instance = None

        with patch("merobox.commands.auth.aiohttp.TCPConnector") as mock_connector:
            with patch("merobox.commands.auth.aiohttp.ClientSession") as mock_session:
                mock_connector_instance = MagicMock()
                mock_connector.return_value = mock_connector_instance

                mock_session_instance = MagicMock()
                mock_session_instance.closed = False
                mock_session.return_value = mock_session_instance

                session = await get_shared_session()

                assert session == mock_session_instance

        # Clean up
        if SessionManager._instance:
            SessionManager._instance._session = None
            SessionManager._instance._connector = None
        SessionManager._instance = None

    @pytest.mark.asyncio
    async def test_close_shared_session(self):
        """Test close_shared_session cleans up shared instance."""
        # Reset singleton
        SessionManager._instance = None

        # Create instance
        instance = SessionManager.get_shared_instance()
        instance.close = AsyncMock()

        await close_shared_session()

        instance.close.assert_awaited_once()
        assert SessionManager._instance is None
