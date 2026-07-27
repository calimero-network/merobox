"""Every registered step type must be known to `merobox bootstrap validate`.

There are two independent validators: `config.VALID_STEP_TYPES` /
`STEP_TYPE_MODELS` (the schema layer) and `validate/validator.py`'s
`validate_step_config` (what the `bootstrap validate` CLI runs, an explicit
elif chain mapping type -> step class). Adding a step to the first and
forgetting the second makes the CLI reject a perfectly valid workflow with
"has unknown type".

That has already happened twice: `login` / `refresh` / `ws_connect` /
`ws_subscribe` shipped in 0.6.33 and were fixed later, and `upload_blob` sat
unregistered long enough that no workflow using it could be validated at all.
This test closes the loop instead of relying on someone remembering.
"""

import pytest

from merobox.commands.bootstrap.config import VALID_STEP_TYPES
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
