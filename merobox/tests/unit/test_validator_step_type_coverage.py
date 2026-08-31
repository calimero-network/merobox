"""Every registered step type must validate AND run.

A step type is registered in three independent places: `config.VALID_STEP_TYPES`
/ `STEP_TYPE_MODELS` (the schema layer), `validate/validator.py`'s
`validate_step_config` (what `bootstrap validate` runs), and the executor's
`_create_step_executor` (what `bootstrap run` dispatches on). Registering in
some but not all of them produces a step that passes every check and then fails
at the one place it matters.

Both halves have already been shipped broken. `login` / `refresh` /
`ws_connect` / `ws_subscribe` and `upload_blob` were unknown to the CLI
validator; `account_relink` / `account_devices` / `account_applications` shipped
schema-valid, validator-valid and undispatchable, so `bootstrap run` answered
"Unknown step type" for three types the docs advertised.
"""

import pytest

from merobox.commands.bootstrap.config import VALID_STEP_TYPES
from merobox.commands.bootstrap.run.executor import WorkflowExecutor
from merobox.commands.bootstrap.validate.validator import validate_step_config

# Types the CLI validator deliberately does not dispatch to a step class.
# Keep this empty unless there is a real reason, and say what it is.
_NOT_DISPATCHED: set[str] = set()


def _unknown_type_errors(step_type: str) -> list[str]:
    """Errors from validating a bare step of this type, filtered to 'unknown type'.

    A bare `{"type": ...}` step trips plenty of other complaints (missing
    `node`, missing `blob_id`, ...) — those are the point of the validator and
    are ignored here. Only "unknown type" means the dispatch is missing.
    """
    errors = validate_step_config({"type": step_type}, f"probe-{step_type}", step_type)
    return [e for e in errors if "unknown type" in e]


@pytest.mark.parametrize("step_type", sorted(VALID_STEP_TYPES - _NOT_DISPATCHED))
def test_registered_step_type_is_known_to_the_cli_validator(step_type):
    assert _unknown_type_errors(step_type) == [], (
        f"'{step_type}' is in config.VALID_STEP_TYPES but validate_step_config "
        f"has no branch for it, so `merobox bootstrap validate` rejects every "
        f"workflow that uses it. Add an elif to validate/validator.py."
    )


def test_the_blob_transfer_steps_specifically():
    """The pair this guard was added for — cheap, explicit regression."""
    assert _unknown_type_errors("upload_blob") == []
    assert _unknown_type_errors("download_blob") == []


def test_a_genuinely_unknown_type_is_still_rejected():
    """Sanity check: the probe would actually catch a missing branch."""
    assert _unknown_type_errors("not_a_real_step_type") != []


def _executor() -> WorkflowExecutor:
    """An executor with only what `_create_step_executor` reads.

    `__new__` rather than `__init__` because constructing one for real wants a
    Docker client, and step construction touches none of it.
    """
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.manager = None
    executor.resolver = None
    executor.auth_mode = None
    return executor


def _dispatches(step_type: str) -> bool:
    """Whether `bootstrap run` can build a step of this type."""
    try:
        return (
            _executor()._create_step_executor(step_type, {"type": step_type})
            is not None
        )
    except KeyError:
        return False
    except Exception:
        # A step that rejects a bare config in its own validation still
        # dispatched, which is all this asks.
        return True


@pytest.mark.parametrize("step_type", sorted(VALID_STEP_TYPES))
def test_registered_step_type_is_dispatchable_by_the_executor(step_type):
    assert _dispatches(step_type), (
        f"'{step_type}' is in config.VALID_STEP_TYPES but "
        f"_create_step_executor has no branch for it, so `merobox bootstrap "
        f"run` fails with 'Unknown step type' on every workflow that uses it."
    )


def test_the_account_read_steps_specifically():
    """The three this guard was added for - cheap, explicit regression."""
    assert _dispatches("account_relink")
    assert _dispatches("account_devices")
    assert _dispatches("account_applications")


def test_a_genuinely_unknown_type_is_not_dispatchable():
    """Sanity check: the probe would actually catch a missing branch."""
    assert not _dispatches("not_a_real_step_type")
