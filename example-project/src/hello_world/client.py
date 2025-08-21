"""
Simple client for demonstration purposes.

This client shows how you might interact with Calimero nodes
in a real application.
"""

import requests
from typing import Dict, Any, Optional


class Client:
    """Simple client for interacting with Calimero nodes."""

    def __init__(self, base_url: str):
        """Initialize client with base URL."""
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()

    def health_check(self) -> Dict[str, Any]:
        """Check node health."""
        try:
            response = self.session.get(f"{self.base_url}/admin-api/health", timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_node_info(self) -> Dict[str, Any]:
        """Get basic node information."""
        try:
            response = self.session.get(f"{self.base_url}/admin-api/node-info", timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_context(self, name: str, metadata: Optional[str] = None) -> Dict[str, Any]:
        """Create a new context."""
        try:
            payload = {"name": name}
            if metadata:
                payload["metadata"] = metadata

            response = self.session.post(
                f"{self.base_url}/admin-api/contexts",
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_contexts(self) -> Dict[str, Any]:
        """List all available contexts."""
        try:
            response = self.session.get(f"{self.base_url}/admin-api/contexts", timeout=10)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def install_application(self, app_path: str, is_dev: bool = True) -> Dict[str, Any]:
        """Install an application on the node."""
        try:
            # This is a simplified version - in reality you'd need to handle file uploads
            payload = {
                "path": app_path,
                "is_dev": is_dev,
                "metadata": "Hello World App"
            }
            
            response = self.session.post(
                f"{self.base_url}/admin-api/applications",
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}
