"""Run the release-v2 example workflows against real merod-v2 processes.

Needs MEROD_V2_BINARY (core, branch release-v2:
`cargo build -p calimero-cloud-node --bin merod-v2`), KV_STORE_WASM
(`cargo build -p kv-store --target wasm32-unknown-unknown --profile app-release`)
and calimero-client-py with `CloudClient`. Skipped otherwise.
"""

import glob
import os

import pytest

from merobox.cloud_v2 import run_workflow

EXAMPLES = sorted(
    glob.glob(
        os.path.join(os.path.dirname(__file__), "../workflow-examples/cloud-v2/*.yml")
    )
)


def _ready() -> bool:
    try:
        from calimero_client_py import CloudClient  # noqa: F401
    except ImportError:
        return False
    binary = os.environ.get("MEROD_V2_BINARY", "")
    wasm = os.environ.get("KV_STORE_WASM", "")
    return os.path.exists(binary) and os.path.exists(wasm)


@pytest.mark.skipif(
    not _ready(), reason="needs merod-v2, kv_store.wasm and CloudClient"
)
@pytest.mark.parametrize("workflow", EXAMPLES, ids=os.path.basename)
def test_example_workflow_passes(workflow):
    assert run_workflow(workflow)
