"""A step key nothing reads must fail validation, not be silently dropped.

`create_group_in_namespace` declares `group_name`; 21 scenarios in
calimero-network/core spelled it `group_alias`, and every one of those
subgroups was born unnamed while the YAML read as though it were named. No run
failed, which is the problem - the harness ignored a stated intent and went
green. `extra="forbid"` on `BaseStepConfig` closes that, and the error names
the nearest valid field so the typo points at its fix.
"""

import pytest

from merobox.commands.bootstrap.config import (
    STEP_TYPE_MODELS,
    validate_workflow_step,
)


def _errors(step):
    return validate_workflow_step(step, 0)


class TestUnknownKeysRejected:
    def test_group_alias_is_rejected_and_points_at_group_name(self):
        errors = _errors(
            {
                "type": "create_group_in_namespace",
                "name": "Create sub",
                "node": "n1",
                "namespace_id": "ns-hex",
                "group_alias": "private-channel",
            }
        )
        assert len(errors) == 1
        assert "unknown field 'group_alias'" in errors[0]
        assert "Did you mean 'group_name'?" in errors[0]

    def test_a_key_with_no_near_match_still_fails(self):
        errors = _errors(
            {
                "type": "create_group_in_namespace",
                "node": "n1",
                "namespace_id": "ns-hex",
                "banana_field": "x",
            }
        )
        assert len(errors) == 1
        assert "unknown field 'banana_field'" in errors[0]
        assert "Did you mean" not in errors[0]

    def test_capability_on_create_mesh_is_rejected(self):
        """Dead since the namespace flow replaced the capability-bearing invite."""
        errors = _errors(
            {
                "type": "create_mesh",
                "context_node": "n1",
                "application_id": "app",
                "nodes": ["n1", "n2"],
                "capability": "member",
            }
        )
        assert any("unknown field 'capability'" in e for e in errors)

    def test_nested_steps_are_checked_too(self):
        errors = _errors(
            {
                "type": "repeat",
                "count": 2,
                "steps": [
                    {
                        "type": "create_group_in_namespace",
                        "node": "n1",
                        "namespace_id": "ns-hex",
                        "group_alias": "x",
                    }
                ],
            }
        )
        assert any("unknown field 'group_alias'" in e for e in errors)


class TestKeysTheExecutorsActuallyRead:
    """Every field an executor reads has to be declared, or forbidding unknown
    keys turns a working workflow into a validation error."""

    @pytest.mark.parametrize(
        "step",
        [
            {
                "type": "create_mesh",
                "context_node": "n1",
                "application_id": "app",
                "nodes": ["n1", "n2"],
            },
            {
                "type": "create_context",
                "node": "n1",
                "application_id": "app",
                "group_id": "g",
                "params": "{}",
            },
            {
                "type": "install_application",
                "node": "n1",
                "package": "com.example.app",
                "version": "1.0.0",
            },
            {
                "type": "join_namespace",
                "node": "n1",
                "group_id": "ns-hex",
                "invitation": "{}",
            },
            {
                "type": "wait_for_sync",
                "context_id": "c",
                "nodes": ["n1"],
                "retry_attempts": 3,
            },
            {
                "type": "call",
                "node": "n1",
                "context_id": "c",
                "method": "get",
                "exec_type": "function_call",
                "state_retry_attempts": 2,
                "state_retry_delay": 0.5,
            },
            {"type": "script", "script": "s.sh", "args": ["a"]},
            {
                "type": "parallel",
                "groups": [{"name": "g", "steps": []}],
                "mode": "burst",
                "failure_mode": "fail-fast",
            },
            {"type": "list_proposals", "node": "n1", "context_id": "c", "args": "{}"},
            {"type": "wait", "seconds": 1, "description": "settle the DAG"},
            {
                "type": "get_group_info",
                "node": "n1",
                "group_id": "g",
                "expected_failure": True,
                "expected_error": "not found",
            },
        ],
    )
    def test_step_validates(self, step):
        assert _errors(step) == []

    def test_install_application_still_needs_a_source(self):
        errors = _errors({"type": "install_application", "node": "n1"})
        assert any("'path' or 'package' + 'version'" in e for e in errors)


def test_the_validate_cli_agrees_with_the_runner():
    """Two independent validators: the schema layer the runner uses, and the
    elif chain behind `bootstrap validate`. A CLI that passes what the runner
    rejects is worse than no CLI."""
    from merobox.commands.bootstrap.validate.validator import (
        validate_workflow_config as cli_validate,
    )

    result = cli_validate(
        {
            "name": "probe",
            "nodes": {"count": 1},
            "steps": [
                {
                    "type": "create_group_in_namespace",
                    "name": "Create sub",
                    "node": "n1",
                    "namespace_id": "ns",
                    "group_alias": "private-channel",
                }
            ],
        }
    )
    assert result["valid"] is False
    assert any("unknown field 'group_alias'" in e for e in result["errors"])


def test_the_validate_cli_does_not_double_report():
    """The CLI runs its own required-field checks; the schema layer must
    contribute only the unknown keys or every complaint prints twice."""
    from merobox.commands.bootstrap.validate.validator import (
        validate_workflow_config as cli_validate,
    )

    result = cli_validate(
        {
            "name": "probe",
            "nodes": {"count": 1},
            "steps": [{"type": "create_group_in_namespace", "name": "Create sub"}],
        }
    )
    assert len(result["errors"]) == len(set(result["errors"]))


def test_every_step_model_forbids_extras():
    """A model that opts back into extras reopens the same hole."""
    permissive = [
        t
        for t, model in STEP_TYPE_MODELS.items()
        if model.model_config.get("extra") != "forbid"
    ]
    assert permissive == []


class TestLeaveStepsAreModelled:
    """The three leave steps had no entry in `STEP_TYPE_MODELS`, so the
    unknown-key gate skipped them entirely — `model_class` came back None and
    `validate_workflow_step` returned no errors for any spelling at all. A step
    type that opts out of the gate is the same silent-drop hole under a
    different name."""

    @pytest.mark.parametrize(
        "step",
        [
            {"type": "leave_context", "node": "n1", "context_id": "c"},
            {"type": "leave_group", "node": "n1", "group_id": "g"},
            {"type": "leave_namespace", "node": "n1", "namespace_id": "ns"},
        ],
    )
    def test_the_documented_form_validates(self, step):
        assert _errors(step) == []

    @pytest.mark.parametrize(
        ("step", "typo"),
        [
            ({"type": "leave_context", "node": "n1", "contex_id": "c"}, "contex_id"),
            ({"type": "leave_group", "node": "n1", "grup_id": "g"}, "grup_id"),
            (
                {"type": "leave_namespace", "node": "n1", "namespace": "ns"},
                "namespace",
            ),
        ],
    )
    def test_a_misspelled_id_is_rejected(self, step, typo):
        errors = _errors(step)
        assert any(f"unknown field '{typo}'" in e for e in errors), errors

    def test_leaving_a_group_by_context_id_is_not_silently_accepted(self):
        """The ids are not interchangeable — `leave_group` on a context id
        would target nothing, and the key naming it must fail rather than be
        dropped."""
        errors = _errors(
            {"type": "leave_group", "node": "n1", "context_id": "c", "group_id": "g"}
        )
        assert any("unknown field 'context_id'" in e for e in errors), errors


def test_every_valid_step_type_that_has_a_model_is_reachable():
    """A step type absent from `STEP_TYPE_MODELS` silently bypasses the gate,
    so the leave family staying modelled is worth pinning."""
    for step_type in ("leave_context", "leave_group", "leave_namespace"):
        assert step_type in STEP_TYPE_MODELS
