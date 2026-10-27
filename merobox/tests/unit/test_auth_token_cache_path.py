"""
Regression tests: AuthManager's token cache against the real calimero-client-py.

The other auth tests mock AuthManager, so none of them noticed when
calimero-client-py's ``get_token_cache_path`` grew an ``api_url`` argument
(client-py #137: the cache filename hashes the node's base URL plus its name)
and every cache read raised ``get_token_cache_path() missing 1 required
positional argument: 'node_name'``. These run the real AuthManager against the
installed package and check merobox writes where the Rust client reads.

merobox/tests/conftest.py replaces ``calimero_client_py`` in ``sys.modules``
with a MagicMock for the whole suite, so this module imports the real package
around that mock and points merobox.commands.auth at it.
"""

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest

from merobox.commands import auth as auth_module
from merobox.commands.auth import (
    AUTH_METHOD_USER_PASSWORD,
    AuthManager,
    AuthToken,
)


def _import_real_client():
    mocked = sys.modules.pop("calimero_client_py", None)
    try:
        return importlib.import_module("calimero_client_py")
    except ImportError:
        return None
    finally:
        if mocked is not None:
            sys.modules["calimero_client_py"] = mocked


calimero_client_py = _import_real_client()
if calimero_client_py is None:
    pytest.skip("calimero_client_py is not importable", allow_module_level=True)

URL = "http://localhost:2528"
NAME = "calimero-node-1"


@pytest.fixture(autouse=True)
def _real_client_in_isolated_home(tmp_path, monkeypatch):
    """Use the real cache helpers; they derive ~/.merobox/auth_cache from HOME."""
    for name in ("get_token_cache_dir", "get_token_cache_path"):
        monkeypatch.setattr(auth_module, name, getattr(calimero_client_py, name))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path(calimero_client_py.get_token_cache_dir()).is_relative_to(tmp_path)


def _client_path(url: str, name: str) -> Path:
    """The path the installed client reads for a connection to ``url``."""
    try:
        return Path(calimero_client_py.get_token_cache_path(url, name))
    except TypeError:
        # PyPI 0.9.0 keys the cache by node name alone.
        return Path(calimero_client_py.get_token_cache_path(name))


def _token(url: str = URL) -> AuthToken:
    return AuthToken(
        access_token="access-1",
        node_url=url,
        auth_method=AUTH_METHOD_USER_PASSWORD,
        refresh_token="refresh-1",
        expires_at=None,
        username="dev",
    )


def test_save_writes_where_the_client_reads_and_get_reads_it_back():
    manager = AuthManager()
    assert manager.save_token(_token(), NAME, URL)

    path = _client_path(URL, NAME)
    assert path.is_file()

    cached = manager.get_cached_token(NAME, URL)
    assert cached is not None
    assert cached.access_token == "access-1"


def test_delete_removes_the_client_path():
    manager = AuthManager()
    assert manager.save_token(_token(), NAME, URL)
    path = _client_path(URL, NAME)
    assert path.is_file()

    assert manager.delete_token(NAME, URL)
    assert not path.exists()
    assert manager.get_cached_token(NAME, URL) is None


def test_get_valid_token_reads_the_cache():
    """A token the client's cache holds is found by the unchanged entry point."""
    path = _client_path(URL, NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_token().to_dict()), encoding="utf-8")

    manager = AuthManager()

    token = asyncio.run(manager.get_valid_token(URL, NAME))
    assert token is not None
    assert token.access_token == "access-1"


def test_tokens_for_the_same_name_on_two_urls_do_not_collide():
    params = inspect.signature(calimero_client_py.get_token_cache_path).parameters
    if len(params) < 2:
        pytest.skip("this calimero-client-py keys the cache by node name alone")

    manager = AuthManager()
    other = "http://localhost:2529"
    assert manager.save_token(_token(URL), NAME, URL)
    assert manager.save_token(_token(other), NAME, other)
    assert _client_path(URL, NAME) != _client_path(other, NAME)
    assert manager.get_cached_token(NAME, URL).node_url == URL
    assert manager.get_cached_token(NAME, other).node_url == other


def test_falls_back_to_the_one_argument_client(monkeypatch, tmp_path):
    """Master pins PyPI 0.9.0, whose get_token_cache_path takes a name only."""
    calls = []

    def one_arg(node_name):
        calls.append(node_name)
        return str(tmp_path / f"{node_name}.json")

    monkeypatch.setattr(auth_module, "get_token_cache_path", one_arg)
    assert auth_module.token_cache_path(URL, NAME) == tmp_path / f"{NAME}.json"
    assert calls == [NAME]
