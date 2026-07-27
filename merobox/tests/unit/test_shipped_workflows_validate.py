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
