"""Merobox for release-v2 (Calimero Cloud) clusters.

A release-v2 cluster is one service: `merod` nodes replicating every app by
Raft, reached by clients signed in with passkeys or API keys. This backend
starts such a cluster with one process per node (so a workflow can kill, pause,
restart and upgrade nodes) and runs YAML workflows against it through
calimero-client-py's `CloudClient`.

A workflow opts in with `backend: cloud-v2`; see `workflow-examples/cloud-v2/`.
"""

from merobox.cloud_v2.cluster import PackageRegistry, ProcessCluster
from merobox.cloud_v2.workflow import (
    WorkflowError,
    load_workflow,
    run_workflow,
    run_workflow_outcome,
)

__all__ = [
    "PackageRegistry",
    "ProcessCluster",
    "WorkflowError",
    "load_workflow",
    "run_workflow",
    "run_workflow_outcome",
]
