"""Pytest configuration for merobox tests.

This file handles mocking of problematic dependencies that may not
install correctly in all environments (e.g., ed25519 on Python 3.12).
"""

import sys
from unittest.mock import MagicMock

# Mock problematic modules before they're imported
# ed25519 has compatibility issues with Python 3.12
sys.modules["ed25519"] = MagicMock()

# py_near and its submodules
sys.modules["py_near"] = MagicMock()
sys.modules["py_near.account"] = MagicMock()
sys.modules["py_near.transactions"] = MagicMock()
sys.modules["py_near.dapps"] = MagicMock()
sys.modules["py_near.dapps.core"] = MagicMock()

# calimero_client_py and its submodules
sys.modules["calimero_client_py"] = MagicMock()
sys.modules["calimero_client_py.client"] = MagicMock()
