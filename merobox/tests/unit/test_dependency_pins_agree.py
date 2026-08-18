"""`pyproject.toml` and `requirements.txt` must pin the same client-py.

Both exist and only one governs the release binary: the PyPI wheel resolves
`pyproject.toml`, while the standalone binary is built with `pip install -r
requirements.txt`. Nothing tied them together, so they drifted — and the drift
was invisible until a scenario failed.

0.6.54 shipped exactly that. `pyproject.toml` had been lifted to `>=0.6.27` for
the `node_identity` step, `requirements.txt` still said `<0.6.26`, and the
binary core's CI downloads embedded a client-py with no `get_node_identity`.
Every `node_identity` step failed, and the step reported it as a generic read
failure rather than the AttributeError it was.

A version test guards the same class in calimero-client-py, where three files
declare a version. This is that test, for the two files that declare a
dependency.
"""

import re
from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parents[3]
_PKG = "calimero-client-py"


def _from_pyproject() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    for dep in data["project"]["dependencies"]:
        if dep.replace("_", "-").startswith(_PKG):
            return dep.strip()
    raise AssertionError(f"{_PKG} missing from pyproject.toml dependencies")


def _from_requirements() -> str:
    for line in (_ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line.replace("_", "-").startswith(_PKG):
            return line
    raise AssertionError(f"{_PKG} missing from requirements.txt")


def _spec(dep: str) -> str:
    """The version constraint, with the name and any whitespace stripped."""
    return re.sub(r"\s+", "", dep[len(_PKG) :]) if dep.startswith(_PKG) else dep


def test_client_py_pin_is_the_same_in_both_files():
    pyproject, requirements = _from_pyproject(), _from_requirements()
    assert _spec(pyproject) == _spec(requirements), (
        f"client-py pin drift: pyproject.toml={pyproject!r}, "
        f"requirements.txt={requirements!r}. The wheel resolves pyproject and "
        f"the release BINARY is built from requirements, so a lift applied to "
        f"only one ships a binary that cannot do what the wheel can."
    )
