"""Pytest configuration for merobox tests.

calimero-client-py is built from source against core, and the unit tests never
talk to a node, so it is mocked here; the cloud-v2 runner imports its
`CloudClient`, `CloudError` and `SoftPasskey` only when it starts a run.
"""

import sys
from unittest.mock import MagicMock

sys.modules["calimero_client_py"] = MagicMock()
