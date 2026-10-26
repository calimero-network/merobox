"""The release-v2 workflow format: loading, validation and placeholders.

These need no cluster. `tests/test_cloud_v2_integration.py` runs the example
workflows against real merod-v2 processes.
"""

import textwrap

import pytest

from merobox.cloud_v2.workflow import WorkflowError, load_workflow, resolve


def write(tmp_path, body):
    p = tmp_path / "wf.yml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_a_minimal_workflow_loads(tmp_path):
    wf = load_workflow(
        write(
            tmp_path,
            """
            backend: cloud-v2
            accounts: [alice]
            steps:
              - type: whoami
                account: alice
            """,
        )
    )
    assert wf["accounts"] == ["alice"]
    assert wf["cluster"] == {}


@pytest.mark.parametrize(
    "body, message",
    [
        ("steps: [{type: wait, seconds: 1}]", "backend: cloud-v2"),
        ("backend: cloud-v2\nsteps: []", "non-empty"),
        ("backend: cloud-v2\nsteps: [{type: teleport}]", "unknown type"),
        (
            "backend: cloud-v2\nsteps: [{type: call, account: a, app: x}]",
            "missing method",
        ),
        (
            "backend: cloud-v2\nsteps: [{type: call, app: x, method: m}]",
            "'account' or 'api_key'",
        ),
        (
            "backend: cloud-v2\naccounts: [alice]\nsteps: [{type: whoami, account: bob}]",
            "not in 'accounts'",
        ),
        (
            "backend: cloud-v2\nsteps: [{type: repeat, count: 2, steps: [{type: nope}]}]",
            "unknown type",
        ),
    ],
)
def test_invalid_workflows_are_refused(tmp_path, body, message):
    with pytest.raises(WorkflowError, match=message):
        load_workflow(write(tmp_path, body))


def test_placeholders_keep_types_and_reach_into_results():
    v = {"app": "abc", "n": 3, "res": {"events": [{"kind": "Inserted"}]}}
    assert resolve("{{n}}", v) == 3
    assert resolve("app-{{app}}-{{n}}", v) == "app-abc-3"
    assert resolve({"a": ["{{res.events.0.kind}}"]}, v) == {"a": ["Inserted"]}


def test_fallbacks_take_the_first_that_resolves():
    v = {"env": {"B": "b"}}
    assert resolve("{{env.A ?? env.B}}", v) == "b"
    with pytest.raises(WorkflowError, match="unresolved"):
        resolve("{{env.A ?? env.C}}", v)


def test_the_examples_are_valid():
    import glob
    import os

    here = os.path.dirname(__file__)
    examples = glob.glob(
        os.path.join(here, "../../../workflow-examples/cloud-v2/*.yml")
    )
    assert examples
    for path in examples:
        load_workflow(path)
