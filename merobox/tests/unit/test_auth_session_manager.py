"""Unit tests for SessionManager connection pooling in auth.py."""

import aiohttp
import pytest

from merobox.commands.auth import (
    SessionManager,
    close_shared_session,
    get_shared_session,
)


class TestSessionManager:
    """Tests for SessionManager class."""

    @pytest.fixture
    def session_manager(self):
        """Create a fresh SessionManager instance."""
        return SessionManager()

    @pytest.fixture(autouse=True)
    async def cleanup_shared_instance(self):
        """Ensure shared instance is cleaned up after each test."""
        yield
        # Reset the class-level singleton
        await SessionManager.close_shared_instance()
        SessionManager._instance = None

    @pytest.mark.asyncio
    async def test_get_session_creates_session(self, session_manager):
        """Test that get_session creates a new aiohttp ClientSession."""
        session = await session_manager.get_session()
        assert session is not None
        assert isinstance(session, aiohttp.ClientSession)
        assert not session.closed
        await session_manager.close()

    @pytest.mark.asyncio
    async def test_get_session_reuses_session(self, session_manager):
        """Test that get_session reuses the same session on subsequent calls."""
        session1 = await session_manager.get_session()
        session2 = await session_manager.get_session()
        assert session1 is session2
        await session_manager.close()

    @pytest.mark.asyncio
    async def test_close_closes_session(self, session_manager):
        """Test that close() properly closes the session."""
        session = await session_manager.get_session()
        assert not session.closed
        await session_manager.close()
        assert session.closed

    @pytest.mark.asyncio
    async def test_get_session_recreates_after_close(self, session_manager):
        """Test that get_session creates a new session after close."""
        session1 = await session_manager.get_session()
        await session_manager.close()
        session2 = await session_manager.get_session()
        assert session1 is not session2
        assert not session2.closed
        await session_manager.close()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test SessionManager as async context manager."""
        async with SessionManager() as session:
            assert session is not None
            assert isinstance(session, aiohttp.ClientSession)
            assert not session.closed

    @pytest.mark.asyncio
    async def test_shared_instance_singleton(self):
        """Test that get_shared_instance returns the same instance."""
        manager1 = await SessionManager.get_shared_instance()
        manager2 = await SessionManager.get_shared_instance()
        assert manager1 is manager2

    @pytest.mark.asyncio
    async def test_close_shared_instance(self):
        """Test that close_shared_instance properly cleans up."""
        manager = await SessionManager.get_shared_instance()
        session = await manager.get_session()
        assert not session.closed
        await SessionManager.close_shared_instance()
        assert session.closed
        assert SessionManager._instance is None


class TestSharedSessionHelpers:
    """Tests for get_shared_session and close_shared_session helper functions."""

    @pytest.fixture(autouse=True)
    async def cleanup_shared_instance(self):
        """Ensure shared instance is cleaned up after each test."""
        yield
        await SessionManager.close_shared_instance()
        SessionManager._instance = None

    @pytest.mark.asyncio
    async def test_get_shared_session(self):
        """Test get_shared_session returns a valid session."""
        session = await get_shared_session()
        assert session is not None
        assert isinstance(session, aiohttp.ClientSession)
        assert not session.closed

    @pytest.mark.asyncio
    async def test_get_shared_session_reuses_session(self):
        """Test get_shared_session returns the same session."""
        session1 = await get_shared_session()
        session2 = await get_shared_session()
        assert session1 is session2

    @pytest.mark.asyncio
    async def test_close_shared_session(self):
        """Test close_shared_session properly closes the session."""
        session = await get_shared_session()
        await close_shared_session()
        assert session.closed
