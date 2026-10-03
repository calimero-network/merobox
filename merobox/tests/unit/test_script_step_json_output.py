"""`json_output: true` on a local `type: script` step.

The script's last output line is parsed as a JSON object and `outputs:`
captures its fields, which is how a local script hands values to the steps
after it.
"""

import asyncio
import os

import pytest

from merobox.commands.bootstrap.steps.script import ScriptStep


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _script(tmp_path, body):
    path = tmp_path / "s.sh"
    path.write_text(body)
    return path


def _step(path, **extra):
    config = {
        "name": "json",
        "type": "script",
        "target": "local",
        "script": str(path),
        "json_output": True,
    }
    config.update(extra)
    return ScriptStep(config)


@pytest.fixture
def in_tmp(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def test_last_line_fields_are_exported(in_tmp):
    path = _script(
        in_tmp,
        'echo "progress, not json"\n'
        'echo \'{"namespaceId": "abc", "nested": {"n": 2}}\'\n',
    )
    step = _step(path, outputs={"ns": "namespaceId", "n": "nested.n"})
    dynamic = {}
    assert _run(step.execute({}, dynamic)) is True
    assert dynamic["ns"] == "abc"
    assert dynamic["n"] == 2


def test_a_last_line_that_is_not_an_object_fails(in_tmp):
    path = _script(in_tmp, "echo '{\"a\": 1}'\necho done\n")
    assert _run(_step(path).execute({}, {})) is False


def test_trailing_blank_lines_are_skipped():
    assert ScriptStep._parse_last_json_line('x\n{"a": 1}\n\n  \n') == {"a": 1}


def test_a_json_array_is_not_an_object():
    assert ScriptStep._parse_last_json_line("[1, 2]\n") is None


def test_json_output_requires_local_target():
    with pytest.raises(ValueError, match="target: local"):
        ScriptStep(
            {
                "name": "x",
                "type": "script",
                "script": "s.sh",
                "target": "nodes",
                "json_output": True,
            }
        )


def test_json_output_must_be_boolean():
    with pytest.raises(ValueError, match="boolean"):
        ScriptStep(
            {
                "name": "x",
                "type": "script",
                "script": "s.sh",
                "target": "local",
                "json_output": "yes",
            }
        )
