"""Every shipped workflow must pass the same schema gate `bootstrap run` applies.

`validate_workflow_config` (the Pydantic layer in config.py) runs before a
workflow executes, so a type mismatch between a step's model and what the step
executor actually accepts fails the run outright — for every workflow using that
field. That is how `expected_size: "{{blob_size}}"` shipped broken: the runtime
step accepts `int | str` and resolves placeholders at execute time, but the
model said `Optional[int]`, so Pydantic rejected the documented form. The unit
tests missed it because they construct step classes directly, bypassing the
model.

This costs half a second and covers every workflow in the repo, instead of
finding the same class of bug four minutes into a CI job.
"""

import glob
import os

import pytest
import yaml

from merobox.commands.bootstrap.config import validate_workflow_config
from merobox.commands.host_access import check_host_access

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_WORKFLOW_GLOB = os.path.join(_REPO_ROOT, "workflow-examples", "*.yml")


def _workflow_files() -> list[str]:
    return sorted(glob.glob(_WORKFLOW_GLOB))


def test_there_are_workflows_to_check():
    """Guard the guard: a bad glob would make every test below vacuously pass."""
    assert len(_workflow_files()) > 20


@pytest.mark.parametrize(
    "workflow_path", _workflow_files(), ids=lambda p: os.path.basename(p)
)
def test_shipped_workflow_passes_the_schema_gate(workflow_path):
    with open(workflow_path) as handle:
        config = yaml.safe_load(handle)

    errors = validate_workflow_config(config)

    assert errors == [], "{} would be rejected by `bootstrap run`:\n  {}".format(
        os.path.basename(workflow_path), "\n  ".join(errors)
    )


def _ci_lanes() -> list[tuple[str, bool]]:
    """(workflow, --no-docker) for every lane CI can run an example in: the binary
    lanes pass --no-docker, and the Docker lane runs those without no_docker."""
    lanes = []
    for path in _workflow_files():
        lanes.append((path, True))
        with open(path) as f:
            if not (yaml.safe_load(f) or {}).get("no_docker"):
                lanes.append((path, False))
    return lanes


@pytest.mark.parametrize(
    "workflow_path,no_docker",
    _ci_lanes(),
    ids=lambda v: os.path.basename(v) if isinstance(v, str) else f"no_docker={v}",
)
def test_shipped_workflow_runs_without_trust(workflow_path, no_docker, monkeypatch):
    """Apart from local script steps, which run_with_retry.sh trusts, every
    example passes the host-access check as CI runs it, from the repo root."""
    monkeypatch.chdir(_REPO_ROOT)
    with open(workflow_path) as f:
        config = _without_local_scripts(yaml.safe_load(f))
    check_host_access(config, os.path.dirname(workflow_path), cli_no_docker=no_docker)


def _without_local_scripts(value):
    if isinstance(value, list):
        return [
            _without_local_scripts(item)
            for item in value
            if not (
                isinstance(item, dict)
                and item.get("type") == "script"
                and item.get("target") == "local"
            )
        ]
    if isinstance(value, dict):
        return {key: _without_local_scripts(item) for key, item in value.items()}
    return value
