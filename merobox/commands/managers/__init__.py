"""
Managers module - Focused manager classes following Single Responsibility Principle.

This module contains specialized managers extracted from the monolithic DockerManager:
- BaseManager: Common Docker client utilities
- NetworkManager: Docker network management
- AuthServiceManager: Auth service stack (Traefik + Auth) management
- NodeManager: Calimero node container management
"""

from merobox.commands.managers.auth_service import AuthServiceManager
from merobox.commands.managers.base import BaseManager
from merobox.commands.managers.network import NetworkManager
from merobox.commands.managers.node import NodeManager

__all__ = [
    "BaseManager",
    "NetworkManager",
    "AuthServiceManager",
    "NodeManager",
]
