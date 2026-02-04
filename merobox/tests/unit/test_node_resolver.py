"""Tests for NodeResolver helper methods.

This module tests the helper methods added to NodeResolver for consistent
node name resolution across the codebase.

These tests verify:
- is_registered_remote() method for checking if nodes are in the registry
- is_url() method for detecting URL references
- register_remote() method for adding nodes to the registry
"""

from unittest.mock import MagicMock
import importlib.util
import sys
from pathlib import Path

import pytest


# Constants to match the auth module
AUTH_METHOD_NONE = "none"
AUTH_METHOD_API_KEY = "api_key"
AUTH_METHOD_USER_PASSWORD = "user_password"


def _load_module_directly(module_name: str, file_path: str):
    """Load a module directly from file without going through package __init__.py."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    return module, spec


# Set up mocks for dependencies before loading node_resolver
_mock_auth = MagicMock()
_mock_auth.AUTH_METHOD_NONE = AUTH_METHOD_NONE
_mock_auth.AUTH_METHOD_API_KEY = AUTH_METHOD_API_KEY
_mock_auth.AUTH_METHOD_USER_PASSWORD = AUTH_METHOD_USER_PASSWORD
_mock_auth.AuthManager = MagicMock()
_mock_auth.AuthToken = MagicMock()
_mock_auth.AuthenticationError = Exception

_mock_constants = MagicMock()
_mock_constants.DEFAULT_CONNECTION_TIMEOUT = 5
_mock_constants.DEFAULT_READ_TIMEOUT = 30
_mock_constants.DEFAULT_RPC_PORT = 2528

_mock_remote_nodes = MagicMock()

# Install mocks
sys.modules['merobox.commands.auth'] = _mock_auth
sys.modules['merobox.commands.constants'] = _mock_constants
sys.modules['merobox.commands.remote_nodes'] = _mock_remote_nodes
sys.modules['aiohttp'] = MagicMock()
sys.modules['rich'] = MagicMock()
sys.modules['rich.console'] = MagicMock()
sys.modules['rich.prompt'] = MagicMock()

# Load node_resolver directly
_workspace = Path(__file__).parent.parent.parent.parent
_node_resolver_path = _workspace / "merobox" / "commands" / "node_resolver.py"
_module, _spec = _load_module_directly("merobox.commands.node_resolver", str(_node_resolver_path))
_spec.loader.exec_module(_module)

NodeResolver = _module.NodeResolver


@pytest.fixture
def mock_remote_manager():
    """Create a mock RemoteNodeManager."""
    manager = MagicMock()
    manager.get.return_value = None
    manager.get_by_url.return_value = None
    manager.is_url.return_value = False
    manager.register.return_value = True
    return manager


@pytest.fixture
def resolver(mock_remote_manager):
    """Create a NodeResolver with mocked dependencies."""
    return NodeResolver(remote_manager=mock_remote_manager)


class TestIsRegisteredRemote:
    """Tests for is_registered_remote method."""

    def test_returns_true_when_node_registered_by_name(self, resolver, mock_remote_manager):
        """Test that is_registered_remote returns True for registered node names."""
        mock_entry = MagicMock()
        mock_remote_manager.get.return_value = mock_entry

        result = resolver.is_registered_remote("my-node")

        assert result is True
        mock_remote_manager.get.assert_called_with("my-node")

    def test_returns_false_when_node_not_registered(self, resolver, mock_remote_manager):
        """Test that is_registered_remote returns False for unregistered nodes."""
        mock_remote_manager.get.return_value = None
        mock_remote_manager.is_url.return_value = False

        result = resolver.is_registered_remote("unknown-node")

        assert result is False

    def test_returns_true_when_url_is_registered(self, resolver, mock_remote_manager):
        """Test that is_registered_remote returns True for registered URLs."""
        mock_remote_manager.get.return_value = None
        mock_remote_manager.is_url.return_value = True
        mock_entry = MagicMock()
        mock_remote_manager.get_by_url.return_value = mock_entry

        result = resolver.is_registered_remote("http://example.com")

        assert result is True
        mock_remote_manager.get_by_url.assert_called_with("http://example.com")

    def test_returns_false_when_url_not_registered(self, resolver, mock_remote_manager):
        """Test that is_registered_remote returns False for unregistered URLs."""
        mock_remote_manager.get.return_value = None
        mock_remote_manager.is_url.return_value = True
        mock_remote_manager.get_by_url.return_value = None

        result = resolver.is_registered_remote("http://unknown.com")

        assert result is False


class TestIsUrl:
    """Tests for is_url method."""

    def test_returns_true_for_http_url(self, resolver, mock_remote_manager):
        """Test that is_url returns True for http:// URLs."""
        mock_remote_manager.is_url.return_value = True

        result = resolver.is_url("http://example.com")

        assert result is True
        mock_remote_manager.is_url.assert_called_with("http://example.com")

    def test_returns_true_for_https_url(self, resolver, mock_remote_manager):
        """Test that is_url returns True for https:// URLs."""
        mock_remote_manager.is_url.return_value = True

        result = resolver.is_url("https://example.com")

        assert result is True

    def test_returns_false_for_node_name(self, resolver, mock_remote_manager):
        """Test that is_url returns False for node names."""
        mock_remote_manager.is_url.return_value = False

        result = resolver.is_url("my-node")

        assert result is False


class TestRegisterRemote:
    """Tests for register_remote method."""

    def test_registers_node_with_basic_info(self, resolver, mock_remote_manager):
        """Test registering a node with name and URL."""
        result = resolver.register_remote(
            name="my-node",
            url="http://example.com",
        )

        assert result is True
        mock_remote_manager.register.assert_called_once_with(
            name="my-node",
            url="http://example.com",
            auth_method=AUTH_METHOD_NONE,
            username=None,
            password=None,
            api_key=None,
            description=None,
        )

    def test_registers_node_with_auth(self, resolver, mock_remote_manager):
        """Test registering a node with authentication."""
        result = resolver.register_remote(
            name="secure-node",
            url="https://secure.example.com",
            auth_method=AUTH_METHOD_API_KEY,
            api_key="secret-key",
            description="A secure node",
        )

        assert result is True
        mock_remote_manager.register.assert_called_once_with(
            name="secure-node",
            url="https://secure.example.com",
            auth_method=AUTH_METHOD_API_KEY,
            username=None,
            password=None,
            api_key="secret-key",
            description="A secure node",
        )

    def test_returns_false_when_registration_fails(self, resolver, mock_remote_manager):
        """Test that register_remote returns False when registration fails."""
        mock_remote_manager.register.return_value = False

        result = resolver.register_remote(
            name="fail-node",
            url="http://fail.com",
        )

        assert result is False
