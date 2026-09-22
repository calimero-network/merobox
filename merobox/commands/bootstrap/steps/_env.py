"""Environment-variable interpolation for step config values.

Some steps need a value that must NOT live in the YAML: a cloud session JWT,
or a measurement (MRTD) that is read out of a release artifact at run time.
Before these steps existed, workflows reached those values by shelling out to
``script`` steps, which read the host environment directly — and which are
subject to the documented ``set -eu`` pipe-swallowing hazard (a failing ``curl``
on the left of a pipe does not trip ``set -eu``, so the script exits 0 and the
workflow proceeds against empty state).

To let those workflows drop the shell, step config values may embed
``${ENV_VAR}`` references, resolved here against ``os.environ``.

This is deliberately NOT part of ``BaseStep._resolve_dynamic_value``: making
every step in the harness interpolate the process environment is a change to
the shared step contract. Only steps that opt in (``cloud_request``,
``issue_ownership_proof``, ``set_tee_admission_policy``) call this.

Rules:
- ``${NAME}`` is substituted anywhere inside a string.
- An unset (or empty) variable raises ``EnvRefError`` — an unresolved secret
  must fail the step loudly, never silently send the literal ``${NAME}`` or an
  empty string to a server.
- ``$NAME`` without braces is NOT a reference (left untouched), so values that
  legitimately contain ``$`` are safe.
"""

import os
import re
from typing import Any

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class EnvRefError(ValueError):
    """A ``${ENV_VAR}`` reference could not be resolved."""


def resolve_env_refs(value: Any) -> Any:
    """Recursively substitute ``${ENV_VAR}`` references in a config value.

    Strings are interpolated; dicts/lists are walked; everything else is
    returned unchanged.
    """
    if isinstance(value, str):
        return _sub(value)
    if isinstance(value, dict):
        return {k: resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_refs(v) for v in value]
    return value


def _sub(value: str) -> str:
    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        env_value = os.environ.get(name)
        if not env_value:
            raise EnvRefError(
                f"environment variable '{name}' is not set (referenced as "
                f"'${{{name}}}')"
            )
        return env_value

    return _ENV_REF.sub(replace, value)
