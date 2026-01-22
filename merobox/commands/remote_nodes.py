"""
Remote node registry management for merobox.

This module provides RemoteNodeManager for:
- Registering remote nodes with name → URL mapping
- Storing auth method hints (user_password, api_key, none)
- Persisting registry to ~/.merobox/remote_nodes.json
- Generating stable node names for token caching
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from merobox.commands.auth import (
    AUTH_METHOD_API_KEY,
    AUTH_METHOD_NONE,
    AUTH_METHOD_USER_PASSWORD,
)

console = Console()

# Default registry file location
DEFAULT_REGISTRY_PATH = Path.home() / ".merobox" / "remote_nodes.json"


@dataclass
class RemoteNodeAuthConfig:
    """Authentication configuration for a remote node."""

    method: str = AUTH_METHOD_NONE  # user_password, api_key, or none
    username: Optional[str] = None  # For user_password auth

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {"method": self.method}
        if self.username is not None:
            result["username"] = self.username
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteNodeAuthConfig":
        """Create from dictionary."""
        return cls(
            method=data.get("method", AUTH_METHOD_NONE),
            username=data.get("username"),
        )


@dataclass
class RemoteNodeEntry:
    """A registered remote node entry."""

    name: str
    url: str
    auth: RemoteNodeAuthConfig = field(default_factory=RemoteNodeAuthConfig)
    description: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "url": self.url,
            "auth": self.auth.to_dict(),
        }
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteNodeEntry":
        """Create from dictionary."""
        auth_data = data.get("auth", {})
        return cls(
            name=data["name"],
            url=data["url"],
            auth=RemoteNodeAuthConfig.from_dict(auth_data),
            description=data.get("description"),
        )


class RemoteNodeManager:
    """Manages the registry of remote Calimero nodes.

    The registry is stored in ~/.merobox/remote_nodes.json with the format:
    {
        "nodes": {
            "node_name": {
                "name": "node_name",
                "url": "https://node1.example.com",
                "auth": {
                    "method": "user_password",
                    "username": "admin"
                },
                "description": "Production node 1"
            },
            ...
        }
    }
    """

    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize RemoteNodeManager.

        Args:
            registry_path: Path to the registry file. Defaults to ~/.merobox/remote_nodes.json
        """
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self._nodes: dict[str, RemoteNodeEntry] = {}
        self._load()

    def _ensure_parent_dir(self) -> None:
        """Ensure the parent directory for the registry file exists."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> None:
        """Load the registry from disk."""
        if not self.registry_path.exists():
            self._nodes = {}
            return

        try:
            with open(self.registry_path, encoding="utf-8") as f:
                data = json.load(f)

            nodes_data = data.get("nodes", {})
            self._nodes = {}
            for name, node_data in nodes_data.items():
                try:
                    self._nodes[name] = RemoteNodeEntry.from_dict(node_data)
                except (KeyError, TypeError) as e:
                    console.print(
                        f"[yellow]Warning: Skipping invalid node entry '{name}': {e}[/yellow]"
                    )
        except (json.JSONDecodeError, OSError) as e:
            console.print(
                f"[yellow]Warning: Could not load remote nodes registry: {e}[/yellow]"
            )
            self._nodes = {}

    def _save(self) -> bool:
        """Save the registry to disk.

        Returns:
            True if saved successfully, False otherwise.
        """
        self._ensure_parent_dir()

        try:
            # Convert nodes to dict format
            nodes_dict = {name: entry.to_dict() for name, entry in self._nodes.items()}
            data = {"nodes": nodes_dict}

            # Write atomically using temp file
            temp_path = self.registry_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            # Set reasonable permissions
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass

            # Atomic rename
            temp_path.rename(self.registry_path)
            return True

        except OSError as e:
            console.print(f"[red]Error saving remote nodes registry: {e}[/red]")
            return False

    def register(
        self,
        name: str,
        url: str,
        auth_method: str = AUTH_METHOD_NONE,
        username: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """Register a remote node.

        Args:
            name: The unique name for this node.
            url: The base URL of the node (e.g., "https://node1.example.com").
            auth_method: Authentication method (user_password, api_key, or none).
            username: Username for user_password auth.
            description: Optional human-readable description.

        Returns:
            True if registered successfully, False otherwise.
        """
        # Validate auth method
        valid_methods = {
            AUTH_METHOD_USER_PASSWORD,
            AUTH_METHOD_API_KEY,
            AUTH_METHOD_NONE,
        }
        if auth_method not in valid_methods:
            console.print(
                f"[red]Invalid auth method '{auth_method}'. "
                f"Must be one of: {', '.join(valid_methods)}[/red]"
            )
            return False

        # Normalize URL
        normalized_url = url.rstrip("/")

        # Create entry
        auth_config = RemoteNodeAuthConfig(method=auth_method, username=username)
        entry = RemoteNodeEntry(
            name=name, url=normalized_url, auth=auth_config, description=description
        )

        # Check if overwriting
        if name in self._nodes:
            console.print(f"[yellow]Updating existing node '{name}'[/yellow]")

        self._nodes[name] = entry

        if self._save():
            console.print(
                f"[green]✓ Registered remote node '{name}' -> {normalized_url}[/green]"
            )
            return True
        return False

    def unregister(self, name: str) -> bool:
        """Unregister a remote node.

        Args:
            name: The name of the node to unregister.

        Returns:
            True if unregistered successfully, False if not found or error.
        """
        if name not in self._nodes:
            console.print(f"[yellow]Node '{name}' is not registered[/yellow]")
            return False

        del self._nodes[name]

        if self._save():
            console.print(f"[green]✓ Unregistered remote node '{name}'[/green]")
            return True
        return False

    def get(self, name: str) -> Optional[RemoteNodeEntry]:
        """Get a registered node by name.

        Args:
            name: The name of the node.

        Returns:
            The RemoteNodeEntry if found, None otherwise.
        """
        return self._nodes.get(name)

    def get_by_url(self, url: str) -> Optional[RemoteNodeEntry]:
        """Get a registered node by URL.

        Args:
            url: The URL to search for.

        Returns:
            The RemoteNodeEntry if found, None otherwise.
        """
        normalized_url = url.rstrip("/")
        for entry in self._nodes.values():
            if entry.url.rstrip("/") == normalized_url:
                return entry
        return None

    def list_all(self) -> list[RemoteNodeEntry]:
        """List all registered nodes.

        Returns:
            List of all RemoteNodeEntry objects.
        """
        return list(self._nodes.values())

    def exists(self, name: str) -> bool:
        """Check if a node is registered.

        Args:
            name: The name to check.

        Returns:
            True if registered, False otherwise.
        """
        return name in self._nodes

    def clear(self) -> bool:
        """Clear all registered nodes.

        Returns:
            True if cleared successfully, False on error.
        """
        self._nodes = {}
        if self._save():
            console.print("[green]✓ Cleared all remote nodes[/green]")
            return True
        return False

    def update_auth(
        self,
        name: str,
        auth_method: Optional[str] = None,
        username: Optional[str] = None,
    ) -> bool:
        """Update the auth configuration for a node.

        Args:
            name: The name of the node.
            auth_method: New auth method (if provided).
            username: New username (if provided).

        Returns:
            True if updated successfully, False if not found or error.
        """
        entry = self._nodes.get(name)
        if entry is None:
            console.print(f"[red]Node '{name}' is not registered[/red]")
            return False

        if auth_method is not None:
            valid_methods = {
                AUTH_METHOD_USER_PASSWORD,
                AUTH_METHOD_API_KEY,
                AUTH_METHOD_NONE,
            }
            if auth_method not in valid_methods:
                console.print(
                    f"[red]Invalid auth method '{auth_method}'. "
                    f"Must be one of: {', '.join(valid_methods)}[/red]"
                )
                return False
            entry.auth.method = auth_method

        if username is not None:
            entry.auth.username = username

        if self._save():
            console.print(f"[green]✓ Updated auth config for '{name}'[/green]")
            return True
        return False

    @staticmethod
    def get_node_name_for_url(url: str) -> str:
        """Generate a stable node name from a URL.

        Used for direct URL references that aren't registered.

        Args:
            url: The node URL.

        Returns:
            A stable identifier suitable for use as a cache key.
        """
        # Normalize URL
        normalized = url.rstrip("/").lower()

        # Remove protocol prefix for cleaner name
        for prefix in ("https://", "http://"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break

        # Create a short hash for uniqueness
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]

        # Combine sanitized host with hash
        safe_chars = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        )
        safe_host = "".join(c if c in safe_chars else "_" for c in normalized)

        # Truncate if too long
        if len(safe_host) > 50:
            safe_host = safe_host[:50]

        return f"url-{safe_host}-{url_hash}"

    def get_stable_node_name(self, node_ref: str) -> str:
        """Get a stable node name for a node reference.

        For registered nodes, returns the registered name.
        For URLs, generates a stable identifier.

        Args:
            node_ref: A node name or URL.

        Returns:
            A stable identifier for token caching.
        """
        # Check if it's a registered name
        if node_ref in self._nodes:
            return node_ref

        # Check if it's a URL that matches a registered node
        entry = self.get_by_url(node_ref)
        if entry:
            return entry.name

        # It's a direct URL, generate stable name
        return self.get_node_name_for_url(node_ref)

    def is_url(self, ref: str) -> bool:
        """Check if a reference looks like a URL.

        Args:
            ref: The reference to check.

        Returns:
            True if it looks like a URL, False otherwise.
        """
        return ref.startswith(("http://", "https://"))

    def resolve_url(self, node_ref: str) -> Optional[str]:
        """Resolve a node reference to its URL.

        Args:
            node_ref: A node name or URL.

        Returns:
            The resolved URL, or None if not found/invalid.
        """
        # If it's a URL, return it directly
        if self.is_url(node_ref):
            return node_ref.rstrip("/")

        # If it's a registered name, return the URL
        entry = self._nodes.get(node_ref)
        if entry:
            return entry.url

        return None
