"""The workflow behind the integration fixture must ask for its discovery.

`merod` leaves mDNS off unless asked for, so a workflow that sets no `mdns`
and no bootstrap peers gets nodes that never find each other: the namespace
join finds no reachable peer, gets no group key, and returns 500, taking every
test that shares the session fixture with it.

The fixture inherited working discovery from an older merod default rather
than requesting it, so a core-side default flip turned this repo red with no
change of its own. Asking explicitly is what makes that impossible.
"""

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_CONFTEST = _ROOT / "example-project" / "conftest.py"


def _fixture_workflow() -> tuple[Path, dict]:
    """The workflow `@run_workflow` loads, resolved against example-project/."""
    match = re.search(r'@run_workflow\(\s*"([^"]+)"', _CONFTEST.read_text())
    assert match, "no @run_workflow(...) literal found in example-project/conftest.py"
    path = (_CONFTEST.parent / match.group(1)).resolve()
    return path, yaml.safe_load(path.read_text())


def test_fixture_workflow_exists():
    path, _ = _fixture_workflow()
    assert path.is_file()


def test_fixture_workflow_asks_for_discovery():
    path, config = _fixture_workflow()
    nodes = config.get("nodes") or {}
    reachable = nodes.get("mdns") is True or bool(config.get("restart"))
    assert reachable, (
        f"{path.relative_to(_ROOT)} must set 'mdns: true' on its nodes (or wire "
        f"bootstrap peers via 'restart: true'); merod leaves mDNS off unless asked, "
        f"so relying on the default means the nodes never discover each other"
    )
